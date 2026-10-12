"""Spec B report: markout tables from markout-<run>.parquet (markout.py), per family x segment x d x
horizon and block (days 1-9, days 10-18, all), gross and net:

  n, median, mean, 1 %-trimmed mean (0.5 % each side), share > 0, and the 95 % day-bootstrap CI of
  the trimmed mean (whole UTC days resampled BOOT times, seed 7; the trimmed mean of a resample is
  the weighted trimmed mean over the globally sorted values with the days' multiplicities as weights);
  the N2 twin paired per trigger (N2 rows carry ref_mint / ref_t; a trigger's N2 value is the mean of
  its matched entries) with the paired difference and its day CI;
  K1 / K2 (F7 pool_reversed, organic pools) and K3 (all pools) as their own rows, with the first
  horizon that meets the spec's section 5 ("advantage at h": trimmed net > 0 with CI > 0 on days
  10-18 and the N2 twin without it);
  and, with --parts, the exit-rule cost per variant: realized net return per position (settle_sql,
  base, 0.5 SOL, q = 0.5) against the net markout at the horizon nearest the variant's median hold,
  on the same signals, with the paired difference and its day CI.

  python -m wallets.markout_report --markout DIR [--parts DIR --cfg FILE] [--boot 10000] [--out FILE]
"""

import argparse
import glob
import json
import math
import os
import sys

import duckdb
import numpy as np

BOOT = 10_000
TRIM = 0.005
GROSS_CAP = 10.0  # gross markouts are capped at +1000 % for the trimmed mean: a drained pool's mid runs to infinity
DEAD = -0.99  # net <= -99 %: the venue could not pay for the tokens (drained pool, completed curve)
SPLIT_DAY = "2026-09-20"
BLOCKS = (("days 1-9", f"day < '{SPLIT_DAY}'"), ("days 10-18", f"day >= '{SPLIT_DAY}'"), ("all days", "TRUE"))
H_ORDER = ("1s", "10s", "1m", "5m", "15m", "1h", "6h")
K_ROWS = (("K1/K2 (F7 pool_reversed, organic)", "family = 'F7' AND segment = 'pool_reversed' AND farm = 'organic'"),
          ("K3 (F7 pool_reversed, all pools)", "family = 'F7' AND segment = 'pool_reversed'"))


def trimmed_mean(x, trim=TRIM):
    x = np.sort(np.asarray(x, dtype=float))
    n = len(x)
    k = int(math.floor(n * trim))
    return float(x[k:n - k].mean()) if n - 2 * k > 0 else float("nan")


def weighted_trimmed_mean(xs, w, trim=TRIM):
    """Trimmed mean of the sample where sorted value xs[i] occurs w[i] times (w >= 0), trimming `trim`
    of the total weight at each end (the boundary values enter with their remaining fraction)."""
    total = w.sum()
    if total <= 0:
        return float("nan")
    cut = total * trim
    cum = np.cumsum(w)
    lo_w = np.clip(cum - cut, 0, w)  # weight kept at the low end
    hi_w = np.clip(total - cut - (cum - w), 0, w)  # weight kept at the high end
    keep = np.minimum(lo_w, hi_w)
    kw = keep.sum()
    return float((xs * keep).sum() / kw) if kw > 0 else float("nan")


def day_ci(values, days, boot=BOOT, seed=7):
    """95 % CI of the trimmed mean over a day bootstrap; (None, None) with fewer than 2 days."""
    values = np.asarray(values, dtype=float)
    days = np.asarray(days)
    uniq, inv = np.unique(days, return_inverse=True)
    if len(uniq) < 2 or len(values) == 0:
        return None, None
    order = np.argsort(values, kind="stable")
    xs, di = values[order], inv[order]
    rng = np.random.default_rng(seed)
    counts = rng.multinomial(len(uniq), np.full(len(uniq), 1 / len(uniq)), size=boot)
    out = np.empty(boot)
    for b in range(boot):
        out[b] = weighted_trimmed_mean(xs, counts[b][di].astype(float))
    out = out[~np.isnan(out)]
    if len(out) == 0:
        return None, None
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


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


def load(con, markout_dir):
    files = sorted(glob.glob(os.path.join(markout_dir, "markout-*.parquet")))
    if not files:
        raise SystemExit(f"no markout-*.parquet in {markout_dir}")
    lit = "[" + ",".join(f"'{f}'" for f in files) + "]"
    con.execute(f"CREATE OR REPLACE TABLE m AS SELECT * FROM read_parquet({lit}, union_by_name = true) WHERE h <> ''")
    con.execute("""
      CREATE OR REPLACE TABLE pairs AS
      SELECT t.family, t.segment, t.mint, t.t, t.d, t.h, t.day, t.farm, t.gross, t.net,
             n.n2_gross, n.n2_net, n.n_n2
      FROM m t JOIN (
        SELECT substr(family, 4) AS family, ref_mint, ref_t, d, h, avg(gross) AS n2_gross, avg(net) AS n2_net, count(*) AS n_n2
        FROM m WHERE family LIKE 'N2\\_%' ESCAPE '\\' AND ref_mint IS NOT NULL GROUP BY ALL) n
      ON n.family = t.family AND n.ref_mint = t.mint AND n.ref_t = t.t AND n.d = t.d AND n.h = t.h
      WHERE t.family NOT LIKE 'N2\\_%' ESCAPE '\\'""")
    return len(files)


def stats(con, where, col, boot):
    rows = con.execute(f"SELECT {col}, day FROM m WHERE {where} AND {col} IS NOT NULL").fetchall()
    if not rows:
        return None
    v = np.array([r[0] for r in rows], dtype=float)
    if col == "gross":
        v = np.minimum(v, GROSS_CAP)
    days = np.array([r[1] for r in rows])
    lo, hi = day_ci(v, days, boot)
    return {"n": len(v), "median": float(np.median(v)), "mean": float(v.mean()), "trim": trimmed_mean(v), "lo": lo, "hi": hi,
            "pos": float((v > 0).mean()), "dead": float((v <= DEAD).mean()),
            "cut": con.execute(f"SELECT avg(cut::INT) FROM m WHERE {where}").fetchone()[0]}


def paired(con, where, col, boot):
    rows = con.execute(f"SELECT {col} - n2_{col}, n2_{col}, day FROM pairs WHERE {where} AND {col} IS NOT NULL AND n2_{col} IS NOT NULL").fetchall()
    if not rows:
        return None
    d = np.array([r[0] for r in rows], dtype=float)
    n2 = np.array([r[1] for r in rows], dtype=float)
    days = np.array([r[2] for r in rows])
    lo, hi = day_ci(d, days, boot)
    return {"n": len(d), "n2_trim": trimmed_mean(n2), "diff": trimmed_mean(d), "lo": lo, "hi": hi}


def groups(con):
    return con.execute("SELECT DISTINCT family, segment, d FROM m WHERE family NOT LIKE 'N2\\_%' ESCAPE '\\' ORDER BY 1, 2, 3").fetchall()


def table(con, label, where, boot, blocks=BLOCKS):
    L = [f"### {label}\n",
         "| block | h | n | cut | dead | gross median | gross trim (cap 10×) | net median | net mean | **net trim** | 95% CI (days) | > 0 | N2 net trim (paired) | Δ vs N2 | 95% CI | advantage |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    verdicts = {}
    for bname, bwhere in blocks:
        for h in H_ORDER:
            w = f"({where}) AND h = '{h}' AND ({bwhere})"
            g = stats(con, w, "gross", boot)
            n = stats(con, w, "net", boot)
            p = paired(con, w, "net", boot)
            if not n:
                continue
            adv = (n["trim"] > 0 and n["lo"] is not None and n["lo"] > 0
                   and (p is None or not (p["n2_trim"] > 0 and p["diff"] is not None)))
            if p is not None and n["trim"] > 0 and n["lo"] is not None and n["lo"] > 0:
                adv = p["n2_trim"] <= 0 or p["lo"] is None or p["lo"] > 0  # N2 not positive, or the trigger clearly above it
            if bname == "days 10-18":
                verdicts[h] = adv
            L.append(f"| {bname} | {h} | {fmt(n['n'])} | {fmt(n['cut'], 0, pct=True)} | {fmt(n['dead'], 0, pct=True)} | {fmt(g['median'], 1, True, True)} | {fmt(g['trim'], 1, True, True)} | "
                     f"{fmt(n['median'], 1, True, True)} | {fmt(n['mean'], 1, True, True)} | **{fmt(n['trim'], 1, True, True)}** | "
                     f"{fmt(n['lo'], 1, True, True)} … {fmt(n['hi'], 1, True, True)} | {fmt(n['pos'], 0, True)} | "
                     + (f"{fmt(p['n2_trim'], 1, True, True)} ({fmt(p['n'])}) | {fmt(p['diff'], 1, True, True)} | {fmt(p['lo'], 1, True, True)} … {fmt(p['hi'], 1, True, True)} |" if p else "– | – | – |")
                     + (f" **{'yes' if adv else 'no'}** |" if bname == "days 10-18" else " |"))
    first = next((h for h in H_ORDER if verdicts.get(h)), None)
    L.append("")
    L.append(f"Days 10-18 verdict (spec section 5): " + (f"advantage first at **{first}**" if first else "no horizon with an advantage") + ".")
    L.append("")
    return "\n".join(L)


def exit_costs(con, parts_dir, cfg_path, boot, ms_per_slot):
    """Realized net per position (variant, d, 0.5 SOL, q 0.5) against the net markout at the horizon
    nearest the variant's median hold, on the same signals."""
    from wallets.markout import engine
    import yaml
    _, costs = engine()
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    _, ret = costs.settle_sql(costs.Scenario.from_cfg("base", cfg))
    files = sorted(glob.glob(os.path.join(parts_dir, "*.parquet")))
    lit = "[" + ",".join(f"'{f}'" for f in files) + "]"
    con.execute(f"""
      CREATE OR REPLACE TABLE pos AS
      SELECT family, variant, segment, mint, t, d, day, {ret} AS ret, (exit_slot - entry_slot) AS hold_slots
      FROM read_parquet({lit}, union_by_name = true)
      WHERE NOT skipped AND NOT entry_failed AND size_sol = 0.5 AND q = 0.5 AND family NOT LIKE 'N2\\_%' ESCAPE '\\'""")
    rows = con.execute("SELECT family, variant, segment, d, median(hold_slots), count(*) FROM pos GROUP BY ALL ORDER BY ALL").fetchall()
    L = ["### Exit-rule cost: realized net per position vs the net markout at the median hold (0.5 SOL, base, q = 0.5)\n",
         "| family | variant | segment | d | positions | median hold | nearest h | realized net (trim) | markout net (trim) | Δ realized − markout | 95% CI (days) | best h afterwards (trim) |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    hs = dict(zip(H_ORDER, (1_000, 10_000, 60_000, 300_000, 900_000, 3_600_000, 21_600_000)))
    for fam, var, seg, d, hold, n in rows:
        hold_ms = (hold or 0) * ms_per_slot
        h = min(H_ORDER, key=lambda k: abs(hs[k] - hold_ms))
        pr = con.execute(f"""
          SELECT p.ret - m.net, p.ret, m.net, p.day FROM pos p JOIN m ON m.family = p.family AND m.segment = p.segment AND m.mint = p.mint AND m.t = p.t AND m.d = p.d
          WHERE p.family = ? AND p.variant = ? AND p.segment = ? AND p.d = ? AND m.h = ? AND m.net IS NOT NULL""", [fam, var, seg, d, h]).fetchall()
        if not pr:
            continue
        diff = np.array([r[0] for r in pr]); re = np.array([r[1] for r in pr]); mk = np.array([r[2] for r in pr]); days = np.array([r[3] for r in pr])
        lo, hi = day_ci(diff, days, boot)
        best = con.execute(f"""
          SELECT h, net FROM (SELECT m.h, m.net FROM pos p JOIN m ON m.family = p.family AND m.segment = p.segment AND m.mint = p.mint AND m.t = p.t AND m.d = p.d
                              WHERE p.family = ? AND p.variant = ? AND p.segment = ? AND p.d = ? AND m.net IS NOT NULL)""", [fam, var, seg, d]).fetchall()
        by_h = {}
        for hh, v in best:
            by_h.setdefault(hh, []).append(v)
        bh = max(by_h, key=lambda k: trimmed_mean(by_h[k])) if by_h else None
        L.append(f"| {fam} | {var} | {seg} | {d} | {fmt(len(pr))} | {fmt(hold_ms / 1000, 0)} s | {h} | {fmt(trimmed_mean(re), 1, True, True)} | "
                 f"{fmt(trimmed_mean(mk), 1, True, True)} | **{fmt(trimmed_mean(diff), 1, True, True)}** | {fmt(lo, 1, True, True)} … {fmt(hi, 1, True, True)} | "
                 f"{bh}: {fmt(trimmed_mean(by_h[bh]), 1, True, True) if bh else '–'} |")
    L.append("")
    return "\n".join(L)


def render(con, markout_dir, boot, parts_dir=None, cfg_path=None, ms_per_slot=267.0):
    runs = sorted(glob.glob(os.path.join(markout_dir, "run-markout-*.json")))
    infos = [json.load(open(r)) for r in runs]
    L = [f"## Markout curves ({', '.join(i['run'] for i in infos)})\n",
         f"Signals {sum(i['signals'] for i in infos):,}; 0.5 SOL round trip at base costs; gross = mid_h / mid_entry − 1, net = SOL back / (0.5 + 2 × order cost) − 1; "
         f"trimmed = 1 % trimmed mean; CI = day bootstrap of the trimmed mean ({boot:,}×); N2 paired per trigger (mean of its matched entries); "
         "cut = share of rows past the store end or the venue's last leg (last known state); dead = share with net ≤ −99 % (the venue cannot pay: drained pool, completed curve); "
         "gross is capped at +1000 % for the trimmed mean. Advantage (days 10-18, spec §5): net trimmed > 0 with CI > 0 and the N2 twin not.\n"]
    for fam, seg, d in groups(con):
        L.append(table(con, f"{fam} · {seg} · d = {d}", f"family = '{fam}' AND segment = '{seg}' AND d = {d}", boot))
    for label, where in K_ROWS:
        for d in (1, 2):
            if con.execute(f"SELECT count(*) FROM m WHERE {where} AND d = {d}").fetchone()[0]:
                L.append(table(con, f"{label} · d = {d}", f"{where} AND d = {d}", boot))
    if parts_dir:
        L.append(exit_costs(con, parts_dir, cfg_path, boot, ms_per_slot))
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--markout", required=True)
    ap.add_argument("--parts")
    ap.add_argument("--cfg", default=os.environ.get("LAB_DEFAULTS", "/home/chupa/Solana-project/data-old-faithful-one/lab/research/week2-defaults.yaml"))
    ap.add_argument("--ms-per-slot", type=float, default=267.0, help="for the median hold in the exit-cost table")
    ap.add_argument("--boot", type=int, default=BOOT)
    ap.add_argument("--out")
    a = ap.parse_args()
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{os.environ.get('LAB_WALLETS_MEMORY', '1500MB')}'; SET threads TO 2")
    load(con, a.markout)
    md = render(con, a.markout, a.boot, a.parts, a.cfg, a.ms_per_slot)
    if a.out:
        with open(a.out, "w") as f:
            f.write(md + "\n")
        print(a.out, file=sys.stderr)
    else:
        print(md)


if __name__ == "__main__":
    main()
