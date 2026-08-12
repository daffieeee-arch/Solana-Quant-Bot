#!/usr/bin/env python3
"""Update solana-bot → balance-pricing (route 2) + native-SOL kind-fix."""
import json, subprocess, os, hashlib, pathlib

def api(m, p):
    e = dict(os.environ); e['TRUENAS_API_METHOD'] = m; e['TRUENAS_API_PARAMS'] = json.dumps(p)
    r = subprocess.run(['node', 'truenas-wss-admin.mjs'], capture_output=True, text=True, env=e)
    if r.returncode != 0: raise RuntimeError(r.stdout[:200] + r.stderr[:100])
    return json.loads(r.stdout)

ROOT = pathlib.Path(__file__).resolve().parent.parent
# freeze over ALLE build-context bestanden (src + frontend + config + Dockerfile)
def collect(d):
    skip = {'node_modules', 'dist', '.git', 'data', 'data-bot-v5'}
    return sorted([str(p.relative_to(d)) for p in d.rglob('*') if p.is_file() and not any(s in p.parts for s in skip)])
files = collect(ROOT / 'src') + collect(ROOT / 'frontend')
h = hashlib.sha256()
h.update('\n'.join(sorted(set(files))).encode())
for f in files:
    h.update((ROOT / f).read_bytes())
for extra in ['package.json', 'Dockerfile', 'tsconfig.json']:
    if (ROOT / extra).exists(): h.update((ROOT / extra).read_bytes())
FREEZE = h.hexdigest()
IMAGE = 'solana-bot:contra-audit11'
print('[freeze]', FREEZE)

r = api('app.query', [[['name', '=', 'solana-bot']], {'extra': {'retrieve_config': True}}])
config = r['result'][0]['config']
svc = config['services']['solana-bot']
svc['build']['args']['SOURCE_FREEZE_SHA256'] = FREEZE
svc['image'] = IMAGE
u = api('app.update', ['solana-bot', {'custom_compose_config': config}])
print('[update] image:', svc.get('image'), '| result:', json.dumps(u.get('result'))[:60])