"""Run the v1 tournament on the dev store and write results + a report.

  scripts/backtest-run.sh <run_id> --families F7,F1,N1,N2 --sizes 0.5,2,10 --delays 1,2

Results go to $LAB_BACKTESTS/<run_id>/: parts/ (one Parquet file per batch, written by the
workers, so memory stays flat and an interrupted run resumes where it stopped) are the position
results, written once (no merged copy: the disk's write volume is limited). Then config.json,
trials.parquet, report.md and, last, summary.parquet: a run is complete when that file exists.
The hold-out is not reachable from here: this only opens the dev store.
"""

import argparse
import bisect
import json
import multiprocessing as mp
import os
import random
import subprocess
import sys
import time
from collections import defaultdict

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from . import health, overlay, replay, rotation, streams
from .costs import LAMPORTS, Scenario, settle_sql
from .report import GATE_SIZES
from .store import load_defaults
from .strategies import (
    BURN_IN_MS,
    END_GUARD_MS,
    M1_BUYERS_MS,
    M1_GRID,
    M1_MAX_HOLD_MS,
    M1_MIN_BUYERS,
    M1_SIZES,
    M1X,
    M1X_TRAILS,
    M1Exit,
    m1_candidates_sql,
    m1x_candidates_sql,
    m1x_variant,
    m1x_vetoed,
    F6_FLOW_MS,
    F6_GRID,
    F6_MAX_HOLD_MS,
    F7_EXIT_GRID,
    F7_GRID,
    F7_LONG_GRID,
    M1_FLOW,
    M1_FLOW_EXIT_MS,
    M1_LONG_TRAILS,
    M1FlowExit,
    m1_long_variant,
    Clock,
    F6Exit,
    FlowIndex,
    N3_HOLD_MS,
    N4_MAX_HOLD,
    N4Exit,
    PastView,
    Signal,
    f1_candidates,
    f1_exit,
    f1_signal,
    f6_min_depth,
    f6_signals_sql,
    f7_signals_sql,
    n1_candidates,
    n2_matches,
    n4_signals_sql,
    ms,
    f7_exit_rule,
    f7_exit_variant,
    tp_sl_time,
    until_slot,
)

STORE = os.environ.get("LAB_STORE", "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb")
OUT = os.environ.get("LAB_BACKTESTS", "/home/chupa/Solana-project/data-old-faithful-one/lab/backtests")

RESULT_SCHEMA = pa.schema([
    ("family", pa.string()), ("variant", pa.string()), ("mint", pa.string()), ("t", pa.int64()),
    ("venue", pa.string()), ("depth", pa.int64()), ("ref_mint", pa.string()), ("ref_t", pa.int64()),
    ("size_sol", pa.float64()), ("d", pa.int64()), ("tau", pa.string()),
    ("entry_failed", pa.bool_()), ("skipped", pa.bool_()), ("tokens", pa.int64()), ("cost", pa.int64()),
    ("proceeds", pa.int64()), ("exit_reason", pa.string()), ("entry_slot", pa.int64()),
    ("exit_decision_slot", pa.int64()), ("exit_slot", pa.int64()), ("cf_graduation", pa.bool_()),
    ("seed_pool", pa.bool_()), ("vault_capped", pa.bool_()), ("exit_attempts", pa.int64()), ("reverted_direct", pa.int64()),
    ("reverted_router", pa.int64()), ("sells_dropped", pa.int64()), ("sells_scaled", pa.int64()),
    ("hist_ret", pa.float64()), ("day", pa.string()),
    ("pool", pa.string()), ("orientation", pa.string()), ("segment", pa.string()), ("regime", pa.string()),
    ("farm", pa.string()), ("overlay", pa.string()), ("q", pa.float64()), ("trigger_trader", pa.string()),
    ("trigger_farmer", pa.bool_()),
    ("d_ms", pa.float64()),
    # M1+ / M1-early (and their N2): the veto inputs at t and the highest mark before the exit (/ cost - 1).
    ("ins_share", pa.float64()), ("curve_buyers", pa.int64()), ("peak_ret", pa.float64()),
    # Why ins_share is undefined (circ_le_0, h_ins_gt_circ, h_ins_lt_0, insiders_unknown; NULL when it is defined
    # or not asked): M1x and O7 rows.
    ("ins_reason", pa.string()),
    # Every position: the highest mark since the entry (the simulated full sale at that moment, lamports, the
    # same quantity as proceeds before costs) and its slot; NULL without a mark (no fill).
    ("peak_mark", pa.int64()), ("peak_slot", pa.int64()),
])


def io_written():
    """Bytes this process has written to storage (incl. DuckDB spill), from /proc/self/io."""
    try:
        with open("/proc/self/io") as f:
            return next(int(l.split()[1]) for l in f if l.startswith("write_bytes"))
    except (OSError, StopIteration):
        return 0


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg} [main wrote {io_written() / 1e9:.2f} GB]", flush=True)


def store_bounds(con):
    meta = json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
    return meta["slot_start"], meta["slot_end_exclusive"], meta


# Windows in milliseconds (v1 slot values at 267.3 ms), converted per epoch by the store's Clock.
MAX_HOLD_MS = {"F7": ms(2_400), "N2_F7": ms(2_400), "F1": ms(4_500), "N2_F1": ms(4_500), "N1": ms(1_500),
               "F6": F6_MAX_HOLD_MS, "N2_F6": F6_MAX_HOLD_MS, "N3": N3_HOLD_MS, "N4": ms(N4_MAX_HOLD),
               "S1": 86_400_000, "N2_S1": 86_400_000, "M1": M1_MAX_HOLD_MS, "N2_M1": M1_MAX_HOLD_MS,
               **{f: M1_MAX_HOLD_MS for f in ("M1P", "N2_M1P", "M1E", "N2_M1E")}}
M1_LIKE = ("M1", "N2_M1", "M1P", "N2_M1P", "M1E", "N2_M1E")  # trailing / hard / time stop with the O7 exit
FAMILY_SIZES = {f: M1_SIZES for f in M1_LIKE}  # families with their own size grid  # S1: only when no planned exit (end of data)
S1_OPEN_HOLD = 10**9  # slots: an S1 position without a planned exit runs to the end of the data
WINDOW_MARGIN_MS = ms(6_000)  # d, graduation and pool open after the last possible exit decision
CLUSTER_GAP_MS = ms(20_000)  # signals further apart than this are loaded and simulated separately
MAX_WINDOW_MS = ms(50_000)  # cap on one load window (busy pools)
F1_CHUNK = 200  # tokens per stream load while screening F1 (whole curve histories)
M1_CHUNK = int(os.environ.get("LAB_M1_CHUNK", "25"))  # young pools per stream load (busy pools: many legs)


F6_STATS = {}


INSIDER_FALLBACKS = [0]  # per process: token_insiders calls left window-dependent (run-info "insider_fallbacks")


def token_insiders(con, tok, lo, clock):
    """overlay.Insiders of a token stream loaded with pool legs from `lo` on, over the token's whole
    history: the pool's trades before lo enter as offsets summed in SQL (busy pools have millions of
    events; the legs are not loaded). Without them insider sells before the window were invisible and
    the share depended on where the caller's window started.

    Only for a pool in the normal orientation (the token as base, as Insiders counts it). A token whose
    pool is reversed falls back to the window-dependent Insiders and is counted in INSIDER_FALLBACKS
    (the run's config.json): an O7 figure on such tokens is not covered by this fix. Counting the
    token side of a reversed pool (quote_net) is a later change."""
    w = clock.slots(overlay.INSIDER_WINDOW_MS, tok.create_slot)
    ins = overlay.Insiders(tok, w)
    if not ins.known or tok.pool is None:
        return ins  # unknown insiders, or no pool: the curve legs are complete from the create on
    if tok.orientation != "normal":
        INSIDER_FALLBACKS[0] += 1
        return ins
    # The insider wallets as a temporary table for the IN filter (registered for this query only).
    con.register("ins_w", pa.table({"w": sorted(ins.insiders)}))
    h0, c0, s0 = con.execute("""
        SELECT COALESCE(sum(CASE WHEN trader IN (SELECT w FROM ins_w) THEN d END), 0), COALESCE(sum(d), 0),
               count(*) FILTER (WHERE trader = ? AND d < 0)
        FROM (SELECT trader, CASE WHEN kind = 'buy' THEN base ELSE -base END AS d FROM pool
              WHERE pool = ? AND kind IN ('buy', 'sell') AND slot < ?)""", [tok.creator, tok.pool, lo]).fetchone()
    con.unregister("ins_w")
    return overlay.Insiders(tok, w, before=(int(h0), int(c0), int(s0))) if (h0 or c0 or s0) else ins


def m1_signals(con, cfg, lo, hi, clock):
    """M1 triggers: for each young pool and k, the first candidate event (price >= k x graduation
    price, organic flow) that also has >= 20 distinct organic buyers over the last 15 min and an
    insider share <= 25% (O7). Returns [(mint, pool, t, depth, k)]."""
    cands = defaultdict(list)
    for mint, pool, t, depth, ratio, cslot in m1_candidates_sql(con, cfg, lo, hi, clock):
        cands[pool].append((mint, t, depth, ratio))
    out = []
    pools = sorted(cands)
    for i in range(0, len(pools), M1_CHUNK):
        part = pools[i:i + M1_CHUNK]
        mints = {cands[p][0][0]: max(c[1] for c in cands[p]) for p in part}
        los = {m: min(c[1] for p in part for c in cands[p] if c[0] == m) for m in mints}
        toks = streams.load_streams(con, {m: (los[m], t) for m, t in mints.items()})
        for pool in part:
            mint = cands[pool][0][0]
            tok = toks.get(mint)
            if tok is None:
                continue
            ins = token_insiders(con, tok, los[mint], clock)
            if not ins.known:
                continue
            buys = [(l.slot, l.trader) for l in tok.pool_legs if l.kind == "buy" and not l.farmer]
            bslots = [b[0] for b in buys]
            todo = {k for k, _ in M1_GRID}
            for _, t, depth, ratio in cands[pool]:
                ks = [k for k in sorted(todo) if ratio >= k]
                if not ks:
                    continue
                lo_b = t - clock.slots(M1_BUYERS_MS, t)
                a, b = bisect.bisect_left(bslots, lo_b), bisect.bisect_right(bslots, t)
                # The O7 entry condition, fail-closed: an undefined insider share is no trigger either.
                if len({buys[j][1] for j in range(a, b)}) < M1_MIN_BUYERS or overlay.vetoes(ins.share(t), overlay.VETO_SHARE):
                    continue
                for k in ks:
                    out.append((mint, pool, t, depth, k))
                    todo.discard(k)
                if not todo:
                    break
    return out


def m1x_signals(con, cfg, lo, hi, clock, family):
    """M1+ (family 'M1P') or M1-early ('M1E') triggers: every (pool, moment, k) whose state at
    t = open + moment meets the trigger, for tokens whose insiders are known (as M1). Returns
    [(mint, pool, moment_ms, t, depth, k)]."""
    spec = M1X[family]
    cands = defaultdict(list)
    for mint, pool, moment, t, depth, ratio, flow, buyers in m1x_candidates_sql(con, cfg, lo, hi, spec["moments_ms"], clock):
        if flow > 0 and buyers >= M1_MIN_BUYERS and ratio >= min(spec["ks"]):
            cands[pool].append((mint, moment, t, depth, ratio))
    out = []
    pools = sorted(cands)
    for i in range(0, len(pools), M1_CHUNK):
        part = pools[i:i + M1_CHUNK]
        mints = {cands[p][0][0]: max(c[2] for c in cands[p]) for p in part}
        toks = streams.load_streams(con, {m: (min(c[2] for p in part for c in cands[p] if c[0] == m), t) for m, t in mints.items()})
        for pool in part:
            tok = toks.get(cands[pool][0][0])
            if tok is None or not overlay.Insiders(tok, clock.slots(overlay.INSIDER_WINDOW_MS, tok.create_slot)).known:
                continue
            for mint, moment, t, depth, ratio in cands[pool]:
                out += [(mint, pool, moment, t, depth, k) for k in spec["ks"] if ratio >= k]
    return sorted(out, key=lambda x: (x[3], x[1], x[2], x[5]))


def grid_set(spec, allowed, every):
    """A grid option as a set: comma separated names from `allowed`, or "all" (= `every`)."""
    names = {x for x in str(spec).split(",") if x}
    if "all" in names:
        names = (names - {"all"}) | set(every)
    if not names or names - set(allowed):
        raise ValueError(f"grid {spec!r}: choose from {sorted(allowed)} or all")
    return names


def build_signals(con, cfg, families, sizes, n1_sample, max_signals, n2_k=5, clock=None, segments=None, slot_range=None,
                  f7_grid="v1", m1_grid="v1"):
    f7g = grid_set(f7_grid, ("v1", "exits", "long"), ("v1", "exits"))
    m1g = grid_set(m1_grid, ("v1", "long"), ("v1", "long"))
    lo, hi, _ = store_bounds(con)
    clock = clock or Clock.from_store(con)
    if slot_range:  # dry runs: decisions in [a, b] only, with the burn-in before a and the end guard after b as data
        a, b = slot_range
        lo, hi = max(lo, a - clock.slots(BURN_IN_MS, a)), min(hi, b + clock.slots(END_GUARD_MS, b))
    signals = []
    creators = dict(con.execute("SELECT mint, creator FROM mints").fetchall())
    if "F7" in families:
        for venue, m, slot, depth, pool, orientation, trader, tfarmer, flagged in f7_signals_sql(con, cfg, lo, hi, clock=clock):
            if segments and ("curve" if venue == "curve" else f"pool_{orientation}") not in segments:
                continue
            m, pool = sys.intern(m), (sys.intern(pool) if pool else None)
            for tp, T in (F7_GRID if "v1" in f7g else ()):
                signals.append(Signal("F7", f"tp{int(tp*100)}_T{T}", m, slot, venue, int(depth), (tp, T),
                                      pool=pool, orientation=orientation, trigger=trader,
                                      trigger_farmer=None if tfarmer is None else bool(tfarmer),
                                      flagged=None if flagged is None else bool(flagged)))
            exits = (F7_EXIT_GRID if "exits" in f7g else []) + (F7_LONG_GRID if "long" in f7g else [])
            for params in exits:  # spec E exits (kind, x, sl) and the long exits (kind, x, sl, cap_ms)
                signals.append(Signal("F7", f7_exit_variant(*params), m, slot, venue, int(depth), params,
                                      pool=pool, orientation=orientation, trigger=trader,
                                      trigger_farmer=None if tfarmer is None else bool(tfarmer),
                                      flagged=None if flagged is None else bool(flagged)))
        log(f"F7: {len(signals)} signals")
    if "F6" in families:
        n6 = 0
        for m, pool, orientation, slot, depth, min_depth, flagged in f6_signals_sql(con, cfg, lo, hi, clock=clock):
            m, pool = sys.intern(m), sys.intern(pool)
            for trail in F6_GRID:
                signals.append(Signal("F6", f"trail{int(trail * 100)}", m, slot, "pool", int(depth), (trail, int(min_depth)),
                                      pool=pool, orientation=orientation, flagged=bool(flagged)))
                n6 += 1
        log(f"F6: {n6} signals")
        from .strategies import F6_DAY_COUNTS
        if F6_DAY_COUNTS:
            c = np.array(F6_DAY_COUNTS)
            F6_STATS.update(sampled_pool_days=len(c), triggers_median=float(np.median(c)), triggers_p90=float(np.percentile(c, 90)),
                            dropped_share=float(1 - len(c) / c.sum()))
    if "S1" in families:
        info = {p: (m, o) for p, m, o in con.execute("SELECT pool, COALESCE(mint, pool), orientation FROM pools").fetchall()}
        n_s1 = 0
        # The run's sizes (owner gate 2026-10-10: 0.5 and 1 SOL), not S1's own spec grid.
        for fam, variant, size, pool, t, exit_t, flagged in rotation.s1_positions(
                con, lo, hi, sizes=tuple(sizes), n2_k=rotation.N2_K if "N2" in families else 0):
            m, o = info[pool]
            signals.append(Signal(fam, variant, sys.intern(m), t, "pool", 0, (size, exit_t), pool=sys.intern(pool),
                                  orientation=o, flagged=bool(flagged)))
            n_s1 += 1
        log(f"S1: {n_s1} positions (S1 and N2_S1)")
    if "M1" in families:
        n_m1 = 0
        for mint, pool, t, depth, k in m1_signals(con, cfg, lo, hi, clock):
            for kk, trail in (M1_GRID if "v1" in m1g else ()):
                if kk == k:
                    signals.append(Signal("M1", f"k{k}_trail{int(trail * 100)}", sys.intern(mint), t, "pool", int(depth),
                                          (k, trail), pool=sys.intern(pool), orientation="normal"))
                    n_m1 += 1
            for ex in ((*M1_LONG_TRAILS, M1_FLOW) if "long" in m1g else ()):  # the long exits: trail30/50, flow300
                signals.append(Signal("M1", m1_long_variant("M1", k, ex), sys.intern(mint), t, "pool", int(depth),
                                      (k, ex), pool=sys.intern(pool), orientation="normal"))
                n_m1 += 1
        log(f"M1: {n_m1} signals")
    for fam in ("M1P", "M1E"):
        if fam in families:
            n_x = 0
            for mint, pool, moment, t, depth, k in m1x_signals(con, cfg, lo, hi, clock, fam):
                for trail in (M1X_TRAILS if "v1" in m1g else ()):
                    signals.append(Signal(fam, m1x_variant(fam, moment, k, trail), sys.intern(mint), t, "pool", int(depth or 0),
                                          (k, trail, moment), pool=sys.intern(pool), orientation="normal"))
                    n_x += 1
                for ex in ((*M1_LONG_TRAILS, M1_FLOW) if "long" in m1g else ()):
                    signals.append(Signal(fam, m1_long_variant(fam, k, ex, moment), sys.intern(mint), t, "pool", int(depth or 0),
                                          (k, ex, moment), pool=sys.intern(pool), orientation="normal"))
                    n_x += 1
            log(f"{fam}: {n_x} signals")
    if "N4" in families:
        for m, pool, orientation, t, depth in n4_signals_sql(con, cfg, lo, hi, n1_sample, clock):
            signals.append(Signal("N4", "crank_scalp", sys.intern(m), t, "pool", int(depth or 0), (),
                                  pool=sys.intern(pool), orientation=orientation))
        log(f"N4: {sum(1 for s in signals if s.family == 'N4')} signals")
    if "F1" in families or "N3" in families:
        cands = defaultdict(list)
        for band in (40, 55, 70):
            for m, t in f1_candidates(con, cfg, lo, hi, band, clock):
                cands[m].append((band, t))
        mints = sorted(cands)
        n_f1 = 0
        for i in range(0, len(mints), F1_CHUNK):
            part = mints[i:i + F1_CHUNK]
            ss = streams.load_streams(con, {m: (0, max(t for _, t in cands[m])) for m in part})
            for m in part:
                if m not in ss:
                    continue
                for band, t in sorted(cands[m]):
                    v = PastView(ss[m], t)
                    if "F1" in families and v.curve and f1_signal(v, creators.get(m), min(sizes) * LAMPORTS, clock):
                        signals.append(Signal("F1", f"B{band}", sys.intern(m), t, "curve", int(v.curve[-1].post.rq), (band,)))
                        n_f1 += 1
                    if "N3" in families and band == 55 and v.curve and f1_signal(
                            v, creators.get(m), min(sizes) * LAMPORTS, clock, flow=False):
                        signals.append(Signal("N3", "B55_hold", sys.intern(m), t, "curve", int(v.curve[-1].post.rq), ()))
            del ss
        log(f"F1: {n_f1} signals from {sum(len(c) for c in cands.values())} candidates")
    if "N1" in families:
        cands = n1_candidates(con, cfg, lo, hi, clock)
        random.Random(1).shuffle(cands)
        for m, cslot in sorted(cands[:n1_sample], key=lambda c: c[1]):
            for H in (150, 1_500):
                signals.append(Signal("N1", f"H{H}", sys.intern(m), cslot, "curve", 0, (H,)))
    # Deterministic order (SQL results come back in no fixed order), then the optional cap.
    signals.sort(key=lambda s: (s.t, s.mint, s.pool or "", s.family, s.variant))
    if max_signals:
        signals = signals[:max_signals]
    if "N2" in families:
        by_trigger = defaultdict(list)
        for s in signals:
            if s.family in ("F7", "F1", "F6", "M1", "M1P", "M1E"):  # S1 draws its own N2 controls (rotation.py)
                by_trigger[(s.family, s.pool or s.mint, s.t, s.venue, s.depth, s.orientation)].append(s)
        t0 = time.time()
        # M1 families: young pools of the same age class (M1, M1+: <= 6 h; M1-early: <= 10 min).
        ages = {"M1": M1_MAX_HOLD_MS, **{f: v["n2_age_ms"] for f, v in M1X.items()}}
        matches = n2_matches(con, [k for k in by_trigger if k[0] not in ages], k=n2_k, clock=clock)
        for fam, age in ages.items():
            matches.update(n2_matches(con, [k for k in by_trigger if k[0] == fam], k=n2_k, clock=clock, max_age_ms=age))
        pool_mint = dict(con.execute("SELECT pool, COALESCE(mint, pool) FROM pools").fetchall())
        n_n2 = 0
        for key, group in by_trigger.items():
            ref = (key[1], key[2])
            for k, slot, depth in matches[key]:
                pool, mint = (k, sys.intern(pool_mint.get(k, k))) if key[3] == "pool" else (None, k)
                for s2 in group:
                    signals.append(Signal("N2_" + s2.family, s2.variant, mint, slot, s2.venue, depth, s2.params, ref,
                                          pool=pool, orientation=s2.orientation))
                    n_n2 += 1
        log(f"N2: {n_n2} signals for {len(by_trigger)} triggers in {time.time() - t0:.0f}s")
    return signals


def long_cap_ms(s):
    """The cap of a long F7 exit (params (kind, x, sl, cap_ms)); None for every other signal."""
    if s.family in ("F7", "N2_F7") and len(s.params) == 4 and isinstance(s.params[0], str):
        return s.params[3]
    return None


def work_items(signals, clock=None):
    """Group signals per token (curve) or pool into clusters; each cluster has its own load
    window, capped in length so a busy pool never loads more than MAX_WINDOW_MS at once."""
    clock = clock or Clock()
    by_key = defaultdict(list)
    for s in signals:  # the long exits apart (their own window): the other variants' clusters stay as they were
        by_key[(s.venue, s.pool if s.venue == "pool" else s.mint, long_cap_ms(s))].append(s)
    items = []
    for key, sigs in by_key.items():
        sigs.sort(key=lambda s: s.t)
        cluster = [sigs[0]]
        for s in sigs[1:]:
            if (s.t - cluster[-1].t > clock.slots(CLUSTER_GAP_MS, s.t)
                    or s.t - cluster[0].t > clock.slots(MAX_WINDOW_MS, s.t)):
                items.append((key, cluster))
                cluster = []
            cluster.append(s)
        items.append((key, cluster))
    out = []
    def end(s):
        if s.family in ("S1", "N2_S1") and s.params[1] is not None:
            return s.params[1]  # the planned rebalance exit
        return s.t + clock.slots(long_cap_ms(s) or MAX_HOLD_MS.get(s.family, ms(4_500)), s.t)

    for (venue, k, _), c in items:
        lo = c[0].t - 300
        hi = max(end(s) for s in c) + clock.slots(WINDOW_MARGIN_MS, c[-1].t)
        out.append((venue, k, lo, hi, c))
    return out


def exit_rule_for(sig, scn, d, clock=None, stream=None, flows=None, insiders=None):
    clock = clock or Clock()
    if sig.family in M1_LIKE:
        if sig.params[1] == M1_FLOW:  # --m1-grid long: flow300 with M1Exit's hard stop, time and O7
            rule = M1FlowExit(flows if flows is not None else FlowIndex(stream), clock.slots(M1_FLOW_EXIT_MS, sig.t),
                              clock.slots(M1_MAX_HOLD_MS, sig.t))
        else:
            rule = M1Exit(sig.params[1], clock.slots(M1_MAX_HOLD_MS, sig.t))
        return overlay.O7Exit(rule, insiders) if insiders is not None else rule
    if sig.family in ("S1", "N2_S1"):
        size, exit_t = sig.params
        return rotation.S1Exit(exit_t, f6_min_depth(size), stream.orientation, S1_OPEN_HOLD)
    if sig.family in ("F6", "N2_F6"):
        return F6Exit(sig.params[0], flows if flows is not None else FlowIndex(stream), clock.slots(F6_FLOW_MS, sig.t),
                      clock.slots(F6_MAX_HOLD_MS, sig.t), stream.orientation)
    if sig.family in ("F7", "N2_F7"):
        if isinstance(sig.params[0], str):  # spec E exits: (kind, parameter, stop-loss)
            if sig.params[0] == "flow" and flows is None:
                flows = FlowIndex(stream)
            return f7_exit_rule(sig.params, scn, sig.t, clock, flows if sig.params[0] == "flow" else None)
        tp, T = sig.params
        return tp_sl_time(tp, -0.08, clock.slots(ms(T), sig.t), scn)
    if sig.family in ("F1", "N2_F1"):
        return f1_exit(sig.depth, clock.slots(ms(4_500), sig.t))
    if sig.family == "N1":
        return until_slot(sig.t + clock.slots(ms(sig.params[0]), sig.t), d)
    if sig.family == "N3":
        return until_slot(sig.t + d + clock.slots(N3_HOLD_MS, sig.t), d)
    if sig.family == "N4":
        return N4Exit(sorted(l.slot for l in stream.pool_legs if l.kind == "boost"), N4_MAX_HOLD)
    raise ValueError(sig.family)


_BLOCKS = None


def _days(con, slots):
    """UTC day of the last block at or before each slot."""
    global _BLOCKS
    if _BLOCKS is None:
        b = con.execute("SELECT slot, block_time FROM blocks WHERE block_time IS NOT NULL ORDER BY slot").fetchnumpy()
        _BLOCKS = (np.asarray(b["slot"], dtype=np.int64), np.asarray(b["block_time"], dtype=np.int64))
    bs, bt = _BLOCKS
    i = np.clip(np.searchsorted(bs, np.asarray(slots, dtype=np.int64), side="right") - 1, 0, len(bs) - 1)
    return np.datetime_as_string((bt[i] // 86_400).astype("datetime64[D]")).tolist()


def parse_only(specs):
    """--only rules, e.g. ["size=0.25:M1,N2_M1", "d=3:F7/pool_reversed,N2_F7/pool_reversed"]: that size,
    d or q is simulated only for the listed families (and segment, when given)."""
    out = {}
    for spec in specs or ():
        dim_val, fams = spec.split(":")
        dim, val = dim_val.split("=")
        if dim not in ("size", "d", "q"):
            raise SystemExit(f"--only: unknown dimension {dim}")
        out[(dim, float(val))] = [tuple(f.split("/")) if "/" in f else (f, None) for f in fams.split(",")]
    return out


def _segment(sig):
    return "curve" if sig.venue == "curve" else f"pool_{sig.orientation}"


def _allowed(only, sig, **dims):
    for dim, val in dims.items():
        rule = only.get((dim, float(val)))
        if rule is not None and not any(f == sig.family and (g is None or g == _segment(sig)) for f, g in rule):
            return False
    return True


def _part_path(out, task_id):
    return os.path.join(out, "parts", f"part-{task_id:06d}.parquet")


O7_FAMILIES = ("F7", "N2_F7", "F1", "N2_F1")


def _row(sig, r, mint, size_sol, d, tau_mode, clock, ov="none", q=0.5, ins_share=None, curve_buyers=None, peak_ret=None,
         ins_reason=None):
    return {
        "ins_share": ins_share, "curve_buyers": curve_buyers, "peak_ret": peak_ret, "ins_reason": ins_reason,
        "family": sig.family, "variant": sig.variant, "mint": mint, "t": sig.t, "venue": sig.venue,
        "depth": sig.depth, "ref_mint": sig.ref[0] if sig.ref else None, "ref_t": sig.ref[1] if sig.ref else None,
        "size_sol": size_sol, "d": d, "tau": tau_mode, "entry_failed": r.entry_failed, "skipped": r.skipped,
        "tokens": r.tokens, "cost": r.cost, "proceeds": r.proceeds, "exit_reason": r.exit_reason,
        "entry_slot": r.entry_slot, "exit_decision_slot": r.exit_decision_slot, "exit_slot": r.exit_slot,
        "cf_graduation": r.cf_graduation, "seed_pool": r.seed_pool, "vault_capped": r.vault_capped,
        "exit_attempts": r.exit_attempts, "reverted_direct": r.reverted_direct, "reverted_router": r.reverted_router,
        "sells_dropped": r.sells_dropped, "sells_scaled": r.sells_scaled,
        "hist_ret": (r.hist_mid_exit / r.hist_mid_entry - 1) if (r.hist_mid_entry and r.hist_mid_exit) else None,
        "pool": sig.pool, "orientation": sig.orientation,
        "segment": _segment(sig),
        "farm": "n/a" if sig.flagged is None else ("flagged" if sig.flagged else "organic"),
        "regime": clock.regime_label(sig.t), "d_ms": round(d * clock.ms_per_slot(sig.t), 1), "overlay": ov,
        "q": q, "trigger_trader": sig.trigger, "trigger_farmer": sig.trigger_farmer,
        **_peak(r),
    }


def _peak(r):
    """The highest mark since the entry and its first slot (r.marks: the replay's (slot, mark) decisions)."""
    marks = getattr(r, "marks", None) or []
    if not marks:
        return {"peak_mark": None, "peak_slot": None}
    slot, mark = max(marks, key=lambda x: (x[1], -x[0]))
    return {"peak_mark": int(mark), "peak_slot": int(slot)}


def _insiders(con, st, venue, clock, cache):
    """O7 insider index of the token behind a stream (pool streams need the token's curve)."""
    return _token_info(con, st, venue, clock, cache)[0]


def _token_info(con, st, venue, clock, cache):
    """(O7 insider index or None, distinct curve buyers or None) of the token behind a stream."""
    if st.mint in cache:
        return cache[st.mint]
    tok, lo = st, 0
    if venue == "pool":
        lo, hi = (st.pool_legs[0].slot, st.pool_legs[-1].slot) if st.pool_legs else (0, 0)
        tok = streams.load_streams(con, {st.mint: (lo, hi)}).get(st.mint)
    elif st.pool_legs:
        lo = st.pool_legs[0].slot
    ins = buyers = None
    if tok is not None and tok.create_slot is not None:
        ins = token_insiders(con, tok, lo, clock)  # the whole history: the same share for every caller
        if not ins.known:
            ins = None
        else:
            buyers = len({l.trader for l in tok.curve if l.is_buy})
    cache[st.mint] = (ins, buyers)
    return cache[st.mint]


def f6_flows(con, pool, sigs, hi, clock):
    """F6Exit's organic flow index from the pool's legs since min(t) - the flow window: the position's
    stream starts at t - 300, which cut the window of the first ~800 slots of every F6 position."""
    lo = min(s.t - clock.slots(F6_FLOW_MS, s.t) for s in sigs) - 1
    st = streams.load_pool_streams(con, {pool: (max(0, lo), hi)}).get(pool)
    return FlowIndex(st) if st is not None else None


def _work(args):
    task_id, out, items, sizes, delays, taus, qs, tol, cfg, clock, overlays, only, delay_ms = args
    health.wait_ok(log, f"batch {task_id}")
    io0, fb0 = io_written(), INSIDER_FALLBACKS[0]
    con = streams.open_store(STORE, threads=1, memory="600MB")
    replay.set_window_end(store_bounds(con)[1])  # no entry or exit at or after the store's end
    base = Scenario.from_cfg("base", cfg)
    rows = []
    for venue, key, lo, hi, sigs in items:
        if venue == "pool":
            st = streams.load_pool_streams(con, {key: (lo, hi)}).get(key)
        else:
            st = streams.load_streams(con, {key: (lo, hi)}).get(key)
        if st is None:
            continue
        mint = st.mint
        flows = f6_flows(con, key, sigs, hi, clock) if any(sg.family in ("F6", "N2_F6") for sg in sigs) else None
        if flows is None and any((sg.family in ("F7", "N2_F7") and isinstance(sg.params[0], str) and sg.params[0] == "flow")
                                 or (sg.family in M1_LIKE and sg.params[1] == M1_FLOW) for sg in sigs):  # flow exits
            flows = FlowIndex(st)
        ins_cache = {}
        for size_sol in sizes:
            size = int(size_sol * LAMPORTS)
            for d_set in delays:
                for q in qs:
                    for tau_mode in taus:
                        tau = replay.Tau(tau_mode)
                        busy_until = defaultdict(lambda: -1)
                        for sig in sigs:
                            # --delay-ms: the delay in whole slots of the trigger's epoch (the engine's _d_at).
                            d = delay_slots(delay_ms, sig.t, clock) if d_set is None else d_set
                            if only and not _allowed(only, sig, size=size_sol, d=d, q=q):
                                continue
                            fk = (sig.family, sig.variant)
                            if sig.t <= busy_until[fk]:
                                continue
                            if sig.family in ("F1", "N2_F1") and size > (85.005 - 1) * LAMPORTS - sig.depth:
                                continue
                            # F6 capacity: the pool's real SOL must have stayed above Q_min(S) (N2: its depth now).
                            if sig.family == "F6" and sig.params[1] < f6_min_depth(size_sol):
                                continue
                            if sig.family == "N2_F6" and sig.depth < f6_min_depth(size_sol):
                                continue
                            if sig.family in ("S1", "N2_S1") and sig.params[0] != size_sol:
                                continue  # S1 portfolios are built per size
                            if sig.family in FAMILY_SIZES and size_sol not in FAMILY_SIZES[sig.family]:
                                continue
                            sim = replay.simulate_pool_position if sig.venue == "pool" else replay.simulate_curve_position
                            ins_m1 = _insiders(con, st, sig.venue, clock, ins_cache) if sig.family in M1_LIKE else None
                            rule = exit_rule_for(sig, base, d, clock, st, flows, ins_m1)
                            r = sim(st, sig.t, size, d, q, rule, tol, tau)
                            busy_until[fk] = r.exit_slot or sig.t
                            fam_x = sig.family.removeprefix("N2_")
                            if fam_x in M1X:
                                # M1+ / M1-early: the vetoes as paired rows (a vetoed trade counts as 0, as O7).
                                ins, buyers = _token_info(con, st, sig.venue, clock, ins_cache)
                                share, why = ins.share_reason(sig.t) if ins is not None else (None, "insiders_unknown")
                                peak = getattr(getattr(rule, "base", rule), "peak", None)
                                pr = peak / r.cost - 1 if (peak and r.cost and not r.skipped) else None
                                rows.append(_row(sig, r, mint, size_sol, d, tau_mode, clock, q=q, ins_share=share,
                                                 curve_buyers=buyers, peak_ret=pr, ins_reason=why))
                                for veto in M1X[fam_x]["vetoes"]:
                                    res = (replay.Result(mint, sig.t, size=size, venue=sig.venue, skipped=True, exit_reason="veto")
                                           if m1x_vetoed(veto, share, buyers) else r)
                                    rows.append(_row(sig, res, mint, size_sol, d, tau_mode, clock, veto, q=q, ins_share=share,
                                                     curve_buyers=buyers, peak_ret=pr if res is r else None, ins_reason=why))
                                continue
                            rows.append(_row(sig, r, mint, size_sol, d, tau_mode, clock, q=q))
                            # O7 on the same trigger (paired with the base row): V1 veto, V2 exit, V3 both.
                            if overlays and sig.family in O7_FAMILIES:
                                # Fail-closed (vins-definition-2026-10-15.md): an undefined share, unknown insiders
                                # included, vetoes; the trigger stays in the pair. Without insiders there is no
                                # insider exit (V2 is the base exit); the rows' ins_reason counts both.
                                ins = _insiders(con, st, sig.venue, clock, ins_cache)
                                share, why = ins.share_reason(sig.t) if ins is not None else (None, "insiders_unknown")
                                vetoed = overlay.vetoes(share, overlay.VETO_SHARE)
                                veto = replay.Result(mint, sig.t, size=size, venue=sig.venue, skipped=True, exit_reason="o7_veto")
                                r2 = (sim(st, sig.t, size, d, q, overlay.O7Exit(exit_rule_for(sig, base, d, clock, st, flows), ins),
                                          tol, tau) if ins is not None else r)
                                for ov, res in (("O7V1", veto if vetoed else r), ("O7V2", r2), ("O7V3", veto if vetoed else r2)):
                                    rows.append(_row(sig, res, mint, size_sol, d, tau_mode, clock, ov, q=q, ins_share=share, ins_reason=why))
        del st
    for r, day in zip(rows, _days(con, [r["t"] for r in rows])):
        r["day"] = day
    con.close()
    path = _part_path(out, task_id)
    pq.write_table(pa.Table.from_pylist(rows, schema=RESULT_SCHEMA), path + ".tmp")
    os.replace(path + ".tmp", path)
    return len(rows), io_written() - io0, INSIDER_FALLBACKS[0] - fb0


def delay_slots(delay_ms, t, clock):
    """A delay in milliseconds as whole slots at slot t: the paper engine's rule (Engine._d_at, #227)."""
    return clock.slots(delay_ms, t)


def engine_commit():
    """Commit of the engine code: set by scripts/backtest-run.sh for a frozen copy, else from git."""
    if os.environ.get("LAB_ENGINE_COMMIT"):
        return os.environ["LAB_ENGINE_COMMIT"]
    here = os.path.dirname(os.path.abspath(__file__))
    git = lambda *a: subprocess.run(["git", *a], capture_output=True, text=True, cwd=here).stdout.strip()  # noqa: E731
    sha = git("rev-parse", "HEAD")
    return (sha + "-dirty") if sha and git("status", "--porcelain", "--", here) else (sha or "unknown")


def results_glob(run_dir):
    """The position results of a run: its parts (older runs also have a merged results.parquet)."""
    merged = os.path.join(run_dir, "results.parquet")
    return merged if os.path.exists(merged) else os.path.join(run_dir, "parts", "part-*.parquet")


def run(families, sizes, delays, taus, qs, tol, workers, n1_sample, max_signals, run_id, n2_k=5, batch=40,
        label="tournament_v1", segments=None, slot_range=None, only=None, decide_range=None, delay_ms=None,
        variants=None, report=True, f7_grid="v1", m1_grid="v1"):
    if delay_ms is not None:
        if delay_ms <= 0:
            raise ValueError("delay_ms must be > 0")
        delays = [None]  # one delay, in slots per trigger (delay_slots)
    cfg = load_defaults()
    out = os.path.join(OUT, run_id)
    os.makedirs(os.path.join(out, "parts"), exist_ok=True)
    t0 = time.time()
    con = streams.open_store(STORE, threads=2, memory="1GB")
    lo, hi, meta = store_bounds(con)
    clock = Clock.from_store(con)
    # --range (dry runs) and --decide (final runs, e.g. the hold-out after its warm-up) both limit the
    # decisions to [a, b); only --range marks the run as a dry run.
    signals = build_signals(con, cfg, families, sizes, n1_sample, max_signals, n2_k, clock, segments, slot_range or decide_range,
                            f7_grid=f7_grid, m1_grid=m1_grid)
    con.close()
    if variants:  # --variants: only these variants are simulated (a pre-registered configuration)
        signals = [s for s in signals if s.variant in variants]
    items = work_items(signals, clock)
    n_signals = len(signals)
    del signals
    overlays = "O7" in families
    only_rules = parse_only(only)
    tasks = [(i, out, items[j:j + batch], sizes, delays, taus, qs, tol, cfg, clock, overlays, only_rules, delay_ms)
             for i, j in enumerate(range(0, len(items), batch))]
    todo = [t for t in tasks if not os.path.exists(_part_path(out, t[0]))]
    log(f"{n_signals} signals in {len(items)} clusters, {len(tasks)} batches ({len(tasks) - len(todo)} already done) "
        f"in {time.time() - t0:.0f}s")
    del items, tasks
    positions, written, fallbacks = 0, 0, INSIDER_FALLBACKS[0]  # the main process's (signals) first
    with mp.get_context("spawn").Pool(workers, maxtasksperchild=20) as pool:
        for i, (n, w, fb) in enumerate(pool.imap_unordered(_work, todo)):
            positions, written, fallbacks = positions + n, written + w, fallbacks + fb
            if i % 50 == 0 or i == len(todo) - 1:
                log(f"batch {i + 1}/{len(todo)}: {positions} positions, {time.time() - t0:.0f}s, "
                    f"workers wrote {written / 1e9:.2f} GB")
    con = duckdb.connect()
    parts = os.listdir(os.path.join(out, "parts"))
    total = con.execute(f"SELECT count(*) FROM read_parquet('{results_glob(out)}')").fetchone()[0] if parts else 0
    con.close()
    log(f"{total} positions in {len(os.listdir(os.path.join(out, 'parts')))} parts")
    config = {"run_id": run_id, "batch": label, "engine_commit": engine_commit(), "engine_dir": os.path.dirname(os.path.abspath(__file__)),
              "families": families, "sizes_sol": sizes, "delays": delays, "delay_ms": delay_ms, "variants": variants, "f7_grid": f7_grid, "m1_grid": m1_grid, "tau": taus, "q": qs, "segments": segments, "slot_range": slot_range, "decide_range": decide_range, "only": only or None, "f6_trigger_cap": F6_STATS or None,
              "gate_sizes": list(GATE_SIZES), "slippage_tol": tol, "n1_sample": n1_sample, "n2_k": n2_k,
              "max_signals": max_signals, "store": meta,
              "signals": n_signals, "positions": total, "seconds": round(time.time() - t0),
              "insider_fallbacks": fallbacks}
    with open(os.path.join(out, "config.json"), "w") as f:
        json.dump(config, f, indent=1)
    if not report:  # --no-report: the positions only (a pre-registered test reads them itself)
        log(f"done (no report): {out}")
        return
    from .report import write_report
    write_report(out, cfg)
    if "F7" in families and (not segments or "pool_reversed" in segments):
        from .analysis import f7_reversed
        with open(f7_reversed(out, STORE)) as f:
            checks = f.read()
        with open(os.path.join(out, "report.md"), "a") as f:
            f.write("\n" + checks.replace("# F7 in", "## F7 in", 1).replace("\n## ", "\n### "))
        log("F7 reversed checks appended to the report")
    if any(f in families for f in ("M1P", "M1E")):
        from .analysis import m1x
        log(f"M1+ / M1-early evaluation: {m1x(out, STORE)}")
    log(f"done: {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--families", default="F7,F1,N1,N2")
    ap.add_argument("--sizes", default="0.5,2,5,10,25")
    ap.add_argument("--delays", default=None, help="delays in slots, comma separated (default 1,2)")
    ap.add_argument("--delay-ms", type=int, default=None, help="one delay in ms, whole slots of each trigger's epoch (instead of --delays)")
    ap.add_argument("--variants", default="", help="simulate only these signal variants, comma separated (e.g. tp4_T600)")
    ap.add_argument("--no-report", action="store_true", help="write the positions and config.json only")
    ap.add_argument("--f7-grid", default="v1",
                    help="F7 variants, comma separated: v1 = the 9 tp/T (default), exits = spec E's 18 exit rules, "
                         "long = the 6 long exits (trail20/30/50_H6, trail30_H1, flow300_H6[_sl8]); all = v1,exits")
    ap.add_argument("--m1-grid", default="v1",
                    help="M1/M1P/M1E variants: v1 = trail20/35 (default), long = trail30/50 and flow300, all = both")
    ap.add_argument("--tau", default="empirical")
    ap.add_argument("--q", default="0.5", help="intra-slot position(s) of our order, comma separated")
    ap.add_argument("--segments", default="", help="F7 only: keep these segments (curve,pool_normal,pool_reversed)")
    ap.add_argument("--tol", type=float, default=0.05)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--n2-k", type=int, default=5)
    ap.add_argument("--batch", default="tournament_v1", help="label for trials.parquet")
    ap.add_argument("--n1-sample", type=int, default=3000)
    ap.add_argument("--max-signals", type=int, default=0)
    ap.add_argument("--run-id", default=time.strftime("%Y%m%dT%H%M%S"))
    ap.add_argument("--range", default="", help="dry runs: signal slots lo:hi within the store")
    ap.add_argument("--decide", default="", help="final runs: decisions only in slots lo:hi (the rest of the store is warm-up)")
    ap.add_argument("--only", action="append", help="e.g. size=0.25:M1,N2_M1 or d=3:F7/pool_reversed (repeatable)")
    a = ap.parse_args()
    if a.delay_ms is not None and a.delays is not None:
        raise SystemExit("pass --delays or --delay-ms, not both")
    run(a.families.split(","), [float(x) for x in a.sizes.split(",")], [int(x) for x in (a.delays or "1,2").split(",")],
        a.tau.split(","), [float(x) for x in a.q.split(",")], a.tol, a.workers, a.n1_sample, a.max_signals, a.run_id, a.n2_k, label=a.batch,
        segments=[x for x in a.segments.split(",") if x] or None,
        slot_range=tuple(int(x) for x in a.range.split(":")) if a.range else None, only=a.only,
        decide_range=tuple(int(x) for x in a.decide.split(":")) if a.decide else None, delay_ms=a.delay_ms,
        variants=[x for x in a.variants.split(",") if x] or None, report=not a.no_report, f7_grid=a.f7_grid, m1_grid=a.m1_grid)


if __name__ == "__main__":
    main()
