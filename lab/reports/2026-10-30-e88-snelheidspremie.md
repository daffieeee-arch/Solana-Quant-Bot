# E88 — snelheidspremie in de volgfamilie: netto SOL/dag bij vertraging d 0 / 1 / 2 / 3 (verkend 1064–1068, 30 okt 2026)

**Status: gelezen 30 okt 2026, lezing A (plan v0.4 §5): snelheid koopt niets in deze familie; de volg-/sniperfamilie blijft op verkende data gesloten. Verkend, in-sample (de 36 leiders zijn op deze dagen gekozen, E81 §11), 1 blik, geen oordeel, geen kandidaat, geen winstclaim. d 0/d 1 is op onze confirmed-stroom onbereikbaar (E67): dit meet de waarde van snelheid, geen strategie.**

## 0. Kort
- **Vraag (eigenaar via de coördinator, 30 okt):** wat zou sneller zijn ons opleveren in de enige familie waar wij winnaars kennen? Dezelfde volgregel als E82/E83 op dezelfde 11.251 signalen van 36 vaste wallets, met vertraging d 0 (eindstand van het leiderslot), 1, 2 en 3 slots, bij 0,05 en 0,5 SOL, na eigen impact en alle kosten (scenario base).
- **Feit (.out 732467e4…):** elke cel verliest, en de volger verliest **méér** naarmate hij sneller is. W_half, netto SOL per UTC-dag (6 dagen, dag-bootstrap [2,5 %; 97,5 %]): bij 0,05 SOL d 0 −7,877 [−9,618; −5,441], d 1 −7,785 [−9,463; −5,376], d 2 −7,091 [−8,600; −4,894], d 3 −6,575 [−7,836; −4,547] (= E83-klein); bij 0,5 SOL d 0 −65,724 [−80,775; −44,569], d 1 −64,874 [−79,353; −44,581], d 2 −58,888 [−72,454; −40,343], d 3 −54,054 [−64,705; −37,131] (= E82 r4). Gepaard per dag (P1, zonder annulering): d 0 − d 2 bij 0,05 −0,786 [−1,044; −0,500], bij 0,5 −6,836 [−9,242; −4,326]; d 1 − d 2 −0,694 [−0,942; −0,434] / −5,986 [−8,315; −3,613]; P3 d 2 − d 3 (met annulering) −0,462 [−0,821; −0,147] / −4,552 [−8,012; −1,521]; P2 annuleringswaarde ≈ 0 (+0,053 [−0,017; +0,127] / +0,282 [−0,286; +0,884]).
- **Plafond (feit, uit de episodes):** de leiders zelf verdienen op deze signalen Σ +1.221,16 SOL = +203,527 SOL/dag [+137,147; +266,597] (eigen inzet, resterende tokens 0; 44,5 % van de signalen > 0, mediaan −0,00 %); hun weg bij onze inzet, één episode per sleutel (8.308), zonder onze impact en fees: +2,380 SOL/dag [+1,558; +3,142] bij 0,05 en +23,796 [+15,581; +31,416] bij 0,5.
- **Lezing A (vooraf vastgelegd):** d 0 én d 1 bij beide inzetten bovengrens (97,5 %) < 0 → snelheid koopt niets; de familie blijft gesloten. De waarde van snelheid in deze familie is negatief: sneller instappen en uitstappen vergroot het verlies.
- **Prior (hypothese vooraf, registerrij):** "snelheid verkleint het verlies en draait het teken niet om" — het teken draaide inderdaad niet om, maar de richting was fout: snelheid verkleinde het verlies niet, ze vergrootte het.

## 1. Opzet (feit; plan `research/e88-snelheidspremie-2026-10-30/e88-plan-2026-10-30.md` v0.4 bf7d5d86…)
- Data: store `fwd1064-1068-eb3cba4` (39b01872…), r4-episodes `wallets/e82-episodes-1064-1068-638380d` (5797e206…), passlist E81 §11 (59a18b63…); engine main 12878f2 (= #620: `follow.py --q`, E65-regel G1: d 0 alleen bij q 1,0 = vulling op de eindstand van slot t0, eerst in t0 + 1; d ≥ 1 bij q 0,5; `--cancel-t1` geweigerd bij d < 2).
- Drie follow-runs (NAS-job 99zzzzz9o, 10:16–10:20Z; 36 / 35 / 11.251 in alle drie): a `--delays 2,3 --cancel-t1` (90.008 posities), b `--delays 1,2` (90.008), c `--delays 0 --q 1.0` (45.004); verder r4's vlaggen (`--forward --venues curve --wallets passlist --from-slot 459720000 --to-slot 461739000`), `--sizes 0.05,0.5`, W_half primair, W_full beschrijvend. Mappen `wallets/e88-follow-1064-1068-12878f2-{a,b,c}` (NAS, met SHA256SUMS).
- Maat: netto SOL per UTC-dag van t0, één positie per munt per cel (E82-uitlezing), dag-bootstrap 10.000 seed 7; drie vaste paren per dag (plan v0.4 §4, LAB should-fix b): P1 snelheid zonder annulering = d 0 / d 1 − d 2 (zonder), P2 annuleringswaarde = d 2 (met) − d 2 (zonder), P3 vertraging mét annulering = d 2 (met) − d 3 (met = r4). Eén d per positie verschuift instap én exit: elk paar bundelt beide (LAB should-fix a); graduatie-exits (pool-open + 1) en de einde-data-fallback (§2) verschuiven niet mee.
- Reader `e88_read_speed_ladder.py` v4 (61356751…) met poorten G0–G3 vóór enige tabel; .out `e88_read_speed_ladder_1064-1068_v4.out` **732467e4477a71701518ed8b1548f9be1f6c0c55b99ccb470a7ee1c21d68c748** (NAS-job 99zzzzz9r), map-SHA256SUMS na de job b1af0203… (88 regels).

## 2. Keten en blikken (feit)
- Rook (synthetisch, 0 blikken): `e88_smoke_d0.py` (46e1f2a8…) — d 0 door de échte replay op een handgebouwde stream: vulling op de eindstand van t0 ná de koop van de leider, exit op de eindstand van u ná de verkoop van de leider (12/12; .out 8093aa69…); d 0 bij q 0,5 vult vóór de koop van de leider (G1-illustratie). Reader-rook op synthetische parts met vier negatieve controles (gen v3 d1b51b55…, runner v3 208a35cb…; .outs 940c8e03… / 11b16358…). De E83-synthetische store (0cbdcd81…, alleen fee-kolommen) kon geen follow-run voeden; de registerrij zei dat wel — tekstcorrectie in deze PR.
- Herlezing 1 (99zzzzz9p, reader v2 a3cc467c…, .out 3088435f…): G1 RED zonder tabel — graduatie-exits verkopen op pool-open + 1 bij elke d (1.653 rijen; diag 99zzzzz9p `e88_diag_g1.py` 2c0a7232…, .out 3420a704…). **0 blikken.**
- Herlezing 2 (99zzzzz9p, reader v3 43f0acab…, .out b26128d6…): G1 RED zonder tabel — 12 rijen (één episode Hbd8UtA1…/4mwKAjYj…, t0 461.731.368) met exit_slot NULL bij d ≥ 2: de vulslot ligt voorbij de laatste curve-leg (387), `PositionSim.end()` doet één `_try_exit` die op TOL faalt, `_try_exit` wist `self.pending`, de fallback schrijft exit_slot None met de verkoop op de laatste stand; label leader_sold (end()-wrat), geen waarderingsfout; identiek in r4/klein; afrekening telt één failed_tx (0,000505 SOL) extra (`FILLED_SQL`), in de gepinde −324,33 inbegrepen (diag 99zzzzz9q `e88_diag_null.py` aa71384a…, .out 5c9f7c68…). **0 blikken.**
- Herlezing 3 (99zzzzz9r, reader v4 61356751…): G0–G3 GREEN, **1 blik** (deel 1–4 samen). Alle plan-/reader-versies als bestand (v0.1 2ff0926a…, v0.2 8a49d1c9…, v0.3 906d96b5…; reader v1 7add03de…, v2, v3), jobscripts `e88_job.v1.sh` a0b52068… (niet gedraaid), `e88_job.sh` 26ef5652…, `e88_job_read.sh` c8183a9b…, `e88_job_read_v4.sh` 42485f04….
- Engine-nit (buiten E88, aparte bouw-PR met test, pas ná deze lezing): `end()` moet de bedoelde vulslot vóór `_try_exit` vastleggen (reden end_of_data); de fix verandert de boekhouding (één failed_tx minder per zo'n rij) en dus pinbare cijfers bij reruns van r4-achtige cellen.

## 3. Poorten (byte-gelijk uit de .out, regels 11–41)
```
G0 GREEN: configuratie van a/b/c/r4/klein zoals vooraf
a: rijen 90008 (t0 < min_t0 459720000, tellen niet: 0); overlap_skipped 6454; cellen [(0.05, 2, 'W_full'), (0.05, 2, 'W_half'), (0.05, 3, 'W_full'), (0.05, 3, 'W_half'), (0.5, 2, 'W_full'), (0.5, 2, 'W_half'), (0.5, 3, 'W_full'), (0.5, 3, 'W_half')]
b: rijen 90008 (t0 < min_t0 459720000, tellen niet: 0); overlap_skipped 6822; cellen [(0.05, 1, 'W_full'), (0.05, 1, 'W_half'), (0.05, 2, 'W_full'), (0.05, 2, 'W_half'), (0.5, 1, 'W_full'), (0.5, 1, 'W_half'), (0.5, 2, 'W_full'), (0.5, 2, 'W_half')]
c: rijen 45004 (t0 < min_t0 459720000, tellen niet: 0); overlap_skipped 3788; cellen [(0.05, 0, 'W_full'), (0.05, 0, 'W_half'), (0.5, 0, 'W_full'), (0.5, 0, 'W_half')]
r4: rijen 45004 (t0 < min_t0 459720000, tellen niet: 0); overlap_skipped 3195; cellen [(0.5, 3, 'W_full'), (0.5, 3, 'W_half'), (2.0, 3, 'W_full'), (2.0, 3, 'W_half')]
klein: rijen 67506 (t0 < min_t0 459720000, tellen niet: 0); overlap_skipped 4791; cellen [(0.05, 3, 'W_full'), (0.05, 3, 'W_half'), (0.1, 3, 'W_full'), (0.1, 3, 'W_half'), (0.2, 3, 'W_full'), (0.2, 3, 'W_half')]
G1 v4: sleutels met exit_slot NULL 1; laatste curve-leg per sleutel uit de store: [('Hbd8UtA1', 461731387)]
G1 structuur: gevulde rijen met entry_slot ≠ t0 + d: 0
   per map, d, exit-reden: gevulde rijen, afwijkend van de regel (leader_sold/time/end_of_data: c == exit_decision_slot, a/b >= + d; graduation == exit_decision_slot + 1; window_end beide == 461738999; NULL alleen bij exit_attempts >= 1 en laatste leg < exit_decision_slot + d), zonder exit_slot, seed_pool, cf_graduation
   a d 2 graduation         401      0     0      1     30
   a d 2 leader_sold      39376      0     4      0      0
   a d 2 time               863      0     0      0      0
   a d 3 graduation         384      0     0      1     30
   a d 3 leader_sold      38617      0     4      0      0
   a d 3 time               839      0     0      0      0
   b d 1 graduation         435      0     0      1     37
   b d 1 leader_sold      41918      0     0      0      0
   b d 1 time               955      0     0      0      0
   b d 2 graduation         401      0     0      1     30
   b d 2 leader_sold      39918      0     4      0      0
   b d 2 time               863      0     0      0      0
   c d 0 graduation         467      0     0      1     33
   c d 0 leader_sold      43456      0     0      0      0
   c d 0 time              1049      0     0      0      0
G1 GREEN
G2 reproductie a d 3 / 0.5 vs r4: gepaard 22502, alleen in r4 0, alleen in a 0, afwijkend 0
G2 reproductie a d 3 / 0.05 vs klein: gepaard 22502, alleen in klein 0, alleen in a 0, afwijkend 0
G2 primaire cel 0,5 / d 3 / W_half zonder overlap: a Σ -324.33 SOL / 9269 posities; r4 Σ -324.33 / 9269; pin -324.33 / 9269
G2 GREEN
G3 episodes (curve, wallets met posities, t0 in [459720000, 461739000)): 11251 == run-json 11251; zonder rendement/cost/proceeds 0
G3 GREEN — alle poorten groen; hierna de blik
```

## 4. Tabellen (byte-gelijk uit de .out, regels 42–223; DEEL 1 ladder, DEEL 2 paren, DEEL 3 plafond, DEEL 4 lezing)
```
scenario base: per_tx 0.000721 SOL; kostenbasis 2 x per_tx = 2.88 % van 0.05, 0.29 % van 0.5

==== DEEL 1 — ladder per inzet en variant: variant, inzet, cel, signalen, zonder overlap, zonder instap (cancelled_t1_complete / cancelled_t1_leader_sold / busy_pool / curve_complete / no_curve_state / no_fill / entry_failed), posities, Σ netto SOL, dagen, gem. SOL/dag, sd, bootstrap [2,5 %; 97,5 %], mediaan netto per positie (SOL), mediaan netto/inzet (%)
   W_half  0.05 d 0 (q 1,0, zonder annulering)            11251  10344     0     0     0     0     5     0     0  10339    -47.26  6   -7.877   3.003 [-9.618; -5.441] -0.0059 -11.7 %
        2026-10-21: -2.068 SOL  posities 571
        2026-10-22: -9.951 SOL  posities 2211
        2026-10-23: -7.764 SOL  posities 1985
        2026-10-24: -8.729 SOL  posities 2155
        2026-10-25: -8.422 SOL  posities 1732
        2026-10-26: -10.329 SOL  posities 1685
   W_half  0.05 d 1 (zonder annulering)                   11251  10398     0     0     0     0     6     0   386  10006    -46.71  6   -7.785   2.947 [-9.463; -5.376] -0.0058 -11.6 %
        2026-10-21: -2.071 SOL  posities 555
        2026-10-22: -9.750 SOL  posities 2139
        2026-10-23: -7.593 SOL  posities 1911
        2026-10-24: -9.170 SOL  posities 2083
        2026-10-25: -8.147 SOL  posities 1677
        2026-10-26: -9.981 SOL  posities 1641
   W_half  0.05 d 2 (zonder annulering)                   11251  10466     0     0     0     2     7     0   897   9560    -42.55  6   -7.091   2.693 [-8.600; -4.894] -0.0057 -11.4 %
        2026-10-21: -1.864 SOL  posities 523
        2026-10-22: -8.863 SOL  posities 2050
        2026-10-23: -7.245 SOL  posities 1815
        2026-10-24: -8.040 SOL  posities 1992
        2026-10-25: -7.238 SOL  posities 1616
        2026-10-26: -9.296 SOL  posities 1564
   W_half  0.05 d 2 (met annulering per einde t0 + 1)     11251  10467     9   134     0     0     0     0   890   9434    -42.23  6   -7.038   2.665 [-8.550; -4.835] -0.0057 -11.5 %
        2026-10-21: -1.866 SOL  posities 523
        2026-10-22: -8.843 SOL  posities 2050
        2026-10-23: -7.283 SOL  posities 1810
        2026-10-24: -8.060 SOL  posities 1965
        2026-10-25: -7.037 SOL  posities 1568
        2026-10-26: -9.135 SOL  posities 1518
   W_half  0.05 d 3 (met annulering = r4)                 11251  10481     9   135     0     1     0     0  1070   9266    -39.45  6   -6.575   2.442 [-7.836; -4.547] -0.0057 -11.3 %
        2026-10-21: -1.695 SOL  posities 514
        2026-10-22: -8.268 SOL  posities 2018
        2026-10-23: -7.342 SOL  posities 1769
        2026-10-24: -7.428 SOL  posities 1941
        2026-10-25: -6.824 SOL  posities 1537
        2026-10-26: -7.896 SOL  posities 1487
   W_half   0.5 d 0 (q 1,0, zonder annulering)            11251  10344     0     0     0     0     5     0     0  10339   -394.35  6  -65.724  25.600 [-80.775; -44.569] -0.0485 -9.7 %
        2026-10-21: -16.760 SOL  posities 571
        2026-10-22: -81.747 SOL  posities 2211
        2026-10-23: -64.297 SOL  posities 1985
        2026-10-24: -70.975 SOL  posities 2155
        2026-10-25: -71.092 SOL  posities 1732
        2026-10-26: -89.476 SOL  posities 1685
   W_half   0.5 d 1 (zonder annulering)                   11251  10398     0     0     0     0     6     0   384  10008   -389.24  6  -64.874  25.027 [-79.353; -44.581] -0.0477 -9.5 %
        2026-10-21: -16.652 SOL  posities 555
        2026-10-22: -79.808 SOL  posities 2140
        2026-10-23: -62.538 SOL  posities 1911
        2026-10-24: -75.255 SOL  posities 2084
        2026-10-25: -68.796 SOL  posities 1677
        2026-10-26: -86.197 SOL  posities 1641
   W_half   0.5 d 2 (zonder annulering)                   11251  10465     0     0     0     2     7     0   892   9564   -353.33  6  -58.888  23.008 [-72.454; -40.343] -0.0463 -9.3 %
        2026-10-21: -14.842 SOL  posities 523
        2026-10-22: -72.377 SOL  posities 2051
        2026-10-23: -59.812 SOL  posities 1815
        2026-10-24: -65.377 SOL  posities 1992
        2026-10-25: -60.116 SOL  posities 1617
        2026-10-26: -80.807 SOL  posities 1566
   W_half   0.5 d 2 (met annulering per einde t0 + 1)     11251  10466     9   134     0     0     0     0   885   9438   -351.64  6  -58.606  22.791 [-71.677; -40.253] -0.0472 -9.4 %
        2026-10-21: -14.869 SOL  posities 523
        2026-10-22: -72.229 SOL  posities 2051
        2026-10-23: -60.229 SOL  posities 1810
        2026-10-24: -65.960 SOL  posities 1965
        2026-10-25: -58.723 SOL  posities 1569
        2026-10-26: -79.627 SOL  posities 1520
   W_half   0.5 d 3 (met annulering = r4)                 11251  10483     9   135     0     1     0     0  1069   9269   -324.33  6  -54.054  20.563 [-64.705; -37.131] -0.0455 -9.1 %
        2026-10-21: -12.964 SOL  posities 514
        2026-10-22: -66.563 SOL  posities 2019
        2026-10-23: -60.903 SOL  posities 1769
        2026-10-24: -59.554 SOL  posities 1941
        2026-10-25: -56.680 SOL  posities 1538
        2026-10-26: -67.662 SOL  posities 1488
   W_full  0.05 d 0 (q 1,0, zonder annulering)            11251  10264     0     0     0     0     5     0     0  10259    -46.82  6   -7.803   3.038 [-9.607; -5.365] -0.0060 -12.0 %
   W_full  0.05 d 1 (zonder annulering)                   11251  10326     0     0     0     0     6     0   378   9942    -46.51  6   -7.752   3.021 [-9.579; -5.287] -0.0060 -12.0 %
   W_full  0.05 d 2 (zonder annulering)                   11251  10404     0     0     0     2     7     0   889   9506    -42.75  6   -7.125   2.809 [-8.804; -4.855] -0.0058 -11.6 %
   W_full  0.05 d 2 (met annulering per einde t0 + 1)     11251  10405     9   131     0     0     0     0   882   9383    -42.43  6   -7.072   2.789 [-8.775; -4.826] -0.0058 -11.7 %
   W_full  0.05 d 3 (met annulering = r4)                 11251  10424     9   133     0     1     0     0  1059   9222    -39.57  6   -6.595   2.632 [-8.127; -4.478] -0.0057 -11.5 %
   W_full   0.5 d 0 (q 1,0, zonder annulering)            11251  10264     0     0     0     0     5     0     0  10259   -391.95  6  -65.325  25.901 [-81.037; -44.672] -0.0503 -10.1 %
   W_full   0.5 d 1 (zonder annulering)                   11251  10326     0     0     0     0     6     0   376   9944   -388.66  6  -64.776  25.703 [-80.548; -43.936] -0.0497 -9.9 %
   W_full   0.5 d 2 (zonder annulering)                   11251  10403     0     0     0     2     7     0   884   9510   -356.83  6  -59.471  24.184 [-74.591; -39.822] -0.0480 -9.6 %
   W_full   0.5 d 2 (met annulering per einde t0 + 1)     11251  10404     9   131     0     0     0     0   877   9387   -355.17  6  -59.194  24.040 [-74.258; -39.688] -0.0490 -9.8 %
   W_full   0.5 d 3 (met annulering = r4)                 11251  10424     9   133     0     1     0     0  1056   9225   -325.79  6  -54.299  22.319 [-67.257; -36.356] -0.0471 -9.4 %

==== DEEL 2 — drie vaste paren per dag (gemeenschappelijke dagen met posities; één d per positie verschuift instap én exit, elk paar bundelt beide): variant, inzet, paar, dagen, gem. verschil SOL/dag, sd, bootstrap
   W_half  0.05 P1 snelheid zonder annulering: d 0 (q 1,0) − d 2 (zonder)  dagen  6 gem.   -0.786 sd   0.382 [-1.044; -0.500]
        2026-10-21: -0.205
        2026-10-22: -1.088
        2026-10-23: -0.519
        2026-10-24: -0.689
        2026-10-25: -1.184
        2026-10-26: -1.032
   W_half  0.05 P1 snelheid zonder annulering: d 1 − d 2 (zonder)          dagen  6 gem.   -0.694 sd   0.355 [-0.942; -0.434]
        2026-10-21: -0.208
        2026-10-22: -0.887
        2026-10-23: -0.348
        2026-10-24: -1.130
        2026-10-25: -0.909
        2026-10-26: -0.685
   W_half  0.05 P2 annuleringswaarde: d 2 (met) − d 2 (zonder)             dagen  6 gem.   +0.053 sd   0.101 [-0.017; +0.127]
        2026-10-21: -0.003
        2026-10-22: +0.020
        2026-10-23: -0.038
        2026-10-24: -0.020
        2026-10-25: +0.201
        2026-10-26: +0.161
   W_half  0.05 P3 vertraging mét annulering: d 2 (met) − d 3 (met = r4)   dagen  6 gem.   -0.462 sd   0.461 [-0.821; -0.147]
        2026-10-21: -0.172
        2026-10-22: -0.575
        2026-10-23: +0.059
        2026-10-24: -0.633
        2026-10-25: -0.214
        2026-10-26: -1.239
   W_half   0.5 P1 snelheid zonder annulering: d 0 (q 1,0) − d 2 (zonder)  dagen  6 gem.   -6.836 sd   3.411 [-9.242; -4.326]
        2026-10-21: -1.918
        2026-10-22: -9.371
        2026-10-23: -4.484
        2026-10-24: -5.598
        2026-10-25: -10.976
        2026-10-26: -8.669
   W_half   0.5 P1 snelheid zonder annulering: d 1 − d 2 (zonder)          dagen  6 gem.   -5.986 sd   3.253 [-8.315; -3.613]
        2026-10-21: -1.810
        2026-10-22: -7.431
        2026-10-23: -2.725
        2026-10-24: -9.878
        2026-10-25: -8.680
        2026-10-26: -5.389
   W_half   0.5 P2 annuleringswaarde: d 2 (met) − d 2 (zonder)             dagen  6 gem.   +0.282 sd   0.824 [-0.286; +0.884]
        2026-10-21: -0.027
        2026-10-22: +0.148
        2026-10-23: -0.417
        2026-10-24: -0.584
        2026-10-25: +1.393
        2026-10-26: +1.180
   W_half   0.5 P3 vertraging mét annulering: d 2 (met) − d 3 (met = r4)   dagen  6 gem.   -4.552 sd   4.478 [-8.012; -1.521]
        2026-10-21: -1.905
        2026-10-22: -5.666
        2026-10-23: +0.674
        2026-10-24: -6.406
        2026-10-25: -2.043
        2026-10-26: -11.966
   W_full  0.05 P1 snelheid zonder annulering: d 0 (q 1,0) − d 2 (zonder)  dagen  6 gem.   -0.678 sd   0.309 [-0.893; -0.453]
   W_full  0.05 P1 snelheid zonder annulering: d 1 − d 2 (zonder)          dagen  6 gem.   -0.628 sd   0.396 [-0.917; -0.347]
   W_full  0.05 P2 annuleringswaarde: d 2 (met) − d 2 (zonder)             dagen  6 gem.   +0.053 sd   0.100 [-0.015; +0.127]
   W_full  0.05 P3 vertraging mét annulering: d 2 (met) − d 3 (met = r4)   dagen  6 gem.   -0.477 sd   0.454 [-0.854; -0.202]
   W_full   0.5 P1 snelheid zonder annulering: d 0 (q 1,0) − d 2 (zonder)  dagen  6 gem.   -5.854 sd   2.795 [-7.841; -3.921]
   W_full   0.5 P1 snelheid zonder annulering: d 1 − d 2 (zonder)          dagen  6 gem.   -5.305 sd   3.496 [-7.987; -2.901]
   W_full   0.5 P2 annuleringswaarde: d 2 (met) − d 2 (zonder)             dagen  6 gem.   +0.277 sd   0.802 [-0.288; +0.867]
   W_full   0.5 P3 vertraging mét annulering: d 2 (met) − d 3 (met = r4)   dagen  6 gem.   -4.895 sd   4.141 [-8.273; -2.372]

==== DEEL 3 — plafond uit de episodes (leidersweg op t0, zonder onze impact en fees; in-sample; geen strategie)
signalen 11251; aandeel met leader_ret_realized > 0 44.5 %; mediaan leader_ret_realized -0.00 %; eigen netto van de leiders Σ +1221.16 SOL (proceeds − cost, resterende tokens 0)
   eigen netto per dag (alle signalen): dagen 6, gem. +203.527 SOL/dag, sd  90.211, bootstrap [+137.147; +266.597]
        2026-10-21: +74.992 SOL  episodes 616
        2026-10-22: +325.445 SOL  episodes 2380
        2026-10-23: +198.442 SOL  episodes 2133
        2026-10-24: +258.209 SOL  episodes 2345
        2026-10-25: +233.834 SOL  episodes 1904
        2026-10-26: +130.242 SOL  episodes 1873
   plafond bij inzet 0.05 (één episode per sleutel: 8308): dagen 6, gem.   +2.380 SOL/dag [+1.558; +3.142]
        2026-10-21: +0.921 SOL  episodes 459
        2026-10-22: +2.146 SOL  episodes 1828
        2026-10-23: +3.004 SOL  episodes 1596
        2026-10-24: +3.327 SOL  episodes 1715
        2026-10-25: +3.523 SOL  episodes 1354
        2026-10-26: +1.356 SOL  episodes 1356
   plafond bij inzet 0.5 (één episode per sleutel: 8308): dagen 6, gem.  +23.796 SOL/dag [+15.581; +31.416]
        2026-10-21: +9.212 SOL  episodes 459
        2026-10-22: +21.464 SOL  episodes 1828
        2026-10-23: +30.045 SOL  episodes 1596
        2026-10-24: +33.266 SOL  episodes 1715
        2026-10-25: +35.228 SOL  episodes 1354
        2026-10-26: +13.562 SOL  episodes 1356

==== DEEL 4 — lezing vooraf (plan §5; W_half, d 0 en d 1 bij 0,05 en 0,5; verkend, in-sample, geen oordeel)
   d 0 / 0.05: gem.   -7.877 SOL/dag [-9.618; -5.441]
   d 1 / 0.05: gem.   -7.785 SOL/dag [-9.463; -5.376]
   d 0 / 0.5: gem.  -65.724 SOL/dag [-80.775; -44.569]
   d 1 / 0.5: gem.  -64.874 SOL/dag [-79.353; -44.581]
A — d 0 én d 1 bij beide inzetten bovengrens (97,5 %) < 0: snelheid koopt niets in deze familie; de familie blijft op verkende data gesloten.
Alle getallen in-sample (de 36 zijn op deze dagen gekozen); d 0/d 1 is op onze confirmed-stroom onbereikbaar; dit meet de waarde van snelheid, geen strategie.

```

## 5. Lezing
- **Feit:** alle 20 cellen negatief op alle 6 dagen; de ladder is monotoon: hoe kleiner d, hoe groter het verlies, bij beide inzetten en beide varianten; de bootstrap-intervallen van P1 en P3 liggen geheel onder nul. Mediaan netto per inzet per positie ≈ −11,3 … −11,7 % (0,05) en −9,1 … −9,7 % (0,5), vrijwel onafhankelijk van d.
- **Reconstructie:** sneller = meer gevulde posities van dezelfde verliezende trade. Bij d 0 valt de vulling samen met de beslisstand, dus de 5 %-slippagegrens weigert niets (entry_failed 0; 10.339 posities bij 0,05 / W_half), bij d 1 weigert ze 386, bij d 2 897, bij d 3 1.070 (9.266 posities): de slippagegrens werkt bij d ≥ 1 als filter tegen de signalen waarna de prijs al > 5 % bewoog. Daarnaast koopt de volger bij elke d ná de koopimpact van de leider en verkoopt hij ná diens verkoopimpact; op d 0 precies op de eindstand van die slots.
- **Modelaanname:** één d voor instap én exit (de engine scheidt ze niet); q 0,5 bij d ≥ 1; scenario base (fee 0,000605 + tip 0,0001 + verwachte herpogingen; failed-tx 0,000505); W_half-exit op de leiderspositie; geen landingskans of prioriteitsfee-race; d 0 = "eerst in t0 + 1", geen colocatie/shred-niveau daarbuiten.
- **Onbekend:** of een andere exitregel dan "volg de leider" de ladder omkeert (niet gemeten; buiten dit plan); de 36 zijn in-sample gekozen, dus ook het plafond is een bovengrens.
- **Plafond vs. volger (feit):** de leiders verdienen +203,5 SOL/dag op deze signalen vóór hun eigen impact aan beide kanten; bij onze inzet is hun weg +2,4 / +23,8 SOL/dag; de volger haalt daarvan niets: −7,9 / −65,7 SOL/dag bij d 0. Het verschil is de prijs van ná hen komen (hun impact, de dump bij hun exit) plus fees en eigen impact.
- **Geen oordeel, geen kandidaat:** verkend, in-sample; d 0/d 1 vergt processed/shred-data plus een eigen verzendpad (sleutels/signing = eigenaarsbesluit buiten paper) en zou, zoals gemeten, het verlies vergroten.

## 6. Pins
- .out 732467e4477a71701518ed8b1548f9be1f6c0c55b99ccb470a7ee1c21d68c748; map `research/e88-snelheidspremie-2026-10-30/` SHA256SUMS b1af0203… (88 regels; alle versies, rook-.outs, diag-.outs, jobscripts v2/v3/v4 en de RED-.outs 3088435f… / b26128d6…); follow-mappen a/b/c op de NAS met SHA256SUMS; r4-reproductie bit-gelijk (G2: 22.502 gepaard, 0 afwijkend; Σ −324,33 / 9.269 == pin).
