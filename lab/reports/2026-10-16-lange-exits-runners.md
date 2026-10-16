# Lange exits, runners en de H4-controles (jobs 82/83, 8155, 816)

Datum 2026-10-16. Sessie LAB research (roadmap #167). Alles hieronder is dev-data (epochs 1033–1046, dag 10–18 verbruikt); niets is een kandidaat. Labels: **feit** (uit de data of de code), **reconstructie** (kostenmodel, replay), **modelaanname**, **onbekend**.

Bronnen: job 82 `long-dev-1033-1046-fc4173c` (engine fc4173c, F7 v1 + lange grid, M1/M1E/M1P, d 1–2, 0,5 SOL, q 0,5), job 83 `wallets/long-v1/` (exits_report 9a85d00: exits.md, overfit.md), job 8155 `wallets/runners-v1/` (runners d6e9842), job 816 `wallets/transfers-v1/epoch-{1040,1045}/` (transfers e22e5d7). Scripts van de controles: `lab/wallets/runners.py`, `runners_report.py` (deze PR) en de scratch-queries in de PR-tekst.

## 0. Samenvatting

- **Lange exits (spec F §3, E28):** geen variant haalt de lat van spec E §2 (dag 10–18, farm alle, d = 1 én 2). Trails (20/30/50 %, 1 u / 6 u) en `_sl8` zijn overal negatief. De enige positieve lange exit is **`flow300_H6` op organic pools**: Σ +7,20 / +6,20 SOL per dag (d = 1 / 2) op dag 10–18 en +12,06 / +10,14 op dag 1–9, met 22 % drains. M1/M1E/M1P: geen lange exit met CI > 0 in welk blok dan ook; lange exits redden M1 niet.
- **H4-controles (F7 pool_reversed, organic, flow300_H6):** de organic/flagged-splitsing is point-in-time (code); positief in beide dagblokken en bij d = 1 én 2; 75–82 % van de dagen > 0; slechtste dag −4,4 SOL (dag 10–18) en −8,7 SOL (2026-10-01, buiten de blokken); top-10 pools = 31–33 % van de netto winst op dag 10–18 maar 56–58 % op dag 1–9 (één pool +20 SOL); top-1 % posities = de helft van de netto winst. **hold180 organic (E25) haalt ≈ 90 % hiervan** (+6,67 / +5,50 SOL per dag op dag 10–18): H3 dekt het grootste deel van H4.
- **Runners (spec F §1, E27):** 592.247 instappen op 279.347 tokens. De F7-multiples (59 % ×2 binnen 1 u) zijn echt én waardeloos: 99 % van die instappen staat na 6 u op ≤ 5 % van de kosten (pump-then-drain). De mark-formule is per trigger tegen de engine gecontroleerd (proceeds op de exit-staat: mediaan ratio 0,995). Eén fout in de module: f7/m1/m1e namen alleen de eerste trigger per pool (3.354 van 38.515 F7-triggers); gefixt in deze PR, herdraai als `runners-v2` op de NAS. Leads met het herstelde criterium: 79, allemaal activiteitskenmerken op c1/c5/c15, geen op f7/m1/m1e. AUC is geen winst: de kwintieltabel krijgt nu de netto SOL van drie eenvoudige exits (hold / tp ×2 / trail 30 / trail 50); die kolom komt met `runners-v2`.
- **E26-correctie (job 816):** 4.1 100,0 % op 1040 en 1045 (gehaald); 4.2 409/669 en 366/574 exact (= job 79, niet exact, oorzaak open); Q1(b) 25,0 % / 48,4 % (= job 79, lat 80 %, niet gehaald). Transfers gesloten, geen nieuwe fixronde.

## 1. Lange exits (job 82/83; spec F §3)

Grid: F7 v1 (tp4/6/10 × T150/600/2400) plus `trail20_H6`, `trail30_H1`, `trail30_H6`, `trail50_H6`, `flow300_H6`, `flow300_H6_sl8`; M1P k15/k20, M1E m1/m2/m5, M1 k2/k3 elk × trail20/30/35/50 en flow300. Referentie per familie: F7 `tp4_T600`, M1x de eigen `_trail20`. Positie-eenheid: trigger × d × farm; N2-tweelingen vallen buiten het (family, segment)-filter van het rapport en staan alleen in overfit.md. `window_end` = periode-einde (4 F7-posities). M1P-varianten tellen als trials alleen als ze posities geven (k15, k20: 20 trials).

### 1.1 F7 pool_reversed, dag 10–18 (bindend) — reconstructie

| variant | d | farm | n | netto trim | 95 % CI (dagen) | Σ SOL / dag | empty | > 0 | slechtste dag | runners ×5 / gevangen | Σ SOL/dag runners / drains / collapses / rest |
|---|---|---|---|---|---|---|---|---|---|---|---|
| tp4_T600 (ref) | 1 | organic | 8.984 | +1,9 % | +1,3 … +2,3 | +6,39 | 0,4 % | 72 % | −1,1 | 0,0 % / 100 % | +0,21 / −1,82 / −0,10 / +8,11 |
| **flow300_H6** | 1 | organic | 2.080 | **+5,8 %** | +1,3 … +10,1 | **+7,20** | 22,3 % | 59 % | −4,4 | 1,4 % / 52 % | +3,67 / −21,15 / −1,12 / +25,71 |
| flow300_H6 | 2 | organic | 2.033 | +5,1 % | +0,6 … +9,4 | +6,20 | 21,9 % | 57 % | −5,4 | 1,1 % / 57 % | +2,76 / −20,29 / −1,03 / +24,67 |
| flow300_H6 | 1 | flagged | 3.040 | −2,1 % | −10,9 … +6,3 | −0,83 | 22,5 % | 54 % | −67,7 | 0,5 % / 50 % | +1,24 / −31,23 / −1,05 / +29,94 |
| flow300_H6 | 1 | alle | 5.120 | +1,1 % | −5,7 … +7,0 | +6,37 | 22,4 % | 56 % | −64,9 | 0,8 % / 51 % | +4,90 / −52,38 / −2,17 / +55,64 |
| trail30_H6 | 1 | organic | 2.590 | −9,5 % | −12,8 … −6,6 | −8,55 | 15,3 % | 36 % | −18,2 | 2,2 % / 39 % | +2,85 / −18,05 / −0,34 / +5,49 |
| trail50_H6 | 1 | organic | 1.754 | −27,7 % | −32,0 … −24,6 | −20,03 | 35,9 % | 30 % | −33,7 | 9,1 % / 1 % | +5,50 / −28,67 / −3,96 / +3,59 |

Dag 1–9 (toets): `flow300_H6` organic +18,3 % / +15,7 % (d = 1 / 2; CI +10,2 … +26,9 en +7,7 … +24,9), Σ +12,06 / +10,14 SOL per dag, empty 19 %; flagged −6,7 % / −7,1 %; trail50 −27,8 % / −28,9 %. Per regime (job 82 report.md, alle dagen, netto SOL per trade): 270 ms d1 +0,0435 [+0,019 … +0,072] (n 2.427), d2 +0,0360 [+0,014 … +0,059]; 320 ms d1 +0,132 [+0,060 … +0,172] (n 418), d2 +0,100 [+0,035 … +0,141]. Win 58 %, de top-1 % posities leveren ≈ 60 % van de P&L.

Lat (spec E §2, dag 10–18, farm alle, d = 1 én 2, empty ≤ ref + 2 pt): **geen enkele variant**; `tp6/tp10_T600` en `_T2400` halen Δ > 0 alleen bij d = 2 (verdict-tabel in exits.md). `flow300_H6` faalt de lat op farm alle (CI raakt 0) en op empty (22 % tegen 0,4 %).

Overfit (overfit.md, trials = variant × d × farm, T = 20 dagen): F7 60 trials, beste `flow300_H6 d1 organic`, SR 0,83, **DSR(trials) 0,00, DSR(1/T) 0,92, PBO 0,05**; de bekend slechte armen (flagged, trails) blazen SR0(trials) op, dus de DSR(trials) is hier te streng en de PBO te mild. M1 20 trials DSR 0,00 / PBO 0,73; M1E 30 trials DSR 0,00 / PBO 0,16; M1P 20 trials DSR 0,05 / PBO 0,07 (beste `k20_trail20 d2`, SR 0,10).

### 1.2 M1P / M1E / M1 — reconstructie

Dag 10–18, d = 1 (exits.md; m1x.md geeft dezelfde cijfers met de gate-maten): M1P `k15_flow300` −0,7 % (CI −12,6 … +10,2; n 140), `k15_trail30` −0,6 %, `k15_trail50` −6,5 %, `k20_flow300` +7,1 % (CI −10,8 … +25,9; n 87); M1E `m1_flow300` −4,9 % (CI −11,0 … +0,0; n 312), `m1_trail50` −10,2 %, `m5_flow300` −15,0 %; M1 `k3_trail50` is de beste M1-trial met SR −0,53. Dag 1–9: M1P k15/k20 flow300 −12 … −17 %, trail50 −11 %. Geen M1x-variant heeft in enig blok een CI boven nul. De runners-meting zegt waarom: 40 % van de m1/m1e-instappen haalt ×2, maar 71 % (m1) en 53 % (m1e) van die ×2-instappen staat na 6 u op ≤ 5 % (§3.2).

## 2. H4-controles op dev (coördinator 2026-10-16)

Voorstel H4: "F7 pool_reversed, farm organic, exit flow300_H6", hoofdmaat Σ netto SOL per UTC-dag > 0 met dag-bootstrap-ondergrens > 0, d_ms 600 als E20, absolute toets (niet tegen tp4_T600), pin op fc4173c, clean-job bij Lab Backtest. De controles:

### 2.1 Point-in-time van organic / flagged — feit (code)

- Farmer-vlag per trade (`lab/backtest/store.py`, FARMER_RATIO 0,05, FARMER_MIN_TRADES 2): een wallet is farmer in een pool als over **zijn eigen trades in de 24 u vóór de trade** |netto tokens| < 5 % van het bruto volume, bij ≥ 2 trades; het venster is `RANGE BETWEEN 24h PRECEDING AND 1 PRECEDING`, dus alleen terugkijkend.
- Pool-label per trigger (`strategies.py f6_signals_sql`, F7 deelt die functie): `flagged` = farmer-SOL-volume > 50 % van het SOL-volume over `[t − 1 u, t)` (`1 PRECEDING`). Het label hoort bij de trigger, niet bij de pool of de periode.
- Consistent daarmee: van de 3.325 F7-pools met `flow300_H6`-posities dragen er 846 beide labels over hun triggers (3.844 van de 8.093 triggers); organic 2.845 triggers op 1.942 pools, flagged 5.248 op 2.229.

### 2.2 Dagblokken, d = 1 en 2, staart — reconstructie (job 82 parts, settle_sql base)

Σ netto SOL per UTC-dag (dagen zonder trade tellen 0), F7 organic:

| exit | d | blok | n | Σ / dag | dagen > 0 | slechtste dag | p5 dagen | beste dag | mediaan dag |
|---|---|---|---|---|---|---|---|---|---|
| flow300_H6 | 1 | dag 1–9 | 706 | +12,06 | 75 % | −1,34 | −1,26 | +34,29 | +10,80 |
| flow300_H6 | 1 | dag 10–18 | 2.080 | +7,20 | 82 % | −4,36 | −2,82 | +18,83 | +8,60 |
| flow300_H6 | 1 | alle 20 | 2.845 | +8,35 | 75 % | −8,72 | −4,57 | +34,29 | +8,47 |
| flow300_H6 | 2 | dag 1–9 | 676 | +10,14 | 75 % | −1,33 | −1,18 | +29,84 | +8,45 |
| flow300_H6 | 2 | dag 10–18 | 2.033 | +6,20 | 82 % | −5,39 | −3,79 | +17,66 | +8,46 |
| flow300_H6 | 2 | alle 20 | 2.766 | +7,06 | 75 % | −8,13 | −5,53 | +29,84 | +8,37 |
| tp4_T600 | 1 | dag 10–18 | 8.984 | +6,39 | 91 % | −1,10 | −0,03 | +20,30 | +3,48 |
| tp4_T600 | 2 | dag 10–18 | 8.376 | +2,26 | 64 % | −1,78 | −1,32 | +9,78 | +1,07 |
| hold180 (E25) | 1 | dag 1–9 | 845 | +8,92 | 88 % | −1,67 | +0,57 | +21,16 | +8,30 |
| hold180 (E25) | 1 | dag 10–18 | 2.575 | +6,67 | 82 % | −4,96 | −3,87 | +18,93 | +4,82 |
| hold180 (E25) | 2 | dag 1–9 | 810 | +8,58 | 88 % | −1,67 | +0,48 | +19,79 | +7,34 |
| hold180 (E25) | 2 | dag 10–18 | 2.512 | +5,50 | 82 % | −5,29 | −5,19 | +18,59 | +4,63 |

- Positief in beide blokken, bij d = 1 én d = 2, in beide regimes (§1.1). Het blok dag 10–18 draagt de kleinere winst per dag (+7,2 tegen +12,1).
- **De slechtste dag ligt buiten de blokken:** 2026-10-01 (dag 20 van dev) −8,72 SOL bij d = 1, −8,13 bij d = 2. Binnen dag 10–18 is de slechtste dag −4,4 / −5,4 SOL, p5 van de dagen −2,8 / −3,8. Eén slechte dag kost dus ruwweg een volle goede dag.
- Drains: 19–22 % van de posities (exits.md `empty`; p5 van de posities −99,8 %), tegen 0,1–0,5 % bij tp4_T600. Splitsing Σ SOL/dag op dag 10–18, d = 1: runners (×5) +3,67, drains −21,15, collapses −1,12, rest +25,71. De winst zit in de brede "rest" (veel kleine plussen), de drains vreten er driekwart van op; de runners zelf zijn 1,4 % van de posities en 52 % daarvan wordt gevangen (exit ≥ 50 % van de piek).
- Exit-redenen flow300_H6 organic d = 1, dag 10–18: 100 % `outflow` (de regel stopt op de 5-min organic-outflow); gemiddeld +7,6 %, 22,3 % drains.

### 2.3 Concentratie — reconstructie

| d | blok | pools | Σ netto | Σ top-10 pools | aandeel in netto | aandeel in bruto plus | beste pool |
|---|---|---|---|---|---|---|---|
| 1 | dag 1–9 | 553 | +96,5 | +54,3 | 56 % | 31 % | +20,25 SOL, 1 trigger |
| 1 | dag 10–18 | 1.353 | +79,2 | +24,8 | 31 % | 8 % | +3,94 SOL, 1 trigger |
| 2 | dag 1–9 | 524 | +81,1 | +47,3 | 58 % | 30 % | +18,23 SOL, 1 trigger |
| 2 | dag 10–18 | 1.323 | +68,2 | +22,7 | 33 % | 8 % | +3,05 SOL, 1 trigger |

Per positie: de top-10 posities zijn 31–32 % van de netto winst op dag 10–18 en 57–59 % op dag 1–9; **de top-1 % posities zijn 49 % (dag 10–18) en 50 % (dag 1–9) van de netto winst.** De dag-1–9-uitkomst hangt aan één pool (+20 SOL op één trigger); dag 10–18 is breder gedragen maar nog steeds een staartverdeling (payoff 0,91, win 59 %).

### 2.4 hold180 organic tegenover flow300 organic

hold180 (E25, exits-dev-befacff, zelfde triggers en kosten) organic: dag 10–18 +6,67 / +5,50 SOL per dag (d = 1 / 2) tegen flow300_H6 +7,20 / +6,20; dag 1–9 +8,92 / +8,58 tegen +12,06 / +10,14; slechtste dag −4,96 / −5,29 tegen −4,36 / −5,39; dagen > 0 82 % in beide. **hold180 organic is op dag 10–18 ≈ 90 % van flow300 organic**; het verschil zit in dag 1–9 (de +20-SOL-pool). H3 (hold180 op clean, pin dbef1b8) dekt dus het grootste deel van wat H4 zou toetsen; H4 voegt vooral de exit-regel (outflow-stop, 6 u cap) en het organic-filter als hoofdregel toe.

### 2.5 Oordeel over het H4-voorstel

De dev-controles houden stand: point-in-time label, positief in beide blokken en bij beide d, beide regimes. Kanttekeningen die in de vastlegging horen: (1) de staart (top-1 % posities = de helft van de winst, drains 22 %, slechtste dag −8,7 SOL direct na het blok); (2) F7 heeft met `tp4_T600` de hold-out al eens gemist nadat het op dev DSR en PBO doorstond (overfit.md): H4 is een hypothese op een familie die eerder faalde; (3) N voor de DSR = alle F7-trials tot nu (E20 + 108 + 36 + 60); (4) hold180 organic doet bijna hetzelfde, dus H3 en H4 zijn geen onafhankelijke toetsen.

## 3. Runners (job 8155, spec F §1, E27)

### 3.1 Wat er staat — reconstructie

592.247 instappen op 279.347 tokens, 21 UTC-dagen; c1 276.008, c5 189.877, c15 121.947 (94 / 91 / 85 % op de curve), f7 3.354 (alleen de eerste trigger per pool, zie §3.3), m1 208, m1e 853. Mayhem-mints (≈ 28 % van de dev-mints) krijgen geen curve-instap (datanotitie register §1, #285): de agent reset de virtuele SOL in zijn eigen trades; hun pool-fase telt wel mee.

Dag 10–18, 270 ms (runners.md §2):

| moment | segment | n | ≥×2 1u | ≥×5 6u | ≥×10 6u | t_max med (min) | end_6h med | end ≥ kosten | ×2 binnen 1 u en na 6 u ≤ 5 % |
|---|---|---|---|---|---|---|---|---|---|
| c1 | curve | 161.670 | 5,1 % | 1,39 % | 0,51 % | 0,0 | −0,001 | 1,5 % | 13 % |
| c1 | pool | 9.000 | 23,9 % | 7,50 % | 2,52 % | 5,3 | 0,005 | 7,4 % | 82 % |
| c5 | curve | 108.431 | 3,4 % | 1,00 % | 0,39 % | 0,0 | −0,001 | 1,8 % | – |
| c15 | curve | 60.411 | 2,6 % | 0,92 % | 0,38 % | 0,0 | 0,004 | 2,6 % | 8 % |
| f7 | pool_reversed | 2.065 | 59,2 % | 20,39 % | 4,99 % | 12,8 | −0,001 | 0,4 % | 99 % |
| m1 | pool | 135 | 36,3 % | 9,63 % | 5,19 % | 13,0 | 0,024 | 11,9 % | 71 % |
| m1e | pool | 564 | 38,7 % | 11,70 % | 3,19 % | 4,6 | 0,031 | 7,4 % | 53 % |

De laatste kolom is de pump-then-drain-maat (alle dagen, niet-afgekapte instappen): van de instappen die binnen 1 u ×2 halen, het aandeel dat na 6 u op ≤ 5 % van de kosten staat. Op reversed pools (f7) is dat 99 %, op de pools van jonge tokens 74–82 %, op de curve 8–13 %. De F7-multiples zijn dus geen mark-fout maar het patroon pump → leegtrekken; dat is ook waarom `trail50_H6` in de engine −28 % doet terwijl 57 % van dezelfde triggers ×2 haalt: de drain is één stap, de trail verkoopt erna.

### 3.2 Mark en instap tegen de engine — feit

- Instap: van de 37.672 gevulde `tp4_T600 d=1`-posities (f7rev) matchen 3.242 op (pool, t) met een runners-instap; kosten identiek (100 %), tokens ratio 1,000 (exact ±1 bij 32 %, de rest afrondingsverschil van de budget-formule). `tp4` in de engine is +4 %, niet ×4 (take_profit: proceeds/cost mediaan 1,061; 71 % van de posities).
- Mark-formule: de runners-verkoop (pool-post-state, reversed-pool-formule van runners.py) van de engine-tokens op de laatste pool-rij ≤ exit_slot, tegen de engine-proceeds van 1.031 posities in een venster van 1040: mediaan ratio 0,995, 81 % binnen 1 %, p1–p99 0,91–1,09 (de engine verkoopt op de slot-staat, de runners op de laatste trade-staat: 27–54 % van de exits valt in hetzelfde slot).
- Pieken (job 82 `peak_mark`): bij `trail50_H6 d=1` ligt de runners-max_6h ≥ de engine-piek bij 91,7 % van de 3.230 paren, bij `flow300_H6` 95,2 %; de engine-piek is lager omdat haar posities eerder sluiten (trail mediaan 1.429 slots ≈ 6–7 min, flow300 1.126). Engine ≥×2 42 % tegen runners 57 % (6 u), ≥×5 7,2 % tegen 18,9 %. Bij 8 % ligt de runners-max onder de engine-piek, mediaan 5 % van de kosten (**onbekend**: de engine markt op slot-staten die de trade-marks niet zien; klein, niet verder uitgezocht).
- m1/m1e tegen M1P/M1E `*_trail50 d=1`: kosten identiek, tokens gelijk bij 73 % (M1P) en 19 % (M1E) — op normale pools met veel trades verschilt de instap-staat (engine op slot t+1, runners op de eerste trade ≥ 600 ms); runners-max ≥ engine-piek bij 78–86 %. Zelfde beeld, geen schone reconciliatie (modelverschil in de instap, geen fout).

### 3.3 Fout en fix: één instap per trigger

runners.py nam per (pool, moment) de eerste trigger: 3.354 f7-instappen op 3.339 pools tegen 38.515 engine-triggers; m1 208 van 219, m1e 853 van 1.233. Deze PR maakt de eenheid (key, moment, t), dus elke trigger telt (zoals in de engine; test `test_reversed_pool_f7_entry` met twee triggers op één pool). De multiples van §3.1 voor f7/m1/m1e zijn daardoor de multiples van *eerste* triggers; de herdraai (`runners-v2`, NAS-commando bij de PR) geeft de per-trigger-cijfers. Voor c1/c5/c15 verandert niets.

### 3.4 Leads — reconstructie, herscoring

Het criterium "AUC ≥ 0,65 in elk regime, dag 10–18" faalde altijd: 320 ms bestaat alleen op dag 1–9. Nu wordt elk regime op zijn eigen blok gescoord (270 ms dag 10–18, 320 ms dag 1–9) en telt een cel alleen mee met ≥ 30 runners. Herscoring van runners-v1 (10.000 dag-draws): **79 leads, allemaal c1/c5/c15**, geen op f7 (421 runners, AUC's 0,40–0,61), m1 (13 runners, onder de drempel) of m1e (66 runners; `post5_inflow` 0,81 in 270 ms maar niet in 320 ms). Sterkste: `post5_buyers` 0,92 / 0,89 (c1, 270 / 320 ms), `post5_sellers` 0,89 / 0,86, `depth_sol` 0,84–0,86, `all_inflow` 0,83–0,86, `holders` 0,82–0,84, `top5_share` 0,22–0,24, `min_since_create` 0,17–0,21. Dat zijn activiteitskenmerken: ze scheiden levend van dood, niet winstgevend van verliesgevend (kwintiel 5 van c1 `post5_buyers`: 7,7 % ×5, gemiddelde end_6h 0,35; vasthouden verliest ook daar ≈ 65 %).

### 3.5 AUC is geen winst: eenvoudige exits per kwintiel

runners.py berekent nu per instap drie exits op dezelfde marks (modelaanname: verkoop op de staat van de triggerende mark, zonder slot-vertraging, dus iets optimistisch): `x_tp2` (eerste mark ≥ 2 × kosten), `x_tr30` / `x_tr50` (eerste mark ≤ 70 / 50 % van de hoogste eerdere mark), anders het 6-u-einde. Het rapport zet per kwintiel de netto SOL per instap en per dag voor hold / tp ×2 / trail 30 / trail 50. runners-v1 heeft die kolommen niet; ze komen met `runners-v2`. Smoke op 20.000 slots van 1040 (1.349 instappen, c1-kwintielen van 108): kwintiel 5 van `post5_buyers` hold −0,39 SOL per instap, tp ×2 +0,14, trail 30 −0,06, trail 50 −0,08; kwintiel 1–4 bij elke exit −0,29 … −0,46. Te klein voor een conclusie; het patroon (alleen een ×2-take-profit in het hoogste kwintiel boven nul, en dat is een hold-beslissing 5 min ná de instap) is wat de volledige run moet bevestigen of ontkrachten.

### 3.6 Caveats

Kostenmodel base (landing en gefaalde transacties niet gemodelleerd); mult = gesimuleerde volle verkoop met cap op de echte SOL; c-momenten zijn een meetbasis; share_c en de wash-vlaggen zitten er niet in (onbekend); 6-u-holds kruisen dagranden; Alpenglow (epoch 1064) nooit gepoold met dev. De caveat-tekst in runners.md over "vq unusable / rebuilt from the real reserves" is vervangen door de #285-tekst.

## 4. E26-correctie: job 816 (transfers #281 + #282, engine-insiderset) — feit

| check | job 79 | job 815 (curve-only insiders) | **job 816 (engine-set)** | lat |
|---|---|---|---|---|
| 4.1 conservatie 1040 / 1045 | – | 99,8 % / 99,8 % | **100,0 % (68.159/68.159) / 100,0 % (114.303/114.303)** | ≥ 99,9 % ✔ |
| 4.2 oude share exact 1040 / 1045 | 409/669, 366/574 | 540/669, 480/574 | **409/669, 366/574** | exact ✘ |
| Q1(b) 1040 / 1045 | 25,0 % (21/84), 48,4 % (30/62) | 23,9 %, 38,3 % | **25,0 % (21/84), 48,4 % (30/62)** | ≥ 80 % ✘ |
| share_c > 0,90 (triggers) | oude share 22 % / 20 % | 3,6 % / 1,9 % | 15,6 % (120/770) / 13,7 % (87/633) | – |
| legs-only share > 1 | – | – | 97 / 71 | – |

4.1 is gehaald (de balansregel van #281). 4.2 valt met de engine-set terug op precies de job-79-cijfers: de afwijking zit dus niet in de insiderset maar elders (circulatie, venster of de oude M1x-share-berekening; **onbekend**, open gelaten). Q1(b) blijft in elke insiderdefinitie ver onder 80 %. Afspraak: transfers gesloten, geen nieuwe fixronde; share_c (engine-set) is het veto-kenmerk als het ooit wordt gebruikt, met 14–16 % van de triggers boven 0,90.

## 5. Herdraai runners (NAS)

```
git fetch origin && git checkout <merge-sha van deze PR>
OUT_DIR=$DATA/wallets/runners-v2 LAB_SPILL_DIR=/lab/tmp/nas-runners LAB_SPILL_MAX=50GB bash lab/wallets/nas_runners.sh
```

Nieuwe map (`runners-v2`): het script slaat bestaande epoch-bestanden over, dus niet in `runners-v1` draaien. Verwacht ≈ 20–30 min (14 epochs à 1–2 min plus het rapport); f7 ≈ 38.000 instappen, m1/m1e per trigger. Watcher kopieert `entries-*.parquet`, `run-runners-*.json`, `runners.md`, `runners.json`, `SHA256SUMS` naar de VPS.
