"""Paper engine on a synthetic feed: F7 trigger and filters, entry and exits (take profit, stop
loss, time), the N2 shadow, risk limits and the kill switch, feed gaps with the heartbeat, daily
report and backup, and restart idempotency of the journal."""

import json
import os

import pytest
import yaml

from backtest.replay import CurveLeg
from backtest.strategies import Clock
from backtest.venues import Curve, curve_buy_exact_out, curve_sell
from paper import engine
from paper.engine import Engine
from paper.feed import Event, Feed, TokenInfo
from paper.journal import Journal
from paper.report import daily_report

SOL = 1_000_000_000
START = Curve(1_073_000_000_000_000 - 400_000_000_000_000, 30 * SOL + 40 * SOL,
              793_100_000_000_000 - 400_000_000_000_000, 40 * SOL, 95, 30)
CREATE = 450_000


class Synth(Feed):
    """One or more pump tokens; trades given as (slot, trader, 'buy'|'sell', tokens)."""

    def __init__(self, tokens, crash_after=None):
        self.meta_bounds = (0, 10_000_000)
        self.tok, self.legs, self.crash_after = {}, {}, crash_after
        for mint, (creator, trades) in tokens.items():
            self.tok[mint] = TokenInfo(mint, CREATE, creator, False, "11111111111111111111111111111111", None, None)
            s, legs = START, []
            for tx, (slot, trader, side, t) in enumerate(trades):
                f = curve_buy_exact_out(s, t) if side == "buy" else curve_sell(s, t)
                legs.append(CurveLeg(slot, tx, (slot, tx, 0, 0), side == "buy", t, f.curve_quote, f.fees, side, s, f.state,
                                     trader, max_cost=10**15 if side == "buy" else None, min_out=0 if side == "sell" else None,
                                     direct=True))
                s = f.state
            self.legs[mint] = legs

    def token(self, mint):
        return self.tok.get(mint)

    def pool(self, pool):
        return None

    def curve_history(self, mint, upto_slot):
        return [l for l in self.legs[mint] if l.slot <= upto_slot]

    def events(self):
        evs = sorted((Event(l.slot, l.tx, 0, 0, 1_790_000_000 + int(l.slot * 0.4), "curve", m, l)
                      for m, legs in self.legs.items() for l in legs), key=lambda e: (e.order, e.key))
        for i, e in enumerate(evs):
            if self.crash_after is not None and i == self.crash_after:
                raise RuntimeError("simulated crash")
            yield e


def risk_file(tmp_path, **over):
    r = {"size_sol": 0.5, "max_open_positions": 5, "max_exposure_sol": 2.5, "daily_loss_limit_sol": 1.0,
         "kill_switch": False, "families": ["F7"]}
    r.update(over)
    p = tmp_path / f"risk-{len(os.listdir(tmp_path))}.yaml"
    p.write_text(yaml.safe_dump(r))
    return str(p)


def run(tmp_path, tokens, risk, variants=((0.04, 600),), name="j", crash_after=None, n2_k=0, backup=False):
    j = Journal(str(tmp_path / name))
    eng = Engine(Synth(tokens, crash_after), j, risk, variants=list(variants), d=1, q=0.5, n2_k=n2_k, clock=Clock(),
                 backup=backup)
    eng.run()
    return j


T = 1_000_000_000_000  # tokens in a typical trade (~0.1 SOL)


def trigger_token(creator="C", after=()):
    """Buys (inflow), then a large non-creator sell at slot 500_040 (the F7 trigger), then `after`."""
    trades = [(500_000, "A", "buy", 20 * T), (500_010, "A2", "buy", 20 * T), (500_040, "B", "sell", 60 * T)]
    return (creator, trades + list(after))


def test_f7_trigger_entry_and_take_profit(tmp_path):
    j = run(tmp_path, {"M": trigger_token(after=[(500_100, "W", "buy", 150 * T), (500_200, "Z", "buy", T)])},
            risk_file(tmp_path))
    sig = j.rows("signals")
    assert [(s["key"], s["t"], s["status"]) for s in sig] == [("M", 500_040, "entered")]
    o = j.rows("orders")[0]
    assert o["status"] == "filled" and o["entry_slot"] == 500_041
    x = j.rows("exits")[0]
    assert x["exit_reason"] == "take_profit" and x["pnl"] > 0


def test_stop_loss_and_time_exit(tmp_path):
    j = run(tmp_path, {"M": trigger_token(after=[(500_100, "S", "sell", 150 * T), (500_200, "Z", "buy", T)])},
            risk_file(tmp_path), name="sl")
    assert j.rows("exits")[0]["exit_reason"] == "stop_loss"
    j = run(tmp_path, {"M": trigger_token(after=[(501_000, "Z", "buy", T)])}, risk_file(tmp_path),
            variants=((0.5, 150),), name="time")
    x = j.rows("exits")[0]
    assert x["exit_reason"] == "time" and x["exit_decision_slot"] == 500_041 + 150


def test_filters_block_the_signal(tmp_path):
    # The creator sold within the last 200 slots: no signal.
    creator_sold = ("C", [(499_900, "C", "buy", 10 * T), (499_950, "C", "sell", 5 * T)] + trigger_token()[1])
    j = run(tmp_path, {"M": creator_sold}, risk_file(tmp_path))
    assert j.rows("signals") == []
    # No inflow over the 50 slots before: no signal.
    j = run(tmp_path, {"M": ("C", [(500_040, "B", "sell", 60 * T)])}, risk_file(tmp_path), name="noflow")
    assert j.rows("signals") == []


def test_risk_limits_and_kill_switch(tmp_path):
    two = {"M1": trigger_token(), "M2": trigger_token()}
    j = run(tmp_path, two, risk_file(tmp_path, max_open_positions=1), name="maxopen")
    st = sorted((s["key"], s["status"], s["reason"]) for s in j.rows("signals"))
    assert st == [("M1", "entered", None), ("M2", "skipped", "max open positions")]
    j = run(tmp_path, two, risk_file(tmp_path, kill_switch=True), name="kill")
    assert {s["reason"] for s in j.rows("signals")} == {"kill switch"}
    bad = tmp_path / "bad.yaml"
    bad.write_text("size_sol: -1\n")
    j = run(tmp_path, two, str(bad), name="bad")
    assert all(s["status"] == "skipped" and s["reason"].startswith("risk config") for s in j.rows("signals"))


def _content(j):
    return {t: j.rows(t) for t in ("signals", "orders", "fills", "exits", "heartbeat", "positions")}


def test_restart_gives_the_same_journal(tmp_path):
    toks = {"M1": trigger_token(after=[(500_100, "W", "buy", 150 * T)]),
            "M2": trigger_token(after=[(500_300, "S", "sell", 150 * T), (500_400, "Z", "buy", T)])}
    full = _content(run(tmp_path, toks, risk_file(tmp_path), name="full"))
    with pytest.raises(RuntimeError):
        run(tmp_path, toks, risk_file(tmp_path), name="restart", crash_after=6)  # dies halfway
    again = _content(run(tmp_path, toks, risk_file(tmp_path), name="restart"))  # resumed: same journal directory
    assert again == full
    assert full["exits"] and full["positions"] == []


def test_n2_shadow_beside_f7(tmp_path):
    # M2 traded this hour, before the trigger, at a similar curve depth: it is M's N2 match.
    toks = {"M": trigger_token(after=[(500_100, "W", "buy", 150 * T)]),
            "M2": ("C2", [(500_020, "X", "buy", T), (500_300, "Y", "buy", T)])}
    j = run(tmp_path, toks, risk_file(tmp_path, max_open_positions=1), n2_k=1)
    sig = {(s["family"], s["key"]): (s["t"], s["status"]) for s in j.rows("signals")}
    assert sig == {("F7", "M"): (500_040, "entered"), ("N2_F7", "M2"): (500_040, "entered")}  # N2 is not risk-limited
    ex = {x["family"]: x for x in j.rows("exits")}
    assert ex["N2_F7"]["key"] == "M2" and ex["N2_F7"]["entry_slot"] == 500_041  # filled in a slot without M2 events
    rows = {r["family"]: r for r in daily_report(j, ex["F7"]["day"])}
    assert rows["F7"]["vs_n2_sol_per_trade"] == pytest.approx(
        rows["F7"]["net_sol_per_trade"] - rows["N2_F7"]["net_sol_per_trade"])
    # A token that only trades after the trigger is not a match (no look-ahead).
    toks["M2"] = ("C2", [(500_041, "X", "buy", T)])
    j = run(tmp_path, toks, risk_file(tmp_path), n2_k=1, name="late")
    assert {s["family"] for s in j.rows("signals")} == {"F7"}


def test_feed_gap_heartbeat_report_and_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "BACKUP", str(tmp_path / "backup"))
    # No events between slot 500_040 (the trigger) and 501_000: a feed gap of 959 slots (and one of 29
    # slots before the trigger; gaps of more than GAP_SLOTS are logged).
    j = run(tmp_path, {"M": trigger_token(after=[(501_000, "Z", "buy", T)])}, risk_file(tmp_path),
            variants=((0.5, 150),), name="gap", backup=True)
    beats = j.rows("heartbeat")
    gaps = [(b["last_slot"], b["gap_slots"], b["note"]) for b in beats if b["note"]]
    assert gaps == [(500_010, 29, "feed gap 500010..500040"), (500_040, 959, "feed gap 500040..501000")]
    minute = [b for b in beats if not b["note"] and b["last_slot"] == 500_040]
    assert len(minute) == 1 and minute[0]["gap_slots"] == 959  # the first event after the gap starts a new minute
    # The position is not stuck in the gap: its time exit is decided at entry + T on the unchanged state.
    x = j.rows("exits")[0]
    assert x["exit_reason"] == "time" and x["exit_decision_slot"] == 500_041 + 150 and x["exit_slot"] < 501_000
    day = x["day"]
    assert "feed gaps: 2 (largest 959 slots)" in (tmp_path / "gap" / day / "report.md").read_text()
    # The day's Parquet files and report are copied to the backup directory.
    assert sorted(os.listdir(tmp_path / "backup" / "gap" / day)) == sorted(os.listdir(tmp_path / "gap" / day))


def test_segments_limit_where_f7_trades(tmp_path):
    toks = {"M": trigger_token(after=[(500_100, "W", "buy", 150 * T)])}
    j = Journal(str(tmp_path / "seg"))
    Engine(Synth(toks), j, risk_file(tmp_path), variants=[(0.04, 600)], d=1, q=0.5, n2_k=0, clock=Clock(), backup=False,
           segments=["pool_reversed"]).run()
    assert j.rows("signals") == []  # a curve trigger, but only pool_reversed is traded
    j = Journal(str(tmp_path / "curve"))
    Engine(Synth(toks), j, risk_file(tmp_path), variants=[(0.04, 600)], d=1, q=0.5, n2_k=0, clock=Clock(), backup=False,
           segments=["curve"], farm="organic").run()
    assert [(s["status"], s["farm"]) for s in j.rows("signals")] == [("entered", "n/a")]


def test_farming_window_and_the_organic_filter(tmp_path):
    """The backtest's pool flag: farmers' SOL volume > 50 % of all over slots [t - 1 h, t - 1]."""
    from types import SimpleNamespace as NS

    from backtest.strategies import F6_FARM_MS
    from paper.feed import PoolInfo

    class Pools(Feed):
        meta_bounds = (0, 10**9)

        def pool(self, p):
            return PoolInfo(p, "M", "reversed", "reversed", False, None, 0)

        def token(self, m):
            return None

    def leg(slot, sol, farmer):
        return NS(kind="sell", slot=slot, order=(slot, 0, 0, 0), farmer=farmer, base=sol * SOL, pre=NS(b=200 * SOL))

    eng = Engine(Pools(), Journal(str(tmp_path / "farm")), None, ignore_risk=True, clock=Clock(), backup=False, farm="organic")
    eng._farm_add("P", [leg(100, 10, True), leg(101, 4, False)])
    eng._farm_add("P", [leg(102, 8, False), leg(101, 4, False)])  # the same trade again (a backfill overlap) counts once
    assert eng._pool_flagged("P", 101) and eng._pool_flagged("P", 102)  # 10 of 10, 10 of 14 SOL
    assert not eng._pool_flagged("P", 103)  # 10 of 22: not more than half
    assert not eng._pool_flagged("P", 100 + eng.clock.slots(F6_FARM_MS, 100) + 1)  # the farming trade left the hour
    assert not eng._pool_flagged("Q", 103)  # no volume: organic, as the SQL's COALESCE(..., false)
    eng._load_risk()
    eng._signal("F7", "tp4_T600", "P", "pool", "reversed", 102, (0.04, 600), 200 * SOL, "B", "flagged")
    eng._signal("F7", "tp4_T600", "P", "pool", "reversed", 103, (0.04, 600), 200 * SOL, "B", "organic")
    assert [(s["t"], s["status"], s["reason"], s["farm"]) for s in eng.j.rows("signals")] == [
        (102, "skipped", "farm flagged", "flagged"), (103, "entered", None, "organic")]


def test_open_positions_carry_a_mark_and_the_paper_wallet(tmp_path):
    toks = {"M": trigger_token(after=[(500_100, "W", "buy", 30 * T), (500_200, "Z", "buy", T), (500_300, "Y", "buy", 150 * T)])}
    j = Journal(str(tmp_path / "mark"))
    eng = Engine(Synth(toks, crash_after=5), j, risk_file(tmp_path), variants=[(0.04, 600)], d=1, q=0.5, n2_k=0,
                 clock=Clock(), backup=False)
    with pytest.raises(RuntimeError):
        eng.run()  # stopped after the event of slot 500_200: the position is open
    pos = j.rows("positions")
    assert len(pos) == 1 and pos[0]["mark_slot"] >= pos[0]["entry_slot"] and pos[0]["mark_proceeds"] > 0
    assert pos[0]["mark_pnl"] == pos[0]["mark_proceeds"] - eng.scn.entry_total(pos[0]["cost"])
    # A complete run: the take profit closes it; wallet.json is the UI's format, marked as paper.
    j = run(tmp_path, toks, risk_file(tmp_path), name="wallet")
    import json as _json

    with open(os.path.join(j.dir, "wallet.json")) as f:
        w = _json.load(f)
    x = j.rows("exits")[0]
    assert w["mode"] == "paper" and w["address"] is None and w["balance_lamports"] == 10 * SOL + x["pnl"]
    assert [t["kind"] for t in w["txs"]] == ["sell", "buy"] and w["txs"][1]["amount_lamports"] < 0
    assert sum(t["amount_lamports"] for t in w["txs"]) == x["pnl"]


def test_pool_cooldown_after_a_total_loss(tmp_path):
    from backtest.replay import Result
    from paper.engine import Open, Pending

    eng = Engine(Synth({}), Journal(str(tmp_path / "cool")), risk_file(tmp_path, pool_cooldown_h=24), clock=Clock(),
                 backup=False)
    eng._load_risk()
    p = Pending("F7", "tp4_T600", "P", "pool", "reversed", 1_000, 1_001, (0.04, 600), 200 * SOL)
    r = Result("P", 1_000, size=int(0.5 * SOL), venue="pool", entry_slot=1_001, tokens=10**12, cost=int(0.5 * SOL),
               proceeds=int(0.04 * SOL), exit_slot=1_050, exit_reason="stop_loss", exit_attempts=1)
    eng._settle_if_done(Open(p, r, None, 1_001), 1_050, force=True)
    day = eng.clock.slots(24 * 3_600_000, 1_050)
    assert eng.cool["P"] == 1_050 + day
    assert eng._risk_block("F7", "P", 1_060) == "pool cooldown" and eng._risk_block("F7", "P", 1_051 + day) is None
    assert eng._risk_block("N2_F7", "P", 1_060) is None  # the shadow is not limited
    # A loss that is not total leaves the pool open.
    r2 = Result("Q", 1_000, size=int(0.5 * SOL), venue="pool", entry_slot=1_001, tokens=10**12, cost=int(0.5 * SOL),
                proceeds=int(0.3 * SOL), exit_slot=1_050, exit_reason="stop_loss", exit_attempts=1)
    eng._settle_if_done(Open(Pending("F7", "tp4_T600", "Q", "pool", "reversed", 1_000, 1_001, (0.04, 600), 0), r2, None, 1_001),
                        1_050, force=True)
    assert "Q" not in eng.cool


def test_paper_config_file(tmp_path):
    from paper.engine import load_config

    sha, conf = load_config(os.path.join(os.path.dirname(engine.__file__), "k3-paper-2026-10-12.yaml"))
    assert (conf["segments"], conf["farm"], conf["variants"], conf["d"], conf["q"]) == (["pool_reversed"], "all", ["0.04:600"], 2, 0.5)
    assert os.path.exists(conf["risk_path"]) and len(sha) == 64
    from paper import risk

    r, why = risk.load(conf["risk_path"])
    assert why is None and (r.size_sol, r.max_open_positions, r.daily_loss_limit_sol, r.pool_cooldown_h) == (0.5, 5, 1.0, 24.0)


def test_ignore_risk_is_refused_for_paper_runs(tmp_path, capsys):
    """A paper run (a config, or live data) always has its risk limits: --ignore-risk is for replay parity only."""
    conf = os.path.join(os.path.dirname(engine.__file__), "k3-paper-2026-10-12.yaml")
    for extra in (["--live", str(tmp_path)], ["--config", conf, "--from", "1", "--to", "2"]):
        with pytest.raises(SystemExit) as e:
            engine.main(extra + ["--ignore-risk", "--journal", str(tmp_path / "j")])
        assert e.value.code == 2
        assert "--ignore-risk is for replay parity runs only" in capsys.readouterr().err
    assert not (tmp_path / "j").exists()  # refused before anything was opened



class FlipRisk(Synth):
    """Rewrites the risk file when the first event of `at` (or later) is handed out: after the signal
    at an earlier slot, before its entry."""

    def __init__(self, tokens, path, at, text):
        super().__init__(tokens)
        self.path, self.at, self.text, self.done = path, at, text, False

    def events(self):
        for e in super().events():
            if not self.done and e.slot >= self.at:
                with open(self.path, "w") as f:
                    f.write(self.text)
                self.done = True
            yield e


def test_risk_rechecked_at_entry(tmp_path):
    """The kill switch, or a risk file that turned invalid, between the signal and the entry opens nothing."""
    toks = {"M": trigger_token(after=[(500_041, "Z", "buy", T), (500_100, "W", "buy", 150 * T)])}
    for name, text, why in (("kill", None, "at entry: kill switch"), ("bad", "size_sol: -1\n", "at entry: risk config")):
        path = risk_file(tmp_path)
        if text is None:
            text = open(path).read().replace("kill_switch: false", "kill_switch: true")
        j = Journal(str(tmp_path / name))
        Engine(FlipRisk(toks, path, 500_041, text), j, path, variants=[(0.04, 600)], d=1, q=0.5, n2_k=0, clock=Clock(),
               backup=False).run()
        assert [(s["status"]) for s in j.rows("signals")] == ["entered"]  # the file was fine at the signal
        o = j.rows("orders")[0]
        assert o["status"] == "skipped" and o["reason"].startswith(why) and o["size"] == 0
        assert j.rows("fills") == [] and j.rows("exits") == []


def test_quiet_token_history_is_swept_hourly(tmp_path):
    """recent / last_state / last_seen of a token without events for an hour (and nothing open) are dropped."""
    toks = {"A": ("C", [(500_000, "A1", "buy", T), (500_010, "A2", "buy", T)]),
            "B": ("C", [(600_000, "B1", "buy", T), (600_010, "B2", "buy", T)])}
    eng = Engine(Synth(toks), Journal(str(tmp_path / "j")), risk_file(tmp_path), variants=[(0.04, 600)], d=1, q=0.5,
                 n2_k=0, clock=Clock(), backup=False)
    eng.run()
    assert "A" not in eng.last_seen and "A" not in eng.last_state and "A" not in eng.recent
    assert eng.last_seen["B"] == 600_010 and eng.recent["B"]


def test_farmer_seed_range_is_journaled(tmp_path):
    seed = {"store": "/x/fwd.duckdb", "lo": 1, "hi": 2, "h24": 1, "first_live_slot": 2, "complete_from": 2}

    class Seeded(Synth):
        farm_seed = seed

    j = Journal(str(tmp_path / "j"))
    Engine(Seeded({"M": trigger_token()}), j, risk_file(tmp_path), variants=[(0.04, 600)], d=1, q=0.5, n2_k=0,
           clock=Clock(), backup=False).run()
    assert json.loads(j.state("farmer_seed")) == seed


def test_live_needs_a_readable_store(tmp_path, capsys):
    for store in ("", str(tmp_path / "missing.duckdb")):
        with pytest.raises(SystemExit) as e:
            engine.main(["--live", str(tmp_path), "--store", store, "--journal", str(tmp_path / "j")])
        assert e.value.code == 2 and "--live needs a readable --store" in capsys.readouterr().err
