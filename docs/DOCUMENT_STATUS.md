# DOCUMENT_STATUS.md — V2 documentation authority map

> **Document status: ACTIVE.** This registry prevents historical evidence and cancelled runbooks from being mistaken for current instructions.

## Status semantics

- **ACTIVE:** current authoritative product, architecture, workflow or operations boundary. It still must be verified against code/current GitHub state.
- **SUPERSEDED:** replaced and non-authoritative; retained for traceability or useful evidence.
- **HISTORICAL:** records earlier evidence/invariants only; not an active architecture, runbook, authorization or future target.
- **RETIRED:** target is cancelled; do not execute its instructions. Retained only until controlled cleanup.

An embedded historical `approved`, `ready` or similar field never overrides this document status.

## Active documents

- [`../AGENTS.md`](../AGENTS.md)
- [`../README.md`](../README.md)
- [`HANDOFF_V2.md`](HANDOFF_V2.md)
- [`DOCUMENT_STATUS.md`](DOCUMENT_STATUS.md)
- [`ADR_0001_V2_DATA_FIRST_CUTOVER.md`](ADR_0001_V2_DATA_FIRST_CUTOVER.md)
- [`ARCHITECTURE.md`](ARCHITECTURE.md)
- [`ROADMAP.md`](ROADMAP.md)
- [`PROJECT_V2_REBASE.md`](PROJECT_V2_REBASE.md)
- [`DECISIONS.md`](DECISIONS.md)
- [`KNOWN_ISSUES.md`](KNOWN_ISSUES.md)
- [`DEVELOPMENT_WORKFLOW.md`](DEVELOPMENT_WORKFLOW.md)
- [`WSL_DEVELOPMENT_SETUP.md`](WSL_DEVELOPMENT_SETUP.md)
- [`CI.md`](CI.md)
- [`FRONTEND_COCKPIT_ARCHITECTURE.md`](FRONTEND_COCKPIT_ARCHITECTURE.md)
- [`triton-cost-safety.md`](triton-cost-safety.md)
- [`operations/GITHUB_PROJECTS_ROADMAP.md`](operations/GITHUB_PROJECTS_ROADMAP.md)
- [`operations/PROJECT_V2_G0_PREFLIGHT.md`](operations/PROJECT_V2_G0_PREFLIGHT.md)

## Superseded documents

- [`HANDOFF.md`](HANDOFF.md)
- [`CURRENT_STATE.md`](CURRENT_STATE.md)
- [`PUMP_OFFLINE_RESEARCH.md`](PUMP_OFFLINE_RESEARCH.md)
- [`research/OLD_FAITHFUL_QUANT_DATA_SPEC.md`](research/OLD_FAITHFUL_QUANT_DATA_SPEC.md)

## Historical documents

- `HERMES_REVIEW_REQUEST_REPO_ALIGNMENT_CI.md`
- `HERMES_REVIEW_RESPONSE_ROUND1.md`
- `HERMES_REVIEW_RESPONSE_ROUND2.md`
- `HERMES_REVIEW_RESPONSE_ROUND3.md`
- `PHASE3_PUMP_V2_PILOT.md`
- `PHASE4_OLD_FAITHFUL_ADAPTER.md`
- `PHASE6_PUMP_SILVER_EVENT_CONTRACT.md`
- `PHASE6B_PUMP_SILVER_STATE_PROVENANCE_CONTRACT.md`
- `PHASE7A_PUMP_ACTIVATION_EVIDENCE.md`
- `PHASE7_OLD_FAITHFUL_PILOT_A_READINESS.md`
- `PHASE8A_BRONZE_RUNNER_RESEARCH_COCKPIT.md`
- `offline-hardening-decimals-cost.md`
- `protocol-coverage.md`
- `pump-source-classification.md`
- `../observability/grafana/legacy-v1/README.md`

The JSON manifests under `docs/research/` are historical evidence artifacts unless a future V2 dataset manifest explicitly supersedes them. Their old ranges, caps, eligibility fields and source claims are not active V2 approval.

`PHASE7A_PUMP_ACTIVATION_EVIDENCE.md` is intentionally classified here without an inline banner because the offline citation gate content-hashes that evidence document. PR 1 preserves those evidence bytes rather than rewriting the trusted digest.

## Retired documents

- [`../STRATEGY-REDESIGN-v2.md`](../STRATEGY-REDESIGN-v2.md)
- [`../.hermes/plans/2026-07-25_221221-paper-ledger-dashboard.md`](../.hermes/plans/2026-07-25_221221-paper-ledger-dashboard.md)
- `PHASE8C_COCKPIT_ONLY_RUNTIME_GRAFANA_ARCHITECTURE.md`
- `PHASE8D1_EXISTING_DIGEST_RECOVERY.md`
- `PHASE8D1_REMOTE_IMAGE_BUILD_GHCR_READINESS.md`
- `data/CLICKHOUSE_BRONZE_SILVER_GOLD_PLAN.md`
- `operations/GHCR_PRIVATE_IMAGE_RELEASE_AND_TRUENAS_PULL.md`
- `operations/GRAFANA_LEGACY_ARCHIVE_AND_RESEARCH_PLATFORM_MIGRATION.md`
- `../observability/grafana/provisioning/README.md`

Retired documents remain in PR 1 for archive-safe review. PR 2A may remove them only after invariant/evidence salvage and the separately approved annotated pre-cleanup tag.
