"""The creation source of a live paper run's pools (research/recorder-creation-filters-spec-2026-10-20.md v0.2 §7, LAB
review M5): which of the journal's pool rows sit on a pool the recording saw created, on one the paper store knew at
the start, or on one the feed learned from the recorder's `pool_creations` / `pool_accounts` account stream alone. The
last are old pools learned as new (the "fresh fees" filter is refuted, spec §1.2): within the backtest's "pools of any
age", but a biased slice, labelled so the owner can decide whether they count for the stop rule.

  python -m paper.creation_source --journal DIR --root REC [--store STORE] [--day YYYY-MM-DD ...] [--out DIR]

Read-only: the journal is opened mode=ro (the engine keeps writing it in WAL), the recording's Parquet and the store
with DuckDB (memory_limit 1 GB, one thread). Writes, beside the engine's daily report (--out: elsewhere):
  <journal>/<day>/creation_source.md   the report section of each day with pool rows
  <journal>/creation_source.json       every day, for the web app's Paper tab (lab/ui)
It runs outside the engine process (a timer or by hand), so the engine and its report.md are not touched.

A pool's class (`creation_source`), in this order:
  created         a CreatePoolEvent of it in the recording (live or a probe's backfill)
  store           in the paper store's pools (known at the start; --store, else the journal's farmer_seed store)
  pool_creations  neither: learned from the account stream without a creation
Counted per day, group and class: the signals of pool rows by status, their orders by status, their fills by side,
and their exits with the sum of their P&L (lamports, the journal's `pnl`).
"""

import argparse
import glob
import json
import os
import sqlite3
from collections import defaultdict

import duckdb

CLASSES = ("created", "store", "pool_creations")


def _rows(db, sql):
    return db.execute(sql).fetchall()


def journal_rows(journal):
    """The journal's pool rows: signals, orders, fills and exits, each with its key, and the groups' variants."""
    db = sqlite3.connect(f"file:{os.path.join(journal, 'journal.sqlite')}?mode=ro", uri=True, timeout=5)
    try:
        sig = _rows(db, "SELECT family, variant, key, t, day, venue, status FROM signals")
        pool_keys = {(f, v, k, t) for f, v, k, t, _, venue, _ in sig if venue == "pool"}
        orders = [r for r in _rows(db, "SELECT family, variant, key, t, day, status FROM orders") if r[:4] in pool_keys]
        fills = [r for r in _rows(db, "SELECT family, variant, key, t, day, side FROM fills") if r[:4] in pool_keys]
        exits = [r for r in _rows(db, "SELECT family, variant, key, t, day, pnl FROM exits") if r[:4] in pool_keys]
        state = dict(_rows(db, "SELECT k, v FROM state WHERE k IN ('groups', 'farmer_seed')"))
    finally:
        db.close()
    group_of = {}
    for name, g in json.loads(state.get("groups") or "{}").items():
        for v in g.get("variants") or ():
            group_of[v] = name
    store = json.loads(state.get("farmer_seed") or "{}").get("store")
    return {"signals": [r for r in sig if r[5] == "pool"], "orders": orders, "fills": fills, "exits": exits,
            "group_of": group_of, "store": store}


def _parquet(con, root, tables, column="pool"):
    files = [f for t in tables for f in glob.glob(os.path.join(root, "segments", "*", t, "*.parquet"))]
    if not files:
        return set()
    return {r[0] for r in con.execute(f"SELECT DISTINCT {column} FROM read_parquet(?, union_by_name = true)", [files]).fetchall()}


def classes(pools, root, store):
    """{pool: class} for `pools` (module doc)."""
    con = duckdb.connect(config={"memory_limit": "1GB", "threads": 1})
    try:
        created = _parquet(con, root, ("pump_amm/CreatePoolEvent", "backfill/pump_amm/CreatePoolEvent"))
    finally:
        con.close()
    known = set()
    if store and pools:
        st = duckdb.connect(store, read_only=True, config={"memory_limit": "512MB", "threads": 1})
        try:
            known = {r[0] for r in st.execute("SELECT pool FROM pools WHERE pool IN (SELECT unnest(?::VARCHAR[]))", [sorted(pools)]).fetchall()}
        finally:
            st.close()
    return {p: "created" if p in created else "store" if p in known else "pool_creations" for p in pools}


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
            for day, by in sorted(out.items(), key=lambda kv: str(kv[0]))}


def section(day, by, pools_by_class):
    """The day's report section (Markdown)."""
    lines = [f"## Creation source of the pool rows, {day}", "",
             "A pool row's pool: `created` (a CreatePoolEvent in the recording), `store` (known at the start), "
             "`pool_creations` (learned from the account stream without a creation: an old pool learned as new). "
             "The owner decides whether `pool_creations` rows count for the stop rule.", "",
             "| group / class | signals | orders | fills | exits | P&L (SOL) |", "|---|---|---|---|---|---|"]
    fmt = lambda d: ", ".join(f"{k} {n}" for k, n in sorted(d.items())) or "-"  # noqa: E731
    for key, x in by.items():
        lines.append(f"| {key} | {fmt(x['signals'])} | {fmt(x['orders'])} | {fmt(x['fills'])} | {x['exits']} | "
                     f"{x['pnl_lamports'] / 1e9:+.4f} |")
    lines += ["", "Pools with rows: " + ", ".join(f"{c} {pools_by_class.get(c, 0)}" for c in CLASSES), ""]
    return "\n".join(lines)


def run(journal, root, store=None, days=None, out=None):
    """Compute, write the files (module doc; under `out` when given) and return the JSON."""
    out = out or journal
    rows = journal_rows(journal)
    pools = {r[2] for r in rows["signals"]}
    store = store or rows["store"]
    cls = classes(pools, root, store if store and os.path.isfile(store) else None)
    by_day = counts(rows, cls)
    pools_by_day = defaultdict(lambda: defaultdict(set))
    for f, v, k, t, day, _, _ in rows["signals"]:
        pools_by_day[day][cls[k]].add(k)
    doc = {"journal": os.path.abspath(journal), "root": os.path.abspath(root), "store": store,
           "classes": list(CLASSES), "pools": {c: sorted(p for p, x in cls.items() if x == c) for c in CLASSES}, "days": {}}
    for day, by in by_day.items():
        n = {c: len(ps) for c, ps in pools_by_day[day].items()}
        doc["days"][str(day)] = {"rows": by, "pools_by_class": n}
        if days and day not in days:
            continue
        if day:
            os.makedirs(os.path.join(out, day), exist_ok=True)
            with open(os.path.join(out, day, "creation_source.md"), "w") as f:
                f.write(section(day, by, n))
    os.makedirs(out, exist_ok=True)
    tmp = os.path.join(out, "creation_source.json.tmp")
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
    os.replace(tmp, os.path.join(out, "creation_source.json"))
    return doc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--journal", required=True, help="the engine's --journal directory (with journal.sqlite)")
    ap.add_argument("--root", required=True, help="the recorder's directory (its segments/)")
    ap.add_argument("--store", help="the paper store (default: the journal's farmer_seed store)")
    ap.add_argument("--day", action="append", help="write only these days' sections (default: every day)")
    ap.add_argument("--out", help="write the files here instead of beside the journal")
    a = ap.parse_args(argv)
    doc = run(a.journal, a.root, a.store, set(a.day) if a.day else None, a.out)
    for day, d in doc["days"].items():
        print(day, json.dumps(d["pools_by_class"]), json.dumps(d["rows"]))


if __name__ == "__main__":
    main()
