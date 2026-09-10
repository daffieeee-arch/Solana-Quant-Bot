# B5 entry gate — Raw → Bronze → Silver

> **Document status: ACTIVE.** Entry criteria for B5/#84. This document does
> **not** start B5, authorize acquisition, or claim authentic Bronze/Silver.

## Blocked until

1. B4/#83 has verified authentic Raw for the approved leased range, with
   immutable receipts, coverage/gap ledger and deterministic resume evidence; and
2. the payload decision packet is either executed under an explicit GO or
   superseded by a newer approved packet; and
3. no engineering-validation Raw is relabelled as `RESEARCH_SAMPLING`.

Current state: **blocked**. Metadata-only evidence exists; CAR payload is not
authorized. See [`B4_PAYLOAD_RUN_DECISION_PACKET.md`](B4_PAYLOAD_RUN_DECISION_PACKET.md).

## Required observable outcome (when unblocked)

From the roadmap / issue #84:

- Rust-owned or Rust-authorized lossless Raw → Bronze → Silver;
- immutable manifests with provenance, quarantine and coverage;
- static HTML/JSON data-quality report;
- one token-lifecycle report on authentic facts;
- explicit PR-5 decision for the physical Parquet writer.

## Explicit non-goals

- Gold features/labels or walk-forward edge claims;
- interactive Research Observatory (B6);
- repairing frozen legacy paper fills;
- treating event prices as executable liquidity;
- silent missing→zero mappings.

## Language boundary

- Rust produces or authorizes canonical Bronze/Silver records.
- Python may only materialize approved records losslessly after the PR-5 writer
  decision; it must not decode Pump wire formats or invent Silver semantics.

## Evidence class

First authentic Bronze/Silver on an `ENGINEERING_VALIDATION_ONLY` slice may reach
`Engineering Validation` mechanics evidence and remains **permanently excluded**
from edge/profit claims. A later `RESEARCH_SAMPLING` slice needs its own
outcome-independent plan.
