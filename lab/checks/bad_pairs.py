"""Split the bad PumpSwap continuity pairs by cause.

Input: the Parquet written by continuity.py --dump-bad (one row per bad pair: pool, both event
positions, the pre-state b/e of the later event and the post-state b2/e2 of the earlier one).

Every pair gets a cause from what changed between the two events:
- A: base up by at most 10 raw units, E unchanged;
- B: base changed by more, E unchanged;
- C: effective quote changed, base unchanged;
- D: both changed.

The pool class (sol, reversed, other) comes from the backtest store; "hold-out only" pools are not
in it. --raw also counts other events of the pool between the two events, from all pump_amm
tables with a pool column. That reads raw chunks, so run it as a heavy job.

Usage: python bad_pairs.py <bad_pairs.parquet> --store dev2.duckdb [--raw <chunks_dir>] [--md out.md]
"""

import argparse
import glob
import json
import os

import duckdb

DUST = 10  # raw units
POOL_TABLES = ("BuyEvent", "SellEvent", "DepositEvent", "WithdrawEvent", "CreatePoolEvent", "BoostBuyAndBurnEvent",
               "InitBoostEvent", "AdminCtoPoolEvent", "MigratePoolCoinCreatorEvent")

CAUSE = f"""CASE WHEN d_b <> 0 AND COALESCE(d_e, 0) = 0 AND abs(d_b) <= {DUST} THEN 'A. base up by at most {DUST} raw units, E unchanged'
                 WHEN d_b <> 0 AND COALESCE(d_e, 0) = 0 THEN 'B. base changed by more, E unchanged'
                 WHEN d_b = 0 AND d_e <> 0 THEN 'C. effective quote changed, base unchanged'
                 ELSE 'D. base and effective quote changed' END"""


def between_counts(con, root, rows):
    """{(pool, slot, tx_index, outer_ix, inner_ix): 'Table×n, ...'} of other pool events strictly between the pair."""
    chunks = []
    for m in sorted(glob.glob(os.path.join(root, "*", "_manifest.json"))):
        with open(m) as f:
            j = json.load(f)
        if j["status"] == "complete":
            chunks.append((j["slot_start"], j["slot_end_exclusive"], os.path.dirname(m)))
    out = {}
    for r in rows:
        pool, a, b = r[0], tuple(r[1:5]), tuple(r[5:9])
        dirs = {d for lo, hi, d in chunks if lo <= b[0] and a[0] < hi}
        found = []
        for t in POOL_TABLES:
            files = sorted(f for d in dirs for f in glob.glob(os.path.join(d, "pump_amm", t, "*.parquet")))
            if not files:
                continue
            lst = "[" + ",".join(f"'{f}'" for f in files) + "]"
            n = con.execute(f"""SELECT count(*) FROM read_parquet({lst}, union_by_name = true) WHERE pool = ?
                                AND slot BETWEEN ? AND ? AND (slot, tx_index, outer_ix, inner_ix) > (?, ?, ?, ?)
                                AND (slot, tx_index, outer_ix, inner_ix) < (?, ?, ?, ?)""",
                            [pool, a[0], b[0], *a, *b]).fetchone()[0]
            if n:
                found.append(f"{t}×{n}")
        out[(pool, *b)] = ", ".join(found)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bad")
    ap.add_argument("--store", required=True)
    ap.add_argument("--raw", help="chunks dir: also count other pool events between each pair (reads raw chunks)")
    ap.add_argument("--md")
    a = ap.parse_args()
    con = duckdb.connect(config={"memory_limit": "700MB", "threads": 1})
    con.execute(f"ATTACH '{a.store}' AS st (READ_ONLY)")
    con.execute(f"""CREATE TEMP TABLE b AS SELECT x.*, {CAUSE} AS cause, COALESCE(p.quote_class, 'hold-out only') AS cls
                    FROM read_parquet('{a.bad}') x LEFT JOIN st.pools p USING (pool)""")
    lines = ["| cause | pairs | sol | reversed | other | hold-out only | dB min … max | dE min … max | median slot gap | same slot |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in con.execute("""SELECT cause, count(*), count(*) FILTER (WHERE cls = 'sol'), count(*) FILTER (WHERE cls = 'reversed'),
                              count(*) FILTER (WHERE cls = 'other'), count(*) FILTER (WHERE cls = 'hold-out only'),
                              min(d_b)::VARCHAR || ' … ' || max(d_b)::VARCHAR, min(d_e)::VARCHAR || ' … ' || max(d_e)::VARCHAR,
                              quantile_disc(slot - prev_slot, 0.5), count(*) FILTER (WHERE slot = prev_slot)
                            FROM b GROUP BY 1 ORDER BY 1""").fetchall():
        lines.append("| " + " | ".join(str(x) for x in r) + " |")
    lines += ["", "Decreases: " + str(con.execute("SELECT count(*) FROM b WHERE d_b < 0 OR d_e < 0").fetchone()[0])
              + "; pairs involving a deposit or withdrawal: " + str(con.execute(
                  "SELECT count(*) FROM b WHERE kind IN ('deposit', 'withdraw') OR prev_kind IN ('deposit', 'withdraw')").fetchone()[0])
              + "; same transaction: " + str(con.execute(
                  "SELECT count(*) FROM b WHERE slot = prev_slot AND tx_index = prev_tx_index").fetchone()[0]) + "."]
    if a.raw:
        rows = con.execute("""SELECT pool, prev_slot, prev_tx_index, prev_outer_ix, prev_inner_ix, slot, tx_index, outer_ix, inner_ix
                              FROM b""").fetchall()
        found = {k: v for k, v in between_counts(con, a.raw, rows).items() if v}
        lines += ["", f"Pairs with another event of the pool in between: {len(found)}"] + [f"- {k[0][:8]}… {k[1]}: {v}" for k, v in found.items()]
    text = "\n".join(lines) + "\n"
    if a.md:
        with open(a.md, "w") as f:
            f.write(text)
    print(text)


if __name__ == "__main__":
    main()
