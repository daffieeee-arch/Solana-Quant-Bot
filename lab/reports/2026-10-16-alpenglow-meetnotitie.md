# Alpenglow-meetnotitie: wat verandert er op 21 oktober voor onze strategieën, en wat meten we de eerste week

LAB research, 2026-10-16. Opdracht van de coördinator (voor de eigenaar): kort, officiële bronnen, af vóór 21 okt.
Labels: **feit** (bron of eigen meting), **reconstructie** (afgeleid uit code of data), **modelaanname**, **onbekend**.
Deze notitie wijzigt niets aan K3 (E14, bevroren) of aan de H-armen (spec abc0f29f); zij benoemt metingen.

## 1. Wat verandert er (bronnen in §5)

| Onderwerp | Vóór (TowerBFT, nu) | Na (Alpenglow, epoch 1064) | Label |
|---|---|---|---|
| Moment | — | Feature gate aan het begin van epoch 1064, woensdag 21 okt ~14:50–15:00 UTC; daarna nog 5.000 slots TowerBFT (~17–18 min); Alpenglow neemt over op slot **459.653.000** [S1, S4] | feit |
| Omschakeling zelf | — | ~2 slots (~400 ms) alleen vote-blokken, gebruikerstransacties staan stil; die blokken halen `confirmed` niet; kleine sprong in de slotnummers; preconfs (Jito/Harmonic) ~10 min uit [S4] | feit |
| Slottijd | 400 → 200 ms in vier stappen (SIMD-0525), laatste stap epoch 1053 (9 okt); gemeten in onze stores: 1033–1036 316 ms, 1037–1052 266–268 ms, **1053–1054 218,3 ms** | **Alpenglow wijzigt de slottijd niet**; de 200 ms is een doel, de meting beslist. Votor-timeouts en -certificaten bepalen het tempo in plaats van PoH [S1, S2] | feit (bron + eigen meting) |
| Finaliteit | ~12,8 s (31 blokken na confirmed) | doel ~150 ms na het voorstel (één ronde ≥ 80 % stake ≈ 100 ms, twee rondes ≥ 60 %); `confirmed` = `finalized`, `confirmed` verdwijnt later [S1, S3] | feit (doel, geen meting) |
| Votes | vote-transacties in blokken (~75 % van alle tx) | votes gaan direct tussen validators, als BLS-certificaten; **tx-tellingen per blok dalen** zonder dat activiteit verandert [S1, S2] | feit |
| Leader schedule | 4 slots per leader, 432.000 slots per epoch (~24 h bij 200 ms) | ongewijzigd in 4.3; "fast leader handoff" (meerdere banken per slot) komt pas in Agave 4.4, verwacht < 1 % van de slots. Geyser/gRPC dragen `bank_id` [S1] | feit |
| Blok-tijdstempel | `Clock.unix_timestamp` = stake-gewogen mediaan van vote-tijden | de **leader** zet hem: tussen ouder en ouder + 2 × verstreken slotduur; eerlijke leaders zetten `parent + 0` tot de echte tijd is ingehaald [S1] | feit |
| PoH-ticks | 64 tick-entries per slot | geen ticks meer; blokgrens uit block meta + footer [S1] | feit |
| Fees, SVM, tx-formaat | — | **onveranderd** voor gebruikers [S1, S4]. Validators betalen 0,8 SOL VAT per epoch (SIMD-0357), verbrand; de vote-fees (~1 SOL/epoch) vervallen [S1] | feit |
| Prioriteitsfee-gedrag | — | geen officiële uitspraak; Triton verwacht alleen een kleine piek direct na de pauze (herhaalde tx landen samen) [S4] | onbekend |
| Jito / MEV | BAM op een deel van de stake; Block Engine-bundels; BAM-veiling 25 → 20 ms sinds de 200 ms-slots | BAM-nodes gaan "in de dagen vóór activatie" in **Maintenance Mode** (validators terug naar gewone Jito-Agave Block Engine); bundels en tips blijven werken; MPP, DBI, FireBAM/FrankenBAM uit; BAM komt na activatie geleidelijk terug; duur niet aangekondigd [S5, S6] | feit (plan, geen tijden) |

## 2. Wat dit betekent voor onze strategieën

**Vertraging d.** K3 en de H-armen gebruiken `d_ms = 600`, per epoch in slots omgerekend met de gemeten slottijd (`Clock.slots`: afgerond, minstens 1; randen 240 en 400 ms). Bij 218 ms is dat **3 slots** (≈ 655 ms); bij een zuivere 200 ms blijft het 3 slots (600 ms). Alpenglow verandert dat niet, tenzij de gemeten slottijd boven 240 ms komt (dan 2 slots) — *reconstructie uit `paper/engine._d_at` en `backtest/strategies.Clock`*. De dev-historie blijft d = 2 bij 266–316 ms. Alles na epoch 1064 is één nieuw regime (besluit 2026-10-14), nooit gepoold met dev of clean.

**Wat d verbergt: data-latency.** Onze recorder leest op `confirmed`. Onder TowerBFT betekent dat ≥ 66 % van de stake heeft gestemd, met votes die in latere blokken landen: minstens 1–2 slots na het blok plus propagatie. Onder Alpenglow komt `confirmed` als het certificaat er is, doel ~150 ms na het voorstel. De gemeten `latency_ms` (ontvangst − bloktijd) per trigger moet dus **dalen**. d blijft 600 ms (pre-registratie); het verschil tussen gemeten latency en d is een meting, geen reden om d vóór de klok aan te passen. Wordt de latency structureel < 1 slot, dan is d = 3 slots conservatiever dan nodig; dat gaat als bevinding in het eerste weekrapport, een andere d is een nieuwe trial — *modelaanname*.

**Fills.** De fills zijn gesimuleerd tegen de reserves op het instap-slot t + d, inclusief eigen impact, met de kosten van scenario base (fee 0,000605 SOL, tip 0,0001, failed 0,000505, land 0,97). Consensus raakt die rekensom niet; wat wél kan veranderen is **wie er vóór ons in het blok zit**: BAM Maintenance Mode zet een deel van de stake terug op gewone bundel-inname, en de 25 → 20 ms veiling is al sinds epoch 1053 van kracht. Effect op onze landingskans en adverse execution: *onbekend*; de paper-reconciliatie (fill vs. eerste echte trade in dezelfde pool na ons slot) meet het.

**Vensters in slots.** F7 T600, flow300, H6, het 1 h-farmvenster en het 24 h-farmervlag zijn ms-vensters die per epoch in slots gaan (`Clock`, met de fallback naar het laatst gemeten epoch, #224). Een andere slottijd verschuift dus niets in tijd; alleen de eerste ~1.000 slots van 1064 rekenen met het 1063-getal — *reconstructie*. De slotsprong bij de omschakeling verkort één keer elk lopend venster met het aantal overgeslagen slotnummers (enkele slots, ~400 ms): verwaarloosbaar, en het forward-venster van E32 begint pas 24 h na de switch.

**Bloktijdstempel.** `latency_ms` en `segment_lag_ms` worden tegen `block_time` (seconden) gerekend. Na 1064 zet de leader die tijd zelf, met `parent + 0` tot de echte tijd is ingehaald: de stempel kan dus **achterlopen** op de echte tijd en onze latency te hoog laten lijken. De recorder schrijft sinds de Alpenglow-PR per blok `block_footers.block_producer_time_ns` (producent-tijd in ns): dat is de juiste referentie na de switch (§3). Ook `LiveFeed` meet de slottijd per epoch uit `block_time` over ≥ 1.000 slots; over zo'n span middelt de leader-stempel uit (bovengrens ouder + 2 × slotduur), maar de eerste meting van 1064 bevat de 5.000 TowerBFT-slots en de sprong — hooguit ~1 % — *modelaanname*.

**Tx-tellingen en stille blokken.** `executed_tx_count` per blok daalt sterk (votes weg). Niets in paper of backtest gebruikt die telling, en de recorder stopt niet op lege blokken (readiness-notitie 2026-10-14) — *reconstructie*. Dashboards en alarmen die op tx/blok rekenen moeten opnieuw geijkt (wij hebben er geen).

**Niet van toepassing.** Rotor (blokpropagatie) heeft geen datum; multiple banks per slot pas in 4.4; VAT raakt alleen validators.

## 3. Metingen voor de eerste week (vóór/na de switch)

"Vóór" = de recorder-uren vóór 21 okt 14:50 UTC. Advies: recorder en paper-engine **≥ 24 h vóór de switch** laten draaien (de store-opwarmtijd van spec H4/H5 vraagt dat al), anders is er geen "vóór" op dezelfde meetlat; de epochs 1053–1054 (218 ms) zijn dan de referentie uit de stores. Alles per UTC-dag én gesplitst op het switch-slot 459.653.000.

| # | Meting | Staat al in | Nog toe te voegen |
|---|---|---|---|
| M1 | Latency per F7-trigger: ontvangst − bloktijd, segment-sluiting − bloktijd, mediaan/p90, aandeel later dan d | journal `signals`: `block_time`, `received_unix_ns`, `created_unix_ns`, `latency_ms`, `segment_lag_ms`, `d_ms`; dagrapport §Latency | — |
| M2 | Latency tegen de **producent-tijd**: ontvangst − `block_producer_time_ns` van het footer van slot t (alleen na de switch) | recorder `block_footers` (slot, bank_id, producer-tijd, ontvangst, created) | engine: kolom `producer_latency_ms` in `signals` (NULL vóór de switch); dagrapport naast M1 |
| M3 | Triton-kant: `created_unix_ns − block_producer_time_ns` per blok (certificaat-/serverlag) en `received − created` (netwerk) | beide tijden in `block_footers` en `arrivals` | dagrapport: mediaan/p90 per dag uit de segmenten (leesscript, geen enginewijziging) |
| M4 | Slottijd: ms per slot per uur en per epoch; d in slots dat werkelijk gold | `LiveFeed.clock.per_epoch` (na 1.000 slots), `d_ms` per signaal; `blocks.block_time` | `state`-rij `ms_per_slot_<epoch>` in het journal + uurmeting uit `block_producer_time_ns` in het dagrapport |
| M5 | Skip-rate en tx per blok | recorder `blocks`: `skipped`, `executed_tx_count`, `entry_count` | dagrapport: aandeel skipped, mediaan tx/blok, vóór/na |
| M6 | De switch zelf: eerste footer-slot, slotsprong, late/onbevestigde rijen, reconnects | `anomalies` `alpenglow_first_footer` + `alpenglow.json`; `heartbeat.gap_slots` (> 25); segment-meta `late_rows`, `unconfirmed_slots`, `reconnects` | één regel in het dagrapport van 21 okt (feit, geen oordeel) |
| M7 | Meerdere banken per slot | `block_footers.bank_id` | telling `bank_id > 0` per dag (verwacht 0 tot Agave 4.4) |
| M8 | Fills en landing: paper-fill vs. eerste echte trade in de pool na ons instap-slot; `entry_failed`, `exit_attempts`, `reverted_*`, `sells_dropped/scaled` | journal `orders.status/reason`, `fills`, `exits`; `paper.reconcile` (rijgelijkheid live ↔ OF1) | reconciliatie per dag van E32 (al gepland) levert de fill-afstand; geen nieuwe kolom |
| M9 | Kosten in de keten: mediaan prioriteitsfee en tip van pump-trades, aandeel gefaalde tx | store (fee, tip, outer program) en `tx_ctx` op de NAS | NAS-job op de forward-store ≥ 1064 (na `fwd.lock`): per dag vóór/na, niet eerder |
| M10 | Datagebruik Triton | journal `usage` (bytes per stream per minuut) | — (votes zitten niet in ons abonnement; verwacht geen verschil) |

Minimale set vóór 21 okt: M2 en M4 (twee kolommen, één leesscript); M3/M5/M6/M7 zijn leesscripts over de segmenten en kunnen na de switch. M9 wacht op de forward-store.

## 4. Open punten

- Jito noemt geen begin- of eindtijd van Maintenance Mode en geen effect op tips [S5]; de aandelen BAM-stake vóór/na zijn *onbekend* voor ons (geen bron die wij lezen).
- De ~150 ms is een doel; de echte certificaatlatency (M3) is de eerste meting die telt.
- De Clock-regel "parent + 0 tot ingehaald" kan `latency_ms` (M1) na de switch vertekenen; M2 is dan leidend. Vóór de switch is M1 de enige maat.

## 5. Bronnen

- S1 solana.com, *Alpenglow* (upgrades): <https://solana.com/upgrades/alpenglow> — activatie epoch 1064, 5.000 slots, slot 459.653.000, confirmed = finalized, votes uit blokken, Clock-formule, ticks, bank_id, VAT, fast leader handoff in 4.4.
- S2 solana.com, *Reduced slot times*: <https://solana.com/upgrades/reduced-slot-times> — vier stappen, laatste 200 ms op epoch 1053 (slot 454.896.000, 9 okt 2026); 4-slot leader window en 432.000 slots per epoch ongewijzigd.
- S3 Anza, *Alpenglow* en *Commitment status*: <https://docs.anza.xyz/consensus/alpenglow>, <https://docs.anza.xyz/consensus/commitments> — 150 ms-doel, tiers, footer met finalisatiecertificaat, confirmed/finalized-definities.
- S4 Triton, *Alpenglow migration: avoid the misinformation*: <https://blog.triton.one/alpenglow-migration-avoid-the-misinformation/> — volgorde van de omschakeling, 2 slots zonder gebruikerstx, preconfs ~10 min uit, lees op confirmed, `getAgGenesisCert`, geen fee-wijziging.
- S5 Jito/BAM, *Update on BAM readiness for Alpenglow on Mainnet* (5 okt 2026): <https://bam.dev/blog/update-on-bam-readiness-for-alpenglow-on-mainnet/> en changelog 9 okt: <https://bam.dev/blog/bam-changelog-sept-23-oct-9/> — Maintenance Mode, Block Engine-bundels blijven, MPP/DBI/FireBAM uit, veiling 25 → 20 ms.
- S6 SIMD-0326 *Alpenglow* (tekst): <https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0326-alpenglow.md> — certificaattypen en drempels, lokale timeouts, skip-votes, geen slottijdwijziging.
- Eigen metingen: `store/dev2.duckdb`, `fwd1052–1054.duckdb` meta `ms_per_slot`; code `lab/paper/engine.py` (`_latency`, `_d_at`), `lab/paper/live.py` (slottijd per epoch), `lab/recorder/src/segment.rs` (`FOOTER_COLS`, `ARRIVAL_COLS`, `BLOCK_COLS`), readiness-notitie `research/alpenglow-readiness-2026-10-14.md` (Lab Backtest).
