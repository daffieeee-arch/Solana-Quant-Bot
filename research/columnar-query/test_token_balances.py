"""Read Rust-written synthetic Parquet; Python never derives token semantics."""
import json
import pathlib
import sys
import unittest
import tempfile

from query import connect, rows
import token_balance_report as balances

FIXTURES = pathlib.Path(sys.argv.pop(1)) if len(sys.argv) > 1 else None
OLD_REJECTED_BUY = "SELECT b.slot, b.transaction_index, b.disposition AS bronze_outcome, b.transaction_status, json_extract_string(d.value, '$.admission') AS instruction_admission, json_extract_string(d.value, '$.full_instruction_error.reason') AS reason, json_extract_string(d.value, '$.unexplained_suffix.hex') AS retained_suffix, json_extract_string(d.value, '$.unexplained_suffix.semantics') AS suffix_semantics, b.raw_sha256, b.record_sha256 FROM bronze b, json_each(CAST(decode(b.record_bytes) AS JSON), '$.transaction.pump_structural_analysis.buy_source_diagnostics') d ORDER BY b.record_ordinal, CAST(d.key AS BIGINT)"


def rejected_buy_sql():
    return json.loads(pathlib.Path(__file__).with_name('queries.sql.json').read_bytes())['rejected_buy']


def query_records(db, padding, count, offset=0):
    """Synthetic SQL-shape fixture, not a canonical/decoded Bronze dataset."""
    statement="""SELECT i::UBIGINT AS slot,i::UBIGINT AS transaction_index,
        i::UBIGINT AS record_ordinal,'DECODED' AS disposition,
        CASE WHEN i%2=0 THEN 'OK' ELSE 'ERROR' END AS transaction_status,
        'fixture-raw' AS raw_sha256,'fixture-record-'||i AS record_sha256,
        CAST(json_object('unrelated_metadata',repeat('x',?), 'identity',i,
        'transaction',json_object('pump_structural_analysis',json_object('buy_source_diagnostics',
        CASE i%4 WHEN 0 THEN NULL WHEN 1 THEN '[]'::JSON ELSE
        '[{"admission":"NOT_ADMITTED","full_instruction_error":{"reason":"SOURCE_GAP"}},{"admission":"NOT_ADMITTED","full_instruction_error":{"reason":"SOURCE_GAP"}}]'::JSON END))) AS BLOB) AS record_bytes
        FROM range(?,?) t(i)"""
    # Fixture creation itself is bounded; do not bulk-build every padded JSON.
    for start in range(offset,offset+count,128):
        prefix='CREATE TABLE bronze AS ' if start==offset else 'INSERT INTO bronze '
        db.execute(prefix+statement,[padding,start,min(start+128,offset+count)])


class TokenBalanceQueries(unittest.TestCase):
    def test_rejected_buy_narrow_projection_preserves_nulls_duplicates_failures_and_order(self):
        with connect() as db:
            query_records(db, 16, 12)
            expected=rows(db.execute(OLD_REJECTED_BUY))
            actual=rows(db.execute(rejected_buy_sql()))
            self.assertEqual(actual,expected)
            self.assertTrue(any(row[3]=='ERROR' for row in actual['rows']))
            self.assertTrue(any(row[4] is None for row in actual['rows']))
            self.assertEqual(len(actual['rows']),15)

    def test_rejected_buy_large_unrelated_metadata_fits_unchanged_256mb_cap(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-query-parent-') as temporary:
            # 64 MiB of parent-only payload exposes the former lateral retention.
            # This tight query budget is unchanged; original parent bytes remain
            # in the dataset, but only the diagnosis array enters json_each.
            files=[]
            for start in range(0,1024,128):
                file=pathlib.Path(temporary)/f'synthetic-query-shape-{start}.parquet'
                with connect() as writer:
                    query_records(writer, 65536, 128, start)
                    writer.execute("COPY bronze TO ? (FORMAT PARQUET, COMPRESSION UNCOMPRESSED)",[str(file)])
                files.append(str(file))
            # Like the real reader, query files rather than retaining a 64-MiB
            # fixture table in the same in-memory database. This is explicitly a
            # synthetic SQL-shape fixture, not the canonical Rust dataset writer.
            with connect() as db:
                db.from_parquet(files).create_view('bronze')
                actual=rows(db.execute(rejected_buy_sql()))
                self.assertEqual(len(actual['rows']),1280)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM bronze').fetchone(),(1024,))
                self.assertEqual(db.execute("SELECT current_setting('memory_limit')").fetchone(),('244.1 MiB',))

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
