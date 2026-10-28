"""Breadth-probe harness on the runners mini store: the fixed 3-slot entry, the trail30 / stop exit with the delayed
sell, the end-of-horizon fallback, the forced-cut (agent-free) exit, and runners.legs' mayhem opt-in."""
import duckdb
import pytest

from backtest import venues as V
from wallets import runners as R
from wallets.probes import harness as H
from wallets.tests.test_runners import LO, M, S, _curve_rows, _store

TX = 705_000


def _mini(tmp_path):
    # a buy-driven rise, then a sell that drops the price past 30 % of the peak, then a small recovery
    trades = [(S, True, 2 * 10**9, True), (S + 20, True, 3 * 10**9, True), (S + 40, True, 10**9, True), (S + 60, True, 25 * 10**9, True),
              (S + 100, False, 200 * 10**12, True), (S + 110, False, 100 * 10**12, True), (S + 140, True, 10**9, True), (S + 200, True, 10**9, True)]
    rows, states = _curve_rows(trades)
    _store(tmp_path / "s.duckdb", rows, [])
    return rows, states


def _run(tmp_path, monkeypatch, moments_sql, cut_sql=None, mayhem_curve=False, key=M):
    tmp_path = tmp_path / (key + ("_m" if mayhem_curve else ""))
    tmp_path.mkdir()
    rows, states = _mini(tmp_path)
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path / "spill"))
    con, clock = H.open_store(str(tmp_path / "s.duckdb"), "500MB", 1)
    hi_ext = H.prepare(con, LO, LO + 5_000, clock, H.tok_sql_for_mints(f"m.mint = '{key}'"), mayhem_curve=mayhem_curve)
    H.moments_from(con, moments_sql, clock)
    H.simulate(con, TX, hi_ext, clock, extra_exit_sql=cut_sql)
    ex = con.execute("SELECT * FROM ex ORDER BY t").arrow().read_all().to_pylist()
    return ex, states, con


def test_trail_exit_with_delayed_sell_and_end_fallback(tmp_path, monkeypatch):
    # moment at S + 10: the entry is the first trade with slot >= S + 13 -> S + 20 (d = 3 slots, fixed)
    ex, states, con = _run(tmp_path, monkeypatch, f"SELECT '{M}' AS key, 'e10' AS moment, {S + 10} AS t UNION ALL SELECT '{M}', 'late', {S + 150}")
    by = {r["moment"]: r for r in ex}
    r = by["e10"]
    assert r["d_slots"] == 3 and r["entry_slot"] == S + 20 and r["entry_venue"] == "curve"
    pre = states[0][1]
    fill = V.curve_buy_exact_in(pre, R.SIZE)
    assert abs(r["tokens"] - fill.tokens) <= 2 and r["cost"] == R.SIZE + TX
    # marks after the entry: S+20 (own), S+40, S+60 (peak), S+100 (big sell: trail or stop), S+110, S+140, S+200
    net = {slot: V.curve_sell(c, fill.tokens).trader_quote - TX for slot, c in states[1:]}
    peak = max(net[S + 40], net[S + 60])
    assert net[S + 100] <= 0.7 * peak, "the fixture must trigger the trail at S + 100"
    # the sell happens at the first mark with slot >= trigger slot + 3 -> S + 110
    assert r["trig_slot"] == S + 100 and r["exit_slot"] == S + 110 and r["exit_net"] == net[S + 110]
    assert r["reason"] in ("trail", "stop") and r["max_net"] == peak and r["n_marks"] == 7 and r["day"].startswith("20")
    # late moment: entry at S + 200 (the only trade >= S + 153), no later mark: exit = the last mark = the entry mark, reason end
    late = by["late"]
    assert late["entry_slot"] == S + 200 and late["reason"] == "end" and late["exit_slot"] == S + 200 and late["n_marks"] == 1


def test_forced_cut_exit_uses_the_last_mark_before_the_cut(tmp_path, monkeypatch):
    # cut at the S + 100 sell (as if it were an agent leg): the agent-free exit is the mark before it (S + 60)
    cut = f"SELECT '{M}' AS key, 'e10' AS moment, {S + 10} AS t, {(S + 100) * 1000000000 + 1 * 100000 + 2 * 1000 + 0} AS ord_cut"
    ex, states, con = _run(tmp_path, monkeypatch, f"SELECT '{M}' AS key, 'e10' AS moment, {S + 10} AS t", cut_sql=cut)
    r = ex[0]
    fill = V.curve_buy_exact_in(states[0][1], R.SIZE)
    net60 = V.curve_sell(states[3][1], fill.tokens).trader_quote - TX
    assert r["reason_cut"] == "cut" and r["exit_net_cut"] == net60 and r["exit_net"] != net60 and r["reason"] in ("trail", "stop")


def test_mayhem_legs_only_when_asked(tmp_path, monkeypatch):
    ex, _, con = _run(tmp_path, monkeypatch, f"SELECT 'Mmay' AS key, 'e10' AS moment, {S + 10} AS t", key="Mmay")
    assert ex == [] and con.execute("SELECT count(*) FROM cl").fetchone()[0] == 0
    ex2, _, con2 = _run(tmp_path, monkeypatch, f"SELECT 'Mmay' AS key, 'e10' AS moment, {S + 10} AS t", mayhem_curve=True, key="Mmay")
    assert len(ex2) == 1 and ex2[0]["mayhem"] is True and con2.execute("SELECT count(*) FROM cl").fetchone()[0] == 8


def test_with_size_restores_the_global():
    before = R.SIZE
    assert H.with_size(5.0, lambda: R.SIZE) == 5 * 10**9 and R.SIZE == before


def test_e71_parity_on_the_fixture_curve(tmp_path, monkeypatch):
    from wallets.probes import e71
    ex, _, con = _run(tmp_path, monkeypatch, f"SELECT 'Mmay' AS key, 'e10' AS moment, {S + 10} AS t", mayhem_curve=True, key="Mmay")
    par = e71.parity(con, n=100)
    assert par["sampled"] == 8 and par["parity_ok"] and par["sells_ok"] == par["sells"] == 2 and par["buys_ok"] == par["buys"] == 6


def test_report_capacity_and_null_cost_rows(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from wallets.probes import report as P
    # capacity: one position per key, 5 seats, trigger order
    rows = [{"key": "A", "t": 10, "entry_slot": 13, "exit_slot": 100}, {"key": "A", "t": 20, "entry_slot": 23, "exit_slot": 50},
            {"key": "B", "t": 15, "entry_slot": 18, "exit_slot": 30}] + [{"key": f"K{i}", "t": 16, "entry_slot": 19, "exit_slot": 90} for i in range(5)]
    taken = P.capacity(rows)
    assert [r["key"] for r in taken] == ["A", "B", "K0", "K1", "K2"]   # A busy at t 20; seats full for K3 / K4
    # e74 rows with a NULL cost_sol are skipped and counted; the bootstrap pads the day basis with zero days
    tbl = pa.table({"probe": ["e74"] * 3, "size_sol": [0.5] * 3, "moment": ["e10"] * 3, "key": ["X", "Y", "Z"], "pool": [None] * 3, "t": [1, 2, 3],
                    "entry_slot": [4, 5, 6], "exit_slot": [10, 11, 12], "day": ["2026-09-20", "2026-09-21", "2026-09-21"], "reason": ["end"] * 3,
                    "cost_sol": [10**9, None, 10**9], "exit_sol": [12 * 10**8, 5 * 10**8, 9 * 10**8], "cost": [1, 1, 1], "exit_net": [1, 1, 1]})
    pq.write_table(tbl, tmp_path / "e74-1-s0.5.parquet")
    rows, skipped = P.load(str(tmp_path), "e74")
    assert skipped == 1 and len(rows) == 2
    ci = P.bootstrap(P.by_day(rows), 200, all_days=["2026-09-19", "2026-09-20", "2026-09-21"])
    assert ci["days"] == 3


def test_e71_parity_accepts_exact_out_buys():
    from backtest import venues as V
    from wallets.probes import e71
    import duckdb
    c = V.Curve(1_073_000_000_000_000, 30_000_000_000, 793_100_000_000_000, 0, 95, 30)
    t = 10**12
    q = t * c.vq // (c.vt - t) + 1          # a 'buy' (exact tokens out) as the store logs it: q is the curve quote
    con = duckdb.connect()
    con.execute(f"""CREATE TEMP TABLE cl AS SELECT true AS is_buy, {q}::HUGEINT AS sol, {t}::HUGEINT AS tokens, {c.vt}::HUGEINT AS vt_pre, {c.vq}::HUGEINT AS vq_pre,
                    {c.rt}::HUGEINT AS rt_pre, 0::HUGEINT AS rq_pre, 95 AS fee_bps, 30 AS creator_bps, 'W' AS trader, NULL::VARCHAR AS fee_payer""")
    par = e71.parity(con)
    assert par["buys_ok"] == 1 and par["buys_ok_exact_out"] == 1 and par["parity_ok"]


def test_e72_ranks_generic_filter_and_originals():
    import duckdb
    from wallets.common import Clock
    from wallets.probes import e72
    con = duckdb.connect()
    con.execute("CREATE TABLE mints (mint VARCHAR, create_slot UBIGINT, symbol VARCHAR, quote_mint VARCHAR, mayhem BOOLEAN)")
    sysk = e72.SYS
    lo = 1040 * 432_000
    rows = [("O1", lo + 100, "$Cat", sysk, False), ("C1", lo + 200, "cat", sysk, False), ("C2", lo + 300, "CAT!", sysk, False),
            ("C3", lo + 400, "cat", sysk, False), ("C4", lo + 500, "cat", sysk, False), ("C5", lo + 600, "cat", sysk, False),   # the 6th 'cat' has n_prev60 = 5 -> no copy
            ("L", lo + 20_000, "cat", sysk, False), ("M", lo + 250, "cat", sysk, True), ("P", lo + 260, "cat", "PUMPx", False), ("E", lo + 270, "", sysk, False)]
    # generic, point-in-time: 25 'test' creates in the 24 h before the window (outside 60 min), then a 'test' pair inside it:
    # the second of the pair is a rank-2 copy by the 60 min rule but has 26 earlier 'test' creates in 24 h -> dropped
    rows += [(f"T{i}", lo - 100_000 + i * 1_000, "test", sysk, False) for i in range(25)] + [("TA", lo + 700, "test", sysk, False), ("TB", lo + 710, "test", sysk, False)]
    con.executemany("INSERT INTO mints VALUES (?, ?, ?, ?, ?)", rows)
    con.execute("CREATE TABLE meta (json VARCHAR)")
    con.execute("INSERT INTO meta VALUES (?)", ['{"ms_per_slot": {"1040": 267.0}}'])
    creates, copies, generic = e72.ranks(con, lo, lo + 432_000, Clock.from_store(con))
    c = {r[0]: r for r in con.execute("SELECT mint, rank, orig_mint FROM copies ORDER BY create_slot").fetchall()}
    assert set(c) == {"C1", "C2", "C3", "C4"} and c["C1"][1] == 2 and c["C4"][1] == 5 and c["C2"][2] == "O1"   # '$Cat' / 'CAT!' -> 'cat'
    assert generic == 1 and copies == 5  # TB dropped by the 24 h filter; C1-C4 + TB are the rank 2-5 copies by the 60 min rule
    assert con.execute("SELECT mint, t FROM origs").fetchall() == [("O1", lo + 200)]
    # L is 20,000 slots (~89 min) later: outside 60 min -> not a copy; M (mayhem), P (PUMP quote), E (empty key) are not in km / ranks
    assert con.execute("SELECT count(*) FROM ranks WHERE mint IN ('L', 'M', 'P', 'E')").fetchone()[0] == 1


def test_e73_triggers_k_distinct_wallets_and_cooldown():
    import duckdb
    from wallets.common import Clock
    from wallets.probes import e73
    con = duckdb.connect()
    con.execute("CREATE TABLE meta (json VARCHAR)")
    con.execute("INSERT INTO meta VALUES (?)", ['{"ms_per_slot": {"1040": 250.0}}'])
    lo = 1040 * 432_000
    # key A: W1 at +0, W2 at +40 (10 s), W1 again at +50 (same wallet: no), W3 at +100 (25 s after W1, 15 s after W2), then far later W4 / W5
    buys = [("A", lo + 0, "W1"), ("A", lo + 40, "W2"), ("A", lo + 50, "W1"), ("A", lo + 100, "W3"), ("A", lo + 20_000, "W4"), ("A", lo + 20_010, "W5"),
            ("B", lo + 0, "W1"), ("B", lo + 1_000, "W2")]   # B: 250 s apart -> only the 120 s cell never, 30 s never
    con.execute("CREATE TABLE gb (key VARCHAR, slot UBIGINT, ord UBIGINT, trader VARCHAR)")
    con.executemany("INSERT INTO gb VALUES (?, ?, ?, ?)", [(k, s, s * 1000, w) for k, s, w in buys])
    e73.triggers(con, Clock.from_store(con))
    t = sorted(con.execute("SELECT moment, key, t FROM trig_all").fetchall())
    # k2n30 (120 slots): A triggers at +40 (W2 after W1), cooldown 60 min = 14,400 slots -> the +20,010 pair triggers again
    assert ("k2n30", "A", lo + 40) in t and ("k2n30", "A", lo + 20_010) in t and ("k2n30", "A", lo + 100) not in t
    # k3n30: the third distinct wallet W3 at +100 (W1, W2 within 120 slots before)
    assert ("k3n30", "A", lo + 100) in t and not any(m == "k3n30" and k == "A" and s > lo + 100 for m, k, s in t)
    # k2n10 (40 slots): W2 at +40 is exactly 40 slots after W1 -> counts (>= a.slot - 40)
    assert ("k2n10", "A", lo + 40) in t
    assert not any(k == "B" for _, k, _ in t)


def test_e73_same_slot_buys_use_instruction_order_not_future_trades():
    """Two selected traders in one slot are within 30 s; the earlier instruction cannot see the later one."""
    import duckdb
    from wallets.common import Clock
    from wallets.probes import e73

    con = duckdb.connect()
    con.execute("CREATE TABLE meta (json VARCHAR)")
    con.execute("INSERT INTO meta VALUES (?)", ['{"ms_per_slot": {"1040": 250.0}}'])
    lo = 1040 * 432_000
    con.execute("CREATE TABLE gb (key VARCHAR, slot UBIGINT, ord UBIGINT, trader VARCHAR)")
    con.executemany("INSERT INTO gb VALUES (?, ?, ?, ?)", [
        ("same-slot", lo, lo * 1_000_000_000 + 100_000, "W1"),
        ("same-slot", lo, lo * 1_000_000_000 + 200_000, "W2"),
        ("same-wallet", lo, lo * 1_000_000_000 + 100_000, "W1"),
        ("same-wallet", lo, lo * 1_000_000_000 + 200_000, "W1"),
    ])
    e73.triggers(con, Clock.from_store(con))
    assert con.execute("SELECT key, slot, ord FROM cand_k2n30 ORDER BY key").fetchall() == [
        ("same-slot", lo, lo * 1_000_000_000 + 200_000)
    ]
    assert con.execute("SELECT key, moment, t FROM trig_all WHERE moment = 'k2n30'").fetchall() == [
        ("same-slot", "k2n30", lo)
    ]


def test_e73_good_set_threshold_is_5_sol_in_lamports(tmp_path):
    import duckdb
    import pyarrow as pa
    import pyarrow.parquet as pq
    from wallets.probes import e73
    # two wallets, 7 positive days each before 2026-09-19: A sums to 4.9 SOL (out), B to 5.1 SOL (in); C has only 4 days (out)
    rows = []
    for i in range(7):
        day = f"2026-09-{12 + i:02d}"
        rows.append(("A", day, 0.7 * 10**9, 0, 10**8))
        rows.append(("B", day, int(5.1 * 10**9 / 7) + 1, 0, 10**8))
        if i < 4:
            rows.append(("C", day, 10**10, 0, 10**8))
    rows.append(("Z", "2026-09-19", 10**9, 0, 10**8))   # the measured day exists in the data (Z itself has no history)
    pq.write_table(pa.table({"wallet": [r[0] for r in rows], "day": [r[1] for r in rows], "cash": [float(r[2]) for r in rows],
                             "fees": [float(r[3]) for r in rows], "buy_sol": [float(r[4]) for r in rows]}), tmp_path / "wallet-day-0.parquet")
    con = duckdb.connect()
    e73.good_days(con, str(tmp_path / "out"), money=str(tmp_path)) if (tmp_path / "out").mkdir() is None else None
    good = con.execute("SELECT wallet, day FROM good ORDER BY 1, 2").fetchall()
    assert ("B", "2026-09-19") in good and not any(w == "A" for w, _ in good) and not any(w == "C" for w, _ in good)
    assert all(d >= "2026-09-19" for _, d in good)


def test_probe_run_creates_the_out_dir_before_side_files(tmp_path, monkeypatch):
    """E71 attempt 1 on the NAS failed: mint_peaks was COPY'd before H.write created the out dir (2026-10-24)."""
    import inspect
    from wallets.probes import e71, e72, e73, run as PR
    for mod in (e71, e72, e73):
        src = inspect.getsource(mod.run)
        assert src.index("os.makedirs(out_dir, exist_ok=True)") < src.index("H.write"), mod.__name__
    assert "os.makedirs(a.out, exist_ok=True)" in inspect.getsource(PR.main)


def test_e71_run_succeeds_on_a_nonexistent_out_dir(tmp_path, monkeypatch):
    """The real path: e71.run on the mini store (mayhem twin, SOL quote) into an out dir that does not exist yet."""
    import duckdb
    from wallets.probes import e71
    rows, states = _mini(tmp_path)
    w = duckdb.connect(str(tmp_path / "s.duckdb"))
    w.execute("ALTER TABLE mints ADD COLUMN quote_mint VARCHAR")
    w.execute("ALTER TABLE mints ADD COLUMN symbol VARCHAR")
    w.execute(f"UPDATE mints SET quote_mint = '{e71.SYS}', symbol = 'TST'")
    w.close()
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path / "spill"))
    con, clock = H.open_store(str(tmp_path / "s.duckdb"), "500MB", 1)
    out = tmp_path / "new" / "deeper" / "s0.5"
    assert not out.exists()
    path, info = e71.run(con, LO, LO + 5_000, clock, 0.5, TX, str(out))
    assert out.exists() and (out / f"mint_peaks-{LO}.parquet").exists() and (out / f"run-e71-{LO}.json").exists()
    assert info["rows"] == 1 and info["parity_ok"] and info["base_rates"]["mints"] == 1


def test_e73_good_buys_count_token_buys_on_both_pool_orientations():
    """Codex finding #1 (2026-10-24): the store flags a token buy with token_buy, orientation-aware (store.py: on a reversed
    pool, WSOL is the base, so a token buy is kind 'sell'). E73's good-wallet buys must take token buys on normal and reversed
    pools, and leave out token sells, boost / withdraw / deposit rows, non-SOL quotes and Mayhem venues; the curve branch
    takes is_buy. Checked on tables in the store's own shapes."""
    import duckdb
    from wallets.probes import e73
    con = duckdb.connect()
    lo = 1040 * 432_000
    con.execute("CREATE TABLE mints (mint VARCHAR, quote_mint VARCHAR, mayhem BOOLEAN)")
    con.execute("CREATE TABLE curve (mint VARCHAR, slot UBIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT, trader VARCHAR, is_buy BOOLEAN)")
    con.execute("CREATE TABLE pools (pool VARCHAR, mint VARCHAR, orientation VARCHAR, quote_class VARCHAR, mayhem BOOLEAN)")
    con.execute("CREATE TABLE pool (pool VARCHAR, slot UBIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT, trader VARCHAR, kind VARCHAR, token_buy BOOLEAN)")
    con.executemany("INSERT INTO mints VALUES (?, ?, ?)", [("Mn", e73.SYS, False), ("Mr", e73.SYS, False), ("Mm", e73.SYS, True), ("Mp", "PUMPquote", False)])
    con.executemany("INSERT INTO curve VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [("Mn", lo + 1, 1, 0, 0, "Wc", True), ("Mn", lo + 2, 1, 0, 0, "Wc", False), ("Mm", lo + 3, 1, 0, 0, "Wc", True), ("Mp", lo + 4, 1, 0, 0, "Wc", True)])
    con.executemany("INSERT INTO pools VALUES (?, ?, ?, ?, ?)",
                    [("Pn", "Mn", "normal", "sol", False), ("Pr", "Mr", "reversed", "reversed", False), ("Pm", "Mm", "reversed", "reversed", True), ("Po", "Mp", "normal", "other", False)])
    rows = [  # (pool, slot, kind, token_buy, trader): the store's conventions per orientation
        ("Pn", lo + 10, "buy", True, "W1"),        # normal pool, token buy
        ("Pn", lo + 11, "sell", False, "W1"),      # normal pool, token sell
        ("Pr", lo + 12, "sell", True, "W2"),       # reversed pool, token buy (kind 'sell': WSOL is the base)
        ("Pr", lo + 13, "buy", False, "W2"),       # reversed pool, token sell
        ("Pn", lo + 14, "boost", None, "W3"), ("Pn", lo + 15, "withdraw", None, "W3"), ("Pn", lo + 16, "deposit", None, "W3"),
        ("Pm", lo + 17, "sell", True, "W4"),       # Mayhem reversed pool: out
        ("Po", lo + 18, "buy", True, "W5"),        # non-SOL quote: out
        ("Pn", lo + 500, "buy", True, "W6"),       # outside the window
    ]
    con.executemany("INSERT INTO pool VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [(p, s, 1, 0, 0, w, k, tb) for p, s, k, tb, w in rows])
    got = sorted(con.execute(f"SELECT key, slot, trader FROM ({e73.good_buys_sql(lo, lo + 100)})").fetchall())
    assert got == [("Mn", lo + 1, "Wc"), ("Mn", lo + 10, "W1"), ("Pr", lo + 12, "W2")], got


def test_probes_run_forward_flag_is_wired_through_the_harness(tmp_path, monkeypatch):
    """LAB nit on #574: probes.run --forward sets the harness flag and prepare() goes through runners.window(forward=True):
    a dev store with the flag is refused, a forward store (window inside, start at FORWARD_START) runs with the cap at the
    store's end; without the flag the forward store is read under the dev guard (its slots are dev slots here)."""
    import json
    from wallets import common
    from wallets.probes import e71, run as PR
    rows, states = _mini(tmp_path)
    for name in ("s.duckdb",):
        w = duckdb.connect(str(tmp_path / name))
        w.execute("ALTER TABLE mints ADD COLUMN quote_mint VARCHAR")
        w.execute("ALTER TABLE mints ADD COLUMN symbol VARCHAR")
        w.execute(f"UPDATE mints SET quote_mint = '{e71.SYS}', symbol = 'TST'")
        w.close()
    import shutil
    shutil.copy(tmp_path / "s.duckdb", tmp_path / "f.duckdb")
    w = duckdb.connect(str(tmp_path / "f.duckdb"))
    w.execute("UPDATE meta SET json = ?", [json.dumps({"ms_per_slot": {str(LO // 432_000): 400.0}, "period": "forward", "slot_start": LO, "slot_end_exclusive": LO + 60_000})])
    w.close()
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path / "spill"))
    monkeypatch.setattr(common, "FORWARD_START", LO)
    try:
        with pytest.raises(ValueError):   # dev store + --forward
            PR.main(["--probe", "e71", "--from-slot", str(LO), "--to-slot", str(LO + 5_000), "--out", str(tmp_path / "o1"), "--store", str(tmp_path / "s.duckdb"), "--memory", "500MB", "--threads", "1", "--forward"])
        assert H.FORWARD is True
        PR.main(["--probe", "e71", "--from-slot", str(LO), "--to-slot", str(LO + 5_000), "--out", str(tmp_path / "o2"), "--store", str(tmp_path / "f.duckdb"), "--memory", "500MB", "--threads", "1", "--forward"])
        assert (tmp_path / "o2" / f"run-e71-{LO}.json").exists() and R.CAP == LO + 60_000
        PR.main(["--probe", "e71", "--from-slot", str(LO), "--to-slot", str(LO + 5_000), "--out", str(tmp_path / "o3"), "--store", str(tmp_path / "s.duckdb"), "--memory", "500MB", "--threads", "1"])
        assert H.FORWARD is False and R.CAP == common.HOLDOUT_START
    finally:
        H.FORWARD = False


def test_e73_empty_window_writes_an_empty_result_instead_of_crashing(tmp_path, monkeypatch):
    """E73 on the NAS (99zzzzz79) crashed in window 446256000: the good set's days (19 Sep on) lie after the window, so every
    cell had 0 candidates and the untyped empty VALUES made trig_all.key an INTEGER -> BinderException in the harness's
    m.mint IN (SELECT key FROM trig_all). An empty window must give an empty result file with rows 0 and typed triggers."""
    import json
    import duckdb
    import pyarrow as pa
    import pyarrow.parquet as pq
    from wallets.probes import e73
    rows, states = _mini(tmp_path)
    w = duckdb.connect(str(tmp_path / "s.duckdb"))
    w.execute("ALTER TABLE mints ADD COLUMN quote_mint VARCHAR")
    w.execute(f"UPDATE mints SET quote_mint = '{e73.SYS}'")
    w.close()
    good = tmp_path / "good-days.parquet"   # a good set whose only day lies years after the fixture window: no good buy in it
    pq.write_table(pa.table({"wallet": ["W0", "W1"], "day": ["2030-01-01", "2030-01-01"]}), good)
    monkeypatch.setenv("LAB_E73_GOOD", str(good))
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path / "spill"))
    con, clock = H.open_store(str(tmp_path / "s.duckdb"), "500MB", 1)
    out = tmp_path / "out"
    path, info = e73.run(con, LO, LO + 5_000, clock, 0.5, TX, str(out))
    assert info["rows"] == 0 and info["good_buys"] == 0 and info["good_wallet_days"] == 2
    assert con.execute("SELECT count(*) FROM trig_all").fetchone()[0] == 0
    assert [t[1] for t in con.execute("DESCRIBE trig_all").fetchall()] == ["VARCHAR", "VARCHAR", "UBIGINT"]
    assert con.execute(f"SELECT count(*) FROM read_parquet('{path}')").fetchone()[0] == 0
    assert json.load(open(out / f"run-e73-{LO}.json"))["rows"] == 0


def test_e73_triggers_keep_their_types_when_some_cells_are_empty():
    """One cell with a trigger, the others empty: the UNION ALL stays VARCHAR / VARCHAR / UBIGINT (no INTEGER key)."""
    import duckdb
    from wallets.common import Clock
    from wallets.probes import e73
    con = duckdb.connect()
    con.execute("CREATE TABLE meta (json VARCHAR)")
    con.execute("INSERT INTO meta VALUES (?)", ['{"ms_per_slot": {"1040": 250.0}}'])
    lo = 1040 * 432_000
    con.execute("CREATE TABLE gb (key VARCHAR, slot UBIGINT, ord UBIGINT, trader VARCHAR)")
    con.executemany("INSERT INTO gb VALUES (?, ?, ?, ?)", [("A", lo, lo * 1000, "W1"), ("A", lo + 400, (lo + 400) * 1000, "W2")])   # 100 s apart: only k2n120
    e73.triggers(con, Clock.from_store(con))
    assert con.execute("SELECT key, moment, t FROM trig_all").fetchall() == [("A", "k2n120", lo + 400)]
    assert [t[1] for t in con.execute("DESCRIBE trig_all").fetchall()] == ["VARCHAR", "VARCHAR", "UBIGINT"]


def _e73_report_input(tmp_path, days):
    import pyarrow as pa
    import pyarrow.parquet as pq
    n = len(days)
    tbl = pa.table({"probe": ["e73"] * n, "size_sol": [0.5] * n, "moment": ["k2n30"] * n,
                    "key": [f"K{i}" for i in range(n)], "pool": [None] * n,
                    "t": [10 + 100 * i for i in range(n)], "entry_slot": [13 + 100 * i for i in range(n)],
                    "exit_slot": [20 + 100 * i for i in range(n)], "day": pa.array(days, type=pa.string()),
                    "reason": ["end"] * n, "cost": [500_000_000] * n, "exit_net": [600_000_000] * n})
    pq.write_table(tbl, tmp_path / "e73-1-s0.5.parquet")


def test_e73_report_uses_fixed_calendar_including_days_without_any_signal(tmp_path):
    """Real report entry point: eleven absent dates must still be bootstrap units and zero daily-net rows."""
    import datetime
    import json
    from wallets.probes import report as P
    _e73_report_input(tmp_path, ["2026-09-19", "2026-10-01"])
    P.main(["--probe", "e73", "--in", str(tmp_path), "--parts", str(tmp_path / "no-reference"), "--boot", "200"])
    res = json.loads((tmp_path / "report-e73.json").read_text())
    expected_days = [(datetime.date(2026, 9, 19) + datetime.timedelta(days=i)).isoformat() for i in range(13)]
    assert res["days_with_signals"] == ["2026-09-19", "2026-10-01"]
    assert res["bootstrap_calendar_days"] == expected_days
    assert res["calendar_source"]["sha256"] == "7bbd1aa112bdad00bba8ff504749d6bb04cf1af99b4ee9fd3ae9d70b590490b1"
    primary = res["cells"]["k2n30"]["10"]
    assert primary["day_basis"] == primary["ci"]["days"] == 13
    assert primary["days_with_trades"] == 2 and primary["trades_per_day"] == pytest.approx(2 / 13)
    assert list(primary["daily_net_sol"]) == expected_days
    assert primary["daily_net_sol"]["2026-09-20"] == 0
    assert sum(primary["daily_net_sol"].values()) == pytest.approx(0.2)
    assert res["verdict"] == "STOP"  # positive trades are not enough to satisfy the fixed trades/day rule
    assert "13 dagen, inclusief dagen zonder signalen" in (tmp_path / "report-e73.md").read_text()


@pytest.mark.parametrize("day", [None, "2026-09-18", "2026-10-02", "2026-10-20"])
def test_e73_report_refuses_missing_or_out_of_contract_dates(tmp_path, day):
    from wallets.probes import report as P
    _e73_report_input(tmp_path, [day])
    with pytest.raises(ValueError, match="preregistered"):
        P.main(["--probe", "e73", "--in", str(tmp_path), "--parts", str(tmp_path / "no-reference"), "--boot", "20"])
    assert not (tmp_path / "report-e73.json").exists()


def test_e73_report_keeps_calendar_when_entire_selection_has_no_signals(tmp_path):
    import json
    from wallets.probes import report as P
    _e73_report_input(tmp_path, [])
    P.main(["--probe", "e73", "--in", str(tmp_path), "--parts", str(tmp_path / "no-reference"), "--boot", "20"])
    res = json.loads((tmp_path / "report-e73.json").read_text())
    assert len(res["bootstrap_calendar_days"]) == 13 and res["days_with_signals"] == []
    assert res["verdict"] == "NO_PRIMARY_CELL"
