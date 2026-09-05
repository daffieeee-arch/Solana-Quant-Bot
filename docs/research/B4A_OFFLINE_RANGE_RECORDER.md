# B4A Offline Range Recorder (Fixture-only delivery)

Scope: this contract is the bounded fixture/evidence stage for B4 acquisition under
issue #83. It captures only deterministic raw-byte handling and crash/replay semantics.

## Objective

Build a minimal, offline-first range recorder that:

- stores an immutable pre-parse acquisition plan,
- plans contiguous byte ranges from a fixed `response_entity_bytes` budget,
- records segment receipts and checkpoints with stable hashes,
- supports deterministic resume against unmodified plan/provenance,
- runs without live network calls in this phase,
- emits only fixture-compatible evidence for B4A.

## Exact scope for B4A

- Offline execution only.
- No live OF1/prod calls, no provider credentials, no network side-effects.
- Single official host allow-list (`files.old-faithful.net`) and explicit policy defaults.
- No dependency on alternate providers, S3, public RPC, proxies, or endpoint overrides.
- No claim of observed compatibility or historical activation.
- No B4B/production acquisition logic.

## Plan contract

`OFFICIAL_B4A_HOST = "files.old-faithful.net"`

A validated plan includes:

- `schemaVersion`: `B4A_OFFLINE_RANGE_RECORDER_PLAN_1`
- `planId`: caller-supplied textual ID
- `issue`: issue reference (`#N`)
- `sourceHost`: fixed to official host
- `source`
  - `epoch`
  - `carUrl`
  - `carSizeBytes`
  - `carSha256`
  - `indexUrl`
  - `indexSha256`
  - `indexEntries`
- `slotRange`
- `budget`
  - `maxResponseEntityBytes` (bounded, defaulted)
  - `maxRequests`
  - `maxDiskBytes` (must cover the planned raw object)
  - `maxRuntimeMs`
  - `responseTimeoutMs`
  - `requestRetries`
- `policy`
  - `hostAllowlist`
  - `networkEnabled` (must be `false` in B4A)
  - `fallbackHostAllowed` (`false`)
  - `alternateProtocolAllowed` (`false`)
  - `proxyAllowed` (`false`)
  - `s3Allowed` (`false`)
- `provenance`
  - `sourceFingerprint`
  - `indexFingerprint`
  - `codeFingerprint`
  - `toolchainFingerprint`

`validatePlan()` must reject:

- schema mismatch,
- missing/invalid issue,
- non-official host,
- policy relaxation,
- malformed hashes,
- non-positive metrics,
- oversized response-entity budget.

## Segment plan

- `deriveSegments(plan)` splits `[0, carSizeBytes-1]` into contiguous closed
  byte ranges with a step of `min(plan.budget.maxResponseEntityBytes, 16MiB)`.
- Segment IDs are deterministic: `<planId>-seg-<index>`.
- Missing segment metadata is not recoverable without checkpoint validation.

## Receipt and checkpoint state machine

### Receipt

Every segment capture records:

- `receiptVersion`
- `runId`
- `segmentId`
- `requestStart`/`requestEnd`
- `responseStatus`
- `responseEntityBytes`
- `responseTotalBytes`
- `responseEntitySha256`
- `receivedAt`

`responseEntityBytes` is the canonical budget metric.
Every observed request attempt is counted against `maxRequests`; the fixed
runtime and disk budgets are also hard stops.

### Validation rules

`applyObservedSegment()` accepts a segment only when:

- segment belongs to current plan,
- segment is not already completed,
- HTTP status is `206` or `200` (single segment only),
- `Content-Range` matches exact segment bounds for `206`,
- received bytes equal planned entity bytes,
- payload is written at the exact byte offset.

### Persistence and resume

- Raw bytes are written at explicit offsets in `b4a-offline-car.bin`; the file
  is never treated as an append-only stream.
- Segment receipts are appended as line-delimited JSON in `b4a-offline-receipts.ndjson`.
- `b4a-offline-checkpoint.json` stores
  - canonical `planHash`,
  - receipts,
  - provenance snapshot,
  - planned/observed response-entity counts,
  - completion status.
- Checkpoints are written to a temporary file and atomically renamed. Resume
  re-reads each captured range and verifies its receipt hash before exposing the
  next segment.
- `verifyCompleteRaw()` is the explicit final gate: only a complete object with
  the exact planned size and `carSha256` is accepted as complete raw capture.

`validateResumeState()` enforces:

- unchanged `planHash`,
- unchanged requested provenance fields,
- receipt array type integrity.

## State machine

1. **Idle**: validated plan exists, no checkpoint.
2. **Active**: checkpoints may be persisted after each accepted segment.
3. **Partial**: resume hydrates completed segments deterministically.
4. **Complete**: all planned segments captured.

Resumption is invalid if any plan, provenance, or checksum contract changes.

## Security and endpoint policy

- B4A forbids caller-selected endpoints.
- Host/policy is fixed as above.
- No endpoint override, no proxy, no backend/S3 indirection.
- Offline test fixture path is the only allowed operational route in this PR.

## Dependency policy

No new external network client is required for B4A offline fixture path.

## Acceptance criteria

- Deterministic plan hash and deterministic segment plan.
- Exact byte-bound enforcement on every segment.
- Deterministic checkpoint/resume with provenance binding.
- Duplicate/unknown segments rejected.
- Replay/partial receipt state is detectable via persisted state and summary.
- No live OF1 calls in B4A PR.

## Adversarial and property cases

- wrong host,
- wrong content-range,
- missing content-range for `206`,
- `200` on multi-segment plans,
- entity-byte mismatch,
- duplicate segment application,
- malformed/mutated checkpoint/provenance mismatch.

## Risks

- Any missing source checksum identity invalidates safe resume.
- Offline-only mode cannot prove authenticity/observed compatibility.

## Rollback

On any hard failure before B4B approval:

- retain checkpoints and receipts,
- remove only B4A test/runtime entry points,
- keep plan/reconciliation artifacts for forensic evidence.

## Project boundaries

This is B4A and remains **Fixture** evidence. B4B will be the separately approved live acquisition phase and must pass explicit lease/governance checks.
