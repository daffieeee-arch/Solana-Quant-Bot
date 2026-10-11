"""The forward mechanical validation (backtest.mechanics) on a small dev window: every check passes
and the replay grid runs without exceptions. Skips without the dev store or the epoch-1043 chunk."""

import os

import pytest

from backtest import data, mechanics

STORE = "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb"


LO, HI = 450700000, 450702000  # 2,000 slots of epoch 1043 (its chunk has instruction args)


def mini_chunk(src, out):
    """The raw trade tables of [LO, HI) copied from a chunk, as a chunk of their own."""
    con = data.connect(threads=1, memory="700MB")
    for t in ("pump/TradeEvent", "pump_amm/BuyEvent", "pump_amm/SellEvent"):
        os.makedirs(os.path.join(out, t))
        con.execute(f"""COPY (SELECT * FROM {data.parquet(t, [src])} WHERE slot >= {LO} AND slot < {HI})
                        TO '{os.path.join(out, t, "part-0.parquet")}' (FORMAT parquet)""")
    con.close()
    return (LO, HI, out)


def test_mechanics_on_a_dev_window(tmp_path):
    src = [c for c in data.complete_chunks() if c[0] <= LO and HI <= c[1]]
    if not src or not os.path.exists(STORE):
        pytest.skip("dev store or epoch-1043 chunk not available")
    chunk = mini_chunk(src[0], str(tmp_path / "chunk"))
    ok = mechanics.run(STORE, str(tmp_path / "out"), per_key=2, where=f"slot BETWEEN {LO} AND {HI - 1}",
                       forward_only=False, chunks=[chunk], log=lambda m: None)
    assert ok, (tmp_path / "out" / "mechanics.md").read_text()


def test_forward_only_refuses_the_dev_store(tmp_path):
    if not os.path.exists(STORE):
        pytest.skip("dev store not available")
    with pytest.raises(SystemExit, match="forward period"):
        mechanics.run(STORE, str(tmp_path))
