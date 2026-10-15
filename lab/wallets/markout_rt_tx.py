"""Exploratory (dev2 is used data): the F7 pool_reversed markout curves split by the rt_tx flag of the trigger
(the trigger transaction also buys the token back in the same pool: a same-tx round trip, visible at decision time).
The flag is computed exactly as in research/blindspots/a4c_f7rev_returns.py (sha256 7e6980c3...): per trigger
(pool, t, trigger_trader) from the parts, the trigger's sell leg in the store joined to its transaction, rt_tx =
the transaction has both a buy and a sell on that pool. Markout rows (markout-f7rev.parquet) carry no trader, so
the flag is bool_or over the triggers of a (pool, t). This file is separate from the pre-registered markout spec B
(it changes nothing there) and is descriptive only: a wash split per horizon, not a test.

  LAB_STORE=<dev2> python -m wallets.markout_rt_tx --markout <markout-f7rev.parquet> --parts <f7rev parts> --out <md>
"""

import argparse
import glob
import os
import sys

import numpy as np

from .common import STORE, connect
from .markout_report import DEAD, TRIM, day_ci, trimmed_mean, weighted_trimmed_mean

BUCKETS = 8


def flags(con, parts_dir):
    """Temp table f_rt (pool, t, rt_tx) for the F7 pool_reversed triggers of the parts; same SQL as a4c."""
    files = sorted(glob.glob(os.path.join(parts_dir, "*.parquet")))
    lit = "[" + ",".join(f"'{f}'" for f in files) + "]"
    con.execute(f"""CREATE OR REPLACE TEMP TABLE tr AS
        SELECT DISTINCT pool, t, trigger_trader FROM read_parquet({lit}, union_by_name = true)
        WHERE family = 'F7' AND segment = 'pool_reversed' AND trigger_trader IS NOT NULL""")
    lo, hi = con.execute("SELECT min(t), max(t) FROM tr").fetchone()
    con.execute("CREATE OR REPLACE TEMP TABLE f_rt3 (pool VARCHAR, t BIGINT, trigger_trader VARCHAR, rt_tx BOOLEAN)")
    for b in range(BUCKETS):
        con.execute(f"CREATE OR REPLACE TEMP TABLE trb AS SELECT * FROM tr WHERE hash(pool) % {BUCKETS} = {b}")
        con.execute(f"""CREATE OR REPLACE TEMP TABLE txp AS
            SELECT pool, slot, tx_index, bool_or(token_buy) AND bool_or(NOT token_buy) AS rt
            FROM pool WHERE kind IN ('buy', 'sell') AND slot BETWEEN {lo} AND {hi} AND pool IN (SELECT DISTINCT pool FROM trb)
            GROUP BY 1, 2, 3""")
        con.execute("""INSERT INTO f_rt3
            SELECT tr.pool, tr.t, tr.trigger_trader, bool_or(p.rt)
            FROM trb tr JOIN pool l ON l.pool = tr.pool AND l.slot = tr.t AND l.trader = tr.trigger_trader AND NOT l.token_buy AND l.kind IN ('buy', 'sell')
            JOIN txp p ON p.pool = l.pool AND p.slot = l.slot AND p.tx_index = l.tx_index GROUP BY ALL""")
        print(f"bucket {b}: {con.execute('SELECT count(*) FROM f_rt3').fetchone()[0]} flagged triggers", file=sys.stderr, flush=True)
    con.execute("CREATE OR REPLACE TEMP TABLE f_rt AS SELECT pool, t, bool_or(rt_tx) AS rt_tx FROM f_rt3 GROUP BY 1, 2")
    return con.execute("SELECT count(*), sum(rt_tx::INT) FROM f_rt").fetchone()


def group(con, d, h, cond):
    """Net markouts of one group at (d, h): every row counts (cut rows carry the last known state, as in spec B),
    dead = net <= DEAD (the pool could not pay: LP withdraw to dust). Returns (values, days)."""
    rows = con.execute(f"SELECT net, day FROM mk WHERE d = {d} AND h = '{h}' AND net IS NOT NULL AND ({cond})").fetchall()
    return np.array([r[0] for r in rows], dtype=float), np.array([r[1] for r in rows])


def stats(v, days):
    if len(v) == 0:
        return None
    lo, hi = day_ci(v, days)
    return {"n": len(v), "dead": float((v <= DEAD).mean()), "median": float(np.median(v)), "trim": trimmed_mean(v), "lo": lo, "hi": hi}


def diff_ci(v0, d0, v1, d1, boot=10_000, seed=7):
    """Trimmed mean of group 0 minus group 1 with a paired day bootstrap (both groups drawn with the same day
    counts); (diff, lo, hi) or None without 2 days in both."""
    if len(v0) == 0 or len(v1) == 0:
        return None
    uniq = np.unique(np.concatenate([d0, d1]))
    if len(uniq) < 2:
        return None
    idx = {day: i for i, day in enumerate(uniq)}
    rng = np.random.default_rng(seed)
    counts = rng.multinomial(len(uniq), np.full(len(uniq), 1 / len(uniq)), size=boot)
    out = []
    for v, d in ((v0, d0), (v1, d1)):
        order = np.argsort(v, kind="stable")
        xs, di = v[order], np.array([idx[x] for x in d[order]])
        out.append(np.array([weighted_trimmed_mean(xs, counts[b][di].astype(float)) for b in range(boot)]))
    m = out[0] - out[1]
    m = m[~np.isnan(m)]
    if len(m) == 0:
        return None
    return (trimmed_mean(v0) - trimmed_mean(v1), float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5)))


def render(con, markout_parquet):
    con.execute(f"""CREATE OR REPLACE TEMP TABLE mk AS
        SELECT m.*, f.rt_tx FROM read_parquet('{markout_parquet}') m LEFT JOIN f_rt f USING (pool, t)
        WHERE m.family = 'F7' AND m.segment = 'pool_reversed' AND m.h <> ''""")
    n_sig, n_flag, n_rt = con.execute("SELECT count(DISTINCT (pool, t)), count(DISTINCT (pool, t)) FILTER (WHERE rt_tx IS NOT NULL), count(DISTINCT (pool, t)) FILTER (WHERE rt_tx) FROM mk").fetchone()
    L = ["## F7 pool_reversed markouts split by rt_tx (exploratory, dev2)\n",
         f"Signals {n_sig:,}, with a flag {n_flag:,}, rt_tx {n_rt:,} ({100 * n_rt / max(n_flag, 1):.1f} %). Net markout per signal at 0.5 SOL "
         "(round trip through the pool state at the horizon, base costs), the statistics of spec B: every row counts (a row past the venue's "
         "last leg or the store end carries the last known state, flagged `cut`), dead = net ≤ −99 % (the pool could not pay for the tokens: "
         f"the LP withdrew to dust), trimmed mean ({100 * TRIM:g} % at each end) with a 95 % day-bootstrap CI, the median beside it. "
         "The difference is the difference of the trimmed means with a paired day bootstrap. An earlier version of this table excluded the "
         "cut rows and used plain means: beyond 1 min that is a survivor set priced partly on dust (see the rt_tx check of 2026-10-16). "
         "Descriptive: the flag comes from a4c (sha256 7e6980c3...), the markouts from spec B unchanged.\n"]
    fx = lambda v: "–" if v is None else f"{100 * v:+.2f} %"  # noqa: E731
    fs = lambda st: f"| {st['n']:,} | {100 * st['dead']:.0f} % | {fx(st['trim'])} (med {fx(st['median'])}) | {fx(st['lo'])} … {fx(st['hi'])} " if st else "| 0 | – | – | – "  # noqa: E731
    for d in sorted(r[0] for r in con.execute("SELECT DISTINCT d FROM mk").fetchall()):
        L += [f"\n### d = {d}\n", "| horizon | n rt_tx | dead | rt_tx (trimmed, median) | 95 % CI | n other | dead | geen rt_tx (trimmed, median) | 95 % CI | difference (other − rt_tx) |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for h, _ in con.execute("SELECT DISTINCT h, h_ms FROM mk ORDER BY h_ms").fetchall():
            v1, d1 = group(con, d, h, "rt_tx")
            v0, d0 = group(con, d, h, "NOT rt_tx")
            diff = diff_ci(v0, d0, v1, d1)
            L.append(f"| {h} {fs(stats(v1, d1))}{fs(stats(v0, d0))}| " + (f"{fx(diff[0])} [{fx(diff[1])} … {fx(diff[2])}] |" if diff else "– |"))
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--markout", required=True, help="markout-f7rev.parquet")
    ap.add_argument("--parts", required=True, help="the f7rev parts (trigger traders)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    con = connect(os.environ.get("LAB_STORE", STORE), memory=os.environ.get("LAB_WALLETS_MEMORY", "1500MB"), threads=int(os.environ.get("LAB_WALLETS_THREADS", "2")))
    n, n_rt = flags(con, a.parts)
    print(f"triggers with a flag: {n}, rt_tx: {n_rt}", file=sys.stderr, flush=True)
    md = render(con, a.markout)
    with open(a.out, "w") as f:
        f.write(md + "\n")
    print(a.out, file=sys.stderr)


if __name__ == "__main__":
    main()
