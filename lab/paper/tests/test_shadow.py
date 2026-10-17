"""Shadow live end to end on 2,000 dev slots: the simulated recorder's segments, the engine on them
(ShadowFeed) against its replay, engine --live with risk limits, and the reconciliation. Every
difference between live and replay has a cause other than 'unexplained'."""

import argparse
import json
import os
from collections import defaultdict, deque
from dataclasses import dataclass
from types import SimpleNamespace as NS

import duckdb
import pytest

from backtest import data, streams
from backtest.store import load_defaults
from backtest.strategies import Clock, decision_bounds
from paper import shadow
from paper.feed import HOLDOUT_END, HOLDOUT_START, ReplayFeed
from paper.live import FarmerFlags, LiveFeed, segment_dirs

STORE = "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb"
LO, HI = 450700000, 450702000


def test_coverage_intervals_and_gaps():
    cov = shadow.Coverage([("P", 10, 20, "tx_watchlist"), ("P", 20, 30, "tx_watchlist"), ("P", 40, 50, "tx_probes")],
                          [(25, 27)])
    assert cov.iv["P"] == [[10, 30], [40, 50]]
    assert cov.seen("P", 10) and cov.seen("P", 24) and not cov.seen("P", 25) and cov.seen("P", 27)
    assert not cov.seen("P", 30) and cov.seen("P", 45) and not cov.seen("Q", 15)
    assert cov.gap_overlaps(0, 25) and not cov.gap_overlaps(27, 40)


@dataclass(slots=True)
class _Leg:
    slot: int
    trader: str
    pre: tuple
    post: tuple


def _fake_run(mints, legs_per_mint):
    hist = defaultdict(list)
    for m in range(mints):
        for i in range(legs_per_mint):
            hist[f"mint{m:06d}"].append(_Leg(1000 + i, f"trader{m}-{i}", (m, i, 3 * i), (m, i + 1, 3 * i + 1)))
    feed = NS(curve_hist=hist, seen_keys={("c", m, i): "rec" for m in range(200) for i in range(5)}, seen_order=deque(),
              farmers=FarmerFlags(), tokens={}, pools={}, con=duckdb.connect(), seed_con=None)
    eng = NS(recent={}, last_state={}, busy={}, watch=None)
    return feed, eng


def test_creator_sells_live_cannot_know():
    """job 84 (shadow-1053-1054): pool Cj2rYAJ…, token created after the live start whose creator moved the
    creator fee to a sharing config (the pool's coin creator); the creator's 20.7 SOL sell was an F7 trigger
    live took and the replay's creator rule vetoed."""
    c = duckdb.connect()
    c.execute("CREATE TABLE mints AS SELECT * FROM (VALUES ('M1', 1000, 'C1'), ('M2', 100, 'C2'), ('M3', 1000, 'C3')) v(mint, create_slot, creator)")
    c.execute("""CREATE TABLE pools AS SELECT * FROM (VALUES ('P1', 'M1', 'S1'), ('P2', 'M2', 'S2'), ('P3', 'M3', 'C3'))
                 v(pool, mint, coin_creator)""")
    c.execute("""CREATE TABLE pool AS SELECT * FROM (VALUES
                   ('P1', 1200, 'C1', 'sell', false), ('P1', 1300, 'C1', 'buy', true), ('P1', 1400, 'X', 'sell', false),
                   ('P1', 1250, 'C1', 'withdraw', false),
                   ('P2', 1200, 'C2', 'sell', false),  -- created before the live start: live knows its creator
                   ('P3', 1200, 'C3', 'sell', false))  -- creator = coin creator: live knows it too
                 v(pool, slot, trader, kind, token_buy)""")
    assert shadow.creator_sells_unknown_live(c, {"P1", "P2", "P3"}, 500) == {"P1": [1200]}
    assert shadow.creator_sells_unknown_live(c, set(), 500) == {}


def test_state_estimates_are_close_to_the_exact_size(monkeypatch):
    """The sampled byte estimate of a structure is within 10 % of its exact size (nothing sampled)."""
    feed, eng = _fake_run(3000, 12)
    est = shadow._est_mb(feed.curve_hist)
    monkeypatch.setattr(shadow, "STATE_SAMPLE", 10**9)
    monkeypatch.setattr(shadow, "STATE_INNER", 10**9)
    exact = shadow._est_mb(feed.curve_hist)
    assert exact > 5 and abs(est - exact) <= 0.1 * exact


def test_state_report_names_every_structure_and_the_memory_parts():
    feed, eng = _fake_run(2000, 3)
    st = shadow._state_report(feed, eng, {"feed": feed.con, "seed": None})
    json.dumps(st)  # it goes to state.jsonl as is
    assert st["n"]["curve_hist_legs"] == 6000 and st["n"]["seen_keys"] == 1000
    assert st["est_mb"]["feed.curve_hist"] > 0 and "eng.recent" in st["est_mb"] and "watch.rows" not in st["est_mb"]
    assert {"py_blocks_k", "arrow_mb", "duckdb_feed_mb"} <= set(st["mem"]) and "duckdb_seed_mb" not in st["mem"]
    feed.con.close()
    assert "duckdb_feed_mb" not in shadow._mem_parts({"feed": feed.con})  # a closed database is left out


def test_replay_feed_never_touches_the_holdout():
    for lo, hi in ((HOLDOUT_START - 10, HOLDOUT_START + 1), (HOLDOUT_START, HOLDOUT_END), (HOLDOUT_END - 1, HOLDOUT_END + 10)):
        with pytest.raises(ValueError, match="hold-out"):
            ReplayFeed(STORE, lo, hi)


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    chunk = [c for c in data.complete_chunks() if c[0] <= LO and HI <= c[1]]
    if not chunk or not os.path.exists(STORE):
        pytest.skip("dev store or its chunk not available")
    out = str(tmp_path_factory.mktemp("shadow"))
    a = argparse.Namespace(store=STORE, out=out, lo=LO, hi=HI, chunks=os.path.dirname(chunk[0][2]), variants="0.04:600,0.06:600",
                           d=1, q=0.5, gaps=1, reconnects=2, seed=7, no_probes=False, watch_warmup=360,
                           risk=os.path.join(os.path.dirname(shadow.__file__), "risk.yaml"), reconcile_every=1, memory="700MB")
    window = shadow.WINDOW_SLOTS
    shadow.WINDOW_SLOTS = 300  # the work tables are built in 7 slot windows
    yield out, shadow.run(a, log=lambda m: None)
    shadow.WINDOW_SLOTS = window


def test_segments_follow_the_minutes_with_reconnects_and_a_gap(run):
    out, info = run
    segs = segment_dirs(os.path.join(out, "rec"))
    assert len(segs) == info["recorder"]["segments"] >= 8  # ~9 minutes
    firsts = [m["first_slot"] for _, _, m in segs]
    lasts = [m["last_slot"] for _, _, m in segs]
    assert firsts[0] == LO and lasts[-1] == HI - 1 and all(b + 1 == a for a, b in zip(firsts[1:], lasts))  # contiguous
    reasons = [r["reason"] for _, _, m in segs for r in m.get("reconnects", ())]
    assert sum("with from_slot" in r for r in reasons) == 2 and sum("without from_slot" in r for r in reasons) == 1
    (ga, gb), = info["gaps"]
    for _, seg, m in segs:  # no slot of the gap was written
        f = os.path.join(seg, "blocks", "part-00000.parquet")
        if os.path.exists(f):
            con = data.connect(threads=1, memory="200MB")
            assert con.execute(f"SELECT count(*) FROM '{f}' WHERE slot >= {ga} AND slot < {gb}").fetchone()[0] == 0
        with open(os.path.join(seg, "watchlist.json")) as fh:
            w = json.load(fh)
        assert w["new_pools"] and isinstance(w["pools"], list)
    assert info["recorder"].get("tx_watchlist", 0) > 0 and info["estimated_gb_per_day"] > 0


def test_progress_lines_write_the_state_of_every_structure(run):
    out, _ = run
    with open(os.path.join(out, "state.jsonl")) as f:
        lines = [json.loads(line) for line in f]
    assert lines and lines[0]["seq"] == 0 and lines[0]["rss_mb"] > 0
    assert {"feed.curve_hist", "feed.seen_keys", "eng.recent", "watch.rows"} <= set(lines[-1]["est_mb"])
    assert "py_blocks_k" in lines[-1]["mem"] and "duckdb_feed_mb" in lines[-1]["mem"]


def test_live_against_replay_has_no_unexplained_difference(run):
    out, info = run
    assert info["unexplained"] == 0, [r for r in info["summary"].items()]
    f7 = {k: g for k, g in info["summary"].items() if k.startswith("F7 pool")}
    assert sum(g["triggers_both"] for g in f7.values()) > 0, info["summary"]  # live traded the replay's triggers
    assert info["livefeed"]["late_rows"] == 0


def test_reconcile_clean_outside_the_gap(run):
    out, info = run
    rc = info["reconcile"]
    assert rc["segments_checked"] >= 8 and rc["others_clean"], rc
    # The criterion (runbook 0.3): ok_outside_gaps; ok counts the gap segments' missing rows too.
    with open(os.path.join(out, "reconcile.json")) as f:
        rep = json.load(f)
    assert rc["ok_outside_gaps"] is True and rep["ok_outside_gaps"] is True and rep["ok"] is False
    assert rc["gap_segments"] and all(v["extra"] == v["mismatched"] == 0 for v in rc["gap_segments"].values())
    assert sum(v["missing"] for v in rc["gap_segments"].values()) > 0


def test_engine_live_with_risk_limits_writes_daily_reports(run):
    out, info = run
    assert info["live_risk_reports"], info
    with open(os.path.join(out, "compare.md")) as f:
        text = f.read()
    assert "development data" in text and "Unexplained differences: 0" in text


def test_work_tables_are_in_slot_order_across_windows(run):
    out, _ = run
    assert shadow.WINDOW_SLOTS < HI - LO
    con = duckdb.connect(os.path.join(out, "work", "shadow-rows.duckdb"), read_only=True)
    tables = [r[0] for r in con.execute("""SELECT table_name FROM duckdb_tables()
                                           WHERE table_name IN ('txu', 'pc') OR regexp_matches(table_name, '^t[0-9]+$')""").fetchall()]
    assert "txu" in tables and len(tables) > 2
    for t in tables:
        back = con.execute(f"""SELECT count(*) FILTER (WHERE slot < prev)
                               FROM (SELECT slot, lag(slot) OVER (ORDER BY rowid) AS prev FROM {t})""").fetchone()[0]
        assert back == 0, t
    con.close()


def test_resume_reuses_replay_and_work_database_with_the_same_result(run):
    """A rerun with resume (e.g. after a kill during the live step) reuses the finished replay and
    work database, runs the rest again and gives the same comparison; STATUS.json says done."""
    out, info = run
    chunk = [c for c in data.complete_chunks() if c[0] <= LO and HI <= c[1]]
    logs = []
    a = argparse.Namespace(store=STORE, out=out, lo=LO, hi=HI, chunks=os.path.dirname(chunk[0][2]), variants="0.04:600,0.06:600",
                           d=1, q=0.5, gaps=1, reconnects=2, seed=7, no_probes=False, watch_warmup=360,
                           risk=os.path.join(os.path.dirname(shadow.__file__), "risk.yaml"), reconcile_every=1, memory="700MB",
                           resume=True)
    again = shadow.run(a, log=logs.append)
    assert any(m.startswith("replay reused") for m in logs)
    assert any(m.startswith("reusing the work database") for m in logs)
    assert again["summary"] == info["summary"] and again["unexplained"] == info["unexplained"]
    assert again["recorder"]["rows"] == info["recorder"]["rows"]
    with open(os.path.join(out, "STATUS.json")) as f:
        assert json.load(f)["step"] == "done"


def test_from_compare_reuses_replay_and_live_with_the_same_result(run, tmp_path):
    """After a kill in the comparison (the container's memory limit), --from-compare runs steps 3-5 on the
    finished replay and live step: the coverage rebuilt from the segments gives the same comparison."""
    out, info = run
    chunk = [c for c in data.complete_chunks() if c[0] <= LO and HI <= c[1]]
    kw = dict(store=STORE, lo=LO, hi=HI, chunks=os.path.dirname(chunk[0][2]), variants="0.04:600,0.06:600",
              d=1, q=0.5, gaps=1, reconnects=2, seed=7, no_probes=False, watch_warmup=360,
              risk=os.path.join(os.path.dirname(shadow.__file__), "risk.yaml"), reconcile_every=1, memory="700MB")
    logs = []
    again = shadow.run(argparse.Namespace(out=out, from_compare=True, **kw), log=logs.append)
    assert any(m.startswith("live reused") for m in logs) and any(m.startswith("compare: ") and "rss" in m for m in logs)
    assert again["summary"] == info["summary"] and again["unexplained"] == info["unexplained"]
    assert again["recorder"]["rows"] == info["recorder"]["rows"] and again["estimated_gb_per_day"] == info["estimated_gb_per_day"]
    assert again["reconcile"] == info["reconcile"]
    with open(os.path.join(out, "STATUS.json")) as f:
        assert json.load(f)["step"] == "done"
    with pytest.raises(SystemExit):  # no finished live step here
        shadow.run(argparse.Namespace(out=str(tmp_path), from_compare=True, **kw), log=lambda m: None)


def test_pump_creates_bring_every_create_and_its_creator(tmp_path):
    """--pump-creates: each segment holds every pump.fun create transaction of its slots, and LiveFeed knows
    the creator (the store's) of every token created after the live start. Without them it takes the pool's
    coin creator, a sharing config when the creator moved the creator fee (#233, job 84)."""
    chunk = [c for c in data.complete_chunks() if c[0] <= LO and HI <= c[1]]
    if not chunk or not os.path.exists(STORE):
        pytest.skip("dev store or its chunk not available")
    hi = LO + 600
    scon = streams.open_store(STORE, threads=2, memory="700MB")
    clock = Clock.from_store(scon)
    blo, bhi = scon.execute("SELECT min(slot), max(slot) + 1 FROM blocks").fetchone()
    root = str(tmp_path / "rec")
    rec = shadow.ShadowRecorder(scon, chunk, root, LO, hi, clock, decision_bounds(load_defaults(), blo, bhi, clock),
                                probes_on=False, gaps=0, reconnects=0, work=str(tmp_path / "work"), memory="700MB",
                                log=lambda m: None, pump_creates=True)
    for seq in range(len(rec.ranges)):
        rec.write(seq)
    created = dict(scon.execute(f"SELECT mint, creator FROM mints WHERE create_slot >= {LO} AND create_slot < {hi}").fetchall())
    scon.close()
    assert len(created) > 10
    segs = segment_dirs(root)
    got = duckdb.connect().execute(f"""SELECT DISTINCT mint, "user" FROM read_parquet(
        {[os.path.join(seg, "pump", "CreateEvent", "part-00000.parquet") for _, seg, _ in segs
          if os.path.exists(os.path.join(seg, "pump", "CreateEvent"))]!r})""").fetchall()
    assert dict(got) == created
    usage = sum(u["messages"] for _, _, m in segs for u in m["usage"] if u["stream"] == "tx_pump_creates")
    assert usage == rec.totals["tx_pump_creates"] >= len(created)
    feed = LiveFeed(root, store=STORE, follow=False, snapshot_before=LO, h24=10**9, memory="700MB")
    list(feed.events())
    assert {m: feed.token(m).creator for m in created} == created
    feed.close()


def test_a_farm_label_computed_before_the_pools_backfill_is_its_own_cause(tmp_path):
    """farm_label_order (job 91, pool 4reQ..., t 455516057): the labels differ although live received every trade of
    the pool's farm hour, because its backfill arrived in a later segment than its first recorded trade. Without any
    one of the three conditions it is not this cause (here another one, or unexplained)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from paper.journal import Journal

    store = str(tmp_path / "s.duckdb")
    con = duckdb.connect(store)
    con.execute("CREATE TABLE pool (pool VARCHAR, slot UBIGINT, tx_index BIGINT, kind VARCHAR)")
    con.executemany("INSERT INTO pool VALUES ('P', ?, 0, ?)", [(s, "buy" if s % 2 else "sell") for s in range(1_000, 1_200)])
    con.execute("INSERT INTO pool VALUES ('P', 1_000, 1, 'deposit')")  # not a trade: the label does not count it
    con.close()

    def seg(root, seq, first, recorded=(), backfill=(), backfills=()):
        d = os.path.join(root, "segments", f"{seq:09d}")
        for sub, slots in (("", recorded), ("backfill", backfill)):
            if slots:
                os.makedirs(os.path.join(d, sub, "pump_amm", "BuyEvent"), exist_ok=True)
                pq.write_table(pa.table({"pool": ["P"] * len(slots), "slot": list(slots), "tx_index": [0] * len(slots)}),
                               os.path.join(d, sub, "pump_amm", "BuyEvent", "part-0.parquet"))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "_segment.json"), "w") as f:
            json.dump({"seq": seq, "first_slot": first, "last_slot": first + 99, "complete": True,
                       "backfills": [{"pool": "P", "from_slot": 1_000, "to_slot": 1_099, "status": "complete", "rows": 100}
                                     for _ in backfills]}, f)

    def journals(farm_r, farm_l):
        out = []
        # The replay skips on its label; live, on an organic label, on the arm's open position (busy).
        for name, farm, reason in (("r", farm_r, "farm flagged"), ("l", farm_l, "busy" if farm_l == "organic" else "farm flagged")):
            j = Journal(str(tmp_path / name))
            j.put("signals", family="F7", variant="flow300_H6", key="P", t=1_300, venue="pool", orientation="reversed",
                  status="skipped", reason=reason, farm=farm, depth=1, trigger_trader="X")
            j.close()
            out.append(str(tmp_path / name))
        return out

    def cause(root, farm_l="organic", hour_slots=500):
        r, l = journals("flagged", farm_l)
        scon = streams.open_store(store, threads=1, memory="256MB")
        rows = shadow.compare(r, l, shadow.Coverage([], []), scon, root, d=1, hour_slots=hour_slots)
        scon.close()
        if rows[0]["cause"] == "farm_label_order":
            assert (rows[0]["replay_farm"], rows[0]["live_farm"], rows[0]["with_order"]) == ("flagged", farm_l, False)
        return rows[0]["cause"]

    late = str(tmp_path / "late")  # recorded from 1_100 (segment 1); the backfill [1_000, 1_100) in segment 2
    seg(late, 1, 1_100, recorded=range(1_100, 1_200))
    seg(late, 2, 1_200, backfill=range(1_000, 1_100), backfills=[1])
    assert cause(late) == "farm_label_order"
    # LAB review S1: the trades flagged before the backfill (1_100..1_199) must lie in the trigger's farm hour; an hour
    # of [1_250, 1_300) starts after the backfill's segment (1_200), so the late backfill cannot explain this label.
    assert cause(late, hour_slots=50) != "farm_label_order"
    assert cause(late, farm_l="flagged") is None  # the same label: no difference at all
    same = str(tmp_path / "same")  # the backfill came with the first recorded trades: the flags had their history
    seg(same, 1, 1_100, recorded=range(1_100, 1_200), backfill=range(1_000, 1_100), backfills=[1])
    assert cause(same) != "farm_label_order"
    hole = str(tmp_path / "hole")  # a trade of the farm hour was not received
    seg(hole, 1, 1_100, recorded=range(1_101, 1_200))
    seg(hole, 2, 1_200, backfill=range(1_000, 1_100), backfills=[1])
    assert cause(hole) != "farm_label_order"
