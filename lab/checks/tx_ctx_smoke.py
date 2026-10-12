"""Checks of the extractor's tx_ctx table (MEV phase 0) on one extracted chunk, against the chunk's
own tables. Light: one chunk, DuckDB with 1 GB.

  python -m checks.tx_ctx_smoke <chunk dir>     # prints JSON; exit 1 when a check fails

Checks:
- one row per transaction, (slot, tx_index) unique: every successful transaction with a pump or
  PumpSwap event and every failed_txs row, nothing else;
- fee payer, fee, compute units, priority fee and Jito tip equal the events' context columns;
- `programs` names pump / pump_amm wherever the transaction has their events, and jupiter_v6 at the
  top level wherever an event's outer program is Jupiter v6;
- direct pump.fun buys by the fee payer (outer program pump, one TradeEvent, no PumpSwap event): the
  fee payer's token delta for the mint equals the event's token_amount (at least 99%).
Descriptive (no pass/fail): venues, transactions with two or more DEXes and a positive SOL + WSOL net
for the fee payer (arbitrage candidates), the SOL delta of direct buys against the event amounts,
and the table's size per 1,000 slots with the projection per 216,000-slot chunk.
"""

import glob
import json
import os
import sys

import duckdb

PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
JUPITER_V6 = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"
WSOL = "So11111111111111111111111111111111111111112"
CHUNK_SLOTS = 216_000
CTX_COLS = ("fee_payer", "tx_fee", "cu_consumed", "priority_fee", "jito_tip")


def src(d, table):
    files = sorted(glob.glob(os.path.join(d, table, "*.parquet")))
    return f"read_parquet({files!r}, union_by_name = true)" if files else None


def delta(mint_col):
    """The fee payer's token delta for a mint from signer_token_deltas ("mint:delta;...")."""
    return (f"TRY_CAST(split_part(list_filter(string_split(COALESCE(c.signer_token_deltas, ''), ';'), "
            f"x -> starts_with(x, {mint_col} || ':'))[1], ':', 2) AS HUGEINT)")


def check(d):
    con = duckdb.connect()
    con.execute("SET memory_limit = '1GB'; SET threads TO 2")
    ctx, failed = src(d, "tx_ctx"), src(d, "failed_txs")
    if not ctx:
        return {"dir": d, "ok": False, "error": "no tx_ctx table (extractor before tx_ctx?)"}
    con.execute(f"CREATE TEMP VIEW c AS SELECT * FROM {ctx}")
    ev_tables = sorted({os.path.relpath(os.path.dirname(f), d) for f in glob.glob(os.path.join(d, "pump*", "*", "*.parquet"))})
    ev = " UNION ALL ".join(
        f"SELECT slot, tx_index, '{t.split('/')[0]}' AS program, outer_program, {', '.join(CTX_COLS)} FROM {src(d, t)}"
        for t in ev_tables)
    con.execute(f"CREATE TEMP TABLE ev AS {ev}")
    con.execute(f"CREATE TEMP TABLE f AS SELECT slot, tx_index FROM {failed}" if failed
                else "CREATE TEMP TABLE f (slot UBIGINT, tx_index UBIGINT)")
    out, ok = {"dir": d}, True

    n, keys = con.execute("SELECT count(*), count(DISTINCT (slot, tx_index)) FROM c").fetchone()
    ev_txs = con.execute("SELECT count(DISTINCT (slot, tx_index)) FROM ev").fetchone()[0]
    n_failed = con.execute("SELECT count(*) FROM f").fetchone()[0]
    missing_ok = con.execute("""SELECT count(*) FROM (SELECT DISTINCT slot, tx_index FROM ev) e
                                ANTI JOIN (SELECT * FROM c WHERE NOT failed) c USING (slot, tx_index)""").fetchone()[0]
    missing_failed = con.execute("SELECT count(*) FROM f ANTI JOIN (SELECT * FROM c WHERE failed) c USING (slot, tx_index)").fetchone()[0]
    extra = con.execute("""SELECT (SELECT count(*) FROM (SELECT * FROM c WHERE NOT failed) c ANTI JOIN ev USING (slot, tx_index))
                                + (SELECT count(*) FROM (SELECT * FROM c WHERE failed) c ANTI JOIN f USING (slot, tx_index))""").fetchone()[0]
    rows_ok = n == keys == ev_txs + n_failed and missing_ok == missing_failed == extra == 0
    out["rows"] = {"tx_ctx": n, "unique_keys": keys, "event_txs": ev_txs, "failed_txs": n_failed, "event_txs_missing": missing_ok,
                   "failed_missing": missing_failed, "extra": extra, "ok": rows_ok}
    ok &= rows_ok

    mism = {col: con.execute(f"""SELECT count(*) FROM (SELECT DISTINCT slot, tx_index, {col} FROM ev) e JOIN c USING (slot, tx_index)
                                 WHERE e.{col} IS DISTINCT FROM c.{col}""").fetchone()[0] for col in CTX_COLS}
    out["context_mismatches"] = mism
    ok &= not any(mism.values())

    labels = con.execute(f"""
        WITH t AS (SELECT slot, tx_index, bool_or(program = 'pump') AS has_pump, bool_or(program = 'pump_amm') AS has_amm,
                          bool_or(outer_program = '{JUPITER_V6}') AS via_jup FROM ev GROUP BY slot, tx_index)
        SELECT count(*) FILTER (WHERE has_pump AND NOT regexp_matches(c.programs, '(^|;)[0-9]+:pump(\\*[0-9]+)?(;|$)')),
               count(*) FILTER (WHERE has_amm AND NOT regexp_matches(c.programs, '(^|;)[0-9]+:pump_amm(\\*[0-9]+)?(;|$)')),
               count(*) FILTER (WHERE via_jup AND NOT regexp_matches(c.programs, '(^|;)1:jupiter_v6(\\*[0-9]+)?(;|$)')),
               count(*) FILTER (WHERE via_jup)
        FROM t JOIN c USING (slot, tx_index)""").fetchone()
    out["programs"] = {"pump_events_without_pump": labels[0], "pump_amm_events_without_pump_amm": labels[1],
                       "jupiter_outer_without_jupiter_v6": labels[2], "jupiter_outer_txs": labels[3]}
    ok &= labels[0] == labels[1] == labels[2] == 0

    trade = src(d, "pump/TradeEvent")
    if trade:
        con.execute(f"""CREATE TEMP TABLE direct AS
            WITH single AS (SELECT slot, tx_index FROM {trade} GROUP BY slot, tx_index HAVING count(*) = 1),
                 no_amm AS (SELECT slot, tx_index FROM ev GROUP BY slot, tx_index
                            HAVING count(*) FILTER (WHERE program = 'pump_amm') = 0)
            SELECT t.slot, t.tx_index, t.mint, t.token_amount, COALESCE(t.quote_amount, t.sol_amount) AS q,
                   t.fee + t.creator_fee AS fees, c.tx_fee, c.jito_tip, c.signer_sol_delta, {delta('t.mint')} AS d
            FROM {trade} t JOIN c USING (slot, tx_index) SEMI JOIN single USING (slot, tx_index) SEMI JOIN no_amm USING (slot, tx_index)
            WHERE t.is_buy AND t.outer_program = '{PUMP}' AND t."user" = c.fee_payer AND NOT c.failed""")
        nd, agree = con.execute("SELECT count(*), count(*) FILTER (WHERE d = token_amount) FROM direct").fetchone()
        share = agree / nd if nd else None
        # SOL: -(curve quote + trading fees + tx fee + tip) leaves rent of new accounts and other fees.
        rest = con.execute("""SELECT median(-signer_sol_delta - q - fees - tx_fee - jito_tip),
                                     quantile_cont(-signer_sol_delta - q - fees - tx_fee - jito_tip, 0.9)
                              FROM direct WHERE signer_sol_delta IS NOT NULL""").fetchone()
        out["direct_pump_buys"] = {"n": nd, "token_delta_equals_token_amount": agree, "share": share,
                                   "sol_rest_lamports_median_p90": rest}
        ok &= nd > 0 and share is not None and share >= 0.99

    out["venues_top"] = con.execute("SELECT venues, count(*) FROM c WHERE NOT failed GROUP BY 1 ORDER BY 2 DESC LIMIT 12").fetchall()
    out["arbitrage_candidates"] = dict(zip(("n_dex_ge_2", "with_positive_sol_plus_wsol_net"), con.execute(f"""
        SELECT count(*), count(*) FILTER (WHERE signer_sol_delta + COALESCE({delta(f"'{WSOL}'")}, 0) > 0)
        FROM c WHERE NOT failed AND n_dex >= 2""").fetchone()))

    with open(os.path.join(d, "_manifest.json")) as fh:
        m = json.load(fh)
    slots = m.get("slot_end_exclusive", 0) - m.get("slot_start", 0)
    size = sum(os.path.getsize(f) for f in glob.glob(os.path.join(d, "tx_ctx", "*.parquet")))
    total = sum(os.path.getsize(f) for f in glob.glob(os.path.join(d, "**", "*.parquet"), recursive=True))
    out["size"] = {"slots": slots, "tx_ctx_bytes": size, "chunk_bytes": total, "share": round(size / total, 4) if total else None,
                   "tx_ctx_mb_per_1000_slots": round(size / slots * 1000 / 1e6, 2) if slots else None,
                   "projected_gb_per_216000_slot_chunk": round(size / slots * CHUNK_SLOTS / 1e9, 2) if slots else None}
    out["ok"] = bool(ok)
    return out


if __name__ == "__main__":
    r = check(sys.argv[1])
    print(json.dumps(r, indent=1, default=str))
    sys.exit(0 if r["ok"] else 1)
