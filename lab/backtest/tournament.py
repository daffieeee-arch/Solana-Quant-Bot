"""Run the v1 tournament on the dev store and write results + a report.

  scripts/backtest-run.sh <run_id> --families F7,F1,N1,N2 --sizes 0.5,2,10 --delays 1,2

Results go to $LAB_BACKTESTS/<run_id>/: parts/ (one Parquet file per batch, written by the
workers, so memory stays flat and an interrupted run resumes where it stopped), then
results.parquet, summary.parquet, report.md, config.json and trials.parquet.
The hold-out is not reachable from here: this only opens the dev store.
"""

import argparse
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

from . import overlay, replay, rotation, streams
from .costs import LAMPORTS, Scenario, settle_sql
from .store import load_defaults
from .strategies import (
    F6_FLOW_MS,
    F6_GRID,
    F6_MAX_HOLD_MS,
    F7_GRID,
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
])


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def store_bounds(con):
    meta = json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
    return meta["slot_start"], meta["slot_end_exclusive"], meta


# Windows in milliseconds (v1 slot values at 267.3 ms), converted per epoch by the store's Clock.
MAX_HOLD_MS = {"F7": ms(2_400), "N2_F7": ms(2_400), "F1": ms(4_500), "N2_F1": ms(4_500), "N1": ms(1_500),
               "F6": F6_MAX_HOLD_MS, "N2_F6": F6_MAX_HOLD_MS, "N3": N3_HOLD_MS, "N4": ms(N4_MAX_HOLD),
               "S1": 86_400_000, "N2_S1": 86_400_000}  # S1: only when no planned exit (end of data)
S1_OPEN_HOLD = 10**9  # slots: an S1 position without a planned exit runs to the end of the data
WINDOW_MARGIN_MS = ms(6_000)  # d, graduation and pool open after the last possible exit decision
CLUSTER_GAP_MS = ms(20_000)  # signals further apart than this are loaded and simulated separately
MAX_WINDOW_MS = ms(50_000)  # cap on one load window (busy pools)
F1_CHUNK = 200  # tokens per stream load while screening F1 (whole curve histories)


def build_signals(con, cfg, families, sizes, n1_sample, max_signals, n2_k=5, clock=None, segments=None):
    lo, hi, _ = store_bounds(con)
    clock = clock or Clock.from_store(con)
    signals = []
    creators = dict(con.execute("SELECT mint, creator FROM mints").fetchall())
    if "F7" in families:
        for venue, m, slot, depth, pool, orientation, trader, tfarmer, flagged in f7_signals_sql(con, cfg, lo, hi, clock=clock):
            if segments and ("curve" if venue == "curve" else f"pool_{orientation}") not in segments:
                continue
            m, pool = sys.intern(m), (sys.intern(pool) if pool else None)
            for tp, T in F7_GRID:
                signals.append(Signal("F7", f"tp{int(tp*100)}_T{T}", m, slot, venue, int(depth), (tp, T),
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
    if "S1" in families:
        info = {p: (m, o) for p, m, o in con.execute("SELECT pool, COALESCE(mint, pool), orientation FROM pools").fetchall()}
        n_s1 = 0
        for fam, variant, size, pool, t, exit_t, flagged in rotation.s1_positions(
                con, lo, hi, n2_k=rotation.N2_K if "N2" in families else 0):
            m, o = info[pool]
            signals.append(Signal(fam, variant, sys.intern(m), t, "pool", 0, (size, exit_t), pool=sys.intern(pool),
                                  orientation=o, flagged=bool(flagged)))
            n_s1 += 1
        log(f"S1: {n_s1} positions (S1 and N2_S1)")
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
            if s.family in ("F7", "F1", "F6"):  # S1 draws its own N2 controls (rotation.py)
                by_trigger[(s.family, s.pool or s.mint, s.t, s.venue, s.depth, s.orientation)].append(s)
        t0 = time.time()
        matches = n2_matches(con, list(by_trigger), k=n2_k, clock=clock)
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


def work_items(signals, clock=None):
    """Group signals per token (curve) or pool into clusters; each cluster has its own load
    window, capped in length so a busy pool never loads more than MAX_WINDOW_MS at once."""
    clock = clock or Clock()
    by_key = defaultdict(list)
    for s in signals:
        by_key[(s.venue, s.pool if s.venue == "pool" else s.mint)].append(s)
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
        return s.t + clock.slots(MAX_HOLD_MS.get(s.family, ms(4_500)), s.t)

    for (venue, k), c in items:
        lo = c[0].t - 300
        hi = max(end(s) for s in c) + clock.slots(WINDOW_MARGIN_MS, c[-1].t)
        out.append((venue, k, lo, hi, c))
    return out


def exit_rule_for(sig, scn, d, clock=None, stream=None, flows=None):
    clock = clock or Clock()
    if sig.family in ("S1", "N2_S1"):
        size, exit_t = sig.params
        return rotation.S1Exit(exit_t, f6_min_depth(size), stream.orientation, S1_OPEN_HOLD)
    if sig.family in ("F6", "N2_F6"):
        return F6Exit(sig.params[0], flows if flows is not None else FlowIndex(stream), clock.slots(F6_FLOW_MS, sig.t),
                      clock.slots(F6_MAX_HOLD_MS, sig.t), stream.orientation)
    if sig.family in ("F7", "N2_F7"):
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


def _part_path(out, task_id):
    return os.path.join(out, "parts", f"part-{task_id:06d}.parquet")


O7_FAMILIES = ("F7", "N2_F7", "F1", "N2_F1")


def _row(sig, r, mint, size_sol, d, tau_mode, clock, ov="none", q=0.5):
    return {
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
        "segment": "curve" if sig.venue == "curve" else f"pool_{sig.orientation}",
        "farm": "n/a" if sig.flagged is None else ("flagged" if sig.flagged else "organic"),
        "regime": clock.regime_label(sig.t), "d_ms": round(d * clock.ms_per_slot(sig.t), 1), "overlay": ov,
        "q": q, "trigger_trader": sig.trigger, "trigger_farmer": sig.trigger_farmer,
    }


def _insiders(con, st, venue, clock, cache):
    """O7 insider index of the token behind a stream (pool streams need the token's curve)."""
    if st.mint in cache:
        return cache[st.mint]
    tok = st
    if venue == "pool":
        lo, hi = (st.pool_legs[0].slot, st.pool_legs[-1].slot) if st.pool_legs else (0, 0)
        tok = streams.load_streams(con, {st.mint: (lo, hi)}).get(st.mint)
    ins = None
    if tok is not None and tok.create_slot is not None:
        ins = overlay.Insiders(tok, clock.slots(overlay.INSIDER_WINDOW_MS, tok.create_slot))
        if not ins.known:
            ins = None
    cache[st.mint] = ins
    return ins


def _work(args):
    task_id, out, items, sizes, delays, taus, qs, tol, cfg, clock, overlays = args
    con = streams.open_store(STORE, threads=1, memory="600MB")
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
        flows = FlowIndex(st) if any(sg.family in ("F6", "N2_F6") for sg in sigs) else None
        ins_cache = {}
        for size_sol in sizes:
            size = int(size_sol * LAMPORTS)
            for d in delays:
                for q in qs:
                    for tau_mode in taus:
                        tau = replay.Tau(tau_mode)
                        busy_until = defaultdict(lambda: -1)
                        for sig in sigs:
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
                            sim = replay.simulate_pool_position if sig.venue == "pool" else replay.simulate_curve_position
                            r = sim(st, sig.t, size, d, q, exit_rule_for(sig, base, d, clock, st, flows), tol, tau)
                            busy_until[fk] = r.exit_slot or sig.t
                            rows.append(_row(sig, r, mint, size_sol, d, tau_mode, clock, q=q))
                            # O7 on the same trigger (paired with the base row): V1 veto, V2 exit, V3 both.
                            if overlays and sig.family in O7_FAMILIES:
                                ins = _insiders(con, st, sig.venue, clock, ins_cache)
                                if ins is None:
                                    continue
                                vetoed = ins.share(sig.t) > overlay.VETO_SHARE
                                veto = replay.Result(mint, sig.t, size=size, venue=sig.venue, skipped=True, exit_reason="o7_veto")
                                r2 = sim(st, sig.t, size, d, q, overlay.O7Exit(exit_rule_for(sig, base, d, clock, st, flows), ins),
                                         tol, tau)
                                for ov, res in (("O7V1", veto if vetoed else r), ("O7V2", r2), ("O7V3", veto if vetoed else r2)):
                                    rows.append(_row(sig, res, mint, size_sol, d, tau_mode, clock, ov, q=q))
        del st
    for r, day in zip(rows, _days(con, [r["t"] for r in rows])):
        r["day"] = day
    con.close()
    path = _part_path(out, task_id)
    pq.write_table(pa.Table.from_pylist(rows, schema=RESULT_SCHEMA), path + ".tmp")
    os.replace(path + ".tmp", path)
    return len(rows)


def engine_commit():
    """Commit of the engine code: set by scripts/backtest-run.sh for a frozen copy, else from git."""
    if os.environ.get("LAB_ENGINE_COMMIT"):
        return os.environ["LAB_ENGINE_COMMIT"]
    here = os.path.dirname(os.path.abspath(__file__))
    git = lambda *a: subprocess.run(["git", *a], capture_output=True, text=True, cwd=here).stdout.strip()  # noqa: E731
    sha = git("rev-parse", "HEAD")
    return (sha + "-dirty") if sha and git("status", "--porcelain", "--", here) else (sha or "unknown")


def merge_results(out, cfg):
    """parts/*.parquet -> results.parquet (sorted), with base-scenario P&L columns for dashboards."""
    pnl, ret = settle_sql(Scenario.from_cfg("base", cfg))
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '1GB'; SET threads TO 2; SET temp_directory = '{streams.SPILL_DIR}'")
    con.execute(f"""COPY (SELECT *, {pnl} AS pnl_base_lamports, {ret} AS ret_base
                          FROM read_parquet('{out}/parts/part-*.parquet')
                          ORDER BY family, variant, size_sol, d, tau, t, mint)
                    TO '{out}/results.parquet.tmp' (FORMAT parquet, COMPRESSION zstd)""")
    n = con.execute(f"SELECT count(*) FROM read_parquet('{out}/results.parquet.tmp')").fetchone()[0]
    con.close()
    os.replace(f"{out}/results.parquet.tmp", f"{out}/results.parquet")
    return n


def run(families, sizes, delays, taus, qs, tol, workers, n1_sample, max_signals, run_id, n2_k=5, batch=40,
        label="tournament_v1", segments=None):
    cfg = load_defaults()
    out = os.path.join(OUT, run_id)
    os.makedirs(os.path.join(out, "parts"), exist_ok=True)
    t0 = time.time()
    con = streams.open_store(STORE, threads=2, memory="1GB")
    lo, hi, meta = store_bounds(con)
    clock = Clock.from_store(con)
    signals = build_signals(con, cfg, families, sizes, n1_sample, max_signals, n2_k, clock, segments)
    con.close()
    items = work_items(signals, clock)
    n_signals = len(signals)
    del signals
    overlays = "O7" in families
    tasks = [(i, out, items[j:j + batch], sizes, delays, taus, qs, tol, cfg, clock, overlays)
             for i, j in enumerate(range(0, len(items), batch))]
    todo = [t for t in tasks if not os.path.exists(_part_path(out, t[0]))]
    log(f"{n_signals} signals in {len(items)} clusters, {len(tasks)} batches ({len(tasks) - len(todo)} already done) "
        f"in {time.time() - t0:.0f}s")
    del items, tasks
    positions = 0
    with mp.get_context("spawn").Pool(workers, maxtasksperchild=20) as pool:
        for i, n in enumerate(pool.imap_unordered(_work, todo)):
            positions += n
            if i % 50 == 0 or i == len(todo) - 1:
                log(f"batch {i + 1}/{len(todo)}: {positions} positions, {time.time() - t0:.0f}s")
    total = merge_results(out, cfg)
    log(f"results.parquet: {total} positions")
    config = {"run_id": run_id, "batch": label, "engine_commit": engine_commit(), "engine_dir": os.path.dirname(os.path.abspath(__file__)),
              "families": families, "sizes_sol": sizes, "delays": delays, "tau": taus, "q": qs, "segments": segments,
              "slippage_tol": tol, "n1_sample": n1_sample, "n2_k": n2_k, "max_signals": max_signals, "store": meta,
              "signals": n_signals, "positions": total, "seconds": round(time.time() - t0)}
    with open(os.path.join(out, "config.json"), "w") as f:
        json.dump(config, f, indent=1)
    from .report import write_report
    write_report(out, cfg)
    log(f"done: {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--families", default="F7,F1,N1,N2")
    ap.add_argument("--sizes", default="0.5,2,5,10,25")
    ap.add_argument("--delays", default="1,2")
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
    a = ap.parse_args()
    run(a.families.split(","), [float(x) for x in a.sizes.split(",")], [int(x) for x in a.delays.split(",")],
        a.tau.split(","), [float(x) for x in a.q.split(",")], a.tol, a.workers, a.n1_sample, a.max_signals, a.run_id, a.n2_k, label=a.batch,
        segments=[x for x in a.segments.split(",") if x] or None)


if __name__ == "__main__":
    main()
