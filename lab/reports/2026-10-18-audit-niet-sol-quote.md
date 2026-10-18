# Audit niet-SOL-quote-curves in het wallet-onderzoek (2026-10-18)

LAB research, 2026-10-18, op verzoek van de coördinator na PR #363. Vraag: welke conclusies in het register die nog meewegen in beslissingen rusten op een script dat `s.curve` zonder quote-filter las, en wat betekent dat per conclusie: **niet geraakt**, **geraakt maar klein** (richting gelijk, getallen verschuiven) of **moet opnieuw**. Labels **feit** / **reconstructie** / **modelaanname**.

## 0. Het gat (feit)

- 82.143 van de 707.011 mints in de dev-store (11,6 %) hebben een andere quote-mint dan SOL (`s.mints.quote_mint`: PUMP (`pumpCmXqMfrs…`), DOGE-achtige tokens, …). Op die curves staat `s.curve.q` in eenheden van dat token, niet in lamports; de store laadt voor hun migratiepools geen trades (`quote_class = 'other'`).
- De engine en de paper-kant filteren: `strategies.py:325` (curve-signalen `m.quote_mint IN (…)`), `:343` (pools `quote_class IN ('sol','reversed')`), `store.py:499/530` (sweep); `cases.py`, `money.py` (money-v1, `SOL_QUOTES`), `episodes.py`, `clusters.py`, `runners1b.py` filteren ook. K3/F7 en H4/H5 zijn niet geraakt.
- Zonder filter lazen: `winners/slow_legs.py`, `slow_replay.py`, `closed.py`, `profile.py`, `feasibility.py`, `k1_rolling.py` (gefixt in #363), `runners.py`, `mev0.py`, `markout.py`, `transfers.py`, `transfers_diag.py`, `phantom.py`, `explore/whale_epoch.py`.
- Vanaf deze PR: `wallets/common.py` heeft `SOL_KEY`, `SOL_QUOTE` (curve, met `mints m`) en `POOL_SOL` (pools); de test `wallets/tests/test_quote_filter.py` weigert elk script onder `lab/wallets` dat `curve` leest zonder `quote_mint`/`SOL_QUOTE` of een `# no-quote-filter: <reden>`-marker. De oude scripts dragen die marker (verbruikte data, hier geauditeerd).

## 1. Per conclusie

Gemeten op de bewaarde uitvoer op de VPS, gejoind met `s.mints` (lichte queries, alleen dev).

| register | script | conclusie die meeweegt | aandeel niet-SOL | SOL-only uitkomst | oordeel |
|---|---|---|---|---|---|
| E17 (MEV0, #206) | `mev0.py` | NO-GO fase 1 (top-3 84 %, nieuwkomers 3 %, mislukt ≤ 97 %) | 240 van 46.966 arbs met een store-mint (0,5 %; 84 van 1.709 SOL bruto); 264.925 arbs zonder store-mint zijn pool/andere-DEX-legs (SOL) | criteria rusten op aandelen van spelers en mislukte pogingen, niet op curve-SOL | **niet geraakt** |
| E11/E22 (markout) | `markout.py` | markout-curves per signaal | 0: de signalen komen uit de engine-parts (al SOL-gefilterd); markouts zijn prijsratio's | — | **niet geraakt** |
| E04/E10/E15/E19/E22/E26 (transfers, insiders) | `transfers.py` | share_c, funder-graaf, transfer-gevoede verkoop; checks 4.1/4.2 | mints in het venster incl. niet-SOL; de SOL-kolom van een niet-SOL-curve is intern consistent per mint-dag (check 4.1 haalde 100 %); share_c telt per engine-trigger (SOL-pools) | — | **niet geraakt**; `transfers_diag.py` is diagnose |
| E05 (phantom) | `phantom.py` | illustratie van de eigenaarslijst | onbekend per token | geen beslissing | **niet geraakt** (illustratie) |
| E24 (winnaars I) | `closed.py`, `profile.py`, `feasibility.py` | "geen archetype haalbaar bij ≥ 600 ms": vertraagde replay maakt elk archetype negatief | feas_replay: 2.929 van 14.287 posities (20 %), maar Σ inzet 254 k eenheden tegen 21 k SOL; closed_wmd: 260 k van 3,76 M wallet-mint-dagen, kas 510 k eenheden tegen 245 k SOL | per archetype op SOL-curves: A0 −0,47, A1 create-sniper −1,27, A2 scalper −0,23, A3 −0,32, A5 −1,02 SOL per positie bij d = 3; hun eigen resultaat positief (A1 +2.929, A2 +844 SOL). Richting gelijk; de genoemde "+1,98 → −1,25/−2,10" was gemengde munt (SOL-only scalpers +0,11 → −0,23) | **geraakt maar klein**; het strenge label (797 wallets, groep C) was gemengd en leeft alleen door in E33/E39, zie daar |
| E27 (runners, spec F §1) | `runners.py` | curve-runners niet winstgevend: elk kwintiel bij elke exit negatief | c1 32 k van 276 k instappen (12 %), c5 11 %, c15 13 % | SOL-only gemiddelde exits in eenheden van de kosten: tp2 0,21–0,30, trail30 0,32–0,36, trail50 0,28–0,33 (alles < 1 = verlies); ×2-aandeel binnen 1 u 4–6 % | **geraakt maar klein** |
| E33 (winnaars II) | `slow_legs.py`, `slow_replay.py` | "geld zit op de curve", grote inzetten op jonge curves, K1/K2-kandidaten | 5,7 % van de curve-posities, maar Σ 653 k eenheden tegen 117 k SOL; bij eerste koop ≥ 5e9 29.038 tegen 4.640 | niet opnieuw uitgesplitst (verbruikte data); K2 (reversed pools) niet geraakt | **moet opnieuw** voor zover het de K1-vraag betreft → gedekt door de SOL-only K1-herrun (NAS, #363); de beschrijvende sommen blijven gecorrigeerd in het register |
| E35 (walvis-koop op jonge curve) | `whale_epoch.py` | dood spoor: volger overal negatief, grote koop = uitsluitingssignaal | 20.162 van 39.423 events (51 %) | SOL-only: volger −0,039 / −0,043 / −0,056 SOL per positie (60 s / 300 s / 1 u); creator-aandeel 67 % (was 79 % gemengd) | **geraakt maar klein** |
| E39 (K1-houdbaarheid) | `slow_replay.py` | +12,3/dag kandidaat (al ingetrokken door E40) | 64 % van de E40-rondes ≥ 5 'SOL' | — | **moet opnieuw** → gedekt door de K1-herrun |
| E40 (K1 rollend label) | `k1_rolling.py` | dood spoor (−38,5/dag) | 64 % van de rondes ≥ 5 'SOL'; 1.492 van 1.763 gelabelde wallets | smoke met filter: plafondgroep +0,20 → −0,26 per ronde | **moet opnieuw** → NAS-herrun op de #363-squash (één job: k1_rolling, k2_pools, k2_sim) |

## 2. Herberekeningen

Eén NAS-opdracht, al aangevraagd bij #363: `k1_rolling` (SOL-only, point-in-time) plus `k2_pools`/`k2_sim`. Niets anders kan een conclusie omdraaien: E24, E27 en E35 zijn op de bewaarde uitvoer SOL-only herrekend en blijven negatief; E17, markout en transfers lezen geen curve-SOL in hun criteria. De agenda v2 (idee 1 drains, 2 kosten, 5 Alpenglow) rust op pools en engine-uitvoer, niet op deze scripts.

## 3. Open

- E24's beschrijvende tabellen (top-100-archetypen, persistentie 55–58 % tegen 10 %) zijn niet SOL-only herrekend: ze sturen geen beslissing meer (route = tijd-ongevoelige signalen, H3/H4/H5).
- Of de insider-ledger van `transfers.py` bij PUMP-quoted curves de PUMP-stroom als 'SOL' in Q3 (transfer-gevoede verkoop) meetelt is niet nagekeken; Q3 is beschrijvend (segment), geen criterium.
