# E58 runner-census op dev: welke multi-day-stijgers zag ons universum? (2026-10-22)

| | |
|---|---|
| spec | `data-old-faithful-one/lab/research/spec-runner-census-2026-10-21.md` v0.2, sha256 3979c71d274737717ef6609a86549937d2b2b459bc2563204e979aa08c733ead (buiten Git; drempels vooraf vastgelegd, vóór enige blik op de data; verkennend, geen toets) |
| code | stap 1 `wallets/explore/runner_hours.py` + `nas_runner_hours.sh` (#514, pin 2d1d300); stap 2 `wallets/explore/runner_census.py` (#515, 759a14f); census gedraaid op e0382c5 (UTC-daglabels, zie de correctie onder de run-rij) |
| run | stap 1 NAS-job 99zzzzz5 op 2d1d300 (rc 0, 1 test passed) → `wallets/runners-hours-2d1d300/` (hours.parquet sha256 **b7450895be72931a10fc9dd7f6df49aa4bfe94828c9ce8567b6d319273d2ca77**, pools.parquet 09004cc7…, hour_bounds.parquet 023b30ed…, run-runner-hours.json 9199b366…; 1.543.272 rijen, 149.522 pools, 473 uren, null_close_hours 250); stap 2 VPS 2026-10-22 in een 3G-scope (DuckDB 2 GB, 2 threads), 70 s → `wallets/runner-census-e0382c5/` (census.json sha256 **92d1e8dc4fdb39d80d31a55b724403ffe97109c9c54a0dec05d052b443754b1a**, census.md c6682515…, runners.parquet 656e7156…, entries.parquet 8243adb8…) |
| correctie | de eerste run (`runner-census-1ea3e7e/`, census.json 6be8a5f8…, 06:34Z) labelde de bootstrap-dagen met `to_timestamp()` in de sessie-tijdzone van de VPS (Europe/Amsterdam): de dag-bootstrap groepeerde op CEST-dagen in plaats van UTC-dagen (spec §3; 14 van de 193 instap-pools op een andere dag). Gerepareerd in e0382c5 (`day_labels`, vast UTC, test); herrun: runners.parquet en entries.parquet byte-gelijk, gemiddelden gelijk, alleen de intervallen schuiven (1 u [−9,7; +1,1] → [−10,0; +1,6]; 6 u [−34,0; −21,2] → [−34,2; −20,3]; 24 u [−68,3; −52,2] → [−67,8; −52,7]); conclusies ongewijzigd. De eerste run blijft staan als bewijs |
| data | dev2 1033–1046 (verbruikt), slots 446.256.000–452.304.000; alle PumpSwap-pools (store v2 heeft alleen legs van SOL-markten: de 6.345 'other'-pools zijn geteld, zonder uren); F7-triggers uit `backtests/f7rev-1033-1046/parts` |

Labels: feit / reconstructie / modelaanname / onbekend. Runner = uur-close ≥ 3 × de minimum-close over de bestaande uren van [u − 72 u, u − 24 u] (pool ≥ 24 u oud; gedeeltelijk venster toegestaan en geteld) met diepte op het uur van dat minimum ≥ 500 SOL (primair) of ≥ 100 SOL (secundair, alleen telling); één per pool per beweging (reset onder 1,5 × het minimum). "Gezien" = SOL-quoted PumpSwap-pool (normal of reversed).

## 1. Telling (feit)

| vloer | runners | pools | gezien | gedeeltelijk venster | normal | reversed | pools met F7-trigger |
|---|---|---|---|---|---|---|---|
| primair 500 SOL | 450 | 445 | 450 (100 %) | 368 (82 %) | 101 (97 pools) | 349 (348 pools) | 58 (alle reversed) |
| secundair 100 SOL | 1.912 | 1.899 | 1.912 (100 %) | 1.829 (96 %) | 204 (193 pools) | 1.708 (1.706 pools) | 399 (alle reversed) |

- "Gezien 100 %" is op store v2 een constructie-feit, geen uitkomst: alleen SOL-markten hebben legs, dus het niet-geziene deel is alleen als pool-aantal meetbaar (6.345 'other'-pools = 4 % van 155.867; 5.457 zonder SOL-pool van de quote, 622 USDC, 266 met SOL-pool). Runners in Meteora/Raydium-pools: onbekend (geen decoders in de store; de venue-store 1045 van #518 is de route).
- Leeftijd van de pool op het runner-uur: mediaan 24 u (p10 24, p90 73 u) bij de primaire vloer; het venster bestaat dus meestal uit één uur (het eerste uur van de pool). De runners zijn jonge pools, geen gevestigde tokens.
- Per UTC-dag 2–42 primaire runners (20 dagen; 12 sep 2, piek 20 sep 42).

## 2. Wat de runners zijn (beschrijvende snede ná de run: 2 kijkmomenten)

De spec legt de liquiditeit vast op het uur van het **minimum**, niet op het runner-uur. Gesplitst op de diepte (SOL-zijde) op het runner-uur:

| vloer | oriëntatie | diepte op runner-uur | runners | gedeeltelijk | F7 | mediaan close/min | p90 |
|---|---|---|---|---|---|---|---|
| primair | normal | ≥ 100 SOL | 101 | 86 | 0 | 3,2 × | 4,8 × |
| primair | reversed | < 100 SOL | 348 | 281 | 58 | 1.869 × | 121.413 × |
| primair | reversed | ≥ 100 SOL | 1 | 1 | 0 | 91 × | – |
| secundair | normal | ≥ 100 SOL | 199 | 130 | 0 | 3,3 × | 7,6 × |
| secundair | reversed | < 100 SOL | 1.704 | 1.690 | 399 | 9,7 × | 63.140 × |

- **Reversed "runners" zijn leeggehaalde pools, geen stijgers (reconstructie):** op het runner-uur is de SOL-zijde bij 348 van de 349 al weg (< 100 SOL; mediaan < 0,05 SOL) en de close/min-verhouding ligt in de duizenden tot miljoenen. Voorbeeld s5SFKC5p…: uur 1–4 close 0,00085 → 0,00139 SOL per token met diepte 753 → 1.111 SOL (echte koopstroom), uur 5 close 86,2 bij diepte 0,0 (de LP-opname; de close na de laatste leg van dat uur rekent met lege reserves). De 58 F7-pools onder de primaire runners zitten allemaal in deze groep: **geen enkele F7-getriggerde pool was een liquide 3×-stijger** (feit op deze definitie).
- **Liquide runners (diepte ≥ 100 SOL op het runner-uur): 102 bij 500 SOL**, 101 ervan normal pools, mediaan 3,2 × (p90 4,8 ×), 0 met F7-trigger; ≈ 5,7 per dag (18 dagen met ten minste één). Daarvan hebben **15 een volledig venster van 72 u** (pool ≥ 72 u oud: mediaan 3,33 ×, diepte op het minimum 641 → 1.308 SOL op het runner-uur) — dat zijn de enige "multi-day-stijgers van een gevestigde pool" die de dev-data laten zien; bv. 3dcwhqJp… 0,030 → 0,092 SOL per token over 72 u met diepte 1.868 → 3.364 SOL, DA4pM4xS… 0,039 → 0,122 met diepte 1.644 → 2.980 SOL (feit).

## 3. Momentum-instap (vooraf vastgelegd; beschrijvend, geen regel)

Instap = einde van het eerste uur met close ≥ 2 × de close van 24 u eerder én diepte ≥ 500 SOL; 0,5 SOL rondrit met de fee-tier van de pool, koop-impact op de instapdiepte, verkoop-impact op de diepte op h, netwerk base 0,29 pt; wiped = −100 % bij diepte op h < max(1 SOL, 1 % van de instapdiepte); eenheid pool, 0,5 %-getrimd, dag-bootstrap 10.000 (seed 7).

| set | instappen | pools | netto 1 u | 6 u | 24 u | wiped 24 u | zonder horizon 1 u / 6 u / 24 u |
|---|---|---|---|---|---|---|---|
| SOL, binnen universum | 210 | 193 | −3,5 pt [−10,0; +1,6] | −27,0 [−34,2; −20,3] | **−59,9 [−67,8; −52,7]** | 56 % (mediaan −100 %) | 0 / 2 / 9 |
| buiten universum, USDC | 0 | 0 | – | – | – | – | 0 |

- Instapdiepte mediaan 1.458 SOL (minimum 501); 91 van de 193 instap-pools zijn ook primaire runner; 0 instappen in F7-pools (feit).
- Na het 2×-uur verliest de instap op 24 u gemiddeld 60 punten en is meer dan de helft van de pools leeg: het momentum van een jonge SOL-pool met ≥ 500 SOL eindigt in dev typisch in een drain binnen een dag (reconstructie; modelaanname voor de kosten en de vulling op de uur-close).
- Buiten het universum is niets te meten (0 instappen): store v2 heeft daar geen legs.

## 4. Antwoord en beperkingen

- **Antwoord (dev, feit op deze definities):** het universum ziet alle 3×-stijgers die de store kan laten zien; het mist niets binnen PumpSwap-SOL. Liquide multi-day-stijgers zijn zeldzaam (102 in 20 dagen, 15 met een volledig venster) en de vooraf vastgelegde momentum-instap erop is zwaar negatief. Het Dacity/TTWO-type (gevestigde pool die dagen stijgt) komt in dev 15 keer voor en zit in normal pools, niet in de reversed pools van F7.
- **Niet gemeten / onbekend:** 'other'-quote-pools (4 %), Meteora/Raydium, intra-uur-runs (uurbalken), de periode ná de synthetische migratie van 7 okt en TTWO zelf (buiten dev), de post-Alpenglow-regime.
- **Methodezwakte (vooraf niet voorzien):** de liquiditeitsvloer op het minimum-uur laat drains door als "runner"; een volgende versie van de definitie moet ook de diepte op het runner-uur eisen. Dat is hier als beschrijvende snede gedaan, niet als nieuwe drempel.
- Niets hiervan verandert het universum, K3/H4/H5 of de paper engine; geen vervolgtoets gepland.

## 5. Kijkmomenten

Spec §5 ≈ 30 (2 vloeren × ~6 klassen + 3 horizonnen × 2 × 2) + 2 beschrijvende sneden (diepte op het runner-uur; volledig venster) = **≈ 32** → registerrij E58.
