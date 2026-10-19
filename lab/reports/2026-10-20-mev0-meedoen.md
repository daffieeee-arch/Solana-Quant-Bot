# MEV fase 0, vervolg: wat is er nodig om mee te doen? (lezing van de bestaande E17-data)

LAB research, 2026-10-20. Vraag (owner via de coördinator, 19 okt): wat zou een deelnemer aan de atomaire-arbitrage/backrun-markt rond pump.fun en PumpSwap moeten kunnen (snelheid, fooien, mislukte pogingen), en wat zou dat opleveren? Alleen een lezing van de bestaande MEV-fase-0-uitvoer (`wallets/mev0-rerun/mev0-v1/`, NAS-job 96 op a33446b, de fee_payer-fix; E17). Niets is verstuurd of gehandeld; geen nieuwe meting. Labels **feit** / **reconstructie** / **modelaanname** / **onbekend**. Register E43.

> **Dekking, bovenaan.** De data beslaan twee epochs (1040, 1045) = vijf UTC-dagen, waarvan **één volledig gedekt: 2026-09-29 (99,9 %)**; 22 sep is 79 % gedekt, 23 sep 55 %, 28 sep 10 %, 30 sep 23 %. Alle dagcijfers hieronder zijn van 29 sep tenzij anders gezegd; de andere dagen wijzen dezelfde kant op maar zijn geen dagen. Alles is vóór Alpenglow (epoch 1064, 21 okt); de markt na de switch is niet gemeten. "Verklaard" = een arb die netto niet meer SOL oplevert dan er op zijn store-legs is verhandeld (de regel uit de E17-noot van 15 okt): de onverklaarde rijen (70 % van de netto winst) zijn liquiditeitsopnames van LP's, geen arbs, en vallen hier overal buiten.

## 0. Samenvatting

- **Het geld zit in hetzelfde slot als de kans** (feit): van de 630 + 20 + 27 = 677 SOL netto van verklaarde backruns op 29 sep landt **93 %** in het slot van de trigger-trade zelf, 3 % in het volgende slot en 4 % op ≥ 2 slots. Wie niet in hetzelfde slot kan landen (geen bundel, geen colocatie) vist in een vijver van ≈ 47 SOL per dag, gedeeld door ≈ 200 fee-payers.
- **De top-3 verdient 63 % van de backrun-winst met weinig fooien** (feit): 8.996 arbs, 842 SOL, 9 % met een Jito-tip, mediane priority fee 0,000174 SOL; de 87.412 arbs van nieuwkomers (eerste dag ≥ 28 sep) brengen samen 256 SOL met 59 % getipt (mediane tip 1.000 lamports). Fooien kopen hier geen positie: tussen concurrerende same-slot-backruns op dezelfde trigger is er geen verband tussen tip of priority fee en de blokpositie (n 144, correlatie −0,04 / −0,13).
- **Kosten per gelande arb zijn klein, mislukte pogingen zijn de regel** (feit): fooien 7 % en fees 26 % van de bruto winst; de mediane verklaarde arb levert netto 0,00001 SOL; 4,7 miljoen mislukte pogingen op 29 sep (131 SOL, 99 % van winnaars), mediane mislukkingsgraad van een winnaar 83 %; 218 van de 326 winnaars blijven na die kosten positief.
- **Wat een nieuwkomer zonder same-slot-landing kan verwachten** (modelaanname, band): hooguit enkele SOL per dag bruto bij een proportioneel aandeel in de ≥ 1-slot-pot, vóór mislukte pogingen en vóór de onbekende landingskans; realistisch < 1 SOL per dag. Dat is geen bedrijfsmodel, en same-slot meedoen valt buiten de huidige kaders (Triton-only, paper-only, geen keys/signing/sending): het zou een apart owner-besluit vragen over searcher-infra, bundels (Jito) en colocatie.
- **Onbekend uit OF1:** colocatie, SWQoS/stake-gewogen toegang, private orderflow en bundel-lidmaatschap, en de pogingen die nooit zijn geland (OF1 toont alleen gelande, al dan niet mislukte, transacties).
- **Conclusie:** de NO-GO van E17 blijft; "meedoen" vereist same-slot-infrastructuur die buiten de huidige kaders valt en een apart owner-besluit zou vragen. Geen actie; een herhaling na Alpenglow alleen op verzoek van de owner (nieuwe pre-registratie, zelfde "verklaard"-regel).

## 1. Snelheid: afstand van de kans tot de arb (feit)

Verklaarde backruns (een arb direct na andermans trigger-trade ≥ 1 SOL of ≥ 1 % van de diepte), per dag:

| dag | n | zelfde slot | slot + 1 | ≥ 2 slots | netto zelfde slot | netto + 1 | netto ≥ 2 |
|---|---|---|---|---|---|---|---|
| 22 sep (79 %) | 92.351 | 53.718 | 34.127 | 4.506 | 435,0 SOL | 21,2 | 52,1 |
| 23 sep (55 %) | 63.444 | 38.566 | 22.838 | 2.040 | 74,8 | 1,3 | 6,0 |
| **29 sep (99,9 %)** | **91.910** | **58.343** | **28.931** | **4.636** | **630,3** | **19,6** | **27,2** |
| 30 sep (23 %) | 22.561 | 15.202 | 6.663 | 696 | 51,4 | 1,5 | 4,1 |

- Cumulatief aandeel van de backrun-netto op 29 sep: 93,1 % binnen 0 slots, 96,0 % binnen 1 slot, 96,3 % binnen 5 slots.
- Binnen hetzelfde slot zit de arb mediaan 119 transacties (p90 444) ná de trigger in het blok: geen aaneengesloten bundel achter de trigger, wel hetzelfde blok. 46 % heeft een Jito-tip, 55 % een priority fee.
- Op de bereikbare kansen (≥ 2 slots) is de concurrentie laag: 4.596 triggers met precies één bereikbare backrun, 20 met twee (die leveren samen 0,00 SOL).

## 2. Fooien en fees als deel van de bruto winst (feit, verklaarde arbs)

| dag | n | bruto | netto | Jito-tips | priority | tx-fees | tips / bruto | fees / bruto | getipt | mediane netto |
|---|---|---|---|---|---|---|---|---|---|---|
| 22 sep | 172.406 | 879 SOL | 770 | 40,7 | 67,4 | 68,3 | 4,6 % | 15,4 % | 62 % | 0,00001 |
| **29 sep** | **177.942** | **960** | **768** | **67,0** | **124,1** | **125,0** | **7,0 %** | **26,0 %** | **57 %** | **0,00001** |

Bereikbare verklaarde kansen op 29 sep (23.192): netto p50 0,00001 SOL, p90 0,0005, p99 0,008, max 1,42; totaal 48,6 SOL; de 188 kansen ≥ 0,01 SOL dragen 44,5 SOL. De markt van "bereikbaar" is dus een handvol kansen per dag van 0,01–1,4 SOL.

Top-3 tegen nieuwkomers (29 sep, verklaarde backruns, alle afstanden):

| groep | arbs | netto | mediane afstand | getipt | mediane tip | mediane priority fee |
|---|---|---|---|---|---|---|
| top-3 fee-payers | 8.996 | 841,6 SOL | 0 slots | 9 % | 0 | 0,000174 SOL |
| nieuwkomers (eerste dag ≥ 28 sep) | 87.412 | 256,4 | 0 slots | 59 % | 0,000001 | 0 |
| overige | 75.719 | 240,8 | 0 slots | 61 % | 0,000001 | 0 |

## 3. Mislukte pogingen (feit, bovengrens: v3-punt 1 van de spec)

| dag | mislukte pogingen | door winnaars | kosten | mediane kosten per poging | fee-payers |
|---|---|---|---|---|---|
| 22 sep | 3.304.195 | 98 % | 160,3 SOL | 0,000008 | 4.490 |
| **29 sep** | **4.719.406** | **99 %** | **131,0** | **0,000007** | **6.102** |

Per winnaar op 29 sep (326 winnaars met verklaarde arbs): 212 hebben mislukte pogingen; mediane mislukkingsgraad 83 % (pogingen / (arbs + pogingen)); netto 768 − 125 SOL pogingskosten → 218 van de 326 blijven positief. De grootste (MriyaNN8…): 4.757 arbs, 376 SOL netto, 16,9 SOL fooien, 9.902 mislukte pogingen (8,0 SOL), mislukkingsgraad 68 %; andere top-10-spelers zitten op 59–99,8 % mislukt bij 0–8 SOL kosten. Mislukken is dus goedkoop (≈ 7.000 lamports per poging) en structureel: wie meedoet, verstuurt tientallen pogingen per gelande arb.

## 4. Wat een deelnemer nodig heeft en wat het oplevert (modelaanname, band)

- **Reactietijd.** Same-slot landen (93 % van het geld) vereist dat de order in hetzelfde blok als de trigger wordt opgenomen: een bundel of een pad naar de leader dat sneller is dan de blokbouw (colocatie, SWQoS). Dat is de infrastructuur van de top-3, die met 9 % fooien en mediaan 0,000174 SOL priority fee wint; de blokpositie wordt niet door de fooi bepaald (§1). Een VPS die op `processed` leest en dan verstuurt, landt op zijn vroegst in slot + 1 (de M-a-aanname voor K3) en concurreert om 3–4 % van het geld.
- **Fooibudget.** Bij een same-slot-pad zijn fooien 7 % en fees 26 % van bruto: een budget van ≈ 0,3 × bruto. Zonder same-slot-pad koopt een fooi niets (§2).
- **Verwachting voor een nieuwkomer zonder same-slot-pad** (band): de ≥ 1-slot-pot op de enige volle dag is 19,6 + 27,2 = 46,8 SOL, verdeeld over ≈ 200 fee-payers; proportioneel ≈ 0,2 SOL per dag; de bovengrens (alle 188 bereikbare kansen ≥ 0,01 SOL winnen) 44,5 SOL per dag, onbereikbaar zonder de landingskans te kennen. Daar gaan de mislukte pogingen af (83 % mislukt bij 0,000007 SOL per stuk is klein) en de niet-gelande pogingen die OF1 niet toont (onbekend). **Band: 0 … enkele SOL per dag, waarschijnlijk < 1.**
- **Niet uit OF1 te halen:** colocatie en leader-afstand, SWQoS, private orderflow, bundel-lidmaatschap (OF1 heeft geen bundel-ID), en de pogingen die de leader nooit opnam. Die bepalen juist wie same-slot wint.

## 5. Conclusie en wat niet

E17 blijft NO-GO. Meedoen op het niveau waar het geld zit, is een searcher met bundels en colocatie. Dat valt buiten de huidige kaders (Triton-only, paper-only, geen keys/signing/sending; elke live-stap is een apart owner-besluit): meedoen zou een apart owner-besluit vragen over searcher-infra, bundels (Jito) en colocatie. Kosten daarvan, voor zover gemeten: fooien 7 % en fees 26 % van de bruto winst bij een same-slot-pad (§2) plus ≈ 7.000 lamports per mislukte poging bij een mislukkingsgraad van 83 % (§3); de infrastructuurkosten zelf (colocatie, SWQoS, een bundel-pad) staan niet in OF1 en zijn hier onbekend. Schadelijke MEV (sandwiches, front-runs) blijft beschrijving en nooit een optie. Het restant voor een "langzame" deelnemer is te klein om te meten, laat staan te verdienen. Geen bouw, geen Triton-verkeer. Een herhaling na Alpenglow (OF1 ≥ 1064, bloktijden ≈ 150 ms, formaat te verifiëren) alleen op verzoek, met dezelfde verklaard-regel en nulsom-controle als vooraf vastgelegd. Dit is één dag, vóór Alpenglow, op verbruikte dev-data.

Script: `lab/wallets/explore/mev0_meedoen.py` (DuckDB-leesqueries op de parquet-uitvoer van job 96; alleen lezen, eigen scope). Geen nieuwe data.
