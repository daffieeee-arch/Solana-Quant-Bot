"""trigger_gap on a tiny in-memory pool table: pre-deep history, the farmer flag with and without it."""

import duckdb
import pyarrow as pa

from wallets import trigger_gap as G


def _con(rows):
    con = duckdb.connect()
    con.execute("SET memory_limit = '200MB'; SET threads TO 1")
    con.execute("CREATE TABLE pool (pool VARCHAR, trader VARCHAR, slot BIGINT, kind VARCHAR, sol_depth BIGINT, token_buy BOOLEAN, token_amount BIGINT)")
    con.executemany("INSERT INTO pool VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return con


def test_pre_deep_history_and_flag_flip():
    SOL = G.SOL
    rows = [
        # pool P: created shallow, deep from slot 100; seller W farms it: buy 100 / sell 100 before deep, one buy after
        ("P", "W", 10, "buy", 85 * SOL, True, 100), ("P", "W", 20, "sell", 90 * SOL, False, 100),
        ("P", "X", 100, "buy", 160 * SOL, True, 5), ("P", "W", 120, "buy", 170 * SOL, True, 100),
        ("P", "W", 200, "sell", 180 * SOL, False, 100),                      # the trigger sell itself (slot t): excluded
        # pool Q: deep from its first trade; seller V has no history
        ("Q", "V", 50, "sell", 300 * SOL, False, 10), ("Q", "U", 60, "buy", 300 * SOL, True, 10),
    ]
    con = _con(rows)
    trig = pa.table({"pool": ["P", "Q"], "t": [200, 60], "trader": ["W", "U"], "farm": ["flagged", "organic"],
                     "trigger_farmer": [True, False], "lo": [0, 0]})
    tbl = G.measure(con, trig)
    r = {x["pool"]: x for x in tbl.to_pylist()}
    p = r["P"]
    assert p["deep_slot"] == 100 and p["first_slot"] == 10
    assert p["n_all"] == 3 and p["n_pre"] == 2                      # the slot-200 sell is not history
    assert p["net_all"] == 100 and p["gross_all"] == 300
    assert p["flag_all"] is False                                    # |100| < 0.05 x 300 fails: not a farmer over all trades
    assert p["net_post"] == 100 and p["gross_post"] == 100 and p["flag_post"] is False
    q = r["Q"]
    assert q["deep_slot"] == q["first_slot"] == 50 and q["n_all"] == 0 and q["flag_all"] is False and q["flag_post"] is False
    s = G.summarize(tbl)
    assert s["all"]["triggers"] == 2 and s["all"]["deep_at_first_event"] == 1 and s["all"]["with_pre_deep_trade"] == 1
    assert s["all"]["history_trades"] == 3 and s["all"]["history_trades_pre_deep"] == 2
    assert s["all"]["flag_matches_store"] == 1                       # the store said W farms; we found 3 trades, net 100: no
    assert s["flagged"]["triggers"] == 1 and s["organic"]["triggers"] == 1
    md = G.report(s)
    assert "| all | 2 | 2 | 50.0% | 50.0% | 3 | 66.7% | 50.0% | 0.0% | 50.0% | 0.0% | 0.0% (0) | 0 |" in md


def test_flag_flips_when_pre_deep_trades_carry_the_round_trip():
    SOL = G.SOL
    rows = [("P", "W", 10, "buy", 85 * SOL, True, 100), ("P", "W", 20, "sell", 90 * SOL, False, 100),
            ("P", "W", 120, "buy", 170 * SOL, True, 1), ("P", "W", 130, "sell", 171 * SOL, False, 1)]
    con = _con(rows)
    trig = pa.table({"pool": ["P"], "t": [200], "trader": ["W"], "farm": ["flagged"], "trigger_farmer": [True], "lo": [0]})
    r = G.measure(con, trig).to_pylist()[0]
    assert r["flag_all"] is True and r["flag_post"] is True          # 2 post trades, net 0: still a farmer
    trig2 = pa.table({"pool": ["P"], "t": [125], "trader": ["W"], "farm": ["flagged"], "trigger_farmer": [True], "lo": [0]})
    r2 = G.measure(con, trig2).to_pylist()[0]
    assert r2["flag_all"] is True and r2["flag_post"] is False       # only one post trade: the flag is lost without the pre-deep ones
    s = G.summarize(G.measure(con, trig2))
    assert s["all"]["flips"] == 1 and s["all"]["flips_lost_flag"] == 1


def test_window_uses_the_clock():
    class C:
        def slots(self, ms, at):
            return ms // 300
    trig = pa.table({"pool": ["P"], "t": [1_000_000]})
    assert G.with_window(trig, C()).column("lo").to_pylist() == [1_000_000 - G.DAY_MS // 300]
