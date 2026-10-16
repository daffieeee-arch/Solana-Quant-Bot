"""MEV phase 0 on a mini store and a mini tx_ctx chunk (spec v3, section 5): a same-venue arb over two
pools of one mint that is also a same-slot backrun and closes a gap, a cross-venue arb next to an
unparsed DEX that is a reachable backrun, transactions that are not arbs (a loss, a position that is
not flat), a sandwich and a front-run, failed attempts (by a winner, with a winner's program set,
neither), a newcomer on the second day, and the concentration measures on a hand-made distribution."""

import json
import os

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from wallets import mev0

SOL = 10**9
E = 1040
LO = E * 432_000
S = LO + 1_000  # first slot of the scenario
T0 = 1_790_000_000  # unix time of slot S (day 1); day 2 starts at slot S + 100
WSOL = mev0.WSOL
POOL_COLS = ("kind, pool, slot, tx_index, outer_ix, inner_ix, parent_ix_disc, b, vault, vq, e, lp_bps, protocol_bps, creator_bps, "
             "cashback_bps, base, e_delta, quote_user, limit_quote, limit_base, boost_left, lp_amount, lp_supply, trader, outer_program, "
             "farmer, fee_payer, token_buy, sol_amount, sol_depth, token_amount")


def leg(pool, slot, tx, ix, b, e, base, e_delta, fp, token_buy, sol, kind=None, payer=None):
    """A store pool row: pre-state (b, e), amounts, and the normalized token side."""
    k = kind or ("buy" if (token_buy if pool != "P2" else not token_buy) else "sell")
    depth = e if pool != "P2" else b
    # fee_payer NULL: the trader pays its own transaction (the store's NULLIF); payer names a separate paying wallet
    return (k, pool, slot, tx, ix, 0, None, b, e, 0, e, 20, 5, 5, 0, base, e_delta, abs(e_delta), None, None, None, None, None, fp,
            "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA", False, payer, token_buy, sol, depth, base if pool != "P2" else abs(e_delta))


def store(path):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE meta (json VARCHAR)")
    con.execute("INSERT INTO meta VALUES (?)", [json.dumps({"ms_per_slot": {str(E): 267.3}})])
    con.execute("CREATE TABLE blocks (slot UBIGINT, block_time BIGINT)")
    con.executemany("INSERT INTO blocks VALUES (?, ?)", [(x, T0 if x < S + 100 else T0 + 86_400) for x in range(S, S + 200)])
    con.execute("CREATE TABLE pools (pool VARCHAR, quote_class VARCHAR, orientation VARCHAR, mint VARCHAR, create_slot UBIGINT, coin_creator VARCHAR)")
    con.executemany("INSERT INTO pools VALUES (?, ?, ?, ?, ?, 'CR')", [
        ("P1", "sol", "normal", "M", S - 600), ("P2", "reversed", "reversed", "M", S - 300),  # two pools of mint M
        ("Q", "sol", "normal", "N", S - 50_000), ("P3", "sol", "normal", "L", S - 10)])
    con.execute("CREATE TABLE mints (mint VARCHAR, create_slot UBIGINT, creator VARCHAR)")
    con.execute(f"CREATE TABLE pool ({', '.join(c + ' ' + t for c, t in zip(POOL_COLS.split(', '), ['VARCHAR', 'VARCHAR', 'UBIGINT', 'BIGINT', 'BIGINT', 'BIGINT', 'VARCHAR', 'BIGINT', 'BIGINT', 'BIGINT', 'BIGINT', 'INTEGER', 'INTEGER', 'INTEGER', 'INTEGER', 'BIGINT', 'BIGINT', 'BIGINT', 'HUGEINT', 'HUGEINT', 'BIGINT', 'BIGINT', 'BIGINT', 'VARCHAR', 'VARCHAR', 'BOOLEAN', 'VARCHAR', 'BOOLEAN', 'BIGINT', 'BIGINT', 'BIGINT']))})")
    rows = [
        # P1 (normal, M) is cheaper than P2 (reversed, M): a gap from the first trade on.
        leg("P1", S, 5, 0, 10**12, 100 * SOL, 2 * 10**10, 2 * SOL, "B", True, 2 * SOL),               # trigger (2 SOL) by B
        # The arb by A in the same slot: buy M on P1, sell it on P2; both end near 1.11e-10 SOL per unit.
        leg("P1", S, 7, 0, 98 * 10**10, 102 * SOL, 3 * 10**10, 33 * SOL // 10, "A", True, 33 * SOL // 10),
        leg("P2", S, 7, 1, 100 * SOL, 8 * 10**11, 79 * SOL // 10, 3 * 10**10, "A", False, 79 * SOL // 10, kind="buy"),
        # Q (normal, N): a 1.5 SOL trigger by C, then the cross-venue arb by A2 three slots later.
        leg("Q", S + 3, 1, 0, 10**13, 300 * SOL, 5 * 10**10, 15 * SOL // 10, "C", True, 15 * SOL // 10),
        leg("Q", S + 6, 2, 0, 995 * 10**10, 3015 * SOL // 10, 10**10, -3 * SOL // 10, "A2", False, 3 * SOL // 10),
        # Not arbs: a loss (D) and a position that is not flat (F), both next to raydium on Q.
        leg("Q", S + 8, 1, 0, 996 * 10**10, 3012 * SOL // 10, 10**9, -SOL // 10, "D", False, SOL // 10),
        leg("Q", S + 9, 1, 0, 996 * 10**10, 3011 * SOL // 10, 10**9, -SOL // 10, "F", False, SOL // 10),
        # P3 (normal, L): X sandwiches Y in slot S + 20; then a front-run by FR on Z in slot S + 30, sold at S + 32.
        leg("P3", S + 20, 1, 0, 10**12, 50 * SOL, 10**10, SOL // 2, "X", True, SOL // 2),
        leg("P3", S + 20, 2, 0, 99 * 10**10, 505 * SOL // 10, 2 * 10**10, SOL, "Y", True, SOL),
        leg("P3", S + 20, 3, 0, 97 * 10**10, 515 * SOL // 10, 10**10, -6 * SOL // 10, "X", False, 6 * SOL // 10),
        leg("P3", S + 30, 1, 0, 98 * 10**10, 509 * SOL // 10, 10**9, SOL // 10, "FR", True, SOL // 10),
        leg("P3", S + 30, 2, 0, 979 * 10**9, 510 * SOL // 10, 10**10, 2 * SOL, "Z", True, 2 * SOL),
        leg("P3", S + 32, 1, 0, 969 * 10**9, 530 * SOL // 10, 10**9, -12 * SOL // 100, "FR", False, 12 * SOL // 100),
        # A routed sale (R): token T -> N on raydium -> SOL on Q. Flat in N with a SOL gain, but it spent T: no closed loop.
        leg("Q", S + 12, 1, 0, 996 * 10**10, 3010 * SOL // 10, 10**9, -SOL // 10, "R", False, SOL // 10),
        # Day 2: a trigger on Q by G, then the newcomer A3's cross-venue arb 5 slots later (reachable).
        leg("Q", S + 150, 1, 0, 997 * 10**10, 3010 * SOL // 10, 4 * 10**10, 12 * SOL // 10, "G", True, 12 * SOL // 10),
        leg("Q", S + 155, 1, 0, 993 * 10**10, 3022 * SOL // 10, 10**10, -3 * SOL // 10, "A3", False, 3 * SOL // 10),
    ]
    con.executemany(f"INSERT INTO pool ({POOL_COLS}) VALUES ({', '.join('?' * len(POOL_COLS.split(', ')))})", rows)
    con.execute("CREATE TABLE curve (mint VARCHAR, slot UBIGINT, tx_index UBIGINT, outer_ix UBIGINT, inner_ix UBIGINT, is_buy BOOLEAN, "
                "q HUGEINT, rq HUGEINT, t HUGEINT, trader VARCHAR, fee_payer VARCHAR)")
    con.close()


def ctx_row(slot, tx, fp, venues, sol_delta, deltas=None, failed=False, programs="1:compute_budget*2;1:BOT;2:pump_amm", fee=10_000,
            tip=0):
    return {"slot": slot, "tx_index": tx, "failed": failed, "fee_payer": fp, "tx_fee": fee, "cu_consumed": 1, "cu_limit": 1,
            "cu_price_micro": 0, "priority_fee": fee - 5_000, "jito_tip": tip, "programs": programs, "venues": venues,
            "n_dex": len([v for v in venues.split(",") if v and v != "jupiter_v6"]), "signer_sol_delta": sol_delta,
            "signer_token_deltas": deltas}


def chunks(root):
    d = root / "c0"
    for t in ("tx_ctx", "failed_txs"):
        (d / t).mkdir(parents=True)
    (d / "_manifest.json").write_text(json.dumps({"status": "complete", "slot_start": LO, "slot_end_exclusive": LO + 432_000}))
    rows = [
        ctx_row(S, 5, "B", "pump_amm", -2 * SOL - 10_000, "M:20000000000"),
        ctx_row(S, 7, "A", "pump_amm", 3_000_000, "M:0", tip=1_000_000),                                         # same-venue arb
        ctx_row(S + 3, 1, "C", "pump_amm", -15 * SOL // 10, "N:50000000000"),
        ctx_row(S + 6, 2, "A2", "pump_amm,raydium_clmm", -10_000, f"N:5;{WSOL}:4000000", programs="1:BOT2;2:pump_amm;2:raydium_clmm"),
        ctx_row(S + 8, 1, "D", "pump_amm,raydium_clmm", -2_000_000, "N:0"),                                      # a loss
        ctx_row(S + 9, 1, "F", "pump_amm,raydium_clmm", 5_000_000, "N:500000000"),                                # not flat
        ctx_row(S + 12, 1, "R", "pump_amm,raydium_clmm", 99_000_000, "N:0;T:-7000000000", programs="1:jupiter_v6;2:raydium_clmm;2:pump_amm"),
        ctx_row(S + 13, 1, "U", "", -8_000, failed=True, programs="1:jupiter_v6", fee=8_000),  # R's program set: R is no winner
        # Router-only set of the winner A3: a failed Jupiter transaction of someone else (J) is no attempt; A3's own is one.
        ctx_row(S + 156, 1, "J", "", -6_000, failed=True, programs="1:jupiter_v6", fee=6_000),
        ctx_row(S + 156, 2, "A3", "", -6_500, failed=True, programs="1:jupiter_v6", fee=6_500),
        ctx_row(S + 20, 1, "X", "pump_amm", -SOL // 2, "L:10000000000"),
        ctx_row(S + 20, 2, "Y", "pump_amm", -SOL, "L:20000000000"),
        ctx_row(S + 20, 3, "X", "pump_amm", 6 * SOL // 10, "L:-10000000000"),
        ctx_row(S + 30, 1, "FR", "pump_amm", -SOL // 10, "L:1000000000"),
        ctx_row(S + 30, 2, "Z", "pump_amm", -2 * SOL, "L:10000000000"),
        ctx_row(S + 32, 1, "FR", "pump_amm", 12 * SOL // 100, "L:-1000000000"),
        ctx_row(S + 150, 1, "G", "pump_amm", -12 * SOL // 10, "N:40000000000"),
        # A3 wins through Jupiter: its top-level program set is router-only (v4 point 2).
        ctx_row(S + 155, 1, "A3", "jupiter_v6,meteora_dlmm,pump_amm", 7_000_000, "N:0", programs="1:jupiter_v6;2:pump_amm;2:meteora_dlmm"),
        # Failed: by the winner A one slot after its arb; with A2's program set (W) next to A2's reachable arb;
        # by someone else with other programs (V): not an attempt.
        ctx_row(S + 1, 1, "A", "pump_amm", -9_000, failed=True, fee=9_000),
        ctx_row(S + 7, 4, "W", "", -12_000, failed=True, programs="1:BOT2", fee=12_000),
        ctx_row(S + 1, 2, "V", "", -7_000, failed=True, programs="1:OTHER", fee=7_000),
    ]
    pq.write_table(pa.Table.from_pylist(rows), d / "tx_ctx" / "part-00000.parquet")
    pq.write_table(pa.Table.from_pylist([{"slot": S + 7, "tx_index": 4, "fee_payer": "W", "jito_tip_attempted": 50_000}]),
                   d / "failed_txs" / "part-00000.parquet")
    return str(root)


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("mev0")
    store(tmp / "store.duckdb")
    old = {k: os.environ.get(k) for k in ("LAB_STORE", "LAB_SPILL_DIR")}
    os.environ["LAB_STORE"], os.environ["LAB_SPILL_DIR"] = str(tmp / "store.duckdb"), str(tmp / "spill")
    try:
        mev0.main(["--chunks", chunks(tmp / "chunks"), "--epochs", str(E), "--out", str(tmp / "out")])
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    return tmp / "out"


def rows(out, name):
    return pq.read_table(out / f"{name}-{E}.parquet").to_pylist()


def test_arbs_kinds_net_and_backruns(out):
    arbs = {r["fee_payer"]: r for r in rows(out, "arbs")}
    assert set(arbs) == {"A", "A2", "A3", "R"}  # D lost, F was not flat, the sandwich is three transactions
    assert [x for x, r in arbs.items() if not r["closed_loop"]] == ["R"]  # R sold token T through N: not a closed loop
    a = arbs["A"]
    assert (a["kind"], a["other_venue_unparsed"], a["mint"], a["net"], a["gross"]) == ("same_venue", False, "M", 3_000_000, 4_010_000)
    assert (a["backrun"], a["prior_fee_payer"], a["dist_slots"], a["dist_tx"], a["reachable"]) == (True, "B", 0, 2, False)
    b = arbs["A2"]
    assert (b["kind"], b["other_venue_unparsed"], b["net"], b["other_dex"]) == ("cross_venue", True, 3_990_000, ["raydium_clmm"])
    assert (b["backrun"], b["dist_slots"], b["dist_tx"], b["reachable"], b["top_programs"]) == (True, 3, None, True, "BOT2")
    assert arbs["A3"]["reachable"] and arbs["A3"]["day"] != a["day"]


def test_failed_attempts_are_an_upper_bound_by_winner_or_program_set(out):
    att = {r["fee_payer"]: r for r in rows(out, "attempts")}
    assert set(att) == {"A", "W", "A3"}  # V: neither; U: only the routed sale R's program set, and R is no winner
    assert (att["A3"]["by_winner"], att["A3"]["same_programs"]) == (True, False)  # v4: J (router-only, no winner) is out
    assert (att["A"]["by_winner"], att["A"]["reachable"], att["A"]["cost"]) == (True, False, 9_000)
    assert (att["W"]["same_programs"], att["W"]["reachable"], att["W"]["jito_tip_attempted"]) == (True, True, 50_000)


def test_sandwich_and_front_run_are_described_not_arbs(out):
    harm = {r["kind"]: r for r in rows(out, "harm")}
    assert (harm["sandwich"]["fee_payer"], harm["sandwich"]["victim"], harm["sandwich"]["profit"]) == ("X", "Y", SOL // 10)
    assert (harm["front_run"]["fee_payer"], harm["front_run"]["victim"], harm["front_run"]["profit"]) == ("FR", "Z", 2 * SOL // 100)
    assert len(rows(out, "harm")) == 2  # the sandwich's front leg is not also a front-run


def test_signers_first_day_gives_the_newcomer(out):
    s = {r["fee_payer"]: r for r in rows(out, "signers")}
    assert s["A3"]["first_day"] > s["A"]["first_day"] and s["A"]["first_day"] == s["A"]["last_day"]


def test_a_gap_between_two_pools_of_one_mint_closed_by_the_arb(out):
    g = rows(out, "gaps")
    assert len(g) == 1
    assert (g[0]["mint"], g[0]["open_slot"], g[0]["close_slot"], g[0]["close_tx"], g[0]["closed_by_arb"], g[0]["reachable"]) == \
        ("M", S, S, 7, True, False)
    assert g[0]["best_net"] > 0 and 0.05 <= g[0]["best_size_sol"] <= 1.0


def test_run_json_and_days(out):
    with open(out / f"run-mev0-{E}.json") as f:
        info = json.load(f)
    assert info["spec"].startswith("week3-specs-mev0-2026-10-12.md v4") and info["counts"]["arbs"] == 4
    assert info["counts"]["attempts"] == 3
    assert [r["slots"] for r in rows(out, "days")] == [100, 100]


def test_concentration_on_a_hand_made_distribution():
    from wallets.mev0_report import concentration

    top1, top3, top5, hhi = concentration([50, 30, 10, 10, None, 0])
    assert (round(top1, 6), round(top3, 6), round(top5, 6), round(hhi, 6)) == (0.5, 0.9, 1.0, 0.36)
    assert concentration([]) == (None, None, None, None)


def test_the_decision_rule():
    from wallets.mev0_report import decide

    assert decide(True, True, True, True, 40).startswith("GO")
    assert decide(False, True, True, True, 20).startswith("meer meten")  # only 1 fails, by less than a factor 2
    assert decide(False, True, True, True, 10).startswith("NO-GO")  # by more than a factor 2
    assert decide(True, False, True, True, 40).startswith("NO-GO")
    assert decide(False, True, True, None, 20).startswith("NO-GO")  # an unmeasured criterion fails


def test_the_report_puts_the_go_table_first(out, tmp_path):
    from wallets import mev0_report

    md = tmp_path / "report.md"
    mev0_report.main(["--dir", str(out), "--epochs", str(E), "--out", str(md)])
    text = md.read_text()
    assert text.index("## Go/no-go") < text.index("## §2.1")
    assert "NO-GO" in text  # two partial days, no measurement day: nothing is shown as met
    for section in ("§2.2", "§2.3", "§2.4", "§2.5", "§2.6", "§2.7", "§2.8", "§2.9", "§2.10", "§2.11"):
        assert f"## {section}" in text


def test_days_are_utc_whatever_the_host_time_zone(tmp_path):
    """A block at 23:30 UTC and one at 00:30 UTC fall on two UTC days, also on a host in Europe/Amsterdam
    (where to_timestamp()::DATE would put both on the local next day)."""
    import datetime as dt

    store(tmp_path / "s.duckdb")
    late = int(dt.datetime(2026, 9, 25, 23, 30, tzinfo=dt.timezone.utc).timestamp())
    con = duckdb.connect(str(tmp_path / "s.duckdb"))
    con.executemany("INSERT INTO blocks VALUES (?, ?)", [(S + 300, late), (S + 301, late + 3600)])
    con.close()
    con = duckdb.connect(str(tmp_path / "s.duckdb"), read_only=True)
    con.execute("SET TimeZone = 'Europe/Amsterdam'")
    mev0.setup(con, chunks(tmp_path / "chunks"), LO, LO + 432_000)
    got = dict(con.execute(f"SELECT slot, day FROM days WHERE slot IN ({S + 300}, {S + 301})").fetchall())
    assert (str(got[S + 300]), str(got[S + 301])) == ("2026-09-25", "2026-09-26")


def test_the_first_day_of_every_epoch_counts_no_newcomers():
    """Epochs 1040 and 1045 are not contiguous: on 1045's first day everyone active only in 1041-1044 would
    look new. Both epochs' first days are marked, and the newcomer median skips them."""
    import datetime as dt

    from wallets import mev0_report as R

    d = [dt.date(2026, 9, 20) + dt.timedelta(days=i) for i in (0, 1, 7, 8)]
    con = duckdb.connect()
    con.execute("CREATE TABLE days_all AS SELECT * FROM (VALUES (1040, ?::DATE), (1040, ?::DATE), (1045, ?::DATE), (1045, ?::DATE)) t(epoch, day)", d)
    con.execute("CREATE TABLE dayinfo AS SELECT day, 1.0 AS coverage FROM days_all")
    # One reachable arb per day; N is first seen on 1045's first day, M on the day after.
    arbs = [(d[0], "A"), (d[1], "A"), (d[2], "N"), (d[3], "M")]
    con.execute("CREATE TABLE arbs_cl (day DATE, fee_payer VARCHAR, net BIGINT, backrun BOOLEAN, reachable BOOLEAN)")
    con.executemany("INSERT INTO arbs_cl VALUES (?, ?, 20000000, true, true)", arbs)
    con.execute("CREATE VIEW arbs_all AS SELECT * FROM arbs_cl")
    con.execute("CREATE TABLE first_seen AS SELECT * FROM (VALUES ('A', ?::DATE), ('N', ?::DATE), ('M', ?::DATE)) t(fee_payer, first_day)",
                [d[0], d[2], d[3]])
    con.execute("CREATE TABLE gaps_all (day DATE, closed_by_arb BOOLEAN, reachable BOOLEAN, best_net BIGINT)")
    con.execute("CREATE TABLE attempts_all (day DATE, reachable BOOLEAN, cost BIGINT)")
    days = R.per_day(con)
    assert [days[x]["first_day"] for x in d] == [True, False, True, False]
    crit, _, n_full = R.criteria(days)
    assert n_full == 4 and "nieuwkomers 50,0 %" in crit["3 open voor nieuwkomers"][0]  # median over d[1] (0 %) and d[3] (100 %)
