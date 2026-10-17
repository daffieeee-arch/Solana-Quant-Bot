"""paper.alpenglow_metrics on a synthetic recorder root and journal: a segment before the switch (blocks at 250 ms,
no footers) and one after it (blocks at 200 ms with footers, one slot with two banks, a slot gap); the switch slot
comes from alpenglow.json; M2 joins the journal's F7 triggers to the footers (last received footer of a slot); the
keeper's kept copies are read with the same code; a footer received twice (reconnect) counts once; a root without footers still reports the blocks."""

import json
import os

import pyarrow as pa
import pyarrow.parquet as pq

from paper import alpenglow_metrics as am
from paper.journal import Journal

T0 = 1_792_000_000  # block time of the first slot (UTC day 2026-10-14)
SWITCH = 2000


def write(d, table, rows, name="part-0.parquet"):
    os.makedirs(os.path.join(d, table), exist_ok=True)
    pq.write_table(pa.table({k: [r[k] for r in rows] for k in rows[0]}), os.path.join(d, table, name))


def root(tmp_path):
    rec = str(tmp_path / "rec")
    # segment 1: slots 0..1200 at 250 ms (block_time in whole seconds), every 100th slot skipped, no footers
    s1 = os.path.join(rec, "segments", "000000001")
    write(s1, "blocks", [{"slot": s, "parent_slot": s - 1, "block_time": T0 + s // 4, "block_height": s, "executed_tx_count": 2000 + s % 7,
                          "entry_count": 64, "skipped": s % 100 == 50} for s in range(0, 1201)])
    with open(os.path.join(s1, "_segment.json"), "w") as f:
        json.dump({"seq": 1, "first_slot": 0, "last_slot": 1200, "complete": True, "totals": {"late_rows": 3, "unconfirmed_slots": 0},
                   "reconnects": [{"unix": 1}]}, f)
    # segment 2: slots 2000..3200 at 200 ms, with a gap of 40 slots after 2500, footers for every block (producer time in ns),
    # slot 2100 with two banks (the later received one has bank_id 1)
    s2 = os.path.join(rec, "segments", "000000002")
    slots = [s for s in range(2000, 3201) if not 2500 < s < 2540]
    bt = lambda s: T0 + 400 + (s - 2000) // 5  # noqa: E731  (200 ms per slot in whole seconds)
    pns = lambda s: (T0 + 400) * 10**9 + (s - 2000) * 200_000_000  # noqa: E731
    write(s2, "blocks", [{"slot": s, "parent_slot": s - 1, "block_time": bt(s), "block_height": s, "executed_tx_count": 800 + s % 5,
                          "entry_count": 10, "skipped": False} for s in slots])
    footers = [{"slot": s, "bank_id": 0, "block_producer_time_ns": pns(s), "received_unix_ns": pns(s) + 150_000_000,
                "created_unix_ns": pns(s) + 100_000_000} for s in slots]
    footers.append({"slot": 2100, "bank_id": 1, "block_producer_time_ns": pns(2100) + 50_000_000, "received_unix_ns": pns(2100) + 300_000_000,
                    "created_unix_ns": pns(2100) + 250_000_000})
    # the same footer again after a reconnect, received 2 s later: deduplicated on (slot, bank_id), the first receipt counts
    footers.append({**footers[10], "received_unix_ns": footers[10]["received_unix_ns"] + 2_000_000_000, "created_unix_ns": footers[10]["created_unix_ns"] + 2_000_000_000})
    write(s2, "block_footers", footers)
    with open(os.path.join(s2, "_segment.json"), "w") as f:
        json.dump({"seq": 2, "first_slot": 2000, "last_slot": 3200, "complete": True, "totals": {"late_rows": 0, "unconfirmed_slots": 2},
                   "reconnects": []}, f)
    # an incomplete segment is ignored
    s3 = os.path.join(rec, "segments", "000000003")
    write(s3, "blocks", [{"slot": 9000, "parent_slot": 8999, "block_time": T0 + 9000, "block_height": 1, "executed_tx_count": 1, "entry_count": 1, "skipped": False}])
    with open(os.path.join(s3, "_segment.json"), "w") as f:
        json.dump({"seq": 3, "first_slot": 9000, "last_slot": 9000, "complete": False}, f)
    with open(os.path.join(rec, "alpenglow.json"), "w") as f:
        json.dump({"first_footer_slot": SWITCH, "bank_id": 0, "block_producer_time_ns": pns(2000), "received_unix_ns": pns(2000) + 150_000_000}, f)
    return rec, pns


def journal(tmp_path, pns):
    j = Journal(str(tmp_path / "journal"))
    base = dict(family="F7", variant="tp4_T600", venue="pool", status="entered")
    # before the switch: receive - block time 700 ms, d 750 (3 slots at 250): not later than d
    j.put("signals", key="P1", t=1000, day="2026-10-14", block_time=T0 + 250, received_unix_ns=(T0 + 250) * 10**9 + 700_000_000,
          latency_ms=700, segment_lag_ms=1000, d_ms=750, **base)
    j.put("signals", key="P1", t=1000, day="2026-10-14", block_time=T0 + 250, received_unix_ns=(T0 + 250) * 10**9 + 700_000_000,
          latency_ms=700, segment_lag_ms=1000, d_ms=750, **{**base, "variant": "hold180"})  # the same trigger, counted once
    # after the switch: slot 2100 has two footers; the last received one (bank 1) is taken: 300 - 50 = 250 ms after its producer time
    bt2 = T0 + 400 + 20
    j.put("signals", key="P2", t=2100, day="2026-10-14", block_time=bt2, received_unix_ns=pns(2100) + 300_000_000,
          latency_ms=int((pns(2100) + 300_000_000) / 1e6 - bt2 * 1000), segment_lag_ms=1000, d_ms=600, **base)
    j.put("signals", key="P3", t=2600, day="2026-10-14", block_time=T0 + 520, received_unix_ns=pns(2600) + 900_000_000,
          latency_ms=900, segment_lag_ms=1000, d_ms=600, **base)
    j.put("signals", key="P4", t=2700, day="2026-10-14", block_time=None, received_unix_ns=None, latency_ms=None, segment_lag_ms=None, d_ms=600, **base)
    j.commit()
    j.close()
    return str(tmp_path / "journal")


def test_metrics_before_and_after_the_switch(tmp_path):
    rec, pns = root(tmp_path)
    m = am.compute(rec, (), journal(tmp_path, pns))
    assert m["switch_slot"] == SWITCH and m["switch_source"] == "alpenglow.json"
    assert m["sources"] == {"blocks": 2, "block_footers": 1}
    ep = {r["epoch"]: r["ms_blocks"] for r in m["m4_epoch"]}
    assert ep == {0: 200.0}  # one epoch over both segments (slot 0 .. 3200 in 640 s); the day table splits the sides
    day = {r["side"]: r for r in m["m4_day"]}
    assert (day["before"]["ms_blocks"], day["after"]["ms_blocks"]) == (250.0, 200.0)
    assert m["m4_epoch_footers"][0]["ms_footers"] == 200.0
    m5 = {r["side"]: r for r in m["m5_blocks"]}
    assert m5["before"]["blocks"] == 1201 and abs(m5["before"]["skipped_share"] - 12 / 1201) < 1e-4 and m5["before"]["tx_median"] > 2000
    assert m5["after"]["skipped_share"] == 0 and 800 <= m5["after"]["tx_median"] < 805
    m3 = m["m3_footers"][0]
    assert m3["side"] == "after" and m3["server_lag_ms_median"] == 100.0 and m3["network_ms_median"] == 50.0 and m3["receive_ms_median"] == 150.0
    m7 = m["m7_banks"][0]
    assert (m7["footers"], m7["multi_footer_slots"], m7["bank_id_gt0"]) == (1163, 1, 1)  # 1,201 slots minus the 39-slot gap, plus the second bank of 2100
    gaps = m["m6_switch"]["gaps_over_25_slots"]
    assert [(g["slot_before"], g["slot_after"]) for g in gaps] == [(1200, 2000), (2500, 2540)]
    seg = m["m6_switch"]["segments"]
    assert seg["before"] == {"segments": 1, "late_rows": 3, "unconfirmed_slots": 0, "reconnects": 1}
    assert seg["after"] == {"segments": 1, "late_rows": 0, "unconfirmed_slots": 2, "reconnects": 0}
    m2 = {r["side"]: r for r in m["m2_triggers"]}
    assert m2["before"]["triggers"] == 1 and m2["before"]["latency_ms_median"] == 700 and m2["before"]["later_than_d_share"] == 0
    assert m2["before"]["with_footer"] == 0 and m2["before"]["producer_latency_ms_median"] is None
    assert m2["after"]["triggers"] == 2 and m2["after"]["with_footer"] == 2 and m2["after"]["multi_footer_slots"] == 1
    assert m2["after"]["producer_latency_ms_median"] == (250 + 900) / 2 and m2["after"]["producer_later_than_d_share"] == 0.5
    text = am.render(m)
    assert "## M2" in text and "| 2026-10-14 | after | 2 |" in text and "1200 → 2000 (800 slots)" in text


def test_kept_copies_and_blocks_only(tmp_path):
    rec, pns = root(tmp_path)
    # the keeper's layout: <kept>/<seq>/<table>.parquet, with the footer of a later segment duplicated (deduplicated here)
    kept = str(tmp_path / "kept")
    for seq in ("000000001", "000000002"):
        for table in ("blocks", "block_footers"):
            src = os.path.join(rec, "segments", seq, table, "part-0.parquet")
            if os.path.exists(src):
                os.makedirs(os.path.join(kept, seq), exist_ok=True)
                pq.write_table(pq.read_table(src), os.path.join(kept, seq, f"{table}.parquet"))
    os.makedirs(os.path.join(kept, "000000003"), exist_ok=True)
    pq.write_table(pq.read_table(os.path.join(rec, "segments", "000000002", "block_footers", "part-0.parquet")).slice(0, 5),
                   os.path.join(kept, "000000003", "block_footers.parquet"))
    a = am.compute(rec, (), None)
    b = am.compute(None, [kept], None, switch_slot=SWITCH)
    assert b["switch_source"] == "argument" and b["m4_day"] == a["m4_day"] and b["m7_banks"] == a["m7_banks"] and b["m3_footers"] == a["m3_footers"]
    assert b["m6_switch"]["segments"]["after"]["segments"] == 0  # no segment metadata without a recorder root
    # blocks only (no footers anywhere): the footer tables are empty, the default switch slot applies
    only = str(tmp_path / "only")
    os.makedirs(os.path.join(only, "000000001"))
    pq.write_table(pq.read_table(os.path.join(rec, "segments", "000000001", "blocks", "part-0.parquet")), os.path.join(only, "000000001", "blocks.parquet"))
    c = am.compute(None, [only], None)
    assert c["switch_slot"] == am.SWITCH_SLOT and c["switch_source"] == "default" and c["m3_footers"] == [] and c["m2_triggers"] == []
    assert c["m4_day"][0]["ms_blocks"] == 250.0
    assert am.main(["--kept", only, "--out", str(tmp_path / "r.md"), "--json", str(tmp_path / "r.json")]) == 0
    assert "## M5" in open(tmp_path / "r.md").read() and json.load(open(tmp_path / "r.json"))["switch_slot"] == am.SWITCH_SLOT
