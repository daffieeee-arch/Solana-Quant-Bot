"""Which development store the lab reads, and one view of PumpSwap trades that works on every store version.

Switching stores is the STORE line below. lab/bin/q, lab/analytics/build.py and lab/ui/server.py all read it.
Every reader checks the store's meta (period 'dev', slot_end_exclusive <= hold-out start) before use.
"""
import json
import os

STORE = "store/dev.duckdb"  # relative to LAB_DATA_ROOT; LAB backtest's v2 store is "store/dev2.duckdb"

HOLDOUT_START = 452_304_000
SOL_QUOTES = ("So11111111111111111111111111111111111111112", "11111111111111111111111111111111")
SOL_IN = "(" + ", ".join(f"'{q}'" for q in SOL_QUOTES) + ")"


def path(data_root):
    return os.path.join(data_root, STORE)


def check_meta(meta_json):
    """The store's meta as a dict; SystemExit if it is not development-only."""
    m = json.loads(meta_json)
    if m.get("period") != "dev" or m.get("slot_end_exclusive", HOLDOUT_START + 1) > HOLDOUT_START:
        raise SystemExit(f"store is not development-only: period={m.get('period')!r}, "
                         f"slot_end_exclusive={m.get('slot_end_exclusive')!r}")
    return m


def pool_trades_sql(version, db="dev"):
    """SELECT for one row per PumpSwap token trade, seen from the token's side.

    Columns: pool, slot, tx_index, outer_ix, inner_ix, trader, kind ('buy' = trader buys the token),
    mint (NULL if unknown), pool_create_slot, quote (raw quote amount; lamports for SOL markets),
    sol (lamports, NULL if the market is not SOL-quoted), token_amount (raw),
    price (quote per whole token, before the trade), farmer (store v2 farming flag; false on v1), orientation ('normal' | 'reversed').

    v1: pool.kind is the token side; 'boost' rows duplicate a buy and are left out; non-SOL pools present.
    v2: only SOL markets; token_buy is the token side; a boost is one protocol 'boost' row (left out, no
        trader decision); in 'reversed' pools base is WSOL, so the token price is b/e instead of e/b.
    """
    if version >= 2:
        return f"""
            SELECT p.pool, p.slot, p.tx_index, p.outer_ix, p.inner_ix, p.trader,
                   CASE WHEN p.token_buy THEN 'buy' ELSE 'sell' END AS kind, ps.mint,
                   ps.create_slot AS pool_create_slot, p.sol_amount AS quote, p.sol_amount AS sol,
                   p.token_amount,
                   CASE WHEN ps.orientation = 'reversed' THEN p.b::DOUBLE / NULLIF(p.e::DOUBLE, 0)
                        ELSE p.e::DOUBLE / NULLIF(p.b::DOUBLE, 0) END / 1000 AS price,
                   coalesce(p.farmer, false) AS farmer, ps.orientation
            FROM {db}.pool p JOIN {db}.pools ps USING (pool)
            WHERE p.kind IN ('buy', 'sell')"""
    return f"""
        SELECT p.pool, p.slot, p.tx_index, p.outer_ix, p.inner_ix, p.trader, p.kind, ps.mint,
               ps.create_slot AS pool_create_slot, p.quote_gross AS quote, p.base AS token_amount,
               CASE WHEN ps.quote_mint IN {SOL_IN} THEN p.quote_gross END AS sol,
               p.e::DOUBLE / NULLIF(p.b::DOUBLE, 0) / 1000 AS price,
               false AS farmer, 'normal' AS orientation
        FROM {db}.pool p JOIN {db}.pools ps USING (pool)
        WHERE p.kind IN ('buy', 'sell')"""


def attach(con, data_root, alias="dev"):
    """ATTACH the configured store READ_ONLY, check its meta, create TEMP VIEW pool_trades. Returns meta."""
    p = path(data_root).replace("'", "''")
    con.execute(f"ATTACH '{p}' AS {alias} (READ_ONLY)")
    meta = check_meta(con.execute(f"SELECT json FROM {alias}.meta").fetchone()[0])
    con.execute(f"CREATE OR REPLACE TEMP VIEW pool_trades AS {pool_trades_sql(meta.get('store_version', 1), alias)}")
    return meta
