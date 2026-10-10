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

from . import replay, streams
from .costs import LAMPORTS, Scenario, settle_sql
from .store import load_defaults
from .strategies import (
    F7_GRID,
    Clock,
    PastView,
    Signal,
    f1_candidates,
    f1_exit,
    f1_signal,
    f7_signals_sql,
    n1_candidates,
    n2_matches,
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
    ("d_ms", pa.float64()),
])


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def store_bounds(con):
    meta = json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
    return meta["slot_start"], meta["slot_end_exclusive"], meta


# Windows in milliseconds (v1 slot values at 267.3 ms), converted per epoch by the store's Clock.
MAX_HOLD_MS = {"F7": ms(2_400), "N2_F7": ms(2_400), "F1": ms(4_500), "N2_F1": ms(4_500), "N1": ms(1_500)}
WINDOW_MARGIN_MS = ms(6_000)  # d, graduation and pool open after the last possible exit decision
CLUSTER_GAP_MS = ms(20_000)  # signals further apart than this are loaded and simulated separately
MAX_WINDOW_MS = ms(50_000)  # cap on one load window (busy pools)
F1_CHUNK = 200  # tokens per stream load while screening F1 (whole curve histories)


def build_signals(con, cfg, families, sizes, n1_sample, max_signals, n2_k=5, clock=None):
    lo, hi, _ = store_bounds(con)
    clock = clock or Clock.from_store(con)
    signals = []
    creators = dict(con.execute("SELECT mint, creator FROM mints").fetchall())
    if "F7" in families:
        for venue, m, slot, depth, pool, orientation in f7_signals_sql(con, cfg, lo, hi, clock=clock):
            m, pool = sys.intern(m), (sys.intern(pool) if pool else None)
            for tp, T in F7_GRID:
                signals.append(Signal("F7", f"tp{int(tp*100)}_T{T}", m, slot, venue, int(depth), (tp, T),
                                      pool=pool, orientation=orientation))
        log(f"F7: {len(signals)} signals")
    if "F1" in families:
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
                    if v.curve and f1_signal(v, creators.get(m), min(sizes) * LAMPORTS, clock):
                        signals.append(Signal("F1", f"B{band}", sys.intern(m), t, "curve", int(v.curve[-1].post.rq), (band,)))
                        n_f1 += 1
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
            if s.family in ("F7", "F1"):
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
    for (venue, k), c in items:
        lo = c[0].t - 300
        hi = max(s.t + clock.slots(MAX_HOLD_MS.get(s.family, ms(4_500)), s.t) for s in c) + clock.slots(WINDOW_MARGIN_MS, c[-1].t)
        out.append((venue, k, lo, hi, c))
    return out


def exit_rule_for(sig, scn, d, clock=None):
    clock = clock or Clock()
    if sig.family in ("F7", "N2_F7"):
        tp, T = sig.params
        return tp_sl_time(tp, -0.08, clock.slots(ms(T), sig.t), scn)
    if sig.family in ("F1", "N2_F1"):
        return f1_exit(sig.depth, clock.slots(ms(4_500), sig.t))
    if sig.family == "N1":
        return until_slot(sig.t + clock.slots(ms(sig.params[0]), sig.t), d)
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


def _work(args):
    task_id, out, items, sizes, delays, taus, q, tol, cfg, clock = args
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
        for size_sol in sizes:
            size = int(size_sol * LAMPORTS)
            for d in delays:
                for tau_mode in taus:
                    tau = replay.Tau(tau_mode)
                    busy_until = defaultdict(lambda: -1)
                    for sig in sigs:
                        fk = (sig.family, sig.variant)
                        if sig.t <= busy_until[fk]:
                            continue
                        if sig.family in ("F1", "N2_F1") and size > (85.005 - 1) * LAMPORTS - sig.depth:
                            continue
                        rule = exit_rule_for(sig, base, d, clock)
                        if sig.venue == "pool":
                            r = replay.simulate_pool_position(st, sig.t, size, d, q, rule, tol, tau)
                        else:
                            r = replay.simulate_curve_position(st, sig.t, size, d, q, rule, tol, tau)
                        busy_until[fk] = r.exit_slot or sig.t
                        rows.append({
                            "family": sig.family, "variant": sig.variant, "mint": mint, "t": sig.t, "venue": sig.venue,
                            "depth": sig.depth, "ref_mint": sig.ref[0] if sig.ref else None,
                            "ref_t": sig.ref[1] if sig.ref else None,
                            "size_sol": size_sol, "d": d, "tau": tau_mode, "entry_failed": r.entry_failed, "skipped": r.skipped,
                            "tokens": r.tokens, "cost": r.cost, "proceeds": r.proceeds, "exit_reason": r.exit_reason,
                            "entry_slot": r.entry_slot, "exit_decision_slot": r.exit_decision_slot, "exit_slot": r.exit_slot,
                            "cf_graduation": r.cf_graduation, "seed_pool": r.seed_pool, "vault_capped": r.vault_capped,
                            "exit_attempts": r.exit_attempts,
                            "reverted_direct": r.reverted_direct, "reverted_router": r.reverted_router,
                            "sells_dropped": r.sells_dropped, "sells_scaled": r.sells_scaled,
                            "hist_ret": (r.hist_mid_exit / r.hist_mid_entry - 1) if (r.hist_mid_entry and r.hist_mid_exit) else None,
                            "pool": sig.pool, "orientation": sig.orientation,
                            "segment": "curve" if sig.venue == "curve" else f"pool_{sig.orientation}",
                            "regime": clock.regime_label(sig.t), "d_ms": round(d * clock.ms_per_slot(sig.t), 1),
                        })
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


def run(families, sizes, delays, taus, q, tol, workers, n1_sample, max_signals, run_id, n2_k=5, batch=40,
        label="tournament_v1"):
    cfg = load_defaults()
    out = os.path.join(OUT, run_id)
    os.makedirs(os.path.join(out, "parts"), exist_ok=True)
    t0 = time.time()
    con = streams.open_store(STORE, threads=2, memory="1GB")
    lo, hi, meta = store_bounds(con)
    clock = Clock.from_store(con)
    signals = build_signals(con, cfg, families, sizes, n1_sample, max_signals, n2_k, clock)
    con.close()
    items = work_items(signals, clock)
    n_signals = len(signals)
    del signals
    tasks = [(i, out, items[j:j + batch], sizes, delays, taus, q, tol, cfg, clock)
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
              "families": families, "sizes_sol": sizes, "delays": delays, "tau": taus, "q": q,
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
    ap.add_argument("--q", type=float, default=0.5)
    ap.add_argument("--tol", type=float, default=0.05)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--n2-k", type=int, default=5)
    ap.add_argument("--batch", default="tournament_v1", help="label for trials.parquet")
    ap.add_argument("--n1-sample", type=int, default=3000)
    ap.add_argument("--max-signals", type=int, default=0)
    ap.add_argument("--run-id", default=time.strftime("%Y%m%dT%H%M%S"))
    a = ap.parse_args()
    run(a.families.split(","), [float(x) for x in a.sizes.split(",")], [int(x) for x in a.delays.split(",")],
        a.tau.split(","), a.q, a.tol, a.workers, a.n1_sample, a.max_signals, a.run_id, a.n2_k, label=a.batch)


if __name__ == "__main__":
    main()
