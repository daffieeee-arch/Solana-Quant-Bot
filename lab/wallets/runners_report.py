"""Report of spec F section 1 (runners.py entries): per entry moment the distribution of mult_1h / mult_6h, the time of
the maximum, end_6h and dd_6h (per regime, per segment, days 1-9 / 10-18 / all); per feature the AUC for "x5 within
6 h" and "x10 within 6 h" with a 95 % day-bootstrap CI (seed 7) and quintile tables (share of runners and mean end_6h
per quintile); the creator's earlier tokens (count, median mult_6h at c5) joined as features. No bar: a feature with
AUC >= 0.65 in both regimes is a lead for a pre-registration of its own, nothing more.

The day bootstrap of an AUC is exact and fast through the day-pair matrix W[d1, d2] = the Mann-Whitney wins of the
positives of day d1 over the negatives of day d2 (ties count 1/2): AUC(counts c) = c W c / ((c . P)(c . N)), so 10,000
day resamples cost one matrix product each (a weighted AUC per resample over 10^5-10^6 rows did not).

  python -m wallets.runners_report --entries DIR [--boot 10000] [--out FILE]
"""

import argparse
import glob
import json
import math
import os
import sys

import duckdb
import numpy as np

from .common import OUT_ROOT

BLOCKS = (("days 10-18", "day >= '2026-09-20' AND day <= '2026-09-30'"), ("days 1-9", "day < '2026-09-20'"), ("all days", "TRUE"))
MOMENTS = ("c1", "c5", "c15", "f7", "m1", "m1e")
WINDOWS = ("all", "pre1", "pre5", "post1", "post5")
WIN_FEATS = ("inflow", "inflow_org", "buyers", "sellers", "buyers_nofarm", "medbuy", "payer_other", "rt_share")
OTHER_FEATS = ("top5_share", "holders", "payer_hub", "min_since_create", "depth_sol", "creator_prev_n", "creator_prev_mult6")
LEAD_AUC = 0.65
SEED = 7


def features():
    return [f"{w}_{f}" for w in WINDOWS for f in WIN_FEATS] + list(OTHER_FEATS)


def load(con, entries_dir):
    files = sorted(glob.glob(os.path.join(entries_dir, "entries-*.parquet")))
    if not files:
        raise SystemExit(f"no entries-*.parquet under {entries_dir}")
    con.execute(f"CREATE OR REPLACE TABLE e AS SELECT * FROM read_parquet({files!r}, union_by_name = true)")
    # the creator's earlier tokens (by create slot) and their median mult_6h at c5
    con.execute("""CREATE OR REPLACE TABLE ch AS
        WITH tk AS (SELECT DISTINCT key, creator, create_slot FROM e WHERE creator IS NOT NULL),
             c5 AS (SELECT key, mult_6h FROM e WHERE moment = 'c5')
        SELECT a.key, count(b.key) AS creator_prev_n, median(c5.mult_6h) AS creator_prev_mult6
        FROM tk a LEFT JOIN tk b ON b.creator = a.creator AND b.create_slot < a.create_slot LEFT JOIN c5 ON c5.key = b.key
        GROUP BY 1""")
    con.execute("""CREATE OR REPLACE TABLE ent AS
        SELECT e.*, ch.creator_prev_n, ch.creator_prev_mult6, mult_6h >= 5 AS run5, mult_6h >= 10 AS run10,
               dense_rank() OVER (ORDER BY day) - 1 AS day_idx
        FROM e LEFT JOIN ch USING (key)""")
    return con.execute("SELECT count(*), count(DISTINCT key), count(DISTINCT day) FROM ent").fetchone()


def auc_day_matrix(x, y, day_idx, n_days):
    """W[d1, d2], P[d], N[d] of the Mann-Whitney statistic split by the days of the positive and the negative."""
    W = np.zeros((n_days, n_days))
    P = np.bincount(day_idx[y], minlength=n_days).astype(float)
    N = np.bincount(day_idx[~y], minlength=n_days).astype(float)
    negs = [np.sort(x[(~y) & (day_idx == d)]) for d in range(n_days)]
    for d1 in range(n_days):
        pos = x[y & (day_idx == d1)]
        if len(pos) == 0:
            continue
        for d2 in range(n_days):
            neg = negs[d2]
            if len(neg) == 0:
                continue
            lo = np.searchsorted(neg, pos, "left")
            hi = np.searchsorted(neg, pos, "right")
            W[d1, d2] = (lo + 0.5 * (hi - lo)).sum()
    return W, P, N


def auc_ci(x, y, day_idx, n_days, boot, seed=SEED):
    """AUC of y against x and its 95 % day-bootstrap interval (days resampled with replacement, seed fixed)."""
    if y.sum() == 0 or (~y).sum() == 0:
        return None, None, None
    W, P, N = auc_day_matrix(x, y, day_idx, n_days)
    a = W.sum() / (P.sum() * N.sum())
    if n_days < 2 or boot <= 0:
        return float(a), None, None
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, n_days, size=(boot, n_days))
    C = np.stack([np.bincount(d, minlength=n_days) for d in draws]).astype(float)
    num = np.einsum("bi,ij,bj->b", C, W, C)
    den = (C @ P) * (C @ N)
    ok = den > 0
    v = num[ok] / den[ok]
    return float(a), float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))


def fmt(x, nd=2, pct=False):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "–"
    return f"{100 * x:.{nd}f} %" if pct else f"{x:.{nd}f}"


def distribution(con, where):
    rows = con.execute(f"""
        SELECT moment, regime, segment, count(*) AS n,
               avg((mult_1h >= 2)::INT), avg((mult_1h >= 5)::INT), avg((mult_1h >= 10)::INT),
               avg((mult_6h >= 2)::INT), avg((mult_6h >= 5)::INT), avg((mult_6h >= 10)::INT),
               median(t_max_min), quantile_cont(t_max_min, 0.9), median(end_6h), avg((end_6h >= 1)::INT), median(dd_6h),
               median(end_6h) FILTER (WHERE mult_6h >= 5), median(t_max_min) FILTER (WHERE mult_6h >= 5), avg((end_6h >= 1)::INT) FILTER (WHERE mult_6h >= 5),
               median(depth_sol), avg(cut::INT)
        FROM ent WHERE {where} GROUP BY 1, 2, 3 ORDER BY 1, 2, 3""").fetchall()
    L = ["| moment | regime | segment | n | ≥×2 1h | ≥×5 1h | ≥×10 1h | ≥×2 6h | ≥×5 6h | ≥×10 6h | t_max med / p90 (min) | end_6h med | end ≥ cost | dd med | runners ≥×5: end med / t_max med / end ≥ cost | depth med (SOL) | cut |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]:,} | {fmt(r[4], 1, True)} | {fmt(r[5], 2, True)} | {fmt(r[6], 2, True)} | {fmt(r[7], 1, True)} | {fmt(r[8], 2, True)} | {fmt(r[9], 2, True)} | "
                 f"{fmt(r[10], 1)} / {fmt(r[11], 1)} | {fmt(r[12], 3)} | {fmt(r[13], 1, True)} | {fmt(r[14], 3)} | {fmt(r[15], 2)} / {fmt(r[16], 1)} / {fmt(r[17], 0, True)} | {fmt(r[18], 2)} | {fmt(r[19], 1, True)} |")
    return L


def aucs(con, where, boot, feats, n_days):
    """Per moment x regime x feature: AUC (x5, x10 within 6 h) with CI; rows with a NULL feature are left out."""
    out = {}
    cells = con.execute(f"SELECT moment, regime, count(*) FROM ent WHERE {where} GROUP BY 1, 2 ORDER BY 1, 2").fetchall()
    for moment, regime, n in cells:
        base = con.execute(f"SELECT day_idx, run5::INT AS run5, run10::INT AS run10, {', '.join(feats)} FROM ent WHERE {where} AND moment = ? AND regime = ?", [moment, regime]).fetchnumpy()
        day_idx = base["day_idx"].astype(int)
        for f in feats:
            x = np.asarray(base[f], dtype=float)
            ok = ~np.isnan(x)
            if ok.sum() < 50:
                continue
            res = {"n": int(ok.sum())}
            for tgt in ("run5", "run10"):
                y = np.asarray(base[tgt], dtype=float)[ok] > 0.5
                a, lo, hi = auc_ci(x[ok], y, day_idx[ok], n_days, boot)
                res[tgt] = (a, lo, hi, int(y.sum()))
            out[(moment, regime, f)] = res
    return out


def auc_table(res, feats, moments, regimes):
    L = ["| feature | " + " | ".join(f"{m} {r}: ×5 AUC [CI] (n+) / ×10 AUC" for m in moments for r in regimes) + " |",
         "|---|" + "---|" * (len(moments) * len(regimes))]
    for f in feats:
        cells = []
        for m in moments:
            for r in regimes:
                v = res.get((m, r, f))
                if not v or "run5" not in v:
                    cells.append("–")
                    continue
                a5, lo5, hi5, n5 = v["run5"]
                a10 = v["run10"][0] if "run10" in v else None
                lead = a5 is not None and (a5 >= LEAD_AUC or a5 <= 1 - LEAD_AUC)
                s5 = f"**{fmt(a5)}**" if lead else fmt(a5)
                ci = f" [{fmt(lo5)} … {fmt(hi5)}]" if lo5 is not None else ""
                cells.append(f"{s5}{ci} ({n5}) / {fmt(a10)}")
        L.append(f"| {f} | " + " | ".join(cells) + " |")
    return L


def quintiles(con, where, moment, feat):
    rows = con.execute(f"""
        WITH q AS (SELECT ntile(5) OVER (ORDER BY {feat}) AS qt, run5, run10, end_6h, mult_6h FROM ent WHERE {where} AND moment = ? AND {feat} IS NOT NULL)
        SELECT qt, count(*), avg(run5::INT), avg(run10::INT), avg(end_6h), median(mult_6h) FROM q GROUP BY 1 ORDER BY 1""", [moment]).fetchall()
    return [f"| {r[0]} | {r[1]:,} | {fmt(r[2], 2, True)} | {fmt(r[3], 2, True)} | {fmt(r[4], 3)} | {fmt(r[5], 3)} |" for r in rows]


def render(con, info, boot, feats, n_days):
    moments = [m for m in MOMENTS if con.execute("SELECT count(*) FROM ent WHERE moment = ?", [m]).fetchone()[0]]
    regimes = [r[0] for r in con.execute("SELECT DISTINCT regime FROM ent ORDER BY 1").fetchall()]
    L = ["# Runners from realistic entry moments (spec F section 1)\n",
         f"Entries {info['entries']:,} on {info['tokens']:,} tokens over {info['days']} UTC days; moments {', '.join(moments)}; regimes {', '.join(regimes)}; "
         f"day bootstrap {boot:,} draws (seed {SEED}). Entry = 0.5 SOL at the state before the first trade ≥ 600 ms after the moment; mark = net SOL of a full sell "
         "(impact, venue fees, network cost, capped at the venue's real SOL); mult = mark / cost. Days 10-18 are consumed dev data: nothing here is a candidate.\n"]
    per = con.execute("SELECT moment, count(*), count(DISTINCT key), avg((segment = 'curve')::INT), avg((segment = 'pool')::INT), avg((segment = 'pool_reversed')::INT) FROM ent GROUP BY 1 ORDER BY 1").fetchall()
    L += ["## 1. Entries per moment\n", "| moment | entries | tokens | curve | pool | pool_reversed |", "|---|---|---|---|---|---|"]
    L += [f"| {m} | {n:,} | {k:,} | {fmt(c, 0, True)} | {fmt(p, 0, True)} | {fmt(r, 0, True)} |" for m, n, k, c, p, r in per]
    L.append("")
    L.append("## 2. Distribution of the multiples\n")
    for name, where in BLOCKS:
        L += [f"### {name}\n"] + distribution(con, where) + [""]
    where = BLOCKS[0][1]
    L += ["## 3. Features: AUC for ≥×5 and ≥×10 within 6 h (days 10-18)\n",
          "AUC > 0.5: a higher value goes with runners. 'all' = since the create up to the moment t; 'pre1' / 'pre5' = the 1 / 5 min before t; "
          "'post1' / 'post5' = the 1 / 5 min after the entry (a hold decision at that later point, not an entry feature). "
          f"Bold: AUC ≥ {LEAD_AUC} or ≤ {1 - LEAD_AUC:.2f}. CI = 95 % day bootstrap; (n+) = runners ≥×5 in the cell.\n"]
    res = aucs(con, where, boot, feats, n_days)
    L += auc_table(res, feats, moments, regimes) + [""]
    # leads: AUC >= 0.65 (either direction) in every regime of a moment
    leads = []
    for m in moments:
        for f in feats:
            vals = [res.get((m, r, f), {}).get("run5", (None,))[0] for r in regimes]
            if all(v is not None and (v >= LEAD_AUC or v <= 1 - LEAD_AUC) for v in vals):
                leads.append((m, f, vals))
    L.append("## 4. Leads (AUC ≥ 0.65 or ≤ 0.35 in every regime, days 10-18)\n")
    L += [f"- {m}: `{f}` AUC " + " / ".join(fmt(v) for v in vals) for m, f, vals in leads] or ["- none"]
    L.append("")
    L.append("## 5. Quintiles of the strongest features (days 10-18, all regimes)\n")
    top = {}
    for (m, r, f), v in res.items():
        a = v.get("run5", (None,))[0]
        if a is not None:
            top.setdefault(m, []).append((abs(a - 0.5), f))
    for m in moments:
        best = sorted(set(top.get(m, [])), reverse=True)[:6]
        for _, f in best:
            L += [f"**{m} · {f}**\n", "| quintile | n | ≥×5 6h | ≥×10 6h | mean end_6h | median mult_6h |", "|---|---|---|---|---|---|"] + quintiles(con, where, m, f) + [""]
    L += ["## 6. Caveats\n",
          "- Reconstruction with the cost model (base scenario; landing and failed transactions not modelled). The curve state is rebuilt from the real reserves "
          "(the store's vq is unusable on Mayhem rows); mints without an event row get no curve entry.",
          "- `mult` is the simulated full sell: on a thin venue ×10 on the mark is not ×10 in SOL (the cap on the real SOL is in the measure).",
          "- c1 / c5 / c15 are a measuring base, not a strategy; f7 / m1 / m1e are the realistic entries. share_c (transfers-v1) and the wash flags are not here.",
          "- 6 h holds cross day borders; the day is the entry's UTC day. Alpenglow (epoch 1064) is a new regime, never pooled with dev.", ""]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--entries", default=os.path.join(OUT_ROOT, "runners-v1"))
    ap.add_argument("--boot", type=int, default=10_000)
    ap.add_argument("--out")
    ap.add_argument("--memory", default=os.environ.get("LAB_WALLETS_MEMORY", "1GB"))
    a = ap.parse_args(argv)
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{a.memory}'; SET threads TO {os.environ.get('LAB_WALLETS_THREADS', '2')}")
    n, tokens, days = load(con, a.entries)
    info = {"entries": n, "tokens": tokens, "days": days, "boot": a.boot}
    feats = [f for f in features() if f in [c[0] for c in con.execute("SELECT * FROM ent LIMIT 0").description]]
    md = render(con, info, a.boot, feats, days)
    out = a.out or os.path.join(a.entries, "runners.md")
    with open(out, "w") as f:
        f.write(md)
    with open(os.path.splitext(out)[0] + ".json", "w") as f:
        json.dump(info, f, indent=1)
    print(json.dumps(info), file=sys.stderr)


if __name__ == "__main__":
    main()
