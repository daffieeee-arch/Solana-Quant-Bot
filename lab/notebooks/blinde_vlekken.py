"""Blinde vlekken: which information from the chain we use, which we miss, and which earlier conclusions
that may change. Reads the JSON of `python -m checks.blindspots` under
$LAB_DATA_ROOT/research/blindspots/out/ (one file per audited chunk) and the F7-reversed trigger check
(f7rev-wash.json) when present. Read-only. Served with `marimo run` (app mode, code hidden).
"""

import marimo

__generated_with = "0.25.0"
app = marimo.App(width="medium", app_title="Blinde vlekken")


@app.cell
def _():
    import glob
    import json
    import os

    import marimo as mo

    DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
    OUT = os.path.join(DATA, "research", "blindspots", "out")

    def load(path):
        """The JSON file as a dict, or None when it is missing or unreadable."""
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    audits = {}
    for _p in sorted(glob.glob(os.path.join(OUT, "blindspots-*.json"))):
        _r = load(_p)
        if _r:
            audits[os.path.basename(_p)[len("blindspots-"):-len(".json")]] = _r
    wash = load(os.path.join(OUT, "f7rev-wash.json"))

    def pct(x, digits=1):
        return "–" if x is None else f"{x * 100:.{digits}f} %".replace(".", ",")

    def num(x, digits=0):
        if x is None:
            return "–"
        return f"{x:,.{digits}f}".replace(",", " ").replace(".", ",")

    def short(a):
        return "–" if not a else (a if len(a) <= 12 else a[:6] + "…" + a[-4:])

    return OUT, audits, mo, num, pct, short, wash


@app.cell
def _(mo):
    mo.md(r"""
    # Blinde vlekken

    **Wat zien we nu niet?** Deze pagina zet naast elkaar wat de blockchain laat zien en wat onze tabellen
    bewaren. Elke uitspraak heeft een label:

    - **feit**: direct waargenomen in de chaindata;
    - **reconstructie**: afgeleid, met een bekende foutmarge;
    - **aanname**: een model (kosten, vertraging, vulling);
    - **onbekend**: we kunnen het (nog) niet zien.

    Alle cijfers zijn beschrijvend. Er wordt hier niets getoetst en er wordt niets aan K3 of de meetklok veranderd.
    """)
    return


@app.cell
def _(mo):
    _rows = [
        ("Trades op pump.fun en PumpSwap (bedragen, reserves, fees)", "benut", "feit",
         "Gecontroleerd: de tokenbedragen in onze rijen zijn gelijk aan de token-delta van de wallet op de chain (97–100 % per variant)."),
        ("Wie handelt: `user`, fee payer, signers", "deels benut", "feit",
         "De store gebruikt `user` als handelaar. 22 % van de curve-trades heeft een andere fee payer (bots, relayers). De kolom signers is leeg."),
        ("Route: via welk programma de trade liep", "aanwezig, niet benut", "feit",
         "Een groot deel van de curve-trades loopt via bot- of routerprogramma's, niet direct via pump."),
        ("Heen-en-terug in één transactie (wash)", "aanwezig, niet gemarkeerd", "feit",
         "72 % van het SOL-volume in reversed pools (1.000 slots van 1054); in gewone pools ~0 %. Volume- en instroom-features tellen het mee."),
        ("Mislukte transacties", "aanwezig, niet benut", "feit",
         "Meer mislukt dan geland; een klein aantal fee payers doet het meeste. Ons kostenmodel gebruikt alleen een slagingskans."),
        ("Andere DEX'en in dezelfde transactie", "alleen in tx_ctx (2 epochs)", "feit",
         "Nodig voor arbitrage en routes; zit niet in de store."),
        ("Tokenoverdrachten buiten trades", "verloren bij extractie, nu in test (#239)", "feit",
         "Wallets geven tokens aan andere wallets (bundels verdelen voorraad). Verklaart een insider-aandeel > 1."),
        ("SOL-funding van wallets", "verloren bij extractie, nu in test (#239)", "feit",
         "Wie zet welke wallets op? Basis voor een funder-graaf."),
        ("Account aanmaken en sluiten (rent)", "verloren bij extractie, nu in test (#239)", "feit",
         "Een nieuw token-account kost ~0,002 SOL rent, terug bij sluiten. Zit niet in ons kostenmodel."),
        ("Pools met een andere quote dan SOL", "bewust uitgesloten", "feit", "6.345 pools; ook druk verhandelde tokens vallen buiten ons universum."),
        ("Handel op andere DEX'en (Raydium, Meteora) in pump-tokens", "niet geëxtraheerd", "onbekend",
         "Kan alleen met een gerichte her-extractie uit OF1."),
        ("Ontvangsttijd en mempool", "niet in OF1", "onbekend", "Historisch niet te reconstrueren; alleen live te meten (recorder)."),
        ("Jito-bundels (welke transacties samen)", "niet expliciet", "reconstructie", "Alleen af te leiden uit tip en volgorde."),
    ]
    mo.vstack([
        mo.md("## 1. Kaart: welke informatie we benutten en wat we missen"),
        mo.ui.table([{"informatie": a, "status": b, "label": c, "wat het betekent": d} for a, b, c, d in _rows],
                    selection=None, pagination=False),
    ])
    return


@app.cell
def _(audits, mo):
    chunk = mo.ui.dropdown(options=list(audits), value=next(iter(audits), None), label="Gecontroleerde data")
    return (chunk,)


@app.cell
def _(OUT, audits, chunk, mo, num, pct):
    if not audits:
        audit_view = mo.callout(mo.md(f"Nog geen audit gevonden in `{OUT}`."), kind="warn")
    else:
        _r = audits[chunk.value]
        _s = _r.get("slots") or {}
        _actor = [{"venue": _a["venue"], "trades": num(_a["n"]), "user = fee payer": pct(_a["user_is_fee_payer"]),
                   "SOL waar user ≠ payer": num(_a["sol_user_not_payer"])} for _a in _r.get("actor", [])]
        _rt = [{"venue": _a["venue"], "SOL-volume": num(_a["sol"]), "waarvan heen-en-terug in één tx": num(_a["sol_round_trip"]),
                "aandeel": pct(_a["share"])} for _a in _r.get("round_trip_volume", [])]
        _td = [{"venue": _a["venue"], "kant": _a["side"], "variant": _a["variant"], "n": num(_a["n"]),
                "gelijk aan chain": pct(_a["equal"]), "anders": pct(_a["differs"]), "geen delta": pct(_a["no_delta"])}
               for _a in _r.get("token_delta", [])]
        audit_view = mo.vstack([
            mo.md(f"## 2. Wat de chain laat zien: {chunk.value}"),
            mo.md(f"Slots {num(_s.get('lo'))} tot {num(_s.get('hi'))} ({num(_s.get('n'))} slots met transacties). **feit**"),
            chunk,
            mo.md("**Wie handelt?** Aandeel trades waarin de handelaar (`user`) ook de fee payer is."),
            mo.ui.table(_actor, selection=None, pagination=False),
            mo.md("**Wash-volume:** SOL-volume in transacties die dezelfde token in één transactie kopen én verkopen."),
            mo.ui.table(_rt, selection=None, pagination=False),
            mo.md("**Kloppen onze bedragen?** Tokenbedrag in onze rij tegen de token-delta van de wallet op de chain "
                  "(alleen transacties met één trade waarin de handelaar de fee payer is)."),
            mo.ui.table(_td, selection=None, page_size=12),
        ])
    audit_view
    return


@app.cell
def _(audits, chunk, mo, num, pct, short):
    if not audits:
        more_view = mo.md("")
    else:
        _r = audits[chunk.value]
        _blocks = []
        _route = [{"venue": _a["venue"], "programma": short(_a["outer_program"]), "trades": num(_a["n"]), "aandeel": pct(_a["share"])}
                  for _a in _r.get("route", [])]
        _blocks += [mo.md("**Route:** onder welk programma de trade liep (pump / pump_amm = direct)."),
                    mo.ui.table(_route, selection=None, page_size=12)]
        if _r.get("failed"):
            _f = _r["failed"]
            _l = _r.get("landed") or {}
            _blocks += [mo.md(f"**Mislukte transacties:** {num(_f['n'])} mislukt tegen {num(_l.get('n'))} geland; "
                              f"fees mislukt {num(_f['fees_sol'], 2)} SOL tegen geland {num(_l.get('fees_sol'), 2)} SOL. **feit**"),
                        mo.ui.table([{"faalaandeel per fee payer": _a["failure_share"], "payers": num(_a["payers"]),
                                      "geland": num(_a["landed"]), "mislukt": num(_a["failed"]),
                                      "fees mislukt (SOL)": num(_a["failed_fees_sol"], 2)} for _a in _r.get("failed_by_payer", [])],
                                    selection=None, pagination=False)]
        if _r.get("owner_to_owner"):
            _o, _h = _r["owner_to_owner"], _r.get("holders_in_trade_tx") or {}
            _blocks += [mo.md(
                f"**Tokenoverdrachten buiten trades (nieuw, #239):** {num(_o['n'])} overdrachten tussen verschillende eigenaren, "
                f"{num(_o['mints'])} tokens, {num(_o['senders'])} afzenders naar {num(_o['receivers'])} ontvangers; "
                f"{num(_o['receivers_who_trade'])} ontvangers handelen zelf in dezelfde periode. In trade-transacties veranderde "
                f"bij {num(_h.get('not_fee_payer'))} van {num(_h.get('n'))} wallets het saldo terwijl ze niet de fee payer waren. **feit**"),
                mo.ui.table([{"slot": _a["slot"], "token": short(_a["mint"]), "afzender": short(_a["source_owner"]),
                              "ontvangers": _a["receivers"]} for _a in _r.get("fan_out_examples", [])],
                            selection=None, pagination=False)]
        more_view = mo.vstack(_blocks)
    more_view
    return


@app.cell
def _(mo, num, pct, wash):
    if wash is None:
        wash_view = mo.callout(mo.md("De controle van de F7-reversed-triggers (`f7rev-wash.json`) is er nog niet."), kind="neutral")
    else:
        _t = wash.get("summary", {})
        _names = {"rt_tx": "trigger is wash-leg", "geen rt_tx": "echte verkoop", "alle": "alle"}
        _rows = [{"groep": f"{_r['family']}: {_names[_r['group']]}", "d": _r["d"], "periode": _r["period"], "trades": num(_r["n"]),
                  "netto/trade": pct(_r["mean"], 2), "95 % CI (dagen)": f"{pct(_r['lo'], 2)} … {pct(_r['hi'], 2)}"}
                 for _r in wash.get("returns", []) if _r["group"] in _names]
        wash_view = mo.vstack([
            mo.md("## 3. Raakt het K3? F7-triggers in reversed pools (dev, dag 1–18)"),
            mo.md(f"Van {num(_t.get('triggers'))} triggers is de trigger-verkoop bij **{pct(_t.get('round_trip_share'))}** "
                  f"onderdeel van een heen-en-terug in dezelfde transactie, en koopt de verkoper bij "
                  f"{pct(_t.get('buys_back_50'))} van de overige binnen 50 slots terug. **feit**"),
            mo.md("**Rendement per groep** (K3-instelling, basiskosten). **Verkennend**: deze data is al gebruikt, dus dit is "
                  "een idee om te toetsen, geen bewijs. N2 = willekeurige instap in dezelfde pools (controle). "
                  "Uitleg en vervolg: `lab/reports/2026-10-15-blindspots-v0.md` §4."),
            mo.ui.table(_rows, selection=None, pagination=False),
        ])
    wash_view
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## 4. Welke eerdere conclusies kunnen veranderen

    | conclusie | waarom ze kan veranderen | wat we doen |
    |---|---|---|
    | Geld volgen: groeps-P&L (bijv. farmers sterk negatief) | Zonder transfers zien we tokens die een wallet kreeg (niet kocht) als winst of verlies bij verkoop. **reconstructie** | Opnieuw rekenen na de transfers-extractie (#239) |
    | Insider-aandeel > 1 en de V_ins/O7-veto's | Insiders geven tokens door aan andere wallets; die verkopen zien we niet als insider. **feit** | Spec token-transfers Q1 |
    | Whale stap 1: leiders' eigen rendement | Zelfde transfer-probleem; het volger-resultaat (−13 tot −15 %) blijft wel staan, want dat is onze eigen replay. **reconstructie** | Alleen de leiderskolom herrekenen |
    | F7 in reversed pools (K3) | Een deel van de grote verkopen is wash: de prijs wordt in dezelfde transactie teruggezet. **feit** | Zie §3; K3 zelf blijft ongemoeid |
    | Kosten: alleen slagingskans en fees | Rent voor token-accounts en mislukte pogingen van concurrenten ontbreken. **aanname** | Rent in het kostenmodel meten |
    """)
    return


if __name__ == "__main__":
    app.run()
