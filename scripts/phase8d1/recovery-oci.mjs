function bindsSubject(payload,linuxManifestDigest){
  const expected=linuxManifestDigest.replace(/^sha256:/,'');return Array.isArray(payload?.subject)&&payload.subject.some(subject=>subject?.digest?.sha256===expected);
}
function isStatement(payload,predicateType){return /^https:\/\/in-toto\.io\/Statement\/v(?:0\.1|1)$/.test(payload?._type??'')&&payload?.predicateType===predicateType&&payload?.predicate&&typeof payload.predicate==='object';}
function exactSource(payload,imageSourceSha){
  const definition=payload?.predicate?.buildDefinition,external=definition?.externalParameters,invocation=payload?.predicate?.invocation?.parameters,values=[external?.['build-args']?.SOURCE_GIT_SHA,external?.buildArgs?.SOURCE_GIT_SHA,external?.['build-arg:SOURCE_GIT_SHA'],invocation?.['build-args']?.SOURCE_GIT_SHA,invocation?.buildArgs?.SOURCE_GIT_SHA,invocation?.['build-arg:SOURCE_GIT_SHA']];
  return definition&&typeof definition==='object'&&payload?.predicate?.runDetails?.builder?.id&&values.some(value=>value===imageSourceSha);
}
function isSpdx(payload){
  const value=payload?.predicate;if(!value||!/^SPDX-\d+\.\d+$/.test(value.spdxVersion??'')||value.SPDXID!=='SPDXRef-DOCUMENT'||value.dataLicense!=='CC0-1.0'||typeof value.name!=='string'||!value.name||typeof value.documentNamespace!=='string'||!/^https?:\/\//.test(value.documentNamespace)||!Array.isArray(value.creationInfo?.creators)||value.creationInfo.creators.length<1||value.creationInfo.creators.some(entry=>typeof entry!=='string'||!entry)||!Number.isFinite(Date.parse(value.creationInfo?.created??''))||!Array.isArray(value.documentDescribes)||value.documentDescribes.length<1||value.documentDescribes.some(id=>typeof id!=='string'||!SPDX_ID.test(id))||!Array.isArray(value.packages)||value.packages.length<1)return false;
  const elementArrays=[value.packages,value.files??[],value.snippets??[]],elementIds=[value.SPDXID,...elementArrays.flatMap(entries=>entries.map(entry=>entry?.SPDXID))];if(elementIds.some(id=>typeof id!=='string'||!SPDX_ID.test(id))||new Set(elementIds).size!==elementIds.length)return false;const packageIds=new Set(value.packages.map(entry=>entry.SPDXID));return value.documentDescribes.every(id=>packageIds.has(id))&&value.packages.every(entry=>typeof entry?.name==='string'&&entry.name&&typeof entry?.downloadLocation==='string'&&typeof entry?.filesAnalyzed==='boolean');
}

export function evaluateAttestationEvidence({linuxManifestDigest,imageSourceSha,records}){
  let provenancePresent=false,spdxSbomPresent=false,provenanceSourceShaPresent=false;
  for(const record of Array.isArray(records)?records:[]){
    if(record?.referenceDigest!==linuxManifestDigest||record?.blobStatus!==200||!record.payload||!bindsSubject(record.payload,linuxManifestDigest)||!isStatement(record.payload,record.predicateType))continue;
    if(record.predicateType===PROVENANCE_TYPE){provenancePresent=true;if(exactSource(record.payload,imageSourceSha))provenanceSourceShaPresent=true;}
    if(record.predicateType===SPDX_TYPE&&isSpdx(record.payload))spdxSbomPresent=true;
  }
  return Object.freeze({provenancePresent,spdxSbomPresent,provenanceSourceShaPresent});
}
const PROVENANCE_TYPE='https://slsa.dev/provenance/v1';
const SPDX_TYPE='https://spdx.dev/Document';
const SPDX_ID=/^SPDXRef-[A-Za-z0-9.-]+$/;
