"""Spec A: follow the money (week3-specs-money-2026-10-12.md). P&L per wallet x token x UTC day on the
pump curve and PumpSwap (SOL-quote pools, normal and reversed; pools with another quote have no SOL
price and are left out), from every trade in the store:

  cash        SOL received for sells - SOL paid for buys, user side (pool: sol_amount; curve: q plus the
              curve and creator fee on a buy, minus them on a sell)
  fees        tx_fee + priority_fee + jito_tip once per transaction (slot, tx_index), to the trader of
              the transaction's legs; `paid_by_other` counts transactions whose fee payer differs
  inventory   tokens bought - sold per wallet x token, floor 0 (tokens from before the period or from a
              transfer are not ours to count)
  value       inventory x the token's last price at the day's end (any wallet, curve and pool together),
              capped at the real SOL the venue held then (what it could pay at most)
  pnl         cash + value_end - value_end of the previous day: the daily mark-to-market, so the sum over
              wallets per day closes and a token that goes to zero is a loss on the day it fell.
              An open position is carried (marked) on every later day until it is sold or the period
              ends, unless it is dust (< DUST SOL at its last mark).

Positions (one per wallet x token, opened when the inventory goes from 0 to > 0) give the behaviour
features the report needs for the labels: opened in the create slot + SNIPE_SLOTS (sniper), the hold
until the first leg that leaves <= 10 % of the position's maximum (bot), the buy size (whale), and the
store's farmer flag on pool legs (farmer). All of it per wallet x day, so the report can label on any
window (days 1-9, or a late wallet's first two full days) without touching the store again.

  python -m wallets.money --from-slot A --to-slot B [--buckets 8] [--out DIR]

Writes marks.parquet (token x day: last price, depth, venue), wallet-day-<bucket>.parquet, payers.parquet
(fee payer x wallet x token with the first slot and day of the link, for the creator link and the service-payer
rule), creators.parquet (wallet, mint, create slot and day)
lp_legs.parquet (every LP deposit / withdraw leg of a SOL-priced pool with the SOL moved and the depth before and after:
the report's LP-drain section) and venue_flows.parquet (per venue kind: venue fees that left in SOL, network fees, the traders' net cash, SOL in
the venues at the start and the end, and the SOL that entered from outside the trades (LP deposits minus
withdrawals; a pool's opening deposit is the pre-depth of its first trade); network fees are not split per
venue (they are per transaction and already summed per wallet x day); the zero-sum identity is cash_all + venue_fees + (sol_end - sol_start) - external_in = 0). Slots stay before the hold-out. Heavy: NAS.
"""

import argparse
import json
import os
import sys
import time

from .common import OUT_ROOT, SOL, connect, git_commit, guard

SNIPE_SLOTS = 3
EXIT_SHARE = 0.10
DUST = SOL // 1000  # 0.001 SOL
SOL_QUOTES = ("11111111111111111111111111111111", "So11111111111111111111111111111111111111112")


def legs_sql(lo, hi, bucket=None, buckets=1, fees=True):
    """Every trade leg in [lo, hi) on a SOL-priced venue, user side, with the per-transaction fee on one leg
    (fees=False: no fee window, for the marks and the side tables that do not need it; the first NAS run
    spilled 15 GB on that window under the marks query)."""
    b = f"AND hash(trader) % {buckets} = {bucket}" if bucket is not None else ""
    return f"""
      WITH pl AS (
        SELECT p.trader AS wallet, po.mint, 'pool' AS venue, p.slot, p.tx_index, p.outer_ix, p.inner_ix, p.token_buy AS is_buy,
               p.sol_amount::BIGINT AS sol, p.token_amount::BIGINT AS tokens, p.farmer,
               p.tx_fee::BIGINT AS tx_fee, COALESCE(p.priority_fee, 0)::BIGINT AS prio, COALESCE(p.jito_tip, 0)::BIGINT AS tip, p.fee_payer,
               CASE WHEN po.orientation = 'reversed' THEN p.b + CASE WHEN p.kind = 'sell' THEN p.base ELSE -p.base END
                    ELSE p.vault + p.e_delta END AS depth_post,
               CASE WHEN po.orientation = 'reversed' THEN (p.b + CASE WHEN p.kind = 'sell' THEN p.base ELSE -p.base END)::DOUBLE / (p.e + p.e_delta)
                    ELSE (p.e + p.e_delta)::DOUBLE / (p.b + CASE WHEN p.kind = 'sell' THEN p.base ELSE -p.base END) END AS price_post,
               CASE WHEN po.orientation = 'reversed' THEN 0  -- fees of a reversed pool are taken in tokens (the quote), not in SOL
                    WHEN p.kind = 'buy' THEN p.sol_amount - p.e_delta ELSE -p.e_delta - p.sol_amount END AS venue_fee,
               m.create_slot
        FROM pool p JOIN pools po ON po.pool = p.pool LEFT JOIN mints m ON m.mint = po.mint
        WHERE p.slot >= {lo} AND p.slot < {hi} AND p.kind IN ('buy', 'sell') AND po.quote_class IN ('sol', 'reversed') AND po.mint IS NOT NULL
              AND p.e + p.e_delta > 0 {b}),
      cl AS (
        SELECT c.trader AS wallet, c.mint, 'curve' AS venue, c.slot, c.tx_index, c.outer_ix, c.inner_ix, c.is_buy,
               (c.q + CASE WHEN c.is_buy THEN c.fee + c.creator_fee ELSE -(c.fee + c.creator_fee) END)::BIGINT AS sol, c.t::BIGINT AS tokens, false AS farmer,
               c.tx_fee::BIGINT AS tx_fee, COALESCE(c.priority_fee, 0)::BIGINT AS prio, COALESCE(c.jito_tip, 0)::BIGINT AS tip, c.fee_payer,
               c.rq AS depth_post, c.vq::DOUBLE / c.vt AS price_post, (c.fee + c.creator_fee)::BIGINT AS venue_fee, m.create_slot
        FROM curve c LEFT JOIN mints m ON m.mint = c.mint
        WHERE c.slot >= {lo} AND c.slot < {hi} AND c.vt > 0 AND m.quote_mint IN ({",".join(f"'{q}'" for q in SOL_QUOTES)}) {b}),
      legs AS (SELECT * FROM pl UNION ALL SELECT * FROM cl)
      SELECT *, {"CASE WHEN row_number() OVER (PARTITION BY slot, tx_index, wallet ORDER BY venue, mint) = 1 THEN tx_fee + prio + tip ELSE 0 END" if fees else "0::BIGINT"} AS fee_once,
             fee_payer IS NOT NULL AND fee_payer <> wallet AS paid_by_other
      FROM legs"""


def day_bounds(con):
    """UTC day -> its first slot, from the blocks table (the day of a slot = the last bound <= slot)."""
    con.execute("""
      CREATE OR REPLACE TEMP TABLE day_bounds AS
      SELECT strftime(make_timestamp(block_time * 1000000), '%Y-%m-%d') AS day, min(slot) AS from_slot
      FROM blocks GROUP BY 1 ORDER BY 2""")


def dated(sql):
    """Adds `day` to a legs query via day_bounds."""
    return f"SELECT l.*, db.day FROM ({sql}) l ASOF JOIN day_bounds db ON l.slot >= db.from_slot"


def period_days(con, lo, hi):
    """The UTC days that [lo, hi) touches, from day_bounds."""
    return [r[0] for r in con.execute(f"""
      SELECT day FROM day_bounds WHERE from_slot < {hi} AND from_slot >= (SELECT COALESCE(max(from_slot), 0) FROM day_bounds WHERE from_slot <= {lo})
      ORDER BY day""").fetchall()]


def lp_sql(lo, hi):
    """Deposit / withdraw legs of SOL-priced pools: no trade, but the venue's SOL depth changes (a reversed pool is
    usually emptied by its LP after the last trade), with the price after the leg and the SOL moved."""
    return f"""
      SELECT po.mint, po.pool, 'pool' AS venue, p.slot, p.tx_index, p.outer_ix, p.inner_ix, p.kind,
             CASE WHEN po.orientation = 'reversed' THEN p.b ELSE p.vault END AS depth_pre,
             CASE WHEN po.orientation = 'reversed' THEN p.b + CASE WHEN p.kind = 'deposit' THEN p.base ELSE -p.base END
                  ELSE p.vault + CASE WHEN p.kind = 'deposit' THEN p.quote_user ELSE -p.quote_user END END AS depth_post,
             CASE WHEN po.orientation = 'reversed'
                  THEN (p.b + CASE WHEN p.kind = 'deposit' THEN p.base ELSE -p.base END)::DOUBLE
                       / nullif(p.vault + COALESCE(p.vq, 0) + CASE WHEN p.kind = 'deposit' THEN p.quote_user ELSE -p.quote_user END, 0)
                  ELSE (p.vault + COALESCE(p.vq, 0) + CASE WHEN p.kind = 'deposit' THEN p.quote_user ELSE -p.quote_user END)::DOUBLE
                       / nullif(p.b + CASE WHEN p.kind = 'deposit' THEN p.base ELSE -p.base END, 0) END AS price_post,
             CASE WHEN p.kind = 'deposit' THEN 1 ELSE -1 END * CASE WHEN po.orientation = 'reversed' THEN p.base ELSE p.quote_user END AS sol_in
      FROM pool p JOIN pools po ON po.pool = p.pool
      WHERE p.slot >= {lo} AND p.slot < {hi} AND p.kind IN ('deposit', 'withdraw') AND po.quote_class IN ('sol', 'reversed') AND po.mint IS NOT NULL"""


def build_marks(con, lo, hi, days):
    """token x UTC day from the token's first trading day to the period end: the last price and real SOL
    depth at the day's end (any wallet, curve and pool together), carried forward over days without a trade."""
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE marks_raw AS
      SELECT mint, day, arg_max(price_post, ord) AS price, arg_max(depth_post, ord) AS depth, arg_max(venue, ord) AS venue, max(slot) AS last_slot
      FROM (SELECT mint, venue, slot, (slot, tx_index, outer_ix, inner_ix) AS ord, price_post, depth_post, day FROM ({dated(legs_sql(lo, hi, fees=False))})
            UNION ALL
            SELECT mint, venue, slot, (slot, tx_index, outer_ix, inner_ix), price_post, depth_post, day FROM ({dated(lp_sql(lo, hi))}) WHERE price_post IS NOT NULL)
      GROUP BY mint, day""")
    con.execute("""
      CREATE OR REPLACE TEMP TABLE marks AS
      SELECT g.mint, g.day, r.price, r.depth, r.venue, r.last_slot, r.day = g.day AS traded
      FROM (SELECT f.mint, d.day FROM (SELECT mint, min(day) AS first_day FROM marks_raw GROUP BY mint) f
            JOIN (SELECT unnest(?::VARCHAR[]) AS day) d ON d.day >= f.first_day) g
      ASOF JOIN marks_raw r ON r.mint = g.mint AND g.day >= r.day""", [days])
    return con.execute("SELECT count(*) FROM marks").fetchone()[0]


def build_bucket(con, lo, hi, bucket, buckets, days):
    """wallet x day rows (P&L and behaviour features) of one wallet bucket."""
    con.execute(f"CREATE OR REPLACE TEMP TABLE legs AS {dated(legs_sql(lo, hi, bucket, buckets))}")
    # positions: inventory per wallet x mint over the legs in order; a position opens when it goes 0 -> > 0
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE pos_legs AS
      WITH seqd AS (
        SELECT *, row_number() OVER (PARTITION BY wallet, mint ORDER BY slot, tx_index, outer_ix, inner_ix, venue, is_buy DESC) AS seq FROM legs),
      ordered AS (
        SELECT *, sum(CASE WHEN is_buy THEN tokens ELSE 0 END) OVER w AS cum_b, sum(CASE WHEN is_buy THEN 0 ELSE tokens END) OVER w AS cum_s
        FROM seqd WINDOW w AS (PARTITION BY wallet, mint ORDER BY seq ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)),
      inv AS (
        SELECT *, greatest(0, cum_b - cum_s) AS inv_after,
               lag(greatest(0, cum_b - cum_s), 1, 0) OVER (PARTITION BY wallet, mint ORDER BY seq) AS inv_before
        FROM ordered),
      pid AS (
        SELECT *, sum(CASE WHEN is_buy AND inv_before = 0 AND inv_after > 0 THEN 1 ELSE 0 END)
                    OVER (PARTITION BY wallet, mint ORDER BY seq ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS pos_id
        FROM inv)
      SELECT *, max(inv_after) OVER (PARTITION BY wallet, mint, pos_id ORDER BY seq ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS max_inv
      FROM pid""")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE positions AS
      SELECT p.*, db.day AS open_day FROM (
        SELECT wallet, mint, pos_id, min(slot) AS open_slot,
               arg_min(create_slot, slot) AS create_slot, arg_min(sol, slot) AS first_buy_sol,
               min(CASE WHEN NOT is_buy AND inv_after <= {EXIT_SHARE} * max_inv THEN slot END) AS close_slot,
               sum(CASE WHEN is_buy THEN sol ELSE 0 END) AS buy_sol
        FROM pos_legs WHERE pos_id > 0 GROUP BY wallet, mint, pos_id) p
      ASOF JOIN day_bounds db ON p.open_slot >= db.from_slot""")
    # per wallet x mint x day: cash, fees, inventory at the day's end
    con.execute("""
      CREATE OR REPLACE TEMP TABLE wmd AS
      SELECT wallet, mint, day,
             sum(CASE WHEN is_buy THEN -sol ELSE sol END) AS cash, sum(fee_once) AS fees, sum(paid_by_other::INT) AS paid_by_other,
             sum(CASE WHEN is_buy THEN sol ELSE 0 END) AS buy_sol, sum(CASE WHEN is_buy THEN 0 ELSE sol END) AS sell_sol,
             count(*) AS n_legs, sum(is_buy::INT) AS n_buys, sum((venue = 'pool')::INT) AS n_pool_legs, sum((venue = 'pool' AND farmer)::INT) AS n_farmer_legs,
             sum(venue_fee) AS venue_fees, arg_max(inv_after, seq) AS inv_end
      FROM pos_legs GROUP BY wallet, mint, day""")
    # carry open inventory to later days until the next trade day or the period end (dust dropped)
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE carry AS
      WITH spans AS (
        SELECT w.wallet, w.mint, w.day AS from_day, w.inv_end,
               lead(w.day) OVER (PARTITION BY w.wallet, w.mint ORDER BY w.day) AS next_day
        FROM wmd w),
      open AS (
        SELECT s.wallet, s.mint, s.from_day, s.next_day, s.inv_end
        FROM spans s JOIN marks k ON k.mint = s.mint AND k.day = s.from_day
        WHERE s.inv_end > 0 AND s.inv_end * k.price >= {DUST})
      SELECT o.wallet, o.mint, d.day, o.inv_end AS inv_end
      FROM open o JOIN (SELECT unnest(?::VARCHAR[]) AS day) d ON d.day > o.from_day AND (o.next_day IS NULL OR d.day < o.next_day)""", [days])
    con.execute("""
      CREATE OR REPLACE TEMP TABLE valued AS
      WITH rows AS (
        SELECT wallet, mint, day, cash, fees, paid_by_other, buy_sol, sell_sol, n_legs, n_buys, n_pool_legs, n_farmer_legs, venue_fees, inv_end FROM wmd
        UNION ALL
        SELECT wallet, mint, day, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, inv_end FROM carry),
      marked AS (
        SELECT r.*, CASE WHEN r.inv_end > 0 THEN least(r.inv_end * COALESCE(k.price, 0), COALESCE(k.depth, 0)::DOUBLE) ELSE 0 END AS value_end
        FROM rows r LEFT JOIN marks k ON k.mint = r.mint AND k.day = r.day)
      SELECT *, value_end - lag(value_end, 1, 0) OVER (PARTITION BY wallet, mint ORDER BY day) AS dvalue FROM marked""")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE wd AS
      SELECT v.wallet, v.day,
             sum(v.cash) AS cash, sum(v.fees) AS fees, sum(v.paid_by_other) AS paid_by_other, sum(v.value_end) AS value_end, sum(v.dvalue) AS dvalue,
             sum(v.cash) - sum(v.fees) + sum(v.dvalue) AS pnl,
             sum(v.buy_sol) AS buy_sol, sum(v.sell_sol) AS sell_sol, sum(v.n_legs) AS n_legs, sum(v.n_buys) AS n_buys,
             sum(v.n_pool_legs) AS n_pool_legs, sum(v.n_farmer_legs) AS n_farmer_legs, sum(v.venue_fees) AS venue_fees,
             count(DISTINCT CASE WHEN v.n_legs > 0 THEN v.mint END) AS n_mints,
             COALESCE(any_value(p.n_opened), 0) AS n_opened, COALESCE(any_value(p.n_snipe), 0) AS n_snipe, any_value(p.median_buy_sol) AS median_buy_sol,
             COALESCE(any_value(p.n_closed), 0) AS n_closed, any_value(p.median_hold_slots) AS median_hold_slots
      FROM valued v LEFT JOIN (
        SELECT wallet, open_day AS day, count(*) AS n_opened,
               sum((create_slot IS NOT NULL AND open_slot <= create_slot + {SNIPE_SLOTS})::INT) AS n_snipe,
               median(first_buy_sol) AS median_buy_sol, count(close_slot) AS n_closed, median(close_slot - open_slot) AS median_hold_slots
        FROM positions GROUP BY wallet, open_day) p ON p.wallet = v.wallet AND p.day = v.day
      GROUP BY v.wallet, v.day""")
    return con.execute("SELECT count(*), count(DISTINCT wallet) FROM wd").fetchone()


def build_side_tables(con, lo, hi):
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE payers AS
      SELECT fee_payer, wallet, mint, count(*) AS n, min(slot) AS first_slot, min(day) AS first_day
      FROM ({dated(legs_sql(lo, hi, fees=False))}) WHERE fee_payer IS NOT NULL AND fee_payer <> wallet GROUP BY ALL""")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE lp_legs AS
      SELECT l.pool, l.mint, po.orientation, l.slot, l.tx_index, l.kind, l.sol_in, l.depth_pre, l.depth_post
      FROM ({lp_sql(lo, hi)}) l JOIN pools po ON po.pool = l.pool""")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE creators AS
      SELECT c.wallet, c.mint, c.create_slot, db.day
      FROM (SELECT DISTINCT creator AS wallet, mint, create_slot FROM mints WHERE creator IS NOT NULL AND create_slot >= {lo} AND create_slot < {hi}) c
      ASOF JOIN day_bounds db ON c.create_slot >= db.from_slot""")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE venue_flows AS
      WITH l AS (SELECT * FROM ({legs_sql(lo, hi, fees=False)})),
      sol AS (
        SELECT 'pool' AS venue, sum(s0) AS sol_start, sum(s1) AS sol_end FROM (
        SELECT key, arg_min(depth_pre, (slot, tx_index)) AS s0, arg_max(depth_post, (slot, tx_index)) AS s1
        FROM (SELECT p.pool AS key, p.slot, p.tx_index, CASE WHEN po.orientation = 'reversed' THEN p.b ELSE p.vault END AS depth_pre,
                     CASE WHEN po.orientation = 'reversed' THEN p.b + CASE WHEN p.kind = 'sell' THEN p.base ELSE -p.base END
                          ELSE p.vault + p.e_delta END AS depth_post
              FROM pool p JOIN pools po ON po.pool = p.pool
              WHERE p.slot >= {lo} AND p.slot < {hi} AND p.kind IN ('buy', 'sell') AND po.quote_class IN ('sol', 'reversed') AND po.mint IS NOT NULL
              UNION ALL
              SELECT l.pool, l.slot, l.tx_index, l.depth_pre, l.depth_post FROM ({lp_sql(lo, hi)}) l)
        GROUP BY key)
        UNION ALL
        SELECT 'curve', sum(s0), sum(s1) FROM (
        SELECT key, arg_min(rq_pre, (slot, tx_index)) AS s0, arg_max(rq, (slot, tx_index)) AS s1
        FROM (SELECT c.mint AS key, c.slot, c.tx_index, c.rq, c.rq - CASE WHEN c.is_buy THEN c.q ELSE -c.q END AS rq_pre
              FROM curve c JOIN mints m ON m.mint = c.mint
              WHERE c.slot >= {lo} AND c.slot < {hi} AND c.vt > 0 AND m.quote_mint IN ({",".join(f"'{q}'" for q in SOL_QUOTES)}))
        GROUP BY key)),
      ext AS (
        -- SOL that enters or leaves a venue outside the trades: LP deposits and withdrawals (the opening deposit of a pool created
        -- in the period is already the pre-depth of its first trade, so it is not external); the curve has none (its SOL leaves
        -- at migration, after its last trade)
        SELECT 'pool' AS venue, sum(sol_in) AS external_in FROM ({lp_sql(lo, hi)})
        UNION ALL SELECT 'curve', 0)
      SELECT f.venue, f.venue_fees, NULL::BIGINT AS network_fees, f.legs, f.gross_sol, f.cash_all, s.sol_start, s.sol_end, COALESCE(e.external_in, 0) AS external_in
      FROM (SELECT venue, sum(venue_fee) AS venue_fees, count(*) AS legs, sum(sol) AS gross_sol,
                   sum(CASE WHEN is_buy THEN -sol ELSE sol END) AS cash_all FROM l GROUP BY venue) f
      LEFT JOIN (SELECT venue, sum(sol_start) AS sol_start, sum(sol_end) AS sol_end FROM sol GROUP BY venue) s ON s.venue = f.venue
      LEFT JOIN ext e ON e.venue = f.venue""")


def run(lo, hi, out_dir, buckets=8, store=None):
    t0 = time.time()
    guard(hi)
    mem = os.environ.get("LAB_WALLETS_MEMORY", "1GB")
    con = connect(store, memory=mem) if store else connect(memory=mem)
    os.makedirs(out_dir, exist_ok=True)
    day_bounds(con)
    days = period_days(con, lo, hi)
    n_marks = build_marks(con, lo, hi, days)
    con.execute(f"COPY marks TO '{os.path.join(out_dir, 'marks.parquet')}' (FORMAT PARQUET)")
    build_side_tables(con, lo, hi)
    for name in ("payers", "creators", "venue_flows", "lp_legs"):
        con.execute(f"COPY {name} TO '{os.path.join(out_dir, name + '.parquet')}' (FORMAT PARQUET)")
    counts = []
    for b in range(buckets):
        n_rows, n_wallets = build_bucket(con, lo, hi, b, buckets, days)
        con.execute(f"COPY wd TO '{os.path.join(out_dir, f'wallet-day-{b}.parquet')}' (FORMAT PARQUET)")
        for t in ("wd", "valued", "carry", "wmd", "positions", "pos_legs", "legs"):  # free the spill before the next bucket
            con.execute(f"DROP TABLE IF EXISTS {t}")
        counts.append((n_rows, n_wallets))
        print(f"bucket {b}: {n_rows} wallet-days, {n_wallets} wallets, {time.time() - t0:.0f}s", file=sys.stderr)
    info = {"slots": [lo, hi], "buckets": buckets, "marks": n_marks, "days": days, "wallet_days": sum(c[0] for c in counts),
            "wallets": sum(c[1] for c in counts), "dust_lamports": DUST, "snipe_slots": SNIPE_SLOTS, "exit_share": EXIT_SHARE,
            "commit": git_commit(), "seconds": round(time.time() - t0, 1)}
    with open(os.path.join(out_dir, "run-money.json"), "w") as f:
        json.dump(info, f, indent=1)
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-slot", type=int, required=True)
    ap.add_argument("--to-slot", type=int, required=True)
    ap.add_argument("--buckets", type=int, default=8)
    ap.add_argument("--out", default=os.path.join(OUT_ROOT, "money-v1"))
    a = ap.parse_args()
    print(json.dumps(run(a.from_slot, a.to_slot, a.out, a.buckets)), file=sys.stderr)


if __name__ == "__main__":
    main()
