"""A4 — are large token sells in PumpSwap pools real exits or wash legs?

For trigger-sized token sells (>= 3 SOL and >= 4 % of the pool's SOL depth, as F7 sizes them; depth >= 150 SOL),
per orientation: is the sell part of a same-transaction round trip (the same tx also buys the mint), does the
same user (or fee payer) buy the mint back within 50 slots, and what share of the pool's SOL volume in the chunk
comes from same-tx round trips. Observed facts on one chunk.

Usage: python a4_wash.py <chunk dir> <out.json>
"""
import json
import sys

import duckdb

chunk, out = sys.argv[1], sys.argv[2]
W = "So11111111111111111111111111111111111111112"
con = duckdb.connect(config={"memory_limit": "1GB", "threads": 2})
pq = lambda t: f"read_parquet('{chunk}/{t}/*.parquet', union_by_name=true)"  # noqa: E731
q = lambda s: con.execute(s).fetchall()  # noqa: E731
# Token-side legs of pools with a SOL side: (orientation, pool, slot, tx, user, payer, token_buy, sol, sol_depth before).
con.execute(f"""CREATE TEMP TABLE legs AS
SELECT CASE WHEN ix_base_mint = '{W}' THEN 'reversed' ELSE 'normal' END AS o, pool, slot, tx_index, outer_ix, "user", fee_payer,
       ix_base_mint = '{W}' AS rev, CASE WHEN ix_base_mint = '{W}' THEN false ELSE true END AS token_buy,
       (CASE WHEN ix_base_mint = '{W}' THEN base_amount_out ELSE quote_amount_in END)::DOUBLE / 1e9 AS sol,
       (CASE WHEN ix_base_mint = '{W}' THEN pool_base_token_reserves ELSE pool_quote_token_reserves END)::DOUBLE / 1e9 AS depth
FROM {pq('pump_amm/BuyEvent')} WHERE ix_base_mint = '{W}' OR ix_quote_mint = '{W}'
UNION ALL
SELECT CASE WHEN ix_base_mint = '{W}' THEN 'reversed' ELSE 'normal' END, pool, slot, tx_index, outer_ix, "user", fee_payer,
       ix_base_mint = '{W}', CASE WHEN ix_base_mint = '{W}' THEN true ELSE false END,
       (CASE WHEN ix_base_mint = '{W}' THEN base_amount_in ELSE quote_amount_out END)::DOUBLE / 1e9,
       (CASE WHEN ix_base_mint = '{W}' THEN pool_base_token_reserves ELSE pool_quote_token_reserves END)::DOUBLE / 1e9
FROM {pq('pump_amm/SellEvent')} WHERE ix_base_mint = '{W}' OR ix_quote_mint = '{W}'""")
con.execute("""CREATE TEMP TABLE txp AS SELECT pool, slot, tx_index, bool_or(token_buy) AND bool_or(NOT token_buy) AS rt
               FROM legs GROUP BY 1, 2, 3""")
res = {"chunk": chunk}
res["volume_share_same_tx_round_trip"] = q("""
SELECT o, count(*) AS legs, sum(sol) AS sol, sum(sol) FILTER (WHERE rt) AS sol_rt, sum(sol) FILTER (WHERE rt) / sum(sol) AS share_rt
FROM legs JOIN txp USING (pool, slot, tx_index) GROUP BY 1 ORDER BY 1""")
con.execute("""CREATE TEMP TABLE trig AS
SELECT l.*, t.rt FROM legs l JOIN txp t USING (pool, slot, tx_index)
WHERE NOT l.token_buy AND l.sol >= 3 AND l.sol >= 0.04 * l.depth AND l.depth >= 150""")
res["trigger_sized_sells"] = q("""
SELECT o, count(*) AS n, count(DISTINCT pool) AS pools,
       avg(rt::INT) AS in_same_tx_round_trip,
       avg((EXISTS (SELECT 1 FROM legs b WHERE b.pool = trig.pool AND b.token_buy AND (b."user" = trig."user" OR b.fee_payer = trig.fee_payer)
                    AND b.slot BETWEEN trig.slot AND trig.slot + 50 AND (b.slot, b.tx_index) <> (trig.slot, trig.tx_index)))::INT) AS same_wallet_buys_back_50_slots,
       avg((trig."user" <> trig.fee_payer)::INT) AS user_not_payer
FROM trig GROUP BY 1 ORDER BY 1""")
res["top_pools_trigger_sells"] = q("""
SELECT o, pool, count(*) AS n, avg(rt::INT) AS rt, round(avg(sol), 2) AS avg_sol, round(avg(depth), 1) AS depth
FROM trig GROUP BY 1, 2 ORDER BY n DESC LIMIT 10""")
json.dump(res, open(out, "w"), indent=1, default=str)
print(json.dumps(res, indent=1, default=str))
