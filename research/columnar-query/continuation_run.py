#!/usr/bin/env python3
"""One explicitly admitted offline B7 continuation. Rust verifies all meaning.

Schedules only the native inventory's four remaining slots/atomic parts. Reuses
old output by verification; no reacquisition, refunds, lease renewal or repair.
"""
import argparse
import fcntl
import hashlib
import json
import pathlib
import sys
import subprocess

from collection_run import Runner, digest, exclusive_json, process_limits
from manifest_reader import MAX_MANIFEST_BYTES, pairs_unique, regular_bytes


class ContinuationRunner(Runner):
    def __init__(self, decision, decoder, projector, verifier, authority=None):
        self.decision_path=decision.resolve(strict=True)
        decision_raw=regular_bytes(self.decision_path,MAX_MANIFEST_BYTES)
        d=json.loads(decision_raw,object_pairs_hook=pairs_unique)
        self.decoder=decoder.resolve(strict=True)
        self.projector=projector.resolve(strict=True)
        self.verifier=verifier.resolve(strict=True)
        self.binary_hashes={str(p):digest(p) for p in [self.decoder,self.projector,self.verifier]}
        if [digest(p) for p in [self.decoder,self.projector,self.verifier]] != [
                d['plan']['workers']['batch_decoder_sha256'],d['plan']['workers']['projector_sha256'],d['collector_sha256']]:
            raise ValueError('continuation worker mismatch')
        self.accounting_root=pathlib.Path(d['original_plan_path']).parent.resolve(strict=True)
        self.root=self.accounting_root/'continuation-1'
        self.campaign=True
        # Same original driver lock also excludes any old-run scheduler. Native
        # admission remains the authority; a local lock never grants processing.
        lock=self.accounting_root/'driver.lock'
        if lock.is_symlink() or not lock.is_file(): raise ValueError('native driver lock required')
        self.driver_lock=lock.open('r+')
        fcntl.flock(self.driver_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            if authority is not None:
                self.campaign_command('continuation-admit',self.decision_path,authority)
            self.campaign_check(1024*1024)
            if regular_bytes(self.root/'decision.json',MAX_MANIFEST_BYTES)!=decision_raw:
                raise ValueError('admitted continuation decision mismatch')
            self.plan_path=self.root/'plan.json'
            self.plan_raw=regular_bytes(self.plan_path,MAX_MANIFEST_BYTES)
            self.plan=json.loads(self.plan_raw,object_pairs_hook=pairs_unique)
            identity={'schema':'OF1_CONTINUATION_RUNNER_1','decision_sha256':hashlib.sha256(decision_raw).hexdigest(),
                      'binary_hashes':self.binary_hashes,'runner_sha256':digest(pathlib.Path(__file__)),
                      'worker_runner_sha256':digest(pathlib.Path(__file__).with_name('collection_run.py')),
                      'manifest_reader_sha256':digest(pathlib.Path(__file__).with_name('manifest_reader.py')),
                      'python_version':sys.version.split()[0]}
            p=self.root/'runner-identity.json'
            if p.exists():
                if json.loads(regular_bytes(p,MAX_MANIFEST_BYTES),object_pairs_hook=pairs_unique)!=identity:
                    raise ValueError('continuation runner changed on resume')
            else: exclusive_json(p,identity)
            self.counter=0
        except BaseException:
            self.driver_lock.close()
            raise

    def campaign_command(self,*args):
        # Native completion may stream-verify the whole retained output. Its
        # upper bound is the already admitted absolute lease, never a reset.
        if args[0]=='continuation-complete':
            timeout=self.remaining_until(self.deadline_boot_ms)
        elif args[0]=='continuation-admit':
            timeout=120  # Prefix verification before the one native admission.
        else:
            return super().campaign_command(*args)
        result=subprocess.run([str(self.verifier),*[str(a) for a in args]],capture_output=True,
                              preexec_fn=process_limits,timeout=timeout,check=True)
        if len(result.stdout)>1024*1024: raise ValueError('oversize native continuation status')
        return json.loads(result.stdout,object_pairs_hook=pairs_unique)

    def campaign_check(self,reserve):
        status=self.campaign_command('continuation-check',self.decision_path,reserve)
        if status.get('state')!='WITHIN_CONTINUATION_LEASE' or status.get('remaining_ms',0)<=0:
            raise ValueError('continuation lease exhausted')
        deadline=status.get('deadline_boot_ms')
        if type(deadline) is not int: raise ValueError('absolute continuation deadline required')
        self.deadline_boot_ms=deadline
        return self.remaining_until(deadline)

    def run(self,max_new_parts=None):
        self.step('VERIFY_RETAINED_TWELVE_SLOTS',[self.verifier,'continuation-verify-retained',self.decision_path])
        inventory=self.campaign_command('continuation-inventory',self.decision_path)
        completed=0
        for batch in inventory['batches']:
            name=batch['batch_id']
            directory=self.root/batch['output_directory']
            for ordinal in range(batch['inventory']['parts']):
                part=directory/f'part-{ordinal:04d}'
                decoded,parquet=part/'decode',part/'parquet'
                if not parquet.exists() and max_new_parts is not None and completed>=max_new_parts:
                    print(json.dumps({'state':'CONTROLLED_PAUSE','next_batch':name,'next_part':ordinal}),flush=True)
                    return None
                self.campaign_check(1024*1024)
                part.mkdir(parents=True,exist_ok=True)
                if decoded.exists():
                    self.step('VERIFY_DECODE_PART',[self.verifier,'continuation-verify-part',self.decision_path,name,ordinal,'false'])
                else:
                    self.step('DECODE_PART',[self.decoder,self.plan_path,name,decoded,ordinal])
                if parquet.exists():
                    self.step('VERIFY_PARQUET_PART',[self.verifier,'continuation-verify-part',self.decision_path,name,ordinal,'true'])
                else:
                    self.step('PROJECT_PART',[self.projector,decoded,digest(decoded/'execution.json'),parquet])
                    completed+=1
            self.step('SEAL_COMPLETE_SLOT',[self.verifier,'continuation-seal-slot',self.decision_path,name])
        # Native completion verifies and publishes under one lock. Its artifacts
        # have explicit bounds. No post-completion file write inside the campaign.
        result=self.campaign_command('continuation-complete',self.decision_path)
        print(json.dumps(result),flush=True)
        return self.root/'collection.json'


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('decision',type=pathlib.Path)
    for name in ['decoder','projector','verifier']:
        parser.add_argument('--'+name,type=pathlib.Path,required=True)
    parser.add_argument('--authority',type=pathlib.Path)
    parser.add_argument('--max-new-parts',type=int)
    args=parser.parse_args()
    if args.max_new_parts is not None and args.max_new_parts<0: parser.error('nonnegative pause boundary required')
    runner=ContinuationRunner(args.decision,args.decoder,args.projector,args.verifier,args.authority)
    try: runner.run(args.max_new_parts)
    finally: runner.driver_lock.close()
