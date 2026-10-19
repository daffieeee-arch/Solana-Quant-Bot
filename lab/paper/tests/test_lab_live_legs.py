"""The Rust legs reader (lab-live legs: lab/live/src/legs.rs, legs_read.rs; M1 C1a of the Rust live route) against
paper.legs_dump on the same store: every event ReplayFeed hands the engine over the range, line for line, compared by
count and sha256 per slot window (research/m1-c1a-legs-spec-2026-10-19.md §7).

- The store: LAB_LEGS_STORE (default fwd1053: two chunks; from its first slot, so no farmer history before the range
  is needed, and h24 changes at the chunk boundary 455112000). Its chunks: backtest.data.root() (LAB_CHUNKS), checked
  against the store's chunk_list (the extractor's sha256 per chunk), here and by lab-live itself.
- The range: the whole store, or LAB_LEGS_FROM / LAB_LEGS_TO. A start inside the store (the dev chunk of dev2, M1
  C1a-2) takes the store's farmer history over [H0, from) from the export (snapshot_export --history-from/-to).
- Each emitted chunk's h24, computed by lab-live from the chunk's blocks, equals the store's (store.py:486-488 on its
  blocks table), and lab-live's H0 is the export's.
- On a difference, both sides dump the first differing window (--out-from) and the first differing line is shown.

Needs the built binary: LAB_LIVE_BIN=<path to lab-live> (lab/live/nas_m1a.sh builds it on the NAS); skips without it.
A whole store is a NAS job: the Python dump of fwd1053 (~27 M events) takes about half an hour.
"""

import json
import os
import subprocess
import time

import duckdb
import pytest

from backtest import data
from paper import legs_dump, snapshot_export

BIN = os.environ.get("LAB_LIVE_BIN", "")
STORE = os.environ.get("LAB_LEGS_STORE", "/home/chupa/Solana-project/data-old-faithful-one/lab/store/fwd1053.duckdb")
HOLDOUT = (452304000, 454464000)

pytestmark = pytest.mark.skipif(not (BIN and os.access(BIN, os.X_OK)), reason="set LAB_LIVE_BIN to the built lab-live (NAS)")


def _measure(line):
    if os.environ.get("LAB_LIVE_MEASURE_LOG"):
        with open(os.environ["LAB_LIVE_MEASURE_LOG"], "a") as f:
            f.write(line + "\n")


@pytest.fixture(scope="module")
def store_meta():
    if not os.path.exists(STORE):
        pytest.skip(f"no store at {STORE}")
    con = duckdb.connect(STORE, read_only=True)
    try:
        return json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
    finally:
        con.close()


@pytest.fixture(scope="module")
def export(tmp_path_factory, store_meta):
    """The snapshot with the farmer history of this replay (empty from the store's start), without the seed day."""
    out = str(tmp_path_factory.mktemp("legs-snapshot"))
    return out, snapshot_export.export(STORE, out, history=_range(store_meta), seed=False)


def _range(meta):
    lo = int(os.environ.get("LAB_LEGS_FROM", meta["slot_start"]))
    hi = int(os.environ.get("LAB_LEGS_TO", meta["slot_end_exclusive"]))
    return lo, hi


def _rust(snap, lo, hi, summary, *extra):
    """lab-live legs in the background (its log to <summary>.log, so a long run never blocks on a pipe)."""
    log = open(summary + ".log", "w")
    proc = subprocess.Popen([BIN, "legs", "--chunks-root", data.root(), "--snapshot", snap, "--from", str(lo), "--to", str(hi),
                             "--summary", summary, *extra], stdout=log, stderr=subprocess.STDOUT)
    proc.log = log
    return proc


def _wait(proc, timeout=7200):
    proc.wait(timeout=timeout)
    proc.log.close()
    with open(proc.log.name) as f:
        return proc.returncode, f.read()


def _store_h24(lo, hi):
    """store.py:486-488 on the store's blocks: a DOUBLE ms, `or 267.3`, round half to even."""
    con = duckdb.connect(STORE, read_only=True)
    try:
        ms = con.execute(f"""SELECT (max(block_time) - min(block_time)) * 1000.0 / greatest(max(slot) - min(slot), 1)
                             FROM blocks WHERE slot >= {lo} AND slot < {hi}""").fetchone()[0] or 267.3
    finally:
        con.close()
    return round(86_400_000 / ms)


def test_the_chunks_are_the_stores(store_meta):
    by_lo = {c[0]: c for c in data.complete_chunks()}
    a, b = _range(store_meta)
    for c in store_meta["chunk_list"]:
        lo, hi = c["slots"]
        if not (lo < b and hi > a):
            continue  # only the chunks the replay reads need to be here
        assert lo in by_lo and by_lo[lo][1] == hi, f"chunk {lo}-{hi} of the store is not under {data.root()}"
        with open(os.path.join(by_lo[lo][2], "_manifest.json")) as f:
            assert json.load(f).get("extractor_sha256") == c["extractor_sha256"], f"chunk {lo}-{hi}: another extraction"


def test_the_event_stream_is_replayfeeds(export, store_meta, tmp_path):
    snap, meta = export
    lo, hi = _range(store_meta)
    window = 10_000
    t0 = time.time()
    proc = _rust(snap, lo, hi, str(tmp_path / "rust.json"), "--window", str(window))
    want = legs_dump.dump(STORE, lo, hi, window)  # the Python side runs meanwhile
    py_secs = time.time() - t0
    rc, err = _wait(proc)
    assert rc == 0, err[-3000:]
    with open(tmp_path / "rust.json") as f:
        got = json.load(f)
    _measure(f"lab-live legs [{lo}, {hi}): {got['events']:,} events in {got['secs']:.0f} s, peak RSS "
             f"{(got['peak_rss_bytes'] or 0) / 1e9:.2f} GB, h24 {got['h24']}, H0 {got['h0']}, farmer history "
             f"{json.dumps(got['farm_hist'])}, {got['keys']:,} keys; paper.legs_dump "
             f"{want['events']:,} events in {py_secs:.0f} s; admission {json.dumps(got['admission'])}")
    # each emitted chunk's h24 is the store's (review on #369), and H0 the export's
    emitted = [c["slots"] for c in store_meta["chunk_list"] if c["slots"][0] < hi and c["slots"][1] > lo]
    assert got["h24"] == [[c_lo, c_hi, _store_h24(c_lo, c_hi)] for c_lo, c_hi in emitted]
    assert got["chunks_verified"] is True and got["h0"] == meta["farm_hist"]["lo"]
    if (got["events"], got["sha256"], got["windows"]) == (want["events"], want["sha256"], want["windows"]):
        assert got["kinds"] == want["kinds"]
        return
    # the first differing window, dumped by both from the range's start (the pool context and farmer history need it)
    pairs = list(zip(got["windows"], want["windows"]))
    k = next((i for i, (g, w) in enumerate(pairs) if g != w), len(pairs))
    a, b = (want["windows"] if k < len(want["windows"]) else got["windows"])[k][:2]
    proc = _rust(snap, lo, b, str(tmp_path / "rust-w.json"), "--window", str(window), "--out", str(tmp_path / "rust.txt"),
                 "--out-from", str(a))
    legs_dump.dump(STORE, lo, b, window, out=str(tmp_path / "py.txt"), out_from=a)
    rc, err = _wait(proc)
    assert rc == 0, err[-3000:]
    with open(tmp_path / "rust.txt") as f:
        r = f.read().splitlines()
    with open(tmp_path / "py.txt") as f:
        p = f.read().splitlines()
    i = next((i for i, (x, y) in enumerate(zip(r, p)) if x != y), min(len(r), len(p)))
    pytest.fail(f"events {got['events']:,} (Rust) vs {want['events']:,} (Python), kinds {got['kinds']} vs {want['kinds']}; "
                f"window [{a}, {b}): {len(r)} vs {len(p)} lines, first difference at line {i}:\n"
                f"rust:   {r[i] if i < len(r) else '(none)'}\npython: {p[i] if i < len(p) else '(none)'}")


def test_the_hold_out_another_replays_history_and_a_range_outside_the_store_are_refused(export, store_meta, tmp_path):
    snap, _ = export
    a, b = _range(store_meta)
    start = store_meta["slot_start"]
    # the export's farmer history is for a replay from `a`: a start 1,000 slots later needs other rows
    for lo, hi, why in [(HOLDOUT[0] - 1_000, HOLDOUT[0] + 1_000, "hold-out"), (a + 1_000, b, "farmer history"),
                        (start - 1, start + 1_000, "store period")]:
        rc, err = _wait(_rust(snap, lo, hi, str(tmp_path / "x.json")), timeout=600)
        assert rc != 0 and why in err, (lo, hi, err[-2000:])
