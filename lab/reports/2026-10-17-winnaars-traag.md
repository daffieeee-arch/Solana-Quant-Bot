# Leer van de winnaars II: trage archetypen in groep C (dev ≤ 1046)

LAB research, 2026-10-17. Vervolg op `2026-10-15-winnaars.md` (E24): daar bleek snel kopiëren dood (reactielatentie 1 slot, vertraagde replay negatief). Opdracht (coördinator, 2026-10-16): kijk in groep C (797 wallets) naar de **trage** archetypen, houdtijd minuten tot uren, met instaptrigger, pooltype en grootte; welke overleven onze 2–3 slots vertraging; open rondes ook op de laatste prijs. Alleen dev-data epochs 1033–1046; hold-out (1047–1051) en clean (1055–1063) niet gelezen. Verkennend, geen lat; labels **feit** / **reconstructie** / **modelaanname** / **onbekend**.

## 0. Samenvatting

- Groep C verdient op de **curve**, en vooral snel: van Σ +781 k SOL op gesloten rondes (dev, 18 dagen) komt 393 k uit houdtijden ≤ 1 min. De trage curve-rondes zijn wel groot: 1–5 min +163 k, 5–15 min +114 k, 15–60 min +73 k, 1–6 u +14 k, > 6 u +12 k. Pools (normaal) leveren honderden SOL, reversed pools ±1,3 k uit één cluster van 25 wallets.
- De trage curve-winst zit in **grote inzetten op jonge curves**: posities > 20 SOL (3–5 % van de trage rondes) leveren 146 k / 93 k / 63 k SOL in de buckets 1–5 / 5–15 / 15–60 min, gemiddeld 45–92 SOL per positie; instap mediaan binnen 1 min na create, op 1,5–3× de startprijs. De koper **is** de pump: 20–80 SOL op een curve met 30 SOL virtueel verplaatst de prijs zelf ×2–×10; de winst komt van wie erna koopt — *reconstructie*.
- Per positie is het mager en scheef: mediaan verkoop/aankoop 1,01–1,04 per gesloten trage ronde, winstaandeel 51–62 %, 65–82 % van de positieve som in de top 1 % posities. Ze verkopen vóór het einde (75–88 % boven de laatste prijs; de token staat aan het einde op de helft van hun instapprijs).
- Dag 10–18 houdt het patroon maar kleiner: trage curve 1–5 min 106 k → 57 k, 5–15 min 89 k → 25 k, 15–60 min 57 k → 16 k (dag 1–9 is per constructie positief).
- Vertraagde replay (§5, steekproef 400 gesloten rondes per cel): met hún inzet verliest een kopie op 3 slots het meeste (curve 1–5 min −0,64 SOL per ronde; ≥ 1 u negatief); met **0,5 SOL** levert het volgen van trage curve-instappen (≥ 5 min) +0,04…+0,06 SOL per ronde bij 38 % winstkans en een dunne staart (p95 +0,9), ook op dag 10–18 (+0,038, n 1.167) en vooral achter instappen ≥ 5 SOL (+0,11…+0,15). Pools: onbetrouwbaar (model overschat op lange houdtijd), mediaan negatief. Het reversed-cluster overleeft de vertraging volledig: +0,10…+0,13 SOL per ronde, 97 % winst, in beide dagblokken.
- Kandidaat-hypothesen (§6, register E33, vóór enig lezen van forward-data): **K1** het volgen van de trage curve-instap (≥ 1 SOL, liefst ≥ 5 SOL) van een persistente wallet met 0,5 SOL op 3 slots, uitstap bij hun verkoop; **K2** de fabrieks-reversed-pools in minuut 2–7. Beide missen nog een point-in-time-trigger en zijn niet vastgelegd; geen wijziging aan H4/H5/E32.

## 1. Data en methode

- **Groep C** (E24): traders met ≥ 7 van 9 labeldagen (dag 1–9, ≤ 2026-09-19) positief op gesloten rondes, > 10 SOL netto, ≥ 3 uitkomstdagen; 797 wallets — *feit*.
- **Legs**: alle curve- en pool-trades van de 797 op dev (store `dev2`, epochs 1033–1046): 5,34 M legs, 1,47 M posities (wallet × mint), kosten = tx-fee + prioriteitsfee + Jito-tip één keer per transactie — *feit*. Scripts `lab/wallets/winners/slow_legs.py`, `slow_positions.py` → `wallets/winners-v1/c_legs.parquet`, `c_pos.parquet`, `c_prices.parquet`; tabellen `slow_tables.py`, replay `slow_replay.py` → `c_replay.parquet`, fabriekscheck `slow_reversed_check.py`. Alles op de VPS in een 3G-scope (legs 2,5 min, posities 40 s, replay 14 min).
- **Ronde**: per wallet × mint: eerste koop → laatste verkoop; `closed` = verkochte tokens binnen 5 % van gekochte (1,42 M), `open` = voorraad over (31,6 k, waarvan 28,8 k nooit verkocht), `tokens_in` = meer verkocht dan gekocht (13,5 k, transfer-gevoed; buiten beschouwing, Σ +8,6 k SOL). Houdtijd in buckets (≤ 3 slots, ≤ 1 min, 1–5, 5–15, 15–60 min, 1–6 u, > 6 u) met de slottijd van het epoch (316 / 267 ms).
- **Open rondes op de laatste prijs** (opdracht): voorraad × laatste trade-prijs van de venue op dev, begrensd op de SOL-diepte van de venue aan het einde (meer dan er in zit kan niet verkocht worden). Voor de curve is die grens `vq` (inclusief de 30 SOL virtueel), dus de waarde van open curve-rondes is een **bovengrens** — *modelaanname*. Uitkomst: open curve-rondes 3.181, nog belegd 8,8 k SOL, gewaardeerd 44,4 k; maar slechts 11 % van die posities staat boven kostprijs, de som zit in enkele grote houders. Reversed/pool open rondes: ≈ 0.
- **Instaptrigger**: leeftijd van de curve/pool bij de eerste koop, prijs t.o.v. de eerste trade-prijs van de mint, en of een F7-trigger (tp4_T600, d = 1, alle farms, `exits-dev-1033-1046-befacff`) in de instappool vóór of tijdens de ronde viel.
- Niet gemeten: faalratio (tx_ctx alleen op de NAS), bundels, funder-clustering — *onbekend*. De kolom `other_payer` in §7 (100 %) was een leesfout: de store zet `fee_payer` op NULL als de trader zelf betaalt (`NULLIF(tx.fee_payer, user)`, analytics/ANALYST.md), dus `fee_payer <> trader` is NULL en `avg` telt alleen de echte derdebetalers; de juiste vorm is `fee_payer IS NOT NULL` (LAB review 2026-10-17; op dev ~9 % bij pool-legs, ~20 % op de curve). `slow_legs.py` is gecorrigeerd; de tabel in §7 is niet opnieuw gedraaid en het kenmerk speelt in de conclusies geen rol.

## 2. Waar het geld zit (gesloten rondes, dev)

| houdtijd | rondes | wallets | Σ netto SOL | winst-aandeel | mediaan | gemiddeld | top-1 % van de winst | Σ dag 1–9 | Σ dag 10–18 |
|---|---|---|---|---|---|---|---|---|---|
| ≤ 3 slots | 71.719 | 496 | +12.463 | 0,55 | 0,002 | 0,17 | 0,76 | 4.222 | 8.241 |
| ≤ 1 min | 903.438 | 791 | +392.625 | 0,49 | 0,00 | 0,43 | 0,85 | 277.012 | 115.613 |
| 1–5 min | 283.022 | 737 | +163.437 | 0,51 | 0,001 | 0,58 | 0,79 | 106.087 | 57.350 |
| 5–15 min | 95.186 | 670 | +113.955 | 0,55 | 0,01 | 1,20 | 0,80 | 89.442 | 24.513 |
| 15–60 min | 36.081 | 538 | +73.095 | 0,62 | 0,05 | 2,03 | 0,82 | 57.401 | 15.693 |
| 1–6 u | 16.866 | 403 | +13.696 | 0,59 | 0,02 | 0,81 | 0,70 | 7.533 | 6.163 |
| > 6 u | 16.602 | 407 | +11.519 | 0,61 | 0,03 | 0,69 | 0,65 | 8.208 | 3.311 |

*Feit* (store-legs). Open rondes met verkopen erbij veranderen het beeld niet (Σ open-waarde 44,8 k, bovengrens, §1).

## 3. Trage archetypen

**3a. Curve, per grootte** (gesloten + open, Σ netto SOL; *feit*):

| houdtijd | < 1 SOL | 1–5 SOL | 5–20 SOL | > 20 SOL (n, Σ, mediaan, gemiddeld) |
|---|---|---|---|---|
| 1–5 min | +4,4 k (n 172 k, win 0,45) | +20,7 k (82 k, 0,58) | +6,2 k (6,1 k, 0,69) | n 3.269, **+146,5 k**, mediaan +4,0, gem. +44,8 |
| 5–15 min | +2,6 k (44 k, 0,43) | +12,2 k (37 k, 0,63) | +6,3 k (8,1 k, 0,73) | n 1.175, **+93,3 k**, mediaan +6,1, gem. +79,4 |
| 15–60 min | +0,8 k (11 k, 0,49) | +6,0 k (16 k, 0,65) | +5,3 k (6,8 k, 0,76) | n 687, **+63,1 k**, mediaan +2,9, gem. +91,9 |
| 1–6 u | +0,3 k | +1,3 k | +2,3 k (0,81) | n 413, +9,6 k, gem. +23,1 |
| > 6 u | +0,1 k | +0,8 k | +1,7 k (0,77) | n 728, +7,3 k, gem. +10,0 |

- Instap: 181 k van 390 k trage curve-rondes binnen 1 min na create (Σ +260 k), 130 k in minuut 1–5 (+59 k), 73 k in 5–30 min (+49 k); later dan 30 min: klein. Prijs bij instap 1,5–3× de eerste prijs levert het meest (+213 k op 183 k rondes), < 1,5× +129 k, 3–10× +48 k, ≥ 10× +0,7 k — *feit*.
- Exits: mediaan verkoop/aankoop 1,01–1,04; 75–88 % verkoopt boven de latere laatste prijs; de mint eindigt op 0,50–0,59× hun instapprijs; 2–11 % van de curves is aan het einde leeg — *feit*. De winst is dus een dunne staart van pumps, niet een gestage marge.
- Groottes > 20 SOL op een curve met 30 SOL virtueel: de koop zelf zet de prijs ×2 tot ×10 (constant product); de "trage winnaar" is hier de **maker** van de pump en verkoopt aan de instroom erna. Niet te kopiëren met 0,5 SOL zonder die instroom; wél een signaal om te volgen (replay §5) — *reconstructie*.

**3b. Pools (normaal)**: trage rondes Σ enkele honderden SOL per bucket, mediaan ≈ 0, winstaandeel 0,36–0,72, gemiddelde inzet 1–5 SOL; grootste cel 5–20 SOL × 5–15 min +460 SOL (n 499). Farmer-aandeel van hun legs 4–26 % — *feit*. Geen archetype met gewicht.

**3c. Reversed pools: één cluster van 25 wallets.** Houdtijd 1–15 min, inzet < 5 SOL, **winstaandeel 95–100 %**, mediaan +0,02…+0,56 SOL, Σ ≈ +1,3 k SOL gesloten; daarnaast 27 k rondes "gekocht, nooit verkocht" (Σ voorraad ≈ 0 waarde). Instap: 96 % binnen 5 min na de poolcreatie, de laatste prijs ligt bóven hun instap (1,06–1,16×), 76–78 % van die pools is aan het einde leeg (LP-drain). Onze F7-trigger valt in 384 van 31.886 rondes vóór hun instap — *feit*. Deze wallets betalen ~100 lamports prioriteitsfee en geen tips: programmatisch. Zij zijn **niet** de LP: 0 deposits/withdraws, 0 creators; elk van de 3.312 pools heeft een **eigen, eenmalige creator** die de LP zelf terugtrekt (100 %), ~206 traders en ~300 SOL volume per pool (het cluster is 6 % van de traders), 150–200 zulke pools per dag; het cluster maakt mediaan 12 rondes per pool, instap mediaan 523 slots (~2,3 min) na de eerste trade, en is in 94 % van de pools netto positief, Σ 60–185 SOL per dag (dalend over dev). Een pump-fabriek met eenmalige creators en een botcluster dat van de retail-instroom vóór de drain profiteert; of cluster en fabriek één operator zijn: *onbekend* — *reconstructie* (`slow_reversed_check.py`).

## 4. Wallet-archetypen (dominante houdtijd × venue)

| archetype | wallets | Σ dag 1–9 | Σ dag 10–18 | wallets > 0 op dag 10–18 | mediaan/wallet dag 10–18 | mediaan rondes | mediaan inzet |
|---|---|---|---|---|---|---|---|
| curve ≤ 3 slots | 13 | 6.543 | 2.374 | 1,00 | 84,6 | 3.710 | 0,82 |
| curve ≤ 1 min (scalpers) | 547 | 455.865 | 205.823 | 0,97 | 157,6 | 7.880 | 0,54 |
| curve 1–5 min | 159 | 96.632 | 35.164 | 0,97 | 129,3 | 5.480 | 0,99 |
| curve 5–15 min | 11 | 2.374 | 3.970 | 0,97 | 20,4 | 2.101 | 0,46 |
| curve 15–60 min | 1 | 85 | 63 | 1 | 63 | 114 | 3,06 |
| pool ≤ 1 min | 20 | 2.925 | 498 | 0,88 | 10,7 | 444 | 1,50 |
| pool 1–5 min | 14 | 695 | 701 | 0,99 | 35,7 | 2.515 | 0,45 |
| pool 5–60 min | 3 | 112 | 181 | 1,00 | 12–153 | 30–134 | 3,5–7,6 |
| pool > 6 u | 2 | 137 | −58 | 0,59 | 21,5 | 91 | 6,38 |
| reversed, meest "nooit verkocht" | 25 | 1.005 | 867 | 1,00 | 36,1 | 1.601 | 0,61 |

*Feit*. Een wallet "trage curve" (1–5 min, 159 stuks) bestaat dus echt en blijft op dag 10–18 in 97 % positief, mediaan 129 SOL per wallet over 9 dagen; de 5–15 min-groep is 11 wallets. Boven 15 min zijn er vrijwel geen consistente wallets: de lange houdtijden in §2 zijn bijvangst van snellere wallets.

## 5. Vertraagde replay van trage rondes

Steekproef: 400 gesloten rondes per venue × houdtijd (1 min – > 6 u), één venue, zelfde epoch; toestand = laatste trade vóór het instap-/uitstap-slot + d (constant product op vt/vq resp. poolreserves, fee-bps van hun eigen koop, kosten 2 × 0,000705 SOL); d = 0 / 2 / 3 slots op **beide** kanten; met hún inzet en met 0,5 SOL. Controle: d = 0 met hun inzet reproduceert hun werkelijke netto (mediaanratio curve 0,96, pool 1,01, reversed 1,00) — *modelaanname, gecontroleerd*. `c_replay.parquet`.

| venue | houdtijd | n | werkelijk /ronde | hun inzet d=0 | hun inzet d=3 (gem., winst%) | 0,5 SOL d=0 | **0,5 SOL d=3** (gem., mediaan, winst%) | Σ 0,5 SOL d=3 | p5 / p95 |
|---|---|---|---|---|---|---|---|---|---|
| curve | 1–5 min | 388 | −0,01 | −0,35 | −0,64 (27 %) | +0,034 | **−0,040**, −0,07, 30 % | −15 | −0,33 / +0,39 |
| curve | 5–15 min | 385 | +0,22 | +0,47 | +0,14 (34 %) | +0,136 | **+0,055**, −0,06, 38 % | +21 | −0,34 / +0,96 |
| curve | 15–60 min | 375 | +2,15 | +4,14 | +1,93 (35 %) | +0,133 | **+0,059**, −0,07, 39 % | +22 | −0,35 / +0,91 |
| curve | 1–6 u | 334 | +0,26 | +0,31 | −0,10 (33 %) | +0,108 | **+0,043**, −0,06, 38 % | +14 | −0,33 / +0,80 |
| curve | > 6 u | 326 | +0,18 | −0,22 | −0,63 (31 %) | +0,112 | **+0,050**, −0,06, 39 % | +16 | −0,35 / +0,82 |
| pool | 1–5 min | 362 | +0,06 | −0,10 | −0,23 (34 %) | −0,018 | −0,047, −0,05, 35 % | −17 | −0,40 / +0,33 |
| pool | 5–15 min | 323 | +0,29 | +0,12 | −0,07 (29 %) | +0,002 | −0,041, −0,14, 31 % | −13 | −0,45 / +0,77 |
| pool | 15–60 min | 395 | +0,41 | +1,39 | +0,93 (33 %) | +0,199 | +0,133, −0,15, 34 % | +53 | −0,45 / +1,34 |
| pool | 1–6 u | 394 | +0,37 | +5,05 | +4,57 (37 %) | +0,536 | +0,460, −0,09, 39 % | +181 | −0,44 / +2,90 |
| pool | > 6 u | 378 | +0,25 | +6,09 | +5,49 (34 %) | +0,497 | +0,417, −0,13, 37 % | +158 | −0,44 / +2,04 |
| reversed | 1–5 min | 399 | +0,24 | +0,23 | +0,22 (92 %) | +0,069 | **+0,067**, +0,05, 95 % | +27 | 0,00 / +0,19 |
| reversed | 5–15 min | 399 | +0,55 | +0,55 | +0,54 (100 %) | +0,160 | **+0,157**, +0,13, 100 % | +63 | +0,04 / +0,35 |

Lezing:
- **Curve, hun inzet:** de 1–5 min-rondes verliezen bij 3 slots vertraging hun hele voorsprong (−0,64 per ronde): wie 3 slots na een 20–80 SOL-koop instapt, koopt de pump die de ander maakte en verkoopt vóór diens verkoop in een lege markt. 5–15 en 15–60 min houden iets (de laatste puur staart, corr 1,0 met één uitschieter); ≥ 1 u negatief — *modelaanname*.
- **Curve, 0,5 SOL:** een kleine kopie 3 slots achter hun instap en 3 slots achter hun verkoop levert +0,04…+0,06 SOL per ronde (+8–12 % op 0,5 SOL) in elke bucket ≥ 5 min, met 38–39 % winstkans en een dunne staart (p95 +0,8…+1,0, p5 −0,33). Naar hun inzet: achter kopen < 1 SOL −0,005 (n 794), 1–5 SOL +0,039 (720), 5–20 SOL +0,113 (261), > 20 SOL +0,147 (33). Naar dagblok: dag 1–9 +0,022 (641), **dag 10–18 +0,038 (1.167)**. Een wallet-volgregel op trage curve-instappen van een persistente wallet overleeft de vertraging dus wél — maar dit is één steekproef op verbruikte data, het label (groep C) is op dag 1–9 bepaald en beide blokken zitten in de steekproef — *reconstructie*.
- **Pools:** de replay met hún inzet geeft op 1–6 u en > 6 u 10–20× méér dan ze werkelijk verdienden (corr 0,15–0,22): constant product op de laatst gelogde reserves is op lange houdtijden onbetrouwbaar (gedraineerde of geherstarte pools, zie de rt_tx-les in de markout-noot). De 0,5 SOL-cijfers voor pools ≥ 15 min zijn daarom niet te vertrouwen; de medianen zijn overal negatief — *onbekend*.
- **Reversed cluster:** 3 slots vertraging kost 5 % (+0,224 tegen +0,236 met hun inzet); met 0,5 SOL +0,07 / +0,16 per ronde, 95–100 % winst, p5 ≥ 0; per dagblok +0,126 (dag 1–9, n 359) en +0,101 (dag 10–18, n 439), 97–98 % winst — *modelaanname*. De vraag is niet de vertraging maar het herkennen van de pool (§6).

## 6. Kandidaat-hypothesen voor de forward-toets (register E33, niet vastgelegd)

Geen van beide is nu een hypothese met trigger, regel en lat; het zijn kandidaten om op dev point-in-time te operationaliseren vóórdat er iets wordt gehasht. Geen wijziging aan H4/H5 (E31) of de forward-toets (E32).

**K1 — trage curve-instap van een persistente wallet volgen (wallet-copy, traag).** Trigger: een koop ≥ 1 SOL (bij voorkeur ≥ 5 SOL) op een pump-curve door een wallet uit een persistentieset (gesloten rondes, ≥ 7 van 9 dagen positief, bepaald over de voorafgaande dagen); instap +3 slots met 0,5 SOL; uitstap 3 slots na hún eerste verkoop, met tijdplafond. Dev-indicatie (§5): +0,04…+0,06 SOL per ronde, 38 % winst, dag 10–18 +0,038. Nodig vóór vastlegging: (a) de set point-in-time (label uit dag 1–9, volgen op dag 10–18, álle rondes in plaats van 400 per cel); (b) de dag-CI in de H4-vorm; (c) of de persistentie van de set zelf houdbaar is (E24: top-20 van money-v1 volgen was −7,3 %; dit label is anders en alleen traag); (d) live een wallet-watchlist in paper (`tx_wallets`-stream bestaat in de recorder; engine-werk). Risico: 62 % verliezende rondes, winst in de staart; wallets stoppen of veranderen.

**K2 — fabrieks-reversed-pools, minuut 2–7.** Trigger: een reversed pool van een eenmalige creator (de 3.312 pools; 150–200 per dag), instap ~2 min na de eerste trade, uitstap na ≤ 5 min of bij de eerste withdraw-aanzet. Dev-indicatie (§5): +0,10…+0,13 SOL per ronde met 0,5 SOL, 97 % winst, beide blokken. Nodig vóór vastlegging: (a) een point-in-time-herkenning van de fabriek zonder het cluster te kennen (creator-leeftijd, pool-parameters, retail-instroom in minuut 1–2; F7 vuurt in slechts 1 % van de clusterrondes vóór hun instap, dus de huidige F7-filters missen deze pools of komen te laat); (b) of de fabriek buiten dev doorloopt (de dagwinst van het cluster daalde van 150–185 naar 55–80 SOL); (c) wie de tegenpartij is: de retail-instroom of de creator; en of ons eigen 0,5 SOL de uitkomst voor de 25 bots verandert. Risico: één operator; stopt die, dan stopt het signaal.

**Niet verder:** curve-kopie met grote inzet of binnen 5 min (negatief bij 3 slots); trage pool-posities (klein, model onbetrouwbaar); wallets met houdtijd > 15 min bestaan nauwelijks als consistente groep (§4).

## 7. Open punten

- Faalratio, bundels en funder-clustering: *onbekend* (NAS).
- De bovengrens op open curve-waarde (§1); een strakkere grens is `rq` (echte SOL in de curve).
- Dag 10–18 is verbruikte data (E24); alles hier is beschrijvend.
