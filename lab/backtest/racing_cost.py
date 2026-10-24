"""Racing cost on dev (research/racing-cost-plan-2026-10-23.md, sha256 342876de; E65; exploratory and in-sample, no
new rule): for the leads H5 and C3/hold300, the net SOL per day at C = 10 / 25 / 100 SOL as a function of our delay d
(one d for the entry and the exit, as in E63), with a landing probability and a fee to land at d 0; per lead the
break-even delay, the value of being as fast as the fastest bot and an infra what-if. Research and simulation only:
nothing is sent, nothing is signed.

  python -m backtest.racing_cost table  --parts RUN_DIR [RUN_DIR ...] --store dev2.duckdb --out DIR
  python -m backtest.racing_cost report --parts RUN_DIR [RUN_DIR ...] --race DIR --c3-keys PARQUET --store dev2.duckdb
                                        --route-ii PARQUET --e63-json JSON --out DIR [--draws 200] [--workers 4]
                                        (E63's run last in --parts: G0 compares every run's grid with it)
  python -m backtest.racing_cost w8b    --e1 PARQUET --out DIR

- Rows (plan sections 1 and 2): the runs' F7 pool_reversed positions of flow300_tp100_H6 and hold300 at 0.5 / 1.25 /
  5 SOL, overlay none, not skipped. A d 0 row must have q 1.0 (the fill on the trigger slot's end state: first in
  t + 1) and a d >= 1 row q 0.5; any other row of these variants refuses the run (G1: d 0 at q < 1 fills inside the
  trigger slot, possibly before the trigger, a lookahead). For a (variant, size, d) the first RUN_DIR with rows
  supplies all of them, so E63's run (capital-ceiling-d15ac45) supplies d 2 and 3. Dev only, as capital_ceiling.
- table (plan 3 and 4): per (pool, t) of the runs' rows, the pool's transactions in slot t + 1 with a token-buy leg
  (replay._entry_index's rule: a round trip counts once, a sell not), n_t1, and the first one's priority_fee +
  jito_tip, once per transaction (race.parquet); every such transaction (race_txs.parquet).
- report: E63's limited() and capital_days unchanged (size 5 % of C, at most 5 open, daily loss 10 % of C, a 24 h
  cooldown after a total loss, one open position per pool, cash; an unsold position freed at its rule's deadline).
  At d 0: p_land(n) = 1 / 0.5 / 0.25 / 0.1 for n = 0 / 1 / 2 / >= 3 t + 1 token buys; a miss is no position with the
  failed transaction paid (primary) or the same trigger's d 1 row (sensitivity); F extra lamports per d 0 attempt on
  top of the scenario. Without limits the exact expectation; with limits DRAWS paired draws (u from sha256 of
  pool|t|draw: the same u in every cell), the day series averaged over the draws, then day-bootstrapped. Gates G1-G5
  (plan section 8); any RED: exit 1.
- w8b (plan 7): arithmetic on E51's probe rows, not comparable with H5 and C3.

Writes racing.json and racing.md (report), race.parquet, race_txs.parquet and race.json (table), w8b.json and w8b.md.
"""

import argparse
import hashlib
import json
import multiprocessing
import os
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor

import duckdb
import numpy as np

from .capital_ceiling import (COOLDOWN_MS, DAILY_LOSS, DEV, MAX_OPEN, SCENARIOS, Refused, boot_days, capital_days,
                              check_run, ci, exit_of, flag_share, fmt, limited, unlimited)
from .costs import Scenario, settle_sql
from .store import load_defaults

PLAN = "research/racing-cost-plan-2026-10-23.md"
PLAN_SHA = "342876de94d2cf54af7d851961ecd92abfb673b9dc1ba2e607a345a1eff50796"
# (name, variant, farm, C3 arm): the names of E63's CANDIDATES
LEADS = (("H5", "flow300_tp100_H6", "organic", False), ("C3/hold300", "hold300", None, True))
VARIANTS = ("flow300_tp100_H6", "hold300")
SIZES = (0.5, 1.25, 5.0)
SHARE = 0.05  # size per trade as a share of C (E63's primary mapping): C = 10 / 25 / 100 SOL
P_LAND = (1.0, 0.5, 0.25, 0.1)  # for 0 / 1 / 2 / >= 3 token buys in t + 1 (coordinator)
DRAWS = 200
F_FIXED = (0.001, 0.005, 0.01)  # SOL per d 0 attempt, besides 0 and the lead's p50 / p90
MISSES = ("none", "d1")
EPOCH = 432_000
REF_D = (3, 2)  # live now (t + 3) and the 600 ms baseline on dev (2 slots in every dev epoch)
SOL_USD = 104.37  # CoinGecko, 2026-10-23
MONTH_DAYS = 365 / 12
# Infra what-if (plan 6e; estimates with sources in the plan): (USD per month low, high, the d it maps to)
TIERS = {"as_now": (35, 50, 3), "fast": (50, 650, 1), "fastest": (2_400, 6_800, 0)}
FAST_TIP = 0.001  # SOL per transaction on the fast tier's send path (entry and exit)


def usd_month_to_sol_day(usd):
    return usd / SOL_USD / MONTH_DAYS


def p_land(n):
    return P_LAND[min(int(n), 3)]


def draws_u(pool, t, draws=DRAWS, _cache={}):
    """The landing draws of a trigger: the first 8 bytes of sha256(pool|t|j) / 2^64, the same in every cell."""
    key = (pool, t, draws)
    if key not in _cache:
        _cache[key] = _draws(pool, t, draws)
    return _cache[key]


def _draws(pool, t, draws):
    return np.array([int.from_bytes(hashlib.sha256(f"{pool}|{t}|{j}".encode()).digest()[:8], "big") / 2 ** 64
                     for j in range(draws)])


def _src(run):
    return f"read_parquet('{os.path.join(run, 'parts', '*.parquet')}', union_by_name = true)"


def _vlist():
    return ", ".join(f"'{v}'" for v in VARIANTS)


def _slist():
    return ", ".join(repr(s) for s in SIZES)


def _guard(con, run, src, cols):
    """check_run's dev window, one tau, no route-(ii) entry rows, and G1: d 0 at q 1.0 only, d >= 1 at q 0.5 only."""
    cfg = check_run(run)
    if "entry" in cols and con.execute(f"SELECT count(*) FROM {src} WHERE entry IS NOT NULL").fetchone()[0]:
        raise Refused(f"{run}: route-(ii) entry rows (--entries); a racing run has none")
    taus = [r[0] for r in con.execute(f"SELECT DISTINCT tau FROM {src} WHERE family = 'F7'").fetchall()] if "tau" in cols else ["empirical"]
    if len(taus) > 1:
        raise Refused(f"{run}: more than one tau mode {taus}")
    bad = con.execute(f"""SELECT count(*) FILTER (WHERE d = 0 AND q IS DISTINCT FROM 1.0),
                                 count(*) FILTER (WHERE d >= 1 AND q IS DISTINCT FROM 0.5), count(*) FILTER (WHERE d < 0 OR d IS NULL)
                          FROM {src} WHERE family = 'F7' AND segment = 'pool_reversed' AND variant IN ({_vlist()})""").fetchone()
    if any(bad):
        raise Refused(f"{run}: G1 RED: {bad[0]} d 0 rows not at q 1.0 (a fill inside the trigger slot: a lookahead), "
                      f"{bad[1]} d >= 1 rows not at q 0.5, {bad[2]} rows with d < 0 or NULL")
    return cfg


# The settings every run must share with E63's (capital-ceiling-d15ac45), so that its d 2 and 3 cells are like for like with
# the new d 0 / 1 / 5 / 8 / 13 cells by construction (LAB review of #536): equal fields, and the leads' variants and sizes
# inside each run's sets (busy is kept per (family, variant), so other variants in a run do not change these rows).
# slippage_tol is the tournament's name for --tol (config.json); the earlier "tol" named no field (found 24 Oct).
SAME = ("families", "segments", "f7_grid", "m1_grid", "tau", "slippage_tol", "n2_k", "only", "decide_range")


def grid_check(runs):
    cfgs = []
    for run in runs:
        with open(os.path.join(run, "config.json")) as f:
            cfgs.append(json.load(f))
    ref = cfgs[-1]  # the last run: E63's
    bad = []
    for run, c in zip(runs, cfgs):
        diff = [k for k in SAME if c.get(k) != ref.get(k)]
        if (c.get("store") or {}).get("git_commit") != (ref.get("store") or {}).get("git_commit"):
            diff.append("store.git_commit")
        if not set(VARIANTS) <= set(c.get("variants") or []):
            diff.append(f"variants {c.get('variants')}")
        if not set(SIZES) <= set(c.get("sizes_sol") or []):
            diff.append(f"sizes {c.get('sizes_sol')}")
        if diff:
            bad.append(f"{run}: {diff}")
    if bad:
        raise Refused(f"the runs' grids differ from E63's (the last run): {bad}")
    return {"runs": len(runs), "fields": list(SAME) + ["store.git_commit", "variants and sizes inside"], "status": "GREEN"}


def load(con, runs, c3_keys, store):
    """The rows table r (both scenarios settled, ms per slot per row), the C3 keys c3, the store's per-epoch ms per
    slot and E63's fee table (the sandwich flag)."""
    cfg = load_defaults()
    pnl = {s: settle_sql(Scenario.from_cfg(s, cfg))[0] for s in SCENARIOS}
    con.execute("""CREATE TABLE r (src INTEGER, variant VARCHAR, size_sol DOUBLE, d INTEGER, q DOUBLE, t UBIGINT, day VARCHAR,
                   pool VARCHAR, farm VARCHAR, entry_failed BOOLEAN, cost BIGINT, proceeds BIGINT, exit_slot UBIGINT,
                   exit_decision_slot UBIGINT, d_ms DOUBLE, depth DOUBLE, pnl_base BIGINT, pnl_pessimistic BIGINT)""")
    provenance = []
    for i, run in enumerate(runs):
        src = _src(run)
        cols = {c[0] for c in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()}
        cfg_run = _guard(con, run, src, cols)
        overlay = "COALESCE(overlay, 'none') = 'none'" if "overlay" in cols else "true"
        con.execute(f"""INSERT INTO r SELECT {i}, variant, size_sol, d, q, t, day, pool, farm, entry_failed, cost, proceeds, exit_slot,
                          exit_decision_slot, d_ms, depth, {pnl['base']}, {pnl['pessimistic']}
                        FROM {src} x WHERE family = 'F7' AND segment = 'pool_reversed' AND {overlay}
                          AND variant IN ({_vlist()}) AND size_sol IN ({_slist()}) AND NOT skipped
                          AND NOT EXISTS (SELECT 1 FROM r WHERE r.variant = x.variant AND r.size_sol = x.size_sol AND r.d = x.d)""")
        n = con.execute(f"SELECT count(*) FROM r WHERE src = {i}").fetchone()[0]
        provenance.append({"run": run, "engine_commit": cfg_run.get("engine_commit"), "rows": n, "dry_run_range": cfg_run.get("slot_range"),
                           "cells": [list(c) for c in con.execute(f"SELECT DISTINCT variant, size_sol, d FROM r WHERE src = {i} ORDER BY ALL").fetchall()]})
    dup = con.execute("SELECT variant, size_sol, d, pool, t, count(*) FROM r GROUP BY ALL HAVING count(*) > 1 LIMIT 3").fetchall()
    if dup:
        raise Refused(f"duplicate positions (variant, size, d, pool, t), e.g. {dup}: a run that mixes two runs' parts")
    lo, hi = con.execute("SELECT min(t), max(greatest(t, COALESCE(exit_slot, 0), COALESCE(exit_decision_slot, 0))) FROM r").fetchone()
    if lo is None:
        raise Refused("no rows of the leads' variants")
    if lo < DEV[0] or hi >= DEV[1]:
        raise Refused(f"rows reach outside dev: [{lo}, {hi}]")
    con.execute(f"""CREATE TABLE c3 AS SELECT DISTINCT pool, t FROM read_parquet('{c3_keys}')
                    WHERE d = 2 AND h = '5m' AND paid_buyers_60s >= 2""")
    klo, khi, nk = con.execute("SELECT min(t), max(t), count(*) FROM c3").fetchone()
    if not nk or klo < DEV[0] or khi >= DEV[1]:
        raise Refused(f"C3 keys: {nk} keys in [{klo}, {khi}], not dev")
    con.execute(f"ATTACH '{store}' AS st (READ_ONLY)")
    m = json.loads(con.execute("SELECT json FROM st.meta").fetchone()[0])
    if (m.get("slot_start"), m.get("slot_end_exclusive")) != DEV:
        raise Refused(f"{store}: window [{m.get('slot_start')}, {m.get('slot_end_exclusive')}) is not dev2's")
    ms = m["ms_per_slot"] if isinstance(m["ms_per_slot"], dict) else json.loads(m["ms_per_slot"])
    con.execute("CREATE TABLE ep_ms (ep BIGINT, ms DOUBLE)")
    con.executemany("INSERT INTO ep_ms VALUES (?, ?)", [(int(k), float(v)) for k, v in ms.items()])
    miss = con.execute(f"SELECT count(*) FROM r ANTI JOIN ep_ms e ON e.ep = r.t // {EPOCH}").fetchone()[0]
    if miss:
        raise Refused(f"{miss} rows in an epoch without the store's ms per slot")
    # LAB review of the plan (should-fix 2): d 0 takes the store's ms per slot where d >= 1 takes d_ms / d; the engine's
    # d_ms is round(d x the same epoch value, 1) (tournament.py), so every d >= 1 row must agree within the rounding.
    n_ms, off = con.execute(f"""SELECT count(*), count(*) FILTER (WHERE abs(r.d_ms - r.d * e.ms) > 0.05 + 1e-6)
                                FROM r JOIN ep_ms e ON e.ep = r.t // {EPOCH} WHERE r.d >= 1""").fetchone()
    if off:
        raise Refused(f"ms per slot: {off} of {n_ms} d >= 1 rows have a d_ms that is not round(d x the store's epoch ms per slot, 1)")
    # E63's sandwich flag (analysis.f7_candidate section 4): the pool's fee at the trigger slot.
    con.execute("CREATE TEMP TABLE trig AS SELECT DISTINCT pool, t FROM r")
    con.execute("""CREATE TABLE fee AS SELECT e.pool, e.slot AS t, max(e.lp_bps + e.protocol_bps + e.creator_bps) / 1e4 AS f
                   FROM st.pool e SEMI JOIN trig g ON e.pool = g.pool AND e.slot = g.t
                   WHERE e.kind IN ('buy', 'sell') GROUP BY ALL""")
    return {"provenance": provenance, "c3_keys": nk, "store_commit": m.get("git_commit"), "ms_check_rows": n_ms}


def cell_rows(con, variant, farm, arm, size, d, scenario):
    """capital_ceiling.cell_rows' 11-tuple (t, exit_slot, exit_decision_slot, d, cost, pnl, entry_failed, day, pool,
    proceeds, ms per slot) in its order; the ms per slot is d_ms / d (E63) at d >= 1 and the store's epoch value at
    d 0, where d_ms / d is undefined."""
    where = f"r.variant = '{variant}' AND r.size_sol = {size} AND r.d = {d}"
    if farm:
        where += f" AND r.farm = '{farm}'"
    join = "SEMI JOIN c3 USING (pool, t)" if arm else ""
    return con.execute(f"""SELECT r.t, r.exit_slot, r.exit_decision_slot, r.d, r.cost, r.pnl_{scenario}, r.entry_failed, r.day,
                                  r.pool, r.proceeds, CASE WHEN r.d = 0 THEN e.ms ELSE r.d_ms / r.d END
                           FROM r {join} LEFT JOIN ep_ms e ON e.ep = r.t // {EPOCH}
                           WHERE {where} ORDER BY r.t, r.exit_slot NULLS LAST, r.pool""").fetchall()


def trades_of(rows):
    """limited()'s trades: the place freed at the exit or, unsold, at the rule's deadline (E63's primary)."""
    t = [(r[0], exit_of((r[1], r[2], r[3]), "deadline"), r[4], r[5], r[6], r[7], r[8], r[9], r[10]) for r in rows]
    t.sort(key=lambda x: (x[0], x[1] if x[1] is not None else -1))
    return t


def run_days(trades, days, capital, size):
    """capital_days on sorted trades at C (limited()'s call): the SOL per day series, the 12-tuple and the extras."""
    out, extra = capital_days(trades, capital * 1e9, MAX_OPEN, DAILY_LOSS * capital * 1e9, COOLDOWN_MS, size=int(round(size * 1e9)))
    return [extra["day_pnl"].get(x, 0.0) / 1e9 for x in days], out, extra


def day_sums(rows, days, pnl=lambda r: r[5] or 0):
    per = defaultdict(float)
    for r in rows:
        per[r[7]] += pnl(r) / 1e9
    return [per.get(x, 0.0) for x in days]


def miss_row(r, alt, F, failed_tx):
    """A missed d 0 attempt: the same trigger's d 1 row with F (sensitivity), or no position with the failed
    transaction and F paid (primary; capital_days' failed path, which needs the size in cash)."""
    if alt is not None:
        return alt[:5] + ((alt[5] or 0) - F,) + alt[6:]
    return (r[0], None, None, 0, 0, -(failed_tx + F), True, r[7], r[8], 0, r[10])


def expected(rows0, ns, F, alt_of, failed_tx, days):
    """Without limits, exactly: per row p (pnl - F) + (1 - p) (miss), per day; and the expected attempts, landings
    and misses."""
    per = defaultdict(float)
    landed = 0.0
    for r, n in zip(rows0, ns):
        p = p_land(n)
        alt = alt_of(r)
        miss = ((alt[5] or 0) - F) if alt is not None else -(failed_tx + F)
        per[r[7]] += (p * ((r[5] or 0) - F) + (1 - p) * miss) / 1e9
        landed += p
    vals = [per.get(x, 0.0) for x in days]
    mean, lo, hi = boot_days(vals)
    return vals, {"sol_day": mean, "lo": lo, "hi": hi, "attempts_per_day": round(len(rows0) / len(days), 2),
                  "landed_per_day": round(landed / len(days), 2), "missed_per_day": round((len(rows0) - landed) / len(days), 2)}


def draw_job(job):
    """With limits over the paired draws, for every (F, miss) of one lead, size and scenario: the day series averaged
    over the draws and per draw its SOL/day, ruin and entries taken."""
    rows0, ns, us, alts, fs, failed_tx, days, capital, size = (job[k] for k in
                                                             ("rows0", "ns", "us", "alts", "fs", "failed_tx", "days", "capital", "size"))
    ps = np.array([p_land(n) for n in ns])
    out = {}
    for fkey, F in fs:
        for miss in MISSES:
            acc = np.zeros(len(days))
            per_draw, ruined, taken = [], 0, []
            for j in range(us.shape[1]):
                hit = us[:, j] < ps
                rows = [(r[:5] + ((r[5] or 0) - F,) + r[6:]) if h else miss_row(r, alts[i] if miss == "d1" else None, F, failed_tx)
                        for i, (r, h) in enumerate(zip(rows0, hit))]
                vals, o, _ = run_days(trades_of(rows), days, capital, size)
                acc += vals
                per_draw.append(float(np.mean(vals)))
                ruined += o[6] < size
                taken.append(o[1] / len(days))
            out[(fkey, miss)] = {"series": list(acc / us.shape[1]), "per_draw": per_draw, "ruined": ruined, "taken_per_day": float(np.mean(taken))}
    return job["key"], out


def breakeven(points):
    """points [(d, sol_day, lo)] at d >= 1: the first d whose point is <= 0, and the largest d up to which every lower
    bound is > 0."""
    pts = sorted(p for p in points if p[0] >= 1 and p[1] is not None)
    first = next((d for d, v, _ in pts if v <= 0), None)
    safe = None
    for d, _, lo in pts:
        if lo is None or lo <= 0:
            break
        safe = d
    return {"grid": [d for d, _, _ in pts], "first_non_positive_d": first,
            "beyond_grid": first is None, "lower_bound_positive_up_to_d": safe}


def paired(a, b):
    m, lo, hi = boot_days([x - y for x, y in zip(a, b)])
    return {"sol_day": m, "lo": lo, "hi": hi, "sol_month": None if m is None else m * 30}


def tier_net(delta, tier, ref="as_now"):
    """A tier's value net of its fixed cost over the as-now tier (USD per month to SOL per day at SOL_USD): [low, high]."""
    lo_c = usd_month_to_sol_day(TIERS[tier][0]) - usd_month_to_sol_day(TIERS[ref][1])
    hi_c = usd_month_to_sol_day(TIERS[tier][1]) - usd_month_to_sol_day(TIERS[ref][0])
    return None if delta is None else [delta - hi_c, delta - lo_c]


# --- gates -------------------------------------------------------------------------------------------------------

GATE_COLS = ("cost", "proceeds", "exit_slot", "exit_reason", "entry_failed", "tokens", "entry_slot", "skipped")


def _cell_run(provenance, variant, size, d):
    return next((p["run"] for p in provenance if [variant, size, d] in p["cells"]), None)


def gate_g2(con, provenance, route_ii):
    """d 1 at 0.5 SOL, flow300_tp100_H6: route (ii)'s D1both rows, row for row (a dry run: INFO, its busy chains start
    at the range)."""
    run = _cell_run(provenance, "flow300_tp100_H6", 0.5, 1)
    if run is None:
        return {"status": "RED", "why": "no d 1 cell at 0.5 SOL"}
    dry = next(p["dry_run_range"] for p in provenance if p["run"] == run)
    win = f"AND t >= {dry[0]} AND t < {dry[1]}" if dry else ""
    sel = ", ".join(GATE_COLS)
    base = "family = 'F7' AND segment = 'pool_reversed' AND COALESCE(overlay, 'none') = 'none' AND variant = 'flow300_tp100_H6' AND size_sol = 0.5"
    a = f"(SELECT pool, t, {sel} FROM {_src(run)} WHERE {base} AND d = 1)"
    b = f"(SELECT pool, t, {sel} FROM read_parquet('{route_ii}') WHERE {base} AND entry = 'D1both' {win})"
    diff = " OR ".join(f"a.{c} IS DISTINCT FROM b.{c}" for c in GATE_COLS)
    na, nb, unmatched, differ = con.execute(f"""SELECT count(a.t), count(b.t), count(*) FILTER (WHERE a.t IS NULL OR b.t IS NULL),
                                                       count(*) FILTER (WHERE a.t IS NOT NULL AND b.t IS NOT NULL AND ({diff}))
                                                FROM {a} a FULL JOIN {b} b USING (pool, t)""").fetchone()
    ok = na == nb and na > 0 and not unmatched and not differ
    return {"rows": na, "route_ii_rows": nb, "unmatched": unmatched, "differ": differ, "dry_run": bool(dry),
            "status": "GREEN" if ok else ("INFO (dry run)" if dry else "RED")}


def gate_g3(con, provenance, route_ii):
    """d 0 at 0.5 SOL, flow300_tp100_H6: cost and tokens equal to route (ii)'s E0 on the shared (pool, t); no d 0 row
    of any lead failed."""
    run = _cell_run(provenance, "flow300_tp100_H6", 0.5, 0)
    if run is None:
        return {"status": "RED", "why": "no d 0 cell at 0.5 SOL"}
    base = ("family = 'F7' AND segment = 'pool_reversed' AND COALESCE(overlay, 'none') = 'none' AND variant = 'flow300_tp100_H6' "
            "AND size_sol = 0.5 AND NOT skipped AND NOT entry_failed")
    shared, differ = con.execute(f"""SELECT count(*), count(*) FILTER (WHERE a.cost IS DISTINCT FROM b.cost OR a.tokens IS DISTINCT FROM b.tokens)
                                     FROM (SELECT pool, t, cost, tokens FROM {_src(run)} WHERE {base} AND d = 0) a
                                     JOIN (SELECT pool, t, cost, tokens FROM read_parquet('{route_ii}') WHERE {base} AND entry = 'E0') b
                                     USING (pool, t)""").fetchone()
    n0, failed = con.execute("SELECT count(*), count(*) FILTER (WHERE entry_failed) FROM r WHERE d = 0").fetchone()
    ok = shared > 0 and not differ and not failed
    return {"shared": shared, "differ": differ, "d0_rows": n0, "d0_failed": failed, "status": "GREEN" if ok else "RED"}


def gate_g4(cells_e63, mine):
    """Our d 2 and 3 cells of H5 and C3/hold300 equal E63's ceiling.json (without limits; with limits primary, deadline)."""
    by = {(c["candidate"], c["size"], c["d"], c["scenario"]): c for c in cells_e63}
    checked, bad = 0, []
    for key, (u, x) in mine.items():
        c = by.get(key)
        if c is None:
            bad.append(f"{key}: not in E63")
            continue
        e = next(y for y in c["limited"] if y["mapping"] == "primary" and y["unsold"] == "deadline")
        du = [k for k in u if u[k] != c["unlimited"].get(k)]
        dx = [k for k in x if x[k] != e.get(k)]
        checked += 1
        if du or dx:
            bad.append(f"{key}: unlimited {du}, limited {dx}")
    return {"cells": checked, "differ": bad, "status": "GREEN" if checked and not bad else "RED"}


def gate_g5(con, route_ii):
    """The race table's n against route (ii)'s K1 / K2 / K3 capped flags (Kk capped <=> n < k) on every shared trigger,
    and a race row for every (pool, t) of the rows."""
    shared, mism = con.execute(f"""SELECT count(*), count(*) FILTER (WHERE k.capped IS DISTINCT FROM (x.n_t1 < CAST(substr(k.entry, 2) AS INTEGER)))
                                   FROM read_parquet('{route_ii}') k JOIN race x USING (pool, t)
                                   WHERE k.entry IN ('K1', 'K2', 'K3') AND k.capped IS NOT NULL""").fetchone()
    missing = con.execute("SELECT count(*) FROM (SELECT DISTINCT pool, t FROM r) ANTI JOIN race USING (pool, t)").fetchone()[0]
    ok = shared > 0 and not mism and not missing
    return {"shared_rows": shared, "mismatch": mism, "rows_without_race": missing, "status": "GREEN" if ok else "RED"}


# --- table -------------------------------------------------------------------------------------------------------

def table(con, runs, store, out):
    con.execute("CREATE TABLE k (pool VARCHAR, t UBIGINT)")
    for run in runs:
        check_run(run)
        con.execute(f"""INSERT INTO k SELECT DISTINCT pool, t FROM {_src(run)} WHERE family = 'F7' AND segment = 'pool_reversed'
                          AND variant IN ({_vlist()}) AND size_sol IN ({_slist()}) AND NOT skipped""")
    con.execute("CREATE TABLE kk AS SELECT DISTINCT pool, t, t + 1 AS s FROM k")
    lo, hi, nk = con.execute("SELECT min(t), max(t), count(*) FROM kk").fetchone()
    if not nk:
        raise Refused("no keys")
    if lo < DEV[0] or hi + 1 >= DEV[1]:
        raise Refused(f"keys reach outside dev: [{lo}, {hi}]")
    con.execute(f"ATTACH '{store}' AS st (READ_ONLY)")
    m = json.loads(con.execute("SELECT json FROM st.meta").fetchone()[0])
    if (m.get("slot_start"), m.get("slot_end_exclusive")) != DEV:
        raise Refused(f"{store}: window [{m.get('slot_start')}, {m.get('slot_end_exclusive')}) is not dev2's")
    # One row per transaction of the pool in t + 1 (its fee columns repeat on every leg: max, once).
    con.execute(f"""CREATE TABLE race_txs AS SELECT k.pool, k.t, p.tx_index, max(p.priority_fee) AS prio, max(p.jito_tip) AS tip,
                      max(p.tx_fee) AS tx_fee
                    FROM st.pool p JOIN kk k ON p.pool = k.pool AND p.slot = k.s
                    WHERE p.slot BETWEEN {lo + 1} AND {hi + 1} AND p.kind IN ('buy', 'sell')
                    GROUP BY k.pool, k.t, p.tx_index HAVING bool_or(p.token_buy)""")
    con.execute("""CREATE TABLE race AS SELECT k.pool, k.t, count(x.tx_index) AS n_t1, min(x.tx_index) AS first_tx,
                     arg_min(x.prio + x.tip, x.tx_index) AS first_fee, arg_min(x.prio, x.tx_index) AS first_prio,
                     arg_min(x.tip, x.tx_index) AS first_tip
                   FROM kk k LEFT JOIN race_txs x USING (pool, t) GROUP BY k.pool, k.t""")
    os.makedirs(out, exist_ok=True)
    for name in ("race", "race_txs"):
        con.execute(f"COPY (SELECT * FROM {name} ORDER BY ALL) TO '{os.path.join(out, name + '.parquet')}' (FORMAT PARQUET)")
    dist = dict(con.execute("SELECT least(n_t1, 3), count(*) FROM race GROUP BY 1 ORDER BY 1").fetchall())
    info = {"plan": PLAN, "plan_sha256": PLAN_SHA, "runs": runs, "store_commit": m.get("git_commit"), "keys": nk,
            "key_slots": [lo, hi], "n_t1_0_1_2_3plus": dist, "txs": con.execute("SELECT count(*) FROM race_txs").fetchone()[0]}
    with open(os.path.join(out, "race.json"), "w") as f:
        json.dump(info, f, indent=1)
    print(f"race table: {nk:,} keys, n_t1 {dist}, {info['txs']:,} token-buy transactions in t + 1 ({out})")
    return info


# --- report ------------------------------------------------------------------------------------------------------

def fee_stats(con, keys_sql):
    """The first t + 1 token buyer's priority_fee + jito_tip (n >= 1) and every t + 1 token-buy transaction's, in
    lamports, over the given (pool, t)."""
    first = [r[0] for r in con.execute(f"SELECT x.first_fee FROM race x SEMI JOIN ({keys_sql}) k USING (pool, t) WHERE x.n_t1 >= 1").fetchall()]
    alltx = con.execute(f"""SELECT prio + tip, tip > 0 FROM race_txs x SEMI JOIN ({keys_sql}) k USING (pool, t)""").fetchall()
    q = lambda v, p: None if not v else int(round(float(np.percentile(v, p))))  # noqa: E731
    fees = [a for a, _ in alltx]
    return {"first_n": len(first), "first_p50": q(first, 50), "first_p90": q(first, 90), "first_p99": q(first, 99),
            "all_n": len(fees), "all_p50": q(fees, 50), "all_p90": q(fees, 90), "all_p99": q(fees, 99),
            "all_tipped_pct": round(100 * sum(b for _, b in alltx) / len(alltx), 1) if alltx else None}


def compute(con, draws, workers):
    cfg = load_defaults()
    scn = {s: Scenario.from_cfg(s, cfg) for s in SCENARIOS}
    days = [r[0] for r in con.execute("SELECT DISTINCT day FROM r ORDER BY 1").fetchall()]
    grid = [r[0] for r in con.execute("SELECT DISTINCT d FROM r ORDER BY 1").fetchall()]
    nmap = dict(((p, t), n) for p, t, n in con.execute("SELECT pool, t, n_t1 FROM race").fetchall())
    leads, jobs, g4 = {}, [], {}
    for name, variant, farm, arm in LEADS:
        keys = (f"SELECT DISTINCT r.pool, r.t FROM r {'SEMI JOIN c3 USING (pool, t)' if arm else ''} WHERE r.variant = '{variant}' "
                f"AND r.d = 0" + (f" AND r.farm = '{farm}'" if farm else ""))
        fs = fee_stats(con, keys)
        fgrid = [("0", 0)] + [(k, fs[f"first_{k}"]) for k in ("p50", "p90") if fs[f"first_{k}"] is not None] \
            + [(f"{x:g}", int(round(x * 1e9))) for x in F_FIXED]
        L = {"variant": variant, "farm": farm or "all", "c3_arm": arm, "fees": fs, "f_grid": fgrid, "sizes": {}}
        for size in SIZES:
            capital = round(size / SHARE, 6)
            S = {"capital": capital, "scenarios": {}}
            for sc in SCENARIOS:
                cells, series, useries = {}, {}, {}
                for d in grid:
                    rows = cell_rows(con, variant, farm, arm, size, d, sc)
                    if not rows:
                        continue
                    u, x = unlimited(rows, days), limited(rows, days, capital, size, "deadline")
                    if d in (2, 3):
                        g4[(name, size, d, sc)] = (u, x)
                    series[d] = run_days(trades_of(rows), days, capital, size)[0]
                    useries[d] = day_sums(rows, days)
                    cells[d] = {"unlimited": u, "limited": x, "sandwich_flagged_pct": flag_share(con, variant, farm, arm, size, d)[0]}
                    if d == 0:
                        ns = [nmap[(r[8], r[0])] for r in rows]
                        rows1 = {(r[8], r[0]): r for r in cell_rows(con, variant, farm, arm, size, 1, sc)} if 1 in grid else {}
                        dist = Counter(min(n, 3) for n in ns)
                        cells[0]["n_t1"] = {str(k): dist.get(k, 0) for k in range(4)}
                        cells[0]["e_p_land"] = round(float(np.mean([p_land(n) for n in ns])), 4)
                        cells[0]["expected"] = {}
                        for fkey, F in fgrid:
                            for miss in MISSES:
                                alt_of = (lambda r: rows1.get((r[8], r[0]))) if miss == "d1" else (lambda r: None)
                                vals, e = expected(rows, ns, F, alt_of, scn[sc].failed_tx, days)
                                useries[("d0", fkey, miss)] = vals
                                cells[0]["expected"][f"{fkey}|{miss}"] = e
                        us = np.vstack([draws_u(r[8], r[0], draws) for r in rows])
                        jobs.append({"key": (name, size, sc), "rows0": rows, "ns": ns, "us": us,
                                     "alts": [rows1.get((r[8], r[0])) for r in rows], "fs": fgrid, "failed_tx": scn[sc].failed_tx,
                                     "days": days, "capital": capital, "size": size})
                S["scenarios"][sc] = {"cells": cells, "_series": series, "_useries": useries}
            L["sizes"][size] = S
        leads[name] = L
    # The draws, in parallel over (lead, size, scenario): spawned workers (no fork of a process with DuckDB's threads).
    if workers > 1 and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn")) as ex:
            done = dict(ex.map(draw_job, jobs))
    else:
        done = dict(draw_job(j) for j in jobs)
    for name, L in leads.items():
        for size, S in L["sizes"].items():
            for sc, X in S["scenarios"].items():
                cells, series, useries = X["cells"], X.pop("_series"), X.pop("_useries")
                res = done.get((name, size, sc))
                if res is not None:
                    cells[0]["drawn"] = {}
                    for (fkey, miss), v in res.items():
                        m, lo, hi = boot_days(v["series"])
                        cells[0]["drawn"][f"{fkey}|{miss}"] = {
                            "sol_day": m, "lo": lo, "hi": hi, "draws_p5": float(np.percentile(v["per_draw"], 5)),
                            "draws_p95": float(np.percentile(v["per_draw"], 95)), "ruined_draws_pct": round(100 * v["ruined"] / draws, 1),
                            "taken_per_day": round(v["taken_per_day"], 1)}
                        series[("d0", fkey, miss)] = v["series"]
                X["breakeven"] = {"limited": breakeven([(d, c["limited"]["sol_day"], c["limited"]["lo"]) for d, c in cells.items()]),
                                  "unlimited": breakeven([(d, c["unlimited"]["sol_day"], c["unlimited"]["lo"]) for d, c in cells.items()])}
                speed = {}
                fk = "p90" if any(k == "p90" for k, _ in L["f_grid"]) else "0"
                for ref in REF_D:
                    if ref not in series:
                        continue
                    for miss in MISSES:
                        if ("d0", fk, miss) in series:
                            speed[f"fastest_F{fk}_{miss}_vs_d{ref}"] = {"limited": paired(series[("d0", fk, miss)], series[ref]),
                                                                       "unlimited": paired(useries[("d0", fk, miss)], useries[ref])}
                    if 0 in series:
                        speed[f"oracle_vs_d{ref}"] = {"limited": paired(series[0], series[ref]), "unlimited": paired(useries[0], useries[ref])}
                    if 1 in series:
                        speed[f"fast_d1_vs_d{ref}"] = {"limited": paired(series[1], series[ref]), "unlimited": paired(useries[1], useries[ref])}
                X["speed"] = speed
                # Infra what-if (plan 6e): fastest = d 0 at F p90 (miss none) over d 3; fast = d 1 over d 3 less its tips.
                infra = {}
                if f"fastest_F{fk}_none_vs_d3" in speed:
                    infra["fastest_net_sol_day"] = tier_net(speed[f"fastest_F{fk}_none_vs_d3"]["limited"]["sol_day"], "fastest")
                if "fast_d1_vs_d3" in speed:
                    tips = FAST_TIP * 2 * cells[1]["limited"]["taken_per_day"]
                    d = speed["fast_d1_vs_d3"]["limited"]["sol_day"]
                    infra["fast_tips_sol_day"] = round(tips, 3)
                    infra["fast_net_sol_day"] = tier_net(None if d is None else d - tips, "fast")
                X["infra"] = infra
                # F*: the fee per d 0 attempt at which d 0 (p_land) equals the reference, without limits (exact).
                fstar = {}
                if 0 in cells:
                    for miss in MISSES:
                        e0 = cells[0]["expected"].get(f"0|{miss}")
                        for ref in REF_D:
                            if e0 and ref in cells and e0["attempts_per_day"]:
                                fstar[f"{miss}_vs_d{ref}"] = (e0["sol_day"] - cells[ref]["unlimited"]["sol_day"]) / e0["attempts_per_day"]
                X["f_star_sol"] = fstar
    return days, grid, leads, g4


def report_md(out):
    L = [f"# Racing cost on dev (E65; exploratory, in-sample; plan {PLAN}, sha256 {PLAN_SHA[:16]}…)"
         + (" — DRY RUN, not evidence" if out["dry_run"] else ""), "",
         f"Dev only, {len(out['days'])} UTC days ({out['days'][0]} … {out['days'][-1]}). d counts dev slots (266–317 ms); "
         "one d for entry and exit. The 600 ms baseline (Clock.slots rounds 600 ms to whole slots) is d 2 on dev and d 3 live "
         "now. Capital rules: E63's (size 5 % of C, at most 5 open, daily loss 10 % of C, 24 h cooldown, one open per pool, "
         "unsold freed at the deadline). d 0 = the fill on the trigger slot's end state (first in t + 1) and a zero-latency "
         "exit, with p_land 1 / 0.5 / 0.25 / 0.1 for 0 / 1 / 2 / >= 3 t + 1 token buys. Landed means first in t + 1; there "
         "is no landed-behind state: a miss is no position (failed transaction paid) or the d 1 row (mid t + 1); route "
         "(ii)'s K1 / K2 rows (E46, 0.5 SOL) bound landing behind one or two buys. F = extra SOL per d 0 attempt on top of "
         f"the scenario, whose per_tx is {out['per_tx']['base']:,} lamports in base ({out['per_tx']['pessimistic']:,} pessimistic), "
         "the tip counted twice in it. The leads are alternatives: they do not add up.", "",
         "The d 0 rows with p_land: the interval is the day bootstrap of the draw-averaged day series, i.e. the day CI of the "
         "expectation with the landing randomness averaged out; the draws' p5…p95 of SOL/day stand beside it. F* = the F at "
         "which d 0 equals the reference without limits, exactly, per d 0 attempt (every d 0 trigger, landed or not).", "",
         "Gates: " + "; ".join(f"{k} {v['status']}" for k, v in out["gates"].items()), ""]
    for name, Ld in out["leads"].items():
        fs = Ld["fees"]
        L += [f"## {name} ({Ld['variant']}, farm {Ld['farm']}{', E56 arm keys' if Ld['c3_arm'] else ''})", "",
              f"First t + 1 token buyer's priority fee + Jito tip (lamports, n >= 1, {fs['first_n']:,} triggers): p50 {fs['first_p50']}, "
              f"p90 {fs['first_p90']}, p99 {fs['first_p99']}; every t + 1 token-buy transaction ({fs['all_n']:,}): p50 {fs['all_p50']}, "
              f"p90 {fs['all_p90']}, p99 {fs['all_p99']}, {fs['all_tipped_pct']} % with a Jito tip. A lower bound (other relays' tips, "
              "dropped and failed transactions are not seen).", ""]
        for sc in SCENARIOS:
            L += [f"### Costs {sc}: net SOL/day with limits [95 % day CI]", "",
                  "| d | " + " | ".join(f"C {S['capital']:g} (size {s:g})" for s, S in Ld["sizes"].items()) + " |",
                  "|---|" + "---|" * len(Ld["sizes"])]
            grid = sorted({d for S in Ld["sizes"].values() for d in S["scenarios"][sc]["cells"]})
            for d in grid:
                row = [f"d {d}" + (" (oracle: p_land 1, F 0)" if d == 0 else (" (600 ms on dev)" if d == 2 else (" (live now)" if d == 3 else "")))]
                for S in Ld["sizes"].values():
                    c = S["scenarios"][sc]["cells"].get(d)
                    row.append("–" if c is None else f"{ci(c['limited']['sol_day'], c['limited']['lo'], c['limited']['hi'])}, "
                               f"{c['limited']['taken_per_day']:g}/day, failed {fmt(c['unlimited']['entry_failed_pct'], 1, False)} %"
                               + (" RUINED" if c["limited"]["ruined"] else ""))
                L.append("| " + " | ".join(row) + " |")
            for fkey, _ in Ld["f_grid"]:
                for miss in MISSES:
                    row = [f"d 0, p_land, F {fkey}, miss {'none' if miss == 'none' else '→ d 1'}"]
                    for S in Ld["sizes"].values():
                        c = S["scenarios"][sc]["cells"].get(0)
                        x = (c or {}).get("drawn", {}).get(f"{fkey}|{miss}")
                        row.append("–" if x is None else f"{ci(x['sol_day'], x['lo'], x['hi'])}, {x['taken_per_day']:g}/day, "
                                   f"draws {fmt(x['draws_p5'])}…{fmt(x['draws_p95'])}, ruined {x['ruined_draws_pct']:g} %")
                    L.append("| " + " | ".join(row) + " |")
            L += [""]
            for s, S in Ld["sizes"].items():
                X = S["scenarios"][sc]
                b = X["breakeven"]["limited"]
                c0 = X["cells"].get(0, {})
                L.append(f"- C {S['capital']:g}: break-even delay (with limits) "
                         + (f"> {max(b['grid'])} (none <= 0 on the grid)" if b["beyond_grid"] and b["grid"] else f"d {b['first_non_positive_d']}")
                         + f", lower bound > 0 up to d {b['lower_bound_positive_up_to_d']}; n t + 1 {c0.get('n_t1')}, E[p_land] {c0.get('e_p_land')}")
                for k, v in X["speed"].items():
                    L.append(f"  - {k}: with limits {ci(v['limited']['sol_day'], v['limited']['lo'], v['limited']['hi'])} SOL/day "
                             f"({fmt(v['limited']['sol_month'], 0)}/month); without {ci(v['unlimited']['sol_day'], v['unlimited']['lo'], v['unlimited']['hi'])}")
                for k, v in X["infra"].items():
                    L.append(f"  - {k}: {v if not isinstance(v, list) else '[' + ', '.join(fmt(x) for x in v) + ']'}")
                for k, v in X["f_star_sol"].items():
                    L.append(f"  - F* {k}: {fmt(v, 5)} SOL per d 0 attempt")
            L += [""]
    L += ["## Infra what-if (estimates; plan 6e)", "",
          f"SOL ${SOL_USD} (CoinGecko 2026-10-23); a month of 365/12 days, so $1,000/month = {usd_month_to_sol_day(1000):.3f} SOL/day "
          "(the speed values' 'per month' is 30 x per day). "
          + "; ".join(f"{k}: ${a:,}–{b:,}/month = {usd_month_to_sol_day(a):.3f}–{usd_month_to_sol_day(b):.3f} SOL/day ↔ d {d}"
                      for k, (a, b, d) in TIERS.items())
          + f". Fast adds a {FAST_TIP:g} SOL tip per transaction (entry and exit). Net = the paired gain over d 3 less the tier's "
          "extra fixed cost over as-now ([low, high]).", "",
          "## Caveats", "", out["caveats"], "", "## Provenance", ""]
    L += [f"- {p['run']}: engine {p['engine_commit']}, {p['rows']:,} rows, cells {p['cells']}" for p in out["provenance"]]
    return "\n".join(L) + "\n"


CAVEATS = ("In-sample: dev was looked at more than 1,300 times and H5 and the arm were chosen on it. d 0 is optimistic: all of "
           "slot t known, first in t + 1, a zero-latency exit; not a same-slot backrun. p_land is an assumption (OF1 shows only "
           "landed transactions) and does not depend on F. The observed fees are a lower bound. After a miss the busy chain is "
           "not reopened. Sandwiching is not modelled (E63's flag per cell). Live trigger supply is ~20–29 % of dev's. "
           "Pre-Alpenglow slots (266–317 ms against ~200 ms). The scenarios count the tip twice (kept for comparability with E63). "
           "T3: on processed a pump transaction reaches us p50 81 ms before its slot completes: whether d 0 is reachable live is open.")


def report(a):
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{a.memory}'; SET threads = {a.threads}")
    tmp = os.environ.get("LAB_SPILL_DIR")
    if tmp:
        con.execute(f"SET temp_directory = '{tmp}'")
    grid = grid_check(a.parts)
    info = load(con, a.parts, a.c3_keys, a.store)
    con.execute(f"CREATE TABLE race AS SELECT * FROM read_parquet('{os.path.join(a.race, 'race.parquet')}')")
    con.execute(f"CREATE TABLE race_txs AS SELECT * FROM read_parquet('{os.path.join(a.race, 'race_txs.parquet')}')")
    gates = {"G0": grid, "G1": {"status": "GREEN", "why": "every run passed the q guard (load)"},
             "G5": gate_g5(con, a.route_ii)}
    if gates["G5"]["rows_without_race"]:
        raise Refused(f"G5 RED: {gates['G5']['rows_without_race']} (pool, t) of the rows have no race row (build the table over the same runs)")
    gates["G2"], gates["G3"] = gate_g2(con, info["provenance"], a.route_ii), gate_g3(con, info["provenance"], a.route_ii)
    days, grid, leads, g4 = compute(con, a.draws, a.workers)
    with open(a.e63_json) as f:
        e63 = json.load(f)
    gates["G4"] = gate_g4(e63["cells"], g4)
    if e63["days"] != days:
        gates["G4"] = {"status": "RED", "why": f"days differ from E63's ({len(e63['days'])} vs {len(days)})"}
    cfg = load_defaults()
    per_tx = {s: Scenario.from_cfg(s, cfg).per_tx() for s in SCENARIOS}
    out = {"plan": PLAN, "plan_sha256": PLAN_SHA, "days": days, "grid": grid, "per_tx": per_tx, "ms_check_rows": info["ms_check_rows"], "provenance": info["provenance"], "c3_keys": info["c3_keys"],
           "store_commit": info["store_commit"], "draws": a.draws, "p_land": P_LAND, "sizes": SIZES, "share": SHARE,
           "tiers_usd_month": TIERS, "sol_usd": SOL_USD, "fast_tip_sol": FAST_TIP, "gates": gates, "leads": leads, "caveats": CAVEATS,
           "dry_run": any(p["dry_run_range"] for p in info["provenance"])}
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "racing.json"), "w") as f:
        json.dump(out, f, indent=1, default=str)
    with open(os.path.join(a.out, "racing.md"), "w") as f:
        f.write(report_md(out))
    red = {k: v for k, v in gates.items() if v["status"] == "RED"}
    print(f"{len(leads)} leads, d grid {grid}, {len(days)} days; gates "
          + ", ".join(f"{k} {v['status']}" for k, v in sorted(gates.items())) + f" (racing.json, racing.md)")
    return 1 if red else 0


# --- W8-B --------------------------------------------------------------------------------------------------------

E51_CHECK = {1: 1.48, 2: 0.35, 3: 0.05}  # E51: capped mean net per pool at 0.25 SOL, all pools, %
W8B_SIZES = (0.25, 0.5, 1.25, 5.0)
W8B_EXTRAS = (0.0, 0.006)  # e1's 0.4 pt network cost alone, and with E51's +0.6 pt extras (LAB review of the plan)


def w8b(a):
    """E51's probe rows (e1.py: 0.25 SOL, linear impact 0.25/Q, end-of-slot fills, d from the first non-boost trade
    slot) scaled to a size S by e1's own cost decomposition c = 2 fp/1e4 + imp + 0.004: net_S = g - 1 - 2 fp/1e4 -
    imp S / 0.25 - 0.001 / S, capped at +100 % as E51; without limits, on 324k-slot pseudo-days."""
    con = duckdb.connect()
    con.execute("SET memory_limit = '1GB'; SET threads = 2")
    cols = ["cs", "ep", "late_mig", "fp"] + [f"{x}{d}" for d in (1, 2, 3) for x in ("g", "c")]
    rows = con.execute(f"SELECT {', '.join(cols)} FROM read_parquet('{a.e1}')").fetchall()
    if not rows or any(not (1033 <= r[1] <= 1046) for r in rows):
        raise Refused("e1 rows outside dev epochs 1033-1046")
    cs = np.array([r[0] for r in rows], dtype=float)
    ndays = (cs.max() - cs.min()) / 324_000
    day = ((cs - cs.min()) // 324_000).astype(int)
    nclu = int(day.max()) + 1
    fp = np.array([r[3] for r in rows], dtype=float)
    late = [r[2] for r in rows]
    groups = {"all": np.ones(len(rows), bool), "same_slot (post hoc)": np.array([x is False for x in late]),
              "late_migration": np.array([x is True for x in late])}
    res = {"source": a.e1, "pools": len(rows), "pseudo_days": round(ndays, 2), "cells": []}
    for di, d in enumerate((1, 2, 3)):
        g = np.array([np.nan if r[4 + 2 * di] is None else r[4 + 2 * di] for r in rows], dtype=float)
        c = np.array([np.nan if r[5 + 2 * di] is None else r[5 + 2 * di] for r in rows], dtype=float)
        imp = c - 2 * fp / 1e4 - 0.004
        for S in W8B_SIZES:
            for extra in W8B_EXTRAS:
                net = np.minimum(g - 1 - 2 * fp / 1e4 - imp * S / 0.25 - 0.001 / S - extra, 1.0)
                for gname, mask in groups.items():
                    ok = mask & np.isfinite(net)
                    sums = np.bincount(day[ok], weights=net[ok] * S, minlength=nclu)
                    m, lo, hi = boot_days(list(sums))
                    f = nclu / ndays
                    res["cells"].append({"d": d, "size": S, "extra": extra, "group": gname, "n": int(ok.sum()),
                                         "capped_mean_pct": round(100 * float(net[ok].mean()), 2), "sol_day": m * f, "lo": lo * f, "hi": hi * f})
    chk = {d: next(x["capped_mean_pct"] for x in res["cells"] if x["d"] == d and x["size"] == 0.25 and x["group"] == "all" and x["extra"] == 0)
           for d in (1, 2, 3)}
    res["e51_check"] = {"expected": E51_CHECK, "got": chk, "status": "GREEN" if chk == E51_CHECK else "RED"}
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "w8b.json"), "w") as f:
        json.dump(res, f, indent=1)
    L = ["# W8-B on E51's probe rows (E65 plan section 7; arithmetic, not comparable with H5 and C3)", "",
         f"{res['pools']:,} pools over {res['pseudo_days']} pseudo-days of 324,000 slots. e1's d counts from the first non-boost "
         "trade slot f and fills at the end of f + d (behind every buy of that slot); there is no e1 d 0. 'On dev 600 ms lies "
         "between e1's d 1 and d 2, live between d 2 and d 3' is a convention mapping, not a measurement. Without limits, no "
         "p_land, no observed fees: e1's flat 0.4 pt network cost (0.001 SOL per round trip), and beside it E51's +0.6 pt extras "
         "(the basis H5 and C3 are not on either). The impact is e1's linear 0.25/Q scaled with the size (imp x S / 0.25): a lower "
         "bound on the cost, whose error grows with S (E51's caveat). Same-slot is a post-hoc subgroup. "
         f"E51 check at 0.25 SOL: {res['e51_check']['status']} ({chk}).", "",
         "| group | size | extras | d 1 | d 2 | d 3 |", "|---|---|---|---|---|---|"]
    for gname in groups:
        for S in W8B_SIZES:
            for extra in W8B_EXTRAS:
                cells = {x["d"]: x for x in res["cells"] if x["group"] == gname and x["size"] == S and x["extra"] == extra}
                L.append(f"| {gname} | {S:g} | +{100 * extra:.1f} pt | "
                         + " | ".join(f"{cells[d]['capped_mean_pct']:+.2f} %, {ci(cells[d]['sol_day'], cells[d]['lo'], cells[d]['hi'])} SOL/day"
                                      for d in (1, 2, 3)) + " |")
    with open(os.path.join(a.out, "w8b.md"), "w") as f:
        f.write("\n".join(L) + "\n")
    print(f"w8b: {res['pools']} pools; E51 check {res['e51_check']['status']} {chk}")
    return 0 if res["e51_check"]["status"] == "GREEN" else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("table")
    t.add_argument("--parts", nargs="+", required=True)
    t.add_argument("--store", required=True)
    t.add_argument("--out", required=True)
    r = sub.add_parser("report")
    r.add_argument("--parts", nargs="+", required=True, help="tournament run dirs (config.json + parts/), in priority order")
    r.add_argument("--race", required=True, help="the table's output dir (race.parquet, race_txs.parquet)")
    r.add_argument("--c3-keys", required=True)
    r.add_argument("--store", required=True)
    r.add_argument("--route-ii", required=True, help="backtests/route-ii-full-547f422/entry-rows/part-000000.parquet (gates G2, G3, G5)")
    r.add_argument("--e63-json", required=True, help="E63's ceiling.json (gate G4)")
    r.add_argument("--out", required=True)
    r.add_argument("--draws", type=int, default=DRAWS)
    r.add_argument("--workers", type=int, default=1)
    w = sub.add_parser("w8b")
    w.add_argument("--e1", required=True)
    w.add_argument("--out", required=True)
    for p in (t, r):
        p.add_argument("--memory", default=os.environ.get("LAB_DUCKDB_MEM", "2GB"))
        p.add_argument("--threads", type=int, default=2)
    a = ap.parse_args(argv)
    if a.cmd == "table":
        con = duckdb.connect()
        con.execute(f"SET memory_limit = '{a.memory}'; SET threads = {a.threads}")
        tmp = os.environ.get("LAB_SPILL_DIR")
        if tmp:
            con.execute(f"SET temp_directory = '{tmp}'")
        table(con, a.parts, a.store, a.out)
        return 0
    if a.cmd == "report":
        return report(a)
    return w8b(a)


if __name__ == "__main__":
    sys.exit(main())
