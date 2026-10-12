# Casussen uit eigen data: "trending", dumpers en blijvers (2026-10-12, volledige herrun met de scherpe dev-bundel-vlag)

Op verzoek van de eigenaar, via de coördinator. Alleen ontwikkeldata (11–30 september 2026 UTC, vóór de hold-out), alleen pump-tokens met SOL-quote in hun eigen migratiepool; reversed pools en andere pools doen niet mee (farming-plekken). Koers = reserve-prijs van de pool (e/b, incl. virtuele reserves bij boost-pools; dezelfde koers die een koersdienst toont). Lijsten eisen ≥ 100 handelaren, ≥ 300 SOL volume en echte liquiditeit op het meetmoment (≥ 5 SOL per dag, ≥ 10 SOL na 7 dagen). Code: `lab/wallets/cases.py`, `cases_report.py`; ruwe output met tijdlijnen per casus: `data-old-faithful-one/lab/wallets/cases-v1/` (NAS-run op commit 1b74b66; `report.md` daar bevat alle tijdlijnen; de LAB ui-pagina "Casussen" toont dezelfde lijsten). De DexScreener-links zijn om zelf te kijken; het is geen databron.

## Wat je ziet, in gewoon Nederlands

1. **"Trending 24 u" (top-5 per dag op 24-uurs-rendement × volume) is over 20 dagen voor 95% nep.** 95 van de 100 toppers zijn dev-bundels: de token gradueert in zijn eigen create-slot (de maker koopt de hele curve voor 85 SOL met 1–2 wallets, bij 10 van de 95 met 3–5 wallets), waarna 6.000–27.000 SOL in de pool wordt gestort en de koers ×10.000 tot ×100.000 boven de openingsprijs komt. Steeds dezelfde namen (AROS, VSOF, USDF, WOFI, WOTF, WSOS, ECTF, TDOF, NTDA) met nieuwe mints, mediaan 2.049 "handelaren", binnen een dag leeggehaald met één verkoop van ~10.500 SOL, vaak door de creator. De "mcap" van tientallen miljoenen SOL is een rekenartefact van een leeggekochte pool. De 5 echte: PAID (15 sept), TIGRINO (18 sept), CLIP en FAMILY (21 sept), manifest (26 sept).
2. **De vlag is twee keer aangescherpt en staat nu op "graduatie in de create-slot óf ≤ 5 curve-kopers".** "Eén partij > 50% van de pool-kopen" ving 46 van de 100; "create-slot én ≤ 2 curve-kopers" 85, maar dezelfde operators gebruiken ook 3–5 curve-wallets: alle 10 "organische" 7-daagse instorters van die ronde hadden er precies 4, en 35 van de 100 "organische" stijgers gradueerden alsnog in de create-slot. Van alle token-dagen met ≥ 100 handelaren en ≥ 300 SOL volume (26.274 in 20 dagen) is 37% zo'n create-slot-graduatie (7.470 met ≤ 2 kopers, 747 met 3–5, 1.469 met 6–50); hun mediane einde van de dag is 0–1% van de piek. De dag- en weekaggregaten zijn bewaard, dus een volgende regelwijziging kost geen store-scan (`cases.py --reselect`).
3. **Met de scherpe vlag blijven 100 organische stijgers over, en die zien er heel anders uit dan de bundels:** mediaan 8.486 handelaren, 31.700 SOL volume op de dag, piek 275× de dagopening (meestal vanaf de curve) en aan het eind van de dag nog 31% van de piek (kwartielen 18% en 57%; 32 van de 100 eindigen boven de helft, 12 boven 80%). Niemand van hen gradueerde in de create-slot. Voorbeelden: e/acc (35.777 handelaren, 456.000 SOL op 26 sept, einde 41% van de piek), PAID (23.944 handelaren, 15 sept, 52%), SI (24.758 handelaren, 30 sept, 50%), TIGRINO (72%), HUHCAT (62%), TIPPED, PARASITE, KARDASHEV, TWINE, FROINK, BANDIT, FLEX. Dit zijn de tokens die de eigenaar in Phantom ziet stijgen; 4 van de 100 staan een latere dag bij de dumpers.
4. **De dumpers (top-5 per dag op volume die ≤ 10% van hun piek eindigden) zijn voor 56% dev-bundels** (26 van de "één koper"-structuur, 30 create-slot-graduaties met 3–50 kopers die dezelfde dag worden gedumpt). De 44 organische dumpers: mediaan 8.336 handelaren, 38.500 SOL volume, piek 240× en einde op 3% van de piek, met nog 81 SOL in de pool (SOLFROG 160.000 SOL op 28 sept, einde 0%; JACK, CAKE, KEVIN, HYPED, Cream, FAMILY, GENO). Hype, piek, instorting — op dezelfde dag.
5. **Na 7 dagen blijven weinig tokens over.** Van de top-10 "blijvers" op mcap zijn 7 de bundelstructuur (WSOS, FAIR, fomo, MCAT, WEPE, MINESTOCK: 145–2.300 handelaren, koers nooit bewogen). Echt zijn PAID (88.126 handelaren, 1,28 mln SOL volume, ~116.000 SOL mcap na een week op een derde van de piek), JEANPHIL (52.568 handelaren, 60% van de piek), USDF-DRMn (3.204 handelaren) en daaronder SDOG, Tilcayo, FLEX, HUHCAT, DRMR, parafactual en UPTOBER (47.407 handelaren, 28% van de piek). De 10 "instorters" met een piek-mcap van 15–42 miljoen SOL zijn alle create-slot-structuren. De organische instorters over 7 dagen zijn de echte hype-tokens: NPC (27.295 handelaren, piek-mcap 52.000 SOL, na een week 2%), TIGRINO (24.246 handelaren, 216.000 SOL volume, 3%), fomopay, YAP, POT, familiars (17–21k handelaren, 1–4% over), plus vier kleine met 700–2.200 handelaren (TDOF-eS3i, DAYTRADER, LAUNCHPAD, $GULL) die net boven de vlaggrens van 5 curve-kopers zitten.
6. **In de eerste 15 minuten zijn organische stijgers en organische dumpers maar matig uit elkaar te houden** (alle 100 stijgers en 44 dumpers met tijdlijn, mediaan op +15 min na de pool-opening): de dumpers gradueerden sneller (2,6 tegen 7,5 min), met minder curve-kopers (267 tegen 456) en juist méér vroege pool-handel (2.356 tegen 1.688 handelaren, 5.700 tegen 2.600 SOL organisch volume, koers 4,4× tegen 3,3× de open); insider-aandeel 0,06 tegen 0,04, aandeel grote kopers 0,01 tegen 0,00, geen creator-verkopen bij beide. Kortom: snelle hype in het eerste kwartier hoort eerder bij de dumper dan bij de blijver. De grote verschillen uit de eerste versie (insider-aandeel 0,00 tegen 0,78) kwamen door de bundels in de dumperslijst. Dit sluit aan bij onderzoek 1b over 18 dagen: op +15 min geven koers en instroom hooguit een lift van ~2×, en wie na een dag nog 50% van de piek vasthoudt, is op +15 min niet te zien.
7. **Gevolg.** Een trending-lijst uit eigen data begint met de dev-bundel-vlag (create-slot-graduatie of ≤ 5 curve-kopers); daarna is de lijst bruikbaar als casusselectie. Voor handelen zegt dit niets: of een 2×-filter netto iets oplevert staat in de spec M1+ voor LAB backtest.

Beperkingen: 20 dagen (1 oktober is een halve dag en valt af); de 7-dagen-lijst loopt over de hele periode; labels van kopers (wallet-score) komen pas na de NAS-run van stap 1.

## Per dag: trending 24 u (met en zonder zelffinanciering) en pumpers/dumpers

Zelfgefinancierd (dev-bundel) = de token graduateerde in zijn create-slot, of hoogstens 5 wallets kochten de hele curve. `trending_24h_organic` is dezelfde lijst zonder die tokens; 'grootste koper' is het aandeel van één wallet in de pool-kopen van de dag (informatie, geen filter).

| dag | lijst | # | token | zelfgefin. | grootste koper | volume SOL | 24-u-rendement | piek × | eind t.o.v. piek | traders | SOL in pool (eind) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-09-11 | dumpers_24h | 1 | [WOFI](https://dexscreener.com/solana/FdCXKB3o2ThJ4go3qfDUaRL1HA814FxQbEzshnaUpump) | ja | 0.71 | 8,469 | -96% | 2183.1 | 0% | 858 | 0 |
| 2026-09-11 | dumpers_24h | 2 | [HOOD](https://dexscreener.com/solana/hDPhFfYTr2CHvZ8w3HuVG23fF1oVWWxNHoJUUtJpump) | ja | 0.64 | 5,904 | -96% | 1047.9 | 0% | 2637 | 0 |
| 2026-09-11 | dumpers_24h | 3 | [minecat](https://dexscreener.com/solana/E48e1En3hWSBE8LgNDkDaKFvxDgjHHk83AwSxqicpump) | ja | 0.04 | 4,593 | -30% | 62.9 | 1% | 8802 | 3 |
| 2026-09-11 | dumpers_24h | 4 | [BULLBRAIN](https://dexscreener.com/solana/Hy3q5X2iaL9idGZ9f89JJC1MbGXEnup1zhtWc12epump) | ja | 0.04 | 4,519 | -16% | 50.5 | 2% | 1161 | 4 |
| 2026-09-11 | dumpers_24h | 5 | [MrBeast](https://dexscreener.com/solana/RFGkQxMHbNw7nmG4x6euoCmkxQXh3TovGa32F7xpump) | ja | 0.70 | 4,431 | -96% | 609.0 | 0% | 3874 | 0 |
| 2026-09-11 | trending_24h | 1 | [$1](https://dexscreener.com/solana/wEcST4bnx6yxqn3Eb9Nmu7u8vrHYVaYY3GP31Tjpump) | ja | 0.52 | 3,648 | +79659% | 797.7 | 100% | 3564 | 2,388 |
| 2026-09-11 | trending_24h | 2 | [GTA 6](https://dexscreener.com/solana/hwvkLWd1xMP2jZiPMmV1uHKnQyDMkgQSWc95UJJpump) | ja | 0.84 | 2,301 | +68330% | 684.3 | 100% | 1532 | 2,208 |
| 2026-09-11 | trending_24h | 3 | [ClaudeAI](https://dexscreener.com/solana/Eq9qMbuZMCd8wkCJFcb6mETPtLZPYLteNZ7vLJ9vpump) | ja | 0.58 | 2,139 | +52263% | 523.6 | 100% | 2467 | 1,930 |
| 2026-09-11 | trending_24h | 4 | [BARRON](https://dexscreener.com/solana/ZyNRXJfwNWUJCTZU9c2XU6Zdrg2k96pWY8tv7gHpump) | ja | 0.50 | 1,846 | +36112% | 362.1 | 100% | 2418 | 1,603 |
| 2026-09-11 | trending_24h | 5 | [OpenClaw](https://dexscreener.com/solana/EsBo9HQ89QbHtuy5nYCU72TrhJ9uGmgpHTU9Pz7pump) | ja | 0.71 | 1,710 | +34831% | 349.3 | 100% | 1284 | 1,573 |
| 2026-09-11 | trending_24h_organic | 1 | [FLM](https://dexscreener.com/solana/4Vur6RbGdWjhFsfULJx8z8ACfpFC9d2wMYaY43Srpump) | nee | 0.03 | 5,660 | +384% | 41.3 | 12% | 2998 | 41 |
| 2026-09-11 | trending_24h_organic | 2 | [FLYWHEEL](https://dexscreener.com/solana/FDLUrwjczQDNyn8BnrYMBUqGC17UaEETKUuyYtTDf1yw) | nee | 0.03 | 1,345 | +1069% | 18.0 | 65% | 1196 | 73 |
| 2026-09-11 | trending_24h_organic | 3 | [ㅤㅤㅤ](https://dexscreener.com/solana/4LJemPwc55jzi6Zy2juu4ZzDRBGYdog4VKx3jwhSoNk8) | nee | 0.03 | 861 | +1174% | 14.4 | 89% | 826 | 67 |
| 2026-09-11 | trending_24h_organic | 4 | [FLYSEM](https://dexscreener.com/solana/CgZqKU8bJitpfVdeHPGsGKcUntxigorYuZtAN65fpump) | nee | 0.03 | 1,068 | +884% | 11.6 | 84% | 451 | 74 |
| 2026-09-11 | trending_24h_organic | 5 | [LAMA](https://dexscreener.com/solana/2fWzx35rQMAATQGhJzVTvzeXHcenCQLqCog9Jkg5pump) | nee | 0.04 | 902 | +425% | 7.7 | 68% | 644 | 39 |
| 2026-09-12 | dumpers_24h | 1 | [ALLDOG](https://dexscreener.com/solana/AZu5n7foTz4rRfii2DLCbDM76ZmCQeaeEC9icfPPpump) | ja | 0.01 | 27,610 | -22% | 398.2 | 0% | 10376 | 5 |
| 2026-09-12 | dumpers_24h | 2 | [WOFI](https://dexscreener.com/solana/DtkfCLLjatm2V72DHuUuBBHJcDnH4PBMs8SD9j6pump) | ja | 0.90 | 24,222 | -96% | 18580.1 | 0% | 1945 | 1 |
| 2026-09-12 | dumpers_24h | 3 | [WOFI](https://dexscreener.com/solana/sKGHsS36WfUJydnr7ksLFC6YPm6NBuznEV7ZzZopump) | ja | 0.93 | 23,553 | -96% | 18049.8 | 0% | 1940 | 0 |
| 2026-09-12 | dumpers_24h | 4 | [WOFI](https://dexscreener.com/solana/12bNr4f9DyHMC5drzXooJVqkRrskktjn1Bfxw8j1pump) | ja | 0.98 | 22,368 | -96% | 17042.5 | 0% | 934 | 0 |
| 2026-09-12 | dumpers_24h | 5 | [LAPTOP](https://dexscreener.com/solana/ujV4LLitcy7XeCL6JDKGJV1j5vvawKDa9b1EDT2pump) | ja | 0.11 | 22,275 | -34% | 3401.7 | 0% | 3448 | 1 |
| 2026-09-12 | trending_24h | 1 | [WOFI](https://dexscreener.com/solana/BygcY6mmCNMJRppvbvG6LPr9U6zY1PKtNixzAR5Vpump) | ja | 0.65 | 15,168 | +6056560% | 60566.6 | 100% | 1555 | 9,353 |
| 2026-09-12 | trending_24h | 2 | [WOFI](https://dexscreener.com/solana/VEY71Uj9G6oZiVHR3dDjhMVrVAXfaTNatTjt226pump) | ja | 0.55 | 23,716 | +3665321% | 36669.8 | 100% | 1988 | 16,287 |
| 2026-09-12 | trending_24h | 3 | [WOFI](https://dexscreener.com/solana/baeHvY89kRnrYoHreyeENbtREirurwMzanzih4wpump) | ja | 0.62 | 17,452 | +2185019% | 21851.2 | 100% | 1966 | 12,569 |
| 2026-09-12 | trending_24h | 4 | [WOFI](https://dexscreener.com/solana/sHYAHJRQj1gCyZDgJf6GdQRyBrcMaMb5W64Rrw2pump) | ja | 0.33 | 19,643 | +1924208% | 19243.1 | 100% | 1995 | 11,806 |
| 2026-09-12 | trending_24h | 5 | [WOTF](https://dexscreener.com/solana/LVC9prkf2S17isxQSorJSxAN4X89WrmkmKYD8nLpump) | ja | 0.43 | 14,518 | +1052711% | 10528.1 | 100% | 1982 | 8,725 |
| 2026-09-12 | trending_24h_organic | 1 | [TWINE](https://dexscreener.com/solana/CNWxmoBSQZo2Sgp5KSAK5m9FwSqDbXQRP4CNMuoe78Gm) | nee | 0.17 | 82,577 | +18149% | 630.3 | 29% | 8381 | 500 |
| 2026-09-12 | trending_24h_organic | 2 | [revolve ](https://dexscreener.com/solana/J8X5ygWHY5uHFch7m3MisSC7eDAWpAkgi1pyqfT5pump) | nee | 0.04 | 23,379 | +5732% | 227.5 | 26% | 6491 | 190 |
| 2026-09-12 | trending_24h_organic | 3 | [PAD](https://dexscreener.com/solana/9iKAPEqEDNqfZ2wD3g25RMRpYFW78AnXJgm6yNq5zpad) | nee | 0.05 | 19,154 | +801% | 165.2 | 5% | 5754 | 57 |
| 2026-09-12 | trending_24h_organic | 4 | [LAMA](https://dexscreener.com/solana/2fWzx35rQMAATQGhJzVTvzeXHcenCQLqCog9Jkg5pump) | nee | 0.08 | 18,402 | +644% | 32.5 | 23% | 4847 | 137 |
| 2026-09-12 | trending_24h_organic | 5 | [SOL4EX](https://dexscreener.com/solana/6v4XyHhHj6uNsach9W4u59DntpdB7Q216E9fx37upump) | nee | 0.04 | 5,804 | +788% | 48.4 | 18% | 2418 | 55 |
| 2026-09-13 | dumpers_24h | 1 | [Mizzy](https://dexscreener.com/solana/2HFakYaUp8au5oZThEvZ8wvSqoTreTQAvqtvoKarpump) | ja | 0.00 | 30,522 | -19% | 375.0 | 0% | 10187 | 5 |
| 2026-09-13 | dumpers_24h | 2 | [GROK](https://dexscreener.com/solana/rhobiM4oS4piCBNqAMH9BesnvQMKiRJ2xwjaNC5pump) | ja | 0.24 | 25,296 | -95% | 4522.0 | 0% | 3230 | 1 |
| 2026-09-13 | dumpers_24h | 3 | [WOFI](https://dexscreener.com/solana/VEY71Uj9G6oZiVHR3dDjhMVrVAXfaTNatTjt226pump) | ja | 0.48 | 24,782 | -100% | 1.3 | 0% | 1947 | 5 |
| 2026-09-13 | dumpers_24h | 4 | [WOFI](https://dexscreener.com/solana/4N7fcsLHFCdUuMHu9LcFfBBvEr5B8hp8dg17Wf26pump) | ja | 0.40 | 24,614 | -96% | 12505.7 | 0% | 792 | 0 |
| 2026-09-13 | dumpers_24h | 5 | [WOFI](https://dexscreener.com/solana/vbvtJZrWdNDdi9MmGAypVDP76HHxv6FUw6ZphuXpump) | ja | 0.74 | 24,116 | -96% | 15831.2 | 0% | 1963 | 1 |
| 2026-09-13 | trending_24h | 1 | [WOFI](https://dexscreener.com/solana/F2reRYPQUPkagQy7t5VaZgtiHqXNFQ91ej15GRypump) | ja | 0.54 | 23,205 | +4030184% | 40302.8 | 100% | 2015 | 17,079 |
| 2026-09-13 | trending_24h | 2 | [WOFI](https://dexscreener.com/solana/CHbodGeNZW5Fh12XLvobHAYzxwQjCWKk55kstzdYpump) | ja | 0.85 | 9,991 | +5304463% | 53051.4 | 100% | 1536 | 8,746 |
| 2026-09-13 | trending_24h | 3 | [WOFI](https://dexscreener.com/solana/izUd2DKhKefxT44NCCNpVvaRb963MYeWFsubGVSpump) | ja | 0.55 | 19,340 | +2414524% | 24146.2 | 100% | 1976 | 13,216 |
| 2026-09-13 | trending_24h | 4 | [WOTF](https://dexscreener.com/solana/fKMMqSi9hhBBvGKx2eHtSC9HH7YAektw8GxbxGFpump) | ja | 0.40 | 15,654 | +1157135% | 11572.4 | 100% | 1995 | 9,149 |
| 2026-09-13 | trending_24h | 5 | [NTDA](https://dexscreener.com/solana/6agyG6m21AwAabMdm8W5Y8htPCf45a8AhTxsKVTpump) | ja | 0.35 | 10,836 | +509941% | 5100.4 | 100% | 2011 | 6,069 |
| 2026-09-13 | trending_24h_organic | 1 | [NINA](https://dexscreener.com/solana/H22aquDouZNFyfykwnXmY5Y9z5sMQtLFy5tUKR99pump) | nee | 0.04 | 55,193 | +9679% | 349.2 | 28% | 14232 | 275 |
| 2026-09-13 | trending_24h_organic | 2 | [PVE](https://dexscreener.com/solana/6ssGtD5KUhzrENtn5BiEdTt4tcb6sfn7wPHRJyBYsp7N) | nee | 0.08 | 39,266 | +6830% | 202.0 | 34% | 8445 | 256 |
| 2026-09-13 | trending_24h_organic | 3 | [MIZO](https://dexscreener.com/solana/5Zspimi8VD6LctJLtaSjLqaSUvmh3Rs849iPtGocpump) | nee | 0.07 | 35,024 | +3926% | 271.5 | 15% | 10182 | 158 |
| 2026-09-13 | trending_24h_organic | 4 | [PVE](https://dexscreener.com/solana/55VWo7QQpqfYwratKUZHhSSnSghyTVCJx54yNoWVpump) | nee | 0.04 | 8,514 | +13451% | 269.4 | 50% | 1971 | 263 |
| 2026-09-13 | trending_24h_organic | 5 | [MPAIR](https://dexscreener.com/solana/EUTipzFepgT9614RaCQaKcqL2NnJFDmvpTcZTrXYpump) | nee | 0.07 | 23,029 | +2128% | 260.7 | 9% | 4805 | 104 |
| 2026-09-14 | dumpers_24h | 1 | [BRAIN](https://dexscreener.com/solana/9LuwgFQemAoV9rgVBBtwbRSxBmamcRRbysEK8yL2pump) | nee | 0.04 | 39,210 | +3254% | 396.0 | 8% | 9823 | 131 |
| 2026-09-14 | dumpers_24h | 2 | [WHEEL TYSON](https://dexscreener.com/solana/6XV1bfWhSZfmeZgfRzJHSZ51df31BNVzNE8Xxr2Kpump) | ja | 0.00 | 34,482 | -14% | 556.8 | 0% | 10947 | 7 |
| 2026-09-14 | dumpers_24h | 3 | [WOFI](https://dexscreener.com/solana/izUd2DKhKefxT44NCCNpVvaRb963MYeWFsubGVSpump) | ja | 0.50 | 33,714 | -100% | 2.1 | 0% | 1979 | 37 |
| 2026-09-14 | dumpers_24h | 4 | [MEMEBACK](https://dexscreener.com/solana/GogsSnkXwke5FEUxyV6FmR4eczFK1KJDVsaJNaNspump) | ja | 0.00 | 30,946 | -19% | 449.8 | 0% | 10822 | 6 |
| 2026-09-14 | dumpers_24h | 5 | [TWINE](https://dexscreener.com/solana/CNWxmoBSQZo2Sgp5KSAK5m9FwSqDbXQRP4CNMuoe78Gm) | nee | 0.08 | 29,367 | -98% | 1.2 | 1% | 5282 | 130 |
| 2026-09-14 | trending_24h | 1 | [WOFI](https://dexscreener.com/solana/RdSJAdbWBBgKejj8GDzmqcQPdpnhMPYBTNog77Qpump) | ja | 0.37 | 22,232 | +3365798% | 33659.0 | 100% | 1996 | 15,621 |
| 2026-09-14 | trending_24h | 2 | [WOFI](https://dexscreener.com/solana/CeUfw3D5TUvfGu5ohSryU6JjY3gzkRVE9MbZwGuhpump) | ja | 0.86 | 10,065 | +4986767% | 49868.7 | 100% | 1532 | 8,480 |
| 2026-09-14 | trending_24h | 3 | [World Wate](https://dexscreener.com/solana/otsy54Ji8vPqkBEQudeEeTApm69juBvgVctNjBXpump) | ja | 0.35 | 10,698 | +542950% | 5430.5 | 100% | 1988 | 6,263 |
| 2026-09-14 | trending_24h | 4 | [KIBA](https://dexscreener.com/solana/n5NhHM8QBhNBFGL9K751mTa9iCYTZnQjMuYeBxypump) | ja | 0.40 | 9,516 | +432498% | 4326.0 | 100% | 2091 | 5,587 |
| 2026-09-14 | trending_24h | 5 | [WOFI](https://dexscreener.com/solana/74zzesdirYG2DiA2a914c3xNjYSi5HdJyJwH9YLpump) | ja | 0.76 | 7,093 | +498899% | 4995.7 | 100% | 1972 | 5,995 |
| 2026-09-14 | trending_24h_organic | 1 | [BRAIN](https://dexscreener.com/solana/9LuwgFQemAoV9rgVBBtwbRSxBmamcRRbysEK8yL2pump) | nee | 0.04 | 39,210 | +3254% | 396.0 | 8% | 9823 | 131 |
| 2026-09-14 | trending_24h_organic | 2 | [AdamSandr](https://dexscreener.com/solana/E9nmbyzLqnBQbAJL7dMta9aR3HYy5W1ewPRf221zpump) | nee | 0.05 | 10,886 | +1709% | 69.1 | 26% | 3097 | 176 |
| 2026-09-14 | trending_24h_organic | 3 | [JohnWick](https://dexscreener.com/solana/2QmmEqXymWNZNBRwEaoD4dn7vvRRjo9LFPwjzKiQpump) | nee | 0.05 | 6,563 | +521% | 47.5 | 13% | 3427 | 42 |
| 2026-09-14 | trending_24h_organic | 4 | [clarity](https://dexscreener.com/solana/GPrTuSgAXVVJ67bAU4xe3UFD5aDDmVj7bZy73vTQpump) | nee | 0.08 | 1,100 | +1991% | 26.2 | 80% | 880 | 88 |
| 2026-09-14 | trending_24h_organic | 5 | [USDF](https://dexscreener.com/solana/DRMnFyekQiCTMrajtgsTycqp4ie6r1tZnh6qpAGjpump) | nee | 0.47 | 645 | +2936% | 30.8 | 98% | 164 | 347 |
| 2026-09-15 | dumpers_24h | 1 | [DOOM](https://dexscreener.com/solana/GE2j19bLPf4hKHWhFP4VXS5x86aHt5pnkdWmudrvdoom) | nee | 0.04 | 38,794 | +836% | 243.8 | 4% | 8476 | 77 |
| 2026-09-15 | dumpers_24h | 2 | [CAT](https://dexscreener.com/solana/AdgZULsSbgvopxu9Avv53F8dtrZasPpEdXXZUAdWTdyT) | nee | 0.03 | 36,778 | +1938% | 287.6 | 7% | 10584 | 111 |
| 2026-09-15 | dumpers_24h | 3 | [bruh](https://dexscreener.com/solana/6tRotGypA5QJKwmfgGFe4yp36eNQ4Vz7m8MNNpBnpYmq) | nee | 0.05 | 35,644 | +879% | 358.2 | 3% | 8195 | 62 |
| 2026-09-15 | dumpers_24h | 4 | [WOFI](https://dexscreener.com/solana/nnadFcf81yR2P3MAAXD9dPySRTVnBCHja3vPbJNpump) | ja | 0.52 | 34,900 | -95% | 27631.5 | 0% | 1991 | 2 |
| 2026-09-15 | dumpers_24h | 5 | [WOHUHCAT](https://dexscreener.com/solana/AQdPS6ByMt7aZhB49VT44C6vAqDFFQvvgqm5A2NKpump) | nee | 0.02 | 31,444 | +2% | 114.4 | 1% | 1947 | 10 |
| 2026-09-15 | trending_24h | 1 | [PAID](https://dexscreener.com/solana/98kfF7rmsg1QDUEoCqNE7g7M1FdrTt92TEp2CLzypump) | nee | 0.05 | 232,933 | +213436% | 4100.3 | 52% | 23944 | 1,375 |
| 2026-09-15 | trending_24h | 2 | [WOTF](https://dexscreener.com/solana/Zd8xgiFB1AGYTmZ78MS5APSXmDTNTLUJKuSGUD7pump) | ja | 0.76 | 15,542 | +2414936% | 24150.4 | 100% | 1852 | 13,210 |
| 2026-09-15 | trending_24h | 3 | [WOFI](https://dexscreener.com/solana/Z7zAsKeEahmBMd4VDQRQLZD77E3FVkrRpmQMB9upump) | ja | 0.34 | 20,167 | +1712318% | 17134.8 | 100% | 1990 | 11,138 |
| 2026-09-15 | trending_24h | 4 | [KIBA](https://dexscreener.com/solana/zp9PDZfkMidthG6gjeZ1NjtSALpd5wpQB6x5mRopump) | ja | 0.33 | 19,273 | +1570920% | 15710.2 | 100% | 2129 | 10,667 |
| 2026-09-15 | trending_24h | 5 | [ECTF](https://dexscreener.com/solana/Va1Jg8sD31wks9zXeESnnSxBAz2p2h6Rs5mSwNQpump) | ja | 0.40 | 15,389 | +1263592% | 12645.8 | 100% | 2002 | 9,561 |
| 2026-09-15 | trending_24h_organic | 1 | [PAID](https://dexscreener.com/solana/98kfF7rmsg1QDUEoCqNE7g7M1FdrTt92TEp2CLzypump) | nee | 0.05 | 232,933 | +213436% | 4100.3 | 52% | 23944 | 1,375 |
| 2026-09-15 | trending_24h_organic | 2 | [HUHCAT](https://dexscreener.com/solana/A9AHYeqb7nQk7LZUraw7rBCzYRjy2DRvE6NqWfFHKRdH) | nee | 0.05 | 86,467 | +46250% | 749.6 | 62% | 16330 | 695 |
| 2026-09-15 | trending_24h_organic | 3 | [CAT](https://dexscreener.com/solana/AdgZULsSbgvopxu9Avv53F8dtrZasPpEdXXZUAdWTdyT) | nee | 0.03 | 36,778 | +1938% | 287.6 | 7% | 10584 | 111 |
| 2026-09-15 | trending_24h_organic | 4 | [PAIDCAT](https://dexscreener.com/solana/7xwPE6M7h19FT5U7pd6HejjNmrh3rDSMFCoAwibVCRwT) | nee | 0.08 | 12,390 | +4345% | 132.4 | 34% | 4418 | 145 |
| 2026-09-15 | trending_24h_organic | 5 | [DOOM](https://dexscreener.com/solana/GE2j19bLPf4hKHWhFP4VXS5x86aHt5pnkdWmudrvdoom) | nee | 0.04 | 38,794 | +836% | 243.8 | 4% | 8476 | 77 |
| 2026-09-16 | dumpers_24h | 1 | [HYPED](https://dexscreener.com/solana/G2KDX81e31T6pZbkrJLPKAXqKcd8VoE3UEp83rRtpump) | nee | 0.04 | 71,518 | +452% | 637.7 | 1% | 12771 | 49 |
| 2026-09-16 | dumpers_24h | 2 | [TRUMP](https://dexscreener.com/solana/AL44bCBjmdXQzdzSacvsaMRNM8kkdA9hmymjb7eGSCeW) | nee | 0.09 | 41,299 | +1569% | 236.6 | 7% | 10933 | 123 |
| 2026-09-16 | dumpers_24h | 3 | [BOAR](https://dexscreener.com/solana/Hk8Uiq8CeNdmZzu1aPaT7MhvujtJifFS9kKhfpXmpaid) | nee | 0.04 | 36,316 | +193% | 34.1 | 9% | 5229 | 164 |
| 2026-09-16 | dumpers_24h | 4 | [WIFELON](https://dexscreener.com/solana/ChuxPtvuk8BGAtyBm6vd3ZNQaqWuTJXiNRguXvXqpump) | ja | 0.02 | 27,274 | +0% | 90.1 | 1% | 3487 | 9 |
| 2026-09-16 | dumpers_24h | 5 | [WOTF](https://dexscreener.com/solana/NnLz6wXfB3c41KwPWPvk9Rz75zR5enTGtYmsELrpump) | ja | 0.50 | 25,949 | -100% | 3.6 | 0% | 1964 | 1 |
| 2026-09-16 | trending_24h | 1 | [ELON](https://dexscreener.com/solana/eqUvaqyXr3a2Rcmh2pxp2tJt3cpKrEhft1w7gJSpump) | ja | 0.33 | 19,242 | +1544113% | 15442.1 | 100% | 1999 | 10,576 |
| 2026-09-16 | trending_24h | 2 | [WOFI](https://dexscreener.com/solana/YuEFCTBvg8WgiZrgTS59fZMSKmXtSeVGS2qV6m4pump) | ja | 0.83 | 13,697 | +1939762% | 19408.9 | 100% | 1970 | 11,836 |
| 2026-09-16 | trending_24h | 3 | [ECTF](https://dexscreener.com/solana/JGLw4v2fNcXF4ybtsvx5WWNce4qaryR36sQoZJLpump) | ja | 0.36 | 17,259 | +1467856% | 14689.2 | 100% | 1995 | 10,308 |
| 2026-09-16 | trending_24h | 4 | [WOTF](https://dexscreener.com/solana/CjeHX5YjMAzYTvdk544UpaM4gSo1ZrqGcHudZdXmpump) | ja | 0.67 | 6,023 | +2450757% | 24508.6 | 100% | 1564 | 5,940 |
| 2026-09-16 | trending_24h | 5 | [WOFI](https://dexscreener.com/solana/g48ZQAQAy4wKEKyiDZn9y6qbgQXHTDfwPpkMmRBpump) | ja | 0.49 | 11,773 | +1026501% | 10266.0 | 100% | 1999 | 8,612 |
| 2026-09-16 | trending_24h_organic | 1 | [POT](https://dexscreener.com/solana/4R2vepDVY7Gdcm2VKuAy6EbBfWsYSEsDJou1QGKuMfnf) | nee | 0.06 | 71,971 | +9553% | 545.8 | 18% | 12595 | 261 |
| 2026-09-16 | trending_24h_organic | 2 | [lockinu](https://dexscreener.com/solana/7GPGqsfVK1gG88GuVEetrsVyDiikABTsj9B9aHEHpump) | nee | 0.05 | 47,885 | +11832% | 437.1 | 27% | 12442 | 270 |
| 2026-09-16 | trending_24h_organic | 3 | [DONO](https://dexscreener.com/solana/9HB7uiNQeWTGG1tkuGaMQLhdt9Lg6vmJ4vtLJc1Wpump) | nee | 0.05 | 48,210 | +11686% | 434.3 | 27% | 10865 | 270 |
| 2026-09-16 | trending_24h_organic | 4 | [ZEREBLAST](https://dexscreener.com/solana/4hMvdY7KbsCzHnRE7Js6kNFyTdkqKMeTv9GPYD5ppump) | nee | 0.03 | 15,666 | +17897% | 330.1 | 55% | 4653 | 312 |
| 2026-09-16 | trending_24h_organic | 5 | [TRUMP](https://dexscreener.com/solana/AL44bCBjmdXQzdzSacvsaMRNM8kkdA9hmymjb7eGSCeW) | nee | 0.09 | 41,299 | +1569% | 236.6 | 7% | 10933 | 123 |
| 2026-09-17 | dumpers_24h | 1 | [KEVIN](https://dexscreener.com/solana/GZik7vJSnDAao9tbbcLrMGKfxLu7VCjoAtkDhsv8A3iE) | nee | 0.05 | 72,483 | +3760% | 411.8 | 9% | 13827 | 181 |
| 2026-09-17 | dumpers_24h | 2 | [SUUB](https://dexscreener.com/solana/CrrcQsPD5zNW2hEKhBy5ARP1haGBKAmoqsgD8dqPpump) | nee | 0.04 | 49,189 | +2571% | 327.3 | 8% | 12017 | 123 |
| 2026-09-17 | dumpers_24h | 3 | [SEND](https://dexscreener.com/solana/2XuQnYQ1q3oTjPL9j4PfhV1AEqcZ6Hq2PzPzuxaASEND) | nee | 0.06 | 35,751 | +2119% | 384.2 | 6% | 10985 | 102 |
| 2026-09-17 | dumpers_24h | 4 | [WANG](https://dexscreener.com/solana/JoBxYVvPhPSkiZNiUjZdty1uR6TaNneuQvE3zZ2pump) | nee | 0.07 | 35,671 | +2398% | 287.7 | 9% | 10244 | 110 |
| 2026-09-17 | dumpers_24h | 5 | [ECTF](https://dexscreener.com/solana/fudW5f1NCktzbjASfP2q12YQ39p5Hy1RVyTgjLWpump) | ja | 0.34 | 33,504 | -95% | 21279.2 | 0% | 2019 | 1 |
| 2026-09-17 | trending_24h | 1 | [WOTF](https://dexscreener.com/solana/DRWUnUkFbvA5T6DchUmdtZTLxpj6sdFFUHJxHucxpump) | ja | 0.51 | 7,932 | +4280623% | 42807.2 | 100% | 1556 | 7,859 |
| 2026-09-17 | trending_24h | 2 | [WOTF](https://dexscreener.com/solana/Yrp7FkbrXijJX1NC7ZpEAGvcpEUi46YH1XSzgprpump) | ja | 0.40 | 15,803 | +1183762% | 11838.6 | 100% | 1996 | 9,254 |
| 2026-09-17 | trending_24h | 3 | [ECTF](https://dexscreener.com/solana/AV9ZWwVsBRuyQXrd7n6k764rQck3K85PeEPLGh1pump) | ja | 0.56 | 10,535 | +711804% | 7125.8 | 100% | 1729 | 7,168 |
| 2026-09-17 | trending_24h | 4 | [WOFI](https://dexscreener.com/solana/be56wxStyKGxEtRoveKAd4FPcAQG6NG1Z4apfygpump) | ja | 0.60 | 9,729 | +654197% | 6543.0 | 100% | 1675 | 6,870 |
| 2026-09-17 | trending_24h | 5 | [NTDA](https://dexscreener.com/solana/99UZLCcstv32wmWr1D4BHbYxvTc1LYcFYmGF9UR9pump) | ja | 0.71 | 7,643 | +572301% | 5724.0 | 100% | 2711 | 6,423 |
| 2026-09-17 | trending_24h_organic | 1 | [Tilcayo](https://dexscreener.com/solana/AyYNfPtftg2zDP4ZbgcoQMggQtwLh4zpfVVmUJs2thto) | nee | 0.04 | 66,101 | +54498% | 674.2 | 81% | 11695 | 708 |
| 2026-09-17 | trending_24h_organic | 2 | [SOLCAT](https://dexscreener.com/solana/9U1f18idDeySFnYrurxqT1f5n5nE4g4Uk5LLzP69bh1) | nee | 0.06 | 72,550 | +30234% | 454.8 | 67% | 15022 | 558 |
| 2026-09-17 | trending_24h_organic | 3 | [KEVIN](https://dexscreener.com/solana/GZik7vJSnDAao9tbbcLrMGKfxLu7VCjoAtkDhsv8A3iE) | nee | 0.05 | 72,483 | +3760% | 411.8 | 9% | 13827 | 181 |
| 2026-09-17 | trending_24h_organic | 4 | [WISH](https://dexscreener.com/solana/4qpraJNwVi8yXn2hx6xxVnsifin1PEhJ8Nv61sab5a3U) | nee | 0.09 | 37,856 | +6560% | 488.9 | 14% | 9565 | 187 |
| 2026-09-17 | trending_24h_organic | 5 | [MINIONS](https://dexscreener.com/solana/4zv9pgsqM3Ey1pputaKj8mXw1kgzR2QJkZRhgmmspump) | nee | 0.06 | 12,519 | +16570% | 184.4 | 90% | 4470 | 355 |
| 2026-09-18 | dumpers_24h | 1 | [POT](https://dexscreener.com/solana/4R2vepDVY7Gdcm2VKuAy6EbBfWsYSEsDJou1QGKuMfnf) | nee | 0.07 | 45,750 | +62% | 34.0 | 5% | 6402 | 221 |
| 2026-09-18 | dumpers_24h | 2 | [PERK](https://dexscreener.com/solana/7qVyANei9jYf173z4FBKcmKp3nXDDBKiXAWh7aT2perk) | ja | 0.11 | 29,143 | -97% | 1.5 | 2% | 6408 | 81 |
| 2026-09-18 | dumpers_24h | 3 | [ECTF](https://dexscreener.com/solana/pRq66LWX2A7saEhFpHn65TAMC89rXcHE1EzXRwwpump) | ja | 0.36 | 27,212 | -90% | 13297.4 | 0% | 2006 | 9 |
| 2026-09-18 | dumpers_24h | 4 | [Norman](https://dexscreener.com/solana/4PYVWqC358kpvKYDrwbniVAeUXUq6dULa27Z2TCVpump) | ja | 0.01 | 25,650 | -22% | 421.1 | 0% | 9629 | 5 |
| 2026-09-18 | dumpers_24h | 5 | [WOTF](https://dexscreener.com/solana/VT7qCKfuAwxKWBibCxRWwaawEL4cQ3tevv276Wbpump) | ja | 0.40 | 24,139 | -92% | 11063.5 | 0% | 2017 | 7 |
| 2026-09-18 | trending_24h | 1 | [USDF](https://dexscreener.com/solana/stncqgk9GpXpcRUfaAYu3LPJMMAfsiZ5PrEYh8Xpump) | ja | 0.36 | 17,660 | +1375161% | 13752.6 | 100% | 2014 | 9,978 |
| 2026-09-18 | trending_24h | 2 | [TDOF](https://dexscreener.com/solana/7U5NzDtMd6BYEtXZi1y4LR625zkpgHtpXK37s3mpump) | ja | 0.36 | 17,497 | +1361586% | 13625.4 | 100% | 2012 | 9,928 |
| 2026-09-18 | trending_24h | 3 | [TIGRINO](https://dexscreener.com/solana/91ryaCo5yGpYZM3bs6GUPs97VWJQj7RozBmqPULgpump) | nee | 0.06 | 114,199 | +109550% | 1514.2 | 72% | 15964 | 884 |
| 2026-09-18 | trending_24h | 4 | [WOFI](https://dexscreener.com/solana/V8A3PSmDPE2n1F2vdfmqboGr5zfn8rw27an6dWnpump) | ja | 0.35 | 13,352 | +698270% | 6983.7 | 100% | 2010 | 7,108 |
| 2026-09-18 | trending_24h | 5 | [NTDA](https://dexscreener.com/solana/xohnfeLoVuDkd1Qr2oA1UPNrLgyJcvGokJDps9kpump) | ja | 0.53 | 11,391 | +777518% | 7780.2 | 100% | 2033 | 7,493 |
| 2026-09-18 | trending_24h_organic | 1 | [TIGRINO](https://dexscreener.com/solana/91ryaCo5yGpYZM3bs6GUPs97VWJQj7RozBmqPULgpump) | nee | 0.06 | 114,199 | +109550% | 1514.2 | 72% | 15964 | 884 |
| 2026-09-18 | trending_24h_organic | 2 | [CROSSR](https://dexscreener.com/solana/Ea9TZ2XC7AkgfKrFV5MnTKig5YWJoZH7xt4d4BnYpump) | nee | 0.06 | 31,263 | +9242% | 435.1 | 21% | 7486 | 228 |
| 2026-09-18 | trending_24h_organic | 3 | [KMS](https://dexscreener.com/solana/D1sm1V3pHGTv32ugeXoer4a91xqfuh4jJBYQwePSKVez) | nee | 0.05 | 38,324 | +6927% | 254.4 | 28% | 7590 | 225 |
| 2026-09-18 | trending_24h_organic | 4 | [Prism](https://dexscreener.com/solana/5zNmPYCm724a7HLE8FXB2d6R3BbsuPSjzNAxBmkHpump) | nee | 0.05 | 9,953 | +17616% | 198.8 | 89% | 3436 | 297 |
| 2026-09-18 | trending_24h_organic | 5 | [Neko](https://dexscreener.com/solana/2669rK4Wj3REXqeFuvGsLAV1ZseN9CoSvG1kSPGhpump) | nee | 0.06 | 13,940 | +4363% | 110.4 | 40% | 4173 | 153 |
| 2026-09-19 | dumpers_24h | 1 | [BIDDY](https://dexscreener.com/solana/biddm3N7RjyjfcnzmjgrQJ24QKooBGUy1RYP1aEgtzW) | nee | 0.07 | 44,275 | +477% | 101.9 | 6% | 10728 | 115 |
| 2026-09-19 | dumpers_24h | 2 | [NIUMAJAK](https://dexscreener.com/solana/GmLqBMbe4ffg7BZjdqRh1zE5d8EzZjS7p72QjmXKpump) | ja | 0.01 | 35,012 | +18% | 108.7 | 1% | 2666 | 12 |
| 2026-09-19 | dumpers_24h | 3 | [Pigeon](https://dexscreener.com/solana/53rZTtfGghxqdd6qTeDdfS8MBqg2CTyQeLW1LHCRpump) | nee | 0.08 | 34,101 | +1167% | 187.9 | 7% | 9359 | 92 |
| 2026-09-19 | dumpers_24h | 4 | [NTDA](https://dexscreener.com/solana/9pKW3aw8MpekCbUb2txMsB6xDVdAkmHb7aq3rJyJpump) | ja | 0.74 | 26,840 | -96% | 24487.6 | 0% | 1183 | 0 |
| 2026-09-19 | dumpers_24h | 5 | [WORMSOL](https://dexscreener.com/solana/3mCs98j1Hjr6KoNMQTmBf6RoQcUVBCnARvHyrk2Lpump) | ja | 0.02 | 20,988 | +12% | 76.5 | 1% | 1867 | 10 |
| 2026-09-19 | trending_24h | 1 | [NTDA](https://dexscreener.com/solana/FfK9fj41K98EuNZK2oQDRWuoyT9vb4d4jR412HcMpump) | ja | 0.75 | 20,031 | +3395836% | 33959.4 | 100% | 2761 | 15,669 |
| 2026-09-19 | trending_24h | 2 | [TDOF](https://dexscreener.com/solana/14GhrktpeZFPyNUEqyas68MKo9kTXEikeorpszv2pump) | ja | 0.37 | 17,231 | +1323498% | 13236.0 | 100% | 2014 | 9,788 |
| 2026-09-19 | trending_24h | 3 | [WOTF](https://dexscreener.com/solana/13JxdD21ufLZEuamFDRppFG1nr1irYGRugg4jBKApump) | ja | 0.38 | 16,375 | +1330949% | 13310.5 | 100% | 2013 | 9,814 |
| 2026-09-19 | trending_24h | 4 | [USDF](https://dexscreener.com/solana/bAxbrMB1UqRNneWQ5yUbBVuJLoMcYRf1GbaCWyupump) | ja | 0.41 | 15,404 | +1174068% | 11741.7 | 100% | 2161 | 9,216 |
| 2026-09-19 | trending_24h | 5 | [NTDA](https://dexscreener.com/solana/13MxrpxYECHvsxUXw3c5v5VDk7L4ctdaXEMB9mpbpump) | ja | 0.52 | 11,520 | +784923% | 7850.2 | 100% | 2056 | 7,529 |
| 2026-09-19 | trending_24h_organic | 1 | [JEANPHIL](https://dexscreener.com/solana/GTBxUiw6wJdmmkCGZgRHLyYxqu1vG4KtRpeox6yDpump) | nee | 0.08 | 76,190 | +105842% | 1678.3 | 63% | 12149 | 819 |
| 2026-09-19 | trending_24h_organic | 2 | [FLEX](https://dexscreener.com/solana/fvHLJUwsynVHJrssbZ8MLNyku9jt2izUspbBD4Spump) | nee | 0.08 | 77,253 | +64569% | 2123.8 | 30% | 8495 | 633 |
| 2026-09-19 | trending_24h_organic | 3 | [TIPPED](https://dexscreener.com/solana/tipp4C4Jnpft26HC9VXNjUPidojZqxXf8nzKvrKf5BS) | nee | 0.09 | 110,271 | +9061% | 253.1 | 36% | 18471 | 503 |
| 2026-09-19 | trending_24h_organic | 4 | [fomopay](https://dexscreener.com/solana/BP4Wic5LNKsqpmiREW6uNVEC16juvFCSd4WzVBatpump) | nee | 0.08 | 38,429 | +16943% | 383.5 | 44% | 9904 | 330 |
| 2026-09-19 | trending_24h_organic | 5 | [Claimr](https://dexscreener.com/solana/4htjusK8sBXXpGmcnbTmQHCRTFoh4fSwQryWEnTmpump) | nee | 0.05 | 9,716 | +14846% | 160.7 | 93% | 3413 | 277 |
| 2026-09-20 | dumpers_24h | 1 | [DUMBCOIN](https://dexscreener.com/solana/GgT4FpomaanrjKCvSeQzix8TiPXNQKWwfAANFm8282Vf) | nee | 0.04 | 32,766 | +1062% | 603.3 | 2% | 5386 | 70 |
| 2026-09-20 | dumpers_24h | 2 | [TYLER](https://dexscreener.com/solana/95pCcNPexz8rg1Sazi2GscfAGSFy3NXSPDL9TdEMZE2V) | nee | 0.09 | 31,189 | +2509% | 319.8 | 8% | 9881 | 122 |
| 2026-09-20 | dumpers_24h | 3 | [INU](https://dexscreener.com/solana/EfzQM5Lmh67umxFJqS67VQWybVhajo8m259NqNqppump) | ja | 0.02 | 23,210 | -57% | 37.9 | 1% | 2464 | 11 |
| 2026-09-20 | dumpers_24h | 4 | [Stryker](https://dexscreener.com/solana/G9QZfEPHtZrUF1arcUFvZgYn6zSQLZdvcjCuyhKrpump) | ja | 0.01 | 22,882 | -99% | 2.9 | 0% | 10129 | 18 |
| 2026-09-20 | dumpers_24h | 5 | [FOMONKEY](https://dexscreener.com/solana/5cfpcH6HQyHwqwB1oax4aQg17y3wKZyCuQ4UBsuJpump) | nee | 0.02 | 21,841 | -54% | 43.7 | 1% | 3080 | 12 |
| 2026-09-20 | trending_24h | 1 | [USDF](https://dexscreener.com/solana/T8sjUditwkQD2BtmkcRN3MdxegHh494LcJaV7v2pump) | ja | 0.34 | 18,214 | +1641610% | 16428.1 | 100% | 2068 | 10,902 |
| 2026-09-20 | trending_24h | 2 | [ECTF](https://dexscreener.com/solana/kPbqndJpHgEMjrrmp3aXuVxTqqqE2G145G7TrMopump) | ja | 0.35 | 18,106 | +1404229% | 14043.3 | 100% | 2033 | 10,083 |
| 2026-09-20 | trending_24h | 3 | [WOTF](https://dexscreener.com/solana/2EmpacsA5czi6MKS9QKkbkoAAZdbyN5KQktcqzpEpump) | ja | 0.59 | 6,768 | +3129411% | 31295.1 | 100% | 1557 | 6,716 |
| 2026-09-20 | trending_24h | 4 | [USDF](https://dexscreener.com/solana/CzkoFiK1LW2jFEaZNK7x8i9asSebHT9CWyPj7jKapump) | ja | 0.44 | 12,588 | +1340309% | 13404.1 | 100% | 2782 | 9,843 |
| 2026-09-20 | trending_24h | 5 | [TDOF](https://dexscreener.com/solana/usS7cmTLPgmfvH9E11y2RNzAspaARXENYjPVRMbpump) | ja | 0.51 | 11,794 | +812282% | 8123.8 | 100% | 2023 | 7,659 |
| 2026-09-20 | trending_24h_organic | 1 | [ZEBRA](https://dexscreener.com/solana/EqFG72Z35r8cAV8mKu3UK2L8NUTR1ZNyMr88E4BnwN8Q) | nee | 0.06 | 38,861 | +48607% | 615.2 | 79% | 8724 | 563 |
| 2026-09-20 | trending_24h_organic | 2 | [INU](https://dexscreener.com/solana/Wb33GwzjQLvvVhV2XB3YonQZQFM9zhXiU7AZB931Uts) | nee | 0.09 | 13,167 | +44224% | 510.6 | 87% | 3187 | 487 |
| 2026-09-20 | trending_24h_organic | 3 | [TYLER](https://dexscreener.com/solana/95pCcNPexz8rg1Sazi2GscfAGSFy3NXSPDL9TdEMZE2V) | nee | 0.09 | 31,189 | +2509% | 319.8 | 8% | 9881 | 122 |
| 2026-09-20 | trending_24h_organic | 4 | [ZECAT](https://dexscreener.com/solana/FHbotUdmriumk8q2PbYJE51844baLjUjyzoNVAGFSTeS) | nee | 0.07 | 7,356 | +6280% | 94.1 | 68% | 2807 | 173 |
| 2026-09-20 | trending_24h_organic | 5 | [HOLT ](https://dexscreener.com/solana/Hqa3scJGHpXQ8wgcEsugqqzrvwTcugRptHL1LgBCpump) | nee | 0.05 | 9,583 | +4812% | 164.5 | 30% | 3301 | 147 |
| 2026-09-21 | dumpers_24h | 1 | [FAMILY](https://dexscreener.com/solana/FvMLDEUDKUr9C34e8Nynz5Aqfdk34a2RBKQxygLpfomo) | nee | 0.07 | 64,314 | +6232% | 1096.3 | 6% | 13769 | 195 |
| 2026-09-21 | dumpers_24h | 2 | [MCAT](https://dexscreener.com/solana/4FtEnXqwFUc3negW4HhjLVN92p6W1ejE97jNNiNQpump) | nee | 0.02 | 29,855 | +78% | 249.2 | 1% | 6565 | 19 |
| 2026-09-21 | dumpers_24h | 3 | [ZEBRA](https://dexscreener.com/solana/EqFG72Z35r8cAV8mKu3UK2L8NUTR1ZNyMr88E4BnwN8Q) | nee | 0.10 | 27,021 | -96% | 1.2 | 3% | 7177 | 101 |
| 2026-09-21 | dumpers_24h | 4 | [INU](https://dexscreener.com/solana/Wb33GwzjQLvvVhV2XB3YonQZQFM9zhXiU7AZB931Uts) | nee | 0.10 | 24,943 | -99% | 1.1 | 1% | 6092 | 40 |
| 2026-09-21 | dumpers_24h | 5 | [AWARDED](https://dexscreener.com/solana/5wZDE8f5uKHbVaHAyz84Lgz7xefAkCpvcwoWYaRCrfn) | nee | 0.08 | 24,378 | -37% | 70.9 | 1% | 7612 | 18 |
| 2026-09-21 | trending_24h | 1 | [WSOS](https://dexscreener.com/solana/6ZW53aH72GH6m26YJSmSbz49yrvsGzYonB6DBvc8pump) | ja | 0.03 | 5,827 | +400674% | 4008.2 | 100% | 3513 | 5,386 |
| 2026-09-21 | trending_24h | 2 | [CLIP](https://dexscreener.com/solana/9DdHxVe1BSPaTy3iGEwvWsooRchNLK61XFAvzot59FwD) | nee | 0.07 | 69,909 | +20212% | 469.0 | 43% | 15282 | 392 |
| 2026-09-21 | trending_24h | 3 | [WOTF](https://dexscreener.com/solana/CsB8XjUPBfPX7h5qLDne7agm2gAE8ss81eC8XKAzpump) | ja | 0.62 | 5,230 | +266967% | 2670.7 | 100% | 2674 | 4,386 |
| 2026-09-21 | trending_24h | 4 | [LOOONGCAT](https://dexscreener.com/solana/5tYcEEKvN63RrzpomgF2jWMxJMHmr4Zo19RX1mXipump) | ja | 0.01 | 17,302 | +27373% | 321.1 | 86% | 10254 | 400 |
| 2026-09-21 | trending_24h | 5 | [FAMILY](https://dexscreener.com/solana/FvMLDEUDKUr9C34e8Nynz5Aqfdk34a2RBKQxygLpfomo) | nee | 0.07 | 64,314 | +6232% | 1096.3 | 6% | 13769 | 195 |
| 2026-09-21 | trending_24h_organic | 1 | [CLIP](https://dexscreener.com/solana/9DdHxVe1BSPaTy3iGEwvWsooRchNLK61XFAvzot59FwD) | nee | 0.07 | 69,909 | +20212% | 469.0 | 43% | 15282 | 392 |
| 2026-09-21 | trending_24h_organic | 2 | [FAMILY](https://dexscreener.com/solana/FvMLDEUDKUr9C34e8Nynz5Aqfdk34a2RBKQxygLpfomo) | nee | 0.07 | 64,314 | +6232% | 1096.3 | 6% | 13769 | 195 |
| 2026-09-21 | trending_24h_organic | 3 | [HUSKY](https://dexscreener.com/solana/8TSUhXRhpEFtUVfDFAQdYCwgp2mEbs2jQk7tAYc1pump) | nee | 0.06 | 9,763 | +4279% | 71.4 | 61% | 4075 | 159 |
| 2026-09-21 | trending_24h_organic | 4 | [ORACLE](https://dexscreener.com/solana/HUcEu35oz1yku1yLBSYRL1UyAnAh59ZCkAYPRe2xpump) | nee | 0.04 | 6,112 | +3862% | 46.6 | 85% | 1450 | 132 |
| 2026-09-21 | trending_24h_organic | 5 | [PSTOCK](https://dexscreener.com/solana/stckszzSEhZgCsLmmUpas1hmkggVMRtLGucDTTXLfHs) | nee | 0.04 | 13,246 | +1338% | 117.3 | 12% | 5077 | 74 |
| 2026-09-22 | dumpers_24h | 1 | [CATEWALK](https://dexscreener.com/solana/BLSuVTxKYDL4vm4XmG3oEJfsSGfJZF3cgy98ri68pump) | ja | 0.00 | 42,775 | +3750% | 456.3 | 8% | 20321 | 140 |
| 2026-09-22 | dumpers_24h | 2 | [LOOONGCAT](https://dexscreener.com/solana/5tYcEEKvN63RrzpomgF2jWMxJMHmr4Zo19RX1mXipump) | ja | 0.00 | 34,502 | -100% | 1.4 | 0% | 16510 | 10 |
| 2026-09-22 | dumpers_24h | 3 | [AROS](https://dexscreener.com/solana/AyijV7rdzfHi3PkzX2V1NjnuqaU3PqQT3Bc63tMpump) | ja | 0.40 | 28,692 | -92% | 22501.3 | 0% | 2012 | 6 |
| 2026-09-22 | dumpers_24h | 4 | [twh](https://dexscreener.com/solana/jbS3hKYmEn4Rh2Zrvyv9xExC6UvdcUKYxRZrQ8Ypump) | ja | 0.01 | 27,989 | -10% | 271.8 | 0% | 2794 | 6 |
| 2026-09-22 | dumpers_24h | 5 | [JEANBEAN](https://dexscreener.com/solana/EV9qfjb2bGMXByQiS6vZ4n53HHCAcWdGzrbGXE8Wpump) | ja | 0.02 | 25,963 | -13% | 249.8 | 0% | 2829 | 5 |
| 2026-09-22 | trending_24h | 1 | [AROS](https://dexscreener.com/solana/uNXUUn5u2WbbR2ZnJyXdkRCpa2dMyWgHWx9P2Uqpump) | ja | 0.62 | 16,549 | +3363973% | 33647.0 | 100% | 1949 | 15,596 |
| 2026-09-22 | trending_24h | 2 | [AROS](https://dexscreener.com/solana/33wRsdRCcJkYBdXvGisAdgD7ef8koLP5sTqykzejpump) | ja | 0.44 | 15,760 | +3188128% | 31883.7 | 100% | 2039 | 15,196 |
| 2026-09-22 | trending_24h | 3 | [WSOS](https://dexscreener.com/solana/2Xy8hfogaKmCJdyuciMS9kjPwkDkigWCd5J1ZKuypump) | ja | 0.64 | 6,245 | +2665633% | 26657.3 | 100% | 1556 | 6,196 |
| 2026-09-22 | trending_24h | 4 | [AROS](https://dexscreener.com/solana/4M3gYZ2dQ39KuBuJYVmQi5qrtFfMpMWHpJhhx7Wapump) | ja | 0.28 | 12,874 | +753764% | 7538.6 | 100% | 113 | 7,383 |
| 2026-09-22 | trending_24h | 5 | [VSOF](https://dexscreener.com/solana/HwVyzxBVE68x7aZub6UG78i6RQLTEoy9cQuxMq4ppump) | ja | 0.36 | 9,162 | +771949% | 7720.9 | 100% | 2778 | 7,467 |
| 2026-09-22 | trending_24h_organic | 1 | [inu](https://dexscreener.com/solana/E28eQFoCPndfLUPnxYgEWHhbnEVqSpyNPgAacoAEpump) | nee | 0.04 | 20,848 | +10017% | 209.4 | 48% | 5678 | 272 |
| 2026-09-22 | trending_24h_organic | 2 | [SWEEP](https://dexscreener.com/solana/FDaJxBqw1fDhwPQTn8XU3bKH8sXXRe8o5kzC3CdUAv7X) | nee | 0.05 | 28,220 | +2290% | 86.2 | 28% | 9202 | 187 |
| 2026-09-22 | trending_24h_organic | 3 | [BAKARI](https://dexscreener.com/solana/3y4KW6Yere8NAjVSPVVWEXkUoDfXV8b6t3EDGS2W5wP2) | nee | 0.04 | 13,601 | +4102% | 116.5 | 36% | 5248 | 157 |
| 2026-09-22 | trending_24h_organic | 4 | [KCAT](https://dexscreener.com/solana/MboMMGXjGDi45hfjCVzYru32kEM5nw7VzCqCofZpump) | nee | 0.05 | 21,960 | +1943% | 139.1 | 15% | 7431 | 124 |
| 2026-09-22 | trending_24h_organic | 5 | [BFC](https://dexscreener.com/solana/3B72iCu6FXMqN1fk5EKrkDxT4Er6cDomoo6Ym23Ypump) | nee | 0.03 | 3,625 | +7467% | 146.7 | 52% | 961 | 199 |
| 2026-09-23 | dumpers_24h | 1 | [EPEP](https://dexscreener.com/solana/2ekqDh2VNP5mfMUh7fngof1APUBmkAsJJATmDZBapump) | ja | 0.01 | 40,823 | +11% | 276.9 | 0% | 2692 | 10 |
| 2026-09-23 | dumpers_24h | 2 | [based](https://dexscreener.com/solana/4wYd4vPhzaUQs5vqUrEJ7CPtKg1qzXHhauERuh4MiNMB) | nee | 0.08 | 34,219 | +1129% | 208.8 | 6% | 11069 | 84 |
| 2026-09-23 | dumpers_24h | 3 | [MADVULT](https://dexscreener.com/solana/MadVLQYW7VUzfMdMDHuS3UVgJSNUQGwvobYgYZxpqp6) | nee | 0.06 | 32,060 | -4% | 221.7 | 0% | 3331 | 7 |
| 2026-09-23 | dumpers_24h | 4 | [APEZCAT](https://dexscreener.com/solana/D5Sd6RGFcsyCg4UgKMTax22ZYjBrivQXAAGAVAaQpump) | nee | 0.02 | 30,643 | +1% | 64.9 | 2% | 1613 | 11 |
| 2026-09-23 | dumpers_24h | 5 | [MOUSE](https://dexscreener.com/solana/8MPLzMvudX7ehD8VjKSDDWCS9bAu1N6KK3Ubg5xypump) | nee | 0.03 | 29,108 | +33% | 181.0 | 1% | 3226 | 12 |
| 2026-09-23 | trending_24h | 1 | [AROS](https://dexscreener.com/solana/12PUFAUzgLj1onv3wSqX9ZagZBiaxofEjTd24CnQpump) | ja | 0.67 | 24,962 | +6194609% | 61947.1 | 100% | 2041 | 21,170 |
| 2026-09-23 | trending_24h | 2 | [VSOF](https://dexscreener.com/solana/gBiiUhaYjAj4zCGSzKJa1S23ZgB9U7w8k3epbbQpump) | ja | 0.61 | 19,625 | +3614636% | 36153.4 | 100% | 1987 | 16,169 |
| 2026-09-23 | trending_24h | 3 | [VSOF](https://dexscreener.com/solana/2dfyCX3hJs6uzzXH2KPgHduDgsXCTP57mQf3E1nFpump) | ja | 0.67 | 5,963 | +2428644% | 24287.4 | 100% | 1561 | 5,913 |
| 2026-09-23 | trending_24h | 4 | [USDF](https://dexscreener.com/solana/tTthNiywcdw5KApjD7c6JfFnFW3KdgMBBKgzc14pump) | ja | 0.34 | 12,297 | +1034827% | 10349.3 | 100% | 2075 | 8,653 |
| 2026-09-23 | trending_24h | 5 | [VSOF](https://dexscreener.com/solana/H1vNzAivJt1rjeEFNfCmGcDwUEoLmYAcCAYYysnVpump) | ja | 0.54 | 10,382 | +918274% | 9183.7 | 100% | 2765 | 8,143 |
| 2026-09-23 | trending_24h_organic | 1 | [familiars](https://dexscreener.com/solana/2PENPmfgJfq6CG3k4byj4oWwHf8SerqakmYHMkUupump) | nee | 0.06 | 18,337 | +26988% | 306.3 | 88% | 4861 | 381 |
| 2026-09-23 | trending_24h_organic | 2 | [BLUF](https://dexscreener.com/solana/c4AtfqMRbC9FuHtVEHDhCm453tytU9E34MXXrp6bLuf) | nee | 0.08 | 45,432 | +9008% | 432.2 | 21% | 11654 | 233 |
| 2026-09-23 | trending_24h_organic | 3 | [ARCHIBROWN](https://dexscreener.com/solana/HaYRgD7rjwj1AwoWjafokj4196sufYm4vKFamLZVpump) | nee | 0.07 | 23,012 | +8553% | 232.9 | 37% | 6561 | 243 |
| 2026-09-23 | trending_24h_organic | 4 | [$GULL](https://dexscreener.com/solana/XQvVFz2jrrqFvhdG4NRd2o9xCQci9Xn9nGYVumBpump) | nee | 0.09 | 5,318 | +14564% | 650.9 | 23% | 2247 | 391 |
| 2026-09-23 | trending_24h_organic | 5 | [GIGACAT](https://dexscreener.com/solana/2jcvq8QcJ8TzEYJKYMCjR61kXVSzib4mEkz89tQxpump) | nee | 0.06 | 20,292 | +2859% | 170.5 | 17% | 7490 | 129 |
| 2026-09-24 | dumpers_24h | 1 | [MCNAP](https://dexscreener.com/solana/23d5EtiLeT1t6seccoNNADa82yUeKrch8g6ei9tVpump) | ja | 0.02 | 48,271 | +25% | 133.5 | 1% | 2868 | 14 |
| 2026-09-24 | dumpers_24h | 2 | [NERD](https://dexscreener.com/solana/5kdbTqWKajFMwTc2Mg1EiAeFzndWN7bX79BykGhNpump) | nee | 0.03 | 44,267 | +32% | 112.7 | 1% | 2844 | 15 |
| 2026-09-24 | dumpers_24h | 3 | [PINF](https://dexscreener.com/solana/MLdXKXUGXoYSDx5pcDWMrCdLP2AyxzxaQd3QvFMpump) | nee | 0.03 | 38,138 | +13% | 171.4 | 1% | 2591 | 11 |
| 2026-09-24 | dumpers_24h | 4 | [AROS](https://dexscreener.com/solana/12PUFAUzgLj1onv3wSqX9ZagZBiaxofEjTd24CnQpump) | ja | 0.48 | 36,880 | -100% | 1.6 | 0% | 3067 | 2 |
| 2026-09-24 | dumpers_24h | 5 | [PLOP](https://dexscreener.com/solana/F8BTnFMCaajnrRYfmVGqJrvm4PYcFb1AiTwLxCpSpump) | ja | 0.02 | 28,095 | -4% | 110.9 | 1% | 2461 | 8 |
| 2026-09-24 | trending_24h | 1 | [USDF](https://dexscreener.com/solana/Ufwjn7mtvgUfRG4nMSxRS84ewXHzdHLJRGzMDBMpump) | ja | 0.41 | 40,740 | +4346481% | 43473.6 | 100% | 3445 | 17,759 |
| 2026-09-24 | trending_24h | 2 | [AROS](https://dexscreener.com/solana/DQz5ucSjjWMXKaCPB35zvJFCvc7sULYhQKvLxbnpump) | ja | 0.66 | 11,092 | +1356461% | 13565.6 | 100% | 2970 | 9,897 |
| 2026-09-24 | trending_24h | 3 | [AROS](https://dexscreener.com/solana/2DznHsR5j6XHARnTVGubwAXxfmF9aQ8Pwwu3TN7ppump) | ja | 0.77 | 8,207 | +750181% | 7502.8 | 100% | 2668 | 7,355 |
| 2026-09-24 | trending_24h | 4 | [VSOF](https://dexscreener.com/solana/4m9Y1o568PXrWuunpMoN3sHaZKcVUbuRyGcjbBzNpump) | ja | 0.65 | 8,911 | +592656% | 5927.6 | 100% | 106 | 6,537 |
| 2026-09-24 | trending_24h | 5 | [AROS](https://dexscreener.com/solana/3DQbzaaQtDpNyeKY3ZQFWbqZxr8FV1FwGMxf1WpVpump) | ja | 0.98 | 4,094 | +1143418% | 11435.2 | 100% | 1445 | 4,051 |
| 2026-09-24 | trending_24h_organic | 1 | [NPC](https://dexscreener.com/solana/7GUnr7krtQhJwd6ASY2VUprd9t4c64zcgCsjdmZepump) | nee | 0.08 | 45,347 | +25753% | 277.6 | 93% | 14497 | 664 |
| 2026-09-24 | trending_24h_organic | 2 | [SI](https://dexscreener.com/solana/82ezhRLKdKwkSC9jkM3js1yf93VbmvLXNMPkBmompump) | nee | 0.08 | 29,625 | +4590% | 161.5 | 29% | 10070 | 206 |
| 2026-09-24 | trending_24h_organic | 3 | [FomoFantasy](https://dexscreener.com/solana/Bg75GaeTN7uVqtUEsquh61hwENeaA8TD5K8HwX4Lfomo) | nee | 0.06 | 11,335 | +5120% | 110.2 | 47% | 4743 | 158 |
| 2026-09-24 | trending_24h_organic | 4 | [parafactual](https://dexscreener.com/solana/CvUX4G2tzq7J3ECmNvWZLayZYV6KpgQp6SJ5K8Uvsu5g) | nee | 0.11 | 24,662 | +1823% | 28.0 | 69% | 6580 | 351 |
| 2026-09-24 | trending_24h_organic | 5 | [Jeanjak](https://dexscreener.com/solana/CgMTyUGeUhz7qsbxr46Uw2vjkEXioWEtJUoMcpgUpump) | nee | 0.02 | 14,548 | +2839% | 117.4 | 25% | 1579 | 114 |
| 2026-09-25 | dumpers_24h | 1 | [SOLRISING](https://dexscreener.com/solana/93Q1FohrzWzsvJXJujLW7PrZLZtpCPmviKyWKEX3pump) | ja | 0.01 | 43,669 | -60% | 90.9 | 0% | 3332 | 10 |
| 2026-09-25 | dumpers_24h | 2 | [AROS](https://dexscreener.com/solana/Xc4WkwXwNmMApMmxvwT8994tLMUziPCgxBmmPC4pump) | ja | 0.69 | 43,499 | -95% | 53517.5 | 0% | 3129 | 2 |
| 2026-09-25 | dumpers_24h | 3 | [PEEP](https://dexscreener.com/solana/jU7hpVbGfLPFzfHB1iy74hPA379iubPqVQwLGBepump) | nee | 0.04 | 41,460 | +231% | 158.1 | 2% | 2889 | 31 |
| 2026-09-25 | dumpers_24h | 4 | [IMU](https://dexscreener.com/solana/33owuxq97P85XaV2oXvBFmMoykb3ZjLH5wdAV4uDpump) | nee | 0.01 | 40,006 | -81% | 114.0 | 0% | 3319 | 12 |
| 2026-09-25 | dumpers_24h | 5 | [SHIBA](https://dexscreener.com/solana/3V43dHnk3kkU3w8RnxEsmbJcHQppGjTJ3Mni3owDpump) | ja | 0.01 | 33,171 | -67% | 88.7 | 0% | 3199 | 7 |
| 2026-09-25 | trending_24h | 1 | [USDF](https://dexscreener.com/solana/qhyWQAfBWW8pmDUiNWVwq4e4SZWeMgEvJ3KRRespump) | ja | 0.59 | 20,615 | +3892045% | 38936.3 | 100% | 3273 | 16,780 |
| 2026-09-25 | trending_24h | 2 | [VSOF](https://dexscreener.com/solana/mRRHbeK6G5kmT8wc3AwkSeijAQWEnzS69vmbQ1Gpump) | ja | 0.34 | 15,869 | +2535837% | 25373.7 | 100% | 3030 | 13,548 |
| 2026-09-25 | trending_24h | 3 | [VSOF](https://dexscreener.com/solana/BYPmzwqUT8NcDTWeGFbMQqafg7JHvLxfepjfF4Ahpump) | ja | 0.34 | 17,574 | +1991614% | 19917.1 | 100% | 132 | 12,007 |
| 2026-09-25 | trending_24h | 4 | [AROS](https://dexscreener.com/solana/QcakAHiP7BgZqzFupUEH5RzcpmoaYn9hWuBih62pump) | ja | 0.68 | 14,102 | +1957655% | 19577.6 | 100% | 3102 | 11,893 |
| 2026-09-25 | trending_24h | 5 | [AROS](https://dexscreener.com/solana/57LVTCfaFUZ9tMM7vhJDgF3oebWt7GTzdrhRK5fqpump) | ja | 0.69 | 14,275 | +1914340% | 19144.4 | 100% | 2763 | 11,761 |
| 2026-09-25 | trending_24h_organic | 1 | [e/acc](https://dexscreener.com/solana/CbcyNo7m1amFWqEQm2m4PLv1UNvpcL3C1Ujm6AkzpKoU) | nee | 0.06 | 50,666 | +24661% | 320.1 | 77% | 12106 | 453 |
| 2026-09-25 | trending_24h_organic | 2 | [YAP](https://dexscreener.com/solana/jLz71QZfjnZZMLjUaCBw7KmUyftkkNq8NkWi2u9dyap) | nee | 0.05 | 31,850 | +19912% | 390.4 | 51% | 8414 | 343 |
| 2026-09-25 | trending_24h_organic | 3 | [Bagwork](https://dexscreener.com/solana/5NhN6zzDkzwXFGPFqtpTopV4ttBeZ6CWy1oRL9Rkpump) | nee | 0.06 | 47,206 | +12132% | 682.2 | 18% | 12168 | 273 |
| 2026-09-25 | trending_24h_organic | 4 | [TTP](https://dexscreener.com/solana/5iqjhP5ig6JWdXQf6hYAwojZeMf11pTCvH87gCQtpump) | nee | 0.06 | 41,283 | +9401% | 586.4 | 16% | 10718 | 235 |
| 2026-09-25 | trending_24h_organic | 5 | [SWARM](https://dexscreener.com/solana/5g9PWcRUHpmqm5uKycpShKXJB18sGLeCEzgB3qrRpump) | nee | 0.07 | 38,638 | +6720% | 301.7 | 23% | 14514 | 261 |
| 2026-09-26 | dumpers_24h | 1 | [Cream](https://dexscreener.com/solana/CMuvWQ6qQbSt7NUQdPGcVKCoDgETieuqS68qu6BaeVgR) | nee | 0.09 | 66,182 | +2760% | 296.6 | 10% | 12559 | 190 |
| 2026-09-26 | dumpers_24h | 2 | [DEBT](https://dexscreener.com/solana/FYM6PhwAS3QwTBwvgLj8JYeMgpNfKsWtW3hGxPM9eFkt) | nee | 0.09 | 61,916 | +656% | 225.8 | 3% | 13887 | 77 |
| 2026-09-26 | dumpers_24h | 3 | [act/acc](https://dexscreener.com/solana/3gLCmbKsF2eps3JpFLykgVhkzi8Vh2wLAco7FUnvpump) | nee | 0.01 | 53,060 | +9% | 454.0 | 0% | 3583 | 10 |
| 2026-09-26 | dumpers_24h | 4 | [AROS](https://dexscreener.com/solana/DsJDVBeNnUYvsJEaLyhNm6xrULzVDzuAPCVCHKRCpump) | ja | 0.59 | 44,477 | -95% | 55994.4 | 0% | 2684 | 2 |
| 2026-09-26 | dumpers_24h | 5 | [P(DOOM)](https://dexscreener.com/solana/ARPwPPWbaj3FYBf6k1Lt2jqRkv9JJUg3Hxav5aHKTEem) | nee | 0.08 | 37,916 | +2522% | 325.8 | 8% | 8628 | 124 |
| 2026-09-26 | trending_24h | 1 | [AROS](https://dexscreener.com/solana/kFWgzdFBd6DERYGSWUrT3ZUUjoREcv14rHJCBSbpump) | ja | 0.52 | 30,673 | +10101265% | 101024.8 | 100% | 3056 | 27,045 |
| 2026-09-26 | trending_24h | 2 | [VSOF](https://dexscreener.com/solana/ehegfG1UQEFSYKcV6ReiMupjheDm22FoQQukiDspump) | ja | 0.72 | 30,512 | +8763512% | 87636.1 | 100% | 3076 | 25,183 |
| 2026-09-26 | trending_24h | 3 | [AROS](https://dexscreener.com/solana/82w6YGQD7cb7SypvkoQrRTHG3qNzmVTNUcZ6njNRpump) | ja | 0.65 | 10,219 | +914806% | 9149.1 | 100% | 2758 | 8,126 |
| 2026-09-26 | trending_24h | 4 | [manifest](https://dexscreener.com/solana/8vdJA3MYhUhirZZzjZ5i8jXhNkHgrCnirCrqDG23pump) | nee | 0.02 | 21,834 | +282120% | 3306.6 | 85% | 2035 | 1,243 |
| 2026-09-26 | trending_24h | 5 | [XBC](https://dexscreener.com/solana/12ppTUnBWHbGy11h3QsXiat2sydhLAgjevu55jYzpump) | ja | 0.77 | 8,070 | +712364% | 7125.2 | 100% | 3051 | 7,167 |
| 2026-09-26 | trending_24h_organic | 1 | [manifest](https://dexscreener.com/solana/8vdJA3MYhUhirZZzjZ5i8jXhNkHgrCnirCrqDG23pump) | nee | 0.02 | 21,834 | +282120% | 3306.6 | 85% | 2035 | 1,243 |
| 2026-09-26 | trending_24h_organic | 2 | [KARDASHEV](https://dexscreener.com/solana/5wW9mhbwq1HTFh341iimpmrqBB4mfxdXiYhdYBL7hUnp) | nee | 0.10 | 85,890 | +20982% | 979.3 | 22% | 14165 | 431 |
| 2026-09-26 | trending_24h_organic | 3 | [MUNKIEAI](https://dexscreener.com/solana/2nJpGnrkX6pDPstr4JELcWnGsjiN57vajfR89iZkpump) | nee | 0.01 | 29,906 | +24966% | 525.1 | 48% | 3245 | 369 |
| 2026-09-26 | trending_24h_organic | 4 | [DDOS](https://dexscreener.com/solana/HSnCtWsw5YmfAkJo9S3eetWtGHPMrc2pAxvCu24AnQEa) | nee | 0.09 | 68,872 | +7742% | 428.9 | 18% | 13385 | 260 |
| 2026-09-26 | trending_24h_organic | 5 | [e/acc](https://dexscreener.com/solana/CbcyNo7m1amFWqEQm2m4PLv1UNvpcL3C1Ujm6AkzpKoU) | nee | 0.06 | 456,244 | +1061% | 28.0 | 41% | 35777 | 2,059 |
| 2026-09-27 | dumpers_24h | 1 | [MPAID](https://dexscreener.com/solana/Ab6CVmhqD3x1bvMZBnjJBJqeTLMm7tAapcZwSU6apump) | ja | 0.02 | 111,227 | +70% | 354.9 | 0% | 6976 | 23 |
| 2026-09-27 | dumpers_24h | 2 | [JACK](https://dexscreener.com/solana/J1yxV53EmVRmt9RUj6PPufMfdAYUbtgwXCuNYTBTsYzJ) | nee | 0.10 | 107,867 | +1948% | 819.9 | 2% | 20664 | 196 |
| 2026-09-27 | dumpers_24h | 3 | [CAKE](https://dexscreener.com/solana/DAemPFNc3RtibBDKkUA4eL2Ns11q9VRdbpptmqm8DGqN) | nee | 0.06 | 95,556 | +1694% | 267.1 | 7% | 14346 | 229 |
| 2026-09-27 | dumpers_24h | 4 | [GENO](https://dexscreener.com/solana/6XBj4xjqJeF7R4aqfrSdyyx6TUHgKEjUkCttmoECpump) | nee | 0.01 | 63,018 | -81% | 116.1 | 0% | 3198 | 12 |
| 2026-09-27 | dumpers_24h | 5 | [fomonpc](https://dexscreener.com/solana/9Qa71JpzkwD94Ghs7DYsJNuXF1SJdSSHJQDgmbCcpump) | ja | 0.02 | 56,913 | +35% | 295.3 | 0% | 3327 | 15 |
| 2026-09-27 | trending_24h | 1 | [JEANPHIL](https://dexscreener.com/solana/xQ9npAUWQFHrK2SUSoDgzHzsRjMgBvsJ1j6AK3Ypump) | ja | 0.43 | 13,098 | +1355082% | 13551.8 | 100% | 3047 | 9,898 |
| 2026-09-27 | trending_24h | 2 | [USDF](https://dexscreener.com/solana/Y49yTFUyBiim3HQEiVnVKiiRPqTY2nNrJyvxsi4pump) | ja | 0.55 | 9,937 | +903724% | 9038.2 | 100% | 3073 | 8,077 |
| 2026-09-27 | trending_24h | 3 | [VSOF](https://dexscreener.com/solana/wQK5ZserCHkhUCJQJxLL7NKg4N3o3R9VTEqJFqfpump) | ja | 0.59 | 9,245 | +791686% | 7920.1 | 100% | 3021 | 7,558 |
| 2026-09-27 | trending_24h | 4 | [AROS](https://dexscreener.com/solana/12mHDRzSgf5xo8NPyVQY2nZ3XarQuYUEShaVTgAFpump) | ja | 0.65 | 8,518 | +630017% | 6301.2 | 100% | 180 | 6,740 |
| 2026-09-27 | trending_24h | 5 | [VSOF](https://dexscreener.com/solana/3CA4RGSFWAu7ePYr2rmCndNqgoYuMj97m5vAn6Zqpump) | ja | 0.80 | 6,668 | +464492% | 4645.9 | 100% | 103 | 5,784 |
| 2026-09-27 | trending_24h_organic | 1 | [MARTIANS](https://dexscreener.com/solana/HgoScXAo4zFv5kDb6axGrwuz95FaVEcLskyDVZfo297z) | nee | 0.13 | 26,089 | +36324% | 610.5 | 60% | 6868 | 454 |
| 2026-09-27 | trending_24h_organic | 2 | [moin](https://dexscreener.com/solana/8DXqVUopcdviLvujTcpwEqkKzB43Arp6E1axzsEE5Btq) | nee | 0.06 | 35,584 | +17804% | 387.6 | 46% | 10928 | 344 |
| 2026-09-27 | trending_24h_organic | 3 | [VAULT](https://dexscreener.com/solana/7tEsDuij7aj4b2jLUAS5oXcp6B3zWratYVmviS5zpump) | nee | 0.07 | 39,309 | +6070% | 300.6 | 21% | 10693 | 222 |
| 2026-09-27 | trending_24h_organic | 4 | [JACK](https://dexscreener.com/solana/J1yxV53EmVRmt9RUj6PPufMfdAYUbtgwXCuNYTBTsYzJ) | nee | 0.10 | 107,867 | +1948% | 819.9 | 2% | 20664 | 196 |
| 2026-09-27 | trending_24h_organic | 5 | [CAKE](https://dexscreener.com/solana/DAemPFNc3RtibBDKkUA4eL2Ns11q9VRdbpptmqm8DGqN) | nee | 0.06 | 95,556 | +1694% | 267.1 | 7% | 14346 | 229 |
| 2026-09-28 | dumpers_24h | 1 | [SOLFROG](https://dexscreener.com/solana/TTwA29nT5v7Vti9xdvYrhMnHxo7mXNbx8aLcFLjpump) | nee | 0.01 | 159,996 | +102% | 479.4 | 0% | 6110 | 30 |
| 2026-09-28 | dumpers_24h | 2 | [hippo/acc](https://dexscreener.com/solana/CGfVJX4D35wE8kviKKE1XyKU9EHRXe4Zsw3R55ABpump) | ja | 0.02 | 107,915 | +137% | 662.7 | 0% | 5782 | 29 |
| 2026-09-28 | dumpers_24h | 3 | [XPAD](https://dexscreener.com/solana/FFzTy32bZbPA8zRLbbiWoNzW2emGv86tUxaoqhz1pump) | ja | 0.00 | 58,304 | +239% | 609.7 | 1% | 13277 | 31 |
| 2026-09-28 | dumpers_24h | 4 | [peptober](https://dexscreener.com/solana/9VXMEkhYKcj9s4dvzz3GF4ZfHGc5oAURiSY7ftPpump) | ja | 0.01 | 53,809 | +50% | 435.9 | 0% | 5225 | 15 |
| 2026-09-28 | dumpers_24h | 5 | [AROS](https://dexscreener.com/solana/kFWgzdFBd6DERYGSWUrT3ZUUjoREcv14rHJCBSbpump) | ja | 0.49 | 40,047 | -100% | 1.1 | 0% | 3000 | 4 |
| 2026-09-28 | trending_24h | 1 | [AROS](https://dexscreener.com/solana/XU438yQcHEf5bGAZ3pHqXhbZPqotoapdanjnhj1pump) | ja | 0.45 | 12,366 | +1285607% | 12857.1 | 100% | 3011 | 9,640 |
| 2026-09-28 | trending_24h | 2 | [COLLECT](https://dexscreener.com/solana/hAvfbtSG3ZTH9wwHQLEERJbiyHypu9Q7JefzhrEpump) | ja | 0.59 | 9,461 | +791877% | 7919.8 | 100% | 2968 | 7,560 |
| 2026-09-28 | trending_24h | 3 | [JEANPHIL](https://dexscreener.com/solana/14cmYsjbqAHh1DW7iC5cQ2tiAaarbBxvTRHS8K1Tpump) | ja | 0.59 | 9,282 | +787230% | 7875.9 | 100% | 2911 | 7,537 |
| 2026-09-28 | trending_24h | 4 | [NTDA](https://dexscreener.com/solana/JQhbwzFJKUBPYVR1JSfRyyKP2sypPXSBJguxc8Npump) | ja | 0.86 | 5,825 | +421824% | 4219.2 | 100% | 2937 | 5,510 |
| 2026-09-28 | trending_24h | 5 | [WSOS](https://dexscreener.com/solana/EthghzCFZNvTUbUeHsTZCWXZ4HXjzszSPs9bwvJpump) | ja | 0.94 | 5,332 | +379646% | 3797.9 | 100% | 2568 | 5,226 |
| 2026-09-28 | trending_24h_organic | 1 | [FROINK](https://dexscreener.com/solana/2yu92oYzBWLAdVpu8BoaLzmM1oxPsHoboay2BXmeDDZr) | nee | 0.06 | 82,494 | +20359% | 668.1 | 31% | 15162 | 378 |
| 2026-09-28 | trending_24h_organic | 2 | [swordcat](https://dexscreener.com/solana/5tCju6YNxHq5zrA6tGndr6F7TK42mpUFmeE31cSFpump) | nee | 0.06 | 40,694 | +13801% | 375.8 | 37% | 12169 | 354 |
| 2026-09-28 | trending_24h_organic | 3 | [BOB](https://dexscreener.com/solana/E4vwseNWpkBxzNxyv3msVLCzYHU5SXrj1QWXEycypump) | nee | 0.07 | 32,478 | +11317% | 202.1 | 56% | 11315 | 258 |
| 2026-09-28 | trending_24h_organic | 4 | [AI6](https://dexscreener.com/solana/DKxXdaMC1so182urvrrnhs6V6fGTrttPS8br6JuEpump) | nee | 0.03 | 17,193 | +5485% | 112.2 | 50% | 5696 | 171 |
| 2026-09-28 | trending_24h_organic | 5 | [LEVERAGE](https://dexscreener.com/solana/BM2k8mJUbMthHoioykyUm2NjMrXvLBYhoXruwYLpump) | nee | 0.06 | 39,629 | +1175% | 420.3 | 3% | 11856 | 76 |
| 2026-09-29 | dumpers_24h | 1 | [SI](https://dexscreener.com/solana/7Wh6rxVWUBFCNCWCz7nLaV7z3SDr2M6rFTjjWP6aE8p1) | nee | 0.05 | 55,066 | +4241% | 482.8 | 9% | 12862 | 176 |
| 2026-09-29 | dumpers_24h | 2 | [cNFTs](https://dexscreener.com/solana/A71Uf3jwg57fNedSao9eFwm9eKSLYFbVJ85T4fuXpump) | nee | 0.10 | 45,270 | +1185% | 303.1 | 4% | 11749 | 79 |
| 2026-09-29 | dumpers_24h | 3 | [INUINK](https://dexscreener.com/solana/9k7NgXqiJ7tvLtiB6HXdJHnKZZynFz46AB6Eg4Uwpump) | ja | 0.00 | 42,418 | -100% | 1.4 | 0% | 19716 | 10 |
| 2026-09-29 | dumpers_24h | 4 | [PFSOL](https://dexscreener.com/solana/5DzdcJUwovmAUXMfrWgbhKmJ3RWmEtXwf7EEtuHRpump) | nee | 0.02 | 25,192 | -6% | 203.9 | 0% | 1665 | 7 |
| 2026-09-29 | dumpers_24h | 5 | [NTDA](https://dexscreener.com/solana/xtzSskqY2PSSUiq7pBvtK48HikcL2pRUvtseHSTpump) | ja | 0.79 | 20,181 | -94% | 13132.8 | 0% | 2945 | 4 |
| 2026-09-29 | trending_24h | 1 | [NTDA](https://dexscreener.com/solana/3aDDk7wDuNvujvr4AqsVfYZcoKQn5EkXAVqXcdxhpump) | ja | 0.73 | 13,184 | +1800217% | 18003.2 | 100% | 2751 | 11,403 |
| 2026-09-29 | trending_24h | 2 | [NTDA](https://dexscreener.com/solana/GGf4EX9qbzxuboefDTEvqHdysHqtZSQC7Sahprjpump) | ja | 0.61 | 12,365 | +1500010% | 15001.1 | 100% | 3033 | 10,410 |
| 2026-09-29 | trending_24h | 3 | [UDR](https://dexscreener.com/solana/uEMVScZfPPfSGeoKkPCQ82oRedJn6u1dQvDEo8qpump) | ja | 0.44 | 12,866 | +1286583% | 12866.8 | 100% | 3212 | 9,644 |
| 2026-09-29 | trending_24h | 4 | [VSOF](https://dexscreener.com/solana/erZo4bdwE1wZ4E2D8RQzCMdxypL5kVWC7Du6zdCpump) | ja | 0.45 | 12,564 | +1263669% | 12640.9 | 100% | 3041 | 9,557 |
| 2026-09-29 | trending_24h | 5 | [DOTF](https://dexscreener.com/solana/jSh3qi3qf7b3gWtCdpfMzSH2WFR1Qp6gyw1Xc3Npump) | ja | 0.46 | 12,389 | +1223023% | 12233.9 | 100% | 2985 | 9,402 |
| 2026-09-29 | trending_24h_organic | 1 | [PARASITE](https://dexscreener.com/solana/3kmygWKZBkCYrgZHKfiuB9UFKTcDLTFFsKo3BWpmpump) | nee | 0.07 | 104,887 | +24961% | 1230.4 | 20% | 25350 | 453 |
| 2026-09-29 | trending_24h_organic | 2 | [SI](https://dexscreener.com/solana/7Wh6rxVWUBFCNCWCz7nLaV7z3SDr2M6rFTjjWP6aE8p1) | nee | 0.05 | 55,066 | +4241% | 482.8 | 9% | 12862 | 176 |
| 2026-09-29 | trending_24h_organic | 3 | [SI](https://dexscreener.com/solana/9aqmJjCnnMQv42TXLk921ceUkN35nea2QP969n1caqjj) | nee | 0.04 | 21,848 | +5869% | 194.5 | 31% | 6002 | 207 |
| 2026-09-29 | trending_24h_organic | 4 | [POND](https://dexscreener.com/solana/84D3VopU3g5qo7jUwzQK4P4BnVPQWr2MzeaRCFkHpump) | nee | 0.03 | 25,781 | +2227% | 174.7 | 13% | 8928 | 111 |
| 2026-09-29 | trending_24h_organic | 5 | [cNFTs](https://dexscreener.com/solana/A71Uf3jwg57fNedSao9eFwm9eKSLYFbVJ85T4fuXpump) | nee | 0.10 | 45,270 | +1185% | 303.1 | 4% | 11749 | 79 |
| 2026-09-30 | dumpers_24h | 1 | [AIRPAD](https://dexscreener.com/solana/6ZG6fMHaE7NpubJr16G8nFLBg91rH5kU5SVpqUmQpump) | ja | 0.00 | 87,746 | +96% | 667.8 | 0% | 13014 | 22 |
| 2026-09-30 | dumpers_24h | 2 | [Saw](https://dexscreener.com/solana/9twiuSdTVkwtAC9XQDG57dFRhF4iqPih461HJfMZKci9) | nee | 0.06 | 62,702 | +2478% | 412.9 | 6% | 15654 | 141 |
| 2026-09-30 | dumpers_24h | 3 | [UDR](https://dexscreener.com/solana/BwzpbM9DHpCh9qu9WosyUxjKw9ZLCyHWYeKts6TKpump) | ja | 0.27 | 46,560 | -94% | 54330.2 | 0% | 2438 | 3 |
| 2026-09-30 | dumpers_24h | 4 | [UDR](https://dexscreener.com/solana/NkpbN7shUNdkvt24F33oai9Cf9rXDzJ4E8Sx2mNpump) | ja | 0.71 | 44,599 | -91% | 63701.6 | 0% | 3228 | 9 |
| 2026-09-30 | dumpers_24h | 5 | [Mitch ](https://dexscreener.com/solana/FutN7pjfoJjEsfRG8BMtam2jtWdbg2up9HMa7dYwpump) | ja | 0.04 | 28,421 | -10% | 190.8 | 0% | 1554 | 7 |
| 2026-09-30 | trending_24h | 1 | [UDR](https://dexscreener.com/solana/PEYMPE7pk4KJmRn51KZsTgXPUh1zVMRxWbsWygapump) | ja | 0.81 | 25,519 | +7804354% | 78044.5 | 100% | 3023 | 23,758 |
| 2026-09-30 | trending_24h | 2 | [GOIF](https://dexscreener.com/solana/m6JwPCoxzsM2f7jiSdXLnFEQzEcug2SXTzBb1zopump) | ja | 0.89 | 15,407 | +3083588% | 30836.9 | 100% | 2948 | 14,926 |
| 2026-09-30 | trending_24h | 3 | [GOIF](https://dexscreener.com/solana/3bNwW6PrQJBGXTiE6peXL3org95Eiheqy9Nsci4fpump) | ja | 0.58 | 14,460 | +2287376% | 22874.8 | 100% | 2277 | 12,858 |
| 2026-09-30 | trending_24h | 4 | [VSOF](https://dexscreener.com/solana/CnrPtHUeeM6QtHjkEArLF68B2pf2TvhJfSvShaQpump) | ja | 0.39 | 14,519 | +1603364% | 16034.6 | 100% | 3091 | 10,770 |
| 2026-09-30 | trending_24h | 5 | [AROS](https://dexscreener.com/solana/GC9GWsd9mQhj4GWpUQBxw7peBEimGjgpUrGgkWBpump) | ja | 0.41 | 14,009 | +1512047% | 15121.5 | 100% | 3054 | 10,458 |
| 2026-09-30 | trending_24h_organic | 1 | [BANDIT](https://dexscreener.com/solana/JCX5nDG99k1CWB9AttJ3U57NmjSgnCoagFaZhqfEf7Ah) | nee | 0.07 | 78,771 | +15932% | 568.2 | 28% | 16376 | 342 |
| 2026-09-30 | trending_24h_organic | 2 | [Meridian](https://dexscreener.com/solana/GjrhXcr8YzUZBbu5rifdqM5CPBS4LFJveq3ZiY2u3g5G) | nee | 0.07 | 21,189 | +13316% | 295.9 | 45% | 7133 | 307 |
| 2026-09-30 | trending_24h_organic | 3 | [SI](https://dexscreener.com/solana/9aqmJjCnnMQv42TXLk921ceUkN35nea2QP969n1caqjj) | nee | 0.07 | 134,713 | +1581% | 33.5 | 50% | 24758 | 1,091 |
| 2026-09-30 | trending_24h_organic | 4 | [Saw](https://dexscreener.com/solana/9twiuSdTVkwtAC9XQDG57dFRhF4iqPih461HJfMZKci9) | nee | 0.06 | 62,702 | +2478% | 412.9 | 6% | 15654 | 141 |
| 2026-09-30 | trending_24h_organic | 5 | [SI](https://dexscreener.com/solana/9rJsqPD1UqeXo1dgPybwTKm7Luga8bXVQ5A3EiYFhfeA) | nee | 0.04 | 31,495 | +2465% | 205.1 | 13% | 11401 | 133 |

## 7 dagen: blijvers en instorters (met en zonder zelffinanciering)

| lijst | # | token | zelfgefin. | mcap na 7 d (SOL) | piek-mcap (SOL) | eind t.o.v. piek | volume 7 d | traders | SOL in pool (7 d) |
|---|---|---|---|---|---|---|---|---|---|
| collapsed_7d | 1 | [AROS](https://dexscreener.com/solana/12PUFAUzgLj1onv3wSqX9ZagZBiaxofEjTd24CnQpump) | ja | 38 | 41,715,680 | 0% | 61,803 | 3201 | 9 |
| collapsed_7d | 2 | [USDF](https://dexscreener.com/solana/qhyWQAfBWW8pmDUiNWVwq4e4SZWeMgEvJ3KRRespump) | ja | 25 | 36,695,981 | 0% | 60,642 | 3444 | 4 |
| collapsed_7d | 3 | [VSOF](https://dexscreener.com/solana/gBiiUhaYjAj4zCGSzKJa1S23ZgB9U7w8k3epbbQpump) | ja | 95 | 34,877,626 | 0% | 58,335 | 3138 | 24 |
| collapsed_7d | 4 | [WOFI](https://dexscreener.com/solana/F2reRYPQUPkagQy7t5VaZgtiHqXNFQ91ej15GRypump) | ja | 40 | 22,044,743 | 0% | 47,589 | 2055 | 9 |
| collapsed_7d | 5 | [AROS](https://dexscreener.com/solana/Xc4WkwXwNmMApMmxvwT8994tLMUziPCgxBmmPC4pump) | ja | 24 | 21,989,268 | 0% | 43,418 | 3134 | 3 |
| collapsed_7d | 6 | [WOFI](https://dexscreener.com/solana/izUd2DKhKefxT44NCCNpVvaRb963MYeWFsubGVSpump) | ja | 22 | 21,174,446 | 0% | 52,983 | 2049 | 3 |
| collapsed_7d | 7 | [USDF](https://dexscreener.com/solana/Ufwjn7mtvgUfRG4nMSxRS84ewXHzdHLJRGzMDBMpump) | ja | 23 | 19,829,101 | 0% | 60,979 | 3536 | 3 |
| collapsed_7d | 8 | [WOFI](https://dexscreener.com/solana/RdSJAdbWBBgKejj8GDzmqcQPdpnhMPYBTNog77Qpump) | ja | 20 | 16,231,901 | 0% | 40,934 | 2006 | 1 |
| collapsed_7d | 9 | [NTDA](https://dexscreener.com/solana/FfK9fj41K98EuNZK2oQDRWuoyT9vb4d4jR412HcMpump) | ja | 30 | 16,201,153 | 0% | 38,681 | 2981 | 6 |
| collapsed_7d | 10 | [WOTF](https://dexscreener.com/solana/Zd8xgiFB1AGYTmZ78MS5APSXmDTNTLUJKuSGUD7pump) | ja | 41 | 15,563,557 | 0% | 40,458 | 1992 | 10 |
| collapsed_7d_organic | 1 | [TDOF](https://dexscreener.com/solana/eS3iMtCY7XPvib3ERakSjfHophQmRWLVzT6roTqpump) | nee | 18 | 340,868 | 0% | 7,909 | 1364 | 0 |
| collapsed_7d_organic | 2 | [DAYTRADER](https://dexscreener.com/solana/HTsqNsBZc5sjooBTL2WrKvFxd6S8P3kEkPmATnfHpump) | nee | 39 | 110,921 | 0% | 7,725 | 745 | 9 |
| collapsed_7d_organic | 3 | [LAUNCHPAD](https://dexscreener.com/solana/HKWp72BBE7giEdVciLf6PuKgkpo9jqT9V83ximEypump) | nee | 19 | 52,930 | 0% | 8,369 | 694 | 1 |
| collapsed_7d_organic | 4 | [NPC](https://dexscreener.com/solana/7GUnr7krtQhJwd6ASY2VUprd9t4c64zcgCsjdmZepump) | nee | 1,168 | 51,938 | 2% | 124,351 | 27295 | 175 |
| collapsed_7d_organic | 5 | [YAP](https://dexscreener.com/solana/jLz71QZfjnZZMLjUaCBw7KmUyftkkNq8NkWi2u9dyap) | nee | 403 | 46,566 | 1% | 139,656 | 19425 | 94 |
| collapsed_7d_organic | 6 | [TIGRINO](https://dexscreener.com/solana/91ryaCo5yGpYZM3bs6GUPs97VWJQj7RozBmqPULgpump) | nee | 1,282 | 42,618 | 3% | 215,919 | 24246 | 196 |
| collapsed_7d_organic | 7 | [$GULL](https://dexscreener.com/solana/XQvVFz2jrrqFvhdG4NRd2o9xCQci9Xn9nGYVumBpump) | nee | 20 | 40,947 | 0% | 6,349 | 2229 | 1 |
| collapsed_7d_organic | 8 | [fomopay](https://dexscreener.com/solana/BP4Wic5LNKsqpmiREW6uNVEC16juvFCSd4WzVBatpump) | nee | 510 | 40,884 | 1% | 111,690 | 21212 | 117 |
| collapsed_7d_organic | 9 | [POT](https://dexscreener.com/solana/4R2vepDVY7Gdcm2VKuAy6EbBfWsYSEsDJou1QGKuMfnf) | nee | 1,213 | 34,480 | 4% | 124,005 | 17432 | 190 |
| collapsed_7d_organic | 10 | [familiars](https://dexscreener.com/solana/2PENPmfgJfq6CG3k4byj4oWwHf8SerqakmYHMkUupump) | nee | 689 | 33,150 | 2% | 115,656 | 19734 | 123 |
| stayers_7d | 1 | [WSOS](https://dexscreener.com/solana/sEkKnqHR8KKrjMjFqurrzdyTn4hiivmM8JHKWmopump) | ja | 388,046 | 388,171 | 100% | 9,578 | 1808 | 2,609 |
| stayers_7d | 2 | [WSOS](https://dexscreener.com/solana/6dWQ7YC1k5sydiAij5bdc8F5jGjnE6TFN5GSoYvdpump) | ja | 336,876 | 336,966 | 100% | 6,477 | 145 | 2,427 |
| stayers_7d | 3 | [FAIR](https://dexscreener.com/solana/QbPr1ponxN4J7ozvkdh1dNzqc4zgdkE1RGygHeKpump) | ja | 312,884 | 312,987 | 100% | 12,803 | 2326 | 2,343 |
| stayers_7d | 4 | [fomo](https://dexscreener.com/solana/UNHwuKaSGKP7qmYMFS5aSJdqifHj6CiXWvtoeYNpump) | ja | 126,870 | 126,933 | 100% | 12,463 | 179 | 1,493 |
| stayers_7d | 5 | [PAID](https://dexscreener.com/solana/98kfF7rmsg1QDUEoCqNE7g7M1FdrTt92TEp2CLzypump) | nee | 115,640 | 354,971 | 33% | 1,284,084 | 88126 | 2,797 |
| stayers_7d | 6 | [JEANPHIL](https://dexscreener.com/solana/GTBxUiw6wJdmmkCGZgRHLyYxqu1vG4KtRpeox6yDpump) | nee | 66,427 | 109,954 | 60% | 422,187 | 52568 | 1,639 |
| stayers_7d | 7 | [MCAT](https://dexscreener.com/solana/7EA8EStQETtKkqFW8rqj77nfkiLEniVag8Y2jNUTpump) | ja | 65,881 | 79,229 | 83% | 6,386 | 2182 | 1,066 |
| stayers_7d | 8 | [USDF](https://dexscreener.com/solana/DRMnFyekQiCTMrajtgsTycqp4ie6r1tZnh6qpAGjpump) | nee | 41,153 | 60,265 | 68% | 12,557 | 3204 | 849 |
| stayers_7d | 9 | [WEPE](https://dexscreener.com/solana/EL6Urf21PoPesMrbYSRc1yqqHjhk6DGUR63VZDmmpump) | ja | 38,101 | 124,752 | 31% | 9,886 | 1601 | 809 |
| stayers_7d | 10 | [MINESTOCK](https://dexscreener.com/solana/9a8PquAAAoo51TABtasHSXMbm3CPSRnkxZfn6F8Dpump) | ja | 33,805 | 55,755 | 61% | 5,865 | 384 | 760 |
| stayers_7d_organic | 1 | [PAID](https://dexscreener.com/solana/98kfF7rmsg1QDUEoCqNE7g7M1FdrTt92TEp2CLzypump) | nee | 115,640 | 354,971 | 33% | 1,284,084 | 88126 | 2,797 |
| stayers_7d_organic | 2 | [JEANPHIL](https://dexscreener.com/solana/GTBxUiw6wJdmmkCGZgRHLyYxqu1vG4KtRpeox6yDpump) | nee | 66,427 | 109,954 | 60% | 422,187 | 52568 | 1,639 |
| stayers_7d_organic | 3 | [USDF](https://dexscreener.com/solana/DRMnFyekQiCTMrajtgsTycqp4ie6r1tZnh6qpAGjpump) | nee | 41,153 | 60,265 | 68% | 12,557 | 3204 | 849 |
| stayers_7d_organic | 4 | [SDOG](https://dexscreener.com/solana/yFcTuQJCMsd1Asya15GZ7h4KGdxsujRLcUiTWWXpump) | nee | 13,854 | 25,138 | 55% | 65,040 | 12241 | 574 |
| stayers_7d_organic | 5 | [Tilcayo](https://dexscreener.com/solana/AyYNfPtftg2zDP4ZbgcoQMggQtwLh4zpfVVmUJs2thto) | nee | 9,766 | 36,921 | 26% | 202,790 | 23162 | 595 |
| stayers_7d_organic | 6 | [FLEX](https://dexscreener.com/solana/fvHLJUwsynVHJrssbZ8MLNyku9jt2izUspbBD4Spump) | nee | 9,667 | 59,967 | 16% | 120,302 | 13503 | 503 |
| stayers_7d_organic | 7 | [HUHCAT](https://dexscreener.com/solana/A9AHYeqb7nQk7LZUraw7rBCzYRjy2DRvE6NqWfFHKRdH) | nee | 3,643 | 31,660 | 12% | 135,371 | 21828 | 326 |
| stayers_7d_organic | 8 | [DRMR](https://dexscreener.com/solana/7Xgc5RacHtGzfhw2HukUBk5V986yBbvshr7rrCoopump) | nee | 3,088 | 3,100 | 100% | 7,974 | 3593 | 227 |
| stayers_7d_organic | 9 | [parafactual](https://dexscreener.com/solana/CvUX4G2tzq7J3ECmNvWZLayZYV6KpgQp6SJ5K8Uvsu5g) | nee | 2,737 | 32,217 | 8% | 79,831 | 13711 | 256 |
| stayers_7d_organic | 10 | [UPTOBER](https://dexscreener.com/solana/8Vte25yt28L8BfLXm8DrzjSYEyaKX8yry6hRKRmX7FGd) | nee | 1,307 | 4,733 | 28% | 24,583 | 47407 | 156 |

## Organische stijgers tegenover organische dumpers in de eerste 15 minuten

| kenmerk op +15 min | stijgers (mediaan) | dumpers (mediaan) |
|---|---|---|
| min. tot graduatie | 7.46 (n=100) | 2.55 (n=44) |
| unieke kopers curve | 455.50 (n=100) | 266.50 (n=44) |
| aandeel kopers ≥ 5 SOL | 0.00 (n=100) | 0.01 (n=44) |
| netto instroom curve (SOL) | 85.01 (n=100) | 85.01 (n=44) |
| insider-aandeel | 0.04 (n=100) | 0.06 (n=44) |
| creator-verkopen | 0.00 (n=100) | 0.00 (n=44) |
| traders pool | 1688.00 (n=100) | 2355.50 (n=44) |
| organisch volume pool (SOL) | 2592.19 (n=100) | 5661.22 (n=44) |
| prijs t.o.v. open | 3.34 (n=100) | 4.37 (n=44) |
| split-buyer-events | 0.00 (n=100) | 0.00 (n=44) |
