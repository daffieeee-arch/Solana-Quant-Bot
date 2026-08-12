#!/usr/bin/env python3
"""Update solana-bot image zonder de geheime RPC/WS/token-waarden aan te raken."""
import json, subprocess, os, sys

def api(m, p):
    e = dict(os.environ)
    e['TRUENAS_API_METHOD'] = m
    e['TRUENAS_API_PARAMS'] = json.dumps(p)
    r = subprocess.run(['node', 'truenas-wss-admin.mjs'], capture_output=True, text=True, env=e)
    if r.returncode != 0:
        raise RuntimeError(r.stdout[:200] + r.stderr[:100])
    return json.loads(r.stdout)

FREEZE = 'b729483c75f3c2244c4102b41e96da5316b7c036f7aa9d49a35aa5d53732ac1c'
IMAGE = 'solana-bot:contra-triton-4'

r = api('app.query', [[['name', '=', 'solana-bot']], {'extra': {'retrieve_config': True}}])
config = r['result'][0]['config']
svc = config['services']['solana-bot']
env = svc['environment']
svc['build']['args']['SOURCE_FREEZE_SHA256'] = FREEZE
svc['image'] = IMAGE
print('[update] image:', svc.get('image'), '| freeze:', FREEZE[:12], '...')
print('[update] RPC_HTTP is Triton:', 'johnb' in env.get('RPC_HTTP_ENDPOINT', ''))
u = api('app.update', ['solana-bot', {'custom_compose_config': config}])
print('[update] result:', json.dumps(u.get('result'))[:80])