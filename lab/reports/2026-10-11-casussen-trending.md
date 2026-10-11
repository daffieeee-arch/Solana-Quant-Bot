# Casussen uit eigen data: "trending", dumpers en blijvers (2026-10-11)

Op verzoek van de eigenaar, via de coördinator. Alleen ontwikkeldata (vóór de hold-out), alleen pump-tokens met SOL-quote in hun eigen migratiepool; reversed pools en andere pools doen niet mee (dat zijn farming-plekken). Selectie en tijdlijnen: `lab/wallets/cases.py`, rapport: `lab/wallets/cases_report.py`. Ruwe output: `data-old-faithful-one/lab/wallets/cases-v1-sample/`. De DexScreener-links zijn om zelf te kijken; het is geen databron.

## Wat je ziet, in gewoon Nederlands

1. **"Trending 24 u" (top-5 op 24-uurs-rendement × volume) is bijna helemaal nep.** De winnaars op 25 en 26 september zijn tokens die in hun eigen create-slot gradueerden: één koper (de maker zelf) kocht de hele curve (85 SOL), en daarna stortte dezelfde partij 8.000–27.000 SOL in de pool, waardoor de koers ×10.000 tot ×100.000 boven de openingsprijs kwam. Het zijn steeds dezelfde namen (AROS, VSOF, USDF, WOFI, WSOS) met telkens een nieuwe mint, en ze worden binnen een dag weer leeggehaald: de grootste verkopen zijn 10.500–10.800 SOL in één klap na ~1.000 minuten, vaak door de creator zelf. Een "mcap" van tientallen miljoenen SOL is hier een rekenartefact van een leeggekochte pool: niemand kan er tegen die prijs uit.
2. **De echte hype-tokens staan bij de dumpers.** SOLRISING, IMU, PEEP, SHIBA (25 sept) en Cream, DEBT, act/acc, P(DOOM) (26 sept) hadden 2.900–13.900 handelaren en 33.000–66.000 SOL volume op een dag, piekten op 90–450× de openingsprijs en eindigden op 0–10% van hun piek. Dit is het beeld dat de eigenaar in Phantom ziet: hype, piek, instorting.
3. **Na 7 dagen blijven er weinig over.** PAID (88.000 handelaren, 1,3 mln SOL volume, nog ~116.000 SOL mcap na een week op een derde van zijn piek), JEANPHIL (52.000 handelaren), MCAT, WEPE en MINESTOCK zijn echte overlevers. WSOS, FAIR en fomo in diezelfde lijst zijn weer de zelfgefinancierde structuur (145–2.300 handelaren, koers nooit bewogen). De "instorters" met een piek-mcap van 15–42 miljoen SOL zijn allemaal AROS/USDF/VSOF/WOFI-kopieën: dezelfde operatie.
4. **Wat stijgers en dumpers in de eerste 15 minuten onderscheidt — en wat niet.** De mediaan van de "stijgers" heeft 1 koper op de curve, 100% grote kopers en een insider-aandeel van 0,79; de dumpers hebben 28 curve-kopers, 2.148 pool-traders in een kwartier en een insider-aandeel dat net zo hoog is (0,78). Netto instroom op de curve (85 SOL, de hele curve) en creator-verkopen (0) zijn gelijk. Het enige harde verschil is dus *wie* koopt: één partij tegenover een menigte. Dat voorspelt geen succes; het voorspelt welke van de twee soorten mislukking je krijgt. Geen van beide groepen had split-buyer-bundels in de eerste 15 minuten.
5. **Gevolg voor het onderzoek.** Een "trending"-lijst op koers × volume uit eigen data moet eerst de zelfgefinancierde structuur eruit filteren (graduatie in de create-slot én ≤ 2 curve-kopers, of één wallet die de pool voor > 50% vult). Dat filter is nu niet in de lijst gezet, zodat je ziet hoe dominant het is. In het runner-onderzoek (1b) zitten deze tokens apart ("graduatie in de create-slot": 879 van 1.622 graduaties op twee dagen).

Beperkingen: twee dagen voor de daglijsten; de 7-dagen-lijst loopt over de hele ontwikkelperiode. De koers is de reserve-prijs van de pool (incl. virtuele reserves bij boost-pools), dezelfde die een koersdienst toont. Labels van kopers (wallet-score) komen pas na de NAS-run.

## Per dag: trending 24 u en pumpers/dumpers

| dag | lijst | # | token | volume SOL | 24-u-rendement | piek × | eind t.o.v. piek | traders | SOL in pool (eind) |
|---|---|---|---|---|---|---|---|---|---|
| 2026-09-25 | dumpers_24h | 1 | [SOLRISING](https://dexscreener.com/solana/93Q1FohrzWzsvJXJujLW7PrZLZtpCPmviKyWKEX3pump) | 43,669 | -60% | 90.9 | 0% | 3332 | 10 |
| 2026-09-25 | dumpers_24h | 2 | [AROS](https://dexscreener.com/solana/Xc4WkwXwNmMApMmxvwT8994tLMUziPCgxBmmPC4pump) | 43,499 | -95% | 53517.5 | 0% | 3129 | 2 |
| 2026-09-25 | dumpers_24h | 3 | [PEEP](https://dexscreener.com/solana/jU7hpVbGfLPFzfHB1iy74hPA379iubPqVQwLGBepump) | 41,460 | +231% | 158.1 | 2% | 2889 | 31 |
| 2026-09-25 | dumpers_24h | 4 | [IMU](https://dexscreener.com/solana/33owuxq97P85XaV2oXvBFmMoykb3ZjLH5wdAV4uDpump) | 40,006 | -81% | 114.0 | 0% | 3319 | 12 |
| 2026-09-25 | dumpers_24h | 5 | [SHIBA](https://dexscreener.com/solana/3V43dHnk3kkU3w8RnxEsmbJcHQppGjTJ3Mni3owDpump) | 33,171 | -67% | 88.7 | 0% | 3199 | 7 |
| 2026-09-25 | trending_24h | 1 | [USDF](https://dexscreener.com/solana/qhyWQAfBWW8pmDUiNWVwq4e4SZWeMgEvJ3KRRespump) | 20,615 | +3892045% | 38936.3 | 100% | 3273 | 16,780 |
| 2026-09-25 | trending_24h | 2 | [VSOF](https://dexscreener.com/solana/mRRHbeK6G5kmT8wc3AwkSeijAQWEnzS69vmbQ1Gpump) | 15,869 | +2535837% | 25373.7 | 100% | 3030 | 13,548 |
| 2026-09-25 | trending_24h | 3 | [VSOF](https://dexscreener.com/solana/BYPmzwqUT8NcDTWeGFbMQqafg7JHvLxfepjfF4Ahpump) | 17,574 | +1991614% | 19917.1 | 100% | 132 | 12,007 |
| 2026-09-25 | trending_24h | 4 | [AROS](https://dexscreener.com/solana/QcakAHiP7BgZqzFupUEH5RzcpmoaYn9hWuBih62pump) | 14,102 | +1957655% | 19577.6 | 100% | 3102 | 11,893 |
| 2026-09-25 | trending_24h | 5 | [AROS](https://dexscreener.com/solana/57LVTCfaFUZ9tMM7vhJDgF3oebWt7GTzdrhRK5fqpump) | 14,275 | +1914340% | 19144.4 | 100% | 2763 | 11,761 |
| 2026-09-26 | dumpers_24h | 1 | [Cream](https://dexscreener.com/solana/CMuvWQ6qQbSt7NUQdPGcVKCoDgETieuqS68qu6BaeVgR) | 66,182 | +2760% | 296.6 | 10% | 12559 | 190 |
| 2026-09-26 | dumpers_24h | 2 | [DEBT](https://dexscreener.com/solana/FYM6PhwAS3QwTBwvgLj8JYeMgpNfKsWtW3hGxPM9eFkt) | 61,916 | +656% | 225.8 | 3% | 13887 | 77 |
| 2026-09-26 | dumpers_24h | 3 | [act/acc](https://dexscreener.com/solana/3gLCmbKsF2eps3JpFLykgVhkzi8Vh2wLAco7FUnvpump) | 53,060 | +9% | 454.0 | 0% | 3583 | 10 |
| 2026-09-26 | dumpers_24h | 4 | [AROS](https://dexscreener.com/solana/DsJDVBeNnUYvsJEaLyhNm6xrULzVDzuAPCVCHKRCpump) | 44,477 | -95% | 55994.4 | 0% | 2684 | 2 |
| 2026-09-26 | dumpers_24h | 5 | [P(DOOM)](https://dexscreener.com/solana/ARPwPPWbaj3FYBf6k1Lt2jqRkv9JJUg3Hxav5aHKTEem) | 37,916 | +2522% | 325.8 | 8% | 8628 | 124 |
| 2026-09-26 | trending_24h | 1 | [AROS](https://dexscreener.com/solana/kFWgzdFBd6DERYGSWUrT3ZUUjoREcv14rHJCBSbpump) | 30,673 | +10101265% | 101024.8 | 100% | 3056 | 27,045 |
| 2026-09-26 | trending_24h | 2 | [VSOF](https://dexscreener.com/solana/ehegfG1UQEFSYKcV6ReiMupjheDm22FoQQukiDspump) | 30,512 | +8763512% | 87636.1 | 100% | 3076 | 25,183 |
| 2026-09-26 | trending_24h | 3 | [AROS](https://dexscreener.com/solana/82w6YGQD7cb7SypvkoQrRTHG3qNzmVTNUcZ6njNRpump) | 10,219 | +914806% | 9149.1 | 100% | 2758 | 8,126 |
| 2026-09-26 | trending_24h | 4 | [manifest](https://dexscreener.com/solana/8vdJA3MYhUhirZZzjZ5i8jXhNkHgrCnirCrqDG23pump) | 21,834 | +282120% | 3306.6 | 85% | 2035 | 1,243 |
| 2026-09-26 | trending_24h | 5 | [XBC](https://dexscreener.com/solana/12ppTUnBWHbGy11h3QsXiat2sydhLAgjevu55jYzpump) | 8,070 | +712364% | 7125.2 | 100% | 3051 | 7,167 |

## 7 dagen: blijvers en instorters

| lijst | # | token | mcap na 7 d (SOL) | piek-mcap (SOL) | eind t.o.v. piek | volume 7 d | traders | SOL in pool (7 d) |
|---|---|---|---|---|---|---|---|---|
| collapsed_7d | 1 | [AROS](https://dexscreener.com/solana/12PUFAUzgLj1onv3wSqX9ZagZBiaxofEjTd24CnQpump) | 38 | 41,715,680 | 0% | 61,803 | 3201 | 9 |
| collapsed_7d | 2 | [USDF](https://dexscreener.com/solana/qhyWQAfBWW8pmDUiNWVwq4e4SZWeMgEvJ3KRRespump) | 25 | 36,695,981 | 0% | 60,642 | 3444 | 4 |
| collapsed_7d | 3 | [VSOF](https://dexscreener.com/solana/gBiiUhaYjAj4zCGSzKJa1S23ZgB9U7w8k3epbbQpump) | 95 | 34,877,626 | 0% | 58,335 | 3138 | 24 |
| collapsed_7d | 4 | [WOFI](https://dexscreener.com/solana/F2reRYPQUPkagQy7t5VaZgtiHqXNFQ91ej15GRypump) | 40 | 22,044,743 | 0% | 47,589 | 2055 | 9 |
| collapsed_7d | 5 | [AROS](https://dexscreener.com/solana/Xc4WkwXwNmMApMmxvwT8994tLMUziPCgxBmmPC4pump) | 24 | 21,989,268 | 0% | 43,418 | 3134 | 3 |
| collapsed_7d | 6 | [WOFI](https://dexscreener.com/solana/izUd2DKhKefxT44NCCNpVvaRb963MYeWFsubGVSpump) | 22 | 21,174,446 | 0% | 52,983 | 2049 | 3 |
| collapsed_7d | 7 | [USDF](https://dexscreener.com/solana/Ufwjn7mtvgUfRG4nMSxRS84ewXHzdHLJRGzMDBMpump) | 23 | 19,829,101 | 0% | 60,979 | 3536 | 3 |
| collapsed_7d | 8 | [WOFI](https://dexscreener.com/solana/RdSJAdbWBBgKejj8GDzmqcQPdpnhMPYBTNog77Qpump) | 20 | 16,231,901 | 0% | 40,934 | 2006 | 1 |
| collapsed_7d | 9 | [NTDA](https://dexscreener.com/solana/FfK9fj41K98EuNZK2oQDRWuoyT9vb4d4jR412HcMpump) | 30 | 16,201,153 | 0% | 38,681 | 2981 | 6 |
| collapsed_7d | 10 | [WOTF](https://dexscreener.com/solana/Zd8xgiFB1AGYTmZ78MS5APSXmDTNTLUJKuSGUD7pump) | 41 | 15,563,557 | 0% | 40,458 | 1992 | 10 |
| stayers_7d | 1 | [WSOS](https://dexscreener.com/solana/sEkKnqHR8KKrjMjFqurrzdyTn4hiivmM8JHKWmopump) | 388,046 | 388,171 | 100% | 9,578 | 1808 | 2,609 |
| stayers_7d | 2 | [WSOS](https://dexscreener.com/solana/6dWQ7YC1k5sydiAij5bdc8F5jGjnE6TFN5GSoYvdpump) | 336,876 | 336,966 | 100% | 6,477 | 145 | 2,427 |
| stayers_7d | 3 | [FAIR](https://dexscreener.com/solana/QbPr1ponxN4J7ozvkdh1dNzqc4zgdkE1RGygHeKpump) | 312,884 | 312,987 | 100% | 12,803 | 2326 | 2,343 |
| stayers_7d | 4 | [fomo](https://dexscreener.com/solana/UNHwuKaSGKP7qmYMFS5aSJdqifHj6CiXWvtoeYNpump) | 126,870 | 126,933 | 100% | 12,463 | 179 | 1,493 |
| stayers_7d | 5 | [PAID](https://dexscreener.com/solana/98kfF7rmsg1QDUEoCqNE7g7M1FdrTt92TEp2CLzypump) | 115,640 | 354,971 | 33% | 1,284,084 | 88126 | 2,797 |
| stayers_7d | 6 | [JEANPHIL](https://dexscreener.com/solana/GTBxUiw6wJdmmkCGZgRHLyYxqu1vG4KtRpeox6yDpump) | 66,427 | 109,954 | 60% | 422,187 | 52568 | 1,639 |
| stayers_7d | 7 | [MCAT](https://dexscreener.com/solana/7EA8EStQETtKkqFW8rqj77nfkiLEniVag8Y2jNUTpump) | 65,881 | 79,229 | 83% | 6,386 | 2182 | 1,066 |
| stayers_7d | 8 | [USDF](https://dexscreener.com/solana/DRMnFyekQiCTMrajtgsTycqp4ie6r1tZnh6qpAGjpump) | 41,153 | 60,265 | 68% | 12,557 | 3204 | 849 |
| stayers_7d | 9 | [WEPE](https://dexscreener.com/solana/EL6Urf21PoPesMrbYSRc1yqqHjhk6DGUR63VZDmmpump) | 38,101 | 124,752 | 31% | 9,886 | 1601 | 809 |
| stayers_7d | 10 | [MINESTOCK](https://dexscreener.com/solana/9a8PquAAAoo51TABtasHSXMbm3CPSRnkxZfn6F8Dpump) | 33,805 | 55,755 | 61% | 5,865 | 384 | 760 |

## Stijgers tegenover dumpers in de eerste 15 minuten

| kenmerk op +15 min | stijgers (mediaan) | dumpers (mediaan) |
|---|---|---|
| min. tot graduatie | 0.00 (n=10) | 0.03 (n=10) |
| unieke kopers curve | 1.00 (n=10) | 28.50 (n=10) |
| aandeel kopers ≥ 5 SOL | 1.00 (n=10) | 0.32 (n=10) |
| netto instroom curve (SOL) | 85.01 (n=10) | 85.01 (n=10) |
| insider-aandeel | 0.79 (n=10) | 0.78 (n=10) |
| creator-verkopen | 0.00 (n=10) | 0.00 (n=10) |
| traders pool | 676.50 (n=10) | 2148.00 (n=10) |
| organisch volume pool (SOL) | 8244.75 (n=10) | 9207.98 (n=10) |
| prijs t.o.v. open | 9416.72 (n=10) | 7.60 (n=10) |
| split-buyer-events | 0.00 (n=10) | 0.00 (n=10) |

## Tijdlijnen

**AROS** — American Reserved Oil Supply · [DexScreener](https://dexscreener.com/solana/Xc4WkwXwNmMApMmxvwT8994tLMUziPCgxBmmPC4pump) · `Xc4WkwXwNmMApMmxvwT8994tLMUziPCgxBmmPC4pump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 53530.5× de open-prijs (990 min na creatie), eind 24 u op 0.05× — graduatie in de create-slot
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 1 / 1; aandeel kopers ≥ 5 SOL 1.00 / 1.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.79 / 0.79; creator-verkopen 0 / 0; traders pool 360 / 935; organisch volume pool (SOL) 15242.31 / 16320.08; prijs t.o.v. open 31547.82 / 32796.57; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 10780 SOL na 992 min, 3146 SOL na 992 min, 1501 SOL na 992 min, 879 SOL na 992 min, 577 SOL na 992 min

**IMU** — IMU · [DexScreener](https://dexscreener.com/solana/33owuxq97P85XaV2oXvBFmMoykb3ZjLH5wdAV4uDpump) · `33owuxq97P85XaV2oXvBFmMoykb3ZjLH5wdAV4uDpump`

- Tijdlijn: graduatie 0.9 min na creatie, piek in de eerste 24 u op 55.7× de open-prijs (188 min na creatie), eind 24 u op 0.10×
- Op +5 / +15 min: min. tot graduatie 0.90 / 0.90; unieke kopers curve 54 / 54; aandeel kopers ≥ 5 SOL 0.02 / 0.02; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.46 / 0.00; creator-verkopen 0 / 0; traders pool 195 / 2435; organisch volume pool (SOL) 491.94 / 1643.59; prijs t.o.v. open 2.03 / 2.53; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 49 SOL na 219 min, 48 SOL na 170 min, 48 SOL na 186 min, 46 SOL na 188 min, 45 SOL na 178 min

**USDF** — United States Dividend Fund · [DexScreener](https://dexscreener.com/solana/qhyWQAfBWW8pmDUiNWVwq4e4SZWeMgEvJ3KRRespump) · `qhyWQAfBWW8pmDUiNWVwq4e4SZWeMgEvJ3KRRespump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 43000.6× de open-prijs (1216 min na creatie), eind 24 u op 42995.31× — graduatie in de create-slot
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 1 / 1; aandeel kopers ≥ 5 SOL 1.00 / 1.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.79 / 0.79; creator-verkopen 0 / 0; traders pool 1232 / 2366; organisch volume pool (SOL) 11267.01 / 11840.23; prijs t.o.v. open 17157.02 / 17718.26; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 8 SOL na 1211 min, 5 SOL na 770 min, 5 SOL na 9 min, 5 SOL na 6 min, 5 SOL na 7 min

**AROS** — AROS · [DexScreener](https://dexscreener.com/solana/57LVTCfaFUZ9tMM7vhJDgF3oebWt7GTzdrhRK5fqpump) · `57LVTCfaFUZ9tMM7vhJDgF3oebWt7GTzdrhRK5fqpump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 21381.6× de open-prijs (1330 min na creatie), eind 24 u op 0.05× — graduatie in de create-slot
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 1 / 1; aandeel kopers ≥ 5 SOL 1.00 / 1.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.79 / 0.79; creator-verkopen 0 / 0; traders pool 132 / 330; organisch volume pool (SOL) 9023.52 / 9076.84; prijs t.o.v. open 11254.91 / 11326.34; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 10503 SOL na 1333 min (creator), 664 SOL na 1333 min, 321 SOL na 1333 min, 189 SOL na 1333 min, 124 SOL na 1333 min

**VSOF** — VSOF · [DexScreener](https://dexscreener.com/solana/BYPmzwqUT8NcDTWeGFbMQqafg7JHvLxfepjfF4Ahpump) · `BYPmzwqUT8NcDTWeGFbMQqafg7JHvLxfepjfF4Ahpump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 22133.6× de open-prijs (1437 min na creatie), eind 24 u op 22133.59× — graduatie in de create-slot
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 1 / 1; aandeel kopers ≥ 5 SOL 1.00 / 1.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.80 / 0.80; creator-verkopen 0 / 0; traders pool 81 / 90; organisch volume pool (SOL) 5060.54 / 7412.66; prijs t.o.v. open 3570.36 / 7507.10; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 4 SOL na 632 min, 3 SOL na 199 min, 2 SOL na 10 min, 2 SOL na 20 min, 2 SOL na 1342 min

**VSOF** — VSOF · [DexScreener](https://dexscreener.com/solana/mRRHbeK6G5kmT8wc3AwkSeijAQWEnzS69vmbQ1Gpump) · `mRRHbeK6G5kmT8wc3AwkSeijAQWEnzS69vmbQ1Gpump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 29027.1× de open-prijs (1052 min na creatie), eind 24 u op 0.06× — graduatie in de create-slot
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 1 / 1; aandeel kopers ≥ 5 SOL 1.00 / 1.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.80 / 0.80; creator-verkopen 0 / 0; traders pool 322 / 897; organisch volume pool (SOL) 5045.84 / 5193.31; prijs t.o.v. open 3567.09 / 3735.29; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 6534 SOL na 1054 min, 2473 SOL na 1054 min, 1301 SOL na 1054 min, 802 SOL na 1054 min, 544 SOL na 1054 min

**SHIBA** — SHIBA · [DexScreener](https://dexscreener.com/solana/3V43dHnk3kkU3w8RnxEsmbJcHQppGjTJ3Mni3owDpump) · `3V43dHnk3kkU3w8RnxEsmbJcHQppGjTJ3Mni3owDpump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 16.6× de open-prijs (6 min na creatie), eind 24 u op 0.06× — graduatie in de create-slot
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 6 / 6; aandeel kopers ≥ 5 SOL 0.67 / 0.67; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.83 / 0.85; creator-verkopen 0 / 0; traders pool 1197 / 2367; organisch volume pool (SOL) 5682.60 / 15275.20; prijs t.o.v. open 12.77 / 7.80; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 15 SOL na 11 min, 15 SOL na 14 min, 15 SOL na 14 min, 14 SOL na 22 min, 13 SOL na 14 min

**SOLRISING** — SOL Rising · [DexScreener](https://dexscreener.com/solana/93Q1FohrzWzsvJXJujLW7PrZLZtpCPmviKyWKEX3pump) · `93Q1FohrzWzsvJXJujLW7PrZLZtpCPmviKyWKEX3pump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 17.0× de open-prijs (7 min na creatie), eind 24 u op 0.07× — graduatie in de create-slot
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 6 / 6; aandeel kopers ≥ 5 SOL 0.67 / 0.67; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.85 / 0.85; creator-verkopen 0 / 0; traders pool 982 / 2320; organisch volume pool (SOL) 5021.61 / 15248.44; prijs t.o.v. open 7.36 / 8.47; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 25 SOL na 5 min, 19 SOL na 9 min, 19 SOL na 8 min, 19 SOL na 28 min, 19 SOL na 29 min

**PEEP** — PEEP · [DexScreener](https://dexscreener.com/solana/jU7hpVbGfLPFzfHB1iy74hPA379iubPqVQwLGBepump) · `jU7hpVbGfLPFzfHB1iy74hPA379iubPqVQwLGBepump`

- Tijdlijn: graduatie 0.1 min na creatie, piek in de eerste 24 u op 10.8× de open-prijs (22 min na creatie), eind 24 u op 0.07×
- Op +5 / +15 min: min. tot graduatie 0.05 / 0.05; unieke kopers curve 48 / 48; aandeel kopers ≥ 5 SOL 0.08 / 0.08; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.83 / 0.83; creator-verkopen 0 / 0; traders pool 647 / 1676; organisch volume pool (SOL) 2539.69 / 8959.31; prijs t.o.v. open 5.83 / 5.70; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 33 SOL na 68 min, 30 SOL na 43 min, 29 SOL na 64 min, 29 SOL na 33 min, 28 SOL na 66 min

**AROS** — American Reserved Oil Supply · [DexScreener](https://dexscreener.com/solana/QcakAHiP7BgZqzFupUEH5RzcpmoaYn9hWuBih62pump) · `QcakAHiP7BgZqzFupUEH5RzcpmoaYn9hWuBih62pump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 21347.5× de open-prijs (429 min na creatie), eind 24 u op 0.06× — graduatie in de create-slot
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 1 / 1; aandeel kopers ≥ 5 SOL 1.00 / 1.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.79 / 0.79; creator-verkopen 0 / 0; traders pool 326 / 767; organisch volume pool (SOL) 9042.76 / 9581.25; prijs t.o.v. open 11199.93 / 11805.67; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 3897 SOL na 430 min, 2035 SOL na 430 min, 1251 SOL na 430 min, 846 SOL na 430 min, 611 SOL na 430 min

**Cream** — Cream · [DexScreener](https://dexscreener.com/solana/CMuvWQ6qQbSt7NUQdPGcVKCoDgETieuqS68qu6BaeVgR) · `CMuvWQ6qQbSt7NUQdPGcVKCoDgETieuqS68qu6BaeVgR`

- Tijdlijn: graduatie 0.2 min na creatie, piek in de eerste 24 u op 39.3× de open-prijs (260 min na creatie), eind 24 u op 4.63×
- Op +5 / +15 min: min. tot graduatie 0.20 / 0.20; unieke kopers curve 178 / 178; aandeel kopers ≥ 5 SOL 0.01 / 0.01; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.05 / 0.05; creator-verkopen 34 / 34; traders pool 2410 / 3362; organisch volume pool (SOL) 5068.36 / 8178.67; prijs t.o.v. open 3.84 / 0.77; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 57 SOL na 66 min, 48 SOL na 209 min, 43 SOL na 255 min, 40 SOL na 545 min, 37 SOL na 390 min

**AROS** — AROS · [DexScreener](https://dexscreener.com/solana/DsJDVBeNnUYvsJEaLyhNm6xrULzVDzuAPCVCHKRCpump) · `DsJDVBeNnUYvsJEaLyhNm6xrULzVDzuAPCVCHKRCpump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 56008.1× de open-prijs (178 min na creatie), eind 24 u op 0.05× — graduatie in de create-slot
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 1 / 1; aandeel kopers ≥ 5 SOL 1.00 / 1.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.80 / 0.80; creator-verkopen 0 / 0; traders pool 558 / 687; organisch volume pool (SOL) 6008.86 / 7445.77; prijs t.o.v. open 5053.22 / 2963.49; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 7287 SOL na 178 min, 3413 SOL na 178 min, 1980 SOL na 178 min, 1293 SOL na 178 min, 911 SOL na 178 min

**AROS** — AROS · [DexScreener](https://dexscreener.com/solana/kFWgzdFBd6DERYGSWUrT3ZUUjoREcv14rHJCBSbpump) · `kFWgzdFBd6DERYGSWUrT3ZUUjoREcv14rHJCBSbpump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 103320.1× de open-prijs (1437 min na creatie), eind 24 u op 103320.13× — graduatie in de create-slot
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 1 / 1; aandeel kopers ≥ 5 SOL 1.00 / 1.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.79 / 0.79; creator-verkopen 0 / 0; traders pool 146 / 586; organisch volume pool (SOL) 15071.81 / 15230.01; prijs t.o.v. open 31122.66 / 31661.97; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 9 SOL na 921 min, 4 SOL na 1070 min, 4 SOL na 985 min, 3 SOL na 127 min, 3 SOL na 269 min

**VSOF** — VSOF · [DexScreener](https://dexscreener.com/solana/ehegfG1UQEFSYKcV6ReiMupjheDm22FoQQukiDspump) · `ehegfG1UQEFSYKcV6ReiMupjheDm22FoQQukiDspump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 92246.5× de open-prijs (1334 min na creatie), eind 24 u op 0.06× — graduatie in de create-slot
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 1 / 1; aandeel kopers ≥ 5 SOL 1.00 / 1.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.79 / 0.79; creator-verkopen 0 / 0; traders pool 276 / 792; organisch volume pool (SOL) 20526.14 / 21578.64; prijs t.o.v. open 55739.57 / 56359.32; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 15317 SOL na 1337 min, 3885 SOL na 1337 min, 1774 SOL na 1337 min, 1016 SOL na 1337 min, 659 SOL na 1337 min

**XBC** — Xpress Bridge Currency · [DexScreener](https://dexscreener.com/solana/12ppTUnBWHbGy11h3QsXiat2sydhLAgjevu55jYzpump) · `12ppTUnBWHbGy11h3QsXiat2sydhLAgjevu55jYzpump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 7951.6× de open-prijs (1437 min na creatie), eind 24 u op 7951.61× — graduatie in de create-slot
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 1 / 1; aandeel kopers ≥ 5 SOL 1.00 / 1.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.80 / 0.80; creator-verkopen 0 / 0; traders pool 104 / 406; organisch volume pool (SOL) 5912.26 / 5971.19; prijs t.o.v. open 4879.76 / 4934.12; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 15 SOL na 1388 min, 13 SOL na 1199 min, 4 SOL na 986 min, 3 SOL na 943 min, 2 SOL na 1185 min

**P(DOOM)** — p(doom) · [DexScreener](https://dexscreener.com/solana/ARPwPPWbaj3FYBf6k1Lt2jqRkv9JJUg3Hxav5aHKTEem) · `ARPwPPWbaj3FYBf6k1Lt2jqRkv9JJUg3Hxav5aHKTEem`

- Tijdlijn: graduatie 3.2 min na creatie, piek in de eerste 24 u op 25.2× de open-prijs (72 min na creatie), eind 24 u op 1.38×
- Op +5 / +15 min: min. tot graduatie 3.25 / 3.25; unieke kopers curve 309 / 309; aandeel kopers ≥ 5 SOL 0.00 / 0.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.14 / 0.12; creator-verkopen 2 / 2; traders pool 937 / 1690; organisch volume pool (SOL) 1665.73 / 3472.27; prijs t.o.v. open 3.94 / 4.24; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 53 SOL na 98 min, 53 SOL na 138 min, 46 SOL na 156 min, 46 SOL na 62 min, 42 SOL na 156 min

**AROS** — AROS · [DexScreener](https://dexscreener.com/solana/82w6YGQD7cb7SypvkoQrRTHG3qNzmVTNUcZ6njNRpump) · `82w6YGQD7cb7SypvkoQrRTHG3qNzmVTNUcZ6njNRpump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 11187.6× de open-prijs (1163 min na creatie), eind 24 u op 0.04× — graduatie in de create-slot
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 1 / 1; aandeel kopers ≥ 5 SOL 1.00 / 1.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.80 / 0.80; creator-verkopen 0 / 0; traders pool 130 / 329; organisch volume pool (SOL) 6020.48 / 6070.75; prijs t.o.v. open 5062.99 / 5110.10; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 5701 SOL na 1166 min, 1018 SOL na 1166 min, 427 SOL na 1166 min, 415 SOL na 1166 min, 387 SOL na 1166 min

**act/acc** — ACT I: Effective Accelerationism · [DexScreener](https://dexscreener.com/solana/3gLCmbKsF2eps3JpFLykgVhkzi8Vh2wLAco7FUnvpump) · `3gLCmbKsF2eps3JpFLykgVhkzi8Vh2wLAco7FUnvpump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 31.0× de open-prijs (16 min na creatie), eind 24 u op 0.07×
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 9 / 9; aandeel kopers ≥ 5 SOL 0.56 / 0.56; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.78 / 0.77; creator-verkopen 0 / 0; traders pool 606 / 1976; organisch volume pool (SOL) 3146.33 / 14939.08; prijs t.o.v. open 10.38 / 23.28; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 33 SOL na 7 min, 30 SOL na 34 min, 28 SOL na 21 min, 26 SOL na 14 min, 26 SOL na 16 min

**DEBT** — DEBT COIN · [DexScreener](https://dexscreener.com/solana/FYM6PhwAS3QwTBwvgLj8JYeMgpNfKsWtW3hGxPM9eFkt) · `FYM6PhwAS3QwTBwvgLj8JYeMgpNfKsWtW3hGxPM9eFkt`

- Tijdlijn: graduatie 0.6 min na creatie, piek in de eerste 24 u op 23.4× de open-prijs (37 min na creatie), eind 24 u op 0.63×
- Op +5 / +15 min: min. tot graduatie 0.62 / 0.62; unieke kopers curve 206 / 206; aandeel kopers ≥ 5 SOL 0.00 / 0.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.03 / 0.00; creator-verkopen 8 / 8; traders pool 1997 / 3553; organisch volume pool (SOL) 4130.84 / 9456.66; prijs t.o.v. open 8.55 / 7.39; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 66 SOL na 67 min, 49 SOL na 37 min, 48 SOL na 70 min, 39 SOL na 143 min, 35 SOL na 115 min

**manifest** — manifest getting paid · [DexScreener](https://dexscreener.com/solana/8vdJA3MYhUhirZZzjZ5i8jXhNkHgrCnirCrqDG23pump) · `8vdJA3MYhUhirZZzjZ5i8jXhNkHgrCnirCrqDG23pump`

- Tijdlijn: graduatie 2.3 min na creatie, piek in de eerste 24 u op 293.3× de open-prijs (197 min na creatie), eind 24 u op 0.09×
- Op +5 / +15 min: min. tot graduatie 2.32 / 2.32; unieke kopers curve 229 / 229; aandeel kopers ≥ 5 SOL 0.02 / 0.02; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.03 / 0.00; creator-verkopen 0 / 0; traders pool 588 / 808; organisch volume pool (SOL) 752.00 / 1467.47; prijs t.o.v. open 1.27 / 3.87; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 85 SOL na 228 min, 81 SOL na 212 min, 75 SOL na 105 min, 73 SOL na 150 min, 65 SOL na 219 min

**FAIR** — Federal AI Reserve · [DexScreener](https://dexscreener.com/solana/QbPr1ponxN4J7ozvkdh1dNzqc4zgdkE1RGygHeKpump) · `QbPr1ponxN4J7ozvkdh1dNzqc4zgdkE1RGygHeKpump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 445.4× de open-prijs (1216 min na creatie), eind 24 u op 445.37× — graduatie in de create-slot
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 1 / 1; aandeel kopers ≥ 5 SOL 1.00 / 1.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.80 / 0.80; creator-verkopen 0 / 0; traders pool 55 / 120; organisch volume pool (SOL) 1604.67 / 1616.07; prijs t.o.v. open 393.51 / 394.69; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 1 SOL na 1121 min, 1 SOL na 6 min, 0 SOL na 6 min, 0 SOL na 207 min, 0 SOL na 681 min

**MCAT** — Mooncat · [DexScreener](https://dexscreener.com/solana/7EA8EStQETtKkqFW8rqj77nfkiLEniVag8Y2jNUTpump) · `7EA8EStQETtKkqFW8rqj77nfkiLEniVag8Y2jNUTpump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 121.5× de open-prijs (1148 min na creatie), eind 24 u op 109.20× — graduatie in de create-slot
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 1 / 1; aandeel kopers ≥ 5 SOL 1.00 / 1.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.81 / 0.81; creator-verkopen 0 / 0; traders pool 25 / 120; organisch volume pool (SOL) 732.36 / 739.49; prijs t.o.v. open 93.66 / 94.63; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 10 SOL na 823 min, 10 SOL na 982 min, 10 SOL na 437 min, 10 SOL na 1363 min, 9 SOL na 1363 min

**NTDA** — NTDA · [DexScreener](https://dexscreener.com/solana/FfK9fj41K98EuNZK2oQDRWuoyT9vb4d4jR412HcMpump) · `FfK9fj41K98EuNZK2oQDRWuoyT9vb4d4jR412HcMpump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 39440.0× de open-prijs (1216 min na creatie), eind 24 u op 0.06× — graduatie in de create-slot
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 1 / 1; aandeel kopers ≥ 5 SOL 1.00 / 1.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.79 / 0.79; creator-verkopen 0 / 0; traders pool 127 / 324; organisch volume pool (SOL) 13528.21 / 13589.76; prijs t.o.v. open 25129.75 / 25274.15; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 91 SOL na 305 min, 86 SOL na 305 min, 84 SOL na 305 min, 82 SOL na 305 min, 82 SOL na 305 min

**JEANPHIL** — Jean Phil · [DexScreener](https://dexscreener.com/solana/GTBxUiw6wJdmmkCGZgRHLyYxqu1vG4KtRpeox6yDpump) · `GTBxUiw6wJdmmkCGZgRHLyYxqu1vG4KtRpeox6yDpump`

- Tijdlijn: graduatie 51.2 min na creatie, piek in de eerste 24 u op 267.7× de open-prijs (1127 min na creatie), eind 24 u op 135.97×
- Op +5 / +15 min: min. tot graduatie 51.09 / 51.09; unieke kopers curve 374 / 374; aandeel kopers ≥ 5 SOL 0.01 / 0.01; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.02 / 0.02; creator-verkopen 0 / 0; traders pool 440 / 737; organisch volume pool (SOL) 506.23 / 986.87; prijs t.o.v. open 2.57 / 2.23; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 79 SOL na 447 min, 77 SOL na 910 min, 75 SOL na 217 min, 69 SOL na 86 min, 68 SOL na 178 min

**WSOS** — World Strategic Oil Supply🔥 · [DexScreener](https://dexscreener.com/solana/6dWQ7YC1k5sydiAij5bdc8F5jGjnE6TFN5GSoYvdpump) · `6dWQ7YC1k5sydiAij5bdc8F5jGjnE6TFN5GSoYvdpump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 230.8× de open-prijs (1216 min na creatie), eind 24 u op 230.60× — graduatie in de create-slot
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 1 / 1; aandeel kopers ≥ 5 SOL 1.00 / 1.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.81 / 0.81; creator-verkopen 0 / 0; traders pool 40 / 46; organisch volume pool (SOL) 933.05 / 1022.10; prijs t.o.v. open 138.21 / 141.29; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 1 SOL na 900 min, 1 SOL na 20 min, 1 SOL na 1 min, 1 SOL na 16 min, 1 SOL na 5 min

**AROS** — American Reserved Oil Supply · [DexScreener](https://dexscreener.com/solana/12PUFAUzgLj1onv3wSqX9ZagZBiaxofEjTd24CnQpump) · `12PUFAUzgLj1onv3wSqX9ZagZBiaxofEjTd24CnQpump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 63034.0× de open-prijs (1216 min na creatie), eind 24 u op 63013.24× — graduatie in de create-slot
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 1 / 1; aandeel kopers ≥ 5 SOL 1.00 / 1.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.79 / 0.79; creator-verkopen 0 / 0; traders pool 310 / 878; organisch volume pool (SOL) 15647.78 / 15714.95; prijs t.o.v. open 33486.83 / 33666.87; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 4 SOL na 594 min, 4 SOL na 592 min, 4 SOL na 609 min, 4 SOL na 611 min, 3 SOL na 604 min

**VSOF** — VSOF · [DexScreener](https://dexscreener.com/solana/gBiiUhaYjAj4zCGSzKJa1S23ZgB9U7w8k3epbbQpump) · `gBiiUhaYjAj4zCGSzKJa1S23ZgB9U7w8k3epbbQpump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 39105.4× de open-prijs (1216 min na creatie), eind 24 u op 39098.74× — graduatie in de create-slot
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 1 / 1; aandeel kopers ≥ 5 SOL 1.00 / 1.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.79 / 0.79; creator-verkopen 0 / 0; traders pool 325 / 895; organisch volume pool (SOL) 11031.57 / 11095.75; prijs t.o.v. open 16744.03 / 16864.41; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 4 SOL na 483 min, 4 SOL na 474 min, 4 SOL na 472 min, 4 SOL na 485 min, 4 SOL na 489 min

**WOFI** — WOFI · [DexScreener](https://dexscreener.com/solana/izUd2DKhKefxT44NCCNpVvaRb963MYeWFsubGVSpump) · `izUd2DKhKefxT44NCCNpVvaRb963MYeWFsubGVSpump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 33154.5× de open-prijs (1216 min na creatie), eind 24 u op 33154.52× — graduatie in de create-slot
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 1 / 1; aandeel kopers ≥ 5 SOL 1.00 / 1.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.79 / 0.79; creator-verkopen 0 / 0; traders pool 315 / 900; organisch volume pool (SOL) 9068.67 / 9177.37; prijs t.o.v. open 11308.51 / 11428.77; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 7 SOL na 1299 min, 7 SOL na 617 min, 4 SOL na 478 min, 4 SOL na 54 min, 4 SOL na 841 min

**WOFI** — WOFI · [DexScreener](https://dexscreener.com/solana/F2reRYPQUPkagQy7t5VaZgtiHqXNFQ91ej15GRypump) · `F2reRYPQUPkagQy7t5VaZgtiHqXNFQ91ej15GRypump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 53665.6× de open-prijs (1001 min na creatie), eind 24 u op 0.06× — graduatie in de create-slot
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 1 / 1; aandeel kopers ≥ 5 SOL 1.00 / 1.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.79 / 0.79; creator-verkopen 0 / 0; traders pool 309 / 907; organisch volume pool (SOL) 11090.86 / 11205.68; prijs t.o.v. open 16892.45 / 17046.72; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 16792 SOL na 1185 min, 1340 SOL na 1185 min, 496 SOL na 1185 min, 258 SOL na 1185 min, 158 SOL na 1186 min

**USDF** — United States Dividend Fund  · [DexScreener](https://dexscreener.com/solana/DRMnFyekQiCTMrajtgsTycqp4ie6r1tZnh6qpAGjpump) · `DRMnFyekQiCTMrajtgsTycqp4ie6r1tZnh6qpAGjpump`

- Tijdlijn: graduatie 0.8 min na creatie, piek in de eerste 24 u op 19.8× de open-prijs (1124 min na creatie), eind 24 u op 19.64×
- Op +5 / +15 min: min. tot graduatie 0.95 / 0.95; unieke kopers curve 16 / 16; aandeel kopers ≥ 5 SOL 0.12 / 0.12; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.94 / 0.98; creator-verkopen 0 / 0; traders pool 61 / 64; organisch volume pool (SOL) 34.57 / 46.45; prijs t.o.v. open 1.01 / 0.79; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 15 SOL na 1327 min, 14 SOL na 134 min, 13 SOL na 134 min, 12 SOL na 91 min, 11 SOL na 1193 min

**WOFI** — WOFI · [DexScreener](https://dexscreener.com/solana/RdSJAdbWBBgKejj8GDzmqcQPdpnhMPYBTNog77Qpump) · `RdSJAdbWBBgKejj8GDzmqcQPdpnhMPYBTNog77Qpump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 39514.8× de open-prijs (904 min na creatie), eind 24 u op 0.06× — graduatie in de create-slot
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 1 / 1; aandeel kopers ≥ 5 SOL 1.00 / 1.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.80 / 0.80; creator-verkopen 0 / 0; traders pool 315 / 912; organisch volume pool (SOL) 5266.35 / 5632.70; prijs t.o.v. open 3816.65 / 4206.19; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 13968 SOL na 1068 min (creator), 1337 SOL na 1070 min, 505 SOL na 1070 min, 265 SOL na 1070 min, 163 SOL na 1070 min

**fomo** — Fomo · [DexScreener](https://dexscreener.com/solana/UNHwuKaSGKP7qmYMFS5aSJdqifHj6CiXWvtoeYNpump) · `UNHwuKaSGKP7qmYMFS5aSJdqifHj6CiXWvtoeYNpump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 123.4× de open-prijs (1216 min na creatie), eind 24 u op 123.41× — graduatie in de create-slot
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 1 / 1; aandeel kopers ≥ 5 SOL 1.00 / 1.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.81 / 0.81; creator-verkopen 0 / 0; traders pool 19 / 32; organisch volume pool (SOL) 760.00 / 770.31; prijs t.o.v. open 100.08 / 101.34; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 1 SOL na 695 min, 1 SOL na 397 min, 1 SOL na 651 min, 1 SOL na 6 min, 0 SOL na 1360 min

**WOTF** — WOTF · [DexScreener](https://dexscreener.com/solana/Zd8xgiFB1AGYTmZ78MS5APSXmDTNTLUJKuSGUD7pump) · `Zd8xgiFB1AGYTmZ78MS5APSXmDTNTLUJKuSGUD7pump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 37887.8× de open-prijs (941 min na creatie), eind 24 u op 0.05× — graduatie in de create-slot
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 1 / 1; aandeel kopers ≥ 5 SOL 1.00 / 1.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.79 / 0.79; creator-verkopen 0 / 0; traders pool 136 / 386; organisch volume pool (SOL) 11099.74 / 11348.25; prijs t.o.v. open 16821.06 / 17205.47; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 14933 SOL na 1114 min, 770 SOL na 1114 min, 274 SOL na 1114 min, 140 SOL na 1114 min, 85 SOL na 1114 min

**PAID** — Paid · [DexScreener](https://dexscreener.com/solana/98kfF7rmsg1QDUEoCqNE7g7M1FdrTt92TEp2CLzypump) · `98kfF7rmsg1QDUEoCqNE7g7M1FdrTt92TEp2CLzypump`

- Tijdlijn: graduatie 1.8 min na creatie, piek in de eerste 24 u op 527.1× de open-prijs (295 min na creatie), eind 24 u op 256.56×
- Op +5 / +15 min: min. tot graduatie 2.07 / 2.07; unieke kopers curve 226 / 226; aandeel kopers ≥ 5 SOL 0.00 / 0.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.16 / 0.16; creator-verkopen 0 / 0; traders pool 2171 / 3141; organisch volume pool (SOL) 4530.71 / 7918.80; prijs t.o.v. open 9.73 / 5.70; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 373 SOL na 346 min, 272 SOL na 346 min, 270 SOL na 1406 min, 260 SOL na 1406 min, 255 SOL na 1406 min

**MINESTOCK** — MINESTOCK · [DexScreener](https://dexscreener.com/solana/9a8PquAAAoo51TABtasHSXMbm3CPSRnkxZfn6F8Dpump) · `9a8PquAAAoo51TABtasHSXMbm3CPSRnkxZfn6F8Dpump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 118.4× de open-prijs (1216 min na creatie), eind 24 u op 76.41× — graduatie in de create-slot
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 1 / 1; aandeel kopers ≥ 5 SOL 1.00 / 1.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.81 / 0.81; creator-verkopen 0 / 0; traders pool 59 / 136; organisch volume pool (SOL) 612.52 / 616.57; prijs t.o.v. open 68.65 / 69.65; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 1 SOL na 891 min, 0 SOL na 6 min, 0 SOL na 4 min, 0 SOL na 279 min, 0 SOL na 1059 min

**USDF** — United States Dividend Fund · [DexScreener](https://dexscreener.com/solana/Ufwjn7mtvgUfRG4nMSxRS84ewXHzdHLJRGzMDBMpump) · `Ufwjn7mtvgUfRG4nMSxRS84ewXHzdHLJRGzMDBMpump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 48271.8× de open-prijs (1216 min na creatie), eind 24 u op 0.05× — graduatie in de create-slot
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 1 / 1; aandeel kopers ≥ 5 SOL 1.00 / 1.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.79 / 0.79; creator-verkopen 0 / 0; traders pool 305 / 1950; organisch volume pool (SOL) 12025.88 / 12104.73; prijs t.o.v. open 19879.18 / 20028.75; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 13 SOL na 594 min, 11 SOL na 998 min, 10 SOL na 1190 min, 10 SOL na 1208 min, 9 SOL na 1155 min

**WSOS** — World Strategic Oil Supply · [DexScreener](https://dexscreener.com/solana/sEkKnqHR8KKrjMjFqurrzdyTn4hiivmM8JHKWmopump) · `sEkKnqHR8KKrjMjFqurrzdyTn4hiivmM8JHKWmopump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 296.6× de open-prijs (1215 min na creatie), eind 24 u op 296.47× — graduatie in de create-slot
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 1 / 1; aandeel kopers ≥ 5 SOL 1.00 / 1.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.80 / 0.80; creator-verkopen 0 / 0; traders pool 394 / 1352; organisch volume pool (SOL) 1155.44 / 1165.04; prijs t.o.v. open 212.65 / 214.16; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 1 SOL na 2 min, 0 SOL na 433 min, 0 SOL na 433 min, 0 SOL na 433 min, 0 SOL na 433 min

**WEPE** — WEPE · [DexScreener](https://dexscreener.com/solana/EL6Urf21PoPesMrbYSRc1yqqHjhk6DGUR63VZDmmpump) · `EL6Urf21PoPesMrbYSRc1yqqHjhk6DGUR63VZDmmpump`

- Tijdlijn: graduatie 0.0 min na creatie, piek in de eerste 24 u op 146.0× de open-prijs (681 min na creatie), eind 24 u op 139.98× — graduatie in de create-slot
- Op +5 / +15 min: min. tot graduatie 0.00 / 0.00; unieke kopers curve 1 / 1; aandeel kopers ≥ 5 SOL 1.00 / 1.00; netto instroom curve (SOL) 85.01 / 85.01; insider-aandeel 0.81 / 0.81; creator-verkopen 0 / 0; traders pool 106 / 239; organisch volume pool (SOL) 843.29 / 852.29; prijs t.o.v. open 120.40 / 122.50; split-buyer-events 0 / 0
- Grootste verkopers (eerste 24 u): 14 SOL na 242 min, 14 SOL na 882 min, 14 SOL na 242 min, 13 SOL na 683 min, 13 SOL na 242 min

