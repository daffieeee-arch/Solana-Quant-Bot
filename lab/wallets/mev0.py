"""MEV phase 0: atomic arbitrage and backruns around pump.fun and PumpSwap venues, measured on Old
Faithful history only. Nothing is sent and nothing is traded. Frozen spec:
data-old-faithful-one/lab/research/week3-specs-mev0-2026-10-12.md, version 4 (sha256 57112dee...).

Inputs:
- tx_ctx of the re-extracted chunks (--chunks, events/txctx-v1/chunks): fee payer, success,
  programs, venues, fees, tip and the fee payer's SOL and token deltas per transaction;
- the store (LAB_STORE): curve and PumpSwap trades with (slot, tx_index), SOL amount and depth,
  pools, mints and block times.

Definitions (spec section 1, versions 3 and 4):
- arb: a successful transaction whose fee payer trades one mint M at two or more venues, has a flat
  position in M (|token delta| <= 1 % of the largest leg) and gains SOL + WSOL. net =
  signer_sol_delta + the WSOL delta: the fee (priority fee included) and a Jito tip are already in
  signer_sol_delta (v3 point 2). An arb also needs closed_loop (v4 point 1): no other token
  delta than WSOL and M. Without it a routed sale of another token through M (A -> M -> SOL)
  passes the literal test: on the 1054 smoke chunk, 36 of 309 literal candidates, with 39.7 of
  their 40.1 SOL. arbs-<e> keeps every literal candidate with the flag. Winners, program sets,
  events, gaps and the report use closed-loop arbs only; the report names the literal count apart.
  - same_venue: two or more store pools of M under one program. Fully proven.
  - cross_venue: store venues under different programs (curve and pool), or one store venue plus
    another DEX program in tx_ctx.venues. In the second case other_venue_unparsed = true: that the
    other venue also traded M is assumed (v3 point 3).
- opportunity: the trade before the arb's first leg on that venue (prior). The arb is a backrun
  when that trade is a trigger: >= 1 SOL or >= 1 % of the venue's SOL depth, by another fee payer.
  reachable = the arb landed >= 2 slots after it (spec: same-slot opportunities are Jito-bundle
  territory).
- attempt, an upper bound (v3 point 1): a failed transaction whose fee payer won an arb in the
  period, or whose top-level program set equals a winning arb's, and that lands in the same or the
  next slot as an arb or a trigger. A pure router set (only ROUTERS, no DEX or own program) is no
  program-set match (v4 point 2): such a transaction counts only by a winner. Cost = its fee; a
  failed transaction's tip does not land.
- gap, an unrealized opportunity (only between two store pools of one mint): after each trade on
  either pool, the best net over SIZES_SOL of buying on one pool and selling on the other in one
  transaction (ARB_TX_COST); open while > 0. The curve against a pool in the migration slot gives
  none: a complete curve does not trade.
- harm, description only (spec section 2.10): a sandwich (one fee payer buys before and sells after
  another fee payer's trade on the same venue in the same slot) and a front-run (a trade directly
  before a larger trade in the same direction on the same venue in the same slot, sold within
  3 slots).

  LAB_STORE=<dev2> python -m wallets.mev0 --chunks <events/txctx-v1/chunks> --epochs 1040,1045 [--out DIR]

Writes into --out (default <wallets>/mev0-v1), per epoch: arbs-<e>.parquet, attempts-<e>.parquet,
gaps-<e>.parquet, signers-<e>.parquet, harm-<e>.parquet, days-<e>.parquet and run-mev0-<e>.json.
Attempts need the winners of every epoch, so they are written after all epochs' arbs. Slots stay
before the hold-out (common.guard). Heavy (two epochs of tx_ctx and store trades): NAS.
"""

import argparse
import glob
import json
import os
import time

from .common import EPOCH_SLOTS, OUT_ROOT, SOL, STORE, Clock, connect, git_commit, guard

SPEC = "week3-specs-mev0-2026-10-12.md v4 (sha256 57112dee)"
WSOL = "So11111111111111111111111111111111111111112"
ROUTERS = ("jupiter_v6",)  # routers: never a venue of their own
STORE_PROGRAMS = ("pump", "pump_amm")  # venues the store parses (curve, PumpSwap)
INFRA = ("system", "token", "token2022", "ata", "compute_budget", "memo", "memo_v1")
TRIGGER_SOL = SOL  # a trigger: >= 1 SOL ...
TRIGGER_DEPTH = 0.01  # ... or >= 1 % of the venue's SOL depth
FLAT = 0.01  # |token delta in M| <= 1 % of the largest leg in M
REACHABLE_SLOTS = 2
PRIOR_LOOKBACK = 10_000  # slots before the epoch whose trades can be an arb's prior trade
SIZES_SOL = (0.05, 0.1, 0.25, 0.5, 1.0)  # gap sizes (spec: 0.05-1 SOL, the best point)
ARB_TX_COST = 700_000  # lamports: one transaction with two legs (spec section 3.1)
KEY = 1_000_000  # k = slot * KEY + tx_index: one ordered key for ASOF joins


def chunk_files(chunks_root, table, lo, hi):
    """Parquet files of `table` in the complete chunks that overlap [lo, hi)."""
    out = []
    for m in sorted(glob.glob(os.path.join(chunks_root, "*", "_manifest.json"))):
        with open(m) as f:
            man = json.load(f)
        if man.get("status") == "complete" and man["slot_start"] < hi and lo < man["slot_end_exclusive"]:
            out += sorted(glob.glob(os.path.join(os.path.dirname(m), table, "*.parquet")))
    return out


def _lit(files):
    return "[" + ",".join("'" + f.replace("'", "''") + "'" for f in files) + "]"


def delta(mint_sql, col="signer_token_deltas"):
    """The fee payer's token delta for a mint from "mint:delta;..." (NULL when the mint is not there)."""
    return (f"TRY_CAST(split_part(list_filter(string_split(COALESCE({col}, ''), ';'), "
            f"x -> starts_with(x, {mint_sql} || ':'))[1], ':', 2) AS HUGEINT)")


def top_programs(col="programs"):
    """The sorted set of top-level (depth 1) programs other than infrastructure, as one string."""
    infra = ", ".join(f"'{p}'" for p in INFRA)
    return (f"array_to_string(list_sort(list_distinct(list_filter(list_transform(list_filter(string_split({col}, ';'), "
            f"x -> starts_with(x, '1:')), x -> split_part(split_part(x, ':', 2), '*', 1)), x -> x NOT IN ({infra})))), ',')")


def router_only(col="top_programs"):
    """SQL: the top-level program set holds routers only (v4 point 2: no program-set match)."""
    routers = ", ".join(f"'{p}'" for p in ROUTERS)
    return f"(len(list_filter(string_split({col}, ','), x -> x NOT IN ({routers}))) = 0)"


def setup(con, chunks_root, lo, hi):
    """Views for one epoch [lo, hi): ctx (tx_ctx), legs (curve and pool buys and sells from
    lo - PRIOR_LOOKBACK) and days (slot -> UTC day, as the store computes it: never the host's time
    zone, which DuckDB's to_timestamp()::DATE would use)."""
    files = chunk_files(chunks_root, "tx_ctx", lo, hi)
    if not files:
        raise SystemExit(f"no tx_ctx for [{lo}, {hi}) under {chunks_root}")
    con.execute(f"""CREATE OR REPLACE TEMP VIEW ctx AS SELECT * FROM read_parquet({_lit(files)}, union_by_name = true)
                    WHERE slot >= {lo} AND slot < {hi}""")
    a = max(0, lo - PRIOR_LOOKBACK)
    # The store's fee_payer is NULL when the trader pays its own transaction (NULLIF(tx.fee_payer, user),
    # analytics/ANALYST.md): the legs carry the paying wallet, so a self-payer compares equal to itself (harm,
    # backrun) instead of NULL (LAB review 2026-10-17: sandwiches of self-payers were missed, backruns overcounted).
    con.execute(f"""CREATE OR REPLACE TEMP VIEW legs AS
        SELECT p.pool AS venue, 'pump_amm' AS program, COALESCE(ps.mint, p.pool) AS mint, p.slot, p.tx_index::UBIGINT AS tx_index,
               p.outer_ix, p.inner_ix, p.slot * {KEY} + p.tx_index AS k, p.token_buy AS buy, p.sol_amount AS sol,
               p.sol_depth AS depth, p.token_amount AS tokens, COALESCE(p.fee_payer, p.trader) AS fee_payer
        FROM pool p JOIN pools ps USING (pool)
        WHERE p.slot >= {a} AND p.slot < {hi} AND p.kind IN ('buy', 'sell') AND ps.quote_class IN ('sol', 'reversed')
        UNION ALL
        SELECT c.mint || ':curve', 'pump', c.mint, c.slot, c.tx_index, c.outer_ix, c.inner_ix, c.slot * {KEY} + c.tx_index,
               c.is_buy, c.q::BIGINT, c.rq::BIGINT, c.t::BIGINT, COALESCE(c.fee_payer, c.trader)
        FROM curve c WHERE c.slot >= {a} AND c.slot < {hi}""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE days AS
        SELECT slot, epoch_ms(block_time::BIGINT * 1000)::DATE AS day FROM blocks
        WHERE slot >= {lo} AND slot < {hi} AND block_time IS NOT NULL ORDER BY slot""")


def find_arbs(con, ms):
    """Arb transactions of the epoch (one row each; with several qualifying mints, the one with the
    most venues), with the opportunity they closed (the prior trade on their first venue)."""
    routers = ", ".join(f"'{p}'" for p in ROUTERS + STORE_PROGRAMS)
    # Candidate transactions first (the list() aggregation over every store leg of an epoch, tens of millions of
    # groups, ran out of 18.6 GiB of spill on the NAS): an arb needs two store venues, so two or more legs in one
    # transaction, or one store leg beside another DEX in tx_ctx.venues, and a successful transaction. Every
    # other transaction can never pass the tests below, so the result is unchanged (1054 smoke: 309 / 273 arbs).
    con.execute(f"""CREATE OR REPLACE TEMP TABLE txk AS
        SELECT slot, tx_index FROM legs WHERE slot IN (SELECT slot FROM days) GROUP BY slot, tx_index HAVING count(*) >= 2
        UNION
        SELECT c.slot, c.tx_index FROM ctx c
        SEMI JOIN (SELECT DISTINCT slot, tx_index FROM legs WHERE slot IN (SELECT slot FROM days)) l USING (slot, tx_index)
        WHERE NOT c.failed AND len(list_filter(string_split(COALESCE(c.venues, ''), ','), x -> x <> '' AND x NOT IN ({routers}))) > 0""")
    # Store venues per (candidate transaction, mint).
    con.execute("""CREATE OR REPLACE TEMP TABLE txm AS
        SELECT slot, tx_index, mint, count(DISTINCT venue) AS n_store, count(DISTINCT program) AS n_programs,
               list_sort(list_distinct(list(venue))) AS store_venues, list_sort(list_distinct(list(program))) AS store_programs,
               arg_min(venue, outer_ix * 1000 + inner_ix) AS first_venue, max(tokens) AS max_tokens,
               sum(CASE WHEN buy THEN -sol ELSE sol END) AS legs_sol
        FROM legs SEMI JOIN txk USING (slot, tx_index) WHERE slot IN (SELECT slot FROM days) GROUP BY slot, tx_index, mint""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE cand AS
        SELECT c.slot, c.tx_index, c.slot * {KEY} + c.tx_index AS k, c.fee_payer, m.mint, m.n_store, m.n_programs,
               m.store_venues, m.store_programs, m.first_venue, m.max_tokens, m.legs_sol,
               list_filter(string_split(COALESCE(c.venues, ''), ','), x -> x <> '' AND x NOT IN ({routers})) AS other_dex,
               c.venues, c.n_dex, c.signer_sol_delta, COALESCE({delta(f"'{WSOL}'", 'c.signer_token_deltas')}, 0) AS wsol_delta,
               COALESCE({delta('m.mint', 'c.signer_token_deltas')}, 0) AS mint_delta,
               len(list_filter(string_split(COALESCE(c.signer_token_deltas, ''), ';'),
                   x -> x <> '' AND NOT starts_with(x, '{WSOL}:') AND NOT starts_with(x, m.mint || ':')
                        AND TRY_CAST(split_part(x, ':', 2) AS HUGEINT) <> 0)) AS other_tokens,
               c.tx_fee, c.priority_fee, c.jito_tip, {top_programs('c.programs')} AS top_programs
        FROM ctx c JOIN txm m USING (slot, tx_index) WHERE NOT c.failed""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE arbs AS
        WITH q AS (
            SELECT *, signer_sol_delta + wsol_delta AS net,
                   CASE WHEN n_store >= 2 AND n_programs = 1 THEN 'same_venue'
                        WHEN n_store >= 2 OR len(other_dex) > 0 THEN 'cross_venue' END AS kind,
                   n_store < 2 AS other_venue_unparsed, other_tokens = 0 AS closed_loop
            FROM cand)
        SELECT * EXCLUDE (rn) FROM (
            SELECT *, row_number() OVER (PARTITION BY slot, tx_index ORDER BY closed_loop DESC, n_store DESC, len(other_dex) DESC, mint) AS rn
            FROM q WHERE kind IS NOT NULL AND abs(mint_delta) <= {FLAT} * max_tokens AND net > 0) WHERE rn = 1""")
    # The prior trade on the first venue: the opportunity the arb closed.
    con.execute(f"""CREATE OR REPLACE TEMP TABLE arbs_out AS
        SELECT a.*, d.day, p.slot AS prior_slot, p.tx_index AS prior_tx, p.fee_payer AS prior_fee_payer, p.sol AS prior_sol,
               p.depth AS prior_depth, p.buy AS prior_buy,
               p.slot IS NOT NULL AND p.fee_payer IS DISTINCT FROM a.fee_payer
                   AND (p.sol >= {TRIGGER_SOL} OR p.sol >= {TRIGGER_DEPTH} * p.depth) AS backrun,
               a.slot - p.slot AS dist_slots,
               CASE WHEN p.slot = a.slot THEN a.tx_index - p.tx_index END AS dist_tx,
               (a.slot - p.slot) * {ms} AS dist_ms,
               COALESCE(a.slot - p.slot >= {REACHABLE_SLOTS}, false) AS reachable,
               net + tx_fee + jito_tip AS gross
        FROM arbs a
        ASOF LEFT JOIN (SELECT venue, k, slot, tx_index, fee_payer, sol, depth, buy FROM legs) p
            ON p.venue = a.first_venue AND a.k > p.k
        ASOF LEFT JOIN days d ON a.slot >= d.slot""")
    # Size and age of the first venue at the arb (spec section 2.8).
    con.execute(f"""CREATE OR REPLACE TEMP TABLE arbs_out AS
        SELECT a.*, l.depth AS depth, (a.slot - COALESCE(ps.create_slot, mt.create_slot)) * {ms} / 60000.0 AS age_min
        FROM arbs_out a
        LEFT JOIN (SELECT l.venue, l.slot, l.tx_index, arg_min(l.depth, l.outer_ix * 1000 + l.inner_ix) AS depth
                   FROM legs l SEMI JOIN arbs x ON l.venue = x.first_venue AND l.slot = x.slot AND l.tx_index = x.tx_index
                   GROUP BY l.venue, l.slot, l.tx_index) l ON l.venue = a.first_venue AND l.slot = a.slot AND l.tx_index = a.tx_index
        LEFT JOIN pools ps ON ps.pool = a.first_venue
        LEFT JOIN mints mt ON mt.mint = a.mint AND a.first_venue = a.mint || ':curve'""")
    return con.execute("SELECT count(*) FROM arbs_out").fetchone()[0]


def find_harm(con):
    """Sandwiches and front-runs on store venues (description only), from consecutive trades per
    venue; a front-run's trader must trade the other way on the venue within 3 slots."""
    con.execute("""CREATE OR REPLACE TEMP TABLE harm AS
        WITH o AS (
            SELECT venue, slot, tx_index, fee_payer, buy, sol,
                   lag(fee_payer) OVER w AS p_fp, lag(buy) OVER w AS p_buy, lag(sol) OVER w AS p_sol, lag(slot) OVER w AS p_slot,
                   lag(tx_index) OVER w AS p_tx, lead(fee_payer) OVER w AS n_fp, lead(buy) OVER w AS n_buy,
                   lead(sol) OVER w AS n_sol, lead(slot) OVER w AS n_slot
            FROM legs WHERE slot IN (SELECT slot FROM days)
            WINDOW w AS (PARTITION BY venue ORDER BY slot, tx_index, outer_ix, inner_ix)),
        mine AS (  -- each trader's next trade on the same venue
            SELECT venue, fee_payer, slot, tx_index, buy, sol, lead(slot) OVER v AS x_slot, lead(buy) OVER v AS x_buy,
                   lead(sol) OVER v AS x_sol
            FROM legs WHERE slot IN (SELECT slot FROM days)
            WINDOW v AS (PARTITION BY venue, fee_payer ORDER BY slot, tx_index, outer_ix, inner_ix))
        SELECT 'sandwich' AS kind, venue, slot, tx_index AS victim_tx, p_fp AS fee_payer, fee_payer AS victim,
               CASE WHEN buy THEN n_sol - p_sol ELSE p_sol - n_sol END AS profit
        FROM o WHERE p_fp = n_fp AND p_fp <> fee_payer AND p_slot = slot AND n_slot = slot AND p_buy = buy AND n_buy <> buy
        UNION ALL  -- a front-run that is the front of a sandwich is counted as the sandwich only
        SELECT 'front_run', o.venue, o.slot, o.tx_index, o.p_fp, o.fee_payer,
               CASE WHEN m.buy THEN m.x_sol - m.sol ELSE m.sol - m.x_sol END
        FROM o JOIN mine m ON m.venue = o.venue AND m.fee_payer = o.p_fp AND m.slot = o.p_slot AND m.tx_index = o.p_tx
        WHERE o.p_fp <> o.fee_payer AND o.p_slot = o.slot AND o.p_buy = o.buy AND o.sol > o.p_sol
          AND m.x_buy <> m.buy AND m.x_slot <= m.slot + 3 AND NOT (o.n_fp = o.p_fp AND o.n_slot = o.slot)""")
    con.execute("""CREATE OR REPLACE TEMP TABLE harm AS
        SELECT h.*, d.day FROM harm h ASOF LEFT JOIN days d ON h.slot >= d.slot""")
    return con.execute("SELECT count(*) FROM harm").fetchone()[0]


def find_signers(con):
    """First and last UTC day per fee payer over every tx_ctx row of the epoch (newcomers, section 2.9)."""
    con.execute("""CREATE OR REPLACE TEMP TABLE signers AS
        WITH s AS (SELECT fee_payer, min(slot) AS lo, max(slot) AS hi, count(*) AS txs FROM ctx GROUP BY fee_payer)
        SELECT s.fee_payer, a.day AS first_day, b.day AS last_day, s.txs
        FROM s ASOF LEFT JOIN days a ON s.lo >= a.slot ASOF LEFT JOIN days b ON s.hi >= b.slot""")
    return con.execute("SELECT count(*) FROM signers").fetchone()[0]


def _price(p, orientation):
    """SOL per token unit of a pool state."""
    if p is None or p.b <= 0 or p.e <= 0:
        return None
    return p.b / p.e if orientation == "reversed" else p.e / p.b


def _buy(V, p, orientation, sol):
    f = V.rev_buy_token(p, sol) if orientation == "reversed" else V.pool_buy_exact_in(p, sol)
    return f.tokens if f else 0


def _sell(V, p, orientation, tokens):
    if tokens <= 0:
        return 0
    f = V.rev_sell_token(p, tokens) if orientation == "reversed" else V.pool_sell(p, tokens)
    return f.trader_quote if f else 0


def best_round_trip(V, a, oa, b, ob):
    """(net lamports, size SOL, direction) of the best buy on one pool and sell on the other over SIZES_SOL,
    after ARB_TX_COST; (None, None, None) without two priced states."""
    pa, pb = _price(a, oa), _price(b, ob)
    if pa is None or pb is None:
        return None, None, None
    best = (None, None, None)
    for (x, ox, y, oy, direction) in ((a, oa, b, ob, "a_to_b"), (b, ob, a, oa, "b_to_a")):
        for s in SIZES_SOL:
            lam = int(s * SOL)
            net = _sell(V, y, oy, _buy(V, x, ox, lam)) - lam - ARB_TX_COST
            if best[0] is None or net > best[0]:
                best = (net, s, direction)
    return best


def find_gaps(con, lo, hi):
    """Unrealized opportunities between two store pools of one mint: per mint with >= 2 SOL-market
    pools trading in the epoch, the best round trip after every trade; a gap runs from the trade
    that opens a net > 0 to the first trade after which it is <= 0 (open_slot to close_slot)."""
    from backtest import streams
    from backtest import venues as V

    pairs = con.execute(f"""
        WITH cand AS (SELECT pool, COALESCE(mint, pool) AS m, orientation FROM pools WHERE quote_class IN ('sol', 'reversed')
                      QUALIFY count(*) OVER (PARTITION BY COALESCE(mint, pool)) >= 2),
             active AS (SELECT DISTINCT pool FROM pool WHERE pool IN (SELECT pool FROM cand) AND slot >= {lo} AND slot < {hi})
        SELECT m, list(pool ORDER BY pool), list(orientation ORDER BY pool)
        FROM cand JOIN active USING (pool) GROUP BY m HAVING count(*) >= 2""").fetchall()
    rows = []
    for mint, pools, orients in pairs:
        st = streams.load_pool_streams(con, {p: (lo, hi - 1) for p in pools})
        orient = dict(zip(pools, orients))
        events = sorted((l.order, p, l) for p in pools if p in st for l in st[p].pool_legs)
        state = {}
        for i in range(len(pools)):
            for j in range(i + 1, len(pools)):
                pa, pb = pools[i], pools[j]
                state.clear()
                for p in (pa, pb):  # a pool's state before its first trade in the epoch
                    if p in st and st[p].pool_legs:
                        state[p] = st[p].pool_legs[0].pre
                gap = None
                for order, p, leg in events:
                    if p not in (pa, pb):
                        continue
                    state[p] = leg.post
                    net, size, direction = best_round_trip(V, state.get(pa), orient[pa], state.get(pb), orient[pb])
                    slot, tx = order[0], order[1]
                    if net is not None and net > 0:
                        if gap is None:
                            gap = {"mint": mint, "pool_a": pa, "pool_b": pb, "open_slot": slot, "open_tx": tx, "best_net": net,
                                   "best_size_sol": size, "direction": direction}
                        elif net > gap["best_net"]:
                            gap.update(best_net=net, best_size_sol=size, direction=direction)
                    elif gap is not None:
                        rows.append({**gap, "close_slot": slot, "close_tx": tx, "censored": False})
                        gap = None
                if gap is not None:
                    rows.append({**gap, "close_slot": hi, "close_tx": None, "censored": True})
    import pyarrow as pa_

    schema = pa_.schema([("mint", pa_.string()), ("pool_a", pa_.string()), ("pool_b", pa_.string()), ("open_slot", pa_.int64()),
                         ("open_tx", pa_.int64()), ("best_net", pa_.int64()), ("best_size_sol", pa_.float64()),
                         ("direction", pa_.string()), ("close_slot", pa_.int64()), ("close_tx", pa_.int64()),
                         ("censored", pa_.bool_())])
    con.register("gaps_rows", pa_.Table.from_pylist(rows, schema=schema))
    # Closed by an arb: the closing transaction is a realized opportunity already (arbs-<e>).
    con.execute(f"""CREATE OR REPLACE TEMP TABLE gaps AS
        SELECT g.*, g.close_slot - g.open_slot AS duration_slots, g.close_slot - g.open_slot >= {REACHABLE_SLOTS} AS reachable,
               a.slot IS NOT NULL AS closed_by_arb, d.day
        FROM gaps_rows g
        LEFT JOIN arbs_out a ON a.slot = g.close_slot AND a.tx_index = g.close_tx AND a.closed_loop
        ASOF LEFT JOIN days d ON g.open_slot >= d.slot""")
    con.unregister("gaps_rows")
    return len(rows)


def find_attempts(con, out, epochs, e, lo, hi, chunks_root):
    """Failed transactions counted as attempts (an upper bound, v3 point 1) for epoch e, against the
    winners and winning program sets of every epoch's arbs."""
    arb_files = [os.path.join(out, f"arbs-{x}.parquet") for x in epochs]
    con.execute(f"CREATE OR REPLACE TEMP VIEW all_arbs AS SELECT * FROM read_parquet({_lit(arb_files)}) WHERE closed_loop")
    setup(con, chunks_root, lo, hi)
    con.execute(f"""CREATE OR REPLACE TEMP TABLE events AS
        SELECT DISTINCT slot FROM read_parquet('{os.path.join(out, f"arbs-{e}.parquet")}') WHERE closed_loop
        UNION SELECT DISTINCT slot FROM legs
              WHERE slot >= {lo} AND slot < {hi} AND (sol >= {TRIGGER_SOL} OR sol >= {TRIGGER_DEPTH} * depth)""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE attempts AS
        WITH f AS (SELECT slot, tx_index, fee_payer, tx_fee, priority_fee, {top_programs()} AS top_programs FROM ctx WHERE failed),
             winners AS (SELECT DISTINCT fee_payer FROM all_arbs),
             sets AS (SELECT DISTINCT top_programs FROM all_arbs WHERE top_programs <> '' AND NOT {router_only()}),
             reach AS (SELECT DISTINCT slot FROM read_parquet('{os.path.join(out, f"arbs-{e}.parquet")}') WHERE reachable AND closed_loop)
        SELECT f.*, f.fee_payer IN (SELECT fee_payer FROM winners) AS by_winner,
               f.top_programs IN (SELECT top_programs FROM sets) AS same_programs,
               (f.slot IN (SELECT slot FROM reach) OR f.slot - 1 IN (SELECT slot FROM reach)) AS reachable,
               f.tx_fee AS cost, d.day
        FROM f ASOF LEFT JOIN days d ON f.slot >= d.slot
        WHERE (f.fee_payer IN (SELECT fee_payer FROM winners) OR f.top_programs IN (SELECT top_programs FROM sets))
          AND (f.slot IN (SELECT slot FROM events) OR f.slot - 1 IN (SELECT slot FROM events))""")
    failed_txs = chunk_files(chunks_root, "failed_txs", lo, hi)
    if failed_txs:  # the tip a failed transaction attempted (it does not land)
        con.execute(f"""CREATE OR REPLACE TEMP TABLE attempts AS
            SELECT a.*, t.jito_tip_attempted FROM attempts a LEFT JOIN
                (SELECT slot, tx_index, jito_tip_attempted FROM read_parquet({_lit(failed_txs)}, union_by_name = true)
                 WHERE slot >= {lo} AND slot < {hi}) t USING (slot, tx_index)""")
    return con.execute("SELECT count(*) FROM attempts").fetchone()[0]


def write(con, table, path):
    tmp = path + ".tmp"
    con.execute(f"COPY (SELECT * FROM {table}) TO '{tmp}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--chunks", required=True, help="the tx_ctx chunks (events/txctx-v1/chunks)")
    ap.add_argument("--epochs", default="1040,1045")
    ap.add_argument("--out", default=os.path.join(OUT_ROOT, "mev0-v1"))
    a = ap.parse_args(argv)
    epochs = [int(x) for x in a.epochs.split(",") if x]
    os.makedirs(a.out, exist_ok=True)
    store = os.environ.get("LAB_STORE", STORE)
    con = connect(store, memory=os.environ.get("LAB_WALLETS_MEMORY", "4GB"), threads=int(os.environ.get("LAB_WALLETS_THREADS", "4")))
    clock = Clock.from_store(con)
    info = {}
    for e in epochs:
        lo, hi = e * EPOCH_SLOTS, (e + 1) * EPOCH_SLOTS
        guard(hi)
        t0 = time.time()
        ms = clock.ms_per_slot(lo)
        setup(con, a.chunks, lo, hi)
        n = {"arbs": find_arbs(con, ms)}
        write(con, "arbs_out", os.path.join(a.out, f"arbs-{e}.parquet"))
        n["harm"] = find_harm(con)
        write(con, "harm", os.path.join(a.out, f"harm-{e}.parquet"))
        n["signers"] = find_signers(con)
        write(con, "signers", os.path.join(a.out, f"signers-{e}.parquet"))
        n["gaps"] = find_gaps(con, lo, hi)
        write(con, "gaps", os.path.join(a.out, f"gaps-{e}.parquet"))
        con.execute(f"""CREATE OR REPLACE TEMP TABLE days_out AS SELECT {e} AS epoch, day, count(*) AS slots, min(slot) AS first_slot,
                        max(slot) AS last_slot, {ms} AS ms_per_slot FROM days GROUP BY day ORDER BY day""")
        write(con, "days_out", os.path.join(a.out, f"days-{e}.parquet"))
        info[e] = {"lo": lo, "hi": hi, "ms_per_slot": ms, "counts": n, "seconds_arbs": round(time.time() - t0)}
        print(f"epoch {e}: {n} ({time.time() - t0:.0f}s)", flush=True)
    for e in epochs:  # attempts need every epoch's winners
        lo, hi = e * EPOCH_SLOTS, (e + 1) * EPOCH_SLOTS
        t0 = time.time()
        info[e]["counts"]["attempts"] = find_attempts(con, a.out, epochs, e, lo, hi, a.chunks)
        write(con, "attempts", os.path.join(a.out, f"attempts-{e}.parquet"))
        info[e]["seconds_attempts"] = round(time.time() - t0)
        with open(os.path.join(a.out, f"run-mev0-{e}.json"), "w") as f:
            json.dump({"spec": SPEC, "commit": git_commit(), "store": store, "chunks": a.chunks,
                       "epochs": epochs, **info[e]}, f, indent=1)
        print(f"epoch {e}: attempts {info[e]['counts']['attempts']} ({time.time() - t0:.0f}s)", flush=True)
    con.close()


if __name__ == "__main__":
    main()
