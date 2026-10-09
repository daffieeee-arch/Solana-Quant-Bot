"""Build a compact DuckDB store for one period of the event dataset.

  python -m backtest.store dev                 # slots before the hold-out (safe to iterate on)
  python -m backtest.store holdout --final     # only for the single final tournament run

The hold-out store can be built once: a lock file records when and from which commit. The dev
store never contains a hold-out slot, so strategy code that only opens the dev store cannot
peek at it.

Tables (sorted by token/pool and chain order):
  mints   pump CreateEvent + lifecycle (complete slot, migration pool)
  curve   pump TradeEvent with pre/post reserves, fees, flags and instruction limits
  pools   PumpSwap pools whose base mint was created in the loaded data
  pool    PumpSwap events of those pools with pre-trade (B, E) state and limits
  blocks  slot -> block_time
Universe flags are kept as columns; strategies filter (SOL quote, non-mayhem, burn-in).
"""

import argparse
import json
import os
import subprocess
import time

import yaml

from . import data

DEFAULTS = "/home/chupa/Solana-project/data-old-faithful-one/lab/research/week2-defaults.yaml"
STORE_DIR = "/home/chupa/Solana-project/data-old-faithful-one/lab/store"
H = "::HUGEINT"


def load_defaults(path=DEFAULTS):
    with open(path) as f:
        return yaml.safe_load(f)


def period_chunks(period, cfg):
    hold = cfg["windows"]["holdout"]
    chunks = data.complete_chunks()
    if period == "dev":
        return [c for c in chunks if c[1] <= hold["start_slot"]]
    if period == "holdout":
        return [c for c in chunks if c[0] >= hold["start_slot"] and c[1] <= hold["end_slot_exclusive"]]
    raise ValueError(period)


def arg(key, cast="HUGEINT"):
    return f"TRY_CAST(json_extract_string(parent_ix_args, '$.{key}') AS {cast})"


def build(period, out_path, cfg):
    chunks = period_chunks(period, cfg)
    if not chunks:
        raise SystemExit(f"no complete chunks for period {period}")
    lo, hi = chunks[0][0], chunks[-1][1]
    gaps = [(a[1], b[0]) for a, b in zip(chunks, chunks[1:]) if a[1] != b[0]]
    if gaps:
        raise SystemExit(f"period {period} has gaps between complete chunks: {gaps}")
    tmp = out_path + ".tmp"
    for p in (tmp, tmp + ".wal"):
        if os.path.exists(p):
            os.remove(p)
    con = data.connect()
    con.execute(f"ATTACH '{tmp}' AS s")
    P = lambda t: data.parquet(t, chunks)  # noqa: E731
    ix_case = " ".join(f"WHEN '{d}' THEN '{n}'" for d, n in data.PUMP_IX.items())

    con.execute(f"CREATE TABLE s.blocks AS SELECT slot, any_value(block_time) AS block_time FROM {P('blocks')} "
                "WHERE NOT skipped GROUP BY slot ORDER BY slot")

    con.execute(f"""
      CREATE TABLE s.mints AS
      WITH c AS (
        SELECT mint, slot AS create_slot, tx_index AS create_tx, "user" AS creator, creator AS creator_field,
               COALESCE(is_mayhem_mode, false) AS mayhem, COALESCE(quote_mint, '11111111111111111111111111111111') AS quote_mint,
               COALESCE(is_cashback_enabled, false) AS cashback, COALESCE(is_holder_reward, false) AS holder_reward,
               token_total_supply AS supply, name, symbol
        FROM {P('pump/CreateEvent')} QUALIFY row_number() OVER (PARTITION BY mint ORDER BY slot, tx_index) = 1),
      done AS (SELECT mint, min(slot) AS complete_slot FROM {P('pump/CompleteEvent')} GROUP BY mint),
      mig AS (SELECT mint, min(slot) AS migrate_slot, any_value(pool) AS pool FROM {P('pump/CompletePumpAmmMigrationEvent')} GROUP BY mint)
      SELECT c.*, done.complete_slot, mig.migrate_slot, mig.pool
      FROM c LEFT JOIN done USING (mint) LEFT JOIN mig USING (mint) ORDER BY create_slot, create_tx""")

    con.execute(f"""
      CREATE TABLE s.curve AS
      SELECT t.mint, t.slot, t.tx_index, t.outer_ix, t.inner_ix,
        CASE t.parent_ix_disc {ix_case} ELSE 'other' END AS variant,
        t.is_buy, t.token_amount{H} AS t, COALESCE(t.quote_amount, t.sol_amount){H} AS q,
        t.fee{H} AS fee, t.creator_fee{H} AS creator_fee, t.fee_basis_points::INTEGER AS fee_bps,
        t.creator_fee_basis_points::INTEGER AS creator_bps,
        t.virtual_token_reserves{H} AS vt, COALESCE(t.virtual_quote_reserves, t.virtual_sol_reserves){H} AS vq,
        t.real_token_reserves{H} AS rt, COALESCE(t.real_quote_reserves, t.real_sol_reserves){H} AS rq,
        t."user" AS trader, t.fee_payer, t.priority_fee, t.jito_tip, t.tx_fee,
        COALESCE(t.mayhem_mode, false) AS mayhem_trade,
        {arg('amount')} AS arg_amount,
        COALESCE({arg('spendable_sol_in')}, {arg('spendable_quote_in')}) AS arg_budget,
        {arg('max_sol_cost')} AS arg_max_cost,
        {arg('min_tokens_out')} AS arg_min_tokens,
        {arg('min_sol_output')} AS arg_min_quote_out
      FROM {P('pump/TradeEvent')} t
      SEMI JOIN s.mints m ON m.mint = t.mint
      ORDER BY t.mint, t.slot, t.tx_index, t.outer_ix, t.inner_ix""")

    con.execute(f"""
      CREATE TABLE s.pools AS
      SELECT p.pool, p.base_mint AS mint, p.quote_mint, p.slot AS create_slot, p.coin_creator,
             COALESCE(p.is_mayhem_mode, false) AS mayhem, p.pool_base_amount{H} AS b0, p.pool_quote_amount{H} AS q0
      FROM {P('pump_amm/CreatePoolEvent')} p SEMI JOIN s.mints m ON m.mint = p.base_mint
      QUALIFY row_number() OVER (PARTITION BY p.pool ORDER BY p.slot) = 1""")

    eff = f"(pool_quote_token_reserves{H} + COALESCE(TRY_CAST(virtual_quote_reserves AS HUGEINT), 0))"
    common = "pool, slot, tx_index, outer_ix, inner_ix, \"user\" AS trader, fee_payer, priority_fee, jito_tip, tx_fee, parent_ix_disc"
    fees = ("lp_fee_basis_points::INTEGER AS lp_bps, protocol_fee_basis_points::INTEGER AS protocol_bps, "
            "COALESCE(coin_creator_fee_basis_points, 0)::INTEGER AS creator_bps")
    con.execute(f"""
      CREATE TABLE s.pool AS
      SELECT * FROM (
        SELECT 'buy' AS kind, {common}, pool_base_token_reserves{H} AS b, {eff} AS e, {fees},
               base_amount_out{H} AS base, quote_amount_in{H} AS quote_gross, user_quote_amount_in{H} AS quote_net,
               max_quote_amount_in{H} AS limit_quote, min_base_amount_out{H} AS limit_base
        FROM {P('pump_amm/BuyEvent')}
        UNION ALL BY NAME
        SELECT 'sell' AS kind, {common}, pool_base_token_reserves{H} AS b, {eff} AS e, {fees},
               base_amount_in{H} AS base, quote_amount_out{H} AS quote_gross, user_quote_amount_out{H} AS quote_net,
               min_quote_amount_out{H} AS limit_quote, NULL::HUGEINT AS limit_base
        FROM {P('pump_amm/SellEvent')}
        UNION ALL BY NAME
        SELECT 'boost' AS kind, pool, slot, tx_index, outer_ix, inner_ix, authority AS trader, fee_payer,
               priority_fee, jito_tip, tx_fee, parent_ix_disc,
               base_reserves_after{H} AS b_after, real_quote_reserves_after{H} + TRY_CAST(virtual_quote_reserves AS HUGEINT) AS e_after,
               base_amount_burned{H} AS base, quote_amount_in_used{H} AS quote_gross, boost_vault_remaining{H} AS boost_left
        FROM {P('pump_amm/BoostBuyAndBurnEvent')}
      ) x SEMI JOIN s.pools p ON p.pool = x.pool
      ORDER BY pool, slot, tx_index, outer_ix, inner_ix""")

    counts = {t: con.execute(f"SELECT count(*) FROM s.{t}").fetchone()[0] for t in ("blocks", "mints", "curve", "pools", "pool")}
    meta = {
        "period": period, "slot_start": lo, "slot_end_exclusive": hi, "chunks": len(chunks),
        "built_at_unix": int(time.time()), "counts": counts,
        "git_commit": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                                     cwd=os.path.dirname(__file__)).stdout.strip(),
    }
    con.execute("CREATE TABLE s.meta AS SELECT ? AS json", [json.dumps(meta)])
    con.execute("DETACH s")
    con.close()
    os.replace(tmp, out_path)
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("period", choices=["dev", "holdout"])
    ap.add_argument("--final", action="store_true", help="required for the one-time hold-out build")
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
    meta = build(a.period, os.path.join(a.out_dir, f"{a.period}.duckdb"), cfg)
    if a.period == "holdout":
        with open(lock, "w") as f:
            json.dump(meta, f)
    print(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main()
