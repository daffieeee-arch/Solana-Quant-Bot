"""S1: hourly momentum rotation in deep pools (week2-specs-S1-O7-2026-10-10.md).

Every UTC hour the pools of the universe are ranked by score = r_L / (sigma_5min * sqrt(L / 5 min)),
from 5-minute mid prices (state after the last trade of each block). The portfolio holds up to
three pools, each of size S: a position stays while its pool is in the top 6 with score > 0;
free places are filled from the top 3 (r_L > 0, score >= 1). Everything at hour H uses events
before H only.

The ranking gives each position a planned exit hour; the replay adds the emergency exits (real SOL
depth < 0.5 Q_min, mark <= -20%). Deviation: a place freed by an emergency exit stays in cash
until the position's planned exit hour (the precomputed portfolio does not refill it).

N2_S1: for every S1 entry, 3 random pools of the same universe and hour (outside the top 3), held
for the same planned hours with the same emergency exits.
"""

import bisect
import math
from collections import defaultdict

import numpy as np

from .strategies import ExitRule, _mix, _seed, f6_min_depth, pool_universe_sql

HOUR_S, BLOCK_S = 3600, 300
LOOKBACKS = {"L1h": 12, "L4h": 48, "L24h": 288}  # in 5-minute blocks
MIN_WALLETS = 30
TOP, KEEP = 3, 6
N2_K = 3
S1_SIZES = (0.5, 2, 5, 10, 25)


def _ev_sql(lo, hi):
    """Buy/sell events of pools that ever reach the smallest Q_min, with block time and the
    post-trade token price in SOL (both orientations)."""
    return f"""
        SELECT e.pool, b.block_time, e.slot, e.tx_index, e.outer_ix, e.inner_ix, e.trader, e.token_buy, e.farmer,
               e.sol_amount, e.sol_depth,
               CASE WHEN p.orientation = 'normal'
                    THEN (e.e + e.e_delta)::DOUBLE / (CASE WHEN e.token_buy THEN e.b - e.base ELSE e.b + e.base END)
                    ELSE (CASE WHEN e.token_buy THEN e.b + e.base ELSE e.b - e.base END)::DOUBLE / (e.e + e.e_delta) END AS price
        FROM pool e JOIN pools p USING (pool) JOIN blocks b ON b.slot = e.slot
        WHERE {pool_universe_sql()} AND e.kind IN ('buy', 'sell') AND e.slot >= {lo} AND e.slot < {hi}
          AND e.pool IN (SELECT pool FROM s1_pools)"""


def _prepare(con, lo, hi):
    con.execute(f"""CREATE OR REPLACE TEMP TABLE s1_pools AS
                    SELECT DISTINCT pool FROM pool WHERE kind IN ('buy', 'sell') AND sol_depth >= {f6_min_depth(0.5)}
                      AND slot >= {lo} AND slot < {hi}""")


def hourly_features(con, lo, hi):
    """{(pool, h): (first_hour, min real SOL depth, organic SOL volume, farming share of the last
    hour)} for decision hours h (features over hours h-24 .. h-1), on a full hour grid per pool."""
    rows = con.execute(f"""
        WITH ev AS ({_ev_sql(lo, hi)}),
        agg AS (
          SELECT pool, block_time // {HOUR_S} AS hr, min(sol_depth) AS min_depth,
                 sum(sol_amount) FILTER (WHERE NOT farmer) AS org_vol, sum(sol_amount) FILTER (WHERE farmer) AS farm_vol,
                 sum(sol_amount) AS vol
          FROM ev GROUP BY ALL),
        grid AS (SELECT pool, unnest(range(min(hr), max(hr) + 24)) AS hr, min(hr) AS first_hr FROM agg GROUP BY pool),
        g AS (SELECT grid.pool, grid.hr, grid.first_hr, agg.min_depth, agg.org_vol, agg.farm_vol, agg.vol
              FROM grid LEFT JOIN agg USING (pool, hr))
        SELECT pool, hr + 1 AS h, first_hr,
          min(min_depth) OVER w AS min_depth_24h, sum(COALESCE(org_vol, 0)) OVER w AS org_vol_24h,
          COALESCE(farm_vol, 0) / NULLIF(vol, 0) AS farm_share_1h
        FROM g WINDOW w AS (PARTITION BY pool ORDER BY hr RANGE BETWEEN 23 PRECEDING AND CURRENT ROW)""").fetchall()
    return {(p, h): (first, mind, vol, fs) for p, h, first, mind, vol, fs in rows}


def wallet_counts(con, lo, hi):
    """{pool: (hours, counts)}: distinct organic wallets over the 24 h before each hour, as a step
    function. A wallet's trading hours form islands (gaps <= 24 h); each island covers the decision
    hours [first + 1, last + 24]."""
    rows = con.execute(f"""
        WITH ev AS ({_ev_sql(lo, hi)}),
        tw AS (SELECT DISTINCT pool, trader, block_time // {HOUR_S} AS hr FROM ev WHERE NOT farmer),
        isl AS (SELECT *, SUM(CASE WHEN prev IS NULL OR hr - prev > 24 THEN 1 ELSE 0 END)
                            OVER (PARTITION BY pool, trader ORDER BY hr) AS island
                FROM (SELECT *, LAG(hr) OVER (PARTITION BY pool, trader ORDER BY hr) AS prev FROM tw)),
        iv AS (SELECT pool, min(hr) + 1 AS a, max(hr) + 24 AS b FROM isl GROUP BY pool, trader, island),
        d AS (SELECT pool, a AS h, 1 AS x FROM iv UNION ALL SELECT pool, b + 1, -1 FROM iv)
        SELECT pool, h, sum(sum(x)) OVER (PARTITION BY pool ORDER BY h) FROM d GROUP BY pool, h ORDER BY pool, h""").fetchall()
    out = defaultdict(lambda: ([], []))
    for p, h, n in rows:
        out[p][0].append(h)
        out[p][1].append(n)
    return dict(out)


def _wallets_at(steps, h):
    hs, ns = steps
    i = bisect.bisect_right(hs, h) - 1
    return ns[i] if i >= 0 else 0


def candles(con, lo, hi):
    """{pool: (5-min block indices, closing prices)} for blocks with trades."""
    out = defaultdict(lambda: ([], []))
    for pool, blk, price in con.execute(f"""
            WITH ev AS ({_ev_sql(lo, hi)})
            SELECT pool, block_time // {BLOCK_S} AS blk, arg_max(price, (slot, tx_index, outer_ix, inner_ix))
            FROM ev GROUP BY ALL ORDER BY pool, blk""").fetchall():
        out[pool][0].append(blk)
        out[pool][1].append(price)
    return {p: (np.array(b, dtype=np.int64), np.array(x, dtype=np.float64)) for p, (b, x) in out.items()}


def score(blocks, prices, end_blk, n):
    """(r_L, score) at the start of 5-min block end_blk from forward-filled closes, or None."""
    grid = np.arange(end_blk - n - 1, end_blk)  # n + 1 closes, the last one before end_blk
    idx = np.searchsorted(blocks, grid, side="right") - 1
    if idx[0] < 0:
        return None  # no price at the start of the lookback
    closes = prices[idx]
    if np.any(closes <= 0) or not np.all(np.isfinite(closes)):
        return None
    rets = np.diff(np.log(closes))
    sd = float(np.std(rets, ddof=1))
    if sd <= 0:
        return None
    r = float(np.log(closes[-1] / closes[0]))
    return r, r / (sd * math.sqrt(n))


def s1_positions(con, lo, hi, sizes=S1_SIZES, lookbacks=LOOKBACKS, n2_k=N2_K):
    """[(family, variant, size_sol, pool, t, exit_t, flagged)] for S1 and N2_S1 positions: t is the
    decision slot of the entry hour, exit_t that of the planned exit hour (None: end of data)."""
    _prepare(con, lo, hi)
    hours = con.execute(f"""SELECT block_time // {HOUR_S} + 1 AS h, max(slot) FROM blocks
                            WHERE slot >= {lo} AND slot < {hi} GROUP BY 1 ORDER BY 1""").fetchall()
    feat = hourly_features(con, lo, hi)
    nw = wallet_counts(con, lo, hi)
    cs = candles(con, lo, hi)
    by_hour = defaultdict(list)
    for (p, h), v in feat.items():
        by_hour[h].append((p, v))
    out = []
    for size in sizes:
        qmin = f6_min_depth(size)
        for variant, n in lookbacks.items():
            held, n2 = {}, {}  # S1: pool -> entry; N2: (S1 pool, pool) -> entry
            for h, t in hours:
                uni = []
                for p, (first, mind, vol, fs) in by_hour.get(h, ()):
                    if (h - first >= 24 and mind is not None and mind >= qmin and vol >= qmin and p in cs
                            and p in nw and _wallets_at(nw[p], h) >= MIN_WALLETS):
                        sc = score(*cs[p], h * (HOUR_S // BLOCK_S), n)
                        if sc is not None:
                            uni.append((sc[1], sc[0], p, (fs or 0) > 0.5))
                uni.sort(key=lambda x: (-x[0], x[2]))
                rank = {p: i for i, (_, _, p, _) in enumerate(uni)}
                sco = {p: s for s, _, p, _ in uni}
                flag = {p: f for _, _, p, f in uni}
                for p in list(held):  # hysteresis: keep while in the top 6 with score > 0
                    if rank.get(p, KEEP) >= KEEP or sco.get(p, 0) <= 0:
                        out.append(held.pop(p) + (t,))
                        for k in [k for k in n2 if k[0] == p]:
                            out.append(n2.pop(k) + (t,))
                for s, r, p, f in uni[:TOP]:
                    if len(held) >= TOP:
                        break
                    if p in held or r <= 0 or s < 1:
                        continue
                    held[p] = ("S1", variant, size, p, t, f)
                    others = [x[2] for x in uni if rank[x[2]] >= TOP and x[2] not in held]
                    if n2_k and others:
                        seed = np.uint64(_seed("N2_S1", variant, size, p, t))
                        keys = _mix(np.array([_seed(o) for o in others], dtype=np.uint64) ^ seed)
                        for i in np.argsort(keys, kind="stable")[:n2_k]:
                            n2[(p, others[i])] = ("N2_S1", variant, size, others[i], t, flag[others[i]])
            out += [v + (None,) for v in list(held.values()) + list(n2.values())]
    return [(fam, var, size, pool, t, exit_t, flagged) for fam, var, size, pool, t, flagged, exit_t in out]


class S1Exit(ExitRule):
    """Planned rebalance exit, or an emergency exit on liquidity (< 0.5 Q_min) or mark <= -20%."""

    def __init__(self, exit_slot, q_min, orientation, max_slots):
        self.exit_slot, self.q_min, self.orientation, self.max_slots = exit_slot, q_min, orientation, max_slots

    def deadline(self, entry_slot):
        return self.exit_slot if self.exit_slot is not None else entry_slot + self.max_slots

    def __call__(self, ctx):
        pool = ctx.get("pool")
        if pool is not None and (pool.b if self.orientation == "reversed" else pool.real) < 0.5 * self.q_min:
            return "liquidity"
        if ctx["mark"] <= 0.8 * ctx["cost"]:
            return "stop_loss"
        if self.exit_slot is not None and ctx["slot"] >= self.exit_slot:
            return "rebalance"
        if ctx["slots_held"] >= self.max_slots:
            return "time"
        return None
