# ARCHITECTURE.md — End-to-end architectuur

*Statuslabels: ✅ bewezen (getest/deployed) · 🔶 functioneel maar HOLD · ⛔ future/ontwerp*

## Dataflow (live Triton — wanneer TRITON_LIVE_ENABLED=true)

```
Dragon's Mouth/geyser (Triton) ─ client-factory (geyser of vixen) ─→ VixenEvents
   │  program-ids: 6EF8 (pump), 675k (AMMv4), CPMM, CAMM, cpamdp, whir, pAMM, Moon, JUP6
   ▼
decode/normalization (triton-geyser.ts / triton.ts)
   ├─ parsePumpSwap (structureel: discriminator + PDA-cross-match) → mint/curve/kind ✅
   └─ parseGenericSwap / onRaydiumUpdate / onCpmmUpdate (per programma)
   ▼
MarketSnapshot (scoring.ts) — poolDepth, programId, pairId, marketIdentity ⛔ optional
   ▼
MarketIdentity (market-identity2.ts, discriminated union)
   ├─ pump_bonding_curve (PDA ["bonding-curve", mint]) ✅
   ├─ amm_cpmm (marketId=pool-state, lpMint apart) 🔶 identity-incomplete
   └─ clmm (ontwerp) ⛔
   ▼
Scanner (scanner.ts) — + contra-momentum score/score, entry-gate (shadow), risk
   ▼
Paper execution (portfolio.ts) — enter/exit positions, risk (stop-loss/trailing/max-hold)
   ▼
WAL/ledger (ledger.ts) — append-only crash-safe journal; authoritative state ✅
   ▼
Quarantine/accounting (accounting.ts + capital-accounting.ts) — administratief WAL-event ✅
```

## Zero-cost offline mode (TRITON_LIVE_ENABLED=false) ✅ bewezen

- main.ts bouwt **niets** (geen geyser/vixen-factory, geen TritonProvider/TitanProvider/reserve-reader)
- `requireLiveTritonOrThrow` op centrale grens; status `OFFLINE_ZERO_COST`, health `DISABLED_OFFLINE_ZERO_COST`
- Bot draait offline: dashboard, ledger-replay, scanner-logica, shadow-MarketIdentity-evaluatie

## Strategy & risk

- Contra-momentum (ENTRY_MODE=contra): dip-kopen, surge/sniping anti-edge
- Risk: stop-loss (dynamisch volxpect), trailing-stop, max-hold, time-stop, breakeven-buffer
- Paper: entryCost + slippage/fee → realistische PnL; exit via WAL-event

## ClickHouse (historisch, naast bot) ✅ draaiend · 🔶 backfill gepauzeerd

- `memecoin_swaps`: ReplacingMergeTree(slot) ORDER BY signature (v1 TRANSACTION_NET_SWAP)
- v2-ontwerp: Bronze (event-level) / Silver (current) / Gold (net) — zie vorige docs

## Old Faithful / Jetstreamer 🔶 gepauzeerd

- Backfill supervisors gepauzeerd (cron paused); data veilig; resume =zelfde supervisor-command met epoch-skip-dedup

## Grafana ✅ draait

- Op 192.168.1.234:30037; geen live Triton-costs-metrics zonder budget-safeguards (⛔)

## Frontend/backend

- Backend: Node/TS (src/main.ts dashboard, /api/status, /api/controls, /api/debug)
- Frontend: React (frontend/src/App.tsx) — toont OFFLINE_ZERO_COST + DISABLED health
- Dashboard: 100.79.221.55:3000 (Tailscale) / 192.168.1.234:3000 (LAN)

## TrueNAS deployment

- App `solana-bot` (custom, host_network) met secret-mounts `/run/secrets/*` (Triton-endpoint/token, rpc-*)
- Image gebouwd uit git (SOURCE_GIT_SHA → runtime provenance); immutable tags beginkaarten per build
- ClickHouse: los proces host_network poort 8123/9000 (geen TrueNAS-app; autostart niet structureel)

## Bewezen vs future/HOLD

| Onderdeel | Status |
|---|---|
| Offline zero-cost mode | ✅ bewezen |
| Pump parse (IDL + observed) | ✅ bewezen (offline fixtures) |
| PDA/address primitives (web3.js) | ✅ bewezen |
| WAL/ledger | ✅ bewezen |
| Quarantine/accounting | ✅ bewezen |
| TP/SL lifecycle + replay | ✅ bewezen (offline) |
| Live Dragon's Mouth connect | 🔶 HOLD (balance $0) |
| MarketIdentity ENFORCE | ⛔ HOLD (shadow) |
| Non-Pump identities | ⛔ future |
| v2 event-level backfill | ⛔ ontwerp |