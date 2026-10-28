"""Diagnostic (iii) (backtest.h5_dev_repro) on tiny synthetic runs: equal runs are GREEN; a changed row, a row moved to
another day, another days list and a different settlement rule are found (rows, the integer day series, the fields);
E63's side is anchored before anything is compared; step 0 is informative; the guards refuse a run compared with
itself, an input that is not its archived copy, E63's run or jsons not E63's alone, another store, changed analysis
code, LAB_CORE not python, a column only one run has, the clean bridge, a snapshot that is not the pin's or not the
run's own, and a step-2 json that is not capital_ceiling's main on the new run; the NAS script's tournament flags are
GRID and its spill dir never overlaps its evidence."""

import hashlib
import json
import os
import re
import shutil
import subprocess
from types import SimpleNamespace

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backtest import capital_ceiling as cc
from backtest import h5_dev_repro as h5
from backtest.costs import Scenario, settle
from backtest.store import load_defaults

CHECK_ENGINE = h5.check_engine  # the real one: the lab fixture stubs it
LO = cc.DEV[0]
DAY0 = np.datetime64("2026-09-12")
SPAN = 100_000  # slots per fixture day
DAY_OF = cc.SlotDays([LO + SPAN * i for i in range(6)], [str(DAY0 + np.timedelta64(i, "D")) for i in range(6)])
DAYS = DAY_OF.days[:4]
STORE_META = {"store_version": 2, "period": "dev", "slot_start": cc.DEV[0], "slot_end_exclusive": cc.DEV[1], "chunks": 1,
              "git_commit": "fixture", "built_at_unix": 1, "counts": {"blocks": 5}, "ms_per_slot": {"1033": 400.0}}
COLUMNS = [("family", pa.string()), ("variant", pa.string()), ("mint", pa.string()), ("t", pa.int64()), ("depth", pa.int64()),
           ("size_sol", pa.float64()), ("d", pa.int64()), ("tau", pa.string()), ("entry_failed", pa.bool_()),
           ("skipped", pa.bool_()), ("tokens", pa.int64()), ("cost", pa.int64()), ("proceeds", pa.int64()),
           ("exit_reason", pa.string()), ("entry_slot", pa.int64()), ("exit_decision_slot", pa.int64()),
           ("exit_slot", pa.int64()), ("vault_capped", pa.bool_()), ("exit_attempts", pa.int64()), ("day", pa.string()),
           ("pool", pa.string()), ("segment", pa.string()), ("farm", pa.string()), ("overlay", pa.string()), ("q", pa.float64()),
           ("d_ms", pa.float64())]
E63_ONLY = [("entry", pa.string()), ("capped", pa.bool_()), ("k_tx", pa.int64())]
# (pool, day index, slot offset, farm, kind, return): kind filled / failed / unsold (its last attempt failed) / skipped;
# C exits two days after its trigger (the trigger-cohort and exit-day books differ).
TRIGGERS = [("A", 0, 1_000, "organic", "filled", 0.4), ("B", 0, 2_000, "flagged", "filled", 0.2),
            ("C", 1, 1_000, "organic", "filled", 0.9), ("D", 1, 2_000, "organic", "failed", 0.0),
            ("E", 2, 1_000, "organic", "unsold", 0.0), ("F", 3, 1_000, "organic", "filled", -0.5),
            ("G", 3, 2_000, "organic", "skipped", 0.0)]


def parts_rows():
    out = []
    for size in h5.SIZES:
        for d in h5.DELAYS:
            for pool, day, off, farm, kind, ret in TRIGGERS:
                t = LO + SPAN * day + off
                cost = 0 if kind in ("failed", "skipped") else int(size * 1e9)
                exit_slot = {"filled": t + (2 * SPAN if pool == "C" else 500)}.get(kind)
                out.append({"family": "F7", "variant": h5.H5, "mint": f"m{pool}", "t": t, "depth": int(100e9), "size_sol": size,
                            "d": d, "tau": "empirical", "entry_failed": kind == "failed", "skipped": kind == "skipped",
                            "tokens": 1000, "cost": cost, "proceeds": int(cost * (1 + ret)) if kind == "filled" else 0,
                            "exit_reason": "tp" if kind == "filled" else None, "entry_slot": t + d,
                            "exit_decision_slot": t + 400, "exit_slot": exit_slot, "vault_capped": False,
                            "exit_attempts": 3 if kind == "unsold" else 1, "day": DAYS[day], "pool": pool,
                            "segment": "pool_reversed", "farm": farm, "overlay": "none", "q": 0.5, "d_ms": 400.0 * d})
    return out


def write_parts(run, rows, e63_columns):
    os.makedirs(os.path.join(run, "parts"))
    schema = pa.schema(COLUMNS + (E63_ONLY if e63_columns else []))
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), os.path.join(run, "parts", "part-000000.parquet"))


def write_run(root, name, rows, commit, variants, e63_columns=False, **extra):
    run = os.path.join(root, name)
    write_parts(run, rows, e63_columns)
    cfg = {**h5.GRID, "variants": variants, "engine_commit": commit, "store": STORE_META, "positions": len(rows),
           "slot_range": None, **extra}
    with open(os.path.join(run, "config.json"), "w") as f:
        json.dump(cfg, f)
    return run


def make_lab(root):
    """dev2 (meta, blocks a day per SPAN slots, a fee leg), E63's run and both its ceiling.jsons (capital_ceiling's main
    on it; the original's limited cells changed: E63's trigger-day book), job 87, the new run from an 'engine' dir
    and its step-2 ceiling.json, the C3 keys."""
    p = SimpleNamespace(root=root)
    os.makedirs(os.path.join(root, "store"))
    p.store = os.path.join(root, "store", "dev2.duckdb")
    con = duckdb.connect(p.store)
    con.execute("CREATE TABLE meta (json VARCHAR)")
    con.execute("INSERT INTO meta VALUES (?)", [json.dumps(STORE_META)])
    t0 = int(DAY0.astype("datetime64[s]").astype(np.int64))
    con.execute("CREATE TABLE blocks (slot UBIGINT, block_time BIGINT)")
    con.executemany("INSERT INTO blocks VALUES (?, ?)", [(LO + SPAN * i, t0 + 86_400 * i) for i in range(6)])
    con.execute("CREATE TABLE pool (pool VARCHAR, slot UBIGINT, kind VARCHAR, lp_bps INTEGER, protocol_bps INTEGER, creator_bps INTEGER)")
    con.execute("INSERT INTO pool VALUES ('A', ?, 'buy', 20, 5, 5)", [LO + 1_000])
    con.close()
    p.keys = os.path.join(root, "keys.parquet")
    pq.write_table(pa.table({"pool": ["A", "C"], "t": [LO + 1_000, LO + SPAN + 1_000], "d": [2, 2], "h": ["5m", "5m"],
                             "paid_buyers_60s": [3, 2]}), p.keys)
    rows = parts_rows()
    p.e63 = write_run(root, "capital-ceiling-d15ac45", rows, h5.E63_COMMIT, ["tp4_T600", "hold300", h5.H5],
                      e63_columns=True, venue_math="python")
    p.bigtp = write_run(root, "bigtp", rows, h5.BIGTP_COMMIT, None)
    p.engine = os.path.join(root, "new", "engine", "backtest")
    os.makedirs(p.engine)
    p.new = write_run(os.path.join(root, "new"), "h5-dev2-15fa3dd", rows, h5.PIN, [h5.H5], engine_dir=p.engine)
    assert cc.main(["--parts", p.e63, "--c3-keys", p.keys, "--store", p.store, "--out", os.path.join(root, "exitday")]) == 0
    p.exitday = os.path.join(root, "exitday", "ceiling.json")
    with open(p.exitday) as f:
        j = json.load(f)
    del j["daily_loss_book"]
    for c in j["cells"]:
        for x in c["limited"]:
            x["sol_day"] += 1.0  # the trigger-day book: not compared
    os.makedirs(os.path.join(p.e63, "ceiling"))
    with open(os.path.join(p.e63, "ceiling", "ceiling.json"), "w") as f:
        json.dump(j, f)
    p.new_json = new_json(p)
    return p


def new_json(p):
    out = os.path.join(p.root, "ceiling-new")
    shutil.rmtree(out, ignore_errors=True)
    assert cc.main(["--parts", p.new, "--c3-keys", p.keys, "--store", p.store, "--out", out]) == 0
    return os.path.join(out, "ceiling.json")


def pnl(r, scn, rule):
    """The row's P&L by costs.settle (the Python twin, not the SQL the module runs)."""
    res = SimpleNamespace(entry_failed=r["entry_failed"], cost=r["cost"], proceeds=r["proceeds"], exit_attempts=r["exit_attempts"],
                          exit_slot=r["exit_slot"], exit_reason=r["exit_reason"])
    return settle(res, scn, rule)[0]


def descriptives(rows, half):
    """descriptives.out's H5 figures of a fixture's rows, by costs.settle under rule 1."""
    scn = Scenario.from_cfg("base", load_defaults())
    out = {}
    for size in (0.5, 5.0):
        r = [x for x in rows if x["size_sol"] == size and x["d"] == 3 and x["farm"] == "organic" and not x["skipped"]]
        p = [(x["day"], pnl(x, scn, 1)) for x in r]
        out[size] = (round(sum(v for _, v in p) / 1e9, 1), len(r), len({x["pool"] for x in r}),
                     round(sum(v for d, v in p if d < half) / 1e9 / 10, 2), round(sum(v for d, v in p if d >= half) / 1e9 / 10, 2))
    return out


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """A fresh lab (its configs hold its own paths) with the module's archived constants set to its files."""
    bind_fixture_analysis(monkeypatch)
    p = make_lab(str(tmp_path))
    monkeypatch.setattr(h5, "HALF", DAYS[2])
    monkeypatch.setattr(h5, "DESCRIPTIVES", descriptives(parts_rows(), DAYS[2]))
    monkeypatch.setattr(h5, "check_engine", lambda d: "stub")  # the snapshot check: test_the_snapshot_must_be_the_pins_tree
    shas(p, monkeypatch)
    return p


def bind_fixture_analysis(monkeypatch):
    """Synthetic runs use this checkout's analysis; this never changes the archived production pins."""
    here = os.path.dirname(cc.__file__)
    monkeypatch.setattr(h5, "ANALYSIS", {name: h5.git_blob(os.path.join(here, name)) for name in h5.ANALYSIS})


def shas(p, monkeypatch):
    monkeypatch.setattr(h5, "SHA256", {**h5.SHA256, "e63_config": h5.sha256(os.path.join(p.e63, "config.json")),
                                       "e63_json": h5.sha256(os.path.join(p.e63, "ceiling", "ceiling.json")),
                                       "e63_exitday_json": h5.sha256(p.exitday), "c3_keys": h5.sha256(p.keys),
                                       "bigtp_config": h5.sha256(os.path.join(p.bigtp, "config.json"))})


def argv(p, mode="full", out="out", new=None, extra=()):
    a = ["compare", "--mode", mode, "--new", new or (p.new if mode == "full" else p.bigtp), "--e63", p.e63,
         "--e63-exitday-json", p.exitday, "--c3-keys", p.keys, "--store", p.store, "--out", os.path.join(p.root, out)]
    if mode == "full":
        a += ["--engine-dir", p.engine, "--new-json", p.new_json]
    return a + list(extra)


def result(p, out="out"):
    with open(os.path.join(p.root, out, "repro.json")) as f:
        o = json.load(f)
    assert o["scope"] == h5.SCOPE  # the archive carries the GREEN's scope verbatim (LAB nit on #585), in every mode
    return o


def rewrite(run, change):
    """The run's parts with change(rows) applied (its config's positions kept)."""
    f = os.path.join(run, "parts", "part-000000.parquet")
    t = pq.read_table(f)
    rows = t.to_pylist()
    change(rows)
    pq.write_table(pa.Table.from_pylist(rows, schema=t.schema), f)


def find(rows, pool, size=0.5, d=2):
    return next(r for r in rows if (r["pool"], r["size_sol"], r["d"]) == (pool, size, d))


def edit_json(path, change):
    """The JSON file with change(j) applied in place."""
    with open(path) as f:
        j = json.load(f)
    change(j)
    with open(path, "w") as f:
        json.dump(j, f)


def h5_cell(j, size=0.5, d=2, scen="base"):
    return next(c for c in j["cells"] if (c["candidate"], c["size"], c["d"], c["scenario"]) == ("H5", size, d, scen))


def test_equal_runs_are_green(lab):
    assert h5.main(argv(lab)) == 0
    o = result(lab)
    assert o["verdict"] == "GREEN" and o["reasons"] == [] and o["settle_rule"] == 1
    assert len(o["cells"]) == len(h5.SIZES) * len(h5.DELAYS)
    assert all(c["rows"]["only_new"] == c["rows"]["only_e63"] == 0 and c["rows"]["new_rows"] == len(TRIGGERS) for c in o["cells"])
    assert all(v == [] for v in o["json"].values())
    # The day series of both sides are written, the same lines on each side.
    with open(os.path.join(lab.root, "out", "days.csv")) as f:
        lines = f.read().splitlines()[1:]
    new = sorted(x.split(",", 1)[1] for x in lines if x.startswith("new,"))
    assert new and new == sorted(x.split(",", 1)[1] for x in lines if x.startswith("e63,"))


def test_a_changed_row_is_red_on_its_day_by_its_settled_difference(lab):
    rewrite(lab.new, lambda rows: find(rows, "A").update(proceeds=find(rows, "A")["proceeds"] + 1_000_000))
    lab.new_json = new_json(lab)
    assert h5.main(argv(lab)) == 1
    o = result(lab)
    c = next(c for c in o["cells"] if (c["size"], c["d"]) == (0.5, 2))
    assert (c["rows"]["only_new"], c["rows"]["only_e63"]) == (1, 1)
    assert [x["columns"] for x in c["rows"]["pairs"]] == [{"proceeds": [find(parts_rows(), "A")["proceeds"] + 1_000_000,
                                                                        find(parts_rows(), "A")["proceeds"]]}]
    scn = Scenario.from_cfg("base", load_defaults())
    a = find(parts_rows(), "A")
    want = pnl({**a, "proceeds": a["proceeds"] + 1_000_000}, scn, 1) - pnl(a, scn, 1)
    assert want > 0 and c["scenarios"]["base"]["day_unlimited"] == {"max_abs_lamports": want, "days_differing": {DAYS[0]: want}}
    others = [x for x in o["cells"] if (x["size"], x["d"]) != (0.5, 2)]
    assert all(x["rows"]["only_new"] == 0 and x["scenarios"]["base"]["day_unlimited"]["max_abs_lamports"] == 0 for x in others)
    assert o["json"]["exitday_cells"] and o["verdict"] == "RED"


def test_a_row_moved_to_another_day_is_red_though_the_totals_agree(lab):
    rewrite(lab.new, lambda rows: find(rows, "F", 1.25, 3).update(day=DAYS[2]))
    lab.new_json = new_json(lab)
    assert h5.main(argv(lab)) == 1
    c = next(c for c in result(lab)["cells"] if (c["size"], c["d"]) == (1.25, 3))
    assert [list(x["columns"]) for x in c["rows"]["pairs"]] == [["day"]]
    du = c["scenarios"]["base"]["day_unlimited"]["days_differing"]
    assert sorted(du) == [DAYS[2], DAYS[3]] and sum(du.values()) == 0 and du[DAYS[2]] < 0


def test_a_missing_skipped_row_is_found_by_the_rows_alone(lab):
    # G is skipped: no P&L, no day series, no field moves; only the row comparison sees it (and only on E63's side).
    rewrite(lab.new, lambda rows: rows.remove(find(rows, "G", 2.5, 2)))
    cfg = h5.config(lab.new)
    with open(os.path.join(lab.new, "config.json"), "w") as f:
        json.dump({**cfg, "positions": cfg["positions"] - 1}, f)
    lab.new_json = new_json(lab)
    assert h5.main(argv(lab)) == 1
    o = result(lab)
    c = next(c for c in o["cells"] if (c["size"], c["d"]) == (2.5, 2))
    assert (c["rows"]["only_new"], c["rows"]["only_e63"]) == (0, 1) and c["rows"]["only_e63_rows"][0]["pool"] == "G"
    assert o["reasons"] == ["2.5 SOL d 2: rows 0 only new / 1 only E63"]


def test_e63_is_settled_under_rule_1(lab):
    """Main's default is rule 2; the series compared with E63 settle every row under rule 1: E's last exit attempt
    failed (unsold, 3 attempts), so rule 1 charges 2 failed fees and rule 2 three."""
    con = duckdb.connect()
    cc.load(con, [lab.e63], lab.keys)
    day_of = DAY_OF
    _, series, _ = h5.cell(con, 0.5, 3, "base", DAYS, day_of)
    scn = Scenario.from_cfg("base", load_defaults())
    e = find(parts_rows(), "E", 0.5, 3)
    assert series["unlimited"][DAYS[2]] == pnl(e, scn, 1) == pnl(e, scn, 2) + scn.failed_tx


def test_a_settle_rule_other_than_1_is_refused(lab, monkeypatch):
    monkeypatch.setattr(cc, "SETTLE_RULE_E63", 2)
    with pytest.raises(cc.Refused, match="rule 1"):
        h5.main(argv(lab))


def test_step0_is_informative(lab):
    assert h5.main(argv(lab, "step0", out="s0")) == 0
    assert [(c["size"], c["d"]) for c in result(lab, "s0")["cells"]] == [h5.STEP0_CELL]
    rewrite(lab.bigtp, lambda rows: find(rows, "C").update(exit_slot=find(rows, "C")["exit_slot"] + 1))
    assert h5.main(argv(lab, "step0", out="s1")) == 3
    o = result(lab, "s1")
    assert o["verdict"] == "DIFF (informative)" and "informative only" in open(os.path.join(lab.root, "s1", "repro.md")).read()


def test_e63s_side_is_anchored_before_anything_is_compared(lab, monkeypatch):
    with open(lab.exitday) as f:
        j = json.load(f)
    j["cells"][0]["limited"][3]["taken"] += 1
    with open(lab.exitday, "w") as f:
        json.dump(j, f)
    shas(lab, monkeypatch)
    with pytest.raises(cc.Refused, match="not E63's series"):
        h5.main(argv(lab, out="a1"))
    assert not os.path.exists(os.path.join(lab.root, "a1", "repro.json"))


def test_the_descriptives_anchor_reads_the_day_series(lab, monkeypatch):
    total, n, pools, first, second = h5.DESCRIPTIVES[5.0]
    monkeypatch.setattr(h5, "DESCRIPTIVES", {**h5.DESCRIPTIVES, 5.0: (total, n, pools, round(first + 0.01, 2), second)})
    with pytest.raises(cc.Refused, match="descriptives"):
        h5.main(argv(lab))


def test_the_without_limits_fields_of_e63s_own_json_are_anchored(lab, monkeypatch):
    with open(os.path.join(lab.e63, "ceiling", "ceiling.json")) as f:
        j = json.load(f)
    j["cells"][1]["unlimited"]["filled"] += 1
    with open(os.path.join(lab.e63, "ceiling", "ceiling.json"), "w") as f:
        json.dump(j, f)
    shas(lab, monkeypatch)
    with pytest.raises(cc.Refused, match="ceiling.json"):
        h5.main(argv(lab))


def test_a_run_compared_with_itself_is_refused(lab):
    with pytest.raises(cc.Refused, match="compared with itself"):
        h5.main(argv(lab, new=lab.e63))
    cfg = h5.config(lab.new)
    with open(os.path.join(lab.new, "config.json"), "w") as f:
        json.dump({**cfg, "engine_commit": h5.E63_COMMIT}, f)
    with pytest.raises(cc.Refused, match="engine"):
        h5.main(argv(lab))


def test_a_new_run_that_is_not_h5_alone_or_not_e63s_grid_is_refused(lab):
    cfg = h5.config(lab.new)
    with open(os.path.join(lab.new, "config.json"), "w") as f:
        json.dump({**cfg, "delays": [2]}, f)
    with pytest.raises(cc.Refused, match="flags"):
        h5.main(argv(lab, out="g1"))
    with open(os.path.join(lab.new, "config.json"), "w") as f:
        json.dump(cfg, f)
    rewrite(lab.new, lambda rows: rows[0].update(variant="tp4_T600"))
    with pytest.raises(cc.Refused, match="alone"):
        h5.main(argv(lab, out="g2"))


def test_a_column_only_one_run_has_must_be_null(lab):
    rewrite(lab.e63, lambda rows: rows[5].update(k_tx=2))
    with pytest.raises(cc.Refused, match="k_tx"):
        h5.main(argv(lab))


def test_another_store_or_changed_analysis_code_is_refused(lab, monkeypatch):
    con = duckdb.connect(lab.store)
    con.execute("UPDATE meta SET json = ?", [json.dumps({**STORE_META, "git_commit": "rebuilt"})])
    con.close()
    with pytest.raises(cc.Refused, match="git_commit"):
        h5.main(argv(lab))
    monkeypatch.setattr(h5, "ANALYSIS", {**h5.ANALYSIS, "costs.py": "0" * 40})
    with pytest.raises(cc.Refused, match="analysis code"):
        h5.check_analysis()


def test_the_clean_bridge_is_refused(lab):
    with pytest.raises(cc.Refused, match="LAB research"):
        h5.main(argv(lab, extra=["--clean-bridge"]))


def test_a_range_run_is_a_dry_run_only(lab):
    # A RANGE run of the first two days: its rows and positions only.
    rewrite(lab.new, lambda rows: rows.__setitem__(slice(None), [r for r in rows if r["t"] < LO + 2 * SPAN]))
    cfg = h5.config(lab.new)
    with open(os.path.join(lab.new, "config.json"), "w") as f:
        json.dump({**cfg, "slot_range": [LO, LO + 2 * SPAN], "positions": h5.parts_rows(lab.new)[1]}, f)
    with pytest.raises(cc.Refused, match="slot_range"):
        h5.main(argv(lab, out="d0"))
    assert h5.main(argv(lab, out="d1", extra=["--dry-run"])) == 0
    o = result(lab, "d1")
    assert o["verdict"].startswith("DRY RUN") and o["days"] == DAYS[:2] and "json" not in o


def test_limited_days_keeps_limiteds_own_capital_days_call(lab, monkeypatch):
    con = duckdb.connect()
    cc.load(con, [lab.e63], lab.keys)
    day_of = DAY_OF
    rows = cc.cell_rows(con, h5.H5, "organic", False, 0.5, 2, "base")
    res, trig, exit_day = h5.limited_days(rows, DAYS, 10.0, 0.5, "deadline", day_of)
    assert res == cc.limited(rows, DAYS, 10.0, 0.5, "deadline", day_of)
    assert abs(np.mean([trig.get(x, 0) / 1e9 for x in DAYS]) - res["sol_day"]) < 1e-12
    # C (trigger day 1) is realized on day 3: the two books differ.
    assert trig != exit_day and sum(trig.values()) == sum(exit_day.values()) and DAYS[1] in trig and DAYS[3] in exit_day
    real = cc.capital_days
    def boom(*a):
        raise RuntimeError("boom")

    monkeypatch.setattr(cc, "limited", boom)
    with pytest.raises(RuntimeError):
        h5.limited_days(rows, DAYS, 10.0, 0.5, "deadline", day_of)
    assert cc.capital_days is real


def test_cells_are_capital_ceilings_own(lab):
    con = duckdb.connect()
    cc.load(con, [lab.e63], lab.keys)
    day_of = DAY_OF
    _, cells = cc.compute(con, False, day_of)
    mine = h5.side(con, [(s, d) for s in h5.SIZES for d in h5.DELAYS], DAYS, day_of)
    want = {(c["size"], c["d"], c["scenario"]): json.loads(json.dumps(c)) for c in cells if c["candidate"] == "H5"}
    assert {k: v[0] for k, v in mine.items()} == want


def test_lamports_must_be_whole():
    assert h5.lamports({"x": 5.0, "y": -3.0}) == {"x": 5, "y": -3}
    with pytest.raises(cc.Refused, match="exact"):
        h5.lamports({"x": 5.5})
    with pytest.raises(cc.Refused, match="exact"):
        h5.lamports({"x": float(2 ** 53)})


def test_field_diffs_are_exact():
    assert h5.field_diffs({"a": 1.0, "b": [1, 2]}, {"a": 1.0, "b": [1, 2]}) == []
    assert h5.field_diffs({"a": 1.0}, {"a": 1.0 + 1e-15}) == [["a", 1.0, 1.0 + 1e-15]]
    assert h5.field_diffs({"a": True}, {"a": 1}) == [["a", True, 1]]
    assert h5.field_diffs({"a": 1}, {"b": 1}) == [["a", 1, "<missing>"], ["b", "<missing>", 1]]


def snapshot(snap, monkeypatch):
    """A three-file 'pin' written to snap, with PIN_MANIFEST and the clean H5 Spec's replay files set to it: (manifest,
    its lines)."""
    _write(os.path.join(snap, "replay.py"), b"engine\n")
    _write(os.path.join(snap, "store.py"), f'x = 1\nDEFAULTS = "{h5.store.DEFAULTS}"\n'.encode())
    _write(os.path.join(snap, "tests", "t.py"), b"")
    want, lines = h5.manifest(str(snap))
    monkeypatch.setattr(h5, "PIN_MANIFEST", want)
    import backtest.clean_h5 as ch5
    monkeypatch.setattr(ch5, "SPEC", SimpleNamespace(name="H5", replay_sha256={"replay.py": hashlib.sha256(b"engine\n").hexdigest()}))
    return want, lines


def test_the_snapshot_must_be_the_pins_tree(tmp_path, monkeypatch):
    assert h5.git_blob(_write(tmp_path / "hello", b"hello\n")) == "ce013625030ba8dba906f756967f9e9ca394464a"  # git hash-object
    snap = tmp_path / "backtest"
    want, lines = snapshot(snap, monkeypatch)
    assert [x.split(" ", 1)[1] for x in lines] == ["replay.py", "store.py", "tests/t.py"]
    assert h5.check_engine(str(snap)) == want
    _write(snap / "__pycache__" / "replay.cpython-313.pyc", b"x")
    with pytest.raises(cc.Refused, match="not 15fa3dd"):
        h5.check_engine(str(snap))
    shutil.rmtree(snap / "__pycache__")
    _write(snap / "replay.py", b"engine!\n")
    with pytest.raises(cc.Refused, match="not 15fa3dd"):
        h5.check_engine(str(snap))


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return str(path)


def test_the_nas_scripts_tournament_flags_are_grid():
    with open(os.path.join(os.path.dirname(h5.__file__), "nas_h5_dev_repro.sh")) as f:
        text = f.read()
    args = re.search(r"^ARGS=\((.*?)\)$", text, re.M | re.S).group(1).split()
    i = args.index("--no-report")  # the one flag without a value
    assert args[i + 1:] == ["--workers", '"$WORKERS"', "--run-id", '"$RUN_ID"']
    g = h5.GRID
    assert dict(zip(args[:i][0::2], args[:i][1::2])) == {
        "--families": ",".join(g["families"]), "--segments": ",".join(g["segments"]), "--variants": ",".join(g["variants"]),
        "--f7-grid": g["f7_grid"], "--sizes": ",".join(f"{x:g}" for x in g["sizes_sol"]),
        "--delays": ",".join(map(str, g["delays"])), "--q": ",".join(f"{x:g}" for x in g["q"]), "--tau": ",".join(g["tau"]),
        "--tol": f"{g['slippage_tol']:g}"}
    assert len(args[:i]) == 18 and g["batch"] == "tournament_v1" and "--batch" not in args  # the tournament's default label
    assert f"PIN={h5.PIN}" in text


def test_e63s_config_must_be_the_grid_but_for_the_variants(lab, monkeypatch):
    cfg = h5.config(lab.e63)
    with open(os.path.join(lab.e63, "config.json"), "w") as f:
        json.dump({**cfg, "f7_grid": "v1,big"}, f)
    shas(lab, monkeypatch)
    with pytest.raises(cc.Refused, match="grid"):
        h5.check_inputs(lab.e63, lab.exitday, lab.keys, lab.store)


def test_the_inputs_step_checks_job_87_too(lab, monkeypatch):
    a = ["inputs", "--e63", lab.e63, "--e63-exitday-json", lab.exitday, "--c3-keys", lab.keys, "--store", lab.store, "--bigtp", lab.bigtp]
    assert h5.main(a) == 0
    cfg = h5.config(lab.bigtp)
    with open(os.path.join(lab.bigtp, "config.json"), "w") as f:
        json.dump({**cfg, "store": {**STORE_META, "built_at_unix": 2}}, f)
    shas(lab, monkeypatch)
    with pytest.raises(cc.Refused, match="job 87"):
        h5.main(a)


def test_compare_checks_the_runs_own_snapshot(lab, monkeypatch):
    """The new config's engine_commit is only LAB_ENGINE_COMMIT, which the script sets: what ties the rows to 15fa3dd
    is the run's engine_dir, which must be --engine-dir, and compare's own check_engine of that dir."""
    monkeypatch.setattr(h5, "check_engine", CHECK_ENGINE)
    snapshot(lab.engine, monkeypatch)
    assert h5.main(argv(lab, out="e0")) == 0
    # The same files under another dir of the same name: not the snapshot the run came from.
    other = os.path.join(lab.root, "other", "engine", "backtest")
    shutil.copytree(lab.engine, other)
    cfg = h5.config(lab.new)
    edit_json(os.path.join(lab.new, "config.json"), lambda j: j.update(engine_dir=other))
    with pytest.raises(cc.Refused, match="engine_dir"):
        h5.main(argv(lab, out="e1"))
    edit_json(os.path.join(lab.new, "config.json"), lambda j: j.update(engine_dir=cfg["engine_dir"]))
    a = argv(lab, out="e2")
    del a[a.index("--engine-dir"):a.index("--engine-dir") + 2]
    with pytest.raises(cc.Refused, match="engine_dir"):
        h5.main(a)
    _write(os.path.join(lab.engine, "replay.py"), b"engine!\n")
    with pytest.raises(cc.Refused, match="not 15fa3dd"):
        h5.main(argv(lab, out="e3"))


@pytest.mark.parametrize("key,cmd", [("e63_config", "full"), ("e63_json", "full"), ("e63_exitday_json", "full"),
                                     ("c3_keys", "full"), ("defaults", "full"), ("bigtp_config", "inputs"),
                                     ("bigtp_config", "step0")])
def test_an_input_that_is_not_its_archived_copy_is_refused(lab, monkeypatch, key, cmd):
    """The same content in another copy (the JSON re-serialized, the parquet in another compression, the YAML with a
    comment more) is refused by its sha256 alone: a NAS copy that differs would move E63's side or the exit rules and
    look like an engine difference (map risk 4)."""
    if key == "defaults":
        copy = os.path.join(lab.root, "week2-defaults.yaml")
        with open(h5.store.DEFAULTS) as f, open(copy, "w") as g:
            g.write(f.read() + "\n# a copy\n")
        monkeypatch.setattr(h5.store, "DEFAULTS", copy)
    elif key == "c3_keys":
        pq.write_table(pq.read_table(lab.keys), lab.keys, compression="gzip")
    else:
        path = {"e63_config": os.path.join(lab.e63, "config.json"), "e63_json": os.path.join(lab.e63, "ceiling", "ceiling.json"),
                "e63_exitday_json": lab.exitday, "bigtp_config": os.path.join(lab.bigtp, "config.json")}[key]
        with open(path) as f:
            j = json.load(f)
        with open(path, "w") as f:
            json.dump(j, f, indent=2)
    a = argv(lab, cmd) if cmd != "inputs" else ["inputs", "--e63", lab.e63, "--e63-exitday-json", lab.exitday,
                                                "--c3-keys", lab.keys, "--store", lab.store, "--bigtp", lab.bigtp]
    with pytest.raises(cc.Refused, match=f"archived {key} "):
        h5.main(a)


@pytest.mark.parametrize("change,match", [(lambda j: j.update(engine_commit=h5.PIN), "not E63's run"),
                                          (lambda j: j.update(venue_math="rust"), "not E63's run"),
                                          (lambda j: j.update(positions=j["positions"] + 1), "rows in its parts")],
                         ids=["engine", "venue-math", "incomplete"])
def test_e63s_run_must_be_e63s_complete_python_run(lab, monkeypatch, change, match):
    edit_json(os.path.join(lab.e63, "config.json"), change)
    shas(lab, monkeypatch)
    with pytest.raises(cc.Refused, match=match):
        h5.check_inputs(lab.e63, lab.exitday, lab.keys, lab.store)


@pytest.mark.parametrize("which", ["ceiling.json", "exit-day ceiling.json"])
@pytest.mark.parametrize("change", [lambda p, j: p.update(engine_commit=h5.PIN),
                                    lambda p, j: j["provenance"].append(dict(p, run="other"))], ids=["engine", "two-runs"])
def test_e63s_jsons_must_be_of_e63s_run_alone(lab, monkeypatch, which, change):
    path = lab.exitday if which.startswith("exit-day") else os.path.join(lab.e63, "ceiling", "ceiling.json")
    edit_json(path, lambda j: change(j["provenance"][0], j))
    shas(lab, monkeypatch)
    with pytest.raises(cc.Refused, match=f"E63's {which} is not of E63's run alone"):
        h5.check_inputs(lab.e63, lab.exitday, lab.keys, lab.store)


def test_lab_core_other_than_python_is_refused(monkeypatch):
    bind_fixture_analysis(monkeypatch)
    monkeypatch.setenv("LAB_CORE", "rust")
    with pytest.raises(cc.Refused, match="LAB_CORE"):
        h5.check_analysis()
    monkeypatch.setenv("LAB_CORE", "python")
    h5.check_analysis()


def test_frozen_analysis_refuses_the_changed_failed_entry_timing():
    # The newer risk-day implementation must not silently reproduce an archived E63 comparison.
    # Use the preserved production pins here, unlike the self-contained synthetic fixture above.
    assert h5.git_blob(cc.__file__) != h5.ANALYSIS["capital_ceiling.py"]
    with pytest.raises(cc.Refused, match="analysis code changed"):
        h5.check_analysis()


def test_step0_refuses_an_incomplete_job_87(lab, monkeypatch):
    edit_json(os.path.join(lab.bigtp, "config.json"), lambda j: j.update(positions=j["positions"] + 1))
    shas(lab, monkeypatch)
    with pytest.raises(cc.Refused, match="positions"):
        h5.main(argv(lab, "step0"))


@pytest.mark.parametrize("change", [lambda j, p: j.update(settle_rule=2), lambda j, p: j.update(daily_loss_book="trigger_day"),
                                    lambda j, p: j["provenance"][0].update(engine_commit=h5.E63_COMMIT),
                                    lambda j, p: j["provenance"][0].update(run=p.e63),
                                    lambda j, p: j["provenance"].append(dict(j["provenance"][0], run=p.e63)),
                                    None], ids=["rule-2", "trigger-day-book", "e63-engine", "e63-run", "two-runs", "e63s-own-json"])
def test_step2s_json_must_be_capital_ceilings_main_on_the_new_run(lab, change):
    """Labels alone (the cells are the new run's): rule 2, another loss book, E63's engine or run in the provenance, or
    E63's own exit-day json as step 2's (map risk 2: E63 compared with itself, GREEN without the check)."""
    if change is None:
        lab.new_json = lab.exitday
    else:
        edit_json(lab.new_json, lambda j: change(j, lab))
    with pytest.raises(cc.Refused, match="not capital_ceiling's main on the new run"):
        h5.main(argv(lab))


@pytest.mark.parametrize("change", [lambda j: h5_cell(j, 2.0, 3)["unlimited"].update(filled=h5_cell(j, 2.0, 3)["unlimited"]["filled"] + 1),
                                    lambda j: j["cells"].remove(h5_cell(j, 10.0, 3, "pessimistic"))], ids=["field", "cell"])
def test_step2s_cells_must_be_this_modules_recompute_of_the_new_run(lab, change):
    edit_json(lab.new_json, change)
    with pytest.raises(cc.Refused, match="not this module's recompute"):
        h5.main(argv(lab))


def test_step2_compares_max_useful_and_e63s_own_without_limits_fields(lab, monkeypatch):
    with open(lab.new_json) as f:
        best = json.load(f)["max_useful"]["H5"]["best_size"]
    edit_json(lab.new_json, lambda j: j["max_useful"]["H5"].update(best_size=10.0))
    assert h5.main(argv(lab, out="m1")) == 1
    o = result(lab, "m1")
    assert o["json"]["max_useful_h5"] == [["best_size", 10.0, best]] and o["reasons"] == ["step 2 json: max_useful_h5 1 differences"]
    # E63's own ceiling.json's sandwich flag: not anchored (only its without-limits fields are), compared in step 2.
    lab.new_json = new_json(lab)
    edit_json(os.path.join(lab.e63, "ceiling", "ceiling.json"), lambda j: h5_cell(j).update(sandwich_flagged_pct=50.0))
    shas(lab, monkeypatch)
    assert h5.main(argv(lab, out="m2")) == 1
    o = result(lab, "m2")
    with open(lab.new_json) as f:
        flag = h5_cell(json.load(f))["sandwich_flagged_pct"]
    assert flag != 50.0 and o["json"]["e63_json_unlimited"] == [["(0.5, 2, 'base')", "flag", flag, 50.0]]
    assert o["json"]["exitday_cells"] == [] and o["reasons"] == ["step 2 json: e63_json_unlimited 1 differences"]


def test_a_new_run_whose_days_are_not_e63s_is_red_by_its_days(lab):
    """F's rows on a fifth day: the new run's days are not E63's. RED with that reason and the json's days (each
    side's cells over its own days, as capital_ceiling's main), not a refusal of step 2's json as another run's."""
    rewrite(lab.new, lambda rows: [r.update(day=DAY_OF.days[4]) for r in rows if r["pool"] == "F"])
    lab.new_json = new_json(lab)
    assert h5.main(argv(lab)) == 1
    o = result(lab)
    assert o["new_days"] == DAYS[:3] + [DAY_OF.days[4]] and o["days"] == DAYS
    assert f"the new run's days {o['new_days']} are not E63's" in o["reasons"] and o["json"]["days"]


COMMIT = "0123456" + "0" * 33


def nas_script(tmp, data, spill_root, swap=None):
    """nas_h5_dev_repro.sh copied into a repo dir of its own (a COMMIT file, no git), STEP0_ONLY=1, with a stub python
    that logs its calls and exits 0 (swap: (link, target), the stub's first call makes link a symlink to target):
    (rc, stderr, the stub's calls, OUT)."""
    repo = tmp / "repo"
    os.makedirs(repo / "lab" / "backtest")
    shutil.copy(os.path.join(os.path.dirname(h5.__file__), "nas_h5_dev_repro.sh"), repo / "lab" / "backtest")
    (repo / "COMMIT").write_text(COMMIT + "\n")
    py = tmp / "py"
    py.write_text('#!/bin/bash\necho "$*" >> "$STUB_LOG"\n'
                  'if [ -n "${SWAP:-}" ] && [ ! -L "$SWAP" ]; then rm -rf -- "$SWAP"; ln -s "$SWAP_TO" "$SWAP"; fi\n')
    py.chmod(0o755)
    env = {"PATH": os.environ["PATH"], "LAB_DATA_ROOT": str(data), "LAB_SPILL_ROOT": str(spill_root), "LAB_PY": str(py),
           "STUB_LOG": str(tmp / "calls"), "ENGINE_REPO": str(tmp / "no-engine"), "STEP0_ONLY": "1"}
    if swap:
        env.update(SWAP=str(swap[0]), SWAP_TO=str(swap[1]))
    p = subprocess.run(["bash", str(repo / "lab" / "backtest" / "nas_h5_dev_repro.sh")], env=env, capture_output=True,
                       text=True, timeout=60)
    calls = (tmp / "calls").read_text().splitlines() if (tmp / "calls").exists() else []
    return p.returncode, p.stderr, calls, data / "backtests" / f"h5-dev-repro-{COMMIT[:7]}-step0"


def test_the_nas_scripts_spill_dir_is_its_own(tmp_path):
    """The spill dir is removed on every exit: apart from the output it is; the same dir (LAB_SPILL_ROOT=$DATA/backtests),
    one inside the other or the same through a symlinked backtests dir is refused before anything runs."""
    name = f"h5-dev-repro-{COMMIT[:7]}-step0"
    t = tmp_path / "apart"
    rc, err, calls, out = nas_script(t, t / "data", t / "spill")
    assert rc == 0 and len(calls) == 3 and (out / "job.log").exists() and (out / "SHA256SUMS").exists(), err
    assert os.listdir(t / "spill") == []  # its spill dir made and removed
    for case, data, spill_root in (("same", "data", "data/backtests"), ("spill-in-out", "data", f"data/backtests/{name}"),
                                   ("out-in-spill", f"sp/{name}/data", "sp"), ("symlinked-backtests", "data", "bt")):
        t = tmp_path / case
        os.makedirs(t / "bt")
        if case == "symlinked-backtests":
            os.makedirs(t / "data")
            os.symlink(t / "bt", t / "data" / "backtests")
        rc, err, calls, out = nas_script(t, t / data, t / spill_root)
        assert rc == 1 and "overlap" in err and calls == [] and not out.exists() and os.listdir(t / "bt") == [], (case, err)


def test_the_nas_script_keeps_its_evidence_when_the_spill_dir_turns_into_it(tmp_path):
    """The exit's own check: the spill root swapped for a symlink to the backtests dir during the run would make the
    spill dir the output; it is left (RED), the evidence kept."""
    data, spill_root = tmp_path / "data", tmp_path / "sp"
    rc, err, calls, out = nas_script(tmp_path, data, spill_root, swap=(spill_root, data / "backtests"))
    assert rc == 1 and "not removed" in err and len(calls) == 3
    assert (out / "job.log").exists() and (out / "SHA256SUMS").exists()
