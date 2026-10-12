# De Phantom-tokens van de eigenaar als casussen (2026-10-12)

Op verzoek van de eigenaar, via de coördinator: de zestien tokens die hij in Phantom zag (Pepper, DOPE, Normie, Incognito, Dacity, LARP, ROSE, QCAT, SPACEXSI, BYTE, baton, CYBERLEEK, SWOLF, CLAUDIA, ZCAT, HIGGS), teruggezocht in drie forward-stores: epoch 1052, 1053 en 1054, samen 8 oktober 06:28 tot 11 oktober 19:04 UTC. **Alleen illustratie.** Deze dagen liggen ná de hold-out en zijn nooit gebruikt om iets af te stellen; dat blijft zo. Code: `lab/wallets/phantom.py` (data per store) en `phantom_report.py` (samenvoeging en tekst); ruwe uitvoer en de volledige tijdlijnen: `data-old-faithful-one/lab/wallets/phantom-v1/`. Alleen adressen; labels zijn waarschijnlijkheden. Koers = reserve-prijs van de pool, dezelfde koers die een koersdienst toont. De DexScreener-links zijn om zelf te kijken, geen databron.

## In gewoon Nederlands

1. **Alle zestien namen zijn gevonden, maar bijna elke naam bestaat meerdere keren.** Per naam is de mint met de meeste handel gekozen; de andere mints met handel staan in de tabel "Alternatieven". Dezelfde naam wordt binnen een dag vaak opnieuw gelanceerd: SpaceXSI 21 mints in één epoch (vijf gegradueerd), CYBERLEEK 8 + 17, Pepper The Raccoon twee keer op dezelfde dag, allebei met een echte run. Een naam in Phantom zegt dus niet welke token je ziet; het adres wel.

2. **Zes van de zestien zijn create-slot-bundels** (SpaceXSI, CyberLeek, baton, SWOLF, CLAUDIA, HIGGS): één wallet koopt de hele curve (85 SOL) in dezelfde slot als de aanmaak, dezelfde seconde wordt geld in de pool gestort en de koers staat meteen op 12× tot 6.238× de openingsprijs. Daarna kopen 2.000 tot 3.200 gewone wallets, en wordt de voorraad die bij de maker lag verkocht: SpaceXSI (178 + 100 SOL in één minuut op +39 min, naar 0,04×), CyberLeek (negen wallets die nooit in de pool kochten verkopen na 12,6 uur ruim 4.000 SOL in dezelfde minuut, naar 0,05×), baton en Claudia (naar 0,04×). SWOLF en HIGGS waren aan het einde van de store nog niet leeggehaald (218× en 163×, bij HIGGS maar 24 minuten data). Bij CyberLeek stopten 2.180 wallets per saldo 6.670 SOL meer in dan ze eruit haalden; 987 wallets haalden 6.689 SOL meer eruit. Wie de maker is, zegt de data niet; wel dat de hele voorraad bij de maker lag en dat die aan de kopers is verkocht.

3. **Drie tokens vallen buiten ons universum: Normie, Incognito en Dacity gradueerden naar een pool met een andere quote-munt dan SOL** (52.000 tot 74.000 pool-trades, dus wél druk verhandeld). Onze store kent voor zo'n pool geen SOL-zijde en dus geen koers; F7, M1 en de watchlist kijken alleen naar SOL-pools. Dit is nieuw: pump.fun laat sinds kort andere quote-munten toe, en de eigenaar zag ze. Of dat universum de moeite is, is een aparte vraag voor de store (Lab Backtest); hier staat alleen hun curve.

4. **Vijf organische graduaties, en wat onze strategieën ermee zouden hebben gedaan** (geen replay, dus geen vulling, kosten of exit; alleen "zou de trigger zijn afgegaan en wat deed de koers daarna"):

| token | aanmaak (UTC) | graduatie na | kopers curve / traders dag | piek t.o.v. open | stand aan het eind | F7 | M1 (k = 2) | M1+ op +15 min |
|---|---|---|---|---|---|---|---|---|
| Pepper 1 (`HXHZT27p…`) | 10 okt 12:41 | 3 min | 328 / 7.915 | 14,2× op +53 min | 5,5× na 4,2 u (einde store); **0,20× na 24 u** (vervolgd in store 1054) | +9 min (pool) bij 4,6×: 10 min later 1,46× | +106 min bij 4,8×: na 1 u 0,87× | bij 6,8×: na 1 u 0,98× |
| Pepper 2 (`9g4Pib6A…`) | 10 okt 17:10 | 7 min | 660 / 8.064 | 18,3× | 6,9× na 24 u | +1 min (curve) bij 0,27×: na 2.400 slots 3,6× | +33 min bij 3,0×: na 1 u 0,50×, na 6 u 1,93× | bij 2,9×: na 24 u 2,34×, maar hype-veto |
| DOPE (`33jENUQn…`) | 11 okt 13:28 | 4 min | 349 / 17.419 | 44,3× | 33,9× na 5,5 u (einde store) | +3 min (curve): na 2.400 slots 27× | **+32 s bij 2,5×: na 1 u 6,9×, na 5,5 u 13,7×** | bij 9,8×: na 1 u 1,27×, na 5,5 u 3,5× |
| LARP (`E447xHwX…`) | 11 okt 15:03 | 13 min | 906 / 9.353 | 14,3× | 0,33× na 3,8 u (einde store) | +1 min (curve) | +46 min bij 8,6×: na 1 u 0,23× | bij 12,1×: na 1 u 0,24×, hype-veto |
| QCAT (`GmsAcJAF…`) | 9 okt 05:56 | 58 min | 993 / 2.061 | 2,7× | 0,64× na 7,8 u; **0,17× na 24 u** (store 1053) | geen | +39 min bij 2,0×: na 1 u 0,30× | geen trigger (1,14×), hype-veto |
| BYTE (QuByte, `QUALaRd6…`) | 10 okt 19:46 | 5 min | 705 / 2.880 | 2,5× | 0,21× na 23 u | +36 s (curve) | +28 min bij 2,0×: na 6 u 0,66× | geen trigger (0,57×) |

   Wat dit laat zien: van de zes organische runs was DOPE de enige waar M1 (verdubbeling plus instroom en kopers) op tijd was, omdat de verdubbeling daar al na 32 seconden kwam en de koers daarna nog 13× deed. Bij Pepper 1, LARP, QCAT en BYTE kwam de M1-trigger ná de piek en stond de koers een uur later op 0,23× tot 0,87× van de instap. Pepper 2 is het tussengeval: een uur na de M1-trigger 0,50× (een harde stop van −25 % had je eruit gezet), zes uur later 1,93×. **De hype zit in de eerste minuten; op onze beslismomenten was hij bij vijf van de zes al voorbij.** Dat is precies de vraag die de spec M1-early (beslismomenten +1, +2 en +5 min) na de hold-out in de replay gaat meten over 18 ontwikkeldagen, met echte vulling, kosten en vertraging, in plaats van uit deze zes voorbeelden.

5. **Correctie op wat eerder over Pepper is gezegd.** In het eerste bericht (store 1053 alleen) stond "einde 24 u 5,5×". Dat was de stand na 4,2 uur, waar die store ophoudt. In store 1054 gaat dezelfde pool door: 0,20× na 24 uur en 0,17× na 37 uur. Ook Pepper 1 is dus binnen een dag ingestort; alleen Pepper 2 (6,9× na een volle dag) en DOPE (34× na 5,5 uur, verder onbekend) hielden hun koers in de data die we hebben. Vergelijk de "nog 1,5×, daarna mediaan 0,03×" van de 24 vroege instappers in de meting van 12 oktober.

6. **De concurrentie op +1 minuut is één bot.** Wallet `ARu4n5mF…` was de grootste koper van Pepper 1 (616 SOL op +1 min), DOPE (2.362 SOL op +15 s), LARP (700 SOL op +2 s) en QCAT (83 SOL op +3 s), en handelde in epoch 1052 in 4.863 pools en in 1053 in 4.157 pools, met per epoch 4.400 tot 5.200 SOL meer verkocht dan gekocht. Een sniper die overal als eerste zit en per saldo wint. Wie op +1 minuut wil instappen, koopt ná deze bot en verkoopt vóór of ná hem; daarom staat in de spec M1-early d = 1, 2 en 3 naast elkaar.

7. **Twee curve-tokens die nooit gradueerden** zagen er in hun eerste minuten als runners uit: MissRose (944 kopers, 8,9× op de curve, F7-trigger op +18 min, terug naar 1,9× en 13 SOL reserves) en ZCAT (284 kopers, 3,2×, terug naar 0,85×). Op de curve is 8,9× met 944 kopers geen garantie dat de pool er ooit komt.

Beperkingen: drie stores van elk één epoch; een token dat in de ene epoch is aangemaakt en in de volgende gradueerde, mist in de tweede store zijn curve en vlaggen (de keuze op "meeste handel" pakt de store met de pool, Pepper 1 is daarom met de hand vervolgd in 1054). Vensters die over het einde van een store heen lopen staan als "data tot +X u". F7 en M1 zijn hier de triggerdefinities van de engine, zonder replay. Transfers zijn onzichtbaar: een insider-aandeel boven 100 % betekent dat insiders meer verkochten dan ze zichtbaar kochten.

## Volledige uitvoer (gegenereerd door `phantom_report.py`)

Alleen ter illustratie: forward-data ná de hold-out (store 1052: 2026-10-08 06:28 UTC – 2026-10-09 14:40 UTC (slots 454,464,000–454,896,000, commit 24d622e); store 1053: 2026-10-09 14:40 UTC – 2026-10-10 16:52 UTC (slots 454,896,000–455,328,000, commit 24d622e); store 1054: 2026-10-10 16:52 UTC – 2026-10-11 19:04 UTC (slots 455,328,000–455,760,000, commit 24d622e)). Deze dagen zijn nooit gebruikt om iets af te stellen en doen dat ook hierna niet. Per naam is de mint met de meeste handel gekozen; dezelfde namen worden vaker gelanceerd (copycats), de andere mints staan in de tabel met alternatieven. Alleen adressen; labels zijn waarschijnlijkheden. Koers = reserve-prijs van de pool (wat een koersdienst toont). Code: `lab/wallets/phantom.py`, `phantom_report.py`.

### Overzicht

| token | naam | aangemaakt (UTC) | graduatie | dev-bundel | curve-kopers | traders 24 u | volume 24 u (SOL) | piek t.o.v. open | eind 24 u | insider-aandeel +15 min | F7 | M1 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [Pepper](https://dexscreener.com/solana/9g4Pib6AM3oVdP9YA7ZcssAXAuhoUzkjR8w27doYpump) | Pepper The Raccoon | 2026-10-10 17:10 UTC | na 7 min | nee | 660 | 8.064 | 24.910 | 18.3× | 6.88× | 12 % | F7 op +1 min (curve) | M1 op +33 min |
| [DOPE](https://dexscreener.com/solana/33jENUQnjoitkMFpvNJcSHyLAHFWJuHwT6E3E8CNpump) | Doge Pepe | 2026-10-11 13:28 UTC | na 4 min (data tot +5.5 u) | nee | 349 | 17.419 | 70.818 | 44.3× | 33.9× | 11 % | F7 op +3 min (curve) | M1 op +32 s |
| [Normie](https://dexscreener.com/solana/DzMwArmdzPqcqgvyqAmTxy79XdA9NwK9mMteqWLJpump) | The Normie | 2026-10-11 09:32 UTC | na 2 min, pool met andere quote-munt (52.310 trades, geen SOL-koers) | nee | 299 | 0 | – | – | – | – | geen F7 | M1 n.v.t. (geen normale SOL-pool) |
| [Incognito](https://dexscreener.com/solana/BBZ3sgY66QVsxcXXBVpuQfDzH2b5Ub8qmfKNQJjBpump) | Incognito69420 | 2026-10-11 05:41 UTC | na 3 min, pool met andere quote-munt (74.348 trades, geen SOL-koers) | nee | 171 | 0 | – | – | – | – | geen F7 | M1 n.v.t. (geen normale SOL-pool) |
| [Dacity](https://dexscreener.com/solana/nxPqZ4F7ux8Vph5NFu25rEP6Kx9Gr1U6Fmj36Rjpump) | Dacity Coin | 2026-10-11 11:16 UTC | na 25 s, pool met andere quote-munt (52.650 trades, geen SOL-koers) | nee | 94 | 0 | – | – | – | – | geen F7 | M1 n.v.t. (geen normale SOL-pool) |
| [LARP](https://dexscreener.com/solana/E447xHwXKMLUhqriexGwmjmMPn7YSsvyRKv9L2Tdpump) | LARP | 2026-10-11 15:03 UTC | na 13 min (data tot +3.8 u) | nee | 906 | 9.353 | 26.651 | 14.3× | 0.33× | 2 % | F7 op +1 min (curve) | M1 op +46 min |
| [ROSE](https://dexscreener.com/solana/7b6dX3nE25xrfNViEKZHL9wVJFcBxQYuWPpmzHUxx28H) | MissRose | 2026-10-10 07:30 UTC | niet (curve, laatste stand 13 SOL) (data tot +9.4 u) | nee | 944 | 964 | 1.859 | 8.92× | 1.87× | – | F7 op +18 min (curve) | M1 n.v.t. (niet gegradueerd) |
| [QCAT](https://dexscreener.com/solana/GmsAcJAFKEtAXDoRUroaQP5GBdmPcGwA2sRdC9Mbpump) | Quantum Cat | 2026-10-09 05:56 UTC | na 58 min (data tot +7.8 u) | nee | 993 | 2.061 | 3.384 | 2.69× | 0.64× | 0 % | geen F7 | M1 op +39 min |
| [SpaceXSI](https://dexscreener.com/solana/kS5HANa73V6Rb5hWikpQqgJYYFAJSCto5saBP45pump) | SpaceXSI | 2026-10-09 10:45 UTC | in de create-slot (data tot +3.9 u) | ja | 1 | 2.583 | 854 | 20.6× | 0.04× | 85 % | F7 op +39 min (pool) | M1 n.v.t. (graduatie in de create-slot (buiten het M1-universum)) |
| [BYTE](https://dexscreener.com/solana/QUALaRd6ZvZkt9NqJjVVN3MP23RtFNwDbJc3VDHcHwN) | QuByte | 2026-10-10 19:46 UTC | na 5 min (data tot +23.2 u) | nee | 705 | 2.880 | 5.393 | 2.49× | 0.21× | 16 % | F7 op +36 s (curve) | M1 op +28 min |
| [baton](https://dexscreener.com/solana/s49Ssm3Zv9NkAzoVc9sV9p4TMWCESNVsfbVDsCypump) | baton | 2026-10-11 05:51 UTC | in de create-slot (data tot +13.2 u) | ja | 1 | 2.565 | 612 | 11.9× | 0.04× | > 100 % (verkopen boven de zichtbare kopen: transfers) | F7 op +7 min (pool) | M1 n.v.t. (graduatie in de create-slot (buiten het M1-universum)) |
| [CYBERLEEK](https://dexscreener.com/solana/Sd8R2j4kf7eK2ABSFEWyhBWfmJ6PoKu6k43jjRLpump) | CyberLeek | 2026-10-08 12:45 UTC | in de create-slot | ja | 1 | 3.167 | 15.815 | 6.238× | 0.05× | 80 % | geen F7 | M1 n.v.t. (graduatie in de create-slot (buiten het M1-universum)) |
| [SWOLF](https://dexscreener.com/solana/8sx4iwmwGfJsLpRNRhbJBJr5yfdAHZkKQ75jU1Wopump) | Starknet Wolf | 2026-10-10 20:43 UTC | in de create-slot (data tot +22.4 u) | ja | 1 | 2.070 | 1.831 | 218× | 218× | 81 % | geen F7 | M1 n.v.t. (graduatie in de create-slot (buiten het M1-universum)) |
| [CLAUDIA](https://dexscreener.com/solana/D8AWqghypBX9XxFAChDvwi73qXt3qsUd12AoHEE9NUS6) | Claudia the AI | 2026-10-11 09:01 UTC | in de create-slot (data tot +10.0 u) | ja | 1 | 2.557 | 1.324 | 42.8× | 0.04× | 84 % | F7 op +48 min (pool) | M1 n.v.t. (graduatie in de create-slot (buiten het M1-universum)) |
| [ZCAT](https://dexscreener.com/solana/7DxkGn2NkrtGNhmR9HHp7RTEceejwtT3f8GaSgWBpump) | ZCAT | 2026-10-10 19:48 UTC | niet (curve, laatste stand 0 SOL) (data tot +23.3 u) | nee | 284 | 302 | 2 | 3.24× | 0.85× | – | geen F7 | M1 n.v.t. (niet gegradueerd) |
| [HIGGS](https://dexscreener.com/solana/EgeMmDHNGekF2NsYfqNLmgbYuNt6aX66XNTcZPbopump) | Higgspad | 2026-10-11 18:40 UTC | in de create-slot (data tot +24 min) | ja | 1 | 53 | 1.026 | 163× | 163× | 81 % | geen F7 | M1 n.v.t. (graduatie in de create-slot (buiten het M1-universum)) |

### Alternatieven met dezelfde naam

| token | mint | naam | aangemaakt (slot) | gegradueerd | pool-trades | curve-trades | store | gekozen |
|---|---|---|---|---|---|---|---|---|
| pepper | [9g4Pib](https://dexscreener.com/solana/9g4Pib6AM3oVdP9YA7ZcssAXAuhoUzkjR8w27doYpump) `9g4Pib6A…` | Pepper The Raccoon | 455332920 | ja | 43.846 | 1.811 | 1054 | **ja** |
| pepper | [HXHZT2](https://dexscreener.com/solana/HXHZT27p7C9t62QyDgPoEHiRFsLoqUTYnJAmZ16tpump) `HXHZT27p…` | Pepper The Raccoon | 455259276 | ja | 44.321 | 681 | 1053 |  |
| pepper | [HKmoMg](https://dexscreener.com/solana/HKmoMgNfbdNLp1wM6HxhSagty7Parnkdi1NsMoZrpump) `HKmoMgNf…` | Pepper The Raccoon | 455261240 | ja | 3.205 | 2.866 | 1053 |  |
| pepper | [FXPypu](https://dexscreener.com/solana/FXPypupNwkUaY5XZp5jrZ2PXNJRtKMpYsg8diJdZpump) `FXPypupN…` | Pepper The Raccoon  | 455331937 | ja | 5.808 | 218 | 1054 |  |
| pepper | [5bcMnx](https://dexscreener.com/solana/5bcMnxY6djMES8oTTtcvEuzdSE2Rnk5DsVyCf8yPpump) `5bcMnxY6…` | Pepper The Raccoon | 455264936 | nee | 0 | 3.076 | 1053 |  |
| pepper | [8suiBn](https://dexscreener.com/solana/8suiBnjZ8ecHkvAPEoCjMbzJn3pm13PndriBxRUgpaid) `8suiBnjZ…` | Pepper The Raccoon | 455273222 | nee | 0 | 1.300 | 1053 |  |
| dope | [33jENU](https://dexscreener.com/solana/33jENUQnjoitkMFpvNJcSHyLAHFWJuHwT6E3E8CNpump) `33jENUQn…` | Doge Pepe | 455667924 | ja | 104.025 | 879 | 1054 | **ja** |
| dope | [FiLRrZ](https://dexscreener.com/solana/FiLRrZ7Bt2HHGfs83RFJdGmp3mLceycq3zkDN6Apump) `FiLRrZ7B…` | Doge Pepe | 455712911 | ja | 37.264 | 283 | 1054 |  |
| dope | [7dvY8W](https://dexscreener.com/solana/7dvY8Ww9hYUeM2zN6vLXrn2JGwymjfbupQu4yz76pump) `7dvY8Ww9…` | Doge Pepe | 455670665 | ja | 15.342 | 950 | 1054 |  |
| dope | [BNRXJL](https://dexscreener.com/solana/BNRXJLnqCDKrYok4g36FEDr1j6u5Yf2xHkL3ZiS7xSuF) `BNRXJLnq…` | Doge Pepe | 455672400 | ja | 5.679 | 700 | 1054 |  |
| dope | [GAMexx](https://dexscreener.com/solana/GAMexxpyaGGFQmQLB6SPSWe9ynTE1cNCDM7ULXbspump) `GAMexxpy…` | Doge Pepe | 455675186 | ja | 2.076 | 585 | 1054 |  |
| dope | [Eo4qpY](https://dexscreener.com/solana/Eo4qpYxDGmy2fwXXWUMUiTr3scfnRLcVFpPfE1bMpump) `Eo4qpYxD…` | Doge Pepe | 455702563 | nee | 0 | 2.460 | 1054 |  |
| normie | [DzMwAr](https://dexscreener.com/solana/DzMwArmdzPqcqgvyqAmTxy79XdA9NwK9mMteqWLJpump) `DzMwArmd…` | The Normie | 455602958 | ja | 52.310 | 745 | 1054 | **ja** |
| normie | [EdfxjD](https://dexscreener.com/solana/EdfxjDnHYZnRXstcSZCoJhzf3JE6FcXyo41287fvpump) `EdfxjDnH…` | Normie | 455666948 | nee | 0 | 4.307 | 1054 |  |
| normie | [3NVgJd](https://dexscreener.com/solana/3NVgJd5g3BPi44pjYHLHaF9EGN9vMCRrFFXbu1LMpump) `3NVgJd5g…` | Normie | 454501543 | ja | 1.532 | 7 | 1052 |  |
| normie | [DseYzK](https://dexscreener.com/solana/DseYzKv13cEbWAs3muneToqzykBQvf3KQ1SWiJzSpump) `DseYzKv1…` | The Normie | 455709947 | nee | 0 | 1.443 | 1054 |  |
| normie | [DiV1Jf](https://dexscreener.com/solana/DiV1Jfg8fFmHR4hhci1MvqqBQRihWz24cHJ4eiAGpump) `DiV1Jfg8…` | The Normie | 455603129 | nee | 0 | 901 | 1054 |  |
| normie | [DastBv](https://dexscreener.com/solana/DastBvd4fBKxBixai8KmgUsdyv5PFGM2FqFEkoZKpump) `DastBvd4…` | normie | 454640090 | nee | 0 | 722 | 1052 |  |
| incognito | [BBZ3sg](https://dexscreener.com/solana/BBZ3sgY66QVsxcXXBVpuQfDzH2b5Ub8qmfKNQJjBpump) `BBZ3sgY6…` | Incognito69420 | 455539217 | ja | 74.348 | 450 | 1054 | **ja** |
| incognito | [4aRwxC](https://dexscreener.com/solana/4aRwxCWGcpiyHJSSwAiV4kUL512vdRxS7emJEdgapump) `4aRwxCWG…` | incognito69420 | 455540685 | ja | 2.897 | 2.018 | 1054 |  |
| incognito | [Bmi7gm](https://dexscreener.com/solana/Bmi7gmSQXTy89odvXmH8tTVDEobbgeaSpgFdfGCSBmkn) `Bmi7gmSQ…` | Incognito69420 | 455554422 | ja | 3.484 | 320 | 1054 |  |
| incognito | [4WM1Jg](https://dexscreener.com/solana/4WM1JgcNwpRjrvVChJhokfPyedy7XzE63H1q7iQ3pump) `4WM1JgcN…` | Incognito69420 | 455540108 | ja | 2.243 | 411 | 1054 |  |
| incognito | [6W7uJz](https://dexscreener.com/solana/6W7uJzYjZHh6rhYgso5rAWTTteBx3VV963dwMJyYpump) `6W7uJzYj…` | Incognito69420 | 455539005 | nee | 0 | 2.501 | 1054 |  |
| incognito | [9PphPH](https://dexscreener.com/solana/9PphPHueF3eq7sAFBjS2wmPDNExWnkV6qV8EosfkNxFJ) `9PphPHue…` | incognito69420 | 455540637 | nee | 0 | 973 | 1054 |  |
| dacity | [nxPqZ4](https://dexscreener.com/solana/nxPqZ4F7ux8Vph5NFu25rEP6Kx9Gr1U6Fmj36Rjpump) `nxPqZ4F7…` | Dacity Coin | 455631518 | ja | 52.650 | 222 | 1054 | **ja** |
| dacity | [4z3KDe](https://dexscreener.com/solana/4z3KDegrsGr7BDMAyEh2KHs9YKxMda2umAeNNo2Mxieh) `4z3KDegr…` | Dacity Coin | 455632508 | ja | 4.021 | 172 | 1054 |  |
| dacity | [AZ46rs](https://dexscreener.com/solana/AZ46rsZqLVEoJKC3ijKJqzqaGZVFtK3pzdh6Xniwpump) `AZ46rsZq…` | Dacity coin | 455629038 | nee | 0 | 928 | 1054 |  |
| dacity | [HN6jD2](https://dexscreener.com/solana/HN6jD2BUo54xPRZPRjVkRx23D7YCvLrugPkQKjK7zZNp) `HN6jD2BU…` | AudaCity Coin | 455633107 | ja | 701 | 81 | 1054 |  |
| dacity | [EikXrQ](https://dexscreener.com/solana/EikXrQQJBSs5SR5Fm3vqfqNFYdKgyyWHaAUAd8qfpump) `EikXrQQJ…` | Dacity Coin | 455632396 | nee | 0 | 682 | 1054 |  |
| dacity | [Gr8tMC](https://dexscreener.com/solana/Gr8tMCGZFksM9ntigms2xjdSyMbSmmvDPbLNDcZQpump) `Gr8tMCGZ…` | Dacity Coin | 455631415 | nee | 0 | 548 | 1054 |  |
| larp | [E447xH](https://dexscreener.com/solana/E447xHwXKMLUhqriexGwmjmMPn7YSsvyRKv9L2Tdpump) `E447xHwX…` | LARP | 455693999 | ja | 48.356 | 2.652 | 1054 | **ja** |
| larp | [DdEmao](https://dexscreener.com/solana/DdEmaohujxvQAe8BBmSmJsNCMubTVrhEUK2umNLBpump) `DdEmaohu…` | LARP | 455714385 | ja | 3.507 | 1 | 1054 |  |
| larp | [CCudyi](https://dexscreener.com/solana/CCudyizVXhxazCv2quVgtAKLzN5ZV179T9JnhuVG7PW2) `CCudyizV…` | The Larp Fail Son | 455746421 | nee | 0 | 1.981 | 1054 |  |
| larp | [3aBccV](https://dexscreener.com/solana/3aBccVeYQrDE3c1CE15YAWK6TWr2vi66RV45MRSJpump) `3aBccVeY…` | Live Action Roleplay | 455700643 | nee | 0 | 1.543 | 1054 |  |
| larp | [8hMgPv](https://dexscreener.com/solana/8hMgPvypV1chPahm9iK62KoamDzniqjjVo4w8ffypump) `8hMgPvyp…` | The Larp Fail Son | 455746573 | nee | 0 | 593 | 1054 |  |
| larp | [3jeeCe](https://dexscreener.com/solana/3jeeCeEH84P2VWZrQsEDb6W6bxPU6VGxYTmWRGvGpump) `3jeeCeEH…` | Tekorlarp.fun | 455096167 | nee | 0 | 531 | 1053 |  |
| rose | [7b6dX3](https://dexscreener.com/solana/7b6dX3nE25xrfNViEKZHL9wVJFcBxQYuWPpmzHUxx28H) `7b6dX3nE…` | MissRose | 455173527 | nee | 0 | 4.049 | 1053 | **ja** |
| rose | [4YzBUJ](https://dexscreener.com/solana/4YzBUJWa3CyAYH3NKdBx5FeHFiHSrj5FtxvYD5FoSuLx) `4YzBUJWa…` | TikTok Official Coin | 454638818 | nee | 0 | 2.593 | 1052 |  |
| rose | [EXSRtz](https://dexscreener.com/solana/EXSRtzHJARfb8FL5bNABuEcXzEx3uJMQgU6qxAa9byCk) `EXSRtzHJ…` | Wind Rose | 455572863 | nee | 0 | 1.363 | 1054 |  |
| rose | [EQdr8d](https://dexscreener.com/solana/EQdr8dxsApNVrJ3UC1fPsN5popF7W9CMiP7XPf1KaBc9) `EQdr8dxs…` | ROSE | 455179355 | nee | 0 | 439 | 1053 |  |
| rose | [ATqXek](https://dexscreener.com/solana/ATqXek4z3CBQ4kYvX9H4e3TwL72Q8hEGpZ8tYWGrpump) `ATqXek4z…` | Wind Rose | 455582229 | nee | 0 | 363 | 1054 |  |
| rose | [GJaRJ2](https://dexscreener.com/solana/GJaRJ2eY1CX99iSVaXNwyTcfyJoD46iZ4Fiz6Vd8pump) `GJaRJ2eY…` | Rose from Black Pink | 455235455 | nee | 0 | 319 | 1053 |  |
| qcat | [GmsAcJ](https://dexscreener.com/solana/GmsAcJAFKEtAXDoRUroaQP5GBdmPcGwA2sRdC9Mbpump) `GmsAcJAF…` | Quantum Cat | 454778509 | ja | 7.778 | 4.549 | 1052 | **ja** |
| qcat | [7fYXa9](https://dexscreener.com/solana/7fYXa9CxYRrSWi36EqS8vxwKQVZSCN3sihpSdAyeSBcF) `7fYXa9Cx…` | Quantum Cat | 454602458 | nee | 0 | 5.531 | 1052 |  |
| qcat | [Bk3PZU](https://dexscreener.com/solana/Bk3PZUcQnzvdZGcfKddsCdsvBD5uVwLZ4KDhVtNviZjx) `Bk3PZUcQ…` | Quantum Cat | 454772908 | ja | 2.115 | 1.459 | 1052 |  |
| qcat | [A9waYL](https://dexscreener.com/solana/A9waYLpUfQrRVMRUu6UYpPzzMPdMXGj52wPJkfenpump) `A9waYLpU…` | Quantum Cat | 454907852 | nee | 0 | 2.182 | 1053 |  |
| qcat | [3UKdbH](https://dexscreener.com/solana/3UKdbHA6PDuxXa2oPTKoEtLagPR76JWxNCDBBV7zqbit) `3UKdbHA6…` | Schrödinger's Cat | 454974490 | nee | 0 | 1.885 | 1053 |  |
| qcat | [2G6ZBc](https://dexscreener.com/solana/2G6ZBcMWFdEh6YSXX3NipKGn5PuZ3kgA98SeH8s2fyea) `2G6ZBcMW…` | Quantum Cat | 454761336 | nee | 0 | 1.455 | 1052 |  |
| spacexsi | [kS5HAN](https://dexscreener.com/solana/kS5HANa73V6Rb5hWikpQqgJYYFAJSCto5saBP45pump) `kS5HANa7…` | SpaceXSI | 454843368 | ja | 37.443 | 1 | 1052 | **ja** |
| spacexsi | [FvNJ6A](https://dexscreener.com/solana/FvNJ6A1Y1UVrxETHLQD2Dx3ucK6RXHpFj73EPqurpump) `FvNJ6A1Y…` | SpaceXSI | 455308017 | ja | 35.514 | 1 | 1053 |  |
| spacexsi | [5htFdA](https://dexscreener.com/solana/5htFdAP5fBqMHqiRrqmxsrrwVXKJcwYahMvNrud6pump) `5htFdAP5…` | SpaceXSI | 454602288 | ja | 31.323 | 1 | 1052 |  |
| spacexsi | [W6mmGA](https://dexscreener.com/solana/W6mmGAheMDqqZ2uyvZwBtHghJ3sc7wZceUEB32fpump) `W6mmGAhe…` | SpaceXSI | 454913360 | ja | 13.314 | 11 | 1053 |  |
| spacexsi | [ATTxVB](https://dexscreener.com/solana/ATTxVBCkt774zSBSzPJp9BwuXmx8qeRGtQB5zLNpump) `ATTxVBCk…` | SpaceXSI | 454887036 | ja | 13.163 | 1 | 1052 |  |
| spacexsi | [wtzzeh](https://dexscreener.com/solana/wtzzehp9R75v8mdCXztNQzXw7izcEWkhXVEt9UUpump) `wtzzehp9…` | SpaceXSI | 455531205 | ja | 11.784 | 1 | 1054 |  |
| byte | [QUALaR](https://dexscreener.com/solana/QUALaRd6ZvZkt9NqJjVVN3MP23RtFNwDbJc3VDHcHwN) `QUALaRd6…` | QuByte | 455375666 | ja | 14.172 | 1.941 | 1054 | **ja** |
| byte | [Enjah2](https://dexscreener.com/solana/Enjah2hhRoqMiwEQChjAsaUfuii23tWi7S6Ppsvxpump) `Enjah2hh…` | Byte | 455528219 | nee | 0 | 325 | 1054 |  |
| byte | [EeQsms](https://dexscreener.com/solana/EeQsmsKCb35GmEbUtJtAXB9tSbwK6MkSgq2Cr6Vapump) `EeQsmsKC…` | BYTE | 455551718 | nee | 0 | 157 | 1054 |  |
| byte | [9SWTCh](https://dexscreener.com/solana/9SWTChXYDiTtGPrrp1HHJEQb5Ca7DmSwVVc4sfyTpump) `9SWTChXY…` | BYTEDANCE | 454647523 | nee | 0 | 32 | 1052 |  |
| byte | [3vj1H7](https://dexscreener.com/solana/3vj1H7g6ocQytrhVh2sohsj77FLRQaDsamj2yscspump) `3vj1H7g6…` | Byte | 455019272 | nee | 0 | 29 | 1053 |  |
| byte | [DGAnRg](https://dexscreener.com/solana/DGAnRgDi1wY8RdYevK8xyBBishgQy52iSQfCDAdnpump) `DGAnRgDi…` | Bytecade | 455354999 | nee | 0 | 24 | 1054 |  |
| baton | [s49Ssm](https://dexscreener.com/solana/s49Ssm3Zv9NkAzoVc9sV9p4TMWCESNVsfbVDsCypump) `s49Ssm3Z…` | baton | 455541920 | ja | 11.692 | 1 | 1054 | **ja** |
| baton | [FiFbHn](https://dexscreener.com/solana/FiFbHnJCVS2jjCCudJcbH5jKdCziqKpvue4G1g6fpump) `FiFbHnJC…` | baton | 454961628 | nee | 0 | 1.758 | 1053 |  |
| baton | [4BLrYy](https://dexscreener.com/solana/4BLrYyZVzs5yiiJnSUiMwFaRq73jDyHitS6Nudjmpump) `4BLrYyZV…` | BATON | 454962076 | nee | 0 | 625 | 1053 |  |
| baton | [Dxgcmz](https://dexscreener.com/solana/DxgcmzZb2fKB3FLmFn1ooQouYySe2NAhe4F2kzvHpump) `DxgcmzZb…` | Baton API | 454641101 | nee | 0 | 436 | 1052 |  |
| baton | [AEDgYC](https://dexscreener.com/solana/AEDgYCs799Ra2kHxSyth4seUqExwP78yXqbNUDFbpump) `AEDgYCs7…` | Baton Royale | 455406576 | nee | 0 | 434 | 1054 |  |
| baton | [4EFH6G](https://dexscreener.com/solana/4EFH6GK6BYdvLrfHCTDCmSHfvrWjuCCVT4FGsX5H8uQ7) `4EFH6GK6…` | BATON | 454650409 | nee | 0 | 418 | 1052 |  |
| cyberleek | [Sd8R2j](https://dexscreener.com/solana/Sd8R2j4kf7eK2ABSFEWyhBWfmJ6PoKu6k43jjRLpump) `Sd8R2j4k…` | CyberLeek | 454548669 | ja | 49.141 | 1 | 1052 | **ja** |
| cyberleek | [W1cZbM](https://dexscreener.com/solana/W1cZbMf3b9vvgSM9qP5irzZdCvsL3MmngVpRVekpump) `W1cZbMf3…` | CYBERLEEK | 455594536 | ja | 15.637 | 1 | 1054 |  |
| cyberleek | [ABAUBZ](https://dexscreener.com/solana/ABAUBZtoJCNLSXmZeZ3gMdcgxvbQ5qWGz5qW8fcCpump) `ABAUBZto…` | Pedro Santos | 455729360 | ja | 8.020 | 756 | 1054 |  |
| cyberleek | [6zGNMJ](https://dexscreener.com/solana/6zGNMJemLTGCHm3exz8ns5k1pq9XeKxrBc19Y5pqpump) `6zGNMJem…` | CYBERLEEK | 455295043 | ja | 5.394 | 1 | 1053 |  |
| cyberleek | [RckEna](https://dexscreener.com/solana/RckEnasgxygFWgvdYCjuYCyxnjFzAdL3HzFEdNFpump) `RckEnasg…` | CyberLeek | 454543676 | ja | 4.519 | 1 | 1052 |  |
| cyberleek | [WJa1Zt](https://dexscreener.com/solana/WJa1Ztfy9BqgwNmWSDDCQVX6xseUJ8sHtSYPjx3pump) `WJa1Ztfy…` | CyberLeek | 455307798 | ja | 4.304 | 1 | 1053 |  |
| swolf | [8sx4iw](https://dexscreener.com/solana/8sx4iwmwGfJsLpRNRhbJBJr5yfdAHZkKQ75jU1Wopump) `8sx4iwmw…` | Starknet Wolf | 455391285 | ja | 71.986 | 1 | 1054 | **ja** |
| swolf | [J1A6zY](https://dexscreener.com/solana/J1A6zYNAhxKNbojJyt7Wvyp7UCyRycqcyYi6LMSjpump) `J1A6zYNA…` | Starknet Wolf | 454819324 | nee | 0 | 702 | 1052 |  |
| swolf | [Cfos7d](https://dexscreener.com/solana/Cfos7diZP3WKDL35Wyxm2232mDAVGxmVtEeWHKMBpump) `Cfos7diZ…` | Starknet Wolf | 455143435 | nee | 0 | 152 | 1053 |  |
| swolf | [Bfo3YG](https://dexscreener.com/solana/Bfo3YGtdnCQ1hErZwMqkMF8VdMvZd258FLtDgWfw4EiL) `Bfo3YGtd…` | Starknet Wolf | 454939574 | nee | 0 | 107 | 1053 |  |
| swolf | [AvgCs3](https://dexscreener.com/solana/AvgCs3Cw66i1zkchcy9TGsv9XtGsTpTuN1XxJLwpump) `AvgCs3Cw…` | starknet wolf | 455030996 | nee | 0 | 29 | 1053 |  |
| swolf | [Ce9RLr](https://dexscreener.com/solana/Ce9RLrQXJMo6hdnKZxAZS5fXrLm4RmhdQhWQMD8Ypump) `Ce9RLrQX…` | SWOLF NET | 454842990 | nee | 0 | 28 | 1052 |  |
| claudia | [D8AWqg](https://dexscreener.com/solana/D8AWqghypBX9XxFAChDvwi73qXt3qsUd12AoHEE9NUS6) `D8AWqghy…` | Claudia the AI | 455594440 | ja | 43.589 | 1 | 1054 | **ja** |
| claudia | [xYiPKY](https://dexscreener.com/solana/xYiPKYKBGyr66pGE5RHceKpuWC9WKMf2fiDPwGapump) `xYiPKYKB…` | Claudia | 455448054 | ja | 4.062 | 1 | 1054 |  |
| claudia | [E8dV9R](https://dexscreener.com/solana/E8dV9RDALcxrtXbeCNFRwcMmJ4tnVFdkDukRpuEpump) `E8dV9RDA…` | sxsi.com - Claudia  | 455072776 | nee | 0 | 738 | 1053 |  |
| claudia | [BQbz4R](https://dexscreener.com/solana/BQbz4RAADvAVBxNcZLi9WKhTc6CgJTDGbZpyEyxipump) `BQbz4RAA…` | Airplane Mode | 455710061 | nee | 0 | 433 | 1054 |  |
| claudia | [GXquks](https://dexscreener.com/solana/GXquksFSLPLhX64FwFdG2xRE8V3DUxdTk13TNrQdpump) `GXquksFS…` | Claudia Agent Files | 455156059 | nee | 0 | 324 | 1053 |  |
| claudia | [8WoHHt](https://dexscreener.com/solana/8WoHHtdYRox7tXrqEUBoJ4YmN53YoBHmR6UJhN3opump) `8WoHHtdY…` | CLAUDIA | 455475230 | nee | 0 | 257 | 1054 |  |
| zcat | [7DxkGn](https://dexscreener.com/solana/7DxkGn2NkrtGNhmR9HHp7RTEceejwtT3f8GaSgWBpump) `7DxkGn2N…` | ZCAT | 455376016 | nee | 0 | 934 | 1054 | **ja** |
| zcat | [3uAUAw](https://dexscreener.com/solana/3uAUAwaD9a777gcetEsdnz8SS7kpcM6hgFjrou6Spump) `3uAUAwaD…` | Zombie Cat | 455459744 | nee | 0 | 544 | 1054 |  |
| zcat | [6dD75W](https://dexscreener.com/solana/6dD75WnQonMNAypo3cJuvynfbAApP3FaYBc7ZYsDpump) `6dD75WnQ…` | Love of the Dead Zombie Cat | 454495846 | nee | 0 | 26 | 1052 |  |
| zcat | [3mVeNg](https://dexscreener.com/solana/3mVeNgiNv189j48WustBkQ5RYL6RHPE5NYXh3zFVe3Wu) `3mVeNgiN…` | Anonymous Cat | 454745032 | nee | 0 | 20 | 1052 |  |
| zcat | [Esbym9](https://dexscreener.com/solana/Esbym94SzZaab6PCx6RTfFBWZY6Af2J6NgeTh53rbuQP) `Esbym94S…` | Anonymous Cat | 454717097 | nee | 0 | 12 | 1052 |  |
| zcat | [7VJSP5](https://dexscreener.com/solana/7VJSP5orQRcgKtLLk4pipuz6sXke7xq9ZFrosYEKshdS) `7VJSP5or…` | Anonymous Cat | 454716089 | nee | 0 | 10 | 1052 |  |
| higgs | [EgeMmD](https://dexscreener.com/solana/EgeMmDHNGekF2NsYfqNLmgbYuNt6aX66XNTcZPbopump) `EgeMmDHN…` | Higgspad | 455753318 | ja | 692 | 1 | 1054 | **ja** |
| higgs | [FMpi1E](https://dexscreener.com/solana/FMpi1EQ2Z8LLFLq4Vhis5iE6ciKwzRfSmmZNDLrPvEeP) `FMpi1EQ2…` | Higgspad | 455053863 | nee | 0 | 44 | 1053 |  |
| higgs | [7qmUpv](https://dexscreener.com/solana/7qmUpvZJT82mLSzbHxFFaxkQAoTN5xjbFj4mfk2k5ZPA) `7qmUpvZJ…` | higgscoin | 455191318 | nee | 0 | 17 | 1053 |  |
| higgs | [FhBTUA](https://dexscreener.com/solana/FhBTUAnnDQiuUDajB8dLMfnfaeRig5zQoiRLiDSHBp7X) `FhBTUAnn…` | Higgspad | 454702128 | nee | 0 | 14 | 1052 |  |
| higgs | [53YxS9](https://dexscreener.com/solana/53YxS9gk29jmsHFNmJnEYrF451U5f7tgLv5gPKNN15bP) `53YxS9gk…` | Higgspad | 455753385 | nee | 0 | 6 | 1054 |  |
| higgs | [7KfvhB](https://dexscreener.com/solana/7KfvhBDGFPHQ7jcBLDDzrE2icw5bUxwwKhgBWhQypump) `7KfvhBDG…` | HIGGS | 454633458 | nee | 0 | 2 | 1052 |  |

### Per token

#### Pepper — Pepper The Raccoon

[DexScreener](https://dexscreener.com/solana/9g4Pib6AM3oVdP9YA7ZcssAXAuhoUzkjR8w27doYpump) · mint `9g4Pib6AM3oVdP9YA7ZcssAXAuhoUzkjR8w27doYpump` · store 1054

- Aangemaakt op 2026-10-10 17:10 UTC door `DfG6Uhpa…`.
- Gegradueerd na 7 min met 660 curve-kopers en 846 SOL curve-volume.
- Eerste 24 u in de pool: 42.531 trades van 8.064 handelaren, 24.910 SOL volume (74 % organisch); piek 18.3× de openingsprijs, einde 24 u op 6.88× (38 % van de piek).
- Insiders (creator + kopers binnen 10 s na de aanmaak): 27; hun aandeel op +15 min 12 %, na 24 u 8 %; creator-verkopen in 24 u: 0.
- Op +15 min: koers 2.94× de open, netto instroom 68 SOL, 1.378 pool-traders, 1.816 SOL organisch volume.

**Tijdlijn** (koers = reserve-prijs t.o.v. de openingsprijs van de pool; diepte = reële SOL in de pool/curve aan het eind van het vak):

| venster | trades | kopers | verkopers | volume SOL | netto SOL | farmer-aandeel | koers eind | koers piek | diepte SOL |
|---|---|---|---|---|---|---|---|---|---|
| curve (vóór de pool) | 1.811 | 660 | 429 | 846 | 85 | – | 0.98× | 0.98× | 85 |
| +0–5 min | 2.558 | 737 | 619 | 1.049 | 9 | 10 % | 1.25× | 1.84× | 84 |
| +5–10 min | 866 | 329 | 260 | 359 | -2 | 11 % | 1.23× | 1.63× | 85 |
| +10–15 min | 1.336 | 380 | 333 | 720 | 61 | 24 % | 2.99× | 3.51× | 138 |
| +15–20 min | 1.512 | 484 | 344 | 837 | 28 | 21 % | 3.72× | 4.77× | 160 |
| +20–25 min | 965 | 320 | 288 | 524 | 7 | 21 % | 3.82× | 5.20× | 155 |
| +25–30 min | 1.321 | 369 | 420 | 666 | -34 | 30 % | 2.22× | 4.15× | 118 |
| +30–35 min | 461 | 168 | 135 | 238 | 39 | 12 % | 3.50× | 3.56× | 153 |
| +35–40 min | 591 | 183 | 213 | 326 | -28 | 18 % | 2.40× | 4.26× | 120 |
| +40–45 min | 419 | 153 | 130 | 249 | 26 | 19 % | 3.26× | 3.59× | 145 |
| +45–50 min | 201 | 87 | 83 | 111 | -16 | 20 % | 2.65× | 3.26× | 130 |
| +50–55 min | 479 | 225 | 131 | 284 | 48 | 24 % | 4.44× | 4.58× | 171 |
| +55–60 min | 369 | 123 | 133 | 208 | -18 | 25 % | 3.69× | 4.99× | 155 |
| +1–2 u | 4.126 | 1.094 | 924 | 2.485 | 34 | 21 % | 3.84× | 5.58× | 161 |
| +2–3 u | 3.470 | 952 | 772 | 2.234 | 18 | 28 % | 3.62× | 5.35× | 158 |
| +3–4 u | 2.926 | 808 | 556 | 1.846 | 104 | 34 % | 7.97× | 9.26× | 246 |
| +4–5 u | 6.127 | 1.755 | 1.397 | 3.353 | 17 | 30 % | 7.15× | 12.8× | 235 |
| +5–6 u | 2.340 | 693 | 710 | 1.239 | 28 | 29 % | 7.91× | 9.33× | 248 |
| +6–7 u | 1.140 | 328 | 408 | 629 | -38 | 27 % | 5.60× | 8.48× | 206 |
| +7–8 u | 898 | 297 | 326 | 482 | 0 | 21 % | 5.36× | 5.71× | 202 |
| +8–9 u | 506 | 157 | 195 | 261 | -14 | 29 % | 4.55× | 6.05× | 185 |
| +9–10 u | 422 | 138 | 125 | 258 | 14 | 31 % | 5.11× | 5.59× | 197 |
| +10–11 u | 432 | 120 | 127 | 298 | 21 | 24 % | 5.97× | 6.53× | 215 |
| +11–12 u | 406 | 101 | 128 | 210 | 2 | 23 % | 5.98× | 6.71× | 215 |
| +12–13 u | 426 | 129 | 123 | 172 | -7 | 27 % | 5.54× | 7.22× | 207 |
| +13–14 u | 262 | 82 | 93 | 82 | -5 | 14 % | 5.27× | 5.94× | 202 |
| +14–15 u | 293 | 80 | 88 | 130 | 18 | 41 % | 6.11× | 6.75× | 219 |
| +15–16 u | 574 | 164 | 158 | 341 | 51 | 28 % | 8.77× | 9.69× | 266 |
| +16–17 u | 795 | 220 | 234 | 624 | 4 | 28 % | 8.63× | 12.4× | 264 |
| +17–18 u | 818 | 246 | 249 | 621 | 35 | 30 % | 10.5× | 12.9× | 293 |
| +18–19 u | 1.670 | 435 | 452 | 1.664 | 51 | 34 % | 13.1× | 18.3× | 331 |
| +19–20 u | 600 | 179 | 171 | 369 | 7 | 21 % | 13.4× | 14.3× | 336 |
| +20–21 u | 1.017 | 285 | 324 | 748 | -31 | 33 % | 10.7× | 17.1× | 299 |
| +21–22 u | 716 | 182 | 252 | 469 | 18 | 22 % | 11.6× | 12.3× | 313 |
| +22–23 u | 619 | 190 | 272 | 333 | -44 | 23 % | 8.57× | 12.8× | 266 |
| +23–24 u | 870 | 235 | 282 | 490 | -24 | 20 % | 6.88× | 8.94× | 237 |

**Grootste kopers** (eerste 24 u): `ARu4n5mF…` 662.6 SOL op +4 s (farmer); `2tgUbS9U…` 255.0 SOL op +1 min (farmer); `7KeUejFS…` 194.0 SOL op +4.1 u; `922M9fBT…` 157.3 SOL op +15 min (farmer); `kEFiAX3j…` 146.0 SOL op +0 s (farmer).

**Grootste verkopers** (eerste 24 u): `FHZxGWzu…` 48.5 SOL op +18.4 u; `AGcP3J4Z…` 43.2 SOL op +3.9 u; `FirN2PFj…` 29.1 SOL op +23.5 u; `2rXyxPML…` 27.0 SOL op +18.5 u; `FUCKNigg…` 26.2 SOL op +16.2 u.

**Onze strategieën, beschrijvend** (geen replay, geen P&L):

- F7 (curve): eerste trigger op +1 min na de aanmaak, diepte 33 SOL, koers 0.27× de open; 31 trigger(s) in totaal. Koers daarna t.o.v. de trigger: na 150 slots: 1.08× (piek 2.35×), na 600 slots: 1.41× (piek 2.35×), na 2400 slots: 3.63× (piek 6.75×).
- F7 (pool): eerste trigger op +24 min na de aanmaak, diepte 175 SOL, koers 4.26× de open; 31 trigger(s) in totaal. Koers daarna t.o.v. de trigger: na 150 slots: 0.92× (piek 1.07×), na 600 slots: 0.89× (piek 1.12×), na 2400 slots: 0.78× (piek 1.22×).
- M1 (k = 2): trigger op +33 min na de pool-opening bij 3.03×, 667 organische kopers in 15 min, insider-aandeel 11 %, diepte 141 SOL. Koers daarna t.o.v. de trigger: na 15 min: 0.93× (piek 1.40×), na 1 u: 0.50× (piek 1.84×), na 6 u: 1.93× (piek 4.22×).
- M1 (k = 3): trigger op +33 min na de pool-opening bij 3.03×, 667 organische kopers in 15 min, insider-aandeel 11 %, diepte 141 SOL. Koers daarna t.o.v. de trigger: na 15 min: 0.93× (piek 1.40×), na 1 u: 0.50× (piek 1.84×), na 6 u: 1.93× (piek 4.22×).
- M1+ op +15 min: koers 2.94×, netto instroom 68 SOL, 1.176 organische kopers, insider-aandeel 12 %, 660 curve-kopers → trigger bij k = 1.5, 2, maar hype-veto (> 585 curve-kopers). Koers daarna t.o.v. +15 min: na 1 u: 1.18× (piek 1.77×), na 6 u: 2.13× (piek 4.35×), na 24 u: 2.34× (piek 6.23×).

#### DOPE — Doge Pepe

[DexScreener](https://dexscreener.com/solana/33jENUQnjoitkMFpvNJcSHyLAHFWJuHwT6E3E8CNpump) · mint `33jENUQnjoitkMFpvNJcSHyLAHFWJuHwT6E3E8CNpump` · store 1054

- Aangemaakt op 2026-10-11 13:28 UTC door `CZutgB7w…`.
- **Let op:** de store eindigt 5.5 u na de pool-opening (of de aanmaak); '24 u' en 'eind' zijn hier dus de stand op dat moment.
- Gegradueerd na 4 min met 349 curve-kopers en 533 SOL curve-volume.
- Eerste 24 u in de pool: 104.025 trades van 17.419 handelaren, 70.818 SOL volume (77 % organisch); piek 44.3× de openingsprijs, einde 24 u op 33.9× (77 % van de piek).
- Insiders (creator + kopers binnen 10 s na de aanmaak): 14; hun aandeel op +15 min 11 %, na 24 u 11 %; creator-verkopen in 24 u: 0.
- Op +15 min: koers 9.81× de open, netto instroom 322 SOL, 5.125 pool-traders, 12.519 SOL organisch volume.

**Tijdlijn** (koers = reserve-prijs t.o.v. de openingsprijs van de pool; diepte = reële SOL in de pool/curve aan het eind van het vak):

| venster | trades | kopers | verkopers | volume SOL | netto SOL | farmer-aandeel | koers eind | koers piek | diepte SOL |
|---|---|---|---|---|---|---|---|---|---|
| curve (vóór de pool) | 879 | 349 | 178 | 533 | 85 | – | 0.99× | 0.99× | 85 |
| +0–5 min | 9.586 | 2.404 | 1.571 | 6.271 | 227 | 16 % | 9.14× | 10.6× | 281 |
| +5–10 min | 7.199 | 2.148 | 1.527 | 5.054 | 28 | 20 % | 7.91× | 11.1× | 241 |
| +10–15 min | 5.508 | 1.632 | 1.333 | 3.990 | 66 | 21 % | 9.81× | 12.7× | 289 |
| +15–20 min | 2.767 | 1.008 | 737 | 1.490 | 4 | 16 % | 9.13× | 11.8× | 286 |
| +20–25 min | 3.777 | 1.137 | 1.153 | 2.522 | 107 | 28 % | 15.4× | 18.1× | 349 |
| +25–30 min | 2.799 | 1.040 | 831 | 1.566 | 31 | 22 % | 17.0× | 17.7× | 375 |
| +30–35 min | 3.002 | 1.089 | 893 | 1.984 | 19 | 28 % | 17.2× | 20.0× | 385 |
| +35–40 min | 2.025 | 688 | 708 | 1.117 | 31 | 22 % | 19.1× | 20.7× | 395 |
| +40–45 min | 3.200 | 1.060 | 934 | 1.950 | -18 | 22 % | 15.9× | 19.6× | 369 |
| +45–50 min | 2.360 | 820 | 728 | 1.480 | -23 | 23 % | 13.0× | 17.5× | 339 |
| +50–55 min | 1.952 | 751 | 591 | 1.113 | 49 | 25 % | 16.2× | 16.9× | 367 |
| +55–60 min | 2.122 | 700 | 642 | 1.259 | 5 | 29 % | 15.5× | 19.4× | 364 |
| +1–2 u | 13.131 | 2.989 | 2.809 | 8.108 | 50 | 23 % | 13.8× | 20.2× | 347 |
| +2–3 u | 8.494 | 2.066 | 2.065 | 5.035 | -15 | 16 % | 9.43× | 14.4× | 289 |
| +3–4 u | 7.104 | 1.962 | 1.707 | 4.249 | 99 | 23 % | 13.5× | 17.9× | 355 |
| +4–5 u | 23.606 | 4.506 | 4.418 | 19.955 | 406 | 28 % | 36.2× | 44.3× | 613 |
| +5–6 u | 5.393 | 1.517 | 1.474 | 3.675 | 10 | 21 % | 33.9× | 38.2× | 594 |

**Grootste kopers** (eerste 24 u): `ARu4n5mF…` 2.362 SOL op +15 s; `kEFiAX3j…` 685.7 SOL op +40 s (farmer); `iK7BmyUo…` 482.6 SOL op +48 s (farmer); `MriyaNN8…` 464.9 SOL op +8 min (farmer); `GV7eoS7A…` 329.9 SOL op +21 min (farmer).

**Grootste verkopers** (eerste 24 u): `3saciJi6…` 60.0 SOL op +4.9 u; `HJwYWjkE…` 40.6 SOL op +5.2 u; `FCsPncAH…` 38.1 SOL op +4.6 u; `6WveoGoZ…` 35.6 SOL op +5.0 u; `4kU4JzzL…` 33.5 SOL op +2.8 u.

**Onze strategieën, beschrijvend** (geen replay, geen P&L):

- F7 (curve): eerste trigger op +3 min na de aanmaak, diepte 46 SOL, koers 0.38× de open; 53 trigger(s) in totaal. Koers daarna t.o.v. de trigger: na 150 slots: 1.33× (piek 1.36×), na 600 slots: 3.54× (piek 4.11×), na 2400 slots: 27.1× (piek 29.6×).
- F7 (pool): eerste trigger op +6 min na de aanmaak, diepte 157 SOL, koers 3.56× de open; 53 trigger(s) in totaal. Koers daarna t.o.v. de trigger: na 150 slots: 1.27× (piek 1.58×), na 600 slots: 1.27× (piek 1.58×), na 2400 slots: 1.92× (piek 3.12×).
- M1 (k = 2): trigger op +32 s na de pool-opening bij 2.54×, 232 organische kopers in 15 min, insider-aandeel 12 %, diepte 120 SOL. Koers daarna t.o.v. de trigger: na 15 min: 3.58× (piek 5.13×), na 1 u: 6.88× (piek 8.35×), na 6 u: 13.7× (piek 17.9×).
- M1 (k = 3): trigger op +46 s na de pool-opening bij 3.00×, 400 organische kopers in 15 min, insider-aandeel 12 %, diepte 135 SOL. Koers daarna t.o.v. de trigger: na 15 min: 2.97× (piek 4.25×), na 1 u: 5.40× (piek 6.92×), na 6 u: 11.3× (piek 14.8×).
- M1+ op +15 min: koers 9.81×, netto instroom 322 SOL, 4.826 organische kopers, insider-aandeel 11 %, 349 curve-kopers → trigger bij k = 1.5, 2. Koers daarna t.o.v. +15 min: na 1 u: 1.27× (piek 2.11×), na 6 u: 3.46× (piek 4.51×), na 24 u: 3.46× (piek 4.51×).

#### Normie — The Normie

[DexScreener](https://dexscreener.com/solana/DzMwArmdzPqcqgvyqAmTxy79XdA9NwK9mMteqWLJpump) · mint `DzMwArmdzPqcqgvyqAmTxy79XdA9NwK9mMteqWLJpump` · store 1054

- Aangemaakt op 2026-10-11 09:32 UTC door `4HXb7ivg…`.
- Gegradueerd naar een pool met een **andere quote-munt dan SOL** (52.310 pool-trades in de store). Onze store kent voor zo'n pool geen SOL-zijde en dus geen koers; de token valt buiten het universum van F7, M1 en de watchlist. Wat hieronder staat gaat alleen over de curve.
- **Let op:** de store eindigt 9.5 u na de pool-opening (of de aanmaak); '24 u' en 'eind' zijn hier dus de stand op dat moment.
- Insiders (creator + kopers binnen 10 s na de aanmaak): 43; hun aandeel op +15 min –, na 24 u 42 %; creator-verkopen in 24 u: 0.

**Tijdlijn** (koers = reserve-prijs t.o.v. de openingsprijs van de pool; diepte = reële SOL in de pool/curve aan het eind van het vak):

| venster | trades | kopers | verkopers | volume SOL | netto SOL | farmer-aandeel | koers eind | koers piek | diepte SOL |
|---|---|---|---|---|---|---|---|---|---|
| curve (vóór de pool) | 745 | 299 | 172 | 7.811 | 1.669 | – | 0.99× | 0.99× | 1.669 |

**Onze strategieën, beschrijvend** (geen replay, geen P&L):

- F7: geen trigger (geen verkoop ≥ max(3 SOL, 4 %) met instroom ervoor, zonder creator-verkoop, op de curve bij 20–75 SOL of in de pool bij ≥ 150 SOL).
- M1: niet van toepassing (geen normale SOL-pool).

#### Incognito — Incognito69420

[DexScreener](https://dexscreener.com/solana/BBZ3sgY66QVsxcXXBVpuQfDzH2b5Ub8qmfKNQJjBpump) · mint `BBZ3sgY66QVsxcXXBVpuQfDzH2b5Ub8qmfKNQJjBpump` · store 1054

- Aangemaakt op 2026-10-11 05:41 UTC door `29yFzeBZ…`.
- Gegradueerd naar een pool met een **andere quote-munt dan SOL** (74.348 pool-trades in de store). Onze store kent voor zo'n pool geen SOL-zijde en dus geen koers; de token valt buiten het universum van F7, M1 en de watchlist. Wat hieronder staat gaat alleen over de curve.
- **Let op:** de store eindigt 13.3 u na de pool-opening (of de aanmaak); '24 u' en 'eind' zijn hier dus de stand op dat moment.
- Insiders (creator + kopers binnen 10 s na de aanmaak): 4; hun aandeel op +15 min –, na 24 u 0 %; creator-verkopen in 24 u: 0.

**Tijdlijn** (koers = reserve-prijs t.o.v. de openingsprijs van de pool; diepte = reële SOL in de pool/curve aan het eind van het vak):

| venster | trades | kopers | verkopers | volume SOL | netto SOL | farmer-aandeel | koers eind | koers piek | diepte SOL |
|---|---|---|---|---|---|---|---|---|---|
| curve (vóór de pool) | 450 | 171 | 72 | 0 | 0 | – | 1.00× | 1.00× | 0 |

**Onze strategieën, beschrijvend** (geen replay, geen P&L):

- F7: geen trigger (geen verkoop ≥ max(3 SOL, 4 %) met instroom ervoor, zonder creator-verkoop, op de curve bij 20–75 SOL of in de pool bij ≥ 150 SOL).
- M1: niet van toepassing (geen normale SOL-pool).

#### Dacity — Dacity Coin

[DexScreener](https://dexscreener.com/solana/nxPqZ4F7ux8Vph5NFu25rEP6Kx9Gr1U6Fmj36Rjpump) · mint `nxPqZ4F7ux8Vph5NFu25rEP6Kx9Gr1U6Fmj36Rjpump` · store 1054

- Aangemaakt op 2026-10-11 11:16 UTC door `FFdBLYqL…`.
- Gegradueerd naar een pool met een **andere quote-munt dan SOL** (52.650 pool-trades in de store). Onze store kent voor zo'n pool geen SOL-zijde en dus geen koers; de token valt buiten het universum van F7, M1 en de watchlist. Wat hieronder staat gaat alleen over de curve.
- **Let op:** de store eindigt 7.8 u na de pool-opening (of de aanmaak); '24 u' en 'eind' zijn hier dus de stand op dat moment.
- Insiders (creator + kopers binnen 10 s na de aanmaak): 35; hun aandeel op +15 min –, na 24 u 51 %; creator-verkopen in 24 u: 0.

**Tijdlijn** (koers = reserve-prijs t.o.v. de openingsprijs van de pool; diepte = reële SOL in de pool/curve aan het eind van het vak):

| venster | trades | kopers | verkopers | volume SOL | netto SOL | farmer-aandeel | koers eind | koers piek | diepte SOL |
|---|---|---|---|---|---|---|---|---|---|
| curve (vóór de pool) | 222 | 94 | 33 | 0 | 0 | – | 0.99× | 0.99× | 0 |

**Onze strategieën, beschrijvend** (geen replay, geen P&L):

- F7: geen trigger (geen verkoop ≥ max(3 SOL, 4 %) met instroom ervoor, zonder creator-verkoop, op de curve bij 20–75 SOL of in de pool bij ≥ 150 SOL).
- M1: niet van toepassing (geen normale SOL-pool).

#### LARP — LARP

[DexScreener](https://dexscreener.com/solana/E447xHwXKMLUhqriexGwmjmMPn7YSsvyRKv9L2Tdpump) · mint `E447xHwXKMLUhqriexGwmjmMPn7YSsvyRKv9L2Tdpump` · store 1054

- Aangemaakt op 2026-10-11 15:03 UTC door `4sXwHNgf…`.
- **Let op:** de store eindigt 3.8 u na de pool-opening (of de aanmaak); '24 u' en 'eind' zijn hier dus de stand op dat moment.
- Gegradueerd na 13 min met 906 curve-kopers en 941 SOL curve-volume.
- Eerste 24 u in de pool: 48.356 trades van 9.353 handelaren, 26.651 SOL volume (74 % organisch); piek 14.3× de openingsprijs, einde 24 u op 0.33× (2 % van de piek).
- Insiders (creator + kopers binnen 10 s na de aanmaak): 60; hun aandeel op +15 min 2 %, na 24 u 3 %; creator-verkopen in 24 u: 0.
- Op +15 min: koers 12.1× de open, netto instroom 301 SOL, 4.834 pool-traders, 8.230 SOL organisch volume.

**Tijdlijn** (koers = reserve-prijs t.o.v. de openingsprijs van de pool; diepte = reële SOL in de pool/curve aan het eind van het vak):

| venster | trades | kopers | verkopers | volume SOL | netto SOL | farmer-aandeel | koers eind | koers piek | diepte SOL |
|---|---|---|---|---|---|---|---|---|---|
| curve (vóór de pool) | 2.652 | 906 | 532 | 941 | 85 | – | 1.00× | 1.00× | 85 |
| +0–5 min | 3.943 | 1.183 | 897 | 1.766 | 92 | 12 % | 4.09× | 4.77× | 163 |
| +5–10 min | 8.542 | 2.521 | 1.542 | 5.562 | 128 | 27 % | 8.46× | 11.0× | 266 |
| +10–15 min | 5.841 | 1.940 | 1.514 | 3.662 | 81 | 28 % | 12.1× | 14.3× | 301 |
| +15–20 min | 5.856 | 1.934 | 1.600 | 3.098 | -10 | 27 % | 9.51× | 13.0× | 278 |
| +20–25 min | 3.481 | 1.258 | 981 | 1.975 | 27 | 24 % | 10.2× | 11.4× | 297 |
| +25–30 min | 2.690 | 949 | 827 | 1.515 | -12 | 27 % | 8.52× | 12.3× | 251 |
| +30–35 min | 2.676 | 997 | 871 | 1.638 | 16 | 31 % | 8.57× | 10.3× | 260 |
| +35–40 min | 1.653 | 629 | 596 | 895 | -35 | 29 % | 6.01× | 9.31× | 221 |
| +40–45 min | 1.246 | 498 | 419 | 595 | 32 | 26 % | 7.49× | 8.06× | 236 |
| +45–50 min | 1.271 | 488 | 415 | 694 | 15 | 33 % | 8.05× | 9.62× | 249 |
| +50–55 min | 1.060 | 376 | 347 | 696 | 19 | 35 % | 8.79× | 10.4× | 264 |
| +55–60 min | 1.299 | 488 | 480 | 797 | -38 | 34 % | 6.07× | 9.63× | 213 |
| +1–2 u | 6.914 | 1.481 | 2.040 | 3.178 | -81 | 19 % | 1.49× | 6.61× | 98 |
| +2–3 u | 1.597 | 362 | 608 | 524 | -60 | 16 % | 0.28× | 1.89× | 33 |
| +3–4 u | 287 | 86 | 124 | 57 | 5 | 12 % | 0.33× | 0.36× | 37 |

**Grootste kopers** (eerste 24 u): `ARu4n5mF…` 699.9 SOL op +2 s (farmer); `iK7BmyUo…` 209.6 SOL op +4 min (farmer); `7y5aRdzh…` 136.6 SOL op +13 s (farmer); `kEFiAX3j…` 118.8 SOL op +34 s (farmer); `HGXr65Ki…` 97.1 SOL op +2 min (farmer).

**Grootste verkopers** (eerste 24 u): `ARu4n5mF…` 43.6 SOL op +59 min; `DSJx3GcJ…` 26.9 SOL op +30 min; `7bANjVkU…` 23.6 SOL op +48 min; `6ckZ4bGb…` 22.8 SOL op +103 min; `7y75CqEh…` 21.8 SOL op +85 min.

**Onze strategieën, beschrijvend** (geen replay, geen P&L):

- F7 (curve): eerste trigger op +1 min na de aanmaak, diepte 28 SOL, koers 0.23× de open; 31 trigger(s) in totaal. Koers daarna t.o.v. de trigger: na 150 slots: 0.79× (piek 1.00×), na 600 slots: 0.60× (piek 1.00×), na 2400 slots: 2.96× (piek 3.76×).
- F7 (pool): eerste trigger op +18 min na de aanmaak, diepte 190 SOL, koers 5.14× de open; 31 trigger(s) in totaal. Koers daarna t.o.v. de trigger: na 150 slots: 1.18× (piek 1.25×), na 600 slots: 1.62× (piek 1.96×), na 2400 slots: 2.43× (piek 2.78×).
- M1 (k = 2): trigger op +46 min na de pool-opening bij 8.64×, 1.255 organische kopers in 15 min, insider-aandeel 2 %, diepte 256 SOL. Koers daarna t.o.v. de trigger: na 15 min: 0.71× (piek 1.21×), na 1 u: 0.23× (piek 1.21×), na 6 u: 0.04× (piek 1.21×).
- M1 (k = 3): trigger op +46 min na de pool-opening bij 8.64×, 1.255 organische kopers in 15 min, insider-aandeel 2 %, diepte 256 SOL. Koers daarna t.o.v. de trigger: na 15 min: 0.71× (piek 1.21×), na 1 u: 0.23× (piek 1.21×), na 6 u: 0.04× (piek 1.21×).
- M1+ op +15 min: koers 12.1×, netto instroom 301 SOL, 4.490 organische kopers, insider-aandeel 2 %, 906 curve-kopers → trigger bij k = 1.5, 2, maar hype-veto (> 585 curve-kopers). Koers daarna t.o.v. +15 min: na 1 u: 0.24× (piek 1.08×), na 6 u: 0.03× (piek 1.08×), na 24 u: 0.03× (piek 1.08×).

#### ROSE — MissRose

[DexScreener](https://dexscreener.com/solana/7b6dX3nE25xrfNViEKZHL9wVJFcBxQYuWPpmzHUxx28H) · mint `7b6dX3nE25xrfNViEKZHL9wVJFcBxQYuWPpmzHUxx28H` · store 1053

- Aangemaakt op 2026-10-10 07:30 UTC door `6cm1tgjj…`.
- **Let op:** de store eindigt 9.4 u na de pool-opening (of de aanmaak); '24 u' en 'eind' zijn hier dus de stand op dat moment.
- Niet gegradueerd in deze store: de curve kwam tot 13 SOL reële reserves met 944 kopers en 1.859 SOL volume; piek 8.92× de eerste prijs, einde op 1.87×.
- Insiders (creator + kopers binnen 10 s na de aanmaak): 9; hun aandeel op +15 min –, na 24 u 0 %; creator-verkopen in 24 u: 0.

**Tijdlijn** (koers = reserve-prijs t.o.v. de openingsprijs van de pool; diepte = reële SOL in de pool/curve aan het eind van het vak):

| venster | trades | kopers | verkopers | volume SOL | netto SOL | farmer-aandeel | koers eind | koers piek | diepte SOL |
|---|---|---|---|---|---|---|---|---|---|
| +0–5 min | 432 | 206 | 114 | 204 | 22 | – | 2.71× | 4.38× | 22 |
| +5–10 min | 224 | 76 | 90 | 84 | 1 | – | 2.78× | 3.00× | 23 |
| +10–15 min | 82 | 38 | 33 | 29 | 4 | – | 3.19× | 3.56× | 27 |
| +15–20 min | 148 | 69 | 40 | 75 | 10 | – | 4.44× | 5.10× | 37 |
| +20–25 min | 91 | 35 | 34 | 53 | 1 | – | 4.63× | 5.97× | 38 |
| +25–30 min | 187 | 97 | 55 | 81 | 13 | – | 6.51× | 7.30× | 51 |
| +30–35 min | 115 | 39 | 42 | 55 | -15 | – | 4.30× | 6.45× | 36 |
| +35–40 min | 107 | 40 | 58 | 44 | -3 | – | 3.91× | 4.31× | 33 |
| +40–45 min | 82 | 33 | 34 | 37 | -9 | – | 2.86× | 4.40× | 24 |
| +45–50 min | 82 | 37 | 30 | 45 | 8 | – | 3.76× | 3.83× | 32 |
| +50–55 min | 137 | 74 | 45 | 56 | 6 | – | 4.51× | 5.23× | 37 |
| +55–60 min | 52 | 24 | 21 | 14 | -2 | – | 4.25× | 4.52× | 36 |
| +1–2 u | 791 | 227 | 232 | 406 | 3 | – | 4.59× | 8.92× | 38 |
| +2–3 u | 402 | 134 | 140 | 200 | -14 | – | 2.86× | 5.68× | 24 |
| +3–4 u | 243 | 82 | 87 | 109 | 2 | – | 3.09× | 3.93× | 26 |
| +4–5 u | 351 | 124 | 95 | 188 | 21 | – | 5.78× | 5.87× | 46 |
| +5–6 u | 264 | 87 | 103 | 99 | -17 | – | 3.44× | 6.82× | 29 |
| +6–7 u | 104 | 32 | 42 | 34 | -3 | – | 3.10× | 3.93× | 26 |
| +7–8 u | 78 | 29 | 33 | 22 | -11 | – | 1.97× | 3.17× | 15 |
| +8–9 u | 56 | 24 | 30 | 17 | 0 | – | 1.99× | 2.13× | 15 |
| +9–10 u | 21 | 4 | 15 | 5 | -1 | – | 1.87× | 2.09× | 13 |

**Grootste kopers** (eerste 24 u): `ssssswdk…` 35.5 SOL op +3 min; `ARu4n5mF…` 29.2 SOL op +20 min; `2k2jJVet…` 21.0 SOL op +14 min; `sssssDdM…` 20.3 SOL op +5 min; `omegoMAe…` 20.0 SOL op +3 min.

**Grootste verkopers** (eerste 24 u): `ssssswdk…` 36.3 SOL op +4 min; `ARu4n5mF…` 25.0 SOL op +30 min; `2k2jJVet…` 20.9 SOL op +15 min; `77wS9zZH…` 20.8 SOL op +2 min; `omegoMAe…` 20.3 SOL op +6 min.

**Onze strategieën, beschrijvend** (geen replay, geen P&L):

- F7 (curve): eerste trigger op +18 min na de aanmaak, diepte 42 SOL, koers – de open; 11 trigger(s) in totaal. Koers daarna t.o.v. de trigger: na 150 slots: 0.91× (piek 1.03×), na 600 slots: 0.86× (piek 1.03×), na 2400 slots: 1.51× (piek 1.51×).
- M1: niet van toepassing (niet gegradueerd).

#### QCAT — Quantum Cat

[DexScreener](https://dexscreener.com/solana/GmsAcJAFKEtAXDoRUroaQP5GBdmPcGwA2sRdC9Mbpump) · mint `GmsAcJAFKEtAXDoRUroaQP5GBdmPcGwA2sRdC9Mbpump` · store 1052

- Aangemaakt op 2026-10-09 05:56 UTC door `BCyNgSB1…`.
- **Let op:** de store eindigt 7.8 u na de pool-opening (of de aanmaak); '24 u' en 'eind' zijn hier dus de stand op dat moment.
- Gegradueerd na 58 min met 993 curve-kopers en 1.410 SOL curve-volume.
- Eerste 24 u in de pool: 7.778 trades van 2.061 handelaren, 3.384 SOL volume (86 % organisch); piek 2.69× de openingsprijs, einde 24 u op 0.64× (24 % van de piek).
- Insiders (creator + kopers binnen 10 s na de aanmaak): 7; hun aandeel op +15 min 0 %, na 24 u 0 %; creator-verkopen in 24 u: 1 (0.0 SOL).
- Op +15 min: koers 1.14× de open, netto instroom -6 SOL, 495 pool-traders, 438 SOL organisch volume.

**Tijdlijn** (koers = reserve-prijs t.o.v. de openingsprijs van de pool; diepte = reële SOL in de pool/curve aan het eind van het vak):

| venster | trades | kopers | verkopers | volume SOL | netto SOL | farmer-aandeel | koers eind | koers piek | diepte SOL |
|---|---|---|---|---|---|---|---|---|---|
| curve (vóór de pool) | 4.549 | 993 | 814 | 1.410 | 85 | – | 0.97× | 0.97× | 85 |
| +0–5 min | 444 | 150 | 167 | 207 | -39 | 4 % | 0.48× | 1.04× | 42 |
| +5–10 min | 300 | 145 | 80 | 136 | 25 | 4 % | 0.98× | 1.07× | 68 |
| +10–15 min | 316 | 142 | 102 | 124 | 8 | 12 % | 1.14× | 1.20× | 75 |
| +15–20 min | 287 | 131 | 91 | 99 | -8 | 15 % | 0.92× | 1.18× | 65 |
| +20–25 min | 255 | 128 | 79 | 142 | 24 | 16 % | 1.47× | 1.62× | 87 |
| +25–30 min | 420 | 205 | 116 | 167 | -5 | 13 % | 1.28× | 1.64× | 81 |
| +30–35 min | 282 | 143 | 95 | 110 | 18 | 18 % | 1.75× | 1.80× | 97 |
| +35–40 min | 447 | 217 | 140 | 194 | 20 | 11 % | 2.29× | 2.42× | 114 |
| +40–45 min | 708 | 284 | 224 | 298 | -16 | 17 % | 1.71× | 2.69× | 97 |
| +45–50 min | 418 | 180 | 139 | 178 | 10 | 15 % | 1.96× | 2.28× | 105 |
| +50–55 min | 355 | 142 | 138 | 125 | -24 | 18 % | 1.22× | 1.97× | 79 |
| +55–60 min | 411 | 150 | 145 | 135 | -10 | 10 % | 0.95× | 1.32× | 68 |
| +1–2 u | 1.026 | 303 | 363 | 330 | -11 | 17 % | 0.63× | 0.95× | 52 |
| +2–3 u | 545 | 211 | 185 | 280 | 5 | 13 % | 0.66× | 1.30× | 54 |
| +3–4 u | 321 | 113 | 98 | 161 | 25 | 14 % | 1.17× | 1.31× | 77 |
| +4–5 u | 375 | 139 | 116 | 235 | -1 | 18 % | 1.08× | 1.41× | 74 |
| +5–6 u | 316 | 129 | 109 | 168 | 2 | 23 % | 1.08× | 1.18× | 74 |
| +6–7 u | 347 | 129 | 114 | 210 | 3 | 17 % | 1.10× | 1.51× | 75 |
| +7–8 u | 205 | 69 | 76 | 86 | -21 | 13 % | 0.64× | 1.10× | 53 |

**Grootste kopers** (eerste 24 u): `ARu4n5mF…` 83.4 SOL op +3 s (farmer); `FEM6uYLz…` 36.3 SOL op +17 min; `ECChupKn…` 34.1 SOL op +39 min; `AcUJM6Aq…` 24.5 SOL op +14 min; `2npiwsPo…` 24.3 SOL op +13 s (farmer).

**Grootste verkopers** (eerste 24 u): `FEM6uYLz…` 11.6 SOL op +2.2 u; `GGztQqQ6…` 10.2 SOL op +58 min; `DjCYDeUz…` 9.2 SOL op +6.5 u; `GGztQqQ6…` 9.0 SOL op +6.2 u; `ARu4n5mF…` 8.7 SOL op +4.6 u.

**Onze strategieën, beschrijvend** (geen replay, geen P&L):

- F7: geen trigger (geen verkoop ≥ max(3 SOL, 4 %) met instroom ervoor, zonder creator-verkoop, op de curve bij 20–75 SOL of in de pool bij ≥ 150 SOL).
- M1 (k = 2): trigger op +39 min na de pool-opening bij 2.00×, 415 organische kopers in 15 min, insider-aandeel 0 %, diepte 105 SOL. Koers daarna t.o.v. de trigger: na 15 min: 0.76× (piek 1.35×), na 1 u: 0.30× (piek 1.35×), na 6 u: 0.50× (piek 1.35×).
- M1 (k = 3): geen trigger binnen 6 u (209 kandidaat-momenten met ≥ 2× en instroom).
- M1+ op +15 min: koers 1.14×, netto instroom -6 SOL, 386 organische kopers, insider-aandeel 0 %, 993 curve-kopers → geen trigger; hype-veto (> 585 curve-kopers). Koers daarna t.o.v. +15 min: na 1 u: 0.46× (piek 2.35×), na 6 u: 1.07× (piek 2.35×), na 24 u: 0.56× (piek 2.35×).

#### SpaceXSI — SpaceXSI

[DexScreener](https://dexscreener.com/solana/kS5HANa73V6Rb5hWikpQqgJYYFAJSCto5saBP45pump) · mint `kS5HANa73V6Rb5hWikpQqgJYYFAJSCto5saBP45pump` · store 1052

- Aangemaakt op 2026-10-09 10:45 UTC door `4VHBrTDT…`.
- **Let op:** de store eindigt 3.9 u na de pool-opening (of de aanmaak); '24 u' en 'eind' zijn hier dus de stand op dat moment.
- Gegradueerd **in de create-slot**: 1 wallet(s) kochten de hele curve (85 SOL) in dezelfde slot als de aanmaak — de dev-bundel-structuur.
- Eerste 24 u in de pool: 37.443 trades van 2.583 handelaren, 854 SOL volume (100 % organisch); piek 20.6× de openingsprijs, einde 24 u op 0.04× (0 % van de piek).
- Insiders (creator + kopers binnen 10 s na de aanmaak): 1; hun aandeel op +15 min 85 %, na 24 u > 100 % (verkopen boven de zichtbare kopen: transfers); creator-verkopen in 24 u: 1 (0.0 SOL).
- Op +15 min: koers 11.7× de open, netto instroom 191 SOL, 352 pool-traders, 238 SOL organisch volume.

**Tijdlijn** (koers = reserve-prijs t.o.v. de openingsprijs van de pool; diepte = reële SOL in de pool/curve aan het eind van het vak):

| venster | trades | kopers | verkopers | volume SOL | netto SOL | farmer-aandeel | koers eind | koers piek | diepte SOL |
|---|---|---|---|---|---|---|---|---|---|
| +0–5 min | 3.887 | 324 | 301 | 158 | 153 | 0 % | 8.74× | 8.79× | 234 |
| +5–10 min | 4.062 | 318 | 312 | 43 | 21 | 0 % | 10.4× | 10.4× | 257 |
| +10–15 min | 3.994 | 306 | 297 | 40 | 18 | 5 % | 11.7× | 11.8× | 274 |
| +15–20 min | 4.056 | 302 | 296 | 40 | 20 | 0 % | 13.4× | 13.4× | 294 |
| +20–25 min | 4.067 | 305 | 295 | 42 | 19 | 0 % | 15.0× | 15.0× | 312 |
| +25–30 min | 4.070 | 304 | 292 | 41 | 20 | 0 % | 16.9× | 16.9× | 332 |
| +30–35 min | 4.060 | 305 | 297 | 40 | 21 | 0 % | 19.0× | 19.0× | 353 |
| +35–40 min | 4.020 | 305 | 292 | 317 | -258 | – | 1.66× | 20.6× | 92 |
| +40–45 min | 5.223 | 304 | 2.502 | 135 | -91 | 0 % | 0.04× | 2.16× | 0 |
| +45–50 min | 3 | 1 | 2 | 0 | 0 | – | 0.04× | 0.04× | 0 |
| +50–55 min | 1 | 0 | 1 | 0 | -0 | – | 0.04× | 0.04× | 0 |

**Grootste kopers** (eerste 24 u): `6E2G75Z3…` 149.9 SOL op +0 s; `8mct7xQy…` 30.9 SOL op +5 min; `7VhEtNLj…` 30.2 SOL op +5 min; `9F9hLL82…` 28.8 SOL op +5 min; `6SzVntTU…` 28.7 SOL op +5 min.

**Grootste verkopers** (eerste 24 u): `3YLYcXcm…` 178.0 SOL op +39 min; `3YLYcXcm…` 100.0 SOL op +39 min; `3YLYcXcm…` 7.8 SOL op +43 min; `9F9hLL82…` 5.6 SOL op +43 min; `29kWT6HQ…` 5.0 SOL op +43 min.

**Onze strategieën, beschrijvend** (geen replay, geen P&L):

- F7 (pool): eerste trigger op +39 min na de aanmaak, diepte 368 SOL, koers 5.88× de open; 1 trigger(s) in totaal. Koers daarna t.o.v. de trigger: na 150 slots: 0.27× (piek 1.00×), na 600 slots: 0.31× (piek 1.00×), na 2400 slots: 0.01× (piek 1.00×).
- M1: niet van toepassing (graduatie in de create-slot (buiten het M1-universum)).

#### BYTE — QuByte

[DexScreener](https://dexscreener.com/solana/QUALaRd6ZvZkt9NqJjVVN3MP23RtFNwDbJc3VDHcHwN) · mint `QUALaRd6ZvZkt9NqJjVVN3MP23RtFNwDbJc3VDHcHwN` · store 1054

- Aangemaakt op 2026-10-10 19:46 UTC door `FQBE3jXf…`.
- **Let op:** de store eindigt 23.2 u na de pool-opening (of de aanmaak); '24 u' en 'eind' zijn hier dus de stand op dat moment.
- Gegradueerd na 5 min met 705 curve-kopers en 729 SOL curve-volume.
- Eerste 24 u in de pool: 14.172 trades van 2.880 handelaren, 5.393 SOL volume (81 % organisch); piek 2.49× de openingsprijs, einde 24 u op 0.21× (8 % van de piek).
- Insiders (creator + kopers binnen 10 s na de aanmaak): 47; hun aandeel op +15 min 16 %, na 24 u 30 %; creator-verkopen in 24 u: 0.
- Op +15 min: koers 0.57× de open, netto instroom -14 SOL, 1.619 pool-traders, 1.902 SOL organisch volume.

**Tijdlijn** (koers = reserve-prijs t.o.v. de openingsprijs van de pool; diepte = reële SOL in de pool/curve aan het eind van het vak):

| venster | trades | kopers | verkopers | volume SOL | netto SOL | farmer-aandeel | koers eind | koers piek | diepte SOL |
|---|---|---|---|---|---|---|---|---|---|
| curve (vóór de pool) | 1.941 | 705 | 449 | 729 | 85 | – | 1.00× | 1.00× | 85 |
| +0–5 min | 2.896 | 974 | 647 | 1.200 | 31 | 10 % | 1.94× | 2.49× | 103 |
| +5–10 min | 1.919 | 563 | 714 | 730 | -35 | 11 % | 0.83× | 2.29× | 62 |
| +10–15 min | 620 | 222 | 198 | 221 | -11 | 22 % | 0.57× | 0.93× | 49 |
| +15–20 min | 285 | 102 | 101 | 108 | -4 | 20 % | 0.49× | 0.80× | 45 |
| +20–25 min | 258 | 109 | 87 | 145 | 54 | 24 % | 1.69× | 1.79× | 95 |
| +25–30 min | 487 | 186 | 139 | 239 | -1 | 28 % | 1.58× | 2.31× | 92 |
| +30–35 min | 516 | 181 | 158 | 241 | -15 | 32 % | 1.10× | 1.65× | 74 |
| +35–40 min | 442 | 164 | 106 | 260 | 9 | 31 % | 1.26× | 1.67× | 81 |
| +40–45 min | 242 | 103 | 70 | 90 | -6 | 34 % | 1.08× | 1.27× | 74 |
| +45–50 min | 341 | 128 | 116 | 152 | -10 | 15 % | 0.81× | 1.08× | 62 |
| +50–55 min | 299 | 103 | 97 | 143 | 29 | 29 % | 1.48× | 1.83× | 89 |
| +55–60 min | 182 | 81 | 51 | 71 | -10 | 33 % | 1.18× | 1.48× | 78 |
| +1–2 u | 1.270 | 352 | 312 | 500 | 2 | 22 % | 1.08× | 1.62× | 73 |
| +2–3 u | 604 | 149 | 190 | 203 | -6 | 21 % | 0.90× | 1.20× | 65 |
| +3–4 u | 461 | 178 | 130 | 244 | 32 | 20 % | 1.64× | 2.17× | 95 |
| +4–5 u | 194 | 65 | 60 | 82 | -11 | 19 % | 1.30× | 1.64× | 82 |
| +5–6 u | 112 | 48 | 42 | 42 | 2 | 22 % | 1.37× | 1.52× | 85 |
| +6–7 u | 53 | 15 | 26 | 16 | -6 | 9 % | 1.18× | 1.36× | 78 |
| +7–8 u | 123 | 46 | 37 | 47 | -2 | 13 % | 1.12× | 1.35× | 75 |
| +8–9 u | 99 | 21 | 32 | 25 | 3 | 16 % | 1.18× | 1.32× | 78 |
| +9–10 u | 260 | 76 | 57 | 72 | -20 | 13 % | 0.72× | 1.20× | 57 |
| +10–11 u | 288 | 70 | 75 | 62 | -14 | 21 % | 0.46× | 0.75× | 42 |
| +11–12 u | 117 | 44 | 38 | 24 | 3 | 19 % | 0.49× | 0.51× | 44 |
| +12–13 u | 148 | 47 | 47 | 44 | 1 | 36 % | 0.50× | 0.60× | 44 |
| +13–14 u | 64 | 23 | 23 | 12 | 3 | 36 % | 0.55× | 0.57× | 47 |
| +14–15 u | 109 | 27 | 28 | 18 | -5 | 19 % | 0.46× | 0.55× | 42 |
| +15–16 u | 169 | 27 | 28 | 17 | -0 | 32 % | 0.45× | 0.48× | 41 |
| +16–17 u | 286 | 33 | 45 | 36 | 12 | 39 % | 0.64× | 0.68× | 52 |
| +17–18 u | 237 | 40 | 61 | 47 | -9 | 20 % | 0.47× | 0.77× | 43 |
| +18–19 u | 349 | 102 | 75 | 130 | 28 | 20 % | 0.97× | 1.35× | 69 |
| +19–20 u | 247 | 47 | 43 | 52 | 8 | 43 % | 1.15× | 1.34× | 77 |
| +20–21 u | 283 | 62 | 104 | 94 | -45 | 19 % | 0.31× | 1.15× | 31 |
| +21–22 u | 166 | 39 | 37 | 23 | -4 | 42 % | 0.25× | 0.31× | 26 |
| +22–23 u | 38 | 6 | 22 | 5 | -4 | 7 % | 0.20× | 0.25× | 22 |
| +23–24 u | 8 | 3 | 5 | 0 | 0 | 10 % | 0.21× | 0.21× | 22 |

**Grootste kopers** (eerste 24 u): `ARu4n5mF…` 83.9 SOL op +35 s (farmer); `2tgUbS9U…` 66.3 SOL op +53 s (farmer); `EguxFSGu…` 48.2 SOL op +22 s (farmer); `GsZWRSRU…` 32.8 SOL op +3 min (farmer); `5ZgfM36J…` 29.2 SOL op +4 min (insider).

**Grootste verkopers** (eerste 24 u): `FkCXpYrp…` 11.0 SOL op +40 min; `FkCXpYrp…` 11.0 SOL op +29 min; `CyxHYV5C…` 10.1 SOL op +36 s; `AacnZiHm…` 9.9 SOL op +37 min; `94SiWU5b…` 8.6 SOL op +74 min.

**Onze strategieën, beschrijvend** (geen replay, geen P&L):

- F7 (curve): eerste trigger op +36 s na de aanmaak, diepte 53 SOL, koers 0.50× de open; 1 trigger(s) in totaal. Koers daarna t.o.v. de trigger: na 150 slots: 1.47× (piek 1.71×), na 600 slots: 1.67× (piek 1.71×), na 2400 slots: 4.27× (piek 5.00×).
- M1 (k = 2): trigger op +28 min na de pool-opening bij 2.00×, 279 organische kopers in 15 min, insider-aandeel 16 %, diepte 105 SOL. Koers daarna t.o.v. de trigger: na 15 min: 0.52× (piek 1.16×), na 1 u: 0.72× (piek 1.16×), na 6 u: 0.66× (piek 1.16×).
- M1 (k = 3): geen trigger binnen 6 u (68 kandidaat-momenten met ≥ 2× en instroom).
- M1+ op +15 min: koers 0.57×, netto instroom -14 SOL, 1.421 organische kopers, insider-aandeel 16 %, 705 curve-kopers → geen trigger; hype-veto (> 585 curve-kopers). Koers daarna t.o.v. +15 min: na 1 u: 2.32× (piek 4.04×), na 6 u: 2.31× (piek 4.04×), na 24 u: 0.36× (piek 4.04×).

#### baton — baton

[DexScreener](https://dexscreener.com/solana/s49Ssm3Zv9NkAzoVc9sV9p4TMWCESNVsfbVDsCypump) · mint `s49Ssm3Zv9NkAzoVc9sV9p4TMWCESNVsfbVDsCypump` · store 1054

- Aangemaakt op 2026-10-11 05:51 UTC door `HFywc8jR…`.
- **Let op:** de store eindigt 13.2 u na de pool-opening (of de aanmaak); '24 u' en 'eind' zijn hier dus de stand op dat moment.
- Gegradueerd **in de create-slot**: 1 wallet(s) kochten de hele curve (85 SOL) in dezelfde slot als de aanmaak — de dev-bundel-structuur.
- Eerste 24 u in de pool: 11.692 trades van 2.565 handelaren, 612 SOL volume (100 % organisch); piek 11.9× de openingsprijs, einde 24 u op 0.04× (0 % van de piek).
- Insiders (creator + kopers binnen 10 s na de aanmaak): 1; hun aandeel op +15 min > 100 % (verkopen boven de zichtbare kopen: transfers), na 24 u > 100 % (verkopen boven de zichtbare kopen: transfers); creator-verkopen in 24 u: 1 (0.0 SOL).
- Op +15 min: koers 0.04× de open, netto instroom -79 SOL, 2.565 pool-traders, 610 SOL organisch volume.

**Tijdlijn** (koers = reserve-prijs t.o.v. de openingsprijs van de pool; diepte = reële SOL in de pool/curve aan het eind van het vak):

| venster | trades | kopers | verkopers | volume SOL | netto SOL | farmer-aandeel | koers eind | koers piek | diepte SOL |
|---|---|---|---|---|---|---|---|---|---|
| +0–5 min | 4.238 | 338 | 310 | 205 | 185 | 0 % | 11.1× | 11.1× | 265 |
| +5–10 min | 4.113 | 320 | 323 | 257 | -153 | 0 % | 2.32× | 11.9× | 112 |
| +10–15 min | 3.341 | 232 | 2.505 | 150 | -110 | 0 % | 0.04× | 2.77× | 0 |

**Grootste kopers** (eerste 24 u): `5MCnK45i…` 169.9 SOL op +0 s; `7Ud5vgBF…` 13.8 SOL op +1 min; `3RnALvN6…` 12.3 SOL op +1 min; `6q5oSMu5…` 12.2 SOL op +1 min; `5kFYAGxn…` 11.9 SOL op +1 min.

**Grootste verkopers** (eerste 24 u): `GHHPmm8S…` 181.0 SOL op +7 min; `GHHPmm8S…` 14.5 SOL op +11 min; `3hDAFWWi…` 7.2 SOL op +11 min; `7Ud5vgBF…` 7.1 SOL op +11 min; `6q5oSMu5…` 5.1 SOL op +11 min.

**Onze strategieën, beschrijvend** (geen replay, geen P&L):

- F7 (pool): eerste trigger op +7 min na de aanmaak, diepte 275 SOL, koers 1.69× de open; 1 trigger(s) in totaal. Koers daarna t.o.v. de trigger: na 150 slots: 0.95× (piek 1.02×), na 600 slots: 1.09× (piek 1.09×), na 2400 slots: 0.03× (piek 1.63×).
- M1: niet van toepassing (graduatie in de create-slot (buiten het M1-universum)).

#### CYBERLEEK — CyberLeek

[DexScreener](https://dexscreener.com/solana/Sd8R2j4kf7eK2ABSFEWyhBWfmJ6PoKu6k43jjRLpump) · mint `Sd8R2j4kf7eK2ABSFEWyhBWfmJ6PoKu6k43jjRLpump` · store 1052

- Aangemaakt op 2026-10-08 12:45 UTC door `2GLyiiR5…`.
- Gegradueerd **in de create-slot**: 1 wallet(s) kochten de hele curve (85 SOL) in dezelfde slot als de aanmaak — de dev-bundel-structuur.
- Eerste 24 u in de pool: 49.141 trades van 3.167 handelaren, 15.815 SOL volume (99 % organisch); piek 6.238× de openingsprijs, einde 24 u op 0.05× (0 % van de piek).
- Insiders (creator + kopers binnen 10 s na de aanmaak): 1; hun aandeel op +15 min 80 %, na 24 u > 100 % (verkopen boven de zichtbare kopen: transfers); creator-verkopen in 24 u: 2 (142.7 SOL).
- Op +15 min: koers 3.712× de open, netto instroom 5.130 SOL, 2.311 pool-traders, 5.656 SOL organisch volume.

**Tijdlijn** (koers = reserve-prijs t.o.v. de openingsprijs van de pool; diepte = reële SOL in de pool/curve aan het eind van het vak):

| venster | trades | kopers | verkopers | volume SOL | netto SOL | farmer-aandeel | koers eind | koers piek | diepte SOL |
|---|---|---|---|---|---|---|---|---|---|
| +0–5 min | 326 | 289 | 8 | 4.922 | 4.905 | – | 3.393× | 3.393× | 4.938 |
| +5–10 min | 3.661 | 2.067 | 56 | 512 | 173 | 13 % | 3.638× | 3.643× | 5.114 |
| +10–15 min | 2.363 | 1.756 | 56 | 401 | 53 | 28 % | 3.712× | 3.713× | 5.166 |
| +15–20 min | 292 | 277 | 5 | 29 | 15 | – | 3.733× | 3.736× | 5.182 |
| +20–25 min | 297 | 284 | 7 | 24 | 10 | – | 3.747× | 3.749× | 5.191 |
| +25–30 min | 297 | 284 | 8 | 24 | 9 | 2 % | 3.761× | 3.763× | 5.200 |
| +30–35 min | 293 | 279 | 4 | 29 | 15 | 3 % | 3.782× | 3.784× | 5.216 |
| +35–40 min | 306 | 294 | 5 | 25 | 12 | 3 % | 3.800× | 3.802× | 5.228 |
| +40–45 min | 304 | 293 | 5 | 25 | 14 | – | 3.821× | 3.822× | 5.242 |
| +45–50 min | 302 | 285 | 7 | 29 | 13 | 2 % | 3.840× | 3.842× | 5.256 |
| +50–55 min | 286 | 272 | 7 | 25 | 12 | – | 3.857× | 3.859× | 5.267 |
| +55–60 min | 286 | 274 | 6 | 24 | 9 | – | 3.871× | 3.873× | 5.277 |
| +1–2 u | 3.562 | 2.428 | 20 | 298 | 137 | 0 % | 4.074× | 4.075× | 5.414 |
| +2–3 u | 3.267 | 2.275 | 15 | 270 | 129 | 0 % | 4.266× | 4.268× | 5.541 |
| +3–4 u | 3.530 | 2.692 | 18 | 286 | 138 | – | 4.482× | 4.482× | 5.680 |
| +4–5 u | 3.464 | 2.685 | 11 | 288 | 137 | – | 4.699× | 4.699× | 5.817 |
| +5–6 u | 2.906 | 2.279 | 11 | 291 | 134 | 1 % | 4.916× | 4.916× | 5.950 |
| +6–7 u | 3.173 | 2.271 | 12 | 285 | 135 | 0 % | 5.140× | 5.140× | 6.085 |
| +7–8 u | 3.141 | 2.447 | 11 | 274 | 133 | 0 % | 5.365× | 5.365× | 6.217 |
| +8–9 u | 3.538 | 2.747 | 8 | 283 | 132 | 0 % | 5.593× | 5.593× | 6.349 |
| +9–10 u | 3.500 | 2.450 | 24 | 308 | 102 | 0 % | 5.772× | 5.775× | 6.450 |
| +10–11 u | 3.353 | 2.323 | 13 | 295 | 142 | 0 % | 6.028× | 6.028× | 6.592 |
| +11–12 u | 3.566 | 2.624 | 9 | 118 | 76 | – | 6.167× | 6.167× | 6.668 |
| +12–13 u | 3.120 | 1.963 | 894 | 6.749 | -6.654 | 0 % | 0.05× | 6.238× | 1 |
| +13–14 u | 2 | 1 | 1 | 0 | 0 | – | 0.05× | 0.05× | 1 |
| +17–18 u | 1 | 0 | 1 | 0 | -0 | – | 0.05× | 0.05× | 1 |
| +19–20 u | 1 | 0 | 1 | 0 | -0 | – | 0.05× | 0.05× | 1 |
| +20–21 u | 1 | 0 | 1 | 0 | -0 | – | 0.05× | 0.05× | 1 |
| +22–23 u | 1 | 1 | 0 | 0 | 0 | – | 0.05× | 0.05× | 1 |
| +23–24 u | 2 | 0 | 2 | 0 | -0 | – | 0.05× | 0.05× | 1 |

**Grootste kopers** (eerste 24 u): `AMkUTwhE…` 4.859 SOL op +0 s; `4WaQkCVM…` 634.1 SOL op +18 s; `4NA1SWzb…` 618.4 SOL op +18 s; `8hGmwBZC…` 606.2 SOL op +1 min; `5Eu7t8Zk…` 603.8 SOL op +30 s.

**Grootste verkopers** (eerste 24 u): `GFenRnGz…` 776.3 SOL op +12.6 u; `8UZvhCSz…` 615.3 SOL op +12.6 u; `9TRADxMp…` 499.7 SOL op +12.6 u; `4u8wcYM9…` 413.9 SOL op +12.6 u; `DsxtZrcH…` 348.5 SOL op +12.6 u.

**Onze strategieën, beschrijvend** (geen replay, geen P&L):

- F7: geen trigger (geen verkoop ≥ max(3 SOL, 4 %) met instroom ervoor, zonder creator-verkoop, op de curve bij 20–75 SOL of in de pool bij ≥ 150 SOL).
- M1: niet van toepassing (graduatie in de create-slot (buiten het M1-universum)).

#### SWOLF — Starknet Wolf

[DexScreener](https://dexscreener.com/solana/8sx4iwmwGfJsLpRNRhbJBJr5yfdAHZkKQ75jU1Wopump) · mint `8sx4iwmwGfJsLpRNRhbJBJr5yfdAHZkKQ75jU1Wopump` · store 1054

- Aangemaakt op 2026-10-10 20:43 UTC door `HDqopgNR…`.
- **Let op:** de store eindigt 22.4 u na de pool-opening (of de aanmaak); '24 u' en 'eind' zijn hier dus de stand op dat moment.
- Gegradueerd **in de create-slot**: 1 wallet(s) kochten de hele curve (85 SOL) in dezelfde slot als de aanmaak — de dev-bundel-structuur.
- Eerste 24 u in de pool: 71.986 trades van 2.070 handelaren, 1.831 SOL volume (100 % organisch); piek 218× de openingsprijs, einde 24 u op 218× (100 % van de piek).
- Insiders (creator + kopers binnen 10 s na de aanmaak): 1; hun aandeel op +15 min 81 %, na 24 u 80 %; creator-verkopen in 24 u: 0.
- Op +15 min: koers 77.5× de open, netto instroom 654 SOL, 528 pool-traders, 664 SOL organisch volume.

**Tijdlijn** (koers = reserve-prijs t.o.v. de openingsprijs van de pool; diepte = reële SOL in de pool/curve aan het eind van het vak):

| venster | trades | kopers | verkopers | volume SOL | netto SOL | farmer-aandeel | koers eind | koers piek | diepte SOL |
|---|---|---|---|---|---|---|---|---|---|
| +0–5 min | 138 | 110 | 26 | 653 | 650 | – | 76.2× | 76.3× | 725 |
| +5–10 min | 284 | 216 | 68 | 6 | 1 | – | 76.9× | 76.9× | 728 |
| +10–15 min | 281 | 217 | 59 | 5 | 3 | 7 % | 77.5× | 77.5× | 731 |
| +15–20 min | 272 | 216 | 55 | 4 | 2 | – | 77.9× | 78.0× | 733 |
| +20–25 min | 271 | 213 | 57 | 5 | 2 | – | 78.4× | 78.5× | 735 |
| +25–30 min | 269 | 212 | 55 | 5 | 2 | 7 % | 78.8× | 78.8× | 737 |
| +30–35 min | 274 | 219 | 54 | 5 | 3 | – | 79.4× | 79.4× | 740 |
| +35–40 min | 269 | 213 | 55 | 4 | 2 | – | 79.7× | 79.7× | 741 |
| +40–45 min | 264 | 208 | 55 | 4 | 2 | – | 80.1× | 80.1× | 743 |
| +45–50 min | 268 | 212 | 55 | 4 | 2 | – | 80.5× | 80.5× | 745 |
| +50–55 min | 270 | 215 | 55 | 4 | 2 | 0 % | 80.8× | 80.8× | 747 |
| +55–60 min | 266 | 211 | 55 | 5 | 2 | 0 % | 81.2× | 81.2× | 748 |
| +1–2 u | 3.221 | 1.803 | 601 | 53 | 26 | 1 % | 86.7× | 86.7× | 774 |
| +2–3 u | 3.241 | 1.788 | 589 | 52 | 23 | 0 % | 91.8× | 91.8× | 797 |
| +3–4 u | 3.227 | 1.774 | 577 | 54 | 24 | 0 % | 97.2× | 97.3× | 821 |
| +4–5 u | 3.224 | 1.770 | 570 | 53 | 21 | 0 % | 102× | 102× | 842 |
| +5–6 u | 3.227 | 1.786 | 582 | 54 | 25 | 0 % | 108× | 108× | 866 |
| +6–7 u | 3.218 | 1.761 | 562 | 52 | 25 | 0 % | 114× | 114× | 891 |
| +7–8 u | 3.230 | 1.766 | 564 | 53 | 24 | – | 120× | 120× | 915 |
| +8–9 u | 3.239 | 1.794 | 591 | 54 | 24 | – | 127× | 127× | 939 |
| +9–10 u | 3.234 | 1.767 | 563 | 53 | 24 | – | 133× | 133× | 962 |
| +10–11 u | 3.219 | 1.787 | 583 | 54 | 23 | 0 % | 139× | 139× | 985 |
| +11–12 u | 3.203 | 1.763 | 565 | 50 | 19 | – | 144× | 144× | 1.004 |
| +12–13 u | 3.181 | 1.760 | 557 | 55 | 24 | – | 151× | 151× | 1.027 |
| +13–14 u | 3.226 | 1.772 | 572 | 52 | 22 | – | 157× | 157× | 1.049 |
| +14–15 u | 3.240 | 1.782 | 580 | 53 | 25 | – | 165× | 165× | 1.074 |
| +15–16 u | 3.212 | 1.759 | 562 | 57 | 27 | – | 173× | 173× | 1.101 |
| +16–17 u | 3.221 | 1.767 | 567 | 52 | 25 | – | 181× | 181× | 1.125 |
| +17–18 u | 3.233 | 1.776 | 578 | 54 | 25 | – | 188× | 188× | 1.150 |
| +18–19 u | 3.218 | 1.787 | 588 | 53 | 23 | – | 196× | 196× | 1.173 |
| +19–20 u | 3.192 | 1.761 | 562 | 42 | 14 | – | 200× | 201× | 1.187 |
| +20–21 u | 3.234 | 1.794 | 595 | 53 | 21 | – | 208× | 208× | 1.208 |
| +21–22 u | 3.243 | 1.772 | 574 | 53 | 23 | – | 215× | 215× | 1.231 |
| +22–23 u | 1.177 | 896 | 234 | 20 | 8 | – | 218× | 218× | 1.239 |

**Grootste kopers** (eerste 24 u): `9CLjD5o5…` 648.0 SOL op +0 s; `CLza2fpg…` 46.3 SOL op +6 min; `BAHnhL8h…` 46.2 SOL op +7 min; `7CAvDA8o…` 45.9 SOL op +8 min; `vpEEiygW…` 44.2 SOL op +5 min.

**Grootste verkopers** (eerste 24 u): `FvGnZp71…` 1.0 SOL op +5 min; `BM9CcyEr…` 0.5 SOL op +15.5 u; `2yiawYcD…` 0.5 SOL op +5 min; `FtfQi1xH…` 0.5 SOL op +16.0 u; `HPxryCTo…` 0.4 SOL op +15.3 u.

**Onze strategieën, beschrijvend** (geen replay, geen P&L):

- F7: geen trigger (geen verkoop ≥ max(3 SOL, 4 %) met instroom ervoor, zonder creator-verkoop, op de curve bij 20–75 SOL of in de pool bij ≥ 150 SOL).
- M1: niet van toepassing (graduatie in de create-slot (buiten het M1-universum)).

#### CLAUDIA — Claudia the AI

[DexScreener](https://dexscreener.com/solana/D8AWqghypBX9XxFAChDvwi73qXt3qsUd12AoHEE9NUS6) · mint `D8AWqghypBX9XxFAChDvwi73qXt3qsUd12AoHEE9NUS6` · store 1054

- Aangemaakt op 2026-10-11 09:01 UTC door `EXwP1XWa…`.
- **Let op:** de store eindigt 10.0 u na de pool-opening (of de aanmaak); '24 u' en 'eind' zijn hier dus de stand op dat moment.
- Gegradueerd **in de create-slot**: 1 wallet(s) kochten de hele curve (85 SOL) in dezelfde slot als de aanmaak — de dev-bundel-structuur.
- Eerste 24 u in de pool: 43.589 trades van 2.557 handelaren, 1.324 SOL volume (100 % organisch); piek 42.8× de openingsprijs, einde 24 u op 0.04× (0 % van de piek).
- Insiders (creator + kopers binnen 10 s na de aanmaak): 1; hun aandeel op +15 min 84 %, na 24 u > 100 % (verkopen boven de zichtbare kopen: transfers); creator-verkopen in 24 u: 1 (0.0 SOL).
- Op +15 min: koers 14.5× de open, netto instroom 224 SOL, 328 pool-traders, 277 SOL organisch volume.

**Tijdlijn** (koers = reserve-prijs t.o.v. de openingsprijs van de pool; diepte = reële SOL in de pool/curve aan het eind van het vak):

| venster | trades | kopers | verkopers | volume SOL | netto SOL | farmer-aandeel | koers eind | koers piek | diepte SOL |
|---|---|---|---|---|---|---|---|---|---|
| +0–5 min | 3.965 | 321 | 308 | 183 | 169 | 0 % | 9.84× | 9.86× | 249 |
| +5–10 min | 4.097 | 302 | 296 | 48 | 29 | 0 % | 12.3× | 12.3× | 281 |
| +10–15 min | 4.088 | 302 | 303 | 46 | 26 | 0 % | 14.5× | 14.5× | 306 |
| +15–20 min | 4.008 | 299 | 291 | 47 | 29 | 0 % | 17.1× | 17.1× | 335 |
| +20–25 min | 4.076 | 303 | 293 | 46 | 28 | 0 % | 19.9× | 19.9× | 362 |
| +25–30 min | 4.044 | 301 | 292 | 45 | 26 | 0 % | 22.7× | 22.7× | 388 |
| +30–35 min | 4.043 | 302 | 288 | 45 | 27 | 0 % | 25.7× | 25.7× | 414 |
| +35–40 min | 4.023 | 302 | 289 | 45 | 26 | 0 % | 28.9× | 28.9× | 440 |
| +40–45 min | 4.472 | 301 | 292 | 141 | 63 | 0 % | 37.2× | 37.2× | 502 |
| +45–50 min | 5.317 | 307 | 1.349 | 664 | -484 | 0 % | 0.13× | 42.8× | 13 |
| +50–55 min | 1.445 | 0 | 1.445 | 13 | -13 | 0 % | 0.04× | 0.13× | 0 |
| +55–60 min | 1 | 0 | 1 | 0 | -0 | – | 0.04× | 0.04× | 0 |
| +1–2 u | 7 | 2 | 4 | 0 | 0 | – | 0.04× | 0.04× | 0 |
| +9–10 u | 3 | 1 | 1 | 0 | 0 | – | 0.04× | 0.04× | 0 |

**Grootste kopers** (eerste 24 u): `4imgFChp…` 151.9 SOL op +0 s; `HZi7rnxX…` 61.9 SOL op +3 min; `EfLCsfnJ…` 60.2 SOL op +8 s; `2ru6Mjsy…` 60.1 SOL op +3 min; `FReRTBhb…` 57.0 SOL op +3 min.

**Grootste verkopers** (eerste 24 u): `5NpcZzQo…` 445.1 SOL op +48 min; `HZi7rnxX…` 8.9 SOL op +50 min; `2ru6Mjsy…` 7.3 SOL op +50 min; `EfLCsfnJ…` 5.2 SOL op +50 min; `7ooSb5H6…` 4.0 SOL op +50 min.

**Onze strategieën, beschrijvend** (geen replay, geen P&L):

- F7 (pool): eerste trigger op +48 min na de aanmaak, diepte 539 SOL, koers 1.62× de open; 1 trigger(s) in totaal. Koers daarna t.o.v. de trigger: na 150 slots: 1.11× (piek 1.11×), na 600 slots: 0.03× (piek 1.32×), na 2400 slots: 0.03× (piek 1.32×).
- M1: niet van toepassing (graduatie in de create-slot (buiten het M1-universum)).

#### ZCAT — ZCAT

[DexScreener](https://dexscreener.com/solana/7DxkGn2NkrtGNhmR9HHp7RTEceejwtT3f8GaSgWBpump) · mint `7DxkGn2NkrtGNhmR9HHp7RTEceejwtT3f8GaSgWBpump` · store 1054

- Aangemaakt op 2026-10-10 19:48 UTC door `CS3EREmM…`.
- **Let op:** de store eindigt 23.3 u na de pool-opening (of de aanmaak); '24 u' en 'eind' zijn hier dus de stand op dat moment.
- Niet gegradueerd in deze store: de curve kwam tot 0 SOL reële reserves met 284 kopers en 2 SOL volume; piek 3.24× de eerste prijs, einde op 0.85×.
- Insiders (creator + kopers binnen 10 s na de aanmaak): 11; hun aandeel op +15 min –, na 24 u > 100 % (verkopen boven de zichtbare kopen: transfers); creator-verkopen in 24 u: 1 (0.0 SOL).

**Tijdlijn** (koers = reserve-prijs t.o.v. de openingsprijs van de pool; diepte = reële SOL in de pool/curve aan het eind van het vak):

| venster | trades | kopers | verkopers | volume SOL | netto SOL | farmer-aandeel | koers eind | koers piek | diepte SOL |
|---|---|---|---|---|---|---|---|---|---|
| +0–5 min | 296 | 159 | 65 | 1 | 0 | – | 2.65× | 2.75× | 0 |
| +5–10 min | 504 | 163 | 164 | 1 | -0 | – | 1.29× | 3.24× | 0 |
| +10–15 min | 67 | 15 | 39 | 0 | -0 | – | 1.07× | 1.44× | 0 |
| +15–20 min | 25 | 4 | 21 | 0 | -0 | – | 0.92× | 1.07× | 0 |
| +20–25 min | 5 | 0 | 5 | 0 | -0 | – | 0.90× | 0.91× | 0 |
| +25–30 min | 6 | 0 | 6 | 0 | -0 | – | 0.88× | 0.90× | 0 |
| +35–40 min | 2 | 0 | 2 | 0 | -0 | – | 0.87× | 0.87× | 0 |
| +40–45 min | 1 | 0 | 1 | 0 | -0 | – | 0.87× | 0.87× | 0 |
| +45–50 min | 1 | 0 | 1 | 0 | -0 | – | 0.87× | 0.87× | 0 |
| +50–55 min | 1 | 0 | 1 | 0 | -0 | – | 0.87× | 0.87× | 0 |
| +55–60 min | 1 | 0 | 1 | 0 | -0 | – | 0.87× | 0.87× | 0 |
| +1–2 u | 3 | 0 | 2 | 0 | -0 | – | 0.87× | 0.87× | 0 |
| +2–3 u | 2 | 1 | 0 | 0 | 0 | – | 0.87× | 0.87× | 0 |
| +3–4 u | 1 | 0 | 1 | 0 | -0 | – | 0.86× | 0.86× | 0 |
| +4–5 u | 3 | 1 | 2 | 0 | -0 | – | 0.86× | 0.86× | 0 |
| +5–6 u | 1 | 0 | 1 | 0 | -0 | – | 0.86× | 0.86× | 0 |
| +8–9 u | 1 | 0 | 1 | 0 | -0 | – | 0.86× | 0.86× | 0 |
| +10–11 u | 3 | 0 | 3 | 0 | -0 | – | 0.86× | 0.86× | 0 |
| +11–12 u | 2 | 1 | 1 | 0 | 0 | – | 0.86× | 0.92× | 0 |
| +12–13 u | 3 | 0 | 3 | 0 | -0 | – | 0.85× | 0.86× | 0 |
| +15–16 u | 1 | 0 | 1 | 0 | -0 | – | 0.85× | 0.85× | 0 |
| +16–17 u | 2 | 0 | 2 | 0 | -0 | – | 0.85× | 0.85× | 0 |
| +19–20 u | 2 | 1 | 1 | 0 | -0 | – | 0.85× | 0.85× | 0 |
| +21–22 u | 1 | 0 | 1 | 0 | -0 | – | 0.85× | 0.85× | 0 |

**Grootste kopers** (eerste 24 u): `G9gmk9ms…` 0.1 SOL op +1 min; `Hzc6DqRm…` 0.0 SOL op +2 min; `AmwJToZR…` 0.0 SOL op +16 s; `Fhbh1DTU…` 0.0 SOL op +3 s (insider); `CS3EREmM…` 0.0 SOL op +0 s (creator).

**Grootste verkopers** (eerste 24 u): `G9gmk9ms…` 0.1 SOL op +2 min; `Fhbh1DTU…` 0.0 SOL op +4 s (insider); `BYrSGYCC…` 0.0 SOL op +6 min; `Hzc6DqRm…` 0.0 SOL op +2 min; `AmwJToZR…` 0.0 SOL op +23 s.

**Onze strategieën, beschrijvend** (geen replay, geen P&L):

- F7: geen trigger (geen verkoop ≥ max(3 SOL, 4 %) met instroom ervoor, zonder creator-verkoop, op de curve bij 20–75 SOL of in de pool bij ≥ 150 SOL).
- M1: niet van toepassing (niet gegradueerd).

#### HIGGS — Higgspad

[DexScreener](https://dexscreener.com/solana/EgeMmDHNGekF2NsYfqNLmgbYuNt6aX66XNTcZPbopump) · mint `EgeMmDHNGekF2NsYfqNLmgbYuNt6aX66XNTcZPbopump` · store 1054

- Aangemaakt op 2026-10-11 18:40 UTC door `GErNmJAy…`.
- **Let op:** de store eindigt 24 min na de pool-opening (of de aanmaak); '24 u' en 'eind' zijn hier dus de stand op dat moment.
- Gegradueerd **in de create-slot**: 1 wallet(s) kochten de hele curve (85 SOL) in dezelfde slot als de aanmaak — de dev-bundel-structuur.
- Eerste 24 u in de pool: 692 trades van 53 handelaren, 1.026 SOL volume (100 % organisch); piek 163× de openingsprijs, einde 24 u op 163× (100 % van de piek).
- Insiders (creator + kopers binnen 10 s na de aanmaak): 1; hun aandeel op +15 min 81 %, na 24 u 81 %; creator-verkopen in 24 u: 0.
- Op +15 min: koers 163× de open, netto instroom 992 SOL, 53 pool-traders, 1.011 SOL organisch volume.

**Tijdlijn** (koers = reserve-prijs t.o.v. de openingsprijs van de pool; diepte = reële SOL in de pool/curve aan het eind van het vak):

| venster | trades | kopers | verkopers | volume SOL | netto SOL | farmer-aandeel | koers eind | koers piek | diepte SOL |
|---|---|---|---|---|---|---|---|---|---|
| +0–5 min | 159 | 50 | 11 | 995 | 990 | 0 % | 161× | 161× | 1.063 |
| +5–10 min | 139 | 32 | 13 | 10 | 0 | 1 % | 162× | 162× | 1.065 |
| +10–15 min | 140 | 31 | 8 | 6 | 2 | 3 % | 163× | 163× | 1.067 |
| +15–20 min | 143 | 31 | 14 | 9 | 1 | 7 % | 163× | 163× | 1.068 |
| +20–25 min | 111 | 30 | 8 | 6 | 1 | 0 % | 163× | 163× | 1.068 |

**Grootste kopers** (eerste 24 u): `2oqQeRYz…` 986.0 SOL op +0 s; `Didnsop7…` 1.6 SOL op +7 s; `3LpLpurK…` 1.4 SOL op +1 min; `98PoyUtK…` 1.4 SOL op +18 s; `FJnw28E5…` 1.3 SOL op +35 s (farmer).

**Grootste verkopers** (eerste 24 u): `2yiawYcD…` 1.0 SOL op +5 min; `32HNKzfw…` 0.5 SOL op +5 min; `5QMx2Gcd…` 0.5 SOL op +7 min; `321JJrps…` 0.5 SOL op +3 min; `321JJrps…` 0.5 SOL op +19 min.

**Onze strategieën, beschrijvend** (geen replay, geen P&L):

- F7: geen trigger (geen verkoop ≥ max(3 SOL, 4 %) met instroom ervoor, zonder creator-verkoop, op de curve bij 20–75 SOL of in de pool bij ≥ 150 SOL).
- M1: niet van toepassing (graduatie in de create-slot (buiten het M1-universum)).

### Beperkingen

- Elke store is één epoch (≈ 2 dagen) en kent alleen zijn eigen events: een token dat in de vorige epoch is aangemaakt en in deze gradueerde, mist hier zijn curve en aanmaak (geen curve-kopers, geen dev-bundel-vlag, insiders onbekend); de keuze op "meeste handel" pakt dan de epoch met de pool. Dat staat per token bij de store-tag.
- De F7- en M1-regels zijn de definities van het toernooi (triggers), zonder replay: geen vulling, kosten of exit, dus geen P&L.
- Transfers zijn onzichtbaar: een insider-aandeel boven 100 % betekent dat insiders meer verkochten dan ze zichtbaar kochten.
