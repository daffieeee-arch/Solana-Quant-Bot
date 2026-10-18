# Verkenning agenda-ideeën 1 en 2: drainrisico per creator-klasse, en de grote koop op een jonge curve

LAB research, 2026-10-17. Eerste stappen van `2026-10-17-onderzoeksagenda.md`, idee 1 (drainrisico vooraf) en idee 2 (nieuw instapmoment: grote koop op een jonge curve). Alleen dev 1033–1046; hold-out en clean niet gelezen. Verkennend, elke proef in het register (E34, E35). Labels **feit** / **reconstructie** / **modelaanname** / **onbekend**. Scripts `lab/wallets/explore/drain_classes.py`, `whale_epoch.py`.

## 0. Samenvatting

- **Idee 1, stap 1.** Het fabrieksveto uit K2 raakt de H4/H5-posities nauwelijks (2 % van de posities). Wat wél scheidt is de **klasse van de creator**, bekend bij de poolcreatie: pools met **onbekende creator** (het veld staat op het system-program; 32 % van alle reversed pools op dev, 80–93 % binnen 7 minuten gedraineerd, elke dag) zijn 19–20 % van de H4/H5-posities en op dag 10–18 de hele staart: Σ −32 … −40 SOL, 28–30 % volledig verloren. Een veto erop maakt dag 10–18 +29 … +39 SOL beter, de slechtste dag van −7,5 … −8,7 naar −1,2 … −2,1 en het verlies-alles-aandeel 8 → 3 %. **Maar** op dag 1–9 (de 320 ms-dagen) waren diezelfde pools winstgevend (+23 SOL, 6–10 % verloren): het effect is niet stabiel over de blokken, dus geen claim — wel de sterkste kandidaat voor een point-in-time veto in een volgende forward-ronde. Benoemde seriële creators (≥ 100 eerdere pools) zijn klein en ≈ 0; "eerste pool van de creator" en creators met 1–9 eerdere pools zijn de beste klassen.
- **Idee 2, stap 1.** Over heel dev zijn er 39.423 kopen ≥ 10 SOL op curves ≤ 5 min oud; **79 % is de dev-buy van de creator zelf** in de create-transactie (mediaan leeftijd 0 s). Een 0,5 SOL-volger 3 slots later verliest in **elke** deelverzameling: creator-kopen −0,04 … −0,07 SOL per positie (winstkans 9 %), niet-creator-kopen −0,02 … −0,04 (17–20 %); de enige cel met plus op dag 1–9 (niet-creator, ≥ 20 kopers vóór de koop: +0,017 bij 5 min, n 258) is op dag 10–18 −0,07 (n 215). Een top van ≥ 2× de instap binnen 15 min komt in 4–5 % voor. **Dood spoor** bij ≥ 600 ms; het geld van de "whale-pumps" uit winnaars II zit bij de koper zelf, niet bij wie volgt.

## 1. Idee 1, stap 1: wat zit er in de drains van H4/H5 (feit, dev)

Data: H5 = `bigtp-dev-1033-1046-4dbd34b` (flow300_tp100_H6, organic, d 1/2: 3.089 / 3.222 posities), H4 = `long-dev-1033-1046-fc4173c` (flow300_H6, organic, d 1/2: 2.934 / 3.075); netto per positie via `costs.settle_sql` (base). Alle 6.000+ instappools zijn in dev gecreëerd (`k2_pools.parquet`: 51.248 reversed pools), mediaan leeftijd bij de trigger 4 min. "Verlies-alles" = proceeds 0 of geen exit.

**Fabrieksveto (K2, R1: eenmalige creator die nooit handelde + b0 ≥ 300 SOL):** raakt 7–9 posities per cel; Σ-effect ±2 SOL. *Correctie 2026-10-18 (Codex 7): 'nooit handelde' was over de hele periode geteld; point-in-time (`creator_traded_before`) verschuift R1 met 17 pools op 3.535 (K2-noot, correctie bovenaan); het veto-effect hier (±2 SOL, 2 % van de posities) is niet herdraaid. De creator-klasse (system-program, eerdere pools) is wél point-in-time en staat los hiervan.* Irrelevant voor H4/H5: de F7-trigger komt in die pools zelden, en als hij komt is het ná de cyclus.

**Creator-klasse op het moment van de poolcreatie** (point-in-time; H5 d = 1, dag 1–9 / dag 10–18):

| klasse | n | Σ SOL | verlies-alles | gem. per positie |
|---|---|---|---|---|
| eerste pool van de creator | 147 / 340 | +8,9 / +32,8 | 9 % / 3 % | +0,06 / +0,10 |
| creator met 1–9 eerdere pools | 206 / 722 | +31,4 / +68,4 | 8 % / 4 % | +0,15 / +0,09 |
| creator met 10–99 eerdere pools | 274 / 575 | +9,4 / +34,6 | 4 % / 3 % | +0,03 / +0,06 |
| **creator onbekend (system-program)** | 156 / 438 | **+22,7 / −31,6** | 6 % / **28 %** | +0,15 / −0,07 |
| benoemde seriële creator ≥ 100 pools | 0 / 231 | – / +5,8 | – / 0 % | – / +0,03 |

H4 d = 1 hetzelfde beeld (onbekend: +23,3 / −38,7, 10 % / 29 %); d = 2 idem. Per regime: onbekend 320 ms +80,5 SOL (485 posities, 7 % verloren), 270 ms −132,0 (1.848, 28 %).

**Veto "creator onbekend"** (per h, d, dagblok: Σ met → zonder; slechtste dag met → zonder):

| h | d | dag 1–9 | dag 10–18 | slechtste dag 1–9 | slechtste dag 10–18 |
|---|---|---|---|---|---|
| H4 | 1 | 96,5 → 73,2 | 70,4 → 109,2 | −1,3 → −1,3 | −8,7 → −1,8 |
| H4 | 2 | 81,0 → 57,6 | 59,9 → 99,8 | −1,3 → −1,3 | −8,1 → −1,2 |
| H5 | 1 | 72,4 → 49,7 | 110,0 → 141,7 | −1,3 → −1,3 | −7,5 → −1,9 |
| H5 | 2 | 72,1 → 49,5 | 95,4 → 128,7 | −1,3 → −1,3 | −6,9 → −2,1 |

Universum-feit: van de 16.507 reversed pools met onbekende creator wordt 80–93 % binnen 7 min na de eerste trade gedraineerd, elke dag van dev (de aantallen per dag dalen van ~1.200 naar ~200 eind september). Bij "eerste pool van de creator" daalt dat drainaandeel van 86–94 % (12–16 sep) naar 33–55 % (23 sep–1 okt). Wat "creator onbekend" technisch betekent (welk create-pad vult het veld niet) is *onbekend*; vraag aan Lab Backtest.

**Lezing.** De klasse is een echte scheiding in het universum (drains) en op dag 10–18 ook in de P&L, maar in de 320 ms-dagen leverden dezelfde pools winst: de F7-trigger kwam daar kennelijk vóór de drain. Eén blok is geen bewijs; het is een kandidaat-veto (point-in-time, bekend bij creatie) voor een overlay-proef op de forward-store, náást H4/H5, niet in plaats van. Niets verandert aan E31/E32 — *reconstructie*.

## 2. Idee 2, stap 1: de grote koop op een jonge curve (modelaanname, dev)

Definitie: eerste koop ≥ 10 SOL op een pump-curve ≤ 5 min na de creatie (mint in de periode, geen mayhem); volger: 0,5 SOL op slot + 3 tegen de gelogde post-toestand, verkoop na 60 / 300 / 900 / 3.600 s (curve-formule, fee-bps van de koop, 2 × 0,000705 SOL kosten; een voltooide curve vóór de exit = geen uitkomst, in 0 % van de gevallen).

| deelverzameling | n | gem. netto 60 s | 300 s | 900 s | 3.600 s | winstkans 300 s |
|---|---|---|---|---|---|---|
| creator-koop, dag 1–9 | 21.904 | −0,037 | −0,046 | −0,051 | −0,058 | 10 % |
| creator-koop, dag 10–18 | 9.242 | −0,046 | −0,055 | −0,063 | −0,069 | 9 % |
| niet-creator, dag 1–9 | 5.475 | −0,024 | −0,023 | −0,030 | −0,033 | 17 % |
| niet-creator, dag 10–18 | 2.802 | −0,029 | −0,033 | −0,041 | −0,042 | 20 % |
| niet-creator, ≥ 5 kopers vóór, dag 1–9 | 441 | −0,021 | +0,006 | −0,015 | – | 32 % |
| niet-creator, ≥ 5 kopers vóór, dag 10–18 | 374 | −0,048 | −0,070 | −0,099 | – | 31 % |

Naar prijsmultiple vóór de koop (niet-creator): 1,5–3× en 10–20 SOL −0,004 (n 525, 33 %), < 1,5× −0,028, ≥ 3× −0,02; naar grootte: ≥ 20 SOL niet beter. Top ≥ 1,5× / ≥ 2× de instap binnen 15 min: 15 % / 5 % (niet-creator), 11 % / 4 % (creator).

**Lezing.** De "whale-pump" is bijna altijd de creator die zijn eigen token vult; wie 3 slots later volgt koopt de top. De niet-creator-kopen na anderen zijn precies de groep-C-scalpers van winnaars II, en ook achter hén verdient een volger niets zonder hun uitstapmoment. Idee 2 sluit als instapmoment; wat overblijft is dat een grote koop op een jonge curve een *uitsluitings*-signaal is (de top is gezet) — *reconstructie*.

## 3. Wat nu

- Idee 1 gaat door met de creator-klasse (incl. "onbekend") als kernkenmerk, plus de D-hazard, als point-in-time veto-overlay; volgende stap: de E27-kwintielmethode op de H4/H5-triggers met creator-historie (idee 3 schuift hierin), en de vraag aan Lab Backtest wat het lege creator-veld betekent.
- Idee 2 is gesloten (E35). Idee 4 (ondraineerbare pools) wacht op de bandbreedte; K1 (idee 5) komt na de clean-rapporten.
