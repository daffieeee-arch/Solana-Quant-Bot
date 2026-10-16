# K2 point-in-time: fabrieks-reversed-pools zijn herkenbaar, maar niet te verhandelen (dood spoor)

LAB research, 2026-10-17. Vervolg op `2026-10-17-winnaars-traag.md` §3c/§6 (E33, kandidaat K2). Opdracht (coördinator): herken de fabriekspools zonder het cluster te kennen, met alleen wat in minuut 1–2 zichtbaar is; instap in minuut 2–7 met de kosten en vertraging van de engine (d 2/3); per dagblok; aandeel van één operator; dagtrend. Alleen dev 1033–1046; hold-out en clean niet gelezen. Verkennend; labels **feit** / **reconstructie** / **modelaanname** / **onbekend**.

## 0. Samenvatting

- **Herkenbaar:** ja, scherp. Een reversed pool met een eenmalige creator die nooit eerder handelde en ≥ 300 SOL startliquiditeit (b0) is in 92 % een fabriekspool en vangt 98 % van de 3.312; met ≥ 40 traders en ≥ 30 SOL netto-instroom in de eerste 2 minuten erbij 99 % precisie bij 89 % recall. Alles point-in-time (creator-historie, b0, instroom) — *feit*.
- **Niet te verhandelen:** de pools zijn een **5-minutencyclus**: 21 % is binnen 1 min na de eerste trade door de creator gedraineerd (LP-withdraw), 52 % binnen 2 min, 70 % binnen 3, 90 % binnen 5 en 99,9 % binnen 7 min — *feit*. Met 0,5 SOL, de base-kosten en d = 2/3 is **elke** combinatie van instap (minuut 1 of 2) en houdtijd (30 s – 5 min) negatief: best −0,04 SOL per pool (minuut 1, 30 s), slechtst −0,13 (minuut 2, 5 min); dagsommen −1 … −19 SOL, **geen enkele dag positief**, in beide dagblokken — *modelaanname (constant product op de gelogde reserves, afschrijving bij een withdraw vóór de exit)*.
- **Eén operator:** 96–97 % van het resultaat van de regel komt uit de 3.312 fabriekspools (het cluster-label); de 21 niet-fabriekspools die de regel ook vangt verliezen alles (−0,50 per pool). De dagwinst van het cluster zelf daalde over dev van 150–185 naar 55–80 SOL (winnaars II §3c).
- **Waarom het cluster wél wint:** het stapt op ~1 min in, houdt mediaan ~1 min en wint 95–100 % terwijl 21–52 % van de pools in dat venster wordt gedraineerd. Dat kan alleen met kennis van het drainmoment (zelfde operator, of een reactie op de withdraw-transactie binnen hetzelfde slot). Een regel op 2–3 slots vertraging met een vaste houdtijd heeft die kennis niet — *reconstructie*.
- **Conclusie:** K2 is een dood spoor; register E33 bijgewerkt. Geen vastlegging, geen enginewerk.

## 1. Data en methode

- Universum: alle reversed pools gecreëerd in dev (`pools.created_in_period`, niet-mayhem): **51.248**, waarvan 3.312 fabriekspools (het label uit winnaars II: verhandeld door het cluster van 25 bots) — *feit*. Script `lab/wallets/winners/k2_pools.py` → `wallets/winners-v1/k2_pools.parquet`.
- Kenmerken op minuut 2 na de eerste trade (point-in-time): creator (eerder gecreëerde pools; eerder gehandeld, ooit; eigen trades in de pool), b0/q0 en LP-supply, `create → eerste trade`, en over [eerste trade, +120 s): trades, traders, kopers, koop-/verkoop-SOL, netto-instroom, grootste koop, prijsmultiple, tip-aandeel, farmer-aandeel, deposits/withdraws/boosts.
- Simulatie (`k2_sim.py` → `k2_sim.parquet`): 0,5 SOL kopen op eerste trade + 60 s of + 120 s, plus d = 2 of 3 slots, tegen de laatst gelogde reserves vóór dat slot (constant product, fee-bps van de pool, 2 × 0,000705 SOL kosten); verkopen na 30 / 60 / 120 / 180 / 300 s + d; staat een LP-withdraw vóór het exit-slot, dan is de positie afgeschreven (−0,5 SOL − kosten; D-noot: de eerste withdraw is in 92 % de drain). Geen landingskans (0,97) en geen adverse execution meegenomen: de uitkomst is daarmee eerder te gunstig — *modelaanname*.

## 2. Herkenning op minuut 2 (feit)

| kenmerk | fabriek p10 / p50 / p90 | overige reversed pools p10 / p50 / p90 |
|---|---|---|
| eerder gecreëerde pools door de creator | 0 / 0 / 0 | 0 / 7 / 11.953 |
| creator ooit eerder gehandeld | 0 % | 50 % |
| create → eerste trade (slots) | 30 / 37 / 146 | 0 / 1 / 29 |
| b0, startliquiditeit (SOL) | 374 / 447 / 479 | 81 / 85 / 168 |
| traders in 2 min | 45 / 85 / 111 | 87 / 230 / 856 |
| koop-SOL in 2 min | 71 / 129 / 222 | 1,1 / 4,3 / 362 |
| netto-instroom in 2 min (SOL) | 42 / 74 / 108 | 1,0 / 3,8 / 56 |
| grootste koop (SOL) | 3,5 / 6,9 / 12,6 | 0,03 / 0,22 / 30 |
| tip-aandeel | 0 | 0 / 0,002 / 0,024 |
| LP-supply | 5,4e11 / 6,9e11 / 6,3e14 | 3,3e12 / 9,2e12 / 1,3e13 |

| regel (alleen minuut-1–2-informatie) | matches | fabriek | precisie | recall | dag 1–9 / 10–18 |
|---|---|---|---|---|---|
| R0 eenmalige creator, nooit gehandeld | 13.877 | 3.312 | 24 % | 100 % | 6.171 / 7.706 |
| R1 R0 + b0 ≥ 300 SOL | 3.518 | 3.237 | 92 % | 98 % | 1.364 / 2.154 |
| R2 R1 + ≥ 40 traders + instroom ≥ 30 SOL | 2.996 | 2.974 | 99 % | 90 % | 1.103 / 1.893 |
| **R3** R2 + geen tips, geen creator-trades | **2.980** | 2.959 | **99 %** | 89 % | 1.097 / 1.883 |
| R4 alleen b0 ≥ 300 | 5.816 | 3.237 | 56 % | 98 % | 2.566 / 3.250 |
| R5 alleen ≥ 40 traders + instroom ≥ 30 | 11.757 | 3.039 | 26 % | 92 % | 4.686 / 7.071 |

Elke fabriekspool heeft een eigen, verse creator (3.312 creators voor 3.312 pools), die zelf de LP terugtrekt; ~206 traders en ~300 SOL volume per pool, 150–200 pools per dag (winnaars II §3c).

## 3. Drains en simulatie (modelaanname)

Drain-timing van de R3-matches na de eerste trade: binnen 1 min 20,7 %, 2 min 52,2 %, 3 min 70,1 %, 5 min 90,4 %, 7 min 99,9 % — *feit*.

Gemiddeld netto SOL per pool bij 0,5 SOL, d = 3 (d = 2 wijkt < 0,001 af), R3-matches die fabriek zijn (n 1.089 dag 1–9 / 1.870 dag 10–18); tussen haakjes het winstaandeel:

| instap | blok | 30 s | 60 s | 120 s | 180 s | 300 s |
|---|---|---|---|---|---|---|
| minuut 1 | dag 1–9 | −0,044 (0,75) | −0,047 (0,81) | −0,051 (0,79) | −0,055 (0,75) | −0,047 (0,73) |
| minuut 1 | dag 10–18 | −0,048 (0,77) | −0,062 (0,80) | −0,077 (0,76) | −0,089 (0,71) | −0,098 (0,67) |
| minuut 2 | dag 1–9 | −0,090 (0,57) | −0,087 (0,70) | −0,091 (0,72) | −0,090 (0,72) | −0,086 (0,71) |
| minuut 2 | dag 10–18 | −0,101 (0,57) | −0,106 (0,67) | −0,117 (0,70) | −0,123 (0,68) | −0,127 (0,65) |

- Het winstaandeel is hoog (65–81 %) en de mediaan positief (+0,011 / +0,024 SOL bij minuut 1, 30 / 60 s), maar elke gedraineerde pool kost de volle 0,5 SOL (p5 = −0,501): 12–13 % drains bij 30 s, 21–24 % bij 120 s, 28–34 % bij 300 s na een instap op minuut 1 — de verwachte waarde is overal negatief.
- Σ over alle R3-matches, instap minuut 1, d = 3, per houdtijd 30 / 60 / 120 / 180 / 300 s: dag 1–9 −50 / −55 / −59 / −64 / −55 SOL; dag 10–18 −95 / −122 / −151 / −172 / −190. Instap minuut 2: −102 … −98 en −195 … −245.
- Per dag (minuut 1, 30 s, d = 3): van −0,02 (11 sep, 3 pools) tot −19,2 SOL (26 sep); **21 van 21 dagen negatief**; 96–97 % van de som uit fabriekspools.
- De 21 R3-matches die géén fabriekspool zijn verliezen in elke cel (vrijwel) alles: −0,29 … −0,50 per pool.
- De 353 fabriekspools die R3 mist (lagere instroom) zijn nog slechter: −0,32 … −0,37 per pool.

## 4. Oordeel

K2 valt af. De herkenning is geen probleem, de cyclus wel: de operator draineert de pool binnen minuten, en het cluster dat eraan verdient heeft het drainmoment vóór ons. Een "minuut 1, ≤ 30 s"-regel verliest nog steeds (drains van 12 % × 0,5 SOL tegen winsten van ~0,01–0,05), draait op één fabriek, en die fabriek's opbrengst daalde over dev. Geen hypothese, geen vastlegging; E33 noteert het als dood spoor. Wat blijft: de kenmerken in §2 zijn een bruikbare **uitsluiting** (zo'n pool is een pump-en-drain in wording) voor welk filter dan ook op reversed pools; de F7-trigger vuurt er al zelden (1 % van de clusterrondes).

## 5. Open punten

- Of cluster en fabriek één operator zijn: *onbekend* (geen funding-data op de VPS).
- De simulatie neemt geen landingskans en geen adverse execution mee; beide maken het alleen slechter.
- K1 (trage curve-instap van een persistente wallet volgen) blijft geparkeerd tot de persistentieset houdbaar blijkt (coördinator 2026-10-17).
