# Track record van de creator als point-in-time kenmerk op de curve: verschuift de kansen, maar niets wordt winstgevend (agenda-idee 3)

LAB research, 2026-10-17. Vraag: is de geschiedenis van de creator (eerdere tokens, hoe die eindigden) op het moment van de creatie een kenmerk dat de curve-instapmomenten (c1/c5/c15, E27: ongefilterd allemaal negatief) laat omslaan? Alleen dev 1033–1046 (runners-v2, 612.980 instappen zonder mayhem); dag 1–9 en 10–18 apart. Verkennend; register E38. Labels **feit** / **modelaanname**. Script `lab/wallets/explore/creator_track.py` → `wallets/winners-v1/creator_hist.parquet`, `creator_entries.parquet`.

## 0. Samenvatting

- Per token is op het moment van de creatie bekend: hoeveel tokens de creator eerder maakte (508.726 mints, 160.797 creators; 67 % van de tokens heeft een creator met eerdere tokens), hoeveel daarvan migreerden, en hoeveel een ×2-runner waren in de 6 uur na een c1-instap (alleen geteld als dat 6-uursvenster vóór de nieuwe creatie afliep: point-in-time) — *feit*.
- De klasse verschuift de runner-kans en het verlies, maar **elke klasse en elke bucket blijft diep negatief** voor een 0,5 SOL-instap op c1/c5/c15 (gemiddeld −0,14 … −0,46 SOL per instap bij hold 6 u / tp ×2 / trail 30 / trail 50, kosten base, geen vertraging): eerste token van de creator: ×2-kans 9–10 %, netto −0,26 … −0,36; creator met eerdere tokens zonder bekende uitkomst of zonder migratie/runner: 2–4 %, −0,36 … −0,46; creator met een eerdere ×2-runner: 6–7 %, −0,29 … −0,39. De beste deelgroep — creators met ≥ 3 bekende eerdere uitkomsten waarvan ≥ 50 % runners (n 196 / 409) — haalt 20–23 % ×2-runners en toch −0,14 … −0,20 per instap — *modelaanname (runners-v2-marks, simpele exits)*.
- Idee 3 sluit: een creator-filter maakt de curve-momenten minder slecht, niet winstgevend; het past bij E27 ("runners vroeg op de curve vinden is niet winstgevend bij ≥ 600 ms"). Voor de reversed-markt (F7) is er geen creator-historie: die tokens zijn geen pump-mints (`prior_mints` NULL voor alle 38.502 f7-instappen).

## 1. Tabel (c1, 0,5 SOL, geen vertraging; dag 1–9 / dag 10–18)

| creator-klasse bij de creatie | n | ×2 binnen 6 u | ×5 | netto tp ×2 (SOL) | netto trail 30 (SOL) |
|---|---|---|---|---|---|
| A eerste token | 33.238 / 71.652 | 9,8 % / 9,3 % | 2,0 % / 2,6 % | −0,34 / −0,36 | −0,26 / −0,29 |
| B eerdere tokens, geen bekende 6 u-uitkomst | 15.804 / 22.507 | 3,6 % / 3,1 % | 1,1 % / 0,8 % | −0,41 / −0,44 | −0,37 / −0,39 |
| C eerdere token gemigreerd, geen runner | 1.839 / 1.108 | 6,3 % / 7,6 % | 1,4 % / 2,9 % | −0,33 / −0,34 | −0,28 / −0,26 |
| D eerdere token was een ×2-runner | 24.288 / 47.230 | 6,4 % / 6,5 % | 1,5 % / 1,6 % | −0,37 / −0,39 | −0,29 / −0,30 |
| E eerdere tokens, geen migratie, geen runner | 20.150 / 33.255 | 3,1 % / 2,1 % | 0,7 % / 0,5 % | −0,41 / −0,44 | −0,36 / −0,40 |

Aandeel eerdere runners bij creators met ≥ 3 bekende uitkomsten (c1): 0 % → ×2-kans 2–3 %, −0,41 … −0,44; < 20 % → 5–6 %, −0,39 … −0,40; 20–50 % → 12–13 %, −0,29 … −0,31; ≥ 50 % → 20–23 % (n 196 / 409), −0,17 … −0,20 (tp ×2) en −0,14 … −0,15 (trail 30). c5 en c15: hetzelfde patroon, iets minder negatief op c15 (−0,25 … −0,41).

## 2. Oordeel

Geen hypothese, geen vastlegging. De creator-historie is een echte scheiding (de ×2-kans loopt van 2 % tot 23 %) maar de curve-markt verliest bij elke klasse: de meeste tokens eindigen in de eerste 6 uur op 10–30 % van de instap (`end_6h` 0,07–0,47), en de runners betalen dat niet terug. Wat overblijft: de klassen B en E (eerdere tokens zonder migratie of runner, 34 % van de c1-instappen) zijn een zuivere **uitsluiting** mocht er ooit een curve-signaal komen; en de ≥ 50 %-runner-creators (0,2 % van de instappen) zijn een watchlist-idee voor realtime gedrag (Triton-lijst, punt 5), geen regel.
