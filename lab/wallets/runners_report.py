"""Report for onderzoek 1b (runners.py output): lifts per feature, a logistic baseline, examples.

  python -m wallets.runners_report --runners DIR [--decision t5|t15] [--target runner10|runner3]

- Base rate per outcome, with graduations in the create slot apart.
- Per feature: runner rate per quintile of the feature (lift = rate / base) at the decision moment.
- Logistic baseline, walk-forward per day: fit on all earlier days (ridge, standardised features,
  Newton steps), predict the day; AUC and the runner rate in the top decile of the predictions.
- Examples: tokens with the strongest hype signals (buyers, organic flow) that did not run, next
  to runners with the same signals.
"""

import argparse
import glob
import math
import os

import duckdb
import numpy as np

FEATURES = ["curve_minutes", "buyers", "big_share", "inflow_sol", "creator_sells", "insider_share", "insider_sold_share",
            "pool_trades", "pool_traders", "organic_sol", "net_sol", "px_mult", "max_mult_so_far", "split_events"]


def load(runners_dir):
    con = duckdb.connect()
    con.execute("SET memory_limit = '500MB'; SET threads TO 2")
    files = sorted(glob.glob(os.path.join(runners_dir, "runners-4*.parquet")))
    con.execute(f"CREATE TABLE r AS SELECT * FROM read_parquet({files!r}, union_by_name = true)")
    return con


def base_rates(con):
    return con.execute("""
      SELECT grad_in_create, count(*) AS n, avg(runner3::INT) AS r3, avg(runner10::INT) AS r10,
             median(max_mult) AS med_max, median(end_mult) AS med_end, avg((end_mult >= 3)::INT) AS end3,
             count(*) FILTER (WHERE max_mult IS NULL) AS no_trades
      FROM r GROUP BY 1 ORDER BY 1""").fetchall()


def target_col(decision, target):
    """Open-based targets (runner3/runner10) are one column; forward targets (fwd3/fwd10) are per decision."""
    return f"{decision}_{target}" if target.startswith("fwd") else target


def lifts(con, decision, target):
    out = []
    target = target_col(decision, target)
    for f in FEATURES:
        col = f"{decision}_{f}"
        rows = con.execute(f"""
          WITH x AS (SELECT {col} AS v, {target}::INT AS y FROM r WHERE NOT grad_in_create AND {col} IS NOT NULL),
          q AS (SELECT v, y, ntile(5) OVER (ORDER BY v) AS q5 FROM x)
          SELECT q5, count(*), min(v), max(v), avg(y) FROM q GROUP BY 1 ORDER BY 1""").fetchall()
        base = con.execute(f"SELECT avg({target}::INT) FROM r WHERE NOT grad_in_create AND {col} IS NOT NULL").fetchone()[0]
        out.append((f, base, rows))
    return out


def auc(y, s):
    """Mann-Whitney AUC with average ranks for ties."""
    s = np.asarray(s, dtype=float)
    order = np.argsort(s, kind="stable")
    ranks = np.empty(len(s))
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and s[order[j + 1]] == s[order[i]]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    n1, n0 = y.sum(), len(y) - y.sum()
    if n1 == 0 or n0 == 0:
        return float("nan")
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def fit_logistic(X, y, l2=1.0, steps=25):
    """Ridge logistic regression by Newton's method on standardised features (intercept unpenalised)."""
    n, k = X.shape
    w = np.zeros(k + 1)
    Xb = np.hstack([np.ones((n, 1)), X])
    reg = np.eye(k + 1) * l2
    reg[0, 0] = 0
    for _ in range(steps):
        p = 1 / (1 + np.exp(-Xb @ w))
        g = Xb.T @ (p - y) + reg @ w
        H = (Xb * (p * (1 - p))[:, None]).T @ Xb + reg
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-6:
            break
    return w


def walk_forward(con, decision, target):
    target = target_col(decision, target)
    cols = [f"{decision}_{f}" for f in FEATURES]
    rows = con.execute(f"SELECT day, {', '.join(cols)}, {target}::INT FROM r WHERE NOT grad_in_create ORDER BY day").fetchall()
    days = sorted({r[0] for r in rows})
    X = np.array([[float(v) if v is not None else np.nan for v in r[1:-1]] for r in rows])
    y = np.array([r[-1] for r in rows], dtype=float)
    d = np.array([r[0] for r in rows])
    X = np.log1p(np.clip(np.nan_to_num(X, nan=0.0), 0, None))  # heavy-tailed counts and sums
    out = []
    for day in days[1:]:
        tr, te = d < day, d == day
        if y[tr].sum() < 5 or te.sum() < 20:
            continue
        mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-9
        w = fit_logistic((X[tr] - mu) / sd, y[tr])
        s = np.hstack([np.ones((te.sum(), 1)), (X[te] - mu) / sd]) @ w
        top = s >= np.quantile(s, 0.9)
        out.append({"day": day, "n": int(te.sum()), "runners": int(y[te].sum()), "auc": auc(y[te], s),
                    "base": float(y[te].mean()), "top_decile_rate": float(y[te][top].mean()),
                    "weights": dict(zip(FEATURES, [round(float(x), 2) for x in w[1:]]))})
    return out


def examples(con, decision, target, n=5):
    target = target_col(decision, target)
    hype = f"{decision}_buyers DESC, {decision}_organic_sol DESC"
    cols = f"symbol, name, mint, day, {decision}_buyers AS buyers, {decision}_organic_sol AS organic_sol, " \
           f"{decision}_insider_share AS insider_share, {decision}_px_mult AS px_mult, {decision}_fwd_max_mult, {decision}_fwd_end_mult"
    runners = con.execute(f"SELECT {cols} FROM r WHERE NOT grad_in_create AND {target} ORDER BY {hype} LIMIT {n}").fetchall()
    duds = con.execute(f"SELECT {cols} FROM r WHERE NOT grad_in_create AND NOT {target} AND max_mult IS NOT NULL "
                       f"ORDER BY {hype} LIMIT {n}").fetchall()
    return runners, duds


def fmt(x, p=2):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return f"{x:.{p}f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runners", required=True)
    ap.add_argument("--decision", default="t5")
    ap.add_argument("--target", default="runner10")
    a = ap.parse_args()
    con = load(a.runners)
    print("## Base rates\n")
    print("| graduated in create slot | graduations | ≥3× in 24 h | ≥10× | median max × | median end × | end ≥3× | no pool trades |")
    print("|---|---|---|---|---|---|---|---|")
    for g, n, r3, r10, mm, me, e3, nt in base_rates(con):
        print(f"| {'yes' if g else 'no'} | {n} | {r3:.1%} | {r10:.1%} | {fmt(mm)} | {fmt(me)} | {e3:.1%} | {nt} |")
    print("\nForward from the decision moment (graduations outside the create slot):\n")
    print("| decision | ≥3× after | ≥10× after | median max × after | median end × after |")
    print("|---|---|---|---|---|")
    for dec in ("t5", "t15"):
        r = con.execute(f"SELECT avg({dec}_fwd3::INT), avg({dec}_fwd10::INT), median({dec}_fwd_max_mult), median({dec}_fwd_end_mult) "
                        f"FROM r WHERE NOT grad_in_create AND {dec}_fwd_max_mult IS NOT NULL").fetchone()
        print(f"| {dec} | {r[0]:.1%} | {r[1]:.1%} | {fmt(r[2])} | {fmt(r[3])} |")
    print(f"\n## Lift per feature at {a.decision}, target {a.target} (graduations outside the create slot)\n")
    print("| feature | base | Q1 (low) | Q2 | Q3 | Q4 | Q5 (high) | Q5 range |")
    print("|---|---|---|---|---|---|---|---|")
    for f, base, rows in lifts(con, a.decision, a.target):
        cells = " | ".join(f"{r[4]:.1%} ({r[4] / base:.1f}×)" if base else f"{r[4]:.1%}" for r in rows)
        top = rows[-1] if rows else None
        print(f"| {f} | {base:.1%} | {cells} | {fmt(top[2])}–{fmt(top[3])} |" if top else f"| {f} | n/a |")
    print(f"\n## Logistic baseline, walk-forward per day ({a.decision}, {a.target})\n")
    print("| day | graduations | runners | AUC | base | top-decile rate |")
    print("|---|---|---|---|---|---|")
    wf = walk_forward(con, a.decision, a.target)
    for r in wf:
        print(f"| {r['day']} | {r['n']} | {r['runners']} | {fmt(r['auc'])} | {r['base']:.1%} | {r['top_decile_rate']:.1%} |")
    if wf:
        print(f"\nLast fit weights (standardised log1p features): {wf[-1]['weights']}")
    print(f"\n## Examples: strongest hype signals at {a.decision}\n")
    runners, duds = examples(con, a.decision, a.target)
    print("| outcome | symbol | day | buyers | organic SOL | insider share | price × at decision | max × after decision | end × after decision |")
    print("|---|---|---|---|---|---|---|---|---|")
    for label, rows in (("runner", runners), ("no runner", duds)):
        for sym, name, mint, day, b, org, ins, px, mx, en in rows:
            print(f"| {label} | {sym} | {day} | {b} | {fmt(org, 0)} | {fmt(ins)} | {fmt(px)} | {fmt(mx, 1)} | {fmt(en)} |")


if __name__ == "__main__":
    main()
