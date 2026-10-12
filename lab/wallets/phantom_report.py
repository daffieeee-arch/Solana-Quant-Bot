"""Markdown for the owner about the named tokens of phantom.py, in plain Dutch.

Merges every timelines-phantom-<tag>.json in --cases: per symbol the mint with the most trades over
all stores wins, the other mints with the same symbol (copycats, earlier launches) stay in the
alternatives table. Addresses only; the DexScreener links are for the owner to look, not a source.

  python -m wallets.phantom_report --cases DIR [--out FILE.md]
"""

import argparse
import glob
import json
import os
import sys


def fmt(x, p=1):
    if x is None:
        return "–"
    if isinstance(x, bool):
        return "ja" if x else "nee"
    if isinstance(x, int):
        return f"{x:,}".replace(",", ".")
    if abs(x) >= 1000:
        return f"{x:,.0f}".replace(",", ".")
    return f"{x:.{p}f}"


def mult(x):
    if x is None:
        return "–"
    return f"{x:,.0f}×".replace(",", ".") if x >= 100 else f"{x:.2f}×" if x < 10 else f"{x:.1f}×"


def pct(x, cap=True):
    if x is None:
        return "–"
    if cap and x > 1.5:
        return "> 100 % (verkopen boven de zichtbare kopen: transfers)"
    return f"{x * 100:.0f} %"


def minutes(m):
    if m is None:
        return "–"
    if m < 1:
        return f"{m * 60:.0f} s"
    if m < 120:
        return f"{m:.0f} min"
    return f"{m / 60:.1f} u"


def load(cases_dir):
    runs = [json.load(open(f)) for f in sorted(glob.glob(os.path.join(cases_dir, "timelines-phantom-*.json")))]
    if not runs:
        raise SystemExit(f"no timelines-phantom-*.json in {cases_dir}")
    chosen, alts, tls = {}, {}, {}
    syms = []
    for r in runs:
        for s in r["symbols"]:
            if s not in syms:
                syms.append(s)
        for s, rows in r.get("candidates", {}).items():
            for c in rows:
                key = (s, c["mint"])
                a = alts.setdefault(key, dict(c, tags=[]))
                a["tags"].append(r["tag"])
                a["n_pool"], a["n_curve"] = max(a["n_pool"], c["n_pool"]), max(a["n_curve"], c["n_curve"])
        for s, m in r["chosen"].items():
            tl = r["timelines"].get(m)
            if tl is None:
                continue
            n = (tl.get("window_24h") or {}).get("trades") or 0
            if s not in chosen or n > chosen[s][1]:
                chosen[s] = (m, n, r["tag"])
                tls[m] = tl
    return runs, syms, chosen, alts, tls


def link(tl_or_mint, label=None):
    m = tl_or_mint["mint"] if isinstance(tl_or_mint, dict) else tl_or_mint
    label = label or (tl_or_mint.get("symbol") if isinstance(tl_or_mint, dict) else m[:6])
    return f"[{label}](https://dexscreener.com/solana/{m})"


def strategy_cell(tl):
    f7, m1 = tl.get("f7") or {}, tl.get("m1") or {}
    a = "–"
    if f7.get("checked"):
        a = ("F7 op +" + minutes(f7["first"][0]["minutes_after_create"]) + f" ({f7['first'][0]['venue']})") if f7.get("first") else "geen F7"
    b = "–"
    if m1.get("checked"):
        k2 = (m1.get("k") or {}).get("2") or {}
        b = ("M1 op +" + minutes(k2["trigger"]["minutes_after_open"])) if k2.get("trigger") else "geen M1"
    elif m1.get("reason"):
        b = f"M1 n.v.t. ({m1['reason']})"
    return a, b


def overview(syms, chosen, tls):
    L = ["| token | naam | aangemaakt (UTC) | graduatie | dev-bundel | curve-kopers | traders 24 u | volume 24 u (SOL) | piek t.o.v. open | eind 24 u | insider-aandeel +15 min | F7 | M1 |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in syms:
        if s not in chosen:
            L.append(f"| {s} | – | niet gevonden | | | | | | | | | | |")
            continue
        tl = tls[chosen[s][0]]
        w = tl.get("window_24h") or {}
        grad = ("in de create-slot" if tl.get("grad_in_create") else "na " + minutes(tl.get("curve_minutes"))) if tl.get("open_slot") is not None \
            else f"niet (curve, laatste stand {fmt(tl.get('curve_end_rq_sol'), 0)} SOL)"
        a, b = strategy_cell(tl)
        L.append(f"| {link(tl)} | {tl.get('name') or ''} | {tl.get('create_time') or '–'} | {grad} | {fmt(tl.get('self_funded'))} | "
                 f"{fmt(tl.get('curve_buyers'))} | {fmt(w.get('traders'))} | {fmt(w.get('vol_sol'), 0)} | {mult(tl.get('max_mult'))} | "
                 f"{mult(tl.get('end_mult'))} | {pct(tl.get('t15_insider_share'))} | {a} | {b} |")
    return "\n".join(L)


def alternatives(syms, chosen, alts):
    L = ["| token | mint | naam | aangemaakt (slot) | gegradueerd | pool-trades | curve-trades | store | gekozen |", "|---|---|---|---|---|---|---|---|---|"]
    n = 0
    for s in syms:
        rows = sorted([a for (ss, _), a in alts.items() if ss == s], key=lambda a: (-(a["n_pool"] + a["n_curve"]), a["mint"]))
        for a in rows[:6]:
            n += 1
            L.append(f"| {s} | {link(a['mint'])} `{a['mint'][:8]}…` | {a.get('name') or ''} | {a.get('create_slot') or '–'} | {fmt(bool(a.get('graduated')))} | "
                     f"{fmt(a['n_pool'])} | {fmt(a['n_curve'])} | {', '.join(a['tags'])} | {'**ja**' if s in chosen and chosen[s][0] == a['mint'] else ''} |")
    return "\n".join(L) if n else "(geen)"


def token_block(s, tl, tag):
    L = [f"### {tl.get('symbol') or s} — {tl.get('name') or ''}", "",
         f"{link(tl, 'DexScreener')} · mint `{tl['mint']}` · store {tag}", ""]
    w = tl.get("window_24h") or {}
    o = tl.get("open_slot")
    facts = [f"Aangemaakt op {tl.get('create_time') or '?'}" + (f" door `{tl['creator'][:8]}…`" if tl.get("creator") else "") + "."]
    if o is not None:
        if tl.get("grad_in_create"):
            facts.append(f"Gegradueerd **in de create-slot**: {fmt(tl.get('curve_buyers'))} wallet(s) kochten de hele curve "
                         f"({fmt(tl.get('curve_sol'), 0)} SOL) in dezelfde slot als de aanmaak — de dev-bundel-structuur.")
        else:
            facts.append(f"Gegradueerd na {minutes(tl.get('curve_minutes'))} met {fmt(tl.get('curve_buyers'))} curve-kopers en "
                         f"{fmt(tl.get('curve_sol'), 0)} SOL curve-volume" + (" (≤ 5 kopers: dev-bundel-vlag)." if tl.get("self_funded") else "."))
        facts.append(f"Eerste 24 u in de pool: {fmt(w.get('trades'))} trades van {fmt(w.get('traders'))} handelaren, {fmt(w.get('vol_sol'), 0)} SOL volume "
                     f"({pct((w.get('organic_sol') or 0) / w['vol_sol'], cap=False) if w.get('vol_sol') else '–'} organisch); "
                     f"piek {mult(tl.get('max_mult'))} de openingsprijs, einde 24 u op {mult(tl.get('end_mult'))}"
                     + (f" ({pct(tl['end_mult'] / tl['max_mult'], cap=False)} van de piek)." if tl.get("max_mult") and tl.get("end_mult") is not None else "."))
    else:
        facts.append(f"Niet gegradueerd in deze store: de curve kwam tot {fmt(tl.get('curve_end_rq_sol'), 0)} SOL reële reserves met "
                     f"{fmt(tl.get('curve_buyers'))} kopers en {fmt(tl.get('curve_sol'), 0)} SOL volume; piek {mult(tl.get('max_mult'))} de eerste prijs, "
                     f"einde op {mult(tl.get('end_mult'))}.")
    ins = tl.get("n_insiders")
    facts.append(f"Insiders (creator + kopers binnen 10 s na de aanmaak): {fmt(ins)}; hun aandeel op +15 min {pct(tl.get('t15_insider_share'))}, "
                 f"na 24 u {pct(tl.get('insider_share_24h'))}; creator-verkopen in 24 u: {fmt(w.get('creator_sells'))}"
                 + (f" ({fmt(w.get('creator_sold_sol'), 1)} SOL)." if w.get("creator_sold_sol") else "."))
    if tl.get("t15_buyers") is not None:
        facts.append(f"Op +15 min: koers {mult(tl.get('t15_px_mult'))} de open, netto instroom {fmt(tl.get('t15_net_sol'), 0)} SOL, "
                     f"{fmt(tl.get('t15_pool_traders'))} pool-traders, {fmt(tl.get('t15_organic_sol'), 0)} SOL organisch volume.")
    L += [f"- {x}" for x in facts]
    L.append("")
    L.append("**Tijdlijn** (koers = reserve-prijs t.o.v. de openingsprijs van de pool; diepte = reële SOL in de pool/curve aan het eind van het vak):\n")
    L.append("| venster | trades | kopers | verkopers | volume SOL | netto SOL | farmer-aandeel | koers eind | koers piek | diepte SOL |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in tl.get("series") or []:
        lab = "curve (vóór de pool)" if r["from_min"] is None else (f"+{r['from_min']}–{r['to_min']} min" if r["to_min"] <= 60 else f"+{r['from_min'] // 60}–{r['to_min'] // 60} u")
        L.append(f"| {lab} | {fmt(r['trades'])} | {fmt(r['buyers'])} | {fmt(r['sellers'])} | {fmt(r['vol_sol'], 0)} | {fmt(r['net_sol'], 0)} | "
                 f"{pct(r['farmer_share'], cap=False)} | {mult(r['px_end_mult'])} | {mult(r['px_max_mult'])} | {fmt(r['depth_sol'], 0)} |")
    L.append("")
    for key, title in (("big_buyers", "Grootste kopers"), ("big_sellers", "Grootste verkopers")):
        rows = tl.get(key) or []
        if rows:
            L.append(f"**{title}** (eerste 24 u): " + "; ".join(
                f"`{x['wallet'][:8]}…` {fmt(x['sol'], 1)} SOL op +{minutes(x.get('minutes_after_open') if x.get('minutes_after_open') is not None else x.get('minutes_after_create'))}"
                + (" (creator)" if x.get("creator") else " (insider)" if x.get("insider") else "") + (" (farmer)" if x.get("farmer") else "")
                for x in rows) + ".\n")
    L.append("**Onze strategieën, beschrijvend** (geen replay, geen P&L):\n")
    f7 = tl.get("f7") or {}
    if not f7.get("checked"):
        L.append(f"- F7: niet te bepalen ({f7.get('reason', 'niet gecontroleerd')}).")
    elif not f7.get("first"):
        L.append("- F7: geen trigger (geen verkoop ≥ max(3 SOL, 4 %) met instroom ervoor, zonder creator-verkoop, op de curve bij 20–75 SOL of in de pool bij ≥ 150 SOL).")
    else:
        for t in f7["first"]:
            after = ", ".join(f"na {k}: {mult(v['end_mult'])} (piek {mult(v['max_mult'])})" for k, v in (t.get("after") or {}).items())
            L.append(f"- F7 ({t['venue']}): eerste trigger op +{minutes(t['minutes_after_create'])} na de aanmaak, diepte {fmt(t['depth_sol'], 0)} SOL, "
                     f"koers {mult(t.get('px_mult'))} de open; {f7.get('n_triggers')} trigger(s) in totaal. Koers daarna t.o.v. de trigger: {after}.")
    m1 = tl.get("m1") or {}
    if not m1.get("checked"):
        L.append(f"- M1: niet van toepassing ({m1.get('reason', 'niet gecontroleerd')}).")
    else:
        for k, r in (m1.get("k") or {}).items():
            t = r.get("trigger")
            if t:
                after = ", ".join(f"na {kk}: {mult(v['end_mult'])} (piek {mult(v['max_mult'])})" for kk, v in (t.get("after") or {}).items())
                L.append(f"- M1 (k = {k}): trigger op +{minutes(t['minutes_after_open'])} na de pool-opening bij {mult(t['ratio'])}, "
                         f"{fmt(t['organic_buyers_15m'])} organische kopers in 15 min, insider-aandeel {pct(t['insider_share'])}, diepte {fmt(t['depth_sol'], 0)} SOL. "
                         f"Koers daarna t.o.v. de trigger: {after}.")
            elif r.get("first_price_candidate"):
                f = r["first_price_candidate"]
                L.append(f"- M1 (k = {k}): de koers haalde {mult(f['ratio'])} op +{minutes(f['minutes_after_open'])} met instroom, maar "
                         f"{fmt(f.get('organic_buyers_15m'))} organische kopers in 15 min / insider-aandeel {pct(f.get('insider_share'))} hielden de trigger tegen.")
            else:
                L.append(f"- M1 (k = {k}): geen trigger binnen 6 u ({m1.get('candidates', 0)} kandidaat-momenten met ≥ 2× en instroom).")
        p = m1.get("m1plus_t15")
        if p:
            vetos = [v for v, on in (("insider-veto (> 90 %)", p.get("veto_ins")), ("hype-veto (> 585 curve-kopers)", p.get("veto_hype"))) if on]
            trig = [k for k, on in (p.get("trigger_k") or {}).items() if on]
            after = ", ".join(f"na {kk}: {mult(v['end_mult'])} (piek {mult(v['max_mult'])})" for kk, v in (p.get("after") or {}).items())
            L.append(f"- M1+ op +15 min: koers {mult(p.get('px_mult'))}, netto instroom {fmt(p.get('net_sol'), 0)} SOL, {fmt(p.get('organic_buyers'))} organische kopers, "
                     f"insider-aandeel {pct(p.get('insider_share'))}, {fmt(p.get('curve_buyers'))} curve-kopers → "
                     + (f"trigger bij k = {', '.join(trig)}" if trig else "geen trigger") + (f", maar {' en '.join(vetos)}" if vetos and trig else f"; {' en '.join(vetos)}" if vetos else "")
                     + (f". Koers daarna t.o.v. +15 min: {after}." if after else "."))
    L.append("")
    return "\n".join(L)


def render(runs, syms, chosen, alts, tls):
    L = ["# Casussen: de Phantom-tokens van de eigenaar\n"]
    stores = "; ".join(f"store {r['tag']}: {r['time_range'][0]} – {r['time_range'][1]} (slots {r['slot_range'][0]:,}–{r['slot_range'][1]:,}, commit {r['commit'][:7]})"
                       for r in runs)
    L.append(f"Alleen ter illustratie: forward-data ná de hold-out ({stores}). Deze dagen zijn nooit gebruikt om iets af te stellen en doen dat ook hierna niet. "
             "Per naam is de mint met de meeste handel gekozen; dezelfde namen worden vaker gelanceerd (copycats), de andere mints staan in de tabel met alternatieven. "
             "Alleen adressen; labels zijn waarschijnlijkheden. Koers = reserve-prijs van de pool (wat een koersdienst toont). "
             "Code: `lab/wallets/phantom.py`, `phantom_report.py`.\n")
    L.append("## Overzicht\n")
    L.append(overview(syms, chosen, tls))
    missing = [s for s in syms if s not in chosen]
    if missing:
        L.append(f"\nNiet gevonden in deze stores: {', '.join(missing)}.")
    L.append("\n## Alternatieven met dezelfde naam\n")
    L.append(alternatives(syms, chosen, alts))
    L.append("\n## Per token\n")
    for s in syms:
        if s in chosen:
            L.append(token_block(s, tls[chosen[s][0]], chosen[s][2]))
    L.append("## Beperkingen\n")
    L.append("- Elke store is één epoch (≈ 2 dagen) en kent alleen zijn eigen events: een token dat in de vorige epoch is aangemaakt en in deze "
             "gradueerde, mist hier zijn curve en aanmaak (geen curve-kopers, geen dev-bundel-vlag, insiders onbekend); de keuze op \"meeste handel\" "
             "pakt dan de epoch met de pool. Dat staat per token bij de store-tag.")
    L.append("- De F7- en M1-regels zijn de definities van het toernooi (triggers), zonder replay: geen vulling, kosten of exit, dus geen P&L.")
    L.append("- Transfers zijn onzichtbaar: een insider-aandeel boven 100 % betekent dat insiders meer verkochten dan ze zichtbaar kochten.")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    md = render(*load(a.cases))
    if a.out:
        with open(a.out, "w") as f:
            f.write(md)
        print(f"wrote {a.out}", file=sys.stderr)
    else:
        print(md)


if __name__ == "__main__":
    main()
