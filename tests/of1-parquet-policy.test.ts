import { readFileSync, mkdtempSync, writeFileSync, truncateSync, rmSync, symlinkSync, unlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
// @ts-expect-error Reviewed local ESM gate.
import { validateParquetInputs } from '../scripts/assert-of1-parquet-offline.mjs';
// @ts-expect-error Reviewed local ESM CI prerequisite selector.
import { selectPython313, validateQueryLock } from '../scripts/prepare-columnar-query-ci.mjs';
// @ts-expect-error Local read-only viewer validation; import does not listen.
import { MAX_REPORT_BYTES, validateReportDirectory } from '../research/columnar-query/serve.mjs';
const base=new URL('../rust/of1-parquet-projection/',import.meta.url);
const manifest=readFileSync(new URL('Cargo.toml',base));
const lock=readFileSync(new URL('Cargo.lock',base));
const review=JSON.parse(readFileSync(new URL('dependency-review.json',base),'utf8'));
describe('Parquet dependency and mandatory reader gate',()=>{
  it('admits the measured multi-shard report without truncation but retains an exact file cap',()=>{
    const root=mkdtempSync(join(tmpdir(),'columnar-viewer-test-'));
    try{
      for(const name of ['index.html','query-results.json','query-execution.json'])writeFileSync(join(root,name),'fixture');
      const result=join(root,'query-results.json');
      truncateSync(result,3307557);expect(validateReportDirectory(root)).toBe(root);
      expect(MAX_REPORT_BYTES).toBe(8*1024*1024);
      truncateSync(result,MAX_REPORT_BYTES);expect(validateReportDirectory(root)).toBe(root);
      truncateSync(result,MAX_REPORT_BYTES+1);expect(()=>validateReportDirectory(root)).toThrow('bounded regular');
      unlinkSync(result);symlinkSync(join(root,'index.html'),result);expect(()=>validateReportDirectory(root)).toThrow('bounded regular');
    }finally{rmSync(root,{recursive:true,force:true});}
  });
  it('accepts reviewed identities',()=>expect(validateParquetInputs(manifest,lock,review,{})).toEqual([]));
  it('rejects changed manifest/lock before fetch',()=>{
    expect(validateParquetInputs(Buffer.concat([manifest,Buffer.from('\n')]),lock,review,{})).toContain('unreviewed Parquet manifest');
    expect(validateParquetInputs(manifest,Buffer.concat([lock,Buffer.from('\n')]),review,{})).toContain('unreviewed Parquet lock');
  });
  it('rejects runtime/source hooks',()=>{
    for(const source of [{'src/main.rs':'std::net::TcpStream'},{'build.rs':'fn main(){}'},{'src/lib.rs':'Command::new("curl")'}])expect(validateParquetInputs(manifest,lock,review,source).join(' ')).toContain('runtime capability');
  });
  it('requires existing CPython 3.13 ABI, not Ubuntu default or invented action pin',()=>{
    expect(selectPython313(['3.12.9','3.14.2'])).toBeNull();
    expect(selectPython313(['3.13.9','3.13.15','3.13.16-rc1','3.14.0'])).toBe('3.13.15');
  });
  it('static gate works without Cargo/compiler and unknown mode fails before subprocess',()=>{
    const gate=fileURLToPath(new URL('../scripts/assert-of1-parquet-offline.mjs',import.meta.url));
    const run=(mode:string)=>spawnSync(process.execPath,[gate,mode],{encoding:'utf8',env:{...process.env,PATH:'/nonexistent'}});
    expect(run('--static').status).toBe(0);
    const invalid=run('--skip');
    expect(invalid.status).toBe(1);
    expect(invalid.stderr).toContain('usage:');
  });
  it('pins the sole reviewed DuckDB wheel before any CI install',()=>{
    const bytes=readFileSync(new URL('../research/columnar-query/requirements.lock',import.meta.url));
    expect(()=>validateQueryLock(bytes)).not.toThrow();
    for(const extra of ['another-package==1 --hash=sha256:abc','--extra-index-url https://invalid.example','-r injected.txt'])expect(()=>validateQueryLock(Buffer.concat([bytes,Buffer.from(extra)]))).toThrow('unreviewed DuckDB-only lock');
  });
});
