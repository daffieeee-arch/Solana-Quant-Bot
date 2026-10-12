"""paper.keeper on a synthetic recorder segment: the trades of tokens with a signal (recorded and
backfill), their arrivals, 1-minute candles of every pool and curve, DONE.json last; a second run
does nothing; without a journal the candles are still kept."""

import json
import os

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from paper import keeper
from paper.journal import Journal

BT = 1_790_000_040  # block time of slot 1000, at a minute start (BT % 60 == 0)


def write(d, table, rows):
    os.makedirs(os.path.join(d, table), exist_ok=True)
    pq.write_table(pa.table({k: [r[k] for r in rows] for k in rows[0]}), os.path.join(d, table, "part-0.parquet"))


def ev(slot, tx, **kw):
    return {"slot": slot, "tx_index": tx, "outer_ix": 0, "inner_ix": 1, **kw}


def segment(root):
    seg = os.path.join(root, "segments", "000000005")
    write(seg, "blocks", [{"slot": s, "block_time": BT + (s - 1000) * 20} for s in (1000, 1001, 1002, 1003)])
    write(seg, "pump_amm/BuyEvent", [
        ev(1000, 1, pool="SIGPOOL", base_amount_out=100, quote_amount_in=200),   # price 2
        ev(1001, 4, pool="SIGPOOL", base_amount_out=100, quote_amount_in=500),   # price 5
        ev(1002, 2, pool="OTHER", base_amount_out=10, quote_amount_in=30),       # price 3
        ev(1003, 0, pool="SIGPOOL", base_amount_out=100, quote_amount_in=400),   # price 4, the next minute
    ])
    write(seg, "pump_amm/SellEvent", [ev(1001, 9, pool="SIGPOOL", base_amount_in=100, quote_amount_out=100)])  # price 1
    write(seg, "pump/TradeEvent", [ev(1000, 3, mint="SIGMINT", token_amount=1000, sol_amount=7, quote_amount=None),
                                   ev(1002, 5, mint="OTHERMINT", token_amount=10, sol_amount=1, quote_amount=None)])
    write(seg, "arrivals", [{"slot": s, "tx_index": t, "signature": f"s{s}-{t}", "received_unix_ns": 1}
                            for s, t in ((1000, 1), (1000, 3), (1001, 4), (1001, 9), (1002, 2), (1003, 0))])
    write(os.path.join(seg, "backfill"), "pump_amm/BuyEvent", [ev(990, 0, pool="SIGPOOL", base_amount_out=1, quote_amount_in=1)])
    with open(os.path.join(seg, "_segment.json"), "w") as f:
        json.dump({"seq": 5, "first_slot": 1000, "last_slot": 1003, "complete": True}, f)
    return seg


def test_keeper_keeps_signal_trades_arrivals_and_candles(tmp_path):
    seg = segment(str(tmp_path / "rec"))
    j = Journal(str(tmp_path / "journal"))
    for key in ("SIGPOOL", "SIGMINT"):
        j.put("signals", family="F7", variant="tp4_T600", key=key, t=1000, status="entered")
    j.commit()
    out = str(tmp_path / "kept")
    assert keeper.main(["--journal", str(tmp_path / "journal"), "--out", out, seg]) == 0
    k = os.path.join(out, "000000005")
    done = json.load(open(os.path.join(k, "DONE.json")))
    assert done["signal_trades"] == {"pump_amm/BuyEvent": 3, "pump_amm/SellEvent": 1, "pump/TradeEvent": 1,
                                     "backfill/pump_amm/BuyEvent": 1}
    con = duckdb.connect()
    buys = con.execute(f"SELECT DISTINCT pool FROM '{k}/signal_trades/pump_amm__BuyEvent.parquet'").fetchall()
    assert buys == [("SIGPOOL",)]
    arr = con.execute(f"SELECT slot, tx_index FROM '{k}/arrivals.parquet' ORDER BY ALL").fetchall()
    assert arr == [(1000, 1), (1000, 3), (1001, 4), (1001, 9), (1003, 0)] and done["arrivals"] == 5  # not OTHER's
    c = {(r[0], r[1], r[2]): r[3:] for r in con.execute(
        f"SELECT venue, key, minute, trades, open, high, low, close FROM '{k}/candles.parquet'").fetchall()}
    m = BT // 60
    assert c[("pool", "SIGPOOL", m)] == (3, 2.0, 5.0, 1.0, 1.0)  # 1000: 2, 1001: 5 then the sell at 1
    assert c[("pool", "SIGPOOL", (BT + 60) // 60)] == (1, 4.0, 4.0, 4.0, 4.0)
    assert c[("pool", "OTHER", m)] == (1, 3.0, 3.0, 3.0, 3.0)  # candles of every pool, not only signals
    assert c[("curve", "SIGMINT", m)][0] == 1 and c[("curve", "OTHERMINT", m)][1] == 0.1
    mtime = os.path.getmtime(os.path.join(k, "DONE.json"))
    assert keeper.main(["--journal", str(tmp_path / "journal"), "--out", out, seg]) == 0  # kept already: nothing
    assert os.path.getmtime(os.path.join(k, "DONE.json")) == mtime


def test_without_a_journal_the_candles_are_kept(tmp_path):
    seg = segment(str(tmp_path / "rec"))
    out = str(tmp_path / "kept")
    assert keeper.main(["--journal", str(tmp_path / "no-journal"), "--out", out, seg]) == 0
    done = json.load(open(os.path.join(out, "000000005", "DONE.json")))
    assert done["signal_trades"] == {} and done["arrivals"] == 0 and done["candles"] == 5


def test_a_broken_segment_is_not_marked_kept(tmp_path):
    seg = segment(str(tmp_path / "rec"))
    with open(os.path.join(seg, "pump_amm", "BuyEvent", "part-0.parquet"), "wb") as f:
        f.write(b"not parquet")
    out = str(tmp_path / "kept")
    assert keeper.main(["--journal", str(tmp_path / "j"), "--out", out, seg]) == 1
    assert not os.path.exists(os.path.join(out, "000000005", "DONE.json"))
