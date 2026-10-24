"""Report of one breadth probe over all windows and sizes (plan section 1): the capacity pass (capital / 20 per
position, 5 open positions, signals in time order, no free slot = skipped), the net margin per trade at 0.5 SOL with a
day-clustered bootstrap (10,000 x, seed 7), the stop / go verdict, SOL per day at 10 / 25 / 100 SOL, and whether the
probe adds to H5 / C3 (Pearson of the daily net with the E63 parts, share of triggers within 60 s of an H5 / C3 entry
on the same key). Every cell is reported; the primary cell decides.

  python -m wallets.probes.report --probe e74 --in DIR [--out DIR] [--parts DIR] [--boot 10000]
"""

import argparse
import glob
import json
import os
import sys

import duckdb
import numpy as np
import pyarrow.parquet as pq

from ..common import HOLDOUT_START, SOL

PARTS = "/home/chupa/Solana-project/data-old-faithful-one/lab/backtests/capital-ceiling-d15ac45/parts"
SIZES = {10: 0.5, 25: 1.25, 100: 5.0}
SLOTS_OPEN = 5
DAY_BASIS = {"e74": 17, "e71": 20, "e72": 20, "e73": 13}   # plan section 1 / 4.1 / 5.1
PRIMARY = {"e74": "e10", "e71": "e10", "e72": "copy", "e73": "k2n30"}
MIN_TRADES_PER_DAY = 3.0
WINDOW_S = 60


def load(in_dir, probe):
    files = sorted(glob.glob(os.path.join(in_dir, f"{probe}-*.parquet")))
    if not files:
        raise SystemExit(f"no {probe}-*.parquet in {in_dir}")
    con = duckdb.connect()
    con.execute("SET memory_limit='1GB'")
    sol = "cost_sol, exit_sol" if probe == "e74" else "cost, exit_net"
    qcols = ", cost AS cost_q, exit_net AS exit_q" if probe == "e74" else ", NULL::HUGEINT AS cost_q, NULL::HUGEINT AS exit_q"
    cut = ", exit_net_cut, reason_cut" if probe == "e71" else ", NULL::HUGEINT AS exit_net_cut, NULL::VARCHAR AS reason_cut"
    rows = con.execute(f"""SELECT probe, size_sol, moment, key, pool, t, entry_slot, exit_slot, day, reason, {sol} {cut} {qcols}
                           FROM read_parquet({files!r}, union_by_name=true) ORDER BY size_sol, moment, entry_slot, t""").fetchall()
    cols = ["probe", "size_sol", "moment", "key", "pool", "t", "entry_slot", "exit_slot", "day", "reason", "cost", "exit_net", "exit_net_cut", "reason_cut", "cost_q", "exit_q"]
    out = [dict(zip(cols, r)) for r in rows]
    skipped = [r for r in out if r["cost"] is None or r["exit_net"] is None]
    return [r for r in out if r["cost"] is not None and r["exit_net"] is not None], len(skipped)


def capacity(rows):
    """Signals in trigger order (t); a position holds one of SLOTS_OPEN seats from entry_slot through exit_slot, and a
    key with an open position is busy (one position per key, as the engine)."""
    seats = {}  # key -> exit_slot
    taken = []
    for r in sorted(rows, key=lambda r: (r["t"], r["entry_slot"], r["key"])):
        seats = {k: x for k, x in seats.items() if x >= r["entry_slot"]}
        if len(seats) < SLOTS_OPEN and r["key"] not in seats:
            seats[r["key"]] = r["exit_slot"]
            taken.append(r)
    return taken


def by_day(rows, net_col="exit_net"):
    d = {}
    for r in rows:
        x = d.setdefault(r["day"], [0.0, 0.0, 0])
        x[0] += float(r[net_col] or 0) - float(r["cost"])
        x[1] += float(r["cost"])
        x[2] += 1
    return d


def bootstrap(day_tab, boot, seed=7, all_days=()):
    """Margin = sum net / sum cost over resampled days (with replacement); the 2.5 / 97.5 percentiles. Days of the
    probe's basis without a trade enter as zero rows (all_days), so the day count is the basis, not the active days."""
    day_tab = {**{d: [0.0, 0.0, 0] for d in all_days}, **day_tab}
    days = sorted(day_tab)
    if not days:
        return None
    net = np.array([day_tab[d][0] for d in days])
    cost = np.array([day_tab[d][1] for d in days])
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(days), size=(boot, len(days)))
    m = net[idx].sum(axis=1) / np.maximum(cost[idx].sum(axis=1), 1.0)
    return {"days": len(days), "lo": float(np.percentile(m, 2.5)), "hi": float(np.percentile(m, 97.5))}


def cell(rows, probe, boot, net_col="exit_net", all_days=()):
    """One cell: per capital the capacity pass, margin, CI (0.5 SOL), SOL/day."""
    out = {}
    basis = DAY_BASIS[probe]
    for cap, size in SIZES.items():
        rs = [r for r in rows if abs(float(r["size_sol"]) - size) < 1e-9]
        taken = capacity(rs)
        dt = by_day(taken, net_col)
        net = sum(v[0] for v in dt.values())
        cost = sum(v[1] for v in dt.values())
        c = {"size_sol": size, "signals": len(rs), "taken": len(taken), "days_with_trades": len(dt), "day_basis": basis,
             "net_sol": net / SOL, "cost_sol": cost / SOL, "margin": (net / cost) if cost else None,
             "sol_per_day": net / SOL / basis, "trades_per_day": len(taken) / basis,
             "loss_ge_90pct": sum(1 for r in taken if float(r[net_col] or 0) <= 0.1 * float(r["cost"])) / max(1, len(taken)),
             "median_hold_slots": float(np.median([r["exit_slot"] - r["entry_slot"] for r in taken])) if taken else None,
             "reasons": {k: sum(1 for r in taken if (r["reason_cut"] if net_col == "exit_net_cut" else r["reason"]) == k)
                         for k in sorted({(r["reason_cut"] if net_col == "exit_net_cut" else r["reason"]) for r in taken})},
             "uncapped_margin": (sum(float(r[net_col] or 0) - float(r["cost"]) for r in rs) / max(1.0, sum(float(r["cost"]) for r in rs))) if rs else None,
             "truncated_at_holdout": sum(1 for r in taken if r["reason"].startswith("end") and r["exit_slot"] >= HOLDOUT_START - 10)}
        if probe == "e74" and taken:
            qc = sum(float(r["cost_q"]) for r in taken if r["cost_q"] is not None)
            c["margin_pump_units"] = (sum(float(r["exit_q"] or 0) - float(r["cost_q"]) for r in taken if r["cost_q"] is not None) / qc) if qc else None
        if cap == 10:
            c["ci"] = bootstrap(dt, boot, all_days=all_days)
            h1 = {d: v[0] for d, v in by_day([r for r in taken if r["day"] < "2026-09-22"], net_col).items()}
            h2 = {d: v[0] for d, v in by_day([r for r in taken if r["day"] >= "2026-09-22"], net_col).items()}
            c["net_sol_block_1_9"], c["net_sol_block_10_20"] = sum(h1.values()) / SOL, sum(h2.values()) / SOL
            c["daily_net_sol"] = {d: v[0] / SOL for d, v in sorted(dt.items())}
            c["go"] = bool(c["ci"] and c["ci"]["lo"] > 0 and c["trades_per_day"] >= MIN_TRADES_PER_DAY)
        out[str(cap)] = c
    return out


def reference(parts, rows_primary):
    """H5 (flow300_tp100_H6 organic d 3 0.5 SOL) and C3 (hold300, same filter) daily net and entries from the E63 parts."""
    if not parts or not glob.glob(os.path.join(parts, "*.parquet")):
        return None
    con = duckdb.connect()
    con.execute("SET memory_limit='1GB'")
    ref = {}
    # hold300 is C3's exit arm but the E63 parts carry no arm key filter: labelled as such, never as "C3"
    for name, variant in (("H5", "flow300_tp100_H6"), ("hold300 (geen arm-filter)", "hold300")):
        farm = con.execute(f"""SELECT list(DISTINCT farm) FROM read_parquet('{parts}/*.parquet', union_by_name=true) WHERE variant = '{variant}'""").fetchone()[0]
        farm_sql = "AND farm = 'organic'" if "organic" in (farm or []) else ""
        q = f"""SELECT day, sum(coalesce(proceeds, 0) - cost) AS net, list(mint) AS mints, list(pool) AS pools, list(t) AS ts
                FROM read_parquet('{parts}/*.parquet', union_by_name=true)
                WHERE family = 'F7' AND variant = '{variant}' AND d = 3 AND size_sol = 0.5 AND q = 0.5 AND coalesce(overlay, 'none') = 'none'
                  AND NOT skipped AND NOT entry_failed {farm_sql} GROUP BY day"""
        days = {}
        entries = []
        for day, net, mints, pools, ts in con.execute(q).fetchall():
            days[day] = float(net) / SOL
            entries += [(m, p, t) for m, p, t in zip(mints, pools, ts)]
        ref[name] = {"daily_net_sol": days, "entries": entries, "farm_filter": farm_sql or "all farms"}
    return ref


def _ms(slot):
    """Dev slot time per regime: 316 ms up to epoch 1036, 267 ms after (store meta, rounded)."""
    return 316.0 if slot // 432_000 <= 1036 else 267.0


def adds_to(ref, primary_cell, taken_rows):
    """Pearson of the daily net with H5 / hold300 over the common days; share of the taken triggers within 60 s of an
    H5 / hold300 entry on the same key (mint or pool)."""
    out = {}
    mine = primary_cell["daily_net_sol"]
    for name, r in ref.items():
        common = sorted(set(mine) & set(r["daily_net_sol"]))
        a = np.array([mine[d] for d in common])
        b = np.array([r["daily_net_sol"][d] for d in common])
        corr = float(np.corrcoef(a, b)[0, 1]) if len(common) >= 3 and a.std() > 0 and b.std() > 0 else None
        keyed = {}
        for m, p, t in r["entries"]:
            for k in (m, p):
                if k:
                    keyed.setdefault(k, []).append(t)
        near = 0
        for row in taken_rows:
            w = int(WINDOW_S * 1000 / _ms(int(row["t"])))
            ts = keyed.get(row["key"], []) + (keyed.get(row["pool"], []) if row["pool"] and row["pool"] != row["key"] else [])
            if any(abs(int(t) - int(row["t"])) <= w for t in ts):
                near += 1
        out[name] = {"days_common": len(common), "pearson_daily_net": corr, "triggers_within_60s_same_key": near,
                     "share_within_60s": near / max(1, len(taken_rows)), "ref_farm_filter": r["farm_filter"]}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", required=True)
    ap.add_argument("--in", dest="in_dir", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--parts", default=PARTS)
    ap.add_argument("--boot", type=int, default=10_000)
    a = ap.parse_args(argv)
    out_dir = a.out or a.in_dir
    rows, n_skipped = load(a.in_dir, a.probe)
    moments = sorted({r["moment"] for r in rows})
    all_days = sorted({r["day"] for r in rows})  # every UTC day with a signal in any cell / size: zero rows for cells without a trade that day
    res = {"probe": a.probe, "rows": len(rows), "rows_skipped_null_cost": n_skipped, "moments": moments, "primary_moment": PRIMARY[a.probe],
           "days_with_signals": all_days, "cells": {}}
    for m in moments:
        res["cells"][m] = cell([r for r in rows if r["moment"] == m], a.probe, a.boot, all_days=all_days)
        if a.probe == "e71":
            res["cells"][m + "_agentfree"] = cell([r for r in rows if r["moment"] == m], a.probe, a.boot, net_col="exit_net_cut", all_days=all_days)
    prim = res["cells"].get(PRIMARY[a.probe], {}).get("10")
    if prim:
        go = prim["go"]
        if a.probe == "e71":
            af = res["cells"].get(PRIMARY[a.probe] + "_agentfree", {}).get("10")
            go = go and bool(af and af["go"])
            res["agentfree_go"] = bool(af and af["go"])
        res["verdict"] = "GO" if go else "STOP"
        ref = reference(a.parts, None)
        if ref:
            prows = [r for r in rows if r["moment"] == PRIMARY[a.probe] and abs(float(r["size_sol"]) - 0.5) < 1e-9]
            res["adds_to"] = adds_to(ref, prim, capacity(prows))
        else:
            res["adds_to_note"] = f"no E63 parts under {a.parts}: adds-to not computed"
            print(res["adds_to_note"])
    else:
        res["verdict"] = "NO_PRIMARY_CELL"
    if a.probe == "e71":
        runs = sorted(glob.glob(os.path.join(a.in_dir, "..", "s*", "run-e71-*.json")) + glob.glob(os.path.join(a.in_dir, "run-e71-*.json")))
        pars = [json.load(open(f)) for f in runs]
        res["parity"] = {os.path.basename(f): p.get("parity") for f, p in zip(runs, pars)}
        res["parity_ok"] = bool(pars) and all(p.get("parity_ok") for p in pars)
        if not res["parity_ok"]:
            res["verdict"] = "NOT_JUDGED_PARITY"
        peaks = sorted(glob.glob(os.path.join(a.in_dir, "..", "s0.5", "mint_peaks-*.parquet")))
        if peaks:
            con = duckdb.connect()
            con.execute("SET memory_limit='1GB'")
            r = con.execute(f"""SELECT count(*), sum(graduated::INT), avg((peak_mult >= 2)::INT), avg((peak_mult >= 5)::INT), avg((peak_mult >= 10)::INT),
                                       avg((peak_mult >= 20)::INT), median(peak_mult), avg((peak_mult >= 10)::INT) FILTER (WHERE graduated),
                                       avg((peak_mult >= 10)::INT) FILTER (WHERE NOT graduated), avg((peak_mult >= 2)::INT) FILTER (WHERE graduated)
                                FROM read_parquet({peaks!r})""").fetchone()
            res["base_rates_all_mayhem"] = {"mints": r[0], "graduated": r[1], "ge_x2": r[2], "ge_x5": r[3], "ge_x10": r[4], "ge_x20": r[5], "median_peak": r[6],
                                            "ge_x10_graduated": r[7], "ge_x10_not_graduated": r[8], "ge_x2_graduated": r[9]}
    with open(os.path.join(out_dir, f"report-{a.probe}.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    lines = [f"# {a.probe}: {res['verdict']}", "", "| cel | kapitaal | signalen | genomen | trades/dag | marge | CI 95 % (0,5 SOL) | SOL/dag | ≥ 90 % verlies | mediaan hold (slots) |", "|---|---|---|---|---|---|---|---|---|---|"]
    for m, cs in res["cells"].items():
        for cap, c in cs.items():
            ci = c.get("ci")
            lines.append(f"| {m} | {cap} | {c['signals']} | {c['taken']} | {c['trades_per_day']:.2f} | {('%.2f %%' % (100 * c['margin'])) if c['margin'] is not None else '–'} | "
                         f"{('[%.2f; %.2f] %%' % (100 * ci['lo'], 100 * ci['hi'])) if ci else '–'} | {c['sol_per_day']:+.3f} | {100 * c['loss_ge_90pct']:.1f} % | {c['median_hold_slots'] or '–'} |")
    if res.get("base_rates_all_mayhem"):
        b = res["base_rates_all_mayhem"]
        lines.append(f"- basisrates alle Mayhem-mints (§2.0): {b['mints']} mints ({b['graduated']} gegradueerd): ≥ ×2 {100 * b['ge_x2']:.2f} %, ≥ ×5 {100 * b['ge_x5']:.2f} %, "
                     f"≥ ×10 {100 * b['ge_x10']:.2f} %, ≥ ×20 {100 * b['ge_x20']:.2f} %, mediaan ×{b['median_peak']:.3f}; ≥ ×10 gegradueerd {100 * (b['ge_x10_graduated'] or 0):.2f} % / niet {100 * (b['ge_x10_not_graduated'] or 0):.2f} %")
    if "parity_ok" in res:
        lines.append(f"- fill-pariteit (§2.3): {'OK' if res['parity_ok'] else 'FAALT'} — {res['parity']}")
    if res.get("adds_to"):
        for n, v in res["adds_to"].items():
            lines.append(f"- {n}: Pearson dag-netto {v['pearson_daily_net']} over {v['days_common']} dagen; triggers binnen 60 s op dezelfde key {v['triggers_within_60s_same_key']} ({100 * v['share_within_60s']:.1f} %)")
    with open(os.path.join(out_dir, f"report-{a.probe}.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
