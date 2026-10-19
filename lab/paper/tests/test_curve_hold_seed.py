"""The tests of the curve_hold proof of M1 C2-5 (paper/tests/curve_hold_proof.py; research/m1-c2-positionsim-spec-2026-10-19.md
D4, §4.1): curve_hold.parquet is the part of a curve position's seed that the unmodified engine takes from before the
replay's `from`. The proof itself, every mint of R1 and R2, is a NAS job (lab/paper/nas_c2_hold.sh). Here:
- an entry of 0 in the engine's Holdings acts like none, so the export may drop it (the engine's own Holdings);
- the proof on test_snapshot_export's tiny store: a leg without is_buy, a NULL trader, a zero net, a leg at from - 1, a
  value beyond 2^53 and BIGINT's lowest value; a file that differs in one net, misses a pair or misses the leg at
  from - 1 is caught; the script's EQUAL line, for a replay [from, to) and for one from the store's start (R2's case);
- the proof on a VPS-sized sample of a real store: LAB_HOLD_STORE (default dev2) and LAB_HOLD_FROM / LAB_HOLD_TO. On
  dev2 the default is the first 2,000 slots of R1, [450128159, 450130159): 1,044 mints, 567 of them with 375,312
  earlier legs. On another store the default is its whole period. LAB_HOLD_MINTS (default 300; 0: every mint) is the
  number of mints compared. Its measure line (a SAMPLE line, EQUAL only with every mint) goes to LAB_LIVE_MEASURE_LOG.
"""

import json
import os
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backtest import streams
from backtest.replay import Holdings
from paper.tests import curve_hold_proof as P
from paper.tests.test_snapshot_export import TINY, TINY_HOLD, tiny_store

DEV2 = "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb"
STORE = os.environ.get("LAB_HOLD_STORE", DEV2)
LIMIT = int(os.environ.get("LAB_HOLD_MINTS", "300"))


def test_a_zero_entry_acts_like_none():
    # X buys and sells 5 before `from`: the engine's seed keeps X at 0, curve_hold has no X. The same later events on
    # both (a reverted buy of X: hist +7, cf 0; a counterfactual sell; Y's sells) must give the same sell amounts.
    legs = [SimpleNamespace(trader="X", is_buy=True, t=5), SimpleNamespace(trader="X", is_buy=False, t=5),
            SimpleNamespace(trader="Y", is_buy=True, t=3), SimpleNamespace(trader="Z", is_buy=None, t=9)]
    seeded = Holdings(exact=True)
    seeded.seed(legs)
    assert seeded.hist == seeded.cf == {"X": 0, "Y": 3}  # Z skipped: no is_buy
    from_file = Holdings(exact=True)
    from_file.hist, from_file.cf = {"Y": 3}, {"Y": 3}
    for events in ([("sell", "X", 4)], [("apply", "X", 7, 0), ("sell", "X", 7), ("sell", "X", 3)],
                   [("apply", "X", 7, 7), ("apply", "X", 0, -2), ("sell", "X", 7)], [("apply", "Y", 0, -1), ("sell", "Y", 3), ("sell", "Z", 9)]):
        a, b = Holdings(exact=True), Holdings(exact=True)
        a.hist, a.cf, b.hist, b.cf = dict(seeded.hist), dict(seeded.cf), dict(from_file.hist), dict(from_file.cf)
        for e in events:
            if e[0] == "apply":
                a.apply(*e[1:])
                b.apply(*e[1:])
            else:
                assert a.sell_amount(*e[1:]) == b.sell_amount(*e[1:]), (events, e)
        assert {k: v for k, v in a.hist.items() if v} == {k: v for k, v in b.hist.items() if v}
        assert {k: v for k, v in a.cf.items() if v} == {k: v for k, v in b.cf.items() if v}


def test_the_engines_seed_on_a_tiny_store_is_its_curve_hold(tmp_path, monkeypatch):
    store = tiny_store(tmp_path / "tiny.duckdb", TINY)
    log = tmp_path / "measure.log"
    monkeypatch.setenv("LAB_LIVE_MEASURE_LOG", str(log))
    monkeypatch.delenv("LAB_HOLD_MINTS", raising=False)
    out = tmp_path / "snap"
    n = P.main([store, "2000", "2500", "--out", str(out)])
    # A, B, E, G trade in [2000, 2500); A's W2 nets to 0 (an entry of 0 in the seed, no row); A's W3 has no is_buy;
    # A's W1 has a leg at 1999 = from - 1
    assert n == {"mints": 4, "compared": 4, "legs": 11, "no_is_buy": 1, "pairs": 6, "zero": 1, "rows": 5, "with_rows": 3,
                 "file_rows": 5, "file_mints": 3}
    meta = json.load(open(out / "meta.json"))
    assert meta["curve_hold"]["skipped_null_is_buy"] == n["no_is_buy"] and P.read_file(out / "curve_hold.parquet")[1] == len(TINY_HOLD)
    # a replay from the store's start: nothing before it (R2's case); a temporary export, removed afterwards
    n = P.main([store, "1000", "2500"])
    assert (n["legs"], n["file_rows"]) == (0, 0)
    assert log.read_text().splitlines()[0].startswith("curve_hold proof [2000, 2500) on tiny.duckdb: EQUAL, 5 rows, 3 mints; "
                                                      "every one of 4 mints compared: 11 earlier legs (1 without is_buy, skipped)")
    assert log.read_text().splitlines()[1].startswith("curve_hold proof [1000, 2500) on tiny.duckdb: EQUAL, 0 rows, 0 mints; every "
                                                      "one of 6 mints compared, no curve row before the store's start: 0 earlier legs")
    # a file that differs from the seed in one net (2^53, as a DOUBLE would hold 2^53 + 1), misses a pair, or misses the
    # leg at from - 1 (A's W1 6: an export of slot < from - 1) is caught
    t = pq.read_table(out / "curve_hold.parquet")
    for bad in (t.set_column(2, "net_t", pa.array([10, 2**53, 7, -3, -(2**63)], pa.int64())), t.slice(1),
                t.set_column(2, "net_t", pa.array([6, 2**53 + 1, 7, -3, -(2**63)], pa.int64()))):
        pq.write_table(bad, tmp_path / "bad.parquet")
        with pytest.raises(AssertionError):
            P.prove(store, 2000, 2500, tmp_path / "bad.parquet")
    # a sample is never EQUAL
    _, line = P.run(store, 2000, 2500, str(tmp_path / "sample"), limit=2)
    assert line.startswith("curve_hold proof [2000, 2500) on tiny.duckdb: SAMPLE, not the proof: 2 of 4 mints compared")


def _range(store_meta):
    if "LAB_HOLD_FROM" in os.environ:
        return int(os.environ["LAB_HOLD_FROM"]), int(os.environ["LAB_HOLD_TO"])
    if STORE == DEV2:
        return P.R1[0], P.R1[0] + 2_000
    return store_meta["slot_start"], store_meta["slot_end_exclusive"]


def test_the_engine_seeds_from_before_from_what_curve_hold_holds(tmp_path):
    if not os.path.exists(STORE):
        pytest.skip("store not built")
    con = streams.open_store(STORE, threads=1, memory="512MB")
    try:
        store_meta = json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
    finally:
        con.close()
    a, b = _range(store_meta)
    n, line = P.run(STORE, a, b, str(tmp_path / "hold"), LIMIT)
    assert n["compared"] > 0 and (n["compared"] == n["mints"]) == line.startswith(
        f"curve_hold proof [{a}, {b}) on {os.path.basename(STORE)}: EQUAL, {n['file_rows']:,} rows, {n['file_mints']:,} mints; ")
    if STORE == DEV2 and (a, b) == (P.R1[0], P.R1[0] + 2_000) and n["compared"] == n["mints"]:
        assert (n["mints"], n["legs"]) == (1_044, 375_312)  # measured 2026-10-19
    P.measure(line)
