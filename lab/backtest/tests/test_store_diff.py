"""backtest/store_diff.py on tiny synthetic stores (tests/store_diff_fixture.py): C1 equal, a changed row, a tie
column, an any_value mint, type drift, an empty table and the u64 gate; C2's window rules (rows outside the window,
the curve remainder, the mints look-ahead, pools as categories) and its RED cases; C4's trigger sets with the pinned
f7_signals_sql (main's, the same SQL as be47fc4's) on in-store, older and unexplained differences; the guards (no clean
or hold-out store, no spill beside a store), the manifest check and the CLI. No real store is opened."""

import hashlib
import json
import os

import pytest
import store_diff_fixture as F

from backtest import store_diff as D
from backtest import strategies

SOL = F.SOL


def _paths(tmp_path, a, b, types_b=None, skip_b=()):
    d = tmp_path / "stores"
    d.mkdir(parents=True, exist_ok=True)
    return F.write(str(d / "a.duckdb"), a), F.write(str(d / "b.duckdb"), b, types_b, skip_b)


def full(tmp_path, a, b, **kw):
    pa, pb = _paths(tmp_path, a, b, **kw)
    con, metas = D.connect({"a": pa, "b": pb}, str(tmp_path / "spill"))
    return D.full(con, metas)


def window(tmp_path, a, b, **kw):
    pa, pb = _paths(tmp_path, a, b, **kw)
    con, metas = D.connect({"a": pa, "b": pb}, str(tmp_path / "spill"))
    return D.window(con, metas, F.LO, F.HI)


def triggers(tmp_path, a, b):
    pa, pb = _paths(tmp_path, a, b)
    return D.triggers({"a": pa, "b": pb}, strategies, F.LO, F.HI, str(tmp_path / "spill"))[0]


def row(store, table, **match):
    return next(r for r in store[table] if all(r[k] == v for k, v in match.items()))


# ----------------------------------------------------------------------------- C1


def test_c1_the_same_build_is_green_on_every_table(tmp_path):
    a, _ = F.scenario()
    r = full(tmp_path, a, F.s0_like(a))
    assert r["green"], r["red"]
    assert sorted(r["tables"]) == sorted(F.SCHEMA)
    assert r["tables"]["pool"]["rows_a"] == len(a["pool"]) and r["tables"]["pool"]["equal"]
    assert r["meta_differs"]["period"] == ["dev-diag-clean-path", "dev"]  # meta is informational


def test_c1_a_changed_row_is_red_and_the_keyed_diff_names_the_column(tmp_path):
    a, _ = F.scenario()
    s0 = F.s0_like(a)
    row(s0, "pool", pool="P_REV", slot=F.LO + 105)["farmer"] = True
    r = full(tmp_path, a, s0)
    assert not r["green"] and r["red"] == ["pool differs"]
    d = r["tables"]["pool"]["diff"]
    assert list(d["columns"]) == ["farmer"] and d["columns"]["farmer"]["rows"] == 1
    assert d["columns"]["farmer"]["keys"] == [["P_REV", str(F.LO + 105), "2", "0", "1", "buy"]]
    assert d["only_a"]["rows"] == d["only_b"]["rows"] == 0


def test_c1_a_row_on_one_side_only_is_red(tmp_path):
    a, _ = F.scenario()
    s0 = F.s0_like(a)
    s0["curve"].append(F.curve("M_A", F.LO + 900, True, SOL, 63 * SOL, 43 * SOL, "T9"))
    r = full(tmp_path, a, s0)
    assert r["red"] == ["curve differs"]
    assert r["tables"]["curve"]["diff"]["only_b"]["rows"] == 1 and r["tables"]["curve"]["diff"]["only_a"]["rows"] == 0


def test_c1_tie_columns_are_compared_apart_never_red(tmp_path):
    a, _ = F.scenario()
    s0 = F.s0_like(a)
    row(s0, "pools", pool="P_OLD")["coin_creator"] = "CO_TIE"  # arg_max over a tie in the last slot
    row(s0, "pool_stats", pool="P_OLD").update(last_b=1, last_vault=2, coin_creator="CO_TIE")
    row(s0, "pools", pool="P_NORM")["part"] = 7  # ntile over another pool set
    row(s0, "pools", pool="P_OLD")["mint"] = "M_ANY"  # any_value over two links: mint_source NULL
    row(s0, "mints", mint="M_A")["pool"] = "P_ANY"  # any_value over two migrations
    r = full(tmp_path, a, s0)
    assert r["green"], r["red"]
    assert r["tables"]["pools"]["apart"] == {"coin_creator": 1, "part": 1, "mint (mint_source NULL)": 1}
    assert r["tables"]["pool_stats"]["apart"] == {"last_b": 1, "last_vault": 1, "coin_creator": 1}
    assert r["tables"]["mints"]["apart"] == {"pool": 1}


def test_c1_a_mint_from_the_create_event_is_not_a_tie(tmp_path):
    a, _ = F.scenario()
    s0 = F.s0_like(a)
    row(s0, "pools", pool="P_REV")["mint"] = "M_OTHER"  # mint_source create_pool: deterministic, compared
    r = full(tmp_path, a, s0)
    assert r["red"] == ["pools differs"] and list(r["tables"]["pools"]["diff"]["columns"]) == ["mint"]


def test_c1_type_drift_is_listed_and_the_values_still_compared(tmp_path):
    a, _ = F.scenario()
    s0 = F.s0_like(a)
    # a negative BIGINT hashes apart from the same HUGEINT: read both as HUGEINT
    r = full(tmp_path, a, s0, types_b={("pool", "e_delta"): "HUGEINT", ("curve", "vq"): "VARCHAR"})
    assert r["green"], r["red"]
    assert r["tables"]["pool"]["type_drift"] == [["e_delta", "BIGINT", "HUGEINT"]]
    assert r["tables"]["curve"]["type_drift"] == [["vq", "HUGEINT", "VARCHAR"]]
    row(s0, "pool", pool="P_OLD", slot=F.LO + 2000)["e_delta"] = -11 * SOL
    r = full(tmp_path / "x", a, s0, types_b={("pool", "e_delta"): "HUGEINT"})
    assert r["red"] == ["pool differs"] and list(r["tables"]["pool"]["diff"]["columns"]) == ["e_delta"]


def test_c1_an_empty_table_a_missing_table_and_the_u64_gate_are_red(tmp_path):
    a, _ = F.scenario()
    a["curve"] = []
    assert full(tmp_path / "1", a, F.s0_like(a))["red"] == ["curve is empty"]
    a, _ = F.scenario()
    r = full(tmp_path / "2", a, F.s0_like(a), skip_b=("pool_class_daily",))
    assert r["red"] == ["tables differ: only in A ['pool_class_daily'], only in B []"]
    a, _ = F.scenario()
    a["meta"]["excluded_pools_int64"]["pools"] = 1
    assert full(tmp_path / "3", a, F.s0_like(a))["red"] == ["a: meta.excluded_pools_int64 is " + repr(a["meta"]["excluded_pools_int64"])
                                                           + ", not 0 pools"]
    a, _ = F.scenario()
    del a["meta"]["excluded_pools_int64"]
    assert not full(tmp_path / "4", a, F.s0_like(a))["green"]  # fail closed: the key must be there


def test_c1_other_chunks_or_slots_are_red(tmp_path):
    a, _ = F.scenario()
    s0 = F.s0_like(a)
    s0["meta"]["chunk_list"][2]["extractor_sha256"] = "eac48884" * 8
    assert full(tmp_path, a, s0)["red"] == ["the chunk slots or extractor builds differ"]


# ----------------------------------------------------------------------------- C2


def test_c2_window_effects_are_categories_and_the_pinned_tables_are_equal(tmp_path):
    a, b = F.scenario()
    r = window(tmp_path, a, b)
    assert r["green"], r["red"]
    ex = r["exact"]
    assert [ex[t]["rows_a"] for t in ("blocks", "pool", "curve", "mints")] == [7, 6, 3, 2]
    assert ex["curve"]["remainder_b"] == 1  # M_OLD's row: created before A's start (warm-up truncation)
    assert ex["mints"]["apart"] == {"pool": 0}
    p = r["categories"]["pools"]
    assert p["paired"] == 3 and p["a_only"] == 0 and p["b_only_with_rows_in_window"] == 0
    assert p["columns"]["mint"] == {"older/b_created_before_start": 1}
    assert p["columns"]["created_in_period"] == {"older/b_created_before_start": 1}
    assert p["columns"]["can_boost"] == {"created_in_a/b_trades_after_end": 1}
    assert "part" not in p["columns"]
    u = p["f7_universe_older_pools_in_window"]
    assert u["pools"] == {"reversed": 1} and u["mint"] == {"reversed": 1} and u["coin_creator"] == {}
    ps = r["categories"]["pool_stats"]
    assert ps["pools"] == 1 and ps["equal"]  # only P_NORM lies inside A's store in B
    assert r["categories"]["meta"]["ms_per_slot_equal"] and r["categories"]["meta"]["extractor_equal"]


def test_c2_rows_outside_the_window_do_not_count(tmp_path):
    a, b = F.scenario()
    row(b, "pool", pool="P_REV", slot=F.HI + 10)["farmer"] = True
    row(b, "pool", pool="P_OLD", slot=F.WS + 5000)["farmer"] = True  # A's warm-up: not in the window
    assert window(tmp_path, a, b)["green"]


def test_c2_a_changed_pool_row_in_the_window_is_red(tmp_path):
    a, b = F.scenario()
    row(b, "pool", pool="P_REV", slot=F.LO + 105)["farmer"] = True
    r = window(tmp_path, a, b)
    assert r["red"] == ["pool differs in the window"]
    assert list(r["exact"]["pool"]["diff"]["columns"]) == ["farmer"]


def test_c2_pool_rows_on_one_side_are_put_in_categories(tmp_path):
    a, b = F.scenario()
    b["pool"].append(F.pool_row("P_GONE", F.LO + 3000, True, SOL, 300 * SOL, "W0", "normal"))  # absent in A
    b["pool"].append(F.pool_row("P_NORM", F.LO + 3001, True, SOL, 300 * SOL, "W0", "normal"))  # present, same class
    r = window(tmp_path, a, b)
    assert r["red"] == ["pool differs in the window"]
    assert r["exact"]["pool"]["one_sided_by_pool"] == {"only_a": {}, "only_b": {"pool absent in A": 1, "pool present, same class": 1}}
    assert r["categories"]["pools"]["b_only_with_rows_in_window"] == 1


def test_c2_lifecycle_inside_the_window_must_match(tmp_path):
    a, b = F.scenario()
    row(b, "mints", mint="M_B")["complete_slot"] = F.LO + 400  # B completes inside the window, A does not
    r = window(tmp_path, a, b)
    assert r["red"] == ["mints differs in the window"]
    assert list(r["exact"]["mints"]["diff"]["columns"]) == ["complete_slot"]


def test_c2_curve_rows_of_window_mints_must_match(tmp_path):
    a, b = F.scenario()
    row(b, "curve", mint="M_B", slot=F.LO + 50)["q"] = 2 * SOL
    r = window(tmp_path, a, b)
    assert r["red"] == ["curve differs in the window"] and r["exact"]["curve"]["remainder_b"] == 1


def test_c2_an_empty_window_is_red_not_vacuously_green(tmp_path):
    a, b = F.scenario()
    for s in (a, b):
        s["pool"] = [r for r in s["pool"] if not F.LO <= r["slot"] < F.HI]
    assert window(tmp_path, a, b)["red"] == ["pool has no rows in A's window"]


def test_c2_needs_a_window_that_ends_with_a(tmp_path):
    a, b = F.scenario()
    a["meta"]["window"] = [F.LO + 216_000, F.HI]
    with pytest.raises(SystemExit, match="does not end at the window"):
        window(tmp_path, a, b)


# ----------------------------------------------------------------------------- C4


def test_c4_equal_triggers_and_an_explained_mint_are_green(tmp_path):
    a, b = F.scenario()
    r = triggers(tmp_path, a, b)
    assert r["green"], r["red"]
    rev = r["segments"]["pool_reversed"]
    assert (rev["a"], rev["b"], rev["equal"], rev["differ"], rev["in_store"], rev["unexplained"]) == (2, 2, 1, 1, 0, 0)
    assert rev["explained"] == {"mint": 1}  # P_OLD: A has no mint (the trigger names the pool), B has M_X
    assert rev["examples"][0]["a"][1] == "P_OLD" and rev["examples"][0]["b"][1] == "M_X"
    assert (r["segments"]["curve"]["equal"], r["segments"]["pool_normal"]["equal"]) == (1, 1)
    assert r["clock"]["regimes"] == [[F.LO, F.HI, 267.93]]


def test_c4_a_difference_in_an_in_store_pool_is_red(tmp_path):
    a, b = F.scenario()
    row(b, "pools", pool="P_REV")["coin_creator"] = "W1"  # B drops the trigger: the trigger wallet is the coin creator
    r = triggers(tmp_path, a, b)
    assert r["red"] == ["1 pool_reversed trigger(s) of in-store pools differ"]
    rev = r["segments"]["pool_reversed"]
    assert rev["only_a"] == 1 and rev["explained"] == {"coin_creator,creator": 1, "mint": 1}  # P_REV explained, but in-store


def test_c4_an_older_pool_with_another_coin_creator_is_explained(tmp_path):
    a, b = F.scenario()
    row(b, "pools", pool="P_OLD")["coin_creator"] = "W3"
    r = triggers(tmp_path, a, b)
    assert r["green"], r["red"]
    assert r["segments"]["pool_reversed"]["explained"] == {"coin_creator,creator": 1}


def test_c4_a_pool_whose_mint_b_created_before_the_start_is_not_in_store(tmp_path):
    a, b = F.scenario()
    b["mints"].append(F.mint("M_R", F.WS - 500, "W1"))  # B knows P_REV's mint and its creator from before A's start
    r = triggers(tmp_path, a, b)
    assert r["green"], r["red"]
    assert r["segments"]["pool_reversed"]["explained"] == {"creator": 1, "mint": 1}


def test_c4_a_difference_no_pool_attribute_explains_is_red(tmp_path):
    a, b = F.scenario()
    b["pool"].append(F.pool_row("P_OLD", F.LO + 1985, False, 10 * SOL, 205 * SOL, "W4"))  # a sell 15 slots before
    r = triggers(tmp_path, a, b)
    assert r["red"] == ["1 pool_reversed difference(s) unexplained by a pool attribute"]
    assert r["segments"]["pool_reversed"]["only_a"] == 1


def test_c4_without_a_pool_reversed_trigger_in_a_is_red(tmp_path):
    a, b = F.scenario()
    for s in (a, b):
        s["pool"] = [r for r in s["pool"] if r["pool"] == "P_NORM"]
    assert triggers(tmp_path, a, b)["red"] == ["A has no pool_reversed trigger in the window"]


def test_f7_range_covers_exactly_the_window():
    clock = strategies.Clock({1042: 267.83, 1043: 267.93})
    d, e = D.f7_range(strategies.decision_bounds, clock, F.LO, F.HI)
    a, b = strategies.decision_bounds({}, d, e, clock)
    assert a <= F.LO and strategies.decision_bounds({}, d + 1, e, clock)[0] > F.LO and b >= F.HI - 1


# ----------------------------------------------------------------------------- guards, inputs, CLI


def test_guards_refuse_clean_and_holdout_stores_and_a_spill_beside_a_store(tmp_path):
    a, b = F.scenario()
    d = tmp_path / "s"
    d.mkdir()
    clean = F.write(str(d / "clean-1055-1062.duckdb"), a)
    with pytest.raises(SystemExit, match="clean or hold-out path"):
        D.connect({"a": clean}, str(tmp_path / "spill"))
    for k, v in (("period", "clean"), ("slot_end_exclusive", F.HI + 20 * F.EPOCH), ("prereg", "x")):
        bad = dict(a, meta=dict(a["meta"], **{k: v}))
        p = F.write(str(d / f"bad-{k}.duckdb"), bad)
        with pytest.raises(SystemExit, match="not a dev store"):
            D.connect({"a": p}, str(tmp_path / "spill"))
    ok = F.write(str(d / "a.duckdb"), a)
    with pytest.raises(SystemExit, match="lies in the store directory"):
        D.connect({"a": ok}, str(d / "spill"))


def _manifest(root, lo, hi, threads=6, rows=None, status="complete"):
    os.makedirs(os.path.join(root, f"{lo}-{hi}"), exist_ok=True)
    with open(os.path.join(root, f"{lo}-{hi}", "_manifest.json"), "w") as f:
        json.dump({"slot_start": lo, "slot_end_exclusive": hi, "status": status, "extractor_sha256": "0cfc4db2" * 8,
                   "threads": threads, "rows": rows or {"blocks": 216000}}, f)


def test_inputs_match_the_reference_manifests(tmp_path):
    root = tmp_path / "chunks"
    ref = {"chunks": {}}
    for lo in range(F.WS, F.HI, 216_000):
        _manifest(str(root), lo, lo + 216_000)
        rows = json.dumps({"blocks": 216000}, sort_keys=True, separators=(",", ":"))
        ref["chunks"][f"{lo}-{lo + 216_000}"] = {"status": "complete", "extractor_sha256": "0cfc4db2" * 8, "threads": 6,
                                                 "rows_sha256": hashlib.sha256(rows.encode()).hexdigest()}
    (tmp_path / "ref.json").write_text(json.dumps(ref))
    os.makedirs(root / "452304000-452520000")  # a hold-out chunk dir without a manifest: never opened
    assert D.inputs(str(tmp_path / "ref.json"), str(root), F.WS, F.HI)["green"]
    _manifest(str(root), F.LO, F.LO + 216_000, threads=1)
    r = D.inputs(str(tmp_path / "ref.json"), str(root), F.WS, F.HI)
    assert r["red"] == [f"{F.LO}-{F.LO + 216_000}: manifest differs from the reference"]
    _manifest(str(root), F.LO, F.LO + 216_000, status="partial")
    assert f"gap [{F.LO}, {F.LO + 216_000})" in D.inputs(str(tmp_path / "ref.json"), str(root), F.WS, F.HI)["red"]
    with pytest.raises(SystemExit, match="clean or hold-out root"):
        D.inputs(str(tmp_path / "ref.json"), str(tmp_path / "clean-v1"), F.WS, F.HI)


def test_the_committed_reference_holds_the_dev_chunks_only():
    with open(os.path.join(os.path.dirname(D.__file__), "dev_chunk_manifests.json")) as f:
        ref = json.load(f)["chunks"]
    slots = sorted(tuple(map(int, k.split("-"))) for k in ref)
    assert slots[0][0] == D.DEV_FIRST and slots[-1][1] == D.HOLDOUT_START and len(slots) == 28
    assert all(x[1] == y[0] for x, y in zip(slots, slots[1:]))
    assert {ref[k]["extractor_sha256"][:8] for k in ("450144000-450360000", "450576000-450792000")} == {"0cfc4db2"}


def test_cli_writes_a_new_report_and_returns_the_verdict(tmp_path, capsys):
    a, b = F.scenario()
    pa, pb = _paths(tmp_path, a, F.s0_like(a))
    out = tmp_path / "c1.json"
    assert D.main(["full", pa, pb, "--spill", str(tmp_path / "spill"), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["green"] and capsys.readouterr().out.strip().endswith("C1 GREEN")
    with pytest.raises(FileExistsError):  # a report is never overwritten
        D.main(["full", pa, pb, "--spill", str(tmp_path / "spill"), "--out", str(out)])
    assert D.main(["window", pa, pb, "--lo", str(F.LO), "--hi", str(F.HI), "--spill", str(tmp_path / "spill")]) == 0
    assert D.main(["meta", pb, "--expect-period", "dev", "--expect-commit", "ba322be60d25", "--spill", str(tmp_path / "spill")]) == 0
    assert D.main(["meta", pa, "--expect-period", "dev", "--spill", str(tmp_path / "spill")]) == 1


def test_cli_triggers_pin_the_strategies_file(tmp_path):
    a, b = F.scenario()
    pa, pb = _paths(tmp_path, a, b)
    lab = os.path.dirname(os.path.dirname(os.path.realpath(strategies.__file__)))
    with open(strategies.__file__, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    args = ["triggers", pa, pb, "--lo", str(F.LO), "--hi", str(F.HI), "--spill", str(tmp_path / "spill"), "--code-lab", lab]
    assert D.main(args + ["--strategies-sha256", sha[:16], "--rows", str(tmp_path / "t.csv")]) == 0
    lines = (tmp_path / "t.csv").read_text().splitlines()
    assert lines[0].startswith("side,venue,segment") and len(lines) == 1 + 4 + 4
    with pytest.raises(SystemExit, match="is not the pinned"):
        D.main(args + ["--strategies-sha256", "0" * 16])
    with pytest.raises(SystemExit, match="not from"):
        D.main(args[:-1] + [str(tmp_path), "--strategies-sha256", sha[:16]])
