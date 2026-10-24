# E70 Instaptolerantie op dev: verandert een andere slippagegrens dan 5 % wat Terugveer lang (H5) en de koopstroom-arm C3 opleveren? (2026-10-24)

| | |
|---|---|
| vraag | onafhankelijke audit, via de coördinator (24 okt): paper's instaptolerantie van 5 % is nooit gevarieerd, maar laat 15–22 % van de instappen mislukken (bij Terugveer snel K3 waren dat de winnaars). Wat doen 0,02 / 0,05 / 0,10 / 0,15 met netto SOL/dag, instappen per dag, het aandeel mislukte instappen en het aandeel leeglopers? |
| plan | `data-old-faithful-one/lab/research/tol-sweep-plan-2026-10-24.md`, sha256 **35ca706b8ae3312a6f1de77d6e1aa33da0f1c472e1433190af41b060e0af5999** (buiten Git; vastgelegd vóór enig nieuw getal; coördinator OK; LAB plan-review OK, beide should-fixes in de code) |
| code | `lab/backtest/tol_sweep.py` + `nas_tol_sweep.sh` (#545, gemerged als 8584c5d; LAB code-review OK op e3bef48) |
| run | NAS-job **99zzzzz60** op e3bef48, rc 0 (03:48:48Z), 36 tests; poorten T0–T3 GREEN (T0 de enginebestanden en `week2-defaults.yaml` byte voor byte die van E63 (d15ac45); T1 E63's `ceiling-report-b149a7d/ceiling.json` exact gereproduceerd bij 0,05; T2 de runs verschillen alleen in slippage_tol; T3 alleen dev). Gesynchroniseerd: `backtests/tol-sweep-e3bef48/` SHA256SUMS **77e11b62**1d3dad6e8c49ac2db65956ccc3676002d4ab9221416c73104eeb164f (5 bestanden OK); runs `tolsweep-t002-e3bef48/` (41.075 posities), `-t010-` (34.121), `-t015-` (33.691), elk 86.926 signalen; tol 0,05 = E63's run |
| data | dev2 1033–1046 (verbruikt), 20 UTC-dagen; C3-sleutels 1a274ec6… |

Labels: feit / reconstructie / modelaanname / onbekend. Elk bedrag is een **reconstructie met het kostenmodel op dev, vóór Alpenglow, in-sample**. De leads zijn alternatieven op dezelfde triggers.

**Eén tolerantie voor beide benen.** De engine heeft één tolerantie, net als paper's Engine (tol = 0,05). Die geldt voor twee dingen:
- een instap mislukt als de vulling onder de verwachte tokens × (1 − tol) uitkomt;
- een uitstap wordt opnieuw geprobeerd als de verkoop onder de mark × (1 − tol) zou uitkomen.

Een "betere tol" voor paper verandert dus beide benen. Deze proef doet precies dat. Een instap-alleen-tolerantie bestaat in de engine niet en is niet doorgerekend.

## Kort

- **De tolerantie is geen hefboom.** Met paper's limieten en base-kosten bevat elk gepaard verschil met 0,05 de nul:
  - H5, 10 SOL: +0,10 / −0,18 / −0,30 (tol 0,02 / 0,10 / 0,15).
  - H5, 25 SOL: −0,05 / −0,79 / −1,16.
  - C3, 10 SOL: −2,02 [−4,84; +0,36] / −0,80 / −0,33.
  - C3, 25 SOL: −2,53 [−7,83; +1,92] / +0,36 / +0,04.
  - Strakker (0,02) is voor C3 eerder slechter. Losser levert met limieten niets op.
  - Conclusie: **de tolerantie verandert het dev-plafond niet; 0,05 blijft.** K3 is niet getest (buiten de taak).
- **Zonder limieten is losser iets beter** (totaalverschil zonder limieten, ongepaard; §3):
  - H5: +0,4…+0,7 SOL/dag;
  - C3: +0,5…+1,7 SOL/dag.

  Met limieten verdwijnt dat. Minder mislukte instappen betekent dat de plekken (max 5 open, één per pool) langer bezet zijn. Dan komen er minder posities per dag door: H5 bij 10 SOL 91 per dag bij 0,05, 82 bij 0,10 en 77 bij 0,15.
- **Het verschil zit in de instap-set, niet in de uitstap.** Op de gemeenschappelijke posities (≈ 2.350–2.580 bij H5) verandert de opbrengst gemiddeld maar −0,1…+0,17 %, en de instapkosten zijn identiek. Het verschil komt uit welke posities er ontstaan. Losser laat posities toe die bij 5 % werden geweigerd. Die zijn per stuk winstgevend:
  - H5 bij 0,10: ≈ +19,5 % per positie, tegen ≈ +10,4 % gemiddeld (afgeleid: SOL/dag × 20 dagen / n, op 0,5 SOL);
  - dat past bij de audit: de geweigerde instappen waren vaak winnaars.

  Maar ze verdringen latere posities in dezelfde pools, die minstens even goed waren (per stuk 2–5 punten beter): ≈ +22,5 %, want één open positie per pool. Netto blijft er weinig over.
- **Leeglopers:** het aandeel posities dat > 90 % verliest stijgt nauwelijks, van 18,9 % (0,05) naar 19,1 % / 19,2 % bij H5 en van 20,1 % naar 20,7 % / 21,1 % bij C3. De uitstap-herhalingen dalen van 7 % naar 1,4 % / 0,5 %.

## 1. Hoe gerekend (plan §1–§5)

- **Cellen.** H5 (flow300_tp100_H6, organisch) en C3/hold300 (E56's arm-sleutels).
  - d 3, q 0,5, tau empirical.
  - 0,5 SOL (C 10) en 1,25 SOL (C 25).
  - E63's kapitaalregels: 5 % van C, max 5 open, daglimiet 10 % van C, 24 u pauze, één per pool, onverkocht vrij op de deadline.
  - Base primair, pessimistic ernaast.
- **Runs.** tol 0,02 / 0,10 / 0,15 zijn drie nieuwe toernooiruns met verder E63's argumenten. tol 0,05 is E63's run, en T0 bewijst dat het dezelfde engine is.
- **Uitkomsten.**
  - SOL/dag met en zonder limieten (dag-bootstrap, 10.000 trekkingen, seed 7);
  - posities per dag;
  - aandeel mislukte instappen;
  - aandeel leeglopers (opbrengst < 10 % van de kosten, E63's totaalverlies-regel);
  - aandeel uitstap-herhalingen;
  - het gepaarde verschil met 0,05.
  - Zonder limieten ook de opsplitsing per been op (pool, t): nieuw / verloren / gemeenschappelijk, plus de fees van mislukte instappen. De delen tellen op tot het totaal.
- **Blikken.** 32 cellen: 4 tol × 2 leads × 2 groottes × 2 kostenscenario's. Daarvan zijn er 24 nieuw en 12 primair (base, met limieten).

## 2. Uitkomsten (base; [95 % dag-interval])

**Terugveer lang H5:**

| C (grootte) | tol | met limieten SOL/dag | t.o.v. 0,05 (gepaard) | posities/dag | instap mislukt | leeglopers | uitstap herhaald | netto per gevulde trade | zonder limieten |
|---|---|---|---|---|---|---|---|---|---|
| 10 (0,5) | 0,02 | +5,72 [+3,83; +7,57] | +0,10 [−1,03; +1,22] | 113 | 28,9 % | 18,5 % | 14,3 % | +9,85 % | +6,38 |
| 10 (0,5) | 0,05 | +5,62 [+3,66; +7,55] | – | 91,4 | 15,1 % | 18,9 % | 7,0 % | +10,40 % | +7,33 |
| 10 (0,5) | 0,10 | +5,45 [+3,23; +7,70] | −0,18 [−0,89; +0,61] | 81,5 | 3,3 % | 19,1 % | 1,4 % | +10,44 % | +7,74 |
| 10 (0,5) | 0,15 | +5,33 [+3,11; +7,56] | −0,30 [−1,07; +0,53] | 77 | 0,6 % | 19,2 % | 0,5 % | +10,51 % | +7,88 |
| 25 (1,25) | 0,02 | +13,42 [+8,86; +17,91] | −0,05 [−2,88; +2,74] | 112,5 | 28,9 % | 18,5 % | 14,8 % | +9,16 % | +14,83 |
| 25 (1,25) | 0,05 | +13,47 [+8,59; +18,25] | – | 91,3 | 14,9 % | 18,9 % | 7,6 % | +9,74 % | +17,15 |
| 25 (1,25) | 0,10 | +12,68 [+7,33; +18,11] | −0,79 [−2,50; +1,12] | 81,5 | 3,2 % | 19,1 % | 1,6 % | +9,64 % | +17,85 |
| 25 (1,25) | 0,15 | +12,30 [+6,95; +17,72] | −1,16 [−3,05; +0,91] | 76,8 | 0,6 % | 19,2 % | 0,6 % | +9,54 % | +17,87 |

**Koopstroom-arm C3/hold300:**

| C (grootte) | tol | met limieten SOL/dag | t.o.v. 0,05 (gepaard) | posities/dag | instap mislukt | leeglopers | uitstap herhaald | netto per gevulde trade | zonder limieten |
|---|---|---|---|---|---|---|---|---|---|
| 10 (0,5) | 0,02 | +5,85 [+2,88; +9,14] | −2,02 [−4,84; +0,36] | 117,2 | 41,9 % | 19,2 % | 13,0 % | +19,27 % | +9,79 |
| 10 (0,5) | 0,05 | +7,87 [+4,37; +11,53] | – | 107,9 | 22,4 % | 20,1 % | 5,0 % | +17,63 % | +10,12 |
| 10 (0,5) | 0,10 | +7,07 [+3,18; +11,54] | −0,80 [−3,16; +1,04] | 83,8 | 5,0 % | 20,7 % | 1,0 % | +17,59 % | +10,88 |
| 10 (0,5) | 0,15 | +7,54 [+3,77; +11,79] | −0,33 [−2,90; +1,97] | 85,1 | 0,8 % | 21,1 % | 0,1 % | +17,02 % | +10,67 |
| 25 (1,25) | 0,02 | +13,74 [+6,63; +21,61] | −2,53 [−7,83; +1,92] | 117 | 41,8 % | 19,1 % | 12,9 % | +18,23 % | +23,23 |
| 25 (1,25) | 0,05 | +16,27 [+8,22; +25,13] | – | 101,5 | 22,4 % | 20,1 % | 5,0 % | +16,55 % | +23,77 |
| 25 (1,25) | 0,10 | +16,63 [+7,32; +27,33] | +0,36 [−2,24; +2,99] | 83,6 | 5,0 % | 20,7 % | 1,0 % | +16,49 % | +25,51 |
| 25 (1,25) | 0,15 | +16,30 [+7,27; +26,71] | +0,04 [−2,44; +2,43] | 80,8 | 0,8 % | 21,1 % | 0,1 % | +15,93 % | +24,96 |

- tol 0,05 reproduceert E63 exact (poort T1).
- Met pessimistische kosten is het beeld hetzelfde (tol.md). Ook daar bevat elk gepaard verschil met 0,05 de nul.

## 3. Waar het verschil vandaan komt (zonder limieten, base, op (pool, t))

| lead, C | tol | totaal Δ SOL/dag | nieuw: n, SOL/dag, leeglopers | verloren: n, SOL/dag, leeglopers | gemeenschappelijk: n, Δ SOL/dag, Δ opbrengst |
|---|---|---|---|---|---|
| H5, 10 | 0,02 | −0,95 | 249, +1,35, 12,4 % | 473, +2,38, 17,8 % | 2.351, +0,10, +0,13 % |
| H5, 10 | 0,10 | +0,41 | 385, +1,88, 16,4 % | 242, +1,36, 12,8 % | 2.582, −0,11, −0,09 % |
| H5, 10 | 0,15 | +0,56 | 461, +2,30, 17,1 % | 283, +1,64, 13,4 % | 2.541, −0,11, −0,04 % |
| C3, 10 | 0,02 | −0,33 | 336, +2,01, 17,6 % | 596, +2,41, 21,8 % | 1.704, +0,08, +0,14 % |
| C3, 10 | 0,10 | +0,76 | 493, +2,97, 20,5 % | 317, +2,18, 15,8 % | 1.983, −0,05, −0,09 % |
| C3, 10 | 0,15 | +0,55 | 584, +3,24, 21,9 % | 377, +2,66, 16,4 % | 1.923, −0,05, −0,08 % |

(25 SOL: hetzelfde patroon, ≈ 2,3 × groter; tol.md.)

- **Gemeenschappelijk = het uitstapbeen.** De instapkosten zijn bij geen enkele gemeenschappelijke positie anders. De opbrengst verschuift gemiddeld ±0,1 %. Een lossere uitstap-tolerantie kost dus bijna niets.
- **Nieuw tegen verloren = de instap-set.**
  - Bij 0,10 / 0,15 komen er posities bij die 5 % weigerde. Per positie (afgeleid: SOL/dag × 20 / n, op 0,5 SOL): H5 ≈ +20 %, C3 ≈ +22–24 %. Dat is boven het gemiddelde en past bij de bevinding van de audit.
  - Tegelijk vallen er latere posities in dezelfde pools weg, omdat er één open positie per pool is. Die waren per stuk minstens even goed (2–5 punten beter): H5 ≈ +22–23 %, C3 ≈ +27–28 %.
  - Bij 0,02 is het omgekeerd: er vallen meer winstgevende posities weg dan er bijkomen.
- **De rest** (fees van mislukte instappen) is ≤ ±0,02 SOL/dag.

## 4. Kanttekeningen

- In-sample op verbruikte dev, vóór Alpenglow; H5 en de arm zijn op deze data gekozen.
- Een lossere tolerantie accepteert slechtere vullingen, dus meer sandwichruimte (niet gemodelleerd). Een winst bij 0,15 is een bovengrens.
- Eén tolerantie voor beide benen. Een lossere tol laat ook een take-profit-verkoop verder onder zijn mark vullen.
- De bezet-ketens verschillen per tol (andere posities bestaan).
- K3 zit niet in deze taak.
- Het live triggeraanbod is ≈ 20–29 % van dev's.
- Er gaat niets naar paper zonder eigenaarsbesluit; paper blijft op 0,05.

## 5. Bestanden

- Plan: `research/tol-sweep-plan-2026-10-24.md` (35ca706b…).
- Runs: `backtests/tolsweep-t002-e3bef48/`, `-t010-` en `-t015-`.
- Rapport: `backtests/tol-sweep-e3bef48/report/tol.md` + `tol.json` (SHA256SUMS 77e11b62…).
- tol 0,05: E63's `backtests/capital-ceiling-d15ac45/`.
- Droogruns op de VPS (geen bewijs): `tol-sweep-bb31ea7-dry/` en `tol-sweep-e3bef48-dry/`.
