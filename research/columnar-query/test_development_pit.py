"""Offline fixtures for the bounded DEVELOPMENT chain-order presentation."""
import copy
import json
import unittest

from development_cohort import build_report, summarize_window
from development_pit import build_pit, html_report, project_window
from manifest_reader import sha
from test_development_cohort import fact, full_admission, window


def bound(f):
    record = f['record']; source = record['source']
    source.update({'raw_path': 'published/0000000004/raw.bin', 'raw_sha256': 'a' * 64,
                   'receipt_sequence': 4})
    source['bindings']['manifest_sha256'] = 'b' * 64
    source['bindings']['receipts'] = [{'sequence': 4, 'path': 'published/0000000004/receipt.json',
                                      'raw_sha256': 'a' * 64, 'sha256': 'd' * 64}]
    record['source_evidence_sha256'] = 'c' * 64
    record.update({'observed_at': None, 'observation_model_id': None, 'actionable_at': None,
                   'execution_opportunity_at': None, 'executable_price': None,
                   'quote_mint_identity': 'UNKNOWN', 'quote_decimals': None})
    f['canonical_record_json'] = json.dumps(record, sort_keys=True, separators=(',', ':'))
    f['record_sha256'] = sha(f['canonical_record_json'].encode())
    return f


def fixture_part():
    return {'part_id': 0, 'source_id': 'fixture-only',
            'parquet_manifest_path': '/fixture/manifest.json',
            'parquet_manifest_sha256': 'e' * 64,
            'decoder_source_sha256': 'f' * 64,
            'decoder_binary_sha256': '1' * 64,
            'writer': {'version': 'FIXTURE', 'source_sha256': '2' * 64,
                       'executable_sha256': '3' * 64}}


def summarized(facts):
    source = window()
    source['collection_path'] = '/fixture/collection.json'
    source['parts'] = [fixture_part()]
    source['facts'] = [bound(f) for f in facts]
    source['counts']['silver_facts'] = len(facts)
    return summarize_window(source)


class DevelopmentPitTests(unittest.TestCase):
    def test_later_sell_is_only_outcome_evidence(self):
        sample = window()['sample_identity']
        start = sample['start_slot']
        earlier = fact(sample, start + 7, 2, True, 0)
        later = fact(sample, start + 8, 0, False, 1)
        result = project_window(summarized([earlier, later]))
        self.assertEqual([f['pit_role'] for f in result['facts']],
                         ['EARLIER_BUY_CANDIDATE', 'LATER_SELL_OUTCOME_EVIDENCE'])
        self.assertEqual(result['decision_boundary']['before_slot'], str(start + 8))
        self.assertEqual(result['observed_positive_witnesses'][0]['earlier_buy_fact_sha256'],
                         earlier['record_sha256'])
        self.assertEqual(result['observed_positive_witnesses'][0]['later_sell_fact_sha256'],
                         later['record_sha256'])
        self.assertNotIn(later['record_sha256'],
                         [f['fact_sha256'] for f in result['facts']
                          if f['pit_role'] == 'EARLIER_BUY_CANDIDATE'])
        snapshot = result['candidate_mint_snapshots'][0]
        self.assertEqual(snapshot['chain_earlier_buy_candidate_hashes'], [earlier['record_sha256']])
        self.assertEqual(snapshot['later_sell_label_only_hashes'], [later['record_sha256']])
        self.assertIsNone(snapshot['historically_known_at_boundary'])
        self.assertEqual(result['negative_conclusion'], 'UNAVAILABLE_SEMANTIC_COVERAGE_NOT_ESTABLISHED')

    def test_atomic_parent_and_exact_values(self):
        sample = window()['sample_identity']; start = sample['start_slot']
        parent = 'f' * 64
        earlier_buy = fact(sample, start + 7, 2, True, 0, parent=parent)
        earlier_sell = fact(sample, start + 7, 2, False, 1, parent=parent)
        later_sell = fact(sample, start + 8, 1, False, 2)
        result = project_window(summarized([earlier_buy, earlier_sell, later_sell]))
        self.assertEqual(result['atomic_package_count_with_admitted_facts'], 2)
        self.assertEqual(result['facts'][0]['package_id'], result['facts'][1]['package_id'])
        self.assertEqual(result['facts'][0]['token_amount_raw_u64'], '18446744073709551615')
        self.assertIsNone(result['facts'][0]['quote_decimals'])
        self.assertEqual(result['facts'][0]['quote_mint_identity'], 'UNKNOWN')
        same_parent_after_boundary = fact(sample, start + 8, 1, False, 3, parent=parent)
        with self.assertRaises(ValueError):
            project_window(summarized([earlier_buy, same_parent_after_boundary]))

    def test_missing_causal_evidence_is_not_filled_or_inferred(self):
        sample = window()['sample_identity']; start = sample['start_slot']
        first = fact(sample, start, 0, True, 0)
        result = project_window(summarized([first]))
        self.assertEqual(result['observed_positive_witnesses'], [])
        self.assertEqual(result['candidate_mint_snapshots'][0]['negative_state'],
                         'UNAVAILABLE_INCOMPLETE_SEMANTIC_COVERAGE')
        self.assertEqual(result['causal_evidence']['status'],
                         'UNAVAILABLE_NO_HISTORICAL_INFORMATION_OR_EXECUTION_EVIDENCE')
        for name in ('observed_at', 'observation_model_id', 'actionable_at', 'decision_at',
                     'latency_model_id', 'execution_opportunity_at', 'executable_fill'):
            self.assertIsNone(result['causal_evidence'][name])
        altered = summarized([first]); altered['mints'][0]['facts'][0]['record']['observed_at'] = {'slot': start}
        with self.assertRaises(ValueError):
            project_window(altered)
        failed = fact(sample, start, 0, True, 1)
        failed['record']['transaction_status'] = 'ERROR'
        failed['canonical_record_json'] = json.dumps(failed['record'], sort_keys=True, separators=(',', ':'))
        failed['record_sha256'] = sha(failed['canonical_record_json'].encode())
        with self.assertRaises(ValueError):
            summarized([failed])

    def test_fixed_role_and_snapshot_refuse_evaluation_or_phase2(self):
        admission = full_admission()
        for window_data in admission['windows']:
            window_data['collection_path'] = '/fixture/collection.json'
            window_data['parts'] = [fixture_part()]
            window_data['facts'] = [bound(f) for f in window_data['facts']]
        cohort = build_report(admission)
        report = build_pit(cohort, cohort['native_admission_sha256'], 'a' * 64)
        self.assertEqual(report['totals']['silver_facts'], '122')
        self.assertEqual([len(w['facts']) for w in report['windows']], [57, 19, 13, 33])
        self.assertIn(b'UNAVAILABLE', html_report(report))
        wrong = copy.deepcopy(cohort)
        wrong['windows'][0]['sample_identity']['b7']['cohort_role'] = 'RESERVED_EVALUATION'
        with self.assertRaises(ValueError): build_pit(wrong, cohort['native_admission_sha256'], 'a' * 64)
        wrong = copy.deepcopy(cohort); wrong['windows'][1]['collection_sha256'] = '0' * 64
        # The sealed admission/report equality gate is separate; direct window
        # projection still must enforce the frozen per-window collection pin.
        with self.assertRaises(ValueError): build_pit(wrong, cohort['native_admission_sha256'], 'a' * 64)
        wrong = copy.deepcopy(cohort); wrong['schema'] = 'OF1_B7_DEVELOPMENT_COHORT_2'
        with self.assertRaises(ValueError): build_pit(wrong, cohort['native_admission_sha256'], 'a' * 64)
        with self.assertRaises(ValueError): build_pit(cohort, '0' * 64, 'a' * 64)


if __name__ == '__main__':
    unittest.main()
