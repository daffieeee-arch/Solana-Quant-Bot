"""Clock: an epoch not measured yet takes the last measured epoch before it (live: the first 1,000
slots of a new epoch, e.g. after Alpenglow's switch in epoch 1064 changed the slot time)."""

import pytest

from backtest.strategies import EPOCH_SLOTS, REF_MS, Clock
from wallets.common import Clock as WClock


@pytest.mark.parametrize("cls", [Clock, WClock])
def test_an_unmeasured_epoch_takes_the_last_measured_one_before_it(cls):
    c = cls({1040: 267.1, 1045: 201.5})
    assert c.ms_per_slot(1040 * EPOCH_SLOTS) == 267.1 and c.ms_per_slot(1045 * EPOCH_SLOTS + 5) == 201.5  # measured
    assert c.ms_per_slot(1046 * EPOCH_SLOTS) == 201.5  # after the last measured epoch
    assert c.ms_per_slot(1042 * EPOCH_SLOTS) == 267.1  # between two measured epochs: the earlier one
    assert c.ms_per_slot(1030 * EPOCH_SLOTS) == REF_MS and cls().ms_per_slot(0) == REF_MS  # nothing before it
    c.per_epoch[1046] = 199.0  # LiveFeed measured the new epoch
    assert c.ms_per_slot(1046 * EPOCH_SLOTS) == 199.0 and c.ms_per_slot(1047 * EPOCH_SLOTS) == 199.0


def test_windows_in_a_new_epoch_follow_the_last_measured_slot_time():
    c = Clock({1063: 267.3})
    assert c.slots(60_000, 1064 * EPOCH_SLOTS) == round(60_000 / 267.3)
    c.per_epoch[1064] = 200.0
    assert c.slots(60_000, 1065 * EPOCH_SLOTS) == 300
