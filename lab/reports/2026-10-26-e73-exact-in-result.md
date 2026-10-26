# E73 — vaste walletbreedteproef na PumpSwap exact-in-correctie (26 oktober 2026)

**Oordeel: STOP voor de vooraf gekozen primaire regel `k2n30` op 10 SOL modelkapitaal.** Twee verschillende vooraf geselecteerde walletadressen die binnen 30 seconden dezelfde markt kopen, leverden op deze bekeken DEVELOPMENT-data geen positief rendement na de in de replay gemodelleerde kosten op. De gecorrigeerde marge is −19,99%; het 95%-dagbootstrapinterval is [−24,69%, −14,57%]. Dit is geen toets op een onaangeroerde periode en geen bewijs over uitvoerbare live fills.

## Vaste proef en bronbinding

- Plan v0.2: `data-old-faithful-one/lab/research/breedte-proeven-plan-2026-10-24.md`, SHA-256 `7bbd1aa112bdad00bba8ff504749d6bb04cf1af99b4ee9fd3ae9d70b590490b1`; primaire cel `k2n30`, secundair `k3n30`, `k2n10`, `k2n120`.
- DEVELOPMENT-epochs 1033–1046: alle veertien vensters, elk op 0,5 / 1,25 / 5 SOL. De vooraf vastgelegde dagbasis is 19 september–1 oktober 2026, dertien UTC-dagen met een beschikbare, uitsluitend uit voorafgaande dagen gekozen goede-walletset. Lege vroege vensters blijven in de uitvoer. Geen holdout is gelezen.
- Instap vanaf drie slots na het signaal; 0,5 SOL per positie, 10 SOL modelkapitaal en maximaal vijf open posities; vaste trail30/stop50/H6-uitstap met uitstapvertraging. De SQL-correctie voor bronbewezen PumpSwap zero-cashback exact-in en de correctie van twee geordende walletkopen binnen één slot zitten samen in commit `aa1318bf020efb82eb62ba8121600e186aa17b87` (PR #596). Bonding-curve- en onbewezen cashbackregels bleven ongewijzigd.
- NAS-bronstore `dev2.duckdb`: 42.334.171.136 bytes, SHA-256 `15b03bbec103dfc218012c4ca80b70e7fc2b0fc5bf332c434e8fc4ffea4be354`. Vaste kostconfiguratie `week2-defaults.yaml`: SHA-256 `1e8aedddd2a179a6131e8487a986e82877d98a58bf00bab2e7ae3d4c58d89243`.
- Nieuwe uitvoer: `lab/wallets/probes-e73-aa1318b/` op de NAS, rapport-SHA-256 `6c3109e4aab9b79ff811136cf032c0d28d613a775f34204ef9c9dae9807c4d1a`, `SHA256SUMS`-SHA-256 `482d5f4cf026f667ed174753c37adfce4749e4a9da5a08bb00a9c04e0eb8d15a`. Alle 42 runreceipts, Parquet-rijtellingen en 131 bestandschecksums zijn gecontroleerd; de run en de aparte read-only NAS-verificatie eindigden beide met code 0. De reproduceerbare voor/na-vergelijking staat buiten Git in `lab/research/e73-exact-in-completion-20261026/comparison.json` (SHA-256 `5e7ea4dadb0fb9309dd14cb90640c94564ea036116ea528f35abb2434244c604`).
- Bewaarde oude SQL-uitvoer: `lab/wallets/probes-e73-6a4458f/`, rapport-SHA-256 `ae60278e830030032ef2a091b31a6958d13185eb50578318e9f46b6e8dfd3055`. Zij is uitsluitend historische referentie en is niet overschreven.

## Primaire `k2n30`-cel, 0,5 SOL per positie / 10 SOL kapitaal

| Maatstaf | Oude SQL-referentie | Gecorrigeerde volledige run | Verschil nieuw − oud |
|---|---:|---:|---:|
| Ruwe triggers na cooldown (vensterlogs) | 144.936 | 166.952 | +22.016 |
| Vulbare signalen (rapport/Parquet) | 142.213 | 164.124 | +21.911 |
| Ingenomen posities, vijf plaatsen | 976 | 901 | −75 |
| Vulbaar maar wegens capaciteit overgeslagen | 141.237 | 163.223 | +21.986 |
| Totale instapuitgave, incl. gemodelleerde instapkosten (SOL) | 488,688080 | 451,135205 | −37,552875 |
| Netto na gemodelleerde instap-/uitstapkosten en prijsimpact (SOL) | −100,203846280 | −90,183238252 | +10,020608028 |
| Nettomarge = netto / instapuitgave | −20,50% | −19,99% | +0,51 procentpunt |
| 95%-dagbootstrap op vaste dertien dagen | [−24,91%, −16,04%] | [−24,69%, −14,57%] | beide geheel negatief |
| Ingenomen posities per onderzoeksdag | 75,08 | 69,31 | −5,77 |
| Posities met minstens 90% verlies | 144/976 (14,75%) | 128/901 (14,21%) | −16 posities |

`Totale instapuitgave` is het modelkapitaal dat voor de ingenomen posities werd uitgegeven, **niet** een afzonderlijk opgeteld bedrag aan alleen fees. De vaste base-configuratie rekent per instap en uitstap 0,000705 SOL netwerkfee plus tip; voor 901 ingenomen posities is dat samen 1,270410 SOL. De venue-fees en eigen prijsimpact zitten eveneens in de replaybedragen, maar het E73-rapport splitst ze niet als zelfstandige totaalkolommen uit. Mislukte landing en werkelijke latency zijn niet waargenomen en kunnen daarom niet als gerealiseerde kosten worden geclaimd.

Het grotere aantal ruwe triggers is mede te verwachten door de correcte telling van twee verschillend geordende wallets binnen één slot; de lagere capaciteitsopname volgt uit de nieuwe signaal-/positievolgorde. De nettoverbetering van 10,02 SOL is het **gezamenlijke effect van de exact-in- en same-slot-correcties** in deze run. Zonder afzonderlijke, vooraf gespecificeerde ablatierun kan zij niet aan één wijziging worden toegeschreven. Het oude resultaat was al negatief; de correctie verandert het STOP-oordeel niet.

## Dagverloop en concentratie

| UTC-dag | Oud netto SOL | Nieuw netto SOL |
|---|---:|---:|
| 19 sep | −10,693819496 | −6,708879335 |
| 20 sep | −6,310832555 | −6,574495609 |
| 21 sep | −8,637450655 | −9,437076635 |
| 22 sep | −19,079817780 | −14,787391978 |
| 23 sep | −7,634983519 | −9,076274415 |
| 24 sep | −9,343060117 | −4,494117947 |
| 25 sep | −7,025458922 | −8,407624574 |
| 26 sep | −6,760089522 | −6,066194361 |
| 27 sep | −7,538940988 | −8,734827594 |
| 28 sep | −2,358713636 | −5,843627939 |
| 29 sep | −8,898144084 | −9,687068847 |
| 30 sep | −1,045188479 | +2,088775158 |
| 1 okt | −4,877346527 | −2,454434176 |

De nieuwe run verliest op twaalf van de dertien dagen. De slechtste dag, 22 september, draagt 14,787 SOL verlies: 16,0% van het bruto verlies op negatieve dagen (oud 19,0%). De drie slechtste dagen dragen samen 36,8% (oud 39,0%). Het verlies is dus niet uitsluitend één pool of één dag; het rapport biedt geen betrouwbare identificatie van economisch onafhankelijke handelaren achter de walletadressen.

De secundaire cellen blijven beschrijvend. Bij 10 SOL kapitaal zijn hun gecorrigeerde marges `k2n10` −18,13% (95% [−21,79%, −13,35%]), `k2n120` −19,15% ([−22,11%, −16,08%]) en `k3n30` −17,12% ([−20,15%, −14,16%]); de 25- en 100-SOL-cellen zijn eveneens negatief. Geen secundaire cel vervangt het vooraf gekozen primaire STOP-oordeel.

**Beperking:** de vastgepinde bronregressies voor PumpSwap exact-in komen uit epoch 1064; E73 gebruikt oudere DEVELOPMENT-epochs 1033–1046. De regressies bewijzen de rekenregel voor hun onderzochte bronversie, maar niet zelfstandig dat diezelfde programmaversie in alle E73-slots actief was. Dit zijn simulatieresultaten op al bekeken DEVELOPMENT-data, met historische reserve-/marktoestanden, veronderstelde d=3-slotinstap, gesimuleerde landing en één vaste exitregel. Verschillende adressen zijn niet noodzakelijk onafhankelijke personen. Positieve historische walletkasstromen bewijzen geen verhandelbare winst. Deze negatieve primaire proef is afgerond; nieuw parameterzoeken of een late secundaire promotie zou een ander experiment zijn. K3, de paperdienst en de formele meetklok blijven ongewijzigd.
