# DEVELOPMENT_WORKFLOW.md — Werkwijze

## Branch-per-taak

- Elke taak op een eigen branch vanaf `fix/audit14` (of een nieuwere stabiele tip)
- Nooit direct werk op de baseline-tip; nooit ongeverifieerde code naar `main`
- Klein logische commits met herkenbare messages

## Quality gates (vóór merge)

```bash
npx vitest run          # volledige suite groen
npm run build          # build ✓
npx tsc --noEmit       # 0 errors
git status --porcelain # clean
# secret-scan (push-risico): git log --all + tracked files
```

- Targeted tests eerst (TDD), daarna volle suite
- Frontend: build + tests

## Ontwikkeldiscipline

- `test-driven-development` (tests vóór fix-groen)
- `systematic-debugging` (root-cause eerst, geen gok-fixes)
- `requesting-code-review` + fresh-context reviewer-subagents bij verandering in
  risico-gevoelige paden (parser, identity, ledger, zero-cost, quarantaine)
- `codebase-inspection` voor grote codebases
- `crash-safe-persistence`-overwegingen bij WAL/ledger-wijzigingen

## MCP usage policy

- triton-docs/solana-mcp: protocol-semantiek onderzoeken (géén live-calls)
- clickhouse MCP: uitsluitend read-only, begrensd (hermes_ro), nooit zware scans
- truenas-mcp: read-only voorkeur; app-mutaties alleen na approval
- old-faithful-docs: backfill/archief-semantiek
- Grafana: metrics lezen; geen live Triton-cost-tests zonder budget

## Deployment

- Alleen na expliciete user approval
- Unieke immutable image-tag per build (bv. `contra-audit16-offline-pump-<sha>`)
- Git-SHA-provenance verplicht (runtime build.gitSha = exacte commit)
- Rollback-tag annoteren vóór deploy
- Banner: TRITON_LIVE_ENABLED expliciet (false in offline)

## GitHub CI

- Huidige status: **geen geconfigureerde CI** (private repo, push via deploy-key)
- Toekomst: lint/test-pipeline zonder secrets (secrets zitten alleen in `_FILE`-mounts op TrueNAS)

## Rollback/tag/provenance

- Immutable annotated tags voor alle milestone/baseline-punten
- Provenance: `SOURCE_GIT_SHA` build-arg + OCI-revision → `/api/status.build.gitSha`

## Nooit

- Secrets committen (`.env*`, `secrets/`, `*-token*.txt`, `*-wss-admin*.mjs`, inline keys)
- Force-push of `--tags`/`--mirror` naar GitHub
- Live Triton/Titan/RPC/DAS-verkeer zonder balance + budget-safeguards
- Backfill herstarten zonder expliciete opdracht
- Echte blockchaintransacties

## Offline-first

- Parser/identity/accounting-paden testbaar zonder netwerk (mocks + fixtures, fetch-spy=0)
- Live-functies nooit nodig voor tests
- Determinisme: clock-injectie, geen externe prijzen in tests