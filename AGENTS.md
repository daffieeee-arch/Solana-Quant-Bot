# AGENTS.md — Solana Paper Trading Bot

*Lees eerst: `docs/HANDOFF.md`. Deze AGENTS.md is de compacte handleiding voor elke coding-agent (Hermes, Cursor, andere LLM's).*

## Projectdoel

Een Triton-only Solana paper-trading bot die pump.fun/Raydium-launches detecteert via
Dragon's Mouth/Vixen-geyser-streams, met contra-momentum-entry, volledig **paper-only**
(nooit echte transacties). Geen live trades. Geen marktinvloed.

## Huidige bewezen baseline

- Branch `fix/audit14`, HEAD `3e95a3c` (full SHA `3e95a3cb79acd9dab0b7568032712e5a26f6ec37`)
- Baseline-tag: **`offline-pump-baseline-20260815`** → exact HEAD
- GitHub: `daffieeee-arch/solana-paper-scanner` (main = baseline)
- 592/592 tests · build ✓ · tsc 0
- Runtime: **OFFLINE_ZERO_COST** (TRITON_LIVE_ENABLED=false)

## Git/GitHub workflow

- Werk per taak op een **feature-branch** vanaf `fix/audit14`; nooit direct op baseline
- Klein logische commits; git clean + volledige test-suite vóór commit
- Push via SSH-deploy-key (`~/.ssh/config` alias `github-solana-paper-scanner`), expliciete refs (geen `--force`/`--tags`/`--mirror`)
- Rollback-points als **annotated tags** (immutable, versie-markerend)

## Architectuurregels (kern)

- **TRITON-ONLY hard**: Triton levert RPC/geyser/DAS/Titan; Helius/Birdeye/Truenav not allowed (CoinGecko KEEP voor market-context)
- **Zero-cost default**: TRITON_LIVE_ENABLED=false → geen live client-constructie
- **MarketIdentity fail-closed**: geen entry zonder canonical identity; `gx:<mint>`-only → reject
- **WAL/ledger = authoritative state**; quarantine = append-only administratief event
- **Pump-only bewezen baseline**: andere protocollen fail-closed/IDENTITY_INCOMPLETE
- **Entry enforcement UIT** (shadow-mode); project verplicht dat

## Safety constraints (niet onderhandelbaar)

- Nooit live Triton/Titan/Dragon's Mouth/RPC/DAS-verkeer in offline mode
- Nooit echte blockchaintransacties; paper-only
- Nooit secrets committen (`.env*`, `secrets/`, `*-token*.txt`, `*-wss-admin*.mjs`); inline-secrets verboden
- Backfill Oud Faithful/Jetstreamer → ClickHouse: gepauzeerd, nooit herstarten zonder expliciete opdracht
- ClickHouse niet stoppen/wijzigen tijdens backfill-context
- Deployment alleen na expliciete user-approval + immutable image-tag + Git-SHA-provenance

## MCP's beschikbaar (Hermes)

| MCP | Gebruik |
|---|---|
| triton-docs | Triton/Dragon's Mouth/Titan-documentatie |
| solana-mcp | Solana program/instruction/account-semantiek |
| truenas-mcp | TrueNAS apps/storage/resource (read-only waar mogelijk) |
| old-faithful-docs | Old Faithful/Jetstreamer-archiefsemantiek |
| clickhouse | Read-only ClickHouse-query's (begrensd; hermes_ro) |
| grafana | Metrics/alerts (geen live Triton-costs zonder budget) |

## Vereiste development/review skills

`systematic-debugging` (root-cause eerst) · `test-driven-development` (tests vóór fix) ·
`requesting-code-review` + fresh-context reviewer-subagents · `codebase-inspection` ·
`crash-safe-persistence` (WAL-sensitive) · `github-pr-workflow`

## Test/build quality gates (vóór élke merge)

```bash
npx vitest run          # volledige suite groen
npm run build          # build ✓
npx tsc --noEmit       # 0 errors
git status --porcelain # clean
# secret-scan: geen keys/tokens; gebruik `git log --all`-scan bij push-risico
```

## Documenten eerst lezen

1. `docs/HANDOFF.md` — fresh-context startpunt
2. `docs/CURRENT_STATE.md` — exacte actuele status (SHAs/modes)
3. `docs/ARCHITECTURE.md` — end-to-end architectuur
4. `docs/DECISIONS.md` — waarom-keuzes
5. `docs/KNOWN_ISSUES.md` — open blockers
6. `docs/DEVELOPMENT_WORKFLOW.md` — werkwijze
7. `docs/triton-cost-safety.md` · `docs/offline-hardening-decimals-cost.md` · `docs/pump-source-classification.md` · `docs/protocol-coverage.md`