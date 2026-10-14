"""LiveFeed's long-run memory (shadow-1052-a8e69ed, #165): the compact farmer history gives the same
flags as the deque version it replaces (random sequences with backfill inserts and prunes, and the
real trades of a dev-store slice), in a fraction of the memory; the backfill dedup keys behave as a
set; the feed's DuckDB keeps no file cache; curve legs of a completed mint are dropped."""

import os
import random
import tracemalloc
from collections import defaultdict, deque
from types import SimpleNamespace

import duckdb
import pytest

from backtest.store import FARMER_MIN_TRADES, FARMER_RATIO
from paper import live
from paper.feed import TokenInfo
from paper.live import FarmerFlags, LiveFeed, _Keys

STORE = "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb"


class OldFarmerFlags:
    """The deque version before #165's memory fix (paper/live.py at 02f8d47), the reference."""

    def __init__(self):
        self.hist = defaultdict(deque)

    def flag(self, pool, trader, slot, h24):
        dq = self.hist[(pool, trader)]
        while dq and dq[0][0] < slot - h24:
            dq.popleft()
        n = net = gross = 0
        for s, signed, g in dq:
            if s >= slot:
                break
            n, net, gross = n + 1, net + signed, gross + g
        return n >= FARMER_MIN_TRADES and abs(net) < FARMER_RATIO * gross

    def add(self, pool, trader, slot, token_buy, token_amount):
        dq = self.hist[(pool, trader)]
        item = (slot, token_amount if token_buy else -token_amount, token_amount)
        if not dq or dq[-1][0] <= slot:
            dq.append(item)
            return
        i = len(dq)
        while i > 0 and dq[i - 1][0] > slot:
            i -= 1
        dq.insert(i, item)

    def prune(self, slot, h24):
        for k in [k for k, dq in self.hist.items() if not dq or dq[-1][0] < slot - h24]:
            del self.hist[k]


def replay(trades, h24, prune_every=0):
    """The flags of both implementations over trades (pool, trader, slot, token_buy, amount), as
    LiveFeed uses them: flag, then add; a prune every `prune_every` trades."""
    old, new = OldFarmerFlags(), FarmerFlags()
    out_old, out_new = [], []
    for i, (pool, trader, slot, tb, amount) in enumerate(trades):
        out_old.append(old.flag(pool, trader, slot, h24))
        out_new.append(new.flag(pool, trader, slot, h24))
        old.add(pool, trader, slot, tb, amount)
        new.add(pool, trader, slot, tb, amount)
        if prune_every and i % prune_every == prune_every - 1:
            old.prune(slot, h24)
            new.prune(slot, h24)
    return out_old, out_new, old, new


def test_random_sequences_with_backfill_inserts_and_prunes_give_the_same_flags():
    rng = random.Random(7)
    for h24 in (50, 400):
        trades, slot = [], 1_000
        for _ in range(20_000):
            slot += rng.choice((0, 0, 1, 2, 5))
            s = slot - rng.randint(1, 300) if rng.random() < 0.08 else slot  # a backfill trade: older than the last
            trades.append((f"P{rng.randint(0, 6)}", f"W{rng.randint(0, 9)}", s, rng.random() < 0.5, rng.randint(1, 10**9)))
        out_old, out_new, old, new = replay(trades, h24, prune_every=997)
        assert out_new == out_old
        assert sum(out_new) > 50  # flags do happen
        assert {k: list(v) for k, v in new.hist.items()} == {k: list(v) for k, v in old.hist.items() if v}


@pytest.mark.skipif(not os.path.exists(STORE), reason="dev store not available")
def test_real_dev_store_trades_give_the_same_flags():
    """Every buy and sell of the dev store's pools over 6,000 slots, in chain order: the full day window
    (the store's h24) and a short one, so that trades expire and wallets are pruned."""
    con = duckdb.connect(STORE, read_only=True)
    con.execute("SET memory_limit = '1GB'; SET threads TO 2")
    lo = 449_300_000
    trades = con.execute("""SELECT pool, trader, slot, token_buy, token_amount FROM pool
                            WHERE slot >= ? AND slot < ? AND kind IN ('buy', 'sell')
                            ORDER BY slot, tx_index, outer_ix, inner_ix""", [lo, lo + 6_000]).fetchall()
    con.close()
    assert len(trades) > 100_000
    for h24, prune_every in ((323_000, 50_000), (1_500, 20_000)):
        out_old, out_new, *_ = replay(trades, h24, prune_every)
        assert out_new == out_old and sum(out_new) > 1_000


def test_the_compact_history_takes_a_fraction_of_the_memory():
    trades = [(f"P{i % 500}", f"W{i % 2_000}", 1_000 + i // 10, i % 2 == 0, 10**9 + i) for i in range(100_000)]

    def size(cls):
        tracemalloc.start()
        f = cls()
        for t in trades:
            f.add(*t)
        used = tracemalloc.get_traced_memory()[0]
        tracemalloc.stop()
        return used

    old, new = size(OldFarmerFlags), size(FarmerFlags)
    assert new * 3 < old, (new, old)


def test_backfill_keys_behave_as_a_set():
    k = _Keys([(500, 1, 0, 0), (100, 0, 0, 0)])
    k.add((300, 2, 1, 3))
    k.add((300, 2, 1, 3))  # again: once
    k |= {(5_000, 1, 0, 0), (200, 65_536, 0, 0), (250, 1, 0, None)}  # the last two do not pack
    assert len(k) == 6 and (300, 2, 1, 3) in k and (300, 2, 1, 4) not in k and (250, 1, 0, None) in k
    assert k == {(100, 0, 0, 0), (300, 2, 1, 3), (500, 1, 0, 0), (5_000, 1, 0, 0), (200, 65_536, 0, 0), (250, 1, 0, None)}
    assert [x for x in k][:4] == [(100, 0, 0, 0), (300, 2, 1, 3), (500, 1, 0, 0), (5_000, 1, 0, 0)]  # packed keys in order
    k.drop_before(400)
    assert k == {(500, 1, 0, 0), (5_000, 1, 0, 0)}


def test_the_feed_keeps_no_file_cache_and_drops_completed_curves(tmp_path):
    f = LiveFeed(str(tmp_path), store=None, follow=False)
    assert f.con.execute("SELECT current_setting('enable_external_file_cache')").fetchone()[0] is False
    f.tokens["C"] = TokenInfo("C", 100, "X", False, None, 4_000, "P")  # complete at 4,000
    f.tokens["D"] = TokenInfo("D", 100, "X", False, None, 4_500, "P2")  # complete at 4,500: within 1,000 slots
    for m in ("C", "D", "E"):
        f.curve_hist[m].append(SimpleNamespace(slot=4_900))
    f._prune(5_100, 100_000)
    assert set(f.curve_hist) == {"D", "E"} and live.CURVE_DONE_SLOTS == 1_000
