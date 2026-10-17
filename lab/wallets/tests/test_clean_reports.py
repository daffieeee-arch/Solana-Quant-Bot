"""wallets.clean_reports on a synthetic H4 result (fixtures/clean_h4.json), run-parts and a mini clean store: the notes are
written, the unsold / unsold_busy count and the descriptive pool-class table (creator class, b0 band, bot before t) come
out of the store, and without --store the class section says 'onbekend'."""

import json
import os
import shutil

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from wallets import clean_reports as cr

HERE = os.path.dirname(os.path.abspath(__file__))
SYS = cr.SYSTEM_PROGRAM
COLS = ["family", "segment", "variant", "d", "t", "pool", "mint", "farm", "regime", "skipped", "entry_failed", "exit_reason", "exit_slot",
        "proceeds", "cost", "exit_attempts", "day", "mayhem_reverted"]


def row(pool, t, day, proceeds, exit_slot, farm="organic", d=1, variant="flow300_H6"):
    return dict(zip(COLS, ["F7", "pool_reversed", variant, d, t, pool, "M" + pool, farm, "270ms", False, False, "outflow" if exit_slot else None,
                           exit_slot, proceeds, 500_000_000, 1, day, False]))


def setup(tmp_path, with_bots=True):
    res = tmp_path / "h4-x"
    os.makedirs(res / "run-parts")
    shutil.copy(os.path.join(HERE, "fixtures", "clean_h4.json"), res / "result.json")
    shutil.copy(os.path.join(HERE, "fixtures", "clean_e20.json"), tmp_path / "e20.json")
    rows = [row("A", 100, "2026-10-10", 0, None),            # unsold, pool A (creator unknown), bot traded before t
            row("A", 200, "2026-10-11", 0, None),            # unsold_busy
            row("B", 300, "2026-10-12", 1_000_000_000, 350),  # won, pool B (first pool of its creator, b0 150 SOL)
            row("C", 400, "2026-10-10", 0, None),            # lost, pool C (serial creator, b0 80 SOL)
            row("D", 500, "2026-10-12", 600_000_000, 520, farm="flagged")]
    pq.write_table(pa.Table.from_pylist(rows), res / "run-parts" / "part-0.parquet")
    store = tmp_path / "clean.duckdb"
    con = duckdb.connect(str(store))
    con.execute("CREATE TABLE pools (pool VARCHAR, coin_creator VARCHAR, create_slot UBIGINT, b0 BIGINT)")
    con.executemany("INSERT INTO pools VALUES (?, ?, ?, ?)", [("A", SYS, 50, 90_000_000_000), ("B", "CR1", 60, 150_000_000_000),
                                                             ("C", "CR2", 70, 80_000_000_000), ("D", "CR2", 10, 85_000_000_000)]
                    + [(f"X{i}", "CR2", 1 + i, 1) for i in range(12)])  # CR2 has 12 earlier pools before C
    con.execute("CREATE TABLE pool (pool VARCHAR, slot UBIGINT, kind VARCHAR, trader VARCHAR)")
    con.executemany("INSERT INTO pool VALUES (?, ?, ?, ?)", [("A", 90, "buy", "BOT1"), ("A", 150, "sell", "BOT1"), ("B", 290, "buy", "OTHER"), ("C", 450, "buy", "BOT1")])
    con.close()
    watch = tmp_path / "watch.json"
    watch.write_text(json.dumps({"groups": {"reversed_bots": ["BOT1"] if with_bots else [], "group_c_top100": []}}))
    return str(res / "result.json"), str(store), str(watch)


def test_notes_with_classes_from_the_store(tmp_path):
    result, store, watch = setup(tmp_path)
    out = tmp_path / "out"
    e20 = str(tmp_path / "e20.json")
    # the store is only read with the four decided runs given (here the H4 fixture stands in for H3 and H5)
    assert cr.main(["--date", "2026-10-99", "--out", str(out), "--e20", e20, "--h3", result, "--h4", result, "--h5", result,
                    "--store", store, "--watch-wallets", watch]) == 0
    md = (out / "2026-10-99-flow300-clean.md").read_text()
    assert "## 2b. Beschrijvende pool-klassen" in md and "geen poort" in md and "binnen de clean store" in md and "trigger-dag" in md
    # organic positions: A (t 100), A (t 200), B, C -> creator classes: unknown x2, first pool x1, serial (12 earlier) x1
    assert "| creator onbekend (system-program) | 2 |" in md
    assert "| eerste pool van de creator | 1 |" in md
    assert "| creator met 10-99 eerdere pools | 1 |" in md
    assert "| b0 100-200 SOL | 1 |" in md and "| b0 50-100 SOL | 3 |" in md
    # the bot traded in A before t = 100 and 200 (slot 90), in C only after t (slot 450): 2 with, 2 without
    assert "| bot in de pool vóór t | 2 |" in md and "| geen bot vóór t | 2 |" in md
    cl = cr.classes(result, "flow300_H6", None, None, store, watch)
    bot = {r["class"]: r for r in cl["bot_class"]}
    assert bot["bot in de pool vóór t"]["lost_all"] == 1.0 and bot["geen bot vóór t"]["lost_all"] == 0.5
    # unsold / unsold_busy as before
    ub = cr.unsold(result, "flow300_H6")
    assert ub["organic|alle d"]["unsold"] == 3 and ub["organic|alle d"]["unsold_busy"] == 1
    assert (out / "2026-10-99-clean-samenvatting.txt").exists()


def test_without_store_the_class_section_is_unknown(tmp_path):
    result, store, watch = setup(tmp_path)
    out = tmp_path / "out"
    assert cr.main(["--date", "2026-10-99", "--out", str(out), "--h4", result]) == 0
    md = (out / "2026-10-99-flow300-clean.md").read_text()
    assert "## 2b. Beschrijvende pool-klassen" in md and "Onbekend: geen store" in md
    assert cr.classes(result, "flow300_H6", None, None, None, None) is None


def test_store_refused_without_the_four_decided_runs(tmp_path):
    import pytest
    result, store, watch = setup(tmp_path)
    out = tmp_path / "out"
    with pytest.raises(SystemExit):  # only h4: argparse error
        cr.main(["--date", "2026-10-99", "--out", str(out), "--h4", result, "--store", store])
    # all four given but one is a dry run: refused too
    dry = json.load(open(result))
    dry["dry_run"] = True
    (tmp_path / "dry.json").write_text(json.dumps(dry))
    with pytest.raises(SystemExit):
        cr.main(["--date", "2026-10-99", "--out", str(out), "--e20", str(tmp_path / "e20.json"), "--h3", result, "--h4", result, "--h5", str(tmp_path / "dry.json"), "--store", store])
    # the §2b intro names the store and its sha (here from the result's window, the mini store has no meta)
    cl = cr.classes(result, "flow300_H6", None, json.load(open(result)), store, watch)
    assert cl["store"] == "clean.duckdb" and cl["store_sha256"] == json.load(open(result))["window"]["store_sha256"]
