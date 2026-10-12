"""Event feeds for the paper engine.

A feed yields normalized on-chain events in chain order (slot, tx_index, outer_ix, inner_ix). Each
event carries its venue (curve or pool), its key (the mint of a bonding curve, or the PumpSwap
pool), the block time, and the leg itself: the same CurveLeg / PoolLeg objects the replay uses,
with the state before and after, amounts, trader, limits and whether the call was direct. A feed
also answers metadata (tokens, pools) and the history of a curve (holdings since its create).

ReplayFeed reads the dev store (development slots only). LiveFeed is a stub until LAB data's
recorder exists; it will implement the same interface.
"""

import heapq
from dataclasses import dataclass

from backtest import streams

HOLDOUT_START = 452304000  # never replay the hold-out [HOLDOUT_START, HOLDOUT_END)
HOLDOUT_END = 454464000    # epoch 1052 on (validation, forward): after the hold-out, replay allowed


@dataclass(frozen=True, slots=True)
class Event:
    slot: int
    tx_index: int
    outer_ix: int
    inner_ix: int
    block_time: int
    venue: str  # curve | pool
    key: str  # mint (curve) or pool address (pool)
    leg: object  # backtest.replay.CurveLeg or PoolLeg
    # Live only: (received_unix_ns, created_unix_ns, segment written_unix) of the event's transaction,
    # from the segment's arrivals/ and _segment.json; None in replay. Reporting only (signal latency).
    arrival: tuple = None

    @property
    def order(self):
        return (self.slot, self.tx_index, self.outer_ix, self.inner_ix)


@dataclass(frozen=True, slots=True)
class TokenInfo:
    mint: str
    create_slot: int
    creator: str
    mayhem: bool
    quote_mint: str
    complete_slot: int
    pool: str


@dataclass(frozen=True, slots=True)
class PoolInfo:
    pool: str
    mint: str
    orientation: str
    quote_class: str
    mayhem: bool
    coin_creator: str
    create_slot: int


class Feed:
    def events(self):
        raise NotImplementedError

    def token(self, mint):
        raise NotImplementedError

    def pool(self, pool):
        raise NotImplementedError

    def curve_history(self, mint, upto_slot):
        """The token's curve legs from its create up to and including upto_slot."""
        raise NotImplementedError


class LiveFeed(Feed):
    """Placeholder for the live recorder (LAB data, Triton Yellowstone). Not available yet."""

    def __init__(self, *a, **k):
        raise NotImplementedError("LiveFeed comes with the recorder (W3); use ReplayFeed")


class ReplayFeed(Feed):
    """Events of a store for slots [lo, hi), in chain order. [lo, hi) must not touch the hold-out."""

    def __init__(self, store, lo, hi, batch=200_000, con=None):
        if lo < HOLDOUT_END and hi > HOLDOUT_START:
            raise ValueError(f"ReplayFeed never reads the hold-out [{HOLDOUT_START}, {HOLDOUT_END})")
        self.lo, self.hi, self.batch = lo, hi, batch
        self.con = con or streams.open_store(store, threads=2, memory="1GB")
        self.tokens = {r[0]: TokenInfo(*r) for r in self.con.execute(
            "SELECT mint, create_slot, creator, mayhem, quote_mint, complete_slot, pool FROM mints").fetchall()}
        self.pools = {r[0]: PoolInfo(*r) for r in self.con.execute(
            """SELECT pool, COALESCE(mint, pool), orientation, quote_class, COALESCE(mayhem, false), coin_creator, create_slot
               FROM pools WHERE quote_class IN ('sol', 'reversed')""").fetchall()}
        self.meta_bounds = self.con.execute("SELECT min(slot), max(slot) + 1 FROM blocks").fetchone()

    def token(self, mint):
        return self.tokens.get(mint)

    def pool(self, pool):
        return self.pools.get(pool)

    def curve_history(self, mint, upto_slot):
        rows = self.con.execute(f"""SELECT {streams.CURVE_COLS} FROM curve WHERE mint = ? AND slot <= ?
                                    ORDER BY slot, tx_index, outer_ix, inner_ix""", [mint, upto_slot]).fetchall()
        return streams._curve_legs(rows)

    def events(self, window=10_000):
        """Curve and pool legs merged in chain order. Each slot window is read as two sorted
        streams (curve, pool) and merged, so memory stays bounded."""
        ctx = {}  # per pool: last virtual quote and fee tiers (pool_leg)
        for a in range(self.lo, self.hi, window):
            b = min(a + window, self.hi)
            bt = dict(self.con.execute(f"SELECT slot, block_time FROM blocks WHERE slot >= {a} AND slot < {b}").fetchall())
            cc, pc = self.con.cursor(), self.con.cursor()
            cc.execute(f"""SELECT mint, {streams.CURVE_COLS} FROM curve WHERE slot >= {a} AND slot < {b}
                           ORDER BY slot, tx_index, outer_ix, inner_ix""")
            pc.execute(f"""SELECT pool, {streams.POOL_COLS} FROM pool WHERE slot >= {a} AND slot < {b}
                           ORDER BY slot, tx_index, outer_ix, inner_ix""")

            def curve_events():
                while rows := cc.fetchmany(20_000):
                    for r in rows:
                        yield Event(r[1], r[2], r[3], r[4], bt.get(r[1]), "curve", r[0], streams.curve_leg(r[1:]))

            def pool_events():
                while rows := pc.fetchmany(20_000):
                    for r in rows:
                        yield Event(r[2], r[3], r[4], r[5], bt.get(r[2]), "pool", r[0],
                                    streams.pool_leg(r[1:], ctx.setdefault(r[0], {})))

            yield from heapq.merge(curve_events(), pool_events(), key=lambda e: e.order)
            cc.close()
            pc.close()
