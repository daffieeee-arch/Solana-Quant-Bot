#!/usr/bin/env node
// CI-only, isolated existing DuckDB dependency. No action download or system install.
import { readdirSync, existsSync, readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';

export function selectPython313(versions){
  return versions.filter(v=>/^3\.13\.\d+$/u.test(v)).sort((a,b)=>Number(b.split('.')[2])-Number(a.split('.')[2]))[0]??null;
}
export function validateQueryLock(bytes){
  if(createHash('sha256').update(bytes).digest('hex')!=='70f60feae3f4dd54ddea610b6054aae1f267295671e31287ac3f3c7d2a5dcff2')throw Error('unreviewed DuckDB-only lock: no additional wheel, index or requirements directive permitted');
}
function main(){
  validateQueryLock(readFileSync(resolve('research/columnar-query/requirements.lock')));
  if(process.env.GITHUB_ACTIONS!=='true'||!process.env.RUNNER_TOOL_CACHE||!process.env.RUNNER_TEMP)throw Error('CI runner/toolcache required; local users select their existing isolated interpreter');
  const base=join(process.env.RUNNER_TOOL_CACHE,'Python');
  const version=selectPython313(readdirSync(base));
  if(!version)throw Error('preinstalled CPython 3.13 ABI missing: do not download another toolchain or skip DuckDB');
  const python=join(base,version,'x64/bin/python3');
  const venv=join(process.env.RUNNER_TEMP,'solana-quant-columnar-query');
  if(existsSync(venv))throw Error('fresh isolated query environment required');
  const run=(exe,args)=>{const r=spawnSync(exe,args,{stdio:'inherit',timeout:300000});if(r.error||r.status!==0)throw Error(r.error?.message??'isolated query preparation failed');};
  run(python,['-c','import sys,platform; assert sys.version_info[:2]==(3,13) and platform.machine()=="x86_64" and platform.python_implementation()=="CPython"; print(sys.version)']);
  run(python,['-m','venv',venv]);
  run(join(venv,'bin/python'),['-m','pip','--isolated','--disable-pip-version-check','install','--index-url','https://pypi.org/simple','--only-binary=:all:','--no-deps','--require-hashes','-r',resolve('research/columnar-query/requirements.lock')]);
}
if(process.argv[1]&&pathToFileURL(resolve(process.argv[1])).href===import.meta.url){try{main();}catch(e){console.error(e.message);process.exitCode=1;}}
