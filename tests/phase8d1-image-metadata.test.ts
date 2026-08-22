import { describe, expect, it } from 'vitest';
import { validateImageMetadata } from '../scripts/phase8d1/validate-image-metadata.mjs';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';

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
    const body='A'.repeat(96),canary=`${begin}\n${body}\n${end}\n`;
    const result=run('block',canary);expect(result.status).not.toBe(0);expect(`${result.stdout}${result.stderr}`).not.toContain(body);expect(result.stderr).toContain('private_key');
  });
});
