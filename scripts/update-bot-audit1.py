#!/usr/bin/env python3
"""Update solana-bot naar de audit-fixbatch (TRITON-ONLY + prijs/curve/exits-fixes)."""
import json, subprocess, os

def api(m, p):
    e = dict(os.environ)
    e['TRUENAS_API_METHOD'] = m
    e['TRUENAS_API_PARAMS'] = json.dumps(p)
    r = subprocess.run(['node', 'truenas-wss-admin.mjs'], capture_output=True, text=True, env=e)
    if r.returncode != 0:
        raise RuntimeError(r.stdout[:200] + r.stderr[:100])
    return json.loads(r.stdout)

FREEZE = 'd5d301faa7040642125e488797e8709961cd707847c9705fc96dbf3500f10909'
IMAGE = 'solana-bot:contra-audit1'

r = api('app.query', [[['name', '=', 'solana-bot']], {'extra': {'retrieve_config': True}}])
config = r['result'][0]['config']
svc = config['services']['solana-bot']
env = svc['environment']
svc['build']['args']['SOURCE_FREEZE_SHA256'] = FREEZE
svc['image'] = IMAGE
print('[update] image:', svc.get('image'), '| freeze:', FREEZE[:12], '...')
print('[update] RPC_HTTP is Triton (johnb):', 'johnb' in env.get('RPC_HTTP_ENDPOINT', ''))
print('[update] ENTRY_MODE:', env.get('ENTRY_MODE'), '| SOL_PRICE:', env.get('SOL_PRICE_USD'))
u = api('app.update', ['solana-bot', {'custom_compose_config': config}])
print('[update] result:', json.dumps(u.get('result'))[:80])