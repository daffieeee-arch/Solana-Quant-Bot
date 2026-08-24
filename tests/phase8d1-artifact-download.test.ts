import { describe, expect, it } from 'vitest';
import { downloadGitHubArtifactZip } from '../scripts/phase8d1/recovery-http.mjs';

const headerBag=(values:Record<string,string>)=>({get:(name:string)=>Object.entries(values).find(([key])=>key.toLowerCase()===name.toLowerCase())?.[1]??null});

describe('Phase 8D1-R GitHub artifact two-hop download',()=>{
  it('uses authenticated GitHub API negotiation then a credential-free signed download',async()=>{
    const token='test-token-never-forward',signed='https://signed-download.example.invalid:443/a/../artifact.zip?sig=a%2fb',zip=Uint8Array.from([0x50,0x4b,0x03,0x04]);
    const calls:Array<{url:string;options:any}>=[];
    const fetchImpl=async(url:string,options:any)=>{
      calls.push({url,options});
      if(calls.length===1)return {status:302,headers:headerBag({location:signed}),arrayBuffer:async()=>{throw new Error('FIRST_HOP_BODY_MUST_NOT_BE_READ');}};
      return {status:200,headers:headerBag({}),arrayBuffer:async()=>zip.buffer};
    };
    const result=await downloadGitHubArtifactZip({repository:'owner/repo',artifactId:9493911850,role:'preflight',token,deadlineMs:Date.now()+1_000,fetchImpl});
    expect([...result]).toEqual([...zip]);expect(calls).toHaveLength(2);
    expect(calls[0].url).toBe('https://api.github.com/repos/owner/repo/actions/artifacts/9493911850/zip');
    expect(calls[0].options).toMatchObject({method:'GET',redirect:'manual'});
    expect(calls[0].options.headers).toEqual({Authorization:`Bearer ${token}`,Accept:'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'phase8d1-existing-digest-recovery'});
    expect(calls[1].url).toBe(signed);expect(calls[1].options).toMatchObject({method:'GET',redirect:'follow'});
    expect(calls[1].options.headers).toEqual({'User-Agent':'phase8d1-existing-digest-recovery'});
    expect(JSON.stringify(calls[1].options)).not.toContain(token);
  });
  it('rejects missing or unsafe signed locations before the second hop',async()=>{
    const invoke=async(location:string|null)=>{let calls=0;const fetchImpl:any=async()=>{calls++;return {status:302,headers:headerBag(location===null?{}:{location}),arrayBuffer:async()=>new ArrayBuffer(0)};};const promise=downloadGitHubArtifactZip({repository:'owner/repo',artifactId:9493911850,role:'preflight',token:'token',deadlineMs:Date.now()+1_000,fetchImpl});return {promise,calls:()=>calls};};
    const missing=await invoke(null);await expect(missing.promise).rejects.toThrow('ARTIFACT_DOWNLOAD_LOCATION_MISSING_PREFLIGHT');expect(missing.calls()).toBe(1);
    for(const location of ['/relative.zip','http://download.example/artifact.zip','https://user:pass@download.example/artifact.zip','https://download.example/artifact.zip#fragment','https://download.example/artifact.zip#']){const attempt=await invoke(location);await expect(attempt.promise).rejects.toThrow('ARTIFACT_DOWNLOAD_LOCATION_INVALID_PREFLIGHT');expect(attempt.calls(),location).toBe(1);}
  });
  it('emits readable closed-role HTTP reasons and rejects unexpected redirect outcomes',async()=>{
    const base={repository:'owner/repo',artifactId:9493911850,role:'preflight',token:'token',deadlineMs:Date.now()+1_000};
    const firstHop=async(status:number)=>downloadGitHubArtifactZip({...base,fetchImpl:async()=>({status,headers:headerBag({}),arrayBuffer:async()=>{throw new Error('BODY_MUST_NOT_BE_READ');}}) as any});
    await expect(firstHop(415)).rejects.toThrow('ARTIFACT_DOWNLOAD_HTTP_PREFLIGHT_415');
    await expect(firstHop(301)).rejects.toThrow('ARTIFACT_DOWNLOAD_HTTP_PREFLIGHT_301');
    await expect(firstHop(307)).rejects.toThrow('ARTIFACT_DOWNLOAD_HTTP_PREFLIGHT_307');
    for(const status of [503,302]){let calls=0;const fetchImpl:any=async()=>{calls++;return calls===1?{status:302,headers:headerBag({location:'https://signed.example/artifact.zip'})}:{status,headers:headerBag({location:'https://signed.example/artifact.zip'}),arrayBuffer:async()=>new ArrayBuffer(0)};};await expect(downloadGitHubArtifactZip({...base,fetchImpl})).rejects.toThrow(`ARTIFACT_DOWNLOAD_SIGNED_HTTP_PREFLIGHT_${status}`);expect(calls).toBe(2);}
    await expect(downloadGitHubArtifactZip({...base,role:'unknown-role',fetchImpl:async()=>{throw new Error('MUST_NOT_FETCH');}} as any)).rejects.toThrow('ARTIFACT_DOWNLOAD_ROLE_INVALID');
  });
  it('normalizes signed-download redirect exhaustion to a closed role reason',async()=>{
    let calls=0;const fetchImpl:any=async()=>{calls++;if(calls===1)return {status:302,headers:headerBag({location:'https://signed.example/artifact.zip'})};const error=new TypeError('fetch failed');(error as any).cause=new Error('redirect count exceeded');throw error;};
    await expect(downloadGitHubArtifactZip({repository:'owner/repo',artifactId:9493911850,role:'preflight',token:'token',deadlineMs:Date.now()+1_000,fetchImpl})).rejects.toThrow('ARTIFACT_DOWNLOAD_SIGNED_REDIRECT_HOLD_PREFLIGHT');expect(calls).toBe(2);
  });
});
