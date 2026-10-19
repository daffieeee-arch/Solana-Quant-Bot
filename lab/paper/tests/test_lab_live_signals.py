"""The Rust signals (lab-live signals: lab/live/src/signals.rs and signals_core.rs on lab/core's signal state; M1 C1b of
the Rust live route) against the paper engine's own on the same replay (research/m1-c1b-signals-spec-2026-10-19.md §1.5,
§6.4):

- The store and range: LAB_SIGNALS_STORE (default dev2) and LAB_SIGNALS_FROM / LAB_SIGNALS_TO; the default is R1, dev2's
  arm range 450128159-450380000 (three chunks, across the 1041/1042 clock change), and for another store its whole period.
  lab/live/nas_m1b.sh also runs R2, the whole of fwd1053. Both sides take the store's meta_bounds as their dev bounds:
  paper.engine has no --dev-bounds yet, so R3 (live bounds) waits until after the paper start.
- The snapshot: paper.snapshot_export with this replay's farmer history, without the seed day (as test_lab_live_legs).
- The Python reference: paper.engine main with the spec's §2.2 arguments (tp4_T600, d_ms 600, --ignore-risk, the groups
  file paper/h-arms-2026-10-16.yaml, --no-backup), in its own process with its own journal. Its state 'groups' (written
  before the run) becomes lab-live's --groups, so both journals carry the same text; lab-live runs meanwhile.
- A1: paper.journal_diff --scope signals is green (0 unexplained, the same schema and groups, rows on both sides, no Rust
  row decided beyond the farm gates), and state 'feed' is the same text.
- A2: neither journal has a row of T1 (7orRH…) or T2 (A1YQ…, 8iXGs…).
- A3 (`guard`): every row of both journals lies in the universe and the bounds, checked from the snapshot's mints.parquet,
  pools.parquet and meta.json with Python's Clock and decision_bounds, not by engine code. lab-live's summary has the same
  bounds.
- The measure (A6): rows per side and per gate, Python's status among its rows past the farm gates (decided in C2/C3), the
  Rust summary (quote NULL among the universe rejections), times and peak RSS: one line to LAB_LIVE_MEASURE_LOG.

Needs the built binary: LAB_LIVE_BIN=<path to lab-live> (lab/live/nas_m1b.sh builds it on the NAS); those tests skip
without it, and fail instead with LAB_LEGS_REQUIRE=1. The engine run is far more than 2,000 slots: a NAS job, never on the
VPS. The guard's own test runs anywhere.
"""

import json
import os
import sqlite3
import subprocess
import sys
import time

import duckdb
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest

from backtest import data
from backtest.strategies import SOL_QUOTES, Clock, decision_bounds, ms
from paper import journal_diff, snapshot_export
from paper.journal import Journal

LAB = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BIN = os.environ.get("LAB_LIVE_BIN", "")
STORE = os.environ.get("LAB_SIGNALS_STORE", "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb")
R1 = (450_128_159, 450_380_000)  # C1a's proven arm range (QUEUE 88)
T1 = "7orRHgZjwNmvpcSBHUMQ8wqW6rwKpqE1hJJR43AkQa1Z"
T2 = ("A1YQFKJoFcia2WUqkfFrEBnDbJZ2AFg7XiWq2unwNhAj", "8iXGs3rSqshaWD9iCZEXu5XLvD5CUhuu7DSA7kd3Cg92")
# The spec's §2.2 reference (D3): the primary tp4_T600 unsegmented, farm all, n2_k 1, d_ms 600, no risk limits, and group H.
ENGINE_ARGS = ["--variants", "0.04:600", "--d-ms", "600", "--ignore-risk", "--groups", "paper/h-arms-2026-10-16.yaml", "--no-backup"]

REQUIRE = os.environ.get("LAB_LEGS_REQUIRE") == "1"  # nas_m1b.sh: a missing binary or store fails, never skips
needs_bin = pytest.mark.skipif(not (BIN and os.access(BIN, os.X_OK)) and not REQUIRE,
                               reason="set LAB_LIVE_BIN to the built lab-live (NAS)")


def _measure(line):
    if os.environ.get("LAB_LIVE_MEASURE_LOG"):
        with open(os.environ["LAB_LIVE_MEASURE_LOG"], "a") as f:
            f.write(line + "\n")


@pytest.fixture(scope="module")
def store_meta():
    if os.environ.get("LAB_SIGNALS_DEV_BOUNDS"):
        pytest.fail("LAB_SIGNALS_DEV_BOUNDS: paper.engine has no --dev-bounds yet (R3 comes after the paper start)")
    if not os.path.exists(STORE):
        (pytest.fail if REQUIRE else pytest.skip)(f"no store at {STORE}")
    con = duckdb.connect(STORE, read_only=True)
    try:
        return json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
    finally:
        con.close()


def _range(meta):
    whole = (meta["slot_start"], meta["slot_end_exclusive"])
    lo, hi = R1 if os.path.basename(STORE) == "dev2.duckdb" else whole
    return int(os.environ.get("LAB_SIGNALS_FROM", lo)), int(os.environ.get("LAB_SIGNALS_TO", hi))


@pytest.fixture(scope="module")
def export(tmp_path_factory, store_meta):
    """The snapshot with this replay's farmer history (empty from the store's start), without the seed day."""
    out = str(tmp_path_factory.mktemp("signals-snapshot"))
    return out, snapshot_export.export(STORE, out, history=_range(store_meta), seed=False)


def _state(journal, key):
    """A journal's state value, read-only; None while the journal or the key is not there yet."""
    path = os.path.join(journal, "journal.sqlite")
    if not os.path.exists(path):
        return None
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        try:
            row = db.execute("SELECT v FROM state WHERE k = ?", [key]).fetchone()
        finally:
            db.close()
    except sqlite3.Error:
        return None  # the engine is still creating its tables
    return row[0] if row else None


def _start(cmd, log, **kw):
    """A process in the background, its output to `log` (a long run never blocks on a pipe)."""
    f = open(log, "w")
    proc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, **kw)
    proc.log = f
    return proc


def _python(lo, hi, journal, log):
    return _start([sys.executable, "-m", "paper.engine", "--from", str(lo), "--to", str(hi), *ENGINE_ARGS, "--store", STORE,
                   "--journal", journal], log, cwd=LAB, env={**os.environ, "PYTHONPATH": LAB})


def _rust(snap, lo, hi, groups, journal, summary, *extra):
    return _start([BIN, "signals", "--chunks-root", data.root(), "--snapshot", snap, "--from", str(lo), "--to", str(hi), "--groups",
                   groups, "--journal", journal, "--summary", summary, *extra], summary + ".log")


def _wait(proc, timeout=7200):
    """(return code, its output, its peak RSS in bytes): os.wait4 gives the child's own maximum resident set (None when
    the process was reaped elsewhere or killed at the timeout)."""
    t0, peak = time.time(), None
    while proc.returncode is None:
        pid, status, usage = os.wait4(proc.pid, os.WNOHANG)
        if pid:
            proc.returncode = os.waitstatus_to_exitcode(status)
            peak = usage.ru_maxrss * 1024
        elif time.time() - t0 > timeout:
            proc.kill()
            proc.wait()
        else:
            time.sleep(2)
    proc.log.close()
    with open(proc.log.name) as f:
        return proc.returncode, f.read(), peak


def _wait_groups(proc, journal, timeout=3600):
    """The engine's state 'groups', committed before its run starts (engine.py:1490-1500)."""
    t0 = time.time()
    while time.time() - t0 < timeout and proc.poll() is None:
        groups = _state(journal, "groups")
        if groups is not None:
            return groups
        time.sleep(2)
    groups = _state(journal, "groups")
    if groups is None:
        proc.kill()
        rc, out, _ = _wait(proc, 60)
        pytest.fail(f"paper.engine wrote no state 'groups' (exit {rc}):\n{out[-3000:]}")
    return groups


def _keys(journal, keys):
    db = sqlite3.connect(f"file:{os.path.join(journal, 'journal.sqlite')}?mode=ro", uri=True)
    try:
        return db.execute(f"SELECT DISTINCT key FROM signals WHERE key IN ({', '.join('?' * len(keys))})", list(keys)).fetchall()
    finally:
        db.close()


def guard(journal, snap):
    """A3 from the snapshot alone, not by engine code: [(family, key, t, why)] of the rows outside the universe or the
    decision bounds (empty: none). (dlo, dhi) is decision_bounds on the snapshot's meta_bounds with its Clock; a curve row's
    key is a mint that is not mayhem and is quoted in SOL_QUOTES, an F7 curve row's mint was created in [burn, dev1); a pool
    row's key is a non-mayhem pool of quote_class sol or reversed (engine.py:555-568, 612)."""
    with open(os.path.join(snap, "meta.json")) as f:
        meta = json.load(f)
    clock = Clock(meta["ms_per_slot"])
    dev0, dev1 = meta["meta_bounds"]
    dlo, dhi = decision_bounds(None, dev0, dev1, clock)
    burn = dev0 + clock.slots(ms(324_000), dev0)
    db = sqlite3.connect(f"file:{os.path.join(journal, 'journal.sqlite')}?mode=ro", uri=True)
    try:
        rows = db.execute("SELECT family, key, t, venue FROM signals").fetchall()
    finally:
        db.close()

    def table(name, key, cols):
        t = pq.read_table(os.path.join(snap, f"{name}.parquet"), columns=[key, *cols])
        want = pa.array(sorted({r[1] for r in rows}), pa.string())
        return {r[key]: r for r in t.filter(pc.is_in(t[key], value_set=want)).to_pylist()}

    mints = table("mints", "mint", ["create_slot", "mayhem", "quote_mint"])
    pools = table("pools", "pool", ["quote_class", "mayhem"])
    bad = []
    for family, key, t, venue in rows:
        if not dlo <= t <= dhi:
            bad.append((family, key, t, f"t outside [{dlo}, {dhi}]"))
        if venue == "curve":
            m = mints.get(key)
            why = ("no mint" if m is None else "mayhem" if m["mayhem"] else "quote not SOL" if m["quote_mint"] not in SOL_QUOTES
                   else "created outside [burn, dev1)" if family == "F7" and not (m["create_slot"] is not None and burn <= m["create_slot"] < dev1)
                   else None)
        else:
            p = pools.get(key)
            why = ("not a SOL market" if p is None or p["quote_class"] not in ("sol", "reversed") else "mayhem pool" if p["mayhem"] else None)
        if why:
            bad.append((family, key, t, why))
    return bad


@needs_bin
def test_the_rust_signals_are_the_engines(export, store_meta, tmp_path):
    snap, meta = export
    lo, hi = _range(store_meta)
    py_j, rs_j, groups = str(tmp_path / "python"), str(tmp_path / "rust"), str(tmp_path / "groups.json")
    t0 = time.time()
    py = _python(lo, hi, py_j, str(tmp_path / "python.log"))
    try:
        with open(groups, "w") as f:
            f.write(_wait_groups(py, py_j))
        t1 = time.time()
        rc, out, _ = _wait(_rust(snap, lo, hi, groups, rs_j, str(tmp_path / "rust.json")), timeout=3600)
        rs_secs = time.time() - t1
        assert rc == 0, out[-3000:]
        rc, py_out, py_peak = _wait(py)
        py_secs = time.time() - t0
        assert rc == 0, py_out[-3000:]
    finally:
        if py.poll() is None:
            py.kill()
    with open(tmp_path / "rust.json") as f:
        s = json.load(f)
    res = journal_diff.run(py_j, rs_j, str(tmp_path / "diff"), scope="signals")
    t = res["tables"]["signals"]
    rej = s["rejects"]
    _measure(f"lab-live signals [{lo}, {hi}) on {os.path.basename(STORE)}: {'GREEN' if res['green'] else 'RED'}, "
             f"{res['unexplained']} unexplained; rows Python {t['rows_a']:,}, Rust {t['rows_b']:,} (only Python {t['only_a']}, only "
             f"Rust {t['only_b']}, different {t['different']}, Rust decided beyond C1b {t['decided_beyond_c1b']}); gates Python "
             f"{res['gates']['a']}, Rust {res['gates']['b']}; decided in C2/C3 (Python's status past the farm gates) "
             f"{res['decided_in_c2_c3']}; Rust: {s['events']:,} events in {s['event_slots']:,} slots from {s['first_slot']}, bounds "
             f"{s['bounds']}, windows {s['windows']}, triggers {s['triggers']}, F7 {s['f7']}, rows {s['rows']}, rollovers "
             f"{s['rollovers']}, N2 overwrites {s['overwrites']}, universe rejections {rej} (quote NULL {rej['quote NULL']}; tokens "
             f"with a NULL quote_mint {s['universe']['tokens_quote_null']}), {s['keys']:,} keys, {rs_secs:.0f} s, peak RSS "
             f"{(s['peak_rss_bytes'] or 0) / 1e9:.2f} GB; paper.engine {py_secs:.0f} s, peak RSS {(py_peak or 0) / 1e9:.2f} GB")
    # A1: the signals scope is green, and both runs name the same replay.
    assert res["green"], "\n".join(journal_diff.report_signals(res))
    assert _state(rs_j, "feed") == _state(py_j, "feed") == json.dumps({"kind": "replay", "from": lo, "to": hi})
    # A2: T1 and T2 nowhere.
    for j in (py_j, rs_j):
        assert _keys(j, (T1, *T2)) == [], j
    # A3: the universe and the bounds, from the snapshot; lab-live's bounds are Python's.
    for j in (py_j, rs_j):
        bad = guard(j, snap)
        assert not bad, f"{j}: {len(bad)} rows outside the universe or the bounds, the first {bad[:10]}"
    dev0, dev1 = meta["meta_bounds"]
    dlo, dhi = decision_bounds(None, dev0, dev1, Clock(meta["ms_per_slot"]))
    assert s["bounds"] == [dev0, dev1, dlo, dlo, dhi] and s["first_slot"] is not None and s["first_slot"] >= lo


@needs_bin
def test_a_snapshot_without_its_clock_or_bounds_and_groups_it_cannot_run_are_refused(export, store_meta, tmp_path):
    snap, meta = export
    lo, hi = _range(store_meta)
    groups = tmp_path / "groups.json"
    k3 = {"d": 1, "d_ms": 600, "farm": "all", "n2_k": 1, "primary": True, "q": 0.5, "require_farm_complete": False, "risk": None,
          "segments": None, "variants": ["tp4_T600"], "wallet": "wallet.json"}
    for drop, why in (("meta_bounds", "no meta_bounds"), ("ms_per_slot", "no ms_per_slot")):
        d = tmp_path / drop
        d.mkdir()
        for name in os.listdir(snap):
            if name != "meta.json":
                os.symlink(os.path.join(snap, name), d / name)
        with open(d / "meta.json", "w") as f:
            json.dump({k: v for k, v in meta.items() if k != drop}, f)
        groups.write_text(json.dumps({"K3": k3}, sort_keys=True))
        rc, out, _ = _wait(_rust(str(d), lo, hi, str(groups), str(tmp_path / f"j-{drop}"), str(tmp_path / f"{drop}.json")), timeout=600)
        assert rc != 0 and why in out, out[-2000:]
    groups.write_text(json.dumps({"K3": k3, "H": {**k3, "variants": ["flow300_H6"]}}, sort_keys=True))  # two primaries
    rc, out, _ = _wait(_rust(snap, lo, hi, str(groups), str(tmp_path / "j-groups"), str(tmp_path / "groups-run.json")), timeout=600)
    assert rc != 0 and "exactly one primary" in out, out[-2000:]


def test_the_guard_finds_rows_outside_the_universe_and_the_bounds(tmp_path):
    """The A3 check itself, on a small snapshot: dev2's bounds and two epochs of its clock, (dlo, dhi) = (446529755,
    452284004)."""
    snap = tmp_path / "snap"
    snap.mkdir()
    pump = "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn"
    mints = [("M_ok", 450_000_000, False, SOL_QUOTES[0]), ("M_wsol", 450_000_000, None, SOL_QUOTES[1]), ("M_old", 1, False, SOL_QUOTES[0]),
             ("M_mayhem", 450_000_000, True, SOL_QUOTES[0]), ("M_pump", 450_000_000, False, pump), ("M_null", 450_000_000, False, None)]
    pq.write_table(pa.table({"mint": [m[0] for m in mints], "create_slot": pa.array([m[1] for m in mints], pa.uint64()),
                             "creator": ["C"] * len(mints), "mayhem": [m[2] for m in mints], "quote_mint": [m[3] for m in mints],
                             "complete_slot": pa.array([None] * len(mints), pa.uint64()), "pool": pa.array([None] * len(mints), pa.string())}),
                   snap / "mints.parquet")
    pools = [("P_sol", "sol", False), ("P_rev", "reversed", None), ("P_other", "other", False), ("P_mayhem", "sol", True)]
    pq.write_table(pa.table({"pool": [p[0] for p in pools], "mint": [p[0] for p in pools], "orientation": ["normal"] * len(pools),
                             "quote_class": [p[1] for p in pools], "mayhem": [p[2] for p in pools], "coin_creator": ["C"] * len(pools),
                             "create_slot": pa.array([1] * len(pools), pa.uint64())}), snap / "pools.parquet")
    with open(snap / "meta.json", "w") as f:
        json.dump({"meta_bounds": [446_256_000, 452_304_000], "ms_per_slot": {"1033": 316.36, "1046": 267.35}}, f)
    j = Journal(str(tmp_path / "j"))
    t = 450_200_000
    rows = [("F7", "M_ok", "curve", t), ("F7", "M_wsol", "curve", t), ("N2_F7", "M_old", "curve", t), ("F7", "P_sol", "pool", t),
            ("F7", "P_rev", "pool", t), ("F7", "M_old", "curve", t), ("F7", "M_mayhem", "curve", t), ("N2_F7", "M_pump", "curve", t),
            ("F7", "M_null", "curve", t), ("F7", "M_absent", "curve", t), ("F7", "P_other", "pool", t), ("N2_F7", "P_mayhem", "pool", t),
            ("F7", "P_absent", "pool", t), ("F7", "P_sol", "pool", 446_529_754), ("F7", "P_sol", "pool", 446_529_755),
            ("F7", "P_sol", "pool", 452_284_004), ("F7", "P_sol", "pool", 452_284_005)]
    for family, key, venue, slot in rows:
        j.put("signals", family=family, variant="tp4_T600", key=key, t=slot, venue=venue)
    j.close()
    bad = sorted(guard(str(tmp_path / "j"), str(snap)))
    assert bad == sorted([("F7", "M_old", t, "created outside [burn, dev1)"), ("F7", "M_mayhem", t, "mayhem"),
                          ("N2_F7", "M_pump", t, "quote not SOL"), ("F7", "M_null", t, "quote not SOL"), ("F7", "M_absent", t, "no mint"),
                          ("F7", "P_other", t, "not a SOL market"), ("N2_F7", "P_mayhem", t, "mayhem pool"),
                          ("F7", "P_absent", t, "not a SOL market"),
                          ("F7", "P_sol", 446_529_754, "t outside [446529755, 452284004]"),
                          ("F7", "P_sol", 452_284_005, "t outside [446529755, 452284004]")])
