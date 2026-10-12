"""Overfitting checks for a tournament: the Deflated Sharpe Ratio and the Probability of Backtest
Overfitting (standard in every result report with a candidate; coordinator, 2026-10-12).

Trials are the configurations a tournament compared; each trial's return series is its net P&L
per UTC day (SOL, base costs through backtest.costs.settle_sql, days without a trade count 0).
The two numbers answer "how much of the best trial's Sharpe ratio is selection?":

  DSR  Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014). The best of N trials has an expected
       Sharpe ratio under the null of no skill of
         SR0 = sqrt(V[SR_n]) * ((1 - g) * Z(1 - 1/N) + g * Z(1 - 1/(N e)))      g = 0.5772...
       (V[SR_n]: variance of the trials' Sharpe ratios, Z: standard normal quantile). The DSR is
       the probabilistic Sharpe ratio of the selected trial against SR0 instead of against 0:
         PSR(SR*) = Phi( (SR - SR*) sqrt(T - 1) / sqrt(1 - s SR + (k - 1) / 4 SR^2) )
       with T observations, s the skewness and k the (non-excess) kurtosis of the returns. SR
       here is per day, not annualised. N counts every trial as independent; correlated trials
       make SR0 too high and the DSR too low, which is the safe direction. V[SR_n] is reported
       two ways: the variance across the trials' Sharpe ratios (the paper's definition; when the
       trial set mixes structurally different arms it also contains their true differences and
       SR0 grows) and the sampling variance of a skill-less Sharpe ratio, 1/T (Lo 2002), which
       asks only "is this the best of N lucky draws of the same length?". Both DSRs are shown;
       --where restricts the trials to the set the selection was actually made over.

  PBO  Probability of Backtest Overfitting by CSCV (Bailey, Borwein, Lopez de Prado & Zhu 2017).
       The T x N matrix is cut into S blocks of days; for every choice of S/2 blocks as the
       in-sample set J (C(S, S/2) splits) the trial with the best in-sample Sharpe is picked and
       its out-of-sample rank among the N trials is taken as w = rank / (N + 1) (1 = best); the
       logit lambda = log(w / (1 - w)) is below 0 when the in-sample winner is in the worse
       out-of-sample half. PBO = share of splits with lambda <= 0 (0.5 = pure noise). Also
       reported: the slope of out-of-sample on in-sample Sharpe over the splits (performance
       degradation; < 0 is typical of overfitting) and the share of splits where the in-sample
       winner loses out of sample.

Both use only numpy (no scipy in lab-py): Phi via math.erfc, Z by bisection on Phi.

  python -m wallets.overfit --parts DIR [--family F7] [--scenario base] [--blocks 16]
                            [--days A B] [--where "farm = 'organic' AND size_sol = 0.5"]
                            [--key family,variant,size_sol,d,q,farm,overlay]
                            [--select "variant=tp4_T600,size_sol=0.5,d=2,farm=organic"] [--out FILE]

Reads the run's parts (positions), settles them at the scenario's costs, sums the net P&L per
trial per day in [A, B] and writes one Markdown section per family: N trials, T days, the best
trial, its SR, SR0, DSR, the PBO with the degradation slope, and the DSR of --select (the
candidate) when given. Development data only; the parts decide which days exist.
"""

import argparse
import glob
import itertools
import math
import os
import sys

import numpy as np

EULER_GAMMA = 0.5772156649015329
DEFAULTS = os.environ.get("LAB_DEFAULTS", "/home/chupa/Solana-project/data-old-faithful-one/lab/research/week2-defaults.yaml")
KEY = ("family", "variant", "size_sol", "d", "q", "farm", "overlay")


# --- normal distribution without scipy -------------------------------------------------------

def norm_cdf(x):
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def norm_ppf(p):
    """Standard normal quantile by bisection (|error| < 1e-10); -inf / +inf at 0 / 1."""
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    lo, hi = -40.0, 40.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if norm_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12:
            break
    return 0.5 * (lo + hi)


# --- Sharpe statistics --------------------------------------------------------------------------

def sharpe(x):
    """Mean / sample standard deviation of a return series; 0 when the series has no variance."""
    x = np.asarray(x, dtype=float)
    if len(x) < 2:
        return 0.0
    sd = x.std(ddof=1)
    return float(x.mean() / sd) if sd > 0 else 0.0


def sharpe_columns(m):
    """Sharpe ratio per column of a T x N matrix (T >= 2)."""
    m = np.asarray(m, dtype=float)
    sd = m.std(axis=0, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        sr = np.where(sd > 0, m.mean(axis=0) / sd, 0.0)
    return sr


def moments(x):
    """(skewness, non-excess kurtosis) of a series; (0, 3) when it has no variance."""
    x = np.asarray(x, dtype=float)
    sd = x.std()
    if len(x) < 3 or sd == 0:
        return 0.0, 3.0
    z = (x - x.mean()) / sd
    return float((z ** 3).mean()), float((z ** 4).mean())


def expected_max_sharpe(n_trials, var_sr):
    """E[max SR] of n_trials independent trials with no skill and Sharpe-ratio variance var_sr."""
    if n_trials <= 1 or var_sr <= 0:
        return 0.0
    n = float(n_trials)
    return math.sqrt(var_sr) * ((1 - EULER_GAMMA) * norm_ppf(1 - 1 / n) + EULER_GAMMA * norm_ppf(1 - 1 / (n * math.e)))


def psr(sr, sr_bench, t, skew=0.0, kurt=3.0):
    """Probabilistic Sharpe ratio: P(true SR > sr_bench) given sr over t observations."""
    if t < 2:
        return float("nan")
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr * sr
    if denom <= 0:
        return float("nan")
    return norm_cdf((sr - sr_bench) * math.sqrt(t - 1) / math.sqrt(denom))


def dsr(m, selected=None):
    """Deflated Sharpe ratio of one trial of the T x N matrix m (default: the best in-sample).

    Returns a dict: n_trials, t, selected, sr, var_sr, sr0 and dsr (SR0 from the trials' Sharpe
    variance), sr0_null and dsr_null (SR0 from the null sampling variance 1/T), psr0 (against 0),
    skew, kurt."""
    m = np.asarray(m, dtype=float)
    t, n = m.shape
    srs = sharpe_columns(m)
    sel = int(np.argmax(srs)) if selected is None else int(selected)
    var_sr = float(srs.var(ddof=1)) if n > 1 else 0.0
    sr0 = expected_max_sharpe(n, var_sr)
    sr0_null = expected_max_sharpe(n, 1.0 / t) if t > 0 else 0.0
    skew, kurt = moments(m[:, sel])
    sr = float(srs[sel])
    return {"n_trials": n, "t": t, "selected": sel, "sr": sr, "var_sr": var_sr, "sr0": sr0, "sr0_null": sr0_null,
            "psr0": psr(sr, 0.0, t, skew, kurt), "dsr": psr(sr, sr0, t, skew, kurt), "dsr_null": psr(sr, sr0_null, t, skew, kurt),
            "skew": skew, "kurt": kurt}


def pbo(m, blocks=16, max_splits=None):
    """CSCV probability of backtest overfitting of the T x N matrix m.

    Returns a dict: pbo, n_splits, blocks, slope (OOS Sharpe on IS Sharpe of the IS winner over
    the splits), prob_loss (share of splits whose IS winner has OOS Sharpe < 0), lambdas."""
    m = np.asarray(m, dtype=float)
    t, n = m.shape
    s = min(int(blocks), t)
    if s < 2 or n < 2:
        return {"pbo": float("nan"), "n_splits": 0, "blocks": s, "slope": float("nan"), "prob_loss": float("nan"),
                "lambdas": np.zeros(0)}
    s -= s % 2
    parts = np.array_split(np.arange(t), s)
    combos = itertools.combinations(range(s), s // 2)
    if max_splits:
        combos = itertools.islice(combos, int(max_splits))
    lam, is_sr, oos_sr = [], [], []
    for c in combos:
        train = np.concatenate([parts[i] for i in c])
        test = np.concatenate([parts[i] for i in range(s) if i not in c])
        if len(train) < 2 or len(test) < 2:
            continue
        sr_in = sharpe_columns(m[train])
        sr_out = sharpe_columns(m[test])
        best = int(np.argmax(sr_in))
        rank = 1 + int((sr_out < sr_out[best]).sum())  # 1 = worst out of sample; ties count as below
        w = rank / (n + 1)
        lam.append(math.log(w / (1 - w)))
        is_sr.append(sr_in[best])
        oos_sr.append(sr_out[best])
    lam = np.array(lam)
    if len(lam) == 0:
        return {"pbo": float("nan"), "n_splits": 0, "blocks": s, "slope": float("nan"), "prob_loss": float("nan"), "lambdas": lam}
    is_sr, oos_sr = np.array(is_sr), np.array(oos_sr)
    slope = float(np.polyfit(is_sr, oos_sr, 1)[0]) if is_sr.std() > 0 else float("nan")
    return {"pbo": float((lam <= 0).mean()), "n_splits": int(len(lam)), "blocks": s, "slope": slope,
            "prob_loss": float((oos_sr < 0).mean()), "lambdas": lam}


# --- from a run's parts --------------------------------------------------------------------------

def day_matrix(con, parts_dir, cfg, scenario="base", key=KEY, family=None, days=None, where=None):
    """Per-day net P&L (SOL) per trial from a run's parts: (families -> (trial labels, days, T x N matrix))."""
    from backtest.costs import Scenario, settle_sql  # the engine on sys.path (LAB_ENGINE or this repo's lab/)

    extra = where
    pnl, _ = settle_sql(Scenario.from_cfg(scenario, cfg))
    files = sorted(glob.glob(os.path.join(parts_dir, "*.parquet")))
    if not files:
        raise SystemExit(f"no parquet parts in {parts_dir}")
    lit = "[" + ",".join(f"'{f}'" for f in files) + "]"
    where = ["NOT skipped"]
    if family:
        where.append(f"family = '{family}'")
    if days:
        where.append(f"day >= '{days[0]}' AND day <= '{days[1]}'")
    if extra:
        where.append(f"({extra})")
    cols = ", ".join(key)
    rows = con.execute(f"""
      SELECT {cols}, day, sum({pnl})::DOUBLE / 1e9 AS pnl, count(*) AS n
      FROM read_parquet({lit}, union_by_name = true) WHERE {' AND '.join(where)}
      GROUP BY ALL ORDER BY ALL""").fetchall()
    out = {}
    k = len(key)
    for r in rows:
        fam = r[0]
        out.setdefault(fam, {}).setdefault(tuple(r[:k]), {})[r[k]] = r[k + 1]
    result = {}
    for fam, trials in out.items():
        all_days = sorted({d for t in trials.values() for d in t})
        labels = sorted(trials)
        m = np.array([[trials[lab].get(d, 0.0) for lab in labels] for d in all_days], dtype=float)
        result[fam] = (labels, all_days, m)
    return result


def label(tr, key=KEY):
    return ", ".join(f"{k}={v}" for k, v in zip(key, tr) if k != "family")


def select_index(labels, key, sel):
    """Index of the trial matching 'k=v,k=v' (every given field must match); None when absent."""
    if not sel:
        return None
    want = dict(p.split("=", 1) for p in sel.split(","))
    for i, tr in enumerate(labels):
        if all(str(dict(zip(key, tr)).get(k)) == v for k, v in want.items()):
            return i
    return None


def fmt(x, nd=2):
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{nd}f}"


def render(parts_dir, scenario, blocks, days, key, sel, families, where=None):
    L = [f"## Overfitting check ({os.path.basename(os.path.normpath(parts_dir))}, {scenario} costs"
         + (f", days {days[0]} … {days[1]}" if days else "") + (f", trials where {where}" if where else "") + ")\n",
         "Trial = " + " × ".join(k for k in key if k != "family") + "; return series = net P&L in SOL per UTC day "
         "(days without a trade count 0); SR per day. DSR: probability that the best trial's Sharpe ratio beats the expected "
         "maximum of N skill-less trials, SR0 from the variance of the trials' Sharpe ratios (paper) or from the null "
         "sampling variance 1/T (same-length lucky draws). PBO: share of CSCV splits where the in-sample winner is in the worse "
         "out-of-sample half (0.5 = noise); slope = OOS on IS Sharpe of the winner; loss = share of splits where the winner "
         "loses out of sample.\n",
         "| family | N trials | T days | best trial (in-sample) | SR | PSR vs 0 | SR0 (trials) | **DSR** | SR0 (1/T) | **DSR (1/T)** | **PBO** (splits) | slope | loss |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    rows = []
    for fam, (labels, all_days, m) in families.items():
        d = dsr(m)
        p = pbo(m, blocks)
        L.append(f"| {fam} | {d['n_trials']} | {d['t']} | {label(labels[d['selected']], key)} | {fmt(d['sr'])} | {fmt(d['psr0'])} | "
                 f"{fmt(d['sr0'])} | **{fmt(d['dsr'])}** | {fmt(d['sr0_null'])} | **{fmt(d['dsr_null'])}** | "
                 f"**{fmt(p['pbo'])}** ({p['n_splits']}) | {fmt(p['slope'])} | {fmt(p['prob_loss'])} |")
        i = select_index(labels, key, sel)
        if i is not None:
            ds = dsr(m, i)
            rows.append(f"| {fam} | {label(labels[i], key)} | {fmt(ds['sr'])} | {fmt(ds['psr0'])} | **{fmt(ds['dsr'])}** | "
                        f"**{fmt(ds['dsr_null'])}** | rank {1 + int((sharpe_columns(m) > ds['sr']).sum())} of {ds['n_trials']} |")
    if rows:
        L += ["", "Selected trial (the candidate), deflated against the same N:", "",
              "| family | trial | SR | PSR vs 0 | **DSR** | **DSR (1/T)** | in-sample rank |", "|---|---|---|---|---|---|---|"] + rows
    L.append("")
    L.append("Reading: a DSR below 0.95 means the best trial's Sharpe ratio is not distinguishable from the best of N lucky "
             "draws; a PBO near 0.5 means the in-sample choice carries no information about out-of-sample rank. Both depend "
             "on the trial set: arms that are known to be bad (flagged pools, d = 3) inflate SR0 (trials) and deflate the PBO, "
             "so run the check on the set the choice was made over. With T of 11 to 20 days these are weak tests by "
             "construction, which is the point. F7 passed both on dev and still failed the hold-out: the DSR and the PBO "
             "test selection, not drift; they never replace a hold-out or a forward test.")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parts", required=True)
    ap.add_argument("--family")
    ap.add_argument("--scenario", default="base")
    ap.add_argument("--blocks", type=int, default=16)
    ap.add_argument("--days", nargs=2, metavar=("FROM", "TO"))
    ap.add_argument("--where", help="extra SQL filter on the parts, e.g. \"farm = 'organic' AND size_sol = 0.5\"")
    ap.add_argument("--key", default=",".join(KEY))
    ap.add_argument("--select", help="k=v,k=v of the candidate trial to deflate against the same N")
    ap.add_argument("--cfg", default=DEFAULTS)
    ap.add_argument("--memory", default=os.environ.get("LAB_WALLETS_MEMORY", "1500MB"))
    ap.add_argument("--out")
    a = ap.parse_args()
    import duckdb
    import yaml
    with open(a.cfg) as f:
        cfg = yaml.safe_load(f)
    key = tuple(a.key.split(","))
    if key[0] != "family":
        key = ("family",) + key
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{a.memory}'; SET threads TO 2; SET preserve_insertion_order = false")
    spill = os.environ.get("LAB_SPILL_DIR")
    if spill:
        con.execute(f"SET temp_directory = '{spill}'")
    fams = day_matrix(con, a.parts, cfg, a.scenario, key, a.family, a.days, a.where)
    md = render(a.parts, a.scenario, a.blocks, a.days, key, a.select, fams, a.where)
    if a.out:
        with open(a.out, "w") as f:
            f.write(md + "\n")
        print(a.out, file=sys.stderr)
    else:
        print(md)


if __name__ == "__main__":
    main()
