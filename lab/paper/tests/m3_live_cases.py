"""The golden cases of M3-08 (research/m3-08-core-live-api-spec-2026-10-19.md §3): C2's cases with what a live feed learns
while it reads, applied as LiveFeed applies it, and the journal writes the real paper engine (paper/engine.py, unchanged)
makes on them with risk inert. lab/live's paper test runs the same cases through lab/core's live API (learn_token,
learn_pool, set_epoch_ms) and must make the same writes.

  M3_CASES_REGEN=1 pytest paper/tests/test_live_cases.py      # rewrite data/m3_live_cases.json from the engine
  python -m paper.tests.m3_live_cases                          # the same

A case's `learn` list says what the feed learns and when: an item applies right before the first event whose slot is
at least its at_slot (after the last event when there is none). LiveFeed learns while it reads a segment, before it yields
that segment's events, so the engine's end of the slot before (run when that event arrives) already sees it.
"""

import json
import os
import re
import sys
import tempfile
from dataclasses import asdict

from paper.feed import PoolInfo, TokenInfo
from paper.journal import Journal
from paper.tests import c1b_signal_cases as C1
from paper.tests import c2_position_cases as C2

SOL = C1.SOL
T = C1.T
PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "m3_live_cases.json")
CASE = (*C2.CASE[:-2], "learn", "cost_scenario", "expect_ops")

FORMAT = {
    **{k: v for k, v in C2.FORMAT.items() if k not in ("case", "regenerate")},
    "case": "name, why, clock, dev_bounds, groups, labels, tokens, pools, events (as in c2_position_cases.json FORMAT), "
            "learn, cost_scenario, expect_ops. tokens and pools are the snapshot: what the feed knows at the start.",
    "learn": "what the feed learns while it reads, in order: {at_slot, token} (a tokens row: the token is replaced), "
             "{at_slot, pool} (a pools row: added when the pool is not known yet, as a SOL market when its quote_class is "
             "sol or reversed) or {at_slot, epoch, ms} (the epoch's ms per slot in the engine's clock). An item applies "
             "right before the first event whose slot is >= at_slot, after the last event when there is none (lab/core: "
             "learn_token, learn_pool, set_epoch_ms before that push).",
    "regenerate": "M3_CASES_REGEN=1 pytest paper/tests/test_live_cases.py (or python -m paper.tests.m3_live_cases).",
}


class LearnFeed(C1.CaseFeed):
    """A case's feed that learns while it reads (the case's `learn`), as LiveFeed does: tokens replaced, pools added once
    (a SOL market into `pools`), an epoch's ms per slot set in the engine's clock (LiveFeed's clock is the engine's)."""

    def __init__(self, case):
        super().__init__(case)
        self.learn = list(case["learn"])
        self.classed = {p["pool"] for p in case["pools"]}  # LiveFeed's pool_class: every pool it knows, of any class
        self.clock = None  # the engine's (run sets it)

    def _apply(self, x):
        if "token" in x:
            t = x["token"]
            self.tokens[t["mint"]] = TokenInfo(t["mint"], t["create_slot"], t["creator"], t["mayhem"], t["quote_mint"],
                                               t["complete_slot"], t["pool"])
        elif "pool" in x:
            p = x["pool"]
            if p["pool"] not in self.classed:
                self.classed.add(p["pool"])
                if p["quote_class"] in C1.SOL_MARKETS:
                    self.pools[p["pool"]] = PoolInfo(**p)
        else:
            self.clock.per_epoch[int(x["epoch"])] = float(x["ms"])

    def events(self):
        todo = list(self.learn)
        for ev in self.evs:
            while todo and todo[0]["at_slot"] <= ev.slot:
                self._apply(todo.pop(0))
            yield ev
        for x in todo:
            self._apply(x)


def run(case):
    """The engine's journal writes of a case on its learning feed, and the engine after the run."""
    with tempfile.TemporaryDirectory(prefix="m3-case-") as d:
        j = C2.RecJournal(d)
        feed = LearnFeed(case)
        eng = C1.engine(case, j, feed)
        feed.clock = eng.clock
        eng.run()
        ops = j.ops
        j.close()
    return ops, eng


class Case(C2.Case):
    """C2's case builder with a learn list: a token or pool built as usual is moved out of the snapshot into `learn`."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.learn = []

    def learn_token(self, mint, at_slot, **change):
        """The token leaves the snapshot (or, with `keep`, stays there as it is) and is learned at at_slot with `change`."""
        keep = change.pop("keep", False)
        i = [t["mint"] for t in self.tokens].index(mint)
        tok = self.tokens[i] if keep else self.tokens.pop(i)
        self.learn.append({"at_slot": at_slot, "token": {**tok, **change}})

    def learn_pool(self, pool, at_slot):
        i = [p["pool"] for p in self.pools].index(pool)
        self.learn.append({"at_slot": at_slot, "pool": self.pools.pop(i)})

    def learn_epoch(self, epoch, ms, at_slot):
        self.learn.append({"at_slot": at_slot, "epoch": epoch, "ms": ms})

    def build(self):
        case = C1.Case.build(self)
        assert [x["at_slot"] for x in self.learn] == sorted(x["at_slot"] for x in self.learn), f"{self.name}: learn order"
        case["learn"] = self.learn
        with tempfile.TemporaryDirectory(prefix="m3-case-") as d:  # the engine's own groups and cost scenario text
            j = Journal(d)
            eng = C1.engine(case, j)
            case["groups"] = C1.groups_state(eng)
            case["cost_scenario"] = json.dumps(asdict(eng.scn), sort_keys=True)
            j.close()
        case["expect_ops"], self.eng = run(case)
        self.ops = case["expect_ops"]
        return case


ok = C2.ok


def first_slot(c, key):
    return min(e.slot for e in c.evs if e.key == key)


# ---------------------------------------------------------------------------------------------- the cases

def pool_learned_mid_run():
    """Pools and a token absent from the snapshot, learned with their first trades (live.py:420-485): F7 sees them from
    then on, and a learn that comes with the next slot's first event is seen at the end of the slot before."""
    c = Case("pool_learned_mid_run", "P1 is in the snapshot (the control). P2 is learned with its first trade (t + 70): its "
                                     "trigger at t + 100 enters. P3 is learned with the first event after its trigger slot "
                                     "(O at t + 201): the end of t + 200 runs when that event arrives, after the learn, so "
                                     "P3's trigger enters too. P4 is learned with the event after that (O at t + 302), past "
                                     "the end of its trigger slot t + 300: no row for it. C, a curve token created after the "
                                     "snapshot, is learned with its first trade: its trigger at t + 400 enters.", n2_k=0)
    t = 450_160_000
    p1, p2, p3, p4, o = c.pool("P1"), c.pool("P2"), c.pool("P3"), c.pool("P4"), c.pool("O")
    for p, at in ((p1, t), (p2, t + 100), (p3, t + 200), (p4, t + 300)):
        reversed_trigger(c, p, at, 400 * SOL)
    c.trades(o, "reversed", 5_000 * SOL, 10**15, [(t + 201, 0, "small buyer", "buy", SOL, False),
                                                   (t + 301, 0, "small buyer", "buy", SOL, False),
                                                   (t + 302, 0, "small buyer", "buy", SOL, False),
                                                   (t + 3_000, 0, "small buyer", "buy", SOL, False)])
    cm = c.token("C", create_slot=t + 300)
    C1._curve_trigger(c, cm, t + 400)
    c.learn_pool(p2, first_slot(c, p2))
    c.learn_pool(p3, t + 201)
    c.learn_pool(p4, t + 302)
    c.learn_token(cm, first_slot(c, cm))
    c.learn.sort(key=lambda x: x["at_slot"])
    case = c.build()
    for key in (p1, p2, p3, cm):
        ok(c.one("orders", key=key)["status"] == "filled", c, f"{key} entered")
        ok(len(c.rows("exits", key=key)) == 1, c, f"{key} exited")
    ok(not c.rows("signals", key=p4), c, "P4: learned after its trigger slot ended, no row")
    return case


def graduation_learned_after_the_entry():
    """What the feed knows when our curve completes decides the sale (engine.py:977-1000): a completion, a migration
    and a pool learned after our entry send it to the learned pool, not the seed pool."""
    c = Case("graduation_learned_after_the_entry", "H's snapshot row has no completion and no pool, and its pool PH is not "
                                                   "known. Our entry (t + 2) fills when O's event at t + 3 arrives; the feed "
                                                   "then learns H's completion at t + 10 and migration to PH, and PH "
                                                   "(created t + 150), with the whale's event at t + 10. That buy, re-run "
                                                   "after ours, completes the curve: the sale waits for PH and fills at its "
                                                   "open + 1, t + 151; cf_graduation is 0, as H completed at that slot.",
             n2_k=0)
    t = 450_160_000
    ph = c.pool("PH", "normal", create_slot=t + 150)
    h = c.token("H")
    c.pools[0]["mint"] = h
    legs = C1._curve_trigger(c, h, t)
    c.curve(h, [(t + 10, 0, "whale", "buy", legs[-1].post.rt - T)], start=legs[-1].post)
    c.trades(c.pool("O"), "reversed", 5_000 * SOL, 10**15, [(t + 3, 0, "small buyer", "buy", SOL, False)])
    c.trades(ph, "normal", 10**15, 80 * SOL, [(t + 150, 0, "first buyer", "buy", SOL, False), (t + 151, 0, "buyer", "buy", SOL, False)])
    c.learn_pool(ph, t + 5)
    c.learn_token(h, t + 5, keep=True, complete_slot=t + 10, pool=ph)
    case = c.build()
    x = c.one("exits", key=h)
    ok((x["entry_slot"], x["exit_reason"], x["seed_pool"], x["exit_decision_slot"], x["exit_slot"], x["cf_graduation"])
       == (t + 2, "graduation", 0, t + 150, t + 151, 0), c, f"H {x}")
    return case


def completion_learned_too_late():
    """The same completion, learned after our sim completed: the feed knew no pool then, so the sale went to the seed pool
    at once and cf_graduation is 1 (engine.py:981-990)."""
    c = Case("completion_learned_too_late", "K as H in graduation_learned_after_the_entry, but the feed learns K's "
                                            "completion and pool only with O's second event (t + 14), after the whale's "
                                            "buy completed the curve at t + 10 (that slot ends when O's first event, "
                                            "t + 12, arrives): the sale is in the seed pool at t + 11, cf_graduation 1.",
             n2_k=0)
    t = 450_160_000
    pk = c.pool("PK", "normal", create_slot=t + 150)
    k = c.token("K")
    c.pools[0]["mint"] = k
    legs = C1._curve_trigger(c, k, t)
    c.curve(k, [(t + 10, 0, "whale", "buy", legs[-1].post.rt - T)], start=legs[-1].post)
    c.trades(c.pool("O"), "reversed", 5_000 * SOL, 10**15, [(t + 12, 0, "small buyer", "buy", SOL, False),
                                                           (t + 14, 0, "small buyer", "buy", SOL, False)])
    c.trades(pk, "normal", 10**15, 80 * SOL, [(t + 150, 0, "first buyer", "buy", SOL, False), (t + 151, 0, "buyer", "buy", SOL, False)])
    c.learn_pool(pk, t + 14)
    c.learn_token(k, t + 14, keep=True, complete_slot=t + 10, pool=pk)
    case = c.build()
    x = c.one("exits", key=k)
    ok((x["exit_reason"], x["seed_pool"], x["exit_slot"], x["cf_graduation"]) == ("graduation", 1, t + 11, 1), c, f"K {x}")
    return case


def clock_learned_mid_run():
    """An epoch's ms per slot learned mid-run (live.py:637-650) sets the slots from then on; what the clock gave before
    stays (an entry slot and an exit rule, engine.py:667-672, 887-890)."""
    c = Case("clock_learned_mid_run", "Epoch 1042 starts at dev2's 267.83 ms a slot: P's trigger at t enters at t + 2 with "
                                      "tp4_T600's cap 599. The feed then learns 150 ms a slot (at t + 50): Q's trigger at "
                                      "t + 100 enters at t + 4 after it, with the cap 1,069; P's position keeps its cap.",
             n2_k=0)
    t = 450_160_000
    p, q = c.pool("P"), c.pool("Q")
    legs = reversed_trigger(c, p, t, 400 * SOL)
    c.more(p, "reversed", legs[-1], [(t + 20, 0, "buyer 3", "buy", 40 * SOL, False), (t + 30, 0, "buyer 4", "buy", SOL, False)])
    legs = reversed_trigger(c, q, t + 100, 400 * SOL)
    c.more(q, "reversed", legs[-1], [(t + 120, 0, "buyer 3", "buy", 40 * SOL, False), (t + 130, 0, "buyer 4", "buy", SOL, False)])
    c.learn_epoch(1042, 150.0, t + 50)
    case = c.build()
    for key, d, cap in ((p, 2, 599), (q, 4, 1_069)):
        o, x = c.one("orders", key=key), c.one("exits", key=key)
        ok((o["entry_slot"] - o["t"], x["cap_slots"]) == (d, cap), c, f"{key} {o} {x}")
    return case


def reversed_trigger(c, pool, t, depth, **kw):
    return C1._reversed_trigger(c, pool, t, depth, **kw)


CASES = [pool_learned_mid_run, graduation_learned_after_the_entry, completion_learned_too_late, clock_learned_mid_run]


# ---------------------------------------------------------------------------------------------- the file

def dumps(doc):
    """C2's layout, with every learn item on one line too."""
    flat = {}

    def line(x):
        flat[f'"@{len(flat)}@"'] = json.dumps(x)
        return f"@{len(flat) - 1}@"

    cases = [{**c, "tokens": [line(t) for t in c["tokens"]], "pools": [line(p) for p in c["pools"]],
              "events": [line(e) for e in c["events"]], "learn": [line(x) for x in c["learn"]],
              "expect_ops": [line(o) for o in c["expect_ops"]]} for c in doc["cases"]]
    return re.sub(r'"@\d+@"', lambda m: flat[m.group(0)], json.dumps({**doc, "cases": cases}, indent=1)) + "\n"


def generate(path=PATH):
    """Build and run every case, and write the file with the engine's writes."""
    out = [{k: case[k] for k in CASE} for case in (f() for f in CASES)]
    doc = {"format": FORMAT, "spec": "research/m3-08-core-live-api-spec-2026-10-19.md §3 (lab/paper/tests/m3_live_cases.py)",
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
