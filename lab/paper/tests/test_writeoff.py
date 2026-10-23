"""The write-off (engine._write_off_if_stuck; owner, 2026-10-23): a position whose exit can no longer fill is closed at
its rule's deadline. A drained pool (all liquidity withdrawn, so no sale is possible) is written off at its failed
deadline attempt, frees its place for the next signal, books its loss and the pool's cooldown; a slippage miss past the
deadline in a pool that still trades is no write-off; a restart writes off a position an older engine left open; and
paper.journal_diff / paper.parity name the write-off as a cause. Built with the golden cases' builders (C2 and C3), not
stored: the goldens stay byte-identical (no golden case reaches a write-off on an engine tick)."""

import contextlib
import json
import os
import sqlite3

import pytest

from paper import journal_diff, parity
from paper import restart as RS
from paper.engine import Engine
from paper.journal import Journal
from paper.tests import c1b_signal_cases as C1
from paper.tests import c2_position_cases as C2
from paper.tests import c3_risk_cases as C3

SOL = C1.SOL
T0 = 450_160_000
CAP = 599  # tp4_T600's time cap at the cases' clock (epoch 1042): C2's NX case
D = 2  # d_ms 600 at the cases' clock


def ticker(c, slots):
    """Small buys in a pool of their own: engine ticks (step 5 runs for every open position at each event slot)."""
    c.trades(c.pool("TICK"), "reversed", 5_000 * SOL, 10**15, [(s, 0, f"small buyer {i}", "buy", SOL // 10, False) for i, s in enumerate(slots)])


def drained(c, label, t, at=5):
    """A K3 trigger at t whose pool loses all its liquidity `at` slots later (C2's NX)."""
    p = c.pool(label)
    legs = C2.reversed_trigger(c, p, t, 400 * SOL)
    c.withdraw(p, legs[-1], t + at, 0, 1_000_000, 1_000_000)
    return p


def settled(eng, x):
    """The P&L settle() gives an unsold row: the modelled close (rent back), each earlier failed attempt's fee."""
    s = eng.scn
    return s.exit_net(0) - max(0, x["exit_attempts"] - 1) * s.failed_tx - s.entry_total(x["cost"])


def test_a_drained_pool_is_written_off_at_its_failed_deadline_attempt():
    c = C2.Case("writeoff_drained", "NX: drained at t + 105; the stop loss fails, the deadline's attempt fails, ticks follow", n2_k=0)
    t = T0
    nx = drained(c, "NX", t + 100)
    ticker(c, [t + 300, t + 800, t + 900])  # one before the deadline (no write-off there), two after
    c.build()
    x = c.one("exits", key=nx)
    deadline = t + 102 + CAP
    assert (x["exit_reason"], x["exit_decision_slot"], x["exit_slot"], x["proceeds"], x["exit_attempts"]) == (
        "written_off", deadline, deadline + D, 0, 2), x
    assert x["pnl"] == settled(c.eng, x) and x["tokens"] > 0
    f = c.one("fills", key=nx, side="sell")
    assert (f["slot"], f["quote"], f["tokens"]) == (deadline + D, 0, x["tokens"])
    assert not c.rows("positions")
    # Freed at the write-off: busy is its exit slot, not 'open'.
    assert c.eng.busy[("F7", "tp4_T600", nx)] == deadline + D


def test_a_pool_with_events_but_no_sale_is_written_off_in_its_own_slot():
    # Step 1: zero-LP withdraws keep the drained pool's events coming (no other pool ticks); at the event after the
    # deadline's failed attempt the position is written off in its own slot.
    c = C2.Case("writeoff_step1", "NX drained at t + 105, zero-LP withdraw events at t + 800 and t + 900", n2_k=0)
    t = T0
    p = c.pool("NX")
    legs = C2.reversed_trigger(c, p, t + 100, 400 * SOL)
    w = c.withdraw(p, legs[-1], t + 105, 0, 1_000_000, 1_000_000)
    w = c.withdraw(p, w, t + 800, 0, 0, 1)
    c.withdraw(p, w, t + 900, 0, 0, 1)
    c.build()
    x = c.one("exits", key=p)
    assert x["exit_reason"] == "written_off" and x["exit_slot"] == x["exit_decision_slot"] + D, x
    assert x["exit_decision_slot"] >= t + 102 + CAP and not c.rows("positions"), x


def test_without_a_tick_after_the_deadline_the_data_end_closes_it_as_before():
    # C2's golden NX: the case's events end before deadline + d, so end() closes it (exit_slot NULL), as without the fix.
    c = C2.Case("writeoff_no_tick", "NX alone: no event after the withdraw", n2_k=0)
    nx = drained(c, "NX", T0 + 100)
    c.build()
    x = c.one("exits", key=nx)
    assert (x["exit_slot"], x["proceeds"], x["exit_reason"] != "written_off") == (None, 0, True), x


def test_a_slippage_miss_past_the_deadline_is_no_write_off():
    c = C2.Case("writeoff_slippage", "L: the deadline's sale at t + 603 comes after a 60 SOL token sell (q 0.5 of two txs) "
                                     "and misses its minimum; the pool still trades, so it sells at the next decision", n2_k=0)
    t = T0
    p = c.pool("L")
    legs = C2.reversed_trigger(c, p, t, 400 * SOL)
    deadline = t + 2 + CAP
    c.more(p, "reversed", legs[-1], [(deadline + D, 0, "seller 2", "sell", 60 * SOL, False),
                                     (deadline + D, 1, "buyer 4", "buy", SOL // 10, False),
                                     (deadline + 50, 0, "buyer 5", "buy", SOL // 10, False)])
    c.build()
    x = c.one("exits", key=p)
    assert x["exit_reason"] != "written_off" and x["exit_slot"] is not None and x["proceeds"] > 0, x
    assert x["exit_attempts"] == 2 and x["exit_decision_slot"] > deadline, x


def test_the_written_off_place_is_free_for_the_next_signal():
    c = C3.Case("writeoff_max_open", "max_open_positions 1. P1 drained; P2 triggers before P1's write-off: max open; a tick "
                                     "writes P1 off; P3 triggers after it: entered.", k3=C3.risk(max_open_positions=1))
    t = T0
    p1 = drained(c, "P1", t)
    C2.reversed_trigger(c, c.pool("P2"), t + 300, 400 * SOL)
    ticker(c, [t + 650])
    C2.reversed_trigger(c, c.pool("P3"), t + 800, 400 * SOL)
    c.build()
    x = c.one("exits", key=p1)
    assert (x["exit_reason"], x["exit_slot"]) == ("written_off", t + 2 + CAP + D), x
    sig = {k: c.one("signals", key=c.key(k), variant="tp4_T600") for k in ("P2", "P3")}
    assert (sig["P2"]["status"], sig["P2"]["reason"]) == ("skipped", "max open positions"), sig["P2"]
    assert sig["P3"]["status"] == "entered", sig["P3"]
    # The normal exit path: the day's loss, the pool's 24 h cooldown from the exit slot, busy at the exit slot.
    k3 = c.eng.groups[0]
    assert k3.day_loss == sum(r["pnl"] for r in c.rows("exits") if r["family"] == "F7"), k3.day_loss
    assert k3.cool[p1] == x["exit_slot"] + c.eng.clock.slots(24 * 3_600_000, t + 650)
    assert c.eng.busy[("F7", "tp4_T600", p1)] == x["exit_slot"]


def test_two_write_offs_on_a_day_reach_the_daily_loss_limit():
    c = C3.Case("writeoff_daily_loss", "daily loss limit 1 SOL: P1 and P2 drained and written off (about -0.5 SOL each); P3 "
                                       "then: daily loss limit")
    t = T0
    drained(c, "P1", t)
    drained(c, "P2", t + 10)
    ticker(c, [t + 700])
    C2.reversed_trigger(c, c.pool("P3"), t + 800, 400 * SOL)
    c.build()
    offs = [r for r in c.rows("exits") if r["exit_reason"] == "written_off"]
    assert len(offs) == 2 and sum(r["pnl"] for r in offs) <= -SOL, offs
    r = c.one("signals", key=c.key("P3"), variant="tp4_T600")
    assert (r["status"], r["reason"]) == ("skipped", "daily loss limit"), r


# ------------------------------------------------------------------------------------------------- restarts

class CrashFeed(C1.CaseFeed):
    """The case's events up to `crash_at` (exclusive): then the process dies."""

    def __init__(self, case, crash_at=None):
        super().__init__(case)
        self.crash_at = crash_at

    def events(self):
        for e in super().events():
            if self.crash_at is not None and e.slot >= self.crash_at:
                raise RuntimeError("simulated crash")
            yield e


def live_engine(case, path, crash_at=None):
    """C1.engine with the HWM (restart=True), risk inert, on one journal directory."""
    gs = json.loads(case["groups"])
    (name, p), = [(n, g) for n, g in gs.items() if g["primary"]]
    others = [C1.Group(n, [C1.TP_T.get(v, v) for v in g["variants"]], farm=g["farm"], segments=g["segments"], d=g["d"],
                       d_ms=g["d_ms"], q=g["q"], n2_k=g["n2_k"], risk_path=g["risk"], wallet_file=g["wallet"],
                       require_farm_complete=g["require_farm_complete"]) for n, g in gs.items() if not g["primary"]]
    return Engine(CrashFeed(case, crash_at), Journal(path), p["risk"], variants=[C1.TP_T.get(v, v) for v in p["variants"]],
                  d=p["d"], d_ms=p["d_ms"], q=p["q"], n2_k=p["n2_k"], segments=p["segments"], farm=p["farm"], name=name,
                  groups=others, ignore_risk=True, clock=C1.Clock(case["clock"]), dev_bounds=tuple(case["dev_bounds"]),
                  backup=False, restart=True)


def crash(eng):
    with pytest.raises(RuntimeError, match="simulated crash"):
        eng.run()
    eng.journal.db.rollback()  # the process died: what it wrote after its last commit is gone
    hwm = json.loads(eng.journal.state(RS.HWM_KEY))
    eng.journal.close()
    return hwm


def restart_case():
    """NX drained at t + 105 (deadline + d = t + 703); ticks every ~250 slots (new minutes, so commits) up to t + 3,000."""
    c = C2.Case("writeoff_restart", "NX drained; ticks past its deadline; the run dies at t + 2,500", n2_k=0)
    t = T0
    nx = drained(c, "NX", t + 100)
    ticker(c, list(range(t + 250, t + 3_001, 250)))
    return c.build(), nx


def test_a_write_off_before_the_hwm_is_replayed_once(tmp_path):
    case, nx = restart_case()
    hwm = crash(live_engine(case, str(tmp_path / "j"), crash_at=T0 + 2_500))
    assert hwm["slot"] > T0 + 102 + CAP + D
    r = live_engine(case, str(tmp_path / "j"))
    r.run()
    xs = r.journal.rows("exits", "key = ?", (nx,))
    assert len(xs) == 1 and xs[0]["exit_reason"] == "written_off", xs
    rec = json.loads(r.journal.state(RS.RESTARTS_KEY))[-1]
    # The replay made the write-off itself and matched the journal's row: no difference at all (not forced from it).
    assert {k: v for k, v in rec["counts"].items() if v and k not in RS.BEFORE} == {}, rec["counts"]


def test_a_position_an_older_engine_left_open_is_written_off_at_the_resume(tmp_path, monkeypatch):
    case, nx = restart_case()
    with monkeypatch.context() as m:  # the first run: an engine without the write-off
        m.setattr(Engine, "_write_off_if_stuck", lambda self, o, slot: False)
        hwm = crash(live_engine(case, str(tmp_path / "j"), crash_at=T0 + 2_500))
    with contextlib.closing(sqlite3.connect(str(tmp_path / "j" / "journal.sqlite"))) as db:
        assert db.execute("SELECT count(*) FROM positions WHERE key = ?", (nx,)).fetchone()[0] == 1  # open at the HWM
    r = live_engine(case, str(tmp_path / "j"))
    r.run()
    xs = r.journal.rows("exits", "key = ?", (nx,))
    assert len(xs) == 1, xs
    x = xs[0]
    assert (x["exit_reason"], x["exit_slot"], x["proceeds"]) == ("written_off", T0 + 102 + CAP + D, 0), x
    assert x["pnl"] == settled(r, x)
    rec = json.loads(r.journal.state(RS.RESTARTS_KEY))[-1]
    c = rec["counts"]
    assert c.get("written_off_at_resume") == 1 and not c.get("restart_lost") and not c.get("replay_exits_not_in_journal"), c
    assert not c.get("exposure_different"), c
    assert x["day"] == rec["day"]  # booked on the restart's day
    lines = RS.restart_lines(r.journal, rec["day"])
    assert any("written off at the HWM" in line for line in lines) and not any("written_off_at_resume" in line for line in lines), lines
    assert hwm["slot"] > x["exit_slot"]


def test_a_second_restart_after_a_write_off_at_the_resume_across_midnight_is_clean(tmp_path, monkeypatch):
    # NX's write-off (t + 703) is on 2026-09-21, before MIDNIGHT; the first restart writes it off at its HWM on 09-22
    # (the row's day). The second restart's replay reaches it on 09-21 and books it on 09-22 when it gets there.
    t = 450_130_000
    c = C2.Case("writeoff_midnight", "NX drained before midnight; ticks to well after it", n2_k=0)
    nx = drained(c, "NX", t + 100)
    ticker(c, list(range(t + 250, t + 4_001, 250)))
    case = c.build()
    j = str(tmp_path / "j")
    with monkeypatch.context() as m:
        m.setattr(Engine, "_write_off_if_stuck", lambda self, o, slot: False)
        crash(live_engine(case, j, crash_at=t + 2_500))
    first = crash(live_engine(case, j, crash_at=t + 3_500))
    r = live_engine(case, j)
    r.run()
    recs = json.loads(r.journal.state(RS.RESTARTS_KEY))
    assert recs[0]["counts"].get("written_off_at_resume") == 1, recs[0]
    assert {k: v for k, v in recs[-1]["counts"].items() if v and k not in RS.BEFORE} == {}, recs[-1]["counts"]
    xs = r.journal.rows("exits", "key = ?", (nx,))
    assert len(xs) == 1 and xs[0]["exit_reason"] == "written_off" and xs[0]["day"] == recs[0]["day"] != C2._day(C1.block_time(xs[0]["exit_slot"])), xs
    assert first["slot"] > t + 2_500


def test_a_write_off_the_journal_neither_exits_nor_holds_is_counted(tmp_path, monkeypatch):
    # An inconsistent journal (its NX position row gone): the replay's write-off is counted as an exit of its own.
    case, nx = restart_case()
    with monkeypatch.context() as m:
        m.setattr(Engine, "_write_off_if_stuck", lambda self, o, slot: False)
        crash(live_engine(case, str(tmp_path / "j"), crash_at=T0 + 2_500))
    with contextlib.closing(sqlite3.connect(str(tmp_path / "j" / "journal.sqlite"))) as db:
        db.execute("DELETE FROM positions WHERE key = ?", (nx,))
        db.commit()
    r = live_engine(case, str(tmp_path / "j"))
    r.run()
    c = json.loads(r.journal.state(RS.RESTARTS_KEY))[-1]["counts"]
    assert c.get("replay_exits_not_in_journal") == 1 and not c.get("written_off_at_resume"), c
    assert not r.journal.rows("exits", "key = ?", (nx,))


# ------------------------------------------------------------------------------------------------- the tools

def _journal(path, exits, fills=(), state=None):
    os.makedirs(path, exist_ok=True)
    j = Journal(path)
    for r in exits:
        j.put("exits", **r)
    for r in fills:
        j.put("fills", **r)
    for k, v in (state or {}).items():
        j.state(k, v)
    j.commit()
    j.close()


def _exit(**over):
    row = dict(family="F7", variant="tp4_T600", key="P", t=100, day="2026-09-22", entry_slot=102, exit_decision_slot=701,
               exit_slot=None, exit_reason="time", tokens=5, cost=500, proceeds=0, pnl=-1_000, ret=-1.0, exit_attempts=2,
               cf_graduation=0, seed_pool=0, vault_capped=0, reverted_direct=0, reverted_router=0, sells_dropped=0,
               sells_scaled=0, peak_mark=None, peak_slot=None, n_flow_slots=None, cap_slots=599)
    row.update(over)
    return row


def test_journal_diff_names_the_write_off_and_the_freed_pool(tmp_path):
    scn = json.dumps({"failed_tx": 505_000})
    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    sell = dict(family="F7", variant="tp4_T600", key="P", t=100, side="sell", day="2026-09-22", tokens=5, quote=0)
    _journal(a, [_exit(day="2026-09-23")], [dict(sell, slot=None)], {"cost_scenario": scn})
    _journal(b, [_exit(exit_slot=703, exit_reason="written_off", exit_attempts=2),
                 _exit(t=900, entry_slot=902, exit_decision_slot=950, exit_slot=952, exit_reason="take_profit", proceeds=600, pnl=99)],
             [dict(sell, slot=703)], {"cost_scenario": scn})
    causes = journal_diff.writeoff_causes(a, b)
    res = journal_diff.compare(a, b, causes=causes)
    assert res["unexplained"] == 0 and res["explained"] == {"written_off": 2, "busy_freed": 1}, res["explained"]
    # Without the causes every difference is unexplained.
    assert journal_diff.compare(a, b)["unexplained"] == 3
    # Main sold it later (the pool got liquidity again): written_off_later_sold, listed apart.
    d = str(tmp_path / "d")
    _journal(d, [_exit(day="2026-09-23", exit_slot=900, exit_decision_slot=898, proceeds=400, pnl=-150, exit_reason="stop_loss")],
             [dict(sell, slot=900, quote=400)], {"cost_scenario": scn})
    res = journal_diff.compare(d, b, causes=journal_diff.writeoff_causes(d, b))
    assert res["unexplained"] == 0 and res["explained"] == {"written_off_later_sold": 2, "busy_freed": 1}, res["explained"]
    # A write-off that does not match (another entry) stays unexplained.
    c = str(tmp_path / "c")
    _journal(c, [_exit(exit_slot=703, exit_reason="written_off", entry_slot=103)], [dict(sell, slot=703)], {"cost_scenario": scn})
    assert journal_diff.compare(a, c, causes=journal_diff.writeoff_causes(a, c))["unexplained"] >= 1


def test_parity_explains_a_written_off_row_and_its_busy_window():
    k1, k2 = ("P", 100), ("P", 300)
    bt = {k1: {"status": "filled", "exit_slot": None, "proceeds": 0, "tokens": 5, "cost": 500, "exit_reason": "time"},
          k2: {"status": "filled", "exit_slot": 400, "proceeds": 600, "tokens": 5, "cost": 500}}
    en = {k1: {"status": "filled", "exit_slot": 703, "proceeds": 0, "tokens": 5, "cost": 500, "exit_reason": "written_off"},
          k2: {"status": "busy"}}
    assert parity.written_off_row(bt, en, k1) and parity.unsold_busy(bt, en, k2)
    _, diffs, _ = parity.compare(bt, en, 0, 1_000)
    unexplained, busy, wo, later = parity._split(diffs, bt, en)
    assert (len(unexplained), len(busy), len(wo), len(later)) == (0, 1, 1, 0)
    # The backtest sold it later (a pool with liquidity again): explained too, listed apart.
    sold = {**bt, k1: {**bt[k1], "exit_slot": 900, "proceeds": 400}, k2: {"status": "busy"}}  # held to 900: k2 busy there too
    assert parity.written_off_later_sold(sold, en, k1) and not parity.written_off_row(sold, en, k1)
    _, diffs, _ = parity.compare(sold, en, 0, 1_000)
    assert [len(x) for x in parity._split(diffs, sold, en)] == [0, 0, 0, 1]
    # After the write-off the engine's pool is free: a trigger past it is no unsold_busy.
    assert not parity.unsold_busy(bt, {**en, k1: {**en[k1], "exit_slot": 250}}, k2)
