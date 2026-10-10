"""N2 matches follow the spec: another SOL-quoted, non-mayhem token, same hour, similar depth,
at one of its own events; k distinct tokens; deterministic per trigger."""

import os

import pytest

from backtest import streams
from backtest.strategies import HOUR_SLOTS, SOL, SOL_QUOTES, n2_matches

STORE = os.environ.get("LAB_STORE", "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb")


@pytest.fixture(scope="module")
def con():
    if not os.path.exists(STORE):
        pytest.skip("dev store not built")
    return streams.open_store(STORE, threads=2, memory="1GB")


def test_matches_satisfy_the_spec(con):
    lo, hi = con.execute("SELECT min(slot), max(slot) FROM blocks").fetchone()
    mid = (lo + hi) // 2
    curve = con.execute(f"SELECT mint, slot, rq + q FROM curve WHERE slot BETWEEN {mid} AND {mid + 3 * HOUR_SLOTS} "
                        "AND rq BETWEEN 20e9 AND 70e9 USING SAMPLE 12 ROWS").fetchall()
    pool = con.execute(f"""SELECT e.pool, e.slot, e.sol_depth, p.orientation FROM pool e JOIN pools p USING (pool)
                           WHERE e.slot BETWEEN {mid} AND {mid + 3 * HOUR_SLOTS} AND e.kind IN ('buy', 'sell')
                             AND NOT e.token_buy AND e.sol_depth >= 150e9 AND p.quote_class IN ('sol', 'reversed')
                           USING SAMPLE 16 ROWS""").fetchall()
    triggers = ([("F7", m, s, "curve", int(d), None) for m, s, d in curve]
                + [("F7", p, s, "pool", int(d), o) for p, s, d, o in pool])
    got = n2_matches(con, triggers, k=5)
    assert got == n2_matches(con, list(reversed(triggers)), k=5)
    n = 0
    for tr in triggers:
        _, key, t, venue, depth, orientation = tr
        ms = got[tr]
        assert len(ms) <= 5 and len({m for m, _, _ in ms}) == len(ms)
        for m, slot, d in ms:
            n += 1
            assert m != key
            assert slot - slot % HOUR_SLOTS == t - t % HOUR_SLOTS
            if venue == "curve":
                assert abs(d - depth) <= 5 * SOL + 1
                ok = con.execute("SELECT count(*) FROM curve WHERE mint = ? AND slot = ? AND abs(rq::DOUBLE - ?) < 1",
                                 [m, slot, d]).fetchone()[0]
                mayhem, quote = con.execute("SELECT mayhem, quote_mint FROM mints WHERE mint = ?", [m]).fetchone()
                assert not mayhem and quote in SOL_QUOTES
            else:
                assert 0.8 * depth - 1 <= d <= 1.2 * depth + 1
                ok = con.execute("""SELECT count(*) FROM pool WHERE pool = ? AND slot = ? AND kind IN ('buy', 'sell')""",
                                 [m, slot]).fetchone()[0]
                cls, o, mayhem = con.execute("SELECT quote_class, orientation, mayhem FROM pools WHERE pool = ?", [m]).fetchone()
                assert cls in ("sol", "reversed") and o == orientation and not mayhem
            assert ok >= 1
    assert n > 0
