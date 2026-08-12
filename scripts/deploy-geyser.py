#!/usr/bin/env python3
"""Deploy solana-bot met TRITON_STREAM=geyser + nieuwe SOURCE_FREEZE_SHA256.
Pad: app.query (retrieve_config) → config muteren (env + build-arg) → app.update
→ job-poll tot terminal state. TrueNAS bouwt de image zelf uit de build-context
(= /mnt/fastdisk/ai/hermes/solana-paper-scanner, byte-identiek aan agent-pad).
"""
import json, subprocess, sys, time, os

NEW_FREEZE = 'ee0f27d2c9cedc9ddef690cf2f3a1d53c4ed7f0b24fa9e77ea12588ca976a689'
# Forceer cache-invalidate: nieuwe image-tag zodat er géén oude laag hergebruikt
# kan worden (pull_policy:never + zelfde tag kan een gestale image-laag laten staan).
NEW_IMAGE_TAG = 'solana-bot:contra-2'
# Extra env die we bij deze deploy toevoegen (contra-mode live-fase).
EXTRA_ENV = {
    'ENTRY_MODE': 'contra',
    'MIN_AGE_MINUTES': '20',
}

def api(method, params):
    env = dict(os.environ)
    env['TRUENAS_API_METHOD'] = method
    env['TRUENAS_API_PARAMS'] = json.dumps(params)
    out = subprocess.run(['node', 'truenas-wss-admin.mjs'],
                         capture_output=True, text=True, env=env, check=True).stdout
    return json.loads(out)

# 1. Huidige config ophalen
res = api('app.query', [[['name', '=', 'solana-bot']], {'extra': {'retrieve_config': True}}])
app = res['result'][0]
config = app['config']
name = app['name']
svc = config['services']['solana-bot']
print(f'[deploy] app={name} state={app["state"]}')

# 2. Mutaties: TRITON_STREAM + new freeze build-arg + contra-env
env = dict(svc.get('environment') or {})
env['TRITON_STREAM'] = 'geyser'
for k, v in EXTRA_ENV.items():
    env[k] = v
svc['environment'] = env
build = svc.get('build') or {}
build['args'] = build.get('args') or {}
old_freeze = build['args'].get('SOURCE_FREEZE_SHA256')
build['args']['SOURCE_FREEZE_SHA256'] = NEW_FREEZE
svc['build'] = build
# image-tag bump om cache-invalidate te forceren
svc['image'] = NEW_IMAGE_TAG
print(f'[deploy] TRITON_STREAM → geyser')
print(f'[deploy] SOURCE_FREEZE_SHA256: {(old_freeze or "(geen)")[:16]}… → {NEW_FREEZE[:16]}…')
print(f'[deploy] image: → {NEW_IMAGE_TAG}')

# 3. app.update — TrueNAS 26 accepteert custom_compose_config (structured object),
# niet {custom_app: ...} (dat gaf 'Extra inputs are not permitted').
try:
    upd = api('app.update', [name, {'custom_compose_config': config}])
except subprocess.CalledProcessError as e:
    print('[deploy] app.update stderr:', e.stderr[-400:] if e.stderr else '')
    sys.exit(1)
job_id = None
if isinstance(upd, dict) and 'result' in upd:
    r = upd['result']
    job_id = r if isinstance(r, (int, str)) else (r.get('job_id') if isinstance(r, dict) else None)
print(f'[deploy] app.update ok → job_id={job_id}')

# 4. Poll job-keten tot terminal state (TrueNAS 26: chained build→deploy jobs)
seen = set()
for i in range(40):
    time.sleep(15)
    if job_id is None:
        break
    try:
        jr = api('core.get_jobs', [[['id', '=', job_id]]])
    except Exception:
        continue
    jobs = jr.get('result', [])
    if not jobs:
        continue
    j = jobs[0]
    state = j.get('state')
    print(f'[deploy] job {job_id}: {state}')
    if state == 'FAILED':
        print('[deploy] FAILED:', json.dumps(j.get('error', {}))[:400])
        sys.exit(1)
    if state == 'SUCCESS':
        # chained job? core.job_wait returns next job id as int
        try:
            nxt = api('core.job_wait', [job_id])
            nxt_id = nxt.get('result')
            if isinstance(nxt_id, (int,)) and nxt_id and nxt_id != job_id and nxt_id not in seen:
                seen.add(nxt_id)
                job_id = nxt_id
                print(f'[deploy] chain → job {job_id}')
                continue
        except Exception:
            pass
        break
    if i >= 2 and state not in ('RUNNING', 'WAITING', 'PENDING'):
        # TrueNAS 26 BETA: job kan SUCCESS zijn zonder container-start; check app
        break

# 5. Verifieer
time.sleep(15)
inst = api('app.get_instance', [name]).get('result', {})
print('[deploy] eind-state:', inst.get('state'))
print('[deploy] klaar — verifieer via /api/debug (discovery source + usage)')