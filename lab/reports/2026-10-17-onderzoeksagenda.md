# Onderzoeksagenda LAB research (versie 1, 2026-10-17)

Op verzoek van de eigenaar (via de coördinator, 2026-10-17): mijn beste ideeën voor zelfstandig, datagedreven onderzoek op de Old Faithful-data, ook buiten de bestaande signaalmomenten. Per idee: waarom kansrijk, wat het nodig heeft, hoe het eerlijk wordt getoetst, en wat ik als eerste doe. De spelregels blijven: alles point-in-time, realistische kosten en vertraging (d = 2/3 slots), vrij verkennen op dev ≤ 1046 met elke proef in het register, hold-out 1047–1051 alleen voor diagnose, clean 1055–1063 LOCKED tot E20/H3/H4/H5, claims alleen op forward ≥ 1064 met vastlegging vooraf, DSR over de proeven en een blik van LAB review. Niets hiervan verandert K3 of H4/H5 in paper. Codes staan uitgelegd in `woordenlijst.md`.

## Wat de data tot nu toe zeggen (de basis van de keuzes)

1. **Het enige positieve signaalmoment is herstel na een grote verkoop in reversed pools (F7 pool_reversed)**, en dan vooral op pools zonder volume-farmers (organic), met vasthouden zolang de echte instroom positief is (H4) of tot +100 % (H5). Alles op de curve (c-momenten, F1), bij de migratie (M1) en in normale pools is negatief bij ≥ 600 ms vertraging (E02, E07, E27, E28).
2. **De grootste verliezer is de LP-drain**: 19–22 % van de H4-posities eindigt in een leeggehaalde pool en de slechtste dagen komen daaruit (E28, E29); mediaan 28 min van trigger tot drain (E21); de fabriekspools van K2 drainen binnen 5 minuten (E33).
3. **Wat winnaars verdienen is óf snelheid, óf de pump zelf, óf voorkennis van de drain** (E24, E33): kopiëren faalt bij 2–3 slots vertraging. Een traag, klein kopieerspoor achter persistente wallets (K1) is het enige dat in de replay overeind bleef en staat geparkeerd.
4. **Insider-/transferkenmerken op het triggermoment onderscheiden niets** (E22, E26); ná de instap voorspelt echte instroom wél (AUC 0,65), vandaar H4.

Dus: de hefboom zit niet in nóg een exit op F7, maar in (a) het **vermijden van drains** vóór ze gebeuren, (b) **nieuwe instapmomenten** die niet van snelheid afhangen, en (c) het **universum** (welke pools überhaupt).

## Idee 1 — Drainrisico vooraf: een point-in-time veto en exit voor reversed pools

**Waarom kansrijk.** De drains bepalen de staart en de slechtste dagen van H4/H5. K2 liet zien dat pump-en-drain-pools op minuut 2 al herkenbaar zijn (eenmalige creator die nooit handelde, b0 ≥ 300 SOL, ≥ 40 traders in 2 min: 99 % precisie). De D-noot geeft de hazard van trigger naar drain; creator-historie (eerdere pools van dezelfde creator en hoe die eindigden), LP-leeftijd en LP-mutaties zijn allemaal vóór de trigger bekend. Een veto dat 30–50 % van de drains vangt tegen weinig gemiste winnaars zou H4's slechtste dagen halveren.

**Data, tools, rekentijd.** Store dev2 (pools, pool-rijen incl. deposit/withdraw, creators), `survival-v1` (E21), de H4/H5-dev-parts (job 87, `bigtp-dev-1033-1046-4dbd34b`) om het effect per positie te meten. Tools: DuckDB; `lifelines` (Cox/Kaplan-Meier) voor de hazard, al in `requirements-lab.txt`. Rekentijd: kenmerken per pool op de VPS (zoals K2: ~10 min per 14 epochs); de hazardfit op de NAS als hij over alle 51 k reversed pools gaat.

**Eerlijke toets.** Veto en exit als **overlay** op de bestaande H4/H5-posities, dev-only, in de H4-vorm (Σ netto SOL per UTC-dag, dagbootstrap): Δ Σ/dag, Δ slechtste dag, Δ drains, gemiste winnaars, dag 1–9 en 10–18 apart, beide regimes; elke drempelvariant is een proef in het register (DSR). Pas daarna een vastlegging als extra arm voor een **volgende** forward-ronde; de lopende forward-toets (E32) blijft zoals hij is.

**Eerste stap.** De K2-kenmerken en de creator-historie als kolommen bij elke H4-trigger (point-in-time op t), en de simpele telling: hoeveel van de 19 % drains en van de winst zit in pools die het veto zou raken. Eén VPS-run, één tabel.

## Idee 2 — Een nieuw instapmoment: de grote koop op een jonge curve ("whale-pump")

**Waarom kansrijk.** Winnaars II (E33): de trage curve-winst van groep C komt uit kopen > 20 SOL op curves jonger dan een minuut, 1,5–3× de startprijs; de koper is de pump en verkoopt aan wie erna komt. Dat is een **gebeurtenis** die wij 2–3 slots later zien, zonder wallet-label. De vraag is of de instroom ná zo'n koop lang genoeg aanhoudt voor een kleine volger: de 0,5 SOL-replay achter kopen ≥ 5 SOL gaf +0,11…+0,15 SOL per ronde, maar die replay kende de wallet én de uitstap van de koper. Nieuw signaalmoment, buiten F7, niet van reactiesnelheid afhankelijk (de koop zelf is de trigger, de houdtijd minuten).

**Data, tools, rekentijd.** Store dev2 (curve-rijen); de bestaande replay-engine via een nieuwe familie bij Lab Backtest ("grote koop ≥ X SOL op een curve ≤ Y oud, prijs ≤ Z× start", exits hold / trail / tp uit het bestaande grid, d 2/3, farm-label n.v.t. op de curve, mayhem uitgesloten). Een dev-run van 14 epochs op de NAS (~1 u); vooraf een VPS-verkenning op één epoch met `runners.py`-achtige marks.

**Eerlijke toets.** Zoals E27/E28: per dagblok en regime, Σ/dag met dag-CI, drains, top-1 %-concentratie, DSR over het grid (alle X/Y/Z/exit-combinaties tellen als proeven), hold-out alleen als diagnose. Positief in beide blokken en beide regimes → vastlegging voor forward.

**Eerste stap.** Op één dev-epoch: alle kopen ≥ 20 SOL op curves ≤ 5 min oud, de marks 1 / 5 / 15 / 60 min erna (runners-methode), gesplitst naar de prijsmultiple op dat moment en naar wie koopt (bekende farmer of niet). Dat zegt binnen een uur of er iets te halen is vóór er een familie wordt gebouwd.

## Idee 3 — Track record van de creator als point-in-time kenmerk (curve én pools)

**Waarom kansrijk.** In K2 hadden de "overige" reversed pools creators met mediaan 7 en p90 11.953 eerdere pools: seriële fabrieken. Van elke eerdere pool/mint van dezelfde creator is de uitkomst bekend vóór de nieuwe begint (gemigreerd? gedraineerd na hoeveel minuten? hoeveel kopers?). Dat is een zuiver point-in-time kenmerk dat nog in geen enkel filter zit, en het raakt zowel het drainrisico (idee 1) als de curve-momenten van E27 (die ongefilterd allemaal negatief waren; een filter op "creator met goede historie" is de meest plausibele deelverzameling die kan omslaan).

**Data, tools, rekentijd.** Store (mints.creator, pools.coin_creator, migrate/complete-slots, withdraw-rijen); runners-v2 (627 k instappen, E27) als meetbank. VPS: creator-historie als één tabel (per creator per dag), dan een join; de kwintieltabellen van E27 opnieuw met dit kenmerk.

**Eerlijke toets.** Dezelfde methode als E27 (≥ 30 runners per cel, kwintielen, simpele exits, beide blokken), elke cel een proef; iets dat alleen op dag 1–9 of alleen in één regime werkt, telt niet.

**Eerste stap.** De creator-historietabel bouwen en de verdeling van "n-de token van deze creator" en "uitkomst van de vorige" tegen de runner-/drainkans zetten.

## Idee 4 — Het universum: pools die niet gedraineerd kúnnen worden (LP gelockt of verbrand)

**Waarom kansrijk.** Als de LP-tokens verbrand of gelockt zijn, bestaat het drainrisico niet; F7/H4 op alléén zulke pools zou de staart structureel wegnemen. Of dat universum groot genoeg is (F7 vuurt nu ~38 k keer over dev) is onbekend.

**Data, tools, rekentijd.** Dit zit **niet** in de store: LP-mint-transfers naar het burn-adres of naar locker-programma's vragen een gerichte OF1-extractie (zoals de transfers-extractie van E19, maar voor de LP-mints van reversed pools). Pas als de schone extractie klaar is; verzoek aan de coördinator. Daarna is de meting zelf licht (pool-lijst × F7-parts).

**Eerlijke toets.** Universum-splitsing van de bestaande H4/H5-dev-parts (gelockt vs niet), Σ/dag en drains per deel; een nieuw universum is een nieuwe vastlegging voor forward.

**Eerste stap.** Bij de coördinator peilen wat één epoch LP-mint-transfers aan OF1-bandbreedte kost; intussen in de store kijken of `lp_supply`-sprongen zonder withdraw (verbranding) al iets verraden.

## Idee 5 — Geparkeerd, voorwaardelijk: traag kopiëren van persistente wallets (K1)

**Waarom (nog) niet.** De 0,5 SOL-replay achter trage curve-instappen van groep-C-wallets bleef als enige kopieerspoor positief (+0,04…+0,06 SOL per ronde, 38 % winstkans, dag 10–18 +0,038). Maar het label is op dezelfde dagen bepaald, en E04 (whale stap 1) liet zien dat volgen van winnaars van gisteren negatief kan zijn. Eerst moet de persistentieset zelf point-in-time houdbaar blijken (label dag 1–9 → volgen dag 10–18, álle rondes). Dat is één VPS-run op de bestaande `c_pos.parquet`; als dat niet standhoudt, sluit K1.

## Volgorde en wat ik vraag

| volgorde | idee | eerste stap | waar | rekentijd | vraag aan de coördinator |
|---|---|---|---|---|---|
| 1 | drainrisico-veto (1) | kenmerken bij H4-triggers + telling | VPS | uren | geen (later NAS voor de hazardfit) |
| 2 | whale-pump-instap (2) | één-epoch-verkenning | VPS | 1 u | daarna: familie bij Lab Backtest + NAS-run (~1 u) |
| 3 | creator-track-record (3) | historietabel + E27-kwintielen | VPS | uren | geen |
| 4 | K1-houdbaarheid (5) | point-in-time persistentieset | VPS | 1 u | geen |
| 5 | ondraineerbare pools (4) | bandbreedte peilen | — | — | OF1-extractie LP-mint-transfers (na clean) |

Elke stap komt als kleine PR met een noot en een registerregel; niets gaat naar paper zonder spec, build en besluit van de eigenaar. Werk voor de paperstart van 20 oktober (clean-rapporten E20/H3/H4/H5, Alpenglow-metingen, de MEV0-rerun-noot) gaat vóór.
