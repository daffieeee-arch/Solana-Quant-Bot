# Phase 3 — Pump v2 Bronze capture pilot

> **Document status: HISTORICAL.** Legacy fixture/evidence work only; not an active V2 architecture, runbook or authorization. See [`HANDOFF_V2.md`](HANDOFF_V2.md).

## Status

**IMPLEMENTATION PILOT ONLY — NOT RESEARCH READY.**

This phase proves the first deterministic, transport-free capture boundary for a future Old Faithful → Jetstreamer → ClickHouse v2 dataset. It does not stream Old Faithful, start ClickHouse, mutate the legacy dataset, approve a provenance tuple, or authorize strategy optimization.

The legacy v1 physical snapshot is pinned separately in [`research/V1_FORENSIC_MANIFEST.json`](research/V1_FORENSIC_MANIFEST.json). It remains `SUPERSEDED_NOT_PUMP_OOS_EVIDENCE`.

Rebuild that manifest read-only from an externally quiesced filesystem snapshot with an operator-supplied canonical timestamp:

```bash
python3 scripts/build-v1-forensic-manifest.py \
  --table-root /opt/data/backtest/jetstreamer/bin/store/17c/17c42dd2-2249-496e-8558-c6f41bedd731 \
  --ddl /opt/data/backtest/jetstreamer/bin/store/51b/51b58e89-f325-44d7-bc3c-00ed5ea19388/memecoin_swaps.sql \
  --provenance-root /opt/data/backtest/memecoin-backfill-provenance \
  --binary /opt/data/backtest/jetstreamer/target/release/memecoin-backfill \
  --status-dir /opt/data/backtest/backfill-state \
  --observed-at 2026-08-17T12:07:54.603Z
```

The generator emits JSON to stdout, starts no service, and writes no datastore file. It is specific to table UUID `17c42dd2-2249-496e-8558-c6f41bedd731` and exact DDL SHA-256 `dde1157bbc706ba26c5af5409b6ef15fae4fb5559de80d643e96846c9d21cf74`; neither identity is caller-overridable. It rejects symlinks in named inputs or ancestor components, external DDL/binary drift, table/provenance/status-tree drift, parser-repository HEAD/clean-state drift, non-round-tripping active-part names, zero mutation suffixes, overlapping block ranges, and merely part-like directories unless the complete payload-stem set exactly equals the recognized mark-stem set. Git reads internally disable optional locks so the parser repository index is not refreshed. It does not verify process/container writer state, payload bytes against ClickHouse checksums, or the truth of the supplied timestamp. The manifest therefore records `writerState: NOT_VERIFIED_BY_GENERATOR`, `payloadVerification: METADATA_ONLY_NOT_REHASHED`, and `observedAtSource: OPERATOR_SUPPLIED_NOT_VERIFIED_BY_GENERATOR`. Current coverage inputs conflict, so it remains `INCOMPLETE` with reconciliation status `UNRESOLVED_CONFLICTING_PROVENANCE_AND_SUPERVISOR_STATE`.

## Safety boundary

The pilot must remain:

- file/fixture-only and network-isolated through a transitive static allowlist, ESM/CJS runtime guards, and a fail-closed Linux seccomp filter that returns `EPERM` for network syscalls;
- independent of Triton, RPC, DAS, Titan, CoinGecko, Old Faithful HTTP, ClickHouse, Grafana, and app runtime;
- append/capture-oriented rather than strategy-oriented;
- exact-integer first: no `Float64` source-of-truth prices;
- fail-closed for malformed ordering, account resolution, balances, identity, and discriminator data;
- unable to add or self-approve a real `PUMP_SNAPSHOT_V2` provenance tuple.

Explicit non-goals:

- no ClickHouse start, DDL, DML, `FINAL`, `OPTIMIZE`, truncate, drop, or delete;
- no CAR streaming, slot-range replay, epoch pilot, or three-month backfill;
- no deployment or app restart;
- no strategy parameter search, expectancy claim, or configuration promotion;
- no historical holder/rug-risk parity claim;
- no pool-depth/fill-impact claim.

## Bronze source contract

A source record represents one ordered Solana transaction and must carry:

- canonical primary signature;
- safe non-negative `slot` and `transactionIndex`;
- canonical UTC block timestamp;
- explicit transaction success/failure state;
- static account keys plus loaded writable and loaded readonly addresses;
- complete top-level instructions;
- complete inner-instruction groups with unique valid parent indices;
- when RPC `stackHeight` is present, exact Solana semantics: transaction-level instructions are height `1`; CPI instructions are height `2` through `9` (the SIMD-0268 maximum);
- exact transaction fee in raw lamports;
- complete ordered transaction log messages;
- exact pre/post native lamport balances;
- exact raw pre/post token balances with account index, mint, owner, token-program ID, decimals, and decimal-string amount.

Resolved account order is strictly:

```text
static account keys
+ loaded writable addresses
+ loaded readonly addresses
```

Insert order is never chain order.

## First vertical slice

The first code slice exposes a strict `unknown -> PumpV2BronzeTransaction` capture function that:

1. validates transaction coordinates, signature, block time, execution state, exact fee, and log availability;
2. resolves the complete canonical account-key vector;
3. preserves every top-level and inner instruction with deterministic location coordinates;
4. records every structurally Pump-targeting instruction instead of returning only the first;
5. preserves discriminator bytes and labels only pinned known variants as `known_discriminator`, including explicit official-IDL versus observed-runtime provenance;
6. quarantines unknown Pump discriminators rather than dropping or guessing them;
7. binds an instruction mint to the independently derived `['bonding-curve', mint]` PDA when possible;
8. preserves exact integer native/token balances without deriving prices;
9. emits stable natural event keys from slot/transaction/instruction coordinates;
10. rejects malformed or sparse arrays, duplicate inner-parent groups, impossible reported stack heights, unsafe integers, balance-length drift, invalid/oversized base58 identity, oversized logs/instruction payloads, more than 4,096 transaction-wide instruction account references, and ambiguous mint/curve mappings; PDA derivations are cached once per account per transaction.

A failed Pump transaction remains Bronze source evidence. Bronze never emits an `isExecutedTrade` verdict: transaction success plus a known discriminator is still only a candidate until Silver validates the canonical account-role and event payload contract.

## Deterministic coordinates

Each captured instruction is keyed by:

```text
slot
+ transactionIndex
+ signature
+ instructionLocation
+ parentInstructionIndex (inner only)
+ instructionIndex
+ programId
+ discriminatorHex
```

`instructionIndex` is the top-level index for top-level instructions and the index within its inner group for CPI instructions. Future event decoding adds an explicit event/log ordinal rather than overloading instruction position.

## Required RED → GREEN test slices

### Slice A — strict transaction envelope

RED cases:

- unsafe or negative slot/transaction index;
- invalid signature;
- non-canonical timestamp;
- missing success/failure state;
- missing/malformed logs or a non-`UInt64` fee;
- an empty, duplicate, or malformed resolved account vector;
- pre/post native balances with lengths different from the resolved account vector.

GREEN result: a canonical immutable transaction envelope with exact decimal-string lamports.

### Slice B — loaded-address and instruction ordering

RED cases:

- Pump program or curve appears only through loaded addresses;
- duplicate/invalid inner parent index;
- out-of-range program/account index;
- insertion order differs from chain coordinates.

GREEN result: static+writable+readonly resolution and stable top-level/inner ordering.

### Slice C — all Pump candidates and quarantine

RED cases:

- multiple Pump instructions in one transaction;
- known and unknown discriminators coexist;
- non-Pump instruction carries Pump-like bytes;
- failed transaction contains a known trade discriminator.

GREEN result: all Pump candidates captured, unknowns quarantined, no failed transaction counted as executed.

### Slice D — structural mint/curve evidence

RED cases:

- curve does not match the independently derived PDA;
- no unique mint/curve pair exists;
- two candidate mints map ambiguously within one instruction.

GREEN result: canonical identity only when a unique PDA relation is proven; otherwise an explicit quarantine reason.

### Slice E — exact token balances

RED cases:

- malformed decimal amount;
- duplicate token balance account index;
- invalid mint, owner, or token-program identity;
- decimals outside the byte domain.

GREEN result: lossless bigint-compatible raw amounts and independent pre/post snapshots, including valid token-authority (`owner`) transitions; no `uiAmount`, cross-snapshot trade inference, or floating-point price authority.

## Follow-up gates

The Bronze slice is necessary but insufficient. Before any real dataset can become `READY`:

1. pin a slot-effective official Pump IDL registry and hashes;
2. decode canonical `CreateEvent`/`TradeEvent` payloads, reserves, fees, and event ordinals;
3. reconcile native SOL deltas against exact official account roles;
4. implement cohort-state capture for mint/freeze authorities, metadata changes, supply, and holder concentration;
5. port the approved golden corpus/contract to the actual Rust Jetstreamer plugin;
6. run one bounded slot-range pilot and then one complete closed epoch;
7. prove expected-versus-observed slot coverage, duplicate semantics, deterministic rerun hashes, storage use, and quarantine rates;
8. complete an independent parser/export review;
9. only then add a real provenance tuple to the Phase-2 registry;
10. separately approve any three-month run and subsequent strategy research.

## Mutation gates

Separate explicit execution approval remains required before:

- starting ClickHouse;
- creating v2 tables or views;
- starting Jetstreamer or any Old Faithful stream;
- running a slot-range/epoch/full backfill;
- changing Grafana datasources or dashboards;
- deleting, optimizing, deduplicating, or archiving v1 physical data;
- merging/deploying any runtime behavior change.
