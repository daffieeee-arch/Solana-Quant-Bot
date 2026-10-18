"""wallets.clean_reports on synthetic results in the producers' real shapes (fixtures clean_e20 / clean_h3 / clean_h4 /
clean_h5.json: clean_test, clean_h3 "H3" + h3-parts/, clean_h4 "H4" + h4-parts/, clean_hx "test" + hypothesis H5 +
run-parts/), run-parts and a mini clean store: the notes are written from each producer's own block, the unsold /
unsold_busy count and the descriptive pool-class table (creator class, b0 band, bot before t) come out of the store,
without --store the class section says 'onbekend', and a result of the wrong shape is refused."""

import json
import os
import shutil

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from wallets import clean_reports as cr

HERE = os.path.dirname(os.path.abspath(__file__))
SYS = cr.SYSTEM_PROGRAM
COLS = ["family", "segment", "variant", "d", "t", "pool", "mint", "farm", "regime", "skipped", "entry_failed", "exit_reason", "exit_slot",
        "proceeds", "cost", "exit_attempts", "day", "mayhem_reverted"]
PARTS = {"H3": "h3-parts", "H4": "h4-parts", "H5": "run-parts"}


def row(pool, t, day, proceeds, exit_slot, farm="organic", d=1, variant="flow300_H6"):
    return dict(zip(COLS, ["F7", "pool_reversed", variant, d, t, pool, "M" + pool, farm, "270ms", False, False, "outflow" if exit_slot else None,
                           exit_slot, proceeds, 500_000_000, 1, day, False]))


def rows(variant):
    return [row("A", 100, "2026-10-10", 0, None, variant=variant),            # unsold, pool A (creator unknown), bot traded before t
            row("A", 200, "2026-10-11", 0, None, variant=variant),            # unsold_busy
            row("B", 300, "2026-10-12", 1_000_000_000, 350, variant=variant),  # won, pool B (first pool of its creator, b0 150 SOL)
            row("C", 400, "2026-10-10", 0, None, variant=variant),            # lost, pool C (serial creator, b0 80 SOL)
            row("D", 500, "2026-10-12", 600_000_000, 520, farm="flagged", variant=variant)]


def setup(tmp_path, with_bots=True):
    """The four results as their producers lay them out: <name>-x/result.json with the producer's parts dir beside it."""
    res = {}
    for n in ("H3", "H4", "H5"):
        d = tmp_path / f"{n.lower()}-x"
        os.makedirs(d / PARTS[n])
        shutil.copy(os.path.join(HERE, "fixtures", f"clean_{n.lower()}.json"), d / "result.json")
        pq.write_table(pa.Table.from_pylist(rows(cr.PREREG[n][2])), d / PARTS[n] / "part-0.parquet")
        res[n] = str(d / "result.json")
    shutil.copy(os.path.join(HERE, "fixtures", "clean_e20.json"), tmp_path / "e20.json")
    res["E20"] = str(tmp_path / "e20.json")
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
    return res, str(store), str(watch)


def four(res, out):
    return ["--date", "2026-10-99", "--out", str(out), "--e20", res["E20"], "--h3", res["H3"], "--h4", res["H4"], "--h5", res["H5"]]


def test_each_producer_shape_is_read_from_its_own_block(tmp_path):
    res, store, watch = setup(tmp_path)
    out = tmp_path / "out"
    assert cr.main(four(res, out) + ["--store", store, "--watch-wallets", watch]) == 0
    # H3: clean_h3's "H3" block, the daily difference against K3 over all farms, both series in the day table
    h3 = (out / "2026-10-99-hold180-clean.md").read_text()
    assert "hold180 tegen tp4_T600 (K3), farm alle" in h3 and "verbeteringstoets" in h3
    assert "dagverschil hold180 − tp4_T600" in h3 and "+0,63 SOL, 95 %-interval +0,12 … +1,10" in h3 and "**geslaagd**" in h3
    assert "| 2026-10-17 | -2,50 | -1,00 | -1,50 |" in h3
    assert "1055: 100 = 100, 1056: 90 = 90" in h3  # control from h3_rows
    assert "Posities hold180 (farm alle): 300 rijen" in h3 and "clean_h3, Lab Backtest; toetsblok `H3`, rijen in `h3-parts/`" in h3
    assert "Σ netto SOL per UTC-dag van hold180 (organic)" not in h3  # not an absolute organic > 0 test
    # H4: clean_h4's "H4" block (no "test" key in the file), vs_tp4_T600_organic, control from h4_rows
    h4 = (out / "2026-10-99-flow300-clean.md").read_text()
    assert "H4: gemiddeld Σ netto SOL per UTC-dag van flow300_H6 (organic) = +1,10 SOL, 95 %-interval -0,20 … +2,50" in h4
    assert "| 2026-10-17 | -3,10 |" in h4 and "flow300_H6 − tp4_T600 (organic), gemiddeld per dag: +1,20 SOL" in h4
    assert "1055: 100 = 100, 1056: 90 = 90" in h4 and "toetsblok `H4`, rijen in `h4-parts/`" in h4
    # H5: clean_hx's "test" block with hypothesis H5, take-profit exits and the common triggers
    h5 = (out / "2026-10-99-flow300-tp100-clean.md").read_text()
    assert "H5: gemiddeld Σ netto SOL per UTC-dag van flow300_tp100_H6 (organic) = +1,10 SOL" in h5
    assert "Take-profit-exits flow300_tp100_H6|organic: n 30" in h5 and "Gemeenschappelijke triggers met flow300_H6" in h5
    assert "toetsblok `test`, rijen in `run-parts/`" in h5
    # the cross-references and the owner summary use each test's own terms
    assert "- H3 (hold180 farm alle, tegen K3): hold180 beter dan K3" in h4 and "Σ/dag hold180 − tp4_T600 (farm alle) +0,63 SOL" in h4
    s = (out / "2026-10-99-clean-samenvatting.txt").read_text()
    assert "H3 (hold180 farm alle, verbetering tegen K3): **hold180 beter dan K3" in s and "+0,63 SOL [+0,12 … +1,10]" in s
    assert "H4 (flow300_H6 organic): **verworpen**; Σ/dag +1,10 SOL [-0,20 … +2,50], 6/7 dagen met positie" in s
    assert "E20 (K3 zonder/met rt_tx)" in s


def test_classes_and_unsold_come_from_the_producers_parts_dirs(tmp_path):
    res, store, watch = setup(tmp_path)
    out = tmp_path / "out"
    assert cr.main(four(res, out) + ["--store", store, "--watch-wallets", watch]) == 0
    for n, slug in (("H3", "hold180"), ("H4", "flow300"), ("H5", "flow300-tp100")):
        md = (out / f"2026-10-99-{slug}-clean.md").read_text()
        assert "## 2b. Beschrijvende pool-klassen" in md and "geen poort" in md and "binnen de clean store" in md and "trigger-dag" in md
        # organic positions: A (t 100), A (t 200), B, C -> creator classes: unknown x2, first pool x1, serial (12 earlier) x1
        assert "| creator onbekend (system-program) | 2 |" in md
        assert "| eerste pool van de creator | 1 |" in md
        assert "| creator met 10-99 eerdere pools | 1 |" in md
        assert "| b0 100-200 SOL | 1 |" in md and "| b0 50-100 SOL | 3 |" in md
        # the bot traded in A before t = 100 and 200 (slot 90), in C only after t (slot 450): 2 with, 2 without
        assert "| bot in de pool vóór t | 2 |" in md and "| geen bot vóór t | 2 |" in md
        assert f"`unsold_busy` (organic, alle d): 1 posities" in md
        cl = cr.classes(res[n], cr.PREREG[n][2], None, None, store, watch, name=n)
        bot = {r["class"]: r for r in cl["bot_class"]}
        assert bot["bot in de pool vóór t"]["lost_all"] == 1.0 and bot["geen bot vóór t"]["lost_all"] == 0.5
        ub = cr.unsold(res[n], cr.PREREG[n][2], name=n)
        assert ub["organic|alle d"]["unsold"] == 3 and ub["organic|alle d"]["unsold_busy"] == 1
    # the parts dir is found by the producer's name, and without a name by trying the three
    assert cr.parts_dir(res["H4"], name="H4").endswith("h4-parts") and cr.parts_dir(res["H4"]).endswith("h4-parts")
    assert cr.parts_dir(res["H3"], name="H3").endswith("h3-parts") and cr.parts_dir(res["E20"]) is None


def test_without_store_the_class_section_is_unknown(tmp_path):
    res, store, watch = setup(tmp_path)
    out = tmp_path / "out"
    assert cr.main(["--date", "2026-10-99", "--out", str(out), "--h4", res["H4"]]) == 0
    md = (out / "2026-10-99-flow300-clean.md").read_text()
    assert "## 2b. Beschrijvende pool-klassen" in md and "Onbekend: geen store" in md
    assert cr.classes(res["H4"], "flow300_H6", None, None, None, None, name="H4") is None


def test_wrong_shape_is_refused(tmp_path):
    res, store, watch = setup(tmp_path)
    out = tmp_path / "out"
    # an H5 file passed as --h4 (hypothesis H5, no "H4" block), an H4 file as --h3, an H3 file as --h5: argparse errors
    for args in (["--h4", res["H5"]], ["--h3", res["H4"]], ["--h5", res["H3"]], ["--e20", res["H4"]]):
        with pytest.raises(SystemExit):
            cr.main(["--date", "2026-10-99", "--out", str(out), *args])
    # a result whose control failed (result.json written before the test block): no decided run
    o = json.load(open(res["H4"]))
    bad = {k: o[k] for k in ("dry_run", "documents", "window", "exclusions", "control")}
    (tmp_path / "bad.json").write_text(json.dumps(bad))
    assert cr.test_block("H4", bad) is None
    with pytest.raises(SystemExit):
        cr.main(["--date", "2026-10-99", "--out", str(out), "--h4", str(tmp_path / "bad.json")])
    assert cr.test_block("H5", json.load(open(res["H5"])))["series"] == "flow300_tp100_H6"
    assert cr.test_block("H3", json.load(open(res["H3"])))["mean_daily_diff_sol"] == 0.63
    assert cr.test_block("H4", json.load(open(res["H5"]))) is None


def test_store_refused_without_the_four_decided_runs(tmp_path):
    res, store, watch = setup(tmp_path)
    out = tmp_path / "out"
    with pytest.raises(SystemExit):  # only h4: argparse error
        cr.main(["--date", "2026-10-99", "--out", str(out), "--h4", res["H4"], "--store", store])
    # all four given but one is a dry run: refused too
    dry = json.load(open(res["H5"]))
    dry["dry_run"] = True
    (tmp_path / "dry.json").write_text(json.dumps(dry))
    with pytest.raises(SystemExit):
        cr.main(["--date", "2026-10-99", "--out", str(out), "--e20", res["E20"], "--h3", res["H3"], "--h4", res["H4"], "--h5", str(tmp_path / "dry.json"), "--store", store])
    # the §2b intro names the store and its sha (here from the result's window, the mini store has no meta)
    cl = cr.classes(res["H4"], "flow300_H6", None, json.load(open(res["H4"])), store, watch, name="H4")
    assert cl["store"] == "clean.duckdb" and cl["store_sha256"] == json.load(open(res["H4"]))["window"]["store_sha256"]
