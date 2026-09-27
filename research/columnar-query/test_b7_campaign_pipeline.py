#!/usr/bin/env python3
"""Small native Fixture campaign through the real immutable worker route.

Not acquisition or research evidence. Only the explicit temporary campaign is
written; no provider or alternate decoding. The source fixture is Rust-created.
"""
import copy
import json
import pathlib
import sys
import subprocess

from collection_reader import load_collection, attach_collection
from collection_run import Runner, digest, process_limits
from manifest_reader import attach_dataset, load_manifest, sample_inventory


def run(fixture, decoder, projector, verifier):
    plan=json.loads((fixture/'plan.json').read_bytes())
    plan['workers']={'batch_decoder_sha256':digest(decoder),'projector_sha256':digest(projector)}
    path=fixture/'admitted-plan.json'
    path.write_text(json.dumps(plan,indent=2)+'\n')
    sample=plan['sources'][0]['sample_identity']
    output=fixture/'campaign/work/w00'
    try: Runner(path,output,decoder,projector,verifier)
    except ValueError as e: assert 'approval' in str(e)
    else: raise AssertionError('unapproved processing accepted')
    assert not output.exists()
    approval=fixture/'processing-fixture.json'
    approval.write_text(json.dumps({'authority':{'mode':'FIXTURE'},'window':0,'plan_sha256':digest(path),
                                   'worker_sha256s':[digest(p) for p in [decoder,projector,verifier]]}))
    runner=Runner(path,output,decoder,projector,verifier,approval)
    try: Runner(path,output,decoder,projector,verifier)
    except BlockingIOError: pass
    else: raise AssertionError('duplicate campaign driver')
    probe=[str(verifier),'campaign-check',str(runner.plan_path),str(output),'0']
    unlimited=subprocess.run(probe,capture_output=True,timeout=30)
    assert unlimited.returncode==1 and unlimited.stderr==b'Error: WorkerLimit\n', unlimited.stderr
    bounded=subprocess.run(probe,capture_output=True,preexec_fn=process_limits,timeout=30,check=True)
    assert json.loads(bounded.stdout)['remaining_ms']>0
    runner.run(max_new_batches=1)
    pending=output/'collection-progress-0000.json'
    pending_manifest,_=load_collection(pending)
    assert all(b['state']=='PENDING' for b in pending_manifest['batches'])
    try: attach_collection(None,output,pending_manifest)
    except ValueError as e: assert 'B7_ANALYTICAL_EXPORT_NOT_AUTHORIZED' in str(e)
    else: raise AssertionError('pending B7 collection escaped campaign admission')
    outside=fixture/'denied-generic-report'
    generic=subprocess.run([sys.executable,str(pathlib.Path(__file__).with_name('collection_report.py')),str(pending),str(outside)],capture_output=True,timeout=30)
    assert generic.returncode!=0 and b'B7_ANALYTICAL_EXPORT_NOT_AUTHORIZED' in generic.stderr
    assert not outside.exists()
    first=output/plan['batches'][0]['output_directory']/'parquet'
    initial=digest(first/'manifest.json')
    runner.driver_lock.close()
    resumed=Runner(path,output,decoder,projector,verifier)
    manifest_path=resumed.run()
    manifest,_=load_collection(manifest_path)
    assert manifest['state']=='COMPLETE'
    assert manifest['layers']['bronze']['rows']==16
    assert manifest['layers']['silver']['rows']==1
    assert digest(first/'manifest.json')==initial
    for batch in manifest['batches']:
        child,_=load_manifest(output/batch['parquet_manifest_path'].rsplit('/',1)[0])
        assert child['sample_identity']==sample
        assert child['input']['execution']['sample_identity']==sample
        assert child['batch_binding']['sample_identity']==sample
        assert child['evidence']['receipt_evidence']=='Fixture'
        assert child['evidence']['research_ready'] is False
        bad=copy.deepcopy(child);bad['sample_identity']['b7']['cohort_role']='RESERVED_EVALUATION'
        try: sample_inventory(bad)
        except ValueError: pass
        else: raise AssertionError('role tampering accepted')
        try: attach_dataset(None,first,child)
        except ValueError as e: assert 'B7_ANALYTICAL_EXPORT_NOT_AUTHORIZED' in str(e)
        else: raise AssertionError('unbudgeted analytical/evaluation export')
    report=json.loads((output/'campaign-report.json').read_bytes())
    assert report['sample_identity']==sample
    assert report['campaign_accounting']['attempts_reserved']==20
    assert report['layers']['bronze']['rows']==16
    assert (output/'index.html').is_file()
    print(json.dumps({'state':'PASS','evidence':'Fixture','packages':16,'silver_facts':1,
                      'same_sample_in_all_native_manifests':True,'resume_preserves_first_batch':True}))


if __name__=='__main__':
    run(*[pathlib.Path(v).resolve() for v in sys.argv[1:]])
