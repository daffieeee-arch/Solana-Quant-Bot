# OF1 Rust planner — first correctness PR

> **Document status: ACTIVE — FIXTURE ONLY.** B4/#83 remains `In Progress / ACTIVE NOW / Unproven`; B5/#84 remains `Backlog / NEXT / Unproven`. No acquisition run is authorized.

## Implemented boundary

`rust/of1-range-recorder` owns the bounded **planning** path. The [fixture durability module](OF1_DURABLE_RAW_STORE.md) and separately enabled [loopback-only transport](OF1_OFFLINE_TRANSPORT.md) integrate it with immutable Raw/receipt publication and restart. Default builds have no transport capability. The [26-case parity inventory](../../schemas/acquisition/of1/transport-parity.json) maps the replaced JS implementations; neither historical tests nor current fixture integration establish authentic acquisition.

The [generated plan evidence](OF1_OFFLINE_PLAN_EVIDENCE.md) and [JSON twin](../../schemas/acquisition/of1/offline-plan-evidence.json) expose six requests over three fixture-index-present slots and one index-reported absent slot. No CAR or historical chain bytes were obtained. Planned payload bytes are 160, not a received-byte count. The expanded synthetic index is 5,184,000 bytes and lives only in temporary test directories before Rust reads it.

## Official format identity, not a runtime dependency

The [source receipt](../../schemas/acquisition/of1/source-receipt.json) binds official repository `anza-xyz/jetstreamer`, commit `cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24` and exact file/blob/content identities. The [pinned index source](https://github.com/anza-xyz/jetstreamer/blob/cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24/jetstreamer-firehose/src/index.rs) describes 12-byte little-endian records: `offset:u64` followed by `length:u32`, indexed by slot ordinal. The [pinned epoch helper](https://github.com/anza-xyz/jetstreamer/blob/cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24/jetstreamer-firehose/src/epochs.rs) supplies the `epoch * 432000` OF1 convention, not an independently proven chain-history epoch schedule.

Only format facts are implemented. No upstream crate or code is vendored. The pinned MIT license was inspected. Legacy-index fallback, HTTP/S3 base overrides, public-RPC helper and upstream network tests are **not** imported or executed.

## Types and invariants

- `OfflinePlan`: exact schema, pinned format identity, mandatory epoch bounds, half-open slot window, object length, index SHA-256, source/code/toolchain fingerprints and explicit budgets. Unknown/duplicate/missing JSON fields, fractional/negative quantities and unsupported authority fail closed. Example fingerprints are synthetic context, not runtime attestations.
- `ValidatedPlan`: private constructor after validation. Only `OFFLINE_FIXTURE`, `ENGINEERING_VALIDATION_ONLY`, `approved:false`, `network_enabled:false`. No endpoint input exists; host/path identities are constructed internally.
- `PersistedIndex`: reads a bounded existing regular file and verifies exact length and SHA-256 before record interpretation. No caller-labelled observed slots. This is not an acquisition receipt/publication implementation.
- `RangePlan`: requests in deterministic slot/byte order linked to index ordinals. No prefetch, gap coalescing or full-CAR split. `(0,0)` means `INDEX_REPORTED_ABSENT`; contradictory zero records, out-of-object ranges, overflow and requested-range overlap fail closed.
- `max_plan_entry_bytes` bounds dynamic request/absence entry storage before allocation, not process RSS. Request count, planned payload bytes and minimum index-plus-payload disk size are checked before chunk expansion. Runtime reservation/accounting is not implemented here.
- Stable struct serialization binds the plan hash. A changed source/index/code/toolchain field changes identity; later resume must also attest those against the real process and durable receipts.

Budgets are per-plan input, not universal acquisition constants. The synthetic demonstration window is not a replacement for either proposed authentic engineering window. A full epoch index fixture is not a full epoch CAR download.

## Evidence limitations

Index-to-byte planning is fixture-compatible only. Arbitrary payload bytes or supplied slot lists cannot produce chain-coverage claims. Slot semantic membership and block/transaction/Pump counts remain `UNAVAILABLE_NOT_DECODED_IN_B4`; CID verification and epoch-root membership remain `UNAVAILABLE`. A hash of partial local bytes never verifies a declared whole-epoch hash.

## Tests, dependency and CI boundary

Rust tests cover source drift, schema/authority rejection, old full-epoch/negative-slot regressions, malformed/index-hash/size/offset checks, allocation amplification, determinism and 128 seeded chunking cases. An independent JS writer produces sealed LE index bytes; Rust interprets them. This is a structural cross-language check, not independent historical authority.

The [dependency review](../../rust/of1-range-recorder/dependency-review.json) lists all locked packages, license expressions and build-script presence. Dependencies are serde/JSON, hashes, typed errors and tempfile for tests. The durability module uses the pinned standard library's file lock; `fs2` is unnecessary. Existing lockfiles remain unchanged. No native installation, Solana SDK, Jetstreamer runtime, database or HTTP dependency is added.

Build-script roles are compiler/platform cfg and capability detection for generic-array, proc-macro2, quote, serde, serde_core, serde_json, thiserror, zmij, libc, getrandom and rustix. Their registry checksums are lock-bound. Default builds, tests and planner/storage reports use the existing syscall network-deny launcher; the separately enabled loopback seam follows the [transport gate](OF1_OFFLINE_TRANSPORT.md). Future manifest/lock changes fail before CI fetch until reviewed; resolved license/build-script metadata must match the review.

```bash
node scripts/assert-of1-planner-offline.mjs --static
node scripts/assert-of1-planner-offline.mjs --all
node scripts/assert-of1-planner-offline.mjs --print
```

Use pinned Node 22.23.2/Rust 1.97.1. `--all` retains socket-denied planner/durability gates and separately verifies the explicitly enabled loopback-only integration, including twice-regenerated artifacts. `--print` prints artifact text for review without overwriting committed evidence. Temporary fixtures are removed afterward. The default npm build and Roadmap Sync remain unchanged.

## Remaining PRs and preservation gate

1. **Planning, PR #101:** Rust schema/index/range planning and visible fixture report.
2. **Durability, PR #102:** the [fixture store](OF1_DURABLE_RAW_STORE.md) addresses Raw/receipt mixed generations and overwrite, immutable publication, reservations before dispatch, failed-attempt/restart accounting and persistent deadline. The old budget bug predates Cursor in PR94.
3. **Offline integration:** [loopback-only range/stream tests and visible restart progress](OF1_OFFLINE_TRANSPORT.md), no external transport; [invariant parity](../../schemas/acquisition/of1/transport-parity.json) gates replacement of both JS implementations.
4. **Lease/evidence:** separate metadata GO from payload GO, justify budgets, distinguish engineering failure/insufficient data/edge falsification, replace incomplete approval checklists and explicitly pin historical PR evidence. Live activity remains unauthorized.

The original [planning-stage salvage inventory](../../schemas/acquisition/of1/test-salvage.json) remains byte-identical historical evidence of that stage's retention and open regressions. Its [integration/parity successor](../../schemas/acquisition/of1/transport-parity.json) binds all 26 original tests, useful replacement invariants and deliberately retired invalid expectations. B3 matrices and historical ledgers remain unchanged. Git is the archive. Rollback is a normal revert; user capture directories are never modified or imported.
