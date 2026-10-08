"""Offline causal-boundary and fixed-snapshot regressions for the B8 prototype."""
import copy
import unittest

from development_baseline import build_baseline, html_report, project_window
from development_cohort import build_report
from development_pit import build_pit, project_window as project_pit_window
from manifest_reader import sha
from mint_timeline import canonical
from test_development_cohort import fact, full_admission, window
from test_development_pit import bound, fixture_part, summarized


class DevelopmentBaselineTests(unittest.TestCase):
    def test_later_fact_is_label_only_and_rule_uses_earlier_atomic_package(self):
        sample = window()['sample_identity']; start = sample['start_slot']
        first = fact(sample, start + 7, 2, True, 0)
        later = fact(sample, start + 8, 1, False, 1)
        pit = project_pit_window(summarized([first, later]))
        result = project_window(pit)
        self.assertEqual(result['prediction']['pair_probability'], '1')
        self.assertEqual(result['outcome']['label'], '1')
        self.assertEqual(result['feature']['first_half_buy_fact_sha256'], [first['record_sha256']])
        self.assertEqual(result['second_half_label_only_facts'][0]['fact_sha256'], later['record_sha256'])
        self.assertNotIn(later['record_sha256'], result['feature']['first_half_buy_fact_sha256'])
        changed = copy.deepcopy(pit)
        changed['observed_positive_witnesses'] = []
        changed['facts'] = [f for f in changed['facts'] if f['pit_role'].startswith('EARLIER_')]
        changed['candidate_mint_snapshots'][0]['observed_positive_witness'] = None
        changed['candidate_mint_snapshots'][0]['negative_state'] = 'UNAVAILABLE_INCOMPLETE_SEMANTIC_COVERAGE'
        changed['candidate_mint_snapshots'][0]['later_sell_label_only_hashes'] = []
        projected = project_window(changed)
        self.assertEqual(projected['prediction'], result['prediction'])
        self.assertIsNone(projected['outcome']['label'])
        self.assertEqual(projected['outcome']['state'], 'UNKNOWN_INCOMPLETE_PUMP_COVERAGE')
        self.assertEqual(projected['outcome']['unassessable_candidate_mints'][0]['mint'],
                         changed['candidate_mint_snapshots'][0]['mint'])

    def test_multiple_facts_one_package_exact_integer_and_nulls(self):
        sample = window()['sample_identity']; start = sample['start_slot']
        parent = 'e' * 64
        first = fact(sample, start + 7, 2, True, 0, parent=parent)
        second = fact(sample, start + 7, 2, False, 1, parent=parent)
        later = fact(sample, start + 8, 1, False, 2)
        result = project_window(project_pit_window(summarized([first, second, later])))
        self.assertEqual(len(result['first_half_admitted_facts']), 2)
        self.assertEqual(result['first_half_admitted_facts'][0]['package_id'],
                         result['first_half_admitted_facts'][1]['package_id'])
        self.assertEqual(result['feature']['first_half_package_count_with_admitted_facts'], '1')
        self.assertEqual(result['feature']['first_half_admitted_buy_count'], '1')
        self.assertEqual(result['first_half_admitted_facts'][0]['token_amount_raw_u64'],
                         '18446744073709551615')
        self.assertIsNone(result['unavailable']['historical_observed_at'])
        self.assertIsNone(result['first_half_admitted_facts'][0]['quote_decimals'])
        broken = project_pit_window(summarized([first, second, later]))
        broken['facts'][-1]['package_id'] = broken['facts'][0]['package_id']
        with self.assertRaises(ValueError):
            project_window(broken)

    def test_fixed_four_window_report_is_deterministic_and_denies_evaluation(self):
        admission = full_admission()
        for item in admission['windows']:
            item['collection_path'] = '/fixture/collection.json'
            item['parts'] = [fixture_part()]
            item['facts'] = [bound(f) for f in item['facts']]
        cohort = build_report(admission)
        pit = build_pit(cohort, cohort['native_admission_sha256'], 'a' * 64)
        report = build_baseline(pit, 'b' * 64, 'c' * 64)
        self.assertEqual(report['totals'], {'blocks': '64', 'packages': '80541',
                                           'failures': '9505', 'silver_facts': '122'})
        self.assertEqual([len(w['first_half_admitted_facts']) + len(w['second_half_label_only_facts'])
                          for w in report['windows']], [57, 19, 13, 33])
        self.assertEqual(report['assessment']['state'], 'INSUFFICIENT_SAMPLE')
        self.assertEqual(report['assessment']['known_positive_windows'], '4')
        self.assertEqual(report['assessment']['rule_brier'],
                         {'squared_error_sum': '0', 'denominator': '4'})
        self.assertEqual(report['assessment']['always_no_brier'],
                         {'squared_error_sum': '4', 'denominator': '4'})
        self.assertIn(b'INSUFFICIENT SAMPLE', html_report(report))
        self.assertEqual(sha(canonical(report)), sha(canonical(build_baseline(pit, 'b' * 64, 'c' * 64))))
        wrong = copy.deepcopy(pit)
        wrong['windows'][0]['sample_identity']['b7']['cohort_role'] = 'RESERVED_EVALUATION'
        with self.assertRaises(ValueError):
            build_baseline(wrong, 'b' * 64, 'c' * 64)
        # Native projection itself must reject this before the baseline can run.
        invalid = copy.deepcopy(cohort)
        invalid['windows'][0]['sample_identity']['b7']['cohort_role'] = 'RESERVED_EVALUATION'
        with self.assertRaises(ValueError):
            build_pit(invalid, cohort['native_admission_sha256'], 'a' * 64)
        wrong = copy.deepcopy(pit); wrong['evaluation_access'] = 'ALLOWED'
        with self.assertRaises(ValueError):
            build_baseline(wrong, 'b' * 64, 'c' * 64)


if __name__ == '__main__':
    unittest.main()
