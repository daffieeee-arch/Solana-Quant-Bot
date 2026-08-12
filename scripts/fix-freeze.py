#!/usr/bin/env python3
"""Corrigeer SOURCE_FREEZE_SHA256 naar de correct berekende hash (306b5e92…)."""
import json, subprocess, os, hashlib, pathlib

def api(m, p):
    e = dict(os.environ); e['TRUENAS_API_METHOD'] = m; e['TRUENAS_API_PARAMS'] = json.dumps(p)
    r = subprocess.run(['node', 'truenas-wss-admin.mjs'], capture_output=True, text=True, env=e)
    if r.returncode != 0: raise RuntimeError(r.stdout[:200] + r.stderr[:100])
    return json.loads(r.stdout)

ROOT = pathlib.Path('/opt/data/solana-paper-scanner')
SKIP = {'node_modules', '.git', 'dist', 'data-bot-v5', '__pycache__'}
def collect(root_abs):
    out = []
    for dirpath, dirnames, filenames in os.walk(root_abs):
        dirnames[:] = [d for d in dirnames if d not in SKIP]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            if os.path.islink(full): continue
            out.append(full)
    return sorted(out)
items = collect(str(ROOT / 'src')) + collect(str(ROOT / 'frontend'))
h = hashlib.sha256()
names = sorted(os.path.relpath(f, str(ROOT)) for f in items)
h.update('\n'.join(names).encode())
for f in items:
    with open(f, 'rb') as fh: h.update(fh.read())
for extra in ['package.json', 'Dockerfile', 'tsconfig.json']:
    ep = str(ROOT / extra)
    if os.path.exists(ep):
        with open(ep, 'rb') as fh: h.update(fh.read())
FREEZE = h.hexdigest()
print('[freeze-corrigeer]', FREEZE)

r = api('app.query', [[['name', '=', 'solana-bot']], {'extra': {'retrieve_config': True}}])
config = r['result'][0]['config']
svc = config['services']['solana-bot']
old = svc['build']['args'].get('SOURCE_FREEZE_SHA256')
svc['build']['args']['SOURCE_FREEZE_SHA256'] = FREEZE
print('[vóór]', old, '\n[ná]', FREEZE)
u = api('app.update', ['solana-bot', {'custom_compose_config': config}])
print('[update] result:', json.dumps(u.get('result'))[:60])