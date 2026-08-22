import { describe, expect, it } from 'vitest';
import { validateImageMetadata } from '../scripts/phase8d1/validate-image-metadata.mjs';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';

describe('Phase 8D1 image metadata credential boundary', () => {
  it('accepts ordinary runtime environment and history metadata', () => {
    expect(validateImageMetadata(['PATH=/usr/bin:/bin','NODE_ENV=production'], ['{"CreatedBy":"ENV NODE_ENV=production"}'])).toEqual([]);
  });
  it.each([
    [['HARMLESS=https://user:password@example.invalid'], []],
    [['API_TOKEN=supersecretvalue'], []],
    [[], ['{"CreatedBy":"ENV HARMLESS=https://user:password@example.invalid"}']],
    [[], ['{"CreatedBy":"RUN printf API_TOKEN=supersecretvalue"}']],
  ])('rejects credential-bearing Config.Env or history values', (env, history) => {
    expect(validateImageMetadata(env as string[], history as string[]).join('\n')).toMatch(/credential/i);
  });
  it('reports rootfs candidates with bounded redacted metadata and never prints the value', () => {
    const root=mkdtempSync(join(tmpdir(),'phase8d1-rootfs-redaction-'));
    try{
      const content=join(root,'content'),archive=join(root,'rootfs.tar'),output=join(root,'inventory.ndjson');mkdirSync(join(content,'app'),{recursive:true});
      const canary='CANARY_DO_NOT_PRINT_123456789';writeFileSync(join(content,'app','config.txt'),`API_TOKEN=${canary}\n`);
      expect(spawnSync('tar',['-cf',archive,'-C',content,'.']).status).toBe(0);
      const result=spawnSync('python3',['scripts/phase8d1/inventory-rootfs.py',archive,output],{encoding:'utf8'});
      expect(result.status).not.toBe(0);expect(`${result.stdout}${result.stderr}`).not.toContain(canary);
      expect(result.stderr).toContain('app/config.txt');expect(result.stderr).toContain('credential_assignment');expect(result.stderr).toMatch(/"length":\d+/);
    } finally { rmSync(root,{recursive:true,force:true}); }
  });
  it('allows private-key marker text but rejects a complete PEM private-key block', () => {
    const run=(name:string,value:string)=>{
      const root=mkdtempSync(join(tmpdir(),`phase8d1-pem-${name}-`));
      try{const content=join(root,'content'),archive=join(root,'rootfs.tar'),output=join(root,'inventory.ndjson');mkdirSync(join(content,'app'),{recursive:true});writeFileSync(join(content,'app','sample.txt'),value);expect(spawnSync('tar',['-cf',archive,'-C',content,'.']).status).toBe(0);return spawnSync('python3',['scripts/phase8d1/inventory-rootfs.py',archive,output],{encoding:'utf8'});}finally{rmSync(root,{recursive:true,force:true});}
    };
    const begin=['-----BEGIN','PRIVATE KEY-----'].join(' '),end=['-----END','PRIVATE KEY-----'].join(' ');
    expect(run('marker',`Documentation mentions ${begin} only.`).status).toBe(0);
    const invalidBody='A'.repeat(96),invalid=`${begin}\n${invalidBody}\n${end}\n`;
    expect(run('invalid-block',invalid).status).toBe(0);
    const der=Buffer.concat([Buffer.from([0x30,0x2e,0x02,0x01,0x00,0x30,0x05,0x06,0x03,0x2b,0x65,0x70,0x04,0x22,0x04,0x20]),Buffer.alloc(32)]),body=der.toString('base64'),canary=`${begin}\n${body}\n${end}\n`;
    const result=run('block',canary);expect(result.status).not.toBe(0);expect(`${result.stdout}${result.stderr}`).not.toContain(body);expect(result.stderr).toContain('private_key');
    for(const [name,decoy] of [['nonstructural',invalid],['invalid-base64',`${begin}\n${'!'.repeat(96)}\n${end}\n`]]){
      const afterDecoy=run(name,`${decoy}${canary}`);expect(afterDecoy.status).not.toBe(0);expect(`${afterDecoy.stdout}${afterDecoy.stderr}`).not.toContain(body);expect(afterDecoy.stderr).toContain('private_key');
    }
  });
  it('allows a public test vector only at its exact path and candidate hash', () => {
    const root=mkdtempSync(join(tmpdir(),'phase8d1-public-kat-'));
    try{
      const content=join(root,'content'),archive=join(root,'rootfs.tar'),output=join(root,'inventory.ndjson'),allowlist=join(root,'allowlist.json');mkdirSync(join(content,'app'),{recursive:true});
      const begin=['-----BEGIN','PRIVATE KEY-----'].join(' '),end=['-----END','PRIVATE KEY-----'].join(' '),der=Buffer.concat([Buffer.from([0x30,0x2e,0x02,0x01,0x00,0x30,0x05,0x06,0x03,0x2b,0x65,0x70,0x04,0x22,0x04,0x20]),Buffer.alloc(32)]),candidate=`${begin}\n${der.toString('base64')}\n${end}`,pem=`${candidate}\n`;writeFileSync(join(content,'app','sample.txt'),pem);expect(spawnSync('tar',['-cf',archive,'-C',content,'.']).status).toBe(0);
      const sha=createHash('sha256').update(candidate).digest('hex'),entry={path:'app/sample.txt',candidateSha256:sha,classification:'PUBLIC_GNUTLS_KAT_NOT_CREDENTIAL',sourceCommit:'ca61668d7764fc29fb4cc2aa396cb035e176636d',sourceUrl:'https://github.com/gnutls/gnutls/blob/ca61668d7764fc29fb4cc2aa396cb035e176636d/lib/crypto-selftests-pk.c',symbol:'synthetic_test'};writeFileSync(allowlist,JSON.stringify({schemaVersion:'PHASE8D1_PUBLIC_KEY_TEST_VECTOR_ALLOWLIST_1',entries:[entry]}));
      const accepted=spawnSync('python3',['scripts/phase8d1/inventory-rootfs.py','--allowlist',allowlist,archive,output],{encoding:'utf8'});expect(accepted.status,accepted.stderr).toBe(0);expect(accepted.stdout).toContain('"publicTestVectorAllowances": 1');
      entry.path='app/other.txt';writeFileSync(allowlist,JSON.stringify({schemaVersion:'PHASE8D1_PUBLIC_KEY_TEST_VECTOR_ALLOWLIST_1',entries:[entry]}));const rejected=spawnSync('python3',['scripts/phase8d1/inventory-rootfs.py','--allowlist',allowlist,archive,output],{encoding:'utf8'});expect(rejected.status).not.toBe(0);
    } finally { rmSync(root,{recursive:true,force:true}); }
  });
});
