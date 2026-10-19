"""The join plans of markout's tsig and matched queries, without the engine (runs in CI): a left-only predicate
beside a key (`s.venue = ...` in the first f7rev run, `x.tx AND ...` on tx_entry and `s.venue = 'curve'` on
grad_pool in --tx-delays v1/v2) makes DuckDB plan a blockwise nested-loop join, quadratic in the signals: 35.7 s
on the one-day smoke, hours on the full run (job 99zp, 2026-10-19). Every join in both plans must be a hash join."""

import json

import duckdb

from wallets import markout


def _operators(con, sql):
    tree = json.loads(con.execute("EXPLAIN (FORMAT json) " + sql).fetchall()[0][1])

    def walk(node):
        yield node["name"]
        for child in node.get("children", []):
            yield from walk(child)

    return [name for root in tree for name in walk(root)]


def _con():
    con = duckdb.connect()
    con.execute("CREATE TEMP TABLE targets (sid BIGINT, d INTEGER, h VARCHAR, h_ms BIGINT, target BIGINT)")
    con.execute("CREATE TEMP TABLE sig (sid BIGINT, venue VARCHAR, orientation VARCHAR, key VARCHAR)")
    con.execute("CREATE TEMP TABLE venue_last (key VARCHAR, last_slot BIGINT)")
    con.execute("CREATE TEMP TABLE grad_pool (mint VARCHAR, pool VARCHAR, orientation VARCHAR)")
    con.execute("CREATE TEMP TABLE tsig (sid BIGINT, d INTEGER, h VARCHAR, h_ms BIGINT, target BIGINT, venue VARCHAR, orientation VARCHAR, "
                "key VARCHAR, g_orientation VARCHAR, pkey VARCHAR, ckey VARCHAR, gkey VARCHAR, cut BOOLEAN)")
    con.execute("CREATE TEMP TABLE pool_at (key VARCHAR, target BIGINT, slot BIGINT, b BIGINT, e BIGINT, virt BIGINT, lp_bps BIGINT, "
                "protocol_bps BIGINT, creator_bps BIGINT, cashback_bps BIGINT)")
    con.execute("CREATE TEMP TABLE curve_at (key VARCHAR, target BIGINT, slot BIGINT, vt BIGINT, vq BIGINT, rt BIGINT, rq BIGINT, "
                "fee_bps BIGINT, creator_bps BIGINT)")
    con.execute("CREATE TEMP TABLE tx_entry (sid BIGINT, d INTEGER, slot BIGINT, b BIGINT, e BIGINT, virt BIGINT, lp_bps BIGINT, "
                "protocol_bps BIGINT, creator_bps BIGINT, cashback_bps BIGINT, capped BOOLEAN)")
    return con


def test_tsig_and_matched_plan_hash_joins_only():
    con = _con()
    for sql in (markout.tsig_sql(10**9), markout.matched_sql()):
        joins = [op for op in _operators(con, sql) if "JOIN" in op]
        assert len(joins) == 4 and all(j == "HASH_JOIN" for j in joins), joins


def test_the_old_pattern_is_caught():
    """The guard itself: the v1 join (`x.tx AND` beside the key) must show up as a nested-loop join."""
    con = _con()
    old = markout.matched_sql().replace("LEFT JOIN tx_entry te ON te.sid = x.tsid AND te.d = x.d",
                                        "LEFT JOIN tx_entry te ON x.tx AND te.sid = x.sid AND te.d = x.d")
    assert old != markout.matched_sql()
    assert "BLOCKWISE_NL_JOIN" in _operators(con, old)
