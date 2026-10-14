"""A4c (exploratory; dev2 is used data) — F7 pool_reversed returns split by two point-in-time wash flags.

  rt_tx:   the trigger transaction itself also buys the token back (same-tx round trip; visible at decision time)
  wash_1h: >= 50 % of the pool's SOL volume in the hour before the trigger was same-tx round trips
Positions: K3's configuration (variant tp4_T600, 0.5 SOL, q 0.5, overlay none, all farm labels), d = 1 and 2,
base costs (backtest.costs settle_sql). Mean net return per filled position with a 95 % day-bootstrap CI.
Also the random control N2_F7 (same pools, random times) for the pool-level flag.

Usage (cwd = repo lab/): python a4c_f7rev_returns.py <parts dir> <store> <out.json> [holdout]
"""
import json
import os
import sys

import duckdb
import numpy as np

sys.path.insert(0, os.getcwd())
from backtest.costs import Scenario, settle_sql  # noqa: E402
from backtest.store import load_defaults  # noqa: E402

parts, store, out = sys.argv[1:4]
HOUR = 13_500  # slots, ~1 h at 267 ms
con = duckdb.connect(config={"memory_limit": os.environ.get("LAB_DUCKDB_MEM", "2GB"), "threads": 2})
if os.environ.get("LAB_SPILL_DIR"):
    con.execute(f"SET temp_directory='{os.environ['LAB_SPILL_DIR']}'")
con.execute(f"ATTACH '{store}' AS s (READ_ONLY)")
pnl, ret = settle_sql(Scenario.from_cfg("base", load_defaults()))
con.execute(f"""CREATE TEMP TABLE pos AS
SELECT family, pool, t, trigger_trader, day, d, farm, entry_failed, {ret} AS ret
FROM read_parquet('{parts}/*.parquet')
WHERE segment = 'pool_reversed' AND variant = 'tp4_T600' AND size_sol = 0.5 AND q = 0.5 AND NOT skipped AND overlay = 'none' AND d IN (1, 2)""")
con.execute("CREATE TEMP TABLE tr AS SELECT DISTINCT family, pool, t, trigger_trader FROM pos")
lo, hi = con.execute("SELECT min(t), max(t) FROM tr").fetchone()
BUCKETS = int(os.environ.get("A4C_BUCKETS", "8"))
con.execute("CREATE TEMP TABLE f_rt (pool VARCHAR, t BIGINT, trigger_trader VARCHAR, rt_tx BOOLEAN)")
con.execute("CREATE TEMP TABLE f_w (pool VARCHAR, t BIGINT, wash_share DOUBLE)")
for b in range(BUCKETS):  # pools in hash buckets: one bucket's legs at a time (memory)
    con.execute(f"""CREATE OR REPLACE TEMP TABLE trb AS SELECT * FROM tr WHERE hash(pool) % {BUCKETS} = {b}""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE txp AS
    SELECT pool, slot, tx_index, bool_or(token_buy) AND bool_or(NOT token_buy) AS rt, sum(sol_amount) AS sol
    FROM s.pool WHERE kind IN ('buy', 'sell') AND slot BETWEEN {lo - HOUR} AND {hi} AND pool IN (SELECT DISTINCT pool FROM trb)
    GROUP BY 1, 2, 3""")
    # rt_tx: only F7 triggers have a trigger trader; N2 rows get NULL.
    con.execute("""INSERT INTO f_rt
    SELECT tr.pool, tr.t, tr.trigger_trader, bool_or(p.rt)
    FROM trb tr JOIN s.pool l ON l.pool = tr.pool AND l.slot = tr.t AND l.trader = tr.trigger_trader AND NOT l.token_buy AND l.kind IN ('buy', 'sell')
    JOIN txp p ON p.pool = l.pool AND p.slot = l.slot AND p.tx_index = l.tx_index GROUP BY ALL""")
    con.execute(f"""INSERT INTO f_w
    SELECT tr.pool, tr.t, sum(p.sol) FILTER (WHERE p.rt) / NULLIF(sum(p.sol), 0)
    FROM (SELECT DISTINCT pool, t FROM trb) tr JOIN txp p ON p.pool = tr.pool AND p.slot >= tr.t - {HOUR} AND p.slot < tr.t
    GROUP BY ALL""")
    print(f"bucket {b}: {con.execute('SELECT count(*) FROM f_rt').fetchone()[0]} rt rows, {con.execute('SELECT count(*) FROM f_w').fetchone()[0]} w rows", flush=True)
con.execute("DROP TABLE txp")
con.execute("""CREATE TEMP TABLE j AS
SELECT pos.*, f_rt.rt_tx, coalesce(f_w.wash_share, 0) >= 0.5 AS wash_1h, f_w.wash_share
FROM pos LEFT JOIN f_rt USING (pool, t, trigger_trader) LEFT JOIN f_w USING (pool, t)""")


def boot(day_sums, n=10_000, seed=7):
    """Mean over positions with a 95 % CI from resampling whole days: day_sums = [(sum, count)]."""
    a = np.array(day_sums, dtype=float)
    if len(a) == 0 or a[:, 1].sum() == 0:
        return None, None, None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), size=(n, len(a)))
    s, c = a[idx, 0].sum(1), a[idx, 1].sum(1)
    m = s / np.where(c > 0, c, np.nan)
    return float(a[:, 0].sum() / a[:, 1].sum()), float(np.nanpercentile(m, 2.5)), float(np.nanpercentile(m, 97.5))


res = {"parts": parts, "config": "tp4_T600, 0.5 SOL, q 0.5, overlay none, base costs", "rows": []}
PERIODS = (("dag 1-9", "day <= '2026-09-19'"), ("dag 10-18", "day >= '2026-09-20'"))
if len(sys.argv) > 4 and sys.argv[4] == "holdout":  # diagnosis only (hold-out 1047-1051 is closed for tuning)
    PERIODS = (("hold-out", "true"),)
groups = [("F7", "alle", "true"), ("F7", "rt_tx", "rt_tx"), ("F7", "geen rt_tx", "NOT rt_tx"),
          ("F7", "wash_1h", "wash_1h"), ("F7", "geen wash_1h", "NOT wash_1h"),
          ("F7", "geen rt_tx en geen wash_1h", "NOT rt_tx AND NOT wash_1h"),
          ("N2_F7", "alle", "true"), ("N2_F7", "wash_1h", "wash_1h"), ("N2_F7", "geen wash_1h", "NOT wash_1h")]
for fam, name, cond in groups:
    for d in (1, 2):
        for period, days in PERIODS:
            w = f"family = '{fam}' AND d = {d} AND ({cond}) AND {days} AND NOT entry_failed AND ret IS NOT NULL"
            ds = con.execute(f"SELECT sum(ret), count(*) FROM j WHERE {w} GROUP BY day").fetchall()
            n = sum(c for _, c in ds)
            m, lo_, hi_ = boot(ds)
            res["rows"].append({"family": fam, "group": name, "d": d, "period": period, "n": n, "mean": m, "lo": lo_, "hi": hi_})
res["flags"] = con.execute("""SELECT family, count(DISTINCT (pool, t)) AS triggers, avg(rt_tx::INT) AS rt_tx, avg(wash_1h::INT) AS wash_1h
                              FROM (SELECT DISTINCT family, pool, t, rt_tx, wash_1h FROM j) GROUP BY 1 ORDER BY 1""").fetchall()
json.dump(res, open(out, "w"), indent=1, default=str)
for r in res["rows"]:
    f = lambda x: "  –  " if x is None else f"{x * 100:+.2f}%"  # noqa: E731
    print(f"{r['family']:6} {r['group']:28} d={r['d']} {r['period']:9} n={r['n']:6} {f(r['mean'])} [{f(r['lo'])} … {f(r['hi'])}]")
print(res["flags"])
