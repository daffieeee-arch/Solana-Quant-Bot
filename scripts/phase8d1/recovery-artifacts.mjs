const MAX_BYTES=1_048_576;
const SAFE_EXTENSIONS=new Set(['.json','.txt','.sha256']);
const credentialPatterns=[/-----BEGIN\s+(?:RSA |DSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----/i,/(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9_]{20,})/i,/\b(?:TOKEN|PASSWORD|SECRET|API_KEY)=[^\s\x00]{8,}/i];
const unique=errors=>[...new Set(errors)].sort();

export function validateOriginalArtifactMetadata(actual,contract){
  const errors=[];
  if(!Array.isArray(actual)||actual.length!==contract?.originalArtifacts?.length)return ['ARTIFACT_SET_MISMATCH'];
  for(const expected of contract.originalArtifacts){
    const found=actual.find(entry=>entry?.name===expected.name);
    if(!found){errors.push(`ARTIFACT_MISSING:${expected.role}`);continue;}
    if(found.id!==expected.id)errors.push(`ARTIFACT_ID_MISMATCH:${expected.role}`);
    if(found.size_in_bytes!==expected.bytes)errors.push(`ARTIFACT_SIZE_MISMATCH:${expected.role}`);
    if(found.digest!==`sha256:${expected.sha256}`)errors.push(`ARTIFACT_DIGEST_MISMATCH:${expected.role}`);
    if(found.expired!==false)errors.push(`ARTIFACT_EXPIRED:${expected.role}`);
    if(found.workflow_run?.id!==contract.originalPublishRunId)errors.push(`ARTIFACT_RUN_MISMATCH:${expected.role}`);
    if(found.workflow_run?.head_sha!==contract.imageSourceSha)errors.push(`ARTIFACT_SOURCE_MISMATCH:${expected.role}`);
  }
  return unique(errors);
}

export function validateArtifactEntries(entries){
  const errors=[];
  if(!Array.isArray(entries)||entries.length<1)return ['ARTIFACT_ENTRY_SET_INVALID'];
  let total=0;
  for(const entry of entries){
    const path=String(entry?.path??''),bytes=Buffer.from(entry?.bytes??[]);total+=bytes.length;
    const parts=path.split(/[\\/]+/);
    if(!path||path.startsWith('/')||parts.includes('..')||parts.some(part=>part===''))errors.push('ARTIFACT_PATH_UNSAFE');
    const lower=path.toLowerCase(),dot=lower.lastIndexOf('.'),extension=dot>=0?lower.slice(dot):'';
    if(!SAFE_EXTENSIONS.has(extension)||/\.(?:tar|tgz|gz|zst|img|layer|dockerbuild)$/.test(lower))errors.push('ARTIFACT_IMAGE_PAYLOAD_FORBIDDEN');
    if(bytes.length>MAX_BYTES)errors.push('ARTIFACT_BYTES_EXCEEDED');
    const text=bytes.toString('utf8');if(credentialPatterns.some(pattern=>pattern.test(text)))errors.push('ARTIFACT_CREDENTIAL_CONTENT');
  }
  if(total>MAX_BYTES)errors.push('ARTIFACT_BYTES_EXCEEDED');
  return unique(errors);
}

function findImage(state,name){return Array.isArray(state?.images)?state.images.find(image=>image?.name===name):undefined;}
export function validateOriginalStateChain(states,contract){
  const errors=[],source=contract.imageSourceSha,cockpit=contract.images.find(image=>image.name==='cockpit'),runner=contract.images.find(image=>image.name==='runner');
  const expectedVerdicts={preflight:'PRE_PUSH_READY',cockpit:'PARTIAL_PUBLISH_HOLD',runner:'BOTH_PUSHED_RETEST_REQUIRED_HOLD',final:'BOTH_PUSHED_RETEST_REQUIRED_HOLD'};
  for(const [stage,verdict] of Object.entries(expectedVerdicts)){
    const state=states?.[stage];
    if(!state){errors.push(`ARTIFACT_STATE_MISSING:${stage}`);continue;}
    if(state.sourceGitSha!==source||state.releaseId!==source)errors.push(`ARTIFACT_SOURCE_MISMATCH:${stage}`);
    if(state.verdict!==verdict)errors.push(`ARTIFACT_VERDICT_MISMATCH:${stage}`);
    for(const item of [cockpit,runner]){
      const image=findImage(state,item.name);if(!image){errors.push(`ARTIFACT_IMAGE_MISSING:${stage}:${item.name}`);continue;}
      if(image.package!==item.package||image.tag!==item.tag)errors.push(`ARTIFACT_TAG_MISMATCH:${stage}:${item.name}`);
    }
  }
  const preCockpit=findImage(states?.preflight,'cockpit'),preRunner=findImage(states?.preflight,'runner');
  for(const [name,image] of [['cockpit',preCockpit],['runner',preRunner]])if(image?.preflightStatus!=='ABSENT'||image?.pushStatus!=='NOT_ATTEMPTED'||image?.registryDigest!==null)errors.push(`ARTIFACT_PREFLIGHT_MISMATCH:${name}`);
  const cockpitStageCockpit=findImage(states?.cockpit,'cockpit'),cockpitStageRunner=findImage(states?.cockpit,'runner');
  if(cockpitStageCockpit?.pushStatus!=='PUSHED'||cockpitStageCockpit?.registryDigest!==cockpit.digest)errors.push('ARTIFACT_DIGEST_MISMATCH:cockpit:cockpit');
  if(cockpitStageRunner?.pushStatus!=='NOT_ATTEMPTED'||cockpitStageRunner?.registryDigest!==null)errors.push('ARTIFACT_PUSH_STATUS_MISMATCH:cockpit:runner');
  for(const stage of ['runner','final'])for(const item of [cockpit,runner]){const image=findImage(states?.[stage],item.name);if(image?.pushStatus!=='PUSHED')errors.push(`ARTIFACT_PUSH_STATUS_MISMATCH:${stage}:${item.name}`);if(image?.registryDigest!==item.digest)errors.push(`ARTIFACT_DIGEST_MISMATCH:${stage}:${item.name}`);}
  if(states?.verify?.sourceGitSha!==source||states?.verify?.status!=='VERIFY_ONLY_SUCCEEDED')errors.push('ARTIFACT_VERIFY_SUPPORT_MISMATCH');
  return unique(errors);
}
