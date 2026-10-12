# Watchlist voor live paper trading: omvang, dekking en wat we live missen (2026-10-12)

Meting over de ontwikkelperiode (12–30 september 2026 UTC, 19 hele dagen; 11 september en 1 oktober zijn halve dagen en vallen af) met `lab/wallets/watchlist.py` op de NAS (commit fb07497) en `lab/wallets/trigger_gap.py` (41cba16). Ruwe uitvoer: `data-old-faithful-one/lab/wallets/watchlist-v1/` (report.md, stats per dag, leden per minuut) en `wallets/trigger-gap-v1/`. Alles point-in-time: de lijst voor minuut m + 1 komt uit data tot en met minuut m. Alleen adressen; labels zijn waarschijnlijkheden.

## Wat de lijst kost en dekt (regels zoals ontworpen: F7-reversed in bij ≥ 150 SOL, uit onder 120)

| | per dag (mediaan van de 19 dagwaarden; events en inname als gemiddelde) |
|---|---|
| pools op de lijst (mediaan / p95 / max per minuut) | 161 / 203 / 216 |
| waarvan F7-reversed / M1 | 102 / 65 |
| events op de lijst | 3,34 mln van 21,6 mln pool-events (15,4 %) |
| … F7-reversed / M1 | 1,65 mln / 1,69 mln |
| inname als volledige transacties (3 KB) / ruw (1 KB) | 10,3 GB / 3,4 GB |
| M1-beslismomenten (open + 15 min) gedekt | 100 % (6.122) |
| M1-trades ≤ 6 u gedekt | 100 % |
| toernooi-F7-triggers gedekt: alle / organisch | 24,4 % (34.712) / **73,9 %** (10.963) |
| F7-kandidaatsells (≥ 150 SOL diep, sell ≥ max(3 SOL, 4 %)) gedekt | 23,8 % (489.604) |

De lage dekking van *alle* triggers en kandidaatsells is gewenst: 98,5 % van de geflagde (farming-)triggers staat niet op de lijst, precies wat de organisch-regel moet doen. Het doel van 5 GB/dag wordt met volledige transacties niet gehaald (10,3 GB); ruw (Deshred, 1 KB) wel. M1 is de helft van de inname.

## Dagelijks verversen uit OF1 werkt niet: diepe reversed pools ontstaan dezelfde dag

Live zien we nieuwe reversed pools niet goedkoop (creatie-detectie kost 8–56 GB/dag, `lab/nas-results/creation-filter`). Het alternatief, de kandidatenset dagelijks uit OF1 verversen met 1–2 dagen vertraging, is gemeten tegen de echte triggers:

- **99,0 % van de toernooi-triggers (99,8 % van de organische) valt in een pool die dezelfde UTC-dag is aangemaakt.** Een universum ververst aan het eind van D−1 bevat 1,0 % van de triggers, aan het eind van D−2 0,0 %. Of je "bestond", "≥ 50 SOL" of "≥ 150 SOL" als criterium neemt maakt niets uit.
- Pools worden niet diep aangemaakt: 0,4 % van de triggers zit in een pool die met ≥ 150 SOL is aangemaakt (de meeste starten op ~85 SOL). De rest wordt later diep: **mediaan 4 minuten na aanmaak (organisch 2), p90 14 (organisch 7).**
- De eerste toernooi-trigger van een pool komt mediaan 5 minuten na aanmaak (p10 1, p90 22); de eerste organische mediaan 3 (p90 10).

Gevolg: live F7 heeft creatie-detectie nodig, en die moet binnen een minuut werken. LAB backtest bouwt dat in de recorder (#182): nieuwe reversed pools als vault-saldo volgen vanaf aanmaak en bij ≥ 150 SOL overschakelen op volledige transacties.

## Het gat vóór 150 SOL: de farmer-vlag van de verkoper

Tot de pool 150 SOL haalt zien we live alleen het vault-saldo, geen verkopers. De F7-trigger zelf komt daarna, maar de farmer-vlag van de verkoper komt uit zijn eigen trades in de pool in de 24 u ervoor (≥ 2 trades, |netto tokens| < 5 % van bruto). Per trigger (35.081, verkoper bekend) herberekend met en zonder de trades van vóór de eerste slot met ≥ 150 SOL (de herberekening over alle trades komt in 100 % overeen met de vlag uit het toernooi):

| triggers | n | verkoper heeft trades vóór 150 SOL | aandeel van zijn historie dat ervóór valt | vlag slaat om zonder die trades | … vlag verloren |
|---|---|---|---|---|---|
| alle | 35.081 | 82,2 % | 16,5 % | 11,0 % (3.847) | 2.251 |
| organisch | 11.101 | 73,5 % | 28,9 % | **14,4 %** (1.593) | 375 |
| geflagd | 23.980 | 86,3 % | 15,9 % | 9,4 % (2.254) | 1.876 |

Bij 14,4 % van de organische triggers geeft de verkoper zonder zijn vroege trades een andere vlag. Daarom bouwt LAB backtest een backfill vanaf de aanmaakslot (`from_slot`) bij de start van een probe.

## Waarom 26 % van de organische triggers niet op de lijst stond: de instapdrempel, niet de organisch-regel

De 2.857 gemiste organische triggers zijn nagelopen tegen de toestand die de lijst zag toen hij over de triggerminuut besliste (laatste reële diepte, farmer-aandeel in het voorbije uur, minuten stil):

| oorzaak | triggers | aandeel van de 10.963 |
|---|---|---|
| pool stond op het beslismoment op 120–150 SOL (vulde zich naar 150; de trigger-sell is de eerste trade die ≥ 150 ziet) | 2.255 | 20,6 % |
| geen enkele trade vóór de triggerminuut (pool aangemaakt en getriggerd binnen dezelfde minuut) | 286 | 2,6 % |
| pool op 100–120 SOL | 207 | 1,9 % |
| pool onder 100 SOL | 48 | 0,4 % |
| diepte ≥ 150 en organisch, toch niet op de lijst (verdict van de vorige minuut) | 60 | 0,5 % |
| farming-uur (farmers > 50 % van het volume) | 1 | 0,0 % |
| 6 uur stil | 0 | 0 % |

Van de 2.571 gemiste triggers met een trade ervoor was de pool in 2.567 gevallen organisch volgens de eigen uur-regel. De organisch-definitie (voorbije uur tegenover het kalenderuur van het toernooi), de hysterese en de 6-uurregel verklaren samen minder dan 1 procentpunt. De oorzaak is dat de lijst instapt op dezelfde drempel waarop F7 triggert (150 SOL): een pool die zich van 85 naar 150 SOL vult staat er pas op na een minuut waarin de laatste trade al ≥ 150 zag, en bij de meeste organische pools is dat de triggerminuut zelf.

**Lagere instapdrempel.** Dezelfde dag (27 september) met andere drempels, zelfde code (`LAB_WATCHLIST_F7_ENTER` / `LAB_WATCHLIST_F7_EXIT`):

| in / uit (SOL) | F7-pools op de lijst (mediaan / max) | events op de lijst | GB/dag (3 KB) | organische triggers gedekt (451) | alle triggers | kandidaatsells |
|---|---|---|---|---|---|---|
| 150 / 120 (gemeten) | 63 / 77 | 3,23 mln | 9,93 | 70,5 % | 21,4 % | 21,1 % |
| 120 / 100 | 82 / 108 | 3,68 mln | 11,32 | **94,0 %** | 28,5 % | 23,9 % |
| 100 / 80 | 119 / 160 | 4,20 mln | 12,89 | 95,6 % | 28,9 % | 23,9 % |

Instap op 120 SOL (uit onder 100) haalt de dekking van 70 % naar 94 % voor 14 % meer events (+1,4 GB/dag als volledige transacties); 100 SOL voegt daar nog 1,6 punt aan toe voor nog eens 1,6 GB. Advies: 120 / 100. De volledige periode met 120 / 100 staat hieronder.

**Volledige periode met 120 / 100** (VPS-run op dezelfde code, uitvoer `wallets/watchlist-v1-enter120/`):

| | 150 / 120 | 120 / 100 |
|---|---|---|
| pools op de lijst (mediaan / p95 / max per minuut) | 161 / 203 / 216 | 188 / 234 / 246 |
| waarvan F7-reversed (mediaan / max) | 102 / 115 | 131 / 151 |
| events op de lijst per dag | 3,34 mln (F7 1,65 mln) | 3,97 mln (F7 2,28 mln) |
| inname volledige transacties / ruw | 10,3 / 3,4 GB | 12,2 / 4,1 GB |
| organische toernooi-triggers gedekt (10.963) | 73,9 % (per dag 45–82 %) | **95,6 %** (per dag 86–100 %) |
| alle toernooi-triggers / kandidaatsells gedekt | 24,4 % / 23,8 % | 31,4 % / 25,9 % |
| M1-beslismomenten / M1-trades ≤ 6 u | 100 % / 100 % | 100 % / 100 % |

De resterende 4,4 % zijn vooral pools die binnen dezelfde minuut worden aangemaakt en getriggerd (2,6 %) en pools die nog onder 120 SOL stonden.

## Wat dit betekent voor de recorder (Week 1 beginning, #182)

1. Creatie-detectie van reversed pools is verplicht; dagelijks verversen uit OF1 dekt 0–1 %.
2. De F7-lijst stapt in op 120 SOL reële diepte en gaat eruit onder 100; de trigger blijft 150 SOL. Dat is een instelling van `Rules`, geen nieuwe regel.
3. Een nieuwe pool wordt mediaan 2–4 minuten na aanmaak diep en triggert mediaan 3–5 minuten na aanmaak; de wissel van vault-saldo naar volledige transacties moet per seconde, niet per minuut.
4. De farmer-vlag heeft bij 14 % van de organische triggers de trades van vóór 150 SOL nodig: backfill vanaf de aanmaakslot bij de start van een probe.
5. Inname: 10,3 GB/dag als volledige transacties bij 150 / 120, 12,2 GB bij 120 / 100; ruw een derde daarvan. M1 is de helft; de F7-helft groeit met de lagere drempel.

Beperkingen: ontwikkeldata, 19 dagen; de toernooi-triggers zijn die van `f7rev-1033-1046` (0,5 SOL, d = 1, één variant); het organisch-label van het toernooi geldt per kalenderuur en kent ook trades na de trigger, de lijst alleen het voorbije uur. Vault-updates van alle pools, de curve en de gevolgde wallets zitten niet in de inname-telling.
