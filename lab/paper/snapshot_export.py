"""The store snapshot LiveFeed loads at its start (paper/live.py `_load_snapshot`, `_init_seed`, `_seed_pool`),
exported as Parquet for the Rust live route, which reads it without DuckDB (lab/docs/rust-live-route-design-2026-10-16.md
§3; M1 part 3):

  python -m paper.snapshot_export --store <store.duckdb> --out <dir> [--history-from SLOT --history-to SLOT] [--no-seed]

- mints.parquet: mint, create_slot, creator, mayhem, quote_mint, complete_slot, pool (TokenInfo; every mint);
- pools.parquet: pool, mint (the pool where the store has none), orientation, quote_class, mayhem (false where
  null), coin_creator, create_slot (PoolInfo; every pool: LiveFeed keeps the class of all of them, the PoolInfo
  of the SOL markets);
- farm_seed.parquet: the store's last day of pool buys and sells (pool, trader, slot, tx_index, outer_ix, inner_ix,
  token_buy, token_amount), by pool in LiveFeed's order. It is the farmer history a live start after the store
  seeds per pool (`_seed_pool`). The window is at least 432,000 slots, a day at 200 ms slots (Alpenglow), so a
  live day computed from faster slots is still covered;
- farm_hist.parquet (with --history-from/--history-to: the replay of [from, to) by `lab-live legs`, M1 C1a-2): the
  store's pool buys and sells over [H0, from), the farmer history the store's flags of [from, to) were computed with
  (store.py:500-519; research/m1-c1a2-spec-2026-10-19.md). H0 = max(store start, min over the store's chunks C that
  overlap [from, to) of (max(from, C.lo) - h24(C))), with h24 per chunk from its own blocks (store.py:486-488), never
  the Clock's day. Same columns as farm_seed, in chain order, and only the (pool, trader) keys that trade in
  [from, to): no other key's history is in a window of an emitted trade. So the file is tied to [from, to): the reader
  requires its `hi` to be the replay's from, and the replay's to at most its `to`. --no-seed leaves farm_seed out (a
  replay never reads it);
- meta.json: the store and its meta (slot range, commit), the row counts, the seed window [lo, hi), and since
  snapshot_version 2 the store's meta_bounds (feed.py:100), ms_per_slot (the Clock), chunk_list (slots and the
  extractor's sha256 per chunk) and farmer rule, each chunk's h24 (`chunk_h24`: [lo, hi, h24]) and the farm_hist window.

A live start's `snapshot_before` is applied by the reader. This export keeps everything. A history range touching the
hold-out or the clean period is refused.
"""

import argparse
import json
import os
import time

from backtest import streams
from backtest.store import DAY_MS
from backtest.strategies import Clock

MIN_H24 = DAY_MS // 200  # a day of 200 ms slots
SNAPSHOT_VERSION = 2
HOLDOUT = (452304000, 454464000)  # epochs 1047-1051, never read
CLEAN = (455760000, 459648000)  # epochs 1055-1063, locked

MINTS = "SELECT mint, create_slot, creator, mayhem, quote_mint, complete_slot, pool FROM mints ORDER BY mint"
POOLS = """SELECT pool, COALESCE(mint, pool) AS mint, orientation, quote_class, COALESCE(mayhem, false) AS mayhem, coin_creator,
                  create_slot FROM pools ORDER BY pool"""
SEED = """SELECT pool, trader, slot, tx_index, outer_ix, inner_ix, token_buy, token_amount FROM pool
          WHERE kind IN ('buy', 'sell') AND slot >= {lo} AND slot < {hi} ORDER BY pool, slot, tx_index, outer_ix, inner_ix"""
# The farmer history of a replay of [a, b), in chain order: only the (pool, trader) keys that buy or sell in [a, b) (all
# NULL traders are one key, as in the store's window), since no other key's history is in a window of an emitted trade.
HIST = """SELECT h.pool, h.trader, h.slot, h.tx_index, h.outer_ix, h.inner_ix, h.token_buy, h.token_amount FROM pool h
          SEMI JOIN (SELECT DISTINCT pool, trader FROM pool WHERE kind IN ('buy', 'sell') AND slot >= {a} AND slot < {b}) k
            ON k.pool = h.pool AND k.trader IS NOT DISTINCT FROM h.trader
          WHERE h.kind IN ('buy', 'sell') AND h.slot >= {lo} AND h.slot < {a}
          ORDER BY h.slot, h.tx_index, h.outer_ix, h.inner_ix"""


def chunk_h24(con, lo, hi):
    """A chunk's day in slots for the farmer window, as the store computed it (store.py:486-488) on its blocks."""
    ms = con.execute(f"""SELECT (max(block_time) - min(block_time)) * 1000.0 / greatest(max(slot) - min(slot), 1)
                         FROM blocks WHERE slot >= {int(lo)} AND slot < {int(hi)}""").fetchone()[0] or 267.3
    return round(DAY_MS / ms)


def history_lo(start, chunks, a, b):
    """H0 of a replay of [a, b): the earliest slot a farmer window of its trades reaches, [s - h24(C), s - 1] for a trade
    at s >= a in chunk C, and nothing before the store's start. `chunks`: [lo, hi, h24] of the store's chunks."""
    over = [(lo, h) for lo, hi, h in chunks if lo < b and hi > a]
    if not over:
        raise ValueError(f"[{a}, {b}) overlaps no chunk of the store")
    return max(start, min(max(a, lo) - h for lo, h in over))


def _overlaps(lo, hi, period):
    return lo < period[1] and hi > period[0]


def _copy(con, out, name, sql):
    tmp = os.path.join(out, f".{name}.parquet.tmp")
    n = con.execute(f"COPY ({sql}) TO '{tmp}' (FORMAT parquet, COMPRESSION zstd)").fetchone()[0]  # the rows written
    os.replace(tmp, os.path.join(out, f"{name}.parquet"))
    return int(n)


def export(store, out, history=None, seed=True):
    """The snapshot of `store` into `out`; history=(from, to) adds farm_hist.parquet for a replay of [from, to);
    seed=False leaves farm_seed out."""
    os.makedirs(out, exist_ok=True)
    for name in ("farm_seed", "farm_hist"):  # never a file of an earlier export that meta.json does not describe
        if os.path.exists(os.path.join(out, f"{name}.parquet")):
            os.remove(os.path.join(out, f"{name}.parquet"))
    con = streams.open_store(store, threads=2, memory="1GB")
    try:
        start, end = con.execute("SELECT min(slot), max(slot) + 1 FROM pool").fetchone()
        start, end = int(start or 0), int(end or 0)
        h24 = max(Clock.from_store(con).slots(DAY_MS, max(start, end - 1)), MIN_H24)
        lo = max(start, end - h24)
        store_meta = json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
        s0, s1 = store_meta["slot_start"], store_meta["slot_end_exclusive"]
        chunks = [[int(c["slots"][0]), int(c["slots"][1]), chunk_h24(con, *c["slots"])] for c in store_meta["chunk_list"]]
        hist = None
        if history is not None:
            a, b = (int(x) for x in history)
            if not s0 <= a < b <= s1:
                raise ValueError(f"history range [{a}, {b}) is not inside the store period [{s0}, {s1})")
            h0 = history_lo(s0, chunks, a, b)
            for name, period in (("hold-out", HOLDOUT), ("clean period", CLEAN)):
                if _overlaps(h0, b, period):
                    raise ValueError(f"history range [{h0}, {b}) touches the {name} [{period[0]}, {period[1]})")
            hist = {"lo": h0, "hi": a, "to": b}
        counts = {}
        for name, sql in (("mints", MINTS), ("pools", POOLS)):
            counts[name] = _copy(con, out, name, sql)
        if seed:
            counts["farm_seed"] = _copy(con, out, "farm_seed", SEED.format(lo=lo, hi=end))
        if hist is not None:
            counts["farm_hist"] = hist["rows"] = _copy(con, out, "farm_hist", HIST.format(lo=hist["lo"], a=hist["hi"], b=hist["to"]))
        meta_bounds = [int(x) for x in con.execute("SELECT min(slot), max(slot) + 1 FROM blocks").fetchone()]
    finally:
        con.close()
    meta = {"store": os.path.abspath(store), "store_slots": [s0, s1], "store_commit": store_meta.get("git_commit"),
            "pool_slots": [start, end], "counts": counts, "snapshot_version": SNAPSHOT_VERSION, "meta_bounds": meta_bounds,
            "ms_per_slot": store_meta["ms_per_slot"], "farmer": store_meta["farmer"], "chunk_list": store_meta["chunk_list"],
            "chunk_h24": chunks, "exported_unix": int(time.time())}
    if seed:
        meta["farm_seed"] = {"lo": lo, "hi": end, "h24": h24}
    if hist is not None:
        meta["farm_hist"] = hist
    with open(os.path.join(out, "meta.json.tmp"), "w") as f:
        json.dump(meta, f, indent=1)
    os.replace(os.path.join(out, "meta.json.tmp"), os.path.join(out, "meta.json"))
    return meta


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--store", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--history-from", type=int)
    ap.add_argument("--history-to", type=int)
    ap.add_argument("--no-seed", action="store_true")
    a = ap.parse_args(argv)
    if (a.history_from is None) != (a.history_to is None):
        ap.error("give --history-from and --history-to together")
    history = None if a.history_from is None else (a.history_from, a.history_to)
    meta = export(a.store, a.out, history, seed=not a.no_seed)
    print(json.dumps({k: v for k, v in meta.items() if k not in ("chunk_list", "chunk_h24")}, indent=1))


if __name__ == "__main__":
    main()
