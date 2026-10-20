"""LiveFeed learns a segment's tokens and pools in a fixed order (live.py _new_tokens_and_pools): first slot, then key,
per query. Before, DuckDB's GROUP BY and QUALIFY order decided, and it differs between processes (threads 2,
preserve_insertion_order off; M3-09a measured 49/11 of 60 runs on a job-91 segment), so the journal's 'learned' rows and
the live watchlist's new_pools came in another order in another run. Which rows are learned does not change.

The segment's names are chosen so that their name order is not their slot order, and the check runs in fresh processes
(DuckDB's order only differs between processes)."""

import json
import os
import subprocess
import sys

import pyarrow as pa
import pyarrow.parquet as pq

WSOL = "So11111111111111111111111111111111111111112"
LAB = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# (name, slot): name order and slot order differ
MINTS = [("Mz", 1_000_010), ("Ma", 1_000_011), ("Mq", 1_000_012), ("Mb", 1_000_013), ("Mx", 1_000_014), ("Mc", 1_000_015)]
POOLS = [("P9", 1_000_040), ("P1", 1_000_041), ("P7", 1_000_042), ("P2", 1_000_043), ("P8", 1_000_044), ("P3", 1_000_045),
         ("P6", 1_000_046), ("P4", 1_000_047)]


def write(d, table, rows):
    os.makedirs(os.path.join(d, table), exist_ok=True)
    pq.write_table(pa.table({k: [r[k] for r in rows] for k in rows[0]}), os.path.join(d, table, "part-0.parquet"))


def recording(root):
    """One complete segment of 100 slots: six tokens created, all completed and migrated in reverse name order of their
    slots, two pools by CreatePoolEvent and six by pool_creations only (each seen twice, at two slots; not learned since
    creation-filters spec v0.2 M1, only counted)."""
    d = os.path.join(root, "segments", f"{0:09d}")
    write(d, "blocks", [{"slot": s, "block_time": 1_790_000_000 + s // 3, "skipped": False} for s in range(1_000_000, 1_000_100, 10)])
    write(d, "pump/CreateEvent", [{"mint": m, "slot": s - 10, "tx_index": 0, "user": "U", "is_mayhem_mode": False,
                                   "quote_mint": WSOL} for m, s in MINTS])
    write(d, "pump/CompleteEvent", [{"mint": m, "slot": s + 10, "tx_index": 0} for m, s in MINTS])
    write(d, "pump/CompletePumpAmmMigrationEvent", [{"mint": m, "pool": f"G{m}", "slot": s + 20, "tx_index": 1} for m, s in MINTS])
    row = lambda p, s: {"pool": p, "base_mint": f"T{p}", "quote_mint": WSOL, "slot": s, "tx_index": 0, "coin_creator": "CC",  # noqa: E731
                        "is_mayhem_mode": False}
    write(d, "pump_amm/CreatePoolEvent", [row(p, s) for p, s in POOLS[:2]])
    write(d, "pool_creations", [row(p, s + k) for p, s in POOLS[2:] for k in (0, 5)])
    with open(os.path.join(d, "_segment.json"), "w") as f:
        json.dump({"seq": 0, "first_slot": 1_000_000, "last_slot": 1_000_099, "complete": True}, f)


def learned(root):
    """The feed's learned rows and new pools after reading the recording, in their order."""
    from paper.live import LiveFeed

    f = LiveFeed(root, follow=False)
    list(f.events())
    out = {"learned": [[k, key] for k, key, *_ in f.drain_learned()], "new_pools": list(f.new_pools),
           "without_creation": f.stats["pools_seen_without_creation"]}
    f.close()
    return out


def test_the_learned_order_is_first_slot_then_key(tmp_path):
    root = str(tmp_path / "rec")
    recording(root)
    got = learned(root)
    by_slot = [m for m, _ in sorted(MINTS, key=lambda x: (x[1], x[0]))]
    pools = [p for p, _ in sorted(POOLS[:2], key=lambda x: (x[1], x[0]))]
    # completions (slot order), then migrations (slot order), then CreatePoolEvent's pools (pool_creations' pools are not
    # learned: M1); the epoch's first block last (segment_events learns it after _new_tokens_and_pools)
    want = [["token", m] for m in by_slot] * 2 + [["pool", p] for p in pools] + [["epoch", "2"]]
    assert got["learned"] == want
    assert got["new_pools"] == pools and got["without_creation"] == len(POOLS) - 2


def test_fresh_processes_learn_in_one_order(tmp_path):
    root = str(tmp_path / "rec")
    recording(root)
    code = ("import json, sys; sys.path.insert(0, sys.argv[1]); from paper.tests.test_live_learn_order import learned; "
            "print(json.dumps(learned(sys.argv[2])))")
    outs = {subprocess.run([sys.executable, "-c", code, LAB, root], capture_output=True, text=True, check=True).stdout
            for _ in range(8)}
    assert len(outs) == 1, outs
    assert json.loads(outs.pop()) == learned(root)
