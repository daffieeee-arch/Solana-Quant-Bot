import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
// @ts-expect-error Reviewed local ESM gate, no declaration required.
import { validateBronzeInputs } from '../scripts/assert-of1-bronze-offline.mjs';

const crate=new URL('../rust/of1-bronze-decoder/',import.meta.url);
const manifest=readFileSync(new URL('Cargo.toml',crate));
const lock=readFileSync(new URL('Cargo.lock',crate));
const review=JSON.parse(readFileSync(new URL('dependency-review.json',crate),'utf8'));

describe('bounded offline Bronze dependency gate',()=>{
  it('accepts the reviewed locked graph inputs',()=>expect(validateBronzeInputs(manifest,lock,review,{})).toEqual([]));
  it('rejects changed manifest and lock before fetch',()=>{
    expect(validateBronzeInputs(Buffer.concat([manifest,Buffer.from('\n')]),lock,review,{})).toContain('unreviewed Bronze manifest');
    expect(validateBronzeInputs(manifest,Buffer.concat([lock,Buffer.from('\n')]),review,{})).toContain('unreviewed Bronze lock');
  });
  it('rejects source network/process/build hooks',()=>{
    for(const sources of [{'src/lib.rs':'use std::net::TcpStream;'}, {'src/main.rs':'Command::new("curl")'},{'build.rs':'fn main() {}'}])expect(validateBronzeInputs(manifest,lock,review,sources).join(' ')).toContain('unexpected Bronze runtime capability');
  });
});
