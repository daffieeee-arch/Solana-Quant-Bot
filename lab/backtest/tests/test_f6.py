"""F6 building blocks: organic flow ignores farmers and reads both pool orientations; the exit
rule fires on the trailing stop, organic outflow and time."""

from backtest.replay import PoolLeg, Stream
from backtest.strategies import F6Exit, FlowIndex
from backtest.venues import Pool

SOL = 1_000_000_000
P = Pool(1_000_000 * 10**6, 500 * SOL, 2, 93, 30)


def leg(slot, kind, base, quote, farmer=False):
    return PoolLeg(slot, 0, (slot, 0, 0, 0), kind, False, base, quote, P, P, "w", farmer=farmer)


def test_flow_ignores_farmers_and_reads_orientation():
    legs = [leg(10, "buy", 1, 5 * SOL), leg(11, "sell", 1, 2 * SOL), leg(12, "buy", 1, 50 * SOL, farmer=True)]
    normal = FlowIndex(Stream("M", 0, "C", [], legs))
    assert normal.flow(0, 100) == 3 * SOL  # +5 -2, the farmer's 50 ignored
    assert normal.flow(11, 11) == -2 * SOL
    # Reversed pool: a pool sell is a token buy and its SOL amount is the base (WSOL).
    rev = [leg(10, "sell", 7 * SOL, 1), leg(11, "buy", 3 * SOL, 1)]
    assert FlowIndex(Stream("M", 0, "C", [], rev, orientation="reversed")).flow(0, 100) == 4 * SOL


class Flows:
    def __init__(self, value):
        self.value = value

    def flow(self, lo, hi):
        return self.value


def ctx(mark, slot, held):
    return {"mark": mark, "slot": slot, "slots_held": held, "pool": P}


def test_exit_rule():
    r = F6Exit(0.08, Flows(0), 1125, 54_000, "normal")
    assert r(ctx(100, 1, 1)) is None
    assert r(ctx(120, 2, 2)) is None
    assert r(ctx(111, 3, 3)) is None  # 7.5% below the peak
    assert r(ctx(110, 4, 4)) == "trailing_stop"  # 8.3% below
    out = F6Exit(0.08, Flows(-6 * SOL), 1125, 54_000, "normal")  # -1.2% of 500 SOL depth
    assert out(ctx(100, 1, 1)) == "outflow"
    assert F6Exit(0.08, Flows(0), 1125, 54_000, "normal")(ctx(100, 9, 54_000)) == "time"


def test_n4_exit_sells_after_the_next_crank():
    from backtest.strategies import N4Exit

    r = N4Exit([100, 144, 190], 400)
    base = {"mark": 1, "pool": P}
    assert r(dict(base, entry_slot=143, slot=143, slots_held=0)) is None  # crank at 144 not seen yet
    assert r(dict(base, entry_slot=143, slot=144, slots_held=1)) == "after_crank"
    assert r(dict(base, entry_slot=191, slot=500, slots_held=309)) is None
    assert r(dict(base, entry_slot=191, slot=591, slots_held=400)) == "time"
