#!/usr/bin/env python3
"""Recreate solana-bot via STRUCTURED custom_compose_config (dict-vorm).
Vanmiddag bewezen werkend; de YAML-string-vorm faalde op secret-file mounts (ENOENT).
"""
import json, subprocess, os, sys, time

def api(method, params):
    env = dict(os.environ)
    env['TRUENAS_API_METHOD'] = method
    env['TRUENAS_API_PARAMS'] = json.dumps(params)
    r = subprocess.run(['node', 'truenas-wss-admin.mjs'], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise RuntimeError(f'API {method} exit {r.returncode}: {r.stdout[:300]} {r.stderr[:200]}')
    return json.loads(r.stdout)

config = json.load(open('/tmp/solana-config.json'))
# TrueNAS structured vorm: volumes gebruiken source/target/type (key-namen zoals de werkende config)
svc = config['services']['solana-bot']
print('[create-structured] image:', svc['image'], '| volumes:', len(svc['volumes']))

try:
    r = api('app.create', [{'app_name': 'solana-bot', 'custom_app': True, 'custom_compose_config': config}])
    print('[create-structured] result:', json.dumps(r.get('result'))[:120])
except RuntimeError as e:
    print('[create-structured] FOUT:', str(e)[:300])
    sys.exit(1)