"""Follow the money on a mini store: user-side cash flow per venue, the per-transaction fee, the mark with
its depth cap, the daily mark-to-market that closes across days, the carried open position, the position
features (snipe, hold) and the side tables."""

import json
import os

import duckdb
import pytest

from wallets import money

SLOT0 = 446_256_000
SOL = 10**9
DAY_SLOTS = 216_000  # 400 ms per slot -> 86400 s


def _store(path):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE meta (json VARCHAR)")
    con.execute("INSERT INTO meta VALUES (?)", [json.dumps({"slot_start": SLOT0, "slot_end_exclusive": SLOT0 + 432_000, "ms_per_slot": {str(SLOT0 // 432_000): 400.0}})])
    con.execute("CREATE TABLE blocks (slot BIGINT, block_time BIGINT)")
    con.execute(f"INSERT INTO blocks SELECT {SLOT0} + i, floor(1789084800 + i * 0.4)::BIGINT FROM range(0, 3 * {DAY_SLOTS}) t(i)")  # 2026-09-11 00:00 UTC + 3 days
    con.execute("CREATE TABLE mints (mint VARCHAR, create_slot BIGINT, creator VARCHAR, quote_mint VARCHAR)")
    con.execute("INSERT INTO mints VALUES ('T', ?, 'CR', 'So11111111111111111111111111111111111111112'), ('U', ?, 'CR2', 'So11111111111111111111111111111111111111112')",
                [SLOT0 + 10, SLOT0 + 20])
    con.execute("CREATE TABLE pools (pool VARCHAR, quote_class VARCHAR, orientation VARCHAR, mint VARCHAR, create_slot BIGINT, b0 BIGINT, q0 BIGINT)")
    con.execute("INSERT INTO pools VALUES ('P', 'sol', 'normal', 'T', ?, 10**12, 100 * 10**9), ('O', 'other', 'normal', 'U', ?, 10**12, 0)", [SLOT0 + 50, SLOT0 + 60])
    con.execute("CREATE TABLE pool (kind VARCHAR, pool VARCHAR, slot BIGINT, tx_index BIGINT, trader VARCHAR, fee_payer VARCHAR, token_buy BOOLEAN, "
                "sol_amount BIGINT, token_amount BIGINT, farmer BOOLEAN, b BIGINT, e BIGINT, vault BIGINT, base BIGINT, e_delta BIGINT, lp_bps BIGINT, "
                "tx_fee BIGINT, priority_fee BIGINT, jito_tip BIGINT, quote_user BIGINT, vq BIGINT)")
    d1 = SLOT0 + DAY_SLOTS
    rows = [
        # day 1: W1 buys 1 SOL of T in pool P (pre b 1e12, e 100 SOL), two legs in one tx (fee counted once), pool fee 1 %
        ("buy", "P", SLOT0 + 100, 7, "W1", None, True, 1 * SOL, 10**10, False, 10**12, 100 * SOL, 100 * SOL, 10**10, 990_000_000, 20, 5000, 1000, 0),
        ("buy", "P", SLOT0 + 100, 7, "W1", None, True, 1 * SOL, 10**10, False, 99 * 10**10, 100990 * SOL // 1000, 100990 * SOL // 1000, 10**10, 990_000_000, 20, 5000, 1000, 0),
        # day 1: W2 (farmer-flagged, fee paid by X) buys 0.5 SOL and sells it all 10 slots later for 0.4 SOL
        ("buy", "P", SLOT0 + 200, 1, "W2", "X", True, SOL // 2, 5 * 10**9, True, 98 * 10**10, 101980 * SOL // 1000, 101980 * SOL // 1000, 5 * 10**9, 495_000_000, 20, 5000, 0, 0),
        ("sell", "P", SLOT0 + 210, 1, "W2", "X", False, 4 * SOL // 10, 5 * 10**9, True, 975 * 10**9, 102475 * SOL // 1000, 102475 * SOL // 1000, 5 * 10**9, -404_000_000, 20, 5000, 0, 0),
        # day 2: the price of T collapses: W3 dumps; W1 still holds 2e10 tokens
        ("sell", "P", d1 + 50, 1, "W3", None, False, 60 * SOL, 5 * 10**11, False, 98 * 10**10, 102071 * SOL // 1000, 102071 * SOL // 1000, 5 * 10**11, -60_600_000_000, 20, 5000, 0, 0),
        # pool with another quote: no SOL price, left out
        ("buy", "O", SLOT0 + 300, 1, "W1", None, True, 0, 10**9, False, 10**12, 0, 0, 10**9, 0, 20, 5000, 0, 0),
    ]
    con.executemany("INSERT INTO pool VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)", [r + (r[7],) for r in rows])  # quote_user = sol_amount, vq 0
    # day 3: the LP of P withdraws 20 SOL (proportional) after the last trade, from the day-2 post state (b 1.48e12, vault 41.471 SOL):
    # the mark's depth falls to 21.471 SOL, the price stays 0.028, external_in = -20
    con.execute("INSERT INTO pool VALUES ('withdraw', 'P', ?, 1, 'LP', NULL, false, 0, 0, false, 1480 * 10**9, 41471 * 10**6, 41471 * 10**6, 713750 * 10**6, NULL, 20, 5000, 0, 0, 20 * 10**9, 0)",
                [d1 + DAY_SLOTS + 10])
    con.execute("CREATE TABLE curve (mint VARCHAR, slot BIGINT, tx_index BIGINT, trader VARCHAR, fee_payer VARCHAR, is_buy BOOLEAN, t BIGINT, q BIGINT, fee BIGINT, "
                "creator_fee BIGINT, vt BIGINT, vq BIGINT, rt BIGINT, rq BIGINT, tx_fee BIGINT, priority_fee BIGINT, jito_tip BIGINT)")
    # day 1: sniper S buys U on the curve 2 slots after the create (paid q + fees), day 2 sells half
    con.executemany("INSERT INTO curve VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
        ("U", SLOT0 + 22, 1, "S", None, True, 10**12, 2 * SOL, 20_000_000, 6_000_000, 10**15, 32 * SOL, 79 * 10**13, 2 * SOL, 5000, 0, 1000),
        ("U", d1 + 10, 1, "S", None, False, 5 * 10**11, 15 * SOL // 10, 15_000_000, 4_500_000, 10005 * 10**11, 305 * SOL // 10, 7905 * 10**11, SOL // 2, 5000, 0, 0)])
    con.close()


def test_money_on_the_mini_store(tmp_path):
    _store(tmp_path / "s.duckdb")
    info = money.run(SLOT0, SLOT0 + 3 * DAY_SLOTS, str(tmp_path / "out"), buckets=1, store=str(tmp_path / "s.duckdb"))
    con = duckdb.connect()
    wd = {(r[0], r[1]): dict(zip([c[0] for c in con.execute(f"SELECT * FROM '{tmp_path / 'out' / 'wallet-day-0.parquet'}' LIMIT 0").description], r))
          for r in con.execute(f"SELECT * FROM '{tmp_path / 'out' / 'wallet-day-0.parquet'}'").fetchall()}
    assert info["wallets"] == 4 and info["marks"] == 6 and info["days"] == ["2026-09-11", "2026-09-12", "2026-09-13"]  # T and U x 3 days (carried)
    # W1 day 1: paid 2 SOL (two legs of one tx), fee once (6000), inventory 2e10 tokens marked at the day's last price (W2's sell state)
    w1 = wd[("W1", "2026-09-11")]
    assert w1["cash"] == -2 * SOL and w1["fees"] == 6000 and w1["n_legs"] == 2 and w1["n_opened"] == 1
    price_d1 = (102475 * SOL // 1000 - 404_000_000) / (975 * 10**9 + 5 * 10**9)
    assert w1["value_end"] == pytest.approx(2 * 10**10 * price_d1) and w1["pnl"] == pytest.approx(-2 * SOL - 6000 + 2 * 10**10 * price_d1)
    # W1 day 2: no trade, the position is carried and marked at the collapsed price; the fall is day 2's loss
    w1b = wd[("W1", "2026-09-12")]
    price_d2 = (102071 * SOL // 1000 - 60_600_000_000) / (98 * 10**10 + 5 * 10**11)
    assert w1b["n_legs"] == 0 and w1b["value_end"] == pytest.approx(2 * 10**10 * price_d2) and w1b["pnl"] < 0
    assert ("W1", "2026-09-13") in wd and wd[("W1", "2026-09-13")]["value_end"] == pytest.approx(w1b["value_end"], rel=1e-4)  # carried, price unchanged by the withdraw
    # W2: round trip inside day 1, farmer legs, fee paid by X, hold 10 slots
    w2 = wd[("W2", "2026-09-11")]
    assert w2["cash"] == -SOL // 2 + 4 * SOL // 10 and w2["n_farmer_legs"] == 2 and w2["paid_by_other"] == 2 and w2["value_end"] == 0
    assert w2["n_closed"] == 1 and w2["median_hold_slots"] == 10 and w2["fees"] == 10000
    # S: sniper (2 slots after the create), user side = q + fees on the buy
    s1 = wd[("S", "2026-09-11")]
    assert s1["n_snipe"] == 1 and s1["cash"] == -(2 * SOL + 26_000_000) and s1["fees"] == 6000
    s2 = wd[("S", "2026-09-12")]
    assert s2["cash"] == 15 * SOL // 10 - 19_500_000 and s2["n_closed"] == 0  # half sold: not closed (> 10 % left)
    # the other-quote pool is not counted
    assert all(k[0] != "W1" or r["n_mints"] <= 1 for k, r in wd.items())
    # the mark is capped at the venue's real SOL
    marks = con.execute(f"SELECT mint, day, price, depth, venue, traded FROM '{tmp_path / 'out' / 'marks.parquet'}' ORDER BY 1, 2").fetchall()
    assert [m[:2] for m in marks] == [("T", d) for d in info["days"]] + [("U", d) for d in info["days"]] and marks[3][4] == "curve"
    assert marks[2][2] == pytest.approx(marks[1][2], rel=1e-4) and marks[2][5] and marks[1][5]  # day 3: the withdraw keeps the price, counts as an event
    assert marks[2][3] == 21471 * 10**6 and marks[1][3] == 41471 * 10**6  # and lowers the depth by the 20 SOL withdrawn
    side = con.execute(f"SELECT * FROM '{tmp_path / 'out' / 'payers.parquet'}'").fetchall()
    assert side == [("X", "W2", "T", 2)]
    assert con.execute(f"SELECT count(*) FROM '{tmp_path / 'out' / 'creators.parquet'}'").fetchone()[0] == 2
    flows = dict((r[0], r) for r in con.execute(f"SELECT venue, venue_fees, network_fees, cash_all, sol_start, sol_end, external_in FROM '{tmp_path / 'out' / 'venue_flows.parquet'}'").fetchall())
    assert flows["curve"][1] == 26_000_000 + 19_500_000 and flows["pool"][2] == 6000 + 5000 + 5000 + 5000
    v, c, s0, s1, ext = flows["pool"][1], flows["pool"][3], flows["pool"][4], flows["pool"][5], flows["pool"][6]
    assert ext == -20 * SOL and s0 == 100 * SOL and s1 == 21471 * 10**6  # the opening deposit is the first trade's pre-depth; the LP withdrawal is external
    assert abs(c + v + (s1 - s0) - ext) < 1e-6  # the books close on P


if __name__ == "__main__":
    pytest.main([__file__])
