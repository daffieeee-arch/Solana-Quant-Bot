"""Same-transaction round trips in a PumpSwap pool: the wash flags of an F7 trigger, one definition
for every analysis (the clean-data test of the wash-trigger hypothesis, reports, later filters).

  rt_tx    The trigger leg, the trigger wallet's token sell in the pool at slot t, is in a
           transaction that also buys the token in that pool (any wallet). Visible at the decision
           slot: the trigger transaction is complete. NULL when the trigger wallet has no sell leg
           in the pool at t (N2 controls have no trigger wallet).
  wash_1h  Descriptive only: the share of the pool's SOL volume in the HOUR_SLOTS slots before t
           that was same-transaction round trips; >= WASH_SHARE counts as wash_1h.

Both exactly as lab/reports/2026-10-15-blindspots-v0/a4c_f7rev_returns.py (sha256 7e6980c3, the
reference of hypothesis-wash-trigger-2026-10-15, sha256 35bdaa0d): legs of kind buy and sell,
grouped per (pool, slot, tx_index). tests/test_wash.py checks the definition and
checks/rt_tx_regress.py reproduces the audit's numbers on the f7rev dev run.
"""

HOUR_SLOTS = 13_500  # ~1 h at 267 ms, as in the audit
WASH_SHARE = 0.5


def round_trip_txs_sql(store, pools_sql, lo, hi, at_slots_sql=None):
    """Per transaction in a pool (pool, slot, tx_index): rt (it buys and sells the token) and its SOL volume.
    at_slots_sql (pool, t): only the transactions in those pools at those slots (rt_tx alone needs no more)."""
    where = f"slot BETWEEN {lo} AND {hi} AND pool IN ({pools_sql})"
    if at_slots_sql:
        where = f"(pool, slot) IN (SELECT (pool, t) FROM ({at_slots_sql}))"
    return f"""SELECT pool, slot, tx_index, bool_or(token_buy) AND bool_or(NOT token_buy) AS rt, sum(sol_amount) AS sol
    FROM {store}.pool WHERE kind IN ('buy', 'sell') AND {where}
    GROUP BY 1, 2, 3"""


def flags(con, triggers, store="s", buckets=8, wash=True, hour_slots=HOUR_SLOTS, out_rt="f_rt", out_w="f_w", log=None):
    """TEMP tables out_rt (pool, t, trigger_trader, rt_tx) and, with wash, out_w (pool, t, wash_share) for
    the triggers in the table or query `triggers` (columns pool, t, trigger_trader; duplicates are fine).

    `store` is the attached store's alias. Pools go in hash buckets, one bucket's legs at a time
    (memory). Triggers without a trigger wallet or without its sell leg at t get no out_rt row
    (rt_tx NULL after a LEFT JOIN); triggers with no pool volume in the hour get no out_w row."""
    src = triggers if triggers.lstrip().upper().startswith(("SELECT", "WITH")) else f"SELECT * FROM {triggers}"
    con.execute(f"CREATE OR REPLACE TEMP TABLE _wash_tr AS SELECT DISTINCT pool, t, trigger_trader FROM ({src})")
    lo, hi = con.execute("SELECT min(t), max(t) FROM _wash_tr").fetchone()
    con.execute(f"CREATE OR REPLACE TEMP TABLE {out_rt} (pool VARCHAR, t BIGINT, trigger_trader VARCHAR, rt_tx BOOLEAN)")
    if wash:
        con.execute(f"CREATE OR REPLACE TEMP TABLE {out_w} (pool VARCHAR, t BIGINT, wash_share DOUBLE)")
    if lo is None:
        return
    first = lo - hour_slots if wash else lo
    for b in range(buckets):
        con.execute(f"CREATE OR REPLACE TEMP TABLE _wash_trb AS SELECT * FROM _wash_tr WHERE hash(pool) % {buckets} = {b}")
        at = None if wash else "SELECT DISTINCT pool, t FROM _wash_trb"
        con.execute(f"CREATE OR REPLACE TEMP TABLE _wash_txp AS {round_trip_txs_sql(store, 'SELECT DISTINCT pool FROM _wash_trb', first, hi, at)}")
        con.execute(f"""INSERT INTO {out_rt}
        SELECT tr.pool, tr.t, tr.trigger_trader, bool_or(p.rt)
        FROM _wash_trb tr
        JOIN {store}.pool l ON l.pool = tr.pool AND l.slot = tr.t AND l.trader = tr.trigger_trader AND NOT l.token_buy
                               AND l.kind IN ('buy', 'sell')
        JOIN _wash_txp p ON p.pool = l.pool AND p.slot = l.slot AND p.tx_index = l.tx_index
        GROUP BY ALL""")
        if wash:
            con.execute(f"""INSERT INTO {out_w}
            SELECT tr.pool, tr.t, sum(p.sol) FILTER (WHERE p.rt) / NULLIF(sum(p.sol), 0)
            FROM (SELECT DISTINCT pool, t FROM _wash_trb) tr JOIN _wash_txp p ON p.pool = tr.pool AND p.slot >= tr.t - {hour_slots} AND p.slot < tr.t
            GROUP BY ALL""")
        if log:
            log(f"wash flags: bucket {b + 1}/{buckets}")
    for t in ("_wash_txp", "_wash_trb", "_wash_tr"):
        con.execute(f"DROP TABLE {t}")
