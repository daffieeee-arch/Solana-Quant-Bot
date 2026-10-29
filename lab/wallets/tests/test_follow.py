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


def test_pack_pools_heaviest_first_and_alone_above_cap():
    from wallets.follow import pack_pools

    w = {"A": 500_000, "B": 120_000, "C": 40_000, "D": 30_000, "E": 10, "F": 0}
    assert pack_pools(["F", "E", "D", "C", "B", "A"], w, cap=150_000, max_pools=25) == [["A"], ["B"], ["C", "D", "E", "F"]]
    assert pack_pools(["C", "D", "E", "F"], w, cap=150_000, max_pools=2) == [["C", "D"], ["E", "F"]]
    assert pack_pools([], w) == []


def test_follow_exit_observes_only_after_start():
    """E82 pre-reg §3: the exit observation u begins after our fill (slots > t0 + d)."""
    st = Stream("M", 0, "c", [cleg(10, "w", True, 100), cleg(12, "w", False, 90)], [])
    rule = follow.FollowExit(follow.LeaderPath(st, "w"), t0=10, exit_share=0.5, max_slots=1000, start=13)
    ctx = lambda slot: {"slot": slot, "slots_held": slot - 13}  # noqa: E731
    assert rule(ctx(12)) is None and rule(ctx(13)) is None  # the leader is out at 12, but we only look from 14
    assert rule(ctx(14)) == "leader_sold"
    assert follow.FollowExit(follow.LeaderPath(st, "w"), 10, 0.5, 1000)(ctx(12)) == "leader_sold"  # K1 behaviour


def test_cancel_at_t1_uses_the_state_at_the_end_of_t0_plus_1_only():
    """E82 pre-reg §3: cancel on the leader already out, or the curve complete, by the end of t0 + 1; a sale in t0 + 2
    or later never cancels (the fill at t0 + d is the replay's business)."""
    out_at_11 = Stream("M", 0, "c", [cleg(10, "w", True, 100), cleg(11, "w", False, 60)], [])
    out_at_12 = Stream("M", 0, "c", [cleg(10, "w", True, 100), cleg(12, "w", False, 60)], [])
    half_at_11 = Stream("M", 0, "c", [cleg(10, "w", True, 100), cleg(11, "w", False, 40)], [])
    assert follow.cancel_at_t1(out_at_11, follow.LeaderPath(out_at_11, "w"), 10, 0.5) == "cancelled_t1_leader_sold"
    assert follow.cancel_at_t1(out_at_12, follow.LeaderPath(out_at_12, "w"), 10, 0.5) is None
    assert follow.cancel_at_t1(half_at_11, follow.LeaderPath(half_at_11, "w"), 10, 0.5) is None  # 60 left > 50
    done = Stream("M", 0, "c", [cleg(10, "w", True, 100)], [], complete_slot=11)
    assert follow.cancel_at_t1(done, follow.LeaderPath(done, "w"), 10, 0.5) == "cancelled_t1_complete"
    later = Stream("M", 0, "c", [cleg(10, "w", True, 100)], [], complete_slot=12)
    assert follow.cancel_at_t1(later, follow.LeaderPath(later, "w"), 10, 0.5) is None


def test_run_group_cancel_t1_skips_the_replay_and_records_the_reason():
    st = Stream("M", 0, "c", [cleg(10, "w", True, 100), cleg(11, "w", False, 60), cleg(40, "w", True, 100)], [])
    ep = lambda t0: {"wallet": "w", "key": "M", "venue": "curve", "orientation": "curve", "t0": t0, "day": "d", "leader_ret_realized": 0.0,  # noqa: E731
                     "leader_ret_marked": 0.0, "hold_slots": None, "closed": False, "create_slot": False, "pool": None}
    calls = []

    def fake_sim(st, t0, size, d, q, rule, tol, tau, max_slots):
        calls.append((t0, rule.start))
        from backtest.replay import Result
        return Result("M", t0, size=size, venue="curve", entry_slot=t0 + d, exit_slot=t0 + d + 5, exit_reason="time")

    rows = []
    follow.run_group(st, [ep(10), ep(40)], "curve", fake_sim, {"w": False}, [0.5], [3], 1000, None, rows, cancel_t1=True)
    by_t0 = {(r["t0"], r["variant"]): r for r in rows}
    assert by_t0[(10, "W_half")]["skipped"] and by_t0[(10, "W_half")]["exit_reason"] == "cancelled_t1_leader_sold"
    assert by_t0[(10, "W_full")]["exit_reason"] is None or by_t0[(10, "W_full")]["skipped"] is False  # 60 left > 10 %: W_full is sent
    assert not by_t0[(40, "W_half")]["skipped"] and by_t0[(40, "W_half")]["exit_reason"] == "time"
    assert calls and all(start == t0 + 3 for t0, start in calls)  # the exit rule observes only after the fill


def test_one_position_per_key_keeps_the_earliest_and_skips_overlaps():
    def row(key, t0, wallet, exit_slot, skipped=False, failed=False, variant="W_half"):
        return {"key": key, "t0": t0, "wallet": wallet, "size_sol": 0.5, "d": 3, "variant": variant, "exit_slot": exit_slot,
                "skipped": skipped, "entry_failed": failed}
    rows = [row("M", 10, "a", 50), row("M", 30, "b", 70), row("M", 51, "c", 60), row("M", 55, "d", 90, skipped=True), row("M", 61, "e", None),
            row("M", 99, "f", 120), row("N", 10, "a", 20), row("M", 30, "b", 70, variant="W_full")]
    out = follow.one_position_per_key(rows)
    flags = [(r["key"], r["t0"], r["variant"], r["overlap_skipped"]) for r in out]
    assert flags == [("M", 10, "W_half", False), ("M", 30, "W_half", True), ("M", 51, "W_half", False), ("M", 55, "W_half", True),
                     ("M", 61, "W_half", False), ("M", 99, "W_half", True), ("N", 10, "W_half", False), ("M", 30, "W_full", False)]


def test_range_guard_routes_forward_stores_only(monkeypatch):
    import pytest as _pt

    meta = {"period": "forward", "slot_start": 459_648_000, "slot_end_exclusive": 461_808_000}
    follow.range_guard(459_700_000, 461_000_000, True, meta)
    with _pt.raises(ValueError):
        follow.range_guard(459_700_000, 462_000_000, True, meta)  # past the store's end
    with _pt.raises(ValueError):
        follow.range_guard(459_700_000, 461_000_000, True, {**meta, "period": "dev"})
    with _pt.raises(ValueError):
        follow.range_guard(459_700_000, 461_000_000, False)  # the dev guard refuses a slot past the hold-out start
