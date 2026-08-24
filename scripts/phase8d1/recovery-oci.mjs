import { createHash } from 'node:crypto';

const PROVENANCE_TYPE='https://slsa.dev/provenance/v1';
const SPDX_TYPE='https://spdx.dev/Document';
const SPDX_ID=/^SPDXRef-[A-Za-z0-9.-]+$/;
const DIGEST=/^sha256:[0-9a-f]{64}$/;
const SHA=/^[0-9a-f]{40}$/;
const EXPECTED_BUILDER_ID='https://github.com/daffieeee-arch/solana-paper-scanner/actions/runs/32641496527';
const OCI_MANIFEST='application/vnd.oci.image.manifest.v1+json';
const IN_TOTO_LAYER='application/vnd.in-toto+json';
const ATTESTATION_ARTIFACT='application/vnd.docker.attestation.manifest.v1+json';
const EMPTY_CONFIG={mediaType:'application/vnd.oci.empty.v1+json',digest:'sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a',size:2};

const rejected=reason=>Object.freeze({accepted:false,format:null,referenceDigest:null,reason});

export function evaluateAttestationManifestBinding(input){
  const {descriptor,manifest,linuxDescriptor}=input??{};
  if(!descriptor||descriptor.mediaType!==OCI_MANIFEST||descriptor.platform?.os!=='unknown'||descriptor.platform?.architecture!=='unknown')return rejected('ATTESTATION_DESCRIPTOR_INVALID');
  if(!linuxDescriptor||linuxDescriptor.mediaType!==OCI_MANIFEST||!DIGEST.test(linuxDescriptor.digest??'')||!Number.isSafeInteger(linuxDescriptor.size)||linuxDescriptor.size<1)return rejected('ATTESTATION_TARGET_INVALID');
  if(descriptor.annotations?.['vnd.docker.reference.type']!=='attestation-manifest')return rejected('ATTESTATION_REFERENCE_TYPE_MISMATCH');
  if(!manifest||manifest.mediaType!==OCI_MANIFEST||!Array.isArray(manifest.layers))return rejected('ATTESTATION_MANIFEST_INVALID');
  const annotationDigest=descriptor.annotations?.['vnd.docker.reference.digest']??null;
  const hasArtifactType=manifest.artifactType!==undefined&&manifest.artifactType!==null&&manifest.artifactType!=='';
  const hasSubject=manifest.subject!==undefined&&manifest.subject!==null;
  if(!hasArtifactType&&!hasSubject){
    if(annotationDigest!==linuxDescriptor.digest)return rejected('ATTESTATION_MANIFEST_LINK_MISMATCH');
    return Object.freeze({accepted:true,format:'legacy',referenceDigest:linuxDescriptor.digest,reason:null});
  }
  if(manifest.artifactType!==ATTESTATION_ARTIFACT||!hasSubject)return rejected('ATTESTATION_ARTIFACT_SHAPE_MISMATCH');
  const subject=manifest.subject;
  if(subject.mediaType!==linuxDescriptor.mediaType||subject.digest!==linuxDescriptor.digest||subject.size!==linuxDescriptor.size)return rejected('ATTESTATION_SUBJECT_MISMATCH');
  if(annotationDigest!==null&&annotationDigest!==linuxDescriptor.digest)return rejected('ATTESTATION_MANIFEST_LINK_MISMATCH');
  const config=manifest.config;if(!config||config.mediaType!==EMPTY_CONFIG.mediaType||config.digest!==EMPTY_CONFIG.digest||config.size!==EMPTY_CONFIG.size)return rejected('ATTESTATION_CONFIG_MISMATCH');
  return Object.freeze({accepted:true,format:'oci-artifact',referenceDigest:linuxDescriptor.digest,reason:null});
}

export function verifyAttestationLayerBytes(layer,status,bytes){
  return Boolean(layer&&layer.mediaType===IN_TOTO_LAYER&&status===200&&Buffer.isBuffer(bytes)&&Number.isSafeInteger(layer.size)&&layer.size===bytes.length&&DIGEST.test(layer.digest??'')&&`sha256:${createHash('sha256').update(bytes).digest('hex')}`===layer.digest);
}

function bindsSubject(payload,linuxManifestDigest){
  const expected=linuxManifestDigest.replace(/^sha256:/,'');
  if(!Array.isArray(payload?.subject)||payload.subject.length<1)return false;
  const digests=payload.subject.map(subject=>subject?.digest?.sha256);
  return digests.every(value=>typeof value==='string'&&value===expected)&&new Set(digests).size===1;
}
function isStatement(payload,predicateType){return /^https:\/\/in-toto\.io\/Statement\/v(?:0\.1|1)$/.test(payload?._type??'')&&payload?.predicateType===predicateType&&payload?.predicate&&typeof payload.predicate==='object';}
function exactSource(payload,imageSourceSha){
  const args=payload?.predicate?.buildDefinition?.externalParameters?.request?.args;
  return SHA.test(imageSourceSha??'')&&payload?.predicate?.runDetails?.builder?.id===EXPECTED_BUILDER_ID&&args&&typeof args==='object'&&args['build-arg:SOURCE_GIT_SHA']===imageSourceSha;
}
function describedPackages(value,packageIds){
  const direct=Array.isArray(value.documentDescribes)?value.documentDescribes:null;
  if(direct&&direct.some(id=>typeof id!=='string'||!packageIds.has(id)))return null;
  const relationshipIds=new Set();
  for(const relationship of value.relationships??[]){
    if(relationship?.spdxElementId==='SPDXRef-DOCUMENT'&&relationship?.relationshipType==='DESCRIBES'&&typeof relationship?.relatedSpdxElement==='string')relationshipIds.add(relationship.relatedSpdxElement);
  }
  if([...relationshipIds].some(id=>!packageIds.has(id)))return null;
  const directSet=direct?new Set(direct):null;
  if(directSet&&relationshipIds.size>0&&(directSet.size!==relationshipIds.size||[...directSet].some(id=>!relationshipIds.has(id))))return null;
  const selected=directSet??relationshipIds;
  return selected.size>0?selected:null;
}
function isSpdx(payload){
  const value=payload?.predicate;
  if(!value||!['SPDX-2.2','SPDX-2.3'].includes(value.spdxVersion)||value.SPDXID!=='SPDXRef-DOCUMENT'||value.dataLicense!=='CC0-1.0'||typeof value.name!=='string'||!value.name||typeof value.documentNamespace!=='string'||!/^https?:\/\//.test(value.documentNamespace)||!Array.isArray(value.creationInfo?.creators)||value.creationInfo.creators.length<1||value.creationInfo.creators.some(entry=>typeof entry!=='string'||!entry)||!Number.isFinite(Date.parse(value.creationInfo?.created??''))||!Array.isArray(value.packages)||value.packages.length<1||(value.files!==undefined&&!Array.isArray(value.files))||(value.snippets!==undefined&&!Array.isArray(value.snippets))||(value.documentDescribes!==undefined&&!Array.isArray(value.documentDescribes))||(value.relationships!==undefined&&!Array.isArray(value.relationships)))return false;
  const elementArrays=[value.packages,value.files??[],value.snippets??[]],elementIds=[value.SPDXID,...elementArrays.flatMap(entries=>entries.map(entry=>entry?.SPDXID))];
  if(elementIds.some(id=>typeof id!=='string'||!SPDX_ID.test(id))||new Set(elementIds).size!==elementIds.length)return false;
  const known=new Set(elementIds);
  for(const relationship of value.relationships??[]){
    if(!relationship||typeof relationship.relationshipType!=='string'||!relationship.relationshipType||typeof relationship.spdxElementId!=='string'||typeof relationship.relatedSpdxElement!=='string'||!known.has(relationship.spdxElementId)||!known.has(relationship.relatedSpdxElement))return false;
  }
  const packageIds=new Set(value.packages.map(entry=>entry.SPDXID));
  if(!describedPackages(value,packageIds))return false;
  return value.packages.every(entry=>typeof entry?.name==='string'&&entry.name&&typeof entry?.downloadLocation==='string'&&typeof entry?.filesAnalyzed==='boolean');
}

export function evaluateAttestationEvidence(value){
  const {linuxManifestDigest,imageSourceSha,records}=value??{};
  const recordList=Array.isArray(records)?records:[];
  if(!DIGEST.test(linuxManifestDigest??'')||!SHA.test(imageSourceSha??'')||recordList.length!==2)return Object.freeze({provenancePresent:false,spdxSbomPresent:false,provenanceSourceShaPresent:false});
  const valid=[];
  for(const record of recordList){
    if(record?.referenceDigest!==linuxManifestDigest||!['legacy','oci-artifact'].includes(record?.manifestFormat)||record?.layerMediaType!==IN_TOTO_LAYER||!DIGEST.test(record?.layerDigest??'')||record?.layerDigestVerified!==true||record?.blobStatus!==200||!record.payload||!bindsSubject(record.payload,linuxManifestDigest)||!isStatement(record.payload,record.predicateType)||![PROVENANCE_TYPE,SPDX_TYPE].includes(record.predicateType))return Object.freeze({provenancePresent:false,spdxSbomPresent:false,provenanceSourceShaPresent:false});
    valid.push(record);
  }
  const provenance=valid.filter(record=>record.predicateType===PROVENANCE_TYPE),spdx=valid.filter(record=>record.predicateType===SPDX_TYPE);
  if(provenance.length!==1||spdx.length!==1||!exactSource(provenance[0].payload,imageSourceSha)||!isSpdx(spdx[0].payload))return Object.freeze({provenancePresent:false,spdxSbomPresent:false,provenanceSourceShaPresent:false});
  return Object.freeze({provenancePresent:true,spdxSbomPresent:true,provenanceSourceShaPresent:true});
}
