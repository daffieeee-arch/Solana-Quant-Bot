"""Spec F section 1: runners (x2 / x5 / x10 within 1 h and 6 h) from realistic entry moments, and the point-in-time
features that separate them (week3-specs-runners-2026-10-15.md; roadmap #167). Exploratory on used data (dev2).

Tokens: every mint created in [lo, hi) (store `mints`), its curve and, after migration, its first SOL pool; plus, for
the f7 moment, the reversed pools of the F7 triggers (their token is not a `mints` row: the pool is the key).

Entry moments, each measured on its own:
  c1 / c5 / c15  the first trade at or after create + 1 / 5 / 15 min (block time), on the venue live then
  f7             the F7 trigger t (f7rev parts: F7, tp4_T600, d = 1, every farm; one row per trigger, filled or not)
  m1 / m1e       the M1P / M1E triggers (the m1x triggers parquet of transfers-v1; N2 twins excluded)
The entry is the first trade at least Clock.slots(600 ms, t) slots after the moment: a buy of 0.5 SOL (venue fees
inside the budget) at the venue state before that trade, plus the base scenario's network cost per order. No entry
when nothing can fill there (curve complete without a pool state yet, empty pool).

Measures per entry: the mark at every trade from the entry trade on (its post-state) up to 6 h = the net SOL of a
full sell of the position at that state (price impact, venue fees, network cost; capped at the real SOL of the
venue state PLUS our own net deposit q_in, since 2026-10-24 (spec-runners-mark-cap-2026-10-24.md; before, the cap was the
logged reserve alone and valued a position on a venue thinner than the budget at that reserve: E71 section 3.3; reversed
pools are deliberately unchanged: their constant-product payout is always below b; the sell itself follows the engine's
pool_sell_capped / curve_sell). mult_1h / mult_6h = the highest mark / cost within 1 h /
6 h, t_max the minutes from the entry to that mark, end_6h the mark at the last trade within 6 h, dd_6h the lowest
mark before the maximum. After the curve completes, the mint's SOL pool prices the position (its own state).
Three simple exits on the same marks, as multiples of the cost (x_tp2: the first mark >= 2 x cost; x_tr30 / x_tr50:
the first mark <= 70 % / 50 % of the highest earlier mark; otherwise the 6 h end): the sell is at the state of the
mark that triggers the rule, without the engine's slot delay (modelaanname, a touch optimistic).

The curve state is the row's logged state (vt, vq, rt, rq after the trade; the state before it by +/- t and q, as
streams.py does). Mayhem mints get no curve entry (their pool phase stays): the Mayhem agent (BwWK17cb..., fee tiers
0) resets the curve's virtual SOL inside its own trades, every later trade is priced on the reset state (Lab
Backtest, 2026-10-16), and a replay cannot simulate through an agent leg; all engine universes exclude them too.

Features (only trades before the moment t, windows 'all' since the create, 'pre1' / 'pre5' = the 1 / 5 min before t;
and 'post1' / 'post5' = the 1 / 5 min after the entry, for a hold decision at that later point): net SOL inflow
(all legs; organic = legs not flagged farmer by the store), unique buyers and sellers, share of buyers without a
farmer leg, median buy size, share of buys whose fee payer is another wallet, share of buyers whose fee payer pays
>= 5 buyers of this token (since the create), share of transactions that buy and sell in one transaction,
top-5 holders' share of the circulation (store legs only, no transfers), minutes since the create, the real SOL
depth at the entry, and (report stage) the creator's earlier tokens and their median mult_6h at c5.
share_c (transfers-v1) and the wash flags rt_tx / wash_1h are not here: 'onbekend' in the note.

  LAB_STORE=<dev2> python -m wallets.runners --from-slot A --to-slot B [--f7-parts DIR] [--m1x-triggers FILE]
      [--cfg week2-defaults.yaml] [--out DIR]
Writes entries-<A>.parquet (one row per key x moment x t with an entry: every F7 / M1 trigger of a pool counts) and
run-runners-<A>.json. Slots stay before
the hold-out. Heavy on a full epoch (every trade of every new token joined to its entries): NAS; a slice of a few
thousand slots on the VPS as a smoke test.
"""

# no-quote-filter: consumed data, conclusions audited 2026-10-18 (lab/reports/2026-10-18-audit-niet-sol-quote.md)
import argparse
import json
import os
import sys
import time

from .common import EPOCH_SLOTS, HOLDOUT_START, OUT_ROOT, SOL, Clock, connect, git_commit, regime_label
from .markout import ms_case

MOMENTS_MIN = {"c1": 1, "c5": 5, "c15": 15}
ENTRY_MS = 600
H1_MS = 3_600_000
H6_MS = 21_600_000
SIZE = SOL // 2
BPS = 10_000
DEFAULTS = os.environ.get("LAB_DEFAULTS", "/home/chupa/Solana-project/data-old-faithful-one/lab/research/week2-defaults.yaml")
WINDOWS = ("all", "pre1", "pre5", "post1", "post5")
FEATS = ("inflow", "inflow_org", "buyers", "sellers", "buyers_nofarm", "medbuy", "payer_other", "rt_share")


def progress(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", file=sys.stderr, flush=True)


def _count(con, t):
    return con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]


def tx_cost(cfg_path=DEFAULTS):
    """Network cost per order of the base scenario (fee + tip), lamports."""
    try:
        import yaml
        with open(cfg_path) as f:
            base = yaml.safe_load(f)["costs"]["scenarios"]["base"]
        return int(round((float(base["fee_per_tx_sol"]) + float(base["tip_sol"])) * SOL))
    except (OSError, KeyError, TypeError):
        return 705_000


def horizon(con, hi, clock):
    """hi_ext: the mark horizon (H6_MS after the window's last slot), never into the hold-out."""
    h6 = f"greatest(1, round({H6_MS} / ({ms_case(clock, str(hi - 1))})))::BIGINT"
    return min(HOLDOUT_START, hi + int(con.execute(f"SELECT {h6}").fetchone()[0]) + 10)


def setup(con, lo, hi, clock, f7_parts=None, m1x_triggers=None):
    """Tokens, legs (curve and pool, with pre- and post-states) and the moments of the window."""
    hi_ext = horizon(con, hi, clock)
    # tokens: mints created in the window, with their first SOL pool
    con.execute(f"""CREATE OR REPLACE TEMP TABLE tok AS
        SELECT m.mint AS key, m.mint, m.create_slot, m.creator, m.mayhem, m.complete_slot, true AS has_curve,
               p.pool, p.orientation, p.pool_slot
        FROM mints m LEFT JOIN (
            SELECT mint, arg_min(pool, (CASE WHEN quote_class = 'sol' THEN 0 ELSE 1 END, create_slot)) AS pool,
                   arg_min(orientation, (CASE WHEN quote_class = 'sol' THEN 0 ELSE 1 END, create_slot)) AS orientation,
                   min(create_slot) AS pool_slot
            FROM pools WHERE quote_class IN ('sol', 'reversed') GROUP BY mint) p ON p.mint = m.mint
        WHERE m.create_slot >= {lo} AND m.create_slot < {hi}""")
    # the F7 triggers' reversed pools: the pool is the key (its token is not a mints row)
    if f7_parts:
        con.execute(f"""INSERT INTO tok
            SELECT DISTINCT f.pool, f.mint, ps.create_slot, ps.coin_creator, ps.mayhem, NULL, false, f.pool, ps.orientation, ps.create_slot
            FROM read_parquet('{f7_parts}/*.parquet', union_by_name = true) f JOIN pools ps ON ps.pool = f.pool
            WHERE f.family = 'F7' AND f.variant = 'tp4_T600' AND f.d = 1 AND f.size_sol = 0.5 AND f.q = 0.5
              AND f.t >= {lo} AND f.t < {hi} AND f.pool NOT IN (SELECT key FROM tok)""")
    progress(f"tok {_count(con, 'tok')} (mints {lo}..{hi}, pools of F7 triggers)")
    legs(con, lo, hi_ext)
    moments(con, lo, hi, clock, f7_parts, m1x_triggers)
    return hi_ext


def legs(con, lo, hi_ext, mayhem_curve=False):
    """cl / pl from the tok table: curve and pool legs with pre- and post-states. Mayhem curve legs only when asked
    (wallets.probes E71, a labelled model assumption: our buy does not change the Mayhem agent's resets)."""
    # curve legs: the mint's constant vq offset from its event rows; fee tiers = the mint's modal event tiers
    # curve legs of the non-mayhem mints: the logged post-state and the pre-state (streams.py), fee tiers from the row
    con.execute(f"""CREATE OR REPLACE TEMP TABLE cl AS
        SELECT c.mint AS key, 'curve' AS venue, NULL::VARCHAR AS orientation,
               c.slot * 1000000000 + c.tx_index * 100000 + c.outer_ix * 1000 + c.inner_ix AS ord, c.slot, c.tx_index, c.is_buy,
               c.q::HUGEINT AS sol, c.t::HUGEINT AS tokens, c.trader, c.fee_payer, false AS farmer,
               c.vt::HUGEINT AS vt, c.vq::HUGEINT AS vq, c.rt::HUGEINT AS rt, c.rq::HUGEINT AS rq,
               CASE WHEN c.is_buy THEN c.vt + c.t ELSE c.vt - c.t END::HUGEINT AS vt_pre,
               CASE WHEN c.is_buy THEN c.vq - c.q ELSE c.vq + c.q END::HUGEINT AS vq_pre,
               CASE WHEN c.is_buy THEN c.rt + c.t ELSE c.rt - c.t END::HUGEINT AS rt_pre,
               CASE WHEN c.is_buy THEN c.rq - c.q ELSE c.rq + c.q END::HUGEINT AS rq_pre,
               COALESCE(c.fee_bps, 0) AS fee_bps, COALESCE(c.creator_bps, 0) AS creator_bps
        FROM curve c JOIN tok t ON t.mint = c.mint AND t.has_curve AND (NOT COALESCE(t.mayhem, false) OR {str(bool(mayhem_curve)).lower()}) WHERE c.slot >= {lo} AND c.slot < {hi_ext}""")
    progress(f"curve legs {_count(con, 'cl')}")
    # pool legs: post-states as streams.pool_leg (buy / sell: b -/+ base, e + e_delta; boost: b - base, e + e_delta;
    # withdraw / deposit: vault + last virtual quote -/+ quote_user, b -/+ base); the pre-state of a trade is the row
    con.execute(f"""CREATE OR REPLACE TEMP TABLE pl AS
        WITH raw AS (
            SELECT t.key, p.pool, p.slot, p.tx_index, p.outer_ix, p.inner_ix, p.kind, p.b, p.e, p.vault, COALESCE(p.vq, 0) AS vq, p.base, p.e_delta, p.quote_user,
                   p.lp_bps, p.protocol_bps, p.creator_bps, p.cashback_bps, p.token_buy, p.sol_amount, p.token_amount, p.trader, p.fee_payer, p.farmer, t.orientation
            FROM pool p JOIN tok t ON t.pool = p.pool WHERE p.slot >= {lo} AND p.slot < {hi_ext} AND p.kind IN ('buy', 'sell', 'boost', 'withdraw', 'deposit')),
        trades AS (
            SELECT *, b AS b_pre, e AS e_pre, CASE WHEN kind = 'sell' THEN b + base ELSE b - base END AS b_post, e + e_delta AS e_post, vq AS virt,
                   COALESCE(lp_bps, 0) AS lp, COALESCE(protocol_bps, 0) AS pr, COALESCE(creator_bps, 0) AS cr, COALESCE(cashback_bps, 0) AS cb
            FROM raw WHERE kind IN ('buy', 'sell')),
        virts AS (SELECT key, slot, tx_index, outer_ix, inner_ix, vq AS virt FROM raw WHERE kind IN ('buy', 'sell', 'boost')),
        others AS (
            SELECT o.*, NULL::BIGINT AS b_pre, NULL::BIGINT AS e_pre,
                   CASE WHEN o.kind = 'deposit' THEN o.b + o.base ELSE o.b - o.base END AS b_post,
                   CASE WHEN o.kind = 'boost' THEN o.e + o.e_delta WHEN o.kind = 'withdraw' THEN o.vault + COALESCE(v.virt, 0) - o.quote_user
                        ELSE o.vault + COALESCE(v.virt, 0) + o.quote_user END AS e_post,
                   CASE WHEN o.kind = 'boost' THEN o.vq ELSE COALESCE(v.virt, 0) END AS virt,
                   COALESCE(t.lp, 0) AS lp, COALESCE(t.pr, 0) AS pr, COALESCE(t.cr, 0) AS cr, COALESCE(t.cb, 0) AS cb
            FROM (SELECT * FROM raw WHERE kind IN ('boost', 'withdraw', 'deposit')) o
            ASOF LEFT JOIN trades t ON t.key = o.key AND o.slot >= t.slot
            ASOF LEFT JOIN virts v ON v.key = o.key AND o.slot >= v.slot)
        SELECT key, 'pool' AS venue, orientation, slot * 1000000000 + tx_index * 100000 + outer_ix * 1000 + inner_ix AS ord, slot, tx_index, kind,
               kind IN ('buy', 'sell') AS trade, token_buy AS is_buy, sol_amount::HUGEINT AS sol, token_amount::HUGEINT AS tokens, trader, fee_payer, COALESCE(farmer, false) AS farmer,
               b_pre::HUGEINT AS b_pre, e_pre::HUGEINT AS e_pre, b_post::HUGEINT AS b_post, e_post::HUGEINT AS e_post, virt::HUGEINT AS virt, lp, pr, cr, cb
        FROM (SELECT * FROM trades UNION ALL SELECT * FROM others)""")
    progress(f"pool legs {_count(con, 'pl')}")


def moments(con, lo, hi, clock, f7_parts=None, m1x_triggers=None):
    """trades_all, moments and mom (c1 / c5 / c15 and the f7 / m1 triggers) from tok, cl and pl."""
    # the moments: c1 / c5 / c15 = the first trade (any venue) whose block time is >= create time + X min; f7 / m1 / m1e = triggers
    con.execute(f"""CREATE OR REPLACE TEMP TABLE trades_all AS
        SELECT key, ord, slot, is_buy, sol, tokens, trader, fee_payer, farmer, tx_index FROM cl
        UNION ALL SELECT key, ord, slot, is_buy, sol, tokens, trader, fee_payer, farmer, tx_index FROM pl WHERE trade""")
    mins = ", ".join(f"('{k}', {v})" for k, v in MOMENTS_MIN.items())
    con.execute(f"""CREATE OR REPLACE TEMP TABLE moments AS
        WITH ct AS (SELECT t.key, b.block_time AS t0 FROM tok t JOIN blocks b ON b.slot = t.create_slot WHERE t.has_curve),
             cand AS (SELECT a.key, m.moment, min(a.slot) AS t
                      FROM trades_all a JOIN ct USING (key) JOIN blocks b ON b.slot = a.slot, (SELECT * FROM (VALUES {mins}) v(moment, minutes)) m
                      WHERE b.block_time >= ct.t0 + m.minutes * 60 GROUP BY 1, 2)
        SELECT key, moment, t FROM cand""")
    if f7_parts:
        con.execute(f"""INSERT INTO moments
            SELECT DISTINCT f.pool, 'f7', f.t::BIGINT FROM read_parquet('{f7_parts}/*.parquet', union_by_name = true) f SEMI JOIN tok ON tok.key = f.pool
            WHERE f.family = 'F7' AND f.variant = 'tp4_T600' AND f.d = 1 AND f.size_sol = 0.5 AND f.q = 0.5 AND f.t >= {lo} AND f.t < {hi}""")
    if m1x_triggers:
        con.execute(f"""INSERT INTO moments
            SELECT DISTINCT q.mint, CASE WHEN q.family = 'M1P' THEN 'm1' ELSE 'm1e' END, q.t::BIGINT
            FROM read_parquet('{m1x_triggers}') q SEMI JOIN tok ON tok.key = q.mint WHERE q.family IN ('M1P', 'M1E') AND q.t >= {lo} AND q.t < {hi}""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE mom AS
        SELECT m.key, m.moment, m.t, greatest(1, round({ENTRY_MS} / ({ms_case(clock, 'm.t')})))::BIGINT AS d_slots,
               greatest(1, round({H1_MS} / ({ms_case(clock, 'm.t')})))::BIGINT AS h1, greatest(1, round({H6_MS} / ({ms_case(clock, 'm.t')})))::BIGINT AS h6,
               greatest(1, round(60000 / ({ms_case(clock, 'm.t')})))::BIGINT AS m1s, greatest(1, round(300000 / ({ms_case(clock, 'm.t')})))::BIGINT AS m5s
        FROM moments m""")
    progress(f"moments {_count(con, 'mom')}: " + str(con.execute("SELECT moment, count(*) FROM mom GROUP BY 1 ORDER BY 1").fetchall()))


def _fees(x, *bps):
    return " + ".join(f"ceil(({x}) * {b} / {BPS}.0)::HUGEINT" for b in bps)


def _net_for_budget(budget, *bps):
    """SQL for the engine's net_for_budget: the largest n with n + sum(ceil(n * bps / 10000)) <= budget. n0 = (budget - k) * BPS
    // (BPS + sum bps) never exceeds it and is at most k below it (k fee terms, each ceil adds < 1): k + 1 candidates."""
    k = len(bps)
    n0 = f"((({budget}) - {k}) * {BPS}) // ({BPS} + {' + '.join(str(b) for b in bps)})"
    cases = " ".join(f"WHEN ({n0} + {j}) + ({_fees(f'{n0} + {j}', *bps)}) <= ({budget}) THEN {n0} + {j}" for j in range(k, 0, -1))
    return f"(CASE {cases} ELSE {n0} END)"


def _pool_mark(pos, b, e, virt, q_in):
    """SQL: the gross SOL a full sell of `pos` tokens into a normal pool at the logged state (b tokens, e quote incl. the
    virtual part `virt`) can realise: the constant-product output e * pos // (b + pos), capped at the real quote reserve
    e - virt plus our own deposit q_in (the logged states lack it: spec-runners-mark-cap-2026-10-24). The output grows
    with the tokens sold, so the most any sell can take out of a real reserve R is min(out(pos), R), within e/b lamports
    above the largest sell the reserve can really pay (one token's worth of rounding); no token inversion
    (the R * b // virt inversion of #552 solved out(x) = R only for q_in = 0: Codex finding #3, 2026-10-24)."""
    return f"least(({e}) * ({pos}) // (({b}) + ({pos})), greatest(0, ({e}) - ({virt}) + ({q_in})))"


def entries(con, cost_tx):
    """The entry trade of every moment (the first fillable trade >= t + d_slots) and the fill at its pre-state."""
    con.execute(f"""CREATE OR REPLACE TEMP TABLE cand AS
        SELECT m.key, m.moment, m.t, m.d_slots, m.h1, m.h6, m.m1s, m.m5s, l.ord, l.slot, l.venue, l.orientation,
               l.vt_pre, l.vq_pre, l.rt_pre, l.rq_pre, l.fee_bps, l.creator_bps, l.b_pre, l.e_pre, l.virt, l.lp, l.pr, l.cr, l.cb,
               row_number() OVER (PARTITION BY m.key, m.moment, m.t ORDER BY l.ord) AS rn
        FROM mom m JOIN (
            SELECT key, ord, slot, venue, orientation, vt_pre, vq_pre, rt_pre, rq_pre, fee_bps, creator_bps,
                   NULL::HUGEINT AS b_pre, NULL::HUGEINT AS e_pre, NULL::HUGEINT AS virt, NULL AS lp, NULL AS pr, NULL AS cr, NULL AS cb
            FROM cl WHERE rt_pre > 0
            UNION ALL
            SELECT key, ord, slot, venue, orientation, NULL, NULL, NULL, NULL, NULL, NULL, b_pre, e_pre, virt, lp, pr, cr, cb
            FROM pl WHERE trade AND b_pre > 0 AND e_pre > 0) l
        ON l.key = m.key AND l.slot >= m.t + m.d_slots""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE ent AS
        WITH c AS (SELECT * FROM cand WHERE rn = 1),
        f AS (
            SELECT *,
              -- curve: net quote for the budget, tokens by the constant product on the pre-state, capped at the real tokens
              CASE WHEN venue = 'curve' THEN {_net_for_budget(f'{SIZE}::HUGEINT', 'fee_bps', 'creator_bps')} END::HUGEINT AS cq,
              CASE WHEN venue = 'pool' AND orientation = 'normal' THEN {_net_for_budget(f'{SIZE}::HUGEINT', 'lp', 'pr', 'cr', 'cb')} END::HUGEINT AS pn,
              CASE WHEN venue = 'pool' AND orientation = 'reversed' THEN (e_pre * {SIZE}) // (b_pre + {SIZE}) END::HUGEINT AS rg
            FROM c),
        g AS (
            SELECT *,
              CASE WHEN venue = 'curve' THEN least(rt_pre, ((cq - 1) * vt_pre) // (vq_pre + cq - 1))
                   WHEN orientation = 'normal' THEN (b_pre * (pn - 1)) // (e_pre + pn - 1)
                   WHEN rg <= e_pre - virt THEN rg - ({_fees('rg', 'lp', 'pr', 'cr', 'cb')}) END::HUGEINT AS tokens,
              CASE WHEN venue = 'curve' THEN rq_pre WHEN orientation = 'normal' THEN e_pre - virt ELSE b_pre END::HUGEINT AS depth
            FROM f)
        SELECT key, moment, t, d_slots, h1, h6, m1s, m5s, ord AS entry_ord, slot AS entry_slot, venue, orientation, depth, tokens,
               -- the curve fill capped at the real tokens buys exactly rt_pre (curve_buy_exact_out): q = rt * vq // (vt - rt) + 1 plus fees
               CASE WHEN venue = 'curve' AND tokens = rt_pre
                    THEN (rt_pre * vq_pre) // (vt_pre - rt_pre) + 1 + ({_fees('(rt_pre * vq_pre) // (vt_pre - rt_pre) + 1', 'fee_bps', 'creator_bps')}) + {cost_tx}
                    ELSE {SIZE} + {cost_tx} END::HUGEINT AS cost,
               -- q_in: our own net deposit into the venue's real reserve (after the venue fees), which the logged states do not
               -- contain; marks add it to the real reserve before capping a sell (spec-runners-mark-cap-2026-10-24.md)
               CASE WHEN venue = 'curve' AND tokens = rt_pre THEN (rt_pre * vq_pre) // (vt_pre - rt_pre) + 1
                    WHEN venue = 'curve' THEN cq
                    WHEN orientation = 'normal' THEN pn
                    ELSE {SIZE} END::HUGEINT AS q_in
        FROM g WHERE tokens IS NOT NULL AND tokens > 0 AND (venue = 'curve' OR orientation = 'reversed' OR tokens < b_pre)""")
    progress(f"entries {_count(con, 'ent')} of {_count(con, 'mom')} moments (one per key x moment x t): " + str(con.execute("SELECT moment, venue, count(*) FROM ent GROUP BY 1, 2 ORDER BY 1, 2").fetchall()))


def marks(con, cost_tx, hi_ext):
    """Net mark of every entry at every trade from the entry trade on, within 6 h; then the multiples."""
    con.execute(f"""CREATE OR REPLACE TEMP TABLE mk AS
        WITH st AS (
            SELECT key, ord, slot, 'curve' AS venue, NULL::VARCHAR AS orientation, vt, vq, rt, rq, fee_bps, creator_bps,
                   NULL::HUGEINT AS b, NULL::HUGEINT AS e, NULL::HUGEINT AS virt, NULL AS lp, NULL AS pr, NULL AS cr, NULL AS cb FROM cl
            UNION ALL
            SELECT key, ord, slot, 'pool', orientation, NULL, NULL, NULL, NULL, NULL, NULL, b_post, e_post, virt, lp, pr, cr, cb FROM pl),
        j AS (
            SELECT e.key, e.moment, e.t, e.entry_ord, e.entry_slot, e.h1, e.cost, e.tokens AS pos, e.q_in, s.ord, s.slot, s.venue, s.orientation,
                   s.vt, s.vq, s.rt, s.rq, s.fee_bps, s.creator_bps, s.b, s.e, s.virt, s.lp, s.pr, s.cr, s.cb
            FROM ent e JOIN st s ON s.key = e.key AND s.ord >= e.entry_ord AND s.slot <= e.entry_slot + e.h6),
        q AS (
            SELECT *,
              -- the real reserve available to our sell = the logged reserve + our own deposit q_in (the logged states lack it;
              -- without q_in a position on a venue thinner than the budget is valued at the reserve: E71 section 3.3)
              CASE WHEN venue = 'curve' AND rt > 0 THEN least(rq + q_in, (pos * vq) // (vt + pos)) END::HUGEINT AS cq,
              CASE WHEN venue = 'pool' AND orientation = 'normal' AND b > 0 AND e > 0 THEN {_pool_mark('pos', 'b', 'e', 'virt', 'q_in')} END::HUGEINT AS pg,
              CASE WHEN venue = 'pool' AND orientation = 'reversed' AND b > 0 AND e > 0 THEN {_net_for_budget('pos', 'lp', 'pr', 'cr', 'cb')} END::HUGEINT AS rn
            FROM j),
        n AS (
            SELECT key, moment, t, entry_ord, entry_slot, h1, cost, ord, slot, venue,
              CASE WHEN cq IS NOT NULL THEN greatest(0, cq - ({_fees('cq', 'fee_bps', 'creator_bps')})) - {cost_tx}
                   WHEN pg IS NOT NULL THEN greatest(0, pg - ({_fees('pg', 'lp', 'pr', 'cr', 'cb')})) - {cost_tx}
                   WHEN rn IS NOT NULL AND rn > 1 THEN (b * (rn - 1)) // (e + rn - 1) - {cost_tx} END::HUGEINT AS net
            FROM q)
        SELECT * FROM n WHERE net IS NOT NULL""")
    progress(f"marks {_count(con, 'mk')}")
    # three simple exits on the same marks (modelaanname: the sell is at the state of the mark that triggers the rule, no
    # slot delay): tp2 = the first mark >= 2 x cost; tr30 / tr50 = the first mark <= 70 % / 50 % of the highest earlier
    # mark; without a trigger the 6 h end. A one-step drain triggers the trail on the drained state: the rug is kept.
    con.execute(f"""CREATE OR REPLACE TEMP TABLE agg AS
        WITH w AS (
            SELECT *, max(net) OVER (PARTITION BY key, moment, t ORDER BY ord ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS pmax FROM mk),
        a AS (
            SELECT key, moment, t, count(*) AS n_marks, max(net) FILTER (WHERE slot <= entry_slot + h1) AS max1, max(net) AS max6,
                   arg_max(ord, (net, -ord)) AS ord_max, arg_max(slot, (net, -ord)) AS slot_max, arg_max(net, ord) AS last_net, max(slot) AS last_slot,
                   count(*) FILTER (WHERE venue = 'curve') AS n_curve, count(*) FILTER (WHERE venue = 'pool') AS n_pool,
                   arg_min(net, ord) FILTER (WHERE net >= 2 * cost) AS x_tp2,
                   arg_min(net, ord) FILTER (WHERE net <= 0.7 * pmax) AS x_tr30,
                   arg_min(net, ord) FILTER (WHERE net <= 0.5 * pmax) AS x_tr50
            FROM w GROUP BY 1, 2, 3)
        SELECT a.*, d.dd_net
        FROM a JOIN (SELECT a.key, a.moment, a.t, min(m.net) AS dd_net FROM a JOIN mk m ON m.key = a.key AND m.moment = a.moment AND m.t = a.t AND m.ord <= a.ord_max GROUP BY 1, 2, 3) d
             USING (key, moment, t)""")
    progress(f"agg {_count(con, 'agg')}")


def features(con):
    """Point-in-time features per (key, moment, t): windows all / pre1 / pre5 before t and post1 / post5 after the entry."""
    win = {"all": "l.slot <= m.t", "pre1": "l.slot > m.t - m.m1s AND l.slot <= m.t", "pre5": "l.slot > m.t - m.m5s AND l.slot <= m.t",
           "post1": "l.slot > m.entry_slot AND l.slot <= m.entry_slot + m.m1s", "post5": "l.slot > m.entry_slot AND l.slot <= m.entry_slot + m.m5s"}

    def cols(w, cond):
        return [f"sum(CASE WHEN l.is_buy THEN l.sol ELSE -l.sol END) FILTER (WHERE {cond}) / 1e9 AS {w}_inflow",
                f"sum(CASE WHEN l.is_buy THEN l.sol ELSE -l.sol END) FILTER (WHERE {cond} AND NOT l.farmer) / 1e9 AS {w}_inflow_org",
                f"count(DISTINCT l.trader) FILTER (WHERE {cond} AND l.is_buy) AS {w}_buyers",
                f"count(DISTINCT l.trader) FILTER (WHERE {cond} AND NOT l.is_buy) AS {w}_sellers",
                f"count(DISTINCT l.trader) FILTER (WHERE {cond} AND l.is_buy AND NOT l.farmer)::DOUBLE / nullif(count(DISTINCT l.trader) FILTER (WHERE {cond} AND l.is_buy), 0) AS {w}_buyers_nofarm",
                f"median(l.sol) FILTER (WHERE {cond} AND l.is_buy) / 1e9 AS {w}_medbuy",
                f"avg((l.fee_payer IS NOT NULL)::INT) FILTER (WHERE {cond} AND l.is_buy) AS {w}_payer_other"]  # store: NULL = pays itself
    near = [c for w, cond in win.items() if w != "all" for c in cols(w, cond)]
    con.execute(f"""CREATE OR REPLACE TEMP TABLE fe AS
        SELECT m.key, m.moment, m.t, {', '.join(near)}
        FROM ent m JOIN trades_all l ON l.key = m.key AND l.slot > m.t - m.m5s AND l.slot <= m.entry_slot + m.m5s
        GROUP BY 1, 2, 3""")
    # the 'all' window reaches back to the create: its own pass over every leg <= t
    con.execute(f"""CREATE OR REPLACE TEMP TABLE fe_all AS
        SELECT m.key, m.moment, m.t, {', '.join(cols('all', 'true'))}
        FROM ent m JOIN trades_all l ON l.key = m.key AND l.slot <= m.t GROUP BY 1, 2, 3""")
    # same-transaction round trips per window; payer hubs and top-5 holders since the create
    con.execute("""CREATE OR REPLACE TEMP TABLE txs AS
        SELECT key, slot, tx_index, bool_or(is_buy) AND bool_or(NOT is_buy) AS both FROM trades_all GROUP BY 1, 2, 3""")
    rt = ", ".join(f"avg(x.both::INT) FILTER (WHERE {cond.replace('l.', 'x.')}) AS {w}_rt_share" for w, cond in win.items())
    con.execute(f"""CREATE OR REPLACE TEMP TABLE fe_rt AS
        SELECT m.key, m.moment, m.t, {rt} FROM ent m JOIN txs x ON x.key = m.key AND x.slot <= m.entry_slot + m.m5s GROUP BY 1, 2, 3""")
    con.execute("""CREATE OR REPLACE TEMP TABLE fe_hub AS
        WITH pb AS (SELECT m.key, m.moment, m.t, COALESCE(l.fee_payer, l.trader) AS fee_payer, l.trader FROM ent m JOIN trades_all l ON l.key = m.key AND l.slot <= m.t AND l.is_buy GROUP BY ALL),
             pc AS (SELECT key, moment, t, fee_payer, count(*) AS n FROM pb GROUP BY 1, 2, 3, 4)
        SELECT pb.key, pb.moment, pb.t, count(DISTINCT pb.trader) FILTER (WHERE pc.n >= 5)::DOUBLE / count(DISTINCT pb.trader) AS payer_hub
        FROM pb JOIN pc USING (key, moment, t, fee_payer) GROUP BY 1, 2, 3""")
    con.execute("""CREATE OR REPLACE TEMP TABLE fe_top AS
        WITH h AS (SELECT m.key, m.moment, m.t, l.trader, sum(CASE WHEN l.is_buy THEN l.tokens ELSE -l.tokens END) AS net
                   FROM ent m JOIN trades_all l ON l.key = m.key AND l.slot <= m.t GROUP BY 1, 2, 3, 4),
             r AS (SELECT *, row_number() OVER (PARTITION BY key, moment, t ORDER BY net DESC) AS rk FROM h WHERE net > 0)
        SELECT key, moment, t, sum(net) FILTER (WHERE rk <= 5)::DOUBLE / sum(net) AS top5_share, count(*) AS holders FROM r GROUP BY 1, 2, 3""")
    progress("features")


def assemble(con, clock, lo):
    regimes = " ".join(f"WHEN {e} THEN '{regime_label(v)}'" for e, v in sorted(clock.per_epoch.items())) if clock.per_epoch else ""
    reg = f"CASE e.entry_slot // {EPOCH_SLOTS} {regimes} ELSE '{regime_label(clock.ms_per_slot(lo))}' END" if regimes else f"'{regime_label(clock.ms_per_slot(lo))}'"
    con.execute(f"""CREATE OR REPLACE TEMP TABLE out AS
        SELECT e.key, t.mint, e.moment, e.t, e.entry_slot, e.entry_ord, e.venue AS entry_venue,
               CASE WHEN e.venue = 'pool' AND e.orientation = 'reversed' THEN 'pool_reversed' WHEN e.venue = 'pool' THEN 'pool' ELSE 'curve' END AS segment,
               {reg} AS regime, epoch_ms(b.block_time * 1000)::DATE AS day,
               e.depth / 1e9 AS depth_sol, e.tokens, e.cost / 1e9 AS cost_sol,
               (e.entry_slot - t.create_slot) * ({ms_case(clock, 'e.entry_slot')}) / 60000.0 AS min_since_create,
               t.create_slot, t.creator, t.mayhem, t.has_curve,
               a.n_marks, a.n_curve, a.n_pool, a.max1 / e.cost::DOUBLE AS mult_1h, a.max6 / e.cost::DOUBLE AS mult_6h,
               (a.slot_max - e.entry_slot) * ({ms_case(clock, 'e.entry_slot')}) / 60000.0 AS t_max_min,
               a.last_net / e.cost::DOUBLE AS end_6h, a.dd_net / e.cost::DOUBLE AS dd_6h,
               COALESCE(a.x_tp2, a.last_net) / e.cost::DOUBLE AS x_tp2, COALESCE(a.x_tr30, a.last_net) / e.cost::DOUBLE AS x_tr30,
               COALESCE(a.x_tr50, a.last_net) / e.cost::DOUBLE AS x_tr50,
               (a.last_slot - e.entry_slot) * ({ms_case(clock, 'e.entry_slot')}) / 60000.0 AS last_min,
               e.h6 AS h6_slots, e.entry_slot + e.h6 >= {HOLDOUT_START} AS cut,
               fa.* EXCLUDE (key, moment, t), fe.* EXCLUDE (key, moment, t), fr.* EXCLUDE (key, moment, t), fh.payer_hub, ft.top5_share, ft.holders
        FROM ent e JOIN tok t ON t.key = e.key JOIN blocks b ON b.slot = e.entry_slot JOIN agg a ON a.key = e.key AND a.moment = e.moment AND a.t = e.t
        LEFT JOIN fe_all fa ON fa.key = e.key AND fa.moment = e.moment AND fa.t = e.t LEFT JOIN fe ON fe.key = e.key AND fe.moment = e.moment AND fe.t = e.t
        LEFT JOIN fe_rt fr ON fr.key = e.key AND fr.moment = e.moment AND fr.t = e.t
        LEFT JOIN fe_hub fh ON fh.key = e.key AND fh.moment = e.moment AND fh.t = e.t LEFT JOIN fe_top ft ON ft.key = e.key AND ft.moment = e.moment AND ft.t = e.t""")
    return _count(con, "out")


def run(lo, hi, out_dir, f7_parts=None, m1x_triggers=None, cfg=DEFAULTS, store=None, memory=None, threads=None):
    if hi > HOLDOUT_START:
        raise ValueError(f"slot {hi} reaches into the hold-out (starts at {HOLDOUT_START})")
    t0 = time.time()
    con = connect(store or os.environ.get("LAB_STORE", "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb"),
                  memory or os.environ.get("LAB_WALLETS_MEMORY", "1GB"), int(threads or os.environ.get("LAB_WALLETS_THREADS", "2")))
    clock = Clock.from_store(con)
    cost = tx_cost(cfg)
    hi_ext = setup(con, lo, hi, clock, f7_parts, m1x_triggers)
    entries(con, cost)
    marks(con, cost, hi_ext)
    features(con)
    n = assemble(con, clock, lo)
    os.makedirs(out_dir, exist_ok=True)
    con.execute(f"COPY out TO '{out_dir}/entries-{lo}.parquet' (FORMAT PARQUET)")
    info = {"lo": lo, "hi": hi, "hi_ext": hi_ext, "entries": n, "moments": _count(con, "mom"), "tokens": _count(con, "tok"),
            "curve_excluded": {"mayhem": con.execute("SELECT count(*) FROM tok WHERE has_curve AND COALESCE(mayhem, false)").fetchone()[0]},
            "curve_rows_without_fee_tiers": con.execute("SELECT count(*) FROM cl WHERE fee_bps = 0").fetchone()[0],
            "tx_cost_lamports": cost, "size_lamports": SIZE, "commit": git_commit(), "seconds": round(time.time() - t0, 1),
            "per_moment": {m: c for m, c in con.execute("SELECT moment, count(*) FROM out GROUP BY 1").fetchall()}}
    with open(f"{out_dir}/run-runners-{lo}.json", "w") as f:
        json.dump(info, f, indent=1, default=str)
    progress(f"done: {n} entries in {info['seconds']} s -> {out_dir}/entries-{lo}.parquet")
    return info


def main(argv=None):
    ap = argparse.ArgumentParser(description="Spec F section 1: runners from realistic entry moments (one window)")
    ap.add_argument("--from-slot", type=int, required=True)
    ap.add_argument("--to-slot", type=int, required=True)
    ap.add_argument("--f7-parts")
    ap.add_argument("--m1x-triggers")
    ap.add_argument("--cfg", default=DEFAULTS)
    ap.add_argument("--out", default=os.path.join(OUT_ROOT, "runners-v1"))
    a = ap.parse_args(argv)
    info = run(a.from_slot, a.to_slot, a.out, a.f7_parts, a.m1x_triggers, a.cfg)
    print(json.dumps({k: v for k, v in info.items() if k != "per_moment"}, default=str))


if __name__ == "__main__":
    main()
