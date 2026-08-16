# KNOWN_ISSUES.md — Open issues and HOLDs

Only current unresolved items belong here.

## Triton live and cost safety

1. **Balance is $0.** Live Triton reactivation is blocked.
2. **Connectivity root cause remains technically unconfirmed.** Prepaid cutoff is the leading explanation for pending/no-event streams, but production endpoint/auth must be re-proven only after safeguards and a bounded future test.
3. **First $125 cost attribution is unresolved.** Local RPC counters cannot explain the full spend; Triton Billable Items/product-level usage is still required.
4. **Runtime budget protection is incomplete.** Maximum test duration, request/byte metering, cost estimator, warning threshold, hard stop, and automatic disconnect are documented but not fully implemented.
5. **Grafana cost observability is missing.** No complete per-service Triton cost dashboard or alerts exist.

## Pump parser and transaction coverage

6. **Observed dispatcher provenance is partial.** `liveSell` has primary in-repo evidence; `liveBuy`, `liveBuyV2`, and `liveBuyExactSolIn` remain experimental and require full structural/PDA validation.
7. **Live binary differs from the pinned public IDL.** Revalidate observed dispatch forms during any future live shadow test.
8. **Versioned transaction loaded-address resolution needs deeper real-shape coverage.** Current fixtures prove shape handling but do not fully demonstrate all `meta.loadedAddresses` cases from live transactions.

## Protocol identities

9. **Non-Pump identities are incomplete.** AMMv4/CPMM need reliable canonical pool-state wiring; CLMM requires pool/vault/tick-state decoding; PumpSwap, Meteora, Orca, Moonshot/Moonit, and Jupiter need separate protocol-specific work.
10. **Generic builders are not generic support.** Existing AMM/CLMM types and lane labels must not be treated as production-ready protocol coverage.

## Entry and strategy validation

11. **MarketIdentity enforcement remains off.** Shadow-only until live connectivity and a representative shadow window prove valid identities are populated without broad false rejection.
12. **A technically correct Pump pipeline has not proven a profitable edge.** A chronological, walk-forward, out-of-sample historical research harness is the next product phase.
13. **Automatic strategy promotion is disabled.** Deterministic historical quote/replay parity is not yet sufficient for safe self-promotion.

## ClickHouse and TrueNAS

14. **ClickHouse autostart after NAS reboot is not structural.** It currently runs as a manually started host-network process rather than a dedicated TrueNAS service/app.
15. **ClickHouse default user and LAN exposure remain unsafe.** The passwordless default user is reachable over HTTP 8123; staged service-user/network hardening is pending.
16. **ClickHouse current table is unpartitioned.** Avoid global `OPTIMIZE ... FINAL`; any migration/finalization must be separately designed and approved.

## Backfill

17. **Old Faithful/Jetstreamer backfill is paused.** Supervisors and repair cron must not be restarted without approval.
18. **Completion logic is wrong.** A fixed row-count threshold must be replaced by source-cursor/end-slot and pending-range/error semantics.
19. **Stall watchdog has false positives.** It needs source-cursor/heartbeat states such as WRITING, SEEKING_EMPTY_SLOTS, NETWORK_RETRY, STALLED, and COMPLETE.
20. **Repair cron can duplicate work.** Existing-process guards and reliable status/checkpoint semantics are required.

## Historical data contract

21. **v1 is only `TRANSACTION_NET_SWAP`.** Multi-hop, inner-CPI, exact pool event, and route detail are absent.
22. **The true dataset-wide retry overlap is unknown.** About 3.4% cross-part exact-retry overlap was observed in a sampled range; the sample is not a full-dataset estimate, and normal ReplacingMergeTree background merges may already have consolidated some duplicates.
23. **v2 event-level pipeline is design-only.** Bronze/Silver/Gold DDL, parser, provenance, pilot, and cost measurements are not implemented.

## Repository and CI

24. **CI enforcement is new.** The workflow introduced by `chore/repo-alignment-ci` must complete a first green run and then be made a required branch-protection check.
25. **Legacy runtime artifacts remain in published history.** They are removed from the current tree without rewriting history. Do not reintroduce them; use `tests/fixtures/` for intentional deterministic samples.
26. **`OFFLINE_ZERO_COST` is not fully network-isolated.** The normal runtime can still request free CoinGecko/CoinDesk context. Use network-isolated replay for deterministic research.