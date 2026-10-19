"""The golden cases of M1 C3-2 and C3-3 (research/m1-c3-risk-spec-2026-10-19.md §2.2-§2.7, §5): small feeds that the real
paper engine (paper/engine.py, unchanged) runs with risk ON, from each group's own risk file, and whose journal writes
(the five tables and the heartbeat) and wallet files the Rust route must reproduce one by one. test_risk_cases.py asserts that the engine still writes exactly the stored ones;
lab/live's paper_core tests read the same file.

  C3_CASES_REGEN=1 pytest paper/tests/test_risk_cases.py      # rewrite data/c3_risk_cases.json from the engine
  python -m paper.tests.c3_risk_cases                          # the same

The cases are C2's (c2_position_cases.Case), plus each group's risk file as versions: the text the run starts with,
and texts that replace it when the feed hands out its first event at or after a slot, as test_engine.py's FlipRisk does
(the engine re-reads the file at each new minute and at each entry, engine.py:471, 869). A text of null is a missing
file. The files are `risk-K3.yaml` and `risk-H.yaml` in the run's working directory, so the groups' text names them
as is. Each case asserts what it is for on the engine's own writes.
"""

import contextlib
import json
import os
import sys
import tempfile
from dataclasses import asdict, replace

from paper.engine import _day
from paper.tests import c1b_signal_cases as C1
from paper.tests import c2_position_cases as C2

SOL = C1.SOL
T = C1.T
PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "c3_risk_cases.json")
FILES = {"K3": "risk-K3.yaml", "H": "risk-H.yaml"}
LIMITS = {"size_sol": "0.5", "max_open_positions": "5", "max_exposure_sol": "2.5", "daily_loss_limit_sol": "1.0",
          "kill_switch": "false", "families": "[F7]", "pool_cooldown_h": "24"}  # paper/risk.yaml's (and risk-h.yaml's)
MIDNIGHT_SLOT = 450_131_835  # the first slot of 2026-09-22 by C1.block_time

FORMAT = {
    "file": "{format, spec, cases: [case]}; every case runs on its own, from an empty engine, with risk on.",
    "case": "C2's case (c2_position_cases.json FORMAT: name, why, clock, dev_bounds, groups, labels, tokens, pools, events, "
            "cost_scenario, expect_ops) plus risk. groups names each group's risk file: risk-K3.yaml, risk-H.yaml.",
    "expect_ops": "C2's writes of the five tables, plus every heartbeat put ([\"put\", \"heartbeat\", row], engine.py:461-465, "
                  "477-480) and every wallet file the engine writes ([\"wallet\", file, the file's text: json.dump's], engine.py:1267-1283: "
                  "each new dated minute and the end of data, a group only when its wallet changed), in the engine's order.",
    "risk": "group name -> [[at, text], ...]: the first item's at is null, the text the run starts with; each later item "
            "replaces the file's text when the feed hands out its first event with slot >= at, before that event (the "
            "engine has not ended the slot before it yet). A null text is a missing file. Several items with the same at "
            "apply in the order given, the groups in the order of this object.",
    "regenerate": "C3_CASES_REGEN=1 pytest paper/tests/test_risk_cases.py (or python -m paper.tests.c3_risk_cases).",
}


def risk(**over):
    """A risk file's text: paper/risk.yaml's limits with `over` (key=None drops the key)."""
    v = {**LIMITS, **{k: str(x).lower() if isinstance(x, bool) else x for k, x in over.items()}}
    return "".join(f"{k}: {x}\n" for k, x in v.items() if x is not None)


class FlipFeed(C1.CaseFeed):
    """A case's feed that rewrites the groups' risk files as the case's risk versions say."""

    def __init__(self, case):
        super().__init__(case)
        self.flips = [(at, FILES[g], text) for g, vs in case["risk"].items() for at, text in vs[1:]]
        self.flips.sort(key=lambda f: f[0])  # stable: the groups in the object's order on a tie

    def events(self):
        i = 0
        for e in self.evs:
            while i < len(self.flips) and self.flips[i][0] <= e.slot:
                write(self.flips[i][1], self.flips[i][2])
                i += 1
            yield e


def write(path, text):
    if text is None:
        with contextlib.suppress(FileNotFoundError):
            os.remove(path)
    else:
        with open(path, "w") as f:
            f.write(text)


class RecJournal(C2.RecJournal):
    """C2's recording journal, the heartbeat table too."""

    def put(self, table, **row):
        if table == "heartbeat":
            self.ops.append(["put", table, {c: row.get(c) for c in self.cols[table]}])
        super().put(table, **row)


def record_wallets(eng, j):
    """Each wallet file `_write_wallet` writes, as ["wallet", file, its text], in the groups' order."""
    write = eng._write_wallet

    def recorded(block_time=None):
        dirty = [g for g in eng.groups if g.wallet_dirty and not eng.replaying]
        write(block_time)
        for g in dirty:
            with open(os.path.join(j.dir, g.wallet_file)) as f:
                j.ops.append(["wallet", g.wallet_file, f.read()])

    eng._write_wallet = recorded


def engine(case, journal):
    """C1.engine with risk on: the groups' risk files (relative to the working directory) and FlipFeed."""
    gs = json.loads(case["groups"])
    (name, p), = [(n, g) for n, g in gs.items() if g["primary"]]
    others = [C1.Group(n, [C1.TP_T.get(v, v) for v in g["variants"]], farm=g["farm"], segments=g["segments"], d=g["d"],
                       d_ms=g["d_ms"], q=g["q"], n2_k=g["n2_k"], risk_path=g["risk"], wallet_file=g["wallet"],
                       require_farm_complete=g["require_farm_complete"]) for n, g in gs.items() if not g["primary"]]
    eng = C1.Engine(FlipFeed(case), journal, p["risk"], variants=[C1.TP_T.get(v, v) for v in p["variants"]], d=p["d"],
                    d_ms=p["d_ms"], q=p["q"], n2_k=p["n2_k"], segments=p["segments"], farm=p["farm"], name=name,
                    groups=others, ignore_risk=False, clock=C1.Clock(case["clock"]), dev_bounds=tuple(case["dev_bounds"]),
                    backup=False)
    if C1.groups_state(eng) != case["groups"]:
        raise ValueError(f"case {case['name']}: groups is not what the engine journals: {C1.groups_state(eng)}")
    return eng


def run(case):
    """The engine's journal writes of a case and the engine after the run, in a fresh working directory with the
    groups' first risk texts."""
    with tempfile.TemporaryDirectory(prefix="c3-case-") as d, contextlib.chdir(d):
        for g, vs in case["risk"].items():
            write(FILES[g], vs[0][1])
        j = RecJournal(os.path.join(d, "journal"))
        eng = engine(case, j)
        record_wallets(eng, j)
        eng.run()
        ops = j.ops
        j.close()
    return ops, eng


class Case(C2.Case):
    """C2's case builder with each group's risk file."""

    def __init__(self, name, why, n2_k=0, k3=None, h=None, **kw):
        super().__init__(name, why, n2_k=n2_k, **kw)
        self.risk = {"K3": [[None, risk() if k3 is None else k3]], "H": [[None, risk() if h is None else h]]}

    def flip(self, group, at, text):
        assert not self.risk[group] or self.risk[group][-1][0] is None or self.risk[group][-1][0] <= at
        self.risk[group].append([at, text])

    def build(self):
        case = C1.Case.build(self)
        gs = json.loads(case["groups"])
        for g in gs:
            gs[g]["risk"] = FILES[g]
        case["groups"] = json.dumps(gs, sort_keys=True)
        case["risk"] = self.risk
        with tempfile.TemporaryDirectory(prefix="c3-case-") as d:  # the engine's own groups and cost scenario text
            j = C2.Journal(d)
            eng = engine(case, j)
            case["groups"] = C1.groups_state(eng)
            case["cost_scenario"] = json.dumps(asdict(eng.scn), sort_keys=True)
            j.close()
        case["expect_ops"], self.eng = run(case)
        self.ops = case["expect_ops"]
        return case


ok = C2.ok
trigger = C2.reversed_trigger


def minute(slot):
    return C1.block_time(slot) // 60


def statuses(c, **match):
    return sorted((r["key"], r["family"], r["variant"], r["t"], r["status"], r["reason"]) for r in c.rows("signals", **match))


# ---------------------------------------------------------------------------------------------- the cases

def max_open_counts_pending_and_open():
    """'max open positions' (engine.py:837-840): the group's open positions and its pending entries count, N2's not."""
    c = Case("max_open_counts_pending_and_open", "max_open_positions 1. P1 triggers at t and is pending until t + 2: P2's "
                                                  "trigger at t + 1 is skipped (one pending). P1 is open (no exit before "
                                                  "its deadline) when P3 triggers at t + 300: skipped (one open).",
             k3=risk(max_open_positions=1))
    t = 450_160_000
    trigger(c, c.pool("P1"), t, 400 * SOL)
    trigger(c, c.pool("P2"), t + 1, 400 * SOL)
    trigger(c, c.pool("P3"), t + 300, 400 * SOL)
    case = c.build()
    ok(c.one("signals", key=c.key("P1"), variant="tp4_T600")["status"] == "entered", c, "P1")
    for p in ("P2", "P3"):
        r = c.one("signals", key=c.key(p), variant="tp4_T600")
        ok((r["status"], r["reason"]) == ("skipped", "max open positions"), c, f"{p} {r}")
    return case


def max_exposure_at_its_edge():
    """'max exposure' (engine.py:841-843): the open positions at their venue cost plus the pending ones and the new one at
    the file's size; equal to the limit passes, above it blocks."""
    c = Case("max_exposure_at_its_edge", "size 0.5, max_exposure 1.0. P1 fills at a venue cost of exactly 0.5 SOL (a "
                                         "reversed pool's buy spends its size). P2 at t + 100: 0.5 + 0.5 = 1.0 is not above "
                                         "the limit, it enters. P3 at t + 200: 1.5, 'max exposure'.",
             k3=risk(max_exposure_sol="1.0"))
    t = 450_160_000
    trigger(c, c.pool("P1"), t, 400 * SOL)
    trigger(c, c.pool("P2"), t + 100, 400 * SOL)
    trigger(c, c.pool("P3"), t + 200, 400 * SOL)
    case = c.build()
    ok(c.one("orders", key=c.key("P1"))["status"] == "filled" and c.one("fills", key=c.key("P1"), side="buy")["quote"] == SOL // 2,
       c, "P1 costs exactly 0.5 SOL")
    ok(c.one("signals", key=c.key("P2"), variant="tp4_T600")["status"] == "entered", c, "P2 at the edge")
    r = c.one("signals", key=c.key("P3"), variant="tp4_T600")
    ok((r["status"], r["reason"]) == ("skipped", "max exposure"), c, f"P3 {r}")
    return case


def kill_switch_stops_n2_too():
    """The kill switch (engine.py:829-830) is the one limit N2's shadow rows obey."""
    c = Case("kill_switch_stops_n2_too", "kill_switch true: P's K3 row and its N2 row for Q are skipped 'kill switch'.", n2_k=1,
             k3=risk(kill_switch=True))
    t = 450_175_000
    c.trades(c.pool("Q"), "reversed", 1_000 * SOL, 10**15, [(t - 300, 0, "small buyer", "buy", SOL, False)])
    trigger(c, c.pool("P"), t, 1_050 * SOL)
    case = c.build()
    rows = c.rows("signals", variant="tp4_T600")
    ok({r["family"] for r in rows} == {"F7", "N2_F7"} and {(r["status"], r["reason"]) for r in rows} == {("skipped", "kill switch")},
       c, f"rows {rows}")
    return case


def family_off_leaves_n2():
    """'family not enabled' (engine.py:833-834) comes after the N2 exemption: with no family enabled, F7's rows are skipped
    and the N2 row still enters and trades at 0.5 SOL."""
    c = Case("family_off_leaves_n2", "families []: P's K3 row is skipped 'family not enabled'; its N2 row for Q enters, and "
                                     "Q's N2 position opens at 0.5 SOL.", n2_k=1, k3=risk(families="[]"))
    t = 450_175_000
    c.trades(c.pool("Q"), "reversed", 1_000 * SOL, 10**15, [(t - 300, 0, "small buyer", "buy", SOL, False)])
    trigger(c, c.pool("P"), t, 1_050 * SOL)
    case = c.build()
    r = c.one("signals", family="F7", variant="tp4_T600")
    ok((r["status"], r["reason"]) == ("skipped", "family not enabled"), c, f"F7 {r}")
    o = c.one("orders", family="N2_F7")
    ok((o["status"], o["size"]) == ("filled", SOL // 2), c, f"N2 {o}")
    return case


def files_invalid_or_missing():
    """A group whose file is invalid or missing opens nothing, N2 included (engine.py:827-828); the reason is risk.load's.
    The other group trades on its own file."""
    c = Case("files_invalid_or_missing", "K3's file has no size_sol: its rows (F7 and N2) are skipped with risk.load's "
                                         "KeyError reason. H's file is missing: H's rows say FileNotFoundError. The feed "
                                         "starts an hour before the triggers so H's farm window is complete.", n2_k=1,
             k3=risk(size_sol=None), h=None)
    c.risk["H"] = [[None, None]]
    t0, t = 450_150_000, 450_165_000
    c.trades(c.pool("O"), "reversed", 5_000 * SOL, 10**15, [(t0, 0, "small buyer", "buy", SOL, False)])
    c.trades(c.pool("Q"), "reversed", 1_000 * SOL, 10**15, [(t - 300, 0, "small buyer", "buy", SOL, False)])
    trigger(c, c.pool("P"), t, 1_050 * SOL)
    case = c.build()
    want = {"F7": "risk config invalid: KeyError 'size_sol'", "N2_F7": "risk config invalid: KeyError 'size_sol'"}
    for r in c.rows("signals"):
        why = "risk config unreadable: FileNotFoundError" if r["variant"].startswith("flow") else want[r["family"]]
        ok((r["status"], r["reason"]) == ("skipped", why), c, f"row {r}")
    ok({r["variant"] for r in c.rows("signals")} >= {"tp4_T600", "flow300_H6"}, c, "K3, N2 and H rows")
    return case


def at_entry_the_file_is_read_again():
    """The file is read again at each entry (engine.py:868-877): a kill switch or an invalid file that came after the
    signal writes an order 'at entry: …' of size 0, sets busy to t (so the key can trade again) and opens nothing; N2
    entries too."""
    c = Case("at_entry_the_file_is_read_again", "P1 triggers at t (entry t + 2); K3's file turns the kill switch on at "
                                                "t + 2 (the feed's event there comes before the slot t is ended): P1's order "
                                                "and its N2 order are 'at entry: kill switch'. The file is valid again from "
                                                "t + 300 (reloaded at the minute that starts before t + 470). P1 triggers "
                                                "again at t + 500: busy was set to t at the skipped entry, so it enters and "
                                                "fills. P2 triggers at t + 600 and its file loses size_sol at its entry slot: "
                                                "'at entry: risk config invalid: …'. A flip lands before the engine ends the "
                                                "slot before it, so t, t + 600 and the slots before them (the inflow buys) "
                                                "share a minute: no minute's reload reads the new text before the signal.",
             n2_k=1)
    t = 450_175_120
    assert minute(t - 30) == minute(t) and minute(t + 570) == minute(t + 600) == minute(t + 602)
    assert minute(t + 300) == minute(t + 100) < minute(t + 470)
    c.trades(c.pool("Q"), "reversed", 1_000 * SOL, 10**15, [(t - 300, 0, "small buyer", "buy", SOL, False)])
    p1 = c.pool("P1")
    legs = trigger(c, p1, t, 1_050 * SOL)
    legs = c.more(p1, "reversed", legs[-1], [(t + 2, 0, "buyer 3", "buy", SOL, False)])
    c.flip("K3", t + 2, risk(kill_switch=True))
    c.trades(c.pool("O"), "reversed", 5_000 * SOL, 10**15, [(t + 100, 0, "small buyer", "buy", SOL, False)])  # ends t + 2 first
    c.flip("K3", t + 300, risk())
    c.more(p1, "reversed", legs[-1], [(t + 470, 0, "buyer", "buy", 2 * SOL, False), (t + 500, 0, "seller", "sell", 55 * SOL, False)])
    p2 = c.pool("P2")
    legs = trigger(c, p2, t + 600, 1_050 * SOL)
    c.more(p2, "reversed", legs[-1], [(t + 602, 0, "buyer 3", "buy", SOL, False)])
    c.flip("K3", t + 602, risk(size_sol=None))
    case = c.build()
    for o in c.rows("orders", t=t):
        ok((o["status"], o["reason"], o["size"]) == ("skipped", "at entry: kill switch", 0), c, f"t's order {o}")
    ok({o["family"] for o in c.rows("orders", t=t)} == {"F7", "N2_F7"}, c, "both orders at t")
    ok(c.rows("fills", key=p1, t=t) == [] and c.rows("exits", key=p1, t=t) == [], c, "nothing filled at t")
    r = c.one("signals", key=p1, t=t + 500, family="F7", variant="tp4_T600")
    ok(r["status"] == "entered" and c.one("orders", key=p1, t=t + 500, family="F7")["status"] == "filled", c, f"P1 again {r}")
    o = c.one("orders", key=p2, family="F7")
    ok((o["status"], o["reason"]) == ("skipped", "at entry: risk config invalid: KeyError 'size_sol'"), c, f"P2 {o}")
    return case


def size_from_the_file_n2_at_half_a_sol():
    """The order's size is the file's size_sol in lamports (engine.py:880-881), read again at each minute and at the entry;
    N2 always trades 0.5 SOL."""
    c = Case("size_from_the_file_n2_at_half_a_sol", "K3 starts at size 0.4, max exposure 2.5. P1 at t: its F7 order is "
                                                   "0.4 SOL, its N2 order 0.5 SOL. The file changes to size 0.3 at t + 500 "
                                                   "(a later minute): P2's order at t + 1,000 is 0.3 SOL. O's trade at t + 100 "
                                                   "makes P1's quiet entry come before the flip (a flip lands before the "
                                                   "quiet entries due before its event).", n2_k=1,
             k3=risk(size_sol="0.4"))
    t = 450_175_000
    c.trades(c.pool("Q"), "reversed", 1_000 * SOL, 10**15, [(t - 300, 0, "small buyer", "buy", SOL, False)])
    trigger(c, c.pool("P1"), t, 1_050 * SOL)
    c.trades(c.pool("O"), "reversed", 5_000 * SOL, 10**15, [(t + 100, 0, "small buyer", "buy", SOL, False),
                                                             (t + 500, 0, "small buyer", "buy", SOL, False)])
    c.flip("K3", t + 500, risk(size_sol="0.3"))
    trigger(c, c.pool("P2"), t + 1_000, 1_050 * SOL)
    case = c.build()
    ok(c.one("orders", key=c.key("P1"), family="F7")["size"] == 400_000_000, c, "P1 F7 size")
    ok(c.one("orders", family="N2_F7", t=t)["size"] == 500_000_000, c, "P1 N2 size")
    ok(c.one("orders", key=c.key("P2"), family="F7")["size"] == 300_000_000, c, "P2 F7 size")
    return case


def daily_loss_blocks_until_midnight():
    """'daily loss limit' (engine.py:844-845): a stop loss before midnight books its loss; the next trigger that day is
    skipped; the day's loss is reset at the first dated slot of the next day (engine.py:455-460), so a trigger after
    midnight enters."""
    c = Case("daily_loss_blocks_until_midnight", "daily_loss_limit 0.001 SOL. SL: a stop loss decided at t + 11 and settled at "
                                                 "SL's next event (t + 20) loses more than that. P2 at t + 200 (the same day): 'daily loss limit'. P3 at t + 1,000, "
                                                 "after midnight: entered.", k3=risk(daily_loss_limit_sol="0.001"))
    t = MIDNIGHT_SLOT - 600
    sl = c.pool("SL")
    legs = trigger(c, sl, t, 400 * SOL)
    c.more(sl, "reversed", legs[-1], [(t + 10, 0, "seller 2", "sell", 20 * SOL, False), (t + 11, 0, "seller 3", "sell", 20 * SOL, False),
                                      (t + 20, 0, "buyer 3", "buy", SOL // 10, False)])
    trigger(c, c.pool("P2"), t + 200, 400 * SOL)
    trigger(c, c.pool("P3"), t + 1_000, 400 * SOL)
    assert _day(C1.block_time(t + 200)) != _day(C1.block_time(t + 1_000))
    case = c.build()
    x = c.one("exits", key=sl)
    ok(x["exit_reason"] == "stop_loss" and x["pnl"] < -1_000_000 and x["exit_slot"] < t + 200, c, f"SL {x}")
    r = c.one("signals", key=c.key("P2"), variant="tp4_T600")
    ok((r["status"], r["reason"]) == ("skipped", "daily loss limit"), c, f"P2 {r}")
    ok(c.one("signals", key=c.key("P3"), variant="tp4_T600")["status"] == "entered", c, "P3 after midnight")
    return case


def cooldown_after_a_total_loss():
    """The pool cooldown (engine.py:1054-1057, 835-836): an exit with proceeds under 10 % of the cost closes the pool for
    pool_cooldown_h, from its exit slot (or the settle slot), in the clock's slots at the settle slot."""
    c = Case("cooldown_after_a_total_loss", "pool_cooldown_h 0.01 (36 s, 135 slots at epoch 1042). P's position: a 390 SOL "
                                            "token sell takes the pool's SOL out right after the entry; the stop loss sells "
                                            "for under 10 % of the cost. A whale buy refills the pool. P triggers again "
                                            "inside the cooldown: 'pool cooldown'; again after it: entered.",
             k3=risk(pool_cooldown_h="0.01"))
    t = 450_160_000
    p = c.pool("P")
    legs = trigger(c, p, t, 400 * SOL)
    legs = c.more(p, "reversed", legs[-1], [(t + 5, 0, "dumper", "sell", 350 * SOL, False), (t + 9, 0, "buyer 3", "buy", SOL // 10, False),
                                            (t + 20, 0, "whale", "buy", 350 * SOL, False)])
    legs = c.more(p, "reversed", legs[-1], [(t + 60, 0, "buyer", "buy", 2 * SOL, False), (t + 90, 0, "seller", "sell", 20 * SOL, False)])
    legs = c.more(p, "reversed", legs[-1], [(t + 300, 0, "buyer", "buy", 2 * SOL, False), (t + 330, 0, "seller", "sell", 20 * SOL, False)])
    case = c.build()
    x = c.one("exits", key=p, t=t)
    ok(x["tokens"] > 0 and x["proceeds"] < 0.1 * x["cost"], c, f"a total loss {x}")
    rows = {r["t"]: (r["status"], r["reason"]) for r in c.rows("signals", key=p, variant="tp4_T600")}
    ok(rows.get(t + 90) == ("skipped", "pool cooldown"), c, f"inside {rows}")
    ok(rows.get(t + 330, ("",))[0] == "entered", c, f"after {rows}")
    return case


def _day_loss_case(name, limit, why):
    """TP gains, then SL loses more; P triggers after both settled. The day's loss is their signed sum."""
    c = Case(name, why, k3=risk(daily_loss_limit_sol=limit))
    t = 450_160_000
    tp, sl = c.pool("TP"), c.pool("SL")
    legs = trigger(c, tp, t, 400 * SOL)
    c.more(tp, "reversed", legs[-1], [(t + 10, 0, "buyer 3", "buy", 30 * SOL, False), (t + 11, 0, "buyer 4", "buy", 30 * SOL, False),
                                      (t + 20, 0, "buyer 5", "buy", SOL // 10, False)])
    legs = trigger(c, sl, t + 100, 400 * SOL)
    c.more(sl, "reversed", legs[-1], [(t + 110, 0, "seller 2", "sell", 60 * SOL, False), (t + 111, 0, "seller 3", "sell", 60 * SOL, False),
                                      (t + 120, 0, "buyer 3", "buy", SOL // 10, False)])
    trigger(c, c.pool("P"), t + 300, 400 * SOL)
    return c


def _exact_limit(lamports):
    """A daily_loss_limit_sol text whose float times 1e9 is exactly `lamports` (the engine compares -day_loss >= it)."""
    import math
    x = lamports / 1e9
    for _ in range(64):
        if x * 1e9 == lamports:
            return repr(x)
        x = math.nextafter(x, math.inf if x * 1e9 < lamports else -math.inf)
    raise ValueError(f"no float times 1e9 is {lamports}")


def daily_loss_is_signed_and_its_limit_inclusive():
    """The day's loss is the signed sum of the day's P&L (a gain lowers it, engine.py:1285-1287), and the limit blocks
    at equality (`-day_loss >= limit * 1e9`, :844): the limit set to exactly the sum of TP's gain and SL's loss blocks P;
    the same limit with one lamport more lets P enter."""
    probe = _day_loss_case("probe", "1.0", "")
    probe.build()
    gain, loss = probe.one("exits", key=probe.key("TP"))["pnl"], probe.one("exits", key=probe.key("SL"))["pnl"]
    assert gain > 0 > loss and -loss > gain, (gain, loss)
    day = -(gain + loss)
    c = _day_loss_case("daily_loss_is_signed_and_its_limit_inclusive", _exact_limit(day),
                       f"TP takes profit (+{gain:,} lamports) and SL stops out ({loss:,}): the day's loss is {day:,} lamports. "
                       f"daily_loss_limit_sol is the float whose 1e9 multiple is exactly that: P at t + 300 is skipped "
                       f"'daily loss limit' (equality blocks). SL's loss alone is above the limit, so a day's loss that "
                       f"ignored the gain would block too; daily_loss_one_lamport_over_passes shows the other side.")
    case = c.build()
    r = c.one("signals", key=c.key("P"), variant="tp4_T600")
    ok((r["status"], r["reason"]) == ("skipped", "daily loss limit"), c, f"P {r}")
    return case


def daily_loss_one_lamport_over_passes():
    """The case above with a limit one lamport above the day's loss: P enters, though SL's loss alone is above it."""
    probe = _day_loss_case("probe", "1.0", "")
    probe.build()
    gain, loss = probe.one("exits", key=probe.key("TP"))["pnl"], probe.one("exits", key=probe.key("SL"))["pnl"]
    day = -(gain + loss)
    c = _day_loss_case("daily_loss_one_lamport_over_passes", _exact_limit(day + 1),
                       f"As daily_loss_is_signed_and_its_limit_inclusive with the limit at {day + 1:,} lamports: P enters. "
                       f"SL's loss alone ({-loss:,}) is above that limit: only the signed sum lets P in.")
    case = c.build()
    ok(-loss > day + 1, c, "SL alone would block")
    ok(c.one("signals", key=c.key("P"), variant="tp4_T600")["status"] == "entered", c, "P")
    return case


def failed_entry_books_its_fee():
    """A failed entry costs the failed transaction (engine.py:929-931) and books it into the day's loss."""
    c = Case("failed_entry_books_its_fee", "daily_loss_limit 0.000001 SOL (1,000 lamports). ES: a 100 SOL token buy before our "
                                           "insertion in the entry slot: entry_slippage, which costs the failed transaction's "
                                           "fee. P at t + 300: 'daily loss limit'.", k3=risk(daily_loss_limit_sol="0.000001"))
    t = 450_160_000
    es = c.pool("ES")
    legs = trigger(c, es, t, 400 * SOL)
    c.more(es, "reversed", legs[-1], [(t + 2, 0, "whale", "buy", 100 * SOL, False), (t + 2, 1, "buyer 3", "buy", SOL, False)])
    trigger(c, c.pool("P"), t + 300, 400 * SOL)
    case = c.build()
    o = c.one("orders", key=es)
    x = c.one("exits", key=es)
    ok((o["status"], x["exit_reason"]) == ("entry_failed", "entry_slippage") and x["pnl"] < -1_000, c, f"ES {o} {x}")
    r = c.one("signals", key=c.key("P"), variant="tp4_T600")
    ok((r["status"], r["reason"]) == ("skipped", "daily loss limit"), c, f"P {r}")
    return case


def n2_positions_do_not_count():
    """N2's positions are left out of the group's open positions and exposure (engine.py:837-838)."""
    c = Case("n2_positions_do_not_count", "max_open_positions 1, max_exposure 0.5. P1 at t and its N2 row for Q enter. P1 "
                                          "stops out at t + 11 and settles at t + 20; Q's N2 position stays open with its "
                                          "0.5 SOL. P2 at t + 300 enters: Q's open N2 position is neither an open position "
                                          "nor exposure for the group.", n2_k=1,
             k3=risk(max_open_positions=1, max_exposure_sol="0.5"))
    t = 450_175_000
    c.trades(c.pool("Q"), "reversed", 1_000 * SOL, 10**15, [(t - 300, 0, "small buyer", "buy", SOL, False)])
    p1 = c.pool("P1")
    legs = trigger(c, p1, t, 1_050 * SOL)
    c.more(p1, "reversed", legs[-1], [(t + 10, 0, "seller 2", "sell", 60 * SOL, False), (t + 11, 0, "seller 3", "sell", 60 * SOL, False),
                                      (t + 20, 0, "buyer 3", "buy", SOL // 10, False)])
    trigger(c, c.pool("P2"), t + 300, 1_050 * SOL)
    case = c.build()
    ok(c.one("orders", family="N2_F7", t=t)["status"] == "filled", c, "Q's N2 position")
    x = c.one("exits", family="N2_F7", t=t)
    ok(x["exit_slot"] > t + 300, c, f"Q is still open at t + 300 {x}")
    ok(c.one("signals", key=c.key("P2"), family="F7", variant="tp4_T600")["status"] == "entered", c, "P2")
    return case


def h_group_has_its_own_file():
    """Each group reads its own file (engine.py:537): H's kill switch stops H's rows only; K3 trades."""
    c = Case("h_group_has_its_own_file", "H's file has the kill switch on, K3's not. The feed starts an hour before the "
                                         "triggers (H's farm window complete). P's K3 row enters, its H rows are 'kill "
                                         "switch'.", h=risk(kill_switch=True))
    t0, t = 450_150_000, 450_165_000
    c.trades(c.pool("O"), "reversed", 5_000 * SOL, 10**15, [(t0, 0, "small buyer", "buy", SOL, False)])
    trigger(c, c.pool("P"), t, 400 * SOL)
    case = c.build()
    ok(c.one("signals", key=c.key("P"), variant="tp4_T600")["status"] == "entered", c, "K3")
    for v in ("flow300_H6", "flow300_tp100_H6"):
        r = c.one("signals", key=c.key("P"), variant=v)
        ok((r["status"], r["reason"]) == ("skipped", "kill switch"), c, f"H {v} {r}")
    return case


def at_entry_the_limits_are_not_checked_again():
    """At the entry only the file's validity and the kill switch count (engine.py:868-873), not the limits: P1 and P2 enter
    with max_open 5; the file changes to max_open 1 before their entries; both fill."""
    c = Case("at_entry_the_limits_are_not_checked_again", "P1 at t and P2 at t + 1 enter (max_open 5, both pending). The "
                                                          "file changes to max_open_positions 1 at t + 2 (P1's entry slot, "
                                                          "before the engine ends t + 1): both entries read it and fill.")
    t = 450_175_120
    assert minute(t - 30) == minute(t) == minute(t + 1)
    p1 = c.pool("P1")
    legs = trigger(c, p1, t, 400 * SOL)
    c.more(p1, "reversed", legs[-1], [(t + 2, 0, "buyer 3", "buy", SOL // 10, False)])
    trigger(c, c.pool("P2"), t + 1, 400 * SOL)
    c.flip("K3", t + 2, risk(max_open_positions=1))
    case = c.build()
    for p in ("P1", "P2"):
        ok(c.one("orders", key=c.key(p))["status"] == "filled", c, p)
    return case


def _cooldown_case(name, why, e=None):
    """P stops out at a total loss; e: the cooldown's end by the engine's rule, where P triggers again."""
    c = Case(name, why, k3=risk(pool_cooldown_h="0.01"))
    t = 450_160_000
    p = c.pool("P")
    legs = trigger(c, p, t, 400 * SOL)
    legs = c.more(p, "reversed", legs[-1], [(t + 5, 0, "dumper", "sell", 350 * SOL, False), (t + 12, 0, "buyer 3", "buy", SOL // 10, False),
                                            (t + 20, 0, "whale", "buy", 350 * SOL, False)])
    if e is not None:
        c.more(p, "reversed", legs[-1], [(e - 30, 0, "buyer", "buy", 2 * SOL, False), (e, 0, "seller", "sell", 20 * SOL, False)])
    return c


def cooldown_ends_at_its_slot():
    """The cooldown's end is the exit slot (not the settle slot) plus Clock.slots(pool_cooldown_h * 3,600,000, settle
    slot), and a trigger at exactly that slot passes (`cool > t`, engine.py:835)."""
    probe = _cooldown_case("probe", "")
    probe.build()
    x = probe.one("exits", key=probe.key("P"))
    settle = 450_160_012  # P's next event after the exit: step 1 settles it there
    assert x["exit_slot"] < settle, x
    e = x["exit_slot"] + C1.Clock(probe.clock).slots(0.01 * 3_600_000, settle)
    c = _cooldown_case("cooldown_ends_at_its_slot", f"pool_cooldown_h 0.01. P's stop loss sells at a total loss in slot "
                                                    f"{x['exit_slot']} (quiet), settled at P's next event {settle}. The "
                                                    f"cooldown ends at the exit slot plus 36 s in slots: {e}. P triggers at "
                                                    f"{e}: entered (the cooldown is over at its end slot; one slot later "
                                                    f"by the settle slot, or with cool >= t, it would block).", e)
    case = c.build()
    ok(c.one("exits", key=c.key("P"), t=450_160_000)["exit_slot"] == x["exit_slot"], c, "the same exit")
    rows = {r["t"]: (r["status"], r["reason"]) for r in c.rows("signals", key=c.key("P"), variant="tp4_T600")}
    ok(rows.get(e, ("",))[0] == "entered", c, f"rows {rows}")
    return case


def _clock_change_case(name, why, e=None):
    c = Case(name, why, k3=risk(pool_cooldown_h="1"))
    t = 450_143_980  # epoch 1041 (265.83 ms a slot); 1042 (267.83 ms) starts at 450,144,000
    p = c.pool("P")
    legs = trigger(c, p, t, 400 * SOL)
    legs = c.more(p, "reversed", legs[-1], [(t + 5, 0, "dumper", "sell", 350 * SOL, False), (450_144_010, 0, "buyer 3", "buy", SOL // 10, False),
                                            (450_144_020, 0, "whale", "buy", 350 * SOL, False)])
    if e is not None:
        c.more(p, "reversed", legs[-1], [(e - 30, 0, "buyer", "buy", 2 * SOL, False), (e, 0, "seller", "sell", 20 * SOL, False)])
    return c


def cooldown_across_a_clock_change():
    """The cooldown's length is Clock.slots at the settle slot (engine.py:1056), not at the exit slot: a 1 h cooldown is
    13,542 slots in epoch 1041 and 13,441 in epoch 1042."""
    probe = _clock_change_case("probe", "")
    probe.build()
    x = probe.one("exits", key=probe.key("P"))
    clock = C1.Clock(probe.clock)
    assert x["exit_slot"] < 450_144_000 <= 450_144_010
    e = x["exit_slot"] + clock.slots(3_600_000.0, 450_144_010)
    assert e != x["exit_slot"] + clock.slots(3_600_000.0, x["exit_slot"])
    c = _clock_change_case("cooldown_across_a_clock_change", f"pool_cooldown_h 1. P's stop loss sells at a total loss in "
                                                             f"slot {x['exit_slot']} (epoch 1041) and settles at P's next "
                                                             f"event 450144010 (epoch 1042): the cooldown is 13,441 slots "
                                                             f"(1042's clock), to {e}. P triggers at {e}: entered (by the "
                                                             f"exit slot's clock it would end 101 slots later).", e)
    case = c.build()
    ok(c.one("exits", key=c.key("P"), t=450_143_980)["exit_slot"] == x["exit_slot"], c, "the same exit")
    ok(c.one("signals", key=c.key("P"), t=e, variant="tp4_T600")["status"] == "entered", c, "P at e")
    return case


def a_cooldown_beyond_64_bits_is_forever():
    """A finite but huge pool_cooldown_h (1e16 h): Clock.slots is 1.3e20 slots, an int Python adds to the exit slot without
    overflow, so the pool stays closed (review of #384: lab-core saturates instead of overflowing)."""
    c = Case("a_cooldown_beyond_64_bits_is_forever", "pool_cooldown_h 10000000000000000.0. P stops out at a total loss (a 350 "
                                                     "SOL token sell after the entry); a whale refills it; P triggers again at "
                                                     "t + 300: 'pool cooldown' (the cooldown is 1.3e20 slots long).",
             k3=risk(pool_cooldown_h="10000000000000000.0"))
    t = 450_160_000
    p = c.pool("P")
    legs = trigger(c, p, t, 400 * SOL)
    legs = c.more(p, "reversed", legs[-1], [(t + 5, 0, "dumper", "sell", 350 * SOL, False), (t + 12, 0, "buyer 3", "buy", SOL // 10, False),
                                            (t + 20, 0, "whale", "buy", 350 * SOL, False)])
    c.more(p, "reversed", legs[-1], [(t + 270, 0, "buyer", "buy", 2 * SOL, False), (t + 300, 0, "seller", "sell", 20 * SOL, False)])
    case = c.build()
    x = c.one("exits", key=p, t=t)
    ok(x["proceeds"] < 0.1 * x["cost"], c, f"a total loss {x}")
    r = c.one("signals", key=p, t=t + 300, variant="tp4_T600")
    ok((r["status"], r["reason"]) == ("skipped", "pool cooldown"), c, f"P again {r}")
    return case


def exposure_counts_the_open_cost():
    """An open position counts at its venue cost (engine.py:841), the pending and the new one at the file's size now."""
    c = Case("exposure_counts_the_open_cost", "size 0.3, max_exposure 0.7. P1 fills at 0.3 SOL. The file changes to size 0.4 "
                                              "at t + 300; a new minute starts before P2's trigger at t + 470, which reloads it. "
                                              "P2: 0.3 (P1's cost, P1 still open) + 0.4 = 0.7, not above the limit: entered (at "
                                              "the new size, 0.8 would block).",
             k3=risk(size_sol="0.3", max_exposure_sol="0.7"))
    t = 450_175_000
    trigger(c, c.pool("P1"), t, 1_050 * SOL)
    c.trades(c.pool("O"), "reversed", 5_000 * SOL, 10**15, [(t + 100, 0, "small buyer", "buy", SOL, False),
                                                             (t + 300, 0, "small buyer", "buy", SOL, False)])
    c.flip("K3", t + 300, risk(size_sol="0.4", max_exposure_sol="0.7"))
    assert minute(t + 470 - 30) > minute(t + 300) > minute(t + 100)
    trigger(c, c.pool("P2"), t + 470, 1_050 * SOL)
    case = c.build()
    ok(c.one("fills", key=c.key("P1"), side="buy")["quote"] == 300_000_000, c, "P1's cost")
    x = c.one("exits", key=c.key("P1"))
    ok(x["exit_slot"] > t + 470, c, f"P1 open at P2 {x}")
    o = c.one("orders", key=c.key("P2"))
    ok((o["status"], o["size"]) == ("filled", 400_000_000), c, f"P2 {o}")
    return case


def n2_pending_does_not_count():
    """N2's pending entries are left out of the group's count (engine.py:838)."""
    c = Case("n2_pending_does_not_count", "max_open_positions 2. P0 at t - 200 opens on pool A. A triggers again at t: its K3 "
                                          "row is busy, its N2 row for Q enters (pending to t + 2). P2 at t + 1: one open "
                                          "position and no F7 entry pending: entered (with Q's N2 entry counted it would be "
                                          "two).", n2_k=1, k3=risk(max_open_positions=2))
    t = 450_175_120
    c.trades(c.pool("Q"), "reversed", 1_000 * SOL, 10**15, [(t - 600, 0, "small buyer", "buy", SOL, False)])
    a = c.pool("A")
    legs = trigger(c, a, t - 200, 1_050 * SOL)
    c.more(a, "reversed", legs[-1], [(t - 30, 0, "buyer", "buy", 2 * SOL, False), (t, 0, "seller", "sell", 50 * SOL, False)])
    trigger(c, c.pool("P2"), t + 1, 1_050 * SOL)
    case = c.build()
    ok(c.one("orders", key=a, family="F7")["status"] == "filled", c, "P0 open")
    r = c.one("signals", key=a, family="F7", t=t, variant="tp4_T600")
    ok((r["status"], r["reason"]) == ("skipped", "busy"), c, f"A at t {r}")
    ok(c.one("signals", family="N2_F7", t=t)["status"] == "entered", c, "A's N2 row")
    ok(c.one("signals", key=c.key("P2"), family="F7", variant="tp4_T600")["status"] == "entered", c, "P2")
    return case


def other_groups_do_not_count():
    """A group counts only its own positions (engine.py:837-838): H's open positions are not K3's."""
    c = Case("other_groups_do_not_count", "K3 max_open_positions 1. P at t: K3 and both H variants enter (the feed starts an "
                                          "hour before, H's farm window is complete). Buys lift P: K3 takes profit and settles "
                                          "at t + 20; H's positions hold. P2 at t + 300: K3 has no open position (H has two): "
                                          "entered.", k3=risk(max_open_positions=1))
    t0, t = 450_150_000, 450_165_000
    c.trades(c.pool("O"), "reversed", 5_000 * SOL, 10**15, [(t0, 0, "small buyer", "buy", SOL, False)])
    p = c.pool("P")
    legs = trigger(c, p, t, 400 * SOL)
    c.more(p, "reversed", legs[-1], [(t + 10, 0, "buyer 3", "buy", 30 * SOL, False), (t + 11, 0, "buyer 4", "buy", 30 * SOL, False),
                                     (t + 20, 0, "buyer 5", "buy", SOL // 10, False)])
    trigger(c, c.pool("P2"), t + 300, 400 * SOL)
    case = c.build()
    ok(c.one("exits", key=p, variant="tp4_T600")["exit_slot"] <= t + 20, c, "K3 closed")
    for v in ("flow300_H6", "flow300_tp100_H6"):
        ok(c.one("exits", key=p, variant=v)["exit_slot"] > t + 300, c, f"H {v} open at P2")
    ok(c.one("signals", key=c.key("P2"), variant="tp4_T600")["status"] == "entered", c, "P2")
    return case


def n2_loss_is_not_booked():
    """N2's P&L stays out of the day's loss (engine.py:1286): N2 loses more than K3 gains, and K3 still trades under a
    1,000-lamport limit."""
    c = Case("n2_loss_is_not_booked", "daily_loss_limit 0.000001 SOL. P1 takes profit (buys after the entry) and settles at "
                                      "t + 20. Its N2 position on Q stops out (sells after the entry) and settles at t + 20, "
                                      "losing more than P1 gained. P2 at t + 300: the day's loss is P1's gain alone: entered.",
             n2_k=1, k3=risk(daily_loss_limit_sol="0.000001"))
    t = 450_175_000
    q = c.pool("Q")
    legs = c.trades(q, "reversed", 1_000 * SOL, 10**15, [(t - 300, 0, "small buyer", "buy", SOL, False)])
    c.more(q, "reversed", legs[-1], [(t + 10, 0, "seller 2", "sell", 60 * SOL, False), (t + 11, 0, "seller 3", "sell", 60 * SOL, False),
                                     (t + 20, 0, "buyer 3", "buy", SOL // 10, False)])
    p1 = c.pool("P1")
    legs = trigger(c, p1, t, 1_050 * SOL)
    c.more(p1, "reversed", legs[-1], [(t + 10, 0, "buyer 3", "buy", 25 * SOL, False), (t + 11, 0, "buyer 4", "buy", 25 * SOL, False),
                                      (t + 20, 0, "buyer 5", "buy", SOL // 10, False)])
    trigger(c, c.pool("P2"), t + 300, 1_050 * SOL)
    case = c.build()
    gain = c.one("exits", key=p1, family="F7")["pnl"]
    loss = c.one("exits", key=q, family="N2_F7", t=t)["pnl"]
    ok(gain > 0 and gain + loss < -1_000, c, f"P1 {gain}, Q {loss}: booked together they would block")
    ok(c.one("signals", key=c.key("P2"), family="F7", variant="tp4_T600")["status"] == "entered", c, "P2")
    return case


def _ten_percent_case(name, why, dump, again=True):
    """P (K3, 0.5 SOL at a venue cost of exactly 0.5 SOL) stops out after a token sell that takes `dump` lamports out;
    its sale fills on that state. A whale refills P; P triggers again 90 slots after its trigger, inside 36 s."""
    c = Case(name, why, k3=risk(pool_cooldown_h="0.01"))
    t = 450_160_000
    p = c.pool("P")
    legs = trigger(c, p, t, 400 * SOL)
    legs = c.more(p, "reversed", legs[-1], [(t + 5, 0, "dumper", "sell", dump, False), (t + 12, 0, "buyer 3", "buy", SOL // 10, False),
                                            (t + 20, 0, "whale", "buy", 350 * SOL, False)])
    if again:
        c.more(p, "reversed", legs[-1], [(t + 60, 0, "buyer", "buy", 2 * SOL, False), (t + 90, 0, "seller", "sell", 20 * SOL, False)])
    return c


def a_sale_of_exactly_a_tenth_is_no_total_loss():
    """The total loss is `proceeds < 0.1 * cost` (engine.py:1055), strict: 0.1 * 500,000,000 is exactly 50,000,000.0 as a
    float, and a sale that pays exactly 50,000,000 lamports sets no cooldown (LAB review on #384)."""
    def proceeds(dump):
        c = _ten_percent_case("probe", "", dump, again=False)
        c.build()
        x = c.one("exits", key=c.key("P"))
        assert x["cost"] == 500_000_000 and x["exit_reason"] == "stop_loss", x
        return x["proceeds"]

    lo, hi = 100 * SOL, 300 * SOL  # proceeds fall as the dump grows
    assert proceeds(lo) > 50_000_000 > proceeds(hi)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if proceeds(mid) > 50_000_000:
            lo = mid
        else:
            hi = mid
    dump = hi
    assert proceeds(dump) == 50_000_000, (dump, proceeds(dump))
    assert 0.1 * 500_000_000 == 50_000_000.0
    c = _ten_percent_case("a_sale_of_exactly_a_tenth_is_no_total_loss",
                          f"pool_cooldown_h 0.01. A token sell takes {dump:,} lamports out of P after K3's entry (cost "
                          f"500,000,000): the stop loss sells for exactly 50,000,000 lamports, a tenth of the cost, which is "
                          f"no total loss (proceeds < 0.1 * cost is strict). P triggers again at t + 90, inside what would "
                          f"be the cooldown: entered.", dump)
    case = c.build()
    x = c.one("exits", key=c.key("P"), t=450_160_000)
    ok((x["proceeds"], x["cost"]) == (50_000_000, 500_000_000), c, f"exactly a tenth {x}")
    ok(c.one("signals", key=c.key("P"), t=450_160_090, variant="tp4_T600")["status"] == "entered", c, "P again")
    return case


def the_cooldown_keeps_the_later_end():
    """`g.cool[key] = max(g.cool.get(key, 0), end)` (engine.py:1057): a later total loss with a shorter cooldown does not
    shorten the one before (LAB review on #384). Group H's two variants (no stop loss) hold one pool to deadlines 400
    slots apart and lose it all there; the file's pool_cooldown_h drops from 24 to 0.01 between their settles."""
    t0, t = 450_150_000, 450_165_000
    t2 = t + 400
    da = t + 2 + C1.Clock(C1.DEV2_CLOCK).slots(6 * 3_600_000, t)  # flow300_H6's deadline (entry t + 2, 6 h)
    db = da + 400  # flow300_tp100_H6's, entered again at t2 + 2
    c = Case("the_cooldown_keeps_the_later_end", "The feed starts an hour before (H's farm window is complete). P triggers at "
                                                 "t: K3 and both H variants enter. A 400 SOL token buy at t + 50: K3 and "
                                                 "flow300_tp100_H6 take profit; flow300_H6 (no take-profit, no stop loss) holds. "
                                                 "P triggers again at t + 400: flow300_tp100_H6 enters again, flow300_H6 is "
                                                 "busy. At t + 500 the LPs withdraw 99.999 % of P (no trade, no flow). Both H "
                                                 "positions hold to their 6 h deadlines, da and da + 400, and sell there for "
                                                 "under a tenth of the cost: total losses, settled at O's events da + 10 and "
                                                 "da + 410. H's file changes pool_cooldown_h from 24 to 0.01 at da + 30 "
                                                 "(after the first settle; a later minute reloads it before the second). A "
                                                 "whale refills P at da + 450. P triggers at da + 800, past the 0.01 h end "
                                                 "(about da + 547) and inside the 24 h one: H's rows are 'pool cooldown'.")
    c.trades(c.pool("O"), "reversed", 5_000 * SOL, 10**15, [(t0, 0, "small buyer", "buy", SOL, False)] +
             [(da + d, 0, "small buyer", "buy", SOL, False) for d in (10, 20, 30, 410, 420)])
    p = c.pool("P")
    legs = trigger(c, p, t, 400 * SOL)
    legs = c.more(p, "reversed", legs[-1], [(t + 50, 0, "whale", "buy", 400 * SOL, False), (t + 52, 0, "buyer 3", "buy", SOL // 10, False),
                                            (t2 - 30, 0, "buyer", "buy", 2 * SOL, False), (t2, 0, "seller", "sell", 40 * SOL, False)])
    w = c.withdraw(p, legs[-1], t + 500, 0, 999_990, 1_000_000)
    legs = c.more(p, "reversed", w, [(da + 450, 0, "whale 2", "buy", 400 * SOL, False), (da + 770, 0, "buyer", "buy", 2 * SOL, False),
                                     (da + 800, 0, "seller", "sell", 20 * SOL, False)])
    c.flip("H", da + 30, risk(pool_cooldown_h="0.01"))
    case = c.build()
    a, b = c.one("exits", key=p, t=t, variant="flow300_H6"), c.one("exits", key=p, t=t2, variant="flow300_tp100_H6")
    ok(a["exit_slot"] == da + 2 and a["proceeds"] < 0.1 * a["cost"], c, f"flow300_H6 {a}")
    ok(b["exit_slot"] == db + 2 and b["proceeds"] < 0.1 * b["cost"], c, f"flow300_tp100_H6 {b}")
    for v in ("flow300_H6", "flow300_tp100_H6"):
        r = c.one("signals", key=p, t=da + 800, variant=v)
        ok((r["status"], r["reason"]) == ("skipped", "pool cooldown"), c, f"H {v} at da + 800 {r}")
    return case


def the_wallet_keeps_its_last_fifty():
    """The paper wallet (engine.py:1252-1283): newest first, 50 deep (`deque(maxlen=50)`): 26 positions buy and sell, 52
    transactions; the file at the end of data holds the last 50. Its balance is 10 SOL + the realized P&L."""
    c = Case("the_wallet_keeps_its_last_fifty", "max_open_positions 30, max_exposure 20 SOL. 26 pools trigger 100 slots apart; "
                                                "each K3 position enters and closes (by time, or at the end of data). 26 buys "
                                                "and 26 sells: the last wallet file holds the newest 50, newest first; P0's "
                                                "buy is dropped.",
             k3=risk(max_open_positions=30, max_exposure_sol="20"))
    t = 450_160_000
    for i in range(26):
        trigger(c, c.pool(f"P{i}"), t + 100 * i, 400 * SOL)
    case = c.build()
    walls = [w for w in c.ops if w[0] == "wallet"]
    last = json.loads(walls[-1][2])
    ok(len(c.rows("orders", status="filled")) == 26 and len(last["txs"]) == 50, c, f"{len(last['txs'])} txs")
    slots = [x["slot"] for x in last["txs"]]
    ok(slots == sorted(slots, reverse=True) and min(slots) > t + 2, c, f"newest first, P0's buy at t + 2 dropped: {slots[-3:]}")
    ok(last["balance_lamports"] == 10 * SOL + sum(x["pnl"] for x in c.rows("exits")), c, "the balance")
    return case


# ---------------------------------------------------------------------------------------------- from the review of #384
# (a workflow of four lenses, each finding checked by two skeptics: paths the cases above left open, each pinning a mutant
# that passed them)

MIN_START = 450_175_206  # a minute's first slot by C1.block_time (the next starts at 450,175,431)
FIRST_0922 = 450_131_836  # the first dated slot of 2026-09-22 by C1.block_time


def sig(c, key, t=None, family="F7", variant="tp4_T600"):
    m = {"key": key, "family": family, "variant": variant} | ({} if t is None else {"t": t})
    r = c.one("signals", **m)
    return r["status"], r["reason"]


def a_quiet_failed_entry_books_the_day_before():
    """A failed quiet entry whose entry slot is past midnight is made before the next dated slot's clock (engine.py:310,
    356): its fee goes into the old day, which that slot then resets (spec §2.4)."""
    c = Case("a_quiet_failed_entry_books_the_day_before", "daily_loss_limit 1,000 lamports. ES triggers at t (2026-09-21); an "
                                                          "undated whale buy at t + 1 (no clock, no reset); the quiet entry at "
                                                          "t + 2 (past midnight) fails on its slippage and books its fee into "
                                                          "09-21; O's event at t + 66 is 09-22's first dated slot and resets. P "
                                                          "at t + 300: entered.", k3=risk(daily_loss_limit_sol="0.000001"))
    t = FIRST_0922 - 2
    assert _day(C1.block_time(t)) == "2026-09-21" and _day(C1.block_time(t + 2)) == "2026-09-22"
    es = c.pool("ES")
    legs = trigger(c, es, t, 400 * SOL)
    c.more(es, "reversed", legs[-1], [(t + 1, 0, "whale", "buy", 100 * SOL, False)])
    c.evs = [replace(e, block_time=None) if e.slot == t + 1 else e for e in c.evs]
    c.trades(c.pool("O"), "reversed", 5_000 * SOL, 10**15, [(t + 66, 0, "small buyer", "buy", SOL, False)])
    trigger(c, c.pool("P"), t + 300, 400 * SOL)
    case = c.build()
    o = c.one("orders", key=es)
    ok((o["status"], o["day"]) == ("entry_failed", "2026-09-21"), c, f"ES {o}")
    ok(sig(c, c.key("P"))[0] == "entered", c, "P")
    return case


def a_loss_settled_after_midnight_is_the_new_days():
    """The reset comes first in _clock (engine.py:455-460), before step 1: a stop loss settled at the first dated slot of
    a new day is that day's loss."""
    c = Case("a_loss_settled_after_midnight_is_the_new_days", "daily_loss_limit 0.001 SOL. SL's stop loss is decided before "
                                                             "midnight and settled at 09-22's first dated slot. P at 09-22 + 600: "
                                                             "'daily loss limit'.", k3=risk(daily_loss_limit_sol="0.001"))
    t = FIRST_0922 - 300
    sl = c.pool("SL")
    legs = trigger(c, sl, t, 400 * SOL)
    c.more(sl, "reversed", legs[-1], [(FIRST_0922 - 30, 0, "seller 2", "sell", 20 * SOL, False),
                                      (FIRST_0922 - 29, 0, "seller 3", "sell", 20 * SOL, False),
                                      (FIRST_0922 - 20, 0, "buyer 4", "buy", SOL // 10, False),
                                      (FIRST_0922, 0, "buyer 3", "buy", SOL // 10, False)])
    trigger(c, c.pool("P"), FIRST_0922 + 600, 400 * SOL)
    case = c.build()
    x = c.one("exits", key=sl)
    ok(x["exit_reason"] == "stop_loss" and x["day"] == "2026-09-22" and x["pnl"] < -1_000_000, c, f"SL {x}")
    ok(sig(c, c.key("P")) == ("skipped", "daily loss limit"), c, "P")
    return case


def an_entry_reread_holds_for_the_gate():
    """The entry's re-read stores every group's risk (engine.py:869, 532-537): the gate after it, in the same minute, uses
    it. The minute at s1 loads an invalid file (P2's trigger at s1 skipped); valid again before P1's entry at s1 + 1."""
    c = Case("an_entry_reread_holds_for_the_gate", "K3's file loses size_sol at s1 (a minute's first slot; P2's row there "
                                                   "is skipped with risk.load's reason) and is valid again at s1 + 2; P1's "
                                                   "entry at s1 + 1 reads the valid file, which the gate then keeps: P3 at "
                                                   "s1 + 50, the same minute, enters.")
    s1 = MIN_START
    assert minute(s1 - 31) == minute(s1 - 1) < minute(s1) == minute(s1 + 50)
    p1 = c.pool("P1")
    legs = trigger(c, p1, s1 - 1, 400 * SOL)
    c.more(p1, "reversed", legs[-1], [(s1 + 1, 0, "buyer 3", "buy", SOL // 10, False)])
    trigger(c, c.pool("P2"), s1, 400 * SOL)
    c.trades(c.pool("O"), "reversed", 5_000 * SOL, 10**15, [(s1 + 2, 0, "small buyer", "buy", SOL, False)])
    c.flip("K3", s1, risk(size_sol=None))
    c.flip("K3", s1 + 2, risk())
    trigger(c, c.pool("P3"), s1 + 50, 400 * SOL)
    case = c.build()
    ok(sig(c, c.key("P2")) == ("skipped", "risk config invalid: KeyError 'size_sol'"), c, "P2")
    ok(c.one("orders", key=p1)["status"] == "filled", c, "P1 filled")
    ok(sig(c, c.key("P3"))[0] == "entered", c, "P3")
    return case


def a_missing_file_that_appears():
    """A missing file appears: the minutes after it load it (engine.py:471)."""
    c = Case("a_missing_file_that_appears", "K3's file is missing at the start and appears at t + 100: P1 at t and P2 at "
                                            "t + 150 (no new minute since) are 'risk config unreadable: FileNotFoundError'; "
                                            "P3 at t + 400 (a later minute) enters.")
    c.risk["K3"] = [[None, None]]
    t = MIN_START - 225 + 40
    assert minute(t - 30) == minute(t) == minute(t + 150) < minute(t + 400 - 30)
    trigger(c, c.pool("P1"), t, 400 * SOL)
    c.trades(c.pool("O"), "reversed", 5_000 * SOL, 10**15, [(t + 100, 0, "small buyer", "buy", SOL, False)])
    c.flip("K3", t + 100, risk())
    trigger(c, c.pool("P2"), t + 150, 400 * SOL)
    trigger(c, c.pool("P3"), t + 400, 400 * SOL)
    case = c.build()
    for p in ("P1", "P2"):
        ok(sig(c, c.key(p)) == ("skipped", "risk config unreadable: FileNotFoundError"), c, p)
    ok(sig(c, c.key("P3"))[0] == "entered", c, "P3")
    return case


def _graduation_case(name, why, k3):
    c = Case(name, why, k3=k3)
    t = 450_160_000
    pg = c.pool("PG", "normal", create_slot=t + 5_000)
    g = c.token("G", pool=pg)
    c.pools[0]["mint"] = g
    C2.nearly_complete(c, g, t)
    c.trades(pg, "normal", 10**15, 80 * SOL, [(t + 5_000, 0, "first buyer", "buy", SOL, False), (t + 5_001, 0, "buyer", "buy", SOL, False)])
    trigger(c, c.pool("P"), t + 300, 400 * SOL)
    trigger(c, c.pool("P2"), t + 5_300, 400 * SOL)
    return c, g, t


def a_position_waiting_for_its_pool_is_open():
    """A curve position whose own buy completed the curve waits for its pool and stays open (engine.py:837, spec §4 item
    10): it counts for max open."""
    c, g, t = _graduation_case("a_position_waiting_for_its_pool_is_open", "max_open_positions 1. G's curve trigger: our buy "
                                                                          "completes the curve; the position waits for PG "
                                                                          "(created at t + 5,000). P at t + 300: 'max open "
                                                                          "positions'. After the sale at PG's open + 1, P2 at "
                                                                          "t + 5,300 enters.", risk(max_open_positions=1))
    case = c.build()
    x = c.one("exits", key=g)
    ok(x["exit_reason"] == "graduation" and x["exit_slot"] == t + 5_001, c, f"G {x}")
    ok(sig(c, c.key("P")) == ("skipped", "max open positions"), c, "P")
    ok(sig(c, c.key("P2"))[0] == "entered", c, "P2")
    return case


def a_position_waiting_for_its_pool_has_exposure():
    """The waiting position's venue cost counts in the exposure (engine.py:841)."""
    c, g, t = _graduation_case("a_position_waiting_for_its_pool_has_exposure", "max_exposure 0.5006 SOL: G's waiting "
                                                                               "position's 0.5 SOL plus P's 0.5 is above it: P "
                                                                               "at t + 300 'max exposure'; P2 after the sale "
                                                                               "enters.", risk(max_exposure_sol="0.5006"))
    case = c.build()
    ok(sig(c, c.key("P")) == ("skipped", "max exposure"), c, "P")
    ok(sig(c, c.key("P2"))[0] == "entered", c, "P2")
    return case


def _curve_retrigger(c, mint, at):
    """A curve trigger at `at` from the mint's last state: buys of 20T at at - 40 and at - 30, then a sell paying about
    4 SOL."""
    from backtest.venues import curve_buy_exact_out, curve_sell
    legs = [e.leg for e in c.evs if e.key == mint and e.slot < at]
    start = s = max(legs, key=lambda l: (l.slot, l.tx)).post
    for n in (20 * T, 20 * T):
        s = curve_buy_exact_out(s, n).state
    lo, hi = 1, s.vt
    while lo < hi:
        m = (lo + hi) // 2
        f = curve_sell(s, m)
        if f is not None and f.curve_quote >= 4 * SOL:
            hi = m
        else:
            lo = m + 1
    return c.curve(mint, [(at - 40, 0, "buyer", "buy", 20 * T), (at - 30, 0, "buyer 2", "buy", 20 * T), (at, 0, "seller", "sell", lo)],
                   start=start)


def a_curve_with_risk_on():
    """A curve position with risk on (RC3's paths): the file's size, and the pool cooldown on the mint after a total
    loss."""
    c = Case("a_curve_with_risk_on", "size 0.3, pool_cooldown_h 0.01. CB's curve trigger: K3 buys 0.3 SOL. A dump takes "
                                     "the price under a tenth: the stop loss sells at a total loss, the mint cooled for 36 "
                                     "s. A whale buys back; CB triggers again at t + 90 ('pool cooldown') and t + 300 "
                                     "(entered).", k3=risk(size_sol="0.3", pool_cooldown_h="0.01"))
    t = 450_160_000
    cb = c.token("CB")
    legs = C1._curve_trigger(c, cb, t, rq=70 * SOL)
    s = legs[-1].post
    dump = s.vt * s.vq // (20 * SOL) - s.vt
    legs = c.curve(cb, [(t + 10, 0, "dumper", "sell", dump), (t + 11, 0, "seller 3", "sell", T), (t + 20, 0, "buyer 3", "buy", T)],
                   start=s)
    c.buy_in(cb, legs[-1].post, t + 30, 0, "whale", 35 * SOL)
    _curve_retrigger(c, cb, t + 90)
    _curve_retrigger(c, cb, t + 300)
    case = c.build()
    o = c.one("orders", key=cb, t=t)
    x = c.one("exits", key=cb, t=t)
    ok(o["size"] == 300_000_000 and x["proceeds"] < 0.1 * x["cost"], c, f"CB {o} {x}")
    ok(sig(c, cb, t + 90) == ("skipped", "pool cooldown") and sig(c, cb, t + 300)[0] == "entered", c, "CB again")
    return case


def n2_passes_the_daily_loss_limit():
    """N2 rows return before the daily loss limit (engine.py:831-832)."""
    c = Case("n2_passes_the_daily_loss_limit", "daily_loss_limit 1,000 lamports. SL stops out at a loss far above it. P "
                                               "at t + 300: its F7 row is 'daily loss limit', its N2 row for Q enters.",
             n2_k=1, k3=risk(daily_loss_limit_sol="0.000001"))
    t = 450_175_000
    c.trades(c.pool("Q"), "reversed", 1_000 * SOL, 10**15, [(t - 300, 0, "small buyer", "buy", SOL, False),
                                                           (t + 100, 0, "small buyer", "buy", SOL, False)])
    sl = c.pool("SL")
    legs = trigger(c, sl, t, 5_000 * SOL)
    c.more(sl, "reversed", legs[-1], [(t + 10, 0, "seller 2", "sell", 300 * SOL, False), (t + 11, 0, "seller 3", "sell", 300 * SOL, False),
                                      (t + 20, 0, "buyer 3", "buy", SOL // 10, False)])
    trigger(c, c.pool("P"), t + 300, 1_050 * SOL)
    case = c.build()
    ok(sig(c, c.key("P")) == ("skipped", "daily loss limit"), c, "P's F7 row")
    ok(c.one("signals", family="N2_F7", t=t + 300)["status"] == "entered", c, "P's N2 row")
    return case


def n2_passes_a_pool_cooldown():
    """N2 rows return before the pool cooldown (engine.py:831-836)."""
    c = Case("n2_passes_a_pool_cooldown", "pool_cooldown_h 0.01. Q stops out at a total loss and is refilled. P triggers "
                                          "inside Q's cooldown; its N2 row picks Q: entered.", n2_k=1, k3=risk(pool_cooldown_h="0.01"))
    t = 450_160_000
    q = c.pool("Q")
    legs = trigger(c, q, t, 400 * SOL)
    c.more(q, "reversed", legs[-1], [(t + 5, 0, "dumper", "sell", 350 * SOL, False), (t + 9, 0, "buyer 3", "buy", SOL // 10, False),
                                     (t + 20, 0, "whale", "buy", 350 * SOL, False)])
    trigger(c, c.pool("P"), t + 60, 420 * SOL)
    case = c.build()
    x = c.one("exits", key=q, family="F7")
    ok(x["proceeds"] < 0.1 * x["cost"], c, f"Q's total loss {x}")
    r = c.one("signals", family="N2_F7", t=t + 60)
    ok((r["key"], r["status"]) == (q, "entered"), c, f"P's N2 row {r}")
    return case


def max_open_from_a_later_minute():
    """A file changed between minutes holds from the next minute's reload (engine.py:471)."""
    c = Case("max_open_from_a_later_minute", "max_open 5 at the start; P1 opens. The file changes to max_open 1 at t + 300; "
                                             "the minute before t + 470 reloads it: P2 at t + 470 is 'max open positions'.")
    t = 450_175_000
    assert minute(t + 300) < minute(t + 470 - 30)
    trigger(c, c.pool("P1"), t, 1_050 * SOL)
    c.trades(c.pool("O"), "reversed", 5_000 * SOL, 10**15, [(t + 100, 0, "small buyer", "buy", SOL, False),
                                                           (t + 300, 0, "small buyer", "buy", SOL, False)])
    c.flip("K3", t + 300, risk(max_open_positions=1))
    trigger(c, c.pool("P2"), t + 470, 1_050 * SOL)
    case = c.build()
    ok(sig(c, c.key("P2")) == ("skipped", "max open positions"), c, "P2")
    return case


def an_entry_reread_reaches_the_other_group():
    """_load_risk at an entry reads every group (engine.py:532-537): K3's curve entry reads H's kill switch for H too."""
    c = Case("an_entry_reread_reaches_the_other_group", "H's file turns its kill switch on at x + 2, after the minute's load; "
                                                        "K3's curve entry at x + 2 reads it for H too, so H's rows on P2 at "
                                                        "x + 50 (the same minute) are 'kill switch' and K3's row enters. CU is "
                                                        "a curve: H trades no curve, so H has no entry of its own.")
    t0, x = 450_150_000, MIN_START + 60
    assert minute(x - 40) == minute(x) == minute(x + 50)
    c.trades(c.pool("O"), "reversed", 5_000 * SOL, 10**15, [(t0, 0, "small buyer", "buy", SOL, False)])
    cu = c.token("CU")
    legs = C1._curve_trigger(c, cu, x)
    c.curve(cu, [(x + 2, 0, "buyer 3", "buy", T)], start=legs[-1].post)
    c.flip("H", x + 2, risk(kill_switch=True))
    trigger(c, c.pool("P2"), x + 50, 400 * SOL)
    case = c.build()
    ok(sig(c, c.key("P2"))[0] == "entered", c, "K3 on P2")
    for v in ("flow300_H6", "flow300_tp100_H6"):
        ok(sig(c, c.key("P2"), variant=v) == ("skipped", "kill switch"), c, f"H {v}")
    return case


def h_variants_count_each_other():
    """A group's two variants count each other's pending entries (engine.py:838)."""
    c = Case("h_variants_count_each_other", "H's max_open 1: P's flow300_H6 row enters (pending), its flow300_tp100_H6 row "
                                            "then sees one pending: 'max open positions'.", h=risk(max_open_positions=1))
    t0, t = 450_150_000, 450_165_000
    c.trades(c.pool("O"), "reversed", 5_000 * SOL, 10**15, [(t0, 0, "small buyer", "buy", SOL, False)])
    trigger(c, c.pool("P"), t, 400 * SOL)
    case = c.build()
    ok(sig(c, c.key("P"), variant="flow300_H6")[0] == "entered", c, "flow300_H6")
    ok(sig(c, c.key("P"), variant="flow300_tp100_H6") == ("skipped", "max open positions"), c, "flow300_tp100_H6")
    return case


def the_size_is_the_entry_reads():
    """The size is the entry's own read (engine.py:869, 880): the file changes between the signal and the entry, in one
    minute."""
    c = Case("the_size_is_the_entry_reads", "P triggers at t; the file changes to size 0.3 at its entry slot t + 2, in the "
                                            "same minute: the order is 0.3 SOL.")
    t = 450_175_120
    assert minute(t - 30) == minute(t) == minute(t + 2)
    p = c.pool("P")
    legs = trigger(c, p, t, 400 * SOL)
    c.more(p, "reversed", legs[-1], [(t + 2, 0, "buyer 3", "buy", SOL // 10, False)])
    c.flip("K3", t + 2, risk(size_sol="0.3"))
    case = c.build()
    ok(c.one("orders", key=p)["size"] == 300_000_000, c, "P's size")
    return case


def busy_is_t_after_an_entry_skip():
    """After 'at entry: …' busy is t (engine.py:876), not the entry slot: a row of the same key in the entry slot is
    decided by the gate, not by busy."""
    c = Case("busy_is_t_after_an_entry_skip", "P1 at t: its N2 row for Q pends to t + 2; the kill switch from t + 2: both "
                                              "entries are skipped at entry. P2 triggers at t + 2: its N2 row for Q is "
                                              "'kill switch' (busy is t < t + 2), not 'busy'.", n2_k=2)
    t = 450_175_120
    c.trades(c.pool("Q"), "reversed", 1_000 * SOL, 10**15, [(t - 300, 0, "small buyer", "buy", SOL, False)])
    trigger(c, c.pool("P1"), t, 1_050 * SOL)
    trigger(c, c.pool("P2"), t + 2, 1_050 * SOL)
    c.trades(c.pool("O"), "reversed", 5_000 * SOL, 10**15, [(t + 3, 0, "b", "buy", SOL, False)])
    c.flip("K3", t + 2, risk(kill_switch=True))
    case = c.build()
    q = c.key("Q")
    ok(c.one("orders", key=q, t=t, family="N2_F7")["reason"] == "at entry: kill switch", c, "Q at t")
    ok(sig(c, q, t + 2, family="N2_F7") == ("skipped", "kill switch"), c, "Q at t + 2")
    return case


def a_flip_lands_before_the_quiet_entries():
    """FlipFeed's timing (FORMAT): a flip lands when the feed hands out its event, before the quiet entries due before it
    (engine.py:310): P's quiet entry at t + 2 reads a kill switch flipped at t + 50, the next event."""
    c = Case("a_flip_lands_before_the_quiet_entries", "P triggers at t; no event until O's at t + 50, where K3's kill switch "
                                                      "flips on: P's quiet entry at t + 2 (and its N2 entry) read it: 'at "
                                                      "entry: kill switch'.", n2_k=1)
    t = 450_175_120
    c.trades(c.pool("Q"), "reversed", 1_000 * SOL, 10**15, [(t - 300, 0, "small buyer", "buy", SOL, False)])
    trigger(c, c.pool("P"), t, 1_050 * SOL)
    c.trades(c.pool("O"), "reversed", 5_000 * SOL, 10**15, [(t + 50, 0, "b", "buy", SOL, False)])
    c.flip("K3", t + 50, risk(kill_switch=True))
    case = c.build()
    ok({(o["family"], o["reason"]) for o in c.rows("orders")} == {("F7", "at entry: kill switch"), ("N2_F7", "at entry: kill switch")},
       c, f"orders {c.rows('orders')}")
    return case


def a_flip_lands_before_the_slot_before_ends():
    """FlipFeed's timing (FORMAT): a flip lands before the engine ends the slot before its event, so a minute that starts
    in that slot reads it: t starts a minute, the kill switch flips at t + 1 (the next event), and t's minute loads it."""
    s = MIN_START
    c = Case("a_flip_lands_before_the_slot_before_ends", "P triggers at t, a minute's first slot; K3's kill switch flips on "
                                                         "at t + 1, O's event: t's own minute reload reads it, so P's row is "
                                                         "'kill switch'.")
    assert minute(s - 30) < minute(s)
    trigger(c, c.pool("P"), s, 400 * SOL)
    c.trades(c.pool("O"), "reversed", 5_000 * SOL, 10**15, [(s + 1, 0, "b", "buy", SOL, False), (s + 300, 0, "b", "buy", SOL, False)])
    c.flip("K3", s + 1, risk(kill_switch=True))
    case = c.build()
    ok(sig(c, c.key("P")) == ("skipped", "kill switch"), c, "P")
    return case


CASES = [max_open_counts_pending_and_open, max_exposure_at_its_edge, kill_switch_stops_n2_too, family_off_leaves_n2,
         files_invalid_or_missing, at_entry_the_file_is_read_again, size_from_the_file_n2_at_half_a_sol,
         daily_loss_blocks_until_midnight, cooldown_after_a_total_loss, daily_loss_is_signed_and_its_limit_inclusive,
         daily_loss_one_lamport_over_passes, failed_entry_books_its_fee, n2_positions_do_not_count, h_group_has_its_own_file,
         at_entry_the_limits_are_not_checked_again, cooldown_ends_at_its_slot, exposure_counts_the_open_cost,
         n2_pending_does_not_count, other_groups_do_not_count, n2_loss_is_not_booked, cooldown_across_a_clock_change,
         a_sale_of_exactly_a_tenth_is_no_total_loss, the_cooldown_keeps_the_later_end, the_wallet_keeps_its_last_fifty,
         a_cooldown_beyond_64_bits_is_forever, a_quiet_failed_entry_books_the_day_before,
         a_loss_settled_after_midnight_is_the_new_days, an_entry_reread_holds_for_the_gate, a_missing_file_that_appears,
         a_position_waiting_for_its_pool_is_open, a_position_waiting_for_its_pool_has_exposure, a_curve_with_risk_on,
         n2_passes_the_daily_loss_limit, n2_passes_a_pool_cooldown, max_open_from_a_later_minute,
         an_entry_reread_reaches_the_other_group, h_variants_count_each_other, the_size_is_the_entry_reads,
         busy_is_t_after_an_entry_skip, a_flip_lands_before_the_quiet_entries, a_flip_lands_before_the_slot_before_ends]


def table():
    return {"format": FORMAT, "spec": "research/m1-c3-risk-spec-2026-10-19.md", "cases": [f() for f in CASES]}


def generate(path=PATH):
    with open(path, "w") as f:
        f.write(C2.dumps(table()))


if __name__ == "__main__":
    generate()
    print(f"wrote {PATH}", file=sys.stderr)
