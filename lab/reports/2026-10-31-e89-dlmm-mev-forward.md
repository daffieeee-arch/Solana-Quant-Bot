# E89 stap (b) — forward-lezing: bereikbaar MEV-aandeel Meteora DLMM bij onze latentie (epochs 1064–1068, 21–26 okt 2026) — lezing 31 okt 2026

Vervolg op `2026-10-30-e89-dlmm-mev.md` (stap (a), epoch 1045). Pre-registratie `research/e89-dlmm-mev-2026-10-30/e89-prereg-forward-2026-10-30.md` v0.2 (sha 5d5b4d9e…), gehasht 30 okt 17:41Z vóór de extractie; stap (b) is coördinatorsbesluit `ops/nas/QUEUE.md` 1008 (motivering 1009) binnen eigenaarsopdracht 999. Labels: **feit** / **reconstructie** / **modelaanname** / **onbekend** / **hypothese**. Regelnummers (r…) verwijzen naar `e89_read_census_timing_fwd1064-1068.out` (sha 70ecf207…) tenzij anders gezegd. Dit is een plafondmeting op verse DLMM-kolommen van verkende dagen: geen schone toets, geen kandidaat, geen "bewezen winst".

## 0. Kort
- **Lezing C (onbeslist) per pre-reg §1.** De pre-geregistreerde maat F_legs(d_any; k 3; inzet 1 SOL; kosten 0,001 SOL) komt op **0,98 SOL/dag, dag-bootstrap [0,48; 1,66]** over de vijf UTC-dagen met ≥ 12 h dekking (vier volle dagen: 1,07 [0,44; 1,88]; r1761). A vraagt een ondergrens > 2,0 SOL/dag in beide reeksen én een top-1-aandeel < 0,5: de ondergrens is 0,48 en de top-1 is 0,777 (r1356). B vraagt een bovengrens ≤ 0,010: die is 1,66. Het hele interval ligt onder de A-lat.
- **Feit: de reeks "zonder bcnzSs3F" is gelijk aan de volle reeks** (r1762 = r1761). Geen van zijn 270 arbs bij ≥ 2 slots heeft een been in de forward-store (n_gematcht 0, r1356). De legs-basis dekt 41,5 % van de DLMM-any-netto (153.881 van 609.950 arbs, r17), vrijwel alleen dlmm+pump_amm (82,4 % van de netto van die subklasse gematcht); van dlmm+pump, de curve↔DLMM-niche, is 0,02 % van de netto gematcht (r22). De tweede reeks had dus geen onderscheidend vermogen (§6.1).
- **Feit: de niche is post-Alpenglow groter en nog steeds één adres.** De ≥ 2-slots-taart van DLMM-any (d_any) is 361,5 SOL over het venster (127,4 h) met **bcnzSs3F 0,777** (270 arbs, 280,8 SOL, mediaan 29 slots na de laatste swap, landing 0,924; r1356); per dag 0,66–0,89 (r1704–1709) en in de subklasse dlmm+pump 0,999–1,000 op elke dag (r1714, r1718, r1722, r1726, r1730, r1734). Op 1045 was het 88,7 % van 66,7 SOL per epoch.
- **Modelaanname (bovengrens zonder beenmaat):** F_ub vol 57,3 SOL/dag [43,1; 72,0]; zonder bcnzSs3F 11,8 [6,8; 17,4] (r1763–1764).
- Geen kandidaat, geen paper-pad; per pre-reg §1 volgt uit C geen bouwvraag. Blikken: 1 (DEEL 2 + DEEL 8 samen); DEEL 0/3/6/7 0; DEEL 1/5 niet gelezen.

## 1. Uitvoering (feit)
- **Extractie 1064:** NAS-job `50-e89-census-fwd-1064` (wrapper sha 39516d30…; script `e89_extract_fwd.sh` e0d1d964…), 30 okt 17:43:12Z → 21:00:14Z, rc 0. Chunks 459648000–459864000: 35.504.482 venue_census-rijen (178 bestanden); 459864000–460080000: 27.475.263 (138 bestanden); http_429 0; "epoch 1064: GREEN"; raw-sums `SHA256SUMS.census-fwd-1064` 322 bestanden, sha 50703324… (`fwd/extract-1064/job.log` r1–6, `epoch-check-1064.log`).
- **Extractie 1065–1068:** job `51-e89-census-fwd-1065-1068` (wrapper c1d1c8d4…), 30 okt 21:01:44Z → 31 okt 07:32:42Z, rc 0, vier GREEN. Chunkrijen 1065: 32.350.775 / 29.122.082; 1066: 32.473.900 / 24.398.749; 1067: 28.136.976 / 23.334.471; 1068: 26.077.794 / 32.806.801; http_429 0. Raw-sums 1065 314 bestanden 792058d5…, 1066 291 dfc93da1…, 1067 264 0815cec3…, 1068 302 8345bebc… (`fwd/extract-1065_1066_1067_1068/job.log`, `epoch-check-106x.log`). Beide jobs: bin `/lab/bin/lab-extractor-27b8cd3` sha256 7138d837…, repo_sha 12878f2, THREADS 1, `LAB_VENUE_CENSUS=1`, time-out 6 h per epoch; schijf ≈ 20 GB per epoch.
- **Leesjob** `e89_job_fwd.sh` 768f0d75… = job `52-e89-read-fwd1064-1068` (wrapper 252423d5…, QUEUE 1022), gestart na de merge van #624 (ba7d417) en LAB's go; reader v7 fe1cb5e5… op REPO_SHA 12878f2; store `fwd1064-1068-eb3cba4` 39b01872…; .out 70ecf207… (1.766 regels, rc 0). Map-SHA256SUMS 06756422… (41 regels, vóór de run) → 3754bfdc… (52 regels, door de leesjob: beide jobs, vijf raw-sums, `fwd/report-1064-1068/{venue_day.parquet cb63f56e…, venue_census.md 221a31d4…, venue_census.json fae0ba6d…}`, .out) → 05ea2247… (59 regels, 31 okt: + de zeven joblogs van §1); `sha256sum -c` OK op de VPS (31 okt).
- **Poorten (r2–14):** G5 GREEN forward 1064–1068; G0 GREEN 10 chunks, 291.681.293 rijen = manifestsom (reader meldt `pin=None`: de job gaf `--pin-rows` niet mee — nit zonder gevolg, de som van de tien extractielog-tellingen is dezelfde 291.681.293); G2 GREEN 66 venue-dagen gereproduceerd (|Δnetto| ≤ 1e-6 SOL); G3 21 okt 10,40 h (< 12 h, valt af), 22–25 okt 24,00 h, 26 okt 20,97 h; G1 GREEN 1.566.393 arbs, 3.630.677 arb-legs, 250 herberekend op ruwe strings; G4 GREEN 714.756 benen gematcht, DLMM-any 153.881 (bron store); ALL GATES GREEN. LAB las de poorten en DEEL 0 vóór de lees-go; de lees-go kwam 31 okt.
- **Datagebruik:** 1064–1068 was eerder verkend voor pump-tabellen (E81–E84, E88); de DLMM-/census-rijen waren nooit bekeken (pre-reg §2). Geen hold-out (1047–1051), geen schoon (1055–1063). Het census-rapport van main (`fwd/report-1064-1068/`) is niet gelezen.

## 2. DEEL 0 (feit; r15–24)
| grootheid | waarde |
|---|---|
| census-rijen | 291.681.293 |
| arbs (census-definitie v1) | 1.566.393 |
| DLMM-any-arbs | 609.950 |
| arbs op de randdag 21 okt | 232.015 |
| DLMM-any met eigen stap korter (d_any < d_other) | 94.723 (15,5 %) |
| arbs zonder voorganger (any) / deels zonder (other) | 186 / 17.440 |
| benen gematcht (store) / waarvan DLMM-any | 714.756 / 153.881 = 41,5 % van de DLMM-any-netto |

Per subklasse (r21–24; gematcht = store-been op (slot, tx_index)): dlmm+pump_amm 195.243 arbs, 135.428 gematcht (69,4 % van n, 82,4 % van de netto; mediaan been 0,10 SOL, p90 1,40); dlmm+overig 369.352 / 18.354 (5,0 % / 1,4 %; mediaan 0,0034); **dlmm+pump 1.139 / 99 (8,7 % / 0,02 %; mediaan 0,012)**; dlmm_only 44.216 / 0.

## 3. De blik: dagreeks en ladder (modelaanname; DEEL 8 r1738–1764, DEEL 2 r1083–1277)
F = A′ + B − n_won · 2,155 · 0,000505 (pre-reg §3): A′ = onbetwist bij d_any ≥ 4, B = gelijkspel bij d_any = 3 alleen als de winnaarskosten ≤ c; basis `legs` = gematchte arbs met kwadratische cap f(s/legs), basis `ub` = alle arbs met f = 1.

| UTC-dag (uren) | F_legs k 3 / s 1 / c 0,001 (per 24 h) | F_ub vol | F_ub zonder bcnzSs3F |
|---|---|---|---|
| 22 okt (24,00) | 0,77 | 80,07 | 8,50 |
| 23 okt (24,00) | 1,03 | 31,91 | 3,75 |
| 24 okt (24,00) | 2,24 | 48,74 | 16,50 |
| 25 okt (24,00) | 0,24 | 54,71 | 9,32 |
| 26 okt (20,97) | 0,63 | 71,10 | 20,80 |

(r1740–1759; F_legs zonder bcnzSs3F = F_legs vol op elke dag.) Dag-bootstrap (10.000, seed 7; r1761–1764): legs vol en zonder **0,983 [0,477; 1,656]** (5 dagen), 1,072 [0,440; 1,876] (4 volle dagen); ub vol 57,31 [43,11; 72,01] en 53,86 [37,61; 72,24]; ub zonder 11,77 [6,76; 17,36] en 9,52 [5,14; 14,50].

Ladder DLMM-any, basis legs, per volle dag 22 / 23 / 24 / 25 okt (c 0,001): k 3 s 0,25: 0,30 / 0,50 / 0,84 / 0,10 (r1188, r1212, r1236, r1260); **k 3 s 1: 0,77 / 1,03 / 2,24 / 0,24** (r1190, r1214, r1238, r1262); k 3 s 5: 1,12 / 1,73 / 5,26 / 0,33 (r1192, r1216, r1240, r1264); k 2 s 1: 1,14 / 1,21 / 2,47 / 0,27 (r1184, r1208, r1232, r1256); k 4 s 1: 0,63 / 0,94 / 2,22 / 0,20 (r1196, r1220, r1244, r1268). Bij c 0,0001 ligt F 0,04–0,16 lager (meer gelijkspel-winsten, dus meer mislukte pogingen; r1189, r1213, r1237, r1261). Basis ub k 3 c 0,001: 80,07 / 31,91 / 48,74 / 54,71 (r1202, r1226, r1250, r1274); k 2: 89,19 / 33,33 / 51,95 / 61,72; k 4: 72,48 / 31,77 / 47,73 / 45,30 — de ub-ladder daalt nauwelijks met k, als op 1045: wat traag open staat, staat lang open.

Tegenover 1045 (noot (a) §3: F_legs 9,79 SOL/dag bij 1 SOL via de mev0-beenmaat, dekking 64,3 % mét de niche-benen van mediaan 20,9 SOL; d_other) staat nu ≈ 1,0 SOL/dag bij 41,5 % dekking zónder de niche en op d_any. De twee zijn niet één-op-één te vergelijken (§6.1).

## 4. Concentratie bij ≥ 2 slots (feit; 0 blikken; DEEL 3 r1345–1366, DEEL 7 r1702–1736)
- **Taart per dag (per 24 h, d_any ≥ 2):** 21 okt 132,0 (randdag, 10,4 h), 22 okt 89,7, 23 okt 33,0, 24 okt 52,2, 25 okt 62,7, 26 okt 76,4 SOL; 239–301 payers per dag (r1704–1709). De gematchte netto in die taart is per dag maar 0,59–6,68 SOL (r1347–1352): de trage taart met bekende beenmaat is klein; de grote trage taart is de niche zonder beenmaat.
- **Top-10 over het venster** (r1356–1365; taart 361,5 SOL; mislukt = mislukte transacties van de payer in het venster, kosten in SOL): bcnzSs3F 280,8 SOL (0,777; 270 arbs, mediaan d 29, mediaan bruto 0,96 SOL, 1.418 arbs over alle d, 116 mislukt à 0,22 SOL, landing 0,924); NALUxTJs 29,2 (0,081; 1.617 arbs, mediaan d 10, mediaan been 0,28 SOL, fooi-aandeel 0,555, landing 0,953); MriyaNN8 18,6 tegen 86.024 mislukt à 43,4 SOL (landing 0,240); HvFdDWS3 4,6 tegen 25,4 mislukt; 4XBqViD1 3,8 tegen 20,9; A5Y3Wz4t 2,7; GxDC9e7S 2,3 tegen 23,1; HL2kH8ya 1,9 tegen 12,2; FfAJbv3p 1,7 tegen 1,5; 3U2tSmAB 1,6 (16.330 arbs, 0 mislukt). Buiten bcnzSs3F en NALUxTJs verliezen de trage payers op mislukte transacties, als op 1045.
- **Niche per dag (DEEL 7):** bcnzSs3F netto 42,6 / 79,6 / 28,2 / 34,7 / 49,2 / 46,5 SOL (21–26 okt), aandeel 0,745 / 0,888 / 0,854 / 0,664 / 0,784 / 0,698, 27–81 arbs per dag, beenmaat NULL (geen store-been); de subklasse dlmm+pump is elke dag 0,999–1,000 van hem (DEEL 7b).

## 5. Lezing (pre-reg §1, letterlijk)
- **A:** ondergrens vol 0,48 ≤ 2,0; zonder 0,48 ≤ 2,0; top-1 0,777 ≥ 0,5 → nee, op alle drie.
- **A1:** "ondergrens > 2,0 in de volle reeks maar ≤ 2,0 zonder bcnzSs3F, óf top-1-aandeel ≥ 0,5". De volle reeks komt niet boven 2,0, dus de niche "bestaat" niet in de zin van de maat; het tweede lid (top-1 ≥ 0,5) is wél waar. Ik lees A1 als "A faalt alléén op het één-adres-criterium" → nee. De letterlijke óf-lezing zou A1 geven; LAB beslist het label. De gevolgen zijn gelijk: geen bouwvraag vanzelf.
- **B:** bovengrens 1,66 > 0,010 → nee.
- **C: onbeslist.** Pre-reg §1: geen verdere toets zonder nieuw idee, geen herhaling op dezelfde dagen. Opmerking (geen lezing): de 97,5 %-grens 1,66 ligt onder de A-lat 2,0; pre-reg §4 zei vooraf dat vijf dagen A alleen beslissen bij een ware dagwaarde ≥ ≈ 6,5 — de gemeten waarde is ≈ 1.
- **Power achteraf (reconstructie):** dag-sd van de legs-reeks ≈ 0,76 bij gemiddelde 0,98 → T ≈ 8,6 · (0,76 / 0,98)² ≈ 5 dagen voor "> 0"; de uurreeksen (DEEL 6, 0 blikken) geven per dag T 1,1 / 1,1 / 5,4 / 1,3 d (r1520, r1580, r1640, r1700); 24 okt is spits (sd_dag 1,79).

## 6. Kanttekeningen
1. **Dekking van de beenmaat (feit + hypothese).** De store-beenmaat matcht de curve↔DLMM-niche niet: dlmm+pump 0,02 % van de netto, bcnzSs3F 0 van 270 (§2, §4). **Hypothese:** de `mints`-tabel van de forward-store kent alleen mints met een create-event in of rond het venster; de inner join op `mints.quote_mint = SOL-sleutel` laat curve-benen van oudere tokens vallen, en juist oudere pump-tokens hebben een DLMM-pool (vgl. de NULL-mint-pools van vóór het venster, E81 v0.2; voor pools bijt dat hier niet omdat de poolfilter op `pools.quote_class` loopt). Niet geverifieerd. Voorstel: een 0-blik-dekkingsdiagnose (alleen de store: curve-rijen zonder `mints`-rij en hun aandeel; script eerst gehasht, LAB-go). Op 1045 kwam de beenmaat uit mev0's eigen extractie en matchte de niche wél (mediaan 20,9 SOL).
2. **De pre-geregistreerde tweede reeks had geen onderscheidend vermogen** op de legs-basis. Op de ub-basis is "zonder bcnzSs3F" 11,8 SOL/dag [6,8; 17,4]: wat de overige payers bij ≥ 3 slots werkelijk vingen, zonder groottecap en met gemodelleerde mislukte pogingen — een plafond voor "al het andere".
3. **d_any tegen d_other:** 94.723 DLMM-any-arbs (15,5 %) hebben een eigen stap die korter is dan de laatste andere trade; de primaire maat is d_any (strenger, pre-reg §3). DEEL 1b (d_other) is niet gelezen.
4. **Rekensom voor de eigenaarsvraag "1 tot 6 SOL per dag" (reconstructie op gepinde getallen; geen blik, geen lezing):** de niche is ≈ 53 SOL/dag bruto voor één speler die mediaan 29 slots laat is (280,8 SOL / 5,31 d). Met de 1045-mediaan van 20,9 SOL per been en de kwadratische cap bij 1 SOL (f ≈ 0,093) zou dat ≈ 4,9 SOL/dag zijn als elke niche-arb onbetwist op die maat gewonnen werd. Drie onbekenden: de verse beenmaten (niet gemeten, punt 1), de DLMM-zijde (prijs, bins, uitvoerbaarheid: geen decoder) en de concurrentie met een speler die 92 % van zijn pogingen landt. Het gemeten deel van de taart met bekende beenmaat (PumpSwap-zijde) geeft ≈ 1 SOL/dag [0,5; 1,7]. Beide zijn bovengrenzen.
5. **Nit:** G0 `pin=None` — de leesjob gaf `--pin-rows` niet mee; de telling is langs de manifests en de extractielogs gesloten (§1).
6. **Disclosure (LAB-ruling 31 okt: geen extra blik — de pre-reg was gehasht vóór de extractie, er viel niets te tunen en ik had niets gezien; wel één regel per incident):** (i) de coördinator zag bij de controle van de jobuitvoer, 31 okt vóór de lees-go, met `grep "^DEEL"` de vier samenvattingsregels van DEEL 8 (r1761–1764); er is geen lezing uit getrokken. (ii) LAB review zag, 31 okt vóór de lees-go, door een awk-anker op `^DEEL 1` de DEEL 1-rijen van c:alle (r28–62); er is geen lezing uit getrokken. De exacte tijdstippen staan bij de coördinator en LAB (QUEUE).

## 7. Vraag 2 ("anders groter")
Niets nieuws gemeten: de instructietelling (DEEL 5, 0 blikken) is voor deze noot niet gelezen en blijft voor een latere vraag; de andere venues zitten in DEEL 1 (0 blikken, niet gelezen). De stand van (a) §6 geldt: limit orders en LP-posities op DLMM vragen een DLMM-decoder; Jito simulateBundle alleen als simulatie; geen paper-pad zonder een tweebenige arb-simulator.

## 8. Wat dit niet meet
Geen DLMM-prijzen/bins (geen decoder); geen landingskans voor ons; de cap neemt de beenmaat van de winnaar als optimum; ≈ 5,3 dagen in één regime (post-Alpenglow, 212 ms slots); 1064–1068 zijn verkende dagen voor pump-hypothesen; de beenmaat dekt 41,5 % van de netto en de niche niet (§6.1); "zonder bcnzSs3F" verwijdert zijn gevangen arbs, niet de kansen die hij liet liggen; een A was een plafond geweest, geen kandidaat.

## 9. Pins
`.out` 70ecf207321bad45773dd010d4384f3753cd2d4a9395acb053cd51a1b531e0a6 (poorten r2–14; DEEL 0 r15–24; DEEL 1 r26–806; DEEL 1b r807–1082; DEEL 2 r1083–1277; DEEL 2b r1278–1344; DEEL 3 r1345–1366; DEEL 5 r1367–1461; DEEL 6 r1462–1701; DEEL 7 r1702–1737; DEEL 8 r1738–1764; rc=0 r1766). Map-SHA256SUMS `research/e89-dlmm-mev-2026-10-30/SHA256SUMS` 05ea2247fff1549138ab7782ed9b0e3e69550fd080a831f8ea2a27f4e6a7518b (59 regels; 3754bfdc… = de 52 regels van de leesjob, 06756422… = de 41 regels van vóór de run): pre-reg v0.2 5d5b4d9e…, reader v7 fe1cb5e5…, jobs e0d1d964… / 768f0d75…, raw-sums 50703324… / 792058d5… / dfc93da1… / 0815cec3… / 8345bebc…, joblogs 72b27f58… (1064 job.log), e13c4bdc… (epoch-check-1064), b1268e80… (1065–1068 job.log), 1257d2ed… / 3dee174a… / 753752c6… / 41c51ddb… (epoch-checks 1065–1068). NAS-jobs 50-e89-census-fwd-1064 (39516d30…), 51-e89-census-fwd-1065-1068 (c1d1c8d4…), 52-e89-read-fwd1064-1068 (252423d5…; QUEUE 1022). Store `fwd1064-1068-eb3cba4` 39b01872…; bin 7138d837…; repo 12878f2; #624 ba7d417.
