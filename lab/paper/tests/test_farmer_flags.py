"""LiveFeed's farmer flag with a sliding window (FarmerFlags keeps each (pool, wallet) window's count and sums from
its last query) gives exactly the flags of the window summed again per query (the implementation before, kept here
as the reference): on random trades with backfills (earlier slots, inserted in order), queries at earlier slots,
amounts beyond 2^63, and prunes; and on a dev slice of the store's pool trades."""

import os
import random

import pytest

from backtest.store import FARMER_MIN_TRADES, FARMER_RATIO
from paper.live import FarmerFlags


class Reference:
    """FarmerFlags as it was (a8e69ed..3e1bd84): every flag sums the window again."""

    def __init__(self):
        self.hist = {}  # (pool, trader) -> [start, list of (slot, signed, gross)]

    def flag(self, pool, trader, slot, h24):
        t = self.hist.get((pool, trader))
        if t is None:
            return False
        a, i, lo = t[1], t[0], slot - h24
        while i < len(a) and a[i][0] < lo:
            i += 1
        t[0] = i
        n = net = gross = 0
        for s, v, g in a[i:]:
            if s >= slot:
                break
            n, net, gross = n + 1, net + v, gross + g
        return n >= FARMER_MIN_TRADES and abs(net) < FARMER_RATIO * gross

    def add(self, pool, trader, slot, token_buy, token_amount):
        t = self.hist.setdefault((pool, trader), [0, []])
        a, item = t[1], (slot, token_amount if token_buy else -token_amount, token_amount)
        i = len(a)
        while i > t[0] and a[i - 1][0] > slot:
            i -= 1
        a.insert(i, item)

    def prune(self, slot, h24):
        for k in [k for k, t in self.hist.items() if len(t[1]) == t[0] or t[1][-1][0] < slot - h24]:
            del self.hist[k]


def drive(rows, h24, backfill=0.0, prune_every=0, seed=1, silent=False):
    """Flag then add every trade (LiveFeed's order), on both; a share `backfill` of the trades comes late (a probe's
    backfill: flagged and added at its own, earlier slot; silent: added without a flag, so it lands inside the last
    query's window, which LiveFeed never does but the window's sums must survive); returns the flags compared."""
    rnd = random.Random(seed)
    new, ref, late, n = FarmerFlags(), Reference(), [], 0
    for k, (pool, trader, slot, tb, amount) in enumerate(rows):
        if backfill and rnd.random() < backfill:
            late.append((pool, trader, slot, tb, amount))
            continue
        while late and rnd.random() < 0.3:  # a backfill arrives: earlier trades, in their own order
            p, tr, s, b, am = late.pop(0)
            if not silent:
                assert new.flag(p, tr, s, h24) == ref.flag(p, tr, s, h24), ("backfill", k)
                n += 1
            new.add(p, tr, s, b, am)
            ref.add(p, tr, s, b, am)
        assert new.flag(pool, trader, slot, h24) == ref.flag(pool, trader, slot, h24), k
        new.add(pool, trader, slot, tb, amount)
        ref.add(pool, trader, slot, tb, amount)
        n += 1
        if prune_every and k % prune_every == 0:
            new.prune(slot, h24)
            ref.prune(slot, h24)
    for (pool, trader), t in new.hist.items():  # the windows' sums agree with the trades they hold
        win = list(zip(t.a[t.start:t.end:3], t.a[t.start + 1:t.end:3], t.a[t.start + 2:t.end:3]))
        assert (t.n, t.net, t.gross) == (len(win), sum(v for _, v, _ in win), sum(g for _, _, g in win))
    return n


def synthetic(n, pools=3, traders=6, seed=7, big=False):
    rnd, slot, rows = random.Random(seed), 1_000, []
    for _ in range(n):
        slot += rnd.choice((0, 0, 1, 2, 5, 40))
        amount = rnd.randint(1, 10**6) if not big or rnd.random() > 0.01 else (1 << 63) + rnd.randint(0, 10**9)
        rows.append((f"P{rnd.randrange(pools)}", f"W{rnd.randrange(traders)}", slot, rnd.random() < 0.5, amount))
    return rows


# silent: late trades added without a flag query first, so they land inside the last query's window. Only these
# cases reach FarmerFlags.add's in-window adjustment (LiveFeed always flags a trade before it adds it); a mutation
# that drops the adjustment fails them (LAB review of #340).
@pytest.mark.parametrize("backfill,prune_every,h24,silent", [(0.0, 0, 300, False), (0.2, 0, 300, False), (0.2, 500, 120, False),
                                                             (0.05, 97, 1_000, False), (0.2, 0, 300, True), (0.2, 500, 120, True)])
def test_the_sliding_window_gives_the_summed_windows_flags(backfill, prune_every, h24, silent):
    assert drive(synthetic(6_000), h24, backfill, prune_every, silent=silent) > 4_000


def test_trades_added_inside_the_last_window_keep_its_sums():
    assert drive(synthetic(6_000), 300, backfill=0.2, silent=True) > 4_000


def test_amounts_beyond_2_63_keep_the_same_flags():
    assert drive(synthetic(3_000, big=True), 200, backfill=0.1) > 2_500


def test_a_bot_with_thousands_of_trades_in_one_pool():
    # One wallet churning in one pool (the quadratic case): a farmer by the ratio rule most of the time.
    rows = [("P", "BOT", 1_000 + i // 3, i % 2 == 0, 1_000 + (i % 7)) for i in range(20_000)]
    assert drive(rows, 2_000, backfill=0.01) > 19_000


STORE = os.environ.get("LAB_STORE", "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb")


def test_the_flags_of_a_dev_slice_are_unchanged():
    """The store's pool trades of [450144000, 450164000) in chain order (the farmer window scaled to 4,000 slots so
    it slides), with 2 % of them as late backfills."""
    if not os.path.exists(STORE):
        pytest.skip("dev store not built")
    from backtest import streams

    con = streams.open_store(STORE, threads=2, memory="1GB")
    rows = con.execute("""SELECT pool, trader, slot, token_buy, token_amount FROM pool WHERE kind IN ('buy', 'sell')
                          AND slot >= 450144000 AND slot < 450164000 ORDER BY slot, tx_index, outer_ix, inner_ix""").fetchall()
    con.close()
    assert len(rows) > 50_000
    assert drive(rows, 4_000, backfill=0.02, prune_every=20_000) > 0.95 * len(rows)


def test_the_flags_of_the_whole_dev_chunk_are_unchanged():
    """The whole dev chunk [450144000, 450360000) with the real farmer window (a day of slots by the store's slot time)
    and 1 % late backfills: the reference sums its windows again per query, so this runs long. A NAS test."""
    if not os.environ.get("LAB_NAS_TESTS"):
        pytest.skip("the reference over the whole dev chunk runs long: a NAS test, set LAB_NAS_TESTS=1")
    if not os.path.exists(STORE):
        pytest.skip("dev store not built")
    from backtest import streams
    from backtest.store import DAY_MS
    from backtest.strategies import Clock

    con = streams.open_store(STORE, threads=2, memory="2GB")
    h24 = round(DAY_MS / Clock.from_store(con).ms_per_slot(450144000))
    rows = con.execute("""SELECT pool, trader, slot, token_buy, token_amount FROM pool WHERE kind IN ('buy', 'sell')
                          AND slot >= 450144000 AND slot < 450360000 ORDER BY slot, tx_index, outer_ix, inner_ix""").fetchall()
    con.close()
    n = drive(rows, h24, backfill=0.01, prune_every=200_000)
    print(f"whole dev chunk: {len(rows)} trades, h24 {h24} slots, {n} flags equal")
    assert n > 0.98 * len(rows)
