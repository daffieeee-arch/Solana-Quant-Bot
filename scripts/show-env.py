import json, subprocess, os
def api(m, p):
    e=dict(os.environ); e['TRUENAS_API_METHOD']=m; e['TRUENAS_API_PARAMS']=json.dumps(p)
    r=subprocess.run(['node','truenas-wss-admin.mjs'],capture_output=True,text=True,env=e)
    return json.loads(r.stdout) if r.stdout else {}
r=api('app.query',[[['name','=','solana-bot']],{'extra':{'retrieve_config':True}}])
cfg=r.get('result',[{}])[0].get('config',{})
svc=cfg.get('services',{}).get('solana-bot',{})
envs=svc.get('environment',[])
# environment is een lijst van dicts of strings?
for kv in envs:
    if isinstance(kv, dict):
        key = kv.get('name') or ''
        if any(k in key.upper() for k in ['MIN_LIQUIDITY','MAX_LIQUIDITY','MIN_AGE','ENTRY','MIN_MOMENTUM','MIN_PRICE','MIN_VOLUME','SOL_PRICE','STRICT']):
            print(key, '=', kv.get('value'))
    else:
        s=str(kv)
        if any(k in s.upper() for k in ['MIN_LIQUIDITY','MAX_LIQUIDITY','MIN_AGE','MIN_MOMENTUM','MIN_PRICE','MIN_VOLUME','SOL_PRICE']):
            print(s)