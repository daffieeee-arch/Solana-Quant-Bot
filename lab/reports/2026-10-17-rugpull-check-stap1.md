# Rug-pull-check, stap 1: in de markt waar ons voordeel zit wordt élke pool leeggetrokken

LAB research, 2026-10-17. Eigenaarsprioriteit (via de coördinator): idee 4 van de agenda, "pools die niet leeggetrokken kunnen worden", uitgebreid tot een rug-pull-check voor de pools waarin we handelen. Stap 1 gebruikt alleen de store (dev 1033–1046; hold-out en clean niet gelezen). Verkennend; register E36. Labels **feit** / **reconstructie** / **modelaanname** / **onbekend**. Script `lab/wallets/explore/rug_lp_store.py`.

## 0. Samenvatting

- **Omgekeerde pools (pool_reversed), de enige markt met een positief signaalmoment:** van de 51.248 pools die in dev zijn aangemaakt is **99,95 % binnen dev volledig leeggetrokken** door een LP-withdraw (51.220; de 28 niet-leeggetrokken zijn allemaal van de laatste dag). Mediaan 7,1 minuten tussen de eerste trade en de withdraw; in 68 % haalt de creator zelf de LP weg, in 32 % een andere wallet. Nergens is bewijs van LP-verbranding (0 pools met een dalende LP-supply zonder withdraw) — *feit*.
- **Gegradueerde pump.fun-pools (normaal):** 96 % van de 21.672 in dev aangemaakte normale pools is een pump-curve-token; **1,5 %** krijgt ooit een withdraw. Dat klopt met "LP verbrand bij migratie" (de 332 uitzonderingen zijn vrijwel allemaal geen pump-mints) — *feit*. Maar in die markt is geen van onze signalen positief (E02, E07, E27).
- **Het voordeel bestaat dus binnen de drain-cyclus, niet ondanks.** H4/H5-posities waarvan de pool pas ná onze exit werd leeggetrokken: +0,18 … +0,28 SOL per positie, 0 % verlies-alles, Σ +300 … +330 SOL per blok; posities waarbij de withdraw tijdens de positie viel: −0,35 … −0,45 per positie, 30–56 % verlies-alles, Σ −220 … −240 (dag 10–18). Een filter "alleen ondraineerbaar" laat in de F7-pool_reversed-markt **niets** over — *feit (diagnose met toekomstinformatie)*.
- **Gevolg voor de rug-pull-check:** de vraag "kan deze pool leeggetrokken worden" heeft in onze markt altijd het antwoord ja. De bruikbare check is **"wordt hij leeggetrokken vóórdat wij eruit zijn"**: een point-in-time schatting van de tijd tot de drain (agenda idee 1: creator-klasse, b0, instroom in de eerste minuten, de D-hazard) als veto en als exit. Daar gaat het onderzoek nu heen. Een LP-burn/lock-extractie op dev levert voor reversed pools niets op (er wordt niet verbrand of gelockt); mint-/freeze-authority en Token-2022-extensies blijven een aparte, kleinere risicolaag (zie §4).

## 1. Methode (feit)

- Universum: alle reversed pools met `created_in_period` op dev (`k2_pools.parquet`, 51.248), hun deposit- en withdraw-rijen uit de store (`pool.kind in ('deposit','withdraw')`, `lp_supply` = de LP-mint-supply op het event, `base / b` = het opgenomen deel van de SOL-reserve).
- Tegenproef: normale pools (`orientation = 'normal'`) aangemaakt in dev, zelfde telling.
- Posities: H5 (`bigtp-dev-1033-1046-4dbd34b`, flow300_tp100_H6, organic, d 1/2) en H4 (`long-dev-1033-1046-fc4173c`, flow300_H6, organic), netto via `costs.settle_sql` (base), gesplitst op het moment van de eerste withdraw van de pool ten opzichte van trigger en exit. Dat is een diagnose met toekomstinformatie, geen regel.

## 2. Reversed pools: wat de LP doet (feit)

| LP-lot binnen dev | pools | aandeel | mediaan b0 (SOL) | mediaan min eerste trade → withdraw | eerste withdrawer = creator |
|---|---|---|---|---|---|
| volledige withdraw (≥ 90 % van de reserve) | 51.215 | 99,9 % | 85 | 7,1 | 68 % |
| nooit opgenomen in dev | 28 | 0,1 % (alle op 1 okt: censuur) | 85 | – | – |
| gedeeltelijk | 5 | 0,0 % | – | – | – |

- LP-events: 58.628 withdraws op 51.220 pools; deposits ná de creatie in maar 852 pools (de openingsliquiditeit is geen DepositEvent). Pools met ≥ 2 LP-events: 5.019; LP-supply die daalt zonder withdraw (verbranding): **0**.
- Per creatiedag is het aandeel "nooit opgenomen" 0,0 % op elke dag behalve 1 oktober (1,4 %, het einde van dev).

Normale pools (tegenproef): 21.672 aangemaakt in dev, 332 met een withdraw (1,5 %), daarvan 13 % door de coin-creator, mediaan 97 % van de reserve; 96 % van de normale pools heeft een pump-curve-mint.

## 3. H4/H5-posities naar het LP-lot van de pool (diagnose, toekomstinformatie)

| lot | h | d | n dag 1–9 / 10–18 | Σ SOL dag 1–9 / 10–18 | verlies-alles | gem. per positie |
|---|---|---|---|---|---|---|
| withdraw ná onze exit | H4 | 1 | 553 / 1.639 | +155,4 / +306,3 | 0 % | +0,28 / +0,19 |
| withdraw ná onze exit | H5 | 1 | 614 / 1.795 | +126,8 / +330,9 | 0 % | +0,21 / +0,18 |
| withdraw tijdens de positie | H4 | 1 | 185 / 536 | −64,6 / −238,5 | 30 % / 33 % | −0,35 / −0,44 |
| withdraw tijdens de positie | H5 | 1 | 158 / 500 | −55,3 / −223,8 | 32 % / 36 % | −0,35 / −0,45 |
| withdraw (deels) vóór de trigger | H4/H5 | 1 | 11 / 10–11 | +1 … +6 | 0–9 % | +0,09 … +0,52 |
| nooit opgenomen | – | – | 0 | – | – | – |

d = 2 hetzelfde beeld (tijdens de positie: 49–56 % verlies-alles, Σ −52 … −229). Elke positie zit in een pool die binnen dev wordt leeggetrokken; de P&L is het verschil tussen "wij eerst" en "zij eerst".

Point-in-time kenmerken die er al zijn: een withdraw vóór t komt in 10–12 posities per cel voor (te weinig voor een regel); de openingsliquiditeit b0 (bekend bij creatie) scheidt wel iets: H5 d = 1 b0 100–200 SOL 1 % verlies-alles (n 793, Σ +48), 50–100 SOL 10 % (n 2.111, Σ +130), ≥ 200 SOL 3 % maar Σ −3,6 (n 67). Samen met de creator-klasse (E34) en de instroom in de eerste minuten (K2) is dat de kern van een tijd-tot-drain-schatting.

## 4. Wat dit betekent voor de rug-pull-check (reconstructie)

1. **Literal "alleen ondraineerbare pools"** = alleen gegradueerde pump.fun-pools. Daar is LP-verbranding de norm, maar daar is geen van onze signaalmomenten winstgevend. In de reversed-markt bestaat zo'n pool niet.
2. **De echte check is timing.** Per trigger de kans dat de drain binnen de verwachte houdtijd valt (H4: mediaan minuten, plafond 6 u), geschat uit wat op t bekend is: creator-klasse en -historie, b0, leeftijd van de pool, instroom en aantal traders in de eerste minuten, eerdere (gedeeltelijke) withdraws, en de overlevingscurve van E21. Gebruik: veto bij een hoge kans, en een exit zodra de kans tijdens de positie oploopt. Dat is agenda-idee 1 en gaat nu voor; het wordt op dev als overlay op H4/H5 gemeten en pas forward getoetst.
3. **LP-burn/lock-extractie uit OF1 voor dev**: niet nodig voor de reversed-markt (0 bewijs van verbranding, 99,95 % opgenomen); voor normale pools is het antwoord al bekend (verbrand bij migratie). Ik vraag die extractie daarom **niet** aan.
4. **Mint-/freeze-authority en Token-2022-extensies** (transfer fee, transfer hook, permanent delegate) van de reversed-tokens: een aparte risicolaag ("kan ik wel verkopen"), die de replay nu niet modelleert. Hoe vaak dat in onze posities speelt is *onbekend*; de drain komt in elk geval eerder (mediaan 7 min). Voorstel: (a) voor paper/live een goedkope check vóór de instap via `getAccountInfo` op de token-mint (mint- en freeze-authority, Token-2022-programma en extensies) — spec voor Lab Backtest, en een eigenaarsbesluit over betaalde RPC; (b) voor dev een kleine extractie-spec (InitializeMint / SetAuthority / extensie-initialisaties voor de ~51 k reversed-mints), na de HWM-PR, om te tellen hoe vaak het voorkomt. Geen van beide is urgent ten opzichte van de drain-timing.

## 5. Open punten

- Of de 32 % "andere wallet" die de LP opneemt dezelfde operator is als de creator: *onbekend* zonder funding-data.
- De replay rekent met de echte withdraw-rijen (drains eerlijk); verkoop-blokkades door freeze/hook zitten er niet in — *modelaanname* die alleen gunstiger kan zijn dan de werkelijkheid.
- Alles op één dev-periode; de verhouding "wij eerst / zij eerst" per regime en per dagblok staat in E34/E36 en wordt forward opnieuw gemeten.
