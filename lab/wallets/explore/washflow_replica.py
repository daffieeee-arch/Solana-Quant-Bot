"""E59 wash-flow population replica (research/prereg-washflow-replica-2026-10-22.md v1.0; M6 of the "follow the money"
workflow, E57). NAS job (run with PYTHONPATH=lab in a scope; LAB_DATA for the data root, LAB_STORE for the store):

  python -m wallets.explore.washflow_replica OUT_DIR

1. Selection on the FIT half (2026-09-11 .. 09-20) from money-v1 wallet-days: farmer-pool days (n_pool_legs >= 300 and
   farmer legs >= 50 %), per wallet >= MIN_DAYS such days, >= MIN_LEGS pool legs, median buy 0.05-3 SOL, bought >= 0.8 x sold,
   margin (cash - fees) / bought >= MARGIN (money-v1's fees carry the #504 double count: pessimistic, accepted in advance).
   The scale factor f_w = min(1, SIZE / p90 of the wallet's buy legs in the fit half, from the store) keeps the round trips in
   balance (a cap per leg does not: pre-reg §4).
2. Replica on the TEST half (2026-09-21 .. 09-30, 10 full UTC days): every buy / sell leg of the selected wallets; price in SOL
   per token (normal pool e / b, reversed b / e); p0 = the leg's pre-trade price, ppost = the price after the wallet's own leg,
   p_k = the pre-trade price of the first leg in the pool at slot >= s + k (k = 1, 2, 3; ppost when there is none);
   s = f_w x sol_amount; sg = +1 sell, -1 buy. Replica cash per leg: (i) with their impact sg s p_k / p0; (ii) without their
   own impact sg s p_k / ppost; (iii) PRIMARY = (ii) - s^2 / D_k (our own impact at the k-leg's SOL-side depth) - the network
   cost per leg (base scenario BASE_LAMPORTS; minimal MIN_LAMPORTS as a reference).
3. Per day (UTC, from blocks), per wallet, all / without the top 5 by fit net; day bootstrap (BOOT draws, seed 7) of the mean
   SOL per day; the outcome rule of pre-reg §5 on variant (iii), k = 3, base fees, all wallets: net per leg > 0 after base
   fees AND lower bound > 0 AND >= 7 of 10 days > 0 AND without the top 5 > 0 -> "LEAD LIVES", else "CLOSED".

Writes selected.parquet, legs-shift.parquet, washflow.md, washflow.json and run-washflow.json in OUT_DIR.
"""
import json
import os
import sys
import time

import duckdb
import numpy as np

from wallets.common import connect, git_commit

LAB = os.environ.get("LAB_DATA", "/home/chupa/Solana-project/data-old-faithful-one/lab")
STORE = os.environ.get("LAB_STORE", f"{LAB}/store/dev2.duckdb")
MONEY = os.environ.get("LAB_MONEY", f"{LAB}/wallets/money-v1")
FIT = ("2026-09-11", "2026-09-20")
TEST = ("2026-09-21", "2026-09-30")
MIN_DAYS, MIN_LEGS, MARGIN, MB = 4, 10_000, 0.005, (0.05, 3.0)
SIZE = 0.25
BASE_LAMPORTS, MIN_LAMPORTS = 720_618, 5_000
KS = (1, 2, 3)
TOP = 5
SEED = 7
RULE = {"fit": FIT, "test": TEST, "min_days": MIN_DAYS, "min_legs": MIN_LEGS, "margin": MARGIN, "median_buy_sol": MB, "size_sol": SIZE, "scale": "min(1, size / p90 buy leg in fit)",
        "base_lamports": BASE_LAMPORTS, "min_lamports": MIN_LAMPORTS, "ks": KS, "primary": {"variant": "iii", "k": 3, "fees": "base"}, "top": TOP, "min_pos_days": 7, "seed": SEED}


def select_wallets(con, money_dir):
    """The fit-half selection of pre-reg §3 from money-v1's wallet-days; returns the number selected."""
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE fitw AS
      SELECT wallet, count(*) AS ndays, sum(cash) / 1e9 AS cash, sum(fees) / 1e9 AS fees, sum(buy_sol) / 1e9 AS buy, sum(sell_sol) / 1e9 AS sell,
             sum(n_pool_legs) AS legs, median(median_buy_sol) / 1e9 AS mb
      FROM read_parquet('{money_dir}/wallet-day-*.parquet')
      WHERE n_pool_legs >= 300 AND n_farmer_legs >= 0.5 * n_pool_legs AND day >= '{FIT[0]}' AND day <= '{FIT[1]}' GROUP BY 1""")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE sel AS
      SELECT wallet, cash - fees AS net_fit, buy AS buy_fit, legs AS legs_fit, (cash - fees) / buy AS margin_fit, ndays AS days_fit,
             row_number() OVER (ORDER BY cash - fees DESC) AS rank_fit
      FROM fitw WHERE ndays >= {MIN_DAYS} AND legs >= {MIN_LEGS} AND mb BETWEEN {MB[0]} AND {MB[1]} AND buy >= 0.8 * sell AND buy > 0 AND (cash - fees) / buy >= {MARGIN}""")
    return con.execute("SELECT count(*) FROM sel").fetchone()[0]


def slot_bounds(con):
    """(fit_lo, fit_hi, test_lo, test_hi): slot bounds of the fit and test halves from the store's blocks (UTC dates)."""
    r = con.execute(f"""SELECT min(slot) FILTER (WHERE d >= DATE '{FIT[0]}'), max(slot) FILTER (WHERE d <= DATE '{FIT[1]}'),
                               min(slot) FILTER (WHERE d >= DATE '{TEST[0]}'), max(slot) FILTER (WHERE d <= DATE '{TEST[1]}')
                        FROM (SELECT slot, CAST(to_timestamp(block_time) AT TIME ZONE 'UTC' AS DATE) AS d FROM blocks)""").fetchone()
    if any(x is None for x in r):
        raise SystemExit(f"the store's blocks do not cover the fit and test halves: {r}")
    return tuple(int(x) for x in r)


PRICE = "CASE WHEN orientation = 'reversed' THEN b / e ELSE e / b END"
DEPTH = "CASE WHEN orientation = 'reversed' THEN b ELSE e END"


def scale_factors(con, fit_lo, fit_hi):
    """f_w per selected wallet from the p90 of its buy legs (SOL) in the fit half."""
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE sf AS
      SELECT s.wallet, least(1.0, {SIZE} / greatest(quantile_cont(p.sol_amount / 1e9, 0.9), 1e-9)) AS f_w, quantile_cont(p.sol_amount / 1e9, 0.9) AS p90_buy_fit
      FROM pool p JOIN sel s ON s.wallet = p.trader
      WHERE p.kind IN ('buy', 'sell') AND p.token_buy AND p.slot BETWEEN {fit_lo} AND {fit_hi} GROUP BY s.wallet""")
    con.execute("CREATE OR REPLACE TEMP TABLE sel2 AS SELECT s.*, COALESCE(f.f_w, 1.0) AS f_w, f.p90_buy_fit FROM sel s LEFT JOIN sf f USING (wallet)")


def extract(con, test_lo, test_hi):
    """wl: the selected wallets' buy / sell legs in the test half with p0, ppost, sg, s; o: every buy / sell leg of their pools
    in the test half (+ 400 slots) with the pre-trade price and SOL-side depth."""
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE wl AS
      SELECT p.trader AS wallet, p.pool, s.orientation, p.slot, (p.slot * 1000000 + p.tx_index * 100 + COALESCE(p.outer_ix, 0) * 10 + COALESCE(p.inner_ix, 0))::BIGINT AS k0,
             p.kind, p.token_buy, p.sol_amount / 1e9 AS sol, COALESCE(p.farmer, false) AS farmer,
             CASE WHEN s.orientation = 'reversed' THEN p.b::DOUBLE / p.e ELSE p.e::DOUBLE / p.b END AS p0,
             CASE WHEN s.orientation = 'reversed' THEN (CASE WHEN p.kind = 'sell' THEN p.b + p.base ELSE p.b - p.base END)::DOUBLE / (p.e + p.e_delta)
                  ELSE (p.e + p.e_delta)::DOUBLE / (CASE WHEN p.kind = 'sell' THEN p.b + p.base ELSE p.b - p.base END) END AS ppost,
             CASE WHEN p.token_buy THEN -1 ELSE 1 END AS sg, w.f_w, w.f_w * p.sol_amount / 1e9 AS s,
             (CASE WHEN s.orientation = 'reversed' THEN (CASE WHEN p.kind = 'sell' THEN p.b + p.base ELSE p.b - p.base END) ELSE p.e + p.e_delta END)::DOUBLE / 1e9 AS dsol0
      FROM pool p JOIN sel2 w ON w.wallet = p.trader JOIN pools s USING (pool)
      WHERE p.kind IN ('buy', 'sell') AND p.b > 0 AND p.e > 0 AND p.slot BETWEEN {test_lo} AND {test_hi}""")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE o AS
      SELECT p.pool, p.slot, (p.slot * 1000000 + p.tx_index * 100 + COALESCE(p.outer_ix, 0) * 10 + COALESCE(p.inner_ix, 0))::BIGINT AS k,
             CASE WHEN s.orientation = 'reversed' THEN p.b::DOUBLE / p.e ELSE p.e::DOUBLE / p.b END AS p,
             (CASE WHEN s.orientation = 'reversed' THEN p.b ELSE p.e END)::DOUBLE / 1e9 AS dsol
      FROM pool p SEMI JOIN (SELECT DISTINCT pool FROM wl) k ON k.pool = p.pool JOIN pools s USING (pool)
      WHERE p.kind IN ('buy', 'sell') AND p.b > 0 AND p.e > 0 AND p.slot BETWEEN {test_lo} AND {test_hi} + 400""")
    return con.execute("SELECT count(*), count(DISTINCT wallet), count(DISTINCT pool) FROM wl").fetchone(), con.execute("SELECT count(*) FROM o").fetchone()[0]


def shift(con, buckets=4):
    """sh: wl with p_k and D_k for every k (ASOF per pool: the first leg at slot >= s + k), per pool-hash bucket."""
    con.execute("CREATE OR REPLACE TEMP TABLE sh (wallet VARCHAR, pool VARCHAR, orientation VARCHAR, slot BIGINT, k0 BIGINT, kind VARCHAR, token_buy BOOLEAN, sol DOUBLE, farmer BOOLEAN, "
                "p0 DOUBLE, ppost DOUBLE, sg INTEGER, f_w DOUBLE, s DOUBLE, dsol0 DOUBLE, " + ", ".join(f"p{k} DOUBLE, d{k} DOUBLE" for k in KS) + ")")
    for b in range(buckets):
        con.execute(f"CREATE OR REPLACE TEMP TABLE wb AS SELECT * FROM wl WHERE hash(pool) % {buckets} = {b}")
        con.execute(f"CREATE OR REPLACE TEMP TABLE ob AS SELECT * FROM o WHERE hash(pool) % {buckets} = {b}")
        joins = " ".join(f"ASOF LEFT JOIN ob r{k} ON r{k}.pool = w.pool AND r{k}.k >= (w.slot + {k}) * 1000000" for k in KS)
        cols = ", ".join(f"COALESCE(r{k}.p, w.ppost) AS p{k}, COALESCE(r{k}.dsol, w.dsol0) AS d{k}" for k in KS)  # no later leg: no move, the pool's own post-depth
        con.execute(f"INSERT INTO sh SELECT w.wallet, w.pool, w.orientation, w.slot, w.k0, w.kind, w.token_buy, w.sol, w.farmer, w.p0, w.ppost, w.sg, w.f_w, w.s, w.dsol0, {cols} FROM wb w {joins}")
    return con.execute("SELECT count(*) FROM sh").fetchone()[0]


def value_sql(k, variant, fees):
    """Replica cash per leg for variant (i) / (ii) / (iii) at k, minus the fee scenario (lamports per leg)."""
    fee = f" - {fees} / 1e9"
    if variant == "i":
        return f"sg * s * p{k} / p0{fee}"
    if variant == "ii":
        return f"sg * s * p{k} / ppost{fee}"
    return f"sg * s * p{k} / ppost - s * s / greatest(COALESCE(d{k}, 1e-9), 1e-9){fee}"  # (iii): our own impact at the k-leg's depth


def day_boot(sums, boot, seed=SEED):
    """Bootstrap over the test days of the mean SOL per day: (lo, hi)."""
    x = np.asarray(sums, dtype=float)
    if len(x) < 2:
        return None, None
    rng = np.random.default_rng(seed)
    m = x[rng.integers(0, len(x), size=(boot, len(x)))].mean(axis=1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def analyse(con, boot, log=lambda m: None):
    con.execute(f"""CREATE OR REPLACE TEMP TABLE days AS SELECT slot, CAST(to_timestamp(block_time) AT TIME ZONE 'UTC' AS DATE)::VARCHAR AS day FROM blocks
                     WHERE slot IN (SELECT DISTINCT slot FROM sh)""")
    con.execute("CREATE OR REPLACE TEMP TABLE shd AS SELECT sh.*, d.day, w.rank_fit FROM sh JOIN days d USING (slot) JOIN sel2 w USING (wallet)")
    n_legs, n_w, n_days, turnover, own = con.execute("SELECT count(*), count(DISTINCT wallet), count(DISTINCT day), sum(s) FILTER (WHERE token_buy), sum(sg * sol) FROM shd").fetchone()
    res = {"rule": RULE, "input": {"legs": n_legs, "wallets": n_w, "days": n_days, "turnover_scaled_buy_sol": turnover, "own_cash_test": own,
                                   "own_cash_scaled_test": con.execute("SELECT sum(sg * s) FROM shd").fetchone()[0]}, "cells": {}}
    log(f"{n_legs:,} legs of {n_w} wallets over {n_days} days; own cash in the test half {own:+.1f} SOL; scaled buy turnover {turnover:,.0f} SOL")
    for k in KS:
        for variant in ("i", "ii", "iii"):
            for fees_name, fees in (("base", BASE_LAMPORTS), ("min", MIN_LAMPORTS)):
                for scope, where in (("all", "true"), ("without_top5", f"rank_fit > {TOP}")):
                    v = value_sql(k, variant, fees)
                    per_day = con.execute(f"SELECT day, sum({v}), count(*) FROM shd WHERE {where} GROUP BY 1 ORDER BY 1").fetchall()
                    sums = [r[1] for r in per_day]
                    lo, hi = day_boot(sums, boot)
                    tot, legs = sum(sums), sum(r[2] for r in per_day)
                    cell = {"sol_per_day": tot / max(len(sums), 1), "lo": lo, "hi": hi, "pos_days": sum(1 for x in sums if x > 0), "days": len(sums), "total": tot,
                            "legs": legs, "net_per_leg": tot / legs if legs else None, "per_day": {r[0]: r[1] for r in per_day}}
                    res["cells"][f"k{k}_{variant}_{fees_name}_{scope}"] = cell
                    if variant == "iii" and k == 3 and fees_name == "base" and lo is not None and cell["net_per_leg"] is not None:
                        log(f"k {k} ({variant}, {fees_name}, {scope}): {cell['sol_per_day']:+.2f} SOL/day [{lo:+.2f}; {hi:+.2f}], {cell['pos_days']}/{cell['days']} days > 0, net per leg {cell['net_per_leg']:+.6f}")
    p = res["cells"]["k3_iii_base_all"]
    q = res["cells"]["k3_iii_base_without_top5"]
    c1 = p["net_per_leg"] is not None and p["net_per_leg"] > 0
    c2 = p["lo"] is not None and p["lo"] > 0  # fewer than 2 days: no interval, no lead
    c3 = p["pos_days"] >= RULE["min_pos_days"]
    c4 = q["sol_per_day"] > 0
    res["verdict"] = {"outcome": "LEAD LIVES" if (c1 and c2 and c3 and c4) else "CLOSED", "net_per_leg_positive": c1, "lower_bound_positive": c2, "pos_days_ok": c3, "without_top5_positive": c4}
    res["per_wallet"] = [dict(zip(("wallet", "rank_fit", "legs", "own_cash", "primary_k3"), r)) for r in con.execute(
        f"SELECT wallet, rank_fit, count(*), sum(sg * sol), sum({value_sql(3, 'iii', BASE_LAMPORTS)}) FROM shd GROUP BY 1, 2 ORDER BY 2").fetchall()]
    return res


def markdown(res, out_dir):
    r, i, v = res["rule"], res["input"], res["verdict"]
    L = [f"## E59 wash-flow population replica ({os.path.basename(out_dir.rstrip('/'))})", "",
         f"Selection on {r['fit'][0]} .. {r['fit'][1]} (margin >= {r['margin'] * 100:.1f} %, >= {r['min_days']} farmer-pool days, >= {r['min_legs']:,} legs); replica on {r['test'][0]} .. {r['test'][1]}: "
         f"{i['legs']:,} legs of {i['wallets']} wallets over {i['days']} days; their own cash in the test half {i['own_cash_test']:+.1f} SOL (scaled {i['own_cash_scaled_test']:+.1f}); "
         f"scaled buy turnover {i['turnover_scaled_buy_sol']:,.0f} SOL; size {r['size_sol']} SOL via f_w = {r['scale']}; fees base {r['base_lamports']:,} / min {r['min_lamports']:,} lamports per leg.", "",
         f"**Outcome: {v['outcome']}** (primary = variant iii, k 3, base fees, all wallets): net per leg > 0: {v['net_per_leg_positive']}; lower bound > 0: {v['lower_bound_positive']}; "
         f">= {r['min_pos_days']} of {i['days']} days > 0: {v['pos_days_ok']}; without the top {r['top']} > 0: {v['without_top5_positive']}.", "",
         "| k | variant | fees | scope | SOL/day | 95 % day CI | days > 0 | net per leg (SOL) | legs |", "|---|---|---|---|---|---|---|---|---|"]
    for key, c in res["cells"].items():
        k, variant, fees, scope = key.split("_", 3)
        ci = f"[{c['lo']:+.2f}; {c['hi']:+.2f}]" if c["lo"] is not None else "–"
        L.append(f"| {k[1:]} | {variant} | {fees} | {scope} | {c['sol_per_day']:+.2f} | {ci} | {c['pos_days']}/{c['days']} | {c['net_per_leg']:+.6f} | {c['legs']:,} |" if c["net_per_leg"] is not None
                 else f"| {k[1:]} | {variant} | {fees} | {scope} | – | – | – | – | 0 |")
    L += ["", "Per wallet (primary k 3, variant iii, base fees):", "", "| rank (fit) | wallet | legs | own cash | primary |", "|---|---|---|---|---|"]
    for w in res["per_wallet"]:
        L.append(f"| {w['rank_fit']} | {w['wallet'][:8]}… | {w['legs']:,} | {w['own_cash']:+.1f} | {w['primary_k3']:+.1f} |")
    L.append("")
    return "\n".join(L) + "\n"


def main():
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    boot = int(os.environ.get("BOOT", "10000"))
    t0 = time.time()
    log = lambda m: print(f"{time.time() - t0:7.0f} s  {m}", file=sys.stderr, flush=True)
    con = connect(STORE, memory=os.environ.get("LAB_WALLETS_MEMORY", "1500MB"), threads=int(os.environ.get("LAB_WALLETS_THREADS", "2")))
    n_sel = select_wallets(con, MONEY)
    log(f"selected {n_sel} wallets on the fit half")
    if n_sel == 0:
        raise SystemExit("no wallet passes the fit-half selection")
    fit_lo, fit_hi, test_lo, test_hi = slot_bounds(con)
    scale_factors(con, fit_lo, fit_hi)
    con.execute(f"COPY sel2 TO '{out}/selected.parquet' (FORMAT PARQUET)")
    (n_wl, n_w, n_pools), n_o = extract(con, test_lo, test_hi)
    log(f"legs: {n_wl:,} of {n_w} wallets in {n_pools:,} pools; pool legs {n_o:,}")
    if n_wl == 0 or n_o == 0:
        raise SystemExit("empty extract")
    n_sh = shift(con, buckets=int(os.environ.get("LAB_MARKOUT_BUCKETS", "4")))
    log(f"shifted {n_sh:,} legs")
    con.execute(f"COPY sh TO '{out}/legs-shift.parquet' (FORMAT PARQUET)")
    res = analyse(con, boot, log)
    with open(f"{out}/washflow.md", "w") as f:
        f.write(markdown(res, out))
    with open(f"{out}/washflow.json", "w") as f:
        json.dump(res, f, indent=1, default=float)
    info = {"selected": n_sel, "legs": n_wl, "pool_legs": n_o, "slots": {"fit": [fit_lo, fit_hi], "test": [test_lo, test_hi]}, "store": STORE, "money": MONEY, "commit": git_commit(),
            "outcome": res["verdict"]["outcome"], "seconds": round(time.time() - t0, 1)}
    with open(f"{out}/run-washflow.json", "w") as f:
        json.dump(info, f, indent=1)
    print(json.dumps(info))


if __name__ == "__main__":
    main()
