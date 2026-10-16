"""The paper arms on a dev slice (replay, the risk files of the paper config): K3's journal rows and wallet are the
same with and without group H (its own budget), the arms trade, and a restart with an open arm position gives the
same journal (research/week4-spec-paper-h4h5-2026-10-16.md §6.4). The arms' parity with the backtest is
test_parity's (paper/parity.py --arm on a whole chunk on the NAS)."""

import json
import os

import pytest

from backtest import streams
from backtest.strategies import Clock
from paper import engine
from paper.engine import Engine, config_groups, load_config, load_groups
from paper.feed import ReplayFeed
from paper.journal import Journal

STORE = os.environ.get("LAB_STORE", "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb")
LO, HI = 450_130_000, 450_175_000
TABLES = ("signals", "orders", "fills", "exits", "positions")


class CrashFeed(ReplayFeed):
    def __init__(self, *a, crash_slot=None, **k):
        super().__init__(*a, **k)
        self.crash_slot = crash_slot

    def events(self):
        for e in super().events():
            if self.crash_slot is not None and e.slot >= self.crash_slot:
                raise RuntimeError("simulated crash")
            yield e


def _run(journal_dir, clock, with_h=True, crash_slot=None):
    here = os.path.dirname(engine.__file__)
    _, conf = load_config(os.path.join(here, "k3-paper-2026-10-12.yaml"))
    groups = config_groups(load_groups(os.path.join(here, "h-arms-2026-10-16.yaml"))[1]) if with_h else ()
    feed = CrashFeed(STORE, LO, HI, crash_slot=crash_slot)
    eng = Engine(feed, Journal(journal_dir), conf["risk_path"], variants=conf["variants"], d_ms=conf["d_ms"], q=conf["q"],
                 n2_k=conf["n2_k"], segments=conf["segments"], farm=conf["farm"], clock=clock, backup=False,
                 dev_bounds=(LO - 400_000, HI), groups=groups)
    eng.run()
    return eng


def _content(j, where="1=1"):
    return {t: j.rows(t, where) for t in TABLES}


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    if not os.path.exists(STORE):
        pytest.skip("dev store not built")
    con = streams.open_store(STORE, threads=2, memory="1GB")
    clock = Clock.from_store(con)
    con.close()
    d = tmp_path_factory.mktemp("arms")
    both = _run(str(d / "both"), clock)
    k3 = _run(str(d / "k3"), clock, with_h=False)
    return d, clock, both, k3


def test_k3_is_the_same_with_and_without_group_h(runs):
    _, _, both, k3 = runs
    only_k3 = "variant = 'tp4_T600'"
    a, b = _content(both.j, only_k3), _content(k3.j, only_k3)
    assert a == b and a["signals"] and a["exits"]
    w = [json.load(open(os.path.join(e.j.dir, "wallet.json"))) for e in (both, k3)]
    assert w[0] == w[1]
    arms = both.j.rows("signals", "variant IN ('flow300_H6', 'flow300_tp100_H6')")
    assert arms and not k3.j.rows("signals", "variant <> 'tp4_T600'")
    assert {s["farm"] for s in arms if s["status"] == "entered"} == {"organic"}  # the arms open on organic triggers only
    assert os.path.exists(os.path.join(both.j.dir, "wallet-h.json"))


def test_the_arms_trade_with_their_windows_in_the_exits(runs):
    _, clock, both, _ = runs
    xs = both.j.rows("exits", "variant IN ('flow300_H6', 'flow300_tp100_H6') AND tokens > 0")
    assert xs, "no arm position on the slice"
    assert {x["exit_reason"] for x in xs} <= {"outflow", "take_profit", "time", "end_of_data"}
    for x in xs:
        assert x["n_flow_slots"] == clock.slots(300_000, x["t"]) and x["cap_slots"] == clock.slots(6 * 3_600_000, x["t"])
        assert x["peak_mark"] is not None and x["peak_slot"] >= x["entry_slot"]
    sig = both.j.rows("signals", "variant = 'flow300_H6'")
    assert all(s["pool_seen_slot"] is not None and s["farm_window_complete"] in (0, 1) for s in sig)


def test_a_restart_with_an_open_arm_position_gives_the_same_journal(runs):
    d, clock, both, _ = runs
    xs = [x for x in both.j.rows("exits", "variant IN ('flow300_H6', 'flow300_tp100_H6') AND tokens > 0")
          if x["exit_slot"] and x["exit_slot"] - x["entry_slot"] > 200]
    if not xs:
        pytest.skip("no arm position held long enough on the slice")
    x = max(xs, key=lambda r: r["exit_slot"] - r["entry_slot"])
    crash = (x["entry_slot"] + x["exit_slot"]) // 2
    with pytest.raises(RuntimeError, match="simulated crash"):
        _run(str(d / "restart"), clock, crash_slot=crash)
    again = _run(str(d / "restart"), clock)  # the same journal directory, from the slice's start (the live restart)
    assert _content(again.j) == _content(both.j)
    assert again.j.rows("heartbeat") == both.j.rows("heartbeat")
    for w in ("wallet.json", "wallet-h.json"):
        assert json.load(open(os.path.join(again.j.dir, w))) == json.load(open(os.path.join(both.j.dir, w)))


def test_the_journal_keeps_k3s_config_sha_and_the_groups_sha(tmp_path):
    if not os.path.exists(STORE):
        pytest.skip("dev store not built")
    here = os.path.dirname(engine.__file__)
    k3, h = os.path.join(here, "k3-paper-2026-10-12.yaml"), os.path.join(here, "h-arms-2026-10-16.yaml")
    engine.main(["--from", str(LO), "--to", str(LO + 2_000), "--journal", str(tmp_path / "j"), "--config", k3, "--groups", h,
                 "--store", STORE, "--no-backup"])
    j = Journal(str(tmp_path / "j"))
    assert j.state("config_sha256") == load_config(k3)[0] and j.state("groups_sha256") == load_groups(h)[0]
    assert j.state("risk_sha256_at_start") and j.state("risk_sha256_at_start:H")
    assert sorted(json.loads(j.state("groups"))) == ["H", "K3"]
    assert json.loads(j.state("feed"))["kind"] == "replay" and json.loads(j.state("cost_scenario"))["name"] == "base"
    engine.main(["--from", str(LO), "--to", str(LO + 2_000), "--journal", str(tmp_path / "k"), "--config", k3, "--store", STORE,
                 "--no-backup"])
    k = Journal(str(tmp_path / "k"))
    assert k.state("groups_sha256") is None and sorted(json.loads(k.state("groups"))) == ["K3"]
