"""Cluster links, the service-payer registry, sell windows and the veto, on a tiny synthetic store."""

import json
import os

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from wallets import clusters as C
from wallets import veto as V
from wallets.common import HOLDOUT_START, Clock, connect, guard

SOLQ = "So11111111111111111111111111111111111111112"
MS = {"1040": 250.0, "1041": 250.0}  # 4 slots per second: 3 s = 12 slots, 40 s = 160, 10 min = 2400


def make_store(path, curve, pool=(), mints=None):
    con = duckdb.connect(path)
    con.execute("""CREATE TABLE curve (mint VARCHAR, slot UBIGINT, tx_index UBIGINT, trader VARCHAR, fee_payer VARCHAR,
                   jito_tip UBIGINT, is_buy BOOLEAN, t HUGEINT, q HUGEINT)""")
    if curve:
        con.executemany("INSERT INTO curve VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", list(curve))
    con.execute("""CREATE TABLE mints (mint VARCHAR, create_slot UBIGINT, mayhem BOOLEAN, quote_mint VARCHAR,
                   complete_slot UBIGINT)""")
    con.executemany("INSERT INTO mints VALUES (?, ?, ?, ?, ?)", mints or [("M", 0, False, SOLQ, None)])
    con.execute("""CREATE TABLE pool (kind VARCHAR, pool VARCHAR, slot UBIGINT, tx_index BIGINT, trader VARCHAR,
                   fee_payer VARCHAR, jito_tip UBIGINT, token_buy BOOLEAN, token_amount BIGINT, sol_amount BIGINT,
                   farmer BOOLEAN)""")
    if pool:
        con.executemany("INSERT INTO pool VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", list(pool))
    con.execute("CREATE TABLE pools (pool VARCHAR, mint VARCHAR, orientation VARCHAR, quote_class VARCHAR, mayhem BOOLEAN)")
    con.execute("INSERT INTO pools VALUES ('P', 'M', 'reversed', 'reversed', false)")
    con.execute("CREATE TABLE meta (json VARCHAR)")
    con.execute("INSERT INTO meta VALUES (?)", [json.dumps({"ms_per_slot": MS})])
    con.close()


S0 = 1040 * 432_000 + 100_000  # a slot in epoch 1040


def buy(slot, tx, trader, payer=None, tip=0, t=100):
    return ("M", slot, tx, trader, payer, tip, True, t, 10**9)


def sell(slot, tx, trader, t):
    return ("M", slot, tx, trader, None, 0, False, t, 10**9)


def test_union_find_links_and_min_two_buyers():
    edges = [("curve", "M", 1, "a", "b", "tx"), ("curve", "M", 1, "c", "payer", "pay"),
             ("curve", "M", 1, "d", "payer", "pay"), ("curve", "M", 1, "e", "x", "pay")]
    buyers = {("curve", "M", 1, w) for w in "abcde"}
    comps = C.components(edges, buyers)
    assert [(c[3], c[4], c[5]) for c in comps] == [(["a", "b"], [], ["tx"]), (["c", "d"], ["payer"], ["pay"])]
    # e and its payer x: one buying wallet, not a cluster


def test_edges_tip_adjacency_and_service(tmp_path):
    store = str(tmp_path / "s.duckdb")
    curve = [buy(S0, 10, "a", tip=5000), buy(S0, 11, "b", tip=5000),  # adjacent, same tip: bundle
             buy(S0, 20, "c", tip=5000), buy(S0, 22, "d", tip=5000),  # not adjacent
             buy(S0, 30, "e", tip=7000), buy(S0, 31, "f", tip=8000),  # different tip
             buy(S0, 40, "g", payer="svc"), buy(S0, 41, "h", payer="svc")]  # service payer: no link
    make_store(store, curve)
    con = connect(store)
    events, members, stats, _ = C.process_window(con, S0, S0 + 100, Clock(MS), {"svc"})
    got = sorted(tuple(sorted(r["wallet"] for r in members.to_pylist() if r["eid"] == e)) for e in
                 set(members.column("eid").to_pylist()))
    assert got == [("a", "b")]
    events, members, _, _ = C.process_window(con, S0, S0 + 100, Clock(MS), set())
    got = sorted(tuple(sorted(r["wallet"] for r in members.to_pylist() if r["eid"] == e)) for e in
                 set(members.column("eid").to_pylist()))
    assert got == [("a", "b"), ("g", "h", "svc")]


def test_service_registry_looks_back_only(tmp_path):
    store = str(tmp_path / "s.duckdb")
    at = S0 + 1000
    past = [("M%d" % (i % 60), at - 10, i, "w%d" % i, "svc", 0, True, 1, 1) for i in range(120)]
    future = [("M%d" % (i % 60), at, i, "w%d" % i, "late", 0, True, 1, 1) for i in range(120)]
    make_store(store, past + future)
    assert C.service_payers(connect(store), at, Clock(MS)) == {"svc"}


def test_sell_windows(tmp_path):
    store = str(tmp_path / "s.duckdb")
    curve = [buy(S0, 10, "a", payer="p", t=100), buy(S0, 12, "b", payer="p", t=100),
             sell(S0, 5, "a", 50),  # before the event's last buy: not counted
             sell(S0, 13, "a", 20),  # same slot, after the last buy: within 3 s
             sell(S0 + 100, 1, "b", 30),  # within 40 s (160 slots)
             sell(S0 + 2000, 1, "p", 40),  # the paying wallet sells: within 10 min
             sell(S0 + 3000, 1, "a", 10),  # after 10 min
             buy(S0, 30, "solo", t=50), sell(S0 + 5, 1, "solo", 50)]
    # b sells in the token's pool after graduation: same mint, counts within 10 min
    pool = [("sell", "P", S0 + 1000, 1, "b", None, 0, False, 5, 1, False)]
    make_store(store, curve, pool)
    events, _, stats, _ = C.process_window(connect(store), S0, S0 + 50, Clock(MS), set())
    e = events.to_pylist()[0]
    assert (e["tokens"], e["sold_3s"], e["sold_40s"], e["sold_10m"]) == (200, 20, 50, 95)
    solo = [r for r in stats.to_pylist() if r["kind"] == "solo"][0]
    assert solo["share_sum_3s"] == 1.0 and solo["half_3s"] == 1


def write_positions(run_dir, rows):
    os.makedirs(os.path.join(run_dir, "parts"))
    base = dict(segment="pool_reversed", farm="organic", variant="v", size_sol=0.5, d=1, day="2026-09-25",
                venue="pool", mint="M", pool="P", skipped=False, entry_failed=False, cost=10**9, exit_attempts=1, exit_reason="time")
    pq.write_table(pa.Table.from_pylist([dict(base, exit_slot=r["t"] + 100, **r) for r in rows]),  # sold (costs.FILLED_SQL)
                   os.path.join(run_dir, "parts", "p.parquet"))


def test_veto_uses_only_the_past(tmp_path):
    store = str(tmp_path / "s.duckdb")
    lo = S0
    t = lo + 400_000  # > 24 h (345,600 slots at 250 ms) after the range start
    row = lambda slot, w, payer: ("buy", "P", slot, 1, w, payer, 0, True, 1, 1, False)  # noqa: E731
    pool = [row(t, "x", "q"), row(t, "y", "q"),  # cluster at t
            row(t + 1, "z", "r"), row(t + 1, "z2", "r"),  # cluster at t + 1
            row(t - 1000, "s", "u"), row(t - 1000, "s2", "u")]  # cluster at t - 1000 (outside K1, inside K2)
    make_store(store, [], pool)
    cdir = str(tmp_path / "clusters")
    os.makedirs(cdir)
    con = connect(store)
    for a in (t - 1000, t, t + 1):
        ev, mem, _, _ = C.process_window(con, a, a + 1, Clock(MS), set())
        pq.write_table(ev, os.path.join(cdir, f"events-{a}.parquet"))
        pq.write_table(mem, os.path.join(cdir, f"members-{a}.parquet"))
    run = str(tmp_path / "run")
    write_positions(run, [dict(family="F7", t=t, trigger_trader="s", proceeds=2 * 10**9),
                          dict(family="F7", t=t - 1, trigger_trader="x", proceeds=2 * 10**9),
                          dict(family="F7", t=t - 200, trigger_trader="z", proceeds=2 * 10**9)])
    cfg = {"costs": {"ata_rent_lamports": 0, "scenarios": {"base": {
        "fee_per_tx_sol": 0, "tip_sol": 0, "failed_tx_sol": 0, "land_prob": 1.0, "ata_rent": "lost",
        "adverse_exec_per_side": 0.0}}}}
    daily = V.evaluate(con, run, cdir, lo, t + 2, cfg, Clock(MS))
    by = {r["veto"]: r for r in daily}
    # K1 (60 s = 240 slots): only the trigger at t (cluster in slot t itself). The trigger at t - 1
    # would be vetoed if the cluster at t leaked into its decision.
    assert by["K1"]["vetoed"] == 1 and by["K1"]["vetoed_pnl"] == 1.0
    # K2 (10 min = 2400 slots): all three see the cluster at t - 1000.
    assert by["K2"]["vetoed"] == 3
    # K3: "s" was in a cluster before t; "x" and "z" join clusters only at or after their trigger.
    assert by["K3"]["vetoed"] == 1


def test_guard():
    guard(HOLDOUT_START)
    with pytest.raises(ValueError):
        guard(HOLDOUT_START + 1)
