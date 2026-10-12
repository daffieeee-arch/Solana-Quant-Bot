"""creation_filter on a synthetic chunk whose answer is known: an account only migrations have, and
fee recipients every trade has but no creation."""

import json
import os
import sys

import duckdb

sys.path.insert(0, os.path.dirname(__file__))
import creation_filter as cf  # noqa: E402


def key(tag, i=0):
    """A distinct base58-looking 44-character address."""
    s = f"{tag}{i}".replace("0", "z").replace("l", "L").replace("I", "J").replace("O", "P")
    return (s + "x" * 44)[:44]


AMM, PUMP, WSOL = cf.PUMP_AMM, cf.PUMP, cf.WSOL
G, W, PG, F1, F2 = key("GlobalConfig"), key("WithdrawAuth"), key("PumpGlobal"), key("FeeRecipA"), key("FeeRecipB")


def write(con, root, table, rows, cols):
    d = os.path.join(root, table)
    os.makedirs(d, exist_ok=True)
    con.execute(f"CREATE OR REPLACE TEMP TABLE t ({', '.join(f'{c} {k}' for c, k in cols)})")
    con.executemany(f"INSERT INTO t VALUES ({', '.join('?' for _ in cols)})", rows)
    con.execute(f"COPY t TO '{os.path.join(d, 'part-00000.parquet')}' (FORMAT parquet)")


CTX = [("slot", "UBIGINT"), ("tx_index", "UBIGINT"), ("fee_payer", "VARCHAR"), ("outer_program", "VARCHAR"),
       ("parent_program", "VARCHAR"), ("signers", "VARCHAR"), ("ix_accounts", "VARCHAR")]


def chunk(root):
    con = duckdb.connect()
    creates, migs, buys, curve = [], [], [], []
    for i in range(20):
        slot, pool, payer = 1000 + i * 10, key("Pool", i), key("Creator", i)
        migration = i < 10
        base, quote = (key("Mint", i), WSOL) if migration else (WSOL, key("Mint", i))
        acc = json.dumps({"pool": pool, "global_config": G, "creator": payer, "program": AMM})
        creates.append((slot, 1, payer, PUMP if migration else AMM, AMM, None, acc, base, quote))
        if migration:
            migs.append((slot, 1, payer, PUMP, PUMP, None, json.dumps({"withdraw_authority": W, "global": PG, "program": PUMP})))
    for j in range(2000):
        slot, tx = 1000 + j // 3, 10 + j % 3
        acc = {"pool": key("Pool", j % 20), "global_config": G, "user": key("User", j % 300),
               "protocol_fee_recipient": F1 if j % 2 else F2, "program": AMM}
        buys.append((slot, tx, key("User", j % 300), AMM, AMM, None, json.dumps(acc)))
        if j % 40 == 0:  # a cross-venue transaction: a pump.fun trade in the same transaction
            curve.append((slot, tx, key("User", j % 300), PUMP, PUMP, None, json.dumps({"global": PG, "program": PUMP})))
    write(con, root, "pump_amm/CreatePoolEvent", creates, CTX + [("base_mint", "VARCHAR"), ("quote_mint", "VARCHAR")])
    write(con, root, "pump/CompletePumpAmmMigrationEvent", migs, CTX)
    write(con, root, "pump_amm/BuyEvent", buys, CTX)
    write(con, root, "pump/TradeEvent", curve, CTX)
    write(con, root, "blocks", [(s, 1_700_000_000 + (s - 1000) * 4 // 10) for s in range(1000, 2000)],
          [("slot", "UBIGINT"), ("block_time", "BIGINT")])
    with open(os.path.join(root, "_manifest.json"), "w") as f:
        json.dump({"status": "complete", "slot_start": 1000, "slot_end_exclusive": 2000,
                   "event_context": ["slot", "tx_index", "ix_accounts"]}, f)


def test_the_known_answers_are_found(tmp_path):
    root = tmp_path / "chunks"
    chunk(str(root / "1000-2000"))
    chunks = cf.chunks_with_accounts(str(root))
    assert len(chunks) == 1
    con = duckdb.connect()
    con.execute("SET memory_limit = '500MB'; SET threads TO 1")
    res = cf.analyse(con, chunks, 1000, 2000, 100.0)
    assert res["create_txs"] == 20
    assert res["create_groups"] == {"direct/reversed": 10, "migration/sol": 10}
    by = {r["accounts"][0]["address"]: r for r in res["required_single"]}
    # The withdraw authority: every migration, no direct creation, no other transaction.
    assert by[W]["catch"] == {"direct/reversed": 0.0, "migration/sol": 1.0}
    assert by[W]["other_share"] == 0 and by[W]["accounts"][0]["name"] == "withdraw_authority"
    # The global config is in everything.
    assert by[G]["catch_all"] == 1.0 and by[G]["other_share"] == 1.0
    # [PumpSwap, pump global] matches the migrations and the cross-venue trades only.
    assert by[PG]["other_per_day"] > 0 and by[PG]["other_share"] < 0.05
    # Excluding both fee recipients keeps every creation and drops every trade.
    last = res["exclude_greedy"][-1]
    assert {a["address"] for a in last["accounts"]} == {F1, F2}
    assert last["catch_all"] == 1.0 and last["other_share"] == 0
    assert "| " in cf.markdown(res)
