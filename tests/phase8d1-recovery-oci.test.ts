import { describe, expect, it } from 'vitest';
import { createHash } from 'node:crypto';
import { evaluateAttestationEvidence, evaluateAttestationManifestBinding, verifyAttestationLayerBytes } from '../scripts/phase8d1/recovery-oci.mjs';

const digest=`sha256:${'a'.repeat(64)}`;
const wrongDigest=`sha256:${'b'.repeat(64)}`;
const mediaType='application/vnd.oci.image.manifest.v1+json';
const source='9ed8d5b8d8b67284c8fc20c164f6816bbfc0c180';
const root=(annotations:any={})=>({mediaType,platform:{os:'unknown',architecture:'unknown'},annotations});
const legacyAnnotations={'vnd.docker.reference.type':'attestation-manifest','vnd.docker.reference.digest':digest};
const emptyConfig={mediaType:'application/vnd.oci.empty.v1+json',digest:'sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a',size:2};
const ociManifest=(subjectDigest=digest)=>({mediaType,artifactType:'application/vnd.docker.attestation.manifest.v1+json',subject:{mediaType,digest:subjectDigest,size:123},config:emptyConfig,layers:[]});
const legacyManifest={mediaType,config:{mediaType:'application/vnd.oci.image.config.v1+json',digest:`sha256:${'c'.repeat(64)}`,size:100},layers:[]};
const statement=(predicateType:string,predicate:any,subjectDigest=digest)=>({_type:'https://in-toto.io/Statement/v1',predicateType,subject:[{name:'pkg:image',digest:{sha256:subjectDigest.slice(7)}}],predicate});
const record=(predicateType:string,payload:any,overrides:any={})=>({referenceDigest:digest,manifestFormat:'oci-artifact',layerMediaType:'application/vnd.in-toto+json',layerDigest:`sha256:${'d'.repeat(64)}`,layerDigestVerified:true,predicateType,blobStatus:200,payload,...overrides});
const provenance=()=>record('https://slsa.dev/provenance/v1',statement('https://slsa.dev/provenance/v1',{buildDefinition:{externalParameters:{request:{args:{'build-arg:SOURCE_GIT_SHA':source}}}},runDetails:{builder:{id:'https://github.com/daffieeee-arch/solana-paper-scanner/actions/runs/32641496527'}}}));
const spdxPredicate=()=>({spdxVersion:'SPDX-2.3',SPDXID:'SPDXRef-DOCUMENT',dataLicense:'CC0-1.0',name:'image-sbom',documentNamespace:'https://example.invalid/spdx/image',creationInfo:{created:'2026-08-23T00:00:00Z',creators:['Tool: syft']},packages:[{SPDXID:'SPDXRef-Package-image',name:'image',downloadLocation:'NOASSERTION',filesAnalyzed:false}],files:[{SPDXID:'SPDXRef-File-bin',fileName:'/bin/app'}],relationships:[{spdxElementId:'SPDXRef-DOCUMENT',relationshipType:'DESCRIBES',relatedSpdxElement:'SPDXRef-Package-image'},{spdxElementId:'SPDXRef-Package-image',relationshipType:'CONTAINS',relatedSpdxElement:'SPDXRef-File-bin'}]});
const spdx=()=>record('https://spdx.dev/Document',statement('https://spdx.dev/Document',spdxPredicate()));

describe('Phase 8D1-R attestation manifest target binding',()=>{
  it('returns a rejected binding for null or undefined input',()=>{
    for(const value of [null,undefined])expect(evaluateAttestationManifestBinding(value as any)).toMatchObject({accepted:false});
  });
  it('accepts exact legacy annotation binding',()=>{
    expect(evaluateAttestationManifestBinding({descriptor:root(legacyAnnotations),manifest:legacyManifest,linuxDescriptor:{mediaType,digest,size:123}})).toMatchObject({accepted:true,format:'legacy',referenceDigest:digest});
  });
  it('accepts exact OCI artifact subject binding with or without an equal legacy digest annotation',()=>{
    expect(evaluateAttestationManifestBinding({descriptor:root({'vnd.docker.reference.type':'attestation-manifest'}),manifest:ociManifest(),linuxDescriptor:{mediaType,digest,size:123}})).toMatchObject({accepted:true,format:'oci-artifact',referenceDigest:digest});
    expect(evaluateAttestationManifestBinding({descriptor:root(legacyAnnotations),manifest:ociManifest(),linuxDescriptor:{mediaType,digest,size:123}})).toMatchObject({accepted:true,format:'oci-artifact',referenceDigest:digest});
  });
  it('rejects conflicting, missing, or malformed manifest bindings',()=>{
    const cases=[
      {descriptor:root({...legacyAnnotations,'vnd.docker.reference.digest':wrongDigest}),manifest:ociManifest()},
      {descriptor:root(legacyAnnotations),manifest:{...ociManifest(),artifactType:'application/vnd.example.wrong'}},
      {descriptor:root(legacyAnnotations),manifest:ociManifest(wrongDigest)},
      {descriptor:root(legacyAnnotations),manifest:{...ociManifest(),subject:undefined}},
      {descriptor:root({'vnd.docker.reference.type':'attestation-manifest'}),manifest:legacyManifest},
      {descriptor:root({...legacyAnnotations,'vnd.docker.reference.type':'wrong'}),manifest:legacyManifest},
    ];
    for(const value of cases)expect(evaluateAttestationManifestBinding({...value,linuxDescriptor:{mediaType,digest,size:123}}).accepted).toBe(false);
  });
});

describe('Phase 8D1-R attestation payload validation',()=>{
  it('returns atomic all-false for null or undefined input',()=>{
    for(const value of [null,undefined])expect(evaluateAttestationEvidence(value as any)).toEqual({provenancePresent:false,spdxSbomPresent:false,provenanceSourceShaPresent:false});
  });
  it('verifies OCI layer digest prefixes, size, status, and media type from actual bytes',()=>{
    const bytes=Buffer.from('{"ok":true}'),layer={mediaType:'application/vnd.in-toto+json',digest:`sha256:${createHash('sha256').update(bytes).digest('hex')}`,size:bytes.length};
    expect(verifyAttestationLayerBytes(layer,200,bytes)).toBe(true);
    expect(verifyAttestationLayerBytes({...layer,digest:`sha256:${'0'.repeat(64)}`},200,bytes)).toBe(false);
    expect(verifyAttestationLayerBytes({...layer,size:bytes.length+1},200,bytes)).toBe(false);
    expect(verifyAttestationLayerBytes({...layer,mediaType:'application/octet-stream'},200,bytes)).toBe(false);
    expect(verifyAttestationLayerBytes(layer,404,bytes)).toBe(false);
  });
  it('accepts BuildKit SLSA v1 and Syft SPDX relationship structure for both images',()=>{
    const evidence=evaluateAttestationEvidence({linuxManifestDigest:digest,imageSourceSha:source,records:[provenance(),spdx()]});
    expect(evidence).toEqual({provenancePresent:true,spdxSbomPresent:true,provenanceSourceShaPresent:true});
  });
  it('rejects unknown layers, predicate mismatches, wrong subjects and wrong source SHA',()=>{
    expect(evaluateAttestationEvidence({linuxManifestDigest:digest,imageSourceSha:source,records:[record('https://slsa.dev/provenance/v1',provenance().payload,{layerMediaType:'application/octet-stream'})]}).provenancePresent).toBe(false);
    expect(evaluateAttestationEvidence({linuxManifestDigest:digest,imageSourceSha:source,records:[record('https://slsa.dev/provenance/v1',{...provenance().payload,predicateType:'https://spdx.dev/Document'})]}).provenancePresent).toBe(false);
    expect(evaluateAttestationEvidence({linuxManifestDigest:digest,imageSourceSha:source,records:[record('https://slsa.dev/provenance/v1',statement('https://slsa.dev/provenance/v1',provenance().payload.predicate,wrongDigest))]}).provenancePresent).toBe(false);
    expect(evaluateAttestationEvidence({linuxManifestDigest:digest,imageSourceSha:'f'.repeat(40),records:[provenance(),spdx()]})).toEqual({provenancePresent:false,spdxSbomPresent:false,provenanceSourceShaPresent:false});
  });
  it('rejects any extra unknown or invalid attestation record',()=>{
    const unknown=record('https://example.invalid/unknown',statement('https://example.invalid/unknown',{ok:true}));expect(evaluateAttestationEvidence({linuxManifestDigest:digest,imageSourceSha:source,records:[provenance(),spdx(),unknown]}).provenancePresent).toBe(false);
    const invalidDigest={...spdx(),layerDigestVerified:false};expect(evaluateAttestationEvidence({linuxManifestDigest:digest,imageSourceSha:source,records:[provenance(),spdx(),invalidDigest]}).spdxSbomPresent).toBe(false);
  });
  it('rejects duplicate or conflicting approved predicate layers',()=>{
    const duplicateProvenance=provenance(),conflictingProvenance=provenance();conflictingProvenance.payload.predicate.buildDefinition.externalParameters.request.args['build-arg:SOURCE_GIT_SHA']='f'.repeat(40);
    expect(evaluateAttestationEvidence({linuxManifestDigest:digest,imageSourceSha:source,records:[duplicateProvenance,conflictingProvenance,spdx()]}).provenancePresent).toBe(false);
    expect(evaluateAttestationEvidence({linuxManifestDigest:digest,imageSourceSha:source,records:[provenance(),spdx(),spdx()]}).spdxSbomPresent).toBe(false);
  });
  it('accepts documentDescribes-only SPDX and rejects unresolved relationship endpoints',()=>{
    const direct=spdx();direct.payload.predicate.documentDescribes=['SPDXRef-Package-image'];direct.payload.predicate.relationships=[];expect(evaluateAttestationEvidence({linuxManifestDigest:digest,imageSourceSha:source,records:[provenance(),direct]}).spdxSbomPresent).toBe(true);
    const unresolved=spdx();unresolved.payload.predicate.relationships.push({spdxElementId:'NONE',relationshipType:'CONTAINS',relatedSpdxElement:'SPDXRef-File-bin'});expect(evaluateAttestationEvidence({linuxManifestDigest:digest,imageSourceSha:source,records:[provenance(),unresolved]})).toEqual({provenancePresent:false,spdxSbomPresent:false,provenanceSourceShaPresent:false});
  });
  it('rejects malformed digests, sources, builders, SPDX versions and array shapes atomically',()=>{
    const cases:Array<{manifestDigest:string;sourceSha:string;records:any[]}>=[];
    const malformedLayer=provenance();malformedLayer.layerDigest='not-a-digest';cases.push({manifestDigest:digest,sourceSha:source,records:[malformedLayer,spdx()]});
    cases.push({manifestDigest:'not-a-digest',sourceSha:source,records:[provenance(),spdx()]},{manifestDigest:digest,sourceSha:'not-a-sha',records:[provenance(),spdx()]});
    const evilBuilder=provenance();evilBuilder.payload.predicate.runDetails.builder.id='https://evil.invalid/builder';cases.push({manifestDigest:digest,sourceSha:source,records:[evilBuilder,spdx()]});
    const badVersion=spdx();badVersion.payload.predicate.spdxVersion='SPDX-999.999';cases.push({manifestDigest:digest,sourceSha:source,records:[provenance(),badVersion]});
    const badFiles=spdx();badFiles.payload.predicate.files={};cases.push({manifestDigest:digest,sourceSha:source,records:[provenance(),badFiles]});
    const badDescribes=spdx();badDescribes.payload.predicate.documentDescribes={};cases.push({manifestDigest:digest,sourceSha:source,records:[provenance(),badDescribes]});
    for(const value of cases)expect(evaluateAttestationEvidence({linuxManifestDigest:value.manifestDigest,imageSourceSha:value.sourceSha,records:value.records})).toEqual({provenancePresent:false,spdxSbomPresent:false,provenanceSourceShaPresent:false});
  });
  it('rejects invalid SPDX and conflicting document description forms',()=>{
    const invalid=spdx();invalid.payload.predicate.packages[0].SPDXID='SPDXRef-DOCUMENT';expect(evaluateAttestationEvidence({linuxManifestDigest:digest,imageSourceSha:source,records:[invalid]}).spdxSbomPresent).toBe(false);
    const conflict=spdx();conflict.payload.predicate.documentDescribes=['SPDXRef-Package-other'];expect(evaluateAttestationEvidence({linuxManifestDigest:digest,imageSourceSha:source,records:[conflict]}).spdxSbomPresent).toBe(false);
  });
});
