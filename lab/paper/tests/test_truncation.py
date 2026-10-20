"""The truncation-invariance harness (paper/truncation.py; spec v0.2, LAB review 2026-10-20): the production window is
pinned, the comparison's rules (GREEN, and RED on an empty or meaningless input or any difference), the truncated copy
of a store, and the VPS smoke on a 40k-slot window of fwd1053-1054: the engine GREEN, a planted look-ahead RED."""

import inspect
import json
import os
import sqlite3

import duckdb
import pytest

from backtest import strategies as S
from paper import engine
from paper import truncation as T

FWD = "/home/chupa/Solana-project/data-old-faithful-one/lab/store/fwd1053-1054.duckdb"


def test_the_production_window_is_pinned():
    """LAB review: decision_bounds, the Engine and paper.engine's CLI keep production's burn-in and end guard by default;
    the harness's values come only through their parameters, and a paper run (--config or --live) refuses others."""
    assert (S.BURN_IN_MS, S.END_GUARD_MS) == (86_605_200.0, 5_346_000.0)  # ms(324_000) and ms(20_000) at REF_MS 267.3
    db = inspect.signature(S.decision_bounds).parameters
    assert (db["burn_in_ms"].default, db["end_guard_ms"].default) == (S.BURN_IN_MS, S.END_GUARD_MS)
    en = inspect.signature(engine.Engine.__init__).parameters
    assert (en["burn_in_ms"].default, en["end_guard_ms"].default) == (S.BURN_IN_MS, S.END_GUARD_MS)
    a = engine.parser().parse_args(["--journal", "j"])
    assert (a.burn_in_ms, a.end_guard_ms) == (S.BURN_IN_MS, S.END_GUARD_MS)
    clock = S.Clock({"1052": 270.0})
    lo, hi = 454_464_000, 454_896_000
    assert S.decision_bounds(None, lo, hi, clock) == (lo + clock.slots(S.ms(324_000), lo), hi - clock.slots(S.ms(20_000), hi - 1))
    assert S.decision_bounds(None, lo, hi, clock, burn_in_ms=0, end_guard_ms=0) == (lo, hi)


@pytest.mark.parametrize("flag", ["--live", "--config"])
def test_a_paper_run_refuses_another_window(tmp_path, flag):
    with pytest.raises(SystemExit) as e:
        engine.main(["--journal", str(tmp_path / "j"), flag, str(tmp_path / "x"), "--end-guard-ms", "0"])
    assert e.value.code == 2


def journal(path, signals=(), orders=(), fills=(), exits=()):
    """A journal.sqlite with the compared tables only (compare reads every column but the run-local ones)."""
    os.makedirs(path, exist_ok=True)
    db = sqlite3.connect(os.path.join(path, "journal.sqlite"))
    db.execute("CREATE TABLE signals (key TEXT, t INTEGER, status TEXT, latency_ms REAL)")
    db.execute("CREATE TABLE orders (key TEXT, entry_slot INTEGER)")
    db.execute("CREATE TABLE fills (key TEXT, slot INTEGER, side TEXT)")
    db.execute("CREATE TABLE exits (key TEXT, exit_decision_slot INTEGER, exit_reason TEXT)")
    for table, rows in (("signals", signals), ("orders", orders), ("fills", fills), ("exits", exits)):
        for r in rows:
            db.execute(f"INSERT INTO {table} VALUES ({', '.join('?' * len(r))})", r)
    db.commit()
    db.close()
    return str(path)


HIDDEN = {"completions": 1, "mints_created": 0, "migrations": 0, "pools_created": 0}


def test_the_comparison_is_green_only_on_the_same_decisions_near_a_real_cut(tmp_path):
    cut = 10_000
    sig = [("A", 9_990, "entered", 1.0), ("B", 5_000, "entered", 2.0)]
    base = dict(signals=sig, orders=[("A", 9_992)], fills=[("A", 9_992, "buy")], exits=[("B", 6_000, "tp")])
    a = journal(tmp_path / "a", **base)
    # the same decisions; latency (run-local) differs; the full run has more after the cut, an end-of-data exit and an
    # exit decided at the cut itself (it needed slot cut + 1)
    b = journal(tmp_path / "b", signals=[(k, t, s, 9.0) for k, t, s, _ in sig] + [("C", 10_001, "entered", 0.0)],
                orders=[("A", 9_992), ("C", 10_002)], fills=[("A", 9_992, "buy")],
                exits=[("B", 6_000, "tp"), ("A", 9_999, "end_of_data"), ("A", 10_000, "tp")])
    r = T.compare(a, b, cut, hidden=HIDDEN)
    assert r["status"] == "GREEN" and r["compared"] == {"signals": [2, 2], "orders": [1, 1], "fills": [1, 1], "exits": [1, 1]}
    assert r["near_signals"] == 1
    # a decision at or before the cut that the full run made otherwise: RED, with the rows
    c = journal(tmp_path / "c", **{**base, "signals": [("A", 9_990, "skipped", 1.0), sig[1]]})
    r = T.compare(a, c, cut, hidden=HIDDEN)
    assert r["status"] == "RED" and r["diffs"][0]["table"] == "signals" and r["diffs"][0]["only_full"] == [["A", 9_990, "skipped"]]
    # empty, nothing near the cut, nothing hidden: RED each
    e = journal(tmp_path / "e")
    assert "the comparison is empty" in T.compare(e, e, cut, hidden=HIDDEN)["why"][0]
    assert any("could not have mattered" in w for w in T.compare(a, a, cut, near=5, hidden=HIDDEN)["why"])
    assert any("the two inputs are the same" in w for w in T.compare(a, a, cut, hidden={k: 0 for k in HIDDEN})["why"])


def fake_store(path):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE blocks AS SELECT range AS slot, 1_700_000_000 + range // 3 AS block_time FROM range(100, 200)")
    for t in ("curve", "pool", "pool_sweeps"):
        con.execute(f"CREATE TABLE {t} AS SELECT range AS slot, 0 AS tx_index, 0 AS outer_ix, 0 AS inner_ix, 'K' || range AS k "
                    "FROM range(100, 200, 10)")
    con.execute("""CREATE TABLE mints AS SELECT * FROM (VALUES ('M1', 110, 150, 160, 'P1'), ('M2', 120, 180, 190, 'P2'),
                   ('M3', 170, NULL, NULL, NULL)) t(mint, create_slot, complete_slot, migrate_slot, pool)""")
    con.execute("CREATE TABLE pools AS SELECT * FROM (VALUES ('P1', 160), ('P2', 190), ('PX', NULL)) t(pool, create_slot)")
    con.execute("CREATE TABLE pool_stats (pool VARCHAR)")
    con.execute("CREATE TABLE meta (json VARCHAR)")
    con.execute("INSERT INTO meta VALUES (?)", [json.dumps({"slot_start": 100, "slot_end_exclusive": 200})])
    con.close()
    return str(path)


def test_the_truncated_copy_hides_the_facts_after_the_cut(tmp_path):
    full = fake_store(tmp_path / "full.duckdb")
    counts = T.truncate_store(full, str(tmp_path / "cut.duckdb"), 100, 165)
    con = duckdb.connect(str(tmp_path / "cut.duckdb"), read_only=True)
    assert con.execute("SELECT max(slot) FROM blocks").fetchone()[0] == 165 and counts["pool"] == 7
    assert con.execute("SELECT mint, complete_slot, migrate_slot, pool FROM mints ORDER BY mint").fetchall() == \
        [("M1", 150, 160, "P1"), ("M2", None, None, None)]  # M3 created after the cut; M2's graduation after it
    assert con.execute("SELECT pool FROM pools ORDER BY pool").fetchall() == [("P1",), ("PX",)]
    tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    assert "pool_stats" not in tables  # an end-of-store aggregate: the future
    assert json.loads(con.execute("SELECT json FROM meta").fetchone()[0])["slot_end_exclusive"] == 166
    con.close()
    assert T.hidden_between(full, 165, 199) == {"completions": 1, "migrations": 1, "mints_created": 1, "pools_created": 1}
    with pytest.raises(T.Red, match="exists"):
        T.truncate_store(full, str(tmp_path / "cut.duckdb"), 100, 165)
    with pytest.raises(T.Red, match="empty"):
        T.truncate_store(full, str(tmp_path / "none.duckdb"), 300, 400)


class Survivorship(engine.Engine):
    """Spec §5's planted look-ahead: the F7 trigger refuses a pool whose last trade in the store is at or before t + 500
    (one lookup of the whole store: the future)."""
    last = None

    def _f7(self, venue, key, legs, t):
        if venue != "curve":
            if self.last is None:
                self.last = dict(self.feed.con.execute("SELECT pool, max(slot) FROM pool GROUP BY pool").fetchall())
            if self.last.get(key, 0) <= t + 500:
                return None
        return super()._f7(venue, key, legs, t)


@pytest.mark.skipif(not os.path.exists(FWD), reason="the route B store (fwd1053-1054) is not here")
def test_the_vps_smoke_is_green_and_a_planted_look_ahead_red(tmp_path):
    """The spec's VPS smoke (§6): fwd1053-1054 [455,600,000, 455,640,000] cut at its end, the full input 3,000 slots on,
    burn-in 3,000 reference slots in both runs (end guard 0): GREEN. The same input with the survivorship look-ahead
    planted: RED, with more decisions in the run that has the future."""
    lo, cut, end, burn = 455_600_000, 455_640_000, 455_643_000, S.ms(3_000)
    ok = T.harness(FWD, str(tmp_path / "ok"), lo, [cut], end, burn_in_ms=burn)
    r = ok["cuts"][0]
    assert ok["status"] == "GREEN", r
    assert r["compared"]["signals"][0] == r["compared"]["signals"][1] > 0 and r["near_signals"] > 0 and sum(r["hidden"].values()) > 0
    bad = T.harness(FWD, str(tmp_path / "bad"), lo, [cut], end, burn_in_ms=burn, engine_cls=Survivorship)
    b = bad["cuts"][0]
    assert bad["status"] == "RED" and any(d["table"] == "signals" for d in b["diffs"]), b
    n_cut, n_full = b["compared"]["signals"]
    assert n_cut < n_full  # the look-ahead refuses pools that stop trading; with more future it refuses fewer
