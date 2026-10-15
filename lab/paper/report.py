"""Daily paper report (Markdown + Parquet) from the journal.

Per family and variant: trades, net SOL per trade, win rate, fill rate and the difference to the
N2 shadow; the stop-rule counter (trades, days, GO / ADJUST / STOP, with a day-bootstrap CI from
5 days on); the 10 SOL capital simulation (addendum §6); heartbeat and feed gaps; the measured
latency of the live F7 triggers next to the simulated d.
"""

import json
import os
from collections import defaultdict

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from backtest.report import CAPITAL_SOL, MAX_POSITIONS, MIN_TRADES, _boot, capital_sim

MIN_DAYS_CI = 5


def _verdict(pnls_by_day, n, filled_by_day=None):
    """The stop rule (owner, 2026-10-10): net P&L per trade after all costs, from MIN_TRADES trades on.
    pnls_by_day holds every exit's P&L, failed entries' costs included; n and filled_by_day (per day)
    count the filled trades only, so a failed entry costs but is no trade (default: every exit)."""
    days = sorted(pnls_by_day)
    counts = filled_by_day if filled_by_day is not None else {d: len(v) for d, v in pnls_by_day.items()}
    total = sum(sum(v) for v in pnls_by_day.values())
    mean = total / n if n else None
    lo = hi = None
    if len(days) >= MIN_DAYS_CI:
        lo, hi = _boot(np.array([sum(pnls_by_day[d]) for d in days], dtype=float),
                       np.array([counts.get(d, 0) for d in days], dtype=float))
    if mean is None or n < MIN_TRADES:
        v = "too few trades"
    elif lo is not None and lo > 0:
        v = "GO"
    else:
        v = "ADJUST" if mean > 0 else "STOP"
    return mean, lo, hi, v


def stop_rule_inputs(exits):
    """(P&L per day of every exit, filled trades per day, filled trades): a failed entry (no tokens) costs
    its transaction but is no trade."""
    by_day, filled_by_day = defaultdict(list), defaultdict(int)
    for x in exits:
        by_day[x["day"]].append(x["pnl"])
        filled_by_day[x["day"]] += bool(x["tokens"])
    return by_day, filled_by_day, sum(filled_by_day.values())


def _p(xs, q):
    return int(round(np.percentile(xs, q))) if xs else None


def latency_lines(journal, day):
    """The day's live F7 triggers (each once: its variants and N2 signals share it): receive and
    segment close minus block time, median and p90, and how many were later than the simulated d.
    Measured, reporting only; a replay journal has no such rows."""
    trig = {}
    for r in journal.rows("signals", "day = ? AND family = 'F7' AND block_time IS NOT NULL", (day,)):
        trig.setdefault((r["key"], r["t"]), r)
    lines = ["", "## Latency (live F7 triggers, measured against the simulated d)", ""]
    if not trig:
        return lines + ["- no live trigger with arrival data this day (replay journals have none)"]
    xs = list(trig.values())
    lines.append(f"- triggers {len(xs)}; simulated d {_p([r['d_ms'] for r in xs], 50)} ms (median)")
    for label, col in (("receive - block time", "latency_ms"), ("segment close - block time", "segment_lag_ms")):
        v = [r[col] for r in xs if r[col] is not None]
        later = sum(1 for r in xs if r[col] is not None and r[col] > r["d_ms"])
        lines.append(f"- {label}: median {_p(v, 50)} ms, p90 {_p(v, 90)} ms; later than d: {later} of {len(v)}"
                     if v else f"- {label}: no data")
    return lines


def watchlist_lines(log_path, day):
    """The live watchlist at the end of `day` from watchlist-log.jsonl (one line per minute): pools
    tracked and archived, and how many were pruned and restored during the day."""
    from .engine import _day

    lines = ["", "## Live watchlist", ""]
    if not log_path or not os.path.exists(log_path):
        return lines + ["- no watchlist log"]
    before = last = None
    with open(log_path) as f:
        for raw in f:
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            d = _day(row["minute"] * 60)
            if d < day:
                before = row
            elif d == day:
                last = row
    if last is None:
        return lines + ["- no watchlist minute this day"]
    c, c0 = last.get("counts", {}), (before or {}).get("counts", {})
    if "tracked" not in c:
        return lines + [f"- end of day: {c.get('pools', 0)} pools on the list (no pruning counts in this log)"]

    def today(k):  # the totals count since the engine started: a restart during the day starts them at 0
        return c[k] - c0.get(k, 0) if c[k] >= c0.get(k, 0) else c[k]

    return lines + [f"- end of day: {c.get('pools', 0)} on the list, {c['tracked']} tracked, {c['archived']} archived; "
                    f"pruned {today('pruned_total')}, restored {today('restored_total')} this day"]


def farm_lines(journal, day):
    """How many of the day's live F7 triggers had a farm label from incomplete farmer history (before
    LiveFeed's complete_from): those labels are not comparable with the backtest's."""
    trig = {}
    for r in journal.rows("signals", "day = ? AND family = 'F7' AND farm_complete IS NOT NULL", (day,)):
        trig.setdefault((r["key"], r["t"]), r["farm_complete"])
    if not trig:
        return []
    n0 = sum(1 for v in trig.values() if not v)
    return ["", "## Farm labels", "",
            f"- {n0} of {len(trig)} triggers before the farmer history was complete (journal state farmer_seed)"]


def daily_report(journal, day, watch_log=None):
    """Write <journal>/<day>/report.md and report.parquet; returns the summary rows."""
    exits = journal.rows("exits")
    orders = journal.rows("orders")
    beats = journal.rows("heartbeat", "day = ?", (day,))
    today = [x for x in exits if x["day"] == day]
    rows = []
    groups = defaultdict(list)
    for x in today:
        groups[(x["family"], x["variant"])].append(x)
    ord_today = defaultdict(lambda: [0, 0])
    for o in orders:
        if o["day"] == day:
            ord_today[(o["family"], o["variant"])][0] += 1
            ord_today[(o["family"], o["variant"])][1] += o["status"] == "filled"
    for (fam, var), xs in sorted(groups.items()):
        filled = [x for x in xs if x["tokens"]]
        pnl = [x["pnl"] for x in xs]
        n_ord, n_fill = ord_today[(fam, var)]
        rows.append({"day": day, "family": fam, "variant": var, "trades": len(filled), "entry_failed": len(xs) - len(filled),
                     "net_sol_per_trade": sum(pnl) / len(xs) / 1e9 if xs else None,
                     "win_rate": float(np.mean([x["pnl"] > 0 for x in filled])) if filled else None,
                     "fill_rate": n_fill / n_ord if n_ord else None, "total_sol": sum(pnl) / 1e9})
    by = {(r["family"], r["variant"]): r for r in rows}
    for r in rows:
        n2 = by.get(("N2_" + r["family"], r["variant"]))
        r["vs_n2_sol_per_trade"] = (r["net_sol_per_trade"] - n2["net_sol_per_trade"]
                                    if n2 and r["net_sol_per_trade"] is not None and n2["net_sol_per_trade"] is not None else None)
    # Stop-rule counter and capital simulation over everything so far (traded families only).
    traded = [x for x in exits if not x["family"].startswith("N2_") and x["day"] <= day]
    by_day, filled_by_day, n_filled = stop_rule_inputs(traded)
    mean, lo, hi, verdict = _verdict(by_day, n_filled, filled_by_day)
    final, mdd, taken, skipped = capital_sim([(x["entry_slot"], x["exit_slot"], x["cost"], x["pnl"], not x["tokens"])
                                              for x in traded])
    gaps = [b for b in beats if b["note"]]
    lines = [f"# Paper report {day}", "",
             "| family | variant | trades | failed entries | net SOL/trade | win | fill rate | vs N2 SOL/trade |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        f = lambda x, fmt: "" if x is None else format(x, fmt)  # noqa: E731
        lines.append(f"| {r['family']} | {r['variant']} | {r['trades']} | {r['entry_failed']} | "
                     f"{f(r['net_sol_per_trade'], '+.4f')} | {f(r['win_rate'] and r['win_rate'] * 100, '.0f')}% | "
                     f"{f(r['fill_rate'] and r['fill_rate'] * 100, '.0f')}% | {f(r['vs_n2_sol_per_trade'], '+.4f')} |")
    ci = f"{lo / 1e9:+.4f} … {hi / 1e9:+.4f} SOL" if lo is not None else f"from {MIN_DAYS_CI} days"
    lines += ["", "## Stop-rule counter (all paper days so far)", "",
              f"- trades {n_filled} (failed entries {len(traded) - n_filled}, their cost included), days {len(by_day)}, "
              f"net {'' if mean is None else f'{mean / 1e9:+.4f}'} SOL/trade, "
              f"95% CI {ci}: **{verdict}**",
              "", f"## Capital simulation ({CAPITAL_SOL:g} SOL, at most {MAX_POSITIONS} positions)", "",
              f"- final {final / 1e9:.3f} SOL, max drawdown {mdd * 100:.1f}%, trades {taken}, skipped {skipped}",
              "", "## Heartbeat", "",
              f"- minutes with a heartbeat: {sum(1 for b in beats if not b['note'])}; feed gaps: {len(gaps)}"
              + (f" (largest {max(b['gap_slots'] for b in gaps)} slots)" if gaps else "")]
    lines += latency_lines(journal, day)
    lines += farm_lines(journal, day)
    if watch_log is not None:
        lines += watchlist_lines(watch_log, day)
    out = os.path.join(journal.dir, day)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "report.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    cols = ["day", "family", "variant", "trades", "entry_failed", "net_sol_per_trade", "win_rate", "fill_rate", "total_sol",
            "vs_n2_sol_per_trade"]
    pq.write_table(pa.table({c: [r.get(c) for r in rows] for c in cols}), os.path.join(out, "report.parquet"))
    return rows
