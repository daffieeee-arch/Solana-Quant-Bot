"""paper.creation_source: the live run's pool rows by their pool's creation source (creation-filters spec v0.2 §7, LAB
review M5), on a hand-made journal, recording and store."""

import json
import os

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from paper import creation_source as C
from paper.journal import Journal

GROUPS = {"K3": {"primary": True, "variants": ["tp4_T600"]}, "H": {"primary": False, "variants": ["flow300_H6", "flow300_tp100_H6"]}}


def _put(root, seg, table, pools):
    d = root / "segments" / f"{seg:09d}" / table
    d.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"slot": pa.array([1] * len(pools), pa.uint64()), "pool": pools}), str(d / "part-00000.parquet"))


def setup(tmp_path):
    """Pools: N created live (seg 0), B created in a probe's backfill (seg 1), S in the store, O only in pool_creations.
    Day 1: K3 enters N (filled, exit +2 SOL), S (filled, exit -1 SOL) and O (filled, exit +0.5 SOL), H skips O and B;
    day 2: K3 enters O again (entry failed); a curve row (not a pool row) is not counted."""
    rec = tmp_path / "rec"
    _put(rec, 0, "pump_amm/CreatePoolEvent", ["N"])
    _put(rec, 1, "backfill/pump_amm/CreatePoolEvent", ["B"])
    _put(rec, 1, "pool_creations", ["O", "O", "S", "N"])
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
    j.put("signals", family="F7", variant="tp4_T600", key="M", t=60, day=d2, venue="curve", status="entered")
    j.close()
    return str(tmp_path / "j"), str(rec), store


def test_each_pool_rows_class_is_counted_per_day_and_group(tmp_path):
    journal, rec, store = setup(tmp_path)
    doc = C.run(journal, rec)  # the store from the journal's farmer_seed
    assert doc["pools"] == {"created": ["B", "N"], "store": ["S"], "pool_creations": ["O"]}
    d1 = doc["days"]["2026-10-20"]["rows"]
    assert d1["K3/created"] == {"signals": {"entered": 1}, "orders": {"filled": 1}, "fills": {"buy": 1, "sell": 1}, "exits": 1,
                                "pnl_lamports": 2 * 10**9}
    assert d1["K3/store"]["pnl_lamports"] == -(10**9) and d1["K3/pool_creations"]["pnl_lamports"] == 5 * 10**8
    assert d1["H/pool_creations"] == {"signals": {"skipped": 1}, "orders": {}, "fills": {}, "exits": 0, "pnl_lamports": 0}
    assert d1["H/created"]["signals"] == {"skipped": 1}  # B: created in a backfill counts as created
    assert doc["days"]["2026-10-20"]["pools_by_class"] == {"created": 2, "store": 1, "pool_creations": 1}
    d2 = doc["days"]["2026-10-21"]["rows"]
    assert d2 == {"K3/pool_creations": {"signals": {"entered": 1}, "orders": {"entry_failed": 1}, "fills": {}, "exits": 0, "pnl_lamports": 0}}
    # the files: a section per day beside the engine's report, the JSON for the Paper tab
    md = open(os.path.join(journal, "2026-10-20", "creation_source.md")).read()
    assert "| K3/pool_creations | entered 1 | filled 1 | buy 1, sell 1 | 1 | +0.5000 |" in md and "pool_creations 1" in md
    assert json.load(open(os.path.join(journal, "creation_source.json"))) == json.loads(json.dumps(doc, sort_keys=True))
    assert not os.path.exists(os.path.join(journal, "creation_source.json.tmp"))


def test_without_a_store_a_pool_without_a_creation_counts_as_pool_creations(tmp_path):
    journal, rec, _ = setup(tmp_path)
    doc = C.run(journal, rec, store=str(tmp_path / "missing.duckdb"), days={"2026-10-21"})
    assert doc["pools"]["store"] == [] and doc["pools"]["pool_creations"] == ["O", "S"]
    assert os.path.exists(os.path.join(journal, "2026-10-21", "creation_source.md"))
    assert not os.path.exists(os.path.join(journal, "2026-10-20", "creation_source.md"))  # only the days asked


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
