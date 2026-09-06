# OF1 fixture Raw/receipt durability

> **Document status: ACTIVE — FIXTURE ONLY.** Second bounded B4 repair after [the index planner](OF1_RUST_PLANNER.md). B4 remains open, `In Progress / ACTIVE NOW / Unproven`; B5 remains `Backlog / NEXT / Unproven`. No acquisition run is authorized.

## Storage contract (PR #102 baseline)

`rust/of1-range-recorder/src/durable.rs` accepts the validated offline plan, the exact persisted index and injected response bytes. It has no HTTP client, provider configuration or decoder. It creates a new run directory, never imports or overwrites an old JS capture. The [executed fixture report](OF1_DURABILITY_EVIDENCE.md) and [JSON twin](../../schemas/acquisition/of1/durability-evidence.json) show durable attempts, immutable publication, unchanged deadlines and corruption rejection.

The separate [offline transport integration](OF1_OFFLINE_TRANSPORT.md) drives this store from a local fixture response, with attempt-level streaming evidence and retry comparison. Its [end-to-end report](OF1_TRANSPORT_EVIDENCE.md) does not change the evidence level or authorize external acquisition.

The run contains immutable `run.json` and `index.raw`, a retained OS writer lock, numbered immutable reservation files in `attempts/`, non-authoritative `pending/` artifacts, and complete Raw/receipt pairs in `published/`. There is no mutable checkpoint and no auto-repair or cleanup of ambiguous evidence.

## Publication and restart contract

1. Validate the whole existing store and clocks before a new attempt. Hold one nonblocking `std::fs::File::try_lock`; a process exit releases it. Detect replacement of the root/lock inode.
2. Check request count, per-request retry limit, cumulative charged entity bytes and space for the candidate. Persist and fsync the reservation, link it without replacement into `attempts/`, then fsync that parent **before** returning a run-bound, non-cloneable permit.
3. Validate the injected exact `206` status, half-open byte range, total object length and complete payload length. Errors stop that writer; the reservation is not refunded.
4. Write Raw to a new candidate directory, fsync Raw, write/fsync the complete receipt, then fsync the candidate directory. No consumer can read it as published evidence yet.
5. Recheck original deadline, timeout and store integrity; rename the complete directory within the same filesystem and fsync both parents. Publication selects the pair together. A post-rename error is ambiguous, never permission to repeat the write: reopen and verify exact state.
6. Resume revalidates the exact plan, pinned source/index, declared code/toolchain context, measured current-executable SHA-256, stored index and every published pair. Reject minimal/stale/cross-run receipts, wrong request/attempt identity, altered bytes, unexpected entries and incomplete canonical records.

Pending files remain forensic material, count toward disk usage and never become published evidence automatically. A fully prepared but unrenamed candidate still requires a new charged attempt after restart. A torn/unmatched reservation intent is **ambiguous**: it could represent an early crash or loss of a spent canonical record. Resume refuses it rather than refunding a possibly dispatched attempt; this includes the pre-reservation-publication crash seam. No automatic repair is attempted. Existing published requests cannot be overwritten or silently redownloaded.

## Budget and time semantics

- Every canonical reservation is permanently spent, including failed/interrupted attempts. `max_requests` includes retries. `request_retries` applies per planned request, not per process.
- `charged_entity_bytes` conservatively sums full requested response-entity allowances; it is **not** a claim of exact bytes received before a process died. Verified receipt bytes are reported separately. Unreceipted received-byte totals are `null`/`UNAVAILABLE`, never zero.
- Disk checks include the index, run metadata, reservation/staging/publication files and temporary hard-link names. The local charge is the maximum of 4-KiB-rounded logical file bytes and reported allocated blocks; directories are charged at least 4 KiB or their allocated blocks, whichever is greater. It is a conservative application accounting policy, not an exact filesystem-wide physical-byte measurement. No refunds/pruning occur on resume.
- One original wall deadline and boot-elapsed deadline are persisted at creation. Linux `/proc/uptime` includes process downtime; boot identity must remain unchanged. Reboot, detected wall/boot rollback, expiration (including equality) and response timeout fail closed. There is no automatic lease renewal. `Store::inspect` returns only an integrity-checked forensic summary after expiration, never a writer/permit; evidence is not hidden by a budget/deadline stop.
- The executable hash is measured locally on create/resume. The planner's synthetic source/code/toolchain labels remain declared fixture context, not authentic provenance or a remote attestation. A rebuild may require a fresh fixture run; resume never silently accepts a different executable.

The PR #102 storage-only report does not meter a streaming network transport; the separate integration report exercises that seam on loopback fixtures. Byte receipt timestamps remain operational fixture clocks, never historical feature/decision timestamps. Time is rechecked after reservation fsync before returning a permit, and after the pre-publication integrity audit immediately before rename. These are deadline gates, not real-time interruption of a stalled synchronous filesystem call. Filesystem `fsync` semantics and a non-adversarial local kernel are prerequisites. In-process crash seams and real child-process exits test ordering, not physical power-loss behavior or coordinated rollback of the entire store. The small-fixture audit re-reads captured content; scale/concurrent-reader performance is not claimed.

## Verification and unchanged boundaries

Regression tests cover mixed generations/minimal receipts, no overwrite, interrupted attempts across multiple restarts, retry/byte/disk caps, original deadlines/timeouts/boot changes, exact source/index/executable context, every publication seam and process-released locking. The deterministic report executes its scenarios; it is not a hand-authored success checklist. The existing syscall-deny gate retains default crate builds/tests and both planner/storage report generators, including the local same-test-binary crash children. The feature-enabled loopback integration is verified separately.

No dependencies or toolchains are added. The [integration parity inventory](../../schemas/acquisition/of1/transport-parity.json) records the test-backed replacement of both old JS recorders/tests; this storage report's earlier evidence remains unchanged. No B3 code/evidence, historical migration ledger, Project schema or ordinary Roadmap Sync is changed. Rollback is a normal Git revert; retain run artifacts and do not silently import them into an older store schema.

## Required later lease/evidence step

Keep the two separate GO gates for metadata/index acquisition and payload acquisition. That planned step must justify each proposed request/entity-byte/disk/runtime/retry cap from measured source/index sizes and bounded amplification, record expansion/stop rules, and obtain a concrete approved run plan. Numbers in fixture reports are test parameters, not approved live budgets.

It must distinguish **engineering failure** (acquisition, integrity, budget or infrastructure failure), **insufficient data** (coverage/sample/sufficiency gates not met), and **edge falsification** (a preregistered hypothesis fails on valid, sufficient, appropriately costed research evidence). An exhausted engineering budget or failed download is neither proof of no edge nor research falsification. This PR does not alter lease flags or introduce statistical decisions.
