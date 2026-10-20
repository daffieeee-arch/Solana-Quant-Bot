"""M3-10's golden cases (paper/tests/m3_10_farmer_cases.py): LiveFeed's FarmerFlags still gives exactly the stored answers,
so lab-live's port (lab/live/src/farm_live.rs, which reads the same file) mirrors today's LiveFeed.

  M3_10_CASES_REGEN=1 pytest paper/tests/test_m3_10_farmer_cases.py      # rewrite the file from FarmerFlags first
"""

import json
import os

from backtest.store import FARMER_MIN_TRADES, FARMER_RATIO
from paper.tests import m3_10_farmer_cases as C

if os.environ.get("M3_10_CASES_REGEN") == "1":
    C.write()


def test_farmer_flags_gives_the_stored_answers():
    with open(C.PATH) as f:
        assert f.read() == C.text()


def test_the_constants_are_the_stores():
    # spec v0.2 §1.1: the flag's two constants, pinned to backtest/store.py (lab-live's test checks the file's copy)
    assert (FARMER_RATIO, FARMER_MIN_TRADES) == (0.05, 2)
    assert json.load(open(C.PATH))["constants"] == {"farmer_ratio": 0.05, "farmer_min_trades": 2}


def test_the_cases_show_what_they_name():
    cases = {c["name"]: c["ops"] for c in json.load(open(C.PATH))["cases"]}
    flags = lambda name: [op[-1] for op in cases[name] if op[0] == "flag"]  # noqa: E731
    # the quirk: at 950 the window [850, 950) holds 850 and 880 (a farmer by the store's rule), but the query at 1000
    # moved the start past them
    assert flags("start_moves_forward_only") == [True, False, False, False]
    # the float threshold: the first key flags only in floats, the second only in integers
    big = [op for op in cases["the_float_threshold_above_2_53"] if op[0] == "add"]
    (a, b), (c, d) = (big[0][5], big[1][5]), (big[2][5], big[3][5])
    assert flags("the_float_threshold_above_2_53") == [True, False]
    assert (20 * (a - b) < a + b, 20 * (c - d) < c + d) == (False, True)
    assert flags("same_slot_trades") == [False, True]
    assert flags("fewer_than_two_trades") == [False, True]
    assert flags("zero_amounts") == [False]  # 0 < 0.0 is false
    # compaction happened (the start went back to 0) and the answers did not change with it
    states = [op[-1] for op in cases["compaction"] if op[0] == "state"]
    assert states[0][0] == 0 and states[0][5] < 80
