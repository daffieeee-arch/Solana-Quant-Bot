const API_ORIGIN='https://api.github.com';
const MAX_PAGES=100;
const MAX_ITEMS=10_000;
const ARTIFACT_ROLES=Object.freeze({preflight:'PREFLIGHT','cockpit-push':'COCKPIT_PUSH','runner-push':'RUNNER_PUSH','final-hold':'FINAL_HOLD','verify-support':'VERIFY_SUPPORT'});
const RECOVERY_USER_AGENT='phase8d1-existing-digest-recovery';

function reasonForStatus(status){
  if(status===401||status===403)return 'PACKAGE_API_PERMISSION_HOLD';
  if(status===404)return 'PACKAGE_METADATA_NOT_VISIBLE_HOLD';
  return `PACKAGE_METADATA_HTTP_HOLD_${status}`;
}

async function bounded(deadlineMs,timeoutMs,operation){
  const remaining=deadlineMs-Date.now();if(!Number.isFinite(deadlineMs)||remaining<=0)throw new Error('PACKAGE_METADATA_TOTAL_TIMEOUT_HOLD');
  const duration=Math.max(1,Math.min(timeoutMs,remaining)),reason=remaining<=timeoutMs?'PACKAGE_METADATA_TOTAL_TIMEOUT_HOLD':'PACKAGE_API_TIMEOUT_HOLD',controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(new Error(reason)),duration);
  try{return await operation(controller.signal);}
  catch(error){if(controller.signal.aborted)throw controller.signal.reason instanceof Error?controller.signal.reason:new Error(reason);throw error;}
  finally{clearTimeout(timer);}
}

export async function fetchJsonBounded(url,{deadlineMs,timeoutMs=15_000,fetchImpl=fetch,...options}){
  return bounded(deadlineMs,timeoutMs,async signal=>{const response=await fetchImpl(url,{...options,signal});if(response.status<200||response.status>=300)return {status:response.status,body:null,link:response.headers.get('link'),location:response.headers.get('location')};const body=response.status===204?null:await response.json();return {status:response.status,body,link:response.headers.get('link'),location:response.headers.get('location')};});
}
export async function fetchBytesBounded(url,{deadlineMs,timeoutMs=30_000,fetchImpl=fetch,...options}){
  return bounded(deadlineMs,timeoutMs,async signal=>{const response=await fetchImpl(url,{...options,signal}),bytes=Buffer.from(await response.arrayBuffer());return {status:response.status,bytes,location:response.headers.get('location'),digest:response.headers.get('docker-content-digest')};});
}
export async function fetchHeadBounded(url,{deadlineMs,timeoutMs=30_000,fetchImpl=fetch,...options}){
  return bounded(deadlineMs,timeoutMs,async signal=>{const response=await fetchImpl(url,{...options,method:'HEAD',signal});return {status:response.status,location:response.headers.get('location'),digest:response.headers.get('docker-content-digest')};});
}

function artifactRole(role){const value=ARTIFACT_ROLES[role];if(!value)throw new Error('ARTIFACT_DOWNLOAD_ROLE_INVALID');return value;}
function signedArtifactUrl(value,roleCode){if(typeof value!=='string'||value.includes('#'))throw new Error(`ARTIFACT_DOWNLOAD_LOCATION_INVALID_${roleCode}`);let url;try{url=new URL(value);}catch{throw new Error(`ARTIFACT_DOWNLOAD_LOCATION_INVALID_${roleCode}`);}if(url.protocol!=='https:'||url.username||url.password)throw new Error(`ARTIFACT_DOWNLOAD_LOCATION_INVALID_${roleCode}`);return value;}
function redirectExhausted(error){let value=error;for(let depth=0;depth<4&&value;depth++){if(value?.message==='redirect count exceeded'||value?.code==='ERR_FR_TOO_MANY_REDIRECTS')return true;value=value.cause;}return false;}
export async function downloadGitHubArtifactZip({repository,artifactId,role,token,deadlineMs,fetchImpl=fetch}){
  const roleCode=artifactRole(role),apiUrl=`${API_ORIGIN}/repos/${repository}/actions/artifacts/${artifactId}/zip`;
  const first=await bounded(deadlineMs,30_000,async signal=>{const response=await fetchImpl(apiUrl,{method:'GET',redirect:'manual',signal,headers:{Authorization:`Bearer ${token}`,Accept:'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':RECOVERY_USER_AGENT}});return {status:response.status,location:response.headers.get('location')};});
  if(first.status!==302)throw new Error(`ARTIFACT_DOWNLOAD_HTTP_${roleCode}_${first.status}`);
  if(!first.location)throw new Error(`ARTIFACT_DOWNLOAD_LOCATION_MISSING_${roleCode}`);
  const location=signedArtifactUrl(first.location,roleCode);
  let signed;try{signed=await fetchBytesBounded(location,{deadlineMs,timeoutMs:30_000,fetchImpl,method:'GET',redirect:'follow',headers:{'User-Agent':RECOVERY_USER_AGENT}});}catch(error){if(redirectExhausted(error))throw new Error(`ARTIFACT_DOWNLOAD_SIGNED_REDIRECT_HOLD_${roleCode}`);throw error;}
  if(signed.status!==200)throw new Error(`ARTIFACT_DOWNLOAD_SIGNED_HTTP_${roleCode}_${signed.status}`);
  return signed.bytes;
}

function splitLinkHeader(value){
  const rows=[];let start=0,angle=0,quoted=false,escaped=false;
  for(let index=0;index<value.length;index++){
    const char=value[index];if(escaped){escaped=false;continue;}if(quoted&&char==='\\'){escaped=true;continue;}if(char==='"'){quoted=!quoted;continue;}if(!quoted&&char==='<')angle++;else if(!quoted&&char==='>')angle--;else if(!quoted&&angle===0&&char===','){rows.push(value.slice(start,index).trim());start=index+1;}if(angle<0)throw new Error('PACKAGE_API_PAGINATION_HOLD');
  }
  if(quoted||angle!==0)throw new Error('PACKAGE_API_PAGINATION_HOLD');rows.push(value.slice(start).trim());return rows.filter(Boolean);
}
function parseLinkSegment(segment){
  const target=segment.match(/^<([^>]+)>([\s\S]*)$/);if(!target)throw new Error('PACKAGE_API_PAGINATION_HOLD');let rest=target[2],relations=[];const parameters=new Set(),token=/^[!#$%&'*+.^_`|~0-9A-Za-z-]+/;
  while(rest.trim()){
    rest=rest.trimStart();if(!rest.startsWith(';'))throw new Error('PACKAGE_API_PAGINATION_HOLD');rest=rest.slice(1).trimStart();const nameMatch=rest.match(token);if(!nameMatch)throw new Error('PACKAGE_API_PAGINATION_HOLD');const name=nameMatch[0].toLowerCase();if(parameters.has(name))throw new Error('PACKAGE_API_PAGINATION_HOLD');parameters.add(name);rest=rest.slice(nameMatch[0].length).trimStart();let value=null;
    if(rest.startsWith('=')){rest=rest.slice(1).trimStart();if(rest.startsWith('"')){let output='',closed=false,index=1;for(;index<rest.length;index++){const char=rest[index];if(char==='\\'){index++;if(index>=rest.length)throw new Error('PACKAGE_API_PAGINATION_HOLD');output+=rest[index];continue;}if(char==='"'){closed=true;index++;break;}output+=char;}if(!closed)throw new Error('PACKAGE_API_PAGINATION_HOLD');value=output;rest=rest.slice(index);}else{const valueMatch=rest.match(token);if(!valueMatch)throw new Error('PACKAGE_API_PAGINATION_HOLD');value=valueMatch[0];rest=rest.slice(valueMatch[0].length);}}
    if(name==='rel'){if(value===null)throw new Error('PACKAGE_API_PAGINATION_HOLD');relations.push(...value.split(/\s+/).filter(Boolean).map(relation=>relation.toLowerCase()));}
  }
  return {url:target[1],relations};
}
function nextLink(value){
  if(!value)return null;const next=[];for(const segment of splitLinkHeader(value)){const parsed=parseLinkSegment(segment);if(parsed.relations.includes('next'))next.push(parsed.url);}if(next.length>1)throw new Error('PACKAGE_API_PAGINATION_HOLD');return next[0]??null;
}
function validateVersionsUrl(value,expectedPath){
  const url=new URL(value);if(url.origin!==API_ORIGIN||url.protocol!=='https:'||url.username||url.password||url.pathname!==expectedPath)throw new Error('PACKAGE_API_PAGINATION_HOLD');return url.href;
}

export async function fetchAllPackageVersions({initialUrl,token,deadlineMs,fetchImpl=fetch}){
  const initial=new URL(initialUrl),expectedPath=initial.pathname;let url=validateVersionsUrl(initial.href,expectedPath),pages=0;const versions=[];
  while(url){
    pages++;if(pages>MAX_PAGES)throw new Error('PACKAGE_API_PAGINATION_HOLD');
    const response=await fetchJsonBounded(url,{deadlineMs,timeoutMs:15_000,fetchImpl,method:'GET',headers:{Authorization:`Bearer ${token}`,Accept:'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'phase8d1-existing-digest-recovery'}});
    if(response.status!==200)throw new Error(reasonForStatus(response.status));if(!Array.isArray(response.body))throw new Error('PACKAGE_API_RESPONSE_HOLD');versions.push(...response.body);if(versions.length>MAX_ITEMS)throw new Error('PACKAGE_API_PAGINATION_HOLD');
    const next=nextLink(response.link);url=next?validateVersionsUrl(next,expectedPath):null;
  }
  return Object.freeze({versions:Object.freeze(versions),pages});
}
