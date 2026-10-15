"""Research D (exploratory, dev2 is used data): time from a trigger to the LP drain of its pool, and an inactivity
exit before it.

Per trigger (pool, t) of the parts (default F7 pool_reversed, the K3 segment): the first withdraw after t that
removes >= DRAIN_FRAC of the pool's WSOL is the drain (event); a trigger without one is censored at the end of the
observation (the store end, or t + --observe-slots). Kaplan-Meier (lifelines) from t to the drain, in minutes of
the epoch's slot time, per group: all, farm (organic / flagged), day block, regime. The K3 window: the share of
triggers drained within K3_HOLD_SLOTS slots (T600) of t.

Inactivity exit "no trade for X minutes" (X in GAP_MINUTES): the exit fires at the end of the first gap of >= X
between consecutive trades after t (the last trade before the gap, plus X). Reported against the drain: fires
before the drain (with the minutes saved and the pool's WSOL at the exit), after it, or never.

The LP-drain exit itself ("leave at the first withdraw") was rejected on the day smoke of 2026-10-16: in 92 % the
first withdraw is the drain. Descriptive: no test, no registration; a confirming test needs clean or forward data.

  LAB_STORE=<dev2> python -m wallets.survival --parts <f7rev parts> --out <dir> [--observe-slots N] [--from-slot A --to-slot B]
"""

import argparse
import glob
import json
import math
import os
import sys
import time

import numpy as np

from .common import OUT_ROOT, REF_MS, SOL, Clock, connect, git_commit, guard, regime_label
from .markout import ms_case

DRAIN_FRAC = 0.9
K3_HOLD_SLOTS = 600          # T600: the K3 exit rule holds at most 600 slots *at the reference slot time*: the engine converts
                             # clock.slots(ms(600), t) = 600 x 267.3 ms into the epoch's slots (~500 at 320 ms, ~735 at 218 ms)
K3_SQL = f"greatest(1, round({K3_HOLD_SLOTS} * {REF_MS} / ms_per_slot))"   # the hold in slots of the trigger's epoch
GAP_MINUTES = (1, 2, 5, 10)
GRID_MIN = (1, 2, 3, 5, 10, 30, 60, 360)
SPLIT_DAY, LAST_DAY = "2026-09-20", "2026-09-30"
FAMILY, SEGMENT = "F7", "pool_reversed"


def progress(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", file=sys.stderr, flush=True)


def _count(con, table):
    return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def load_triggers(con, parts_dir, family, segment, lo, hi):
    files = sorted(glob.glob(os.path.join(parts_dir, "*.parquet")))
    if not files:
        raise SystemExit(f"no parquet parts in {parts_dir}")
    lit = "[" + ",".join(f"'{f}'" for f in files) + "]"
    where = [f"family = '{family}'", f"segment = '{segment}'", "venue = 'pool'", "pool IS NOT NULL", "t IS NOT NULL"]
    if lo is not None:
        where.append(f"t >= {lo}")
    if hi is not None:
        where.append(f"t < {hi}")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE trig AS
      SELECT pool, t::BIGINT AS t, any_value(mint) AS mint, any_value(day) AS day, any_value(farm) AS farm
      FROM read_parquet({lit}, union_by_name = true) WHERE {' AND '.join(where)} GROUP BY pool, t""")
    n = _count(con, "trig")
    progress(f"triggers: {n} from {len(files)} parts")
    return n


def build(con, clock, end_slot, observe_slots):
    """Per trigger: the drain, the first withdraw, the inactivity exits, the censoring; all in slots and minutes."""
    lo, hi = con.execute("SELECT min(t), max(t) FROM trig").fetchone()
    hi_obs = min(end_slot, hi + observe_slots) if observe_slots else end_slot
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE legs AS
      SELECT p.pool, p.slot, p.tx_index, p.kind, p.b, p.base, p.sol_amount,
             CASE WHEN p.kind = 'sell' THEN p.b + p.base WHEN p.kind IN ('buy', 'withdraw') THEN p.b - p.base
                  WHEN p.kind = 'deposit' THEN p.b + p.base ELSE p.b END AS b_post,
             CASE WHEN p.kind = 'withdraw' THEN p.base::DOUBLE / nullif(p.b, 0) END AS frac
      FROM pool p SEMI JOIN (SELECT DISTINCT pool FROM trig) k USING (pool)
      WHERE p.slot >= {lo} AND p.slot < {hi_obs} AND p.kind IN ('buy', 'sell', 'withdraw', 'deposit')""")
    progress(f"legs: {_count(con, 'legs')} in [{lo}, {hi_obs})")
    # gaps between consecutive trades per pool (the last trade's gap runs to the end of the observation)
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE gaps AS
      SELECT pool, slot, b_post, COALESCE(lead(slot) OVER (PARTITION BY pool ORDER BY slot, tx_index), {hi_obs}) - slot AS gap
      FROM legs WHERE kind IN ('buy', 'sell')""")
    con.execute("CREATE OR REPLACE TEMP TABLE wd AS SELECT pool, slot, tx_index, frac, b FROM legs WHERE kind = 'withdraw'")
    ms = ms_case(clock, "tr.t")
    gap_cols = ", ".join(
        f"(SELECT min(g.slot + round({m} * 60000 / ({ms}))::BIGINT) FROM gaps g WHERE g.pool = tr.pool AND g.slot >= tr.t AND g.gap >= round({m} * 60000 / ({ms}))::BIGINT) AS exit_{m}m, "
        f"(SELECT arg_min(g.b_post, g.slot) FROM gaps g WHERE g.pool = tr.pool AND g.slot >= tr.t AND g.gap >= round({m} * 60000 / ({ms}))::BIGINT) AS b_exit_{m}m"
        for m in GAP_MINUTES)
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE surv AS
      SELECT tr.pool, tr.t, tr.mint, tr.day, tr.farm, ({ms})::DOUBLE AS ms_per_slot,
             (SELECT min(w.slot) FROM wd w WHERE w.pool = tr.pool AND w.slot > tr.t AND w.frac >= {DRAIN_FRAC}) AS drain_slot,
             (SELECT min(w.slot) FROM wd w WHERE w.pool = tr.pool AND w.slot > tr.t) AS first_withdraw_slot,
             (SELECT arg_min(w.frac, (w.slot, w.tx_index)) FROM wd w WHERE w.pool = tr.pool AND w.slot > tr.t) AS first_withdraw_frac,
             (SELECT max(g.slot) FROM gaps g WHERE g.pool = tr.pool AND g.slot > tr.t) AS last_trade_slot,
             {gap_cols},
             least({hi_obs}, tr.t + {observe_slots if observe_slots else end_slot}) AS censor_slot
      FROM trig tr""")
    # everything observed is bounded at the censor slot (an event at or after it is unobserved: NULL), and the WSOL
    # at an inactivity exit is the reserve after the last leg of any kind at or before the exit slot (a withdraw or
    # deposit inside the quiet gap counts; the gap-opening trade's reserve did not see it)
    bounded = ", ".join(f"CASE WHEN {c} < censor_slot THEN {c} END AS {c}" for c in ["drain_slot", "first_withdraw_slot", "last_trade_slot"] + [f"exit_{m}m" for m in GAP_MINUTES])
    b_exit = ", ".join(f"(SELECT arg_max(l.b_post, (l.slot, l.tx_index)) FROM legs l WHERE l.pool = s.pool AND l.slot <= s.exit_{m}m) AS b_exit_{m}m" for m in GAP_MINUTES)
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE surv AS
      SELECT * REPLACE ({bounded}, CASE WHEN first_withdraw_slot < censor_slot THEN first_withdraw_frac END AS first_withdraw_frac)
      FROM surv""")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE surv AS
      SELECT * REPLACE ({b_exit}), drain_slot IS NOT NULL AS event,
             (COALESCE(drain_slot, censor_slot) - t) AS time_slots,
             (COALESCE(drain_slot, censor_slot) - t) * ms_per_slot / 60000.0 AS time_min,
             drain_slot IS NOT NULL AND drain_slot - t <= {K3_SQL} AS drain_in_k3_hold,
             CASE WHEN day < '{SPLIT_DAY}' THEN 'days 1-9' WHEN day <= '{LAST_DAY}' THEN 'days 10-18' ELSE 'later' END AS block
      FROM surv s""")
    n, ev = con.execute("SELECT count(*), sum(event::INT) FROM surv").fetchone()
    progress(f"survival rows: {n}, drains observed {ev}")
    return n, ev


def km(times, events, grid=GRID_MIN):
    """Kaplan-Meier: survival with 95 % CI at the grid (minutes), median; None without data."""
    from lifelines import KaplanMeierFitter
    t, e = np.asarray(times, dtype=float), np.asarray(events, dtype=bool)
    if len(t) == 0:
        return None
    kmf = KaplanMeierFitter().fit(t, e)
    sf = kmf.survival_function_at_times(list(grid)).values
    ci = kmf.confidence_interval_survival_function_
    lo = np.interp(grid, ci.index.values, ci.iloc[:, 0].values)
    hi = np.interp(grid, ci.index.values, ci.iloc[:, 1].values)
    med = kmf.median_survival_time_
    return {"n": int(len(t)), "events": int(e.sum()), "grid": {g: (float(s), float(a), float(b)) for g, s, a, b in zip(grid, sf, lo, hi)},
            "median_min": None if not np.isfinite(med) else float(med)}


def wilson(k, n, z=1.96):
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (p, c - h, c + h)


def pct(x, nd=1):
    return "–" if x is None else f"{100 * x:.{nd}f} %"


def groups(con):
    g = [("all", "TRUE")]
    g += [(f"farm {f}", f"farm = '{f}'") for (f,) in con.execute("SELECT DISTINCT farm FROM surv WHERE farm IS NOT NULL ORDER BY 1").fetchall()]
    g += [(b, f"block = '{b}'") for (b,) in con.execute("SELECT DISTINCT block FROM surv ORDER BY 1").fetchall()]
    # One row per slot-time regime (ms per slot rounded to 10 ms, the engine's names), not per epoch: the first NAS
    # run grouped on the raw per-epoch ms_per_slot and labelled 14 epoch rows "regime 270ms" / "regime 320ms".
    regs = con.execute("SELECT DISTINCT round(ms_per_slot / 10) * 10 FROM surv ORDER BY 1").fetchall()
    if len(regs) > 1:
        g += [(f"regime {regime_label(m)}", f"round(ms_per_slot / 10) * 10 = {m!r}") for (m,) in regs]
    return g


def render(con, info):
    L = [f"## D (exploratory): time from trigger to the LP drain — {FAMILY} {SEGMENT}, dev2, slots [{info['lo']}, {info['hi']})\n",
         f"Triggers {info['triggers']:,} (distinct pool, t); drains observed {info['events']:,}; a drain = the first withdraw after t removing ≥ {DRAIN_FRAC:.0%} "
         f"of the pool's WSOL; censoring at the store end{' or t + ' + format(info['observe_slots'], ',') + ' slots' if info['observe_slots'] else ''}. "
         "Kaplan–Meier (lifelines), minutes of the epoch's slot time, 95 % CI (Greenwood). Descriptive on used data: no test, no registration.\n",
         "### Survival S(t) = share not yet drained t minutes after the trigger\n",
         "| group | n | drains | " + " | ".join(f"{g} min" for g in GRID_MIN) + " | median (min) | drained within the K3 hold (T600 = 600 slots at 267.3 ms, in the epoch's slots) |",
         "|---|---|---|" + "---|" * len(GRID_MIN) + "---|---|"]
    for name, where in groups(con):
        rows = con.execute(f"SELECT time_min, event, drain_in_k3_hold FROM surv WHERE {where}").fetchall()
        r = km([x[0] for x in rows], [x[1] for x in rows])
        if r is None:
            continue
        k3 = wilson(sum(1 for x in rows if x[2]), len(rows))
        cells = " | ".join(f"{pct(v[0])} [{pct(v[1], 0)}–{pct(v[2], 0)}]" for v in r["grid"].values())
        med = "–" if r["median_min"] is None else f"{r['median_min']:.1f}"
        L.append(f"| {name} | {r['n']:,} | {r['events']:,} | {cells} | {med} | {pct(k3[0])} [{pct(k3[1])}–{pct(k3[2])}] |")
    fw = con.execute(f"""SELECT count(first_withdraw_slot), count(*) FILTER (first_withdraw_frac >= {DRAIN_FRAC}),
                         count(*) FILTER (event AND drain_slot > last_trade_slot), count(*) FILTER (event) FROM surv""").fetchone()
    L += ["\n### The first withdraw\n",
          f"Triggers with a withdraw in the observation {fw[0]:,}; the first withdraw is already the drain (≥ {DRAIN_FRAC:.0%}) in {fw[1]:,} ({pct(fw[1] / max(fw[0], 1))}). "
          f"Among the observed drains, {fw[2]:,} of {fw[3]:,} ({pct(fw[2] / max(fw[3], 1))}) come after the pool's last trade (trading stopped first).\n",
          "### Inactivity exit: no trade for X minutes\n",
          "Fires at the end of the first gap of ≥ X minutes between trades after t. Against the drain: before it (minutes saved = drain − exit; WSOL in the pool at the exit), after it, or never within the observation.\n",
          "| X | fires | before the drain | median minutes saved | median WSOL at exit (SOL) | exits with ≥ 1 SOL in the pool | after the drain | fires, no drain observed | never, drained | never, not drained |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for m in GAP_MINUTES:
        r = con.execute(f"""
          SELECT count(exit_{m}m), count(*) FILTER (exit_{m}m IS NOT NULL AND event AND exit_{m}m < drain_slot),
                 median((drain_slot - exit_{m}m) * ms_per_slot / 60000.0) FILTER (exit_{m}m IS NOT NULL AND event AND exit_{m}m < drain_slot),
                 median(b_exit_{m}m / {SOL}) FILTER (exit_{m}m IS NOT NULL AND event AND exit_{m}m < drain_slot),
                 count(*) FILTER (exit_{m}m IS NOT NULL AND event AND exit_{m}m < drain_slot AND b_exit_{m}m >= {SOL}),
                 count(*) FILTER (exit_{m}m IS NOT NULL AND event AND exit_{m}m >= drain_slot), count(*) FILTER (exit_{m}m IS NOT NULL AND NOT event),
                 count(*) FILTER (exit_{m}m IS NULL AND event), count(*) FILTER (exit_{m}m IS NULL AND NOT event), count(*)
          FROM surv""").fetchone()
        n = r[9]
        L.append(f"| {m} min | {r[0]:,} ({pct(r[0] / n)}) | {r[1]:,} ({pct(r[1] / n)}) | {'–' if r[2] is None else f'{r[2]:.1f}'} | "
                 f"{'–' if r[3] is None else f'{r[3]:.2f}'} | {r[4]:,} | {r[5]:,} | {r[6]:,} | {r[7]:,} | {r[8]:,} |")
    L += ["\nReading guide: an exit that fires after the drain sells into an empty pool; 'never, drained' is the share the inactivity rule cannot protect. "
          "The K3 column of the survival table is the share of triggers whose pool is drained within the engine's maximum hold: T600 is a duration "
          f"(600 slots at the reference {REF_MS} ms), converted to the epoch's slots as the engine does (clock.slots(ms(600), t)).\n",
          f"Run: commit {info['commit']}, {info['seconds']} s."]
    return "\n".join(L)


def run(parts_dir, out_dir, family=FAMILY, segment=SEGMENT, lo=None, hi=None, observe_slots=None, store=None):
    t0 = time.time()
    mem = os.environ.get("LAB_WALLETS_MEMORY", "1GB")
    con = connect(store, memory=mem) if store else connect(memory=mem)
    clock = Clock.from_store(con)
    end_slot = int(json.loads(con.execute("SELECT json FROM meta").fetchone()[0])["slot_end_exclusive"])
    guard(hi if hi is not None else end_slot)
    n = load_triggers(con, parts_dir, family, segment, lo, hi)
    if n == 0:
        raise SystemExit("no triggers")
    n, ev = build(con, clock, end_slot, observe_slots)
    os.makedirs(out_dir, exist_ok=True)
    con.execute(f"COPY (SELECT * FROM surv ORDER BY pool, t) TO '{os.path.join(out_dir, 'survival.parquet')}' (FORMAT PARQUET)")
    info = {"parts": parts_dir, "family": family, "segment": segment, "lo": con.execute("SELECT min(t) FROM surv").fetchone()[0],
            "hi": con.execute("SELECT max(t) FROM surv").fetchone()[0] + 1, "triggers": n, "events": ev, "observe_slots": observe_slots,
            "drain_frac": DRAIN_FRAC, "k3_hold_slots": K3_HOLD_SLOTS, "gap_minutes": list(GAP_MINUTES), "commit": git_commit(),
            "seconds": round(time.time() - t0, 1)}
    with open(os.path.join(out_dir, "survival.md"), "w") as f:
        f.write(render(con, info) + "\n")
    with open(os.path.join(out_dir, "run-survival.json"), "w") as f:
        json.dump(info, f, indent=1, default=str)
    progress(f"written {out_dir} ({info['seconds']} s)")
    return info


def rerender(out_dir):
    """Re-render survival.md from an existing survival.parquet and run-survival.json (seconds, no store): the
    report of a NAS run can be fixed on the VPS without rerunning the build."""
    import duckdb
    con = duckdb.connect()
    # drain_in_k3_hold is recomputed (the hold is a duration per epoch; the first NAS run used a flat 600 slots)
    con.execute(f"CREATE TEMP TABLE surv AS SELECT * REPLACE (event AND drain_slot - t <= {K3_SQL} AS drain_in_k3_hold) FROM '{os.path.join(out_dir, 'survival.parquet')}'")
    with open(os.path.join(out_dir, "run-survival.json")) as f:
        info = json.load(f)
    with open(os.path.join(out_dir, "survival.md"), "w") as f:
        f.write(render(con, info) + "\n")
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parts", help="positions of the run (required unless --rerender)")
    ap.add_argument("--rerender", action="store_true", help="only re-render survival.md from the parquet and json in --out")
    ap.add_argument("--out", default=os.path.join(OUT_ROOT, "survival-v1"))
    ap.add_argument("--family", default=FAMILY)
    ap.add_argument("--segment", default=SEGMENT)
    ap.add_argument("--from-slot", type=int)
    ap.add_argument("--to-slot", type=int)
    ap.add_argument("--observe-slots", type=int, help="censor at t + N slots instead of the store end (a smoke)")
    a = ap.parse_args()
    if a.rerender:
        info = rerender(a.out)
    elif a.parts:
        info = run(a.parts, a.out, a.family, a.segment, a.from_slot, a.to_slot, a.observe_slots)
    else:
        ap.error("--parts is required unless --rerender")
    print(json.dumps(info, default=str), file=sys.stderr)


if __name__ == "__main__":
    main()
