"""Onderzoek 1b: can "runners" be recognised in advance? (#167, owner question of 2026-10-11)

Universe: graduations in [lo, hi) (pump.fun CompletePumpAmmMigrationEvent) into a canonical SOL
pool, non-mayhem, development slots only. Graduations in the create slot are flagged and reported
apart. The graduation market cap is the pool's opening price (q0 / b0; supply is constant, so a
price multiple is a market-cap multiple).

Outcomes over the 24 h after the pool opened (pre-trade price e / b of every pool trade):
  max_mult   highest price / opening price        runner10 = max_mult >= 10, runner3 = >= 3
  end_mult   last price before +24 h / opening    (most peaks collapse)

Features at two decision moments, pool open + 5 min and + 15 min, using only events with
slot <= the decision slot:
  curve: minutes from create to graduation, unique curve buyers, share of buyers >= 5 SOL, net SOL
         inflow on the curve, creator sells, insider share (O7: creator plus buyers within 10 s of
         the create; net tokens held / circulating, from curve and pool legs up to the decision),
         insider net sold share since graduation;
  pool:  trades, unique traders, organic SOL volume (non-farmer), net SOL flow, price at the
         decision / open, highest price so far / open, split-buyer events (clusters.py `tx`
         events on the token, when a clusters directory is given).
A third moment, curve B = 55 SOL real reserves, is the first curve trade after which the real SOL
reserves are >= 55 SOL: its universe is every token that got there (graduated or not, the
non-graduated ones are non-runners), with the curve features only.

  python -m wallets.runners --from-slot A --to-slot B [--clusters DIR] [--out DIR]

Writes runners-<A>.parquet (one row per graduation, columns prefixed t5_ / t15_) and
runners-b55-<A>.parquet. The report (quintile lifts, logistic walk-forward) is runners_report.py.
"""

import argparse
import json
import os
import sys
import time

import pyarrow.parquet as pq

from .common import OUT_ROOT, SOL, Clock, connect, git_commit, guard

DAY_MS = 86_400_000
DECISIONS_MS = {"t5": 5 * 60_000, "t15": 15 * 60_000}
INSIDER_MS = 10_000
BIG_BUY = 5 * SOL
B55 = 55 * SOL
SOL_QUOTES = ("11111111111111111111111111111111", "So11111111111111111111111111111111111111112")


def grads_sql(lo, hi):
    quotes = ",".join(f"'{q}'" for q in SOL_QUOTES)
    return f"""
      SELECT m.mint, m.pool, m.creator, m.create_slot::BIGINT AS create_slot, m.complete_slot::BIGINT AS complete_slot,
             m.migrate_slot::BIGINT AS open_slot, m.complete_slot = m.create_slot AS grad_in_create,
             p.q0::DOUBLE / p.b0 AS p_open, m.name, m.symbol
      FROM mints m JOIN pools p ON p.pool = m.pool
      WHERE m.migrate_slot >= {lo} AND m.migrate_slot < {hi} AND NOT m.mayhem AND m.quote_mint IN ({quotes})
        AND p.quote_class = 'sol' AND p.orientation = 'normal' AND NOT COALESCE(p.mayhem, false) AND p.b0 > 0"""


def build(con, lo, hi, clock, clusters_dir=None):
    n_day = clock.slots(DAY_MS, lo)
    n_ins = clock.slots(INSIDER_MS, lo)
    ms = clock.ms_per_slot(lo)
    con.execute(f"CREATE OR REPLACE TEMP TABLE g AS {grads_sql(lo, hi)}")
    # Pool trades of these pools over the outcome window (pre-trade price e / b).
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE pt AS
      SELECT e.pool, e.slot::BIGINT AS slot, e.tx_index, e.trader, e.token_buy, e.sol_amount, e.token_amount, e.farmer,
             e.e::DOUBLE / e.b AS px
      FROM pool e SEMI JOIN g ON g.pool = e.pool
      WHERE e.kind IN ('buy', 'sell') AND e.b > 0 AND e.slot >= {lo} AND e.slot < {hi + n_day}""")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE outcome AS
      SELECT g.mint, max(pt.px) / g.p_open AS max_mult,
             arg_max(pt.px, pt.slot * 1048576 + pt.tx_index) / g.p_open AS end_mult,
             count(*) AS n_trades_24h, sum(pt.sol_amount) / {SOL} AS sol_vol_24h
      FROM g JOIN pt ON pt.pool = g.pool AND pt.slot >= g.open_slot AND pt.slot < g.open_slot + {n_day}
      GROUP BY g.mint, g.p_open""")
    # Curve legs of the tokens (the whole curve: it ends at graduation) and all legs by trader for
    # the insider holdings.
    con.execute("""
      CREATE OR REPLACE TEMP TABLE ct AS
      SELECT c.mint, c.slot::BIGINT AS slot, c.trader, c.is_buy, c.q, c.t, c.rq
      FROM curve c SEMI JOIN g ON g.mint = c.mint""")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE insiders AS
      SELECT DISTINCT mint, wallet FROM (
        SELECT mint, creator AS wallet FROM g
        UNION ALL
        SELECT ct.mint, ct.trader FROM ct JOIN g USING (mint) WHERE ct.is_buy AND ct.slot <= g.create_slot + {n_ins})""")
    con.execute("""
      CREATE OR REPLACE TEMP TABLE legs AS
      SELECT ct.mint, ct.slot, ct.trader, CASE WHEN ct.is_buy THEN ct.t ELSE -ct.t END AS dt FROM ct
      UNION ALL
      SELECT g.mint, pt.slot, pt.trader, CASE WHEN pt.token_buy THEN pt.token_amount ELSE -pt.token_amount END
      FROM pt JOIN g ON g.pool = pt.pool""")
    feats = []
    for name, dms in DECISIONS_MS.items():
        n_dec = clock.slots(dms, lo)
        feats.append(f"""
          SELECT g.mint, g.open_slot + {n_dec} AS t_dec,
            (g.complete_slot - g.create_slot) * {ms} / 60000 AS curve_minutes,
            cb.buyers, cb.big_share, cb.inflow_sol, cb.creator_sells,
            ins.h_ins::DOUBLE / NULLIF(ins.circ, 0) AS insider_share,
            1 - ins.h_ins::DOUBLE / NULLIF(ins0.h_ins, 0) AS insider_sold_share,
            COALESCE(pf.n_trades, 0) AS pool_trades, COALESCE(pf.traders, 0) AS pool_traders,
            COALESCE(pf.organic_sol, 0) AS organic_sol, COALESCE(pf.net_sol, 0) AS net_sol,
            pf.px_last / g.p_open AS px_mult, pf.px_max / g.p_open AS max_mult_so_far,
            COALESCE(sb.n, 0) AS split_events
          FROM g
          LEFT JOIN (SELECT mint, count(DISTINCT trader) FILTER (WHERE is_buy) AS buyers,
                            count(DISTINCT trader) FILTER (WHERE is_buy AND q >= {BIG_BUY})::DOUBLE
                              / NULLIF(count(DISTINCT trader) FILTER (WHERE is_buy), 0) AS big_share,
                            sum(CASE WHEN is_buy THEN q ELSE -q END) / {SOL} AS inflow_sol,
                            count(*) FILTER (WHERE NOT is_buy AND trader = g2.creator) AS creator_sells
                     FROM ct JOIN g g2 USING (mint) WHERE ct.slot <= g2.open_slot + {n_dec} GROUP BY mint) cb USING (mint)
          LEFT JOIN (SELECT l.mint, sum(l.dt) FILTER (WHERE i.wallet IS NOT NULL) AS h_ins, sum(l.dt) AS circ
                     FROM legs l JOIN g g2 USING (mint) LEFT JOIN insiders i ON i.mint = l.mint AND i.wallet = l.trader
                     WHERE l.slot <= g2.open_slot + {n_dec} GROUP BY l.mint) ins USING (mint)
          LEFT JOIN (SELECT l.mint, sum(l.dt) FILTER (WHERE i.wallet IS NOT NULL) AS h_ins
                     FROM legs l JOIN g g2 USING (mint) LEFT JOIN insiders i ON i.mint = l.mint AND i.wallet = l.trader
                     WHERE l.slot < g2.open_slot GROUP BY l.mint) ins0 USING (mint)
          LEFT JOIN (SELECT g2.mint, count(*) AS n_trades, count(DISTINCT pt.trader) AS traders,
                            sum(pt.sol_amount) FILTER (WHERE NOT pt.farmer) / {SOL} AS organic_sol,
                            sum(CASE WHEN pt.token_buy THEN pt.sol_amount ELSE -pt.sol_amount END) / {SOL} AS net_sol,
                            arg_max(pt.px, pt.slot * 1048576 + pt.tx_index) AS px_last, max(pt.px) AS px_max
                     FROM pt JOIN g g2 ON g2.pool = pt.pool
                     WHERE pt.slot >= g2.open_slot AND pt.slot <= g2.open_slot + {n_dec} GROUP BY g2.mint) pf USING (mint)
          LEFT JOIN (SELECT g2.mint, count(*) AS n FROM {split_events_sql(clusters_dir)} e JOIN g g2
                       ON (e.venue = 'curve' AND e.key = g2.mint) OR (e.venue = 'pool' AND e.key = g2.pool)
                     WHERE e.slot <= g2.open_slot + {n_dec} GROUP BY g2.mint) sb USING (mint)""")
    joins = ""
    cols = []
    for (name, _), sql in zip(DECISIONS_MS.items(), feats):
        con.execute(f"CREATE OR REPLACE TEMP TABLE f_{name} AS {sql}")
        joins += f" LEFT JOIN f_{name} USING (mint)"
        cols.append(", ".join(f"f_{name}.{c} AS {name}_{c}" for c in
                              [r[0] for r in con.execute(f"DESCRIBE f_{name}").fetchall() if r[0] != "mint"]))
    out = con.execute(f"""
      SELECT g.*, o.max_mult, o.end_mult, o.n_trades_24h, o.sol_vol_24h,
             COALESCE(o.max_mult >= 10, false) AS runner10, COALESCE(o.max_mult >= 3, false) AS runner3,
             strftime(make_timestamp(b.block_time * 1000000), '%Y-%m-%d') AS day, {", ".join(cols)}
      FROM g LEFT JOIN outcome o USING (mint) {joins}
      ASOF LEFT JOIN blocks b ON b.slot <= g.open_slot
      ORDER BY g.open_slot""").to_arrow_table()
    # B = 55: curve features at the first trade that leaves >= 55 SOL real reserves.
    b55 = con.execute(f"""
      WITH first AS (SELECT mint, min(slot) AS t_dec FROM curve c
                     WHERE c.slot >= {lo} AND c.slot < {hi} AND c.rq >= {B55} GROUP BY mint),
      u AS (SELECT m.mint, m.creator, m.create_slot::BIGINT AS create_slot, m.complete_slot::BIGINT AS complete_slot,
                   m.migrate_slot::BIGINT AS open_slot, f.t_dec
            FROM mints m JOIN first f USING (mint) WHERE NOT m.mayhem AND m.quote_mint IN ({",".join(f"'{q}'" for q in SOL_QUOTES)}))
      SELECT u.*, (u.t_dec - u.create_slot) * {ms} / 60000 AS minutes_to_b55,
             u.open_slot IS NOT NULL AND u.open_slot < u.t_dec + {n_day} AS graduated_24h,
             COALESCE(o.max_mult, 0) AS max_mult, COALESCE(o.max_mult >= 10, false) AS runner10,
             COALESCE(o.max_mult >= 3, false) AS runner3,
             cb.buyers, cb.big_share, cb.inflow_sol, cb.creator_sells,
             strftime(make_timestamp(b.block_time * 1000000), '%Y-%m-%d') AS day
      FROM u LEFT JOIN outcome o USING (mint)
      LEFT JOIN (SELECT c.mint, count(DISTINCT c.trader) FILTER (WHERE c.is_buy) AS buyers,
                        count(DISTINCT c.trader) FILTER (WHERE c.is_buy AND c.q >= {BIG_BUY})::DOUBLE
                          / NULLIF(count(DISTINCT c.trader) FILTER (WHERE c.is_buy), 0) AS big_share,
                        sum(CASE WHEN c.is_buy THEN c.q ELSE -c.q END) / {SOL} AS inflow_sol,
                        count(*) FILTER (WHERE NOT c.is_buy AND c.trader = u2.creator) AS creator_sells
                 FROM curve c JOIN u u2 USING (mint) WHERE c.slot <= u2.t_dec GROUP BY c.mint) cb USING (mint)
      ASOF LEFT JOIN blocks b ON b.slot <= u.t_dec
      ORDER BY u.t_dec""").to_arrow_table()
    return out, b55


def split_events_sql(clusters_dir):
    if not clusters_dir:
        return "(SELECT NULL::VARCHAR AS venue, NULL::VARCHAR AS key, NULL::BIGINT AS slot WHERE false)"
    return (f"(SELECT venue, key, slot FROM read_parquet('{os.path.join(clusters_dir, 'events-*.parquet')}') "
            f"WHERE links LIKE '%tx%')")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-slot", type=int, required=True)
    ap.add_argument("--to-slot", type=int, required=True)
    ap.add_argument("--clusters")
    ap.add_argument("--out", default=os.path.join(OUT_ROOT, "runners-v1"))
    a = ap.parse_args()
    guard(a.to_slot)
    con = connect(memory="1000MB")  # the /dev/shm spill (<= 1 GB) counts in the same 2 GB scope
    clock = Clock.from_store(con)
    guard(a.to_slot + clock.slots(DAY_MS, a.to_slot))
    t0 = time.time()
    out, b55 = build(con, a.from_slot, a.to_slot, clock, a.clusters)
    os.makedirs(a.out, exist_ok=True)
    pq.write_table(out, os.path.join(a.out, f"runners-{a.from_slot}.parquet"))
    pq.write_table(b55, os.path.join(a.out, f"runners-b55-{a.from_slot}.parquet"))
    with open(os.path.join(a.out, f"run-runners-{a.from_slot}-{a.to_slot}.json"), "w") as f:
        json.dump({"from_slot": a.from_slot, "to_slot": a.to_slot, "commit": git_commit(), "graduations": out.num_rows,
                   "b55_tokens": b55.num_rows, "decisions_ms": DECISIONS_MS, "seconds": round(time.time() - t0)}, f, indent=1)
    print(f"{a.from_slot}-{a.to_slot}: {out.num_rows} graduations, {b55.num_rows} B55 tokens, {time.time() - t0:.0f}s",
          file=sys.stderr)


if __name__ == "__main__":
    main()
