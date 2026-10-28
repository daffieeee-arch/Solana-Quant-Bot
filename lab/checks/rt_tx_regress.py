"""backtest.wash against the blind-spot audit's numbers on the f7rev dev run (dev2, days 1-18).

  python -m checks.rt_tx_regress [<parts dir> <store>]   # exit 1 on any difference

The references (research/blindspots/out/):
  a4b-f7rev-dev2.json     40,737 F7 pool_reversed triggers with a trigger leg, 10,987 of them rt_tx
  a4c-f7rev-returns.json  K3's configuration (tp4_T600, 0.5 SOL, q 0.5, overlay none, base costs), d = 1:
                          filled positions after rt_tx triggers 2,190 (days 1-9) and 6,797 (days 10-18),
                          after the other triggers 10,845 and 17,840, with their mean net returns.
Heavy: the dev2 store (42 GB) is read for the trigger pools; run it on the NAS or in a memory scope.
"""

import os
import sys

import duckdb

LAB = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
PARTS = os.path.join(LAB, "backtests/f7rev-1033-1046/parts")
STORE = os.path.join(LAB, "store/dev2.duckdb")

TRIGGERS = (40_737, 10_987)
# (group, period): (filled positions, mean net return) at d = 1
POSITIONS = {
    ("rt_tx", "dag 1-9"): (2_190, -0.016441400803807824),
    ("rt_tx", "dag 10-18"): (6_797, -0.003527492103877893),
    ("geen rt_tx", "dag 1-9"): (10_845, 0.01788264831436821),
    ("geen rt_tx", "dag 10-18"): (17_840, 0.019367981873875787),
}
PERIODS = {"dag 1-9": "day <= '2026-09-19'", "dag 10-18": "day >= '2026-09-20'"}


def check(parts=None, store=None, log=print):
    """The differences from the references (empty when the flag reproduces the audit)."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    from backtest import wash
    from backtest.costs import Scenario, settle_sql
    from backtest.store import load_defaults

    parts, store = parts or PARTS, store or STORE
    con = duckdb.connect(config={"memory_limit": os.environ.get("LAB_DUCKDB_MEM", "2GB"), "threads": 2})
    if os.environ.get("LAB_SPILL_DIR"):
        con.execute(f"SET temp_directory='{os.environ['LAB_SPILL_DIR']}'")
    con.execute(f"ATTACH '{store}' AS s (READ_ONLY)")
    # a4b: every distinct F7 pool_reversed trigger of the run
    con.execute(f"""CREATE TEMP TABLE trig AS SELECT DISTINCT pool, t, trigger_trader, day, trigger_farmer
                    FROM read_parquet('{parts}/*.parquet') WHERE segment = 'pool_reversed'""")
    wash.flags(con, "trig", buckets=1, wash=False, log=log)
    bad = []
    got = con.execute("""SELECT count(*), count(*) FILTER (WHERE rt_tx) FROM trig JOIN f_rt USING (pool, t, trigger_trader)""").fetchone()
    if tuple(got) != TRIGGERS:
        bad.append(f"triggers with a trigger leg, rt_tx: {tuple(got)} != {TRIGGERS}")
    # a4c: K3's positions at d = 1
    _, ret = settle_sql(Scenario.from_cfg("base", load_defaults()), 1)  # the frozen numbers below: rule 1 (Codex review #6)
    con.execute(f"""CREATE TEMP TABLE pos AS
    SELECT family, pool, t, trigger_trader, day, d, entry_failed, {ret} AS ret FROM read_parquet('{parts}/*.parquet')
    WHERE segment = 'pool_reversed' AND variant = 'tp4_T600' AND size_sol = 0.5 AND q = 0.5 AND NOT skipped
      AND overlay = 'none' AND d = 1 AND family = 'F7'""")
    for (group, period), (n_ref, mean_ref) in POSITIONS.items():
        cond = "rt_tx" if group == "rt_tx" else "NOT rt_tx"
        n, mean = con.execute(f"""SELECT count(*), avg(ret) FROM pos LEFT JOIN f_rt USING (pool, t, trigger_trader)
                                  WHERE {cond} AND {PERIODS[period]} AND NOT entry_failed AND ret IS NOT NULL""").fetchone()
        if n != n_ref or mean is None or abs(mean - mean_ref) > 1e-12:
            bad.append(f"{group}, {period}: n {n} mean {mean} != {n_ref} {mean_ref}")
    log(f"triggers {tuple(got)}; differences: {bad or 'none'}")
    return bad


if __name__ == "__main__":
    sys.exit(1 if check(*sys.argv[1:3]) else 0)
