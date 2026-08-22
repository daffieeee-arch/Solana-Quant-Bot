import { afterEach, describe, expect, it } from 'vitest';
import { cp, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';

const roots: string[] = [];
afterEach(async () => { while (roots.length) await rm(roots.pop()!, { recursive: true, force: true }); });

async function clonePolicyFiles() {
  const root = await mkdtemp(join(tmpdir(), 'phase8d1-policy-entrypoint-')); roots.push(root);
  for (const path of [
    '.github/workflows/ci.yml', '.github/workflows/phase8d-images-verify.yml', '.github/workflows/phase8d-images-publish.yml',
    'deployment/phase8d1/runtime-identities.json', 'deployment/phase8d1/runtime-identity-drift-55-to-59.json', 'deployment/phase8d1/remote-build-contract.json', 'deployment/phase8d1/base-image-lock.json', 'deployment/phase8d1/release-manifest.schema.json', 'deployment/phase8d1/cockpit-egress-deny-seccomp.json', 'deployment/phase8d1/expected-fixture-files.sha256', 'deployment/phase8d1/rootfs-public-key-test-vectors.json',
    'containers/Dockerfile.cockpit', 'containers/Dockerfile.phase8a-runner',
    'scripts/phase8d1/resolve-base-images.mjs', 'scripts/phase8d1/prebuild-hashes.sh', 'scripts/phase8d1/verify-images.sh', 'scripts/phase8d1/inventory-rootfs.py', 'scripts/phase8d1/validate-image-metadata.mjs', 'scripts/phase8d1/publish-gates.sh', 'scripts/phase8d1/verify-published-images.sh', 'scripts/phase8d1/json-schema-subset.mjs', 'scripts/phase8d1/validate-release-manifest.mjs', 'scripts/phase8d1/validate-runtime-identities.mjs',
  ]) { const target=join(root,path); await cp(path,target,{recursive:true}); }
  for(const args of [['init','-q'],['add','--all']]){
    const result=spawnSync('git',args,{cwd:root,encoding:'utf8'});
    if(result.status!==0)throw new Error(`git ${args.join(' ')} failed: ${result.stderr}`);
  }
  return root;
}
async function rejected(path: string, mutate: (text: string) => string) {
  const root=await clonePolicyFiles(); const target=join(root,path); await writeFile(target,mutate(await readFile(target,'utf8')));
  const contractPath=join(root,'deployment/phase8d1/remote-build-contract.json');
  const contract=JSON.parse(await readFile(contractPath,'utf8'));
  const digest=createHash('sha256').update(await readFile(target)).digest('hex');
  if (path.endsWith('phase8d-images-verify.yml')) contract.workflowSha256.verify=digest;
  else if (path.endsWith('phase8d-images-publish.yml')) contract.workflowSha256.publish=digest;
  else if (contract.supplyChainFileSha256[path]) contract.supplyChainFileSha256[path]=digest;
  await writeFile(contractPath,`${JSON.stringify(contract,null,2)}\n`);
  const result=spawnSync(process.execPath,[resolve('scripts/phase8d1/assert-phase8d1-supply-chain.mjs')],{cwd:root,encoding:'utf8'});
  expect(result.status, `${result.stdout}\n${result.stderr}`).not.toBe(0);
}

describe('Phase 8D1 production policy rejects real workflow file bypasses', () => {
  it('accepts the exact copied positive-control files', async () => {
    const root=await clonePolicyFiles();
    const result=spawnSync(process.execPath,[resolve('scripts/phase8d1/assert-phase8d1-supply-chain.mjs')],{cwd:root,encoding:'utf8'});
    expect(result.status, `${result.stdout}\n${result.stderr}`).toBe(0);
  });
  it('rejects verify-job permission/runner/push/cache/secret/action/checkout/command drift', async () => {
    const cases: Array<(text:string)=>string> = [
      text=>text.replace('    runs-on: ubuntu-24.04','    permissions: write-all\n    runs-on: ubuntu-24.04'),
      text=>text.replace('    runs-on: ubuntu-24.04','    runs-on: self-hosted'),
      text=>text.replace('          push: false','          push: \'true\''),
      text=>text.replace('          push: false','          push: false\n          cache-to: type=registry,ref=ghcr.io/example/cache'),
      text=>text.replace('      - name: Set up isolated Buildx','      - name: leak\n        run: echo "${{ secrets[\'DEPLOY_TOKEN\'] }} ${{ github.token }}"\n      - name: Set up isolated Buildx'),
      text=>text.replace('      - name: Set up isolated Buildx','      - { uses: actions/checkout@v4 }\n      - name: Set up isolated Buildx'),
      text=>text.replace('          ref: ${{ github.event.pull_request.head.sha || github.sha }}','          ref: ${{ github.sha }} # github.event.pull_request.head.sha || github.sha'),
      text=>text.replace('      - name: Set up isolated Buildx','      - name: push anyway\n        run: docker push ghcr.io/example/image:tag\n      - name: Set up isolated Buildx'),
    ];
    for (const mutate of cases) await rejected('.github/workflows/phase8d-images-verify.yml', mutate);
  });
  it('rejects any additional publish job with write/identity/secret/unpinned/push capability', async () => {
    const injected = '\n  bypass:\n    runs-on: ubuntu-latest\n    permissions:\n      packages: write\n      id-token: write\n    steps:\n      - { uses: actions/checkout@v4 }\n      - run: echo "${{ secrets.DEPLOY_TOKEN }}" && docker push ghcr.io/example/x:y\n';
    await rejected('.github/workflows/phase8d-images-publish.yml', text=>text+injected);
  });
  it('rejects credential-bearing image ENV and history mutations', async () => {
    await rejected('containers/Dockerfile.cockpit', text=>text.replace('ENV NODE_ENV=production','ENV NODE_ENV=production\nENV API_TOKEN=supersecretvalue'));
    await rejected('containers/Dockerfile.phase8a-runner', text=>text.replace('WORKDIR /src','WORKDIR /src\nRUN printf API_TOKEN=supersecretvalue'));
    await rejected('containers/Dockerfile.cockpit', text=>text.replace('ENV NODE_ENV=production','ENV NODE_ENV=production\nENV HARMLESS=https://user:password@example.invalid'));
    await rejected('containers/Dockerfile.phase8a-runner', text=>text.replace('WORKDIR /src','WORKDIR /src\nRUN printf HARMLESS=https://user:password@example.invalid'));
  });
  it('rejects rebound forged runtime evidence anchors/counts/source/timestamps', async () => {
    const cases: Array<(value:any)=>void>=[
      x=>{delete x.sourceEvidence;},
      x=>{x.sourceEvidence.audit1.sha256='0'.repeat(64);},
      x=>{x.observedSourceCounts.rawRecords=0;},
      x=>{x.sourceGitSha='f'.repeat(40);},
      x=>{[x.audits[0].observedAt,x.audits[1].observedAt]=[x.audits[1].observedAt,x.audits[0].observedAt];},
    ];
    for(const mutate of cases) await rejected('deployment/phase8d1/runtime-identities.json',text=>{const value=JSON.parse(text);mutate(value);return `${JSON.stringify(value,null,2)}\n`;});
  });
  it('rejects a rebound arbitrary replacement drift artifact',async()=>{
    const root=await clonePolicyFiles();
    const driftPath=join(root,'deployment/phase8d1/runtime-identity-drift-55-to-59.json');
    const identityPath=join(root,'deployment/phase8d1/runtime-identities.json');
    const contractPath=join(root,'deployment/phase8d1/remote-build-contract.json');
    const forged='{"schemaVersion":"FORGED_EMPTY_DRIFT"}\n';await writeFile(driftPath,forged);
    const driftSha=createHash('sha256').update(forged).digest('hex');
    const identity=JSON.parse(await readFile(identityPath,'utf8'));identity.drift.differenceFileSha256=driftSha;const identityText=`${JSON.stringify(identity,null,2)}\n`;await writeFile(identityPath,identityText);
    const contract=JSON.parse(await readFile(contractPath,'utf8'));contract.supplyChainFileSha256['deployment/phase8d1/runtime-identity-drift-55-to-59.json']=driftSha;contract.supplyChainFileSha256['deployment/phase8d1/runtime-identities.json']=createHash('sha256').update(identityText).digest('hex');await writeFile(contractPath,`${JSON.stringify(contract,null,2)}\n`);
    const result=spawnSync(process.execPath,[resolve('scripts/phase8d1/assert-phase8d1-supply-chain.mjs')],{cwd:root,encoding:'utf8'});expect(result.status,`${result.stdout}\n${result.stderr}`).not.toBe(0);
  });
  it('rejects a rebound public test-vector allowlist mutation',async()=>{
    await rejected('deployment/phase8d1/rootfs-public-key-test-vectors.json',text=>{const value=JSON.parse(text);value.entries[0].candidateSha256='0'.repeat(64);return `${JSON.stringify(value,null,2)}\n`;});
  });
});
