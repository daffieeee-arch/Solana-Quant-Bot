# B4 Offline Remainder

> **Document status: ACTIVE.** Contract for the next offline B4 package.
> This is **not** live B4B and **not** an `ACQUISITION_LEASED` plan.
>
> Live leased acquisition stays reserved. See
> [`B4A_OFFLINE_RANGE_RECORDER.md`](B4A_OFFLINE_RANGE_RECORDER.md) § next
> steps and [`OF1_QUANT_RESEARCH_MEMO_V2.md`](OF1_QUANT_RESEARCH_MEMO_V2.md).

## Why this package exists

B4A (`scripts/b4a-offline-range-recorder.mjs`) records injected bytes with
`networkEnabled: false` and no HTTP client. That is a Fixture partial of
B4 / GitHub #83. It does **not** close B4.

The remaining offline gap is not "turn the network on". It is:

1. treat the Jetstreamer 0.7.0 SHA as a candidate until a formal pin
   review exists
2. keep HTTP / S3 / backend overrides default-deny
3. persist raw bytes and receipts **before** parse
4. close a requested **slot range**, not a full CAR object
5. keep the TUI against injected I/O
6. draft a run-plan template that stays `approved: false`

Until those exist, a later lease would encode the wrong unit of work
(full CAR vs slot window) and could silently accept an override URL.

## Name

Call this package **B4-offline remainder**.

Do **not** call it B4B. B4B is reserved for later *live* leased
acquisition after this remainder exists and a separate immutable plan is
approved.

## Non-goals

- No Triton / OF1 download.
- No credit spend.
- No `networkEnabled: true`.
- No HTTP client in the B4A recorder.
- No hosted Old Faithful gRPC probe.
- No Jetstreamer HTTP/S3/backend override wired to a real host.
- No authentic Raw / Bronze / Silver claim.
- No strategy, paper fill, or edge claim.
- No change to CI checks just to go green.

## Scope

### 1. Formal Jetstreamer pin review

Record, in-repo, a review of candidate
`JETSTREAMER_V0_7_0_GIT_SHA` = `cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24`
currently cited from `rust/old-faithful-pump-reducer/src/lib.rs`.

The review must say whether that SHA is acceptable as the *initial* V2
acquisition crate pin, or why it is not. A callback-type snapshot is not
that review. KNOWN_ISSUES #6 stays open until the review lands.

### 2. Default-deny override wrapper

Wrap or refuse the Jetstreamer knobs that can retarget the archive:

- `JETSTREAMER_HTTP_BASE_URL`
- `ARCHIVE_BASE`
- `ARCHIVE_BACKEND`
- S3-shaped overrides

Default is deny. A test must prove that a non-empty override fails closed
offline. Do not add a live client to exercise the deny path.

### 3. Raw bytes and receipts before parse

Offline fixtures must be persistable as:

- raw object bytes
- content identity (CID and/or SHA-256 as documented for that object)
- a receipt that names host class, requested range, byte count, and
  `acquired_at` as operational provenance only

Parse / decode happens after those artifacts exist. A parse success must
not be the only durable output.

### 4. Slot-range closure

B4A `deriveSegments` currently splits `[0, carSizeBytes-1]` — the full
CAR object. That is not the V2 slot-range closure.

The remainder must define, and test offline, a requested half-open slot
window `[startSlot, endSlot)` and refuse to treat a full-epoch CAR as
that window. Silent holes are `GAP` or `QUARANTINED`, never zero-filled.

`[422506000, 422506128)` may appear only as a **provisional example**.
It is not an approved plan.

### 5. TUI against injected I/O

Any progress UI stays bound to injected readers / writers. It must not
open a network handle. If a TUI cannot run without a live archive, it is
out of this package.

### 6. Draft run-plan template

Ship a template (see
[`B4_OFFLINE_REMAINDER_DRAFT_PLAN.json`](B4_OFFLINE_REMAINDER_DRAFT_PLAN.json))
with:

- `approved: false`
- `networkEnabled: false`
- `purpose: ENGINEERING_VALIDATION_ONLY`
- empty or explicitly unset host / range / budget fields until a later
  human fills and separately approves a real lease

Filling those fields in the template is not approval.

## Acceptance criteria

This remainder is `PASS` as an engineering package when all of the
following are true. It still does **not** make B4 `Proven` and still does
**not** authorize acquisition.

1. Jetstreamer pin review is committed and KNOWN_ISSUES #6 is updated to
   that review's conclusion (still open if the pin is only a candidate).
2. Override deny-path tests fail closed with no network.
3. An offline fixture can be written as raw bytes + receipt + identity
   **before** decode.
4. Slot-window tests refuse full-CAR closure and distinguish
   `UNAVAILABLE` / `GAP` / `QUARANTINED`.
5. TUI, if present, runs on injected I/O only.
6. The draft plan remains `approved: false` and `networkEnabled: false`.
7. `npm run ci:research-citations` and the scoped tests for new files
   pass. GitHub CI remains the authoritative full gate when the host
   toolchain is not the CI pin.

## Suggested files

Concrete paths may move during implementation. Do not create a network
client under a "test helper" name.

- pin review note: [`JETSTREAMER_V0_7_0_PIN_REVIEW.md`](JETSTREAMER_V0_7_0_PIN_REVIEW.md)
- offline implementation: `scripts/b4-offline-remainder.mjs` plus `tests/b4-offline-remainder.test.ts`
- this document stays the contract; do not silently widen it

## After this package

The separate draft lease is
[`B4_ENGINEERING_VALIDATION_LEASE_PLAN.md`](B4_ENGINEERING_VALIDATION_LEASE_PLAN.md).
It is still `approved: false` and is not live B4B. This remainder
document is not that approval.
