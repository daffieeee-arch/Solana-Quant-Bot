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
# Unieke immutable tag per build: SHA-suffix forceert een nieuwe lokale build
# (vaste tag + pull_policy:never herbouwde NIET — running sha bleef verouderd).
IMAGE = f'solana-bot:contra-audit16-offline-pump-{subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()[:10]}'
print('[freeze]', FREEZE)

# Fase-W/Provenance: immutable Git SHA van de deploy-tip (unieke rollback-marker).
GIT_SHA = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True, cwd=ROOT).stdout.strip()[:40] or 'unknown'
print('[git]', GIT_SHA)

# ── bestaande app-config ophalen en bijwerken ──
r = api('app.query', [[['name', '=', 'solana-bot']], {'extra': {'retrieve_config': True}}])
config = r['result'][0]['config']
svc = config['services']['solana-bot']
svc['build']['args']['SOURCE_FREEZE_SHA256'] = FREEZE
svc['build']['args']['SOURCE_GIT_SHA'] = GIT_SHA
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
# Fase-LIVE: SHADOW-ONLY — evalueert echte candidates tegen het volledige
# MarketIdentity-entry-contract en rapporteert WOULD_ACCEPT/WOULD_REJECT,
# maar ENFORCEERT de gate NIET (geen nieuwe entry-blokkering, geen entries
# uitsluitend door shadow). Observability via /api/debug.shadowMetrics.
env['ENTRY_SHADOW_MODE'] = '1'
# Systematic-debugging: capture parsePumpTxn failures (bounded, disabled-by-default via env)
env['DEBUG_PARSE_PUMP'] = '1'
# ZERO-COST mode (2026-08-15): Triton prepaid balance = $0 → géén live Triton-consumptie.
# TRITON_LIVE_ENABLED=false default in development; expliciete manual unlock voor reactivatie.
env['TRITON_LIVE_ENABLED'] = 'false'

# ── SECURITY P0 (audit C1/H3): strip inline secrets vóór de push ──
# De live config bevatte TRITON_TOKEN/RPC_* inline (plaintext leesbaar via
# app-query). De beoogde architectuur is _FILE-mounts (docker secrets op de host).
# Deze stap verwijdert de inline secret-waarden EN stelt de _FILE-varianten in;
# via volumes/secrets blijft de bot de waarden uit /run/secrets lezen.
# NB: de bestandsmounts zelf worden door TrueNAS-UI/deployer toegevoegd (geen
# inline path-leaks in de env); hier worden alleen env-keys gestript.
SECRET_INLINE_KEYS = ('TRITON_TOKEN', 'TRITON_ENDPOINT', 'RPC_HTTP_ENDPOINT', 'RPC_WS_ENDPOINT', 'BIRDEYE_API_KEY')
for k in SECRET_INLINE_KEYS:
    env.pop(k, None)
FILE_KEYS = {
    'RPC_HTTP_ENDPOINT_FILE': '/run/secrets/rpc-http-endpoint',
    'RPC_WS_ENDPOINT_FILE': '/run/secrets/rpc-ws-endpoint',
    'TRITON_ENDPOINT_FILE': '/run/secrets/triton-endpoint',
    'TRITON_TOKEN_FILE': '/run/secrets/triton-token',
}
env.update(FILE_KEYS)

# ── SECURITY P0: voeg de secret-volume-mounts toe (host secret-file → /run/secrets)
# Zonder deze volumes vindt de container de _FILE-paden niet en blijft Triton
# fail-closed disabled. Mount host /mnt/fastdisk/ai/hermes/secrets/solana-paper-scanner/<n>
# naar /run/secrets/<n> read-only, uid/gid 10001 (de bot-container-user).
SECRET_SRC = '/mnt/fastdisk/ai/hermes/secrets/solana-paper-scanner'
secrets_map = {
    'rpc-http-endpoint': 'rpc-http-endpoint',
    'rpc-ws-endpoint': 'rpc-ws-endpoint',
    'triton-endpoint': 'triton-endpoint',
    'triton-token': 'triton-token',
}
vols = svc.setdefault('volumes', [])
# verwijder eventuele bestaande /run/secrets volume-mounts (idempotent)
vols[:] = [v for v in vols if not (isinstance(v, dict) and str(v.get('target', '')).startswith('/run/secrets/'))]
for src_name in secrets_map:
    # TrueNAS-apps volume-format (compose bind-mount), één per secret
    vols.append({
        'type': 'bind',
        'source': f'{SECRET_SRC}/{src_name}',
        'target': f'/run/secrets/{src_name}',
        'read_only': True,
    })

u = api('app.update', ['solana-bot', {'custom_compose_config': config}])
print('[update] image:', svc.get('image'), '| job:', json.dumps(u.get('result'))[:60])
print('[env] MIN_AGE=', env['MIN_AGE_MINUTES'], '| MAX_AGE=', env['MAX_AGE_MINUTES'],
      '| BP=', env['CONTRA_MAX_BUY_PRESSURE'], '| SCORE=', env['MIN_MOMENTUM_SCORE'],
      '| HOLD=', env['MAX_HOLD_MINUTES'], '| TOPN=', env['MAX_ENTRIES_PER_SCAN'])