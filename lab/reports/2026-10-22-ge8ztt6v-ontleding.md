# E57 (c): ontleding van Ge8ztT6V — de grootste niet-insider poolwinnaar van de follow-the-money-workflow (2026-10-22)

| | |
|---|---|
| vraag | E57 vervolg (c), critic-aanbeveling 3: wat doet Ge8ztT6V (122,8 SOL/dag gerealiseerd op dev volgens het rapport, live +17 SOL in 3,8 u), waar komt het geld vandaan, en is het met onze vertraging (2–3 slots) bereikbaar? Verkennend, dev (verbruikt); geen toets, geen regel |
| data | money-v2 wallet-dagen (`wallets/money-v2-94f0fee/`), dev2-store (`pool`, `curve`, `pools`, `mints`, `blocks`) 1033–1046; wallet `Ge8ztT6Vv4MUQpHuQd732uQjFgGFaqDnAx4i5ymnfCBs` |
| code / uitvoer | `research/follow-the-money-2026-10-21/lab-checks/`: `ge8_extract.py` (966086b5…), `ge8_pools.py` (22e08ded…), `ge8_analysis.py` (7f438bf6…) → `ge8_analysis.out` (1ad149c3…); SHA256SUMS. Data `wallets/e57c-ge8ztt6v/`: wallet-days.parquet 1d27d6b7…, pool-legs.parquet a371d5d8…, curve-legs.parquet 0d078ab0…, pool-history.parquet f304876b… (alle legs van alle traders in de 191 pools, 1.108.555 rijen). VPS, 3G-scopes, 95 s + 49 s; analyse licht |
| eerder | kopie-shift van de workflow op dezelfde wallet: critic-herrun `scripts/m3-critic/c4.out` (met fee-fix; leidend) en judge-m1 `scripts/m3-judge/j4.out`, `j5.out`, `j6.out`; hier onafhankelijk herhaald op de legs |

Labels: feit / reconstructie / modelaanname / onbekend. Prijs = e/b vóór de leg (SOL per token; alleen normal pools). Trip = koop → de eigen eerstvolgende verkoop in dezelfde pool (asof).

## 1. Wie (feit)

- **FLASHX-terminalgebruiker:** 5.847 van de 5.906 pool-legs via programma FLASHX8D…, 56 rechtstreeks via PumpSwap (pAMMBay6…); 32 curve-kopen (256 SOL) ook via FLASHX. Alleen **normal SOL-pools** (0 reversed, 0 andere quote); 191 pools; 17 actieve dagen (12–30 sep, 17 en 24 sep niet); 5.906 transacties met precies één leg per transactie, ≈ 350 per actieve dag; 60 % van de kopen is de eerste leg in de pool in dat slot.
- **Maat:** koop mediaan 4,95 SOL (p10 2,0; p90 15,8; max 49,5), verkoop mediaan 7,2 SOL (max 72,8); koop = 4,5 % van de SOL-diepte (mediaan 117 SOL; p10 69, p90 203) → eigen prijsimpact ≈ 9 % per koop (modelaanname x·y = k).
- **Kosten:** priority fee vast **0,0733 SOL per transactie**, geen Jito-tips; 227,8 SOL aan netwerkkosten over de periode (≈ 13 SOL per actieve dag).
- **Resultaat (money-v2):** kas +2.564 SOL, fees 230, netto +2.335; 15 van 17 dagen positief, slechtste dag −35,5 (12 sep), beste +342 (16 sep); **150,8 SOL kas per actieve dag** (bruto, 17 actieve dagen); netto na fees 137,4 per actieve dag; het rapport's 122,8 is de netto 2.335 gedeeld door de 19 kalenderdagen 12–30 sep (= 122,9) — drie verschillende maten, hier uit elkaar gehouden (correctie 2026-10-22, LAB review). Geen voorraad over (1 pool met > 1 % van de gekochte tokens over); de 34 curve-kopen worden na migratie in de pool verkocht (19 pools). Geen creator-link, 0–1 snipes per dag: geen insider; eigen aandeel 3,7 % van het SOL-volume in zijn pools: geen wash (feit).

## 2. Waar het geld zit (feit)

- **Hete verse pools:** eerste koop mediaan 377 slots (≈ 2 min) na poolcreatie; mediaan 852 betalers per pool (p10 212, p90 1.646); 40 % van alle legs in die pools is farmer-gevlagd (de wash-stroom van E59 zit erin, maar de wallet handelt ertegenin, niet mee).
- **Per pool:** mediaan 20 legs over ≈ 7 min (1.604 slots); netto (verkopen − kopen − fees) +2.583 SOL over 191 pools, 99 positief, mediaan +0,7, beste +212, slechtste −41, verliezen samen −797. **Concentratie: top 5 pools 30 %, top 12 55 %, top 20 75 % van het netto.**
- **Per trip** (3.112): houdtijd mediaan 103 slots (≈ 30 s; p10 8, p90 568), mediaan rendement −4,0 %, gemiddeld +5,8 %, winstkans 40 %. Korte trips verliezen (≤ 100 slots: 1.542 trips, −256 SOL), lange winnen (101–500 slots: +1.873 SOL, winstkans 54 %; > 500: +1.215, mediaan +24 %, winstkans 75 %). Top 1 % van de trips = 23 % van de bruto winst, top 10 % = 75 %.

## 3. Hoe (feit + reconstructie)

| maat | waarde |
|---|---|
| laatste leg van een ander vóór de koop | 40 % in hetzelfde slot, 36 % één slot eerder, 16 % 2–3 slots, 8 % later (77 % ≤ 1 slot) |
| prijsbeweging over de 50 slots vóór de koop | mediaan +0,9 %, 47 % dips: **geen prijssignaal vooraf** |
| markout ná hun koop (incl. eigen impact ≈ 9 %) | 10 slots +5,9 % (74 % omhoog), 50 slots +4,9 %, 200 slots +1,4 % (52 %), **1.000 slots −12,5 % (44 %)** |
| prijs ná hun verkoop | 10 slots −7,3 % (80 % lager), 50 slots −8,8 %, 200 slots −10,3 % (64 %), 1.000 slots −10,5 % |

- Reconstructie: een **snelle stroomvolger met uitstapselectie** (M1/M3-klasse van E57). De instap draagt geen duurzame edge (de markout zakt onder nul vóór 1.000 slots ≈ 4,5 min); het geld komt uit de uitstap: in de pools die lopen blijft hij 1–5 min zitten en verkoopt vóór de daling, de rest laat hij binnen een halve minuut met een klein verlies los. Wat hij ziet dat wij niet zien, is onbekend (FLASHX toont stroom per pool in real time; zijn 0,073 SOL per transactie koopt een plek vooraan in het slot).

## 4. Bereikbaar voor ons? Nee (feit op dev)

- **Kopie 2 slots later:** instap mediaan **+6,6 %** duurder dan zijn fill (gemiddeld +10,7 %; in 89 % van de kopen hoger), uitstap 2 slots later mediaan **−7,6 %** (gemiddeld −10,5 %; 89 % lager); 3 slots: +6,7 % / −7,8 %. Samen ≈ 14 punten per rondrit tegen zijn gemiddelde trip van +5,8 %.
- Dit bevestigt de kopie-shift van de workflow (critic-herrun `scripts/m3-critic/c4.out` via `c4_judgecopy.py`, mét de #504-fee-fix; zelfde wallet, 3.146 posities): kopie 0,5 SOL op k = 2 met realistische exit (start van slot max(verkoop, koop + k) + 2) **−4,94 SOL/dag** (k = 3 −4,89; 1,0 SOL −11,06); met zijn exacte uitstapmoment (orakel) +9,31; zijn eigen replay +127,5/dag; curve→curve-trips −36 %, migratie-exits +16 % (29 stuks). De oudere judge-tabel `j4.out` (zonder fee-fix) geeft −5,23 / −5,18 / −11,35 / +9,02 (bronvermelding gecorrigeerd 2026-10-22, LAB review).
- **Oordeel:** niet bereikbaar bij 2–3 slots en niet kopieerbaar als regel: geen signaal vóór de koop, de winst zit in de uitstaptiming op een minderheid van trips en pools. Geen vervolg, geen paper-arm; T2/T3 blijven geparkeerd. E57 is hiermee afgerond: (a) E59 gesloten, (b) kasbron gedaan, (c) deze noot.

## 5. Beperkingen en kijkmomenten

- Prijs = e/b vóór de leg; de eigen impact is in de markouts niet uitgesplitst (modelaanname ≈ 9 %); trips asof (één verkoop per koop); dev vóór Alpenglow; de live-slice van 3,8 u is niet opnieuw bekeken; geen DSR (beschrijvend).
- Kijkmomenten: ≈ 15 cellen (profiel, concentratie, context, markouts 2 × 4 horizonnen, kopie 2 × 2) → registerrij E57.
