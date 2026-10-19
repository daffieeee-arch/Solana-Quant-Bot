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


def test_a_pool_lines_farmer_flag_is_masked_and_a_curve_line_kept():
    ctx = {}
    ev = _pool(1700, 0, "sell", ctx)
    s = legs_dump.line(ev)
    assert legs_dump.no_farmer(ev, s) == s[: -len("true")] + "-"
    c = _curve(1100, 0)
    assert legs_dump.no_farmer(c, legs_dump.line(c)) == legs_dump.line(c)


class _LiveFeed:
    """LiveFeed's interface as dump_segments uses it. The backfill of pool P2 arrives while the feed reads on to the
    1500 event (the first event of a later slot), so the engine drains it at the end of slot 1100."""

    made = []

    def __init__(self, root, store=None, follow=True, snapshot_before=None, h24=None, memory="1GB", seed_farmers=False):
        assert not follow and seed_farmers
        self.args = (root, store, snapshot_before, h24, memory)
        self.history, self.fixes, self.learned, self.usage, self.new_pools = [], [], [], [], []
        self.stats = {"segments": 2, "late_rows": 0}
        self.farm_seed = None
        self.closed = False
        _LiveFeed.made.append(self)

    def events(self):
        ctx, hctx = {}, {}
        self.farm_seed = {"store": "/somewhere/s.duckdb", "lo": 1, "hi": 1100, "h24": 1099, "first_live_slot": 1100,
                          "complete_from": 1100}
        self.usage.append({"minute": 7, "stream": "tx", "bytes": 10, "messages": 1, "seq": 0, "note": None})
        ev = _curve(1100, 0)
        yield Event(*[getattr(ev, k) for k in ("slot", "tx_index", "outer_ix", "inner_ix", "block_time", "venue", "key",
                                               "leg")], arrival=(5, 4, 3))
        yield _pool(1100, 1, "buy", ctx)
        self.history.append(("P2", [_pool(900, 0, "buy", hctx).leg]))
        self.fixes.append(("P1", (1100, 1, 0, 2), True))
        self.learned.append(("pool", "P3", 1, 1500, "[]"))
        self.new_pools.append("P3")
        yield _pool(1500, 1, "sell", ctx)
        self.history.append(("P2", [_pool(950, 0, "sell", hctx).leg]))

    def drain_history(self):
        out, self.history = self.history, []
        return out

    def drain_flag_fixes(self):
        out, self.fixes = self.fixes, []
        return out

    def drain_learned(self):
        out, self.learned = self.learned, []
        return out

    def drain_usage(self):
        out, self.usage = self.usage, []
        return out

    def close(self):
        self.closed = True


def test_the_segments_summary_drains_where_the_engine_does(monkeypatch, tmp_path):
    import paper.live

    monkeypatch.setattr(paper.live, "LiveFeed", _LiveFeed)
    _LiveFeed.made.clear()
    res = legs_dump.dump_segments("rec", "store", 1000, None, window=1000, h24=1099)
    feed = _LiveFeed.made[0]
    assert feed.closed and feed.args == ("rec", "store", None, 1099, "1GB")
    assert res["events"] == 3 and res["to"] is None and res["kinds"] == {"curve": 1, "pool/buy": 1, "pool/sell": 1}
    assert [w[:3] for w in res["windows"]] == [[1000, 2000, 3]] and len(res["windows"][0]) == 5
    hctx = {}
    first, last = _pool(900, 0, "buy", hctx).leg, _pool(950, 0, "sell", hctx).leg

    def digest(lines):
        return {"count": len(lines), "sha256": hashlib.sha256("".join(l + "\n" for l in lines).encode()).hexdigest()}

    # The backfill read on the way to slot 1500 is drained at the end of slot 1100, the last one at the end of 1500.
    assert res["history"] == digest([legs_dump.history_line(1100, "P2", first), legs_dump.history_line(1500, "P2", last)])
    assert legs_dump.history_line(1100, "P2", first).split("\t")[:6] == ["1100", "P2", "900", "0", "0", "2"]
    assert res["flag_fixes"] == digest(["1100\tP1\t1100\t1\t0\t2\ttrue"])
    assert res["learned"] == digest(["pool\tP3\t1\t1500\t[]"]) and res["new_pools"] == digest(["P3"])
    assert res["usage"] == digest(['{"bytes": 10, "messages": 1, "minute": 7, "note": null, "seq": 0, "stream": "tx"}'])
    assert res["arrivals"] == digest(["1100\t0\t0\t1\t5\t4\t3"])
    assert res["farm_seed"] == {"lo": 1, "hi": 1100, "h24": 1099, "first_live_slot": 1100, "complete_from": 1100}
    assert res["stats"] == {"late_rows": 0, "segments": 2}
    # The lines are the store form's; the farmer-free digest masks the pool lines' flag.
    ctx = {}
    evs = [_curve(1100, 0), _pool(1100, 1, "buy", ctx), _pool(1500, 1, "sell", ctx)]
    lines = [legs_dump.line(e) for e in evs]
    assert res["sha256"] == digest(lines)["sha256"] == res["windows"][0][3]
    masked = [legs_dump.no_farmer(e, s) for e, s in zip(evs, lines)]
    assert res["sha256_no_farmer"] == digest(masked)["sha256"] == res["windows"][0][4] != res["sha256"]
    summary = tmp_path / "s.json"
    legs_dump.main(["--segments", "rec", "--store", "store", "--from", "1000", "--summary", str(summary), "--window", "1000",
                    "--h24", "1099"])
    assert json.loads(summary.read_text()) == json.loads(json.dumps(res))


def test_the_segments_options_are_refused_without_segments(tmp_path):
    s = str(tmp_path / "s.json")
    for extra in (["--to", "3000", "--h24", "5"], ["--to", "3000", "--snapshot-before", "1000"], []):
        with pytest.raises(SystemExit):
            legs_dump.main(["--store", "store", "--from", "1000", "--summary", s] + extra)


def test_a_run_over_its_rss_cap_stops(monkeypatch, tmp_path):
    evs = [_curve(1100, 0), _curve(1101, 0)]
    assert list(legs_dump.capped(iter(evs), 1 << 30)) == evs
    with pytest.raises(SystemExit, match="over the cap of 1 MB after 0 events"):
        list(legs_dump.capped(iter(evs), 1))
    _Feed.events_list = evs
    monkeypatch.setattr(legs_dump, "ReplayFeed", _Feed)
    with pytest.raises(SystemExit, match="peak RSS"):
        legs_dump.main(["--store", "store", "--from", "1000", "--to", "3000", "--summary", str(tmp_path / "s.json"),
                        "--max-rss-gb", "0.001"])
