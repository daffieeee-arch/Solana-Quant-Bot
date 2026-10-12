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

Measure per group on days 10-18 (SPLIT_DAY … LAST_DAY): net P&L per wallet-day (cash - network fees +
the change in marked inventory value), its day-bootstrap 95 % CI (whole days, BOOT times, seed 7) and
two-sided p-value, P&L per SOL deployed, the median wallet P&L, the share of wallets > 0; the sources
(median hold, snipe share, creator-link share, median buy, fees over turnover). Placebo: for every group
PLACEBO draws of as many non-group wallets, one per group wallet from the same stratum (positions decile
x active days x median-buy decile of the label window); the group's rank among the draws. The bar
(spec section 5): mean > 0, Holm over the seven p-values at alpha 0.05, and rank >= 95 of 100.

  python -m wallets.money_report --money DIR [--clusters DIR] [--boot 10000] [--placebo 100] [--out FILE]
"""

import argparse
import glob
import math
import os
import sys

import duckdb
import numpy as np

SPLIT_DAY, LAST_DAY = "2026-09-20", "2026-09-30"
MIN_POS = 5
SNIPE_SHARE = 0.5
BUNDLER_MIN = 3
FARMER_SHARE, FARMER_MIN_LEGS = 0.5, 20
BOT_PER_DAY, BOT_HOLD_S = 50, 30.0
WHALE_SOL, WHALE_MIN_POS = 5.0, 20
SERVICE_WALLETS, SERVICE_MINTS = 100, 50
GROUPS = ("creator", "sniper", "bundler", "farmer", "bot", "whale", "retail")
BOOT, PLACEBO, ALPHA, PLACEBO_SHARE = 10_000, 100, 0.05, 0.95
MS = {"316ms": 316.4, "267ms": 267.1}


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


def load(con, money_dir, clusters_dir=None, ms_per_slot=267.1):
    files = sorted(glob.glob(os.path.join(money_dir, "wallet-day-*.parquet")))
    if not files:
        raise SystemExit(f"no wallet-day-*.parquet in {money_dir}")
    lit = "[" + ",".join(f"'{f}'" for f in files) + "]"
    con.execute(f"CREATE OR REPLACE TABLE wd AS SELECT * FROM read_parquet({lit}, union_by_name = true)")
    for name in ("payers", "creators", "venue_flows", "marks"):
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM '{os.path.join(money_dir, name + '.parquet')}'")
    # label window per wallet
    con.execute(f"""
      CREATE OR REPLACE TABLE win AS
      SELECT wallet, first_day,
             CASE WHEN first_day < '{SPLIT_DAY}' THEN '0000' ELSE first_day END AS label_from_excl,
             CASE WHEN first_day < '{SPLIT_DAY}' THEN '{SPLIT_DAY}' ELSE strftime(CAST(first_day AS DATE) + INTERVAL 3 DAY, '%Y-%m-%d') END AS measure_from
      FROM (SELECT wallet, min(day) AS first_day FROM wd WHERE n_legs > 0 GROUP BY wallet)""")
    # the label days: days 1-9, or the two full days after the first day for late wallets
    con.execute("""
      CREATE OR REPLACE TABLE feat AS
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
      CREATE OR REPLACE TABLE service AS
      SELECT fee_payer FROM payers GROUP BY fee_payer HAVING count(DISTINCT wallet) >= {SERVICE_WALLETS} AND count(DISTINCT mint) >= {SERVICE_MINTS}""")
    con.execute("""
      CREATE OR REPLACE TABLE creator_set AS
      SELECT DISTINCT wallet, 'creator' AS how FROM creators
      UNION
      SELECT DISTINCT p.wallet, 'paid by creator' FROM payers p WHERE p.fee_payer IN (SELECT wallet FROM creators) AND p.fee_payer NOT IN (SELECT fee_payer FROM service)
      UNION
      SELECT DISTINCT p2.wallet, 'shares payer' FROM payers p2
      WHERE p2.fee_payer NOT IN (SELECT fee_payer FROM service)
        AND p2.fee_payer IN (SELECT p1.fee_payer FROM payers p1 WHERE p1.wallet IN (SELECT wallet FROM creators))""")
    # bundler memberships (cluster events bought into), before the label window's end
    if clusters_dir and glob.glob(os.path.join(clusters_dir, "members-*.parquet")):
        mlit = "[" + ",".join(f"'{f}'" for f in sorted(glob.glob(os.path.join(clusters_dir, 'members-*.parquet')))) + "]"
        cutoff = con.execute(f"SELECT max(last_slot) FROM marks WHERE day < '{SPLIT_DAY}' AND traded").fetchone()[0] or 0
        con.execute(f"""
          CREATE OR REPLACE TABLE memberships AS
          SELECT wallet, count(DISTINCT (window_slot, eid)) AS n FROM read_parquet({mlit}) WHERE buyer AND slot < {cutoff} GROUP BY wallet""")
    else:
        con.execute("CREATE OR REPLACE TABLE memberships (wallet VARCHAR, n BIGINT)")
    con.execute(f"""
      CREATE OR REPLACE TABLE labels AS
      SELECT w.wallet, w.measure_from, f.n_positions, f.active_days, f.median_buy_sol, f.snipe_share, f.farmer_share, f.n_pool_legs,
             f.median_hold_slots * {ms_per_slot} / 1000.0 AS median_hold_s, COALESCE(m.n, 0) AS memberships,
             c.how AS creator_how,
             CASE WHEN COALESCE(f.n_positions, 0) >= {MIN_POS} AND c.wallet IS NOT NULL THEN 'creator'
                  WHEN COALESCE(f.n_positions, 0) >= {MIN_POS} AND f.snipe_share >= {SNIPE_SHARE} THEN 'sniper'
                  WHEN COALESCE(f.n_positions, 0) >= {MIN_POS} AND COALESCE(m.n, 0) >= {BUNDLER_MIN} THEN 'bundler'
                  WHEN COALESCE(f.n_positions, 0) >= {MIN_POS} AND f.n_pool_legs >= {FARMER_MIN_LEGS} AND f.farmer_share >= {FARMER_SHARE} THEN 'farmer'
                  WHEN COALESCE(f.n_positions, 0) >= {MIN_POS} AND f.n_positions::DOUBLE / nullif(f.active_days, 0) >= {BOT_PER_DAY}
                       AND f.median_hold_slots * {ms_per_slot} / 1000.0 <= {BOT_HOLD_S} THEN 'bot'
                  WHEN COALESCE(f.n_positions, 0) >= {WHALE_MIN_POS} AND f.median_buy_sol >= {WHALE_SOL} THEN 'whale'
                  ELSE 'retail' END AS label,
             c.wallet IS NOT NULL AS creator_any, f.snipe_share >= {SNIPE_SHARE} AS sniper_any, COALESCE(m.n, 0) >= {BUNDLER_MIN} AS bundler_any,
             f.n_pool_legs >= {FARMER_MIN_LEGS} AND f.farmer_share >= {FARMER_SHARE} AS farmer_any,
             f.n_positions::DOUBLE / nullif(f.active_days, 0) >= {BOT_PER_DAY} AND f.median_hold_slots * {ms_per_slot} / 1000.0 <= {BOT_HOLD_S} AS bot_any,
             COALESCE(f.n_positions, 0) >= {WHALE_MIN_POS} AND f.median_buy_sol >= {WHALE_SOL} AS whale_any
      FROM win w LEFT JOIN feat f ON f.wallet = w.wallet LEFT JOIN memberships m ON m.wallet = w.wallet LEFT JOIN creator_set c ON c.wallet = w.wallet""")
    # measurement rows: days 10-18, from the wallet's measure_from
    con.execute(f"""
      CREATE OR REPLACE TABLE meas AS
      SELECT d.*, l.label FROM wd d JOIN labels l ON l.wallet = d.wallet
      WHERE d.day >= '{SPLIT_DAY}' AND d.day <= '{LAST_DAY}' AND d.day >= l.measure_from""")
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


def group_stats(con, where, boot):
    per_day = con.execute(f"SELECT day, sum(pnl), count(*) FROM meas WHERE {where} GROUP BY day ORDER BY day").fetchall()
    if not per_day:
        return None
    tot = con.execute(f"""
      SELECT sum(pnl), count(*), count(DISTINCT wallet), sum(buy_sol), sum(fees), sum(buy_sol + sell_sol),
             median(median_hold_slots), avg(n_snipe::DOUBLE / nullif(n_opened, 0)), median(median_buy_sol)
      FROM meas WHERE {where}""").fetchone()
    per_wallet = con.execute(f"SELECT wallet, sum(pnl) AS p FROM meas WHERE {where} GROUP BY wallet").fetchall()
    pw = np.array([r[1] for r in per_wallet], dtype=float)
    lo, hi, p = day_boot([r[1] for r in per_day], [r[2] for r in per_day], boot)
    mean = tot[0] / tot[1]
    return {"pnl": tot[0] / 1e9, "n": tot[1], "wallets": tot[2], "mean": mean / 1e9, "lo": None if lo is None else lo / 1e9,
            "hi": None if hi is None else hi / 1e9, "p": p, "per_sol": tot[0] / tot[3] if tot[3] else None,
            "median_wallet": float(np.median(pw)) / 1e9 if len(pw) else None, "share_pos": float((pw > 0).mean()) if len(pw) else None,
            "fees_turnover": tot[4] / tot[5] if tot[5] else None, "hold_s": None if tot[6] is None else tot[6] * 267.1 / 1000,
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


def placebo(con, group, draws, boot, seed=7):
    """Rank of the group's mean per wallet-day among `draws` stratified random sets of non-group wallets."""
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
        pool.setdefault((pd_, act, bd), []).append(w)
    wd = {}
    for w, day, pnl in con.execute("SELECT wallet, day, pnl FROM meas WHERE label <> ?", [group]).fetchall():
        wd.setdefault(w, []).append((day, pnl))
    rng = np.random.default_rng(seed)
    real = group_stats(con, f"label = '{group}'", 100)["mean"]
    means = []
    for _ in range(draws):
        per_day = {}
        for pd_, act, bd, cnt in members:
            cands = pool.get((pd_, act, bd)) or pool.get((pd_, act, max(1, bd - 1))) or pool.get((pd_, max(0, act - 1), bd)) or []
            if not cands:
                continue
            for w in rng.choice(cands, size=cnt, replace=True):
                for day, pnl in wd.get(w, []):
                    s, c = per_day.get(day, (0.0, 0))
                    per_day[day] = (s + pnl, c + 1)
        tot = sum(s for s, _ in per_day.values())
        n = sum(c for _, c in per_day.values())
        means.append(tot / n / 1e9 if n else 0.0)
    means = np.array(means)
    return {"below": int((means < real).sum()), "draws": len(means), "median": float(np.median(means)), "p05": float(np.percentile(means, 5)),
            "p95": float(np.percentile(means, 95))}


def zero_sum(con):
    rows = con.execute("SELECT venue, venue_fees, network_fees, legs, gross_sol, cash_all, sol_start, sol_end, external_in FROM venue_flows ORDER BY venue").fetchall()
    out = []
    for venue, vfee, nfee, legs, gross, cash, s0, s1, ext in rows:
        resid = (cash or 0) + (vfee or 0) + ((s1 or 0) - (s0 or 0)) - (ext or 0)
        out.append({"venue": venue, "cash": cash / 1e9, "venue_fees": vfee / 1e9, "network_fees": nfee / 1e9, "d_sol": ((s1 or 0) - (s0 or 0)) / 1e9,
                    "external": (ext or 0) / 1e9, "resid": resid / 1e9, "resid_share": resid / gross if gross else None, "gross": gross / 1e9, "legs": legs})
    return out


def render(con, boot, draws, do_placebo=True):
    L = ["## Follow the money (spec A)\n",
         f"Label window: days 1-9 (< {SPLIT_DAY}), late wallets their first two full active days; measured {SPLIT_DAY} … {LAST_DAY}. "
         f"Net P&L per wallet-day = cash − network fees + Δ marked inventory (last price of the day, capped at the venue's real SOL). "
         f"CI: day bootstrap ({boot:,}×); p: two-sided. Bar (spec §5): mean > 0, Holm over the 7 groups at α = {ALPHA}, and above ≥ {PLACEBO_SHARE:.0%} of the {draws} placebo draws.\n"]
    stats = {g: group_stats(con, f"label = '{g}'", boot) for g in GROUPS}
    pv = [stats[g]["p"] if stats[g] else None for g in GROUPS]
    passed = holm(pv)
    pl = {g: (placebo(con, g, draws, boot) if do_placebo and stats[g] else None) for g in GROUPS}
    L += ["| group | wallets | wallet-days | days | Σ P&L SOL | **mean / wallet-day SOL** | 95% CI | p | Holm | per SOL deployed | median wallet SOL | wallets > 0 | above placebos | placebo median (p5 … p95) | **verdict** |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, g in enumerate(GROUPS):
        s = stats[g]
        if not s:
            L.append(f"| {g} | 0 | | | | | | | | | | | | | – |")
            continue
        q = pl[g]
        ok = s["mean"] > 0 and i in passed and q is not None and q["below"] >= PLACEBO_SHARE * q["draws"]
        L.append(f"| {g} | {fmt(s['wallets'])} | {fmt(s['n'])} | {s['days']} | {fmt(s['pnl'], 1, sign=True)} | **{fmt(s['mean'], 4, sign=True)}** | "
                 f"{fmt(s['lo'], 4, sign=True)} … {fmt(s['hi'], 4, sign=True)} | {fmt(s['p'], 3)} | {'pass' if i in passed else 'fail'} | "
                 f"{fmt(s['per_sol'], 2, True, True)} | {fmt(s['median_wallet'], 4, sign=True)} | {fmt(s['share_pos'], 0, True)} | "
                 + (f"above {q['below']} / {q['draws']} | {fmt(q['median'], 4, sign=True)} ({fmt(q['p05'], 4, sign=True)} … {fmt(q['p95'], 4, sign=True)}) |" if q else "– | – |")
                 + f" **{'earns' if ok else 'no'}** |")
    L += ["", "Sources (measurement days):", "", "| group | median hold | snipe share | creator-linked | median first buy SOL | fees / turnover |", "|---|---|---|---|---|---|"]
    for g in GROUPS:
        s = stats[g]
        if not s:
            continue
        cl = con.execute("SELECT avg(creator_any::INT) FROM labels WHERE label = ?", [g]).fetchone()[0]
        L.append(f"| {g} | {fmt(s['hold_s'], 0)} s | {fmt(s['snipe'], 0, True)} | {fmt(cl, 0, True)} | {fmt(s['buy'], 2)} | {fmt(s['fees_turnover'], 2, True)} |")
    L += ["", "Label overlap (any-label flags, wallets in the measurement):", "", "| label | wallets | also creator | also sniper | also bundler | also farmer | also bot | also whale |", "|---|---|---|---|---|---|---|---|"]
    for g in GROUPS[:-1]:
        r = con.execute(f"""SELECT count(*), sum(creator_any::INT), sum(sniper_any::INT), sum(bundler_any::INT), sum(farmer_any::INT), sum(bot_any::INT), sum(whale_any::INT)
                            FROM labels WHERE label = '{g}' AND wallet IN (SELECT DISTINCT wallet FROM meas)""").fetchone()
        L.append(f"| {g} | " + " | ".join(fmt(int(x or 0)) for x in r) + " |")
    L += ["", "Zero-sum check (whole period, cash only: Σ traders' cash + venue fees that left in SOL + Δ SOL in the venues − SOL that entered from outside the trades "
          "(LP deposits − withdrawals) = residual):", "",
          "| venue | legs | gross SOL | Σ cash | venue fees | network fees | Δ SOL in venues | external in | **residual** | residual / gross |", "|---|---|---|---|---|---|---|---|---|---|"]
    for z in zero_sum(con):
        L.append(f"| {z['venue']} | {fmt(z['legs'])} | {fmt(z['gross'], 0)} | {fmt(z['cash'], 1, sign=True)} | {fmt(z['venue_fees'], 1)} | {fmt(z['network_fees'], 1)} | "
                 f"{fmt(z['d_sol'], 1, sign=True)} | {fmt(z['external'], 1, sign=True)} | **{fmt(z['resid'], 1, sign=True)}** | {fmt(z['resid_share'], 2, True)} |")
    L.append("")
    L.append("A residual above 1 % of the gross turnover means the books do not close; the group table is then not to be published before the cause is found (spec §6).")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--money", required=True)
    ap.add_argument("--clusters")
    ap.add_argument("--boot", type=int, default=BOOT)
    ap.add_argument("--placebo", type=int, default=PLACEBO)
    ap.add_argument("--no-placebo", action="store_true")
    ap.add_argument("--split-day", help="smoke tests only: first measurement day (the spec's 2026-09-20)")
    ap.add_argument("--last-day", help="smoke tests only: last measurement day (the spec's 2026-09-30)")
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
    load(con, a.money, a.clusters)
    md = render(con, a.boot, a.placebo, not a.no_placebo)
    if a.out:
        with open(a.out, "w") as f:
            f.write(md + "\n")
        print(a.out, file=sys.stderr)
    else:
        print(md)


if __name__ == "__main__":
    main()
