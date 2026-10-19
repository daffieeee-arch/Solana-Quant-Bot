# Meetplan eerste paperweek (20–27 oktober 2026) — LAB research, v1.0 (2026-10-19)

Wat LAB research vanaf de paperstart meet op de live-journals en de recorder-segmenten, in welke volgorde, en wat daarvoor van de coördinator nodig is. Alles is **meting, geen claim**: de stopregel en de forward-toets (E32) beslissen; dit plan levert de tellingen, de vóór/na-vergelijking en de controle van de aannames daarachter. Labels per uitkomst: feit (geteld), reconstructie (afgeleid), modelaanname, onbekend.

**Kader (vast).** Bronnen: de paper-journal (SQLite + dag-exports), de recorder-segmenten en de keeper-kopieën, de dagrapporten (`paper.report`) en, voor de verwachting, dev 1033–1046 en de fwd1053-acceptatie-journals. Geen hold-out 1047–1051, geen schone set 1055–1063; de forward-store ≥ 1064 pas na publicatie en alleen binnen E32. Geen code in engine, feed, live, strategies of store: alleen leesscripts onder `lab/wallets/` (en `paper/alpenglow_metrics.py` als de coördinator erom vraagt), read-only op de journal (sqlite3 read-only of de dag-parquets). Alles na de switch is één nieuw regime (besluit 2026-10-14), nooit gepoold met dev.

## 1. Volgorde

| # | wanneer | meting | bron | wat het oplevert |
|---|---|---|---|---|
| A1 | 20 okt, eerste uren na de start | **Loopt het zoals gepland:** rijen per tabel per uur, heartbeat-gaten > 25 slots, `usage` bytes per stream tegen het dagbudget (M10), `state` farmer_seed (`complete_from`) en `ms_per_slot_<epoch>`, eerste F7-signalen met hun status | journal | één regel per uur in mijn notitie; afwijking → melden, geen oordeel |
| A2 | 21 okt vóór 14:50 UTC | **Vóór-basislijn:** M1 (latency per trigger, dagrapport), M4 (ms/slot per uur), M5 (skip, tx/blok) over de segmenten tot dan; getallen vastgezet vóór de switch | `alpenglow_metrics --recorder --kept --journal`, dagrapport | de "vóór"-zijde op dezelfde meetlat |
| A3 | 21 okt na de switch | **De switch zelf:** M6 (eerste footer-slot, slotsprong, late/onbevestigde rijen, reconnects), M7 (banken per slot), start van M2 (producent-latency) | segmenten, `alpenglow.json`, journal | één feitregel voor 21 okt |
| A4 | dagelijks 21–27 okt | **K3-tellingen tegen de verwachting:** F7-reversed-triggers/dag, aandeel organic, status (entered / busy / farm flagged / risk-reden), `entry_failed`, fills, exit-redenen (tp / sl / time), netto per gevulde trade, N2-schaduw; per dag naast de verwachting (§2) | journal `signals`/`orders`/`fills`/`exits`, dagrapport | een tabel per dag; dag buiten ½–2× de verwachting gemarkeerd (diagnose, geen besluit) |
| A5 | 24 okt (D+3) en 27 okt (D+7) | **Alpenglow vóór/na, M2–M7** per UTC-dag en per zijde; M2 ook tegen `d_ms` (aandeel triggers later dan d) | `alpenglow_metrics` op segmenten + keeper-kopieën + journal | de tabel van idee 5 (d na Alpenglow): alleen meten, een andere d is een nieuwe trial |
| A6 | zodra de bron er is (§3) | **LiveFeed tegen store, farmer-vlaggen en signalen** (vóór M3): per trigger het paper-label (`farm`, `farm_window_complete`) tegen het store-label van dezelfde chunk, en de signaalrijen via `paper.parity` / `journal_diff --scope signals`; de drie bekende verschillen (float-grens, h24 per chunk, venster dat alleen vooruit schuift; ontwerp §C1b) elk geteld | parity-run op een chunk met segmenten én store | het getal waarmee de coördinator vóór M3 beslist: Python-LiveFeed exact maken of Rust kopieert het met een gedocumenteerd verschil |
| A7 | wekelijks (E32) | M8 fill tegen de eerste echte trade in de pool na ons slot; `exit_attempts`, `reverted_*`, `sells_dropped/scaled` | `paper.reconcile`, journal | de fill-afstand; hoort bij E32, hier alleen gelezen |
| A8 | als Lab Backtest de schema's stuurt | Triton-metingen punten 1, 7, 4, 5 (uitvoeringssucces van opgenomen transacties, fee-markt, drain-voorbode, bekende wallets): leesscripts op `mint_checks`, `fee_checks`, `drain_watch/`, signal_trades | journal-tabellen van de spec v1.3 | na 7 paperdagen het gemeten kostenscenario (agenda-idee 2) |

Niet deze week: M9 (kosten in de keten) wacht op de forward-store ≥ 1064; de drain-voorbode op dev (agenda-idee 1) wacht op job 35; M-b/M-c (Rust-doc §3) komen na M-a.

## 2. De verwachting voor A4

Twee bronnen, met verschillende betekenis:

- **Trigger-niveau, dev 1033–1046 (feit, toernooi-parts f7rev, tp4_T600, d = 2, q = 0,5, 20 dagen):** 1.906 F7-reversed-triggers per dag (mediaan 1.746), 32 % organic, 7,4 % `entry_failed`; exit-mix take-profit 66 %, stop-loss 33 %, time 0,4 %; gemiddeld +0,71 % per gevulde trade (mediaan +4,68 %: tp4 raakt vaker dan niet, de staart verliest). Het toernooi kent geen busy, risicolimieten of cooldown: elke trigger is een positie. Daarom is dit **alleen** de verwachting voor triggers/dag, organic-aandeel, `entry_failed` en de exit-mix.
- **Paper-niveau (risico aan: 5 open, 2,5 SOL, −1 SOL/dag, 24 h pool-cooldown, organic-only voor H):** de fwd1053-acceptatie-journals van M1 C2/C3 (R2, RC1–RC3) op 218 ms zijn de enige run met de paper-regels in het huidige regime; daaruit komen posities/dag, aandeel busy en risk-skips en netto/dag. Ik heb ze niet op de VPS (§3). Zonder die kopie staat in de dagtabel alleen de trigger-verwachting, en de paper-telling zonder referentie.
- **Hold-out (alleen ter herinnering, geen invoer):** K3 d2_q0.5 +0,02 % [−0,90 … +0,61] per trade; wordt niet tegen de paperweek gelegd.

Afwijkingen > 2× of < ½× van de verwachting per dag worden gemarkeerd en per oorzaak nagelopen (recorder-gat, universum, seed incompleet, regime), niet "verklaard".

## 3. Wat ik van de coördinator nodig heb

1. **Paden op de VPS** (lezen): journal-map van K3 en groep H, recorder-root (`segments/`, `alpenglow.json`), keeper-map (`kept/`), backup-map met de dag-exports, en de map van de dagrapporten. Eén regel volstaat.
2. **Kopie naar de VPS:** de fwd1053 paper-journals met risico aan (M1 C3 RC1–RC3 en lab-live paper R2), als parquet-dagexports of sqlite, onder `data…/lab/journal/`. Dat is de paper-niveau-verwachting van §2 (klein, geen NAS-rekentijd).
3. **NAS-job voor A6:** `paper.parity` (engine op segmenten tegen de replay op de store) op een chunk waarvoor beide bestaan. Dev 450144000–450360000 is al identiek (3.053 van 3.053, parity.md), maar daar verandert h24 niet. Nodig is een fwd1053-chunk met recorder-segmenten (de meeting van 14 oktober?) tegen de fwd1053-store; de signaal- en labelverschillen per oorzaak tel ik uit de twee journals. Bestaan er geen fwd1053-segmenten, dan valt A6 samen met E32-reconciliatie punt 2 (paper-label tegen store-label op de forward-store ≥ 1064) en is het getal er pas ná OF1-publicatie; dat moet de M3-planning dan weten.
4. **Bevestiging** dat de recorder ≥ 24 h vóór de switch draait (anders geen "vóór"-zijde) en van het switch-slot zodra `alpenglow.json` er is.
5. **Lab Backtest:** de schema's van `mint_checks`, `fee_checks`, `drain_watch/` en signal_trades zodra ze bestaan (A8).

## 4. Uitvoer

- Een werknotitie `lab/reports/2026-10-27-paperweek1.md` met de A4-dagtabellen, de A5-tabel vóór/na en de A6-telling; tussenstand op 24 oktober als korte sectie in dezelfde notitie (PR per keer). Leesscript `lab/wallets/paperweek.py` (nieuw, read-only; K3-tellingen per dag uit de dag-exports met de verwachting ernaast; hergebruikt `paper.report`-functies waar dat kan).
- Registerrijen: A4/A5 als één meetrij (E4x), A6 als eigen rij, met sha van de notitie.
- Op de VPS alleen leesqueries (eigen scope, ≤ 1 GB DuckDB); alles ≥ 10 min of ≥ 5 GB als NAS-job via de coördinator.
