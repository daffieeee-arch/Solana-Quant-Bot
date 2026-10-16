# Laatbloeiers tegen drains (spec E §3, verkennend): F7 pool_reversed op dev (2026-10-15)

| | |
|---|---|
| spec | `data-old-faithful-one/lab/research/week3-specs-exits-2026-10-15.md` §3 (sha256 7372ba01…) |
| code | `lab/wallets/latebloom.py`, `nas_latebloom.sh` (#262); run op main 3d402a2+ (NAS task 73-latebloom, 376 s, 8 buckets) |
| uitvoer | `data-old-faithful-one/lab/wallets/latebloom-v1/`: `latebloom.parquet` (sha256 25444f48…, één rij per trigger: uitkomsten + kenmerken), `latebloom.md` (8c57e095…, alle bins), `run-latebloom.json` (ce14ff0a…) |
| data | de 40.557 F7-triggers (distinct pool, t) van de f7rev-run met een 5-min-markout (spec B, `markout-f7rev.parquet`, d = 1, 0,5 SOL) en een opname-slot (D, `survival.parquet`); store dev2; de hold-out is niet gebruikt |

Beschrijvend op verbruikte data: geen model, geen kenmerkselectie, geen drempel, geen registratie als toets. Vraag: welke kenmerken op het beslis-slot t, en welke in de eerste 30/60 s ná de entry, scheiden de triggers die op 5 min nog leven en hoger staan ("laatbloeiers") van de triggers waarvan de pool al is opgenomen of die lager staan? Dag 10–18 bindend (n = 26.186), dag 1–9 ernaast (n = 13.935).

## 1. Definities

- **laat_5** = de pool is niet opgenomen vóór t + d + 5 min én het netto markout op 5 min is > 0; **leeg_5** = de opname (≥ 90 % van de WSOL, D) ligt vóór t + d + 5 min; **lager_5** = de rest. Idem op 15 min.
- Kenmerken op t gebruiken alleen legs met slot ≤ t; de post-entry-vensters alleen legs in (t + d, t + d + X] met X = 30 s, 60 s.
- Per kenmerk: AUC van laat_5 tegen de rest (0,5 = geen scheiding), aandeel laatbloeiers en gemiddeld netto op 5 min (gecapt op +1000 %) in het hoogste tegen het laagste kwintiel (categorisch: per categorie), met een dag-bootstrap (2.000×, dagen samen getrokken) voor het verschil.

## 2. Uitkomsten per blok

| blok | n | laat 5 min | leeg op 5 min | lager op 5 min | laat 15 min | leeg op 15 min | lager op 15 min | netto 5 min (gem.) | netto 5 min (mediaan) |
|---|---|---|---|---|---|---|---|---|---|
| dag 1–9 | 13.935 | 55,7 % | 15,5 % | 28,7 % | 47,0 % | 37,7 % | 15,3 % | +6,5 % | +4,8 % |
| dag 10–18 | 26.186 | 59,6 % | 12,5 % | 27,9 % | 54,0 % | 30,4 % | 15,5 % | +7,3 % | +7,2 % |
| alles | 40.557 | 58,3 % | 13,5 % | 28,2 % | 51,6 % | 32,9 % | 15,4 % | +6,9 % | +6,5 % |

Zes op de tien F7-triggers staan op 5 min nog hoger; op 15 min is de opname (30 %) de grootste tegenhanger, niet een lagere koers (15 %). Dit is het markout-rapport (§4: dead 12 % op 5 min, 30 % op 15 min) in dezelfde cijfers.

## 3. Kenmerken op t: niets scheidt de laatbloeiers vooraf

Dag 10–18; Δ = hoogste − laagste bin, 95 %-CI over dagen.

| kenmerk | AUC laat_5 | laagste bin: laat / netto 5 min | hoogste bin: laat / netto 5 min | Δ laat | Δ netto 5 min |
|---|---|---|---|---|---|
| depth_sol (WSOL-diepte op t) | 0,464 | 150–161 SOL: 62,4 % / +10,1 % | ≥ 219 SOL: 55,7 % / +3,3 % | −6,7 pt [−10,4 … −2,2] | −6,8 pt [−10,7 … −1,2] |
| depth_ratio (diepte / openingsreserve) | 0,458 | ≤ 1,79: 67,1 % / +8,3 % | ≥ 3,02: 56,1 % / +10,7 % | −11,0 pt [−17,2 … −5,6] | +2,4 pt [−5,4 … +8,5] |
| buyers_1h | 0,469 | ≤ 462: 65,2 % / +12,1 % | ≥ 2.062: 55,7 % / +6,4 % | −9,5 pt [−13,8 … −5,3] | −5,7 pt [−11,1 … +1,5] |
| trades_1h | 0,486 | ≤ 1.247: 63,9 % / +10,7 % | ≥ 5.432: 58,4 % / +9,2 % | −5,6 pt [−10,2 … −1,2] | −1,5 pt [−5,8 … +3,1] |
| impact_pct (eigen impact 0,5 SOL) | 0,537 | ≤ 0,54 %: 55,5 % / +3,3 % | ≥ 0,63 %: 62,9 % / +11,0 % | +7,3 pt [+2,8 … +11,1] | +7,7 pt [+2,1 … +11,9] |
| wash_1h (wash-aandeel ≥ 0,5) | 0,509 | nee: 59,0 % / +9,1 % | ja: 61,3 % / +2,3 % | +2,3 pt [−1,3 … +7,1] | −6,8 pt [−10,7 … −4,0] |
| trigger_farmer | 0,489 | nee: 61,1 % / +10,4 % | ja: 58,8 % / +5,5 % | −2,3 pt [−4,7 … +0,7] | −4,9 pt [−7,3 … −1,9] |
| rt_tx (a4c) | 0,515 | nee: 58,6 % / +8,6 % | ja: 62,0 % / +4,3 % | +3,4 pt [−0,4 … +7,7] | −4,4 pt [−9,7 … +0,2] |
| age_min, vol_1h_sol, farmer_share_1h, farm_flagged, regime | 0,49–0,50 | – | – | CI's bevatten 0 | CI's bevatten 0 |
| deposits_pre, withdraws_pre, deposit_by_creator, deposit_by_trigger | 0,50 | vrijwel altijd 0 | – | – | – |

- Geen enkel kenmerk op t komt boven AUC 0,54 of onder 0,46. **Een F7-trigger is op t niet te filteren op laatbloeien** (feit op dev). De richting die er is: dunne pools (laagste diepte-kwintiel, hoogste impact) bloeien vaker na en leveren meer netto (+10 tegen +3 %); dikke pools (≥ 219 SOL) en pools met veel kopers in het uur ervoor minder. Dat is de spiegel van de markout-regel "winst zit in de dunne pool", niet nieuw.
- wash_1h, trigger_farmer en rt_tx drukken het netto (−4 … −7 pt) zonder het laat-aandeel te veranderen: de farmer-triggers bloeien even vaak na, maar minder hoog. Bekend uit stap 1 en het markout-rapport (§6).
- LP-stortingen vóór t komen vrijwel niet voor (deposits_pre bijna altijd 0; last_deposit_min op 360 triggers), dus de "storting door de creator/trigger-wallet" uit de spec is op dev leeg: **onbekend**, niet "geen effect".
- Dag 1–9: dezelfde richtingen, iets scherper (age_min Δ netto −18 pt [−33 … −6]; trigger_farmer −13 pt; impact +12 pt); AUC's 0,46–0,55.

## 4. Kenmerken in de eerste 30/60 s na de entry: de instroom draagt de informatie

Dag 10–18.

| kenmerk | AUC laat_5 [CI] | laagste kwintiel: laat / netto 5 min | hoogste kwintiel: laat / netto 5 min | Δ laat | Δ netto 5 min |
|---|---|---|---|---|---|
| inflow_sol_30 (netto SOL-instroom 30 s) | 0,609 [0,600 … 0,622] | ≤ −7,0 SOL: 43,4 % / −7,1 % | ≥ +12,1 SOL: 70,0 % / +26,0 % | +26,5 pt [+23,9 … +30,0] | +33,1 pt [+29,9 … +37,9] |
| inflow_sol_60 | **0,645** [0,633 … 0,660] | ≤ −7,9 SOL: 37,8 % / −11,5 % | ≥ +17,7 SOL: 73,5 % / +33,3 % | +35,7 pt [+33,4 … +38,9] | +44,7 pt [+40,9 … +50,4] |
| inflow_org_sol_30 / _60 (farmer-legs eruit) | 0,499 / 0,494 | 60,5 % / +9,1 % | 61,1 % / +8,1 % | +0,6 / +0,0 pt (CI's bevatten 0) | −1,0 / −1,9 pt (CI's bevatten 0) |
| drawup_pct_30 (max mid vs entry) | 0,590 | ≤ 8,1 %: 47,1 % / −5,9 % | ≥ 24,6 %: 68,4 % / +25,8 % | +21,3 pt [+19,3 … +23,6] | +31,7 pt [+28,2 … +36,2] |
| drawup_pct_60 | 0,616 | ≤ 10,4 %: 42,6 % / −11,0 % | ≥ 34,9 %: 71,1 % / +31,5 % | +28,5 pt [+25,8 … +31,1] | +42,5 pt [+38,3 … +48,3] |
| drawdown_pct_30 (min mid vs entry) | 0,596 | ≤ −15,0 %: 45,8 % / −1,9 % | ≥ −1,3 %: 69,6 % / +13,1 % | +23,8 pt [+19,7 … +30,0] | +14,9 pt [+12,2 … +18,7] |
| drawdown_pct_60 | 0,625 | ≤ −19,3 %: 42,1 % / −6,3 % | ≥ −2,3 %: 71,6 % / +14,6 % | +29,5 pt [+25,2 … +35,0] | +21,0 pt [+17,8 … +24,7] |
| trades_60 | 0,514 | ≤ 60: 58,1 % / +1,1 % | ≥ 321: 60,7 % / +18,3 % | +2,5 pt [−3,9 … +8,5] | +17,2 pt [+8,4 … +25,0] |
| new_buyers_60 | 0,509 | ≤ 8: 58,4 % / +6,7 % | ≥ 93: 60,8 % / +12,3 % | +2,4 pt [−2,1 … +6,1] | +5,6 pt [+0,6 … +9,6] |
| withdraw_30 (withdraw gezien in 30 s) | 0,482 | nee: 60,5 % / +8,9 % | ja (n = 394, 1,5 %): 1,5 % / −97,3 % | −59,0 pt | −106,1 pt |
| withdraw_60 | 0,465 | nee: 61,4 % / +10,4 % | ja (n = 760, 2,9 %): 0,9 % / −98,3 % | −60,5 pt | −108,8 pt |

1. **De netto SOL-instroom in de eerste 60 s is het sterkste kenmerk** (AUC 0,645): het hoogste kwintiel bloeit in 74 % na met +33 % netto op 5 min, het laagste in 38 % met −12 %. Op 30 s is het al 0,61. Dag 1–9 geeft hetzelfde beeld (0,644; +40 tegen −12 %).
2. **De organische instroom draagt niets** (AUC 0,49–0,50, Δ's rond 0): haal de farmer-legs eruit en het signaal is weg. De voortzetting na een F7-trigger komt dus van de gevlagde farmers zelf, niet van nieuwe organische kopers (reconstructie: het farm-label is de engine-vlag van stap 1). new_buyers_60 (nieuwe distincte kopers) scheidt het laat-aandeel niet (+2,4 pt, CI bevat 0) en het netto maar licht (+5,6 pt).
3. Drawup en drawdown in 30/60 s doen bijna hetzelfde (0,59–0,63): de koers die in de eerste minuut doorzet, zet ook door tot 5 min. Dit overlapt deels mechanisch met de uitkomst (de eerste minuut zit in het 5-min-pad), dus het is **geen voorspelling maar beschrijving van voortzetting**; het verschil met de instroom is dat de instroom vóór de koers komt.
4. Een withdraw in de eerste 30/60 s (1,5 % / 2,9 % van de triggers) is in 98–99 % de opname zelf: netto −97 … −98 %. Een exit "bij de eerste withdraw" redt daar niets (D: de eerste withdraw is in 91 % de opname; de verkoop erna is in een lege pool).
5. Het aantal trades in de eerste minuut zegt iets over de hoogte (netto +18 tegen +1 %) maar niet over het laat-aandeel (CI bevat 0).

## 5. Wat dit betekent voor spec E

- **Vooraf filteren kan niet** (§3): de triggerselectie van F7 verandert niet. Het veto-idee "geen F7 bij dikke pools / veel kopers vooraf" is ≤ 10 pt en zit al in het markout-beeld; geen nieuwe hypothese.
- **Na de entry wel**: de eerste 30–60 s instroom en koers scheiden laatbloeiers van de rest met 30–45 pt netto-verschil op 5 min. Dat is precies wat de flow- en trail-varianten van de exit-grid (spec E §1: flow{10,30,60}_T600, trail{3,5,10}_T600) proberen te verzilveren. Deze noot verandert de grid en de vooraf vastgelegde lat (§2) niet; de grid-uitkomst is het oordeel, deze noot alleen de motivatie. Dat het signaal uit farmer-legs komt, is een waarschuwing: een flow-exit volgt dan de farmer, en de farmer kan zijn gedrag veranderen (of al veranderd zijn na Alpenglow).
- Alles op verbruikte dev-data: alleen een vooraf vastgelegde toets op schone data (clean-v1, na E20) kan iets bevestigen.

## 6. Open en beperkingen

- De aanvulling uit de spec (holder-concentratie van de token op t en de transfers van Q1/Q3 op 1040/1045) is gebouwd (`wallets/latebloom_transfers.py`, #296) en gedraaid (NAS job 89, f241e17, `wallets/latebloom-v1/transfers/epoch-{1040,1045}/`), maar **levert voor F7-reversed geen rij op**: het transfers-ledger bestaat alleen voor mints die in het venster zijn aangemaakt (pump-curve-mints uit `mints`), en geen enkele reversed pool in dev2 heeft zo'n token (feit: 0 van 54.027 reversed pools met een rij in `mints`; 0 van 4.138 resp. 1.535 triggers met een ledger). Dat is de constructie van transfers-v1, geen bug. Een ledger vanaf de pool-start zou een nieuwe extractie met een mint-startlijst vragen (~1,6 TB OF1 per 2 epochs) en laat de oude voorraad onbekend; besluit coördinator 2026-10-16: laten rusten, E22 genoteerd als "niet meetbaar voor F7-reversed met transfers-v1". De kolommen werken wel voor normale pools (de M1x-mints van Q1), mocht dat later nodig zijn.
- Eén periode, 8 dag-blokken in de bootstrap, verbruikte data; geen model en geen multiple-testing-correctie over de 35 kenmerken (daarom alleen effectgroottes met CI, geen "significant"). Na Alpenglow (epoch 1064) een nieuw regime.
- Het markout is op 0,5 SOL en d = 1; de uitkomst kent geen pad (de replay met de grid wel).
