#!/usr/bin/env python3
"""Volledige reinstall van solana-bot met verse image (contra-final).
Pad: stop → delete → create (werkt in TrueNAS 26 BETA waar update+start faalt).
De image `solana-bot:contra-final` is een NIEUWE tag → TrueNAS MOET bouwen.
Daarna job-keten pollen tot RUNNING.
"""
import json, subprocess, sys, time, os

NEW_FREEZE = 'ee0f27d2c9cedc9ddef690cf2f3a1d53c4ed7f0b24fa9e77ea12588ca976a689'
NEW_IMAGE_TAG = 'solana-bot:contra-final'
NAME = 'solana-bot'

def api(method, params):
    env = dict(os.environ)
    env['TRUENAS_API_METHOD'] = method
    env['TRUENAS_API_PARAMS'] = json.dumps(params)
    out = subprocess.run(['node', 'truenas-wss-admin.mjs'],
                         capture_output=True, text=True, env=env, check=True).stdout
    return json.loads(out)

def wait_job(job_id, timeout=300):
    """Poll een job-keten (TrueNAS 26: core.job_wait chain) tot terminal state."""
    start = time.time()
    seen = set()
    while time.time() - start < timeout:
        time.sleep(15)
        try:
            jr = api('core.get_jobs', [[['id', '=', job_id]]])
        except Exception:
            continue
        jobs = jr.get('result') or []
        if not jobs:
            continue
        j = jobs[0]
        state = j.get('state')
        print(f'  job {job_id}: {state}')
        if state == 'FAILED':
            print('  FAILED:', json.dumps(j.get('error'))[:300])
            sys.exit(1)
        if state == 'SUCCESS':
            try:
                nxt = api('core.job_wait', [job_id]).get('result')
                if isinstance(nxt, int) and nxt and nxt not in seen:
                    seen.add(nxt)
                    job_id = nxt
                    print(f'  chain → job {job_id}')
                    continue
            except Exception:
                pass
            return True
    print('  TIMEOUT wachten op job')
    return False

# 1. Huidige config ophalen (voor de create-payload)
res = api('app.query', [[['name', '=', NAME]], {'extra': {'retrieve_config': True}}])
app = res['result'][0]
config = app['config']
svc = config['services']['solana-bot']
print(f'[reinstall] app={NAME} state={app["state"]}')

# 2. Config muteren: verse image-tag + freeze + env
env = dict(svc.get('environment') or {})
env['TRITON_STREAM'] = 'geyser'
env['ENTRY_MODE'] = 'contra'
env['MIN_AGE_MINUTES'] = '20'
svc['environment'] = env
build = svc.get('build') or {}
build['args'] = dict(build.get('args') or {})
build['args']['SOURCE_FREEZE_SHA256'] = NEW_FREEZE
svc['build'] = build
svc['image'] = NEW_IMAGE_TAG
print(f'[reinstall] image → {NEW_IMAGE_TAG} | freeze → {NEW_FREEZE[:16]}… | ENTRY_MODE=contra | MIN_AGE=20')

# 3. stop (als running)
try:
    st = api('app.stop', [NAME])
    print('[reinstall] app.stop →', json.dumps(st.get('result')))
    time.sleep(15)
except Exception as e:
    print('[reinstall] stop niet nodig:', str(e)[:60])

# 4. delete
try:
    dl = api('app.delete', [NAME])
    print('[reinstall] app.delete →', json.dumps(dl.get('result')))
    time.sleep(20)
except Exception as e:
    print('[reinstall] delete fout (misschien al weg):', str(e)[:80])

# 5. create — app.create(name, {custom_app:true, custom_compose_config: config})
try:
    cr = api('app.create', [NAME, {'custom_app': True, 'custom_compose_config': config}])
    print('[reinstall] app.create →', json.dumps(cr.get('result')))
    job_id = cr.get('result')
    if isinstance(job_id, int):
        wait_job(job_id, timeout=400)
    elif isinstance(job_id, dict) and 'job' in job_id:
        wait_job(job_id['job'], timeout=400)
    else:
        print('[reinstall] create-result vorm onverwacht, poll via app state')
        time.sleep(60)
except Exception as e:
    print('[reinstall] app.create FOUT:', str(e)[:150])

# 6. Verifieer
time.sleep(20)
inst = api('app.get_instance', [NAME]).get('result', {})
print('[reinstall] eind-state:', inst.get('state'))
for c in inst.get('active_workloads', {}).get('container_details', []):
    print('[reinstall] container:', c.get('id', '')[:12], '| state:', c.get('state'), '| image:', c.get('image'))