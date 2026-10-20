# F7-kostenkloof ontleed: waar gaan de ~13 punten heen? (dev, E48)

**Vraag (coördinator 2026-10-21, "edge first"):** op dev is de F7-markout op 5 m +8,8 lp bruto (E45-dev, pooleenheid) maar netto −4,4 pt. Splits het verschil per pool in (a) prijsbeweging tussen trigger en onze fill, (b) eigen AMM-impact in en uit, (c) protocol/LP-fees, (d) prioriteitsfee + tip, (e) mislukte instappen en hun selectie-effect, plus: hoeveel neemt een all-or-nothing bundle-guard weg? Dat bepaalt welke Triton-dienst het getal kan bewegen.

**Antwoord in één zin:** (a)–(d) zijn samen 3,4 pt; de kloof is de rug. In 13,5 % van de triggers (29 % van de pools) kan de pool op 5 m niets meer uitbetalen (LP weg, mediane mid −80 %), en dat kost 29 pt per pool. Geen snelheidsdienst, geen kleinere maat en geen bundle-guard raakt dat.

Labels: **feit** = berekend op de genoemde data; **reconstructie** = afgeleid uit een andere grootheid; **modelaanname** = aanname van het rekenmodel; **onbekend** = niet gemeten.

## 1. Data en eenheid

| | |
|---|---|
| triggers | 40.737 F7 pool_reversed, 3.341 pools, 20 dagen (dev 1033–1046; verbruikte data) |
| markouts | NAS-run `markout-v1-txdelay-v4` (E42; `markout-f7rev-txdelay.parquet` sha256 256ac0da…, gesynct 2026-10-21): mid, bruto, netto en instap-impact per trigger voor d = 0/1/2/3 slots, "t, trigger-tx + 0" (= de trigger-tx-prijs) en "t+1, eerste tx", h = 1 m / 5 m; 0,5 SOL; netwerkkosten 720.618 lamports per order (scenario base) |
| fee-tier en marktfees | de legs van de triggerslots uit de dev-store (`wallets/explore/costgap_legs.py`, 130.433 legs): alle 40.737 triggers op de 30 bps-tier (lp 20 / protocol 5 / creator 5 of lp 25 / protocol 5) |
| mislukte instappen | route-(ii)-rijen R · tp4_T600 (`backtests/route-ii-full-547f422/entry-rows`, E46): 2.838 van 38.114 K3-triggers `entry_failed` (engine-regel: tokens op d < 0,95 × verwacht op het slot-einde van t) |
| eenheid | als E45: gemiddelde per pool over haar triggers, 0,5 %-getrimd gemiddelde over pools, 95 %-interval uit een pool-bootstrap (10.000 trekkingen, seed 7); punten van de inzet (0,5 SOL), niet log, zodat een totaal verlies −100 pt is en niet −690 lp |
| code | `wallets/explore/costgap_points.py` (waterval, groottes, mislukte instappen, guard; json sha256 336da29e…), `costgap_live.py` (live/wiped-splitsing, P1-controle, dieptebanden; json 518e893a…) |

De waterval is per trigger exact: mechanisme − vertraging − fee in − impact in − fee uit − impact uit − netwerk = netto (max. afwijking 6e-8). Mechanisme = mid op h t.o.v. de trigger-tx-prijs; vertraging = wat onze instap-mid duurder is dan die prijs; impact in = prijs per token boven de mid min de fee (reconstructie: de fee zit in de tokens die de pool geeft); impact uit = de rest na fee uit en netwerk (bevat ook het geval "pool kan niet betalen").

## 2. De waterval (d = 2 slots = de 600 ms van het papier, h = 5 m)

| term per pool | 0,5 SOL | label |
|---|---|---|
| mechanisme (mid 5 m t.o.v. trigger-tx-prijs), **live** pools (op 5 m nog betaalbaar; 86,5 % van de triggers) | **+38,6** [+36,0; +41,5] | feit |
| (a) vertraging: instap op d 2 duurder dan de trigger-tx-prijs | **−1,26** [−1,08; −1,43] | feit |
| (c) fee in / fee uit (30 bps) | −0,41 / −0,41 | feit |
| (b) eigen impact in / uit (0,5 SOL) | −0,41 / −0,52 | reconstructie |
| (d) netwerk 2 × 720.618 lamports | −0,39 | feit (scenario) |
| = netto, live pools | **+34,4** [+32,0; +37,0] | feit |
| **wiped**: pool betaalt op 5 m niets (verkoop geeft 0; mediane mid −80 %); 13,5 % van de triggers = **29,1 %** [27,7; 30,5] poolaandeel | **−29,1** [−30,5; −27,7] | feit |
| = netto, alle triggers | **−4,38** [−7,04; −1,82] (E45-dev gereproduceerd) | feit |

Per trigger (niet per pool): live +23,4 %, wiped 13,5 % → alle triggers +6,75 % (de pooleenheid weegt de kleine, vaak gerugde pools zwaarder; zie E47 §4).

**(a) de vertragingsladder** (instap-mid t.o.v. de trigger-tx-prijs, per pool, 5 m): d 0 (slot-einde t) +0,42 *goedkoper*, d 1 −0,52, d 2 −1,26, d 3 −1,60, "t+1 eerste tx" −0,10. Netto alle triggers: d 0 −3,24, d 1 −3,87, d 2 −4,38, d 3 −4,67, t+1 −3,57. De prijs daalt binnen slot t na de trigger nog door; van d 2 naar d 0 is hooguit 1,7 pt te winnen (naar "t+1 eerste tx" 1,2 pt). Op 1 m hetzelfde beeld (d 2 −0,93).

**(b) groottes** (reconstructie: SOL-reserve b bij instap uit impact en fee, exit-impact lineair geschaald = modelaanname): netto alle triggers 0,1 SOL −4,96 [−7,60; −2,40], 0,25 SOL −4,33, 0,5 SOL −4,38. Kleiner helpt niet: de netwerkkosten wegen 1,44 % bij 0,1 SOL en de wipe is grootte-onafhankelijk. Live is de hele impact in + uit 0,9 pt.

**(d) wat de markt betaalt** in de triggerslots (feit, 130.433 legs): mediane prioriteitsfee 0 lamports (p90 100), tx_fee mediaan 5.000, 1,9 % van de legs met een Jito-tip. Ons scenario (605.000 + 100.000 + mislukte-tx-aandeel per order) is conservatief; (d) kan het getal niet bewegen.

**Wipes per horizon:** op 1 m is 3,0 % van de triggers (9,5 % poolaandeel) al wiped, op 5 m 13,5 % (29,1 %). Netto alle triggers op 1 m −4,72 [−5,98; −3,46] (live +5,70, wiped −9,50): de rug valt meestal tussen 1 en 5 min na de trigger, en op 1 m is de terugveer nog klein.

## 3. (e) Mislukte instappen en de bundle-guard

| set (K3-triggers, d 2, 5 m) | pools | netto per pool |
|---|---|---|
| gevuld in de engine | 3.274 | −4,32 [−6,89; −1,81] |
| mislukt in de engine (7,45 %), als ze op d 2 wél gevuld waren | 1.141 | **+9,55** [+4,50; +14,48] |
| alle K3-triggers | 3.341 | −4,40 [−7,04; −1,86] |

De mislukte instappen zijn de triggers waarvan de prijs op d 2 al ≥ 5 % boven het slot-einde van t stond (gemiddeld +6,9 %): de terugveer was al begonnen. Ze missen is een gemiste kans, geen kost (feit; het rechtstreekse verlies is 505.000 lamports per mislukking = 0,1 pt).

**Bundle-guard** "instap keert terug als de fill meer dan X % boven de referentie ligt; de mislukte poging kost alleen de tip (0,0001 SOL)":

| X | referentie | overgehouden triggers | netto overgehouden set (per pool, 5 m) | revert-kost |
|---|---|---|---|---|
| 0,5 % | trigger-tx-prijs | 69,9 % | −2,94 | 0,006 pt |
| 1 % | trigger-tx-prijs | 73,4 % | −3,27 [−5,98; −0,63] | 0,005 pt |
| 2 % | trigger-tx-prijs | 78,7 % | −4,01 | 0,004 pt |
| 5 % | trigger-tx-prijs | 88,6 % | −4,07 [−6,63; −1,46] | 0,002 pt |
| 1 % | slot-einde t | 76,0 % | −3,42 [−6,14; −0,71] | 0,005 pt |
| 5 % | slot-einde t | 89,6 % | −3,92 [−6,59; −1,29] | 0,002 pt |

Op d 3 (Alpenglow-proxy) hetzelfde (−3,0 … −4,7). De guard neemt hooguit 1,4 pt van de kloof weg, gooit 10–30 % van de triggers weg en raakt de wipe niet: die komt ná de instap.

## 4. Dieptebanden bij instap (idee 1 van de coördinator, dev, achteraf gekozen)

SOL-reserve b bij onze instap (d 2; reconstructie uit impact en fee; kwantielen 10/25/50/75/90 %: 148 / 156 / 172 / 198 / 238 SOL; de trigger eist ≥ 150 SOL vóór de triggerverkoop):

| b bij instap | pools | triggers | netto 0,5 SOL | netto 0,1 SOL | wiped-aandeel | live-markout (lp) | dag 1–9 | dag 10–18 |
|---|---|---|---|---|---|---|---|---|
| < 100 | 47 | 50 | +266 [+18; +594] | +481 | 48 % | +56 | +575 | +17 |
| **100–150** | **2.113** | **5.770** | **+8,28 [+4,64; +11,83]** | +7,72 | 24,8 % | +22,9 | +6,9 [+0,4; +13,2] | +9,8 [+5,4; +14,2] |
| 150–200 | 2.649 | 25.155 | +1,54 [−1,32; +4,46] | +0,93 | 25,5 % | +18,8 | +1,9 | +1,8 |
| 200–300 | 961 | 9.626 | −3,89 [−7,88; −0,05] | −4,61 | 19,6 % | +9,2 | −4,3 | −3,8 |
| 300–500 | 70 | 95 | −28 [−46; −11] | −29 | 36 % | −1,3 | −48 | −3,9 |
| ≥ 500 | 40 | 41 | −73 [−89; −55] | −73 | 50 % | −67 | −69 | −86 |

Lezing: de band 100–150 (de triggerverkoop en wat erna kwam drukten de pool onder de 150-drempel; 14 % van de triggers) heeft hetzelfde wipe-aandeel als 150–200 maar een grotere terugveer in de live pools (+22,9 tegen +18,8 lp): een grotere schok, een grotere terugveer. Beide dagblokken positief. Dit is **geen claim**: de band is na het zien van dev gekozen, het is de verbruikte data, en de keuze telt mee in het projectbrede aantal proeven. Schaal (modelaanname): 290 triggers/dag × 0,5 SOL × +8 pt ≈ 10 SOL/dag op papier, vóór capaciteit en busy. Weg: pre-registratie op clean 1055–1063 (na E20/H en E45) en forward ≥ 1064, zie E49 zodra die vastligt.

## 5. Controle van E45's P1

P1 (log-markout, floor ln 0,001) wordt niet opgeblazen door de betekenisloze mids van lege pools: op d 2 / 5 m is P1 +8,77 lp [+5,45; +12,22] = live-bijdrage +15,69 [+14,47; +16,87] + wiped-bijdrage −6,87 [−9,87; −3,77]. De pre-registratie hoeft niet aangepast. Wel is de **netto-maat** (secundair "verhandelbaar") de enige die de wipe volledig telt; P1 meet het mechanisme in de mids, niet wat de pool kan betalen.

## 6. Wat dit niet zegt

- Niets over clean of forward; alles hier is dev 1033–1046 en pre-Alpenglow (218–400 ms-slots).
- "Wiped" is "onze verkoop van 0,5 SOL aan tokens levert op h niets op" (reserves weg of kleiner dan onze positie); of de LP-withdraw de oorzaak is, is per trigger niet nagekeken (E36 beschrijft de LP-lots van reversed pools; onbekend hoeveel van de 13,5 % daar precies op valt).
- De exit-impact bij 0,1 en 0,25 SOL is lineair geschaald uit de 0,5 SOL-run (modelaanname; conservatief voor kleine maten).
- Fee-tier van clean pools: onbekend; op dev één tier (30 bps).

## 7. Gevolg

1. Geen Deshred/Riptide-ladder en geen bundle-guard voor F7 op grond van dev: samen hooguit 1,7 + 1,4 pt tegen een gat van 29.
2. Goedkope dev-toets (verkennend): slippage-tolerantie 15 % in plaats van 5 % (sweep [0,02; 0,15] staat in `week2-defaults.yaml`), omdat de engine nu de teruggeveerde triggers wegselecteert.
3. De hefboom is het wipe-aandeel (point-in-time rug-filter op E41/E36-klassen; bij +34 live is 0,5 SOL positief zodra het wipe-poolaandeel onder ~20 % komt) en de schokgrootte (band 100–150). Beide alleen via pre-registratie.
