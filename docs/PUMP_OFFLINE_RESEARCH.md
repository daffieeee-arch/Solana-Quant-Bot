# Pump offline historical research harness

## Status

`NETWORK_ISOLATED_REPLAY` research is implemented as a pure TypeScript engine plus a file-only CLI. It does not connect to Triton, Old Faithful, ClickHouse, RPC, DAS, Titan, CoinGecko, or any other network service.

The engine is available, but real data remains **HOLD**. The reviewed-provenance registry is intentionally empty until a separate v2 parser/export review pins an exact provenance ID, parser Git SHA, and export-query SHA-256. Consequently, self-asserted v2 manifests return `BLOCKED`; they cannot authorize themselves by setting capability booleans.

The current `default.memecoin_swaps` table is **not accepted** as Pump strategy evidence. Its `TRANSACTION_NET_SWAP_V1` contract fails the harness capability gate before simulation.

## Read-only ClickHouse audit — 2026-08-16

The bounded audit temporarily started ClickHouse on `127.0.0.1` with crash reporting disabled. No backfill process, DDL, DML, `FINAL`, `OPTIMIZE`, or mutation ran. Before shutdown, the physical table state remained:

- 562,915,792 rows;
- 92,499,404,532 bytes on disk;
- 15 active parts with the same names and row totals as before the queries.

A bounded diagnostic used only nine small active parts:

- 3,996,998 rows and 3,996,998 unique signatures;
- time range `2026-06-18T12:03:46Z` through `2026-06-28T06:13:14Z`;
- only 298 Pump-program rows, covering 174 mints;
- 136 rows labelled buy and 162 labelled sell;
- all 298 rows used base decimals 6;
- stored median `price_lamports_per_token`: `0.00008978874258517292`;
- decimal-corrected median: `89.78874258517293` lamports per UI token.

This was a capability diagnostic, not a random sample and not an OOS performance sample.

## Why `TRANSACTION_NET_SWAP_V1` is blocked

The tracked parser provenance is `0efc5ed3c17e6500d34f9fe4f68242a0c98dc3cc`. Its v1 row contract emits at most one dominant net swap per transaction. It cannot reproduce the live Pump path because:

1. it requires a WSOL token-balance delta, while Pump bonding-curve trades use native SOL;
2. it scans only top-level program IDs and static account keys, omitting inner CPI and loaded-address routes;
3. it stores `wsol_raw_delta / base_raw_delta` but labels it lamports per token without applying base decimals;
4. it has no canonical bonding-curve identity or PDA evidence per row;
5. first observed swap is not a canonical launch timestamp;
6. transaction index/order, historical SOL/USD, liquidity, rug-risk, and whale-flow snapshots are absent;
7. therefore the production `evaluateMarketGate`, scoring, and paper lifecycle cannot be replayed with field parity.

The older analysis scripts also must not be treated as current evidence: they allowed overlapping signals, lacked chronological train/validation/test separation and bounded max-hold, and encoded TP/SL hits as `+1/-1` rather than actual `+TP%/-SL%` returns.

## Eligible source contract

The harness can accept only schema-version 2 `PUMP_SNAPSHOT_V2` after its exact parser/query tuple is added to the reviewed registry. Its manifest pins:

- reviewed provenance ID;
- parser Git SHA;
- export-query SHA-256;
- canonical record-content SHA-256;
- inclusive/exclusive data window;
- canonical UTC timestamps (`YYYY-MM-DDTHH:mm:ss.sssZ` only);
- explicit chronological train and validation cutoffs plus a hold-horizon embargo;
- provenance sampling interval;
- all required capabilities.

Every record must be Pump-only and include:

- unique sample ID and complete transaction-event provenance (`signature`, `slot`, `transactionIndex`, instruction location/index and parent index for CPI);
- canonical base58 Pump program, mint, bonding-curve pair ID, and matching complete `pump_bonding_curve` MarketIdentity;
- bonding-curve identity equal to the PDA independently derived from `['bonding-curve', mint]` and the official Pump program;
- provenance-backed scan-cycle ID/start time whose observation falls within the declared sampling cadence; cycles are anchored to the manifest window and may not overlap;
- canonical launch timestamp and observation timestamp;
- historical token USD price and record-specific historical SOL/USD price;
- liquidity, volume, 5-minute momentum, and integer buy/sell counts;
- structurally valid rug-risk and explicit whale-flow snapshot semantics (`null` means observed absent, not unknown);
- causal feature-window and bounded-mark-cadence provenance.

Exact `gx:<mint>` identities, invalid base58 keys, PDA mismatches, incomplete identities, unstable mint/curve mappings, mixed protocols, duplicate sample/event/cycle-mint keys, overlapping or cadence-misaligned cycles, hash mismatches, non-canonical timestamps, observations outside the manifest window, inconsistent launches, malformed or unbounded market fields, empty purged splits, insufficient embargoes, unreviewed provenance, and missing capabilities fail closed.

Pool-depth snapshots are currently rejected, not simulated. The shared production 6-decimal-base/9-decimal-quote conversion is corrected and regression-tested, but historical fill-impact claims remain forbidden until real source and fill-impact provenance are separately approved.

## Simulation semantics

For an accepted dataset:

- mints are assigned by canonical launch time to purged train, validation, or test cohorts; embargo-window launches are rejected and scan decisions exactly at an `EndExclusive` cutoff are right-censored;
- records are ordered with locale-independent code-unit comparison after observation time, slot, transaction index, instruction index, and sample ID;
- production `evaluateMarketGate`, `scoreContraMomentum`/`scoreMomentum`, `enterPaperPosition`, and `evaluateOpenPosition` are reused;
- USD snapshot fields remain unchanged for gates/scoring, while portfolio entry/mark prices are normalized to token-SOL (`token USD / historical SOL USD`) so lamport P&L reflects both price paths;
- candidates are grouped by provenance scan cycle, score-ranked, and limited by `maxEntriesPerScan` once per real cycle;
- at most one completed trade per mint is used, preventing repeated correlated re-entry inflation;
- production fee, slippage, dynamic stop, time-stop, max-hold, take-profit activation, and trailing-stop semantics apply;
- every position mark uses the same shared non-future/two-minute freshness rule as production; stale marks cannot trigger exits;
- a fresh mark arriving later than max-hold plus one declared scan interval cannot create a late windfall/loss; the position remains censored;
- all lamport-denominated intermediate and report values must be safe integers; precision-unsafe accounting blocks the run;
- an open position at end-of-data is reported as **censored**, never force-closed with fabricated P&L;
- each split starts with a fresh paper portfolio. Cross-split capital is intentionally not shared.

The unit-tested engine proves deterministic lifecycle reuse for structurally valid synthetic records. It does not yet produce real OOS evidence because the provenance registry is empty, and it makes no pool-depth fill-fidelity claim.

Raw split and simulation functions are private and are not exported from the built module. A single fixed synthetic provenance tuple exercises that private path in regression tests; it emits only `SYNTHETIC_TEST_ONLY`, carries `approved: false`, and can never produce CLI exit 0. The real-provenance registry remains empty.

## Usage

Build first:

```bash
npm run build
```

Run with explicit local JSON files:

```bash
npm --silent run research:pump-offline -- \
  --manifest /path/to/manifest.json \
  --records /path/to/records.json \
  --config /path/to/paper-config.json
```

- stdout is stable, machine-readable JSON;
- every report artifact binds the canonical manifest, records, and complete decoded config SHA-256 plus the claimed provenance tuple, its class, and its reviewed/approved state;
- each opened descriptor is read through EOF under a byte cap; symlinks, concurrent growth beyond the cap, malformed JSON, and size drift fail closed without echoing JSON bytes;
- exit `0`: independently reviewed dataset accepted and simulation completed (currently unavailable while the registry is empty);
- exit `2`: dataset correctly blocked by suitability gates, or an explicitly synthetic test-only report;
- exit `1`: malformed inputs or operational error.

Reproduce the current v1 rejection without ClickHouse or network:

```bash
npm --silent run research:pump-offline -- \
  --manifest tests/fixtures/pump-research/v1-manifest.json \
  --records tests/fixtures/pump-research/v1-records.json \
  --config tests/fixtures/pump-research/paper-config.json
```

The fixture intentionally contains no historical rows; it pins the audited v1 parser/capability contract and must return `BLOCKED` with exit code 2. It is not a data export.

`npm run build` also executes `verify:research-transport` against every built research module, the actual compiled CLI, and the Phase 3 Bronze capture module under ESM and CommonJS import guards. Loading HTTP/1, HTTP/2, HTTPS, net/TLS, UDP/DNS, WebSocket/fetch/browser transports, child processes/workers, or the broad `@solana/web3.js` root fails the build. Both runtime probes additionally run under a generated Linux seccomp cBPF filter installed by a temporary hardened launcher compiled from tracked source; the build fails unless compilation succeeds and a self-test proves network syscalls return `EPERM`. Pump PDA derivation uses a narrow local crypto/base58 primitive whose bytes are regression-tested against `PublicKey.findProgramAddressSync`.

## Next data step

Do not tune strategy parameters on v1. `docs/PHASE3_PUMP_V2_PILOT.md` defines the first Bronze capture vertical slice and `docs/research/V1_FORENSIC_MANIFEST.json` preserves the read-only v1 filesystem evidence with writer state explicitly unverified by the generator. Bronze capture alone is not a dataset and cannot enter the provenance registry. Phase 4 provides the reviewed transport-free Old Faithful contracts. The fixture-verified Phase-5 Rust reducer against the pinned Jetstreamer callback types was squash-merged through PR #7 as `9739eed415c90e4433b77e0cabc46bd32577bb9e`; it has processed no real archive/CAR/slot data and remains `researchReady: false`. Phase 6A is synthetic and fixture-only. Phase 6B was squash-merged through PR #12 as `71ab573c6c23080881022a8ee28ed2e24f94bbff`; post-merge main CI run `32301299856` succeeded. It adds only synthetic exact-byte Pump state/provenance fixtures and remains disconnected from callback/reducer runtime with `approved: false`, `researchReady: false`, and `pilotEligible: false`; no real CAR/archive/slot/accountstate, OOS, execution or profitability evidence exists.

The Pilot A readiness package is documented in [`PHASE7_OLD_FAITHFUL_PILOT_A_READINESS.md`](PHASE7_OLD_FAITHFUL_PILOT_A_READINESS.md) and merged through PR #14 as `a5f2edf1cba51cc350e4809b66a8b018debbf6f2`; post-merge CI run `32350736436` succeeded. Its exact source manifest, bounded `[422506000, 422507000)` range, output/budget/observability/frontend contracts and fail-closed evaluator remain `CANDIDATE_UNAPPROVED` and `HOLD_UNPROVEN_ACTIVATION` with `approved: false`, `researchReady: false`, and `pilotEligible: false`. All ten registry entries remain `STRUCTURALLY_SUPPORTED_UNPROVEN_ACTIVATION`.

The next step is read-only historical Pump deployment/upgrade, IDL, discriminator, and layout activation-evidence research. After that, GitHub Actions must gain a separately enforced and adversarially tested citationgate; the local exact-byte citationgate passed, but remote CI does not yet enforce it. Only then may a separately approved bandwidth-cap preflight be considered, followed—under another explicit GO—by a possible Pilot A. Pilot A may never claim raw state, liquidity, position-size impact, execution-grade returns, OOS readiness, or profitability. **Pilot B** remains NO-GO. This documentation authorizes no accepted Silver decode, CAR/range download, archive stream, slot processing, network shaping, or pilot.
