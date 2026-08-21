#!/usr/bin/env node
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';

const FIELDS=['sourceClass','objectType','objectName','dataset','mountpoint','uid','gid','appName','path'];
const SHA=/^[0-9a-f]{64}$/;
const GIT=/^[0-9a-f]{40}$/;
const EXPECTED={
  sourceGitSha:'6c597b4c2c274583a4fdd9ba7a24db44c21df5a8',
  canonicalInventorySha256:'16bfd98e15fd625ab948a4759cffbe75af2163c79b9111fd81dcfd3d71c25867',
  audit1:{observedAt:'2026-08-21T20:21:26.795330+00:00',path:'/opt/data/research-scratch/phase8d1-identity-rebind/final-audit-1/evidence.json',sha256:'86f5fd880fe000183e937bf1bc2369c563c21eb317cc2d3342810922231c91a6'},
  audit2:{observedAt:'2026-08-21T20:22:08.064264+00:00',path:'/opt/data/research-scratch/phase8d1-identity-rebind/final-audit-2/evidence.json',sha256:'61b861751036d6b08edca80dc070883accf3355d40d8aa3f46b50b52428e811c'},
  canonicalInventoryPath:'/opt/data/research-scratch/phase8d1-identity-rebind/final-audit-1/canonical-inventory.jsonl',
  driftSemanticSha256:'3a7bd799a3463f66f761da51372deec2ef7c47995c25a8a2bff209a6fc718906',
};
const TOP_KEYS=['audits','collisionChecks','drift','invariants','mutations','observedSourceCounts','requiredSources','schemaVersion','selected','sourceEvidence','sourceGitSha','status'];
const AUDIT_KEYS=['allSourcesComplete','canonicalInventorySha256','canonicalRecordCount','datasetMountOwnerRecordCount','observedAt','rawRecordCount'];
const COLLISION_KEYS=['activeContainerSelectedIdAbsent','appNestedSelectedIdAbsent','datasetAclSelectedIdAbsent','datasetMountOwnerSelectedIdAbsent','gid61000Absent','localGroupGidAbsent','localPasswdUidAbsent','trueNasGroupGidAbsent','trueNasUserUidAbsent','uid61000Absent','uid61001Absent'];
const DRIFT_KEYS=['addedRecordCount','allDifferencesExplained','cause','changedOwnershipCount','classification','differenceFile','differenceFileSha256','netCountDelta','oldSampleCount','precommitSampleCount','removedRecordCount','unknownCount'];
const COUNT_KEYS=['appConfigs','apps','canonicalRecords','datasetAclReads','datasetMountOwners','datasets','datasetsWithoutAcl','localGroup','localPasswd','rawRecords','trueNasGroups','trueNasUsers'];
const SOURCE_KEYS=['appConfigs','apps','datasetAcls','datasetMountStats','datasets','grafanaKnownIdentity','localGroup','localPasswd','trueNasGroups','trueNasUsers'];
const MUTATION_KEYS=['accountsCreated','aclChanged','appCreated','datasetCreated','groupsCreated','ownershipChanged'];
const INVARIANT_KEYS=['phase8d2LiveRecheckRequired','rawCountsAreObservations','selectedCollisionAlwaysBlocks','stableCanonicalAuditsRequired','unexplainedDriftBlocks'];
const exactKeys=(value,keys)=>value&&typeof value==='object'&&!Array.isArray(value)&&JSON.stringify(Object.keys(value).sort())===JSON.stringify([...keys].sort());
const stableValue=value=>Array.isArray(value)?value.map(stableValue):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,stableValue(value[key])])):value;
const stableSha=value=>createHash('sha256').update(JSON.stringify(stableValue(value))).digest('hex');

export function canonicalizeIdentityRecords(records){
  if(!Array.isArray(records))throw new Error('records_not_array');
  const lines=records.map(row=>JSON.stringify(Object.fromEntries(FIELDS.map(key=>[key,row?.[key]??null])),Object.keys(Object.fromEntries(FIELDS.map(key=>[key,null]))).sort())).sort();
  const unique=[...new Set(lines)];const bytes=`${unique.join('\n')}\n`;
  const selectedIdsPresent=[];for(const line of unique){const row=JSON.parse(line);for(const [kind,value] of [['uid',row.uid],['gid',row.gid]])if(value===61000||value===61001)selectedIdsPresent.push({kind,value,sourceClass:row.sourceClass,path:row.path});}
  return{rawRecordCount:records.length,canonicalRecordCount:unique.length,duplicateExactRecordCount:records.length-unique.length,sha256:createHash('sha256').update(bytes).digest('hex'),bytes,selectedIdsPresent};
}

export function validateRuntimeIdentityDrift(value){
  const e=[];
  const top=['addedRecordCount','allDifferencesExplained','cause','changedOwnershipCount','differences','duplicateSourceRecordCount','netCountDelta','oldSampleCount','precommitSampleCount','removedRecordCount','schemaVersion','stableCanonicalInventorySha256','stableDatasetMountOwnerCount'];
  if(!exactKeys(value,top))e.push('drift_shape_invalid');
  if(value?.schemaVersion!=='PHASE8D1_IDENTITY_DRIFT_DIFF_1'||value.oldSampleCount!==55||value.precommitSampleCount!==59||value.netCountDelta!==4||value.addedRecordCount!==13||value.removedRecordCount!==9||value.changedOwnershipCount!==0||value.duplicateSourceRecordCount!==0||value.stableDatasetMountOwnerCount!==87||value.stableCanonicalInventorySha256!==EXPECTED.canonicalInventorySha256||value.allDifferencesExplained!==true)e.push('drift_summary_invalid');
  if(!Array.isArray(value?.differences)||value.differences.length!==22)e.push('drift_entries_invalid');
  else{
    let added=0,removed=0;
    for(const row of value.differences){
      if(!exactKeys(row,['classification','dataset','direction','gid','mountpoint','reason','source','uid']))e.push('drift_entry_shape_invalid');
      if(row.classification!=='SOURCE_REPRESENTATION_CHANGE'||row.source!=='LEGACY_PER_MOUNT_SINGLE_WSS_HANDSHAKE_SAMPLE'||row.reason!=='Record exists with identical owner in complete stable batch inventory'||typeof row.dataset!=='string'||typeof row.mountpoint!=='string'||!row.mountpoint.startsWith('/mnt/')||!Number.isSafeInteger(row.uid)||!Number.isSafeInteger(row.gid))e.push('drift_entry_semantics_invalid');
      if(row.direction==='ADDED_TO_59_SAMPLE')added++;else if(row.direction==='REMOVED_FROM_59_SAMPLE')removed++;else e.push('drift_entry_direction_invalid');
    }
    if(added!==13||removed!==9)e.push('drift_entry_counts_invalid');
  }
  if(stableSha(value)!==EXPECTED.driftSemanticSha256)e.push('drift_semantic_anchor_mismatch');
  return[...new Set(e)].sort();
}

export function validateRuntimeIdentityEvidence(x){
  const e=[];
  if(!x||typeof x!=='object'||Array.isArray(x))return['identity_evidence_not_object'];
  if(!exactKeys(x,TOP_KEYS))e.push('identity_top_level_key_shape_invalid');
  if(x.schemaVersion!=='PHASE8D1_RUNTIME_IDENTITIES_2')e.push('invalid_identity_schema');
  if(x.status!=='SELECTED_READ_ONLY_NOT_APPLIED')e.push('identity_status_must_never_be_applied_or_deployed');
  if(!GIT.test(x.sourceGitSha??'')||x.sourceGitSha!==EXPECTED.sourceGitSha)e.push('identity_source_git_sha_invalid');
  if(!exactKeys(x.selected,['cockpitUid','fixtureReadGid','runnerUid'])||x.selected?.runnerUid!==61000||x.selected?.cockpitUid!==61001||x.selected?.fixtureReadGid!==61000)e.push('selected_identity_mismatch');
  if(!Array.isArray(x.audits)||x.audits.length!==2)e.push('stable_audits_required');
  else{
    for(const audit of x.audits){
      if(!exactKeys(audit,AUDIT_KEYS))e.push('stable_audit_key_shape_invalid');
      if(audit.allSourcesComplete!==true||audit.canonicalInventorySha256!==EXPECTED.canonicalInventorySha256||audit.rawRecordCount!==621||audit.canonicalRecordCount!==621||audit.datasetMountOwnerRecordCount!==87)e.push('stable_audit_incomplete_or_unanchored');
    }
    if(x.audits[0].observedAt!==EXPECTED.audit1.observedAt||x.audits[1].observedAt!==EXPECTED.audit2.observedAt)e.push('stable_audit_timestamp_anchor_mismatch');
    const first=Date.parse(x.audits[0].observedAt),second=Date.parse(x.audits[1].observedAt);
    if(!Number.isFinite(first)||!Number.isFinite(second)||second-first<30000)e.push('stable_audit_timestamp_order_invalid');
    if(x.audits[0].canonicalInventorySha256!==x.audits[1].canonicalInventorySha256)e.push('stable_audit_mismatch');
  }
  if(!exactKeys(x.requiredSources,SOURCE_KEYS)||SOURCE_KEYS.some(key=>x.requiredSources?.[key]!==true))e.push('required_identity_source_missing_or_unreadable');
  if(!exactKeys(x.collisionChecks,COLLISION_KEYS)||COLLISION_KEYS.some(key=>x.collisionChecks?.[key]!==true))e.push('selected_identity_collision');
  if(!exactKeys(x.observedSourceCounts,COUNT_KEYS))e.push('observed_source_count_shape_invalid');
  const counts={trueNasUsers:63,trueNasGroups:97,localPasswd:19,localGroup:40,apps:15,appConfigs:15,datasets:88,datasetMountOwners:87,datasetAclReads:56,datasetsWithoutAcl:31,rawRecords:621,canonicalRecords:621};
  if(COUNT_KEYS.some(key=>x.observedSourceCounts?.[key]!==counts[key]))e.push('observed_source_count_anchor_mismatch');
  if(!exactKeys(x.drift,DRIFT_KEYS))e.push('identity_drift_key_shape_invalid');
  if(!x.drift||x.drift.oldSampleCount!==55||x.drift.precommitSampleCount!==59||x.drift.netCountDelta!==4||x.drift.addedRecordCount!==13||x.drift.removedRecordCount!==9||x.drift.changedOwnershipCount!==0||x.drift.unknownCount!==0||x.drift.allDifferencesExplained!==true||x.drift.classification!=='SOURCE_REPRESENTATION_CHANGE'||x.drift.differenceFile!=='deployment/phase8d1/runtime-identity-drift-55-to-59.json'||x.drift.differenceFileSha256!=='8721cad73098cab2c7fc7680c1b0faf8d8246a25066dd0c8db3daa76cac699f2')e.push('unexplained_identity_drift');
  if(!exactKeys(x.invariants,INVARIANT_KEYS)||INVARIANT_KEYS.some(key=>x.invariants?.[key]!==true))e.push('identity_invariant_missing');
  if(!exactKeys(x.mutations,MUTATION_KEYS)||MUTATION_KEYS.some(key=>x.mutations?.[key]!==false))e.push('identity_evidence_must_not_apply_mutations');
  if(!exactKeys(x.sourceEvidence,['audit1','audit2','canonicalInventoryPath','canonicalInventorySha256'])||!exactKeys(x.sourceEvidence?.audit1,['path','sha256'])||!exactKeys(x.sourceEvidence?.audit2,['path','sha256']))e.push('source_evidence_shape_invalid');
  else if(x.sourceEvidence.canonicalInventoryPath!==EXPECTED.canonicalInventoryPath||x.sourceEvidence.canonicalInventorySha256!==EXPECTED.canonicalInventorySha256||x.sourceEvidence.audit1.path!==EXPECTED.audit1.path||x.sourceEvidence.audit1.sha256!==EXPECTED.audit1.sha256||x.sourceEvidence.audit2.path!==EXPECTED.audit2.path||x.sourceEvidence.audit2.sha256!==EXPECTED.audit2.sha256)e.push('source_evidence_anchor_mismatch');
  return[...new Set(e)].sort();
}

if(import.meta.url===`file://${process.argv[1]}`){const x=JSON.parse(readFileSync(process.argv[2]??'deployment/phase8d1/runtime-identities.json','utf8'));const e=validateRuntimeIdentityEvidence(x);if(e.length){console.error(e.join('\n'));process.exit(1)}console.log('Phase 8D1 runtime identity evidence PASS');}
