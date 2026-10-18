# K1-houdbaarheid (E39): een kleine kopie van de curve-kopen van persistente wallets blijft op dag 10–18 positief — kandidaat, geen claim

> **Correctie 2026-10-18 (Codex-bevinding 6):** naast de houdtijd-look-ahead (E40) selecteerde ook het universum 'gesloten rondes' (binnen 5 % verkocht, over de hele periode; uitstap in hetzelfde epoch; verkoop na hun laatste verkoop) op de uitkomst. Point-in-time herhaald (NAS-job 99q): −38,5 SOL/dag, 12 van 12 dagen negatief. Zie `2026-10-17-k1-rollend-label.md` §3–4 en register E33/E39/E40.
>
> **Correctie 2026-10-18 (niet-SOL-quote, feit):** het universum bevatte ook curves met een andere quote-mint dan SOL (PUMP e.a.), waar `q` in eenheden van dat token staat; in de E40-rondes ≥ 5 'SOL' was dat 64 %. De +0,079 per ronde en +12,3/dag zijn dus geen SOL-getallen. De SOL-only herhaling staat in `2026-10-17-k1-rollend-label.md` §6: −52,1 SOL/dag, 12 van 12 dagen negatief (NAS-job 99t).

LAB research, 2026-10-17. Vervolg op winnaars II (E33, §5: steekproef van 400 rondes per cel) en de agenda (idee 5). NAS-job 97 (`python -m wallets.winners.slow_replay 100000 out curve`, 3e1bd84) herhaalde de 0,5 SOL-kopie voor **alle** trage, gesloten curve-rondes (houdtijd ≥ 1 min) van groep C op dag 10–18; groep C is op dag 1–9 gelabeld (gesloten rondes, ≥ 7 van 9 dagen positief, > 10 SOL), dus de uitkomst op dag 10–18 is point-in-time ten opzichte van het label. Alleen dev; hold-out en clean niet gelezen. Verkennend; labels **feit** / **modelaanname** / **reconstructie**. Uitvoer `wallets/winners-v1/c_replay-out-curve.parquet` (sha 40974ae6…).

## 0. Samenvatting

- **Omvang (feit):** 175.205 rondes van 676 wallets op 12 dagen (20 sep–1 okt, allemaal 270 ms); de bucket 1–5 min is een willekeurige steekproef van 100.000 van ~180.000, de rest is volledig.
- **Alle trage rondes samen: niets.** 0,5 SOL op 3 slots achter hun koop, verkoop 3 slots na hun eerste verkoop: gemiddeld −0,001 SOL per ronde, winstkans 32,5 %, Σ −142 SOL; per dag −11,8 [−89, +74]. De 1–5 min-rondes (−0,026 per ronde) en de kopen < 1 SOL (−0,030) trekken alles omlaag — *modelaanname*.
- **Achter grotere kopen wél, en dat is op het moment van de koop bekend.** Kopen ≥ 5 SOL (15.023 rondes): +0,079 per ronde, Σ +1.268 SOL; ≥ 20 SOL: +0,124 (n 1.929); 1–5 SOL: +0,019 (n 69.040); < 1 SOL: −0,030 (n 91.142). Elke houdtijd-bucket is bij ≥ 5 SOL positief (1–5 min +0,062; 5–15 min +0,091; 15–60 min +0,081; 1–6 u +0,130; > 6 u +0,042).
- **Met onze capaciteit (maximaal 5 open posities, één per token, 0,5 SOL):** het volgen van alleen de kopen ≥ 5 SOL geeft **+12,3 SOL per dag [9,3; 15,5]** (dagbootstrap, 10.000 trekkingen), geen enkele negatieve dag (minimum +4,3), 1.455 van de 15.023 rondes genomen; ≥ 1 SOL +12,6 [8,9; 17,1]; ≥ 20 SOL +8,4 [5,6; 11,3]; met 20 open posities +54 [45; 64]. Alle trage rondes zonder groottefilter: −0,8 [−4,3; +2,6] — *modelaanname (gretige simulatie, geen landingskans, geen risicobudget)*.
- **Oordeel:** K1 wordt een **kandidaat-hypothese** ("volg de curve-koop ≥ 5 SOL van een persistente wallet met 0,5 SOL; uitstap 3 slots na hun eerste verkoop, met een tijdplafond"), vergelijkbaar in dagwinst met H4 op dev (+10/dag). Geen claim: één dagblok van 12 dagen, één regime, één label-venster, en de kopie kent het uitstapmoment van de wallet. Vóór een vastlegging: (1) een **rollend** point-in-time label op dev (dag t uit dag t−9 … t−1, over de hele traderpopulatie, niet alleen groep C) — NAS-job; (2) de hold-out als diagnose; (3) de live-haalbaarheid: de wallet-watch (Triton-punt 5) ziet hun koop en verkoop, de engine moet 3 slots later kunnen handelen; (4) een tijdplafond als de wallet niet verkoopt. Niets verandert aan K3 of de H-armen.

## 1. Methode (feit)

- Rondes: gesloten (verkochte tokens binnen 5 % van gekochte), één venue (curve), houdtijd ≥ 1 min, instap en uitstap in hetzelfde epoch, geen mayhem; dag 10–18 = `entry_day ≥ 2026-09-20`.
- Kopie: 0,5 SOL kopen tegen de curve-toestand vóór slot `fb + 3`, verkopen tegen de toestand vóór `ls + 3` (hun laatste verkoop; voor rondes met één verkoop gelijk aan de eerste), fee-bps van hun koop, kosten 2 × 0,000705 SOL; d = 0 en d = 2 ernaast (d = 2 wijkt < 0,001 af van d = 3).
- Capaciteit: rondes op volgorde van instap-slot; een ronde wordt genomen als er minder dan N posities open zijn en de token niet al bezet is; busy tot het uitstap-slot. Dagsommen, dagbootstrap.

## 2. Tabellen (modelaanname)

Per houdtijd (alle groottes): 1–5 min n 100.000, hun eigen werkelijke netto +0,33 per ronde, kopie −0,026 (31 %); 5–15 min n 45.774, +0,031 (35 %); 15–60 min n 18.610, +0,044 (36 %); 1–6 u n 8.221, +0,034 (34 %); > 6 u n 2.600, +0,000 (34 %). Mediaan overal −0,07; p95 +0,43 … +0,91.

Per dag (Σ kopie, SOL): alle trage rondes +67, +324, −68, −96, −13, −30, +157, −21, −239, −192, −58, +28; alleen kopen ≥ 5 SOL: +109, +135, +94, +98, +85, +74, +174, +139, +122, +64, +104, +69 (elke dag positief).

Capaciteitssimulatie (dag 10–18, 12 dagen):

| filter op het moment van de koop | max open | genomen / beschikbaar | Σ SOL | per dag [2,5 %; 97,5 %] | slechtste dag |
|---|---|---|---|---|---|
| alle trage rondes | 5 | 4.310 / 175.205 | −9,1 | −0,8 [−4,3; +2,6] | −13,1 |
| koop ≥ 1 SOL | 5 | 3.231 / 84.063 | +150,6 | +12,6 [8,9; 17,1] | +5,4 |
| **koop ≥ 5 SOL** | **5** | **1.455 / 15.023** | **+147,8** | **+12,3 [9,3; 15,5]** | **+4,3** |
| koop ≥ 5 SOL | 20 | 4.523 / 15.023 | +653,2 | +54,4 [45,1; 63,6] | +24,3 |
| koop ≥ 20 SOL | 5 | 708 / 1.929 | +101,1 | +8,4 [5,6; 11,3] | +1,3 |

Concentratie: de top-1 % rondes is 19 % van de positieve som (minder scheef dan de curve-momenten van E27/E38); de top-10 wallets leveren samen +1.566 SOL op een totaal van −142 (de winst zit bij een deel van de wallets; per wallet is de mediaan −0,28 en 42,5 % van de wallets is netto positief).

## 3. Wat dit wel en niet zegt (reconstructie)

- **Wel:** de persistentie van groep C houdt op dag 10–18 stand voor een kleine volger, mits de volger alleen de grote kopen pakt. De grootte is op het moment van de koop bekend; het label is uit dag 1–9. Het verschil met E35 (grote koop op een jonge curve door wie dan ook: negatief) is het wallet-label: dezelfde gebeurtenis, andere afzender.
- **Niet:** een claim. Het label-venster is één keer gezet (dag 1–9) en niet rollend; de 12 dagen zijn één regime; de kopie reageert op hun verkoop (3 slots), wat live een wallet-watch vraagt; de curve-fill is een formule zonder landingskans of andermans trades in de 3 slots; de capaciteitssimulatie is gretig en kent geen dagverlies-stop. De groottedrempel (≥ 5 SOL) is gekozen na het zien van E33's steekproef (daar al zichtbaar), niet vooraf.
- **Volgende stap (geen vastlegging nog):** rollend label op dev als NAS-job (`closed_wmd`-logica over de hele traderpopulatie, per dag), dezelfde kopie-replay voor de dan gelabelde wallets op dag 10–18 én dag 1–9; blijft +10/dag met 5 open posities in beide blokken staan, dan een hypothesefile K1 (hash) en de forward-toets na H4/H5. De eigenaar beslist over de wallet-watch in paper (Triton-punt 5 is al goedgekeurd als meting).
