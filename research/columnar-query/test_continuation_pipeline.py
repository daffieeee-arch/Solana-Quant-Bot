#!/usr/bin/env python3
"""Real native CLI/Parquet continuation, using only a synthetic Rust source.

Exercises intact prior manifests, rejected decision drift, missing/corrupt parts,
controlled interruption and exact resume. Fixture is never research evidence.
"""
import json
import pathlib
import subprocess
import sys

from collection_run import Runner, digest, process_limits
from continuation_run import ContinuationRunner


def inventory(root):
    return {str(p.relative_to(root)):digest(p) for p in root.rglob('*') if p.is_file()}


def run(fixture,decoder,projector,verifier):
    def native(*args,ok=True):
        result=subprocess.run([str(verifier),*[str(a) for a in args]],capture_output=True,
                              preexec_fn=process_limits,timeout=120)
        if ok:
            assert result.returncode==0,(args,result.stderr.decode())
            return json.loads(result.stdout)
        assert result.returncode!=0,('unexpected success',args)
        return result
    plan=json.loads((fixture/'plan.json').read_bytes())
    plan['workers']={'batch_decoder_sha256':digest(decoder),'projector_sha256':digest(projector)}
    path=fixture/'admitted-plan.json';path.write_text(json.dumps(plan,indent=2))
    root=fixture/'campaign/work/w00'
    authority=fixture/'authority.json';authority.write_text('{"mode":"FIXTURE"}')
    approval=fixture/'processing.json'
    approval.write_text(json.dumps({'authority':{'mode':'FIXTURE'},'window':0,'plan_sha256':digest(path),
                                   'worker_sha256s':[digest(p) for p in [decoder,projector,verifier]]}))
    initial=Runner(path,root,decoder,projector,verifier,approval)
    checkpoint=initial.run(max_new_batches=12)
    initial.driver_lock.close()
    before=inventory(root)
    old=json.loads(checkpoint.read_bytes())
    assert old['layers']['bronze']['rows']==12 and old['layers']['silver']['rows']==12
    proposal=native('continuation-proposal',root/'plan.json',checkpoint,digest(decoder),digest(projector))
    decision=fixture/'decision.json';decision.write_text(proposal['decision_json'])
    assert digest(decision)==proposal['decision_sha256']
    native('continuation-check',decision,0,ok=False)
    for field in ['ledger_sha256','checkpoint_sha256','original_processing_sha256']:
        bad=json.loads(proposal['decision_json']);bad[field]='f'*64
        # Decision's serde field order is preserved by Python insertion order.
        changed=fixture/(field+'.json');changed.write_text(json.dumps(bad,indent=2))
        native('continuation-admit',changed,authority,ok=False)
    assert inventory(root)==before
    runner=ContinuationRunner(decision,decoder,projector,verifier,authority)
    native('continuation-admit',decision,authority,ok=False)
    try: ContinuationRunner(decision,decoder,projector,verifier)
    except BlockingIOError: pass
    else: raise AssertionError('duplicate driver admitted')
    status=native('continuation-check',decision,0)
    deadline=status['deadline_boot_ms'];accounting=status['accounting']
    native('continuation-check',decision,4*1024**3,ok=False)
    native('continuation-seal-slot',decision,'batch-012',ok=False)
    assert not (runner.root/'batch-012/slot.json').exists()
    runner.run(max_new_parts=1)
    first=runner.root/'batch-012/part-0000'
    first_hashes=inventory(first)
    native('continuation-seal-slot',decision,'batch-012',ok=False)
    assert not (runner.root/'batch-012/slot.json').exists()
    partial=runner.root/'batch-012/part-0001/decode'
    partial.mkdir(parents=True)
    (partial/'quality.json').write_text('{}')
    native('continuation-verify-part',decision,'batch-012',1,'false',ok=False)
    assert not (runner.root/'batch-012/slot.json').exists()
    # Fixture-only removal; authentic failed artifacts are never repaired.
    (partial/'quality.json').unlink();partial.rmdir()
    # A torn/corrupt Parquet publication must not count as a complete part.
    marker=first/'parquet/COMPLETE';saved=marker.read_bytes();marker.write_bytes(b'wrong')
    native('continuation-verify-part',decision,'batch-012',0,'true',ok=False)
    marker.write_bytes(saved)
    runner.driver_lock.close()
    resumed=ContinuationRunner(decision,decoder,projector,verifier)
    status=native('continuation-check',decision,0)
    assert status['deadline_boot_ms']==deadline
    for key in ['attempts_reserved','entity_bytes_reserved']:
        assert status['accounting'][key]==accounting[key]
    final=resumed.run();resumed.driver_lock.close()
    manifest=json.loads(final.read_bytes())
    assert manifest['state']=='COMPLETE'
    assert manifest['layers']['bronze']['rows']==146
    assert manifest['layers']['silver']['rows']==81
    assert manifest['transaction_status_counts']['ERROR']==65
    assert manifest['retained_checkpoint']==old
    assert len(manifest['slot_outcomes'])==16
    assert all(s['state']=='ACCOUNTED' for s in manifest['slot_outcomes'])
    assert [s['producer'] for s in manifest['slot_outcomes']]==['ORIGINAL_RETAINED']*12+['CONTINUATION']*4
    assert manifest['sample_identity']==plan['sources'][0]['sample_identity']
    assert manifest['research_ready'] is False
    assert inventory(first)==first_hashes
    assert {name:digest(root/name) for name in before}==before
    assert digest(final)==(final.with_suffix('.json.sha256')).read_text()
    assert digest(final.parent/'campaign-report.json')==(final.parent/'campaign-report.COMPLETE').read_text()
    native('continuation-check',decision,0,ok=False)
    native('continuation-admit',decision,authority,ok=False)
    print(json.dumps({'state':'PASS','evidence':'Fixture','packages':146,'failures':65,'facts':81,
        'retained_slots':12,'continued_slots':4,'verified_parts':5,'same_charges':True,'same_deadline_on_restart':True}))


if __name__=='__main__':
    run(*[pathlib.Path(v).resolve() for v in sys.argv[1:]])
