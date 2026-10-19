# Meetopzet Titan Swap API (Triton): dekking en quote-afstand voor pump.fun en PumpSwap — v0.1, 2026-10-19, LAB research

**Status: opzet, niet gebouwd.** Bouwen en Triton-verkeer alleen na het go van de coördinator (owner). Paper-only: alleen read-only quotes, niets ondertekenen, niets versturen. Uitvoering valt buiten scope (vraagt een handtekening).

## 1. Wat Titan is, volgens de bronnen

- **Triton** (docs.triton.one/trading-apis/titan-swap-api): een WebSocket-stream van koersopgaven; de Argos-routingengine is een meta-aggregator over meerdere DEX-aggregators en RFQ-bronnen; pad `wss://<endpoint>.rpcpool.com/<token>/titan/api/v1/ws`; in alle plannen met een actief Solana-abonnement (shared en dedicated); prijs **$0,08 per GB** (streamingtarief); per quote-update een `SwapRoute` met instructies of een kant-en-klare transactie; de stream start met een `NewSwapQuoteStream`-verzoek. [feit, Triton-docs]
- **Titan** (titan-exchange.gitbook.io, export `llms-full.txt`, eerste 100 k tekens gelezen): de WebSocket `/api/v1/ws` is "Enterprise only, through a distributor" (Triton is die distributeur); quotes worden "continuously updated as on-chain state changes"; encodings JSON en MessagePack; genoemde venues Orca, Meteora, Raydium, Phoenix, "Prop AMMs", orderbook-DEX'en; de volledige lijst komt uit `/api/v1/venues` en `/api/v1/providers`. **pump.fun en PumpSwap (pump_amm) worden nergens genoemd.** De HTTP-tegenhanger `GET /api/v1/quote/swap` vraagt `inputMint`, `outputMint`, `amount` (invoer, kleinste eenheid), **`userPublicKey` (verplicht)**, en kent `slippageBps` (standaard 100), `numQuotes` (≤ 10), `dexes` / `excludeDexes` / `venueAllowlist`, `onlyDirectRoutes`, `simulate` (standaard true) en `maxPriceDeviationBps` (standaard 1000). [feit, Titan-docs; de stream-parameters zelf staan niet in het gelezen deel: onbekend]
- Gevolg: **of Titan onze venues dekt is onbekend** en is precies vraag 1 van de meting. Een quote vraagt een `userPublicKey`; welk adres we daarvoor gebruiken zonder privésleutel en zonder een derde te imiteren is dezelfde eigenaarsbeslissing als bij simulateBundle (opzet ná paperweek 1). [modelaanname: een willekeurig geldig adres zonder saldo volstaat voor een quote; te toetsen]

## 2. Vragen en wat de meting oplevert

| # | vraag | maat | levert |
|---|---|---|---|
| Q1 | Dekt Titan (a) pump.fun-curves, (b) verse PumpSwap-pools (< 1 u), (c) oudere PumpSwap-munten (≥ 10 dagen)? | per categorie: aandeel mints met een route, en via welke venue/provider | ja/nee per categorie; zonder (a)/(b) is Titan alleen voor exits of oude munten (revival) van belang |
| Q2 | Hoe ver zit Titans quote van onze eigen pool-wiskunde? | per mint en grootte 0,5 / 2 / 5 SOL: `outAmount` van Titan tegen de engine-fill op dezelfde slot-toestand (reserves uit de recorder, fees uit de data); verschil in bps; apart voor koop en verkoop | de afstand van ons fill-model tot een onafhankelijke routeur; een exit-prijs bij grotere posities waar onze eigen wiskunde ophoudt (meerdere pools / routes) |
| Q3 | Hoe snel en hoe zwaar is de stream? | updates per seconde per stream, bytes per update, latency tegen de slot waarop de quote rust (als die in de update staat; anders onbekend) | de GB-verwachting en of een quote dicht genoeg op het beslismoment zit voor een fill-check naast simulateBundle |

Geen P&L, geen claim: een vergelijking van prijzen op hetzelfde moment.

## 3. Steekproef

- Uit de live-journal van de paperweek (na de start): per dag **10 mints per categorie** (a/b/c), gekozen met een vast zaad uit de signalen van die dag (a: curve-triggers; b: pool_reversed-triggers < 1 u na de poolcreatie; c: pools ≥ 10 dagen oud met een trade dat uur), elk op drie groottes en twee richtingen = 60 quote-vragen per categorie per dag, 180 per dag, **7 dagen → 1.260 quotes**. Een categorie zonder routes na 3 dagen (0 van 30) stopt: dan is Q1 voor die categorie beantwoord.
- Per quote: één `NewSwapQuoteStream`, de eerste **3 updates** of hoogstens **5 s**, dan sluiten. Geen doorlopende streams.
- Referentie per quote: de reserves van de pool/curve uit het recorder-segment op de slot van de quote (of de laatste slot ervoor), gevoed aan de engine-venuefuncties (dezelfde als markout: `pool_buy_exact_in`, `curve_buy_exact_in`, …) met de fees uit de data.

## 4. Meter, dagcap en limieten

- **Meter** zoals de recorder: bytes en berichten per minuut in de journal-tabel `usage` (stream `titan`), plus een eigen teller per dag (quotes gevraagd / met route / zonder route / fout).
- **Dagcap**: 250 quote-streams en **50 MB** per dag (auto-stop; de teller staat in de journal); week-cap 400 MB. Verwachting: een update is enkele kB (JSON, routes met instructies), 3 updates × 1.260 quotes ≈ **5–15 MB voor de hele meting** → ≈ $0,001 bij $0,08/GB; de kosten zitten dus niet in de bytes maar in het bouwen. [modelaanname: updategrootte; de eerste dag meet hem]
- **Rate limits**: Titan-stream-limieten zijn niet gedocumenteerd (onbekend); de Triton-RPC-regels gelden sowieso: 1.200 verzoeken per 10 s per IP, bij 429 ≥ 10 s pauze, `X-Ratelimit-*`-headers loggen (docs.triton.one/core-features/ratelimits). Eén stream tegelijk, ≥ 1 s tussen twee streams.
- **Kosten per quote**: Triton rekent per GB; of Titan zelf iets rekent via Triton staat er niet (onbekend) → navragen bij Triton, samen met de prijs per simulateBundle-verzoek.

## 5. Uitvoering (na go)

- Leesscript `lab/wallets/titan_probe.py` (onderzoekscode, geen engine): leest de mintlijst van de dag uit de journal (read-only), opent de stream, schrijft per quote één rij naar `wallets/titan-v1/quotes-<dag>.parquet` (mint, categorie, richting, grootte, slot, provider, venue, inAmount, outAmount, priceImpact als gegeven, bytes, ms) en de referentie-fill ernaast; vaste anonieme User-Agent; token uit `rpc.env`, nooit in logs of output.
- Rapport `lab/reports/<datum>-titan-dekking.md`: Q1-tabel per categorie, Q2-verdeling van het verschil in bps per categorie en grootte (mediaan, p90, dag-CI), Q3-tabel; registerrij als meting.
- Volgorde: ná paperweek 1 en ná de simulateBundle-opzet (zelfde adresvraag); de twee delen één RPC-module en één approval-bestand per meting (ontwerpkeuze Lab Backtest, spec Triton-metingen v1.3).

## 6. Bronnen

- Triton, Titan Swap API: https://docs.triton.one/trading-apis/titan-swap-api (gelezen 2026-10-19 via de Triton-docs-MCP).
- Triton, rate limits: https://docs.triton.one/core-features/ratelimits.
- Titan, developer docs export: https://titan-exchange.gitbook.io/titan/llms-full.txt (eerste 100 k tekens; de Direct-/stream-referentie zit er niet in).
