"""Synthetic projected records test report semantics, never protocol decoding."""
import copy
import json
import pathlib
import unittest
from unittest.mock import patch

import mint_timeline as timeline
from query import connect

MINT = 'selected-mint'
SQL = json.loads(pathlib.Path(__file__).with_name('mint-timeline.sql.json').read_text())


def packed(record):
    raw = json.dumps(record, sort_keys=True, separators=(',', ':')).encode()
    return timeline.sha(raw), raw + b'\n'


def package(index, keys=None, balances=None, status='OK', diagnoses=None, inner=True):
    tx = {'account_keys': keys or [], 'status': status,
          'transaction_error': {'synthetic_failure': 'InstructionError'} if status == 'ERROR' else None,
          'instructions': [{'index': 0, 'program_id': 'fixture', 'account_indexes': []},
                           {'index': 1, 'program_id': 'fixture', 'account_indexes': [0]}],
          'inner_instructions': [{'outer_index': 0, 'inner_order': 0, 'program_id': 'fixture-cpi',
                                  'stack_height': 2, 'account_indexes': [0]}] if inner else None,
          'token_balance_context': {'observations': balances or [], 'collection_status': 'OBSERVATIONS_RECORDED'},
          'pump_sell_analysis': diagnoses or [], 'signatures': ['fixture-signature']}
    return {'transaction': tx, 'effective_at': {'slot': '40', 'transaction_index_in_slot': index},
            'disposition': 'DECODED', 'atomic_observation_package': True,
            'slice_class': 'ENGINEERING_VALIDATION_ONLY', 'sample_identity': None,
            'source': {'raw_sha256': 'a'*64, 'receipt_sequence': 4, 'run_id': 'fixture-run',
                       'acquired_at_unix_ms': str(10000-index),
                       'bindings': {'receipts': [{'sequence': 4, 'sha256': 'b'*64, 'raw_sha256': 'a'*64}]}}}


def observation(side, ordinal=0):
    return {'mint': MINT, 'side': side, 'ordinal': ordinal, 'amount_string': str(2**64-1),
            'amount_u64': str(2**64-1), 'decimals': 6, 'exact_decimal_amount': '18446744073709.551615',
            'disposition': 'PROJECTED', 'account_key': 'fixture-account'}


def create_tables(db):
    db.execute('CREATE TABLE bronze(collection_source_id VARCHAR, record_sha256 VARCHAR, collection_role VARCHAR, '
               'collection_batch_id VARCHAR,slot UBIGINT,transaction_index UBIGINT,record_bytes BLOB, '
               'token_balance_observations STRUCT(mint VARCHAR)[])')
    db.execute('CREATE TABLE silver(collection_source_id VARCHAR, bronze_record_sha256 VARCHAR, '
               'record_sha256 VARCHAR,record_bytes BLOB,mint VARCHAR)')


def insert(db, r, source='source-A', role='POSTHOC_DESCRIPTIVE_CONTEXT', sides=(), duplicate=False):
    parent, raw = packed(r)
    data = [source, parent, role, 'batch', int(r['effective_at']['slot']),
            r['effective_at']['transaction_index_in_slot'], raw,
            [{'mint': o['mint']} for o in r['transaction']['token_balance_context']['observations']]]
    db.execute('INSERT INTO bronze VALUES (?,?,?,?,?,?,?,?)', data)
    if duplicate:
        db.execute('INSERT INTO bronze VALUES (?,?,?,?,?,?,?,?)', data)
    for n, side in enumerate(sides):
        record = {'bronze_record_sha256': parent, 'source': r['source'],
                  'event_context': {'outer_index': n, 'inner_order': 2},
                  'event_reported': {'mint_address': MINT, 'is_buy': side, 'ix_name': 'buy' if side else 'sell',
                                     'token_amount_raw_u64': str(2**64-1)},
                  'token_balance_context': {'decimals': 6, 'roles': [
                      {'transaction_delta_raw_signed': '-9007199254740993',
                       'delta_status': 'EXACT_TRANSACTION_DELTA'}]}}
        digest, body = packed(record)
        db.execute('INSERT INTO silver VALUES (?,?,?,?,?)', [source, parent, digest, body, MINT])
        if duplicate:
            db.execute('INSERT INTO silver VALUES (?,?,?,?,?)', [source, parent, digest, body, MINT])
    return parent


class MintTimelineTests(unittest.TestCase):
    def test_multiple_instructions_diagnoses_balances_and_facts_do_not_multiply_packages(self):
        dx = {'outer_index': 0, 'instruction_inner_order': 0, 'disposition': 'NOT_ADMITTED',
              'reason': 'MISSING_EVIDENCE', 'event_reported': {'mint_address': MINT}}
        r = package(2, [MINT], [observation('PRE'), observation('POST')], diagnoses=[dx, dx])
        with connect() as db:
            create_tables(db)
            insert(db, r, sides=(True, False), duplicate=True)
            cards = timeline.query_timeline(db, MINT, SQL)
        counts = timeline.summarize(cards)
        self.assertEqual(counts['transactions'], 1)
        self.assertEqual((counts['declared_top_level_instructions'], counts['recorded_cpi_instructions']), (2, 1))
        self.assertEqual(counts['profile_diagnoses'], 2)  # preserve evaluator entries, don't dedup them
        self.assertEqual(counts['diagnosed_instruction_positions'], 1)
        self.assertEqual(counts['balance_observations'], 2)
        self.assertEqual((counts['silver_facts'], counts['buys'], counts['sells']), (2, 1, 1))
        self.assertEqual([(i['outer_index'], i['inner_order']) for i in cards[0]['instructions']],
                         [(0, None), (0, 0), (1, None)])
        self.assertTrue(all(cards[0]['inclusion'].values()))

    def test_independent_channels_failures_unknown_attribution_and_exact_references(self):
        unknown = {'outer_index': 1, 'disposition': 'NOT_ADMITTED', 'reason': 'NO_EVENT',
                   'accounts': [{'role': 'mint', 'expected': MINT, 'observed': MINT}]}
        explicit = {'outer_index': 1, 'event_reported': {'mint_address': MINT}, 'disposition': 'NOT_ADMITTED'}
        records = [package(5, ['prefix-'+MINT]), package(4, diagnoses=[unknown]),
                   package(3, diagnoses=[explicit]), package(2, [MINT], status=None, diagnoses=[unknown], inner=False),
                   package(1, balances=[observation('PRE')], status='ERROR')]
        with connect() as db:
            create_tables(db)
            for r in records:
                insert(db, r)
            cards = timeline.query_timeline(db, MINT, SQL)
        self.assertEqual([c['transaction_index'] for c in cards], [1, 2, 3])
        self.assertEqual([list(k for k,v in c['inclusion'].items() if v) for c in cards],
                         [['BALANCE_MINT'], ['MESSAGE_ACCOUNT_REFERENCE'], ['EXPLICIT_DIAGNOSIS_MINT']])
        self.assertEqual(cards[1]['diagnostics'][0]['mint_attribution'], 'UNKNOWN')
        self.assertIsNone(cards[1]['transaction_status'])
        self.assertEqual(cards[0]['transaction_status'], 'ERROR')
        self.assertIsNotNone(cards[0]['transaction_error'])
        self.assertEqual(timeline.summarize(cards)['status'], {'ERROR': 1, 'OK': 1, 'UNKNOWN': 1})
        self.assertEqual(timeline.summarize(cards)['packages_with_unknown_cpi_inventory'], 1)

    def test_explicit_structural_probe_is_context_not_a_trade_and_other_mint_is_separate(self):
        r = package(1)
        r['transaction']['pump_structural_analysis'] = {'observations': [
            {'context': {'outer_index': 0, 'inner_order': 0},
             'layout_outcome': 'LAYOUT_COMPATIBLE_ONLY', 'structural_fields': {'mint_bytes_base58': MINT}},
            {'context': {'outer_index': 1}, 'layout_outcome': 'LAYOUT_REJECTED',
             'structural_fields': {'mint_bytes_base58': 'different-mint'}}]}
        with connect() as db:
            create_tables(db); insert(db, r)
            cards = timeline.query_timeline(db, MINT, SQL)
        counts = timeline.summarize(cards)
        self.assertEqual((counts['structural_probes'], counts['profile_diagnoses'], counts['silver_facts']), (2, 0, 0))
        self.assertEqual([d['mint_attribution'] for d in cards[0]['diagnostics']], ['EXPLICIT_MATCH', 'EXPLICIT_OTHER_MINT'])

    def test_source_binding_classes_order_and_exact_integer_json(self):
        pilot = package(9, balances=[observation('PRE'), observation('POST')])
        pilot['slice_class'] = 'RESEARCH_SAMPLING'
        pilot['sample_identity'] = {'sample_class': 'RESEARCH_SAMPLING', 'seed': 'fixture'}
        context = package(10, [MINT])
        with connect() as db:
            create_tables(db)
            insert(db, context)
            insert(db, pilot, source='pilot', role='ORIGINAL_SELECTION', sides=(False,))
            # Same record hash in another source is a distinct source-bound package.
            insert(db, pilot, source='independent-binding', role='ORIGINAL_SELECTION')
            cards = timeline.query_timeline(db, MINT, SQL)
        self.assertEqual([c['transaction_index'] for c in cards], [9, 9, 10])
        self.assertEqual(len({c['package_id'] for c in cards}), 3)
        self.assertEqual([c['slice_class'] for c in cards], ['RESEARCH_SAMPLING']*2+['ENGINEERING_VALIDATION_ONLY'])
        raw = timeline.canonical(cards)
        self.assertIn(b'"18446744073709551615"', raw)
        self.assertIn(b'"-9007199254740993"', raw)
        self.assertIn(b'"decimals": "6"', raw)
        self.assertEqual(timeline.canonical(json.loads(raw)), raw)
        with self.assertRaisesRegex(ValueError, 'floating point'):
            timeline.canonical({'bad': 1.5})

    def test_hash_channel_mismatch_and_duplicate_instruction_positions_fail_closed(self):
        with self.assertRaisesRegex(ValueError, 'binding mismatch'):
            timeline.bound_record(b'{}\n', '0'*64)
        r = package(1, [MINT])
        r['transaction']['instructions'].append(copy.deepcopy(r['transaction']['instructions'][0]))
        with connect() as db:
            create_tables(db); insert(db, r)
            with self.assertRaisesRegex(ValueError, 'duplicate recorded instruction'):
                timeline.query_timeline(db, MINT, SQL)
        r = package(2, balances=[observation('PRE')])
        with connect() as db:
            create_tables(db); insert(db, r)
            db.execute("UPDATE bronze SET token_balance_observations=[]")
            # False-positive SQL signal cannot silently supply an unrelated card.
            broken = dict(SQL, selection=SQL['selection'].replace('WHERE silver OR balance OR diagnosis OR message', 'WHERE true'))
            with self.assertRaisesRegex(ValueError, 'selection channels'):
                timeline.query_timeline(db, MINT, broken)

    def test_render_is_complete_escaped_and_separates_unknowns(self):
        r = package(1, [MINT], status='ERROR')
        r['transaction']['transaction_error'] = '<script>alert(1)</script>'
        with connect() as db:
            create_tables(db); insert(db, r, sides=(True,))
            cards = timeline.query_timeline(db, MINT, SQL)
        result = timeline.exact({'mint': MINT, 'counts': timeline.summarize(cards), 'transactions': cards,
                                 'limits': timeline.LIMITS, 'bindings': {}, 'collection': {'selected_slots': []}})
        output = timeline.render(result)
        self.assertEqual(output.count("class='transaction'"), 1)
        self.assertIn('&lt;script&gt;', output)
        self.assertNotIn('<script>', output)
        self.assertNotIn('https://', output)
        self.assertIn('geen nulactiviteit', output)
        self.assertIn('UNAVAILABLE', output)
        self.assertIn('-9007199254740993', output)
        self.assertEqual(output, timeline.render(result))

    def test_no_silent_selection_limit_and_deterministic_query_order(self):
        records = [package(i, [MINT]) for i in [4, 2, 7]]
        outputs = []
        for ordered in [records, list(reversed(records))]:
            with connect() as db:
                create_tables(db)
                for r in ordered:
                    insert(db, r)
                outputs.append(timeline.canonical(timeline.query_timeline(db, MINT, SQL)))
        self.assertEqual(outputs[0], outputs[1])
        with connect() as db:
            create_tables(db)
            for r in records:
                insert(db, r)
            with patch.object(timeline, 'MAX_PACKAGES', 2), self.assertRaisesRegex(ValueError, 'no truncated success'):
                timeline.query_timeline(db, MINT, SQL)


if __name__ == '__main__':
    unittest.main()
