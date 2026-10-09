#!/usr/bin/env python3
"""Synchronize confirmed Moscow DNKOM retail prices, promotions and source descriptions.

The price feed is authoritative. Promotions are independently checked against the
whole main catalogue; descriptive text is copied as plain text from source tabs,
never generated. The previous output survives all required-source failures.
"""
import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
PRICE_URL = "https://results.dnkom.ru/pricelist/moskva"
CATALOGUE_URL = "https://dnkom.ru/analizy-i-tseny/po-tipu/"
CODE_RE = re.compile(r"\d{2,3}\.\d{3}(?:\.\d{1,2})?\Z")
MAX_BYTES = 15_000_000


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def money(value, context, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Non-numeric price: " + context)
    amount = Decimal(str(value))
    if (not amount.is_finite() or amount < 0 or
            (amount == 0 and not allow_zero) or
            amount != amount.quantize(Decimal("0.01"))):
        raise ValueError("Invalid monetary amount: " + context)
    return int(amount) if amount == int(amount) else float(amount)


def text(node):
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip() if node else ""


def displayed_price(node, context):
    value = text(node).replace("\xa0", " ")
    match = re.fullmatch(r"\s*([\d ]+(?:[,.]\d{1,2})?)\s*₽\s*", value)
    if not match:
        raise ValueError("Unrecognized displayed price: " + context)
    return money(float(match.group(1).replace(" ", "").replace(",", ".")), context)


def soup(raw):
    return BeautifulSoup(raw.decode("utf-8-sig", errors="strict"), "html.parser")


def parse_pricelist(raw, minimum_count=2000):
    document = soup(raw)
    settings = []
    for node in document.select("div.frontend-data[data-settings]"):
        value = json.loads(node["data-settings"], object_pairs_hook=unique_object)
        if isinstance(value, dict) and "pricelistServicesData" in value:
            settings.append(value)
    if len(settings) != 1 or settings[0].get("pricelistCityTid") != "1157":
        raise ValueError("Missing/ambiguous Moscow pricelist payload")
    payload = settings[0]
    source = payload.get("pricelistServicesData")
    materials = payload.get("pricelistBiomaterialsData")
    if not isinstance(source, dict) or len(source) < minimum_count or not isinstance(materials, dict):
        raise ValueError("Incomplete services/biomaterials payload")
    required = {"code", "title", "city_tid", "group_tid", "price", "duration",
                "urgent_price", "urgent_duration", "bioCodes", "sampling", "is_profile"}
    for code, value in source.items():
        if not CODE_RE.fullmatch(code) or not isinstance(value, dict) or not required <= value.keys():
            raise ValueError("Incomplete or invalid service: " + code)
        if value["code"] != code or value["city_tid"] != "1157":
            raise ValueError("Code/city mismatch: " + code)
        if not isinstance(value["title"], str) or not value["title"].strip():
            raise ValueError("Missing title: " + code)
        if not isinstance(value["bioCodes"], list) or not all(isinstance(x, str) for x in value["bioCodes"]):
            raise ValueError("Invalid biomaterial references: " + code)
        if not isinstance(value["sampling"], bool) or not isinstance(value["is_profile"], bool):
            raise ValueError("Invalid service flags: " + code)
        money(value["price"], code)
        money(value["urgent_price"], code + " urgent", allow_zero=True)
    descriptions = {}
    visible_codes = set()
    for row in document.select(".service-row"):
        code = text(row.select_one(".service-code"))
        if not CODE_RE.fullmatch(code):
            raise ValueError("Pricelist row without service code")
        if code in visible_codes:
            raise ValueError("Duplicate visible pricelist code: " + code)
        visible_codes.add(code)
        link = row.select_one('a[href*="/service-description/"]')
        if link:
            descriptions[code] = urljoin(PRICE_URL, link["href"])
    if visible_codes != set(source):
        raise ValueError("Visible rows and embedded service payload disagree")
    rows = []
    for code, value in sorted(source.items()):
        biomaterials = []
        for material_code in value["bioCodes"]:
            if material_code not in materials or not isinstance(materials[material_code], dict):
                raise ValueError("Missing biomaterial: " + material_code)
            material = materials[material_code]
            if not isinstance(material.get("name"), str):
                raise ValueError("Invalid biomaterial name")
            options = []
            for option in material.get("samplingServices", []):
                target = option["TargetCode"]
                if target not in source:
                    raise ValueError("Missing sampling service: " + target)
                options.append({"code": target, "name": source[target]["title"],
                                "price_rub": money(source[target]["price"], target),
                                "default_target": option.get("DefaultTarget"),
                                "patient_group_id": option.get("PatientGroup")})
            biomaterials.append({"code": material_code, "name": material["name"],
                                 "sampling_options": options})
        rows.append({"code": code, "name": value["title"].strip(),
                     "price_rub": money(value["price"], code), "old_price_rub": None,
                     "discount_percent": None, "discount_status": "not_in_main_catalogue",
                     "turnaround": value["duration"],
                     "urgent_price_rub": money(value["urgent_price"], code, True) or None,
                     "urgent_turnaround": value["urgent_duration"] or None,
                     "group_id": value["group_tid"], "biomaterials": biomaterials,
                     "is_sampling_service": value["sampling"], "is_profile": value["is_profile"],
                     "description": None, "source_url": descriptions.get(code, PRICE_URL)})
    return rows


def parse_catalogue(raw, current_url):
    document = soup(raw)
    rows = []
    for node in document.select(".analysis"):
        code = text(node.select_one(".code"))
        name_node = node.select_one("a.name[href]")
        if not CODE_RE.fullmatch(code) or not name_node:
            raise ValueError("Unrecognized main catalogue item")
        current = displayed_price(node.select_one(".prices .price"), code)
        old_node = node.select_one(".old-price__value")
        discount_node = node.select_one(".old-price__discount")
        if bool(old_node) != bool(discount_node):
            raise ValueError("Incomplete promotion: " + code)
        old, discount = None, None
        if old_node:
            old = displayed_price(old_node, code + " old")
            match = re.fullmatch(r"-\s*(\d{1,2}(?:[,.]\d+)?)\s*%", text(discount_node))
            if not match or old <= current:
                raise ValueError("Invalid promotion: " + code)
            discount = float(match.group(1).replace(",", "."))
            # DNKOM displays rounded percentages; preserve its displayed value.
            if not 0 < discount < 100 or abs(100 * (old - current) / old - discount) > 1:
                raise ValueError("Promotion percentage inconsistent with prices: " + code)
            discount = int(discount) if discount.is_integer() else discount
        url = urljoin(current_url, name_node["href"])
        if urlparse(url).netloc != "dnkom.ru" or not urlparse(url).path.startswith("/analizy-i-tseny/"):
            raise ValueError("Unexpected catalogue detail link")
        rows.append({"code": code, "name": text(name_node), "price_rub": current,
                     "old_price_rub": old, "discount_percent": discount, "url": url})
    if not rows:
        raise ValueError("Main catalogue page has no services")
    next_urls = set()
    for node in document.find_all(onclick=True):
        match = re.search(r"loadAjaxPage\(['\"]([^'\"]+\?PAGEN_2=\d+)['\"]\)", node["onclick"])
        if match:
            next_urls.add(urljoin(current_url, match.group(1)))
    if len(next_urls) > 1:
        raise ValueError("Ambiguous catalogue pagination")
    next_url = next(iter(next_urls), None)
    if next_url:
        expected = (int(re.search(r"PAGEN_2=(\d+)", current_url).group(1)) + 1
                    if "PAGEN_2=" in current_url else 2)
        if next_url != CATALOGUE_URL + "?PAGEN_2=" + str(expected):
            raise ValueError("Unexpected pagination URL")
    return rows, next_url


def parse_description(raw, code, source_url, updated_at):
    document = soup(raw)
    actual = [text(n) for n in document.select(".code.data")]
    if not actual or actual[0] != code:
        raise ValueError("Description page code mismatch: " + code)
    sections = []
    summary = ""
    for node in document.select(".analysis-tab-data"):
        label = node.find_previous_sibling("h2")
        title = text(label) or ("Описание" if not sections else "Информация")
        for excluded in node.select("script, style, .my-5, .article-sources"):
            excluded.decompose()
        # Several real source pages use raw text + <br>, without paragraphs.
        # Serializing only p/li drops the body and can select bibliography instead.
        for line_break in node.find_all("br"):
            line_break.replace_with("\n\n")
        for heading in node.find_all(["h2", "h3"]):
            heading.replace_with("\n\n\ue000" + text(heading) + "\ue001\n\n")
        for block in node.find_all(["p", "li", "div"]):
            block.insert_before("\n\n")
            block.insert_after("\n\n")
        raw_text = node.get_text(" ", strip=False)
        value = "\n\n".join(re.sub(r"\s+", " ", line).strip() for line in raw_text.split("\n") if line.strip())
        current_title = title
        current_lines = []
        for part in value.split("\n\n"):
            if part.startswith("\ue000") and part.endswith("\ue001"):
                if current_lines:
                    sections.append({"title": current_title, "text": "\n\n".join(current_lines)})
                current_title, current_lines = part[1:-1], []
            else:
                current_lines.append(part)
                if not summary and part:
                    summary = part if len(part) <= 300 else part[:300].rsplit(" ", 1)[0] + "…"
        if current_lines:
            sections.append({"title": current_title, "text": "\n\n".join(current_lines)})
    if not sections:
        raise ValueError("Description tabs missing: " + code)
    return {"summary": summary, "sections": sections,
            "source_url": source_url, "updated_at": updated_at, "status": "current"}


class Fetcher:
    def __init__(self, offline_dir=None, capture_dir=None):
        self.offline_dir = offline_dir
        self.capture_dir = capture_dir
        self.last_request = {}
        self.request_count = 0

    def get(self, url, filename):
        if self.offline_dir:
            return (self.offline_dir / filename).read_bytes()
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc not in {"dnkom.ru", "results.dnkom.ru"}:
            raise ValueError("Unexpected source URL")
        interval = 10 if parsed.netloc == "results.dnkom.ru" else 2
        for attempt in range(3):
            delay = interval - (time.monotonic() - self.last_request.get(parsed.netloc, -interval))
            if delay > 0:
                time.sleep(delay)
            self.last_request[parsed.netloc] = time.monotonic()
            self.request_count += 1
            try:
                request = Request(url, headers={"User-Agent": "DocnlabPriceSync/1.0 (+https://github.com/dilemion/docnlab)",
                                                "Accept": "text/html", "Accept-Language": "ru"})
                with urlopen(request, timeout=35) as response:
                    if response.status != 200 or urlparse(response.url).netloc != parsed.netloc:
                        raise ValueError("Unexpected source response/redirect")
                    raw = response.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ValueError("Source page exceeds size limit")
                if self.capture_dir:
                    self.capture_dir.mkdir(parents=True, exist_ok=True)
                    (self.capture_dir / filename).write_bytes(raw)
                return raw
            except (OSError, TimeoutError):
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
        raise RuntimeError("Source fetch failed")


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def synchronize(args):
    now = datetime.now(timezone.utc)
    timestamp = now.isoformat()
    previous = json.loads(args.output.read_text(encoding="utf-8")) if args.output.exists() else {}
    previous_services = {r["code"]: r for r in previous.get("services", [])}
    fetcher = Fetcher(args.offline_dir, args.capture_dir)
    raw = fetcher.get(PRICE_URL, "pricelist.html")
    services = parse_pricelist(raw, args.minimum_services)
    if previous_services and len(services) < len(previous_services) * .95:
        raise ValueError("Pricelist lost more than 5% of previous services")
    catalogue = {}
    url = CATALOGUE_URL
    pages = 0
    while url:
        pages += 1
        if pages > args.max_catalogue_pages:
            raise ValueError("Catalogue page limit exceeded; incomplete promotions")
        rows, next_url = parse_catalogue(fetcher.get(url, f"catalogue-{pages:03d}.html"), url)
        new_codes = 0
        for row in rows:
            old = catalogue.get(row["code"])
            if old and any(old[k] != row[k] for k in ("price_rub", "old_price_rub", "discount_percent")):
                raise ValueError("Conflicting duplicate main catalogue service: " + row["code"])
            if not old:
                catalogue[row["code"]] = row
                new_codes += 1
        if next_url and new_codes == 0:
            raise ValueError("Catalogue pagination repeated a whole page")
        url = next_url
        if pages % 10 == 0:
            print(json.dumps({"phase": "catalogue", "pages": pages, "unique_codes": len(catalogue)}), flush=True)
    previous_count = previous.get("catalogue", {}).get("unique_codes", 0)
    if len(catalogue) < args.minimum_catalogue_services or (previous_count and len(catalogue) < .95 * previous_count):
        raise ValueError("Main catalogue unexpectedly incomplete")
    matched = 0
    for service in services:
        main = catalogue.get(service["code"])
        if not main:
            continue
        matched += 1
        if service["price_rub"] != main["price_rub"]:
            raise ValueError("Main/pricelist price mismatch: " + service["code"])
        service.update({"old_price_rub": main["old_price_rub"], "discount_percent": main["discount_percent"],
                        "discount_status": "active" if main["old_price_rub"] else "none",
                        "source_url": main["url"]})
    if matched < args.minimum_catalogue_matches:
        raise ValueError("Insufficient cross-check coverage between the sources")
    selection = json.loads(args.selection.read_text(encoding="utf-8"))
    selected = selection.get("description_codes", [])
    if not isinstance(selected, list) or len(set(selected)) != len(selected) or not all(isinstance(c, str) and CODE_RE.fullmatch(c) for c in selected):
        raise ValueError("Invalid description selection")
    if not set(selected) <= {r["code"] for r in services}:
        raise ValueError("Selected description code absent from price feed")
    remaining = args.description_budget
    errors = []
    # Curated codes are enriched first, then every other source-matched service.
    # Expired/current cache checks happen below; the daily budget bounds requests.
    description_order = sorted(services, key=lambda r: (r["code"] not in selected, r["code"]))
    for service in description_order:
        code = service["code"]
        cached = previous_services.get(code, {}).get("description")
        if cached:
            cache_time = datetime.fromisoformat(cached["updated_at"])
            service["description"] = dict(cached)
            service["description"]["status"] = ("current" if now - cache_time < timedelta(days=args.description_ttl_days) else "stale")
        if (service["description"] and service["description"]["status"] == "current") or remaining <= 0:
            continue
        main = catalogue.get(code)
        if not main:
            if code in selected:
                errors.append({"code": code, "error": "not_in_main_catalogue"})
            continue
        remaining -= 1
        try:
            detail = fetcher.get(main["url"], "description-" + code + ".html")
            service["description"] = parse_description(detail, code, main["url"], timestamp)
        except (OSError, ValueError, UnicodeError) as error:
            errors.append({"code": code, "error": str(error)})
    result = {"schema_version": 1, "updated_at": timestamp, "city": "Москва", "city_id": "1157", "currency": "RUB",
              "pricing_status": "confirmed_docnlab_moscow", "pricing_confirmed_at": "2026-10-09",
              "source_url": PRICE_URL, "source_sha256": hashlib.sha256(raw).hexdigest(),
              "discount_status": "validated_main_catalogue", "count": len(services),
              "catalogue": {"source_url": CATALOGUE_URL, "pages": pages, "unique_codes": len(catalogue),
                            "matched_price_codes": matched, "price_codes_without_promotion_metadata": len(services) - matched},
              "descriptions_count": sum(bool(r["description"]) for r in services), "description_errors": errors,
              "services": services}
    atomic_json(args.output, result)
    return {"status": "ok", "services": len(services), "catalogue_pages": pages,
            "matched": matched, "discounts": sum(r["old_price_rub"] is not None for r in services),
            "descriptions": result["descriptions_count"], "description_errors": len(errors),
            "network_requests": fetcher.request_count, "output": str(args.output)}


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/analyses.json")
    parser.add_argument("--selection", type=Path, default=ROOT / "config/analyses-selection.json")
    parser.add_argument("--offline-dir", type=Path, help="Replay saved source pages; performs no network access")
    parser.add_argument("--capture-dir", type=Path, help="Save fetched source HTML outside the published data directory")
    parser.add_argument("--description-budget", type=int, default=100)
    parser.add_argument("--description-ttl-days", type=int, default=30)
    parser.add_argument("--max-catalogue-pages", type=int, default=200)
    parser.add_argument("--minimum-services", type=int, default=2000)
    parser.add_argument("--minimum-catalogue-services", type=int, default=2000)
    parser.add_argument("--minimum-catalogue-matches", type=int, default=1800)
    return parser.parse_args()


def main():
    args = arguments()
    if (not 0 <= args.description_budget <= 100 or not 1 <= args.description_ttl_days <= 365 or
            not 1 <= args.max_catalogue_pages <= 300 or min(args.minimum_services, args.minimum_catalogue_services, args.minimum_catalogue_matches) < 1):
        raise SystemExit("Invalid synchronization bounds")
    try:
        print(json.dumps(synchronize(args), ensure_ascii=False), flush=True)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as error:
        print(json.dumps({"status": "error", "message": str(error), "output_retained": str(args.output)}, ensure_ascii=False), flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
