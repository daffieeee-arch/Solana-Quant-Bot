# TRITON-FIRST PAPER TRADING STRATEGY — REDESIGN v2
*Design-document (niet-uitgevoerd plan) — voor review door gebruiker voordat we bouwen*

**Datum:** 2026-08-07
**Primaire provider:** Triton One (alles wat Triton biedt = eerste keus; Birdeye/Gecko = alleen fallback)
**Status:** Kill-switch op bestaande momentum-path, herontwerp van de strategie van binnenuit.

---

## 1. Probleemstelling (waarom 0 trades)

Feitelijke observatie uit 2 dagen live-logs:
- **81/81 rejections = `age_outside_window`**, allemaal pools van **0–0,1 min oud**.
- De momentum-strategie eist `MIN_AGE_MINUTES=3` én bewezen 5-min-data
  (`priceChangeM5Percent`, `volumeM5Usd`, `buysM5/sellsM5`).
- Vixen detecteert pools in hun **eerste seconden na launch** (discovery-delay 0–4s).

**Paradox:** de momentum-regels willen *bewezen, oudere* beweging; de discovery-bron
levert *babypools zonder enige history*. De twee komen elkaar vrijwel nooit tegen →
0 trades. Dit is een **structureel fout ontwerp**, niet een config-tweaks-probleem.

---

## 2. Ontwerpfilosofie (trader × quant)

Een winnaar in Solana memecoins handelt **op stroom (flow), niet op oud momentum**.
Het ingenieuze inzicht van een pro: de eerste **seconde/minuut** van een launch
bepaalt de koers; de beste entries zijn bij **ontkiemende stroom**, vóór de curve.

Als quant spreek ik daarom uit: **"momentum" moet hergedefinieerd worden van een
statisch 5-min-window naar een **real-time flow-regime** gemeten op de stream zelf.**
Wij hebben geen *historische* data nodig als wij de *levende* stroom kunnen tellen.

---

## 3. Triton diensten per pijler (primaire keus)

| Pijler | Triton dienst | Wat het levert | Gebruik nu? |
|---|---|---|---|
| Signals | **Vixen** (Pumpfun, Raydium AMMv4/CPMM, + uitbreiden: PumpSwap, Moonshot, JupiterSwap) | buy/sell events, per-pool flow, mint-identiteit | deels (3 progs) |
| Latency | **Dragon's Mouth** (gRPC, intro-slot) + **Riptide** | tot ~400ms sneller dan WS | nee |
| Reliab. | **Fumarole** (persistent, never-miss) | 4-dagen-backfill, slot-tracking | nee |
| Prijs | **Triton self-calc** (pool-reserves) + **Titan WS quote-stream** (DART) | exact-pair prijs, live best-route | self-calc ja; Titan WS nee |
| Ticker | **DAS getAsset** | échte coin-tickers (al live) | ja |
| Executie (later live) | Titan Tx, **Cascade** (SWQoS), **Priority-Fees API**, **Shield** (frontrun-beleid) | betrouwbare, goedkope, beschermde swaps | nee |
| Reads | **Steamboat/Cloudbreak** indexen | snelle getProgramAccounts | nee |

---

## 4. De nieuwe strategie: "Flow-First Momentum" (3 lagen)

### Laag A — Rijpings-venster (lost 0-trades op, minimale wijziging)
Ontdekte pool wordt **niet** direct verworpen. In plaats daarvan:
- Zet de pool in een **in-memory "ripening queue"** met `firstSeenAt`.
- Houd **per-mint flow-counters bij vanuit de Vixen-stream** (buys/sells per minuut).
- Evalueer de pool pas zodra **beide** waar zijn:
  1. `ageMinutes ≥ minAgeMinutes` (3 min — rijping), EN
  2. Flow-venster gevuld (≥1 min aan buys/sells gezien in de stream).
- Als de pool nu door alle gates komt → entry.

**Effect:** de momentum-strategie krijgt een eerlijke kans. Elke Vixen-launch die
3 min overleeft en stroom toont wordt nu gévalueerd in plaats van direct weggegooid.

### Laag B — Flow-signaal (real-time, i.p.v. statisch 5-min)
Scoor nu op **live gemeten flow** uit de stream i.p.v. externe 5-min-paneldata:
- `buyFlow = Σ buys over laatste 3 min` (per mint, in-memory, ring-buffer).
- `sellFlow`, `buyPressure = (buys − sells)/(buys + sells)`.
- **Whale-boost**: tel tx ≥ `minWhaleTxSol` (al in code) → sterk signaal.
- Score = gewogen: buy-pressure + flow-volume + whale-beslag + prijs-trend (zelf-betaald).

Score-grens: verlaag van rigid 50 naar een **adaptieve drempel** (bijv. scoretop-kaart),
of een flow-gerichte basis (bv. `buyPressure ≥ 0.6` én `buysM3 ≥ N`).

### Laag C — Exit-beheer (pro-discipline)
- **Trailing stop** op basis van `highPriceUsd` (loopt al) i.p.v. alleen %tp/sl.
- **Tijdstop** (max-hold 15 min) → voorkom dat winst terugdraait.
- **Deels-verkoop-regel**: bij +`minProfitForContinue` (2%) verhoog stop naar breakeven
  (BREAKEVEN_TRIGGER 8% bestaat al — behouden en aanscherpen).
- Geen over-concurrentie: cap 4 gelijktijdige (al zo).

---

## 5. Exacte config-voorstellen (startpunt, te backtesten)

```
MIN_AGE_MINUTES: 3            (behouden — maar nu met rijping, niet directe reject)
MIN_LIQUIDITY_USD: 5000       (iets hoger om scrappy pools uit te filteren)
MIN_VOLUME_M5_USD: 1500       (nu gemeten uit stream-flow, niet extern)
MIN_PRICE_CHANGE_M5_PERCENT: 0.5   (eerder 1.5 — nu flow-gedreven, prijs secundair)
MIN_MOMENTUM_SCORE: 50        (wordt adaptief flow-score, niet statisch)
MIN_STOP_LOSS_PERCENT: 8
MAX_STOP_LOSS_PERCENT: 20
STOP_LOSS_PERCENT: 10
TAKE_PROFIT_PERCENT: 20       (hoger: laat runners lopen, stop beschermt)
TRAILING_STOP_PERCENT: 6
MAX_HOLD_MINUTES: 15
MAX_CONCURRENT_POSITIONS: 4
```

---

## 6. Nieuwbouw / uitbreidingen (in volgorde)

1. **Rijpings-queue** (Laag A) — de gerichtste fix; deels in `scanner.ts`/`composite.ts`
   met een `ripeningByMint` Map (TTL-begrensd, gepruned).
2. **Stream-flow telemetrie** (Laag B) — per-mint ring-buffer van Vixen buys/sells;
   vervangt afhankelijkheid van externe 5-min-data voor de flow-score.
3. **Titan WS-quote-stream** — subscribe `NewSwapQuoteStream` per kandidaat-mint om de
   **live best-route-fill prijs** te streamen (i.p.v. 1 REST quote). Dit maximaliseert
   het gebruik van je betaalde Triton-abonnement en geeft de ware executieprijs.
4. **Fumarole / Dragon's Mouth** als latency/resilience-laag (later; live-roadmap).
5. **Adaptieve score** en **gebalanceerde exit** (Laag C) — aanscherpen na backtest.

---

## 7. Succescriteria (definitie van "winnend")

- **Setup-rate > 0**: ten minste enkele entries per dag (geen 0-trades meer).
- **Positieve expectancy** over ≥100 trades: Σ(PnL)/n > 0, winRate + HRV>0.
- **Reward:risk ≥ 1.5** (TAKE_PROFIT 20 / STOP 10).
- **Drawdown-beheersing**: max daily-loss 0.5 SOL regel blijft; equity groeit monotoon
  bij "fat right tail" (weinig grote winners).

---

## 8. Risico's & eerlijke kanttekeningen

- **Launch-sniping is fraudegevoelig** (rug-pulls, honeypots). De bot is **paper-only**;
  wanneer we naar live gaan MOET een rug/honeypot-check (DAS metadata + burn/liq-lock)
  vooraf. Nu (paper) is dat een score-input, geen live-beveiliging.
- **Flow kan fake zijn** (wash-trading op verse pools). Whale- en volume-latentie
  blijven nodig.
- Rijping toegepast op 3-min-minimum betekent dat we niet-allereerste zijn — dat is
  een bewuste afweging (minder "0-trades", iets minder prime-latency).
- Iedere verandering is TDD + backtest op 200-trade-geschiedenis vóór acceptatie.

---
*Volgende stap: na jouw akkoord bouwen we Pijler 1 (rijpings-queue) + Pijler 2 (flow-telemetrie)
TDD-groen, en integreren we daarna Titan WS. Geef je feedback op dit ontwerp.*
