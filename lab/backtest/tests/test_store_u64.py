"""store.build with PumpSwap amounts outside BIGINT (the E20 clean store build stopped on 24 Oct with base reserves of
9,991,111,111,970,383,772 in clean epoch 1061). A 2,000-slot dev window copied as its own chunk, plus synthetic rows
copied from its own, one per cast the store makes: a buy and a CreatePoolEvent of a new pool (base reserves, b0), and for
existing SOL-market pools of the window a sell (quote reserves of a reversed pool), a buy (virtual quote reserves), a
boost (vault remaining), a withdraw (LP supply), a deposit (quote reserves) and a fee sweep (amount). The build runs and
leaves every such pool out of every store table (the coordinator's rule, 24 Oct: no NULL reaches the replay), counted
in meta.excluded_pools_int64 by whether a trade row showed base_supply = 0; every other row equals the plain build's;
the replay loads every kept pool; and a tournament runs on the fixture store to its positions. No clean data is read."""

import glob
import json
import os
import shutil

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backtest import data, store, strategies, streams, tournament

LO, HI = 450700000, 450702000  # epoch 1043 (as test_store_sweeps): pools created and classified inside
HUGE = 9_991_111_111_970_383_772  # the value of the E20 failure, > 2^63 - 1
NEW_POOL = "U64U64U64U64U64U64U64U64U64U64U64U64U64U64U6"
PA = "pump_amm"


def mini_chunk(src, out):
    con = data.connect(threads=1, memory="700MB")
    tables = sorted({os.path.relpath(os.path.dirname(f), src[2])
                     for f in glob.glob(os.path.join(src[2], "**", "*.parquet"), recursive=True)})
    for t in tables:
        s = data.parquet(t, [src])
        if con.execute(f"SELECT count(*) FROM {s} WHERE slot >= {LO} AND slot < {HI}").fetchone()[0]:
            os.makedirs(os.path.join(out, t), exist_ok=True)
            con.execute(f"COPY (SELECT * FROM {s} WHERE slot >= {LO} AND slot < {HI}) "
                        f"TO '{os.path.join(out, t, 'part-0.parquet')}' (FORMAT parquet)")
    con.close()
    shutil.copy(os.path.join(src[2], "_manifest.json"), out)
    return (LO, HI, out)


def pick_pools(plain_out):
    """Distinct SOL-market pools of the plain store with rows in its pool table, the first of each kind by address: a
    reversed pool with a sell, then a pool with a buy (vq), then any for boost, withdraw, deposit and sweep."""
    con = duckdb.connect(plain_out, read_only=True)
    rows = con.execute("""SELECT p.pool, p.quote_class, bool_or(e.kind = 'sell'), bool_or(e.kind = 'buy')
                          FROM pools p JOIN pool e USING (pool) WHERE p.quote_class IN ('sol', 'reversed')
                          GROUP BY ALL ORDER BY p.pool""").fetchall()
    con.close()
    rev = next(r[0] for r in rows if r[1] == "reversed" and r[2])
    vq = next(r[0] for r in rows if r[0] != rev and r[3])
    rest = [r[0] for r in rows if r[0] not in (rev, vq)][:4]
    assert len(rest) == 4
    return dict(zip(("sell", "vq", "boost", "withdraw", "deposit", "sweep"), [rev, vq] + rest))


def add_rows(out, pick):
    """part-1 files beside each table's part-0, copied from the window's own rows (so every column exists)."""
    con = duckdb.connect()
    f = lambda t, n="part-0": os.path.join(out, PA, t, f"{n}.parquet")  # noqa: E731

    def copy(t, select):
        con.execute(f"COPY ({select}) TO '{f(t, 'part-1')}' (FORMAT parquet)")

    # A new normal pool (base_supply > 0, as E20's: base reserves are the token's) and one more buy of an existing pool
    # with virtual quote reserves (an i128 string) above BIGINT, from that pool's own buy (its base_supply kept).
    copy("BuyEvent", f"""
        (SELECT * REPLACE ('{NEW_POOL}' AS pool, {HUGE}::UBIGINT AS pool_base_token_reserves, 7777 AS tx_index)
         FROM read_parquet('{f("BuyEvent")}') WHERE base_supply > 0 ORDER BY slot, tx_index LIMIT 1)
        UNION ALL
        (SELECT * REPLACE ('{2 ** 63}' AS virtual_quote_reserves, 7778 AS tx_index)
         FROM read_parquet('{f("BuyEvent")}') WHERE pool = '{pick["vq"]}' ORDER BY slot, tx_index LIMIT 1)""")
    copy("SellEvent", f"""SELECT * REPLACE ({HUGE}::UBIGINT AS pool_quote_token_reserves, 7779 AS tx_index)
                          FROM read_parquet('{f("SellEvent")}') WHERE pool = '{pick["sell"]}' ORDER BY slot, tx_index LIMIT 1""")
    copy("CreatePoolEvent", f"""SELECT * REPLACE ('{NEW_POOL}' AS pool, {HUGE}::UBIGINT AS pool_base_amount)
                                FROM read_parquet('{f("CreatePoolEvent")}') ORDER BY slot LIMIT 1""")
    for t, kind, col in (("BoostBuyAndBurnEvent", "boost", "boost_vault_remaining"),
                         ("WithdrawEvent", "withdraw", "lp_mint_supply"),
                         ("DepositEvent", "deposit", "pool_quote_token_reserves")):
        copy(t, f"""SELECT * REPLACE ('{pick[kind]}' AS pool, {HUGE}::UBIGINT AS {col}, 7780 AS tx_index)
                    FROM read_parquet('{f(t)}') ORDER BY slot, tx_index LIMIT 1""")
    con.close()
    os.makedirs(os.path.join(out, PA, "SweepPoolFeeEvent"))
    pq.write_table(pa.table({"slot": pa.array([HI - 10], pa.uint64()), "tx_index": pa.array([1], pa.uint64()),
                             "outer_ix": pa.array([0], pa.uint64()), "inner_ix": pa.array([1], pa.uint64()),
                             "pool": [pick["sweep"]], "amount": pa.array([HUGE], pa.uint64()),
                             "bucket": pa.array([0], pa.uint64())}), f("SweepPoolFeeEvent"))


def test_a_pool_with_an_amount_outside_bigint_leaves_the_store(tmp_path, monkeypatch):
    src = [c for c in data.complete_chunks() if c[0] <= LO and HI <= c[1]]
    if not src:
        pytest.skip("epoch-1043 chunk not available")
    chunk = mini_chunk(src[0], str(tmp_path / "chunk"))
    monkeypatch.setattr(store.health, "wait_ok", lambda *a, **k: 0)
    plain_out = str(tmp_path / "plain.duckdb")
    plain = store.build("dev", plain_out, store.load_defaults(), [chunk], log=lambda m: None)
    assert plain["excluded_pools_int64"] == {"pools": 0, "reversed": 0, "not_reversed": 0, "no_trade_rows": 0, "rows_out_of_range": {}}

    pick = pick_pools(plain_out)
    add_rows(str(tmp_path / "chunk"), pick)
    out = str(tmp_path / "u64.duckdb")
    lines = []
    meta = store.build("dev", out, store.load_defaults(), [chunk], log=lines.append)  # 24 Oct: a ConversionException
    ex = meta["excluded_pools_int64"]
    p = duckdb.connect(plain_out, read_only=True)
    gone = sorted({NEW_POOL, *pick.values()})
    # reversed as the store's pool_stats (bool_or(base_supply = 0) over trade rows); a pool without a trade row: none
    rev = dict(p.execute(f"SELECT pool, reversed FROM pool_stats WHERE pool IN ({', '.join('?' * len(gone))})", gone).fetchall())
    rev[NEW_POOL] = False  # its only trade row is a copy of a base_supply > 0 buy
    assert ex == {"pools": 7, "reversed": sum(rev.values()), "not_reversed": sum(not v for v in rev.values()),
                  "no_trade_rows": 7 - len(rev),
                  "rows_out_of_range": {"boost": 1, "buy": 2, "create": 1, "deposit": 1, "sell": 1, "sweep": 1, "withdraw": 1}}
    assert ex["reversed"] >= 1 and rev[pick["sell"]]
    assert any(m.startswith(f"excluded_pools_int64: 7 pool(s) (reversed {ex['reversed']}, ") for m in lines)
    con = duckdb.connect(out, read_only=True)
    q = f"IN ({', '.join('?' * len(gone))})"
    # The whole pool leaves every store table, not only the row: each existing pool had rows in the plain build.
    for pool in pick.values():
        assert p.execute("SELECT count(*) FROM pool WHERE pool = ?", [pool]).fetchone()[0] > 0, pool
    for t in ("pool", "pools", "pool_stats", "pool_sweeps"):
        assert con.execute(f"SELECT count(*) FROM {t} WHERE pool {q}", gone).fetchone()[0] == 0, t
    # Every other pool's rows are the plain build's (the pools' part slices may move: ntile over fewer pools). Not
    # compared: arg_max(.., slot) over a tie in the last slot (pool_stats' last_b, last_vault, coin_creator, and pools'
    # coin_creator from it) is not deterministic between two builds, in the old store.py as in this one.
    tie = {"pools": {"part", "coin_creator"}, "pool_stats": {"last_b", "last_vault", "coin_creator"}}
    for t in ("pool", "pools", "pool_stats", "curve", "blocks", "mints"):
        cols = ", ".join(c[0] for c in p.execute(f"DESCRIBE {t}").fetchall() if c[0] not in tie.get(t, ()))
        where, args = (f"WHERE pool NOT {q}", gone) if t.startswith("pool") else ("", [])
        assert p.execute(f"SELECT {cols} FROM {t} {where} ORDER BY ALL", args).fetchall() == \
            con.execute(f"SELECT {cols} FROM {t} {where} ORDER BY ALL", args).fetchall(), t
    assert json.loads(con.execute("SELECT json FROM meta").fetchone()[0])["excluded_pools_int64"] == ex
    kept = [r[0] for r in con.execute("SELECT pool FROM pools WHERE quote_class IN ('sol', 'reversed') ORDER BY 1").fetchall()]
    con.close()
    p.close()

    # The replay's loader reads every kept SOL-market pool of the fixture store.
    con = streams.open_store(out, threads=1, memory="500MB")
    st = streams.load_pool_streams(con, {pool: (LO, HI) for pool in kept})
    con.close()
    assert len(kept) > 10 and sorted(st) == kept and sum(len(s.pool_legs) for s in st.values()) > 0

    # A tournament on the fixture store: the replay workers are spawned and open LAB_STORE, so the environment points
    # there too; the burn-in and end guard (decision_bounds and the curve universe, in this process) shrink to fit the
    # 2,000-slot window. F7 and N2 as the clean test, N4 (boost cranks) so that the replay surely runs.
    runs, short = str(tmp_path / "runs"), strategies.ms(50)
    monkeypatch.setenv("LAB_STORE", out)
    monkeypatch.setenv("LAB_BACKTESTS", runs)
    monkeypatch.setattr(tournament, "STORE", out)
    monkeypatch.setattr(tournament, "OUT", runs)
    monkeypatch.setattr(strategies, "BURN_IN_MS", short)
    monkeypatch.setattr(strategies, "END_GUARD_MS", short)
    monkeypatch.setattr(strategies, "decision_bounds", lambda cfg, lo, hi, clock=None, **kw: (
        lo + (clock or strategies.Clock()).slots(short, lo), hi - (clock or strategies.Clock()).slots(short, hi - 1)))
    tournament.run(["F7", "N2", "N4"], [0.5], [3], ["empirical"], [0.5], 0.05, 1, 20, 0, "smoke", segments=["pool_reversed"], report=False)
    cfg = json.load(open(os.path.join(runs, "smoke", "config.json")))
    assert cfg["store"]["excluded_pools_int64"] == ex and cfg["signals"] > 0 and cfg["positions"] > 0
    res = duckdb.connect().execute(f"SELECT DISTINCT pool FROM read_parquet('{tournament.results_glob(os.path.join(runs, 'smoke'))}')").fetchall()
    assert res and {r[0] for r in res} <= set(kept) and not {r[0] for r in res} & set(gone)
