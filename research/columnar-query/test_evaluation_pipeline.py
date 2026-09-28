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

from collection_run import Runner, digest, process_limits
from manifest_reader import load_manifest


def run(root, generator, decoder, projector, verifier, phase):
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
            "            runner.run()"))


    def native(*args,ok=True):
        r=subprocess.run([str(verifier),*[str(a) for a in args]],capture_output=True,
                         preexec_fn=process_limits,timeout=180)
        assert (r.returncode==0)==ok,(args,r.stdout,r.stderr)
        if not ok:
            assert b'SEALED_SENTINEL' not in r.stdout+r.stderr
            return r
        return json.loads(r.stdout)

    def sealed(plan,approval=None,ok=True):
        cmd=[sys.executable,str(driver),str(plan),str(decoder),str(projector),str(verifier)]
        if approval is not None:cmd.append(str(approval))
        r=subprocess.run(cmd,capture_output=True,timeout=300)
        assert (r.returncode==0)==ok,(r.returncode,r.stdout,r.stderr)
        expected={'state':'SEALED_PROCESSING_COMPLETE' if ok else 'SEALED_PROCESSING_STOPPED','outcomes_released':False}
        result=json.loads(r.stdout)
        for k,v in expected.items():assert result[k]==v
        assert set(result)<=set(expected)|{'research_ready'} and r.stderr==b'',(r.stdout,r.stderr)
        return result

    evidence=[{'ordinal':i,'manifest_sha256':digest(root/f'campaign/work/w{i:02}/collection.json')} for i in range(phase*4)]
    for ordinal in range(phase*4,(phase+1)*4):
        env={**os.environ,'COLUMNAR_EVALUATION_FIXTURE_DIR':str(root),'COLUMNAR_EVALUATION_ORDINAL':str(ordinal)}
        subprocess.run([str(generator),'sealed_evaluation_source_fixture','--exact'],
                       env=env,capture_output=True,timeout=90,check=True)
        path=root/f'plan-{ordinal:02}.json';p=json.loads(path.read_bytes())
        p['workers']={'batch_decoder_sha256':digest(decoder),'projector_sha256':digest(projector)}
        path.write_text(json.dumps(p,indent=2))
        work=root/f'campaign/work/w{ordinal:02}'
        approval=root/f'approval-{ordinal:02}.json'
        if ordinal in [4,5,6,7,12,13,14,15]:
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
                sealed(path)
                assert before=={str(f.relative_to(work)):digest(f) for f in (work/'batch-000').rglob('*') if f.is_file()}
                sealed(path,approval,ok=False)  # no budget/lease reset or duplicate
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


if __name__=='__main__':run(*[pathlib.Path(v).resolve() for v in sys.argv[1:6]],int(sys.argv[6]))
