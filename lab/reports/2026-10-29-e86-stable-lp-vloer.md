# E86 — stable-paar geconcentreerde LP op Solana: de vloermeting op 90 dagen publieke data (29 okt 2026)

Registerrij E86; plan v0.3 `research/e86-stable-lp-2026-10-29/e86-plan-2026-10-29.md` (b7a56025…; v0.1 174bc986… en v0.2 b2e4cad6… bewaard); **verkend, 0 blikken** (12 cellen vast in het gehashte plan vóór het lezen; geen cel gekozen); **kaart/vloermeting, geen strategieclaim**; eigenaarsbesluit 29 okt (klasse 2 van drie nieuwe ideeënklassen, PAPER ONLY, gratis publieke data, mandaat 28 okt). Alle getallen uit de gearchiveerde uitvoer (§6).

## 0. Kort
- **Feit (vloer):** de drie grootste stable-paren met geconcentreerde liquiditeit die DefiLlama op Solana noteert (Orca PYUSD-USDC 16,3 M; Raydium USD1-USDC 9,9 M; Raydium USDC-USDT 1,9 M, alle 0,01 %-tier) verdienden over 31 jul – 28 okt een pool-gemiddelde fee-opbrengst (apyBase) van **0,302 / 0,659 / 2,523 %/jaar**. Op 50 SOL (≈ 5.087 USD, §3) is dat bij m = 1 **+0,0003 / +0,0008 / +0,0033 SOL-equivalent per dag** — een orde onder de staking-lat van 0,010 SOL/dag; de TVL-gewogen mand +0,0007 (m = 1), +0,0022 (m = 3), +0,0078 (m = 10).
- **Feit:** geen enkele m = 1- of m = 3-cel haalt de staking-lat, behalve USDC-USDT m = 3 op de rand (+0,0103 [+0,0086; +0,0119], T tegen de lat 7.413 (iid) / 17.603 (blok) dagen — niet toetsbaar). Alleen de optimistische bovengrens m = 10 van de kleinste pool (USDC-USDT, +0,0345 [+0,0292; +0,0401], 3,5 × lat) ligt er duidelijk boven; m = 10 is in het plan de bovengrens, niet een haalbare concentratie, en die pool verloor in het venster de helft van haar TVL (3,73 M → 1,94 M).
- **Lezing (reconstructie):** stable-paar-LP op Solana is géén vloer boven native staking: wie 50 SOL in stables omzet voor LP-fees geeft de SOL-koersblootstelling op én verdient bij realistische concentratie minder dan staken. **De lat voor elk volgend idee blijft staking ≈ 0,010 SOL/dag**; deze meting voegt daar niets boven toe. Geen vervolgmeting nodig; geen celkeuze (0 blikken).
- **Niet gedekt:** Meteora DLMM (DefiLlama noteert geen Meteora-pools op Solana; §1) en beheerde kluizen (kamino-liquidity, uitgesloten als geen pool). Beloningen (apyReward) waren in het venster 0 bij alle drie.

## 1. Opzet en selectie (feit)
- Bronnen (plan §1, letterlijk; gratis, publiek, geen sleutel, curl-standaard-UA): DefiLlama yields-API `GET /pools` en `GET /chart/{pool}` via `yields.llama.fi`; Orca v2-API `GET /pools/search?q=PYUSD-USDC` als kruiscontrole; drie docpagina's als ontvangen. Ruwe pulls door de coördinator naar `wallets/e86-raw-2026-10-29/`: pull 1 (18:20Z) `pools.json` 15.929 pools (ea68634e…); pull 2 (18:39:49–58Z, 7 verzoeken, alle 200). SHA256SUMS **6c696717…** (14 bestanden) vóór enige bewerking.
- **Selectie v1 (plan v0.2) gaf rc 2:** één kandidaat. De gehashte metadata-diagnose `e86_select_diag.py` (4f613f1d…, → fb0d197b…; leest alleen chain/project/symbol/stablecoin/tvlUsd/poolMeta) liet zien dat DefiLlama op Solana **geen enkele Meteora-pool** noteert en Raydium-CLMM-pools onder `raydium-amm` met `poolMeta` "Concentrated - 0.01%" staan; stable-paren ≥ 1 M: orca-dex PYUSD-USDC 16,26 M, kamino-liquidity PYUSD-USDC 16,22 M, raydium-amm USD1-USDC 9,90 M, raydium-amm USDC-USDT 1,94 M.
- **Selectie v2 (plan v0.3 §2, `e86_select.py` 6cf47244…, 0 blikken):** orca-dex ∪ raydium-amm "Concentrated", twee van de zes stables, tvlUsd ≥ 1 M, alle kandidaten max 6 → **3 pools**; uitgesloten met reden: kamino-liquidity 5 (beheerde kluis bovenop een DEX-pool, geen pool; de 16,22 M PYUSD-USDC is vermoedelijk dezelfde liquiditeit als de Orca-pool), raydium-amm Standard 1, coffer 1, tvlUsd < 1 M 4. Tabel = 3 × 3 m + mand × 3 = 12 cellen.

## 2. Parse (feit; `e86_parse_2026-10-29.out` 84cac418…, `parse-info.json` f4a83e2c…)
Per pool precies 90 venster-dagen [2026-07-31, 2026-10-29), 0 dubbele dagen, 0 null apyBase (charts 847/424/645 rijen; laatste van de dag = hoogste timestamp). **Eenheidscontrole** (enige lezing vóór de tabel): mediaan |apy − (apyBase + apyReward)| 0,0 pt → apyBase is procent per jaar, y_d = apyBase/100/365. **Orca-kruiscontrole** (mechanische match op symbool + tvlUsdc, afwijking 0,0 %): whirlpool `9tXiuRRw7kbejLhZXtxDxYs2REe43uH2e7k1kocgdM9B`, feeRate 100 = 1,0 bps, protocolFeeRate 1300 (fees incl. protocolaandeel); momentopname 29 okt fees/tvl 24 u 4.444e-06 per dag (= 0,162 %/jaar; yieldOverTvl gelijk) tegen apyBase van 28 okt 0,082 %/jaar → ratio 1,98 — beide op het sub-1 %-niveau; het verschil (ander 24-uursvenster, protocolaandeel 13 %) verandert de orde niet. Depegvlag pulldag: price 1,00009 → geen.

## 3. Celtabel (feit; `e86_read_cells_2026-10-29.out` f5852bc3…, `cells.json` 5bfff1cf…; netto SOL-equivalent per UTC-dag op 50 SOL = 50 × y_d × m − 0,01/90; iid en 7-daagse blok-bootstrap 10.000 seed 7; T = (2,8 × sd / (gem − lat))² met lat 0,010 resp. 0, iid-sd / blok-sd; USD tegen oracle_v 101,74 USD/SOL van 2026-10-29T01:00Z uit de E85-reeks — benadering; de positie staat in stables en geeft de SOL-koersblootstelling op)

| cel | dagen | gem SOL/dag | sd | iid [2,5; 97,5] | blok7 [2,5; 97,5] | gem USD/dag | dagen y = 0 | slechtste dag | aandeel dagen < lat | T vs lat (iid / blok) | T vs 0 (iid / blok) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| orca-dex PYUSD-USDC m=1 | 90 | +0,0003 | 0,0006 | [+0,0002; +0,0004] | [+0,0002; +0,0004] | +0,03 | 0 | -0,0001 | 100,0 % | — / — | 30 / 19 |
| orca-dex PYUSD-USDC m=3 | 90 | +0,0011 | 0,0018 | [+0,0008; +0,0015] | [+0,0009; +0,0014] | +0,11 | 0 | -0,0001 | 100,0 % | — / — | 19 / 12 |
| orca-dex PYUSD-USDC m=10 | 90 | +0,0040 | 0,0059 | [+0,0029; +0,0053] | [+0,0031; +0,0050] | +0,41 | 0 | -0,0001 | 86,7 % | — / — | 17 / 10 |
| raydium-amm USD1-USDC m=1 | 90 | +0,0008 | 0,0006 | [+0,0007; +0,0009] | [+0,0007; +0,0010] | +0,08 | 0 | -0,0001 | 100,0 % | — / — | 5 / 9 |
| raydium-amm USD1-USDC m=3 | 90 | +0,0026 | 0,0019 | [+0,0022; +0,0030] | [+0,0022; +0,0032] | +0,26 | 0 | -0,0001 | 98,9 % | — / — | 4 / 7 |
| raydium-amm USD1-USDC m=10 | 90 | +0,0089 | 0,0064 | [+0,0076; +0,0103] | [+0,0075; +0,0110] | +0,91 | 0 | +0,0000 | 62,2 % | — / — | 4 / 7 |
| raydium-amm USDC-USDT m=1 | 90 | +0,0033 | 0,0027 | [+0,0028; +0,0039] | [+0,0024; +0,0041] | +0,34 | 0 | +0,0003 | 97,8 % | — / — | 5 / 11 |
| raydium-amm USDC-USDT m=3 | 90 | +0,0103 | 0,0080 | [+0,0086; +0,0119] | [+0,0074; +0,0124] | +1,04 | 0 | +0,0010 | 61,1 % | 7.413 / 17.603 | 5 / 11 |
| raydium-amm USDC-USDT m=10 | 90 | +0,0345 | 0,0266 | [+0,0292; +0,0401] | [+0,0248; +0,0415] | +3,51 | 0 | +0,0036 | 10,0 % | 9 / 21 | 5 / 11 |
| mand TVL-gewogen (3 pools) m=1 | 90 | +0,0007 | 0,0005 | [+0,0006; +0,0008] | [+0,0006; +0,0008] | +0,07 | 0 | -0,0000 | 100,0 % | — / — | 4 / 3 |
| mand TVL-gewogen (3 pools) m=3 | 90 | +0,0022 | 0,0014 | [+0,0020; +0,0025] | [+0,0020; +0,0025] | +0,23 | 0 | +0,0002 | 100,0 % | — / — | 3 / 3 |
| mand TVL-gewogen (3 pools) m=10 | 90 | +0,0078 | 0,0048 | [+0,0068; +0,0088] | [+0,0068; +0,0086] | +0,79 | 0 | +0,0010 | 72,2 % | — / — | 3 / 3 |

Beschrijvend (niet meegeteld): TVL Orca PYUSD-USDC 19,65 M → 16,33 M (gem 18,19 M); Raydium USD1-USDC 9,90 M → 9,91 M (vlak; **vlag: USD1 is een jonge stable, depegrisico hoger**); Raydium USDC-USDT 3,73 M → 1,94 M (min 1,47 M, max 4,09 M) — de hoogste fee-opbrengst per TVL hoort bij de pool die leegloopt. apyReward 0,000 %/jaar bij alle drie.

## 4. Lezing (reconstructie; vloer, geen oordeel over een strategie)
- Het pool-gemiddelde (m = 1) van de grootste stable-pools levert 0,3–2,5 %/jaar: op 50 SOL 0,0003–0,0033 SOL/dag, 3–30 × onder staking. Een smalle range (m = 3) brengt alleen de kleinste pool op de lat; m = 10 is de bovengrens uit het plan en geen haalbare concentratie over 90 dagen (de meeste liquiditeit in een stable-pool ligt al rond de pariteit).
- De bootstrap-intervallen zijn smal (fee-opbrengst varieert weinig per dag), dus de kaart is precies: de conclusie "onder de lat" hangt niet aan ruis. T tegen 0 is kort (3–30 dagen), maar winst > 0 is hier niet de vraag; de vraag was de vloer tegenover staking.
- Wat de meting niet dekt: Meteora DLMM (buiten DefiLlama), beheerde kluizen (kamino-liquidity), actieve herbalancering, en depeg-episodes (binnen het venster geen; USD1 jong).

## 5. Vervolg
Geen. De vloer gaat als getal in het register: **laagst-risico fee-inkomst op 50 SOL in stable-paren ≈ 0,0007 (m = 1) / 0,0022 (m = 3) SOL-equivalent per dag (mand), onder de staking-lat van 0,010** — staking blijft de lat voor E87 en latere ideeën. Een forward-vloermeting (20 dagen apyBase van één pool) is niet nodig en wordt niet gepind (0 blikken gebruikt).

## 6. Pins (`research/e86-stable-lp-2026-10-29/SHA256SUMS` **5c47ee50ac9cf78dabbd3b9a13a51240bea7843f2b6ad6d6c03cbf97bac606fe**, 16 regels; ruwe pull `wallets/e86-raw-2026-10-29/SHA256SUMS` 6c696717ad4a6039686e5e6a34bade890b7feb763f970d86ca1e14cb2e649bb3)
- Plannen: v0.1 174bc986…, v0.2 b2e4cad6…, **v0.3 b7a56025…** (LAB OK; gehasht 18:38:17Z vóór pull 2).
- Selectie: `e86_select.v1.py` 25e6d04e… (rc 2), diagnose `e86_select_diag.py` 4f613f1d… → `.out` fb0d197b…, **`e86_select.py` v2 6cf47244…** → `select-v2.log` 64771d1b… / `selection-v2.json` 77f11461… (ruwe map).
- Parser `e86_parse.py` adc9ad26…, reader `e86_read_cells.py` 7d05f2fc…, rookgenerator `e86_smoke_gen.py` 429beb9c… → `e86_smoke.out` 1a3f61bc… (ok rc 0 met dubbele dag/null/nep-symbool, bad rc 3, rekencontrole +0,009889/+0,004889, reader fail-closed rc 2); alle drie gehasht 18:48:16Z vóór de rook en de run.
- Run 18:49:14Z (`-I`, 3G-scope, python 3.13.15, numpy 2.5.3): `e86_parse_2026-10-29.out` 84cac418…, `run-2026-10-29/daily.csv` 0dcccbb7…, `parse-info.json` f4a83e2c…, `e86_read_cells_2026-10-29.out` f5852bc3…, `cells.json` 5bfff1cf….
- SOL-koers: E85 `run-2026-10-29/hourly.parquet` 6778ee0a…, rij max(hour_end) = 2026-10-29T01:00Z.
