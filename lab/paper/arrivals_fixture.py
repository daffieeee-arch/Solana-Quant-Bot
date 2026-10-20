"""M3-11's arrivals fixture root (research/m3-11-paper-loop-spec-2026-10-20.md v0.3 §5.4): a few segments of a recorder
root around three F7 signals of a Python live run, copied into a new root with synthetic, deterministic arrivals, so both
pipelines (LiveFeed with paper.engine, and lab-live paper --follow) fill the seven live signal columns from the same files.
Job 91's shadow segments have no arrivals table and no written_unix: there the columns are NULL on both sides.

  python -m paper.arrivals_fixture --root REC --journal PYJOURNAL --out DIR [--before 5] [--max-span 40]

The signals are the journal's F7 rows (any status); their slots, distinct, in order. The window is the first run of
segments [a, b] of REC (complete ones, by seq) whose part after its first `before` segments (the triggers' history)
holds three signal slots, as short as possible up to `max-span` segments; the first three are A, B and C. Five segments
of history (about 1,400 slots on job 91) let the feed's clock measure the epoch's ms per slot (a span of 1,000 slots)
before the first signal, so the F7 windows in slots are those of the run the signals come from; with two, a signal can
fail to come again (the clock still at REF_MS). The acceptance checks the three kinds of rows are there. Each segment
of the window becomes OUT/segments/<seq> (the seq kept) with a link to each entry of the original, except:
- `arrivals/part-00000.parquet` (new): a row per transaction of the segment's pump and pump_amm event rows (slot,
  tx_index, signature NULL, received_unix_ns, created_unix_ns, stream): received = the slot's block time + 350 ms +
  (tx_index mod 97) ms, created = received - 200 ms. Slot A's transactions arrive twice (tx_watchlist, and tx_probes
  50 ms earlier: the earliest counts); slot B's have no created_unix_ns;
- `blocks/part-00000.parquet` (rewritten): every block row but slot C's (its events have no block time, so its rows'
  seven columns are all NULL);
- `_segment.json`: as it was, with `written_unix` = the segment's last block time + 2 (whole seconds).
Nothing under REC is written. OUT/fixture.json names the window, its first slot and A, B and C. Refused: an OUT that
exists and is not empty, an OUT inside REC, fewer than three signal slots in any window.
"""

import argparse
import glob
import json
import os
import sqlite3

import pyarrow as pa
import pyarrow.parquet as pq

from paper.restart_root import _refuse_out, _segments

STREAM = "tx_watchlist"
ARRIVALS = pa.schema([("slot", pa.uint64()), ("tx_index", pa.uint64()), ("signature", pa.string()), ("received_unix_ns", pa.uint64()),
                      ("created_unix_ns", pa.uint64()), ("stream", pa.string())])


def signal_slots(journal):
    """The distinct slots of the journal's F7 rows, in order."""
    db = sqlite3.connect(f"file:{os.path.join(journal, 'journal.sqlite')}?mode=ro", uri=True)
    try:
        return sorted({t for (t,) in db.execute("SELECT t FROM signals WHERE family = 'F7'")})
    finally:
        db.close()


def window(segs, slots, before=5, max_span=40):
    """(a, b, [A, B, C]): indexes into `segs` ([(seq, name, meta)], complete ones by seq) of the first shortest window
    whose segments after its first `before` hold three signal slots, and those three (module doc)."""
    for span in range(1, max_span + 1):
        for a in range(len(segs) - before - span + 1):
            lo, hi = segs[a + before][2]["first_slot"], segs[a + before + span - 1][2]["last_slot"]
            inside = [t for t in slots if lo <= t <= hi]
            if len(inside) >= 3:
                return a, a + before + span - 1, inside[:3]
    raise SystemExit(f"arrivals_fixture: no window of at most {max_span} segments after {before} holds three F7 signal slots: refused")


def _read(dirs):
    tables = [pq.read_table(f) for d in dirs for f in sorted(glob.glob(os.path.join(d, "*.parquet")))]
    return pa.concat_tables(tables, promote_options="default") if tables else None


def block_times(seg_dir):
    """{slot: block_time} of a segment's block rows (None where it has none)."""
    t = _read([os.path.join(seg_dir, "blocks")])
    return {} if t is None else dict(zip(t.column("slot").to_pylist(), t.column("block_time").to_pylist()))


def transactions(seg_dir):
    """The (slot, tx_index) of the segment's pump and pump_amm event rows, sorted."""
    out = set()
    for d in sorted(glob.glob(os.path.join(seg_dir, "pump", "*")) + glob.glob(os.path.join(seg_dir, "pump_amm", "*"))):
        t = _read([d])
        if t is not None and "slot" in t.column_names and "tx_index" in t.column_names:
            out.update(zip(t.column("slot").to_pylist(), t.column("tx_index").to_pylist()))
    return sorted(out)


def arrival_rows(txs, bt, two_streams, no_created):
    """The arrivals table of a segment (module doc): bt the slot's block time (the original's, also for slot C)."""
    rows = []
    for slot, tx in txs:
        base = bt.get(slot)
        if base is None:
            continue  # no block time to measure from: no arrival row (the transaction gets (None, None, written))
        received = base * 1_000_000_000 + 350_000_000 + (tx % 97) * 1_000_000
        created = None if slot == no_created else received - 200_000_000
        rows.append((slot, tx, None, received, created, STREAM))
        if slot == two_streams:
            rows.append((slot, tx, None, received - 50_000_000, None if created is None else created - 50_000_000, "tx_probes"))
    cols = list(zip(*rows)) if rows else [[] for _ in ARRIVALS]
    return pa.Table.from_arrays([pa.array(list(c), type=f.type) for c, f in zip(cols, ARRIVALS)], schema=ARRIVALS)


def make(root, journal, out, before=5, max_span=40):
    """Write the fixture root (module doc) under `out`; returns fixture.json's content."""
    root, out = os.path.abspath(root), os.path.abspath(out)
    _refuse_out(root, out)
    segs = [s for s in _segments(root) if s[2].get("complete")]
    a, b, (sa, sb, sc) = window(segs, signal_slots(journal), before, max_span)
    os.makedirs(os.path.join(out, "segments"), exist_ok=True)
    for seq, name, meta in segs[a:b + 1]:
        src, dst = os.path.join(root, "segments", name), os.path.join(out, "segments", name)
        os.mkdir(dst)
        for entry in sorted(os.listdir(src)):
            if entry not in ("_segment.json", "arrivals", "blocks"):
                os.symlink(os.path.join(src, entry), os.path.join(dst, entry))
        bt = block_times(src)
        blocks = _read([os.path.join(src, "blocks")])
        if blocks is not None:
            keep = pa.array([s != sc for s in blocks.column("slot").to_pylist()])
            os.mkdir(os.path.join(dst, "blocks"))
            pq.write_table(blocks.filter(keep), os.path.join(dst, "blocks", "part-00000.parquet"))
        os.mkdir(os.path.join(dst, "arrivals"))
        pq.write_table(arrival_rows(transactions(src), bt, sa, sb), os.path.join(dst, "arrivals", "part-00000.parquet"))
        times = [t for t in bt.values() if t is not None]
        with open(os.path.join(dst, "_segment.json"), "w") as f:
            json.dump(dict(meta, written_unix=max(times) + 2) if times else meta, f)
    info = {"root": root, "journal": os.path.abspath(journal), "seqs": [segs[a][0], segs[b][0]], "segments": b - a + 1,
            "first_slot": int(segs[a][2]["first_slot"]), "full": sa, "no_created": sb, "no_block_time": sc, "two_streams": sa}
    with open(os.path.join(out, "fixture.json"), "w") as f:
        json.dump(info, f, indent=1)
    return info


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", required=True, help="the recorder's directory (its segments/ is read)")
    ap.add_argument("--journal", required=True, help="a Python live run's journal on that root: its F7 rows")
    ap.add_argument("--out", required=True)
    ap.add_argument("--before", type=int, default=5, help="segments of history before the first signal's")
    ap.add_argument("--max-span", type=int, default=40)
    a = ap.parse_args(argv)
    print(json.dumps(make(a.root, a.journal, a.out, a.before, a.max_span)))


if __name__ == "__main__":
    main()
