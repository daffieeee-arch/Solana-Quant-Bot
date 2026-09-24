"""Collection inventories and bounded queries; no Python canonical writer."""
import copy
import hashlib
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

from collection_reader import (attach_collection, literal_path, load_collection,
                               plan_inventory)
from collection_report import bounded_rows, render
from collection_run import Runner, digest, process_limits
from manifest_reader import batch_inventory, sha
from query import connect

FIXTURES=pathlib.Path(sys.argv.pop()) if len(sys.argv)==2 else None


def plan_fixture():
    """Schema fixture only; no authenticated original run or canonical facts."""
    return {'schema':'OF1_BATCH_COLLECTION_PLAN_1','collection_id':'SYNTHETIC_COLLECTION',
            'research_ready':False,'workers':{'batch_decoder_sha256':'a'*64,'projector_sha256':'b'*64},
            'sources':[{'source_id':'fixture','run_root':'/fixture/original-run','run_id':'fixture-run',
                        'bindings':{'schema':'SYNTHETIC_ONLY'},'sample_identity':None}],
            'logical_selection':[{'source_id':'fixture','slot':slot,'role':'ORIGINAL_SELECTION'} for slot in range(10,15)],
            'batches':[{'batch_id':'one','source_id':'fixture','slots':[10,11,12],
                        'receipt_sequences':[4,5,6],'output_directory':'one'},
                       {'batch_id':'two','source_id':'fixture','slots':[13,14],
                        'receipt_sequences':[7,8],'output_directory':'two'}]}


def manifest_fixture(plan):
    empty=hashlib.sha256(b'OF1_ORDERED_RECORD_CHAIN_1').hexdigest()
    return {'schema':'OF1_BATCH_COLLECTION_1','plan':plan,'plan_sha256':sha(json.dumps(plan).encode()),
            'state':'INCOMPLETE','research_ready':False,
            'batches':[{'batch_id':b['batch_id'],'source_id':b['source_id'],
                        'selected_slots':b['slots'],'state':'PENDING'} for b in plan['batches']],
            'slot_outcomes':[dict(r,state='PENDING') for r in plan['logical_selection']],
            'layers':{layer:{'rows':0,'ordered_logical_sha256':empty} for layer in ['bronze','silver']},
            'completeness':{'all_selected_slots_accounted':False,'successful_decoding_separate':True}}


def publish(root,manifest,name='collection.json'):
    raw=(json.dumps(manifest,indent=2)+'\n').encode()
    (root/name).write_bytes(raw)
    (root/(name+'.sha256')).write_text(sha(raw)+'\n')
    if not (root/'plan.json').exists():
        (root/'plan.json').write_text(json.dumps(manifest['plan']))


class CollectionInventoryTests(unittest.TestCase):
    def test_findings_never_treat_a_truncated_mint_preview_as_a_complete_summary(self):
        with tempfile.TemporaryDirectory() as tmp,connect() as db:
            root=pathlib.Path(tmp);manifest=manifest_fixture(plan_fixture())
            summary=attach_collection(db,root,manifest)
            query={'columns':[],'rows':[],'displayed_rows':0,'total_result_rows':201,
                   'preview_complete':False,'sql':'synthetic bounded-preview case'}
            result={'collection':summary,'duckdb_version':'1.5.5',
                    'queries':{name:query for name in ['role_coverage','mint_admitted_sides','pilot_mint_recurrence']}}
            page=render(result,{'query_seconds':0.1})
            self.assertNotIn('Werkelijk bevraagde uitkomst',page)
            self.assertIn('0 van 201 queryrijen',page)
            self.assertIn('Begrensde detailweergave',page)

    def test_pump_denominator_does_not_count_multiple_facts_as_multiple_packages(self):
        sql=json.loads(pathlib.Path(__file__).with_name('context.sql.json').read_text())
        with connect() as db:
            db.execute('CREATE TABLE bronze(collection_role VARCHAR,collection_source_id VARCHAR,record_sha256 VARCHAR,pump_program_involvement BOOLEAN,transaction_status VARCHAR)')
            db.execute("INSERT INTO bronze VALUES ('ORIGINAL_SELECTION','p','a',true,'OK'),('ORIGINAL_SELECTION','p','b',true,'ERROR'),('ORIGINAL_SELECTION','p','c',NULL,NULL)")
            db.execute('CREATE TABLE silver(bronze_record_sha256 VARCHAR,collection_source_id VARCHAR)')
            db.execute("INSERT INTO silver VALUES ('a','p'),('a','p'),('b','different-source')")
            row=db.execute(sql['pump_package_coverage']).fetchone()
            self.assertEqual(row,('ORIGINAL_SELECTION',3,2,1,1,1,1))

    def test_posthoc_recurrence_separates_trade_balance_and_absent_channels(self):
        sql=json.loads(pathlib.Path(__file__).with_name('context.sql.json').read_text())
        with connect() as db:
            db.execute('CREATE TABLE silver(mint VARCHAR,collection_role VARCHAR,is_buy BOOLEAN,slot UBIGINT,user_address VARCHAR,units_decimals UBIGINT)')
            db.execute("INSERT INTO silver VALUES ('A','ORIGINAL_SELECTION',true,10,'trader1',6),('B','ORIGINAL_SELECTION',false,11,'trader2',NULL),('C','ORIGINAL_SELECTION',true,12,'trader3',6),('A','POSTHOC_DESCRIPTIVE_CONTEXT',false,13,'other-trader',6)")
            db.execute('CREATE TABLE bronze(slot UBIGINT,transaction_index UBIGINT,collection_role VARCHAR,token_balance_observations STRUCT(mint VARCHAR)[])')
            db.execute("INSERT INTO bronze VALUES (13,0,'POSTHOC_DESCRIPTIVE_CONTEXT',[{'mint':'B'},{'mint':'B'}]),(14,1,'POSTHOC_DESCRIPTIVE_CONTEXT',NULL)")
            rows=db.execute(sql['pilot_mint_recurrence']).fetchall()
            self.assertEqual([r[0] for r in rows],['A','B','C'])
            self.assertEqual(rows[0][1:4],(1,0,1))
            self.assertEqual(rows[1][1:6],(0,0,0,2,1))
            self.assertEqual(rows[2][1:8],(0,0,0,0,0,None,None))
            self.assertIn('not zero market activity',rows[2][-1])
            sides=db.execute(sql['mint_admitted_sides']).fetchall()
            self.assertTrue(sides[0][3])
            self.assertEqual(sides[0][6],2) # distinct users: never a fabricated round-trip
            self.assertFalse(sides[1][3])
            self.assertEqual(sides[1][8],1) # missing base units retained
            self.assertIn('No automatic',sides[0][-1])

    def test_role_denominators_keep_failed_missing_unsupported_and_unknown_pump(self):
        sql=json.loads(pathlib.Path(__file__).with_name('context.sql.json').read_text())
        with connect() as db:
            db.execute('CREATE TABLE bronze(collection_source_id VARCHAR,collection_role VARCHAR,slice_class VARCHAR,disposition VARCHAR,transaction_status VARCHAR,slot UBIGINT,pump_program_involvement BOOLEAN,status_metadata_bytes BLOB)')
            db.execute("INSERT INTO bronze VALUES ('pilot','ORIGINAL_SELECTION','RESEARCH_SAMPLING','DECODED','OK',10,true,'a'),('context','POSTHOC_DESCRIPTIVE_CONTEXT','ENGINEERING_VALIDATION_ONLY','DECODED','ERROR',13,true,'a'),('context','POSTHOC_DESCRIPTIVE_CONTEXT','ENGINEERING_VALIDATION_ONLY','MISSING',NULL,13,NULL,NULL),('context','POSTHOC_DESCRIPTIVE_CONTEXT','ENGINEERING_VALIDATION_ONLY','UNSUPPORTED',NULL,14,NULL,'a'),('context','POSTHOC_DESCRIPTIVE_CONTEXT','ENGINEERING_VALIDATION_ONLY','QUARANTINED',NULL,14,NULL,'a')")
            rows=db.execute(sql['role_coverage']).fetchall()
            self.assertEqual(sum(r[6] for r in rows),5)
            self.assertEqual(sum(r[7] for r in rows),2)
            self.assertEqual(sum(r[8] for r in rows),3)
            self.assertEqual(sum(r[9] for r in rows),1)
            self.assertEqual(sum(r[6] for r in rows if r[4]=='ERROR'),1)

    def test_context_queries_do_not_use_operational_clocks_or_derive_prices(self):
        sql=pathlib.Path(__file__).with_name('context.sql.json').read_text()
        for forbidden in ['acquired_at','processed_at','::DOUBLE','::FLOAT','SUM(sol_amount','SUM(quote_amount']:
            self.assertNotIn(forbidden,sql)
        self.assertIn('event_reported_timestamp',sql)
        self.assertIn('ORDER BY mint,slot,transaction_index,outer_index',sql)

    def test_more_than_three_slots_physical_partition_and_role_separation(self):
        plan=plan_fixture()
        self.assertEqual(len(plan_inventory(plan)[1]),5)
        changed=copy.deepcopy(plan)
        changed['batches']=[dict(batch_id=f'slot-{i}',source_id='fixture',slots=[i],
                                 receipt_sequences=[i-6],output_directory=f'slot-{i}') for i in range(10,15)]
        self.assertEqual(plan_inventory(plan)[1],plan_inventory(changed)[1])
        self.assertNotEqual(plan['batches'],changed['batches'])

    def test_overlap_gap_order_duplicate_output_and_conflicting_source_rejected(self):
        for change in ['overlap','gap','order','output','source','receipt','logical_gap']:
            with self.subTest(change=change):
                plan=plan_fixture()
                if change=='overlap': plan['batches'][1]['slots']=[12,13]
                if change=='gap': plan['batches'][1]['slots']=[14];plan['batches'][1]['receipt_sequences']=[8]
                if change=='order': plan['batches'].reverse()
                if change=='output': plan['batches'][1]['output_directory']='batches/one/nested'
                if change=='source': plan['batches'][1]['source_id']='other'
                if change=='receipt': plan['batches'][0]['receipt_sequences']=[4,4,6]
                if change=='logical_gap':
                    plan['logical_selection'].pop(3)
                    plan['batches'][1]['slots']=[14];plan['batches'][1]['receipt_sequences']=[8]
                with self.assertRaises(ValueError):plan_inventory(plan)

    def test_source_sample_cannot_be_posthoc_relabelled_or_partially_removed(self):
        plan=plan_fixture()
        plan['sources'][0]['sample_identity']={'start_slot':10,'end_slot_exclusive':15}
        plan_inventory(plan)
        plan['logical_selection'][0]['role']='POSTHOC_DESCRIPTIVE_CONTEXT'
        with self.assertRaisesRegex(ValueError,'posthoc'):plan_inventory(plan)
        plan['logical_selection']=plan['logical_selection'][1:]
        with self.assertRaisesRegex(ValueError,'full original'):plan_inventory(plan)

    def test_unbounded_or_wrong_type_sample_interval_rejected_before_range_allocation(self):
        for start,end in [(0,2**64),(10,10),(10,267),('10',15),(True,15)]:
            plan=plan_fixture();plan['sources'][0]['sample_identity']={'start_slot':start,'end_slot_exclusive':end}
            with self.assertRaisesRegex(ValueError,'bounded original sample'):plan_inventory(plan)

    def test_literal_paths_no_globs_or_substituted_parent(self):
        root=pathlib.Path('/bounded-fixture')
        self.assertEqual(literal_path(root,'batches/one/parquet/manifest.json'),root/'batches/one/parquet/manifest.json')
        for name in ['../bad','/absolute','part/*.parquet','part/[0].parquet']:
            with self.assertRaises(ValueError):literal_path(root,name)

    def test_pending_collection_visible_not_zero_activity_or_complete(self):
        manifest=manifest_fixture(plan_fixture())
        with tempfile.TemporaryDirectory() as tmp,connect() as db:
            root=pathlib.Path(tmp);publish(root,manifest)
            checked,_=load_collection(root)
            summary=attach_collection(db,root,checked)
            self.assertEqual(summary['verified_batches'],0)
            self.assertTrue(all(r['present_packages'] is None for r in summary['selected_slots']))
            result={'collection':summary,'queries':{},'duckdb_version':'1.5.5'}
            html=render(result,{'query_seconds':0.1})
            self.assertIn('INCOMPLETE',html)
            self.assertIn('Geen nulresultaat',html)
            self.assertIn('UNAVAILABLE',html)
            manifest['state']='COMPLETE';publish(root,manifest)
            with self.assertRaisesRegex(ValueError,'false complete'):load_collection(root)

    def test_missing_corrupt_duplicate_or_substituted_batch_never_accepted(self):
        for change in ['missing','corrupt','duplicate','substituted','slot']:
            with self.subTest(change=change),tempfile.TemporaryDirectory() as tmp:
                root=pathlib.Path(tmp);manifest=manifest_fixture(plan_fixture())
                batch=manifest['batches'][0]
                batch.update(state='VERIFIED',parquet_manifest_path='one/parquet/manifest.json',parquet_manifest_sha256='a'*64)
                path=root/batch['parquet_manifest_path'];path.parent.mkdir(parents=True)
                if change!='missing':path.write_bytes(b'not-authenticated-child')
                if change=='duplicate':manifest['batches'].append(copy.deepcopy(batch))
                if change=='substituted':batch['parquet_manifest_path']='unplanned/manifest.json'
                if change=='slot':batch['selected_slots']=[9,10,11]
                publish(root,manifest)
                with self.assertRaises((ValueError,FileNotFoundError)):load_collection(root)

    def test_immutable_progress_snapshot_is_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);manifest=manifest_fixture(plan_fixture())
            publish(root,manifest,'collection-progress-0001.json')
            self.assertEqual(load_collection(root/'collection-progress-0001.json')[0],manifest)

    def test_bounded_details_keep_full_denominator_exact_u64_and_null(self):
        with connect() as db:
            result=bounded_rows(db,"SELECT i,18446744073709551615::UBIGINT AS exact,NULL AS missing FROM range(1001) t(i) ORDER BY i")
            self.assertEqual(result['total_result_rows'],1001)
            self.assertEqual(result['displayed_rows'],200)
            self.assertFalse(result['preview_complete'])
            self.assertEqual(result['rows'][0],['0','18446744073709551615',None])
            self.assertEqual(result['rows'][-1][0],'199')
            with self.assertRaisesRegex(ValueError,'floating point'):bounded_rows(db,'SELECT 1.5::DOUBLE')

    def test_escaped_report_and_preserved_slot_outcomes(self):
        with connect() as db,tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);manifest=manifest_fixture(plan_fixture())
            summary=attach_collection(db,root,manifest)
            summary['collection_id']='<script>not-executable</script>'
            html=render({'collection':summary,'queries':{},'duckdb_version':'1.5.5'},{'query_seconds':0.5})
            self.assertIn('&lt;script&gt;',html);self.assertNotIn('<script>',html)
            self.assertIn('5</b>expliciet geselecteerde slots',html)


class RunnerIdentityTests(unittest.TestCase):
    def test_worker_preserves_stricter_inherited_hard_resource_caps(self):
        import resource
        with patch('collection_run.resource.getrlimit',side_effect=[(1024,1024),(2048,2048)]), patch('collection_run.resource.setrlimit') as setlimit:
            process_limits()
            self.assertEqual(setlimit.call_args_list[0].args,(resource.RLIMIT_AS,(1024,1024)))
            self.assertEqual(setlimit.call_args_list[1].args,(resource.RLIMIT_FSIZE,(2048,2048)))

    def test_resume_changes_no_source_or_plan_and_rejects_binary_or_tool_identity_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);original=root/'original';original.mkdir()
            decoder=root/'decoder';decoder.write_bytes(b'SYNTHETIC_BINARY_IDENTITY_ONLY')
            projector=root/'projector';projector.write_bytes(b'SYNTHETIC_PROJECTOR_IDENTITY_ONLY')
            verifier=root/'verifier';verifier.write_bytes(b'SYNTHETIC_VERIFIER_IDENTITY_ONLY')
            plan=plan_fixture();plan['sources'][0]['run_root']=str(original)
            plan['workers']={'batch_decoder_sha256':digest(decoder),'projector_sha256':digest(projector)}
            path=root/'plan.json';path.write_text(json.dumps(plan))
            output=root/'collection'
            first=Runner(path,output,decoder,projector,verifier)
            self.assertEqual(list(original.iterdir()),[])
            second=Runner(path,output,decoder,projector,verifier)
            self.assertEqual(first.plan_raw,second.plan_raw)
            verifier.write_bytes(b'changed verifier')
            with self.assertRaisesRegex(ValueError,'identity mismatch'):Runner(path,output,decoder,projector,verifier)
            decoder.write_bytes(b'changed decoder')
            with self.assertRaisesRegex(ValueError,'executable differs'):Runner(path,output,decoder,projector,verifier)

    def test_operation_reservation_stops_before_subprocess(self):
        runner=Runner.__new__(Runner)
        runner.campaign=False  # Original non-campaign artifact reservation fixture.
        runner.counter=0
        with tempfile.TemporaryDirectory() as tmp:
            runner.root=pathlib.Path(tmp)
            with patch('collection_run.artifact_size',return_value=4*1024**3-700*1024**2), patch('collection_run.subprocess.run') as command:
                with self.assertRaisesRegex(ValueError,'remaining artifact cap'):
                    runner.step('SYNTHETIC_CAP_TEST',[pathlib.Path('/never-invoked')])
                command.assert_not_called()


class BatchBindingTests(unittest.TestCase):
    def fixture(self):
        plan=plan_fixture()
        raw=json.dumps(plan)
        source=plan['sources'][0];batch=plan['batches'][0]
        binding={'schema':'OF1_BATCH_BINDING_1','plan_json':raw,'plan_sha256':sha(raw.encode()),
                 'batch_id':batch['batch_id'],'source_id':batch['source_id'],
                 'selected_slots':batch['slots'],'receipt_sequences':batch['receipt_sequences'],
                 'original_bindings':source['bindings'],'sample_identity':None,
                 'source_run_id':source['run_id'],'source_run_root':source['run_root'],
                 'logical_selection':plan['logical_selection'],'workers':plan['workers']}
        return {'batch_binding':binding,'sample_identity':None,'selection':{'selected_slots':batch['slots']},
                'input':{'execution':{'run_root':source['run_root'],'batch_binding':binding,
                                       'executable_sha256':plan['workers']['batch_decoder_sha256']}}}

    def test_valid_exact_physical_binding(self):
        manifest=self.fixture()
        self.assertEqual(batch_inventory(manifest),manifest['batch_binding'])

    def test_reclassification_source_receipt_and_worker_substitution(self):
        for key,value in [('plan_sha256','0'*64),('receipt_sequences',[4,6,5]),
                          ('original_bindings',{'forged':True}),('source_run_root','/different/run'),
                          ('sample_identity',{'sample_class':'RESEARCH_SAMPLING'}),
                          ('workers',{'batch_decoder_sha256':'9'*64,'projector_sha256':'b'*64})]:
            with self.subTest(key=key):
                manifest=self.fixture();manifest['batch_binding'][key]=value
                with self.assertRaises(ValueError):batch_inventory(manifest)


def actual_query_regression(root):
    """Existing Rust-written real Parquet, synthetic facts and no redecoding."""
    with connect() as db:
        for layer in ['bronze','silver']:
            path=root/f'token-balances-{layer}.parquet'
            if not path.is_file():raise ValueError('missing actual Rust query fixture')
            db.from_parquet(str(path)).create_view('_fixture',replace=True)
            db.execute(f"CREATE VIEW {layer} AS SELECT *, 'SYNTHETIC_BATCH' AS collection_batch_id, "
                       "'fixture' AS collection_source_id, 'ORIGINAL_SELECTION' AS collection_role FROM _fixture")
            # Bind each immutable path separately; view dependencies cannot share
            # a subsequently replaced temporary alias.
            db.execute(f'DROP VIEW {layer}')
            escaped=str(path).replace("'","''")
            db.execute(f"CREATE VIEW {layer} AS SELECT *, 'SYNTHETIC_BATCH' AS collection_batch_id, "
                       f"'fixture' AS collection_source_id, 'ORIGINAL_SELECTION' AS collection_role FROM read_parquet('{escaped}')")
        sql=json.loads(pathlib.Path(__file__).with_name('collection.sql.json').read_bytes())
        result={name:bounded_rows(db,text) for name,text in sql.items()}
        assert result['layer_counts']['rows']==[['bronze','2'],['silver','2']]
        assert result['trades']['total_result_rows']==2
        assert result['balance_coverage']['rows'][0][-1]=='4'
        print('Collection SQL executed against actual Rust Parquet fixtures PASS')


if __name__=='__main__':
    result=unittest.main(exit=False)
    if not result.result.wasSuccessful():raise SystemExit(1)
    if FIXTURES is not None:actual_query_regression(FIXTURES)
