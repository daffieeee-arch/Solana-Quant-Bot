"""A2 — what do the new transfer tables (#239) show that our trade rows miss?

Descriptive audit of one extracted chunk with token_transfers, token_deltas and sol_funding.
  - pump-mint token transfers inside vs outside a pump trade instruction, by outer program and kind;
  - transfers between two different owners outside a trade (the bundle-distribution signature);
  - token holders whose balance changed in a trade transaction but who are not the fee payer;
  - SOL funding: transfers to fresh wallets and how many of them trade pump in the same chunk.
Observed facts only.

Usage: python a2_transfers.py <chunk dir> <out.json>
"""

import json
import os
import sys

import duckdb

chunk, out = sys.argv[1], sys.argv[2]
con = duckdb.connect(config={"memory_limit": os.environ.get("LAB_DUCKDB_MEM", "1GB"), "threads": 2})
pq = lambda t: f"read_parquet('{chunk}/{t}/*.parquet', union_by_name=true)"  # noqa: E731
q = lambda s: con.execute(s).fetchall()  # noqa: E731
con.execute(f"CREATE TEMP TABLE tt AS SELECT * FROM {pq('token_transfers')} WHERE NOT failed")
con.execute(f"CREATE TEMP TABLE td AS SELECT *, TRY_CAST(delta_raw AS HUGEINT) AS d FROM {pq('token_deltas')} WHERE NOT failed")
con.execute(f"CREATE TEMP TABLE sf AS SELECT * FROM {pq('sol_funding')} WHERE NOT failed")
con.execute(f"""CREATE TEMP TABLE traders AS
    SELECT DISTINCT "user" AS w FROM {pq('pump/TradeEvent')}
    UNION SELECT DISTINCT "user" FROM {pq('pump_amm/BuyEvent')}
    UNION SELECT DISTINCT "user" FROM {pq('pump_amm/SellEvent')}""")

res = {"chunk": chunk}
res["transfers_by_place"] = q("""
SELECT in_trade, has_pump_event, count(*) AS n, count(DISTINCT mint) AS mints,
       count(*) FILTER (WHERE source_owner <> dest_owner) AS owner_changes
FROM tt GROUP BY 1, 2 ORDER BY 1, 2""")
res["outside_trade_by_outer_program"] = q("""
SELECT coalesce(outer_program, '?'), kind, count(*) AS n, count(DISTINCT mint) AS mints,
       count(*) FILTER (WHERE source_owner <> dest_owner) AS owner_changes
FROM tt WHERE NOT in_trade GROUP BY 1, 2 ORDER BY n DESC LIMIT 15""")
# Owner-to-owner moves outside a trade: wallet A hands tokens to wallet B (B can later sell what it never bought).
res["owner_to_owner_outside_trade"] = q("""
SELECT count(*) AS n, count(DISTINCT mint) AS mints, count(DISTINCT source_owner) AS senders,
       count(DISTINCT dest_owner) AS receivers,
       count(DISTINCT dest_owner) FILTER (WHERE dest_owner IN (SELECT w FROM traders)) AS receivers_who_trade
FROM tt WHERE NOT in_trade AND source_owner <> dest_owner AND NOT owner_unresolved""")
res["fan_out_examples"] = q("""
SELECT slot, tx_index, mint, source_owner, count(DISTINCT dest_owner) AS receivers, sum(amount_raw)::VARCHAR AS tokens
FROM tt WHERE NOT in_trade AND source_owner <> dest_owner
GROUP BY 1, 2, 3, 4 HAVING count(DISTINCT dest_owner) >= 3 ORDER BY receivers DESC LIMIT 8""")
# Balance changes in trade transactions for owners other than the fee payer (excluding pool / curve vaults).
res["non_payer_holders_in_trade_tx"] = q("""
SELECT has_pump_event_same_mint, count(*) AS n, count(*) FILTER (WHERE owner <> fee_payer) AS not_fee_payer,
       count(*) FILTER (WHERE owner <> fee_payer AND d > 0) AS received, count(*) FILTER (WHERE owner <> fee_payer AND d < 0) AS sent
FROM td WHERE NOT is_vault GROUP BY 1 ORDER BY 1""")
res["sol_funding"] = q("""
SELECT kind, count(*) AS n, sum(lamports)/1e9 AS sol, count(DISTINCT from_account) AS funders, count(DISTINCT to_account) AS funded,
       count(DISTINCT to_account) FILTER (WHERE to_account IN (SELECT w FROM traders)) AS funded_who_trade
FROM sf GROUP BY 1 ORDER BY n DESC""")
res["top_funders"] = q("""
SELECT from_account, count(DISTINCT to_account) AS wallets, sum(lamports)/1e9 AS sol,
       count(DISTINCT to_account) FILTER (WHERE to_account IN (SELECT w FROM traders)) AS that_trade
FROM sf GROUP BY 1 ORDER BY wallets DESC LIMIT 8""")
json.dump(res, open(out, "w"), indent=1, default=str)
print(json.dumps(res, indent=1, default=str)[:7000])
