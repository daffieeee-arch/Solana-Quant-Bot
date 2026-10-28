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
        C.check_window(meta, 459_600_000, 460_000_000)   # after the clean period, before this store
    with pytest.raises(ValueError, match="after the store"):
        C.check_window(meta, 461_000_000, 461_808_001)
    with pytest.raises(ValueError, match="empty"):
        C.check_window(meta, 460_000_000, 460_000_000)
    assert C.check_window({}, 460_000_000, 460_001_000) == [1064], "a store without slot bounds in meta is not refused"


def test_check_window_refuses_any_overlap_with_the_holdout_and_the_clean_period():
    """LAB review on #602: the old assert (epoch 1064 only) excluded the consumed data by construction; the window check must say so
    itself, independent of the store's meta."""
    wide = {"slot_start": 446_256_000, "slot_end_exclusive": 461_808_000}   # a store that would allow everything
    for lo, hi in ((452_000_000, 452_304_001), (452_304_000, 452_400_000), (454_463_999, 454_500_000), (452_000_000, 455_000_000),   # hold-out
                   (455_700_000, 455_760_001), (456_000_000, 457_000_000), (459_215_999, 459_300_000), (451_000_000, 460_000_000)):   # clean
        with pytest.raises(ValueError, match="consumed data"):
            C.check_window(wide, lo, hi)
    assert C.check_window(wide, 452_000_000, 452_304_000) == [1046], "ending exactly at the hold-out start is allowed"
    assert C.check_window(wide, 454_464_000, 455_760_000) == [1052, 1053, 1054], "the gap between hold-out and clean is allowed"
    assert C.check_window(wide, 459_216_000, 460_000_000) == [1063, 1064], "starting exactly at the clean end is allowed"
    assert C.check_window({}, 459_648_000, 461_808_000) == [1064, 1065, 1066, 1067, 1068]
