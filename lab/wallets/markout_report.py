"""Spec B report: markout tables from markout-<run>.parquet (markout.py), per family x segment x d x
horizon and block (days 1-9, days 10-18, all), gross and net:

  n, median, mean, 1 %-trimmed mean (0.5 % each side), share > 0, and the 95 % day-bootstrap CI of
  the trimmed mean (whole UTC days resampled BOOT times, seed 7; the trimmed mean of a resample is
  the weighted trimmed mean over the globally sorted values with the days' multiplicities as weights);
  the N2 twin paired per trigger (N2 rows carry ref_mint / ref_t, the trigger's venue key, i.e. its pool
  for pool signals and its mint for curve signals; a trigger's N2 value is the mean of its matched
  entries) with the paired difference and its day CI, informative only;
  K1 / K2 (F7 pool_reversed, organic pools) and K3 (all pools) as their own rows, with the first
  horizon that meets the spec's section 5 ("advantage at h": trimmed net > 0 with CI > 0 on days
  10-18 and the N2 twin without it);
  and, with --parts, the exit-rule cost per variant: realized net return per position (settle_sql,
  base, 0.5 SOL, q = 0.5) against the net markout at the horizon nearest the variant's median hold,
  on the same signals, with the paired difference and its day CI;
  and, when the rows carry the --tx-delays codes (markout.TX_DELAYS, or d = 0 / 3), the entry-delay
  comparison of spec M-a: per code, paired per signal against the reference delay REF_D (d = 2, the
  paper's d_ms 600 in the 266-316 ms regimes of the dev days): the entry mid advantage (mid_ref /
  mid_code - 1, > 0 = a cheaper entry) and the net markout difference per horizon, trimmed means with
  day CIs, the coverage (signals with both entries) and the share of code entries capped at the slot's
  last leg; the screen verdict (coordinator's rule, 2026-10-19) on days 10-18: a causal code (0 or
  2001) with net Δ >= SCREEN_PT and its day CI above 0 at h = 1m, 5m or 15m. k-codes (1xxx) need a
  same-slot fill and are shown as an upper bound only.

  python -m wallets.markout_report --markout DIR [--parts DIR --cfg FILE] [--boot 10000] [--out FILE]
"""

import argparse
import glob
import hashlib
import json
import math
import os
import sys

import duckdb
import numpy as np

from wallets import markout
from wallets.common import REF_MS

BOOT = 10_000
TRIM = 0.005
GROSS_CAP = 10.0  # gross markouts are capped at +1000 % for the trimmed mean: a drained pool's mid runs to infinity
DEAD = -0.99  # net <= -99 %: the venue could not pay for the tokens (drained pool, completed curve)
SPLIT_DAY, LAST_DAY = "2026-09-20", "2026-09-30"  # the binding window of the M1x report: 2026-09-20 … 2026-09-30
BINDING = f"days 10-18 ({SPLIT_DAY} … {LAST_DAY[5:]})"
BLOCKS = (("days 1-9", f"day < '{SPLIT_DAY}'"), (BINDING, f"day BETWEEN '{SPLIT_DAY}' AND '{LAST_DAY}'"), ("all days", "TRUE"))


def blocks(con):
    """The fixed day blocks plus one block per slot-time regime found in the rows (labels from the store meta)."""
    regs = [r[0] for r in con.execute("SELECT DISTINCT regime FROM m WHERE regime IS NOT NULL ORDER BY 1").fetchall()]
    return BLOCKS + tuple((r, f"regime = '{r}'") for r in regs)
DEDUP = "family, segment, venue, mint, pool, t, d, h"  # the same trigger appears in more than one run (f7rev and families-v2)
H_ORDER = ("1s", "10s", "1m", "5m", "15m", "1h", "6h")
K_ROWS = (("K1/K2 (F7 pool_reversed, organic)", "family = 'F7' AND segment = 'pool_reversed' AND farm = 'organic'"),
          ("K3 (F7 pool_reversed, all pools)", "family = 'F7' AND segment = 'pool_reversed'"))
REF_D = 2  # the entry-delay comparison's reference: the paper's d_ms 600 = 2 slots in the dev days' 266-316 ms regimes
SCREEN_PT = 0.002  # the screen's threshold: 0.2 pt per trade on the paired net Δ (coordinator, 2026-10-19)
SCREEN_H = ("1m", "5m", "15m")
CAUSAL = (0, 2001)  # entries without a same-slot fill: the end of t (first in t + 1) and after the first leg of t + 1


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


def workers():
    """LAB_REPORT_WORKERS: processes for the bootstrap draws of day_ci (1 = serial, the default); the lab runner has 6."""
    n = int(os.environ.get("LAB_REPORT_WORKERS", "1"))
    if n < 1:
        raise SystemExit("LAB_REPORT_WORKERS must be >= 1")
    return n


_POOL = [None, 0]  # the fork pool and its size
_CI_CACHE = {}


def _pool(n):
    """The pool of n processes for the draws; main() creates it before duckdb.connect() (a fork after DuckDB's
    threads exist may deadlock), day_ci only reuses it or, outside main (tests), creates it on demand."""
    if _POOL[0] is None or _POOL[1] != n:
        import multiprocessing as mp
        close_pool()
        _POOL[0], _POOL[1] = mp.get_context("fork").Pool(n), n
    return _POOL[0]


def close_pool():
    if _POOL[0] is not None:
        _POOL[0].close()
        _POOL[0].join()
        _POOL[0], _POOL[1] = None, 0


def _draws(args):
    """The trimmed mean of each resample in `counts` (rows: the day multiplicities of one draw), in order."""
    xs, di, counts = args
    out = np.empty(len(counts))
    for b in range(len(counts)):
        out[b] = weighted_trimmed_mean(xs, counts[b][di].astype(float))
    return out


def day_ci(values, days, boot=BOOT, seed=7, n_workers=None):
    """95 % CI of the trimmed mean over a day bootstrap; (None, None) with fewer than 2 days.
    The draws (the day multiplicities, seed fixed) are generated once in order; with n_workers > 1 (default
    LAB_REPORT_WORKERS) they are split into blocks over a process pool and concatenated back in order, so the
    percentiles are bit-identical to the serial loop (test_day_ci_parallel_is_bit_identical). Identical inputs
    (the same values and days, boot and seed: the K3 group is the F7 pool_reversed group) are answered from a
    cache keyed by the arrays' bytes."""
    values = np.asarray(values, dtype=float)
    days = np.asarray(days)
    uniq, inv = np.unique(days, return_inverse=True)
    if len(uniq) < 2 or len(values) == 0:
        return None, None
    key = (hashlib.sha1(values.tobytes()).hexdigest(), hashlib.sha1(np.ascontiguousarray(inv).tobytes()).hexdigest(), boot, seed)
    if key in _CI_CACHE:
        return _CI_CACHE[key]
    order = np.argsort(values, kind="stable")
    xs, di = values[order], inv[order]
    rng = np.random.default_rng(seed)
    counts = rng.multinomial(len(uniq), np.full(len(uniq), 1 / len(uniq)), size=boot)
    n = workers() if n_workers is None else n_workers
    if n > 1 and boot >= 2 * n:
        blocks = np.array_split(counts, 2 * n)
        out = np.concatenate(_pool(n).map(_draws, [(xs, di, c) for c in blocks]))
    else:
        out = _draws((xs, di, counts))
    out = out[~np.isnan(out)]
    res = (None, None) if len(out) == 0 else (float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5)))
    _CI_CACHE[key] = res
    return res


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
    con.execute(f"""CREATE OR REPLACE TABLE m AS SELECT * FROM read_parquet({lit}, union_by_name = true) WHERE h <> ''
                    QUALIFY row_number() OVER (PARTITION BY {DEDUP} ORDER BY cut, net IS NULL) = 1""")
    con.execute(f"""CREATE OR REPLACE TABLE entries AS SELECT * FROM read_parquet({lit}, union_by_name = true) WHERE h = ''
                    QUALIFY row_number() OVER (PARTITION BY {DEDUP} ORDER BY cut, mid IS NULL) = 1""")
    cols = [c[0] for c in con.execute("SELECT * FROM m LIMIT 0").description]
    if "regime" not in cols or "ms_per_slot" not in cols:  # rows from before #231: one 'unknown' regime, holds at the reference ms
        print(f"markout rows without regime / ms_per_slot (run from before the regime columns): regime 'unknown', {REF_MS} ms per slot", file=sys.stderr)
        for t in ("m", "entries"):
            con.execute(f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS regime VARCHAR DEFAULT 'unknown'")
            con.execute(f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS ms_per_slot DOUBLE DEFAULT {REF_MS}")
    con.execute("""
      CREATE OR REPLACE TABLE pairs AS
      SELECT t.family, t.segment, t.mint, t.t, t.d, t.h, t.day, t.regime, t.farm, t.gross, t.net,
             n.n2_gross, n.n2_net, n.n_n2
      FROM m t JOIN (
        SELECT substr(family, 4) AS family, ref_mint, ref_t, d, h, avg(gross) AS n2_gross, avg(net) AS n2_net, count(*) AS n_n2
        FROM m WHERE family LIKE 'N2\\_%' ESCAPE '\\' AND ref_mint IS NOT NULL GROUP BY ALL) n
      ON n.family = t.family AND (n.ref_mint = t.mint OR n.ref_mint = t.pool) AND n.ref_t = t.t AND n.d = t.d AND n.h = t.h
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
    alive = v > DEAD
    lo_ex, hi_ex = day_ci(v[alive], days[alive], boot) if alive.sum() else (None, None)
    return {"n": len(v), "median": float(np.median(v)), "mean": float(v.mean()), "trim": trimmed_mean(v), "lo": lo, "hi": hi,
            "pos": float((v > 0).mean()), "dead": float((~alive).mean()),
            "trim_ex": trimmed_mean(v[alive]) if alive.sum() else float("nan"), "lo_ex": lo_ex, "hi_ex": hi_ex,
            "cut": con.execute(f"SELECT avg(cut::INT) FROM m WHERE {where}").fetchone()[0]}


def paired(con, where, col, boot):
    """Per trigger under `where`: the N2 twin's own value (mean of its matched entries, on the trigger's day)
    and the paired difference; both with a day CI. The N2 statistics the verdict uses come from here, so the
    N2 set is exactly the twin of the trigger set (also for the K rows, whose farm filter N2 rows do not carry)."""
    rows = con.execute(f"SELECT {col} - n2_{col}, n2_{col}, day FROM pairs WHERE {where} AND {col} IS NOT NULL AND n2_{col} IS NOT NULL").fetchall()
    if not rows:
        return None
    d = np.array([r[0] for r in rows], dtype=float)
    n2 = np.array([r[1] for r in rows], dtype=float)
    days = np.array([r[2] for r in rows])
    lo, hi = day_ci(d, days, boot)
    n2_lo, n2_hi = day_ci(n2, days, boot)
    return {"n": len(d), "n2_trim": trimmed_mean(n2), "n2_lo": n2_lo, "n2_hi": n2_hi, "diff": trimmed_mean(d), "lo": lo, "hi": hi}


def groups(con):
    return con.execute("SELECT DISTINCT family, segment, d FROM m WHERE family NOT LIKE 'N2\\_%' ESCAPE '\\' ORDER BY 1, 2, 3").fetchall()


def table(con, label, where, boot, blocks=None, twin=True):
    """One group's table. The advantage verdict (days 10-18, spec section 5) uses the conservative net
    (dead rows counted at their loss): trigger net trim > 0 with its day CI > 0, and the N2 twin's own
    net trim NOT > 0 with its own CI > 0 on the same horizon (the twins of exactly these triggers, from
    the paired rows). The paired Δ is shown, not a criterion. twin=False skips the N2 columns (a run without
    twins, such as revival, whose `where` may use columns the pairs table lacks: variants, pool)."""
    blocks = blocks or globals()["blocks"](con)
    L = [f"### {label}\n",
         "| block | h | n | cut | dead | gross median | gross trim (cap 10×) | net median | **net trim** | 95% CI (days) | net trim ex dead | 95% CI ex dead | > 0 | N2 twin net trim (paired) | N2 95% CI | Δ vs N2 | 95% CI | advantage |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    verdicts = {}
    for bname, bwhere in blocks:
        for h in H_ORDER:
            w = f"({where}) AND h = '{h}' AND ({bwhere})"
            g = stats(con, w, "gross", boot)
            n = stats(con, w, "net", boot)
            p = paired(con, w, "net", boot) if twin else None
            if not n:
                continue
            trig_ok = n["trim"] > 0 and n["lo"] is not None and n["lo"] > 0
            n2_ok = p is not None and p["n2_trim"] > 0 and p["n2_lo"] is not None and p["n2_lo"] > 0
            adv = trig_ok and not n2_ok if twin else None  # without a twin there is no advantage verdict (n/a), not a free "yes"
            if bname == BINDING and twin:
                verdicts[h] = adv
            L.append(f"| {bname} | {h} | {fmt(n['n'])} | {fmt(n['cut'], 0, pct=True)} | {fmt(n['dead'], 0, pct=True)} | {fmt(g['median'], 1, True, True)} | {fmt(g['trim'], 1, True, True)} | "
                     f"{fmt(n['median'], 1, True, True)} | **{fmt(n['trim'], 1, True, True)}** | {fmt(n['lo'], 1, True, True)} … {fmt(n['hi'], 1, True, True)} | "
                     f"{fmt(n['trim_ex'], 1, True, True)} | {fmt(n['lo_ex'], 1, True, True)} … {fmt(n['hi_ex'], 1, True, True)} | {fmt(n['pos'], 0, True)} | "
                     + (f"{fmt(p['n2_trim'], 1, True, True)} ({fmt(p['n'])}) | {fmt(p['n2_lo'], 1, True, True)} … {fmt(p['n2_hi'], 1, True, True)} | "
                        f"{fmt(p['diff'], 1, True, True)} | {fmt(p['lo'], 1, True, True)} … {fmt(p['hi'], 1, True, True)} |" if p else "– | – | – | – |")
                     + (f" **{'n/a' if adv is None else 'yes' if adv else 'no'}** |" if bname == BINDING else " |"))
    first = next((h for h in H_ORDER if verdicts.get(h)), None)
    imp = con.execute(f"SELECT avg((impact > 0.05)::INT), count(*) FROM entries WHERE ({where}) AND impact IS NOT NULL").fetchone()
    L.append("")
    L.append(f"{BINDING} verdict (spec section 5, conservative net with dead rows): "
             + ("n/a without an N2 twin" if not twin else f"advantage first at **{first}**" if first else "no horizon with an advantage") + "."
             + (f" Entry impact > 5 % at 0.5 SOL: {fmt(imp[0], 1, True)} of {fmt(imp[1])} entries." if imp and imp[0] is not None else ""))
    L.append("")
    return "\n".join(L)


def exit_costs(con, parts_dir, cfg_path, boot, ms_per_slot=None):
    """Realized net per position (variant, d, 0.5 SOL, q 0.5) against the net markout at the horizon
    nearest the variant's median hold, on the same signals, per slot-time regime (the regime and the ms
    per slot of the signal in the markout rows, from the store meta)."""
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
    con.execute("""
      CREATE OR REPLACE TABLE posm AS
      SELECT p.*, m.regime, m.ms_per_slot, m.h, m.net FROM pos p JOIN m ON m.family = p.family AND m.segment = p.segment AND m.mint = p.mint AND m.t = p.t AND m.d = p.d
      WHERE m.net IS NOT NULL""")
    rows = con.execute("SELECT family, variant, segment, d, regime, median(hold_slots), count(DISTINCT (mint, t)), median(ms_per_slot) FROM posm GROUP BY ALL ORDER BY ALL").fetchall()
    L = ["### Exit-rule cost: realized net per position vs the net markout at the median hold (0.5 SOL, base, q = 0.5), per regime\n",
         "| family | variant | segment | d | regime | positions | median hold | nearest h | realized net (trim) | markout net (trim) | Δ realized − markout | 95% CI (days) | best h afterwards (trim) |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    hs = dict(zip(H_ORDER, (1_000, 10_000, 60_000, 300_000, 900_000, 3_600_000, 21_600_000)))
    for fam, var, seg, d, reg, hold, n, ms in rows:
        hold_ms = (hold or 0) * (ms_per_slot or ms)
        h = min(H_ORDER, key=lambda k: abs(hs[k] - hold_ms))
        pr = con.execute("SELECT ret - net, ret, net, day FROM posm WHERE family = ? AND variant = ? AND segment = ? AND d = ? AND regime = ? AND h = ?",
                         [fam, var, seg, d, reg, h]).fetchall()
        if not pr:
            continue
        diff = np.array([r[0] for r in pr]); re = np.array([r[1] for r in pr]); mk = np.array([r[2] for r in pr]); days = np.array([r[3] for r in pr])
        lo, hi = day_ci(diff, days, boot)
        best = con.execute("SELECT h, net FROM posm WHERE family = ? AND variant = ? AND segment = ? AND d = ? AND regime = ?", [fam, var, seg, d, reg]).fetchall()
        by_h = {}
        for hh, v in best:
            by_h.setdefault(hh, []).append(v)
        bh = max(by_h, key=lambda k: trimmed_mean(by_h[k])) if by_h else None
        L.append(f"| {fam} | {var} | {seg} | {d} | {reg} | {fmt(len(pr))} | {fmt(hold_ms / 1000, 0)} s | {h} | {fmt(trimmed_mean(re), 1, True, True)} | "
                 f"{fmt(trimmed_mean(mk), 1, True, True)} | **{fmt(trimmed_mean(diff), 1, True, True)}** | {fmt(lo, 1, True, True)} … {fmt(hi, 1, True, True)} | "
                 f"{bh}: {fmt(trimmed_mean(by_h[bh]), 1, True, True) if bh else '–'} |")
    L.append("")
    return "\n".join(L)


def delay_codes(con):
    """The entry-delay codes in the rows beside REF_D (the --tx-delays run), in display order."""
    ds = [r[0] for r in con.execute(f"SELECT DISTINCT d FROM m WHERE d <> {REF_D} ORDER BY d").fetchall()]
    return ds if any(d == 0 or d >= 1000 for d in ds) else []


def delay_pairs(con):
    """dpairs: per signal, horizon and code the code's and the reference's entry mid and net (both present),
    with the code entry's capped flag; and dcover: per code the signals with a reference entry."""
    con.execute(f"""
      CREATE OR REPLACE TABLE dpairs AS
      SELECT a.family, a.segment, a.mint, a.pool, a.t, a.day, a.regime, a.farm, a.d, a.h, a.net, b.net AS ref_net,
             ea.mid AS mid, eb.mid AS ref_mid, ea.capped
      FROM m a JOIN m b ON b.family = a.family AND b.segment = a.segment AND b.venue = a.venue AND b.mint = a.mint
                       AND COALESCE(b.pool, '') = COALESCE(a.pool, '') AND b.t = a.t AND b.h = a.h AND b.d = {REF_D}
      JOIN entries ea ON ea.family = a.family AND ea.segment = a.segment AND ea.venue = a.venue AND ea.mint = a.mint
                       AND COALESCE(ea.pool, '') = COALESCE(a.pool, '') AND ea.t = a.t AND ea.d = a.d
      JOIN entries eb ON eb.family = a.family AND eb.segment = a.segment AND eb.venue = a.venue AND eb.mint = a.mint
                       AND COALESCE(eb.pool, '') = COALESCE(a.pool, '') AND eb.t = a.t AND eb.d = {REF_D}
      WHERE a.d <> {REF_D} AND a.family NOT LIKE 'N2\\_%' ESCAPE '\\' AND a.net IS NOT NULL AND b.net IS NOT NULL
        AND ea.mid IS NOT NULL AND eb.mid IS NOT NULL""")


def delay_table(con, label, where, boot, codes):
    """One group's entry-delay table: per code the entry mid advantage and, per block and horizon, the paired net Δ
    against d = REF_D with its day CI, the coverage and the capped share; the screen verdict on the binding block."""
    L = [f"### Entry delay vs d = {REF_D} · {label}\n",
         f"| block | entry | h | n pairs | coverage | capped | entry mid Δ (trim) | 95% CI | **net Δ (trim)** | 95% CI (days) | Δ > 0 | same state | net Δ ex capped (trim) | n ex capped | screen |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    verdict = []
    for bname, bwhere in blocks(con):
        for d in codes:
            for h in H_ORDER:
                n_ref = con.execute(f"SELECT count(*) FROM m WHERE ({where}) AND d = {REF_D} AND h = '{h}' AND net IS NOT NULL AND ({bwhere})").fetchone()[0]
                rows = con.execute(f"SELECT net - ref_net, ref_mid / mid - 1, day, capped, mid = ref_mid FROM dpairs WHERE ({where}) AND d = {d} AND h = '{h}' AND ({bwhere})").fetchall()
                if not rows:
                    continue
                dn = np.array([r[0] for r in rows], dtype=float)
                dm = np.array([r[1] for r in rows], dtype=float)
                days = np.array([r[2] for r in rows])
                capped = np.array([bool(r[3]) for r in rows])
                same = np.array([bool(r[4]) for r in rows])
                ex = dn[~capped]
                lo, hi = day_ci(dn, days, boot)
                mlo, mhi = day_ci(dm, days, boot)
                tr = trimmed_mean(dn)
                ok = tr >= SCREEN_PT and lo is not None and lo > 0
                screen = ""
                if bname == BINDING and d in CAUSAL and h in SCREEN_H:
                    screen = f"**{'yes' if ok else 'no'}**"
                    verdict.append((d, h, ok))
                elif d >= 1000 and bname == BINDING and h in SCREEN_H:
                    screen = "upper bound"
                L.append(f"| {bname} | {markout.delay_label(d)} | {h} | {fmt(len(dn))} | {fmt(len(dn) / n_ref if n_ref else float('nan'), 0, True)} | "
                         f"{fmt(float(capped.mean()), 0, True)} | {fmt(trimmed_mean(dm), 2, True, True)} | {fmt(mlo, 2, True, True)} … {fmt(mhi, 2, True, True)} | "
                         f"**{fmt(tr, 2, True, True)}** | {fmt(lo, 2, True, True)} … {fmt(hi, 2, True, True)} | {fmt(float((dn > 0).mean()), 0, True)} | "
                         f"{fmt(float(same.mean()), 0, True)} | {fmt(trimmed_mean(ex), 2, True, True) if len(ex) else '–'} | {fmt(int(len(ex)))} | {screen} |")
    yes = [(d, h) for d, h, ok in verdict if ok]
    L.append("")
    L.append(f"{BINDING} screen (net Δ ≥ {SCREEN_PT * 100:.1f} pt with its day CI above 0, causal entries {', '.join(markout.delay_label(d) for d in CAUSAL)}, "
             f"h in {', '.join(SCREEN_H)}): " + (("positive at " + ", ".join(f"{markout.delay_label(d)} / {h}" for d, h in yes)) if yes else "not met") + ".")
    L.append("")
    return "\n".join(L)


def delay_section(con, boot):
    """The entry-delay comparison (spec M-a) for every family x segment with codes, and the K rows."""
    codes = delay_codes(con)
    if not codes:
        return ""
    delay_pairs(con)
    L = [f"## Entry delay (spec M-a): paired per signal against d = {REF_D}\n",
         f"Reference d = {REF_D} = the paper's d_ms 600 in the dev days' slot regimes. Entry mid Δ = mid_ref / mid_entry − 1 (> 0: the entry is cheaper than the reference); "
         "net Δ = net markout of the entry − net markout of the reference at the same horizon (each from its own entry slot: the horizons differ by at most 3 slots, "
         "negligible from 1 m on); trimmed means with day CIs; coverage = pairs / reference signals with a net at that horizon. **Capped entries count at their fallback state**: a k-code "
         "beyond the slot's legs enters at the end of t (the slot's last leg), and t+1 first tx without a leg in t + 1 enters at the end of t too; both equal d = 0 then, "
         "which is what such an entry would get (nobody traded before it), so they stay in the paired Δ; 'capped' is their share, 'net Δ ex capped' the Δ on the "
         "signals that did have the leg (a selection on pools that traded, not a cleaner estimate). 'same state' = share of pairs whose entry state equals the "
         "reference's (the pool did not trade between the two entries: their Δ is the fee-free zero), so a mean advantage comes from the other signals. "
         "The k-codes inside t need a same-slot fill (bundles) and are an upper bound; "
         "d = 0 (the end of t: a fill as the first transaction of t + 1) and t+1 first tx are the causal entries without bundles, still without competition, TOL or landing. "
         "Not a P&L: the exit rule, busy and the fill are not recomputed per entry (route (ii)).\n"]
    for fam, seg in con.execute("SELECT DISTINCT family, segment FROM dpairs ORDER BY 1, 2").fetchall():
        L.append(delay_table(con, f"{fam} · {seg}", f"family = '{fam}' AND segment = '{seg}'", boot, codes))
    for label, where in K_ROWS:
        if con.execute(f"SELECT count(*) FROM dpairs WHERE {where}").fetchone()[0]:
            L.append(delay_table(con, label, where, boot, codes))
    return "\n".join(L)


def render(con, markout_dir, boot, parts_dir=None, cfg_path=None, ms_per_slot=None):
    runs = sorted(glob.glob(os.path.join(markout_dir, "run-markout-*.json")))
    infos = [json.load(open(r)) for r in runs]
    L = [f"## Markout curves ({', '.join(i['run'] for i in infos)})\n",
         f"Signals {sum(i['signals'] for i in infos):,}; 0.5 SOL round trip at base costs; gross = mid_h / mid_entry − 1, net = SOL back / (0.5 + 2 × order cost) − 1; "
         f"trimmed = 1 % trimmed mean; CI = day bootstrap of the trimmed mean ({boot:,}×); N2 paired per trigger (mean of its matched entries); "
         "cut = share of rows past the store end or the venue's last leg (last known state); dead = share with net ≤ −99 % (the venue cannot pay: a drained pool; "
         "a completed curve is valued on its migration pool, not dead); gross is capped at +1000 % for the trimmed mean; 'ex dead' leaves the dead rows out. "
         f"Blocks: days 1-9 (< {SPLIT_DAY}), days 10-18 ({SPLIT_DAY} … {LAST_DAY}, the binding window), all days, and one block per slot-time regime in the rows (ms per slot of the signal's epoch from the store meta, rounded to 10 ms; all days). "
         "Advantage (days 10-18, spec §5, on the conservative net that counts dead rows): the trigger's net trimmed mean > 0 with its day CI > 0, and the N2 twin's own "
         "net trimmed mean (the matched entries of exactly these triggers) not > 0 with its own CI > 0 on the same horizon; the paired Δ is informative, not a criterion. "
         "Rows are deduplicated over runs on (family, segment, venue, mint, pool, t, d, h).\n"]
    for fam, seg, d in groups(con):
        L.append(table(con, f"{fam} · {seg} · d = {markout.delay_label(d)}", f"family = '{fam}' AND segment = '{seg}' AND d = {d}", boot))
    for label, where in K_ROWS:
        for (d,) in con.execute(f"SELECT DISTINCT d FROM m WHERE {where} ORDER BY d").fetchall():
            L.append(table(con, f"{label} · d = {markout.delay_label(d)}", f"{where} AND d = {d}", boot))
    sec = delay_section(con, boot)
    if sec:
        L.append(sec)
    if parts_dir:
        L.append(exit_costs(con, parts_dir, cfg_path, boot, ms_per_slot))
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--markout", required=True)
    ap.add_argument("--parts")
    ap.add_argument("--cfg", default=os.environ.get("LAB_DEFAULTS", "/home/chupa/Solana-project/data-old-faithful-one/lab/research/week2-defaults.yaml"))
    ap.add_argument("--ms-per-slot", type=float, help="override the per-regime ms per slot for the median hold in the exit-cost table")
    ap.add_argument("--boot", type=int, default=BOOT)
    ap.add_argument("--out")
    a = ap.parse_args()
    if workers() > 1:
        _pool(workers())  # before DuckDB starts its threads
    try:
        con = duckdb.connect()
        con.execute(f"SET memory_limit = '{os.environ.get('LAB_WALLETS_MEMORY', '1500MB')}'; SET threads TO 2")
        spill = os.environ.get("LAB_SPILL_DIR")
        if spill:
            os.makedirs(spill, exist_ok=True)
            con.execute(f"SET temp_directory = '{spill}'; SET max_temp_directory_size = '{os.environ.get('LAB_SPILL_MAX', '1GB')}'")
        load(con, a.markout)
        md = render(con, a.markout, a.boot, a.parts, a.cfg, a.ms_per_slot)
    finally:
        close_pool()
    if a.out:
        with open(a.out, "w") as f:
            f.write(md + "\n")
        print(a.out, file=sys.stderr)
    else:
        print(md)


if __name__ == "__main__":
    main()
