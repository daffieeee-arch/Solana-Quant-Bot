# Koopt de trigger-verkoper zelf terug? Seller-buyback na F7-triggers op dev (LAB research, 2026-10-21)

**Status: verkennend, dev 1033–1046 (verbruikte data), beschrijvend, geen claim.** Vraag van de coördinator (eigenaarsbesluit 2026-10-20 "edge first", prioriteit 2): is de omkering na de grote verkoop de gescripte terugkoop van dezelfde verkoper (en gelinkte wallets)? Code `wallets/buyback.py` (#485, 3c93d2e); run NAS 99zzzzc op 1843356: `wallets/buyback-1843356/buyback.md` sha256 36fd054e…, `buyback.json` f987ecca…, `buyback.parquet` 4bd51928… (LAB: de run blijft geldig). Labels: **feit** = gemeten; **reconstructie** = afgeleid; **modelaanname**; **onbekend**.

## 0. Antwoord in één alinea

**Nee, niet als regel; wel in een vijfde van de gevallen, en juist dáár beweegt de prijs nauwelijks.** De verkoper koopt na de trigger zelf 6 % van de bruto gekochte SOL terug (mediaan per trigger 5 %); op het criterium "≥ 50 % van de bruto koopstroom" is zelf-terugkoop vrijwel afwezig (53 van 38.114 triggers). De bruto stroom in deze pools is echter grotendeels wash-volume; van de **netto** stroom (koop − verkoop, wat de prijs beweegt) in de eerste minuut is de verkoper zelf 38 % in totaal, maar geconcentreerd: mediaan per trigger 10 %, en bij 5.105 van de 23.799 triggers met positieve nettostroom (21 %) levert de verkoper ≥ 50 % van dat netto. Persistentie per verkoper is klein (correlatie tussen dagblokken 0,18 op het netto-criterium, −0,01 op het bruto-criterium; 0 verkopers die op het bruto-criterium in blok 1 "terugkopers" zijn). **Het mechanisme-deel dat niet de verkoper is, blijft dus staan; het is geen zelf-terugkoop-artefact.** Maar: de markouts die hier langskomen zijn **per trigger** positief en **per pool** negatief (zie §4): dezelfde wegingsval als bij de mechanismetoets; "+5,5 % netto" is geen edge.

## 1. Methode (feit)

- 38.114 F7 pool_reversed-triggers (K3-rijen: tp4_T600, 0,5 SOL, d 2) van 1.952 verkoperwallets over 20 UTC-dagen, 3.341 pools; per trigger de tokenkopen in de pool in (t, t + 1 m / 5 m / 15 m] uit de store-legs (slots via de Clock), in drie klassen: **self** (de verkoper), **linked** (betaald door de fee-payer van de triggertransactie of door de verkoper, of gedaan door die fee-payer), **other**; de koop in de triggertransactie zelf apart (rt_tx, E16); de **nettostroom** (koop − verkoop in SOL) en de verkoper's eigen netto.
- **Onbekend / ondergrens:** de store kent de fee-payer van 6.158 van de 38.114 triggerlegs en van 759 k van de 50,3 M kopen (≈ 8–16 %); "linked" is dus een losse ondergrens. clusters-v1 (geen wallets per event) en sol_funding (twee epochs) zijn niet gebruikt. Tokenzijde via `token_buy` (in een omgekeerde pool is een tokenkoop een pool-'sell' van WSOL).
- Markouts (log en netto 0,5 SOL round-trip, d 2) uit de dev-markout-run gekoppeld op (pool, t); per trigger (1 %-getrimd, dag-CI) én per pool (gemiddelde over de triggers van de pool, getrimd over pools, poolbootstrap).

## 2. Waar de instroom na de trigger vandaan komt (feit)

| venster | bruto kopen (SOL) | verkoper zelf | aandeel | self + linked | mediaan per trigger | triggers met een koop van de verkoper |
|---|---|---|---|---|---|---|
| 1 m | 11,41 M | 711 k | 6,2 % | 8,8 % | 5,5 % | 31.629 (83 %) |
| 5 m | 54,1 M | 3,27 M | 6,0 % | 8,8 % | 5,1 % | 36.944 (97 %) |
| 15 m | 144,4 M | 8,49 M | 5,9 % | 8,9 % | 5,0 % | 37.195 (98 %) |

- **Nettostroom:** 1 m 194.649 SOL over alle triggers, waarvan de verkoper zelf 74.572 SOL = **38 %**; 23.799 triggers met positieve nettostroom, mediaan eigen aandeel 9,7 %, p75 41 %, p90 112 % (de verkoper koopt dan meer terug dan het netto: de rest verkoopt per saldo). 5 m: 747 k SOL, verkoper 12 %. Bij 8.993 triggers verkoopt de verkoper in de eerste minuut per saldo dóór.
- Bruto-criterium "self ≥ 50 % van de kopen in 1 m": 53 ja, 38.049 nee, 12 zonder koop; flagged 0,19 %, organic 0,03 %. **rt_tx** (koop in de triggertransactie zelf): 8.970 triggers (24 %), 82.160 SOL — het E16-beeld.
- Reconstructie: 300 SOL bruto per pool per minuut tegen 5 SOL netto zegt dat deze pools draaien op wash-volume; het bruto-aandeel van de verkoper is daardoor een slechte maat, het netto-aandeel de betere.

## 3. Markouts naar wie de nettostroom levert (reconstructie, met look-ahead: de groep is op de stroom ná t bepaald, de markout op 1 m is bijna dezelfde grootheid)

| groep (nettostroom 1 m) | triggers | pools | netto 1 m per trigger | per pool | netto 5 m per trigger | per pool | log 5 m per trigger |
|---|---|---|---|---|---|---|---|
| netto > 0, vooral anderen | 18.694 | 2.786 | +15,7 % [+14,8; +16,8] | +13,2 % | +16,8 % [+13,8; +20,0] | +8,3 % [+5,2; +11,4] | +24,4 lp |
| netto > 0, verkoper ≥ 50 % ervan | 5.105 | 1.405 | +2,9 % [+2,3; +3,5] | +0,2 % [−0,9; +1,3] | +2,9 % [+0,7; +5,4] | +0,4 % [−2,6; +3,6] | +11,7 lp |
| netto ≤ 0 | 10.067 | 2.175 | −17,7 % | −23,1 % | −8,0 % | −12,8 % | +1,5 lp |
| … waarvan de verkoper per saldo dóórverkoopt | 4.248 | 1.314 | −18,0 % | −22,2 % | −7,9 % | −11,1 % | +1,2 lp |

Lezing: als de verkoper zelf het netto levert, komt de prijs nauwelijks van zijn plaats (+0,2 % per pool op 1 m, +0,4 % op 5 m); de opvering die F7 zoekt komt van ander geld. Dat zegt niets over voorspelbaarheid (look-ahead), wel over de aard van de beweging.

## 4. Per trigger of per pool: dezelfde cijfers, tegengesteld teken (feit)

| maat (d 2) | per trigger (n 38.114, dag-CI) | per pool (3.341 pools, poolbootstrap) |
|---|---|---|
| netto 1 m | +1,17 % [+0,63; +1,63] | **−4,66 %** [−5,91; −3,41] |
| netto 5 m | +5,51 % [+3,42; +7,55] | **−4,40 %** [−7,05; −1,82] |
| log 1 m | +3,57 lp | −0,76 lp [−2,54; +1,08] |
| log 5 m | +14,10 lp | +8,72 lp [+5,37; +12,13] |

Waarom: de triggers zitten in weinig actieve pools (mediaan 4 triggers per pool, p90 30, max 217). Per pool (gemiddelde over de triggers van de pool, 0,5 %-getrimd over de pools van de klasse) is het netto 5 m bij pools met 1–2 triggers (1.168 pools, 1.598 triggers) **−21,7 %**; bij 3–10 triggers (1.207 pools) +2,6 %, 11–50 (816) +9,5 %, > 50 (150) +7,3 %. (Per trigger ongewogen: −14,7 / +5,5 / +9,2 / +7,2 %: zelfde richting en volgorde.) Per trigger wegen de actieve pools zwaar en is het gemiddelde positief; per pool wegen de eenmalige pools even zwaar en is het negatief. De pre-registratie E45 kiest daarom de pool als eenheid; **"+5,5 % netto" in `buyback.md` is per trigger en is geen edge** (het is bovendien dev, zonder exitregel, en een trigger in een actieve pool is pas achteraf als zodanig bekend).

## 5. Persistentie per verkoper (feit)

- 854 verkopers met ≥ 3 triggers in dagen 1–9; 605 ervan triggeren ook in dagen 10–18 (17.707 van de 24.433 blok-2-triggers). Bruto-criterium: 0 terugkoopverkopers in blok 1; correlatie van de terugkoopfrequentie per verkoper tussen de blokken −0,01.
- Netto-criterium (verkoper ≥ 50 % van een positieve nettostroom): gemiddelde frequentie per verkoper 14 % in beide blokken, correlatie 0,18; 12 verkopers met ≥ 50 % in blok 1 doen het in blok 2 bij 19 % van hun 133 triggers. Aandeel van zulke triggers per blok: 28 % (dagen 1–9), 18 % (10–18), 26 % (na 30 sep).
- Reconstructie: geen vaste groep scripted terugkopers; het verschijnsel is verspreid en wisselt per trigger.

## 6. Wat dit niet zegt (modelaanname / onbekend)

Gelinkte wallets zijn grotendeels onbekend (fee-payer schaars; geen funding-graaf over het hele venster): "de verkoper" kan meer wallets zijn dan hier geteld. Geen exitregel, geen landing; dev is verbruikte data; §3 heeft look-ahead. Geen P&L.

## 7. Gevolg

- Geen reden om F7 als "zelf-terugkoop" af te schrijven; de mechanismetoets E45 blijft de toets (pool-eenheid, schone data).
- Registerrij E47 (verkennend, gesloten). Should-fix LAB (de `COALESCE`-terugval op `kind` oriëntatiebewust) in dezelfde PR als deze noot.
