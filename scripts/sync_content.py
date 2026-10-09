#!/usr/bin/env python3
"""Validate a private editorial workbook and atomically publish its approved rows.

Offline input is a UTF-8 JSON object with raw 2D tab values, not a public API.
Use --oauth only on the editor's local machine. CI uses a read-only service account
supplied as JSON in an environment variable; credentials never enter the output.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = ROOT / 'config/content-schema.json'
# Moscow has used UTC+03:00 continuously since 2014. Dates here apply to modern
# publication schedules; fixed offset avoids tzdata dependence on Windows.
MOSCOW = timezone(timedelta(hours=3), name='Europe/Moscow')
ID = re.compile(r'[a-z][a-z0-9_-]{0,79}\Z')
CODE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z', re.ASCII)
MAX_ROWS = 10000


class ContentError(ValueError):
    """An editorial validation error with row/column location and no cell content."""


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ContentError('Input is not readable UTF-8 JSON') from None


def text(value, where):
    if not isinstance(value, str):
        raise ContentError(f'{where}: expected a text cell')
    value = value.strip()
    if any(ord(c) < 32 and c not in '\n\r\t' for c in value):
        raise ContentError(f'{where}: control characters are not allowed')
    return value


def safe_url(value, where, *, image=False):
    if not value:
        return None
    if len(value) > 2048 or any(c.isspace() for c in value) or '\\' in value:
        raise ContentError(f'{where}: invalid URL')
    try:
        parts = urlsplit(value)
        if parts.scheme:
            if parts.scheme.lower() not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
                raise ContentError(f'{where}: use an http(s) URL without credentials')
            _ = parts.port  # Reject malformed ports.
            return value
        if not image or parts.netloc or parts.query or parts.fragment:
            raise ContentError(f'{where}: use an http(s) URL')
        path = value.removeprefix('./')
        decoded = unquote(path)
        if decoded != path:
            raise ContentError(f'{where}: unsafe asset path')
        if not path.startswith('assets/') or any(p in ('', '.', '..') for p in path.split('/')):
            raise ContentError(f'{where}: only paths inside assets/ are allowed')
        if PurePosixPath(path).suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp', '.avif', '.gif'):
            raise ContentError(f'{where}: unsupported image extension')
        return path
    except ValueError as exc:
        if isinstance(exc, ContentError):
            raise
        raise ContentError(f'{where}: invalid URL') from None


def parse_cell(raw, column, where, schema):
    value = text(raw, where)
    typ = column['type']
    if not value:
        if column.get('required'):
            raise ContentError(f'{where}: required value is missing')
        return [] if typ.endswith('_list') or typ == 'list' else None
    if len(value) > column.get('max_length', 8000):
        raise ContentError(f'{where}: text is too long')
    if typ in ('id', 'status', 'setting_key', 'date', 'integer', 'code_list', 'id_list', 'rating', 'review_rating', 'latitude', 'longitude', 'phone', 'email', 'digits'):
        if value.startswith('='):
            raise ContentError(f'{where}: formulas are not accepted')
    if typ == 'id':
        if not ID.fullmatch(value):
            raise ContentError(f'{where}: ID must use lowercase Latin letters, digits, _ and -')
    elif typ == 'status':
        if value not in schema['statuses']:
            raise ContentError(f'{where}: use draft, published or archived')
    elif typ == 'setting_key':
        if value not in schema['settings']:
            raise ContentError(f'{where}: unknown setting key')
    elif typ == 'integer':
        if len(value) > 6 or not re.fullmatch(r'[0-9]+', value, re.ASCII):
            raise ContentError(f'{where}: expected a nonnegative integer')
        value = int(value)
        if not column.get('min', 0) <= value <= column.get('max', 100000):
            raise ContentError(f'{where}: integer is outside the allowed range')
    elif typ in ('rating', 'review_rating', 'latitude', 'longitude'):
        try:
            number = Decimal(value.replace(',', '.'))
        except InvalidOperation:
            raise ContentError(f'{where}: expected a number') from None
        low, high = {'rating': (0, 5), 'review_rating': (1, 5), 'latitude': (-90, 90), 'longitude': (-180, 180)}[typ]
        if not number.is_finite() or not low <= number <= high:
            raise ContentError(f'{where}: number is outside the allowed range')
        if typ == 'review_rating' and number != number.to_integral_value():
            raise ContentError(f'{where}: review rating must be an integer from 1 to 5')
        value = int(number) if typ == 'review_rating' else float(number)
    elif typ == 'date':
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value, re.ASCII):
            raise ContentError(f'{where}: use YYYY-MM-DD')
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ContentError(f'{where}: invalid calendar date') from None
    elif typ in ('url', 'image_url'):
        value = safe_url(value, where, image=typ == 'image_url')
    elif typ in ('list', 'id_list', 'code_list'):
        values = [v.strip() for v in value.split(schema['separator'])]
        if not all(values) or len(values) != len(set(values)):
            raise ContentError(f'{where}: list contains an empty or duplicate entry')
        if typ == 'id_list' and not all(ID.fullmatch(v) for v in values):
            raise ContentError(f'{where}: list contains an invalid ID')
        if typ == 'code_list' and not all(CODE.fullmatch(v) for v in values):
            raise ContentError(f'{where}: analysis codes must keep the dot and leading zeros')
        value = values
    elif typ == 'phone':
        if not re.fullmatch(r'\+?[0-9 ()-]+', value) or not 7 <= len(re.sub(r'\D', '', value)) <= 15:
            raise ContentError(f'{where}: invalid public phone number')
    elif typ == 'email':
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
            raise ContentError(f'{where}: invalid email address')
    elif typ == 'digits':
        if not re.fullmatch(r'[0-9]{5,20}', value, re.ASCII):
            raise ContentError(f'{where}: expected 5–20 digits')
    elif typ == 'text':
        if re.search(r'<[^>]+>', value):
            raise ContentError(f'{where}: use plain text, not HTML')
    else:
        raise ContentError(f'{where}: unsupported schema type')
    return value


def rows_to_records(values, spec, schema):
    title = spec['title']
    if not isinstance(values, list) or not values or not all(isinstance(r, list) for r in values):
        raise ContentError(f'{title}: tab with a header row is required')
    if len(values) > MAX_ROWS:
        raise ContentError(f'{title}: tab exceeds the row limit')
    headers = [text(v, f'{title} row 1') for v in values[0]]
    expected = {c['header']: c for c in spec['columns']}
    if len(headers) != len(set(headers)):
        raise ContentError(f'{title}: duplicate column headers')
    if set(headers) != set(expected):
        raise ContentError(f'{title}: missing, renamed or unknown column headers')
    result = []
    for row_number, row in enumerate(values[1:], 2):
        if not row or not any(str(v).strip() for v in row):
            continue
        if len(row) > len(headers):
            raise ContentError(f'{title} row {row_number}: extra cells without column headers')
        record = {'_row': row_number, '_title': title}
        for i, header in enumerate(headers):
            column = expected[header]
            record[column['key']] = parse_cell(row[i] if i < len(row) else '', column, f'{title} row {row_number}, {header}', schema)
        result.append(record)
    return result


def ensure_unique(records, key):
    seen = set()
    for r in records:
        value = r[key]
        if value in seen:
            raise ContentError(f"{r['_title']} row {r['_row']}: duplicate {key}")
        seen.add(value)


def assert_reference(record, key, targets, *, required_public=True):
    value = record.get(key)
    if not value:
        return
    if value not in targets:
        raise ContentError(f"{record['_title']} row {record['_row']}: unknown {key}")
    if required_public and record['status'] == 'published' and targets[value]['status'] != 'published':
        raise ContentError(f"{record['_title']} row {record['_row']}: published row points to a draft or archived {key}")


def iso_now(value=None):
    if value is None:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        raise ContentError('--now must be an ISO 8601 timestamp with timezone') from None
    if dt.tzinfo is None:
        raise ContentError('--now must include timezone')
    return dt.astimezone(timezone.utc)


def normalize_snapshot(snapshot, schema, now=None, catalog_codes=None):
    if not isinstance(snapshot, dict) or snapshot.get('schema_version') != schema['schema_version']:
        raise ContentError('Snapshot schema_version does not match')
    if set(snapshot) - {'schema_version', 'source', 'tabs'}:
        raise ContentError('Unknown snapshot fields')
    if not isinstance(snapshot.get('tabs'), dict):
        raise ContentError('Snapshot tabs object is required')
    expected_tabs = {v['title'] for v in schema['tabs'].values()}
    if set(snapshot['tabs']) != expected_tabs:
        raise ContentError('Snapshot has missing or unknown data tabs')
    source = snapshot.get('source', {'type': 'offline'})
    if not isinstance(source, dict) or source.get('type') not in ('offline', 'google_sheets'):
        raise ContentError('Snapshot source must be offline or google_sheets')
    # Export only known non-secret source fields, never OAuth details or notes.
    public_source = {'type': source['type']}
    if source['type'] == 'google_sheets':
        sid = source.get('spreadsheet_id', '')
        if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,100}', sid):
            raise ContentError('Invalid spreadsheet ID')
        public_source.update({'spreadsheet_id': sid, 'url': f'https://docs.google.com/spreadsheets/d/{sid}'})
    records = {k: rows_to_records(snapshot['tabs'][v['title']], v, schema) for k, v in schema['tabs'].items()}
    for key in ('doctors', 'branches', 'reviews', 'promotions'):
        ensure_unique(records[key], 'id')
    ensure_unique(records['settings'], 'key')
    doctors = {r['id']: r for r in records['doctors']}
    branches = {r['id']: r for r in records['branches']}
    pairs = set()
    assignments = {key: [] for key in doctors}
    for r in records['doctor_branches']:
        pair = (r['doctor_id'], r['branch_id'])
        if pair in pairs:
            raise ContentError(f"{r['_title']} row {r['_row']}: duplicate doctor/branch pair")
        pairs.add(pair)
        assert_reference(r, 'doctor_id', doctors)
        assert_reference(r, 'branch_id', branches)
        if r['status'] == 'published':
            assignments[r['doctor_id']].append(r['branch_id'])
    for r in records['doctors']:
        if r['status'] == 'published' and not assignments[r['id']]:
            raise ContentError(f"{r['_title']} row {r['_row']}: published doctor needs a published branch assignment")
        if r.get('rating') is not None and r['status'] == 'published' and not r.get('rating_source_url'):
            raise ContentError(f"{r['_title']} row {r['_row']}: published rating needs a source URL")
    for r in records['branches']:
        if (r.get('latitude') is None) != (r.get('longitude') is None):
            raise ContentError(f"{r['_title']} row {r['_row']}: latitude and longitude must be filled together")
        if r['status'] == 'published' and not r.get('phone'):
            raise ContentError(f"{r['_title']} row {r['_row']}: published branch needs a public phone")
    for r in records['reviews']:
        assert_reference(r, 'doctor_id', doctors)
        assert_reference(r, 'branch_id', branches)
        if r['status'] == 'published' and not r.get('source_url'):
            raise ContentError(f"{r['_title']} row {r['_row']}: published review needs a source URL")
    for r in records['promotions']:
        if r['starts_on'] and r['ends_on'] and r['starts_on'] > r['ends_on']:
            raise ContentError(f"{r['_title']} row {r['_row']}: end date is before start date")
        for bid in r['branch_ids']:
            assert_reference(dict(r, branch_id=bid), 'branch_id', branches)
        if catalog_codes is not None and any(c not in catalog_codes for c in r['test_codes']):
            raise ContentError(f"{r['_title']} row {r['_row']}: unknown analysis code in catalogue")
        if r['status'] == 'published' and not r.get('terms'):
            raise ContentError(f"{r['_title']} row {r['_row']}: published promotion needs terms")
    settings = {}
    for r in records['settings']:
        col = {'type': schema['settings'][r['key']], 'required': True}
        value = parse_cell(r['value'], col, f"{r['_title']} row {r['_row']}, Значение", schema)
        if r['status'] == 'published':
            settings[r['key']] = value
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        raise ContentError('Publication timestamp needs timezone')
    today = clock.astimezone(MOSCOW).date().isoformat()
    out = {'schema_version': schema['schema_version'], 'timezone': schema['timezone'], 'generated_at': clock.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'), 'source': public_source, 'settings': dict(sorted(settings.items()))}
    for key in ('doctors', 'branches', 'reviews', 'promotions'):
        public = []
        for r in records[key]:
            if r['status'] != 'published':
                continue
            if key == 'promotions' and ((r['starts_on'] and today < r['starts_on']) or (r['ends_on'] and today > r['ends_on'])):
                continue
            clean = {k: v for k, v in r.items() if k not in ('_row', '_title', 'note')}
            if key == 'doctors':
                clean['branch_ids'] = sorted(assignments[r['id']])
            public.append(clean)
        out[key] = sorted(public, key=lambda r: (r['sort_order'], r['id']))
    return out


def write_atomically(path: Path, document):
    """Keep the last good output unchanged on validation/write errors or no change."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            previous = load_json(path)
            a, b = deepcopy(previous), deepcopy(document)
            a.pop('generated_at', None)
            b.pop('generated_at', None)
            if a == b:
                return False
        except ContentError:
            pass
    encoded = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n', prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        return True
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_workbook(sheet_id, schema, *, oauth=False, service_account_env='GOOGLE_SERVICE_ACCOUNT_JSON'):
    if not re.fullmatch(r'[A-Za-z0-9_-]{20,100}', sheet_id):
        raise ContentError('--sheet requires a spreadsheet ID, not URL')
    if oauth:
        sys.path.insert(0, str(Path.home() / '.agents/skills/stefania-google-sheets/scripts'))
        try:
            import gsheets_api
        except ImportError:
            raise ContentError('Local stefania-google-sheets helper is not installed') from None
        sh = gsheets_api._open(sheet_id)
    else:
        if not re.fullmatch(r'[A-Z][A-Z0-9_]*', service_account_env):
            raise ContentError('Invalid service-account environment variable name')
        secret = os.environ.get(service_account_env)
        if not secret:
            raise ContentError(f'{service_account_env} is missing; CI needs a read-only service account')
        try:
            import gspread
            credentials = json.loads(secret)
            client = gspread.service_account_from_dict(credentials, scopes=['https://www.googleapis.com/auth/spreadsheets.readonly'])
            sh = client.open_by_key(sheet_id)
        except (ImportError, json.JSONDecodeError, ValueError, KeyError):
            raise ContentError('Service-account runtime or credentials are not configured correctly') from None
    titles = [v['title'] for v in schema['tabs'].values()]
    actual = {ws.title for ws in sh.worksheets()}
    if not set(titles) <= actual:
        raise ContentError('Required workbook tabs are missing')
    # One batch read avoids reading different tabs across separate editor saves.
    response = sh.values_batch_get(ranges=["'" + t.replace("'", "''") + "'" for t in titles], params={'valueRenderOption': 'FORMATTED_VALUE'})
    ranges = response.get('valueRanges', [])
    if len(ranges) != len(titles):
        raise ContentError('Incomplete workbook response')
    return {'schema_version': schema['schema_version'], 'source': {'type': 'google_sheets', 'spreadsheet_id': sheet_id}, 'tabs': {title: r.get('values', []) for title, r in zip(titles, ranges)}}


def catalog_codes_from_file(path):
    document = load_json(path)
    if not isinstance(document, dict):
        raise ContentError('Catalogue must be an object')
    # Canonical DNKOM shape is services[].code. tests[] remains supported for
    # earlier offline research fixtures; ambiguous collections are rejected.
    keys = [k for k in ('services', 'tests') if k in document]
    if len(keys) != 1 or not isinstance(document[keys[0]], list):
        raise ContentError('Catalogue needs exactly one services or tests array')
    rows = document[keys[0]]
    if not rows or any(not isinstance(r, dict) or not isinstance(r.get('code'), str) or not CODE.fullmatch(r['code']) for r in rows):
        raise ContentError('Catalogue analysis codes are missing or invalid')
    codes = {r['code'] for r in rows}
    if len(codes) != len(rows):
        raise ContentError('Catalogue analysis codes must be unique')
    return codes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument('--snapshot', type=Path, help='Raw private tab snapshot JSON for offline validation')
    mode.add_argument('--sheet', help='Private Google spreadsheet ID')
    ap.add_argument('--schema', type=Path, default=DEFAULT_SCHEMA)
    ap.add_argument('--output', type=Path, default=ROOT/'data/content.json')
    ap.add_argument('--now', help='ISO 8601 timestamp with timezone for deterministic offline checks')
    ap.add_argument('--oauth', action='store_true', help='LOCAL ONLY: use the Stefania OAuth helper')
    ap.add_argument('--service-account-env', default='GOOGLE_SERVICE_ACCOUNT_JSON')
    ap.add_argument('--catalog', type=Path, help='Catalogue JSON with services[].code (legacy tests[] also accepted); checks promotion references')
    ap.add_argument('--check', action='store_true', help='Validate without writing output')
    args = ap.parse_args(argv)
    try:
        if args.oauth and not args.sheet:
            raise ContentError('--oauth requires --sheet')
        schema = load_json(args.schema)
        snapshot = load_json(args.snapshot) if args.snapshot else read_workbook(args.sheet, schema, oauth=args.oauth, service_account_env=args.service_account_env)
        normalized = normalize_snapshot(snapshot, schema, iso_now(args.now), catalog_codes_from_file(args.catalog) if args.catalog else None)
        changed = False if args.check else write_atomically(args.output, normalized)
        print(json.dumps({'ok': True, 'changed': changed, 'published': {k: len(normalized[k]) for k in ('doctors', 'branches', 'reviews', 'promotions')}, 'settings': len(normalized['settings'])}, ensure_ascii=True))
        return 0
    except ContentError as exc:
        print(f'Content validation failed: {exc}. Last good output retained.', file=sys.stderr)
        return 1
    except Exception as exc:
        # Transport exceptions may contain secrets or raw cells; print type only.
        print(f'Content sync failed ({type(exc).__name__}). Last good output retained.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
