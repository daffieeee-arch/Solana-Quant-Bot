"""x10 stage B on synthetic stage-A tables: the early-buyer history counts only what is known before the entry slot and
leaves the entry's own coin out, the creator history likewise, the money-v2 labels come from the day before, the market
context from the hour before, and the purge flags follow the block boundaries."""
import datetime
import json

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from wallets.x10 import features as F

E = 1040
LO = E * 432_000
S = LO + 1_000
T0 = 1_790_000_000
MS = 400.0
H72 = round(72 * 3_600_000 / MS)


def _store(path):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE meta (json VARCHAR)")
    con.execute("INSERT INTO meta VALUES (?)", [json.dumps({"ms_per_slot": {str(E): MS}})])
    con.execute("CREATE TABLE blocks (slot UBIGINT, block_time BIGINT)")
    con.execute(f"INSERT INTO blocks SELECT r, {T0} + floor((r - {S}) * {MS} / 1000)::BIGINT FROM range({S - 20_000}, {S + 440_000}) t(r)")
    con.execute("CREATE TABLE mints (mint VARCHAR, create_slot UBIGINT, creator VARCHAR, migrate_slot UBIGINT)")
    con.execute("INSERT INTO mints VALUES ('A', ?, 'CR', ?), ('B', ?, 'CR', NULL), ('C', ?, 'CR', NULL), ('D', ?, 'OTHER', NULL), ('X', ?, 'Z', NULL)",
                [S + 100, S + 300, S + 1000, S + 5000, S + 2000, S - 9_500])   # X: a create in the hour before the entry hour
    con.execute("CREATE TABLE curve (mint VARCHAR, slot UBIGINT, is_buy BOOLEAN, q HUGEINT)")
    # the entry hour starts at T0 + 3600 k; entries at S + 5010 (2004 s) lie in hour T0 // 3600 ... build buys one hour earlier
    con.execute("INSERT INTO curve VALUES ('X', ?, true, 1000000000), ('X', ?, true, 3000000000), ('X', ?, false, 5000000000), ('A', ?, true, 7000000000)",
                [S - 9_500, S - 9_400, S - 9_300, S + 150])
    con.close()


def _write(path, rows):
    pq.write_table(pa.Table.from_pylist(rows), path)


def test_families_are_point_in_time(tmp_path):
    _store(tmp_path / "s.duckdb")
    lab = tmp_path / "labels"
    lab.mkdir()
    day = datetime.date(2026, 9, 25)
    base = {"key": "C", "mint": "C", "creator": "CR", "create_slot": S + 5000, "epoch": E, "h6_slots": H72, "day": day, "cost_sol": 0.5}
    entries = [base | {"moment": "e10", "t": S + 5005, "entry_slot": S + 5010},
               base | {"moment": "c2", "t": S + 5060, "entry_slot": S + 5100},
               base | {"key": "D", "mint": "D", "creator": "OTHER", "create_slot": S + 2000, "moment": "e10", "t": S + 2005, "entry_slot": S + 430_000}]  # near the block end
    _write(lab / f"labels-{LO}.parquet", entries)
    _write(lab / f"labels-{LO}-s2.parquet", entries)   # the 2 SOL file must be ignored
    _write(lab / f"mint_peaks-{LO}.parquet", [
        {"mint": "A", "create_slot": S + 100, "creator": "CR", "first_x2_slot": S + 150, "first_x5_slot": S + 200, "first_x10_slot": None, "peak_mult": 6.0},
        {"mint": "B", "create_slot": S + 1000, "creator": "CR", "first_x2_slot": S + 1100, "first_x5_slot": None, "first_x10_slot": None, "peak_mult": 2.5},
        {"mint": "C", "create_slot": S + 5000, "creator": "CR", "first_x2_slot": S + 5020, "first_x5_slot": S + 5050, "first_x10_slot": None, "peak_mult": 7.0},
        {"mint": "D", "create_slot": S + 2000, "creator": "OTHER", "first_x2_slot": None, "first_x5_slot": None, "first_x10_slot": None, "peak_mult": 1.1}])
    _write(lab / f"early_buyers-{LO}.parquet", [
        {"mint": "A", "create_slot": S + 100, "wallet": "W1", "first_buy_slot": S + 101, "buy_sol": 1.0},
        {"mint": "A", "create_slot": S + 100, "wallet": "W3", "first_buy_slot": S + 102, "buy_sol": 1.0},
        {"mint": "B", "create_slot": S + 1000, "wallet": "W1", "first_buy_slot": S + 1001, "buy_sol": 1.0},
        {"mint": "C", "create_slot": S + 5000, "wallet": "W1", "first_buy_slot": S + 5001, "buy_sol": 1.0},
        {"mint": "C", "create_slot": S + 5000, "wallet": "W2", "first_buy_slot": S + 5002, "buy_sol": 1.0},
        {"mint": "C", "create_slot": S + 5000, "wallet": "W4", "first_buy_slot": S + 5030, "buy_sol": 1.0}])   # after the e10 moment: not among its buyers
    money = tmp_path / "money"
    money.mkdir()
    _write(money / "wallet-day-0.parquet", [
        # wallet-day buy_sol is lamports (money.py); W1 bought 100 SOL, W4 49.999999999 SOL (one lamport under the 50 SOL line)
        {"wallet": "W1", "day": day - datetime.timedelta(days=1), "ms_per_slot": MS, "n_opened": 60, "median_hold_slots": 10, "n_snipe": 0, "buy_sol": 100 * 10**9},
        {"wallet": "W4", "day": day - datetime.timedelta(days=1), "ms_per_slot": MS, "n_opened": 1, "median_hold_slots": 10, "n_snipe": 0, "buy_sol": 50 * 10**9 - 1},
        {"wallet": "W2", "day": day, "ms_per_slot": MS, "n_opened": 60, "median_hold_slots": 10, "n_snipe": 3, "buy_sol": 1.0}])   # the entry day itself: must not be used
    (money / "SHA256SUMS.wallet-days").write_text("x\n")
    info = F.run(str(lab), str(money), str(tmp_path / "out"), store=str(tmp_path / "s.duckdb"))
    rows = duckdb.connect().execute(f"SELECT * FROM '{tmp_path}/out/features.parquet' ORDER BY entry_slot").arrow().read_all().to_pylist()
    assert info["entries"] == 3 and len(rows) == 3 and info["windows"] == 1
    e1, e2, e3 = rows
    # e10 on C at entry S + 5010: buyers W1 and W2 (W4 bought after t). W1 knows: early buys A, B (C itself taken out) = 2;
    # hits: A (x5 at S + 200 < entry) = 1, C's own x5 (S + 5050) lies after the entry and is its own coin anyway
    assert e1["eb_n"] == 2 and e1["eb_hits"] == 1 and abs(e1["eb_hit_share"] - 0.5) < 1e-12 and abs(e1["eb_experience"] - 1.0) < 1e-12
    assert abs(e1["eb_hit_rate"] - 0.5) < 1e-12 and e1["eb_max_hits"] == 1   # W2 has no earlier early buy: its rate is NULL and drops out of the mean
    # c2 on C at entry S + 5100: C's x5 (S + 5050) is now known, but it is the entry's own coin: still 1 hit for W1
    assert e2["eb_n"] == 3 and e2["eb_hits"] == 1   # W4 (first buy S + 5030 <= t) is a buyer now
    # money-v2 on the day before: W1 bot + heavy, W2 only has a row on the entry day itself (ignored), W4 not among e1's buyers
    assert abs(e1["eb_active_share"] - 0.5) < 1e-12 and abs(e1["eb_bot_share"] - 0.5) < 1e-12 and e1["eb_sniper_share"] == 0 and abs(e1["eb_heavy_share"] - 0.5) < 1e-12
    # c2's buyers W1, W2, W4: only W1 is heavy; W4's 49.999999999 SOL (in lamports) is not, and a wallet-day written in SOL units
    # (the old fixture's 100.0) would be 100 lamports and never heavy (Codex finding #2, 2026-10-24)
    assert abs(e2["eb_heavy_share"] - 1 / 3) < 1e-12 and abs(e2["eb_active_share"] - 2 / 3) < 1e-12
    # creator CR at entry S + 5010: coins known A, B (C out) = 2; x2 known A, B = 2 (C's x2 at S + 5020 is later); x5 A = 1; migrations A = 1
    assert e1["cr_prior_coins"] == 2 and e1["cr_prior_x2"] == 2 and e1["cr_prior_x5"] == 1 and e1["cr_prior_mig"] == 1
    assert abs(e1["cr_x2_rate"] - 1.0) < 1e-12 and abs(e1["cr_days_since_prev"] - 4000 * MS / 86400000) < 1e-12
    # at entry S + 5100 C's own x2 (S + 5020) is known but taken out: still 2
    assert e2["cr_prior_x2"] == 2 and e2["cr_prior_x5"] == 1
    # creator OTHER: one coin (D itself), no history
    assert e3["cr_prior_coins"] == 0 and e3["cr_prior_x2"] == 0 and e3["cr_days_since_prev"] is None
    # market context: the hour before the entry's hour holds the two X buys (1 + 3 SOL, the sell does not count) and the X create
    assert abs(e1["mkt_buy_sol"] - 4.0) < 1e-9 and e1["mkt_buys"] == 2 and e1["mkt_creates"] == 1
    # purge: epoch 1040 is train; the entry at S + 430,000 has its 24 h window (216,000 slots) over the 1041 boundary
    assert all(r["block"] == "train" for r in rows) and e1["censored_24"] is False and e3["censored_24"] is True and e3["next_boundary"] == 1041 * 432_000
    assert info["censored_24"] == {"train": 1} and info["blocks"] == {"train": 3} and e1["censored_72"] is True   # 72 h = 648,000 slots: over the boundary


def test_heavy_is_judged_in_lamports(tmp_path):
    """Codex finding #2: a wallet-day's buy_sol is a lamport sum; a buyer counts as heavy from 50 SOL = 50e9 lamports on, not
    from 50 'units'. Three wallets around the line, read through the same features run."""
    _store(tmp_path / "s.duckdb")
    lab = tmp_path / "labels"
    lab.mkdir()
    day = datetime.date(2026, 9, 25)
    base = {"key": "C", "mint": "C", "creator": "CR", "create_slot": S + 5000, "epoch": E, "h6_slots": H72, "day": day, "cost_sol": 0.5}
    _write(lab / f"labels-{LO}.parquet", [base | {"moment": "c2", "t": S + 5060, "entry_slot": S + 5100}])   # buyers W1, W2, W4
    _write(lab / f"mint_peaks-{LO}.parquet", [{"mint": "C", "create_slot": S + 5000, "creator": "CR", "first_x2_slot": None, "first_x5_slot": None, "first_x10_slot": None, "peak_mult": 1.0}])
    _write(lab / f"early_buyers-{LO}.parquet", [{"mint": "C", "create_slot": S + 5000, "wallet": w, "first_buy_slot": S + 5001 + i, "buy_sol": 1.0} for i, w in enumerate(("W1", "W2", "W4"))])
    money = tmp_path / "money"
    money.mkdir()
    prev = day - datetime.timedelta(days=1)
    row = {"day": prev, "ms_per_slot": MS, "n_opened": 1, "median_hold_slots": 10, "n_snipe": 0}
    _write(money / "wallet-day-0.parquet", [row | {"wallet": "W1", "buy_sol": 50 * 10**9},        # exactly 50 SOL: heavy
                                            row | {"wallet": "W2", "buy_sol": 50 * 10**9 - 1},    # one lamport short: not heavy
                                            row | {"wallet": "W4", "buy_sol": 100.0}])            # 100 lamports (a SOL-unit mistake): not heavy
    (money / "SHA256SUMS.wallet-days").write_text("x\n")
    F.run(str(lab), str(money), str(tmp_path / "out"), store=str(tmp_path / "s.duckdb"))
    (r,) = pq.read_table(str(tmp_path / "out" / "features.parquet")).to_pylist()
    assert r["eb_n"] == 3 and abs(r["eb_active_share"] - 1.0) < 1e-12 and abs(r["eb_heavy_share"] - 1 / 3) < 1e-12
