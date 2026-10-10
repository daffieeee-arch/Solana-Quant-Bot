"""Offline address derivation reproduces on-chain addresses (pda.py)."""

import os

import duckdb
import pytest

from backtest import pda

CHUNK = "/home/chupa/Solana-project/data-old-faithful-one/lab/events/v1/chunks/451008000-451224000"


def test_known_addresses():
    assert pda.b58encode(pda.b58decode(pda.PUMP_AMM)) == pda.PUMP_AMM
    assert pda.b58encode(pda.b58decode(pda.DEFAULT_KEY)) == pda.DEFAULT_KEY
    # PumpSwap's event authority, seen as an account in every emit_cpi event.
    assert pda._pda([b"__event_authority"], pda.PUMP_AMM) == "GS4CU59F31iL7aR2Q8zVS8DRrcRnXX1yjQ66TqNVQnaR"


@pytest.fixture(scope="module")
def con():
    if not os.path.isdir(CHUNK):
        pytest.skip("dev chunk not present")
    c = duckdb.connect()
    c.execute("SET memory_limit = '1GB'; SET threads TO 2")
    return c


def test_pool_addresses_match_create_and_migration_events(con):
    rows = con.execute(f"""SELECT pool, "index", creator, base_mint, quote_mint
                           FROM '{CHUNK}/pump_amm/CreatePoolEvent/*.parquet' USING SAMPLE 300 ROWS""").fetchall()
    assert rows and all(pda.amm_pool(i, c, b, q) == p for p, i, c, b, q in rows)
    rows = con.execute(f"""SELECT pool, mint, quote_mint
                           FROM '{CHUNK}/pump/CompletePumpAmmMigrationEvent/*.parquet' USING SAMPLE 100 ROWS""").fetchall()
    assert rows and all(pda.canonical_pool(m, q) == p for p, m, q in rows)


def test_fee_account_tells_the_quote_mint(con):
    # Every SOL-quoted pool pays its protocol fee into the recipient's WSOL ATA; other quotes never do.
    rows = con.execute(f"""
        SELECT DISTINCT c.quote_mint = '{pda.WSOL}', b.protocol_fee_recipient, b.protocol_fee_recipient_token_account
        FROM '{CHUNK}/pump_amm/BuyEvent/*.parquet' b
        JOIN '{CHUNK}/pump_amm/CreatePoolEvent/*.parquet' c USING (pool)
        WHERE b.protocol_fee_recipient <> '{pda.DEFAULT_KEY}'""").fetchall()
    assert any(s for s, _, _ in rows) and any(not s for s, _, _ in rows)
    for is_sol, recipient, account in rows:
        assert (pda.ata(recipient, pda.WSOL) == account) == is_sol
