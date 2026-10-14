"""Checks of the extractor's tx_ctx table (MEV phase 0) on one extracted chunk, against the chunk's
own tables. Bounded memory: the chunk is checked in slot buckets of BUCKET_SLOTS (a full 216,000-slot
chunk OOM'd at 1 GB in one pass); DuckDB takes LAB_DUCKDB_MEM (default 1GB), LAB_DUCKDB_THREADS
(default 2) and spills under LAB_SPILL_DIR (at most LAB_SPILL_MAX) when set.

  python -m checks.tx_ctx_smoke <chunk dir>     # prints JSON; exit 1 when a check fails

Checks:
- one row per transaction, (slot, tx_index) unique: every successful transaction with a pump or
  PumpSwap event and every failed_txs row, nothing else;
- fee payer, fee, compute units, priority fee and Jito tip equal the events' context columns;
- `programs` names pump / pump_amm wherever the transaction has their events, and jupiter_v6 at the
  top level wherever an event's outer program is Jupiter v6;
- direct pump.fun buys by the fee payer (outer program pump, one TradeEvent, no PumpSwap event):
  where the fee payer has a token delta for the mint, it equals the event's token_amount (>= 99%); where
  the tokens go to the fee payer's canonical ATA (ix_accounts.associated_user), that delta exists
  (>= 99%). A buy into a token account the fee payer does not own (pump does not require its ATA:
  1054 smoke, 42 buys of 3 wallets) has no fee-payer delta; it is counted, not failed.
Descriptive (no pass/fail): venues, transactions with two or more DEXes and a positive SOL + WSOL net
for the fee payer (arbitrage candidates), the SOL delta of direct buys against the event amounts,
and the table's size per 1,000 slots with the projection per 216,000-slot chunk.
"""

import glob
import heapq
import json
import os
import shutil
import sys
from collections import Counter

import duckdb

from backtest.token_meta_rpc import b58decode, find_program_address

PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
JUPITER_V6 = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"
WSOL = "So11111111111111111111111111111111111111112"
CHUNK_SLOTS = 216_000
CTX_COLS = ("fee_payer", "tx_fee", "cu_consumed", "priority_fee", "jito_tip")
ATA_SAMPLE = 50_000  # direct buys classified by destination (a Python PDA each, ~0.5 ms)
BUCKET_SLOTS = 5_000  # slots per bucket (~1 M tx_ctx rows at the 1054 smoke's density)
ATA_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
TOKEN_PROGRAMS = ("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")


def atas(owner, mint):
    """The owner's associated token accounts for the mint under either token program."""
    return {find_program_address([b58decode(owner), b58decode(tp), b58decode(mint)], ATA_PROGRAM)[0] for tp in TOKEN_PROGRAMS}


def src(d, table):
    files = sorted(glob.glob(os.path.join(d, table, "*.parquet")))
    return f"read_parquet({files!r}, union_by_name = true)" if files else None


def delta(mint_col):
    """The fee payer's token delta for a mint from signer_token_deltas ("mint:delta;...")."""
    return (f"TRY_CAST(split_part(list_filter(string_split(COALESCE(c.signer_token_deltas, ''), ';'), "
            f"x -> starts_with(x, {mint_col} || ':'))[1], ':', 2) AS HUGEINT)")


def connect():
    """DuckDB with LAB_DUCKDB_MEM (default 1 GB) and LAB_DUCKDB_THREADS (default 2); spill under
    LAB_SPILL_DIR (at most LAB_SPILL_MAX, default 10 GB) when set. Returns (connection, spill dir or None)."""
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{os.environ.get('LAB_DUCKDB_MEM', '1GB')}'; "
                f"SET threads TO {int(os.environ.get('LAB_DUCKDB_THREADS', '2'))}; SET preserve_insertion_order = false")
    spill = os.environ.get("LAB_SPILL_DIR")
    if spill:
        spill = os.path.join(spill, f"tx-ctx-smoke-{os.getpid()}")
        os.makedirs(spill, exist_ok=True)
        con.execute(f"SET temp_directory = '{spill}'; SET max_temp_directory_size = '{os.environ.get('LAB_SPILL_MAX', '10GB')}'")
    return con, spill


def check(d, bucket_slots=None, log=lambda m: None):
    """The checks, one slot bucket at a time (bucket_slots, default LAB_TXCTX_BUCKET_SLOTS or
    BUCKET_SLOTS): every check is keyed by (slot, tx_index), so the buckets' counts add up, and a
    full chunk (216,000 slots) needs no more memory than the 1,000-slot smoke. The ATA sample is the
    chunk's ATA_SAMPLE smallest hashes, the SOL rest quantiles come from all direct buys."""
    ctx, failed = src(d, "tx_ctx"), src(d, "failed_txs")
    if not ctx:
        return {"dir": d, "ok": False, "error": "no tx_ctx table (extractor before tx_ctx?)"}
    with open(os.path.join(d, "_manifest.json")) as fh:
        m = json.load(fh)
    lo, hi = m.get("slot_start", 0), m.get("slot_end_exclusive", 0)
    step = int(bucket_slots or os.environ.get("LAB_TXCTX_BUCKET_SLOTS", BUCKET_SLOTS))
    buckets = [(a, min(a + step, hi)) for a in range(lo, hi, step)]
    con, spill = connect()
    ev_tables = sorted({os.path.relpath(os.path.dirname(f), d) for f in glob.glob(os.path.join(d, "pump*", "*", "*.parquet"))})
    trade = src(d, "pump/TradeEvent")
    accts = "t.ix_accounts" if trade and "ix_accounts" in {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {trade}").fetchall()} \
        else "NULL::VARCHAR"
    con.execute("CREATE TEMP TABLE rest (v HUGEINT)")
    rows, mism, labels = Counter(), Counter(), Counter()
    direct, venues, arb = Counter(), Counter(), Counter()
    sample = []  # (hash, mint, buyer, dest, without delta): the ATA_SAMPLE smallest hashes so far

    for i, (a, b) in enumerate(buckets):
        w = f"slot >= {a} AND slot < {b}"
        con.execute(f"CREATE OR REPLACE TEMP TABLE c AS SELECT * FROM {ctx} WHERE {w}")
        ev = " UNION ALL ".join(
            f"SELECT slot, tx_index, '{t.split('/')[0]}' AS program, outer_program, {', '.join(CTX_COLS)} FROM {src(d, t)} WHERE {w}"
            for t in ev_tables)
        con.execute(f"CREATE OR REPLACE TEMP TABLE ev AS {ev}")
        con.execute(f"CREATE OR REPLACE TEMP TABLE f AS SELECT slot, tx_index FROM {failed} WHERE {w}" if failed
                    else "CREATE OR REPLACE TEMP TABLE f (slot UBIGINT, tx_index UBIGINT)")

        n, keys = con.execute("SELECT count(*), count(DISTINCT (slot, tx_index)) FROM c").fetchone()
        rows.update({"tx_ctx": n, "unique_keys": keys,
                     "event_txs": con.execute("SELECT count(DISTINCT (slot, tx_index)) FROM ev").fetchone()[0],
                     "failed_txs": con.execute("SELECT count(*) FROM f").fetchone()[0],
                     "event_txs_missing": con.execute("""SELECT count(*) FROM (SELECT DISTINCT slot, tx_index FROM ev) e
                                ANTI JOIN (SELECT * FROM c WHERE NOT failed) c USING (slot, tx_index)""").fetchone()[0],
                     "failed_missing": con.execute(
                         "SELECT count(*) FROM f ANTI JOIN (SELECT * FROM c WHERE failed) c USING (slot, tx_index)").fetchone()[0],
                     "extra": con.execute("""SELECT (SELECT count(*) FROM (SELECT * FROM c WHERE NOT failed) c ANTI JOIN ev USING (slot, tx_index))
                                + (SELECT count(*) FROM (SELECT * FROM c WHERE failed) c ANTI JOIN f USING (slot, tx_index))""").fetchone()[0]})

        mism.update({col: con.execute(f"""SELECT count(*) FROM (SELECT DISTINCT slot, tx_index, {col} FROM ev) e JOIN c USING (slot, tx_index)
                                          WHERE e.{col} IS DISTINCT FROM c.{col}""").fetchone()[0] for col in CTX_COLS})

        lab = con.execute(f"""
            WITH t AS (SELECT slot, tx_index, bool_or(program = 'pump') AS has_pump, bool_or(program = 'pump_amm') AS has_amm,
                              bool_or(outer_program = '{JUPITER_V6}') AS via_jup FROM ev GROUP BY slot, tx_index)
            SELECT count(*) FILTER (WHERE has_pump AND NOT regexp_matches(c.programs, '(^|;)[0-9]+:pump(\\*[0-9]+)?(;|$)')),
                   count(*) FILTER (WHERE has_amm AND NOT regexp_matches(c.programs, '(^|;)[0-9]+:pump_amm(\\*[0-9]+)?(;|$)')),
                   count(*) FILTER (WHERE via_jup AND NOT regexp_matches(c.programs, '(^|;)1:jupiter_v6(\\*[0-9]+)?(;|$)')),
                   count(*) FILTER (WHERE via_jup)
            FROM t JOIN c USING (slot, tx_index)""").fetchone()
        labels.update(dict(zip(("pump_events_without_pump", "pump_amm_events_without_pump_amm", "jupiter_outer_without_jupiter_v6",
                                "jupiter_outer_txs"), lab)))

        if trade:
            con.execute(f"""CREATE OR REPLACE TEMP TABLE direct AS
                WITH tr AS (SELECT * FROM {trade} WHERE {w}),
                     single AS (SELECT slot, tx_index FROM tr GROUP BY slot, tx_index HAVING count(*) = 1),
                     no_amm AS (SELECT slot, tx_index FROM ev GROUP BY slot, tx_index
                                HAVING count(*) FILTER (WHERE program = 'pump_amm') = 0)
                SELECT t.slot, t.tx_index, t.mint, t."user" AS buyer, t.token_amount, COALESCE(t.quote_amount, t.sol_amount) AS q,
                       t.fee + t.creator_fee AS fees, c.tx_fee, c.jito_tip, c.signer_sol_delta, {delta('t.mint')} AS d,
                       json_extract_string({accts}, '$.associated_user') AS dest
                FROM tr t JOIN c USING (slot, tx_index) SEMI JOIN single USING (slot, tx_index) SEMI JOIN no_amm USING (slot, tx_index)
                WHERE t.is_buy AND t.outer_program = '{PUMP}' AND t."user" = c.fee_payer AND NOT c.failed""")
            nd, with_d, agree = con.execute("SELECT count(*), count(d), count(*) FILTER (WHERE d = token_amount) FROM direct").fetchone()
            direct.update({"n": nd, "with_fee_payer_delta": with_d, "delta_equals_token_amount": agree})
            got = con.execute(f"SELECT hash(slot, tx_index) AS h, mint, buyer, dest, d IS NULL FROM direct ORDER BY h LIMIT {ATA_SAMPLE}").fetchall()
            sample = heapq.nsmallest(ATA_SAMPLE, sample + got, key=lambda r: r[0])
            con.execute("""INSERT INTO rest SELECT -signer_sol_delta - q - fees - tx_fee - jito_tip FROM direct
                           WHERE signer_sol_delta IS NOT NULL""")

        venues.update(dict(con.execute("SELECT venues, count(*) FROM c WHERE NOT failed GROUP BY 1").fetchall()))
        arb.update(dict(zip(("n_dex_ge_2", "with_positive_sol_plus_wsol_net"), con.execute(f"""
            SELECT count(*), count(*) FILTER (WHERE signer_sol_delta + COALESCE({delta(f"'{WSOL}'")}, 0) > 0)
            FROM c WHERE NOT failed AND n_dex >= 2""").fetchone())))
        log(f"bucket {i + 1}/{len(buckets)} [{a}, {b}): {n} tx_ctx rows")

    out, ok = {"dir": d, "buckets": len(buckets), "bucket_slots": step}, True
    out["rows"] = {k: rows[k] for k in ("tx_ctx", "unique_keys", "event_txs", "failed_txs", "event_txs_missing", "failed_missing",
                                        "extra")}
    out["rows"]["ok"] = rows_ok = rows["tx_ctx"] == rows["unique_keys"] == rows["event_txs"] + rows["failed_txs"] \
        and rows["event_txs_missing"] == rows["failed_missing"] == rows["extra"] == 0
    ok &= rows_ok

    out["context_mismatches"] = {col: mism[col] for col in CTX_COLS}
    ok &= not any(mism.values())

    out["programs"] = {k: labels[k] for k in ("pump_events_without_pump", "pump_amm_events_without_pump_amm",
                                              "jupiter_outer_without_jupiter_v6", "jupiter_outer_txs")}
    ok &= labels["pump_events_without_pump"] == labels["pump_amm_events_without_pump_amm"] == \
        labels["jupiter_outer_without_jupiter_v6"] == 0

    if trade:
        share = direct["delta_equals_token_amount"] / direct["with_fee_payer_delta"] if direct["with_fee_payer_delta"] else None
        # Destination: the fee payer's canonical ATA (either token program) or another token account.
        cls = {"canonical_ata": [0, 0], "other_account": [0, 0], "no_ix_accounts": [0, 0]}  # [n, without delta]
        for _, mint, buyer, dest, nodelta in sample:
            k = ("no_ix_accounts" if dest is None else
                 "canonical_ata" if dest in atas(buyer, mint) else "other_account")
            cls[k][0] += 1
            cls[k][1] += nodelta
        ata_share = (cls["canonical_ata"][0] - cls["canonical_ata"][1]) / cls["canonical_ata"][0] if cls["canonical_ata"][0] else None
        # SOL: -(curve quote + trading fees + tx fee + tip) leaves rent of new accounts and other fees.
        rest = con.execute("SELECT median(v), quantile_cont(v, 0.9) FROM rest").fetchone()
        out["direct_pump_buys"] = {
            "n": direct["n"], "with_fee_payer_delta": direct["with_fee_payer_delta"],
            "delta_equals_token_amount": direct["delta_equals_token_amount"], "share": share,
            "by_destination_sampled": {k: {"n": v[0], "without_delta": v[1]} for k, v in cls.items()},
            "canonical_ata_with_delta_share": ata_share, "sol_rest_lamports_median_p90": rest}
        ok &= direct["n"] > 0 and share is not None and share >= 0.99 and ata_share is not None and ata_share >= 0.99

    out["venues_top"] = [[v, n] for v, n in sorted(venues.items(), key=lambda kv: -kv[1])[:12]]
    out["arbitrage_candidates"] = {k: arb[k] for k in ("n_dex_ge_2", "with_positive_sol_plus_wsol_net")}

    slots = hi - lo
    size = sum(os.path.getsize(f) for f in glob.glob(os.path.join(d, "tx_ctx", "*.parquet")))
    total = sum(os.path.getsize(f) for f in glob.glob(os.path.join(d, "**", "*.parquet"), recursive=True))
    out["size"] = {"slots": slots, "tx_ctx_bytes": size, "chunk_bytes": total, "share": round(size / total, 4) if total else None,
                   "tx_ctx_mb_per_1000_slots": round(size / slots * 1000 / 1e6, 2) if slots else None,
                   "projected_gb_per_216000_slot_chunk": round(size / slots * CHUNK_SLOTS / 1e9, 2) if slots else None}
    out["ok"] = bool(ok)
    con.close()
    if spill:
        shutil.rmtree(spill, ignore_errors=True)
    return out


if __name__ == "__main__":
    r = check(sys.argv[1], log=lambda m: print(m, file=sys.stderr, flush=True))
    print(json.dumps(r, indent=1, default=str))
    sys.exit(0 if r["ok"] else 1)
