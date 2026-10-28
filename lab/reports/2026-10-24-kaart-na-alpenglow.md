# Kaart na Alpenglow, stap 1: regime, geldkaart, basisrates en runner-census op OF1-epoch 1064 (verkend, 0 blikken)

Datum: 2026-10-24 (lezing ≈ 21:00–22:30Z). Opdracht: plan `research/stap1-na-alpenglow-plan-2026-10-24.md` v0.3 (sha b27d03d0…; coördinator akkoord, LAB plan-review OK) na het eigenaarsbesluit van 24 okt ~17:45Z ("Ja, vrijgeven": live recorder-data en OF1 ≥ epoch 1064 zijn vrij voor strategieonderzoek). **Dit is een kaart, geen toets:** geen cel wordt op rendement gescoord, 0 blikken; epoch 1064 geldt hierna als **verkend** (registerrij E77). Labels: feit / reconstructie / modelaanname / hypothese / onbekend.

**Bron (feit).** Forward-store `store/fwd1064-day-65865b3.duckdb` (hele epoch 1064, slots 459.648.000–460.080.000, sha f45b37b3…, meta period 'forward'; `blocks` van 2026-10-21 13:36:05Z tot 2026-10-22 15:06:37Z = 25,5 u, 212,57 ms per slot). NAS-uitvoer op 516aa21 (`--forward`, #571): `wallets/money-fwd1064-516aa21/` (SHA256SUMS 73d665d1…; 1.008.214 wallet-dagen, 759.743 wallets, dagen 21–22 okt; geen rapport, zoals het plan voorschrijft) en `wallets/runners-hours-fwd1064-516aa21/` (SHA256SUMS 1c47131d…; 60.960 pool-uren, 12.954 pools, 27 uren); x10 stage A op ad97782 (`--forward`): `wallets/x10-fwd1064-ad97782/` (SHA256SUMS 67cb2795…; 53.729 instappen bij 0,5 SOL, 28.864 mint-pieken). Lezing op de VPS (3G-scopes, DuckDB ≤ 1,8 GB) met eigen scripts in `research/stap1-na-alpenglow-2026-10-24/`: `regime_1064.py` (739555c4…) / `.out` (f9f0df57…), `money_fwd1064.py` (8b300c39…) / `.out` (305e3c89…), `x10_base_rates_fwd.py` (69af48a4…) / `.out` (53a2d79e…), `runner-census-fwd1064/` (census.md 32e5e148…, census.json 54e43884…); SHA256SUMS van het archief 0e0e2de7… (vervangt 99b9d622… van de eerste regimelezing). Dev-referenties: E67 §4–5 (regime), `money-v2-94f0fee` (wallet-dagen 11 sep – 1 okt, zelfde kolommen), `x10-6f294dd` (labels/mint-pieken op het **oude** harnas, 14 epochs), E58-census `runner-census-e0382c5`.

**Twee kanttekeningen die overal gelden (feit).** (1) De store heeft geen warm-up: mints met een create vóór het epochbegin ontbreken (E68), 21 okt is een deeldag van 10,4 u (vanaf 13:36Z) en 22 okt van 15,1 u (tot 15:07Z); tellingen "per dag" zijn dus deeldagen en wallet-dagreeksen beginnen zonder opgebouwde voorraad — vergelijk met dev-dag 1–2 (11–12 sep), niet met dev-dag 21. (2) ad97782 ligt vóór #586: boost/withdraw/deposit-marks kregen hun fee-tiers via een ASOF op het slot alleen (≤ één tier-stap op ≈ 0,4 % van de boost-marks — de dev-1033-schaal uit #586, 113 van 29.700, niet op 1064 gemeten); raakt geen telling hier in deze orde van grootte.

## 0. Kort

- **Regime (feit, `regime_1064.out`; dev = E67 §4):** per 24 u 48.735 creates (dev 35.907, ×1,36), Mayhem-aandeel 21,4 % (dev 28,0 %), 970 graduaties (dev 1.123, ×0,86), graduatie-aandeel 1,99 % (dev 3,13 %); 1.777 omgekeerde pools in de epoch ≈ 1.672/24 u (dev 2.733/dag, ×0,61), waarvan 68,9 % ≥ 120 SOL binnen 60 min = 1.153/24 u (dev 1.023/dag, ×1,13). Zelfde beeld als E67 op de live recorder: meer munten, minder graduaties, minder maar vollere omgekeerde pools.
- **Geldkaart (feit op de wallet-dagen; hypothesen gelabeld):** de boeken sluiten (zero-sum-residu pool +0,085 %, curve −0,51 % van de omzet, beide < 1 %). Gerealiseerd verliezen de handelaren samen −114 k / −140 k SOL per deeldag; de marks (+199 k / +221 k) maken de som-pnl positief — het dev-dag-1-patroon (−80 k / +136 k), geen regimeverschil. Top-1 % van de positieve wallet-dagen neemt 59–71 % van de positieve pnl (dev 66–84 %). **Snipers (per-wallet-dag-label) realiseren +6,1 / +9,6 SOL per wallet-dag** op de twee deeldagen (n 1.287 / 1.279, 48–49 % positief); op dev loopt die dagreeks van −44,9 (12 sep) tot +7,9 (1 okt), de laatste elf dev-dagen −13,8 … +7,9. **Hypothese, niet bewijs:** 1064 ligt aan de bovenkant van de dev-reeks, niet erbuiten; twee deeldagen zonder warm-up. Wallets met pnl ≥ 20 SOL op 21 okt zijn op 22 okt maar in 18 % positief (dev 58 %) — **reconstructie: een marks-artefact,** gesteund door de gerealiseerde cijfers: op gerealiseerd (cash − fees) is het 54,8 % (n 540; dev 68,8 %, n 46.311), mediaan +0,75 (dev +2,48) SOL.
- **Basisrates prijspiek (feit, volledige vensters):** niet-Mayhem ≥ ×10 binnen 1 u 2,51 % (dev 3,33 %), binnen 6 u 2,57 % (dev 3,44 %); ≥ ×2 binnen 1 u 14,85 % (dev 15,19 %); Mayhem ≥ ×10 binnen 1 u 20,7 % (n 276; dev 17,9 %). Labels e10 (0,5 SOL): piek netto-mark/kosten ≥ ×2 binnen 1 u 6,63 % (dev 7,35 %), ≥ ×10 0,50 % (dev 0,62 %); mig ≥ ×2 25,6 % (dev 24,1 %). De ×10-wereld is op 1064 dus eerder iets dunner dan dev, niet rijker. `stop_first_24` 20 % vs dev 91 % is het harnas (mark-cap-fix #552/#559 in ad97782, oud harnas in 6f294dd), geen regime.
- **Runner-census (feit):** 2 runners op de primaire vloer (≥ 500 SOL), 11 op de secundaire, 0 scorebare momentum-instappen — één epoch van 25,5 u kan de 24–72 u-vensters van E58 niet vullen; zinvol pas op de aaneengesloten store 1064–1065(–1066).
- **Wat volgt:** herrun geldkaart + census op de aaneengesloten store 1064–1065 (`fwd1064-1065-6a4458f`, in de wachtrij), daarna 1066; stap 2 (forward-pre-registraties) alleen voor kandidaten met een dev-GO (E72 STOP, E71/E74 STOP; E73 en E75 lopen); de geldkaart levert op zichzelf geen cel.

## 1. Regime (feit; `regime_1064.out`)

| maat | 1064 (25,5 u) | per 24 u | dev (E67 §4) | verhouding |
|---|---|---|---|---|
| creates (CreateEvent) | 51.799 | 48.735 | 35.907/dag | ×1,36 |
| Mayhem-aandeel | 21,4 % (21 okt 19,9 %, 22 okt 23,0 %) | | 28,0 % | |
| SOL-quote creates | 33.754 (65,2 %) | | – | |
| graduaties (migrate_slot) | 1.031 (1,99 %) | 970 | 1.123/dag (3,13 %) | ×0,86 |
| omgekeerde pools aangemaakt | 1.777 | 1.672 | 2.733/dag (1045–1046) | ×0,61 |
| … ≥ 120 SOL binnen 60 min | 1.225 (68,9 %), mediaan maxdiepte 174,5 SOL | 1.153 | 1.023/dag | ×1,13 |
| normale SOL-pools aangemaakt | 1.005 | 946 | – | |
| ms per slot | 212,57 | | ≈ 268 (x10-jager-noot `days.out`; Alpenglow-meetnotitie: 1037–1046 267–268 ms; niet in E67 §4) | ×0,79 |

Creates per uur van de dag pieken 13–15 UTC (2.906 / 4.818 / 2.985) tegen 1.200–1.400 in de ochtend; pool-activiteit: omgekeerd 3,2 M / 4,3 M rijen en 5,6 M / 6,7 M SOL volume per deeldag tegen normaal 4,7 M / 5,2 M rijen en 1,5 M / 1,7 M SOL. E67's recorder-lezing (×1,33 creates, ×0,59 graduaties, ×0,60 omgekeerde creaties, ×1,27 pools ≥ 120) wordt door de OF1-epoch bevestigd (feit op twee onafhankelijke bronnen).

## 2. Geldkaart (money-v2 wallet-dagen; `money_fwd1064.out`)

Definities: `pnl` = cash + Δ waarde van de voorraad (mark-to-market), gerealiseerd = cash − fees; archetypen **per wallet-dag** (vereenvoudigd t.o.v. spec A's labelvenster): bot = ≥ 50 geopend en mediaan hold ≤ 30 s; sniper = ≥ 50 % van de openingen in create-slot + 3 bij ≥ 5 openingen; whale = mediaan koop ≥ 5 SOL bij ≥ 20 openingen; farmer = ≥ 50 % farmer-legs bij ≥ 20 pool-legs; anders retail. Dezelfde code op dev.

| | 21 okt (10,4 u) | 22 okt (15,1 u) | dev dag 1 (11 sep, deel) | dev dag 2 (12 sep) | dev dag 21 (1 okt, deel) |
|---|---|---|---|---|---|
| wallet-dagen | 471.251 | 536.963 | 174.597 | 798.464 | 2.939.346 |
| Σ gerealiseerd (SOL) | −113.864 | −139.674 | −80.089 | −408.481 | – |
| Σ marks-verandering | +198.779 | +220.895 | +135.868 | +394.441 | – |
| aandeel wallet-dagen pnl > 0 | 24,1 % | 24,2 % | 35,0 % | 22,6 % | 11,2 % |
| wallet-dagen pnl ≥ 20 SOL | 2.334 | 2.514 | 475 | 4.360 | 2.789 |
| top-1 % positieve wallet-dagen, aandeel positieve pnl | 59,2 % | 70,7 % | 83,7 % | 74,9 % | 78,8 % |
| top-10 wallets | 12,3 % | 10,8 % | 20,8 % | 12,0 % | 18,0 % |

De dev-reeks groeit van 175 k naar 2,9 M wallet-dagen per dag met de opgebouwde voorraad (een wallet met een gewaardeerde positie telt elke dag mee); de 1064-aantallen zijn daarom niet met dev-dag 21 te vergelijken, en per uur ook niet met dev-dag 1 (deeldag van onbekende lengte) — het aanbod leest men uit §1, niet hieruit.

**Zero-sum (feit, `venue_flows`):** pool: cash −180.557, venue-fees +20.299, Δ SOL in de venues −231.632, extern (LP) −403.810 → residu +11.920 SOL = 0,085 % van 14,07 M omzet; curve: cash −70.865, fees +12.766, Δ +52.548 → residu −5.551 = −0,51 % van 1,09 M. Beide onder de 1 %-lat van spec A §6: de wallet-dagen zijn compleet genoeg voor een kaart.

**Archetypen (feit op de labels; alle wallet-dagen van 1064 tegen alle 21 dev-dagen):**

| archetype | 1064 wallet-dagen | gem. gerealiseerd / wallet-dag | aandeel > 0 | gem. pnl | dev wallet-dagen | dev gem. gerealiseerd | dev aandeel > 0 |
|---|---|---|---|---|---|---|---|
| retail | 995.600 (98,75 %) | −0,21 SOL | 17,2 % | +0,20 | 41,64 M | −0,05 | 8,9 % |
| farmer | 8.339 | −7,39 | 15,3 % | −6,93 | 127.151 | −22,59 | 14,0 % |
| **sniper** | 2.566 | **+7,85** | 48,8 % | +8,65 | 37.299 | −8,56 | 50,9 % |
| bot | 1.698 | +0,78 | 46,1 % | +1,29 | 35.015 | −1,86 | 37,0 % |
| whale | 11 | +11,07 | 72,7 % | +15,87 | 495 | −55,35 | 36,2 % |

Sniper-dagreeks gerealiseerd: 1064 **+6,10 (n 1.287) / +9,61 (n 1.279)**; dev 11 sep +2,24, 12–20 sep −44,9 … −7,7, 21 sep – 1 okt +0,03 / +0,81 / +6,48 / +1,31 / −5,42 / +4,13 / +3,07 / +0,36 / −13,8 / +4,64 / +7,89. **Lezing (hypothese):** de snipers van 1064 verdienen gerealiseerd meer dan op elke dev-dag behalve 1 okt, maar binnen de dev-spreiding en op twee deeldagen zonder warm-up (de dev-dagen 12–20 sep dragen hun verlies deels uit de aanloop: wallets die vóór het venster kochten en in het venster verkopen tellen hun cash zonder de koop — dat raakt dev-dag 2 … 9 en 1064 gelijk, want ook 1064 heeft geen warm-up). Geen signaal: wallets zijn kenmerken, geen cel (plan §2.3); eerst de aaneengesloten store 1064–1065, dan pas een vraag. Bots +0,78 (dev −1,86: dagreeks −6,4 … +4,4) en whales (11 wallet-dagen) zeggen niets.

**Wie verdient ≥ 20 SOL op een dag (feit):** 4.848 wallet-dagen op pnl, waarvan 2.977 ook gerealiseerd ≥ 20 (2.815 beide); mix op pnl: retail 95,2 %, sniper 3,2 %, bot 0,9 %, farmer 0,7 % — dev 95,1 / 2,6 / 1,4 / 0,7 %. Per uur 224 / 166 zulke wallet-dagen (dev-volledige dagen ≈ 130–370 per uur): dezelfde orde.

**Consistentie (feit):** P(pnl > 0 op d+1 | pnl > 0 op d) 36,2 % (dev 43,0 %), gegeven ≤ 0: 14,8 % (dev 7,8 %); op gerealiseerd 47,8 % (dev 52,9 %) resp. 13,2 % (dev 3,9 %). Wallets met pnl ≥ 20 SOL op 21 okt: 22 okt in 18,4 % positief, mediaan −21,3 SOL (dev 58,5 %, +0,71) — **marks-artefact (reconstructie)** (de gewaardeerde voorraad van dag d loopt op dag d+1 leeg; op dev valt dat tegen de volle dag weg); op gerealiseerd ≥ 20: 54,8 % positief op d+1 (n 540), mediaan +0,75, gemiddeld +33,4 SOL (dev 68,8 %, +2,48, +51,5). De persistentie van de grote gerealiseerde winnaars is dus wat lager dan op dev maar van dezelfde soort; één dagpaar, geen conclusie.

## 3. Basisrates van de prijspiek (feit; `x10_base_rates_fwd.out`; dezelfde definities als E64 §2)

Volledige vensters alleen: eerste trade + horizon ≤ einde van het venster (fwd: laatste blok; dev: het blok op lo + 432.000 per epoch, conservatief). NULL = nooit gehaald telt als geen hit (de eerste uitvoer van dit script liet NULLs weg en gaf 95–100 %; gecorrigeerd vóór enige lezing, de .out is de gecorrigeerde).

| mints (mint_peaks) | n 1064 | ≥ ×2 | ≥ ×5 | ≥ ×10 | n dev | ≥ ×2 | ≥ ×5 | ≥ ×10 |
|---|---|---|---|---|---|---|---|---|
| niet-Mayhem, binnen 1 u | 27.385 | 14,85 % | 4,40 % | **2,51 %** | 457.144 | 15,19 % | 5,30 % | **3,33 %** |
| niet-Mayhem, binnen 6 u | 22.888 | 14,99 % | 4,52 % | 2,57 % | 388.306 | 15,41 % | 5,41 % | 3,44 % |
| niet-Mayhem, binnen 24 u | 2.113 | 13,39 % | 3,45 % | 1,94 % | 127.948 | 15,54 % | 5,51 % | 3,57 % |
| Mayhem, binnen 1 u | 276 | 61,96 % | 33,33 % | 20,65 % | 5.190 | 62,56 % | 30,87 % | 17,86 % |
| Mayhem, binnen 6 u | 234 | 59,83 % | 32,05 % | 20,09 % | 4.431 | 62,51 % | 31,19 % | 18,55 % |

Per deeldag niet-Mayhem binnen 6 u ≥ ×10: 21 okt 2,40 % (n 14.901), 22 okt 2,88 % (n 7.987); dev per dag 2,71–4,34 % (21 dagen; Mayhem 12,9–25,3 %). Geheel-data `peak_mult` (72 u afgeknot op het venstereinde, zoals E64 §2) niet-Mayhem ≥ ×10 2,62 % (dev 3,49 %), Mayhem 20,4 % (dev 19,8 %): op 1064 zwaarder afgeknot (25,5 u), dus alleen de volledige-venster-rijen tellen.

| labels (0,5 SOL, ad97782 vs 6f294dd) | 1064 n | ≥ ×2 | ≥ ×5 | ≥ ×10 | mediaan | dev n | ≥ ×2 | ≥ ×5 | ≥ ×10 | mediaan |
|---|---|---|---|---|---|---|---|---|---|---|
| e10, piek netto-mark/kosten binnen 1 u | 21.516 | 6,63 % | 1,47 % | 0,50 % | 0,944 | 359.641 | 7,35 % | 1,79 % | 0,62 % | 0,943 |
| e10, binnen 6 u | 17.806 | 6,83 % | 1,67 % | 0,61 % | 0,943 | 306.355 | 7,92 % | 2,05 % | 0,79 % | 0,943 |
| c30, binnen 1 u | 16.661 | 5,61 % | 1,22 % | 0,42 % | 0,942 | 299.044 | 6,18 % | 1,44 % | 0,47 % | 0,936 |
| c2, binnen 1 u | 12.151 | 5,18 % | 1,05 % | 0,30 % | 0,942 | 236.186 | 5,47 % | 1,21 % | 0,38 % | 0,826 |
| mig, binnen 1 u | 895 | 25,59 % | 3,69 % | 1,45 % | 1,253 | 20.083 | 24,06 % | 5,28 % | 1,60 % | 1,254 |

`hit_m_24` over álle rijen (afgeknot = geen hit, ondergrens; 1064 heeft maar 5–7 % rijen met een volledig 24 u-venster): e10 6,63 / 1,55 / 0,54 % (dev 6,96 / 1,77 / 0,66 %). Instappen per moment 1064: e10 22.526, c30 17.483, c2 12.793, mig 927 (dev 371 k / 308 k / 243 k / 21 k over 14 epochs ≈ 26,5 k / 22 k / 17 k / 1,5 k per epoch): e10 ×0,85, mig ×0,63 per epoch (feit; past bij minder graduaties). **Lezing:** dezelfde prijspiek-wereld als dev, iets dunner op ×5/×10 bij niet-Mayhem en iets rijker bij Mayhem (kleine n); niets wijst op een nieuw ×10-aanbod na Alpenglow. Het ×10-model (E75) wordt niet op deze data gescoord (0 blikken; dat zou 1 blik per primaire cel zijn, plan §3).

## 4. Runner-census (E58-spec, zelfde drempels; `runner-census-fwd1064/census.md`)

12.954 pools met uren (10.694 sol, 2.260 omgekeerd), 1.649 halen de secundaire vloer; **2 runners op de primaire vloer (≥ 500 SOL op het minimum-uur), 11 op de secundaire (≥ 100 SOL)**, alle met een deelvenster (de pool is jonger dan 72 u), 0 met een F7-trigger, 2 momentum-instappen zonder horizon (store-einde), **0 gescoord**. Dev (E58, 14 epochs): 450 / 1.912 runners. Feit: één epoch van 25,5 u kan de 24–72 u-vensters niet vullen; de census is pas leesbaar op de aaneengesloten store. Geen lezing.

## 5. Datagebruik, blikken en vervolg

- Epoch 1064 (OF1) is hiermee **verkend** voor regime, geld, basisrates en census: geen cel gescoord, **0 blikken**; registerrij E77. De live recorder-dagen 24–27 okt zijn **niet** gebruikt (§2.5 van het plan vraagt recorder-triggers als invoer via de coördinator; niet gestart) — de NAS-kopie van de recorder-segmenten (eigenaarsbesluit 24 okt ~18:05Z) is voor dit deel nog niet gelezen.
- Vervolg (plan §4): geldkaart en census opnieuw op de aaneengesloten store `fwd1064-1065-6a4458f` (≈ 2 dagen zonder epochgrens; commando's bij de coördinator), daarna 1066; de x10-basisrates alleen opnieuw op het gefixte harnas als E75 daar aanleiding voor geeft.
- Stap 2 (pre-registraties, sjabloon v0.2 5e4fad6f…): alleen voor een kandidaat met een dev-GO; E71/E72/E74 zijn STOP, E73 en E75 lopen in de nacht. De geldkaart levert geen cel op; de sniper-waarneming is een hypothese die eerst op 1064–1066 moet terugkomen vóór er een pre-reg uit volgt (en dan op een venster ná de hash).

## 6. Addendum 29 okt: sniper-lead opgelost (E78) en leeftijdskaart (E79)

**Sniper-lead (E78; feit; `money_fwd1064_1065.out` 034a5ca2…, `sniper_offsets_fwd1064.out` f54f0e77…, `sniper_offset0_mechanism.out` 2c306406…; SHA256SUMS 6ceaa75a…).** Op de aaneengesloten store 1064–1065 houdt de waarneming: zero-sum sluit (pool +0,094 %, curve −0,46 %), sniper-wallet-dagen realiseren +6,10 / +10,38 / +17,66 SOL (21/22/23 okt), 286 wallet-dagen ≥ 20 SOL dragen de hele som, top-10 wallets 62 % van de positieve som. Maar per ronde naar eerste-koop-offset (epoch 1064, curve + PumpSwap): offset 0 (het create-slot) +21.501 SOL op 71.392 in (+30 %), offset 1 +3,3 %, offset 2 +5,5 % (1.663 rondes, 460 SOL in), offset 3 −0,6 %, ≥ 4 negatief — wat wij kunnen halen (≥ 2 slots na de create) is ≈ 0 SOL over twee dagen. En offset 0 is de launcher zelf: 12.486 van de 27.106 offset-0-rondes zijn de creator zelf (+7.771 SOL: 12.203 niet-top-10-rondes +6.112 SOL, 33 %, plus 283 top-10-rondes +1.659 SOL, 95 %), en de top-10 "snipers" kopen op 8 van 10 uitsluitend munten van één creator (eigen launches); derde wallets in het create-slot verliezen (14.488 rondes, −565 SOL, −2,7 %). **De hypothese van §2 is daarmee opgelost: de sniper-plus is de dev-koop van launchers die hun eigen munt in de eerste golf verkopen — geen handelsstrategie, buiten het kader van de paper-bot (munten uitgeven = aparte eigenaarsbeslissing), en voor derden verliesgevend.** Geen probe, geen pre-reg (coördinator 29 okt). 0 blikken (beschrijvende sneden, geen regel gescoord; LAB review).

**Leeftijdskaart (E79; feit; `rounds_by_age_fwd1064.out` 248d7d54… van `rounds_by_age_fwd1064.v1.py` e1c5d8f2…).** Ronde = (wallet, mint) over epoch 1064, verkopen − kopen (curve + PumpSwap, bruto; netwerkfees apart als 0,000705 SOL per leg), naar de leeftijd van de munt bij de eerste koop en de venue van die koop; 1,89 M rondes, 566 k wallets. Derde partijen (wallet ≠ creator), eerste koop op de curve:

| leeftijd bij eerste koop | rondes | SOL in | gerealiseerd bruto | marge | na 0,0007 SOL/leg | rondes > 0 |
|---|---|---|---|---|---|---|
| create-slot (0) | 38.367 | 65.034 | **+103.332** | +158,9 % | +103.245 | 49,4 % |
| +1 slot | 12.131 | 6.216 | +356 | +5,7 % | +335 | 46,3 % |
| +2–3 slots | 10.667 | 5.190 | +277 | +5,3 % | +259 | 40,4 % |
| +4–10 slots (≈ 1–2 s) | 31.038 | 24.205 | +717 | +3,0 % | +648 | 39,9 % |
| +11–50 (≈ 2–10 s) | 109.804 | 86.322 | +612 | +0,7 % | +352 | 38,9 % |
| +51–150 (≈ 10–30 s) | 115.746 | 75.163 | −276 | −0,4 % | −548 | 35,0 % |
| 0,5–2 min | 168.908 | 102.952 | −2.880 | −2,8 % | −3.300 | 32,9 % |
| 2–10 min | 160.699 | 93.310 | −5.482 | −5,9 % | −5.894 | 32,3 % |
| 10–60 min | 57.890 | 37.341 | −1.620 | −4,3 % | −1.764 | 31,9 % |
| 1–6 u | 15.194 | 11.411 | −432 | −3,8 % | −466 | 31,7 % |
| > 6 u | 5.188 | 2.864 | −592 | −20,7 % | −600 | 25,3 % |

Eerste koop op de pool: elke bucket negatief (−18 … −63 %); in het create-slot (same-slot-migraties) 1.125 rondes, 146.002 SOL in, −77.794 SOL (−53 %) — de tegenpartij van de launchwinst. Creators zelf in het create-slot: 19.174 rondes, +9.117 SOL (+14,9 %). Per dag (derde partijen, curve): 21 okt +26,2 %, 22 okt +10,5 %, gedomineerd door offset 0. Post-hoc waarneming, geen kandidaatcel: naar grootte bij 2–10 slots is de marge < 0,1 SOL +1,8 … +17,6 %, 0,1–0,5 +1,5 … +1,6 %, 0,5–2 SOL −1,3 … +0,8 %, ≥ 2 SOL +8,5 … +12,7 % (aantallen per cel niet gepind). **Lezing:** een derde partij verdient op 1064 alleen in de eerste ≈ 2 s na de create, vooral in het create-slot zelf en bij kleine inzet; op onze grootte (0,5–2 SOL) en haalbare offsets (≥ 2–3 slots) ≈ 0 tot −1 % vóór netwerkfees; daarna en op de pool overal verlies. Geen kandidaat; een bevestiging van het regime (E65/E67: d 1 onbereikbaar; E71/E72). **Proces:** de kaart is gelezen vóór de pre-registratie van de buckets en vóór LAB's antwoord op de blik-vraag → 1 kaart-blik (LAB review 29 okt), epoch 1064 verkend voor leeftijdshypothesen. Herrun met v2 (b54e1e05…, buckets byte-identiek) op de aaneengesloten store 1064–1068 zonder extra blik; elke regel die eruit volgt krijgt een forward-pre-reg met venster ná de hash.
