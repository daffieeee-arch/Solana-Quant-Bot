"""Exploratory (dev2 is used data; spec E §3, week3-specs-exits-2026-10-15.md sha256 7372ba01...): which features at the
decision slot, and which that appear in the first 30 / 60 s of the hold, separate F7 pool_reversed triggers that are
still alive and higher at 5 / 15 min ("late bloomers") from triggers whose pool is drained before, or that are lower?

Outcomes come from spec B (markout-<run>.parquet: net markout at 0.5 SOL per horizon, d = 1) and research D
(survival.parquet: the LP drain slot). Features are point-in-time: at t only legs with slot <= t (the trigger
transaction is complete at the decision), the post-entry windows only legs in (t + d, t + d + X]. Per feature the
AUC of late_5 vs the rest, the late share in the top vs the bottom quintile (per category when categorical), and the
mean net at 5 min per bin, each with a day bootstrap. Descriptive: no model, no feature selection, no threshold.

  LAB_STORE=<dev2> PYTHONPATH=lab python -m wallets.latebloom --parts <f7rev parts> --markout <markout-f7rev.parquet> \\
      --survival <survival.parquet> --out DIR [--from-slot A --to-slot B] [--lookback-slots N] [--boot 2000]

Memory: legs per key-hash bucket of the pools (LAB_LATEBLOOM_BUCKETS, default 8); the store connection takes
LAB_WALLETS_MEMORY. Output: latebloom.parquet (one row per trigger: outcomes + features) and latebloom.md.
"""

import argparse
import glob
import json
import math
import os
import sys
import time

import numpy as np

from backtest import wash  # the repo's one rt_tx / wash_1h definition (read only; lab/backtest is not ours to change)
from .common import OUT_ROOT, SOL, connect, git_commit, guard
from .money_report import day_boot

FAMILY, SEGMENT, D = "F7", "pool_reversed", 1
HOUR_SLOTS = wash.HOUR_SLOTS          # the pre-trigger activity window (~1 h), as the wash share
POST_SECONDS = (30, 60)               # the post-entry windows
H_OUT = ("1m", "5m", "15m")           # horizons of the outcome
BINDING_START, BINDING_END = "2026-09-20", "2026-09-30"
NET_CAP = 10.0                        # net capped at +1000 % for the means (as spec B's gross cap)
QUANTILES = 5
CATEGORICAL_MAX = 6                   # <= this many distinct values: per-category table instead of quintiles

# (name, sql expression on the joined table, description)
FEATURES_PRE = [
    ("age_min", "age_min", "pool age at t (min of the epoch's slot time)"),
    ("depth_sol", "depth_sol", "WSOL depth at the decision (parts)"),
    ("init_depth_sol", "init_depth_sol", "WSOL reserve at the pool's creation (pools.b0; the opening liquidity is not a deposit leg)"),
    ("depth_ratio", "depth_ratio", "depth at the decision / opening reserve"),
    ("trades_1h", "trades_1h", "trades in the hour before t (incl. the trigger slot)"),
    ("buyers_1h", "buyers_1h", "distinct token buyers in the hour before t"),
    ("vol_1h_sol", "vol_1h_sol", "SOL volume in the hour before t"),
    ("farmer_share_1h", "farmer_share_1h", "share of farmer legs in the hour before t"),
    ("deposits_pre", "deposits_pre", "LP deposits up to t"),
    ("last_deposit_min", "last_deposit_min", "minutes since the last LP deposit (NULL: none seen)"),
    ("withdraws_pre", "withdraws_pre", "LP withdraws up to t"),
    ("deposit_by_creator", "deposit_by_creator", "an LP deposit by the coin creator"),
    ("deposit_by_trigger", "deposit_by_trigger", "an LP deposit by the trigger wallet"),
    ("rt_tx", "rt_tx", "trigger sell in a same-transaction round trip (a4c)"),
    ("wash_1h", "wash_1h", "wash share of the hour before t >= 0.5"),
    ("wash_share_1h", "wash_share_1h", "wash share of the SOL volume in the hour before t"),
    ("farm_flagged", "farm_flagged", "farm flag of the parts (flagged vs organic)"),
    ("trigger_farmer", "trigger_farmer", "the trigger wallet is a farmer"),
    ("impact_pct", "impact_pct", "own entry impact of 0.5 SOL (spec B)"),
    ("regime_320", "regime_320", "slot-time regime 320 ms (vs 270 ms)"),
]
FEATURES_POST = [(f"{n}_{x}", f"{n}_{x}", f"{desc} in the first {x} s after the entry") for x in POST_SECONDS for n, desc in [
    ("trades", "trades"), ("new_buyers", "new distinct token buyers (never bought in the pool before)"),
    ("inflow_sol", "net SOL inflow (token buys − sells)"), ("inflow_org_sol", "net organic SOL inflow (farmer legs out)"),
    ("drawup_pct", "max of the mid vs the entry mid"), ("drawdown_pct", "min of the mid vs the entry mid"),
    ("withdraw", "an LP withdraw seen")]]
FEATURES = FEATURES_PRE + FEATURES_POST


def progress(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", file=sys.stderr, flush=True)


def _lit(files):
    return "[" + ",".join(f"'{f}'" for f in files) + "]"


def load_triggers(con, parts_dir, lo, hi):
    files = sorted(glob.glob(os.path.join(parts_dir, "*.parquet")))
    if not files:
        raise SystemExit(f"no parquet parts in {parts_dir}")
    where = [f"family = '{FAMILY}'", f"segment = '{SEGMENT}'", "venue = 'pool'", "pool IS NOT NULL", "t IS NOT NULL", f"d = {D}"]
    if lo is not None:
        where.append(f"t >= {lo}")
    if hi is not None:
        where.append(f"t < {hi}")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE trig AS
      SELECT pool, t::BIGINT AS t, any_value(mint) AS mint, any_value(day) AS day, any_value(farm) AS farm, any_value(regime) AS regime,
             any_value(trigger_trader) AS trigger_trader, bool_or(trigger_farmer) AS trigger_farmer, max(depth)::DOUBLE / {SOL} AS depth_sol
      FROM read_parquet({_lit(files)}, union_by_name = true) WHERE {' AND '.join(where)} GROUP BY pool, t""")
    n = con.execute("SELECT count(*) FROM trig").fetchone()[0]
    progress(f"triggers: {n} from {len(files)} parts")
    return n


def load_outcomes(con, markout_parquet, survival_parquet):
    """Per trigger: entry mid and target (h = ''), net / cut / target per outcome horizon (d = 1), the drain slot."""
    hs = ", ".join(f"'{h}'" for h in H_OUT)
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE mk AS
      SELECT pool, t, h, target, mid, net, cut, impact, ms_per_slot
      FROM read_parquet('{markout_parquet}') m
      WHERE family = '{FAMILY}' AND segment = '{SEGMENT}' AND d = {D} AND h IN ('', {hs}) AND (pool, t) IN (SELECT (pool, t) FROM trig)""")
    cols = ", ".join(f"max(net) FILTER (h = '{h}') AS net_{h}, max(target) FILTER (h = '{h}') AS target_{h}, bool_or(cut) FILTER (h = '{h}') AS cut_{h}" for h in H_OUT)
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE outc AS
      SELECT m.pool, m.t, any_value(ms_per_slot) AS ms_per_slot, max(target) FILTER (h = '') AS entry_slot, max(mid) FILTER (h = '') AS mid_entry,
             max(impact) FILTER (h = '') AS impact, {cols}
      FROM mk m GROUP BY 1, 2""")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE sv AS
      SELECT pool, t, drain_slot, event, first_withdraw_slot, last_trade_slot
      FROM read_parquet('{survival_parquet}') WHERE (pool, t) IN (SELECT (pool, t) FROM trig)""")
    n_mk, n_sv = con.execute("SELECT (SELECT count(*) FROM outc), (SELECT count(*) FROM sv)").fetchone()
    progress(f"outcomes: markout rows for {n_mk} triggers, survival rows for {n_sv}")
    return n_mk, n_sv


def build_features(con, lookback_slots, buckets, log=progress):
    """Point-in-time features from the store's pool legs, one hash bucket of pools at a time."""
    catalog = con.execute("SELECT current_database()").fetchone()[0]
    wash.flags(con, "SELECT pool, t, trigger_trader FROM trig", store=catalog, buckets=buckets, log=log)
    con.execute("""CREATE OR REPLACE TEMP TABLE feat_pre (pool VARCHAR, t BIGINT, trades_1h BIGINT, buyers_1h BIGINT, vol_1h_sol DOUBLE,
                   farmer_share_1h DOUBLE, deposits_pre BIGINT, last_deposit_slot BIGINT, withdraws_pre BIGINT,
                   deposit_by_creator BOOLEAN, deposit_by_trigger BOOLEAN)""")
    post_cols = ", ".join(f"trades_{x} BIGINT, new_buyers_{x} BIGINT, inflow_sol_{x} DOUBLE, inflow_org_sol_{x} DOUBLE, "
                          f"drawup_pct_{x} DOUBLE, drawdown_pct_{x} DOUBLE, withdraw_{x} BOOLEAN" for x in POST_SECONDS)
    con.execute(f"CREATE OR REPLACE TEMP TABLE feat_post (pool VARCHAR, t BIGINT, {post_cols})")
    lo, hi = con.execute("SELECT min(t), max(t) FROM trig").fetchone()
    start = 0 if lookback_slots is None else lo - lookback_slots
    post_max = max(POST_SECONDS) * 1000
    for b in range(buckets):
        con.execute(f"CREATE OR REPLACE TEMP TABLE trb AS SELECT tr.*, o.entry_slot, o.mid_entry, o.ms_per_slot FROM trig tr JOIN outc o USING (pool, t) WHERE hash(tr.pool) % {buckets} = {b}")
        blo, bhi = con.execute("SELECT min(t), max(t) FROM trb").fetchone()
        if blo is None:
            continue
        # every leg of the bucket's pools from the lookback to the end of the longest post window (ms at the slowest slot)
        con.execute(f"""
          CREATE OR REPLACE TEMP TABLE lg AS
          SELECT pool, slot, tx_index, kind, trader, token_buy, sol_amount::DOUBLE / {SOL} AS sol, b, e, farmer
          FROM pool WHERE pool IN (SELECT DISTINCT pool FROM trb) AND slot >= {start} AND slot <= {bhi} + {int(math.ceil(post_max / 200.0))} + 2""")
        con.execute("""
          INSERT INTO feat_pre
          SELECT tr.pool, tr.t,
                 count(*) FILTER (l.kind IN ('buy', 'sell') AND l.slot > tr.t - {H}),
                 count(DISTINCT l.trader) FILTER (l.kind IN ('buy', 'sell') AND l.token_buy AND l.slot > tr.t - {H}),
                 coalesce(sum(l.sol) FILTER (l.kind IN ('buy', 'sell') AND l.slot > tr.t - {H}), 0),
                 avg(l.farmer::INT) FILTER (l.kind IN ('buy', 'sell') AND l.slot > tr.t - {H}),
                 count(*) FILTER (l.kind = 'deposit'), max(l.slot) FILTER (l.kind = 'deposit'), count(*) FILTER (l.kind = 'withdraw'),
                 coalesce(bool_or(l.kind = 'deposit' AND l.trader = p.coin_creator), false),
                 coalesce(bool_or(l.kind = 'deposit' AND l.trader = tr.trigger_trader), false)
          FROM trb tr LEFT JOIN lg l ON l.pool = tr.pool AND l.slot <= tr.t LEFT JOIN pools p ON p.pool = tr.pool
          GROUP BY 1, 2""".replace("{H}", str(HOUR_SLOTS)))
        # first buy per (pool, trader): a buyer in the post window is new when its first buy is after t
        con.execute("CREATE OR REPLACE TEMP TABLE fb AS SELECT pool, trader, min(slot) AS first_buy FROM lg WHERE kind IN ('buy', 'sell') AND token_buy GROUP BY 1, 2")
        sel = []
        for x in POST_SECONDS:
            w = f"(l.kind IN ('buy', 'sell') AND l.slot > tr.entry_slot AND l.slot <= tr.entry_slot + ceil({x * 1000} / tr.ms_per_slot))"
            any_w = f"(l.slot > tr.entry_slot AND l.slot <= tr.entry_slot + ceil({x * 1000} / tr.ms_per_slot))"
            sel.append(f"""count(*) FILTER ({w}),
                 count(DISTINCT l.trader) FILTER ({w} AND l.token_buy AND fb.first_buy > tr.t),
                 coalesce(sum(CASE WHEN l.token_buy THEN l.sol ELSE -l.sol END) FILTER ({w}), 0),
                 coalesce(sum(CASE WHEN l.token_buy THEN l.sol ELSE -l.sol END) FILTER ({w} AND NOT l.farmer), 0),
                 100 * (max(l.b / nullif(l.e, 0)) FILTER ({w}) / nullif(tr.mid_entry, 0) - 1),
                 100 * (min(l.b / nullif(l.e, 0)) FILTER ({w}) / nullif(tr.mid_entry, 0) - 1),
                 coalesce(bool_or(l.kind = 'withdraw') FILTER ({any_w}), false)""")
        con.execute(f"""
          INSERT INTO feat_post
          SELECT tr.pool, tr.t, {', '.join(sel)}
          FROM trb tr LEFT JOIN lg l ON l.pool = tr.pool AND l.slot > tr.entry_slot AND l.slot <= tr.entry_slot + ceil({post_max} / tr.ms_per_slot)
          LEFT JOIN fb ON fb.pool = l.pool AND fb.trader = l.trader
          GROUP BY tr.pool, tr.t, tr.mid_entry""")
        log(f"features: bucket {b + 1}/{buckets} ({con.execute('SELECT count(*) FROM lg').fetchone()[0]:,} legs)")
    con.execute("DROP TABLE IF EXISTS lg; DROP TABLE IF EXISTS fb; DROP TABLE IF EXISTS trb")


def assemble(con):
    """One row per trigger: outcomes, classes and features; the slot-time features in minutes of the epoch."""
    h5, h15 = con.execute("SELECT max(target_5m), max(target_15m) FROM outc").fetchone()
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE lb AS
      SELECT tr.pool, tr.t, tr.mint, tr.day, tr.farm, tr.regime, tr.trigger_farmer,
             CASE WHEN tr.day < '{BINDING_START}' THEN 'days 1-9' WHEN tr.day <= '{BINDING_END}' THEN 'days 10-18' ELSE 'later' END AS block,
             o.ms_per_slot, o.entry_slot, o.mid_entry, 100 * o.impact AS impact_pct,
             o.net_1m, o.net_5m, o.net_15m, o.cut_5m, o.cut_15m, o.target_5m, o.target_15m,
             s.drain_slot, s.event AS drained, s.first_withdraw_slot, s.last_trade_slot,
             (s.drain_slot IS NOT NULL AND s.drain_slot <= o.target_5m) AS drained_5,
             (s.drain_slot IS NOT NULL AND s.drain_slot <= o.target_15m) AS drained_15,
             (s.drain_slot IS NULL OR s.drain_slot > o.target_5m) AND o.net_5m > 0 AS late_5,
             (s.drain_slot IS NULL OR s.drain_slot > o.target_15m) AND o.net_15m > 0 AS late_15,
             (tr.t - p.create_slot) * o.ms_per_slot / 60000.0 AS age_min,
             tr.depth_sol, p.b0 / {SOL} AS init_depth_sol, tr.depth_sol / nullif(p.b0 / {SOL}, 0) AS depth_ratio, f.trades_1h, f.buyers_1h, f.vol_1h_sol, f.farmer_share_1h, f.deposits_pre,
             (tr.t - f.last_deposit_slot) * o.ms_per_slot / 60000.0 AS last_deposit_min, f.withdraws_pre,
             f.deposit_by_creator, f.deposit_by_trigger, r.rt_tx,
             -- wash.flags sums the round-trip volume with a FILTER: NULL (not 0) when the hour had no round trip at all
             CASE WHEN w.pool IS NOT NULL THEN coalesce(w.wash_share, 0) END >= {wash.WASH_SHARE} AS wash_1h,
             CASE WHEN w.pool IS NOT NULL THEN coalesce(w.wash_share, 0) END AS wash_share_1h,
             tr.farm = 'flagged' AS farm_flagged, tr.regime = '320ms' AS regime_320,
             fp.* EXCLUDE (pool, t)
      FROM trig tr JOIN outc o USING (pool, t) LEFT JOIN sv s USING (pool, t) LEFT JOIN pools p ON p.pool = tr.pool
      LEFT JOIN feat_pre f USING (pool, t) LEFT JOIN feat_post fp USING (pool, t)
      LEFT JOIN f_rt r USING (pool, t) LEFT JOIN f_w w USING (pool, t)""")
    return con.execute("SELECT count(*) FROM lb").fetchone()[0]


# ----------------------------------------------------------------------------- statistics

def auc_w(x, y, w):
    """Weighted AUC (Mann-Whitney with ties at 1/2) of y (bool) against x, weights w (day resampling counts)."""
    order = np.argsort(x, kind="mergesort")
    xs, ys, ws = x[order], y[order], w[order]
    wn, wp = ws * (~ys), ws * ys
    if wp.sum() == 0 or wn.sum() == 0:
        return None
    _, start = np.unique(xs, return_index=True)
    sizes = np.diff(np.append(start, len(xs)))
    grp = np.repeat(np.arange(len(start)), sizes)
    n_in = np.bincount(grp, weights=wn)
    n_below = np.cumsum(n_in) - n_in
    return float((wp * (n_below[grp] + 0.5 * n_in[grp])).sum() / (wp.sum() * wn.sum()))


def day_weights(day_idx, n_days, boot, seed=7):
    """boot × n rows of day resampling counts."""
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, n_days, size=(boot, n_days))
    counts = np.stack([np.bincount(d, minlength=n_days) for d in draws])
    return counts[:, day_idx]


def auc_ci(x, y, day_idx, n_days, boot):
    a = auc_w(x, y, np.ones(len(x)))
    if a is None or n_days < 2:
        return a, None, None
    W = day_weights(day_idx, n_days, boot)
    vals = [v for v in (auc_w(x, y, W[i].astype(float)) for i in range(boot)) if v is not None]
    return a, float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def per_day(day_idx, n_days, *arrays):
    return [np.bincount(day_idx, weights=a, minlength=n_days) for a in arrays]


def ratio_ci(num, den, day_idx, n_days, boot):
    s, c = per_day(day_idx, n_days, num, den)
    lo, hi, _ = day_boot(s, c, boot)
    return (float(num.sum() / den.sum()) if den.sum() else None), lo, hi


def diff_ci(num_a, den_a, idx_a, num_b, den_b, idx_b, n_days, boot, seed=7):
    """Day bootstrap of mean_a − mean_b (both ratios of day sums, days drawn together)."""
    sa, ca = per_day(idx_a, n_days, num_a, den_a)
    sb, cb = per_day(idx_b, n_days, num_b, den_b)
    if n_days < 2 or ca.sum() == 0 or cb.sum() == 0:
        return None, None, None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n_days, size=(boot, n_days))
    d = sa[idx].sum(1) / np.maximum(ca[idx].sum(1), 1) - sb[idx].sum(1) / np.maximum(cb[idx].sum(1), 1)
    return float(sa.sum() / ca.sum() - sb.sum() / cb.sum()), float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


def fmt(x, nd=1, pct=False, sign=False):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    v = 100 * x if pct else x
    s = f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}"
    return s + (" %" if pct else "")


def ci(lo, hi, nd=1, pct=False, sign=False):
    return "–" if lo is None else f"[{fmt(lo, nd, pct, sign)} … {fmt(hi, nd, pct, sign)}]"


def analyse(con, block_where, boot):
    """Rows of the per-feature table for one block: AUC, quintile / category shares and net at 5 min."""
    rows = con.execute(f"SELECT day, late_5::INT, least(net_5m, {NET_CAP}) FROM lb WHERE {block_where} AND net_5m IS NOT NULL").fetchall()
    if not rows:
        return []
    days = sorted({r[0] for r in rows})
    day_of = {d: i for i, d in enumerate(days)}
    day_idx = np.array([day_of[r[0]] for r in rows])
    late = np.array([r[1] for r in rows], dtype=bool)
    net = np.array([r[2] for r in rows], dtype=float)
    ones = np.ones(len(rows))
    out = []
    for name, expr, desc in FEATURES:
        vals = con.execute(f"SELECT {expr} FROM lb WHERE {block_where} AND net_5m IS NOT NULL").fetchall()
        x = np.array([np.nan if v[0] is None else float(v[0]) for v in vals])
        ok = ~np.isnan(x)
        if ok.sum() < 20 or len(np.unique(x[ok])) < 2:
            out.append({"feature": name, "desc": desc, "n": int(ok.sum()), "auc": None, "bins": []})
            continue
        a, alo, ahi = auc_ci(x[ok], late[ok], day_idx[ok], len(days), boot)
        uniq = np.unique(x[ok])
        bins = []
        if len(uniq) <= CATEGORICAL_MAX:
            labels = [(f"= {u:g}" if u not in (0.0, 1.0) or len(uniq) > 2 else ("yes" if u == 1.0 else "no"), x == u) for u in uniq]
        else:
            # quintile edges; ties (many zeros) collapse edges, so the bins are numbered after merging duplicates
            q = np.unique(np.nanpercentile(x[ok], np.linspace(0, 100, QUANTILES + 1)))
            labels = []
            for i in range(len(q) - 1):
                lo_q, hi_q = q[i], q[i + 1]
                m = ok & (x >= lo_q) & ((x < hi_q) if i < len(q) - 2 else (x <= hi_q))
                labels.append((f"Q{i + 1}/{len(q) - 1} [{lo_q:g} … {hi_q:g}]", m))
        for lab, m in labels:
            if m.sum() == 0:
                continue
            sh, slo, shi = ratio_ci(late[m].astype(float), ones[m], day_idx[m], len(days), boot)
            mn, mlo, mhi = ratio_ci(net[m], ones[m], day_idx[m], len(days), boot)
            bins.append({"label": lab, "n": int(m.sum()), "late": sh, "late_ci": (slo, shi), "net": mn, "net_ci": (mlo, mhi), "mask": m})
        # top vs bottom bin, paired by day
        if len(bins) >= 2:
            top, bot = bins[-1]["mask"], bins[0]["mask"]
            dl, dllo, dlhi = diff_ci(late[top].astype(float), ones[top], day_idx[top], late[bot].astype(float), ones[bot], day_idx[bot], len(days), boot)
            dn, dnlo, dnhi = diff_ci(net[top], ones[top], day_idx[top], net[bot], ones[bot], day_idx[bot], len(days), boot)
        else:
            dl = dllo = dlhi = dn = dnlo = dnhi = None
        for b_ in bins:
            del b_["mask"]
        out.append({"feature": name, "desc": desc, "n": int(ok.sum()), "auc": a, "auc_ci": (alo, ahi), "bins": bins,
                    "d_late": dl, "d_late_ci": (dllo, dlhi), "d_net": dn, "d_net_ci": (dnlo, dnhi)})
    return out


def render(con, info, results):
    tot = con.execute("SELECT count(*), count(net_5m), count(drain_slot) FROM lb").fetchone()
    L = [f"## Late bloomers vs drains (exploratory, spec E §3): {FAMILY} {SEGMENT}, dev2, d = {D}, 0.5 SOL\n",
         f"Triggers {tot[0]:,} (distinct pool, t), with a 5-min markout {tot[1]:,}, with a drain observed {tot[2]:,}. "
         "Outcome at h ∈ {5 min, 15 min}: **late** = the pool is not drained by t + d + h and the net markout at h is > 0; "
         "**drained** = the LP drain (≥ 90 % of the WSOL, research D) is at or before t + d + h; **lower** = the rest. "
         "Features are point-in-time: at t only legs with slot ≤ t; the post-entry windows only legs in (t + d, t + d + X]. "
         f"Per feature: AUC of late_5 against the rest (0.5 = no separation), late share and mean net at 5 min (capped at +{NET_CAP:.0%}) "
         "per quintile (per category when ≤ 6 values), top − bottom with a day bootstrap "
         f"({info['boot']:,}×; days drawn together). Descriptive on used data: no model, no selection, no threshold.\n"]
    L.append("### Outcome shares per block\n")
    L.append("| block | n | late 5 min | drained by 5 min | lower at 5 min | late 15 min | drained by 15 min | lower at 15 min | net 5 min (mean, capped) | net 5 min (median) |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for blk in ("days 1-9", "days 10-18", "all"):
        w = "TRUE" if blk == "all" else f"block = '{blk}'"
        r = con.execute(f"""SELECT count(*), avg(late_5::INT), avg(drained_5::INT), avg((NOT late_5 AND NOT drained_5)::INT),
                            avg(late_15::INT), avg(drained_15::INT), avg((NOT late_15 AND NOT drained_15)::INT),
                            avg(least(net_5m, {NET_CAP})), median(net_5m) FROM lb WHERE {w} AND net_5m IS NOT NULL""").fetchone()
        if r[0]:
            L.append(f"| {blk} | {r[0]:,} | {fmt(r[1], 1, True)} | {fmt(r[2], 1, True)} | {fmt(r[3], 1, True)} | {fmt(r[4], 1, True)} | {fmt(r[5], 1, True)} | {fmt(r[6], 1, True)} | {fmt(r[7], 1, True, True)} | {fmt(r[8], 1, True, True)} |")
    for blk, res in results.items():
        L.append(f"\n### Features, {blk}\n")
        L.append("| feature | n | AUC late_5 | 95 % CI (days) | bottom bin: late / net 5 min | top bin: late / net 5 min | Δ late (top − bottom) | 95 % CI | Δ net 5 min | 95 % CI |")
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for r in res:
            if not r["bins"]:
                L.append(f"| {r['feature']} | {r['n']:,} | – | – | – | – | – | – | – | – |")
                continue
            b0, b1 = r["bins"][0], r["bins"][-1]
            L.append(f"| {r['feature']} | {r['n']:,} | {fmt(r['auc'], 3)} | {ci(*r['auc_ci'], nd=3)} | "
                     f"{b0['label']}: {fmt(b0['late'], 1, True)} / {fmt(b0['net'], 1, True, True)} | {b1['label']}: {fmt(b1['late'], 1, True)} / {fmt(b1['net'], 1, True, True)} | "
                     f"{fmt(r['d_late'], 1, True, True)} | {ci(*r['d_late_ci'], nd=1, pct=True, sign=True)} | {fmt(r['d_net'], 1, True, True)} | {ci(*r['d_net_ci'], nd=1, pct=True, sign=True)} |")
        L.append(f"\n<details><summary>All bins, {blk}</summary>\n")
        L.append("| feature | bin | n | late 5 min | 95 % CI | net 5 min (mean) | 95 % CI |")
        L.append("|---|---|---|---|---|---|---|")
        for r in res:
            for b_ in r["bins"]:
                L.append(f"| {r['feature']} | {b_['label']} | {b_['n']:,} | {fmt(b_['late'], 1, True)} | {ci(*b_['late_ci'], nd=1, pct=True)} | {fmt(b_['net'], 1, True, True)} | {ci(*b_['net_ci'], nd=1, pct=True, sign=True)} |")
        L.append("\n</details>")
    L.append("\n### Feature definitions\n")
    L += [f"- `{n}`: {d}" for n, _, d in FEATURES]
    L.append(f"\nRun: commit {info['commit']}, {info['seconds']} s, buckets {info['buckets']}, lookback {info['lookback_slots']}.")
    return "\n".join(L)


def run(parts_dir, markout_parquet, survival_parquet, out_dir, lo=None, hi=None, lookback_slots=None, boot=2000, store=None):
    t0 = time.time()
    mem = os.environ.get("LAB_WALLETS_MEMORY", "1GB")
    buckets = int(os.environ.get("LAB_LATEBLOOM_BUCKETS", "8"))
    con = connect(store, memory=mem) if store else connect(memory=mem)
    end_slot = int(json.loads(con.execute("SELECT json FROM meta").fetchone()[0])["slot_end_exclusive"])
    guard(hi if hi is not None else end_slot)
    if load_triggers(con, parts_dir, lo, hi) == 0:
        raise SystemExit("no triggers")
    load_outcomes(con, markout_parquet, survival_parquet)
    build_features(con, lookback_slots, buckets)
    n = assemble(con)
    progress(f"assembled {n} rows; statistics (boot {boot})")
    results = {}
    for blk in ("days 10-18", "days 1-9"):
        res = analyse(con, f"block = '{blk}'", boot)
        if res:
            results[blk] = res
        progress(f"analysed {blk}")
    os.makedirs(out_dir, exist_ok=True)
    con.execute(f"COPY (SELECT * FROM lb ORDER BY pool, t) TO '{os.path.join(out_dir, 'latebloom.parquet')}' (FORMAT PARQUET)")
    info = {"parts": parts_dir, "markout": markout_parquet, "survival": survival_parquet, "family": FAMILY, "segment": SEGMENT, "d": D,
            "lo": lo, "hi": hi, "triggers": n, "lookback_slots": lookback_slots, "buckets": buckets, "boot": boot, "post_seconds": list(POST_SECONDS),
            "commit": git_commit(), "seconds": round(time.time() - t0, 1)}
    with open(os.path.join(out_dir, "latebloom.md"), "w") as f:
        f.write(render(con, info, results) + "\n")
    with open(os.path.join(out_dir, "run-latebloom.json"), "w") as f:
        json.dump(info, f, indent=1, default=str)
    progress(f"written {out_dir} ({info['seconds']} s)")
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parts", required=True, help="the f7rev parts (triggers, farm, trigger wallet, depth)")
    ap.add_argument("--markout", required=True, help="markout-f7rev.parquet (spec B)")
    ap.add_argument("--survival", required=True, help="survival.parquet (research D)")
    ap.add_argument("--out", default=os.path.join(OUT_ROOT, "latebloom-v1"))
    ap.add_argument("--from-slot", type=int)
    ap.add_argument("--to-slot", type=int)
    ap.add_argument("--lookback-slots", type=int, help="legs before the first trigger to load (default: from the store start)")
    ap.add_argument("--boot", type=int, default=2000)
    a = ap.parse_args()
    info = run(a.parts, a.markout, a.survival, a.out, a.from_slot, a.to_slot, a.lookback_slots, a.boot)
    print(json.dumps(info, default=str), file=sys.stderr)


if __name__ == "__main__":
    main()
