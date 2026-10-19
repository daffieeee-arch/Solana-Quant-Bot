# Wat kan alleen (of pas goed) in Rust — strategievoordelen en hoe we ze meten (versie 1.0, 2026-10-19)

Opdracht van de eigenaar (19 okt, via de coördinator): een lijst van wat de Rust-route (M3 live-shadow, M4 gedeelde kern) aan strategievoordeel kan geven, te bouwen zodra M1 bewezen gelijk is aan Python. LAB research schrijft het inhoudelijke deel (§1–§4: welk voordeel, hoe groot, hoe meten); Lab Backtest vult §5 (technische haalbaarheid per idee, wat M3/M4 kan, kosten). Alleen paper, alleen Triton/OF1-data; hold-out 1047–1051 en de schone set 1055–1063 blijven dicht; geen nieuwe betaalde dienst zonder eigenaarsbesluit. Labels: **feit** (bron of meting), **reconstructie**, **modelaanname**, **onbekend**.

## 0. Samenvatting voor de eigenaar

| # | gewone naam | wat het is | voordeel | ruwe grootte (bron) | eerste meting | oordeel |
|---|---|---|---|---|---|---|
| 1 | **Sneller instappen** | per event beslissen op de `processed`-stream, in plaats van na afsluiting van het slot met een gesimuleerde vertraging van 600 ms | prijs (minder trades tussen trigger en instap), kans | K3 op de **hold-out, alleen diagnose**: elk slot vertraging kost ≈ 0,45 procentpunt per trade (+0,68 % bij d 1, +0,04 % bij d 2, −0,26 % bij d 3; dag-CI's om 0); dev H-armen ≈ 13–14 % van het dagresultaat per slot. Van 2,75 naar ≈ 1 slot lineair doorgetrokken +25 … +30 % dagresultaat: **modelaanname met grote onzekerheid** (CI's om 0; het verloop binnen 600 ms is niet gemeten; bij snellere instap kan de instapprijs ook verschuiven doordat de concurrentie in dezelfde slots zit) | eerst: backtest op OF1 met instap per transactie binnen het slot (§3 M-a); M3-latencyhistogram | **meten, grootste kandidaat** |
| 2 | **Sneller uitstappen** | de exit (take-profit, flow-stop, drain) op de eerste transactie die hem rechtvaardigt, niet na het slot + d | risico (slechtste dag, verlies-alles bij drains) | drains tijdens de positie kosten de H-armen −0,35 … −0,45 SOL per positie, Σ −220 … −240 per blok (E36); hoeveel daarvan binnen één slot verdwijnt is onbekend | backtest op OF1: prijs op de eerste exit-trigger-transactie tegen de prijs op slot-einde + d (§3 M-b); Triton-punt 4 (voorbode) | **meten** |
| 3 | **Alle pools volgen** | het hele F7-universum in één proces, zonder watchlist-snoei; ook meer markten tegelijk | kans, capaciteit | de watchlist ziet 15,4 % van de pool-events en mist ≈ 150 organische F7-triggers per dag (2.857 in 19 dagen) omdat de lijst op dezelfde drempel instapt als F7 | trigger_gap op OF1 (bestaat); M3-shadow met de volledige filter tegen de recorder-watchlist (§3 M-c) | **meten; deels ook zonder Rust (lagere lijstdrempel)** |
| 4 | **In hetzelfde slot reageren** | een reactie die nog in het blok van de trigger landt | prijs | de pump zit grotendeels in het triggerslot zelf (K1-kopie: d 0 +0,16 … +0,30 per ronde, d 2 −0,06) — maar wie een transactie op `processed` ziet, landt op zijn vroegst in het volgende blok | alleen als bovengrens meten (d 0 tegen d 1 in de backtest) | **weerlegd als strategie**: vraagt bundels/MEV-infrastructuur, buiten paper |
| 5 | **MEV-achtig (fase 0)** | arbs en backruns rond pump/PumpSwap | prijs | NO-GO (E17): top-3 pakt 84 % van de bereikbare winst, nieuwkomers 3 %, mislukkingen ≤ 97 % | hermeting na Alpenglow, alleen meten | **geen bouw** |
| 6 | **Sneller en meer toetsen** | M4: de gedeelde kern in de backtest met rayon | onderzoekscapaciteit (meer hypothesen met dezelfde DSR-discipline) | onbekend; wall-time per stap meten (ontwerp §16.4) | M4-rapport | **meten bij M4** |

De grootste strategiewinst zit bij 1 en 2 en is pas te geloven na de meting in §3; 3 is deels zonder Rust te halen; 4 en 5 vragen zenden en bundels en vallen buiten paper.

## 1. Waar de vertraging nu zit (reconstructie uit code en ontwerp)

- De Python-paper-engine beslist op **afgesloten segmenten** (confirmed) en rekent met een **gesimuleerde** instapvertraging `d_ms 600` (≈ 2,75 slots bij de gemeten 218 ms per slot in epoch 1053–1054; `backtest/strategies.py Clock`). De echte afstand trigger → beslissing kan tot een minuut zijn; het journaal logt hem als `segment_lag_ms` (ontwerp `rust-live-route-design-2026-10-16.md` §1, §10).
- De Rust-route beslist op **`processed`** per event, met per-(slot, bank) overlays en afwikkeling op `finalized` (na Alpenglow ≈ 150 ms). Een trigger op slot t wordt beoordeeld zodra de bank van t compleet is; de instap kan dan in t + 1 landen (ontwerp §9). Dat is d ≈ 1 slot plus netwerklatency, tegen 2,75 nu — **modelaanname tot M3 het meet** (ontwerp §10: tijdstempels per stap, HdrHistogram).
- Alpenglow (epoch 1064, 21 okt) verandert de slottijd niet (doel 200 ms; meting beslist) maar wel de finaliteit (~150 ms) en de commitment-niveaus (`confirmed` = `finalized`), zie `2026-10-16-alpenglow-meetnotitie.md`.

## 2. De ideeën

### 2.1 Sneller instappen (per event beslissen)

- **Wat.** De F7-trigger (grote koop in een reversed pool) wordt gezien zodra de transactie op `processed` binnenkomt; de instap gaat uit bij het eerstvolgende blok. Vertraging van ≈ 600 ms naar ≈ 1 slot + netwerk.
- **Voordeel.** Minder transacties tussen trigger en instap, dus een lagere instapprijs en minder gemiste rondes (de take-profit van K3 wordt in 3–10 s gehaald; E11: +1,0 … +1,8 % in 3–10 s bij 270 ms).
- **Grootte (feit, diagnose op de hold-out en dev; geen claim).**
  - **Hold-out 1047–1051, alleen diagnose** (eenmalig gelezen voor de eindrangschikking; nooit voor tuning of een meting hieronder), K3 flagged 0,5 SOL: d 1 **+0,68 %** per trade, d 2 +0,04 %, d 3 −0,26 % (dag-CI overal om 0); organisch −0,08 / −0,95 / −1,52 %. Elk slot vertraging kost ≈ 0,45 procentpunt per trade (`2026-10-12-holdout.md`).
  - Dev 1033–1046, lange exits: `flow300_H6` organic Σ +7,20 (d 1) tegen +6,20 (d 2) SOL per dag; `flow300_tp100_H6` +10,68 tegen +9,30 (E28, E30): ≈ 13–14 % van het dagresultaat per slot.
  - Lineair doortrekken van 2,75 naar 1 slot (**modelaanname, grote onzekerheid**): +0,5 … +0,8 procentpunt per trade bij K3, +25 … +30 % van het dagresultaat bij de H-armen; de d 1-rij is de bovengrens. Onzekerheden: de dag-CI's van de d-rijen liggen om 0; het verloop binnen de eerste 600 ms na de trigger is niet gemeten (de markout op 1 s is nog negatief door de round-trip-kosten ≈ 2 %, op 1 en 5 min positief, E11); en wie sneller instapt zit in dezelfde slots als de andere snelle kopers, zodat de instapprijs bij een snellere instap ook door die concurrentie kan verschuiven (onbekend; alleen live te zien, M3/M-d).
- **Meting zonder geld.** (a) OF1-backtest met instap **per transactie** in plaats van per slot: de store heeft `tx_index`, dus de toestand "na de trigger-tx + k transacties" en "eerste transactie van t + 1" bestaat; dat geeft de curve resultaat tegen vertraging in ms voor K3 en de H-armen (§3 M-a). (b) M3-shadow: histogram `t_recv − block_time` en `t_decision − block_time` per trigger, naast de gesimuleerde d (ontwerp §10). (c) Paper: `alpenglow_metrics` M1 (latency per trigger, aandeel later dan d).
- **Nodig en risico.** `processed`-stream (b1 via de recorder of b2 eigen stream, eigenaarsbesluit vóór M3), de Rust-engine M1–M3. Risico: de volgorde binnen een slot is op `processed` niet gegarandeerd (ontwerp §9), en de landingskans van een snelle order is niet meetbaar zonder te zenden (Triton-punt 1 meet alleen uitvoeringssucces van opgenomen transacties).

### 2.2 Sneller uitstappen (instorting, flow valt stil, drain)

- **Wat.** Exits worden nu beoordeeld op de trades van de pool bij slot-afsluiting en uitgevoerd op t_exit + d. Per event kan de exit op de eerste verkoop die de regel haalt, of op de eerste tekenen van een LP-withdraw (accountfilter op de LP-token- en poolaccounts van open posities: Triton-punt 4).
- **Voordeel.** Risico: slechtste dag en verlies-alles. Drains tijdens een positie kosten de H-armen −0,35 … −0,45 SOL per positie met 30–56 % verlies-alles (E36); een withdraw ná onze exit is juist +0,18 … +0,28.
- **Grootte.** **Onbekend**: hoeveel van de dump binnen het exit-slot plus d valt (de rest is al weg voordat welke engine ook reageert). De drain zelf is één transactie; wat helpt is de voorbode (seconden ervoor; Triton-punt 4, nog niet gemeten) of de eerste grote verkoop.
- **Meting.** (a) OF1-backtest: per gerealiseerde exit de prijs op de **eerste** transactie die de exit rechtvaardigt tegen de prijs op t_exit + d (het intra-slot-verlies van exits; §3 M-b). (b) Per drain op een open positie: tijd tussen de eerste voorbode-transactie (LP-account aangeraakt) en de withdraw (Triton-punt 4, tweede paperweek). (c) M3-shadow: latency van de exitbeslissing.
- **Nodig en risico.** Accountfilter op een klein aantal accounts (≤ 5–20 open posities), `processed`. Risico: whipsaw (één verkoop is geen instorting); meet beide kanten (gemiste winst door te vroege exits).

### 2.3 Alle pools volgen (capaciteit en meer markten)

- **Wat.** De recorder volgt een watchlist (mediaan 161 pools, 15,4 % van de pool-events); een Rust-engine kan het hele F7-universum (21,6 M pool-events per dag ≈ 250 tx/s, pieken ×10) in één proces bijhouden, en ook de curve en normale pools naast de reversed pools.
- **Voordeel.** Kans: de lijst mist ≈ 150 organische F7-triggers per dag (2.857 in 19 dagen) omdat ze instapt op dezelfde drempel (150 SOL) waarop F7 triggert (`2026-10-12-watchlist-meting.md`). Capaciteit: meer gelijktijdige markten zonder dat de Python-engine (≈ 90 tx/s gemiddeld) de bottleneck wordt.
- **Grootte.** De waarde van de gemiste triggers is te berekenen uit `trigger-gap-v1` (OF1): **nog niet gedaan**. Een deel is zonder Rust te halen (lagere lijstdrempel, meer bytes).
- **Meting.** (a) OF1: netto resultaat van de gemiste triggers met dezelfde exits (§3 M-c). (b) M3-shadow met de volledige engine-filter naast de recorder: triggers gezien door beide, alleen door de volledige filter, alleen door de watchlist.
- **Nodig en risico.** Eigen stream (b2, 7–12 GB/dag ≈ $0,6–1/dag bij $0,08/GB) of een bredere recorder-filter; VPS-geheugen (ontwerp §10: 1–2 GB). Risico: bytes en budget, head-of-line-blocking achter de recorder bij b1.

### 2.4 In hetzelfde slot reageren — weerlegd als strategie

- Een transactie die wij op `processed` zien, zit al in een blok in aanbouw; onze reactie landt op zijn vroegst in het volgende blok (leader van t + 1). "Hetzelfde slot" vraagt bundels (Jito) of een plek bij de leader: zenden, buiten paper, en MEV-infrastructuur.
- De bovengrens is wel meetbaar: d 0 tegen d 1 in de backtest. De K1-kopie laat zien hoe groot die is (d 0 +0,16 … +0,30 per ronde, d 2 −0,06: de pump zit in het triggerslot). Dat getal zegt wat we **niet** krijgen, niet wat Rust oplevert.

### 2.5 MEV-achtig (fase 0) — geen bouw

- E17 (`2026-10-15-mev-fase0.md`): 457 bereikbare kansen per dag, maar top-3 84 % van de winst, nieuwkomers 3 %, mislukkingen ≤ 97 %: NO-GO. Rust verandert de snelheid, niet de monopolisering of de mislukkingskosten. Na Alpenglow hermeten (alleen OF1, niets zenden), geen bouw.

### 2.6 Sneller en meer toetsen (M4)

- Geen strategievoordeel maar onderzoekscapaciteit: de gedeelde kern in de backtest met rayon (ontwerp §16.4) maakt meer hypothesen per week mogelijk met dezelfde DSR- en pre-registratiediscipline. Grootte onbekend tot de wall-time per stap gemeten is (1/4/8/14 threads, NAS-job).

## 3. Meetplan (alles zonder geld; volgorde)

**Data-regel:** M-a t/m M-e gebruiken alleen dev (1033–1046) en forward (≥ 1064, na pre-registratie); de hold-out 1047–1051 en de schone set 1055–1063 worden er niet voor gelezen. De hold-out-getallen in §2.1 zijn een diagnose uit de eenmalige eindrangschikking, geen invoer voor deze metingen. **Volgorde na de paperstart:** M-a eerst (grootste kandidaat), als NAS-job met de spec vooraf in `research/`; dan M-b en M-c; M-d en M-e lopen met M3 en paper mee. Spec M-a: `research/spec-rust-ma-instap-per-transactie-2026-10-19.md` (v0.1). Twee uitvoeringsroutes, keuze bij de inplanning: (i) een uitbreiding van `wallets/markout.py` met instapdoelen op transactieniveau (onderzoekscode, geen kernwijziging, markout-horizonten plus de eigen exit van de engine uit de parts); (ii) de bewezen M1-kern vanuit Python met een instap op transactie-index (§5, Lab Backtest; na C2). Route (i) kan direct na de paperstart, route (ii) geeft dezelfde exits als de engine.

| # | meting | data | wanneer | levert |
|---|---|---|---|---|
| M-a | **instap per transactie** (eerst): K3 (tp4_T600) en H4/H5 op dev met instap op de toestand na de trigger-tx + k ∈ {0, 1, 3, 10} transacties, op de eerste tx van t + 1, en op t + 1 … t + 3 slot-einde; dagbootstrap; d 1/2/3 als controle (= bestaande tabellen) | OF1 dev 1033–1046 (verbruikte data; alleen gevoeligheid, geen claim); geen hold-out, geen schone set | NAS-job direct na de paperstart; spec vooraf in `research/` | de curve resultaat tegen vertraging in ms; de bovengrens van idee 1 |
| M-b | **intra-slot-verlies van exits**: per exit de prijs op de eerste rechtvaardigende tx tegen de prijs op t_exit + d; apart voor take-profit, flow-stop, drain | OF1 dev | NAS-job, zelfde spec | de bovengrens van idee 2 |
| M-c | **waarde van de gemiste triggers**: netto van de 2.857 gemiste organische F7-triggers met de K3- en H-exits | `trigger-gap-v1` + OF1 dev | VPS of NAS | idee 3 |
| M-d | **latency-histogrammen** per stap (`t_recv`, `t_decision` tegen `block_time`) en de echte d per trigger | M3-shadow (b1, daarna b2) | bij M3 | de echte d van idee 1 en 2; vergelijking met de gesimuleerde 600 ms |
| M-e | **latency per trigger in paper** (`alpenglow_metrics` M1) en de Triton-punten 1, 4, 7 | paper, eerste twee weken | loopt vanaf 20 okt | kostenmodel; voorbode van de drain (idee 2) |

Een claim komt pas forward, pre-geregistreerd, met DSR en LAB review; M-a t/m M-c zijn gevoeligheidsmetingen op verbruikte dev-data.

## 4. Wat Rust niet oplost

- De landingskans en de kosten van mislukte orders: alleen meetbaar door te zenden (buiten paper); Triton-punt 1 geeft een bovengrens.
- De rand zelf: K3 organisch is op de hold-out bij d ≥ 2 niet van 0 te onderscheiden; sneller instappen vergroot een rand, het maakt er geen.
- Marktstructuur: MEV-monopolisering (E17), de 5-minutencyclus van fabriekspools (K2), de vertraging-tegen-pump van K1.

## 5. Technische haalbaarheid en kosten (Lab Backtest, 2026-10-19)

Per idee: wat M3 (b1/b2, `processed`, accountfilters) en M4 kunnen, wat het kost in bytes, VPS-geheugen en bouwtijd, welke latency realistisch is, en wat niet kan zonder te zenden. Prijzen: streaming **$0,08/GB** (Triton-docs, Titan Swap API-pagina; prijspagina 2026-10-11 in `research/triton-edge-2026-10-11.md`). Bytes uit het ontwerp §4 (store fwd1054, 3 KB per transactie: **schatting** tot de meetuur van 20 okt het echte getal geeft). Bouwtijd in agent-werkdagen plus NAS-rondes (ontwerp §5).

| idee | kan in M3/M4 | wat het vraagt | kosten | latency (verwacht, te meten) | opmerking |
|---|---|---|---|---|---|
| 1 sneller instappen | **ja, M3**: beslissen op `processed` zodra de bank van slot t compleet is (ontwerp §9), met een gesimuleerde instap in t + 1. **M4**: de backtest-variant "instap na de trigger-tx + k transacties" (M-a) | `processed`-stroom (b1: recorder op `processed` plus relay; b2: eigen stroom), overlays per (slot, bank_id), stempels per stap (§10); voor M-a een kleine uitbreiding van de kern: invoegen op transactie-index in plaats van op q | b1 **+0 GB**; b2 **7–12 GB/dag ≈ $0,6–1,0/dag** ($17–29/maand); Riptide zonder toeslag (gefactureerd als de gedeelde dienst). Bouw: M3 1–1,5 week plus ≥ 2 weken draaien. VPS: `lab-live` 2 GB (ontwerp §5) | beslissing ≈ einde van slot t (≤ 1 slot ≈ 200–220 ms) + netwerk Triton → VPS (**onbekend**; M-d meet `created_at` → `t_recv` en tegen `block_producer_time_ns`); instap t + 1 tegen nu **3 slots ≈ 655 ms** bij 218 ms (Alpenglow-notitie) — **modelaanname** tot M3 meet | d = 1 is een **nieuwe trial** (pre-registratie, forward). De gesimuleerde fill in t + 1 zegt niets over de echte landing in t + 1 |
| 2 sneller uitstappen | **ja, M3**: dezelfde PositionSim kan na elke transactie beslissen in plaats van per slot (de re-run gaat al per transactie, `positions.rs`); **M4**: M-b op OF1 via de kern | voor de voorbode een accountfilter op de LP-token- en poolaccounts van open posities (≤ 5–20 accounts: expliciete pubkeys volstaan, een cuckoo-filter pas boven ~100, Triton-docs); `processed` | ≤ 0,5 GB/dag ≈ **≤ $0,04/dag** (kansen-rapport punt 4); bouw: een beslis-per-tx-modus in de kern is klein (dagen), de backtest-meting M-b een NAS-job | zoals 1 voor de trades; of een voorbode ≥ 1–2 slots vóór de withdraw komt: **onbekend** (Triton-punt 4, tweede paperweek) | per-tx-exit is een **andere regel** dan Python's: geen parity meer, eerst M-b, dan een pre-geregistreerde variant |
| 3 alle pools volgen | **ja, M3b (b2)** met een eigen filter; volledige onafhankelijkheid vraagt de watchlist-bouwer in Rust (na M3) | eigen stroom; kaarten per pool begrensd door snoei (zoals `pool_seen`); de 24 h-farmer-sommen per (pool, wallet) blijven nodig voor het farm-label | alle reversed pools ≥ 150 SOL: **~17 GB/dag ≈ $1,4/dag (~$41/maand)**; alle SOL-markt-pools: **54,7 GB/dag ≈ $4,4/dag (~$131/maand)**. Geheugen: de engine houdt geen dag aan legs vast, maar per sleutel 400 slots (Recent), per pool een farm-venster van 1 h en per (pool, wallet) de 24 h-sommen; voor alle pools ruw < 0,5 GB (**modelaanname**, M3 meet de piek). Een `PoolLeg` van lab_core is ~110 B: 24 h aan legs van alle pools zou ~2 GB zijn, dus nooit legs per dag bewaren | geen extra latency; doorvoer gemiddeld ~210 tx/s, pieken ×10: een fractie van een core voor decode en state (**modelaanname**, criterion-meting op de NAS) | deels zonder Rust: een lagere watchlist-drempel in de recorder (meer bytes). Eerst M-c (waarde van de gemiste triggers) vóór meer bytes |
| 6 M4 toetsen | **ja, M4**: PositionSim, exits en kosten via PyO3 in `backtest/tournament.py`, parallel met rayon over tokens en varianten (§16.4) | de M1-kern is er (C2 bewijst hem gelijk aan Python); PyO3-bindings; determinisme-test 1 tegen N threads | geen Triton-kosten; bouw **1–2 weken** (ontwerp §5); NAS 16 threads | n.v.t.; wall-time per stap bij 1/4/8/14 threads is de meting (NAS-job) | busy en de risicolimieten van een paper-groep blijven sequentieel: parallel per token of per variant, niet binnen een groep |

**Latency, eerlijk.**
- **Feit:** Triton publiceert voor Riptide **geen getal** tegen het gewone Dragon's Mouth-endpoint; de "tot 400 ms" in de docs is Dragon's Mouth tegen WebSockets ([Riptide](https://docs.triton.one/project-yellowstone/riptide), [Dragon's Mouth](https://docs.triton.one/project-yellowstone/dragons-mouth-grpc-subscriptions)). Riptide is hetzelfde protocol, dezelfde filters en dezelfde token, alleen een ander netwerkpad met automatische routering naar de dichtstbijzijnde locatie: in M3b een configuratiewissel, geen bouw.
- **Feit:** `processed` is de laatste stap op het pad leader → shreds → replay; eerder zien kan alleen via **preconfirmations** (Harmonic, BAM): de bouwer meldt de transactie vóór er shreds zijn, maar alleen voor slots van leaders op die clients, en alleen statische accountsleutels matchen (v0 lookup-tabellen niet) ([Preconfirmations gRPC](https://docs.triton.one/chains/solana/preconfirmations-grpc)). Kosten: BAM $50 per miljoen berichten; Harmonic het hoogste van een slotvloer en de berichten (≈ $324/dag per regio op de vloer, kansen-rapport punt 6). Een **nieuwe betaalde dienst**: alleen als bovengrens-meting en alleen na een eigenaarsbesluit.
- **Modelaanname:** na Alpenglow (epoch 1064) komt `finalized` ~150 ms na het voorstel; de overlays per bank in M3 worden daarmee kort, maar de beslissing zelf hangt aan `processed` en het einde van de bank, niet aan de finaliteit.
- **Te meten in M3 (M-d):** per trigger `t_recv − block_producer_time_ns`, `t_decision − t_recv` en het aandeel triggers waarvoor de beslissing vóór het einde van slot t + 1 valt. Pas dan is een d = 1-variant onderbouwd.

**Wat niet kan zonder te zenden** (ontwerp §11; buiten paper):
- de **landingskans** en de positie van onze order in het blok: een gesimuleerde fill in t + 1 is een aanname; Triton-punt 1 geeft alleen het uitvoeringssucces van opgenomen transacties van anderen;
- **in hetzelfde slot reageren** (idee 4): vraagt een bundel of een plek bij de bouwer (Jito `sendBundle`, BAM); preconfs laten de trigger eerder zien, maar handelen in dat slot blijft zenden. Eens met "weerlegd als strategie";
- **MEV-achtig** (idee 5): Rust verandert de snelheid, niet de verdeling van de winst (E17). Eens met "geen bouw";
- de **echte slippage en mislukte orders** van ons eigen ordertype: alleen meetbaar met echte orders; de paper-reconciliatie (fill tegen de eerste echte trade in dezelfde pool na ons slot) is de beste benadering zonder zenden.

**Volgorde (aanbeveling Lab Backtest).** Eerst M1 bewezen gelijk (C2 loopt; dit is de voorwaarde voor elke variant hierboven). Dan M-a en M-b als NAS-jobs op dev met de bewezen kern (M4-light: de kern vanuit Python, nog zonder rayon), los van M3. M3a (b1) kost geen bytes en levert M-d; M3b (b2, eventueel op Riptide) pas na het eigenaarsbesluit over b1/b2 vóór M3. Idee 3 pas na M-c.

## 6. Bronnen

`lab/docs/rust-live-route-design-2026-10-16.md` (§1, §9, §10, §16); `lab/reports/2026-10-12-holdout.md` (d-tabel); `2026-10-12-watchlist-meting.md`; `2026-10-15-markouts.md` (E11); `2026-10-15-mev-fase0.md` (E17); `2026-10-16-alpenglow-meetnotitie.md`; `2026-10-17-triton-live-kansen.md`; register E28, E30, E36, E40.
