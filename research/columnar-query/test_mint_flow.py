"""Synthetic arithmetic/binding fixtures, not authentic protocol evidence."""
import copy
import json
import pathlib
import tempfile
import unittest

from mint_flow import build_summary, produce
from mint_timeline import canonical, exact, summarize
from manifest_reader import sha

FIXTURE = json.loads((pathlib.Path(__file__).resolve().parents[2] / 'tests/fixtures/mint-flow.json').read_text())


def inputs():
    i = copy.deepcopy(FIXTURE['inspection'])
    return i['timeline'], i['lifecycle'], i['inputs']


def refresh(t, l):
    t['counts'] = exact(summarize(t['transactions']))
    l['counts'] = copy.deepcopy(t['counts'])
    l['facts'] = [{'silver_record_sha256': f['record_sha256']} for p in t['transactions'] for f in p['silver_facts']]


class MintFlowTests(unittest.TestCase):
    def test_independent_sums_repeated_users_atomic_prefixes_and_classes(self):
        t, l, h = inputs()
        before = canonical(t)
        r = build_summary(t, l, h)
        self.assertEqual(r['totals']['ALL'], {'buy_token_raw': '11', 'sell_token_raw': '10',
                         'gross_token_raw': '21', 'net_token_raw': '1', 'unique_event_users': '2',
                         'buy_facts': '2', 'sell_facts': '1', 'facts': '3'})
        self.assertEqual([p['cumulative']['ALL']['net_token_raw'] for p in r['packages']], ['-3', '1', '1'])
        self.assertEqual(r['packages'][0]['cumulative']['ALL']['unique_event_users'], '1')
        self.assertEqual(r['packages'][0]['fact_hashes'], ['9'*64, 'd'*64])
        self.assertEqual(r['totals']['ORIGINAL_SELECTION']['net_token_raw'], '-3')
        self.assertEqual(r['totals']['POSTHOC_DESCRIPTIVE_CONTEXT']['net_token_raw'], '4')
        self.assertEqual(r['packages'][2]['package_totals']['facts'], '0')
        self.assertEqual(before, canonical(t))
        self.assertEqual(canonical(r), canonical(build_summary(t, l, h)))
        # Shared golden contract consumed independently by adapter/UI tests.
        expected = copy.deepcopy(FIXTURE['flow']['report']); expected['producer'] = r['producer']
        self.assertEqual(r, expected)

    def test_aggregates_exceed_u64_and_zero_is_a_real_valid_observation(self):
        t, l, h = inputs()
        for p in t['transactions']:
            for f in p['silver_facts']:
                f['record']['event_reported']['token_amount_raw_u64'] = '18446744073709551615'
        r = build_summary(t, l, h)
        self.assertEqual(r['totals']['ALL']['gross_token_raw'], '55340232221128654845')
        self.assertEqual(r['totals']['ALL']['net_token_raw'], '18446744073709551615')
        t['transactions'][0]['silver_facts'][1]['record']['event_reported']['token_amount_raw_u64'] = '0'
        r = build_summary(t, l, h)
        self.assertEqual(r['packages'][0]['cumulative']['ALL']['net_token_raw'], '-18446744073709551615')
        self.assertEqual(r['packages'][0]['package_totals']['facts'], '2')

    def test_required_amount_and_user_never_default_or_disappear(self):
        for key, invalid in [('token_amount_raw_u64', [None, '', 1, True, '-1', '01', '1.5', '18446744073709551616']),
                             ('user_address', [None, '', 1, ' user', 'x'*129])]:
            for value in invalid + ['DELETE_FIELD']:
                with self.subTest(key=key, value=value):
                    t, l, h = inputs(); event = t['transactions'][0]['silver_facts'][0]['record']['event_reported']
                    if value == 'DELETE_FIELD':
                        del event[key]
                    else:
                        event[key] = value
                    with self.assertRaises((ValueError, KeyError)):
                        build_summary(t, l, h)

    def test_failed_facts_duplicates_order_class_and_bindings_fail_closed(self):
        for variant in ['failed', 'duplicate-fact', 'duplicate-package', 'order', 'class', 'mint', 'parent', 'binding', 'lifecycle']:
            with self.subTest(variant=variant):
                t, l, h = inputs(); p = t['transactions'][0]
                if variant == 'failed': p['transaction_status'] = 'ERROR'
                if variant == 'duplicate-fact': p['silver_facts'][1]['record_sha256'] = p['silver_facts'][0]['record_sha256']
                if variant == 'duplicate-package': t['transactions'][1] = copy.deepcopy(p)
                if variant == 'order': t['transactions'].reverse()
                if variant == 'class': p['slice_class'] = 'ENGINEERING_VALIDATION_ONLY'
                if variant == 'mint': p['silver_facts'][0]['record']['event_reported']['mint_address'] = 'other'
                if variant == 'parent': p['silver_facts'][0]['record']['bronze_record_sha256'] = 'f'*64
                if variant == 'binding': h['timeline'] = 'bad'
                refresh(t, l)
                if variant == 'lifecycle': l['facts'].pop()
                with self.assertRaises(ValueError): build_summary(t, l, h)

    def test_same_address_across_classes_deduplicates_only_within_each_selection(self):
        t, l, h = inputs()
        t['transactions'][1]['silver_facts'][0]['record']['event_reported']['user_address'] = 'reported-user-A'
        r = build_summary(t, l, h)
        self.assertEqual([r['totals'][k]['unique_event_users'] for k in ('ALL', 'ORIGINAL_SELECTION', 'POSTHOC_DESCRIPTIVE_CONTEXT')], ['1', '1', '1'])

    def test_registered_bytes_output_hash_and_no_publication_on_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp).resolve(); t, l, _ = inputs()
            raw_plan = b'{}'; plan_hash = sha(raw_plan)
            c = {'schema': 'OF1_BATCH_COLLECTION_1', 'state': 'COMPLETE', 'research_ready': False, 'plan_sha256': plan_hash}
            raw_collection = json.dumps(c).encode(); collection_hash = sha(raw_collection)
            t['bindings'].update(collection_sha256=collection_hash, plan_sha256=plan_hash)
            l['collection'].update(sha256=collection_hash, plan_sha256=plan_hash)
            blobs = {'plan': raw_plan, 'collection': raw_collection, 'timeline': canonical(t), 'lifecycle': canonical(l)}
            registry = {'schema': 'OF1_MINT_INSPECTOR_REGISTRY_1', 'inputs': {k: {'path': k+'.json', 'sha256': sha(v)} for k, v in blobs.items()}}
            for k, v in blobs.items(): (root/(k+'.json')).write_bytes(v)
            (root/'registry.json').write_text(json.dumps(registry))
            produce(root, root/'registry.json', root/'result')
            execution = json.loads((root/'result/execution.json').read_bytes())
            self.assertEqual(execution['result_sha256'], sha((root/'result/flow.json').read_bytes()))
            with self.assertRaises(ValueError): produce(root, root/'registry.json', root/'result')
            (root/'timeline.json').write_bytes(b'{}')
            with self.assertRaises(ValueError): produce(root, root/'registry.json', root/'bad-result')
            self.assertFalse((root/'bad-result').exists())


if __name__ == '__main__':
    unittest.main()
