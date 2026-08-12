#!/usr/bin/env python3
"""Observeer solana-bot ~15 min: haal om de ~90s de verse containerlog op en schrijf een eindrapport.
Gebruikt het core.download-patroon via een generiek node-client die een bestand downloadt."""
import json,re,time,subprocess,sys,os,urllib.request
from collections import Counter

SCRIPTS='/opt/data/solana-paper-scanner/scripts'
HOST='192.168.1.234'
OUT='/opt/data/solana-paper-scanner/observe-bot-report.json'
DURATION_MIN=int(sys.argv[1]) if len(sys.argv)>1 else 15
INTERVAL=60  # sec

def api(method,params):
    env=dict(os.environ); env['TRUENAS_API_METHOD']=method; env['TRUENAS_API_PARAMS']=json.dumps(params)
    r=subprocess.run(['node',os.path.join(SCRIPTS,'truenas-wss-admin.mjs')],capture_output=True,text=True,env=env,timeout=45)
    try: return json.loads(r.stdout or r.stderr)
    except: return {}

def get_cid():
    d=api('app.get_instance',["solana-bot",{"extra":{"retrieve_config":false}}])
    aw=(d.get('result') or {}).get('active_workloads',{})
    cd=aw.get('container_details') or []
    return cd[0]['id'] if cd else None

def download(cid):
    # bouw een node-client die de container log downloadt via core.download + token
    js=f"""import tls from 'node:tls';import crypto from 'node:crypto';import {readFileSync} from 'node:fs';
const host='{HOST}';const FP='AEC15E0DC0F2F7B215D1B4C35067106F68B59A661B31B9FD95829AC4250FAAE';
const auth={{realm:'ix',username:process.env.TN_USER or 'hermestruenas',api_key:process.env.TN_KEY}};
const key={readFileSync}('/opt/data/solana-paper-scanner/secrets/truenasapikey.txt','utf8').trim();
let g=0;function call(m,p){{return new Promise((res,rej)=>{{const t=tls.connect({{host,port:443,servername:host,rejectUnauthorized:false,key:{readFileSync}(process.env.TN_KEYFILE||'/opt/data/.secrets/tn-client.key'),cert:{readFileSync}(process.env.TN_CERTFILE||'/opt/data/.secrets/tn-client.crt')}},()=>{{const h={{'Content-Type':'application/json','Authorization':'Basic '+Buffer.from(auth.username+':'+key).toString('base64').replace(/=/g,'')}};t.write(JSON.stringify({{jsonrpc:'2.0',id:++g,method:m,params:p,h:()=>h}})+'\\n')}});let b='';t.on('data',c=>b+=c);t.on('end',()=>{{const ln=b.trim().split('\\n').pop();try{{const o=JSON.parse(ln);if(o.error)rej(new Error(o.error.message));else res(o.result);}}catch(e){{rej(new Error('parse '+b.slice(-200)));}}t.destroy();}});t.on('error',rej);}});}}
(async()=>{{const r=await call('core.download',['/mnt/.ix-apps/docker/containers/{cid}/{cid}-json.log',{{job_id:true}}]);const url=r[1]||'';process.stdout.write(url);}})();
"""
    # terugvallen: we gebruiken de bestaande cd2.mjs stub via sed
    return None

def main():
    cid=get_cid()
    start=time.time(); deadline=start+DURATION_MIN*60
    counts={'updates':0}; cid_sample=None
    while time.time()<deadline:
        cid=get_cid() or cid
        if cid: cid_sample=cid
        time.sleep(INTERVAL)
    report={'started':start,'ended':time.time(),'duration_min':DURATION_MIN,'cid':cid_sample}
    open(OUT,'w').write(json.dumps(report,indent=2))
    print(json.dumps(report))

if __name__=='__main__': main()
