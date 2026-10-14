"""Blind-spot audit of one extracted OF1 chunk: what the chain shows next to what our trade rows keep.

Descriptive only (observed facts on the chunk's own tables; nothing is inferred about ownership).
Needs the events and tx_ctx; the transfer sections run only when the chunk has the #239 tables
(token_transfers, token_deltas, sol_funding).

  python -m checks.blindspots <chunk dir> [--slots LO:HI] [--out file.json]

Memory: LAB_DUCKDB_MEM (default 1GB), LAB_DUCKDB_THREADS (default 2), spill under LAB_SPILL_DIR. A
full 216,000-slot chunk does not fit in 1 GB: audit a window with --slots (or run on the NAS).

Sections:
- trades: events per venue, side and instruction variant. Token side as the store keeps it
  (backtest/store.py _pool_trades): a reversed pool (base WSOL) trades the token as its quote, a
  BuyEvent there sells the token, and the exact-in buy's token amount is quote_amount_in (fees
  included), the exact-out buy's user_quote_amount_in.
- actor: the event's `user` against the transaction's fee payer.
- route: the top-level program the trade instruction ran under (pump / pump_amm direct, or a router or bot).
- tx_venues: the DEX set of the transaction (tx_ctx.venues).
- token_delta: for one-event transactions whose user is the fee payer, does the signer's net token
  delta in the mint (tx_ctx.signer_token_deltas) equal the event's token amount? Per venue, side and
  variant; a mismatch means tokens moved that the trade row does not show.
- round_trips, round_trip_volume: transactions that buy and sell the same mint, and their share of
  each venue's SOL volume (wash volume inflates any flow or volume feature built on trade rows).
- failed: failed transactions (failed_txs), their fees, and how concentrated they are by fee payer
  (failure share against the payer's landed pump transactions in tx_ctx).
- transfers (#239 tables): pump-mint token moves outside a trade instruction, owner-to-owner moves
  (the bundle-distribution signature), balance changes of owners other than the fee payer in trade
  transactions, and SOL funding.
"""

import argparse
import json
import os
import sys

import duckdb

from backtest.data import POOL_EXACT_OUT

WSOL = "So11111111111111111111111111111111111111112"
TOP = 12


def connect():
    con = duckdb.connect(config={"memory_limit": os.environ.get("LAB_DUCKDB_MEM", "1GB"),
                                 "threads": int(os.environ.get("LAB_DUCKDB_THREADS", "2"))})
    con.execute("SET TimeZone='UTC'")
    if os.environ.get("LAB_SPILL_DIR"):
        con.execute(f"SET temp_directory='{os.environ['LAB_SPILL_DIR']}'")
    return con


def has(chunk, table):
    d = os.path.join(chunk, table)
    return os.path.isdir(d) and any(f.endswith(".parquet") for f in os.listdir(d))


def rows(con, sql):
    """The query's result as a list of dicts (column name -> value; HUGEINT as int)."""
    cur = con.execute(sql)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, (int(v) if isinstance(v, int) else v for v in r))) for r in cur.fetchall()]


def load(con, chunk, lo, hi):
    """Temp tables trades, ctx and (when present) failed, tt, td, sf for slots [lo, hi)."""
    where = f"WHERE slot >= {lo} AND slot < {hi}"

    def pq(t):
        return f"(SELECT * FROM read_parquet('{chunk}/{t}/*.parquet', union_by_name = true) {where})"

    exact_out = ", ".join(f"'{d}'" for d in POOL_EXACT_OUT)
    rev = f"ix_base_mint = '{WSOL}'"
    quote_in = f"CASE WHEN parent_ix_disc IN ({exact_out}) THEN user_quote_amount_in ELSE quote_amount_in END"
    con.execute(f"""CREATE OR REPLACE TEMP TABLE trades AS
        SELECT 'curve' AS venue, CASE WHEN is_buy THEN 'buy' ELSE 'sell' END AS side, parent_ix_name AS variant,
               slot, tx_index, outer_program, fee_payer, "user", mint, token_amount::HUGEINT AS tok, sol_amount::HUGEINT AS sol
        FROM {pq('pump/TradeEvent')}
        UNION ALL
        SELECT CASE WHEN {rev} THEN 'pool_reversed' ELSE 'pool' END, CASE WHEN {rev} THEN 'sell' ELSE 'buy' END, parent_ix_name,
               slot, tx_index, outer_program, fee_payer, "user",
               CASE WHEN {rev} THEN ix_quote_mint ELSE ix_base_mint END,
               (CASE WHEN {rev} THEN {quote_in} ELSE base_amount_out END)::HUGEINT,
               (CASE WHEN {rev} THEN base_amount_out WHEN ix_quote_mint = '{WSOL}' THEN {quote_in} END)::HUGEINT
        FROM {pq('pump_amm/BuyEvent')}
        UNION ALL
        SELECT CASE WHEN {rev} THEN 'pool_reversed' ELSE 'pool' END, CASE WHEN {rev} THEN 'buy' ELSE 'sell' END, parent_ix_name,
               slot, tx_index, outer_program, fee_payer, "user",
               CASE WHEN {rev} THEN ix_quote_mint ELSE ix_base_mint END,
               (CASE WHEN {rev} THEN user_quote_amount_out ELSE base_amount_in END)::HUGEINT,
               (CASE WHEN {rev} THEN base_amount_in WHEN ix_quote_mint = '{WSOL}' THEN user_quote_amount_out END)::HUGEINT
        FROM {pq('pump_amm/SellEvent')}""")
    con.execute(f"CREATE OR REPLACE TEMP TABLE ctx AS SELECT * FROM {pq('tx_ctx')}")
    present = {"failed_txs": has(chunk, "failed_txs")}
    if present["failed_txs"]:
        con.execute(f"CREATE OR REPLACE TEMP TABLE failed AS SELECT fee_payer, tx_fee, priority_fee, ix_program FROM {pq('failed_txs')}")
    present["transfers"] = all(has(chunk, t) for t in ("token_transfers", "token_deltas", "sol_funding"))
    if present["transfers"]:
        con.execute(f"CREATE OR REPLACE TEMP TABLE tt AS SELECT * FROM {pq('token_transfers')} WHERE NOT failed")
        con.execute(f"CREATE OR REPLACE TEMP TABLE td AS SELECT *, TRY_CAST(delta_raw AS HUGEINT) AS d FROM {pq('token_deltas')} WHERE NOT failed")
        con.execute(f"CREATE OR REPLACE TEMP TABLE sf AS SELECT * FROM {pq('sol_funding')} WHERE NOT failed")
    return present


def audit(chunk, lo=0, hi=2**63 - 1):
    con = connect()
    present = load(con, chunk, lo, hi)
    r = {"chunk": os.path.abspath(chunk), "slots": rows(con, "SELECT min(slot) AS lo, max(slot) AS hi, count(DISTINCT slot) AS n FROM ctx")[0],
         "tables": present}
    r["trades"] = rows(con, """
        SELECT venue, side, variant, count(*) AS n, sum(sol) / 1e9 AS sol FROM trades GROUP BY ALL ORDER BY venue, side, n DESC""")
    r["actor"] = rows(con, """
        SELECT venue, count(*) AS n, avg(("user" = fee_payer)::INT) AS user_is_fee_payer,
               sum(sol) FILTER (WHERE "user" <> fee_payer) / 1e9 AS sol_user_not_payer
        FROM trades GROUP BY 1 ORDER BY 1""")
    r["route"] = rows(con, f"""
        SELECT venue, outer_program, n, n / sum(n) OVER (PARTITION BY venue) AS share FROM (
            SELECT venue, coalesce(outer_program, '?') AS outer_program, count(*) AS n FROM trades GROUP BY 1, 2)
        QUALIFY row_number() OVER (PARTITION BY venue ORDER BY n DESC) <= {TOP} ORDER BY venue, n DESC""")
    r["tx_venues"] = rows(con, f"""
        SELECT venue, venues, n, n / sum(n) OVER (PARTITION BY venue) AS share FROM (
            SELECT t.venue, coalesce(c.venues, '?') AS venues, count(*) AS n FROM trades t LEFT JOIN ctx c USING (slot, tx_index) GROUP BY 1, 2)
        QUALIFY row_number() OVER (PARTITION BY venue ORDER BY n DESC) <= {TOP} ORDER BY venue, n DESC""")
    con.execute("""CREATE OR REPLACE TEMP TABLE per_tx AS
        SELECT slot, tx_index, mint, any_value(venue) AS venue, any_value(side) AS side, any_value(variant) AS variant,
               any_value(fee_payer) AS fee_payer, count(*) AS n_ev, bool_and("user" = fee_payer) AS user_is_payer,
               sum(CASE WHEN side = 'buy' THEN tok ELSE -tok END) AS ev_tok,
               bool_or(side = 'buy') AND bool_or(side = 'sell') AS buy_and_sell
        FROM trades GROUP BY 1, 2, 3""")
    con.execute("""CREATE OR REPLACE TEMP TABLE deltas AS
        SELECT slot, tx_index, split_part(x, ':', 1) AS mint, TRY_CAST(split_part(x, ':', 2) AS HUGEINT) AS delta
        FROM ctx, unnest(string_split(coalesce(signer_token_deltas, ''), ';')) AS u(x) WHERE x <> ''""")
    r["token_delta"] = rows(con, """
        SELECT p.venue, p.side, p.variant, count(*) AS n,
               avg((d.delta IS NULL)::INT) AS no_delta, avg(coalesce(d.delta = p.ev_tok, false)::INT) AS equal,
               avg((d.delta IS NOT NULL AND d.delta <> p.ev_tok)::INT) AS differs
        FROM per_tx p LEFT JOIN deltas d USING (slot, tx_index, mint)
        WHERE p.n_ev = 1 AND p.user_is_payer AND p.ev_tok <> 0 GROUP BY ALL ORDER BY p.venue, p.side, n DESC""")
    r["token_delta_examples"] = rows(con, """
        SELECT p.slot, p.tx_index, p.mint, p.venue, p.side, p.variant, p.ev_tok::VARCHAR AS event_tokens, d.delta::VARCHAR AS signer_delta
        FROM per_tx p JOIN deltas d USING (slot, tx_index, mint)
        WHERE p.n_ev = 1 AND p.user_is_payer AND d.delta <> p.ev_tok ORDER BY p.slot, p.tx_index LIMIT 8""")
    r["round_trips"] = rows(con, """
        SELECT count(*) AS tx_mint, count(DISTINCT fee_payer) AS payers,
               (SELECT count(*) FROM per_tx WHERE n_ev = 2) AS two_event_tx_mint FROM per_tx WHERE buy_and_sell""")[0]
    r["round_trip_volume"] = rows(con, """
        SELECT t.venue, count(*) AS legs, sum(t.sol) / 1e9 AS sol, sum(t.sol) FILTER (WHERE p.buy_and_sell) / 1e9 AS sol_round_trip,
               sum(t.sol) FILTER (WHERE p.buy_and_sell) / sum(t.sol) AS share
        FROM trades t JOIN per_tx p USING (slot, tx_index, mint) GROUP BY 1 ORDER BY 1""")
    r["landed"] = rows(con, """
        SELECT count(*) FILTER (WHERE NOT failed) AS n, sum(tx_fee) FILTER (WHERE NOT failed) / 1e9 AS fees_sol,
               sum(jito_tip) FILTER (WHERE NOT failed) / 1e9 AS tips_sol, count(DISTINCT fee_payer) AS payers FROM ctx""")[0]
    if present["failed_txs"]:
        r["failed"] = rows(con, """
            SELECT count(*) AS n, sum(tx_fee) / 1e9 AS fees_sol, sum(priority_fee) / 1e9 AS priority_sol,
                   count(DISTINCT fee_payer) AS payers FROM failed""")[0]
        r["failed_by_payer"] = rows(con, """
            WITH f AS (SELECT fee_payer, count(*) AS f, sum(tx_fee) AS fee FROM failed GROUP BY 1),
                 l AS (SELECT fee_payer, count(*) AS l FROM ctx WHERE NOT failed GROUP BY 1),
                 p AS (SELECT fee_payer, coalesce(l.l, 0) AS l, coalesce(f.f, 0) AS f, coalesce(f.fee, 0) AS fee
                       FROM f FULL JOIN l USING (fee_payer))
            SELECT CASE WHEN l = 0 THEN 'only failed' WHEN f = 0 THEN 'never failed' WHEN f / (l + f) < 0.5 THEN '< 50 %'
                        WHEN f / (l + f) < 0.9 THEN '50-90 %' ELSE '>= 90 %' END AS failure_share,
                   count(*) AS payers, sum(l) AS landed, sum(f) AS failed, sum(fee) / 1e9 AS failed_fees_sol
            FROM p GROUP BY 1 ORDER BY 1""")
    if present["transfers"]:
        r["transfers_by_place"] = rows(con, """
            SELECT in_trade, has_pump_event, count(*) AS n, count(DISTINCT mint) AS mints,
                   count(*) FILTER (WHERE source_owner <> dest_owner) AS owner_changes FROM tt GROUP BY 1, 2 ORDER BY 1, 2""")
        r["transfers_outside_trade"] = rows(con, f"""
            SELECT kind, coalesce(outer_program, '?') AS outer_program, count(*) AS n, count(DISTINCT mint) AS mints,
                   count(*) FILTER (WHERE source_owner <> dest_owner) AS owner_changes
            FROM tt WHERE NOT in_trade GROUP BY 1, 2 ORDER BY n DESC LIMIT {TOP}""")
        r["owner_to_owner"] = rows(con, """
            WITH traders AS (SELECT DISTINCT "user" AS w FROM trades)
            SELECT count(*) AS n, count(DISTINCT mint) AS mints, count(DISTINCT source_owner) AS senders,
                   count(DISTINCT dest_owner) AS receivers,
                   count(DISTINCT dest_owner) FILTER (WHERE dest_owner IN (SELECT w FROM traders)) AS receivers_who_trade
            FROM tt WHERE NOT in_trade AND source_owner <> dest_owner AND NOT owner_unresolved""")[0]
        r["fan_out_examples"] = rows(con, """
            SELECT slot, tx_index, mint, source_owner, count(DISTINCT dest_owner) AS receivers, sum(amount_raw)::VARCHAR AS tokens
            FROM tt WHERE NOT in_trade AND source_owner <> dest_owner AND NOT owner_unresolved
            GROUP BY 1, 2, 3, 4 HAVING count(DISTINCT dest_owner) >= 3 ORDER BY receivers DESC, slot LIMIT 8""")
        r["holders_in_trade_tx"] = rows(con, """
            SELECT count(*) AS n, count(*) FILTER (WHERE owner <> fee_payer) AS not_fee_payer,
                   count(*) FILTER (WHERE owner <> fee_payer AND d > 0) AS received,
                   count(*) FILTER (WHERE owner <> fee_payer AND d < 0) AS sent
            FROM td WHERE NOT is_vault AND has_pump_event_same_mint""")[0]
        r["sol_funding"] = rows(con, """
            WITH traders AS (SELECT DISTINCT "user" AS w FROM trades UNION SELECT DISTINCT fee_payer FROM trades)
            SELECT kind, count(*) AS n, sum(lamports) / 1e9 AS sol, count(DISTINCT from_account) AS funders,
                   count(DISTINCT to_account) AS funded,
                   count(DISTINCT to_account) FILTER (WHERE to_account IN (SELECT w FROM traders)) AS funded_who_trade
            FROM sf GROUP BY 1 ORDER BY n DESC""")
    return r


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("chunk")
    ap.add_argument("--slots", help="LO:HI, half-open")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    lo, hi = (int(x) for x in a.slots.split(":")) if a.slots else (0, 2**63 - 1)
    r = audit(a.chunk, lo, hi)
    text = json.dumps(r, indent=1, default=str)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text if not a.out else f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
