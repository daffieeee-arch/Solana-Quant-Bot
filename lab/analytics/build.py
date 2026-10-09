"""Build analytics.duckdb (overview tables for the data explorer) from the development store.

    # heavy: run only via the heavy-job lock (see AGENTS.md)
    systemd-run --user --scope -p MemoryMax=3G -p CPUQuota=200% -- \
      flock -n "$LAB_DATA_ROOT/locks/heavy.lock" "$LAB_PY" lab/analytics/build.py
    # add a finished development tournament run as strategy_* tables:
    #   ... lab/analytics/build.py --tournament "$LAB_DATA_ROOT/backtests/<run>"
    # light smoke run on a few hours of slots, to a scratch file:
    "$LAB_PY" lab/analytics/build.py --slots 450144000:450180000 --out /tmp/x.duckdb --memory 900MB

Build-and-swap: everything is written to `<out>.building`, validated, then moved over `<out>` with
os.replace, so readers (lab/bin/q, the web app) never see a half-built file. The build aborts if any
slot column holds a hold-out slot (>= 452,304,000); the only exception is the fixed protocol-marker
labels in market_events.
"""
import argparse
import json
import os
import subprocess
import time

import sys

import duckdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strategies  # noqa: E402

DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
HOLDOUT_START = 452_304_000
SOL_QUOTES = ("So11111111111111111111111111111111111111112", "11111111111111111111111111111111")
LARGE_TRADE_SOL = 25
# Fixed protocol markers: labels only (slot numbers), never hold-out content.
PROTOCOL_MARKERS = [
    (452_520_000, "Eerste fee-sweep (stuk 452.520.000–452.736.000)"),
    (453_800_004, "Eerste v3-curvetrades / PumpSwap v2"),
]
SOL_IN = "(" + ", ".join(f"'{q}'" for q in SOL_QUOTES) + ")"
# Spot price in quote per whole token: quote raw / 1e9 per token raw / 1e6.
CURVE_PRICE = "(vq::DOUBLE / NULLIF(vt::DOUBLE, 0) / 1000)"
POOL_PRICE = "(e::DOUBLE / NULLIF(b::DOUBLE, 0) / 1000)"
ORDER = "(slot, tx_index, outer_ix, inner_ix)"


def tables(lo, hi):
    w_curve = f"c.slot >= {lo} AND c.slot < {hi}"
    w_pool = f"p.slot >= {lo} AND p.slot < {hi} AND p.kind IN ('buy', 'sell')"
    return {
        "slot_time": f"""
            SELECT slot, block_time, to_timestamp(block_time)::TIMESTAMP AS ts
            FROM dev.blocks WHERE slot >= {lo} AND slot < {hi}""",

        "data_coverage": f"""
            WITH b AS (SELECT slot // 432000 AS epoch, min(slot) AS first_slot, max(slot) AS last_slot,
                              count(*) AS blocks, min(ts) AS first_time, max(ts) AS last_time
                       FROM slot_time GROUP BY 1),
                 m AS (SELECT create_slot // 432000 AS epoch, count(*) AS tokens_created FROM dev.mints
                       WHERE create_slot >= {lo} AND create_slot < {hi} GROUP BY 1),
                 g AS (SELECT complete_slot // 432000 AS epoch, count(*) AS graduations FROM dev.mints
                       WHERE complete_slot >= {lo} AND complete_slot < {hi} GROUP BY 1),
                 c AS (SELECT slot // 432000 AS epoch, count(*) AS curve_trades FROM dev.curve c
                       WHERE {w_curve} GROUP BY 1),
                 p AS (SELECT slot // 432000 AS epoch, count(*) AS pool_trades FROM dev.pool p
                       WHERE {w_pool} GROUP BY 1)
            SELECT epoch, first_slot, last_slot, blocks, (last_slot - first_slot + 1) - blocks AS skipped_slots,
                   first_time, last_time, coalesce(tokens_created, 0) AS tokens_created,
                   coalesce(graduations, 0) AS graduations, coalesce(curve_trades, 0) AS curve_trades,
                   coalesce(pool_trades, 0) AS pool_trades
            FROM b LEFT JOIN m USING (epoch) LEFT JOIN g USING (epoch) LEFT JOIN c USING (epoch)
                   LEFT JOIN p USING (epoch)
            ORDER BY epoch""",

        "token_summary": f"""
            WITH cv AS (
                SELECT mint, count(*) AS curve_trades, count(*) FILTER (WHERE is_buy) AS curve_buys,
                       count(*) FILTER (WHERE NOT is_buy) AS curve_sells,
                       coalesce(sum(q) FILTER (WHERE is_buy), 0)::DOUBLE / 1e9 AS curve_buy_quote,
                       coalesce(sum(q) FILTER (WHERE NOT is_buy), 0)::DOUBLE / 1e9 AS curve_sell_quote,
                       count(DISTINCT trader) AS curve_traders, min(slot) AS curve_first_slot,
                       max(slot) AS curve_last_slot, max({CURVE_PRICE}) AS curve_max_price,
                       arg_max({CURVE_PRICE}, {ORDER}) AS curve_last_price
                FROM dev.curve c WHERE {w_curve} GROUP BY 1),
                 pv AS (
                SELECT ps.mint, count(*) AS pool_trades,
                       sum(p.quote_gross)::DOUBLE / 1e9 AS pool_volume_quote,
                       count(DISTINCT p.trader) AS pool_traders, min(p.slot) AS pool_first_slot,
                       max(p.slot) AS pool_last_slot, max({POOL_PRICE}) AS pool_max_price,
                       arg_max({POOL_PRICE}, (p.slot, p.tx_index, p.outer_ix, p.inner_ix)) AS pool_last_price
                FROM dev.pool p JOIN dev.pools ps USING (pool) WHERE {w_pool} GROUP BY 1)
            SELECT m.mint, m.name, m.symbol, m.creator, m.create_slot, st.ts AS create_time,
                   m.quote_mint, m.quote_mint IN {SOL_IN} AS sol_quote, m.mayhem, m.cashback, m.holder_reward,
                   m.supply::DOUBLE / 1e6 AS supply_tokens,
                   m.complete_slot, m.complete_slot IS NOT NULL AS graduated,
                   coalesce(m.complete_slot = m.create_slot, false) AS grad_in_create_slot,
                   m.migrate_slot, m.pool,
                   coalesce(cv.curve_trades, 0) AS curve_trades, coalesce(cv.curve_buys, 0) AS curve_buys,
                   coalesce(cv.curve_sells, 0) AS curve_sells,
                   coalesce(cv.curve_buy_quote, 0) AS curve_buy_quote,
                   coalesce(cv.curve_sell_quote, 0) AS curve_sell_quote,
                   coalesce(cv.curve_traders, 0) AS curve_traders, cv.curve_first_slot, cv.curve_last_slot,
                   cv.curve_max_price, cv.curve_last_price,
                   cv.curve_max_price * m.supply::DOUBLE / 1e6 AS curve_max_mcap,
                   coalesce(pv.pool_trades, 0) AS pool_trades,
                   coalesce(pv.pool_volume_quote, 0) AS pool_volume_quote,
                   coalesce(pv.pool_traders, 0) AS pool_traders, pv.pool_first_slot, pv.pool_last_slot,
                   pv.pool_max_price, pv.pool_last_price,
                   pv.pool_max_price * m.supply::DOUBLE / 1e6 AS pool_max_mcap,
                   coalesce(cv.curve_buy_quote, 0) + coalesce(cv.curve_sell_quote, 0)
                     + coalesce(pv.pool_volume_quote, 0) AS total_volume_quote
            FROM dev.mints m
            JOIN slot_time st ON st.slot = m.create_slot
            LEFT JOIN cv USING (mint) LEFT JOIN pv USING (mint)
            WHERE m.create_slot >= {lo} AND m.create_slot < {hi}
            ORDER BY m.create_slot""",

        "token_candles_1m": f"""
            WITH t AS (
                SELECT 'curve' AS venue, c.mint, c.slot, c.tx_index, c.outer_ix, c.inner_ix, c.is_buy,
                       c.q::DOUBLE / 1e9 AS quote, {CURVE_PRICE} AS price
                FROM dev.curve c WHERE {w_curve}
                UNION ALL
                SELECT 'pool', ps.mint, p.slot, p.tx_index, p.outer_ix, p.inner_ix, p.kind = 'buy',
                       p.quote_gross::DOUBLE / 1e9, {POOL_PRICE}
                FROM dev.pool p JOIN dev.pools ps USING (pool) WHERE {w_pool})
            SELECT t.mint, t.venue, time_bucket(INTERVAL 1 minute, st.ts) AS minute,
                   arg_min(price, {ORDER}) AS open, max(price) AS high, min(price) AS low,
                   arg_max(price, {ORDER}) AS close,
                   count(*) AS trades, count(*) FILTER (WHERE is_buy) AS buys,
                   sum(quote) AS volume_quote,
                   coalesce(sum(quote) FILTER (WHERE is_buy), 0) AS buy_quote,
                   min(t.slot) AS first_slot, max(t.slot) AS last_slot
            FROM t JOIN slot_time st USING (slot)
            GROUP BY 1, 2, 3
            ORDER BY 1, 3, 2""",

        "graduations": f"""
            WITH before AS (
                SELECT c.mint, count(*) AS curve_trades, count(DISTINCT c.trader) AS curve_traders,
                       max(c.rq)::DOUBLE / 1e9 AS quote_raised
                FROM dev.curve c JOIN dev.mints m USING (mint)
                WHERE {w_curve} AND c.slot <= m.complete_slot GROUP BY 1),
                 after AS (
                SELECT ps.mint, min(p.slot) AS pool_first_trade_slot,
                       count(*) FILTER (WHERE p.slot < ps.create_slot + 13468) AS pool_trades_first_hour,
                       coalesce(sum(p.quote_gross) FILTER (WHERE p.slot < ps.create_slot + 13468), 0)::DOUBLE / 1e9
                         AS pool_volume_first_hour
                FROM dev.pool p JOIN dev.pools ps USING (pool) WHERE {w_pool} GROUP BY 1)
            SELECT m.mint, m.name, m.symbol, m.creator, m.quote_mint, m.quote_mint IN {SOL_IN} AS sol_quote,
                   m.mayhem, m.cashback, m.holder_reward,
                   m.create_slot, sc.ts AS create_time, m.complete_slot, sg.ts AS complete_time,
                   m.migrate_slot, m.pool,
                   m.complete_slot = m.create_slot AS in_create_slot,
                   m.complete_slot - m.create_slot AS slots_to_graduate,
                   sg.block_time - sc.block_time AS seconds_to_graduate,
                   coalesce(b.curve_trades, 0) AS curve_trades_before,
                   coalesce(b.curve_traders, 0) AS curve_traders_before, b.quote_raised,
                   a.pool_first_trade_slot, coalesce(a.pool_trades_first_hour, 0) AS pool_trades_first_hour,
                   coalesce(a.pool_volume_first_hour, 0) AS pool_volume_first_hour
            FROM dev.mints m
            JOIN slot_time sc ON sc.slot = m.create_slot
            JOIN slot_time sg ON sg.slot = m.complete_slot
            LEFT JOIN before b USING (mint) LEFT JOIN after a USING (mint)
            WHERE m.complete_slot >= {lo} AND m.complete_slot < {hi}
            ORDER BY m.complete_slot""",

        "wallet_summary": f"""
            WITH t AS (
                SELECT c.trader, 'curve' AS venue, c.mint, c.slot, c.is_buy,
                       CASE WHEN m.quote_mint IN {SOL_IN} THEN c.q::DOUBLE / 1e9 END AS sol
                FROM dev.curve c JOIN dev.mints m USING (mint) WHERE {w_curve}
                UNION ALL
                SELECT p.trader, 'pool', ps.mint, p.slot, p.kind = 'buy',
                       CASE WHEN ps.quote_mint IN {SOL_IN} THEN p.quote_gross::DOUBLE / 1e9 END
                FROM dev.pool p JOIN dev.pools ps USING (pool) WHERE {w_pool}),
                 created AS (SELECT creator AS trader, count(*) AS tokens_created FROM dev.mints
                             WHERE create_slot >= {lo} AND create_slot < {hi} GROUP BY 1)
            SELECT trader AS wallet,
                   count(*) FILTER (WHERE venue = 'curve') AS curve_trades,
                   count(*) FILTER (WHERE venue = 'pool') AS pool_trades,
                   count(DISTINCT mint) AS tokens_traded,
                   coalesce(sum(sol) FILTER (WHERE is_buy), 0) AS buy_sol,
                   coalesce(sum(sol) FILTER (WHERE NOT is_buy), 0) AS sell_sol,
                   coalesce(sum(sol) FILTER (WHERE NOT is_buy), 0) - coalesce(sum(sol) FILTER (WHERE is_buy), 0)
                     AS net_sol_flow,
                   min(slot) AS first_slot, max(slot) AS last_slot,
                   coalesce(any_value(cr.tokens_created), 0) AS tokens_created
            FROM t LEFT JOIN created cr USING (trader)
            GROUP BY trader""",

        "market_events": f"""
            SELECT * FROM (
                SELECT m.complete_slot AS slot, NULL::UBIGINT AS tx_index, st.ts AS time, 'graduation' AS kind,
                       m.mint, m.symbol, m.name, 'curve' AS venue, NULL AS side, NULL::DOUBLE AS sol,
                       NULL AS wallet,
                       CASE WHEN m.complete_slot = m.create_slot THEN 'in de aanmaak-slot' END AS note
                FROM dev.mints m JOIN slot_time st ON st.slot = m.complete_slot
                WHERE m.complete_slot >= {lo} AND m.complete_slot < {hi}
                UNION ALL
                SELECT c.slot, c.tx_index, st.ts,
                       CASE WHEN c.is_buy THEN 'large_buy' ELSE 'large_sell' END, c.mint, m.symbol, m.name,
                       'curve', CASE WHEN c.is_buy THEN 'buy' ELSE 'sell' END, c.q::DOUBLE / 1e9, c.trader, NULL
                FROM dev.curve c JOIN dev.mints m USING (mint) JOIN slot_time st ON st.slot = c.slot
                WHERE {w_curve} AND m.quote_mint IN {SOL_IN} AND c.q >= {LARGE_TRADE_SOL} * 1e9
                UNION ALL
                SELECT p.slot, p.tx_index, st.ts,
                       CASE WHEN p.kind = 'buy' THEN 'large_buy' ELSE 'large_sell' END, ps.mint, m.symbol, m.name,
                       'pool', p.kind, p.quote_gross::DOUBLE / 1e9, p.trader, NULL
                FROM dev.pool p JOIN dev.pools ps USING (pool) LEFT JOIN dev.mints m ON m.mint = ps.mint
                JOIN slot_time st ON st.slot = p.slot
                WHERE {w_pool} AND ps.quote_mint IN {SOL_IN} AND p.quote_gross >= {LARGE_TRADE_SOL} * 1e9
                UNION ALL
                SELECT * FROM (VALUES {", ".join(
                    f"({s}::UBIGINT, NULL::UBIGINT, NULL::TIMESTAMP, 'protocol_marker', NULL, NULL, NULL, NULL, "
                    f"NULL, NULL::DOUBLE, NULL, '{label}')" for s, label in PROTOCOL_MARKERS)})
            ) ORDER BY slot, tx_index NULLS FIRST""",
    }


DICTIONARY = {
    "meta": {"json": "Bouwgegevens: bron, slotbereik, tijdstip, git-commit, aantallen per tabel."},
    "slot_time": {
        "slot": "Slotnummer.", "block_time": "Bloktijd (unix-seconden).", "ts": "Bloktijd als UTC-tijdstempel."},
    "data_coverage": {
        "epoch": "Epoch = slot // 432.000 (ongeveer 32 uur).", "first_slot": "Eerste slot met een blok.",
        "last_slot": "Laatste slot met een blok.", "blocks": "Aantal geproduceerde blokken.",
        "skipped_slots": "Slots zonder blok (overgeslagen door de leader).",
        "first_time": "Tijd van het eerste blok (UTC).", "last_time": "Tijd van het laatste blok (UTC).",
        "tokens_created": "Nieuwe pump.fun-tokens.", "graduations": "Tokens waarvan de curve vol raakte.",
        "curve_trades": "Trades op de bonding curve.", "pool_trades": "Koop- en verkooptrades op PumpSwap."},
    "token_summary": {
        "mint": "Token-adres.", "name": "Naam (door de maker gekozen tekst; altijd escapen).",
        "symbol": "Symbool (door de maker gekozen tekst; altijd escapen).",
        "creator": "Maker (CreateEvent.user).", "create_slot": "Slot van de aanmaak.",
        "create_time": "Tijd van de aanmaak (UTC).", "quote_mint": "Quote-munt van de curve.",
        "sol_quote": "Waar als de quote SOL is; alleen dan zijn bedragen in SOL.",
        "mayhem": "Mayhem-token (buiten het strategie-universum).", "cashback": "Cashback-token.",
        "holder_reward": "Holder-rewards-token.", "supply_tokens": "Totale voorraad in hele tokens.",
        "complete_slot": "Slot waarin de curve vol raakte (graduatie).", "graduated": "Waar als gegradueerd.",
        "grad_in_create_slot": "Graduatie in de aanmaak-slot zelf (nooit handelbaar).",
        "migrate_slot": "Slot van de migratie naar PumpSwap.", "pool": "PumpSwap-pool na migratie.",
        "curve_trades": "Trades op de curve.", "curve_buys": "Aankopen op de curve.",
        "curve_sells": "Verkopen op de curve.",
        "curve_buy_quote": "Quote in via aankopen op de curve (excl. fees; SOL als sol_quote).",
        "curve_sell_quote": "Quote uit via verkopen op de curve (excl. fees).",
        "curve_traders": "Unieke handelaren op de curve.", "curve_first_slot": "Eerste curvetrade.",
        "curve_last_slot": "Laatste curvetrade.",
        "curve_max_price": "Hoogste spotprijs op de curve (quote per token, na de trade).",
        "curve_last_price": "Laatste spotprijs op de curve.",
        "curve_max_mcap": "Hoogste marktwaarde op de curve (quote).",
        "pool_trades": "Koop- en verkooptrades op PumpSwap.",
        "pool_volume_quote": "Quote-volume op PumpSwap (quote_gross).",
        "pool_traders": "Unieke handelaren op PumpSwap.", "pool_first_slot": "Eerste pooltrade.",
        "pool_last_slot": "Laatste pooltrade.",
        "pool_max_price": "Hoogste spotprijs in de pool (quote per token, vóór de trade).",
        "pool_last_price": "Spotprijs vóór de laatste pooltrade.",
        "pool_max_mcap": "Hoogste marktwaarde in de pool (quote).",
        "total_volume_quote": "Curve- plus poolvolume (quote)."},
    "token_candles_1m": {
        "mint": "Token-adres.", "venue": "curve of pool.", "minute": "Begin van de minuut (UTC).",
        "open": "Eerste spotprijs in de minuut (quote per token).", "high": "Hoogste spotprijs.",
        "low": "Laagste spotprijs.", "close": "Laatste spotprijs.", "trades": "Aantal trades.",
        "buys": "Aantal aankopen.", "volume_quote": "Volume in quote (excl. fees).",
        "buy_quote": "Volume van aankopen in quote.", "first_slot": "Eerste slot in de minuut.",
        "last_slot": "Laatste slot in de minuut."},
    "graduations": {
        "mint": "Token-adres.", "name": "Naam (escapen).", "symbol": "Symbool (escapen).",
        "creator": "Maker.", "quote_mint": "Quote-munt.", "sol_quote": "Waar als de quote SOL is.",
        "mayhem": "Mayhem-token.", "cashback": "Cashback-token.", "holder_reward": "Holder-rewards-token.",
        "create_slot": "Slot van de aanmaak.", "create_time": "Tijd van de aanmaak (UTC).",
        "complete_slot": "Slot van de graduatie.", "complete_time": "Tijd van de graduatie (UTC).",
        "migrate_slot": "Slot van de migratie.", "pool": "PumpSwap-pool.",
        "in_create_slot": "Graduatie in de aanmaak-slot (nooit handelbaar).",
        "slots_to_graduate": "Slots van aanmaak tot graduatie.",
        "seconds_to_graduate": "Seconden van aanmaak tot graduatie (bloktijd).",
        "curve_trades_before": "Curvetrades tot en met de graduatie.",
        "curve_traders_before": "Unieke handelaren tot en met de graduatie.",
        "quote_raised": "Hoogste echte quote-reserve op de curve (≈ opgehaald bedrag).",
        "pool_first_trade_slot": "Eerste trade in de pool.",
        "pool_trades_first_hour": "Pooltrades in het eerste uur na het aanmaken van de pool (13.468 slots).",
        "pool_volume_first_hour": "Poolvolume (quote) in dat eerste uur."},
    "wallet_summary": {
        "wallet": "Handelaar (trade-user, niet de fee-payer).",
        "curve_trades": "Trades op de curve.", "pool_trades": "Trades op PumpSwap.",
        "tokens_traded": "Aantal verschillende tokens.", "buy_sol": "SOL uitgegeven aan aankopen (excl. fees).",
        "sell_sol": "SOL ontvangen uit verkopen (excl. fees).",
        "net_sol_flow": "sell_sol − buy_sol. ONVOLLEDIG: transfers, fees en open posities ontbreken; geen winst.",
        "first_slot": "Eerste trade.", "last_slot": "Laatste trade.",
        "tokens_created": "Aantal tokens dat deze wallet aanmaakte."},
    "market_events": {
        "slot": "Slot.", "tx_index": "Positie in het blok (leeg bij graduaties en markers).",
        "time": "Tijd (UTC); leeg bij protocol-markers.",
        "kind": "graduation, large_buy, large_sell of protocol_marker.",
        "mint": "Token-adres.", "symbol": "Symbool (escapen).", "name": "Naam (escapen).",
        "venue": "curve of pool.", "side": "buy of sell.",
        "sol": f"Bedrag in SOL (alleen trades van ten minste {LARGE_TRADE_SOL} SOL, SOL-gequote).",
        "wallet": "Handelaar.",
        "note": "Toelichting. Protocol-markers zijn alleen een label met een slotnummer; geen hold-out-inhoud."},
    "strategy_runs": {
        "run_id": "Naam van de toernooirun.", "config_json": "Instellingen van de run (JSON).",
        "store_first_slot": "Eerste slot van de gebruikte store.", "store_last_slot": "Laatste slot van de gebruikte store."},
    "strategy_results": {
        "family": "Strategiefamilie (F7, F1, N1, N2_… = willekeurige schaduw van die familie).",
        "variant": "Variant uit het vooraf geregistreerde rooster.", "size_sol": "Positiegrootte in SOL.",
        "d": "Vertraging in slots tussen signaal en order.", "tau": "Slippage-instelling van andere handelaren.",
        "scenario": "Kostenscenario (optimistic, base, pessimistic).", "mint": "Token-adres.",
        "venue": "curve of pool bij instap.", "day": "UTC-dag van het signaal.",
        "signal_slot": "Slot van het signaal.", "entry_slot": "Slot van de instap.", "exit_slot": "Slot van de uitstap.",
        "exit_reason": "Reden van uitstap (bijv. take_profit, stop_loss, time).",
        "entry_failed": "Instap mislukt op de eigen slippagegrens (alleen de kosten van de mislukte tx).",
        "cf_graduation": "Graduatie die alleen door onze eigen order gebeurt (contrafeitelijk).",
        "hist_ret": "Koersbeweging zonder ons, van instap tot uitstap.",
        "pnl_sol": "Winst of verlies in SOL na fees, netwerk, huur en mislukte tx (zoals lab/backtest/costs.py).",
        "ret": "Rendement op de inleg na alle kosten (leeg als de instap mislukte)."},
    "strategy_summary": {c: "Kolom uit summary.parquet van de toernooirun (zie lab/backtest/report.py)." for c in (
        "family", "variant", "size_sol", "d", "tau", "scenario", "n", "skipped", "filled", "hist_drift",
        "stop_loss_share", "stop_loss_mean", "reverted_router", "reverted_direct", "sells_dropped", "seed_pool",
        "fail_rate", "mean_ret", "median_ret", "win_rate", "mean_pnl_sol", "total_pnl_sol", "ci_lo", "ci_hi",
        "days", "cf_graduation", "n2_mean_ret", "edge_vs_n2")},
    "strategy_curves": {
        "family": "Strategiefamilie.", "variant": "Variant.", "size_sol": "Positiegrootte in SOL.", "d": "Vertraging.",
        "tau": "Slippage-instelling.", "scenario": "Kostenscenario.", "step": "Volgnummer van de trade (op uitstapmoment).",
        "slot": "Slot van de uitstap (of instap bij een mislukte instap).", "pnl_sol": "P&L van deze trade in SOL.",
        "cum_pnl_sol": "Opgetelde P&L tot en met deze trade (de equity-curve)."},
    "data_dictionary": {
        "table_name": "Tabel.", "column_name": "Kolom.", "description": "Uitleg (Nederlands)."},
}


def git_commit():
    r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                       cwd=os.path.dirname(os.path.abspath(__file__)))
    return r.stdout.strip()


def validate(con, require_tables=True):
    """Abort on a hold-out slot anywhere, an empty core table, or an undocumented column."""
    errors = []
    cols = con.execute("SELECT table_name, column_name FROM duckdb_columns() WHERE database_name = current_database()"
                       " AND schema_name = 'main' ORDER BY table_name, column_index").fetchall()
    for t, c in cols:
        if c not in DICTIONARY.get(t, {}):
            errors.append(f"no data_dictionary entry for {t}.{c}")
        if c == "slot" or c.endswith("_slot"):
            where = " AND kind <> 'protocol_marker'" if t == "market_events" else ""
            n = con.execute(f'SELECT count(*) FROM "{t}" WHERE "{c}" >= {HOLDOUT_START}{where}').fetchone()[0]
            if n:
                errors.append(f"{t}.{c}: {n} rows at or after the hold-out start")
    for t in ("data_coverage", "token_summary", "token_candles_1m", "graduations", "wallet_summary"):
        if require_tables and con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] == 0:
            errors.append(f"{t} is empty")
    return errors


def build(out, slots, memory, threads, tournament=None):
    dev = os.path.join(DATA, "store", "dev.duckdb")
    building = out + ".building"
    for p in (building, building + ".wal"):
        if os.path.exists(p):
            os.remove(p)
    tmp_dir = os.path.join(os.path.dirname(out), "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    t0 = time.time()
    con = duckdb.connect(building, config={"memory_limit": memory, "threads": threads,
                                           "temp_directory": tmp_dir, "preserve_insertion_order": False})
    con.execute("SET enable_progress_bar=false; SET TimeZone='UTC'")
    con.execute(f"ATTACH '{dev}' AS dev (READ_ONLY)")
    src = json.loads(con.execute("SELECT json FROM dev.meta").fetchone()[0])
    src_lo, src_hi = src["slot_start"], src["slot_end_exclusive"]
    if src_hi > HOLDOUT_START or src.get("period") != "dev":
        raise SystemExit(f"source store is not development-only: {src}")
    lo, hi = slots or (src_lo, src_hi)
    lo, hi = max(lo, src_lo), min(hi, src_hi, HOLDOUT_START)
    counts = {}
    for name, sql in tables(lo, hi).items():
        t1 = time.time()
        con.execute(f'CREATE TABLE "{name}" AS {sql}')
        counts[name] = con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
        print(f"{name}: {counts[name]} rows in {time.time() - t1:.0f}s", flush=True)
    run = None
    if tournament:
        run = strategies.load(con, tournament).get("run_id")
        for t in ("strategy_results", "strategy_summary", "strategy_curves"):
            counts[t] = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        print(f"tournament {run}: {counts['strategy_results']} settled positions", flush=True)
    meta = {"source": src, "slot_start": lo, "slot_end_exclusive": hi, "built_at_unix": int(time.time()),
            "git_commit": git_commit(), "counts": counts, "large_trade_sol": LARGE_TRADE_SOL, "tournament_run": run}
    con.execute("CREATE TABLE meta AS SELECT ? AS json", [json.dumps(meta)])
    con.execute("CREATE TABLE data_dictionary (table_name VARCHAR, column_name VARCHAR, description VARCHAR)")
    con.executemany("INSERT INTO data_dictionary VALUES (?, ?, ?)",
                    [(t, c, d) for t, cols in DICTIONARY.items() for c, d in cols.items()])
    errors = validate(con)
    con.execute("DETACH dev")
    con.execute("CHECKPOINT")
    con.close()
    if errors:
        os.remove(building)
        raise SystemExit("validation failed, nothing replaced:\n  " + "\n  ".join(errors))
    os.replace(building, out)
    meta["elapsed_s"] = round(time.time() - t0)
    print(json.dumps(meta, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(DATA, "analytics", "analytics.duckdb"))
    ap.add_argument("--slots", help="LO:HI subset of the development window (for smoke runs)")
    ap.add_argument("--memory", default="2GB")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--tournament", help="finished development run dir (backtests/<run>) to load as strategy_* tables")
    a = ap.parse_args()
    slots = tuple(int(x) for x in a.slots.split(":")) if a.slots else None
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    build(os.path.abspath(a.out), slots, a.memory, a.threads, a.tournament)


if __name__ == "__main__":
    main()
