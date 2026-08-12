#!/usr/bin/env python3
"""Silent nacht-watchdog voor solana-bot (contra live-fase).
Alleen stdout uit bij AFWIJKINGEN, anders leeg → cron levert niets (stil).
File-based state zodat we eenmalige gebeurtenissen (contra-actief, nieuwe entry)
niet herhalen en equity-daling signaleren.
Output (indien afwijkend): korte melding met het afwijkende punt.
"""
import json, urllib.request, datetime, os, re

BASE = os.environ.get('BOT_BASE', 'http://100.79.221.55:3000')
STATE = '/tmp/bot-watchdog-state.json'

def get(path, timeout=10):
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        return {'__error': str(e)[:150]}

def load_state():
    try:
        with open(STATE) as f: return json.load(f)
    except Exception: return {}

def save_state(s):
    with open(STATE, 'w') as f: json.dump(s, f)

def main():
    st = load_state()
    msgs = []
    now = datetime.datetime.utcnow().isoformat()

    dbg = get('/api/debug')
    if '__error' in dbg:
        msgs.append(f'🔴 DEBUG DOWN: {dbg["__error"]}')
    else:
        disc = str(dbg.get('discovery', ''))
        m = re.search(r'subErrors\((\d+)\)', disc)
        if m and int(m.group(1)) > 0:
            msgs.append(f'⚠️ {m.group(1)} subErrors (geyser reconnect nodig?)')

    stp = get('/api/status')
    if '__error' in stp:
        msgs.append(f'🔴 STATUS DOWN: {stp["__error"]}')
    else:
        rd = stp.get('recentDecisions') or []
        reasons = {}
        entries = 0
        for d in rd:
            k = d.get('reason') or '?'
            reasons[k] = reasons.get(k, 0) + 1
            if d.get('action') and d.get('action') not in ('reject',):
                entries += 1
        contra = sum(v for k, v in reasons.items() if k.startswith('contra_'))

        # contra-actief: eenmalig melden
        if contra > 0 and not st.get('contra_seen'):
            msgs.append(f'✅ CONTRA-MODE LIVE bevestigd: {contra} contra-rejections')
            st['contra_seen'] = True
        # legacy surge verdwenen: eenmalig positief
        if st.get('contra_seen') and reasons.get('buy_surge_insufficient', 0) == 0 and not st.get('legacy_gone'):
            msgs.append('↩️ Legacy surge-reasons verdwenen — buffer volledig contra')
            st['legacy_gone'] = True
        # entries
        if entries > 0 and st.get('last_entries', 0) != entries:
            msgs.append(f'📈 {entries} contra-entries zichtbaar in recentDecisions')
            st['last_entries'] = entries
        # equity / P&L daling
        eq = None
        for k in ('totalEquity', 'equity'):
            if k in stp and isinstance(stp[k], (int, float)):
                eq = stp[k]; break
        if eq is not None:
            prev = st.get('equity')
            if prev is not None and eq < prev * 0.95:
                msgs.append(f'📉 equity gedaald: {prev:.4f} → {eq:.4f} SOL')
            st['equity'] = eq

    st['_last'] = now
    save_state(st)

    if msgs:
        print(f"[{now}] solana-bot watchdog")
        for m in msgs:
            print(m)
    # else: geen output → stil (geen cron-delivery)

if __name__ == '__main__':
    main()