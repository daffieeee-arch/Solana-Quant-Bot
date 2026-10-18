# Tijd tot de drain op het triggermoment: niet te voorspellen, en een veto erop kost winst (agenda-idee 1, stap 2–3)

LAB research, 2026-10-17. Vervolg op E34 (creator-klasse) en E36 (elke reversed pool wordt leeggetrokken; het voordeel is "wij eerst"). Vraag: kan de tijd tot de LP-drain op het triggermoment t point-in-time worden geschat, en helpt een veto op de riskantste triggers H4/H5? Alleen dev 1033–1046; fit op dag 1–9, meting op dag 10–18; hold-out en clean niet gelezen. Verkennend; register E37. Labels **feit** / **reconstructie** / **modelaanname**. Scripts `lab/wallets/explore/drain_features.py`, `drain_model.py`.

> **Correctie 2026-10-18 (Codex-bevinding 7, bevestigd):** het kenmerk `creator_never_traded` kwam uit `k2_pools.parquet` als `creator_n_trades IS NULL`, geteld over de hele dev-periode (ook ná t): geen point-in-time kenmerk. Het zat in de AUC-tabel (0,563 / 0,518) en in de logistische en LightGBM-fits. Een lekkend kenmerk kan de AUC alleen opblazen, dus de echte point-in-time AUC's zijn hooguit gelijk (≤ 0,63) en de conclusie (niet te voorspellen op t, veto kost winst) blijft; `drain_features.py` gebruikt sindsdien `creator_traded_before` (point-in-time); niet herdraaid.

## 0. Samenvatting

- **Op t is de drain nauwelijks te voorspellen.** Over 3.383 H4/H5-triggers (dag 10–18: 1.200) valt de drain mediaan 22,7 min na t; 33 % binnen 10 min, 72 % binnen 60 min. Geen enkel point-in-time kenmerk haalt een AUC boven 0,57 (beste: `n_trades_1m` 0,565 voor 60 min, `creator_never_traded` 0,563 voor 10 min; leeftijd van de pool 0,40 / 0,52; b0 0,52 / 0,44). Een logistisch model geeft AUC 0,59 (10 en 60 min), LightGBM 0,52 / 0,63 — *feit*.
- **Een veto op de riskantste triggers maakt het slechter, bij elke drempel.** Het wegnemen van de 10 % hoogste drain-scores (LightGBM, 60 min) kost H5 d = 1 op dag 10–18 Σ 34,4 → 25,9 SOL; bij 20 % 34,4 → 10,0; bij 50 % → −6,1. Het verlies-alles-aandeel daalt niet (10,0 % → 10,7 … 16,1 %), de slechtste dag verbetert hooguit 1–2 SOL. H4 en d = 2 idem; de logistische score idem — *modelaanname (dagsommen uit de dev-parts, costs base)*.
- **Lezing.** De pools die snel draineren zijn dezelfde pools die snel pumpen: de winst van H4/H5 zit in precies de posities met een hoog drain-risico, en ons voordeel is de volgorde (uitstappen vóór de drain) die flow300 al regelt. Een veto vooraf kan die volgorde niet zien. Idee 1 sluit als **veto**; wat blijft is de exit tijdens de positie, en die hebben we (flow300, tp100). Geen vastlegging — *reconstructie*.

## 1. Methode (feit)

- Triggers: de 3.383 unieke (pool, t) van de H4/H5-dev-posities (organic, d 1/2; H5 `bigtp-dev-1033-1046-4dbd34b`, H4 `long-dev-1033-1046-fc4173c`).
- Kenmerken, alleen uit rijen met slot < t: leeftijd van de pool, b0, creator (eerdere pools, ooit gehandeld, onbekend = system-program, eigen trades en netto-instroom in de pool), aantal trades/traders/kopers sinds de creatie, instroom en volume sinds de creatie en in de laatste 1 en 5 min, trades in de laatste minuut, tip- en farmer-aandeel, eerdere withdraws/deposits, diepte op t en maximale diepte, grootste koop en verkoop.
- Uitkomst: eerste withdraw ≥ 50 % van de reserve ná t, in minuten; y10 / y60 = drain binnen 10 / 60 min.
- Modellen: logistische regressie (statsmodels, gestandaardiseerd) en LightGBM (200 rondes, 15 bladeren), gefit op dag 1–9 (2.183 triggers), gescoord op dag 10–18 (1.200). AUC per kenmerk en per model op dag 10–18.
- Overlay: per (h, d) de posities van dag 10–18 met een score (1.053–1.162 per cel; de rest van de posities van die dagen viel buiten de kenmerktabel door de dag-definitie van de pool-creatie); veto = score boven het (1−q)-kwantiel van de testscores, q ∈ {0,1; 0,2; 0,3; 0,5}; Σ, slechtste dag, verlies-alles, gemiddelde dagsom met de ondergrens van de dagbootstrap (2.000 trekkingen).

## 2. Voorspelbaarheid op t (feit, dag 10–18)

| kenmerk | AUC drain ≤ 10 min | AUC drain ≤ 60 min |
|---|---|---|
| leeftijd pool (min) | 0,395 | 0,516 |
| b0 (startliquiditeit) | 0,515 | 0,441 |
| creator: eerdere pools | 0,530 | 0,385 |
| creator nooit gehandeld (periode-kenmerk, zie correctie bovenaan) | 0,563 | 0,518 |
| creator onbekend | 0,550 | 0,429 |
| traders sinds creatie | 0,418 | 0,549 |
| instroom sinds creatie | 0,476 | 0,561 |
| instroom laatste 5 min | 0,559 | 0,531 |
| trades laatste minuut | 0,552 | 0,565 |
| tip-aandeel / farmer-aandeel | 0,494 / 0,494 | 0,556 / 0,553 |
| grootste koop / verkoop vóór t | 0,454 / 0,501 | 0,552 / 0,505 |
| **logistisch model** | **0,588** | **0,592** |
| **LightGBM** | **0,521** | **0,632** |

(AUC < 0,5 = het kenmerk wijst de andere kant op.) LightGBM-gewicht vooral op creator-eerdere-pools, grootste verkoop, trades laatste minuut, instroom 5 min, b0, leeftijd.

## 3. Veto-overlay op dag 10–18 (modelaanname)

LightGBM-score "drain ≤ 60 min", veto op het hoogste deel q; Σ netto SOL over de posities met score, slechtste dag, verlies-alles:

| h | d | q = 0 (geen veto) | q = 0,1 | q = 0,2 | q = 0,3 | q = 0,5 |
|---|---|---|---|---|---|---|
| H5 | 1 | 34,4 (−7,5; 10,0 %) | 25,9 (−5,6; 10,7 %) | 10,0 (−5,9; 11,1 %) | 6,1 (−6,4; 12,6 %) | −6,1 (−12,5; 16,1 %) |
| H5 | 2 | 25,7 (−6,9; 15,7 %) | 20,4 (−5,7; 15,4 %) | 5,9 (−6,0; 14,9 %) | 2,4 (−6,4; 16,4 %) | −8,6 (−12,7; 19,1 %) |
| H4 | 1 | 16,6 (−8,7; 10,4 %) | 14,9 (−7,2; 11,2 %) | 2,6 (−6,6; 11,5 %) | −4,4 (−7,4; 12,9 %) | −14,4 (−14,0; 16,6 %) |
| H4 | 2 | 10,3 (−8,1; 16,0 %) | 11,2 (−7,4; 15,5 %) | −1,7 (−6,9; 14,9 %) | −8,4 (−7,5; 16,3 %) | −16,0 (−14,2; 19,2 %) |

Score "drain ≤ 10 min" en de logistische score: hetzelfde patroon (q 0,1: −2 … +4 SOL; q ≥ 0,2: vrijwel altijd slechter; q 0,5: −12 … −25 SOL). De ondergrens van de dagbootstrap is bij elke variant negatief (−1,4 … −6,4), ook zonder veto: dag 10–18 op de posities met score is per dag niet van nul te onderscheiden.

## 4. Oordeel en wat blijft

- Idee 1 als **veto vooraf**: gesloten. De drain-timing is op t niet te zien (AUC ≤ 0,63) en wat de score wel ziet, zijn de pools die ook de winst brengen.
- De creator-klasse "onbekend" uit E34 blijft een **universum-feit** (80–93 % binnen 7 min gedraineerd) met een blok-afhankelijk P&L-effect; hij komt in geen veto, wel in de rapportage per klasse van de forward-toets.
- Wat werkt is de **exit tijdens de positie** op wat de pool dan doet (flow300: uitstappen zodra de echte instroom negatief wordt; tp100). Een verfijning daarvan — reageren op een gedeeltelijke withdraw of op een versnelde uitstroom — is een exit-variant voor het bestaande grid (spec E/F), geen nieuw signaal; alleen zinvol na de clean-rapporten en forward.
- De agenda gaat verder met idee 3 (track record van de creator op de curve-instappen, E27-methode) en de K1-houdbaarheid; idee 4 is beantwoord (E36).
