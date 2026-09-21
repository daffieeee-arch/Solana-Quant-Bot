#!/usr/bin/env python3
"""Executed six-slot Rust fixture -> batches -> Rust Parquet -> collection SQL.

Requires an explicit previously exported synthetic source and existing offline
binaries. Does not compile, fetch, download, mutate an authentic run or decode
in Python. The test publishes only new directories beneath NEW_OUTPUT.
"""
import argparse
import hashlib
import json
import pathlib
import shutil

from collection_reader import attach_collection,load_collection
from collection_report import run as report
from collection_run import Runner,digest
from query import connect
from manifest_reader import attach_dataset,load_manifest


def inventory(root):
    return {str(path.relative_to(root)):{'bytes':path.stat().st_size,'sha256':digest(path)}
            for path in sorted(root.rglob('*')) if path.is_file()}


def reject_incomplete_children(complete_root,output):
    """Resealed metadata must not promote incomplete Rust-written children."""
    for mutation in ['missing-package','unaccounted-equal-counts']:
        clone=output/mutation
        shutil.copytree(complete_root,clone)
        manifest,_=load_collection(clone)
        child=clone/manifest['batches'][0]['parquet_manifest_path']
        child_manifest=json.loads(child.read_bytes())
        selection=child_manifest['selection'];slot=selection['slots'][0]
        slot['accounted']=False
        if mutation=='missing-package':
            slot['expected_packages']+=1
            manifest['slot_outcomes'][0]['transaction_envelopes']+=1
        selection['status']='INCOMPLETE'
        selection['all_expected_packages_accounted']=False
        child.write_text(json.dumps(child_manifest,indent=2)+'\n')
        (child.parent/'COMPLETE').write_text(digest(child)+'\n')
        manifest['batches'][0]['parquet_manifest_sha256']=digest(child)
        outer=clone/'collection.json'
        outer.write_text(json.dumps(manifest,indent=2)+'\n')
        (clone/'collection.json.sha256').write_text(digest(outer)+'\n')
        # An incomplete dataset is readable evidence in its own right. The
        # collection must reject its promotion, not rely on a corrupt hash.
        checked_child,_=load_manifest(child.parent)
        with connect() as db:attach_dataset(db,child.parent,checked_child)
        checked,_=load_collection(clone)
        assert checked['state']=='COMPLETE'
        try:
            with connect() as db:attach_collection(db,clone,checked)
        except ValueError as error:
            assert 'child package accounting incomplete' in str(error),error
        else:raise AssertionError(f'collection falsely accepted {mutation} child')


def run(fixture,output,decoder,projector,verifier):
    if output.exists():raise ValueError('new synthetic pipeline output required')
    output.mkdir()
    before=inventory(fixture)
    summaries=[];collections=[]
    for width in [1,2,3]:
        original=fixture/f'plan-width-{width}.json'
        plan=json.loads(original.read_bytes())
        plan['workers']={'batch_decoder_sha256':digest(decoder),'projector_sha256':digest(projector)}
        path=output/f'plan-{width}.json';path.write_text(json.dumps(plan,indent=2)+'\n')
        root=output/f'width-{width}'
        runner=Runner(path,root,decoder,projector,verifier)
        if width==1:
            progress=runner.run(max_new_batches=1)
            snapshot,_=load_collection(progress)
            assert snapshot['state']=='INCOMPLETE'
            assert sum(b['state']=='VERIFIED' for b in snapshot['batches'])==1
            first=root/plan['batches'][0]['output_directory']
            first_before=inventory(first)
            report(progress,output/'progress-report')
            resumed=Runner(path,root,decoder,projector,verifier)
            final=resumed.run()
            assert inventory(first)==first_before,'verified first worker must never be replayed'
        else:
            final=runner.run()
        manifest,_=load_collection(final)
        assert manifest['state']=='COMPLETE'
        with connect() as db:
            summary=attach_collection(db,root,manifest)
            assert len(summary['selected_slots'])==6
            assert all(row['accounted'] for row in summary['selected_slots'])
            outcomes=dict(db.execute('SELECT disposition,COUNT(*) FROM bronze GROUP BY disposition').fetchall())
            assert outcomes=={'DECODED':3,'MISSING':1,'QUARANTINED':1,'UNSUPPORTED':1},outcomes
            assert db.execute("SELECT COUNT(*) FROM bronze WHERE transaction_status='ERROR'").fetchone()[0]>=1
            assert db.execute('SELECT COUNT(*) FROM silver').fetchone()[0]==2
            assert db.execute('SELECT record_ordinal FROM bronze ORDER BY record_ordinal').fetchall()==[(i,) for i in range(6)]
            summary['test_outcomes']=outcomes
        report(root,output/f'report-{width}')
        summaries.append(summary);collections.append(manifest)
    assert collections[0]['layers']==collections[1]['layers']==collections[2]['layers'],'partition changed canonical record stream'
    reject_incomplete_children(output/'width-3',output)
    for mutation in ['missing-shard','corrupt-shard','changed-plan']:
        clone=output/mutation
        shutil.copytree(output/'width-3',clone)
        manifest,_=load_collection(clone)
        child=clone/manifest['batches'][0]['parquet_manifest_path']
        child_manifest=json.loads(child.read_bytes())
        shard=child.parent/child_manifest['layers']['bronze']['files'][0]
        if mutation=='missing-shard':shard.unlink()
        elif mutation=='corrupt-shard':
            with shard.open('r+b') as file:
                first=file.read(1);file.seek(0);file.write(bytes([first[0]^1]))
        else:
            plan=json.loads((clone/'plan.json').read_bytes());plan['collection_id']='FORGED_RESUME_PLAN'
            (clone/'plan.json').write_text(json.dumps(plan))
        try:
            checked,_=load_collection(clone)
            with connect() as db:attach_collection(db,clone,checked)
        except (ValueError,FileNotFoundError):pass
        else:raise AssertionError(f'collection falsely accepted {mutation}')
    assert inventory(fixture)==before,'original synthetic source/plan inventory changed'
    receipt={'schema':'OF1_COLLECTION_PIPELINE_TEST_1','input':'SEALED_SYNTHETIC_RUST_FIXTURE',
             'slots':6,'widths':[1,2,3],'logical_layers':collections[0]['layers'],
             'original_source_unchanged':True,'resume_first_batch_unchanged':True,
             'missing_corrupt_changed_plan_rejected':True,
             'incomplete_child_promotion_rejected':True,
             'outcomes':summaries[0]['test_outcomes'],'provider_calls':False,
             'research_ready':False,'binary_hashes':{p.name:digest(p) for p in [decoder,projector,verifier]}}
    (output/'pipeline-test.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['fixture','output','decoder','projector','verifier']:parser.add_argument(name,type=pathlib.Path)
    args=parser.parse_args()
    run(args.fixture,args.output,args.decoder,args.projector,args.verifier)
