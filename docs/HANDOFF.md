# HANDOFF.md — Lees dit bestand eerst

*Doel: een fresh-context agent (Hermes/Cursor/LLM) reconstruert in enkele minuten de huidige werkelijkheid zonder de chatgeschiedenis te lezen.*

## Wat bouwen we?

Een **Triton-only Solana paper-trading bot** die pump.fun-launches detecteert via
Dragon's Mouth/Vixen-geyser-streams, met contra-momentum-entry, volledig **paper-only**
(nooit echte transacties). Zero-cost door default-offline mode. Geen live funds.

## Waar staan we nu?

- Branch `fix/audit14` · HEAD `3e95a3c` · werkboom clean
- Running image `solana-bot:contra-audit16-offline-pump-3e95a3c` · **OFFLINE_ZERO_COST** (TRITON_LIVE_ENABLED=false; balance $0)
- 592/592 tests · build ✓ · tsc 0
- GitHub `daffieeee-arch/solana-paper-scanner` (main = baseline) — push via SSH-deploy-key
- Baseline-tag `offline-pump-baseline-20260815` → HEAD 3e95a3c

## Wat is bewezen?

- Zero-cost offline mode (geen clients/calls/subscriptions; status + health correct)
- Pump.fun parser: structureel (discriminator + PDA) — officiële IDL-varianten + live-observed
- PDA-validatie via officiële `@solana/web3.js` (100-fixture-bewijs)
- WAL/ledger authoritative + quarantine als WAL-event (`position_quarantined`)
- TP/SL paper-lifecycli offline + netwerkloos (fetch-spy 0); WAL-replay deterministisch
- MarketIdentity fail-closed (gx-only/unknown/mismatch → reject)

## Wat is NIET bewezen?

- **Live Triton-connectiviteit** (balance $0; eerst bit-cutoff-vermoeden)
- Pump observed dispatchers experimenteel deel (geen primaire txn)
- Non-Pump protocols (AMMv4/CPMM/CLMM/Meteora/Orca/Moonshot/Jupiter) incomplete
- MarketIdentity ENFORCEMENT (shadow-only)
- Backfill completion/watchdog-fixes
- v2 event-level data-architectuur (ontwerp)

## Wat mag absoluut niet?

- Nooit live Triton/Titan/RPC/DAS-verkeer in offline mode
- Nooit echte blockchaintransacties
- Nooit secrets committen; nooit inline-secrets in code
- Nooit backfill herstarten zonder expliciete opdracht
- Nooit ClickHouse stop/ALTER/OPTIMIZE/FINAL
- Nooit config met TRITON_LIVE_ENABLED=true zonder balance + budget-safeguards
- Geen deployment/ENFORCE zonder approval + immutable tag + Git-SHA-proof

## Belangrijke bestanden/modules

- `src/main.ts` — boot, zero-cost guard, dashboard/status
- `src/zero-cost.ts` — TRITON_LIVE_ENABLED-guard (strikt 'true')
- `src/providers/triton-geyser.ts` — geyser-stream, parsePumpTxn
- `src/providers/triton.ts` — decode/normalization, emitDiscovery
- `src/pump-parser.ts` — structurele Pump-parser + PDA (officiële web3.js)
- `src/market-identity2.ts` / `src/market-identity-upstream.ts` — discriminated identity
- `src/entry-shadow.ts` — shadow entry-gate (WOULD_ACCEPT/REJECT)
- `src/scanner.ts` — scan-loop, candidate-evaluatie
- `src/portfolio.ts` — paper entry/exit/risk (TP/SL/trailing)
- `src/ledger.ts` — crash-safe WAL; `src/accounting.ts`+`capital-accounting.ts` — quarantaine
- `src/config.ts`, `src/dashboard.ts`, `frontend/src/App.tsx`
- `scripts/update-bot-audit13.py` — deploy (image-tag + provenance)

## Welke MCP's/tools bestaan?

- triton-docs · solana-mcp · truenas-mcp · old-faithful-docs · clickhouse (read-only) · grafana
- Skills: systematic-debugging · test-driven-development · requesting-code-review · codebase-inspection · crash-safe-persistence · github-pr-workflow · secure-git-push

## Eerstvolgende taak

1. **Nu (offline)**: v2-event-level-pipeline-ontwerp (Bronze/Silver/Gold) óf protocol-coverage-uitbreiding (AMMv4/CPMM pool-state decoder) — géen live Triton.
2. **Bij reactivatie** (na top-up + budget-safeguards): Pump-connectivity-proof (first-event) + shadow-venster vóór ENFORCE-overweging.

## Recovery source of truth

- **Git SHA**: `3e95a3cb79acd9dab0b7568032712e5a26f6ec37`
- **Tag**: `offline-pump-baseline-20260815`
- **Bundle-backup** (lokaal, buiten repo): `/opt/data/backup-git/solana-paper-scanner-backup-20260815.bundle`

## Daarna lezen

1. `docs/CURRENT_STATE.md` (exacte status)
2. `docs/ARCHITECTURE.md` (end-to-end)
3. `AGENTS.md` (projectregels)
4. `docs/DECISIONS.md` · `docs/KNOWN_ISSUES.md` · `docs/DEVELOPMENT_WORKFLOW.md`
5. `docs/triton-cost-safety.md` · `docs/offline-hardening-decimals-cost.md` · `docs/pump-source-classification.md` · `docs/protocol-coverage.md`