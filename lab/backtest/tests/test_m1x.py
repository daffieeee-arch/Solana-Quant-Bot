"""M1+ / M1-early (week3 specs): the triggers at open + moment equal a direct recomputation from the
store's pool events (no pool missed, none extra), the veto rules, and the paired veto rows and N2
ages of a small tournament run."""

import os

import duckdb
import pytest

from backtest import strategies as S
from backtest.store import load_defaults

STORE = "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb"
LO, HI = 450_700_000, 450_760_000


def test_veto_rules():
    assert S.m1x_vetoed("V_ins", 0.91, 10) and not S.m1x_vetoed("V_ins", 0.90, 10)
    assert S.m1x_vetoed("V_ins", 1.4, 10)  # shares above 1 (invisible transfers) are vetoed
    assert S.m1x_vetoed("V_ins", None, 10)  # undefined vetoes: fail-closed (vins-definition-2026-10-15.md)
    assert not S.m1x_vetoed("V_ins", 0.5, 10) and not S.m1x_vetoed("V_ins+V_hype", 0.5, None)  # V_hype unknown: no veto
    assert S.m1x_vetoed("V_ins+V_hype", 0.1, 586) and not S.m1x_vetoed("V_ins+V_hype", 0.1, 585)
    assert S.m1x_vetoed("V_ins+V_bundle", 0.1, 5) and not S.m1x_vetoed("V_ins+V_bundle", 0.1, 6)
    with pytest.raises(ValueError):
        S.m1x_vetoed("V_other", 0.1, 6)
    assert S.m1x_variant("M1P", 900_000, 1.5, 0.2) == "k15_trail20"
    assert S.m1x_variant("M1E", 120_000, 1.5, 0.35) == "m2_trail35"


@pytest.fixture(scope="module")
def con():
    if not os.path.exists(STORE):
        pytest.skip("dev store not built")
    c = duckdb.connect(STORE, read_only=True)
    c.execute("SET memory_limit = '1GB'; SET threads TO 2")
    return c


def test_candidates_equal_a_direct_recomputation(con):
    clock = S.Clock.from_store(con)
    lo, hi = S.decision_bounds(load_defaults(), LO - 1_000_000, HI, clock)
    moments = (60_000, 120_000, 300_000, 900_000)
    got = {(r[1], r[2]): r for r in S.m1x_candidates_sql(con, load_defaults(), LO - 1_000_000, HI, moments, clock)
           if LO <= r[3] < HI}
    want = {}
    pools = con.execute(f"""SELECT p.pool, p.create_slot, p.q0::DOUBLE / p.b0 FROM pools p JOIN mints m ON m.mint = p.mint
                            WHERE p.quote_class = 'sol' AND NOT COALESCE(p.mayhem, false) AND p.created_in_period AND p.b0 > 0
                              AND NOT m.mayhem AND COALESCE(m.complete_slot, -1) <> m.create_slot
                              AND p.create_slot BETWEEN {LO - 5000} AND {HI}""").fetchall()
    assert pools
    for pool, cs, open_price in pools:
        for mo in moments:
            t = cs + round(mo / clock.ms_per_slot(cs))
            if not (LO <= t < HI and lo <= t <= hi):
                continue
            ev = con.execute("""SELECT token_buy, farmer, trader, sol_amount, e, e_delta, b, base, sol_depth FROM pool
                                WHERE pool = ? AND kind IN ('buy', 'sell') AND slot BETWEEN ? AND ?
                                ORDER BY slot, tx_index, outer_ix, inner_ix""", [pool, cs, t]).fetchall()
            if not ev:
                continue
            tb, _, _, _, e, ed, b, base, depth = ev[-1]
            price = (e + ed) / (b - base if tb else b + base)
            flow = sum(0 if x[1] else (x[3] if x[0] else -x[3]) for x in ev)
            buyers = len({x[2] for x in ev if x[0] and not x[1]})
            want[(pool, mo)] = (t, depth, price / open_price, flow, buyers)
    assert set(got) == set(want) and len(want) > 20
    for k, (t, depth, ratio, flow, buyers) in want.items():
        g = got[k]
        assert (g[3], g[4], g[6], g[7]) == (t, depth, flow, buyers), k
        assert g[5] == pytest.approx(ratio, rel=1e-12), k


def test_tournament_rows_pair_the_vetoes_and_n2_is_young(tmp_path, monkeypatch, con):
    """A small run of both families: every veto row is the base row or a 'veto' skip; N2_M1E entries
    are in pools at most 10 minutes old; m1x.md is written."""
    from backtest import tournament as T

    monkeypatch.setattr(T, "OUT", str(tmp_path))
    monkeypatch.setenv("LAB_TRIALS", str(tmp_path / "trials"))
    import backtest.report as R
    monkeypatch.setattr(R, "TRIALS", str(tmp_path / "trials"))
    T.run(["M1P", "M1E", "N2"], [0.5], [1], ["empirical"], [0.5], 0.05, 1, 0, 0, "m1x", n2_k=1, batch=40,
          label="m1x_test", slot_range=(LO, HI))
    out = tmp_path / "m1x"
    db = duckdb.connect()
    db.execute(f"CREATE TABLE r AS SELECT * FROM read_parquet('{out}/parts/*.parquet')")
    assert {x[0] for x in db.execute("SELECT DISTINCT family FROM r").fetchall()} == {"M1P", "M1E", "N2_M1P", "N2_M1E"}
    key = "row(family, variant, mint, t, COALESCE(pool, ''), size_sol, d, q)"
    for ov in ("V_ins", "V_ins+V_hype", "V_ins+V_bundle"):
        pairs = db.execute(f"""SELECT o.exit_reason, o.proceeds, b.proceeds, b.ins_share, b.curve_buyers
                               FROM (SELECT {key} AS k, * FROM r WHERE overlay = '{ov}') o
                               LEFT JOIN (SELECT {key} AS k, * FROM r WHERE overlay = 'none') b USING (k)""").fetchall()
        assert pairs and all(p[2] is not None or p[0] == "veto" for p in pairs)  # every veto row has its base row
        for reason, proceeds, base_proceeds, share, buyers in pairs:
            vetoed = S.m1x_vetoed(ov, share, buyers)
            assert (reason == "veto") == vetoed
            assert vetoed or proceeds == base_proceeds
    clock = S.Clock.from_store(con)
    n2 = db.execute("SELECT DISTINCT pool, t FROM r WHERE family = 'N2_M1E' AND overlay = 'none'").fetchall()
    assert n2
    for pool, t in n2:
        cs = con.execute("SELECT create_slot FROM pools WHERE pool = ?", [pool]).fetchone()[0]
        assert 0 <= t - cs <= clock.slots(600_000, t), (pool, t, cs)
    assert (out / "m1x.md").exists() and "only days 10-18" in (out / "m1x.md").read_text()
