"""The paper arms beside K3 (research/week4-spec-paper-h4h5-2026-10-16.md): the live flow index against the
backtest's, the variant groups and their config, the exit windows under the Alpenglow clock fallback, and the
daily report (K3's stop rule counts K3 only; the arms get a measurement section)."""

import os
from types import SimpleNamespace as NS

import numpy as np
import pytest
import yaml

from backtest.strategies import EPOCH_SLOTS, F7_BIG_GRID, F7_LONG_GRID, H6_MS, Clock, FlowIndex, f7_exit_rule
from paper import engine
from paper.engine import EXIT_VARIANTS, Engine, Group, LiveFlowIndex, _windows, config_groups, load_config, load_groups, variant
from paper.journal import Journal
from paper.report import daily_report

SOL = 1_000_000_000


def _legs(n, seed=1):
    rng = np.random.default_rng(seed)
    slots = np.sort(rng.integers(1_000, 1_000 + 3 * n, size=n))
    return [NS(kind=str(rng.choice(["buy", "sell", "boost"])), slot=int(s), order=(int(s), i, 0, 0), farmer=bool(rng.random() < 0.3),
               base=int(rng.integers(1, 50)) * SOL, pre=NS(b=500 * SOL)) for i, s in enumerate(slots)]


def test_the_live_flow_index_is_the_backtests():
    legs = _legs(2_000)
    full = FlowIndex(NS(pool_legs=legs, orientation="reversed"))
    live = LiveFlowIndex("reversed")
    for s in sorted({l.slot for l in legs}):  # fed slot by slot, queried at growing decision slots
        live.add([l for l in legs if l.slot == s])
        live.add([l for l in legs if l.slot == s])  # the same legs again count once
        assert live.flow(s - 120, s) == full.flow(s - 120, s)
    assert len(live.slots) < len([l for l in legs if l.kind != "boost"])  # what no query reaches is dropped


def test_a_late_leg_takes_its_place():
    legs = _legs(60, seed=2)
    late = legs.pop(30)
    live = LiveFlowIndex("reversed")
    live.add(legs)
    live.add([late])
    full = FlowIndex(NS(pool_legs=sorted(legs + [late], key=lambda l: l.order), orientation="reversed"))
    for lo in range(1_000, 1_200, 7):
        assert live.flow(lo, lo + 40) == full.flow(lo, lo + 40)


def test_variants_and_groups():
    assert variant("0.04:600") == ("tp4_T600", (0.04, 600)) == variant((0.04, 600))
    assert variant("flow300_H6") == ("flow300_H6", ("flow", 300, None, H6_MS)) and "flow300_tp100_H6" in EXIT_VARIANTS
    assert {f"{n}" for n in EXIT_VARIANTS} >= {"tp50_H6", "trail20_H6", "hold180", "flow60_T600"}
    with pytest.raises(ValueError):
        variant("flow999_H6")
    g = Group("H", ["flow300_H6", "flow300_tp100_H6"], farm="organic", segments=["pool_reversed"], d_ms=600, n2_k=0)
    assert [n for n, _ in g.variants] == ["flow300_H6", "flow300_tp100_H6"] and g.segments == {"pool_reversed"}
    with pytest.raises(ValueError, match="pool exits"):
        Group("H", ["flow300_H6"], segments=None)
    feed = NS(meta_bounds=(0, 10**9))
    with pytest.raises(ValueError, match="distinct"):
        Engine(feed, Journal("/tmp/never-used-arms"), None, variants=["0.04:600"], clock=Clock(), backup=False,
               groups=[Group("H", ["0.04:600"], segments=["pool_reversed"])])


K3_CONFIG_SHA = "04b5e652eb0301ae768745adeb7127c5c69be3af889e209715ff3122d3e81f64"  # E14's config as on main: the arms never change it


def test_k3s_config_is_untouched_and_group_h_is_a_file_of_its_own(tmp_path):
    here = os.path.dirname(engine.__file__)
    sha, conf = load_config(os.path.join(here, "k3-paper-2026-10-12.yaml"))
    assert sha == K3_CONFIG_SHA and "groups" not in conf
    gsha, gconf = load_groups(os.path.join(here, "h-arms-2026-10-16.yaml"))
    (h,) = config_groups(gconf)
    assert len(gsha) == 64 and (h.name, [n for n, _ in h.variants], h.farm, h.segments, h.d_ms, h.q, h.n2_k, h.wallet_file,
                                h.require_farm_complete) == (
        "H", ["flow300_H6", "flow300_tp100_H6"], "organic", {"pool_reversed"}, 600, 0.5, 0, "wallet-h.json", True)
    from paper import risk

    r, why = risk.load(h.risk_path)
    assert why is None and (r.size_sol, r.max_open_positions, r.max_exposure_sol, r.daily_loss_limit_sol, r.pool_cooldown_h) == (
        0.5, 5, 2.5, 1.0, 24.0)
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump({"groups": {"H": dict(gconf["groups"]["H"], wallet="wallet.json")}}))
    with pytest.raises(SystemExit, match="a wallet file of its own"):
        load_groups(str(bad))
    k3 = yaml.safe_load(open(os.path.join(here, "k3-paper-2026-10-12.yaml")))
    (tmp_path / "risk.yaml").write_text("x: 1\n")
    mixed = tmp_path / "mixed.yaml"
    mixed.write_text(yaml.safe_dump(dict(k3, groups=gconf["groups"])))
    with pytest.raises(SystemExit, match="a file of their own"):
        load_config(str(mixed))


def test_without_groups_the_engine_has_its_one_group(tmp_path):
    eng = Engine(NS(meta_bounds=(0, 10**9)), Journal(str(tmp_path / "j")), None, variants=["0.04:600"], clock=Clock(), backup=False)
    assert eng.groups == [eng.primary] and eng.primary.name == "K3" and eng.variants == [(0.04, 600)]
    assert engine.groups_state(eng) == ('{"K3": {"d": 1, "d_ms": null, "farm": "all", "n2_k": 1, "primary": true, "q": 0.5, '
                                        '"require_farm_complete": false, "risk": null, "segments": null, "variants": ["tp4_T600"], '
                                        '"wallet": "wallet.json"}}')


def test_the_exit_windows_follow_the_clock_and_its_fallback():
    """Alpenglow (epoch 1064): an epoch not measured yet takes the last measured one (readiness 3a); the
    windows go into the exits row."""
    t = 1064 * EPOCH_SLOTS + 10
    for params in (F7_LONG_GRID[4], F7_BIG_GRID[2]):
        w = _windows(f7_exit_rule(params, NS(), t, Clock({1063: 400.0}), None))
        assert w == {"n_flow_slots": 750, "cap_slots": round(H6_MS / 400.0)}
        w = _windows(f7_exit_rule(params, NS(), t, Clock({1063: 400.0, 1064: 200.0}), None))
        assert w == {"n_flow_slots": 1_500, "cap_slots": round(H6_MS / 200.0)}
    assert _windows(object()) == {"n_flow_slots": None, "cap_slots": None}


def test_the_report_counts_k3_for_the_stop_rule_and_measures_the_arms(tmp_path):
    j = Journal(str(tmp_path / "j"))
    import json

    j.state("groups", json.dumps({"K3": {"primary": True, "variants": ["tp4_T600"]},
                                  "H": {"primary": False, "variants": ["flow300_H6", "flow300_tp100_H6"]}}))
    base = dict(family="F7", key="P", day="2026-10-21", entry_slot=10, exit_decision_slot=20, exit_slot=22, exit_attempts=1,
                cf_graduation=0, seed_pool=0, vault_capped=0, reverted_direct=0, reverted_router=0, sells_dropped=0, sells_scaled=0)
    j.put("exits", **base, variant="tp4_T600", t=1, exit_reason="take_profit", tokens=5, cost=SOL // 2, proceeds=SOL, pnl=SOL // 10, ret=0.1)
    j.put("exits", **base, variant="flow300_tp100_H6", t=1, exit_reason="take_profit", tokens=5, cost=SOL // 2, proceeds=SOL,
          pnl=SOL // 2, ret=1.0)
    j.put("exits", **base, variant="flow300_H6", t=1, exit_reason="outflow", tokens=5, cost=SOL // 2, proceeds=0, pnl=-SOL // 2,
          ret=-1.0)
    for v in ("tp4_T600", "flow300_H6", "flow300_tp100_H6"):
        j.put("orders", family="F7", variant=v, key="P", t=1, day="2026-10-21", entry_slot=10, size=SOL // 2, status="filled")
    j.put("heartbeat", minute=1, day="2026-10-21", last_slot=22, events=3, open_positions=0, gap_slots=0, note="")
    daily_report(j, "2026-10-21")
    md = open(os.path.join(j.dir, "2026-10-21", "report.md")).read()
    assert "## Stop-rule counter (all paper days so far; tp4_T600 only)" in md
    assert "- trades 1 (failed entries 0, their cost included), days 1, net +0.1000 SOL/trade" in md  # K3 only
    assert "## Group H: measurement, no test" in md
    assert "| flow300_tp100_H6 | 1 | 0 | +0.5000 / +0.5000 | +0.500 | +0.500 | 100% | 100% | 0% | take_profit 1 | 2.00 / 2.00 / 2.00 | 12 |" in md
    assert "| flow300_H6 | 1 | 0 | -0.5000 / -0.5000 | -0.500 | -0.500 | 0% | 100% | 100% | outflow 1 | – | 12 |" in md
    assert "- flow300_H6: 1 days with a position (the day interval from 5 on)" in md
