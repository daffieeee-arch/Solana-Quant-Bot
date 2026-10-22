# E62: geld volgen aan de fee-kant — kan een LP-positie op PumpSwap ≥ 20 SOL/dag opleveren?

LAB research, 2026-10-23. Opdracht coördinator (owner-doel 23 okt: "minimaal 20 SOL per dag"): de lp-fee-stroom van PumpSwap, die we nog nooit gemeten hadden. Verkennend, alleen dev 1033–1046 (11 sep–1 okt), geen hold-out, geen clean. Register E62. Labels **feit** / **reconstructie** / **modelaanname** / **onbekend**. Kapitaalniveaus 10 / 25 / 100 SOL (owner 23 okt: "die 1000 SOL hoeft niet").

Scripts en uitvoer: `research/e62-lp-fees-2026-10-23/` (SHA256SUMS, 16 bestanden); data `wallets/e62-lp-fees/` (`pooldays.parquet` 5c4365c0…, `lp_legs.parquet` 9ceae0a0…, `pooldays-normal-net.parquet` e85ffb61…, `lp_wallets.parquet` d364bb38…; SHA256SUMS.data c455343b…). Ieder getal hieronder staat in een `.out` van dat archief, tenzij "(niet gepind)".

## 0. Samenvatting

- **Stap 1 maakt het niet dood (feit):** iedereen kan in elke PumpSwap-pool storten (`deposit`/`withdraw` hebben alleen `user` als signer, geen admin, geen fee-accounts); de lp-fee blijft in de quote-vault en komt pro rata op de LP-supply; geen deposit-/withdraw-fee. De LP van gemigreerde pump-pools is verbrand (docs), dus een storting daar deelt alleen met andere stortingen.
- **De taart is kleiner dan gedacht (feit):** de lp-fee op alle normale SOL-pools is **6.404 SOL per volle dev-dag** (121.680 SOL over 12–30 sep), niet ~37 k: 91 % van het volume loopt in de 20-bps-tier, 8 % in de 2-bps-tier, 0,6 % niet-canoniek (25 bps); de meeste pool-dagen verdienen niets (mediaan rendement 0; p99 4,5 %/dag; 453 van 325.492 pool-dagen ≥ 20 %/dag bruto).
- **Point-in-time is alles negatief (feit, modelaanname voor de positie):** zes selectieregels op gisteren (rendement, wash, diepte, leeftijd, canoniek) × k = 1/3/5/10 × 10/25/100 SOL: **geen enkele cel positief**. Beste cellen: 10 SOL −0,86 SOL/dag [−4,39; +3,88], 25 SOL −3,30 [−6,25; +0,18], 100 SOL −22,1 [−34,7; −6,8] (dag-bootstrap 10.000×, 20 dagen). Het doel (≥ 20 SOL/dag = 20 %/dag op 100 SOL) ligt ver buiten elk interval. Het fee-rendement van gisteren houdt niet (topdeciel 1,9 % → 0,19 % de dag erna) en de poolwaarde daalt dagelijks (memecoins): IL + in-/uitstapkosten ≫ fees.
- **Wie verdient er dan wél aan LP (feit):** in drie weken dev hebben 101 wallets een gesloten LP-rondrit op normale SOL-pools; mediaan −0,01 SOL, beste +9,2 SOL over vijf dagen, 8 wallets ≥ 1 SOL per actieve dag, **nul** in de buurt van 20 SOL/dag. Stortingen in canonieke pump-pools: 43 legs in 8 pools in heel dev. De grote LP-stromen zijn (a) operators van niet-canonieke pools die hun eigen pool legen en (b) drie wallets met 5.200 / 2.360 / 1.680 SOL in oude grote pools (open aan het eind van dev; rendement onbekend).
- **Gevolg:** passieve of selectieve LP is op dev geen weg naar 20 SOL/dag, bij geen enkel kapitaalniveau; geen vervolgtoets. Een hypothese die overblijft, maar buiten onze middelen: een "vault-level" wash-detector die de 152,4-SOL-fabriek (§5) van binnenuit zou kunnen melken, bestaat niet voor derden (de operator stort en haalt zelf).

## 1. Mechanica (stap 1; feit uit IDL `lab/extractor/idl/pump_amm.json`, docs en de dev-data)

| vraag | antwoord | bron |
|---|---|---|
| kan iedereen storten? | ja: `deposit(lp_token_amount_out, max_base_amount_in, max_quote_amount_in)` en `withdraw(...)` hebben alleen `user` als signer; geen admin-, creator- of whitelist-account; `global_config.disable_flags` = 0 (MR-B1, 22 okt) | IDL; `probe/mrb1-20261022/mrb1.json` |
| wie houdt de bestaande LP? | gemigreerde pump-pools: de LP-tokens worden bij migratie verbrand (pump.fun public docs: "The LP tokens received from the PumpSwap pool are automatically burnt"); E36 vond 0 LP-verbranding bij reversed pools en 1,5 % withdraws bij normale pools, passend bij "verbrand bij migratie" | docs (websearch 23 okt, niet gepind), E36 |
| waar komt de lp-fee terecht? | in de quote-vault: per trade groeit k = b × e (e = vault + virtual quote) met precies lp_fee/e; op slice AB (normale pools) gemeten 0,064 bps per buy / 0,079 per sell tegen verwacht 0,067 / 0,077 (lp 20); 0,029 / 0,026 tegen 0,038 / 0,020 (lp 2); 0,000 bij lp 0. De toestandsketting klopt op 100,000 % van de legs (volgende vault = vault + e_delta, b = b ∓ base), ook over deposit/withdraw-legs | `kcheck_AB_e.out` |
| pro rata? | deposit-aandeel = quote_in / vault = lp_out / lp_supply (verschil 0,000000): stortingen gaan tegen de échte reserves, niet tegen de virtuele | `kcheck_AB_e.out` |
| deposit-/withdraw-fee? | geen (geen fee-velden in Deposit-/WithdrawEvent, geen fee-accounts); kosten = tx-fee en de LP-ATA-rent (Token-2022) | IDL |
| fee-tiers in dev | per leg in de store: (lp 2 / protocol 93 / creator 30) = 125 bps, (20 / 5 / creator 95 → 5) = 120 → 30 bps, niet-canoniek (25 / 5 / 0) en (25 / 5 / 3); reversed pools (25 / 5 / 0) maar daar valt de lp-fee in het token en wordt 99,95 % binnen 7 min gedraind (E36) → alleen normale SOL-pools tellen | `slice_A.out`, E36 |
| virtual quote (vq) | 55 % van de pool-dagen heeft vq > 0 (mediaan vq/vault 0,26, p90 9,1); vq is binnen de dag constant (99,97 %). Prijs = e/b, dus de tokenkant van een storting kost e/vault × de SOL-kant en de positiewaarde is vault + e, niet 2 × vault | `analyse.out` |

Caveat van de coördinator beantwoord: `pool_quote_token_reserves` in Buy/SellEvent is de toestand **vóór** de trade (store-semantiek, `pump-amm-event-reserves`).

## 2. Per pool-dag (stap 2; feit; `lp_pooldays.py` → `pooldays.parquet`, UTC-dagen via `blocks`)

Normale SOL-pools: 400.900 pool-dagen van 95.495 pools, volume 67,97 M SOL, lp-fee exact 126.084 SOL (via sol_amount × bps 126.128, verschil 0,03 %).

| dag | lp-fee (SOL) | 2-bps-aandeel volume | pool-dagen ≥ 1 SOL fee | pool-dagen |
|---|---|---|---|---|
| 11 sep (deel) | 929 | 5,1 % | 156 | 5.492 |
| 12–30 sep (19 dagen) | 5.146 … 8.899, Σ 121.680 = 6.404/dag | 4,3–10,1 % | 705–845 | 17.881–24.447 |
| 1 okt (deel) | 3.475 | 9,7 % | 495 | 16.043 |

(Σ en gemiddelde zijn mijn optelling over de gepinde dagtabel in `analyse.out`.) De voorlopige kill-check op het uur-aggregaat (`kill.out`: 4.339/dag, 63 % in de 2-bps-tier) was fout: dat aggregaat draagt de tier van de láátste trade van het uur, en pump-and-dumps eindigen in de laagste tier. De store-legs zijn de waarheid.

Bruto fee-rendement per pool-dag (lpfee / (vault + e) bij de eerste toestand van de dag; pool-dagen met poolwaarde V0 ≥ 20 SOL, 325.492 = 99,97 % van de fee): mediaan 0, p90 0,008 %, p99 4,46 %, p99,9 23,8 %, max 305 %; ≥ 1 %/dag: 9.987; ≥ 5 %: 2.912; ≥ 20 %: 453 pool-dagen.

**Netto LP-rendement bij hindsight, zonder selectie (modelaanname §6):**

| universum | X | gem. netto SOL | mediaan | aandeel > 0 | gem. bruto fee op X |
|---|---|---|---|---|---|
| alle pool-dagen V0 ≥ 20 (325.492) | 10 | −5,07 | −3,68 | 1,6 % | 0,018 |
| | 25 | −24,3 | −19,2 | 1,1 % | 0,040 |
| | 100 | −272 | −215 | 0,6 % | 0,114 |
| "stabiel": V0 ≥ 50 en |Δprijs| < 10 % (83.061) | 10 | −1,73 | −1,81 | 0,7 % | 0,001 |
| | 25 | −9,29 | −9,81 | 0,3 % | 0,002 |
| | 100 | −114 | −120 | 0,1 % | 0,008 |

In de "stabiele" pools is de fee op 10 SOL 0,001 SOL per dag; de in-/uitstapkosten en de eigen prijsimpact in kleine pools zijn 100× groter. Het verlies in het volle universum is de dagelijkse waardedaling van memecoin-pools.

## 3. Point-in-time (stap 3 en 5; feit; `analyse.py`)

Selectie op dag d − 1, positie X_totaal gelijk verdeeld over de top-k, vasthouden over dag d (eerste → laatste toestand), 20 dagen (12 sep–1 okt), dag-bootstrap 10.000× (seed 7). Regels vooraf opgeschreven in het script (één blik; zes regels × vier k × drie X = 72 cellen, alle geregistreerd):

| regel | k | 10 SOL: SOL/dag [95 %] | 25 SOL | 100 SOL | dagen > 0 |
|---|---|---|---|---|---|
| R1 hoogste bruto rendement gisteren, V0 ≥ 20 | 1 | −0,86 [−4,39; +3,88] | −4,51 [−13,3; +7,3] | −57,1 [−102; −2,2] | 5 / 5 / 4 van 20 |
| R1 | 3 | −1,58 [−3,26; +0,32] | −5,17 [−9,29; −0,64] | −42,1 [−58,6; −24,1] | 5 / 5 / 3 |
| R1 | 10 | −2,06 [−2,91; −1,18] | −5,90 [−7,95; −3,80] | −39,5 [−47,0; −31,5] | 2 / 2 / 1 |
| R2 R1 + wash ≥ 50 % + |Δprijs| < 20 % | 1 | −4,34 [−6,45; −2,21] | −11,2 [−16,5; −6,0] | −51,7 [−73,3; −30,5] | 8 / 8 / 0 |
| R3 hoogste fee (SOL) gisteren, rendement ≥ 1 % | 3 | −1,14 [−2,34; +0,26] | −3,30 [−6,25; +0,18] | −22,1 [−34,7; −6,8] | 6 / 6 / 6 |
| R3 | 1 | −1,17 [−3,08; +1,00] | −3,52 [−8,43; +1,77] | −25,0 [−49,4; −1,4] | 6 / 5 / 5 |
| R4 R1 + leeftijd ≥ 3 d + ≥ 1.000 trades | 5 | −1,38 [−2,21; −0,63] | −3,81 [−5,78; −1,99] | −22,6 [−31,2; −14,6] | 3 / 2 / 1 van 17 |
| R5 canoniek, V0 ≥ 100, |Δprijs| < 20 %, ≥ 2.000 trades | 10 | −2,33 [−2,93; −1,75] | −5,96 [−7,40; −4,50] | −27,3 [−32,9; −21,5] | 1 / 1 / 0 |
| R6 canoniek, hoogste rendement, V0 ≥ 50 | 1 | −1,39 [−4,87; +3,30] | −5,78 [−14,5; +5,9] | −61,3 [−106; −9,5] | 4 / 4 / 3 |

Alle 72 cellen staan in `analyse.out` / `analyse_results.json`; geen enkele heeft een positief gemiddelde. **Kop: beste point-in-time netto SOL/dag = −0,86 (10 SOL), −3,30 (25 SOL), −22,1 (100 SOL); bovengrenzen van de intervallen ≤ +3,9 / +7,3 / −1,4.** Het doel van 20 SOL/dag valt buiten elk interval.

Waarom: het rendement houdt niet en de pool verliest waarde. Topdeciel van gisteren (gem. 1,9 %/dag) geeft de dag erna gem. 0,19 %; de R1-top-1 (gisteren 110 %) geeft 9,3 % bruto de dag erna bij +36 % prijs, maar top-10 (53 %) geeft 2,7 % bij −9,4 % prijs, 8,5 % pools zonder handel en 14 % bijna leeg. De IL en de uitstap wegen zwaarder dan de fee bij elke k.

## 4. Wie stort en haalt er LP (stap 4; feit; `lp_legs.py`, `wallets_lp.py`)

Alle deposit/withdraw-legs in dev: 59.385 withdraws (51.653 pools) en 3.490 deposits (1.009 pools); op **normale SOL-pools** 689 withdraws (390 pools, 354 wallets, 16.694 SOL aan SOL-kant) en 630 deposits (149 pools, 173 wallets, 19.448 SOL); mediaan storting 0,16 SOL (0,3 % van de supply), mediaan opname 46,8 % van de supply.

| herkomst pool | deposits (legs / pools / SOL) | withdraws (legs / pools / SOL) |
|---|---|---|
| canoniek, gemigreerd (AdminCtoPool + MigratePoolCoinCreator) | 43 / 8 / 320 | 30 / 8 / 207 |
| niet-canoniek (`create_pool` in dev) | 375 / 94 / 3.850 | 557 / 332 / 15.519 |
| ouder dan dev (geen bron in de store) | 212 / 47 / 15.278 | 102 / 50 / 969 |

Withdraws door de coin-creator: 80 legs, 101 SOL; door andere wallets 609 legs, 16.592 SOL (mediaan 39 % van de supply). Van 626 stortingen is 354 binnen dev weer opgenomen (mediaan 3,2 u, p90 116 u).

Wallets: 426; 173 met een storting; **101 met storting én opname** (de enige gesloten rondritten): mediaan −0,011 SOL (token op mid gewaardeerd), p90 +1,55, max +9,2 (2UkpYRfm: 10 stortingen, 1 opname, 1 pool, 5 dagen); 8 wallets ≥ 1 SOL per actieve dag; 0 wallets met ≥ 420 SOL (20/dag × 21). Grootste "verliezer" B5ZE74Qk (−7.657 SOL op SOL-basis) is een open positie van 44 % in een oude pool, geen verlies; 72 wallets hebben 9.882 SOL open aan het eind van dev (3xp34US3 5.204, BNTAniTz 2.362, 2yXd7mSt 1.684 SOL, elk één pool) — hun rendement is *onbekend*.

**Niemand verdient in dev ≥ 1 SOL/dag met LP-rondritten buiten een handvol kleine wallets (feit).** De eerdere bevinding E60 (Meteora-launchpads verdienen via LP-removals) heeft op PumpSwap geen tegenhanger: hier is de LP van canonieke pools verbrand en halen alleen operators van eigen pools.

## 5. De 152,4-SOL-fabriek (reconstructie)

628 pool-dagen van 623 niet-canonieke pools (fee-tier 30 of 33 bps) beginnen de dag op exact 152,4 SOL poolwaarde (76,2 SOL vault, vq 0) en eindigen na een prijs ×10 tot ×100.000 met mediaan 1.847 SOL in de vault bij mediaan 5,7 SOL fee. In hindsight is dat de "beste pool-dag" (netto +880 SOL/dag op 100 SOL, canoniek-filter 30 bps ontweek ze niet: tier 33 = 25 + 5 + 3), maar de winst is de prijssprong van één grote koper, niet de fee. De LP-legs van die pools: 33 opnames (1.201 SOL) door 25 wallets, 12 stortingen (163 SOL) door 5 wallets, geen van beide de coin-creator. Hypothese: één operator koopt met eigen SOL in zijn eigen pool (volume/mcap-vervalsing), haalt daarna de LP weg; een derde die ervoor instapt zou 40 % van zijn SOL afromen, dus hij laat het bij de eerste vreemde storting (of de koop volgt pas ná zijn eigen opname-check). Point-in-time (R1/R6 k = 1) vangt deze pools niet: ze bestaan maar één dag.

## 6. Positiemodel (modelaanname, `analyse.py`)

Op de eerste toestand van dag d houden we X SOL, kopen de tokenkant op de mid e/b (X·e/V0, V0 = vault + e), storten beide pro rata (aandeel s = X/(V0 + X)), houden de dag, halen op de laatste toestand en verkopen de tokens. Waarde einde = s·vault_eind + min(s·e_eind, (1 − s)·vault_eind) (de tokenkant kan alleen in de eigen SOL-vault verkocht worden), gecorrigeerd voor netto LP-stromen van anderen in de dag (f = (vault_eind − stortingen + opnames)/vault_eind, 0 ≤ f ≤ 1,5); kosten = de fee-tier van de pool op de tokenkant heen en terug, eigen prijsimpact q²/(2e) heen en terug, vier transacties à 0,0007 SOL. Verdiende fees zitten in vault_eind (§1). Pools zonder handel op dag d: gemarkeerd op de vorige slot, alleen kosten. Niet gemodelleerd: slippage van anderen tussen onze koop en storting, Token-2022-ATA-rent (terug bij sluiten), vq-veranderingen binnen de dag (0,03 % van de pool-dagen).

## 7. Open punten en wat hier niet staat

- Alles op één dev-periode (pre-Alpenglow, 268/316 ms); de fee-tiers zijn sinds september ongewijzigd (MR-B1 22 okt = de tiers in de store-legs).
- Intraday-rotatie (uur-niveau) is niet gemeten; de coördinator's eigen snelle blik op drie chunks (uur h → h + 1) was negatief op alle dagen (niet gepind, geen bevinding).
- De grote open LP-posities (§4) zijn majors; hun fee-rendement ligt in de orde van de 20-bps-pools: V0 ≥ 1.000 SOL en < 1 %/dag (p99 van de verdeling), ver onder 20 %/dag — *reconstructie* uit §2, geen aparte meting.
- Geen claim, geen pre-registratie, geen vervolg; niets gaat in de paper engine.
