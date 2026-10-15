"""The clean-data test of the wash-trigger hypothesis (research/hypothesis-wash-trigger-2026-10-15.md,
sha256 35bdaa0d) on epochs 1055-1063, as the hashed addendum (research/prereg-wash-trigger-addendum-
2026-10-15.md) lays it down: K3 without and with the triggers whose trigger leg is a same-transaction
round trip (rt_tx).

  python -m backtest.clean_test --test-run DIR --matrix-run DIR --store FILE --out DIR \\
      --hypothesis FILE --addendum FILE --addendum-sha256 SHA --exclusions FILE --exclusions-sha256 SHA \\
      --trials 42 --dev-parts DIR [--a4c FILE]

Two tournament runs from one engine snapshot on the clean store: the test run (--delay-ms 600: K3's
delay in whole slots of each trigger's epoch, the paper engine's Clock rule) and the matrix run
(--delays 1,2,3: the dev configurations for the PBO, and d = 3). Order, fail-closed (a failure exits before the
next step and nothing after it is computed):
  1. documents: the hypothesis's sha256 is 35bdaa0d..., the addendum's and the exclusion list's are the
     ones passed (each also equal to its .sha256 file);
  2. store and runs: the store is period 'clean', built for this addendum from one extractor build;
     both runs are F7,N2 pool_reversed, 0.5 SOL, q 0.5, tau empirical, n2-k 5, from one engine commit,
     decisions in the store's window; the test run has d_ms 600, the matrix run d 1, 2, 3 (and any
     other d the Clock gives for 600 ms);
  3. equality: per epoch, the test run's K3 rows equal the matrix run's K3 rows at d = Clock.slots(600)
     of that epoch, row for row;
  4. exclusions: every rule of the list resolved against the store (mints <-> pools, symbols, names),
     written to exclusions.json; their positions leave every run's rows before any outcome;
  5. the minimum: >= MIN_DAYS trading days (UTC days with a filled K3 trade); below it only
     "te weinig dagen" is written;
  6. outcomes: rt_tx and wash_1h (backtest.wash, the audit's a4c SQL), H1, H2, the decision rule,
     wash_1h descriptive, a4c itself (unchanged) on the matrix run at d = 1, 2, whose filter groups
     must equal backtest.wash's (n and mean), then the DSR and the PBO.

H1: mean net return per filled K3 trade without rt_tx > 0 (lower bound of the 95 % interval of a
bootstrap over trading days, DRAWS draws, seed 7). H2: mean without - mean with rt_tx > 0 (both groups
drawn together per day). "bevestigd" when H1 and H2 hold, "risicofilter zonder bewezen winst" when only
H2 holds, else "verworpen". Triggers without a trigger leg (rt_tx NULL) are in neither group (a4c).
The matrix (addendum v4): rows the decision window's UTC dates (a date without a trade counts 0), T
of them; columns the 36 dev configurations of the matrix run (9 variants x d 1, 2 x farm all /
organic), the 6 filters (K3 without rt_tx, without wash_1h, without both; at d_ms 600 and d = 1) and,
43rd, K3 at the Clock's d from the test run. DSR on the H1 series (K3 without rt_tx at d_ms 600) against
the best of --trials (42) skill-less trials, with the Sharpe variance of the 36 dev trials and with the
null variance 1/T; the 43rd column's DSR beside it. PBO (CSCV) over the T x 43 matrix, blocks min(16, T).

--dry-run: any store and runs, no period, window, engine or addendum checks; the output is marked as
a dry run (for testing the job, never for the test).
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

import duckdb
import numpy as np

from . import wash
from .costs import Scenario, settle_sql
from .store import load_defaults
from .strategies import EPOCH_SLOTS, Clock

HYPOTHESIS_SHA = "35bdaa0d3f5ed9a36e2af0582810412d29d7d35b217d50d1dc45fd4acb083a28"
A4C_SHA = "7e6980c31f5acdb996be6efb209b9f749b1cff775e5e2f91667187e73d2a24d6"
DELAY_MS = 600
COMMON = {"families": ["F7", "N2"], "segments": ["pool_reversed"], "sizes_sol": [0.5], "q": [0.5], "tau": ["empirical"],
          "slippage_tol": 0.05, "n2_k": 5, "variants": None}
K3 = "family = 'F7' AND segment = 'pool_reversed' AND variant = 'tp4_T600' AND size_sol = 0.5 AND q = 0.5 AND overlay = 'none' AND NOT skipped"
F7REV = "family = 'F7' AND segment = 'pool_reversed' AND size_sol = 0.5 AND q = 0.5 AND overlay = 'none' AND NOT skipped"
# The replay code the addendum (v5, section 9) pins: byte-equal to f2c9666 (store.py and clean_test.py may differ).
REPLAY_SHA256 = {
    "tournament.py": "82f939ce84f5cf1ef00af4abfc66cb76d19a47e52c79d56b24b41ecc748f4451",
    "strategies.py": "a3c2dfc77beedc38fef05566c01bfc26d0c635b911e51c0b998964d9eb20fc7f",
    "replay.py": "310a99d8b82be7dc07b4cd81c1c265b830c19fa632903b8d8bf1af3741d658d4",
    "costs.py": "27db4d6d9d95a0fb5bf797573aa968d01c1858820d5e16b2625eccf056fa7077",
    "wash.py": "8486a0d289ffc826cb96961b243cfb777e1b774bd6231bbd7b221b3a5dcc8440",
    "venues.py": "bce5c80b7f729459c738b989de5e7ef6592f63fbeeb0112b28fb4f349ab13f03",
    "streams.py": "201412f735647789a37a617adfe8e8b18a6ada088832c3bb371bfe0ba152467e",
    "mechanics.py": "3779c94287de2e030482b679cb52ccb907d8ad10cff038b6a00539f9ff392ad0",
    "overlay.py": "1b606ee7320d4cf4efd0aa9a386485f211f3a1cc97eaf362429a2b6ad86a1d65",
    "data.py": "5ea7765cbf8cca4d1c1cd97dac01f87705f2dc3c16e06fd18328af6b072a8709",
}
MIN_DAYS = 5
DRAWS = 10_000
SEED = 7
B58 = re.compile(r"`([1-9A-HJ-NP-Za-km-z]{32,44})`")


class Refused(SystemExit):
    pass


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def check_document(path, expected, name):
    """The document's sha256 when it equals the expected one and its .sha256 file (when present)."""
    if not path or not os.path.exists(path):
        raise Refused(f"{name}: {path} does not exist")
    got = sha256(path)
    if not expected or len(expected) < 16 or not got.startswith(expected.lower()):
        raise Refused(f"{name}: sha256 {got} is not the expected {expected}")
    side = os.path.splitext(path)[0] + ".sha256"
    if os.path.exists(side):
        recorded = open(side).read().split()[0]
        if recorded != got:
            raise Refused(f"{name}: sha256 {got} differs from {side} ({recorded})")
    return got


def check_replay_code(backtest_dir):
    """The engine's replay files are byte-equal to f2c9666 (addendum v5, section 9); exits otherwise."""
    bad = [f for f, want in REPLAY_SHA256.items() if not os.path.exists(os.path.join(backtest_dir, f))
           or sha256(os.path.join(backtest_dir, f)) != want]
    if bad:
        raise Refused(f"replay code in {backtest_dir} differs from f2c9666: {bad}")
    return sorted(REPLAY_SHA256)


# ------------------------------------------------------------------------------------- exclusions

def parse_exclusions(text):
    """The rules of the exclusion list's table: per row the backticked addresses (a mint or a pool),
    the other backticked words (symbols) and the quoted names. A row with none of them is refused."""
    rules = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.lstrip().startswith("|") else []
        if len(cells) < 3 or set(cells[0]) <= set("-: ") or cells[0].lower() == "datum":
            continue
        addresses = B58.findall(cells[1])
        symbols = [w for w in re.findall(r"`([^`]+)`", cells[1]) if w not in addresses]
        names = re.findall(r'"([^"]+)"', cells[1])
        if not (addresses or symbols or names):
            raise Refused(f"exclusion rule without an address, symbol or name: {line}")
        rules.append({"date": cells[0], "addresses": addresses, "symbols": symbols, "names": names, "label": cells[2]})
    if not rules:
        raise Refused("the exclusion list has no rules")
    return rules


def resolve_exclusions(con, rules, store="s"):
    """(mints, pools, per-rule resolution): an address is a mint or a pool; a mint brings its pools and a
    pool its mint; symbols and names match the store's mints (case-insensitive, any creation slot)."""
    mints, pools, per_rule = set(), set(), []
    for r in rules:
        m, p = set(), set()
        for a in r["addresses"]:
            if con.execute(f"SELECT 1 FROM {store}.mints WHERE mint = ?", [a]).fetchone():
                m.add(a)
            if con.execute(f"SELECT 1 FROM {store}.pools WHERE pool = ?", [a]).fetchone():
                p.add(a)
            if a not in m and a not in p:
                m.add(a)  # not in the store: kept as a mint (a position's own mint column still matches)
        for sym in r["symbols"]:
            m |= {x for (x,) in con.execute(f"SELECT mint FROM {store}.mints WHERE upper(trim(symbol)) = upper(?)", [sym]).fetchall()}
        for name in r["names"]:
            m |= {x for (x,) in con.execute(f"SELECT mint FROM {store}.mints WHERE lower(trim(name)) = lower(?)", [name]).fetchall()}
        m |= {x for (x,) in con.execute(f"SELECT DISTINCT mint FROM {store}.pools WHERE pool IN (SELECT unnest(?::VARCHAR[])) AND mint IS NOT NULL",
                                        [sorted(p)]).fetchall()}
        p |= {x for (x,) in con.execute(f"SELECT pool FROM {store}.pools WHERE mint IN (SELECT unnest(?::VARCHAR[]))", [sorted(m)]).fetchall()}
        per_rule.append({**r, "mints": sorted(m), "pools": sorted(p)})
        mints |= m
        pools |= p
    return mints, pools, per_rule


# ------------------------------------------------------------------------------------- statistics

def boot_mean(day_rows, draws=DRAWS, seed=SEED):
    """Mean per position with a 95 % interval from resampling whole days: day_rows = [(sum, count)] (a4c)."""
    a = np.array([r for r in day_rows if r[1] > 0], dtype=float)
    if len(a) == 0:
        return None, None, None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), size=(draws, len(a)))
    s, c = a[idx, 0].sum(1), a[idx, 1].sum(1)
    m = s / np.where(c > 0, c, np.nan)
    return float(a[:, 0].sum() / a[:, 1].sum()), float(np.nanpercentile(m, 2.5)), float(np.nanpercentile(m, 97.5))


def boot_diff(day_rows, draws=DRAWS, seed=SEED):
    """Mean of group 0 - mean of group 1 with a 95 % interval, both groups drawn together per day:
    day_rows = [(sum0, n0, sum1, n1)] over the days with a trade in either group."""
    a = np.array(day_rows, dtype=float)
    if len(a) == 0 or a[:, 1].sum() == 0 or a[:, 3].sum() == 0:
        return None, None, None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), size=(draws, len(a)))
    with np.errstate(invalid="ignore", divide="ignore"):
        d = a[idx, 0].sum(1) / a[idx, 1].sum(1) - a[idx, 2].sum(1) / a[idx, 3].sum(1)
    point = a[:, 0].sum() / a[:, 1].sum() - a[:, 2].sum() / a[:, 3].sum()
    return float(point), float(np.nanpercentile(d, 2.5)), float(np.nanpercentile(d, 97.5))


def decision(h1, h2):
    if h1 and h2:
        return "bevestigd"
    return "risicofilter zonder bewezen winst" if h2 else "verworpen"


def dsr_both(daily, n_trials, var_sr_dev):
    """The H1 series' Sharpe ratio, its PSR against 0 and the DSR against the best of n_trials skill-less
    trials, with SR0 from the dev trials' Sharpe variance and from the null variance 1/T."""
    from wallets.overfit import expected_max_sharpe, moments, psr

    x = np.asarray(daily, dtype=float)
    t = len(x)
    if t < 2 or x.std(ddof=1) == 0:
        return {"t": t, "n_trials": n_trials, "sr": None}
    sr = float(x.mean() / x.std(ddof=1))
    skew, kurt = moments(x)
    sr0_dev, sr0_null = expected_max_sharpe(n_trials, var_sr_dev), expected_max_sharpe(n_trials, 1.0 / t)
    return {"t": t, "n_trials": n_trials, "sr": sr, "skew": skew, "kurt": kurt, "psr0": psr(sr, 0.0, t, skew, kurt),
            "var_sr_dev": var_sr_dev, "sr0_dev": sr0_dev, "dsr_dev": psr(sr, sr0_dev, t, skew, kurt),
            "sr0_null": sr0_null, "dsr_null": psr(sr, sr0_null, t, skew, kurt)}


def matrix(series, dates, strict=False):
    """T x N matrix of daily values: series = {label: {date: value}}, rows the dates (absent: 0). strict: a
    series day that is not a row is refused (a day of another type or definition would turn into zeros)."""
    labels = sorted(series)
    if strict:
        rows = set(dates)
        stray = sorted({str(d) for k in labels for d in series[k] if d not in rows})
        if stray:
            raise Refused(f"series days outside the matrix rows (type or day definition differs): {stray[:5]}")
    return labels, np.array([[float(series[k].get(d, 0.0) or 0.0) for k in labels] for d in dates], dtype=float)


def dev_sharpe_variance(con, dev_parts, pnl):
    """The Sharpe-ratio variance of the 36 dev trials (f7rev-1033-1046): 9 variants x d 1, 2 x farm all /
    organic at 0.5 SOL, q 0.5, overlay none; daily net P&L in SOL, a day without a trade counts 0."""
    from wallets.overfit import sharpe_columns

    series = _config_series(con, os.path.join(dev_parts, "*.parquet"), pnl, "true", (1, 2))
    dates = sorted({d for s in series.values() for d in s})
    _, m = matrix(series, dates)
    if m.shape[1] != 36:
        raise Refused(f"the dev run has {m.shape[1]} of the 36 F7 pool_reversed configurations")
    return float(sharpe_columns(m).var(ddof=1)), len(dates)


def _config_series(con, glob, pnl, keep, ds):
    """{(variant, d, farm): {day: SOL}} for the F7 pool_reversed configurations at the delays ds."""
    out = {}
    for farm, cond in (("all", "true"), ("organic", "farm = 'organic'")):
        for v, d, day, p in con.execute(f"""SELECT variant, d, day, sum({pnl})::DOUBLE / 1e9 FROM read_parquet('{glob}', union_by_name = true)
                                            WHERE {F7REV} AND d IN ({', '.join(map(str, ds))}) AND {cond} AND {keep} GROUP BY ALL""").fetchall():
            out.setdefault((v, d, farm), {})[day] = p
    return out


# ------------------------------------------------------------------------------------- checks

def check_runs(test_cfg, matrix_cfg, meta, delays, dry_run, engine=None):
    """Both runs are the addendum's on this store; returns the decision window (lo, hi)."""
    bad = []
    for name, cfg in (("test", test_cfg), ("matrix", matrix_cfg)):
        bad += [f"{name} {k}: {cfg.get(k)!r} != {v!r}" for k, v in COMMON.items() if cfg.get(k) != v]
    if test_cfg.get("delay_ms") != DELAY_MS:
        bad.append(f"test delay_ms {test_cfg.get('delay_ms')!r} != {DELAY_MS}")
    need = sorted(set(delays.values()) | {1, 2, 3})
    if matrix_cfg.get("delay_ms") is not None or sorted(matrix_cfg.get("delays") or []) != need:
        bad.append(f"matrix delays {matrix_cfg.get('delays')!r} != {need} (1, 2, 3 and the Clock's d for 600 ms)")
    if test_cfg.get("engine_commit") != matrix_cfg.get("engine_commit"):
        bad.append("the runs come from different engine commits")
    window = test_cfg.get("decide_range") or test_cfg.get("slot_range")
    if (matrix_cfg.get("decide_range") or matrix_cfg.get("slot_range")) != window:
        bad.append("the runs decide in different windows")
    if not dry_run:
        if engine and not str(test_cfg.get("engine_commit", "")).startswith(engine):
            bad.append(f"engine {test_cfg.get('engine_commit')} is not the pinned {engine}")
        if meta.get("period") != "clean":
            bad.append(f"store period {meta.get('period')!r} != 'clean'")
        if list(window or []) != list(meta.get("window") or [None]):
            bad.append(f"the runs decide in {window}, the store's window is {meta.get('window')}")
    if bad:
        raise Refused("the runs are not the pre-registered ones: " + "; ".join(bad))
    if window:
        return tuple(window)
    w = meta.get("window")
    return (w[0], w[1]) if w else (None, None)


def epoch_delays(con, lo, hi, store="s"):
    """{epoch: Clock.slots(600 ms)} for the epochs of [lo, hi), from the store's measured slot times."""
    meta = json.loads(con.execute(f"SELECT json FROM {store}.meta").fetchone()[0])
    clock = Clock(meta.get("ms_per_slot"))
    if lo is None:
        lo, hi = meta["slot_start"], meta["slot_end_exclusive"]
    return {e: clock.slots(DELAY_MS, e * EPOCH_SLOTS) for e in range(lo // EPOCH_SLOTS, (hi - 1) // EPOCH_SLOTS + 1)}


def equality(con, test_glob, matrix_glob, delays):
    """Per epoch: the test run's K3 rows against the matrix run's K3 rows at the epoch's d, row for row."""
    out, ok = {}, True
    for e, d in delays.items():
        rng = f"t >= {e * EPOCH_SLOTS} AND t < {(e + 1) * EPOCH_SLOTS}"
        a = f"SELECT * FROM read_parquet('{test_glob}', union_by_name = true) WHERE {K3} AND {rng}"
        b = f"SELECT * FROM read_parquet('{matrix_glob}', union_by_name = true) WHERE {K3} AND {rng} AND d = {d}"
        n1, n2 = (con.execute(f"SELECT count(*) FROM ({q})").fetchone()[0] for q in (a, b))
        only1 = con.execute(f"SELECT count(*) FROM ({a} EXCEPT ALL {b})").fetchone()[0]
        only2 = con.execute(f"SELECT count(*) FROM ({b} EXCEPT ALL {a})").fetchone()[0]
        out[e] = {"d": d, "test_rows": n1, "matrix_rows": n2, "only_test": only1, "only_matrix": only2}
        ok &= only1 == 0 and only2 == 0
    return ok, out


# ------------------------------------------------------------------------------------- the test

def run_a4c(a4c, parts, store, out_json, engine_dir, keep_sql, con):
    """a4c (unchanged, sha256 7e6980c3) with its fourth argument 'holdout', on the run's parts minus the
    excluded positions (written to a parts directory of their own), from the engine snapshot."""
    check_document(a4c, A4C_SHA, "a4c")
    d = os.path.join(os.path.dirname(out_json), "a4c-parts")
    os.makedirs(d, exist_ok=True)
    con.execute(f"COPY (SELECT * FROM read_parquet('{parts}', union_by_name = true) WHERE {keep_sql}) TO '{d}/part-000000.parquet' (FORMAT parquet)")
    env = dict(os.environ, PYTHONPATH=engine_dir)
    r = subprocess.run([sys.executable, a4c, d, store, out_json, "holdout"], cwd=engine_dir, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        raise Refused(f"a4c failed: {r.stderr[-2000:]}")
    return json.load(open(out_json))


def run_test(a, log=print):
    out = {"dry_run": bool(a.dry_run), "documents": {}}
    # 1. documents
    out["documents"]["hypothesis"] = check_document(a.hypothesis, HYPOTHESIS_SHA, "hypothesis")
    if not a.dry_run:
        out["documents"]["addendum"] = check_document(a.addendum, a.addendum_sha256, "addendum")
    out["documents"]["exclusions"] = check_document(a.exclusions, a.exclusions_sha256, "exclusions")
    con = duckdb.connect(config={"memory_limit": os.environ.get("LAB_DUCKDB_MEM", "2GB"), "threads": 2})
    if os.environ.get("LAB_SPILL_DIR"):
        con.execute(f"SET temp_directory='{os.environ['LAB_SPILL_DIR']}'")
    con.execute(f"ATTACH '{a.store}' AS s (READ_ONLY)")
    meta = json.loads(con.execute("SELECT json FROM s.meta").fetchone()[0])
    if not a.dry_run and meta.get("prereg_sha256") != out["documents"]["addendum"]:
        raise Refused(f"the store was built for pre-registration {meta.get('prereg_sha256')}, not the addendum's")
    if not a.dry_run:
        out["documents"]["replay_code_f2c9666"] = check_replay_code(os.path.join(a.engine_dir, "backtest"))
    # 2. runs, window, delays
    test_cfg = json.load(open(os.path.join(a.test_run, "config.json")))
    matrix_cfg = json.load(open(os.path.join(a.matrix_run, "config.json")))
    w = meta.get("window") or [meta["slot_start"], meta["slot_end_exclusive"]]
    delays = epoch_delays(con, w[0], w[1])
    lo, hi = check_runs(test_cfg, matrix_cfg, meta, delays, a.dry_run, a.engine)
    out["window"] = {"slot_from": lo, "slot_to": hi, "warmup_epochs": meta.get("warmup_epochs"), "store_period": meta.get("period"),
                     "store_commit": meta.get("git_commit"), "store_sha256": sha256(a.store) if not a.dry_run else None,
                     "extractor_sha256": meta.get("clean_extractor_sha256"), "engine_commit": test_cfg.get("engine_commit"),
                     "d_per_epoch": delays}
    tg, mg = os.path.join(a.test_run, "parts", "*.parquet"), os.path.join(a.matrix_run, "parts", "*.parquet")
    # 3. equality of the two runs' K3 rows at each epoch's d
    ok, eq = equality(con, tg, mg, delays)
    out["equality"] = eq
    if not ok:
        raise Refused(f"the test run's K3 rows differ from the matrix run's at the Clock's d: {eq}")
    # 4. exclusions, before any outcome
    rules = parse_exclusions(open(a.exclusions).read())
    mints, pools, per_rule = resolve_exclusions(con, rules)
    out["exclusions"] = {"rules": per_rule, "mints": sorted(mints), "pools": sorted(pools)}
    os.makedirs(a.out, exist_ok=True)
    json.dump(out, open(os.path.join(a.out, "exclusions.json"), "w"), indent=1, default=str)
    con.execute("CREATE TEMP TABLE xm AS SELECT unnest(?::VARCHAR[]) AS mint", [sorted(mints)])
    con.execute("CREATE TEMP TABLE xp AS SELECT unnest(?::VARCHAR[]) AS pool", [sorted(pools)])
    keep = "NOT (coalesce(mint IN (SELECT mint FROM xm), false) OR coalesce(pool IN (SELECT pool FROM xp), false))"
    win = f"t >= {lo} AND t < {hi}" if lo is not None else "true"
    pnl, ret = settle_sql(Scenario.from_cfg("base", load_defaults()))
    con.execute(f"""CREATE TEMP TABLE k3all AS SELECT pool, mint, t, trigger_trader, day, entry_failed, {ret} AS ret, {pnl} / 1e9 AS pnl
                    FROM read_parquet('{tg}', union_by_name = true) WHERE {K3} AND {win}""")
    n_all, trig_all = con.execute("SELECT count(*), count(DISTINCT (pool, t)) FROM k3all").fetchone()
    con.execute(f"CREATE TEMP TABLE k3 AS SELECT * FROM k3all WHERE {keep}")
    n_kept, trig_kept = con.execute("SELECT count(*), count(DISTINCT (pool, t)) FROM k3").fetchone()
    out["positions"] = {"k3": n_all, "excluded_positions": n_all - n_kept, "excluded_triggers": trig_all - trig_kept}
    log(f"exclusions: {len(rules)} rules, {len(mints)} mints, {len(pools)} pools; {n_all - n_kept} K3 positions removed")
    # 5. the minimum
    days = con.execute("SELECT count(DISTINCT day) FROM k3 WHERE NOT entry_failed AND ret IS NOT NULL").fetchone()[0]
    out["positions"]["trading_days"] = days
    if not a.dry_run and days < MIN_DAYS:
        out["decision"] = "te weinig dagen"
        json.dump({k: out[k] for k in ("dry_run", "documents", "window", "decision")} | {"trading_days": days},
                  open(os.path.join(a.out, "result.json"), "w"), indent=1, default=str)
        log(f"te weinig dagen: {days} < {MIN_DAYS}; nothing else is computed")
        return out
    # 6. outcomes: the flags on every trigger of both runs (one definition), then H1, H2
    con.execute(f"""CREATE TEMP TABLE trig AS SELECT DISTINCT pool, t, trigger_trader FROM read_parquet('{tg}', union_by_name = true) WHERE {F7REV} AND {win}
                    UNION SELECT DISTINCT pool, t, trigger_trader FROM read_parquet('{mg}', union_by_name = true) WHERE {F7REV} AND {win}""")
    wash.flags(con, "trig", log=log)
    flag = f"LEFT JOIN f_rt USING (pool, t, trigger_trader) LEFT JOIN f_w USING (pool, t)"
    wash1h = f"coalesce(f_w.wash_share, 0) >= {wash.WASH_SHARE}"
    con.execute(f"CREATE TEMP TABLE j AS SELECT k3.*, f_rt.rt_tx, {wash1h} AS wash_1h FROM k3 {flag} WHERE NOT entry_failed AND ret IS NOT NULL")
    f = con.execute("SELECT count(*), count(*) FILTER (WHERE rt_tx), count(*) FILTER (WHERE NOT rt_tx), count(*) FILTER (WHERE rt_tx IS NULL) FROM j").fetchone()
    out["positions"].update({"filled": f[0], "rt_tx": f[1], "no_rt_tx": f[2], "no_trigger_leg": f[3]})
    group = lambda cond: con.execute(f"SELECT sum(ret), count(*) FROM j WHERE {cond} GROUP BY day").fetchall()  # noqa: E731
    m, l1, u1 = boot_mean(group("NOT rt_tx"), a.draws)
    out["H1"] = {"n": f[2], "mean": m, "lo": l1, "hi": u1, "pass": l1 is not None and l1 > 0}
    pairs = con.execute("""SELECT coalesce(sum(ret) FILTER (WHERE NOT rt_tx), 0), count(*) FILTER (WHERE NOT rt_tx),
                                  coalesce(sum(ret) FILTER (WHERE rt_tx), 0), count(*) FILTER (WHERE rt_tx)
                           FROM j WHERE rt_tx IS NOT NULL GROUP BY day""").fetchall()
    d, l2, u2 = boot_diff(pairs, a.draws)
    m_rt = boot_mean(group("rt_tx"), a.draws)
    out["H2"] = {"diff": d, "lo": l2, "hi": u2, "pass": l2 is not None and l2 > 0,
                 "with_rt_tx": {"n": f[1], "mean": m_rt[0], "lo": m_rt[1], "hi": m_rt[2]}}
    out["decision"] = decision(out["H1"]["pass"], out["H2"]["pass"])
    out["wash_1h"] = {k: dict(zip(("mean", "lo", "hi"), boot_mean(group(c), a.draws))) | {"n": sum(n for _, n in group(c))}
                      for k, c in (("wash_1h", "wash_1h"), ("geen wash_1h", "NOT wash_1h"),
                                   ("geen rt_tx en geen wash_1h", "NOT rt_tx AND NOT wash_1h"))}
    # The filter groups at every d of both runs (one flag definition): K3 rows with rt_tx and wash_1h.
    fl = lambda alias: (f"LEFT JOIN f_rt ON f_rt.pool = {alias}.pool AND f_rt.t = {alias}.t AND f_rt.trigger_trader = {alias}.trigger_trader "  # noqa: E731
                        f"LEFT JOIN f_w ON f_w.pool = {alias}.pool AND f_w.t = {alias}.t")
    con.execute(f"""CREATE TEMP TABLE kf AS
    SELECT k.src, k.d, k.day, k.ret, k.pnl, f_rt.rt_tx, coalesce(f_w.wash_share, 0) >= {wash.WASH_SHARE} AS wash_1h
    FROM (SELECT 'test' AS src, pool, t, trigger_trader, d, day, entry_failed, {ret} AS ret, {pnl} / 1e9 AS pnl
          FROM read_parquet('{tg}', union_by_name = true) WHERE {K3} AND {win} AND {keep}
          UNION ALL
          SELECT 'matrix' AS src, pool, t, trigger_trader, d, day, entry_failed, {ret} AS ret, {pnl} / 1e9 AS pnl
          FROM read_parquet('{mg}', union_by_name = true) WHERE {K3} AND {win} AND {keep}) k {fl('k')}
    WHERE NOT k.entry_failed AND k.ret IS NOT NULL""")
    groups = (("zonder rt_tx", "NOT rt_tx", "geen rt_tx"), ("zonder wash_1h", "NOT wash_1h", "geen wash_1h"),
              ("zonder beide", "NOT rt_tx AND NOT wash_1h", "geen rt_tx en geen wash_1h"))
    # a4c itself, unchanged, on the matrix run (d = 1, 2) minus the excluded positions: its filter groups
    # must equal backtest.wash's (count and mean), else the flag is not a4c's on this data.
    if a.a4c:
        rows = run_a4c(a.a4c, mg, a.store, os.path.join(a.out, "a4c-matrix-run.json"), a.engine_dir, keep, con)["rows"]
        out["a4c_matrix_run"] = rows
        diffs = []
        for _, cond, a4c_name in groups:
            for d in (1, 2):
                n, mean = con.execute(f"SELECT count(*), avg(ret) FROM kf WHERE src = 'matrix' AND d = {d} AND {cond}").fetchone()
                ref = next((r for r in rows if r["family"] == "F7" and r["group"] == a4c_name and r["d"] == d), None)
                if ref is None or ref["n"] != n or (n and abs((ref["mean"] or 0) - mean) > 1e-12):
                    diffs.append(f"{a4c_name} d={d}: wash {n} {mean} vs a4c {ref and (ref['n'], ref['mean'])}")
        out["a4c_consistency"] = diffs or "equal"
        if diffs:
            raise Refused(f"backtest.wash's filter groups differ from a4c's on the matrix run: {diffs}")
    # The matrix: the window's UTC dates x 43 columns (absent: 0)
    from wallets.overfit import pbo

    # The window's UTC dates as the parts' `day` (the day of the last block at or before a slot, YYYY-MM-DD):
    # from the block at or before the window's first slot on.
    first = (f"slot >= (SELECT max(slot) FROM s.blocks WHERE block_time IS NOT NULL AND slot <= {lo}) AND slot < {hi}"
             if lo is not None else "true")
    dates = [x for (x,) in con.execute(f"""SELECT DISTINCT CAST(DATE '1970-01-01' + CAST(block_time // 86400 AS INTEGER) AS VARCHAR)
                                          FROM s.blocks WHERE block_time IS NOT NULL AND {first} ORDER BY 1""").fetchall()]
    series = {f"{v}|d{dd}|{farm}": sr for (v, dd, farm), sr in _config_series(con, mg, pnl, f"{keep} AND {win}", (1, 2)).items()}
    for name, cond, _ in groups:
        for src, d_sql, lab in (("test", "true", "d_ms600"), ("matrix", "d = 1", "d1")):
            series[f"K3 {name}|{lab}"] = dict(con.execute(f"SELECT day, sum(pnl) FROM kf WHERE src = '{src}' AND {d_sql} AND {cond} GROUP BY day").fetchall())
    series["K3|clock"] = dict(con.execute("SELECT day, sum(pnl) FROM kf WHERE src = 'test' GROUP BY day").fetchall())
    labels, mtx = matrix(series, dates, strict=True)
    t = len(dates)
    var_dev, dev_days = dev_sharpe_variance(con, a.dev_parts, pnl)
    out["DSR"] = dsr_both(mtx[:, labels.index("K3 zonder rt_tx|d_ms600")], a.trials, var_dev) | {"series": "K3 zonder rt_tx|d_ms600", "dev_days": dev_days}
    out["DSR_K3_clock"] = dsr_both(mtx[:, labels.index("K3|clock")], a.trials, var_dev) | {"series": "K3|clock", "descriptive": True}
    p_ = pbo(mtx, blocks=min(16, t)) if t >= 2 else {}
    out["PBO"] = {"t": t, "n_trials": len(labels), "blocks": p_.get("blocks"), "pbo": p_.get("pbo"), "slope": p_.get("slope"),
                  "prob_loss": p_.get("prob_loss"), "n_splits": p_.get("n_splits"), "indicative_only": t < 10, "labels": labels,
                  "nonzero_cells": int(np.count_nonzero(mtx)), "cells": int(mtx.size)}
    if len(labels) != 43:
        out["PBO"]["warning"] = f"{len(labels)} columns instead of 43 (a configuration without any trade in the window)"
    json.dump(out, open(os.path.join(a.out, "result.json"), "w"), indent=1, default=str)
    with open(os.path.join(a.out, "result.md"), "w") as fh:
        fh.write(report_md(out))
    log(f"{'DRY RUN: ' if a.dry_run else ''}decision: {out['decision']} (result.json, result.md)")
    return out


def report_md(o):
    pct = lambda x: "–" if x is None else f"{x * 100:+.2f} %"  # noqa: E731
    num = lambda x, nd=3: "–" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{nd}f}"  # noqa: E731
    p, w, ds, pb = o["positions"], o["window"], o["DSR"], o["PBO"]
    lines = [
        "# Clean-toets wash-trigger (hypothese 35bdaa0d)" + (" — DRY RUN, geen toets" if o["dry_run"] else ""), "",
        f"Venster: slots [{w['slot_from']}, {w['slot_to']}), warm-up-epochs {w['warmup_epochs']}; d per epoch (600 ms): {w['d_per_epoch']}.",
        f"Engine {w['engine_commit']}; store {w['store_period']} ({w['store_commit']}, sha256 {w['store_sha256']}); extractor {w['extractor_sha256']}.",
        "Documenten (sha256): " + ", ".join(f"{k} {v[:16]}" for k, v in o["documents"].items() if isinstance(v, str)) + "; replay-code byte-gelijk aan f2c9666: "
        + ("ja" if o["documents"].get("replay_code_f2c9666") else "niet gecontroleerd (dry run)") + ".",
        "Gelijkheid toets-run en matrix-run (K3 bij de Clock-d van elk epoch, rij voor rij): "
        + ", ".join(f"{e}: d={v['d']}, {v['test_rows']} = {v['matrix_rows']} rijen" for e, v in o["equality"].items() if v["test_rows"] or v["matrix_rows"]) + ".",
        f"Uitsluitingen: {len(o['exclusions']['mints'])} mints, {len(o['exclusions']['pools'])} pools; {p['excluded_triggers']} triggers, "
        f"{p['excluded_positions']} van {p['k3']} K3-posities vervallen.",
        f"Gevulde K3-trades: {p['filled']} op {p['trading_days']} handelsdagen; met rt_tx {p['rt_tx']}, zonder {p['no_rt_tx']}, zonder trigger-leg {p['no_trigger_leg']}.",
        "",
        "| toets | n | waarde | 95 %-interval (dagbootstrap, 10.000, seed 7) | geslaagd |",
        "|---|---|---|---|---|",
        f"| H1: K3 zonder rt_tx, gemiddeld nettorendement | {o['H1']['n']} | {pct(o['H1']['mean'])} | {pct(o['H1']['lo'])} … {pct(o['H1']['hi'])} | {'ja' if o['H1']['pass'] else 'nee'} |",
        f"| H2: zonder − met rt_tx | | {pct(o['H2']['diff'])} | {pct(o['H2']['lo'])} … {pct(o['H2']['hi'])} | {'ja' if o['H2']['pass'] else 'nee'} |",
        f"| (K3 met rt_tx) | {o['H2']['with_rt_tx']['n']} | {pct(o['H2']['with_rt_tx']['mean'])} | {pct(o['H2']['with_rt_tx']['lo'])} … {pct(o['H2']['with_rt_tx']['hi'])} | |",
        "", f"**Beslissing: {o['decision']}** (reconstructie met het kostenmodel).", "",
        "wash_1h (beschrijvend): " + "; ".join(f"{k} n={v['n']} {pct(v['mean'])} [{pct(v['lo'])} … {pct(v['hi'])}]" for k, v in o["wash_1h"].items()) + ".",
        f"DSR van de H1-reeks (K3 zonder rt_tx bij d_ms 600; N = {ds.get('n_trials')}, T = {ds.get('t')} UTC-dagen van het venster): "
        f"met de Sharpe-variantie van de 36 dev-trials {num(ds.get('dsr_dev'))}, met de nulvariantie 1/T {num(ds.get('dsr_null'))}; "
        f"PSR tegen 0 {num(ds.get('psr0'))}. Beschrijvend, de 43e kolom (K3 bij de Clock-d): DSR {num(o['DSR_K3_clock'].get('dsr_dev'))} / "
        f"{num(o['DSR_K3_clock'].get('dsr_null'))}.",
        f"PBO (CSCV, {pb['n_trials']} kolommen, T = {pb['t']} UTC-dagen, {pb['blocks']} blokken, {pb['nonzero_cells']} van {pb['cells']} cellen niet 0): {num(pb['pbo'])}"
        + (" — slechts indicatief (T < 10)" if pb["indicative_only"] else "") + f"; helling {num(pb['slope'])}, kans op verlies {num(pb['prob_loss'])}"
        + (f"; {pb['warning']}" if pb.get("warning") else "") + ".",
        f"a4c (ongewijzigd) op de matrix-run, d = 1 en 2, tegen backtest.wash: {o.get('a4c_consistency', 'niet gedraaid')}.",
    ]
    if (ds.get("dsr_dev") or 1) < 0.95 or (ds.get("dsr_null") or 1) < 0.95 or (pb["pbo"] or 0) > 0.5:
        lines.append("Voorbehoud: DSR < 0,95 of PBO > 0,5 (de beslisregel blijft H1 én H2).")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--test-run", required=True, help="the run with --delay-ms 600 (config.json, parts/)")
    ap.add_argument("--matrix-run", required=True, help="the run with --delays 1,2,... (the PBO configurations)")
    ap.add_argument("--store", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--hypothesis", required=True)
    ap.add_argument("--addendum")
    ap.add_argument("--addendum-sha256")
    ap.add_argument("--exclusions", required=True)
    ap.add_argument("--exclusions-sha256", required=True)
    ap.add_argument("--trials", type=int, required=True, help="N for the DSR (the addendum: 42)")
    ap.add_argument("--dev-parts", required=True, help="f7rev-1033-1046/parts: the 36 dev trials' Sharpe variance")
    ap.add_argument("--engine", help="the pinned engine commit (prefix); the runs' engine_commit must start with it")
    ap.add_argument("--engine-dir", help="the engine snapshot (its lab/ level) a4c runs from")
    ap.add_argument("--a4c", help="research/blindspots/a4c_f7rev_returns.py (sha256 7e6980c3), run unchanged on the matrix run")
    ap.add_argument("--draws", type=int, default=DRAWS)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not a.dry_run and not (a.addendum and a.addendum_sha256 and a.engine):
        raise SystemExit("pass --addendum, --addendum-sha256 and --engine (the hashed pre-registration and its engine pin)")
    if a.a4c and not a.engine_dir:
        raise SystemExit("--a4c needs --engine-dir")
    run_test(a)


if __name__ == "__main__":
    sys.exit(main())
