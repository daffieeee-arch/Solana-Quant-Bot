# B5 — bounded nested Pump sell/event association

> **Document status: ACTIVE.** Local stacked follow-up from `765263f5d948576a7d294b0d1ef649be105f9e60`; no GitHub operation, acquisition or Project promotion. B4 remains open and B5 remains NEXT. See the [prior direct-sell evidence](B5_PUMP_SELL_OBSERVATION.md), which is preserved, not rewritten.

## Scope and source rule

Only the already captured height-two Pump sell calls in slot 422496004,
transactions 1002 and 1016, are added. The same exact 24-byte sell instruction,
367-byte event representation, 14 IDL accounts plus three documented remaining
accounts, all address/PDA checks, exact integers, successful transaction status
and complete Raw/receipt/Bronze bindings remain required. The original buy at
index 142 still rejects its unexplained 26th byte. Direct sells 153/996 retain
their original route and source receipt.

The new [source receipt](../../rust/of1-bronze-decoder/sources/pump-nested-sell-evidence.json)
binds the preserved Pump pin `9c82f61cb711b044a17f770ab8ce9f9bdf78f333`
and ordered inner-instruction/stack-height definitions from already retained
Agave `6c1ba34691f17ac902ae3d2e1147eed5723b9cef` and solana-message 3.0.1
source bytes. Each file has an exact path/hash and provenance status. No new
source download, dependency, program activation claim or router decoder.

## Own event, not a neighboring invocation

Both recorded groups have this order beneath outer instruction 2:

| Inner order | Height | Program role | Parent inner |
|---|---|---|---|
| 0 | 2 | Selected Pump sell | null (outer instruction) |
| 1 | 3 | Fee program | 0 |
| 2 | 3 | Token-2022 | 0 |
| 3 | 3 | Pump event-CPI | 0 |
| 4 | 2 | System program, subsequent sibling | null |

The selected subtree is `[0,4)`. Validate the complete recorded group before
selection, including calls after the event. Never sort, fill missing heights,
use log text as a fallback, or search a neighboring subtree for an event.
Exactly one immediate Pump event child must have the correct authority, exact
layout and matching mint/user/token amount. A deeper child's event is not the
selected sell's event. The outer program is recorded, not interpreted.

Message account flags remain checked as necessary capacity. Recorded compiled
CPI instructions contain indices, bytes and optional height, **not CPI signer
or writable flags**. Those flags remain `null` /
`UNAVAILABLE_NOT_RECORDED_IN_STATUS_METADATA`; no inference from PDA seeds or
transaction success. Every nested account row labels its message-minimum check
separately and sets `cpi_privileges_verified=false`.

Admitted Silver is an atomic recorded instruction/event fact, not proof of
complete CPI privileges, account writes, balance deltas, economic identity,
decimals, executable price, launch time, historical activation or an edge.
The observed zero quote-mint bytes are retained without inventing a mint.
`ENGINEERING_VALIDATION_ONLY` and root-to-slot membership `UNAVAILABLE` remain.

## Verification and visible output

The focused suite checks both authentic sealed sections plus synthetic negative
cases: missing/wrong heights, sibling/deeper calls, duplicate events, swapped
contexts, order corruption, every account, message capacity, failed transactions,
truncated events and missing provenance. A synthetic native Raw/receipt fixture
also executes the nested route without relabeling its provenance as authentic.

The existing Rust-generated `quality.html` shows all four sell outcomes,
17-account checks and the full ordered nested trace. `quality.json`,
`bronze.jsonl` and `silver.jsonl` retain the machine-readable evidence.
Execution identities, actual full-run counts, deterministic comparisons and
local gates are recorded separately after the fresh offline release execution;
they are not inferred from these test descriptions. GitHub checks are pending
because this delivery is explicitly local-only.

## Smallest next queryable-data step (not implemented here)

Make the bounded physical-writer decision explicit: one lossless Rust-owned
Parquet/Arrow projection of these existing Bronze/Silver records, with schema,
manifest and logical-hash parity against JSON. Demonstrate one read-only query
over slot/order, mint, raw quantities, provenance and unsupported outcomes.
This needs neither another download nor Python Pump decoding, a universal
router, a full lifecycle, strategy research or a new monitoring stack.
