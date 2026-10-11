"""Walvis stap 0, veto: what a veto on cluster activity does to F7 and its N2 control, net (#167).

Reads the positions of a backtest run (lab/backtest tournament output), the cluster events and
members of clusters.py, and settles every position with base costs (backtest.costs.settle_sql).
A vetoed position counts as 0 P&L, as in report.overlay_effects, so the difference per position
is paired: delta = mean(P&L with veto) - mean(P&L without) = -(sum of vetoed P&L) / positions.

Variants, fixed before any outcome was looked at. t is the decision slot (the trigger); a window
includes slot t itself, because whole slots <= t are known at the decision:

  K1  a cluster event on the same token (curve mint or pool) in [t - 60 s, t]
  K2  a cluster event on the same token in [t - 10 min, t]
  K3  the trigger seller was a member (buyer or paying wallet) of a cluster that bought this
      token in [t - 24 h, t). Only F7 has a trigger seller, so K3 has no N2 counterpart.

A position is evaluated for a variant only when the variant's whole look-back window lies inside
the range the cluster run covered. Day-bootstrap CIs (10,000 resamples) as the stop rule.

  python -m wallets.veto --run DIR --clusters DIR --from-slot A --to-slot B [--out DIR]
"""

import argparse
import glob
import json
import os

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from backtest.costs import Scenario, settle_sql

from .common import OUT_ROOT, Clock, connect, git_commit, guard

DEFAULTS = os.environ.get("LAB_DEFAULTS", "/home/chupa/Solana-project/data-old-faithful-one/lab/research/week2-defaults.yaml")
VARIANTS = {"K1": 60_000, "K2": 600_000, "K3": 86_400_000}  # look-back in ms
GATE_SIZES = (0.5, 1.0)
BOOT = 10_000


def boot_mean(sums, cnts, n=BOOT, seed=7):
    """Day-clustered bootstrap of sum/count: 95% (lo, hi); None with fewer than 2 days."""
    if len(cnts) < 2 or cnts.sum() < 2:
        return None, None
    pick = np.random.default_rng(seed).integers(0, len(cnts), size=(n, len(cnts)))
    with np.errstate(invalid="ignore", divide="ignore"):
        m = sums[pick].sum(1) / cnts[pick].sum(1)
    return float(np.nanpercentile(m, 2.5)), float(np.nanpercentile(m, 97.5))


def positions_sql(run_dir):
    files = sorted(glob.glob(os.path.join(run_dir, "parts", "*.parquet")))
    if not files:
        files = [os.path.join(run_dir, "results.parquet")]
    lit = "[" + ",".join(f"'{f}'" for f in files) + "]"
    return f"read_parquet({lit}, union_by_name = true)"


def evaluate(con, run_dir, clusters_dir, lo, hi, cfg, clock):
    """Per-day sums for every (variant, family, segment, farm, strategy variant, size, d, grad-in-create-slot)."""
    pnl, _ = settle_sql(Scenario.from_cfg("base", cfg))
    src = positions_sql(run_dir)
    have = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()}
    farm = "farm" if "farm" in have else "'n/a'"
    trig = "trigger_trader" if "trigger_trader" in have else "NULL::VARCHAR"
    # Whole look-back windows must lie inside [lo, hi): positions with t in [lo + w, hi).
    con.execute("CREATE OR REPLACE TEMP TABLE ep (epoch BIGINT, ms DOUBLE)")
    con.executemany("INSERT INTO ep VALUES (?, ?)", sorted(clock.per_epoch.items()))
    wins = ", ".join(f"GREATEST(1, round({ms} / COALESCE(ep.ms, 267.3)))::BIGINT AS w_{k}" for k, ms in VARIANTS.items())
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE pos AS
      SELECT p.family, p.segment, {farm} AS farm, p.variant, p.size_sol, p.d, p.day, p.venue,
             CASE WHEN p.venue = 'curve' THEN p.mint ELSE p.pool END AS key, p.mint, p.t::BIGINT AS t,
             {trig} AS seller, {pnl} AS pnl
      FROM {src} p
      WHERE p.t >= {lo} AND p.t < {hi} AND (p.family = 'F7' OR p.family = 'N2_F7') AND NOT p.skipped""")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE trig AS
      SELECT DISTINCT venue, key, t, seller, {wins} FROM pos LEFT JOIN ep ON ep.epoch = t // 432000""")
    con.execute(f"CREATE OR REPLACE TEMP TABLE cev AS SELECT venue, key, slot FROM "
                f"read_parquet('{os.path.join(clusters_dir, 'events-*.parquet')}')")
    con.execute(f"CREATE OR REPLACE TEMP TABLE cmem AS SELECT DISTINCT venue, key, slot, wallet FROM "
                f"read_parquet('{os.path.join(clusters_dir, 'members-*.parquet')}')")
    flags = []
    for k in ("K1", "K2"):
        flags.append(f"""(CASE WHEN t - w_{k} < {lo} THEN NULL ELSE EXISTS (
            SELECT 1 FROM cev e WHERE e.venue = trig.venue AND e.key = trig.key
              AND e.slot BETWEEN trig.t - trig.w_{k} AND trig.t) END) AS {k}""")
    flags.append(f"""(CASE WHEN seller IS NULL OR t - w_K3 < {lo} THEN NULL ELSE EXISTS (
        SELECT 1 FROM cmem m WHERE m.venue = trig.venue AND m.key = trig.key
          AND m.wallet = trig.seller AND m.slot >= trig.t - trig.w_K3 AND m.slot < trig.t) END) AS K3""")
    con.execute(f"CREATE OR REPLACE TEMP TABLE tflag AS SELECT venue, key, t, seller, {', '.join(flags)} FROM trig")
    # Tokens that graduated in their create slot (56% of graduations; never tradable): report apart.
    con.execute("CREATE OR REPLACE TEMP TABLE cg AS SELECT mint FROM mints WHERE complete_slot = create_slot")
    out = []
    for k in VARIANTS:
        out += con.execute(f"""
          SELECT '{k}' AS veto, p.family, p.segment, p.farm, p.variant, p.size_sol, p.d,
                 p.mint IN (SELECT mint FROM cg) AS grad_create_slot, p.day,
                 count(*) AS n, sum(p.pnl) / 1e9 AS pnl, sum(p.pnl) FILTER (WHERE f.{k}) / 1e9 AS vetoed_pnl,
                 count(*) FILTER (WHERE f.{k}) AS vetoed
          FROM pos p JOIN tflag f ON f.venue = p.venue AND f.key = p.key AND f.t = p.t
                                 AND f.seller IS NOT DISTINCT FROM p.seller
          WHERE f.{k} IS NOT NULL GROUP BY ALL""").fetchall()
    cols = ["veto", "family", "segment", "farm", "variant", "size_sol", "d", "grad_create_slot", "day", "n", "pnl",
            "vetoed_pnl", "vetoed"]
    return [dict(zip(cols, r)) for r in out]


def summarize(daily, by=("veto", "family", "segment", "farm", "size_sol", "d")):
    """Delta per position (SOL) with a day-bootstrap CI, pooled over the strategy variants."""
    groups = {}
    for r in daily:
        g = groups.setdefault(tuple(r[k] for k in by), {})
        s, n, v, vp = g.get(r["day"], (0.0, 0, 0, 0.0))
        g[r["day"]] = (s - (r["vetoed_pnl"] or 0.0), n + r["n"], v + r["vetoed"], vp + (r["pnl"] or 0.0))
    out = []
    for key, days in sorted(groups.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        ds = sorted(days)
        delta = np.array([days[d][0] for d in ds])
        n = np.array([days[d][1] for d in ds], dtype=float)
        vetoed = sum(days[d][2] for d in ds)
        base = sum(days[d][3] for d in ds)
        lo, hi = boot_mean(delta, n)
        out.append(dict(zip(by, key), days=len(ds), positions=int(n.sum()), veto_share=vetoed / n.sum(),
                        base_sol=base / n.sum(), delta_sol=float(delta.sum() / n.sum()), ci_lo=lo, ci_hi=hi,
                        per_day=[float(days[d][0] / days[d][1]) for d in ds]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="backtest run directory (parts/ or results.parquet)")
    ap.add_argument("--clusters", required=True, help="clusters.py output directory")
    ap.add_argument("--from-slot", type=int, required=True, help="first slot the cluster run covered")
    ap.add_argument("--to-slot", type=int, required=True, help="end (exclusive) of the cluster run")
    ap.add_argument("--out", default=os.path.join(OUT_ROOT, "veto-v1"))
    a = ap.parse_args()
    guard(a.to_slot)
    with open(DEFAULTS) as f:
        cfg = yaml.safe_load(f)
    con = connect()
    clock = Clock.from_store(con)
    daily = evaluate(con, a.run, a.clusters, a.from_slot, a.to_slot, cfg, clock)
    name = os.path.basename(os.path.normpath(a.run))
    os.makedirs(a.out, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(daily), os.path.join(a.out, f"daily-{name}.parquet"))
    summ = summarize(daily) + summarize(daily, by=("veto", "family", "segment", "farm", "size_sol", "d", "grad_create_slot"))
    with open(os.path.join(a.out, f"summary-{name}.json"), "w") as f:
        json.dump({"run": a.run, "clusters": a.clusters, "from_slot": a.from_slot, "to_slot": a.to_slot,
                   "commit": git_commit(), "variants_ms": VARIANTS, "summary": summ}, f, indent=1)
    for s in summ:
        if s["size_sol"] in GATE_SIZES and "grad_create_slot" not in s:
            ci = f"[{s['ci_lo']:+.4f}, {s['ci_hi']:+.4f}]" if s["ci_lo"] is not None else "n/a"
            print(f"{s['veto']} {s['family']:6} {s['segment']:14} {s['farm']:8} {s['size_sol']:4} d={s['d']} "
                  f"n={s['positions']:7} veto={s['veto_share']:6.1%} base={s['base_sol']:+.4f} "
                  f"delta={s['delta_sol']:+.5f} {ci}")


if __name__ == "__main__":
    main()
