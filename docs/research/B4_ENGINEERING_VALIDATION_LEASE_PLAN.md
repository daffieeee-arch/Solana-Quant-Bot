# B4 Engineering-Validation Lease Plan (draft)

> **Document status: ACTIVE.** Draft `ACQUISITION_LEASED` plan for one
> `ENGINEERING_VALIDATION_ONLY` slice. **`approved: false`.**
> **`networkEnabled: false`.** Filling these fields is not approval.
>
> This document does not authorize a Triton / OF1 call, credit spend,
> client construction, or live B4B. A later human approval must flip
> `approved` and record operator plus timestamp before any process that
> can open a network handle is built or run.

Machine-readable twin:
[`B4_ENGINEERING_VALIDATION_LEASE_PLAN.json`](B4_ENGINEERING_VALIDATION_LEASE_PLAN.json).

## Why this plan exists

The offline B4 remainder is implemented. B4/#83 still has no authentic
bytes. Triton-cost-safety requires an immutable plan with purpose, host,
range, budgets and a hard stop **before** any leased process.

This is that plan, still unapproved.

## Purpose and class

- **Purpose:** prove or falsify bounded OF1 acquisition mechanics
  (receipts, slot-range closure, coverage/gaps, raw-before-parse) on one
  small authentic window.
- **Slice class:** `ENGINEERING_VALIDATION_ONLY`. Forever excluded from
  strategy, edge, Gold, or `RESEARCH_READY` claims.
- **Expected terminal states:** `PASS`, `FALSIFIED`,
  `INSUFFICIENT_SAMPLE`, `ABORTED_BUDGET`, `QUARANTINED`.
- A failed or empty first slice does **not** authorize a larger
  download or a second range.

## Selection rationale

Chosen from source and engineering properties only. No OF1 payload, no
token list, and no outcome was inspected for this plan.

| Property | Choice | Why |
| --- | --- | --- |
| Host | `files.old-faithful.net` | Only allowed OF1 acquisition host |
| Unit | half-open slot window, not a CAR | V2 closure is `[startSlot, endSlot)` |
| Epoch | 978 | After documented poor coverage (epochs 0–156) and after CU fields are populated (epoch 450). Same walking-skeleton epoch already used as a *fixture* epoch, not as observed activation |
| Window | `[422496000, 422496128)` | First 128 slots of epoch 978: `978 * 432000 = 422496000` |
| Size cap | 128 slots | Smallest closed window that still has a named budget; not a full epoch (`[422496000, 422928000)`) |

`[422506000, 422506128)` remains the old **provisional example**. It is
**not** this lease's range and must not be fetched because it appeared
in earlier docs.

Non-claims for this selection:

- no profit, winner, or token-performance selection
- no claim that the window contains Pump `buy` + `TradeEvent`
- if the window has no authentic Pump pair after decode agreement, the
  result is `INSUFFICIENT_SAMPLE`, not permission to hunt a richer range

## Host, path and deny policy

- **Allowlist:** `files.old-faithful.net` only.
- **Capability class:** `ACQUISITION_LEASED` after approval; currently
  `DOCUMENTATION_ONLY` for all docs hosts.
- **Redirects / mirrors / public RPC / S3 / backend overrides:** deny.
- **Jetstreamer knobs:** default-deny
  (`JETSTREAMER_HTTP_BASE_URL`, `ARCHIVE_BASE`, `ARCHIVE_BACKEND`,
  S3-shaped overrides). The [current offline transport](OF1_OFFLINE_TRANSPORT.md)
  has no such override consumer; a future live wrapper remains separately gated.
- **Hosted Old Faithful gRPC / RPC:** not selected; do not probe.
- **Candidate crate pin:** `cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24`
  (candidate only; see
  [`JETSTREAMER_V0_7_0_PIN_REVIEW.md`](JETSTREAMER_V0_7_0_PIN_REVIEW.md)).

Object identities (epoch CAR SHA-256 / CID, `slots.txt` digest, index
digests) are `UNAVAILABLE` until a leased run records them from official
sidecars. Missing identity is not a guessed hash. A full-epoch CAR is
denied even if the host offers one.

## Proposed budgets (unapproved)

Every number is a per-run proposal. It is not an architecture constant.

| Meter | Proposed hard stop | Warning |
| --- | --- | --- |
| Requests (every attempt counts) | 16 | 13 |
| Retries per request | 2 | — |
| Concurrency | 1 | — |
| Single response entity | 16 MiB | 12 MiB |
| Total response bytes | 128 MiB | 100 MiB |
| Disk high-water | 256 MiB | 200 MiB |
| Required free disk before start | 512 MiB | — |
| Memory | 512 MiB | 400 MiB |
| Runtime | 30 minutes | 24 minutes |
| Response timeout | 30 seconds | — |

Cost / prepaid Triton balance: **`NOT_CONFIRMED`**. Do not invent a
dollar cap. Historical $125→$0 notes are not current prices. Approval
requires an explicit current cost/availability confirmation from the
operator.

## Hard stop

Stop immediately, persist abort receipts, and do not resume when any of
these hit:

- any hard budget above
- non-allowlisted host or redirect
- any override env/knob
- attempt to fetch a full-epoch CAR
- `approved != true` or `networkEnabled != true`
- identity / coverage replay failure
- process crash; resume only after plan hash, source identity and
  completed-content hashes revalidate

No auto-expansion of range, requests, bytes, disk or runtime.

## Resume, abort, quarantine

- Resume is identity-bound (plan hash + source/index fingerprints +
  already-written raw hashes).
- Silent holes are `GAP` or `QUARANTINED`, never zero-filled.
- `UNAVAILABLE` remains distinct.
- `acquired_at` / `processed_at` are operational provenance only.

## What approval still requires

A human must record, in the JSON and in this document:

1. `approved: true`
2. `networkEnabled: true`
3. `operator`
4. `approved_at` (RFC3339)
5. `cost_confirmation` other than `NOT_CONFIRMED`

Until those five fields exist, no acquisition client may be constructed.

## Non-goals

- No download on the strength of this draft.
- No B4B network client in this change.
- No B5 Silver, Gold, Observatory, or edge claim.
- No change to B4A `networkEnabled`.
