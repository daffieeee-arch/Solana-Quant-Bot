"""Transfers module on a mini store and a mini transfers chunk: the owner ledger (legs plus the kept delta rows, with
the create transaction's net vault row and a kept row under another owner than the store's trader), conservation,
the corrected insider share against the legs-only share, bar (b) for a legs-only share > 1, wallet births with a hub
and a funder of six same-label wallets, and the transfer-fed selling of a new pool."""

import json
import re
import os

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from wallets import transfers

E = 1040
LO = E * 432_000
S = LO + 1_000
T0 = 1_790_000_000
M = "Mpump"
U = 10**6  # one token unit scale: 1,000 tokens minted
SUPPLY = 1000 * U


def _store(path):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE meta (json VARCHAR)")
    con.execute("INSERT INTO meta VALUES (?)", [json.dumps({"ms_per_slot": {str(E): 267.3}})])
    con.execute("CREATE TABLE blocks (slot UBIGINT, block_time BIGINT)")
    con.executemany("INSERT INTO blocks VALUES (?, ?)", [(x, T0 + (x - S) // 100 * 86_400) for x in range(S - 10, S + 300)])
    con.execute("CREATE TABLE mints (mint VARCHAR, create_slot UBIGINT, creator VARCHAR, supply UBIGINT, mayhem BOOLEAN, cashback BOOLEAN, holder_reward BOOLEAN)")
    con.execute("INSERT INTO mints VALUES (?, ?, 'CR', ?, false, false, false), ('Old', ?, 'ZZ', ?, false, false, false)", [M, S, SUPPLY, S - 5_000, SUPPLY])
    con.execute("CREATE TABLE curve (mint VARCHAR, slot UBIGINT, tx_index UBIGINT, is_buy BOOLEAN, t HUGEINT, q HUGEINT, trader VARCHAR, variant VARCHAR)")
    con.executemany("INSERT INTO curve VALUES (?, ?, ?, ?, ?, ?, ?, 'buy')", [
        (M, S, 1, True, 300 * U, 3 * 10**9, "CR"),        # dev buy in the create transaction
        (M, S + 5, 1, True, 100 * U, 10**9, "I"),         # insider buy (within 10 s = 37 slots)
        (M, S + 30, 1, False, 50 * U, 4 * 10**8, "XSIG"),  # X sells 50 through a PDA-held account: the store's trader differs from the owner
        (M, S + 50, 1, False, 10 * U, 10**8, "X"),        # X sells 10 after the trigger
        ("Old", S + 3, 1, True, 10 * U, 10**8, "Q")])
    con.execute("CREATE TABLE pools (pool VARCHAR, mint VARCHAR, quote_class VARCHAR, orientation VARCHAR, create_slot UBIGINT)")
    con.execute("INSERT INTO pools VALUES ('P', ?, 'sol', 'normal', ?)", [M, S + 60])
    con.execute("CREATE TABLE pool (pool VARCHAR, slot UBIGINT, tx_index BIGINT, kind VARCHAR, trader VARCHAR, token_buy BOOLEAN, token_amount BIGINT, sol_amount BIGINT)")
    con.execute("INSERT INTO pool VALUES ('P', ?, 1, 'sell', 'X', false, ?, 50000000), ('P', ?, 2, 'buy', 'Y', true, ?, 20000000)", [S + 70, 5 * U, S + 70, 2 * U])
    con.close()


def _chunk(root):
    d = root / "c0"
    for t in ("token_deltas", "token_transfers", "sol_funding"):
        (d / t).mkdir(parents=True)
    (d / "_manifest.json").write_text(json.dumps({"status": "complete", "slot_start": LO, "slot_end_exclusive": LO + 432_000, "extractor_sha256": "abc"}))

    def td(slot, tx, owner, delta, vault=False, trade=True):
        return {"slot": slot, "tx_index": tx, "mint": M, "owner": owner, "token_account": owner + "_ta", "delta_raw": str(delta), "decimals": 6,
                "is_vault": vault, "has_pump_event": trade, "has_pump_event_same_mint": trade, "fee_payer": owner, "failed": False}
    pq.write_table(pa.Table.from_pylist([
        td(S, 1, "CURVE", SUPPLY - 300 * U, vault=True),   # the create: mint 1000, dev buy 300 -> the vault's net row is kept
        td(S + 20, 1, "I", -60 * U, trade=False), td(S + 20, 1, "X", 60 * U, trade=False),   # I transfers 60 to X (no trade in that tx)
        td(S + 30, 1, "X", -50 * U),                        # X's sell: kept under the chain owner X (the store says XSIG)
        td(S + 90, 1, "GHOST", 7 * U, ),                    # a failed-flag row must be ignored
    ][:-1] + [dict(td(S + 90, 1, "GHOST", 7 * U), failed=True)]), d / "token_deltas" / "part-00000.parquet")

    def tt(slot, tx, kind, src, dst, amount, in_trade):
        return {"slot": slot, "tx_index": tx, "ix_path": "1.1", "program": "token", "kind": kind, "mint": M, "source_account": None, "source_owner": src,
                "dest_account": None, "dest_owner": dst, "authority": src, "amount_raw": amount, "decimals": 6, "outer_program": "pump" if in_trade else "token",
                "in_trade": in_trade, "owner_unresolved": False, "fee_payer": src or dst, "has_pump_event": in_trade, "failed": False}
    pq.write_table(pa.Table.from_pylist([
        tt(S, 1, "mint_to", None, "CURVE", SUPPLY, True), tt(S, 1, "transfer_checked", "CURVE", "CR", 300 * U, True),
        tt(S + 5, 1, "transfer_checked", "CURVE", "I", 100 * U, True),
        tt(S + 20, 1, "transfer", "I", "X", 60 * U, False),
        tt(S + 30, 1, "transfer_checked", "X", "CURVE", 50 * U, True), tt(S + 50, 1, "transfer_checked", "X", "CURVE", 10 * U, True)]),
        d / "token_transfers" / "part-00000.parquet")

    def sf(slot, tx, frm, to, lamports):
        return {"slot": slot, "tx_index": tx, "ix_path": "1", "from_account": frm, "to_account": to, "lamports": lamports, "kind": "transfer",
                "fee_payer": frm, "has_pump_event": False, "failed": False}
    rows = [sf(S + 1 + i, 1, "F", f"b{i}", 50_000_000) for i in range(6)]            # one funder, six bundler wallets in a day
    rows += [sf(S + 2, 2, "F", "r0", 50_000_000), sf(S + 2, 3, "F2", "r1", 50_000_000)]  # a retail wallet from F, one from F2
    rows += [sf(S + 10, 10 + i, "H", f"h{i}", 3_000_000) for i in range(60)]           # a hub: 60 births of 0.003 SOL on one day
    rows += [sf(S + 11, 99, "F", "b0", 10_000_000)]                                     # a second funding of b0: not a birth
    pq.write_table(pa.Table.from_pylist(rows), d / "sol_funding" / "part-00000.parquet")
    return str(root)


def test_ledger_shares_births_and_bundles(tmp_path, monkeypatch):
    _store(tmp_path / "s.duckdb")
    root = _chunk(tmp_path / "chunks")
    pq.write_table(pa.Table.from_pylist([{"mint": M, "t": S + 40, "ins_share": 400 / 350}, {"mint": "Old", "t": S + 40, "ins_share": 0.1}]), tmp_path / "trig.parquet")
    pq.write_table(pa.Table.from_pylist([{"wallet": f"b{i}", "label": "bundler"} for i in range(6)] + [{"wallet": "r0", "label": "retail"}, {"wallet": "r1", "label": "retail"}]),
                   tmp_path / "labels.parquet")
    monkeypatch.setenv("LAB_STORE", str(tmp_path / "s.duckdb"))
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path / "spill"))
    out = tmp_path / "out"
    transfers.main(["--chunks", root, "--triggers", str(tmp_path / "trig.parquet"), "--labels", str(tmp_path / "labels.parquet"), "--out", str(out)])
    con = duckdb.connect()
    # conservation: the window's mint only (Old was created before it); 1000 minted = 300 (CR) + 40 (I) + 10 (X) + 650 (vault) at the day end
    cons = con.execute(f"SELECT mint, held, minted, diff, ok FROM '{out}/conservation.parquet' ORDER BY day").fetchall()
    assert {c[0] for c in cons} == {M} and all(c[1] == SUPPLY and c[3] == 0 and c[4] for c in cons) and len(cons) == 3  # one row per UTC day
    q1 = con.execute(f"SELECT * FROM '{out}/q1_triggers.parquet'").fetchall()
    cols = [d[0] for d in con.description]
    r = dict(zip(cols, q1[0]))
    assert len(q1) == 1 and r["mint"] == M  # Old is outside the window
    # at t = S + 40: CR 300, I 40, X 10 in circulation (350); insiders CR and I hold 340
    assert r["circ"] == 350 * U and r["h_ins"] == 340 * U and abs(r["share_c"] - 340 / 350) < 1e-12 and r["undefined"] is None
    assert abs(r["share_legs"] - 400 / 350) < 1e-12 and abs(r["share_legs"] - r["ins_share_old"]) < 1e-9  # legs only: > 1, reproduces the engine
    # bar (b): X (not an insider) sells 10 on the curve and 5 on pool P in the hour after t; it received 60 from the insider I: fully explained
    assert r["sellers"] == 1 and r["sold_1h"] == 15 * U and r["from_insider_1hop"] == 15 * U and r["explained_share"] == 1.0
    # Q2: six bundler wallets from F are linked (5 siblings each); the retail wallets are not; H is a hub (60 births on a day)
    q2 = {(a, b): (w, l) for a, b, w, l in con.execute(f"SELECT label, hub, wallets, linked FROM '{out}/q2_measure.parquet'").fetchall()}
    assert q2[("bundler", False)] == (6, 6) and q2[("retail", False)] == (2, 0) and ("onbekend", True) not in q2  # hub births are below 0.005 SOL
    hubs = con.execute(f"SELECT funder, births, kind FROM '{out}/q2_hubs.parquet'").fetchall()
    assert hubs == [("H", 60, "anders")]
    assert con.execute(f"SELECT count(*), count(DISTINCT wallet) FROM '{out}/q2_births.parquet'").fetchone() == (68, 68)  # b0's second funding is no birth
    # Q3: pool P (created in the window): X sells 5 in its first hour, fed by the transfer of 60
    q3 = con.execute(f"SELECT sellers, sold, sold_fed, fed_share FROM '{out}/q3_pools.parquet'").fetchall()
    assert q3 == [(1, 5 * U, 5 * U, 1.0)]
    md = open(out / "transfers.md").read()
    assert "100.0 %" in md and "| bundler | 6 | 6 | 100.0 %" in md and "Hubs" in md


class _Explained:
    """A connection proxy: every CREATE ... TABLE x AS <select> is EXPLAINed first and its join operators recorded."""

    def __init__(self, con):
        self.con, self.plans = con, {}

    def execute(self, sql, *a, **k):
        m = re.match(r"\s*CREATE (?:OR REPLACE )?TEMP TABLE (\w+) AS\s+(.*)$", sql, re.S)
        if m:
            plan = self.con.execute("EXPLAIN " + m.group(2)).fetchall()[0][1]
            self.plans[m.group(1)] = sorted(set(re.findall(r"[A-Z_]*JOIN|CROSS_PRODUCT", plan)))
        return self.con.execute(sql, *a, **k)

    def __getattr__(self, name):
        return getattr(self.con, name)


def test_no_nested_loop_join_in_any_query(tmp_path, monkeypatch):
    """Every table of the module is built with hash (or ASOF) joins: an anti-join with an OR in its condition was
    planned as a blockwise nested-loop join and the NAS run sat in it for 6 h on one epoch (2026-10-16)."""
    _store(tmp_path / "s.duckdb")
    root = _chunk(tmp_path / "chunks")
    pq.write_table(pa.Table.from_pylist([{"mint": M, "t": S + 40, "ins_share": 400 / 350}]), tmp_path / "trig.parquet")
    pq.write_table(pa.Table.from_pylist([{"wallet": "b0", "label": "bundler"}]), tmp_path / "labels.parquet")
    monkeypatch.setenv("LAB_STORE", str(tmp_path / "s.duckdb"))
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path / "spill"))
    seen = {}
    real = transfers.connect

    def wrapped(*a, **k):
        px = _Explained(real(*a, **k))
        seen["plans"] = px.plans
        return px
    monkeypatch.setattr(transfers, "connect", wrapped)
    transfers.main(["--chunks", root, "--triggers", str(tmp_path / "trig.parquet"), "--labels", str(tmp_path / "labels.parquet"), "--out", str(tmp_path / "out")])
    plans = seen["plans"]
    assert {"ledger", "insiders", "q1", "q1b", "q2", "q3", "cons", "births", "hubs"} <= set(plans)
    bad = {t: ops for t, ops in plans.items() if any("NESTED_LOOP" in op or "BLOCKWISE" in op or "CROSS_PRODUCT" in op for op in ops)}
    assert not bad, bad


# ----------------------------------------------------------------------------- review C2 / C3 (Codex review of main, 2026-10-15)

M2 = "M2pump"


def _store_review(path):
    """One mint: dev buy 300 (CR) at the create, insider I buys 100 at S + 5; E (no buy) sells 5 at S + 40; a bundle at
    S + 60 with two exact-out buys of 20 by B2 and C2 (B2's tokens land on a PDA: the kept row is under P); C sells 10 at
    S + 150; D sells 10 at S + 100 and 10 at S + 400."""
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE meta (json VARCHAR)")
    con.execute("INSERT INTO meta VALUES (?)", [json.dumps({"ms_per_slot": {str(E): 267.3}})])
    con.execute("CREATE TABLE blocks (slot UBIGINT, block_time BIGINT)")
    con.executemany("INSERT INTO blocks VALUES (?, ?)", [(x, T0 + (x - S) // 100 * 86_400) for x in range(S - 10, S + 500)])
    con.execute("CREATE TABLE mints (mint VARCHAR, create_slot UBIGINT, creator VARCHAR, supply UBIGINT, mayhem BOOLEAN, cashback BOOLEAN, holder_reward BOOLEAN)")
    con.execute("INSERT INTO mints VALUES (?, ?, 'CR', ?, false, false, false)", [M2, S, SUPPLY])
    con.execute("CREATE TABLE curve (mint VARCHAR, slot UBIGINT, tx_index UBIGINT, is_buy BOOLEAN, t HUGEINT, q HUGEINT, trader VARCHAR, variant VARCHAR)")
    con.executemany("INSERT INTO curve VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
        (M2, S, 1, True, 300 * U, 3 * 10**9, "CR", "buy"), (M2, S + 5, 1, True, 100 * U, 10**9, "I", "buy"),
        (M2, S + 40, 1, False, 5 * U, 5 * 10**7, "E", "sell"),
        (M2, S + 60, 1, True, 20 * U, 2 * 10**8, "B2", "buy_exact_sol_in"), (M2, S + 60, 1, True, 20 * U, 2 * 10**8, "C2", "buy_exact_sol_in"),
        (M2, S + 150, 1, False, 10 * U, 10**8, "C", "sell"),
        (M2, S + 100, 1, False, 10 * U, 10**8, "D", "sell"), (M2, S + 400, 1, False, 10 * U, 10**8, "D", "sell")])
    con.execute("CREATE TABLE pools (pool VARCHAR, mint VARCHAR, quote_class VARCHAR, orientation VARCHAR, create_slot UBIGINT)")
    con.execute("CREATE TABLE pool (pool VARCHAR, slot UBIGINT, tx_index BIGINT, kind VARCHAR, trader VARCHAR, token_buy BOOLEAN, token_amount BIGINT, sol_amount BIGINT)")
    con.close()


def _chunk_review(root):
    d = root / "c0"
    for t in ("token_deltas", "token_transfers", "sol_funding"):
        (d / t).mkdir(parents=True)
    (d / "_manifest.json").write_text(json.dumps({"status": "complete", "slot_start": LO, "slot_end_exclusive": LO + 432_000, "extractor_sha256": "abc"}))

    def td(slot, tx, owner, delta, vault=False, trade=True):
        return {"slot": slot, "tx_index": tx, "mint": M2, "owner": owner, "token_account": owner + "_ta", "delta_raw": str(delta), "decimals": 6,
                "is_vault": vault, "has_pump_event": trade, "has_pump_event_same_mint": trade, "fee_payer": owner, "failed": False}
    pq.write_table(pa.Table.from_pylist([
        td(S, 1, "CURVE", SUPPLY - 300 * U, vault=True),                               # the create: mint 1000, dev buy 300
        td(S + 60, 1, "P", 20 * U), td(S + 60, 1, "CURVE", -40 * U, vault=True),       # the bundle: B2's 20 on the PDA P; C2's row dropped (= its leg); the vault's net
        td(S + 200, 1, "I", -50 * U, trade=False), td(S + 200, 1, "B", 50 * U, trade=False),   # I -> B 50 at S + 200
        td(S + 100, 1, "B", -50 * U, trade=False), td(S + 100, 1, "C", 50 * U, trade=False),   # B -> C 50 at S + 100: before B received from I
        td(S + 300, 1, "I", -20 * U, trade=False), td(S + 300, 1, "D", 20 * U, trade=False),   # I -> D 20 at S + 300: between D's two sales
    ]), d / "token_deltas" / "part-00000.parquet")

    def tt(slot, tx, kind, src, dst, amount, in_trade):
        return {"slot": slot, "tx_index": tx, "ix_path": "1.1", "program": "token", "kind": kind, "mint": M2, "source_account": None, "source_owner": src,
                "dest_account": None, "dest_owner": dst, "authority": src, "amount_raw": amount, "decimals": 6, "outer_program": "pump" if in_trade else "token",
                "in_trade": in_trade, "owner_unresolved": False, "fee_payer": src or dst, "has_pump_event": in_trade, "failed": False}
    pq.write_table(pa.Table.from_pylist([
        tt(S, 1, "mint_to", None, "CURVE", SUPPLY, True),
        tt(S + 200, 1, "transfer", "I", "B", 50 * U, False), tt(S + 100, 1, "transfer", "B", "C", 50 * U, False), tt(S + 300, 1, "transfer", "I", "D", 20 * U, False)]),
        d / "token_transfers" / "part-00000.parquet")
    pq.write_table(pa.Table.from_pylist([{"slot": S + 1, "tx_index": 1, "ix_path": "1", "from_account": "F", "to_account": "w0", "lamports": 50_000_000, "kind": "transfer",
                                          "fee_payer": "F", "has_pump_event": False, "failed": False}]), d / "sol_funding" / "part-00000.parquet")
    return str(root)


def test_review_c2_hop_order_and_chronology_and_c3_same_amount_other_owner(tmp_path, monkeypatch):
    _store_review(tmp_path / "s.duckdb")
    root = _chunk_review(tmp_path / "chunks")
    pq.write_table(pa.Table.from_pylist([{"mint": M2, "t": S + 50}, {"mint": M2, "t": S + 70}]), tmp_path / "trig.parquet")
    monkeypatch.setenv("LAB_STORE", str(tmp_path / "s.duckdb"))
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path / "spill"))
    out = tmp_path / "out"
    transfers.main(["--chunks", root, "--triggers", str(tmp_path / "trig.parquet"), "--out", str(out)])
    con = duckdb.connect()
    cons = con.execute(f"SELECT held, minted, diff, ok FROM '{out}/conservation.parquet' ORDER BY day").fetchall()
    assert cons and all(c[0] == SUPPLY and c[2] == 0 and c[3] for c in cons)   # C3: C2's 20 are in the ledger (one-to-one with P's row, B2's leg dropped)
    q1 = {r["t"]: r for r in con.execute(f"SELECT * FROM '{out}/q1_triggers.parquet'").arrow().read_all().to_pylist()}
    # C3: at S + 70 the circulation is CR 300 + I 100 - E 5 + P 20 (B2's PDA) + C2 20 = 435; with C2's leg dropped it would be 415
    assert q1[S + 70]["circ"] == 435 * U and q1[S + 70]["h_ins"] == 400 * U
    # bar (b) at S + 50 (legs-only share 400 / 395 > 1): non-insider sales in the hour after t = C 10 at S + 150, D 10 at S + 100 and 10 at S + 400
    r = q1[S + 50]
    assert r["share_legs"] > 1 and r["sellers"] == 2 and r["sold_1h"] == 30 * U
    # C2a: B -> C at S + 100 is before B's receipt from I at S + 200: not a second hop, C's sale is unexplained.
    # C2b: D received 20 at S + 300: its sale at S + 100 cannot be explained, the one at S + 400 can -> 10 of 30
    # (the old code: hop2 counted B -> C, and D's receipts up to its last sale explained both sales: 30 of 30)
    assert r["from_insider_1hop"] == 10 * U and r["from_insider_2hops"] == 10 * U and abs(r["explained_share"] - 10 / 30) < 1e-12


def test_diag_residuals_name_the_pattern(tmp_path, monkeypatch):
    """transfers_diag: the per-transaction residual identity holds on a conserving fixture, and a doubled row shows up
    as the pattern carrying the deficit."""
    from wallets import transfers_diag
    _store(tmp_path / "s.duckdb")
    root = _chunk(tmp_path / "chunks")
    # double the insider's transfer row on the receiving side: a kept non-trade row with no counterpart
    d = tmp_path / "chunks" / "c0" / "token_deltas"
    t = pq.read_table(d / "part-00000.parquet").to_pylist()
    t.append({**t[1], "owner": "X2", "delta_raw": str(60 * U)})
    pq.write_table(pa.Table.from_pylist(t), d / "part-00000.parquet")
    monkeypatch.setenv("LAB_STORE", str(tmp_path / "s.duckdb"))
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path / "spill"))
    out = tmp_path / "diag"
    transfers_diag.main(["--chunks", root, "--out", str(out)])
    con = duckdb.connect()
    res = con.execute(f"SELECT slot, residual, venue, mint_fails FROM '{out}/txres.parquet'").fetchall()
    assert res == [(S + 20, 60 * U, "none", True)]          # exactly the transfer transaction carries the surplus
    info = json.load(open(out / "run-diag.json"))
    assert info["identity"] == [1, 1] and info["cons"][1] < info["cons"][0]
    md = (out / "diag.md").read_text()
    assert "| none | – | – | · | · | y | · | · | y |" in md and "100.0 %" in md
