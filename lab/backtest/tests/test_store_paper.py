"""The paper snapshot store (PAPER_STORE): a window in the clean epochs (1055-1063) only once the clean data is spent,
i.e. E20's clean.lock and a real result with a decision of H3, H4 and H5 (coordinator, 2026-10-16); the clean epochs
come from the clean root, the rest from the dataset."""

import json
import os
import sys

import pytest

from backtest import data, store

E = store.HOLDOUT_WARMUP_SLOTS  # one epoch
CFG = {"windows": {"never_include_from_slot": 454_896_000, "holdout": {"start_slot": 452_304_000, "end_slot_exclusive": 454_464_000}}}


def _chunk(root, lo, hi):
    d = os.path.join(root, f"{lo}-{hi}")
    os.makedirs(d)
    with open(os.path.join(d, "_manifest.json"), "w") as f:
        json.dump({"status": "complete", "slot_start": lo, "slot_end_exclusive": hi}, f)


def _result(results, name, sha="abc1234", **r):
    d = os.path.join(results, f"{name}-{sha}", "result")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "result.json"), "w") as f:
        json.dump(r, f)


@pytest.fixture
def roots(tmp_path, monkeypatch):
    v1, clean, results = str(tmp_path / "v1"), str(tmp_path / "clean"), str(tmp_path / "clean-test")
    for e in (1053, 1054, 1055):  # a 1055 chunk in the dataset root must not be used
        for half in (0, E // 2):
            _chunk(v1, e * E + half, e * E + half + E // 2)
    for e in range(1055, 1064):
        for half in (0, E // 2):
            _chunk(clean, e * E + half, e * E + half + E // 2)
    monkeypatch.setenv("LAB_CHUNKS", v1)
    monkeypatch.setattr(store, "CLEAN_CHUNKS", clean)
    lock = str(tmp_path / "store" / "clean.lock")
    os.makedirs(os.path.dirname(lock))
    return v1, clean, results, lock


def _spend(results, lock):
    with open(lock, "w") as f:
        json.dump({"store_sha256": "0" * 64, "test_run": "e20"}, f)
    for name in ("clean", "h3", "h4", "h5"):
        _result(results, name, dry_run=False, decision="verworpen")


def test_the_clean_data_is_spent_only_with_the_lock_and_a_real_result_of_e20_h3_h4_and_h5(roots):
    _, _, results, lock = roots
    why, _ = store.clean_unspent(lock, results)
    assert len(why) == 5 and "clean.lock" in why[0] and "no E20 result" in why[1]
    _result(results, "clean", dry_run=False, decision="niet beoordeeld: 4 handelsdagen")  # E20's own result
    with open(lock, "w") as f:
        json.dump({"store_sha256": "0" * 64}, f)
    _result(results, "h3", dry_run=False, decision="verworpen")
    _result(results, "h4", dry_run=False, decision="niet beoordeeld: 3 dagen")
    _result(results, "h5", sha="dry0000", dry_run=True, decision="a dry run")  # a dry run is no use of the data
    _result(results, "h5", sha="ref0000", dry_run=False, control={"ok": False})  # refused before the outcomes
    why, _ = store.clean_unspent(lock, results)
    assert why == [f"no H5 result (result.json of a real run with a decision) under {results}"]
    _result(results, "h5", dry_run=False, decision="verworpen")
    why, evidence = store.clean_unspent(lock, results)
    assert why == [] and len(evidence["clean_lock_sha256"]) == 64
    assert evidence["results"]["h5"] == [os.path.join(results, "h5-abc1234", "result", "result.json")]


def test_the_default_window_needs_the_spent_clean_data(roots):
    with pytest.raises(SystemExit, match="not spent yet"):
        store.paper_window(None, None, 2, spent=False)
    assert store.paper_window(None, None, 2, spent=True) == (1062 * E, 1064 * E)  # the last two clean epochs
    assert store.paper_window(None, 1060 * E, 1, spent=True) == (1059 * E, 1060 * E)
    assert store.paper_window(1053 * E, 1055 * E, 2, spent=False) == (1053 * E, 1055 * E)  # explicit, before the clean data


def test_the_chunks_come_from_the_clean_root_only_once_spent(roots):
    v1, clean, _, _ = roots
    with pytest.raises(SystemExit, match="not spent yet"):
        store.period_chunks("paper", CFG, 1054 * E, 1056 * E)
    chunks = store.period_chunks("paper", CFG, 1054 * E, 1056 * E, clean_spent=True)
    assert [(c[0], os.path.dirname(c[2])) for c in chunks] == [
        (1054 * E, v1), (1054 * E + E // 2, v1), (1055 * E, clean), (1055 * E + E // 2, clean)]
    before = store.period_chunks("paper", CFG, 1053 * E, 1055 * E)  # no clean epoch: no gate
    assert {os.path.dirname(c[2]) for c in before} == {v1} and len(before) == 4
    with pytest.raises(SystemExit, match="never_include_from_slot"):
        store.period_range("paper", CFG, 1052 * E, 1053 * E)
    assert data.complete_chunks()


def _main(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["store", "paper", *args])
    monkeypatch.setattr(store, "load_defaults", lambda *a: CFG)
    built = {}

    def build(period, out, cfg, chunks, log=print, extra_meta=None):
        built.update(period=period, out=out, chunks=chunks, meta=extra_meta)
        return {"period": period}

    monkeypatch.setattr(store, "build", build)
    monkeypatch.setattr(store, "_disk_check", lambda *a: (1.0, 100.0))
    store.main()
    return built


def test_the_command_refuses_the_clean_epochs_until_they_are_spent(roots, tmp_path, monkeypatch):
    _, clean, results, lock = roots
    gate = ("--clean-lock", lock, "--clean-results", results, "--out-dir", str(tmp_path / "out"))
    with pytest.raises(SystemExit, match="not spent yet"):
        _main(monkeypatch, *gate)  # the default window
    with pytest.raises(SystemExit, match="refused: the window reads clean epochs and the clean data is not spent yet"):
        _main(monkeypatch, *gate, "--from-slot", str(1062 * E), "--to-slot", str(1064 * E))
    b = _main(monkeypatch, *gate, "--from-slot", str(1053 * E), "--to-slot", str(1055 * E))  # route B: before the clean data
    assert b["out"].endswith("paper-1053-1054.duckdb") and "clean_spent" not in b["meta"]
    _spend(results, lock)
    b = _main(monkeypatch, *gate)
    assert b["out"].endswith("paper-1062-1063.duckdb") and {os.path.dirname(c[2]) for c in b["chunks"]} == {clean}
    assert b["meta"]["purpose"].startswith("paper snapshot") and set(b["meta"]["clean_spent"]["results"]) == {"clean", "h3", "h4", "h5"}
