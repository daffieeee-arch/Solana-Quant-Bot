"""Signals may only depend on events with slot <= t.

The SQL implementation of the F7 filters (fast, used by the tournament) must equal the Python
reference evaluated on a PastView, which cannot see anything after t. We also check that
appending future events to a stream never changes a PastView decision."""

import os

import pytest

from backtest import streams
from backtest.store import load_defaults
from backtest.strategies import MAYHEM_AGENT, SOL, PastView, f1_signal, f7_signal, f7_signals_sql

STORE = os.environ.get("LAB_STORE", "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev.duckdb")


@pytest.fixture(scope="module")
def ctx():
    if not os.path.exists(STORE):
        pytest.skip("dev store not built")
    con = streams.open_store(STORE, threads=2, memory="3GB")
    cfg = load_defaults()
    lo, hi = con.execute("SELECT min(slot), max(slot) + 1 FROM blocks").fetchone()
    return con, cfg, lo, hi


def raw_triggers(con, cfg, lo, hi, mints):
    """Event-local trigger conditions only (no window filters)."""
    from backtest.strategies import decision_bounds, universe_sql

    dlo, dhi = decision_bounds(cfg, lo, hi)
    u = universe_sql(cfg, lo, hi)
    con.execute("CREATE OR REPLACE TEMP TABLE t_mints (mint VARCHAR)")
    con.executemany("INSERT INTO t_mints VALUES (?)", [(m,) for m in mints])
    return con.execute(f"""
        SELECT c.mint, c.slot FROM curve c JOIN mints m USING (mint)
        WHERE {u} AND m.mint IN (SELECT mint FROM t_mints) AND NOT c.is_buy AND c.trader <> m.creator
          AND c.trader <> '{MAYHEM_AGENT}' AND c.slot BETWEEN {dlo} AND {dhi}
          AND c.q >= GREATEST({3 * SOL}, 0.04 * (c.vq + c.q)) AND c.rq + c.q BETWEEN {20 * SOL} AND {75 * SOL}
        GROUP BY ALL""").fetchall()


def test_f7_sql_equals_pastview_reference(ctx):
    con, cfg, lo, hi = ctx
    mints = [r[0] for r in con.execute(f"""
        SELECT DISTINCT c.mint FROM curve c WHERE NOT c.is_buy AND c.q >= {3 * SOL}
        ORDER BY hash(c.mint) LIMIT 400""").fetchall()]
    raw = raw_triggers(con, cfg, lo, hi, mints)
    assert len(raw) > 50
    ss = streams.load_streams(con, sorted({m for m, _ in raw}))
    creators = dict(con.execute("SELECT mint, creator FROM mints").fetchall())
    py = {(m, t) for m, t in raw if f7_signal(PastView(ss[m], t), "curve", 0, creators[m])}
    sql = {(m, t) for venue, m, t, _ in f7_signals_sql(con, cfg, lo, hi, only_mints=mints) if venue == "curve"}
    assert py == sql, (len(py), len(sql), sorted(py ^ sql)[:5])


def test_pastview_ignores_the_future(ctx):
    con, cfg, lo, hi = ctx
    mints = [r[0] for r in con.execute("""
        SELECT mint FROM mints WHERE complete_slot IS NOT NULL ORDER BY hash(mint) LIMIT 40""").fetchall()]
    ss = streams.load_streams(con, mints)
    creators = dict(con.execute("SELECT mint, creator FROM mints").fetchall())
    checked = 0
    for m, s in ss.items():
        if len(s.curve) < 20:
            continue
        for leg in s.curve[10::25]:
            t = leg.slot
            full = PastView(s, t)
            cut = type(s)(s.mint, s.create_slot, s.creator, [l for l in s.curve if l.slot <= t],
                          [l for l in s.pool_legs if l.slot <= t], s.pool_open_slot, s.complete_slot)
            for fn in (lambda v: f7_signal(v, "curve", 0, creators[m]), lambda v: f1_signal(v, creators[m], SOL)):
                assert fn(full) == fn(PastView(cut, t))
            checked += 1
    assert checked > 50
