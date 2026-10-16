"""A clean-data hypothesis on F7's entry with one exit variant, as a NAS job after E20 (and H3, H4): the engine behind
clean_h5 (and later ones), clean_h4's job with its constants in a Spec. Each hypothesis has a module of its own with
its Spec (the hypothesis file's sha256, the variant, the grid, the trials) and a main(); nas_clean_hx.sh runs it.

  python -m backtest.clean_h5 --run DIR --store FILE --out DIR --hypothesis FILE --exclusions FILE \\
      --exclusions-sha256 SHA --engine SHA --engine-dir DIR --trials-parts DIR [--trials N]

Order, fail-closed (a failure exits before the next step and nothing after it is computed):
  1. documents: the hypothesis file's sha256 is the Spec's and equals its .sha256 file; the exclusion list's is the
     one passed (and its .sha256 file);
  2. engine: the snapshot's replay files are byte-equal to the Spec's (the pin's); the run's engine commit starts
     with the pin;
  3. store: E20's clean.lock exists (E20 passed its minimum of trading days: the store is final) and the store's
     sha256 is the lock's;
  4. the run: F7 pool_reversed, 0.5 SOL, q 0.5, tau empirical, tolerance 0.05, d_ms 600, the Spec's f7_grid, no
     variant filter, decisions in the store's window;
  5. exclusions: resolved with clean_test's rules against the store and written before any outcome;
  6. control: per epoch the run's tp4_T600 rows (farm all) equal E20 run (1)'s K3 rows on their common columns;
     a difference is reported and the hypothesis is not judged;
  7. outcomes: per variant and farm the net P&L in SOL per UTC day of the entry slot (every non-skipped row, failed
     entries' cost included; a day without a position counts 0) over the window's days. The test: the mean daily
     net SOL of the Spec's variant on its farm group with a 95 % interval (bootstrap over days, DRAWS draws, seed 7);
     it holds when the lower bound is > 0; with fewer than min_days days with such a position it is not judged.
     Descriptive: the same series for the Spec's other variants and for flagged and all, the daily difference to
     tp4_T600, the worst day, the largest position and the shares of the 10 best positions, the 10 best pools and the
     best 1 % of the positions, the exit reasons and the take-profit exits' proceeds / cost (p5, median, p95), the
     daily sums per regime (more than one), the triggers shared with common_with and both sums on them,
     exits_report's stats with the runners / drains / collapses / rest split; DSR with N = trials (the Sharpe variance of the dev trials
     trials_where in --trials-parts, and the null 1/T); PBO (CSCV) over the run's T x k matrix on the farm group.

--dry-run: dev or forward stores only (refused on a 'clean' store), with --e20-run in place of the lock; no pin,
replay or window checks; for testing the job only.
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass

import duckdb
import numpy as np

from . import clean_test as ct
from .costs import Scenario, settle_sql
from .store import load_defaults
from .strategies import EPOCH_SLOTS, F7_BIG_GRID, F7_EXIT_GRID, F7_GRID, F7_LONG_GRID, Clock, f7_exit_variant

F7REV = ct.F7REV
BASELINE = "tp4_T600"
DRAWS, SEED, TOP = 10_000, 7, 10
FARMS = ("organic", "flagged", "all")
GRIDS = {"v1": [f"tp{int(tp * 100)}_T{T}" for tp, T in F7_GRID], "exits": [f7_exit_variant(*g) for g in F7_EXIT_GRID],
         "long": [f7_exit_variant(*g) for g in F7_LONG_GRID], "big": [f7_exit_variant(*g) for g in F7_BIG_GRID]}


def grid_variants(f7_grid):
    """The F7 variants a run with this --f7-grid builds, in the tournament's order."""
    names = f7_grid.split(",")
    return [v for g in ("v1", "exits", "long", "big") if g in names for v in GRIDS[g]]


@dataclass(frozen=True)
class Spec:
    name: str  # "H5"
    hypothesis: str  # the file's name under research/
    sha: str  # its sha256
    variant: str  # the tested series
    replay_sha256: dict  # the pin's replay files (sha256 per file)
    trials: int  # N of the DSR
    trials_run: str  # the dev run whose parts give the trials' Sharpe variance (backtests/<run>/parts)
    trials_columns: int  # how many trials that is (checked)
    trials_where: str = "segment = 'pool_reversed' AND size_sol = 0.5 AND q = 0.5 AND overlay = 'none'"
    farm: str = "organic"
    f7_grid: str = "v1,exits,long,big"
    min_days: int = 5
    describe: tuple = ("tp4_T600", "hold180", "flow300_H6")  # descriptive series beside the variant
    claim: str = ""  # the decision's sentence when the test holds
    common_with: str = None  # descriptive: the triggers the variant shares with this one, and both sums on them

    @property
    def variants(self):
        return grid_variants(self.f7_grid)

    @property
    def run(self):
        return {"families": ["F7"], "segments": ["pool_reversed"], "sizes_sol": [0.5], "q": [0.5], "tau": ["empirical"],
                "slippage_tol": 0.05, "delay_ms": 600, "f7_grid": self.f7_grid, "variants": None}

    def check(self):
        assert len(self.sha) == 64 and self.variant in self.variants and BASELINE in self.variants
        assert all(v in self.variants for v in self.describe) and self.farm in FARMS
        assert self.common_with is None or self.common_with in self.variants
        assert set(self.replay_sha256) == set(ct.REPLAY_SHA256)
        return self


def check_replay(backtest_dir, spec):
    """The snapshot's replay files are byte-equal to the pin's; exits otherwise."""
    bad = [f for f, want in spec.replay_sha256.items() if not os.path.exists(os.path.join(backtest_dir, f))
           or ct.sha256(os.path.join(backtest_dir, f)) != want]
    if bad:
        raise ct.Refused(f"replay code in {backtest_dir} differs from the {spec.name} pin's: {bad}")
    return sorted(spec.replay_sha256)


def check_lock(store, name):
    """E20's clean.lock next to the store, with this store's sha256: (lock, store sha256)."""
    lock_path = os.path.join(os.path.dirname(os.path.abspath(store)), "clean.lock")
    if not os.path.exists(lock_path):
        raise ct.Refused(f"no clean.lock: E20 has not passed its minimum, the store is not final; {name} waits")
    lock = json.load(open(lock_path))
    got = ct.sha256(store)
    if lock.get("store_sha256") != got:
        raise ct.Refused(f"the store's sha256 {got} is not clean.lock's {lock.get('store_sha256')}")
    return lock, got


def check_run(cfg, meta, engine, spec, dry_run=False):
    bad = [f"{k}: {cfg.get(k)!r} != {v!r}" for k, v in spec.run.items() if cfg.get(k) != v]
    if dry_run:
        if bad:
            raise ct.Refused(f"the run is not {spec.name}'s: " + "; ".join(bad))
        return tuple(cfg.get("decide_range") or cfg.get("slot_range"))
    if not str(cfg.get("engine_commit", "")).startswith(engine or "?"):
        bad.append(f"engine {cfg.get('engine_commit')} is not the pin {engine}")
    if meta.get("period") != "clean":
        bad.append(f"store period {meta.get('period')!r}")
    if list(cfg.get("decide_range") or []) != list(meta.get("window") or [None]):
        bad.append(f"decide_range {cfg.get('decide_range')} is not the store's window {meta.get('window')}")
    if bad:
        raise ct.Refused(f"the run is not {spec.name}'s: " + "; ".join(bad))
    return tuple(meta["window"])


def common_columns(con, a_glob, b_glob):
    """The columns both part sets carry, in the first set's order (E20's engine writes fewer than this one's)."""
    cols = lambda g: [c[0] for c in con.execute(f"SELECT * FROM read_parquet('{g}', union_by_name = true) LIMIT 0").description]  # noqa: E731
    b = set(cols(b_glob))
    return [c for c in cols(a_glob) if c in b]


def equality(con, run_glob, e20_glob, keep, lo, hi):
    """Per epoch: the run's tp4_T600 rows (farm all) against E20 run (1)'s K3 rows on their common columns (both after
    the exclusions)."""
    cols = ", ".join(f'"{c}"' for c in common_columns(con, run_glob, e20_glob))
    out, ok = {}, True
    for e in range(lo // EPOCH_SLOTS, (hi - 1) // EPOCH_SLOTS + 1):
        rng = f"t >= {max(lo, e * EPOCH_SLOTS)} AND t < {min(hi, (e + 1) * EPOCH_SLOTS)}"
        a = f"SELECT {cols} FROM read_parquet('{run_glob}', union_by_name = true) WHERE {ct.K3} AND {rng} AND {keep}"
        b = f"SELECT {cols} FROM read_parquet('{e20_glob}', union_by_name = true) WHERE {ct.K3} AND {rng} AND {keep}"
        n1, n2 = (con.execute(f"SELECT count(*) FROM ({q})").fetchone()[0] for q in (a, b))
        o1 = con.execute(f"SELECT count(*) FROM ({a} EXCEPT ALL {b})").fetchone()[0]
        o2 = con.execute(f"SELECT count(*) FROM ({b} EXCEPT ALL {a})").fetchone()[0]
        out[e] = {"run_rows": n1, "e20_rows": n2, "only_run": o1, "only_e20": o2}
        ok &= o1 == 0 and o2 == 0
    return ok, out


def boot_mean_days(x, draws=DRAWS, seed=SEED):
    """Mean of a daily series with a 95 % interval, the days drawn with replacement."""
    x = np.asarray(x, dtype=float)
    if len(x) < 2:
        return (float(x.mean()) if len(x) else None), None, None
    idx = np.random.default_rng(seed).integers(0, len(x), size=(draws, len(x)))
    m = x[idx].mean(1)
    return float(x.mean()), float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def entry_days(con, glob, keep, win, pnl):
    """TEMP table h: the run's non-skipped F7 pool_reversed rows in the window minus the exclusions, with the trigger's
    farm label, the net P&L in SOL and the UTC day of the entry slot (the last block at or before it; the trigger slot
    when a failed entry has none)."""
    con.execute("CREATE OR REPLACE TEMP TABLE bt AS SELECT slot, block_time FROM s.blocks WHERE block_time IS NOT NULL ORDER BY slot")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE h0 AS
        SELECT variant, d, t, pool, mint, farm, regime, entry_failed, exit_reason, proceeds, cost, coalesce(entry_slot, t) AS es,
               {pnl} / 1e9 AS pnl
        FROM read_parquet('{glob}', union_by_name = true) WHERE {F7REV} AND {win} AND {keep}""")
    con.execute("""CREATE OR REPLACE TEMP TABLE h AS
        SELECT h0.*, CAST(DATE '1970-01-01' + CAST(bt.block_time // 86400 AS INTEGER) AS VARCHAR) AS eday
        FROM h0 ASOF JOIN bt ON h0.es >= bt.slot""")


def daily(con, variant, farm):
    """{UTC day: net SOL} of a variant on the triggers of a farm group ('all': every trigger)."""
    where = "TRUE" if farm == "all" else f"farm = '{farm}'"
    return dict(con.execute(f"SELECT eday, sum(pnl) FROM h WHERE variant = ? AND {where} GROUP BY 1", [variant]).fetchall())


def concentration(con, variant, farm, k=TOP):
    """The series' net SOL and the shares of it of the k best positions, the k best pools (net per pool) and the best
    1 % of the positions (at least one), plus the largest position (None as a share when the net is not > 0)."""
    where = f"variant = '{variant}'" + ("" if farm == "all" else f" AND farm = '{farm}'")
    n, total, best = con.execute(f"SELECT count(*), sum(pnl), max(pnl) FROM h WHERE {where}").fetchone()
    k1 = max(1, -(-n // 100)) if n else 0
    top = lambda q: con.execute(f"SELECT sum(x) FROM ({q})").fetchone()[0]  # noqa: E731
    parts = {f"top{k}_positions": top(f"SELECT pnl AS x FROM h WHERE {where} ORDER BY pnl DESC LIMIT {k}"),
             f"top{k}_pools": top(f"SELECT sum(pnl) AS x FROM h WHERE {where} GROUP BY pool ORDER BY x DESC LIMIT {k}"),
             "top1pct_positions": top(f"SELECT pnl AS x FROM h WHERE {where} ORDER BY pnl DESC LIMIT {k1}"),
             "largest_position": best}
    share = lambda x: (x / total) if x is not None and total and total > 0 else None  # noqa: E731
    return {"positions": n, "net_sol": total, "top1pct_n": k1, **{f"{key}_sol": v for key, v in parts.items()},
            **{f"{key}_share": share(v) for key, v in parts.items()}}


def tp_exits(con, variant, farm):
    """The take-profit exits' proceeds / cost (both gross, lamports): n, p5, median, p95."""
    where = f"variant = '{variant}'" + ("" if farm == "all" else f" AND farm = '{farm}'")
    n, p5, med, p95 = con.execute(f"""SELECT count(*), quantile_cont(r, 0.05), median(r), quantile_cont(r, 0.95) FROM
        (SELECT proceeds::DOUBLE / cost AS r FROM h WHERE {where} AND exit_reason = 'take_profit' AND NOT entry_failed AND cost > 0)""").fetchone()
    return {"n": n, "p5": p5, "median": med, "p95": p95}


def common_triggers(con, variant, other, farm):
    """The triggers (pool, t, d) both variants took on a farm group, and each one's net SOL on them; the others per side."""
    where = "TRUE" if farm == "all" else f"farm = '{farm}'"
    n, a, b = con.execute(f"""SELECT count(*), sum(x.pnl), sum(y.pnl) FROM (SELECT * FROM h WHERE variant = ? AND {where}) x
                              JOIN (SELECT * FROM h WHERE variant = ? AND {where}) y USING (pool, t, d)""", [variant, other]).fetchone()
    na, nb = (con.execute(f"SELECT count(*) FROM h WHERE variant = ? AND {where}", [v]).fetchone()[0] for v in (variant, other))
    return {"other": other, "farm": farm, "common": n, f"{variant}_sol": a, f"{other}_sol": b, f"only_{variant}": na - n, f"only_{other}": nb - n}


def run_hx(a, spec, log=print):
    out = {"hypothesis": spec.name, "dry_run": bool(a.dry_run), "documents": {}}
    # 1. documents
    out["documents"]["hypothesis"] = ct.check_document(a.hypothesis, spec.sha, spec.name, sidecar=True)
    out["documents"]["exclusions"] = ct.check_document(a.exclusions, a.exclusions_sha256, "exclusions", sidecar=not a.dry_run)
    # 2. engine
    if not a.dry_run:
        out["documents"]["replay_code"] = check_replay(os.path.join(a.engine_dir, "backtest"), spec)
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
        lock, store_sha = check_lock(a.store, spec.name)
    # 4. the run
    cfg = json.load(open(os.path.join(a.run, "config.json")))
    lo, hi = check_run(cfg, meta, a.engine, spec, a.dry_run)
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
        raise ct.Refused(f"the run's tp4_T600 rows differ from E20 run (1)'s K3 rows: {eq}; {spec.name} is not judged before "
                         "this is explained")
    log(f"control: tp4_T600 equals E20's K3 per epoch; exclusions {len(mints)} mints, {len(pools)} pools")
    # 7. outcomes
    pnl, _ = settle_sql(Scenario.from_cfg("base", load_defaults()))
    entry_days(con, hg, keep, win, pnl)
    first = f"slot >= (SELECT max(slot) FROM bt WHERE slot <= {lo}) AND slot <= greatest({hi}, (SELECT max(es) FROM h))"
    dates = [x for (x,) in con.execute(f"""SELECT DISTINCT CAST(DATE '1970-01-01' + CAST(block_time // 86400 AS INTEGER) AS VARCHAR)
                                          FROM bt WHERE {first} ORDER BY 1""").fetchall()]
    v0, farm = spec.variant, spec.farm
    series = {v: daily(con, v, farm) for v in spec.variants}
    labels, mtx = ct.matrix(series, dates, strict=True)
    col = {v: mtx[:, labels.index(v)] for v in labels}
    m, lo_, hi_ = boot_mean_days(col[v0], a.draws)
    where = "TRUE" if farm == "all" else f"farm = '{farm}'"
    pos_days = con.execute(f"SELECT count(DISTINCT eday) FROM h WHERE variant = ? AND {where}", [v0]).fetchone()[0]
    judged = pos_days >= spec.min_days
    out["test"] = {"days": len(dates), "days_with_a_position": pos_days, "judged": judged, "draws": a.draws, "series": v0,
                   "farm": farm, "mean_daily_net_sol": m, "lo": lo_, "hi": hi_, "pass": judged and lo_ is not None and lo_ > 0,
                   "worst_day_sol": float(col[v0].min()), "daily": {d: float(col[v0][i]) for i, d in enumerate(dates)},
                   **concentration(con, v0, farm)}
    out["decision"] = (spec.claim if out["test"]["pass"] else "verworpen" if judged
                       else f"niet beoordeeld: {_days(pos_days)} met een {farm}-positie van {v0} (minimum {spec.min_days})")
    shown = (v0, *[v for v in spec.describe if v != v0])
    out["series"] = {}
    for v in shown:
        for f in FARMS:
            x = ct.matrix({v: daily(con, v, f)}, dates, strict=True)[1][:, 0]
            mm, ll, hh = boot_mean_days(x, a.draws)
            out["series"][f"{v}|{f}"] = {"mean_daily_net_sol": mm, "lo": ll, "hi": hh, "worst_day_sol": float(x.min()),
                                         **concentration(con, v, f)}
    regimes = [r for (r,) in con.execute("SELECT DISTINCT regime FROM h ORDER BY 1").fetchall()]
    if len(regimes) > 1:
        out["per_regime"] = {r: dict(con.execute(f"""SELECT eday, sum(pnl) FROM h WHERE variant = ? AND {where} AND regime = ?
                                                     GROUP BY 1 ORDER BY 1""", [v0, r]).fetchall()) for r in regimes}
    out["exit_reasons"] = {f"{v}|{f}": {str(k): n for k, n in con.execute(
        """SELECT CASE WHEN entry_failed THEN 'failed entry' ELSE exit_reason END, count(*) FROM h WHERE variant = ? AND farm = ?
           GROUP BY 1 ORDER BY 1""", [v, f]).fetchall()} for v in shown for f in ("organic", "flagged")}
    dm, dl, dh = boot_mean_days(col[v0] - col[BASELINE], a.draws)
    out["vs_tp4_T600"] = {"farm": farm, "mean_daily_diff_sol": dm, "lo": dl, "hi": dh}
    counts = con.execute(f"""SELECT variant, farm, count(*), count(*) FILTER (WHERE entry_failed), round(sum(pnl), 4) FROM h
                             WHERE variant IN ({','.join('?' * len(shown))}) GROUP BY ALL ORDER BY ALL""", list(shown)).fetchall()
    out["positions"] = {f"{v}|{f}": {"rows": n, "failed_entries": fe, "net_sol": s} for v, f, n, fe, s in counts}
    out["tp_exits"] = {f"{v}|{f}": tp_exits(con, v, f) for v in shown for f in ("organic", "flagged")}
    if spec.common_with:
        out["common_with"] = common_triggers(con, v0, spec.common_with, farm)
    out["descriptive"] = describe(con, hg, keep, win, a, shown)
    # DSR and PBO
    from wallets.overfit import day_matrix, pbo, sharpe_columns

    trials = day_matrix(con, a.trials_parts, load_defaults(), family="F7", where=spec.trials_where)
    dev = trials.get("F7", ([], [], np.zeros((0, 0))))[2]
    if dev.shape[1] != spec.trials_columns:
        raise ct.Refused(f"the trials' parts have {dev.shape[1]} of the {spec.trials_columns} dev trials ({spec.trials_where})")
    out["DSR"] = ct.dsr_both(col[v0], a.trials, float(sharpe_columns(dev).var(ddof=1))) | {"series": f"{v0}|{farm}"}
    t = len(dates)
    p = pbo(mtx, blocks=min(16, t)) if t >= 2 else {}
    out["PBO"] = {"t": t, "n_columns": len(labels), "blocks": p.get("blocks"), "pbo": p.get("pbo"), "slope": p.get("slope"),
                  "prob_loss": p.get("prob_loss"), "n_splits": p.get("n_splits"), "indicative_only": t < 10, "selected": v0,
                  "zero_columns": sorted(v for v in labels if not series.get(v))}
    json.dump(out, open(os.path.join(a.out, "result.json"), "w"), indent=1, default=str)
    with open(os.path.join(a.out, "result.md"), "w") as fh:
        fh.write(report_md(out, spec))
    log(f"{'DRY RUN: ' if a.dry_run else ''}{spec.name}: {out['decision']} (result.json, result.md)")
    return out


def describe(con, glob, keep, win, a, shown):
    """exits_report's per-variant stats (with the runners / drains / collapses / rest split) and the paired delta
    against tp4_T600 on the common triggers, per farm group, on the run's rows minus the exclusions."""
    from wallets import exits_report as er

    d = os.path.join(a.out, "run-parts")
    os.makedirs(d, exist_ok=True)
    con.execute(f"COPY (SELECT * FROM read_parquet('{glob}', union_by_name = true) WHERE {F7REV} AND {win} AND {keep}) "
                f"TO '{d}/part-000000.parquet' (FORMAT parquet)")
    c2 = duckdb.connect(config={"memory_limit": os.environ.get("LAB_DUCKDB_MEM", "2GB"), "threads": 2})
    er.load(c2, d, os.environ.get("LAB_DEFAULTS", er.DEFAULT_CFG), baseline=BASELINE)
    out = {}
    for dd in [r[0] for r in c2.execute("SELECT DISTINCT d FROM pos ORDER BY 1").fetchall()]:
        for v in (BASELINE, *[x for x in shown if x != BASELINE]):
            for farm in FARMS:
                s = er.stats(c2, v, dd, farm, "TRUE", a.draws)
                out[f"{v}|d{dd}|{farm}"] = {
                    "stats": s, "paired": None if v == BASELINE else er.paired(c2, v, dd, farm, "TRUE", a.draws, BASELINE),
                    "exit_reasons": dict(c2.execute("SELECT exit_reason, count(*) FROM pos WHERE variant = ? AND d = ? AND farm = ? GROUP BY 1",
                                                    [v, dd, farm]).fetchall())}
    return out


def _days(n):
    return f"{n} dag" if n == 1 else f"{n} dagen"


def _tp_lines(o, spec, num):
    tp = o.get("tp_exits", {}).get(f"{spec.variant}|{spec.farm}")
    if not tp or not tp["n"]:
        return []
    return [f"Take-profit-exits van {spec.variant} ({spec.farm}): {tp['n']}, proceeds/kosten p5 / mediaan / p95 = "
            f"{num(tp['p5'], 2)} / {num(tp['median'], 2)} / {num(tp['p95'], 2)}."]


def _common_line(c, spec, sol):
    o = c["other"]
    return (f"Gemeenschappelijke triggers met {o} ({c['farm']}): {c['common']} (alleen {spec.variant}: {c[f'only_{spec.variant}']}, "
            f"alleen {o}: {c[f'only_{o}']}); Σ netto op die triggers {spec.variant} {sol(c[f'{spec.variant}_sol'])} SOL, {o} {sol(c[f'{o}_sol'])} SOL.")


def report_md(o, spec):
    sol = lambda x: "–" if x is None else f"{x:+.3f}"  # noqa: E731
    num = lambda x, nd=3: "–" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{nd}f}"  # noqa: E731
    h, w, ds, pb = o["test"], o["window"], o["DSR"], o["PBO"]
    rows = []
    for k, s in o["series"].items():
        v, farm = k.split("|")
        rows.append(f"| {v} | {farm} | {sol(s['mean_daily_net_sol'])} | {sol(s['lo'])} … {sol(s['hi'])} | {sol(s['worst_day_sol'])} | "
                    f"{num(s['top10_positions_share'], 2)} / {num(s['top10_pools_share'], 2)} / {num(s['top1pct_positions_share'], 2)} |")
    vs = o["vs_tp4_T600"]
    lines = [
        f"# {spec.name}: {spec.variant} ({spec.farm}) op schone data (hypothese {spec.sha[:8]})"
        + (" — DRY RUN, geen toets" if o.get("dry_run") else ""), "",
        f"Venster: slots [{w['slot_from']}, {w['slot_to']}), {_days(h['days'])} (UTC); d per epoch {w['d_per_epoch']}; ms per slot {w['ms_per_slot_per_epoch']}.",
        (f"Engine {w['engine_commit']} (replay byte-gelijk aan de {spec.name}-pin); store sha256 {w['store_sha256']} (= clean.lock van E20); "
         f"extractor {w['extractor_sha256']}." if not o.get("dry_run") else f"Engine {w['engine_commit']}; pin, replay en lock niet gecontroleerd (dry run)."),
        "Controle: tp4_T600 (farm alle) per epoch rij voor rij gelijk aan de K3-rijen van E20-run (1), op de gemeenschappelijke kolommen: "
        + ", ".join(f"{e}: {v['run_rows']} = {v['e20_rows']}" for e, v in o["control"].items()) + ".",
        f"Uitsluitingen: {len(o['exclusions']['mints'])} mints, {len(o['exclusions']['pools'])} pools.", "",
        f"**{spec.name}: gemiddeld Σ netto SOL per dag van {spec.variant} ({spec.farm}) = {sol(h['mean_daily_net_sol'])} SOL, 95 %-interval "
        f"{sol(h['lo'])} … {sol(h['hi'])} (dagbootstrap, {format(h['draws'], ',').replace(',', '.')} trekkingen, seed 7): "
        f"{'geslaagd' if h['pass'] else 'niet geslaagd'}. Beslissing: {o['decision']}.**", "",
        f"{h['days_with_a_position']} van {_days(h['days'])} met een positie (minimum {spec.min_days}); slechtste dag "
        f"{sol(h['worst_day_sol'])} SOL; grootste positie {sol(h['largest_position_sol'])} SOL van {sol(h['net_sol'])} SOL netto "
        f"(aandeel {num(h['largest_position_share'], 2)}); top-10 posities / top-10 pools / top-1 % posities "
        f"{num(h['top10_positions_share'], 2)} / {num(h['top10_pools_share'], 2)} / {num(h['top1pct_positions_share'], 2)}.", "",
        "| reeks | farm | Σ netto SOL per dag | 95 %-interval | slechtste dag | aandeel top-10 pos. / top-10 pools / top-1 % |",
        "|---|---|---|---|---|---|", *rows, "",
        f"{spec.variant} − tp4_T600 ({spec.farm}), gemiddeld per dag: {sol(vs['mean_daily_diff_sol'])} SOL ({sol(vs['lo'])} … {sol(vs['hi'])}).", "",
        f"DSR van de reeks (N = {ds.get('n_trials')}, T = {ds.get('t')}): met de variantie van de dev-trials {num(ds.get('dsr_dev'))}, "
        f"met 1/T {num(ds.get('dsr_null'))}; PSR tegen 0 {num(ds.get('psr0'))}.",
        f"PBO (CSCV, {pb['n_columns']} kolommen {spec.farm}, T = {pb['t']}, {pb['blocks'] or '–'} blokken): {num(pb['pbo'])}"
        + (" — slechts indicatief (T < 10)" if pb["indicative_only"] else "") + ".",
        *([f"Per regime ({spec.variant}, Σ netto SOL per dag): {o['per_regime']}."] if o.get("per_regime") else []),
        *_tp_lines(o, spec, num),
        *([_common_line(o["common_with"], spec, sol)] if o.get("common_with") else []),
        "", "Beschrijvend (exits_report-definities, organisch, flagged en alle): zie result.json `descriptive` (netto per gevulde trade, "
        "gepaarde Δ tegen tp4_T600 op de gemeenschappelijke triggers, leeg-aandeel, p5/p95, slechtste dag, mediane hold, exit-redenen, "
        "en de splitsing runners ×5 / drains / instortingen / rest in SOL per dag).",
    ]
    if (ds.get("dsr_dev") or 1) < 0.95 or (ds.get("dsr_null") or 1) < 0.95 or (pb["pbo"] or 0) > 0.5:
        lines.append(f"Voorbehoud: DSR < 0,95 of PBO > 0,5 (de beslisregel blijft {spec.name}).")
    return "\n".join(lines) + "\n"


def main(spec, argv=None):
    spec.check()
    ap = argparse.ArgumentParser(description=f"{spec.name}: {spec.variant} ({spec.farm}) on the clean data (backtest.clean_hx)")
    ap.add_argument("--run", required=True, help=f"the {spec.name} run (config.json, parts/)")
    ap.add_argument("--store", required=True, help="E20's clean store (its clean.lock next to it)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--hypothesis", required=True)
    ap.add_argument("--exclusions", required=True)
    ap.add_argument("--exclusions-sha256", required=True)
    ap.add_argument("--engine", help=f"the {spec.name} pin (prefix); the run's engine_commit must start with it")
    ap.add_argument("--engine-dir", required=True, help="the run's engine snapshot (its lab/ level)")
    ap.add_argument("--trials-parts", required=True, help=f"{spec.trials_run}/parts: the dev trials' Sharpe variance")
    ap.add_argument("--trials", type=int, default=spec.trials)
    ap.add_argument("--draws", type=int, default=DRAWS)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--e20-run", help="dry run: E20's test run (in place of the lock's)")
    a = ap.parse_args(argv)
    if not a.dry_run and not a.engine:
        raise SystemExit(f"pass --engine (the {spec.name} pin)")
    if a.dry_run and not a.e20_run:
        raise SystemExit("a dry run needs --e20-run")
    return run_hx(a, spec)


def spec_env(spec):
    """The Spec as shell assignments for nas_clean_hx.sh (one per line)."""
    spec.check()
    import shlex

    return "\n".join(f"{k}={shlex.quote(v)}" for k, v in (("HYP_FILE", spec.hypothesis), ("HYP_SHA", spec.sha), ("F7_GRID", spec.f7_grid),
                                                          ("TRIALS_RUN", spec.trials_run), ("NAME", spec.name.lower())))


if __name__ == "__main__":
    sys.exit("run a hypothesis' module (e.g. python -m backtest.clean_h5)")
