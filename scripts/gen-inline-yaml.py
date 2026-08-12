#!/usr/bin/env python3
"""Genereer reinstall-compose-inline.yaml met ECHTE inline-secret-waarden
(geen _FILE refs, geen file-mounts) zodat de bot niet meer crasht met
'RPC_HTTP_ENDPOINT_FILE could not be read'."""
import json, subprocess, os, urllib.request, ssl, re

def api(method, params):
    env = dict(os.environ)
    env['TRUENAS_API_METHOD'] = method
    env['TRUENAS_API_PARAMS'] = json.dumps(params)
    r = subprocess.run(['node', 'truenas-wss-admin.mjs'], capture_output=True, text=True, env=env, check=True)
    return json.loads(r.stdout)

def download_secret(path):
    dl = api('core.download', ['filesystem.get', [path], 's', True])
    rel = dl['result'][1]
    data = urllib.request.urlopen(
        urllib.request.Request('https://192.168.1.234' + rel),
        timeout=15, context=ssl._create_unverified_context()).read()
    return data.decode().strip()

SECRETS = {
    'RPC_HTTP_ENDPOINT': '/mnt/fastdisk/ai/hermes/secrets/solana-paper-scanner/rpc-http-endpoint',
    'RPC_WS_ENDPOINT': '/mnt/fastdisk/ai/hermes/secrets/solana-paper-scanner/rpc-ws-endpoint',
    'TRITON_ENDPOINT': '/mnt/fastdisk/ai/hermes/secrets/solana-paper-scanner/triton-endpoint',
    'TRITON_TOKEN': '/mnt/fastdisk/ai/hermes/secrets/solana-paper-scanner/triton-token',
}
vals = {}
for k, p in SECRETS.items():
    vals[k] = download_secret(p)
    print(f'[secret] {k}: len={len(vals[k])}')

# Basis-YAML (zonder secret-mounts in volumes, met inline env)
yaml_out = f'''services:
  solana-bot:
    image: solana-bot:contra-final
    build:
      context: /mnt/fastdisk/ai/hermes/solana-paper-scanner
      dockerfile: Dockerfile
      args:
        SOURCE_FREEZE_SHA256: ee0f27d2c9cedc9ddef690cf2f3a1d53c4ed7f0b24fa9e77ea12588ca976a689
    pull_policy: never
    command: ["node", "/app/dist/main.js"]
    restart: unless-stopped
    read_only: true
    init: true
    security_opt: ["no-new-privileges:true"]
    cap_drop: ["ALL"]
    cpus: 1
    mem_limit: 512M
    pids_limit: 200
    tmpfs: ["/tmp"]
    user: "10000:10000"
    environment:
      MODE: paper
      PAPER_STARTING_SOL: "10"
      MAX_POSITION_SOL: "0.25"
      MAX_CONCURRENT_POSITIONS: "4"
      MAX_DAILY_LOSS_SOL: "0.5"
      MIN_LIQUIDITY_USD: "10000"
      MAX_LIQUIDITY_USD: "50000000"
      MIN_AGE_MINUTES: "20"
      MAX_AGE_MINUTES: "259200"
      MIN_PRICE_CHANGE_M5_PERCENT: "1.5"
      MIN_VOLUME_M5_USD: "1000"
      MIN_MOMENTUM_SCORE: "50"
      MIN_BUY_SURGE_COUNT: "10"
      MIN_BUY_PRESSURE: "0.45"
      STOP_LOSS_PERCENT: "12"
      TAKE_PROFIT_PERCENT: "8"
      TRAILING_STOP_PERCENT: "5"
      MAX_HOLD_MINUTES: "15"
      SIMULATED_SLIPPAGE_BPS: "150"
      SIMULATED_FEE_BPS: "100"
      SCAN_INTERVAL_SECONDS: "30"
      MAX_CYCLES: "0"
      STRICT_RISK_MODE: "false"
      DATA_DIR: /data
      DASHBOARD_ENABLED: "true"
      DASHBOARD_PORT: "3000"
      MIN_STOP_LOSS_PERCENT: "5"
      MAX_STOP_LOSS_PERCENT: "15"
      STOP_VOLATILITY_MULTIPLIER: "1"
      BREAKEVEN_TRIGGER_PERCENT: "8"
      MIN_PROFIT_FOR_CONTINUE_PERCENT: "2"
      POSITION_SCORE_DIVISOR: "80"
      LIQUIDITY_POSITION_FRACTION: "0.02"
      MIN_WHALE_TX_SOL: "1"
      SOL_PRICE_USD: "74"
      RPC_HTTP_ENDPOINT: {json.dumps(vals['RPC_HTTP_ENDPOINT'])}
      RPC_WS_ENDPOINT: {json.dumps(vals['RPC_WS_ENDPOINT'])}
      TRITON_ENDPOINT: {json.dumps(vals['TRITON_ENDPOINT'])}
      TRITON_TOKEN: {json.dumps(vals['TRITON_TOKEN'])}
      TRITON_STREAM: geyser
      ENTRY_MODE: contra
      WHALE_WALLETS: ""
    volumes:
      - source: /mnt/fastdisk/ai/hermes/solana-paper-scanner/data-bot-v5
        target: /data
        type: bind
    ports:
      - target: 3000
        host_ip: 100.79.221.55
        published: 3000
        protocol: tcp
      - target: 3000
        host_ip: 192.168.1.234
        published: 3000
        protocol: tcp
'''
open('reinstall-compose-inline.yaml', 'w').write(yaml_out)
print('[ok] reinstall-compose-inline.yaml geschreven met inline-secrets')