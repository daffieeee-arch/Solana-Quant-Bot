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
    (tmp_path / "addendum.sha256").write_text("f" * 64 + "  addendum.md\n")
    with pytest.raises(ct.Refused):  # the recorded hash file disagrees
        ct.check_document(str(doc), sha, "addendum")


def test_the_run_must_be_k3_on_the_clean_store(tmp_path):
    cfg = dict(ct.K3, decide_range=[455_760_000, 456_192_000])
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    meta = {"period": "clean", "slot_start": 455_328_000, "slot_end_exclusive": 456_192_000}
    assert ct.check_run(str(tmp_path), meta, False) == (455_760_000, 456_192_000)
    for k, v in (("delay_ms", None), ("variants", None), ("sizes_sol", [0.5, 2.0]), ("families", ["F7", "O7"])):
        (tmp_path / "config.json").write_text(json.dumps(dict(cfg, **{k: v})))
        with pytest.raises(ct.Refused):
            ct.check_run(str(tmp_path), meta, False)
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    with pytest.raises(ct.Refused):
        ct.check_run(str(tmp_path), dict(meta, period="dev"), False)
    with pytest.raises(ct.Refused):  # decisions must end with the store
        ct.check_run(str(tmp_path), dict(meta, slot_end_exclusive=456_624_000), False)


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
    chunks = store.period_chunks("clean", cfg, e55, e56)
    assert [(c[0], os.path.dirname(c[2])) for c in chunks] == [(e54, v1), (e54 + 216_000, v1), (e55, clean), (e55 + 216_000, clean)]
    assert data.complete_chunks() and store.period_range("clean", cfg, e55, e56) == (e54, e56)
    for lo, hi in ((e54, e55), (e55, 1064 * EPOCH_SLOTS + 1), (None, e56)):
        with pytest.raises(SystemExit):
            store.period_range("clean", cfg, lo, hi)
    with pytest.raises(SystemExit):  # the window's second epoch is not extracted yet
        store.period_chunks("clean", cfg, e55, e56 + EPOCH_SLOTS)
