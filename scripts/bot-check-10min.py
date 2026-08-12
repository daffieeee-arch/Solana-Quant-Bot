#!/usr/bin/env python3
"""Wacht ~10 min, dan bot-check: /api/debug + /api/status samenvatting voor de geyser-deploy."""
import time, json, urllib.request, sys

time.sleep(600)
BASE = 'http://100.79.221.55:3000'

def get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        return {'error': str(e)[:120]}

dbg = get('/api/debug')
st = get('/api/status')

print('=== GEYSER-DEPLOY CHECK (+10 min) ===')
if 'error' in dbg:
    print('debug error:', dbg['error'])
else:
    print('discovery:', dbg.get('discovery', '?')[:160])
    print('disabledProviders:', dbg.get('disabledProviders'))
    u = dbg.get('usage', {})
    print('vixenEvents:', json.dumps(u.get('vixenEvents', {})))
    print('jsonRpcCalls:', json.dumps(u.get('jsonRpcCalls', {})))
    print('scanCount:', dbg.get('scanCount'), '| birthUptimeMs:', dbg.get('birthUptimeMs'))
if 'error' in st:
    print('status error:', st['error'])
else:
    mp = st.get('markPricesByMint') or {}
    print('markt mints:', len(mp))
    rd = st.get('recentDecisions') or []
    print('recentDecisions:', len(rd))
    for d in rd[:4]:
        print('  ', d.get('symbol'), d.get('reason', '')[:60], d.get('score'))
    total = st.get('totalEquity') or st.get('equity')
    pnl = st.get('netPnl') or st.get('netPnlSol')
    print('equity:', total, '| netPnl:', pnl)
print('=== EINDE CHECK ===')