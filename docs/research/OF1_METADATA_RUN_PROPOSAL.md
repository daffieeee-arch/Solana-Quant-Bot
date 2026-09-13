# Next OF1 metadata run — review proposal, not authorization

> **Document status: ACTIVE — UNAPPROVED TEMPLATE.** The separately approved
> 2026-09-06 [metadata run](OF1_STAGED_ACQUISITION.md#observed-checksum-compatibility-offline-correction)
> completed; its expired approval is not this template and authorizes no new run.
> B4 remains Unproven; the [implementation](OF1_STAGED_ACQUISITION.md) is Fixture.
> Metadata GO must name the exact generated plan hash, binary, operator and times.

## Proposed scope and unchanged caps

Newly prepared successors bind the
[versioned clock contract](OF1_STAGED_ACQUISITION.md#versioned-utc-provenance-and-boot-deadlines).
The same Rust `clock-preflight AGGREGATE_JSON` contract observes raw UTC but gates
on nondecreasing same-boot elapsed time. After a new explicit GO only, sample the
actual T0 pair once and retain the approval anchor; initialization is bounded by
T0 boot time +10 minutes and expiry by +20 minutes. No restart/UTC correction
resets either. Old strict-clock GO/stop evidence remains intact; it does not
authorize the new binary. No lease is registered during offline preparation.

The table and twelve-attempt calculation below preserve the engineering-template
allocation. The fixed research-pilot successor is generated with
`metadata-pilot-proposal`, not by relabeling an old run: seven metadata attempts
(at most three per operation), 15,576,576 reserved entity bytes and 600,000 ms.
Together with nine separately proposed payload attempts / 10,990,731 bytes /
360,000 ms this allocates sixteen attempts / 26,567,307 bytes / 960,000 ms within
the unchanged aggregate limits below. The selection remains
`[422669516,422669519)` with its original seed/algorithm. See the
[manifest-bound sample contract](B5_MANIFEST_SHARDS_AND_SAMPLE_IDENTITY.md).

New proposals bind `download_rate` to the exact executable/aggregate approval
target: 700 decimal Mbps = 87,500,000 response-entity bytes/s shared across
cooperating same-user official captures, concurrency one, maximum 65,536-byte
short burst. This is not a physical wire/TLS/headers rate guarantee. The
[limiter contract](OF1_STAGED_ACQUISITION.md#shared-default-download-rate) defines
empty-start/restart accounting and unchanged deadline admission.

The old outside-Git concept packet accidentally described `.car.sha256` and
`.car.cid`; it remains historical evidence. Its successor must consume the
generated `metadata_operations` array from the actual Rust `Request` objects:
`.sha256` and `.cid`, matching the retained HTTP-200 receipts. No second path
catalog, replacement receipt, GO, lease or provider call is created by proposal
generation. All stage budgets are technical volume/runtime bounds, not prices.

Purpose: `ENGINEERING_VALIDATION_ONLY`, epoch 978. Acquire only the fixed modern
index and source declarations needed to propose a feasible payload slice. Do not
inspect token outcomes, select a successful token or infer Pump availability.

| Operation, in this order | Entity contract | Per-operation retry envelope |
|---|---|---|
| GET `/978/epoch-978-slot-ranges.raw` | 200; exactly 5,184,000 bytes | 3 attempts, 15,552,000 bytes |
| GET `/978/epoch-978.sha256` | 200; 1–4096 bytes | 3 attempts, 12,288 bytes |
| GET `/978/epoch-978.cid` | 200; 1–4096 bytes | 3 attempts, 12,288 bytes |
| HEAD `/978/epoch-978.car` | 200; positive Content-Length, zero entity | 3 attempts, zero entity bytes |

Host is internally fixed to `files.old-faithful.net:443`. This is four logical
operations, at most **12 attempts / 15,576,576 response_entity_bytes**. One
successful inventory charges 5,192,192 bytes because sidecars reserve their full
caps, even when shorter. Source-format arithmetic `432000 × 12` was confirmed by
the retained 5,184,000-byte index. The two sidecars measured 101 and 60 bytes;
HEAD declared a 709,264,399,796-byte CAR, with no strong ETag. These are observations
of that one run, not a future availability, range-support or throughput guarantee.
Its approved cost basis was documented free public OF1 access, not paid query
credits; byte/runtime caps are resource bounds, not billing measurements.

No old budget is increased:

| Shared aggregate cap | Value |
|---|---|
| Attempts / retry policy / concurrency | 16 / 2 retries per logical operation / 1 |
| Single / aggregate response entity allowance | 16,777,216 / 134,217,728 bytes |
| Retained run-tree disk / required available disk | 268,435,456 / 536,870,912 bytes |
| Memory hard cap | 536,870,912 bytes |
| Aggregate allocated runtime / attempt deadline | 1,800,000 / 30,000 ms |

**New proposed allocation within those caps:** metadata gets 600,000 ms (10
minutes), not a second 30-minute lease. Payload may receive at most the remaining
1,200,000 ms after separate review. Twelve 30-second attempt timeouts allocate 360
seconds; the remaining 240 seconds are local inspection/dispatch headroom.
These are deadline gates, not preemption of a blocked filesystem syscall: an
overrun cannot authorize another dispatch/publication. This is scheduling headroom, not a measured OF1
throughput claim. Restart preserves the original stage expiry.

After worst-case metadata, at most 4 attempts / 118,641,152 entity bytes remain.
After four successful first attempts, 12 attempts / 129,025,536 bytes remain;
unused metadata retry permission expires at payload admission, while every actual
reservation remains spent. Full two-retry payload admission requires `3Q` attempts
and `3S` bytes. The old 128-slot candidate cannot fit when all slots are nonempty.
No range reduction, cap increase or additional proof request occurs automatically.

Disk/RSS are measured in the new [local evidence](OF1_ACQUISITION_EVIDENCE.md),
including a full-size synthetic index, partial retry, restart and one tiny CAR
envelope. This supports the tested case only, not every 128 MiB payload or real
OF1 performance. The old PR104 8 KiB per-read storage illustration is preserved
as an old-implementation lower bound; the staged path coalesces 64 KiB segments.
All retained attempts, metadata, staging and final copies share the 256 MiB cap.
Before dispatch, the runner checks a conservative reservation disk allowance and
RSS/free-space guards; failure stops rather than claiming the slice must fit.
Physical wire/TLS/DNS bytes, billing and remote throughput remain unmeasured.

One retained [release-build measurement receipt](../../schemas/acquisition/of1/acquisition-resource-measurement.json)
binds the exact implementation-input digest, executable, fixture and Raw/receipt
hashes: **1,416 ms** end-to-end; **327 ms** full-index capture; maximum observed
process peak RSS **30,081,024 bytes**; final conservative disk charge
**11,288,576 bytes**. The six per-operation elapsed observations are 70–327 ms,
including local reservation/publication; they are not a remote latency estimate.
The debug observation was 74,526 ms overall and 5,615 ms for the index, showing why
build profile and host must accompany resource claims. Both observations include
a real child exit/restart and failed CID retry, not physical power-loss testing.

## Executable preparation, still offline

After review/merge, use the exact reviewed commit and existing pinned toolchain.
These preparation commands perform no provider request. Keep the reviewed dataset
location on WSL ext4 **outside Git**; do not place datasets under the checkout.
Run the read-only location preflight with the exact binary before preparing a new
plan. Proposal generation and initialization use that same validator again.

```bash
TOOLCHAIN_RUN="${HOME}/.local/share/solana-quant/run-with-toolchain"
"${TOOLCHAIN_RUN}" cargo +1.97.1 build --locked --offline --release \
  --manifest-path rust/of1-range-recorder/Cargo.toml --features network-of1 \
  --bin of1-acquire
git rev-parse HEAD
"${TOOLCHAIN_RUN}" rustc -Vv
"${TOOLCHAIN_RUN}" cargo -Vv
OF1_ACQUIRE_BIN="$(pwd)/rust/of1-range-recorder/target/release/of1-acquire"
sha256sum "${OF1_ACQUIRE_BIN}"
"${OF1_ACQUIRE_BIN}" dataset-preflight /absolute/path/to/dataset-run
findmnt -T /path/to/existing/dataset-parent
df -B1 /path/to/existing/dataset-parent
```

Record the exact code SHA and SHA-256 of the retained rustc/Cargo identity receipt.
If `CARGO_TARGET_DIR` is set, use the actual emitted binary location, not a guessed
different executable. Generate (and retain outside Git) the unapproved proposal:

```text
of1-acquire metadata-proposal ROOT CODE_SHA TOOLCHAIN_RECEIPT_SHA256
```

In the command signatures below, `of1-acquire` means the exact full path in
`OF1_ACQUIRE_BIN` (invoke `"${OF1_ACQUIRE_BIN}"`), not an installed/global command.
Use the same release binary throughout the approved run and all restarts.

Location admission resolves canonical paths and checks the root and its ancestors.
Only a demonstrably empty ordinary `.git` directory is ignored. Nonempty markers,
gitfiles (including linked worktrees), symlinks and inspection errors still reject
the location. No marker is removed or renamed, and preflight creates no directory,
lease, reservation or request. `OUTSIDE_GIT` is location evidence only, not approval,
free-space/toolchain verification or store/resume readiness. Initialization repeats
the check; preflight is not a guarantee against later filesystem changes.

The first metadata initialization stopped before store creation or network dispatch
because the previous marker-existence check rejected an empty home `.git` directory.
Preserve the original `initialization-failure.json` and its original plan/GO/lease;
do not rewrite that stop as acquisition or retry evidence. The accepted location
fix was followed by a fresh executable/plan and GO for the same dataset location,
four operations and caps; that metadata run succeeded. Its bytes, receipts and
old/new plan/GO/lease/stop evidence remain unchanged. The checksum correction and
later telemetry work change the binary: finish them before preparing the next
decision packet. Do not resume the old store with a rebuilt executable, rewrite
its manifest or reuse/reset an expired approval/deadline.

The JSON contains the complete aggregate plan, metadata allocation and
`approval_target_sha256`. Its approval flags remain false. Save the `aggregate`
object as `aggregate.json`. The eventual metadata lease has exactly:

```json
{
  "schema": "OF1_METADATA_LEASE_1",
  "authority": {
    "mode": "APPROVED",
    "approval_id": "EXPLICIT_OPERATOR_GO_ID",
    "operator": "APPROVED_OPERATOR",
    "approved_at_ms": 0,
    "not_after_ms": 0,
    "approved_plan_sha256": "EXACT_GENERATED_APPROVAL_TARGET_SHA256",
    "cost_confirmation": "NOT_CONFIRMED"
  },
  "budget": {
    "max_requests": 12,
    "max_response_entity_bytes_total": 15576576,
    "max_runtime_ms": 600000
  }
}
```

This displayed template is intentionally **invalid/non-executable**. Only a
separate explicit user GO may supply real approval times/hash/operator and
confirmed no-credit-spend status. No credential is required or recorded. Do not
change cost text merely to pass validation: verify current terms first. The
approved window must encompass the proposed stage deadline; it is capped at that
expiry even if initialization occurs late.

The displayed historical shape also omits the mandatory new-policy
`authority.clock_anchor`. For a new proposal, after GO only, use this exact
binary's `clock-sample` once for actual T0 UTC/boot/boot-ID, and bind
`initialize_by_boot_ms = T0.boot_ms + 600000` and
`expires_at_boot_ms = T0.boot_ms + 1200000`. Rust validates that mapping and
the policy; the anchor is never regenerated on restart. Do not fill it with
preparation time or convert the legacy lease to the new clock policy.

## Commands only after metadata GO

```text
of1-acquire metadata-init ROOT aggregate.json metadata-lease.json
of1-acquire capture-stage ROOT aggregate.json CURRENT_LEASE_SHA256
of1-acquire progress ROOT aggregate.json CURRENT_LEASE_SHA256
```

Initialization reports the exact current lease hash and original deadlines.
`capture-stage` makes at most one attempt per missing logical request and stops
on the first failure. Any retry is another explicit invocation under the same
lease/hash/deadlines and remaining retry budget. Do not delete/reinitialize a run
to reset charges. A complete publication is never downloaded again. Keep the
binary unchanged between proposal/init/restart; rebuilds are not resume identity.

Stop after four verified metadata publications. Retain all attempts, original
bytes/headers, receipts, source/index hashes and resource/terminal report. A
metadata success is not payload authority or complete B4 engineering validation.

## Separate payload gate

Offline `prepare-payload ROOT aggregate.json LEASE_SHA FIRST END` derives and
prints the exact range plan from accepted metadata. It does not grant authority.
The proposed interval must be explicitly reviewed; the existing 128-slot draft
is not an approved command argument. Unknown/missing modern index fails closed.

Use `payload-proposal ROOT aggregate.json LEASE_SHA prepared.json budget.json`
to hash the reviewed stage allocation, exact prepared ranges and aggregate.
Payload admission requires a separate `OF1_PAYLOAD_LEASE_1` receipt with that
approval target plus `prepared_payload_sha256` and `metadata_receipt_sha256`.
`payload-admit` rederives every range and checks shared retry/byte/runtime bounds
before storing the lease. `capture-stage` then captures only that inventory.
Finally `verify-payload` performs offline integrity checks and reports achieved
levels, not a research-ready dataset.

No header-prefix or root-proof acquisition is implemented as an implicit request.
Root-to-slot membership remains UNAVAILABLE; this accepted limitation is not a
fallback to a full CAR download. B5, protocol promotion, strategy and paper
execution require later deliveries. Engineering failure, insufficient data and
edge falsification remain distinct as defined in the [lease draft](B4_ENGINEERING_VALIDATION_LEASE_PLAN.md).
