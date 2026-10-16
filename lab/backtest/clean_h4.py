"""H4 (research/hypothesis-h4-flow300-organic-2026-10-16.md, sha256 H4_SHA): K3's entry (F7 pool_reversed, 0.5 SOL,
q 0.5, d_ms 600) with the exit flow300_H6 on the organic triggers only (farm = 'organic': the engine's label at the
trigger, the pool's farmer share over [t - 1 h, t)), an absolute test on the clean data (1055-1063): a run of its own
after E20 (clean_test, pin 46a0236) and H3 (clean_h3, pin dbef1b8), on E20's store.

  python -m backtest.clean_h4 --run DIR --store FILE --out DIR --hypothesis FILE --exclusions FILE \\
      --exclusions-sha256 SHA --engine SHA --engine-dir DIR --trials-parts DIR [--trials N]

Order, fail-closed (a failure exits before the next step and nothing after it is computed):
  1. documents: H4's sha256 is H4_SHA and equals its .sha256 file; the exclusion list's is the one passed (and its
     .sha256 file);
  2. engine: the snapshot's replay files are byte-equal to the H4 pin's (REPLAY_SHA256_H4: main after #286, whose
     rows equal fc4173c's, the engine flow300_H6 was measured with on dev), the run's engine commit starts with the pin;
  3. store: E20's clean.lock exists (E20 passed its minimum of trading days: the store is final) and the store's
     sha256 is the lock's;
  4. the run: F7 pool_reversed, 0.5 SOL, q 0.5, tau empirical, tolerance 0.05, d_ms 600, f7_grid v1,exits,long
     (tp4_T600 for the control, hold180, flow300_H6), no variant filter, decisions in the store's window;
  5. exclusions: resolved with clean_test's rules against the store and written before any outcome; they leave
     every row;
  6. control: per epoch the run's tp4_T600 rows (farm all) equal E20 run (1)'s K3 rows on their common columns (E20's
     engine writes no peak_mark, peak_slot or mayhem_reverted); a difference is reported and H4 is not judged;
  7. outcomes: per variant and farm the net P&L in SOL per UTC day of the entry slot (every non-skipped row, failed
     entries' cost included; a day without a position counts 0) over the window's days. H4: the mean daily net SOL
     of flow300_H6 on the organic triggers with a 95 % interval (bootstrap over days, DRAWS draws, seed 7); it holds
     when the lower bound is > 0; with fewer than MIN_DAYS days with such a position it is not judged. Descriptive:
     the same series for hold180 and tp4_T600 and for the flagged and all triggers, the daily difference
     flow300_H6 - tp4_T600 (organic), the worst day, the largest position and the shares of the 10 best positions,
     the 10 best pools and the best 1 % of the positions in the net SOL, the exit reasons, the daily sums per regime
     (more than one), and exits_report's per-variant stats with the runners (x5) / drains / collapses / rest split;
     DSR of the H4 series with N = TRIALS (the Sharpe variance of the dev trials TRIALS_WHERE in --trials-parts, and
     the null 1/T); PBO (CSCV) over the T x k matrix of the run's variants on the organic triggers, blocks min(16, T).

--dry-run: dev or forward stores only (refused on a 'clean' store), with --e20-run in place of the lock; no pin,
replay or window checks; for testing the job only.
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
from .strategies import EPOCH_SLOTS, F7_EXIT_GRID, F7_GRID, F7_LONG_GRID, Clock, f7_exit_variant

# The H4 file (LAB research, 2026-10-16, #167): its sha256 and the trials it names for the DSR: N = 210 (E20's 42,
# the exit grid's 108, the long grid's 60), the Sharpe variance of the long grid's 60 dev trials (job 82,
# long-dev-1033-1046-fc4173c: F7 pool_reversed, 15 variants x d 1, 2 x farm flagged, organic).
H4_SHA = "2358103206f7738573ecb40508b7cf0c0b2d7fe2f6c9cb16375c8bf7607d5e97"
TRIALS = 210
TRIALS_WHERE = "segment = 'pool_reversed' AND size_sol = 0.5 AND q = 0.5 AND overlay = 'none'"
TRIALS_COLUMNS = 60
MIN_DAYS = 5  # fewer UTC days with an organic flow300_H6 position: H4 is not judged (the H4 file, note 3)
# The replay code H4 pins: main after #286 (the Mayhem guard: no row of any universe changes) and #287 (no backtest
# file), whose rows equal fc4173c's (the A/B in the PR of this job).
REPLAY_SHA256_H4 = {
    "tournament.py": "4fc1013d4a58d8aa794fff2b1452384dd19d1b74cd10bbbe9ba12a45c21c814c",
    "strategies.py": "ff5528e7837d71bfc39ba3eb35666aa364487ce134ffc4966d69b53c7f0e6688",
    "replay.py": "fd2e3c91f7f10a63682b4a8ecde7c04ddcc5a950626cebc4514b1d42d0d5946e",
    "costs.py": "27db4d6d9d95a0fb5bf797573aa968d01c1858820d5e16b2625eccf056fa7077",
    "wash.py": "8486a0d289ffc826cb96961b243cfb777e1b774bd6231bbd7b221b3a5dcc8440",
    "venues.py": "bce5c80b7f729459c738b989de5e7ef6592f63fbeeb0112b28fb4f349ab13f03",
    "streams.py": "201412f735647789a37a617adfe8e8b18a6ada088832c3bb371bfe0ba152467e",
    "mechanics.py": "3779c94287de2e030482b679cb52ccb907d8ad10cff038b6a00539f9ff392ad0",
    "overlay.py": "1b606ee7320d4cf4efd0aa9a386485f211f3a1cc97eaf362429a2b6ad86a1d65",
    "data.py": "5ea7765cbf8cca4d1c1cd97dac01f87705f2dc3c16e06fd18328af6b072a8709",
}
RUN = {"families": ["F7"], "segments": ["pool_reversed"], "sizes_sol": [0.5], "q": [0.5], "tau": ["empirical"],
       "slippage_tol": 0.05, "delay_ms": 600, "f7_grid": "v1,exits,long", "variants": None}
F7REV = ct.F7REV
H4_VARIANT, HOLD, BASELINE, ORGANIC = "flow300_H6", "hold180", "tp4_T600", "organic"
VARIANTS = ([f"tp{int(tp * 100)}_T{T}" for tp, T in F7_GRID] + [f7_exit_variant(*g) for g in F7_EXIT_GRID]
            + [f7_exit_variant(*g) for g in F7_LONG_GRID])
DRAWS, SEED, TOP = 10_000, 7, 10
FARMS = (ORGANIC, "flagged", "all")


def check_replay(backtest_dir):
    """The snapshot's replay files are byte-equal to the H4 pin's; exits otherwise."""
    bad = [f for f, want in REPLAY_SHA256_H4.items() if not os.path.exists(os.path.join(backtest_dir, f))
           or ct.sha256(os.path.join(backtest_dir, f)) != want]
    if bad:
        raise ct.Refused(f"replay code in {backtest_dir} differs from the H4 pin's: {bad}")
    return sorted(REPLAY_SHA256_H4)


def check_lock(store):
    """E20's clean.lock next to the store, with this store's sha256: (lock, store sha256)."""
    lock_path = os.path.join(os.path.dirname(os.path.abspath(store)), "clean.lock")
    if not os.path.exists(lock_path):
        raise ct.Refused("no clean.lock: E20 has not passed its minimum, the store is not final; H4 waits")
    lock = json.load(open(lock_path))
    got = ct.sha256(store)
    if lock.get("store_sha256") != got:
        raise ct.Refused(f"the store's sha256 {got} is not clean.lock's {lock.get('store_sha256')}")
    return lock, got


def check_run(cfg, meta, engine, dry_run=False):
    bad = [f"{k}: {cfg.get(k)!r} != {v!r}" for k, v in RUN.items() if cfg.get(k) != v]
    if dry_run:
        if bad:
            raise ct.Refused("the run is not H4's: " + "; ".join(bad))
        return tuple(cfg.get("decide_range") or cfg.get("slot_range"))
    if not str(cfg.get("engine_commit", "")).startswith(engine or "?"):
        bad.append(f"engine {cfg.get('engine_commit')} is not the pin {engine}")
    if meta.get("period") != "clean":
        bad.append(f"store period {meta.get('period')!r}")
    if list(cfg.get("decide_range") or []) != list(meta.get("window") or [None]):
        bad.append(f"decide_range {cfg.get('decide_range')} is not the store's window {meta.get('window')}")
    if bad:
        raise ct.Refused("the run is not H4's: " + "; ".join(bad))
    return tuple(meta["window"])


def common_columns(con, a_glob, b_glob):
    """The columns both part sets carry, in the first set's order (E20's engine writes fewer than H4's)."""
    cols = lambda g: [c[0] for c in con.execute(f"SELECT * FROM read_parquet('{g}', union_by_name = true) LIMIT 0").description]  # noqa: E731
    b = set(cols(b_glob))
    return [c for c in cols(a_glob) if c in b]


def equality(con, h4_glob, e20_glob, keep, lo, hi):
    """Per epoch: the run's tp4_T600 rows (farm all) against E20 run (1)'s K3 rows on their common columns (both after
    the exclusions)."""
    cols = ", ".join(f'"{c}"' for c in common_columns(con, h4_glob, e20_glob))
    out, ok = {}, True
    for e in range(lo // EPOCH_SLOTS, (hi - 1) // EPOCH_SLOTS + 1):
        rng = f"t >= {max(lo, e * EPOCH_SLOTS)} AND t < {min(hi, (e + 1) * EPOCH_SLOTS)}"
        a = f"SELECT {cols} FROM read_parquet('{h4_glob}', union_by_name = true) WHERE {ct.K3} AND {rng} AND {keep}"
        b = f"SELECT {cols} FROM read_parquet('{e20_glob}', union_by_name = true) WHERE {ct.K3} AND {rng} AND {keep}"
        n1, n2 = (con.execute(f"SELECT count(*) FROM ({q})").fetchone()[0] for q in (a, b))
        o1 = con.execute(f"SELECT count(*) FROM ({a} EXCEPT ALL {b})").fetchone()[0]
        o2 = con.execute(f"SELECT count(*) FROM ({b} EXCEPT ALL {a})").fetchone()[0]
        out[e] = {"h4_rows": n1, "e20_rows": n2, "only_h4": o1, "only_e20": o2}
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
        SELECT variant, d, t, pool, mint, farm, regime, entry_failed, exit_reason, coalesce(entry_slot, t) AS es, {pnl} / 1e9 AS pnl
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


def run_h4(a, log=print):
    out = {"dry_run": bool(a.dry_run), "documents": {}}
    # 1. documents
    out["documents"]["hypothesis"] = ct.check_document(a.hypothesis, H4_SHA, "H4", sidecar=True)
    out["documents"]["exclusions"] = ct.check_document(a.exclusions, a.exclusions_sha256, "exclusions", sidecar=not a.dry_run)
    # 2. engine
    if not a.dry_run:
        out["documents"]["replay_code_h4"] = check_replay(os.path.join(a.engine_dir, "backtest"))
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
        raise ct.Refused(f"the run's tp4_T600 rows differ from E20 run (1)'s K3 rows: {eq}; H4 is not judged before this is explained")
    log(f"control: tp4_T600 equals E20's K3 per epoch; exclusions {len(mints)} mints, {len(pools)} pools")
    # 7. outcomes
    pnl, _ = settle_sql(Scenario.from_cfg("base", load_defaults()))
    entry_days(con, hg, keep, win, pnl)
    first = f"slot >= (SELECT max(slot) FROM bt WHERE slot <= {lo}) AND slot <= greatest({hi}, (SELECT max(es) FROM h))"
    dates = [x for (x,) in con.execute(f"""SELECT DISTINCT CAST(DATE '1970-01-01' + CAST(block_time // 86400 AS INTEGER) AS VARCHAR)
                                          FROM bt WHERE {first} ORDER BY 1""").fetchall()]
    series = {v: daily(con, v, ORGANIC) for v in VARIANTS}
    labels, mtx = ct.matrix(series, dates, strict=True)
    col = {v: mtx[:, labels.index(v)] for v in labels}
    m, lo4, hi4 = boot_mean_days(col[H4_VARIANT], a.draws)
    pos_days = con.execute("SELECT count(DISTINCT eday) FROM h WHERE variant = ? AND farm = ?", [H4_VARIANT, ORGANIC]).fetchone()[0]
    judged = pos_days >= MIN_DAYS
    out["H4"] = {"days": len(dates), "days_with_a_position": pos_days, "judged": judged, "draws": a.draws, "series": H4_VARIANT,
                 "farm": ORGANIC, "mean_daily_net_sol": m, "lo": lo4, "hi": hi4, "pass": judged and lo4 is not None and lo4 > 0,
                 "worst_day_sol": float(col[H4_VARIANT].min()), "daily": {d: float(col[H4_VARIANT][i]) for i, d in enumerate(dates)},
                 **concentration(con, H4_VARIANT, ORGANIC)}
    out["decision"] = ("F7 organic met flow300_H6 verdient op schone data netto SOL per dag (reconstructie met het kostenmodel)"
                       if out["H4"]["pass"] else "verworpen" if judged
                       else f"niet beoordeeld: {_days(pos_days)} met een organische flow300_H6-positie (minimum {MIN_DAYS})")
    out["series"] = {}
    for v in (H4_VARIANT, HOLD, BASELINE):
        for farm in FARMS:
            x = ct.matrix({v: daily(con, v, farm)}, dates, strict=True)[1][:, 0]
            mm, ll, hh = boot_mean_days(x, a.draws)
            out["series"][f"{v}|{farm}"] = {"mean_daily_net_sol": mm, "lo": ll, "hi": hh, "worst_day_sol": float(x.min()),
                                            **concentration(con, v, farm)}
    regimes = [r for (r,) in con.execute("SELECT DISTINCT regime FROM h ORDER BY 1").fetchall()]
    if len(regimes) > 1:
        out["per_regime"] = {r: dict(con.execute("""SELECT eday, sum(pnl) FROM h WHERE variant = ? AND farm = ? AND regime = ?
                                                    GROUP BY 1 ORDER BY 1""", [H4_VARIANT, ORGANIC, r]).fetchall()) for r in regimes}
    out["exit_reasons"] = {f"{v}|{f}": {str(k): n for k, n in con.execute(
        """SELECT CASE WHEN entry_failed THEN 'failed entry' ELSE exit_reason END, count(*) FROM h WHERE variant = ? AND farm = ?
           GROUP BY 1 ORDER BY 1""", [v, f]).fetchall()} for v in (H4_VARIANT, HOLD, BASELINE) for f in (ORGANIC, "flagged")}
    dm, dl, dh = boot_mean_days(col[H4_VARIANT] - col[BASELINE], a.draws)
    out["vs_tp4_T600_organic"] = {"mean_daily_diff_sol": dm, "lo": dl, "hi": dh}
    counts = con.execute("""SELECT variant, farm, count(*), count(*) FILTER (WHERE entry_failed), round(sum(pnl), 4) FROM h
                            WHERE variant IN (?, ?, ?) GROUP BY ALL ORDER BY ALL""", [H4_VARIANT, HOLD, BASELINE]).fetchall()
    out["positions"] = {f"{v}|{f}": {"rows": n, "failed_entries": fe, "net_sol": s} for v, f, n, fe, s in counts}
    out["descriptive"] = describe(con, hg, keep, win, a)
    # DSR and PBO
    from wallets.overfit import day_matrix, pbo, sharpe_columns

    trials = day_matrix(con, a.trials_parts, load_defaults(), family="F7", where=TRIALS_WHERE)
    dev = trials.get("F7", ([], [], np.zeros((0, 0))))[2]
    if dev.shape[1] != TRIALS_COLUMNS:
        raise ct.Refused(f"the trials' parts have {dev.shape[1]} of the {TRIALS_COLUMNS} dev trials ({TRIALS_WHERE})")
    out["DSR"] = ct.dsr_both(col[H4_VARIANT], a.trials, float(sharpe_columns(dev).var(ddof=1))) | {"series": f"{H4_VARIANT}|{ORGANIC}"}
    t = len(dates)
    p = pbo(mtx, blocks=min(16, t)) if t >= 2 else {}
    out["PBO"] = {"t": t, "n_columns": len(labels), "blocks": p.get("blocks"), "pbo": p.get("pbo"), "slope": p.get("slope"),
                  "prob_loss": p.get("prob_loss"), "n_splits": p.get("n_splits"), "indicative_only": t < 10, "selected": H4_VARIANT,
                  "zero_columns": sorted(v for v in labels if not series.get(v))}
    json.dump(out, open(os.path.join(a.out, "result.json"), "w"), indent=1, default=str)
    with open(os.path.join(a.out, "result.md"), "w") as fh:
        fh.write(report_md(out))
    log(f"{'DRY RUN: ' if a.dry_run else ''}H4: {out['decision']} (result.json, result.md)")
    return out


def describe(con, glob, keep, win, a):
    """exits_report's per-variant stats (with the runners / drains / collapses / rest split) and the paired delta
    against tp4_T600 on the common triggers, per farm group, on the run's rows minus the exclusions."""
    from wallets import exits_report as er

    d = os.path.join(a.out, "h4-parts")
    os.makedirs(d, exist_ok=True)
    con.execute(f"COPY (SELECT * FROM read_parquet('{glob}', union_by_name = true) WHERE {F7REV} AND {win} AND {keep}) "
                f"TO '{d}/part-000000.parquet' (FORMAT parquet)")
    c2 = duckdb.connect(config={"memory_limit": os.environ.get("LAB_DUCKDB_MEM", "2GB"), "threads": 2})
    er.load(c2, d, os.environ.get("LAB_DEFAULTS", er.DEFAULT_CFG), baseline=BASELINE)
    out = {}
    for dd in [r[0] for r in c2.execute("SELECT DISTINCT d FROM pos ORDER BY 1").fetchall()]:
        for v in (BASELINE, H4_VARIANT, HOLD):
            for farm in FARMS:
                s = er.stats(c2, v, dd, farm, "TRUE", a.draws)
                out[f"{v}|d{dd}|{farm}"] = {
                    "stats": s, "paired": None if v == BASELINE else er.paired(c2, v, dd, farm, "TRUE", a.draws, BASELINE),
                    "exit_reasons": dict(c2.execute("SELECT exit_reason, count(*) FROM pos WHERE variant = ? AND d = ? AND farm = ? GROUP BY 1",
                                                    [v, dd, farm]).fetchall())}
    return out


def _days(n):
    return f"{n} dag" if n == 1 else f"{n} dagen"


def report_md(o):
    sol = lambda x: "–" if x is None else f"{x:+.3f}"  # noqa: E731
    num = lambda x, nd=3: "–" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{nd}f}"  # noqa: E731
    h, w, ds, pb = o["H4"], o["window"], o["DSR"], o["PBO"]
    rows = []
    for k, s in o["series"].items():
        v, farm = k.split("|")
        rows.append(f"| {v} | {farm} | {sol(s['mean_daily_net_sol'])} | {sol(s['lo'])} … {sol(s['hi'])} | {sol(s['worst_day_sol'])} | "
                    f"{num(s['top10_positions_share'], 2)} / {num(s['top10_pools_share'], 2)} / {num(s['top1pct_positions_share'], 2)} |")
    vs = o["vs_tp4_T600_organic"]
    lines = [
        f"# H4: flow300_H6 op organische triggers op schone data (hypothese {H4_SHA[:8]})" + (" — DRY RUN, geen toets" if o.get("dry_run") else ""), "",
        f"Venster: slots [{w['slot_from']}, {w['slot_to']}), {_days(h['days'])} (UTC); d per epoch {w['d_per_epoch']}; ms per slot {w['ms_per_slot_per_epoch']}.",
        (f"Engine {w['engine_commit']} (replay byte-gelijk aan de H4-pin); store sha256 {w['store_sha256']} (= clean.lock van E20); extractor {w['extractor_sha256']}."
         if not o.get("dry_run") else f"Engine {w['engine_commit']}; pin, replay en lock niet gecontroleerd (dry run)."),
        "Controle: tp4_T600 (farm alle) per epoch rij voor rij gelijk aan de K3-rijen van E20-run (1), op de gemeenschappelijke kolommen: "
        + ", ".join(f"{e}: {v['h4_rows']} = {v['e20_rows']}" for e, v in o["control"].items()) + ".",
        f"Uitsluitingen: {len(o['exclusions']['mints'])} mints, {len(o['exclusions']['pools'])} pools.", "",
        f"**H4: gemiddeld Σ netto SOL per dag van flow300_H6 (organisch) = {sol(h['mean_daily_net_sol'])} SOL, 95 %-interval "
        f"{sol(h['lo'])} … {sol(h['hi'])} (dagbootstrap, {format(h['draws'], ',').replace(',', '.')} trekkingen, seed 7): {'geslaagd' if h['pass'] else 'niet geslaagd'}. "
        f"Beslissing: {o['decision']}.**", "",
        f"{h['days_with_a_position']} van {_days(h['days'])} met een organische flow300_H6-positie (minimum {MIN_DAYS}); slechtste dag "
        f"{sol(h['worst_day_sol'])} SOL; grootste positie {sol(h['largest_position_sol'])} SOL van {sol(h['net_sol'])} SOL netto "
        f"(aandeel {num(h['largest_position_share'], 2)}); top-10 posities / top-10 pools / top-1 % posities "
        f"{num(h['top10_positions_share'], 2)} / {num(h['top10_pools_share'], 2)} / {num(h['top1pct_positions_share'], 2)}.", "",
        "| reeks | farm | Σ netto SOL per dag | 95 %-interval | slechtste dag | aandeel top-10 pos. / top-10 pools / top-1 % |",
        "|---|---|---|---|---|---|", *rows, "",
        f"flow300_H6 − tp4_T600 (organisch), gemiddeld per dag: {sol(vs['mean_daily_diff_sol'])} SOL ({sol(vs['lo'])} … {sol(vs['hi'])}).", "",
        f"DSR van de H4-reeks (N = {ds.get('n_trials')}, T = {ds.get('t')}): met de variantie van de dev-trials {num(ds.get('dsr_dev'))}, "
        f"met 1/T {num(ds.get('dsr_null'))}; PSR tegen 0 {num(ds.get('psr0'))}.",
        f"PBO (CSCV, {pb['n_columns']} kolommen organisch, T = {pb['t']}, {pb['blocks'] or '–'} blokken): {num(pb['pbo'])}"
        + (" — slechts indicatief (T < 10)" if pb["indicative_only"] else "") + ".",
        *([f"Per regime (flow300_H6 organisch, Σ netto SOL per dag): {o['per_regime']}."] if o.get("per_regime") else []),
        "", "Beschrijvend (exits_report-definities, organisch en alle): zie result.json `descriptive` (netto per gevulde trade, gepaarde Δ "
        "tegen tp4_T600 op de gemeenschappelijke triggers, leeg-aandeel, p5/p95, slechtste dag, mediane hold, exit-redenen, en de "
        "splitsing runners ×5 / drains / instortingen / rest in SOL per dag).",
    ]
    if (ds.get("dsr_dev") or 1) < 0.95 or (ds.get("dsr_null") or 1) < 0.95 or (pb["pbo"] or 0) > 0.5:
        lines.append("Voorbehoud: DSR < 0,95 of PBO > 0,5 (de beslisregel blijft H4).")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", required=True, help="the H4 run (config.json, parts/)")
    ap.add_argument("--store", required=True, help="E20's clean store (its clean.lock next to it)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--hypothesis", required=True)
    ap.add_argument("--exclusions", required=True)
    ap.add_argument("--exclusions-sha256", required=True)
    ap.add_argument("--engine", help="the H4 pin (prefix); the run's engine_commit must start with it")
    ap.add_argument("--engine-dir", required=True, help="the run's engine snapshot (its lab/ level)")
    ap.add_argument("--trials-parts", required=True, help="long-dev-1033-1046-fc4173c/parts: the dev trials' Sharpe variance")
    ap.add_argument("--trials", type=int, default=TRIALS)
    ap.add_argument("--draws", type=int, default=DRAWS)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--e20-run", help="dry run: E20's test run (in place of the lock's)")
    a = ap.parse_args()
    if not a.dry_run and not a.engine:
        raise SystemExit("pass --engine (the H4 pin)")
    if a.dry_run and not a.e20_run:
        raise SystemExit("a dry run needs --e20-run")
    run_h4(a)


if __name__ == "__main__":
    sys.exit(main())
