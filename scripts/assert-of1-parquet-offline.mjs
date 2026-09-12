#!/usr/bin/env node
// Offline physical projection only. No registry access, provider or acquisition.
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { readFileSync, mkdtempSync, rmSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { buildResearchSeccompLauncher } from './build-research-seccomp-launcher.mjs';
import { writeResearchNetworkDenyFilter } from './write-research-seccomp-filter.mjs';

const root=resolve(import.meta.dirname,'..'),crate=join(root,'rust/of1-parquet-projection');
const hash=b=>createHash('sha256').update(b).digest('hex');
const reviewed=JSON.parse(readFileSync(join(crate,'dependency-review.json'),'utf8'));
const scratch=mkdtempSync(join(tmpdir(),'of1-parquet-offline-'));
try {
  const launcher=join(scratch,'launcher'),filter=join(scratch,'network-deny.bpf');
  await writeResearchNetworkDenyFilter(filter,process.arch,{allowLocalProcessSpawn:true});
  await buildResearchSeccompLauncher(launcher);
  const run=(command,args)=>{
    const r=spawnSync(launcher,[filter,command,...args],{cwd:root,encoding:'utf8',maxBuffer:32*1024*1024,timeout:900_000,env:{...process.env,CARGO_NET_OFFLINE:'true'}});
    if(r.error||r.status!==0)throw Error(r.error?.message||r.stderr||r.stdout||'offline gate failed');return r.stdout;
  };
  const probe=run(process.execPath,['--input-type=module','-e',"import net from 'node:net';const s=net.createConnection({host:'127.0.0.1',port:9});s.on('connect',()=>process.exit(2));s.on('error',e=>{if(e.code==='EPERM')console.log('NETWORK_DENIED');else process.exit(3);});"]);
  if(probe!=='NETWORK_DENIED\n')throw Error('socket denial not established');
  const manifest=['--manifest-path',join(crate,'Cargo.toml')];
  for(const [name,key] of [['Cargo.toml','manifest_sha256'],['Cargo.lock','lock_sha256']])if(hash(readFileSync(join(crate,name)))!==reviewed[key])throw Error('dependency identity drift');
  const m=JSON.parse(run('cargo',['+1.97.1','metadata',...manifest,'--locked','--offline','--format-version','1']));
  const ids=new Map(m.packages.map(p=>[p.id,`${p.name}@${p.version}`]));
  const packages=m.packages.map(p=>({name:p.name,version:p.version,license:p.license,buildScript:p.targets.some(t=>t.kind.includes('custom-build'))})).sort((a,b)=>`${a.name}@${a.version}`.localeCompare(`${b.name}@${b.version}`));
  const features=m.resolve.nodes.map(n=>({package:ids.get(n.id),features:n.features})).sort((a,b)=>a.package.localeCompare(b.package));
  if(JSON.stringify(packages)!==JSON.stringify(reviewed.packages)||JSON.stringify(features)!==JSON.stringify(reviewed.features))throw Error('unreviewed dependency graph/features/licenses');
  for(const entry of reviewed.build_scripts){const p=m.packages.find(p=>`${p.name}@${p.version}`===entry.package);const t=p?.targets.find(t=>t.kind.includes('custom-build'));if(!t||hash(readFileSync(t.src_path))!==entry.sha256)throw Error('build script drift');}
  for(const bad of ['object_store','reqwest','hyper','tokio','solana-rpc-client','openssl','zstd-sys','lz4-sys'])if(packages.some(p=>p.name===bad))throw Error(`unexpected runtime/native dependency: ${bad}`);
  const mode=process.argv[2]??'--all';
  const commands=mode==='--release'?[['build',['--release']]]:mode==='--check'?[['check',[]]]:[['fmt',['--','--check']],['clippy',['--all-targets','--','-D','warnings']],['test',['--all-targets']],['build',[]]];
  for(const [cmd,args] of commands){console.log(`Parquet ${cmd}: sockets denied`);process.stdout.write(run('cargo',['+1.97.1',cmd,...manifest,...(cmd==='fmt'?[]:['--locked','--offline']),...args]));}
  console.log('Parquet dependency identity/network-denied gates PASS');
}finally{rmSync(scratch,{recursive:true,force:true});}
