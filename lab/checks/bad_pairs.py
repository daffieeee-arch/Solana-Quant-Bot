"""Explain the bad PumpSwap continuity pairs one by one.

Input: the Parquet written by continuity.py --dump-bad (one row per bad pair: pool, both event
positions, the pre-state b/e of the later event and the post-state b2/e2 of the earlier one).
For every pair this reports the mismatch in base reserves (dB) and in the effective quote E = quote
vault + virtual quote (dE), which side is SOL (normal pools: E in lamports; reversed pools: B is
WSOL), and the other events of the same pool between the two, including the tables continuity
does not model. The pool's class comes from the backtest store when one is given. Each pair gets a category,
and categories are only as strong as the evidence. A transfer into a vault outside the two
programs is not in the dataset (it only has transactions that touch them), so such pairs are
"consistent with" a transfer and stay open.

Usage: python bad_pairs.py <chunks_dir> <bad_pairs.parquet> [--store dev2.duckdb] [--out pairs.parquet] [--md table.md]
"""

import argparse
import glob
import json
import os

import duckdb

POS = ("slot", "tx_index", "outer_ix", "inner_ix")
SIDES = {"buy": "pump_amm/BuyEvent", "sell": "pump_amm/SellEvent", "deposit": "pump_amm/DepositEvent",
         "withdraw": "pump_amm/WithdrawEvent", "create_pool": "pump_amm/CreatePoolEvent",
         "boost_buy_and_burn": "pump_amm/BoostBuyAndBurnEvent"}
OTHER_POOL_TABLES = ("pump_amm/AdminCtoPoolEvent", "pump_amm/MigratePoolCoinCreatorEvent", "pump_amm/InitBoostEvent")
DUST = 10  # raw units


def chunks(root):
    out = []
    for m in sorted(glob.glob(os.path.join(root, "*", "_manifest.json"))):
        with open(m) as f:
            j = json.load(f)
        if j["status"] == "complete":
            out.append((j["slot_start"], j["slot_end_exclusive"], os.path.dirname(m)))
    return out


def chunk_of(chs, slot):
    return next(d for lo, hi, d in chs if lo <= slot < hi)


def rp(files):
    return "read_parquet([" + ",".join(f"'{f}'" for f in files) + "], union_by_name = true)"


def between(con, chs, pool, a, b):
    """Kinds of other events of the pool strictly between positions a and b (all tables with a pool)."""
    found = []
    ds = {chunk_of(chs, a[0]), chunk_of(chs, b[0])}
    for t in list(SIDES.values()) + list(OTHER_POOL_TABLES):
        files = sorted(f for d in ds for f in glob.glob(os.path.join(d, t, "*.parquet")))
        if not files:
            continue
        n = con.execute(f"""SELECT count(*) FROM {rp(files)} WHERE pool = ?
                            AND (slot, tx_index, outer_ix, inner_ix) > (?, ?, ?, ?)
                            AND (slot, tx_index, outer_ix, inner_ix) < (?, ?, ?, ?)""", [pool, *a, *b]).fetchone()[0]
        if n:
            found.append(f"{t.split('/')[1]}×{n}")
    return found


def categorize(r):
    db, de = r["d_base"], r["d_e"]
    if r["others_between"]:
        return f"other event of the pool in between ({r['others_between']})"
    where = "same transaction" if r["same_tx"] else ("same slot" if r["slot_gap"] == 0 else "later slot")
    if db and not de:
        size = "dust (<= 10 raw units)" if abs(db) <= DUST else "more than dust"
        return f"B changed without an event, {size}, {'+' if db > 0 else '-'}, {where}"
    if de and not db:
        return f"E changed without an event, {'+' if de > 0 else '-'}, {where}"
    return f"B and E changed without an event, {where}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("bad")
    ap.add_argument("--store")
    ap.add_argument("--out")
    ap.add_argument("--md")
    a = ap.parse_args()
    con = duckdb.connect(config={"memory_limit": "1GB", "threads": 2})
    chs = chunks(a.root)
    bad = con.execute(f"SELECT * FROM read_parquet('{a.bad}') ORDER BY slot, tx_index, outer_ix, inner_ix").fetchall()
    names = [d[0] for d in con.description]
    pools = {}
    if a.store:
        st = duckdb.connect(a.store, read_only=True, config={"memory_limit": "1GB"})
        pools = {p: (q, o, m, cb) for p, q, o, m, cb in st.execute(
            "SELECT pool, quote_class, orientation, mayhem, can_boost FROM pools").fetchall()}
        st.close()
    rows = []
    for t in bad:
        x = dict(zip(names, t))
        cur = (x["slot"], x["tx_index"], x["outer_ix"], x["inner_ix"])
        prev = (x["prev_slot"], x["prev_tx_index"], x["prev_outer_ix"], x["prev_inner_ix"])
        d_base = int(x["b"] - x["prev_b2"]) if x["b"] is not None and x["prev_b2"] is not None else 0
        d_e = int(x["e"] - x["prev_e2"]) if x["e"] is not None and x["prev_e2"] is not None else 0
        q = pools.get(x["pool"], (None,) * 4)
        r = {"pool": x["pool"], "kind": x["kind"], "prev_kind": x["prev_kind"], "slot": x["slot"], "prev_slot": x["prev_slot"],
             "tx_index": x["tx_index"], "prev_tx_index": x["prev_tx_index"], "same_tx": cur[:2] == prev[:2],
             "slot_gap": int(x["slot"] - x["prev_slot"]), "d_base": d_base, "d_e": d_e,
             "sol_side": "B (WSOL)" if q[1] == "reversed" else ("E (lamports)" if q[0] == "sol" else "none"),
             "quote_class": q[0], "orientation": q[1], "mayhem": q[2], "can_boost": q[3],
             "others_between": ", ".join(between(con, chs, x["pool"], prev, cur))}
        r["category"] = categorize(r)
        rows.append(r)
    if a.out:
        import pyarrow as pa
        import pyarrow.parquet as pq
        pq.write_table(pa.Table.from_pylist([{k: (str(v) if k.startswith("d_") else v) for k, v in r.items()} for r in rows]), a.out)
    by = {}
    for r in rows:
        by.setdefault(r["category"], []).append(r)
    lines = ["| category | pairs | pools | pool classes | slot gap median | example (pool, slot, dB, dE, SOL side) |",
             "|---|---|---|---|---|---|"]
    for cat, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        gaps = sorted(r["slot_gap"] for r in rs)
        classes = ", ".join(f"{k}×{v}" for k, v in sorted(
            {c: sum(1 for r in rs if (r["orientation"] or "?") == c) for c in {r["orientation"] or "?" for r in rs}}.items()))
        e = rs[0]
        lines.append(f"| {cat} | {len(rs)} | {len({r['pool'] for r in rs})} | {classes} | {gaps[len(gaps) // 2]} | "
                     f"{e['pool'][:8]}…, {e['slot']}, {e['d_base']:+d}, {e['d_e']:+d}, {e['sol_side']} |")
    text = "\n".join(lines) + "\n"
    if a.md:
        with open(a.md, "w") as f:
            f.write(text)
    print(text)


if __name__ == "__main__":
    main()
