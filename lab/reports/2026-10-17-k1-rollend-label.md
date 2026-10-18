# K1 met een rollend point-in-time label valt om — en waarom E39 positief leek (E40)

LAB research, 2026-10-17. NAS-job 99k (`python -m wallets.winners.k1_rolling`, b57ea02): kandidaten = alle wallets met een curve-koop ≥ 3 SOL op dev; label voor dag t = in de 9 dagen vóór t ≥ 7 dagen positieve kas op gesloten curve-rondes en > 10 SOL (E24's strikte label, rollend); kopie 0,5 SOL op 3 slots achter hun koop ≥ 5 SOL (vast), verkoop 3 slots na hun laatste verkoop, kosten 2 × 720.618 lamports (base, incl. verwachte mislukte pogingen); capaciteit max 5 / 20 open. Uitvoer `wallets/winners-v1/k1_rolling.md` (sha fac48d0e…), `k1_rolling_rounds.parquet` (5db36d60…), `k1_rolling_labels.parquet` (be7186a9…). Alleen dev; dag 10–18 (14 dagen met label, alle 270 ms). Labels **feit** / **modelaanname** / **reconstructie**.

## 0. Samenvatting

- **Uitkomst (modelaanname):** met het rollende label verliest de kopie overal. ≥ 5 SOL, max 5 open: Σ −1.170 SOL, **−83,6 SOL per dag** [−103,6; −68,0], 14 van 14 dagen negatief; max 20 open −100,7/dag; ≥ 3 SOL −139,8/dag; ≥ 10 SOL −39,2/dag. Per ronde: d = 0 gemiddeld +0,09 … +0,15 (de koop zelf is nog goed), d = 2/3 −0,035 … −0,085, winstkans ~0,22.
- **Waarom E39 positief leek — drie oorzaken, in volgorde van gewicht (feit, uit de decompositie van dezelfde E40-rondes):**
  1. **Een look-ahead in het universum van E33/E39.** Dat universum was "trage rondes" = een *gerealiseerde* houdtijd ≥ 1 min. Op het moment van de koop weet niemand hoe lang de wallet zal vasthouden. Zonder die filter zijn 80 % van de ≥ 5 SOL-rondes van persistente wallets scalps ≤ 1 min (27.651 van 34.695), en daar kost 3 slots vertraging alles: d = 0 +0,10, d = 3 −0,06 per ronde. Rondes > 5 min blijven wél positief (+0,076 bij d = 3), 1–5 min −0,016. Zelfs **groep C zelf** (13.459 van de E40-rondes) is zonder de houdtijdfilter negatief: −13,0 SOL/dag [−17,8; −8,6], 13 van 14 dagen. Dit is de hoofdoorzaak; E39's "+12 per dag" was een selectie op de toekomst.
  2. **Het universum en het label.** Het rollende label laat 1.610 wallets toe (210–1.186 per dag) tegen 797 in groep C; groep C kwam uit de money-v1-groep *traders* (zonder creators, seller-only, bots). In E40 zijn 484 gelabelde wallets *creators* (money-v1) met 15.211 rondes en Σ −1.266 SOL; 12.891 rondes zijn kopen van een creator in zijn **eigen** token (−0,087 per ronde, 17 % winstkans): dat is E35 (de dev-buy is de pump; wie volgt koopt de top). Wallets buiten groep C: −81 SOL/dag; alleen money-v1-traders: −19/dag.
  3. **De kosten:** 720.618 tegen 705.000 lamports per order is 0,00003 SOL per ronde — geen rol.
- **Wat overblijft:** alleen de doorsnede "money-v1-trader, niet het eigen token, gerealiseerde houdtijd ≥ 1 min" is positief (+5,4 SOL/dag [3,0; 7,6], 2 van 14 dagen negatief), en die bevat opnieuw de houdtijd uit de toekomst. Een point-in-time variant zou een **minimale eigen houdtijd** zijn (hun verkoop in de eerste minuut negeren, verkopen op max(hun verkoop, +60 s)); dat vraagt een andere replay en is onwaarschijnlijk: bij de scalps komt onze latere verkoop ná hun dump. **K1 sluit als dood spoor**; de registerregels E33 en E39 krijgen een correctie (universum met look-ahead). Niets verandert aan K3 of de H-armen.

## 1. Decompositie van de E40-rondes (≥ 5 SOL; feit op modeluitkomsten)

| deelverzameling | rondes | wallets | kopie d = 0 | kopie d = 3 | winstkans d = 3 | Σ d = 3 (SOL) | capaciteit 5 open: per dag |
|---|---|---|---|---|---|---|---|
| alle gelabelde wallets | 34.695 | 1.610 | — | −0,052 | 0,22 | −1.792 | −83,6 [−103,5; −67,8], 14/14 < 0 |
| in groep C (E24/E39) | 13.459 | 352 | +0,139 | −0,016 | 0,28 | −221 | −13,0 [−17,8; −8,6], 13/14 |
| niet in groep C | 21.236 | 1.258 | +0,066 | −0,070 | 0,20 | −1.476 | −81,3 [−103,8; −65,8], 14/14 |
| money-v1 *trader* | 19.319 | 1.095 | +0,116 | −0,022 | 0,27 | −425 | −19,4 [−23,7; −15,4], 14/14 |
| money-v1 *creator* | 15.211 | 484 | +0,054 | −0,083 | 0,18 | −1.266 | — |
| creator koopt eigen token | 12.891 | — | — | −0,087 | 0,17 | −1.126 | — |
| trader, niet eigen token, houdtijd ≥ 1 min (toekomst) | 4.307 | — | — | — | — | — | +5,4 [3,0; 7,6], 2/14 |

Per gerealiseerde houdtijd (alle gelabelde, ≥ 5 SOL): ≤ 3 slots n 1.316 (d0 +0,059, d3 −0,046); ≤ 1 min n 27.651 (+0,104, −0,062); 1–5 min n 4.033 (+0,090, −0,016); > 5 min n 1.695 (+0,225, +0,076).

Gevoeligheid van de drempel (vast 5 SOL; alleen ter informatie): ≥ 3 SOL −139,8/dag, ≥ 10 SOL −39,2/dag bij 5 open — hogere drempel = minder slecht, nooit positief.

## 2. Oordeel (reconstructie)

K1 is een dood spoor bij ≥ 600 ms. Het patroon van winnaars I en II blijft: wie persistent wint op de curve, wint met snelheid (scalps ≤ 1 min) of als maker van de pump (eigen token); een volger op 3 slots koopt de top en verkoopt na hun dump. Het positieve E39-beeld kwam uit een universum dat de houdtijd kende. Les voor het register: een "ronde-universum" mag alleen op kenmerken vóór de instap worden gedefinieerd; E33 (§5) en E39 krijgen die correctie.

Wat wél blijft staan: de agenda-conclusie dat het voordeel in de omgekeerde pools (F7/H4/H5) niet van snelheid afhangt, en Triton-punt 5 (de 125 bekende wallets realtime volgen als *meting*, geen regel).

## 3. Correctie 2026-10-18 (Codex-bevinding 6, bevestigd)

Deze noot en de run (b57ea02) hadden zelf nog een look-ahead. `closed` (tokens binnen 5 % verkocht) was over de **hele** dev-periode bepaald en stuurde twee dingen: (1) het label — de kas per dag telde alleen rondes die ooit gesloten zouden worden, dus een verkoop ná dag t bepaalde mee of een wallet op dag t gelabeld was; (2) het kopie-universum — alleen gesloten rondes, met een uitstap in hetzelfde epoch, en de verkoop ná hun *laatste* verkoop (op het moment van de eerste verkoop weet je niet dat het de laatste is). Alle drie zijn selecties op informatie ná de instap; de regel uit §2 ("een ronde-universum mag alleen op kenmerken vóór de instap worden gedefinieerd") gold dus ook hier.

Wat verandert: `wallets/winners/k1_rolling.py` is herbouwd — het label telt per dag t alleen rondes die *op dag t* gesloten waren (verkocht binnen 5 %, over de dagen vóór t); de kopie volgt élke ronde van een gelabelde wallet (gesloten of niet) en stapt uit 3 slots na hun **eerste** verkoop, of aan een plafond van 100.000 slots (≈ 6 u) of het einde van het epoch als ze niet verkopen; de E40-uitstap (laatste verkoop) staat ernaast als `l3`, alleen ter vergelijking. Een smoke op twee epochs (los label; getallen betekenen niets) laat de richting zien: rondes met een eerste-verkoop-uitstap −0,046 per ronde (23 % winst), nooit verkochte rondes aan het plafond +0,20 (59 % winst, n 821): het weglaten van nooit-gesloten rondes was dus niet vanzelf in het voordeel van K1, maar de som blijft sterk negatief (−748 tegen +167 SOL op ≥ 3 SOL). De volledige herhaling is gedaan (zie §4).

## 4. Point-in-time herhaling (NAS-job 99q, c215b4b, 2026-10-18; modelaanname)

Uitvoer `wallets/winners-v1/k1_rolling.md` (sha 1dc66b28…; de E40-versie staat ernaast als `k1_rolling-b57ea02-lookahead.md`), `k1_rolling_rounds.parquet` (60f08f0e…), `k1_rolling_labels.parquet` (b09da9a1…), alleen op de NAS. Het point-in-time label bestaat op 12 dagen (20 sep–1 okt; 18 en 19 sep vallen weg omdat de later gesloten rondes niet meer meetellen).

| drempel | rondes | kopie d3 per ronde (alle rondes) | idem, groep 'hun eerste verkoop' (winstkans) | 5 open: per dag [2,5 %; 97,5 %] | 20 open | dagen < 0 |
|---|---|---|---|---|---|---|
| ≥ 3 SOL | 59.644 | −0,039 | −0,044 (0,23) | −38,3 [−46,7; −29,9] | −130,5 | 12/12 |
| **≥ 5 SOL (vast)** | **31.977** | **−0,036** | **−0,042 (0,23)** | **−38,5 [−48,6; −29,6]** | **−77,7** | **12/12** |
| ≥ 10 SOL | 20.144 | −0,025 | −0,032 (0,25) | −17,3 [−22,2; −13,0] | −27,7 | 12/12 |

- De koop zelf blijft goed (d = 0: +0,10 … +0,16 per ronde); op d = 2/3 is elke dag negatief (≥ 5 SOL: Σ −63 … −192 per dag). Het verschil met E40 (−83,6/dag) zit in de uitstap: de uitstap na hun **eerste** verkoop is op 9 van de 12 dagen beter dan die na hun laatste verkoop (kolom `l3`, de E40-uitstap; `l3` bestaat alleen voor rondes met een verkoop in het epoch, dus vergelijkbaar: Σ −1.317 tegen ≈ −1.424 bij ≥ 5 SOL).
- **Per uitstapsoort (≥ 5 SOL):** hun eerste verkoop 31.456 rondes, −0,042, Σ −1.317; plafond 6 u zonder verkoop 503 rondes, **+0,31**, 58 % winst, Σ +155; plafond met latere verkoop 18 rondes, −0,02. De plafondgroep is een **selectie achteraf**: dat de wallet niet verkoopt is uitkomstinformatie (zij houden de runners vast), op het instapmoment is niet te zien welke 1,6 % van de kopen daarbij hoort. De enige point-in-time variant, een vaste houdtijd van 6 u op álle kopen, houdt bij 98 % van de rondes (31.456 van 31.977: de wallet verkoopt binnen het plafond) de positie over hun verkoop heen vast; wat die rondes na 6 u waard zijn is hier niet gemeten, maar bij hun eerste verkoop staat de kopie al op −0,042 en de E39-houdtijdbuckets (> 6 u +0,042 per ronde, alleen op hun eigen gesloten rondes) geven geen reden om daar winst te verwachten; geen vervolg, geen NAS-tijd.
- **Oordeel ongewijzigd:** K1 is een dood spoor bij ≥ 600 ms; minder negatief dan de E40-versie, maar 12 van 12 dagen negatief bij elke drempel en capaciteit. Register E40 bijgewerkt met deze getallen.

## 5. Codex-ronde 2 (2026-10-18): twee codefouten in de 99q-versie, herrun gevraagd

Beide punten van de review zijn bevestigd in de code van c215b4b; de gemeten invloed komt uit de herrun (NAS-job, zelfde commando als 99q op de nieuwe commit) en wordt hier aangevuld.

- **Eerste verkoop (`slot > fbe`), aangetoonde fout.** Een verkoop later in hetzelfde slot als hun eerste koop telde niet als uitstap; de ronde kreeg de volgende verkoop of het plafond. Nu geldt de transactievolgorde (slot, tx_index); zo'n ronde heet 'hun eerste verkoop (zelfde slot als de koop)' en de kopie koopt en verkoopt dan op fb + d (alleen kosten; met d ≥ 2 is die verkoop bij de instap al zichtbaar, maar de kopie volgt elke transactie met vertraging d en slaat niets over — een keuze, vastgelegd in de code). Smoke op een los label: ~2 % van de rondes (300 van 16.300 bij ≥ 3 SOL).
- **Capaciteitssimulatie, aangetoonde fout met twee delen.** (1) De filter `h3 IS NOT NULL` liet rondes met een onbekende uitkomst niet meedingen, en de bedoelde oorzaak (curve voltooid vóór de uitstap → NULL) bleek nooit te vuren: `variant ILIKE '%complete%'` matcht niets omdat de store alleen instructienamen kent (buy, sell, …). In 99q had dus elke ronde een waarde (31.977 van 31.977 bij ≥ 5 SOL) en een ronde waarvan de curve voltooide is gewaardeerd op de laatste curvestand, de voltooiingsstand (= de startprijs van de pool), niet uitgesloten; het 'Voorbehoud' in die uitvoer was onjuist. (2) Gelijktijdige instappen werden op de uitstap `xs` gesorteerd, toekomstinformatie. Nu: voltooiing uit `s.mints.complete_slot`; vóór de instap = niet ingestapt (op dat moment zichtbaar); tussen instap en uitstap = verkoop op de PumpSwap-pool van de mint (`s.mints.pool`, laatste poolstand vóór xs + d, constant product met de pool-bps; zonder poolrij de startreserves van de pool, enkele slots later); zonder poolstand = onbekend (NULL), apart geteld, met een ondergrens die onbekend als totaal verlies boekt. De capaciteit loopt in signaalvolgorde (fb, tx_index van hun koop) over alle ingestapte rondes; een onbekende uitkomst houdt de plek bezet. Smoke: 500 van 17.100 rondes op de pool afgewikkeld, 0 onbekend, 26 niet ingestapt.
- **Regressietests:** `wallets/tests/test_k1k2_pit.py` (eerste verkoop in hetzelfde slot; causale volgorde en onbekende ronde in de capaciteit; één per token).
- **Wat onbekend blijft tot de herrun:** de grootte van beide effecten op dev. Verwachting (modelaanname): klein, de som zit in de 31.456 eerste-verkoop-rondes; de plafondgroep kan kleiner worden (zelfde-slot-verkopen zaten deels in 'niet verkocht').
- **Niet-SOL-quote-curves (gevonden 2026-10-18 bij de LAB-nit over de startreserves-fallback; feit).** Van de rondes die in de smoke van 6a7580c op de startreserves van de pool werden afgewikkeld zaten 194 van de 196 op pools met quote_class 'other' en nul rijen in `s.pool`: curves met een andere quote-mint dan SOL (`s.mints.quote_mint` = PUMP, DOGE-achtige tokens, …; 82.143 van de 707.011 mints). Daar staat `c.q` in eenheden van dat token, niet in lamports: 'koop ≥ 5 SOL' en de kopie van 0,5 'SOL' waren daar in de verkeerde munt, en de store laadt voor die pools geen trades (alleen `quote_class` sol/reversed). Gemeten op de bewaarde E40-uitvoer (b57ea02): 22.097 van de 34.695 rondes ≥ 5 'SOL' (64 %) op zulke curves, 'netto' 775.907 eenheden tegen 40.108 SOL; 1.492 van de 1.763 gelabelde wallets hadden zo'n ronde (het label zelf was besmet). Fix: `k1_rolling.py` beperkt kandidaten, legs en rondes tot SOL-curves (`SOL_QUOTE`; point-in-time, de quote staat in het CreateEvent), met test. Smoke (los label, 2 epochs): rondes ≥ 5 SOL 11.277 → 2.181; plafondgroep 'niet verkocht' +0,20 → −0,26 per ronde — de positieve plafondgroep van 99q (+0,31, n 503) was dus vermoedelijk vreemde eenheden; startreserves-fallback 196 → 1 ronde (heeft nu een eigen label in de uitvoer). De engine (K3/F7) filtert al op quote; K2 (reversed pools) is niet geraakt; E33 en E39 zijn in het register gecorrigeerd. De 6a7580c-run op de NAS vervalt; de herrun op deze commit levert de SOL-only point-in-time getallen.

## 6. Herhaling SOL-only en point-in-time (NAS-job 99t, f84fb50, 2026-10-18; modelaanname)

Uitvoer `wallets/winners-v1/k1_rolling.md` (sha 4921d3b4…), `k1_rolling_rounds.parquet` (55014c6e…), `k1_rolling_labels.parquet` (32f7f529…); 65 s op de NAS. Kandidaten 64.112 wallets met een koop ≥ 3 SOL op een SOL-curve; 450–667 gelabelde wallets per dag (99q: 870–1.202 — ruim de helft van de labels rustte op PUMP-eenheden); 39.566 rondes te kopiëren, 12 label-dagen.

| drempel | rondes | kopie d3 per ronde | groep 'hun eerste verkoop' (winstkans) | 5 open: per dag [2,5 %; 97,5 %] | 20 open | dagen < 0 |
|---|---|---|---|---|---|---|
| ≥ 3 SOL | 39.566 | −0,054 | −0,056 (0,21) | −62,1 [−77,6; −47,9] | −149,4 | 12/12 |
| **≥ 5 SOL (vast)** | **11.952** | **−0,071** | **−0,072 (0,20)** | **−52,1 [−67,5; −41,7]** | **−67,5** | **12/12** |
| ≥ 10 SOL | 2.099 | −0,065 | −0,066 (0,24) | −11,5 [−17,1; −7,6] | −11,5 | 12/12 |

- **Codex-ronde 2, gemeten (≥ 5 SOL):** 492 rondes (4,1 %) hebben hun eerste verkoop in hetzelfde slot als de koop; de kopie verliest daar alleen de kosten (−0,023 per ronde, Σ −11,4). Afwikkeling bij d = 3: curve 11.781 rondes (−0,070), op de PumpSwap-pool na migratie 135 (−0,124), startreserves-fallback 2 (+0,08), niet ingestapt 34, onbekend 0 — de causale capaciteit (signaalvolgorde, geen NULL-filter) verandert niets aan wie meedingt. De plafondgroep 'niet verkocht' is 181 rondes, **−0,122** per ronde (11,5 % winst): de +0,31 van 99q waren PUMP-eenheden, geen selectie-effect. De eerste-verkoop-uitstap is op 11 van de 12 dagen beter dan E40's laatste-verkoop-uitstap (`l3`).
- **Oordeel ongewijzigd:** K1 is een dood spoor bij ≥ 600 ms; sterker negatief dan 99q (−38,5/dag) omdat de niet-SOL-rondes eruit zijn, bij elke drempel en capaciteit 12 van 12 dagen negatief. De koop zelf blijft goed (d = 0 +0,16 … +0,30), de vertraging kost alles. Register E33/E39/E40 bijgewerkt; geen vervolg.
