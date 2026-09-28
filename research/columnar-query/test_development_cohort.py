"""Offline synthetic presentation fixtures, never acquisition/research evidence."""
import copy
import json
import pathlib
import unittest
from development_cohort import PINS, PINS_BYTES, build_report, summarize_window, native_bytes
from manifest_reader import sha, attach_dataset
from collection_reader import attach_collection
from test_collection import plan_fixture
from mint_timeline import canonical


def fact(sample, slot, tx, buy, n, mint='A'*32, parent=None):
    record = {'sample_identity': sample, 'source': {'run_id': 'fixture-only', 'bindings': {'sample_identity': sample}},
              'transaction_status': 'OK', 'atomic_observation_package': True,
              'effective_at': {'slot': str(slot), 'transaction_index_in_slot': str(tx)},
              'event_reported': {'mint_address': mint, 'is_buy': buy, 'token_amount_raw_u64': '18446744073709551615', 'quote_amount_raw_u64': '0'},
              'bronze_record_sha256': parent or sha(str((slot, tx)).encode()), 'fixture_fact': n}
    raw = json.dumps(record, sort_keys=True, separators=(',', ':'))
    return {'record': record, 'canonical_record_json': raw, 'record_sha256': sha(raw.encode()), 'part_id': 0}


def window(i=0):
    pin = PINS['windows'][i]
    s = {'schema': 'OF1_B7_WINDOW_SAMPLE_1', 'sample_class': 'RESEARCH_SAMPLING',
         'start_slot': pin['range'][0], 'end_slot_exclusive': pin['range'][1],
         'b7': {'cohort_role': 'DEVELOPMENT', 'phase': 1, 'window_ordinal': i, 'selection_sha256': PINS['selection_sha256']}}
    w = {'ordinal': i, 'sample_identity': s, 'collection_sha256': pin['sha256'], 'layers': copy.deepcopy(pin['layers']),
         'counts': copy.deepcopy(pin['counts']), 'coverage': {'all_selected_slots_accounted': True, 'missing_selected_raw_slots': 0},
         'parts': [{}], 'facts': []}
    return w


def full_admission():
    windows = []
    for i in range(4):
        w = window(i); s = w['sample_identity']; start = s['start_slot']
        w['facts'] = [fact(s, start + (0 if n == 0 else 8), n, n == 0, n) for n in range(w['counts']['silver_facts'])]
        windows.append(w)
    return {'schema': 'OF1_B7_DEVELOPMENT_ADMISSION_1', 'pins_sha256': sha(PINS_BYTES),
            'selection_sha256': PINS['selection_sha256'], 'research_ready': False,
            'reader_source_sha256': 'a'*64, 'reader_binary_sha256': 'b'*64, 'windows': windows}


class CohortTests(unittest.TestCase):
    def test_boundary_atomic_duplicates_exact_values_and_one_pair_per_mint(self):
        w = window(); s = w['sample_identity']; start = s['start_slot']
        w['facts'] = [fact(s, start+7, 1, True, 0), fact(s, start+7, 1, True, 1),
                      fact(s, start+8, 0, False, 2), fact(s, start+15, 99, False, 3),
                      fact(s, start+15, 100, True, 4, 'B'*32)]
        w['counts']['silver_facts'] = 5
        r = summarize_window(w)
        self.assertEqual(r['halves'], {'FIRST_8': {'buy_facts': 2, 'sell_facts': 0, 'unique_mints': 1},
                                      'LAST_8': {'buy_facts': 1, 'sell_facts': 2, 'unique_mints': 2}})
        self.assertEqual(r['observed_pair_mints'], 1)
        m = r['mints'][0]; self.assertEqual(m['observed_pair']['buy_fact_sha256'], w['facts'][0]['record_sha256'])
        self.assertEqual(m['observed_pair']['sell_fact_sha256'], w['facts'][2]['record_sha256'])
        self.assertEqual(m['facts'][0]['package_id'], m['facts'][1]['package_id'])
        self.assertNotEqual(m['observed_pair']['buy_package_id'], m['observed_pair']['sell_package_id'])
        self.assertEqual(m['facts'][0]['record']['event_reported']['token_amount_raw_u64'], '18446744073709551615')
        self.assertEqual(r['mints'][1]['pair_state'], 'NO_PAIR_IN_ADMITTED_FACTS')
        self.assertFalse(r['semantic_coverage']['negative_conclusion_allowed'])

    def test_invalid_missing_failed_duplicate_outside_range_and_reordered_facts(self):
        for variant in ['failed', 'missing-amount', 'invalid-amount', 'duplicate', 'order', 'range', 'missing-binding', 'role', 'false-hash', 'part']:
            with self.subTest(variant=variant):
                a = full_admission(); w = a['windows'][0]; f = w['facts'][0]
                if variant == 'failed': f['record']['transaction_status'] = 'ERROR'
                if variant == 'missing-amount': del f['record']['event_reported']['token_amount_raw_u64']
                if variant == 'invalid-amount': f['record']['event_reported']['quote_amount_raw_u64'] = None
                if variant == 'duplicate': w['facts'][1] = copy.deepcopy(f)
                if variant == 'order': w['facts'].reverse()
                if variant == 'range': f['record']['effective_at']['slot'] = str(w['sample_identity']['end_slot_exclusive'])
                if variant == 'missing-binding': f['record']['source']['bindings'] = {}
                if variant == 'role': w['sample_identity']['b7']['cohort_role'] = 'RESERVED_EVALUATION'
                if variant == 'false-hash': f['record_sha256'] = '0'*64
                if variant == 'part': f['part_id'] = 1
                # Keep hashes valid for semantic mutations; the hash test deliberately does not.
                if variant in ['failed', 'missing-amount', 'invalid-amount', 'range', 'missing-binding']:
                    f['canonical_record_json'] = json.dumps(f['record'], sort_keys=True)
                    f['record_sha256'] = sha(f['canonical_record_json'].encode())
                with self.assertRaises((ValueError, KeyError)): build_report(a)

    def test_four_exact_snapshots_reproducible_totals_and_producer_binding(self):
        a = full_admission(); before = native_bytes(a); r = build_report(a)
        self.assertEqual(r['totals'], {'blocks': '64', 'packages': '80541', 'failures': '9505', 'silver_facts': '122'})
        self.assertEqual([w['counts']['silver_facts'] for w in r['windows']], ['57', '19', '13', '33'])
        self.assertEqual(canonical(r), canonical(build_report(a)))
        self.assertEqual(native_bytes(a), before)
        self.assertEqual(r['native_admission_sha256'], sha(before))
        for variant in ['changed-pin', 'duplicate-window', 'missing', 'mixed', 'selection']:
            wrong = copy.deepcopy(a)
            if variant == 'changed-pin': wrong['windows'][0]['collection_sha256'] = '0'*64
            if variant == 'duplicate-window': wrong['windows'][1] = wrong['windows'][0]
            if variant == 'missing': wrong['windows'].pop()
            if variant == 'mixed': wrong['windows'][0]['sample_identity'] = wrong['windows'][1]['sample_identity']
            if variant == 'selection': wrong['selection_sha256'] = '0'*64
            with self.assertRaises(ValueError): build_report(wrong)

    def test_no_generic_reader_or_export_b7_override(self):
        # Denial precedes any filesystem/DB access, including outcome-bearing exports.
        for role in ['DEVELOPMENT', 'RESERVED_EVALUATION']:
            sample = {'start_slot': 10, 'end_slot_exclusive': 15, 'b7': {'cohort_role': role}}
            plan = plan_fixture(); plan['sources'][0]['sample_identity'] = sample
            m = {'sample_identity': sample, 'plan': plan}
            for reader in [attach_dataset, attach_collection]:
                with self.assertRaisesRegex(ValueError, 'B7_ANALYTICAL_EXPORT_NOT_AUTHORIZED'):
                    reader(None, pathlib.Path('/never-open-this-fixture'), m)


if __name__ == '__main__': unittest.main()
