# Phase 4 — Old Faithful / Jetstreamer adapter boundary

## Status

**MERGED CONTRACT BOUNDARY — `researchReady: false`.**

Phase 4 was squash-merged through PR #5 on `main` as `c7a66292e2b02bc33999e558017a3a742498285d`. It contains only a transport-free TypeScript contract boundary plus fixture tests. It has not streamed or downloaded Old Faithful CAR bytes, run a real Jetstreamer reducer or slot pilot, started ClickHouse, restarted the paused backfill, approved a real provenance tuple, or produced strategy evidence.

## Pinned source contracts

- Old Faithful epoch artifacts are published per epoch as CAR bytes, a CAR SHA-256 file, an epoch CID, a slot inventory, recap metadata, and indexes.
- The adapter contract was checked against Jetstreamer `v0.7.0` at `cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24`.
- `TransactionData` exposes the slot, `transaction_slot_index`, signature, vote classification, status metadata, and decoded versioned transaction.
- `BlockData` supplies block time separately. A real reducer must therefore buffer transaction projections by slot and join them only when the matching block callback arrives.
- `PossibleLeaderSkipped` is provisional: it may mean a truly skipped slot or a late block. It is not archive-completeness proof by itself.

## Implemented boundary

`src/research/old-faithful-jetstreamer-adapter.ts` adds:

1. `OLD_FAITHFUL_EPOCH_SOURCE_1`
   - exact-key, fail-closed source validation;
   - structurally decoded CIDv1 DAG-CBOR/SHA-256 epoch CID, CAR SHA-256, and exact CAR byte size;
   - exact slot-inventory SHA-256, byte size, entry count, first/last slot;
   - exact full epoch range derived from the pinned Jetstreamer `epoch * 432000` contract;
   - domain-separated source identity:
     `SHA256("OLD_FAITHFUL_OF1_SOURCE_MANIFEST_1\n" || canonicalSourceBytes)`.
2. `JETSTREAMER_ADAPTER_PROVENANCE_1`
   - separately pins Jetstreamer revision, plugin revision, and plugin-source SHA-256;
   - has its own exact-key validation and domain-separated hash, so changing processor bytes does not change source identity.
3. `JETSTREAMER_TRANSACTION_BLOCK_1`
   - explicit transaction/block slot equality;
   - non-vote Pump projection only;
   - exact transaction index, signature, Unix block time, native/token integer strings, static and loaded addresses, top-level and inner instructions;
   - delegates initial Bronze normalization and Pump quarantine semantics to the reviewed `PUMP_V2_BRONZE_TRANSACTION_1` boundary.
4. `OLD_FAITHFUL_PUMP_V2_OBSERVATION_1`
   - binds every Bronze transaction to both normalized source identity and separate adapter provenance.
5. `OLD_FAITHFUL_COVERAGE_LEDGER_1`
   - takes a separate bounded requested half-open range inside the full source epoch;
   - verifies the complete official slot-inventory bytes against the source manifest before filtering to that requested range;
   - requires canonical newline-delimited, strictly increasing safe-integer slots;
   - reconciles block callbacks with slots present in the pinned archive inventory;
   - resolves a provisional skip only when a matching inventory-backed block callback exists;
   - treats exact retries idempotently, enforces unique `(slot, transaction_slot_index)` and signature identities, and rejects conflicts;
   - revalidates exact Bronze/candidate/quarantine structure, carries the static-account boundary, re-derives candidates and canonical event keys from validated Pump instructions, binds known discriminator semantics, resolved account/index identity, token-balance ordering/ranges, stack-height rules, producer log/reference budgets, bounds canonical observation bytes, and hashes order-independent canonical evidence;
   - binds transaction observations to matching non-skipped blocks and exact block times;
   - labels transaction coverage as `PUMP_V2_NON_VOTE_ONLY`;
   - requires both complete callback coverage and exact inventory agreement before `archiveSlotInventoryReconciled: true`;
   - keeps `researchReady: false` even when callback and archive-slot inventory reconciliation pass.

`archiveSlotInventoryReconciled` means only that the supplied callbacks agree with the hash-pinned slot inventory for the requested range. It does **not** prove that the complete CAR bytes were downloaded and hashed, that every transaction was emitted durably by a real reducer, or that Silver/Gold research data is approved.

## Tests and merge gates

`tests/old-faithful-jetstreamer-adapter.test.ts` covers:

- canonical transaction/block-to-Bronze projection with separate source and processor provenance;
- manifest/CID/hash/epoch-range rejection, structurally invalid CID rejection, and unknown-field rejection;
- transaction/block slot binding, range binding, vote rejection, canonical block time, and static-account boundary enforcement;
- complete-inventory filtering to a bounded requested range;
- out-of-order callbacks, exact block retries, provisional skip→block resolution, post-resolution provisional retry deduplication, incomplete-callback honesty, and archive slot-inventory reconciliation;
- exact and property-reordered transaction retry deduplication;
- conflicting `(slot, transaction_slot_index)` or signature identity rejection;
- fabricated candidate, sparse instruction, unknown-field, and oversized nested observation rejection.

Fresh certified Node `v22.23.2` results, re-proven by post-merge main CI run `32072251102`:

- full suite: **785/785 passed across 68 files**;
- TypeScript: clean;
- build: green;
- repository policy: PASS;
- research transport-free built graph: PASS;
- frontend build: green with the pre-existing non-blocking bundle-size warning.

## Phase 5 open pull request

Open PR #7 contains the committed and pushed fixture-verified read-only, file-output-only Phase-5 Rust reducer against transport-free callback-type snapshots pinned to Jetstreamer v0.7.0 commit `cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24` and `solana-runtime` v3.1.12. It remains unmerged, has processed no real archive/CAR/slot data, and remains `researchReady: false`. The vendored packages record the audited upstream `firehose.rs` SHA-256, crates.io runtime checksum, and reward-type source SHA-256 while excluding HTTP, QUIC, Tokio, socket, metrics-client, and archive-client implementations. It:

1. projects the pinned `TransactionData`/`BlockData` callback payload fields for legacy and v0 transactions without lossy numeric conversion;
2. buffers by `(slot, transaction_slot_index)` until matching `BlockData::Block` supplies time;
3. preserves failed transactions, loaded-address order, inner CPI coordinates, token owners/program IDs, and raw amounts;
4. handles provisional `PossibleLeaderSkipped` callbacks without prematurely finalizing absence;
5. writes immutable per-slot output plus crash-safe WAL/checkpoints and an append-only coverage ledger; WALs, checkpoint generations, and slot artifacts are fully written and synced at non-authoritative deterministic temporary paths before atomic hard-link publication, while startup discards bounded recognized crash residue (including the prior UUID slot-temp format) and retains fail-closed validation for malformed authoritative files; startup re-derives canonical slot bytes and transaction/block semantics from checkpoint state before WAL publication, enforces the current output and manifest-derived artifact-count bounds before recovery mutation, preserves invalid WAL evidence, refuses already-covered WAL slots, and refuses to recreate a missing authoritative coverage ledger over established state; an active writer pins and revalidates the output, slots, and checkpoints directory identities, revalidates the exact artifact/coverage/state set before every commit, and synchronously resolves a durable WAL before accepting a retry;
6. on Linux, maps the same-effective-UID, normalized-output namespace to a System V semaphore collision registry whose slots store complete SHA-256 fingerprints and use separate `SEM_UNDO` active-writer semaphores; distinct full namespaces sharing the 32-bit registry key do not alias, process death releases active ownership without pathname or `/proc` discovery, incompatible/full registries fail closed, and external plus in-output hard-linked advisory-lock paths remain defense in depth against accidental/pathname-level conflicts;
7. remains independent of ClickHouse and the paused legacy backfill.

This is not merged or real-archive proof. `researchReady` remains hard-coded false.

Before any real archive read, run, or slot pilot:

- merge PR #7 only after a separate explicit merge GO and green exact-head GitHub CI;
- keep every review, test, and CI result bound to the exact PR head;
- obtain explicit user approval for the bounded slot range and source artifacts;
- set byte/time/storage limits and an abort policy;
- verify outputs and inventory reconciliation before considering one complete epoch.

No deployment, app start, Triton activation, ClickHouse mutation, Old Faithful CAR download/stream, real slot pilot, or backfill restart is authorized by this merged contract boundary.
