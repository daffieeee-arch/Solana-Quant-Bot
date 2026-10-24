"""E72: ticker / name hypes, S6 (plan v0.2 section 3). key = lower(symbol) without anything but a-z0-9 (a symbol of only
emoji or other symbols gives an empty key and is left out); at a create with
key k on slot s, n_prev60 = earlier creates with key k in the preceding 60 min (slots of the create's clock); the create
is a copy of rank r = n_prev60 + 1 when 1 <= n_prev60 <= 4. Generic-name filter, point-in-time: keys with >= 20 creates in
the preceding 24 h do not count. SOL quote, not Mayhem.

Cells: 'copy' (primary: ranks 2-5 pooled, trigger e10 of the copy), 'copy2' .. 'copy5' (the same rows per rank,
descriptive), 'orig' (secondary: the first create of the cluster, bought at the create slot of the second one).
"""

from .. import runners as R
from ..markout import ms_case
from . import harness as H

SYS = "11111111111111111111111111111111"
GENERIC_24H = 20
MIN60_MS = 3_600_000
H24_MS = 86_400_000


def ranks(con, lo, hi, clock):
    """km (key per create), ranks (n_prev60 / n_prev24 / orig_mint per create in the window), copies (rank 2-5, not generic),
    origs (the first create of a cluster, t = the second create's slot). Returns (creates, copies r2-5, generic dropped)."""
    s60 = f"greatest(1, round({MIN60_MS} / ({ms_case(clock, 'a.create_slot')})))::BIGINT"
    s24 = f"greatest(1, round({H24_MS} / ({ms_case(clock, 'a.create_slot')})))::BIGINT"
    lookback = int(con.execute(f"SELECT greatest(1, round({H24_MS} / ({ms_case(clock, str(lo))})))").fetchone()[0]) + 10
    con.execute(f"""CREATE OR REPLACE TEMP TABLE km AS
        SELECT mint, create_slot, lower(regexp_replace(COALESCE(symbol, ''), '[^a-zA-Z0-9]', '', 'g')) AS k
        FROM mints WHERE quote_mint = '{SYS}' AND NOT COALESCE(mayhem, false) AND create_slot >= {lo} - {lookback} AND create_slot < {hi}""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE ranks AS
        SELECT a.mint, a.create_slot, a.k,
               (SELECT count(*) FROM km b WHERE b.k = a.k AND b.create_slot < a.create_slot AND b.create_slot >= a.create_slot - {s60}) AS n_prev60,
               (SELECT count(*) FROM km b WHERE b.k = a.k AND b.create_slot < a.create_slot AND b.create_slot >= a.create_slot - {s24}) AS n_prev24,
               (SELECT arg_min(b.mint, b.create_slot) FROM km b WHERE b.k = a.k AND b.create_slot < a.create_slot AND b.create_slot >= a.create_slot - {s60}) AS orig_mint
        FROM km a WHERE a.k <> '' AND a.create_slot >= {lo} AND a.create_slot < {hi}""")
    con.execute(f"CREATE OR REPLACE TEMP TABLE copies AS SELECT *, n_prev60 + 1 AS rank FROM ranks WHERE n_prev60 BETWEEN 1 AND 4 AND n_prev24 < {GENERIC_24H}")
    # originals: the first create of a cluster, bought at the second create (n_prev60 = 1 -> that copy's create slot is the trigger)
    con.execute("""CREATE OR REPLACE TEMP TABLE origs AS
        SELECT orig_mint AS mint, min(create_slot) AS t FROM copies WHERE rank = 2 GROUP BY 1""")
    return con.execute("SELECT count(*), count(*) FILTER (WHERE n_prev60 BETWEEN 1 AND 4), count(*) FILTER (WHERE n_prev60 BETWEEN 1 AND 4 AND n_prev24 >= %d) FROM ranks" % GENERIC_24H).fetchone()


def run(con, lo, hi, clock, size_sol, cost_tx, out_dir):
    stats = ranks(con, lo, hi, clock)
    R.progress(f"E72 creates {stats[0]}, copies r2-5 {stats[1]}, of which generic (dropped) {stats[2]}; kept {R._count(con, 'copies')}, originals {R._count(con, 'origs')}")
    tok = H.tok_sql_for_mints("m.mint IN (SELECT mint FROM copies UNION SELECT mint FROM origs)")
    hi_ext = H.prepare(con, lo, hi, clock, tok)
    moments = f"""
        SELECT a.key, 'copy' AS moment, min(a.slot) AS t FROM trades_all a JOIN tok t USING (key) JOIN copies c ON c.mint = t.key WHERE a.slot >= t.create_slot + 10 GROUP BY 1, 2
        UNION ALL
        SELECT a.key, 'copy' || c.rank, min(a.slot) FROM trades_all a JOIN tok t USING (key) JOIN copies c ON c.mint = t.key WHERE a.slot >= t.create_slot + 10 GROUP BY 1, 2
        UNION ALL
        SELECT o.mint, 'orig', o.t FROM origs o JOIN tok t ON t.key = o.mint"""
    H.moments_from(con, moments, clock)
    H.simulate(con, cost_tx, hi_ext, clock)
    con.execute(f"COPY (SELECT * FROM copies) TO '{out_dir}/copies-{lo}.parquet' (FORMAT PARQUET)")
    return H.write(con, out_dir, "e72", lo, size_sol, "e72", {"creates": stats[0], "copies_r2_5": stats[1], "generic_dropped": stats[2],
                                                                 "copies_kept": R._count(con, "copies"), "originals": R._count(con, "origs"), "generic_24h": GENERIC_24H})
