"""H3 (research/hypothesis-h3-hold180-2026-10-15.md, sha256 110df1a2): K3's entry with the exit hold180
against tp4_T600 on the clean data (1055-1063), a run of its own beside E20 (clean_test, pin 46a0236) on
E20's store.

  python -m backtest.clean_h3 --run DIR --store FILE --out DIR --hypothesis FILE --exclusions FILE \\
      --exclusions-sha256 SHA --engine SHA --engine-dir DIR --exits-parts DIR [--trials 150]

Order, fail-closed (a failure exits before the next step and nothing after it is computed):
  1. documents: H3's sha256 is 110df1a2... and equals its .sha256 file; the exclusion list's is the one passed
     (and its .sha256 file);
  2. engine: the snapshot's replay files are byte-equal to befacff (sha256 per file), the run's engine commit
     starts with the pin;
  3. store: E20's clean.lock exists (E20 passed its minimum of trading days: the store is final) and the
     store's sha256 is the lock's;
  4. the run: F7 pool_reversed, 0.5 SOL, q 0.5, tau empirical, tolerance 0.05, d_ms 600, f7_grid all, no
     variant filter, decisions in the store's window;
  5. exclusions: resolved with clean_test's rules against the store and written before any outcome; they
     leave every row;
  6. control: per epoch the run's tp4_T600 rows equal E20 run (1)'s K3 rows, row for row (the lock names
     that run); a difference is reported and H3 is not judged;
  7. outcomes: per variant the net P&L in SOL per UTC day of the entry slot (every non-skipped row, failed
     entries' cost included; a day without a position counts 0) over the window's days. H3: the mean daily
     difference hold180 - tp4_T600 with a 95 % interval (bootstrap over days, both series drawn together,
     DRAWS draws, seed 7); it holds when the lower bound is > 0. Descriptive (exits_report's definitions):
     net per filled trade, the paired delta on the common triggers with the dropped triggers per side, the
     empty share, p5/p95, worst day, median hold, exit reasons, also for hold300 and flow60_T600; d and ms
     per slot per epoch. DSR of the hold180 series with N = 150 (variance of the 108 exit trials of
     exits-dev-1033-1046-befacff, and the null 1/T); PBO (CSCV) over the T x 27 matrix, blocks min(16, T).

--dry-run: dev or forward stores only (refused on a 'clean' store), with --e20-run in place of the lock and
--exits-parts any run of the 27 variants; no pin, replay or window checks; for testing the job only.
"""

import argparse
import json
import os
import sys

import duckdb
import numpy as np

from . import clean_test as ct
from .costs import Scenario, settle_sql
from .store import load_defaults
from .strategies import EPOCH_SLOTS, F7_EXIT_GRID, F7_GRID, Clock, f7_exit_variant

H3_SHA = "110df1a2d70ad7aaecaca3997311224cd594ad00ee04d7ffad3cf8993822a381"
# The replay code H3 pins: byte-equal to befacff (the merge of #261 with hold180).
REPLAY_SHA256_BEFACFF = {
    "tournament.py": "624366d6b99802bd349e0390ffb2f7da6bd48fb9cbcb654a443b2b0ce1b6b076",
    "strategies.py": "20f9c4f7efa1798b89528bcb2a847815c744c3a38ef87767cc679eb926193215",
    "replay.py": "310a99d8b82be7dc07b4cd81c1c265b830c19fa632903b8d8bf1af3741d658d4",
    "costs.py": "27db4d6d9d95a0fb5bf797573aa968d01c1858820d5e16b2625eccf056fa7077",
    "wash.py": "8486a0d289ffc826cb96961b243cfb777e1b774bd6231bbd7b221b3a5dcc8440",
    "venues.py": "bce5c80b7f729459c738b989de5e7ef6592f63fbeeb0112b28fb4f349ab13f03",
    "streams.py": "201412f735647789a37a617adfe8e8b18a6ada088832c3bb371bfe0ba152467e",
    "mechanics.py": "3779c94287de2e030482b679cb52ccb907d8ad10cff038b6a00539f9ff392ad0",
    "overlay.py": "1b606ee7320d4cf4efd0aa9a386485f211f3a1cc97eaf362429a2b6ad86a1d65",
    "data.py": "5ea7765cbf8cca4d1c1cd97dac01f87705f2dc3c16e06fd18328af6b072a8709",
}
RUN = {"families": ["F7"], "segments": ["pool_reversed"], "sizes_sol": [0.5], "q": [0.5], "tau": ["empirical"],
       "slippage_tol": 0.05, "delay_ms": 600, "f7_grid": "all", "variants": None}
F7REV = "family = 'F7' AND segment = 'pool_reversed' AND size_sol = 0.5 AND q = 0.5 AND overlay = 'none' AND NOT skipped"
H3_VARIANT, BASELINE = "hold180", "tp4_T600"
CONTEXT = ("hold300", "flow60_T600")
VARIANTS = [f"tp{int(tp * 100)}_T{T}" for tp, T in F7_GRID] + [f7_exit_variant(*g) for g in F7_EXIT_GRID]
DRAWS, SEED, TRIALS = 10_000, 7, 150


def check_replay(backtest_dir):
    """The snapshot's replay files are byte-equal to befacff; exits otherwise."""
    bad = [f for f, want in REPLAY_SHA256_BEFACFF.items() if not os.path.exists(os.path.join(backtest_dir, f))
           or ct.sha256(os.path.join(backtest_dir, f)) != want]
    if bad:
        raise ct.Refused(f"replay code in {backtest_dir} differs from befacff: {bad}")
    return sorted(REPLAY_SHA256_BEFACFF)


def check_lock(store):
    """E20's clean.lock next to the store, with this store's sha256: (lock, store sha256)."""
    lock_path = os.path.join(os.path.dirname(os.path.abspath(store)), "clean.lock")
    if not os.path.exists(lock_path):
        raise ct.Refused("no clean.lock: E20 has not passed its minimum, the store is not final; H3 waits")
    lock = json.load(open(lock_path))
    got = ct.sha256(store)
    if lock.get("store_sha256") != got:
        raise ct.Refused(f"the store's sha256 {got} is not clean.lock's {lock.get('store_sha256')}")
    return lock, got


def check_run(cfg, meta, engine, dry_run=False):
    bad = [f"{k}: {cfg.get(k)!r} != {v!r}" for k, v in RUN.items() if cfg.get(k) != v]
    if dry_run:
        if bad:
            raise ct.Refused("the run is not H3's: " + "; ".join(bad))
        return tuple(cfg.get("decide_range") or cfg.get("slot_range"))
    if not str(cfg.get("engine_commit", "")).startswith(engine or "?"):
        bad.append(f"engine {cfg.get('engine_commit')} is not the pin {engine}")
    if meta.get("period") != "clean":
        bad.append(f"store period {meta.get('period')!r}")
    if list(cfg.get("decide_range") or []) != list(meta.get("window") or [None]):
        bad.append(f"decide_range {cfg.get('decide_range')} is not the store's window {meta.get('window')}")
    if bad:
        raise ct.Refused("the run is not H3's: " + "; ".join(bad))
    return tuple(meta["window"])


def equality(con, h3_glob, e20_glob, keep, lo, hi):
    """Per epoch: the run's tp4_T600 rows against E20 run (1)'s K3 rows (both after the exclusions)."""
    out, ok = {}, True
    for e in range(lo // EPOCH_SLOTS, (hi - 1) // EPOCH_SLOTS + 1):
        rng = f"t >= {max(lo, e * EPOCH_SLOTS)} AND t < {min(hi, (e + 1) * EPOCH_SLOTS)}"
        a = f"SELECT * FROM read_parquet('{h3_glob}', union_by_name = true) WHERE {ct.K3} AND {rng} AND {keep}"
        b = f"SELECT * FROM read_parquet('{e20_glob}', union_by_name = true) WHERE {ct.K3} AND {rng} AND {keep}"
        n1, n2 = (con.execute(f"SELECT count(*) FROM ({q})").fetchone()[0] for q in (a, b))
        o1 = con.execute(f"SELECT count(*) FROM ({a} EXCEPT ALL {b})").fetchone()[0]
        o2 = con.execute(f"SELECT count(*) FROM ({b} EXCEPT ALL {a})").fetchone()[0]
        out[e] = {"h3_rows": n1, "e20_rows": n2, "only_h3": o1, "only_e20": o2}
        ok &= o1 == 0 and o2 == 0
    return ok, out


def boot_diff_days(x, y, draws=DRAWS, seed=SEED):
    """Mean of x - y over days with a 95 % interval, the days drawn with replacement (both series together)."""
    d = np.asarray(x, dtype=float) - np.asarray(y, dtype=float)
    if len(d) < 2:
        return (float(d.mean()) if len(d) else None), None, None
    idx = np.random.default_rng(seed).integers(0, len(d), size=(draws, len(d)))
    m = d[idx].mean(1)
    return float(d.mean()), float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def entry_days(con, glob, keep, win, pnl):
    """TEMP table h: the run's non-skipped F7 pool_reversed rows in the window minus the exclusions, with the
    net P&L in SOL and the UTC day of the entry slot (the last block at or before it; the trigger slot when a
    failed entry has none)."""
    con.execute("CREATE OR REPLACE TEMP TABLE bt AS SELECT slot, block_time FROM s.blocks WHERE block_time IS NOT NULL ORDER BY slot")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE h0 AS
        SELECT variant, d, t, pool, mint, entry_failed, coalesce(entry_slot, t) AS es, {pnl} / 1e9 AS pnl
        FROM read_parquet('{glob}', union_by_name = true) WHERE {F7REV} AND {win} AND {keep}""")
    con.execute("""CREATE OR REPLACE TEMP TABLE h AS
        SELECT h0.*, CAST(DATE '1970-01-01' + CAST(bt.block_time // 86400 AS INTEGER) AS VARCHAR) AS eday
        FROM h0 ASOF JOIN bt ON h0.es >= bt.slot""")


def run_h3(a, log=print):
    out = {"dry_run": bool(a.dry_run), "documents": {}}
    # 1. documents
    out["documents"]["hypothesis"] = ct.check_document(a.hypothesis, H3_SHA, "H3", sidecar=True)
    out["documents"]["exclusions"] = ct.check_document(a.exclusions, a.exclusions_sha256, "exclusions", sidecar=not a.dry_run)
    # 2. engine
    if not a.dry_run:
        out["documents"]["replay_code_befacff"] = check_replay(os.path.join(a.engine_dir, "backtest"))
    con = duckdb.connect(config={"memory_limit": os.environ.get("LAB_DUCKDB_MEM", "2GB"), "threads": 2})
    if os.environ.get("LAB_SPILL_DIR"):
        con.execute(f"SET temp_directory='{os.environ['LAB_SPILL_DIR']}'")
    # 3. store
    con.execute(f"ATTACH '{a.store}' AS s (READ_ONLY)")
    meta = json.loads(con.execute("SELECT json FROM s.meta").fetchone()[0])
    if a.dry_run:
        if meta.get("period") == "clean" or meta.get("slot_end_exclusive", 0) > ct.CLEAN[0]:
            raise ct.Refused("a dry run never reads the clean data (store period 'clean' or slots in 1055-1063)")
        lock, store_sha = {"test_run": a.e20_run}, None
    else:
        lock, store_sha = check_lock(a.store)
    # 4. the run
    cfg = json.load(open(os.path.join(a.run, "config.json")))
    lo, hi = check_run(cfg, meta, a.engine, a.dry_run)
    clock = Clock(meta.get("ms_per_slot"))
    epochs = range(lo // EPOCH_SLOTS, (hi - 1) // EPOCH_SLOTS + 1)
    out["window"] = {"slot_from": lo, "slot_to": hi, "store_sha256": store_sha, "engine_commit": cfg.get("engine_commit"),
                     "extractor_sha256": meta.get("clean_extractor_sha256"), "e20_test_run": lock.get("test_run"),
                     "d_per_epoch": {e: clock.slots(600, e * EPOCH_SLOTS) for e in epochs},
                     "ms_per_slot_per_epoch": {e: clock.ms_per_slot(e * EPOCH_SLOTS) for e in epochs}}
    # 5. exclusions, before any outcome
    rules = ct.parse_exclusions(open(a.exclusions).read())
    mints, pools, per_rule = ct.resolve_exclusions(con, rules)
    out["exclusions"] = {"rules": per_rule, "mints": sorted(mints), "pools": sorted(pools)}
    os.makedirs(a.out, exist_ok=True)
    json.dump(out, open(os.path.join(a.out, "exclusions.json"), "w"), indent=1, default=str)
    con.execute("CREATE TEMP TABLE xm AS SELECT unnest(?::VARCHAR[]) AS mint", [sorted(mints)])
    con.execute("CREATE TEMP TABLE xp AS SELECT unnest(?::VARCHAR[]) AS pool", [sorted(pools)])
    keep = "NOT (coalesce(mint IN (SELECT mint FROM xm), false) OR coalesce(pool IN (SELECT pool FROM xp), false))"
    win = f"t >= {lo} AND t < {hi}"
    hg = os.path.join(a.run, "parts", "*.parquet")
    # 6. the control against E20 run (1)
    ok, eq = equality(con, hg, os.path.join(lock["test_run"], "parts", "*.parquet"), keep, lo, hi)
    out["control"] = eq
    if not ok:
        json.dump(out, open(os.path.join(a.out, "result.json"), "w"), indent=1, default=str)
        raise ct.Refused(f"the run's tp4_T600 rows differ from E20 run (1)'s K3 rows: {eq}; H3 is not judged before this is explained")
    log(f"control: tp4_T600 equals E20's K3 per epoch; exclusions {len(mints)} mints, {len(pools)} pools")
    # 7. outcomes
    pnl, ret = settle_sql(Scenario.from_cfg("base", load_defaults()))
    entry_days(con, hg, keep, win, pnl)
    first = f"slot >= (SELECT max(slot) FROM bt WHERE slot <= {lo}) AND slot <= greatest({hi}, (SELECT max(es) FROM h))"
    dates = [x for (x,) in con.execute(f"""SELECT DISTINCT CAST(DATE '1970-01-01' + CAST(block_time // 86400 AS INTEGER) AS VARCHAR)
                                          FROM bt WHERE {first} ORDER BY 1""").fetchall()]
    series = {v: dict(con.execute("SELECT eday, sum(pnl) FROM h WHERE variant = ? GROUP BY 1", [v]).fetchall()) for v in VARIANTS}
    labels, mtx = ct.matrix(series, dates, strict=True)
    col = {v: mtx[:, labels.index(v)] for v in labels}
    m, l3, u3 = boot_diff_days(col[H3_VARIANT], col[BASELINE], a.draws)
    out["H3"] = {"days": len(dates), "draws": a.draws, "mean_daily_diff_sol": m, "lo": l3, "hi": u3, "pass": l3 is not None and l3 > 0,
                 "hold180_sol_per_day": float(col[H3_VARIANT].mean()), "tp4_T600_sol_per_day": float(col[BASELINE].mean()),
                 "series": {d: [float(col[H3_VARIANT][i]), float(col[BASELINE][i])] for i, d in enumerate(dates)}}
    out["decision"] = ("hold180 beter dan K3 op schone data (reconstructie met het kostenmodel)" if out["H3"]["pass"]
                       else "verworpen")
    counts = con.execute("""SELECT variant, count(*), count(*) FILTER (WHERE entry_failed), round(sum(pnl), 4) FROM h
                            WHERE variant IN (?, ?) GROUP BY 1""", [H3_VARIANT, BASELINE]).fetchall()
    out["positions"] = {v: {"rows": n, "failed_entries": f, "net_sol": s} for v, n, f, s in counts}
    out["descriptive"] = describe(con, hg, keep, win, a)
    # DSR and PBO
    from wallets.overfit import day_matrix, pbo, sharpe_columns

    trials = day_matrix(con, a.exits_parts, load_defaults(), family="F7",
                        where="segment = 'pool_reversed' AND size_sol = 0.5 AND q = 0.5 AND overlay = 'none'")
    dev = trials.get("F7", ([], [], np.zeros((0, 0))))[2]
    if dev.shape[1] != 108:
        raise ct.Refused(f"exits-dev has {dev.shape[1]} of the 108 exit trials")
    out["DSR"] = ct.dsr_both(col[H3_VARIANT], a.trials, float(sharpe_columns(dev).var(ddof=1))) | {"series": H3_VARIANT}
    t = len(dates)
    p = pbo(mtx, blocks=min(16, t)) if t >= 2 else {}
    out["PBO"] = {"t": t, "n_columns": len(labels), "blocks": p.get("blocks"), "pbo": p.get("pbo"), "slope": p.get("slope"),
                  "prob_loss": p.get("prob_loss"), "n_splits": p.get("n_splits"), "indicative_only": t < 10, "selected": H3_VARIANT,
                  "zero_columns": sorted(v for v in labels if not series.get(v))}
    json.dump(out, open(os.path.join(a.out, "result.json"), "w"), indent=1, default=str)
    with open(os.path.join(a.out, "result.md"), "w") as fh:
        fh.write(report_md(out))
    log(f"{'DRY RUN: ' if a.dry_run else ''}H3: {out['decision']} (result.json, result.md)")
    return out


def describe(con, glob, keep, win, a):
    """exits_report's per-variant stats and the paired delta against tp4_T600 on the common triggers, farm all,
    on the run's rows minus the exclusions (written to parts of their own)."""
    from wallets import exits_report as er

    d = os.path.join(a.out, "h3-parts")
    os.makedirs(d, exist_ok=True)
    con.execute(f"COPY (SELECT * FROM read_parquet('{glob}', union_by_name = true) WHERE {F7REV} AND {win} AND {keep}) "
                f"TO '{d}/part-000000.parquet' (FORMAT parquet)")
    c2 = duckdb.connect(config={"memory_limit": os.environ.get("LAB_DUCKDB_MEM", "2GB"), "threads": 2})
    er.load(c2, d, os.environ.get("LAB_DEFAULTS", er.DEFAULT_CFG), baseline=BASELINE)
    out = {}
    for dd in [r[0] for r in c2.execute("SELECT DISTINCT d FROM pos ORDER BY 1").fetchall()]:
        for v in (BASELINE, H3_VARIANT, *CONTEXT):
            s = er.stats(c2, v, dd, "all", "TRUE", a.draws)
            out[f"{v}|d{dd}"] = {"stats": s, "paired": None if v == BASELINE else er.paired(c2, v, dd, "all", "TRUE", a.draws, BASELINE),
                                 "exit_reasons": dict(c2.execute("SELECT exit_reason, count(*) FROM pos WHERE variant = ? AND d = ? AND farm = 'all' GROUP BY 1",
                                                                 [v, dd]).fetchall())}
    return out


def report_md(o):
    sol = lambda x: "–" if x is None else f"{x:+.3f}"  # noqa: E731
    num = lambda x, nd=3: "–" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{nd}f}"  # noqa: E731
    h, w, ds, pb = o["H3"], o["window"], o["DSR"], o["PBO"]
    lines = [
        "# H3: hold180 tegen tp4_T600 op schone data (hypothese 110df1a2)" + (" — DRY RUN, geen toets" if o.get("dry_run") else ""), "",
        f"Venster: slots [{w['slot_from']}, {w['slot_to']}), {h['days']} UTC-dagen; d per epoch {w['d_per_epoch']}; ms per slot {w['ms_per_slot_per_epoch']}.",
        (f"Engine {w['engine_commit']} (replay byte-gelijk aan befacff); store sha256 {w['store_sha256']} (= clean.lock van E20); extractor {w['extractor_sha256']}."
         if not o.get("dry_run") else f"Engine {w['engine_commit']}; pin, replay en lock niet gecontroleerd (dry run)."),
        "Controle: tp4_T600 per epoch rij voor rij gelijk aan de K3-rijen van E20-run (1): "
        + ", ".join(f"{e}: {v['h3_rows']} = {v['e20_rows']}" for e, v in o["control"].items()) + ".",
        f"Uitsluitingen: {len(o['exclusions']['mints'])} mints, {len(o['exclusions']['pools'])} pools.", "",
        "| reeks | Σ netto SOL per dag (gemiddeld) |", "|---|---|",
        f"| hold180 | {sol(h['hold180_sol_per_day'])} |", f"| tp4_T600 | {sol(h['tp4_T600_sol_per_day'])} |", "",
        f"**H3: gemiddeld dagverschil hold180 − tp4_T600 = {sol(h['mean_daily_diff_sol'])} SOL, 95 %-interval {sol(h['lo'])} … {sol(h['hi'])} "
        f"(dagbootstrap, {h['draws']:,} trekkingen, seed 7): {'geslaagd' if h['pass'] else 'niet geslaagd'}. Beslissing: {o['decision']}.**", "",
        f"DSR van de hold180-reeks (N = {ds.get('n_trials')}, T = {ds.get('t')}): met de variantie van de 108 exit-trials {num(ds.get('dsr_dev'))}, "
        f"met 1/T {num(ds.get('dsr_null'))}; PSR tegen 0 {num(ds.get('psr0'))}.",
        f"PBO (CSCV, {pb['n_columns']} kolommen, T = {pb['t']}, {pb['blocks']} blokken): {num(pb['pbo'])}" + (" — slechts indicatief (T < 10)" if pb["indicative_only"] else "") + ".",
        "", "Beschrijvend (exits_report-definities, farm alle): zie result.json `descriptive` (netto per gevulde trade, gepaarde Δ op de "
        "gemeenschappelijke triggers met de weggevallen triggers per kant, leeg-aandeel, p5/p95, slechtste dag, mediane hold, exit-redenen; "
        "ook hold300 en flow60_T600).",
    ]
    if (ds.get("dsr_dev") or 1) < 0.95 or (ds.get("dsr_null") or 1) < 0.95 or (pb["pbo"] or 0) > 0.5:
        lines.append("Voorbehoud: DSR < 0,95 of PBO > 0,5 (de beslisregel blijft H3).")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", required=True, help="the H3 run (config.json, parts/)")
    ap.add_argument("--store", required=True, help="E20's clean store (its clean.lock next to it)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--hypothesis", required=True)
    ap.add_argument("--exclusions", required=True)
    ap.add_argument("--exclusions-sha256", required=True)
    ap.add_argument("--engine", help="the H3 pin (prefix); the run's engine_commit must start with it")
    ap.add_argument("--engine-dir", required=True, help="the run's engine snapshot (its lab/ level)")
    ap.add_argument("--exits-parts", required=True, help="exits-dev-1033-1046-befacff/parts: the 108 exit trials' Sharpe variance")
    ap.add_argument("--trials", type=int, default=TRIALS)
    ap.add_argument("--draws", type=int, default=DRAWS)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--e20-run", help="dry run: E20's test run (in place of the lock's)")
    a = ap.parse_args()
    if not a.dry_run and not a.engine:
        raise SystemExit("pass --engine (the H3 pin)")
    if a.dry_run and not a.e20_run:
        raise SystemExit("a dry run needs --e20-run")
    run_h3(a)


if __name__ == "__main__":
    sys.exit(main())
