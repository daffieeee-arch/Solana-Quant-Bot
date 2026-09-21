"""Denominators and missing-data semantics; synthetic counts, no domain decode."""
import pathlib
import json
import tempfile
import unittest
from coverage import (coverage, bound_json, sha, rust_slots, suitability, optional_pilot,
                      render, buy_sell_question, objects)
from query import connect, rows


def actual(slot, total, decoded=0, missing=0, unsupported=0, quarantined=0):
    return dict(slot=str(slot), present_envelopes=str(total), decoded=str(decoded), missing=str(missing),
                unsupported=str(unsupported), quarantined=str(quarantined))


def proof(slot, envelopes):
    return dict(slot=str(slot),transaction_envelopes=envelopes,raw_sha256='a'*64,stages={'car_slot':'VERIFIED'})


def extent(slot, count):
    return dict(slot=str(slot),known_indices=str(count),first_index='0' if count else None,last_index=str(count-1) if count else None)


def synthetic_silver(db, records):
    """Typed projected facts only; no transaction/event decoding or admission."""
    numeric = {
        'record_ordinal':'UBIGINT', 'slot':'UBIGINT', 'transaction_index':'UBIGINT',
        'outer_index':'UBIGINT', 'instruction_inner_order':'UBIGINT',
        'event_inner_order':'UBIGINT', 'token_amount_raw_u64':'UBIGINT',
        'sol_amount_raw_u64':'UBIGINT', 'timestamp_raw_i64':'BIGINT',
        'quote_decimals':'UTINYINT', 'base_decimals':'UTINYINT', 'is_buy':'BOOLEAN',
    }
    strings = ['transaction_status','mint','quote_mint_identity','observed_at','actionable_at',
               'execution_opportunity_at','name','ticker','launch_at','candidate_id',
               'source_evidence_sha256','raw_sha256','record_sha256','bronze_record_sha256']
    columns = {**numeric, **{key:'VARCHAR' for key in strings}}
    db.execute('CREATE TABLE silver ('+', '.join(f'{name} {kind}' for name,kind in columns.items())+')')
    if records:
        db.executemany('INSERT INTO silver VALUES ('+','.join('?' for _ in columns)+')',
                       [tuple(record.get(name) for name in columns) for record in records])


def side_summary(db, sql, errors=None):
    values = objects(rows(db.execute(sql['supported_sides'])))[0]
    return {'supported_sides':{key:int(value) for key,value in values.items()},
            'integrity_errors':errors or []}


class CoverageTests(unittest.TestCase):
    def test_nested_buy_diagnostics_are_not_admitted_facts_or_inferred_privileges(self):
        sql=json.loads(pathlib.Path(__file__).with_name('coverage.sql.json').read_bytes())
        # Synthetic Rust-result shape, not protocol bytes or a second decoder.
        diagnostic={'evaluated_profile':'nested25-unresolved','disposition':'NOT_ADMITTED',
                    'reason':'ACCOUNT_MISMATCH','instruction_bytes':25,
                    'instruction':{'track_volume':False,'amount_raw_u64':'18446744073709551615',
                                   'max_sol_cost_raw_u64':'9007199254740993'},
                    'outer_index':2,'instruction_inner_order':0,'instruction_stack_height':2,
                    'event_context':{'inner_order':4,'stack_height':3},
                    'event_reported':{'mint_address':'diagnostic-mint','mayhem_mode':True},
                    'account_address_correspondence':False,'mayhem_context':{'cpi_signer':None},
                    'proof_gaps':['unresolved account 16'], 'accounts':[
                        {'position':6,'role':'user','observed':'pda','expected':'pda',
                         'address_match':True,'message_signer':False,
                         'message_minimum_privileges_match':False,'cpi_signer':None},
                        {'position':16,'role':'bonding_curve_v2','observed':'actual','expected':'source',
                         'address_match':False,'message_signer':False,
                         'message_minimum_privileges_match':True,'cpi_signer':None,
                         'actual_role_if_mismatched':'UNAVAILABLE_SOURCE_PROOF_MISSING'}]}
        with connect() as db:
            db.execute('CREATE TABLE bronze(slot UBIGINT,transaction_index UBIGINT,record_ordinal UBIGINT,transaction_status VARCHAR,record_bytes BLOB)')
            for ordinal,status in enumerate(['OK','ERROR']):
                db.execute('INSERT INTO bronze VALUES (9,?,?,?,?)',[
                    ordinal,ordinal,status,json.dumps({'transaction':{
                        'pump_nested_buy_analysis':[diagnostic,diagnostic]}}).encode()])
            details=objects(rows(db.execute(sql['nested_buy_details'])))
            self.assertEqual(len(details),4)  # preserve duplicate diagnoses and failed transactions
            self.assertEqual([r['transaction_status'] for r in details],['OK','OK','ERROR','ERROR'])
            for row in details:
                self.assertEqual(row['disposition'],'NOT_ADMITTED')
                self.assertEqual(row['track_volume'],'false')
                self.assertEqual(row['reported_mint'],'diagnostic-mint')
                self.assertEqual(row['amount_raw_u64'],'18446744073709551615')
                self.assertEqual(row['max_sol_cost_raw_u64'],'9007199254740993')
                self.assertEqual(row['actual_cpi_signer'],'null')
            gaps=objects(rows(db.execute(sql['nested_buy_account_gaps'])))
            self.assertEqual(len(gaps),8)
            self.assertEqual([r['account_position'] for r in gaps],['6','16']*4)
            self.assertTrue(all(r['actual_cpi_signer']=='null' for r in gaps))
            synthetic_silver(db,[])
            summary=side_summary(db,sql)
            question=buy_sell_question(summary,[])
            self.assertEqual(summary['supported_sides']['buy_facts'],0)
            self.assertEqual(question['result_kind'],'INSUFFICIENT_SUITABLE_DATA')
            self.assertIn('remaining account 16',question['next_step'])
            self.assertIn('separate 24-byte',question['next_step'])

    def test_nested_buy_gaps_keep_null_absent_checks_and_unknown_status(self):
        sql=json.loads(pathlib.Path(__file__).with_name('coverage.sql.json').read_bytes())
        diagnostic={'accounts':[
            {'position':0,'address_match':None,'message_minimum_privileges_match':None},
            {'position':1},
            {'position':2,'address_match':True,'message_minimum_privileges_match':None},
            {'position':3,'address_match':None,'message_minimum_privileges_match':True},
            {'position':4,'address_match':True,'message_minimum_privileges_match':True}]}
        with connect() as db:
            db.execute('CREATE TABLE bronze(slot UBIGINT,transaction_index UBIGINT,record_ordinal UBIGINT,transaction_status VARCHAR,record_bytes BLOB)')
            records=[{'pump_nested_buy_analysis':[diagnostic]},
                     {'pump_nested_buy_analysis':[]}, {}, {'pump_nested_buy_analysis':None}]
            for n,record in enumerate(records):
                db.execute('INSERT INTO bronze VALUES (9,?,?,NULL,?)',[
                    n,n,json.dumps({'transaction':record}).encode()])
            details=objects(rows(db.execute(sql['nested_buy_details'])))
            self.assertEqual(len(details),1)
            self.assertIsNone(details[0]['transaction_status'])
            self.assertIsNone(details[0]['reported_mint'])
            gaps=objects(rows(db.execute(sql['nested_buy_account_gaps'])))
            self.assertEqual([r['account_position'] for r in gaps],['0','1','2','3'])
            self.assertEqual([r['address_match'] for r in gaps],['null',None,'true','null'])

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
        result['queries']={'nested_buy_details':{'columns':[{'name':k} for k in
            ['slot','transaction_index','disposition','reason','reported_mint','actual_cpi_signer']],
            'rows':[['9','1','NOT_ADMITTED','ACCOUNT_MISMATCH','<untrusted-mint>',None]],'sql':'synthetic shape'}}
        rendered=render(result)
        self.assertIn('Afzonderlijke nested-buydiagnoses — geen Silver-toelating',rendered)
        self.assertIn('Werkelijke CPI-signer: UNAVAILABLE',rendered)
        self.assertIn('&lt;untrusted-mint&gt;',rendered)
        self.assertNotIn('<untrusted-mint>',rendered)

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

    def test_sell_profiles_query_preserves_rejected_and_failed_packages_and_null_cpi(self):
        sql=json.loads(pathlib.Path(__file__).with_name('coverage.sql.json').read_bytes())
        # Synthetic Rust-diagnosis shapes only. SQL does not infer account roles,
        # PDA signers, mayhem semantics, successful events or Silver admission.
        cases=[
            ('OK',{'evaluated_profile':'tokenkeg16','selected_candidate':'admitted16',
                   'disposition':'MATCHED_RECORDED_EVENT_FACTS','reason':None,
                   'profile_evidence_status':'OBSERVATION_PREDICATES_MATCHED',
                   'proof_gaps':[], 'account_count':16,'event_reported':{'mayhem_mode':False},
                   'accounts':[]}),
            ('OK',{'evaluated_profile':'mayhem16','selected_candidate':None,
                   'disposition':'NOT_ADMITTED','reason':'ACCOUNT_MISMATCH',
                   'proof_gaps':['remaining account unresolved','CPI privileges unavailable'],
                   'account_count':16,'event_reported':{'mayhem_mode':True},'accounts':[
                       {'position':6,'role':'user','address_match':True,'message_signer':False,
                        'message_minimum_privileges_match':False,'cpi_signer':None},
                       {'position':14,'role':'bonding_curve_v2','address_match':False,
                        'observed':'actual','expected':'source-derived','message_signer':False,
                        'message_minimum_privileges_match':True,'cpi_signer':None}]}),
            ('ERROR',{'disposition':'NOT_ADMITTED','reason':'MISSING_EVENT','accounts':[]}),
        ]
        with connect() as db:
            db.execute('CREATE TABLE bronze(slot UBIGINT,transaction_index UBIGINT,record_ordinal UBIGINT,transaction_status VARCHAR,record_bytes BLOB)')
            db.executemany('INSERT INTO bronze VALUES (99,?,?,?,?)',[(i,i,status,json.dumps({'transaction':{'pump_sell_analysis':[d]}}).encode()) for i,(status,d) in enumerate(cases)])
            details=db.execute(sql['sell_profile_details']).fetchall()
            self.assertEqual(len(details),3)
            self.assertEqual(details[0][2:7],('OK','tokenkeg16','admitted16','MATCHED_RECORDED_EVENT_FACTS',None))
            self.assertEqual(details[1][2:7],('OK','mayhem16',None,'NOT_ADMITTED','ACCOUNT_MISMATCH'))
            self.assertEqual(details[2][2:7],('ERROR',None,None,'NOT_ADMITTED','MISSING_EVENT'))
            self.assertEqual(details[0][10],'false')
            self.assertEqual(details[1][10],'true')
            self.assertIsNone(details[2][10])
            gaps=db.execute(sql['sell_account_evidence_gaps']).fetchall()
            names=[column[0] for column in db.description]
            self.assertIn('expected_account_role',names)
            self.assertNotIn('account_role',names)
            self.assertEqual(len(gaps),2)
            self.assertEqual([row[2] for row in gaps],['6','14'])
            self.assertTrue(all(row[-1] in [None,'null'] for row in gaps))
            self.assertEqual(details,db.execute(sql['sell_profile_details']).fetchall())

    def test_buy_version_queries_preserve_unknown_argument_event_false_and_source_conflict(self):
        sql=json.loads(pathlib.Path(__file__).with_name('coverage.sql.json').read_bytes())
        # Explicitly synthetic Rust diagnostics: the missing instruction argument
        # cannot be filled from the independently reported false event value.
        old={'identity':{'commit':'a'*40,'path':'idl/pump.json','sha256':'b'*64},
             'evidence_status':'PINNED_STRUCTURAL_SOURCE','instruction_bytes':24,
             'args_layout_match':True,'account_count':12,
             'full_observation_profile_match':False,'reason':'MODERN_ACCOUNTS_NOT_PROVEN'}
        modern={'identity':{'commit':'c'*40,'path':'idl/pump.json','sha256':'d'*64},
                'evidence_status':'PINNED_STRUCTURAL_SOURCE','instruction_bytes':25,
                'args_layout_match':False,'account_count':16,
                'full_observation_profile_match':False,'reason':'MISSING_TRACK_VOLUME_ARGUMENT'}
        diagnosis={'evaluated_profile':'separate-source-comparison','disposition':'NOT_ADMITTED',
                   'reason':'SOURCE_PROFILE_CONFLICT','instruction':{'bytes':24,
                       'discriminator_hex':'0102030405060708','amount_raw_u64':'18446744073709551615',
                       'max_sol_cost_raw_u64':'9007199254740993','track_volume':None,
                       'track_volume_evidence':'UNAVAILABLE_NOT_IN_INSTRUCTION','sha256':'e'*64},
                   'event_reported':{'track_volume':False},'token_program':'synthetic-token-program',
                   'account_count':16,'account_address_correspondence':True,'outer_index':2,
                   'event_context':{'inner_order':8,'stack_height':2,'event_sha256':'f'*64},
                   'proof_gaps':['No source-proven combined layout'],'source_evidence_sha256':'0'*64,
                   'source_comparisons':[old,modern]}
        absent=json.loads(json.dumps(diagnosis))
        del absent['instruction']['track_volume']
        raw=json.dumps({'transaction':{'pump_buy_variant_analysis':[diagnosis,diagnosis,absent]}}).encode()
        with connect() as db:
            db.execute('CREATE TABLE bronze(slot UBIGINT,transaction_index UBIGINT,record_ordinal UBIGINT,transaction_status VARCHAR,record_bytes BLOB)')
            db.execute('INSERT INTO bronze VALUES (9,320,0,\'OK\',?)',[raw])
            details=objects(rows(db.execute(sql['buy_version_details'])))
            self.assertEqual(len(details),3)  # Deliberate duplicates remain visible.
            self.assertEqual(details[0],details[1])
            self.assertEqual(details[0]['instruction_track_volume'],'null')
            self.assertIsNone(details[2]['instruction_track_volume'])
            self.assertTrue(all(d['event_track_volume']=='false' for d in details))
            self.assertTrue(all(d['disposition']=='NOT_ADMITTED' for d in details))
            self.assertEqual(details[0]['amount_raw_u64'],'18446744073709551615')
            self.assertEqual(details[0]['max_sol_cost_raw_u64'],'9007199254740993')
            self.assertEqual(details[0]['event_inner_order'],'8')
            versions=objects(rows(db.execute(sql['buy_source_versions'])))
            self.assertEqual(len(versions),6)
            self.assertEqual([v['instruction_bytes'] for v in versions],['24','25']*3)
            self.assertEqual([v['arguments_layout_match'] for v in versions],['true','false']*3)
            self.assertEqual([v['source_account_count'] for v in versions],['12','16']*3)
            self.assertEqual(json.loads(versions[0]['source_identity']),old['identity'])
            self.assertEqual(json.loads(versions[1]['source_identity']),modern['identity'])
            self.assertTrue(all(v['full_observation_profile_match']=='false' for v in versions))
            self.assertEqual(details,objects(rows(db.execute(sql['buy_version_details']))))
            self.assertEqual(versions,objects(rows(db.execute(sql['buy_source_versions']))))

    def test_ordered_buy_sell_query_preserves_integer_extremes_duplicates_and_unknowns(self):
        sql=json.loads(pathlib.Path(__file__).with_name('coverage.sql.json').read_bytes())
        common={'slot':7,'transaction_index':9,'outer_index':2,'event_inner_order':5,
                'transaction_status':'OK','mint':'same-mint','candidate_id':'synthetic-profile',
                'source_evidence_sha256':'a'*64,'raw_sha256':'b'*64,
                'record_sha256':'c'*64,'bronze_record_sha256':'d'*64}
        buy={**common,'record_ordinal':1,'is_buy':True,'instruction_inner_order':None,
             'token_amount_raw_u64':2**64-1,'sol_amount_raw_u64':2**53+1,
             'timestamp_raw_i64':-(2**63)}
        sell={**common,'record_ordinal':0,'is_buy':False,'instruction_inner_order':3,
              'event_inner_order':8,'token_amount_raw_u64':2**53+1,
              'sol_amount_raw_u64':0,'timestamp_raw_i64':2**63-1}
        unknown={'record_ordinal':3,'slot':8,'transaction_index':0,'is_buy':None}
        with connect() as db:
            synthetic_silver(db,[sell,buy,{**buy,'record_ordinal':2},unknown])
            result=rows(db.execute(sql['buy_sell_ordered_facts']))
            records=objects(result)
            self.assertEqual([r['recorded_side'] for r in records],['BUY','BUY','SELL','UNAVAILABLE'])
            self.assertEqual(records[0],records[1])
            self.assertEqual(records[0]['token_amount_raw_u64'],'18446744073709551615')
            self.assertEqual(records[0]['sol_amount_raw_u64'],'9007199254740993')
            self.assertEqual(records[0]['timestamp_raw_i64'],'-9223372036854775808')
            self.assertEqual(records[2]['token_amount_raw_u64'],'9007199254740993')
            self.assertEqual(records[2]['sol_amount_raw_u64'],'0')
            self.assertEqual(records[2]['timestamp_raw_i64'],'9223372036854775807')
            self.assertIsNone(records[3]['token_amount_raw_u64'])
            types={c['name']:c['duckdb_type'] for c in result['columns']}
            self.assertEqual(types['token_amount_raw_u64'],'UBIGINT')
            self.assertEqual(types['timestamp_raw_i64'],'BIGINT')
            for record in records:
                for key in ['quote_mint_identity','quote_decimals','base_decimals','name','ticker',
                            'launch_at','observed_at','actionable_at','execution_opportunity_at']:
                    self.assertIsNone(record[key])
            self.assertEqual(result,rows(db.execute(sql['buy_sell_ordered_facts'])))

    def test_side_inventory_counts_unknown_and_failed_without_silent_filtering(self):
        sql=json.loads(pathlib.Path(__file__).with_name('coverage.sql.json').read_bytes())
        known={'slot':3,'transaction_index':0,'outer_index':1,'event_inner_order':2,
               'token_amount_raw_u64':1,'timestamp_raw_i64':7,'transaction_status':'OK','mint':'M'}
        cases=[{**known,'is_buy':True},{**known,'is_buy':False},
               {**known,'is_buy':None},{**known,'is_buy':False,'mint':None},
               {**known,'is_buy':False,'transaction_status':'ERROR'},
               {**known,'is_buy':True,'token_amount_raw_u64':None,'event_inner_order':None,
                'timestamp_raw_i64':None}]
        with connect() as db:
            synthetic_silver(db,cases)
            summary=side_summary(db,sql)
            self.assertEqual(summary['supported_sides'],{
                'facts':6,'buy_facts':2,'sell_facts':3,'unknown_side_facts':1,
                'failed_transaction_facts':1,'unknown_mint_facts':1,'unknown_raw_token_facts':1,
                'incomplete_order_facts':1,'unavailable_event_timestamp_facts':1})
            mints=objects(rows(db.execute(sql['mint_side_inventory'])))
            self.assertIsNone(mints[0]['mint'])
            self.assertEqual([r['facts'] for r in mints],['1','5'])
            self.assertEqual(mints[1]['unknown_side_facts'],'1')
            self.assertEqual(mints[1]['unknown_raw_token_facts'],'1')
            self.assertEqual(mints[1]['incomplete_order_facts'],'1')
            question=buy_sell_question(summary,mints)
            self.assertEqual(question['result_kind'],'INSUFFICIENT_SUITABLE_DATA')
            self.assertTrue(any('unknown_side_facts: 1' in gap for gap in question['missing']))
            self.assertFalse(question['research_ready'])

    def test_same_mint_raw_question_needs_order_and_quantities_not_name_ticker_or_decimals(self):
        sql=json.loads(pathlib.Path(__file__).with_name('coverage.sql.json').read_bytes())
        common={'slot':3,'outer_index':0,'event_inner_order':1,'transaction_status':'OK',
                'mint':'same-mint','token_amount_raw_u64':2**64-1}
        with connect() as db:
            synthetic_silver(db,[{**common,'transaction_index':1,'is_buy':True},
                                {**common,'transaction_index':2,'is_buy':False}])
            summary=side_summary(db,sql)
            question=buy_sell_question(summary,objects(rows(db.execute(sql['mint_side_inventory']))))
            self.assertEqual(question['result_kind'],'DESCRIPTIVE_RECORDED_FACTS_ONLY')
            self.assertEqual(question['present']['mints_with_both_admitted_sides'],['same-mint'])
            self.assertEqual(question['missing'],[])
            self.assertEqual(summary['supported_sides']['unavailable_event_timestamp_facts'],2)
            self.assertIn('without decimals',question['units_policy'])
            self.assertIn('name',question['optional_metadata'])
            self.assertIn('ticker',question['optional_metadata'])
            self.assertIn('executable price',question['not_inferred'])
            self.assertFalse(question['research_ready'])
            self.assertEqual(question,buy_sell_question(summary,objects(rows(db.execute(sql['mint_side_inventory'])))))

    def test_missing_buy_is_insufficient_data_not_edge_falsification_and_no_unavailable_is_zero(self):
        sql=json.loads(pathlib.Path(__file__).with_name('coverage.sql.json').read_bytes())
        with connect() as db:
            synthetic_silver(db,[{'slot':3,'transaction_index':2,'outer_index':0,'event_inner_order':1,
                                 'is_buy':False,'transaction_status':'OK','mint':'M',
                                 'token_amount_raw_u64':5}])
            summary=side_summary(db,sql)
            question=buy_sell_question(summary,objects(rows(db.execute(sql['mint_side_inventory']))))
            self.assertEqual(question['result_kind'],'INSUFFICIENT_SUITABLE_DATA')
            self.assertIn('No source-admitted buy fact',question['missing'][0])
            self.assertIn('not Silver',question['missing'][0])
            self.assertTrue(any('does not prove no on-chain' in x for x in question['missing']))
            self.assertFalse(question['research_ready'])
            missing=buy_sell_question({'integrity_errors':[]},[])
            self.assertIsNone(missing['present']['supported_sides'])
            self.assertIn('inventory is unavailable',missing['missing'][0])
            self.assertEqual(missing['result_kind'],'INSUFFICIENT_SUITABLE_DATA')

    def test_integrity_failure_overrides_apparent_buy_sell_completeness(self):
        sql=json.loads(pathlib.Path(__file__).with_name('coverage.sql.json').read_bytes())
        common={'slot':3,'outer_index':0,'event_inner_order':1,'transaction_status':'OK',
                'mint':'M','token_amount_raw_u64':5}
        with connect() as db:
            synthetic_silver(db,[{**common,'transaction_index':1,'is_buy':True},
                                {**common,'transaction_index':2,'is_buy':False}])
            inventory=objects(rows(db.execute(sql['mint_side_inventory'])))
            for error in ['SILVER_PARENT_MISMATCH','PARQUET_RUST_SOURCE_BINDING_MISMATCH',
                          'SILVER_SIDE_INVENTORY_MISMATCH','FAILED_TRANSACTION_HAS_ADMITTED_SILVER_FACT']:
                question=buy_sell_question(side_summary(db,sql,[error]),inventory)
                self.assertEqual(question['result_kind'],'ENGINEERING_FAILURE')
                self.assertEqual(question['missing'],[error])
                self.assertFalse(question['research_ready'])


if __name__=='__main__': unittest.main()
