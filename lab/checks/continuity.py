"""Reserve-continuity check for the lab event dataset.

Trades are put in chain order (slot, tx_index, outer_ix, inner_ix) per bonding curve (mint) and
per PumpSwap pool. The state after event n must equal the state before event n+1. A mismatch
means a missing event, a wrong order, a decoding error, or a reserve change without an event.

pump (bonding curve), TradeEvent reserves are POST-trade. Amounts are curve-side, net of fees.
  The quote_* fields hold the real values for every quote mint (for SOL pairs they equal sol_*).
  buy:  pre_tokens = post + token_amount,  pre_quote = post - quote_amount
  sell: pre_tokens = post - token_amount,  pre_quote = post + quote_amount
  Known exception: trades by the Mayhem program's sol vault reset virtual_quote_reserves without
  an event, so the virtual-quote check skips pairs that involve such a trade.

pump_amm (PumpSwap), Buy/SellEvent reserves are PRE-trade. Prices use the effective quote
  E = pool_quote_token_reserves + virtual_quote_reserves; fees kept in the pool move Q and V in
  opposite directions, so E and the base reserve B are the robust invariants:
  buy:  B' = B - base_amount_out,  E' = E + quote_amount_in_with_lp_fee
  sell: B' = B + base_amount_in,   E' = E - quote_amount_out_without_lp_fee
  deposit / withdraw (assumed pre-operation reserves, reported separately):
        B' = B +/- base amount,    E' = E +/- quote amount

Usage: python continuity.py <chunks_dir> [--report out.json] [--examples N]
<chunks_dir> holds chunk directories (<start>-<end>/) with a `_manifest.json` each; only chunks
with status "complete" are used.
"""

import argparse
import glob
import json
import os
import sys

import duckdb

MAYHEM_AGENT = "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s"


def chunks(root):
    out = []
    for m in sorted(glob.glob(os.path.join(root, "*", "_manifest.json"))):
        with open(m) as f:
            man = json.load(f)
        out.append((man["slot_start"], man["slot_end_exclusive"], man["status"], os.path.dirname(m)))
    return sorted(out)


def files(dirs, table):
    return sorted(f for d in dirs for f in glob.glob(os.path.join(d, table, "*.parquet")))


def lit(paths):
    return "[" + ",".join("'" + p.replace("'", "''") + "'" for p in paths) + "]"


def src(paths):
    # Deduplicate in case a firehose thread restart replayed part of a slot.
    return f"""(SELECT DISTINCT ON (signature, outer_ix, inner_ix) *
                FROM read_parquet({lit(paths)}, union_by_name = true))"""


def pump_check(con, trade_files, examples):
    con.execute(f"""
      CREATE TEMP TABLE pump AS
      WITH t AS (
        SELECT slot, tx_index, outer_ix, inner_ix, signature, mint, is_buy, "user" AS trader,
               token_amount AS t,
               COALESCE(quote_amount, sol_amount) AS q,
               virtual_token_reserves AS vt,
               COALESCE(virtual_quote_reserves, virtual_sol_reserves) AS vq,
               real_token_reserves AS rt,
               COALESCE(real_quote_reserves, real_sol_reserves) AS rq,
               decode_status
        FROM {src(trade_files)}
      )
      SELECT *,
        CASE WHEN is_buy THEN vt + t ELSE vt - t END AS pre_vt,
        CASE WHEN is_buy THEN vq - q ELSE vq + q END AS pre_vq,
        CASE WHEN is_buy THEN rt + t ELSE rt - t END AS pre_rt,
        CASE WHEN is_buy THEN rq - q ELSE rq + q END AS pre_rq,
        LAG(vt) OVER w AS prev_vt, LAG(vq) OVER w AS prev_vq,
        LAG(rt) OVER w AS prev_rt, LAG(rq) OVER w AS prev_rq,
        LAG(trader) OVER w AS prev_trader, LAG(slot) OVER w AS prev_slot,
        ROW_NUMBER() OVER w AS n
      FROM t
      WINDOW w AS (PARTITION BY mint ORDER BY slot, tx_index, outer_ix, inner_ix)
    """)
    r = con.execute(f"""
      SELECT count(*), count(DISTINCT mint), count(*) FILTER (WHERE n > 1),
        count(*) FILTER (WHERE n > 1 AND pre_vt = prev_vt),
        count(*) FILTER (WHERE n > 1 AND pre_rt = prev_rt),
        count(*) FILTER (WHERE n > 1 AND pre_rq = prev_rq),
        count(*) FILTER (WHERE n > 1 AND trader <> '{MAYHEM_AGENT}' AND prev_trader <> '{MAYHEM_AGENT}'),
        count(*) FILTER (WHERE n > 1 AND trader <> '{MAYHEM_AGENT}' AND prev_trader <> '{MAYHEM_AGENT}' AND pre_vq = prev_vq),
        count(*) FILTER (WHERE n > 1 AND pre_vt = prev_vt AND pre_rt = prev_rt AND pre_rq = prev_rq)
      FROM pump""").fetchone()
    keys = ["trades", "mints", "pairs", "virtual_token_ok", "real_token_ok", "real_quote_ok",
            "pairs_without_mayhem_agent", "virtual_quote_ok_without_mayhem_agent", "all_three_ok"]
    res = dict(zip(keys, r))
    p = res["pairs"] or 1
    res["rate_all_three"] = res["all_three_ok"] / p
    res["rate_virtual_quote"] = res["virtual_quote_ok_without_mayhem_agent"] / (res["pairs_without_mayhem_agent"] or 1)
    res["decode_status"] = dict(con.execute("SELECT decode_status, count(*) FROM pump GROUP BY 1").fetchall())
    cols = ["mint", "slot", "prev_slot", "signature", "is_buy", "pre_vt", "prev_vt", "pre_rt", "prev_rt", "pre_rq", "prev_rq"]
    res["bad_examples"] = [dict(zip(cols, row)) for row in con.execute(f"""
      SELECT {', '.join(cols)} FROM pump
      WHERE n > 1 AND NOT (pre_vt = prev_vt AND pre_rt = prev_rt AND pre_rq = prev_rq)
      ORDER BY slot LIMIT {examples}""").fetchall()]
    return res


def amm_check(con, dirs, examples):
    buy, sell = files(dirs, "pump_amm/BuyEvent"), files(dirs, "pump_amm/SellEvent")
    dep, wd = files(dirs, "pump_amm/DepositEvent"), files(dirs, "pump_amm/WithdrawEvent")
    parts = []
    eff = "pool_quote_token_reserves + COALESCE(TRY_CAST(virtual_quote_reserves AS HUGEINT), 0)"
    if buy:
        parts.append(f"""SELECT 'buy' AS kind, slot, tx_index, outer_ix, inner_ix, signature, pool,
            pool_base_token_reserves::HUGEINT AS b, ({eff})::HUGEINT AS e,
            (pool_base_token_reserves - base_amount_out)::HUGEINT AS b2,
            ({eff} + quote_amount_in_with_lp_fee)::HUGEINT AS e2 FROM {src(buy)}""")
    if sell:
        parts.append(f"""SELECT 'sell', slot, tx_index, outer_ix, inner_ix, signature, pool,
            pool_base_token_reserves, {eff},
            pool_base_token_reserves + base_amount_in,
            {eff} - quote_amount_out_without_lp_fee FROM {src(sell)}""")
    if dep:
        parts.append(f"""SELECT 'deposit', slot, tx_index, outer_ix, inner_ix, signature, pool,
            pool_base_token_reserves, pool_quote_token_reserves,
            pool_base_token_reserves + base_amount_in, pool_quote_token_reserves + quote_amount_in FROM {src(dep)}""")
    if wd:
        parts.append(f"""SELECT 'withdraw', slot, tx_index, outer_ix, inner_ix, signature, pool,
            pool_base_token_reserves, pool_quote_token_reserves,
            pool_base_token_reserves - base_amount_out, pool_quote_token_reserves - quote_amount_out FROM {src(wd)}""")
    if not parts:
        return None
    con.execute("CREATE TEMP TABLE amm AS WITH ev AS (" + " UNION ALL ".join(parts) + """)
      SELECT *, LAG(b2) OVER w AS prev_b2, LAG(e2) OVER w AS prev_e2, LAG(kind) OVER w AS prev_kind,
             ROW_NUMBER() OVER w AS n
      FROM ev WINDOW w AS (PARTITION BY pool ORDER BY slot, tx_index, outer_ix, inner_ix)""")
    r = con.execute("""
      SELECT count(*), count(DISTINCT pool), count(*) FILTER (WHERE n > 1),
        count(*) FILTER (WHERE n > 1 AND b = prev_b2),
        count(*) FILTER (WHERE n > 1 AND b = prev_b2 AND e = prev_e2),
        count(*) FILTER (WHERE n > 1 AND kind IN ('buy','sell') AND prev_kind IN ('buy','sell')),
        count(*) FILTER (WHERE n > 1 AND kind IN ('buy','sell') AND prev_kind IN ('buy','sell') AND b = prev_b2 AND e = prev_e2)
      FROM amm""").fetchone()
    keys = ["events", "pools", "pairs", "base_ok", "base_and_effective_quote_ok", "trade_pairs", "trade_pairs_ok"]
    res = dict(zip(keys, r))
    res["rate_all_pairs"] = res["base_and_effective_quote_ok"] / (res["pairs"] or 1)
    res["rate_trade_pairs"] = res["trade_pairs_ok"] / (res["trade_pairs"] or 1)
    res["bad_by_kind"] = {f"{k}<-{pk}": c for k, pk, c in con.execute("""
      SELECT kind, prev_kind, count(*) FROM amm WHERE n > 1 AND NOT (b = prev_b2 AND e = prev_e2)
      GROUP BY 1, 2 ORDER BY 3 DESC""").fetchall()}
    cols = ["pool", "slot", "kind", "prev_kind", "signature", "b", "prev_b2", "e", "prev_e2"]
    res["bad_examples"] = [dict(zip(cols, row)) for row in con.execute(f"""
      SELECT {', '.join(cols)} FROM amm WHERE n > 1 AND NOT (b = prev_b2 AND e = prev_e2)
      ORDER BY slot LIMIT {examples}""").fetchall()]
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--report")
    ap.add_argument("--examples", type=int, default=5)
    a = ap.parse_args()

    all_chunks = chunks(a.root)
    usable = [c for c in all_chunks if c[2] == "complete"]
    report = {
        "chunks": [{"start": s, "end": e, "status": st} for s, e, st, _ in all_chunks],
        "gaps_between_complete_chunks": [
            (usable[i][1], usable[i + 1][0]) for i in range(len(usable) - 1) if usable[i][1] != usable[i + 1][0]
        ],
    }
    dirs = [c[3] for c in usable]
    con = duckdb.connect()
    con.execute("SET threads TO 4")
    con.execute("SET memory_limit = '6GB'")

    trades = files(dirs, "pump/TradeEvent")
    if trades:
        report["pump"] = pump_check(con, trades, a.examples)
    amm = amm_check(con, dirs, a.examples)
    if amm:
        report["pump_amm"] = amm
    anomalies = files(dirs, "anomalies")
    if anomalies:
        report["anomalies"] = {f"{p}/{k}/{e}": c for p, k, e, c in con.execute(f"""
          SELECT program, kind, event, count(*) FROM read_parquet({lit(anomalies)}) GROUP BY ALL ORDER BY 4 DESC""").fetchall()}

    out = json.dumps(report, indent=2, default=str)
    if a.report:
        with open(a.report, "w") as f:
            f.write(out)
    print(out)


if __name__ == "__main__":
    sys.exit(main())
