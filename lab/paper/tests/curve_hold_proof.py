"""The curve_hold proof of M1 C2-5 (research/m1-c2-positionsim-spec-2026-10-19.md D4, §3.5, §4.1): curve_hold.parquet
(paper/snapshot_export.py) holds exactly the part of a curve position's seed that the unmodified engine takes from
before the replay's `from`.

  python -m paper.tests.curve_hold_proof STORE FROM TO [--out DIR]      # every mint: the proof, its EQUAL line
  LAB_HOLD_MINTS=300 python -m paper.tests.curve_hold_proof STORE FROM TO   # a sample: a SAMPLE line, never EQUAL

What the engine keeps. A curve entry at slot S reads `feed.curve_history(mint, S)` (engine.py:891-899): every curve leg
of the mint with a slot up to S, from the store's first row on (feed.py:108-111). open_curve_position seeds
`Holdings(exact=True)` with `legs[:i]`, i being our insertion index at S (replay.py:663-664). Holdings.seed
(replay.py:225-230) keeps two dicts, hist and cf, keyed by trader (None, the NULL trader, is a key like any other). For
every leg whose is_buy is not None, both get += t for a buy and -t for a sell, in exact Python ints. So:
- the seed is a sum per trader and the leg order does not matter. Every leg before `from` is in legs[:i], since
  S > from (d >= 1). Their part is Holdings.seed(curve_history(mint, from - 1)), whatever S and i are. This proof covers
  only that part;
- the rest is the mint's legs in [from, S] before i. For S < to these are all the replay's own legs (C2-4b). Not so for
  an entry at S >= to: at the end of the data the engine still enters every pending entry (_end_of_data,
  engine.py:1289-1290, _quiet_entries engine.py:353-360), and curve_history has no bound at the feed's hi
  (feed.py:108-111). On a store that goes on past `to` (dev2 does, R1's to is 450380000) such an entry's seed and fill
  read store legs in [to, S], which neither this file nor the replay holds. Open for C2-4b/C2-6;
- hist and cf are equal after the seed;
- a leg without is_buy is skipped, not booked as a sell (as _apply_hold would book it, replay.py:576);
- a trader whose legs net to 0 keeps an entry of 0. Nothing iterates over the dicts: sell_amount and apply read them
  with .get(trader, 0) and .get(trader, h) (replay.py:232-247). So an entry of 0 acts exactly like none, which is why
  the export drops them. test_curve_hold_seed.py's test_a_zero_entry_acts_like_none shows this with the engine's own
  Holdings.

The proof (`prove`): the mints with a curve row in [from, to) come from the store, by this script's own query. Mint by
mint, the unmodified ReplayFeed.curve_history(mint, from - 1), seeded into the unmodified Holdings(exact=True), gives
hist == cf, and its non-zero entries equal the file's rows of that mint. The file has no other mint, every row is
non-zero, and each (mint, trader) is there once, in the export's order. Memory stays bounded: one mint's legs at a time.
The file itself is read whole, but it is small (142,498 rows on R1).

`run` exports the store's curve_hold for [from, to) first (snapshot_export.export, into --out or a temporary directory)
and then proves it:
- LAB_HOLD_MINTS (or --mints): how many mints are compared, evenly spread over the sorted mints. 0, the script's
  default, compares every mint. Only then is the line EQUAL (spec §4.1):
    curve_hold proof [LO, HI) on <store>.duckdb: EQUAL, <rows> rows, <mints> mints; every one of ... compared, ...
  with <rows> and <mints> the file's, written with thousands separators. A sample writes `: SAMPLE, ...` instead.
  The line goes to stdout and, with LAB_LIVE_MEASURE_LOG, into that file. lab/paper/nas_c2_hold.sh greps it, and so
  does C2-6's nas_m1c2.sh (spec §6.4);
- the pinned counts, every mint compared:
  - R1, dev2 [450128159, 450380000): 33,285 mints, 4,981,887 earlier legs, none without is_buy, 1,362,368
    (mint, trader) pairs, of which 142,498 are non-zero, over 8,039 mints (spec Appendix Q2);
  - R2, a replay from the store's start (the whole of fwd1053): the store has no curve row before it, the file is empty.
test_curve_hold_seed.py runs it on a tiny store and on a VPS-sized sample of dev2.
"""

import argparse
import json
import os
import resource
import sys
import tempfile
import time

import pyarrow as pa
import pyarrow.parquet as pq

from backtest import streams
from backtest.replay import Holdings
from paper import snapshot_export
from paper.feed import ReplayFeed

SCHEMA = pa.schema([("mint", pa.string()), ("trader", pa.string()), ("net_t", pa.int64())])  # net_t BIGINT, not DOUBLE
R1 = (450_128_159, 450_380_000)  # C1b's R1 (spec §1.4)
R1_STORE_START = 446_256_000  # dev2's
R1_COUNTS = {"mints": 33_285, "legs": 4_981_887, "pairs": 1_362_368, "rows": 142_498, "with_rows": 8_039, "no_is_buy": 0}


def read_file(path):
    """curve_hold.parquet as {mint: {trader: net}} and its row count, checked against the export's contract on the
    way: BIGINT nets, never 0, sorted by (mint, trader) with the NULL trader last, each pair once."""
    t = pq.read_table(path)
    assert t.schema.equals(SCHEMA), t.schema
    out, prev = {}, None
    for m, w, n in zip(*(t.column(c).to_pylist() for c in ("mint", "trader", "net_t"))):
        key = (m, w is None, w or "")
        assert m is not None and n != 0 and (prev is None or key > prev), (m, w, n)
        prev = key
        out.setdefault(m, {})[w] = n
    return out, t.num_rows


def prove(store, a, b, path, limit=0):
    """The proof of the module doc for the replay [a, b) of `store` and its export's curve_hold.parquet at `path`; limit:
    the number of mints compared (0: all). Returns the counts."""
    file, rows = read_file(path)
    feed = ReplayFeed(store, a, b)  # the engine's own feed (it refuses the hold-out)
    try:
        mints = [r[0] for r in feed.con.execute(f"SELECT DISTINCT mint FROM curve WHERE slot >= {a} AND slot < {b} ORDER BY mint").fetchall()]
        assert set(file) <= set(mints), sorted(set(file) - set(mints))[:5]
        step = 1 if not limit or limit >= len(mints) else -(-len(mints) // limit)
        n = {"mints": len(mints), "compared": 0, "legs": 0, "no_is_buy": 0, "pairs": 0, "zero": 0, "rows": 0, "with_rows": 0}
        for m in mints[::step]:
            legs = feed.curve_history(m, a - 1)
            assert all(leg.slot < a for leg in legs)
            hold = Holdings(exact=True)
            hold.seed(legs)
            assert hold.hist == hold.cf, m
            got = {w: v for w, v in hold.hist.items() if v != 0}
            assert got == file.get(m, {}), (m, sorted(set(got.items()) ^ set(file.get(m, {}).items()), key=str)[:5])
            n["compared"] += 1
            n["legs"] += len(legs)
            n["no_is_buy"] += sum(1 for leg in legs if leg.is_buy is None)
            n["pairs"] += len(hold.hist)
            n["zero"] += len(hold.hist) - len(got)
            n["rows"] += len(got)
            n["with_rows"] += bool(got)
    finally:
        feed.con.close()
    n["file_rows"], n["file_mints"] = rows, len(file)
    if n["compared"] == len(mints):  # every mint: the file is exactly the engine's non-zero seed entries
        assert (n["rows"], n["with_rows"]) == (rows, len(file))
    return n


def run(store, a, b, out, limit=0):
    """Exports `store`'s curve_hold for the replay [a, b) into `out` and proves it (limit as in prove). Returns the
    counts and the measure line."""
    con = streams.open_store(store, threads=1, memory="512MB")
    try:
        store_meta = json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
        earlier = con.execute(f"SELECT count(*) FROM curve WHERE slot < {a}").fetchone()[0] if a == store_meta["slot_start"] else None
    finally:
        con.close()
    t0 = time.time()
    meta = snapshot_export.export(store, str(out), history=(a, b), seed=False)
    t1 = time.time()
    n = prove(store, a, b, os.path.join(out, "curve_hold.parquet"), limit)
    every = n["compared"] == n["mints"]
    hold = meta["curve_hold"]
    assert hold == {"lo": store_meta["slot_start"], "hi": a, "to": b, "rows": n["file_rows"],
                    "skipped_null_is_buy": hold["skipped_null_is_buy"]}, hold
    if every:
        assert hold["skipped_null_is_buy"] == n["no_is_buy"]
    pins = ""
    if every and store_meta["slot_start"] == R1_STORE_START and (a, b) == R1:  # dev2's R1
        assert {k: n[k] for k in R1_COUNTS} == R1_COUNTS, n
        pins = ", R1 counts as pinned"
    if earlier is not None:  # R2: a replay from the store's start has nothing before it
        assert earlier == n["file_rows"] == n["legs"] == 0, (earlier, n)
        pins = ", no curve row before the store's start"
    base = os.path.basename(store)
    head = (f"EQUAL, {n['file_rows']:,} rows, {n['file_mints']:,} mints; every one of {n['mints']:,} mints compared" if every
            else f"SAMPLE, not the proof: {n['compared']:,} of {n['mints']:,} mints compared (LAB_HOLD_MINTS={limit}) and equal; "
                 f"the file {n['file_rows']:,} rows, {n['file_mints']:,} mints")
    line = (f"curve_hold proof [{a}, {b}) on {base}: {head}{pins}: {n['legs']:,} earlier legs ({n['no_is_buy']} without "
            f"is_buy, skipped), {n['pairs']:,} (mint, trader) pairs, {n['rows']:,} non-zero over {n['with_rows']:,} mints, "
            f"{n['zero']:,} zero nets dropped; export {t1 - t0:.0f} s, proof {time.time() - t1:.0f} s, "
            f"peak RSS {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024} MB")
    return n, line


def measure(line):
    print(line, flush=True)
    if os.environ.get("LAB_LIVE_MEASURE_LOG"):
        with open(os.environ["LAB_LIVE_MEASURE_LOG"], "a") as f:
            f.write(line + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="The curve_hold proof of M1 C2-5 (spec §4.1): the store's curve_hold for "
                                 "the replay [FROM, TO) is the unmodified engine's seed before FROM.")
    ap.add_argument("store")
    ap.add_argument("lo", type=int, metavar="FROM")
    ap.add_argument("hi", type=int, metavar="TO")
    ap.add_argument("--out", help="the export's directory (kept); default a temporary one, removed afterwards")
    ap.add_argument("--mints", type=int, default=int(os.environ.get("LAB_HOLD_MINTS", "0")),
                    help="mints compared (default LAB_HOLD_MINTS, else 0: every mint, the proof)")
    args = ap.parse_args(argv)
    if args.out:
        n, line = run(args.store, args.lo, args.hi, args.out, args.mints)
    else:
        with tempfile.TemporaryDirectory(prefix="curve_hold-") as out:
            n, line = run(args.store, args.lo, args.hi, out, args.mints)
    measure(line)
    return n


if __name__ == "__main__":
    main(sys.argv[1:])
