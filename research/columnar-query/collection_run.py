#!/usr/bin/env python3
"""Sequential offline Rust workers, immutable checkpoints and exact resume.

No decoding, source rewriting, provider construction or plan improvisation.
The caller chooses an already reviewed Rust plan and exact worker binaries.
"""
import argparse
import hashlib
import json
import os
import pathlib
import resource
import subprocess
import sys
import time

from collection_reader import literal_path, load_collection, plan_inventory
from manifest_reader import MAX_MANIFEST_BYTES, pairs_unique, regular_bytes

PROCESS_BYTES = 2 * 1024**3
ARTIFACT_BYTES = 4 * 1024**3
STAGE_FILE_BYTES = 256 * 1024**2
STAGE_RESERVATION_BYTES = 3 * STAGE_FILE_BYTES + 1024**2
MIN_FREE_BYTES = ARTIFACT_BYTES


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as handle:
        while data := handle.read(1024*1024):
            h.update(data)
    return h.hexdigest()


def exclusive_json(path,value):
    with path.open('x',encoding='utf8') as handle:
        json.dump(value,handle,indent=2)
        handle.write('\n')


def artifact_size(root):
    total=0
    for parent,dirs,files in os.walk(root,followlinks=False):
        for name in dirs+files:
            path=pathlib.Path(parent)/name
            if path.is_symlink():
                raise ValueError('unexpected symlink inside new collection outputs')
        total+=sum((pathlib.Path(parent)/name).stat().st_size for name in files)
        if total>ARTIFACT_BYTES:
            raise ValueError('new collection artifacts exceed unchanged 4 GiB cap')
    return total


def process_limits():
    # Respect an enclosing development gate's stricter hard cap. A worker
    # cannot and must not increase an inherited resource limit.
    for kind,cap in [(resource.RLIMIT_AS,PROCESS_BYTES),(resource.RLIMIT_FSIZE,STAGE_FILE_BYTES)]:
        _,hard=resource.getrlimit(kind)
        effective=cap if hard==resource.RLIM_INFINITY else min(cap,hard)
        resource.setrlimit(kind,(effective,effective))


class Runner:
    def __init__(self,plan_path,root,decoder,projector,verifier):
        self.plan_path=plan_path.resolve(strict=True)
        self.plan_raw=regular_bytes(self.plan_path,MAX_MANIFEST_BYTES)
        self.plan=json.loads(self.plan_raw,object_pairs_hook=pairs_unique)
        self.sources,_,self.batches=plan_inventory(self.plan)
        self.root=root.resolve()
        self.decoder=decoder.resolve(strict=True)
        self.projector=projector.resolve(strict=True)
        self.verifier=verifier.resolve(strict=True)
        self.binary_hashes={str(p):digest(p) for p in [self.decoder,self.projector,self.verifier]}
        for path,key in [(self.decoder,'batch_decoder_sha256'),(self.projector,'projector_sha256')]:
            if not path.is_file() or digest(path)!=self.plan['workers'][key]:
                raise ValueError('worker executable differs from immutable plan')
        for source in self.sources.values():
            original=pathlib.Path(source['run_root']).resolve(strict=True)
            if self.root.is_relative_to(original) or original.is_relative_to(self.root):
                raise ValueError('collection outputs must be separate from preserved source runs')
        if not self.root.exists():
            self.root.mkdir()
        if self.root.is_symlink() or not self.root.is_dir():
            raise ValueError('ordinary collection directory required')
        retained=self.root/'plan.json'
        if retained.exists():
            if regular_bytes(retained,MAX_MANIFEST_BYTES)!=self.plan_raw:
                raise ValueError('resume plan identity mismatch')
        else:
            with retained.open('xb') as handle:
                handle.write(self.plan_raw)
        identity={'schema':'OF1_COLLECTION_RUNNER_IDENTITY_1','binary_hashes':self.binary_hashes,
                  'runner_sha256':digest(pathlib.Path(__file__)),
                  'reader_sha256':digest(pathlib.Path(__file__).with_name('collection_reader.py')),
                  'manifest_reader_sha256':digest(pathlib.Path(__file__).with_name('manifest_reader.py')),
                  'python_version':sys.version.split()[0],
                  'plan_sha256':hashlib.sha256(self.plan_raw).hexdigest()}
        identity_path=self.root/'runner-identity.json'
        if identity_path.exists():
            if json.loads(regular_bytes(identity_path,MAX_MANIFEST_BYTES),object_pairs_hook=pairs_unique)!=identity:
                raise ValueError('resume code/toolchain/binary identity mismatch')
        else:
            exclusive_json(identity_path,identity)
        self.plan_path=retained
        self.counter=0
        while (self.root/f'operation-{self.counter:04d}.json').exists() or (self.root/f'operation-{self.counter:04d}.stdout').exists():
            self.counter+=1

    def step(self,label,args):
        self.counter+=1
        prefix=self.root/f'operation-{self.counter:04d}'
        while pathlib.Path(str(prefix)+'.stdout').exists() or pathlib.Path(str(prefix)+'.json').exists():
            self.counter+=1
            prefix=self.root/f'operation-{self.counter:04d}'
        size=artifact_size(self.root)
        if size+STAGE_RESERVATION_BYTES>ARTIFACT_BYTES:
            raise ValueError('insufficient remaining artifact cap for bounded worker and two logs')
        stat=os.statvfs(self.root)
        if stat.f_bavail*stat.f_frsize < max(0,MIN_FREE_BYTES-size):
            raise ValueError('insufficient free space for remaining bounded collection output')
        if regular_bytes(self.plan_path,MAX_MANIFEST_BYTES)!=self.plan_raw:
            raise ValueError('plan changed before worker admission')
        executable=str(pathlib.Path(args[0]).resolve(strict=True))
        if executable not in self.binary_hashes or digest(pathlib.Path(executable))!=self.binary_hashes[executable]:
            raise ValueError('worker executable changed before admission')
        print(json.dumps({'stage':label,'state':'STARTED','operation':self.counter,
                          'artifacts_bytes':size}),flush=True)
        started=time.monotonic()
        error=None; returncode=None
        with pathlib.Path(str(prefix)+'.stdout').open('xb') as stdout, pathlib.Path(str(prefix)+'.stderr').open('xb') as stderr:
            try:
                result=subprocess.run([str(a) for a in args],stdout=stdout,stderr=stderr,
                                      preexec_fn=process_limits,timeout=900,
                                      env={**os.environ,'CARGO_NET_OFFLINE':'true','PYTHONDONTWRITEBYTECODE':'1'})
                returncode=result.returncode
            except (OSError,subprocess.SubprocessError) as failure:
                error=type(failure).__name__
        receipt={'schema':'OF1_COLLECTION_OPERATION_1','stage':label,'returncode':returncode,'error':error,
                 'elapsed_seconds':time.monotonic()-started,'executable_sha256':digest(pathlib.Path(args[0])),
                 'plan_sha256':hashlib.sha256(self.plan_raw).hexdigest(),
                 'stdout_sha256':digest(pathlib.Path(str(prefix)+'.stdout')),
                 'stderr_sha256':digest(pathlib.Path(str(prefix)+'.stderr')),
                 'artifact_bytes_after':artifact_size(self.root),'provider_calls':False,
                 'process_address_space_cap_bytes':PROCESS_BYTES}
        exclusive_json(pathlib.Path(str(prefix)+'.json'),receipt)
        print(json.dumps({'stage':label,'state':'VERIFIED_STEP' if returncode==0 and error is None else 'STOPPED',
                          'operation':self.counter,'elapsed_seconds':receipt['elapsed_seconds']}),flush=True)
        if returncode!=0 or error is not None:
            raise ValueError(f'Rust worker failed: {label}; retained {prefix}.stderr; no later batch executed')

    def snapshot(self):
        index=0
        while (self.root/f'collection-progress-{index:04d}.json').exists():
            index+=1
        path=self.root/f'collection-progress-{index:04d}.json'
        self.step('SEAL_READ_ONLY_PROGRESS',[self.verifier,'seal',self.plan_path,self.root,path])
        return load_collection(path)[0],path

    def run(self,max_new_batches=None):
        self.step('VALIDATE_PLAN_AND_ORIGINAL_SOURCES',[self.verifier,'plan-check',self.plan_path])
        manifest,last=self.snapshot()
        verified={b['batch_id'] for b in manifest['batches'] if b['state']=='VERIFIED'}
        completed=0
        for batch in self.batches:
            if batch['batch_id'] in verified:
                print(json.dumps({'batch_id':batch['batch_id'],'state':'REUSED_EXACT_RUST_VERIFIED_OUTPUT'}),flush=True)
                continue
            if max_new_batches is not None and completed>=max_new_batches:
                print(json.dumps({'state':'CONTROLLED_PAUSE','manifest':str(last),'complete':False}),flush=True)
                return last
            directory=literal_path(self.root,batch['output_directory'])
            directory.mkdir(parents=True,exist_ok=True)
            decoded,parquet=directory/'decode',directory/'parquet'
            if decoded.exists():
                self.step('VERIFY_RETAINED_DECODE_'+batch['batch_id'],[self.verifier,'verify-batch',self.plan_path,batch['batch_id'],decoded])
            else:
                self.step('DECODE_'+batch['batch_id'],[self.decoder,self.plan_path,batch['batch_id'],decoded])
            if parquet.exists():
                raise ValueError('unverified/partial Parquet output retained; never overwrite or silently reuse')
            self.step('PROJECT_'+batch['batch_id'],[self.projector,decoded,digest(decoded/'execution.json'),parquet])
            manifest,last=self.snapshot()
            if not any(b['batch_id']==batch['batch_id'] and b['state']=='VERIFIED' for b in manifest['batches']):
                raise ValueError('Rust seal did not verify completed worker output')
            completed+=1
        if manifest['state']!='COMPLETE':
            raise ValueError('all attempted batches do not prove complete collection')
        final=self.root/'collection.json'
        if not final.exists():
            self.step('SEAL_FINAL_COLLECTION',[self.verifier,'seal',self.plan_path,self.root,final])
        elif load_collection(final)[0]!=manifest:
            raise ValueError('existing final collection differs from current exact verification')
        print(json.dumps({'state':'COMPLETE','manifest':str(final),'research_ready':False}),flush=True)
        return final


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan',type=pathlib.Path)
    parser.add_argument('output',type=pathlib.Path)
    for name in ['decoder','projector','verifier']:
        parser.add_argument('--'+name,type=pathlib.Path,required=True)
    parser.add_argument('--max-new-batches',type=int)
    args=parser.parse_args()
    if args.max_new_batches is not None and args.max_new_batches<0:
        parser.error('max-new-batches must be nonnegative')
    Runner(args.plan,args.output,args.decoder,args.projector,args.verifier).run(args.max_new_batches)
