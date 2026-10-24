"""Daily paper report (Markdown + Parquet) from the journal.

Per family and variant: trades, net SOL per trade, win rate, fill rate and the difference to the
N2 shadow; the stop-rule counter (trades, days, GO / ADJUST / STOP once there are 200 filled trades and 5 trading
days for the day-bootstrap CI, paper/stoprule.py), with what the verdict rests on and what stays uncertain; the
10 SOL capital simulation (addendum §6); heartbeat and feed gaps; the measured
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

from backtest.report import CAPITAL_SOL, MAX_POSITIONS, capital_sim

from .restart import RESTARTS_KEY, restart_lines

# The selection, count, bootstrap and verdict are shared with the web app's Paper tab (lab/ui/paper.py): see
# paper/stoprule.py.
from .stoprule import (CLOCK_DAYS, CLOCK_KEY, CLOCK_LATEST, CLOCK_MAX_DAYS, COSTS_KEY, DRAWS, FEED_KEY,  # noqa: F401
                       GROUPS_KEY, MIN_DAYS_CI, MIN_TRADES, SEED, assessment, clock_state, counted, feed_of,
                       SETTLE_RULE_KEY, parse_clock, parse_groups, primary_group, settle_rule_of, stop_rule_inputs, verdict)

TRIM, EMPTY = 0.01, -0.99


def groups_of(journal):
    """(primary group's variants or None (every variant), {further group: [variants]}) from the journal's state."""
    return parse_groups(journal.state(GROUPS_KEY))


def clock_of(journal):
    """The clock start record ({date, reason, set_at[, previous]}) from the journal state, or None."""
    return parse_clock(journal.state(CLOCK_KEY))


def clock_lines(clock, day, n_filled, verdict_):
    """The clock's state on `day`: not set, not started, its day, or the verdict there."""
    st = clock_state(clock, day, verdict_)
    if st["phase"] == "not_set":
        return ["- no clock start in the journal yet (python -m paper.clock): a count of every paper day, no decision"]
    head = f"- clock start {clock['date']} (set {clock['set_at']}; reason: {clock['reason']})"
    if st["phase"] == "not_started":
        return [head + ": not started, nothing counted yet"]
    n = st["clock_day"]
    if st["limit"]:
        now = f"clock day {n}: the {CLOCK_MAX_DAYS}-day limit, the decision is due: **{verdict_}**"
    elif st["phase"] == "due":
        now = f"clock day {n}, {n_filled} trades: the verdict is there: **{verdict_}**"
    else:
        now = f"clock day {n}, {n_filled} trades: no verdict yet"
    return [head, f"- {now} (the verdict from {MIN_TRADES} filled trades and {MIN_DAYS_CI} trading days on, at most "
                  f"day {CLOCK_MAX_DAYS})"]


def assessment_lines(a):
    """The verdict's basis, assumptions and uncertainty (stoprule.assessment) as report lines."""
    b = a["basis"]
    sol = lambda x: f"{x / 1e9:+.4f}"  # noqa: E731
    ci = f"{sol(b['ci95_lamports'][0])} … {sol(b['ci95_lamports'][1])}" if b["ci95_lamports"] else "–"
    lines = ["", "### What the verdict rests on", "",
             f"- {b['filled_trades']} filled trades (failed entries {b['failed_entries']}), {b['trading_days']} trading days"
             + (f", clock day {b['clock_day']}" if b["clock_day"] else "") + f"; net {sol(b['mean_lamports'])} SOL per "
             f"filled trade, 95 % interval {ci} SOL (day bootstrap, {b['draws']:,} draws, seed {b['seed']})"]
    c = b["costs_lamports"]
    if c:
        expected = set(b.get("costs_expected") or ())
        lines.append("- costs beyond the fill prices (SOL): " + ", ".join(
            f"{k.replace('_', ' ')}{' (expected)' if k in expected else ''} {v / 1e9:.4f}" for k, v in c.items())
                     + "; venue fees are in the fill prices (each pool's and curve's own fee rates), not split out")
    else:
        lines.append("- costs: the journal has no cost scenario (an engine from before 2026-10-16)")
    lines += ["", "Model assumptions:", *(f"- {x['text']}" for x in a["assumptions"]),
              "", "What stays uncertain:", *(f"- {x['text']}" for x in a["uncertainty"]),
              "", f"**{a['not_permission']}**"]
    return lines


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
    """The stop rule's (mean, lo, hi, verdict) in lamports: stoprule.verdict, the Paper tab's too."""
    return verdict(pnls_by_day, n, filled_by_day)


def cost_scenario(journal):
    """The engine's cost scenario (state 'cost_scenario'); for a journal from before it, the base scenario of the
    defaults, which the engine has always used."""
    raw = journal.state(COSTS_KEY)
    if raw:
        return json.loads(raw)
    from dataclasses import asdict

    from backtest.costs import Scenario
    from backtest.store import load_defaults

    return asdict(Scenario.from_cfg("base", load_defaults()))


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


def _day_at(journal, slot):
    """The UTC day of the first heartbeat at or after `slot` (None when the journal has none yet)."""
    if slot is None:
        return None
    r = journal.db.execute("SELECT day FROM heartbeat WHERE last_slot >= ? AND note = '' ORDER BY minute LIMIT 1", [slot]).fetchone()
    return r[0] if r else None


def seed_lines(journal):
    """The farmer seed of every start (journal state farmer_seeds; LAB review B6, S1).
    - Every start seeds the farmer history from the store at its first live slot; a restart replays the segments of
      the last 36 h before the journal's high-water mark (paper/restart.py), so its labels are complete 24 h after
      its first slot (complete_from).
    - Up to the HWM a restart writes no rows and takes the journal's decisions. Only signals after the HWM and before
      complete_from are journaled with incomplete history (farm_complete 0, group H 'skipped, farm history
      incomplete'): when complete_from lies after the HWM, i.e. after an outage of more than ~1.5 days.
    These lines name every start's window, and for a restart (state 'restarts': its HWM) whether that happened."""
    raw = journal.state("farmer_seeds")
    if not raw:
        return []
    restarts = json.loads(journal.state(RESTARTS_KEY) or "[]")
    lines = ["", "## Farmer seed per start", ""]
    for i, seed in enumerate(json.loads(raw), 1):
        a, b = seed.get("first_live_slot"), seed.get("complete_from")
        line = (f"- start {i}: first live slot {a} ({_day_at(journal, a) or '?'}), farm labels complete from slot {b} "
                f"({_day_at(journal, b) or 'not reached yet'})")
        # This start's restart record: the one whose replay began in its seed window.
        rec = next((r for r in restarts if a is not None and b is not None and r.get("first_slot") is not None
                    and a <= r["first_slot"] < b), None)
        if rec is not None and b > rec["hwm"]:
            line += (f"; a restart: the signals of slots [{rec['hwm']}, {b}), after the journal's high-water mark, are "
                     "journaled with incomplete farmer history (farm_complete 0, group H skipped)")
        elif rec is not None:
            line += f"; a restart: complete before the journal's high-water mark (slot {rec['hwm']}), no signal journaled without it"
        lines.append(line)
    return lines


def farm_lines(journal, day):
    """How many of the day's live F7 triggers had a farm label from incomplete farmer history (before
    LiveFeed's complete_from): those labels are not comparable with the backtest's. And how many recorded trades got
    their farmer flag corrected after a late backfill (journal state farm_flag_fixes: a probe followed mid-life, its
    earlier trades arriving after its first recorded ones)."""
    trig = {}
    for r in journal.rows("signals", "day = ? AND family = 'F7' AND farm_complete IS NOT NULL", (day,)):
        trig.setdefault((r["key"], r["t"]), r["farm_complete"])
    fixes, fixes_open = (json.loads(journal.state("farm_flag_fixes") or "{}").get(day) or [0, 0])
    if not trig and not fixes:
        return []
    lines = ["", "## Farm labels", ""]
    if trig:
        n0 = sum(1 for v in trig.values() if not v)
        lines.append(f"- {n0} of {len(trig)} triggers before the farmer history was complete (journal state farmer_seed)")
    if fixes:
        lines.append(f"- {fixes} recorded trades' farmer flags corrected after a probe's late backfill (the farm labels after "
                     f"it are the backtest's; legs already handed out keep their flag), {fixes_open} of them in pools with an "
                     "open position (whose flow window keeps the old flags)")
    return lines


DETECTION_STREAMS = ("creation_detection_off", "probes_off", "pools_seen_without_creation", "late_probe")


def detection_lines(journal, day):
    """What the recording could not see that day (creation-filters spec v0.2 M1, M4, S3; LiveFeed's usage rows): the
    minutes without creation detection (new reversed pools not followed: no creation is no evidence of none) and
    without probes, the pools only the account streams named (not learned), and the probes whose backfill reached past
    Triton's replay window (their farmer history starts at the probe)."""
    rows = [r for r in journal.rows("usage", "day = ?", (day,)) if r["stream"] in DETECTION_STREAMS]
    if not rows:
        return []
    minutes = defaultdict(set)
    count = defaultdict(int)
    for r in rows:
        minutes[r["stream"]].add(r["minute"])
        count[r["stream"]] += r["messages"] or 0
    lines = ["", "## Detection", ""]
    if minutes["creation_detection_off"]:
        lines.append(f"- {len(minutes['creation_detection_off'])} minutes without creation detection (the filter had no new reversed "
                     "pools: not configured or dropped by a downscale): no new pool from them is no evidence of none")
    if minutes["probes_off"]:
        lines.append(f"- {len(minutes['probes_off'])} minutes without probes (dropped by a downscale): new pools known, their trades not followed")
    if count["pools_seen_without_creation"]:
        lines.append(f"- {count['pools_seen_without_creation']} pools named only by the pool account streams, not learned (no creation seen)")
    if count["late_probe"]:
        lines.append(f"- {count['late_probe']} probes whose backfill reached past Triton's replay window: their farmer history starts "
                     "at the probe")
    return lines


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
    mean, lo, hi, verdict_ = _verdict(by_day, n_filled, filled_by_day)
    feed = feed_of(journal.state(FEED_KEY),
                   journal.db.execute("SELECT 1 FROM signals WHERE received_unix_ns IS NOT NULL LIMIT 1").fetchone())
    judged = assessment(traded, mean, lo, hi, verdict_, clock_state(clock, day, verdict_), cost_scenario(journal),
                        primary_group(journal.state(GROUPS_KEY)), feed,
                        settle_rule=settle_rule_of(journal.state(SETTLE_RULE_KEY)))
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
              *([] if feed == "live" else ["- **a replay journal (a test), not live paper trading: a count, no decision**"]),
              *clock_lines(clock, day, n_filled, verdict_),
              f"- trades {n_filled} (failed entries {len(traded) - n_filled}, their cost included), days {len(by_day)}, "
              f"net {'' if mean is None else f'{mean / 1e9:+.4f}'} SOL/trade, "
              f"95% CI {ci}: **{verdict_}**",
              *(assessment_lines(judged) if verdict_ in ("GO", "ADJUST", "STOP") else
                [f"- the verdict comes with {MIN_TRADES} filled trades and {MIN_DAYS_CI} trading days"]),
              "", f"## Capital simulation ({CAPITAL_SOL:g} SOL, at most {MAX_POSITIONS} positions; {since})", "",
              f"- final {final / 1e9:.3f} SOL, max drawdown {mdd * 100:.1f}%, trades {taken}, skipped {skipped}",
              "", "## Heartbeat", "",
              f"- minutes with a heartbeat: {sum(1 for b in beats if not b['note'])}; feed gaps: {len(gaps)}"
              + (f" (largest {max(b['gap_slots'] for b in gaps)} slots)" if gaps else "")]
    lines += latency_lines(journal, day)
    lines += farm_lines(journal, day)
    lines += detection_lines(journal, day)
    lines += seed_lines(journal)
    lines += restart_lines(journal, day)
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
