"""store.period_range: the forward period (1053 on) is only for illustration stores and starts at or
after week2-defaults' never_include_from_slot; the other periods keep their ranges."""

import pytest

from backtest import store

CFG = store.load_defaults()


def test_forward_range():
    assert store.period_range("forward", CFG, 454896000, 455328000) == (454896000, 455328000)


def test_forward_needs_both_slots():
    with pytest.raises(SystemExit, match="needs --from-slot and --to-slot"):
        store.period_range("forward", CFG, 454896000)


def test_forward_not_before_paper_period():
    first = CFG["windows"]["never_include_from_slot"]
    with pytest.raises(SystemExit, match="never_include_from_slot"):
        store.period_range("forward", CFG, first - 1, 455328000)


def test_forward_follows_validation():
    # No gap and no overlap between the validation epoch (1052) and the forward period (1053 on).
    assert CFG["windows"]["never_include_from_slot"] == store.VALIDATE[1] == 454896000


def test_holdout_and_validate_unchanged():
    hold = CFG["windows"]["holdout"]
    assert store.period_range("holdout", CFG) == (hold["start_slot"] - store.HOLDOUT_WARMUP_SLOTS,
                                                   hold["end_slot_exclusive"])
    assert store.period_range("validate", CFG) == store.VALIDATE
