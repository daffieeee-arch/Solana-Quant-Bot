# Uitslag van de schone toetsen van zaterdag 24 oktober 2026 (LAB research) — deel 1 + 2

**Status: alle zes toetsen gelezen op 24 oktober (E20 14:40Z; E45 15:26Z, E49 15:27Z, E56 15:29Z; H3 16:51Z, H4 17:07Z, H5 17:23Z), alle op dezelfde vergrendelde store `clean-1055-1062.duckdb` (sha e8db6e8d…).** Alle regels waren vóór de eerste blik vastgelegd (E20: hypothese 35bdaa0d…, aanvulling v5 94d09d9f…; H3 110df1a2…; H4 2358103206…; H5 c59c6dfd…; E45 94df3921…; E49 a6e5f70e…; E56 08a99f5c…; uitsluitingen ccfaed98…; lees-addendum van LAB review 1d8d2211…); niets hierin is na het zien van de data aangepast. Labels als in het register: **feit** / **reconstructie** / **modelaanname** / **onbekend**. Namen van de eigenaar: "Terugveer-strategie" = F7 (kopen na een grote verkoop in een omgekeerde PumpSwap-pool), "Terugveer snel" = K3 (de paper-kandidaat), "Terugveer lang" = H4/H5, "willekeurige vergelijking" = N2.

Elk getal komt uit de `result.json`/`result.md` of `*.md`/`*.json` van de gepinde runs (§5) of uit een gearchiveerd script met uitvoer in `research/clean-test/e20-uitlezing-2026-10-24/` (SHA256SUMS ac8dd038…). De §7-noten per toets (gegenereerd met `wallets.clean_reports`, lezen alleen de result.json's en de parts ernaast): `2026-10-24-wash-trigger-clean.md` (E20), `-hold180-clean.md` (H3), `-flow300-clean.md` (H4), `-flow300-tp100-clean.md` (H5). Deel 1 (alleen E20) is gemerged als #556 (86df24f); deel 2 voegt H3/H4/H5, E45/E49/E56, de familietabel en de should-fixes van LAB review toe.

## 0. In één oogopslag

| toets | vraag in gewone woorden | regel vooraf (kort) | uitkomst | wat het betekent |
|---|---|---|---|---|
| **E20** — Terugveer snel (K3) met en zonder wash-trigger-filter | verdient K3 op dagen die niemand eerder zag, na alle kosten, als de triggers worden weggelaten waarbij de "grote verkoop" in dezelfde transactie alweer terugkoopt? | H1: K3 zonder rt_tx > 0 per trade (ondergrens > 0) **én** H2: zonder − met > 0 → "bevestigd"; alleen H2 → "risicofilter zonder bewezen winst"; H2 niet → "verworpen" | **risicofilter zonder bewezen winst** — H1 −0,11 % [−0,86; +0,32] niet gehaald, H2 +2,76 % [+0,70; +5,19] gehaald | het filter haalt de slechtste triggers weg (−2,88 % per trade), maar wat overblijft verdient niets; K3 als geheel verloor 1,5 SOL per dag bij 10 SOL |
| **H3** — Terugveer snel met vaste hold van 180 s (hold180) tegen K3 | is langer vasthouden zonder stop beter dan K3's take-profit? | gemiddeld dagverschil hold180 − tp4_T600 > 0 met ondergrens > 0 | **verworpen** — −4,43 SOL/dag [−8,15; −0,52] | langer vasthouden verliest méér: −5,9 tegen −1,5 SOL/dag |
| **H4** — Terugveer lang (flow300_H6, alleen organische triggers) | verdient de lange uitstap op niet-gefarmde pools? | Σ netto SOL per dag > 0 met ondergrens > 0 | **verworpen** — −6,01 SOL/dag [−9,10; −2,97] | verliest op 8 van de 10 dagen |
| **H5** — Terugveer lang met winstplafond ×2 (flow300_tp100_H6, organisch) | idem, met een take-profit op +100 % | idem | **verworpen** — −5,40 SOL/dag [−8,09; −2,71] | op dev was dit +5,6 SOL/dag bij 10 SOL (E63); op ongeziene dagen −5,4 |
| **E45** — mechanismetoets zonder uitstapregel | veert de prijs na zo'n verkoop binnen vijf minuten terug, méér dan bij de willekeurige vergelijking? | bevestigd als P1 (log-markout 5 m) én P2 (F7 − N2) ondergrens > 0; verworpen als een punt ≤ 0 | **verworpen** — P1 −23,80 lp [−35,23; −11,18], P2 −34,01 lp [−46,54; −20,70]; verhandelbaar: nee (netto 0,5 SOL −11,29 pt [−15,68; −7,01]) | het mechanisme bestaat niet op ongeziene data: de prijs daalt na de trigger, en de triggerpools doen het slechter dan willekeurige tweelingen |
| **E49** — dieptebande 100–150 SOL bij instap | houdt de op dev gevonden "beste band" stand? | bevestigd als de 95 %-ondergrens van het netto 5 m-markout > 0; verworpen als het getrimde gemiddelde ≤ 0 | **verworpen** — −1,38 pt [−7,44; +4,53] (485 pools) | de band verliest minder dan de rest (−10,27), maar verdient niets; dev-referentie was +8,28 |
| **E56** — betaalde kopers vóór de trigger (C3) | zijn triggers met ≥ 2 betalende kopers in de minuut ervoor beter? | idem | **verworpen** — −4,69 pt [−13,31; +4,16] (313 pools) | zelfde beeld: minder slecht dan de rest (−12,06), niet winstgevend; dev-referentie +9,19 |

**Eén zin voor de eigenaar (10 SOL = de gepinde 0,5 SOL-maat zelf, QUEUE 735; reconstructie met het kostenmodel):** Alle zes toetsen op tien ongeziene dagen zijn verworpen. Terugveer snel (K3) verloor 1,5 SOL per dag; Terugveer snel met lange hold 5,9; Terugveer lang 6,0 en met winstplafond 5,4 SOL per dag — op dev was dat laatste nog +5,6. De prijs veert na zo'n grote verkoop niet terug: vijf minuten later staat een 0,5 SOL-rondrit netto 11 % lager, ook ten opzichte van willekeurige vergelijkingen; de dieptebande en de triggers met betalende kopers verliezen ook. Geen van de zes krijgt een vervolg; de paper-engine draait K3 ongewijzigd en de stopregel beslist daar. Het wash-filter van E20 haalt de slechtste triggers weg (H2 gehaald), maar wat overblijft verdient niets (H1 niet gehaald): een risicofilter zonder bewezen winst, precies de vooraf vastgelegde categorie.

## 1. E20 — Terugveer snel op schone data

### 1.1 Wat vastlag en wat is gelezen (feit)

- **Regel vooraf:** hypothese `hypothesis-wash-trigger-2026-10-15.md` (sha 35bdaa0d…, #167, 15 okt, vóór E18), aanvulling v5 `prereg-wash-trigger-addendum-2026-10-15.md` (94d09d9f…), uitsluitingen `case-exclusions-clean-data.md` (ccfaed98…). Eén toets, één blik: configuratie K3 (F7 pool_reversed, tp4_T600, farm alle, 0,5 SOL, q 0,5, overlay geen, basiskosten), d_ms 600 via de Clock van de engine.
- **Engine-pin:** `be47fc425fc46942884db256e5c9d809a11d57a9` (tag `clean-e20-pin-2026-10-24`, tak vanaf 46a0236), gezet per erratum op #167 (issuecomment-6384965518, 13:45:42Z) vóór de herstart: de bouw op 46a0236 brak om 12:00:28Z op een u64-reserve > INT64 in epoch 1061 (store-cast; geen uitkomst gelezen). De replay-bestanden zijn byte-gelijk aan f2c9666 (de job controleert dat zelf: `replay code equal to f2c9666` voor alle tien bestanden in job.log). Gevolg van de fix in de store-meta (feit): `excluded_pools_int64` = 2 pools (reversed 0, not_reversed 2; rijen buiten bereik buy 1, sell 2) — geen omgekeerde pool, dus het K3-universum is ongewijzigd.
- **Data:** store `clean-1055-1062.duckdb` (sha e8db6e8d…), venster slots [455.760.000, 459.216.000) = epochs 1055–1062, 18 chunks, extractor f5dd11df…, warm-up 1 epoch (1054, geen beslissingen). Epoch 1063 is door OF1 niet vóór de 12:00Z-klok gepubliceerd; per aanvulling §4 geldt dan de klok en draait de toets op wat compleet is. d per epoch = 3 slots in alle acht epochs (216–219 ms per slot). UTC-handelsdagen: **10** (11–20 oktober 2026), minimum 5 gehaald; **twee daarvan zijn deeldagen**: 11 oktober begint bij het venster (≈ 20:10Z, eerste trigger slot 455.760.671, ≈ 4 uur) en 20 oktober eindigt bij het venster (≈ 12:43Z, laatste trigger 459.190.916, ≈ 12,7 uur). T = 10 is per regel correct; de dagtabellen markeren beide. Toets-run en matrix-run zijn per epoch rij voor rij gelijk (644/709/748/721/794/608/290/335).
- **Uitsluitingen:** 219 mints en 16 pools uit de drie regels van ccfaed98…; 0 triggers en 0 van de 4.849 K3-posities vervielen (de uitgesloten gevallen kwamen in dit venster niet als K3-positie voor).
- **Gevulde K3-trades:** 4.424 (met rt_tx 867, zonder 3.557, zonder trigger-leg 0); mislukte instappen 425 (netto −0,2146 SOL; alleen in de dag-P&L van de toets-run en de DSR, niet in H1/H2 per gevulde trade, zoals vastgelegd).

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
| **schoon 1055–1062 (E20)** | 3 (≈ 650 ms) | **−0,11 % [−0,86; +0,32]** | **−2,88 % [−5,18; −1,21]** | deze toets |

Lezing (reconstructie): de richting van het filter is op alle drie de periodes dezelfde (wash-triggers zijn slechter), maar de voorsprong van de "echte verkopen" is van dev (+1,0 … +1,9 %) via de hold-out (+0,1 … +1,1 %) naar nul gegaan op de schone data; E18 waarschuwde daar al voor ("bij d = 2 ook zonder wash-triggers geen voorsprong meer"). De wash-groep is op de schone data juist slechter geworden (−2,9 % tegen −0,4 … −0,8 %). Het regime verschilt ook: 217 ms per slot en d = 3 tegen 267–316 ms en d = 1–2 op dev.

### 1.4 Beschrijvende kolommen (lees-addendum §2; verandert het oordeel niet)

Alle getallen hieronder uit `research/clean-test/e20-uitlezing-2026-10-24/e20_k3_dag.py` (sha 7dd83ec6…) en `e20_k3_dag.out` (f290601b…) op de gepinde parts van de toets-run (`backtests/clean-1055-1062-459216000-be47fc4/parts/`, config efaccda6… = `clean.lock`), K3 alle triggers = de 43e kolom; pariteit met result.json: 4.849 posities, 425 mislukt, 4.424 gevuld, 10 dagen, −0,2146 SOL, gewogen gemiddelde −0,656 % per gevulde trade — alles gelijk (feit).

**Strengere ondergrenzen (§2.2) van K3 alle triggers, per gevulde trade:** gemiddelde −0,656 %; dagbootstrap-ondergrens 95 % −1,29 %, 99 % −1,54 %, 99,9 % −1,93 %; t-interval op de daggemiddelden (t₉) −1,80 %. Voor H1 (K3 zonder rt_tx) en H2 vragen deze kolommen de rt_tx-vlag per rij; die komt uit `k3-rows.parquet` (Lab Backtest, #557, NAS-job 98-clean-5; lezing van de vergrendelde uitvoer, geen herrun) — **nog onbekend**, volgt als eigen commit met apart gepinde `k3-rows/SHA256SUMS`.

**Dag-P&L van de toets-run (config efaccda6…; = de matrix bij d = 3 rij voor rij) in SOL per handelsdag, 0,5 SOL per trade, elke trigger, mislukte instappen inbegrepen:**

| dag (UTC) | gevuld | mislukt | netto SOL |
|---|---|---|---|
| 11 okt (deeldag ≈ 4 u) | 164 | 23 | −1,63 |
| 12 okt | 426 | 59 | −1,69 |
| 13 okt | 603 | 86 | +0,03 |
| 14 okt | 615 | 69 | **−3,95** (slechtste) |
| 15 okt | 587 | 70 | −1,12 |
| 16 okt | 766 | 24 | −0,07 |
| 17 okt | 529 | 40 | −0,87 |
| 18 okt | 327 | 22 | −3,47 |
| 19 okt | 237 | 29 | −3,68 |
| 20 okt (deeldag ≈ 12,7 u) | 170 | 3 | +1,66 (beste; halve dag) |

Gemiddeld **−1,48 SOL/dag**, totaal −14,78 SOL over 10 dagen; ondergrens 95 % −2,54, 99 % −2,87, 99,9 % −3,24 SOL/dag, t-interval −2,78; 2 van de 10 dagen positief; eerste helft (11–15 okt) −1,67, tweede helft (16–20 okt) −1,29 SOL/dag (§2.3). Exits: take-profit 2.939 trades (+4,9 % gemiddeld), stop-loss 1.378 (−12,8 %), tijd 107 (+1,7 %). Per farm-label: flagged 3.437 trades +0,15 % (+2,5 SOL; als dagreeks +0,24 SOL/dag [−0,57; +0,93]), organic 987 trades −3,45 % (−17,1 SOL) — beschrijvend, geen toets; het label "organic" is een gereconstrueerde farmer-vlag.

**10 SOL-lezing = de 0,5 SOL-maat zelf (QUEUE 735):** −1,48 SOL/dag bij 10 SOL. Ter controle: in de toets-run stonden maximaal 3 posities tegelijk open, dus een plafond van 5 plekken (10 SOL / 0,5 SOL per trade, 5 % van het kapitaal per positie) zou niets veranderen; het E63-plafond wordt per lees-addendum §2.4 niet op clean toegepast. Bij 25 en 100 SOL (grotere posities) wordt het verlies in absolute zin alleen groter; de richting verandert niet (modelaanname: de vulling met eigen prijsimpact komt uit de replay bij 0,5 SOL; grotere posities zijn niet gesimuleerd). De SOL/dag mét rt_tx-filter volgt uit k3-rows (met de mislukte instappen van die groep erbij).

**Fout en trefkans van de vastgelegde regel bij T = 10 (§2.1; modelaanname: de dev-dagvorm is de ware verdeling):** dev-vorm = de dev-K3-rijen (f7rev-1033-1046, d = 2 als dichtstbijzijnde dev-vertraging bij 600 ms, 20 UTC-dagen) met de rt_tx-vlag uit `backtest.wash.flags` op dev2 (`dev_k3_rt.py` → `dev-k3-rt.parquet`; vlagdekking 100 %, rt_tx-aandeel 23,5 %, E16 zei 23,8 %; dev-gemiddelden d = 2: zonder rt_tx +0,86 %, met −0,91 %, verschil +1,76 % — reconstructie, dev verbruikt). De dev-populatie wordt per s eenmaal verschoven naar s × het dev-effect, daaruit 1.000 vensters van T = 10 dagen met teruglegging (seed 7), per venster de regel precies als vastgelegd (gepoold gemiddelde, dagbootstrap 10.000, seed 7, 2,5e percentiel > 0) en ernaast het t-interval op de ongewogen daggemiddelden (`e20_fp_power.py` / `e20_fp_power.out`):

| regel | s = 0: eenzijdige fout (nominaal 2,5 %) | s = 0,5: trefkans bij de helft van dev | s = 1: trefkans bij het dev-effect |
|---|---|---|---|
| H1 (K3 zonder rt_tx > 0) | bootstrap **1,6 %**; t-interval 0,1 % | 18,1 %; t 2,0 % | 66,3 %; t 16,9 % |
| H2 (zonder − met > 0) | bootstrap **2,2 %**; t 0,5 % | 50,5 %; t 10,7 % | 99,7 %; t 62,2 % |

Lezing: de dagbootstrap van E20 is bij T = 10 niet te soepel (fout ≈ 2 %; anders dan bij de dagsom-regels van H3/H4/H5 hieronder, want de trade-gewogen poolmaat varieert minder per dag); H1 had bij een half dev-effect (+0,43 % per trade) maar ≈ 18 % kans om te slagen, bij het volle dev-effect ≈ 66 %. Het niet-halen van H1 is dus op zichzelf geen bewijs van afwezigheid van een half effect — maar de puntschatting ligt met −0,11 % ver onder beide, en het 95 %-interval [−0,86; +0,32] sluit het volle dev-effect (+0,86 %) uit. H2 had ruime trefkans en is gehaald. De t-interval-kolom is geen nominale fout van een 2,5 %-toets: het t-interval meet het **ongewogen** daggemiddelde, terwijl de verschuiving het **gepoolde** gemiddelde centreert (bij s = 0 ligt het ongewogen daggemiddelde op ≈ −0,61 %); alleen de bootstrapkolom is de fout van de vastgelegde regel.

### 1.5 Wat dit betekent (en wat niet)

- **Voor Terugveer snel (K3, paper):** niets verandert aan de paper-engine of de stopregel (die beslist op paper-data, ≥ 200 gevulde trades en ≥ 5 dagen). Wat E20 zegt: op tien ongeziene pre-Alpenglow-dagen verloor K3 geld bij elke inzet, en het beste bekende filter maakt er nul van, geen winst. Een forward-toets van het filter volgt niet (H1-punt < 0).
- **Voor het filter als risicomaatregel:** H2 is gehaald: triggers waarvan de verkoop in dezelfde transactie terugkoopt, zijn op drie periodes slechter dan echte verkopen. Als K3 om een andere reden ooit draait, is het weglaten van rt_tx-triggers verdedigbaar als risicofilter; het bewijst geen edge. Elke toepassing in paper vraagt spec, build en eigenaarsbesluit.
- **Regime:** de schone data zijn pre-Alpenglow (217 ms per slot, d = 3); paper draait ná de omschakeling (E67: 212 ms, aanbod 0,29× dev). Dit oordeel geldt voor het oude regime.

## 2. H3, H4, H5 — Terugveer snel met lange hold en Terugveer lang

### 2.1 Wat vastlag en wat is gelezen (feit)

- **Pins:** H3 `dbef1b8` (hypothese 110df1a2…; replay byte-gelijk aan befacff), H4 `165bb04` (2358103206…), H5 `15fa3dd` (c59c6dfd…); alle drie lezen E20's vergrendelde store (sha gelijk aan `clean.lock`) en weigeren een andere; in alle drie runs is de K3-controle gehaald: tp4_T600 (farm alle) per epoch rij voor rij gelijk aan E20's toets-run (644/709/748/721/794/608/290/335). Zelfde venster, d = 3 per epoch, uitsluitingen 219 mints / 16 pools, T = 10 UTC-dagen (dagdefinitie: per UTC-dag van de instap-slot, E20 per triggerdag — rond middernacht niet exact gelijk; de twee deeldagen van §1.1 gelden ook hier: dag 1 (11 okt, ≈ 4 u) is voor H4/H5 de enige duidelijk positieve dag).
- **Regels:** H3 = gemiddeld dagverschil Σ netto SOL hold180 − tp4_T600 (farm alle, mislukte instappen en drains inbegrepen) > 0 met ondergrens van de dagbootstrap (10.000, seed 7) > 0; H4 = gemiddeld Σ netto SOL per dag van flow300_H6 op organische triggers > 0 met ondergrens > 0; H5 = idem voor flow300_tp100_H6 (take-profit +100 %, anders flow300's outflow-stop, plafond 6 u). Minimum 5 dagen met een positie: 10 van 10 gehaald.

### 2.2 Uitkomsten (de vastgelegde regels; reconstructie met het kostenmodel)

| toets | maat | waarde | 95 %-interval | beslissing | DSR (dev-var / 1/T), PSR | PBO |
|---|---|---|---|---|---|---|
| H3 | Δ hold180 − tp4_T600, SOL/dag, farm alle | **−4,43** | −8,15 … −0,52 | **verworpen** | 0,000 / 0,000; 0,018 | 0,583 (27 kolommen) |
| H4 | flow300_H6 organic, SOL/dag | **−6,01** | −9,10 … −2,97 | **verworpen** | 0,000 / 0,000; 0,002 | 0,841 (33) |
| H5 | flow300_tp100_H6 organic, SOL/dag | **−5,40** | −8,09 … −2,71 | **verworpen** | 0,000 / 0,000; 0,003 | 0,833 (36) |

Alle drie puntschattingen < 0: geen enkele cel is "onbeslist", dus uit geen van de drie volgt een forward-pre-registratie (eigenaarsbeleid 23 okt); de forward-vastlegging E32 (2240645b… + busy-addendum) krijgt voor H4/H5 alleen een beschrijvende rol, zoals E32 §3 al zegt. hold180 zelf: −5,91 SOL/dag (tp4_T600 −1,48; §1.4). H4 587 posities (−60,08 SOL), slechtste dag −14,30, grootste positie +2,03; H5 598 posities (−54,00 SOL), slechtste dag −13,02, grootste positie +1,36; 57 take-profit-exits van H5 vulden op ×2,02 (mediaan; p5/p95 1,96/2,11). H4 en H5 delen 571 van hun triggers; hun verschil zit in de take-profit (flow300_tp100_H6 −53,47 tegen flow300_H6 −62,03 SOL op de gemeenschappelijke triggers). De voorbehoudregel (DSR < 0,95 of PBO > 0,5) vuurt bij alle drie; ze verandert de beslissing niet.

Beschrijvend ernaast (uit de result.md's; alles SOL/dag, 0,5 SOL per positie): flow300_H6 flagged −9,39, alle −15,40; flow300_tp100_H6 flagged −9,71, alle −15,11; hold180 organic −2,57, flagged −3,34; tp50_H6 organic −13,60, tp100_H6 organic −19,93 (de "big-TP"-varianten van E30 verliezen het meest). K3 (tp4_T600) alle −1,48, organic −1,72, flagged +0,24 [−0,57; +0,93] — één keer geciteerd in §1.4; het is dezelfde reeks.

### 2.3 Beschrijvende kolommen (lees-addendum §2; verandert het oordeel niet)

Uit `hx_dagreeks.py` (sha b07e1d8f…) / `hx_dagreeks.out` (0301d16d…) op de dagreeksen in de drie result.json's (de 95 %-ondergrens is daarbij op zes decimalen gelijk aan die van de runs):

| reeks | gemiddelde SOL/dag | ondergrens 95 % / 99 % / 99,9 % | t-interval (t₉) | eerste helft 11–15 okt / tweede helft 16–20 okt | slechtste / beste dag | dagen > 0 |
|---|---|---|---|---|---|---|
| H3 Δ hold180 − tp4_T600 | −4,43 | −8,15 / −9,31 / −10,32 | −9,10 | −2,95 / −5,91 | 14 okt −12,59 / 13 okt +6,43 | 2 van 10 |
| hold180 (alle) | −5,91 | −10,09 / −11,55 / −12,64 | −11,12 | −4,63 / −7,19 | 14 okt −16,54 / 13 okt +6,46 | 2 van 10 |
| H4 flow300_H6 organic | −6,01 | −9,10 / −10,00 / −11,05 | −9,78 | −7,65 / −4,37 | 13 okt −14,30 / 11 okt (deeldag) +2,03 | 2 van 10 |
| H5 flow300_tp100_H6 organic | −5,40 | −8,09 / −8,92 / −9,87 | −8,72 | −6,77 / −4,03 | 13 okt −13,02 / 11 okt (deeldag) +2,18 | 1 van 10 |

Dagreeksen H4 / H5 (SOL): 11 okt +2,03 / +2,18 (deeldag ≈ 4 u); 12 okt −8,09 / −6,55; 13 okt −14,30 / −13,02; 14 okt −7,46 / −7,27; 15 okt −10,40 / −9,20; 16 okt −0,62 / −1,10; 17 okt −10,59 / −8,68; 18 okt −4,82 / −4,51; 19 okt −6,10 / −5,77; 20 okt +0,28 / −0,08 (deeldag ≈ 12,7 u).

**Fout en trefkans bij T = 10 (§2.1; modelaanname: dev-dagvorm):** dev-vorm = de dagsommen (mislukte instappen inbegrepen) van dezelfde variant en farm op dev d = 2 uit de dev-parts op de VPS (`bigtp-dev-1033-1046-4dbd34b` voor H5, `long-dev-1033-1046-fc4173c` voor H4, `exits-dev-1033-1046-befacff` voor hold180 en tp4_T600; 20 UTC-dagen), zelfde methode als §1.4 (`hx_fp_power.py` da8e081e… / `hx_fp_power.out` 8c2ce6b9…):

| regel | dev-gemiddelde (d = 2) | s = 0: eenzijdige fout (nominaal 2,5 %) | s = 0,5: trefkans | s = 1: trefkans |
|---|---|---|---|---|
| H3 (Δ > 0) | +8,03 SOL/dag, sd 9,66 | bootstrap **5,2 %**; t 3,4 % | 34,8 %; t 24,0 % | 76,1 %; t 64,6 % |
| H4 (Σ > 0) | +7,05, sd 9,26 | **3,7 %**; t 1,5 % | 27,4 %; t 16,1 % | 77,7 %; t 58,1 % |
| H5 (Σ > 0) | +8,37, sd 8,25 | **4,9 %**; t 3,4 % | 38,5 %; t 29,2 % | 91,5 %; t 80,9 % |

Lezing: de dagsom-regels zijn bij T = 10 iets te soepel (fout 4–5 % in plaats van 2,5 %; LAB review vond voor H5 bij T = 9 ≈ 5–7 %, hier bij T = 10 4,9 %) en hadden bij een half dev-effect ≈ 27–39 % trefkans. Geen van de drie kwam in de buurt: alle drie puntschattingen zijn negatief en hun 95 %-intervallen sluiten zelfs nul uit aan de onderkant. Het niet-halen is hier dus geen "te weinig dagen".

**Onverkochte posities, de "nooit vrij"-lezing (§2.4; uit de parts naast de result.json's via `wallets.clean_reports`, in de §7-noten):** `unsold` = posities zonder verkoop (de pool was leeg voordat de uitstap kon vullen; verlies ≈ de volle inzet), `unsold_busy` = vervolgposities in dezelfde pool die de toernooiregel `busy_until = exit_slot or t` toeliet maar die live niet kunnen (de engine houdt zo'n pool bezet).

| toets (organic) | posities | Σ netto SOL | unsold | Σ unsold SOL | unsold_busy | Σ unsold_busy SOL | Σ zonder unsold_busy (≈ de live busy-regel) |
|---|---|---|---|---|---|---|---|
| H5 flow300_tp100_H6 | 543 gevuld (598 incl. mislukt) | −53,97 | 158 | −79,24 | 109 | −54,67 | **+0,70** |
| H4 flow300_H6 | 533 | −60,05 | 160 | −80,24 | 110 | −55,17 | −4,89 |
| hold180 organic | 580 | −25,68 | 94 | −47,16 | 48 | −24,08 | −1,60 |

Lezing (beschrijvend; de regels telden deze posities mee, zoals in de pins vastgelegd, dus het oordeel verandert niet): bij Terugveer lang verdwijnt 158–160 van de ≈ 540 organische posities volledig (de pool wordt leeggetrokken vóór de uitstap: `outflow`-exit zonder opbrengst), en 109–110 posities zijn herhalingen in precies zulke pools. Zonder die herhalingen (de realistische busy-regel van het E32-addendum) komt H5 op ≈ +0,07 SOL/dag uit en H4 op ≈ −0,49 SOL/dag: geen van beide is dan winstgevend, maar het verlies van −5 à −6 SOL/dag is grotendeels een artefact van herhaald instappen in leeggetrokken pools. Dit is precies waarvoor het busy-addendum (E32) de forward-regel heeft aangepast; het is geen herformulering van de clean-toets en geen bewijs van een edge (+0,70 SOL over tien dagen is nul). De dev-tegenhanger (hoeveel unsold/unsold_busy op dev in de +8 SOL/dag zat) is **onbekend** zonder nieuwe dev-lezing. `sum(mayhem_reverted)` over de H4/H5-parts = 0 (moet 0 zijn); voor H3 ontbreekt de kolom in de parts (onbekend).

**Makersklasse (§2.5):** niet uitgevoerd op de H4/H5-parts (het telt als een blik per cel en vraagt de store-opzoektabel); de depthband- en paidflow-runs geven de klasse wel beschrijvend per pool (§3.3).

## 3. E45, E49, E56 — mechanisme, dieptebande, betaalde kopers

### 3.1 Wat vastlag en wat is gelezen (feit)

- **Pins:** E45 `6b12ec7` (v0.2 94df3921…), E49 `45a1106` (v0.2 a6e5f70e…), E56 `9ad8032` (v1.0 08a99f5c…); markout-runs op E20's vergrendelde store (`--clean-lock`), d = 600 ms via de Clock (3 slots in 1055–1062), horizon 5 m, eenheid = pool (gemiddelde over de triggers van de pool, 0,5 % getrimd gemiddelde over pools, poolbootstrap 10.000 seed 7), minimum 100 pools en 5 dagen: gehaald (E45 753 pools, E49 485, E56 313; alle 10 dagen). Basis: 5.808 F7 pool_reversed-triggers in 753 pools (≈ 581 per dag; dev ≈ 1.900 per dag — de aanvoer is kleiner). Uitvoer `wallets/mechanism-6b12ec7/` (mechanism.md 389b897f…, .json 203526e4…), `depthband-45a1106/` (3d4575db…, dd58131e…), `paidflow-9ad8032/` (99e865a6…, 03669792…); de NAS-jobs schreven geen SHA256SUMS, de coördinator heeft `SHA256SUMS.coordinator` toegevoegd (af95bac0… / 701e6854… / 52d00f75…, 8 bestanden elk, `sha256sum -c` OK op de VPS; de md/json-sha's gelijk aan de joblogs in QUEUE 799).

### 3.2 Uitkomsten (de vastgelegde regels; log-markout in lp = log × 100, netto in procentpunten van een 0,5 SOL-rondrit tegen de echte reserves plus basiskosten)

| toets | cel | pools | waarde | 95 %-poolinterval | 99,9 %-ondergrens | beslissing |
|---|---|---|---|---|---|---|
| E45 P1 | log-markout F7 @ 5 m | 753 | **−23,80 lp** | −35,23 … −11,18 | −42,76 | **verworpen** (punt ≤ 0) |
| E45 P2 | F7 − N2 @ 5 m | 753 | **−34,01 lp** | −46,54 … −20,70 | −53,67 | idem |
| E45 secundair | verhandelbaar: netto F7 @ 5 m | 753 | −11,29 pt | −15,68 … −7,01 | −18,10 | nee |
| E49 P1 | netto in band 100–150 SOL @ 5 m | 485 | **−1,38 pt** | −7,44 … +4,53 | −10,86 | **verworpen** (getrimd gemiddelde ≤ 0) |
| E56 P1 | netto in arm ≥ 2 betalende kopers @ 5 m | 313 | **−4,69 pt** | −13,31 … +4,16 | −17,57 | **verworpen** (getrimd gemiddelde ≤ 0) |

Consistentie (vastgelegd, verandert niets): E45 zelfde teken op 1 m (P1 −10,85, P2 −14,46) en 15 m (−27,15, −52,68); E49 band op 1 m −4,95 [−7,71; −2,16]; E56 arm op 1 m −4,50 [−8,75; −0,30]. Complementen: E49 buiten de band −10,27 pt [−14,89; −5,51]; E56 buiten de arm −12,06 [−16,88; −7,35]. Dev-referenties (verbruikt): E49 band +8,28 [+4,64; +11,83]; E56 arm +9,19. De geplande vervolgstap bij "bevestigd" (forward-pre-registratie ≥ 1064) vervalt voor alle drie; geen van de drie is onbeslist (puntschattingen ≤ 0).

Eén zin over N2 (reconstructie): de willekeurige tweelingen van E45 veren zelf op (N2 log +10,22 lp [+5,45; +15,09] op 5 m; netto na kosten wel −25,93 pt), terwijl de F7-triggerpools dalen (−23,80 lp): op de schone data presteren de triggerpools **slechter** dan willekeurige pools van dezelfde venue, oriëntatie en diepte in hetzelfde uur.

### 3.3 Beschrijvend (uit de md's; klassen met 5–6 cellen per tabel, geen bevindingen)

- **E45 per farm:** flagged lg −1,50 [−13,49; +10,91] (574 pools), organic −47,02 [−62,47; −28,72] (353 pools); netto −6,60 / −10,29 pt. Per trigger ongewogen: lg −6,37 (68 % > 0), netto −2,13 (60 % > 0) — de actieve pools dragen het per-trigger-cijfer, de poolmaat niet. Slechtste dag 18 okt (log −21,69). Wiped-aandeel (pool leeg binnen 5 m): 18–25 % van de pools in alle drie de runs.
- **E49 per band (5 m, netto pt):** 0–100 SOL +10,78 (12 pools), **100–150 −1,38 (485)**, 150–200 −7,03 [−12,07; −2,06] (560), 200–300 −5,16 (163), 300–500 −42,24 (13), ≥ 500 −61,80 (6). Binnen de band: farm flagged −2,84 (280 pools), organic +1,42 [−7,61; +11,02] (231); makersklasse 1–9 eerdere pools −10,38 (79), 10–99 +1,94 (150), ≥ 100 +32,90 [−14,34; +86,28] (18), systeemprogramma −5,12 (114), eerste in het venster +3,68 (124).
- **E56 per aantal betalende kopers (5 m, netto pt):** 0 −11,79 (445 pools), 1 −7,84 (273), **2–3 −5,70 (214), 4–8 −0,60 (192), ≥ 9 +15,53 [−5,78; +36,86] (49)**; binnen de arm: flagged +3,67 (202), organic −9,42 (162); makersklasse 10–99 +22,14 [+0,67; +42,76] (94 pools), systeemprogramma −34,86 [−64,79; −6,94] (13), eerste in het venster −15,95 (97); dagblokken: eerste helft arm −5,16 (164 pools), tweede helft +0,27 (149); arm × band 100–150 SOL +8,19 [−5,81; +20,21] (164 pools) tegen complement −6,17.
- **Lezing van de subcellen:** "≥ 9 betalende kopers" (+15,5), "maker 10–99 pools" (+22,1 met een interval boven nul) en "maker ≥ 100" (+32,9) zijn beschrijvende klassen uit tabellen van vijf tot zes cellen, met 18–94 pools; ze zijn geen bevinding en gaan niet zonder een nieuwe pre-registratie in een hypothese. Op dev droeg de arm het bandeffect (arm +23,8 tegen rest −0,5 in 100–150 SOL); op clean is arm × band +8,2 [−5,8; +20,2] — zelfde richting, niet onderscheidbaar van nul.
- **Fout en trefkans bij T = 10 voor E45/E49/E56 (§2.1):** niet berekend — de dev-vorm (pool-eenheid, markout-parquet `markout-v1-txdelay-v4`) staat alleen op de NAS; de controle van 23 okt vond ≈ 0,44–0,46 trefkans bij de helft van dev voor de E56-regel (reconstructie van de controle, niet gereproduceerd). Op verzoek als dev-only NAS-job.

## 4. Familie van acht, eerlijkheid en de kloof dev → clean

### 4.1 Familietabel (lees-addendum §2.2; beschrijvend)

| cel | regel (95 %) | oordeel | ondergrens 95 % / 99 % / 99,9 % | eenzijdige fout bij T = 10 (s = 0) |
|---|---|---|---|---|
| E20 H1 | K3 zonder rt_tx > 0 per trade | niet gehaald | −0,86 % / (volgt uit k3-rows) | 1,6 % |
| E20 H2 | zonder − met > 0 | **gehaald** | +0,70 % / (volgt uit k3-rows) | 2,2 % |
| H3 | Δ hold180 − tp4_T600 > 0 | verworpen | −8,15 / −9,31 / −10,32 SOL/dag | 5,2 % |
| H4 | flow300_H6 organic > 0 | verworpen | −9,10 / −10,00 / −11,05 | 3,7 % |
| H5 | flow300_tp100_H6 organic > 0 | verworpen | −8,09 / −8,92 / −9,87 | 4,9 % |
| E45 | P1 én P2 > 0 | verworpen | P1 −35,23 / – / −42,76 lp | niet berekend |
| E49 | band netto > 0 | verworpen | −7,44 / – / −10,86 pt | niet berekend |
| E56 | arm netto > 0 | verworpen | −13,31 / – / −17,57 pt | niet berekend |

Eén van de acht regels is gehaald (E20 H2). Verwacht aantal valse bevestigingen onder de globale nul: Σ fout over de vijf berekende cellen = 0,18; voor de drie poolbootstrap-cellen is de fout niet berekend (werkcijfer van het lees-addendum 5–7 % per cel → ≈ 0,15–0,2 extra; hypothese), dus ≈ 0,3–0,4 van de 8 en ≈ 30 % kans op minstens één valse bevestiging. De cellen zijn afhankelijk (zelfde tien dagen en dezelfde triggers; H4 en H5 delen 571 triggers; E49 en E56 overlappen in de band 100–150 SOL), dus k bevestigingen zijn nooit k onafhankelijke bevindingen — en de ene bevestiging (H2) is een verschil tussen twee verliezende groepen, geen edge. Geen Holm of Bonferroni in een oordeel (dat zou een regelwijziging zijn); de 99,9 %-kolom is de familiebestendige lezing: zelfs daar is geen cel positief.

### 4.2 Eerlijkheid

- **Data:** 1055–1062 is hiermee verbruikt (register: datagebruik → verbruikt, bevestigende toetsen). Epoch 1063 bestaat niet in OF1 vóór de klok; de hold-out 1047–1051 is niet aangeraakt. Uit clean-v1 is vóór de eerste run alleen de bouwlog van 12:00:28Z gezien (foutklasse en chunk; erratum).
- **Blikken:** zes toetsen = zes blikken (E20's H1 én H2 vormen één regel); de beschrijvende kolommen van §1.4, §2.3 en §3.3 beslissen niets en tellen niet (lees-addendum §2.6); de makersklasse-split van §2.5 is niet gedaan. Projectbrede teller na E74 ≈ 2.305 + 6.
- **Wat we niet weten:** de gefilterde E20-kolommen (k3-rows); de fout/trefkans van de drie poolbootstrap-cellen; de dev-tegenhanger van unsold/unsold_busy; het regime na Alpenglow; de landingskans live; de omvang van de wash-stroom in de schone week (E59 gesloten op dev).
- **Wat er mis kon gaan en gecontroleerd is (feit):** store vergrendeld vóór de eerste blik (clean.lock met store-sha, engine, trading_days 10); documenten-sha's en replay-bestanden door elke job zelf gecontroleerd; de K3-controle (tp4_T600 = E20's toets-run per epoch) in H3, H4 en H5 gehaald; SHA256SUMS van alle runs door LAB review en LAB research nagerekend; de pariteit van de E20-parts met result.json (§1.4); de drie markout-runs met dezelfde 5.808 triggers / 753 pools / Clock-d 3.

### 4.3 De kloof dev → clean (vraag van de coördinator; alles hypothese, de oordelen staan)

H5 ging van +5,6 SOL/dag bij 10 SOL op dev (E63) naar −5,4 op clean; H3/H4 en de drie markout-toetsen keerden allemaal van positief op dev naar negatief. Kandidaten, met wat ze kunnen dragen:

1. **Selectie en regime (primaire hypothese).** De dev-cellen zijn gekozen uit honderden trials (DSR op clean 0,000 bij N 150–258, PBO 0,58–0,84), en het regime verschilt: 217 ms-slots en d = 3, ≈ 581 F7-triggers per dag tegen ≈ 1.900 op dev, 18–25 % van de triggerpools binnen vijf minuten leeg, en de N2-tweelingen zelf positief in log terwijl de triggerpools dalen (§3.2). Dit is de lezing die bij alle zes uitkomsten past.
2. **Herhaald instappen in leeggetrokken pools (mechanisch, §2.3):** 109–110 van de ≈ 540 organische H4/H5-posities zijn herhalingen in pools waar een eerdere positie al alles verloor; zonder die groep is H5 ≈ 0 en H4 ≈ −0,5 SOL/dag. Dat verklaart het grootste deel van de −5 à −6, niet het verschil met +8 op dev (hoeveel van dev's +8 zulke herhalingen bevatte, is onbekend tot een dev-lezing).
3. **Vertraging:** clean d = 3 bij 217 ms (≈ 650 ms) is in tijd sneller dan dev d = 3 bij 267 ms (≈ 800 ms) en gelijk aan dev d = 2 (≈ 535 ms); E65 zegt dat d 1 → d 3 H5 ≈ 1,0 SOL/dag kost bij 10 SOL. Kan de kloof niet dragen.
4. **Pipeline (extractor f5dd11df… bij threads 1 tegen dev2's eac48884…; store-commit be47fc4 met warm-up 1054 tegen dev2's ba322be):** raakt alle markten tegelijk. Diagnostiek zonder nieuwe clean-lezing (dev-only NAS-jobs, door de coördinator bij Lab Backtest aangevraagd, achter de wachtrij van vanavond): (i) één dev-chunk opnieuw extraheren met f5dd11df bij threads 1 en event-voor-event vergelijken met de dev2-chunk; (ii) epoch 1043 bouwen met be47fc4's store.py inclusief het warm-up-pad en de tabellen vergelijken met dev2 (het u64-bewijs vergeleek 46a0236 met be47fc4, niet ba322be met be47fc4). Beide sluiten de pipeline formeel in of uit; geen oordeel verandert erdoor.
5. **Engine:** de drie H-pins zijn replay byte-gelijk aan hun dev-engines (controles in de pin-comments) en de K3-controle is in elke run gehaald; een engine-oorzaak is onwaarschijnlijk.
6. **Warm-up 1054:** raakt alleen busy_until- en farmer-toestand in de eerste ≈ 0,8 dag; H4/H5 zijn op 8–9 van de 10 dagen negatief. Verklaart niets.

Elke verdere lezing van de clean-store (bijvoorbeeld de makersklasse-split of een unsold-telling op dev) is een blik en wordt vooraf ingeschreven; er volgt geen hertune en geen nieuwe variant uit deze uitslag.

## 5. Bronnen en pins

| item | pad / sha |
|---|---|
| E20 run | NAS 24 okt 13:46:37Z → 14:40:57Z op `/lab/code/repo-be47fc4`; `research/clean-test/clean-be47fc4/` (job.log, test.log, store.log, result/{result.json 04965a1d…, result.md 3ee5549f…, exclusions.json b4433281…, a4c-matrix-run.json 5e6e9d73…}); SHA256SUMS 350fccdb…; store `clean-1055-1062.duckdb` e8db6e8d…; config toets-run efaccda6…, matrix-run eb855ea3…; parts `backtests/clean-1055-1062-459216000-be47fc4/parts/` (183 bestanden, 263.321 posities); matrix-run `result/a4c-parts/` (792.012) |
| H3 run | `research/clean-test/h3-dbef1b8/` (result.json fda10383…, result.md fec3b283…, SHA256SUMS c0b39bc6…; h3-parts 1 bestand) — 16:51Z |
| H4 run | `research/clean-test/h4-165bb04/` (result.json 1a5f979a…, result.md 47df4725…, SHA256SUMS 8f2c64a5…; h4-parts 1 bestand) — 17:07Z |
| H5 run | `research/clean-test/h5-15fa3dd/` (result.json 1bad8292…, result.md d53711f4…, SHA256SUMS e0db6b26…; run-parts 1 bestand) — 17:23Z |
| E45 / E49 / E56 | `wallets/mechanism-6b12ec7/` (mechanism.md 389b897f…, .json 203526e4…; SHA256SUMS.coordinator af95bac0…), `wallets/depthband-45a1106/` (3d4575db…, dd58131e…; 701e6854…), `wallets/paidflow-9ad8032/` (99e865a6…, 03669792…; 52d00f75…) — 15:26 / 15:27 / 15:29Z; QUEUE 799 |
| erratum E20-pin | #167 issuecomment-6384965518 (13:45:42Z), tekst `research/erratum-e20-store-cast-2026-10-24-final.md` (8743cc70…); NAS-bewijs 98-clean-0-u64-proof-be47fc4 GREEN (`backtests/store-u64-proof-be47fc4/` SHA256SUMS 0afae009…) |
| uitlezing | `research/clean-test/e20-uitlezing-2026-10-24/` SHA256SUMS ac8dd038…: `e20_k3_dag.py` 7dd83ec6…, `.out` f290601b…; `dev_k3_rt.py` b9c29ba1…, `.out` ef884967…, `dev-k3-rt.parquet` ebc8b98b…; `e20_fp_power.py` f1549de9…, `.out` 83bab218…; `hx_dagreeks.py` b07e1d8f…, `.out` 0301d16d…; `hx_fp_power.py` da8e081e…, `.out` 8c2ce6b9… |
| §7-noten | `lab/reports/2026-10-24-{wash-trigger,hold180,flow300,flow300-tp100}-clean.md` (`wallets.clean_reports --date 2026-10-24 --e20 … --h3 … --h4 … --h5 …`, PINS["E20"] = be47fc4) |
| register | rijen E20, E23 (H3), E29 (H4), E31 (H5), E45, E49, E56 → gesloten met uitkomst; datagebruik 1055–1062 → verbruikt |
| nog open | `k3-rows.parquet` (#557, NAS 98-clean-5; eigen commit met apart gepinde `k3-rows/SHA256SUMS`); fout/trefkans E45/E49/E56 (dev-only NAS-job op verzoek); pipeline-diagnostiek (i)/(ii) bij Lab Backtest |
