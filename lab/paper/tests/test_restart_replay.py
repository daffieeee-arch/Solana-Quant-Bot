"""The high-water-mark restart (paper/restart.py) on a dev slice with the paper config (K3 and group H), LAB review
point 7: a run that dies with an H position open over its HWM and restarts from a later start (the keeper removed the
oldest segments) ends with the uninterrupted run's journal (paper.journal_diff green; explained only: the
pool_seen_slot of pools first seen before the restart's start), wallets and HWM state. The H position's exit row is
there once and equals the run without the restart; busy and the daily loss after the HWM are equal.
A NAS test (LAB_NAS_TESTS=1): three engine runs over the real dev2 store."""

import json
import os

import pytest

from backtest import streams
from backtest.strategies import Clock
from paper import engine
from paper import restart as RS
from paper.engine import Engine, config_groups, load_config, load_groups
from paper.journal import Journal
from paper.journal_diff import compare
from paper.tests.test_arms_replay import HI, LO, STORE, TABLES, CrashFeed

ARMS = "variant IN ('flow300_H6', 'flow300_tp100_H6')"


def _run(journal_dir, clock, lo=LO, crash_slot=None):
    here = os.path.dirname(engine.__file__)
    _, conf = load_config(os.path.join(here, "k3-paper-2026-10-12.yaml"))
    groups = config_groups(load_groups(os.path.join(here, "h-arms-2026-10-16.yaml"))[1])
    eng = Engine(CrashFeed(STORE, lo, HI, crash_slot=crash_slot), Journal(journal_dir), conf["risk_path"],
                 variants=conf["variants"], d_ms=conf["d_ms"], q=conf["q"], n2_k=conf["n2_k"], segments=conf["segments"],
                 farm=conf["farm"], clock=clock, backup=False, dev_bounds=(LO - 400_000, HI), groups=groups, restart=True)
    try:
        eng.run()
    except RuntimeError:
        eng.journal.db.rollback()  # the process died: what it wrote after its last commit is gone
        raise
    return eng


def _content(j):
    return {t: j.rows(t) for t in TABLES}


def _pick(u, clock):
    """An H position held over minute commits (at least 600 slots), a crash in the middle of it, and a later start:
    an hour and 1,000 slots before the crash (the farm window of a replay of every pool is complete from an hour after
    its first slot, so the signals after the HWM are decided as in the run), at least 2,000 slots after the slice's
    start and before the position's trigger, and with no position of the run open over both the later start and the
    crash (that one could not be carried: restart_lost). On dev2 [450130000, 450175000): 38B8sK's arms, 1,124 slots."""
    hour = clock.slots(3_600_000, LO)
    every = u.journal.rows("exits", "tokens > 0")
    for x in sorted(u.journal.rows("exits", ARMS + " AND tokens > 0"), key=lambda r: r["entry_slot"] - r["exit_slot"]):
        if not x["exit_slot"] or x["exit_slot"] - x["entry_slot"] < 600:
            continue
        crash = (x["entry_slot"] + x["exit_slot"]) // 2
        lo2 = min(crash - hour - 1_000, x["t"] - 2_000)
        if lo2 >= LO + 2_000 and not any(y["entry_slot"] < lo2 and (y["exit_slot"] or HI) >= crash - 1_000 for y in every):
            return x, crash, lo2
    return None


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    if not os.environ.get("LAB_NAS_TESTS"):
        pytest.skip("three engine runs over the real dev2 store (> 3 GB): a NAS test, set LAB_NAS_TESTS=1 (heavy, NAS)")
    if not os.path.exists(STORE):
        pytest.skip("dev store not built")
    con = streams.open_store(STORE, threads=2, memory="1GB")
    clock = Clock.from_store(con)
    con.close()
    d = tmp_path_factory.mktemp("hwm")
    u = _run(str(d / "u"), clock)
    pick = _pick(u, clock)
    if pick is None:
        pytest.skip("no H position on the slice held long enough with room for a later start")
    x, crash, lo2 = pick
    with pytest.raises(RuntimeError, match="simulated crash"):
        _run(str(d / "r"), clock, crash_slot=crash)
    j1 = Journal(str(d / "r"))
    before, hwm = _content(j1), json.loads(j1.state(RS.HWM_KEY))
    j1.close()
    r = _run(str(d / "r"), clock, lo=lo2)
    print(f"H position {x['variant']} {x['key']} t={x['t']} entry={x['entry_slot']} exit={x['exit_slot']}; crash {crash}, "
          f"HWM {hwm['slot']}, restart from {lo2}; restart record {r.journal.state(RS.RESTARTS_KEY)}")
    return u, r, x, lo2, before, hwm


def test_the_h_position_open_over_the_hwm_exits_once_as_without_the_restart(runs):
    u, r, x, _, before, hwm = runs
    k = (x["family"], x["variant"], x["key"], x["t"])
    assert hwm["slot"] < x["exit_slot"]
    assert k in {(p["family"], p["variant"], p["key"], p["t"]) for p in before["positions"]}  # open at the HWM
    assert k not in {(e["family"], e["variant"], e["key"], e["t"]) for e in before["exits"]}
    w = "family = ? AND variant = ? AND key = ? AND t = ?"
    assert r.journal.rows("exits", w, k) == u.journal.rows("exits", w, k) and len(r.journal.rows("exits", w, k)) == 1
    assert r.journal.rows("fills", w + " AND side = 'sell'", k) == u.journal.rows("fills", w + " AND side = 'sell'", k)


def test_the_restarted_journal_equals_the_uninterrupted_one(runs):
    u, r, _, lo2, before, hwm = runs

    def seen_before_the_restart(table, key, a, b):
        if table == "signals" and a and b and {c for c in a if a[c] != b[c]} == {"pool_seen_slot"}:
            if a["pool_seen_slot"] is not None and a["pool_seen_slot"] < lo2 <= (b["pool_seen_slot"] or 0):
                return "pool first seen before the restart's start"
        return None

    res = compare(u.journal.dir, r.journal.dir, causes=[seen_before_the_restart])
    print(json.dumps({t: {k: v for k, v in s.items() if not k.endswith("_first")} for t, s in res["tables"].items()}))
    assert res["green"], res["tables"]
    # The first run's rows up to the HWM are unchanged.
    after = _content(r.journal)
    for t in ("signals", "orders", "fills", "exits"):
        assert all(row in after[t] for row in before[t]), t


def test_wallets_busy_and_daily_loss_after_the_hwm_are_equal(runs):
    u, r, _, _, _, hwm = runs
    for w in ("wallet.json", "wallet-h.json"):
        assert json.load(open(os.path.join(r.journal.dir, w))) == json.load(open(os.path.join(u.journal.dir, w)))
    assert r.journal.state(RS.HWM_STATE_KEY) == u.journal.state(RS.HWM_STATE_KEY)
    assert {g.name: g.day_loss for g in r.groups} == {g.name: g.day_loss for g in u.groups}
    late = lambda e: {k: b for k, b in e.busy.items() if b == "open" or b >= hwm["slot"]}  # noqa: E731
    assert late(r) == late(u)
    rec = json.loads(r.journal.state(RS.RESTARTS_KEY))
    assert len(rec) == 1 and {k: v for k, v in rec[0]["counts"].items() if k not in RS.BEFORE} == {}, rec
