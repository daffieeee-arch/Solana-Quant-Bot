"""Truncation invariance (look-ahead test): research/truncation-harness-spec v0.2 (LAB review 2026-10-20, sha256 14ce2335…).

For a cut slot T: run the pipeline (feed -> engine -> journal) on data that ends at T, and on the same data extended past
T. Every journal row decided at or before T must be identical; a difference is a read of data after its decision slot.

- Hidden facts at T (truncate_store): rows of blocks / curve / pool / pool_sweeps after T absent; mints created after T
  absent; a mint's complete_slot, migrate_slot and pool NULL when dated after T; pools created after T absent. The
  end-of-store aggregates (pool_stats, pool_class_daily) are left out on purpose: a reader of those reads the future.
  meta is kept with slot_end_exclusive = T + 1. The full input is the same construction at a later end.
- Both runs share every constant; the decision window's end guard is 0 in both (decision_bounds' end_guard_ms), so a
  decision may come right up to T; the burn-in is production's on the NAS, shorter in the VPS smoke (in both runs).
- Decided at or before T: signals.t <= T, orders.entry_slot <= T, fills.slot <= T, exits.exit_decision_slot < T with
  exit_reason not end_of_data / window_end. Run-local columns are not compared (receive, create and write times, latency).
- RED (compare): no signal at or before T, none in the last `near` slots before T, no hidden fact between T and the full
  input's end, or any row difference (table, counts, the first rows each side). An empty input is RED.

  python -m paper.truncation --store FULL --work DIR --from LO --cuts T1,T2 --end END [--burn-in-ms MS] [--near 3000]
"""

import argparse
import json
import os
import shutil
import sqlite3
import time

import duckdb

from backtest.strategies import BURN_IN_MS, F7_GRID, Clock

from .engine import Engine
from .feed import ReplayFeed
from .journal import Journal

# table: (decision-time column, the extra condition, run-local columns left out)
CHECKS = {
    "signals": ("t", "", {"received_unix_ns", "created_unix_ns", "latency_ms", "segment_written_unix", "segment_lag_ms",
                          "written_unix_ns", "arrival"}),
    "orders": ("entry_slot", "", set()),
    "fills": ("slot", "", set()),
    "exits": ("exit_decision_slot", " AND exit_decision_slot < :cut AND exit_reason NOT IN ('end_of_data', 'window_end')", set()),
}


class Red(Exception):
    """The harness's RED: an empty or meaningless input, or a difference."""


def truncate_store(full, out, lo, cut, memory="1500MB"):
    """The store `full` restricted to [lo, cut] with the facts after `cut` hidden, into the new file `out`. Returns its row
    counts; Red when it holds no block or no pool row."""
    if "'" in full:
        raise Red(f"a store path with a quote: {full!r}")
    if os.path.exists(out):
        raise Red(f"{out} exists: a copy is built once")
    con = duckdb.connect(out)
    try:
        con.execute(f"SET memory_limit = '{memory}'; SET threads = 2; SET preserve_insertion_order = true")
        con.execute(f"ATTACH '{full}' AS f (READ_ONLY)")
        con.execute(f"CREATE TABLE blocks AS SELECT * FROM f.blocks WHERE slot BETWEEN {lo} AND {cut} ORDER BY slot")
        for t in ("curve", "pool", "pool_sweeps"):
            con.execute(f"CREATE TABLE {t} AS SELECT * FROM f.{t} WHERE slot BETWEEN {lo} AND {cut} "
                        "ORDER BY slot, tx_index, outer_ix, inner_ix")
        con.execute(f"""CREATE TABLE mints AS SELECT * REPLACE (
            CASE WHEN complete_slot > {cut} THEN NULL ELSE complete_slot END AS complete_slot,
            CASE WHEN migrate_slot > {cut} THEN NULL ELSE migrate_slot END AS migrate_slot,
            CASE WHEN migrate_slot > {cut} THEN NULL ELSE pool END AS pool)
            FROM f.mints WHERE create_slot IS NULL OR create_slot <= {cut}""")
        con.execute(f"CREATE TABLE pools AS SELECT * FROM f.pools WHERE create_slot IS NULL OR create_slot <= {cut}")
        meta = json.loads(con.execute("SELECT json FROM f.meta").fetchone()[0])
        meta.update(truncated_at=cut, slot_end_exclusive=cut + 1)
        con.execute("CREATE TABLE meta (json VARCHAR)")
        con.execute("INSERT INTO meta VALUES (?)", [json.dumps(meta)])
        counts = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("blocks", "curve", "pool", "pool_sweeps", "mints", "pools")}
    finally:
        con.close()
    if not counts["blocks"] or not counts["pool"]:
        raise Red(f"the copy of [{lo}, {cut}] is empty: {counts}")
    return counts


def hidden_between(full, cut, end):
    """The facts of the full store dated in (cut, end]: completions, migrations, mints and pools created."""
    con = duckdb.connect(full, read_only=True)
    try:
        c, m, n = con.execute(f"""SELECT count(*) FILTER (WHERE complete_slot > {cut} AND complete_slot <= {end}),
                                         count(*) FILTER (WHERE migrate_slot > {cut} AND migrate_slot <= {end}),
                                         count(*) FILTER (WHERE create_slot > {cut} AND create_slot <= {end}) FROM mints""").fetchone()
        p = con.execute(f"SELECT count(*) FROM pools WHERE create_slot > {cut} AND create_slot <= {end}").fetchone()[0]
    finally:
        con.close()
    return {"completions": c, "migrations": m, "mints_created": n, "pools_created": p}


def run_replay(store, lo, hi, journal, *, burn_in_ms=BURN_IN_MS, engine_cls=Engine, variants=F7_GRID):
    """paper.engine's replay of [lo, hi) on `store` with the harness's window (end guard 0), risk inert, into `journal`.
    engine_cls: the engine class (a test hook may plant a look-ahead). Returns (events, seconds)."""
    feed = ReplayFeed(store, lo, hi)
    j = Journal(journal)
    try:
        eng = engine_cls(feed, j, None, variants=variants, ignore_risk=True, clock=Clock.from_store(feed.con), backup=False,
                         burn_in_ms=burn_in_ms, end_guard_ms=0)
        t0 = time.time()
        eng.run()
        return eng.events_seen, round(time.time() - t0, 1)
    finally:
        j.close()
        feed.con.close()


def _rows(path, table, col, extra, skip, cut):
    db = sqlite3.connect(f"file:{os.path.join(path, 'journal.sqlite')}?mode=ro", uri=True)
    try:
        cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})") if r[1] not in skip]
        q = f"SELECT {', '.join(cols)} FROM {table} WHERE {col} <= :cut{extra} ORDER BY {', '.join(cols)}"
        return [tuple(r) for r in db.execute(q, {"cut": cut})]
    finally:
        db.close()


def compare(j_cut, j_full, cut, near=3_000, hidden=None):
    """The rows decided at or before `cut` in both journals. Returns the result; its status is GREEN or RED (`why`)."""
    res, diffs = {"cut": cut, "compared": {}}, []
    for table, (col, extra, skip) in CHECKS.items():
        a, b = _rows(j_cut, table, col, extra, skip, cut), _rows(j_full, table, col, extra, skip, cut)
        res["compared"][table] = [len(a), len(b)]
        if a != b:
            sa, sb = set(a), set(b)
            diffs.append({"table": table, "cut": len(a), "full": len(b), "only_cut": [list(r) for r in sorted(sa - sb)[:3]],
                          "only_full": [list(r) for r in sorted(sb - sa)[:3]]})
    db = sqlite3.connect(f"file:{os.path.join(j_cut, 'journal.sqlite')}?mode=ro", uri=True)
    try:
        res["near_signals"] = db.execute("SELECT count(*) FROM signals WHERE t > ? AND t <= ?", (cut - near, cut)).fetchone()[0]
    finally:
        db.close()
    res["near"], res["hidden"], res["diffs"] = near, hidden, diffs
    why = []
    if not res["compared"]["signals"][0]:
        why.append("no signal at or before the cut: the comparison is empty")
    if not res["near_signals"]:
        why.append(f"no signal in the last {near} slots before the cut: the hidden data could not have mattered")
    if hidden is not None and not sum(hidden.values()):
        why.append("no hidden fact between the cut and the full input's end: the two inputs are the same")
    if diffs:
        why.append("the truncation changed decisions at or before the cut: " + ", ".join(f"{d['table']} {d['cut']} vs {d['full']}" for d in diffs))
    res["status"], res["why"] = ("RED", why) if why else ("GREEN", [])
    return res


def harness(full, work, lo, cuts, end, *, burn_in_ms=BURN_IN_MS, near=3_000, engine_cls=Engine, keep_copies=False, log=print):
    """One full run on [lo, end] and a truncated run per cut, each compared at its cut. Store copies are built in `work`
    and removed after their run unless keep_copies. Returns {"full": ..., "cuts": [compare results]}."""
    os.makedirs(work, exist_ok=True)
    out = {"store": os.path.abspath(full), "lo": lo, "end": end, "burn_in_ms": burn_in_ms, "cuts": []}

    def one(name, cut, hi):
        store, journal = os.path.join(work, f"{name}.duckdb"), os.path.join(work, f"j_{name}")
        counts = truncate_store(full, store, lo, cut)
        events, secs = run_replay(store, lo, hi, journal, burn_in_ms=burn_in_ms, engine_cls=engine_cls)
        if not keep_copies:
            os.remove(store)
        log(f"{name}: [{lo}, {cut}] {counts['pool']:,} pool rows, {events:,} events, {secs} s")
        return journal, {"counts": counts, "events": events, "seconds": secs}

    j_full, out["full"] = one("full", end, end + 1)
    for cut in sorted(cuts):
        if not lo < cut < end:
            raise Red(f"cut {cut} not inside ({lo}, {end})")
        j_cut, info = one(f"cut_{cut}", cut, cut + 1)
        r = compare(j_cut, j_full, cut, near=near, hidden=hidden_between(full, cut, end))
        r.update(info)
        log(f"cut {cut}: {r['status']} {r['compared']} near {r['near_signals']} hidden {r['hidden']}" + (f" {r['why']}" if r["why"] else ""))
        out["cuts"].append(r)
    out["status"] = "GREEN" if out["cuts"] and all(r["status"] == "GREEN" for r in out["cuts"]) else "RED"
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--store", required=True)
    ap.add_argument("--work", required=True, help="a new directory for the copies and journals")
    ap.add_argument("--from", dest="lo", type=int, required=True)
    ap.add_argument("--cuts", required=True, help="comma-separated cut slots inside (from, end)")
    ap.add_argument("--end", type=int, required=True, help="the full input's last slot")
    ap.add_argument("--burn-in-ms", type=float, default=BURN_IN_MS)
    ap.add_argument("--near", type=int, default=3_000)
    ap.add_argument("--out", help="the result JSON (default WORK/result.json)")
    a = ap.parse_args(argv)
    try:
        res = harness(a.store, a.work, a.lo, [int(x) for x in a.cuts.split(",") if x], a.end, burn_in_ms=a.burn_in_ms, near=a.near)
    except Red as e:
        res = {"status": "RED", "why": [str(e)]}
    with open(a.out or os.path.join(a.work, "result.json"), "w") as f:
        json.dump(res, f, indent=1)
    print(f"truncation: {res['status']}")
    raise SystemExit(0 if res["status"] == "GREEN" else 1)


if __name__ == "__main__":
    main()
