"""Paper engine = backtest replay on a short dev slice (the full chunk is paper/parity.py)."""

import os

import pytest

from backtest import streams
from backtest.store import load_defaults
from backtest.strategies import Clock
from paper import parity

STORE = os.environ.get("LAB_STORE", "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb")


def test_engine_matches_backtest(tmp_path):
    if not os.path.exists(STORE):
        pytest.skip("dev store not built")
    a, b = 450_150_000, 450_155_000
    con = streams.open_store(STORE, threads=2, memory="1GB")
    clock = Clock.from_store(con)
    bt = parity.backtest_side(con, load_defaults(), clock, a, b)
    en, _ = parity.engine_side(STORE, a, b, str(tmp_path / "journal"), clock)
    keys, diffs, counts = parity.compare(bt, en, a, b)
    assert counts.get("filled", 0) >= 20, counts
    assert diffs == []
