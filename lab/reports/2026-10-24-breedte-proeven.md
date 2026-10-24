# Breedte-proeven E71–E74: vier vooraf vastgelegde stop/go-proeven op ongeteste families (24 oktober 2026)

Opdracht van de coördinator (24 okt, audit-actie 5): per ongeteste familie één vooraf vastgelegde stop/go-proef op dev 1033–1046, beoordeeld op de eigen nettomarge per trade na kosten bij d 3 en drain-bewuste prijspaden, niet tegen "< 20 SOL/dag". Plan **v0.2** `research/breedte-proeven-plan-2026-10-24.md` sha256 7bbd1aa112bdad00bba8ff504749d6bb04cf1af99b4ee9fd3ae9d70b590490b1 (v0.1 503b0346…), bevroren vóór het eerste getal; coördinator en LAB review akkoord. Code: `lab/wallets/probes/` + `lab/wallets/nas_probes.sh` (#546 gemerged 4064111: harnas, E74, E71; #547 gemerged 5cbdf23: E72, E73). Deze noot groeit per proef; nu E74. E71 draait op commit 03bd9a7, E72/E73 op 5cbdf23 (NAS zaterdagavond).

## 0. Kort

- **E74 niet-SOL-quote (PUMP-curves): STOP (feit).** Nettomarge per trade bij 0,5 SOL **−58,9 % [−61,2; −56,5]** (dag-geclusterd, 17 dagen); 262 trades/dag; 52 % van de posities verliest ≥ 90 %; **−77 SOL/dag bij 10 SOL**, −213 bij 25, −1.098 bij 100. De PUMP→SOL-omrekening is niet de oorzaak: in PUMP-eenheden (zonder omrekening) is de marge −58,1 %. Het verlies zit in de curves zelf: twee derde van de posities eindigt op de stop en op curves ≥ 10 SOL diep is de marge nog −21 %. 1 blik, geen vervolg.
- **E71 Mayhem-munten in de curvefase: STOP (feit), met een voorbehoud bij het kopcijfer.** Pariteitspoort OK (14 van 14 epochs 100 %). Primaire cel −68,8 % [−69,6; −68,1], agent-vrij −59,6 % [−60,9; −58,3]; 8.067 trades/dag; mediaan hold 6 slots. **Maar:** 79 % van de instappen zit op curves met minder dan 0,5 SOL echte reserve (mediaan 0,14 SOL), en het harnas capt een verkoop op de gelogde echte reserve zónder onze eigen inleg — op zulke curves kan het de positie niet waarderen en valt de stop vrijwel meteen (artefact, zie §3.3). Op curves ≥ 2 SOL diep (12.145 instappen, 580/dag), waar die cap niet bindt, is de marge **−47,8 %** (agent-vrij −17,1 %) en is elke dag negatief (dagmarges −31 … −71 %). De uitkomst STOP staat dus op het schone deel; het kopcijfer −69 % is geen marktgetal. **Meetfoutcorrectie §2.0:** over alle 191.788 Mayhem-mints piekt 2,25 % ≥ ×10 (gegradueerd 7,36 %, niet-gegradueerd 2,10 %), niet 19,8 %. 2 blikken, geen vervolg. **Herrun op het gefixte harnas (§3.4, 9d9b43c, getelde blik): STOP blijft — primair −19,30 % [−19,59; −19,02], agent-vrij −5,02 % [−5,13; −4,92], 3.841 trades/dag, −371 SOL/dag bij 10 SOL, elke dag negatief; de oude −69 % was voor ≈ 50 punten de cap-artefact (stop-aandeel 75 % → 5,6 %). E71 totaal 4 blikken.**
- E72 (ticker-clusters) en E73 (goede wallets samen): volgen na de NAS-runs van zaterdagavond (commit 4457b80).

## 1. Raamwerk (plan §1, kort)

Instap op de eerste vulbare trade ≥ t + 3 slots met eigen impact, venue-fees uit de rij en base-netwerkkosten (`wallets.runners`); marks = netto van een volledige verkoop op de toestand ná elke latere trade (pool-marks incl. Withdraw/Deposit, gecapt op de echte SOL); uitstap trail30 (mark ≤ 70 % van de hoogste eerdere) of stop (≤ 50 % van de kosten), verkoop op de eerste mark ≥ triggerslot + 3, anders de laatste mark binnen 6 u; positie = kapitaal/20 (0,5 / 1,25 / 5 SOL), 5 plekken, één positie per key, signalen in triggervolgorde; primaire maat = Σ netto / Σ kosten bij 0,5 SOL met dag-bootstrap (10.000×, seed 7, nuldagen meegeteld); GO = ondergrens > 0 én ≥ 3 trades/dag. Optelbaarheid: Pearson van de dag-netto met H5 (E63-parts, flow300_tp100_H6 organic d 3) en hold300 (geen arm-filter) en het aandeel triggers binnen 60 s op dezelfde key; beschrijvend.

## 2. E74 — curves met PUMP als quote (plan §5): STOP

**Bron (feit):** NAS-job 99zzzzz62 op commit 03bd9a7, rc 0 (03:49Z), `wallets/probes-e74-03bd9a7/` (SHA256SUMS 1ff8b367… over 110 bestanden; `report-e74.json` 0ede9370…, `report-e74.md` 3e5612e8…); diagnose `research/breedte-proeven-2026-10-24/e74_diag.py` 0ff4f265… / `e74_diag.out` c18abe9a… (SHA256SUMS 6e71fec8…). Universum: PUMP-quote mints, niet-Mayhem, create vanaf slot 447.266.123 (epoch 1035, de creatie van de referentiepool): 12 epochs, 17 UTC-dagen, 6.282 e10-signalen op 6.282 mints; 0 prijs-fallbacks, 0 rijen zonder kosten.

| cel | kapitaal | signalen | genomen | trades/dag | marge (SOL) | marge (PUMP-eenheden) | CI 95 % | SOL/dag | ≥ 90 % verlies | mediaan hold |
|---|---|---|---|---|---|---|---|---|---|---|
| e10 | 10 SOL | 6.282 | 4.450 | 261,8 | **−58,9 %** | −58,1 % | [−61,2; −56,5] % | **−77,3** | 52,4 % | 115 slots |
| e10 | 25 SOL | 6.282 | 4.638 | 272,8 | −62,5 % | −62,0 % | – | −213,1 | 54,2 % | 98 |
| e10 | 100 SOL | 6.282 | 5.239 | 308,2 | −71,3 % | −71,1 % | – | −1.098,3 | 49,6 % | 52 |

Zonder capaciteit (alle 6.282 signalen, 0,5 SOL): −57,8 % in SOL, −57,1 % in PUMP. Dagblokken 1–9 / 10–20: −494 / −819 SOL netto; per epoch −52 … −68 %, geen enkele epoch boven −50 %. Afgekapt aan de hold-out: 0.

**De omrekening is niet de oorzaak (feit, e74_diag.out):** de referentieprijs (mid-prijs van pool 5r9N5yEc…, mediaan 0,0358–0,0483 lamports per PUMP-eenheid per epoch, min/max binnen een epoch −14 … +9 % t.o.v. de epoch-mediaan) staat bij uitstap gemiddeld op 1,0001× die bij instap (mediaan 1,0000); de referentie is gemiddeld oud (p50 8.718 slots ≈ 39 min, p90 35.786) en de pool is 0,005 SOL diep (modelaanname: mid-prijs + 0,30 % per kant), maar het verschil tussen SOL- en PUMP-marge is 0,8 punt op −58.

**Waar het verlies zit (feit):** per uitstapreden (0,5 SOL, alle signalen): stop 2.466 × −74,1 %, end_after_stop 1.614 × −99,2 % (de stop viel en er kwam geen trade meer: de positie staat op de laatste mark), trail 2.052 × −11,8 %, end_after_trail 57 × −93,9 %, end 93 × +146 %. Slechts 34,7 % van de instappen ziet ooit een mark boven de kosten; 9,6 % krijgt na onze koop geen enkele trade meer. Diepte van de PUMP-curve bij instap (in SOL via de referentieprijs): p10 0,07, p50 3,2, p90 14,0 SOL; in 25,8 % van de instappen is ons budget (13,5 k PUMP ≈ 0,5 SOL) groter dan de hele echte reserve. Marge per dieptebucket: < 0,1 SOL −83 % (820), 0,1–0,5 −87 % (811), 0,5–2 −75 % (963), 2–10 −49 % (2.508), **≥ 10 SOL −21 % (1.180)**. Ook de diepe curves verliezen dus; de dunne maken het extreem. 96,5 % van de posities is binnen een uur gesloten (mediaan 96 slots ≈ 26 s).

**Optelbaarheid (beschrijvend):** Pearson dag-netto met H5 0,16 en met hold300 0,35 over 17 dagen; 0 van de triggers binnen 60 s op dezelfde key als een H5- of hold300-instap (andere munten). Niet relevant bij STOP.

**Caveat (toegevoegd na E71, §3.3):** ook hier capen de marks een verkoop op de gelogde echte reserve zónder onze eigen inleg; in 25,8 % van de instappen is ons budget groter dan de reserve, en de dunne buckets (< 0,1 SOL −83 %, 0,1–0,5 −87 %) dragen die cap. Het oordeel staat op het deel waar de cap niet bindt: **≥ 2 SOL diep, 3.688 instappen (2–10 SOL 2.508 × −49 %, ≥ 10 SOL 1.180 × −21 %)**, beide ver onder nul.

**Oordeel:** STOP — de ondergrens ligt ver onder nul in elke lezing (SOL, PUMP, met en zonder capaciteit, elke epoch, ook op de diepe curves waar de cap niet bindt). Geen vervolg-pre-reg. 1 blik (de primaire cel; kapitaal 25/100 en de diagnose zijn beschrijvend). Lezing in één zin: PUMP-quote-curves verliezen op dev ongeveer 58 % per trade met trail30/stop, de helft van de posities is een drain.

**Wat dit niet zegt:** niets over USDC- en overige quotes (geen SOL-koers in de store, plan §5.1) en niets over Meteora DLMM (nooit geëxtraheerd, plan §6); niets over een andere instap dan e10 of een andere uitstap dan trail30/stop (één regel vooraf).

## 3. E71 — Mayhem-munten in de curvefase (plan §2): STOP, kopcijfer met voorbehoud

**Bron (feit):** NAS-job op commit **03bd9a7** (poging 2; poging 1 faalde op een ontbrekende uitvoermap, hersteld in #550 4457b80 zonder rekenwijziging; de wrapper maakte de mappen vooraf aan, provenance in `SHA256SUMS.provenance` 103f6bfd…), rc 0 (07:30Z), `wallets/probes-e71-03bd9a7/` (SHA256SUMS 06ef5ffa…; `report-e71.json` 7bcfaae0…, `report-e71.md` 508809de…); diagnose `research/breedte-proeven-2026-10-24/e71_diag.py` 2ef5bd27… / `.out` d3c02ae8…, prijspaden `e71_paths.py` e5b6a6dd… / `.out` 23d3f19f… (SHA256SUMS 95b1f048…). Universum: Mayhem-mints met SOL-quote, create in dev: 14 epochs, 185.651 e10-signalen op 185.651 mints; 21 UTC-dagen met signalen (incl. de partiële 11 sep; dagbasis in het plan 20: de bootstrap telt 21 dagen).

**3.1 Poort en basisrates (feit).** Fill-pariteit: 14 van 14 epochs 10.000/10.000 (kopen exact-in óf exact-out, verkopen exact) → E71 wordt beoordeeld. §2.0 meetfoutcorrectie: over **alle 191.788** Mayhem-mints met SOL-quote en ≥ 1 trade piekt ≥ ×2 28,4 %, ≥ ×5 6,4 %, **≥ ×10 2,25 %**, ≥ ×20 0,76 % binnen 72 u vanaf de eerste trade (curve vq/vt én pool), mediaan ×1,44; gegradueerd (5.487) ≥ ×10 7,36 %, niet-gegradueerd 2,10 %. De ×10-noot §2 meldde 19,8 % op 5.326 mints: dat was de poolfase van alleen de gegradueerde Mayhem-mints (runners hield hun curve-legs buiten, dus p0 = de eerste pooltrade) — de noemer verklaart het grootste deel (2,9 % van de mints is gegradueerd), het resterende verschil 7,36 % vs 19,8 % is een definitieverschil (vanaf de eerste curve-trade over curve én pool, tegen vanaf de eerste pooltrade over alleen de pool; de Mayhem-agent reset de virtuele SOL in zijn trades, zodat de vroege curve-prijs anders ligt dan de poolstart) en is hier niet verder uitgesplitst (onbekend deel, hypothese). De ×10-rij blijft dus een poolfase-maat van gegradueerde Mayhem-coins; als basisrate over Mayhem-munten geldt 2,25 %.

**3.2 Cellen (feit, report-e71.json).**

| cel | kapitaal | signalen | genomen | trades/dag | marge | CI 95 % | SOL/dag | ≥ 90 % verlies | mediaan hold |
|---|---|---|---|---|---|---|---|---|---|
| e10 | 10 SOL | 185.651 | 161.342 | 8.067 | **−68,8 %** | [−69,6; −68,1] % | −2.780 | 26,6 % | 6 slots |
| e10 | 25 SOL | 185.651 | 170.401 | 8.520 | −81,3 % | – | −8.665 | 52,3 % | 5 |
| e10 | 100 SOL | 185.651 | 176.407 | 8.820 | −92,6 % | – | −40.840 | 81,4 % | 5 |
| e10 agent-vrij | 10 SOL | 185.651 | 161.342 | 8.067 | **−59,6 %** | [−60,9; −58,3] % | −2.408 | 18,7 % | 6 |
| e10 agent-vrij | 25 / 100 SOL | | | | −76,6 % / −91,4 % | – | −8.157 / −40.306 | | |

Per reden (0,5 SOL, alle signalen, e71_diag.out): stop 139.067 × −76,8 % (mediaan 5 slots), trail 33.490 × −23,8 %, end_after_stop 12.896 × −100 %, end 39 × −0,9 %; agent-vrij: cut 160.880 × −55,8 % (87 % krijgt een agent-leg vóór de uitstap). Per epoch −66 … −72 %, dagblokken 1–9 / 10–20 −69,4 / −68,4 %; 99,9 % van de posities is binnen een uur gesloten; 2,2 % krijgt na onze koop geen trade meer. Optelbaarheid (beschrijvend): Pearson dag-netto met H5 0,03, met hold300 0,13; 0 gelijktijdige triggers.

**3.3 Het kopcijfer is deels een artefact van het harnas (feit + lezing).** Diepte van de curve (echte reserve) bij instap: p10 0,044, p25 0,071, **p50 0,138**, p75 0,391, p90 1,207 SOL; **79 % ligt onder ons budget van 0,5 SOL**, 66 % onder 0,25. De marks capen een verkoop op de gelogde echte reserve (`least(rq, …)` in `runners.marks`, een conventie die `runners` en de ×10-jager delen): die reserve bevat onze eigen inleg níet. Op een curve van 0,1 SOL wordt onze positie van 0,5 SOL dus op hooguit ≈ 0,1 SOL gewaardeerd, ook als niemand handelt — de prijspaden laten dat zien (e71_paths.out: een curve van 0,094 SOL geeft bij de instap-mark 0,22× de kosten, een van 0,127 SOL 0,22×, terwijl een curve van 2,4 SOL 0,955× geeft = onze eigen impact + fees ≈ −4,5 %). Daardoor valt de −50 %-stop op dunne curves meteen (bucket < 0,1 SOL: 70.915 instappen, 99,3 % stop, mediaan 4 slots, −84,7 %; 0,1–0,5 SOL: 75.833, 87,4 % stop, −64,6 %). Per dieptebucket:

| diepte bij instap | n | marge | agent-vrij | aandeel stop | ooit > kosten | mediaan hold |
|---|---|---|---|---|---|---|
| < 0,1 SOL | 70.915 | −84,7 % | −85,0 % | 99,3 % | 22,7 % | 4 |
| 0,1–0,5 | 75.833 | −64,6 % | −58,0 % | 87,4 % | 36,2 % | 6 |
| 0,5–1 | 16.657 | −49,2 % | −16,4 % | 40,3 % | 66,9 % | 22 |
| 1–2 | 10.101 | −48,0 % | −14,9 % | 38,7 % | 68,7 % | 20 |
| 2–5 | 7.249 | −50,4 % | −17,7 % | 41,2 % | 71,3 % | 18 |
| 5–20 | 4.486 | −44,8 % | −16,4 % | 35,2 % | 72,9 % | 15 |
| ≥ 20 | 410 | −34,5 % | −14,3 % | 21,2 % | 74,1 % | 9 |

De ongecapte constant-product-waarde op de gelogde toestand negeert óók onze eigen verschuiving van de virtuele reserves, maar dat is klein bij ≈ 30 SOL virtueel (daarom 0,955× op de curve van 2,4 SOL); de fout zit alleen in de cap. **Fix-spec (runners, niet hier):** cap = gelogde rq + onze netto inleg in de reserve ná de curve-fee, pool-analoog (vault + onze netto SOL), met een test op een dunne curve.

**Schone lezing:** op curves ≥ 2 SOL (12.145 instappen, ≈ 580/dag, cap bindt niet: ons budget ≤ 25 % van de reserve) is de marge **−47,8 %**, agent-vrij **−17,1 %**, en elke UTC-dag negatief (dagmarges −31 … −71 %). De prijspaden van diepe curves tonen wat er gebeurt: de agent verkoopt in brokken van 0,3–0,6 SOL ná onze koop (trail), en daarnaast gewone drains (één verkoop van 2,4 SOL die een reserve van 2,8 SOL naar 0,09 brengt). Zonder de agent-legs blijft −17 % over: eigen impact + fees (≈ −4,5 % bij 2,4 SOL) en de bewegingen vóór de eerste agent-leg.

**Oordeel:** **STOP** — op het schone deel (≥ 2 SOL) zijn beide cellen (primair én agent-vrij, plan §2.4) ver onder nul en is geen dag positief; de poort vraagt een ondergrens > 0 in beide. Het kopcijfer −69 % is geen marktgetal maar vooral de cap op dunne curves; de eerlijke uitspraak is: **Mayhem-curves bij e10 zijn te klein voor 0,5 SOL (79 % heeft minder reserve dan onze inleg) en op de curves die groot genoeg zijn verliest de regel ≈ 48 % per trade, zonder agent-legs ≈ 17 %.** Geen vervolg-pre-reg. 2 blikken (primair + agent-vrij); dieptebuckets en kapitaal 25/100 zijn beschrijvend.

**3.4 Herrun op het gefixte harnas (feit; getelde blik, 24 okt ~22:00Z).** Bron: NAS-job 99zzzzz80 op commit **9d9b43c** (main ná de mark-cap-fix #552 en de Codex-correcties #559/#560: cap = `least(out(pos), echte reserve + eigen inleg)`), rc 0, `wallets/probes-e71-9d9b43c/` (SHA256SUMS af26869f…, 170 bestanden; `report-e71.json` 8cf40365…, `report-e71.md` 1e0c4fb9…; de parquets zijn niet naar de VPS gesynct, dus de dieptebuckets van §3.3 zijn niet herberekend). Zelfde universum en regels (185.651 e10-signalen, 21 dagen), pariteit 14 × 1,0.

| cel | kapitaal | genomen | trades/dag | marge | CI 95 % | SOL/dag | ≥ 90 % verlies | mediaan hold |
|---|---|---|---|---|---|---|---|---|
| e10 | 10 SOL | 76.811 | 3.841 | **−19,30 %** | [−19,59; −19,02] % | −371 | 0,0 % | 24 slots |
| e10 | 25 / 100 SOL | 67.024 / 64.247 | 3.351 / 3.212 | −25,9 % / −39,1 % | – | −1.086 / −6.277 | 0,0 % | 25 / 22 |
| e10 agent-vrij | 10 SOL | 76.811 | 3.841 | **−5,02 %** | [−5,13; −4,92] % | −96,5 | 0,0 % | 24 |
| e10 agent-vrij | 25 / 100 SOL | | | −10,1 % / −28,4 % | – | −422 / −4.568 | | |

Wat de fix deed: de stop was op het oude harnas 75,1 % van de uitstappen van de genomen instappen (121.136 van 161.342; 81,9 % incl. einde-na-stop; report-e71.json 03bd9a7, e10 10 SOL; mediaan hold 6 slots, 26,6 % van de posities ≥ 90 % verlies); nu is het trail 74 %, einde-horizon 17 %, stop 5,6 %, mediaan hold 24 slots en 0,0 % ≥ 90 % verlies — de cap waardeerde de positie op dunne curves op de gelogde reserve en liet de −50 %-stop meteen vallen (§3.3), precies wat de fix wegneemt. Omdat posities langer open staan binden de 5 plekken nu (76.811 van 185.651 genomen); de marge over álle signalen zonder plekkenplafond is −19,5 %, dus de selectie flatteert niet. Elke dag van de 21 is negatief (−50 … −526 SOL/dag bij 10 SOL). Basisrates ongewijzigd (§3.1). Optelbaarheid: Pearson dag-netto met H5 −0,12, hold300 −0,03; 0 gelijktijdige triggers.

**Oordeel op het gefixte harnas: STOP** op de plan-primaire cel (alle instappen; addendum §5 maakt die weer het oordeel): primair én agent-vrij ver onder nul, geen dag positief. Lezing: van de oude −69 % was ≈ 50 punten harnas-artefact; het marktgetal van e10 op Mayhem-curves is ≈ −19 % per trade, en zonder de agent-legs ≈ −5 % (eigen impact + fees ≈ −4,5 % op een curve van 2,4 SOL, §3.3, plus de bewegingen vóór de eerste agent-leg). Het schone deel ≥ 2 SOL (§3.3: −47,8 % oud) is niet herberekend; op het gefixte harnas is dat onderscheid niet meer nodig voor het oordeel. **Caveat:** 9d9b43c ligt vóór #586 — boost/withdraw/deposit-marks kregen hun fee-tiers via een ASOF op het slot alleen (≤ één tier-stap, 5–10 bps, op ≈ 0,4 % van de boost-marks op dev 1033); raakt het oordeel niet. 2 blikken (primair + agent-vrij), E71 totaal 4; geen vervolg.

**Gevolg voor de rest van het lab (hypothese, te checken):** dezelfde cap zit in `runners.marks` en dus in de ×10-jager-labels (E64: instappen e10/c30/c2 op jonge curves met reserve < budget): daar kan een mark nooit boven de gelogde reserve komen, zodat hit-rates op dunne curves onderschat zijn. Omvang onbekend; een correcte cap is `rq + eigen netto inleg` (de SOL die wij zelf in de curve stopten blijft erin tot anderen verkopen). Dat is een `runners`-wijziging met spec en review, niet in deze noot; E64's conclusie (geen model haalt de poort) is er niet door omgedraaid zonder herrun, maar de labelbasisrates zijn er wel door geraakt.

## 4. E72 — ticker-/naamhypes: STOP

NAS-job 99zzzzz78 op commit **9d9b43c** (`STEP=e72`; het gefixte harnas: mark-cap #552/#559, W2 #560), uitvoer `wallets/probes-e72-9d9b43c/` (report-e72.md 26b77c5e…, report-e72.json 1803162f…; NAS-SHA256SUMS 66bf1daa… over 170 bestanden; op de VPS zonder parquets, 44 van 44 gesyncte bestanden `-c` OK). Leesregel zoals in §5-addendum: op het gefixte harnas is **de plan-primaire cel 'copy' bij 10 SOL het oordeel**; dieptebuckets beschrijvend (hier niet berekend: de parquets staan alleen op de NAS en het oordeel hangt er op het gefixte harnas niet van af).

**Basis (feit, dev 1033–1046, 14 epochs):** 428.317 creates met een niet-lege symboolsleutel (SOL-quote, niet-Mayhem); 103.828 daarvan zijn kopieën rang 2–5 binnen 60 min, waarvan 14.770 door de generieke-naamfilter (≥ 20 creates/24 u) vervallen → **89.058 kopieën gehouden** (20,8 % van de creates) en **47.305 originelen** (eerste create van een cluster, gekocht op het moment van de tweede create). Signalen (e10 van de kopie): 69.345 over 21 UTC-dagen (dagbasis 20); 512.268 rijen (6 momenten × 3 grootten), 0 rijen zonder kosten.

| cel (10 SOL = 0,5 SOL per trade) | signalen | genomen | trades/dag | marge per trade | 95 %-dagbootstrap | SOL/dag | ≥ 90 % verlies | mediaan hold (slots) |
|---|---|---|---|---|---|---|---|---|
| **copy (primair, rang 2–5 gepoold)** | 69.345 | 7.420 | 371 | **−10,30 %** | **[−11,65; −8,98]** | **−19,1** | 0,4 % | 169 |
| copy2 | 39.395 | 6.681 | 334 | −10,92 % | [−12,09; −9,61] | −18,3 | 0,6 % | 223 |
| copy3 | 16.195 | 7.007 | 350 | −10,91 % | [−12,48; −9,32] | −19,1 | 0,3 % | 169 |
| copy4 | 8.459 | 5.853 | 293 | −11,40 % | [−13,14; −9,36] | −16,7 | 0,3 % | 180 |
| copy5 | 5.296 | 4.647 | 232 | −10,39 % | [−12,59; −7,98] | −12,1 | 0,3 % | 138 |
| orig (secundair) | 32.066 | 6.695 | 335 | −8,82 % | [−9,39; −8,03] | −14,8 | 0,4 % | 173 |

Bij 25 en 100 SOL (1,25 / 5 SOL per trade) wordt elke cel slechter: copy −14,1 % / −29,0 % per trade (−65 / −528 SOL/dag), orig −12,5 % / −27,5 % — de eigen prijsimpact op dunne curves domineert.

**Oordeel: STOP** (regel §1: ondergrens > 0 én ≥ 3 trades/dag; de ondergrens van de primaire cel ligt op −8,98 %). Alle zes cellen zijn bij 10 SOL ondubbelzinnig negatief met intervallen ver onder nul; per rang is er geen gradient die een andere cel zou redden (copy2–copy5 −10,4 … −11,4 %), en het origineel (gekocht zodra het een cluster blijkt) verliest −8,8 %. Lezing (reconstructie met het kostenmodel): een ticker die wordt gekopieerd is op dev geen koopsignaal — de kopieën én het origineel gaan na het kopiemoment gemiddeld tien procent omlaag binnen de 6-uurs-horizon met trail30/stop −50 %. Adds-to (§1): dag-netto-correlatie met H5 −0,30 en met hold300 −0,22 over 20 dagen; 0 triggers binnen 60 s op dezelfde key (geen gelijktijdigheid met de F7-arm). Het ≥ 90 %-verlies-aandeel (0,3–0,6 %) is laag: het verlies is breed, niet een paar rugs. Blikken: 2 (copy + orig), conservatief als in §6. Geen vervolg; geen forward-pre-reg (E72 is gesloten als idee op dev).

## 5. E73 — meerdere goede wallets samen: volgt

NAS op commit 4457b80 (`STEP=e73`); **eerste run (99zzzzz79, 9d9b43c) crashte in het lege venster 1033 (goede set begint 19 sep): getypeerde triggertabel + leeg resultaat gefixt, vensters vóór de eerste goede dag draaien leeg mee, dagbasis = dagen met een goede set (19 sep – 1 okt, 13 dagen); herrun op de fix-commit**; goede set (plandrempel 5 SOL = 5e9 lamports) ≈ 8–14,5 k wallets per dag (feit, sizing-query 24 okt, niet gepind); cellen k2n30 (primair), k3n30, k2n10, k2n120. Lezing vooraf: "hun gelijktijdige kopen volgen", niet "kopiëren". **Leesregel (vooraf, na E71 §3.3):** dieptebuckets als bij E72; oordeel op het ≥ 2 SOL-deel; afwijkend oordeel op de volledige set → NOT_JUDGED tot de herrun op het gefixte harnas.

**Addendum (vooraf, 24 okt ≈ 10:30Z, vóór enige E72/E73-uitslag; coördinator + LAB review):** E72 en E73 worden aangehouden tot de mark-cap-fix (#552, spec sha 4760bff9…) gemerged is en draaien dan op de fix-commit. Op het gefixte harnas is de plan-primaire cel (alle instappen) weer het oordeel; de dieptebuckets blijven beschrijvend. De herruns E64 → E71/E74 op het gefixte harnas zijn nieuwe, getelde blikken met eigen archief; het oude E64-oordeel blijft in de registerrij als "oud harnas".

## 6. Blikken

Conservatief geteld (plan §1 v0.2): E74 1 (primaire cel), E71 2, E72 2, E73 4 = 9 bij voltooiing; na E74, E71 en E72: 5. Teller ≈ 2.305 + 6 (schone toetsen 24 okt) + 2 (E72) = ≈ 2.313 vóór E73/E75. **Herruns op het gefixte harnas zijn nieuwe getelde blikken (addendum §5): E71-herrun +2 (§3.4) → na E74, E71 (2×) en E72: 7; teller ≈ 2.315 vóór E73/E74-herrun/E75.**
