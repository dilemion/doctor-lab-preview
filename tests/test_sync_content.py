"""Semantic checks for the private Sheets -> public site publication boundary."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sync_content', ROOT/'scripts/sync_content.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)
SCHEMA = sync.load_json(ROOT/'config/content-schema.json')
DRAFT = sync.load_json(ROOT/'tests/fixtures/content-synthetic.json')
NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


def set_cell(snapshot, tab_key, row, key, value):
    table = snapshot['tabs'][SCHEMA['tabs'][tab_key]['title']]
    header = next(c['header'] for c in SCHEMA['tabs'][tab_key]['columns'] if c['key'] == key)
    table[row][table[0].index(header)] = value


def published_fixture():
    snapshot = deepcopy(DRAFT)
    for key, spec in SCHEMA['tabs'].items():
        table = snapshot['tabs'][spec['title']]
        idx = table[0].index('Статус')
        for row in table[1:]:
            row[idx] = 'published'
    for i in range(1, 7):
        set_cell(snapshot, 'doctors', i, 'rating_source_url', 'https://example.org/public-rating')
    for i in range(1, 4):
        set_cell(snapshot, 'branches', i, 'phone', '+7 495 123-45-67')
        set_cell(snapshot, 'reviews', i, 'source_url', 'https://example.org/public-review')
    return snapshot


class ContentPublicationTests(unittest.TestCase):
    def normalized(self, snapshot=None, now=NOW, codes=None):
        return sync.normalize_snapshot(snapshot if snapshot is not None else published_fixture(), SCHEMA, now, codes)

    def test_drafts_are_not_exposed_as_real_doctors_or_reviews(self):
        out = self.normalized(deepcopy(DRAFT))
        self.assertEqual(out['doctors'], [])
        self.assertEqual(out['branches'], [])
        self.assertEqual(out['reviews'], [])
        self.assertEqual(out['promotions'], [])
        self.assertEqual(out['settings'], {})
        self.assertNotIn('Демо', json.dumps(out, ensure_ascii=False))

    def test_valid_publication_joins_doctors_with_branches_without_private_notes(self):
        out = self.normalized()
        self.assertEqual(len(out['doctors']), 6)
        self.assertEqual(len(out['branches']), 3)
        self.assertEqual(len(out['reviews']), 3)
        self.assertEqual(len(out['promotions']), 6)
        self.assertEqual(out['doctors'][0]['branch_ids'], ['branch-001', 'branch-002'])
        self.assertEqual(out['settings']['site_name'], 'Доктор и лаборатория')
        for key in ('doctors', 'branches', 'reviews', 'promotions'):
            self.assertTrue(all(row['status'] == 'published' and 'note' not in row for row in out[key]))

    def test_unicode_survives_json_and_id_sorting_is_deterministic(self):
        snapshot = published_fixture()
        for i in range(1, 7):
            set_cell(snapshot, 'doctors', i, 'sort_order', '10')
        out = self.normalized(snapshot)
        self.assertEqual([r['id'] for r in out['doctors']], sorted(r['id'] for r in out['doctors']))
        self.assertEqual(json.loads(json.dumps(out, ensure_ascii=False))['doctors'][0]['name'], out['doctors'][0]['name'])

    def test_duplicate_ids_rejected_even_for_hidden_draft(self):
        snapshot = deepcopy(DRAFT)
        set_cell(snapshot, 'doctors', 2, 'id', 'doctor-001')
        with self.assertRaisesRegex(sync.ContentError, 'duplicate id'):
            self.normalized(snapshot)

    def test_duplicate_doctor_branch_pairs_rejected(self):
        snapshot = deepcopy(DRAFT)
        table = snapshot['tabs'][SCHEMA['tabs']['doctor_branches']['title']]
        table.append(deepcopy(table[1]))
        with self.assertRaisesRegex(sync.ContentError, 'duplicate doctor/branch'):
            self.normalized(snapshot)

    def test_unknown_foreign_key_rejected_in_any_status(self):
        snapshot = deepcopy(DRAFT)
        set_cell(snapshot, 'doctor_branches', 1, 'branch_id', 'branch-missing')
        with self.assertRaisesRegex(sync.ContentError, 'unknown branch_id'):
            self.normalized(snapshot)

    def test_published_assignment_cannot_point_to_an_archived_branch(self):
        snapshot = published_fixture()
        set_cell(snapshot, 'branches', 1, 'status', 'archived')
        with self.assertRaisesRegex(sync.ContentError, 'draft or archived'):
            self.normalized(snapshot)

    def test_published_doctor_needs_a_published_assignment(self):
        snapshot = published_fixture()
        table = snapshot['tabs'][SCHEMA['tabs']['doctor_branches']['title']]
        idx = table[0].index('Статус')
        for row in table[1:]:
            row[idx] = 'draft'
        with self.assertRaisesRegex(sync.ContentError, 'needs a published branch assignment'):
            self.normalized(snapshot)

    def test_published_review_rating_and_source_are_required(self):
        for key, value in [('source_url', ''), ('rating', '0'), ('rating', '4.5')]:
            with self.subTest(key=key, value=value):
                snapshot = published_fixture()
                set_cell(snapshot, 'reviews', 1, key, value)
                with self.assertRaises(sync.ContentError):
                    self.normalized(snapshot)

    def test_published_doctor_rating_requires_real_source(self):
        snapshot = published_fixture()
        set_cell(snapshot, 'doctors', 1, 'rating_source_url', '')
        with self.assertRaisesRegex(sync.ContentError, 'rating needs a source'):
            self.normalized(snapshot)

    def test_extra_duplicate_and_missing_headers_rejected(self):
        for mut in ('extra', 'duplicate', 'missing'):
            with self.subTest(mut=mut):
                snapshot = deepcopy(DRAFT)
                table = snapshot['tabs'][SCHEMA['tabs']['reviews']['title']]
                if mut == 'extra':
                    table[0].append('Секретное_поле')
                elif mut == 'duplicate':
                    table[0][1] = table[0][0]
                else:
                    table[0].pop()
                with self.assertRaises(sync.ContentError):
                    self.normalized(snapshot)

    def test_missing_tabs_wrong_schema_and_unknown_root_fields_rejected(self):
        for mut in ('missing-tab', 'schema', 'root'):
            snapshot = deepcopy(DRAFT)
            if mut == 'missing-tab':
                snapshot['tabs'].pop(SCHEMA['tabs']['reviews']['title'])
            elif mut == 'schema':
                snapshot['schema_version'] = 2
            else:
                snapshot['patient_data'] = []
            with self.assertRaises(sync.ContentError):
                self.normalized(snapshot)

    def test_false_status_boolean_cells_and_nonfinite_numbers_rejected(self):
        for key, value in [('status', 'false'), ('status', False), ('sort_order', True), ('sort_order', '-1'), ('rating', 'NaN'), ('rating', 'Infinity')]:
            snapshot = deepcopy(DRAFT)
            set_cell(snapshot, 'doctors', 1, key, value)
            with self.assertRaises(sync.ContentError):
                self.normalized(snapshot)

    def test_malicious_urls_and_asset_traversal_rejected(self):
        for value in ['javascript:alert(1)', 'data:text/html,payload', '//attacker.example/a.jpg', 'assets/../a.jpg', 'assets/%2e%2e/a.jpg', 'assets/%252e%252e/a.jpg', 'assets/a.svg', 'https://user:pass@example.org/a.jpg']:
            snapshot = deepcopy(DRAFT)
            set_cell(snapshot, 'doctors', 1, 'photo_url', value)
            with self.assertRaises(sync.ContentError):
                self.normalized(snapshot)
        snapshot = deepcopy(DRAFT)
        set_cell(snapshot, 'doctors', 1, 'photo_url', './assets/doctor-01.webp')
        self.normalized(snapshot)  # A safe asset path remains accepted.

    def test_plain_text_contract_rejects_html(self):
        snapshot = deepcopy(DRAFT)
        set_cell(snapshot, 'reviews', 1, 'text', '<img src=x onerror=alert(1)>')
        with self.assertRaisesRegex(sync.ContentError, 'not HTML'):
            self.normalized(snapshot)

    def test_invalid_calendar_date_and_reversed_range_rejected(self):
        for start, end in [('2026-02-30', ''), ('09.10.2026', ''), ('2026-10-10', '2026-10-09')]:
            snapshot = published_fixture()
            set_cell(snapshot, 'promotions', 1, 'starts_on', start)
            set_cell(snapshot, 'promotions', 1, 'ends_on', end)
            with self.assertRaises(sync.ContentError):
                self.normalized(snapshot)

    def test_promotion_boundaries_use_moscow_and_include_end_date(self):
        snapshot = published_fixture()
        set_cell(snapshot, 'promotions', 1, 'starts_on', '2026-10-10')
        set_cell(snapshot, 'promotions', 1, 'ends_on', '2026-10-10')
        before = sync.iso_now('2026-10-09T20:59:59Z')  # 23:59:59 in Moscow.
        active = sync.iso_now('2026-10-09T21:00:00Z')  # 00:00 on Oct 10.
        end = sync.iso_now('2026-10-10T20:59:59Z')
        after = sync.iso_now('2026-10-10T21:00:00Z')
        ids = lambda now: [r['id'] for r in self.normalized(snapshot, now=now)['promotions']]
        self.assertNotIn('promo-001', ids(before))
        self.assertIn('promo-001', ids(active))
        self.assertIn('promo-001', ids(end))
        self.assertNotIn('promo-001', ids(after))

    def test_future_archived_and_expired_promotions_hidden(self):
        snapshot = published_fixture()
        set_cell(snapshot, 'promotions', 1, 'starts_on', '2026-10-11')
        set_cell(snapshot, 'promotions', 2, 'ends_on', '2026-10-08')
        set_cell(snapshot, 'promotions', 3, 'status', 'archived')
        ids = [r['id'] for r in self.normalized(snapshot)['promotions']]
        self.assertNotIn('promo-001', ids)
        self.assertNotIn('promo-002', ids)
        self.assertNotIn('promo-003', ids)

    def test_promotion_codes_preserve_leading_zeros_and_require_catalogue_membership(self):
        snapshot = published_fixture()
        set_cell(snapshot, 'promotions', 1, 'test_codes', '62.056; 10.308; 99.022')
        out = self.normalized(snapshot, codes={'62.056', '10.308', '99.022'})
        self.assertEqual(out['promotions'][0]['test_codes'], ['62.056', '10.308', '99.022'])
        with self.assertRaisesRegex(sync.ContentError, 'unknown analysis code'):
            self.normalized(snapshot, codes={'62.056', '10.308', '12.105.01'})
        set_cell(snapshot, 'promotions', 1, 'status', 'draft')
        with self.assertRaisesRegex(sync.ContentError, 'unknown analysis code'):
            self.normalized(snapshot, codes={'62.056'})
        set_cell(snapshot, 'promotions', 1, 'test_codes', '62.056; 62.056')
        with self.assertRaises(sync.ContentError):
            self.normalized(snapshot)

    def test_future_promotion_foreign_keys_still_validated(self):
        snapshot = published_fixture()
        set_cell(snapshot, 'promotions', 1, 'starts_on', '2027-01-01')
        set_cell(snapshot, 'promotions', 1, 'branch_ids', 'branch-nonexistent')
        with self.assertRaisesRegex(sync.ContentError, 'unknown branch_id'):
            self.normalized(snapshot)

    def test_published_promotion_requires_terms(self):
        snapshot = published_fixture()
        set_cell(snapshot, 'promotions', 1, 'terms', '')
        with self.assertRaisesRegex(sync.ContentError, 'needs terms'):
            self.normalized(snapshot)

    def test_unknown_and_duplicate_settings_rejected(self):
        snapshot = deepcopy(DRAFT)
        set_cell(snapshot, 'settings', 1, 'key', 'api_token')
        with self.assertRaisesRegex(sync.ContentError, 'unknown setting'):
            self.normalized(snapshot)
        snapshot = deepcopy(DRAFT)
        set_cell(snapshot, 'settings', 2, 'key', 'site_name')
        with self.assertRaisesRegex(sync.ContentError, 'duplicate key'):
            self.normalized(snapshot)

    def test_catalogue_services_shape_validates_exact_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'catalog.json'
            p.write_text(json.dumps({'services': [{'code': '62.056'}, {'code': '10.308'}, {'code': '12.105.01'}]}), encoding='utf-8')
            self.assertEqual(sync.catalog_codes_from_file(p), {'62.056', '10.308', '12.105.01'})
            for doc in ({'services': [{'code': 62.056}]}, {'services': []}, {'services': [{'code': '62.056'}, {'code': '62.056'}]}, {'services': [], 'tests': []}):
                p.write_text(json.dumps(doc), encoding='utf-8')
                with self.assertRaises(sync.ContentError):
                    sync.catalog_codes_from_file(p)

    def test_atomic_failure_retains_the_previous_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/'content.json'
            original = b'{"last_good": true}\n'
            out.write_bytes(original)
            with patch.object(sync.os, 'replace', side_effect=OSError('simulated failure')):
                with self.assertRaises(OSError):
                    sync.write_atomically(out, self.normalized())
            self.assertEqual(out.read_bytes(), original)
            self.assertEqual(list(Path(tmp).glob('*.tmp')), [])

    def test_no_change_keeps_timestamp_and_lf_unicode_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/'content.json'
            document = self.normalized()
            self.assertTrue(sync.write_atomically(out, document))
            original = out.read_bytes()
            updated = deepcopy(document)
            updated['generated_at'] = '2026-10-10T12:00:00Z'
            self.assertFalse(sync.write_atomically(out, updated))
            self.assertEqual(out.read_bytes(), original)
            self.assertNotIn(b'\r\n', original)
            self.assertIn('Доктор'.encode('utf-8'), original)

    def test_cli_validation_error_keeps_the_last_good_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, snapshot = Path(tmp)/'content.json', Path(tmp)/'snapshot.json'
            original = b'{"last_good": true}\n'
            out.write_bytes(original)
            invalid = deepcopy(DRAFT)
            set_cell(invalid, 'reviews', 1, 'status', 'false')
            snapshot.write_text(json.dumps(invalid, ensure_ascii=False), encoding='utf-8')
            self.assertEqual(sync.main(['--snapshot', str(snapshot), '--output', str(out), '--now', '2026-10-09T12:00:00Z']), 1)
            self.assertEqual(out.read_bytes(), original)
            snapshot.write_bytes(b'\xff\xfe\x00bad')
            self.assertEqual(sync.main(['--snapshot', str(snapshot), '--output', str(out)]), 1)
            self.assertEqual(out.read_bytes(), original)

    def test_naive_timestamp_not_accepted(self):
        with self.assertRaises(sync.ContentError):
            sync.iso_now('2026-10-09T12:00:00')


if __name__ == '__main__':
    unittest.main()
