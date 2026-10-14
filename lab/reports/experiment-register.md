# Experimentregister en datagebruik (bijgehouden door de coördinator)

Dit register is het geheugen van het lab. Elke proef krijgt hier een regel: wat er is getest, op welke data, onder welke vooraf vastgelegde regel en met welke uitkomst. **Verworpen ideeën blijven staan.** Zo tellen we eerlijk hoeveel proeven er zijn gedaan (nodig voor DSR/PBO en multiple-testing-correctie) en voorkomen we dat dezelfde data twee keer als "bewijs" wordt gebruikt.

**Regels**
- Een bevestigende toets kan alleen op data die in het datagebruik-register hieronder nog als **schoon** staat, met een pre-registratie (sha256) van vóór de run.
- Alles op al gebruikte data is **verkennend**. Het mag een idee opleveren, maar geen bewijs.
- Een bug die een eerdere uitkomst raakt, krijgt een correctienoot bij die proef (kolom "correcties"), plus een regressietest in de code.
- Labels bij conclusies: **feit** (direct waargenomen in de chaindata), **reconstructie** (afgeleid, met een bekende foutmarge), **modelaanname** (kosten, latency, vulling) en **onbekend**.

## 1. Datagebruik

| periode | epochs | slots | status | gebruikt voor |
|---|---|---|---|---|
| dev, dag 1–9 | 1033–1046 (deel) | 446.256.000 – (2026-09-11 … 09-19) | **verbruikt**, verkennend | drempels, labels (money, whale), toernooien |
| dev, dag 10–18 | 1033–1046 (deel) | – 452.304.000 (2026-09-20 … 09-30) | **verbruikt** als bindende dev-periode van E07 en E10; verder alleen verkennend | M1x §10, money-v1, markout, MEV0, blindspots |
| hold-out | 1047–1051 | 452.304.000 – 454.464.000 | **gesloten**: één keer geopend op 2026-10-12 (E06) | alleen diagnose; nooit meer afstemmen |
| forward | 1052–1054 | 454.464.000 – 455.760.000 | **verbruikt** voor illustratie en techniek | shadows, paired exact-in, Phantom-casussen, MEV0- en transfers-smoke, deze audit (1.000 slots van 1054) |
| **schoon, pre-Alpenglow** | **1055–1063** | 455.760.000 – 459.648.000 | **onaangeroerd, niet gedownload** | gereserveerd voor de eerstvolgende bevestigende toets (met pre-registratie) |
| Alpenglow | ≥ 1064 (vanaf ~2026-10-21) | – | nieuw regime (slots ~200 ms) | live paper (K3-meetklok), later forward-toetsen |

Let op: 1055–1063 is de laatste schone periode met de oude slottijd. Elke analyse die deze epochs aanraakt, moet eerst hier worden ingeschreven, met welk deel en waarvoor.

## 2. Proeven

| id | datum | wat | data | regel vooraf | uitkomst | status | correcties |
|---|---|---|---|---|---|---|---|
| E01 | 2026-10-10 | toernooi v1, 7 families | dev 1040–1046 | week2-strategy-specs | vervangen door v1.1 | **vervallen** | boost-cranks telden dubbel (prijzen in ons voordeel) |
| E02 | 2026-10-10 | toernooi v1.1 | dev 1040–1046 | idem + addendum | F7 in reversed pools als enige positief → hold-out-kandidaten | verkennend | – |
| E03 | 2026-10-11 | whale stap 0: bundels, payer-clusters, veto's | dev, 2 dagen | vooraf vastgelegde veto's K1–K3 (whale) | beschrijvend, pilot | verkennend | – |
| E04 | 2026-10-11/13 | whale stap 1: leiders volgen (W_half, W_full) | dev, 21 dagen | spec stap 1 | volgers −13 % tot −15 % netto per positie, kandidaten niet beter dan controle | **verworpen** | leiders' eigen P&L is een reconstructie zonder transfers (zie E15) |
| E05 | 2026-10-12 | Phantom-tokens als casussen | forward 1052–1054 | geen (illustratie) | 6/16 create-slot-bundels, 3 buiten universum (niet-SOL-quote) | illustratie | – |
| E06 | 2026-10-12 | **hold-out** F7-reversed K1–K3 | 1047–1051 | pre-reg sha 7464771b… | K1 −0,84 % [−2,61 … +0,26]; K2 −1,14 %; K3 +0,02 % [−0,90 … +0,61]; niets geslaagd | **verworpen** (K3 blijft als paper-*meting*, "niet bewezen") | – |
| E07 | 2026-10-12 | M1+ en M1-early | dev, bindend dag 10–18 | specs b89632c5…, 8aaaf266… | §10: niet nuttig | **verworpen** | Δ(V_ins) onder voorbehoud: pairing-bug en fail-open insider-aandeel (#235); herrekening vins2 loopt |
| E08 | 2026-10-12 | paired exact-in na de classificatiefix | forward 1053–1054 | – | 5.223 van 5.223 posities identiek | technische check | #171 (exact-in-classificatie) |
| E09 | 2026-10-12 | watchlist-meting (live dekking) | dev | – | F7-triggers gedekt 24 % (alle) / 74 % (organisch) | beschrijvend | – |
| E10 | 2026-10-13 | geld volgen (money-v1) per walletgroep | dev, labels dag 1–9, meting dag 10–18 | week3-specs-money | geen groep haalt de lat; bots +0,30 SOL per wallet-dag, faalt Holm | **verworpen** | groeps-P&L mist transfers (zie E15); herrekening na de transfers-extractie |
| E11 | 2026-10-15 | markout-curves | dev | week3-specs-markout | dag-smoke klaar; volledige run volgt | lopend | stille 1 GB-cap (#221) |
| E12 | 2026-10-13 | MEV fase 0 smoke | forward 1054 (1.000 slots) | spec v4 | 273 closed-loop arbs, 0,39 SOL | technische check | – |
| E13 | 2026-10-14 | shadow-validatie paper-engine (#233) | forward 1053–1054 | ≥ 30 u, geheugen plateau | lopend | technische check | #237 (KeyError gearchiveerde pools) |
| E14 | – | **K3 paper** (vastgezet, d_ms 600) | live, na Alpenglow | stopregel ≥ 200 trades, ondergrens > 0 | meetklok nog niet gestart | vastgezet, **niet aanraken** | – |
| E15 | 2026-10-15 | **blinde-vlekken-audit v0** | 1054 (1.000 slots, met #239-transfers), dev 1040/1045 (4 tx_ctx-chunks) | geen toets (beschrijvend) | tradebedragen kloppen (94–100 %); wash 74–86 % van het reversed-volume; ~500 spambots doen 85–89 % van de mislukkingen; transfers buiten trades zichtbaar | beschrijvend | – |
| E17 | 2026-10-15 | MEV fase 0: go/no-go fase 1 | dev 1040, 1045 (tx_ctx) | spec v4 sha 57112dee… | **NO-GO**: criterium 1 gehaald, 2–4 niet (top-3 84 %, nieuwkomers 3 %, mislukt ≤ 97 %); 1 volle meetdag | **verworpen** voor fase 1 | OOM-fix da43af8 (uitkomst op smoke identiek) |
| E16 | 2026-10-15 | F7-reversed-triggers: wash-leg (rt_tx) of echte verkoop? | dev2 dag 1–18 (f7rev-run) | geen toets; mechanisme opgeschreven vóór de rendementen | 27 % van de triggers is wash-leg; K3-instelling dag 10–18, d=1: wash −0,35 % [−0,54 … −0,18], echt +1,94 % [+1,39 … +2,24]; N2 reageert niet op de vlag | **verkennend**, kandidaat-risicofilter; volgende stap: hold-out-diagnose en pre-registratie op 1055–1063 | – |

## 3. Bekende stille bugs (correctienoten)

| datum | bug | geraakt | fix | regressietest |
|---|---|---|---|---|
| 2026-10-10 | boost-cranks dubbel toegepast | E01 | v1.1 | ja (store) |
| 2026-10-12 | exact-in-buys verkeerd geclassificeerd | replay pool-buys | #171 | ja |
| 2026-10-12 | `Insiders.share()` afhankelijk van het stream-venster | E07 V_ins | #235 (fail-closed) | ja |
| 2026-10-14 | tijdzone-dagen (lokale tijd i.p.v. UTC) | dagindelingen | #228, #230 | ja (`test_summary_utc`) |
| 2026-10-14 | verborgen DuckDB-limiet van 1 GB | markout, tx_ctx-smoke | #221, #223 | – |
| 2026-10-15 | `_m1x_delta` paarde alleen op pool (30.276 "paren" uit ~140 triggers) | E07 Δ(V_ins) | #235 | ja |
| 2026-10-15 | insider-aandeel fail-open (0 bij circ ≤ 0; None vetode nooit) | E07 | #235 | ja |
| 2026-10-15 | LiveWatchlist KeyError op gearchiveerde pools | shadow (E13) | #237 | ja |
| 2026-10-15 | geheugengroei shadow (farmers.hist, farm_keys, DuckDB-cache) | shadow (E13) | #233 | ja |

**Gecontroleerd en correct (2026-10-15):** de tokenzijde van reversed pools (exact-in `quote_amount_in`, exact-out `user_quote_amount_in`, sell `user_quote_amount_out`) klopt met de token-delta's op de chain. De buyback-fee is een verdeling binnen de protocol-fee (50 %), geen extra kost.
