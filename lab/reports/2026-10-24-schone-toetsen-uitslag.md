# Uitslag van de schone toetsen van zaterdag 24 oktober 2026 — deel 1: E20 (LAB research)

**Status: E20 gelezen (24 okt 14:40:57Z); H3/H4/H5, E45, E49 en E56 lopen na E20 op dezelfde vergrendelde store en worden in een volgende versie van deze noot toegevoegd.** Alle regels waren vóór de eerste blik vastgelegd (hypothese 35bdaa0d…, aanvulling v5 94d09d9f…, uitsluitingen ccfaed98…, lees-addendum van LAB review 1d8d2211…); niets hierin is na het zien van de data aangepast. Labels als in het register: **feit** / **reconstructie** / **modelaanname** / **onbekend**. Namen van de eigenaar: "Terugveer-strategie" = F7 (kopen na een grote verkoop in een omgekeerde PumpSwap-pool), "Terugveer snel" = K3 (de paper-kandidaat), "Terugveer lang" = H4/H5, "willekeurige vergelijking" = N2.

Elk getal in deze noot komt uit `research/clean-test/clean-be47fc4/result/result.json` (sha 04965a1d…) en `result.md` (3ee5549f…) van de gepinde run, of uit een gearchiveerd script met uitvoer in `research/clean-test/e20-uitlezing-2026-10-24/` (SHA256SUMS daar); de §7-noot van de aanvulling is `lab/reports/2026-10-24-wash-trigger-clean.md` (gegenereerd met `wallets.clean_reports`, leest alleen result.json).

## 0. In één oogopslag

| toets | vraag in gewone woorden | regel vooraf | uitkomst | wat het betekent |
|---|---|---|---|---|
| **E20** — Terugveer snel (K3) met en zonder wash-trigger-filter | verdient K3 op dagen die niemand eerder zag, na alle kosten, als de triggers worden weggelaten waarbij de "grote verkoop" in dezelfde transactie alweer terugkoopt? | H1: K3 zonder rt_tx > 0 per trade (ondergrens dagbootstrap > 0) **én** H2: zonder − met > 0 (ondergrens > 0) → "bevestigd"; alleen H2 → "risicofilter zonder bewezen winst"; H2 niet → "verworpen" | **risicofilter zonder bewezen winst** — H1 niet gehaald (−0,11 % [−0,86; +0,32]), H2 gehaald (+2,76 % [+0,70; +5,19]) | het filter haalt de slechtste triggers weg (die verloren −2,88 % per trade), maar wat overblijft verdient niets; K3 als geheel verloor ≈ 1,5 SOL per dag bij 10 SOL inzet (reconstructie met het kostenmodel) |
| H3, H4, H5 (Terugveer snel met hold180; Terugveer lang) | — | eigen vastleggingen (dbef1b8 / 165bb04 / 15fa3dd) | nog niet gelezen | — |
| E45, E49, E56 | — | 94df3921… / a6e5f70e… / 08a99f5c… | nog niet gelezen | — |

**Eén zin voor de eigenaar (E20):** Terugveer snel verloor op tien dagen die niemand eerder had gezien gemiddeld 1,5 SOL per dag bij 10 SOL inzet, na alle kosten; twee van de tien dagen waren positief. Het wash-filter haalt de slechtste triggers weg, maar wat overblijft verdient niets (−0,11 % per trade): een risicofilter zonder bewezen winst, precies de vooraf vastgelegde uitkomst. (reconstructie met het kostenmodel)

## 1. E20 — Terugveer snel op schone data

### 1.1 Wat vastlag en wat is gelezen (feit)

- **Regel vooraf:** hypothese `hypothesis-wash-trigger-2026-10-15.md` (sha 35bdaa0d…, #167, 15 okt, vóór E18), aanvulling v5 `prereg-wash-trigger-addendum-2026-10-15.md` (94d09d9f…), uitsluitingen `case-exclusions-clean-data.md` (ccfaed98…). Eén toets, één blik: configuratie K3 (F7 pool_reversed, tp4_T600, farm alle, 0,5 SOL, q 0,5, overlay geen, basiskosten), d_ms 600 via de Clock van de engine.
- **Engine-pin:** `be47fc425fc46942884db256e5c9d809a11d57a9` (tag `clean-e20-pin-2026-10-24`, tak vanaf 46a0236), gezet per erratum op #167 (issuecomment-6384965518, 13:45:42Z) vóór de herstart: de bouw op 46a0236 brak om 12:00:28Z op een u64-reserve > INT64 in epoch 1061 (store-cast; geen uitkomst gelezen). De replay-bestanden zijn byte-gelijk aan f2c9666 (de job controleert dat zelf: `replay code equal to f2c9666` voor alle tien bestanden in job.log). Gevolg van de fix in de store-meta (feit): `excluded_pools_int64` = 2 pools (reversed 0, not_reversed 2; rijen buiten bereik buy 1, sell 2) — geen omgekeerde pool, dus het K3-universum is ongewijzigd.
- **Data:** store `clean-1055-1062.duckdb` (sha e8db6e8d…), venster slots [455.760.000, 459.216.000) = epochs 1055–1062, 18 chunks, extractor f5dd11df…, warm-up 1 epoch (1054, geen beslissingen). Epoch 1063 is door OF1 niet vóór de 12:00Z-klok gepubliceerd; per aanvulling §4 geldt dan de klok en draait de toets op wat compleet is. d per epoch = 3 slots in alle acht epochs (≈ 218 ms per slot). UTC-handelsdagen: **10** (11–20 oktober 2026), minimum 5 gehaald. Toets-run en matrix-run zijn per epoch rij voor rij gelijk (644/709/748/721/794/608/290/335).
- **Uitsluitingen:** 219 mints en 16 pools uit de drie regels van ccfaed98…; 0 triggers en 0 van de 4.849 K3-posities vervielen (de uitgesloten gevallen kwamen in dit venster niet als K3-positie voor).
- **Gevulde K3-trades:** 4.424 (met rt_tx 867, zonder 3.557, zonder trigger-leg 0); mislukte instappen 425 (netto −0,2146 SOL; alleen in de dag-P&L van de matrix en de DSR, niet in H1/H2 per gevulde trade, zoals vastgelegd).

### 1.2 Uitkomst (de vastgelegde regel; reconstructie met het kostenmodel)

| toets | n | waarde per gevulde trade | 95 %-interval (dagbootstrap 10.000, seed 7) | gehaald |
|---|---|---|---|---|
| H1: K3 zonder rt_tx | 3.557 | **−0,11 %** | −0,86 % … +0,32 % | **nee** |
| H2: zonder − met rt_tx | | **+2,76 %** | +0,70 % … +5,19 % | **ja** |
| (K3 met rt_tx) | 867 | −2,88 % | −5,18 % … −1,21 % | |

**Beslissing (vooraf vastgelegde categorie): risicofilter zonder bewezen winst.** Het filter is niet "bevestigd" (dat vroeg H1 én H2) en niet "verworpen" (dat was H2 niet gehaald). Omdat de puntschatting van H1 onder nul ligt, volgt uit E20 **geen** forward-pre-registratie (eigenaarsbeleid "onbeslist → vooruit toetsen" van 23 okt geldt alleen bij puntschatting > 0 en ondergrens ≤ 0).

Ernaast, beschrijvend en zoals vastgelegd geen beslisregel: `wash_1h` n 897 −2,99 % [−5,22; −1,35], geen wash_1h n 3.527 −0,06 % [−0,79; +0,37], geen rt_tx en geen wash_1h n 3.512 −0,05 % [−0,78; +0,38]. **DSR** van de H1-reeks (K3 zonder rt_tx bij d_ms 600; N = 42 trials, T = 10 dagen): 0,031 met de Sharpe-variantie van de 36 dev-trials, 0,004 met de nulvariantie 1/T; PSR tegen 0 = 0,324; de 43e kolom (K3 alle, Clock-d): 0,000 / 0,000. **PBO** (CSCV, 43 kolommen, T = 10, 10 blokken, 430 van 430 cellen ≠ 0): 0,000, helling −0,532, kans op verlies 0,028. De voorbehoudregel van de aanvulling (DSR < 0,95 of PBO > 0,5) staat erbij; ze verandert de beslissing niet. a4c (ongewijzigd, d = 1 en 2 op de matrix-run) tegen `backtest.wash`: equal. Het periodelabel "hold-out" in `a4c-matrix-run.json` is het cosmetische label van a4c's vierde argument (aanvulling §1); het venster is de schone periode 1055–1062, niet de hold-out 1047–1051.

### 1.3 Vergelijking met dev en hold-out (beschrijvend; E16/E18 waren diagnoses op verbruikte data)

| periode | d | K3 zonder rt_tx | K3 met rt_tx | bron |
|---|---|---|---|---|
| dev dag 10–18 (E16) | 1 / 2 | +1,94 % [+1,39; +2,24] / +1,00 % [+0,42; +1,33] | −0,35 % / −0,56 % | `2026-10-15-blindspots-v0.md` §4 |
| hold-out 1047–1051 (E18) | 1 / 2 | +1,10 % [+0,31; +1,58] / +0,08 % [−0,73; +0,67] | −0,69 % / −0,77 % | idem §4.1 |
| **schoon 1055–1062 (E20)** | 3 (≈ 600 ms) | **−0,11 % [−0,86; +0,32]** | **−2,88 % [−5,18; −1,21]** | deze toets |

Lezing (reconstructie): de richting van het filter is op alle drie de periodes dezelfde (wash-triggers zijn slechter), maar de voorsprong van de "echte verkopen" is van dev (+1,0 … +1,9 %) via de hold-out (+0,1 … +1,1 %) naar nul gegaan op de schone data; E18 waarschuwde daar al voor ("bij d = 2 ook zonder wash-triggers geen voorsprong meer"). De wash-groep is op de schone data juist slechter geworden (−2,9 % tegen −0,4 … −0,8 %). Het regime verschilt ook: 218 ms per slot en d = 3 tegen 267–316 ms en d = 1–2 op dev.

### 1.4 Beschrijvende kolommen (lees-addendum §2; verandert het oordeel niet)

Alle getallen hieronder uit `research/clean-test/e20-uitlezing-2026-10-24/e20_k3_dag.py` (sha 7dd83ec6…) en `e20_k3_dag.out` (f290601b…) op de gepinde parts van de toets-run (`backtests/clean-1055-1062-459216000-be47fc4/parts/`, config efaccda6… = `clean.lock`), K3 alle triggers = de 43e kolom; pariteit met result.json: 4.849 posities, 425 mislukt, 4.424 gevuld, 10 dagen, −0,2146 SOL, gewogen gemiddelde −0,656 % per gevulde trade — alles gelijk (feit).

**Strengere ondergrenzen (§2.2) van K3 alle triggers, per gevulde trade:** gemiddelde −0,656 %; dagbootstrap-ondergrens 95 % −1,29 %, 99 % −1,54 %, 99,9 % −1,93 %; t-interval op de daggemiddelden (t₉) −1,80 %. Voor H1 (K3 zonder rt_tx) en H2 vragen deze kolommen de rt_tx-vlag per rij; die staat niet in de parts en volgt uit `k3-rows.parquet` van Lab Backtest (lezing van de vergrendelde uitvoer, geen herrun) — **nog onbekend**.

**Dag-P&L van de matrix (SOL per handelsdag, 0,5 SOL per trade, elke trigger, mislukte instappen inbegrepen):**

| dag (UTC) | gevuld | mislukt | netto SOL |
|---|---|---|---|
| 11 okt | 164 | 23 | −1,63 |
| 12 okt | 426 | 59 | −1,69 |
| 13 okt | 603 | 86 | +0,03 |
| 14 okt | 615 | 69 | **−3,95** (slechtste) |
| 15 okt | 587 | 70 | −1,12 |
| 16 okt | 766 | 24 | −0,07 |
| 17 okt | 529 | 40 | −0,87 |
| 18 okt | 327 | 22 | −3,47 |
| 19 okt | 237 | 29 | −3,68 |
| 20 okt | 170 | 3 | +1,66 (beste) |

Gemiddeld **−1,48 SOL/dag**, totaal −14,78 SOL over 10 dagen; ondergrens 95 % −2,54, 99 % −2,87, 99,9 % −3,24 SOL/dag, t-interval −2,78; 2 van de 10 dagen positief; eerste helft (11–15 okt) −1,67, tweede helft (16–20 okt) −1,29 SOL/dag (§2.3). Exits: take-profit 2.939 trades (+4,9 % gemiddeld), stop-loss 1.378 (−12,8 %), tijd 107 (+1,7 %). Per farm-label: flagged 3.437 trades +0,15 % (+2,5 SOL), organic 987 trades −3,45 % (−17,1 SOL) — beschrijvend; het label "organic" is een gereconstrueerde farmer-vlag.

**Eigenaarskader 10 SOL (0,5 SOL per trade, hoogstens vijf open posities, triggervolgorde):** het kader bindt nooit — maximaal 3 posities tegelijk open, 0 van 4.424 trades overgeslagen — dus **−1,48 SOL/dag bij 10 SOL** is hetzelfde cijfer als de matrix-dag-P&L. Bij 25 en 100 SOL (meer plekken of grotere posities) wordt het verlies in absolute zin alleen groter; de richting verandert niet (modelaanname: de vulling met eigen prijsimpact komt uit de replay bij 0,5 SOL; grotere posities zijn hier niet gesimuleerd). Mét het rt_tx-filter: reconstructie uit result.json ≈ 3.557 × 0,5 SOL × −0,11 % ≈ −2,0 SOL over 10 dagen ≈ −0,2 SOL/dag (niet gepind tot k3-rows).

**Fout en trefkans van de vastgelegde regel bij T = 10 (§2.1; modelaanname: de dev-dagvorm is de ware verdeling):** dev-vorm = de dev-K3-rijen (f7rev-1033-1046, d = 2 als dichtstbijzijnde dev-vertraging bij 600 ms, 20 UTC-dagen) met de rt_tx-vlag uit `backtest.wash.flags` op dev2 (`dev_k3_rt.py` → `dev-k3-rt.parquet`; vlagdekking 100 %, rt_tx-aandeel 23,5 %, E16 zei 23,8 %; dev-gemiddelden d = 2: zonder rt_tx +0,86 %, met −0,91 %, verschil +1,76 % — reconstructie, dev verbruikt). De dev-populatie wordt per s eenmaal verschoven naar s × het dev-effect, daaruit 1.000 vensters van T = 10 dagen met teruglegging (seed 7), per venster de regel precies als vastgelegd (gepoold gemiddelde, dagbootstrap 10.000, seed 7, 2,5e percentiel > 0) en ernaast het t-interval op de ongewogen daggemiddelden (`e20_fp_power.py` / `e20_fp_power.out`):

| regel | s = 0: eenzijdige fout (nominaal 2,5 %) | s = 0,5: trefkans bij de helft van dev | s = 1: trefkans bij het dev-effect |
|---|---|---|---|
| H1 (K3 zonder rt_tx > 0) | bootstrap 1,6 %; t-interval 0,1 % | 18,1 %; t 2,0 % | 66,3 %; t 16,9 % |
| H2 (zonder − met > 0) | bootstrap 2,2 %; t 0,5 % | 50,5 %; t 10,7 % | 99,7 %; t 62,2 % |

Lezing: de dagbootstrap van E20 is bij T = 10 niet te soepel (fout ≈ 2 %; anders dan bij de H5-regel op dagsommen, waar LAB review ≈ 5–7 % vond, want de trade-gewogen poolmaat varieert minder per dag); H1 had bij een half dev-effect (+0,43 % per trade) maar ≈ 18 % kans om te slagen, bij het volle dev-effect ≈ 66 %. Het niet-halen van H1 is dus op zichzelf geen bewijs van afwezigheid van een half effect — maar de puntschatting ligt hier met −0,11 % ver onder beide, en het 95 %-interval [−0,86; +0,32] sluit het volle dev-effect (+0,86 %) uit. H2 had ruime trefkans (≈ 50 % bij de helft, ≈ 100 % bij het volle effect) en is gehaald. Het t-interval op ongewogen daggemiddelden is hier veel conservatiever omdat dagen met weinig trades zwaar wegen; de vastgelegde regel is de bootstrap.

### 1.5 Wat dit betekent (en wat niet)

- **Voor Terugveer snel (K3, paper):** niets verandert aan de paper-engine of de stopregel (die beslist op paper-data, ≥ 200 gevulde trades en ≥ 5 dagen). Wat E20 zegt: op tien ongeziene pre-Alpenglow-dagen verloor K3 geld bij elke inzet, en het beste bekende filter maakt er nul van, geen winst. Een forward-toets van het filter volgt niet (H1-punt < 0).
- **Voor het filter als risicomaatregel:** H2 is gehaald: triggers waarvan de verkoop in dezelfde transactie terugkoopt, zijn op drie periodes slechter dan echte verkopen. Als K3 om een andere reden ooit draait, is het weglaten van rt_tx-triggers verdedigbaar als risicofilter; het bewijst geen edge. Elke toepassing in paper vraagt spec, build en eigenaarsbesluit.
- **Onafhankelijkheid:** E20 zegt niets over H3/H4/H5 (eigen vastleggingen, zelfde triggers en dagen); een positieve H bij een negatieve E20 is geen redding van K3, en meerdere positieve H's zijn één bevinding.
- **Regime:** de schone data zijn pre-Alpenglow (218 ms per slot, d = 3); paper draait ná de omschakeling (E67: 212 ms, aanbod 0,29× dev). Dit oordeel geldt voor het oude regime.

## 2. Eerlijkheid

- **Data:** 1055–1062 is hiermee verbruikt (register: datagebruik → verbruikt, bevestigende toetsen). Epoch 1063 bestaat niet in OF1 vóór de klok; de hold-out 1047–1051 is niet aangeraakt. Uit clean-v1 is vóór de run alleen de bouwlog van 12:00:28Z gezien (foutklasse en chunk; erratum).
- **Blikken:** E20 telt als één blik (H1 én H2 vormen één regel); de beschrijvende kolommen van §1.4 beslissen niets en tellen niet (lees-addendum §2.6). Projectbrede teller na E74 ≈ 2.305 + E20.
- **Wat we niet weten:** de gefilterde kolommen van §1.4 (strengere ondergrenzen, dagblokken en SOL/dag voor K3 zonder rt_tx) wachten op `k3-rows.parquet`; het regime na Alpenglow; de landingskans live; de omvang van de wash-stroom in de schone week (E59 gesloten op dev).
- **Wat er mis kon gaan en gecontroleerd is (feit):** store vergrendeld vóór de eerste blik (clean.lock met store-sha, engine, trading_days 10); documenten-sha's en replay-bestanden door de job zelf gecontroleerd; toets-run = matrix-run per epoch; SHA256SUMS van de run door LAB review en LAB research nagerekend; de pariteit van de parts met result.json hierboven.

## 3. Bronnen en pins

| item | pad / sha |
|---|---|
| run E20 | NAS, 24 okt 13:46:37Z → 14:40:57Z op `/lab/code/repo-be47fc4`; uitvoer `research/clean-test/clean-be47fc4/` (job.log, test.log, store.log, result/{result.json 04965a1d…, result.md 3ee5549f…, exclusions.json b4433281…, a4c-matrix-run.json 5e6e9d73…}); SHA256SUMS 350fccdb…; store `clean-1055-1062.duckdb` e8db6e8d…; config toets-run efaccda6…, matrix-run eb855ea3… |
| parts | `backtests/clean-1055-1062-459216000-be47fc4/parts/` (183 bestanden, 263.321 posities); matrix-run als `result/a4c-parts/` (792.012 posities) |
| erratum pin | #167 issuecomment-6384965518 (13:45:42Z), tekst `research/erratum-e20-store-cast-2026-10-24-final.md` (8743cc70…); NAS-bewijs 98-clean-0-u64-proof-be47fc4 GREEN (`backtests/store-u64-proof-be47fc4/` SHA256SUMS 0afae009…) |
| uitlezing | `research/clean-test/e20-uitlezing-2026-10-24/`: `e20_k3_dag.py` 7dd83ec6…, `e20_k3_dag.out` f290601b…, `dev_k3_rt.py` b9c29ba1…, `dev_k3_rt.out` ef884967…, `dev-k3-rt.parquet` ebc8b98b…, `e20_fp_power.py` f1549de9…, `e20_fp_power.out` 83bab218…; SHA256SUMS 1a4d808f… (`sha256sum -c` OK) |
| §7-noot | `lab/reports/2026-10-24-wash-trigger-clean.md` (`wallets.clean_reports --e20`, label PINS["E20"] = be47fc4 sinds deze PR) |
| register | rij E20 → gesloten; datagebruik 1055–1062 → verbruikt |
