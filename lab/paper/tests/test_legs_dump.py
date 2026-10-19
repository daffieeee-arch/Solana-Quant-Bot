"""paper/legs_dump.py, the reference dump of the engine's event stream for the Rust legs reader (lab-live legs, M1
C1a): the line of a curve and of a pool event (fields, rendering, the PositionSim's tau draw), the summary's windows
and digests, and --out-from. No store needed: the events are built by backtest.streams from store rows."""

import hashlib
import json

import pytest

from backtest import streams
from paper import legs_dump
from paper.feed import Event

PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_AMM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"


def _curve(slot, tx, inner=1):
    # CURVE_COLS: a buy of 1,000 tokens for 50, fees 2 + 1, budget 500, min_tokens 7
    row = (slot, tx, 0, inner, "buy", True, 1_000, 50, 2, 1, 95, 30, 900_000, 30_000, 700_000, 20_000, "T", 500, None, 7, None, PUMP)
    return Event(slot, tx, 0, inner, 1_000_040, "curve", "M1", streams.curve_leg(row))


def _pool(slot, tx, kind, ctx, vq=7, inner=2):
    # POOL_COLS: kind slot tx outer inner disc b vault vq e lp protocol creator cashback base e_delta quote_user
    # limit_quote limit_base boost_left lp_amount lp_supply trader outer_program farmer
    w = kind in ("withdraw", "deposit")
    row = (kind, slot, tx, 0, inner, None, 1_000_000, 500_000, None if w else vq, None if w else 500_000 + vq, 20, 5, 0, 0, 100,
           None if w else 1_001, 1_000, None if w else 2_000, None, None, 77 if w else None, None, "W", PUMP_AMM, kind == "sell")
    return Event(slot, tx, 0, inner, None, "pool", "P1", streams.pool_leg(row, ctx))


def test_a_curve_line_has_the_stores_fields_and_the_tau_draw():
    f = legs_dump.line(_curve(1100, 0)).split("\t")
    assert len(f) == 8 + 21
    assert f[:7] == ["curve", "M1", "1100", "0", "0", "1", "1000040"]
    assert f[7] == str(hash((1100, 0, 0, 1)) % 10_000)
    assert f[8:13] == ["buy", "true", "1000", "50", "3"]  # the fee is fee + creator fee
    assert f[13:17] == ["901000", "29950", "701000", "19950"]  # pre: the buy undone
    assert f[17:23] == ["900000", "30000", "700000", "20000", "95", "30"]
    assert f[23:] == ["T", "500", "null", "7", "null", "true"]


def test_tau_is_cpythons_tuple_hash():
    # the vectors lab/core's Order::py_hash pins (CPython 3.13; int tuples hash the same in every run)
    assert hash((450144000, 12, 3, 4)) == 2365464660030220358


def test_a_pool_line_and_the_pools_context():
    ctx = {}
    buy = legs_dump.line(_pool(1200, 0, "buy", ctx)).split("\t")
    assert len(buy) == 8 + 26  # kind exact_out base quote_net, pre and post (7 each), 8 more
    assert buy[6] == "null" and buy[8:12] == ["buy", "false", "100", "1000"]  # quote_net: the user quote
    assert buy[19:21] == ["999900", "501008"] and buy[27] == "2000"  # post b - base, e + e_delta; limit_quote
    assert buy[12:19] == ["1000000", "500007", "20", "5", "0", "0", "7"]
    assert buy[-1] == "false"
    # a withdraw logs no virtual quote or fees: the pool's last ones (the buy's)
    w = legs_dump.line(_pool(1600, 0, "withdraw", ctx)).split("\t")
    assert w[12:19] == ["1000000", "500007", "20", "5", "0", "0", "7"]
    assert w[19:21] == ["999900", "499007"]  # post: base and quote out
    assert w[-3:] == ["77", "null", "false"]
    sell = legs_dump.line(_pool(1700, 0, "sell", ctx)).split("\t")
    assert sell[-1] == "true"


def test_a_leg_that_disagrees_with_its_event_is_refused():
    ev = _curve(1100, 0)
    with pytest.raises(AssertionError):
        legs_dump.line(Event(1101, 0, 0, 1, None, "curve", "M1", ev.leg))


class _Feed:
    """ReplayFeed's interface as dump uses it: events() and con."""

    events_list = []

    def __init__(self, store, lo, hi):
        self.lo, self.hi = lo, hi
        self.con = type("Con", (), {"close": lambda self: None})()

    def events(self):
        return iter(self.events_list)


def test_the_summary_windows_digests_and_out_from(monkeypatch, tmp_path):
    ctx = {}
    _Feed.events_list = [_curve(1100, 0), _pool(1100, 0, "buy", ctx), _pool(1500, 1, "sell", ctx), _curve(2100, 0)]
    monkeypatch.setattr(legs_dump, "ReplayFeed", _Feed)
    out = tmp_path / "lines.txt"
    res = legs_dump.dump("store", 1000, 3000, window=1000, out=str(out))
    text = out.read_text()
    assert res["events"] == 4 and res["sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert res["kinds"] == {"curve": 2, "pool/buy": 1, "pool/sell": 1}
    lines = text.splitlines()
    assert [w[:3] for w in res["windows"]] == [[1000, 2000, 3], [2000, 3000, 1]]
    assert res["windows"][0][3] == hashlib.sha256(("\n".join(lines[:3]) + "\n").encode()).hexdigest()
    later = tmp_path / "later.txt"
    again = legs_dump.dump("store", 1000, 3000, window=1000, out=str(later), out_from=1500)
    assert again["sha256"] == res["sha256"] and later.read_text().splitlines() == lines[2:]
    summary = tmp_path / "s.json"
    legs_dump.main(["--store", "store", "--from", "1000", "--to", "3000", "--summary", str(summary), "--window", "1000"])
    assert json.loads(summary.read_text())["windows"] == res["windows"]
