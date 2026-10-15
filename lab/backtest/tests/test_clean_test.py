"""The clean-data test's guards and statistics (backtest.clean_test), the store's clean period and the
tournament's delay in milliseconds. The test itself runs on the NAS (backtest/nas_clean_test.sh)."""

import hashlib
import json
import os

import duckdb
import numpy as np
import pytest

from backtest import clean_test as ct
from backtest import data, store
from backtest.strategies import EPOCH_SLOTS, Clock
from backtest.tournament import delay_slots

EXCLUSIONS = """# Uitsluitingen

| datum | mint | naam | waarom | toegevoegd door |
|---|---|---|---|---|
| 2026-10-15 | `33jENUQnjoitkMFpvNJcSHyLAHFWJuHwT6E3E8CNpump` | Doge Pepe (DOPE) | casus; ook alle pools van deze mint | coördinator |
| 2026-10-15 | elke mint met symbool `SEALS` of naam "Save The Seals" die in 1055–1063 is aangemaakt | Save The Seals (SEALS) | casus | coördinator |
| 2026-10-15 | `HvYfFr8nm6bn9V93TDDMgqWwS2D2LsMn1Y3rDCToqeAB` (mint of pool-adres) | Save The Seals (SEALS) | aanvulling op regel 2 | coördinator |
"""
DOPE, HVY = "33jENUQnjoitkMFpvNJcSHyLAHFWJuHwT6E3E8CNpump", "HvYfFr8nm6bn9V93TDDMgqWwS2D2LsMn1Y3rDCToqeAB"


def test_the_exclusion_list_parses_into_its_three_rules():
    rules = ct.parse_exclusions(EXCLUSIONS)
    assert [(r["addresses"], r["symbols"], r["names"]) for r in rules] == [
        ([DOPE], [], []), ([], ["SEALS"], ["Save The Seals"]), ([HVY], [], [])]
    with pytest.raises(ct.Refused):  # a rule that names nothing is refused, not skipped
        ct.parse_exclusions("| datum | mint |\n|---|---|\n| 2026-10-15 | de token van gisteren | x |\n")
    with pytest.raises(ct.Refused):
        ct.parse_exclusions("geen tabel")


def test_exclusions_resolve_both_ways():
    con = duckdb.connect()
    con.execute("ATTACH ':memory:' AS s")
    con.execute("CREATE TABLE s.mints (mint VARCHAR, symbol VARCHAR, name VARCHAR)")
    con.execute("CREATE TABLE s.pools (pool VARCHAR, mint VARCHAR)")
    con.executemany("INSERT INTO s.mints VALUES (?, ?, ?)", [(DOPE, "DOPE", "Doge Pepe"), ("SealMint", "seals ", "x"),
                                                              ("NameMint", "SAVE", " save the seals"), ("Other", "OTH", "o")])
    con.executemany("INSERT INTO s.pools VALUES (?, ?)", [("DopePool1", DOPE), ("DopePool2", DOPE), (HVY, "PoolMint"),
                                                          ("PoolMintPool2", "PoolMint"), ("OtherPool", "Other")])
    mints, pools, per_rule = ct.resolve_exclusions(con, ct.parse_exclusions(EXCLUSIONS))
    assert mints == {DOPE, "SealMint", "NameMint", "PoolMint"}
    assert pools == {"DopePool1", "DopePool2", HVY, "PoolMintPool2"}
    assert per_rule[2]["mints"] == ["PoolMint"]  # the address was a pool: its mint and that mint's pools


def test_bootstraps_and_the_decision():
    rows = [(0.02, 2), (0.01, 1), (0.03, 3)]
    m, lo, hi = ct.boot_mean(rows, draws=2_000)
    assert m == pytest.approx(0.01) and lo == pytest.approx(0.01) and hi == pytest.approx(0.01)  # equal per position
    assert ct.boot_mean([]) == (None, None, None)
    d, lo, hi = ct.boot_diff([(0.04, 2, -0.02, 2), (0.02, 1, -0.01, 1)], draws=2_000)
    assert d == pytest.approx(0.02 + 0.01) and lo == pytest.approx(0.03) and hi == pytest.approx(0.03)
    assert ct.boot_diff([(0.04, 2, 0.0, 0)]) == (None, None, None)  # no trade with rt_tx: no H2
    rng = np.random.default_rng(1)
    noisy = [(float(x), 1) for x in rng.normal(0.0, 0.05, 40)]
    _, lo, hi = ct.boot_mean(noisy, draws=2_000)
    assert lo < 0 < hi
    assert [ct.decision(*x) for x in ((True, True), (False, True), (True, False), (False, False))] == [
        "bevestigd", "risicofilter zonder bewezen winst", "verworpen", "verworpen"]


def test_documents_must_match_their_hashes(tmp_path):
    doc = tmp_path / "addendum.md"
    doc.write_text("vast")
    sha = hashlib.sha256(b"vast").hexdigest()
    assert ct.check_document(str(doc), sha[:16], "addendum") == sha
    with pytest.raises(ct.Refused):
        ct.check_document(str(doc), "0" * 64, "addendum")
    with pytest.raises(ct.Refused):
        ct.check_document(str(doc), sha[:8], "addendum")  # too short a prefix
    with pytest.raises(ct.Refused):  # outside a dry run the sidecar is required for a prefix
        ct.check_document(str(doc), sha[:16], "addendum", sidecar=True)
    assert ct.check_document(str(doc), sha, "addendum", sidecar=True) == sha  # or all 64 digits
    (tmp_path / "addendum.sha256").write_text(sha + "  addendum.md\n")
    assert ct.check_document(str(doc), sha[:16], "addendum", sidecar=True) == sha
    (tmp_path / "addendum.sha256").write_text("f" * 64 + "  addendum.md\n")
    with pytest.raises(ct.Refused):  # the recorded hash file disagrees
        ct.check_document(str(doc), sha, "addendum")


def _cfgs(**over):
    test = dict(ct.COMMON, delay_ms=600, delays=[None], decide_range=[455_760_000, 456_192_000], engine_commit="abc1234ff")
    matrix = dict(ct.COMMON, delay_ms=None, delays=[1, 2, 3], decide_range=[455_760_000, 456_192_000], engine_commit="abc1234ff")
    for k, v in over.items():
        (test if k.startswith("t_") else matrix)[k[2:]] = v
    return test, matrix


def test_the_runs_must_be_the_pre_registered_ones():
    meta = {"period": "clean", "window": [455_760_000, 456_192_000], "slot_start": 455_328_000}
    delays = {1055: 3}
    assert ct.check_runs(*_cfgs(), meta, delays, False, "abc1234") == (455_760_000, 456_192_000)
    for over in ({"t_delay_ms": None}, {"t_variants": ["tp4_T600"]}, {"t_sizes_sol": [0.5, 2.0]}, {"t_families": ["F7"]},
                 {"m_delays": [1, 2]}, {"m_engine_commit": "other"}, {"m_decide_range": [455_760_000, 456_000_000]},
                 {"t_decide_range": None, "m_decide_range": None}, {"t_n2_k": 3}):
        with pytest.raises(ct.Refused):
            ct.check_runs(*_cfgs(**over), meta, delays, False, "abc1234")
    with pytest.raises(ct.Refused):  # a d the Clock gives must be in the matrix run
        ct.check_runs(*_cfgs(), meta, {1055: 4}, False, "abc1234")
    with pytest.raises(ct.Refused):
        ct.check_runs(*_cfgs(), meta, delays, False, "def5678")
    with pytest.raises(ct.Refused):
        ct.check_runs(*_cfgs(), dict(meta, period="forward"), delays, False, "abc1234")
    with pytest.raises(ct.Refused):  # decisions must cover the store's window exactly
        ct.check_runs(*_cfgs(), dict(meta, window=[455_760_000, 456_624_000]), delays, False, "abc1234")


def test_the_replay_code_must_be_f2c9666s(tmp_path):
    import shutil

    here = os.path.dirname(os.path.dirname(os.path.abspath(ct.__file__)))
    assert ct.check_replay_code(os.path.join(here, "backtest")) == sorted(ct.REPLAY_SHA256)
    copy = tmp_path / "backtest"
    shutil.copytree(os.path.join(here, "backtest"), copy, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    with open(copy / "replay.py", "a") as f:
        f.write("# changed\n")
    with pytest.raises(ct.Refused):
        ct.check_replay_code(str(copy))


def test_equality_per_epoch_at_the_clocks_d(tmp_path):
    e55 = 1055 * EPOCH_SLOTS
    base = {"family": "F7", "segment": "pool_reversed", "variant": "tp4_T600", "size_sol": 0.5, "q": 0.5, "overlay": "none",
            "skipped": False, "pool": "P", "ret_x": 0.1, "day": "2026-10-11"}
    test = [dict(base, t=e55 + 10, d=3), dict(base, t=e55 + 20, d=3)]
    matrix = [dict(r, d=d) for r in test for d in (1, 2, 3)]
    for name, rows in (("test", test), ("matrix", matrix)):
        os.makedirs(tmp_path / name)
        duckdb.connect().execute(f"COPY (SELECT * FROM (VALUES {', '.join(_row(r) for r in rows)}) v({', '.join(base | {'t': 0, 'd': 0})})) "
                                 f"TO '{tmp_path / name / 'p.parquet'}' (FORMAT parquet)")
    con = duckdb.connect()
    ok, eq = ct.equality(con, str(tmp_path / "test" / "*.parquet"), str(tmp_path / "matrix" / "*.parquet"), {1055: 3})
    assert ok and eq[1055] == {"d": 3, "test_rows": 2, "matrix_rows": 2, "only_test": 0, "only_matrix": 0}
    ok, eq = ct.equality(con, str(tmp_path / "test" / "*.parquet"), str(tmp_path / "matrix" / "*.parquet"), {1055: 2})
    assert not ok and eq[1055]["only_test"] == 2  # the test run's d = 3 rows are not the matrix run's d = 2 rows


def _row(r):
    vals = [r[k] for k in ("family", "segment", "variant", "size_sol", "q", "overlay", "skipped", "pool", "ret_x", "day", "t", "d")]
    return "(" + ", ".join(f"'{v}'" if isinstance(v, str) else str(v).upper() if isinstance(v, bool) else str(v) for v in vals) + ")"


def test_the_matrix_fills_absent_days_with_zero_and_the_dsr_has_both_variances():
    labels, m = ct.matrix({"a": {"d1": 1.0, "d3": 2.0}, "b": {"d2": -1.0}}, ["d1", "d2", "d3"])
    assert labels == ["a", "b"] and m.tolist() == [[1.0, 0.0], [0.0, -1.0], [2.0, 0.0]]
    with pytest.raises(ct.Refused):  # a series day that is no row (another type or day) is refused, not zeroed
        ct.matrix({"a": {"2026-10-11": 1.0}}, ["2026-10-12"], strict=True)
    import datetime
    with pytest.raises(ct.Refused):
        ct.matrix({"a": {datetime.date(2026, 10, 11): 1.0}}, ["2026-10-11"], strict=True)
    rng = np.random.default_rng(3)
    d = ct.dsr_both(rng.normal(0.2, 1.0, 30), 42, 0.02)
    assert d["t"] == 30 and d["sr0_null"] > 0 and d["sr0_dev"] > 0 and 0 <= d["dsr_dev"] <= 1 and 0 <= d["dsr_null"] <= 1
    assert d["dsr_dev"] < d["psr0"]  # deflated below the PSR against 0
    assert ct.dsr_both([0.0, 0.0, 0.0], 42, 0.02)["sr"] is None


def test_delay_ms_in_whole_slots_of_the_triggers_epoch():
    clock = Clock({1054: 200.0, 1055: 400.0})
    assert delay_slots(600, 1055 * EPOCH_SLOTS, clock) == 2  # 1.5 rounds to 2
    assert delay_slots(600, 1054 * EPOCH_SLOTS, clock) == 3
    assert delay_slots(600, 1056 * EPOCH_SLOTS, clock) == 2  # an unmeasured epoch: the last measured one


def _chunk(root, lo, hi):
    d = os.path.join(root, f"{lo}-{hi}")
    os.makedirs(d)
    with open(os.path.join(d, "_manifest.json"), "w") as f:
        json.dump({"status": "complete", "slot_start": lo, "slot_end_exclusive": hi}, f)


def test_the_clean_period_warms_up_from_the_dataset_and_reads_the_window_from_the_clean_root(tmp_path, monkeypatch):
    v1, clean = str(tmp_path / "v1"), str(tmp_path / "clean")
    e54, e55, e56 = 1054 * EPOCH_SLOTS, 1055 * EPOCH_SLOTS, 1056 * EPOCH_SLOTS
    for lo in (e54, e54 + 216_000, e55):  # a 1055 chunk in the dataset root must not be used
        _chunk(v1, lo, lo + 216_000)
    for lo in (e55, e55 + 216_000):
        _chunk(clean, lo, lo + 216_000)
    monkeypatch.setenv("LAB_CHUNKS", v1)
    monkeypatch.setattr(store, "CLEAN_CHUNKS", clean)
    cfg = {"windows": {"holdout": {"start_slot": 452_304_000, "end_slot_exclusive": 454_464_000}}}
    chunks = store.period_chunks("clean", cfg, e55, e56, warmup_epochs=1)
    assert [(c[0], os.path.dirname(c[2])) for c in chunks] == [(e54, v1), (e54 + 216_000, v1), (e55, clean), (e55 + 216_000, clean)]
    assert [c[0] for c in store.period_chunks("clean", cfg, e55, e56)] == [e55, e55 + 216_000]  # no warm-up
    assert data.complete_chunks() and store.period_range("clean", cfg, e55, e56, 1) == (e54, e56)
    # the window's end defaults to the end of the clean root's complete chunks
    assert store.period_range("clean", cfg, None, None, 1) == (e54, e56)
    _chunk(clean, e56 + 216_000, e56 + 432_000)  # after a gap: not part of the window
    assert store.clean_window_end(e55) == e56
    for lo, hi in ((e54, e55), (e55, 1064 * EPOCH_SLOTS + 1)):
        with pytest.raises(SystemExit):
            store.period_range("clean", cfg, lo, hi)
    with pytest.raises(SystemExit):  # the window's second epoch is not extracted yet
        store.period_chunks("clean", cfg, e55, e56 + EPOCH_SLOTS)


def test_the_lock_is_the_one_run(tmp_path):
    from types import SimpleNamespace

    store = tmp_path / "clean-1055-1063.duckdb"
    store.write_bytes(b"store")
    add = tmp_path / "addendum.md"
    add.write_text("v5")
    runs = []
    for name in ("test", "matrix"):
        d = tmp_path / name
        d.mkdir()
        (d / "config.json").write_text(json.dumps({"engine_commit": "abc"}))
        runs.append(str(d))
    a = SimpleNamespace(store=str(store), addendum=str(add), test_run=runs[0], matrix_run=runs[1])
    first = ct.lock_run(a, "s1", {"engine_commit": "abc"}, {}, 9)
    assert (tmp_path / "clean.lock").exists() and first["trading_days"] == 9
    assert ct.lock_run(a, "s1", {"engine_commit": "abc"}, {}, 9)["store_sha256"] == "s1"  # a resume of the same run
    with pytest.raises(ct.Refused):  # another store (e.g. rebuilt with more epochs) after the run
        ct.lock_run(a, "s2", {"engine_commit": "abc"}, {}, 9)


@pytest.mark.parametrize("meta", [{"period": "clean", "slot_end_exclusive": 456_192_000},
                                  {"period": "forward", "slot_end_exclusive": 456_192_000}])
def test_a_dry_run_never_reads_the_clean_data(tmp_path, monkeypatch, meta):
    from types import SimpleNamespace

    store = str(tmp_path / "s.duckdb")
    c = duckdb.connect(store)
    c.execute("CREATE TABLE meta AS SELECT ? AS json", [json.dumps(meta)])
    c.close()
    monkeypatch.setattr(ct, "check_document", lambda *a, **k: "0" * 64)
    a = SimpleNamespace(dry_run=True, hypothesis="h", exclusions="e", exclusions_sha256="0" * 64, store=store)
    with pytest.raises(ct.Refused, match="never reads the clean data"):
        ct.run_test(a)


def test_failed_entries_cost_in_every_column_and_not_in_the_per_trade_tests(tmp_path):
    """A failed K3 entry's cost (its net P&L) is in K3|clock and the filter columns, as in the dev
    columns built the same way; counts and means per filled trade leave it out."""
    base = {"family": "F7", "segment": "pool_reversed", "variant": "tp4_T600", "size_sol": 0.5, "q": 0.5, "overlay": "none",
            "farm": "organic", "mint": "M", "day": "2026-10-11", "trigger_trader": "W"}
    rows = [dict(base, pool="A", t=1, entry_failed=False, skipped=False, pnl_x=10_000_000, ret_x=0.02),
            dict(base, pool="B", t=2, entry_failed=False, skipped=False, pnl_x=-5_000_000, ret_x=-0.01),
            dict(base, pool="C", t=3, entry_failed=True, skipped=False, pnl_x=-505_000, ret_x=None),
            dict(base, pool="D", t=4, entry_failed=False, skipped=True, pnl_x=None, ret_x=None)]
    con = duckdb.connect()
    for name, d in (("test", 3), ("matrix", 1)):
        os.makedirs(tmp_path / name)
        con.execute("CREATE OR REPLACE TABLE r (family VARCHAR, segment VARCHAR, variant VARCHAR, size_sol DOUBLE, q DOUBLE, overlay VARCHAR, "
                    "farm VARCHAR, mint VARCHAR, day VARCHAR, trigger_trader VARCHAR, pool VARCHAR, t BIGINT, entry_failed BOOLEAN, "
                    "skipped BOOLEAN, pnl_x BIGINT, ret_x DOUBLE, d BIGINT)")
        con.executemany("INSERT INTO r VALUES (" + ", ".join("?" * 17) + ")", [list(r.values()) + [d] for r in rows])
        con.execute(f"COPY r TO '{tmp_path / name / 'p.parquet'}' (FORMAT parquet)")
    con.execute("CREATE TABLE f_rt AS SELECT * FROM (VALUES ('A', 1, 'W', false), ('B', 2, 'W', true), ('C', 3, 'W', false)) v(pool, t, trigger_trader, rt_tx)")
    con.execute("CREATE TABLE f_w (pool VARCHAR, t BIGINT, wash_share DOUBLE)")
    tg, mg = str(tmp_path / "test" / "*.parquet"), str(tmp_path / "matrix" / "*.parquet")
    ct.k3_frame(con, tg, mg, "true", "true", "ret_x", "pnl_x")
    s = ct.k3_series(con)
    all_rows = (10_000_000 - 5_000_000 - 505_000) / 1e9
    assert s["K3|clock"]["2026-10-11"] == pytest.approx(all_rows)
    assert ct._config_series(con, mg, "pnl_x", "true", (1,))[("tp4_T600", 1, "all")]["2026-10-11"] == pytest.approx(all_rows)
    assert s["K3 zonder rt_tx|d1"]["2026-10-11"] == pytest.approx((10_000_000 - 505_000) / 1e9)  # A and the failed C
    assert s["K3 zonder rt_tx|d_ms600"] == s["K3 zonder rt_tx|d1"]
    n, mean = con.execute(f"SELECT count(*), avg(ret) FROM kf WHERE src = 'matrix' AND d = 1 AND NOT rt_tx AND {ct.FILLED}").fetchone()
    assert (n, mean) == (1, 0.02)  # per filled trade (a4c's check, H1/H2): the failed entry is not a trade
