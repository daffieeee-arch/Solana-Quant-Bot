"""tx_ctx_smoke in slot buckets gives the one-pass result: the 1054 smoke chunk (1,000 slots) in one
bucket and in 28 uneven buckets of 37 slots, at a memory limit of 300 MB."""

import os

import pytest

from checks import tx_ctx_smoke

SMOKE = "/home/chupa/Solana-project/data-old-faithful-one/lab/nas-results/smoke-1054-txctx/chunk"


@pytest.mark.skipif(not os.path.exists(os.path.join(SMOKE, "tx_ctx")), reason="1054 smoke chunk not available")
def test_buckets_give_the_one_pass_result(monkeypatch, tmp_path):
    monkeypatch.setenv("LAB_DUCKDB_MEM", "300MB")
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path))
    one = tx_ctx_smoke.check(SMOKE, bucket_slots=10_000)
    many = tx_ctx_smoke.check(SMOKE, bucket_slots=37)
    assert (one.pop("buckets"), many.pop("buckets")) == (1, 28)
    one.pop("bucket_slots"), many.pop("bucket_slots")
    assert one == many and one["ok"]
    assert one["rows"]["tx_ctx"] == 183_358 and one["direct_pump_buys"]["by_destination_sampled"]["other_account"]["without_delta"] == 42
    assert os.listdir(tmp_path) == []  # its spill directory is removed
