"""Reconcile the live recorder against Old Faithful once an epoch is archived.

The recorder only receives the transactions of its watchlist (pools and wallets) and, when its
watchlist.json says new_pools, every pump.fun graduation (account_required [PumpSwap, withdraw
authority]). For every complete segment inside complete OF1 chunks, the event rows of the watchlist
pools, of the watched wallets and of graduation transactions (those with a
CompletePumpAmmMigrationEvent) must equal OF1's rows for the same slots: the same positions, and
every column the same (amounts, reserves, fees, parent instruction and accounts). Anything else is
a gap (missing live), an extra row, or a mismatch, counted per table and column.

  python -m paper.reconcile <recorder_root> [--out reconcile.json]    # OF1 chunks from LAB_CHUNKS

Exit code 1 when any segment has a gap, an extra row or a mismatch.
"""

import argparse
import glob
import json
import os
import sys

from backtest import data

from .live import segment_dirs

KEY = ("slot", "tx_index", "outer_ix", "inner_ix")
MIGRATION = "pump/CompletePumpAmmMigrationEvent"


def watch_of(seg):
    """The segment's effective transaction filter: (pools, wallets, graduations). Accepts the
    watchlist builder's format (pools as objects with a "pool" field) and plain address lists."""
    path = os.path.join(seg, "watchlist.json")
    if not os.path.exists(path):
        return set(), set(), False
    with open(path) as f:
        w = json.load(f)
    pools = {p["pool"] if isinstance(p, dict) else p for p in w.get("pools", ())}
    wallets = {x["wallet"] if isinstance(x, dict) else x for x in w.get("wallets", ())}
    return pools, wallets, bool(w.get("new_pools"))


def _lit(values):
    return "[" + ",".join("'" + v.replace("'", "''") + "'" for v in sorted(values)) + "]" if values else "[]"


def reconcile_segment(con, seg, meta, chunks):
    """{table: {live, of1, missing, extra, mismatched, columns: {col: n}}} for one segment."""
    lo, hi = meta["first_slot"], meta["last_slot"]
    pools, wallets, graduations = watch_of(seg)
    mig_l = data.parquet(MIGRATION, [(lo, hi + 1, seg)], missing_ok=True)
    mig_o = data.parquet(MIGRATION, chunks, missing_ok=True)

    def graduation(src):
        return f"EXISTS (SELECT 1 FROM {src} m WHERE m.slot = x.slot AND m.tx_index = x.tx_index)" if src else "false"

    out = {}
    tables = sorted({os.path.relpath(os.path.dirname(f), seg) for f in glob.glob(os.path.join(seg, "pump*", "*", "*.parquet"))}
                    | {os.path.relpath(os.path.dirname(f), d) for _, _, d in chunks
                       for f in glob.glob(os.path.join(d, "pump*", "*", "*.parquet"))})
    for t in tables:
        live = data.parquet(t, [(lo, hi + 1, seg)], missing_ok=True)
        of1 = data.parquet(t, chunks, missing_ok=True)
        cols_l = data.columns(t, [(lo, hi + 1, seg)]) if live else set()
        cols_o = data.columns(t, chunks) if of1 else set()
        cols = cols_l or cols_o
        conds = []
        if "pool" in cols and pools:
            conds.append(f"x.pool IN (SELECT unnest({_lit(pools)}))")
        if "user" in cols and wallets:
            conds.append(f"x.\"user\" IN (SELECT unnest({_lit(wallets)}))")
        if not conds and not graduations:
            continue  # neither the table's pools nor its wallets are watched
        where_l = " OR ".join(conds + ([graduation(mig_l)] if graduations else []))
        where_o = " OR ".join(conds + ([graduation(mig_o)] if graduations else []))
        empty = "SELECT NULL::UBIGINT AS slot, NULL::BIGINT AS tx_index, NULL::BIGINT AS outer_ix, NULL::BIGINT AS inner_ix WHERE false"
        l_sql = f"SELECT x.* FROM {live} x WHERE ({where_l})" if live else empty
        o_sql = f"SELECT x.* FROM {of1} x WHERE x.slot BETWEEN {lo} AND {hi} AND ({where_o})" if of1 else empty
        common = sorted((cols_l & cols_o) - set(KEY)) if live and of1 else []
        flags = "".join(f', (l."{c}" IS DISTINCT FROM o."{c}") AS d{i}' for i, c in enumerate(common))
        differs = " OR ".join(f"d{i}" for i in range(len(common))) or "false"
        per_col = "".join(f", count(*) FILTER (WHERE in_l AND in_o AND d{i})" for i in range(len(common)))
        row = con.execute(f"""
            WITH l AS ({l_sql}), o AS ({o_sql}),
            j AS (SELECT l.slot IS NOT NULL AS in_l, o.slot IS NOT NULL AS in_o{flags}
                  FROM l FULL OUTER JOIN o ON {" AND ".join(f"l.{k} = o.{k}" for k in KEY)})
            SELECT count(*) FILTER (WHERE in_l), count(*) FILTER (WHERE in_o),
                   count(*) FILTER (WHERE in_o AND NOT in_l), count(*) FILTER (WHERE in_l AND NOT in_o),
                   count(*) FILTER (WHERE in_l AND in_o AND ({differs})){per_col}
            FROM j""").fetchone()
        res = dict(zip(("live", "of1", "missing", "extra", "mismatched"), row[:5]))
        res["columns"] = {c: n for c, n in zip(common, row[5:]) if n}
        if res["live"] or res["of1"]:
            out[t] = res
    return out


def run(root, chunks_root=None):
    chunks = data.complete_chunks(chunks_root)
    con = data.connect(threads=2, memory=os.environ.get("LAB_DUCKDB_MEM", "1GB"))
    report = {"segments": [], "totals": {"missing": 0, "extra": 0, "mismatched": 0, "segments_checked": 0,
                                         "segments_waiting": 0}}
    for seq, seg, meta in segment_dirs(root):
        lo, hi = meta["first_slot"], meta["last_slot"]
        cover = [c for c in chunks if c[0] <= hi and lo < c[1]]
        if not cover or cover[0][0] > lo or cover[-1][1] <= hi:
            report["totals"]["segments_waiting"] += 1  # not archived yet
            continue
        res = reconcile_segment(con, seg, meta, cover)
        report["segments"].append({"seq": seq, "slots": [lo, hi], "tables": res})
        report["totals"]["segments_checked"] += 1
        for r in res.values():
            for k in ("missing", "extra", "mismatched"):
                report["totals"][k] += r[k]
    t = report["totals"]
    report["ok"] = t["missing"] == t["extra"] == t["mismatched"] == 0
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--chunks", default=None, help="OF1 chunks root (default LAB_CHUNKS)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    report = run(a.root, a.chunks)
    text = json.dumps(report, indent=1, default=str)
    if a.out:
        with open(a.out, "w") as f:
            f.write(text)
    print(json.dumps(report["totals"]), "OK" if report["ok"] else "GAPS OR MISMATCHES")
    sys.exit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
