"""Run the v1 tournament on the dev store and write results + a report.

  python -m backtest.tournament --families F7,F1,N1 --sizes 0.5,2,10 --delays 1,2

Results go to $LAB_DATA_ROOT/backtests/<run_id>/ (results.parquet, report.md, config.json).
The hold-out is not reachable from here: this only opens the dev store.
"""

import argparse
import json
import multiprocessing as mp
import os
import random
import time
from collections import defaultdict

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from . import replay, streams
from .costs import LAMPORTS, Scenario, settle
from .store import load_defaults
from .strategies import (
    F7_GRID,
    PastView,
    Signal,
    f1_candidates,
    f1_exit,
    f1_signal,
    f7_signals_sql,
    n1_candidates,
    n2_matches,
    tp_sl_time,
    until_slot,
)

STORE = os.environ.get("LAB_STORE", "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev.duckdb")
OUT = os.environ.get("LAB_BACKTESTS", "/home/chupa/Solana-project/data-old-faithful-one/lab/backtests")


def store_bounds(con):
    meta = json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
    return meta["slot_start"], meta["slot_end_exclusive"], meta


MAX_HOLD = {"F7": 2_400, "N2_F7": 2_400, "F1": 4_500, "N2_F1": 4_500, "N1": 1_500}
WINDOW_MARGIN = 6_000  # d, graduation and pool open after the last possible exit decision
CLUSTER_GAP = 20_000  # signals further apart than this are loaded and simulated separately


def build_signals(con, cfg, families, sizes, n1_sample, max_signals):
    lo, hi, _ = store_bounds(con)
    signals = []
    creators = dict(con.execute("SELECT mint, creator FROM mints").fetchall())
    if "F7" in families:
        for venue, m, slot, depth in f7_signals_sql(con, cfg, lo, hi):
            for tp, T in F7_GRID:
                signals.append(Signal("F7", f"tp{int(tp*100)}_T{T}", m, slot, venue, int(depth), (tp, T)))
    if "F1" in families:
        for band in (40, 55, 70):
            cands = f1_candidates(con, cfg, lo, hi, band)
            for i in range(0, len(cands), 2000):
                part = cands[i:i + 2000]
                ss = streams.load_streams(con, {m: (0, t) for m, t in part})
                for m, t in part:
                    v = PastView(ss[m], t)
                    if v.curve and f1_signal(v, creators.get(m), min(sizes) * LAMPORTS):
                        signals.append(Signal("F1", f"B{band}", m, t, "curve", int(v.curve[-1].post.rq), (band,)))
    if "N1" in families:
        cands = n1_candidates(con, cfg, lo, hi)
        random.Random(1).shuffle(cands)
        for m, cslot in sorted(cands[:n1_sample], key=lambda c: c[1]):
            for H in (150, 1_500):
                signals.append(Signal("N1", f"H{H}", m, cslot, "curve", 0, (H,)))
    # Deterministic order (SQL results come back in no fixed order), then the optional cap.
    signals.sort(key=lambda s: (s.t, s.mint, s.family, s.variant))
    if max_signals:
        signals = signals[:max_signals]
    if "N2" in families:
        base = [s for s in signals if s.family in ("F7", "F1")]
        by_trigger = defaultdict(list)
        for s in base:
            by_trigger[(s.family, s.mint, s.t)].append(s)
        for key, group in by_trigger.items():
            for m, slot in n2_matches(con, group[0]):
                for s2 in group:
                    signals.append(Signal("N2_" + s2.family, s2.variant, m, slot, s2.venue, s2.depth, s2.params))
    return signals


def work_items(signals):
    """Group signals per token into clusters; each cluster has its own load window."""
    by_mint = defaultdict(list)
    for s in signals:
        by_mint[s.mint].append(s)
    items = []
    for m, sigs in by_mint.items():
        sigs.sort(key=lambda s: s.t)
        cluster = [sigs[0]]
        for s in sigs[1:]:
            if s.t - cluster[-1].t > CLUSTER_GAP:
                items.append(cluster)
                cluster = []
            cluster.append(s)
        items.append(cluster)
    out = []
    for c in items:
        lo = c[0].t - 300
        hi = max(s.t + MAX_HOLD.get(s.family, 4_500) for s in c) + WINDOW_MARGIN
        out.append((c[0].mint, lo, hi, c))
    return out


def exit_rule_for(sig, scn, d):
    if sig.family in ("F7", "N2_F7"):
        tp, T = sig.params
        return tp_sl_time(tp, -0.08, T, scn)
    if sig.family in ("F1", "N2_F1"):
        return f1_exit(sig.depth, 4_500)
    if sig.family == "N1":
        return until_slot(sig.t + sig.params[0], d)
    raise ValueError(sig.family)


def _work(args):
    items, sizes, delays, taus, q, tol, cfg = args
    con = streams.open_store(STORE, threads=1, memory="1GB")
    base = Scenario.from_cfg("base", cfg)
    rows = []
    for mint, lo, hi, sigs in items:
        st = streams.load_streams(con, {mint: (lo, hi)}).get(mint)
        if st is None:
            continue
        for size_sol in sizes:
            size = int(size_sol * LAMPORTS)
            for d in delays:
                for tau_mode in taus:
                    tau = replay.Tau(tau_mode)
                    busy_until = defaultdict(lambda: -1)
                    for sig in sigs:
                        key = (sig.family, sig.variant)
                        if sig.t <= busy_until[key]:
                            continue
                        if sig.family in ("F1", "N2_F1") and size > (85.005 - 1) * LAMPORTS - sig.depth:
                            continue
                        rule = exit_rule_for(sig, base, d)
                        if sig.venue == "pool":
                            r = replay.simulate_pool_position(st, sig.t, size, d, q, rule, tol, tau)
                        else:
                            r = replay.simulate_curve_position(st, sig.t, size, d, q, rule, tol, tau)
                        busy_until[key] = r.exit_slot or sig.t
                        rows.append({
                            "family": sig.family, "variant": sig.variant, "mint": mint, "t": sig.t, "venue": sig.venue,
                            "size_sol": size_sol, "d": d, "tau": tau_mode, "entry_failed": r.entry_failed, "skipped": r.skipped,
                            "tokens": r.tokens, "cost": r.cost, "proceeds": r.proceeds, "exit_reason": r.exit_reason,
                            "entry_slot": r.entry_slot, "exit_decision_slot": r.exit_decision_slot, "exit_slot": r.exit_slot,
                            "cf_graduation": r.cf_graduation, "seed_pool": r.seed_pool, "exit_attempts": r.exit_attempts,
                            "reverted_direct": r.reverted_direct, "reverted_router": r.reverted_router,
                            "sells_dropped": r.sells_dropped, "sells_scaled": r.sells_scaled,
                            "hist_ret": (r.hist_mid_exit / r.hist_mid_entry - 1) if (r.hist_mid_entry and r.hist_mid_exit) else None,
                        })
        del st
    con.close()
    return rows


def run(families, sizes, delays, taus, q, tol, workers, n1_sample, max_signals, run_id):
    cfg = load_defaults()
    con = streams.open_store(STORE)
    lo, hi, meta = store_bounds(con)
    t0 = time.time()
    signals = build_signals(con, cfg, families, sizes, n1_sample, max_signals)
    items = work_items(signals)
    con.close()
    print(f"{len(signals)} signals in {len(items)} clusters in {time.time()-t0:.0f}s", flush=True)
    batches = [items[i:i + 40] for i in range(0, len(items), 40)]
    tasks = [(b, sizes, delays, taus, q, tol, cfg) for b in batches]
    rows = []
    with mp.get_context("spawn").Pool(workers, maxtasksperchild=20) as pool:
        for i, part in enumerate(pool.imap_unordered(_work, tasks)):
            rows.extend(part)
            if i % 20 == 0:
                print(f"batch {i+1}/{len(tasks)}: {len(rows)} positions, {time.time()-t0:.0f}s", flush=True)
    con = streams.open_store(STORE)
    blocks = dict(con.execute("SELECT slot, block_time FROM blocks").fetchall())
    for r in rows:
        bt = blocks.get(r["t"]) or blocks.get(r["entry_slot"])
        r["day"] = time.strftime("%Y-%m-%d", time.gmtime(bt)) if bt else None
    out = os.path.join(OUT, run_id)
    os.makedirs(out, exist_ok=True)
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, os.path.join(out, "results.parquet"))
    config = {"run_id": run_id, "families": families, "sizes_sol": sizes, "delays": delays, "tau": taus, "q": q,
              "slippage_tol": tol, "n1_sample": n1_sample, "store": meta, "signals": len(signals),
              "positions": len(rows), "seconds": round(time.time() - t0)}
    with open(os.path.join(out, "config.json"), "w") as f:
        json.dump(config, f, indent=1)
    from .report import write_report
    write_report(out, cfg)
    print(f"done: {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--families", default="F7,F1,N1,N2")
    ap.add_argument("--sizes", default="0.5,2,5,10,25")
    ap.add_argument("--delays", default="1,2")
    ap.add_argument("--tau", default="empirical")
    ap.add_argument("--q", type=float, default=0.5)
    ap.add_argument("--tol", type=float, default=0.05)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--n1-sample", type=int, default=3000)
    ap.add_argument("--max-signals", type=int, default=0)
    ap.add_argument("--run-id", default=time.strftime("%Y%m%dT%H%M%S"))
    a = ap.parse_args()
    run(a.families.split(","), [float(x) for x in a.sizes.split(",")], [int(x) for x in a.delays.split(",")],
        a.tau.split(","), a.q, a.tol, a.workers, a.n1_sample, a.max_signals, a.run_id)


if __name__ == "__main__":
    main()
