#!/usr/bin/env python3
"""Trade monitor — check dashboard, log patterns, flag issues."""
import json, urllib.request, sys
from datetime import datetime, timezone

DASHBOARD = "http://192.168.1.234:3000/api/dashboard-data"
MAX_STOP_LOSS_RATIO = 0.8  # Alert if >80% exits are stop_loss
MIN_WIN_RATE = 0.2  # Alert if win rate < 20%

try:
    with urllib.request.urlopen(DASHBOARD, timeout=10) as resp:
        d = json.load(resp)
except Exception as e:
    print(json.dumps({"event": "monitor_error", "message": str(e)}))
    sys.exit(0)

u = d['summary']; s = d['scanner']
trades = d['closedTrades'] or []
positions = d['positions'] or []
now = datetime.now(timezone.utc).isoformat()

# Count exit reasons
reasons = {}
for t in trades:
    r = t.get('reason', 'unknown')
    reasons[r] = reasons.get(r, 0) + 1

stop_losses = reasons.get('stop_loss', 0)
trailing = reasons.get('trailing_stop', 0)
time_stops = reasons.get('time_stop', 0)
total_exits = stop_losses + trailing + time_stops or 1

report = {
    "event": "trade_monitor",
    "at": now,
    "equity_sol": u['totalEquitySol'],
    "pnl_sol": u['realizedPnlSol'],
    "open_pos": u['openPositions'],
    "win_rate_pct": u['winRate'],
    "scans": s['scans'],
    "candidates": s['candidatesFound'],
    "total_closed": len(trades),
    "exit_reasons": {
        "stop_loss": stop_losses,
        "trailing_stop": trailing,
        "time_stop": time_stops,
    },
    "alerts": [],
}

# Alert 1: too many stop losses
if stop_losses / total_exits > MAX_STOP_LOSS_RATIO:
    report['alerts'].append(f"HIGH_STOP_LOSS_RATE: {stop_losses}/{total_exits}={stop_losses/total_exits*100:.0f}% stops — consider widening stops")

# Alert 2: no trailing exits (never hitting take profit)
if trailing == 0 and total_exits > 5:
    report['alerts'].append("NO_TRAILING_EXITS: never hit take-profit — lower TP% or widen stops to survive dip")

# Alert 3: low win rate
if u['winRate'] < MIN_WIN_RATE * 100:
    report['alerts'].append(f"LOW_WIN_RATE: {u['winRate']}% — need better entry timing or wider stops")

# Alert 4: consecutive losses pattern
if total_exits >= 3:
    recent = trades[:3]
    all_loss = all(t['pnlSol'] < 0 for t in recent)
    if all_loss:
        report['alerts'].append("CONSECUTIVE_LOSSES: last 3 trades all negative")

# Alert 5: equity draining
if u['totalEquitySol'] < 9.5:
    report['alerts'].append(f"LOW_EQUITY: {u['totalEquitySol']}SOL from 10SOL start")

print(json.dumps(report))
