# Grafana provisioning — unapplied

> **Document status: RETIRED.** This provisioning target is cancelled. Do not apply it; retain only until controlled PR 2A cleanup. See [`docs/HANDOFF_V2.md`](../../../docs/HANDOFF_V2.md).

This directory is a version-controlled provisioning contract for the future **Solana Research Platform** folder. It is not copied into or imported by the live Grafana instance in Phase 8C.

- `allowUiUpdates: false`
- fixed dashboard UIDs
- fixed folder UID `srp-research-platform`
- file provisioning only
- datasource variables only
- no credentials or live datasource UIDs
- no automatic import/deployment

A later operations phase must snapshot Grafana, verify exact JSON hashes, provide approved datasource mappings, apply provisioning, validate health, and test rollback.
