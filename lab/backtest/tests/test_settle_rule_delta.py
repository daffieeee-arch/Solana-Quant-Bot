"""settle_rule_delta (Codex review #6, B2): rule 2 changes exactly the rows whose last counted exit attempt failed, by one
failed_tx each; the clean sources are admitted only as locked (SHA256SUMS, settle_rule 1, E20 reproduced bit for bit)."""

import hashlib
import json
import os

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backtest import settle_rule_delta as D
from backtest.costs import Scenario, settle_sql
from backtest.store import load_defaults

SOL = 10**9
BASE = dict(family="F7", segment="pool_reversed", variant="tp4_T600", size_sol=0.5, d=1, q=0.5, overlay="none", farm="organic",
            skipped=False, entry_failed=False, cost=SOL // 2, proceeds=0, exit_attempts=1, exit_slot=None, exit_reason="time",
            trigger_trader="W")
ROWS = [  # (changes under rule 2)
    (dict(t=1, pool="A", day="2026-10-01", proceeds=SOL // 2, exit_slot=10), False),  # sold at once
    (dict(t=2, pool="B", day="2026-10-01", proceeds=SOL // 2, exit_attempts=3, exit_slot=20, exit_reason="take_profit"), False),  # sold after 2 misses
    (dict(t=3, pool="C", day="2026-10-01", exit_attempts=2), True),  # unsold after 2 failed attempts
    (dict(t=4, pool="D", day="2026-10-02", exit_attempts=1, exit_slot=40, exit_reason="written_off"), True),  # a write-off
    (dict(t=5, pool="E", day="2026-10-02", exit_attempts=0, exit_reason="end_of_data"), False),  # no attempt at all
    (dict(t=6, pool="F", day="2026-10-02", entry_failed=True, cost=0, exit_attempts=0, exit_reason="entry_slippage"), False),
    (dict(t=7, pool="G", day="2026-10-02", skipped=True, cost=0, exit_attempts=0, exit_reason="no_fill"), False),  # not settled
]


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def parts(d, rows):
    os.makedirs(d, exist_ok=True)
    pq.write_table(pa.Table.from_pylist([dict(BASE, **r) for r in rows]), os.path.join(d, "part-0.parquet"))


def run(tmp_path, *argv):
    return D.main(["--out", str(tmp_path / "out"), *argv])


def test_dev_rule_2_charges_one_failed_tx_per_unfilled_row(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path / "spill"))
    parts(tmp_path / "run" / "parts", [r for r, _ in ROWS])
    assert run(tmp_path, "--run", f"dev={tmp_path / 'run' / 'parts'}") == 0
    res = json.load(open(tmp_path / "out" / "delta.json"))
    (cell,) = res["sources"]["dev dev"]["cells"]
    cfg = load_defaults()
    assert (cell["n"], cell["changed"], cell["days"]) == (6, sum(c for _, c in ROWS), 2)
    for s in ("base", "pessimistic"):
        f = Scenario.from_cfg(s, cfg).failed_tx
        assert cell[s]["delta_sol"] == pytest.approx(-2 * f / 1e9, abs=1e-15) and res["failed_tx"][s] == f
        assert cell[s]["delta_sol_per_day"] == pytest.approx(-f / 1e9, abs=1e-15)
    assert (tmp_path / "out" / "delta.md").exists() and not (tmp_path / "out.tmp").exists()
    assert run(tmp_path, "--run", f"dev={tmp_path / 'run' / 'parts'}") == 2  # an existing out is refused


def clean_run(tmp_path, rule=None):
    run_dir = tmp_path / "h4-165bb04"
    parts(run_dir / "result" / "h4-parts", [r for r, _ in ROWS])
    res = {"H4": {"decision": "x"}} | ({"settle_rule": rule} if rule else {})
    json.dump(res, open(run_dir / "result" / "result.json", "w"))
    with open(run_dir / "SHA256SUMS", "w") as f:
        f.write(f"{sha(run_dir / 'result' / 'result.json')}  result/result.json\n")
    return run_dir


def test_clean_h_admits_only_the_locked_result(tmp_path, capsys):
    run_dir = clean_run(tmp_path)
    assert run(tmp_path, "--clean", f"H4={run_dir / 'result' / 'result.json'}") == 0
    src = json.load(open(tmp_path / "out" / "delta.json"))["sources"]["clean H4"]
    assert (src["rows"], src["changed"], src["provenance"]["settle_rule"]) == (6, 2, 1)
    (tmp_path / "out").rename(tmp_path / "out1")
    json.dump({"H4": {"decision": "y"}}, open(run_dir / "result" / "result.json", "w"))  # not the listed result.json
    assert run(tmp_path, "--clean", f"H4={run_dir / 'result' / 'result.json'}") == 2
    assert "is not the one" in capsys.readouterr().err and not (tmp_path / "out").exists()
    run_dir = clean_run(tmp_path / "r2", rule=2)  # a result settled under rule 2 is no frozen result
    assert run(tmp_path, "--clean", f"H4={run_dir / 'result' / 'result.json'}") == 2
    assert run(tmp_path, "--clean", f"H9={run_dir / 'result' / 'result.json'}") == 2


def e20_fixture(tmp_path, k3_rows=None, extra_parts=()):
    test_run = tmp_path / "clean-run"
    parts(test_run / "parts", [r for r, _ in ROWS] + [dict(t=1, pool="A", day="2026-10-01", variant="tp6_T600", proceeds=SOL)]
          + list(extra_parts))  # another variant of the same trigger: not K3
    with open(test_run / "config.json", "w") as f:
        f.write('{"run": "test"}')
    k3_dir = tmp_path / "k3-rows"
    os.makedirs(k3_dir)
    pnl, ret = settle_sql(Scenario.from_cfg("base", load_defaults()), 1)
    con = duckdb.connect()
    con.execute(f"""COPY (SELECT pool, t, trigger_trader, entry_failed, NOT entry_failed AND {ret} IS NOT NULL AS filled,
                         t % 2 = 0 AS rt_tx, {k3_rows or pnl} / 1e9 AS net_sol, {ret} AS ret
                       FROM read_parquet('{test_run / 'parts' / '*.parquet'}') WHERE {D.E20_K3}) TO '{k3_dir / 'k3-rows.parquet'}' (FORMAT parquet)""")
    json.dump({"lock": {"test_run": str(test_run), "test_config_sha256": sha(test_run / "config.json")}}, open(k3_dir / "counts.json", "w"))
    with open(k3_dir / "SHA256SUMS", "w") as f:
        for n in ("k3-rows.parquet", "counts.json"):
            f.write(f"{sha(k3_dir / n)}  {n}\n")
    return k3_dir, test_run


def test_e20_is_reproduced_under_rule_1_before_it_is_measured(tmp_path, capsys):
    k3_dir, test_run = e20_fixture(tmp_path)
    assert run(tmp_path, "--e20", f"{k3_dir}={test_run}") == 0
    src = json.load(open(tmp_path / "out" / "delta.json"))["sources"]["clean E20 (K3)"]
    assert (src["rows"], src["changed"], src["provenance"]["rows_reproduced"]) == (6, 2, 6)
    g = src["e20_groups"]["filled"]
    assert g["n"] == 5 and g["changed"] == 2 and g["delta"] < 0
    # refused: a net that rule 1 does not reproduce (the pessimistic scenario's), a key with two K3 rows, a changed config
    for i, (kw, extra) in enumerate(((dict(k3_rows=settle_sql(Scenario.from_cfg("pessimistic", load_defaults()), 1)[0]), ()),
                                     ({}, (dict(t=3, pool="C", day="2026-10-01", d=2, exit_attempts=2),)))):
        k, t = e20_fixture(tmp_path / f"bad{i}", extra_parts=extra, **kw)
        assert D.main(["--out", str(tmp_path / f"bad{i}" / "out"), "--e20", f"{k}={t}"]) == 2
    assert "does not reproduce" in capsys.readouterr().err
    with open(test_run / "config.json", "a") as f:
        f.write(" ")
    assert D.main(["--out", str(tmp_path / "out2"), "--e20", f"{k3_dir}={test_run}"]) == 2
    assert "config.json is not the locked one" in capsys.readouterr().err
