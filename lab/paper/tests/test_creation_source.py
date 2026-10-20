"""paper.creation_source: the live run's pool rows by their pool's creation source (creation-filters spec v0.2 §7, LAB
review M5), on a hand-made journal, recording and store."""

import json
import os
import sqlite3

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from paper import creation_source as C
from paper.journal import Journal

GROUPS = {"K3": {"primary": True, "variants": ["tp4_T600"]}, "H": {"primary": False, "variants": ["flow300_H6", "flow300_tp100_H6"]}}


def _put(root, seg, table, pools, closed=True):
    d = root / "segments" / f"{seg:09d}"
    (d / table).mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"slot": pa.array([1] * len(pools), pa.uint64()), "pool": pools}), str(d / table / "part-00000.parquet"))
    if closed:
        (d / "_segment.json").write_text(json.dumps({"seq": seg, "first_slot": 1, "last_slot": 2, "complete": True}))


def setup(tmp_path):
    """Pools: N created live (seg 0), B created in a probe's backfill (seg 1), S in the store, O only in pool_creations,
    Q created only in the open segment 2 (not read: Q counts as pool_creations). Day 1: K3 enters N (exit +2 SOL), S
    (exit -1 SOL) and O (exit +0.5 SOL), H skips O and B; day 2: K3 enters O again (entry failed) and Q; a curve row is
    not a pool row; day 3: the engine ran (heartbeat), no pool row."""
    rec = tmp_path / "rec"
    _put(rec, 0, "pump_amm/CreatePoolEvent", ["N"])
    _put(rec, 1, "backfill/pump_amm/CreatePoolEvent", ["B"])
    _put(rec, 1, "pool_creations", ["O", "O", "S", "N"])
    _put(rec, 2, "pump_amm/CreatePoolEvent", ["Q"], closed=False)
    store = str(tmp_path / "store.duckdb")
    con = duckdb.connect(store)
    con.execute("CREATE TABLE pools (pool VARCHAR)")
    con.execute("INSERT INTO pools VALUES ('S'), ('X')")
    con.close()
    j = Journal(str(tmp_path / "j"))
    j.state("groups", json.dumps(GROUPS, sort_keys=True))
    j.state("farmer_seed", json.dumps({"store": store, "lo": 1, "hi": 1}))
    d1, d2 = "2026-10-20", "2026-10-21"
    for key, t, pnl in (("N", 10, 2 * 10**9), ("S", 20, -(10**9)), ("O", 30, 5 * 10**8)):
        j.put("signals", family="F7", variant="tp4_T600", key=key, t=t, day=d1, venue="pool", status="entered")
        j.put("orders", family="F7", variant="tp4_T600", key=key, t=t, day=d1, status="filled")
        for side in ("buy", "sell"):
            j.put("fills", family="F7", variant="tp4_T600", key=key, t=t, side=side, day=d1)
        j.put("exits", family="F7", variant="tp4_T600", key=key, t=t, day=d1, pnl=pnl)
    for key, t in (("O", 30), ("B", 40)):
        j.put("signals", family="F7", variant="flow300_H6", key=key, t=t, day=d1, venue="pool", status="skipped", reason="farm flagged")
    j.put("signals", family="F7", variant="tp4_T600", key="O", t=50, day=d2, venue="pool", status="entered")
    j.put("orders", family="F7", variant="tp4_T600", key="O", t=50, day=d2, status="entry_failed")
    j.put("signals", family="F7", variant="tp4_T600", key="Q", t=55, day=d2, venue="pool", status="entered")
    j.put("signals", family="F7", variant="tp4_T600", key="M", t=60, day=d2, venue="curve", status="entered")
    j.put("heartbeat", minute=1, day="2026-10-22", note="")
    j.close()
    return str(tmp_path / "j"), str(rec), store


def test_each_pool_rows_class_is_counted_per_day_and_group(tmp_path):
    journal, rec, store = setup(tmp_path)
    doc = C.run(journal, rec)  # the store from the journal's farmer_seed
    assert doc["pools"] == {"created": ["B", "N"], "store": ["S"], "pool_creations": ["O", "Q"]}  # Q: the open segment is not read
    assert doc["inputs"] == {"segments_scanned": 3, "segments_closed": 2, "created_pools_found": 2, "store_pools_matched": 1,
                             "signals_read": 8, "pool_rows": 20}
    d1 = doc["days"]["2026-10-20"]
    assert d1["rows"]["K3/created"] == {"signals": {"entered": 1}, "orders": {"filled": 1}, "fills": {"buy": 1, "sell": 1}, "exits": 1,
                                        "pnl_lamports": 2 * 10**9}
    assert d1["rows"]["K3/store"]["pnl_lamports"] == -(10**9) and d1["rows"]["K3/pool_creations"]["pnl_lamports"] == 5 * 10**8
    assert d1["rows"]["H/pool_creations"] == {"signals": {"skipped": 1}, "orders": {}, "fills": {}, "exits": 0, "pnl_lamports": 0}
    assert d1["rows"]["H/created"]["signals"] == {"skipped": 1}  # B: created in a backfill counts as created
    assert d1["pools_with_rows"] == {"created": 2, "store": 1, "pool_creations": 1} and d1["pool_rows"] == 17
    assert doc["days"]["2026-10-21"]["rows"] == {
        "K3/pool_creations": {"signals": {"entered": 2}, "orders": {"entry_failed": 1}, "fills": {}, "exits": 0, "pnl_lamports": 0}}
    # a quiet day: the engine ran, no pool row, said so
    assert doc["days"]["2026-10-22"] == {"pool_rows": 0, "rows": {}, "pools_with_rows": {}}
    assert "0 pool rows this day." in open(os.path.join(journal, "2026-10-22", "creation_source.md")).read()
    md = open(os.path.join(journal, "2026-10-20", "creation_source.md")).read()
    assert "| K3/pool_creations | entered 1 | filled 1 | buy 1, sell 1 | 1 | +0.5000 |" in md and "17 pool rows" in md
    assert json.load(open(os.path.join(journal, "creation_source.json"))) == json.loads(json.dumps(doc, sort_keys=True))
    assert not [f for _, _, fs in os.walk(journal) for f in fs if f.endswith(".tmp")]


def test_a_named_store_that_cannot_be_read_is_refused_and_only_no_store_skips_it(tmp_path):
    journal, rec, _ = setup(tmp_path)
    with pytest.raises(SystemExit, match="cannot be read"):
        C.run(journal, rec, store=str(tmp_path / "missing.duckdb"))
    assert not os.path.exists(os.path.join(journal, "creation_source.json"))
    doc = C.run(journal, rec, no_store=True, days={"2026-10-21"})
    assert doc["store"] is None and doc["pools"]["store"] == [] and doc["pools"]["pool_creations"] == ["O", "Q", "S"]
    assert os.path.exists(os.path.join(journal, "2026-10-21", "creation_source.md"))
    assert not os.path.exists(os.path.join(journal, "2026-10-20", "creation_source.md"))  # only the days asked
    with pytest.raises(SystemExit):
        C.main(["--journal", journal, "--root", rec, "--store", "s", "--no-store"])


def test_an_empty_input_is_refused_and_nothing_is_written(tmp_path):
    journal, rec, store = setup(tmp_path)
    (tmp_path / "empty" / "segments" / "000000000").mkdir(parents=True)  # only an open segment
    for root, why in ((str(tmp_path / "none"), "no segments/"), (str(tmp_path / "empty"), "no closed segment")):
        with pytest.raises(SystemExit, match=why):
            C.run(journal, root)
    with pytest.raises(SystemExit, match="no journal"):
        C.run(str(tmp_path / "nojournal"), rec)
    bare = tmp_path / "bare"
    bare.mkdir()
    db = sqlite3.connect(str(bare / "journal.sqlite"))
    db.execute("CREATE TABLE state (k TEXT, v TEXT)")
    db.commit()
    db.close()
    with pytest.raises(SystemExit, match="no signals table"):
        C.run(str(bare), rec)
    assert not os.path.exists(os.path.join(journal, "creation_source.json")) and not os.path.exists(bare / "creation_source.json")


def test_the_journal_is_read_only(tmp_path):
    journal, rec, store = setup(tmp_path)
    db = os.path.join(journal, "journal.sqlite")
    before = (os.path.getsize(db), open(db, "rb").read())
    C.main(["--journal", journal, "--root", rec, "--store", store])
    assert (os.path.getsize(db), open(db, "rb").read()) == before
    # --out: the files elsewhere, nothing new beside the journal
    os.remove(os.path.join(journal, "creation_source.json"))
    C.main(["--journal", journal, "--root", rec, "--out", str(tmp_path / "o")])
    assert os.path.isfile(tmp_path / "o" / "creation_source.json") and os.path.isfile(tmp_path / "o" / "2026-10-21" / "creation_source.md")
    assert not os.path.exists(os.path.join(journal, "creation_source.json"))
