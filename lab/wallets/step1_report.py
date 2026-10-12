"""Walvis stap 1, the report over a finished run: follower replay (follow.py), leader episodes
(episodes.py) and the daily scores (score.py), rendered as Markdown (#167).

Reads only the run's parquet files (no store). Net returns are settled at base costs through
backtest.costs.settle_sql, exactly as score.py does. Everything per variant x delay x group
(candidates / the random control wallets):

  - net return per position (mean, median, SOL), win rate, entry failures, and a 95% bootstrap
    interval over UTC days (whole days resampled, BOOT times: the stop rule's method);
  - the leader's own marked return on the same episodes, the share of our exits caused by the
    leader's sale, the share where the leader sold within FAST_SLOTS of our entry, the median
    slots until the leader sold;
  - per venue / orientation and per day;
  - wallet level: correlation between a wallet's own return and its follower return, the share of
    wallets with a positive follower mean;
  - scores: ranked wallets, FDR survivors (q < 0.10), copyable wallets per day, and the
    walk-forward check that matters: the follower return on day D of the wallets ranked top TOP_N
    at the start of D (the score used only data before D), against all candidates on D.

  python -m wallets.step1_report --follow DIR --episodes DIR --scores DIR [--out FILE.md] [--boot 10000]
"""

import argparse
import glob
import json
import os
import sys

import duckdb
import numpy as np
import yaml

from backtest.costs import Scenario, settle_sql

from .score import DEFAULTS, FAST_SLOTS, FDR_Q, LEADER_SIZE, LEADER_VARIANT, MIN_N

BOOT = 10_000
TOP_N = 20
MIN_WALLET_N = 10


def fmt(x, p=1, pct=False, sign=False):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "–"
    if isinstance(x, (bool, np.bool_)):
        return "yes" if x else "no"
    if pct:
        s = f"{x * 100:+.{p}f}%" if sign else f"{x * 100:.{p}f}%"
        return s
    if isinstance(x, (int, np.integer)):
        return f"{x:,}"
    if abs(x) >= 1000:
        return f"{x:,.0f}"
    return f"{x:+.{p}f}" if sign else f"{x:.{p}f}"


def day_bootstrap(sums, counts, boot, seed=1):
    """95% interval of the mean per position when whole days are resampled with replacement."""
    sums, counts = np.asarray(sums, dtype=float), np.asarray(counts, dtype=float)
    if len(sums) < 2 or counts.sum() == 0:
        return None, None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(sums), size=(boot, len(sums)))
    means = sums[idx].sum(axis=1) / np.maximum(counts[idx].sum(axis=1), 1)
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def load(con, follow_dir, cfg):
    pnl, ret = settle_sql(Scenario.from_cfg("base", cfg))
    files = sorted(glob.glob(os.path.join(follow_dir, "follow-*.parquet")))
    if not files:
        raise SystemExit(f"no follow-*.parquet in {follow_dir}")
    lit = "[" + ",".join(f"'{f}'" for f in files) + "]"
    con.execute(f"""
      CREATE OR REPLACE TABLE p AS
      SELECT *, {pnl} AS pnl, {ret} AS ret,
             exit_reason = 'leader_sold' AS led,
             exit_reason = 'leader_sold' AND exit_decision_slot - entry_slot <= {FAST_SLOTS} AS fast
      FROM read_parquet({lit}, union_by_name = true) WHERE NOT skipped""")
    return len(files)


def groups(con, boot):
    rows = con.execute("""
      SELECT variant, d, control, count(*) AS n, count(DISTINCT wallet) AS wallets, avg(entry_failed::INT) AS failed,
             avg(ret) AS mean_ret, median(ret) AS med_ret, avg(pnl) / 1e9 AS pnl_sol, avg((ret > 0)::INT) AS win,
             avg(leader_ret_marked) AS leader, avg(led::INT) AS led, avg(fast::INT) AS fast,
             median(exit_decision_slot - entry_slot) FILTER (WHERE led) AS slots_to_sell,
             median(exit_slot - entry_slot) AS hold
      FROM p GROUP BY ALL ORDER BY variant, d, control""").fetchall()
    per_day = con.execute("SELECT variant, d, control, day, sum(ret), count(ret) FROM p GROUP BY ALL").fetchall()
    by = {}
    for v, d, c, day, s, n in per_day:
        by.setdefault((v, d, c), []).append((s or 0.0, n))
    out = []
    for r in rows:
        key = r[:3]
        lo, hi = day_bootstrap([x[0] for x in by[key]], [x[1] for x in by[key]], boot)
        out.append(dict(zip(["variant", "d", "control", "n", "wallets", "failed", "mean_ret", "med_ret", "pnl_sol", "win",
                             "leader", "led", "fast", "slots_to_sell", "hold"], r), ci_lo=lo, ci_hi=hi, days=len(by[key])))
    return out


def venues(con):
    return con.execute(f"""
      SELECT d, venue, orientation, control, count(*) AS n, avg(ret) AS mean_ret, avg(pnl) / 1e9 AS pnl_sol,
             avg(leader_ret_marked) AS leader, avg(entry_failed::INT) AS failed, avg(led::INT) AS led
      FROM p WHERE variant = '{LEADER_VARIANT}' AND size_sol = {LEADER_SIZE} GROUP BY ALL ORDER BY d, venue, orientation, control""").fetchall()


def days(con):
    return con.execute(f"""
      SELECT day, count(*) FILTER (WHERE NOT control) AS n_cand, avg(ret) FILTER (WHERE NOT control) AS cand,
             avg(pnl) FILTER (WHERE NOT control) / 1e9 AS cand_sol, avg(leader_ret_marked) FILTER (WHERE NOT control) AS leader,
             count(*) FILTER (WHERE control) AS n_ctrl, avg(ret) FILTER (WHERE control) AS ctrl,
             avg(entry_failed::INT) FILTER (WHERE NOT control) AS failed, avg(led::INT) FILTER (WHERE NOT control) AS led
      FROM p WHERE variant = '{LEADER_VARIANT}' AND size_sol = {LEADER_SIZE} AND d = 2 GROUP BY day ORDER BY day""").fetchall()


def wallet_level(con):
    return con.execute(f"""
      WITH w AS (SELECT wallet, control, avg(leader_ret_marked) AS l, avg(ret) AS f, count(ret) AS n, avg(fast::INT) AS fast
                 FROM p WHERE variant = '{LEADER_VARIANT}' AND size_sol = {LEADER_SIZE} AND d = 2 GROUP BY wallet, control
                 HAVING count(ret) >= {MIN_WALLET_N})
      SELECT control, count(*) AS wallets, corr(l, f) AS corr_lf, avg(f) AS mean_f, median(f) AS med_f,
             quantile_cont(f, 0.9) AS p90_f, avg((f > 0)::INT) AS pos_share, avg((l > 0)::INT) AS leader_pos_share,
             avg((fast <= 0.25)::INT) AS copyable_share
      FROM w GROUP BY control ORDER BY control""").fetchall()


def exits(con):
    return con.execute("SELECT exit_reason, count(*) AS n, avg(ret) AS mean_ret FROM p GROUP BY 1 ORDER BY 2 DESC").fetchall()


def scores(con, scores_dir):
    sc = sorted(glob.glob(os.path.join(scores_dir, "scores-*.parquet")))
    lb = sorted(glob.glob(os.path.join(scores_dir, "leaderboard-*.parquet")))
    if not sc or not lb:
        return None
    sl = "[" + ",".join(f"'{f}'" for f in sc) + "]"
    ll = "[" + ",".join(f"'{f}'" for f in lb) + "]"
    con.execute(f"CREATE OR REPLACE TABLE s AS SELECT * FROM read_parquet({sl}, union_by_name = true)")
    con.execute(f"CREATE OR REPLACE TABLE lb AS SELECT * FROM read_parquet({ll}, union_by_name = true)")
    per_day = con.execute(f"""
      SELECT s.day, count(*) FILTER (WHERE rank IS NOT NULL) AS ranked, sum(top::INT) AS top,
             sum((rank IS NOT NULL AND shrunk_ret > 0)::INT) AS shrunk_pos, any_value(prior) AS prior, any_value(k) AS k,
             (SELECT count(*) FROM lb WHERE lb.day = s.day) AS wallets,
             (SELECT sum(copyable::INT) FROM lb WHERE lb.day = s.day) AS copyable,
             (SELECT sum((copyable AND follow_shrunk_d2 > 0)::INT) FROM lb WHERE lb.day = s.day) AS copyable_pos,
             (SELECT sum((top_d2 AND copyable)::INT) FROM lb WHERE lb.day = s.day) AS top_copyable
      FROM s WHERE variant = '{LEADER_VARIANT}' AND size_sol = {LEADER_SIZE} AND d = 2 GROUP BY s.day ORDER BY s.day""").fetchall()
    per_variant = con.execute("""
      SELECT variant, d, count(DISTINCT day) AS days, sum((rank IS NOT NULL)::INT) AS ranked, sum(top::INT) AS top,
             sum((rank IS NOT NULL AND shrunk_ret > 0)::INT) AS shrunk_pos, avg(prior) AS prior, median(k) AS k
      FROM s GROUP BY ALL ORDER BY variant, d""").fetchall()
    # Walk-forward: the top-N of day D (scored on data before D) followed on D itself.
    wf = con.execute(f"""
      WITH top AS (SELECT day, wallet, rank_d2, follow_shrunk_d2, copyable FROM lb WHERE rank_d2 <= {TOP_N}),
      cp AS (SELECT day, wallet, control, ret, pnl FROM p WHERE variant = '{LEADER_VARIANT}' AND size_sol = {LEADER_SIZE} AND d = 2),
      pos AS (SELECT day, wallet, follow_shrunk_d2 FROM lb WHERE copyable AND follow_shrunk_d2 > 0)
      SELECT cp.day,
             count(*) FILTER (WHERE top.wallet IS NOT NULL) AS n_top, avg(ret) FILTER (WHERE top.wallet IS NOT NULL) AS ret_top,
             count(DISTINCT cp.wallet) FILTER (WHERE top.wallet IS NOT NULL) AS w_top,
             count(*) FILTER (WHERE pos.wallet IS NOT NULL) AS n_pos, avg(ret) FILTER (WHERE pos.wallet IS NOT NULL) AS ret_pos,
             count(DISTINCT cp.wallet) FILTER (WHERE pos.wallet IS NOT NULL) AS w_pos,
             count(*) FILTER (WHERE NOT control) AS n_all, avg(ret) FILTER (WHERE NOT control) AS ret_all,
             sum(ret) FILTER (WHERE top.wallet IS NOT NULL) AS sum_top, count(ret) FILTER (WHERE top.wallet IS NOT NULL) AS cnt_top,
             sum(ret) FILTER (WHERE pos.wallet IS NOT NULL) AS sum_pos, count(ret) FILTER (WHERE pos.wallet IS NOT NULL) AS cnt_pos
      FROM cp LEFT JOIN top ON top.day = cp.day AND top.wallet = cp.wallet
      LEFT JOIN pos ON pos.day = cp.day AND pos.wallet = cp.wallet
      WHERE cp.day IN (SELECT DISTINCT day FROM lb) GROUP BY cp.day ORDER BY cp.day""").fetchall()
    last = con.execute("SELECT max(day) FROM lb").fetchone()[0]
    labels = con.execute("""
      SELECT label_text, count(*) AS n, avg(leader_ret_marked) AS leader, avg(leader_positions_per_day) AS per_day,
             median(leader_median_hold_s) AS hold_s, avg(follow_ret_d2) AS follow
      FROM lb WHERE day = ? GROUP BY 1 ORDER BY 2 DESC LIMIT 12""", [last]).fetchall()
    top12 = con.execute("""
      SELECT wallet, label_text, leader_n, leader_ret_marked, leader_median_hold_s, follow_n, follow_ret_d1, follow_ret_d2,
             follow_shrunk_d2, q_d2, leader_sold_fast_share_d2, copyable, control
      FROM lb WHERE day = ? AND rank_d2 IS NOT NULL ORDER BY rank_d2 LIMIT 12""", [last]).fetchall()
    return {"per_day": per_day, "per_variant": per_variant, "wf": wf, "last": last, "labels": labels, "top12": top12}


def episodes_summary(con, ep_dir):
    files = sorted(glob.glob(os.path.join(ep_dir, "episodes-*.parquet")))
    if not files:
        return None
    lit = "[" + ",".join(f"'{f}'" for f in files) + "]"
    return con.execute(f"""
      SELECT count(*) AS episodes, count(DISTINCT wallet) AS wallets, count(DISTINCT day) AS days,
             min(t0) AS lo, max(t0) AS hi, avg(leader_ret_marked) AS leader_marked, avg(leader_ret_realized) AS leader_realized,
             avg(closed::INT) AS closed, avg(create_slot::INT) AS create_slot,
             (SELECT count(*) FROM (SELECT wallet FROM read_parquet({lit}, union_by_name = true) GROUP BY wallet HAVING count(*) >= {MIN_N})) AS wallets_min_n
      FROM read_parquet({lit}, union_by_name = true)""").fetchone()


def render(a, n_files, g, v, dd, wl, ex, sc, ep, boot):
    L = []
    cand = [r for r in g if not r["control"]]
    lo = min(r["n"] for r in g) if g else 0
    L.append(f"Run: `{a.follow}` ({n_files} files), `{a.episodes}`, `{a.scores}`. Net returns at base costs "
             f"(`backtest.costs`), 0.5 SOL unless stated. Intervals: 95% bootstrap over UTC days ({boot:,} resamples of whole days).\n")
    if ep:
        L.append(f"Leader episodes: {fmt(ep[0])} in {fmt(ep[2])} days (slots {ep[3]:,}–{ep[4]:,}), {fmt(ep[1])} wallets, "
                 f"{fmt(ep[9])} with ≥ {MIN_N} episodes; the leaders' own return {fmt(ep[5], pct=True, sign=True)} marked / "
                 f"{fmt(ep[6], pct=True, sign=True)} realized, {fmt(ep[7], pct=True)} closed inside 4 h, {fmt(ep[8], pct=True)} opened in a create slot.\n")
    L.append("### Net return per position, per variant\n")
    L.append("| variant | d | group | positions | wallets | days | entry failed | mean net | 95% CI (days) | median | net SOL / position | win | leader's own (marked) | exits by leader sale | leader sold ≤ 10 slots after our entry | median slots to leader sale | median hold (slots) |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in g:
        ci = f"{fmt(r['ci_lo'], pct=True, sign=True)} … {fmt(r['ci_hi'], pct=True, sign=True)}" if r["ci_lo"] is not None else "–"
        L.append(f"| {r['variant']} | {r['d']} | {'control' if r['control'] else 'candidates'} | {fmt(r['n'])} | {fmt(r['wallets'])} | {r['days']} | "
                 f"{fmt(r['failed'], pct=True)} | **{fmt(r['mean_ret'], pct=True, sign=True)}** | {ci} | {fmt(r['med_ret'], pct=True, sign=True)} | "
                 f"{fmt(r['pnl_sol'], 3, sign=True)} | {fmt(r['win'], 0, pct=True)} | {fmt(r['leader'], pct=True, sign=True)} | "
                 f"{fmt(r['led'], 0, pct=True)} | {fmt(r['fast'], 0, pct=True)} | {fmt(r['slots_to_sell'], 0)} | {fmt(r['hold'], 0)} |")
    L.append("")
    L.append(f"### By venue ({LEADER_VARIANT}, {LEADER_SIZE} SOL)\n")
    L.append("| d | venue | orientation | group | positions | mean net | net SOL | leader's own | entry failed | exits by leader sale |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for d, ven, ori, c, n, m, s, l, f, led in v:
        L.append(f"| {d} | {ven} | {ori} | {'control' if c else 'candidates'} | {fmt(n)} | {fmt(m, pct=True, sign=True)} | {fmt(s, 3, sign=True)} | "
                 f"{fmt(l, pct=True, sign=True)} | {fmt(f, pct=True)} | {fmt(led, 0, pct=True)} |")
    L.append("")
    L.append(f"### Per day ({LEADER_VARIANT}, d = 2, {LEADER_SIZE} SOL)\n")
    L.append("| day | candidate positions | mean net | net SOL | leader's own | entry failed | exits by leader sale | control positions | control mean net |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    neg = 0
    for day, n, m, s, l, nc, mc, f, led in dd:
        neg += (m or 0) < 0
        L.append(f"| {day} | {fmt(n)} | {fmt(m, pct=True, sign=True)} | {fmt(s, 3, sign=True)} | {fmt(l, pct=True, sign=True)} | "
                 f"{fmt(f, pct=True)} | {fmt(led, 0, pct=True)} | {fmt(nc)} | {fmt(mc, pct=True, sign=True)} |")
    L.append(f"\n{neg} of {len(dd)} days negative for the candidates.\n")
    L.append(f"### Wallet level ({LEADER_VARIANT}, d = 2; wallets with ≥ {MIN_WALLET_N} filled positions)\n")
    L.append("| group | wallets | corr(own return, follower return) | mean follower return | median | p90 | wallets with positive follower mean | wallets with positive own return | ≤ 25% fast leader sales |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for c, n, corr, mf, md, p90, pos, lpos, cp in wl:
        L.append(f"| {'control' if c else 'candidates'} | {fmt(n)} | {fmt(corr, 2, sign=True)} | {fmt(mf, pct=True, sign=True)} | {fmt(md, pct=True, sign=True)} | "
                 f"{fmt(p90, pct=True, sign=True)} | {fmt(pos, 0, pct=True)} | {fmt(lpos, 0, pct=True)} | {fmt(cp, 0, pct=True)} |")
    L.append("")
    L.append("Exit reasons (all variants): " + ", ".join(f"{r} {fmt(n)} ({fmt(m, pct=True, sign=True)})" for r, n, m in ex) + ".\n")
    if sc:
        L.append(f"### Scores per day ({LEADER_VARIANT}, d = 2; the score of day D uses [D − 7 d, D) only)\n")
        L.append(f"| day | wallets in window | ranked (≥ {MIN_N} positions) | positive shrunk score | top (q < {FDR_Q}) | prior | k | copyable | copyable and positive | top and copyable |")
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for day, ranked, top, spos, prior, k, w, cp, cpp, tc in sc["per_day"]:
            L.append(f"| {day} | {fmt(w)} | {fmt(ranked)} | {fmt(spos)} | {fmt(top)} | {fmt(prior, pct=True, sign=True)} | {fmt(k, 1)} | {fmt(cp)} | {fmt(cpp)} | {fmt(tc)} |")
        L.append("")
        L.append("| variant | d | days | ranked wallet-days | positive shrunk | top (q < 0.10) | mean prior | median k |")
        L.append("|---|---|---|---|---|---|---|---|")
        for var, d, nd, ranked, top, spos, prior, k in sc["per_variant"]:
            L.append(f"| {var} | {d} | {nd} | {fmt(ranked)} | {fmt(spos)} | {fmt(top)} | {fmt(prior, pct=True, sign=True)} | {fmt(k, 1)} |")
        L.append("")
        L.append(f"### Walk-forward: follow the top {TOP_N} of each day on that day ({LEADER_VARIANT}, d = 2)\n")
        L.append("The score of day D is computed from positions closed before D; the table follows those wallets on D itself.\n")
        L.append(f"| day | top-{TOP_N} wallets active | their positions | their mean net | copyable-and-positive wallets active | positions | mean net | all candidates: positions | mean net |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        st, ct, sp, cpn = [], [], [], []
        for day, n_top, r_top, w_top, n_pos, r_pos, w_pos, n_all, r_all, s_t, c_t, s_p, c_p in sc["wf"]:
            st.append(s_t or 0.0); ct.append(c_t or 0); sp.append(s_p or 0.0); cpn.append(c_p or 0)
            L.append(f"| {day} | {fmt(w_top)} | {fmt(n_top)} | {fmt(r_top, pct=True, sign=True)} | {fmt(w_pos)} | {fmt(n_pos)} | "
                     f"{fmt(r_pos, pct=True, sign=True)} | {fmt(n_all)} | {fmt(r_all, pct=True, sign=True)} |")
        if sum(ct):
            lo_t, hi_t = day_bootstrap(st, ct, boot)
            lo_p, hi_p = day_bootstrap(sp, cpn, boot)
            L.append(f"\nOver all days: top-{TOP_N} {fmt(sum(st) / sum(ct), pct=True, sign=True)} per position over {fmt(sum(ct))} positions"
                     + (f" (95% CI {fmt(lo_t, pct=True, sign=True)} … {fmt(hi_t, pct=True, sign=True)})" if lo_t is not None else "")
                     + (f"; copyable-and-positive {fmt(sum(sp) / sum(cpn), pct=True, sign=True)} over {fmt(sum(cpn))}"
                        + (f" (95% CI {fmt(lo_p, pct=True, sign=True)} … {fmt(hi_p, pct=True, sign=True)})" if lo_p is not None else "")
                        if sum(cpn) else "") + ".\n")
        L.append(f"### Labels on {sc['last']} (wallets active in the window)\n")
        L.append("| labels | wallets | leader's own return (marked) | positions / day | median hold (s) | follower return d = 2 |")
        L.append("|---|---|---|---|---|---|")
        for lt, n, l, pd, h, f in sc["labels"]:
            L.append(f"| {lt or '(none)'} | {fmt(n)} | {fmt(l, pct=True, sign=True)} | {fmt(pd, 1)} | {fmt(h, 0)} | {fmt(f, pct=True, sign=True)} |")
        L.append("")
        L.append(f"### Top 12 by shrunk score on {sc['last']} (addresses only)\n")
        L.append("| # | wallet | labels | episodes | own return | own hold (s) | followed positions | follower d = 1 | d = 2 | shrunk | q | fast leader sales | copyable | control |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for i, (w, lt, ln, lr, lh, fn, f1, f2, shr, q, fast, cp, c) in enumerate(sc["top12"], 1):
            L.append(f"| {i} | `{w}` | {lt or ''} | {fmt(ln)} | {fmt(lr, pct=True, sign=True)} | {fmt(lh, 0)} | {fmt(fn)} | {fmt(f1, pct=True, sign=True)} | "
                     f"{fmt(f2, pct=True, sign=True)} | {fmt(shr, pct=True, sign=True)} | {fmt(q, 2)} | {fmt(fast, 0, pct=True)} | {fmt(cp)} | {fmt(c)} |")
        L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--follow", required=True)
    ap.add_argument("--episodes", required=True)
    ap.add_argument("--scores", required=True)
    ap.add_argument("--out", help="Markdown file (default: stdout)")
    ap.add_argument("--boot", type=int, default=BOOT)
    ap.add_argument("--memory", default=os.environ.get("LAB_WALLETS_MEMORY", "1GB"))
    a = ap.parse_args()
    with open(DEFAULTS) as f:
        cfg = yaml.safe_load(f)
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{a.memory}'; SET threads TO 2; SET preserve_insertion_order = false")
    n_files = load(con, a.follow, cfg)
    md = render(a, n_files, groups(con, a.boot), venues(con), days(con), wallet_level(con), exits(con),
                scores(con, a.scores), episodes_summary(con, a.episodes), a.boot)
    if a.out:
        with open(a.out, "w") as f:
            f.write(md)
        print(f"wrote {a.out}", file=sys.stderr)
    else:
        print(md)


if __name__ == "__main__":
    main()
