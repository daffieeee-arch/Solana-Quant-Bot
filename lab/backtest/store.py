"""Build a compact DuckDB store for one period of the event dataset (store v2).

  python -m backtest.store dev [--from-slot S] [--to-slot E]  # slots before the hold-out
  python -m backtest.store validate                           # epoch 1052: mechanical checks only
  python -m backtest.store holdout --final                    # only for the single final run

The hold-out store can be built once: a lock file records when and from which commit. The dev
store never contains a hold-out slot, so strategy code that only opens the dev store cannot
peek at it. The build reads one chunk at a time, so it fits a 3 GB memory scope.

Tables:
  blocks           slot -> block_time
  mints            pump CreateEvent + lifecycle (complete slot, migration pool)
  curve            pump TradeEvent of those mints with pre/post reserves, fees and limits
  pools            every PumpSwap pool active in the period, classified offline (see below)
  pool_stats       per-pool aggregates over the whole period: descriptive only, never use
                   them in a signal (they look ahead)
  pool             events of SOL-market pools, pre-trade state: buy / sell / boost /
                   withdraw / deposit (the fee-free inner buy of a boost crank is stored once,
                   as 'boost')
  pool_sweeps      fee sweeps of those pools (new protocol; (B, E) does not move)
  pool_class_daily pools and events per UTC day and pool class
  meta             period, chunks, counts, ms per slot per epoch

Pool classes (from the pool's own events, no RPC): a pool's protocol fee account is the
recipient's ATA for the quote mint, so it equals ATA(recipient, WSOL) exactly when the quote is
SOL ('sol'). base_supply = 0 means the base mint is WSOL ('reversed': a SOL market with the
token as quote). Other quotes ('other') and pools without evidence ('unknown') are excluded and
counted. Pool event rows carry SOL-side columns that read the same for both orientations:
token_buy (the trader buys the token), sol_amount (SOL paid or received by the trader) and
sol_depth (real SOL in the pool).
"""

import argparse
import json
import os
import shutil
import subprocess
import time

import yaml

from . import data, pda

DEFAULTS = "/home/chupa/Solana-project/data-old-faithful-one/lab/research/week2-defaults.yaml"
STORE_DIR = "/home/chupa/Solana-project/data-old-faithful-one/lab/store"
SPILL_DIR = "/home/chupa/Solana-project/data-old-faithful-one/lab/tmp-duckdb"
VALIDATE = (454464000, 454896000)  # epoch 1052: after the hold-out, still 250 ms slots
DISK_FLOOR_GB = 50
POOL_PARTS = 4
H = "::HUGEINT"
B = "::BIGINT"


def load_defaults(path=DEFAULTS):
    with open(path) as f:
        return yaml.safe_load(f)


def period_range(period, cfg, from_slot=None, to_slot=None):
    hold = cfg["windows"]["holdout"]
    if period == "dev":
        dev = cfg["windows"]["development"]
        lo = from_slot if from_slot is not None else dev["start_slot_target"]
        hi = to_slot if to_slot is not None else dev["end_slot_exclusive"]
        if hi > hold["start_slot"]:
            raise SystemExit("the dev period must end at or before the hold-out start")
        return lo, hi
    if period == "holdout":
        return hold["start_slot"], hold["end_slot_exclusive"]
    if period == "validate":
        lo, hi = VALIDATE
        if lo < hold["end_slot_exclusive"]:
            raise SystemExit("the validation period overlaps the hold-out")
        return lo, hi
    raise ValueError(period)


def period_chunks(period, cfg, from_slot=None, to_slot=None):
    """The complete chunks that tile [lo, hi) exactly; fails on any gap."""
    lo, hi = period_range(period, cfg, from_slot, to_slot)
    chunks = [c for c in data.complete_chunks() if c[0] >= lo and c[1] <= hi]
    edge, missing = lo, []
    for c in chunks:
        if c[0] != edge:
            missing.append((edge, c[0]))
        edge = c[1]
    if edge != hi:
        missing.append((edge, hi))
    if missing:
        raise SystemExit(f"period {period} [{lo}, {hi}) is not covered by complete chunks; missing {missing}")
    return chunks


class Chunk:
    """SQL sources of one chunk; columns that an older extractor did not write read as NULL."""

    def __init__(self, chunk):
        self.chunk = [chunk]

    def src(self, table):
        return data.parquet(table, self.chunk, missing_ok=True)

    def col(self, table, name, cast):
        cols = data.columns(table, self.chunk) if data.files(table, self.chunk) else set()
        return f"TRY_CAST({name} AS {cast})" if name in cols else f"NULL::{cast}"

    def arg(self, table, key, cast="HUGEINT"):
        if "parent_ix_args" not in (data.columns(table, self.chunk) if data.files(table, self.chunk) else set()):
            return f"NULL::{cast}"
        return f"TRY_CAST(json_extract_string(parent_ix_args, '$.{key}') AS {cast})"


def _chunk_meta(chunk):
    with open(os.path.join(chunk[2], "_manifest.json")) as f:
        m = json.load(f)
    return {"slots": [chunk[0], chunk[1]], "extractor_sha256": m.get("extractor_sha256"),
            "manifest_mtime": int(os.path.getmtime(os.path.join(chunk[2], "_manifest.json")))}


def _disk_check(out_dir, chunks):
    """Projected store size (~70 bytes per stored event) must leave the disk floor intact."""
    events = 0
    for c in chunks:
        with open(os.path.join(c[2], "_manifest.json")) as f:
            rows = json.load(f).get("rows", {})
        events += sum(v for k, v in rows.items() if k.startswith(("pump/TradeEvent", "pump_amm/")))
    need_gb = events * 70 / 1e9 * 1.5  # file plus spill
    free_gb = shutil.disk_usage(out_dir).free / 1e9
    if free_gb - need_gb < DISK_FLOOR_GB:
        raise SystemExit(f"disk: {free_gb:.0f} GB free, build needs ~{need_gb:.0f} GB, floor {DISK_FLOOR_GB} GB")
    return need_gb, free_gb


# ----------------------------------------------------------------------------- per-chunk SQL


def _pool_trades(c, evidence=False):
    """Buy and sell rows of one chunk in pool orientation (boost-crank inner buys excluded).
    evidence=True adds the columns used to classify pools."""
    buy, sell = c.src("pump_amm/BuyEvent"), c.src("pump_amm/SellEvent")
    vq = lambda t: c.col(t, "virtual_quote_reserves", "BIGINT")  # noqa: E731
    cb = lambda t: c.col(t, "cashback_fee_basis_points", "INTEGER")  # noqa: E731
    common = ("pool, slot, tx_index, outer_ix, inner_ix, \"user\" AS trader, NULLIF(fee_payer, \"user\") AS fee_payer, "
              "outer_program, priority_fee, jito_tip, tx_fee, parent_ix_disc, "
              + ("protocol_fee_recipient AS recipient, protocol_fee_recipient_token_account AS recipient_account, "
                 "coin_creator, base_supply, can_boost, " if evidence else "")
              + f"pool_base_token_reserves{B} AS b, pool_quote_token_reserves{B} AS vault, "
              "lp_fee_basis_points::INTEGER AS lp_bps, protocol_fee_basis_points::INTEGER AS protocol_bps, "
              "COALESCE(coin_creator_fee_basis_points, 0)::INTEGER AS creator_bps")
    exact_out = ",".join(f"'{d}'" for d in data.POOL_EXACT_OUT)
    parts = []
    if buy:
        parts.append(f"""
          SELECT 'buy' AS kind, {common}, COALESCE({vq('pump_amm/BuyEvent')}, 0) AS vq,
                 COALESCE({cb('pump_amm/BuyEvent')}, 0) AS cashback_bps,
                 base_amount_out{B} AS base, quote_amount_in_with_lp_fee{B} AS e_delta,
                 CASE WHEN parent_ix_disc IN ({exact_out}) THEN user_quote_amount_in ELSE quote_amount_in END{B} AS quote_user,
                 max_quote_amount_in{H} AS limit_quote, min_base_amount_out{H} AS limit_base
          FROM {buy} WHERE parent_ix_disc IS DISTINCT FROM '{data.BOOST_BUY_DISC}'""")
    if sell:
        parts.append(f"""
          SELECT 'sell' AS kind, {common}, COALESCE({vq('pump_amm/SellEvent')}, 0) AS vq,
                 COALESCE({cb('pump_amm/SellEvent')}, 0) AS cashback_bps,
                 base_amount_in{B} AS base, -(quote_amount_out_without_lp_fee{B}) AS e_delta,
                 user_quote_amount_out{B} AS quote_user, min_quote_amount_out{H} AS limit_quote, NULL::HUGEINT AS limit_base
          FROM {sell}""")
    return " UNION ALL BY NAME ".join(parts) if parts else None


def _pool_other(c):
    """Boost, withdraw and deposit rows of one chunk (pre-state where the event exposes it)."""
    parts = []
    boost = c.src("pump_amm/BoostBuyAndBurnEvent")
    if boost:
        parts.append(f"""
          SELECT 'boost' AS kind, pool, slot, tx_index, outer_ix, inner_ix, authority AS trader,
                 NULLIF(fee_payer, authority) AS fee_payer, outer_program,
                 priority_fee, jito_tip, tx_fee, parent_ix_disc,
                 (base_reserves_after + base_amount_burned){B} AS b,
                 (real_quote_reserves_after - quote_amount_in_used){B} AS vault,
                 COALESCE(TRY_CAST(virtual_quote_reserves AS BIGINT), 0) AS vq,
                 base_amount_burned{B} AS base, quote_amount_in_used{B} AS e_delta, quote_amount_in_used{B} AS quote_user,
                 boost_vault_remaining{B} AS boost_left
          FROM {boost}""")
    for kind, table, base, quote, lp in (("withdraw", "pump_amm/WithdrawEvent", "base_amount_out", "quote_amount_out",
                                          "lp_token_amount_in"),
                                         ("deposit", "pump_amm/DepositEvent", "base_amount_in", "quote_amount_in",
                                          "lp_token_amount_out")):
        src = c.src(table)
        if src:
            parts.append(f"""
              SELECT '{kind}' AS kind, pool, slot, tx_index, outer_ix, inner_ix, "user" AS trader,
                     NULLIF(fee_payer, "user") AS fee_payer, outer_program,
                     priority_fee, jito_tip, tx_fee, parent_ix_disc,
                     pool_base_token_reserves{B} AS b, pool_quote_token_reserves{B} AS vault, NULL::BIGINT AS vq,
                     {base}{B} AS base, {quote}{B} AS quote_user, {lp}{B} AS lp_amount, lp_mint_supply{B} AS lp_supply
              FROM {src}""")
    return " UNION ALL BY NAME ".join(parts) if parts else None


POOL_SCHEMA = """kind VARCHAR, pool VARCHAR, slot UBIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT,
  trader VARCHAR, fee_payer VARCHAR, outer_program VARCHAR, priority_fee UBIGINT, jito_tip UBIGINT, tx_fee UBIGINT,
  parent_ix_disc VARCHAR, b BIGINT, vault BIGINT, vq BIGINT, e BIGINT, lp_bps INTEGER, protocol_bps INTEGER,
  creator_bps INTEGER, cashback_bps INTEGER, base BIGINT, e_delta BIGINT, quote_user BIGINT, limit_quote HUGEINT,
  limit_base HUGEINT, boost_left BIGINT, lp_amount BIGINT, lp_supply BIGINT, orientation VARCHAR, token_buy BOOLEAN,
  sol_amount BIGINT, sol_depth BIGINT"""


# ----------------------------------------------------------------------------- build


def build(period, out_path, cfg, chunks, log=print):
    lo, hi = chunks[0][0], chunks[-1][1]
    tmp = out_path + ".building"
    for p in (tmp, tmp + ".wal"):
        if os.path.exists(p):
            os.remove(p)
    os.makedirs(SPILL_DIR, exist_ok=True)
    con = data.connect(threads=int(os.environ.get("LAB_DUCKDB_THREADS", "2")),
                       memory=os.environ.get("LAB_DUCKDB_MEM", "1500MB"), temp=SPILL_DIR)
    con.execute("SET max_temp_directory_size = '30GB'")
    con.execute(f"ATTACH '{tmp}' AS s")
    t0 = time.time()
    P = lambda t, **k: data.parquet(t, chunks, **k)  # noqa: E731
    ix_case = " ".join(f"WHEN '{d}' THEN '{n}'" for d, n in data.PUMP_IX.items())

    # Mints and their lifecycle: small tables, read over the whole period.
    con.execute(f"""
      CREATE TABLE s.mints AS
      WITH c AS (
        SELECT mint, slot AS create_slot, tx_index AS create_tx, "user" AS creator, creator AS creator_field,
               COALESCE(is_mayhem_mode, false) AS mayhem, COALESCE(quote_mint, '{pda.DEFAULT_KEY}') AS quote_mint,
               COALESCE(is_cashback_enabled, false) AS cashback, COALESCE(is_holder_reward, false) AS holder_reward,
               token_total_supply AS supply, name, symbol
        FROM {P('pump/CreateEvent')} QUALIFY row_number() OVER (PARTITION BY mint ORDER BY slot, tx_index) = 1),
      done AS (SELECT mint, min(slot) AS complete_slot FROM {P('pump/CompleteEvent')} GROUP BY mint),
      mig AS (SELECT mint, min(slot) AS migrate_slot, any_value(pool) AS pool FROM {P('pump/CompletePumpAmmMigrationEvent')} GROUP BY mint)
      SELECT c.*, done.complete_slot, mig.migrate_slot, mig.pool
      FROM c LEFT JOIN done USING (mint) LEFT JOIN mig USING (mint) ORDER BY create_slot, create_tx""")
    log(f"mints {con.execute('SELECT count(*) FROM s.mints').fetchone()[0]} ({time.time() - t0:.0f}s)")

    con.execute("CREATE TABLE s.blocks (slot UBIGINT, block_time BIGINT)")
    con.execute("""CREATE TEMP TABLE pool_agg (pool VARCHAR, first_slot UBIGINT, last_slot UBIGINT, n_buy BIGINT,
                   n_sell BIGINT, reversed BOOLEAN, can_boost BOOLEAN, coin_creator VARCHAR, last_b BIGINT, last_vault BIGINT)""")
    con.execute("CREATE TEMP TABLE pool_fee (pool VARCHAR, recipient VARCHAR, account VARCHAR)")
    con.execute("CREATE TEMP TABLE pool_day (pool VARCHAR, day DATE, n_buy BIGINT, n_sell BIGINT)")

    # Pass 1: blocks and per-pool evidence, one chunk at a time.
    for ch in chunks:
        c = Chunk(ch)
        con.execute(f"INSERT INTO s.blocks SELECT slot, any_value(block_time) FROM {c.src('blocks')} "
                    "WHERE NOT skipped GROUP BY slot ORDER BY slot")
        trades = _pool_trades(c, evidence=True)
        if not trades:
            continue
        con.execute(f"""INSERT INTO pool_agg SELECT pool, min(slot), max(slot), count(*) FILTER (WHERE kind = 'buy'),
                          count(*) FILTER (WHERE kind = 'sell'), bool_or(base_supply = 0), bool_or(can_boost),
                          arg_max(coin_creator, slot), arg_max(b, slot), arg_max(vault, slot) FROM ({trades}) GROUP BY pool""")
        con.execute(f"""INSERT INTO pool_fee SELECT DISTINCT pool, recipient, recipient_account FROM ({trades})
                        WHERE recipient IS NOT NULL AND recipient <> '{pda.DEFAULT_KEY}'""")
        con.execute(f"""INSERT INTO pool_day SELECT tr.pool, epoch_ms(bl.block_time * 1000)::DATE,
                          count(*) FILTER (WHERE tr.kind = 'buy'), count(*) FILTER (WHERE tr.kind = 'sell')
                        FROM ({trades}) tr JOIN s.blocks bl ON bl.slot = tr.slot
                        WHERE bl.slot >= {ch[0]} AND bl.slot < {ch[1]} GROUP BY ALL""")
        log(f"pass 1 {ch[0]} ({time.time() - t0:.0f}s)")

    _classify_pools(con, P, chunks, log)
    log(f"pools classified ({time.time() - t0:.0f}s): "
        + str(con.execute("SELECT quote_class, count(*) FROM s.pools GROUP BY 1 ORDER BY 1").fetchall()))

    # Pass 2: event tables for the SOL markets, one chunk at a time (each chunk sorted).
    con.execute(f"CREATE TABLE s.pool ({POOL_SCHEMA})")
    con.execute("""CREATE TABLE s.pool_sweeps (pool VARCHAR, slot UBIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT,
                   amount BIGINT, bucket INTEGER)""")
    first = True
    for ch in chunks:
        c = Chunk(ch)
        T = "pump/TradeEvent"
        trade = c.src(T)
        if trade:
            sql = f"""
              SELECT t.mint, t.slot, t.tx_index, t.outer_ix, t.inner_ix,
                CASE t.parent_ix_disc {ix_case} ELSE 'other' END AS variant,
                t.is_buy, t.token_amount{H} AS t, COALESCE(t.quote_amount, t.sol_amount){H} AS q,
                t.fee{H} AS fee, t.creator_fee{H} AS creator_fee, t.fee_basis_points::INTEGER AS fee_bps,
                t.creator_fee_basis_points::INTEGER AS creator_bps,
                COALESCE({c.col(T, 'cashback_fee_basis_points', 'INTEGER')}, 0) AS cashback_bps,
                t.virtual_token_reserves{H} AS vt, COALESCE(t.virtual_quote_reserves, t.virtual_sol_reserves){H} AS vq,
                t.real_token_reserves{H} AS rt, COALESCE(t.real_quote_reserves, t.real_sol_reserves){H} AS rq,
                t."user" AS trader, NULLIF(t.fee_payer, t."user") AS fee_payer, t.outer_program, t.priority_fee,
                t.jito_tip, t.tx_fee,
                COALESCE(t.mayhem_mode, false) AS mayhem_trade,
                {c.arg(T, 'amount')} AS arg_amount,
                COALESCE({c.arg(T, 'spendable_sol_in')}, {c.arg(T, 'spendable_quote_in')}) AS arg_budget,
                {c.arg(T, 'max_sol_cost')} AS arg_max_cost,
                {c.arg(T, 'min_tokens_out')} AS arg_min_tokens,
                {c.arg(T, 'min_sol_output')} AS arg_min_quote_out,
                {c.arg(T, 'partial_fill', 'BOOLEAN')} AS arg_partial_fill
              FROM {trade} t SEMI JOIN s.mints m ON m.mint = t.mint
              ORDER BY t.mint, t.slot, t.tx_index, t.outer_ix, t.inner_ix"""
            con.execute(f"CREATE TABLE s.curve AS {sql}" if first else f"INSERT INTO s.curve BY NAME {sql}")
            first = False
        trades, other = _pool_trades(c), _pool_other(c)
        src = " UNION ALL BY NAME ".join(x for x in (trades, other) if x)
        if src:
            # Sorted inserts in POOL_PARTS slices of the pools, so one sort never holds a whole chunk.
            for part in range(POOL_PARTS):
                sel = f"""
                  SELECT x.*, x.vault + x.vq AS e, p.orientation,
                    CASE WHEN x.kind IN ('buy', 'sell') THEN (x.kind = 'buy') = (p.orientation = 'normal') END AS token_buy,
                    CASE WHEN p.orientation = 'normal' THEN x.quote_user ELSE x.base END AS sol_amount,
                    CASE WHEN p.orientation = 'normal' THEN x.vault ELSE x.b END AS sol_depth
                  FROM ({src}) x JOIN s.pools p USING (pool)
                  WHERE p.quote_class IN ('sol', 'reversed') AND p.part = {part}
                  ORDER BY x.pool, x.slot, x.tx_index, x.outer_ix, x.inner_ix"""
                con.execute(f"INSERT INTO s.pool BY NAME {sel}")
        sweep = c.src("pump_amm/SweepPoolFeeEvent")
        if sweep:
            con.execute(f"""INSERT INTO s.pool_sweeps SELECT pool, slot, tx_index, outer_ix, inner_ix, amount{B}, bucket::INTEGER
                            FROM {sweep} SEMI JOIN s.pools p USING (pool) WHERE p.quote_class IN ('sol', 'reversed')""")
        log(f"pass 2 {ch[0]} ({time.time() - t0:.0f}s)")

    con.execute("""
      CREATE TABLE s.pool_class_daily AS
      SELECT d.day, p.quote_class, p.mayhem, count(DISTINCT d.pool) AS pools, sum(d.n_buy) AS buys, sum(d.n_sell) AS sells
      FROM pool_day d JOIN s.pools p USING (pool) GROUP BY ALL ORDER BY ALL""")

    counts = {t: con.execute(f"SELECT count(*) FROM s.{t}").fetchone()[0]
              for t in ("blocks", "mints", "curve", "pools", "pool", "pool_sweeps")}
    ms = con.execute("""SELECT slot // 432000 AS epoch, (max(block_time) - min(block_time)) * 1000.0 / (max(slot) - min(slot))
                        FROM s.blocks GROUP BY 1 ORDER BY 1""").fetchall()
    unhandled = {}
    for ch in chunks:
        with open(os.path.join(ch[2], "_manifest.json")) as f:
            rows = json.load(f).get("rows", {})
        for t in ("pump/PostCompleteBuyEvent", "pump/SweepBondingCurveFeeEvent"):
            unhandled[t] = unhandled.get(t, 0) + rows.get(t, 0)
    meta = {
        "store_version": 2, "period": period, "slot_start": lo, "slot_end_exclusive": hi, "chunks": len(chunks),
        "chunk_list": [_chunk_meta(c) for c in chunks], "built_at_unix": int(time.time()), "build_seconds": round(time.time() - t0),
        "counts": counts,
        "pool_classes": {k: {"pools": n, "rows": r} for k, n, r in con.execute(
            "SELECT quote_class, count(*), sum(n_buy + n_sell) FROM s.pools JOIN s.pool_stats USING (pool) GROUP BY 1").fetchall()},
        "ms_per_slot": {int(e): round(v, 2) for e, v in ms},
        "unhandled_event_files": unhandled,
        "git_commit": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                                     cwd=os.path.dirname(__file__)).stdout.strip(),
    }
    con.execute("CREATE TABLE s.meta AS SELECT ? AS json", [json.dumps(meta)])
    con.execute("DETACH s")
    con.close()
    os.replace(tmp, out_path)
    return meta


def _classify_pools(con, P, chunks, log):
    """pools + pool_stats from the per-chunk evidence; checked against every CreatePoolEvent."""
    recipients = [r[0] for r in con.execute("SELECT DISTINCT recipient FROM pool_fee").fetchall()]
    con.execute("CREATE OR REPLACE TEMP TABLE wsol_ata (recipient VARCHAR, account VARCHAR)")
    con.executemany("INSERT INTO wsol_ata VALUES (?, ?)", [(r, pda.ata(r, pda.WSOL)) for r in recipients])
    create = P("pump_amm/CreatePoolEvent", missing_ok=True)
    create_sql = (f"""SELECT pool, base_mint, quote_mint, slot AS create_slot, coin_creator, is_mayhem_mode AS mayhem,
                         pool_base_amount{B} AS b0, pool_quote_amount{B} AS q0
                      FROM {create} QUALIFY row_number() OVER (PARTITION BY pool ORDER BY slot) = 1""" if create else
                  "SELECT NULL::VARCHAR AS pool, NULL::VARCHAR AS base_mint, NULL::VARCHAR AS quote_mint, NULL::UBIGINT AS create_slot, "
                  "NULL::VARCHAR AS coin_creator, NULL::BOOLEAN AS mayhem, NULL::BIGINT AS b0, NULL::BIGINT AS q0 WHERE false")
    con.execute(f"CREATE OR REPLACE TEMP TABLE cp AS {create_sql}")
    # Base-mint links from events that name both the pool and the mint.
    links = []
    for table, mint in (("pump/CompletePumpAmmMigrationEvent", "mint"), ("pump_amm/BoostBuyAndBurnEvent", "mint"),
                        ("pump_amm/InitBoostEvent", "mint"), ("pump_amm/MigratePoolCoinCreatorEvent", "base_mint"),
                        ("pump_amm/AdminCtoPoolEvent", "base_mint")):
        src = P(table, missing_ok=True)
        if src and {"pool", mint} <= data.columns(table, chunks):
            links.append(f"SELECT pool, {mint} AS mint, '{table.split('/')[1]}' AS src FROM {src}")
    con.execute("CREATE OR REPLACE TEMP TABLE link AS " + (" UNION ALL ".join(links) if links else
                "SELECT NULL::VARCHAR AS pool, NULL::VARCHAR AS mint, NULL::VARCHAR AS src WHERE false"))
    con.execute("""
      CREATE OR REPLACE TEMP TABLE evidence AS
      SELECT f.pool, count(*) AS n_pairs, count(*) FILTER (WHERE w.account = f.account) AS n_wsol,
             list(DISTINCT f.recipient) AS recipients
      FROM pool_fee f LEFT JOIN wsol_ata w USING (recipient) GROUP BY f.pool""")
    # Mayhem pools pay a separate set of fee recipients: learn the map from pools with a create event.
    rec = con.execute("""
      SELECT f.recipient, count(DISTINCT cp.mayhem) AS n, any_value(cp.mayhem) AS mayhem
      FROM (SELECT DISTINCT pool, recipient FROM pool_fee) f JOIN cp USING (pool) GROUP BY 1""").fetchall()
    bad = [r for r in rec if r[1] > 1]
    if bad:
        raise SystemExit(f"fee recipient maps to both mayhem and normal pools: {bad[:5]}")
    con.execute("CREATE OR REPLACE TEMP TABLE rec_mayhem (recipient VARCHAR, mayhem BOOLEAN)")
    con.executemany("INSERT INTO rec_mayhem VALUES (?, ?)", [(r[0], r[2]) for r in rec])
    con.execute(f"""
      CREATE TABLE s.pool_stats AS
      SELECT pool, min(first_slot) AS first_slot, max(last_slot) AS last_slot, sum(n_buy) AS n_buy, sum(n_sell) AS n_sell,
             bool_or(reversed) AS reversed, bool_or(can_boost) AS can_boost, arg_max(coin_creator, last_slot) AS coin_creator,
             arg_max(last_b, last_slot) AS last_b, arg_max(last_vault, last_slot) AS last_vault
      FROM pool_agg GROUP BY pool""")
    con.execute(f"""
      CREATE TABLE s.pools AS
      WITH ev AS (
        SELECT s.pool, s.reversed, s.can_boost, s.coin_creator AS coin_creator_ev, e.n_pairs, e.n_wsol,
               (SELECT bool_or(r.mayhem) FROM rec_mayhem r WHERE list_contains(e.recipients, r.recipient)) AS mayhem_ev,
               (SELECT count(DISTINCT r.mayhem) FROM rec_mayhem r WHERE list_contains(e.recipients, r.recipient)) AS mayhem_kinds
        FROM s.pool_stats s LEFT JOIN evidence e USING (pool)),
      lk AS (SELECT pool, any_value(mint) AS mint, any_value(src) AS src, count(DISTINCT mint) AS n FROM link GROUP BY pool)
      SELECT ev.pool,
        CASE WHEN ev.reversed THEN 'reversed'
             WHEN ev.n_pairs IS NULL THEN 'unknown'
             WHEN ev.n_wsol = ev.n_pairs THEN 'sol'
             WHEN ev.n_wsol = 0 THEN 'other'
             ELSE 'mixed' END AS quote_class,
        CASE WHEN ev.reversed THEN 'reversed' ELSE 'normal' END AS orientation,
        CASE WHEN ev.reversed THEN cp.quote_mint WHEN ev.n_pairs > 0 AND ev.n_wsol = ev.n_pairs THEN '{pda.WSOL}'
             ELSE cp.quote_mint END AS quote_mint,
        CASE WHEN ev.reversed THEN COALESCE(cp.quote_mint, '') ELSE COALESCE(cp.base_mint, lk.mint) END AS mint,
        CASE WHEN cp.pool IS NOT NULL THEN 'create_pool' WHEN lk.n = 1 THEN lk.src END AS mint_source,
        COALESCE(cp.mayhem, CASE WHEN ev.mayhem_kinds = 1 THEN ev.mayhem_ev END) AS mayhem,
        CASE WHEN cp.pool IS NOT NULL THEN 'create_pool' WHEN ev.mayhem_kinds = 1 THEN 'fee_recipient' END AS mayhem_source,
        cp.create_slot, COALESCE(cp.coin_creator, ev.coin_creator_ev) AS coin_creator, ev.can_boost, cp.b0, cp.q0,
        cp.pool IS NOT NULL AS created_in_period
      FROM ev LEFT JOIN cp USING (pool) LEFT JOIN lk USING (pool)""")
    con.execute("UPDATE s.pools SET mint = NULL WHERE mint = ''")
    con.execute(f"""ALTER TABLE s.pools ADD COLUMN part INTEGER""")
    con.execute(f"""UPDATE s.pools SET part = q.part FROM (SELECT pool, (ntile({POOL_PARTS}) OVER (ORDER BY pool)) - 1 AS part
                    FROM s.pools) q WHERE q.pool = s.pools.pool""")
    # Checks against the pools whose create event we have.
    chk = con.execute(f"""
      SELECT count(*) AS n,
        count(*) FILTER (WHERE (p.quote_class = 'sol') <> (cp.quote_mint = '{pda.WSOL}' AND cp.base_mint <> '{pda.WSOL}')),
        count(*) FILTER (WHERE (p.quote_class = 'reversed') <> (cp.base_mint = '{pda.WSOL}')),
        count(*) FILTER (WHERE p.quote_class = 'mixed')
      FROM s.pools p JOIN cp USING (pool)""").fetchone()
    log(f"classification check on {chk[0]} created pools: sol mismatch {chk[1]}, reversed mismatch {chk[2]}, mixed {chk[3]}")
    if chk[0] and (chk[1] + chk[2] + chk[3]) > max(10, 0.001 * chk[0]):
        raise SystemExit(f"pool classification disagrees with CreatePoolEvent on too many pools: {chk}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("period", choices=["dev", "holdout", "validate"])
    ap.add_argument("--final", action="store_true", help="required for the one-time hold-out build")
    ap.add_argument("--from-slot", type=int, help="dev only: first slot (default: development.start_slot_target)")
    ap.add_argument("--to-slot", type=int, help="dev only: end slot, exclusive (default: the hold-out start)")
    ap.add_argument("--out", help="output file (default: <store dir>/<period>.duckdb)")
    ap.add_argument("--out-dir", default=os.environ.get("LAB_STORE_DIR", STORE_DIR))
    a = ap.parse_args()
    cfg = load_defaults()
    os.makedirs(a.out_dir, exist_ok=True)
    if a.period == "holdout":
        lock = os.path.join(a.out_dir, "holdout.lock")
        if not a.final:
            raise SystemExit("the hold-out store is built once, for the final run: pass --final")
        if os.path.exists(lock):
            raise SystemExit(f"hold-out already used: {open(lock).read()}")
    chunks = period_chunks(a.period, cfg, a.from_slot, a.to_slot)
    need, free = _disk_check(a.out_dir, chunks)
    print(f"{len(chunks)} chunks [{chunks[0][0]}, {chunks[-1][1]}); ~{need:.0f} GB needed, {free:.0f} GB free", flush=True)
    out = a.out or os.path.join(a.out_dir, f"{a.period}.duckdb")
    meta = build(a.period, out, cfg, chunks, log=lambda m: print(m, flush=True))
    if a.period == "holdout":
        with open(lock, "w") as f:
            json.dump(meta, f)
    print(json.dumps({k: v for k, v in meta.items() if k != "chunk_list"}, indent=1))


if __name__ == "__main__":
    main()
