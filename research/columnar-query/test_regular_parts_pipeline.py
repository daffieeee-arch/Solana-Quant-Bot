#!/usr/bin/env python3
"""Real CLI fixture: normal B7 processing starts with bounded atomic parts.

An interruption after part zero preserves partial evidence, not a complete slot.
No acquisition, outcome selection, changed limits or synthetic research claims.
"""
import copy
import json
import pathlib
import subprocess
import sys

from collection_run import Runner, digest, process_limits
from manifest_reader import attach_dataset


def run(fixture, decoder, projector, verifier):
    def native(*args, ok=True):
        r=subprocess.run([str(verifier),*[str(a) for a in args]],capture_output=True,
                         preexec_fn=process_limits,timeout=120)
        assert (r.returncode==0)==ok,(args,r.stdout,r.stderr)
        return json.loads(r.stdout) if ok else r

    plan=json.loads((fixture/'plan.json').read_bytes())
    plan['workers']={'batch_decoder_sha256':digest(decoder),'projector_sha256':digest(projector)}
    path=fixture/'admitted-plan.json';path.write_text(json.dumps(plan,indent=2))
    root=fixture/'campaign/work/w00'
    for mutation in ['profile','role','partition']:
        bad=copy.deepcopy(plan)
        if mutation=='profile':bad['slot_part_profile']='OF1_ATOMIC_SLOT_PARTS_256_V1'
        elif mutation=='role':bad['sources'][0]['sample_identity']['b7']['cohort_role']='RESERVED_EVALUATION'
        else:bad['batches'][0]['slots']+=bad['batches'][1]['slots']
        invalid=fixture/(mutation+'.json');invalid.write_text(json.dumps(bad))
        native('plan-check',invalid,ok=False)
    native('parts-inventory',path,root,ok=False)
    assert not root.exists()
    approval=fixture/'approval.json';approval.write_text(json.dumps({
        'authority':{'mode':'FIXTURE'},'window':0,'plan_sha256':digest(path),
        'worker_sha256s':[digest(p) for p in [decoder,projector,verifier]]}))
    runner=Runner(path,root,decoder,projector,verifier,approval)
    status=native('campaign-check',runner.plan_path,root,0)
    native('campaign-check',runner.plan_path,root,4*1024**3,ok=False)
    native('parts-inventory',runner.plan_path,root/'other',ok=False)
    result=subprocess.run([str(decoder),str(runner.plan_path),'batch-000',str(root/'whole')],
        capture_output=True,preexec_fn=process_limits,timeout=30)
    assert result.returncode!=0 and b'PLAN_REQUIRES_ATOMIC_PARTS' in result.stderr
    assert not (root/'whole').exists()
    # Inject a scheduler stop after a successful native part publication. This
    # simulates a process interruption without changing/corrupting native code.
    step=runner.step
    def stop_after_first_project(label,args):
        step(label,args)
        if label=='PROJECT_PART':raise InterruptedError('fixture stop after first atomic part')
    runner.step=stop_after_first_project
    try:runner.run()
    except InterruptedError:pass
    else:raise AssertionError('interruption not reached')
    first=root/'batch-000/part-0000'
    first_hashes={str(p.relative_to(first)):digest(p) for p in first.rglob('*') if p.is_file()}
    assert not (root/'batch-000/decode').exists()
    assert not (root/'batch-000/slot.json').exists()
    native('parts-seal-slot',runner.plan_path,root,'batch-000',ok=False)
    native('parts-complete',runner.plan_path,root,ok=False)
    assert not (root/'collection.json').exists()
    # A damaged existing publication cannot be silently accepted on restart.
    marker=first/'parquet/COMPLETE';saved=marker.read_bytes();marker.write_bytes(b'invalid')
    native('parts-verify',runner.plan_path,root,'batch-000',0,'true',ok=False)
    marker.write_bytes(saved)  # synthetic fixture only
    runner.driver_lock.close()
    resumed=Runner(path,root,decoder,projector,verifier)
    again=native('campaign-check',resumed.plan_path,root,0)
    assert again['deadline_boot_ms']==status['deadline_boot_ms']
    final=resumed.run();resumed.driver_lock.close()
    m=json.loads(final.read_bytes())
    assert m['state']=='COMPLETE' and m['schema']=='OF1_PARTED_BATCH_COLLECTION_1'
    assert m['sample_identity']==plan['sources'][0]['sample_identity']
    assert m['layers']['bronze']['rows']==146 and m['layers']['silver']['rows']==81
    assert m['transaction_status_counts']['ERROR']==65
    assert len(m['slots'])==len(m['slot_outcomes'])==16
    assert [s['part_count'] for s in m['slot_outcomes']]==[2]+[1]*15
    assert all(s['state']=='ACCOUNTED' for s in m['slot_outcomes'])
    assert {str(p.relative_to(first)):digest(p) for p in first.rglob('*') if p.is_file()}==first_hashes
    identities=set();facts=[];failed=set()
    import hashlib
    logical={layer:b'OF1_ORDERED_RECORD_CHAIN_1' for layer in ['bronze','silver']}
    for child in m['slots']:
        slotpath=root/child['manifest_path'];assert digest(slotpath)==child['sha256']
        slot=json.loads(slotpath.read_bytes())
        for part in slot['parts']:
            assert digest(root/part['parquet_manifest_path'])==part['parquet_manifest_sha256']
            decoded=root/part['decode_directory']
            for layer in ['bronze','silver']:
                for raw in (decoded/(layer+'.jsonl')).read_bytes().splitlines():
                    logical[layer]=hashlib.sha256(logical[layer]+len(raw).to_bytes(8,'little')+raw).digest()
                    record=json.loads(raw)
                    if layer=='bronze':
                        identity=(record['effective_at']['slot'],record['effective_at']['transaction_index_in_slot'])
                        assert identity not in identities;identities.add(identity)
                        if record['transaction']['status']=='ERROR':failed.add(identity)
                    else:facts.append(record)
            # Native verifier already checked this bound part manifest. The
            # generic complete-selection reader intentionally rejects partial slots.
            dataset=json.loads((root/part['parquet_manifest_path']).read_bytes())
            assert dataset['sample_identity']==m['sample_identity']
            try:attach_dataset(None,first/'parquet',dataset)
            except ValueError as e:assert 'B7_ANALYTICAL_EXPORT_NOT_AUTHORIZED' in str(e)
            else:raise AssertionError('analytical/evaluation export admitted')
    assert len(identities)==146 and len(failed)==65 and len(facts)==81
    for layer in logical:assert logical[layer].hex()==m['layers'][layer]['ordered_logical_sha256']
    for fact in facts:assert (fact['effective_at']['slot'],fact['effective_at']['transaction_index_in_slot']) not in failed
    assert digest(final)==final.with_suffix('.json.sha256').read_text()
    assert digest(root/'campaign-report.json')==(root/'campaign-report.COMPLETE').read_text()
    assert m['research_ready'] is False
    native('campaign-check',resumed.plan_path,root,0,ok=False)
    native('parts-complete',resumed.plan_path,root,ok=False)
    print(json.dumps({'state':'PASS','evidence':'Fixture','packages':146,'failures':65,'facts':81,
        'ordinary_parts_from_first_slot':True,'same_deadline':True,'independent_logical_hashes':True}))


if __name__=='__main__':run(*[pathlib.Path(v).resolve() for v in sys.argv[1:]])
