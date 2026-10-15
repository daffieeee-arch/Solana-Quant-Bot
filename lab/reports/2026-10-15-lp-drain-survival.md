# D (verkennend): tijd van trigger tot LP-opname, F7 pool_reversed op dev (2026-10-15)

| | |
|---|---|
| code | `lab/wallets/survival.py`, `nas_survival.sh` (#257); regime-rijen en `--rerender` (#259); review-fixes C4–C6 (#264); run op commit **8cef820** (main met #264) |
| run | NAS (task 74-survival, 8cef820), 46 s: de rebuild met C4–C6; de eerdere run (task 80 op 33b731f, met de VPS-rerender) staat in `survival-v1/superseded-33b731f/` |
| uitvoer | `data-old-faithful-one/lab/wallets/survival-v1/`: `survival.parquet` (sha256 e05f8195…, één rij per trigger), `survival.md` (01b7ba3d…), `run-survival.json` (80cfdadc…) |
| data | triggers van de f7rev-run (F7, segment pool_reversed, alle varianten delen hun trigger): 40.737 distincte (pool, t), slots 446.531.016–452.284.000; store dev2; de hold-out is niet gebruikt |

> **Correctie (2026-10-15, Codex-review C4–C6, #264):** (C6) de K3-hold stond als vaste 600 slots; de engine rekent T600 als duur (600 × 267,3 ms) per epoch om in slots, dus ~500 slots bij 320 ms. De kolom "opgenomen binnen de K3-hold": alles 7,5 % (was 7,8 %), regime 320 ms 9,0 % (was 10,7 %), dag 1–9 8,5 % (was 9,4 %). (C4) de kolom "mediaan WSOL in de pool bij de exit" nam de reserve van de laatste *trade*; een withdraw of deposit in het stille gat telde niet mee. De NAS-rebuild op #264 (task 74) verschuift die medianen licht omhoog: 1 min 350 → 359 SOL, 2 min 416 → 427, 5 min 478 → 488, 10 min 555 → 563; de tabel in §3 is vervangen, de lezing verandert niet. (C5) raakt alleen smokes met `--observe-slots`, niet deze run.

Beschrijvend op verbruikte data, geen toets en geen registratie. Doel: de duur van de markt na een F7-trigger meten, als verklaring voor de dead-rijen van de markouts (`2026-10-15-markouts.md`, §4) en als onderbouwing van de beslissing in het register dat "verlaten bij de eerste opname" geen exit-regel is.

## 1. Definities

- **Opname (drain)** = de eerste withdraw na t die ≥ 90 % van de WSOL in de pool weghaalt. Censuur op het store-einde. Tijd in minuten van de gemeten ms/slot van de epoch.
- **Kaplan–Meier** (lifelines 0.30.3), 95 %-CI Greenwood. **K3-venster** = aandeel triggers waarvan de pool binnen de maximale hold van K3 na t is opgenomen, Wilson-CI. T600 is een duur: 600 slots bij de referentie 267,3 ms, per epoch omgerekend naar slots zoals de engine doet (`clock.slots(ms(600), t)`): ~600 bij 270 ms, ~500 bij 320 ms, ~735 bij 218 ms.
- **Inactiviteits-exit**: "geen trade gedurende X minuten", vuurt aan het einde van het eerste gat ≥ X minuten tussen trades na t; vergeleken met het moment van de opname.
- Groepen: alles, farm-vlag (organisch / gemarkeerd, uit de parts), dagblokken, slot-tijdregime.

## 2. Overleving S(t) = aandeel pools nog niet opgenomen t minuten na de trigger

| groep | n | opnames | 1 min | 3 min | 5 min | 10 min | 30 min | 60 min | mediaan (min) | opgenomen binnen de K3-hold |
|---|---|---|---|---|---|---|---|---|---|---|
| alles | 40.737 | 40.736 | 97,0 % | 91,6 % | 86,5 % | 75,9 % | 48,0 % | 27,9 % | 27,9 | **7,5 %** [7,3–7,8] |
| farm organisch | 12.641 | 12.641 | 96,4 % | 90,3 % | 85,3 % | 74,2 % | 45,4 % | 25,9 % | 25,9 | 8,9 % [8,4–9,4] |
| farm gemarkeerd | 28.096 | 28.095 | 97,3 % | 92,2 % | 87,1 % | 76,7 % | 49,1 % | 28,8 % | 29,1 | 6,9 % [6,6–7,2] |
| dag 1–9 | 13.991 | 13.990 | 96,7 % | 90,5 % | 84,5 % | 72,1 % | 42,0 % | 21,2 % | 22,5 | 8,5 % [8,0–8,9] |
| dag 10–18 | 26.309 | 26.309 | 97,2 % | 92,2 % | 87,6 % | 77,9 % | 51,1 % | 31,4 % | 31,2 | 7,0 % [6,7–7,3] |
| regime 270 ms (1037–1046) | 32.903 | 32.902 | 97,2 % | 92,0 % | 87,2 % | 77,1 % | 50,2 % | 30,1 % | 30,2 | 7,2 % [6,9–7,5] |
| regime 320 ms (1033–1036) | 7.834 | 7.834 | 96,4 % | 89,9 % | 83,9 % | 70,6 % | 38,4 % | 18,4 % | 20,8 | 9,0 % [8,4–9,7] |

CI's per cel (± 0–1 pt) staan in `survival.md`. Op 6 u is 0,5 % van de pools nog niet opgenomen; op één na is elke trigger binnen de store-periode opgenomen (40.736 van 40.737).

## 3. De eerste opname en de inactiviteits-exit

- De eerste withdraw na de trigger **is** in 91,4 % van de gevallen al de opname (≥ 90 %). Gedeeltelijke opnames vooraf zijn zeldzaam; de LP vertrekt in één keer.
- In 63,0 % van de opnames is de laatste trade van de pool al voorbij: eerst stopt de handel, dan haalt de LP de WSOL weg. Maar het gat tussen die laatste trade en de opname is kort:

| X | vuurt | vóór de opname | mediaan gewonnen minuten | mediaan WSOL in de pool bij de exit | ná de opname (verkoop in een lege pool) |
|---|---|---|---|---|---|
| 1 min | 40.737 (100 %) | 1.704 (4,2 %) | 50,5 | 359 SOL | 39.032 (95,8 %) |
| 2 min | 40.737 (100 %) | 1.068 (2,6 %) | 52,8 | 427 SOL | 39.668 |
| 5 min | 40.737 (100 %) | 853 (2,1 %) | 45,0 | 488 SOL | 39.883 |
| 10 min | 40.737 (100 %) | 585 (1,4 %) | 40,0 | 563 SOL | 40.151 |

Een inactiviteits-exit vuurt bij elke trigger (elke pool valt uiteindelijk stil), maar in 96–99 % van de gevallen pas ná de opname: de LP neemt op binnen de minuut nadat de handel stilvalt. De 1–4 % waar de regel wél vóór de opname vuurt, zijn grote pools (mediaan 359–563 SOL) die tientallen minuten blijven staan; dat zijn niet de pools waar de K3-posities zitten.

## 4. Wat dit betekent

1. **De markt na een F7-trigger leeft mediaan 28 minuten.** 13,5 % van de pools is binnen 5 min leeg, 24 % binnen 10 min, 52 % binnen 30 min, 72 % binnen een uur. Dat is precies het dead-aandeel van de markouts (12 % op 5 min, 30 % op 15 min, 69 % op 1 u): de lange horizonten van spec B meten geen koers maar het einde van de pool.
2. **Binnen de K3-hold (T600 = 600 × 267,3 ms ≈ 2,7 min; ~600 slots bij 270 ms, ~500 bij 320 ms) is 7,5 % van de pools al leeg** (dag 10–18: 7,0 %). Een K3-positie die de volle hold uitzit, verkoopt in ongeveer 1 op 13 gevallen in een lege pool; de take-profit sluit mediaan na 3–4 s, dus de werkelijke blootstelling is kleiner. In het trage regime (320 ms, epochs 1033–1036) is het 9,0 %: de hold is daar korter in slots, maar de pools leven ook korter (mediaan 20,8 tegen 30,2 min).
3. **"Verlaten bij de eerste opname" is geen exit-regel** (bevestigt de register-beslissing): de eerste opname is in 91 % meteen de volledige opname, er is dus geen waarschuwing vooraf. **"Verlaten bij inactiviteit" evenmin**: de regel vuurt in 96–99 % van de gevallen pas na de opname. Een exit voor F7 reversed moet uit koers of flow komen vóór de opname, en hoort bij meta-labeling na een positieve schone toets (E20), niet bij een nieuwe dev-ronde.
4. Organische pools worden iets sneller opgenomen dan gemarkeerde (8,9 % tegen 6,9 % binnen de K3-hold, mediaan 25,9 tegen 29,1 min); het verschil is klein en verkennend. De gemarkeerde (farm-)pools worden door hun farmer langer in leven gehouden, wat spoort met de omkering op 1 u in de rt_tx-split (markout-rapport §6).

## 5. Beperkingen

- Eén periode, verbruikte dev-data, geen toets; na Alpenglow (epoch 1064) een nieuw regime.
- Alleen de F7-triggers van de f7rev-run (reversed pools met SOL-quote); de opname is gedefinieerd op de WSOL-reserve, een pool die door verkopen leegloopt zonder withdraw telt niet als opname.
- Het regime-verschil is gerapporteerd, niet getoetst; het 320 ms-regime valt geheel in dag 1–9.
