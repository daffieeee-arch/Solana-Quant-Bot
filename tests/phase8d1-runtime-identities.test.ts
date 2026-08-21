import { describe, expect, it } from 'vitest';
import { canonicalizeIdentityRecords, validateRuntimeIdentityEvidence } from '../scripts/phase8d1/validate-runtime-identities.mjs';
import { readFileSync } from 'node:fs';

const valid=()=>JSON.parse(readFileSync('deployment/phase8d1/runtime-identities.json','utf8'));

describe('Phase 8D1 canonical runtime identity evidence',()=>{
  it('accepts stable complete collision-free evidence',()=>expect(validateRuntimeIdentityEvidence(valid())).toEqual([]));
  it.each([
    ['TrueNAS user UID',(x:any)=>x.collisionChecks.trueNasUserUidAbsent=false],
    ['local passwd UID',(x:any)=>x.collisionChecks.localPasswdUidAbsent=false],
    ['TrueNAS group GID',(x:any)=>x.collisionChecks.trueNasGroupGidAbsent=false],
    ['local group GID',(x:any)=>x.collisionChecks.localGroupGidAbsent=false],
    ['app nested ID',(x:any)=>x.collisionChecks.appNestedSelectedIdAbsent=false],
    ['dataset mount owner',(x:any)=>x.collisionChecks.datasetMountOwnerSelectedIdAbsent=false],
    ['dataset ACL owner',(x:any)=>x.collisionChecks.datasetAclSelectedIdAbsent=false],
  ])('rejects selected ID present only in %s',(_name,mutate)=>{const x:any=valid();mutate(x);expect(validateRuntimeIdentityEvidence(x).join('\n')).toMatch(/collision/i)});
  it('rejects a missing or unreadable source',()=>{const x:any=valid();x.requiredSources.datasetAcls=false;expect(validateRuntimeIdentityEvidence(x).join('\n')).toMatch(/source/i)});
  it('rejects unstable consecutive canonical audits',()=>{const x:any=valid();x.audits[1].canonicalInventorySha256='0'.repeat(64);expect(validateRuntimeIdentityEvidence(x).join('\n')).toMatch(/stable/i)});
  it('rejects unexplained identity drift',()=>{const x:any=valid();x.drift.unknownCount=1;x.drift.allDifferencesExplained=false;expect(validateRuntimeIdentityEvidence(x).join('\n')).toMatch(/drift/i)});
  it('accepts a legitimate extra non-conflicting owner as an observation count change',()=>{
    const base=[{sourceClass:'STAT',objectType:'owner',objectName:'a',dataset:'a',mountpoint:'/mnt/a',uid:568,gid:568,appName:null,path:'/mnt/a'}];
    const more=[...base,{sourceClass:'STAT',objectType:'owner',objectName:'b',dataset:'b',mountpoint:'/mnt/b',uid:1000,gid:1000,appName:null,path:'/mnt/b'}];
    const a=canonicalizeIdentityRecords(base),b=canonicalizeIdentityRecords(more);expect(b.canonicalRecordCount).toBe(a.canonicalRecordCount+1);expect(b.selectedIdsPresent).toEqual([]);
  });
  it('deduplicates only exact duplicate source records',()=>{const row={sourceClass:'STAT',objectType:'owner',objectName:'a',dataset:'a',mountpoint:'/mnt/a',uid:568,gid:568,appName:null,path:'/mnt/a'};const x=canonicalizeIdentityRecords([row,{...row}]);expect(x.rawRecordCount).toBe(2);expect(x.canonicalRecordCount).toBe(1)});
  it('is insensitive to object property order while retaining semantic fields',()=>{const a:any={sourceClass:'STAT',objectType:'owner',objectName:'a',dataset:'a',mountpoint:'/mnt/a',uid:568,gid:568,appName:null,path:'/mnt/a'};const b:any={path:'/mnt/a',appName:null,gid:568,uid:568,mountpoint:'/mnt/a',dataset:'a',objectName:'a',objectType:'owner',sourceClass:'STAT'};expect(canonicalizeIdentityRecords([a]).sha256).toBe(canonicalizeIdentityRecords([b]).sha256)});
  it('requires Phase 8D2 live recheck and can never be applied/deployed',()=>{for(const mutate of [(x:any)=>x.invariants.phase8d2LiveRecheckRequired=false,(x:any)=>x.status='APPLIED',(x:any)=>x.status='DEPLOYED']){const x:any=valid();mutate(x);expect(validateRuntimeIdentityEvidence(x)).not.toEqual([])}});
  it('rejects forged source anchors, source SHA, counts and reversed audit ordering',()=>{
    for(const mutate of [
      (x:any)=>{delete x.sourceEvidence;},
      (x:any)=>{x.sourceEvidence.audit1.sha256='0'.repeat(64);},
      (x:any)=>{x.sourceEvidence.audit2.path='/tmp/forged.json';},
      (x:any)=>{x.sourceGitSha='f'.repeat(40);},
      (x:any)=>{x.observedSourceCounts.datasetMountOwners=0;},
      (x:any)=>{x.audits[0].rawRecordCount=0;},
      (x:any)=>{[x.audits[0].observedAt,x.audits[1].observedAt]=[x.audits[1].observedAt,x.audits[0].observedAt];},
    ]){const x:any=valid();mutate(x);expect(validateRuntimeIdentityEvidence(x).join('\n')).not.toBe('');}
  });
  it('rejects unknown top-level and nested evidence properties',()=>{for(const mutate of [(x:any)=>{x.extra=true;},(x:any)=>{x.audits[0].extra=true;},(x:any)=>{x.drift.extra=true;}]){const x:any=valid();mutate(x);expect(validateRuntimeIdentityEvidence(x).join('\n')).toMatch(/key|shape/i)}});
});
