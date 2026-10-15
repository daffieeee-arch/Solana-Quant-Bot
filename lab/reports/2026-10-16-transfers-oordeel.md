# Token-overboekingen en wallet-funding: oordeel Q1–Q3 tegen de lat van spec v3 (LAB research, 2026-10-16)

| | |
|---|---|
| spec | `data-old-faithful-one/lab/research/spec-token-transfers-2026-10-15.md` v3 (sha256 611a6327…), §1 (vragen en lat) en §4 (validatie) |
| extractor | transfers-v1 (Lab Backtest, 0d915ac+), chunks 1040 en 1045, extractor-sha 5092ffa4…; `token_deltas`, `token_transfers`, `sol_funding` (E19) |
| code | `lab/wallets/transfers.py` (#266 → #270 → #272 → #278 → #279; daarna #281 en #282, zie §1), `transfers_diag.py`; NAS-jobs 72/76/79/815 (`nas_transfers.sh`, DIAG=1) |
| uitvoer | `data-old-faithful-one/lab/wallets/transfers-v1/epoch-{1040,1045}/transfers.md`, `diag/diag.md`, `diag/txres.parquet`, `q1_triggers.parquet`, `run-transfers.json` (job 815, commit f70e93c); job 79 in `superseded-98bc5ea/` |
| data | dev2, epochs 1040 (2026-09-22/23) en 1045 (09-29/30): beide in de bindende dagen 10–18; mints aangemaakt in het venster (41.427 / 59.648), zodat elke ledger compleet is vanaf de create; hold-out en clean-v1 niet gelezen |

Labels: **feit** (direct uit de chaindata), **reconstructie** (afgeleid, met bekende foutmarge), **modelaanname**, **onbekend**. Eén regime (270 ms) in beide epochs; na Alpenglow apart.

De cijfers hieronder zijn van job 815. Twee fixes die uit de diagnose van die run volgen (#281 en #282, §1) zijn gemerged en draaien als job 816 opnieuw; dat getal komt in het register bij E26 als correctie. De richting van de conclusies hangt er niet van af (§1 en §2 zeggen waarom).

## 0. Oordeel in het kort

| onderdeel | lat (spec v3) | 1040 | 1045 | oordeel |
|---|---|---|---|---|
| 4.1 behoud per mint-dag | ≥ 99,9 % | 99,8 % (68.037 / 68.159) | 99,8 % (114.073 / 114.303) | **nét niet gehaald**; de rest is voor 100 % één patroon (residual = de leg zelf), fix #281, hertelling job 816 |
| 4.2 reproductie oude share | exact op niet-onbepaalde triggers | 540 / 669 exact (80,7 %) | 480 / 574 (83,6 %) | **niet exact**; oorzaak gevonden (insider-set van de engine ≠ spec-tekst), fix #282, hertelling job 816 |
| Q1 (a) onbepaald aandeel | < 1 % (alleen insiders_unknown blijft) | 0 van 770 | 0 van 633 | **gehaald** |
| Q1 (b) verkoop na share > 1 verklaard door insider-transfers | ≥ 80 % van de triggers | 17 / 71 = **23,9 %** | 18 / 47 = **38,3 %** | **niet gehaald** (job 79 met de bredere insider-set: 25,0 % / 48,4 %; de set van 816 ligt daartussen) |
| Q1 (c) V_ins/O7 herrekend met share_c | herrekening, geen tuning | – | – | Lab Backtest, na job 816; §2 geeft de verwachte omvang |
| Q2 funder-koppeling | bundler en sniper ≥ 30 %, retail ≤ 5 % (hubs uitgesloten) | sniper 8,3 %, retail 2,4 %, bundler onbekend | sniper 4,5 %, retail 1,6 %, bundler onbekend | **niet gehaald** voor sniper; retail wel; bundler onbekend |
| Q3 verkoopwallets van bundles | geen lat (beschrijvend) | 16,6 % transfer-gevoed | 12,3 % | token-eigenschap voor de K3-beschrijving |

## 1. Check 4.1 en 4.2 (validatie vóór gebruik)

**4.1 Behoud per mint-dag (feit: Σ voorraden per owner incl. vaults = gemint − verbrand).** Job 72 (#266): 95,1 % / 96,5 %; job 76 (#270, ledger-fixes: boost-vaultzijde, same-tx-doorstuur, vaults benoemd door de ledger): 99,5 %; job 79 (#272): 99,5 % / 99,5 %, met in de diagnose 82 % van de rest in pool-trades waarvan de netto leg precies 2× het bedrag was: gerouteerde trades (router-account ontvangt de pool-output in_trade en stuurt door naar de trader; of trader → tijdelijk account → vault), door de flows-regel van #270 dubbel geteld (#278, `hops`). Job 815 (#278 + #279): **68.037 / 68.159 (99,8 %) en 114.073 / 114.303 (99,8 %)**; Σ|residual| over de falende mints op 1040 van 2,74e15 naar 3,7e14 (×7,4). Diagnose (`txres.parquet`): **álle 3.324 resp. 7.328 resttransacties hebben residual = netto leg, exact** (ook de 4 + 14 in behoudende mints; in job 79 was het 2× de leg). De kept holder-rijen plus de vaultzijde balanceren al tegen gemint − verbrand; de owner-zijde van de leg is de surplus. De voorbeelden: een custody-wallet (HG1Z…, BvSX…) schuift de tokens naar een per-trade signer (de trader in de store) en betaalt 0,85 % fee aan 8psN… vanaf hetzelfde account; bij een buy landt de pool-output op een andere owner dan de signer, met een top-up uit een derde account in dezelfde tx; een tijdelijk account dat de output splitst. Geen kept rij heeft daar exact het legbedrag, dus de bedrag-regel van #272 greep niet. Fix **#281**: de ledger bouwt eerst de chain-rijen, en voegt de owner-zijde van een leg alleen toe in transacties waar die een gat laten (de trader-rij gedropt als gelijk aan de leg; de create-tx met de dev buy). Verwachting job 816: ≥ 99,9 % op beide epochs; staat het binnen enkele tienden van de lat, dan is 4.1 daarmee afgesloten (afspraak coördinator 2026-10-16).

**4.2 Reproductie van het oude getal (reconstructie).** Job 79: 409/669 (61 %) en 366/574 (64 %) exact; van de afwijkers had 258/260 resp. 205/208 een hógere legs-only share (mediaan +0,14; typisch 1,00 tegen 0,79). #279 beperkte de insiders tot creator + curve-kopers ≤ 10 s, letterlijk de spec-tekst. Job 815: **540 / 669 (80,7 %) en 480 / 574 (83,6 %) exact**, en de richting is omgekeerd: bij 123 van 129 en 86 van 94 afwijkers ligt de legs-only share nu ónder de oude (mediaan −0,05 / −0,12; typisch 0,86 tegen 1,00); 115 resp. 79 triggers die in job 79 exact waren zijn dat nu niet, 246 resp. 193 andersom. Oorzaak (feit, code gelezen): `overlay.Insiders` van de engine telt de creator plus **elke koper binnen 10 s op de curve én op een normal-pool**, en zijn circulatie bevat geen legs van reversed pools. De spec-tekst ("creator + curve-kopers ≤ create + 10 s") is dus smaller dan wat de engine en de O7-veto werkelijk gebruiken; voor de reproductie en voor Q1 (c) geldt de engine-set. Van de afwijkende mints heeft 84 van 90 (1040) en 59 van 69 (1045) pool-koop binnen 37 slots na de create (tokens die in de create-tx volmaken en migreren); de 6 resp. 10 overige: onbekend tot job 816 (vermoedelijk de reversed-pool-legs in de circulatie). Fix **#282**: insiders = creator + kopers van curve- en normal-pool-legs binnen het venster (plus de chain-owners daarachter), legs-only ledger = diezelfde legs. **Bevinding voor de spec:** de tekst van Q1 en de engine verschillen; de engine-set is de bredere en de geldende.

## 2. Q1: insideraandeel share_c (reconstructie: ledger uit legs + chain-delta's)

share_c(t) = insider-voorraad / circulatie op t, uit voorraden per owner (vaults uitgesloten); per constructie in [0, 1]. Triggers: M1x (M1P/M1E en hun N2's, d = 1, 0,5 SOL), mint aangemaakt in het venster. De insider-set hier is die van job 815 (creator + curve-kopers ≤ 10 s, #279); job 816 gebruikt de engine-set.

| | 1040 | 1045 |
|---|---|---|
| triggers / met oude share | 770 / 669 | 633 / 574 |
| share_c gedefinieerd (lat a) | 770 (100 %; onbepaald 0) | 633 (100 %; onbepaald 0) |
| share_c Q20 / Q50 / Q80 / Q90 | 0,000 / 0,004 / 0,530 / 0,853 | 0,000 / 0,010 / 0,410 / 0,814 |
| share_c > 0,90 (de O7-drempel, niet verschoven) | 28 (3,6 %) | 12 (1,9 %) |
| oude share > 0,90 | 148 van 669 (22,1 %) | 117 van 574 (20,4 %) |
| share_c op de triggers met oude share > 0,90: mediaan / nog > 0,90 | 0,151 / 28 | 0,239 / 10 |
| mediaan share_c − oude share; \|verschil\| > 0,10 | −0,044; 309 van 669 (46 %) | −0,050; 251 van 574 (44 %) |
| legs-only share > 1 (de "> 1"-triggers van lat b) | 84 | 55 |
| share_c op die triggers: mediaan / Q80 | 0,000 / 0,003 | 0,000 / 0,002 |
| mediaan houders op t / insiders per mint | 355 / 6 | 403 / 7 |

**Wat dit zegt (reconstructie).** De oude share (legs-only) zet de O7-veto op ruim een vijfde van de triggers; per chain houden de insiders op t meestal veel minder dan de legs suggereren: de veto-set krimpt tot 2–4 % van de triggers, en de triggers die overblijven zijn vrijwel alle ook in de oude set (28 van 28, 10 van 12). Dat is de omvang die Q1 (c) te verwerken krijgt: de meeste V_ins-vetos van O7 vervallen als share_c de maat is; of de uitkomst van O7 daardoor verandert is de herrekening van Lab Backtest, geen conclusie hier. Bij alle "> 1"-triggers is share_c ≈ 0: de insiders hebben op t per chain niets meer; hun tokens zijn via transfers vertrokken, en de legs-only share > 1 was het spiegelbeeld daarvan (niet-insiders met negatieve netto legs, omdat ze verkochten wat ze per transfer kregen).

**Lat (b): wie verkoopt in het uur na t (feit op tokens, reconstructie op hops).** Van de "> 1"-triggers met niet-insider-poolverkoop in de 60 min na t: 1040 **71, waarvan 17 (23,9 %)** met minstens de helft van die verkoop (tokens) terug te voeren op een transfer vanaf een insider binnen 2 hops; mediaan verklaard 0,0 %, 21 triggers met 0 %; verkocht 48,9e15 tokens, waarvan 8,87e15 (18,1 %) vanaf een insider in 1 hop en niets extra in de tweede hop; gemiddeld 310 verkopers per trigger. 1045 **47, waarvan 18 (38,3 %)**; mediaan 17,6 %, 17 triggers met 0 %; verkocht 23,0e15, 1 hop 7,56e15 (32,9 %), 2 hops +0,02 pt; 458 verkopers per trigger. Job 79 (bredere set: alle kopers ≤ 10 s plus elke chain-koper in die tx'en) gaf 21/84 = 25,0 % en 30/62 = 48,4 %; de engine-set van job 816 ligt tussen beide. **Oordeel: lat (b) niet gehaald**, in alle versies ruim onder 80 %. De insiders verklaren dus niet de verkoopgolf na een trigger: op tokens komt 18–33 % van de vroege niet-insider-verkoop binnen twee hops van een insider, en een tweede hop voegt niets toe. Waar de rest vandaan komt — meer hops, kopen via een custody- of programma-account dat de store niet als trader ziet, of verkoop door de insiders zelf vóór t — is met deze meting niet vast te stellen: **onbekend**.

## 3. Q2: funder-graaf (reconstructie: money-v1-labels dag 1–9, `sol_funding` eerste SOL-ontvangst)

Geboorten (eerste SOL-ontvangst ≥ 0,005 SOL) en funders; hub = funder met ≥ 50 geboorten op een dag of ≥ 200 in 7 dagen (addendum v3 §5). Maat: het aandeel wallets per label waarvan de eerste funder binnen 7 dagen ≥ 5 andere wallets met hetzelfde label opzette.

| | 1040 | 1045 |
|---|---|---|
| geboorten / funders | 2.313.057 / 1.045.041 | 2.433.134 / 1.070.889 |
| onder 0,005 SOL (buiten Q2) | 496.560 (21,5 %) | 686.136 (28,2 %) |
| mediane geboorte | 0,0169 SOL | 0,0165 SOL |
| hubs | 1.368 | 1.163 |

| label | 1040: wallets (geen hub) | gekoppeld | 1045: wallets | gekoppeld | lat |
|---|---|---|---|---|---|
| sniper | 168 | **8,3 %** | 177 | **4,5 %** | ≥ 30 %: **niet gehaald** |
| bot | 79 | 0,0 % | 190 | 0,0 % | – |
| farmer | 2.275 | 5,5 % | 2.685 | 6,0 % | – |
| creator | 5.853 | 0,2 % | 10.629 | 0,0 % | – |
| whale | 29 | 0,0 % | 28 | 25,0 % (7 van 28) | – |
| retail | 240.306 | **2,4 %** | 288.686 | **1,6 %** | ≤ 5 %: gehaald |
| onbekend | 831.194 | 20,6 % | 836.507 | 26,4 % | – |
| bundler | – | – | – | – | label bestaat niet in money-v1: **onbekend** |

Met hubs erbij stijgt de koppeling overal (retail 66,7 % / 52,9 %, farmer 49,4 % / 48,5 %): de hubs zijn beurzen en drip-faucets (top-20 in transfers.md: 5tzFkiKscXHK… beurs-achtig met 93 k SOL; VLX…-adressen met 0,0018 SOL per geboorte en 100 % onbekend), geen operators van clusters. **Oordeel Q2 (reconstructie):** de verwachting "bundler en sniper ≥ 30 %" wordt niet gehaald; de eerste funder is géén clusterkenmerk voor snipers; de fee-payer-link blijft de enige koppeling (spec v3 §1). Opvallend en beschrijvend: de grootste koppeling zit bij "onbekend" (20–26 %), de grote restgroep zonder money-v1-label; een labeling van die groep is een eigen vraag. Q2 hangt niet van de ledger af; de kleine verschillen met job 79 (tientallen op miljoenen) komen uit de hop- en insider-regels in de geboortetelling.

## 4. Q3: verkoopwallets van bundles (beschrijvend, reconstructie)

Per pool aangemaakt in het venster: het deel van de niet-insider-poolverkopen (tokens) in het eerste uur dat afkomstig is van wallets die de tokens per transfer ontvingen (transfer-gevoed).

| | 1040 | 1045 |
|---|---|---|
| pools aangemaakt in het venster | 1.396 | 1.574 |
| met niet-insider-verkoop in het eerste uur | 1.350 | 1.517 |
| transfer-gevoed aandeel van die verkoop (tokens) | **16,6 %** | **12,3 %** |
| mediaan per pool | 4,0 % | 3,7 % |
| pools met ≥ 50 % transfer-gevoed | 398 (28,5 %) | 454 (28,8 %) |
| mediaan minuten van laatste transfer tot eerste verkoop | 2,1 | 2,5 |

Lezing: de verdeling is tweetoppig. De mediane pool heeft bijna geen transfer-gevoede verkoop, maar bij ruim een kwart van de pools komt meer dan de helft van de vroege niet-insider-verkoop uit per transfer ontvangen tokens, en die verkopers handelen binnen een paar minuten na de ontvangst. Dat is de "bundle-uitverkoop" als token-eigenschap; hij hoort als segment "transfer-gevoede pools" in de K3-paper-beschrijving en in de markout-rapporten (spec v3 §1), zonder lat. Job 79 gaf 16,9 % / 12,4 %: de insider-set (#279) verschuift wie als niet-insider telt, de lezing niet.

## 5. Wat hierna

- Job 816 (NAS, #281 + #282, na job 82): 4.1 en 4.2 opnieuw; het getal komt als correctie bij E26. Afspraak (coördinator 2026-10-16): dit is de laatste transfers-fixronde, tenzij 816 een conclusie kan omdraaien.
- Q1 (c): Lab Backtest herrekent V_ins en de O7-tabellen met share_c (engine-set) onder de ongewijzigde §10-regels; LAB research schrijft de herrekennoot bij het M1x-rapport (laatste herrekening, spec v3 §4.3). Verwachting uit §2: de veto-set krimpt van ~21 % naar 2–4 % van de triggers.
- Spec v3 Q1: de tekst "creator + curve-kopers" tegenover de engine-set (curve + normal-pool-kopers): een addendum van de coördinator, geen nieuwe meting.
- Latebloom-noot: de houderconcentratie- en transfers-kolommen voor 1040/1045 (E22, open punt).
- Q2: de groep "onbekend" (836 k wallets per epoch) is de grootste gekoppelde groep; een labeling daarvan is een nieuwe vraag, geen onderdeel van deze spec.
- Alles hier is dev (verbruikte dagen); Alpenglow (epoch 1064) is een nieuw regime.
