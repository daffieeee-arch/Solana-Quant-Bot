#!/usr/bin/env node
// The existing syscall-denial gate applied to one new, isolated decoder crate.
// No acquisition execution, fixture server, extra permissions or generic framework.
import { createCiPhaseTimer } from './lib/ci-phase-timing.mjs';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { readFileSync, readdirSync, mkdtempSync, rmSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { pathToFileURL } from 'node:url';
import { buildResearchSeccompLauncher } from './build-research-seccomp-launcher.mjs';
import { writeResearchNetworkDenyFilter } from './write-research-seccomp-filter.mjs';

const root=resolve(import.meta.dirname,'..');
const crate=join(root,'rust/of1-bronze-decoder');
const hash=b=>createHash('sha256').update(b).digest('hex');
const review=()=>JSON.parse(readFileSync(join(crate,'dependency-review.json'),'utf8'));

export function validateBronzeInputs(manifest,lock,reviewed,sources){
  const errors=[];
  if(hash(manifest)!==reviewed.manifest_sha256)errors.push('unreviewed Bronze manifest');
  if(hash(lock)!==reviewed.lock_sha256)errors.push('unreviewed Bronze lock');
  for(const [name,source] of Object.entries(sources)){
    if(name==='build.rs'||/std::net|TcpStream|TcpListener|UdpSocket|std::process::Command|Command::new|unsafe\s*\{/u.test(source))errors.push(`unexpected Bronze runtime capability: ${name}`);
  }
  for(const name of ['solana-rpc-client','solana-signer','reqwest','hyper','tokio','rocksdb','openssl'])if(String(lock).includes(`name = "${name}"`))errors.push(`forbidden Bronze dependency: ${name}`);
  return errors;
}
function sources(at,prefix=''){
  const result={};for(const entry of readdirSync(at,{withFileTypes:true})){
    if(entry.name==='target')continue;
    if(entry.isSymbolicLink())throw Error('review symlink in Bronze crate');
    const path=join(at,entry.name),name=prefix+entry.name;
    if(entry.isDirectory())Object.assign(result,sources(path,name+'/'));
    else if(name.endsWith('.rs'))result[name]=readFileSync(path,'utf8');
  }return result;
}
async function run(mode){
  const reviewed=review();const errors=validateBronzeInputs(readFileSync(join(crate,'Cargo.toml')),readFileSync(join(crate,'Cargo.lock')),reviewed,sources(crate));
  if(errors.length)throw Error(errors.join('\n'));
  if(mode==='--static'){console.log('Bronze manifest/lock/source PASS');return;}
  if(mode!=='--all')throw Error('usage: --static | --all');
  const scratch=mkdtempSync(join(tmpdir(),'of1-bronze-offline-'));
  const launcher=join(scratch,'launcher'),filter=join(scratch,'network-deny.bpf');
  const timing=createCiPhaseTimer();
  let passed=false;
  const isolated=(command,args)=>timing.measure(`bronze.${command==='cargo'?args[1]:'probe'}`,()=>{
    const r=spawnSync(launcher,[filter,command,...args],{cwd:root,encoding:'utf8',maxBuffer:32*1024*1024,timeout:900_000,env:{...process.env,CARGO_NET_OFFLINE:'true'}});
    if(r.error||r.status!==0)throw Error(r.error?.message||r.stderr||r.stdout||'isolated Bronze command failed');return r.stdout;
  });
  try{
    await writeResearchNetworkDenyFilter(filter,process.arch,{allowLocalProcessSpawn:true});await buildResearchSeccompLauncher(launcher);
    const probe=isolated(process.execPath,['--input-type=module','-e',"import net from 'node:net';const s=net.createConnection({host:'127.0.0.1',port:9});s.on('connect',()=>process.exit(2));s.on('error',e=>{if(e.code==='EPERM')console.log('NETWORK_DENIED');else process.exit(3);});"]);
    if(probe!=='NETWORK_DENIED\n')throw Error('Bronze network-denial probe failed');
    const manifest=['--manifest-path',join(crate,'Cargo.toml')];
    const m=JSON.parse(isolated('cargo',['+1.97.1','metadata',...manifest,'--locked','--offline','--format-version','1']));
    const identities=new Map(m.packages.map(p=>[p.id,`${p.name}@${p.version}`]));
    const packages=m.packages.map(p=>({name:p.name,version:p.version,license:p.license,buildScript:p.targets.some(t=>t.kind.includes('custom-build'))})).sort((a,b)=>`${a.name}@${a.version}`.localeCompare(`${b.name}@${b.version}`));
    const features=m.resolve.nodes.map(n=>({package:identities.get(n.id),features:n.features})).sort((a,b)=>a.package.localeCompare(b.package));
    if(JSON.stringify(packages)!==JSON.stringify(reviewed.packages)||JSON.stringify(features)!==JSON.stringify(reviewed.features))throw Error('Bronze dependency/license/features drift');
    for(const entry of reviewed.build_scripts){const p=m.packages.find(p=>`${p.name}@${p.version}`===entry.package);const t=p?.targets.find(t=>t.kind.includes('custom-build'));if(!t||hash(readFileSync(t.src_path))!==entry.sha256)throw Error('Bronze build-script drift: '+entry.package);}
    for(const [command,args] of [
      ['fmt',[...manifest,'--','--check']],['clippy',[...manifest,'--locked','--offline','--all-targets','--','-D','warnings']],['test',[...manifest,'--locked','--offline','--all-targets']],['build',[...manifest,'--locked','--offline']],
    ]){console.log(`Bronze ${command} (sockets denied)`);process.stdout.write(isolated('cargo',['+1.97.1',command,...(['test','build'].includes(command)?['--profile','ci-test']:[]),...args]));}
    console.log('Bronze offline graph/fmt/clippy/tests/build PASS');
    passed=true;
  }finally{rmSync(scratch,{recursive:true,force:true});timing.finish({passed,summaryPath:process.env.GITHUB_STEP_SUMMARY});}
}
if(process.argv[1]&&pathToFileURL(resolve(process.argv[1])).href===import.meta.url)run(process.argv[2]).catch(e=>{console.error(e.message);process.exitCode=1;});
