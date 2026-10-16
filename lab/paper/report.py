"""Daily paper report (Markdown + Parquet) from the journal.

Per family and variant: trades, net SOL per trade, win rate, fill rate and the difference to the
N2 shadow; the stop-rule counter (trades, days, GO / ADJUST / STOP, with a day-bootstrap CI from
5 days on); the 10 SOL capital simulation (addendum §6); heartbeat and feed gaps; the measured
latency of the live F7 triggers next to the simulated d. With variant groups (journal state 'groups') the
stop-rule counter and the capital simulation count the primary group (K3) only; every further group (H) gets a
section per variant, a measurement and no test (research/week4-spec-paper-h4h5-2026-10-16.md §4). With a clock
start in the journal state (paper/clock.py) the stop-rule counter and the capital simulation count the trades entered
on or after that day, and the report shows the clock day and when the decision is due; without one they count every
paper day, a count without a decision.
"""

import json
import os
from collections import defaultdict

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from backtest.report import CAPITAL_SOL, MAX_POSITIONS, MIN_TRADES, _boot, capital_sim

# The selection and count are shared with the web app's Paper tab (lab/ui/paper.py): see paper/stoprule.py.
from .stoprule import (CLOCK_DAYS, CLOCK_KEY, CLOCK_LATEST, CLOCK_MAX_DAYS, GROUPS_KEY,  # noqa: F401 (re-exported)
                       clock_state, counted, decide, parse_clock, parse_groups, stop_rule_inputs)

MIN_DAYS_CI = 5
DRAWS, SEED, TRIM, EMPTY = 10_000, 7, 0.01, -0.99


def groups_of(journal):
    """(primary group's variants or None (every variant), {further group: [variants]}) from the journal's state."""
    return parse_groups(journal.state(GROUPS_KEY))


def clock_of(journal):
    """The clock start record ({date, reason, set_at[, previous]}) from the journal state, or None."""
    return parse_clock(journal.state(CLOCK_KEY))


def clock_lines(clock, day, n_filled, verdict):
    """The clock's state on `day`: not set, not started, its day, or the decision due."""
    st = clock_state(clock, day, n_filled, MIN_TRADES)
    if st["phase"] == "not_set":
        return ["- no clock start in the journal yet (python -m paper.clock): a count of every paper day, no decision"]
    head = f"- clock start {clock['date']} (set {clock['set_at']}; reason: {clock['reason']})"
    if st["phase"] == "not_started":
        return [head + ": not started, nothing counted yet"]
    n = st["clock_day"]
    if st["phase"] == "due" and n >= CLOCK_MAX_DAYS:
        now = f"clock day {n}: the {CLOCK_MAX_DAYS}-day limit, the decision is due: **{verdict}**"
    elif st["phase"] == "due":
        now = f"clock day {n}, {n_filled} trades: the decision is due: **{verdict}**"
    elif st["phase"] == "extended":
        now = f"clock day {n}, {n_filled} trades: it runs on until {MIN_TRADES} trades, at most day {CLOCK_MAX_DAYS}"
    else:
        now = f"clock day {n} of {CLOCK_DAYS}"
    return [head, f"- {now} (the decision after {CLOCK_DAYS} days with at least {MIN_TRADES} trades, else at "
                  f"{MIN_TRADES} trades, at most day {CLOCK_MAX_DAYS})"]


def _trim(x, frac=TRIM):
    x = np.sort(np.asarray(x, dtype=float))
    k = int(len(x) * frac)
    return float(x[k:len(x) - k].mean()) if len(x) > 2 * k else None


def _boot_days(x, draws=DRAWS, seed=SEED):
    """Mean of a daily series with a 95 % interval over a day bootstrap (as clean_h4)."""
    x = np.asarray(x, dtype=float)
    idx = np.random.default_rng(seed).integers(0, len(x), size=(draws, len(x)))
    m = x[idx].mean(1)
    return float(x.mean()), float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def arm_lines(journal, day, group, variants):
    """A further group's section, per variant: a measurement, no test (no stop rule)."""
    days = [d for d in journal.days() if d <= day]
    lines = ["", f"## Group {group}: measurement, no test", "",
             "| variant | trades | failed entries | net SOL/trade (trim 1 % / mean) | Σ SOL today | Σ SOL so far | win | fill rate | "
             "drains | exit reasons | take-profit proceeds/cost p5 / median / p95 | median hold (slots) |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    ci = []
    for v in variants:
        xs = [x for x in journal.rows("exits", "family = 'F7' AND variant = ? AND day <= ?", (v, day))]
        today = [x for x in xs if x["day"] == day]
        filled = [x for x in xs if x["tokens"]]
        orders = journal.rows("orders", "family = 'F7' AND variant = ? AND day <= ?", (v, day))
        n_fill = sum(o["status"] == "filled" for o in orders)
        rets = [x["ret"] for x in filled if x["ret"] is not None]
        per_trade = [x["pnl"] / 1e9 for x in xs]
        reasons = {}
        for x in xs:
            k = "failed entry" if not x["tokens"] else x["exit_reason"]
            reasons[k] = reasons.get(k, 0) + 1
        tp = sorted(x["proceeds"] / x["cost"] for x in filled if x["exit_reason"] == "take_profit" and x["cost"])
        holds = [x["exit_slot"] - x["entry_slot"] for x in filled if x["exit_slot"] is not None and x["entry_slot"] is not None]
        f = lambda x, fmt: "–" if x is None else format(x, fmt)  # noqa: E731
        tpq = " / ".join(f"{np.percentile(tp, q):.2f}" for q in (5, 50, 95)) if tp else "–"
        lines.append(f"| {v} | {len(filled)} | {len(xs) - len(filled)} | {f(_trim(per_trade), '+.4f')} / "
                     f"{f(float(np.mean(per_trade)) if per_trade else None, '+.4f')} | {sum(x['pnl'] for x in today) / 1e9:+.3f} | "
                     f"{sum(per_trade):+.3f} | {f(float(np.mean([r > 0 for r in rets])) * 100 if rets else None, '.0f')}% | "
                     f"{f(n_fill / len(orders) * 100 if orders else None, '.0f')}% | "
                     f"{f(float(np.mean([r <= EMPTY for r in rets])) * 100 if rets else None, '.0f')}% | "
                     f"{', '.join(f'{k} {n}' for k, n in sorted(reasons.items())) or '–'} | {tpq} | "
                     f"{f(float(np.median(holds)) if holds else None, '.0f')} |")
        by_day = {d: sum(x["pnl"] for x in xs if x["day"] == d) / 1e9 for d in days}
        with_pos = sum(1 for d in days if any(x["day"] == d for x in xs))
        if with_pos >= MIN_DAYS_CI:
            m, lo, hi = _boot_days([by_day[d] for d in days])
            ci.append(f"- {v}: Σ net SOL per UTC day {m:+.3f}, 95 % interval {lo:+.3f} … {hi:+.3f} "
                      f"({len(days)} days, {with_pos} with a position; day bootstrap {DRAWS:,}, seed {SEED})")
        else:
            ci.append(f"- {v}: {with_pos} days with a position (the day interval from {MIN_DAYS_CI} on)")
    return lines + ["", "Day series (descriptive, H4's form; no stop rule, no decision):", *ci]


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
    return mean, lo, hi, decide(mean, lo, n, MIN_TRADES)


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
    # Stop-rule counter and capital simulation over everything so far (traded families only; with groups the
    # primary group's variants only: K3's stop rule); with a clock start, the trades entered on or after its day.
    primary, further = groups_of(journal)
    clock = clock_of(journal)
    traded = counted(exits, orders, day, primary, clock)
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
    since = f"from the clock start {clock['date']}" if clock else "all paper days so far"
    lines += ["", f"## Stop-rule counter ({since}" + (f"; {', '.join(sorted(primary))} only" if primary else "") + ")", "",
              *clock_lines(clock, day, n_filled, verdict),
              f"- trades {n_filled} (failed entries {len(traded) - n_filled}, their cost included), days {len(by_day)}, "
              f"net {'' if mean is None else f'{mean / 1e9:+.4f}'} SOL/trade, "
              f"95% CI {ci}: **{verdict}**",
              "", f"## Capital simulation ({CAPITAL_SOL:g} SOL, at most {MAX_POSITIONS} positions; {since})", "",
              f"- final {final / 1e9:.3f} SOL, max drawdown {mdd * 100:.1f}%, trades {taken}, skipped {skipped}",
              "", "## Heartbeat", "",
              f"- minutes with a heartbeat: {sum(1 for b in beats if not b['note'])}; feed gaps: {len(gaps)}"
              + (f" (largest {max(b['gap_slots'] for b in gaps)} slots)" if gaps else "")]
    lines += latency_lines(journal, day)
    lines += farm_lines(journal, day)
    for group, variants in sorted(further.items()):
        lines += arm_lines(journal, day, group, variants)
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
