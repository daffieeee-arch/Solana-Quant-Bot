"""Walvis stap 1b: the follower's return per leader episode, through the backtest replay (#167).

For every episode (episodes.py) of a candidate wallet we buy the same token at t0 + d with our own
size, costs and price impact (the replay re-executes every later historical trade against the
counterfactual state) and sell when the leader has sold: at the first decision slot u where the
leader's holdings of the token are <= `exit_share` x their holdings at the end of slot t0; the sale
fills at u + d. Time stop at MAX_HOLD_MS (the episode cap). No take-profit or stop-loss: the
question is only what following this wallet earns.

The leader's holdings path is read from the same historical legs the replay re-executes (curve
legs, and the migration pool's legs for a normal pool; a reversed pool's token is its quote side),
so the exit sees exactly what a live follower would see: whole slots <= u.

Variants, fixed before any outcome: W_half (exit_share 0.5), W_full (exit_share 0.1). Sizes and
delays from the command line (gate: 0.5 SOL, d = 1 and 2). Candidates: wallets with >= --min-n
episodes in the range, plus --control random other wallets with >= 2 episodes (the N2-wallet
control: following random active wallets), drawn deterministically.

The engine is `backtest` from LAB_ENGINE (a directory holding backtest/), default this repo's lab/.
Store v2 needs the store-v2 engine (the tournament run's engine/ snapshot records its commit).

  python -m wallets.follow --episodes DIR --from-slot A --to-slot B [--out DIR] [--sizes 0.5]
                           [--delays 1,2] [--min-n 5] [--control 300] [--wallets wallets.csv] [--workers N]

Output: follow-<A>-partNNNNN.parquet per batch of tokens (resumable; identical for any worker
count) and run-follow-<A>-<B>.json.
"""

import argparse
import bisect
import glob
import hashlib
import json
import os
import sys
import time

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, os.environ.get("LAB_ENGINE", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from backtest import replay, streams  # noqa: E402
from backtest.replay import Tau  # noqa: E402

from .common import OUT_ROOT, SOL, Clock, git_commit, guard  # noqa: E402
from .episodes import EPISODE_MS  # noqa: E402

MAX_HOLD_MS = EPISODE_MS
# A pool with more historical legs than this inside the replay window is a volume-farming pool:
# every position would re-execute all of them (minutes per episode). Its episodes are recorded as
# skipped ('busy_pool') and counted, not replayed.
MAX_LEGS = 150_000
WORKER_MEM = os.environ.get("LAB_WORKER_MEMORY", os.environ.get("LAB_WALLETS_MEMORY", "800MB"))  # DuckDB per worker; ~1.3 GB RSS
MAIN_MEM = os.environ.get("LAB_FOLLOW_MEMORY", os.environ.get("LAB_WALLETS_MEMORY", "1GB"))  # DuckDB in the main process
VARIANTS = {"W_half": 0.5, "W_full": 0.1}
Q_FRAC, TOL = 0.5, 0.05  # intra-slot position and own slippage tolerance (week2-defaults)
STORE = os.environ.get("LAB_STORE", "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb")


def token_delta(leg, orientation):
    """Signed token amount of a historical leg for its trader (0 for non-trade legs)."""
    if isinstance(leg, replay.CurveLeg):
        return leg.t if leg.is_buy else -leg.t
    if leg.kind not in ("buy", "sell"):
        return 0
    if orientation == "reversed":  # base is WSOL: a pool sell of WSOL buys the token (quote side)
        return leg.quote_net if leg.kind == "sell" else -leg.quote_net
    return leg.base if leg.kind == "buy" else -leg.base


class LeaderPath:
    """The leader's token holdings after each slot, from the stream's historical legs."""

    def __init__(self, stream, wallet):
        legs = [(l.slot, token_delta(l, "normal")) for l in stream.curve if l.trader == wallet]
        legs += [(l.slot, token_delta(l, stream.orientation)) for l in stream.pool_legs if l.trader == wallet]
        legs.sort(key=lambda x: x[0])
        self.slots, self.h, h = [], [], 0
        for s, d in legs:
            h += d
            if self.slots and self.slots[-1] == s:
                self.h[-1] = h
            else:
                self.slots.append(s)
                self.h.append(h)

    def holdings(self, slot):
        i = bisect.bisect_right(self.slots, slot)
        return self.h[i - 1] if i else 0


class FollowExit:
    """Sell when the leader's holdings fall to <= exit_share x their holdings at the end of slot t0."""

    def __init__(self, path, t0, exit_share, max_slots):
        self.path, self.ref, self.exit_share, self.max_slots = path, path.holdings(t0), exit_share, max_slots

    def deadline(self, entry_slot):
        return entry_slot + self.max_slots

    def __call__(self, ctx):
        if self.ref > 0 and self.path.holdings(ctx["slot"]) <= self.exit_share * self.ref:
            return "leader_sold"
        if ctx["slots_held"] >= self.max_slots:
            return "time"
        return None


def _seed(*parts):
    return int.from_bytes(hashlib.sha256("|".join(map(str, parts)).encode()).digest()[:8], "little")


def candidates(eps, min_n, n_control, lo):
    """{wallet: control?}: wallets with >= min_n episodes, plus n_control random wallets with >= 2."""
    counts = {}
    for e in eps:
        counts[e["wallet"]] = counts.get(e["wallet"], 0) + 1
    out = {w: False for w, n in counts.items() if n >= min_n}
    rest = sorted(w for w, n in counts.items() if 2 <= n < min_n)
    rest.sort(key=lambda w: _seed("control", lo, w))
    out.update({w: True for w in rest[:n_control]})
    return out


RESULT_FIELDS = ["entry_failed", "skipped", "tokens", "cost", "proceeds", "exit_reason", "entry_slot",
                 "exit_decision_slot", "exit_slot", "exit_attempts", "cf_graduation", "seed_pool", "vault_capped",
                 "hist_mid_entry", "hist_mid_exit"]


def read_wallets(path):
    """{wallet: control} from a CSV with columns wallet,control (header optional)."""
    out = {}
    with open(path) as f:
        for line in f:
            w, _, c = line.strip().partition(",")
            if w and w != "wallet":
                out[w] = c.strip().lower() in ("1", "true", "t", "yes")
    return out


def run(ep_dir, lo, hi, out_dir, sizes, delays, min_n, n_control, store=STORE, wallets_file=None, max_per_wallet=0,
        workers=1):
    guard(hi)
    con = streams.open_store(store, threads=2, memory=MAIN_MEM)
    con.execute("SET enable_progress_bar = false")
    clock = Clock.from_store(con)
    max_slots = clock.slots(MAX_HOLD_MS, lo)
    guard(hi + max_slots + 2 * max(delays) + 2)
    files = sorted(glob.glob(os.path.join(ep_dir, "episodes-*.parquet")))
    eps = [e for f in files for e in pq.read_table(f).to_pylist() if lo <= e["t0"] < hi]
    # Candidates come from a wallet list (chosen over a whole period, so a run can be split into
    # slices) or from the episodes of this range.
    cands = read_wallets(wallets_file) if wallets_file else candidates(eps, min_n, n_control, lo)
    eps = [e for e in eps if e["wallet"] in cands]
    if max_per_wallet:  # a deterministic random subset of each wallet's episodes in this range
        eps.sort(key=lambda e: _seed("cap", e["wallet"], e["key"], e["t0"]))
        seen, capped = {}, []
        for e in eps:
            if seen.get(e["wallet"], 0) < max_per_wallet:
                seen[e["wallet"]] = seen.get(e["wallet"], 0) + 1
                capped.append(e)
        eps = capped
    by_key = {}
    for e in eps:
        by_key.setdefault((e["venue"], e["pool"] if e["venue"] == "pool" else e["key"]), []).append(e)
    os.makedirs(out_dir, exist_ok=True)
    t_start = time.time()
    keys = sorted(by_key)
    tail = max_slots + 2 * max(delays) + 2
    # Streams are loaded in batches (one query per batch): curve tokens are small, pools can be big.
    # Curve legs come from the create (load_streams); pool legs only from the first trigger on,
    # because a busy pool has millions of legs before it. A batch is the unit of work for the
    # workers; every batch writes its own part, so the result does not depend on the worker count.
    # Pools are weighed (their legs in the range, one cheap count) and packed heaviest first, a heavy
    # pool alone: with fixed batches of 25 in key order the busiest pools came last and one worker
    # ran on while the others idled (NAS 2026-09-14: the last 12 % of the episodes took 63 % of the
    # time). Rows are identical for any batching: a pool's episodes always share one batch and its
    # streams are loaded from its own first trigger.
    pool_keys = [k for v, k in keys if v == "pool"]
    weights = pool_weights(con, pool_keys, lo - 1, hi + tail)
    batches = [("pool", ks, sum(weights.get(k, 0) for k in ks)) for ks in pack_pools(pool_keys, weights)]
    curve_keys = [k for v, k in keys if v == "curve"]
    batches += [("curve", curve_keys[i:i + 200], 0) for i in range(0, len(curve_keys), 200)]
    batches.sort(key=lambda b: -b[2])  # heaviest first (LPT); curve batches last
    jobs = [(i, venue, {k: by_key[(venue, k)] for k in ks}, tail, cands, sizes, delays, max_slots,
             os.path.join(out_dir, f"follow-{lo}-part{i:05d}.parquet")) for i, (venue, ks, _) in enumerate(batches)]
    con.close()
    n_done = n_busy = n_rows = 0
    if workers > 1:
        import multiprocessing as mp
        with mp.get_context("forkserver").Pool(workers, initializer=_init_worker, initargs=(store,)) as pool:
            results = pool.imap_unordered(replay_batch, jobs)
            for i, (n_ep, n_b, n_r) in enumerate(results):
                n_done, n_busy, n_rows = n_done + n_ep, n_busy + n_b, n_rows + n_r
                if i % 20 == 0:
                    print(f"{n_done}/{len(eps)} episodes, {n_rows} positions, {time.time() - t_start:.0f}s",
                          file=sys.stderr, flush=True)
    else:
        _init_worker(store)
        for i, job in enumerate(jobs):
            n_ep, n_b, n_r = replay_batch(job)
            n_done, n_busy, n_rows = n_done + n_ep, n_busy + n_b, n_rows + n_r
            if i % 20 == 0:
                print(f"{n_done}/{len(eps)} episodes, {n_rows} positions, {time.time() - t_start:.0f}s",
                      file=sys.stderr, flush=True)
    with open(os.path.join(out_dir, f"run-follow-{lo}-{hi}.json"), "w") as f:
        json.dump({"from_slot": lo, "to_slot": hi, "commit": git_commit(), "engine": os.path.dirname(replay.__file__),
                   "sizes": sizes, "delays": delays, "variants": VARIANTS, "min_n": min_n, "control": n_control,
                   "wallets_file": wallets_file, "max_per_wallet": max_per_wallet, "workers": workers, "parts": len(jobs),
                   "candidates": len(cands), "episodes": len(eps), "episodes_busy_pool": n_busy, "positions": n_rows,
                   "max_hold_ms": MAX_HOLD_MS, "max_legs": MAX_LEGS, "pool_batch_legs": POOL_BATCH_LEGS, "q": Q_FRAC, "tol": TOL,
                   "seconds": round(time.time() - t_start)}, f, indent=1)
    print(f"{lo}-{hi}: {len(cands)} wallets, {len(eps)} episodes ({n_busy} in busy pools, skipped), {n_rows} positions, "
          f"{len(jobs)} parts, {time.time() - t_start:.0f}s", file=sys.stderr)


POOL_BATCH_LEGS = MAX_LEGS  # a batch holds light pools up to this many legs together; a heavier pool runs alone
POOL_BATCH_MAX = 25


def pool_weights(con, pools, lo, hi):
    """Legs per pool in [lo, hi): an upper bound of what a batch loads (its own window is narrower)."""
    if not pools:
        return {}
    con.execute("CREATE OR REPLACE TEMP TABLE weigh_pools (pool VARCHAR)")
    con.executemany("INSERT INTO weigh_pools VALUES (?)", [(p,) for p in pools])
    return dict(con.execute("SELECT pool, count(*) FROM pool e SEMI JOIN weigh_pools USING (pool) "
                            "WHERE e.slot >= ? AND e.slot < ? AND e.kind IN ('buy', 'sell') GROUP BY pool", [lo, hi]).fetchall())


def pack_pools(pools, weights, cap=None, max_pools=None):
    """Batches of pools, heaviest first: a pool above the cap alone, lighter ones packed up to the cap or max_pools."""
    cap = POOL_BATCH_LEGS if cap is None else cap
    max_pools = POOL_BATCH_MAX if max_pools is None else max_pools
    out, cur, cur_w = [], [], 0
    for p in sorted(pools, key=lambda p: (-weights.get(p, 0), p)):
        w = weights.get(p, 0)
        if cur and (cur_w + w > cap or len(cur) >= max_pools):
            out.append(cur)
            cur, cur_w = [], 0
        cur.append(p)
        cur_w += w
    if cur:
        out.append(cur)
    return out


_W = {}


def _init_worker(store):
    """Per process: its own read-only store connection and tau table."""
    _W["con"] = streams.open_store(store, threads=1, memory=WORKER_MEM)
    _W["con"].execute("SET enable_progress_bar = false")
    _W["tau"] = Tau("empirical")


def replay_batch(job):
    """Replay one batch of tokens and write its part: (episodes, episodes in busy pools, positions)."""
    i, venue, groups, tail, cands, sizes, delays, max_slots, path = job
    if os.path.exists(path):  # resumable
        t = pq.read_metadata(path)
        return sum(len(g) for g in groups.values()), 0, t.num_rows
    con, tau = _W["con"], _W["tau"]
    win = {k: (min(e["t0"] for e in g) - 1, max(e["t0"] for e in g) + tail) for k, g in groups.items()}
    loaded = streams.load_streams(con, win) if venue == "curve" else streams.load_pool_streams(con, win)
    sim = replay.simulate_curve_position if venue == "curve" else replay.simulate_pool_position
    rows, n_busy = [], 0
    for key, group in groups.items():
        st = loaded.get(key)
        if st is None:
            continue
        before = len(rows)
        run_group(st, group, venue, sim, cands, sizes, delays, max_slots, tau, rows)
        n_busy += (len(rows) > before and rows[-1]["exit_reason"] == "busy_pool") * len(group)
    if rows:  # an empty part is not written (a parquet file without columns cannot be read back)
        tmp = path + ".tmp"
        pq.write_table(pa.Table.from_pylist(rows), tmp)
        os.replace(tmp, path)
    return sum(len(g) for g in groups.values()), n_busy, len(rows)


def run_group(st, group, venue, sim, cands, sizes, delays, max_slots, tau, rows):
    """Replay every (size, d, variant) position of the episodes of one token stream."""
    busy = len(st.pool_legs) + len(st.curve) > MAX_LEGS
    paths = {}
    for e in group:
        path = None if busy else paths.get(e["wallet"]) or paths.setdefault(e["wallet"], LeaderPath(st, e["wallet"]))
        for size in sizes:
            for d in delays:
                for variant, share in VARIANTS.items():
                    if busy:
                        r = replay.Result(e["key"], e["t0"], size=int(size * SOL), venue=venue, skipped=True,
                                          exit_reason="busy_pool")
                    else:
                        r = sim(st, e["t0"], int(size * SOL), d, Q_FRAC, FollowExit(path, e["t0"], share, max_slots),
                                TOL, tau, max_slots=max_slots)
                    rows.append({"wallet": e["wallet"], "key": e["key"], "venue": venue, "orientation": e["orientation"],
                                 "t0": e["t0"], "day": e["day"], "control": cands[e["wallet"]], "size_sol": size, "d": d,
                                 "variant": variant, "leader_ret_realized": e["leader_ret_realized"],
                                 "leader_ret_marked": e["leader_ret_marked"], "leader_hold_slots": e["hold_slots"],
                                 "leader_closed": e["closed"], "create_slot": e["create_slot"],
                                 **{f: getattr(r, f) for f in RESULT_FIELDS}})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", required=True, help="episodes.py output directory")
    ap.add_argument("--from-slot", type=int, required=True)
    ap.add_argument("--to-slot", type=int, required=True)
    ap.add_argument("--out", default=os.path.join(OUT_ROOT, "follow-v1"))
    ap.add_argument("--sizes", default="0.5")
    ap.add_argument("--delays", default="1,2")
    ap.add_argument("--min-n", type=int, default=5)
    ap.add_argument("--control", type=int, default=300)
    ap.add_argument("--store", default=STORE)
    ap.add_argument("--wallets", help="CSV wallet,control: the candidates (overrides --min-n/--control)")
    ap.add_argument("--max-per-wallet", type=int, default=0, help="replay at most this many episodes per wallet in the range (0: all)")
    ap.add_argument("--workers", type=int, default=1, help="worker processes (each ~1.3 GB RSS); parts are identical for any N")
    ap.add_argument("--variants", default=",".join(VARIANTS), help="exit variants to replay (W_half,W_full)")
    a = ap.parse_args()
    for v in list(VARIANTS):
        if v not in a.variants.split(","):
            del VARIANTS[v]
    run(a.episodes, a.from_slot, a.to_slot, a.out, [float(s) for s in a.sizes.split(",")],
        [int(d) for d in a.delays.split(",")], a.min_n, a.control, a.store, a.wallets, a.max_per_wallet, a.workers)


if __name__ == "__main__":
    main()
