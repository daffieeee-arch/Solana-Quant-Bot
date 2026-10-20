"""The shared golden cases of M3-10 (research/m3-10-farmer-flags-spec-2026-10-20.md v0.2 §6.4): LiveFeed's farmer flag
(paper/live.py FarmerFlags, unchanged) on hand-made sequences of operations, whose answers and window states lab-live's
port (lab/live/src/farm_live.rs) must reproduce. test_m3_10_farmer_cases.py asserts that FarmerFlags still gives exactly
the stored answers; lab-live's test reads the same file.

  M3_10_CASES_REGEN=1 pytest paper/tests/test_m3_10_farmer_cases.py     # rewrite data/m3_10_farmer_cases.json
  python -m paper.tests.m3_10_farmer_cases                               # the same

FORMAT below (also in the file). Pools and traders are short names; a trader null is Python's None, a key like any
other. The answers come from FarmerFlags itself, run op by op on a fresh instance per case.
"""

import json
import os

from backtest.store import FARMER_MIN_TRADES, FARMER_RATIO
from paper.live import FarmerFlags

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "m3_10_farmer_cases.json")

FORMAT = {
    "file": "{format, constants, cases: [case]}; every case runs on a fresh FarmerFlags.",
    "constants": "backtest/store.py's FARMER_RATIO and FARMER_MIN_TRADES, as the flag uses them.",
    "case": "name, why, ops: [op], each op a list; the ops with an answer carry it as their last element.",
    "add": "[\"add\", pool, trader, slot, token_buy, amount]: FarmerFlags.add (token_buy null is falsy: a sell's sign).",
    "flag": "[\"flag\", pool, trader, slot, h24, answer]: FarmerFlags.flag, the answer a bool.",
    "state": "[\"state\", pool, trader, [start, end, n, net, gross, len]]: the key's _Trades after the ops so far, start, "
             "end and len in triples (the array's flat indices / 3); net and gross as decimal strings (unbounded ints).",
    "prune": "[\"prune\", slot, h24, [[pool, trader], ...]]: FarmerFlags.prune, then the keys left, sorted (null last).",
    "entries": "[\"entries\", n]: FarmerFlags.entries(), the triples from each key's start on.",
}

# Two trades of one key whose gross is above 2^53 (the double spacing above 1): |net| < 0.05 * gross as Python
# computes it (a float) and 20 |net| < gross (the store's integer rule, lab/live legs.rs FarmHist) answer differently.
BIG_FLAG = (2207245274034650097, 1997031438412302468)  # net 210213835622347629, gross 4204276712446952565: float yes, integer no
BIG_NO = (2299638931080683164, 2080625699549189532)  # net 219013231531493632, gross 4380264630629872696: float no, integer yes


def _cases():
    """The cases' ops without answers (flag, state, prune and entries get theirs from FarmerFlags)."""
    P, Q, W, V = "P", "Q", "W", "V"
    return [
        {"name": "sliding_window", "why": "the window [t - h24, t) slides: trades leave at its start, a trade at t itself is "
                                          "outside; two trades with |net| below 5 % of gross flag",
         "ops": [["add", P, W, 100, True, 1000], ["flag", P, W, 150, 100], ["add", P, W, 150, False, 990],
                 ["flag", P, W, 151, 100], ["flag", P, W, 150, 100], ["state", P, W], ["flag", P, W, 201, 100],
                 ["flag", P, W, 251, 100], ["state", P, W], ["add", P, W, 260, True, 5], ["flag", P, W, 300, 100]]},
        {"name": "same_slot_trades", "why": "trades of one slot never see each other: the window ends before the slot",
         "ops": [["add", P, W, 500, True, 100], ["add", P, W, 500, False, 100], ["flag", P, W, 500, 1000],
                 ["flag", P, W, 501, 1000], ["state", P, W]]},
        {"name": "start_moves_forward_only", "why": "the quirk: after a query at 1000 moved the start past 850 and 880, a "
                                                    "query at 950 (window [850, 950)) does not see them; the store's rule "
                                                    "would",
         "ops": [["add", P, W, 850, True, 100], ["add", P, W, 880, False, 100], ["add", P, W, 920, True, 10],
                 ["flag", P, W, 900, 100], ["flag", P, W, 1000, 100], ["state", P, W], ["flag", P, W, 950, 100],
                 ["state", P, W], ["flag", P, W, 1010, 100]]},
        {"name": "a_shorter_h24_then_a_longer", "why": "a later query with a longer day cannot move the start back either",
         "ops": [["add", P, W, 100, True, 50], ["add", P, W, 150, False, 50], ["flag", P, W, 300, 100],
                 ["flag", P, W, 301, 1000], ["state", P, W]]},
        {"name": "backfill_insertions", "why": "an older trade is inserted in slot order after its slot's trades, never "
                                               "before the start; left of the last query's end it joins its sums",
         "ops": [["add", P, W, 100, True, 10], ["add", P, W, 200, False, 10], ["add", P, W, 300, True, 10],
                 ["flag", P, W, 250, 1000], ["state", P, W], ["add", P, W, 150, True, 7], ["state", P, W],
                 ["add", P, W, 200, True, 3], ["state", P, W], ["add", P, W, 260, False, 4], ["state", P, W],
                 ["flag", P, W, 400, 120], ["state", P, W], ["add", P, W, 50, False, 9], ["state", P, W],
                 ["flag", P, W, 400, 120], ["flag", P, W, 301, 1000]]},
        {"name": "keys_none_and_token_buy_none", "why": "a trader None is a key of its own; token_buy None counts as a sell",
         "ops": [["add", P, None, 10, True, 100], ["add", P, None, 20, None, 98], ["flag", P, None, 30, 100],
                 ["add", P, W, 10, True, 100], ["flag", P, W, 30, 100], ["state", P, None], ["entries"]]},
        {"name": "amounts_from_2_63", "why": "amounts of 2^63 and more (Python switches the key to a plain list); the sums "
                                             "are exact",
         "ops": [["add", Q, W, 1, True, 2**63 + 5], ["add", Q, W, 2, False, 2**63 + 1], ["add", Q, W, 3, True, 2**64 - 1],
                 ["flag", Q, W, 3, 100], ["state", Q, W], ["flag", Q, W, 4, 100], ["state", Q, W],
                 ["add", Q, W, 2, True, 7], ["state", Q, W]]},
        {"name": "the_float_threshold_above_2_53", "why": "|net| < 0.05 * gross in floats (Python) against 20 |net| < "
                                                          "gross (the store): with a gross above 2^53 the two can differ",
         "ops": [["add", P, W, 10, True, BIG_FLAG[0]], ["add", P, W, 11, False, BIG_FLAG[1]], ["flag", P, W, 12, 100],
                 ["add", P, V, 10, True, BIG_NO[0]], ["add", P, V, 11, False, BIG_NO[1]], ["flag", P, V, 12, 100],
                 ["state", P, W], ["state", P, V]]},
        {"name": "fewer_than_two_trades", "why": "one trade never flags; a second that nets it out does",
         "ops": [["add", P, W, 10, True, 10], ["flag", P, W, 11, 100], ["add", P, W, 11, False, 10], ["flag", P, W, 12, 100]]},
        {"name": "zero_amounts", "why": "two trades of no tokens: |net| 0 is not below 5 % of a gross of 0",
         "ops": [["add", P, W, 10, True, 0], ["add", P, W, 11, False, 0], ["flag", P, W, 12, 100]]},
        {"name": "compaction", "why": "a key whose start passed more than 32 triples and half its array gives the memory "
                                      "back: the indices shift, no answer changes",
         "ops": [op for k in range(80) for op in (["add", P, W, 10 + k, k % 2 == 0, 100],)]
                + [["flag", P, W, 100, 30], ["state", P, W], ["flag", P, W, 101, 30], ["state", P, W],
                   ["add", P, W, 75, True, 1], ["state", P, W], ["flag", P, W, 120, 30], ["state", P, W]]},
        {"name": "prune", "why": "prune drops the keys whose last trade left the window (or with no trade left) and "
                                 "compacts the others",
         "ops": [["add", P, W, 10, True, 5], ["add", P, V, 10, True, 5], ["add", P, V, 90, False, 5], ["add", Q, W, 50, True, 1],
                 ["flag", Q, W, 200, 100], ["prune", 150, 100], ["entries"], ["state", P, V],
                 ["add", P, W, 160, False, 5], ["flag", P, W, 170, 100], ["prune", 400, 100], ["entries"]]},
    ]


def _state(t):
    a = t.a
    return [t.start // 3, t.end // 3, t.n, str(t.net), str(t.gross), len(a) // 3]


def _key_sort(k):
    return (k[0], k[1] is None, k[1] or "")


def build():
    out = []
    for case in _cases():
        f = FarmerFlags()
        ops = []
        for op in case["ops"]:
            kind = op[0]
            if kind == "add":
                f.add(op[1], op[2], op[3], op[4], op[5])
                ops.append(op)
            elif kind == "flag":
                ops.append(op + [bool(f.flag(op[1], op[2], op[3], op[4]))])
            elif kind == "state":
                ops.append(op + [_state(f.hist[(op[1], op[2])])])
            elif kind == "prune":
                f.prune(op[1], op[2])
                ops.append(op + [sorted(([p, t] for p, t in f.hist), key=_key_sort)])
            elif kind == "entries":
                ops.append(op + [f.entries()])
            else:
                raise ValueError(kind)
        out.append({"name": case["name"], "why": case["why"], "ops": ops})
    return {"format": FORMAT, "constants": {"farmer_ratio": FARMER_RATIO, "farmer_min_trades": FARMER_MIN_TRADES}, "cases": out}


def text():
    return json.dumps(build(), indent=1) + "\n"


def write():
    with open(PATH, "w") as f:
        f.write(text())


if __name__ == "__main__":
    write()
