"""Transfers module on a mini store and a mini transfers chunk: the owner ledger (legs plus the kept delta rows, with
the create transaction's net vault row and a kept row under another owner than the store's trader; the check 4.1
patterns of 2026-10-15: a pool boost burning from its vault without a delta row, a bot that buys and forwards the
tokens in the same transaction, a PDA feeding a seller in the sell transaction, a PDA-held trader account flagged
is_vault), conservation, the corrected insider share against the legs-only share, bar (b) for a legs-only share > 1,
wallet births with a hub and a funder of six same-label wallets, and the transfer-fed selling of a new pool."""

import json
import re
import os

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from wallets import keys, transfers

E = 1040
LO = E * 432_000
S = LO + 1_000
T0 = 1_790_000_000
M = "Mpump"
U = 10**6  # one token unit scale: 1,000 tokens minted
SUPPLY = 1000 * U
CV = keys.curve_pda(M)   # the owner of the curve's token vault


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
        (M, S + 45, 1, True, 20 * U, 2 * 10**8, "R"),      # bot R buys 20 and forwards 19 to W and 1 to FEE in the same tx (net 0: no row)
        (M, S + 46, 1, True, 10 * U, 10**8, "PDA1"),      # a PDA buys 10 (its row equals the leg: dropped)
        (M, S + 47, 1, False, 10 * U, 10**8, "Z"),        # PDA1 transfers 10 to Z, Z sells them in the same tx (Z net 0; PDA1's row kept, flagged is_vault)
        ("Old", S + 3, 1, True, 10 * U, 10**8, "Q")])
    con.execute("CREATE TABLE pools (pool VARCHAR, mint VARCHAR, quote_class VARCHAR, orientation VARCHAR, create_slot UBIGINT)")
    con.execute("INSERT INTO pools VALUES ('P', ?, 'sol', 'normal', ?)", [M, S + 60])
    con.execute("CREATE TABLE pool (pool VARCHAR, slot UBIGINT, tx_index BIGINT, kind VARCHAR, trader VARCHAR, token_buy BOOLEAN, token_amount BIGINT, sol_amount BIGINT, base BIGINT)")
    con.execute("INSERT INTO pool VALUES ('P', ?, 1, 'sell', 'X', false, ?, 50000000, NULL), ('P', ?, 2, 'buy', 'Y', true, ?, 20000000, NULL)", [S + 70, 5 * U, S + 70, 2 * U])
    con.execute("INSERT INTO pool VALUES ('P', ?, 3, 'boost', 'BOOSTER', NULL, NULL, NULL, ?)", [S + 80, 3 * U])   # boost_buy_and_burn: 3 burned from the vault
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
        td(S, 1, CV, SUPPLY - 300 * U, vault=True),        # the create: mint 1000, dev buy 300 -> the vault's net row is kept
        td(S + 20, 1, "I", -60 * U, trade=False), td(S + 20, 1, "X", 60 * U, trade=False),   # I transfers 60 to X (no trade in that tx)
        td(S + 30, 1, "X", -50 * U),                        # X's sell: kept under the chain owner X (the store says XSIG)
        td(S + 45, 1, "W", 19 * U), td(S + 45, 1, "FEE", 1 * U),   # R's forwards; R itself has no row (net 0), the vault's row was dropped
        td(S + 47, 1, "PDA1", -10 * U, vault=True),         # PDA1 -> Z -> curve: PDA1's row is kept and flagged (off-curve owner); Z and the vault have none
        dict(td(S + 90, 1, "GHOST", 7 * U), failed=True),   # a failed-flag row must be ignored
    ]), d / "token_deltas" / "part-00000.parquet")

    def tt(slot, tx, kind, src, dst, amount, in_trade):
        return {"slot": slot, "tx_index": tx, "ix_path": "1.1", "program": "token", "kind": kind, "mint": M, "source_account": None, "source_owner": src,
                "dest_account": None, "dest_owner": dst, "authority": src, "amount_raw": amount, "decimals": 6, "outer_program": "pump" if in_trade else "token",
                "in_trade": in_trade, "owner_unresolved": False, "fee_payer": src or dst, "has_pump_event": in_trade, "failed": False}
    pq.write_table(pa.Table.from_pylist([
        tt(S, 1, "mint_to", None, CV, SUPPLY, True), tt(S, 1, "transfer_checked", CV, "CR", 300 * U, True),
        tt(S + 5, 1, "transfer_checked", CV, "I", 100 * U, True),
        tt(S + 20, 1, "transfer", "I", "X", 60 * U, False),
        tt(S + 30, 1, "transfer_checked", "X", CV, 50 * U, True), tt(S + 50, 1, "transfer_checked", "X", CV, 10 * U, True),
        tt(S + 45, 1, "transfer_checked", CV, "R", 20 * U, True), tt(S + 45, 1, "transfer", "R", "W", 19 * U, False), tt(S + 45, 1, "transfer", "R", "FEE", 1 * U, False),
        tt(S + 46, 1, "transfer_checked", CV, "PDA1", 10 * U, True),
        tt(S + 47, 1, "transfer", "PDA1", "Z", 10 * U, False), tt(S + 47, 1, "transfer_checked", "Z", CV, 10 * U, True),
        tt(S + 80, 3, "burn", "P", None, 3 * U, True)]),
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
    pq.write_table(pa.Table.from_pylist([{"mint": M, "t": S + 40, "ins_share": 400 / 350}, {"mint": M, "t": S + 48, "ins_share": 400 / 370},
                                         {"mint": "Old", "t": S + 40, "ins_share": 0.1}]), tmp_path / "trig.parquet")
    pq.write_table(pa.Table.from_pylist([{"wallet": f"b{i}", "label": "bundler"} for i in range(6)] + [{"wallet": "r0", "label": "retail"}, {"wallet": "r1", "label": "retail"}]),
                   tmp_path / "labels.parquet")
    monkeypatch.setenv("LAB_STORE", str(tmp_path / "s.duckdb"))
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path / "spill"))
    out = tmp_path / "out"
    transfers.main(["--chunks", root, "--triggers", str(tmp_path / "trig.parquet"), "--labels", str(tmp_path / "labels.parquet"), "--out", str(out)])
    con = duckdb.connect()
    # conservation: the window's mint only (Old was created before it); 1000 minted - 3 burned by the boost = 300 (CR) + 40 (I) + 10 (X)
    # + 19 (W) + 1 (FEE) + 627 (vault: 650 - 20 (R) - 10 (PDA1) + 10 (Z) - 3) at the day end; R, PDA1 and Z hold 0
    cons = con.execute(f"SELECT mint, held, minted, burned, diff, ok FROM '{out}/conservation.parquet' ORDER BY day").fetchall()
    assert {c[0] for c in cons} == {M} and all(c[1] == SUPPLY - 3 * U and c[3] == 3 * U and c[4] == 0 and c[5] for c in cons) and len(cons) == 3  # one row per UTC day
    q1 = {r["t"]: r for r in con.execute(f"SELECT * FROM '{out}/q1_triggers.parquet'").arrow().read_all().to_pylist()}
    assert set(q1) == {S + 40, S + 48} and all(r["mint"] == M for r in q1.values())  # Old is outside the window
    r = q1[S + 40]
    # at t = S + 40: CR 300, I 40, X 10 in circulation (350); insiders CR and I hold 340
    assert r["circ"] == 350 * U and r["h_ins"] == 340 * U and abs(r["share_c"] - 340 / 350) < 1e-12 and r["undefined"] is None
    assert abs(r["share_legs"] - 400 / 350) < 1e-12 and abs(r["share_legs"] - r["ins_share_old"]) < 1e-9  # legs only: > 1, reproduces the engine
    # bar (b): X (not an insider) sells 10 on the curve and 5 on pool P in the hour after t; it received 60 from the insider I: fully
    # explained. Z sells 10 fed by PDA1 (no insider): sellers 2, 15 of 25 explained
    assert r["sellers"] == 2 and r["sold_1h"] == 25 * U and r["from_insider_1hop"] == 15 * U and abs(r["explained_share"] - 15 / 25) < 1e-12
    # at t = S + 48 the forwards W 19 and FEE 1 are in circulation (370), R / PDA1 / Z hold 0, PDA1 is a holder not a vault; legs only 400 / 370
    r = q1[S + 48]
    assert r["circ"] == 370 * U and r["h_ins"] == 340 * U and r["holders"] == 5 and r["neg_owners"] == 0 and abs(r["share_legs"] - 400 / 370) < 1e-12
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
    con.execute("CREATE TABLE pool (pool VARCHAR, slot UBIGINT, tx_index BIGINT, kind VARCHAR, trader VARCHAR, token_buy BOOLEAN, token_amount BIGINT, sol_amount BIGINT, base BIGINT)")
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
        td(S, 1, keys.curve_pda(M2), SUPPLY - 300 * U, vault=True),                    # the create: mint 1000, dev buy 300
        td(S + 60, 1, "P", 20 * U, vault=True), td(S + 60, 1, keys.curve_pda(M2), -40 * U, vault=True),   # the bundle: B2's 20 on the PDA P (flagged: off-curve owner); C2's row dropped (= its leg); the vault's net
        td(S + 200, 1, "I", -50 * U, trade=False), td(S + 200, 1, "B", 50 * U, trade=False),   # I -> B 50 at S + 200
        td(S + 100, 1, "B", -50 * U, trade=False), td(S + 100, 1, "C", 50 * U, trade=False),   # B -> C 50 at S + 100: before B received from I
        td(S + 300, 1, "I", -20 * U, trade=False), td(S + 300, 1, "D", 20 * U, trade=False),   # I -> D 20 at S + 300: between D's two sales
    ]), d / "token_deltas" / "part-00000.parquet")

    def tt(slot, tx, kind, src, dst, amount, in_trade):
        return {"slot": slot, "tx_index": tx, "ix_path": "1.1", "program": "token", "kind": kind, "mint": M2, "source_account": None, "source_owner": src,
                "dest_account": None, "dest_owner": dst, "authority": src, "amount_raw": amount, "decimals": 6, "outer_program": "pump" if in_trade else "token",
                "in_trade": in_trade, "owner_unresolved": False, "fee_payer": src or dst, "has_pump_event": in_trade, "failed": False}
    pq.write_table(pa.Table.from_pylist([
        tt(S, 1, "mint_to", None, keys.curve_pda(M2), SUPPLY, True),
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
    # the same transaction closes an account: a token_transfers row without an amount (the NAS run of 2026-10-15 on
    # 1040 crashed in the render on such a NULL once the examples were no longer trades)
    d = tmp_path / "chunks" / "c0" / "token_transfers"
    t = pq.read_table(d / "part-00000.parquet").to_pylist()
    row = next(x for x in t if x["slot"] == S + 20)
    t.append({**row, "ix_path": "1.2", "kind": "close_account", "amount_raw": None, "dest_owner": None})
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
    assert "| close_account | 1.2 | I | None | – | False | False |" in md   # the NULL amount renders as –


def test_diag_on_a_conserving_fixture_renders_empty(tmp_path, monkeypatch):
    """No failing mint: no residual rows, Σ|residual| 0, and the report still renders (boost rows without token_amount
    and all)."""
    from wallets import transfers_diag
    _store(tmp_path / "s.duckdb")
    root = _chunk(tmp_path / "chunks")
    monkeypatch.setenv("LAB_STORE", str(tmp_path / "s.duckdb"))
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path / "spill"))
    out = tmp_path / "diag"
    transfers_diag.main(["--chunks", root, "--out", str(out)])
    con = duckdb.connect()
    assert con.execute(f"SELECT count(*) FROM '{out}/txres.parquet'").fetchone() == (0,)
    info = json.load(open(out / "run-diag.json"))
    assert info["identity"] == [1, 1] and info["cons"][1] == info["cons"][0]
    md = (out / "diag.md").read_text()
    assert "conservation 3 / 3 mint-days (100.0 %)" in md and "with a non-zero residual 0; mints 1, failing 4.1 on some day 0" in md
    assert "Σ|residual| over the failing mints 0, over the conserving mints 0" in md and "### Rows of the first three examples" in md


M3 = "M3pump"


def _store_hops(path):
    """Job 79 (2026-10-15) residual patterns, and check 4.2: U1 buys 30 on the pool through a router (the pool's output lands on the
    router's account and is forwarded to U1 in the same transaction) and sells 5 through a temporary account (owner
    unresolved, closed in the transaction) that feeds the vault; a bot R buys 20 and forwards 19 from its own account."""
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE meta (json VARCHAR)")
    con.execute("INSERT INTO meta VALUES (?)", [json.dumps({"ms_per_slot": {str(E): 267.3}})])
    con.execute("CREATE TABLE blocks (slot UBIGINT, block_time BIGINT)")
    con.executemany("INSERT INTO blocks VALUES (?, ?)", [(x, T0 + (x - S) // 100 * 86_400) for x in range(S - 10, S + 300)])
    con.execute("CREATE TABLE mints (mint VARCHAR, create_slot UBIGINT, creator VARCHAR, supply UBIGINT, mayhem BOOLEAN, cashback BOOLEAN, holder_reward BOOLEAN)")
    con.execute("INSERT INTO mints VALUES (?, ?, 'CR', ?, false, false, false)", [M3, S, SUPPLY])
    con.execute("CREATE TABLE curve (mint VARCHAR, slot UBIGINT, tx_index UBIGINT, is_buy BOOLEAN, t HUGEINT, q HUGEINT, trader VARCHAR, variant VARCHAR)")
    con.executemany("INSERT INTO curve VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
        (M3, S, 1, True, 300 * U, 3 * 10**9, "CR", "buy"),
        (M3, S + 45, 1, True, 20 * U, 2 * 10**8, "R", "buy")])
    con.execute("CREATE TABLE pools (pool VARCHAR, mint VARCHAR, quote_class VARCHAR, orientation VARCHAR, create_slot UBIGINT)")
    con.execute("INSERT INTO pools VALUES ('P3', ?, 'sol', 'normal', ?)", [M3, S + 2])   # migrated 2 slots after the create
    con.execute("CREATE TABLE pool (pool VARCHAR, slot UBIGINT, tx_index BIGINT, kind VARCHAR, trader VARCHAR, token_buy BOOLEAN, token_amount BIGINT, sol_amount BIGINT, base BIGINT)")
    con.execute("INSERT INTO pool VALUES ('P3', ?, 1, 'buy', 'U1', true, ?, 30000000, NULL), ('P3', ?, 1, 'sell', 'U1', false, ?, 5000000, NULL)", [S + 70, 30 * U, S + 71, 5 * U])
    con.execute("INSERT INTO pool VALUES ('P3', ?, 1, 'buy', 'PB', true, ?, 10000000, NULL)", [S + 3, 10 * U])   # a pool buy 3 slots after the create: no insider
    # job 815 (2026-10-16): a custody wallet W moves 18 to the per-trade signer SIG, which sells them, and pays 1 to FEE
    # from the same account; a buy whose output lands on CUST (with a 2 top-up from U1 in the same tx), not on the signer SIG2
    con.execute("INSERT INTO pool VALUES ('P3', ?, 1, 'sell', 'SIG', false, ?, 18000000, NULL), ('P3', ?, 1, 'buy', 'SIG2', true, ?, 10000000, NULL)", [S + 75, 18 * U, S + 76, 10 * U])
    con.close()


def _chunk_hops(root):
    d = root / "c0"
    for t in ("token_deltas", "token_transfers", "sol_funding"):
        (d / t).mkdir(parents=True)
    (d / "_manifest.json").write_text(json.dumps({"status": "complete", "slot_start": LO, "slot_end_exclusive": LO + 432_000, "extractor_sha256": "abc"}))

    def td(slot, tx, owner, delta, vault=False, trade=True):
        return {"slot": slot, "tx_index": tx, "mint": M3, "owner": owner, "token_account": owner + "_ta", "delta_raw": str(delta), "decimals": 6,
                "is_vault": vault, "has_pump_event": trade, "has_pump_event_same_mint": trade, "fee_payer": owner, "failed": False}
    pq.write_table(pa.Table.from_pylist([
        td(S, 1, keys.curve_pda(M3), SUPPLY - 300 * U, vault=True),      # the create: mint 1000, dev buy 300 (CR's row dropped)
        td(S + 45, 1, "W", 19 * U), td(S + 45, 1, "FEE", 1 * U),          # R's forwards; R has no row (net 0); the vault's row dropped
        td(S + 60, 1, "CR", -300 * U), td(S + 60, 1, "P3", 300 * U, vault=True),   # CR deposits the pool
        # S + 3: PB's row equals its leg (dropped), the pool vault's row dropped
        # S + 70: U1's row equals its leg (dropped), the router's net is 0 (no row), the pool vault's row dropped
        # S + 71: U1's row equals its leg (dropped), the temporary account nets 0 and is closed, the vault's row dropped
        td(S + 75, 1, "W", -19 * U), td(S + 75, 1, "FEE", 1 * U),      # the custody sell: W's whole net (18 to SIG + 1 fee), SIG nets 0 (no row), the vault's row dropped
        td(S + 76, 1, "CUST", 12 * U), td(S + 76, 1, "U1", -2 * U),    # the custody buy: CUST's whole net (10 from the vault + 2 from U1), SIG2 no row, the vault's row dropped
    ]), d / "token_deltas" / "part-00000.parquet")

    def tt(slot, tx, ix, kind, src, dst, amount, in_trade, src_acc=None, dst_acc=None, unresolved=False):
        return {"slot": slot, "tx_index": tx, "ix_path": ix, "program": "token", "kind": kind, "mint": M3, "source_account": src_acc, "source_owner": src,
                "dest_account": dst_acc, "dest_owner": dst, "authority": src, "amount_raw": amount, "decimals": 6, "outer_program": "pump" if in_trade else "token",
                "in_trade": in_trade, "owner_unresolved": unresolved, "fee_payer": src or dst or "U1", "has_pump_event": in_trade, "failed": False}
    pq.write_table(pa.Table.from_pylist([
        tt(S, 1, "1.1", "mint_to", None, keys.curve_pda(M3), SUPPLY, True), tt(S, 1, "1.2", "transfer_checked", keys.curve_pda(M3), "CR", 300 * U, True),
        tt(S + 45, 1, "1.1", "transfer_checked", keys.curve_pda(M3), "R", 20 * U, True),
        tt(S + 45, 1, "2.1", "transfer", "R", "W", 19 * U, False), tt(S + 45, 1, "2.2", "transfer", "R", "FEE", 1 * U, False),   # by owner (no accounts): R is the trader, not a hop
        tt(S + 60, 1, "1.1", "transfer", "CR", "P3", 300 * U, True),
        tt(S + 3, 1, "1.1", "transfer_checked", "P3", "PB", 10 * U, True),
        # the router buy: vault -> ROUTER's account (in trade), ROUTER -> U1 (not in trade), same amount
        tt(S + 70, 1, "4.19", "transfer_checked", "P3", "ROUTER", 30 * U, True, "P3_ta", "ROUTER_ta"),
        tt(S + 70, 1, "4.31", "transfer_checked", "ROUTER", "U1", 30 * U, False, "ROUTER_ta", "U1_ta"),
        # the temporary-account sell: U1 -> TMP (not in trade, owner unresolved), close, TMP -> vault (in trade)
        tt(S + 71, 1, "3", "transfer_checked", "U1", None, 5 * U, False, "U1_ta", "TMP_ta", unresolved=True),
        tt(S + 71, 1, "4", "close_account", "U1", None, None, False, "TMP_ta", None),
        tt(S + 71, 1, "6.1", "transfer_checked", None, "P3", 5 * U, True, "TMP_ta", "P3_ta", unresolved=True),
        # the custody sell: W -> SIG and W -> FEE (both in trade), SIG -> vault; no kept row has the leg's amount (W's is 19)
        tt(S + 75, 1, "3.0", "transfer_checked", "W", "SIG", 18 * U, True, "W_ta", "SIG_ta"),
        tt(S + 75, 1, "3.15", "transfer_checked", "W", "FEE", 1 * U, True, "W_ta", "FEE_ta"),
        tt(S + 75, 1, "3.3", "transfer_checked", "SIG", "P3", 18 * U, True, "SIG_ta", "P3_ta"),
        # the custody buy: vault -> CUST (in trade, CUST is not the store's trader), U1 -> CUST 2 (in trade)
        tt(S + 76, 1, "1.12", "transfer_checked", "P3", "CUST", 10 * U, True, "P3_ta", "CUST_ta"),
        tt(S + 76, 1, "1.21", "transfer_checked", "U1", "CUST", 2 * U, True, "U1_ta", "CUST_ta")]),
        d / "token_transfers" / "part-00000.parquet")
    pq.write_table(pa.Table.from_pylist([{"slot": S + 1, "tx_index": 1, "ix_path": "1", "from_account": "F", "to_account": "w0", "lamports": 50_000_000, "kind": "transfer",
                                          "fee_payer": "F", "has_pump_event": False, "failed": False}]), d / "sol_funding" / "part-00000.parquet")
    return str(root)


def test_router_and_temporary_account_hops_count_once(tmp_path, monkeypatch):
    """The trader's non-trade transfer that continues the trade's in-trade transfer through an intermediate account is
    not a flow: U1's buy of 30 and sell of 5 appear once (job 79: the net leg was twice the amount); R's forwards stay.
    A trade whose tokens a kept holder row already carries (job 815: W's custody sell through the signer SIG with a fee
    from the same account, SIG2's buy landing on CUST with a top-up) adds no leg: the chain's rows balance by themselves."""
    _store_hops(tmp_path / "s.duckdb")
    root = _chunk_hops(tmp_path / "chunks")
    pq.write_table(pa.Table.from_pylist([{"mint": M3, "t": S + 72}, {"mint": M3, "t": S + 77}]), tmp_path / "trig.parquet")
    monkeypatch.setenv("LAB_STORE", str(tmp_path / "s.duckdb"))
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path / "spill"))
    out = tmp_path / "out"
    transfers.main(["--chunks", root, "--triggers", str(tmp_path / "trig.parquet"), "--out", str(out)])
    con = duckdb.connect()
    cons = con.execute(f"SELECT held, minted, diff, ok FROM '{out}/conservation.parquet' ORDER BY day").fetchall()
    assert cons and all(c[0] == SUPPLY and c[2] == 0 and c[3] for c in cons)   # without the hop rule: +30 and -5 doubled -> diff 25; without the balance rule: -18 and +10 doubled -> diff -8
    q1 = {r[0]: r[1:] for r in con.execute(f"SELECT t, circ, h_ins, share_c FROM '{out}/q1_triggers.parquet'").fetchall()}
    # circulation at S + 72: CR 0 (deposited) + W 19 + FEE 1 + U1 25 + PB 10 = 55 (curve vault 680 and pool vault 265 excluded);
    # insiders = CR (creator) only: PB bought on the pool 3 slots after the create and R on the curve after the 10 s window
    assert q1[S + 72] == (55 * U, 0, 0)
    # at S + 77: W 0, FEE 2, U1 23, PB 10, CUST 12 = 47; the signers SIG and SIG2 hold nothing (with their legs added: 39)
    assert q1[S + 77] == (47 * U, 0, 0)
