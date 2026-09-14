"""Read Rust-written synthetic Parquet; Python never derives token semantics."""
import json
import pathlib
import sys
import unittest

from query import connect, rows
import token_balance_report as balances

FIXTURES = pathlib.Path(sys.argv.pop(1)) if len(sys.argv) > 1 else None


class TokenBalanceQueries(unittest.TestCase):
    def db(self):
        if FIXTURES is None:
            raise ValueError('explicit Rust Parquet fixture directory required; do not skip')
        db = connect()
        for layer in ['bronze', 'silver']:
            file = FIXTURES / f'token-balances-{layer}.parquet'
            if not file.is_file():
                raise ValueError(f'missing Rust fixture: {file}')
            db.execute(f'CREATE TABLE {layer} AS SELECT * FROM read_parquet(?)', [str(file)])
        return db

    def test_actual_nested_parquet_exact_unsigned_and_signed_values(self):
        with self.db() as db:
            result = rows(db.execute('SELECT o.amount_string,o.amount_u64,o.decimals,o.decimals_present,o.decimals_field_state,o.exact_decimal_amount FROM bronze,UNNEST(token_balance_observations) t(o) ORDER BY record_ordinal,o.ordinal'))
            self.assertEqual(len(result['rows']), 4)
            self.assertTrue(all(row == ['18446744073709551615', '18446744073709551615', '0', False, 'PROTO3_DEFAULT', '18446744073709551615'] for row in result['rows']))
            self.assertEqual(result['columns'][1]['duckdb_type'], 'UBIGINT')
            result = rows(db.execute('SELECT base_decimals,units_decimals,units_event_token_amount_raw_u64,units_event_token_amount_decimal,r.transaction_delta_raw_signed,len(r.pre_observation_indexes) FROM silver,UNNEST(units_role_balances) t(r)'))
            self.assertTrue(all(row == [None, '6', '9007199254740993', '9007199254.740993', '-18446744073709551615', '2'] for row in result['rows']))

    def test_all_fixed_queries_execute_from_parquet_and_retain_duplicate_observations(self):
        with self.db() as db:
            sql, binding = balances.query_contract(db)
            result = {k: dict(rows(db.execute(v)), sql=v) for k, v in sql.items()}
            self.assertEqual(len(sql), 7)
            summary = balances.summary(result, binding)
            self.assertEqual(summary['packages_with_balances'], 2)
            self.assertEqual(summary['observations'], 4)
            self.assertEqual(summary['trades'], 2)
            self.assertEqual(summary['trade_decimals_observed'], [6])
            self.assertIn('pre/postdelta omvat de hele transactie', balances.render(result, binding))

    def test_absent_projection_empty_collection_and_observed_zero_are_different(self):
        with self.db() as db:
            db.execute("UPDATE bronze SET token_balance_observations=NULL, token_balance_observations_state='MISSING' WHERE record_ordinal=0")
            db.execute("UPDATE bronze SET token_balance_observations=[], token_balance_observations_state='VALUE' WHERE record_ordinal=1")
            sql, _ = balances.query_contract(db)
            observed = balances.objects(rows(db.execute(sql['token_balance_coverage'])))[0]
            self.assertEqual(observed['packages_with_recorded_balances'], '0')
            self.assertEqual(observed['empty_or_not_recorded_packages'], '1')
            self.assertEqual(observed['projection_unavailable_packages'], '1')
            self.assertEqual(observed['recorded_observations'], '0')
        with connect() as db:
            db.execute('CREATE TABLE bronze(slot UBIGINT)')
            db.execute('CREATE TABLE silver(slot UBIGINT)')
            sql, binding = balances.query_contract(db)
            self.assertEqual(sql, {})
            self.assertIsNone(balances.summary({}, binding)['observations'])
            self.assertIn('UNAVAILABLE', balances.render({}, binding))

    def test_conflicting_context_is_visible_without_new_trade_admission_or_invented_decimal(self):
        with self.db() as db:
            db.execute("UPDATE silver SET units_binding_status='CONFLICTING',units_decimals=NULL,units_event_token_amount_decimal=NULL,mint='<script>bad</script>'")
            sql, binding = balances.query_contract(db)
            result = {k: dict(rows(db.execute(v)), sql=v) for k, v in sql.items()}
            summary = balances.summary(result, binding)
            self.assertEqual(summary['trades'], 2)
            self.assertEqual(summary['conflicting_trade_contexts'], 2)
            self.assertEqual(summary['trades_with_bound_base_units'], 0)
            rendered = balances.render(result, binding)
            self.assertIn('CONFLICTING', rendered)
            self.assertIn('&lt;script&gt;', rendered)
            self.assertNotIn('<script>', rendered)
            self.assertIn('basis-tokenweergave <b>UNAVAILABLE</b>', rendered)

    def test_followup_horizon_is_frozen_proposal_not_selection_or_network_authority(self):
        with self.db() as db:
            _, binding = balances.query_contract(db)
        proposal = binding['future_context_proposal_not_current_selection']
        self.assertFalse(proposal['network_authorized'])
        self.assertFalse(proposal['research_ready'])
        self.assertEqual(proposal['original_selection_unchanged'], [422669516, 422669519])
        self.assertEqual(proposal['new_contiguous_context_union'], [422669519, 422669535])
        self.assertEqual(proposal['new_slots'], 16)
        self.assertEqual(proposal['horizon_after_each_observation_slots'], 16)
        self.assertIsNone(proposal['duration_seconds'])
        self.assertIsNone(proposal['new_byte_request_runtime_budget'])
        self.assertIn('POSTHOC', proposal['classification_of_future_context'])
        self.assertTrue(any('ongeacht rendement' in rule for rule in proposal['stop_rules']))


if __name__ == '__main__':
    unittest.main()
