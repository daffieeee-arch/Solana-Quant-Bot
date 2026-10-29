"""wallets.regime_inputs on synthetic recorder segments: the window from block times, segment gaps and coverage, the
recorder counts per hour, E67's probe pools, E68's store-shaped reversed-pool rows (boost buys out, backfill after the
main rows without overlap, history before the window), and the refusals. The trigger route itself is proven on real data:
the explore window reproduces wallets/regime-1064-inputs/live-triggers-explore.parquet and probe-pools-explore.parquet."""

import datetime
import json
import os

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from wallets import regime_inputs as R

W = R.W
SOL = 10**9
S0 = 460_600_000
BT0 = 1_793_000_000  # 2,000 s past a UTC hour; 0.4 s a slot below


def put(seg, table, rows):
    d = os.path.join(seg, table)
    os.makedirs(d, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), os.path.join(d, "part-00000.parquet"))


def trade(kind, pool, slot, tx, user, base, quote, b, vault, sig, disc=None, base_mint=W, quote_mint="TOK"):
    r = {"pool": pool, "slot": slot, "tx_index": tx, "outer_ix": 0, "inner_ix": 0, "user": user, "ix_base_mint": base_mint,
         "ix_quote_mint": quote_mint, "coin_creator": "CC", "parent_ix_disc": disc, "pool_base_token_reserves": b,
         "pool_quote_token_reserves": vault, "virtual_quote_reserves": "0", "signature": sig}
    if kind == "buy":
        r.update(base_amount_out=base, quote_amount_in=quote, user_quote_amount_in=quote)
    else:
        r.update(base_amount_in=base, user_quote_amount_out=quote)
    return r


def segment(root, seq, first, last, level=0, complete=True, blocks=True, written=None):
    d = os.path.join(root, f"{seq:09d}")
    os.makedirs(d, exist_ok=True)
    if blocks:
        put(d, "blocks", [{"slot": s, "block_time": BT0 + (s - S0) * 2 // 5, "skipped": False} for s in range(first, last + 1)])
    with open(os.path.join(d, "_segment.json"), "w") as f:
        json.dump({"seq": seq, "first_slot": first, "last_slot": last, "written_unix": written or BT0 + (last - S0) * 2 // 5,
                   "complete": complete, "slots_closed": last - first + 1, "reconnects": [],
                   "filter": {"level": level, "backfill": True, "new_reversed": True, "pump_creates": True, "tx_pools": 1, "tx_probes": 1,
                              "new_vaults": 0, "dropped": []}}, f)
    return d


@pytest.fixture
def root(tmp_path):
    """Three segments of 10,000 slots (400 ms a slot: about 66 min each) with a gap of 100 slots before the third; the
    second at filter level 1. Pool P (reversed, probed) trades at 100 then 130 SOL depth; pool Q (reversed, not probed)."""
    r = str(tmp_path / "segments")
    a = segment(r, 1, S0, S0 + 9_999)
    b = segment(r, 2, S0 + 10_000, S0 + 19_999, level=1)
    c = segment(r, 3, S0 + 20_100, S0 + 29_999)
    os.makedirs(os.path.join(r, "000000004"))  # still open: no _segment.json
    put(a, "pump_amm/CreatePoolEvent", [{"pool": "P", "slot": S0 + 5, "tx_index": 0, "base_mint": W, "quote_mint": "TOK", "coin_creator": "CC",
                                         "is_mayhem_mode": False, "signature": "cp1"},
                                        # a second migration whose transaction also matched tx_new_reversed: filed there (E80)
                                        {"pool": "M", "slot": S0 + 7, "tx_index": 0, "base_mint": "TOKM", "quote_mint": W, "coin_creator": "CC",
                                         "is_mayhem_mode": False, "signature": "cp2"}])
    put(a, "pump/CompletePumpAmmMigrationEvent", [{"mint": "TOK", "pool": "P", "slot": S0 + 5, "tx_index": 0, "signature": "cp1"},
                                                  {"mint": "TOKM", "pool": "M", "slot": S0 + 7, "tx_index": 0, "signature": "cp2"}])
    put(a, "arrivals", [{"slot": S0 + 5, "tx_index": 0, "signature": "cp1", "stream": "tx_new_pools"},
                        {"slot": S0 + 7, "tx_index": 0, "signature": "cp2", "stream": "tx_new_reversed"},
                        {"slot": S0 + 10, "tx_index": 1, "signature": "t1", "stream": "tx_probes"},
                        {"slot": S0 + 20, "tx_index": 1, "signature": "t2", "stream": "tx_probes"}])
    put(a, "pump/CreateEvent", [{"mint": "TOK", "slot": S0 + 1, "tx_index": 0, "user": "CR", "is_mayhem_mode": False, "quote_mint": None},
                                {"mint": "TOK2", "slot": S0 + 9_500, "tx_index": 0, "user": "CR", "is_mayhem_mode": False, "quote_mint": None}])
    put(a, "pump_amm/BuyEvent", [
        trade("buy", "P", S0 + 10, 1, "U1", 1 * SOL, 10**9, 100 * SOL, 10**15, "t1"),  # depth 100 SOL: below 120
        trade("buy", "P", S0 + 20, 1, "U2", 1 * SOL, 10**9, 130 * SOL, 10**15, "t2"),  # depth 130 SOL: t0
        trade("buy", "P", S0 + 30, 1, "U3", 1 * SOL, 10**9, 140 * SOL, 10**15, "t3", disc=R.BOOST_BUY_DISC),  # a boost inner buy
        trade("buy", "Q", S0 + 40, 1, "U4", 1 * SOL, 10**9, 200 * SOL, 10**15, "t4"),  # not probed
        trade("buy", "N", S0 + 50, 1, "U5", 10**9, 1 * SOL, 10**12, 100 * SOL, "t5", base_mint="TOKN", quote_mint=W)])  # a normal pool
    put(a, "pump_amm/SellEvent", [trade("sell", "P", S0 + 60, 2, "U1", 2 * SOL, 10**9, 128 * SOL, 10**15, "t6")])
    put(b, "pump_amm/SellEvent", [trade("sell", "Q", S0 + 15_000, 1, "U4", 1 * SOL, 10**9, 199 * SOL, 10**15, "t7")])
    put(c, "pump_amm/BuyEvent", [trade("buy", "P", S0 + 25_000, 1, "U6", 1 * SOL, 10**9, 131 * SOL, 10**15, "t8")])
    # backfill: a copy of a main row (dropped) and an earlier row (kept as history)
    put(a, "backfill/pump_amm/BuyEvent", [trade("buy", "P", S0 + 10, 1, "U1", 1 * SOL, 10**9, 100 * SOL, 10**15, "t1"),
                                          trade("buy", "P", S0 + 3, 1, "U0", 1 * SOL, 10**9, 99 * SOL, 10**15, "t0")])
    return r


def args(root, out, **kw):
    a = ["--segments", root, "--label", "test", "--out", str(out), "--memory", "512MB", "--threads", "1"]
    if "utc" in kw:
        a += ["--from-utc", kw["utc"][0], "--to-utc", kw["utc"][1]]
    else:
        lo, hi = kw.get("slots", (S0, S0 + 30_000))
        a += ["--from-slot", str(lo), "--to-slot", str(hi)]
    return a


def test_the_inputs_on_synthetic_segments(root, tmp_path):
    out = tmp_path / "out"
    assert R.main(args(root, out)) == 0
    info = json.load(open(out / "info.json"))
    assert (info["segments_in_window"], info["segments_still_open"], info["segments_not_level0"]) == (3, 1, 1)
    assert info["slot_gaps"] == [[S0 + 20_000, S0 + 20_100]]
    assert info["history_gaps"] == [[S0 - R.HISTORY_SLOTS, S0]] and info["history_complete"] is False  # nothing before S0
    # E68's store-shaped rows: reversed pools only, the boost buy out, the backfill copy dropped, its earlier row kept as history
    con = duckdb.connect()
    sums = dict(l.split("  ")[::-1] for l in open(out / "SHA256SUMS").read().splitlines())
    assert set(sums) == {"live-triggers-test.parquet", "probe-pools-test.parquet", "segments.parquet", "coverage-hourly.parquet",
                         "counts-hourly.parquet", "recorder-counts.json", "info.json"}
    assert info["triggers"]["raw_reversed_trades"] == 7 and info["triggers"]["raw_backfill"] == 1 and info["triggers"]["raw_pools"] == 2
    trig = pq.read_table(out / "live-triggers-test.parquet")
    assert trig.column_names == list(R.TRIG_COLS)
    # E67's probe pool: P only (Q never on tx_probes), t0 at the first trade with >= 120 SOL on its SOL side (the trade
    # at 100 SOL does not count; first_seen is E67's min(slot) over the same rows)
    p0 = con.execute(f"SELECT * FROM '{out / 'probe-pools-test.parquet'}'").fetchall()
    assert [(r[0], r[1], r[3]) for r in p0] == [("P", S0 + 20, S0 + 20)]
    assert p0[0][2] == pytest.approx(130 * SOL / 10**15)
    assert [f.name for f in pq.read_schema(out / "probe-pools-test.parquet")] == ["pool", "t0", "p0", "first_seen"]
    assert str(pq.read_schema(out / "probe-pools-test.parquet").field("t0").type) == "uint64"
    # recorder counts: blocks, creates, graduations (CompletePumpAmmMigrationEvent: both migrations, whichever stream filed the
    # transaction), pool creations filed on tx_new_pools (only cp1: cp2 sits on tx_new_reversed), buy + sell rows seen (every pool)
    rc = json.load(open(out / "recorder-counts.json"))["window"]
    assert (rc["blocks"], rc["creates_mints"], rc["graduations_pools"], rc["pool_creations_on_tx_new_pools"], rc["pump_amm_buy_sell_rows_seen"]) == (29_900, 2, 2, 1, 8)
    hourly = con.execute(f"SELECT sum(blocks), sum(creates_mints), sum(graduations_pools), sum(pool_creations_on_tx_new_pools), sum(pump_amm_buy_sell_rows_seen) FROM '{out / 'counts-hourly.parquet'}'").fetchone()
    assert hourly == (29_900, 2, 2, 1, 8)
    cov = con.execute(f"SELECT sum(blocks), min(level0_share), max(level0_share) FROM '{out / 'coverage-hourly.parquet'}'").fetchone()
    assert cov[0] == 29_900 and 0 < cov[1] < 1 and cov[2] == 1.0  # the level-1 segment lowers its hours' share


def test_the_window_from_utc_is_the_first_block_at_or_after_each_time(root, tmp_path):
    lo_t, hi_t = BT0 + 400, BT0 + 8_000  # slots 1,000 and 20,000 (2 s per 5 slots); 20,000-20,099 have no block (the gap)
    iso = lambda t: datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    assert R.main(args(root, tmp_path / "o", utc=(iso(lo_t), iso(hi_t)))) == 0
    assert json.load(open(tmp_path / "o" / "info.json"))["window_slots"] == [S0 + 1_000, S0 + 20_100]


def test_refusals_write_nothing(root, tmp_path, capsys):
    (tmp_path / "exists").mkdir()
    assert R.main(args(root, tmp_path / "exists")) == 2
    assert R.main(args(root, os.path.join(root, "out"))) == 2  # never inside the segments
    assert R.main(args(root, tmp_path / "late", slots=(S0, S0 + 40_000))) == 2  # the segments end before the window does
    assert R.main(args(root, tmp_path / "empty", slots=(S0 + 10, S0 + 10))) == 2
    err = capsys.readouterr().err
    assert err.count("REFUSED") == 4 and "too early" in err
    assert not (tmp_path / "late").exists() and not (tmp_path / "empty").exists() and not os.path.exists(os.path.join(root, "out"))


def test_the_journal_comparison_reads_f7_signals_in_the_window(root, tmp_path):
    import sqlite3
    j = tmp_path / "journal.sqlite"
    db = sqlite3.connect(j)
    db.execute("CREATE TABLE signals (family TEXT, key TEXT, t INTEGER, trigger_trader TEXT, depth INTEGER, farm TEXT, status TEXT, orientation TEXT, pool_seen_slot INTEGER)")
    db.executemany("INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?)", [("F7", "P", S0 + 60, "U1", 128 * SOL, "organic", "entered", "reversed", S0),
                                                                      ("F7", "P", S0 + 60, "U1", 128 * SOL, "organic", "entered", "reversed", S0),  # a second variant
                                                                      ("F7", "Q", S0 + 40_000, "U4", 0, "organic", "x", "reversed", S0),  # outside the window
                                                                      ("K", "P", S0 + 70, "U1", 0, "organic", "x", "reversed", S0)])
    db.commit()
    db.close()
    assert R.main(args(root, tmp_path / "o") + ["--journal", str(j)]) == 0
    jc = json.load(open(tmp_path / "o" / "info.json"))["journal_comparison"]
    assert jc["journal_f7_signals"] == 1 and sum(jc["reversed_pairs"].values()) >= 1
    assert pq.read_table(tmp_path / "o" / "journal-f7-test.parquet").num_rows == 1


def test_p0_is_the_first_leg_of_a_transaction_with_two(tmp_path):
    """Two qualifying legs in one transaction (slot, tx_index): p0 is the first leg's price, not either (E67 ordered by
    slot and tx_index only: 3 of the explore window's 1,282 probe pools got the other leg's price)."""
    r = str(tmp_path / "segments")
    a = segment(r, 1, S0, S0 + 1_499)  # >= MIN_CLOCK_SLOTS of blocks for the clock
    put(a, "arrivals", [{"slot": S0 + 10, "tx_index": 4, "signature": "tx", "stream": "tx_probes"}])
    legs = [dict(trade("buy", "P", S0 + 10, 4, "U", 1 * SOL, 10**9, b * SOL, 10**15, "tx"), outer_ix=o) for o, b in ((14, 160), (8, 150))]
    put(a, "pump_amm/BuyEvent", legs)
    put(a, "pump_amm/SellEvent", [trade("sell", "P", S0 + 20, 1, "U", 1 * SOL, 10**9, 149 * SOL, 10**15, "s")])
    put(a, "pump_amm/CreatePoolEvent", [{"pool": "P", "slot": S0 + 1, "tx_index": 0, "base_mint": W, "quote_mint": "TOK", "coin_creator": "CC",
                                         "is_mayhem_mode": False, "signature": "cp"}])
    assert R.main(args(r, tmp_path / "o", slots=(S0, S0 + 1_500))) == 0
    ((pool, t0, p0, first),) = duckdb.connect().execute(f"SELECT * FROM '{tmp_path / 'o' / 'probe-pools-test.parquet'}'").fetchall()
    assert (pool, t0, first) == ("P", S0 + 10, S0 + 10) and p0 == pytest.approx(150 * SOL / 10**15)  # outer_ix 8 before 14


def test_a_segment_missing_a_table_its_meta_counts_is_refused(root, tmp_path, capsys):
    """An incomplete copy: segment 3's _segment.json counts a BuyEvent row, its file is gone (rsync writes a directory's
    own files first). Refused, not counted low."""
    seg3 = os.path.join(root, "000000003")
    meta = json.load(open(os.path.join(seg3, "_segment.json")))
    meta["rows"] = {"pump_amm/BuyEvent": 1, "blocks": 9_900}
    json.dump(meta, open(os.path.join(seg3, "_segment.json"), "w"))
    assert R.main(args(root, tmp_path / "ok")) == 0  # the file is there: counted as before
    for f in os.listdir(os.path.join(seg3, "pump_amm", "BuyEvent")):
        os.remove(os.path.join(seg3, "pump_amm", "BuyEvent", f))
    assert R.main(args(root, tmp_path / "o")) == 2
    assert "segment 3: _segment.json counts 1 pump_amm/BuyEvent rows but the file is missing" in capsys.readouterr().err
    assert not (tmp_path / "o").exists()


def test_a_start_the_segments_do_not_cover_is_refused(root, tmp_path, capsys):
    iso = lambda t: datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    assert R.main(args(root, tmp_path / "o", utc=(iso(BT0 - 3_600), iso(BT0 + 8_000)))) == 2  # an hour before the first block
    assert "do not cover the window's start" in capsys.readouterr().err and not (tmp_path / "o").exists()


def test_an_epoch_piece_at_the_window_edge_does_not_set_the_clock(tmp_path):
    """Per-epoch clock: an epoch with fewer than MIN_CLOCK_SLOTS slots of blocks (here 3 at the window's end) is left out,
    so the Clock carries the epoch before it (no NaN, no 0, no noisy value)."""
    E = (S0 // R.EPOCH_SLOTS + 1) * R.EPOCH_SLOTS
    r = str(tmp_path / "segments")
    a = segment(r, 1, E - 3_000, E - 1)
    segment(r, 2, E, E + 2)
    put(a, "pump_amm/CreatePoolEvent", [{"pool": "P", "slot": E - 2_900, "tx_index": 0, "base_mint": W, "quote_mint": "TOK", "coin_creator": "CC",
                                         "is_mayhem_mode": False, "signature": "cp"}])
    put(a, "arrivals", [{"slot": E - 2_800, "tx_index": 1, "signature": "t", "stream": "tx_probes"}])
    put(a, "pump_amm/BuyEvent", [trade("buy", "P", E - 2_800, 1, "U", 1 * SOL, 10**9, 130 * SOL, 10**15, "t")])
    put(a, "pump_amm/SellEvent", [trade("sell", "P", E - 2_700, 1, "U", 1 * SOL, 10**9, 129 * SOL, 10**15, "s")])
    assert R.main(args(r, tmp_path / "o", slots=(E - 2_000, E + 3))) == 0
    assert list(json.load(open(tmp_path / "o" / "info.json"))["ms_per_slot"]) == [str(E // R.EPOCH_SLOTS - 1)]
