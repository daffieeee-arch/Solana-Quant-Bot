"""Spec A report (week3-specs-money-2026-10-12.md) from money.py's output: labels from behaviour on the
label window, the seven groups measured on days 10-18, the day-bootstrap CI and p-value, the Holm
correction over the seven groups, 100 stratified placebo draws per group, and the zero-sum check.

Label window: days 1-9 (< SPLIT_DAY). A wallet first seen on or after SPLIT_DAY is labelled on its first
two full active days and measured from the day after. Labels need >= MIN_POS positions in the window
(retail does not). Hierarchy, first match wins: creator (created a token in the period, or shares a
non-service fee payer with a creator / had its fees paid by one), sniper (>= 50 % of positions opened
within SNIPE_SLOTS of the create), bundler (>= BUNDLER_MIN memberships of cluster events, clusters-v1),
farmer (>= 50 % of pool legs flagged, >= 20 pool legs), bot (>= BOT_PER_DAY positions per active day and
median hold <= BOT_HOLD_S), whale (median first buy >= WHALE_SOL, >= 20 positions), retail (the rest).

Labels use only behaviour before the measurement: a wallet that creates its first token, or first shares a
fee payer with a creator, on a measurement day is not a creator for that measurement (the whole-period flag
stays as a descriptive column); cluster memberships count up to the label window's end.

Measure per group on days 10-18 (SPLIT_DAY … LAST_DAY): net P&L per wallet-day (cash - network fees +
the change in marked inventory value), its day-bootstrap 95 % CI (whole days, BOOT times, seed 7) and
two-sided p-value, P&L per SOL deployed, the median wallet P&L, the share of wallets > 0; the sources
(median hold, snipe share, creator-link share, median buy, fees over turnover). Control tables (spec
section 3): the same for days 1-9 (in-sample: the label window itself) and per slot-time regime (one block
per regime label in the rows: the day's epoch ms per slot from the store meta, rounded to 10 ms). Placebo (binding table only): for every group PLACEBO draws, with replacement,
of as many non-group wallets, one per group wallet from the same stratum (positions decile x active days
x median-buy decile of the label window; an empty stratum falls back to its neighbours and is reported);
the number of draws below the group. The bar (spec section 5): mean > 0, Holm over the seven p-values at
alpha 0.05, and above >= 95 % of the draws. The placebo runs on numpy arrays (wallet-day rows once), so the
report step fits a small memory limit next to the DuckDB tables.

  python -m wallets.money_report --money DIR [--clusters DIR] [--boot 10000] [--placebo 100] [--out FILE]
"""

import argparse
import glob
import math
import os
import sys

import duckdb
import numpy as np

from wallets.common import REF_MS

SPLIT_DAY, LAST_DAY = "2026-09-20", "2026-09-30"
MS_SQL = "ms_per_slot"  # per wallet-day row, from the store meta (money.py); nothing about regimes is hardcoded here
MIN_POS = 5
SNIPE_SHARE = 0.5
BUNDLER_MIN = 3
FARMER_SHARE, FARMER_MIN_LEGS = 0.5, 20
BOT_PER_DAY, BOT_HOLD_S = 50, 30.0
WHALE_SOL, WHALE_MIN_POS = 5.0, 20
SERVICE_WALLETS, SERVICE_MINTS = 100, 50
GROUPS = ("creator", "sniper", "bundler", "farmer", "bot", "whale", "retail")
BOOT, PLACEBO, ALPHA, PLACEBO_SHARE = 10_000, 100, 0.05, 0.95
def fmt(x, nd=2, pct=False, sign=False):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    if isinstance(x, (bool, np.bool_)):
        return "yes" if x else "no"
    if isinstance(x, (int, np.integer)):
        return f"{x:,}"
    v = x * 100 if pct else x
    s = f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}"
    return s + ("%" if pct else "")


def load(con, money_dir, clusters_dir=None, materialize=True):
    """materialize=False keeps the wallet-day rows (tens of millions on a full run) and the all-days measurement as
    views on the parquet files instead of tables: slower (every statistic rescans the files) but it fits a few GB,
    so the report and its checks can be re-rendered on the VPS."""
    files = sorted(glob.glob(os.path.join(money_dir, "wallet-day-*.parquet")))
    if not files:
        raise SystemExit(f"no wallet-day-*.parquet in {money_dir}")
    lit = "[" + ",".join(f"'{f}'" for f in files) + "]"
    kind = "TABLE" if materialize else "VIEW"
    con.execute(f"CREATE OR REPLACE TEMP {kind} wd AS SELECT * FROM read_parquet({lit}, union_by_name = true)")
    cols = [c[0] for c in con.execute("SELECT * FROM wd LIMIT 0").description]
    if "ms_per_slot" not in cols or "regime" not in cols:  # rows from before #231: one 'unknown' regime, holds at the reference ms
        print(f"wallet-day rows without ms_per_slot / regime (run from before the regime columns): regime 'unknown', {REF_MS} ms per slot", file=sys.stderr)
        con.execute(f"CREATE OR REPLACE TEMP {kind} wd AS SELECT *, {REF_MS}::DOUBLE AS ms_per_slot, 'unknown' AS regime FROM read_parquet({lit}, union_by_name = true)")
    for name in ("payers", "creators", "venue_flows", "marks", "lp_legs"):
        path = os.path.join(money_dir, name + ".parquet")
        if os.path.exists(path):
            con.execute(f"CREATE OR REPLACE TEMP TABLE {name} AS SELECT * FROM '{path}'")
        elif name == "lp_legs":
            con.execute("CREATE OR REPLACE TEMP TABLE lp_legs (pool VARCHAR, mint VARCHAR, orientation VARCHAR, slot BIGINT, tx_index BIGINT, kind VARCHAR, sol_in BIGINT, depth_pre BIGINT, depth_post BIGINT)")
    con.execute("CREATE OR REPLACE TEMP TABLE wallet_ms AS SELECT wallet, median(ms_per_slot) AS ms FROM wd GROUP BY wallet")
    # label window per wallet
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE win AS
      SELECT wallet, first_day,
             CASE WHEN first_day < '{SPLIT_DAY}' THEN '0000' ELSE first_day END AS label_from_excl,
             CASE WHEN first_day < '{SPLIT_DAY}' THEN '{SPLIT_DAY}' ELSE strftime(CAST(first_day AS DATE) + INTERVAL 3 DAY, '%Y-%m-%d') END AS measure_from
      FROM (SELECT wallet, min(day) AS first_day FROM wd WHERE n_legs > 0 GROUP BY wallet)""")
    # the label days: days 1-9, or the two full days after the first day for late wallets
    con.execute("""
      CREATE OR REPLACE TEMP TABLE feat AS
      SELECT w.wallet,
             sum(d.n_opened) AS n_positions, count(*) FILTER (WHERE d.n_legs > 0) AS active_days,
             median(d.median_buy_sol) / 1e9 AS median_buy_sol,
             sum(d.n_snipe)::DOUBLE / nullif(sum(d.n_opened), 0) AS snipe_share,
             sum(d.n_pool_legs) AS n_pool_legs, sum(d.n_farmer_legs)::DOUBLE / nullif(sum(d.n_pool_legs), 0) AS farmer_share,
             median(d.median_hold_slots) AS median_hold_slots, sum(d.n_closed) AS n_closed
      FROM win w JOIN wd d ON d.wallet = w.wallet AND d.day > w.label_from_excl AND d.day < w.measure_from
      GROUP BY w.wallet""")
    # creators and the fee-payer link (service payers excluded)
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE service AS
      SELECT fee_payer FROM payers GROUP BY fee_payer HAVING count(DISTINCT wallet) >= {SERVICE_WALLETS} AND count(DISTINCT mint) >= {SERVICE_MINTS}""")
    con.execute("""
      CREATE OR REPLACE TEMP TABLE creator_set AS
      SELECT wallet, min(since) AS since, arg_min(how, since) AS how FROM (
        SELECT wallet, day AS since, 'creator' AS how FROM creators
        UNION ALL
        SELECT p.wallet, greatest(p.first_day, c.day), 'paid by creator'
        FROM payers p JOIN (SELECT wallet, min(day) AS day FROM creators GROUP BY wallet) c ON c.wallet = p.fee_payer
        WHERE p.fee_payer NOT IN (SELECT fee_payer FROM service)
        UNION ALL
        SELECT p2.wallet, greatest(p2.first_day, p1.first_day, c.day), 'shares payer'
        FROM payers p2 JOIN payers p1 ON p1.fee_payer = p2.fee_payer AND p1.wallet <> p2.wallet
        JOIN (SELECT wallet, min(day) AS day FROM creators GROUP BY wallet) c ON c.wallet = p1.wallet
        WHERE p2.fee_payer NOT IN (SELECT fee_payer FROM service))
      GROUP BY wallet""")
    # bundler memberships (cluster events bought into), before the label window's end
    # cluster memberships up to the wallet's label window end (the last traded slot before its measure_from)
    con.execute("""
      CREATE OR REPLACE TEMP TABLE day_cut AS
      SELECT w.measure_from AS day, (SELECT max(last_slot) FROM marks k WHERE k.day < w.measure_from AND k.traded) AS cutoff
      FROM (SELECT DISTINCT measure_from FROM win) w""")
    if clusters_dir and glob.glob(os.path.join(clusters_dir, "members-*.parquet")):
        mlit = "[" + ",".join(f"'{f}'" for f in sorted(glob.glob(os.path.join(clusters_dir, 'members-*.parquet')))) + "]"
        con.execute(f"""
          CREATE OR REPLACE TEMP TABLE memberships AS
          SELECT e.wallet, count(*) AS n
          FROM (SELECT DISTINCT wallet, window_slot, eid, slot FROM read_parquet({mlit}) WHERE buyer) e
          JOIN win w ON w.wallet = e.wallet JOIN day_cut dc ON dc.day = w.measure_from
          WHERE e.slot < COALESCE(dc.cutoff, 0) GROUP BY e.wallet""")
    else:
        con.execute("CREATE OR REPLACE TEMP TABLE memberships (wallet VARCHAR, n BIGINT)")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE labels AS
      SELECT w.wallet, w.measure_from, f.n_positions, f.active_days, f.median_buy_sol, f.snipe_share, f.farmer_share, f.n_pool_legs,
             f.median_hold_slots * wm.ms / 1000.0 AS median_hold_s, COALESCE(m.n, 0) AS memberships,
             c.how AS creator_how, c.since AS creator_since,
             CASE WHEN COALESCE(f.n_positions, 0) >= {MIN_POS} AND c.since < w.measure_from THEN 'creator'
                  WHEN COALESCE(f.n_positions, 0) >= {MIN_POS} AND f.snipe_share >= {SNIPE_SHARE} THEN 'sniper'
                  WHEN COALESCE(f.n_positions, 0) >= {MIN_POS} AND COALESCE(m.n, 0) >= {BUNDLER_MIN} THEN 'bundler'
                  WHEN COALESCE(f.n_positions, 0) >= {MIN_POS} AND f.n_pool_legs >= {FARMER_MIN_LEGS} AND f.farmer_share >= {FARMER_SHARE} THEN 'farmer'
                  WHEN COALESCE(f.n_positions, 0) >= {MIN_POS} AND f.n_positions::DOUBLE / nullif(f.active_days, 0) >= {BOT_PER_DAY}
                       AND f.median_hold_slots * wm.ms / 1000.0 <= {BOT_HOLD_S} THEN 'bot'
                  WHEN COALESCE(f.n_positions, 0) >= {WHALE_MIN_POS} AND f.median_buy_sol >= {WHALE_SOL} THEN 'whale'
                  ELSE 'retail' END AS label,
             c.wallet IS NOT NULL AS creator_any, c.since < w.measure_from AS creator_before, f.snipe_share >= {SNIPE_SHARE} AS sniper_any, COALESCE(m.n, 0) >= {BUNDLER_MIN} AS bundler_any,
             f.n_pool_legs >= {FARMER_MIN_LEGS} AND f.farmer_share >= {FARMER_SHARE} AS farmer_any,
             f.n_positions::DOUBLE / nullif(f.active_days, 0) >= {BOT_PER_DAY} AND f.median_hold_slots * wm.ms / 1000.0 <= {BOT_HOLD_S} AS bot_any,
             COALESCE(f.n_positions, 0) >= {WHALE_MIN_POS} AND f.median_buy_sol >= {WHALE_SOL} AS whale_any
      FROM win w LEFT JOIN feat f ON f.wallet = w.wallet LEFT JOIN memberships m ON m.wallet = w.wallet LEFT JOIN creator_set c ON c.wallet = w.wallet
      LEFT JOIN wallet_ms wm ON wm.wallet = w.wallet""")
    # measurement rows: days 10-18, from the wallet's measure_from
    con.execute(f"""
      CREATE OR REPLACE TEMP {kind} meas AS
      SELECT d.*, l.label FROM wd d JOIN labels l ON l.wallet = d.wallet
      WHERE d.day >= '{SPLIT_DAY}' AND d.day <= '{LAST_DAY}' AND d.day >= l.measure_from""")
    con.execute(f"""
      CREATE OR REPLACE TEMP {kind} meas_all AS
      SELECT d.*, l.label FROM wd d JOIN labels l ON l.wallet = d.wallet JOIN win w ON w.wallet = d.wallet WHERE d.day > w.label_from_excl""")
    return len(files)


def day_boot(sums, counts, boot, seed=7):
    """Bootstrap of a ratio-of-sums mean over days: (lo, hi, p two-sided); None with < 2 days."""
    sums, counts = np.asarray(sums, dtype=float), np.asarray(counts, dtype=float)
    k = len(sums)
    if k < 2 or counts.sum() == 0:
        return None, None, None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, k, size=(boot, k))
    means = sums[idx].sum(axis=1) / np.maximum(counts[idx].sum(axis=1), 1)
    p = 2 * min((means <= 0).mean(), (means >= 0).mean())
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)), float(min(1.0, p))


def group_stats(con, where, boot, table="meas"):
    per_day = con.execute(f"SELECT day, sum(pnl), count(*) FROM {table} WHERE {where} GROUP BY day ORDER BY day").fetchall()
    if not per_day:
        return None
    tot = con.execute(f"""
      SELECT sum(pnl), count(*), count(DISTINCT wallet), sum(buy_sol), sum(fees), sum(buy_sol + sell_sol),
             median(median_hold_slots * {MS_SQL}) / 1000.0, avg(n_snipe::DOUBLE / nullif(n_opened, 0)), median(median_buy_sol)
      FROM {table} WHERE {where}""").fetchone()
    per_wallet = con.execute(f"SELECT wallet, sum(pnl) AS p FROM {table} WHERE {where} GROUP BY wallet").fetchall()
    pw = np.array([r[1] for r in per_wallet], dtype=float)
    lo, hi, p = day_boot([r[1] for r in per_day], [r[2] for r in per_day], boot)
    mean = tot[0] / tot[1]
    return {"pnl": tot[0] / 1e9, "n": tot[1], "wallets": tot[2], "mean": mean / 1e9, "lo": None if lo is None else lo / 1e9,
            "hi": None if hi is None else hi / 1e9, "p": p, "per_sol": tot[0] / tot[3] if tot[3] else None,
            "median_wallet": float(np.median(pw)) / 1e9 if len(pw) else None, "share_pos": float((pw > 0).mean()) if len(pw) else None,
            "fees_turnover": tot[4] / tot[5] if tot[5] else None, "hold_s": tot[6],
            "snipe": tot[7], "buy": None if tot[8] is None else tot[8] / 1e9, "days": len(per_day)}


def holm(pvals, alpha=ALPHA):
    """Holm step-down: returns the set of indices that pass."""
    order = sorted(range(len(pvals)), key=lambda i: (pvals[i] if pvals[i] is not None else 2))
    passed = set()
    m = len(pvals)
    for k, i in enumerate(order):
        p = pvals[i]
        if p is None or p > alpha / (m - k):
            break
        passed.add(i)
    return passed


class Rows:
    """The measurement's wallet-day rows as numpy arrays, grouped by wallet: pnl, day index, and per wallet the
    slice [ptr[i], ptr[i + 1]). Built once per report; a placebo draw is a weighted bincount over them."""

    def __init__(self, con):
        t = con.execute("SELECT wallet, day, pnl FROM meas ORDER BY wallet").fetchnumpy()
        wallets = np.asarray(t["wallet"], dtype=object)
        self.days, self.day_idx = np.unique(np.asarray(t["day"], dtype=object), return_inverse=True)
        self.pnl = np.asarray(t["pnl"], dtype=float)
        uniq, start = np.unique(wallets, return_index=True)
        self.wallet_id = {w: k for k, w in enumerate(uniq)}
        self.ptr = np.append(start, len(wallets))
        self.row_wallet = np.repeat(np.arange(len(uniq)), np.diff(self.ptr))

    def mean_per_wallet_day(self, chosen_ids):
        """Mean P&L per wallet-day of a multiset of wallets (chosen with replacement)."""
        w = np.bincount(chosen_ids, minlength=len(self.ptr) - 1).astype(float)
        rw = w[self.row_wallet]
        tot = np.bincount(self.day_idx, weights=self.pnl * rw, minlength=len(self.days)).sum()
        n = rw.sum()
        return tot / n / 1e9 if n else 0.0


def placebo(con, group, draws, boot, seed=7, rows=None):
    """Draws (with replacement) of as many non-group wallets as the group has, one per group wallet from the
    same stratum of the label window; returns how many draws fall below the group's mean per wallet-day, and
    the members for whom no stratum (nor a neighbouring one) had a candidate."""
    rows = rows or Rows(con)
    con.execute("""
      CREATE OR REPLACE TABLE strata AS
      SELECT wallet, label, ntile(10) OVER (ORDER BY COALESCE(n_positions, 0)) AS pos_dec, least(COALESCE(active_days, 0), 9) AS act,
             ntile(10) OVER (ORDER BY COALESCE(median_buy_sol, 0)) AS buy_dec
      FROM labels WHERE wallet IN (SELECT DISTINCT wallet FROM meas)""")
    members = con.execute("SELECT pos_dec, act, buy_dec, count(*) FROM strata WHERE label = ? GROUP BY ALL", [group]).fetchall()
    if not members:
        return None
    pool = {}
    for pd_, act, bd, w in con.execute("SELECT pos_dec, act, buy_dec, wallet FROM strata WHERE label <> ?", [group]).fetchall():
        if w in rows.wallet_id:
            pool.setdefault((pd_, act, bd), []).append(rows.wallet_id[w])
    pool = {k: np.array(v) for k, v in pool.items()}
    rng = np.random.default_rng(seed)
    real = group_stats(con, f"label = '{group}'", 100)["mean"]
    means, unmatched = [], 0
    plan = []
    for pd_, act, bd, cnt in members:
        cands = None
        for key in ((pd_, act, bd), (pd_, act, max(1, bd - 1)), (pd_, act, min(10, bd + 1)), (pd_, max(0, act - 1), bd), (max(1, pd_ - 1), act, bd)):
            if key in pool:
                cands = pool[key]
                break
        if cands is None:
            unmatched += cnt
        else:
            plan.append((cands, cnt))
    for _ in range(draws):
        chosen = np.concatenate([rng.choice(c, size=n, replace=True) for c, n in plan]) if plan else np.zeros(0, dtype=int)
        means.append(rows.mean_per_wallet_day(chosen) if len(chosen) else 0.0)
    means = np.array(means)
    return {"below": int((means < real).sum()), "draws": len(means), "median": float(np.median(means)), "p05": float(np.percentile(means, 5)),
            "p95": float(np.percentile(means, 95)), "unmatched": unmatched, "members": sum(c for *_, c in members)}


DRAIN_SHARE = 0.9  # an LP withdrawal that removes >= 90 % of the pool's SOL
DRAIN_WINDOWS = ((1, "1 min"), (5, "5 min"), (15, "15 min"), (60, "1 h"), (360, "6 h"))
def drains(con, parts_dir):
    """Descriptive (coordinator, 2026-10-12, for the K3 paper's exit and risk rules): how often the LP empties a
    reversed pool after an F7 trigger, and how long after. Trigger = distinct (pool, t) of family F7 in segment
    pool_reversed in the parts; drain = the first LP withdrawal on that pool at or after t that removes
    >= DRAIN_SHARE of the pool's SOL. Minutes use the regime's ms per slot. Rows: all triggers, per regime, and
    per pool (first trigger only), plus the same for the pools of the N2_F7 twins."""
    files = sorted(glob.glob(os.path.join(parts_dir, "*.parquet")))
    if not files or con.execute("SELECT count(*) FROM lp_legs").fetchone()[0] == 0:
        return None
    lit = "[" + ",".join(f"'{f}'" for f in files) + "]"
    con.execute(f"""
      CREATE OR REPLACE TABLE trig AS
      SELECT family, pool, t, any_value(day) AS day, any_value(regime) AS regime, median(d_ms / d) AS ms_per_slot
      FROM read_parquet({lit}, union_by_name = true)
      WHERE family IN ('F7', 'N2_F7') AND segment = 'pool_reversed' AND pool IS NOT NULL AND d > 0 GROUP BY family, pool, t""")
    con.execute(f"""
      CREATE OR REPLACE TABLE drained AS
      SELECT tr.family, tr.pool, tr.t, tr.day, tr.regime, min(d.slot) AS drain_slot,
             (min(d.slot) - tr.t) * tr.ms_per_slot / 60000.0 AS minutes
      FROM trig tr LEFT JOIN (
        SELECT pool, slot FROM lp_legs WHERE kind = 'withdraw' AND depth_pre > 0 AND -sol_in >= {DRAIN_SHARE} * depth_pre) d
      ON d.pool = tr.pool AND d.slot >= tr.t
      GROUP BY tr.family, tr.pool, tr.t, tr.day, tr.regime, tr.ms_per_slot""")
    rows = []
    regimes = [r[0] for r in con.execute("SELECT DISTINCT regime FROM drained WHERE family = 'F7' AND regime IS NOT NULL ORDER BY 1").fetchall()]
    sets = [("F7 triggers, all", "family = 'F7'")] + [(f"F7 triggers, {r}", f"family = 'F7' AND regime = '{r}'") for r in regimes] + [
        ("F7 pools (first trigger per pool)", "family = 'F7' AND t = (SELECT min(t) FROM drained x WHERE x.family = 'F7' AND x.pool = drained.pool)"),
        ("N2_F7 twins' pools, all", "family = 'N2_F7'")]
    for label, where in sets:
        r = con.execute(f"""
          SELECT count(*), count(DISTINCT pool), avg((drain_slot IS NOT NULL)::INT),
                 {", ".join(f"avg(COALESCE(minutes <= {m}, false)::INT)" for m, _ in DRAIN_WINDOWS)},
                 median(minutes), avg(minutes), quantile_cont(minutes, 0.9)
          FROM drained WHERE {where}""").fetchone()
        if r[0]:
            rows.append((label,) + tuple(r))
    L = ["### LP drain after an F7 trigger in reversed pools (descriptive, for the K3 paper's exit and risk rules)\n",
         f"Drain = the first LP withdrawal on the trigger's pool at or after the trigger that removes ≥ {DRAIN_SHARE:.0%} of the pool's SOL; "
         "minutes from the trigger with the trigger's own ms per slot (d_ms / d of the parts); 'ever' = drained before the end of the development period. "
         "N2 rows: the pools the matched random entries sat in.\n",
         "| set | triggers | pools | drained ever | " + " | ".join(f"≤ {w}" for _, w in DRAIN_WINDOWS) + " | median min | mean min | p90 min |",
         "|---|---|---|---|" + "---|" * len(DRAIN_WINDOWS) + "---|---|---|"]
    for row in rows:
        label, n, pools, ever = row[0], row[1], row[2], row[3]
        wins = row[4:4 + len(DRAIN_WINDOWS)]
        med, mean, p90 = row[4 + len(DRAIN_WINDOWS):]
        L.append(f"| {label} | {fmt(n)} | {fmt(pools)} | {fmt(ever, 0, True)} | " + " | ".join(fmt(w, 0, True) for w in wins)
                 + f" | {fmt(med, 0)} | {fmt(mean, 0)} | {fmt(p90, 0)} |")
    L.append("")
    return "\n".join(L)


def zero_sum(con):
    rows = con.execute("SELECT venue, venue_fees, network_fees, legs, gross_sol, cash_all, sol_start, sol_end, external_in FROM venue_flows ORDER BY venue").fetchall()
    out = []
    for venue, vfee, nfee, legs, gross, cash, s0, s1, ext in rows:
        resid = (cash or 0) + (vfee or 0) + ((s1 or 0) - (s0 or 0)) - (ext or 0)
        out.append({"venue": venue, "cash": cash / 1e9, "venue_fees": vfee / 1e9, "network_fees": None if nfee is None else nfee / 1e9, "d_sol": ((s1 or 0) - (s0 or 0)) / 1e9,
                    "external": (ext or 0) / 1e9, "resid": resid / 1e9, "resid_share": resid / gross if gross else None, "gross": gross / 1e9, "legs": legs})
    return out


def group_table(con, boot, title, where_block, table, draws=0, do_placebo=False):
    """The per-group table for one block of days; the verdict only on the binding block (with placebo)."""
    stats = {g: group_stats(con, f"label = '{g}' AND ({where_block})", boot, table) for g in GROUPS}
    pv = [stats[g]["p"] if stats[g] else None for g in GROUPS]
    passed = holm(pv)
    rows = Rows(con) if do_placebo else None
    pl = {g: (placebo(con, g, draws, boot, rows=rows) if do_placebo and stats[g] else None) for g in GROUPS}
    L = [f"### {title}\n",
         "| group | wallets | wallet-days | days | Σ P&L SOL | **mean / wallet-day SOL** | 95% CI | p | Holm | per SOL deployed | median wallet SOL | wallets > 0 |"
         + (" above placebos | placebo median (p5 … p95) | **verdict** |" if do_placebo else ""),
         "|---|---|---|---|---|---|---|---|---|---|---|---|" + ("---|---|---|" if do_placebo else "")]
    for i, g in enumerate(GROUPS):
        s = stats[g]
        if not s:
            L.append(f"| {g} | 0 | | | | | | | | | | |" + (" | | – |" if do_placebo else ""))
            continue
        q = pl[g]
        ok = s["mean"] > 0 and i in passed and q is not None and q["below"] >= PLACEBO_SHARE * q["draws"]
        L.append(f"| {g} | {fmt(s['wallets'])} | {fmt(s['n'])} | {s['days']} | {fmt(s['pnl'], 1, sign=True)} | **{fmt(s['mean'], 4, sign=True)}** | "
                 f"{fmt(s['lo'], 4, sign=True)} … {fmt(s['hi'], 4, sign=True)} | {fmt(s['p'], 3)} | {'pass' if i in passed else 'fail'} | "
                 f"{fmt(s['per_sol'], 2, True, True)} | {fmt(s['median_wallet'], 4, sign=True)} | {fmt(s['share_pos'], 0, True)} |"
                 + ((f" above {q['below']} / {q['draws']}" + (f" ({q['unmatched']} of {q['members']} members without a stratum match)" if q["unmatched"] else "")
                     + f" | {fmt(q['median'], 4, sign=True)} ({fmt(q['p05'], 4, sign=True)} … {fmt(q['p95'], 4, sign=True)}) |" if q else " – | – |")
                    + f" **{'earns' if ok else 'no'}** |" if do_placebo else ""))
    L.append("")
    return "\n".join(L), stats


def render(con, boot, draws, do_placebo=True, parts_dir=None):
    L = ["## Follow the money (spec A)\n",
         f"Label window: days 1-9 (< {SPLIT_DAY}), late wallets their first two full active days; labels use only behaviour before the measurement "
         f"(a token created or a fee payer first shared on a measurement day does not count; the whole-period creator flag is a descriptive column). "
         f"Binding measurement {SPLIT_DAY} … {LAST_DAY}; control tables for days 1-9 (in-sample: the label window) and per slot-time regime "
         "(one per regime label in the rows: the day's epoch ms per slot from the store meta, rounded to 10 ms). "
         f"Net P&L per wallet-day = cash − network fees + Δ marked inventory (last price of the day, capped at the venue's real SOL). "
         f"CI: day bootstrap ({boot:,}×); p: two-sided. Placebo: {draws} draws with replacement of stratum-matched non-group wallets. "
         f"Bar (spec §5): mean > 0, Holm over the 7 groups at α = {ALPHA}, and above ≥ {PLACEBO_SHARE:.0%} of the placebo draws.\n"]
    md, stats = group_table(con, boot, f"Binding: days 10-18 ({SPLIT_DAY} … {LAST_DAY})", "TRUE", "meas", draws, do_placebo)
    L.append(md)
    regimes = [r[0] for r in con.execute("SELECT DISTINCT regime FROM wd WHERE regime IS NOT NULL ORDER BY 1").fetchall()]
    for title, where in [(f"Control: days 1-9 (< {SPLIT_DAY}, in-sample)", f"day < '{SPLIT_DAY}'")] + \
                        [(f"Control: regime {r} (days whose epoch runs at {r})", f"regime = '{r}'") for r in regimes]:
        md, _ = group_table(con, boot, title, where, "meas_all")
        L.append(md)
    L += ["Sources (binding measurement days):", "", "| group | median hold | snipe share | creator-linked before the measurement | creator-linked whole period | median first buy SOL | fees / turnover |", "|---|---|---|---|---|---|---|"]
    for g in GROUPS:
        s = stats[g]
        if not s:
            continue
        cb, ca = con.execute("SELECT avg(creator_before::INT), avg(creator_any::INT) FROM labels WHERE label = ? AND wallet IN (SELECT DISTINCT wallet FROM meas)", [g]).fetchone()
        L.append(f"| {g} | {fmt(s['hold_s'], 0)} s | {fmt(s['snipe'], 0, True)} | {fmt(cb, 0, True)} | {fmt(ca, 0, True)} | {fmt(s['buy'], 2)} | {fmt(s['fees_turnover'], 2, True)} |")
    L += ["", "Label overlap (any-label flags, wallets in the measurement):", "", "| label | wallets | also creator | also sniper | also bundler | also farmer | also bot | also whale |", "|---|---|---|---|---|---|---|---|"]
    for g in GROUPS[:-1]:
        r = con.execute(f"""SELECT count(*), sum(creator_any::INT), sum(sniper_any::INT), sum(bundler_any::INT), sum(farmer_any::INT), sum(bot_any::INT), sum(whale_any::INT)
                            FROM labels WHERE label = '{g}' AND wallet IN (SELECT DISTINCT wallet FROM meas)""").fetchone()
        L.append(f"| {g} | " + " | ".join(fmt(int(x or 0)) for x in r) + " |")
    real_, marked, netfees = con.execute(f"SELECT sum(cash - fees) / 1e9, sum(CASE WHEN day = (SELECT max(day) FROM wd) THEN value_end ELSE 0 END) / 1e9, sum(fees) / 1e9 FROM wd").fetchone()
    L += ["", "Zero-sum check. The conservation law the data allow is on cash: every SOL a trader pays goes into the venue, every SOL received comes out of it, "
          "so Σ traders' cash + venue fees that left in SOL + Δ SOL in the venues − SOL that entered from outside the trades (LP deposits − withdrawals) = 0 "
          "when every leg is captured. Spec §6 wrote it with the marked inventory on the left; marks are a valuation, not a flow, so they cannot close the "
          "books and are shown separately: realised Σ (cash − network fees) over the period "
          f"{fmt(real_, 0, sign=True)} SOL, marked Σ value_end on the last day {fmt(marked, 0)} SOL; network fees (all venues, once per transaction, from the wallet-days) {fmt(netfees, 0)} SOL.", "",
          "| venue | legs | gross SOL | Σ cash | venue fees | Δ SOL in venues | external in | **residual** | residual / gross |", "|---|---|---|---|---|---|---|---|---|"]
    for z in zero_sum(con):
        L.append(f"| {z['venue']} | {fmt(z['legs'])} | {fmt(z['gross'], 0)} | {fmt(z['cash'], 1, sign=True)} | {fmt(z['venue_fees'], 1)} | "
                 f"{fmt(z['d_sol'], 1, sign=True)} | {fmt(z['external'], 1, sign=True)} | **{fmt(z['resid'], 1, sign=True)}** | {fmt(z['resid_share'], 2, True)} |")
    L.append("")
    L.append("A residual above 1 % of the gross turnover means the books do not close; the group table is then not to be published before the cause is found (spec §6).")
    if parts_dir:
        d = drains(con, parts_dir)
        if d:
            L += ["", d]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--money", required=True)
    ap.add_argument("--clusters")
    ap.add_argument("--boot", type=int, default=BOOT)
    ap.add_argument("--placebo", type=int, default=PLACEBO)
    ap.add_argument("--no-placebo", action="store_true")
    ap.add_argument("--parts", help="an F7 tournament's parts for the LP-drain section (F7 pool_reversed triggers)")
    ap.add_argument("--split-day", help="smoke tests only: first measurement day (the spec's 2026-09-20)")
    ap.add_argument("--last-day", help="smoke tests only: last measurement day (the spec's 2026-09-30)")
    ap.add_argument("--views", action="store_true", help="keep the wallet-day rows as views on the parquet (fits a few GB; slower)")
    ap.add_argument("--out")
    a = ap.parse_args()
    global SPLIT_DAY, LAST_DAY
    if a.split_day or a.last_day:
        SPLIT_DAY, LAST_DAY = a.split_day or SPLIT_DAY, a.last_day or LAST_DAY
        print(f"smoke window {SPLIT_DAY} … {LAST_DAY}: not the spec's binding window", file=sys.stderr)
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{os.environ.get('LAB_WALLETS_MEMORY', '1500MB')}'; SET threads TO 2")
    spill = os.environ.get("LAB_SPILL_DIR")
    if spill:
        os.makedirs(spill, exist_ok=True)
        con.execute(f"SET temp_directory = '{spill}'; SET max_temp_directory_size = '{os.environ.get('LAB_SPILL_MAX', '1GB')}'")
    load(con, a.money, a.clusters, materialize=not a.views)
    md = render(con, a.boot, a.placebo, not a.no_placebo, a.parts)
    if a.out:
        with open(a.out, "w") as f:
            f.write(md + "\n")
        print(a.out, file=sys.stderr)
    else:
        print(md)


if __name__ == "__main__":
    main()
