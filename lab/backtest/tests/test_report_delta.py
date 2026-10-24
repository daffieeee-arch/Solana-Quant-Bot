"""report_delta (Codex review #5's side-by-side tables): changed leaves with old and new, list elements named by their
cell fields, day series by their largest difference, and the gate that nothing without limits may move."""

import json

from backtest import report_delta as rd


def cell(size, unl, lim, series):
    return {"candidate": "H5", "size": size, "d": 3, "scenario": "base", "unlimited": {"sol_day": unl},
            "limited": [{"mapping": "5%", "unsold": "deadline", "capital": size * 20, "skip_daily_limit": lim[0], "sol_day": lim[1]}],
            "series": series}


def test_only_the_limited_rows_moved_is_green(tmp_path):
    old = {"plan": "p", "cells": [cell(0.5, 1.0, (3, 0.2), [1.0, 2.0]), cell(1.25, 2.0, (0, 0.5), [0.0, 0.0])]}
    new = {"plan": "q", "cells": [cell(0.5, 1.0, (1, 0.3), [1.0, 2.5]), cell(1.25, 2.0, (0, 0.5), [0.0, 0.0])]}
    res = rd.diff(old, new)
    paths = {"/".join(p): (o, n, d) for p, o, n, d in res["rows"]}
    key = "cells/[candidate=H5,size=0.5,d=3,scenario=base]/limited/[mapping=5%,unsold=deadline,capital=10.0]"
    assert paths[key + "/skip_daily_limit"] == (3, 1, -2)
    assert abs(paths[key + "/sol_day"][2] - 0.1) < 1e-12
    assert paths["cells/[candidate=H5,size=0.5,d=3,scenario=base]/series"][2] == "max |d| 0.5"
    assert res["changed"] == 3 and res["red_unchanged"] == []  # plan is skipped; the 1.25 cell did not move
    for name, x in (("old", old), ("new", new)):
        (tmp_path / f"{name}.json").write_text(json.dumps(x))
    assert rd.main([str(tmp_path / "old.json"), str(tmp_path / "new.json"), "--label", "E63", "--out", str(tmp_path / "d")]) == 0
    assert "skip_daily_limit | 3 | 1 | -2 |" in (tmp_path / "d" / "delta-E63.md").read_text()


def test_a_moved_row_without_limits_is_red(tmp_path):
    old = {"cells": [cell(0.5, 1.0, (3, 0.2), [1.0])]}
    new = {"cells": [cell(0.5, 1.1, (3, 0.2), [1.0])]}
    res = rd.diff(old, new)
    assert res["red_unchanged"] == ["cells/[candidate=H5,size=0.5,d=3,scenario=base]/unlimited/sol_day"]
    for name, x in (("old", old), ("new", new)):
        (tmp_path / f"{name}.json").write_text(json.dumps(x))
    assert rd.main([str(tmp_path / "old.json"), str(tmp_path / "new.json"), "--label", "E70", "--out", str(tmp_path / "d")]) == 1
