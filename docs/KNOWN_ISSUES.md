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
19. **v2 real-data pipeline remains incomplete:** Bronze capture, the Phase-4 contracts, the fixture-verified Phase-5 Rust reducer, and the synthetic fixture-only Phase 6A/6B Silver event and exact-state/provenance contracts are merged. None has processed or proven real CAR/archive/slot/accountstate data; `approved: false`, `researchReady: false`, and `pilotEligible: false` remain mandatory. Real activation-slot provenance, CAR-byte verification, an explicitly authorized bounded source pilot, state-enriched causal evidence, Gold features, registry approval, OOS evidence, execution evidence, and strategy profitability remain absent.

## Repository and dependencies

20. **Dependency audit follow-up:** current lockfile reports three moderate production-chain findings through `@solana/web3.js -> jayson -> uuid@8.3.2` and one high dev-chain finding through Vite/PostCSS/nanoid. They predate PR #1; investigate root-cause-first and do not run `npm audit fix --force` blindly.
21. **Frontend bundle warning:** production bundle remains about 585 kB and Vite reports a non-blocking chunk-size warning.

## Research

22. Technical Pump lifecycle correctness, the fail-closed Phase 2 harness, transport-free Phase 3 Bronze, fixture-only Phase 6A/6B, and the offline Phase-7 readiness candidate are implemented. See [`PHASE7_OLD_FAITHFUL_PILOT_A_READINESS.md`](PHASE7_OLD_FAITHFUL_PILOT_A_READINESS.md). Real activation/layout provenance remains absent, so the candidate is `HOLD_UNPROVEN_ACTIVATION` with `approved: false`, `researchReady: false`, and `pilotEligible: false`.
23. Pilot A execution remains HOLD pending exact-byte review, proven slot activation, a separately approved bandwidth-cap preflight, and explicit GO. Pilot B, full epoch, multi-month research, OOS, execution-quality and profitability claims remain NO-GO. No tuning, protocol expansion, live spend, archive download/stream, or inferred approval is permitted.
