"""The venue store on a fake chunk: failed transactions dropped, one DBC row per swap from the newest layout (a lone
EvtSwap kept), the chained quote reserve, DAMM v2 / LaunchLab / CPMM rows, the pools, the graduation links, the checks,
and the refusals (an existing file, a gap)."""

import json
import os

import duckdb
import pytest

from backtest import venue_store

WSOL = "So11111111111111111111111111111111111111112"
CTX_COLS = ("slot UBIGINT, tx_index UBIGINT, failed BOOLEAN, outer_ix UBIGINT, inner_ix UBIGINT, event_seq UBIGINT, event_seq_all UBIGINT, "
            "fee_payer VARCHAR, outer_program VARCHAR, parent_inner_ix UBIGINT, parent_ix_disc VARCHAR, parent_ix_name VARCHAR, "
            "parent_ix_args VARCHAR, decode_status VARCHAR, source VARCHAR, payload_len UBIGINT, ix_trader VARCHAR, ix_pool VARCHAR, "
            "ix_base_mint VARCHAR, ix_quote_mint VARCHAR")


def ctx(slot, tx, failed=False, outer=0, parent_inner=None, seq_all=0, name="swap", source="cpi", base="BASE", quote=WSOL):
    return [slot, tx, failed, outer, None, 0, seq_all, "PAYER", "PROG", parent_inner, "d", name, "{}", "ok", source, 1, "TRADER", "POOL", base, quote]


def write(chunk, table, fields, rows):
    con = duckdb.connect()
    con.execute(f"CREATE TABLE t ({CTX_COLS}{', ' + fields if fields else ''})")
    n = len(CTX_COLS.split(", ")) + (len(fields.split(", ")) if fields else 0)
    if rows:
        con.executemany(f"INSERT INTO t VALUES ({', '.join('?' * n)})", rows)
    d = os.path.join(chunk, table)
    os.makedirs(d, exist_ok=True)
    con.execute(f"COPY t TO '{os.path.join(d, 'part-00000.parquet')}' (FORMAT parquet)")
    con.close()


def plain(chunk, table, schema, rows):
    con = duckdb.connect()
    con.execute(f"CREATE TABLE t ({schema})")
    con.executemany(f"INSERT INTO t VALUES ({', '.join('?' * len(schema.split(', ')))})", rows)
    d = os.path.join(chunk, table)
    os.makedirs(d, exist_ok=True)
    con.execute(f"COPY t TO '{os.path.join(d, 'part-00000.parquet')}' (FORMAT parquet)")
    con.close()


def make_chunk(root, lo=1000, hi=2000):
    chunk = os.path.join(root, f"{lo}-{hi}")
    os.makedirs(chunk)
    with open(os.path.join(chunk, "_manifest.json"), "w") as f:
        json.dump({"status": "complete", "slot_start": lo, "slot_end_exclusive": hi, "commit": "abc", "venues": {"idl_sha256": {"x": "y"}},
                   "counters": {"venue_events": 1}}, f)
    plain(chunk, "venue_census", "slot UBIGINT, tx_index UBIGINT, failed BOOLEAN, tx_fee UBIGINT, priority_fee UBIGINT, jito_tip UBIGINT, cu_consumed UBIGINT",
          [[s, t, False, 5000, 100, 7, 50000] for s, t in ((99, 0), (100, 0), (100, 1), (100, 2), (100, 3), (101, 0), (102, 0), (103, 0), (104, 0))])
    sr = lambda i, o: json.dumps({"actual_input_amount": i, "output_amount": o, "next_sqrt_price": "18446744073709551616", "trading_fee": 10,
                                  "protocol_fee": 2, "referral_fee": 0})
    sr2 = lambda i, o: json.dumps({"included_fee_input_amount": i, "excluded_fee_input_amount": i - 10, "amount_left": 0, "output_amount": o,
                                   "next_sqrt_price": "18446744073709551616", "trading_fee": 10, "protocol_fee": 2, "referral_fee": 0})
    swap_fields = "pool VARCHAR, config VARCHAR, trade_direction UBIGINT, has_referral BOOLEAN, params VARCHAR, swap_result VARCHAR, amount_in UBIGINT, current_timestamp UBIGINT"
    swap2_fields = ("pool VARCHAR, config VARCHAR, trade_direction UBIGINT, has_referral BOOLEAN, swap_parameters VARCHAR, swap_result VARCHAR, "
                    "quote_reserve_amount UBIGINT, migration_threshold UBIGINT, current_timestamp UBIGINT")
    write(chunk, "meteora_dbc/EvtSwap", swap_fields, [
        ctx(99, 0) + ["P1", "CFG", 1, False, "{}", sr(50, 900), 50, 1],         # a lone EvtSwap before any anchor: no reserve
        ctx(100, 0) + ["P1", "CFG", 1, False, "{}", sr(400, 7000), 400, 1],     # with its EvtSwap2: the EvtSwap2 row wins
        ctx(100, 1) + ["P1", "CFG", 1, False, "{}", sr(300, 5000), 300, 1],     # a lone EvtSwap after the anchor: chained
        ctx(101, 0, failed=True) + ["P1", "CFG", 1, False, "{}", sr(1, 1), 1, 1],  # failed: dropped
        ctx(100, 2) + ["P1", "CFG", 1, False, "{}", sr(988, 800), 1010, 1],     # 0.2.2: with its EvtSwap2 and EvtSwap3
    ])
    write(chunk, "meteora_dbc/EvtSwap2", swap2_fields, [
        ctx(100, 0, seq_all=1) + ["P1", "CFG", 1, False, json.dumps({"amount_0": 400, "amount_1": 1, "swap_mode": 0}), sr2(400, 7000), 5000, 85000, 1],
        ctx(101, 0, failed=True, seq_all=1) + ["P1", "CFG", 1, False, "{}", sr2(1, 1), 1, 1, 1],
        ctx(100, 2, seq_all=1) + ["P1", "CFG", 1, False, json.dumps({"amount_0": 1010, "amount_1": 1, "swap_mode": 0}), sr2(1010, 800), 6288, 85000, 1],
    ])
    # DBC 0.2.2: one swap emits EvtSwap, EvtSwap2 and EvtSwap3 (the row is EvtSwap3's, threshold and amount_left from the
    # EvtSwap2); fees off the quote input (fee_on_base_token false): the reserve gets excluded_in - fees; then a swap with
    # its fees on the base token: the reserve gets the whole excluded_in
    swap3_fields = ("pool VARCHAR, config VARCHAR, trade_direction UBIGINT, swap_mode UBIGINT, has_referral BOOLEAN, fee_on_base_token BOOLEAN, "
                    "included_transfer_fee_amount_in UBIGINT, excluded_transfer_fee_amount_in UBIGINT, included_transfer_fee_amount_out UBIGINT, "
                    "excluded_transfer_fee_amount_out UBIGINT, trading_fee UBIGINT, protocol_fee UBIGINT, referral_fee UBIGINT, next_sqrt_price VARCHAR, "
                    "quote_reserve UBIGINT, current_timestamp UBIGINT")
    write(chunk, "meteora_dbc/EvtSwap3", swap3_fields, [
        ctx(100, 2, seq_all=2) + ["P1", "CFG", 1, 0, False, False, 1010, 1000, 800, 800, 10, 2, 0, "18446744073709551616", 6288, 1],
        ctx(100, 3) + ["P1", "CFG", 1, 0, False, True, 505, 500, 400, 396, 4, 1, 0, "18446744073709551616", 6788, 1],
    ])
    write(chunk, "meteora_dbc/EvtInitializePool", "pool VARCHAR, config VARCHAR, creator VARCHAR, base_mint VARCHAR, pool_type UBIGINT, activation_point UBIGINT",
          [ctx(98, 0, name="initialize_virtual_pool_with_spl_token") + ["P1", "CFG", "CR", "BASE", 0, 0]])
    damm_res = json.dumps({"included_fee_input_amount": 1000, "excluded_fee_input_amount": 990, "amount_left": 0, "output_amount": 333,
                           "next_sqrt_price": "18446744073709551616", "claiming_fee": 5, "protocol_fee": 1, "compounding_fee": 0, "referral_fee": 0})
    write(chunk, "meteora_damm_v2/EvtSwap2", "pool VARCHAR, trade_direction UBIGINT, collect_fee_mode UBIGINT, has_referral BOOLEAN, params VARCHAR, "
          "swap_result VARCHAR, included_transfer_fee_amount_in UBIGINT, included_transfer_fee_amount_out UBIGINT, excluded_transfer_fee_amount_out UBIGINT, "
          "current_timestamp UBIGINT, reserve_a_amount UBIGINT, reserve_b_amount UBIGINT",
          [ctx(102, 0) + ["D1", 0, 1, False, "{}", damm_res, 1000, 333, 333, 1, 9000, 4000],
           ctx(102, 1, failed=True) + ["D1", 0, 1, False, "{}", damm_res, 1, 1, 1, 1, 1, 1]])
    write(chunk, "meteora_damm_v2/EvtLiquidityChange", "pool VARCHAR, position VARCHAR, owner VARCHAR, token_a_amount UBIGINT, token_b_amount UBIGINT, "
          "reserve_a_amount UBIGINT, reserve_b_amount UBIGINT, liquidity_delta VARCHAR, change_type UBIGINT",
          [ctx(102, 0, outer=1, name="add_liquidity") + ["D1", "POS", "OWN", 10, 20, 9010, 4020, "12345", 0]])
    write(chunk, "meteora_damm_v2/EvtInitializePool", "pool VARCHAR, token_a_mint VARCHAR, token_b_mint VARCHAR, creator VARCHAR, sqrt_min_price VARCHAR, "
          "sqrt_max_price VARCHAR, sqrt_price VARCHAR, liquidity VARCHAR, collect_fee_mode UBIGINT, activation_type UBIGINT, activation_point UBIGINT, "
          "pool_type UBIGINT, token_a_amount UBIGINT, token_b_amount UBIGINT, pool_fees VARCHAR",
          [ctx(100, 5, name="initialize_pool") + ["D1", "BASE", WSOL, "CR", "1", "2", "3", "4", 1, 0, 0, 0, 9000, 4000, "{}"]])
    ll = "pool_state VARCHAR, trade_direction VARCHAR, exact_in BOOLEAN, amount_in UBIGINT, amount_out UBIGINT, virtual_base UBIGINT, virtual_quote UBIGINT, " \
         "real_base_before UBIGINT, real_quote_before UBIGINT, real_base_after UBIGINT, real_quote_after UBIGINT, protocol_fee UBIGINT, platform_fee UBIGINT, " \
         "creator_fee UBIGINT, share_fee UBIGINT, total_base_sell UBIGINT, pool_status VARCHAR"
    # a Constant curve (virtual base 1e15, virtual quote 3e10): a buy of 1e9 quote after 2 lamports of fees, then a sell of half
    # the tokens (the program rounds the remaining reserve up, so k grows by far less than 1e-6); amount_out of a sell is net
    write(chunk, "raydium_launchlab/TradeEvent", ll, [
        ctx(103, 0, name="buy_exact_in") + ["L1", "Buy", True, 10**9 + 2, 32258064516129, 10**15, 3 * 10**10, 0, 0, 32258064516129, 10**9,
                                             1, 1, 0, 0, 10**14, "Fund"],
        ctx(103, 1, name="sell_exact_in") + ["L1", "Sell", True, 16129032258064, 508196719, 10**15, 3 * 10**10, 32258064516129, 10**9,
                                              16129032258065, 491803279, 1, 1, 0, 0, 10**14, "Fund"],
        ctx(103, 2, failed=True, name="buy_exact_in") + ["L1", "Buy", True, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, "Fund"],
    ])
    write(chunk, "raydium_launchlab/PoolCreateEvent", "pool_state VARCHAR, creator VARCHAR, config VARCHAR, base_mint_param VARCHAR, curve_param VARCHAR, "
          "vesting_param VARCHAR, amm_fee_on VARCHAR", [ctx(97, 0, name="initialize_v2") + ["L1", "CR", "CFG", '{"decimals": 6}',
          '{"Constant": {"data": {"supply": 1000000000000000, "total_base_sell": 100000000000000, "total_quote_fund_raising": 85000000000, "migrate_type": 1}}}',
          "{}", "QuoteToken"]])
    cp = "pool_id VARCHAR, input_vault_before UBIGINT, output_vault_before UBIGINT, input_amount UBIGINT, output_amount UBIGINT, input_transfer_fee UBIGINT, " \
         "output_transfer_fee UBIGINT, base_input BOOLEAN, input_mint VARCHAR, output_mint VARCHAR, trade_fee UBIGINT, creator_fee UBIGINT, creator_fee_on_input BOOLEAN"
    write(chunk, "raydium_cpmm/SwapEvent", cp, [
        ctx(104, 0, name="swap_base_input", source="log") + ["C1", 1000, 2000, 10, 19, 0, 0, True, WSOL, "BASE", 1, 0, False],
        ctx(104, 1, failed=True, name="swap_base_input", source="log") + ["C1", 1, 1, 1, 1, 0, 0, True, WSOL, "BASE", 1, 0, False],
        # the extractor's balance fallback: vault deltas, the raw vaults before, no fees
        ctx(104, 2, name="swap_base_input", source="balance") + ["C1", 1100, 1900, 20, 37, None, None, True, WSOL, "BASE", None, None, None],
    ])
    write(chunk, "raydium_cpmm/LpChangeEvent", "pool_id VARCHAR, lp_amount_before UBIGINT, token_0_vault_before UBIGINT, token_1_vault_before UBIGINT, "
          "token_0_amount UBIGINT, token_1_amount UBIGINT, token_0_transfer_fee UBIGINT, token_1_transfer_fee UBIGINT, change_type UBIGINT",
          [ctx(104, 0, outer=1, name="deposit", source="log") + ["C1", 50, 1010, 1981, 5, 9, 0, 0, 0]])
    plain(chunk, "venue_inits", "slot UBIGINT, tx_index UBIGINT, outer_ix UBIGINT, inner_ix UBIGINT, fee_payer VARCHAR, venue VARCHAR, ix_name VARCHAR, pool VARCHAR, "
          "creator VARCHAR, token_0_mint VARCHAR, token_1_mint VARCHAR, token_0_vault VARCHAR, token_1_vault VARCHAR, lp_mint VARCHAR, ix_args VARCHAR",
          [[96, 0, 0, None, "PAYER", "raydium_cpmm", "initialize", "C1", "CR", WSOL, "BASE", "V0", "V1", "LP", '{"init_amount_0": 5}']])
    plain(chunk, "venue_graduations", "slot UBIGINT, tx_index UBIGINT, outer_ix UBIGINT, inner_ix UBIGINT, fee_payer VARCHAR, venue VARCHAR, ix_name VARCHAR, "
          "from_pool VARCHAR, to_venue VARCHAR, to_pool VARCHAR, base_mint VARCHAR, quote_mint VARCHAR",
          [[100, 5, 0, None, "PAYER", "meteora_dbc", "migration_damm_v2", "P1", "meteora_damm_v2", "D1", "BASE", WSOL],
           [96, 0, 0, None, "PAYER", "raydium_launchlab", "migrate_to_cpswap", "L1", "raydium_cpmm", "C1", "BASE", WSOL],
           [96, 1, 0, None, "PAYER", "raydium_launchlab", "migrate_to_amm", "L2", "raydium_amm_v4", "A1", "BASE", WSOL]])
    return chunk


def test_the_venue_store_on_a_fake_chunk(tmp_path):
    root = tmp_path / "chunks"
    root.mkdir()
    make_chunk(str(root))
    out = str(tmp_path / "v.duckdb")
    meta = venue_store.build("dev", out, venue_store.chunks_for(str(root), 1000, 2000), memory="1GB", threads=1, log=lambda *_: None)
    con = duckdb.connect(out, read_only=True)
    dbc = con.execute("SELECT slot, tx_index, event, amount_in, quote_reserve_delta::BIGINT, quote_reserve::BIGINT, reserve_source, "
                      "migration_threshold, amount_left, is_buy, tx_fee FROM dbc ORDER BY slot, tx_index").fetchall()
    assert dbc == [
        (99, 0, "EvtSwap", 50, 50, None, None, None, None, True, 5000),            # before any anchor
        (100, 0, "EvtSwap2", 400, 390, 5000, "event", 85000, 0, True, 5000),      # EvtSwap2 over its EvtSwap; the reserve gets the after-fee input
        (100, 1, "EvtSwap", 300, 300, 5300, "chained", None, None, True, 5000),   # a lone EvtSwap stays, chained: 5000 + 300
        (100, 2, "EvtSwap3", 1010, 988, 6288, "event", 85000, 0, True, 5000),     # EvtSwap3 over both; threshold and amount_left from the EvtSwap2;
                                                                                    # delta = excluded_in 1000 - fees 12
        (100, 3, "EvtSwap3", 505, 500, 6788, "event", None, None, True, 5000),    # fees on the base token: delta = excluded_in
    ]
    assert con.execute("SELECT kind, a_to_b, amount_in, amount_out, reserve_a, reserve_b, token_a_amount FROM damm2 ORDER BY kind").fetchall() == [
        ("add", None, None, None, 9010, 4020, 10), ("swap", True, 1000, 333, 9000, 4000, None)]
    assert con.execute("SELECT is_buy, real_quote_before, real_quote_after, status, curve FROM launchlab ORDER BY tx_index").fetchall() == [
        (True, 0, 10**9, "Fund", "Constant"), (False, 10**9, 491803279, "Fund", "Constant")]
    assert con.execute("SELECT kind, source, input_amount, input_vault_before, trader_paid::BIGINT, trader_received::BIGINT, token_0_amount, "
                       "token_0_transfer_fee FROM cpmm ORDER BY kind, tx_index").fetchall() == [
        ("deposit", "log", None, None, None, None, 5, 0),
        ("swap", "log", 10, 1000, 10, 19, None, None),
        ("swap", "balance", 20, None, None, None, None, None)]   # a balance row's raw vaults are no reserves: NULL
    assert con.execute("SELECT amount_in, amount_out, pool_amount_in, pool_amount_out FROM damm2 WHERE kind = 'swap'").fetchall() == [(1000, 333, 1000, 333)]
    assert con.execute("SELECT json_extract_string(params, '$.curve_param.Constant.data.migrate_type') FROM venue_pools "
                       "WHERE venue = 'raydium_launchlab'").fetchone()[0] == "1"   # nested values stay JSON, not JSON strings
    pools = con.execute("SELECT venue, pool, base_mint, quote_mint, create_slot FROM venue_pools ORDER BY venue").fetchall()
    assert pools == [("meteora_damm_v2", "D1", "BASE", WSOL, 100), ("meteora_dbc", "P1", "BASE", WSOL, 98),
                     ("raydium_cpmm", "C1", "BASE", WSOL, 96), ("raydium_launchlab", "L1", "BASE", WSOL, 97)]
    assert con.execute("SELECT to_venue, linked FROM venue_graduations ORDER BY to_venue").fetchall() == [
        ("meteora_damm_v2", True), ("raydium_amm_v4", False), ("raydium_cpmm", True)]
    checks = {c["check"]: c for c in meta["checks"]}
    assert checks["dbc_chained_share"]["status"] == "red" and checks["dbc_chained_share"]["value"] == pytest.approx(1 / 5, abs=1e-5)
    assert checks["dbc_reserve_continuity"]["value"] == 1.0   # 6288 + 500 = 6788
    assert checks["cpmm_balance_share"]["status"] == "red" and checks["cpmm_balance_share"]["value"] == 0.5   # 1 of 2 swaps
    assert checks["graduations_linked_share"]["value"] == 1.0 and checks["dbc_reserve_continuity"]["status"] == "green"
    assert checks["launchlab_continuity"]["status"] == "green" and checks["launchlab_continuity"]["value"] == 1.0
    assert checks["launchlab_constant_product"]["status"] == "green" and checks["launchlab_constant_product"]["value"] == 1.0
    assert checks["launchlab_quote_legs"]["value"] == {"buys": 1, "buy_gross_in": 1, "sells": 1, "sell_net_out": 1, "sell_gross_out": 0}
    assert meta["counts"] == {"dbc": 5, "damm2": 2, "launchlab": 2, "cpmm": 3, "venue_pools": 4, "venue_graduations": 3}
    assert not os.path.exists(out + ".part")
    stored = json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
    assert stored["chunks"][0]["commit"] == "abc" and stored["period"] == "dev"
    con.close()
    with pytest.raises(SystemExit, match="built once"):
        venue_store.build("dev", out, venue_store.chunks_for(str(root), 1000, 2000), log=lambda *_: None)
    with pytest.raises(SystemExit, match="not covered"):
        venue_store.chunks_for(str(root), 1000, 3000)


def test_an_empty_table_is_refused_and_leaves_no_file(tmp_path):
    root = tmp_path / "chunks"
    root.mkdir()
    chunk = make_chunk(str(root))
    # an empty CPMM SwapEvent and no LpChangeEvent: the cpmm table has no row
    write(chunk, "raydium_cpmm/SwapEvent", "pool_id VARCHAR, input_amount UBIGINT", [])
    import shutil
    shutil.rmtree(os.path.join(chunk, "raydium_cpmm", "LpChangeEvent"))
    out = str(tmp_path / "v.duckdb")
    with pytest.raises(SystemExit, match="no rows in .'cpmm'."):
        venue_store.build("dev", out, venue_store.chunks_for(str(root), 1000, 2000), memory="1GB", threads=1, log=lambda *_: None)
    assert not os.path.exists(out) and not os.path.exists(out + ".part")


def test_a_gate_without_rows_is_red_and_info_only_on_a_short_window(tmp_path):
    import duckdb as d
    con = d.connect()
    con.execute("ATTACH ':memory:' AS v")
    for t, cols in (("dbc", "pool VARCHAR, slot UBIGINT, tx_index UBIGINT, outer_ix UBIGINT, parent_inner_ix UBIGINT, reserve_source VARCHAR, "
                            "quote_reserve_event UBIGINT, quote_reserve_delta HUGEINT"),
                    ("cpmm", "kind VARCHAR, source VARCHAR, decode_status VARCHAR"),
                    ("venue_graduations", "to_venue VARCHAR, linked BOOLEAN"),
                    ("launchlab", "pool VARCHAR, slot UBIGINT, tx_index UBIGINT, outer_ix UBIGINT, parent_inner_ix UBIGINT, real_base_before UBIGINT, "
                                  "real_quote_before UBIGINT, real_base_after UBIGINT, real_quote_after UBIGINT, virtual_base UBIGINT, virtual_quote UBIGINT, "
                                  "is_buy BOOLEAN, amount_in UBIGINT, amount_out UBIGINT, protocol_fee UBIGINT, platform_fee UBIGINT, creator_fee UBIGINT, "
                                  "share_fee UBIGINT, curve VARCHAR")):
        con.execute(f"CREATE TABLE v.{t} ({cols})")
    for strict in (True, False):
        st = {c["check"]: c["status"] for c in venue_store.run_checks(con, log=lambda *_: None, strict=strict)}
        assert st["dbc_chained_share"] == st["cpmm_balance_share"] == st["launchlab_continuity"] == "red"
        assert st["graduations_linked_share"] == st["launchlab_constant_product"] == ("red" if strict else "info")


def test_the_hold_out_and_the_clean_epochs_are_refused(tmp_path):
    out = str(tmp_path / "x.duckdb")
    with pytest.raises(SystemExit, match="hold-out"):
        venue_store.main(["dev", "--chunks", str(tmp_path), "--from-slot", "452000000", "--to-slot", "452400000", "--out", out])
    with pytest.raises(SystemExit, match="clean"):
        venue_store.main(["forward", "--chunks", str(tmp_path), "--from-slot", "455000000", "--to-slot", "455900000", "--out", out])
    assert not os.path.exists(out)
