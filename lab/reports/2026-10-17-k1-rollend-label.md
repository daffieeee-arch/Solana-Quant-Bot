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

| drempel | rondes | kopie d3 per ronde | winstkans | 5 open: per dag [2,5 %; 97,5 %] | 20 open | dagen < 0 |
|---|---|---|---|---|---|---|
| ≥ 3 SOL | 59.644 | −0,044 | 0,23 | −38,3 [−46,7; −29,9] | −130,5 | 12/12 |
| **≥ 5 SOL (vast)** | **31.977** | **−0,042** | **0,23** | **−38,5 [−48,6; −29,6]** | **−77,7** | **12/12** |
| ≥ 10 SOL | 20.144 | −0,032 | 0,25 | −17,3 [−22,2; −13,0] | −27,7 | 12/12 |

- De koop zelf blijft goed (d = 0: +0,10 … +0,16 per ronde); op d = 2/3 is elke dag negatief (≥ 5 SOL: Σ −63 … −192 per dag). Het verschil met E40 (−83,6/dag) zit in de uitstap: de uitstap na hun **eerste** verkoop is op elke dag beter dan die na hun laatste verkoop (kolom `l3`, de E40-uitstap: Σ −1.808 tegen −1.317 bij ≥ 5 SOL).
- **Per uitstapsoort (≥ 5 SOL):** hun eerste verkoop 31.456 rondes, −0,042, Σ −1.317; plafond 6 u zonder verkoop 503 rondes, **+0,31**, 58 % winst, Σ +155; plafond met latere verkoop 18 rondes, −0,02. De plafondgroep is een **selectie achteraf**: dat de wallet niet verkoopt is uitkomstinformatie (zij houden de runners vast), op het instapmoment is niet te zien welke 1,6 % van de kopen daarbij hoort. De enige point-in-time variant, een vaste houdtijd van 6 u op álle kopen, loopt bij 94 % van de rondes over hun dump heen en is daarmee vrijwel zeker slechter dan −0,042; geen vervolg, geen NAS-tijd.
- **Oordeel ongewijzigd:** K1 is een dood spoor bij ≥ 600 ms; minder negatief dan de E40-versie, maar 12 van 12 dagen negatief bij elke drempel en capaciteit. Register E40 bijgewerkt met deze getallen.
