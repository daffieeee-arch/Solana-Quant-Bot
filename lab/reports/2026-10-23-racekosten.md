# E65 Racekosten op dev: wat levert sneller instappen op voor Terugveer lang (H5) en de koopstroom-arm C3 bij 10, 25 en 100 SOL? (2026-10-23)

| | |
|---|---|
| vraag | coördinator-opdracht 23 okt: hoe verandert netto SOL/dag van de leads bij 10 / 25 / 100 SOL met onze effectieve vertraging d = 0 / 1 / 2 / 3 slots en de 600 ms-basis, met een landingskans naar het aantal t+1-kopers vóór ons (p_land 1 / 0,5 / 0,25 / 0,1 bij 0 / 1 / 2 / ≥ 3) en de fee om te landen uit wat winnaars betaalden; per lead de break-even-vertraging, wat "zo snel als de snelste bot" waard is, en een ruwe infra-prijs (wat-als; colocatie staat geparkeerd) |
| plan | `data-old-faithful-one/lab/research/racing-cost-plan-2026-10-23.md`, sha256 **342876de94d2cf54af7d851961ecd92abfb673b9dc1ba2e607a345a1eff50796** (buiten Git; vastgelegd vóór enig nieuw getal; coördinator OK, QUEUE 643; LAB plan-review OK, 6 should-fixes in de code) |
| code | `lab/backtest/racing_cost.py` (table / report / w8b) + `nas_racing_cost.sh` (#536; run op 01b1853, LAB code-review OK; vervolgcommit 4d12307 voegt alleen bewakingen toe: spill-map, rastercontrole G0, wachter-sleep) |
| run | NAS-job **99zzzzz58** op 01b1853, rc 0 (18:27:50Z), 30 tests; poorten G1–G5 GREEN (G1 de q-regel: een d 0-rij binnen het triggerslot is vooruitkijken; G2 d 1 = route (ii) D1both rij voor rij; G3 d 0 kosten en tokens = route (ii) E0; G4 E63's `ceiling-report-b149a7d/ceiling.json` exact gereproduceerd; G5 het aantal t+1-kopers = de K1–K3-vlaggen van route (ii)). Gesynchroniseerd: `backtests/racing-cost-01b1853/` SHA256SUMS **f76ca439**d6ddacd90031adea500d8e0c69ba07892d1614fbb17b556678e48102 (7 bestanden OK; racing.json, racing.md, race/), plus `racing-d0-01b1853/` en `racing-dB-01b1853/` |
| data | dev2 1033–1046 (verbruikt), 20 UTC-dagen; d 2 en d 3 uit E63's run (capital-ceiling-d15ac45, ongewijzigd: geen engine-wijziging sinds d15ac45); racetabel 14.810 triggers uit dev2.pool; C3-sleutels 1a274ec6…; W8-B uit E51's proberijen `w8est/e1_1033_1046.parquet` (sha256 7abe8026…) → `backtests/racing-w8b-01b1853/` (SHA256SUMS: w8b.json aa0b77a7…, w8b.md 1d108a70…) |

Labels: feit / reconstructie / modelaanname / onbekend. Elk bedrag is een **reconstructie met het kostenmodel op dev, vóór Alpenglow, in-sample** (dev is meer dan 1.300 keer bekeken; H5 en de arm zijn erop gekozen). De leads zijn alternatieven op dezelfde triggers: ze tellen niet op. d telt dev-slots (267–317 ms); één d voor in- en uitstap, zoals E63.

## Kort

- **Racen om als eerste in t+1 te landen ("zo snel als de snelste bot", d 0) verliest.** Met de landingskansen van de opdracht en "gemist = geen positie" kost het t.o.v. nu (d 3) **H5 −1,8 / −4,5 / −16,9** en **C3 −4,5 / −8,5 / −19,8 SOL/dag** bij 10 / 25 / 100 SOL (met limieten; alle zes intervallen onder 0, bovengrenzen −0,62 … −4,76). Zelfs met zekere landing (orakel) is eerst-in-t+1 nauwelijks beter dan midden-in-t+1 (d 1): H5 +0,07 / −0,14 / −4,46, C3 +1,0 / +2,4 / +3,3 (ongepaarde puntverschillen). Daarbovenop kost die snelheid ≈ $2.400–6.800 per maand per regio. **Geen colocatie-race.**
- **De waarde zit in van d 3 naar d 1 ("snel"):** H5 **+1,00 [+0,25; +1,83] / +2,64 [+0,74; +4,75] / +1,60 [−4,70; +8,79]**, C3 **+0,17 [−2,34; +2,16] / +2,78 [+0,25; +5,88] / +11,19 [−2,70; +31,74]** SOL/dag (gepaarde dag-bootstrap, met limieten). Netto na de "snel"-laag (≤ $650/maand + 0,001 SOL tip per transactie): H5 ≈ +0,7 / +2,3 / +1,3, C3 ≈ −0,1 / +2,5 / +10,9.
- **Maar d 1 is live op onze huidige stroom onbereikbaar** (feit, E67: slot t+1 is in 99,8 % van de F7-triggers al geproduceerd als wij t zien). d 1 vergt **processed-commitment** (gratis, p50 321 ms eerder) **plus een eigen verzendpad** dat in ≈ 300 ms (p50) landt; dat is nooit gemeten, want het lab verstuurt niets. Processed alleen schuift ons ≈ 1,5 slot op (van live t+3 naar ≈ t+2, in tijd dev's d 2): H5 d 2 − d 3 +0,29 / +0,34 / −2,09, C3 −0,13 / +1,66 / +12,24 (puntschattingen).
- **Snelheid is een verbetering, geen voorwaarde.** De break-even-vertraging ligt voor beide leads bij elk kapitaal **voorbij 13 slots** (> 3,5 s op dev): ze blijven bij elke onderzochte vertraging positief, met de ondergrens van het interval boven 0 (in-sample).
- **De fee om te landen is niet de drempel.** De eerste t+1-koper betaalde mediaan **0** lamports prioriteitsfee + Jito-tip (p90 5.000 bij H5, 13.270 bij C3); ons kostenmodel betaalt al 720.618 per transactie. Het verschil zit in de plek in het blok (latency), niet in de fee.
- **W8-B** (migratie-opening; rekenwerk op E51's proberijen, niet vergelijkbaar): bij 0,5 SOL d 1 +5,5, d 2 +0,8, d 3 −0,4 SOL/dag; met E51's +0,6 pt extra kosten al vanaf d 2 negatief; bij 1,25 SOL alleen d 1 positief (interval inclusief 0), bij 5 SOL overal negatief. Snelheid beslist daar wél, en d 1 is live onbereikbaar.

> **Correctie 2026-10-24** (Codex-bevinding #5, PR #566 → main 89c0dc9: de dagelijkse verlieslimiet werd op de triggerdag geboekt, paper boekt op de uitstapdag; nu op de uitstapdag. Herberekening report-only, NAS-job 99zzzzz67d op 576db7a, `backtests/capital-exitday-576db7a/` SHA256SUMS 950ecbd5…; `delta-E65.md` 557d869f… / `.json` aba9e595…, `e65/racing.json` 7f4936c0…, `e65/racing.md` a7da1bb7…; poorten G0–G5 GREEN): 5.994 bladeren vergeleken, 1.968 gewijzigd, **alle met limieten; zonder limieten ongewijzigd (poort GREEN)**. Met limieten, base, 10 / 25 / 100 SOL, **oud → nieuw**: snelste (d 0, p_land, F p90, gemist = geen) − d 3: H5 −1,82 [−3,01; −0,62] / −4,52 [−7,41; −1,56] / −16,93 [−27,57; −6,41] → **−1,31 [−2,92; +0,37] / −3,41 [−7,24; +0,63] / −11,13 [−24,91; +3,01]**; C3 −4,49 [−6,94; −2,15] / −8,45 [−13,74; −3,42] / −19,82 [−36,22; −4,76] → −3,56 [−5,78; −1,46] / −8,51 [−13,80; −3,46] / −18,86 [−35,68; −3,03]. Snel d 1 − d 3: H5 +1,00 [+0,25; +1,83] / +2,64 [+0,74; +4,75] / +1,60 [−4,70; +8,79] → **+0,69 [−1,14; +2,73] / +3,06 [−1,12; +7,94] / −3,36 [−17,91; +12,77]**; C3 +0,17 [−2,34; +2,16] / +2,78 [+0,25; +5,88] / +11,19 [−2,70; +31,74] → +1,13 [+0,08; +2,41] / +1,74 [−1,29; +5,10] / +12,35 [−0,90; +32,47]. Niveaus H5 met limieten: d 1 +6,63 / +16,11 / +38,67 → +5,11 / +13,67 / +24,75; d 2 +5,91 / +13,81 / +34,99 → +5,38 / +12,70 / +25,90; d 3 +5,62 / +13,47 / +37,08 → +4,42 / +10,61 / +28,11. **Lezing bijgesteld:** "racen verliest" blijft in puntschatting bij alle zes cellen, maar de drie H5-intervallen bevatten nu nul (de C3-intervallen niet); de waarde van "snel" (d 1 − d 3) is voor H5 niet meer van nul te onderscheiden (was +1,00 [+0,25; +1,83] bij 10 SOL) en voor C3 alleen bij 10 SOL. De beslissing verandert niet: d 1 is live onbereikbaar (E67) en geen racelaag wordt gebouwd. Zonder limieten (de "without"-kolommen) staan alle cijfers als voorheen. Het oude rapport (`racing-cost-01b1853/report/`) blijft staan.

## 1. Hoe gerekend (plan §1–§9)

- **Rijen.** H5 = flow300_tp100_H6, organisch; C3 = hold300 op E56's arm-sleutels (paid_buyers_60s ≥ 2), beide als E63 (F7 pool_reversed). Groottes 0,5 / 1,25 / 5 SOL = 5 % van C.
- **Vertragingen.** d 0 = `--delays 0 --q 1.0`: de vulling op de eindstand van triggerslot t (= eerst in t+1, de stand van route (ii)'s E0) en een uitstap zonder vertraging; de engine laat die nooit mislukken. d 1, 5, 8, 13 nieuw (q 0,5, midden in het slot); d 2 en d 3 uit E63. De 600 ms-basis is d 2 op dev en d 3 live.
- **Landing (alleen d 0).** n = transacties met een tokenkoop in slot t+1 (de regel van `_entry_index`), uit dev2.pool; p_land 1 / 0,5 / 0,25 / 0,1 (modelaanname van de opdracht; OF1 toont alleen gelande transacties). Gemist = geen positie, de mislukte transactie betaald (primair), of de d 1-rij van dezelfde trigger (gevoeligheid). Met limieten 200 gepaarde trekkingen (sha256 van pool|t|trekking, gelijk in elke cel).
- **Fee F** per d 0-poging bovenop het kostenscenario: 0, de p50 / p90 van de eerste t+1-koper van de lead, 0,001 / 0,005 / 0,01 SOL. F* = de fee waarbij d 0 gelijk is aan de referentie (zonder limieten, exact).
- **Limieten en statistiek** als E63: 5 % van C per trade, max 5 open, daglimiet 10 % van C, 24 u pauze, één per pool, onverkocht vrij op de deadline; dag-bootstrap (10.000, seed 7).

## 2. Netto SOL/dag per vertraging (base, met limieten, [95 % dag-interval])

**Terugveer lang H5** (mislukte instap % = de engine's 5 %-slippagegrens; posities per dag met limieten; beide bij 10 SOL):

| d | 10 SOL | 25 SOL | 100 SOL | instap mislukt | posities/dag |
|---|---|---|---|---|---|
| 0 orakel (p_land 1, F 0) | +6,70 [+4,53; +8,86] | +15,97 [+10,70; +21,18] | +34,21 [+18,41; +50,12] | 0 % | 78,8 |
| 0 met p_land, F p90, gemist = geen | +3,81 [+2,60; +5,02] | +8,95 [+6,02; +11,89] | +20,15 [+9,15; +31,41] | – | 107,3¹ |
| 0 met p_land, F p90, gemist → d 1 | +6,38 | +15,22 | +32,43 | – | 79,7 |
| 1 | +6,63 [+4,46; +8,73] | +16,11 [+10,67; +21,48] | +38,67 [+19,37; +58,16] | 3,0 % | 81,1 |
| 2 (600 ms op dev) | +5,91 | +13,81 | +34,99 | 10,1 % | 86,4 |
| 3 (live nu) | +5,62 [+3,66; +7,55] | +13,47 [+8,59; +18,25] | +37,08 [+20,32; +53,89] | 15,1 % | 91,4 |
| 5 | +5,47 | +12,91 | +33,05 | 21,8 % | 96,9 |
| 8 | +6,13 | +14,38 | +36,98 | 26,4 % | 109,9 |
| 13 | +6,01 [+3,97; +8,25] | +14,11 [+9,00; +19,66] | +32,80 [+14,08; +52,34] | 30,2 % | 109,7 |

**Koopstroom-arm C3/hold300:**

| d | 10 SOL | 25 SOL | 100 SOL | instap mislukt | posities/dag |
|---|---|---|---|---|---|
| 0 orakel | +9,06 [+4,89; +13,68] | +21,43 [+11,49; +32,48] | +51,69 [+20,75; +87,01] | 0 % | 89,6 |
| 0 met p_land, F p90, gemist = geen | +3,38 [+1,71; +5,34] | +7,82 [+3,78; +12,58] | +17,34 [+4,32; +33,04] | – | 87,5¹ |
| 0 met p_land, F p90, gemist → d 1 | +8,86 | +20,34 | +46,07 | – | 94,7 |
| 1 | +8,04 [+3,96; +12,60] | +19,05 [+9,32; +29,94] | +48,35 [+19,01; +82,50] | 6,1 % | 88,6 |
| 2 | +7,74 | +17,93 | +49,40 | 15,9 % | 97,1 |
| 3 | +7,87 [+4,37; +11,53] | +16,27 [+8,22; +25,13] | +37,16 [+12,43; +65,47] | 22,4 % | 107,9 |
| 5 | +5,94 | +14,03 | +37,54 | 28,5 % | 92,5 |
| 8 | +7,28 | +15,90 | +41,37 | 32,3 % | 111,7 |
| 13 | +8,94 [+4,87; +13,34] | +17,35 [+8,73; +26,77] | +36,73 [+10,06; +68,11] | 35,6 % | 125,2 |

- ¹ Bij d 0 met p_land tellen de missers mee als genomen (een betaalde, mislukte transactie zonder positie).
- d 3 reproduceert E63 exact (poort G4). Pessimistische kosten geven hetzelfde beeld, ≈ 1–2 SOL/dag lager bij 10 SOL (racing.md).
- **Niet monotoon in d.** Na d 5 stijgt SOL/dag weer, terwijl het aandeel mislukte instappen oploopt van 15 % (d 3) naar 30–36 % (d 13). Het valt samen met meer posities per dag (H5 bij 10 SOL: 81 bij d 1, 91 bij d 3, 110 bij d 8): een mislukte instap neemt geen plek in, dus komen er meer latere triggers aan bod. Hypothese: de 5 %-slippagegrens van de engine werkt bij grote d als filter; hij weigert vooral instappen in pools die al wegliepen. Dat is een eigenschap van het model, geen bewezen voordeel van traagheid: lees d 8 en d 13 niet als "trager is beter".
- Bij 100 SOL zijn de verschillen tussen vertragingen klein tegenover de intervallen. Daar beslissen prijsimpact en limieten, niet de snelheid.

## 3. Wat snelheid waard is (gepaard, met limieten, base)

| verschil (SOL/dag) | H5 10 / 25 / 100 SOL | C3 10 / 25 / 100 SOL |
|---|---|---|
| snelste (d 0, p_land, F p90, gemist = geen) − d 3 | −1,82 [−3,01; −0,62] / −4,52 [−7,41; −1,56] / −16,93 [−27,57; −6,41] | −4,49 [−6,94; −2,15] / −8,45 [−13,74; −3,42] / −19,82 [−36,22; −4,76] |
| snelste, gemist → d 1, − d 3 | +0,76 [+0,01; +1,58] / +1,75 [+0,02; +3,67] / −4,64 [−12,71; +3,94] | +0,99 [−2,03; +4,27] / +4,07 [−0,06; +9,66] / +8,90 [−6,62; +29,07] |
| orakel − d 3 (bovengrens) | +1,08 [+0,32; +1,90] / +2,50 [+0,60; +4,53] / −2,87 [−10,61; +5,44] | +1,19 [−1,65; +3,75] / +5,16 [+1,37; +9,87] / +14,52 [−0,66; +37,16] |
| **snel: d 1 − d 3** | **+1,00 [+0,25; +1,83] / +2,64 [+0,74; +4,75] / +1,60 [−4,70; +8,79]** | **+0,17 [−2,34; +2,16] / +2,78 [+0,25; +5,88] / +11,19 [−2,70; +31,74]** |
| d 1 − d 2 | +0,72 [+0,17; +1,36] / +2,30 [+0,84; +3,93] / +3,69 [−0,83; +8,97] | +0,30 [−0,32; +0,97] / +1,12 [−0,18; +2,62] / −1,06 [−10,50; +6,48] |

- **Waarom de race verliest.** Bij p_land gemiddeld 0,59 (H5) en 0,50 (C3) mist een d 0-poging 41–50 % van de triggers. Een gemiste trigger is een gemiste winstgevende positie, en de mislukte transactie wordt wel betaald. Eerst-in-t+1 levert per geslaagde positie bijna niets op boven midden-in-t+1: het orakel ligt ≈ op d 1 (ongepaarde puntverschillen orakel − d 1: H5 +0,07 / −0,14 / −4,46, C3 +1,0 / +2,4 / +3,3). Daardoor wint geen enkele fee de race terug: F* (snelste − d 3) is negatief, H5 −0,026 / −0,062 / −0,159 en C3 −0,043 / −0,102 / −0,288 SOL per poging. Met terugval naar d 1 ligt F* tegen d 3 op H5 +0,010 / +0,025 / +0,086 en C3 +0,009 / +0,021 / +0,071 SOL.
- **Wat winnaars betaalden (feit, dev; ondergrens: tips aan andere relays, gevallen en mislukte transacties zie je niet).**
  - H5: de eerste t+1-koper p50 0 / p90 5.000 / p99 241.141 lamports (1.893 triggers); alle t+1-kopen p90 1.000, 5,4 % met een Jito-tip.
  - C3: p50 0 / p90 13.270 / p99 270.465 (1.766 triggers); alle t+1-kopen p90 4.820, 9,8 % met tip.
  - Ons base-scenario betaalt per transactie al 720.618 lamports (waarvan 0,0001 SOL tip dubbel geteld, voor vergelijkbaarheid met E63).
- **t+1-concurrentie** (dev, H5 bij 10 SOL): 0 / 1 / 2 / ≥ 3 kopers bij 39 / 31 / 13 / 17 % van de triggers; C3 30 / 26 / 19 / 24 %. Live is het minder (feit, E67: gemiddeld 0,47 tokenkopen in t+1, 69,5 % zonder). Een live d 0-poging zou dus vaker landen, maar d 0 is live niet haalbaar (§5).

## 4. Infra wat-als (schattingen met bronnen in het plan; $104,37/SOL, $1.000/maand ≈ 0,315 SOL/dag)

| laag | prijs | ↔ d | netto t.o.v. "zoals nu" (SOL/dag, H5 / C3 bij 10 / 25 / 100 SOL) |
|---|---|---|---|
| zoals nu (confirmed-stroom op de VPS) | $35–50/maand ≈ 0,011–0,016 SOL/dag | 3 | – |
| snel (processed-commitment, een verzendpad; eventueel staked connections of een server in Frankfurt) | $50–650/maand + 0,001 SOL tip per transactie (≈ 0,16–0,18 SOL/dag) | 1 | H5 +0,65…+0,84 / +2,29…+2,48 / +1,25…+1,44; C3 −0,20…−0,01 / +2,41…+2,60 / +10,8…+11,0 |
| zo snel als de snelste bot (dedicated node + raw shreds + tips; per regio) | $2.400–6.800/maand ≈ 0,76–2,14 SOL/dag | 0 | H5 −3,9…−2,6 / −6,7…−5,3 / −19,1…−17,7; C3 −6,6…−5,2 / −10,6…−9,2 / −22,0…−20,6 |

De nettowaarden zijn het punt van het gepaarde verschil min de extra laagkosten; de intervallen uit §3 gelden ook hier.

## 5. Is d 1 live bereikbaar?

- **Feit (E67, onze confirmed-stroom ná Alpenglow):** bij 99,8 % van de F7-triggers is slot t+1 al geproduceerd als wij de trigger ontvangen (mediaan −23 ms). Slot t+2 volgt mediaan +180 ms ná ontvangst. Op deze stroom is d 1 dus onbereikbaar, d 2 nipt, en d 3 (≈ 640 ms) is wat de engine simuleert.
- **Feit (lead3-probe 22 okt, `research/triton-services-edge-2026-10-20.md`):** processed-commitment toont een transactie p50 321 ms (p90 461 ms) eerder dan confirmed, op hetzelfde endpoint en tegen hetzelfde tarief.
- **Feit (T3, 22 okt):** op processed komt een pump-transactie p50 80,7 ms vóór Tritons slot-afronding binnen (p10 16,8, p90 163), en p50 108,7 ms vóór de eerste shred van het volgende slot.
- **Reconstructie:** op processed blijft er p50 ≈ 100 ms tot t+1 begint, plus ≈ 200 ms waarin t+1 loopt. In die ≈ 300 ms moet de transactie beslist, gebouwd, verstuurd en door de leader opgenomen worden. In de staart (p90) is het venster kleiner. Voor t+2 komt er ≈ 200 ms bij.
- **Onbekend:** onze eigen tijd van verzenden tot opname, en de landingskans, via elk pad (Cascade, Sender, Jito). Het lab verstuurt niets. simulateBundle (#538) meet de vulling, niet de opname.
- **Dev tegen live.** d telt hier dev-slots van 267–317 ms. Dev's d 1 ligt in tijd ≈ 270–320 ms na het einde van slot t, en dat is live ≈ 1,3–1,5 slot.
- **Dus:**
  - "snel" = d 1 kan alleen met processed plus een snel verzendpad. Dat is mogelijk binnen het tijdsbudget, maar onbewezen, en verzenden is een eigenaarsbesluit (paper-gated).
  - Processed alleen brengt ons van live t+3 naar ≈ t+2: bij H5 +0,3 SOL/dag (10–25 SOL), bij C3 gemengd.
  - De +1,0 / +2,6 SOL/dag van H5 (10 / 25 SOL) bij d 1 is een **bovengrens** onder die twee voorwaarden.

## 6. W8-B (migratie-opening; rekenwerk, niet vergelijkbaar met H5 en C3)

`racing_cost w8b` past E51's eigen kostenopbouw (c = 2·fp + impact + 0,4 pt) per pool toe op 0,5 / 1,25 / 5 SOL: zonder limieten, op pseudo-dagen van 324.000 slots, zonder p_land, zonder waargenomen fees. De lineaire impact is een ondergrens van de kosten. E51-controle bij 0,25 SOL: +1,48 / +0,35 / +0,05 %, GREEN.

| alle pools | d 1 | d 2 | d 3 |
|---|---|---|---|
| 0,5 SOL | +5,49 [+2,67; +8,30] | +0,83 [−1,51; +3,16] | −0,38 [−2,91; +2,05] |
| 0,5 SOL, +0,6 pt | +3,07 | −1,61 | −2,83 [−5,36; −0,40] |
| 1,25 SOL | +4,37 [−2,65; +11,34] | −7,32 | −10,36 |
| 5 SOL | −191 | −239 | −252 |

- Dezelfde-slot-subgroep (post hoc) bij 0,5 SOL: +8,59 / +3,60 / +2,12; met +0,6 pt +7,02 / +2,00 / +0,51.
- Hier beslist snelheid wél. De break-even ligt bij 0,5 SOL tussen e1's d 2 en d 3, met +0,6 pt tussen d 1 en d 2, en bij 1,25 SOL tussen d 1 en d 2.
- e1's d telt vanaf het eerste niet-boost-tradeslot, met een vulling aan het einde van dat slot. Dat dev's 600 ms tussen e1's d 1 en d 2 valt, en live tussen d 2 en d 3, is een afspraak, geen meting.
- Een racemodel voor W8-B (eerst in het slot, n per pool, waargenomen fees) bestaat niet.

## 7. Kanttekeningen

- **In-sample** op verbruikte dev, vóór Alpenglow. De leads zijn op deze data gekozen.
- **d 0 is optimistisch:** heel slot t is bekend, eerst in t+1, uitstap zonder vertraging. Een backrun in hetzelfde slot zit er niet in.
- **p_land is een modelaanname** en hangt niet af van F. Na een misser gaat de bezet-keten van de pool niet open, wat conservatief is.
- **De waargenomen fees zijn een ondergrens.**
- **Sandwich niet gemodelleerd:** de E63-vlag staat op 55 % van de trades bij 0,5 SOL en op 98–100 % vanaf 1 SOL.
- **Live aanbod:** het live triggeraanbod is ≈ 20–29 % van dev (E63). Dev-bedragen per dag zijn een plafond.
- **Dubbele tip:** het kostenscenario telt de tip dubbel, voor vergelijkbaarheid met E63.
- **Exit-snelheid:** één d voor in- en uitstap. Voor H5 doet de uitstap-snelheid er niet toe (route (ii): D1both ≈ D1); voor hold300 is dat niet gemeten.

## 8. Bestanden

- Plan: `research/racing-cost-plan-2026-10-23.md` (sha256 342876de…).
- Runs:
  - `backtests/racing-d0-01b1853/`: 50.598 rijen, d 0 bij q 1,0.
  - `backtests/racing-dB-01b1853/`: 230.028 rijen, d 1 / 5 / 8 / 13.
  - E63's `backtests/capital-ceiling-d15ac45/`: d 2 en d 3.
- Rapport: `backtests/racing-cost-01b1853/report/racing.md` + `racing.json` en `race/` (SHA256SUMS f76ca439…).
- W8-B: `backtests/racing-w8b-01b1853/`.
- Droogruns op de VPS (geen bewijs): `racing-cost-01b1853-dry/` en `racing-cost-4d12307-dry/`.
