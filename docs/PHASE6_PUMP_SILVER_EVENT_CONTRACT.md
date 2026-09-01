# Phase 6A — Pump Silver event contract

> **Document status: HISTORICAL.** Legacy fixture/golden-vector evidence only; not universal Pump protocol truth or V2 research evidence. See [`HANDOFF_V2.md`](HANDOFF_V2.md).

## Status

**MERGED OFFLINE, FIXTURE-ONLY CONTRACT — `researchReady: false`.**

Phase 6A was squash-merged through PR #9 as `95511866c31b6325c2341b5b7a3b98c9503d85d1`; post-merge main CI run `32231262382` passed Node 953/953, Rust 60/60, TypeScript, builds, policy, transport isolation, push integrity, and clean-tree gates. It adds a canonical Pump `CreateEvent`/`TradeEvent` boundary above the merged Bronze capture and Phase-5 Rust reducer. It does not approve a historical dataset, process real archive/CAR/slot bytes, provide OOS or profitability evidence, or make any strategy result research-ready.

The implementation is intentionally not connected to Triton, Old Faithful streaming, ClickHouse, the paused backfill, the application runtime, or any deployment path.

## Pinned fixture provenance

The fixture-only registry is bound to:

- repository: `https://github.com/pump-fun/pump-public-docs`;
- commit: `9c82f61cb711b044a17f770ab8ce9f9bdf78f333`;
- IDL path: `idl/pump.json`;
- IDL SHA-256: `b90bc471327f671449271d5d1d42354d1fae6f5a06502f5834459a3108138e49`;
- Pump program: `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`.

This registry is explicitly fixture-only and unapproved. A content hash or official-doc commit is not activation-slot evidence. Real historical use still requires a separately reviewed, slot-effective provenance registry.

## Contract boundary

The contract:

1. decodes the complete pinned `CreateEvent` and `TradeEvent` Borsh payloads from Anchor self-CPI event data;
2. preserves every `u64`/`i64` amount as a decimal string;
3. binds events to exact signature, slot, transaction index, parent instruction, inner-instruction index, and stack height;
4. resolves legacy and v0 loaded-address transactions through the normalized Bronze boundary;
5. replays every public normalized input through the authoritative Bronze producer/canonicalizer before any promotion, rather than trusting `pumpCandidates` or shape-only assertions;
6. validates official parent discriminators, exact parent argument encoding, account-role layouts, every IDL-declared PDA/ATA constraint, event kind, direction, fees, reserves (`realTokenReserves <= virtualTokenReserves` and `realTokenReserves <= tokenTotalSupply`, while virtual pricing reserves may exceed supply), quote identity, and exact token/native transaction-net movement; the canonical Anchor `emit_cpi!` self-CPI has exactly one signing `event_authority` account meta, while the Pump program is identified by the inner instruction's `programIdIndex` and remains a parent account role rather than a second inner account meta;
7. reconciles successful legacy trades against raw lamport movements plus network fee and successful v2 trades against raw WSOL movements, then binds event real reserves to the corresponding post-state curve balances;
8. supports the pinned fixture variants `create`, `create_v2`, `buy`, `sell`, `buy_exact_sol_in`, `buy_v2`, `sell_v2`, and `buy_exact_quote_in_v2` only where the registry provides an explicit event-name binding;
9. retains a decoded failed transaction only when token state is unchanged and native state reflects fee-only rollback, while never setting `isExecutedTrade: true` for it;
10. snapshots the fixture-registry array length into a safe `1..16` bound, iterates only that bound, requires a dense own entry at each index, rechecks the length after normalization, and rejects the complete registry when the vector is stateful, sparse, malformed, or when any entry contains anything other than the exact four own registry data properties, exposes accessors, throws during bounded descriptor/value observation, or has non-canonical provenance text; normalized scalar fields (including the producer's 88-character signature maximum), arrays, log characters, instruction bytes, and cumulative account references are pre-bounded before producer replay, output retention, canonical hashing, or quarantine generation;
11. quarantines unknown, ambiguous, malformed, duplicated, role-conflicting, PDA-conflicting, decimal-conflicting, reserve-conflicting, fee-conflicting, or state-movement-conflicting evidence with bounded aggregate output;
12. emits a domain-separated, property-order-independent canonical SHA-256 while preserving own keys such as `__proto__`, `constructor`, and `prototype`;
13. hard-codes `researchReady: false` in the registry, transaction envelope, and event records.

## Production depth/accounting correction

The accompanying production correction is dimensionally explicit for both 6/9 and legitimate-scale 9/9 pools. RPC and Pump-account `u64` reserves remain canonical decimal strings whenever a JavaScript safe integer cannot represent them exactly. Constant-product buy and sell execution uses `bigint` rational arithmetic and floors at the pool boundary, so raw token and quote outputs are always whole minor units. Depth-aware entry persists the actual raw base-token quantity as a canonical decimal string together with its decimal domain; genuine entry impact is not capped by the flat-slippage fallback. Depth-aware exit sells exactly that persisted raw quantity into compatible current pool reserves, floors raw WSOL proceeds, and applies the separate simulated execution fee once with integer basis-point arithmetic. Reporting-only price and liquidity views may convert already-validated reserves to floating point, but those views never determine raw quantities or booked proceeds.

Malformed fees, unsafe numeric reserves, non-canonical or out-of-`u64` reserve strings, missing depth, decimal mismatch, unsafe proceeds/PnL, or an unsafe derived portfolio aggregate all fail closed: a new entry with explicitly invalid depth is rejected and an existing depth-aware position remains byte-equivalent on `HOLD`, including across a UTC-day boundary (no daily-loss reset is committed by the failed exit). Only genuine legacy positions without either raw-quantity identity field retain the prior flat-slippage fallback. Ledger validation accepts canonical positive raw strings (and safe integer legacy values) only when paired with a valid decimal domain, and rejects missing, orphaned, fractional, negative, unsafe, or out-of-domain depth fields. A real WAL roundtrip regression covers the large 9/9 position. These fixture/mock regressions do not constitute real fill-impact provenance or research approval.

The static transport policy retains generic descriptor reflection as forbidden. It has two narrow built-root exceptions: the Phase 6A `pump-silver-contract.js` registry normalizer may inspect only four direct literal keys on its exact `entry` parameter, and the Phase 6B `pump-silver-state-contract.js` `hasExactOwnKeys` helper may inspect only the loop-bound own-name of its exact `value` parameter. The latter call must initialize the exact local `descriptor`, whose symbol may be used only for a presence test, a strict `enumerable` comparison, or global `Object.hasOwn(descriptor, 'value')`; reading or aliasing `descriptor.value` is forbidden. Computed, concatenated, aliased, shadowed, nested-scope, wrong-file, wrong-function, wrong-parameter, unrelated descriptor reads, and descriptor-result capability escapes remain forbidden.

## Rust/TypeScript parity

`tests/fixtures/pump-silver/event-vectors.json` contains static, synthetic golden vectors for the complete pinned `CreateEvent` and `TradeEvent` wire schemas. TypeScript and Rust decode the same bytes into the same JSON values. The Rust decoder is a library boundary only; Phase 6A does not run the reducer on archive data or publish Silver slot output.

Phase 6B builds on this event boundary with a separate synthetic exact-byte account-state and provenance contract documented in [`PHASE6B_PUMP_SILVER_STATE_PROVENANCE_CONTRACT.md`](PHASE6B_PUMP_SILVER_STATE_PROVENANCE_CONTRACT.md). It does not change Phase 6A semantics and is not a callback/reducer integration.

## Deliberately excluded

Phase 6A does **not** include:

- holder, authority, creator-risk, or rug-risk time series;
- Gold features or strategy optimization;
- OOS results or registry approval;
- a real CAR download, archive stream, or slot pilot;
- ClickHouse or backfill work;
- Triton or any other live provider;
- deployment or application start.

## Next gate

Before any real historical use:

1. establish independently reviewed activation-slot ranges for every accepted IDL/discriminator layout;
2. process a separately approved bounded source pilot without weakening zero-cost or transport-isolation gates;
3. verify real callback/event/account-state semantics and deterministic Silver output;
4. approve the exact source/parser/export provenance tuple;
5. keep `researchReady: false` until those gates and the later state/risk contracts pass.
