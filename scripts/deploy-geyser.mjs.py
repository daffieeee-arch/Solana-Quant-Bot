#!/usr/bin/env python3
"""Deploy solana-bot met TRITON_STREAM=geyser via app.update (custom-app).
Haalt de huidige config op, voegt TRITON_STREAM toe, dient hem opnieuw in.
Behoudt volumes, secrets-mounts en alle bestaande env-vars."""
import json, subprocess, sys, time

def api(method, params):
    env = dict(__import__('os').environ)
    env['TRUENAS_API_METHOD'] = method
    env['TRUENAS_API_PARAMS'] = json.dumps(params)
    out = subprocess.run(
        ['node', 'truenas-wss-admin.mjs'],
        capture_output=True, text=True, env=env, check=True,
    ).stdout
    return json.loads(out)

# 1. Haal huidige config op
res = api('app.query', [[['name', '=', 'solana-bot']], {'extra': {'retrieve_config': True}}])
app = res['result'][0]
config = app['config']
name = app['name']
print(f'[deploy] app={name} state={app["state"]}')
svc = config['services']['solana-bot']

# 2. TRITON_STREAM toevoegen (geyser = Dragon's Mouth gRPC; vixen = default)
env = dict(svc.get('environment') or {})
previous = env.get('TRITON_STREAM', '(niet gezet → default vixen)')
env['TRITON_STREAM'] = 'geyser'
svc['environment'] = env
print(f'[deploy] TRITON_STREAM: {previous} → geyser')

# 3. Submit via app.update (TrueNAS custom-app update)
payload = {'custom_app': config}
try:
    upd = api('app.update', [name, payload])
    job_id = None
    if isinstance(upd, dict) and 'result' in upd:
        res_u = upd['result']
        job_id = res_u if isinstance(res_u, (int, str)) else (res_u.get('job_id') if isinstance(res_u, dict) else None)
    print(f'[deploy] app.update ok → job_id={job_id}')
except Exception as e:
    # fallback: probeer als app.update met custom_app direct
    print(f'[deploy] app.update direct faalde ({e}); probeer config={json.dumps(payload)[:120]}…')
    sys.exit(1)

# 4. Poll job tot SUCCESS (max ~5 min)
if job_id:
    for i in range(20):
        time.sleep(15)
        jr = api('core.get_jobs', [[['id', '=', job_id]]])
        jobs = jr.get('result', [])
        if not jobs:
            continue
        state = jobs[0].get('state')
        print(f'[deploy] job {job_id}: {state}')
        if state in ('SUCCESS', 'FAILED'):
            if state == 'FAILED':
                print('[deploy] FAILED:', json.dumps(jobs[0].get('error', {}))[:400])
                sys.exit(1)
            break
    else:
        print('[deploy] timeout op job-poll')
        sys.exit(1)

# 5. Verifieer: app draait + container-image + TRITON_STREAM gebruikt
time.sleep(10)
inst = api('app.get_instance', [name]).get('result', {})
print('[deploy] state:', inst.get('state'))
print('[deploy] image :', inst.get('active_workloads', {}).get('containers'))
print('[deploy] klaar — check /api/debug voor de usage + discovery status')