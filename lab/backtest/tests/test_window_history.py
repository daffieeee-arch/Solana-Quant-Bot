"""Signal and exit inputs that must not depend on where a work item's stream window starts
(ultra review of #190, items 13 and 14):
- O7 / M1 insiders: the share over a pool window plus SQL offsets for the pool's earlier trades equals
  the share over the token's whole history (tournament.token_insiders);
- F6Exit's organic outflow window: its flow index is loaded from min(t) - the flow window, not from the
  position stream's t - 300 (tournament.f6_flows), and a cut window changes the exit."""

from types import SimpleNamespace as NS

import duckdb

from backtest import overlay, tournament
from backtest.strategies import F6_FLOW_MS, Clock, F6Exit, FlowIndex

CLOCK = Clock()


def curve_leg(slot, trader, t, buy):
    return NS(slot=slot, trader=trader, t=t, is_buy=buy)


def pool_leg(slot, trader, base, kind, quote=0, farmer=False):
    return NS(slot=slot, trader=trader, base=base, kind=kind, quote_net=quote, farmer=farmer, pre=NS(e=0, virt=0, b=0))


def token(pool_legs):
    curve = [curve_leg(100, "CREATOR", 1_000, True), curve_leg(101, "EARLY", 4_000, True),   # insiders (<= c + 10 s)
             curve_leg(5_000, "LATE", 5_000, True)]
    return NS(mint="M", create_slot=100, creator="CREATOR", curve=curve, pool_legs=pool_legs, orientation="normal", pool="P")


# Pool trades after migration: the early insider sells most before the window, the creator sells once.
POOL = [pool_leg(20_000, "EARLY", 3_000, "sell"), pool_leg(20_500, "CREATOR", 200, "sell"), pool_leg(21_000, "OTHER", 2_000, "buy"),
        pool_leg(30_000, "LATE", 1_000, "sell"), pool_leg(30_500, "EARLY", 500, "sell")]
LO = 25_000  # the work item's window starts here


def test_insider_share_with_offsets_equals_the_whole_history():
    w = CLOCK.slots(overlay.INSIDER_WINDOW_MS, 100)
    full = overlay.Insiders(token(POOL), w)
    window = [l for l in POOL if l.slot >= LO]
    cut = overlay.Insiders(token(window), w)
    before = [l for l in POOL if l.slot < LO]
    d = [l.base if l.kind == "buy" else -l.base for l in before]
    offsets = (sum(x for l, x in zip(before, d) if l.trader in full.insiders), sum(d),
               sum(1 for l, x in zip(before, d) if l.trader == "CREATOR" and x < 0))
    fixed = overlay.Insiders(token(window), w, before=offsets)
    for t in (LO, 30_000, 31_000):
        assert fixed.share(t) == full.share(t)
        assert fixed.sold_since(LO, t) == full.sold_since(LO, t)
    assert cut.share(31_000) > full.share(31_000)  # the bug: earlier insider sells invisible, share overstated


def test_token_insiders_sums_the_earlier_pool_trades_in_sql():
    con = duckdb.connect()
    con.execute("CREATE TABLE pool (pool VARCHAR, kind VARCHAR, slot UBIGINT, trader VARCHAR, base BIGINT)")
    con.executemany("INSERT INTO pool VALUES ('P', ?, ?, ?, ?)", [(l.kind, l.slot, l.trader, l.base) for l in POOL])
    w = CLOCK.slots(overlay.INSIDER_WINDOW_MS, 100)
    full = overlay.Insiders(token(POOL), w)
    got = tournament.token_insiders(con, token([l for l in POOL if l.slot >= LO]), LO, CLOCK)
    assert [got.share(t) for t in (LO, 31_000)] == [full.share(t) for t in (LO, 31_000)]
    assert got.sold_since(20_000, 31_000)[1] == 0 and got.creator_sold[0] == 1  # the creator's sell before the window


def test_f6_flows_cover_the_outflow_window(monkeypatch):
    seen = {}

    def load_pool_streams(con, windows):
        seen.update(windows)
        return {}

    monkeypatch.setattr(tournament.streams, "load_pool_streams", load_pool_streams)
    sigs = [NS(t=1_000_000), NS(t=1_000_500)]
    tournament.f6_flows(None, "P", sigs, 1_010_000, CLOCK)
    lo, hi = seen["P"]
    assert lo <= 1_000_000 - CLOCK.slots(F6_FLOW_MS, 1_000_000) and hi == 1_010_000


def test_a_cut_flow_window_exits_early():
    n = CLOCK.slots(F6_FLOW_MS, 1_000_000)
    t = 1_000_000
    # Organic inflow just before the position's t - 300 start, then a small outflow inside it.
    legs = [pool_leg(t - n + 10, "A", 0, "buy", quote=5_000), pool_leg(t - 100, "B", 0, "sell", quote=1_000)]
    full = FlowIndex(NS(pool_legs=legs, orientation="normal"))
    cut = FlowIndex(NS(pool_legs=[l for l in legs if l.slot >= t - 300], orientation="normal"))
    ctx = {"mark": 1.0, "slot": t + 2, "slots_held": 0, "pool": NS(real=10_000, b=0)}
    assert F6Exit(0.5, full, n, 10_000, "normal")(dict(ctx)) is None
    assert F6Exit(0.5, cut, n, 10_000, "normal")(dict(ctx)) == "outflow"
