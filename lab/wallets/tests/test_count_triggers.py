"""count_triggers (live against OF1, trigger counts only): the input names and the window are parameters since 2026-10-29
(plan step 1 after Alpenglow section 2.5: the full-coverage inputs are named *-fullcov-20261024.parquet and span 24-27 Oct)."""
import pytest

from wallets.of1_1064 import count_triggers as C


def test_resolve_inputs_takes_the_given_names_and_tolerates_a_missing_counts_json(tmp_path):
    for n in ("live-triggers-fullcov-20261024.parquet", "probe-pools-fullcov-20261024.parquet", "SHA256SUMS"):
        (tmp_path / n).write_bytes(b"x")
    got = C.resolve_inputs(str(tmp_path), "live-triggers-fullcov-20261024.parquet", "probe-pools-fullcov-20261024.parquet", "check-chunk-recorder.json")
    assert got["live"].endswith("live-triggers-fullcov-20261024.parquet") and got["probe"].endswith("probe-pools-fullcov-20261024.parquet")
    assert got["recorder_counts"] is None, "the counts json is optional on the full-coverage inputs"
    with pytest.raises(FileNotFoundError, match="live-triggers-explore.parquet"):
        C.resolve_inputs(str(tmp_path), "live-triggers-explore.parquet", "probe-pools-fullcov-20261024.parquet")
    (tmp_path / "SHA256SUMS").unlink()
    with pytest.raises(FileNotFoundError, match="SHA256SUMS"):
        C.resolve_inputs(str(tmp_path), "live-triggers-fullcov-20261024.parquet", "probe-pools-fullcov-20261024.parquet")


def test_check_window_allows_several_epochs_inside_the_store_and_refuses_outside():
    meta = {"slot_start": 459_648_000, "slot_end_exclusive": 461_808_000}   # contiguous 1064-1068
    assert C.check_window(meta, 460_512_000 + 4_000, 461_000_000) == [1066, 1067]
    assert C.check_window(meta, 459_671_698, 460_080_000) == [1064]
    with pytest.raises(ValueError, match="before the store"):
        C.check_window(meta, 459_000_000, 460_000_000)
    with pytest.raises(ValueError, match="after the store"):
        C.check_window(meta, 461_000_000, 461_808_001)
    with pytest.raises(ValueError, match="empty"):
        C.check_window(meta, 460_000_000, 460_000_000)
    assert C.check_window({}, 460_000_000, 460_001_000) == [1064], "a store without slot bounds in meta is not refused"
