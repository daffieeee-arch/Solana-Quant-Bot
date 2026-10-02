#!/usr/bin/env python3
"""Synthetic fixed-campaign acceptance through real native CLI workers, offline.

No authentic evaluation data. Fault injection affects only a separately bound
fixture driver copy; no environment override or fault hook in production code.
"""
import copy
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

from collection_run import Runner, digest, process_limits
from manifest_reader import load_manifest


def run(root, generator, decoder, projector, verifier, phase, acquisition_cli):
    assert phase in range(4)
    if phase==0:root.mkdir()
    driver_root=root/'driver'
    if phase==0:driver_root.mkdir()
    for name in ['collection_reader.py','collection_run.py','evaluation_run.py','manifest_reader.py']:
        if phase==0:shutil.copyfile(pathlib.Path(__file__).with_name(name),driver_root/name)
    driver=driver_root/'evaluation_run.py'
    # One deterministic interruption after a complete slot. The error contains
    # an outcome-like sentinel and must never escape the wrapper's response.
    if phase==0:
        original=driver.read_text()
        driver.write_text(original.replace('            runner.run()',
            "            if binding['window_ordinal']==4 and not (root/'batch-000/slot.json').exists():\n"
            "                runner.run(max_new_batches=1)\n"
            "                raise RuntimeError('SEALED_SENTINEL_987654321')\n"
            "            if binding['window_ordinal']==4 and runner.continuation_generation==1 and not (root/'batch-001-continuation-1/slot.json').exists():\n"
            "                runner.run(max_new_batches=1)\n"
            "                raise RuntimeError('SEALED_SENTINEL_SECOND_INTERRUPTION')\n"
            "            runner.run()"))
        scheduler=driver_root/'collection_run.py'
        original=scheduler.read_text()
        marker="        result=self.campaign_command('parts-complete',self.plan_path,self.root)"
        assert original.count(marker)==1
        scheduler.write_text(original.replace(marker,
            "        if self.evaluation and self.continuation_generation==2 and self.root.name=='w04':\n"
            "            raise RuntimeError('SEALED_SENTINEL_AFTER_ALL_SLOTS')\n"+marker))


    def native(*args,ok=True,exe=None):
        r=subprocess.run([str(exe or verifier),*[str(a) for a in args]],capture_output=True,
                         preexec_fn=process_limits,timeout=180)
        assert (r.returncode==0)==ok,(args,r.stdout,r.stderr)
        if not ok:
            assert b'SEALED_SENTINEL' not in r.stdout+r.stderr
            return r
        return json.loads(r.stdout)

    def sealed(plan,approval=None,ok=True,workers=None):
        chosen=workers or (decoder,projector,verifier)
        cmd=[sys.executable,str(driver),str(plan),*[str(w) for w in chosen]]
        if approval is not None:cmd.append(str(approval))
        r=subprocess.run(cmd,capture_output=True,timeout=300)
        assert (r.returncode==0)==ok,(r.returncode,r.stdout,r.stderr)
        expected={'state':'SEALED_PROCESSING_COMPLETE' if ok else 'SEALED_PROCESSING_STOPPED','outcomes_released':False}
        result=json.loads(r.stdout)
        for k,v in expected.items():assert result[k]==v
        assert set(result)<=set(expected)|{'research_ready'} and r.stderr==b'',(r.stdout,r.stderr)
        return result

    def expire_fixture_processing(work, generation):
        # Synthetic clock seam only. This fixture has no authority over the
        # authentic campaign. The real CLI handles every subsequent step.
        campaign=root/'campaign'
        head_path=campaign/'head.json'
        head=json.loads(head_path.read_bytes())
        record=campaign/'journal'/f"{head['sequence']:010}.json"
        state=json.loads(record.read_bytes())
        processing=state['processing']['4']
        stage=(processing['stage'] if generation==0 else
               processing['evaluation_continuations'][-1]['stage'])
        stage['deadline_boot_ms']=0
        stage['deadline_wall_ms']=0
        raw=json.dumps(state,separators=(',',':')).encode()
        record.write_bytes(raw)
        head['sha256']=hashlib.sha256(raw).hexdigest()
        head_path.write_text(json.dumps(head,separators=(',',':')))
        return head['sha256']

    def copied_workers(tag):
        folder=root/f'workers-{tag}'
        folder.mkdir()
        workers=[]
        for source in (decoder,projector,verifier):
            target=folder/source.name
            shutil.copy2(source,target)
            with target.open('ab') as handle:handle.write(f'\nfixture-worker-{tag}\n'.encode())
            workers.append(target)
        return tuple(workers)

    evidence=[{'ordinal':i,'manifest_sha256':digest(root/f'campaign/work/w{i:02}/collection.json')} for i in range(phase*4)]
    for ordinal in range(phase*4,(phase+1)*4):
        window_started=time.monotonic()
        env={**os.environ,'COLUMNAR_EVALUATION_FIXTURE_DIR':str(root),'COLUMNAR_EVALUATION_ORDINAL':str(ordinal)}
        subprocess.run([str(generator),'sealed_evaluation_source_fixture','--exact'],
                       env=env,capture_output=True,timeout=90,check=True)
        preparation_seconds=time.monotonic()-window_started
        if ordinal == 4:
            f=root/'continuation-fixture';run_root=root/'campaign/runs/w04'
            before={str(p.relative_to(run_root)):digest(p) for p in run_root.rglob('*') if p.is_file()}
            old_lease=(f/'lease.txt').read_text()
            def acquisition(op, lease, approval=None, ok=True):
                args=[op,str(run_root),str(f/'aggregate.json'),lease]
                if approval is not None:args.append(str(approval))
                argfile=f/'args.json';argfile.write_text(json.dumps(args))
                env={**os.environ,'OF1_CONTINUATION_TEST_ARGS':str(argfile),
                    'OF1_CONTINUATION_TEST_CLOCK':str(f/'clock.json'),
                    'OF1_CONTINUATION_TEST_RAW':str(f)}
                r=subprocess.run([str(acquisition_cli),'--exact','continuation_tests::cli_child','--nocapture'],
                                 env=env,capture_output=True,timeout=90)
                assert (r.returncode==0)==ok,(r.stdout,r.stderr)
                if ok:return json.loads(r.stdout[r.stdout.index(b'{'):r.stdout.rindex(b'}')+1])
            acquisition('capture-stage',old_lease,ok=False)
            proposal=acquisition('payload-continuation-proposal',old_lease)
            a=f/'approval.json';a.write_text(json.dumps(proposal['approval']))
            admitted=acquisition('payload-continuation-admit',old_lease,a)
            final=acquisition('capture-stage',admitted['current_lease_sha256'])
            assert final['published_requests']==20 and final['attempts_reserved']==21 and final['unpublished_attempts']==1
            assert all(digest(run_root/p)==h for p,h in before.items())
        path=root/f'plan-{ordinal:02}.json'
        work=root/f'campaign/work/w{ordinal:02}'
        approval=root/f'approval-{ordinal:02}.json'
        if ordinal in [4,5,6,7,12,13,14,15]:
            assert not path.exists(), 'fixture must not construct evaluation plan internally'
            head=(root/'campaign/head.json').read_bytes()
            source=native('evaluation-source',root/f'campaign/runs/w{ordinal:02}')
            assert set(source)=={'schema','source','method_sha256','acceptance_sha256','ledger_sha256','preparer_sha256'}
            source_path=root/f'source-{ordinal:02}.json';source_path.write_text(json.dumps(source))
            p=native('evaluation-plan',source_path,digest(decoder),digest(projector))
            assert not work.exists() and (root/'campaign/head.json').read_bytes()==head
            path.write_text(json.dumps(p,indent=2))
            if ordinal==4:
                native('source',root/'campaign/runs/w04',ok=False)
                native('evaluation-source',root/'campaign/runs/w03',ok=False)
                assert set(source['source'])=={'source_id','run_root','run_id','bindings','sample_identity'}
                assert set(source['source']['bindings'])=={'manifest_sha256','payload_manifest_sha256','aggregate_sha256','prepared_payload_sha256','metadata_receipt_sha256','receipts','sample_identity','payload_continuation_sha256'}
                receipt=root/'campaign/runs/w04'/source['source']['bindings']['receipts'][4]['path']
                original=receipt.read_bytes()
                try:
                    receipt.write_bytes(original+b' ')
                    # Valid JSON may have different physical bytes: the saved binding must fail.
                    r=native('evaluation-plan',source_path,digest(decoder),digest(projector),ok=False)
                    assert r.stdout==b'' and r.stderr==b'B7_EVALUATION_DENIED_OR_STOPPED\n'
                    corrupt=json.loads(original);corrupt['sha256']='0'*64
                    receipt.write_text(json.dumps(corrupt))
                    r=native('evaluation-source',root/'campaign/runs/w04',ok=False)
                    assert r.stdout==b'' and r.stderr==b'B7_EVALUATION_DENIED_OR_STOPPED\n'
                finally:receipt.write_bytes(original)
                for key in ['method_sha256','acceptance_sha256','ledger_sha256','preparer_sha256']:
                    bad=copy.deepcopy(source);bad[key]='0'*64
                    invalid=root/f'bad-preparation-{key}.json';invalid.write_text(json.dumps(bad))
                    native('evaluation-plan',invalid,digest(decoder),digest(projector),ok=False)
                for change in ['receipt','cohort','root','extra']:
                    bad=copy.deepcopy(source)
                    if change=='receipt':bad['source']['bindings']['receipts'][4]['sha256']='0'*64
                    elif change=='cohort':bad['source']['sample_identity']['b7']['cohort_role']='DEVELOPMENT'
                    elif change=='root':bad['source']['run_root']=str(root/'campaign/runs/w03')
                    else:bad['outcome']='SEALED_SENTINEL_987654321'
                    invalid=root/f'bad-preparation-{change}.json';invalid.write_text(json.dumps(bad))
                    r=native('evaluation-plan',invalid,digest(decoder),digest(projector),ok=False)
                    assert r.stdout==b'' and r.stderr==b'B7_EVALUATION_DENIED_OR_STOPPED\n'
                malformed=root/'bad-source.json';malformed.write_text('SEALED_SENTINEL_987654321')
                r=native('evaluation-plan',malformed,digest(decoder),digest(projector),ok=False)
                assert r.stdout==b'' and r.stderr==b'B7_EVALUATION_DENIED_OR_STOPPED\n'
                assert not work.exists() and (root/'campaign/head.json').read_bytes()==head
            proposal=native('evaluation-proposal',path,driver,sys.executable)
            a=proposal['binding'];assert a['evaluation']['method_sha256']=='493835145514ed99b3a8858be948f094d5a549f925a493baf6e047abfc8dbd75'
            approval.write_text(json.dumps(a))
            if ordinal==4:
                sealed(path,ok=False);assert not work.exists()
                for field in ['method_sha256','acceptance_sha256','previous_ledger_sha256','python_sha256']:
                    bad=copy.deepcopy(a);bad['evaluation'][field]='0'*64
                    invalid=root/f'invalid-{field}.json';invalid.write_text(json.dumps(bad))
                    sealed(path,invalid,ok=False);assert not work.exists()
                # An ordinary direct CLI cannot use even the valid fixture permit.
                native('campaign-admit',path,approval,ok=False);assert not work.exists()
                sealed(path,approval,ok=False)  # approved injected stop
                before={str(f.relative_to(work)):digest(f) for f in (work/'batch-000').rglob('*') if f.is_file()}
                head_before=json.loads((root/'campaign/head.json').read_text())
                assert not (work/'collection.json').exists()
                native('parts-inventory',work/'plan.json',work,ok=False)
                native('verify-batch',work/'plan.json','batch-000',work/'batch-000/part-0000/decode',ok=False)
                native('evaluation-read-released',root/'campaign',ok=False)
                native('evaluation-read-window',root/'campaign',4,ok=False)
                native('evaluation-release-proposal',root/'campaign','f'*64,ok=False)
                # Corruption is refused, not silently reprocessed; synthetic file only.
                marker=work/'batch-000/part-0000/parquet/COMPLETE';saved=marker.read_bytes();marker.write_bytes(b'corrupt')
                sealed(path,ok=False);marker.write_bytes(saved)
                assert json.loads((root/'campaign/head.json').read_text())==head_before
                # An unfinished original Parquet directory is retained as
                # evidence, never treated as a published slot.
                partial=work/'batch-001/part-0000/parquet'
                partial.mkdir(parents=True)
                (partial/'unfinished').write_bytes(b'fixture partial publication')
                retained_partial=digest(partial/'unfinished')
                native('parts-verify-retained-slot',path,work,'batch-001',ok=False)
                old_charge=native('evaluation-processing-status',path)['attempts_reserved']
                expire_fixture_processing(work,0)
                old_head=json.loads((root/'campaign/head.json').read_bytes())
                old_state=json.loads((root/'campaign/journal'/f"{old_head['sequence']:010}.json").read_bytes())
                sealed(path,ok=False)
                operation=work/'operation-0001.json';saved_operation=operation.read_bytes()
                try:
                    corrupt=json.loads(saved_operation);corrupt.pop('returncode')
                    operation.write_text(json.dumps(corrupt))
                    native('evaluation-processing-status',path,ok=False)
                finally:operation.write_bytes(saved_operation)
                first_workers=copied_workers('first')
                plan_result=native('evaluation-continuation-plan',work/'plan.json',digest(first_workers[0]),
                                   digest(first_workers[1]),exe=first_workers[2])
                first_plan=pathlib.Path(plan_result['plan_path'])
                assert plan_result['generation']==1
                first_proposal=native('evaluation-continuation-proposal',first_plan,driver,sys.executable,
                                      exe=first_workers[2])
                first_approval=root/'continuation-first.json'
                first_approval.write_text(json.dumps(first_proposal['binding']))
                for field in ['window','previous_ledger_sha256','work_tree_sha256','plan_sha256']:
                    bad=copy.deepcopy(first_proposal['binding'])
                    bad[field]=5 if field=='window' else '0'*64
                    invalid=root/f'invalid-continuation-{field}.json'
                    invalid.write_text(json.dumps(bad))
                    sealed(first_plan,invalid,ok=False,workers=first_workers)
                native('evaluation-continuation-admit',first_plan,driver,sys.executable,first_approval,
                       ok=False,exe=first_workers[2])  # driver-only native call denied
                sealed(first_plan,ok=False,workers=first_workers)
                sealed(first_plan,first_approval,ok=False,workers=first_workers)
                assert (work/'batch-001-continuation-1/slot.json').is_file()
                assert not (work/'collection.json').exists()
                assert not (partial/'slot.json').exists()
                assert digest(partial/'unfinished')==retained_partial
                (work/'operation-9999.stdout').write_bytes(b'SEALED_SENTINEL_UNRECEIPTED_OUTPUT')
                assert before=={str(f.relative_to(work)):digest(f) for f in (work/'batch-000').rglob('*') if f.is_file()}
                first_head=json.loads((root/'campaign/head.json').read_bytes())
                first_state=json.loads((root/'campaign/journal'/f"{first_head['sequence']:010}.json").read_bytes())
                assert first_state['processing']['4']['stage']==old_state['processing']['4']['stage']
                assert (first_state['requests'],first_state['entity'])==(old_state['requests'],old_state['entity'])
                assert (root/'campaign/journal'/f"{old_head['sequence']:010}.json").read_bytes()==json.dumps(old_state,separators=(',',':')).encode()
                operational=native('evaluation-processing-status',first_plan,exe=first_workers[2])
                assert operational['attempts_reserved']==old_charge
                assert operational['continuation_generation']==1 and operational['processing_complete'] is False
                assert operational['receipted_operation_count']>0 and operational['unreceipted_operation_count']==1
                assert operational['last_started_operation']=='9999'
                assert not any(key in operational for key in ['facts','labels','packages','silver_facts'])
                assert 'SEALED_SENTINEL' not in json.dumps(operational)
                native('evaluation-read-window',root/'campaign',4,ok=False,exe=first_workers[2])
                second_slot={str(f.relative_to(work)):digest(f) for f in
                             (work/'batch-001-continuation-1').rglob('*') if f.is_file()}
                expire_fixture_processing(work,1)
                sealed(first_plan,ok=False,workers=first_workers)
                second_workers=copied_workers('second')
                second_result=native('evaluation-continuation-plan',work/'plan.json',digest(second_workers[0]),
                                     digest(second_workers[1]),exe=second_workers[2])
                second_plan=pathlib.Path(second_result['plan_path'])
                assert second_result['generation']==2
                second_proposal=native('evaluation-continuation-proposal',second_plan,driver,sys.executable,
                                       exe=second_workers[2])
                assert second_proposal['sealed_slots_verified']==2
                second_approval=root/'continuation-second.json'
                second_approval.write_text(json.dumps(second_proposal['binding']))
                sealed(second_plan,second_approval,ok=False,workers=second_workers)
                assert not (work/'collection.json').exists()
                expire_fixture_processing(work,2)
                third_workers=copied_workers('third')
                third_result=native('evaluation-continuation-plan',work/'plan.json',digest(third_workers[0]),
                                    digest(third_workers[1]),exe=third_workers[2])
                third_plan=pathlib.Path(third_result['plan_path'])
                assert third_result['generation']==3
                third_proposal=native('evaluation-continuation-proposal',third_plan,driver,sys.executable,
                                      exe=third_workers[2])
                assert third_proposal['sealed_slots_verified']==16 and third_proposal['remaining_slots']==0
                third_approval=root/'continuation-third.json'
                third_approval.write_text(json.dumps(third_proposal['binding']))
                sealed(third_plan,third_approval,workers=third_workers)
                assert before=={str(f.relative_to(work)):digest(f) for f in (work/'batch-000').rglob('*') if f.is_file()}
                assert second_slot=={str(f.relative_to(work)):digest(f) for f in
                                    (work/'batch-001-continuation-1').rglob('*') if f.is_file()}
                final_head=json.loads((root/'campaign/head.json').read_bytes())
                final_state=json.loads((root/'campaign/journal'/f"{final_head['sequence']:010}.json").read_bytes())
                assert final_state['processing']['4']['stage']==old_state['processing']['4']['stage']
                assert final_state['processing']['4']['evaluation_continuations'][0]['approval']==first_state['processing']['4']['evaluation_continuations'][0]['approval']
                assert (final_state['requests'],final_state['entity'])==(old_state['requests'],old_state['entity'])
                assert digest(partial/'unfinished')==retained_partial
                final_status=native('evaluation-processing-status',third_plan,exe=third_workers[2])
                assert final_status['attempts_reserved']==old_charge
                assert final_status['processing_complete'] is True and final_status['continuation_generation']==3
                sealed(third_plan,third_approval,ok=False,workers=third_workers)
                native('evaluation-source',root/'campaign/runs/w04',ok=False)
            else:sealed(path,approval)
            assert not (work/'index.html').exists()
            child=work/'batch-000/part-0000/parquet'
            try:load_manifest(child)
            except ValueError as error:assert 'B7_EVALUATION_READ_NOT_AUTHORIZED' in str(error)
            else:raise AssertionError('generic reader leaked evaluation')
            for exe,args in [(decoder,[work/'plan.json','batch-000',work/'escape','0']),
                             (projector,[work/'batch-000/part-0000/decode',digest(work/'batch-000/part-0000/decode/execution.json'),work/'escape'])]:
                r=subprocess.run([str(exe),*[str(a) for a in args]],capture_output=True,preexec_fn=process_limits,timeout=30)
                assert r.returncode!=0 and not (work/'escape').exists()
        else:
            p=json.loads(path.read_bytes())
            p['workers']={'batch_decoder_sha256':digest(decoder),'projector_sha256':digest(projector)}
            path.write_text(json.dumps(p,indent=2))
            a={'authority':{'mode':'FIXTURE'},'window':ordinal,'plan_sha256':digest(path),
               'worker_sha256s':[digest(f) for f in [decoder,projector,verifier]]}
            approval.write_text(json.dumps(a))
            runner=Runner(path,work,decoder,projector,verifier,approval)
            runner.run();runner.driver_lock.close()
        # Only synthetic outcomes inspected here. Actual evaluation remains unopened.
        m=json.loads((work/'collection.json').read_bytes())
        assert m['sample_identity']==p['sources'][0]['sample_identity']
        assert m['layers']['bronze']['rows']==(146 if ordinal==4 else 16)
        if ordinal==4:
            assert m['transaction_status_counts']['ERROR']==65 and m['layers']['silver']['rows']==66
        evidence.append({'ordinal':ordinal,'manifest_sha256':digest(work/'collection.json')})
        if ordinal<15:native('evaluation-read-released',root/'campaign',ok=False)
        # Synthetic fixture timings only; never expose authenticated evaluation
        # workers' outputs. Retained failed/resumed operations remain included.
        operations=[json.loads(f.read_bytes()) for f in sorted(work.glob('operation-*.json'))]
        print(json.dumps({'fixture_timing':'OF1_SEALED_WINDOW_1','ordinal':ordinal,'phase':phase,
                          'preparation_seconds':preparation_seconds,
                          'window_seconds':time.monotonic()-window_started,
                          'recorded_operation_count':len(operations),
                          'recorded_operation_seconds':sum(o['elapsed_seconds'] for o in operations)}),flush=True)
    if phase<3:
        print(json.dumps({'state':'PASS','evidence':'Fixture','stage':phase,'same_campaign_continues':True}))
        return
    proposal=native('evaluation-release-proposal',root/'campaign','f'*64)
    a=proposal['binding'];release=root/'release.json';release.write_text(json.dumps(a))
    for field in ['method_sha256','previous_ledger_sha256']:
        bad=copy.deepcopy(a);bad['binding'][field]='0'*64
        invalid=root/f'release-{field}.json';invalid.write_text(json.dumps(bad))
        native('evaluation-release',root/'campaign',invalid,ok=False)
    native('evaluation-release',root/'campaign',release)
    native('evaluation-release',root/'campaign',release,ok=False)
    result=native('evaluation-read-released',root/'campaign')
    assert [w['window_ordinal'] for w in result['windows']]==[4,5,6,7,12,13,14,15]
    assert result['assessment_performed'] is False
    released=native('evaluation-read-window',root/'campaign',4)
    assert len(released['facts'])==66 and released['counts']['failures']==65
    assert released['instruction_inventory']['rule_version']=='DEVELOPMENT_INSTRUCTION_COVERAGE_1'
    native('evaluation-read-window',root/'campaign',0,ok=False)
    assert all(w['manifest_sha256']==evidence[w['window_ordinal']]['manifest_sha256'] for w in result['windows'])
    print(json.dumps({'state':'PASS','evidence':'Fixture','fixed_windows':16,'released_windows':8,
                      'method_sha256':a['binding']['method_sha256'],'error_and_resume':True,
                      'no_authentic_evaluation_access':True}))


if __name__=='__main__':run(*[pathlib.Path(v).resolve() for v in sys.argv[1:6]],int(sys.argv[6]),pathlib.Path(sys.argv[7]))
