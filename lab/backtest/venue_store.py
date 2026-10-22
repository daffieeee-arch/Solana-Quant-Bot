"""The venue store (venue decoders PR 3; research/venue-decoders-spec-2026-10-21.md v0.2 §2, §3, §6, LAB review on
#506 and #513): Meteora DBC, Meteora DAMM v2, Raydium LaunchLab and Raydium CPMM events of the extractor's venue chunks
(LAB_VENUES=1, events/venues-v2) in one DuckDB file per period. Old Faithful history only.

  python -m backtest.venue_store dev --chunks ROOT [--from-slot S --to-slot E] --out FILE   # development slots only
  python -m backtest.venue_store forward --chunks ROOT --from-slot S --to-slot E --out FILE  # >= epoch 1053, illustration

A store is built once (an existing file is refused) and never grows: a forward range is its own sha-pinned store (spec
§8.3, E32). The hold-out (1047-1051) and the clean epochs (1055-1063) are refused. One chunk is read at a time.

Tables (every event row of a failed transaction is dropped: the runtime recorded it before the failure and rolled the
state back; costs from venue_census on (slot, tx_index)):
  dbc            DBC swaps, one row per swap instruction (slot, tx_index, outer_ix, parent_inner_ix). The program
                 emits EvtSwap only (<= 0.1.5), EvtSwap + EvtSwap2 (0.1.6-0.2.1) or EvtSwap + EvtSwap2 + EvtSwap3 (0.2.2);
                 swap2_with_transfer_hook emits EvtSwap2WithTransferHook (0.2.0-0.2.1) plus EvtSwap3WithTransferHook (0.2.2).
                 The row is taken from the newest layout, EvtSwap3 > EvtSwap2 > EvtSwap (hook variants rank with their base),
                 with migration_threshold and amount_left from the instruction's EvtSwap2 when EvtSwap3 is taken. amount_in /
                 amount_out are what the trader paid / received (EvtSwap's top-level amount_in; EvtSwap2's
                 included_fee_input_amount, vault-side under a Token-2022 transfer fee; EvtSwap3's transfer-fee-included in /
                 excluded out). quote_reserve_delta is the program's exact change of the quote reserve (virtual_pool.rs
                 apply_swap_result: a buy adds the after-fee input, a sell removes the output and the fees); quote_reserve is
                 the event's (EvtSwap2/3, reserve_source 'event', the reserve after the swap) or chained from the pool's
                 nearest earlier anchor plus the deltas since ('chained'; NULL when a delta in between is unknown), NULL
                 without an anchor. Since 0.1.6 every swap carries its reserve, so a chained row points to a decode problem.
                 is_buy: the trader pays quote (trade_direction 1, QuoteToBase). Not every DBC pool is quoted in SOL
                 (1045: 3.6 % of the rows have another quote mint): SOL prices, depth and P&L filter quote_mint = WSOL;
                 meta.checks reports the share per venue (INFO quote_not_wsol).
  damm2          DAMM v2 swaps (EvtSwap2, kind 'swap', a_to_b from trade_direction 0) and liquidity changes
                 (EvtLiquidityChange, kind 'add' / 'remove' from change_type 0 / 1). Swaps: amount_in / amount_out are
                 trader-side (transfer fee included in / excluded out), pool_amount_in / pool_amount_out what reached / left
                 the vault. LP: token_*_amount is pool-side on 'add' and trader-side on 'remove', token_*_amount_gross the
                 transfer-fee-included amount (the trader's on 'add', the reserves' on 'remove'). reserve_a / reserve_b are
                 the pool's tracked liquidity reserves after the event (without unclaimed LP and protocol fees; with the
                 compounding fee in compounding pools), not vault balances; concentrated pools price from next_sqrt_price.
  launchlab      LaunchLab trades (TradeEvent): the curve state before and after, the fees, the pool status (Fund / Migrate /
                 Trade), the pool's curve type (Constant / Fixed / Linear, from its PoolCreateEvent; NULL for a pool created
                 before the period). TradeEvent has no Token-2022 transfer fee: on such mints the trader's amounts differ.
  cpmm           CPMM swaps (SwapEvent, kind 'swap') and LP changes (LpChangeEvent, kind 'deposit' / 'withdraw' from
                 change_type 0 / 1). Swaps: input_amount / output_amount are pool-side (input after its transfer fee,
                 output before it), trader_paid = input_amount + input_transfer_fee, trader_received = output_amount -
                 output_transfer_fee; input_vault_before / output_vault_before are the curve's reserves (vault balances
                 minus accrued protocol, fund and creator fees), on source 'log' rows only. source 'balance' rows (the
                 extractor's fallback from the vaults' token balances) carry the vault deltas, equal to the event's
                 input_amount / output_amount; their fees, trader amounts and reserves are NULL (NULL amounts too when
                 another CPMM instruction of the transaction touches the same pool: balance_ambiguous / missing). LP rows:
                 token_*_amount reach the vault on 'deposit' and the user on 'withdraw'; token_*_transfer_fee per side.
  venue_pools    one row per pool creation: DBC and DAMM v2 EvtInitializePool, LaunchLab PoolCreateEvent, CPMM's initialize
                 rows (venue_inits); base / quote mints (DAMM v2 and CPMM: WSOL is the quote when one side is WSOL, else
                 token_a / token_0 is the base), creator, config, the creation's parameters as JSON (nested values as JSON;
                 enums as their variant names, e.g. CPMM's creator_fee_on "OnlyToken1").
  venue_graduations  the extractor's graduation rows, with linked: the new pool has a venue_pools row (only DAMM v2 and
                 CPMM pools can: DAMM v1 and AMM v4 are not decoded).
  meta           period, chunks (with their extractor commit and venue IDL sha256s), counts, the checks.

Prices follow spec §3 from these columns (not stored): DBC and DAMM v2 (next_sqrt_price / 2^64)^2 in raw units;
LaunchLab Constant curves (virtual_quote + real_quote_after) / (virtual_base - real_base_after) (a Fixed curve
virtual_quote / virtual_base, a Linear one virtual_base * real_base / 2^64), in raw units of the pool's quote mint;
CPMM the reserves before the swap (input_vault_before, output_vault_before; log rows).

Checks (meta.checks; the job is RED on any 'red'): DBC chained share <= 5 % (spec §8.1); DBC reserve continuity: the
previous event reserve plus the exact delta equals the event reserve for >= 99 % of the pool-consecutive pairs; CPMM
balance rows <= 1 % (§8.2); graduations to DAMM v2 / CPMM linked >= 99 % (§6); LaunchLab continuity: real_*_before
equals the pool's previous real_*_after for >= 99 % of the trades (§6); LaunchLab constant product: k after over k before
within 1e-6 for >= 99 % of the trades on Constant pools. INFO: DBC rows without an anchor, CPMM rows not decoded ok,
LaunchLab quote legs (buy gross in, sell net / gross out).
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
        fees = [s.js(t, "swap_result", k, "UBIGINT") if rank < 3 else s.col(t, k, "UBIGINT") for k in ("trading_fee", "protocol_fee", "referral_fee")]
        fee_sum = f"(COALESCE({fees[0]}, 0)::HUGEINT + COALESCE({fees[1]}, 0) + COALESCE({fees[2]}, 0))"
        if ev == "EvtSwap":
            amount_in, amount_out = s.col(t, "amount_in", "UBIGINT"), s.js(t, "swap_result", "output_amount", "UBIGINT")
            q_in, q_out = s.js(t, "swap_result", "actual_input_amount", "UBIGINT"), s.js(t, "swap_result", "output_amount", "UBIGINT")
            sqrt, reserve, threshold, mode, left = s.js(t, "swap_result", "next_sqrt_price", "VARCHAR"), "NULL::UBIGINT", "NULL::UBIGINT", "NULL::UBIGINT", "NULL::UBIGINT"
        elif rank == 2:
            amount_in, amount_out = s.js(t, "swap_result", "included_fee_input_amount", "UBIGINT"), s.js(t, "swap_result", "output_amount", "UBIGINT")
            q_in, q_out = s.js(t, "swap_result", "excluded_fee_input_amount", "UBIGINT"), s.js(t, "swap_result", "output_amount", "UBIGINT")
            sqrt, reserve = s.js(t, "swap_result", "next_sqrt_price", "VARCHAR"), s.col(t, "quote_reserve_amount", "UBIGINT")
            threshold, mode, left = s.col(t, "migration_threshold", "UBIGINT"), s.js(t, "swap_parameters", "swap_mode", "UBIGINT"), s.js(t, "swap_result", "amount_left", "UBIGINT")
        else:
            amount_in, amount_out = s.col(t, "included_transfer_fee_amount_in", "UBIGINT"), s.col(t, "excluded_transfer_fee_amount_out", "UBIGINT")
            # the curve's after-fee input: fees come off the quote input unless they are taken on the base token
            q_in = (f"({s.col(t, 'excluded_transfer_fee_amount_in', 'UBIGINT')}::HUGEINT - CASE WHEN {s.col(t, 'fee_on_base_token', 'BOOLEAN')} "
                    f"THEN 0 ELSE {fee_sum} END)")
            q_out = s.col(t, "included_transfer_fee_amount_out", "UBIGINT")
            sqrt, reserve, threshold, mode, left = s.col(t, "next_sqrt_price", "VARCHAR"), s.col(t, "quote_reserve", "UBIGINT"), "NULL::UBIGINT", s.col(t, "swap_mode", "UBIGINT"), "NULL::UBIGINT"
        is_buy = f"{s.col(t, 'trade_direction', 'UBIGINT')} = 1"
        # virtual_pool.rs apply_swap_result: a buy adds the after-fee input to the quote reserve; a sell takes the output
        # and the fees (sells always pay their fees in quote, out of the output)
        delta = f"CASE WHEN {is_buy} THEN ({q_in})::HUGEINT ELSE -(({q_out})::HUGEINT + {fee_sum}) END"
        parts.append(f"""SELECT {CTX}, '{ev}' AS event, {rank} AS rank, pool, config, {is_buy} AS is_buy,
            {s.col(t, 'has_referral', 'BOOLEAN')} AS has_referral, {mode} AS swap_mode, {amount_in} AS amount_in, {left} AS amount_left_event,
            {amount_out} AS amount_out, {fees[0]} AS trading_fee, {fees[1]} AS protocol_fee, {fees[2]} AS referral_fee,
            {sqrt} AS next_sqrt_price, {reserve} AS quote_reserve_event, {threshold} AS migration_threshold_event, {delta} AS quote_reserve_delta,
            ix_base_mint AS base_mint, ix_quote_mint AS quote_mint
            FROM {src} WHERE NOT failed""")
    if not parts:
        return None
    cost_cols, join = costs_join(s, "e")
    parent = "PARTITION BY slot, tx_index, outer_ix, parent_inner_ix"
    return f"""SELECT e.* EXCLUDE (amount_left_event, migration_threshold_event), {cost_cols} FROM (
        SELECT *, max(amount_left_event) OVER ({parent}) AS amount_left, max(migration_threshold_event) OVER ({parent}) AS migration_threshold
        FROM ({' UNION ALL BY NAME '.join(parts)})
        QUALIFY row_number() OVER ({parent} ORDER BY rank DESC, event_seq_all) = 1) e {join}"""


def damm2_sql(s):
    parts = []
    t = "meteora_damm_v2/EvtSwap2"
    if s.src(t) is not None:
        parts.append(f"""SELECT {CTX}, 'swap' AS kind, pool, {s.col(t, 'trade_direction', 'UBIGINT')} = 0 AS a_to_b,
            {s.col(t, 'collect_fee_mode', 'UBIGINT')} AS collect_fee_mode, {s.col(t, 'has_referral', 'BOOLEAN')} AS has_referral,
            {s.col(t, 'included_transfer_fee_amount_in', 'UBIGINT')} AS amount_in, {s.col(t, 'excluded_transfer_fee_amount_out', 'UBIGINT')} AS amount_out,
            {s.js(t, 'swap_result', 'included_fee_input_amount', 'UBIGINT')} AS pool_amount_in, {s.col(t, 'included_transfer_fee_amount_out', 'UBIGINT')} AS pool_amount_out,
            {s.js(t, 'swap_result', 'amount_left', 'UBIGINT')} AS amount_left,
            {s.js(t, 'swap_result', 'claiming_fee', 'UBIGINT')} AS claiming_fee, {s.js(t, 'swap_result', 'protocol_fee', 'UBIGINT')} AS protocol_fee,
            {s.js(t, 'swap_result', 'compounding_fee', 'UBIGINT')} AS compounding_fee, {s.js(t, 'swap_result', 'referral_fee', 'UBIGINT')} AS referral_fee,
            {s.js(t, 'swap_result', 'next_sqrt_price', 'VARCHAR')} AS next_sqrt_price,
            {s.col(t, 'reserve_a_amount', 'UBIGINT')} AS reserve_a, {s.col(t, 'reserve_b_amount', 'UBIGINT')} AS reserve_b,
            NULL::UBIGINT AS token_a_amount, NULL::UBIGINT AS token_b_amount, NULL::UBIGINT AS token_a_amount_gross, NULL::UBIGINT AS token_b_amount_gross,
            NULL::VARCHAR AS liquidity_delta, NULL::VARCHAR AS position,
            ix_base_mint AS token_a_mint, ix_quote_mint AS token_b_mint
            FROM {s.src(t)} WHERE NOT failed
            QUALIFY row_number() OVER (PARTITION BY slot, tx_index, outer_ix, parent_inner_ix ORDER BY event_seq_all) = 1""")
    t = "meteora_damm_v2/EvtLiquidityChange"
    if s.src(t) is not None:
        parts.append(f"""SELECT {CTX}, CASE {s.col(t, 'change_type', 'UBIGINT')} WHEN 0 THEN 'add' WHEN 1 THEN 'remove' END AS kind, pool,
            NULL::BOOLEAN AS a_to_b, NULL::UBIGINT AS collect_fee_mode, NULL::BOOLEAN AS has_referral, NULL::UBIGINT AS amount_in,
            NULL::UBIGINT AS amount_out, NULL::UBIGINT AS pool_amount_in, NULL::UBIGINT AS pool_amount_out,
            NULL::UBIGINT AS amount_left, NULL::UBIGINT AS claiming_fee, NULL::UBIGINT AS protocol_fee,
            NULL::UBIGINT AS compounding_fee, NULL::UBIGINT AS referral_fee, NULL::VARCHAR AS next_sqrt_price,
            {s.col(t, 'reserve_a_amount', 'UBIGINT')} AS reserve_a, {s.col(t, 'reserve_b_amount', 'UBIGINT')} AS reserve_b,
            {s.col(t, 'token_a_amount', 'UBIGINT')} AS token_a_amount, {s.col(t, 'token_b_amount', 'UBIGINT')} AS token_b_amount,
            {s.col(t, 'transfer_fee_included_token_a_amount', 'UBIGINT')} AS token_a_amount_gross,
            {s.col(t, 'transfer_fee_included_token_b_amount', 'UBIGINT')} AS token_b_amount_gross,
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
        log_only = lambda k: f"CASE WHEN source = 'log' THEN {s.col(t, k, 'UBIGINT')} END AS {k}"
        parts.append(f"""SELECT {CTX}, 'swap' AS kind, pool_id AS pool, {s.col(t, 'base_input', 'BOOLEAN')} AS base_input,
            {s.col(t, 'input_mint', 'VARCHAR')} AS input_mint, {s.col(t, 'output_mint', 'VARCHAR')} AS output_mint,
            {log_only('input_vault_before')}, {log_only('output_vault_before')},
            {', '.join(u(k) for k in ('input_amount', 'output_amount', 'input_transfer_fee', 'output_transfer_fee', 'trade_fee', 'creator_fee'))},
            {s.col(t, 'input_amount', 'UBIGINT')}::HUGEINT + {s.col(t, 'input_transfer_fee', 'UBIGINT')} AS trader_paid,
            {s.col(t, 'output_amount', 'UBIGINT')}::HUGEINT - {s.col(t, 'output_transfer_fee', 'UBIGINT')} AS trader_received,
            {s.col(t, 'creator_fee_on_input', 'BOOLEAN')} AS creator_fee_on_input,
            NULL::UBIGINT AS lp_amount_before, NULL::UBIGINT AS token_0_vault_before, NULL::UBIGINT AS token_1_vault_before,
            NULL::UBIGINT AS token_0_amount, NULL::UBIGINT AS token_1_amount, NULL::UBIGINT AS token_0_transfer_fee, NULL::UBIGINT AS token_1_transfer_fee
            FROM {s.src(t)} WHERE NOT failed
            QUALIFY row_number() OVER (PARTITION BY slot, tx_index, outer_ix, parent_inner_ix ORDER BY event_seq_all) = 1""")
    t = "raydium_cpmm/LpChangeEvent"
    if s.src(t) is not None:
        u = lambda k: f"{s.col(t, k, 'UBIGINT')} AS {k}"
        parts.append(f"""SELECT {CTX}, CASE {s.col(t, 'change_type', 'UBIGINT')} WHEN 0 THEN 'deposit' WHEN 1 THEN 'withdraw' END AS kind,
            pool_id AS pool, NULL::BOOLEAN AS base_input, NULL::VARCHAR AS input_mint, NULL::VARCHAR AS output_mint,
            NULL::UBIGINT AS input_vault_before, NULL::UBIGINT AS output_vault_before, NULL::UBIGINT AS input_amount, NULL::UBIGINT AS output_amount,
            NULL::UBIGINT AS input_transfer_fee, NULL::UBIGINT AS output_transfer_fee, NULL::UBIGINT AS trade_fee, NULL::UBIGINT AS creator_fee,
            NULL::HUGEINT AS trader_paid, NULL::HUGEINT AS trader_received, NULL::BOOLEAN AS creator_fee_on_input,
            {', '.join(u(k) for k in ('lp_amount_before', 'token_0_vault_before', 'token_1_vault_before', 'token_0_amount', 'token_1_amount',
                                       'token_0_transfer_fee', 'token_1_transfer_fee'))}
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
        obj = ", ".join(f"'{k}', {s.col(t, k, 'JSON' if k == 'pool_fees' else 'VARCHAR')}" for k in keys)
        a_, b_ = s.col(t, "token_a_mint", "VARCHAR"), s.col(t, "token_b_mint", "VARCHAR")
        parts.append(f"""SELECT 'meteora_damm_v2' AS venue, pool, CASE WHEN {a_} = '{WSOL}' THEN {b_} ELSE {a_} END AS base_mint,
            CASE WHEN {a_} = '{WSOL}' THEN {a_} ELSE {b_} END AS quote_mint,
            {s.col(t, 'creator', 'VARCHAR')} AS creator, NULL::VARCHAR AS config, slot AS create_slot, tx_index AS create_tx_index,
            json_object({obj})::VARCHAR AS params FROM {s.src(t)} WHERE NOT failed""")
    t = "raydium_launchlab/PoolCreateEvent"
    if s.src(t) is not None:
        obj = ", ".join(f"'{k}', {s.col(t, k, 'JSON' if k != 'amm_fee_on' else 'VARCHAR')}" for k in ("curve_param", "base_mint_param", "vesting_param", "amm_fee_on"))
        parts.append(f"""SELECT 'raydium_launchlab' AS venue, pool_state AS pool, ix_base_mint AS base_mint, ix_quote_mint AS quote_mint,
            {s.col(t, 'creator', 'VARCHAR')} AS creator, {s.col(t, 'config', 'VARCHAR')} AS config, slot AS create_slot, tx_index AS create_tx_index,
            json_object({obj})::VARCHAR AS params FROM {s.src(t)} WHERE NOT failed""")
    t = "venue_inits"
    if s.src(t) is not None:
        parts.append(f"""SELECT venue, pool, CASE WHEN token_0_mint = '{WSOL}' THEN token_1_mint ELSE token_0_mint END AS base_mint,
            CASE WHEN token_0_mint = '{WSOL}' THEN token_0_mint ELSE token_1_mint END AS quote_mint, creator, NULL::VARCHAR AS config,
            slot AS create_slot, tx_index AS create_tx_index,
            json_object('ix_name', ix_name, 'token_0_mint', token_0_mint, 'token_1_mint', token_1_mint, 'token_0_vault', token_0_vault,
                        'token_1_vault', token_1_vault, 'lp_mint', lp_mint, 'args', TRY_CAST(ix_args AS JSON))::VARCHAR AS params
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
    """Built into <out>.part and renamed at the end; a failed build leaves no file (LAB review on 58d306d)."""
    part = out_path + ".part"
    for p in (out_path, part):
        if os.path.exists(p):
            raise SystemExit(f"{p} exists: a venue store is built once")
    try:
        meta = _build(period, part, chunks, memory, threads, log)
    except BaseException:
        if os.path.exists(part):
            os.remove(part)
        raise
    os.replace(part, out_path)
    return meta


def _build(period, out_path, chunks, memory, threads, log):
    con = data.connect(threads=threads, memory=memory)
    # a bounded spill (LAB_DUCKDB_TMP): the default would be the whole free disk
    con.execute(f"SET max_temp_directory_size = '{os.environ.get('LAB_SPILL_MAX', '8GB')}'")
    con.execute(f"ATTACH '{out_path}' AS v")
    t0 = time.time()
    made, grads, manifests = set(), False, []
    for n, (lo, hi, d) in enumerate(chunks):
        s = Src((lo, hi, d))
        with open(os.path.join(d, "_manifest.json")) as f:
            man = json.load(f)
        # the extractor's manifest names its binary by sha256 (extractor_sha256), not by commit (LAB review on #518)
        manifests.append({"slots": [lo, hi], "dir": os.path.basename(d), "extractor_sha256": man.get("extractor_sha256"),
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
        WITH o AS (SELECT *, quote_reserve_delta AS q_delta, count(quote_reserve_event) OVER w AS grp
                   FROM v.dbc WINDOW w AS (PARTITION BY pool ORDER BY slot, tx_index, outer_ix, parent_inner_ix NULLS FIRST
                                           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)),
             a AS (SELECT *, first_value(quote_reserve_event) OVER g AS anchor, first_value(q_delta) OVER g AS anchor_delta,
                          sum(q_delta) OVER g AS cum, count(*) OVER g - count(q_delta) OVER g AS unknown_deltas
                   FROM o WINDOW g AS (PARTITION BY pool, grp ORDER BY slot, tx_index, outer_ix, parent_inner_ix NULLS FIRST
                                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW))
        SELECT * EXCLUDE (q_delta, grp, anchor, anchor_delta, cum, unknown_deltas),
               CASE WHEN quote_reserve_event IS NOT NULL THEN quote_reserve_event::HUGEINT
                    WHEN grp > 0 AND unknown_deltas = 0 THEN anchor::HUGEINT + cum - anchor_delta END AS quote_reserve,
               CASE WHEN quote_reserve_event IS NOT NULL THEN 'event' WHEN grp > 0 AND unknown_deltas = 0 THEN 'chained' END AS reserve_source
        FROM a""")
    con.execute("DROP TABLE v.dbc")
    con.execute("ALTER TABLE v.dbc_new RENAME TO dbc")
    con.execute("""CREATE TABLE v.venue_pools AS SELECT * FROM v.venue_pools_raw
        QUALIFY row_number() OVER (PARTITION BY venue, pool ORDER BY create_slot, create_tx_index) = 1""")
    con.execute("DROP TABLE v.venue_pools_raw")
    # the LaunchLab curve type of each trade's pool (Constant / Fixed / Linear: the CurveParams variant)
    con.execute("""CREATE TABLE v.launchlab_new AS SELECT l.*, json_keys(json_extract(p.params, '$.curve_param'))[1] AS curve
        FROM v.launchlab l LEFT JOIN v.venue_pools p ON p.venue = 'raydium_launchlab' AND p.pool = l.pool""")
    con.execute("DROP TABLE v.launchlab")
    con.execute("ALTER TABLE v.launchlab_new RENAME TO launchlab")
    if grads:
        con.execute("""CREATE TABLE v.venue_graduations AS SELECT g.*, p.pool IS NOT NULL AS linked
            FROM v.grad_raw g LEFT JOIN v.venue_pools p ON p.venue = g.to_venue AND p.pool = g.to_pool""")
        con.execute("DROP TABLE v.grad_raw")
    else:
        con.execute("CREATE TABLE v.venue_graduations (venue VARCHAR, to_venue VARCHAR, to_pool VARCHAR, linked BOOLEAN)")
    counts = {t: con.execute(f"SELECT count(*) FROM v.{t}").fetchone()[0] for t in ("dbc", "damm2", "launchlab", "cpmm", "venue_pools", "venue_graduations")}
    empty = [t for t in ("dbc", "damm2", "launchlab", "cpmm", "venue_pools") if not counts[t]]
    if empty:
        raise SystemExit(f"no rows in {empty}: refused (an empty input is no evidence; #419)")
    checks = run_checks(con, log, strict=period == "dev")
    meta = {"period": period, "slots": [chunks[0][0], chunks[-1][1]], "chunks": manifests, "counts": counts, "checks": checks,
            "commit": git_commit(), "built_unix": int(time.time()), "spec": "research/venue-decoders-spec-2026-10-21.md v0.2",
            "note": "illustration only (forward), never for strategy results" if period == "forward" else None}
    con.execute("CREATE TABLE v.meta AS SELECT ? AS json", [json.dumps(meta)])
    con.execute("DETACH v")
    log(json.dumps({"counts": counts, "checks": checks}))
    return meta


def run_checks(con, log=print, strict=True):
    """Spec §6 / §8 gates on the built store: (name, value, limit, 'green' / 'red'). A gate without rows is red (no
    evidence); on a short window (strict False: a forward smoke) the graduation link and the constant-product gates
    without rows are 'info' with a value that says so (a smoke may hold no graduation, and its pools' curves are known
    only for pools created inside it)."""
    out = []

    def gate(name, value, limit, ok, n=1, empty_info=False):
        if not n:
            status = "info" if empty_info and not strict else "red"
            value = "no rows to check" + (" (a short window)" if status == "info" else "")
        else:
            status = "green" if ok else "red"
        out.append({"check": name, "value": value, "limit": limit, "status": status})
        log(f"check {name}: {value} (limit {limit}) {status.upper()}")

    n, chained, none = con.execute("SELECT count(*), count(*) FILTER (WHERE reserve_source = 'chained'), "
                                   "count(*) FILTER (WHERE reserve_source IS NULL) FROM v.dbc").fetchone()
    gate("dbc_chained_share", round(chained / n, 6) if n else None, DBC_CHAINED_MAX, n and chained / n <= DBC_CHAINED_MAX, n)
    out.append({"check": "dbc_no_anchor_rows", "value": none, "status": "info"})
    n, ok = con.execute("""SELECT count(*), count(*) FILTER (WHERE quote_reserve_event = pq + quote_reserve_delta) FROM (
        SELECT quote_reserve_event, quote_reserve_delta, lag(quote_reserve_event) OVER (PARTITION BY pool ORDER BY slot, tx_index, outer_ix,
               parent_inner_ix NULLS FIRST) AS pq FROM v.dbc) WHERE quote_reserve_event IS NOT NULL AND pq IS NOT NULL""").fetchone()
    gate("dbc_reserve_continuity", round(ok / n, 6) if n else None, CONTINUITY_MIN, n and ok / n >= CONTINUITY_MIN, n)
    n, bal = con.execute("SELECT count(*) FILTER (WHERE kind = 'swap'), count(*) FILTER (WHERE kind = 'swap' AND source = 'balance') FROM v.cpmm").fetchone()
    gate("cpmm_balance_share", round(bal / n, 6) if n else None, CPMM_BALANCE_MAX, n and bal / n <= CPMM_BALANCE_MAX, n)
    n, linked = con.execute("SELECT count(*), count(*) FILTER (WHERE linked) FROM v.venue_graduations "
                            "WHERE to_venue IN ('meteora_damm_v2', 'raydium_cpmm')").fetchone()
    gate("graduations_linked_share", round(linked / n, 6) if n else None, LINK_MIN, n and linked / n >= LINK_MIN, n, empty_info=True)
    n, cont = con.execute("""SELECT count(*), count(*) FILTER (WHERE real_base_before = pb AND real_quote_before = pq) FROM (
        SELECT real_base_before, real_quote_before, lag(real_base_after) OVER w AS pb, lag(real_quote_after) OVER w AS pq
        FROM v.launchlab WINDOW w AS (PARTITION BY pool ORDER BY slot, tx_index, outer_ix, parent_inner_ix NULLS FIRST)) WHERE pb IS NOT NULL""").fetchone()
    gate("launchlab_continuity", round(cont / n, 6) if n else None, CONTINUITY_MIN, n and cont / n >= CONTINUITY_MIN, n)
    n, cp = con.execute("""SELECT count(*), count(*) FILTER (WHERE abs((virtual_quote::DOUBLE + real_quote_after) * (virtual_base::DOUBLE - real_base_after)
        / nullif((virtual_quote::DOUBLE + real_quote_before) * (virtual_base::DOUBLE - real_base_before), 0) - 1) < 1e-6)
        FROM v.launchlab WHERE curve = 'Constant'""").fetchone()
    gate("launchlab_constant_product", round(cp / n, 6) if n else None, CONTINUITY_MIN, n and cp / n >= CONTINUITY_MIN, n, empty_info=True)
    out.append({"check": "launchlab_curves", "value": dict(con.execute("SELECT COALESCE(curve, '(pool before the period)'), count(*) FROM v.launchlab "
                                                                       "GROUP BY 1 ORDER BY 1").fetchall()), "status": "info"})
    legs = con.execute("""SELECT count(*) FILTER (WHERE is_buy),
        count(*) FILTER (WHERE is_buy AND real_quote_after::HUGEINT - real_quote_before = amount_in::HUGEINT - (protocol_fee + platform_fee + creator_fee + share_fee)),
        count(*) FILTER (WHERE NOT is_buy),
        count(*) FILTER (WHERE NOT is_buy AND real_quote_before::HUGEINT - real_quote_after = amount_out::HUGEINT + protocol_fee + platform_fee + creator_fee + share_fee),
        count(*) FILTER (WHERE NOT is_buy AND real_quote_before::HUGEINT - real_quote_after = amount_out) FROM v.launchlab""").fetchone()
    out.append({"check": "launchlab_quote_legs", "value": dict(zip(("buys", "buy_gross_in", "sells", "sell_net_out", "sell_gross_out"), legs)),
                "status": "info"})
    out.append({"check": "quote_not_wsol", "status": "info", "value": {
        "dbc": con.execute(f"SELECT round(avg((quote_mint IS DISTINCT FROM '{WSOL}')::INT), 6) FROM v.dbc").fetchone()[0],
        "launchlab": con.execute(f"SELECT round(avg((quote_mint IS DISTINCT FROM '{WSOL}')::INT), 6) FROM v.launchlab").fetchone()[0],
        "venue_pools": dict(con.execute(f"SELECT venue, round(avg((quote_mint IS DISTINCT FROM '{WSOL}')::INT), 6) FROM v.venue_pools "
                                        "GROUP BY 1 ORDER BY 1").fetchall())}})
    out.append({"check": "cpmm_not_ok_rows", "value": dict(con.execute("SELECT decode_status, count(*) FROM v.cpmm WHERE decode_status <> 'ok' "
                                                                       "GROUP BY 1 ORDER BY 1").fetchall()), "status": "info"})
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
