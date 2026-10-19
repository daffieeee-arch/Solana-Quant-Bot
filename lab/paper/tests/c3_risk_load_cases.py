"""The golden table of M1 C3-1 (research/m1-c3-risk-spec-2026-10-19.md §5, D1, D2): risk files and what the unmodified
`paper.risk.load` makes of them, for lab-live's risk reader (lab/live/src/risk.rs).

  python -m paper.tests.c3_risk_load_cases        # rewrite paper/tests/data/c3_risk_load_cases.json

Every case is a file text (or a missing file, or a directory) with Python's result: the Risk with its size_lamports, the
reason of `(None, reason)`, or the exception risk.load lets through. `rust` says what lab-live must do:
- "same": the same Risk (every float to the bit) or the same reason text;
- "refused": the text is outside lab-live's YAML subset, and lab-live refuses it with its own reason (OWN, fail closed,
  D1). Python's result is kept for the record: Python may accept such a file (`1e9`, a block list, a duplicate key),
  refuse it with a YAML error, or even raise (a families list mixing a number and a word; a file that is not UTF-8).

The subset (lab/live/src/risk.rs): `key: value` lines at column 0, blank lines, `#` comments (at a line's start, or after
a space), LF or CRLF line ends and a leading BOM; no tab, no control character, no other YAML line break (a lone CR, NEL,
LS, PS). A value is an int (`[-+]?(0|[1-9][0-9]*)`), a decimal without exponent
(`[-+]?[0-9]+\\.[0-9]*` or the unsigned `\\.[0-9]+`), a YAML 1.1 bool word, a plain word (`[A-Za-z][A-Za-z0-9_]*`, not a bool, null or
inf/nan word), or a flow list `[a, b]` of plain or quoted words. Everything else is refused, never interpreted.
"""

import json
import os
import sys
import tempfile

from paper import risk as R

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "c3_risk_load_cases.json")
PAPER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OWN = "risk config unreadable: outside lab-live's YAML subset"
FORMAT = ("cases: {name, kind: text|missing|dir|notdir, text (kind text), rust: same|refused, python: {risk: {size_sol, "
          "max_open_positions, max_exposure_sol, daily_loss_limit_sol, kill_switch, families, pool_cooldown_h, "
          "size_lamports}} | {reason} (same) or {record: text} (refused)}; floats as Python's repr")
BASE = ("size_sol: 0.5\nmax_open_positions: 5\nmax_exposure_sol: 2.5\ndaily_loss_limit_sol: 1.0\nkill_switch: false\n"
        "families: [F7]\npool_cooldown_h: 24\n")
KEYS = ("size_sol", "max_open_positions", "max_exposure_sol", "daily_loss_limit_sol", "kill_switch", "families")
BOOLS = ("yes", "Yes", "YES", "no", "No", "NO", "true", "True", "TRUE", "false", "False", "FALSE", "on", "On", "ON",
         "off", "Off", "OFF")


def _set(key, value, text=BASE):
    """BASE with one key's line set to `key: value` (None: the line removed)."""
    lines = [ln for ln in text.split("\n") if not ln.startswith(key + ":")]
    if value is not None:
        lines.insert(0, f"{key}: {value}")
    return "\n".join(lines)


def _file(name):
    with open(os.path.join(PAPER, name)) as f:
        return f.read()


def cases():
    """(name, kind, text, rust) in a fixed order."""
    same, refused = "same", "refused"
    c = [("risk_yaml", "text", _file("risk.yaml"), same),  # the paper start's files (c1e36a0)
         ("risk_h_yaml", "text", _file("risk-h.yaml"), same),
         ("base", "text", BASE, same),
         ("cooldown_absent_is_0", "text", _set("pool_cooldown_h", None), same),
         ("ints_for_floats", "text", _set("size_sol", 1, _set("max_exposure_sol", 3, _set("daily_loss_limit_sol", 1))), same),
         ("int_above_2_53_rounds_half_even", "text", _set("max_exposure_sol", 9007199254740993), same),
         ("int_beyond_i64_for_a_float", "text", _set("max_exposure_sol", 123456789012345678901234567), same),
         ("signs_and_decimal_forms", "text", _set("size_sol", "+0.5", _set("max_exposure_sol", "2.", _set("daily_loss_limit_sol", ".5",
                                                                                                         _set("pool_cooldown_h", "00.5")))), same),
         ("cooldown_minus_zero", "text", _set("pool_cooldown_h", "-0.0"), same),
         ("size_lamports_ties_to_even_2", "text", _set("size_sol", "0.0000000025"), same),
         ("size_lamports_ties_to_even_4", "text", _set("size_sol", "0.0000000035"), same),
         ("size_lamports_0", "text", _set("size_sol", "0.0000000005"), same),
         ("float_for_int_truncates", "text", _set("max_open_positions", "5.7"), same),
         ("float_for_int_truncates_to_0", "text", _set("max_open_positions", "0.9"), same),
         ("bool_for_float", "text", _set("size_sol", "yes", _set("pool_cooldown_h", "no")), same),
         ("bool_for_int", "text", _set("max_open_positions", "On"), same),
         ("families_spaced_and_quoted", "text", _set("families", "[ \"F7\" , 'F7' ,F7 ]"), same),
         ("families_empty", "text", _set("families", "[]"), same),
         ("families_unknown", "text", _set("families", "[F8, F7, A, 'b_2', \"\"]"), same),
         ("families_word_is_its_chars", "text", _set("families", "F7"), same),
         ("families_int", "text", _set("families", "7"), same),
         ("families_float", "text", _set("families", "0.5"), same),
         ("families_bool", "text", _set("families", "yes"), same),
         ("unknown_keys_ignored", "text", BASE + "note: hello\nextra: [a, B_1]\nx: -1.5  # no matter\ny: off\n", same),
         ("comments_blank_lines_crlf_bom", "text", "﻿# risk\r\n\r\n" + BASE.replace("\n", "  # c\r\n"), same),
         ("comment_indented", "text", "   # indented comment\n" + BASE, same),
         ("non_ascii_comment", "text", "# één limiet — € ok\n" + BASE, same),
         ("empty_file", "text", "", same),
         ("only_comments", "text", "# nothing\n\n   # here\n", same),
         ("word_for_float", "text", _set("size_sol", "half"), same),
         ("word_for_int", "text", _set("max_open_positions", "five"), same),
         ("first_error_in_argument_order", "text", _set("size_sol", "abc", _set("max_open_positions", None)), same),
         ("kill_switch_int", "text", _set("kill_switch", "1"), same),
         ("kill_switch_word", "text", _set("kill_switch", "maybe"), same),
         ("kill_switch_list", "text", _set("kill_switch", "[]"), same),
         ("families_checked_before_kill_switch", "text", _set("kill_switch", "1", _set("families", "[F8]")), same),
         ("limits_checked_before_families", "text", _set("size_sol", "0", _set("families", "[F8]")), same),
         ("size_equal_to_exposure", "text", _set("size_sol", "2.5"), same),
         ("size_above_exposure", "text", _set("size_sol", "2.6"), same),
         ("size_0", "text", _set("size_sol", "0"), same),
         ("size_negative", "text", _set("size_sol", "-0.5"), same),
         ("max_open_0", "text", _set("max_open_positions", "0"), same),
         ("daily_loss_0", "text", _set("daily_loss_limit_sol", "0.0"), same),
         ("daily_loss_negative", "text", _set("daily_loss_limit_sol", "-1"), same),
         ("cooldown_negative", "text", _set("pool_cooldown_h", "-1"), same),
         ("missing_file", "missing", None, same),
         ("a_directory", "dir", None, same),
         ("a_path_through_a_file", "notdir", None, same),
         ("unknown_key_of_64_characters", "text", BASE + "a" * 64 + ": 1\n", same)]
    c += [(f"kill_switch_{w}", "text", _set("kill_switch", w), same) for w in BOOLS]
    c += [(f"missing_{k}", "text", _set(k, None), same) for k in KEYS]
    c += [("missing_two_names_the_first", "text", _set("kill_switch", None, _set("max_exposure_sol", None)), same)]
    # Outside the subset: refused by lab-live whatever Python makes of it.
    c += [("exponent_no_dot", "text", _set("size_sol", "1e9"), refused),
          ("exponent_after_dot", "text", _set("max_exposure_sol", "2.5e+0"), refused),
          ("signed_dot_decimal_for_an_int", "text", _set("max_open_positions", "-.5"), refused),  # a string to PyYAML
          ("signed_dot_decimal_for_the_families", "text", _set("families", "+.5"), refused),
          ("octal_looking_int", "text", _set("max_open_positions", "010"), refused),
          ("leading_zero_int", "text", _set("max_open_positions", "09"), refused),
          ("int_beyond_i64", "text", _set("max_open_positions", "99999999999999999999"), refused),
          ("inf_word", "text", _set("max_exposure_sol", "inf"), refused),
          ("nan_word", "text", _set("pool_cooldown_h", "NaN"), refused),
          ("null_word", "text", _set("size_sol", "Null"), refused),
          ("empty_value", "text", _set("size_sol", ""), refused),
          ("quoted_scalar", "text", _set("kill_switch", "\"false\""), refused),
          ("hash_without_space", "text", _set("size_sol", "0.5#c"), refused),
          ("duplicate_key", "text", BASE + "size_sol: 0.7\n", refused),
          ("block_list", "text", _set("families", None) + "\nfamilies:\n  - F7\n", refused),
          ("trailing_comma", "text", _set("families", "[F7,]"), refused),
          ("list_item_number", "text", _set("families", "[7]"), refused),
          ("list_item_null", "text", _set("families", "[F7, NULL]"), refused),
          ("list_mixing_number_and_word", "text", _set("families", "[F8, 7]"), refused),  # Python's risk.load raises
          ("nested_list", "text", _set("families", "[[F7]]"), refused),
          ("flow_mapping", "text", BASE + "extra: {a: 1}\n", refused),
          ("unclosed_list", "text", _set("families", "[F7"), refused),
          ("tab", "text", _set("size_sol", "\t0.5").replace("size_sol: \t", "size_sol:\t"), refused),
          ("no_space_after_colon", "text", _set("size_sol", None) + "size_sol:0.5\n", refused),
          ("space_before_colon", "text", _set("size_sol", None) + "size_sol : 0.5\n", refused),
          ("indented_keys", "text", "  " + BASE.replace("\n", "\n  "), refused),
          ("document_marker", "text", "---\n" + BASE, refused),
          ("unknown_key_outside_the_subset", "text", BASE + "x: 1e9\n", refused),
          ("not_a_mapping", "text", "hello\n", refused),
          ("control_character_in_a_comment", "text", "# bell \x07\n" + BASE, refused),
          ("lone_carriage_return", "text", BASE.replace("\n", "\r"), refused),
          ("next_line_character", "text", "# a\x85b\n" + BASE, refused),
          # review of #383: a key PyYAML refuses (over 1024 characters), and one this reader caps (over 64)
          ("unknown_key_of_65_characters", "text", BASE + "a" * 65 + ": 1\n", refused),
          ("unknown_key_of_1025_characters", "text", BASE + "a" * 1025 + ": 1\n", refused),
          # line breaks only PyYAML (or Python's universal newlines) sees: a key hidden after a comment
          ("cr_hides_a_key_after_a_comment", "text", BASE.replace("kill_switch: false", "kill_switch: false # x\rkill_switch: true"), refused),
          ("ls_hides_a_key_after_a_comment", "text", BASE.replace("size_sol: 0.5", "size_sol: 0.5 # x\u2028size_sol: 2.0"), refused),
          ("ps_hides_a_key_after_a_comment", "text", BASE.replace("size_sol: 0.5", "size_sol: 0.5 # x\u2029size_sol: 2.0"), refused),
          # numbers this reader does not carry: an int key beyond 64 bits, an infinite decimal, a size beyond 64 bits of
          # lamports (Python's size_lamports raises on an infinite size, and pool_cooldown_h inf would raise in Clock.slots)
          ("decimal_beyond_64_bits_for_an_int", "text", _set("max_open_positions", "1" + "0" * 30 + ".0"), refused),
          ("decimal_that_is_infinite", "text", _set("size_sol", "1" + "0" * 400 + ".0", _set("max_exposure_sol", "1" + "0" * 400 + ".0")), refused),
          ("cooldown_that_is_infinite", "text", _set("pool_cooldown_h", "1" + "0" * 400 + ".0"), refused),
          ("size_beyond_64_bits_of_lamports", "text", _set("size_sol", "10000000000", _set("max_exposure_sol", "20000000000")), refused),
          ("a_list_for_a_number", "text", _set("size_sol", "[F7]"), refused)]
    return c


def run(kind, text):
    """Python's risk.load on the case: {risk: …}, {reason: …} or {raises: …}."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "risk.yaml")
        if kind == "text":
            with open(path, "w", newline="") as f:
                f.write(text)
        elif kind == "dir":
            os.mkdir(path)
        elif kind == "notdir":  # a path through a regular file
            with open(path, "w"):
                pass
            path = os.path.join(path, "risk.yaml")
        try:
            risk, reason = R.load(path)
            if risk is None:
                return {"reason": reason}
            out = {k: getattr(risk, k) for k in ("size_sol", "max_open_positions", "max_exposure_sol", "daily_loss_limit_sol",
                                                 "kill_switch", "pool_cooldown_h")}
            out["families"] = list(risk.families)
            out["size_lamports"] = risk.size_lamports  # raises on an infinite size (the engine would, at its first use)
            return {"risk": out}
        except Exception as e:  # noqa: BLE001 - recorded: lab-live refuses these files
            return {"raises": f"{e.__class__.__name__}: {e}"}


def record(result):
    """A refused case's Python result as text, for the record (it may hold inf or nan, which JSON cannot)."""
    if "risk" in result:
        return "Risk(" + ", ".join(f"{k}={v!r}" for k, v in result["risk"].items()) + ")"
    return next(f"{k}: {v}" for k, v in result.items())


def table():
    rows = []
    for name, kind, text, rust in cases():
        result = run(kind, text)
        assert rust == "refused" or "raises" not in result, (name, result)  # a same case is a Risk or a reason
        row = {"name": name, "kind": kind, "rust": rust, "python": result if rust == "same" else {"record": record(result)}}
        if kind == "text":
            row["text"] = text
        rows.append(row)
    names = [r["name"] for r in rows]
    assert len(set(names)) == len(names), "case names must be unique"
    return {"format": FORMAT, "own_reason_prefix": OWN, "cases": rows}


def text():
    return json.dumps(table(), indent=1, allow_nan=False) + "\n"


def generate():
    with open(PATH, "w") as f:
        f.write(text())


if __name__ == "__main__":
    generate()
    print(f"wrote {PATH}", file=sys.stderr)
