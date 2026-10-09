"""Contract tests for price authority, promotion visibility and atomic publication."""
import argparse
from copy import deepcopy
import html
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("sync_analyses", Path(__file__).resolve().parents[1] / "scripts/sync_analyses.py")
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


def pricelist(price=5760, code="62.056"):
    return {"pricelistCityTid": "1157", "pricelistServicesData": {
        code: {"code": code, "title": "После вакцинации", "city_tid": "1157", "group_tid": "5",
               "price": price, "duration": "1 д.", "urgent_price": 0, "urgent_duration": "",
               "bioCodes": [], "sampling": False, "is_profile": True}}, "pricelistBiomaterialsData": {}}


def price_html(payload):
    codes = "".join('<div class="service-row"><div class="service-code">' + c +
                    '</div><a href="/service-description/posle-vakcinacii-0">Описание</a></div>'
                    for c in payload["pricelistServicesData"])
    return ('<div class="frontend-data" data-settings="' + html.escape(json.dumps(payload), quote=True) +
            '"></div>' + codes).encode("utf-8")


def catalogue(price=5760, code="62.056", promotion=True, next_page=None):
    old = ('<div class="old-price__value">6 195 ₽</div><div class="old-price__discount">-7<span>%</span></div>'
           if promotion else "")
    page = (f'<div class="analysis"><div class="code">{code}</div>'
            '<a class="name" href="/analizy-i-tseny/po-tipu/posle-vaktsinatsii/">После вакцинации</a>'
            '<div class="item-notice">Взятие 330 руб</div><div class="prices">' + old +
            f'<div class="price">{price:,} <span>₽</span></div></div></div>').replace(",", " ")
    if next_page:
        page += f"<button onclick=\"AskronUtil.loadAjaxPage('/analizy-i-tseny/po-tipu/?PAGEN_2={next_page}');\"></button>"
    return page.encode("utf-8")


class PricelistTests(unittest.TestCase):
    def test_exact_code_and_effective_price_are_retained(self):
        rows = sync.parse_pricelist(price_html(pricelist()), minimum_count=1)
        self.assertEqual(rows[0]["code"], "62.056")
        self.assertEqual(rows[0]["price_rub"], 5760)
        self.assertIsNone(rows[0]["urgent_price_rub"])
        self.assertIsNone(rows[0]["description"])
        self.assertEqual(rows[0]["source_url"], "https://results.dnkom.ru/service-description/posle-vakcinacii-0")

    def test_extended_codes_and_leading_zeroes_are_preserved(self):
        rows = sync.parse_pricelist(price_html(pricelist(code="26.138.02")), minimum_count=1)
        self.assertEqual(rows[0]["code"], "26.138.02")

    def test_invalid_prices_are_rejected(self):
        for bad in (0, -1, True, "5760", float("nan"), float("inf"), .001):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                sync.parse_pricelist(price_html(pricelist(price=bad)), minimum_count=1)

    def test_wrong_city_and_missing_field_are_rejected(self):
        payload = pricelist()
        payload["pricelistCityTid"] = "other"
        with self.assertRaises(ValueError):
            sync.parse_pricelist(price_html(payload), minimum_count=1)
        payload = pricelist()
        del payload["pricelistServicesData"]["62.056"]["price"]
        with self.assertRaises(ValueError):
            sync.parse_pricelist(price_html(payload), minimum_count=1)

    def test_incomplete_visible_rows_are_rejected(self):
        raw = price_html(pricelist()).replace(b"service-code", b"broken-class")
        with self.assertRaises(ValueError):
            sync.parse_pricelist(raw, minimum_count=1)

    def test_duplicate_json_keys_and_invalid_utf8_are_rejected(self):
        with self.assertRaises(ValueError):
            sync.unique_object([("price", 5), ("price", 7)])
        with self.assertRaises(UnicodeError):
            sync.parse_pricelist(b"\xff", minimum_count=1)

    def test_sampling_fee_stays_separate(self):
        payload = pricelist()
        base = payload["pricelistServicesData"]["62.056"]
        base["bioCodes"] = ["blood"]
        sample = deepcopy(base)
        sample.update(code="42.001", title="Взятие крови", price=330, bioCodes=[], sampling=True)
        payload["pricelistServicesData"]["42.001"] = sample
        payload["pricelistBiomaterialsData"] = {"blood": {"name": "Кровь", "samplingServices": [
            {"TargetCode": "42.001", "PatientGroup": "1", "DefaultTarget": True}]}}
        rows = sync.parse_pricelist(price_html(payload), minimum_count=1)
        service = next(r for r in rows if r["code"] == "62.056")
        self.assertEqual(service["price_rub"], 5760)
        self.assertEqual(service["biomaterials"][0]["sampling_options"][0]["price_rub"], 330)
        payload["pricelistBiomaterialsData"]["blood"]["samplingServices"][0]["TargetCode"] = "42.999"
        with self.assertRaises(ValueError):
            sync.parse_pricelist(price_html(payload), minimum_count=1)


class CatalogueTests(unittest.TestCase):
    def test_discount_original_current_and_percent_are_distinct(self):
        rows, next_url = sync.parse_catalogue(catalogue(next_page=2), sync.CATALOGUE_URL)
        self.assertEqual(rows[0]["price_rub"], 5760)
        self.assertEqual(rows[0]["old_price_rub"], 6195)
        self.assertEqual(rows[0]["discount_percent"], 7)
        self.assertEqual(next_url, sync.CATALOGUE_URL + "?PAGEN_2=2")

    def test_no_discount_is_not_a_zero_percent_promotion(self):
        rows, _ = sync.parse_catalogue(catalogue(promotion=False), sync.CATALOGUE_URL)
        self.assertIsNone(rows[0]["discount_percent"])
        self.assertIsNone(rows[0]["old_price_rub"])

    def test_missing_old_price_or_bad_percentage_is_rejected(self):
        for raw in (catalogue().replace(b"old-price__value", b"unknown"), catalogue().replace(b"-7<span>", b"-30<span>")):
            with self.assertRaises(ValueError):
                sync.parse_catalogue(raw, sync.CATALOGUE_URL)

    def test_pagination_must_move_to_immediate_next_page(self):
        with self.assertRaises(ValueError):
            sync.parse_catalogue(catalogue(next_page=4), sync.CATALOGUE_URL)


class DescriptionTests(unittest.TestCase):
    def test_raw_text_br_nested_headings_and_bibliography_do_not_replace_body(self):
        raw = ('<div class="code data">99.022</div><h2>Описание</h2>'
               '<div class="analysis-tab-data">Красота начинается изнутри.<br><br>'
               '<b>Цинк</b><br>Исходный текст о цинке.'
               '<h2>Подготовка</h2><p>Натощак.</p>'
               '<div class="article-sources"><h3>Список источников</h3><ol><li>Автор книги</li></ol></div>'
               '</div>').encode("utf-8")
        row = sync.parse_description(raw, "99.022", "https://dnkom.ru/analizy-i-tseny/test/", "2026-10-09T12:00:00+00:00")
        self.assertEqual(row["summary"], "Красота начинается изнутри.")
        self.assertIn("Исходный текст о цинке.", row["sections"][0]["text"])
        self.assertEqual(row["sections"][1], {"title": "Подготовка", "text": "Натощак."})
        self.assertNotIn("Список источников", json.dumps(row, ensure_ascii=False))

    def test_plain_text_preparation_source_no_generated_content(self):
        raw = ('<div class="code data">62.056</div><h2>Описание</h2>'
               '<div class="analysis-tab-data"><p>Текст источника.</p><script>bad()</script></div>'
               '<h2>Подготовка</h2><div class="analysis-tab-data"><p>Натощак.</p></div>').encode("utf-8")
        row = sync.parse_description(raw, "62.056", "https://dnkom.ru/analizy-i-tseny/test/", "2026-10-09T12:00:00+00:00")
        self.assertEqual(row["summary"], "Текст источника.")
        self.assertEqual(row["sections"][1], {"title": "Подготовка", "text": "Натощак."})
        self.assertNotIn("bad()", json.dumps(row))
        with self.assertRaises(ValueError):
            sync.parse_description(raw, "99.022", "test", "test")


class SynchronizeTests(unittest.TestCase):
    def test_crosscheck_mismatch_and_incomplete_catalogue_retain_last_good(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / "output.json"
            selection = root / "selection.json"
            selection.write_text('{"description_codes": []}', encoding="utf-8")
            (root / "pricelist.html").write_bytes(price_html(pricelist()))
            output.write_text('{"marker": "last-good", "services": []}', encoding="utf-8")
            original = output.read_bytes()
            args = argparse.Namespace(output=output, selection=selection, offline_dir=root,
                capture_dir=None, minimum_services=1, max_catalogue_pages=2, minimum_catalogue_services=1,
                minimum_catalogue_matches=1, description_budget=0, description_ttl_days=30)
            (root / "catalogue-001.html").write_bytes(catalogue(price=5755))
            with self.assertRaises(ValueError):
                sync.synchronize(args)
            self.assertEqual(output.read_bytes(), original)
            (root / "catalogue-001.html").write_bytes(catalogue(next_page=2))
            with self.assertRaises(OSError):
                sync.synchronize(args)
            self.assertEqual(output.read_bytes(), original)

    def test_effective_price_not_discounted_twice_and_expired_promotion_cleared(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / "output.json"
            selection = root / "selection.json"
            selection.write_text('{"description_codes": []}', encoding="utf-8")
            (root / "pricelist.html").write_bytes(price_html(pricelist()))
            (root / "catalogue-001.html").write_bytes(catalogue())
            args = argparse.Namespace(output=output, selection=selection, offline_dir=root,
                capture_dir=None, minimum_services=1, max_catalogue_pages=2, minimum_catalogue_services=1,
                minimum_catalogue_matches=1, description_budget=0, description_ttl_days=30)
            sync.synchronize(args)
            row = json.loads(output.read_text(encoding="utf-8"))["services"][0]
            self.assertEqual(row["price_rub"], 5760)
            self.assertEqual(row["old_price_rub"], 6195)
            (root / "catalogue-001.html").write_bytes(catalogue(promotion=False))
            sync.synchronize(args)
            row = json.loads(output.read_text(encoding="utf-8"))["services"][0]
            self.assertEqual(row["price_rub"], 5760)
            self.assertIsNone(row["old_price_rub"])
            self.assertEqual(row["discount_status"], "none")


if __name__ == "__main__":
    unittest.main()
