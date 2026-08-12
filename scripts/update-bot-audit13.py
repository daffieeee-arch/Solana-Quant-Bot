#!/usr/bin/env python3
"""Deploy solana-bot → contra-audit14: Titan SDK-websocket + mark/symbol/dexlink fixes.

Code: scoreContraMomentum (dip-score) + Top-N ranked entry (maxEntriesPerScan).
Config (uit 3-maanden backtest):
  MIN_AGE_MINUTES 20→5        (beste edge 5-15 min)
  MAX_AGE_MINUTES 259200→240  (4h+ is verliezend)
  CONTRA_MAX_BUY_PRESSURE 0.55 (bp<=0.55 = beste expectancy +23,4%)
  MIN_MOMENTUM_SCORE 50→40    (dip-score haalbaar)
  MAX_HOLD_MINUTES 15→30      (later exits — gebruiker)
  MAX_ENTRIES_PER_SCAN 1      (Top-N ranked entry)
"""
import json, subprocess, os, hashlib, pathlib, sys

def api(m, p):
    e = dict(os.environ)
    e['TRUENAS_API_METHOD'] = m
    e['TRUENAS_API_PARAMS'] = json.dumps(p)
    r = subprocess.run(['node', 'truenas-wss-admin.mjs'], capture_output=True, text=True, env=e)
    if r.returncode != 0:
        raise RuntimeError(r.stdout[:200] + r.stderr[:100])
    return json.loads(r.stdout)

# ── freeze berekenen uit de build-context (dezelfde bron als de Docker-build) ──
ROOT = pathlib.Path(__file__).resolve().parent.parent
def freeze_hash(root: pathlib.Path) -> str:
    files = []
    for sub in ('src', 'frontend'):
        d = root / sub
        if not d.exists():
            continue
        for p in sorted(d.rglob('*')):
            if p.is_file() and 'node_modules' not in str(p) and 'dist' not in str(p):
                files.append(str(p.relative_to(root)))
    h = hashlib.sha256()
    h.update('\n'.join(files).encode())
    for f in files:
        h.update((root / f).read_bytes())
    h.update((root / 'package.json').read_bytes())
    h.update((root / 'Dockerfile').read_bytes())
    return h.hexdigest()

FREEZE = freeze_hash(ROOT)
IMAGE = 'solana-bot:contra-audit14'
print('[freeze]', FREEZE)

# ── bestaande app-config ophalen en bijwerken ──
r = api('app.query', [[['name', '=', 'solana-bot']], {'extra': {'retrieve_config': True}}])
config = r['result'][0]['config']
svc = config['services']['solana-bot']
svc['build']['args']['SOURCE_FREEZE_SHA256'] = FREEZE
svc['image'] = IMAGE

# ── config-wijzigingen (strategie) ──
env = svc.setdefault('environment', {})
env['MIN_AGE_MINUTES'] = '5'
env['MAX_AGE_MINUTES'] = '240'
env['CONTRA_MAX_BUY_PRESSURE'] = '0.55'
env['MIN_MOMENTUM_SCORE'] = '40'
env['MAX_HOLD_MINUTES'] = '30'
env['MAX_ENTRIES_PER_SCAN'] = '1'
# ENTRY_MODE blijft contra
env['ENTRY_MODE'] = 'contra'

u = api('app.update', ['solana-bot', {'custom_compose_config': config}])
print('[update] image:', svc.get('image'), '| job:', json.dumps(u.get('result'))[:60])
print('[env] MIN_AGE=', env['MIN_AGE_MINUTES'], '| MAX_AGE=', env['MAX_AGE_MINUTES'],
      '| BP=', env['CONTRA_MAX_BUY_PRESSURE'], '| SCORE=', env['MIN_MOMENTUM_SCORE'],
      '| HOLD=', env['MAX_HOLD_MINUTES'], '| TOPN=', env['MAX_ENTRIES_PER_SCAN'])