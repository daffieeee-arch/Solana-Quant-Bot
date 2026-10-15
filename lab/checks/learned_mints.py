"""The mints the extractor learned in the stream, per transfers chunk (_learned_mints.txt; LAB_TRANSFERS=1),
against the chunk's own pump.fun curve events and the stores' mints, and the rows of known non-pump
mints (PUMP, USDC, ...) in the transfer tables.

  python -m checks.learned_mints <chunks root> [--store FILE ...] [--out FILE]   # JSON; exit 1 on a failure

Each learned mint gets one source, the first that holds:
  create_or_trade  a pump/CreateEvent or pump/TradeEvent of it in the chunk
  curve_event      another curve event whose mint is the curve's token (fee distributions and sweeps,
                   completion, creator changes: the extractor's CURVE_MINT_EVENTS since 1b16335)
  store            a store's mints table has it (--store)
  incentive        only a token incentive event (ClaimTokenIncentivesEvent, AdminUpdateTokenIncentivesEvent):
                   their mint is the incentive token, PUMP. Builds before 1b16335 learned it; a failure
  none             no event of it in the chunk and no store: a failure
Rows of the known non-pump mints are counted per table (filterable afterwards; PUMP from 0d915ac).
"""

import argparse
import glob
import json
import os
import sys

import duckdb

CURVE_MINT_EVENTS = ["CompleteEvent", "CompletePumpAmmMigrationEvent", "PostCompleteBuyEvent", "DistributeCreatorFeesEvent",
                     "DistributeFeeToHoldersEvent", "SweepBondingCurveFeeEvent", "MigrateBondingCurveCreatorEvent",
                     "SetCreatorEvent", "SetMetaplexCreatorEvent", "AdminCtoEvent", "UpdateMayhemVirtualParamsEvent"]
INCENTIVE_EVENTS = ["ClaimTokenIncentivesEvent", "AdminUpdateTokenIncentivesEvent"]
NON_PUMP = {
    "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn": "PUMP",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
    "USD1ttGY1N17NEEHLmELoaybftRBUSErhqYiQzvEmuB": "USD1",
    "So11111111111111111111111111111111111111112": "WSOL",
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": "BONK",
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN": "JUP",
}


def _mints_of(con, chunk, events):
    """Distinct mints of these pump/ event tables in the chunk (tables without files are skipped)."""
    fs = [f for e in events for f in glob.glob(os.path.join(chunk, "pump", e, "*.parquet"))]
    if not fs:
        return set()
    lit = "[" + ",".join(f"'{f}'" for f in fs) + "]"
    return {m for (m,) in con.execute(f"SELECT DISTINCT mint FROM read_parquet({lit}, union_by_name = true) WHERE mint IS NOT NULL").fetchall()}


def check_chunk(con, chunk, store_aliases):
    learned_file = os.path.join(chunk, "_learned_mints.txt")
    if not os.path.exists(learned_file):
        return {"chunk": os.path.basename(chunk), "learned_file": False}
    learned = [m for m in open(learned_file).read().split() if m]
    sources = {}
    remaining = set(learned)
    for name, events in (("create_or_trade", ["CreateEvent", "TradeEvent"]), ("curve_event", CURVE_MINT_EVENTS)):
        hit = remaining & _mints_of(con, chunk, events)
        sources.update({m: name for m in hit})
        remaining -= hit
    for alias in store_aliases:
        if not remaining:
            break
        hit = {m for (m,) in con.execute(f"SELECT mint FROM {alias}.mints WHERE mint IN (SELECT unnest(?::VARCHAR[]))", [sorted(remaining)]).fetchall()}
        sources.update({m: "store" for m in hit})
        remaining -= hit
    hit = remaining & _mints_of(con, chunk, INCENTIVE_EVENTS)
    sources.update({m: "incentive" for m in hit})
    sources.update({m: "none" for m in remaining - hit})
    counts = {k: sum(1 for v in sources.values() if v == k) for k in ("create_or_trade", "curve_event", "store", "incentive", "none")}
    rows = {}
    for table in ("token_transfers", "token_deltas"):
        fs = glob.glob(os.path.join(chunk, table, "*.parquet"))
        if fs:
            lit = "[" + ",".join(f"'{f}'" for f in fs) + "]"
            got = con.execute(f"SELECT mint, count(*) FROM read_parquet({lit}) WHERE mint IN (SELECT unnest(?::VARCHAR[])) GROUP BY 1",
                              [list(NON_PUMP)]).fetchall()
            rows[table] = {NON_PUMP[m]: n for m, n in got}
    return {"chunk": os.path.basename(chunk), "learned_file": True, "learned": len(learned), "sources": counts,
            "failures": sorted(m for m, v in sources.items() if v in ("incentive", "none")),
            "non_pump_learned": sorted(NON_PUMP[m] for m in learned if m in NON_PUMP), "non_pump_rows": rows}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="a chunks root (events/transfers-v1/chunks) or one chunk")
    ap.add_argument("--store", action="append", default=[], help="a store whose mints count as known (repeatable)")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    con = duckdb.connect(config={"memory_limit": os.environ.get("LAB_DUCKDB_MEM", "1GB"), "threads": int(os.environ.get("LAB_DUCKDB_THREADS", "2"))})
    aliases = []
    for i, s in enumerate(a.store):
        con.execute(f"ATTACH '{s}' AS st{i} (READ_ONLY)")
        aliases.append(f"st{i}")
    chunks = [a.root] if os.path.exists(os.path.join(a.root, "_manifest.json")) else sorted(
        os.path.dirname(m) for m in glob.glob(os.path.join(a.root, "*", "_manifest.json")))
    res = [check_chunk(con, c, aliases) for c in chunks]
    total = {"chunks": len(res), "learned": sum(r.get("learned", 0) for r in res),
             "failures": sum(len(r.get("failures", [])) for r in res),
             "without_learned_file": [r["chunk"] for r in res if not r["learned_file"]]}
    out = {"root": a.root, "stores": a.store, "total": total, "chunks": res}
    text = json.dumps(out, indent=1)
    if a.out:
        with open(a.out, "w") as f:
            f.write(text)
    print(text)
    return 1 if total["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
