# Blinde vlekken v0: wat zien we nu niet? (2026-10-15)

| | |
|---|---|
| vraag | Waar is ons beeld van de chain onvolledig of verkeerd, en welke eerdere conclusies kunnen daardoor veranderen? |
| code | `lab/checks/blindspots.py` (tests `lab/checks/test_blindspots.py`), notebook `lab/notebooks/blinde_vlekken.py` |
| data | (1) 1.000 slots van epoch 1054 (455.328.000–455.329.000), geëxtraheerd met #239: events, tx_ctx, failed_txs, token_transfers, token_deltas, sol_funding. (2) dev2-store en de f7rev-run (epochs 1033–1046, dag 1–18). (3) Volgt: de vier tx_ctx-chunks van 1040 en 1045 (NAS, runner-taak `15-blindspots`). |
| status | **Beschrijvend en verkennend.** Er wordt niets getoetst. Alle data hier is al gebruikt (zie `experiment-register.md`: E15, E16). K3, de stopregel en de meetklok blijven ongemoeid. |
| labels | **feit** = direct waargenomen in de chaindata · **reconstructie** = afgeleid, met een bekende foutmarge · **aanname** = model · **onbekend** = niet te zien |

## In gewoon Nederlands

1. **Onze tradebedragen kloppen.** Bij 94–100 % van de trades (per variant, ontwikkeldata) is het tokenbedrag in onze rij gelijk aan wat de wallet op de chain echt kreeg of kwijtraakte; de rest ging naar een token-account van een andere eigenaar, niet naar een fout bedrag (< 1,5 % "anders"). Dat geldt ook voor de lastige "omgekeerde" pools (base WSOL). (**feit**)
2. **We zien maar de helft van de geldstromen van een wallet.** Wallets krijgen en geven tokens buiten trades om: in 6,7 minuten 1.131 overdrachten tussen verschillende eigenaren, waarbij één wallet tot 10 andere wallets in één transactie bevoorraadt. Die overdrachten zaten tot nu toe niet in onze data. Conclusies over wallet-winst ("geld volgen", leiders' eigen rendement, het insider-aandeel) zijn daardoor **reconstructies met een gat**. (**feit** voor de overdrachten; de gevolgen zijn een **reconstructie**)
3. **In de pools waar K3 handelt is het meeste volume nep.** 72 % van het SOL-volume in "omgekeerde" pools is heen-en-terug in één transactie: dezelfde wallet verkoopt en koopt dezelfde token binnen één transactie terug (wash). In de ontwikkeldata (vier volle chunks van epochs 1040 en 1045, ~64 uur) is dat 74–86 %. In gewone pools is het ~0 %. (**feit**)
4. **Dat raakt de F7-triggers waar K3 op draait.** Over de hele ontwikkelperiode is bij **27 %** van de F7-triggers in omgekeerde pools de "grote verkoop" zo'n wash-leg: de prijs staat aan het eind van dezelfde transactie alweer terug. Bij nog eens bijna de helft van de rest koopt dezelfde wallet binnen ~13 seconden terug. (**feit**)
5. **En het kost geld.** Op de ontwikkeldata verliezen F7-trades na een wash-trigger −0,35 % tot −1,98 % per trade (interval onder nul), terwijl trades na een echte verkoop +1,0 % tot +1,9 % halen. Dat is **verkennend** (deze data is al gebruikt) en dus een idee om op schone data te toetsen, geen bewijs. K3 blijft ongemoeid.
6. **Mislukte transacties zijn er meer dan gelukte**, maar ze komen vooral van een kleine groep spam- en MEV-bots: In de ontwikkeldata doen ~400–540 fee payers met ≥ 90 % mislukking 85–89 % van alle mislukte pump-transacties (in de 1054-smoke 97 payers, 61 %). Voor gewone handelaars is het slagingspercentage hoog. (**feit**)
7. **Wie "de handelaar" is, is meestal duidelijk.** Bij curve-trades is de `user` in 78 % ook de fee payer. Van de rest komt 80 % van één bekende bot (de Mayhem-agent van pump.fun, die al is uitgesloten als trigger). Wel loopt maar 24 % van de curve-trades direct via pump: de rest gaat via trading-apps en bots. (**feit**)

## 1. Lagenkaart: waar informatie verloren gaat

| laag | wat er in zit | wat er verloren gaat |
|---|---|---|
| **OF1-archief** (CAR-bestanden, ~900 GB/epoch) | alles: elke transactie, alle instructies, pre/post-saldi, logs, mislukte transacties | niets (wel: geen ontvangsttijd, geen mempool) |
| **lokale chunks** (extractor, ~13 GB/epoch) | pump- en PumpSwap-events met context (fee payer, outer program, ix-accounts), failed_txs, token_meta, blocks; tx_ctx alleen voor 1040, 1045 en smokes; transfers alleen in de #239-smoke | transfers, funding, token-saldi van andere eigenaren, handel op andere DEX'en, account aanmaken/sluiten |
| **store dev2** (DuckDB, 42 GB) | trades per venue (curve, pool) met `trader` = `user`, fee payer apart, SOL-zijde en diepte, farmer-vlag (24 u) | outer program (route), mislukte transacties, wash-markering per transactie, signers (leeg) |
| **afgeleide features** (F7, M1, insiders, money, whale) | point-in-time vensters op de store | alles wat de store mist; insiders en wallet-P&L zonder transfers |

**Wat we ermee doen:** benut en gecontroleerd (tradebedragen) · aanwezig maar niet benut (route, failed_txs, wash per transactie) · verloren bij extractie maar terug te halen uit OF1 (transfers, funding, token-saldi: #239) · niet te reconstrueren uit lokale data (andere DEX'en: gerichte her-extractie) · niet te reconstrueren (ontvangsttijd, mempool: alleen live).

## 2. Bevindingen met echte voorbeelden (1.000 slots van 1054)

### 2.1 De tradebedragen kloppen met de chain (**feit**)

Voor transacties met één trade waarin de handelaar de fee payer is, vergelijken we het tokenbedrag in onze rij met de token-delta van de signer (`tx_ctx.signer_token_deltas`):

| venue | variant | n | gelijk | anders | geen delta |
|---|---|---|---|---|---|
| curve | buy, buy_v2, buy_exact_quote_in_v2/v3 | 4.513 | 97,5–100 % | 0–0,2 % | 0–2,5 % |
| curve | sell, sell_v2, sell_v3 | 4.954 | 98,2–100 % | 0–0,1 % | 0–1,8 % |
| pool | buy_exact_quote_in, buy | 17.498 | 97,3–98,3 % | 0,1–0,6 % | 1,1–2,6 % |
| pool | buy_v2 | 235 | 68,9 % | 3,8 % | 27,2 % |
| pool omgekeerd | SellEvent (token kopen) | 12.554 | 100 % | 0 % | 0 % |
| pool omgekeerd | BuyEvent exact-in | 2.046 | 99,3 % | 0,1 % | 0,6 % |
| pool omgekeerd | BuyEvent exact-out | 400 | 100 % | 0 % | 0 % |

In de ontwikkeldata (vier chunks, 2,3–8,4 mln trades per venue per chunk) is "gelijk" 94,1–99,9 % per variant met ≥ 500 trades, "anders" ≤ 2,6 % en "geen delta" ≤ 5,0 %.

"Geen delta" betekent dat de tokens naar een account gingen dat niet van de signer is (pump vereist geen eigen ATA). De store kiest per variant het juiste veld: `quote_amount_in` voor exact-in, `user_quote_amount_in` voor exact-out en `user_quote_amount_out` voor sells. Dat is bevestigd door Lab Backtest en LAB research (`money.py` gebruikt de store-kolommen). De buyback-fee is 50 % van de protocol-fee, geen extra heffing.

### 2.2 Wie handelt, en via welk programma (**feit**)

| venue | trades | `user` = fee payer | SOL waar user ≠ payer |
|---|---|---|---|
| curve | 13.021 | 78,2 % | 268 |
| pool | 35.562 | 92,0 % | 1.259 |
| pool omgekeerd | 17.700 | 95,3 % | 5.659 |

- **Curve:** 2.282 van de ~2.840 trades met user ≠ payer zijn van één paar: de Mayhem-agent van pump.fun (`MAyhSmzX…`, 1 user, 1 payer). Die is in F7 al uitgesloten. Zonder hem is user ≠ payer ~5 %.
- **Omgekeerde pools:** de 5.659 SOL komen vooral van wash-bots met wisselende fee payers. Voorbeeld: wallet `5vATRuDx…` verkoopt in pool `81REJaDQ…` voor 9,65 SOL tokens en koopt ze in dezelfde transactie terug voor 9,697 SOL (tx `5GhxgdGY39j6D9gVqjzk2K9bQH3zNnwpa9v8sPZ5DgyYwaqy7qCUJkYBFkfX8kSvNb1e16TFF4BQPdJsPtJg782J`, fee payer `BcB37umf…`). In die pool deden 23 wallets 294 van zulke trades met 23 verschillende fee payers.
- **Route:** curve-trades lopen voor 27 % via `FLASHX8…` (1.082 verschillende wallets, elk betaalt zelf: een trading-app), 24 % direct via pump, 18 % Mayhem, 9 % `6Vo3245e…`, 5 % `term9YPb…`, 4 % GMGN, 2 % Jupiter. Pool-trades lopen voor 73 % direct via pump_amm.
- **Signers:** de kolom `signers` in de events is leeg. Wie meetekent zien we alleen via tx_ctx en failed_txs.

**Gevolg:** labels per `user` (zoals de store doet) zijn meestal juist. Fee-payer-clusters (whale stap 0) moeten relayers en wash-bots met rotatie herkennen: dat doet de service-registry van stap 0 al voor payers met ≥ 100 wallets.

### 2.3 Wash-volume: heen-en-terug in één transactie (**feit**)

| venue | SOL-volume (1.000 slots) | waarvan heen-en-terug in één tx | aandeel |
|---|---|---|---|
| curve | 4.398 | 0 | 0 % |
| pool (normaal) | 11.600 | 0,08 | 0,0 % |
| pool omgekeerd | 28.877 | 20.740 | **71,8 %** |

In de ontwikkeldata (tx_ctx-chunks van 1040 en 1045, elk 216.000 slots ≈ 16 uur) is het aandeel per chunk 85,8 %, 84,2 %, 76,9 % en 74,2 % (20,2 / 19,7 / 10,0 / 10,4 mln SOL omgekeerd volume per chunk); curve en gewone pools 0,0 %.

De vijf omgekeerde pools met het meeste volume (elk ~1.500–2.600 SOL in 6,7 minuten) hebben bijna evenveel koop- als verkoopvolume, met trades van 8 tot 13 SOL. De store kent een farmer-vlag (wallet met netto ≈ 0 over 24 uur), maar geen markering per transactie. **Elke feature die SOL-volume of instroom optelt, telt dit mee.** F7 gebruikt instroom (netto, dus wash valt grotendeels weg) en de verkoopgrootte (bruto, dus een wash-leg kan als "grote verkoop" tellen): zie §4.

### 2.4 Mislukte transacties (**feit**)

117.689 mislukte transacties tegen 65.669 gelande transacties met pump-events, in 1.000 slots.

| faalaandeel per fee payer | payers | geland | mislukt | fees mislukt (SOL) |
|---|---|---|---|---|
| nooit mislukt | 19.655 | 51.929 | 0 | 0 |
| < 50 % | 513 | 10.500 | 1.684 | 0,43 |
| 50–90 % | 653 | 2.350 | 5.105 | 1,80 |
| ≥ 90 % | 97 | 890 | 71.941 | 0,84 |
| alleen mislukt | 629 | 0 | 38.959 | 1,12 |

In de ontwikkeldata (vier chunks van 16 uur): 13,3–21,8 mln mislukt tegen 12,8–17,1 mln geland per chunk; ~400–540 payers met ≥ 90 % mislukking doen 85–89 % van de mislukkingen, en ~575.000–630.000 payers per chunk mislukken nooit. Fees van mislukte transacties in de smoke: 4,18 SOL, tegen 8,16 SOL voor gelande. 80.160 mislukkingen gebeurden in een ander top-level-programma (bots die pump via hun eigen programma aanroepen). **Gevolg:** onze slagingskans-aanname (`land_prob`) beschrijft een gewone handelaar, niet deze spam. Wie wil concurreren om dezelfde kansen (MEV, backruns), betaalt wel voor veel mislukkingen: dat meet MEV fase 0.

### 2.5 Wat de nieuwe transfer-tabellen laten zien (#239-smoke) (**feit**)

| | aantal | toelichting |
|---|---|---|
| token-overdrachten van pump-mints | 68.382 | 76,7 % in een trade-instructie, de rest erbuiten |
| buiten een trade, tussen verschillende eigenaren | 1.131 | 207 tokens, 456 afzenders, 550 ontvangers; 163 ontvangers handelen zelf in deze 6,7 minuten |
| grootste "fan-out" | 10 ontvangers in één tx | slot 455.328.800, tx 727: `GzDHuxPy…` geeft elk 76,8 mln tokens van `Tqj8yFma…pump` aan 10 wallets |
| saldo veranderd zonder fee payer te zijn (in trade-tx) | 516 van 898 | 342 ontvangen, 174 verzonden |
| token-accounts gesloten buiten trades | 7.928 | rent (~0,002 SOL per account) komt terug bij sluiten |
| burns buiten trades | 1.258 | 19 tokens |
| SOL-funding | 37.069 transfers, 52.374 SOL | 18.064 afzenders naar 23.534 ontvangers; maar 189 ontvangers handelen pump in deze periode. De grootste afzenders (734, 615 en 404 ontvangers) lijken exchanges. |

**Gevolg:** het insider-aandeel > 1 (38 % van de nieuwe pools) komt hoogstwaarschijnlijk van zulke fan-outs: tokens die een insider kocht, worden door andere wallets verkocht. De funding-tabel moet strenger gefilterd worden voordat hij voor 14 dagen wordt geëxtraheerd (bericht aan Lab Backtest).

## 3. Welke eerdere conclusies kunnen veranderen

| conclusie (register) | wat er kan veranderen | richting | label | wat we doen |
|---|---|---|---|---|
| E10 geld volgen: groeps-P&L (bijv. farmers −1,69 mln SOL) | verkopen van tokens die een wallet kreeg (niet kocht) tellen nu als winst; gekochte tokens die werden doorgegeven tellen als verlies | onbekend; per groep anders | reconstructie | herrekenen na de transfers-extractie van 1040/1045 |
| E07 V_ins en de O7-exit | het insider-aandeel mist doorgegeven tokens | het aandeel was te laag voor bundels en > 1 waar ontvangers verkopen | reconstructie | spec token-transfers Q1 (lat vooraf vastgelegd) |
| E04 whale stap 1: leiders' eigen rendement | zelfde transfer-gat | leiders' kolom kan verschuiven | reconstructie | het volgersresultaat (−13 tot −15 %) blijft staan: dat is onze eigen replay |
| E02/E06 F7 in omgekeerde pools (K3) | een deel van de triggers is een wash-leg (§4) | zie §4 | feit (triggers), verkennend (rendement) | nieuwe hypothese voor 1055–1063, K3 blijft ongemoeid |
| kostenmodel | rent voor token-accounts ontbreekt; mislukte pogingen van concurrenten niet | klein per trade (rent komt terug) | aanname | meten in de paper-run (journal) |

## 4. K3 en F7 in omgekeerde pools: zijn de triggers echte verkopen?

F7 koopt na een grote verkoop (≥ 3 SOL en ≥ 4 % van de diepte, diepte ≥ 150 SOL) als de instroom ervoor positief was. Het idee is dat een grote verkoper de prijs tijdelijk onder de waarde duwt en dat die terugveert. Als de "grote verkoop" een wash-leg is, staat de prijs aan het eind van dezelfde transactie alweer terug en is er niets om op terug te veren.

**Triggers (dev, dag 1–18, f7rev-run; 40.737 F7-triggers):** (**feit**)
- bij **27,0 %** (10.987) is de trigger-verkoop onderdeel van een heen-en-terug in dezelfde transactie;
- bij triggers waarvan de wallet een farmer is: 34,8 %, bij de andere 10,3 %;
- van de overige triggers koopt de verkoper bij 46,6 % dezelfde token binnen 50 slots (~13 s) terug;
- in de trigger-pools is 65,6 % van al het SOL-volume heen-en-terug in één transactie;
- het aandeel stijgt in de tijd: van 10–40 % per dag in dag 1–9 naar 32–61 % in dag 10–18.

**Rendement per groep (K3-instelling: tp4_T600, 0,5 SOL, q 0,5, basiskosten; verkennend, dev is verbruikt):**

Twee vlaggen, allebei te zien op het beslismoment: **rt_tx** = de trigger-transactie koopt de token zelf terug; **wash_1h** = ≥ 50 % van het SOL-volume van de pool in het uur ervoor was heen-en-terug in één transactie. 23,8 % van de F7-triggers heeft rt_tx, 20,2 % wash_1h; bij de willekeurige controle N2 heeft 9,8 % wash_1h.

| groep | d | dag 1–9: netto/trade [95 % CI] | n | dag 10–18: netto/trade [95 % CI] | n |
|---|---|---|---|---|---|
| F7 alle | 1 | +1,21 % [+0,29 … +1,77] | 13.035 | +1,31 % [+0,80 … +1,66] | 24.637 |
| F7 alle | 2 | +0,15 % [−0,77 … +0,70] | 12.071 | +0,55 % [+0,10 … +0,87] | 23.205 |
| F7 **trigger is wash-leg** (rt_tx) | 1 | **−1,64 %** [−2,34 … −1,04] | 2.190 | **−0,35 %** [−0,54 … −0,18] | 6.797 |
| F7 **trigger is wash-leg** (rt_tx) | 2 | **−1,98 %** [−3,00 … −1,08] | 2.169 | **−0,56 %** [−0,75 … −0,37] | 6.701 |
| F7 **echte verkoop** (geen rt_tx) | 1 | **+1,79 %** [+0,92 … +2,32] | 10.845 | **+1,94 %** [+1,39 … +2,24] | 17.840 |
| F7 **echte verkoop** (geen rt_tx) | 2 | +0,62 % [−0,35 … +1,17] | 9.902 | **+1,00 %** [+0,42 … +1,33] | 16.504 |
| F7 wash-pool (wash_1h) | 1 | −2,58 % [−3,92 … −1,66] | 2.014 | −0,55 % [−0,78 … −0,36] | 5.648 |
| F7 geen wash-pool | 1 | +1,90 % [+1,21 … +2,34] | 11.021 | +1,86 % [+1,34 … +2,16] | 18.989 |
| N2 (willekeurig) wash-pool | 1 | −6,92 % [−8,50 … −5,77] | 4.124 | −4,38 % [−5,05 … −3,81] | 8.224 |
| N2 (willekeurig) geen wash-pool | 1 | −7,76 % [−8,73 … −7,01] | 36.507 | −5,01 % [−5,80 … −3,99] | 76.353 |

Netto per gevulde trade na basiskosten, 95 %-interval uit een bootstrap over handelsdagen (10.000 trekkingen). Code: `lab/reports/2026-10-15-blindspots-v0/a4b_f7rev_triggers.py` en `a4c_f7rev_returns.py`.

**Lezing.**
- **Wat we zien (feit, op al gebruikte data):** F7-trades na een wash-trigger verliezen geld, in beide periodes en bij beide vertragingen, met een interval onder nul. Trades na een echte verkoop doen het duidelijk beter dan F7 als geheel.
- **Waarom dat klopt met het mechanisme:** bij een wash-leg is er geen gehaaste verkoper die de prijs omlaag duwt; de prijs staat aan het eind van de transactie alweer terug, dus er is niets om op terug te veren. We betalen dan alleen kosten.
- **Controle:** de willekeurige instap N2 in dezelfde pools reageert nauwelijks op de wash-vlag: wash-pools zijn niet in het algemeen slechter. Het effect zit in de trigger.
- **Waarom dit geen bewijs is (verkennend):**
  - dev is verbruikt, en F7 zelf is op deze data gekozen;
  - de vlag is bedacht na het zien van de wash-aandelen (wel vóór het zien van de rendementen; de lezing hierboven stond in het concept voordat de rendementen er waren);
  - het wash-aandeel groeit in de tijd;
  - de hold-out (K3 +0,02 %) is niet gesplitst.
- **Volgende toets:**
  - (1) als *diagnose* (geen afstemming): het wash-aandeel en de split in de hold-out 1047–1051;
  - (2) als *bevestiging*: een pre-registratie op de schone epochs 1055–1063 met K3 met en zonder rt_tx-filter, de lat vooraf vastgelegd en DSR/PBO erbij.
- **K3 zelf blijft ongemoeid.** De paper-run legt de trigger-transactie al vast, dus de split kan achteraf uit de paper-data worden berekend zonder K3 te veranderen.

## 5. Getoetste en verworpen hypothesen (deze ronde)

| hypothese | uitkomst | label |
|---|---|---|
| De store pakt bij omgekeerde pools het verkeerde tokenveld | **verworpen**: 99,3–100 % gelijk aan de chain | feit |
| De buyback-fee is een extra kost die onze fills missen | **verworpen**: 50 % van de protocol-fee, exact bij alle 53.262 events | feit |
| F7-triggers in omgekeerde pools zijn echte verkopen | **verworpen**: 27 % wash in dezelfde tx, plus 47 % van de rest koopt binnen 13 s terug | feit |
| Mislukte transacties maken onze kosten veel hoger dan aangenomen | **niet bevestigd**: 91 % van de payers mislukt nooit; de mislukkingen zitten bij spam- en MEV-bots | feit |
| Een wash-trigger (rt_tx) is voor F7 een verliesgevende trigger | **ondersteund, verkennend**: −0,35 tot −1,98 % tegen +0,62 tot +1,94 % zonder; toets op 1055–1063 | feit (dev, verbruikt)

## 6. Shortlist (nog niets bewezen)

| # | kandidaat | mechanisme en tegenpartij | data | resultaat tot nu toe | onzekerheid | live haalbaar | volgende falsificatietoets |
|---|---|---|---|---|---|---|---|
| 1 | **Risicofilter "wash-trigger"** voor F7 in omgekeerde pools | F7 verdient alleen als de verkoper de prijs echt omlaag duwde (een gehaaste verkoper is de tegenpartij). Bij een wash-leg is er geen tegenpartij en geen terugvering. | trigger-transactie zelf (koopt hij terug in dezelfde tx?), te zien op het beslismoment | dev (verkennend): zonder wash-triggers +1,94 % (d=1) / +1,00 % (d=2) per trade in dag 10–18, wash-triggers −0,35 % / −0,56 %; capaciteit −24 % trades | dev is verbruikt; regime verandert met Alpenglow | ja: de trigger-transactie zit in dezelfde stream | pre-registratie op 1055–1063 (schoon): K3 met en zonder filter, lat vooraf |
| 2 | **Insider-aandeel uit transfers** (veto/exit voor M1, O7) | bundels verdelen voorraad over wallets die in de pool verkopen; wie koopt, is hun tegenpartij | #239 token_transfers | nog niet gemeten | volume en filter van de nieuwe tabellen | deels: transfers zitten in de live stream, maar de geschiedenis van vóór de start ontbreekt | spec Q1 (lat vooraf) op dev, daarna 1055–1063 |
| 3 | **Kostenmodel: rent en mislukte pogingen** | uitvoering, geen strategie | tx_ctx, token_transfers | rent ~0,002 SOL per nieuw account, terug bij sluiten | klein | ja | meten in het paper-journal |

## 7. Volgende stappen

1. De audit op de vier volledige tx_ctx-chunks van 1040 en 1045 (NAS, in de wachtrij).
2. Transfers-extractie (#239) op 1040/1045 na de volumemeting; dan Q1/Q2 en een herrekening van "geld volgen".
3. **1055–1063:** het plan met omvang en waarde (download ~380 GB per epoch, zonder het archief te bewaren), alleen voor vooraf geregistreerde toetsen. Eerst de eigenaar informeren.
4. Een wash-vlag per transactie in de store (`rt_tx`), zodat elke feature ermee kan filteren. Dat is een store-wijziging, dus Lab Backtest, met een regressietest.
