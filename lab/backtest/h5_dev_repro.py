"""Diagnostic (iii), dev only (research/notes/pipeline-diagnostics-map-2026-10-24.json, result[2]; exploratory: no clean
verdict changes, nothing re-tuned): are E63's H5 cells (flow300_tp100_H6, F7 pool_reversed, q 0.5, farm organic, sizes
0.5-10 SOL, d 2 and 3, capital-ceiling-d15ac45: engine d15ac45) the same with the H5 clean pin's engine (15fa3dd) on
dev2? nas_h5_dev_repro.sh runs it; the steps:

  python -m backtest.h5_dev_repro inputs --e63 RUN --e63-exitday-json FILE --bigtp RUN --c3-keys PARQUET --store FILE
  python -m backtest.h5_dev_repro engine SNAPSHOT/backtest
  python -m backtest.h5_dev_repro compare --mode step0|full --new RUN --e63 RUN --e63-exitday-json FILE --c3-keys PARQUET
                                          --store FILE --out DIR [--engine-dir DIR --new-json FILE] [--dry-run]

What compare compares, new run against E63 (exact: 0 rows, 0 lamports on every day, equal JSON values; the largest
|difference| is reported so that a looser reading stays possible, but GREEN means exact):
- rows: per (size, d) every row of the variant (F7 pool_reversed q 0.5, every farm, skipped rows too) on all the
  columns both runs' parts carry (exit_attempts, vault_capped, mint, regime, d_ms included), EXCEPT ALL both ways; a
  column only one run has must be NULL on every row (route (ii)'s entry, capped, k_tx: NULL without --entries);
- per UTC day (the parts' day: the trigger slot's) and per cell (size x d x base / pessimistic): without limits the
  sum of the settled P&L in integer lamports; with limits, for C = size / 5 % and size / 10 % and every unsold
  convention, the trigger-cohort day_pnl and the limit's exit_day_pnl of capital_ceiling.limited's own capital_days
  call (limited_days: no second copy of its trades), as integer lamports;
- the cells' unlimited and limited dicts field by field, each run's over its own days (as capital_ceiling's main on
  that run computes them); a new run whose days are not E63's is RED by its days, never refused as another json;
  full: also the new ceiling.json (capital_ceiling's main on the new rows, step 2) against E63's: its H5 cells,
  sandwich keys included, and max_useful['H5'].

Settlement: capital_ceiling.load's, costs.settle_sql under rule 1 (capital_ceiling.SETTLE_RULE_E63), which E63 was
settled with; main's default is rule 2 since Codex review #6, so a compare refuses when capital_ceiling's rule is not 1.
The daily loss limit is this checkout's capital_days (Codex review #5: counted on the exit day). E63's side is
anchored first, and any miss refuses before anything is compared (it would not be E63's series): its days equal
ceiling.json's 20; its H5 cells equal nas_capital_exitday.sh's recompute of E63 (capital-exitday-576db7a/e63/ceiling.json,
the same module and rule) in every field, and E63's own ceiling.json (0ede273e) in the without-limits fields (its
with-limits cells were booked on the trigger day: not reproducible by main since #566); its d 3 base day series give
descriptives.out's H5 totals, trades, pools and halves (full and step0 both anchor the whole E63 side).

Refused (rc 1, fail closed): an input whose sha256 is not the archived one; dev2's meta not E63's store block; this
checkout's capital_ceiling.py / costs.py not the reviewed blobs; LAB_CORE not python; a run compared with itself (the
same directory, the same engine commit, or a new run that is not 15fa3dd's: engine_commit, engine_dir, the snapshot's
files against the pin's tree, H5 alone, E63's grid and store); an existing --out.
- step0: job 87 (bigtp-dev-1033-1046-4dbd34b, engine 4dbd34b: the same engine files as 15fa3dd) at 0.5 SOL d 2,
  informative only (rc 3 on a difference, not RED): job 87 also ran N2, whose N2_F7 signals can share the (pool, 6 h)
  cluster key with F7's H6 triggers and merge two clusters (busy is reset per cluster), so its F7 rows may differ
  without an engine difference.
- full: nas_h5_dev_repro.sh's run (15fa3dd from a snapshot, E63's flags restricted to H5: F7-only, so its H6
  clusters are E63's); rc 1 on any difference. --dry-run: a RANGE run, both sides cut to its slots; not evidence, rc 0
  unless refused.
- --clean-bridge (step 4: the clean conventions with the case exclusions on dev2) is refused: it needs LAB research's OK.

GREEN rules out an engine-version difference between 15fa3dd and d15ac45 on dev2's H5 rows only; it says nothing about
the extractor (i), the store builder (ii), the clean decide window and warm-up, the delay convention (600 ms is d 2 on
dev, d 3 on clean), the entry day vs the trigger day, the limits or the case exclusions.
"""

import argparse
import csv
import glob
import hashlib
import json
import math
import os
import re
import sys
from collections import defaultdict

import duckdb
import pyarrow.parquet as pq

from . import capital_ceiling as cc
from . import report_delta
from . import store

Refused = cc.Refused

H5, FARM = "flow300_tp100_H6", "organic"
SIZES = (0.5, 1.0, 1.25, 2.0, 2.5, 5.0, 10.0)
DELAYS = (2, 3)
STEP0_CELL = (0.5, 2)
PIN = "15fa3dd759d6ffd9b995879554ec29ae0bad749c"  # H5's clean pin (#295)
# sha256 of the lines "<git blob sha1> <path>" (sorted by path, LC_ALL=C) of every file under lab/backtest at the pin:
# git ls-tree -r 15fa3dd lab/backtest, 68 files.
PIN_MANIFEST = "0563576d8d647bbb82d181c2527c4d341d2537dc07f3baba60334974d7644f3f"
E63_COMMIT = "d15ac453c5f62a4db7c585c34fc08e15e4f0121d"
BIGTP_COMMIT = "4dbd34bc283c7b0c4e5e7b04078d45e3ef0315d1"  # job 87; 15fa3dd adds clean_hx / clean_h5 to it, nothing else
# The inputs as archived: E63's SHA256SUMS (NAS job 99zzzzz52), capital-exitday-576db7a's SHA256SUMS, and the VPS copies of
# job 87's config.json, the C3 keys and week2-defaults.yaml (sha256 taken 2026-10-24).
SHA256 = {
    "e63_config": "c5fcd78ef083b67fe0bb326bee4b546fe11d937354f83ed7b18dfa6607e246be",
    "e63_json": "0ede273e35b481b660ff52cb970b51a392d166ca1173f3d2b7bc27f33687a88c",
    "e63_exitday_json": "0441cafff0bc2e9eadcf84468c6f15a77e566ac07047209ce15dd3220c9f7bec",
    "bigtp_config": "9b07053f2397683f41d35ce81f30418767d997ee98e5fe8a051eb7c6fd11a380",
    "c3_keys": "1a274ec60a5610f8abf22b70aed23266feaad8559fd79df77f35d8862fb10a23",
    "defaults": "1e8aedddd2a179a6131e8487a986e82877d98a58bf00bab2e7ae3d4c58d89243",  # research/week2-defaults.yaml
}
# The analysis code this diagnostic was written against (git blob sha1s, main 56d9df1): capital_ceiling.py with
# SETTLE_RULE_E63 = 1 and the daily loss on the exit day (#566, #575), costs.py with settle rule 2 as main's default. A
# merge that changes either one refuses the job rather than silently moving E63's side.
ANALYSIS = {"capital_ceiling.py": "30ce27ea02deedf2b11b4fdc0d38fb58f8f057b4",
            "costs.py": "5c6bb829fb832351bf36acc96fe90f4e75d685d3"}
# E63's descriptives.out (sha256 68eb2b01..., report section 4.4): H5 organic, d 3, base, without limits, per size:
# (total SOL to 0.1, trades, pools, SOL/day over days 1-10 and 11-20 to 0.01).
DESCRIPTIVES = {0.5: (146.5, 3325, 1960, 8.64, 6.01), 5.0: (684.1, 3269, 1960, 47.88, 20.53)}
HALF = "2026-09-22"  # day 11 of 20
# E63's flags restricted to H5 (nas_h5_dev_repro.sh's ARGS): the new run's config.json, and E63's but for variants.
GRID = {"families": ["F7"], "segments": ["pool_reversed"], "variants": [H5], "f7_grid": "v1,exits,big", "m1_grid": "v1",
        "sizes_sol": list(SIZES), "delays": list(DELAYS), "delay_ms": None, "q": [0.5], "tau": ["empirical"],
        "slippage_tol": 0.05, "decide_range": None, "only": None, "max_signals": 0, "batch": "tournament_v1"}
SANDWICH = ("sandwich_flagged_pct", "sandwich_fee_coverage_pct")
TOLERANCE = "exact: 0 differing rows, 0 lamports on every day, equal JSON values (E63's own parity used 0.001 SOL/day)"
SCOPE = ("GREEN rules out an engine-version difference between 15fa3dd and d15ac45 on dev2's H5 rows only. It says "
         "nothing about the extractor (i), the store builder (ii), the clean decide window and warm-up, the delay "
         "convention (600 ms is d 2 on dev, d 3 on clean), the entry day vs the trigger day, the limits or the case "
         "exclusions.")


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def git_blob(path):
    """The git blob sha1 of a file (what git ls-tree lists)."""
    with open(path, "rb") as f:
        data = f.read()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def manifest(backtest_dir):
    """(sha256, lines) of every file under the directory as '<git blob sha1> <relative path>', sorted by path."""
    lines = []
    for root, _, files in os.walk(backtest_dir):
        for name in files:
            p = os.path.join(root, name)
            lines.append(f"{git_blob(p)} {os.path.relpath(p, backtest_dir).replace(os.sep, '/')}")
    lines.sort(key=lambda x: x.split(" ", 1)[1].encode())
    text = "".join(x + "\n" for x in lines)
    return hashlib.sha256(text.encode()).hexdigest(), lines


def check_engine(backtest_dir):
    """The snapshot is 15fa3dd's lab/backtest, file for file (no file more or less: a __pycache__ is refused too), its
    replay files the clean H5 Spec's, and its hard-coded cost defaults path this checkout's."""
    from .clean_h5 import SPEC
    from .clean_hx import check_replay
    got, lines = manifest(backtest_dir)
    if got != PIN_MANIFEST:
        print("\n".join(lines), flush=True)  # before the refusal on stderr: the log's last line is the reason
        raise Refused(f"{backtest_dir} is not 15fa3dd's lab/backtest: manifest {got} != {PIN_MANIFEST} (its lines above; "
                      "compare with git ls-tree -r 15fa3dd lab/backtest)")
    check_replay(backtest_dir, SPEC)
    with open(os.path.join(backtest_dir, "store.py")) as f:
        m = re.search(r'^DEFAULTS = "([^"]+)"$', f.read(), re.M)
    if not m or m.group(1) != store.DEFAULTS:
        raise Refused(f"the snapshot's cost defaults path {m and m.group(1)} is not {store.DEFAULTS}")
    return got


def check_analysis():
    """This checkout's capital_ceiling.py / costs.py are ANALYSIS's blobs; every P&L compared with E63 is settled under
    rule 1, as E63 was (capital_ceiling.load passes SETTLE_RULE_E63 to costs.settle_sql; main's default is rule 2);
    LAB_CORE is python."""
    here = os.path.dirname(os.path.abspath(__file__))
    bad = {f: git_blob(os.path.join(here, f)) for f, want in ANALYSIS.items() if git_blob(os.path.join(here, f)) != want}
    if bad:
        raise Refused(f"analysis code changed: {bad} (want {ANALYSIS}); E63's side would move")
    if cc.SETTLE_RULE_E63 != 1:
        raise Refused(f"capital_ceiling.SETTLE_RULE_E63 is {cc.SETTLE_RULE_E63}: E63 was settled under rule 1")
    if os.environ.get("LAB_CORE", "python") != "python":
        raise Refused(f"LAB_CORE={os.environ['LAB_CORE']}: E63 ran venue_math python")


def need(path, key):
    got = sha256(path)
    if got != SHA256[key]:
        raise Refused(f"{path}: sha256 {got} is not the archived {key} {SHA256[key]}")


def config(run):
    with open(os.path.join(run, "config.json")) as f:
        return json.load(f)


def parts_rows(run):
    files = glob.glob(os.path.join(run, "parts", "*.parquet"))
    return len(files), sum(pq.read_metadata(f).num_rows for f in files)


def check_inputs(e63, exitday_json, c3_keys, store_path, bigtp=None):
    """The archived inputs before anything is read for a result: E63's config / ceiling.json and its exit-day recompute,
    E63's parts complete, the C3 keys, the cost defaults, dev2's meta equal to E63's store block (the store identity:
    git_commit, built_at_unix, counts, chunk_list, ms_per_slot), and job 87 when given. Returns (E63's config, its
    ceiling.json, the exit-day ceiling.json)."""
    check_analysis()
    need(os.path.join(e63, "config.json"), "e63_config")
    need(os.path.join(e63, "ceiling", "ceiling.json"), "e63_json")
    need(exitday_json, "e63_exitday_json")
    need(c3_keys, "c3_keys")
    need(store.DEFAULTS, "defaults")
    cfg = config(e63)
    if cfg.get("engine_commit") != E63_COMMIT or cfg.get("venue_math") != "python":
        raise Refused(f"{e63}: engine {cfg.get('engine_commit')}, venue_math {cfg.get('venue_math')}: not E63's run")
    bad = {k: (cfg.get(k), v) for k, v in GRID.items() if k != "variants" and cfg.get(k) != v}
    if bad or H5 not in (cfg.get("variants") or []):
        raise Refused(f"E63's config is not the grid this job restricts to H5: {bad}, variants {cfg.get('variants')}")
    n = parts_rows(e63)[1]
    if n != cfg["positions"]:
        raise Refused(f"{e63}: {n} rows in its parts, config.json says {cfg['positions']}")
    cc.require_exit_day(exitday_json)
    with open(os.path.join(e63, "ceiling", "ceiling.json")) as f:
        e63_json = json.load(f)
    with open(exitday_json) as f:
        exitday = json.load(f)
    for name, j in (("ceiling.json", e63_json), ("exit-day ceiling.json", exitday)):
        p = j["provenance"]
        if len(p) != 1 or p[0]["engine_commit"] != E63_COMMIT:
            raise Refused(f"E63's {name} is not of E63's run alone: {[(x['run'], x['engine_commit']) for x in p]}")
    con = duckdb.connect(store_path, read_only=True)
    meta = json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
    con.close()
    if (meta.get("slot_start"), meta.get("slot_end_exclusive")) != cc.DEV:
        raise Refused(f"{store_path}: window [{meta.get('slot_start')}, {meta.get('slot_end_exclusive')}) is not dev2's")
    if meta != cfg["store"]:
        diff = sorted(k for k in set(meta) | set(cfg["store"]) if meta.get(k) != cfg["store"].get(k))
        raise Refused(f"{store_path}: meta differs from E63's store block in {diff}: not the store E63 ran on")
    if bigtp:
        need(os.path.join(bigtp, "config.json"), "bigtp_config")
        b = config(bigtp)
        if b.get("engine_commit") != BIGTP_COMMIT or b.get("store") != cfg["store"] or parts_rows(bigtp)[1] != b["positions"]:
            raise Refused(f"{bigtp}: not job 87's complete run on E63's store")
    return cfg, e63_json, exitday


def check_new(mode, new, e63, e63_cfg, engine_dir=None, dry_run=False):
    """The new run is not E63's and is what the mode says: step0 job 87; full 15fa3dd's run from the checked snapshot
    (its config's engine_dir), H5 alone on E63's grid and store, complete; a RANGE run only with dry_run."""
    if os.path.realpath(new) == os.path.realpath(e63):
        raise Refused(f"{new} is E63's run: a run compared with itself")
    cfg = config(new)
    want = BIGTP_COMMIT if mode == "step0" else PIN
    if cfg.get("engine_commit") != want or cfg.get("engine_commit") == e63_cfg.get("engine_commit"):
        raise Refused(f"{new}: engine {cfg.get('engine_commit')}, not {want} (E63: {e63_cfg.get('engine_commit')})")
    if cfg.get("store") != e63_cfg["store"]:
        raise Refused(f"{new}: its store block is not E63's")
    if mode == "step0":
        need(os.path.join(new, "config.json"), "bigtp_config")
        if parts_rows(new)[1] != cfg.get("positions"):
            raise Refused(f"{new}: its parts do not hold config.json's {cfg.get('positions')} positions")
        return cfg
    if not engine_dir or os.path.realpath(cfg.get("engine_dir", "")) != os.path.realpath(engine_dir):
        raise Refused(f"{new}: engine_dir {cfg.get('engine_dir')} is not the checked snapshot {engine_dir}")
    check_engine(engine_dir)
    bad = {k: (cfg.get(k), v) for k, v in GRID.items() if cfg.get(k) != v}
    if bad:
        raise Refused(f"{new}: not E63's flags restricted to H5: {bad}")
    if bool(cfg.get("slot_range")) != dry_run:
        raise Refused(f"{new}: slot_range {cfg.get('slot_range')} with dry_run {dry_run} (a RANGE run is never evidence)")
    con = duckdb.connect()
    src = f"read_parquet('{os.path.join(new, 'parts', '*.parquet')}', union_by_name = true)"
    fams = con.execute(f"SELECT DISTINCT family, variant FROM {src} ORDER BY ALL").fetchall()
    con.close()
    if fams != [("F7", H5)]:
        raise Refused(f"{new}: rows of {fams}, not F7 {H5} alone (another run's parts?)")
    if parts_rows(new)[1] != cfg.get("positions"):
        raise Refused(f"{new}: its parts do not hold config.json's {cfg.get('positions')} positions")
    return cfg


def lamports(book):
    """A day book's float sums of integer lamports as ints; refused unless every one is whole (exact below 2^53)."""
    out = {}
    for day, v in book.items():
        if not float(v).is_integer() or abs(v) >= 2 ** 53:
            raise Refused(f"day {day}: {v!r} lamports is not an exact integer sum")
        out[day] = int(v)
    return out


def limited_days(rows, days, capital, size, unsold, day_of):
    """capital_ceiling.limited's dict and the two day books of its own capital_days call, {day: lamports}: the trigger
    cohort (day_pnl, the reported series) and the limit's exit-day book (exit_day_pnl). limited() runs as it is, its
    capital_days wrapped to keep the extras, so the trades are built by limited alone (no copy to drift)."""
    seen, real = [], cc.capital_days

    def keep(*args, **kw):
        out = real(*args, **kw)
        seen.append(out[1])
        return out

    cc.capital_days = keep
    try:
        res = cc.limited(rows, days, capital, size, unsold, day_of)
    finally:
        cc.capital_days = real
    if len(seen) != 1:
        raise Refused(f"capital_ceiling.limited called capital_days {len(seen)} times, not once")
    return res, lamports(seen[0]["day_pnl"]), lamports(seen[0]["exit_day_pnl"])


def cell(con, size, d, scen, days, day_of):
    """One H5 cell as capital_ceiling.compute builds it (without the sandwich keys: no fee table here) and its day
    series {name: {day: lamports}}: 'unlimited' and limited/<mapping>/<unsold>/<trigger_day | exit_day>."""
    rows = cc.cell_rows(con, H5, FARM, False, size, d, scen)
    c = {"candidate": "H5", "variant": H5, "farm": FARM, "c3_arm": False, "size": size, "d": d, "scenario": scen,
         "unlimited": cc.unlimited(rows, days), "limited": []}
    per_day = defaultdict(int)
    for r in rows:  # r[5]: pnl_<scen>, settled under rule 1 by capital_ceiling.load (check_analysis)
        per_day[r[7]] += r[5] or 0
    series = {"unlimited": dict(per_day)}
    for mapping, frac in cc.MAPPINGS:
        cap = round(size / frac, 6)
        for unsold in cc.UNSOLD:
            lim, trig, exit_day = limited_days(rows, days, cap, size, unsold, day_of)
            lim.update({"mapping": mapping, "named_capital": cap in cc.CAPITALS})
            c["limited"].append(lim)
            series[f"limited/{mapping}/{unsold}/trigger_day"] = trig
            series[f"limited/{mapping}/{unsold}/exit_day"] = exit_day
    return json.loads(json.dumps(c, default=str)), series, {"pools": len({r[8] for r in rows})}


def side(con, cells, days, day_of):
    """{(size, d, scenario): (cell, series, extra)} of a loaded run (capital_ceiling.load's table r)."""
    return {(s, d, scen): cell(con, s, d, scen, days, day_of) for s, d in cells for scen in cc.SCENARIOS}


def exact(a, b):
    """Equal as JSON values, exactly: a bool is not a number, NaN equals NaN, a series element by element."""
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(exact(x, y) for x, y in zip(a, b))
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    return a == b


def field_diffs(a, b):
    """[(path, a, b)] for every leaf (report_delta's paths) where two JSON values differ; a path one side lacks too."""
    x, y = dict(report_delta.leaves(a)), dict(report_delta.leaves(b))
    return [["/".join(p), x.get(p, "<missing>"), y.get(p, "<missing>")] for p in sorted(set(x) | set(y))
            if p not in x or p not in y or not exact(x[p], y[p])]


def h5_cells(j):
    return {(c["size"], c["d"], c["scenario"]): c for c in j["cells"] if c["candidate"] == "H5"}


def strip(c):
    return {k: v for k, v in c.items() if k not in SANDWICH}


def anchor_e63(e63, days, e63_json, exitday):
    """E63's recomputed side is E63's before it is compared with anything: refused otherwise."""
    bad = []
    if days != e63_json["days"] or days != exitday["days"]:
        bad.append(f"days {days[:1]}..{days[-1:]} ({len(days)}) vs ceiling.json's {len(e63_json['days'])}")
    ref, orig = h5_cells(exitday), h5_cells(e63_json)
    if set(ref) != set(e63) or set(orig) != set(e63):
        bad.append(f"cells {sorted(e63)} vs the exit-day json's {sorted(ref)} and ceiling.json's {sorted(orig)}")
    for k, (c, series, extra) in sorted(e63.items()):
        if k in ref:
            bad += [f"exit-day json {k} {p}: {x!r} != {y!r}" for p, x, y in field_diffs(c, strip(ref[k]))]
        if k in orig:
            bad += [f"ceiling.json {k} {p}: {x!r} != {y!r}" for p, x, y in field_diffs(c["unlimited"], orig[k]["unlimited"])]
    for size, (total, trades, pools, first, second) in DESCRIPTIVES.items():
        c, series, extra = e63[(size, 3, "base")]
        s = series["unlimited"]
        got = (sum(s.values()) / 1e9, c["unlimited"]["rows"], extra["pools"],
               sum(v for x, v in s.items() if x < HALF) / 1e9 / 10, sum(v for x, v in s.items() if x >= HALF) / 1e9 / 10)
        ok = (abs(got[0] - total) <= 0.05 + 1e-9 and got[1:3] == (trades, pools)
              and abs(got[3] - first) <= 0.005 + 1e-9 and abs(got[4] - second) <= 0.005 + 1e-9)
        if not ok:
            bad.append(f"descriptives H5 {size:g} SOL d 3: {got} vs {(total, trades, pools, first, second)}")
    if bad:
        raise Refused("E63's recomputed side is not E63's series (nothing compared):\n  " + "\n  ".join(bad[:40]))


def series_diff(a, b):
    """{day: lamports} against {day: lamports} over the days either has (a missing day is 0)."""
    d = {x: a.get(x, 0) - b.get(x, 0) for x in sorted(set(a) | set(b))}
    return {"max_abs_lamports": max((abs(v) for v in d.values()), default=0), "days_differing": {x: v for x, v in d.items() if v}}


def rows_diff(con, new, e63, cells, rng=None, show=20):
    """Per (size, d): the variant's rows of both runs (F7 pool_reversed q 0.5, every farm, skipped too) EXCEPT ALL
    both ways on their common columns; up to `show` residual rows per side, and per (variant, size_sol, d, pool, t) in
    both residuals the columns that differ."""
    where = f"family = 'F7' AND segment = 'pool_reversed' AND q = 0.5 AND variant = '{H5}'"
    if rng:
        where += f" AND t >= {int(rng[0])} AND t < {int(rng[1])}"
    cols = {}
    for name, run in (("new", new), ("e63", e63)):
        src = f"read_parquet('{os.path.join(run, 'parts', '*.parquet')}', union_by_name = true)"
        con.execute(f"CREATE OR REPLACE TEMP TABLE x_{name} AS SELECT * FROM {src} WHERE {where}")
        cols[name] = [c[0] for c in con.execute(f"DESCRIBE x_{name}").fetchall()]
    common = [c for c in cols["e63"] if c in cols["new"]]
    for name, other in (("new", "e63"), ("e63", "new")):
        extra = [c for c in cols[name] if c not in cols[other]]
        if extra:
            nulls = " AND ".join(f'"{c}" IS NULL' for c in extra)
            n = con.execute(f"SELECT count(*) FROM x_{name} WHERE NOT ({nulls})").fetchone()[0]
            if n:
                raise Refused(f"{n} {name} rows carry {extra}, which the other run has no column for: not comparable")
    sel = ", ".join(f'"{c}"' for c in common)
    key = ("variant", "size_sol", "d", "pool", "t")
    out = []
    for size, d in cells:
        a = f"SELECT {sel} FROM x_new WHERE size_sol = {size} AND d = {d}"
        b = f"SELECT {sel} FROM x_e63 WHERE size_sol = {size} AND d = {d}"
        n_new, n_e63 = (con.execute(f"SELECT count(*) FROM ({q})").fetchone()[0] for q in (a, b))
        only = {}
        for name, (p, q) in (("only_new", (a, b)), ("only_e63", (b, a))):
            cur = con.execute(f"SELECT * FROM ({p} EXCEPT ALL {q}) ORDER BY pool, t LIMIT {show}")
            only[name] = [dict(zip(common, r)) for r in cur.fetchall()]
            only[name + "_n"] = con.execute(f"SELECT count(*) FROM ({p} EXCEPT ALL {q})").fetchone()[0]
        pairs = []
        e = {tuple(r[k] for k in key): r for r in only["only_e63"]}
        for r in only["only_new"]:
            o = e.get(tuple(r[k] for k in key))
            if o is not None:
                pairs.append({"key": [r[k] for k in key], "columns": {c: [r[c], o[c]] for c in common if not exact(r[c], o[c])}})
        out.append({"size": size, "d": d, "new_rows": n_new, "e63_rows": n_e63, "only_new": only["only_new_n"],
                    "only_e63": only["only_e63_n"], "only_new_rows": only["only_new"], "only_e63_rows": only["only_e63"],
                    "pairs": pairs})
    return out, common


def json_checks(new_json_path, new_run, new, e63_json, exitday, days):
    """Step 2: the new ceiling.json (capital_ceiling's main on the new rows) is of the new run, rule 1, the exit-day book,
    E63's days; its H5 cells equal this module's recompute of the new run (else refused: not the same rows); against
    E63's: every field of the exit-day json's H5 cells (sandwich keys included) and max_useful['H5'], and the
    without-limits fields and sandwich_flagged_pct of E63's own ceiling.json. Returns the differences."""
    with open(new_json_path) as f:
        j = json.load(f)
    p = j["provenance"]
    if (j.get("settle_rule"), j.get("daily_loss_book")) != (1, "exit_day") or len(p) != 1 or p[0]["engine_commit"] != PIN \
            or os.path.realpath(p[0]["run"]) != os.path.realpath(new_run):
        raise Refused(f"{new_json_path}: rule {j.get('settle_rule')}, book {j.get('daily_loss_book')}, provenance "
                      f"{[(x['run'], x['engine_commit']) for x in p]}: not capital_ceiling's main on the new run")
    cells = h5_cells(j)
    mine = {k: strip(c) for k, c in cells.items()}
    bad = [f"{k} {x}" for k in sorted(set(mine) | set(new)) for x in
           (field_diffs(mine[k], new[k][0]) if k in mine and k in new else ["missing on one side"])]
    if bad:
        raise Refused(f"{new_json_path}'s H5 cells are not this module's recompute of the new run: {bad[:10]}")
    out = {"days": [] if j["days"] == days else [["days", j["days"], days]]}
    ref, orig = h5_cells(exitday), h5_cells(e63_json)
    out["exitday_cells"] = [[str(k)] + x for k in sorted(set(cells) | set(ref))
                            for x in (field_diffs(cells[k], ref[k]) if k in cells and k in ref else [["cell", k in cells, k in ref]])]
    out["max_useful_h5"] = field_diffs(j["max_useful"]["H5"], exitday["max_useful"]["H5"])
    out["e63_json_unlimited"] = [[str(k)] + x for k in sorted(orig) if k in cells for x in
                                 field_diffs({"unlimited": cells[k]["unlimited"], "flag": cells[k].get("sandwich_flagged_pct")},
                                             {"unlimited": orig[k]["unlimited"], "flag": orig[k].get("sandwich_flagged_pct")})]
    return out


def write_days(path, sides):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["side", "size", "d", "scenario", "series", "day", "lamports"])
        for name, cells in sides:
            for (size, d, scen), (_, series, _) in sorted(cells.items()):
                for s in sorted(series):
                    for day in sorted(series[s]):
                        w.writerow([name, size, d, scen, s, day, series[s][day]])


def report_md(o):
    L = [f"# Diagnostic (iii): E63's H5 cells at engine {o['new']['engine_commit'][:7]} vs d15ac45 on dev2 ({o['mode']})"
         + (" — DRY RUN, not evidence" if o["dry_run"] else ""), "",
         f"Verdict: **{o['verdict']}**" + (f" ({'; '.join(o['reasons'])})" if o["reasons"] else ""), "",
         f"Tolerance: {TOLERANCE}. Settlement: rule {o['settle_rule']} (capital_ceiling.SETTLE_RULE_E63; main's default is "
         "rule 2); the daily loss on the exit day (#566).", "", SCOPE, ""]
    if o["mode"] == "step0":
        L += ["Step 0 is informative only: job 87 also ran N2 (N2_F7 signals can merge F7's H6 clusters), so a difference "
              "here is not an engine verdict; the full job (step 1) is.", ""]
    L += [f"E63's side anchored: {o['anchors']}.", "",
          "| size | d | rows new / E63 | only new / only E63 | costs | SOL/day new / E63 (without limits) | "
          "max abs day diff without limits (lamports) | with limits | fields differing |", "|---|---|---|---|---|---|---|---|---|"]
    for c in o["cells"]:
        r = c["rows"]
        for scen, s in c["scenarios"].items():
            L.append(f"| {c['size']:g} | {c['d']} | {r['new_rows']} / {r['e63_rows']} | {r['only_new']} / {r['only_e63']} | {scen} | "
                     f"{s['sol_day'][0]} / {s['sol_day'][1]} | {s['day_unlimited']['max_abs_lamports']} | "
                     f"{max(x['max_abs_lamports'] for x in s['day_limited'].values())} | {len(s['fields'])} |")
    if o.get("json"):
        L += ["", "Step 2, the new ceiling.json against E63's: " + ", ".join(f"{k} {len(v)} differences" for k, v in o["json"].items())]
    for c in o["cells"]:
        for p in c["rows"]["pairs"][:5]:
            L.append(f"- {c['size']:g} SOL d {c['d']} {p['key']}: {p['columns']}")
    L += ["", "## Inputs", ""] + [f"- {k}: {v}" for k, v in o["inputs"].items()]
    return "\n".join(L) + "\n"


def compare(a):
    """The comparison (the module's docstring); returns rc 0 GREEN, 1 RED (full), 3 DIFF (step0, informative)."""
    if a.clean_bridge:
        raise Refused("step 4 (the clean conventions with the case exclusions on dev2) needs LAB research's OK: not built")
    e63_cfg, e63_json, exitday = check_inputs(a.e63, a.e63_exitday_json, a.c3_keys, a.store)
    new_cfg = check_new(a.mode, a.new, a.e63, e63_cfg, a.engine_dir, a.dry_run)
    if a.mode == "full" and not a.new_json and not a.dry_run:
        raise Refused("full: --new-json (step 2's ceiling.json) is required")
    os.makedirs(a.out)  # new: an existing --out is refused
    cons = {}
    for name in ("e63", "new"):
        con = duckdb.connect()
        con.execute(f"SET memory_limit = '{a.memory}'; SET threads = {a.threads}")
        if os.environ.get("LAB_SPILL_DIR"):
            con.execute(f"SET temp_directory = '{os.environ['LAB_SPILL_DIR']}'")
        cons[name] = con
    cons["e63"].execute(f"ATTACH '{a.store}' AS st (READ_ONLY)")
    day_of = cc.slot_days(cons["e63"])
    every = [(s, d) for s in SIZES for d in DELAYS]
    cc.load(cons["e63"], [a.e63], a.c3_keys)
    days = [r[0] for r in cons["e63"].execute("SELECT DISTINCT day FROM r ORDER BY 1").fetchall()]
    e63 = side(cons["e63"], every, days, day_of)
    anchor_e63(e63, days, e63_json, exitday)
    cc.load(cons["new"], [a.new], a.c3_keys)
    new_days = [r[0] for r in cons["new"].execute("SELECT DISTINCT day FROM r ORDER BY 1").fetchall()]
    cells = [STEP0_CELL] if a.mode == "step0" else every
    rng = new_cfg.get("slot_range") if a.dry_run else None
    if rng:  # a RANGE run: both sides cut to its slots and days
        cons["e63"].execute(f"DELETE FROM r WHERE t < {int(rng[0])} OR t >= {int(rng[1])}")
        days = new_days
        e63 = side(cons["e63"], cells, days, day_of)
    # Over the new run's own days, as capital_ceiling's main on it (step 2's json): days that are not E63's are a RED
    # reason of their own (below), not a refusal of step 2's json as another run's.
    new = side(cons["new"], cells, new_days, day_of)
    rows, common = rows_diff(cons["new"], a.new, a.e63, cells, rng)
    out_cells, reasons = [], []
    if new_days != days:
        reasons.append(f"the new run's days {new_days} are not E63's")
    for r in rows:
        if r["only_new"] or r["only_e63"]:
            reasons.append(f"{r['size']:g} SOL d {r['d']}: rows {r['only_new']} only new / {r['only_e63']} only E63")
        scen = {}
        for s in cc.SCENARIOS:
            k = (r["size"], r["d"], s)
            (cn, sn, _), (ce, se, _) = new[k], e63[k]
            fields = field_diffs(cn, ce)
            du = series_diff(sn["unlimited"], se["unlimited"])
            dl = {x: series_diff(sn[x], se[x]) for x in sn if x != "unlimited"}
            if fields or du["max_abs_lamports"] or any(v["max_abs_lamports"] for v in dl.values()):
                reasons.append(f"{r['size']:g} SOL d {r['d']} {s}: {len(fields)} fields, day series max |diff| "
                               f"{max([du['max_abs_lamports']] + [v['max_abs_lamports'] for v in dl.values()])} lamports")
            scen[s] = {"sol_day": [cn["unlimited"]["sol_day"], ce["unlimited"]["sol_day"]], "fields": fields,
                       "day_unlimited": du, "day_limited": dl}
        out_cells.append({"size": r["size"], "d": r["d"], "rows": r, "scenarios": scen})
    o = {"mode": a.mode, "dry_run": bool(rng), "tolerance": TOLERANCE, "scope": SCOPE, "settle_rule": cc.SETTLE_RULE_E63,
         "daily_loss_book": "exit_day", "days": days, "new_days": new_days, "common_columns": common,
         "new": {"run": a.new, "engine_commit": new_cfg["engine_commit"], "slot_range": new_cfg.get("slot_range")},
         "anchors": "GREEN (days, the exit-day json's H5 cells, ceiling.json without limits, descriptives.out)",
         "inputs": {**SHA256, "analysis_blobs": ANALYSIS, "pin_manifest": PIN_MANIFEST,
                    "e63": a.e63, "e63_exitday_json": a.e63_exitday_json, "store": a.store, "c3_keys": a.c3_keys},
         "cells": out_cells}
    if a.mode == "full" and not rng:
        o["json"] = json_checks(a.new_json, a.new, new, e63_json, exitday, days)
        reasons += [f"step 2 json: {k} {len(v)} differences" for k, v in o["json"].items() if v]
    o["reasons"] = reasons
    o["verdict"] = "GREEN" if not reasons else ("DIFF (informative)" if a.mode == "step0" else "RED")
    if rng:
        o["verdict"] = "DRY RUN (not evidence): " + o["verdict"]
    with open(os.path.join(a.out, "repro.json"), "w") as f:
        json.dump(o, f, indent=1, default=str)
    with open(os.path.join(a.out, "repro.md"), "w") as f:
        f.write(report_md(o))
    write_days(os.path.join(a.out, "days.csv"), [("new", new), ("e63", e63)])
    print(f"{a.mode}: {o['verdict']}; {len(cells)} cells, rows only new / only E63 "
          f"{sum(r['only_new'] for r in rows)} / {sum(r['only_e63'] for r in rows)} (repro.json, repro.md, days.csv)")
    if rng or not reasons:
        return 0
    return 3 if a.mode == "step0" else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("inputs", help="the archived inputs and dev2's identity, before the run")
    e = sub.add_parser("engine", help="a 15fa3dd snapshot's lab/backtest, file for file")
    e.add_argument("backtest_dir")
    c = sub.add_parser("compare", help="E63 anchored, then rows, day series and fields")
    for p in (i, c):
        p.add_argument("--e63", required=True, help="backtests/capital-ceiling-d15ac45")
        p.add_argument("--e63-exitday-json", required=True, help="backtests/capital-exitday-576db7a/e63/ceiling.json")
        p.add_argument("--c3-keys", required=True, help="wallets/paidflow-dev-vps/markout-f7rev-txdelay.parquet")
        p.add_argument("--store", required=True, help="store/dev2.duckdb (read-only)")
    i.add_argument("--bigtp", help="backtests/bigtp-dev-1033-1046-4dbd34b (step 0)")
    c.add_argument("--mode", choices=("step0", "full"), required=True)
    c.add_argument("--new", required=True, help="step0: job 87's run; full: the 15fa3dd run")
    c.add_argument("--engine-dir", help="full: the snapshot's backtest dir the run came from")
    c.add_argument("--new-json", help="full: step 2's ceiling.json of the new run")
    c.add_argument("--out", required=True)
    c.add_argument("--dry-run", action="store_true", help="a RANGE run: both sides cut to its slots; not evidence")
    c.add_argument("--clean-bridge", action="store_true", help="step 4: refused (needs LAB research's OK)")
    c.add_argument("--memory", default=os.environ.get("LAB_DUCKDB_MEM", "2GB"))
    c.add_argument("--threads", type=int, default=2)
    a = ap.parse_args(argv)
    if a.cmd == "engine":
        print(f"15fa3dd's lab/backtest, file for file: manifest {check_engine(a.backtest_dir)}")
        return 0
    if a.cmd == "inputs":
        cfg = check_inputs(a.e63, a.e63_exitday_json, a.c3_keys, a.store, a.bigtp)[0]
        print(f"inputs as archived; dev2's meta is E63's store block (git_commit {cfg['store'].get('git_commit')}); "
              f"analysis blobs reviewed; settle rule {cc.SETTLE_RULE_E63}")
        return 0
    return compare(a)


if __name__ == "__main__":
    sys.exit(main())
