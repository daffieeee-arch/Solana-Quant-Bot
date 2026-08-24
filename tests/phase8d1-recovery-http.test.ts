import { describe, expect, it } from 'vitest';
import { existsSync } from 'node:fs';

const path='../scripts/phase8d1/recovery-http.mjs';
const response=(status:number,body:any,link:string|null=null)=>({status,headers:{get:(name:string)=>name.toLowerCase()==='link'?link:null},json:async()=>body,arrayBuffer:async()=>new TextEncoder().encode(JSON.stringify(body)).buffer});
const initial='https://api.github.com/users/o/packages/container/p/versions?per_page=100',next='https://api.github.com/users/o/packages/container/p/versions?per_page=100&page=2';

describe('Phase 8D1-R bounded paginated HTTP',()=>{
  it('bounds bodies, classifies permissions before bodies, and parses Link fail-closed',async()=>{
    expect(existsSync('scripts/phase8d1/recovery-http.mjs')).toBe(true);const {fetchAllPackageVersions,fetchJsonBounded}:any=await import(path);const first=Array.from({length:100},(_,id)=>({id,tags:[`t${id}`]})),second=[{id:100,tags:['target']}];
    const paginate=async(link:string)=>{const seen:string[]=[];const fetchImpl=async(url:string,options:any)=>{expect(options.signal).toBeDefined();seen.push(url);return seen.length===1?response(200,first,link):response(200,second);};const result=await fetchAllPackageVersions({initialUrl:initial,token:'x',deadlineMs:Date.now()+1000,fetchImpl});return {result,seen};};
    let paged=await paginate(`<${next}>; foo; type="application/json"; rel="NEXT"`);expect(paged.result.pages).toBe(2);expect(paged.result.versions).toHaveLength(101);expect(paged.seen).toHaveLength(2);
    await expect(paginate('<https://evil.invalid/next>; rel="next"')).rejects.toThrow('PACKAGE_API_PAGINATION_HOLD');
    for(const link of [`<${next}>; foo=a:b; rel=next`,`<${next}>; foo=<bad>; rel=next`,`<${next}>; rel=next; rel="next"`,`<${next}>; rel="next`])await expect(paginate(link)).rejects.toThrow('PACKAGE_API_PAGINATION_HOLD');
    const hangingBody=async(_:string,options:any)=>({status:200,headers:{get:()=>null},json:()=>new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(options.signal.reason),{once:true}))});
    await expect(fetchJsonBounded('https://api.github.com/test',{deadlineMs:Date.now()+1000,timeoutMs:10,fetchImpl:hangingBody})).rejects.toThrow('PACKAGE_API_TIMEOUT_HOLD');
    const forbiddenBody=async()=>({status:403,headers:{get:()=>null},json:()=>new Promise(()=>{})});const started=Date.now();await expect(fetchAllPackageVersions({initialUrl:initial,token:'x',deadlineMs:Date.now()+1000,fetchImpl:forbiddenBody})).rejects.toThrow('PACKAGE_API_PERMISSION_HOLD');expect(Date.now()-started).toBeLessThan(100);
    await expect(fetchJsonBounded('https://api.github.com/test',{deadlineMs:Date.now()-1,timeoutMs:10,fetchImpl:async()=>response(200,{})})).rejects.toThrow('PACKAGE_METADATA_TOTAL_TIMEOUT_HOLD');
  });
  it('performs stateless unauthenticated exact-digest HEAD and GET checks',async()=>{
    const {fetchUnauthenticatedRegistryManifestStatuses}:any=await import(path),url=`https://ghcr.io/v2/owner/package/manifests/sha256:${'a'.repeat(64)}`;
    const invoke=async(headStatus:number,getStatus:number)=>{const calls:any[]=[];const fetchImpl=async(requestUrl:string,options:any)=>{calls.push({url:requestUrl,options});const status=options.method==='HEAD'?headStatus:getStatus;return {status,headers:{get:()=>null},arrayBuffer:async()=>new ArrayBuffer(0)};};const result=await fetchUnauthenticatedRegistryManifestStatuses({url,deadlineMs:Date.now()+1000,fetchImpl});return {calls,result};};
    for(const status of [401,403,404]){const {calls,result}=await invoke(status,status);expect(result).toEqual({unauthenticatedHeadStatus:status,unauthenticatedGetStatus:status,headDenied:true,getDenied:true});expect(calls.map(call=>call.options.method)).toEqual(['HEAD','GET']);for(const call of calls){expect(call.url).toBe(url);expect(call.options.headers).toEqual({Accept:'application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json','User-Agent':'phase8d1-existing-digest-recovery'});expect(JSON.stringify(call.options)).not.toMatch(/authorization|cookie/i);}}
    expect((await invoke(200,401)).result).toMatchObject({headDenied:false,getDenied:true});expect((await invoke(401,200)).result).toMatchObject({headDenied:true,getDenied:false});
  });
});
