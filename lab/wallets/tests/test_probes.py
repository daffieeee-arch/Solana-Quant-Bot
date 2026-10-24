"""Breadth-probe harness on the runners mini store: the fixed 3-slot entry, the trail30 / stop exit with the delayed
sell, the end-of-horizon fallback, the forced-cut (agent-free) exit, and runners.legs' mayhem opt-in."""
import duckdb

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
