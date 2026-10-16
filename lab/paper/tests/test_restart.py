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
    """The recorder's segments after the keeper removed the oldest: the events from `from_slot` on."""

    def __init__(self, tokens, from_slot=0, crash_after=None, kept=None):
        super().__init__(tokens, crash_after)
        self.from_slot, self.kept = from_slot, kept

    def events(self):
        for e in super().events():
            if e.slot >= self.from_slot:
                yield e

    def keep_pools(self, pools):
        if self.kept is not None:
            self.kept.append(list(pools))


def engine(tmp_path, name, feed, risk=None, **kw):
    return Engine(feed, Journal(str(tmp_path / name)), risk or risk_file(tmp_path), variants=[(0.04, 600)], d=1, q=0.5,
                  n2_k=0, clock=Clock(), backup=False, restart=True, **kw)


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
