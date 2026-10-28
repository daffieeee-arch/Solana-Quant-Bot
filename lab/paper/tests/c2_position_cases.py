"""The golden cases of M1 C2 (research/m1-c2-positionsim-spec-2026-10-19.md §6.2): small feeds that the real paper engine
(paper/engine.py, unchanged) runs with risk inert, and whose journal writes the Rust route must reproduce one by one:
every put of signals, orders, fills, exits and positions and every positions delete, in the engine's order.
test_position_cases.py asserts that the engine still writes exactly the stored ones; lab/live's paper test reads the
same file.

  C2_CASES_REGEN=1 pytest paper/tests/test_position_cases.py      # rewrite data/c2_position_cases.json from the engine
  python -m paper.tests.c2_position_cases                          # the same

The cases are built as C1b's are (c1b_signal_cases.Case: venue math from a start state, so every leg's chain holds), with
two more token fields for the graduations, complete_slot and pool. Each case asserts what it is for on the engine's own
writes, so a case that stops showing its edge fails here.
"""

import json
import os
import re
import sys
import tempfile
from dataclasses import asdict, replace

from backtest.replay import CurveLeg, PoolLeg
from backtest.strategies import Clock
from backtest.venues import Pool, curve_buy_exact_in, pool_buy_exact_in, pool_withdraw
from paper.feed import Event
from paper.engine import _day
from paper.journal import Journal
from paper.tests import c1b_signal_cases as C1

SOL = C1.SOL
T = C1.T
FIVE = ("signals", "orders", "fills", "exits", "positions")
PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "c2_position_cases.json")
CASE = (*C1.CASE[:-2], "cost_scenario", "expect_ops")
H0 = 450_152_531  # an hour bucket's start at epoch 1042 (13,441 slots an hour)
HOUR = 13_441
MIDNIGHT = 1_790_035_200  # 2026-09-22 00:00 UTC

FORMAT = {
    "file": "{format, spec, cases: [case]}; every case runs on its own, from an empty engine, with risk inert (ignore_risk).",
    "case": "name, why, clock, dev_bounds, groups, labels, tokens, pools, events (as in c1b_signal_cases.json FORMAT), "
            "cost_scenario, expect_ops.",
    "tokens": "the mints table as c1b's, plus complete_slot (the token's historical completion, null: none) and pool (its "
              "PumpSwap pool, null: none), which the engine's graduation reads (engine.py:977-992).",
    "cost_scenario": "the journal's state 'cost_scenario' as paper.engine main writes it (json.dumps(asdict(scn), "
                     "sort_keys=True), engine.py:1494): the base scenario of week2-defaults.yaml. Parse it with "
                     "float_roundtrip.",
    "expect_ops": "every journal write of the five tables in the engine's order: [\"put\", table, row] with the table's columns "
                  "(journal.py TABLES; null where the engine writes none) or [\"delete\", \"positions\", key]. The final "
                  "positions table is empty.",
    "integers": "every integer fits i64; ret is a float, parse it with float_roundtrip.",
    "regenerate": "C2_CASES_REGEN=1 pytest paper/tests/test_position_cases.py (or python -m paper.tests.c2_position_cases).",
}


class RecJournal(Journal):
    """The journal, recording every write of the five tables."""

    def __init__(self, directory):
        super().__init__(directory)
        self.ops = []

    def put(self, table, **row):
        if table in FIVE:
            # Keep the historical golden format and numbers. Validate the omitted metadata rather than silently
            # projecting a current-rule result into the old rule-1 corpus.
            if table == "exits":
                assert row.get("settle_rule") == 1
            self.ops.append(["put", table, {c: row.get(c) for c in self.cols[table] if c != "settle_rule"}])
        super().put(table, **row)

    def delete(self, table, **key):
        if table in FIVE:
            self.ops.append(["delete", table, dict(key)])
        super().delete(table, **key)


def run(case):
    """The engine's journal writes of a case, and the engine after the run."""
    with tempfile.TemporaryDirectory(prefix="c2-case-") as d:
        j = RecJournal(d)
        eng = C1.engine(case, j)
        eng.run()
        ops = j.ops
        j.close()
    return ops, eng


class Case(C1.Case):
    """C1b's case builder, with the graduation fields of a token and a few legs C1b does not build."""

    def token(self, label, create_slot=450_100_000, creator="creator", mayhem=False, quote_mint=C1.DEFAULT_KEY,
              complete_slot=None, pool=None):
        mint = super().token(label, create_slot, creator, mayhem, quote_mint)
        self.tokens[-1].update(complete_slot=complete_slot, pool=pool)
        return mint

    def more(self, pool, orientation, last, trades):
        """Pool trades continuing from the last leg's state."""
        return self.trades(pool, orientation, last.post.b, last.post.e, trades)

    def withdraw(self, pool, last, slot, tx, lp, supply, trader="lp"):
        """A withdraw of lp of supply LP tokens from the last leg's state."""
        s = last.post
        post = pool_withdraw(s, lp, supply)
        tx = self.tx(pool, tx)
        leg = PoolLeg(slot, tx, (slot, tx, 0, 0), "withdraw", False, s.b - post.b, s.real * lp // supply, s, post,
                      self.key(trader), direct=True, lp_amount=lp, lp_supply=supply)
        self.evs.append(Event(slot, tx, 0, 0, C1.block_time(slot), "pool", pool, leg))
        return leg

    def buy_in(self, mint, start, slot, tx, trader, sol):
        """A curve buy spending exactly `sol` (buy_exact_sol_in, its own budget, min_tokens 0): re-run after ours it gets
        fewer tokens, where C1b's exact-out buys get the same."""
        tx = self.tx(mint, tx)
        f = curve_buy_exact_in(start, sol)
        leg = CurveLeg(slot, tx, (slot, tx, 0, 0), True, f.tokens, f.curve_quote, f.fees, "buy_exact_sol_in", start, f.state,
                       self.key(trader), sol, None, 0, None, True)
        self.evs.append(Event(slot, tx, 0, 0, C1.block_time(slot), "curve", mint, leg))
        return leg

    def buys_from(self, pool, start, trades):
        """Token buys (slot, tx, trader, SOL) in a normal pool from any start state (virtual quote included), each spending
        exactly that SOL with it as its limit."""
        s, legs = start, []
        for slot, tx, trader, sol in trades:
            tx = self.tx(pool, tx)
            f = pool_buy_exact_in(s, sol)
            legs.append(PoolLeg(slot, tx, (slot, tx, 0, 0), "buy", False, f.tokens, f.trader_quote, s, f.state, self.key(trader),
                                limit_quote=sol, direct=True))
            s = f.state
        self.evs += [Event(l.slot, l.tx, 0, 0, C1.block_time(l.slot), "pool", pool, l) for l in legs]
        return legs

    def undate(self, upto):
        """Events at or before slot `upto` without a block time (a store slot with none)."""
        self.evs = [replace(e, block_time=None) if e.slot <= upto else e for e in self.evs]

    def build(self):
        case = super().build()
        with tempfile.TemporaryDirectory(prefix="c2-case-") as d:  # the engine's own groups and cost scenario text
            j = Journal(d)
            eng = C1.engine(case, j)
            case["groups"] = C1.groups_state(eng)
            case["cost_scenario"] = json.dumps(asdict(eng.scn), sort_keys=True)
            j.close()
        case["expect_ops"], self.eng = run(case)
        self.ops = case["expect_ops"]
        return case

    # -- what the engine wrote

    def rows(self, table, **match):
        """The table's rows after the run (puts replaced and deleted as the journal does), matching the given columns."""
        key = {"signals": 4, "orders": 4, "fills": 5, "exits": 4, "positions": 4}[table]
        out = {}
        for op, t, row in self.ops:
            if t != table:
                continue
            k = tuple(row.values())[:key]
            if op == "put":
                out[k] = row
            else:
                out.pop(k, None)
        return [r for r in out.values() if all(r[c] == v for c, v in match.items())]

    def one(self, table, **match):
        rs = self.rows(table, **match)
        assert len(rs) == 1, (self.name, table, match, rs)
        return rs[0]


def ok(cond, case, what):
    if not cond:
        raise AssertionError(f"{case.name}: {what}")


def reversed_trigger(c, pool, t, depth, **kw):
    return C1._reversed_trigger(c, pool, t, depth, **kw)


# ---------------------------------------------------------------------------------------------- the cases

def k3_pool_exits():
    """tp4_T600 in reversed pools: take_profit, stop_loss and time (the deadline in a quiet gap, advanced by another
    key's events), each K3 position's rows from signal to exit."""
    c = Case("k3_pool_exits", "K3 (tp4_T600, d 2 slots at epoch 1042, cap 599) in three reversed pools. TP: buys after the "
                              "entry lift the price, take_profit, sold 2 slots after the decision. SL: sells take it down, "
                              "stop_loss. TM: no event after the entry; another pool's events advance the clock past the "
                              "deadline (entry + 599), so the time exit is decided in the quiet gap (advance).", n2_k=0)
    t = 450_160_000
    tp, sl, tm, other = c.pool("TP"), c.pool("SL"), c.pool("TM"), c.pool("O")
    legs = reversed_trigger(c, tp, t, 400 * SOL)
    c.more(tp, "reversed", legs[-1], [(t + 10, 0, "buyer 3", "buy", 30 * SOL, False), (t + 11, 0, "buyer 4", "buy", 30 * SOL, False)])
    legs = reversed_trigger(c, sl, t + 100, 400 * SOL)
    c.more(sl, "reversed", legs[-1], [(t + 110, 0, "seller 2", "sell", 20 * SOL, False), (t + 111, 0, "seller 3", "sell", 20 * SOL, False)])
    reversed_trigger(c, tm, t + 200, 400 * SOL)
    c.trades(other, "reversed", 5_000 * SOL, 10**15, [(t + 900, 0, "small buyer", "buy", SOL, False),
                                                       (t + 1_000, 0, "small buyer", "buy", SOL, False)])
    case = c.build()
    ok(c.one("exits", key=tp)["exit_reason"] == "take_profit", c, "TP")
    ok(c.one("exits", key=sl)["exit_reason"] == "stop_loss", c, "SL")
    x = c.one("exits", key=tm)
    ok(x["exit_reason"] == "time" and x["exit_slot"] == t + 200 + 2 + 599 + 2, c, f"TM {x}")
    return case


def busy_until_the_exit_slot():
    """busy holds through the exit slot (engine.py:744, 1058): the position exits inside slot X at our insertion point,
    and P's new trigger in X is skipped busy (t <= exit_slot)."""
    c = Case("busy_until_the_exit_slot", "K3 on P (reversed, 400 SOL): a 40 SOL token buy at t + 30 makes the decision at "
                                         "P's next event take_profit, pending t + 32. Slot t + 32 has a small buy (tx 0) and a "
                                         "new trigger sell (tx 1): the exit fills at q 0.5, between them, so exit_slot is "
                                         "t + 32, and the trigger's K3 row in that slot is busy.", n2_k=0)
    t = 450_160_000
    p = c.pool("P")
    legs = reversed_trigger(c, p, t, 400 * SOL)
    c.more(p, "reversed", legs[-1], [(t + 30, 0, "buyer 3", "buy", 40 * SOL, False), (t + 32, 0, "buyer 4", "buy", SOL // 10, False),
                                     (t + 32, 1, "seller 2", "sell", 20 * SOL, False)])
    case = c.build()
    x = c.one("exits", key=p, t=t)
    ok(x["exit_slot"] == t + 32 and x["exit_reason"] == "take_profit", c, f"exit {x}")
    r = c.one("signals", key=p, t=t + 32, variant="tp4_T600")
    ok((r["status"], r["reason"]) == ("skipped", "busy"), c, f"row {r}")
    return case


def n2_overwrite():
    """C1b's n2_collision with C2's statuses: P1's N2 row enters Q, P2's row for Q in the same slot finds it busy and
    overwrites it (INSERT OR REPLACE); the one Pending stays and opens Q's N2 position."""
    c = Case("n2_overwrite", "Two triggers in one slot pick the same N2 key Q (C1b's n2_collision): the first row enters Q, "
                             "the second is skipped busy and overwrites it; Q's N2 position is the first row's.")
    t, q = 450_185_000, c.pool("Q")
    c.trades(q, "reversed", 1_000 * SOL, 10**15, [(t - 200, 0, "small buyer", "buy", SOL, False)])
    farming = [(t - 41, 0, "farmer", "buy", 2 * SOL, True), (t - 36, 0, "farmer", "sell", 2 * SOL, True),
               (t - 31, 0, "farmer", "buy", 2 * SOL, True)]
    reversed_trigger(c, c.pool("P1"), t, 850 * SOL, inflow=SOL, early=farming)
    reversed_trigger(c, c.pool("P2"), t, 1_180 * SOL)
    case = c.build()
    r = c.one("signals", family="N2_F7", key=q)
    ok((r["status"], r["reason"]) == ("skipped", "busy"), c, f"Q's row {r}")
    o = c.one("orders", family="N2_F7", key=q)
    ok(o["status"] == "filled", c, f"Q's order {o}")
    puts = [w for w in c.ops if w[0] == "put" and w[1] == "signals" and w[2]["key"] == q]
    ok([w[2]["status"] for w in puts] == ["entered", "skipped"], c, "Q's two writes")
    return case


def quiet_entry_across_midnight():
    """An entry in a slot without events is made before the next event slot's clock (engine.py:310), so its order
    carries the day of the last dated slot, the day before midnight."""
    c = Case("quiet_entry_across_midnight", "P triggers at 450131834 (2026-09-21 23:59:59 UTC by the case's block times; "
                                            "epoch 1041, d 2). Its entry slot 450131836 has no event: the entry comes before "
                                            "the next event slot 450131900 (2026-09-22) is clocked, so the order's day is "
                                            "2026-09-21; the exit at the end of data is on 2026-09-22.", n2_k=0)
    t = 450_131_834
    assert _day(C1.block_time(t)) == "2026-09-21" and _day(C1.block_time(t + 66)) == "2026-09-22"
    reversed_trigger(c, c.pool("P"), t, 400 * SOL)
    c.trades(c.pool("O"), "reversed", 5_000 * SOL, 10**15, [(t + 66, 0, "small buyer", "buy", SOL, False)])
    case = c.build()
    o = c.one("orders", key=c.key("P"))
    ok((o["entry_slot"], o["day"]) == (t + 2, "2026-09-21"), c, f"order {o}")
    ok(c.one("exits", key=c.key("P"))["day"] == "2026-09-22", c, "the exit's day")
    return case


def n2_pool_and_curve_entries():
    """N2 positions (k = 1): a reversed pool key and a curve key, each entered quietly on its last state (the N2 key has
    no event at the entry) and closed at the end of data. The curve's entry state and seed come from the engine's own
    legs (spec D4)."""
    c = Case("n2_pool_and_curve_entries", "P (reversed, 1,000 SOL) picks Q1 (950 SOL, one trade this hour); CT (a curve "
                                          "trigger) picks C1 (a curve 2 SOL deeper with two holders). Both N2 entries are quiet "
                                          "and fill on the key's last state; the end of data closes them.", n2_k=1)
    t = 450_170_000
    c.trades(c.pool("Q1"), "reversed", 950 * SOL, 10**15, [(t - 500, 0, "small buyer", "buy", SOL, False)])
    reversed_trigger(c, c.pool("P"), t, 1_000 * SOL)
    ct = c.token("CT")
    legs = C1._curve_trigger(c, ct, t + 1_000)
    depth = legs[-1].post.rq + legs[-1].q
    c1 = c.token("C1")
    c.curve(c1, [(t + 600, 0, "holder 1", "buy", 2 * T), (t + 610, 0, "holder 2", "buy", T)], rq=depth + 2 * SOL)
    case = c.build()
    for key in (c.key("Q1"), c1):
        o = c.one("orders", family="N2_F7", key=key)
        ok(o["status"] == "filled", c, f"N2 order {o}")
    return case


def not_loaded_before_the_first_dated_slot():
    """'not loaded' (engine.py:151, 471, 827): the risk loads at the first slot with a block time, so a trigger before it
    is skipped for every group, N2 rows included; a later trigger enters."""
    c = Case("not_loaded_before_the_first_dated_slot", "The store's first slots have no block time (up to t + 1): P's trigger "
                                                       "at t is skipped 'not loaded' (K3, and its N2 row for Q); P2's trigger "
                                                       "at t + 500, after the first dated slot, enters.", n2_k=1)
    t = 450_175_000
    c.trades(c.pool("Q"), "reversed", 1_000 * SOL, 10**15, [(t - 300, 0, "small buyer", "buy", SOL, False)])
    reversed_trigger(c, c.pool("P"), t, 1_050 * SOL)
    reversed_trigger(c, c.pool("P2"), t + 500, 600 * SOL)
    c.undate(t + 1)
    case = c.build()
    for key, fam in ((c.key("P"), "F7"), (c.key("Q"), "N2_F7")):
        r = c.one("signals", key=key, family=fam, variant="tp4_T600")
        ok((r["status"], r["reason"]) == ("skipped", "not loaded"), c, f"{fam} row {r}")
    ok(c.one("orders", key=c.key("P2"))["status"] == "filled", c, "P2")
    return case


def curve_k3_exits():
    """tp4_T600 on curves: take_profit, stop_loss, and time decided by end() at the future deadline, filled after the
    data's last slot (spec §3.6)."""
    c = Case("curve_k3_exits", "Three curve triggers (C1b's _curve_trigger). CA: a 40T buy after the entry, take_profit. CB: "
                               "a 40T sell, stop_loss. CC: no event after its entry; the data ends first, so end() decides "
                               "the deadline (entry + 599) and the exit fills 2 slots after it, past the last slot.", n2_k=0)
    t = 450_160_000
    ca, cb, cc = c.token("CA"), c.token("CB"), c.token("CC")
    legs = C1._curve_trigger(c, ca, t)
    c.curve(ca, [(t + 10, 0, "buyer 3", "buy", 40 * T)], start=legs[-1].post)
    legs = C1._curve_trigger(c, cb, t + 100)
    c.curve(cb, [(t + 110, 0, "seller 2", "sell", 40 * T)], start=legs[-1].post)
    C1._curve_trigger(c, cc, t + 200)
    case = c.build()
    ok(c.one("exits", key=ca)["exit_reason"] == "take_profit", c, "CA")
    ok(c.one("exits", key=cb)["exit_reason"] == "stop_loss", c, "CB")
    x = c.one("exits", key=cc)
    ok((x["exit_reason"], x["exit_decision_slot"], x["exit_slot"]) == ("time", t + 202 + 599, t + 202 + 601), c, f"CC {x}")
    return case


def curve_entry_failures():
    """The curve entry's skips and its failure (replay.py:633-664): entry_slippage (a big buy before our insertion),
    no_curve_state (the curve completed in the signal slot), curve_complete (it completed between signal and entry)."""
    c = Case("curve_entry_failures", "CS: the entry slot's first tx buys 60T before us (q 0.5 of two txs): our fill is far "
                                     "below the expected one, entry_slippage (exits row, pnl -failed_tx). CN: the signal slot "
                                     "ends with a buy of every token left, so the state after it is complete: no_curve_state. "
                                     "CK: the curve completes at t + 1, before the entry: curve_complete.", n2_k=0)
    t = 450_160_000
    cs, cn, ck = c.token("CS"), c.token("CN", complete_slot=t + 100), c.token("CK", complete_slot=t + 201)
    legs = C1._curve_trigger(c, cs, t)
    c.curve(cs, [(t + 2, 0, "whale", "buy", 60 * T), (t + 2, 1, "buyer 3", "buy", T)], start=legs[-1].post)
    legs = C1._curve_trigger(c, cn, t + 100)
    c.curve(cn, [(t + 100, 1, "whale", "buy", legs[-1].post.rt)], start=legs[-1].post)
    legs = C1._curve_trigger(c, ck, t + 200)
    c.curve(ck, [(t + 201, 0, "whale", "buy", legs[-1].post.rt)], start=legs[-1].post)
    case = c.build()
    o = c.one("orders", key=cs)
    ok((o["status"], o["reason"]) == ("entry_failed", "entry_slippage"), c, f"CS {o}")
    x = c.one("exits", key=cs)
    ok((x["pnl"], x["exit_slot"], x["ret"], x["tokens"]) == (-505_000, None, None, 0), c, f"CS exit {x}")
    ok((c.one("orders", key=cn)["reason"], c.one("orders", key=ck)["reason"]) == ("no_curve_state", "curve_complete"), c, "CN, CK")
    ok(not c.rows("exits", key=cn) and not c.rows("fills", key=ck), c, "skips write no exit or fill")
    return case


def nearly_complete(c, mint, t, left=10**9):
    """A curve trigger at t whose signal slot ends with a buy that leaves `left` tokens: our 0.5 SOL buy at the entry
    takes them all and completes the curve (the expected fill is the same, so no slippage)."""
    legs = C1._curve_trigger(c, mint, t)
    return c.curve(mint, [(t, 1, "whale", "buy", legs[-1].post.rt - left)], start=legs[-1].post)


def our_buy_completes():
    """Our own buy completes the curve (replay.py:666-669; engine.py:942-954, 977-992): with no pool the sale is in the
    standard seed pool at once (uncapped pool_sell at slot + 1, one attempt). cf_graduation is 1 even where the token
    completed at that slot historically: open_curve_position sets it before on_complete looks. With a pool the
    position waits for open + 1 and sells there with pool_sell_capped at the primary group's q."""
    c = Case("our_buy_completes", "G1 (no pool, never completed): our buy completes the curve at the entry; the sale goes to the "
                                  "seed pool at entry + 1, cf_graduation 1. G2: the same, with the token completed at the entry "
                                  "slot historically: still cf_graduation 1 (set by the fill). G3: the token's pool opens at "
                                  "entry + 10 and trades "
                                  "at its open + 1 in two txs: the sale fills there after the first (q 0.5 of the primary "
                                  "group). G4: its pool opens at entry + 10 and next trades at entry + 30: the sale on the "
                                  "pool's last state, exit_slot open + 1 still. G5: its pool opens at entry + 10 but never "
                                  "trades: no state, proceeds 0, the attempt counts. G6: its pool opens after the data: at the "
                                  "end of data the sale goes to the seed pool at the last slot + 1 (no decision slot).", n2_k=0)
    t = 450_160_000
    g1, g2 = c.token("G1"), c.token("G2", complete_slot=t + 102)
    p3, p4 = c.pool("PG3", "normal", create_slot=t + 212), c.pool("PG4", "normal", create_slot=t + 312)
    p5, p6 = c.pool("PG5", "normal", create_slot=t + 412), c.pool("PG6", "normal", create_slot=t + 10**6)
    g3, g4, g5, g6 = c.token("G3", pool=p3), c.token("G4", pool=p4), c.token("G5", pool=p5), c.token("G6", pool=p6)
    for mint, at in ((g1, t), (g2, t + 100), (g3, t + 200), (g4, t + 300), (g5, t + 400), (g6, t + 500)):
        nearly_complete(c, mint, at)
    for pool, mint in ((p3, g3), (p4, g4), (p5, g5), (p6, g6)):
        c.pools[[x["pool"] for x in c.pools].index(pool)]["mint"] = mint
    c.trades(p3, "normal", 10**15, 80 * SOL, [(t + 212, 0, "first buyer", "buy", SOL, False), (t + 213, 0, "buyer", "buy", 5 * SOL, False),
                                             (t + 213, 1, "buyer 2", "buy", 5 * SOL, False)])
    c.trades(p4, "normal", 10**15, 80 * SOL, [(t + 312, 0, "first buyer", "buy", SOL, False), (t + 330, 0, "buyer", "buy", 5 * SOL, False)])
    c.trades(c.pool("O"), "reversed", 5_000 * SOL, 10**15, [(t + 450, 0, "small buyer", "buy", SOL, False),
                                                           (t + 600, 0, "small buyer", "buy", SOL, False)])
    case = c.build()
    for mint, cf in ((g1, 1), (g2, 1)):
        x = c.one("exits", key=mint)
        ok((x["exit_reason"], x["seed_pool"], x["cf_graduation"], x["exit_attempts"]) == ("graduation", 1, cf, 1), c, f"{mint} {x}")
        ok(x["exit_slot"] == x["entry_slot"] + 1 == x["exit_decision_slot"] + 1, c, f"seed sale slots {x}")
    for mint, pool in ((g3, p3), (g4, p4)):
        x = c.one("exits", key=mint)
        open_slot = next(p["create_slot"] for p in c.pools if p["pool"] == pool)
        ok((x["seed_pool"], x["exit_decision_slot"], x["exit_slot"], x["cf_graduation"]) == (0, open_slot, open_slot + 1, 1), c, f"{x}")
    x = c.one("exits", key=g5)
    ok((x["exit_slot"], x["proceeds"], x["vault_capped"], x["exit_attempts"], x["seed_pool"]) == (t + 413, 0, 0, 1, 0), c, f"G5 {x}")
    x = c.one("exits", key=g6)
    ok((x["exit_slot"], x["exit_decision_slot"], x["seed_pool"], x["exit_attempts"]) == (t + 601, None, 1, 1), c, f"G6 {x}")
    return case


def completion_during_the_hold():
    """A re-run buy completes the curve while we hold it (replay.py:510-511): Completed at that slot, then the engine's
    on_complete: H1 has no pool (seed pool at slot + 1), H2's pool opens later (the sale at its open + 1)."""
    c = Case("completion_during_the_hold", "H1, H2, H3: after our entry another trader's buy took all but 1T tokens; re-run "
                                           "after ours it takes what is left and completes the curve. H1 sells into the seed "
                                           "pool at that slot + 1 (cf_graduation 1); H2 waits for its pool, open at t + 150, "
                                           "and sells there at open + 1; H3 completed at that very slot historically, so "
                                           "cf_graduation is 0 (slot < complete_slot fails).", n2_k=0)
    t = 450_160_000
    p2 = c.pool("PH2", "normal", create_slot=t + 150)
    h1, h2, h3 = c.token("H1"), c.token("H2", pool=p2), c.token("H3", complete_slot=t + 210)
    c.pools[0]["mint"] = h2
    for mint, at in ((h1, t), (h2, t + 100), (h3, t + 200)):
        legs = C1._curve_trigger(c, mint, at)
        c.curve(mint, [(at + 10, 0, "whale", "buy", legs[-1].post.rt - T)], start=legs[-1].post)
    c.trades(p2, "normal", 10**15, 80 * SOL, [(t + 150, 0, "first buyer", "buy", SOL, False), (t + 151, 0, "buyer", "buy", SOL, False)])
    case = c.build()
    x = c.one("exits", key=h1)
    ok((x["exit_reason"], x["seed_pool"], x["exit_slot"], x["cf_graduation"]) == ("graduation", 1, t + 11, 1), c, f"H1 {x}")
    x = c.one("exits", key=h2)
    ok((x["exit_reason"], x["seed_pool"], x["exit_decision_slot"], x["exit_slot"]) == ("graduation", 0, t + 150, t + 151), c, f"H2 {x}")
    x = c.one("exits", key=h3)
    ok((x["exit_slot"], x["seed_pool"], x["cf_graduation"]) == (t + 211, 1, 0), c, f"H3 {x}")
    return case


def graduation_kept_through_the_sweep():
    """A graduation waiting for its pool keeps the pool's history through the hourly sweep (engine.py:677-678): the pool
    trades at its open and then nothing happens for over an hour; the next slot's rollover sweeps every quiet key but
    this pool, and step 5 sells on its last state."""
    c = Case("graduation_kept_through_the_sweep", "G (no history of completion) completes on our buy at the entry; its pool "
                                                  "opens at t + 5,000 (one trade) and nothing trades until O at t + 20,000, "
                                                  "the next hour: the rollover there sweeps the quiet keys but not the pool a "
                                                  "graduation waits for, so the sale (open + 1) fills on its last state.", n2_k=0)
    t = 450_160_000
    p = c.pool("PG", "normal", create_slot=t + 5_000)
    g = c.token("G", pool=p)
    c.pools[0]["mint"] = g
    nearly_complete(c, g, t)
    c.trades(p, "normal", 10**15, 80 * SOL, [(t + 5_000, 0, "first buyer", "buy", SOL, False)])
    c.trades(c.pool("O"), "reversed", 5_000 * SOL, 10**15, [(t + 20_000, 0, "small buyer", "buy", SOL, False)])
    case = c.build()
    x = c.one("exits", key=g)
    ok((x["exit_slot"], x["seed_pool"]) == (t + 5_001, 0) and x["proceeds"] > 0, c, f"G {x}")
    return case


def h_flow_exits():
    """Group H's flow variants (flow300_H6, flow300_tp100_H6; reversed pools, organic, a complete farm window): outflow,
    time and take_profit, with the pool's flow index fed as the engine feeds it (engine.py:379-380, 906-907)."""
    c = Case("h_flow_exits", "The feed starts an hour before the triggers (O trades at t0), so H's farm window is complete. At "
                             "epoch 1042 the flow window is 1,120 slots and the cap 80,648. HO: a token sell 1,298 slots after "
                             "the entry; end() decides on it: outflow (both H variants). HT: a token buy 80,000 slots after the "
                             "entry keeps the flow positive at the last slot and at the deadline: time. HP: a 400 SOL token "
                             "buy doubles the price: flow300_tp100_H6 takes profit, flow300_H6 (no tp) holds until end() "
                             "decides the deadline, where the window is empty: outflow. K3 trades all three too.", n2_k=0)
    t0, t = 450_150_000, 450_165_000
    c.trades(c.pool("O"), "reversed", 5_000 * SOL, 10**15, [(t0, 0, "small buyer", "buy", SOL, False)])
    ho, ht, hp = c.pool("HO"), c.pool("HT"), c.pool("HP")
    legs = reversed_trigger(c, ho, t, 400 * SOL)
    c.more(ho, "reversed", legs[-1], [(t + 1_300, 0, "seller 2", "sell", 5 * SOL, False)])
    legs = reversed_trigger(c, ht, t + 100, 400 * SOL)
    c.more(ht, "reversed", legs[-1], [(t + 102 + 80_000, 0, "buyer 3", "buy", SOL, False)])
    legs = reversed_trigger(c, hp, t + 200, 400 * SOL)
    c.more(hp, "reversed", legs[-1], [(t + 210, 0, "whale", "buy", 400 * SOL, False)])
    case = c.build()
    for v in ("flow300_H6", "flow300_tp100_H6"):
        x = c.one("exits", key=ho, variant=v)
        ok((x["exit_reason"], x["exit_decision_slot"], x["n_flow_slots"], x["cap_slots"]) == ("outflow", t + 1_300, 1_120, 80_648), c, f"HO {v} {x}")
    x = c.one("exits", key=ht, variant="flow300_H6")
    ok((x["exit_reason"], x["exit_slot"]) == ("time", t + 102 + 80_648 + 2), c, f"HT {x}")
    ok(c.one("exits", key=hp, variant="flow300_tp100_H6")["exit_reason"] == "take_profit", c, "HP tp100")
    x = c.one("exits", key=hp, variant="flow300_H6")
    ok((x["exit_reason"], x["exit_decision_slot"]) == ("outflow", t + 202 + 80_648), c, f"HP {x}")
    return case


def exits_that_fail():
    """A failed exit and its retry (replay.py:418-428): the sale at the pending slot is below the decision's mark less 5 %
    and is tried again at a later decision; a drained pool where every sale fails: exit_slot NULL (replay.py:541-549);
    and a pool entry's slippage."""
    c = Case("exits_that_fail", "FR: take_profit decided at t + 30; at t + 32 a 60 SOL token sell comes before our insertion "
                                "(q 0.5 of two txs), so the sale misses the minimum and is tried again at a later decision "
                                "(exit_attempts 2). NX: all liquidity is withdrawn at t + 105; every sale fails: the stop_loss "
                                "decided in a quiet gap misses (attempt 1), end() decides the deadline and misses again "
                                "(attempt 2), so exit_slot is NULL and proceeds 0. ES: a 100 SOL token buy before our insertion in the "
                                "entry slot: entry_slippage.", n2_k=0)
    t = 450_160_000
    fr, nx, es = c.pool("FR"), c.pool("NX"), c.pool("ES")
    legs = reversed_trigger(c, fr, t, 400 * SOL)
    c.more(fr, "reversed", legs[-1], [(t + 30, 0, "buyer 3", "buy", 40 * SOL, False), (t + 32, 0, "seller 2", "sell", 60 * SOL, False),
                                      (t + 32, 1, "buyer 4", "buy", SOL // 10, False), (t + 40, 0, "buyer 5", "buy", 50 * SOL, False),
                                      (t + 41, 0, "buyer 6", "buy", SOL // 10, False)])
    legs = reversed_trigger(c, nx, t + 100, 400 * SOL)
    c.withdraw(nx, legs[-1], t + 105, 0, 1_000_000, 1_000_000)
    legs = reversed_trigger(c, es, t + 200, 400 * SOL)
    c.more(es, "reversed", legs[-1], [(t + 202, 0, "whale", "buy", 100 * SOL, False), (t + 202, 1, "buyer 3", "buy", SOL, False)])
    case = c.build()
    x = c.one("exits", key=fr)
    ok(x["exit_attempts"] >= 2 and x["exit_slot"] is not None and x["exit_slot"] > t + 32, c, f"FR {x}")
    x = c.one("exits", key=nx)
    ok((x["exit_slot"], x["proceeds"], x["exit_attempts"], x["exit_decision_slot"]) == (None, 0, 2, t + 102 + 599), c, f"NX {x}")
    ok(c.one("fills", key=nx, side="sell")["slot"] is None, c, "NX's sell fill has no slot")
    o = c.one("orders", key=es)
    ok((o["status"], o["reason"]) == ("entry_failed", "entry_slippage"), c, f"ES {o}")
    return case


def d_from_d_ms():
    """d is the group's d_ms in the clock's slots at t (engine.py:667-672): at 150 ms a slot, 600 ms is 4 slots (and
    tp4_T600's cap 1,069)."""
    c = Case("d_from_d_ms", "Epoch 1042 measured at 150 ms a slot: K3's d_ms 600 is 4 slots, so P's entry is t + 4 and its "
                            "exit 4 slots after the decision; the cap is round(600 * 267.3 / 150) = 1,069.", n2_k=0,
             clock={**C1.DEV2_CLOCK, "1042": 150.0})
    t = 450_160_000
    p = c.pool("P")
    legs = reversed_trigger(c, p, t, 400 * SOL)
    c.more(p, "reversed", legs[-1], [(t + 20, 0, "buyer 3", "buy", 40 * SOL, False), (t + 30, 0, "buyer 4", "buy", SOL, False)])
    case = c.build()
    o, x = c.one("orders", key=p), c.one("exits", key=p)
    ok((o["entry_slot"], x["cap_slots"], x["exit_slot"] - x["exit_decision_slot"]) == (t + 4, 1_069, 4), c, f"{o} {x}")
    return case


def stale_decision_at_the_end():
    """A pending exit cut off by a completion mid-slot (replay.py:510-511) leaves its decision slot; a graduation still
    waiting at the end of data sells into the seed pool at (that decision slot) + 1 (engine.py:1300), not the last
    slot + 1, and the decision slot is not rewritten."""
    c = Case("stale_decision_at_the_end", "Z: take_profit decided for t + 10 (pending t + 12). Slot t + 12's first tx, before our "
                                          "insertion, is a buy of all but 1T tokens; re-run after ours it completes the curve, "
                                          "so the sale never comes. Z's pool opens after the data: the end of data sells into "
                                          "the seed pool at t + 11 (the stale decision slot + 1), reason graduation.", n2_k=0)
    t = 450_160_000
    pz = c.pool("PZ", "normal", create_slot=t + 10**6)
    z = c.token("Z", pool=pz)
    c.pools[0]["mint"] = z
    legs = C1._curve_trigger(c, z, t)
    legs = c.curve(z, [(t + 10, 0, "buyer 3", "buy", 40 * T)], start=legs[-1].post)
    c.curve(z, [(t + 12, 0, "whale", "buy", legs[-1].post.rt - T), (t + 12, 1, "buyer 4", "buy", T // 2)], start=legs[-1].post)
    c.trades(c.pool("O"), "reversed", 5_000 * SOL, 10**15, [(t + 100, 0, "small buyer", "buy", SOL, False)])
    case = c.build()
    x = c.one("exits", key=z)
    ok((x["exit_reason"], x["exit_decision_slot"], x["exit_slot"], x["seed_pool"]) == ("graduation", t + 10, t + 11, 1), c, f"Z {x}")
    return case


def curve_seed_from_the_run():
    """The curve seed is every earlier leg of the mint (Holdings.seed(legs[:i]); here the run's own sums, spec D4): A held
    30T before our entry, buys for 2 SOL after it (an exact-in buy: the re-run gets fewer tokens) and sells everything:
    the sale is cut to what A holds in the counterfactual (30T plus the re-run buy), not to the re-run buy alone."""
    c = Case("curve_seed_from_the_run", "A buys 30T before the entry; after it A spends 2 SOL (the re-run gets fewer tokens) "
                                        "and sells all it has: exact holdings cut the sell to 30T plus what the re-run buy got.",
             n2_k=0)
    t = 450_160_000
    a = c.token("A")
    trades = [(t - 60, 0, "holder", "buy", 30 * T), (t - 40, 0, "buyer", "buy", 20 * T), (t - 30, 0, "buyer 2", "buy", 20 * T),
              (t, 0, "seller", "sell", 60 * T)]
    legs = c.curve(a, trades)
    leg = c.buy_in(a, legs[-1].post, t + 5, 0, "holder", 2 * SOL)
    c.curve(a, [(t + 6, 0, "holder", "sell", 30 * T + leg.t)], start=leg.post)
    case = c.build()
    x = c.one("exits", key=a)
    ok(x["sells_scaled"] == 1 and x["sells_dropped"] == 0, c, f"A {x}")
    return case


def flow_index_from_the_entry():
    """A flow rule's index starts with the pool's legs up to the entry slot (engine.py:906-907): the entry slot's own legs
    count in the window of a decision n_flow slots later, and keep it from an outflow."""
    c = Case("flow_index_from_the_entry", "HF (group H): the entry slot has a 5 SOL token buy before our insertion and a small "
                                          "one after it; exactly 1,120 slots later a 1 SOL token sell. The decision there sees "
                                          "[entry, entry + 1,120], with the entry slot's buys: no outflow. The end of data "
                                          "decides the deadline, whose window is empty: outflow there.", n2_k=0)
    t0, t = 450_150_000, 450_165_000
    c.trades(c.pool("O"), "reversed", 5_000 * SOL, 10**15, [(t0, 0, "small buyer", "buy", SOL, False)])
    hf = c.pool("HF")
    legs = reversed_trigger(c, hf, t, 400 * SOL)
    c.more(hf, "reversed", legs[-1], [(t + 2, 0, "buyer 3", "buy", 5 * SOL, False), (t + 2, 1, "buyer 4", "buy", SOL // 10, False),
                                      (t + 2 + 1_120, 0, "seller 2", "sell", SOL, False)])
    case = c.build()
    x = c.one("exits", key=hf, variant="flow300_H6")
    ok((x["exit_reason"], x["exit_decision_slot"]) == ("outflow", t + 2 + 80_648), c, f"HF {x}")
    return case


def graduation_sale_capped():
    """The sale in the token's own pool is pool_sell_capped (engine.py:1014): a pool whose real quote cannot pay our
    tokens sells as many as it can (vault_capped), where an uncapped sale would fail."""
    c = Case("graduation_sale_capped", "V: a re-run buy completes the curve while we hold it; its pool opens at t + 150 with "
                                       "60 SOL virtual quote and 0.1 SOL real: at open + 1 the sale is capped by the real "
                                       "vault (vault_capped 1, proceeds above 0).", n2_k=0)
    t = 450_160_000
    pv = c.pool("PV", "normal", create_slot=t + 150)
    v = c.token("V", pool=pv)
    c.pools[0]["mint"] = v
    legs = C1._curve_trigger(c, v, t)
    c.curve(v, [(t + 10, 0, "whale", "buy", legs[-1].post.rt - T)], start=legs[-1].post)
    c.buys_from(pv, Pool(10**15, 60_100_000_000, 20, 5, 5, 0, virt=60 * SOL), [(t + 150, 0, "first buyer", 10**7)])
    c.trades(c.pool("O"), "reversed", 5_000 * SOL, 10**15, [(t + 200, 0, "small buyer", "buy", SOL, False)])
    case = c.build()
    x = c.one("exits", key=v)
    ok((x["vault_capped"], x["exit_slot"]) == (1, t + 151) and x["proceeds"] > 0, c, f"V {x}")
    return case


def pending_n2_key_kept_through_the_sweep():
    """A pending entry across the hourly sweep (engine.py:677-678; LAB review on #380): the sweep's bound is the new hour's
    first event slot S less an hour, not the hour's start. With d = 3 an N2 key that traded only at the hour's start, picked
    on the hour's last slot, is still pending when a late first event of the next hour sweeps: the keep holds its legs, and
    its entry fills. (With d <= 2 the entry always comes first.)"""
    clock = {**C1.DEV2_CLOCK, "1042": 218.29}  # fwd1053's slot time at epoch 1042: d_ms 600 is 3 slots, an hour 16,492
    c = Case("pending_n2_key_kept_through_the_sweep",
             "Epoch 1042 at 218.29 ms (an hour is 16,492 slots, d 3). K (reversed, 1,000 SOL) trades once, at the hour's first "
             "slot H0. P triggers on the hour's last slot H0 + h - 1 and its N2 row picks K, entry H0 + h + 2. The next hour's "
             "first event is O's at H0 + h + 1: its rollover sweeps the keys without an event since H0 + 1, K among them, but "
             "K's pending entry keeps it, so the quiet entry at H0 + h + 2 fills on K's legs (else no_pool_state).", n2_k=1,
             clock=clock)
    h = Clock(clock).slots(3_600_000, 450_160_000)
    h0 = (450_160_000 // h + 1) * h
    assert (h, Clock(clock).slots(600, h0 + h - 1)) == (16_492, 3)
    k = c.pool("K")
    c.trades(k, "reversed", 1_000 * SOL, 10**15, [(h0, 0, "small buyer", "buy", SOL, False)])
    reversed_trigger(c, c.pool("P"), h0 + h - 1, 1_000 * SOL)
    c.trades(c.pool("O"), "reversed", 5_000 * SOL, 10**15, [(h0 + h + 1, 0, "small buyer", "buy", SOL, False),
                                                           (h0 + h + 10, 0, "small buyer", "buy", SOL, False)])
    case = c.build()
    o = c.one("orders", family="N2_F7", key=k)
    ok((o["status"], o["entry_slot"]) == ("filled", h0 + h + 2), c, f"K's N2 order {o}")
    return case


CASES = [k3_pool_exits, busy_until_the_exit_slot, n2_overwrite, quiet_entry_across_midnight, n2_pool_and_curve_entries,
         not_loaded_before_the_first_dated_slot, curve_k3_exits, curve_entry_failures, our_buy_completes,
         completion_during_the_hold, graduation_kept_through_the_sweep, h_flow_exits, exits_that_fail, d_from_d_ms,
         stale_decision_at_the_end, curve_seed_from_the_run, flow_index_from_the_entry, graduation_sale_capped,
         pending_n2_key_kept_through_the_sweep]


# ---------------------------------------------------------------------------------------------- the file

def dumps(doc):
    """JSON with indent 1, every token, pool, event and journal write on one line."""
    flat = {}

    def line(x):
        flat[f'"@{len(flat)}@"'] = json.dumps(x)
        return f"@{len(flat) - 1}@"

    cases = [{**c, "tokens": [line(t) for t in c["tokens"]], "pools": [line(p) for p in c["pools"]],
              "events": [line(e) for e in c["events"]], "expect_ops": [line(o) for o in c["expect_ops"]]} for c in doc["cases"]]
    return re.sub(r'"@\d+@"', lambda m: flat[m.group(0)], json.dumps({**doc, "cases": cases}, indent=1)) + "\n"


def generate(path=PATH):
    """Build and run every case, and write the file with the engine's writes."""
    out = [{k: case[k] for k in CASE} for case in (f() for f in CASES)]
    doc = {"format": FORMAT, "spec": "research/m1-c2-positionsim-spec-2026-10-19.md §6.2 (lab/paper/tests/c2_position_cases.py)",
           "cases": out}
    C1.check_ints(doc)
    text = dumps(doc)
    assert json.loads(text) == doc
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        f.write(text)
    os.replace(path + ".tmp", path)
    return doc


if __name__ == "__main__":
    d = generate(sys.argv[1] if len(sys.argv) > 1 else PATH)
    print(f"{len(d['cases'])} cases, {sum(len(c['expect_ops']) for c in d['cases'])} writes: {PATH}")
