# E63 Kapitaalplafond op dev: wat leveren Terugveer snel (K3), Terugveer lang H5 en de koopstroom-arm C3 netto per dag op bij 10, 25 en 100 SOL? (2026-10-23)

| | |
|---|---|
| vraag | coördinator-opdracht 23 okt (eigenaarsdoel ≥ 20 SOL/dag netto; kapitaal onbekend, dus drie niveaus; eigenaar 22 okt ~22:45Z: "Die 1000 sol hoeft niet" → 10 / 25 / 100 SOL) |
| plan | `data-old-faithful-one/lab/research/capital-ceiling-plan-2026-10-23.md`, sha256 **2a17f2d2ea9d6e8f46bee05eb69e1f6427ab38ca456c794671baf587b3110916** (buiten Git; vastgelegd vóór enig nieuw getal; geen nieuwe regels, geen afstemming) |
| code | `lab/backtest/capital_ceiling.py` + `nas_capital_ceiling.sh` (#529, run op d15ac45; LAB review OK); de review-nits en de vastgepinde gap-regel in deze PR (rapport opnieuw gemaakt op dezelfde rijen) |
| run | NAS-job **99zzzzz52** op d15ac45, rc 0: 769.995 posities in 168 parts, "112 cells over 20 days; parity GREEN" (tp4_T600 bij 0,5 / 1 / 2 SOL, d 2 en 3: rijen en SOL/dag gelijk aan families-v2-1033-1046 op 59ca18c). Gesynchroniseerd naar `backtests/capital-ceiling-d15ac45/`: SHA256SUMS **2719431c…**, ceiling.md d6efad83…, ceiling.json 0ede273e…. Het rapport met de nits en de vastgepinde gap-regel: `ceiling-report-5d0bc80/` (SHA256SUMS: ceiling.json 6e5016e5…, ceiling.md 94a1fb95…; de json is gelijk aan die van de NAS op de gap-tekst na: 0 verschillen in de 112 cellen) |
| data | dev2 1033–1046 (verbruikt), slots 446.256.000–452.304.000, 20 UTC-dagen (12 sep – 1 okt); C3-sleutels `wallets/paidflow-dev-vps/markout-f7rev-txdelay.parquet` (sha256 1a274ec6…; 9.308 sleutels); live vergelijking (één live dag, 22 okt): een alleen-lezen snapshot van het paper-journal (sha256 **1691ddc9**7f31b24db15c817f0164facfe62b71cedb13863d5a45e27add3022ec, 23 okt 00:04Z) in `backtests/capital-ceiling-paper-2026-10-22/`, met `reading.py` → `reading.out` (SHA256SUMS: reading.py 0cd62a17…, reading.out 7de2e600…) |
| beschrijvend | toegevoegd na de run (§4.3 aanbod, §4.4 concentratie en helften): `backtests/capital-ceiling-d15ac45/e63/descriptives.py` → `descriptives.out` (SHA256SUMS: a6c25531… / 68eb2b01…) |

Labels: feit / reconstructie / modelaanname / onbekend. Elk rendement hieronder is een **reconstructie met het kostenmodel** (de engine vult in- en uitstap tegen de echte reserves op onze grootte, netwerkkosten scenario base) op **dev, vóór Alpenglow**. De kandidaten zijn alternatieven op dezelfde triggers: ze tellen niet op.

## Kort

- **Terugveer snel (K3)** heeft bij de primaire vertraging van 3 slots al bij 10 SOL geen voordeel: **−0,42 SOL/dag [−1,14; +0,88]**. Vanaf 25 SOL is de rekening binnen ongeveer 10 dagen leeg. Bij 2 slots is het +4,37 bij 10 SOL, maar vanaf 1 SOL per trade eet de prijsimpact het voordeel op.
- **Terugveer lang H5** (organisch, uitstap flow300_tp100_H6): **+5,6 / +13,5 / +37,1 SOL/dag** bij 10 / 25 / 100 SOL. De piek ligt bij 5 SOL per trade; bij 10 SOL per trade is de netto per trade negatief.
- **Koopstroom-arm C3** met de 5-minuten-uitstap hold300: **+7,9 / +16,3 / +37,2 SOL/dag**. Met de uitstap van Terugveer snel (tp4_T600) is de rekening bij elk kapitaal leeg.
- **20 SOL/dag haalt op dev alleen 100 SOL kapitaal, met H5 of C3/hold300.** Dat is een **bovengrens**, om vier redenen (§4):
  1. beide zijn op dev gekozen (in-sample);
  2. vanaf 1 SOL per trade kan vrijwel elke trade gesandwicht worden, en dat is niet gemodelleerd;
  3. het live aanbod van triggers is ongeveer een vijfde (alle) tot ruim een kwart (organisch) van dev;
  4. paper blokkeert groep H nu met vastgelopen posities.

  De eerste toets op ongeziene data komt zaterdag: H5 en E56 op de schone data, bij 0,5 SOL.

## 1. Hoe gerekend (plan §1–§4)

- **Limieten.** De risicoregels van paper (risk.yaml) zijn evenredig geschaald met kapitaal C:
  - grootte 5 % van C (0,5 / 1,25 / 5 SOL; gevoeligheid 10 %);
  - hoogstens 5 open posities, één per pool;
  - daglimiet verlies 10 % van C;
  - 24 u pauze per pool na een totaal verlies.
- **Simulatie.** De dag-reeks-tweeling van `analysis._capital_limited`, met één correctie uit de review: een instap die zal mislukken heeft óók het geld nodig. Zonder die correctie nam een lege rekening juist de instappen die mislukken, en dat weet je pas achteraf.
- **Vertraging en kosten.** d = 3 slots primair (≈ 800–950 ms op dev), d = 2 als gevoeligheid (paper's 600 ms-regel op dev). Kosten base; pessimistic als gevoeligheid.
- **Onverkochte posities.** Een positie in een leeggelopen pool komt vrij op de deadline van zijn regel. Gevoeligheden: nooit (zoals paper nu werkt) en bij instap.
- **Statistiek.** Dag-bootstrap over de 20 UTC-dagen (10.000, seed 7); een dag zonder trade telt 0.

## 2. Kopcijfers (d 3, base, 5 % per trade, vrij op de deadline)

| kandidaat | 10 SOL (0,5 per trade) | 25 SOL (1,25) | 100 SOL (5) | netto per gevulde trade bij 0,5 / 5 / 10 SOL | max nuttig kapitaal |
|---|---|---|---|---|---|
| Terugveer snel K3 (tp4_T600, alle pools) | −0,42 [−1,14; +0,88], 600 trades/dag, 1 van 20 dagen > 0 | leeg op dag 10 (eind 0,88 van 25) | leeg op dag 11 (eind 4,49 van 100) | −0,18 % / −4,24 % / −7,67 % | geen (bij geen grootte > 0) |
| Terugveer lang H5 (flow300_tp100_H6, organisch) | **+5,62 [+3,66; +7,55]**, 91/dag, 16/20 | **+13,47 [+8,59; +18,25]**, 91/dag | **+37,08 [+20,32; +53,89]**, 88/dag, slechtste dag −30,3, max DD 11 % | +10,40 % / +4,92 % / −1,23 % | ≥ 100 SOL (json: 200 = 10 SOL per trade / 5 %, de top van het raster); SOL/dag piekt bij 5 SOL per trade |
| C3 + tp4_T600 | leeg op dag 15 | leeg op dag 14 | leeg op dag 11 | −1,2 % (0,5) | geen |
| C3 + hold300 | **+7,87 [+4,37; +11,53]**, 108/dag, 14/20 | **+16,27 [+8,22; +25,13]** | **+37,16 [+12,43; +65,47]**, 91/dag, slechtste dag −19,8, max DD 22 % | +17,63 % / +10,82 % / +3,66 % | ≥ 100 SOL (json: 200, de top van het raster); piek bij 5 SOL per trade |

Nodig voor 20 SOL/dag: 200 % per dag van 10 SOL, 80 % van 25 SOL, 20 % van 100 SOL.

*Dev → live (één live dag, ter schaal):* live paper op 22 okt, de eerste hele dag na Alpenglow (exitdag UTC, triggers vanaf epoch 1064, snapshot 1691ddc9…, `reading.out`):
- Terugveer snel (K3, 0,5 SOL van 10) realiseerde **+1,06 SOL over 135 exits**, met 3 posities open in leeggelopen pools (gemarkeerd −1,50).
- De N2-vergelijkingsarm (een controle, geen strategie) −1,87 SOL over 317 exits.
- Groep H stapte niet in: 5 posities zitten sinds 21 okt vast (max open).
- Live F7-aanbod die dag: 398 triggers (180 organisch), tegen dev ~2.040 (~630) per dag.

Een dev-plafond is niet bereikbaar zonder meer.

## 3. Gevoeligheden (SOL/dag, d 3 base tenzij anders)

| | 10 SOL | 25 SOL | 100 SOL |
|---|---|---|---|
| H5, positie nooit vrij (paper zoals nu) | +1,79 (20 trades/dag) | +4,50 | +13,22 |
| H5, kosten pessimistic | +4,54 | +11,77 | +32,59 |
| H5, d 2 | +5,91 | +13,81 | +34,99 |
| H5, 10 % per trade (1 / 2,5 / 10 SOL) | +11,05 | +23,60 | +21,61 [−3,48; +49,17] |
| C3/hold300, nooit vrij | +2,57 | +6,10 | +16,72 |
| C3/hold300, pessimistic | +5,82 | +14,60 | +33,61 |
| C3/hold300, d 2 | +7,74 | +17,93 | +49,40 |
| C3/hold300, 10 % per trade | +4,02 | +3,21 | −2,21 |
| K3, d 2 | +4,37 [+1,04; +8,40] | leeg dag 10 | leeg dag 11 |

*Dev → live:* zie de regel onder §2. Zoals paper nu werkt (een vastgelopen positie komt nooit vrij) blijft van H5 ongeveer een derde over. De coördinator legt de eigenaar voor een positie af te schrijven op de deadline van haar regel.

## 4. Waarom dit een plafond is

1. **Sandwich: alleen gemarkeerd, niet gemodelleerd** (modelaanname). De vlag meet blootstelling (de trade is groot genoeg om te sandwichen), niet hoe vaak het echt gebeurt. Dat werkelijke percentage is **onbekend**; een eerste, niet vastgepinde blik van de coördinator op één dev-dag wijst eerder op enkele procenten dan op alle trades. Met een krappe slippagegrens worden de kosten dan vooral mislukte instappen in plaats van verlies.
   - De vlag van `analysis.f7_candidate` §4 (grootte > fee per kant × diepte, dekking 100 %) staat aan bij 55 % van de trades van H5 en C3/hold300 bij 0,5 SOL, bij 98–100 % vanaf 1 SOL, en bij 33 % / 99,6 % voor K3.
   - De engine rekent onze eigen impact exact tegen de reserves. Een sandwich, front-running of een reactie van anderen op onze grootte zit er niet in: de andere orders liggen vast.
   - In het slechtste geval kost een sandwich met 5 % slippagetolerantie per kant tot ongeveer 5 % van de inzet. Bij 5 SOL per trade is dat meer dan de netto marge van H5 (+4,9 %) en ongeveer de helft van C3/hold300 (+10,8 %).
   - De cijfers boven 10 SOL kapitaal zijn daarom een bovengrens.
2. **Op dev gekozen (in-sample).**
   - H5 (de grote take-profit, organisch; E31) en de C3-arm (E54/E56: "arm na het zien van dev gekozen") zijn gekozen nadat dev was bekeken.
   - Dev 1033–1046 is honderden keren bekeken (projectbrede teller > 1.300).
   - De eerste toets op ongeziene data is zaterdag: H5 (E31) en E56 (C3-markout) op schoon 1055–1063, bij 0,5 SOL.
3. **Live aanbod** (één live dag).
   - Op 22 okt zag paper 398 F7-triggers, waarvan 180 organisch (`reading.out` Q3).
   - Dev, de F7 pool_reversed-signaalset van de v4-run (`descriptives.out` §1), telde 40.737 / 12.641 over 20 dagen (~2.040 / ~630 per dag). Dat is ~20 % van alle en ~29 % van de organische. K3's eigen cel van deze run (37.831 / 12.055 rijen na de bezet-regel) geeft 21 % / 30 %.
   - H5 handelt alleen organische triggers. Schaalt het resultaat lineair mee (modelaanname), dan wordt +37 bij 100 SOL ongeveer +11.
   - De C3-arm heeft de betaalde koopstroom van de 60 s vóór de trigger nodig. Of de recorder die live ziet, is **onbekend**.
4. **Paper-regel en concentratie.**
   - Zoals paper nu werkt, blokkeren vastgelopen posities (zie §3).
   - De winst is breed (H5 bij 5 SOL: 1.960 pools; de top-10 pools 18 %, de top-1 % trades 28 %; C3/hold300: 23 % en 42 %; `descriptives.out` §2), maar zakt in de tweede helft van dev. Zonder limieten, per dag:

     | | dag 1–10 | dag 11–20 |
     |---|---|---|
     | H5 bij 5 SOL | +47,9 | +20,5 |
     | C3/hold300 bij 5 SOL | +67,9 | +56,5 |

     Dit is beschrijvend, toegevoegd na de run.
5. **Overig.**
   - d 3 op dev ≈ 800–950 ms; live 3 slots ≈ 640 ms.
   - De app-fee van 0,45 % sinds 17 okt zit niet in dev; of paper hem meerekent, is onbekend.
   - Geen prioriteitsfee die meeschaalt met de grootte.

## 5. Waar de impact het voordeel opeet

- **Terugveer snel K3** heeft bij d 3 bij geen enkele grootte voordeel. Bij d 2 is het weg vanaf 1 SOL per trade.
- **H5:**
  - De netto per trade daalt met de grootte: 10,4 % → 9,7 % (1,25) → 8,1 % (2,5) → 4,9 % (5) → −1,2 % (10 SOL).
  - SOL/dag met limieten piekt bij 5 SOL per trade (C 100).
  - Bij 10 SOL per trade (C 200) is het +17,5 [−9,8; +46,4], met een negatieve netto per trade.
  - Het nuttige maximum ligt dus rond 100 SOL.
- **C3/hold300** piekt ook bij 5 SOL per trade. Bij 10 SOL is de marge +3,7 % en met limieten +8,7 [−16,0; +38,9].

## 6. W8-B (migratie-opening + BOOST)

Geen engine-implementatie, dus geen groottecurve. Geciteerd uit E51 (probe, 0,25 SOL, lineaire impact): netto per pool d 2 +0,35 % [−0,20; +0,90], d 3 +0,05 % [−0,56; +0,64], 780 pools per dag. Het plafond boven 0,25 SOL is **onbekend**.

## 7. Wat dit niet is, en wat volgt

- Geen toets, niets naar paper: alles op verbruikte dev-data, verkennend. Het telt 112 cellen (4 kandidaten × 7 groottes × 2 d × 2 kostenscenario's), waarvan 56 C3-cellen op de teller van E56.
- **Volgt:**
  - zaterdag de schone uitslag van H5 en E56: die beslist of H5/C3 überhaupt verder gaan;
  - de beslissing van de eigenaar over vastgelopen posities in paper, want groep H staat stil;
  - pas daarna, en alleen als het iets oplevert, een sandwich-bewust model voor groottes ≥ 1 SOL.
