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


def boot(day_sums, n=10_000, seed=7):
    a = np.array(day_sums, dtype=float)
    if len(a) == 0 or a[:, 1].sum() == 0:
        return None, None, None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), size=(n, len(a)))
    s, c = a[idx, 0].sum(1), a[idx, 1].sum(1)
    m = s / np.where(c > 0, c, np.nan)
    return float(a[:, 0].sum() / a[:, 1].sum()), float(np.nanpercentile(m, 2.5)), float(np.nanpercentile(m, 97.5))


def render(con, markout_parquet):
    con.execute(f"""CREATE OR REPLACE TEMP TABLE mk AS
        SELECT m.*, f.rt_tx FROM read_parquet('{markout_parquet}') m LEFT JOIN f_rt f USING (pool, t)
        WHERE m.family = 'F7' AND m.segment = 'pool_reversed' AND m.h <> ''""")
    n_sig, n_flag, n_rt = con.execute("SELECT count(DISTINCT (pool, t)), count(DISTINCT (pool, t)) FILTER (WHERE rt_tx IS NOT NULL), count(DISTINCT (pool, t)) FILTER (WHERE rt_tx) FROM mk").fetchone()
    L = ["## F7 pool_reversed markouts split by rt_tx (exploratory, dev2)\n",
         f"Signals {n_sig:,}, with a flag {n_flag:,}, rt_tx {n_rt:,} ({100 * n_rt / max(n_flag, 1):.1f} %). Net markout per signal at 0.5 SOL "
         "(round trip through the pool state at the horizon, base costs), mean with a 95 % day-bootstrap CI; rows past the venue's last leg or the "
         "store end (cut) excluded. Descriptive: the flag comes from a4c (sha256 7e6980c3...), the markouts from spec B unchanged.\n"]
    fx = lambda v: "–" if v is None else f"{100 * v:+.2f} %"  # noqa: E731
    for d in sorted(r[0] for r in con.execute("SELECT DISTINCT d FROM mk").fetchall()):
        L += [f"\n### d = {d}\n", "| horizon | n rt_tx | rt_tx | 95 % CI | n other | geen rt_tx | 95 % CI | difference (other − rt_tx) |", "|---|---|---|---|---|---|---|---|"]
        for h, h_ms in con.execute("SELECT DISTINCT h, h_ms FROM mk ORDER BY h_ms").fetchall():
            row = {}
            for name, cond in (("rt", "rt_tx"), ("no", "NOT rt_tx")):
                ds = con.execute(f"SELECT day, sum(net), count(*) FROM mk WHERE d = {d} AND h = '{h}' AND NOT cut AND net IS NOT NULL AND ({cond}) GROUP BY day").fetchall()
                row[name] = (sum(c for _, _, c in ds), boot([(s_, c) for _, s_, c in ds]))
            # paired-by-day difference: both groups drawn per day together
            days = con.execute(f"""SELECT day, sum(net) FILTER (WHERE NOT rt_tx), count(*) FILTER (WHERE NOT rt_tx), sum(net) FILTER (WHERE rt_tx), count(*) FILTER (WHERE rt_tx)
                                   FROM mk WHERE d = {d} AND h = '{h}' AND NOT cut AND net IS NOT NULL AND rt_tx IS NOT NULL GROUP BY day""").fetchall()
            a = np.array([[x or 0 for x in r[1:]] for r in days], dtype=float)
            diff = (None, None, None)
            if len(a) and a[:, 1].sum() and a[:, 3].sum():
                rng = np.random.default_rng(7)
                idx = rng.integers(0, len(a), size=(10_000, len(a)))
                m = a[idx, 0].sum(1) / np.where(a[idx, 1].sum(1) > 0, a[idx, 1].sum(1), np.nan) - a[idx, 2].sum(1) / np.where(a[idx, 3].sum(1) > 0, a[idx, 3].sum(1), np.nan)
                diff = (a[:, 0].sum() / a[:, 1].sum() - a[:, 2].sum() / a[:, 3].sum(), float(np.nanpercentile(m, 2.5)), float(np.nanpercentile(m, 97.5)))
            (n1, (m1, l1, h1)), (n0, (m0, l0, h0)) = row["rt"], row["no"]
            L.append(f"| {h} | {n1:,} | {fx(m1)} | {fx(l1)} … {fx(h1)} | {n0:,} | {fx(m0)} | {fx(l0)} … {fx(h0)} | {fx(diff[0])} [{fx(diff[1])} … {fx(diff[2])}] |")
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
