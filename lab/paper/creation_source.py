"""The creation source of a live paper run's pools (research/recorder-creation-filters-spec-2026-10-20.md v0.2 §7, LAB
review M5): which of the journal's pool rows sit on a pool the recording saw created, on one the paper store knew at
the start, or on one the feed learned from the recorder's `pool_creations` / `pool_accounts` account stream alone. The
last are old pools learned as new (the "fresh fees" filter is refuted, spec §1.2): within the backtest's "pools of any
age", but a biased slice, labelled so the owner can decide whether they count for the stop rule.

  python -m paper.creation_source --journal DIR --root REC [--store STORE | --no-store] [--day YYYY-MM-DD ...] [--out DIR]

Read-only: the journal is opened mode=ro (the engine keeps writing it in WAL), the recording's Parquet and the store
with DuckDB (memory_limit 1 GB, one thread). Only closed segments (their `_segment.json` complete) are read: the open
one's files may be mid-write. Writes atomically, beside the engine's daily report (--out: elsewhere):
  <journal>/<day>/creation_source.md   the report section of each day the engine ran (a quiet day: "0 pool rows")
  <journal>/creation_source.json       every day and the inputs read, for the web app's Paper tab (lab/ui)
It runs outside the engine process (a timer or by hand), so the engine and its report.md are not touched.

A pool's class (`creation_source`), in this order (the feed's: a store pool is never learned again):
  created         a CreatePoolEvent of it in the recording (live or a probe's backfill)
  store           in the paper store's pools (--store, else the journal's farmer_seed store)
  pool_creations  neither: learned from the account stream without a creation
Counted per day, group and class: the signals of pool rows by status, their orders by status, their fills by side,
and their exits with the sum of their P&L (lamports, the journal's `pnl`).

Refused (exit 1, nothing written): a recording without closed segments, a journal that cannot be read or has no signals
table, and a store that is named (by --store or the journal) but cannot be read. Only --no-store skips the store
class, and the JSON then says `store: null`.
"""

import argparse
import glob
import json
import os
import sqlite3
from collections import defaultdict

import duckdb

CLASSES = ("created", "store", "pool_creations")


def _refuse(why):
    raise SystemExit(f"creation_source: {why}: refused, nothing written")


def journal_rows(journal):
    """The journal's pool rows: signals, orders, fills and exits, each with its key; the groups' variants, the days the
    engine ran (heartbeat and signals), the signals read and the journal's store."""
    path = os.path.join(journal, "journal.sqlite")
    if not os.path.isfile(path):
        _refuse(f"no journal at {path}")
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if "signals" not in tables:
                _refuse(f"{path} has no signals table")
            sig = db.execute("SELECT family, variant, key, t, day, venue, status FROM signals").fetchall()
            pool_keys = {(f, v, k, t) for f, v, k, t, _, venue, _ in sig if venue == "pool"}
            pick = lambda sql: [r for r in db.execute(sql).fetchall() if r[:4] in pool_keys]  # noqa: E731
            orders = pick("SELECT family, variant, key, t, day, status FROM orders")
            fills = pick("SELECT family, variant, key, t, day, side FROM fills")
            exits = pick("SELECT family, variant, key, t, day, pnl FROM exits")
            state = dict(db.execute("SELECT k, v FROM state WHERE k IN ('groups', 'farmer_seed')").fetchall())
            days = {r[0] for r in db.execute("SELECT DISTINCT day FROM heartbeat WHERE day IS NOT NULL").fetchall()} if "heartbeat" in tables else set()
        finally:
            db.close()
    except sqlite3.Error as e:
        _refuse(f"{path} cannot be read ({e})")
    group_of = {}
    for name, g in json.loads(state.get("groups") or "{}").items():
        for v in g.get("variants") or ():
            group_of[v] = name
    days |= {r[4] for r in sig if r[4]}
    return {"signals": [r for r in sig if r[5] == "pool"], "orders": orders, "fills": fills, "exits": exits,
            "group_of": group_of, "store": json.loads(state.get("farmer_seed") or "{}").get("store"), "days": sorted(days),
            "signals_read": len(sig)}


def closed_segments(root):
    """(segment directories scanned, the closed ones): those whose _segment.json says complete."""
    base = os.path.join(root, "segments")
    if not os.path.isdir(base):
        _refuse(f"no segments/ under {root}")
    dirs = sorted(d for d in glob.glob(os.path.join(base, "*")) if os.path.isdir(d) and not os.path.basename(d).startswith("."))
    closed = []
    for d in dirs:
        try:
            with open(os.path.join(d, "_segment.json")) as f:
                if json.load(f).get("complete"):
                    closed.append(d)
        except (OSError, ValueError):
            continue  # the open segment (no _segment.json yet), or one being written
    if not closed:
        _refuse(f"no closed segment under {base}")
    return dirs, closed


def created_pools(closed):
    files = [f for d in closed for t in ("pump_amm/CreatePoolEvent", "backfill/pump_amm/CreatePoolEvent")
             for f in glob.glob(os.path.join(d, t, "*.parquet"))]
    if not files:
        return set()
    con = duckdb.connect(config={"memory_limit": "1GB", "threads": 1})
    try:
        return {r[0] for r in con.execute("SELECT DISTINCT pool FROM read_parquet(?, union_by_name = true)", [files]).fetchall()}
    finally:
        con.close()


def store_pools(store, pools):
    """The pools of `pools` in the store's pools table; a store that cannot be read is refused."""
    if not os.path.isfile(store):
        _refuse(f"the store {store} cannot be read (give --store, or --no-store to skip the store class)")
    try:
        st = duckdb.connect(store, read_only=True, config={"memory_limit": "512MB", "threads": 1})
        try:
            return {r[0] for r in st.execute("SELECT pool FROM pools WHERE pool IN (SELECT unnest(?::VARCHAR[]))", [sorted(pools)]).fetchall()}
        finally:
            st.close()
    except duckdb.Error as e:
        _refuse(f"the store {store} cannot be read ({e})")


def counts(rows, cls):
    """Per day: per group and class the signals by status, orders by status, fills by side, exits and their P&L."""
    out = defaultdict(lambda: defaultdict(lambda: {"signals": defaultdict(int), "orders": defaultdict(int),
                                                   "fills": defaultdict(int), "exits": 0, "pnl": 0}))
    group = lambda v: rows["group_of"].get(v, "?")  # noqa: E731
    for f, v, k, t, day, _, status in rows["signals"]:
        out[day][(group(v), cls[k])]["signals"][status or "undecided"] += 1
    for f, v, k, t, day, status in rows["orders"]:
        out[day][(group(v), cls[k])]["orders"][status] += 1
    for f, v, k, t, day, side in rows["fills"]:
        out[day][(group(v), cls[k])]["fills"][side] += 1
    for f, v, k, t, day, pnl in rows["exits"]:
        c = out[day][(group(v), cls[k])]
        c["exits"] += 1
        c["pnl"] += pnl or 0
    return {day: {f"{g}/{c}": {"signals": dict(x["signals"]), "orders": dict(x["orders"]), "fills": dict(x["fills"]),
                               "exits": x["exits"], "pnl_lamports": x["pnl"]}
                  for (g, c), x in sorted(by.items())}
            for day, by in out.items()}


def section(day, by, pools_with_rows, pool_rows):
    """The day's report section (Markdown)."""
    lines = [f"## Creation source of the pool rows, {day}", "",
             "A pool row's pool: `created` (a CreatePoolEvent in the recording), `store` (known at the start), "
             "`pool_creations` (learned from the account stream without a creation: an old pool learned as new). "
             "The owner decides whether `pool_creations` rows count for the stop rule.", ""]
    if not pool_rows:
        return "\n".join(lines + ["0 pool rows this day.", ""])
    lines += ["| group / class | signals | orders | fills | exits | P&L (SOL) |", "|---|---|---|---|---|---|"]
    fmt = lambda d: ", ".join(f"{k} {n}" for k, n in sorted(d.items())) or "-"  # noqa: E731
    for key, x in by.items():
        lines.append(f"| {key} | {fmt(x['signals'])} | {fmt(x['orders'])} | {fmt(x['fills'])} | {x['exits']} | "
                     f"{x['pnl_lamports'] / 1e9:+.4f} |")
    lines += ["", f"{pool_rows} pool rows. Pools with rows: " + ", ".join(f"{c} {pools_with_rows.get(c, 0)}" for c in CLASSES), ""]
    return "\n".join(lines)


def _write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def run(journal, root, store=None, no_store=False, days=None, out=None):
    """Compute, write the files (module doc; under `out` when given) and return the JSON. Every refusal comes before any
    file is written."""
    out = out or journal
    rows = journal_rows(journal)
    scanned, closed = closed_segments(root)
    pools = {r[2] for r in rows["signals"]}
    created = created_pools(closed)
    if no_store:
        store, known = None, set()
    else:
        store = store or rows["store"]
        if not store:
            _refuse("no store named (by --store or the journal's farmer_seed): give --store, or --no-store")
        known = store_pools(store, pools) if pools else set()
    cls = {p: "created" if p in created else "store" if p in known else "pool_creations" for p in pools}
    by_day = counts(rows, cls)
    pools_by_day = defaultdict(lambda: defaultdict(set))
    rows_by_day = defaultdict(int)
    for f, v, k, t, day, _, _ in rows["signals"]:
        pools_by_day[day][cls[k]].add(k)
    for table in ("signals", "orders", "fills", "exits"):
        for r in rows[table]:
            rows_by_day[r[4]] += 1
    doc = {"journal": os.path.abspath(journal), "root": os.path.abspath(root), "store": store, "classes": list(CLASSES),
           "inputs": {"segments_scanned": len(scanned), "segments_closed": len(closed), "created_pools_found": len(created),
                      "store_pools_matched": len(known), "signals_read": rows["signals_read"],
                      "pool_rows": sum(rows_by_day.values())},
           "pools": {c: sorted(p for p, x in cls.items() if x == c) for c in CLASSES}, "days": {}}
    texts = {}
    for day in sorted(set(rows["days"]) | {d for d in by_day if d}):
        n = {c: len(ps) for c, ps in pools_by_day[day].items()}
        doc["days"][day] = {"pool_rows": rows_by_day[day], "rows": by_day.get(day, {}), "pools_with_rows": n}
        if not days or day in days:
            texts[day] = section(day, by_day.get(day, {}), n, rows_by_day[day])
    for day, text in texts.items():
        os.makedirs(os.path.join(out, day), exist_ok=True)
        _write(os.path.join(out, day, "creation_source.md"), text)
    os.makedirs(out, exist_ok=True)
    _write(os.path.join(out, "creation_source.json"), json.dumps(doc, indent=1, sort_keys=True))
    return doc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--journal", required=True, help="the engine's --journal directory (with journal.sqlite)")
    ap.add_argument("--root", required=True, help="the recorder's directory (its segments/)")
    ap.add_argument("--store", help="the paper store (default: the journal's farmer_seed store)")
    ap.add_argument("--no-store", action="store_true", help="skip the store class (the JSON says store: null)")
    ap.add_argument("--day", action="append", help="write only these days' sections (default: every day)")
    ap.add_argument("--out", help="write the files here instead of beside the journal")
    a = ap.parse_args(argv)
    if a.store and a.no_store:
        ap.error("--store or --no-store, not both")
    doc = run(a.journal, a.root, a.store, a.no_store, set(a.day) if a.day else None, a.out)
    print(json.dumps(doc["inputs"]))
    for day, d in doc["days"].items():
        print(day, d["pool_rows"], json.dumps(d["pools_with_rows"]), json.dumps(d["rows"]))


if __name__ == "__main__":
    main()
