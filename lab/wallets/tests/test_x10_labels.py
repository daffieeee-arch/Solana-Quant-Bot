"""x10 stage A on the runners mini store: the four moments, the labels (target before the stop, within the horizon), the
delayed exits (the sell at the first mark >= trigger slot + d), the per-mint peak and early-buyer tables and the extra
features, all recomputed in Python from the same engine states the runners test uses."""
import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import json
import pytest

from backtest import venues as V
from wallets import runners
from wallets.tests.test_runners import LO, M, MS, S, SIZE, T0, TX, _curve_rows, _pool_trades, _store
from wallets.x10 import labels as X


def _expected(marks, cost, d, h_slots, entry_slot):
    """The labels and delayed exits from a list of (slot, net) marks, the way the plan states them."""
    net = [m for _, m in marks]
    slots = [s for s, _ in marks]
    cap = entry_slot + h_slots
    within = [i for i in range(len(marks)) if slots[i] <= cap]
    first = lambda cond: next((i for i in within if cond(i)), None)  # noqa: E731
    stop = first(lambda i: net[i] <= 0.5 * cost)
    tgt = {m: first(lambda i, m=m: net[i] >= m * cost) for m in (2, 5, 10)}
    pmax = [max(net[:i]) if i else None for i in range(len(net))]
    trail = first(lambda i: i > 0 and net[i] <= 0.7 * pmax[i])
    end_net = net[within[-1]]
    before_stop = lambda i: i is not None and (stop is None or i < stop)  # noqa: E731
    hit = {m: before_stop(tgt[m]) for m in (2, 5, 10)}

    def fill(trig_i):
        if trig_i is None:
            return end_net
        j = next((i for i in within if slots[i] >= slots[trig_i] + d), None)
        return net[j] if j is not None else end_net

    def rule(own):
        return fill(own if before_stop(own) else stop)

    def half(m):
        if before_stop(tgt[m]):
            tr = first(lambda i: i > tgt[m] and net[i] <= 0.7 * pmax[i])
            return 0.5 * fill(tgt[m]) + 0.5 * rule(tr)
        return rule(trail)
    return {"hit": hit, "stop_first": before_stop(stop) and (tgt[2] is None or stop < tgt[2]), "peak": max(net[i] for i in within) / cost,
            "end": end_net / cost, "x": {"trail30": rule(trail) / cost, "tp2": rule(tgt[2]) / cost, "tp5": rule(tgt[5]) / cost,
                                          "tp10": rule(tgt[10]) / cost, "half2": half(2) / cost, "half5": half(5) / cost}}


def test_moments_labels_exits_and_mint_tables(tmp_path, monkeypatch):
    trades = [(S, True, 2 * 10**9, True), (S + 300, True, 3 * 10**9, False), (S + 400, True, 10**9, True), (S + 450, False, 10**12, False),
              (S + 900, True, 25 * 10**9, True), (S + 1500, False, 5 * 10**12, True), (S + 3000, True, 200 * 10**9, True)]
    rows, states = _curve_rows(trades)
    final = states[-1][1]
    p0 = V.Pool(206_900_000_000_000, final.rq - 6 * 10**9, 20, 5, 5, 0)
    prow = [{"kind": "deposit", "slot": S + 3100, "b": 0, "e": 0, "vault": 0, "base": p0.b, "e_delta": None, "quote_user": p0.e}]
    ptrades, pstates = _pool_trades(p0, [(S + 3200, True, 3 * 10**9), (S + 3300, False, 2 * 10**12), (S + 3400, True, 20 * 10**9), (S + 9000, False, 40 * 10**12)])
    _store(tmp_path / "s.duckdb", rows, [{"pool": "P1", "mint": M, "create_slot": S + 3100, "rows": prow + ptrades}])
    con = duckdb.connect(str(tmp_path / "s.duckdb"))
    con.execute("ALTER TABLE mints ADD COLUMN create_tx UBIGINT")
    con.execute("UPDATE mints SET create_tx = 1")   # the fixture's curve rows carry tx_index 1: the create-slot buy is 'in the create transaction'
    con.close()
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path / "spill"))
    out = tmp_path / "out"
    info = X.run(LO, LO + 5_000, str(out), store=str(tmp_path / "s.duckdb"), cfg="/nonexistent.yaml")
    assert info["tx_cost_lamports"] == TX and info["size_sol"] == 0.5
    df = duckdb.connect().execute(f"SELECT * FROM '{out}/labels-{LO}.parquet' ORDER BY moment").arrow().read_all().to_pylist()
    by = {r["moment"]: r for r in df}
    # e10: the first trade >= S + 10 is S + 300; c30: block time >= T0 + 30 s = slot S + 75 -> S + 300; c2: >= T0 + 120 s = S + 300 exactly
    assert set(by) == {"e10", "c30", "c2", "mig"} and all(r["key"] == M for r in df)
    assert by["e10"]["t"] == by["c30"]["t"] == by["c2"]["t"] == S + 300 and by["e10"]["entry_slot"] == S + 400 and by["e10"]["d_slots"] == 2
    # mig: the pool's first trade is S + 3200; the entry is the first trade >= S + 3202 = the sell at S + 3300
    assert by["mig"]["t"] == S + 3200 and by["mig"]["entry_slot"] == S + 3300 and by["mig"]["entry_venue"] == "pool"
    # the marks of the S + 400 entry, as the runners test: curve post-states from S + 400 on, then the pool states
    pre = states[1][1]
    fill = V.curve_buy_exact_in(pre, SIZE)
    marks = []
    for slot, c in states[2:]:
        if c.complete:
            continue
        marks.append((slot, V.curve_sell(c, fill.tokens).trader_quote - TX))
    for slot, p in [(S + 3100, p0)] + pstates:
        f = V.pool_sell_capped(p, fill.tokens)
        marks.append((slot, (f.trader_quote if f else 0) - TX))
    cost = SIZE + TX
    r = by["e10"]
    assert r["n_marks"] == len(marks) and r["n_marks_24"] == r["n_marks_72"] == len(marks)   # 24 h = 216,000 slots: everything is inside
    for H, h_slots in (("24", round(24 * 3_600_000 / MS)), ("72", round(72 * 3_600_000 / MS))):
        e = _expected(marks, cost, 2, h_slots, S + 400)
        for m in (2, 5, 10):
            assert r[f"hit_{m}_{H}"] == e["hit"][m], (m, H)
        assert r[f"stop_first_{H}"] == e["stop_first"]
        assert abs(r[f"peak_{H}"] - e["peak"]) < 1e-9 and abs(r[f"end_{H}"] - e["end"]) < 1e-9
        for rule, x in e["x"].items():
            assert abs(r[f"x_{rule}_{H}"] - x) < 1e-9, (rule, H)
        # the half exits explicitly (LAB nit on #560): on this path x2 is hit, so half2 = half at the target + half on the trail after it
        assert abs(r[f"x_half2_{H}"] - e["x"]["half2"]) < 1e-9 and abs(r[f"x_half5_{H}"] - e["x"]["half5"]) < 1e-9, H
    # the 25 SOL buy makes x2 happen; a tp2 with the 2-slot delay sells at a later mark than the undelayed runners x_tp2 when the trigger
    # mark is not the last one of its slot group
    assert r["hit_2_24"] and r["x_tp2_24"] > 0 and r["trig_tp2_24"]
    net = [m for _, m in marks]
    i2 = next(i for i in range(len(net)) if net[i] >= 2 * cost)
    j = next(i for i in range(len(net)) if marks[i][0] >= marks[i2][0] + 2)
    assert abs(r["x_tp2_24"] - net[j] / cost) < 1e-9 and j > i2
    # extra features of the S + 300 moment: the first 10 s after the create (slots < S + 25) hold only the create buy (W0, 2 SOL)
    assert r["f10_buyers"] == 1 and abs(r["f10_buy_sol"] - rows[0][3] / 1e9) < 1e-9 and r["f10_big_share"] == 1.0 and r["f10_sell_ratio"] is None
    assert r["f60_buyers"] == 1 and r["cs_buys"] == 1 and r["cs_tx_buys"] == 1 and r["cs_2slot_buyers"] == 1
    assert r["creator_sells"] == 0 and r["creator_hold_share"] == 0.0 and r["hour_utc"] is not None and r["weekday"] in range(1, 8)
    # mint tables: p0 = the price after the create buy, the peak over curve post-states and pool states within 72 h; early buyers = W0
    mp = duckdb.connect().execute(f"SELECT * FROM '{out}/mint_peaks-{LO}.parquet'").arrow().read_all().to_pylist()
    assert len(mp) == 1 and mp[0]["mint"] == M
    priced = [(s, c.vq / c.vt) for s, c in states] + [(s, p.e / p.b) for s, p in pstates]
    prices = [p for _, p in priced]
    assert abs(mp[0]["p0"] - prices[0]) < 1e-15 and abs(mp[0]["peak_mult"] - max(prices) / prices[0]) < 1e-9
    for m, col in ((2, "first_x2_slot"), (5, "first_x5_slot"), (10, "first_x10_slot")):
        assert mp[0][col] == next((s for s, p in priced if p >= m * prices[0]), None), col
    assert mp[0]["first_x2_slot"] is not None and mp[0]["n_trades_72"] == len(priced)
    eb = duckdb.connect().execute(f"SELECT * FROM '{out}/early_buyers-{LO}.parquet'").arrow().read_all().to_pylist()
    assert [(e["wallet"], e["first_buy_slot"]) for e in eb] == [("W0", S)]


def test_half_exits_trail_the_whole_position_when_the_target_never_comes(tmp_path, monkeypatch):
    """Codex finding #4 (2026-10-24), the docstring's contract: half2 / half5 sell half at the target and trail the rest; when
    the target never comes the WHOLE position trails. Path: the mark rises to 1.23x, a large sell drops it to 0.86x (<= 70 % of
    the peak: the trail fires at S + 900, no -50 % stop, no x2), then a buy lifts the end mark again. Both halves must fill at
    the trail's delayed mark (S + 1500), so x_half2 = x_half5 = x_trail30 and none of them is the end mark; the old code let
    the target half wait for the end (0.5 x trail + 0.5 x end)."""
    trades = [(S, True, 2 * 10**9, True), (S + 300, True, 3 * 10**9, False), (S + 400, True, 10**9, True), (S + 500, True, 4 * 10**9, True),
              (S + 900, False, 16 * 10**13, True), (S + 1500, True, 5 * 10**8, True), (S + 2000, True, 3 * 10**9, True)]
    rows, states = _curve_rows(trades)
    _store(tmp_path / "s.duckdb", rows, [])
    con = duckdb.connect(str(tmp_path / "s.duckdb"))
    con.execute("ALTER TABLE mints ADD COLUMN create_tx UBIGINT")
    con.execute("UPDATE mints SET create_tx = 1")
    con.close()
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path / "spill"))
    out = tmp_path / "out"
    X.run(LO, LO + 5_000, str(out), store=str(tmp_path / "s.duckdb"), cfg="/nonexistent.yaml")
    r = {x["moment"]: x for x in duckdb.connect().execute(f"SELECT * FROM '{out}/labels-{LO}.parquet'").arrow().read_all().to_pylist()}["e10"]
    assert r["entry_slot"] == S + 400 and r["d_slots"] == 2
    pre = states[1][1]
    fill = V.curve_buy_exact_in(pre, SIZE)
    marks = [(slot, V.curve_sell(c, fill.tokens).trader_quote - TX) for slot, c in states[2:] if not c.complete]
    cost = SIZE + TX
    net = [m for _, m in marks]
    assert max(net) < 2 * cost and min(net) > 0.5 * cost, "fixture: no target and no stop on this path"
    e = _expected(marks, cost, 2, round(24 * 3_600_000 / MS), S + 400)
    trail_fill = net[next(i for i in range(len(net)) if marks[i][0] >= S + 900 + 2)] / cost
    assert abs(e["x"]["trail30"] - trail_fill) < 1e-9 and abs(e["end"] - trail_fill) > 0.05, "fixture: the trail fills at S + 1500, the end mark lies elsewhere"
    for H in ("24", "72"):
        assert not r[f"hit_2_{H}"] and not r[f"stop_first_{H}"]
        assert abs(r[f"x_trail30_{H}"] - trail_fill) < 1e-9
        assert abs(r[f"x_half2_{H}"] - trail_fill) < 1e-9 and abs(r[f"x_half5_{H}"] - trail_fill) < 1e-9, H   # the whole position trails
        assert abs(r[f"x_half2_{H}"] - 0.5 * (trail_fill + r[f"end_{H}"])) > 0.02, "the old half-at-the-end value must not come back"


def test_half_exits_at_the_stop_count_both_halves(tmp_path, monkeypatch):
    """The leg collapse found with Codex finding #4 (#560): when both halves trigger at the same slot they must stay two rows of
    0.5 in the fill. Path: a 20 SOL buy before the entry leaves enough real reserve that a crash at S + 900 puts the mark under
    50 % of the cost (the stop; no x2 before it) without draining the curve (the harness caps marks at the real reserve plus
    our deposit, so a drained curve would not show the stop); the delayed fill is the S + 1500 mark and the end mark lies
    higher. x_half2 / x_half5 must equal the stop's fill (= x_trail30, which triggers on the same slot), not half of it."""
    trades = [(S, True, 2 * 10**9, True), (S + 300, True, 20 * 10**9, False), (S + 400, True, 10**9, True), (S + 500, True, 10**9, True),
              (S + 900, False, 3 * 10**14, True), (S + 1500, True, 10**9, True), (S + 2000, True, 3 * 10**9, True)]
    rows, states = _curve_rows(trades)
    _store(tmp_path / "s.duckdb", rows, [])
    con = duckdb.connect(str(tmp_path / "s.duckdb"))
    con.execute("ALTER TABLE mints ADD COLUMN create_tx UBIGINT")
    con.execute("UPDATE mints SET create_tx = 1")
    con.close()
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path / "spill"))
    out = tmp_path / "out"
    X.run(LO, LO + 5_000, str(out), store=str(tmp_path / "s.duckdb"), cfg="/nonexistent.yaml")
    r = {x["moment"]: x for x in duckdb.connect().execute(f"SELECT * FROM '{out}/labels-{LO}.parquet'").arrow().read_all().to_pylist()}["e10"]
    pre = states[1][1]
    fill = V.curve_buy_exact_in(pre, SIZE)
    marks = [(slot, V.curve_sell(c, fill.tokens).trader_quote - TX) for slot, c in states[2:] if not c.complete]
    cost = SIZE + TX
    net = [m for _, m in marks]
    i_stop = next(i for i in range(len(net)) if net[i] <= 0.5 * cost)
    assert max(net[:i_stop]) < 2 * cost, "fixture: no x2 before the stop"
    assert states[2 + i_stop][1].rq > SIZE, "fixture: the real reserve at the stop must exceed our deposit (else the cap, not the price, sets the mark)"
    stop_fill = net[next(i for i in range(len(net)) if marks[i][0] >= marks[i_stop][0] + 2)] / cost
    e = _expected(marks, cost, 2, round(24 * 3_600_000 / MS), S + 400)
    assert abs(e["x"]["half2"] - stop_fill) < 1e-9 and abs(e["end"] - stop_fill) > 0.05
    for H in ("24", "72"):
        assert r[f"stop_first_{H}"] and not r[f"hit_2_{H}"]
        assert abs(r[f"x_trail30_{H}"] - stop_fill) < 1e-9
        assert abs(r[f"x_half2_{H}"] - stop_fill) < 1e-9 and abs(r[f"x_half5_{H}"] - stop_fill) < 1e-9, H
        assert abs(r[f"x_half2_{H}"] - 0.5 * stop_fill) > 0.1, "the collapsed half-of-the-stop value must not come back"


def test_size_2_sol_run_writes_its_own_file(tmp_path, monkeypatch):
    trades = [(S, True, 2 * 10**9, True), (S + 300, True, 3 * 10**9, False), (S + 400, True, 10**9, True), (S + 900, True, 25 * 10**9, True),
              (S + 1500, False, 5 * 10**12, True)]
    rows, states = _curve_rows(trades)
    _store(tmp_path / "s.duckdb", rows, [])
    con = duckdb.connect(str(tmp_path / "s.duckdb"))
    con.execute("ALTER TABLE mints ADD COLUMN create_tx UBIGINT")
    con.close()
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path / "spill"))
    out = tmp_path / "out"
    X.run(LO, LO + 5_000, str(out), size_sol=2, store=str(tmp_path / "s.duckdb"), cfg="/nonexistent.yaml")
    r = duckdb.connect().execute(f"SELECT * FROM '{out}/labels-{LO}-s2.parquet' WHERE moment = 'e10'").arrow().read_all().to_pylist()[0]
    fill = V.curve_buy_exact_in(states[1][1], 2 * 10**9)
    assert abs(r["tokens"] - fill.tokens) <= 2 and abs(r["cost_sol"] - (2 * 10**9 + TX) / 1e9) < 1e-9
    assert not (out / f"mint_peaks-{LO}.parquet").exists()
    assert runners.SIZE == 5 * 10**8 and runners.H6_MS == 21_600_000   # the runners globals are restored after the run


def test_ab_labels_counts(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from wallets.x10 import ab_labels as AB
    base = {"key": ["A", "B", "C"], "moment": ["e10"] * 3, "t": [1, 2, 3], "tokens": [10, 10, 10], "cost_sol": [0.5] * 3, "depth_sol": [0.1, 2.0, 0.3],
            "hit_2_24": [False, True, False], "hit_5_24": [False] * 3, "hit_10_24": [False] * 3, "hit_2_72": [False, True, False], "hit_5_72": [False] * 3,
            "hit_10_72": [False] * 3, "stop_first_24": [True, False, True], "stop_first_72": [True, False, True],
            "x_tp2_24": [0.2, 2.0, 0.3], "x_trail30_24": [0.2, 1.5, 0.3], "x_tp5_24": [0.2, 1.5, 0.3], "peak_24": [0.3, 2.1, 0.4], "end_24": [0.1, 1.0, 0.2]}
    new = dict(base, hit_2_24=[True, True, False], stop_first_24=[False, False, True], x_trail30_24=[0.9, 1.5, 0.3], peak_24=[2.0, 2.1, 0.4])
    pq.write_table(pa.table(base), tmp_path / "old.parquet")
    pq.write_table(pa.table(new), tmp_path / "new.parquet")
    assert AB.main(["--old", str(tmp_path / "old.parquet"), "--new", str(tmp_path / "new.parquet"), "--out", str(tmp_path / "out"), "--tag", "t"]) == 0
    import json
    res = json.load(open(tmp_path / "out" / "ab-t.json"))
    assert res["red"] == []
    assert res["rows"] == [3, 3, 3] and res["entries_identical"] == 3 and res["rows_with_a_changed_label"] == 1
    assert res["by_moment"]["e10"]["hit_2_24"] == {"old": 1, "new": 2} and res["by_moment"]["e10"]["stop_first_24"] == {"old": 2, "new": 1}
    assert res["direction"] == {"peak_24_up": 1, "peak_24_down": 0} and (tmp_path / "out" / "ab-t.md").exists()


def test_ab_labels_is_fail_closed(tmp_path):
    """A synthetic decrease of peak_24, a changed entry or a missing row makes ab_labels exit 1 with the reason (the NAS job
    then goes RED before stage B: x10-hertelling-plan section 2)."""
    import json
    import pyarrow as pa
    import pyarrow.parquet as pq
    from wallets.x10 import ab_labels as AB
    base = {"key": ["A", "B"], "moment": ["e10"] * 2, "t": [1, 2], "tokens": [10, 10], "cost_sol": [0.5] * 2, "depth_sol": [0.1, 2.0],
            "hit_2_24": [False, True], "hit_5_24": [False] * 2, "hit_10_24": [False] * 2, "hit_2_72": [False, True], "hit_5_72": [False] * 2,
            "hit_10_72": [False] * 2, "stop_first_24": [True, False], "stop_first_72": [True, False],
            "x_tp2_24": [0.2, 2.0], "x_trail30_24": [0.2, 1.5], "x_tp5_24": [0.2, 1.5], "peak_24": [0.3, 2.1], "end_24": [0.1, 1.0]}
    pq.write_table(pa.table(base), tmp_path / "old.parquet")
    pq.write_table(pa.table(dict(base, peak_24=[0.3, 1.9])), tmp_path / "down.parquet")          # a peak went down
    pq.write_table(pa.table(dict(base, tokens=[11, 10])), tmp_path / "entry.parquet")            # an entry differs
    pq.write_table(pa.table({k: v[:1] for k, v in base.items()}), tmp_path / "short.parquet")    # a row is missing
    for name, reason in (("down", "peak_24 down"), ("entry", "entries not identical"), ("short", "rows differ")):
        rc = AB.main(["--old", str(tmp_path / "old.parquet"), "--new", str(tmp_path / f"{name}.parquet"), "--out", str(tmp_path / "out"), "--tag", name])
        res = json.load(open(tmp_path / "out" / f"ab-{name}.json"))
        assert rc == 1 and any(reason in r for r in res["red"]), (name, res["red"])
        assert open(tmp_path / "out" / f"ab-{name}.md").read().strip().splitlines()[-1].startswith("RED")


def test_labels_forward_flag_is_wired_through_runners_window(tmp_path, monkeypatch):
    """LAB nit on #574: the same wiring assert for x10.labels.run(forward=True) as for runners.run -- the window guard runs
    (forward_guard: forward store only, window inside), the cap is the store's end and the run records forward/cap; without the
    flag a forward store that would reach the hold-out is refused; a dev store with the flag is refused."""
    import json
    from wallets import common
    trades = [(S, True, 2 * 10**9, True), (S + 300, True, 3 * 10**9, False), (S + 400, True, 10**9, True), (S + 900, True, 2 * 10**9, True)]
    rows, states = _curve_rows(trades)
    _store(tmp_path / "f.duckdb", rows, [])
    w = duckdb.connect(str(tmp_path / "f.duckdb"))
    w.execute("ALTER TABLE mints ADD COLUMN create_tx UBIGINT")
    w.execute("UPDATE meta SET json = ?", [json.dumps({"ms_per_slot": {str(LO // 432_000): MS}, "period": "forward", "slot_start": LO, "slot_end_exclusive": LO + 60_000})])
    w.close()
    _store(tmp_path / "d.duckdb", rows, [])
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path / "spill"))
    with pytest.raises(ValueError):   # no flag: the dev guard refuses a window into the hold-out, forward store or not
        X.run(LO, common.HOLDOUT_START + 1, str(tmp_path / "o0"), store=str(tmp_path / "f.duckdb"), cfg="/nonexistent.yaml")
    with pytest.raises(ValueError):   # the flag on real slots: the store starts before epoch 1064
        X.run(LO, LO + 5_000, str(tmp_path / "o1"), store=str(tmp_path / "f.duckdb"), cfg="/nonexistent.yaml", forward=True)
    monkeypatch.setattr(common, "FORWARD_START", LO)
    with pytest.raises(ValueError):   # a dev store with the flag
        X.run(LO, LO + 5_000, str(tmp_path / "o2"), store=str(tmp_path / "d.duckdb"), cfg="/nonexistent.yaml", forward=True)
    info = X.run(LO, LO + 5_000, str(tmp_path / "o3"), store=str(tmp_path / "f.duckdb"), cfg="/nonexistent.yaml", forward=True)
    assert info["forward"] is True and info["cap"] == LO + 60_000 and info["hi_ext"] <= LO + 60_000 and info["entries"] >= 1
    assert runners.CAP == LO + 60_000
    runners.window(duckdb.connect(str(tmp_path / "d.duckdb")), LO, LO + 5_000)   # back to the dev cap for other tests
    assert runners.CAP == common.HOLDOUT_START


def test_labels_carry_the_peak_row_kind_and_mixed_tier_flag(tmp_path, monkeypatch):
    """Since 2026-10-24 every labels row says which kind of state row made its peak and whether that row's slot holds trades of the
    pool with more than one fee tier -- the only place where the pre-fix slot-ASOF tie could move a mark; ab_labels' tolerance
    reads them. On the curve-only fixture the peak row is a curve trade (kind NULL: pl has no row) and the flag is false."""
    trades = [(S, True, 2 * 10**9, True), (S + 300, True, 3 * 10**9, False), (S + 400, True, 10**9, True), (S + 900, True, 25 * 10**9, True),
              (S + 1500, False, 5 * 10**12, True)]
    rows, states = _curve_rows(trades)
    _store(tmp_path / "s.duckdb", rows, [])
    con = duckdb.connect(str(tmp_path / "s.duckdb"))
    con.execute("ALTER TABLE mints ADD COLUMN create_tx UBIGINT")
    con.close()
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path / "spill"))
    out = tmp_path / "out"
    X.run(LO, LO + 5_000, str(out), store=str(tmp_path / "s.duckdb"), cfg="/nonexistent.yaml")
    r = duckdb.connect().execute(f"SELECT peak_kind_24, peak_mixed_24, peak_kind_72, peak_mixed_72 FROM '{out}/labels-{LO}.parquet' WHERE moment = 'e10'").fetchone()
    assert r[0] is None and r[1] is False and r[2] is None and r[3] is False


def test_ab_labels_tier_tie_tolerance(tmp_path):
    """x10-hertelling-plan v0.5: with --allow-tier-tie-drops a peak_24 drop passes only when it is at most TIE_TOL relative AND the
    new peak row is a boost / withdraw / deposit row in a mixed-tier slot; it is listed. A larger drop, or a drop on a trade-row
    peak, stays RED; without the flag every drop is RED as before."""
    from wallets.x10 import ab_labels as AB
    base = {"tokens": [100, 100, 100, 100], "cost_sol": [0.5, 0.5, 0.5, 0.5], "hit_2_24": [True, False, False, False], "hit_5_24": [False] * 4, "hit_10_24": [False] * 4,
            "hit_2_72": [True, False, False, False], "hit_5_72": [False] * 4, "hit_10_72": [False] * 4, "stop_first_24": [False, True, False, False], "stop_first_72": [False, True, False, False],
            "x_tp2_24": [2.0, 0.3, 0.9, 0.9], "x_trail30_24": [1.5, 0.3, 0.9, 0.9], "x_tp5_24": [1.5, 0.3, 0.9, 0.9], "end_24": [1.0, 0.2, 0.8, 0.8], "depth_sol": [5.0] * 4}
    keys = {"key": ["A", "B", "C", "D"], "moment": ["e10"] * 4, "t": [1, 2, 3, 4]}
    old = dict(keys, **base, peak_24=[2.1, 0.4, 1.0, 1.0])
    # A: unchanged; B: up (the cap); C: down 0.1 % on a boost peak in a mixed slot (allowed); D: down 0.1 % on a trade-row peak (RED)
    new = dict(keys, **base, peak_24=[2.1, 0.9, 0.999, 0.999], peak_kind_24=[None, None, "boost", "buy"], peak_mixed_24=[False, False, True, False],
               peak_kind_72=[None] * 4, peak_mixed_72=[False] * 4)
    pq.write_table(pa.Table.from_pydict(old), str(tmp_path / "old.parquet"))
    pq.write_table(pa.Table.from_pydict(new), str(tmp_path / "new.parquet"))
    args = ["--old", str(tmp_path / "old.parquet"), "--new", str(tmp_path / "new.parquet"), "--out", str(tmp_path / "ab"), "--tag", "t"]
    assert AB.main(args) == 1                                   # no flag: two drops -> RED
    assert AB.main(args + ["--allow-tier-tie-drops"]) == 1     # D's drop is on a trade-row peak -> still RED
    res = json.load(open(tmp_path / "ab" / "ab-t.json"))
    assert [d["key"] for d in res["peak_24_drops_allowed"]] == ["C"] and [d["key"] for d in res["peak_24_drops_red"]] == ["D"]
    new["peak_kind_24"][3] = "withdraw"; new["peak_mixed_24"][3] = True
    pq.write_table(pa.Table.from_pydict(new), str(tmp_path / "new.parquet"))
    assert AB.main(args + ["--allow-tier-tie-drops"]) == 0     # both drops explained and within tolerance -> GREEN, listed
    md = open(tmp_path / "ab" / "ab-t.md").read()
    assert "2 peak_24 drops within the tier-tie tolerance" in md and md.count("ALLOWED") == 2
    new["peak_24"][2] = 0.99                                    # 1 % drop: beyond one tier step -> RED even on a boost peak
    pq.write_table(pa.Table.from_pydict(new), str(tmp_path / "new.parquet"))
    assert AB.main(args + ["--allow-tier-tie-drops"]) == 1
