"""checks.blindspots on a synthetic chunk: the store's token side per pool variant (normal, reversed
exact-in / exact-out / sell) against the signer's token delta, actor and round-trip counts, the
failure buckets per fee payer, the transfer sections only when the #239 tables exist, and --slots."""

import os

import duckdb
import pytest

from backtest.data import POOL_EXACT_OUT
from checks import blindspots

WSOL = blindspots.WSOL
MINT, RMINT = "Mint1pump", "Rev1pump"
EXACT_IN = "c62e1552b4d9e870"  # any disc not in POOL_EXACT_OUT


def write(chunk, table, rows, cols):
    d = os.path.join(chunk, table)
    os.makedirs(d, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"CREATE TABLE t ({cols})")
    if rows:
        con.executemany(f"INSERT INTO t VALUES ({', '.join('?' * len(rows[0]))})", rows)
    con.execute(f"COPY t TO '{d}/part-00000.parquet' (FORMAT parquet)")


EV = "slot UBIGINT, tx_index UBIGINT, outer_program VARCHAR, parent_ix_name VARCHAR, parent_ix_disc VARCHAR, fee_payer VARCHAR, \"user\" VARCHAR"


def make_chunk(root, transfers=True):
    c = str(root)
    # Curve: tx 1 a direct buy by its fee payer; tx 2 a router buy whose user is not the fee payer.
    write(c, "pump/TradeEvent", [
        (100, 1, "pump", "buy", "d1", "A", "A", MINT, True, 1000, 50),
        (100, 2, "ROUTER", "buy", "d1", "R", "B", MINT, True, 500, 25),
    ], EV + ", mint VARCHAR, is_buy BOOLEAN, token_amount UBIGINT, sol_amount UBIGINT")
    pool_cols = (EV + ", ix_base_mint VARCHAR, ix_quote_mint VARCHAR, base_amount_out UBIGINT, base_amount_in UBIGINT,"
                 " quote_amount_in UBIGINT, user_quote_amount_in UBIGINT, quote_amount_out UBIGINT, user_quote_amount_out UBIGINT")
    write(c, "pump_amm/BuyEvent", [
        # normal pool buy: tokens = base_amount_out
        (101, 3, "pump_amm", "buy", POOL_EXACT_OUT[0], "C", "C", MINT, WSOL, 700, None, 40, 41, None, None),
        # reversed exact-in: the user sells quote_amount_in tokens (fees included) for WSOL
        (101, 4, "pump_amm", "buy_exact_quote_in", EXACT_IN, "D", "D", WSOL, RMINT, 30, None, 900, 897, None, None),
        # reversed exact-out: the user sells user_quote_amount_in tokens
        (101, 5, "pump_amm", "buy", POOL_EXACT_OUT[0], "E", "E", WSOL, RMINT, 30, None, 880, 900, None, None),
        # same-tx round trip with the SellEvent below (tx 6)
        (102, 6, "pump_amm", "buy_exact_quote_in", EXACT_IN, "F", "F", WSOL, RMINT, 20, None, 600, 598, None, None),
    ], pool_cols)
    write(c, "pump_amm/SellEvent", [
        # reversed sell: the user buys user_quote_amount_out tokens with WSOL
        (102, 6, "pump_amm", "sell", "s1", "F", "F", WSOL, RMINT, None, 20, None, None, 601, 599),
        # outside the --slots window used below
        (500, 9, "pump_amm", "sell", "s1", "G", "G", MINT, WSOL, None, 10, None, None, 30, 29),
    ], pool_cols)
    write(c, "tx_ctx", [
        (100, 1, False, "A", 5000, 0, "pump", 1, -55, f"{MINT}:1000"),
        (100, 2, False, "R", 5000, 0, "pump", 1, -5000, None),
        (101, 3, False, "C", 5000, 0, "pump_amm", 1, -46, f"{MINT}:699"),  # 1 token short: differs
        (101, 4, False, "D", 5000, 0, "pump_amm", 1, 25, f"{RMINT}:-900"),
        (101, 5, False, "E", 5000, 0, "pump_amm", 1, 25, f"{RMINT}:-900"),
        (102, 6, False, "F", 5000, 0, "pump_amm", 1, 0, f"{RMINT}:-1"),
        (103, 7, True, "X", 5000, 0, "pump", 1, -5000, None),
        (500, 9, False, "G", 5000, 0, "pump_amm", 1, 29, f"{MINT}:-10"),
    ], "slot UBIGINT, tx_index UBIGINT, failed BOOLEAN, fee_payer VARCHAR, tx_fee UBIGINT, jito_tip UBIGINT, venues VARCHAR,"
       " n_dex UBIGINT, signer_sol_delta BIGINT, signer_token_deltas VARCHAR")
    write(c, "failed_txs", [(103, 7, "X", 5000, 4000, "pump"), (103, 8, "A", 5000, 0, "pump")],
          "slot UBIGINT, tx_index UBIGINT, fee_payer VARCHAR, tx_fee UBIGINT, priority_fee UBIGINT, ix_program VARCHAR")
    if transfers:
        tcols = ("slot UBIGINT, tx_index UBIGINT, kind VARCHAR, outer_program VARCHAR, mint VARCHAR, source_owner VARCHAR,"
                 " dest_owner VARCHAR, amount_raw UBIGINT, in_trade BOOLEAN, has_pump_event BOOLEAN, owner_unresolved BOOLEAN, failed BOOLEAN")
        write(c, "token_transfers", [
            (100, 1, "transfer_checked", "pump", MINT, "CURVE", "A", 1000, True, True, False, False),
            (104, 10, "transfer_checked", None, MINT, "A", "B", 300, False, False, False, False),  # A hands tokens to B (B trades)
            (104, 10, "transfer_checked", None, MINT, "A", "Q", 300, False, False, False, False),
            (104, 10, "transfer_checked", None, MINT, "A", "Z", 300, False, False, False, False),
            (104, 11, "close_account", None, MINT, "A", "A", 0, False, False, False, False),
        ], tcols)
        write(c, "token_deltas", [
            (100, 2, MINT, "B", "-", "500", False, True, True, "R", False),
            (100, 2, MINT, "CURVE", "-", "-500", True, True, True, "R", False),
        ], "slot UBIGINT, tx_index UBIGINT, mint VARCHAR, owner VARCHAR, token_account VARCHAR, delta_raw VARCHAR, is_vault BOOLEAN,"
           " has_pump_event BOOLEAN, has_pump_event_same_mint BOOLEAN, fee_payer VARCHAR, failed BOOLEAN")
        write(c, "sol_funding", [(99, 0, "FUNDER", "B", 10**9, "transfer", False), (99, 1, "FUNDER", "NEW", 10**9, "transfer", False)],
              "slot UBIGINT, tx_index UBIGINT, from_account VARCHAR, to_account VARCHAR, lamports UBIGINT, kind VARCHAR, failed BOOLEAN")
    return c


def by(rows, **kw):
    [r] = [r for r in rows if all(r[k] == v for k, v in kw.items())]
    return r


def test_token_side_per_variant_matches_the_signer_delta(tmp_path):
    r = blindspots.audit(make_chunk(tmp_path))
    td = r["token_delta"]
    assert by(td, venue="curve", side="buy")["n"] == 1 and by(td, venue="curve", side="buy")["equal"] == 1  # tx 2: user is not the payer
    assert by(td, venue="pool", side="buy")["differs"] == 1
    assert by(td, venue="pool_reversed", side="sell", variant="buy_exact_quote_in")["equal"] == 1  # quote_amount_in
    assert by(td, venue="pool_reversed", side="sell", variant="buy")["equal"] == 1  # exact-out: user_quote_amount_in
    assert by(td, venue="pool", side="sell")["equal"] == 1  # normal sell: base_amount_in
    assert r["token_delta_examples"][0]["tx_index"] == 3


def test_actor_round_trips_and_failures(tmp_path):
    r = blindspots.audit(make_chunk(tmp_path))
    assert by(r["actor"], venue="curve")["user_is_fee_payer"] == 0.5
    assert by(r["actor"], venue="curve")["sol_user_not_payer"] == pytest.approx(25 / 1e9)
    assert r["round_trips"] == {"tx_mint": 1, "payers": 1, "two_event_tx_mint": 1}
    assert by(r["round_trip_volume"], venue="pool_reversed")["share"] == pytest.approx(0.4)  # tx 6: 40 of 100 lamports
    assert by(r["round_trip_volume"], venue="curve")["sol_round_trip"] is None
    fp = r["failed_by_payer"]
    assert by(fp, failure_share="only failed")["payers"] == 1  # X: its failed tx_ctx row is not a landing
    assert (by(fp, failure_share="50-90 %")["payers"], by(fp, failure_share="50-90 %")["failed"]) == (1, 1)  # A: 1 of 2
    assert by(fp, failure_share="never failed")["payers"] == 6
    assert r["failed"]["n"] == 2


def test_transfer_sections(tmp_path):
    r = blindspots.audit(make_chunk(tmp_path))
    assert r["tables"] == {"failed_txs": True, "transfers": True}
    assert r["owner_to_owner"] == {"n": 3, "mints": 1, "senders": 1, "receivers": 3, "receivers_who_trade": 1}
    assert r["fan_out_examples"][0]["receivers"] == 3
    assert r["holders_in_trade_tx"] == {"n": 1, "not_fee_payer": 1, "received": 1, "sent": 0}
    assert by(r["sol_funding"], kind="transfer")["funded_who_trade"] == 1


def test_without_transfer_tables_and_with_a_slot_window(tmp_path):
    r = blindspots.audit(make_chunk(tmp_path, transfers=False), 100, 200)
    assert r["tables"]["transfers"] is False and "owner_to_owner" not in r
    assert r["slots"]["hi"] == 103
    assert sum(x["n"] for x in r["trades"]) == 7  # slot 500 is outside


def test_cli_writes_json(tmp_path):
    c = make_chunk(tmp_path / "c")
    out = tmp_path / "o.json"
    assert blindspots.main([c, "--slots", "100:200", "--out", str(out)]) == 0
    assert out.read_text().startswith("{")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
