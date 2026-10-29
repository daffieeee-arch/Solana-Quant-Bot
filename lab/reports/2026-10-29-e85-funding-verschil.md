# E85 — cross-venue-funding-verschil SOL-perp, delta-neutraal (Velocity ↔ Hyperliquid): kaart op 90 dagen publieke data (29 okt 2026)

Registerrij E85; plan v0.4 `research/e85-funding-spread-2026-10-29/e85-plan-2026-10-29.md` (cddadd63…; v0.1–v0.3 bewaard); **verkend, 0 blikken** (alle 27 cellen vast in het plan vóór het lezen; geen cel gekozen); **geen oordeel**; eigenaarsbesluit 29 okt (drie nieuwe ideeënklassen, PAPER ONLY, gratis publieke data, mandaat 28 okt). Alle getallen uit de gearchiveerde uitvoer (§6).

## 0. Kort
- **Feit:** het funding-verschil Hyperliquid − Velocity op SOL-perp bestaat en is persistent: gem −0,229 bps/uur (Velocity +0,316, Hyperliquid +0,087; Binance-referentie +0,046), 19,4 % van de uren > 0, autocorrelatie lag 1 +0,953, lag 24 +0,623 (90 dagen, 31 jul – 28 okt, 2.158 uren).
- **Feit (kaart):** van de 24 regelcellen hebben er 10 een gemiddelde boven de staking-lat (0,010 SOL/dag op 50 SOL) met beide bootstrap-ondergrenzen > 0; de cellen zijn sterk gecorreleerd. **Rode vlag (LAB review):** de W24/θ0-cellen (+0,082 / +0,165 SOL/dag) liggen bóven de orakelcel (+0,040, perfecte kennis van d per uur) — een regel kan de orakel-bovengrens op funding niet overtreffen; hun surplus komt uit de basisterm bij wissels (funding-only +0,029 / +0,057): mean-reversion van de mark-premie tussen venues, afgerekend op uursluitkoersen — een ander mechanisme dan de hypothese en zo niet uitvoerbaar (reconstructie). De funding-gedragen cellen zijn W72/θ0 (+0,020 / +0,040; ≈ funding-only) en W168/θ0 (+0,026 / +0,051; funding-only +0,016 / +0,033).
- **Bewijsbaarheid (plan §4, T uit de blok-sd als minimum):** tegen de staking-lat 57–322 dagen; tegen nul 66–143 dagen (iid 37–81). Niets is binnen 20 dagen bewijsbaar; tegen de lat niets binnen twee maanden.
- **Geen oordeel; geen celkeuze (1 blik).** Of een forward-test van 2–4 maanden voor ≈ 0,02–0,05 SOL/dag onder de eigenaarslat zinvol is, en of daarvoor een `paper-funding-ledger` gebouwd wordt (zonder uitvoerbaar pad is dit een kaart), is een coördinator-/eigenaarsbeslissing (LAB legt hem voor).

## 1. Opzet (feit)
Bronnen (plan §1, letterlijk; gratis, publiek, geen sleutel, curl-standaard-UA): Velocity (ex-Drift) Data API `GET /market/SOL-PERP/fundingRates/{y}/{m}/{d}?format=csv` en `/candles/60`; Hyperliquid info-API `fundingHistory` en `candleSnapshot` 1h; Binance `/fapi/v1/fundingRate` als referentie; tien docpagina's als ontvangen. Ruwe pull door de coördinator op 29 okt 16:15–16:18Z naar `wallets/e85-raw-2026-10-29/` (116 bestanden, SHA256SUMS 6aff0f5b… vóór enige bewerking; pull-log 4e671b0b…). Venster [2026-07-31, 2026-10-29). Fees (docs, als ontvangen): Velocity SOL-PERP taker 6 bps, Hyperliquid tier 0 taker 4,5 bps → 21 bps rondreis; funding Velocity per uur met dead zone 5 bps en vloer oracle/3333, lazy settlement; Hyperliquid per uur = 1/8 van de 8-uursrate.

## 2. Parse (feit; `e85_parse_2026-10-29.out` 76d77110…)
2.159 Velocity-records (0 dubbel); **eenheidscontrole** op 31 jul (24 records, de enige lees-vóór-tabel-stap): fractie per uur = `fundingRate / oraclePriceTwap`, mediaan |ratio − 1| tegen de uurformule met dead zone 0,000 → OK (de 1e9/1e6-schaling van plan v0.1–v0.3 gaf ≈ 0 en stopte parser v1 fail-closed, rc 2; de gelogde diagnose `e85_unit_diag.py` e2b37e51… → 42a9b1f8… legde de eenheid vast: ratio 0,714 op alle 24 records = (|div| − 5 + 3 bps)/(|div| + 3 bps)). ts mod 3600: mediaan 55 s, 100 % binnen 15 min; ts-afstanden mediaan 60,0 min, één gat > 1,5 u (1,61 u) → per uur; 0 uren met > 1 record; kaarsen 3 geldig / 3 uitgesloten (400); Hyperliquid funding 2.160 uren, kaarsen 2.161 (lege pagina 2 uitgesloten), predictedFundings bewaard; Binance 2.168 uren; raster 2.162 uur-einden, Velocity ongedekt 0,14 % (≤ 5 %). Reader: 2.158 uren met beide venues en prijzen, 90 dagen.

## 3. Celtabel (feit; `e85_read_buckets_2026-10-29.out` b9899435…, `cells.json` 9db3027a…; netto SOL per UTC-dag op 50 SOL = funding − wisselkosten − basis; iid en 7-daagse blok-bootstrap 10.000 seed 7; T = (2,8 × sd / (gem − lat))² met lat 0,010 resp. 0, iid-sd / blok-sd)

| cel | gem SOL/dag | sd | iid [2,5; 97,5] | blok7 [2,5; 97,5] | funding-only | fees × 2 | wissels | in positie | slechtste dag | max DD | T vs lat | T vs 0 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| W24/θ0.0/L1 | +0,082 | 0,369 | [+0,023; +0,168] | [+0,021; +0,173] | +0,028 | +0,077 | 20 | 98.8 % | -0,198 | 0,198 | 204 / 201 | 157 / 155 |
| W24/θ0.0/L2 | +0,165 | 0,738 | [+0,047; +0,335] | [+0,042; +0,345] | +0,057 | +0,153 | 20 | 98.8 % | -0,395 | 0,395 | 178 / 176 | 157 / 155 |
| W24/θ0.5/L1 | +0,047 | 0,332 | [-0,001; +0,125] | [-0,007; +0,137] | +0,026 | +0,042 | 18 | 46.7 % | -0,496 | 0,797 | 620 / 683 | 386 / 425 |
| W24/θ0.5/L2 | +0,095 | 0,664 | [-0,002; +0,250] | [-0,013; +0,275] | +0,051 | +0,084 | 18 | 46.7 % | -0,993 | 1,594 | 482 / 531 | 386 / 425 |
| W24/θ1.0/L1 | +0,022 | 0,070 | [+0,009; +0,038] | [+0,005; +0,046] | +0,014 | +0,019 | 10 | 16.5 % | -0,134 | 0,134 | 255 / 500 | 78 / 153 |
| W24/θ1.0/L2 | +0,045 | 0,141 | [+0,018; +0,076] | [+0,010; +0,091] | +0,028 | +0,039 | 10 | 16.5 % | -0,268 | 0,268 | 129 / 253 | 78 / 153 |
| W24/θ2.0/L1 | +0,009 | 0,099 | [-0,010; +0,031] | [-0,008; +0,032] | +0,005 | +0,008 | 6 | 4.1 % | -0,487 | 0,487 | — / — | 888 / 822 |
| W24/θ2.0/L2 | +0,019 | 0,199 | [-0,020; +0,063] | [-0,016; +0,063] | +0,010 | +0,015 | 6 | 4.1 % | -0,974 | 0,974 | 4124 / 3815 | 888 / 821 |
| W72/θ0.0/L1 | +0,020 | 0,045 | [+0,011; +0,029] | [+0,009; +0,035] | +0,022 | +0,017 | 12 | 96.6 % | -0,166 | 0,192 | 155 / 322 | 39 / 81 |
| W72/θ0.0/L2 | +0,040 | 0,089 | [+0,021; +0,058] | [+0,017; +0,070] | +0,044 | +0,033 | 12 | 96.6 % | -0,331 | 0,384 | 69 / 143 | 39 / 81 |
| W72/θ0.5/L1 | +0,056 | 0,303 | [+0,016; +0,125] | [+0,012; +0,133] | +0,020 | +0,053 | 10 | 45.6 % | -0,192 | 0,192 | 343 / 325 | 231 / 219 |
| W72/θ0.5/L2 | +0,111 | 0,605 | [+0,031; +0,250] | [+0,024; +0,267] | +0,040 | +0,106 | 10 | 45.6 % | -0,384 | 0,384 | 279 / 264 | 231 / 219 |
| W72/θ1.0/L1 | +0,008 | 0,049 | [-0,003; +0,018] | [-0,002; +0,022] | +0,009 | +0,005 | 8 | 14.5 % | -0,260 | 0,260 | — / — | 322 / 467 |
| W72/θ1.0/L2 | +0,015 | 0,099 | [-0,005; +0,035] | [-0,004; +0,045] | +0,018 | +0,011 | 8 | 14.5 % | -0,519 | 0,519 | 2626 / 3800 | 322 / 466 |
| W72/θ2.0/L1 | +0,001 | 0,006 | [+0,000; +0,002] | [+0,000; +0,002] | +0,001 | +0,000 | 2 | 1.2 % | +0,000 | 0,000 | — / — | 362 / 413 |
| W72/θ2.0/L2 | +0,002 | 0,011 | [+0,000; +0,004] | [+0,000; +0,005] | +0,002 | +0,000 | 2 | 1.2 % | +0,000 | 0,000 | — / — | 362 / 413 |
| W168/θ0.0/L1 | +0,026 | 0,055 | [+0,015; +0,037] | [+0,012; +0,043] | +0,016 | +0,022 | 12 | 92.2 % | -0,122 | 0,229 | 99 / 176 | 37 / 66 |
| W168/θ0.0/L2 | +0,051 | 0,111 | [+0,029; +0,075] | [+0,024; +0,085] | +0,033 | +0,044 | 12 | 92.2 % | -0,243 | 0,457 | 57 / 101 | 37 / 66 |
| W168/θ0.5/L1 | +0,002 | 0,088 | [-0,019; +0,017] | [-0,021; +0,022] | +0,015 | +0,001 | 4 | 38.3 % | -0,652 | 0,671 | — / — | 14621 / 20454 |
| W168/θ0.5/L2 | +0,004 | 0,176 | [-0,037; +0,033] | [-0,041; +0,045] | +0,030 | +0,002 | 4 | 38.3 % | -1,304 | 1,343 | — / — | 14587 / 20410 |
| W168/θ1.0/L1 | -0,006 | 0,087 | [-0,027; +0,007] | [-0,025; +0,009] | +0,006 | -0,007 | 4 | 10.8 % | -0,755 | 0,755 | — / — | — / — |
| W168/θ1.0/L2 | -0,012 | 0,174 | [-0,053; +0,014] | [-0,050; +0,018] | +0,012 | -0,015 | 4 | 10.8 % | -1,510 | 1,510 | — / — | — / — |
| W168/θ2.0/L1 | +0,000 | 0,000 | [+0,000; +0,000] | [+0,000; +0,000] | +0,000 | +0,000 | 0 | 0.0 % | +0,000 | 0,000 | — / — | — / — |
| W168/θ2.0/L2 | +0,000 | 0,000 | [+0,000; +0,000] | [+0,000; +0,000] | +0,000 | +0,000 | 0 | 0.0 % | +0,000 | 0,000 | — / — | — / — |
| oracle | +0,040 | 0,097 | [+0,021; +0,061] | [+0,017; +0,065] | +0,032 | +0,029 | 36 | 100.0 % | -0,284 | 0,406 | 84 / 121 | 47 / 68 |
| fixed_plus | -0,009 | 0,041 | [-0,017; -0,000] | [-0,025; +0,005] | -0,009 | -0,009 | 2 | 100.0 % | -0,106 | 1,324 | — / — | — / — |
| fixed_minus | +0,008 | 0,042 | [-0,001; +0,016] | [-0,005; +0,024] | +0,009 | +0,007 | 2 | 100.0 % | -0,151 | 0,573 | — / — | 225 / 646 |

Regelcellen met gem > lat én beide ondergrenzen > 0: 10 (W24/θ0 ×2, W24/θ1 ×2, W72/θ0 ×2, W72/θ0,5 ×2, W168/θ0 ×2) — een kaartfeit; de 24 regelcellen zijn sterk gecorreleerd en de W24-helft is basisgedragen (§4).

## 4. Lezing (reconstructie; geen oordeel)
1. De vaste richting short Velocity / long Hyperliquid (`fixed_minus`) houdt netto +0,008 SOL/dag over (funding-only +0,009): het structurele verschil dekt na 21 bps wisselkosten en basis nauwelijks de lat. 2. **W24-cellen zijn geen funding-bewijs:** netto boven het orakel, surplus uit de basis bij wissels, fees × 2 verandert bijna niets; het mechanisme (premie-mean-reversion, afgerekend op fillClose vs HL-close van het beslisuur) is op uursluitingen niet uitvoerbaar zoals gemeten. 3. Funding-gedragen cellen W72/θ0 en W168/θ0 leveren ≈ 0,02–0,05 SOL/dag (≈ 2–5 × de staking-lat) met T tegen de lat 57–322 dagen en tegen 0 66–143 dagen (blok). 4. De blok-bootstrap is maar iets breder dan iid (dagpersistentie lag 24 0,62 < uurpersistentie 0,95); de forward-T uit de blok-sd is het minimum. 5. Risico's buiten de dagreeks: liquidatie van één been bij L 2, venue-risico (Hyperliquid buiten Solana), Velocity's lazy settlement, slippage boven de taker-fee.

## 5. Vervolg (geen keuze hier)
Wil de coördinator/eigenaar één cel kiezen (1 blik), dan gelden LAB's eisen voor de pre-reg: een funding-gedragen cel (W72/θ0 of W168/θ0, L1 of L2); basis afgerekend op de vulkoers van het volgende uur (niet de sluitkoers van het beslisuur); funding-only als nevenmaat; liquidatie- en venue-risico als tekst; T uit de blok-sd tegen 0 én tegen de lat; en de `paper-funding-ledger`-spec vóór de start (uurlijkse poll van dezelfde publieke endpoints, paper-posities, geen sleutels) — anders is E85 een kaart, geen lead.

## 6. Pins
Map `research/e85-funding-spread-2026-10-29/` SHA256SUMS **6d99b01e9d05401f557f115ff2763c7a798afd2ebc9a6b3ff2865f021ca606ea** (18 regels): plannen v0.1 179e4f43…, v0.2 12184340…, v0.3 36a5a1af…, **v0.4 cddadd63…**; parser v1 cbe9cc52… (rc-2-uitvoer 0f0170b3…), **parser v2 82ce157c…**, **reader 07d89f96…**, diagnose e2b37e51… → 42a9b1f8…, rookgenerator df60c871…, rook a9303a52…; run: parse-.out 76d77110…, parse-info 101485cd…, hourly.parquet 6778ee0a…, reader-.out b9899435…, cells.json 9db3027a…. Ruwe map `wallets/e85-raw-2026-10-29/` SHA256SUMS 6aff0f5b… (116), pull-log 4e671b0b….
