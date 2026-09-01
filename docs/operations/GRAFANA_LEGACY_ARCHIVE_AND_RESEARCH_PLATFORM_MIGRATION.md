# Grafana legacy archive and Research Platform migration

> **Document status: RETIRED.** This TrueNAS/Grafana migration target is cancelled. Do not execute this runbook; retain only until controlled PR 2A cleanup. See [`../HANDOFF_V2.md`](../HANDOFF_V2.md).

## Status

**RUNBOOK ONLY — no Grafana, ClickHouse, datasource, plugin, dashboard or service mutation was performed.**

## Observed Phase-8B baseline

- Grafana is already installed and RUNNING as Grafana 13.2.0 at the existing TrueNAS app endpoint.
- Grafana uses persistent SQLite storage and already has the ClickHouse and GitHub plugins.
- ClickHouse is STOPPED, with no process or listener.
- The prior generated/preprocessed ClickHouse configuration was unstable and logged repeated truncated-XML parse failures.
- The current ClickHouse datasource is unreachable and points at the stopped LAN endpoint using the unsuitable `default` user binding.
- A dedicated bounded read-only Grafana user is required before any future LAN exposure.
- Prometheus and Loki are not installed and no corresponding Grafana datasource exists.
- The GitHub datasource is optional and UNAVAILABLE because no separate read-only credential is configured.
- The Elasticsearch datasource has a `timeField` configuration error.
- Those optional datasource problems do not block the standalone Research Cockpit.

## Legacy classification

The active historical dashboard UIDs `memecoin-contra` and `memecoin-contra-strategy` are now archived in Git as **LEGACY_FORENSIC_V1 / FORENSIC_ONLY / NOT_RESEARCH_READY / NOT_STRATEGY_EVIDENCE**. Their old `TRANSACTION_NET_SWAP_V1` and derived expectancy/score outputs are not present strategy evidence and must not be restored as primary dashboards.

ClickHouse connectivity may later be restored only for forensic access and future separately approved schemas. Work from a ZFS **snapshot/clone**, never by mutation of the forensic source. No DDL, DML or OPTIMIZE is permitted on that source.

## Future operations order

1. Verify the hashed legacy exports, screenshots, panel queries and dependencies.
2. Design a dedicated ClickHouse clone/custom app with stable immutable configuration.
3. Configure a dedicated read-only user; never expose the passwordless default user over LAN.
4. Separate `solana_forensic_v1` from all default research datasources.
5. In a later approved schema phase, establish `solana_bronze`, `solana_silver`, `solana_gold` and `solana_ops`.
6. Provision the new **Solana Research Platform** dashboard suite from Git with `allowUiUpdates: false`.
7. Remove old live dashboards only after the replacement base dashboards, archives, screenshots and rollback are independently verified.

## Rollback

- Stop the dedicated future ClickHouse clone/app; do not touch the forensic source.
- Restore the prior datasource export and Grafana database snapshot when required.
- Disable/remove only newly provisioned dashboards and retain version-controlled JSON.
- Return the cockpit to STOPPED/no-app state; leave the existing `solana-bot` app STOPPED and unchanged.
