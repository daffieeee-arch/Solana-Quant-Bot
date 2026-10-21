"""Wallet accounting on synthetic recorder segments: user-side cash on a normal pool, a reversed pool and a SOL curve,
the fee once per transaction (tx_fee + tip, no priority fee again), the proxy that goes to its fee payer, the offset
to the previous transaction in the pool, the UTC day from the block time, a row recorded twice, the pools that stay
unpriced (unknown, another quote, two mint pairs), the pool classes from a create, from the trades' mints and from
the paper store, and the guards (no segment before epoch 1064, no locked store)."""

import json

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from wallets import live_wallet_pnl as lw

SOL = 10**9
WSOL = lw.WSOL
S0 = lw.MIN_SLOT + 1_000
MIDNIGHT = 1_792_627_200  # 2026-10-22 00:00 UTC
DAY1, DAY2 = "2026-10-21", "2026-10-22"
# Wallets (base58 shapes; only the shape is checked)
A = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1"
B = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB1"
PROXY = "PPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPP1"
PAYER = "QQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQ1"
TOKEN = "TTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTpump"
PUMP_Q = "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn"

I, S = pa.int64(), pa.string()
POOL_COLS = {"pool": S, "slot": I, "tx_index": I, "outer_ix": I, "inner_ix": I, "user": S, "fee_payer": S, "outer_program": S,
             "priority_fee": I, "jito_tip": I, "tx_fee": I, "parent_ix_disc": S, "pool_base_token_reserves": I,
             "pool_quote_token_reserves": I, "lp_fee_basis_points": I, "protocol_fee_basis_points": I, "coin_creator": S,
             "coin_creator_fee_basis_points": I, "virtual_quote_reserves": I, "ix_base_mint": S, "ix_quote_mint": S}
BUY_COLS = {**POOL_COLS, "base_amount_out": I, "quote_amount_in_with_lp_fee": I, "user_quote_amount_in": I, "quote_amount_in": I,
            "max_quote_amount_in": I, "min_base_amount_out": I}
SELL_COLS = {**POOL_COLS, "base_amount_in": I, "quote_amount_out_without_lp_fee": I, "user_quote_amount_out": I,
             "min_quote_amount_out": I}
TRADE_COLS = {"mint": S, "slot": I, "tx_index": I, "outer_ix": I, "inner_ix": I, "parent_ix_disc": S, "is_buy": pa.bool_(),
              "token_amount": I, "quote_amount": I, "sol_amount": I, "fee": I, "creator_fee": I, "fee_basis_points": I,
              "creator_fee_basis_points": I, "virtual_token_reserves": I, "virtual_quote_reserves": I, "virtual_sol_reserves": I,
              "real_token_reserves": I, "real_quote_reserves": I, "real_sol_reserves": I, "user": S, "fee_payer": S,
              "outer_program": S, "priority_fee": I, "jito_tip": I, "tx_fee": I, "mayhem_mode": pa.bool_(), "quote_mint": S}
CREATE_POOL_COLS = {"pool": S, "slot": I, "tx_index": I, "base_mint": S, "quote_mint": S}
BLOCK_COLS = {"slot": I, "block_time": I}


def _write(d, table, cols, rows):
    (d / table).mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist([{k: r.get(k) for k in cols} for r in rows], schema=pa.schema(list(cols.items()))),
                   d / table / "part-00000.parquet")


def segment(root, seq, first, last, buys=(), sells=(), trades=(), creates=(), block_t0=MIDNIGHT - 100):
    d = root / "segments" / f"{seq:09d}"
    d.mkdir(parents=True)
    _write(d, "blocks", BLOCK_COLS, [{"slot": s, "block_time": block_t0 + (s - first) // 2} for s in range(first, last + 1)])
    for table, cols, rows in (("pump_amm/BuyEvent", BUY_COLS, buys), ("pump_amm/SellEvent", SELL_COLS, sells),
                              ("pump/TradeEvent", TRADE_COLS, trades), ("pump_amm/CreatePoolEvent", CREATE_POOL_COLS, creates)):
        if rows:
            _write(d, table, cols, rows)
    (d / "_segment.json").write_text(json.dumps({"seq": seq, "first_slot": first, "last_slot": last, "complete": True}))


def pev(pool, slot, tx, user, sol, tokens, base_mint, quote_mint, fee_payer=None, inner=0, tx_fee=5_000, prio=0, tip=0, buy=True):
    """A PumpSwap event row; `sol` and `tokens` go to the quote or base side by the pool's mints."""
    reversed_ = base_mint == WSOL
    base, quote = (sol, tokens) if reversed_ else (tokens, sol)
    r = {"pool": pool, "slot": slot, "tx_index": tx, "outer_ix": 0, "inner_ix": inner, "user": user, "fee_payer": fee_payer or user,
         "outer_program": "x", "priority_fee": prio, "jito_tip": tip, "tx_fee": tx_fee, "parent_ix_disc": "aa",
         "pool_base_token_reserves": 10**12, "pool_quote_token_reserves": 100 * SOL, "lp_fee_basis_points": 20,
         "protocol_fee_basis_points": 5, "coin_creator": "11111111111111111111111111111111", "coin_creator_fee_basis_points": 0,
         "virtual_quote_reserves": 0, "ix_base_mint": base_mint, "ix_quote_mint": quote_mint}
    if buy:  # the user receives base
        r.update(base_amount_out=base, quote_amount_in_with_lp_fee=quote, user_quote_amount_in=quote, quote_amount_in=quote,
                 max_quote_amount_in=quote, min_base_amount_out=0)
    else:
        r.update(base_amount_in=base, quote_amount_out_without_lp_fee=quote, user_quote_amount_out=quote, min_quote_amount_out=0)
    return r


def tev(mint, slot, tx, user, q, fee, creator_fee, buy, quote_mint=None, fee_payer=None, tip=0):
    return {"mint": mint, "slot": slot, "tx_index": tx, "outer_ix": 0, "inner_ix": 0, "parent_ix_disc": "aa", "is_buy": buy,
            "token_amount": 10**9, "quote_amount": q, "sol_amount": q, "fee": fee, "creator_fee": creator_fee, "fee_basis_points": 95,
            "creator_fee_basis_points": 30, "virtual_token_reserves": 10**15, "virtual_quote_reserves": 30 * SOL,
            "virtual_sol_reserves": 30 * SOL, "real_token_reserves": 10**15, "real_quote_reserves": SOL, "real_sol_reserves": SOL,
            "user": user, "fee_payer": fee_payer or user, "outer_program": "x", "priority_fee": 0, "jito_tip": tip, "tx_fee": 5_000,
            "mayhem_mode": False, "quote_mint": quote_mint}


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "recorder"
    # before epoch 1064: never read (a trade of A that would otherwise count)
    segment(r, 0, lw.MIN_SLOT - 300, lw.MIN_SLOT - 1, buys=[pev("NORM", lw.MIN_SLOT - 10, 1, A, 50 * SOL, 10**9, TOKEN, WSOL)])
    s = S0
    segment(r, 1, s, s + 199, creates=[{"pool": "NORM", "slot": s, "tx_index": 0, "base_mint": TOKEN, "quote_mint": WSOL}],
            buys=[
                pev("NORM", s + 10, 1, B, 2 * SOL, 10**9, TOKEN, WSOL),                       # B first: A's offset is 3
                pev("NORM", s + 13, 4, A, 1 * SOL, 10**9, TOKEN, WSOL, prio=7_000, tip=1_000),  # A buys twice in one tx:
                pev("NORM", s + 13, 4, A, 1 * SOL, 10**9, TOKEN, WSOL, inner=1, prio=7_000, tip=1_000),  # the fee once
                pev("NOPE", s + 30, 1, A, 9 * SOL, 10**9, None, None),                       # multi-hop, no mints, no store: unknown
                pev("OTHER", s + 31, 1, A, 9 * SOL, 10**9, TOKEN, PUMP_Q),                    # no WSOL side: another quote
                pev("NORM", s + 40, 1, PROXY, 4 * SOL, 10**9, TOKEN, WSOL, fee_payer=PAYER),   # a proxy: the payer's trade
            ],
            sells=[pev("NORM", s + 13, 6, B, 5 * SOL // 10, 10**9, TOKEN, WSOL, buy=False),     # same slot as A's: offset 0
                   pev("REV", s + 20, 2, A, 3 * SOL, 10**9, WSOL, TOKEN, buy=False)],            # reversed: A sells 3 SOL (base) for tokens
            trades=[tev(TOKEN, s + 50, 1, A, 2 * SOL, 19_000_000, 6_000_000, True),             # curve buy: q + fees
                    tev(PUMP_Q, s + 51, 1, A, 5 * SOL, 0, 0, True, quote_mint=PUMP_Q)])         # curve with a PUMP quote
    # the next UTC day (block times past midnight); slot s + 13 recorded again after a restart: counted once
    segment(r, 2, s + 200, s + 399, block_t0=MIDNIGHT + 10,
            buys=[pev("NORM", s + 13, 4, A, 1 * SOL, 10**9, TOKEN, WSOL, prio=7_000, tip=1_000),
                  pev("REV", s + 260, 1, A, 4 * SOL, 10**9, WSOL, TOKEN, buy=True)],             # reversed: A buys 4 SOL (base)
            sells=[pev("NORM", s + 250, 3, A, 25 * SOL // 10, 2 * 10**9, TOKEN, WSOL, buy=False)])
    return r


def by(rows):
    return {(r["wallet"], r["day"]): r for r in rows}


def test_cash_fees_offsets_and_days_per_wallet(root):
    rows, summary = lw.run([A, B, PROXY, PAYER], root=str(root), store=None, log=lambda m: None)
    r = by(rows)
    a1, a2 = r[(A, DAY1)], r[(A, DAY2)]
    # day 1: NORM 2 x 1 SOL, REV 3 SOL, curve 2 + 0.019 + 0.006 SOL bought; NOPE and OTHER and the PUMP curve unpriced
    assert a1["buy_sol"] == pytest.approx(2 + 3 + 2.025) and a1["sell_sol"] == 0
    assert (a1["legs"], a1["priced_legs"], a1["unknown_pool_legs"], a1["non_sol_legs"]) == (7, 4, 1, 2)
    assert (a1["curve_legs"], a1["pool_legs"], a1["buys"], a1["sells"]) == (2, 5, 4, 0)
    # fees: tx_fee 5000 + tip 1000 once for the two-leg tx (no priority fee on top), 5000 each for the 5 others
    assert a1["txs"] == 6 and a1["fees_sol"] == pytest.approx((6_000 + 5 * 5_000) / SOL)
    assert a1["cash_sol"] == pytest.approx(-7.025) and a1["net_cash_sol"] == pytest.approx(-7.025 - 31_000 / SOL)
    # day 2: sells 2.5 SOL in NORM and 4 SOL from REV; the restart's copy of slot s + 13 is not counted again
    assert (a2["legs"], a2["sell_sol"], a2["buy_sol"], a2["sells"]) == (2, pytest.approx(6.5), 0, 2)
    assert summary["duplicate_rows_dropped"] == 1
    # offsets: A's NORM tx 3 slots after B's buy; its other day-1 txs are the first in their pools (no offset)
    assert (a1["offset_n"], a1["offset_min"], a1["offset_median"]) == (1, 3, 3)
    assert (a2["offset_n"], a2["offset_min"], a2["offset_median"]) == (2, 210, 225)  # after the proxy's tx and REV's first
    b1 = r[(B, DAY1)]  # B's buy is NORM's first tx; its sell is in the slot of A's tx, after it: offset 0
    assert (b1["offset_n"], b1["offset_min"], b1["same_slot_share"]) == (1, 0, 1.0)
    # the proxy's trade goes to its fee payer; the proxy, listed, shows it as signed for another payer
    assert r[(PAYER, DAY1)]["buy_sol"] == pytest.approx(4) and r[(PAYER, DAY1)]["legs"] == 1
    p1 = r[(PROXY, DAY1)]
    assert (p1["legs"], p1["buy_sol"], p1["legs_as_trader_paid_by_other"]) == (0, 0, 1)
    assert summary["wallets_seen"] == 4 and summary["segments_skipped_before_min_slot"] == 1


def test_the_offset_is_to_the_previous_transaction_in_the_same_pool(root):
    con = lw._connect()
    segs, _ = lw.pick_segments(str(root))
    lw.build(con, segs, [A, B, PAYER], lw.MIN_SLOT, None, None, log=lambda m: None)
    got = dict(((p, s, t), o) for p, s, t, o in con.execute(
        "SELECT DISTINCT pool_key, slot, tx_index, offset_slots FROM legs_day WHERE pool_key = 'NORM' ORDER BY slot, tx_index").fetchall())
    assert got == {("NORM", S0 + 10, 1): None, ("NORM", S0 + 13, 4): 3, ("NORM", S0 + 13, 6): 0, ("NORM", S0 + 40, 1): 27,
                   ("NORM", S0 + 250, 3): 210}
    classes = dict(con.execute("SELECT pool, source FROM pool_class").fetchall())
    assert classes == {"NORM": "create", "REV": "ix_mints", "OTHER": "ix_mints", "NOPE": None}


def test_batches_carry_the_offset_and_the_repeat_check_across_their_boundary(root, monkeypatch):
    # one segment per batch: the offset of the next day's first NORM tx and the restart's repeat of slot s + 13 come
    # from last_tx, not from the same batch
    whole, s1 = lw.run([A, B, PAYER], root=str(root), store=None, log=lambda m: None)
    monkeypatch.setattr(lw, "BATCH", 1)
    split, s2 = lw.run([A, B, PAYER], root=str(root), store=None, log=lambda m: None)
    assert split == whole and s2["duplicate_rows_dropped"] == s1["duplicate_rows_dropped"] == 1
    assert s2["legs_all_wallets"] == s1["legs_all_wallets"] == 12 and s1["rows_read"] == 13


def test_the_paper_store_classes_a_pool_the_segments_cannot_and_a_mint_pair_conflict_stays_unknown(root, tmp_path):
    store = tmp_path / "fwd1053-1054.duckdb"
    con = duckdb.connect(str(store))
    con.execute("CREATE TABLE pools (pool VARCHAR, quote_class VARCHAR, orientation VARCHAR)")
    con.execute("INSERT INTO pools VALUES ('NOPE', 'sol', 'normal'), ('ELSE', 'sol', 'normal')")
    con.close()
    rows, summary = lw.run([A], root=str(root), store=str(store), log=lambda m: None)
    a1 = by(rows)[(A, DAY1)]
    assert a1["unknown_pool_legs"] == 0 and a1["buy_sol"] == pytest.approx(2 + 3 + 9 + 2.025)
    assert summary["pool_class_sources"] == {"create": 1, "ix_mints": 2, "store": 1}
    # a pool whose trades name two mint pairs: not priced from them
    segment(root, 3, S0 + 400, S0 + 599, block_t0=MIDNIGHT + 200, buys=[
        pev("TWO", S0 + 410, 1, A, SOL, 10**9, TOKEN, WSOL), pev("TWO", S0 + 411, 1, A, SOL, 10**9, WSOL, TOKEN)])
    rows, summary = lw.run([A], root=str(root), store=None, log=lambda m: None)
    assert by(rows)[(A, DAY2)]["unknown_pool_legs"] == 2 and summary["pool_class_sources"]["conflict"] == 1


def test_nothing_before_epoch_1064_and_no_locked_store(root, tmp_path, capsys):
    with pytest.raises(ValueError, match="clean-locked"):
        lw.pick_segments(str(root), lo=lw.MIN_SLOT - 1)
    segs, early = lw.pick_segments(str(root))
    assert early == 1 and all(m["first_slot"] >= lw.MIN_SLOT for _, _, m in segs)
    for locked in ("clean-1055-1063.duckdb", "fwd1057.duckdb", "store-1063.duckdb", "clean.duckdb"):
        with pytest.raises(ValueError, match="locked"):
            lw.check_store(str(tmp_path / locked))
    assert lw.check_store(lw.PAPER_STORE) == lw.PAPER_STORE
    assert lw.main(["--wallet", A, "--root", str(root), "--from-slot", str(lw.MIN_SLOT - 1), "--no-store",
                    "--out", str(tmp_path / "o")]) == 2
    assert "clean-locked" in capsys.readouterr().err
    assert lw.main(["--wallet", A, "--root", str(root), "--store", str(tmp_path / "clean.duckdb"), "--out", str(tmp_path / "o")]) == 2


def test_wallet_list_and_outputs(root, tmp_path):
    f = tmp_path / "w.txt"
    f.write_text(f"# money-v1 >= 20 SOL/day\n{A}\n{B}  # a bot\n\n{A}\n")
    assert lw.read_wallets(str(f)) == [A, B]
    (tmp_path / "w.json").write_text(json.dumps([B]))
    assert lw.read_wallets(str(tmp_path / "w.json"), [A]) == [A, B]
    for bad in (["0OIl"], []):
        with pytest.raises(ValueError):
            lw.read_wallets(None, bad)
    out = tmp_path / "out"
    assert lw.main(["--wallets", str(f), "--root", str(root), "--no-store", "--out", str(out)]) == 0
    s = json.loads((out / "summary.json").read_text())
    assert s["note"].startswith("partial view") and s["min_slot"] == lw.MIN_SLOT and s["wallet_days"] == 3
    t = pq.read_table(out / "wallet_day.parquet").to_pylist()
    assert {(r["wallet"], r["day"]) for r in t} == {(A, DAY1), (A, DAY2), (B, DAY1)}
    assert (out / "wallet_day.csv").read_text().startswith('"wallet"')
    assert not list(out.glob("*.tmp"))
