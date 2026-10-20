"""paper/risk.py `load` never raises on what a risk file holds (owner, 2026-10-19: fix first). The owner edits the file to
stop trading, so a typo must give `(None, reason)`, which opens nothing, and never an exception, which stopped the engine
at its next minute (`_load_risk` runs every minute)."""

import pytest

from paper import risk as R
from paper.tests.test_engine import run, trigger_token

BASE = ("size_sol: 0.5\nmax_open_positions: 5\nmax_exposure_sol: 2.5\ndaily_loss_limit_sol: 1.0\nkill_switch: false\n"
        "families: [F7]\npool_cooldown_h: 24\n")
# Each raised before this fix (TypeError at sorted() or set(), OverflowError, UnicodeDecodeError).
CRASHED = [
    ("a number and a word", BASE.replace("[F7]", "[F8, 7]").encode(), "risk config invalid: families must be names"),
    ("a list in the families", BASE.replace("[F7]", "[[F7]]").encode(), "risk config invalid: families must be names"),
    ("a mapping in the families", BASE.replace("[F7]", "[{a: 1}]").encode(), "risk config invalid: families must be names"),
    ("an int too large for a float", BASE.replace("size_sol: 0.5", "size_sol: 1" + "0" * 400).encode(),
     "risk config invalid: OverflowError int too large to convert to float"),
    ("infinity for an int", BASE.replace("max_open_positions: 5", "max_open_positions: .inf").encode(),
     "risk config invalid: OverflowError cannot convert float infinity to integer"),
    ("not UTF-8", BASE.replace("[F7]", "[F7]  # caf\xe9").encode("latin-1"), "risk config unreadable: UnicodeDecodeError"),
    # load passed these, and the engine raised at their first use: size_lamports of an infinite size, Clock.slots of an
    # infinite cooldown (found by the review of lab-live's reader, #383)
    ("an infinite size", BASE.replace("size_sol: 0.5", "size_sol: .inf").replace("max_exposure_sol: 2.5", "max_exposure_sol: .inf").encode(),
     "risk config invalid: sizes and limits must be finite"),
    ("an infinite cooldown", BASE.replace("pool_cooldown_h: 24", "pool_cooldown_h: .inf").encode(),
     "risk config invalid: sizes and limits must be finite"),
]


@pytest.mark.parametrize("name,data,reason", CRASHED, ids=[c[0] for c in CRASHED])
def test_a_bad_file_is_a_reason_never_an_exception(tmp_path, name, data, reason):
    p = tmp_path / "risk.yaml"
    p.write_bytes(data)
    assert R.load(str(p)) == (None, reason)


def test_the_valid_file_and_the_other_reasons_are_unchanged(tmp_path):
    """Only one reason changes: a number alone among the families (it was 'unknown families [7]')."""
    p = tmp_path / "risk.yaml"
    p.write_text(BASE)
    assert R.load(str(p)) == (R.Risk(0.5, 5, 2.5, 1.0, False, ("F7",), 24.0), None)
    p.write_text(BASE.replace("[F7]", "[F8, A]"))
    assert R.load(str(p)) == (None, "risk config invalid: unknown families ['A', 'F8']")
    p.write_text(BASE.replace("[F7]", "[7]"))
    assert R.load(str(p)) == (None, "risk config invalid: families must be names")


def test_the_engine_runs_on_with_such_a_file_and_opens_nothing(tmp_path):
    """Before the fix this run raised TypeError at its first dated minute."""
    p = tmp_path / "risk.yaml"
    p.write_bytes(CRASHED[0][1])
    j = run(tmp_path, {"M1": trigger_token(), "M2": trigger_token()}, str(p))
    rows = j.rows("signals")
    assert rows and {(s["status"], s["reason"]) for s in rows} == {("skipped", "risk config invalid: families must be names")}
    assert j.rows("orders") == []
