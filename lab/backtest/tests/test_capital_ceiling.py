"""The capital ceiling (backtest.capital_ceiling): its paper twin equals analysis._capital_limited, the unsold
conventions, the dev-only guards, the candidates' rows and the C3 keys, the day bootstrap and the headline."""

import json
import os
import random

import duckdb
import pytest

from backtest import capital_ceiling as cc
from backtest.analysis import _capital_limited

LO = cc.DEV[0]


def trades(n=400, seed=1):
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        entry = LO + 1000 + i * rnd.randint(5, 60)
        failed = rnd.random() < 0.1
        cost = int(rnd.choice((0.5, 1.25, 5)) * 1e9)
        pnl = -505_000 if failed else int(cost * rnd.uniform(-1.0, 0.6))
        exit_ = None if (not failed and rnd.random() < 0.05) else entry + rnd.randint(1, 3000)
        proceeds = 0 if failed else cost + pnl
        out.append((entry, exit_, cost, pnl, failed, f"2026-09-{12 + i * 20 // n:02d}", f"p{rnd.randint(0, 30)}", proceeds, 400.0))
    return sorted(out, key=lambda x: (x[0], x[1] if x[1] is not None else -1))


@pytest.mark.parametrize("start,maxpos,loss", [(10e9, 5, 1e9), (25e9, 5, 2.5e9), (100e9, 5, 10e9), (2e9, 2, 0.3e9)])
def test_capital_days_is_capital_limited_with_a_day_series(start, maxpos, loss):
    t = trades()
    got, extra = cc.capital_days(t, start, maxpos, loss, cc.COOLDOWN_MS)
    assert got == _capital_limited(t, start, maxpos, loss, cc.COOLDOWN_MS)
    # The day series adds up to the change of capital (final SOL is rounded to 3 decimals), and the taken trades per
    # day to the taken count.
    assert abs(sum(extra["day_pnl"].values()) / 1e9 - (got[6] - start / 1e9)) <= 0.0005 + 1e-9
    assert sum(extra["day_taken"].values()) == got[1]
    assert 0 < extra["peak_open"] <= maxpos


def test_a_ruined_account_takes_no_entry_that_will_fail():
    # A total loss empties the account; then three entries that will fail (cost 0) and one that would fill.
    t = [(LO, LO + 5, int(1e9), int(-1e9), False, "2026-09-12", "a", 0, 400.0)]
    t += [(LO + 10 + i, None, 0, -505_000, True, "2026-09-13", f"f{i}", 0, 400.0) for i in range(3)]
    t += [(LO + 20, LO + 30, int(1e9), int(0.1e9), False, "2026-09-13", "g", int(1.1e9), 400.0)]
    old, _ = cc.capital_days(t, 1e9, 5, 10e9, cc.COOLDOWN_MS)
    new, extra = cc.capital_days(t, 1e9, 5, 10e9, cc.COOLDOWN_MS, size=int(1e9))
    assert old[1] == 4 and old[6] < 0  # _capital_limited's twin: the three failing entries 'taken', cash below 0
    assert new[1] == 1 and new[6] == 0.0 and extra["ruin_day"] == "2026-09-13"


def test_unsold_conventions():
    assert cc.exit_of((900, 800, 3), "deadline") == 900
    assert cc.exit_of((None, 800, 3), "deadline") == 803
    assert cc.exit_of((None, None, 3), "deadline") == cc.DEV[1]
    assert cc.exit_of((None, 800, 3), "never") == cc.DEV[1]
    assert cc.exit_of((None, 800, 3), "entry") is None


def test_a_never_freed_unsold_position_keeps_its_place():
    # Five unsold positions fill every place: under 'never' the sixth signal finds no place, under 'entry' it does.
    rows = [(LO + 10 * i, None, LO + 10 * i + 5, 3, int(0.5e9), int(-0.5e9), False, "2026-09-12", f"p{i}", 0, 400.0) for i in range(6)]
    days = ["2026-09-12"]
    never = cc.limited(rows, days, 100, 0.5, "never")
    entry = cc.limited(rows, days, 100, 0.5, "entry")
    assert (never["taken"], never["skip_full_or_cash"]) == (5, 1)
    assert entry["taken"] == 6


def test_boot_days_is_seeded_and_counts_empty_days():
    m, lo, hi = cc.boot_days([1.0, 0.0, 0.0, 3.0])
    assert m == 1.0 and lo <= m <= hi
    assert cc.boot_days([1.0, 0.0, 0.0, 3.0]) == (m, lo, hi)
    assert cc.boot_days([]) == (None, None, None)


def write_run(root, name, rows, store=cc.DEV):
    d = os.path.join(root, name)
    os.makedirs(os.path.join(d, "parts"))
    with open(os.path.join(d, "config.json"), "w") as f:
        json.dump({"engine_commit": "abc", "store": {"slot_start": store[0], "slot_end_exclusive": store[1]}}, f)
    con = duckdb.connect()
    cols = ("family", "segment", "variant", "q", "overlay", "tau", "size_sol", "d", "t", "day", "pool", "farm", "skipped",
            "entry_failed", "cost", "proceeds", "exit_slot", "exit_decision_slot", "exit_attempts", "d_ms", "depth")
    con.execute("CREATE TABLE x (family VARCHAR, segment VARCHAR, variant VARCHAR, q DOUBLE, overlay VARCHAR, tau VARCHAR, size_sol DOUBLE, "
                "d INTEGER, t UBIGINT, day VARCHAR, pool VARCHAR, farm VARCHAR, skipped BOOLEAN, entry_failed BOOLEAN, cost BIGINT, "
                "proceeds BIGINT, exit_slot UBIGINT, exit_decision_slot UBIGINT, exit_attempts INTEGER, d_ms DOUBLE, depth DOUBLE)")
    con.executemany(f"INSERT INTO x VALUES ({', '.join('?' * len(cols))})", rows)
    con.execute(f"COPY x TO '{d}/parts/part-000000.parquet' (FORMAT parquet)")
    return d


def row(variant, size, d, t, day, pool, farm="organic", failed=False, ret=0.1, unsold=False, family="F7", skipped=False):
    cost = int(size * 1e9)
    proceeds = 0 if failed else int(cost * (1 + ret))
    return (family, "pool_reversed", variant, 0.5, "none", "empirical", size, d, t, day, pool, farm, skipped, failed,
            cost if not failed else 0, proceeds, None if unsold or failed else t + 100, t + 90, 1, 270.0 * d, 200e9)


@pytest.fixture
def run(tmp_path):
    rows = []
    for size in (0.5, 1.25, 5.0):
        for d in (2, 3):
            for i, (day, pool) in enumerate([("2026-09-12", "A"), ("2026-09-12", "B"), ("2026-09-13", "C"), ("2026-09-14", "D")]):
                t = LO + 10_000 + i * 1000
                rows.append(row("tp4_T600", size, d, t, day, pool, farm="flagged" if pool == "B" else "organic", ret=0.2 - 0.05 * size))
                rows.append(row("flow300_tp100_H6", size, d, t, day, pool, farm="flagged" if pool == "B" else "organic", ret=0.3))
                rows.append(row("hold300", size, d, t, day, pool, ret=-0.01))
            rows.append(row("tp4_T600", size, d, LO + 20_000, "2026-09-12", "E", failed=True))
            rows.append(row("tp4_T600", size, d, LO + 21_000, "2026-09-12", "F", skipped=True))
            rows.append(row("tp4_T600", size, d, LO + 22_000, "2026-09-12", "G", family="N2_F7"))
    keys = os.path.join(tmp_path, "keys.parquet")
    con = duckdb.connect()
    con.execute(f"""COPY (SELECT * FROM (VALUES ('A', {LO + 10_000}::UBIGINT, 2, '5m', 3), ('C', {LO + 12_000}::UBIGINT, 2, '5m', 2),
                    ('D', {LO + 13_000}::UBIGINT, 2, '5m', 1), ('A', {LO + 10_000}::UBIGINT, 3, '5m', 3))
                    t(pool, t, d, h, paid_buyers_60s)) TO '{keys}' (FORMAT parquet)""")
    return write_run(str(tmp_path), "run", rows), keys, tmp_path


def test_rows_candidates_and_keys(run):
    d, keys, tmp = run
    con = duckdb.connect()
    info = cc.load(con, [d], keys)
    assert info["c3_keys"] == 2  # paid_buyers_60s >= 2 at d 2 / 5 m: A and C
    days, cells = cc.compute(con, False)
    assert days == ["2026-09-12", "2026-09-13", "2026-09-14"]
    k3 = next(c for c in cells if (c["candidate"], c["size"], c["d"], c["scenario"]) == ("K3", 0.5, 3, "base"))
    h5 = next(c for c in cells if (c["candidate"], c["size"], c["d"], c["scenario"]) == ("H5", 0.5, 3, "base"))
    c3 = next(c for c in cells if (c["candidate"], c["size"], c["d"], c["scenario"]) == ("C3/tp4_T600", 0.5, 3, "base"))
    # K3: all farms, the failed entry counted, the skipped row and the N2 row not.
    assert (k3["unlimited"]["rows"], k3["unlimited"]["filled"]) == (5, 4)
    assert h5["unlimited"]["rows"] == 3  # organic only (B is flagged)
    assert (c3["unlimited"]["rows"], c3["arm_keys_matched"]) == (2, 2)
    # The named capitals: 0.5 / 1.25 / 5 SOL are C 10 / 25 / 100 under the primary mapping; under 10 % they are 5 / 12.5 / 50.
    named = {(x["mapping"], x["capital"]) for c in cells if c["candidate"] == "K3" for x in c["limited"] if x["named_capital"]}
    assert named == {("primary", 10), ("primary", 25), ("primary", 100)}
    out = {"cells": cells, "days": days, "c3_keys": 2, "provenance": info["provenance"],
           "max_useful": {n: cc.max_useful(cells, n) for n, *_ in cc.CANDIDATES}}
    mu = out["max_useful"]["K3"]
    # tp4_T600's return falls with size (0.2 - 0.05 x size): positive at 0.5 and 1.25, negative at 5.
    assert mu["largest_positive_size"] == 1.25 and mu["max_useful_capital"] == 25 and mu["edge_eaten_at_size"] == 5.0
    md = cc.report_md(out)
    assert "## Headline" in md and "20/C" in md and md.count(cc.GAP) == 1 + 2 * len(cc.CANDIDATES)
    assert "Parity against families-v2-1033-1046: not run." in md and "DRY RUN" not in md
    # C3: of the two keys, A and C have rows; none is unmatched.
    assert c3["arm_keys_unmatched"] == {"busy_arm": 0, "busy_non_arm": 0, "no_row": 0}


def test_a_run_outside_dev_is_refused(tmp_path):
    d = write_run(str(tmp_path), "clean", [row("tp4_T600", 0.5, 3, 455_800_000, "2026-10-14", "A")], store=(455_328_000, 459_648_000))
    with pytest.raises(cc.Refused, match="not dev2"):
        cc.load(duckdb.connect(), [d], "unused")


def test_rows_outside_dev_are_refused(tmp_path):
    d = write_run(str(tmp_path), "r", [row("tp4_T600", 0.5, 3, cc.DEV[1] + 5, "2026-10-02", "A")])
    with pytest.raises(cc.Refused, match="outside dev"):
        cc.load(duckdb.connect(), [d], "unused")


def test_duplicate_positions_are_refused(run):
    d, keys, tmp = run
    import shutil
    shutil.copy(os.path.join(d, "parts", "part-000000.parquet"), os.path.join(d, "parts", "part-000001.parquet"))
    with pytest.raises(cc.Refused, match="duplicate"):
        cc.load(duckdb.connect(), [d], keys)


def test_parity_needs_every_reference_cell(run, monkeypatch):
    d, keys, tmp = run
    con = duckdb.connect()
    cc.load(con, [d], keys)
    out = cc.parity(con, d, ["2026-09-12", "2026-09-13", "2026-09-14"])
    # The fixture has tp4_T600 at 0.5 SOL (d 2 and 3) but not at 1 or 2 SOL: those cells are RED.
    by = {(x["size"], x["d"]): x["status"] for x in out}
    assert by[(0.5, 2)] == "GREEN" and by[(0.5, 3)] == "GREEN" and by[(1.0, 2)].startswith("RED")


def test_the_first_run_supplies_a_cell(run):
    d, keys, tmp = run
    other = write_run(str(tmp), "other", [row("tp4_T600", 0.5, 3, LO + 50_000, "2026-09-15", "Z")])
    con = duckdb.connect()
    info = cc.load(con, [other, d], keys)
    # tp4_T600 0.5 d 3 comes from 'other' alone; the rest from 'run'.
    assert con.execute("SELECT count(*), min(src) FROM r WHERE variant = 'tp4_T600' AND size_sol = 0.5 AND d = 3").fetchone() == (1, 0)
    assert info["provenance"][0]["cells"] == [["tp4_T600", 0.5, 3]]
