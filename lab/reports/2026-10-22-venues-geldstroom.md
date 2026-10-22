# E60: "volg het geld" op de andere venues — Meteora DBC / DAMM v2 en Raydium LaunchLab / CPMM, epoch 1045 (2026-10-22)

| | |
|---|---|
| vraag | coördinator 22 okt (eigenaarsprioriteit ≥ 20 SOL/dag): (1) de taart per venue naast pump.fun/PumpSwap; (2) wie verdient er; (3) graduaties (DBC → DAMM v2, LaunchLab → CPMM) tegenover PumpSwap-migraties; (4) runners op DAMM v2 / CPMM met de dieptevloer óók op het runner-uur. Verkennend, dev; elke verklaring is een hypothese; geen regel, geen paper-arm |
| data | venue-store `lab/store/venues-1045-5055f7a.duckdb` (sha256 14201cf3…, #518/#521; tabellen dbc, damm2, launchlab, cpmm, venue_pools, venue_graduations; alleen rijen met quote WSOL, behalve waar anders gezegd) en dev2 (`pool`, `curve`, `mints`, `blocks`) op dezelfde slots 451.440.000–451.872.000; money-v2 wallet-dagen 28–30 sep voor de pump-overlap. **Epoch 1045 = 431.806 slots × 0,267 s ≈ 32 uur** (raakt drie kalenderdagen: eerste blok 2026-09-28 21:29:00Z, laatste 2026-09-30 05:36:03Z = 32,12 u uit `blocks`); "per dag" = totaal ÷ 1,33 |
| code / uitvoer | `research/venues-ftm-2026-10-22/` (v0_overview, v0b_semantics, v1_pie, v2_who, v2b_chains, v3_grad, v4_runners, v5_lp `.py`/`.out`, SHA256SUMS; v2_who.out is de herdraai ná de alias-fix — de eerste uitvoer brak af op een traceback; v5_lp pint de LP-, partner- en vensterfiguren van §2 die eerst uit losse queries kwamen); data `wallets/venues-ftm-1045/` (swaps.parquet 5e1dd541…, payers.parquet 841e3985…, payers-chains.parquet 62542cbb…, graduation-paths.parquet c1a956a2…, pumpswap-migration-paths.parquet 9fe8d9d3…, venue-hours.parquet 5ef7d2b0…). VPS, 3G-scopes, elke stap < 30 s |

Labels: feit / reconstructie / modelaanname / onbekend. Eenvoudige kasboekhouding (gedocumenteerd, niet money-v2): SOL-zijde per swap = wat de betaler aan WSOL betaalde of ontving (fees inbegrepen); venue-fee in SOL = de fee van het event als die in WSOL valt, anders omgerekend tegen de prijs van de swap zelf (DBC: fee op het uitgaande token; DAMM v2: mode 0 uitgaand token, anders token B; CPMM: ingaand token; LaunchLab: quote); netwerkfees = tx_fee + jito_tip per transactie. "Trip-kas" per (betaler, markt) = verkopen × min(1, gekochte / verkochte tokens) − kopen − netwerkfees: voorraad van vóór het venster telt niet mee, open voorraad wordt niet gewaardeerd (conservatief). Een launchpad-pool en zijn gegradueerde pool zijn één markt (venue_graduations); "Meteora" = DBC + DAMM v2-tokenpools, "Raydium" = LaunchLab + CPMM-tokenpools. Majors (SOL tegen stable of SOL als token A) staan apart: SOL-kas zegt daar niets.

## 1. De taart per venue (feit; 32 uur, per dag ÷ 1,33)

| venue | volume SOL | per dag | venue-fees SOL | per dag | netwerkfees | swaps | betalers | pools | same-slot-aandeel volume | LP-uitstroom SOL |
|---|---|---|---|---|---|---|---|---|---|---|
| **PumpSwap** (SOL-pools, dev2) | 24.789.987 | **18,6 M** | 101.925 | **76.400** | 2.160 | 23,9 M | 1.017.616 | 28.524 | – | 690.039 (opnames) |
| **pump.fun curve** (dev2) | 1.591.242 | 1,19 M | 19.066 | 14.300 | 2.019 | 5,07 M | 224.098 | 61.575 mints | – | – |
| DAMM v2 majors (SOL als token A: SOL/stables) | 2.604.798 | 1,96 M | 272 | 204 | 22 | 2.236.087 | 46.243 | 1.226 | 88,5 % | (118 k, LP van majors) |
| **DAMM v2 tokenpools** (WSOL = token B) | 446.145 | 335 k | 3.831 | 2.880 | 153 | 2.765.365 | 64.160 | 14.422 | 73,8 % | 28.068 (removes) |
| **Meteora DBC** | 194.428 | 146 k | 1.364 | 1.030 | 54 | 1.291.010 | 21.942 | 6.348 | 71,6 % | – |
| **Raydium CPMM tokenpools** | 161.158 | 121 k | 833 | 626 | 72 | 241.371 | 32.496 | 3.524 | 56,5 % | 963 (withdraws) |
| **Raydium LaunchLab** (WSOL-quote) | 34.347 | 26 k | 855 | 643 | 40 | 56.617 | 7.170 | 1.154 | 63,6 % | – |

- De pump-venues zijn in SOL-stroom 25–100 × groter dan elk ander venue (wash-stroom inbegrepen: E59). Alle vier de andere venues samen: ≈ 630 k SOL volume per dag en ≈ 5,2 k SOL venue-fees per dag tegen 77 k voor PumpSwap (feit).
- LaunchLab is in epoch 1045 voor 90 % **niet in SOL** genoteerd (666 k van 723 k rijen; vijf quote-mints met elk 500–900 pools, grootteorde van de virtual_quote 0,4–80 "× 1e9" — USD1/BONK-achtige quotes; niet in SOL gemeten, onbekend). Van de 751 WSOL-LaunchLab-pools die in het venster zijn aangemaakt gradueerden er 5; van de 5.759 DBC-pools 4.492 (78 %).
- Same-slot-aandeel (volume in slots waarin ≥ 2 betalers in dezelfde pool handelen): 57–74 % op de tokenpools, 89 % op de majors (feit; de E57-MEV-census mat PumpSwap met een andere definitie op 96 %).
- **Antwoord vraag 1:** nee — er is geen venue waar de taart groter is dan bij pump; de bereikbare taart (§2) is op Meteora nagenoeg nul en op Raydium één gebeurtenis.

## 2. Wie verdient er (feit op de kasdefinitie; 32 uur; "≥ 50 SOL" ≈ 37 SOL/dag)

| keten | betalers | trip-kas > 0 | ≥ 50 SOL | hun kas | winst totaal | verlies totaal | alleen-verkopers (voorraad van vóór het venster) | hun ruwe kas |
|---|---|---|---|---|---|---|---|---|
| Meteora (DBC + DAMM v2 tokenpools) | 74.527 | 3.847 | **7** | 557 | 5.973 | −83.987 | 11.393 | +12.324 |
| Raydium (LaunchLab + CPMM tokenpools) | 36.489 | 2.668 | **66** | 6.693 | 13.479 | −33.285 | 7.765 | +8.205 |

- **Meteora:** de 7 winnaars ≥ 50 SOL zitten verspreid over instap-offsets (mediane afstand tot de vorige leg van een ander: 0 slots 3 wallets / 178 SOL, 1 slot 1 / 170, 2–3 slots 2 / 148, > 3 slots 1 / 62). Profiel van de top: kleine multi-markt-snipers op DBC die na graduatie op DAMM v2 verkopen (515vh1Dr…: 73 markten, 239 swaps, +170 SOL, mediaan 1,8 SOL, instap mediaan 192 slots na poolcreatie, 86 % gesloten; GvRnTzBD… 52 markten +67; 3kxcF8wH… 41 markten +57). Geen creators onder de winnaars; 1 van de 7 handelde ook op pump (−37 SOL daar).
- **Waar het Meteora-geld heen gaat (reconstructie; cijfers gepind in `v5_lp.out`):** niet naar handelaren. Handelaren verliezen netto −78 k SOL in 32 uur; de venue-fees zijn 5,2 k; **DAMM v2-LP-onttrekkingen uit tokenpools 28.067 SOL, waarvan 25.653 uit gegradueerde pools** (4.107 removes op 4.015 pools, mediaan 6,1 SOL per pool, mediaan 727 slots ≈ 3 min na de graduatie; majors apart: 117.943 SOL) plus 12.324 SOL ruwe kas van 11.393 alleen-verkopers. Van de zes grootste onttrekkers horen er vier bij precies één DBC-config en doen per pool precies één swap (7PjT2rSR… 167 pools / 1.777 SOL / config EZmnjPix…, BcV2En8M… 168 / 1.725 / 3Dr15KpC…, 37BBJDzd… 101 / 1.260 / 6m7XSKNZ…, 5jmm5Cqw… 60 / 891 / FPs1mGXU…), 8ReCQzkp… (133 / 1.610) bij twee configs; de grootste, 8TPACXaK… (61 pools / 2.214 SOL), spant 61 configs met 678 swaps en is een andere speler (onbekend). **Hypothese: de vier zijn launchpad-partnerwallets (één swap per pool = de partnertoewijzing verkocht); de Meteora-launchpads verdienen aan de migratie-liquiditeit en de partnertoewijzing, niet aan handel.** BcV2En8M is tegelijk de grootste DBC-verliezer (−1.348: koopt in 168 pools vóór de graduatie) en DAMM v2-"winnaar" (+212): netto −1.136 op handel, +1.725 op LP.
- **Raydium:** 61 van de 66 winnaars hebben instap-offset 0 (same slot) en samen 6.154 van de 6.693 SOL; **55 van de 66 zitten in één pool, Atwk3wi3… (mint resideK2Ej…)**: LaunchLab-pool aangemaakt én gegradueerd in hetzelfde slot (451.783.683/684: 6 trades van 6 betalers vulden de curve met 86 SOL), daarna op CPMM 32.401 swaps, 4.816 betalers, 45.815 SOL volume, diepte tot 582 SOL; prijspad ×11 na 1 min, ×14 na 5 min, ×20 na 30 min, ×17 na 2 u. De winnaars kochten in de eerste slots (poolleeftijd 0–150 slots, 3–6 swaps, 20–70 SOL in, 190–290 SOL uit) — netto +5.785 SOL uit die ene pool. **Hypothese: een gebundelde lancering met vooraf geplaatste kopers; geen herhaalbaar patroon** (de andere 10 WSOL-graduaties van LaunchLab gaven geen winnaars van dit soort). Zonder die pool: 11 winnaars ≥ 50 SOL, ≈ 900 SOL, grotendeels same-slot. 11 van de 66 handelden ook op pump (+218 SOL daar); 1 creator; 6 op beide ketens.
- **Antwoord vraag 2:** op Meteora verdienen de launchpads (LP + toewijzing; hypothese), niet de handelaren; op Raydium verdiende in dit venster één lancering. Niets wijst op een handelaarsklasse met ≥ 20 SOL/dag die 2–3 slots na een trigger instapt: de ≥ 50-SOL-winnaars met mediane offset ≥ 2 slots zijn 3 op Meteora (210 SOL) en 5 op Raydium (539 SOL, waarvan 1 in Atwk3wi3).

## 3. Graduaties: prijspad ná de overgang (feit; prijs = effectieve prijs sol/tok van de swap; horizonten in slots van 267 ms)

| overgang | n (met handel erna) | sprong P1/P0 (mediaan) | 1 min ×P1 (aandeel > 1) | 5 min | 30 min | 2 u | 12 u | gehalveerd na 30 min | eerste swap na de overgang |
|---|---|---|---|---|---|---|---|---|---|
| DBC → DAMM v2 | 4.453 van 4.530 | 3,38 | 0,96 (48 %) | 0,00 (23 %) | 0,00 (16 %) | 0,00 (15 %) | 0,00 (21 %) | 82 % | 1 slot later, 71 % koop, ~0 SOL |
| LaunchLab → CPMM (WSOL) | 11 van 169 | 1,02 | 1,15 (73 %) | 1,06 (55 %) | 0,93 (36 %) | 0,32 (9 %) | 0,27 (0 %) | 27 % | 4 slots later, 73 % koop |
| **PumpSwap-migraties (dev2)** | 1.588 | 1,00 | **1,30 (65 %)** | **1,31 (58 %)** | 0,07 (25 %) | 0,055 (16 %) | 0,055 (13 %) | 71 % | in het migratieslot |

- PumpSwap heeft de bekende W8-B-vorm: een opleving van ≈ +30 % in de eerste 1–5 minuten (65 % van de pools boven de startprijs na 1 min), daarna de instorting tot 5–7 % van de startprijs binnen 2 uur (feit).
- DBC → DAMM v2 heeft **geen opleving**: 3.400 graduaties per dag op curves met een migratiedrempel van ≈ 11,5 SOL (mediaan quote-reserve 9,2 SOL), na 5 minuten is de mediaanprijs nul en 76 % gehalveerd; de sprong van 3,4 × bij de eerste DAMM-swap is een dun-pool-artefact (eerste swap ~0 SOL met eigen impact; modelaanname). De W8-B-vraag heeft hier geen handelbare vorm.
- LaunchLab → CPMM: 11 WSOL-graduaties, te weinig voor een uitspraak; de ene uitschieter (Atwk3wi3, ×20) bepaalt het p90.
- **Antwoord vraag 3:** het migratie-oplevingspatroon is specifiek voor PumpSwap; op Meteora bestaat het niet, op Raydium (SOL) zijn er te weinig gevallen.

## 4. Runners op DAMM v2 / CPMM (feit; niet beoordeelbaar)

Uurreeks per tokenpool (close = effectieve prijs van de laatste swap; diepte = WSOL-reserve na de laatste swap (DAMM v2) / WSOL-vault vóór de laatste swap (CPMM)), regel als E58 **met de dieptevloer op het minimum-uur én het runner-uur**. Het venster is 33 uur, dus het 24–72 u-terugkijkvenster bestaat hooguit 9 uur en meestal uit 1 uur: vloer 500 SOL → **0 runners**; vloer 100 SOL → DAMM v2 3 pools, CPMM 1 (nEnhtdXr… 3,05 ×, diepte 187 → 328 SOL, pool van vóór het venster); 5.659 DAMM v2- en 1.533 CPMM-tokenpools waren ≥ 24 u oud binnen het venster, 28 resp. 57 haalden ooit 500 SOL diepte. **Antwoord vraag 4:** niet te beantwoorden op één epoch; vraagt een venue-store over ≥ 4 epochs (NAS, lang: extractor-chunks + `backtest.venue_store dev`) — aparte beslissing.

## 5. Beperkingen en kijkmomenten

- Eén epoch (32 u) van dev, vóór Alpenglow; kasboekhouding zonder waardering van open voorraad en zonder transfers; majors buiten beschouwing; LaunchLab grotendeels niet-SOL; de pump-overlap op kalenderdagen 28–30 sep (ruimer dan het venster); prijs = effectieve swapprijs (fees en eigen impact inbegrepen). Niets hiervan is een claim; een lead zou een pre-registratie op ongeziene data vergen — er is geen lead.
- Kijkmomenten: ≈ 45 cellen (taart 7 × 4, wie 2 ketens × offsets + tops, graduaties 3 × 5, runners 4) → registerrij E60.
