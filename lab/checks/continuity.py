"""Reserve-continuity check for the lab event dataset.

For every pump.fun bonding curve (per mint) and every PumpSwap pool, trades are put in chain
order (slot, tx_index, outer_ix, inner_ix). The reserves after trade n must equal the reserves
before trade n+1. A mismatch means a missing event, a wrong order, a decoding error or a
reserve change that is not a trade (which we then want to know about).

  pump:     TradeEvent carries post-trade virtual reserves; pre-trade reserves follow from the
            trade amounts (buy: sol in, tokens out; sell: tokens in, sol out).
  pump_amm: Buy/Sell/Deposit/WithdrawEvent carry pre-trade pool reserves; post-trade reserves
            follow from the amounts (see POST_* below).

Usage: python continuity.py <dataset_root> [--report out.json]
<dataset_root> holds chunk directories (<start>-<end>/) with a `_manifest.json` each.
"""

import argparse
import glob
import json
import os
import sys

import duckdb


def complete_chunks(root):
    chunks = []
    for m in sorted(glob.glob(os.path.join(root, "*", "_manifest.json"))):
        with open(m) as f:
            man = json.load(f)
        chunks.append((man["slot_start"], man["slot_end_exclusive"], man["status"], os.path.dirname(m)))
    chunks.sort()
    return chunks


def parquet_list(chunk_dirs, table):
    files = []
    for d in chunk_dirs:
        files += glob.glob(os.path.join(d, table, "*.parquet"))
    return files


PUMP_SQL = """
WITH t AS (
  SELECT DISTINCT ON (signature, outer_ix, inner_ix)
    slot, tx_index, outer_ix, inner_ix, signature, mint, is_buy,
    sol_amount, token_amount, virtual_sol_reserves AS post_sol, virtual_token_reserves AS post_tok,
    quote_mint, quote_amount, virtual_quote_reserves AS post_quote
  FROM read_parquet({files}, union_by_name = true)
), o AS (
  SELECT *,
    CASE WHEN is_buy THEN post_sol - sol_amount ELSE post_sol + sol_amount END AS pre_sol,
    CASE WHEN is_buy THEN post_tok + token_amount ELSE post_tok - token_amount END AS pre_tok,
    LAG(post_sol) OVER w AS prev_post_sol,
    LAG(post_tok) OVER w AS prev_post_tok,
    LAG(slot) OVER w AS prev_slot,
    ROW_NUMBER() OVER w AS n
  FROM t
  WINDOW w AS (PARTITION BY mint ORDER BY slot, tx_index, outer_ix, inner_ix)
)
SELECT * FROM o
"""

# PumpSwap: event reserves are taken before the trade; amounts that move into/out of the pool.
AMM_SQL = """
WITH ev AS (
  SELECT DISTINCT ON (signature, outer_ix, inner_ix) 'buy' AS kind, slot, tx_index, outer_ix, inner_ix, signature, pool,
    pool_base_token_reserves AS pre_base, pool_quote_token_reserves AS pre_quote,
    pool_base_token_reserves - base_amount_out AS post_base,
    pool_quote_token_reserves + quote_amount_in_with_lp_fee AS post_quote
  FROM read_parquet({buy}, union_by_name = true)
  UNION ALL
  SELECT DISTINCT ON (signature, outer_ix, inner_ix) 'sell', slot, tx_index, outer_ix, inner_ix, signature, pool,
    pool_base_token_reserves, pool_quote_token_reserves,
    pool_base_token_reserves + base_amount_in,
    pool_quote_token_reserves - quote_amount_out_without_lp_fee
  FROM read_parquet({sell}, union_by_name = true)
  {liquidity}
), o AS (
  SELECT *,
    LAG(post_base) OVER w AS prev_post_base,
    LAG(post_quote) OVER w AS prev_post_quote,
    LAG(kind) OVER w AS prev_kind,
    ROW_NUMBER() OVER w AS n
  FROM ev
  WINDOW w AS (PARTITION BY pool ORDER BY slot, tx_index, outer_ix, inner_ix)
)
SELECT * FROM o
"""

LIQ_SQL = """
  UNION ALL
  SELECT DISTINCT ON (signature, outer_ix, inner_ix) 'deposit', slot, tx_index, outer_ix, inner_ix, signature, pool,
    pool_base_token_reserves, pool_quote_token_reserves,
    pool_base_token_reserves + base_amount_in, pool_quote_token_reserves + quote_amount_in
  FROM read_parquet({deposit}, union_by_name = true)
"""
WD_SQL = """
  UNION ALL
  SELECT DISTINCT ON (signature, outer_ix, inner_ix) 'withdraw', slot, tx_index, outer_ix, inner_ix, signature, pool,
    pool_base_token_reserves, pool_quote_token_reserves,
    pool_base_token_reserves - base_amount_out, pool_quote_token_reserves - quote_amount_out
  FROM read_parquet({withdraw}, union_by_name = true)
"""


def sql_list(files):
    return "[" + ",".join("'" + f.replace("'", "''") + "'" for f in files) + "]"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--report")
    ap.add_argument("--examples", type=int, default=5)
    a = ap.parse_args()

    chunks = complete_chunks(a.root)
    usable = [c for c in chunks if c[2] == "complete"]
    report = {"chunks": [{"start": s, "end": e, "status": st} for s, e, st, _ in chunks]}
    gaps = [(usable[i][1], usable[i + 1][0]) for i in range(len(usable) - 1) if usable[i][1] != usable[i + 1][0]]
    report["gaps_between_complete_chunks"] = gaps
    dirs = [c[3] for c in usable]
    con = duckdb.connect()
    con.execute("SET threads TO 4; SET memory_limit = '6GB'")

    trade_files = parquet_list(dirs, "pump/TradeEvent")
    if trade_files:
        con.execute("CREATE TEMP TABLE pump AS " + PUMP_SQL.format(files=sql_list(trade_files)))
        r = con.execute("""
          SELECT count(*) AS trades,
                 count(DISTINCT mint) AS mints,
                 count(*) FILTER (WHERE n > 1) AS pairs,
                 count(*) FILTER (WHERE n > 1 AND pre_sol = prev_post_sol AND pre_tok = prev_post_tok) AS pairs_ok,
                 count(*) FILTER (WHERE n > 1 AND NOT (pre_sol = prev_post_sol AND pre_tok = prev_post_tok)) AS pairs_bad,
                 count(*) FILTER (WHERE quote_mint IS NOT NULL AND quote_mint <> 'So11111111111111111111111111111111111111112') AS non_sol_quote_trades
          FROM pump""").fetchone()
        cols = ["trades", "mints", "pairs", "pairs_ok", "pairs_bad", "non_sol_quote_trades"]
        res = dict(zip(cols, r))
        res["continuity_rate"] = (res["pairs_ok"] / res["pairs"]) if res["pairs"] else None
        res["bad_examples"] = [
            dict(zip(["mint", "slot", "prev_slot", "signature", "pre_sol", "prev_post_sol", "pre_tok", "prev_post_tok", "is_buy"], row))
            for row in con.execute(f"""
              SELECT mint, slot, prev_slot, signature, pre_sol, prev_post_sol, pre_tok, prev_post_tok, is_buy FROM pump
              WHERE n > 1 AND NOT (pre_sol = prev_post_sol AND pre_tok = prev_post_tok)
              ORDER BY slot LIMIT {a.examples}""").fetchall()
        ]
        report["pump"] = res

    buy, sell = parquet_list(dirs, "pump_amm/BuyEvent"), parquet_list(dirs, "pump_amm/SellEvent")
    dep, wd = parquet_list(dirs, "pump_amm/DepositEvent"), parquet_list(dirs, "pump_amm/WithdrawEvent")
    if buy and sell:
        liq = (LIQ_SQL.format(deposit=sql_list(dep)) if dep else "") + (WD_SQL.format(withdraw=sql_list(wd)) if wd else "")
        con.execute("CREATE TEMP TABLE amm AS " + AMM_SQL.format(buy=sql_list(buy), sell=sql_list(sell), liquidity=liq))
        r = con.execute("""
          SELECT count(*) AS events, count(DISTINCT pool) AS pools,
                 count(*) FILTER (WHERE n > 1) AS pairs,
                 count(*) FILTER (WHERE n > 1 AND pre_base = prev_post_base AND pre_quote = prev_post_quote) AS pairs_ok,
                 count(*) FILTER (WHERE n > 1 AND pre_base = prev_post_base AND pre_quote <> prev_post_quote) AS quote_only_bad,
                 count(*) FILTER (WHERE n > 1 AND pre_base <> prev_post_base) AS base_bad
          FROM amm""").fetchone()
        res = dict(zip(["events", "pools", "pairs", "pairs_ok", "quote_only_bad", "base_bad"], r))
        res["continuity_rate"] = (res["pairs_ok"] / res["pairs"]) if res["pairs"] else None
        res["bad_examples"] = [
            dict(zip(["pool", "slot", "kind", "prev_kind", "signature", "pre_base", "prev_post_base", "pre_quote", "prev_post_quote"], row))
            for row in con.execute(f"""
              SELECT pool, slot, kind, prev_kind, signature, pre_base, prev_post_base, pre_quote, prev_post_quote FROM amm
              WHERE n > 1 AND NOT (pre_base = prev_post_base AND pre_quote = prev_post_quote)
              ORDER BY slot LIMIT {a.examples}""").fetchall()
        ]
        report["pump_amm"] = res

    out = json.dumps(report, indent=2, default=str)
    if a.report:
        with open(a.report, "w") as f:
            f.write(out)
    print(out)


if __name__ == "__main__":
    sys.exit(main())
