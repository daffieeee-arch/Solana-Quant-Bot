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


def test_read_wallets_takes_the_first_field_of_a_tab_separated_pass_list(tmp_path):
    """E82: e81_passlist.txt is tab-separated (wallet, days, K, re, re_net); the wallet is the first field and a number in
    the second column is never a control flag (LAB must-fix on #609: the comma split gave 36 unusable keys)."""
    p = tmp_path / "e81_passlist.txt"
    rows = [f"{'W%02d' % i}xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\t{3 + i % 4}\t23.651\t38.374\t38.36" for i in range(36)]
    rows[5] = rows[5].replace("\t3\t", "\t1\t")  # days = 1 in the second column: still not a control
    p.write_text("\n".join(rows) + "\n")
    w = follow.read_wallets(str(p))
    assert len(w) == 36 and all(k.startswith("W") and "\t" not in k for k in w) and not any(w.values())


def test_run_refuses_an_empty_or_unmatched_wallet_list(tmp_path, monkeypatch):
    """A list that reads to nothing, or whose wallets have no episode in the range, is a refusal, not an empty run."""
    import json as _json

    import pyarrow as _pa
    import pyarrow.parquet as _pq

    ep_dir = tmp_path / "eps"
    ep_dir.mkdir()
    _pq.write_table(_pa.Table.from_pylist([{"wallet": "A", "key": "M", "venue": "curve", "orientation": "curve", "t0": 100, "pool": None,
                                           "day": "d", "leader_ret_realized": 0.0, "leader_ret_marked": 0.0, "hold_slots": None,
                                           "closed": False, "create_slot": False}]), ep_dir / "episodes-0.parquet")
    empty, other = tmp_path / "empty.txt", tmp_path / "other.txt"
    empty.write_text("wallet,control\n")
    other.write_text("B\t3\t1.0\t1.0\t1.0\n")

    class Con:
        def execute(self, *a):
            return self

        def fetchone(self):
            return (_json.dumps({"period": "dev"}),)

        def close(self):
            pass

    monkeypatch.setattr(follow.streams, "open_store", lambda *a, **k: Con())
    monkeypatch.setattr(follow.Clock, "from_store", classmethod(lambda cls, con: type("C", (), {"slots": staticmethod(lambda ms, lo: 10)})()))
    monkeypatch.setattr(follow, "guard", lambda hi: None)
    import pytest as _pt

    with _pt.raises(ValueError, match="no wallet read"):
        follow.run(str(ep_dir), 0, 1000, str(tmp_path / "o1"), [0.5], [3], 5, 0, store="s", wallets_file=str(empty))
    with _pt.raises(ValueError, match="none of the 1 listed wallets"):
        follow.run(str(ep_dir), 0, 1000, str(tmp_path / "o2"), [0.5], [3], 5, 0, store="s", wallets_file=str(other))


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


def test_check_q_rule_g1_and_cancel_t1_lookahead():
    """E88: d 0 only alone at q 1.0, d >= 1 only at q 0.5, --cancel-t1 only at d >= 2."""
    follow.check_q([0], 1.0)
    follow.check_q([1, 2, 3], 0.5)
    follow.check_q([2, 3], 0.5, cancel_t1=True)
    for delays, q, cancel in [([0], 0.5, False), ([0, 1], 1.0, False), ([1], 1.0, False), ([1, 2], 0.75, False),
                              ([1], 0.5, True), ([0], 1.0, True), ([-1], 0.5, False)]:
        with pytest.raises(ValueError):
            follow.check_q(delays, q, cancel)
    with pytest.raises(ValueError, match="look-ahead"):  # run() checks before it opens anything
        follow.run("no-such-dir", 0, 1000, "no-out", [0.5], [0], 5, 0, store="no-store")
    with pytest.raises(ValueError, match="look-ahead"):
        follow.run("no-such-dir", 0, 1000, "no-out", [0.5], [1], 5, 0, store="no-store", cancel_t1=True)


def test_run_group_passes_q_to_the_replay():
    st = Stream("M", 0, "c", [cleg(10, "w", True, 100), cleg(40, "w", True, 100)], [])
    ep = {"wallet": "w", "key": "M", "venue": "curve", "orientation": "curve", "t0": 10, "day": "d", "leader_ret_realized": 0.0,
          "leader_ret_marked": 0.0, "hold_slots": None, "closed": False, "create_slot": False, "pool": None}
    seen = []

    def fake_sim(st, t0, size, d, q, rule, tol, tau, max_slots):
        seen.append((d, q))
        from backtest.replay import Result
        return Result("M", t0, size=size, venue="curve", entry_slot=t0 + d, exit_slot=t0 + d + 5, exit_reason="time")

    follow.run_group(st, [ep], "curve", fake_sim, {"w": False}, [0.5], [0], 1000, None, [], q=1.0)
    follow.run_group(st, [ep], "curve", fake_sim, {"w": False}, [0.5], [3], 1000, None, [])
    assert sorted(set(seen)) == [(0, 1.0), (3, follow.Q_FRAC)]


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
