"""Follower exit: the leader's holdings path from historical legs, and the exit only sees slots <= u."""

import os
import sys

import pytest

E = os.environ.get("LAB_ENGINE")
if E:  # another test may have imported the repo's backtest already: re-import from the engine
    for m in [m for m in sys.modules if m == "backtest" or m.startswith("backtest.")]:
        del sys.modules[m]
    sys.path.insert(0, E)

follow = pytest.importorskip("wallets.follow")
from backtest.replay import CurveLeg, PoolLeg, Stream  # noqa: E402

if "orientation" not in Stream.__dataclass_fields__:
    pytest.skip("needs the store-v2 engine (LAB_ENGINE)", allow_module_level=True)


def cleg(slot, trader, is_buy, t):
    return CurveLeg(slot, slot, (slot, slot, 0, 0), is_buy, t, 10**9, 0, "buy", None, None, trader)


def pleg(slot, trader, kind, base, quote):
    return PoolLeg(slot, slot, (slot, slot, 0, 0), kind, False, base, quote, None, None, trader)


def test_leader_path_curve_and_normal_pool():
    st = Stream("M", 0, "c", [cleg(10, "w", True, 100), cleg(10, "w", True, 50), cleg(12, "x", True, 999),
                              cleg(20, "w", False, 60)],
                [pleg(30, "w", "sell", 70, 5), pleg(40, "w", "buy", 10, 1)], orientation="normal")
    p = follow.LeaderPath(st, "w")
    assert [p.holdings(s) for s in (9, 10, 11, 20, 29, 30, 40)] == [0, 150, 150, 90, 90, 20, 30]


def test_leader_path_reversed_pool_uses_quote_side():
    st = Stream("M", 0, "c", [], [pleg(5, "w", "sell", 10**9, 1000), pleg(6, "w", "buy", 10**9, 400)],
                orientation="reversed")
    p = follow.LeaderPath(st, "w")
    assert (p.holdings(5), p.holdings(6)) == (1000, 600)


def test_follow_exit_rule():
    st = Stream("M", 0, "c", [cleg(10, "w", True, 100), cleg(15, "w", False, 40), cleg(16, "w", False, 20)], [])
    rule = follow.FollowExit(follow.LeaderPath(st, "w"), t0=10, exit_share=0.5, max_slots=1000)
    ctx = lambda slot: {"slot": slot, "slots_held": slot - 11}  # noqa: E731
    assert rule(ctx(11)) is None and rule(ctx(15)) is None  # 60 left > 50
    assert rule(ctx(16)) == "leader_sold"  # 40 left <= 50
    assert rule({"slot": 12, "slots_held": 1000}) == "time"
    assert rule.deadline(11) == 1011
    # No reference holdings (the trigger is not in the stream): never a leader exit.
    assert follow.FollowExit(follow.LeaderPath(st, "nobody"), 10, 0.5, 1000)(ctx(16)) is None


def test_read_wallets(tmp_path):
    p = tmp_path / "w.csv"
    p.write_text("wallet,control\nA,false\nB,1\nC\n")
    assert follow.read_wallets(str(p)) == {"A": False, "B": True, "C": False}


def test_candidates_min_n_and_deterministic_control():
    eps = [{"wallet": w} for w in "aaaaa" + "bbb" + "cc" + "d"]
    c = follow.candidates(eps, min_n=3, n_control=1, lo=1)
    assert c == {"a": False, "b": False, "c": True}  # d has 1 episode: never a control
    assert follow.candidates(eps, 3, 1, 1) == c
