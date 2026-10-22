"""The venue store (venue decoders PR 3; research/venue-decoders-spec-2026-10-21.md v0.2 §2, §3, §6, LAB review on
#506 and #513): Meteora DBC, Meteora DAMM v2, Raydium LaunchLab and Raydium CPMM events of the extractor's venue chunks
(LAB_VENUES=1, events/venues-v2) in one DuckDB file per period. Old Faithful history only.

  python -m backtest.venue_store dev --chunks ROOT [--from-slot S --to-slot E] --out FILE   # development slots only
  python -m backtest.venue_store forward --chunks ROOT --from-slot S --to-slot E --out FILE  # >= epoch 1053, illustration

A store is built once (an existing file is refused) and never grows: a forward range is its own sha-pinned store (spec
§8.3, E32). The hold-out (1047-1051) and the clean epochs (1055-1063) are refused. One chunk is read at a time.

Tables (every event row of a failed transaction is dropped: the runtime recorded it before the failure and rolled the
state back; costs from venue_census on (slot, tx_index)):
  dbc            DBC swaps, one row per swap instruction (slot, tx_index, outer_ix, parent_inner_ix): a swap emits
                 EvtSwap and EvtSwap2 (or EvtSwap3, or only a transfer-hook variant), and the row is taken from the
                 newest layout, EvtSwap3 > EvtSwap2 > EvtSwap (hook variants rank with their base). quote_reserve is
                 the event's (EvtSwap2/3, reserve_source 'event') or chained from the pool's nearest earlier anchor
                 plus the quote moved since (reserve_source 'chained', an approximation that ignores fees; depth
                 features exclude these rows or report their share), NULL without an anchor; migration_threshold
                 from EvtSwap2. is_buy: the trader pays quote (trade_direction 1, QuoteToBase).
  damm2          DAMM v2 swaps (EvtSwap2, kind 'swap', a_to_b from trade_direction 0) and liquidity changes
                 (EvtLiquidityChange, kind 'add' / 'remove' from change_type 0 / 1), with the pool's reserves after.
  launchlab      LaunchLab trades (TradeEvent): the curve state before and after, the fees, the pool status.
  cpmm           CPMM swaps (SwapEvent, kind 'swap') and LP changes (LpChangeEvent, kind 'deposit' / 'withdraw' from
                 change_type 0 / 1). source 'log' (the event) or 'balance' (the extractor's fallback from the vaults'
                 token balances: the amounts are vault-side, net of any Token-2022 transfer fee, and NULL when another
                 CPMM instruction of the transaction touches the same pool, decode_status balance_ambiguous / missing).
  venue_pools    one row per pool creation: DBC and DAMM v2 EvtInitializePool, LaunchLab PoolCreateEvent, CPMM's
                 initialize rows (venue_inits); base / quote mints (CPMM: WSOL is the quote when one side is WSOL),
                 creator, config, the creation's parameters as JSON.
  venue_graduations  the extractor's graduation rows, with linked: the new pool has a venue_pools row (only DAMM v2 and
                 CPMM pools can: DAMM v1 and AMM v4 are not decoded).
  meta           period, chunks (with their extractor commit and venue IDL sha256s), counts, the checks.

Prices follow spec §3 from these columns (not stored): DBC and DAMM v2 (next_sqrt_price / 2^64)^2 in raw units;
LaunchLab constant product (virtual_quote + real_quote_after) / (virtual_base - real_base_after); CPMM the vaults
before the swap (input_vault_before, output_vault_before).

Checks (meta.checks; the job is RED on any 'red'): DBC chained share <= 5 % (spec §8.1); CPMM balance rows <= 1 % (§8.2);
graduations to DAMM v2 / CPMM linked >= 99 % (§6); LaunchLab continuity: real_*_before equals the pool's previous
real_*_after for >= 99 % of the trades (§6).
"""

import argparse
import glob
import json
import os
import subprocess
import time

import yaml

from . import data
from .store import CLEAN, DEFAULTS, overlaps_clean, period_range

WSOL = "So11111111111111111111111111111111111111112"
SWAP_RANK = {"EvtSwap": 1, "EvtSwap2": 2, "EvtSwap2WithTransferHook": 2, "EvtSwap3": 3, "EvtSwap3WithTransferHook": 3}
CTX = "slot, tx_index, outer_ix, parent_inner_ix, event_seq_all, ix_trader AS trader, fee_payer, parent_ix_name, decode_status, source"
DBC_CHAINED_MAX = 0.05
CPMM_BALANCE_MAX = 0.01
LINK_MIN = 0.99
CONTINUITY_MIN = 0.99


class Src:
    """One chunk's tables; a table or column an older extractor did not write reads as absent / NULL."""

    def __init__(self, chunk):
        self.chunk = [chunk]

    def src(self, table):
        return data.parquet(table, self.chunk, missing_ok=True)

    def has(self, table, column):
        return bool(data.files(table, self.chunk)) and column in data.columns(table, self.chunk)

    def col(self, table, name, cast):
        return f"TRY_CAST({name} AS {cast})" if self.has(table, name) else f"NULL::{cast}"

    def js(self, table, name, key, cast):
        """A field of a nested IDL struct, decoded by the extractor as a JSON string."""
        return f"TRY_CAST(json_extract_string({name}, '$.{key}') AS {cast})" if self.has(table, name) else f"NULL::{cast}"


def costs_join(s, alias):
    """Costs of the event's transaction from venue_census (absent: NULL)."""
    c = s.src("venue_census")
    if c is None:
        return ("NULL::UBIGINT AS tx_fee, NULL::UBIGINT AS priority_fee, NULL::UBIGINT AS jito_tip, NULL::UBIGINT AS cu_consumed", "")
    return (f"c.tx_fee, c.priority_fee, c.jito_tip, c.cu_consumed",
            f"LEFT JOIN (SELECT slot, tx_index, any_value(tx_fee) AS tx_fee, any_value(priority_fee) AS priority_fee, "
            f"any_value(jito_tip) AS jito_tip, any_value(cu_consumed) AS cu_consumed FROM {c} GROUP BY 1, 2) c "
            f"ON c.slot = {alias}.slot AND c.tx_index = {alias}.tx_index")


def dbc_sql(s):
    """DBC swaps of one chunk, one row per swap instruction (the newest layout of its events)."""
    parts = []
    for ev, rank in SWAP_RANK.items():
        t = f"meteora_dbc/{ev}"
        src = s.src(t)
        if src is None:
            continue
        if ev == "EvtSwap":
            amount_in, amount_out = s.js(t, "swap_result", "actual_input_amount", "UBIGINT"), s.js(t, "swap_result", "output_amount", "UBIGINT")
            fees = [s.js(t, "swap_result", k, "UBIGINT") for k in ("trading_fee", "protocol_fee", "referral_fee")]
            sqrt, reserve, threshold, mode, left = s.js(t, "swap_result", "next_sqrt_price", "VARCHAR"), "NULL::UBIGINT", "NULL::UBIGINT", "NULL::UBIGINT", "NULL::UBIGINT"
        elif rank == 2:
            amount_in, amount_out = s.js(t, "swap_result", "included_fee_input_amount", "UBIGINT"), s.js(t, "swap_result", "output_amount", "UBIGINT")
            fees = [s.js(t, "swap_result", k, "UBIGINT") for k in ("trading_fee", "protocol_fee", "referral_fee")]
            sqrt, reserve = s.js(t, "swap_result", "next_sqrt_price", "VARCHAR"), s.col(t, "quote_reserve_amount", "UBIGINT")
            threshold, mode, left = s.col(t, "migration_threshold", "UBIGINT"), s.js(t, "swap_parameters", "swap_mode", "UBIGINT"), s.js(t, "swap_result", "amount_left", "UBIGINT")
        else:
            amount_in, amount_out = s.col(t, "included_transfer_fee_amount_in", "UBIGINT"), s.col(t, "excluded_transfer_fee_amount_out", "UBIGINT")
            fees = [s.col(t, k, "UBIGINT") for k in ("trading_fee", "protocol_fee", "referral_fee")]
            sqrt, reserve, threshold, mode, left = s.col(t, "next_sqrt_price", "VARCHAR"), s.col(t, "quote_reserve", "UBIGINT"), "NULL::UBIGINT", s.col(t, "swap_mode", "UBIGINT"), "NULL::UBIGINT"
        parts.append(f"""SELECT {CTX}, '{ev}' AS event, {rank} AS rank, pool, config, {s.col(t, 'trade_direction', 'UBIGINT')} = 1 AS is_buy,
            {s.col(t, 'has_referral', 'BOOLEAN')} AS has_referral, {mode} AS swap_mode, {amount_in} AS amount_in, {left} AS amount_left,
            {amount_out} AS amount_out, {fees[0]} AS trading_fee, {fees[1]} AS protocol_fee, {fees[2]} AS referral_fee,
            {sqrt} AS next_sqrt_price, {reserve} AS quote_reserve_event, {threshold} AS migration_threshold,
            ix_base_mint AS base_mint, ix_quote_mint AS quote_mint
            FROM {src} WHERE NOT failed""")
    if not parts:
        return None
    cost_cols, join = costs_join(s, "e")
    return f"""SELECT e.*, {cost_cols} FROM (
        SELECT * FROM ({' UNION ALL BY NAME '.join(parts)})
        QUALIFY row_number() OVER (PARTITION BY slot, tx_index, outer_ix, parent_inner_ix ORDER BY rank DESC, event_seq_all) = 1) e {join}"""


def damm2_sql(s):
    parts = []
    t = "meteora_damm_v2/EvtSwap2"
    if s.src(t) is not None:
        parts.append(f"""SELECT {CTX}, 'swap' AS kind, pool, {s.col(t, 'trade_direction', 'UBIGINT')} = 0 AS a_to_b,
            {s.col(t, 'collect_fee_mode', 'UBIGINT')} AS collect_fee_mode, {s.col(t, 'has_referral', 'BOOLEAN')} AS has_referral,
            {s.js(t, 'swap_result', 'included_fee_input_amount', 'UBIGINT')} AS amount_in, {s.js(t, 'swap_result', 'amount_left', 'UBIGINT')} AS amount_left,
            {s.js(t, 'swap_result', 'output_amount', 'UBIGINT')} AS amount_out,
            {s.js(t, 'swap_result', 'claiming_fee', 'UBIGINT')} AS claiming_fee, {s.js(t, 'swap_result', 'protocol_fee', 'UBIGINT')} AS protocol_fee,
            {s.js(t, 'swap_result', 'compounding_fee', 'UBIGINT')} AS compounding_fee, {s.js(t, 'swap_result', 'referral_fee', 'UBIGINT')} AS referral_fee,
            {s.js(t, 'swap_result', 'next_sqrt_price', 'VARCHAR')} AS next_sqrt_price,
            {s.col(t, 'reserve_a_amount', 'UBIGINT')} AS reserve_a, {s.col(t, 'reserve_b_amount', 'UBIGINT')} AS reserve_b,
            NULL::UBIGINT AS token_a_amount, NULL::UBIGINT AS token_b_amount, NULL::VARCHAR AS liquidity_delta, NULL::VARCHAR AS position,
            ix_base_mint AS token_a_mint, ix_quote_mint AS token_b_mint
            FROM {s.src(t)} WHERE NOT failed
            QUALIFY row_number() OVER (PARTITION BY slot, tx_index, outer_ix, parent_inner_ix ORDER BY event_seq_all) = 1""")
    t = "meteora_damm_v2/EvtLiquidityChange"
    if s.src(t) is not None:
        parts.append(f"""SELECT {CTX}, CASE {s.col(t, 'change_type', 'UBIGINT')} WHEN 0 THEN 'add' WHEN 1 THEN 'remove' END AS kind, pool,
            NULL::BOOLEAN AS a_to_b, NULL::UBIGINT AS collect_fee_mode, NULL::BOOLEAN AS has_referral, NULL::UBIGINT AS amount_in,
            NULL::UBIGINT AS amount_left, NULL::UBIGINT AS amount_out, NULL::UBIGINT AS claiming_fee, NULL::UBIGINT AS protocol_fee,
            NULL::UBIGINT AS compounding_fee, NULL::UBIGINT AS referral_fee, NULL::VARCHAR AS next_sqrt_price,
            {s.col(t, 'reserve_a_amount', 'UBIGINT')} AS reserve_a, {s.col(t, 'reserve_b_amount', 'UBIGINT')} AS reserve_b,
            {s.col(t, 'token_a_amount', 'UBIGINT')} AS token_a_amount, {s.col(t, 'token_b_amount', 'UBIGINT')} AS token_b_amount,
            {s.col(t, 'liquidity_delta', 'VARCHAR')} AS liquidity_delta, {s.col(t, 'position', 'VARCHAR')} AS position,
            ix_base_mint AS token_a_mint, ix_quote_mint AS token_b_mint
            FROM {s.src(t)} WHERE NOT failed""")
    if not parts:
        return None
    cost_cols, join = costs_join(s, "e")
    return f"SELECT e.*, {cost_cols} FROM ({' UNION ALL BY NAME '.join(parts)}) e {join}"


def launchlab_sql(s):
    t = "raydium_launchlab/TradeEvent"
    if s.src(t) is None:
        return None
    u = lambda k: f"{s.col(t, k, 'UBIGINT')} AS {k}"
    cost_cols, join = costs_join(s, "e")
    return f"""SELECT e.*, {cost_cols} FROM (
        SELECT {CTX}, pool_state AS pool, {s.col(t, 'trade_direction', 'VARCHAR')} = 'Buy' AS is_buy, {s.col(t, 'exact_in', 'BOOLEAN')} AS exact_in,
            {', '.join(u(k) for k in ('amount_in', 'amount_out', 'virtual_base', 'virtual_quote', 'real_base_before', 'real_quote_before',
                                       'real_base_after', 'real_quote_after', 'protocol_fee', 'platform_fee', 'creator_fee', 'share_fee', 'total_base_sell'))},
            {s.col(t, 'pool_status', 'VARCHAR')} AS status, ix_base_mint AS base_mint, ix_quote_mint AS quote_mint
        FROM {s.src(t)} WHERE NOT failed
        QUALIFY row_number() OVER (PARTITION BY slot, tx_index, outer_ix, parent_inner_ix ORDER BY event_seq_all) = 1) e {join}"""


def cpmm_sql(s):
    parts = []
    t = "raydium_cpmm/SwapEvent"
    if s.src(t) is not None:
        u = lambda k: f"{s.col(t, k, 'UBIGINT')} AS {k}"
        parts.append(f"""SELECT {CTX}, 'swap' AS kind, pool_id AS pool, {s.col(t, 'base_input', 'BOOLEAN')} AS base_input,
            {s.col(t, 'input_mint', 'VARCHAR')} AS input_mint, {s.col(t, 'output_mint', 'VARCHAR')} AS output_mint,
            {', '.join(u(k) for k in ('input_vault_before', 'output_vault_before', 'input_amount', 'output_amount', 'input_transfer_fee',
                                       'output_transfer_fee', 'trade_fee', 'creator_fee'))},
            {s.col(t, 'creator_fee_on_input', 'BOOLEAN')} AS creator_fee_on_input,
            NULL::UBIGINT AS lp_amount_before, NULL::UBIGINT AS token_0_vault_before, NULL::UBIGINT AS token_1_vault_before,
            NULL::UBIGINT AS token_0_amount, NULL::UBIGINT AS token_1_amount
            FROM {s.src(t)} WHERE NOT failed
            QUALIFY row_number() OVER (PARTITION BY slot, tx_index, outer_ix, parent_inner_ix ORDER BY event_seq_all) = 1""")
    t = "raydium_cpmm/LpChangeEvent"
    if s.src(t) is not None:
        u = lambda k: f"{s.col(t, k, 'UBIGINT')} AS {k}"
        parts.append(f"""SELECT {CTX}, CASE {s.col(t, 'change_type', 'UBIGINT')} WHEN 0 THEN 'deposit' WHEN 1 THEN 'withdraw' END AS kind,
            pool_id AS pool, NULL::BOOLEAN AS base_input, NULL::VARCHAR AS input_mint, NULL::VARCHAR AS output_mint,
            NULL::UBIGINT AS input_vault_before, NULL::UBIGINT AS output_vault_before, NULL::UBIGINT AS input_amount, NULL::UBIGINT AS output_amount,
            {s.col(t, 'token_0_transfer_fee', 'UBIGINT')} AS input_transfer_fee, {s.col(t, 'token_1_transfer_fee', 'UBIGINT')} AS output_transfer_fee,
            NULL::UBIGINT AS trade_fee, NULL::UBIGINT AS creator_fee, NULL::BOOLEAN AS creator_fee_on_input,
            {', '.join(u(k) for k in ('lp_amount_before', 'token_0_vault_before', 'token_1_vault_before', 'token_0_amount', 'token_1_amount'))}
            FROM {s.src(t)} WHERE NOT failed""")
    if not parts:
        return None
    cost_cols, join = costs_join(s, "e")
    return f"SELECT e.*, {cost_cols} FROM ({' UNION ALL BY NAME '.join(parts)}) e {join}"


def pools_sql(s):
    """Pool creations of one chunk (successful transactions only)."""
    parts = []
    for ev in ("EvtInitializePool", "EvtInitializePoolWithTransferHook"):
        t = f"meteora_dbc/{ev}"
        if s.src(t) is not None:
            parts.append(f"""SELECT 'meteora_dbc' AS venue, pool, {s.col(t, 'base_mint', 'VARCHAR')} AS base_mint, ix_quote_mint AS quote_mint,
                {s.col(t, 'creator', 'VARCHAR')} AS creator, {s.col(t, 'config', 'VARCHAR')} AS config, slot AS create_slot, tx_index AS create_tx_index,
                json_object('event', '{ev}', 'pool_type', {s.col(t, 'pool_type', 'UBIGINT')}, 'activation_point', {s.col(t, 'activation_point', 'UBIGINT')})::VARCHAR AS params
                FROM {s.src(t)} WHERE NOT failed""")
    t = "meteora_damm_v2/EvtInitializePool"
    if s.src(t) is not None:
        keys = ("sqrt_min_price", "sqrt_max_price", "sqrt_price", "liquidity", "collect_fee_mode", "activation_type", "activation_point", "pool_type",
                "token_a_amount", "token_b_amount", "pool_fees")
        obj = ", ".join(f"'{k}', {s.col(t, k, 'VARCHAR')}" for k in keys)
        parts.append(f"""SELECT 'meteora_damm_v2' AS venue, pool, {s.col(t, 'token_a_mint', 'VARCHAR')} AS base_mint, {s.col(t, 'token_b_mint', 'VARCHAR')} AS quote_mint,
            {s.col(t, 'creator', 'VARCHAR')} AS creator, NULL::VARCHAR AS config, slot AS create_slot, tx_index AS create_tx_index,
            json_object({obj})::VARCHAR AS params FROM {s.src(t)} WHERE NOT failed""")
    t = "raydium_launchlab/PoolCreateEvent"
    if s.src(t) is not None:
        obj = ", ".join(f"'{k}', {s.col(t, k, 'VARCHAR')}" for k in ("curve_param", "base_mint_param", "vesting_param", "amm_fee_on"))
        parts.append(f"""SELECT 'raydium_launchlab' AS venue, pool_state AS pool, ix_base_mint AS base_mint, ix_quote_mint AS quote_mint,
            {s.col(t, 'creator', 'VARCHAR')} AS creator, {s.col(t, 'config', 'VARCHAR')} AS config, slot AS create_slot, tx_index AS create_tx_index,
            json_object({obj})::VARCHAR AS params FROM {s.src(t)} WHERE NOT failed""")
    t = "venue_inits"
    if s.src(t) is not None:
        parts.append(f"""SELECT venue, pool, CASE WHEN token_0_mint = '{WSOL}' THEN token_1_mint ELSE token_0_mint END AS base_mint,
            CASE WHEN token_0_mint = '{WSOL}' THEN token_0_mint ELSE token_1_mint END AS quote_mint, creator, NULL::VARCHAR AS config,
            slot AS create_slot, tx_index AS create_tx_index,
            json_object('ix_name', ix_name, 'token_0_mint', token_0_mint, 'token_1_mint', token_1_mint, 'token_0_vault', token_0_vault,
                        'token_1_vault', token_1_vault, 'lp_mint', lp_mint, 'args', ix_args)::VARCHAR AS params
            FROM {s.src(t)}""")
    return " UNION ALL BY NAME ".join(parts) if parts else None


def chunks_for(root, lo, hi):
    """The complete venue chunks that tile [lo, hi) exactly; fails on a gap."""
    chunks = [c for c in data.complete_chunks(root) if c[0] >= lo and c[1] <= hi]
    edge, missing = lo, []
    for c in chunks:
        if c[0] != edge:
            missing.append((edge, c[0]))
        edge = c[1]
    if edge != hi:
        missing.append((edge, hi))
    if missing:
        raise SystemExit(f"[{lo}, {hi}) is not covered by complete chunks of {root}; missing {missing}")
    return chunks


def git_commit():
    try:
        return subprocess.check_output(["git", "-C", os.path.dirname(os.path.abspath(__file__)), "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


TABLES = {"dbc": dbc_sql, "damm2": damm2_sql, "launchlab": launchlab_sql, "cpmm": cpmm_sql, "venue_pools_raw": pools_sql}


def build(period, out_path, chunks, memory="3GB", threads=2, log=print):
    if os.path.exists(out_path):
        raise SystemExit(f"{out_path} exists: a venue store is built once")
    con = data.connect(threads=threads, memory=memory)
    con.execute(f"ATTACH '{out_path}' AS v")
    t0 = time.time()
    made, grads, manifests = set(), False, []
    for n, (lo, hi, d) in enumerate(chunks):
        s = Src((lo, hi, d))
        with open(os.path.join(d, "_manifest.json")) as f:
            man = json.load(f)
        manifests.append({"slots": [lo, hi], "dir": os.path.basename(d), "commit": man.get("commit") or man.get("extractor_commit"),
                          "venues": (man.get("venues") or {}).get("idl_sha256"), "counters": {k: v for k, v in (man.get("counters") or {}).items() if k.startswith("venue")}})
        for table, fn in TABLES.items():
            sql = fn(s)
            if sql is None:
                continue
            con.execute((f"INSERT INTO v.{table} BY NAME " if table in made else f"CREATE TABLE v.{table} AS ") + sql)
            made.add(table)
        g = s.src("venue_graduations")
        if g is not None:
            con.execute(("INSERT INTO v.grad_raw BY NAME " if grads else "CREATE TABLE v.grad_raw AS ") + f"SELECT * FROM {g}")
            grads = True
        log(f"chunk {n + 1}/{len(chunks)} [{lo}, {hi}) {time.time() - t0:.0f} s")
    for table in TABLES:
        if table not in made:
            raise SystemExit(f"no chunk has rows for {table}: refused (an empty input is no evidence)")
    # DBC: quote_reserve from the event, else chained from the pool's nearest earlier anchor (spec §3, §8.1)
    con.execute("""CREATE TABLE v.dbc_new AS
        WITH o AS (SELECT *, CASE WHEN is_buy THEN amount_in::HUGEINT ELSE -amount_out::HUGEINT END AS q_delta,
                          count(quote_reserve_event) OVER w AS grp
                   FROM v.dbc WINDOW w AS (PARTITION BY pool ORDER BY slot, tx_index, outer_ix, parent_inner_ix NULLS FIRST
                                           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)),
             a AS (SELECT *, first_value(quote_reserve_event) OVER g AS anchor, first_value(q_delta) OVER g AS anchor_delta,
                          sum(q_delta) OVER g AS cum
                   FROM o WINDOW g AS (PARTITION BY pool, grp ORDER BY slot, tx_index, outer_ix, parent_inner_ix NULLS FIRST
                                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW))
        SELECT * EXCLUDE (q_delta, grp, anchor, anchor_delta, cum, quote_reserve_event),
               CASE WHEN quote_reserve_event IS NOT NULL THEN quote_reserve_event::HUGEINT
                    WHEN grp > 0 THEN anchor::HUGEINT + cum - anchor_delta END AS quote_reserve,
               CASE WHEN quote_reserve_event IS NOT NULL THEN 'event' WHEN grp > 0 THEN 'chained' END AS reserve_source
        FROM a""")
    con.execute("DROP TABLE v.dbc")
    con.execute("ALTER TABLE v.dbc_new RENAME TO dbc")
    con.execute("""CREATE TABLE v.venue_pools AS SELECT * FROM v.venue_pools_raw
        QUALIFY row_number() OVER (PARTITION BY venue, pool ORDER BY create_slot, create_tx_index) = 1""")
    con.execute("DROP TABLE v.venue_pools_raw")
    if grads:
        con.execute("""CREATE TABLE v.venue_graduations AS SELECT g.*, p.pool IS NOT NULL AS linked
            FROM v.grad_raw g LEFT JOIN v.venue_pools p ON p.venue = g.to_venue AND p.pool = g.to_pool""")
        con.execute("DROP TABLE v.grad_raw")
    else:
        con.execute("CREATE TABLE v.venue_graduations (venue VARCHAR, to_venue VARCHAR, to_pool VARCHAR, linked BOOLEAN)")
    checks = run_checks(con, log)
    counts = {t: con.execute(f"SELECT count(*) FROM v.{t}").fetchone()[0] for t in ("dbc", "damm2", "launchlab", "cpmm", "venue_pools", "venue_graduations")}
    meta = {"period": period, "slots": [chunks[0][0], chunks[-1][1]], "chunks": manifests, "counts": counts, "checks": checks,
            "commit": git_commit(), "built_unix": int(time.time()), "spec": "research/venue-decoders-spec-2026-10-21.md v0.2",
            "note": "illustration only (forward), never for strategy results" if period == "forward" else None}
    con.execute("CREATE TABLE v.meta AS SELECT ? AS json", [json.dumps(meta)])
    con.execute("DETACH v")
    log(json.dumps({"counts": counts, "checks": checks}))
    return meta


def run_checks(con, log=print):
    """Spec §6 / §8 gates on the built store: (name, value, limit, 'green' / 'red')."""
    out = []

    def gate(name, value, limit, ok):
        out.append({"check": name, "value": value, "limit": limit, "status": "green" if ok else "red"})
        log(f"check {name}: {value} (limit {limit}) {'GREEN' if ok else 'RED'}")

    n, chained, none = con.execute("SELECT count(*), count(*) FILTER (WHERE reserve_source = 'chained'), "
                                   "count(*) FILTER (WHERE reserve_source IS NULL) FROM v.dbc").fetchone()
    gate("dbc_chained_share", round(chained / n, 6) if n else 0.0, DBC_CHAINED_MAX, not n or chained / n <= DBC_CHAINED_MAX)
    out.append({"check": "dbc_no_anchor_rows", "value": none, "status": "info"})
    n, bal = con.execute("SELECT count(*) FILTER (WHERE kind = 'swap'), count(*) FILTER (WHERE kind = 'swap' AND source = 'balance') FROM v.cpmm").fetchone()
    gate("cpmm_balance_share", round(bal / n, 6) if n else 0.0, CPMM_BALANCE_MAX, not n or bal / n <= CPMM_BALANCE_MAX)
    n, linked = con.execute("SELECT count(*), count(*) FILTER (WHERE linked) FROM v.venue_graduations "
                            "WHERE to_venue IN ('meteora_damm_v2', 'raydium_cpmm')").fetchone()
    gate("graduations_linked_share", round(linked / n, 6) if n else None, LINK_MIN, not n or linked / n >= LINK_MIN)
    n, cont = con.execute("""SELECT count(*), count(*) FILTER (WHERE real_base_before = pb AND real_quote_before = pq) FROM (
        SELECT real_base_before, real_quote_before, lag(real_base_after) OVER w AS pb, lag(real_quote_after) OVER w AS pq
        FROM v.launchlab WINDOW w AS (PARTITION BY pool ORDER BY slot, tx_index, outer_ix, parent_inner_ix NULLS FIRST)) WHERE pb IS NOT NULL""").fetchone()
    gate("launchlab_continuity", round(cont / n, 6) if n else None, CONTINUITY_MIN, not n or cont / n >= CONTINUITY_MIN)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("period", choices=("dev", "forward"))
    ap.add_argument("--chunks", required=True, help="the venue chunks' root (events/venues-v2/chunks)")
    ap.add_argument("--from-slot", type=int)
    ap.add_argument("--to-slot", type=int)
    ap.add_argument("--out", required=True)
    ap.add_argument("--memory", default=os.environ.get("LAB_DUCKDB_MEM", "3GB"))
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--defaults", default=DEFAULTS)
    a = ap.parse_args(argv)
    with open(a.defaults) as f:
        cfg = yaml.safe_load(f)
    lo, hi = period_range(a.period, cfg, a.from_slot, a.to_slot)
    hold = cfg["windows"]["holdout"]
    if lo < hold["end_slot_exclusive"] and hi > hold["start_slot"]:
        raise SystemExit(f"[{lo}, {hi}) overlaps the hold-out: refused")
    if overlaps_clean(lo, hi):
        raise SystemExit(f"[{lo}, {hi}) overlaps the clean epochs {CLEAN}: refused")
    meta = build(a.period, a.out, chunks_for(a.chunks, lo, hi), a.memory, a.threads)
    return 1 if any(c["status"] == "red" for c in meta["checks"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
