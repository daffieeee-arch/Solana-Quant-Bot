# M-a: instap per transactie binnen het slot — de volle run (99zx): screen positief, +0,9 pt per trade bij d0, +0,6 pt bij de eerste transactie van t + 1

LAB research, 2026-10-19. Spec `research/spec-rust-ma-instap-per-transactie-2026-10-19.md` v0.4 (sha256 707d7f7e…). Vraag: hoeveel instapvoordeel per F7-trigger (reversed pools: K3 en de H-armen delen de trigger) geeft een instap vóór het slot-einde + d = 2 (de paper-`d_ms 600` in de dev-regimes), als bovengrens voor "sneller instappen" in Rust (`lab/docs/rust-advantages-2026-10.md` §2.1). Alleen dev 1033–1046 (verbruikte data; gevoeligheidsmeting, geen claim). NAS-job 99zx op main 21711ea (`lab/wallets/nas_markout.sh`, 6 workers, BOOT 10.000): `wallets/markout-v1-txdelay-v4/markout.md` (sha256 4226f6b7…), `run-markout-f7rev-txdelay.json` (sha256 3884b9f0…), parquet 167 MB op de NAS. Register E42. Labels **feit** / **modelaanname** / **onbekend**.

## 0. Samenvatting

- **Screen (spec §5) positief op alle zes cellen, in beide rijen** (feit op dev; de organic-rij is een deelverzameling van alle pools, 9.426 van 26.309 paren, dus één populatie twee keer gelezen, geen onafhankelijke bevestiging): dagen 10–18, causale instappen d0 (slot-einde t = een fill als eerste transactie van t + 1) en n1 (ná de eerste transactie van t + 1), h = 1 m / 5 m / 15 m, netto-Δ ≥ 0,2 pt per trade met dag-CI boven 0. K3-rij (alle reversed pools, 26.309 paren): d0 **+0,91 / +0,87 / +0,80 pt**, n1 **+0,56 / +0,55 / +0,47 pt**. K1/K2-rij (organic, 9.426 paren): d0 **+1,18 / +1,12 / +1,06 pt**, n1 **+0,83 / +0,80 / +0,71 pt**. De ondergrens van elke dag-CI ligt ≥ +0,38 pt.
- **Per slot ≈ 0,4–0,5 pt** (feit, dev): bij 1 m is de netto-Δ tegen d2 +0,91 (d0), +0,40 (d1), 0 (d2), −0,29 (d3) voor alle pools; organic +1,18 / +0,59 / 0 / −0,37. Dat is dezelfde orde als de hold-out-diagnose van 12 okt (≈ 0,45 pt per slot, §2.1 van de Rust-notitie), nu met CI's boven 0 omdat het gepaard per signaal is.
- **Binnen het triggerslot zit niets extra** (feit, dev): k0 (direct na de trigger-transactie, een same-slot fill via een bundel) geeft +0,88 pt bij 1 m, niet meer dan d0 (+0,91). Het hele voordeel is het vermijden van de transacties in t + 1 en t + 2; een bundel is er niet voor nodig.
- **Wat het niet zegt:** geen P&L (exit, busy en fill worden niet per instap herrekend; dat is route (ii)); geen latency; een bovengrens zonder concurrentie (wie sneller instapt zit in dezelfde slots als de andere snelle kopers: onbekend, alleen live te zien), zonder TOL en zonder landingskans; dev is verbruikte data.
- **Beslisregel:** de screen is gehaald, dus volgens spec §5 gaat M-a door naar route (ii) (M1-kern: exit en busy per instap). Een positieve screen is geen claim; de eerste stap is een spec, geen bouw (§4 van deze noot).

## 1. Netto-Δ tegen d = 2, dagen 10–18 (gepaard per signaal, 1 %-getrimd, dag-bootstrap 10.000×, 0,5 SOL, base-kosten)

Alle pools (F7 pool_reversed = de K3-rij; de K3-sectie van het rapport is identiek aan de F7-sectie, zelfde where):

| instap | afgekapt | instap-mid Δ | netto-Δ 1 m | CI | netto-Δ 5 m | CI | netto-Δ 15 m | CI | Δ > 0 | zelfde toestand | screen |
|---|---|---|---|---|---|---|---|---|---|---|---|
| d0 (slot-einde t) | 0 % | +0,88 % | **+0,91** | +0,80 … +1,00 | **+0,87** | +0,73 … +1,00 | **+0,80** | +0,67 … +0,90 | 51 % | 31 % | ja |
| n1 (ná eerste tx t + 1) | 50 % | +0,54 % | **+0,56** | +0,47 … +0,63 | **+0,55** | +0,44 … +0,65 | **+0,47** | +0,38 … +0,55 | 40 % | 50 % | ja |
| d1 | 0 % | +0,39 % | +0,40 | +0,34 … +0,45 | +0,39 | +0,31 … +0,45 | +0,33 | +0,26 … +0,38 | 36 % | 56 % | – |
| d3 | 0 % | −0,28 % | −0,29 | −0,36 … −0,20 | −0,34 | −0,43 … −0,22 | −0,32 | −0,40 … −0,22 | 27 % | 58 % | – |
| k0 (na trigger-tx, bundel) | 0 % | +0,85 % | +0,88 | +0,72 … +0,99 | +0,82 | +0,63 … +0,96 | +0,75 | +0,62 … +0,84 | 55 % | 21 % | bovengrens |
| k1 | 63 % | +0,87 % | +0,90 | +0,78 … +1,00 | +0,85 | +0,70 … +0,99 | +0,78 | +0,67 … +0,88 | 52 % | 28 % | bovengrens |

Organic (K1/K2-rij, 9.426 paren):

| instap | afgekapt | instap-mid Δ | netto-Δ 1 m | CI | netto-Δ 5 m | CI | netto-Δ 15 m | CI | Δ > 0 | zelfde toestand | screen |
|---|---|---|---|---|---|---|---|---|---|---|---|
| d0 | 0 % | +1,09 % | **+1,18** | +1,07 … +1,28 | **+1,12** | +0,95 … +1,27 | **+1,06** | +0,91 … +1,21 | 57 % | 17 % | ja |
| n1 | 44 % | +0,74 % | **+0,83** | +0,72 … +0,95 | **+0,80** | +0,66 … +0,91 | **+0,71** | +0,60 … +0,81 | 47 % | 43 % | ja |
| d1 | 0 % | +0,50 % | +0,59 | +0,50 … +0,68 | +0,51 | +0,39 … +0,60 | +0,44 | +0,36 … +0,51 | 42 % | 49 % | – |
| d3 | 0 % | −0,35 % | −0,37 | −0,47 … −0,23 | −0,48 | −0,63 … −0,31 | −0,42 | −0,53 … −0,28 | 29 % | 53 % | – |
| k0 | 0 % | +1,00 % | +1,11 | +0,92 … +1,25 | +1,00 | +0,74 … +1,20 | +0,92 | +0,67 … +1,13 | 60 % | 10 % | bovengrens |

Lezing (feit, dev):
- "Zelfde toestand" = de pool handelde niet tussen de instap en de referentie; die paren zijn de fee-vrije nul. Bij d0 is dat 31 % (organic 17 %), bij n1 50 % (43 %): het gemiddelde voordeel komt uit de andere helft, waar de koers tussen t en t + 2 stijgt.
- n1 is voor 50 % afgekapt (geen transactie van de pool in t + 1; de instap valt dan op het slot-einde van t = d0). Op de 13.023 signalen die wél een transactie in t + 1 hadden is de netto-Δ +0,71 / +0,75 / +0,64 pt (organic 5.321: +1,04 / +1,06 / +0,92); dat is een selectie op pools die handelden, geen zuiverder schatting.
- De k-codes voegen niets toe boven d0: k0 +0,88, k1 +0,90 (63 % afgekapt op de laatste leg van t), k3 +0,92 (90 % afgekapt). De afgekapte k-instappen zijn per definitie d0, en de niet-afgekapte (k1 ex afgekapt +0,90 op 9.827) liggen op dezelfde hoogte.
- De absolute niveaus (K3-rij, netto met dode rijen, 1 m): d2 +1,2 % [+0,7; +1,6], n1 +1,8 % [+1,2; +2,2], d0 +2,1 % [+1,5; +2,5]; organic d2 +1,1 %, n1 +2,0 %, d0 +2,3 %. Bij 15 m ligt de dag-CI van elk absoluut niveau om 0 (30 % dode rijen); het gepaarde verschil is wel scherp.

## 2. Consistentie over blokken en regimes (feit, dev)

| blok | d0 1 m | d0 5 m | d0 15 m | n1 1 m | n1 5 m | n1 15 m |
|---|---|---|---|---|---|---|
| dagen 1–9 (13.991 paren) | +1,15 [+0,98; +1,39] | +1,17 | +1,06 | +0,72 [+0,56; +0,87] | +0,71 | +0,65 |
| dagen 10–18 (26.309) | +0,91 [+0,80; +1,00] | +0,87 | +0,80 | +0,56 [+0,47; +0,63] | +0,55 | +0,47 |
| alle dagen (40.737) | +1,00 [+0,89; +1,10] | +0,97 | +0,89 | +0,61 [+0,53; +0,69] | +0,61 | +0,53 |
| 270 ms (32.903) | +0,95 | +0,92 | +0,86 | +0,59 | +0,59 | +0,52 |
| 320 ms (7.834) | +1,18 | +1,20 | +1,00 | +0,71 | +0,69 | +0,60 |

Alle cellen hebben een dag-CI boven 0 (organic idem, dagen 1–9 d0 +1,48, n1 +1,13). Dagen 1–9 liggen hoger dan 10–18; de richting en de orde zijn gelijk.

## 3. Tellingen (`tx_stats`, feit)

- Pool-signalen 40.737 (alle met een leg in t); trigger-wallet met meer dan één leg in t: 967 (2,4 %); kleinere verkoop van de trigger-wallet vóór de trigger-leg in t: 585; creator-verkoop ná de trigger in t: 0 (het veto van de slotregel verliest niets bij een per-transactie-beslissing).
- Afgekapt: n1 20.640 (50,7 %), k1 25.748 (63 %), k3 36.598 (90 %), k10 40.511 (99 %); k0 0.
- Transactiegrens (v0.4, Codex-punt 1): instappen die naar het einde van hun transactie schoven: n1 1.894 (4,6 % van de signalen, 9,4 % van de niet-afgekapte n1), k1 2.489, k3 889, k10 14, k0 0. Dat zijn de round-trip-transacties [token-koop, token-verkoop]; v0.3 had ze tussen de legs, aan de dure kant. Smoke-dag 2026-09-24: n1 netto-Δ 1 m +0,22 → +0,59 pt door die grens; de volle run bevestigt de richting (+0,56).
- Instap-impact > 5 % bij 0,5 SOL: 0,0 % van de instappen.

## 4. Wat het wel en niet zegt, en de volgende stap

- **Wel (feit, dev):** per F7-trigger is de instapprijs bij een fill als eerste transactie van t + 1 gemiddeld 0,9 % lager dan bij d = 2 (organic 1,1 %), en dat vertaalt zich één op één in de netto-markout tot 15 m. De curve is bij benadering lineair in slots (≈ 0,4–0,5 pt per slot). Een same-slot fill (bundel) voegt niets toe.
- **Niet:** het is geen P&L van K3 of de H-armen (de tp/sl-exit is padafhankelijk, busy en de fill worden niet per instap herrekend), geen haalbare latency (de afstand trigger → beslissing → landing is live te meten: M3-shadow, M-e), geen landingskans en geen TOL. **Bovengrens zonder concurrentie** (modelaanname): in de store staat onze order niet; wie als eerste in t + 1 wil landen concurreert met de kopers die in t + 1 al staan, en hun gedrag verschuift als wij meedoen (onbekend). Dev is verbruikte data: de getallen zijn een gevoeligheidsmeting, geen claim; een claim komt pas forward (≥ 1064) met pre-registratie en DSR.
- **Beslisregel (spec §5):** de screen is gehaald → route (ii). **Eerste stap: een spec v0.1 voor route (ii)**, geen bouw: K3 (flagged, de paper-configuratie) en H4/H5 (organic) op dev met de instap op d0 en n1 náást d = 2, exit, busy en fill per instap herrekend door de M1-kern (M4-light: de kern vanuit Python, C2-pariteit is er), uitkomst als Σ netto per dag en netto per trade met dag-CI, als NAS-job; pre-registratie van de forward-toets (d-rij in de paper-rapporten) erbij. Niets verandert aan K3/H4/H5 of de paper (lopend vanaf 20 okt) zonder spec + bouw + eigenaarsbesluit. De M-a-code (`wallets/markout.py --tx-delays`) blijft het meetinstrument voor de instapcurve na Alpenglow (M-a-herhaling forward).
