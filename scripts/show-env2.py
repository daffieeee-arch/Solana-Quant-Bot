import json, subprocess, os
def api(m, p):
    e=dict(os.environ); e['TRUENAS_API_METHOD']=m; e['TRUENAS_API_PARAMS']=json.dumps(p)
    r=subprocess.run(['node','truenas-wss-admin.mjs'],capture_output=True,text=True,env=e)
    return json.loads(r.stdout) if r.stdout else {}
r=api('app.query',[[['name','=','solana-bot']],{'extra':{'retrieve_config':True}}])
cfg=r.get('result',[{}])[0].get('config',{})
svc=cfg.get('services',{}).get('solana-bot',{})
print('environment type:', type(svc.get('environment')).__name__)
print('environment raw (eerste 800 tekens):')
print(json.dumps(svc.get('environment'))[:800])