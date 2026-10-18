# Onderzoeksagenda LAB research, versie 2 (2026-10-17)

Vervolg op `2026-10-17-onderzoeksagenda.md` (v1). Alle vijf sporen van v1 zijn op dev afgesloten (E34–E40): drainrisico vooraf is niet te zien (E37), de grote koop op een jonge curve is de creator zelf (E35), creator-historie maakt de curve niet winstgevend (E38), elke omgekeerde pool wordt leeggetrokken en de rug-check is timing (E36), en K1 rustte op een look-ahead (E40). Wat staat: herstel na een grote verkoop in omgekeerde pools zonder farmers, met vasthouden op echte instroom (H4) of tot +100 % (H5); clean op 24 oktober, daarna forward (E32).

**Vaste regel vanaf nu (les van E39/E40):** een universum, bucket of deelverzameling voor een replay wordt alleen gedefinieerd op kenmerken die op het beslismoment bekend zijn (grootte van de trigger-trade, leeftijd van de pool, creator-historie vóór de creatie, instroom vóór t, label uit eerdere dagen). Gerealiseerde houdtijd, exit-reden, drainmoment, "gesloten" of uitkomst zijn rapportage achteraf, nooit selectie. Elke noot benoemt per selectiekolom of hij point-in-time is. De overige regels blijven: realistische kosten en vertraging, vrij verkennen op dev met elke proef in het register, hold-out alleen diagnose, clean LOCKED tot E20/H3/H4/H5, claims alleen forward met vastlegging vooraf, DSR over de proeven, LAB review; niets verandert K3 of de H-armen vanzelf.

## Wat de paper-data na 20 oktober nieuw mogelijk maakt

De vier goedgekeurde Triton-metingen (spec `research/spec-triton-paper-metingen-2026-10-17.md`) geven dingen die uit Old Faithful niet te halen zijn: het uitvoeringssucces van opgenomen transacties van ons ordertype en de slippage-fouten (punt 1; geen landingskans, zie correctie onderaan), de fee-markt per trigger-pool (7), transacties van de creator en op de LP-mint buiten de pool (4) en de 125 bekende wallets realtime (5). Drie van de vijf ideeën hieronder hangen daaraan; twee kunnen nu al op dev.

## Idee 1 — Exit op de voorbode van de drain (dev-replay nu, live-meting na de start)

**Waarom kansrijk.** E36: de P&L van H4/H5 is het verschil tussen "wij eruit vóór de drain" (+0,18 … +0,28 SOL per positie) en "de drain tijdens de positie" (−0,35 … −0,45, 30–56 % verlies-alles). E37: op t is de drain niet te voorspellen. Maar de drain is een transactie van de creator met zijn LP-tokens; als die wordt voorafgegaan door een zichtbare handeling (LP-tokens naar een andere wallet, `approve`, een SOL-transfer naar de creator, een `SetAuthority`) één of meer slots eerder, is "verkopen bij de voorbode" een point-in-time exit die de staart afsnijdt. In OF1 staat de volgorde binnen en tussen slots; de vraag is alleen of de voorbode bestaat en hoeveel slots eerder.

**Data, tools, rekentijd.** De E19-transfers-extractie met een andere mint-lijst: de **LP-mints van de omgekeerde pools** (PDA `["pool_lp_mint", pool]` van pump_amm, door Lab Backtest geverifieerd), voor dev-epoch 1040 eerst (LP-transfers zijn zeldzaam: kleine tabel). Geen Rust-wijziging; één NAS-extractie in een vrij OF1-venster. Daarna een VPS-join met de H4/H5-dev-parts en `survival-v1`.

**Eerlijke toets.** Per gedraineerde positie: afstand (slots) tussen de eerste LP-handeling van de creator en de withdraw; aandeel met een voorbode ≥ 1 / ≥ 2 / ≥ 3 slots; vals-positieven. Dan een exit-overlay "verkoop op de voorbode + d" op de H4/H5-dev-parts, Σ/dag en slechtste dag, beide dagblokken en regimes; alles als proef in het register (DSR). Live bevestigt Triton-punt 4 de aankomsttijd. Positief op dev én in de live-meting → spec voor een exit-variant (grid) en vastlegging voor een volgende forward-ronde.

**Eerste stap.** Mint-lijst (LP-PDA's van alle reversed pools in 1040, ~3.800) als bestand met sha; extractie-aanvraag bij de coördinator; het leesscript staat klaar vóór de data er is.

## Idee 2 — Een gemeten kostenscenario uit paper (uitvoeringssucces van opgenomen transacties, fees, tips) en de dev-uitkomsten opnieuw onder die kosten

**Waarom kansrijk.** Het hele netto-beeld hangt aan scenario base: fee 0,000605 + tip 0,0001 SOL, land_prob 0,97, failed 0,000505. Dat zijn aannames. Triton-punten 1 en 7 meten per trigger-slot hoeveel kopen van ons type landen, waarom ze mislukken, en welke fee-percentielen in de slots van onze triggers landen — vóór en ná Alpenglow.

**Data, tools, rekentijd.** `failed_txs/` en `fee_checks` uit de eerste twee paperweken; een scenario `measured` in de kostenconfiguratie (zelfde vorm als base/pessimistic); de bestaande dev-parts (H4/H5, E20-serie) opnieuw afrekenen met `costs.settle_sql` onder dat scenario: minuten op de VPS, geen nieuwe replay.

**Eerlijke toets.** Het scenario wordt uit de meting vastgelegd (datum, sha) vóór het wordt toegepast; daarna de dev- en clean-uitkomsten van H4/H5 onder base én measured naast elkaar (geen keuze achteraf); in de forward-toets als tweede serie, de beslisregel blijft base zoals vastgelegd (E32). Verschuift measured het teken, dan is dat een bevinding voor de volgende vastlegging, geen herinterpretatie.

**Eerste stap.** Het leesscript voor punt 1 en 7 (na de bouw) levert per dag: uitvoeringssucces van opgenomen kopen 0,25–1 SOL in trigger-slots (een bovengrens voor `land_prob`, niet de landingskans), slippage-aandeel, fee-p50/p75/p90 in SOL per order; na 7 dagen het scenario-bestand.

## Idee 3 — Universum-klassen van omgekeerde pools als vaste rapportagekolommen (nu), als vastlegging later

**Waarom kansrijk.** Twee point-in-time kenmerken scheiden het drainrisico en de P&L van H4/H5 sterk, maar niet stabiel over de dev-blokken: de creator-klasse bij de creatie (onbekend/system-program: 28–30 % verlies-alles op dag 10–18, 6–10 % op dag 1–9; E34) en de startliquiditeit b0 (100–200 SOL: 1 % verlies-alles; 50–100 SOL: 10 %; E36). Een veto is op dev niet te verdedigen (E34, E37). Wat wel kan: de klassen **beschrijvend** meenemen in elk clean- en forward-rapport, zodat de forward-data zonder nieuwe vastlegging laat zien of de scheiding standhoudt in het nieuwe regime.

**Data, tools, rekentijd.** `k2_pools.parquet`-logica (creator-historie, b0) als kolommen in `clean_reports.py` en het forward-rapport; alles uit de store op de VPS, minuten.

**Eerlijke toets.** Alleen rapportage per klasse (n, Σ, verlies-alles, slechtste dag) in de clean- en forward-rapporten; geen filter. Houdt de scheiding in forward stand (zelfde teken als dag 10–18 op dev, beide klassen voldoende n), dan een vastlegging "H4/H5 zonder klasse X" voor een volgende forward-ronde, met DSR over de klassen als proeven.

**Eerste stap.** De klasse-tabel in `clean_reports.py` (sjabloon, nu), zodat de clean-rapporten van 24 oktober hem al hebben.

## Idee 4 — De 125 bekende wallets realtime rond ónze triggers: tegenpartij, voorloper of irrelevant

**Waarom kansrijk.** Winnaars I/II: het geld op de curve en in de omgekeerde pools gaat naar snelle scalpers, pump-makers en een botcluster dat het drainmoment kent. Triton-punt 5 laat zien wat die wallets doen in de pools waarin wij handelen: zitten ze vóór onze trigger al in de pool, verkopen ze aan ons, of zijn ze er niet? Dat is een meting die uit OF1 wel in volgorde maar niet in tijd te halen is, en het geeft een point-in-time kenmerk ("cluster-wallet in de pool vóór t") dat op dev meteen terug te rekenen is.

**Data, tools, rekentijd.** `tx_wallets`-rijen in de segmenten (keeper bewaart ze), journal `signals`/`exits`; op dev: de store (de 125 adressen zijn bekend) tegen de H4/H5-dev-parts, VPS, minuten.

**Eerlijke toets.** Eerst dev: per H4/H5-positie of een van de 125 vóór t in de pool handelde, en de P&L per klasse (point-in-time). Daarna live: dezelfde telling op de paper-posities. Een veto komt pas na een vastlegging als beide dezelfde kant op wijzen.

**Eerste stap.** De dev-telling (één VPS-run op `c_legs`-logica voor de 125 adressen × H4/H5-pools).

## Idee 5 — De vertraging d ná Alpenglow: meten, dan een d-variant voor een latere vastlegging

**Waarom kansrijk.** d_ms 600 = 3 slots bij 200–240 ms (E14, pre-registratie). Na Alpenglow komt `confirmed` als het certificaat er is (~150 ms-doel); `alpenglow_metrics` M2 meet per trigger de latency tegen de producenttijd. Is die structureel < 1 slot, dan is 3 slots conservatiever dan nodig en is een 2-slot-variant een echte kandidaat (het voordeel van F7-reversed zakte op dev ~0,7 pt per slot; triton-edge-notitie).

**Data, tools, rekentijd.** De M2-meting uit de eerste paperweek; de forward-store ≥ 1064 (Lab Backtest) voor een replay met d = 2 naast d = 3; NAS, zelfde `clean_hx`-vorm.

**Eerlijke toets.** Nooit de lopende forward-toets aanpassen (E32 blijft d_ms 600); een d-variant wordt een aparte vastlegging voor een volgende forward-ronde, met de M2-meting als motivering en DSR over de d-proeven. Op dev is dit al bekend (d 1 tegen 2); het nieuwe is alleen het regime.

**Eerste stap.** Niets vóór de meetweek; daarna één tabel uit M2 (latency tegen d, aandeel later dan d).

## Volgorde en wat ik vraag

| volgorde | idee | eerste stap | waar | vraag |
|---|---|---|---|---|
| 1 | klasse-kolommen in de clean-/forward-rapporten (3) | sjabloon aanpassen | VPS, nu | geen |
| 2 | bekende wallets rond onze triggers, dev-telling (4) | één VPS-run | VPS, nu | geen |
| 3 | drain-voorbode op dev (1) | LP-mint-lijst + extractie-aanvraag | NAS-extractie 1040 in een vrij OF1-venster | ja: extractie met mint-lijst (geen Rust) |
| 4 | gemeten kostenscenario (2) | leesscripts punt 1/7 | VPS, na de bouw en 7 paperdagen | geen (Lab Backtest bouwt de metingen al) |
| 5 | d na Alpenglow (5) | M2-tabel | VPS, na de meetweek | later: forward-store-replay d = 2 (NAS) |

Werk voor de start (leesscripts, Triton-specs) en de clean-rapporten van 24 oktober gaan voor; de eerste twee stappen passen daar tussendoor.

**Correctie 2026-10-18 (Codex-review via de coördinator; spec v1.3).** Meting 1 meet het uitvoeringssucces van *opgenomen* transacties (geslaagd in `tx_pools`, mislukt in `tx_failed`), niet de landingskans: verstuurde transacties die nooit in een blok kwamen (gedropt, verlopen blockhash, niet meegenomen, verloren bundle) ziet de recorder niet. De echte landingskans P(opgenomen en geslaagd | verstuurd) ligt op of onder deze maat en is pas te meten als we zelf versturen (buiten paper). Voor idee 2 betekent dit: het gemeten scenario krijgt failed_tx, fees en tips uit de meting, maar `land_prob` 0,97 blijft een aanname met deze maat als bovengrens.
