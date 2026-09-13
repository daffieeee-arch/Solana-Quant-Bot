#!/usr/bin/env node
// Offline physical projection only. No registry access, provider or acquisition.
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { readFileSync, readdirSync, mkdtempSync, rmSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { pathToFileURL } from 'node:url';
import { buildResearchSeccompLauncher } from './build-research-seccomp-launcher.mjs';
import { writeResearchNetworkDenyFilter } from './write-research-seccomp-filter.mjs';
import { validateQueryLock } from './prepare-columnar-query-ci.mjs';

const root=resolve(import.meta.dirname,'..'),crate=join(root,'rust/of1-parquet-projection');
const hash=b=>createHash('sha256').update(b).digest('hex');
export function validateParquetInputs(manifest,lock,reviewed,sources){
  const errors=[];
  if(hash(manifest)!==reviewed.manifest_sha256)errors.push('unreviewed Parquet manifest');
  if(hash(lock)!==reviewed.lock_sha256)errors.push('unreviewed Parquet lock');
  for(const [name,source]of Object.entries(sources))if(name==='build.rs'||/std::net|TcpStream|TcpListener|UdpSocket|std::process::Command|Command::new|unsafe\s*\{/u.test(source))errors.push(`unexpected Parquet runtime capability: ${name}`);
  for(const name of ['object_store','reqwest','hyper','tokio','solana-rpc-client','openssl','zstd-sys','lz4-sys'])if(String(lock).includes(`name = "${name}"`))errors.push(`forbidden Parquet dependency: ${name}`);
  return errors;
}
function sources(at,prefix=''){
  const result={};for(const entry of readdirSync(at,{withFileTypes:true})){
    if(entry.name==='target')continue;
    if(entry.isSymbolicLink())throw Error('review symlink in Parquet crate');
    const path=join(at,entry.name),name=prefix+entry.name;
    if(entry.isDirectory())Object.assign(result,sources(path,name+'/'));
    else if(name.endsWith('.rs'))result[name]=readFileSync(path,'utf8');
  }return result;
}
async function gate(mode){
if(!['--static','--all','--release','--check'].includes(mode))throw Error('usage: --static | --all | --release | --check');
validateQueryLock(readFileSync(join(root,'research/columnar-query/requirements.lock')));
const reviewed=JSON.parse(readFileSync(join(crate,'dependency-review.json'),'utf8'));
const errors=validateParquetInputs(readFileSync(join(crate,'Cargo.toml')),readFileSync(join(crate,'Cargo.lock')),reviewed,sources(crate));
if(errors.length)throw Error(errors.join('\n'));
if(mode==='--static'){console.log('Parquet manifest/lock/source PASS (no subprocess or fetch)');return;}
const scratch=mkdtempSync(join(tmpdir(),'of1-parquet-offline-'));
try {
  const launcher=join(scratch,'launcher'),filter=join(scratch,'network-deny.bpf');
  await writeResearchNetworkDenyFilter(filter,process.arch,{allowLocalProcessSpawn:true});
  await buildResearchSeccompLauncher(launcher);
  const run=(command,args)=>{
    const r=spawnSync(launcher,[filter,command,...args],{cwd:root,encoding:'utf8',maxBuffer:32*1024*1024,timeout:900_000,env:{...process.env,CARGO_NET_OFFLINE:'true',PYTHONDONTWRITEBYTECODE:'1',COLUMNAR_TEST_FIXTURE_DIR:join(scratch,'fixtures')}});
    if(r.error||r.status!==0)throw Error(r.error?.message||r.stderr||r.stdout||'offline gate failed');return r.stdout+r.stderr;
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
  const commands=mode==='--release'?[['build',['--release']]]:mode==='--check'?[['check',[]]]:[['fmt',['--','--check']],['clippy',['--all-targets','--','-D','warnings']],['test',['--all-targets']],['build',[]]];
  for(const [cmd,args] of commands){console.log(`Parquet ${cmd}: sockets denied`);process.stdout.write(run('cargo',['+1.97.1',cmd,...manifest,...(cmd==='fmt'?[]:['--locked','--offline']),...args]));}
  if(mode==='--all'){
    const python=process.env.COLUMNAR_QUERY_PYTHON??(process.env.RUNNER_TEMP?join(process.env.RUNNER_TEMP,'solana-quant-columnar-query/bin/python'):null);
    if(!python)throw Error('COLUMNAR_QUERY_PYTHON required: DuckDB must not be silently skipped');
    process.stdout.write(run(python,[join(root,'research/columnar-query/test_query.py'),join(scratch,'fixtures')]));
    process.stdout.write(run(python,[join(root,'research/columnar-query/test_coverage.py')]));
    process.stdout.write(run(python,[join(root,'research/columnar-query/test_manifest.py'),join(scratch,'fixtures')]));
    console.log('DuckDB real Parquet and coverage regressions PASS (sockets denied)');
  }
  console.log('Parquet dependency identity/network-denied gates PASS');
}finally{rmSync(scratch,{recursive:true,force:true});}
}
if(process.argv[1]&&pathToFileURL(resolve(process.argv[1])).href===import.meta.url){
  if(process.argv.length!==3){console.error('one explicit gate mode required');process.exitCode=1;}
  else gate(process.argv[2]).catch(e=>{console.error(e.message);process.exitCode=1;});
}
