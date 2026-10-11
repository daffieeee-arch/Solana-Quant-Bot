"""Walvis stap 1c: the point-in-time wallet score for one day (#167).

On day D (UTC) the score of wallet w uses only what was known before D: its episodes (episodes.py)
and the replayed follower positions (follow.py) with trigger t0 in [D - WINDOW_DAYS, D) that were
closed before D (exit_slot < first slot of D). A position still open at D is unknown and left out.

The score is the follower's expected net return per position at base costs (venue fees, network,
rent, failed transactions: backtest.costs.settle_sql), per variant, size and delay:

  shrunk  empirical-Bayes shrinkage of the wallet mean towards the mean over all scored wallets,
          weight n / (n + k), k = within-wallet variance / between-wallet variance (method of
          moments over wallets with >= MIN_N positions); wallets below MIN_N are shrunk too but get
          no rank, p or q;
  p, q    one-sided test of mean > 0 (t statistic, normal approximation) and Benjamini-Hochberg
          q-values over the scored wallets of that variant/size/delay; `top` = q < FDR_Q.

Labels (probabilities, addresses only; from the same window): creator (created a token), sniper
(>= half of the episodes opened in a create slot), hf_bot (>= HF_PER_DAY episodes per day and a
median leader hold <= HF_HOLD_S), farmer (>= half of the wallet's pool trades flagged farmer),
one_shot (one episode), bundler (>= BUNDLER_MIN memberships of cluster events, clusters.py) and
split_buyer (>= 2 `tx` cluster buys in normal pools: the structure that held in step 0).

  python -m wallets.score --day 2026-09-27 --episodes DIR --follow DIR [--clusters DIR] [--out DIR]

Writes scores-<D>.parquet (long: wallet x variant x size x d) and leaderboard-<D>.parquet (one
row per wallet: labels, leader statistics, follower return at 0.5 SOL for d = 1 and d = 2).
"""

import argparse
import datetime as dt
import glob
import json
import math
import os

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from backtest.costs import Scenario, settle_sql

from .common import OUT_ROOT, Clock, connect, git_commit

DEFAULTS = os.environ.get("LAB_DEFAULTS", "/home/chupa/Solana-project/data-old-faithful-one/lab/research/week2-defaults.yaml")
WINDOW_DAYS = 7
MIN_N = 5
FDR_Q = 0.10
HF_PER_DAY, HF_HOLD_S = 100, 60
BUNDLER_MIN = 3
LEADER_VARIANT, LEADER_SIZE = "W_half", 0.5


def day_bounds(con, day):
    """(first slot of day, first slot of the window start) from the blocks table."""
    d0 = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    t_day, t_win = int(d0.timestamp()), int((d0 - dt.timedelta(days=WINDOW_DAYS)).timestamp())
    r = con.execute("SELECT min(slot) FILTER (WHERE block_time >= ?), min(slot) FILTER (WHERE block_time >= ?), "
                    "min(slot) FROM blocks", [t_day, t_win]).fetchone()
    if r[0] is None:
        raise SystemExit(f"day {day} is not in the store")
    return r[0], (r[1] if r[1] is not None else r[2])


def bh(p):
    """Benjamini-Hochberg q-values for an array of p-values (NaN stays NaN)."""
    q = np.full(len(p), np.nan)
    ok = np.where(~np.isnan(p))[0]
    if not len(ok):
        return q
    order = ok[np.argsort(p[ok])]
    m = len(order)
    adj = p[order] * m / np.arange(1, m + 1)
    adj = np.minimum.accumulate(adj[::-1])[::-1]
    q[order] = np.minimum(adj, 1.0)
    return q


def shrink(means, ns, sds):
    """Empirical-Bayes shrinkage of wallet means: (shrunk, prior, k)."""
    big = ns >= MIN_N
    if big.sum() < 3:
        prior = float(np.average(means, weights=ns)) if ns.sum() else 0.0
        return np.full(len(means), prior), prior, math.inf
    prior = float(np.average(means[big], weights=ns[big]))
    within = float(np.nanmean(sds[big] ** 2))
    between = max(float(np.var(means[big]) - within / float(np.mean(ns[big]))), 1e-12)
    k = within / between
    w = ns / (ns + k)
    return prior + w * (means - prior), prior, k


def score_positions(con, follow_dir, lo, hi, d_slot, cfg):
    """Per wallet x variant x size x d over closed positions with t0 in [lo, hi)."""
    pnl, ret = settle_sql(Scenario.from_cfg("base", cfg))
    files = sorted(glob.glob(os.path.join(follow_dir, "follow-*.parquet")))
    if not files:
        return []
    lit = "[" + ",".join(f"'{f}'" for f in files) + "]"
    rows = con.execute(f"""
      WITH p AS (SELECT *, {pnl} AS pnl, {ret} AS ret FROM read_parquet({lit}, union_by_name = true)
                 WHERE t0 >= {lo} AND t0 < {hi} AND NOT skipped AND COALESCE(exit_slot, {d_slot}) < {d_slot})
      SELECT wallet, variant, size_sol, d, any_value(control) AS control, count(*) AS n, count(ret) AS filled,
             avg(ret) AS mean_ret, stddev_samp(ret) AS sd_ret, avg(pnl) / 1e9 AS mean_pnl_sol, avg((ret > 0)::INT) AS win,
             median(exit_slot - entry_slot) AS median_hold_slots,
             avg((exit_reason = 'leader_sold')::INT) AS leader_exit_share, avg(entry_failed::INT) AS failed_share
      FROM p GROUP BY ALL""").fetchall()
    cols = ["wallet", "variant", "size_sol", "d", "control", "n", "filled", "mean_ret", "sd_ret", "mean_pnl_sol", "win",
            "median_hold_slots", "leader_exit_share", "failed_share"]
    out = [dict(zip(cols, r)) for r in rows]
    groups = {}
    for r in out:
        groups.setdefault((r["variant"], r["size_sol"], r["d"]), []).append(r)
    for g in groups.values():
        means = np.array([r["mean_ret"] if r["mean_ret"] is not None else 0.0 for r in g])
        ns = np.array([r["filled"] for r in g], dtype=float)
        sds = np.array([r["sd_ret"] if r["sd_ret"] is not None else np.nan for r in g])
        shr, prior, k = shrink(means, ns, sds)
        with np.errstate(invalid="ignore", divide="ignore"):
            t = means / (sds / np.sqrt(ns))
        p = np.array([0.5 * math.erfc(x / math.sqrt(2)) if (n >= MIN_N and np.isfinite(x)) else np.nan
                      for x, n in zip(t, ns)])
        q = bh(p)
        rank = [None] * len(g)  # only wallets with >= MIN_N filled positions are ranked
        for i, j in enumerate([j for j in np.argsort(-shr) if ns[j] >= MIN_N]):
            rank[j] = i + 1
        for r, s, pv, qv, rk in zip(g, shr, p, q, rank):
            r.update(shrunk_ret=float(s), prior=prior, k=float(k), p_value=None if np.isnan(pv) else float(pv),
                     q_value=None if np.isnan(qv) else float(qv), top=bool(qv < FDR_Q) if not np.isnan(qv) else False,
                     rank=rk)
    return out


def leader_stats(con, ep_dir, lo, hi, d_slot, clock):
    files = sorted(glob.glob(os.path.join(ep_dir, "episodes-*.parquet")))
    lit = "[" + ",".join(f"'{f}'" for f in files) + "]"
    ms = clock.ms_per_slot(lo)
    rows = con.execute(f"""
      SELECT wallet, count(*) AS n, avg(leader_ret_marked) AS mean_ret_marked, avg(leader_ret_realized) AS mean_ret_realized,
             median(hold_slots) * {ms} / 1000 AS median_hold_s, avg(create_slot::INT) AS create_slot_share,
             avg(closed::INT) AS closed_share, count(DISTINCT day) AS days, sum(cost_sol) / 1e9 AS sol_in,
             avg((venue = 'curve')::INT) AS curve_share, avg((orientation = 'reversed')::INT) AS reversed_share
      FROM read_parquet({lit}, union_by_name = true)
      WHERE t0 >= {lo} AND t0 < {hi} AND end_slot < {d_slot} GROUP BY wallet""").fetchall()
    cols = ["wallet", "n", "mean_ret_marked", "mean_ret_realized", "median_hold_s", "create_slot_share", "closed_share",
            "days", "sol_in", "curve_share", "reversed_share"]
    return {r[0]: dict(zip(cols, r)) for r in rows}


def store_labels(con, wallets, lo, hi):
    """creator and farmer inputs from the store, for the given wallets, over [lo, hi)."""
    con.execute("CREATE OR REPLACE TEMP TABLE ws (wallet VARCHAR)")
    con.executemany("INSERT INTO ws VALUES (?)", [(w,) for w in wallets])
    creators = {r[0] for r in con.execute(
        f"SELECT DISTINCT creator FROM mints SEMI JOIN ws ON ws.wallet = mints.creator "
        f"WHERE create_slot >= {lo} AND create_slot < {hi}").fetchall()}
    farmer = {r[0]: r[1] for r in con.execute(
        f"SELECT trader, avg(farmer::INT) FROM pool SEMI JOIN ws ON ws.wallet = pool.trader "
        f"WHERE slot >= {lo} AND slot < {hi} AND kind IN ('buy', 'sell') GROUP BY trader").fetchall()}
    return creators, farmer


def cluster_labels(con, clusters_dir, wallets, lo, hi):
    if not clusters_dir:
        return {}, {}
    mem = os.path.join(clusters_dir, "members-*.parquet")
    ev = os.path.join(clusters_dir, "events-*.parquet")
    rows = con.execute(f"""
      SELECT m.wallet, count(*) AS memberships,
             count(*) FILTER (WHERE e.venue = 'pool' AND e.orientation = 'normal' AND e.links LIKE '%tx%' AND m.buyer) AS split
      FROM read_parquet('{mem}') m JOIN read_parquet('{ev}') e USING (window_slot, eid)
      SEMI JOIN ws ON ws.wallet = m.wallet WHERE m.slot >= {lo} AND m.slot < {hi} GROUP BY m.wallet""").fetchall()
    return {r[0]: r[1] for r in rows}, {r[0]: r[2] for r in rows}


def leaderboard(day, scores, leaders, creators, farmer, memberships, split, days_in_window):
    by_w = {}
    for s in scores:
        if s["variant"] == LEADER_VARIANT and s["size_sol"] == LEADER_SIZE:
            by_w.setdefault(s["wallet"], {})[s["d"]] = s
    out = []
    for w, L in leaders.items():
        f = by_w.get(w, {})
        per_day = L["n"] / max(1, days_in_window)
        labels = {
            "creator": w in creators,
            "sniper": L["create_slot_share"] >= 0.5,
            "hf_bot": per_day >= HF_PER_DAY and (L["median_hold_s"] or 0) <= HF_HOLD_S,
            "farmer": farmer.get(w, 0) >= 0.5,
            "one_shot": L["n"] == 1,
            "bundler": memberships.get(w, 0) >= BUNDLER_MIN,
            "split_buyer": split.get(w, 0) >= 2,
        }
        f1, f2 = f.get(1), f.get(2)
        out.append({"day": day, "wallet": w, **labels, "label_text": ",".join(k for k, v in labels.items() if v),
                    "leader_n": L["n"], "leader_positions_per_day": per_day, "leader_ret_marked": L["mean_ret_marked"],
                    "leader_ret_realized": L["mean_ret_realized"], "leader_median_hold_s": L["median_hold_s"],
                    "leader_closed_share": L["closed_share"], "leader_sol_in": L["sol_in"], "curve_share": L["curve_share"],
                    "reversed_share": L["reversed_share"],
                    "follow_n": (f2 or f1 or {}).get("filled"), "control": (f2 or f1 or {}).get("control"),
                    "follow_ret_d1": (f1 or {}).get("mean_ret"), "follow_ret_d2": (f2 or {}).get("mean_ret"),
                    "follow_pnl_sol_d2": (f2 or {}).get("mean_pnl_sol"), "follow_shrunk_d2": (f2 or {}).get("shrunk_ret"),
                    "follow_shrunk_d1": (f1 or {}).get("shrunk_ret"), "q_d2": (f2 or {}).get("q_value"),
                    "q_d1": (f1 or {}).get("q_value"), "top_d2": (f2 or {}).get("top", False),
                    "follow_median_hold_slots": (f2 or {}).get("median_hold_slots"),
                    "leader_exit_share_d2": (f2 or {}).get("leader_exit_share"), "rank_d2": (f2 or {}).get("rank")})
    out.sort(key=lambda r: (r["rank_d2"] is None, r["rank_d2"] or 0, -(r["leader_ret_marked"] or 0)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True, help="UTC day D: the score uses [D - 7 d, D)")
    ap.add_argument("--episodes", required=True)
    ap.add_argument("--follow", required=True)
    ap.add_argument("--clusters")
    ap.add_argument("--out", default=os.path.join(OUT_ROOT, "scores-v1"))
    a = ap.parse_args()
    with open(DEFAULTS) as f:
        cfg = yaml.safe_load(f)
    con = connect()
    clock = Clock.from_store(con)
    d_slot, lo = day_bounds(con, a.day)
    scores = score_positions(con, a.follow, lo, d_slot, d_slot, cfg)
    leaders = leader_stats(con, a.episodes, lo, d_slot, d_slot, clock)
    days_in_window = con.execute(
        f"SELECT count(DISTINCT strftime(make_timestamp(block_time * 1000000), '%Y-%m-%d')) FROM blocks "
        f"WHERE slot >= {lo} AND slot < {d_slot}").fetchone()[0]
    wallets = sorted(leaders)
    creators, farmer = store_labels(con, wallets, lo, d_slot)
    memberships, split = cluster_labels(con, a.clusters, wallets, lo, d_slot)
    board = leaderboard(a.day, scores, leaders, creators, farmer, memberships, split, days_in_window)
    os.makedirs(a.out, exist_ok=True)
    pq.write_table(pa.Table.from_pylist([dict(s, day=a.day) for s in scores]), os.path.join(a.out, f"scores-{a.day}.parquet"))
    pq.write_table(pa.Table.from_pylist(board), os.path.join(a.out, f"leaderboard-{a.day}.parquet"))
    with open(os.path.join(a.out, f"run-score-{a.day}.json"), "w") as f:
        json.dump({"day": a.day, "window_slots": [lo, d_slot], "days_in_window": days_in_window, "commit": git_commit(),
                   "min_n": MIN_N, "fdr_q": FDR_Q, "wallets": len(board), "scored": len(scores),
                   "top": sum(1 for s in scores if s["top"])}, f, indent=1)
    print(f"{a.day}: window [{lo}, {d_slot}) = {days_in_window} days, {len(board)} wallets, {len(scores)} score rows, "
          f"{sum(1 for s in scores if s['top'])} top (q < {FDR_Q})")


if __name__ == "__main__":
    main()
