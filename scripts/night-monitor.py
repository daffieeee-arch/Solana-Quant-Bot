#!/usr/bin/env python3
"""Nachtmonitor voor solana-bot contra live-fase.
Ophaalt: geyser-liveness, decision-reasons (contra vs legacy), entries, P&L,
equity, open posities, crashes. Outputs compact voor cron-delivery.
"""
import json, urllib.request, datetime, sys, os

BASE = os.environ.get('BOT_BASE', 'http://100.79.221.55:3000')

def get(path, timeout=10):
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        return {'__error': str(e)[:150]}

def main():
    now = datetime.datetime.utcnow().strftime('%H:%MZ')
    print(f'[{now}] solana-bot nachtmonitor')
    dbg = get('/api/debug')
    st = get('/api/status')

    # 1. Geyser / discovery levensvatbaarheid
    if '__error' in dbg:
        print('❌ DEBUG ENDPOINT DOWN:', dbg['__error'])
    else:
        disc = dbg.get('discovery', '')
        suberr = dbg.get('usage', {}).get('subErrors') if isinstance(dbg.get('usage'), dict) else None
        # discovery bevat "subErrors(N)"
        import re
        m = re.search(r'subErrors\((\d+)\)', str(disc))
        suberr = int(m.group(1)) if m else suberr
        # triton event count in discovery
        m2 = re.search(r'events\(([^)]*)\)', str(disc))
        events = m2.group(1) if m2 else '?'
        print(f'discovery: {str(disc)[:120]}')
        print(f'   events={events} subErrors={suberr}')
        if suberr:
            print(f'⚠️  {suberr} stream substorm-errors — kijk naar geyser reconnect')

    # 2. Decision-reasons (contra live?)
    reasons = {}
    entries = []
    recent_mint = None
    if '__error' in st:
        print('❌ STATUS ENDPOINT DOWN:', st['__error'])
    else:
        rd = st.get('recentDecisions') or []
        for d in rd:
            k = d.get('reason') or '?'
            reasons[k] = reasons.get(k, 0) + 1
            if d.get('action') and d.get('action') not in ('reject',):
                entries.append(d)
        sm = (st.get('markPricesByMint') or {})
        recent_mint = len(sm)
        # contra-specifiek
        contra = sum(v for k, v in reasons.items() if k.startswith('contra_'))
        legacy_surge = reasons.get('buy_surge_insufficient', 0)
        print('decision-reasons (n=%d):' % len(rd))
        for k, v in sorted(reasons.items(), key=lambda x: -x[1])[:8]:
            mark = '' 
            print(f'   {k}: {v}{mark}')
        if contra > 0:
            print(f'✅ contra-mode active: {contra} contra-rejections')
        elif legacy_surge > 0 and len(rd) > 0:
            print(f'⏳ nog legacy surge-reasons ({legacy_surge}) — buffer vult zich..')
        print(f'   market-tracked mints: {recent_mint}')

        # 3. P&L
        for k in ('totalEquity', 'equity', 'availableSol', 'netPnl', 'netPnlSol', 'winRate', 'historyCount', 'closedTradeHistory'):
            if k in st and st[k] is not None:
                v = st[k]
                if k == 'closedTradeHistory':
                    v = '%d' % (len(v) if isinstance(v, list) else v)
                print(f'   {k}: {v}')
    print('EINDE')

if __name__ == '__main__':
    main()