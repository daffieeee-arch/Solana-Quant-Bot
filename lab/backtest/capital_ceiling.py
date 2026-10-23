"""The capital ceiling on dev (research/capital-ceiling-plan-2026-10-23.md, sha256 2a17f2d2; exploratory, no new
rules): per candidate, the net SOL per day at total capital C = 10, 25 and 100 SOL, with the size per trade rising
with C, the engine's own fills against the real reserves at that size (a tournament run over a size grid), paper's
risk rules scaled with C, the network costs of costs.settle_sql and the delay d of the run; and the size at which
the price impact eats the edge.

  python -m backtest.capital_ceiling --parts RUN_DIR [RUN_DIR ...] --c3-keys PARQUET --out DIR [--store dev2.duckdb]
                                     [--parity REF_RUN_DIR]

- Rows: the runs' F7 pool_reversed positions at q 0.5, overlay none, not skipped. For a (variant, size, d) the first
  RUN_DIR with rows supplies all of them (no mixing of engines in one cell). Dev only: every run's store must be
  dev2's window [446,256,000, 452,304,000) and every row inside it; anything else is refused.
- Candidates: K3 = tp4_T600, all farms; H5 = flow300_tp100_H6, farm organic; C3 = E56's arm keys (pool, t with
  paid_buyers_60s >= 2, from the paid-flow markout's d 2 / 5 m rows: the key set is the same for every d, h and size)
  on the tp4_T600 rows and on the hold300 rows (the engine exit nearest E56's 5-minute markout; the run's busy is
  the full F7 one, so its entries are a lower bound). W8-B has no engine rows: E51's row is cited, not recomputed.
- Without limits: the sum of the net per UTC day of the decision window (a day without a trade counts 0), with a
  day bootstrap (10,000 draws, seed 7).
- With limits (plan section 1): paper's risk.yaml scaled with C (size 5 % of C, primary; 10 %, sensitivity; at most
  5 open, daily loss 10 % of C, 24 h cooldown after a total loss, one open position per pool) through
  capital_days, the day-series twin of analysis._capital_limited. A position holds its place from the trigger slot
  t; an unsold one (exit_slot NULL) is freed at exit_decision_slot + d (primary, the rule's deadline), never
  (paper as it runs) or at its entry (the analysis tools).
- The sandwich flag of analysis.f7_candidate section 4 (size x 1e9 > (lp + protocol + creator bps) / 1e4 x depth),
  with --store: a share per cell, not modelled.

Writes ceiling.json (every cell) and ceiling.md (the headline per candidate and C, the tables per candidate).
"""

import argparse
import heapq
import json
import os
import sys
from collections import defaultdict

import duckdb
import numpy as np

from .costs import Scenario, settle_sql
from .store import load_defaults

PLAN = "research/capital-ceiling-plan-2026-10-23.md"
PLAN_SHA = "2a17f2d2ea9d6e8f46bee05eb69e1f6427ab38ca456c794671baf587b3110916"
DEV = (446_256_000, 452_304_000)  # dev2's window: epochs 1033-1046
CAPITALS = (10, 25, 100)
MAPPINGS = (("primary", 0.05), ("sensitivity", 0.10))  # size per trade as a share of C (paper: 0.5 of 10 SOL)
MAX_OPEN = 5
DAILY_LOSS = 0.10  # of C (paper: 1 of 10 SOL)
COOLDOWN_MS = 24 * 3_600_000
GOAL = 20  # SOL per day (owner)
DRAWS, SEED = 10_000, 7
SCENARIOS = ("base", "pessimistic")
UNSOLD = ("deadline", "never", "entry")
PRIMARY = {"d": 3, "scenario": "base", "unsold": "deadline", "mapping": "primary"}
# (name, variant, farm, C3 arm)
CANDIDATES = (("K3", "tp4_T600", None, False), ("H5", "flow300_tp100_H6", "organic", False),
              ("C3/tp4_T600", "tp4_T600", None, True), ("C3/hold300", "hold300", None, True))
W8B = ("W8-B (E51; probe scripts/w8est/e1.py, 0.25 SOL, linear impact 0.25/Q, not replayed by the engine): capped net "
       "per pool d 2 +0.35 % [-0.20; +0.90], d 3 +0.05 % [-0.56; +0.64], 780 pools a day; its size curve and capital "
       "ceiling are unknown until the engine can replay it.")
GAP = ("Dev, pre-Alpenglow, a ceiling. Live paper on 22 Oct, the first whole day after Alpenglow (exit day UTC, "
       "triggers from epoch 1064; read-only journal snapshot sha256 1691ddc9…, 23 Oct 00:04Z): K3 (F7 tp4_T600, 0.5 SOL "
       "of 10) realised +1.06 SOL over 135 exits, with 3 positions open in emptied pools marked -1.50 SOL; the N2 "
       "comparison arm (a control, not a strategy) -1.87 SOL over 317 exits; group H entered nothing (5 positions stuck "
       "open since 21 Oct, max open). Live F7 trigger supply that day: 398, 180 of them organic, against dev's "
       "~2,040 and ~630 a day (40,737 and 12,641 over 20 days). Do not read a dev ceiling as reachable.")


class Refused(SystemExit):
    pass


def boot_days(vals, draws=DRAWS, seed=SEED):
    """Mean per day with a 95 % interval from resampling whole days."""
    a = np.asarray(vals, dtype=float)
    if len(a) == 0:
        return None, None, None
    rng = np.random.default_rng(seed)
    m = a[rng.integers(0, len(a), size=(draws, len(a)))].mean(1)
    return float(a.mean()), float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def capital_days(trades, start, max_positions, daily_loss, cooldown_ms, size=None):
    """analysis._capital_limited with its day series: the same simulation (one open position per pool, no entries on
    a UTC day once its realized P&L reaches -daily_loss, a pool cooldown after a total loss, max_positions, cash) and
    the same 12-tuple, plus a dict of the extras: day_pnl {day: lamports}, taken per day, the peak of open positions
    and the ruin day. trades: (entry, exit, cost, pnl, failed, day, pool, proceeds, ms_per_slot), in lamports, sorted.

    size=None is _capital_limited exactly. With size (lamports, the order size) an entry that will fail needs the cash
    too: _capital_limited takes it without (a failed row has cost 0), but that it fails is known only afterwards, so
    a ruined account went on 'taking' exactly the entries that fail (review of this PR). Then the drawdown also
    counts their fees, and ruin_day is the first trigger day an entry finds too little cash with nothing open (no
    exit can bring cash back: the account is done)."""
    cash, open_, locked, peak, mdd = start, [], 0, start, 0.0
    taken = skipped = skip_pool = skip_day = skip_cool = 0
    day_pnl = defaultdict(float)
    day_taken = defaultdict(int)
    open_pools, cool_until = defaultdict(int), {}
    peak_open = 0
    ruin_day = None

    def settle_until(slot):
        nonlocal cash, locked, peak, mdd
        while open_ and open_[0][0] <= slot:
            exit_slot, cost, back, pool, day, pnl, rug_until = heapq.heappop(open_)
            cash, locked = cash + back, locked - cost
            open_pools[pool] -= 1
            day_pnl[day] += pnl
            if rug_until:
                cool_until[pool] = max(cool_until.get(pool, 0), rug_until)
            peak = max(peak, cash + locked)
            mdd = max(mdd, 1 - (cash + locked) / peak)

    days = set()
    for entry, exit_, cost, pnl, failed, day, pool, proceeds, mps in trades:
        days.add(day)
        settle_until(entry)
        cost, pnl = cost or 0, pnl or 0
        if day_pnl[day] <= -daily_loss:
            skip_day += 1
            continue
        if cool_until.get(pool, -1) > entry:
            skip_cool += 1
            continue
        if open_pools[pool] >= 1:
            skip_pool += 1
            continue
        short = cash < cost if not failed else (size is not None and cash < size)
        if len(open_) >= max_positions or short:
            skipped += 1
            if short and not open_ and ruin_day is None:
                ruin_day = day
            continue
        taken += 1
        day_taken[day] += 1
        if failed:
            cash += pnl
            day_pnl[day] += pnl
            if size is not None:
                peak = max(peak, cash + locked)
                mdd = max(mdd, 1 - (cash + locked) / peak)
            continue
        x = exit_ or entry
        rug = (proceeds or 0) < 0.1 * cost
        cash, locked = cash - cost, locked + cost
        open_pools[pool] += 1
        heapq.heappush(open_, (x, cost, cost + pnl, pool, day, pnl, x + round(cooldown_ms / mps) if rug else 0))
        peak_open = max(peak_open, len(open_))
    settle_until(float("inf"))
    vals = [day_pnl.get(d, 0.0) / 1e9 for d in sorted(days)]
    out = (len(trades), taken, skip_pool, skip_cool, skip_day, skipped, round(cash / 1e9, 3), round(mdd * 100, 1),
           sum(v > 0 for v in vals), len(vals), round(min(vals), 3) if vals else None, round(sum(vals) / len(vals), 3) if vals else None)
    return out, {"day_pnl": dict(day_pnl), "day_taken": dict(day_taken), "peak_open": peak_open, "ruin_day": ruin_day}


def exit_of(row, unsold):
    """The slot that frees a position's place: its exit, or for an unsold one (exit_slot NULL) the rule's deadline
    (exit_decision_slot + d), never (the dev window's end), or its entry (None: capital_days frees it at entry)."""
    exit_slot, decision, d = row
    if exit_slot is not None:
        return exit_slot
    if unsold == "deadline":
        return (decision + d) if decision is not None else DEV[1]
    if unsold == "never":
        return DEV[1]
    return None


def check_run(run_dir):
    """A run of dev2's window only: its config's store and decide range."""
    with open(os.path.join(run_dir, "config.json")) as f:
        cfg = json.load(f)
    st = cfg.get("store") or {}
    if (st.get("slot_start"), st.get("slot_end_exclusive")) != DEV:
        raise Refused(f"{run_dir}: store window [{st.get('slot_start')}, {st.get('slot_end_exclusive')}) is not dev2's {list(DEV)}")
    dr = cfg.get("decide_range") or cfg.get("slot_range")
    if dr and not (DEV[0] <= dr[0] and dr[1] <= DEV[1]):
        raise Refused(f"{run_dir}: decide range {dr} outside dev")
    return cfg


def load(con, runs, c3_keys, store=None):
    """The rows table r (settled under both scenarios) and the C3 keys table c3."""
    cfg = load_defaults()
    pnl = {s: settle_sql(Scenario.from_cfg(s, cfg))[0] for s in SCENARIOS}
    variants = sorted({c[1] for c in CANDIDATES})
    vlist = ", ".join(f"'{v}'" for v in variants)
    con.execute("""CREATE TABLE r (src INTEGER, variant VARCHAR, size_sol DOUBLE, d INTEGER, t UBIGINT, day VARCHAR,
                   pool VARCHAR, farm VARCHAR, entry_failed BOOLEAN, cost BIGINT, proceeds BIGINT, exit_slot UBIGINT,
                   exit_decision_slot UBIGINT, d_ms DOUBLE, depth DOUBLE, pnl_base BIGINT, pnl_pessimistic BIGINT)""")
    provenance = []
    for i, run in enumerate(runs):
        cfg_run = check_run(run)
        parts = os.path.join(run, "parts", "*.parquet")
        src = f"read_parquet('{parts}', union_by_name = true)"
        cols = {c[0] for c in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()}
        if "entry" in cols and con.execute(f"SELECT count(*) FROM {src} WHERE entry IS NOT NULL").fetchone()[0]:
            raise Refused(f"{run}: route-(ii) entry rows (--entries); a ceiling run has none")
        taus = [r[0] for r in con.execute(f"SELECT DISTINCT tau FROM {src} WHERE family = 'F7'").fetchall()] if "tau" in cols else ["empirical"]
        if len(taus) > 1:
            raise Refused(f"{run}: more than one tau mode {taus}")
        overlay = "COALESCE(overlay, 'none') = 'none'" if "overlay" in cols else "true"
        # A (variant, size, d) comes from the first run that has it.
        con.execute(f"""INSERT INTO r SELECT {i}, variant, size_sol, d, t, day, pool, farm, entry_failed, cost, proceeds, exit_slot,
                          exit_decision_slot, d_ms, depth, {pnl['base']}, {pnl['pessimistic']}
                        FROM {src} x WHERE family = 'F7' AND segment = 'pool_reversed' AND q = 0.5 AND {overlay}
                          AND variant IN ({vlist}) AND NOT skipped
                          AND NOT EXISTS (SELECT 1 FROM r WHERE r.variant = x.variant AND r.size_sol = x.size_sol AND r.d = x.d)""")
        n = con.execute(f"SELECT count(*) FROM r WHERE src = {i}").fetchone()[0]
        provenance.append({"run": run, "engine_commit": cfg_run.get("engine_commit"), "rows": n,
                           "dry_run_range": cfg_run.get("slot_range"), "sizes": cfg_run.get("sizes_sol"), "delays": cfg_run.get("delays"),
                           "cells": [list(c) for c in con.execute(f"SELECT DISTINCT variant, size_sol, d FROM r WHERE src = {i} ORDER BY ALL").fetchall()]})
    dup = con.execute("SELECT variant, size_sol, d, pool, t, count(*) FROM r GROUP BY ALL HAVING count(*) > 1 LIMIT 3").fetchall()
    if dup:
        raise Refused(f"duplicate positions (variant, size, d, pool, t), e.g. {dup}: a run that mixes two runs' parts")
    lo, hi = con.execute("SELECT min(t), max(greatest(t, COALESCE(exit_slot, 0), COALESCE(exit_decision_slot, 0))) FROM r").fetchone()
    if lo is None:
        raise Refused("no rows of the candidates' variants")
    if lo < DEV[0] or hi >= DEV[1]:
        raise Refused(f"rows reach outside dev: [{lo}, {hi}]")
    con.execute(f"""CREATE TABLE c3 AS SELECT DISTINCT pool, t FROM read_parquet('{c3_keys}')
                    WHERE d = 2 AND h = '5m' AND paid_buyers_60s >= 2""")
    klo, khi, nk = con.execute("SELECT min(t), max(t), count(*) FROM c3").fetchone()
    if not nk or klo < DEV[0] or khi >= DEV[1]:
        raise Refused(f"C3 keys: {nk} keys in [{klo}, {khi}], not dev")
    flags = False
    if store:
        con.execute(f"ATTACH '{store}' AS st (READ_ONLY)")
        m = json.loads(con.execute("SELECT json FROM st.meta").fetchone()[0])
        if (m.get("slot_start"), m.get("slot_end_exclusive")) != DEV:
            raise Refused(f"{store}: window [{m.get('slot_start')}, {m.get('slot_end_exclusive')}) is not dev2's")
        con.execute("CREATE TEMP TABLE trig AS SELECT DISTINCT pool, t FROM r")
        con.execute("""CREATE TABLE fee AS SELECT e.pool, e.slot AS t, max(e.lp_bps + e.protocol_bps + e.creator_bps) / 1e4 AS f
                       FROM st.pool e SEMI JOIN trig g ON e.pool = g.pool AND e.slot = g.t
                       WHERE e.kind IN ('buy', 'sell') GROUP BY ALL""")
        flags = True
    return {"provenance": provenance, "c3_keys": nk, "sandwich_flags": flags}


def cell_rows(con, variant, farm, arm, size, d, scenario):
    where = f"r.variant = '{variant}' AND r.size_sol = {size} AND r.d = {d}"
    if farm:
        where += f" AND r.farm = '{farm}'"
    join = "SEMI JOIN c3 USING (pool, t)" if arm else ""
    return con.execute(f"""SELECT r.t, r.exit_slot, r.exit_decision_slot, r.d, r.cost, r.pnl_{scenario}, r.entry_failed, r.day,
                                  r.pool, r.proceeds, r.d_ms / r.d
                           FROM r {join} WHERE {where} ORDER BY r.t, r.exit_slot NULLS LAST, r.pool""").fetchall()


def unlimited(rows, days):
    """Without limits: count, fills, failures, unsold, the net per filled trade and per day."""
    per_day = defaultdict(float)
    filled = [r for r in rows if not r[6]]
    for r in rows:
        per_day[r[7]] += (r[5] or 0) / 1e9
    mean, lo, hi = boot_days([per_day.get(x, 0.0) for x in days])
    net_filled = [(r[5] or 0) / 1e9 for r in filled]
    ret_filled = [(r[5] or 0) / (r[4] + 0.0) for r in filled if r[4]]
    return {"rows": len(rows), "per_day_rows": round(len(rows) / len(days), 1), "filled": len(filled),
            "entry_failed_pct": round(100 * (len(rows) - len(filled)) / len(rows), 2) if rows else None,
            "unsold_pct": round(100 * sum(1 for r in filled if r[1] is None) / len(filled), 2) if filled else None,
            "net_per_filled_sol": round(float(np.mean(net_filled)), 5) if filled else None,
            "net_per_filled_pct_of_cost": round(100 * float(np.mean(ret_filled)), 3) if ret_filled else None,
            "sol_day": mean, "lo": lo, "hi": hi}


def limited(rows, days, capital, size, unsold):
    trades = [(r[0], exit_of((r[1], r[2], r[3]), unsold), r[4], r[5], r[6], r[7], r[8], r[9], r[10]) for r in rows]
    trades.sort(key=lambda x: (x[0], x[1] if x[1] is not None else -1))
    out, extra = capital_days(trades, capital * 1e9, MAX_OPEN, DAILY_LOSS * capital * 1e9, COOLDOWN_MS, size=int(round(size * 1e9)))
    vals = [extra["day_pnl"].get(x, 0.0) / 1e9 for x in days]
    mean, lo, hi = boot_days(vals)
    keys = ("signals", "taken", "skip_pool", "skip_cooldown", "skip_daily_limit", "skip_full_or_cash", "final_sol",
            "max_drawdown_pct", "positive_days_traded", "days_traded", "worst_day_traded", "mean_day_traded")
    res = dict(zip(keys, out))
    res.update({"capital": capital, "size": size, "unsold": unsold, "taken_per_day": round(out[1] / len(days), 1),
                "sol_day": mean, "lo": lo, "hi": hi, "positive_days": sum(v > 0 for v in vals), "days": len(days),
                "worst_day": round(min(vals), 3), "peak_open": extra["peak_open"], "ruined": out[6] < size,
                "ruin_day": days.index(extra["ruin_day"]) + 1 if extra["ruin_day"] in days else None})
    return res


def flag_share(con, variant, farm, arm, size, d):
    where = f"r.variant = '{variant}' AND r.size_sol = {size} AND r.d = {d}" + (f" AND r.farm = '{farm}'" if farm else "")
    join = "SEMI JOIN c3 USING (pool, t)" if arm else ""
    v, cov = con.execute(f"""SELECT avg(({size} * 1e9 > fee.f * r.depth)::INT), avg((fee.f IS NOT NULL)::INT)
                             FROM (SELECT * FROM r {join} WHERE {where}) r LEFT JOIN fee USING (pool, t)""").fetchone()
    # A row without a buy or sell leg at (pool, t) has no fee: it drops out of the share (LAB review on #529: the coverage).
    return (None if v is None else round(100 * v, 1)), (None if cov is None else round(100 * cov, 1))


def arm_unmatched(con, variant, size, d):
    """The arm keys without a row in a (variant, size, d), by what made them busy: an earlier arm position in the same
    pool (one open per pool, as in paper), an earlier non-arm position (the run's full-F7 busy: the lower-bound loss),
    or neither (no row)."""
    rows = f"(SELECT pool, t, COALESCE(exit_slot, exit_decision_slot + d, t) AS x FROM r WHERE variant = '{variant}' AND size_sol = {size} AND d = {d})"
    return dict(zip(("busy_arm", "busy_non_arm", "no_row"), con.execute(f"""
        WITH v AS {rows}, k AS (SELECT * FROM c3 ANTI JOIN v USING (pool, t)),
             b AS (SELECT k.pool, k.t, bool_or(a.pool IS NOT NULL) AS by_arm, count(v.pool) AS n FROM k
                   LEFT JOIN v ON v.pool = k.pool AND v.t < k.t AND k.t <= v.x
                   LEFT JOIN c3 a ON a.pool = v.pool AND a.t = v.t GROUP BY ALL)
        SELECT count(*) FILTER (WHERE n > 0 AND by_arm), count(*) FILTER (WHERE n > 0 AND NOT by_arm), count(*) FILTER (WHERE n = 0)
        FROM b""").fetchone()))


def compute(con, flags):
    days = [r[0] for r in con.execute("SELECT DISTINCT day FROM r ORDER BY 1").fetchall()]
    cells = []
    for name, variant, farm, arm in CANDIDATES:
        grid = con.execute(f"SELECT DISTINCT size_sol, d FROM r WHERE variant = '{variant}' ORDER BY ALL").fetchall()
        for size, d in grid:
            for scen in SCENARIOS:
                rows = cell_rows(con, variant, farm, arm, size, d, scen)
                c = {"candidate": name, "variant": variant, "farm": farm or "all", "c3_arm": arm, "size": size, "d": d,
                     "scenario": scen, "unlimited": unlimited(rows, days), "limited": []}
                if flags and scen == "base":
                    c["sandwich_flagged_pct"], c["sandwich_fee_coverage_pct"] = flag_share(con, variant, farm, arm, size, d)
                if arm:
                    c["arm_keys_matched"] = len({(r[8], r[0]) for r in rows})
                    c["arm_keys_unmatched"] = arm_unmatched(con, variant, size, d)
                for mapping, frac in MAPPINGS:
                    cap = round(size / frac, 6)
                    for unsold in UNSOLD:
                        lim = limited(rows, days, cap, size, unsold)
                        lim.update({"mapping": mapping, "named_capital": cap in CAPITALS})
                        c["limited"].append(lim)
                cells.append(c)
    return days, cells


def pick(cells, name, size, d, scen, mapping, unsold):
    for c in cells:
        if (c["candidate"], c["size"], c["d"], c["scenario"]) == (name, size, d, scen):
            for x in c["limited"]:
                if x["mapping"] == mapping and x["unsold"] == unsold:
                    return c, x
    return None, None


def max_useful(cells, name, d=PRIMARY["d"], scen=PRIMARY["scenario"], mapping=PRIMARY["mapping"], unsold=PRIMARY["unsold"]):
    """The largest size whose capital-limited SOL/day (point) is > 0 at C = size / share; the size where the net per
    filled trade first turns <= 0; the size with the highest capital-limited SOL/day."""
    frac = dict(MAPPINGS)[mapping]
    curve = []
    for c in cells:
        if (c["candidate"], c["d"], c["scenario"]) == (name, d, scen):
            x = next(x for x in c["limited"] if x["mapping"] == mapping and x["unsold"] == unsold)
            curve.append((c["size"], x["sol_day"], c["unlimited"]["net_per_filled_sol"]))
    curve.sort()
    if not curve:
        return None
    curve = [x for x in curve if x[1] is not None]
    if not curve:
        return None
    pos = [s for s, v, _ in curve if v > 0]
    eaten = next((s for s, _, n in curve if n is not None and n <= 0), None)
    already = eaten is not None and eaten == curve[0][0]
    # A clean curve (LAB review on #529): with-limits SOL/day never rises with size, and no size below the largest
    # positive one is <= 0.
    monotone = all(a[1] >= b[1] for a, b in zip(curve, curve[1:]))
    best = max(curve, key=lambda x: x[1])
    top = max(s for s, _, _ in curve)
    largest = max(pos) if pos else None
    return {"sizes": [s for s, _, _ in curve], "largest_positive_size": largest,
            "max_useful_capital": None if largest is None else round(largest / frac, 2),
            "positive_at_grid_top": bool(pos) and largest == top, "edge_eaten_at_size": eaten, "no_edge_at_smallest": already,
            "monotone": monotone, "non_positive_below_largest": [s for s, v, _ in curve if largest is not None and s < largest and v <= 0],
            "best_size": best[0], "best_sol_day": best[1]}


def fmt(v, nd=2, sign=True):
    if v is None:
        return "–"
    return f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}"


def ci(m, lo, hi, nd=2):
    return "–" if m is None else f"{fmt(m, nd)} [{fmt(lo, nd)}; {fmt(hi, nd)}]"


def lim(x):
    """A with-limits SOL/day: ruined, it is the capital lost over the window's days, not a rate."""
    if x["ruined"]:
        when = f"day {x['ruin_day']} of {x['days']}" if x["ruin_day"] else "by the end"
        return f"ruined ({when}; final {x['final_sol']:.2f} of {x['capital']:g} SOL)"
    return ci(x["sol_day"], x["lo"], x["hi"])


def report_md(out):
    cells, days = out["cells"], out["days"]
    p = PRIMARY
    dry = [p_["dry_run_range"] for p_ in out["provenance"] if p_.get("dry_run_range")]
    L = [f"# Capital ceiling on dev (exploratory; plan {PLAN}, sha256 {PLAN_SHA[:16]}…)"
         + (f" — DRY RUN over {dry}, not evidence" if dry else ""), "",
         f"Dev only, {len(days)} UTC days ({days[0]} … {days[-1]}). Primary: d = {p['d']} slots, costs {p['scenario']}, "
         f"size {dict(MAPPINGS)['primary'] * 100:g} % of C, at most {MAX_OPEN} open, daily loss {DAILY_LOSS * 100:g} % of C, "
         "24 h cooldown, an unsold position freed at its rule's deadline. The candidate lines are alternatives on the same "
         "triggers: they do not add up.", "",
         f"Owner's goal: >= {GOAL} SOL/day, i.e. a return of 20/C per day: "
         + ", ".join(f"{GOAL / c * 100:g} % of {c} SOL" for c in CAPITALS) + ".", "",
         "## Headline: net SOL/day at C (95 % day-bootstrap interval)", "",
         "| candidate | " + " | ".join(f"C {c}: size, taken/day, SOL/day" for c in CAPITALS) + " | max useful capital | edge eaten at size |",
         "|---|" + "---|" * (len(CAPITALS) + 2)]
    for name, *_ in CANDIDATES:
        row = [name]
        for cap in CAPITALS:
            size = round(cap * dict(MAPPINGS)["primary"], 6)
            c, x = pick(cells, name, size, p["d"], p["scenario"], "primary", p["unsold"])
            row.append("not in the run" if x is None else ("no rows" if not x["signals"] else
                       f"{size:g} SOL, {x['taken_per_day']:g}/day, {lim(x)}"))
        mu = out["max_useful"].get(name)
        if mu is None:
            row += ["–", "–"]
        else:
            if mu["largest_positive_size"] is None:
                row.append("none (no size > 0)")
            elif mu["positive_at_grid_top"] or mu["max_useful_capital"] >= max(CAPITALS):
                row.append(f">= {max(CAPITALS)} SOL (positive up to size {mu['largest_positive_size']:g}, C {mu['max_useful_capital']:g})")
            else:
                row.append(f"{mu['max_useful_capital']:g} SOL (size {mu['largest_positive_size']:g})")
            if mu["largest_positive_size"] is not None and not mu["monotone"]:
                row[-1] += (" — curve not monotone" + (f"; <= 0 at {', '.join(f'{s:g}' for s in mu['non_positive_below_largest'])}"
                                                      if mu["non_positive_below_largest"] else ""))
            row.append(f"not within the grid (> {max(mu['sizes']):g} SOL)" if mu["edge_eaten_at_size"] is None else
                       (f"no edge even at {mu['edge_eaten_at_size']:g} SOL (the smallest size)" if mu["no_edge_at_smallest"]
                        else f"{mu['edge_eaten_at_size']:g} SOL"))
        L.append("| " + " | ".join(row) + " |")
    L += ["", GAP, "", W8B, ""]
    for name, variant, farm, arm in CANDIDATES:
        L += [f"## {name} ({variant}, farm {farm or 'all'}{', E56 arm keys' if arm else ''})", "",
              "Size curve (without limits; with limits at C = size / 5 %, unsold freed at the deadline):", "",
              "| size | d | costs | rows/day | entry failed % | unsold % | net per filled trade SOL (% of cost) | without limits SOL/day | "
              "with limits SOL/day | taken/day | worst day | max DD % | sandwich flag % |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for c in sorted((c for c in cells if c["candidate"] == name), key=lambda c: (c["d"] != p["d"], c["scenario"] != "base", c["size"], c["d"])):
            u = c["unlimited"]
            x = next(x for x in c["limited"] if x["mapping"] == "primary" and x["unsold"] == "deadline")
            L.append(f"| {c['size']:g} | {c['d']} | {c['scenario']} | {u['per_day_rows']:g} | {fmt(u['entry_failed_pct'], 1, False)} | "
                     f"{fmt(u['unsold_pct'], 2, False)} | {fmt(u['net_per_filled_sol'], 4)} ({fmt(u['net_per_filled_pct_of_cost'], 2)} %) | "
                     f"{ci(u['sol_day'], u['lo'], u['hi'])} | {lim(x)} | {x['taken_per_day']:g} | "
                     f"{fmt(x['worst_day'])} | {fmt(x['max_drawdown_pct'], 1, False)} | {fmt(c.get('sandwich_flagged_pct'), 1, False)}"
                     + (f" (of {c['sandwich_fee_coverage_pct']:.0f} % with a fee)" if c.get("sandwich_fee_coverage_pct") is not None else "") + " |")
        L += ["", GAP, "", "At the named capitals (both mappings, every unsold convention):", "",
              "| C | mapping | size | d | costs | unsold | taken/day | skips pool / cooldown / daily / full | SOL/day | positive days | worst day | max DD % | peak open | final SOL |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for c in sorted((c for c in cells if c["candidate"] == name), key=lambda c: (c["d"] != p["d"], c["scenario"] != "base", c["size"])):
            for x in c["limited"]:
                if not x["named_capital"]:
                    continue
                L.append(f"| {x['capital']:g} | {x['mapping']} | {c['size']:g} | {c['d']} | {c['scenario']} | {x['unsold']} | {x['taken_per_day']:g} | "
                         f"{x['skip_pool']} / {x['skip_cooldown']} / {x['skip_daily_limit']} / {x['skip_full_or_cash']} | "
                         f"{lim(x)} | {x['positive_days']}/{x['days']} | {fmt(x['worst_day'])} | "
                         f"{fmt(x['max_drawdown_pct'], 1, False)} | {x['peak_open']} | {fmt(x['final_sol'], 2, False)}{' (ruined)' if x['ruined'] else ''} |")
        if arm:
            L += ["", f"Arm keys {out['c3_keys']:,}. Per (size, d): matched / unmatched because busy from an earlier arm position "
                  "in the same pool (one open per pool, as in paper) / busy from a non-arm position (the full-F7 busy of the run: "
                  "the entries lost, so this line is a lower bound) / no row:", ""]
            seen = set()
            for c in sorted((c for c in cells if c["candidate"] == name), key=lambda c: (c["size"], c["d"])):
                if (c["size"], c["d"]) in seen:
                    continue
                seen.add((c["size"], c["d"]))
                u = c["arm_keys_unmatched"]
                L.append(f"- {c['size']:g} SOL, d {c['d']}: {c['arm_keys_matched']:,} / {u['busy_arm']:,} / {u['busy_non_arm']:,} / {u['no_row']:,}")
        L += ["", GAP, ""]
    L += ["## Provenance", ""] + [f"- {p_['run']}: engine {p_['engine_commit']}, {p_['rows']:,} rows, cells {p_['cells']}" for p_ in out["provenance"]]
    if not out.get("parity"):
        L += ["", "Parity against families-v2-1033-1046: not run."]
    else:
        L += ["", "Parity (tp4_T600 without limits, base, against the reference run):", ""]
        L += [f"- size {x['size']:g} d {x['d']}: rows {x['rows']} vs {x['ref_rows']}, SOL/day {fmt(x['sol_day'], 4)} vs {fmt(x['ref_sol_day'], 4)}: {x['status']}"
              for x in out["parity"]]
    return "\n".join(L) + "\n"


def parity(con, ref, days):
    """tp4_T600 without limits (base) at 0.5 / 1 / 2 SOL, d 2 / 3, against a reference run: rows and SOL/day."""
    cfg = load_defaults()
    pnl = settle_sql(Scenario.from_cfg("base", cfg))[0]
    check_run(ref)
    src = f"read_parquet('{os.path.join(ref, 'parts', '*.parquet')}', union_by_name = true)"
    out = []
    for size in (0.5, 1.0, 2.0):
        for d in (2, 3):
            mine = con.execute(f"SELECT count(*), sum(pnl_base) / 1e9 FROM r WHERE variant = 'tp4_T600' AND size_sol = {size} AND d = {d}").fetchone()
            if not mine[0]:
                out.append({"size": size, "d": d, "rows": 0, "ref_rows": None, "sol_day": None, "ref_sol_day": None,
                            "status": "RED (not in this run)"})
                continue
            refr = con.execute(f"""SELECT count(*), sum({pnl}) / 1e9 FROM {src} WHERE family = 'F7' AND segment = 'pool_reversed' AND q = 0.5
                                   AND COALESCE(overlay, 'none') = 'none' AND variant = 'tp4_T600' AND size_sol = {size} AND d = {d} AND NOT skipped""").fetchone()
            a, b = mine[1] / len(days), (refr[1] or 0) / len(days)
            ok = mine[0] == refr[0] and abs(a - b) <= 0.001
            out.append({"size": size, "d": d, "rows": mine[0], "ref_rows": refr[0], "sol_day": round(a, 4), "ref_sol_day": round(b, 4),
                        "status": "GREEN" if ok else "RED"})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--parts", nargs="+", required=True, help="tournament run dirs (config.json + parts/), in priority order")
    ap.add_argument("--c3-keys", required=True, help="wallets/paidflow-dev-vps/markout-f7rev-txdelay.parquet")
    ap.add_argument("--out", required=True)
    ap.add_argument("--store", help="dev2.duckdb for the sandwich flag (optional)")
    ap.add_argument("--parity", help="a reference run (families-v2-1033-1046) for tp4_T600 at 0.5 / 1 / 2 SOL")
    ap.add_argument("--memory", default=os.environ.get("LAB_DUCKDB_MEM", "2GB"))
    ap.add_argument("--threads", type=int, default=2)
    a = ap.parse_args(argv)
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{a.memory}'; SET threads = {a.threads}")
    tmp = os.environ.get("LAB_SPILL_DIR")
    if tmp:
        con.execute(f"SET temp_directory = '{tmp}'")
    info = load(con, a.parts, a.c3_keys, a.store)
    days, cells = compute(con, info["sandwich_flags"])
    out = {"plan": PLAN, "plan_sha256": PLAN_SHA, "days": days, "c3_keys": info["c3_keys"], "provenance": info["provenance"],
           "primary": PRIMARY, "capitals": CAPITALS, "mappings": dict(MAPPINGS), "max_open": MAX_OPEN, "daily_loss_share": DAILY_LOSS,
           "cells": cells, "max_useful": {name: max_useful(cells, name) for name, *_ in CANDIDATES}, "w8b": W8B, "gap": GAP}
    if a.parity:
        out["parity"] = parity(con, a.parity, days)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "ceiling.json"), "w") as f:
        json.dump(out, f, indent=1, default=str)
    with open(os.path.join(a.out, "ceiling.md"), "w") as f:
        f.write(report_md(out))
    red = [x for x in out.get("parity") or [] if x["status"] != "GREEN"]
    print(f"{len(cells)} cells over {len(days)} days; parity {'RED: ' + str(red) if red else ('GREEN' if out.get('parity') else 'not run')} "
          f"(ceiling.json, ceiling.md)")
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
