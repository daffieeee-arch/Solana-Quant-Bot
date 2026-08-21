# KNOWN_ISSUES.md — Open issues and HOLDs

Only current open items belong here. Resolved issues remain in Git history.

## Triton live and cost

1. **Balance $0:** live reactivation is blocked; prepaid-cutoff is likely but technically unconfirmed until a later bounded test.
2. **Cost attribution unknown:** first $125 is not tied to a verified Billable Items breakdown.
3. **Live Pump connectivity:** first-event/subscription health must be re-proven after any future reactivation.
4. **Cost controls incomplete:** max duration, per-service request/byte metering, warning threshold, hard stop, and automatic disconnect are not implemented.

## MarketIdentity and parser

5. **Broader MarketIdentity enforcement is off:** full contract is shadow-only; only exact `gx:<mint>` identities are currently hard-blocked.
6. **Observed Pump dispatchers partly experimental:** liveBuy/liveBuyV2/liveBuyExactSolIn lack primary in-repo mainnet transactions.
7. **Deep loaded-address resolution incomplete:** tests cover versioned shapes but not complete real-world address-table resolution through every parser path.
8. **Non-Pump completeness missing:** PumpSwap, Raydium, Meteora, Orca, Moonshot, Jupiter, and CLMM routes require protocol-specific canonical identity, decimals, price state, exit route, and evidence before being called supported.
9. Some generic AMM/CLMM builders remain Raydium-oriented and must not be treated as universal protocol support.

## TrueNAS and runtime

10. **Bot app currently stopped:** configured image/provenance is known, but a fresh app query is required before claiming it is running.
11. **ClickHouse autostart unresolved:** ClickHouse is a separate process and does not structurally start after NAS reboot.
12. **ClickHouse default-user/LAN exposure:** default user lacks adequate hardening and HTTP 8123 is LAN-reachable.

## Backfill

13. **Backfill paused:** supervisors and repair cron remain stopped.
14. **Completion logic is wrong:** row-count threshold is not a valid completion definition; use source cursor/max-slot and bounded retry state.
15. **Stall watchdog false positives:** progress must use heartbeat/source cursor and distinguish WRITING, SEEKING, NETWORK_RETRY, STALLED, and COMPLETE.
16. **Repair supervisor guard:** duplicate supervisor starts and stale status files remain open risks.

## Historical data contract

17. **v1 is transaction-net only:** multi-hop, inner-CPI, pool-route, and event-level detail are not preserved.
18. **Duplicate estimate is limited:** roughly 3.4% cross-part exact-retry overlap was observed in a biased sample. The dataset-wide ratio is unknown and normal background merges may already have consolidated some rows. Do not claim that no deduplication occurred merely because no global `OPTIMIZE FINAL` was run.
19. **v2 real-data pipeline remains incomplete:** Bronze capture, Phase 4, the fixture-verified Phase-5 reducer, synthetic Phase 6A/6B, and the Phase-7 readiness package are merged. None has processed or proven real CAR/archive/slot/accountstate payloads; `CANDIDATE_UNAPPROVED`, `HOLD_UNPROVEN_ACTIVATION`, `approved: false`, `researchReady: false`, and `pilotEligible: false` remain mandatory. All ten Phase-7 registry entries remain `STRUCTURALLY_SUPPORTED_UNPROVEN_ACTIVATION`; accepted Silver for real data, Gold, OOS, execution evidence, and profitability remain unavailable.

## Repository and dependencies

20. **Dependency audit follow-up:** current lockfile reports three moderate production-chain findings through `@solana/web3.js -> jayson -> uuid@8.3.2` and one high dev-chain finding through Vite/PostCSS/nanoid. They predate PR #1; investigate root-cause-first and do not run `npm audit fix --force` blindly.
21. **Frontend bundle warning:** production bundle remains about 585 kB and Vite reports a non-blocking chunk-size warning.

## Research

22. The Phase-7 readiness package is documented in [`PHASE7_OLD_FAITHFUL_PILOT_A_READINESS.md`](PHASE7_OLD_FAITHFUL_PILOT_A_READINESS.md) and merged through PR #14 as `a5f2edf1cba51cc350e4809b66a8b018debbf6f2`; post-merge CI run `32350736436` succeeded. Merge does not change its `HOLD_UNPROVEN_ACTIVATION` content status or authorize accepted Silver, preflight, or pilot execution.
23. **Activation evidence remains insufficient for promotion:** Phase 7A corroborates one ProgramData boundary and exact official/on-chain IDL structure, but eight old loader transaction details, the candidate historical ELF/source mapping, and raw candidate-range semantic observations remain missing. Zero of ten entries are proven.
24. **Remote citationgate is merged:** PR #16 merged as `784192a675e31d78da82852e91ceb254eccae982`; post-merge run `32397224604` executed the separate offline citation step exactly once and succeeded. This closes CI enforcement only and does not authorize preflight or Pilot A.
25. Pilot A remains `HOLD_UNPROVEN_ACTIVATION` pending every separate readiness and authorization gate. Phase 8A implements only a synthetic fixture eligibility split, Bronze runner and Research Cockpit/observability surface. It does not close missing real activation/binary evidence, run preflight, create transport eligibility, authorize execution or produce accepted Silver. Pilot B, full epoch, multi-month research, OOS, execution-quality and profitability claims remain NO-GO.
26. **Phase 8C deployment remains HOLD:** Phase 8D1 selected collision-free candidate IDs `61000`/`61001` with shared GID `61000` read-only, but no accounts/ownership were applied. Stable canonical inventory uses complete source reads rather than a fixed raw owner count; Phase 8D2 must repeat it immediately before application. Base-image digests still resolve only inside the future GitHub verify run. The POSIX dataset and both TrueNAS apps remain unapplied, and `solana-bot` remains STOPPED.
27. **Legacy Grafana remains forensic:** `memecoin-contra` and `memecoin-contra-strategy` are archived as `LEGACY_FORENSIC_V1` and must not be restored as strategy evidence. Live removal remains a later operations gate.
28. **Observability infrastructure absent:** Prometheus and Loki are not installed. Static fixture snapshots cannot support meaningful rate panels; bounded replay streaming remains designed but unauthorized.
29. **Hermes has no safe local container builder:** Phase 8D stopped before mutation rather than mounting Docker/containerd sockets or weakening isolation. Phase 8D1 uses a GitHub-hosted remote-builder design; its remote image/container gates cannot be claimed until a later authorized commit/push/PR executes them.
30. **Private GHCR pull credential absent by design:** no package credential is created in Phase 8D1. A future Phase 8D2 credential must be package-read-only, separate from the Hermes repo PAT, and deployment must use registry digests rather than tags.

See [`PHASE8C_COCKPIT_ONLY_RUNTIME_GRAFANA_ARCHITECTURE.md`](PHASE8C_COCKPIT_ONLY_RUNTIME_GRAFANA_ARCHITECTURE.md).
