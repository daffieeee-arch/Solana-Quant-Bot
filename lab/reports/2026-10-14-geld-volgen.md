# Geld volgen (spec A): P&L per groep handelaren op dev, uitslag (2026-10-14)

| | |
|---|---|
| spec | `data-old-faithful-one/lab/research/week3-specs-money-2026-10-12.md` (sha256 33da3725…), vastgelegd op #167 vóór enige uitkomst (2026-10-12T20:08:23Z) |
| code | `lab/wallets/money.py`, `money_report.py` (#209, #214), `money_checks.py` (#229); run op commit **8b90842** |
| run | NAS, 2026-10-13 00:35–04:10 UTC: opbouw 2 u 22 min (8 wallet-buckets, 12 GB, 200 GB spill), rapport 1 u 13 min; controles (`money_checks.py`, #229) NAS 2026-10-16 01:21 UTC; uitvoer `data-old-faithful-one/lab/wallets/money-v1/` (wallet-day-0…7, marks, lp_legs, payers, creators, venue_flows, run-money.json, `money.md`, `money-checks.md`) |
| data | dev2, slots 446.256.000–452.304.000 (2026-09-11 t/m 10-01); 6.405.696 marks (token × dag); de hold-out is niet gebruikt, forward-data komt niet voor |

Alleen beschrijvend. Labels uit gedrag op **dag 1–9** (late wallets: eerste twee volle dagen), **bindend is de meting op dag 10–18** (2026-09-20 t/m 09-30). Netto P&L per wallet-dag = kas − netwerkfees + Δ gemarkeerde inventaris (laatste prijs van de dag, gecapt op de echte SOL in de venue). Dag-bootstrap 10.000×; 100 placebo-trekkingen met teruglegging per groep. Lat (§5): gemiddelde > 0, Holm over de 7 groepen bij α = 0,05, én boven ≥ 95 % van de placebo's.

## 1. Uitslag op de bindende dagen: geen groep slaagt

| groep | wallets | wallet-dagen | Σ P&L SOL | **gem. per wallet-dag SOL** | 95 %-CI (dagen) | p | Holm | per ingezette SOL | mediaan wallet | wallets > 0 | boven placebo's | **oordeel** |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| creator (+ fee-payer-link) | 33.907 | 245.260 | +136 | **+0,0006** | −0,56 … +0,46 | 0,98 | – | +0,01 % | −0,25 | 19 % | 100/100 | nee |
| sniper | 2.721 | 20.384 | −267.468 | **−13,12** | −18,40 … −7,59 | 0,000 | pass | −29,6 % | −0,0007 | 45 % | 0/100 | nee |
| bundler | 1.901 | 17.240 | −5.340 | **−0,31** | −1,73 … +1,25 | 0,68 | – | −0,11 % | −0,0003 | 40 % | 97/100 | nee |
| farmer | 10.226 | 92.641 | −1.693.606 | **−18,28** | −20,52 … −16,12 | 0,000 | pass | −2,11 % | −2,05 | 11 % | 0/100 | nee |
| bot | 2.665 | 22.984 | +6.858 | **+0,30** | +0,06 … +0,54 | 0,016 | **fail** | +0,60 % | −0,10 | 20 % | 98/100 | nee |
| whale | 250 | 2.325 | −30.223 | **−13,00** | −38,17 … +16,60 | 0,34 | – | −17,7 % | −7,17 | 34 % | 4/100 | nee |
| retail | 2.605.692 | 20.258.185 | −740.122 | **−0,04** | −0,12 … +0,05 | 0,37 | – | −1,88 % | +0,0000 | 14 % | 100/100 (*) | nee |

(*) De retail-placebo is per constructie niet informatief: retail is 98 % van alle wallets (2.605.692 van 2.657.362), de complementgroep van de zes andere labels telt 51.670 wallets en vult 150 van de 466 retail-strata; 695.160 retail-leden hebben een stratum-kandidaat, 1.910.532 niet (`money-checks.md`). De retail-rij staat er voor de volledigheid, het oordeel steunt op de CI.

Wat eruit springt:
- **Bots** zijn de enige groep met een positief gemiddelde en een CI boven nul (+0,30 SOL per wallet-dag, p = 0,016, boven 98 van de 100 placebo's), maar de Holm-correctie over zeven groepen vraagt p ≤ 0,0071 voor de eerste toets en dat haalt 0,016 niet. De mediane bot-wallet verliest (−0,10 SOL) en 20 % van de bots is positief: het gemiddelde komt van weinig wallets (de winnende bots samen +17.524 SOL, de slechtste 10 −6.951; §2a).
- **Creators** komen op nul uit: wat ze op de ene plek winnen, verliezen ze op de andere; 19 % positief.
- **Snipers** en **farmers** verliezen fors, op elke dag, met CI's ruim onder nul, en dat verlies is **gerealiseerd** (kas), geen waardering van voorraad (§2a).
- **Whales** (250 wallets): −13 SOL per wallet-dag, maar met een CI van −38 tot +17; te weinig wallets.

## 2. De twee controles vóór de interpretatie

### 2a. Grootte van de verliezen: gerealiseerd of gemarkeerd?

De vraag vooraf was of de grote verliezen echt (kas) zijn of een waardering van onverkochte voorraad tegen de laatste prijs. Per groep op dag 10–18 (SOL, uit `money-checks.md`):

| groep | wallets | kas | netwerkfees | **gerealiseerd** (kas − fees) | Δ gemarkeerd | netto P&L | gekocht | verkocht | voorraad eind (gemarkeerd) |
|---|---|---|---|---|---|---|---|---|---|
| farmer | 10.226 | −1.754.391 | 3.522 | **−1.757.912** | +64.307 | −1.693.606 | 80.225.915 | 78.471.524 | 115.658 |
| retail | 2.605.692 | −433.595 | 31.001 | −464.597 | −275.525 | −740.122 | 39.455.173 | 39.021.578 | 1.361.356 |
| sniper | 2.721 | −226.902 | 5.271 | **−232.173** | −35.295 | −267.468 | 902.430 | 675.528 | 33.108 |
| whale | 250 | −31.381 | 480 | −31.861 | +1.638 | −30.223 | 170.350 | 138.969 | 24.326 |
| bundler | 1.901 | −16.795 | 987 | −17.783 | +12.443 | −5.340 | 4.936.226 | 4.919.431 | 30.751 |
| creator | 33.907 | −15.263 | 3.214 | −18.477 | +18.613 | +136 | 1.805.211 | 1.789.948 | 126.658 |
| bot | 2.665 | +9.935 | 2.429 | **+7.507** | −649 | +6.858 | 1.147.311 | 1.157.247 | 1.343 |

- **Farmers:** het verlies is kas. Ze kopen 80,2 mln SOL en verkopen 78,5 mln; het verschil van 1,76 mln SOL (2,2 % van het gekochte volume) is betaald, elke bindende dag tussen −126 k en −220 k SOL gerealiseerd, en de gemarkeerde voorraad is maar 116 k SOL. Dat past bij de wash-vondst van de blinde-vlekken-audit (74–86 % van het reversed-volume is heen-en-terug in één transactie): het is volume tegen betaling van fees en prijsimpact.
- **Snipers:** ook kas (−232 k), met een kleine voorraad die verder zakt (−35 k).
- **Retail** is de enige groep waar de waardering telt: −465 k gerealiseerd en nog eens −276 k gemarkeerd, met 1,36 mln SOL aan voorraad op de laatste dag die tegen de laatste prijs staat. Dat deel is een reconstructie (laatste prijs, gecapt op de echte SOL in de venue), geen stroom.
- **Bots** realiseren +7,5 k SOL; hun voorraad is verwaarloosbaar (1,3 k). Het bot-gemiddelde is dus echt, maar klein en niet significant na Holm.
- **Creators** komen op nul uit door +18,6 k gemarkeerde voorraad tegen −18,5 k kas.

Concentratie van het verlies (dag 10–18): bij farmers dragen de slechtste 10 wallets −479 k SOL (28 % van het groepsverlies) en de slechtste 100 −767 k (45 %); de mediane farmer verliest 2,1 SOL. Bij snipers zit het hele verlies in de slechtste 10 (−286 k op −267 k totaal; de rest wint 111 k). Groepsgemiddelden zijn dus geen uitspraak over de typische wallet, wel over waar het geld heen gaat.

### 2b. Sluit de boekhouding?

De behoudswet die de data toelaten is op kas: elke SOL die een handelaar betaalt gaat de venue in, elke ontvangen SOL komt eruit, dus Σ kas + venue-fees in SOL + Δ SOL in de venues − SOL van buiten de trades (LP-deposits − withdrawals) = 0 als elke leg is meegenomen. Over de hele periode:

| venue | legs | bruto SOL | Σ kas | venue-fees | Δ SOL in venues | extern (LP) | **residu** | residu / bruto |
|---|---|---|---|---|---|---|---|---|
| curve | 62.894.663 | 20,2 mln | −1.571.754 | 239.054 | +1.311.134 | 0 | **−21.566** | −0,11 % |
| pool | 413.161.957 | 574,8 mln | −3.810.672 | 469.668 | −8.464.940 | −12.078.954 | **+273.010** | +0,05 % |

Beide residuen liggen onder de 1 % van §6; de boekhouding sluit. Gerealiseerd over de periode (Σ kas − netwerkfees, alle wallets): **−5.475.941 SOL**; gemarkeerde inventaris op de laatste dag: 2.003.521 SOL; netwerkfees (één keer per transactie) 93.515 SOL. De sluitpost is de LP-kant: handelaren brachten netto 5,4 mln SOL in de venues, de venues verloren 7,2 mln SOL aan diepte, en LP's namen netto **12,1 mln SOL** uit pools (vrijwel alleen reversed pools, zie §4). Marks zijn een waardering en geen stroom; §6 van de spec schreef de identiteit met de marks erin, dat kan niet sluiten en is hier gesplitst.

### 2c. Beloningen buiten de SOL-boekhouding: cashback en PUMP-incentives (verkennend, reconstructie)

Hypothese van de coördinator na §2a: het farmer-verlies is de prijs van volume-farming en de beloning komt buiten onze SOL-boekhouding binnen (pump.fun-cashback, PUMP-tokenincentives via de UserVolumeAccumulator). Gemeten op dev2 uit `events/v1` (slots < 452.304.000; de chunks van de hold-out zijn weggelaten), beschrijvend:

| | |
|---|---|
| cashback geclaimd in SOL (`pump/ClaimCashbackEvent` + `pump_amm/ClaimCashbackEvent`), hele dev-periode | 904.239 claims, 327.826 wallets, **41.305 SOL**; op dag 10–18 19.236 SOL |
| waarvan naar farmer-achtige wallets (≥ 20 pool-legs, ≥ 50 % farmer-legs, ≥ 5 posities op dag 1–9; 19.199 wallets, superset van het farmer-label) | 351 wallets, 1.307 SOL in de hele periode, **633 SOL op dag 10–18** |
| gerealiseerd verlies van diezelfde farmer-achtige wallets op dag 10–18 | **−1.695.953 SOL** (9.227 actieve wallets; gekocht 79,6 mln) |
| cashback / verlies | **0,04 %** van het groepsverlies; per wallet met cashback mediaan 0,6 % van het eigen verlies; 114 van de 9.227 wallets claimen überhaupt |
| grootste claimer in dev | `F7p3dFrj…`, 20.768 SOL (de helft van alle cashback), geen farmer-achtige wallet; de sniper `ARu4n5mF…` claimt 119 SOL |
| PUMP-tokenincentives: `CloseUserVolumeAccumulatorEvent` (pump + pump_amm), dev | 16.697.190 closes van 4.862.653 wallets, **total_claimed_tokens = 0 en total_unclaimed_tokens = 0 in elke rij**, current_sol_volume 0; `ClaimTokenIncentivesEvent` zit niet in de extractie |

Uitkomst: **cashback verklaart het farmer-verlies niet** (0,04 %). Over PUMP-incentives zegt deze data niets positiefs: de accumulator-closes die wél zijn geëxtraheerd melden nul geclaimde tokens voor 4,9 mln wallets, en het claim-event zelf ontbreekt in `events/v1`; het blijft **onbekend** of farmers elders PUMP ontvangen (dat zou een aparte extractie en een PUMP-prijsaanname vragen; er is geen PUMP/SOL-pool in dev2). De lezing van §1 blijft dus staan: farmers betalen 2,2 % van hun volume en krijgen in de SOL-boekhouding niets terug. Labels: cashback en closes zijn **feit** uit de chaindata; de farmer-achtige set is een **reconstructie** (feature-regel zonder label-voorrang).

## 3. Controletabellen (§3): dag 1–9 en per regime

| groep | dag 1–9 (in-sample: het label-venster) | 316 ms (dagen < 09-18) | 267 ms (dagen ≥ 09-18) |
|---|---|---|---|
| creator | **+0,95** (+0,33 … +1,88), pass | +1,22 (+0,48 … +2,48) | +0,02 (−0,42 … +0,41) |
| sniper | −76,8 (−100,9 … −58,3) | −85,8 | −19,9 (−28,8 … −12,5) |
| bundler | −8,4 (−13,4 … −4,0) | −10,7 | −0,8 (−2,4 … +0,8) |
| farmer | −19,6 (−23,1 … −16,0) | −21,1 | −17,2 (−19,1 … −15,5) |
| bot | −1,8 (−3,2 … −0,6) | −2,7 | +0,13 (−0,14 … +0,40) |
| whale | −40,7 (−64,1 … −24,4) | −46,5 | −13,2 (−33,8 … +10,8) |
| retail | **+0,14** (+0,09 … +0,20) | +0,18 (+0,15 … +0,24) | −0,06 (−0,12 … +0,01) |

Dag 1–9 is in-sample (de labels komen uit diezelfde dagen) en laat precies het selectie-effect zien waar de spec voor waarschuwde: creators en retail "winnen" daar, bots verliezen; op de bindende dagen is dat omgedraaid of weg. Het 316-ms-regime valt grotendeels samen met dag 1–9; het 267-ms-regime bevat de bindende dagen plus drie label-dagen.

## 4. LP-drain na een F7-trigger in reversed pools (beschrijvend, voor de K3-paper)

Drain = de eerste LP-withdrawal op de pool van de trigger, op of na de trigger, die ≥ 90 % van de SOL weghaalt.

| set | triggers | pools | ooit geleegd | ≤ 1 min | ≤ 5 min | ≤ 15 min | ≤ 1 u | ≤ 6 u | mediaan | gem. | p90 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| F7-triggers, alle | 40.737 | 3.341 | **100 %** | 3 % | 13 % | 33 % | **72 %** | 100 % | 28 min | 51 | 130 |
| F7-triggers, 320 ms | 7.834 | 852 | 100 % | 4 % | 16 % | 40 % | 82 % | 100 % | 21 | 38 | 91 |
| F7-triggers, 270 ms | 32.903 | 2.490 | 100 % | 3 % | 13 % | 31 % | 70 % | 99 % | 30 | 54 | 135 |
| F7-pools (eerste trigger per pool) | 3.341 | 3.341 | 100 % | 6 % | 21 % | 43 % | 82 % | 100 % | 19 | 38 | 88 |
| pools van de N2-tweelingen | 158.769 | 20.116 | 100 % | 17 % | 39 % | 60 % | 86 % | 100 % | 9 | 29 | 76 |

**Elke reversed pool waarin F7 ooit triggerde is binnen de dev-periode door zijn LP leeggetrokken**, 72 % binnen het uur na de trigger en de helft binnen 28 minuten; gerekend vanaf de eerste trigger per pool is 82 % binnen het uur weg. De willekeurige N2-pools gaan nog sneller (mediaan 9 min). Voor de K3-paper betekent dit: een positie in een reversed pool heeft een harde horizon van minuten, de time-stop van K3 (T600 = 10 min) ligt daar al binnen, en elke exit-regel die langer wacht dan de drain verkoopt tegen een lege pool. De LP-withdrawals zijn ook de sluitpost van §2b: 12,1 mln SOL in 21 dagen.

## 5. Conclusie

- Geen groep haalt de vooraf vastgelegde lat. Niemand verdient structureel in deze markt op dag 10–18: creators eindigen op nul, bots net boven nul (gerealiseerd, maar niet na Holm), alle andere groepen verliezen, en bij farmers en snipers is dat verlies kas, geen waardering (§2a).
- Wat de spec vroeg ("waarmee: snelheid, voorkennis of timing") krijgt daarmee een negatief antwoord: snelheid (snipers, bots) levert geen structurele winst op, voorkennis (creators) levert per saldo nul op, en de farmers betalen voor hun volume.
- Het geld gaat niet naar een groep handelaren maar naar de LP-kant van reversed pools (12,1 mln SOL netto withdrawals in 21 dagen) en naar fees (0,8 mln SOL). Cashback geeft farmers 0,04 % terug; PUMP-incentives zijn in deze data niet zichtbaar (§2c).
- Wat dit niet is: geen bewijs over individuele wallets (labels zijn groepsgemiddelden uit gedrag), geen uitspraak over PUMP-beloningen buiten de chain-events die we extraheren, en geen uitspraak over de markt na Alpenglow (epoch 1064, ~21 oktober), die als nieuw regime apart gemeten moet worden. Groeps-P&L mist bovendien tokens die per transfer van wallet wisselen (E15); de herrekening met de transfers-extractie (spec v3) volgt.

Review: de coördinator. Daarna beslist de eigenaar.
