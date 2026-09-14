"""Denominators and missing-data semantics; synthetic counts, no domain decode."""
import pathlib
import json
import tempfile
import unittest
from coverage import coverage, bound_json, sha, rust_slots, suitability, optional_pilot, render
from query import connect


def actual(slot, total, decoded=0, missing=0, unsupported=0, quarantined=0):
    return dict(slot=str(slot), present_envelopes=str(total), decoded=str(decoded), missing=str(missing),
                unsupported=str(unsupported), quarantined=str(quarantined))


def proof(slot, envelopes):
    return dict(slot=str(slot),transaction_envelopes=envelopes,raw_sha256='a'*64,stages={'car_slot':'VERIFIED'})


def extent(slot, count):
    return dict(slot=str(slot),known_indices=str(count),first_index='0' if count else None,last_index=str(count-1) if count else None)


class CoverageTests(unittest.TestCase):
    def test_verified_empty_slot_does_not_disappear(self):
        rows=coverage([10,11],[proof(10,0),proof(11,1)],[actual(11,1,decoded=1)],[extent(11,1)])
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['inventory_state'],'VERIFIED_EMPTY')

    def test_missing_whole_slot_has_unknown_envelope_denominator(self):
        row=coverage([10],[],[])[0]
        self.assertIsNone(row['expected_envelopes'])
        self.assertEqual(row['inventory_state'],'INCOMPLETE')
        self.assertIsNone(row['missing_rows'])

    def test_missing_records_not_confused_with_missing_domain_fields(self):
        row=coverage([10],[proof(10,3)],[actual(10,2,decoded=1,missing=1)])[0]
        self.assertEqual(row['missing_rows'],1)
        self.assertFalse(row['reconciled'])
        self.assertEqual(row['outcomes']['missing'],'1')

    def test_all_outcomes_stay_in_denominator(self):
        row=coverage([10],[proof(10,4)],[actual(10,4,decoded=1,missing=1,unsupported=1,quarantined=1)],[extent(10,4)])[0]
        self.assertTrue(row['reconciled'])
        self.assertEqual(row['present_envelopes'],4)

    def test_unexplained_outcome_fails(self):
        with self.assertRaisesRegex(ValueError,'unexplained'):
            coverage([10],[proof(10,2)],[actual(10,2,decoded=1)])

    def test_unselected_slot_rejected(self):
        with self.assertRaisesRegex(ValueError,'unselected'):
            coverage([10],[],[actual(11,1,decoded=1)])

    def test_duplicate_inventory_rejected(self):
        with self.assertRaises(ValueError): coverage([10,10],[],[])
        with self.assertRaises(ValueError): coverage([10],[proof(10,1),proof(10,1)],[])
        with self.assertRaises(ValueError): coverage([10],[],[actual(10,1,decoded=1)]*2)

    def test_extra_rows_not_misreported_complete(self):
        row=coverage([10],[proof(10,1)],[actual(10,2,decoded=2)])[0]
        self.assertEqual(row['unaccounted_extra_rows'],1)
        self.assertFalse(row['reconciled'])

    def test_original_rust_hash_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=pathlib.Path(tmp)/'quality.json'
            raw=b'{"original":true}'
            path.write_bytes(raw)
            self.assertTrue(bound_json(path,sha(raw))['original'])
            path.write_bytes(b'{"original":false}')
            with self.assertRaisesRegex(ValueError,'hash mismatch'): bound_json(path,sha(raw))

    def test_inventory_bounds(self):
        with self.assertRaises(ValueError): coverage(list(range(129)),[],[])

    def test_rust_single_and_multi_slot_shapes(self):
        single=proof(10,0)
        self.assertEqual(rust_slots(single),[single])
        self.assertEqual(rust_slots({'slots':[single]}),[single])
        with self.assertRaises(ValueError): rust_slots({})

    def test_matching_counts_cannot_hide_integrity_failure(self):
        for reason in ['DUPLICATE_TRANSACTION_IDENTITY','PARQUET_RUST_SOURCE_BINDING_MISMATCH']:
            summary={'slots':[{'reconciled':True}], 'silver':{'facts':4}, 'buy_diagnostics':1, 'integrity_errors':[reason]}
            matrix=suitability(summary)
            self.assertEqual(matrix[0]['result_kind'],'ENGINEERING_FAILURE')
            self.assertIn(reason,matrix[0]['missing'])

    def test_matching_count_without_expected_indices_is_incomplete(self):
        shifted=dict(extent(10,2),first_index='1',last_index='2')
        row=coverage([10],[proof(10,2)],[actual(10,2,decoded=2)],[shifted])[0]
        self.assertFalse(row['reconciled'])
        self.assertFalse(row['transaction_index_extent_exact'])
        unknown=dict(extent(10,2),known_indices='1')
        self.assertFalse(coverage([10],[proof(10,2)],[actual(10,2,decoded=2)],[unknown])[0]['reconciled'])

    def test_no_proposal_does_not_invent_plan_or_authority(self):
        self.assertEqual(optional_pilot(pathlib.Path('-')), (None, None))
        with tempfile.TemporaryDirectory() as tmp:
            path=pathlib.Path(tmp)/'proposal.json'
            raw=b'{"schema":"OF1_OFFLINE_PILOT_SELECTION_1","network_authorized":false}'
            path.write_bytes(raw)
            self.assertEqual(optional_pilot(path), (json.loads(raw), sha(raw)))
            path.write_text('{"schema":"OF1_OFFLINE_PILOT_SELECTION_1","network_authorized":true}')
            with self.assertRaisesRegex(ValueError,'not an offline pilot'):
                optional_pilot(path)

    def test_sample_report_without_proposal_retains_actual_identity_and_unknowns(self):
        summary={'slots':[], 'silver':{'facts':0,'parent_packages':0}, 'buy_diagnostics':0,
                 'integrity_errors':[], 'present_envelopes':0,'expected_envelopes':0,
                 'pump_positive':0,'status_ok':0,'status_error':0,'status_unknown':0,
                 'sample_class':'RESEARCH_SAMPLING'}
        result={'summary':summary,'suitability_matrix':suitability(summary),'queries':{},
                'evidence':{'slice_class':'RESEARCH_SAMPLING'},'inventory':{},
                'selection':{'status':'ACCOUNTED','selected_slots':[422669516,422669517,422669518]},
                'sample_identity':{'seed':'fixed-source-bound-seed'},'bindings':{'pilot_sha256':None},'pilot':None}
        rendered=render(result)
        self.assertIn('RESEARCH_SAMPLING',rendered)
        self.assertIn('fixed-source-bound-seed',rendered)
        self.assertIn('Brongebonden selectie — geen nieuw acquisitievoorstel',rendered)
        self.assertIn('Ontbrekende CPI-metadata is onbekend, niet nul',rendered)
        self.assertNotIn('De huidige decoder/writer blijft engineering-only',rendered)
        self.assertNotIn('Transactieaantallen, Pump-aanwezigheid en UTC-context zijn onbekend',rendered)
        self.assertNotIn('behouden payloadcap:',rendered)
        self.assertIn('INSUFFICIENT_SUITABLE_DATA',rendered)
        self.assertEqual(rendered,render(result))

    def test_program_queries_count_rust_references_not_execution_or_unknown_as_zero(self):
        sql=json.loads(pathlib.Path(__file__).with_name('coverage.sql.json').read_bytes())
        # Synthetic projected Rust facts. No instruction bytes or alternative decoder.
        values=[
            ('DECODED',True,{'status':'ERROR','program_ids':['P','Q'],
                            'instructions':[{'program_id':'P'},{'program_id':'P'}],
                            'inner_instructions':[{'program_id':'P'},{'program_id':'Q'}]}),
            ('DECODED',None,{'status':'OK','program_ids':['Q'],
                            'instructions':[{'program_id':'Q'}],'inner_instructions':None}),
            ('DECODED',False,{'status':'OK','program_ids':['R'],
                             'instructions':[{'program_id':'R'}],'inner_instructions':[]}),
            ('MISSING',None,None),
        ]
        with connect() as db:
            db.execute('CREATE TABLE bronze(disposition VARCHAR,pump_program_involvement BOOLEAN,record_bytes BLOB)')
            db.executemany('INSERT INTO bronze VALUES (?,?,?)',[(d,p,json.dumps({'transaction':t}).encode()) for d,p,t in values])
            self.assertEqual(db.execute(sql['program_frequencies']).fetchall(),[
                ('P',1,2,1),('Q',2,1,1),('R',1,1,0)])
            self.assertEqual(db.execute(sql['program_coverage']).fetchall(),[(4,3,1,2,1,2)])
            db.execute('DELETE FROM bronze')
            self.assertEqual(db.execute(sql['program_frequencies']).fetchall(),[])
            self.assertEqual(db.execute(sql['program_coverage']).fetchall(),[(0,0,0,0,0,0)])

    def test_projected_arrays_keep_duplicates_nulls_and_context_without_wide_rows(self):
        sql=json.loads(pathlib.Path(__file__).with_name('coverage.sql.json').read_bytes())
        # A wide synthetic retained record is not a new wire fixture. Only the
        # existing Rust facts are queried; irrelevant original bytes stay stored.
        transaction={'program_ids':['P','P'], 'instructions':[{'program_id':'P'},{}],
                     'inner_instructions':[{'program_id':'P'},None],
                     'pump_structural_analysis':{'observations':[
                         {'context':{'kind':'INNER'},'kind':'SELL','layout_outcome':'REJECTED'},
                         {'context':{'kind':'INNER'},'kind':'SELL','layout_outcome':'REJECTED'}]},
                     'pump_sell_analysis':[{'disposition':'REJECTED','reason':'X'},
                                           {'disposition':'REJECTED','reason':'Y'}]}
        raw=json.dumps({'original_bytes':'ff'*16384,'transaction':transaction}).encode()
        with connect() as db:
            self.assertEqual(db.execute("SELECT current_setting('temp_directory')").fetchone(),('',))
            db.execute('CREATE TABLE bronze(slot UBIGINT, transaction_index UBIGINT,record_ordinal UBIGINT, record_bytes BLOB)')
            db.executemany('INSERT INTO bronze VALUES (7,?,?,?)',[(i,i,raw) for i in range(256)])
            self.assertEqual(db.execute(sql['program_frequencies']).fetchall(),[
                ('P',512,256,256),(None,0,256,0)])
            self.assertEqual(db.execute(sql['structural_probes']).fetchall(),[
                ('INNER','SELL','REJECTED',None,512)])
            admissions=db.execute(sql['sell_admissions']).fetchall()
            self.assertEqual(len(admissions),512)
            self.assertEqual(admissions,[(7,i,'REJECTED',reason,None) for i in range(256) for reason in ['X','Y']])

    def test_suitability_does_not_copy_engineering_buy_failure_into_new_sample(self):
        summary={'slots':[{'reconciled':True}], 'silver':{'facts':2},
                 'buy_diagnostics':1, 'integrity_errors':[]}
        entry=suitability(summary)[2]
        self.assertEqual(entry['result_kind'],'INSUFFICIENT_SUITABLE_DATA')
        self.assertIn('retained Rust diagnostics',entry['missing'][0])
        self.assertNotIn('extra buy byte',json.dumps(entry))


if __name__=='__main__': unittest.main()
