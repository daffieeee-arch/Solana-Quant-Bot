"""Markdown for the case selection (cases.py): tables per list, a timeline per case, and the
comparison of risers and dumpers in the first 15 minutes after graduation.

  python -m wallets.cases_report --cases DIR
"""

import argparse
import glob
import json
import os
import statistics

import duckdb

FEATS = [("curve_minutes", "min. tot graduatie"), ("buyers", "unieke kopers curve"), ("big_share", "aandeel kopers ≥ 5 SOL"),
         ("inflow_sol", "netto instroom curve (SOL)"), ("insider_share", "insider-aandeel"), ("creator_sells", "creator-verkopen"),
         ("pool_traders", "traders pool"), ("organic_sol", "organisch volume pool (SOL)"), ("px_mult", "prijs t.o.v. open"),
         ("split_events", "split-buyer-events")]


def fmt(x, p=2):
    if x is None:
        return "–"
    if isinstance(x, bool):
        return "ja" if x else "nee"
    if isinstance(x, float):
        return f"{x:.{p}f}"
    return str(x)


def load(cases_dir):
    con = duckdb.connect()
    con.execute("SET memory_limit = '300MB'; SET threads TO 1")
    daily = sorted(glob.glob(os.path.join(cases_dir, "cases-2*.parquet")))
    weekly = sorted(glob.glob(os.path.join(cases_dir, "cases-7d-*.parquet")))
    tls = {}
    for f in sorted(glob.glob(os.path.join(cases_dir, "timelines-*.json"))):
        tls.update(json.load(open(f))["timelines"])
    return con, daily, weekly, tls


def minutes(tl, slot_key, ms=267.3):
    a, b = tl.get("create_slot"), tl.get(slot_key)
    return None if a is None or b is None else (b - a) * ms / 60000


def case_block(tl):
    lines = [f"**{tl.get('symbol') or '?'}** — {tl.get('name') or ''} · [DexScreener]({tl['dexscreener']}) · `{tl['mint']}`", ""]
    lines.append(f"- Tijdlijn: graduatie {fmt(minutes(tl, 'open_slot'), 1)} min na creatie"
                 + (f", piek in de eerste 24 u op {fmt(tl.get('max_mult'), 1)}× de open-prijs"
                    f" ({fmt(minutes(tl, 'peak_slot_24h'), 0)} min na creatie), eind 24 u op {fmt(tl.get('end_mult'), 2)}×"
                    if tl.get("max_mult") is not None else ", geen pool-trades gezien")
                 + (" — graduatie in de create-slot" if tl.get("grad_in_create") else ""))
    if tl.get("t5_buyers") is not None:
        lines.append("- Op +5 / +15 min: " + "; ".join(
            f"{label} {fmt(tl.get('t5_' + k))} / {fmt(tl.get('t15_' + k))}" for k, label in FEATS))
    if tl.get("big_sellers"):
        s = ", ".join(f"{x['sol']:.0f} SOL na {x['minutes_after_open']:.0f} min"
                      + (" (creator)" if x["creator"] else " (insider)" if x["insider"] else "") for x in tl["big_sellers"])
        lines.append(f"- Grootste verkopers (eerste 24 u): {s}")
    return "\n".join(lines)


def compare(tls, risers, dumpers):
    out = ["| kenmerk op +15 min | stijgers (mediaan) | dumpers (mediaan) |", "|---|---|---|"]
    for k, label in FEATS:
        a = [tls[m]["t15_" + k] for m in risers if m in tls and tls[m].get("t15_" + k) is not None]
        b = [tls[m]["t15_" + k] for m in dumpers if m in tls and tls[m].get("t15_" + k) is not None]
        out.append(f"| {label} | {fmt(statistics.median(a)) if a else '–'} (n={len(a)}) | "
                   f"{fmt(statistics.median(b)) if b else '–'} (n={len(b)}) |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True)
    a = ap.parse_args()
    con, daily, weekly, tls = load(a.cases)
    print("# Casussen uit eigen data (Triton/OF1), ontwikkelperiode\n")
    print("Alleen adressen; labels zijn waarschijnlijkheden. Links openen DexScreener voor de eigenaar; het is geen databron.\n")
    risers, dumpers = [], []
    if daily:
        rows = con.execute(f"SELECT day, list, rank, mint, volume_sol, ret_24h, peak_mult, end_of_peak, traders, vault_end, "
                           f"COALESCE(self_funded, false), top_buyer_share FROM read_parquet({daily!r}, union_by_name = true) ORDER BY day, list, rank").fetchall()
        print("## Per dag: trending 24 u (met en zonder zelffinanciering) en pumpers/dumpers\n")
        print("Zelfgefinancierd = één koper graduateerde de token in de create-slot én één partij deed > 50% van de pool-kopen van de dag. "
              "`trending_24h_organic` is dezelfde lijst zonder die tokens.\n")
        print("| dag | lijst | # | token | zelfgefin. | grootste koper | volume SOL | 24-u-rendement | piek × | eind t.o.v. piek | traders | SOL in pool (eind) |")
        print("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for day, lst, rank, mint, vol, ret, peak, eop, tr, va, sf, tb in rows:
            tl = tls.get(mint, {})
            sym = tl.get("symbol") or (mint[:6] if mint else "?")
            link = f"[{sym}](https://dexscreener.com/solana/{mint})" if mint else "?"
            print(f"| {day} | {lst} | {rank} | {link} | {'ja' if sf else 'nee'} | {fmt(tb)} | {vol:,.0f} | {ret:+.0%} | {peak:.1f} | {eop:.0%} | {tr} | {va:,.0f} |")
            if lst == "trending_24h_organic":
                risers.append(mint)
            elif lst == "dumpers_24h" and not sf:
                dumpers.append(mint)
        print()
    if weekly:
        rows = con.execute(f"SELECT list, rank, mint, symbol, mcap_7d_sol, mcap_peak_sol, end_of_peak, volume_7d, traders_7d, vault_7d, "
                           f"COALESCE(self_funded, grad_in_create) FROM read_parquet({weekly!r}, union_by_name = true) ORDER BY list, rank").fetchall()
        print("## 7 dagen: blijvers en instorters (met en zonder zelffinanciering)\n")
        print("| lijst | # | token | zelfgefin. | mcap na 7 d (SOL) | piek-mcap (SOL) | eind t.o.v. piek | volume 7 d | traders | SOL in pool (7 d) |")
        print("|---|---|---|---|---|---|---|---|---|---|")
        for lst, rank, mint, sym, m7, mp, eop, vol, tr, va, sf in rows:
            print(f"| {lst} | {rank} | [{sym}](https://dexscreener.com/solana/{mint}) | {'ja' if sf else 'nee'} | {m7:,.0f} | {mp:,.0f} | {eop:.0%} | {vol:,.0f} | {tr} | {va:,.0f} |")
        print()
    if risers or dumpers:
        print("## Organische stijgers tegenover organische dumpers in de eerste 15 minuten\n")
        print(compare(tls, risers, dumpers))
        print()
    print("## Tijdlijnen\n")
    for mint, tl in tls.items():
        print(case_block(tl))
        print()


if __name__ == "__main__":
    main()
