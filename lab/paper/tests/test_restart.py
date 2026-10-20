"""Live restarts with a high-water mark (paper/restart.py) on a synthetic feed: a run that dies and restarts from a
later start (the keeper removed the oldest segments) ends with the uninterrupted run's journal and wallet; up to the
HWM it writes nothing and takes the journal's decisions, also when the risk file changed; a position the replay
cannot carry is closed at its last mark; a first start is the same with and without the HWM. Plus LiveFeed's learned
pools, tokens and epoch spans after the retention, the live watchlist's and open-pools' wait while catching up, and the
report's restart lines."""

import json
import os
import shutil

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backtest.strategies import Clock
from paper import restart as RS
from paper.engine import Engine
from paper.journal import Journal
from paper.tests.test_engine import SOL, T, Synth, risk_file

TABLES = ("signals", "orders", "fills", "exits", "positions")


def trig(base, after=(), creator="C"):
    """Inflow, then a large non-creator sell at base + 40 (an F7 curve trigger), then `after`."""
    return (creator, [(base, "A", "buy", 20 * T), (base + 10, "A2", "buy", 20 * T), (base + 40, "B", "sell", 60 * T)]
            + list(after))


# A: entered and closed (take profit) before the later start. B: entered at 501_041, open over the HWM, take profit
# at 501_600. C: trades only (minutes, so commits, after B's entry). Each of 501_200, 501_300 and 501_450 starts a minute.
TOKENS = {"A": trig(500_000, [(500_100, "W", "buy", 150 * T)]),
          "B": trig(501_000, [(501_200, "Z", "buy", T), (501_600, "Y", "buy", 150 * T)]),
          "C": ("C2", [(501_300, "X", "buy", T), (501_450, "V", "buy", T)])}
CRASH = 10  # dies fetching the event of 501_600: slot 501_450 is not processed, the last commit (HWM) was at 501_300


class Later(Synth):
    """The recorder's segments after the keeper removed the oldest: the events from `from_slot` on (without the slots
    in `skip`)."""

    def __init__(self, tokens, from_slot=0, crash_after=None, kept=None, skip=()):
        super().__init__(tokens, crash_after)
        self.from_slot, self.kept, self.skip = from_slot, kept, set(skip)

    def events(self):
        for e in super().events():
            if e.slot >= self.from_slot and e.slot not in self.skip:
                yield e

    def keep_pools(self, pools):
        if self.kept is not None:
            self.kept.append(list(pools))


def engine(tmp_path, name, feed, risk=None, variants=((0.04, 600),), **kw):
    return Engine(feed, Journal(str(tmp_path / name)), risk or risk_file(tmp_path), variants=list(variants), d=1, q=0.5,
                  n2_k=0, clock=Clock(), backup=False, restart=True, **kw)


def crash(e):
    """Run until the feed's simulated crash; the process died, so what it wrote after its last commit is gone."""
    with pytest.raises(RuntimeError, match="simulated crash"):
        e.run()
    e.journal.db.rollback()
    return json.loads(e.journal.state(RS.HWM_KEY))


def content(j):
    return {t: j.rows(t) for t in TABLES}


def wallet(j):
    with open(os.path.join(j.dir, "wallet.json")) as f:
        return json.load(f)


def crashed(tmp_path, name="r"):
    """R1: dies after its commit at 501_300 with B open. Returns its journal's rows then."""
    e = engine(tmp_path, name, Later(TOKENS, crash_after=CRASH))
    with pytest.raises(RuntimeError, match="simulated crash"):
        e.run()
    e.journal.db.rollback()  # the process died: what it wrote after its last commit is gone
    hwm = json.loads(e.journal.state(RS.HWM_KEY))
    assert hwm["slot"] == 501_300
    rows = content(e.journal)
    assert [x["key"] for x in rows["exits"]] == ["A"] and [p["key"] for p in rows["positions"]] == ["B"]
    e.journal.close()
    return rows


def test_a_restart_from_a_later_start_ends_with_the_uninterrupted_journal(tmp_path):
    u = engine(tmp_path, "u", Later(TOKENS))
    u.run()
    before = crashed(tmp_path)
    r = engine(tmp_path, "r", Later(TOKENS, from_slot=500_500))
    assert r.replaying
    r.run()
    # The journal, wallet and heartbeat equal the uninterrupted run's; B's exit is there once, after the HWM.
    assert content(r.journal) == content(u.journal)
    assert wallet(r.journal) == wallet(u.journal) and wallet(u.journal)["balance_lamports"] != 10 * SOL
    assert r.journal.rows("heartbeat") == u.journal.rows("heartbeat")
    assert [x["key"] for x in r.journal.rows("exits")] == ["A", "B"] and r.journal.rows("exits", "key = 'B'")[0]["exit_slot"] > 501_300
    # Every row of the first run up to the HWM is unchanged (B's open position row left with its exit).
    after = content(r.journal)
    for t in ("signals", "orders", "fills", "exits"):
        assert all(row in after[t] for row in before[t]), t
    # The replay took B's decisions from the journal and found no difference; the HWM's state equals the run's.
    rec = json.loads(r.journal.state(RS.RESTARTS_KEY))
    assert len(rec) == 1 and rec[0]["hwm"] == 501_300 and rec[0]["first_slot"] == 501_000
    assert {k: v for k, v in rec[0]["counts"].items() if k not in RS.BEFORE} == {}, rec[0]["counts"]
    assert rec[0]["counts"]["journal_exits_before_the_replay"] == 1  # A's
    assert r.journal.state(RS.HWM_STATE_KEY) == u.journal.state(RS.HWM_STATE_KEY)


def test_an_exit_decided_before_the_hwm_and_filled_after_it_is_written_once(tmp_path):
    # B's take profit is decided at 501_549; its exit fills at 501_550, after the commit that starts that minute
    # (the HWM). The first run's exit row was not committed; the restart carries the decided exit and writes it once.
    toks = {"A": TOKENS["A"], "B": trig(501_000, [(501_200, "Z", "buy", T), (501_549, "Y", "buy", 150 * T),
                                                  (501_550, "Q", "buy", T)]),
            "C": ("C2", [(501_300, "X", "buy", T), (501_560, "V", "buy", T), (501_900, "W", "buy", T)])}
    u = engine(tmp_path, "u", Later(toks))
    u.run()
    x = u.journal.rows("exits", "key = 'B'")[0]
    assert (x["exit_decision_slot"], x["exit_slot"]) == (501_549, 501_550)
    e = engine(tmp_path, "r", Later(toks, crash_after=12))  # dies fetching 501_900: 501_560 is not processed
    with pytest.raises(RuntimeError, match="simulated crash"):
        e.run()
    e.journal.db.rollback()
    assert json.loads(e.journal.state(RS.HWM_KEY))["slot"] == 501_550
    assert not e.journal.rows("exits", "key = 'B'") and e.journal.rows("positions", "key = 'B'")
    e.journal.close()
    r = engine(tmp_path, "r", Later(toks, from_slot=500_500))
    r.run()
    assert r.journal.rows("exits", "key = 'B'") == [x]
    assert content(r.journal) == content(u.journal) and wallet(r.journal) == wallet(u.journal)
    rec = json.loads(r.journal.state(RS.RESTARTS_KEY))[0]
    assert {k: v for k, v in rec["counts"].items() if k not in RS.BEFORE} == {}, rec["counts"]


def test_up_to_the_hwm_the_journal_decides_also_with_a_changed_risk_file(tmp_path, monkeypatch):
    monkeypatch.setattr(RS, "COMPARE_MS", 300 * Clock().ms_per_slot(501_000))  # 300 slots: [501_000, 501_300)
    u = engine(tmp_path, "u", Later(TOKENS))
    u.run()
    crashed(tmp_path)
    # The owner turned the kill switch on before the restart: B's entry (before the HWM) stays the journal's.
    r = engine(tmp_path, "r", Later(TOKENS, from_slot=500_500), risk=risk_file(tmp_path, kill_switch=True))
    r.run()
    assert content(r.journal) == content(u.journal) and wallet(r.journal) == wallet(u.journal)
    rec = json.loads(r.journal.state(RS.RESTARTS_KEY))[0]
    # Compared in the window: the replay itself would have skipped B (kill switch); the journal had it entered.
    assert rec["compare_6h"]["compared"] and rec["compare_6h"]["different"] == 1 and rec["compare_6h"].get("same", 0) == 0


def test_the_comparison_waits_for_complete_farmer_history(tmp_path):
    rs = RS.Restart(None, {"slot": 10_000}, {})
    rs.first_slot = 1_000
    assert rs.compare(6_000, complete_from=5_000)["compared"] is False  # HWM - 6 h = 4,000 < complete_from
    assert rs.compare(6_000, complete_from=4_000)["compared"] is True
    assert "first slot" in rs.compare(9_500, complete_from=None)["why"]  # before the replay's first slot


def test_a_position_the_replay_cannot_carry_is_closed_at_its_last_mark(tmp_path):
    before = crashed(tmp_path)
    mark = before["positions"][0]
    # The keeper removed B's entry too: the replay starts after it.
    r = engine(tmp_path, "r", Later(TOKENS, from_slot=501_100))
    r.run()
    xs = r.journal.rows("exits", "key = 'B'")
    assert len(xs) == 1 and xs[0]["exit_reason"] == "restart_lost" and xs[0]["exit_slot"] == 501_300
    assert xs[0]["pnl"] == mark["mark_pnl"] and r.scn.exit_net(xs[0]["proceeds"]) == mark["mark_proceeds"]
    assert not r.journal.rows("positions") and not r.journal.rows("fills", "key = 'B' AND side = 'sell'")
    a = r.journal.rows("exits", "key = 'A'")[0]
    assert wallet(r.journal)["balance_lamports"] == 10 * SOL + a["pnl"] + xs[0]["pnl"]
    rec = json.loads(r.journal.state(RS.RESTARTS_KEY))[0]
    assert rec["counts"]["restart_lost"] == 1
    from paper.report import daily_report

    daily_report(r.journal, xs[0]["day"])
    report = open(os.path.join(r.journal.dir, xs[0]["day"], "report.md")).read()
    assert "## Restarts" in report and "restart_lost 1" in report and "closed at their last mark" in report
    # a feed without LiveFeed's stats (this test's): no unknown-mint count, no line about it
    assert rec["unknown_mints"] is None and "unknown mints at the resume" not in report


def test_the_daily_report_names_a_restarts_unknown_mints(tmp_path):
    """The known restart limit (live.py, beside TOKEN_KEEP_DAYS): the restart's record holds LiveFeed's unknown_mints and
    unknown_mint_rows at the resume, and the day's restart line names them."""
    j = Journal(str(tmp_path / "j"))
    j.state(RS.RESTARTS_KEY, json.dumps([{"hwm": 500, "resumed_at": 510, "first_slot": 400, "day": "2026-10-21", "counts": {},
                                          "compare_6h": {}, "unknown_mints": 3, "unknown_mint_rows": 17}]))
    lines = RS.restart_lines(j, "2026-10-21")
    assert "  - unknown mints at the resume: 3 (17 curve rows dropped); among them any token created before the replay " \
           "window that did not graduate, whose pools take the coin creator as F7's creator (a known limit, live.py)" in lines
    assert RS.restart_lines(j, "2026-10-22") == []
    j.close()


def test_a_first_start_is_the_same_with_and_without_the_hwm(tmp_path):
    a = engine(tmp_path, "a", Later(TOKENS))
    a.run()
    b = Engine(Later(TOKENS), Journal(str(tmp_path / "b")), risk_file(tmp_path), variants=[(0.04, 600)], d=1, q=0.5, n2_k=0,
               clock=Clock(), backup=False)
    b.run()
    assert content(a.journal) == content(b.journal) and wallet(a.journal) == wallet(b.journal)
    assert a.journal.rows("heartbeat") == b.journal.rows("heartbeat")
    assert a.journal.state(RS.HWM_KEY) and b.journal.state(RS.HWM_KEY) is None and b.journal.state(RS.HWM_STATE_KEY) is None


def test_open_pools_are_not_written_while_catching_up(tmp_path):
    kept = []
    engine(tmp_path, "far", Later(TOKENS, kept=kept), wall_clock=lambda: 4_000_000_000).run()  # far behind the wall clock
    assert kept == []
    engine(tmp_path, "now", Later(TOKENS, kept=kept), wall_clock=lambda: 1_790_200_640).run()  # the data's last minute
    assert kept and kept[-1] == []


# ---------------------------------------------------------------- LiveFeed: what it learned, after the retention

def write(d, table, rows):
    os.makedirs(os.path.join(d, table), exist_ok=True)
    pq.write_table(pa.table({k: [r[k] for r in rows] for k in rows[0]}), os.path.join(d, table, "part-0.parquet"))


WSOL = "So11111111111111111111111111111111111111112"


def seg(root, seq, first, tables):
    """A segment of 100 slots at `first`. Block times: 500 ms a slot in segment 0, 250 ms after it (the measured slot
    time of the epoch depends on where its span starts)."""
    d = os.path.join(root, "segments", f"{seq:09d}")
    bt = (lambda s: 1_790_000_000 + (s - 1_000_000) // 2) if seq == 0 else (lambda s: 1_790_000_500 + (s - 1_001_000) // 4)
    write(d, "blocks", [{"slot": s, "block_time": bt(s), "skipped": False} for s in range(first, first + 100, 10)])
    for table, rows in tables.items():
        write(d, table, rows)
    with open(os.path.join(d, "_segment.json"), "w") as f:
        json.dump({"seq": seq, "first_slot": first, "last_slot": first + 99, "complete": True}, f)


def pool_row(pool, mint, slot):
    return {"pool": pool, "base_mint": mint, "quote_mint": WSOL, "slot": slot, "tx_index": 0, "coin_creator": "CC",
            "is_mayhem_mode": False}


def test_learned_pools_tokens_and_epoch_spans_come_back_after_the_retention(tmp_path):
    from paper.live import LiveFeed

    root = str(tmp_path / "rec")
    seg(root, 0, 1_000_000, {"pump_amm/CreatePoolEvent": [pool_row("P0", "M0", 1_000_005)],
                             "pump/CreateEvent": [{"mint": "M0", "slot": 1_000_001, "tx_index": 0, "user": "U0",
                                                   "is_mayhem_mode": False, "quote_mint": WSOL}],
                             "pump/CompleteEvent": [{"mint": "M0", "slot": 1_000_003, "tx_index": 0}],
                             "pump/CompletePumpAmmMigrationEvent": [{"mint": "M0", "pool": "P0", "slot": 1_000_005, "tx_index": 1}]})
    seg(root, 1, 1_001_000, {})
    seg(root, 2, 1_002_000, {"pump_amm/CreatePoolEvent": [pool_row("P2", "M2", 1_002_005)]})
    f = LiveFeed(root, follow=False)
    list(f.events())
    learned = f.drain_learned()
    assert {(k, key) for k, key, *_ in learned} == {("token", "M0"), ("pool", "P0"), ("pool", "P2"), ("epoch", "2")}
    j = Journal(str(tmp_path / "j"))
    for kind, key, seq, slot, info in learned:
        j.put("learned", kind=kind, key=key, seq=seq, slot=slot, info=info)
    j.commit()
    shutil.rmtree(os.path.join(root, "segments", f"{0:09d}"))  # the keeper: the oldest segment is gone
    g = LiveFeed(root, follow=False, learned=j.rows("learned"))
    # P0 was learned before the first retained segment: known from the journal; P2 is learned again in order.
    assert g.pool("P0") == f.pool("P0") and g.token("M0") == f.token("M0") and g.token("M0").pool == "P0"
    assert g.pool("P2") is None and g.epoch_span[2][0] == f.epoch_span[2][0]
    list(g.events())
    assert g.new_pools == ["P2"] and g.pool("P2") == f.pool("P2")
    # The epoch's clock span starts at the removed segment's first block, as in the run that saw it.
    assert 2 in f.clock.per_epoch and g.clock.per_epoch[2] == f.clock.per_epoch[2]
    f.close()
    g.close()


def curve_trade(mint, slot):
    """A pump TradeEvent row with every column the store's curve rows read (backtest.store._curve_trades)."""
    return {"mint": mint, "slot": slot, "tx_index": 0, "outer_ix": 0, "inner_ix": 0, "parent_ix_disc": "-", "is_buy": True,
            "token_amount": 1_000, "quote_amount": 7_000, "sol_amount": 7_000, "fee": 0, "creator_fee": 0, "fee_basis_points": 95,
            "creator_fee_basis_points": 5, "virtual_token_reserves": 10**15, "virtual_quote_reserves": 30 * 10**9,
            "virtual_sol_reserves": 30 * 10**9, "real_token_reserves": 10**15, "real_quote_reserves": 10**9,
            "real_sol_reserves": 10**9, "user": "B1", "fee_payer": "B1", "outer_program": "P", "priority_fee": 0, "jito_tip": 0,
            "tx_fee": 5_000, "mayhem_mode": False}


def test_a_restart_past_the_window_does_not_know_a_token_that_did_not_graduate(tmp_path):
    """The known limit (LAB review 2026-10-20, kept as is, M3-12): live.py learns a token at its completion or migration
    only. A token created in a segment the restart no longer reads (the keeper, or a replay window of 36 h) that did not
    graduate is unknown to it: its curve rows are dropped and counted (unknown_mints, unknown_mint_rows), and a pool of
    it takes its coin creator as F7's creator (engine.py _universe). The uninterrupted run knows the token until _prune
    drops it (TOKEN_KEEP_DAYS), so the gap closes three days after the token's creation at the latest."""
    from paper.engine import Engine
    from paper.live import LiveFeed

    root = str(tmp_path / "rec")
    seg(root, 0, 1_000_000, {"pump/CreateEvent": [{"mint": "M1", "slot": 1_000_001, "tx_index": 0, "user": "U1",
                                                   "is_mayhem_mode": False, "quote_mint": WSOL}],
                             "pump_amm/CreatePoolEvent": [pool_row("P1", "M1", 1_000_005)]})
    seg(root, 1, 1_001_000, {"pump/TradeEvent": [curve_trade("M1", 1_001_010)]})
    f = LiveFeed(root, follow=False)
    list(f.events())
    learned = f.drain_learned()
    assert ("token", "M1") not in {(k, key) for k, key, *_ in learned}  # created, not graduated: no learned row
    assert f.token("M1").creator == "U1" and f.stats["unknown_mints"] == 0
    j = Journal(str(tmp_path / "j"))
    for kind, key, seq, slot, info in learned:
        j.put("learned", kind=kind, key=key, seq=seq, slot=slot, info=info)
    j.commit()
    shutil.rmtree(os.path.join(root, "segments", f"{0:09d}"))  # past the window: segment 0 is not read again
    g = LiveFeed(root, follow=False, learned=j.rows("learned"))
    list(g.events())
    assert g.pool("P1") == f.pool("P1")  # the pool comes back from its learned row
    assert g.token("M1") is None and (g.stats["unknown_mints"], g.stats["unknown_mint_rows"]) == (1, 1)
    # F7's creator of the pool: the token's creator in the uninterrupted run, the pool's coin creator after the restart
    creators = []
    for feed, name in ((f, "ef"), (g, "eg")):
        eng = Engine(feed, Journal(str(tmp_path / name)), None, ignore_risk=True, n2_k=0, clock=feed.clock, backup=False)
        creators.append(eng._universe("pool", "P1")[1])
    assert creators == ["U1", "CC"]
    f.close()
    g.close()


# ---------------------------------------------------------------- the live watchlist while catching up

class NoPools:
    new_pools = []

    def pool(self, pool):
        return None

    def token(self, mint):
        return None


def test_the_watchlist_waits_while_catching_up_and_logs_each_minute_once(tmp_path):
    from paper.watchlist_live import LiveWatchlist

    root = str(tmp_path)
    path = os.path.join(root, "watchlist-current.json")
    behind = LiveWatchlist(NoPools(), root, wall_clock=lambda: 1_000 * 60)
    for m in (900, 901, 902):
        behind.minute(m, m * 150)
    assert not os.path.exists(path) and behind.held == 2  # the first minute only starts the stepping
    now = LiveWatchlist(NoPools(), root, wall_clock=lambda: 903 * 60 + 5)  # a restart, caught up at 903
    assert now.logged == 902
    for m in (901, 902, 903, 904):  # the replay repeats 901 and 902: not logged again
        now.minute(m, m * 150)
    with open(os.path.join(root, "watchlist-log.jsonl")) as f:
        assert [json.loads(line)["minute"] for line in f] == [901, 902, 903, 904]
    assert json.load(open(path))["valid_from_slot"] == 904 * 150
    with open(os.path.join(root, "watchlist-log.jsonl"), "a") as f:
        f.write('{"minute": 905, "slot"')  # cut off by a crash: the last complete line counts
    assert LiveWatchlist(NoPools(), root).logged == 904


def test_up_to_the_hwm_only_the_state_learned_rows_and_later_usage_are_written(tmp_path):
    j = Journal(str(tmp_path / "j"))
    m = RS.Muted(j, hwm_minute=100)
    m.put("signals", family="F7", variant="v", key="K", t=1, status="entered")
    m.put("heartbeat", minute=99, note="")
    m.put("usage", minute=100, stream="s", bytes=1)  # the first run's minute: its row stays
    m.put("usage", minute=101, stream="s", bytes=2)  # read after the first run's last commit: kept
    m.put("learned", kind="pool", key="P", seq=1, slot=5, info="[]")
    m.state("k", "v")
    m.delete("positions", family="F7", variant="v", key="K", t=1)
    m.commit()
    assert not j.rows("signals") and not j.rows("heartbeat")
    assert [(u["minute"], u["bytes"]) for u in j.rows("usage")] == [(101, 2)]
    assert j.rows("learned")[0]["key"] == "P" and j.state("k") == "v"



def test_a_run_that_ends_moves_its_hwm_past_its_last_slot(tmp_path):
    u = engine(tmp_path, "u", Later(TOKENS))
    u.run()
    assert json.loads(u.journal.state(RS.HWM_KEY))["slot"] == u.last_slot + 1  # LAB review N1


def test_a_day_the_first_run_did_not_close_is_closed_by_the_restart(tmp_path):
    """LAB review S1: the HWM is in the last minute of a day (519_900; the day ends at 520_000) and the run dies before
    the next day's first slot, so the day was never closed. A restart that does not see the HWM's slot itself resumes
    before the day change and closes the day: its export and report."""
    from paper.engine import _day

    # A exits at 519_101, settled at the next event (519_200), before the HWM; C's events make the minutes.
    toks = {"A": trig(519_000, [(519_100, "W", "buy", 150 * T)]),
            "C": ("C2", [(519_200, "X0", "buy", T), (519_900, "X", "buy", T), (520_000, "V", "buy", T),
                         (520_100, "Y", "buy", T)])}
    day = _day(1_790_000_000 + int(519_999 * 0.4))
    assert day != _day(1_790_000_000 + int(520_000 * 0.4))
    u = engine(tmp_path, "u", Later(toks))
    u.run()
    e = engine(tmp_path, "r", Later(toks, crash_after=7))  # dies fetching 520_100: slot 520_000 is not processed
    assert crash(e)["slot"] == 519_900
    assert not os.path.exists(os.path.join(e.journal.dir, day, "report.md"))
    e.journal.close()
    r = engine(tmp_path, "r", Later(toks, skip={519_900}))
    r.run()
    assert os.path.exists(os.path.join(r.journal.dir, day, "report.md"))
    assert pq.read_table(os.path.join(r.journal.dir, day, "exits.parquet")) == pq.read_table(
        os.path.join(u.journal.dir, day, "exits.parquet"))
    assert content(r.journal) == content(u.journal)


def test_a_group_switched_off_before_the_restart_touches_no_other_group(tmp_path):
    """LAB review S2 (runbook §5, "Group H off"): group H's position open at the HWM is closed (restart_lost) without
    touching K3's daily loss, wallet or cooldowns; K3's rows and wallet are those of the run without the restart."""
    from paper.engine import Group

    def groups():
        return [Group("H", ["0.05:600"], n2_k=0, risk_path=risk_file(tmp_path), wallet_file="wallet-h.json")]

    u = engine(tmp_path, "u", Later(TOKENS), groups=groups())
    u.run()
    crash(engine(tmp_path, "r", Later(TOKENS, crash_after=CRASH), groups=groups()))
    r = engine(tmp_path, "r", Later(TOKENS, from_slot=500_500))  # without group H
    r.run()
    k3 = "variant = 'tp4_T600'"
    assert {t: r.journal.rows(t, k3) for t in TABLES} == {t: u.journal.rows(t, k3) for t in TABLES}
    assert wallet(r.journal) == wallet(u.journal)
    assert json.loads(r.journal.state(RS.HWM_STATE_KEY))["groups"]["K3"] == json.loads(u.journal.state(RS.HWM_STATE_KEY))["groups"]["K3"]
    h = r.journal.rows("exits", "variant = 'tp5_T600' AND key = 'B'")
    assert len(h) == 1 and h[0]["exit_reason"] == "restart_lost" and not r.journal.rows("positions")
    counts = json.loads(r.journal.state(RS.RESTARTS_KEY))[0]["counts"]
    assert counts["restart_lost_of_unconfigured_variants"] == 1 and "restart_lost" not in counts


def test_the_window_carries_a_position_of_almost_6_hours(tmp_path):
    """The replay starts RS.WINDOW_H (36) hours before the HWM: a position entered 5.9 h before it is carried
    (restart_lost 0, LAB review) and exits by time after it as without the restart; A, before the window, is not
    replayed."""
    c = Clock()
    six, entry = c.slots(6 * 3_600_000, 1_000_000), 1_000_041
    hwm = entry + c.slots(int(5.9 * 3_600_000), entry)
    toks = {"A": trig(500_000, [(500_100, "S", "sell", 150 * T)]),
            "B": trig(1_000_000, [(1_020_000, "Z", "buy", T), (1_040_000, "Z2", "buy", T), (1_060_000, "Z3", "buy", T)]),
            "C": ("C2", [(hwm, "X", "buy", T), (hwm + 200, "V", "buy", T), (entry + six + 2_000, "Y", "buy", T),
                         (entry + six + 6_000, "Y2", "buy", T)])}
    v = [(0.9, six)]  # tp 90 %, time 6 h (T in slots at the reference slot time, ms())
    u = engine(tmp_path, "u", Later(toks), variants=v)
    u.run()
    x = u.journal.rows("exits", "key = 'B'")[0]
    assert x["exit_reason"] == "time" and x["exit_slot"] > hwm
    assert crash(engine(tmp_path, "r", Later(toks, crash_after=12), variants=v))["slot"] == hwm
    start = RS.window_start_slot(hwm, c)
    assert 500_100 < start < 1_000_000 and hwm - entry < six
    r = engine(tmp_path, "r", Later(toks, from_slot=start), variants=v)
    r.run()
    assert content(r.journal) == content(u.journal) and wallet(r.journal) == wallet(u.journal)
    rec = json.loads(r.journal.state(RS.RESTARTS_KEY))[0]
    assert rec["first_slot"] == 1_000_000 and "restart_lost" not in rec["counts"], rec


def test_the_window_starts_at_the_first_segment_closed_36_hours_before_the_hwm():
    segs = [(i, "d", {"written_unix": 100_000 + 3_600 * i}) for i in range(50)]
    hwm = {"minute": (100_000 + 3_600 * 45) // 60}  # segment 45's minute
    assert RS.window_start_seq(segs, hwm) == 9  # closed at or after 45 h - 36 h
    assert RS.window_start_seq([(3, "d", {}), (4, "d", {})], hwm) == 3  # no close times: every segment
    assert RS.window_start_slot(1_000_000, Clock(), hours=1) == 1_000_000 - Clock().slots(3_600_000, 1_000_000)


def test_the_live_watchlist_rules_fit_in_the_window():
    """The replay rebuilds the live watchlist's state machine too: its longest rule window must end inside the
    window less the 6 h of the oldest position (archive_minutes 0 = an archived verdict kept forever, not a window:
    a pool archived before the window comes back with a fresh state, the watchlist's only approximation)."""
    import dataclasses

    from wallets import watchlist as W

    windows = {k: v for k, v in dataclasses.asdict(W.Rules()).items() if k.endswith("_minutes") and v}
    assert max(windows.values()) < (RS.WINDOW_H - 6) * 60, windows


def test_the_open_positions_pools_go_to_the_recorder_at_the_hwm(tmp_path):
    crash(engine(tmp_path, "r", Later(TOKENS, crash_after=CRASH)))
    kept = []
    r = engine(tmp_path, "r", Later(TOKENS, from_slot=500_500, kept=kept), wall_clock=lambda: 4_000_000_000)
    r.run()
    assert kept == [[]]  # once, at the resume (curve positions have no pool); never while catching up


def test_a_pool_learned_before_the_window_is_known_from_the_journal(tmp_path):
    """LAB review: a restart reads from the window's first segment (from_seq) while older segments are still on disk;
    what they taught comes from the journal."""
    from paper.live import LiveFeed

    root = str(tmp_path / "rec")
    seg(root, 0, 1_000_000, {"pump_amm/CreatePoolEvent": [pool_row("P0", "M0", 1_000_005)]})
    seg(root, 1, 1_001_000, {"pump_amm/CreatePoolEvent": [pool_row("P1", "M1", 1_001_005)]})
    seg(root, 2, 1_002_000, {"pump_amm/CreatePoolEvent": [pool_row("P2", "M2", 1_002_005)]})
    f = LiveFeed(root, follow=False)
    list(f.events())
    j = Journal(str(tmp_path / "j"))
    for kind, key, seq, slot, info in f.drain_learned():
        j.put("learned", kind=kind, key=key, seq=seq, slot=slot, info=info)
    j.commit()
    g = LiveFeed(root, follow=False, learned=j.rows("learned"), from_seq=2)  # segments 0 and 1 stay on disk
    assert g.pool("P0") == f.pool("P0") and g.pool("P1") == f.pool("P1") and g.pool("P2") is None
    list(g.events())
    assert g.new_pools == ["P2"] and g.pool("P2") == f.pool("P2")
    f.close()
    g.close()


class NoTime(Later):
    """A feed without block times (a recorder segment without its blocks/ rows): no minute is ever seen."""

    def events(self):
        import dataclasses

        for e in super().events():
            yield dataclasses.replace(e, block_time=None)


def test_a_hwm_without_a_minute_restarts_on_every_segment(tmp_path):
    """LAB review nit on 5b81361: a run that ends without having seen a block time checkpoints its HWM with minute
    null (N1). The next start must not fail on it: the window is every segment, and the restart resumes."""
    u = engine(tmp_path, "r", NoTime(TOKENS))
    u.run()
    hwm = json.loads(u.journal.state(RS.HWM_KEY))
    assert hwm["minute"] is None and hwm["slot"] == u.last_slot + 1
    segs = [(i, "d", {"written_unix": 100 + i}) for i in range(3)]
    assert RS.window_start_seq(segs, hwm) == 0 and RS.window_start_seq([], hwm) == 0
    u.journal.close()
    r = engine(tmp_path, "r", NoTime(TOKENS))
    assert r.replaying
    r.run()
    rec = json.loads(r.journal.state(RS.RESTARTS_KEY))
    assert len(rec) == 1 and rec[0]["hwm"] == hwm["slot"]
    assert {k: v for k, v in rec[0]["counts"].items() if k not in RS.BEFORE} == {}, rec


def test_the_live_start_takes_its_window_from_a_hwm_without_a_minute(tmp_path, monkeypatch):
    """engine.main --live on a journal whose HWM has no minute replays every segment (from_seq 0)."""
    from paper import engine as E
    from paper import live
    from paper.tests.test_livefeed_state import fake_store, segment

    root = tmp_path / "rec"
    for i in range(3):
        segment(root, i, 1_000 + 100 * i, 1_099 + 100 * i, 1_700_000_000 + 60 * i, [])
    j = Journal(str(tmp_path / "j"))
    j.state(RS.HWM_KEY, json.dumps({"slot": 1_250, "minute": None, "day": None}))
    j.state(RS.HWM_STATE_KEY, json.dumps({"groups": {}, "busy": []}))
    j.commit()
    j.close()
    seen = {}

    class Stop(Exception):
        pass

    def feed(*a, **k):
        seen.update(k)
        raise Stop

    monkeypatch.setattr(live, "LiveFeed", feed)
    store = fake_store(tmp_path / "s.duckdb", range(0, 10, 10))
    with pytest.raises(Stop):
        E.main(["--live", str(root), "--catch-up", "--journal", str(tmp_path / "j"), "--store", store, "--no-backup",
                "--no-watchlist"])
    assert seen["from_seq"] == 0


@pytest.mark.parametrize("from_slot", [501_301, 10**9], ids=["first-event-past-the-hwm", "no-event"])
def test_a_resume_before_any_block_time_books_on_the_hwms_day(tmp_path, monkeypatch, from_slot):
    """Codex 1 (LAB review: confirmed). The keeper removed every segment up to the HWM (an outage of more than ~3 days,
    or a new recorder root), or a catch-up brings no event at all: the replay resumes before it saw a block time. B's
    restart_lost exit is booked on the HWM's day, the HWM's daily loss stays (no day change follows inside that day),
    and that day closes with its export and report. A's exit lies before the journal rows this replay reads (a margin
    of 100 slots here): the day loss it lacks is from before the replay, no difference. Without any event the HWM stays
    where it was, but its state takes the restart's closes, so the next start keeps B's loss and B is not busy."""
    from paper.report import daily_report

    monkeypatch.setattr(RS, "MARGIN_SLOTS", 100)
    crashed(tmp_path)
    r = engine(tmp_path, "r", Later(TOKENS, from_slot=from_slot))
    day = r.rs.hwm["day"]
    assert day
    r.run()
    xs = r.journal.rows("exits", "key = 'B'")
    assert len(xs) == 1 and xs[0]["exit_reason"] == "restart_lost" and xs[0]["day"] == day
    a = r.journal.rows("exits", "key = 'A'")[0]
    assert r.primary.day_loss == a["pnl"] + xs[0]["pnl"]
    rec = json.loads(r.journal.state(RS.RESTARTS_KEY))[0]
    assert rec["day"] == day and rec["counts"]["restart_lost"] == 1 and "day_loss_different" not in rec["counts"]
    assert rec["counts"]["day_loss_from_before_the_replay"] == 1
    report = open(os.path.join(r.journal.dir, day, "report.md")).read()
    assert "## Restarts" in report and "restart_lost 1" in report
    assert [x["key"] for x in pq.read_table(os.path.join(r.journal.dir, day, "exits.parquet")).to_pylist()] == ["A", "B"]
    daily_report(r.journal, day)  # every exit has a day: the stop rule's day comparison holds
    exits, w = r.journal.rows("exits"), wallet(r.journal)
    assert w["balance_lamports"] == 10 * SOL + a["pnl"] + xs[0]["pnl"]
    r.journal.close()
    again = engine(tmp_path, "r", Later(TOKENS, from_slot=500_500))  # the next start, on the whole window
    again.run()
    w2 = wallet(again.journal)
    assert again.journal.rows("exits") == exits and (w2["balance_lamports"], w2["txs"]) == (w["balance_lamports"], w["txs"])
    assert again.primary.day_loss == a["pnl"] + xs[0]["pnl"] and again.busy.get(("F7", "tp4_T600", "B")) != "open"


def test_a_position_the_replay_refills_differently_is_closed_with_the_journals_fill(tmp_path):
    """Codex 2 (LAB review: confirmed). The replay refills B differently from the journal's buy (here the first run's
    fill had fewer tokens). The replay's position rests on its own fill and pool state, so it cannot carry the
    journal's: B is closed at the HWM at its last journaled mark (restart_lost) with the journal's tokens and cost.
    The buy fill, the exit and the wallet agree; the difference is counted (fills_different)."""
    crashed(tmp_path)
    j = Journal(str(tmp_path / "r"))
    j.db.execute("UPDATE fills SET tokens = tokens - 1000 WHERE key = 'B' AND side = 'buy'")
    j.db.execute("UPDATE positions SET tokens = tokens - 1000 WHERE key = 'B'")
    j.db.commit()
    buy, mark = j.rows("fills", "key = 'B' AND side = 'buy'")[0], j.rows("positions", "key = 'B'")[0]
    j.close()
    r = engine(tmp_path, "r", Later(TOKENS, from_slot=500_500))
    r.run()
    xs = r.journal.rows("exits", "key = 'B'")
    assert len(xs) == 1 and xs[0]["exit_reason"] == "restart_lost" and xs[0]["exit_slot"] == 501_300
    assert (xs[0]["tokens"], xs[0]["cost"], xs[0]["pnl"]) == (buy["tokens"], buy["quote"], mark["mark_pnl"])
    assert r.journal.rows("fills", "key = 'B'") == [buy] and not r.journal.rows("positions")
    a = r.journal.rows("exits", "key = 'A'")[0]
    assert wallet(r.journal)["balance_lamports"] == 10 * SOL + a["pnl"] + xs[0]["pnl"]
    rec = json.loads(r.journal.state(RS.RESTARTS_KEY))[0]
    # B stays busy ('open', as journaled) until its close: no busy difference. The replay holds no B: its exposure.
    assert {k: v for k, v in rec["counts"].items() if k not in RS.BEFORE} == {
        "fills_different": 1, "restart_lost": 1, "exposure_different": 1}, rec["counts"]


# Slot 520_000 is 00:00 UTC in the synthetic clock (1_790_000_000 + 0.4 s per slot).
DAY_TOKENS = {"A": trig(500_000, [(500_100, "W", "buy", 150 * T)]),
              "B": trig(519_500, [(519_700, "Z", "buy", T), (520_600, "Y", "buy", 150 * T)]),
              "C": ("C2", [(519_800, "X", "buy", T), (520_000, "X2", "buy", T), (520_150, "V", "buy", T),
                           (520_700, "U", "buy", T)]),
              "E": trig(519_610, [(519_750, "W2", "buy", 150 * T)])}  # entered and closed on the old day


def test_a_resume_before_the_hwms_slot_takes_the_hwms_day_not_the_replays(tmp_path):
    """LAB review on Codex 1: the HWM is the first slot of a day (520_000) and the replay lacks it, so its last day is
    the day before. It resumes before _clock dates the next slot (520_150): B's restart_lost exit, the restart and the
    daily loss are the HWM's day's, not the replay's older one. The replay's own day loss (E, closed on the old day) is
    no difference to the HWM's."""
    from paper.engine import _day

    e = engine(tmp_path, "r", Later(DAY_TOKENS, crash_after=15))  # dies after its commit at 520_000, B open
    hwm = crash(e)
    assert hwm["slot"] == 520_000 and hwm["day"] == _day(1_790_000_000 + int(520_000 * 0.4))
    assert [p["key"] for p in e.journal.rows("positions")] == ["B"]
    before = json.loads(e.journal.state(RS.HWM_STATE_KEY))["groups"]["K3"]["day_loss"]
    e.journal.close()
    r = engine(tmp_path, "r", Later(DAY_TOKENS, from_slot=519_600, skip={520_000}))  # B's entry before the window
    r.run()
    xs = r.journal.rows("exits", "key = 'B'")
    assert len(xs) == 1 and xs[0]["exit_reason"] == "restart_lost" and xs[0]["day"] == hwm["day"]
    rec = json.loads(r.journal.state(RS.RESTARTS_KEY))[0]
    assert rec["day"] == hwm["day"] and r.primary.day_loss == before + xs[0]["pnl"]
    assert r.journal.rows("exits", "key = 'E'") and "day_loss_different" not in rec["counts"], rec["counts"]


def test_a_dated_slot_at_an_end_of_run_hwm_keeps_its_own_day(tmp_path):
    """LAB review on Codex 1: a run that ended has its HWM at its last slot + 1 with the last slot's day, which it closed
    itself. When that slot starts the next day and the next start has an event there, _clock dates it before the
    resume: the slot's signals are the new day's, and the new day starts without the old day's loss."""
    from paper.engine import _day

    first = {"A": TOKENS["A"], "C": ("C2", [(519_900, "X", "buy", T), (519_999, "X1", "buy", T)])}  # A: a loss on the old day
    u = engine(tmp_path, "r", Later(first))
    u.run()
    hwm = json.loads(u.journal.state(RS.HWM_KEY))
    old, new = _day(1_790_000_000 + int(519_999 * 0.4)), _day(1_790_000_000 + int(520_000 * 0.4))
    assert (hwm["slot"], hwm["day"]) == (520_000, old) and old != new
    u.journal.close()
    later = {**first, "T": trig(519_960, creator="CT")}  # its trigger, the large sell, is at 520_000
    r = engine(tmp_path, "r", Later(later))
    r.run()
    sig = r.journal.rows("signals", "key = 'T'")
    assert sig and {s["t"] for s in sig} == {520_000} and {s["day"] for s in sig} == {new}
    assert json.loads(r.journal.state(RS.RESTARTS_KEY))[0]["day"] == new
    assert r.primary.day_loss == sum(x["pnl"] for x in r.journal.rows("exits", "day = ?", (new,)))


def test_an_exit_the_journal_booked_on_the_old_day_at_an_end_of_run_hwm_is_not_the_new_days_loss(tmp_path):
    """Second review round: the run that ended decided B's stop loss at its last slot (519_999), filled at the HWM's
    slot (520_000, the next day) and booked by its end on the old day, whose daily loss holds it. The next start
    dates 520_000 before it resumes: that journal exit does not count against the new day's daily loss as well, and a
    third start that replays 520_000 on the new day finds no difference to the journal."""
    from paper.engine import _day

    first = {"B": trig(519_500, [(519_700, "Z", "buy", T), (519_999, "S", "sell", 150 * T)]),
             "C": ("C2", [(519_900, "X", "buy", T), (519_999, "X1", "buy", T)])}
    u = engine(tmp_path, "r", Later(first))
    u.run()
    old, new = _day(1_790_000_000 + int(519_999 * 0.4)), _day(1_790_000_000 + int(520_000 * 0.4))
    xb = u.journal.rows("exits", "key = 'B'")
    assert [(x["exit_slot"], x["day"]) for x in xb] == [(520_000, old)] and xb[0]["pnl"] < 0
    assert json.loads(u.journal.state(RS.HWM_KEY))["slot"] == 520_000
    u.journal.close()
    later = {**first, "C": ("C2", [(519_900, "X", "buy", T), (519_999, "X1", "buy", T), (520_000, "X2", "buy", T),
                                   (520_100, "X3", "buy", T), (520_150, "X4", "buy", T), (520_300, "X5", "buy", T),
                                   (520_400, "X6", "buy", T)])}
    e = engine(tmp_path, "r", Later(later, crash_after=11))  # dies after its commit at 520_150, on the new day
    assert crash(e)["slot"] == 520_150
    rec = json.loads(e.journal.state(RS.RESTARTS_KEY))[0]
    assert rec["day"] == new and json.loads(e.journal.state(RS.HWM_STATE_KEY))["groups"]["K3"]["day_loss"] == 0
    e.journal.close()
    # A third start replays 520_000 on the new day: the old day's exit is no difference there either (review round 3).
    r = engine(tmp_path, "r", Later(later))
    r.run()
    rec = json.loads(r.journal.state(RS.RESTARTS_KEY))[-1]
    assert rec["hwm"] == 520_150 and {k: v for k, v in rec["counts"].items() if k not in RS.BEFORE} == {}, rec["counts"]
    assert r.primary.day_loss == sum(x["pnl"] for x in r.journal.rows("exits", "day = ?", (new,))) == 0
