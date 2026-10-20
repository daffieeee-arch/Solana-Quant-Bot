"""check_creation_accounts.py: the file's sha256 and count against the expected ones, the hour's recipients it holds, and
the exact cut on a recording."""

import hashlib
import json
import os

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import check_creation_accounts as cc
from test_creation_accounts import addr


def files(tmp_path, held):
    f = tmp_path / "creation_accounts.txt"
    f.write_text("# header\n\n" + "\n".join(held) + "\n")
    fx = tmp_path / "recipients.json"
    fx.write_text(json.dumps({"counts": {"trade_transactions": 10}, "recipients": {addr(1): 5, addr(2): 3, addr(3): 2}}))
    return str(f), str(fx), hashlib.sha256(f.read_bytes()).hexdigest()


def test_the_file_and_the_recipients_it_holds(tmp_path):
    f, fx, sha = files(tmp_path, [addr(2), addr(9)])
    out = cc.check(f, sha, 2, fx)
    assert out["ok"] and (out["sha256"], out["count"]) == (sha, 2)
    r = out["recipients"]
    assert (r["total"], r["in_file"], r["in_file_transactions"], r["in_file_addresses"]) == (3, 1, 3, [addr(2)])
    assert r["top64_transactions_approx"] == {"without_file": 10, "with_file": 7}


@pytest.mark.parametrize("sha, count, why", [("0" * 64, None, "is not the expected"), (None, 3, "2 addresses, not the expected 3")])
def test_a_mismatch_exits_1(tmp_path, capsys, sha, count, why):
    f, fx, _ = files(tmp_path, [addr(2), addr(9)])
    args = ["--file", f, "--recipients", fx] + (["--expect-sha256", sha] if sha else []) + (["--expect-count", str(count)] if count else [])
    with pytest.raises(SystemExit) as e:
        cc.main(args)
    out = json.loads(capsys.readouterr().out)
    assert e.value.code == 1 and not out["ok"] and why in out["problems"][0]


def test_a_bad_file_is_refused(tmp_path, capsys):
    f, fx, _ = files(tmp_path, [addr(2), "abc"])
    with pytest.raises(SystemExit) as e:
        cc.main(["--file", f, "--recipients", fx])
    assert e.value.code == 1 and "line 4: not an address" in capsys.readouterr().err


def test_the_exact_cut_on_a_recording(tmp_path):
    root = tmp_path / "hour"
    seg = root / "segments" / "000000000"
    for t in ("arrivals", "pump_amm/BuyEvent"):
        os.makedirs(seg / t)
    # s1 and s2 trade on tx_new_reversed, s3 is noise there, s4 trades on tx_pools only (not the stream's)
    pq.write_table(pa.table({"signature": ["s1", "s2", "s3", "s4"], "stream": ["tx_new_reversed"] * 3 + ["tx_pools"]}),
                   seg / "arrivals" / "part-0.parquet")
    ix = [{"protocol_fee_recipient": addr(1)}, {"protocol_fee_recipient": addr(2), "buyback_fee_recipient": addr(3)},
          {"protocol_fee_recipient": addr(1)}]
    pq.write_table(pa.table({"signature": ["s1", "s2", "s4"], "ix_accounts": [json.dumps(d) for d in ix]}),
                   seg / "pump_amm" / "BuyEvent" / "part-0.parquet")
    f, fx, _ = files(tmp_path, [addr(2), addr(3)])
    h = cc.check(f, recipients=fx, hour=str(root))["hour"]
    assert (h["messages"], h["trade_transactions"]) == (3, 2)
    assert h["cut"]["without_file"] == {"learned": 3, "trade_transactions_excluded": 2, "share_of_messages": 0.6667}
    assert h["cut"]["with_file"] == {"learned": 1, "trade_transactions_excluded": 1, "share_of_messages": 0.3333}
