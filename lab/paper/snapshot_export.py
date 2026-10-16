"""The store snapshot LiveFeed loads at its start (paper/live.py `_load_snapshot`, `_init_seed`, `_seed_pool`),
exported as Parquet for the Rust live route, which reads it without DuckDB (lab/docs/rust-live-route-design-2026-10-16.md
§3; M1 part 3):

  python -m paper.snapshot_export --store <store.duckdb> --out <dir>

- mints.parquet: mint, create_slot, creator, mayhem, quote_mint, complete_slot, pool (TokenInfo; every mint);
- pools.parquet: pool, mint (the pool where the store has none), orientation, quote_class, mayhem (false where
  null), coin_creator, create_slot (PoolInfo; every pool: LiveFeed keeps the class of all of them, the PoolInfo
  of the SOL markets);
- farm_seed.parquet: the store's last day of pool buys and sells (pool, trader, slot, tx_index, outer_ix, inner_ix,
  token_buy, token_amount), by pool in LiveFeed's order. It is the farmer history a live start after the store
  seeds per pool (`_seed_pool`). The window is at least 432,000 slots, a day at 200 ms slots (Alpenglow), so a
  live day computed from faster slots is still covered;
- meta.json: the store and its meta (slot range, commit), the row counts, and the seed window [lo, hi).

A live start's `snapshot_before` is applied by the reader. This export keeps everything.
"""

import argparse
import json
import os
import time

from backtest import streams
from backtest.store import DAY_MS
from backtest.strategies import Clock

MIN_H24 = DAY_MS // 200  # a day of 200 ms slots

MINTS = "SELECT mint, create_slot, creator, mayhem, quote_mint, complete_slot, pool FROM mints ORDER BY mint"
POOLS = """SELECT pool, COALESCE(mint, pool) AS mint, orientation, quote_class, COALESCE(mayhem, false) AS mayhem, coin_creator,
                  create_slot FROM pools ORDER BY pool"""
SEED = """SELECT pool, trader, slot, tx_index, outer_ix, inner_ix, token_buy, token_amount FROM pool
          WHERE kind IN ('buy', 'sell') AND slot >= {lo} AND slot < {hi} ORDER BY pool, slot, tx_index, outer_ix, inner_ix"""


def export(store, out):
    os.makedirs(out, exist_ok=True)
    con = streams.open_store(store, threads=2, memory="1GB")
    try:
        start, end = con.execute("SELECT min(slot), max(slot) + 1 FROM pool").fetchone()
        start, end = int(start or 0), int(end or 0)
        h24 = max(Clock.from_store(con).slots(DAY_MS, max(start, end - 1)), MIN_H24)
        lo = max(start, end - h24)
        counts = {}
        for name, sql in (("mints", MINTS), ("pools", POOLS), ("farm_seed", SEED.format(lo=lo, hi=end))):
            tmp = os.path.join(out, f".{name}.parquet.tmp")
            con.execute(f"COPY ({sql}) TO '{tmp}' (FORMAT parquet, COMPRESSION zstd)")
            os.replace(tmp, os.path.join(out, f"{name}.parquet"))
            counts[name] = con.execute(f"SELECT count(*) FROM ({sql})").fetchone()[0]
        store_meta = json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
    finally:
        con.close()
    meta = {"store": os.path.abspath(store), "store_slots": [store_meta.get("slot_start"), store_meta.get("slot_end_exclusive")],
            "store_commit": store_meta.get("git_commit"), "pool_slots": [start, end], "counts": counts,
            "farm_seed": {"lo": lo, "hi": end, "h24": h24}, "exported_unix": int(time.time())}
    with open(os.path.join(out, "meta.json.tmp"), "w") as f:
        json.dump(meta, f, indent=1)
    os.replace(os.path.join(out, "meta.json.tmp"), os.path.join(out, "meta.json"))
    return meta


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--store", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    print(json.dumps(export(a.store, a.out), indent=1))


if __name__ == "__main__":
    main()
