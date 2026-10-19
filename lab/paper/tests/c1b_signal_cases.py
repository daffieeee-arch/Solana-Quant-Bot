"""The shared golden cases of M1 C1b (research/m1-c1b-signals-spec-2026-10-19.md §6.2): small feeds that the real paper
engine (paper/engine.py, unchanged) runs, and whose signal rows the Rust route must reproduce. test_signal_cases.py
asserts that the engine still writes exactly the stored rows; lab/live's signals test reads the same file.

  C1B_CASES_REGEN=1 pytest paper/tests/test_signal_cases.py      # rewrite data/c1b_signal_cases.json from the engine
  python -m paper.tests.c1b_signal_cases                          # the same

The file's format is FORMAT below (also written into the file under "format"). Every case is synthetic except T1: its
13 curve legs are dev2's own (one small read-only query, C1B_DEV2_STORE). Without the store a regeneration keeps T1's
legs from the file. The other cases are built from venue math (backtest/venues.py), so every leg's state chain holds
and the engine's entries and exits run as on real data.
"""

import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import replace

from backtest import streams
from backtest.replay import CurveLeg, PoolLeg
from backtest.strategies import F7_GRID, SOL, Clock, decision_bounds
from backtest.venues import Curve, Pool, curve_buy_exact_out, curve_sell, pool_buy_exact_in, pool_buy_exact_out, pool_sell
from paper.engine import Engine, Group, groups_state, variant
from paper.feed import Event, Feed, PoolInfo, TokenInfo
from paper.journal import Journal
from paper.journal_diff import signal_row

FORMAT = {
    "file": "{format, spec, cases: [case]}; every case runs on its own, from an empty engine.",
    "case": "name, why (what the case shows), clock, dev_bounds, groups, labels, tokens, pools, events, expect, expect_state.",
    "clock": "Clock(ms_per_slot): the measured ms per slot by epoch, keys are epoch numbers as strings (the store meta's "
             "ms_per_slot), values verbatim floats: parse them bit-exact (serde_json float_roundtrip). An epoch not in the map "
             "takes the last earlier one, else 267.3 (strategies.py:57-66).",
    "dev_bounds": "[lo, hi): Engine(dev_bounds=...), as the store's meta_bounds; burn = dlo = lo + slots(ms(324000), lo), "
                  "dhi = hi - slots(ms(20000), hi - 1) (engine.py:250-253).",
    "groups": "the exact text the engine journals as state 'groups' (engine.py:1386-1391, groups_state: json.dumps with "
              "sort_keys of name -> primary, variants, farm, segments, d, d_ms, q, n2_k, risk, wallet, "
              "require_farm_complete). The engine is built from it with ignore_risk: every group passes its risk limits "
              "(engine.py:532-535).",
    "labels": "address -> a readable name, for the case's synthetic keys and traders (documentation only).",
    "tokens": "the mints table: mint, create_slot, creator, mayhem, quote_mint; null is None (a quote_mint of None is "
              "never SOL). A curve key that is not listed is an absent mint.",
    "pools": "the pools table after COALESCE(mint, pool), as snapshot pools.parquet and ReplayFeed have it: pool, mint, "
             "orientation (normal or reversed), quote_class, mayhem, coin_creator, create_slot. The engine knows only the "
             "SOL markets, quote_class sol or reversed (feed.py:97-99); a pool of another class is listed but unknown to it.",
    "events": "the feed in chain order (slot, tx, outer, inner), one leg each: venue (curve or pool), key (the mint or the "
              "pool), slot, tx, outer, inner, block_time (null: None), then the leg's fields for its venue.",
    "events.curve": "backtest/replay.py CurveLeg as lab/core CurveLeg holds it: variant (as CurveVariant::as_str spells it), "
                    "is_buy, t, q, fee (fee plus creator fee), vt, vq, rt, rq (the reserves AFTER the trade), fee_bps, "
                    "creator_bps, trader, budget, max_cost, min_tokens, min_out (null: absent), direct. The reserves before "
                    "are vt + s*t, vq - s*q, rt + s*t, rq - s*q with s = 1 for a buy and -1 for a sell (streams.py:35-38).",
    "events.pool": "backtest/replay.py PoolLeg as lab/core PoolLeg holds it: kind (buy, sell, boost, withdraw or deposit), "
                   "exact_out, base, quote_net, the pool BEFORE the event (b, e, lp_bps, protocol_bps, creator_bps, "
                   "cashback_bps, virt), e_delta, trader, limit_quote, limit_base, boost_left, direct, lp_amount, lp_supply "
                   "(null: absent), farmer. After the event e is e + e_delta, b is b + base for a sell or deposit and "
                   "b - base otherwise (streams.py:56-57, 66).",
    "expect": "the journal's signals rows after the run, sorted by (family, variant, key, t), in the C1b projection "
              "(paper/journal_diff.py signal_row): family, variant, key, t, day, venue, orientation, depth, trigger_trader, "
              "farm, pool_seen_slot, farm_window_complete, block_time, d_ms, farm_complete, gate (the farm gate the row "
              "stopped at, or 'pass'), then status and reason as the Python engine wrote them. C1b compares all but status "
              "and reason: past the farm gates the engine decides entered or busy (C2/C3), where the Rust route writes "
              "NULL; a gated row is 'skipped' with the gate as reason on both sides.",
    "expect_state": "the engine's signal state after the last slot: first_slot; hour (the first slot of the current hour "
                    "bucket); hour_events, the N2 candidates of the current hour per bucket (curve, pool_normal, "
                    "pool_reversed) as [key, slot, depth] in insertion order; farm_win, per pool with items, its items "
                    "in order, each [slot, tx, outer, inner, SOL amount, farmer SOL amount] (the engine's (slot, order, "
                    "amount, farm) of engine.py:580 with the slot once: order is (slot, tx, outer, inner)); pool_seen, "
                    "[first slot, last slot] of every pool with an event, a SOL market or not (step 3 sees every pool key, "
                    "engine.py:393-395), less the pools the 24 h prune has removed (POOL_SEEN_KEEP_MS, engine.py:697-701).",
    "integers": "every integer is a JSON integer and fits i64 (the largest here, depths near 1.2e16, lie above 2^53); "
                "fields Rust holds as u64 are never negative.",
    "regenerate": "C1B_CASES_REGEN=1 pytest paper/tests/test_signal_cases.py (or python -m paper.tests.c1b_signal_cases): "
                  "expect and expect_state are the engine's own output.",
}
PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "c1b_signal_cases.json")
STORE = os.environ.get("C1B_DEV2_STORE", "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb")

# dev2's meta (ms_per_slot) and meta_bounds; the generator checks both against the store when it reads T1.
DEV2_CLOCK = {"1033": 316.36, "1034": 315.9, "1035": 316.53, "1036": 317.13, "1037": 266.46, "1038": 266.83, "1039": 267.12,
              "1040": 266.17, "1041": 265.83, "1042": 267.83, "1043": 267.93, "1044": 268.26, "1045": 267.65, "1046": 267.35}
DEV2_BOUNDS = (446_256_000, 452_304_000)
DEFAULT_KEY = "11111111111111111111111111111111"
WSOL = "So11111111111111111111111111111111111111112"
PUMP_QUOTE = "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn"
SOL_MARKETS = ("sol", "reversed")
T1 = "7orRHgZjwNmvpcSBHUMQ8wqW6rwKpqE1hJJR43AkQa1Z"
T2 = "8iXGs3rSqshaWD9iCZEXu5XLvD5CUhuu7DSA7kd3Cg92"
CURVE = ("variant", "is_buy", "t", "q", "fee", "vt", "vq", "rt", "rq", "fee_bps", "creator_bps", "trader", "budget", "max_cost",
         "min_tokens", "min_out", "direct")
POOL = ("kind", "exact_out", "base", "quote_net", "b", "e", "lp_bps", "protocol_bps", "creator_bps", "cashback_bps", "virt",
        "e_delta", "trader", "limit_quote", "limit_base", "boost_left", "direct", "lp_amount", "lp_supply", "farmer")
CASE = ("name", "why", "clock", "dev_bounds", "groups", "labels", "tokens", "pools", "events", "expect", "expect_state")
BUCKETS = (("curve", ("curve", "curve")), ("pool_normal", ("pool", "normal")), ("pool_reversed", ("pool", "reversed")))
TP_T = {variant(g)[0]: g for g in F7_GRID}  # tp4_T600 -> (0.04, 600); exit variants go by their name


# ---------------------------------------------------------------------------------------------- the file's legs and feed

def fields(ev):
    """An Event as its case entry (FORMAT events)."""
    l = ev.leg
    head = {"venue": ev.venue, "key": ev.key, "slot": ev.slot, "tx": ev.tx_index, "outer": ev.outer_ix, "inner": ev.inner_ix,
            "block_time": ev.block_time}
    if ev.venue == "curve":
        p = l.post
        return {**head, "variant": l.variant, "is_buy": l.is_buy, "t": l.t, "q": l.q, "fee": l.fee, "vt": p.vt, "vq": p.vq,
                "rt": p.rt, "rq": p.rq, "fee_bps": p.fee_bps, "creator_bps": p.creator_bps, "trader": l.trader,
                "budget": l.budget, "max_cost": l.max_cost, "min_tokens": l.min_tokens, "min_out": l.min_out, "direct": l.direct}
    p = l.pre
    return {**head, "kind": l.kind, "exact_out": l.exact_out, "base": l.base, "quote_net": l.quote_net, "b": p.b, "e": p.e,
            "lp_bps": p.lp_bps, "protocol_bps": p.protocol_bps, "creator_bps": p.creator_bps, "cashback_bps": p.cashback_bps,
            "virt": p.virt, "e_delta": l.post.e - p.e, "trader": l.trader, "limit_quote": l.limit_quote,
            "limit_base": l.limit_base, "boost_left": l.boost_left, "direct": l.direct, "lp_amount": l.lp_amount,
            "lp_supply": l.lp_supply, "farmer": l.farmer}


def event(d):
    """A case entry as the Event the feed hands out, its leg built as streams.py builds it from a store row."""
    order = (d["slot"], d["tx"], d["outer"], d["inner"])
    if d["venue"] == "curve":
        post = Curve(d["vt"], d["vq"], d["rt"], d["rq"], d["fee_bps"], d["creator_bps"])
        s = 1 if d["is_buy"] else -1
        pre = replace(post, vt=post.vt + s * d["t"], vq=post.vq - s * d["q"], rt=post.rt + s * d["t"], rq=post.rq - s * d["q"])
        leg = CurveLeg(d["slot"], d["tx"], order, d["is_buy"], d["t"], d["q"], d["fee"], d["variant"], pre, post, d["trader"],
                       d["budget"], d["max_cost"], d["min_tokens"], d["min_out"], d["direct"])
    else:
        pre = Pool(d["b"], d["e"], d["lp_bps"], d["protocol_bps"], d["creator_bps"], d["cashback_bps"], virt=d["virt"])
        b = pre.b + d["base"] if d["kind"] in ("sell", "deposit") else pre.b - d["base"]
        leg = PoolLeg(d["slot"], d["tx"], order, d["kind"], d["exact_out"], d["base"], d["quote_net"], pre,
                      replace(pre, b=b, e=pre.e + d["e_delta"]), d["trader"], d["limit_quote"], d["limit_base"],
                      d["boost_left"], d["direct"], d["lp_amount"], d["lp_supply"], d["farmer"])
    return Event(d["slot"], d["tx"], d["outer"], d["inner"], d["block_time"], d["venue"], d["key"], leg)


class CaseFeed(Feed):
    """A case as a replay feed: its events, tokens and SOL-market pools. Like ReplayFeed it has every pool's trades
    (all_pools: farm_window_complete counts from the feed's first slot), no arrival times and no farmer seed."""

    all_pools = True

    def __init__(self, case):
        self.meta_bounds = tuple(case["dev_bounds"])
        self.tokens = {t["mint"]: TokenInfo(t["mint"], t["create_slot"], t["creator"], t["mayhem"], t["quote_mint"], None, None)
                       for t in case["tokens"]}
        self.pools = {p["pool"]: PoolInfo(**p) for p in case["pools"] if p["quote_class"] in SOL_MARKETS}
        self.evs = [event(e) for e in case["events"]]

    def token(self, mint):
        return self.tokens.get(mint)

    def pool(self, pool):
        return self.pools.get(pool)

    def curve_history(self, mint, upto_slot):
        return [e.leg for e in self.evs if e.venue == "curve" and e.key == mint and e.slot <= upto_slot]

    def events(self):
        yield from self.evs


def engine(case, journal):
    """The engine of a case: its groups (the primary first, the others in the state's name order), clock and bounds,
    without risk limits."""
    gs = json.loads(case["groups"])
    (name, p), = [(n, g) for n, g in gs.items() if g["primary"]]
    others = [Group(n, [TP_T.get(v, v) for v in g["variants"]], farm=g["farm"], segments=g["segments"], d=g["d"],
                    d_ms=g["d_ms"], q=g["q"], n2_k=g["n2_k"], risk_path=g["risk"], wallet_file=g["wallet"],
                    require_farm_complete=g["require_farm_complete"]) for n, g in gs.items() if not g["primary"]]
    eng = Engine(CaseFeed(case), journal, p["risk"], variants=[TP_T.get(v, v) for v in p["variants"]], d=p["d"], d_ms=p["d_ms"],
                 q=p["q"], n2_k=p["n2_k"], segments=p["segments"], farm=p["farm"], name=name, groups=others,
                 ignore_risk=True, clock=Clock(case["clock"]), dev_bounds=tuple(case["dev_bounds"]), backup=False)
    if groups_state(eng) != case["groups"]:
        raise ValueError(f"case {case['name']}: groups is not what the engine journals: {groups_state(eng)}")
    return eng


def state(eng):
    """The engine's signal state after the run (FORMAT expect_state). A farm_win item's slot is its order's first
    field, so it is written once."""
    assert all(s == o[0] for xs in eng.farm_win.values() for s, o, _, _ in xs)
    return {"first_slot": eng.first_slot, "hour": eng.hour,
            "hour_events": {name: [list(x) for x in eng.hour_events.get(k, [])] for name, k in BUCKETS},
            "farm_win": {p: [[*o, a, f] for _, o, a, f in xs] for p, xs in sorted(eng.farm_win.items()) if xs},
            "pool_seen": {p: [eng.pool_seen[p], eng.pool_last[p]] for p in sorted(eng.pool_seen)}}


def run(case):
    """(expect, expect_state) of a case: the engine's signals rows in the C1b projection, and its signal state."""
    with tempfile.TemporaryDirectory(prefix="c1b-case-") as d:
        j = Journal(d)
        eng = engine(case, j)
        eng.run()
        rows = sorted((signal_row(r) for r in j.rows("signals")), key=lambda r: (r["family"], r["variant"], r["key"], r["t"]))
        j.close()
    return rows, state(eng)


# ---------------------------------------------------------------------------------------------- building a case

def groups(n2_k=1):
    """The reference groups (spec D3): K3 primary, tp4_T600 everywhere with farm all, d_ms 600 and n2_k; and group H as
    paper/h-arms-2026-10-16.yaml has it (pool_reversed, organic, require_farm_complete, no N2)."""
    return {"K3": {"primary": True, "variants": ["tp4_T600"], "farm": "all", "segments": None, "d": 1, "d_ms": 600, "q": 0.5,
                   "n2_k": n2_k, "risk": None, "wallet": "wallet.json", "require_farm_complete": False},
            "H": {"primary": False, "variants": ["flow300_H6", "flow300_tp100_H6"], "farm": "organic",
                  "segments": ["pool_reversed"], "d": 1, "d_ms": 600, "q": 0.5, "n2_k": 0, "risk": None,
                  "wallet": "wallet-h.json", "require_farm_complete": True}}


B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def addr(label):
    """A deterministic base58 address for a synthetic key or trader (sha256 of the label, as a Solana key is 32 bytes)."""
    n, s = int.from_bytes(hashlib.sha256(f"c1b golden {label}".encode()).digest(), "big"), ""
    while n:
        n, r = divmod(n, 58)
        s = B58[r] + s
    return s


def block_time(slot):
    return 1_790_000_000 + (slot - 450_000_000) * 267 // 1000


T = 1_000_000_000_000  # curve tokens in a small trade (~0.1 SOL at the start)
START = Curve(673_000_000_000_000, 70 * SOL, 393_100_000_000_000, 40 * SOL, 95, 30)  # test_engine.py's curve
FEES = (20, 5, 5, 0)  # PumpSwap's lp, protocol and creator fee tiers in bps, no cashback


class Case:
    """A case under construction: tokens, pools and legs by readable labels; the events go into chain order. A trade's
    tx index is 10 * the key's place in the case (the order of its first trades) + the tx given, so legs of different keys
    never share a chain position and keys that trade in one slot follow each other in the order they were added."""

    def __init__(self, name, why, n2_k=1, clock=None, dev_bounds=DEV2_BOUNDS):
        self.name, self.why, self.n2_k = name, why, n2_k
        self.clock, self.dev_bounds = dict(clock or DEV2_CLOCK), list(dev_bounds)
        self.labels, self.tokens, self.pools, self.evs, self.places = {}, [], [], [], {}

    def key(self, label):
        if label is None:
            return None
        a = addr(label)
        self.labels[a] = label
        return a

    def tx(self, key, tx):
        return 10 * self.places.setdefault(key, len(self.places)) + tx

    def token(self, label, create_slot=450_100_000, creator="creator", mayhem=False, quote_mint=DEFAULT_KEY):
        mint = self.key(label)
        self.tokens.append({"mint": mint, "create_slot": create_slot, "creator": self.key(creator), "mayhem": mayhem,
                            "quote_mint": quote_mint})
        return mint

    def pool(self, label, orientation="reversed", quote_class=None, mint=None, coin_creator="coin creator",
             create_slot=450_000_000, mayhem=False):
        pool = self.key(label)
        self.pools.append({"pool": pool, "mint": mint or pool, "orientation": orientation,
                           "quote_class": quote_class or ("reversed" if orientation == "reversed" else "sol"),
                           "mayhem": mayhem, "coin_creator": self.key(coin_creator), "create_slot": create_slot})
        return pool

    def curve(self, mint, trades, rq=None, start=START):
        """Curve trades (slot, tx, trader, 'buy' | 'sell', tokens) from `start`, priced by the venue. rq: the real SOL
        reserves after the first trade (the N2 depth of a one-trade token): the start's rq is shifted to give it (rq only
        caps a sell, so the trades are the same)."""
        legs = []
        s = start
        for slot, tx, trader, side, t in trades:
            tx = self.tx(mint, tx)
            f = curve_buy_exact_out(s, t) if side == "buy" else curve_sell(s, t)
            assert f is not None and (side == "buy" or f.curve_quote < s.rq), (self.name, slot, side)  # no capped sell
            legs.append(CurveLeg(slot, tx, (slot, tx, 0, 0), side == "buy", t, f.curve_quote, f.fees, side, s, f.state,
                                 self.key(trader), max_cost=10**15 if side == "buy" else None,
                                 min_out=0 if side == "sell" else None, direct=True))
            s = f.state
        if rq is not None and legs[0].post.rq != rq:
            return self.curve(mint, trades, start=replace(start, rq=start.rq + rq - legs[0].post.rq))
        self.evs += [Event(l.slot, l.tx, 0, 0, block_time(l.slot), "curve", mint, l) for l in legs]
        return legs

    def trades(self, pool, orientation, b, e, trades):
        """Pool trades (slot, tx, trader, 'buy' | 'sell' of the token, SOL, farmer) from reserves (b, e), priced by the
        venue. Reversed (WSOL base): a token buy sells exactly that SOL into the pool, a token sell takes exactly that SOL
        out (an exact-out buy). Normal: a token buy spends that SOL (exact in), a token sell is the smallest that pays it."""
        s, legs = Pool(b, e, *FEES), []
        for slot, tx, trader, side, sol, farmer in trades:
            tx = self.tx(pool, tx)
            if orientation == "reversed":
                kind, f = ("sell", pool_sell(s, sol)) if side == "buy" else ("buy", pool_buy_exact_out(s, sol))
                base, exact_out = sol, side == "sell"
            elif side == "buy":
                kind, f, exact_out = "buy", pool_buy_exact_in(s, sol), False
                base = f.tokens
            else:
                kind, base, exact_out = "sell", _tokens_paying(s, sol), False
                f = pool_sell(s, base)
            assert f is not None, (self.name, slot, side)
            legs.append(PoolLeg(slot, tx, (slot, tx, 0, 0), kind, exact_out, base, f.trader_quote, s, f.state, self.key(trader),
                                direct=True, farmer=farmer))
            s = f.state
        self.evs += [Event(l.slot, l.tx, 0, 0, block_time(l.slot), "pool", pool, l) for l in legs]
        return legs

    def build(self):
        evs = sorted(self.evs, key=lambda e: e.order)
        assert len({e.order for e in evs}) == len(evs), f"{self.name}: two legs at one chain position"
        groups_text = json.dumps(groups(self.n2_k), sort_keys=True)  # the engine's own text replaces it (engine())
        case = {"name": self.name, "why": self.why, "clock": self.clock, "dev_bounds": self.dev_bounds, "groups": groups_text,
                "labels": dict(sorted(self.labels.items(), key=lambda x: x[1])), "tokens": self.tokens, "pools": self.pools,
                "events": [fields(e) for e in evs]}
        for e, d in zip(evs, case["events"]):
            assert event(d) == e, f"{self.name}: {d} does not give its leg back"
        return case


def _tokens_paying(p, sol):
    """The fewest tokens a normal pool pays at least `sol` for (pool_sell's trader_quote grows with the tokens)."""
    lo, hi = 1, 100 * p.b
    while lo < hi:
        m = (lo + hi) // 2
        f = pool_sell(p, m)
        if f is not None and f.trader_quote >= sol:
            hi = m
        else:
            lo = m + 1
    return lo


def _reversed_trigger(c, pool, t, depth, inflow=2 * SOL, sell=None, early=()):
    """A reversed pool's F7 trigger at t: a token buy of `inflow` SOL at t - 30 (positive inflow), then a token sell at t
    of 5 % of the pool's SOL depth `depth` (lamports; the trigger needs 4 %), at least 3 SOL, in whole SOL. early: further
    trades before (slot, tx, trader, side, sol, farmer), whose net SOL is taken off the start so the depth at t holds."""
    sell = sell or -(-max(3 * SOL, depth // 20) // SOL) * SOL
    trades = sorted([*early, (t - 30, 0, "buyer", "buy", inflow, False), (t, 0, "seller", "sell", sell, False)])
    net = sum(sol if side == "buy" else -sol for slot, _, _, side, sol, _ in trades if slot < t)
    return c.trades(pool, "reversed", depth - net, 10**18 if depth > 10**15 else 10**15, trades)


def _curve_trigger(c, mint, t, creator_sells=(), rq=None):
    """A curve's F7 trigger at t (test_engine.py's trigger_token): buys of 20T at t - 40 and t - 30, a sell of 60T by
    'seller' at t (about 6 SOL, real SOL depth near 44 SOL from the standard start); creator_sells: slots of a 1T sell by
    'creator'. rq: the real SOL reserves after the first trade."""
    trades = [(s, 0, "creator", "sell", T) for s in creator_sells]
    trades += [(t - 40, 0, "buyer", "buy", 20 * T), (t - 30, 0, "buyer 2", "buy", 20 * T), (t, 0, "seller", "sell", 60 * T)]
    return c.curve(mint, sorted(trades), rq=rq)


# ---------------------------------------------------------------------------------------------- the cases

def t1_cases(t1):
    """T1 (spec §6.1): 7orRHgZj..., quoted in PUMP, created 450196501, not mayhem. Its 13 legs pass the event-local
    trigger and f7_signal at 450197213 in raw units (epoch 1042 windows 50, 200, 20), but the universe rejects the
    quote. t1: (token row, events) from dev2."""
    tok, evs = t1
    base = {"clock": dict(DEV2_CLOCK), "dev_bounds": list(DEV2_BOUNDS), "groups": json.dumps(groups(), sort_keys=True),
            "labels": {T1: "T1", tok["creator"]: "T1 creator", "6Ljyr7EoLpPyvbWUZ6Urh126cYLxDXVsRoHve1WWxok9": "T1 trigger trader"},
            "pools": [], "events": evs}
    out = []
    for name, change, why in (
            ("t1_as_stored", {}, "T1 as stored: quote_mint PUMP is not SOL, so the universe rejects the curve: no row, no N2 "
                                 "candidacy (hour_events stays empty)."),
            ("t1_quote_default", {"quote_mint": DEFAULT_KEY}, "Control: T1 quoted in 1111...: one F7 row at 450197213, depth "
                                                               "53,657,221,584 (pre rq), trigger trader 6Ljyr7Eo..., farm n/a."),
            ("t1_quote_wsol", {"quote_mint": WSOL}, "Control: T1 quoted in WSOL: the same one F7 row."),
            ("t1_quote_null", {"quote_mint": None}, "quote_mint NULL: None is never SOL (engine.py:559), so no row and no "
                                                    "N2 candidacy; never a load error."),
            ("t1_token_absent", None, "Mint absent: a curve key without a token gives no row and no hour_events entry."),
            ("t1_mayhem", {"quote_mint": DEFAULT_KEY, "mayhem": True}, "Mayhem: SOL-quoted but mayhem, so no row and no N2 "
                                                                       "candidacy.")):
        out.append({"name": name, "why": why, **base, "tokens": [] if change is None else [{**tok, **change}]})
    return out


def t2_cases():
    """T2 (spec §6.1, D8): a pool modelled on 8iXGs3rS..., class 'other', normal, its token quoted in PUMP, with a token
    sell of 195,002,882,750 at a SOL depth of 2,564,283,573,690 after positive inflow (t 450145128, epoch 1042)."""
    t, depth, sell = 450_145_128, 2_564_283_573_690, 195_002_882_750
    out = []
    for name, quote_class, why in (
            ("t2_other_pool", "other", "T2: the pool is not a SOL market (class 'other'), so the engine does not know it: no "
                                       "row, no hour_events entry, no farm window."),
            ("t2_control_sol", "sol", "Control: the same legs in a 'sol' pool: one F7 row, farm organic. Its token is "
                                      "quoted in PUMP: a pool has no token-quote check.")):
        c = Case(name, why)
        mint = "74QqbxgSWRTM6TvxpggsosuEwN21tmjZRSegyfFANAf9"
        c.labels.update({T2: "T2 pool", mint: "T2 token", "5cHQCizCfJmkPjNqJ4ZnEGhKeG8ErtREPoSJm3JTjcUh": "T2 coin creator",
                         "E11BrsVdTAaKZetQjnpD1aU2AWrqw37ftDyNQhQuZdxK": "T2 token creator"})
        c.tokens.append({"mint": mint, "create_slot": 450_128_329, "creator": "E11BrsVdTAaKZetQjnpD1aU2AWrqw37ftDyNQhQuZdxK",
                         "mayhem": False, "quote_mint": PUMP_QUOTE})
        c.pools.append({"pool": T2, "mint": mint, "orientation": "normal", "quote_class": quote_class, "mayhem": False,
                        "coin_creator": "5cHQCizCfJmkPjNqJ4ZnEGhKeG8ErtREPoSJm3JTjcUh", "create_slot": 450_128_374})
        # Exact-in buys move e by what they put in, whatever b is: the start's e gives the depth at t; b is searched
        # until the sell pays exactly 195,002,882,750.
        buys = [(t - 30, 0, "buyer", "buy", 3 * SOL, False), (t - 12, 0, "buyer 2", "buy", 2 * SOL, False)]
        e0 = depth - sum(pool_buy_exact_in(Pool(10**15, depth, *FEES), sol).state.e - depth for *_, sol, _ in buys)
        for b in range(40_000_000_000_000, 40_000_000_001_000):
            s = Pool(b, e0, *FEES)
            for *_, sol, _ in buys:
                s = pool_buy_exact_in(s, sol).state
            if s.e == depth and pool_sell(s, _tokens_paying(s, sell)).trader_quote == sell:
                break
        else:
            raise AssertionError("no start pays T2's sell exactly")
        legs = c.trades(T2, "normal", b, e0, [*buys, (t, 0, "seller", "sell", sell, False)])
        assert (legs[-1].pre.e - legs[-1].pre.virt, legs[-1].quote_net) == (depth, sell)
        out.append(c.build())
    return out


def pool_mint_absent():
    c = Case("pool_mint_absent",
             "Pools need no token: 'mint absent' is for curves only (spec §3.5). PA (reversed, no token) signals, its creator "
             "being its coin_creator. PB (reversed, no token): its coin_creator's sell 100 slots before the trigger blocks "
             "it, as the creator's would. PC (sol, token present, creator TC): its coin_creator's sell does not block, "
             "the token's creator wins. PD (sol, token present with creator NULL): creator is None, no fallback to "
             "coin_creator, so its coin_creator's sell does not block either.")
    pa, pb = c.pool("PA", coin_creator="PA coin creator"), c.pool("PB", coin_creator="PB coin creator")
    tc, td = c.token("TC", creator="TC creator", quote_mint=PUMP_QUOTE), c.token("TD", creator=None)
    pc = c.pool("PC", "normal", mint=tc, coin_creator="PC coin creator")
    pd = c.pool("PD", "normal", mint=td, coin_creator="PD coin creator")
    _reversed_trigger(c, pa, 450_160_000, 200 * SOL)
    _reversed_trigger(c, pb, 450_160_100, 400 * SOL, early=[(450_160_000, 0, "PB coin creator", "sell", SOL, False)])
    for pool, t, depth, coin in ((pc, 450_160_200, 800 * SOL, "PC coin creator"), (pd, 450_160_300, 1_600 * SOL, "PD coin creator")):
        trades = [(t - 100, 0, coin, "sell", SOL, False), (t - 30, 0, "buyer", "buy", 2 * SOL, False),
                  (t, 0, "seller", "sell", depth // 20, False)]
        c.trades(pool, "normal", 10**15, depth, trades)
    return c.build()


def n2_cases():
    out = []
    for k in (1, 3):
        c = Case(f"n2_k{k}", f"N2 with k = {k}: the trigger P (reversed, depth 1,000 SOL) has six pools in the band "
                             "[800, 1,200] SOL that traded earlier this hour (Q1-Q6, 850-1,150) and two outside it (O1 790, O2 "
                             f"1,210). N2 picks the first {k} of the key-sorted candidates ordered by splitmix64(sha256(key) ^ "
                             "sha256('paper-N2|P|t')). Each N2 row has P's depth and farm and its own pool_seen_slot.", n2_k=k)
        t = 450_170_000
        for i, (label, depth) in enumerate((("Q1", 850), ("Q2", 900), ("Q3", 950), ("Q4", 1_050), ("Q5", 1_100), ("Q6", 1_150),
                                            ("O1", 790), ("O2", 1_210))):
            c.trades(c.pool(label), "reversed", depth * SOL, 10**15, [(t - 600 + 50 * i, 0, "small buyer", "buy", SOL, False)])
        _reversed_trigger(c, c.pool("P"), t, 1_000 * SOL)
        out.append(c.build())
    return out


def n2_band_edges_above_2_53():
    depth = 12_000_000_000_000_000
    lo, hi = 0.8 * depth, 1.2 * depth
    assert lo > 2**53 and lo == int(lo) and hi == int(hi) and int(lo) % 4 == 0 and int(hi) % 4 == 0
    c = Case("n2_band_edges_above_2_53",
             f"The pool band compares an integer depth with 0.8 and 1.2 times the trigger's depth exactly (spec D7). P's "
             f"depth is {depth:,}, so the band is [{int(lo):,}, {int(hi):,}], both above 2^53. E_lo and E_hi lie on the "
             "edges and are picked (k = 3); X_lo (one below) and X_hi (one above) are not, although as f64 they round onto "
             "the edges (a cast to f64 would take them).", n2_k=3)
    t = 450_175_000
    for i, (label, b) in enumerate((("E_lo", int(lo)), ("X_lo", int(lo) - 1), ("E_hi", int(hi)), ("X_hi", int(hi) + 1))):
        c.trades(c.pool(label), "reversed", b, 10**18, [(t - 400 + 50 * i, 0, "small buyer", "buy", SOL, False)])
    _reversed_trigger(c, c.pool("P"), t, depth)
    return c.build()


def n2_curve_universe():
    c = Case("n2_curve_universe",
             "The curve N2 universe is every non-mayhem SOL-quoted token, of any age (engine.py:702-705), in the band of "
             "+-5 SOL around the trigger's depth, both edges included. CT triggers (k = 3): C_hi (quote 1111, depth +5 SOL), "
             "C_lo (WSOL, -5 SOL) and C_old (created before burn) are picked; C_hi_out (+5 SOL + 1), C_lo_out (-5 SOL - 1), "
             "C_qnull (quote None), C_mayhem, C_pump (quote PUMP) and C_absent (no token) are not. C_old's own trigger "
             "after CT gives no row: its curve is older than burn.", n2_k=3)
    burn = decision_bounds(None, *DEV2_BOUNDS, Clock(DEV2_CLOCK))[0]
    t = 450_181_000
    ct = c.token("CT")
    legs = _curve_trigger(c, ct, t)
    depth = legs[-1].post.rq + legs[-1].q
    for i, (label, kw, rq) in enumerate((("C_hi", {}, depth + 5 * SOL), ("C_lo", {"quote_mint": WSOL}, depth - 5 * SOL),
                                         ("C_hi_out", {}, depth + 5 * SOL + 1), ("C_lo_out", {}, depth - 5 * SOL - 1),
                                         ("C_qnull", {"quote_mint": None}, depth), ("C_mayhem", {"mayhem": True}, depth),
                                         ("C_pump", {"quote_mint": PUMP_QUOTE}, depth), ("C_absent", None, depth))):
        mint = c.key(label) if kw is None else c.token(label, **kw)
        c.curve(mint, [(t - 500 + 20 * i, 0, "small buyer", "buy", T)], rq=rq)
    old = c.token("C_old", create_slot=burn - 1)
    trades = [(t - 300, 0, "small buyer", "buy", T), (t - 20, 0, "buyer", "buy", 20 * T), (t + 5, 0, "seller", "sell", 60 * T)]
    c.curve(old, trades, rq=depth)
    return c.build()


def n2_collision():
    c = Case("n2_collision",
             "Two triggers in one slot pick the same N2 key, and the later write wins (INSERT OR REPLACE, spec §3.9). In slot "
             "S, P1 (depth 850 SOL, farm flagged: its farmers made 6 of its 7 SOL in the hour) and then P2 (1,180 SOL, "
             "organic) both have only Q (1,000 SOL) in their band. Q's N2 row carries P2's depth and farm; the Python engine "
             "wrote it as busy, after P1's write entered it (status is C2's).")
    t, q = 450_185_000, c.pool("Q")
    c.trades(q, "reversed", 1_000 * SOL, 10**15, [(t - 200, 0, "small buyer", "buy", SOL, False)])
    farming = [(t - 41, 0, "farmer", "buy", 2 * SOL, True), (t - 36, 0, "farmer", "sell", 2 * SOL, True),
               (t - 31, 0, "farmer", "buy", 2 * SOL, True)]
    _reversed_trigger(c, c.pool("P1"), t, 850 * SOL, inflow=SOL, early=farming)
    _reversed_trigger(c, c.pool("P2"), t, 1_180 * SOL)
    return c.build()


def hour_rollover_order():
    c = Case("hour_rollover_order",
             "The hour rolls over inside a slot, after its first key's F7 (engine.py:403-406, 689). Slot B = 450179413 opens "
             "an hour (h 13,441 at epoch 1042). P1 (950 SOL), B's first key, triggers and still sees the previous hour: its "
             "N2 picks Q (1,000 SOL) and P2 (1,048 SOL at its inflow, B - 30). P2 (1,050 SOL) triggers next in B and sees "
             "only the new hour, where P1 is: it picks P1, not Q (k = 3).", n2_k=3)
    b, q = 450_179_413, c.pool("Q")
    c.trades(q, "reversed", 1_000 * SOL, 10**15, [(b - 100, 0, "small buyer", "buy", SOL, False)])
    _reversed_trigger(c, c.pool("P1"), b, 950 * SOL)  # P1 is added first: it trades first in slot B
    _reversed_trigger(c, c.pool("P2"), b, 1_050 * SOL)
    return c.build()


def clock_cases():
    out = []
    c = Case("clock_1041_1042_creator_window",
             "The creator window follows the clock at t: 201 slots in epoch 1041 (265.83 ms), 200 in 1042 (267.83 ms). CA "
             "triggers at 450143500 (1041) with a creator sell 201 slots before: blocked. CB triggers at 450144300 (1042) "
             "with a creator sell 201 slots before: one F7 row. CC (1042) has its creator sell exactly 200 slots before: "
             "blocked, the window includes its edge.")
    for label, t, back, rq in (("CA", 450_143_500, 201, None), ("CB", 450_144_300, 201, None), ("CC", 450_144_400, 200, 65 * SOL)):
        _curve_trigger(c, c.token(label), t, creator_sells=[t - back], rq=rq)
    out.append(c.build())
    c = Case("clock_1041_1042_mid_hour",
             "At the epoch change 450144000 the hour grid moves: h is 13,542 slots in 1041 (bucket 450136080) and 13,441 in "
             "1042 (bucket 450139090), so the first event slot at or after 450144000 starts a new hour in the middle of "
             "the old one. Q trades at 450143800. P1 triggers at 450144010 as that slot's first key and still sees the "
             "old bucket: its N2 picks Q. P2 triggers at 450144100 and sees only the new bucket: it picks P1, not Q "
             "(k = 3), where with the 1041 clock throughout it would still see Q.", n2_k=3)
    q = c.pool("Q")
    c.trades(q, "reversed", 1_000 * SOL, 10**15, [(450_143_800, 0, "small buyer", "buy", SOL, False)])
    _reversed_trigger(c, c.pool("P1"), 450_144_010, 950 * SOL)
    _reversed_trigger(c, c.pool("P2"), 450_144_100, 1_050 * SOL)
    out.append(c.build())
    return out


def h_gates():
    c = Case("h_gates",
             "Group H's gates, in the engine's order (engine.py:737-742). The feed starts at t0 = 450190000 (O trades), so "
             "farm_window_complete is 1 from t0 + 13,441 on (one hour at epoch 1042). PW triggers at t0 + 5,000: H skips it "
             "with 'farm window incomplete'. PE0 at t0 + 13,440 is incomplete too, PE1 at t0 + 13,441 complete. PF's "
             "and PFW's farmers made more than half of their SOL volume in the hour before: farm 'flagged'. PFW triggers "
             "at t0 + 5,500, its window incomplete: H skips it with 'farm flagged', the gate it checks first. PF (window "
             "complete) is skipped with 'farm flagged' too. PO is organic with a complete window: H passes it. K3 (farm "
             "all) passes every one.")
    t0 = 450_190_000
    c.trades(c.pool("O"), "reversed", 5_000 * SOL, 10**15, [(t0, 0, "small buyer", "buy", SOL, False)])
    _reversed_trigger(c, c.pool("PW"), t0 + 5_000, 300 * SOL)
    farming = [(t0 + 5_300 + i, 0, "farmer", "buy" if i % 2 == 0 else "sell", 2 * SOL, True) for i in range(5)]
    _reversed_trigger(c, c.pool("PFW"), t0 + 5_500, 2_500 * SOL, early=farming)  # no other pool in its N2 band
    _reversed_trigger(c, c.pool("PE0"), t0 + 13_440, 700 * SOL)
    _reversed_trigger(c, c.pool("PE1"), t0 + 13_441, 1_100 * SOL)
    farming = [(t0 + 13_500 + i, 0, "farmer", "buy" if i % 2 == 0 else "sell", 2 * SOL, True) for i in range(5)]
    _reversed_trigger(c, c.pool("PF"), t0 + 13_600, 200 * SOL, early=farming)
    _reversed_trigger(c, c.pool("PO"), t0 + 13_700, 450 * SOL)
    return c.build()


def bounds_and_age():
    dlo, dhi = decision_bounds(None, *DEV2_BOUNDS, Clock(DEV2_CLOCK))
    assert (dlo, dhi) == (446_529_755, 452_284_004)
    c = Case("bounds_and_age",
             f"Decisions only in [dlo, dhi] = [{dlo}, {dhi}] (dev2's bounds, both ends included, engine.py:612): pool "
             "triggers at dlo - 1 and dhi + 1 give no row, at dlo and dhi one. A curve must be created in [burn, dev hi) "
             "with burn = dlo: A_burn (created at burn) signals, A_before (created at burn - 1) does not.")
    for label, t, depth in (("P_dlo_out", dlo - 1, 200), ("P_dlo", dlo, 400), ("P_dhi", dhi, 800), ("P_dhi_out", dhi + 1, 1_600)):
        _reversed_trigger(c, c.pool(label, create_slot=446_400_000), t, depth * SOL)
    _curve_trigger(c, c.token("A_before", create_slot=dlo - 1), dlo + 300, rq=30 * SOL)
    _curve_trigger(c, c.token("A_burn", create_slot=dlo), dlo + 400, rq=55 * SOL)
    return c.build()


def read_t1(store=STORE, old=None):
    """T1's token row and its 13 legs as events, from dev2 (spec appendix); without the store, from the file `old`."""
    if not os.path.exists(store):
        if old is None:
            raise SystemExit(f"no dev2 store at {store} and no {PATH} to keep T1's legs from")
        case = next(c for c in old["cases"] if c["name"] == "t1_as_stored")
        print(f"no dev2 store at {store}: T1's legs kept from {PATH}", file=sys.stderr)
        return case["tokens"][0], case["events"]
    con = streams.open_store(store, threads=2, memory="1GB")
    try:
        meta = json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
        bounds = con.execute("SELECT min(slot), max(slot) + 1 FROM blocks").fetchone()
        if meta.get("ms_per_slot") != DEV2_CLOCK or tuple(bounds) != DEV2_BOUNDS:
            raise SystemExit(f"{store} is not dev2 as the cases know it: {meta.get('ms_per_slot')}, {bounds}")
        mint, create, creator, mayhem, quote = con.execute(
            "SELECT mint, create_slot, creator, mayhem, quote_mint FROM mints WHERE mint = ?", [T1]).fetchone()
        rows = con.execute(f"""SELECT {streams.CURVE_COLS} FROM curve WHERE mint = ? AND slot BETWEEN 450196400 AND 450197213
                               ORDER BY slot, tx_index, outer_ix, inner_ix""", [T1]).fetchall()
        bt = dict(con.execute("SELECT slot, block_time FROM blocks WHERE slot BETWEEN 450196400 AND 450197213").fetchall())
    finally:
        con.close()
    legs = streams._curve_legs(rows)
    assert len(legs) == 13 and (create, quote) == (450_196_501, PUMP_QUOTE)
    tok = {"mint": mint, "create_slot": create, "creator": creator, "mayhem": mayhem, "quote_mint": quote}
    return tok, [fields(Event(l.slot, l.tx, l.order[2], l.order[3], bt[l.slot], "curve", T1, l)) for l in legs]


def cases(t1):
    return [*t1_cases(t1), *t2_cases(), pool_mint_absent(), *n2_cases(), n2_band_edges_above_2_53(), n2_curve_universe(),
            n2_collision(), hour_rollover_order(), *clock_cases(), h_gates(), bounds_and_age()]


def check_ints(x, where="cases"):
    """Every integer of the file fits i64 (FORMAT integers); bools are not integers here."""
    if isinstance(x, dict):
        for k, v in x.items():
            check_ints(v, f"{where}.{k}")
    elif isinstance(x, list):
        for i, v in enumerate(x):
            check_ints(v, f"{where}[{i}]")
    elif isinstance(x, int) and not isinstance(x, bool) and not -2**63 <= x < 2**63:
        raise ValueError(f"{where}: {x} does not fit i64")


def dumps(doc):
    """The file's text: JSON with indent 1, but every token, pool, event, expected row and state item on one line, so a
    diff shows whole legs and rows."""
    flat = {}

    def line(x):
        flat[f'"@{len(flat)}@"'] = json.dumps(x)
        return f"@{len(flat) - 1}@"

    cases = [{**c, "tokens": [line(t) for t in c["tokens"]], "pools": [line(p) for p in c["pools"]],
              "events": [line(e) for e in c["events"]], "expect": [line(r) for r in c["expect"]],
              "expect_state": {**c["expect_state"],
                               "hour_events": {k: [line(x) for x in v] for k, v in c["expect_state"]["hour_events"].items()},
                               "farm_win": {k: [line(x) for x in v] for k, v in c["expect_state"]["farm_win"].items()},
                               "pool_seen": {k: line(v) for k, v in c["expect_state"]["pool_seen"].items()}}}
             for c in doc["cases"]]
    return re.sub(r'"@\d+@"', lambda m: flat[m.group(0)], json.dumps({**doc, "cases": cases}, indent=1)) + "\n"


def generate(path=PATH, store=STORE):
    """Build every case, run the engine on it, and write the file with its expectations."""
    old = None
    if os.path.exists(path):
        with open(path) as f:
            old = json.load(f)
    out = []
    for case in cases(read_t1(store, old)):
        with tempfile.TemporaryDirectory(prefix="c1b-case-") as d:  # the engine's own groups text
            j = Journal(d)
            case["groups"] = groups_state(engine(case, j))
            j.close()
        case["expect"], case["expect_state"] = run(case)
        out.append({k: case[k] for k in CASE})
    doc = {"format": FORMAT, "spec": "research/m1-c1b-signals-spec-2026-10-19.md §6.2 (lab/paper/tests/c1b_signal_cases.py)",
           "cases": out}
    check_ints(doc)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = dumps(doc)
    assert json.loads(text) == doc
    with open(path + ".tmp", "w") as f:
        f.write(text)
    os.replace(path + ".tmp", path)
    return doc


if __name__ == "__main__":
    d = generate()
    print(f"{len(d['cases'])} cases, {sum(len(c['expect']) for c in d['cases'])} rows: {PATH}")
