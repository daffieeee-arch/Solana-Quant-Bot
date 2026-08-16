# CURRENT_STATE.md — Actuele projectstatus

*Source of truth = Git/code. Laatst geverifieerd: 2026-08-16.*

## Git & GitHub

- Branch: `fix/audit14`
- HEAD: `3e95a3c` (full: `3e95a3cb79acd9dab0b7568032712e5a26f6ec37`)
- Werkboom: **clean**
- GitHub remote: `daffieeee-arch/solana-paper-scanner` (main = baseline; geverifieerde push)
- Baseline-tag: **`offline-pump-baseline-20260815`** → exact HEAD 3e95a3c
- Overige tags: `offline-pump-pre-deploy-20260815-1204` (7dfcfee) · `shadow-audit15-20260813-2043` (832b28f) · `watch-audit15-20260813-1427` (b2da1b1) · `opt-audit15-20260813-1107` (9112c03) · `pre-audit15-20260813-0906` (fe2ecb5)

## Runtime

- Running image: `solana-bot:contra-audit16-offline-pump-3e95a3c` op TrueNAS
- Mode: **OFFLINE_ZERO_COST** (TRITON_LIVE_ENABLED=false)
- providerHealth: `[{TRITON, DISABLED_OFFLINE_ZERO_COST}]` — nooit ok/degraded/down in offline
- 0 Triton/Titan/Dragon's Mouth/RPC/DAS-clients · 0 subscriptions · 0 calls
- Gateway/bot bereikbaar: `http://100.79.221.55:3000` (LAN 192.168.1.234:3000)

## Protocolstatus

| Protocol | Status |
|---|---|
| Pump.fun | **SUPPORTED_AND_TESTED** (officiële IDL + observed dispatchers; zie pump-source-classification) |
| PumpSwap / Raydium AMMv4/CPMM/CLMM / Meteora / Orca / Moonshot / Jupiter | **IDENTITY_INCOMPLETE** of **NOT_YET_SUPPORTED** (fail-closed) |

## MarketIdentity / entry-gate

- MarketIdentity volledig offline getest (structurele PDA + discriminator-based)
- Entry-gate: **SHADOW-mode**, **ENFORCEMENT UIT**
- WAL/ledger authoritative; quarantaine WAL-event `position_quarantined`

## Triton-live

- **BALANCE $0** — live-endpoorts cut off (vermoedelijk prepaid-cutoff)
- Geen top-up gedaan; reactivatie geblokkeerd zolang saldo/budget niet hersteld
- Live Pump-connectiviteit na reactivatie moet opnieuw bewezen worden (first-event)

## ClickHouse / backfill / Grafana

- ClickHouse: **draait** (host_network, poort 8123, data intact ~563M rows / 86 GB)
- Backfill: **GEPAUZEERD** (supervisors gestopt, cron `7d8d0894389d` gepauzeerd)
- ClickHouse MCP actief (hermes_ro read-only)
- Grafana: draait (port 30037)

## Tests/build/typecheck (actueel)

- **592/592 tests** (61 files) · `npm run build` ✓ · `npx tsc --noEmit` 0 errors
- Frontend build ✓ · secret-scan schoon

## Open blockers (kort; details in KNOWN_ISSUES.md)

1. Triton balance $0 → live reactivatie geblokkeerd
2. Live Pump connectivity na top-up nog niet opnieuw bewezen
3. Non-Pump protocol identities incompleet
4. MarketIdentity ENFORCEMENT uit (bewust)
5. ClickHouse autostart na NAS-reboot niet structureel opgelost
6. ClickHouse default-user/LAN exposure (hardening pending)
7. Backfill completion/watchdog/repair fixes open (onderzoeksfase)
8. v2 event-level backfill = ontwerp (Bronze/Silver/Gold)

## Eerstvolgende aanbevolen hoofdtaak

Wanneer live Triton beschikbaar is: **Pump-connectivity-proof** (één first-event + MarketIdentity-complete shadow-metrics >1 venster) vóór ENFORCE-overweging.
Nu (offline): **v2-event-level-pipeline-ontwerp** of **protocol-coverage-uitbreiding** — zie HANDOFF.md.