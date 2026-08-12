#!/usr/bin/env python3
"""Update solana-bot → zuinigheidsfixes (credits-besparing op Triton)."""
import json, subprocess, os

def api(m, p):
    e = dict(os.environ)
    e['TRUENAS_API_METHOD'] = m
    e['TRUENAS_API_PARAMS'] = json.dumps(p)
    r = subprocess.run(['node', 'truenas-wss-admin.mjs'], capture_output=True, text=True, env=e)
    if r.returncode != 0:
        raise RuntimeError(r.stdout[:200] + r.stderr[:100])
    return json.loads(r.stdout)

# freeze berekenen lokaal uit dezelfde bron als de build-context
import hashlib, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
files = sorted([str(p.relative_to(ROOT)) for p in (ROOT / 'src').rglob('*') if p.is_file() and 'node_modules' not in str(p) and 'dist' not in str(p)])
files += sorted([str(p.relative_to(ROOT)) for p in (ROOT / 'frontend').rglob('*') if p.is_file() and 'node_modules' not in str(p) and 'dist' not in str(p)])
h = hashlib.sha256()
h.update('\n'.join(files).encode())
for f in files:
    h.update((ROOT / f).read_bytes())
h.update((ROOT / 'package.json').read_bytes())
h.update((ROOT / 'Dockerfile').read_bytes())
FREEZE = h.hexdigest()
IMAGE = 'solana-bot:contra-audit10'
print('[freeze]', FREEZE)

r = api('app.query', [[['name', '=', 'solana-bot']], {'extra': {'retrieve_config': True}}])
config = r['result'][0]['config']
svc = config['services']['solana-bot']
svc['build']['args']['SOURCE_FREEZE_SHA256'] = FREEZE
svc['image'] = IMAGE
u = api('app.update', ['solana-bot', {'custom_compose_config': config}])
print('[update] image:', svc.get('image'), '| result:', json.dumps(u.get('result'))[:60])