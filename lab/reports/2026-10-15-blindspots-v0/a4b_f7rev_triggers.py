"""A4b — F7 pool_reversed triggers on dev2 (days 1-18, f7rev-1033-1046): is the trigger sell a wash leg?

For each distinct trigger (pool, t, trigger_trader) of the f7rev run: the trigger trader's legs in that pool at
slot t, whether the same transaction also buys the token (same-tx round trip), whether the trader buys the
token back within 50 slots, and the pool's share of same-tx round-trip volume on the trigger's day.
Descriptive; dev2 only (no hold-out, no forward data).

Usage: python a4b_f7rev_triggers.py <parts dir> <store> <out.json>
"""
import json
import os
import sys

import duckdb

parts, store, out = sys.argv[1:4]
con = duckdb.connect(config={"memory_limit": os.environ.get("LAB_DUCKDB_MEM", "2GB"), "threads": 2})
if os.environ.get("LAB_SPILL_DIR"):
    con.execute(f"SET temp_directory='{os.environ['LAB_SPILL_DIR']}'")
con.execute(f"ATTACH '{store}' AS s (READ_ONLY)")
q = lambda s: con.execute(s).fetchall()  # noqa: E731
con.execute(f"""CREATE TEMP TABLE trig AS
SELECT DISTINCT pool, t, trigger_trader, day, trigger_farmer FROM read_parquet('{parts}/*.parquet') WHERE segment = 'pool_reversed'""")
con.execute("CREATE TEMP TABLE tp AS SELECT DISTINCT pool FROM trig")
lo, hi = q("SELECT min(t), max(t) + 50 FROM trig")[0]
# All buy/sell legs of trigger pools in the trigger slot range (only the pools that ever triggered).
con.execute(f"""CREATE TEMP TABLE legs AS
SELECT pool, slot, tx_index, trader, token_buy, sol_amount FROM s.pool
WHERE kind IN ('buy', 'sell') AND slot BETWEEN {lo} AND {hi} AND pool IN (SELECT pool FROM tp)""")
con.execute("""CREATE TEMP TABLE txp AS SELECT pool, slot, tx_index, bool_or(token_buy) AND bool_or(NOT token_buy) AS rt, sum(sol_amount) AS sol
               FROM legs GROUP BY 1, 2, 3""")
con.execute("""CREATE TEMP TABLE tl AS
SELECT tr.pool, tr.t, tr.trigger_trader, tr.day, tr.trigger_farmer,
       bool_or(p.rt) AS rt,
       max(l.sol_amount) AS sell_sol
FROM trig tr JOIN legs l ON l.pool = tr.pool AND l.slot = tr.t AND l.trader = tr.trigger_trader AND NOT l.token_buy
JOIN txp p ON p.pool = l.pool AND p.slot = l.slot AND p.tx_index = l.tx_index
GROUP BY ALL""")
res = {"parts": parts, "store": store}
res["triggers"] = q("SELECT (SELECT count(*) FROM trig), count(*), count(DISTINCT pool) FROM tl")
res["trigger_leg_in_same_tx_round_trip"] = q("SELECT avg(rt::INT), count(*) FILTER (WHERE rt) FROM tl")
res["buys_back_within_50_slots"] = q("""
SELECT avg((EXISTS (SELECT 1 FROM legs b WHERE b.pool = tl.pool AND b.trader = tl.trigger_trader AND b.token_buy
               AND b.slot > tl.t AND b.slot <= tl.t + 50))::INT) FROM tl WHERE NOT rt""")
res["by_farmer_flag"] = q("SELECT trigger_farmer, count(*), avg(rt::INT) FROM tl GROUP BY 1 ORDER BY 1")
res["by_day"] = q("SELECT day, count(*), avg(rt::INT) FROM tl GROUP BY 1 ORDER BY 1")
res["pool_rt_volume_share"] = q("""
SELECT sum(sol) FILTER (WHERE rt) / sum(sol), count(DISTINCT pool) FILTER (WHERE rt) , count(DISTINCT pool) FROM txp""")
res["top_pools"] = q("""SELECT pool, count(*) AS n, avg(rt::INT) AS rt FROM tl GROUP BY 1 ORDER BY n DESC LIMIT 10""")
json.dump(res, open(out, "w"), indent=1, default=str)
print(json.dumps(res, indent=1, default=str))
