"""The Rust live route's store snapshot (lab/live/src/snapshot.rs, M1 part 3) against LiveFeed's own load of the same
store (paper/live.py `_load_snapshot`, `_init_seed`, `_seed_pool`), through the export of paper/snapshot_export.py:

- every token, pool class and SOL-market PoolInfo, line for line, for the whole store and for a live start inside it
  (`--before`: what that start knew);
- the farmer-history window of a live start, and every seed trade in it (count and sha256, in LiveFeed's order), with
  a few pools also through `_seed_pool` itself.

Needs the built binary: LAB_LIVE_BIN=<path to lab-live> (lab/live/nas_m1a.sh builds it on the NAS); skips without it.
The store: LAB_SNAPSHOT_STORE (default fwd1053, one epoch; a seed day there is ~21 M trades).
"""

import hashlib
import os
import subprocess
from types import SimpleNamespace

import duckdb
import pytest

from paper import snapshot_export
from paper.live import LiveFeed

BIN = os.environ.get("LAB_LIVE_BIN", "")
STORE = os.environ.get("LAB_SNAPSHOT_STORE", "/home/chupa/Solana-project/data-old-faithful-one/lab/store/fwd1053.duckdb")

pytestmark = pytest.mark.skipif(not (BIN and os.access(BIN, os.X_OK)), reason="set LAB_LIVE_BIN to the built lab-live (NAS)")


def _r(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


@pytest.fixture(scope="module")
def export(tmp_path_factory):
    if not os.path.exists(STORE):
        pytest.skip(f"no store at {STORE}")
    out = str(tmp_path_factory.mktemp("snapshot"))
    return out, snapshot_export.export(STORE, out)


def _rust(snap, tmp, *args):
    """lab-live's dump; its log line (counts and peak RSS) goes to LAB_LIVE_MEASURE_LOG when set (nas_m1a.sh: job.log)."""
    out = os.path.join(tmp, "dump.txt")
    run = subprocess.run([BIN, "snapshot", "--dir", snap, *args, "--out", out], check=True, timeout=3600, capture_output=True, text=True)
    if os.environ.get("LAB_LIVE_MEASURE_LOG"):
        with open(os.environ["LAB_LIVE_MEASURE_LOG"], "a") as f:
            f.writelines(f"lab-live snapshot {' '.join(args) or '(whole store)'}: {line.split('] ', 1)[-1]}\n"
                         for line in run.stderr.splitlines() if "peak RSS" in line)
    with open(out) as f:
        return f.read().splitlines()


def _python(before):
    """LiveFeed's own load of the store (its method on a minimal object), as the dump's sorted lines."""
    feed = SimpleNamespace(tokens={}, pools={}, pool_class={}, con=duckdb.connect())
    feed.con.execute("CREATE TABLE live_mints (mint VARCHAR)")
    feed.con.execute("CREATE TABLE live_pools (pool VARCHAR, quote_class VARCHAR, orientation VARCHAR)")
    LiveFeed._load_snapshot(feed, STORE, before)
    lines = ["token\t" + "\t".join(_r(getattr(feed.tokens[m], f)) for f in
                                   ("mint", "create_slot", "creator", "mayhem", "quote_mint", "complete_slot", "pool"))
             for m in sorted(feed.tokens)]
    lines += [f"class\t{p}\t{_r(feed.pool_class[p][0])}\t{_r(feed.pool_class[p][1])}" for p in sorted(feed.pool_class)]
    lines += ["pool\t" + "\t".join(_r(getattr(feed.pools[p], f)) for f in
                                   ("pool", "mint", "orientation", "quote_class", "mayhem", "coin_creator", "create_slot"))
              for p in sorted(feed.pools)]
    feed.con.close()
    return lines


class _Farmers:
    def __init__(self):
        self.added = []

    def add(self, pool, trader, slot, token_buy, amount):
        self.added.append((pool, trader, slot, token_buy, amount))


def _python_seed(first, h24):
    """`_init_seed` at a live start, and every pool's seed trades as `_seed_pool` reads them (its query for all pools at
    once, batch by batch), as the dump's last two lines; plus `_seed_pool` itself on a few pools."""
    feed = SimpleNamespace(store=STORE, farm_seed=None, seeded=set(), stats={"seeded_pools": 0}, farmers=_Farmers())
    LiveFeed._init_seed(feed, first, h24)
    w = feed.farm_seed
    lines = [f"seed_window\t{w['lo']}\t{w['hi']}\t{w['h24']}\t{w['first_live_slot']}\t{w['complete_from']}"]
    cur = feed.seed_con.execute("""SELECT pool, trader, slot, token_buy, token_amount FROM pool WHERE kind IN ('buy', 'sell')
                                   AND slot >= ? AND slot < ? ORDER BY pool, slot, tx_index, outer_ix, inner_ix""", [w["lo"], w["hi"]])
    h, n, sample = hashlib.sha256(), 0, {}
    while batch := cur.fetchmany(200_000):
        for pool, trader, slot, tb, amount in batch:
            h.update(f"{pool}\t{_r(trader)}\t{slot}\t{_r(tb)}\t{_r(amount)}\n".encode())
            n += 1
            if pool in sample or len(sample) < 2:  # the first two pools (by name, the query's order): their trades
                sample.setdefault(pool, []).append((pool, trader, slot, tb, amount))
    lines.append(f"seed_trades\t{n}\t{h.hexdigest()}")
    for pool in sample:  # the method itself gives those pools the same trades, in the same order
        LiveFeed._seed_pool(feed, pool)
    feed.seed_con.close()
    return lines, n, [t for v in sample.values() for t in v], feed.farmers.added


def test_tokens_pools_and_classes_are_livefeeds(export, tmp_path):
    snap, meta = export
    assert _rust(snap, str(tmp_path)) == _python(None)
    lo, hi = meta["pool_slots"]
    mid = (lo + hi) // 2  # a live start inside the store: later tokens, pools and graduations are not known yet
    rust = _rust(snap, str(tmp_path), "--before", str(mid))
    assert rust == _python(mid)
    assert len([x for x in rust if x.startswith("token\t")]) < meta["counts"]["mints"]


def test_the_seed_window_and_its_trades_are_livefeeds(export, tmp_path):
    snap, meta = export
    lo, hi = meta["pool_slots"]
    h24 = meta["farm_seed"]["h24"]
    for first in sorted({hi, max(lo + 1, meta["farm_seed"]["lo"] + h24 // 2)}):  # after the store, and inside its last day
        if max(first - h24, lo) < meta["farm_seed"]["lo"]:
            continue  # not in the export's day (lab-live refuses it: its unit test)
        rust = _rust(snap, str(tmp_path), "--first-slot", str(first), "--h24", str(h24))
        want, n, sampled, added = _python_seed(first, h24)
        assert rust[-2:] == want, first
        assert n > 0 and added == sampled
        if first < hi:  # a start inside the store, with what it knew: --before = --first-slot pinned (LAB review on #368)
            both = _rust(snap, str(tmp_path), "--before", str(first), "--first-slot", str(first), "--h24", str(h24))
            assert both[-2:] == want and both[:-2] == _python(first), first
