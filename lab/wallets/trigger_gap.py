"""The recorder's gap before a reversed pool reaches 150 SOL (LAB backtest question, 2026-10-12, #182): live, a
new reversed pool is watched as a vault balance until it passes 150 SOL and only then as full transactions, so
the trades before that moment are not seen. The F7 trigger itself comes after it; what could suffer is the
seller's farmer flag, which the store computes from the seller's own trades in the pool over the 24 h before
the trigger (backtest.store: >= 2 trades and |net tokens| < 5 % of gross tokens).

For every F7 pool trigger of a tournament run (distinct pool, t, trigger_trader): the seller's trades in the
pool in [t - 24 h, t), how many of them fall before the pool's first slot at >= 150 SOL, and whether the farmer
flag computed from the trades at or after that slot differs from the flag over all of them.

  python -m wallets.trigger_gap --triggers <tournament run dir> --to-slot B --out DIR
"""

import argparse
import glob
import json
import os
import sys
import time

import pyarrow as pa
import pyarrow.parquet as pq

from .common import OUT_ROOT, SOL, Clock, connect, git_commit, guard

FARMER_RATIO = 0.05      # backtest.store.FARMER_RATIO
FARMER_MIN_TRADES = 2    # backtest.store.FARMER_MIN_TRADES
DAY_MS = 24 * 3600 * 1000


def load_triggers(con, trigger_dir, hi):
    files = sorted(glob.glob(os.path.join(trigger_dir, "parts", "part-*.parquet")))
    if not files:
        raise SystemExit(f"no parts in {trigger_dir}")
    return con.execute(f"""
      SELECT DISTINCT pool, t, trigger_trader AS trader, farm, trigger_farmer
      FROM read_parquet({files!r}, union_by_name = true)
      WHERE family = 'F7' AND venue = 'pool' AND size_sol = 0.5 AND d = 1 AND t < {hi} AND trigger_trader IS NOT NULL
        AND variant = (SELECT min(variant) FROM read_parquet({files!r}, union_by_name = true) WHERE family = 'F7')""").to_arrow_table()


def with_window(trig, clock):
    """Adds lo = t - 24 h in slots (the farmer window of the store, per epoch's ms per slot)."""
    t = trig.column("t").to_pylist()
    lo = [x - clock.slots(DAY_MS, x) for x in t]
    return trig.append_column("lo", pa.array(lo, pa.int64()))


MEASURE_SQL = f"""
  WITH deep AS (
    SELECT pool, min(slot) FILTER (WHERE sol_depth >= {150 * SOL}) AS deep_slot, min(slot) AS first_slot
    FROM pool WHERE kind IN ('buy', 'sell') AND pool IN (SELECT pool FROM trig) GROUP BY pool),
  h AS (
    SELECT tr.pool, tr.t, tr.trader, tr.farm, tr.trigger_farmer, d.deep_slot, d.first_slot,
           count(e.slot) AS n_all,
           count(e.slot) FILTER (WHERE e.slot < d.deep_slot) AS n_pre,
           COALESCE(sum(CASE WHEN e.token_buy THEN e.token_amount ELSE -e.token_amount END), 0) AS net_all,
           COALESCE(sum(e.token_amount), 0) AS gross_all,
           COALESCE(sum(CASE WHEN e.token_buy THEN e.token_amount ELSE -e.token_amount END) FILTER (WHERE e.slot >= d.deep_slot), 0) AS net_post,
           COALESCE(sum(e.token_amount) FILTER (WHERE e.slot >= d.deep_slot), 0) AS gross_post
    FROM trig tr JOIN deep d USING (pool)
    LEFT JOIN pool e ON e.pool = tr.pool AND e.trader = tr.trader AND e.kind IN ('buy', 'sell')
                     AND e.slot >= tr.lo AND e.slot < tr.t
    GROUP BY ALL)
  SELECT *, n_all >= {FARMER_MIN_TRADES} AND abs(net_all) < {FARMER_RATIO} * gross_all AS flag_all,
            (n_all - n_pre) >= {FARMER_MIN_TRADES} AND abs(net_post) < {FARMER_RATIO} * gross_post AS flag_post
  FROM h"""


def measure(con, trig):
    con.register("trig", trig)
    return con.execute(MEASURE_SQL).to_arrow_table()


def summarize(tbl):
    rows = tbl.to_pylist()
    out = {}
    for key in ("all", "organic", "flagged"):
        sel = [r for r in rows if key == "all" or r["farm"] == key]
        n = len(sel)
        if not n:
            continue
        hist = [r for r in sel if r["n_all"] > 0]
        pre_any = [r for r in sel if r["n_pre"] > 0]
        flips = [r for r in sel if bool(r["flag_all"]) != bool(r["flag_post"])]
        out[key] = {
            "triggers": n,
            "pools": len({r["pool"] for r in sel}),
            "deep_at_first_event": sum(1 for r in sel if r["deep_slot"] == r["first_slot"]),
            "with_history": len(hist),
            "history_trades": sum(r["n_all"] for r in sel),
            "history_trades_pre_deep": sum(r["n_pre"] for r in sel),
            "with_pre_deep_trade": len(pre_any),
            "flag_all_true": sum(1 for r in sel if r["flag_all"]),
            "flag_matches_store": sum(1 for r in sel if bool(r["flag_all"]) == bool(r["trigger_farmer"])),
            "flag_post_true": sum(1 for r in sel if r["flag_post"]),
            "flips": len(flips),
            "flips_lost_flag": sum(1 for r in flips if r["flag_all"] and not r["flag_post"]),
        }
    return out


def report(summary):
    lines = ["# F7-triggers: de verkoper-historie vóór de pool 150 SOL haalde", "",
             "Per F7-pool-trigger (toernooi, 0,5 SOL, d = 1): de trades van de verkoper in die pool in de 24 u vóór de trigger "
             "(de farmer-vlag van de store: ≥ 2 trades en |netto tokens| < 5 % van bruto), hoeveel daarvan vóór de eerste slot "
             "met ≥ 150 SOL reële diepte vallen (live alleen als vault-saldo gezien) en of de vlag zonder die trades omslaat.", "",
             "| triggers | n | pools | pool al diep bij eerste trade | met historie | historie-trades | … vóór 150 SOL | triggers met trade vóór 150 SOL | "
             "vlag (alle trades) | = store-vlag | vlag (alleen na 150 SOL) | omslag | … vlag verloren |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for key, s in summary.items():
        n = s["triggers"]
        lines.append(f"| {key} | {n:,} | {s['pools']:,} | {s['deep_at_first_event'] / n:.1%} | {s['with_history'] / n:.1%} | "
                     f"{s['history_trades']:,} | {s['history_trades_pre_deep'] / max(s['history_trades'], 1):.1%} | "
                     f"{s['with_pre_deep_trade'] / n:.1%} | {s['flag_all_true'] / n:.1%} | {s['flag_matches_store'] / n:.1%} | "
                     f"{s['flag_post_true'] / n:.1%} | {s['flips'] / n:.1%} ({s['flips']:,}) | {s['flips_lost_flag']:,} |")
    lines += ["", "'pool al diep bij eerste trade': de pool had ≥ 150 SOL bij zijn eerste trade in de store (aangemaakt met ≥ 150 SOL, of van vóór de store); "
              "daar is geen gat. '= store-vlag' controleert de herberekening tegen `trigger_farmer` uit het toernooi."]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--triggers", required=True, help="tournament run directory with parts/")
    ap.add_argument("--to-slot", type=int, required=True)
    ap.add_argument("--out", default=os.path.join(OUT_ROOT, "trigger-gap-v1"))
    a = ap.parse_args()
    guard(a.to_slot)
    t0 = time.time()
    con = connect(memory=os.environ.get("LAB_TRIGGER_GAP_MEMORY", os.environ.get("LAB_WALLETS_MEMORY", "1000MB")))
    trig = with_window(load_triggers(con, a.triggers, a.to_slot), Clock.from_store(con))
    print(f"{trig.num_rows} triggers", file=sys.stderr)
    tbl = measure(con, trig)
    os.makedirs(a.out, exist_ok=True)
    pq.write_table(tbl, os.path.join(a.out, "trigger-gap.parquet"))
    summary = summarize(tbl)
    with open(os.path.join(a.out, "summary.json"), "w") as f:
        json.dump({"commit": git_commit(), "triggers_dir": a.triggers, "to_slot": a.to_slot, "seconds": round(time.time() - t0),
                   "summary": summary}, f, indent=1)
    with open(os.path.join(a.out, "report.md"), "w") as f:
        f.write(report(summary) + "\n")
    print(report(summary))
    print(f"{time.time() - t0:.0f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
