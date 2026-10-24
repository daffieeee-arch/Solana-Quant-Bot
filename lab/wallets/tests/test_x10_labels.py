"""x10 stage A on the runners mini store: the four moments, the labels (target before the stop, within the horizon), the
delayed exits (the sell at the first mark >= trigger slot + d), the per-mint peak and early-buyer tables and the extra
features, all recomputed in Python from the same engine states the runners test uses."""
import duckdb

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
    AB.main(["--old", str(tmp_path / "old.parquet"), "--new", str(tmp_path / "new.parquet"), "--out", str(tmp_path / "out"), "--tag", "t"])
    import json
    res = json.load(open(tmp_path / "out" / "ab-t.json"))
    assert res["rows"] == [3, 3, 3] and res["entries_identical"] == 3 and res["rows_with_a_changed_label"] == 1
    assert res["by_moment"]["e10"]["hit_2_24"] == {"old": 1, "new": 2} and res["by_moment"]["e10"]["stop_first_24"] == {"old": 2, "new": 1}
    assert res["direction"] == {"peak_24_up": 1, "peak_24_down": 0} and (tmp_path / "out" / "ab-t.md").exists()
