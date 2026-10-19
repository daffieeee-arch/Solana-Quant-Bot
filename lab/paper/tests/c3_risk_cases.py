"""The golden cases of M1 C3-2 (research/m1-c3-risk-spec-2026-10-19.md §2.2-§2.5, §5): small feeds that the real paper
engine (paper/engine.py, unchanged) runs with risk ON, from each group's own risk file, and whose journal writes the
Rust route must reproduce one by one. test_risk_cases.py asserts that the engine still writes exactly the stored ones;
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
from dataclasses import asdict

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
        j = C2.RecJournal(os.path.join(d, "journal"))
        eng = engine(case, j)
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


CASES = [max_open_counts_pending_and_open, max_exposure_at_its_edge, kill_switch_stops_n2_too, family_off_leaves_n2,
         files_invalid_or_missing, at_entry_the_file_is_read_again, size_from_the_file_n2_at_half_a_sol,
         daily_loss_blocks_until_midnight, cooldown_after_a_total_loss, daily_loss_is_signed_and_its_limit_inclusive,
         daily_loss_one_lamport_over_passes, failed_entry_books_its_fee, n2_positions_do_not_count, h_group_has_its_own_file,
         at_entry_the_limits_are_not_checked_again, cooldown_ends_at_its_slot, exposure_counts_the_open_cost,
         n2_pending_does_not_count, other_groups_do_not_count, n2_loss_is_not_booked, cooldown_across_a_clock_change]


def table():
    return {"format": FORMAT, "spec": "research/m1-c3-risk-spec-2026-10-19.md", "cases": [f() for f in CASES]}


def generate(path=PATH):
    with open(path, "w") as f:
        f.write(C2.dumps(table()))


if __name__ == "__main__":
    generate()
    print(f"wrote {PATH}", file=sys.stderr)
