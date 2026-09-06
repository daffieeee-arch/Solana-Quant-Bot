# B4 Engineering-Validation Lease Plan (draft)

> **Document status: ACTIVE — UNAPPROVED, NOT EXECUTABLE.**
> `approved: false`, `networkEnabled: false`, `readyToRun: false`.
> This draft authorizes no metadata, index, CAR, Triton or provider request.

The [JSON twin](B4_ENGINEERING_VALIDATION_LEASE_PLAN.json) is a reviewable
planning document, **not** the Rust runner's input schema. Its revised schema
does not change runtime authority. Neither flipping flags nor merging a PR can
create a lease. Cost/availability confirmation remains `NOT_CONFIRMED`.

## Current capability and next result

PRs #101–#103 establish the [planner](OF1_RUST_PLANNER.md),
[durable store](OF1_DURABLE_RAW_STORE.md) and
[loopback transport](OF1_OFFLINE_TRANSPORT.md) at **Fixture** evidence.
The [executed report](OF1_TRANSPORT_EVIDENCE.md) is synthetic local HTTP,
not a completed live downloader. Default builds have no transport, and
`validate_plan()` still accepts only offline fixture authority. The new
[staged acquisition path](OF1_STAGED_ACQUISITION.md) adds a separate typed lease
contract, optional official HTTPS and local CAR/slot checks; its
[executed evidence](OF1_ACQUISITION_EVIDENCE.md) remains Fixture only. The
[concrete metadata proposal](OF1_METADATA_RUN_PROPOSAL.md) is the next review
surface, not an approved run.

B4/#83 remains open, In Progress / ACTIVE NOW / Unproven; B5/#84 remains
Backlog / NEXT / Unproven. The next authentic result is one small immutable
**engineering Raw package**, with receipts, integrity levels, missing evidence
and budget outcome visible. It is not Bronze/Silver, a Pump lifecycle, or a
research result. No generic framework, server or infrastructure phase is needed.

## Three different questions

| Question | Permitted result | What it does not imply |
|---|---|---|
| Did the approved acquisition/integrity mechanics work? | `ENGINEERING_PASS`, `ENGINEERING_FAILURE`, `ABORTED_BUDGET`, `QUARANTINED`, with exact stage/reason | A download error, integrity mismatch or exhausted budget says nothing about market edge |
| Is there enough admissible data for the stated task? | Currently `UNAVAILABLE_NOT_DECODED_IN_B4`; after valid offline decode, no required Pump pair means `INSUFFICIENT_DATA_FOR_PUMP_MECHANICS`; later research may report `INSUFFICIENT_SAMPLE` | Missing or undecoded observations are not zero events and not falsification |
| Did a preregistered economic hypothesis fail? | `NOT_EVALUATED_ENGINEERING_SLICE` for this slice; later `FALSIFIED` only on valid, sufficient, outcome-independent PIT research evidence with appropriate costs, execution evidence and uncertainty | An engineering slice can never establish or falsify a trading edge |

Incomplete transaction packages cannot enter a strategy decision.
Absent executable economics makes an economic claim inadmissible; it does not
show that no edge exists. Unsupported codecs/variants or conflicting bytes
quarantine; they are not negative market observations.
`ENGINEERING_PASS` must name the accepted scope (metadata capture, payload
integrity, etc.), not imply completion of every downstream phase.

## Candidate range — not a run selection

Retain the original unapproved candidate: epoch 978,
`[422496000, 422496128)`, because `978 × 432000 = 422496000` under
the pinned OF1 convention. No payload, token list or outcome selected it.
128 is a proposal, **not** the smallest useful window or evidence that it fits
the budgets. Documented epoch-level coverage/CU properties do not prove this
window's availability, Pump activity, activation or data sufficiency.

The older `[422506000, 422506128)` remains a historical provisional example,
not this plan. After metadata inspection, a smaller range may be proposed based
on size/coverage mechanics, never silently substituted or extended to hunt
successful tokens. Any change requires a newly reviewed, immutable plan.
Both this slice and any activity-seeded replacement remain
`ENGINEERING_VALIDATION_ONLY`; later outcome-independent
`RESEARCH_SAMPLING` requires its own preregistered selection/expansion plan.

## Two separate acquisition approvals

Both stages use the internally constructed `files.old-faithful.net:443`.
No caller URL, endpoint environment variable, proxy, mirror, redirect, public
RPC, legacy-index fallback, S3 or backend override is allowed. Documentation
hosts are `DOCUMENTATION_ONLY`; **every** request below is instead
`ACQUISITION_LEASED`, including HEAD and small sidecars.

### M — metadata/index GO (not granted)

The smallest proposed inventory is four logical operations:

| Method | Fixed epoch-978 path | Proposed entity contract |
|---|---|---|
| GET | `/978/epoch-978-slot-ranges.raw` | Exactly 5,184,000 bytes under the pinned modern index format |
| GET | `/978/epoch-978.sha256` | At most 4,096 bytes; unmeasured proposed cap; source-declared whole-CAR hash only |
| GET | `/978/epoch-978.cid` | At most 4,096 bytes; unmeasured proposed cap; source-declared root only |
| HEAD | `/978/epoch-978.car` | Zero response-entity bytes; bounded headers carrying object length/validators |

Index/sidecar GET may accept an exact bounded **200 full metadata object**;
this is not permission for a CAR 200. HEAD spends one attempt/time, and its
Content-Length describes the object, not transferred entity bytes. Missing or
incompatible HEAD/index/sidecar fails closed; no GET probe or legacy fallback.
Slots inventory, recap, GSFA and legacy indexes are **not** implicit requests.
A full epoch **index** is small metadata, not a full epoch CAR.

A metadata lease must approve methods, paths, exact/capped sizes, all retry
allowances, header limits, disk/memory/time allocation and source-format/code/
toolchain identity before dispatch. The authentic index hash cannot exist
before first acquisition: approve its expected format/path/size, preserve the
received bytes and receipt atomically, compute its hash, then validate offline.
Do not synthesize a pre-existing hash or bypass the current fixture validator.

Required output: original index/sidecar bytes, bounded HEAD receipt, immutable
attempt ledger and metadata manifest with request/response identities,
status, range (if any), bounded headers, timestamps, hashes, object length,
validators or explicit absence, and approval identity. **Stop here.**
No CAR entity, header prefix or proof-node bytes are allowed under M.

### P — payload GO (not granted)

After accepted metadata receipts, derive offline:

- actual selected index records, index-reported absences, exact CAR length;
- deterministic byte requests and retry envelope, without slot coalescing;
- metadata/source/validator fingerprints and the measured feasibility report;
- any separately needed CAR-header or proof-node ranges and their budgets.

A separate approved payload plan binds those receipts/hashes, the exact range
list, code/executable/toolchain identity, evidence limitations and remaining
aggregate budget. No null/unknown required identity is an executable default.
Missing/incompatible index data stops, rather than trying another source.
A changed source validator or conflicting retained overlap stops as specified
in [the transport contract](OF1_OFFLINE_TRANSPORT.md).

Payload requests require exact **206**, start/end/total/length, bounded framing,
no implicit retries or decompression, and reservation before network dispatch.
Preserve exact entity bytes before CAR/domain interpretation. A failed or
partial attempt remains charged and retained. A completed request is not
redownloaded to recover a missing receipt.

Separate GO does not duplicate the draft's aggregate budget. Metadata attempts,
charged bytes and retained disk (even in another directory) carry into the
payload feasibility check. Each stage needs an explicitly allocated runtime
and original absolute wall/boot deadline; allocations must fit the aggregate
proposal. Payload GO may establish its own approved deadline, but cannot renew
the metadata lease or refund its history. Restart never grants a new deadline.
No stage allocation is approved. The concrete proposal allocates 10 minutes to
metadata within the unchanged 30-minute aggregate; payload needs its own GO.

## Budget evidence — retained proposals, not feasibility approval

The original numbers are retained, not raised. They are aggregate unallocated
proposals, not one allowance per stage. The exact byte metric is
**`response_entity_bytes`**. Full response allowances are charged before
dispatch, even for interrupted attempts; verified published bytes are separate.
Unreceipted actual received totals remain unavailable.

| Meter | Original proposal / warning | Evidence and limitation |
|---|---|---|
| Attempts | 16 / 13 | Every attempt includes metadata/HEAD/retries; cannot cover 128 nonempty slots |
| Retries | 2 per logical request | Up to 3 attempts, never 2 total; global caps may stop earlier |
| Concurrency | 1 | Serial fixture path; no parallel acquisition is needed |
| Single entity | 16 MiB / 12 MiB | Proposed payload cap, not measured slot size |
| Total entity allowance | 128 MiB / 100 MiB | Only eight full-size 16 MiB attempts, before metadata |
| Disk high-water | 256 MiB / 200 MiB | Insufficient for the illustrative 128 MiB successful stream below |
| Free disk prerequisite | 512 MiB | Enforced at staged-store admission/read boundaries; no real acquisition measured |
| Memory | 512 MiB / 400 MiB | Staged path bounds allocations and samples RSS; measured local fixture is not authentic throughput evidence |
| Runtime | 30 minutes / 24 minutes | Proposed aggregate stage allocation, not measured throughput; no renewal on restart |
| Attempt timeout | 30 seconds | Persisted deadlines and full-size local TLS fixture measured; official DNS/TLS/remote performance remain unmeasured |

Draft warnings are not separate implemented guarantees; the staged path enforces
hard free-space/RSS and other resource caps. Headers, framing probes, TCP/TLS overhead and physical wire bytes
are not included in the entity metric; header/read/deadline limits must bound
them separately. Physical wire usage and provider cost remain unmeasured.

### Checkable arithmetic

The [source receipt](../../schemas/acquisition/of1/source-receipt.json) and
`SLOTS_PER_EPOCH`/`RECORD_BYTES` prove the **format expectation**:
`432000 × 12 = 5,184,000` index bytes. They do not prove that the authentic
epoch-978 object exists or has that size.

- One index with two retries reserves at most **15,552,000 entity bytes**.
- Proposed M inventory: `5,184,000 + 2 × 4,096 = 5,192,192` entity
  allowance per complete inventory. With two retries for every operation:
  **12 attempts / 15,576,576 bytes**. The sidecar bounds are proposals.
- Against the original aggregate proposal, that worst-case M reservation
  leaves **4 attempts / 118,641,152 entity bytes**, not a fresh 16/128 MiB.
- For nonzero selected slot lengths `L[i]` and payload chunk cap `C`,
  `Q = sum(ceil(L[i] / C))`, `S = sum(L[i])`.
  Full two-retry accommodation needs `3Q` attempts and `3S` charged bytes.
  Add separately enumerated CAR-header/proof ranges and retained metadata
  charges; require the combined envelopes to fit before payload approval.
- 128 nonempty slots require **at least 128 initial attempts / 384 with two
  retries**, because the planner does not coalesce slots. Neither actual
  nonempty count nor Q/S is known. Thus the candidate is not ready to run.

The **PR104 baseline fixture store** retains a Raw chunk, receipt and directory per read, then a final
Raw copy. At full 8 KiB reads with minimum 4 KiB receipt/directory charges,
128 MiB successfully published needs **at least 384 MiB**, before index,
run/attempt metadata, retries or staging. This is an illustrative **lower
bound**, not a safe 3× upper bound: short reads and failed attempts amplify
storage further. This old illustration is preserved, not applied as the new
staged store's measured factor: it coalesces 64 KiB segments. The cap stops
execution; it does not guarantee completion.

Still **UNAVAILABLE**: authentic object length/index hash, selected lengths/
Q/S, sidecar sizes, validator behavior, header compatibility, real fragmentation,
authentic disk high-water, RSS, fsync/verification throughput, DNS/TLS duration and cost.
The new local report measures a full-size synthetic index, fragmentation, restart
and a small CAR envelope; its results do not establish official-source behavior.
If safe resource bounds do not fit, stop for a smaller reviewed plan or a
specific measured implementation fix—not an automatic budget increase.

## Implementation acceptance boundaries

The following PR104 requirements are now implemented in the
[staged path](OF1_STAGED_ACQUISITION.md) and exercised locally, **not** against OF1.
The table preserves their scope; no requirement is satisfied by an authentic run
yet. Root-proof acquisition and draft warning notifications remain outside the
minimal executable path. Only hard resource stops are implemented.

| Boundary | Required bounded work / acceptance gate |
|---|---|
| Live admission | Separate metadata/payload lease validation tied to explicit approval, code/toolchain/executable and immutable receipts. Current fixture-only schema must not be weakened or activated by flipping booleans |
| Official HTTPS | Minimal fixed-host Rust TLS path; certificate/hostname verification; default-disabled production capability; reviewed locked dependencies/features/licenses/build scripts; no proxy/redirect/backend/autoretry/decompression. Original deadline covers DNS/connect/TLS/headers/entity reads |
| Metadata bootstrap | Durable bounded small-object GET200 and headers-only HEAD, preserving bytes before parse; index/source manifest publication without circular prerequisite of an already-known index hash. Reuse existing reservation/publication invariants, not the loopback API as a live client |
| Live budget enforcement | Include metadata plus payload charges, retained copies, explicit free-space and bounded memory policy; draft warning notifications are deferred, hard stops are implemented. Exercise large/short-read fixtures and time spent in hashing/fsync; test live-adapter paths only against sealed local fixtures until GO |
| CAR framing and node integrity | Bounded CAR header/section/uvarint/CID parsing, exact exhaustion and cross-request section assembly from already captured bytes. Pin supported codecs/multihashes, recompute node digests and reject mismatch/truncation/unsupported forms. Merely parsing a CID is not verification |
| Selected slot envelope | Minimal archival Block.slot and intra-slot link/closure checks against the planned index slots; missing/out-of-range/duplicate or unresolved links cannot become complete coverage. No Pump decode, Bronze or Silver in this acquisition work |
| Visible receipt | Show stage, attempts, reserved/published entity bytes, deadline, range completeness, local hashes, verification level and reasons. Block/transaction/Pump counts stay `UNAVAILABLE_NOT_DECODED_IN_B4` until an owning decoder actually establishes them |

Use the existing isolated Rust crate and durable store seams. No full
Jetstreamer runtime, RocksDB, ClickHouse, generic transport framework or new
infrastructure stage is needed. The [locked dependency review](../../rust/of1-range-recorder/dependency-review.json)
records the selected minimal TLS/resource graph; CAR parsing adds no dependency.

## Integrity ladder and root-membership limitation

| Evidence | What it proves | Current state |
|---|---|---|
| Local SHA-256 of received bytes | local artifact integrity | Fixture only |
| Recomputed complete node multihash equals CID | content/CID agreement for that node | Fixture-tested; no authentic observation |
| Block.slot and complete selected intra-slot links | archival slot-envelope consistency | Fixture-tested; no authentic observation |
| Captured CAR-header root equals sidecar root | agreement between source declarations | Offline primitive fixture-tested; no header acquisition in minimal runner; not inclusion proof |
| Verified epoch → subset → block links from captured nodes | root-to-slot inclusion in that archive | `UNAVAILABLE` until all required proof nodes are obtained and checked |

The modern index contains offsets/lengths, not authenticated root links.
Pinned upstream [node_reader.rs](https://github.com/anza-xyz/jetstreamer/blob/cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24/jetstreamer-firehose/src/node_reader.rs)
constructs a CID from a supplied digest; using it alone would not recompute
that digest. The pinned [epoch](https://github.com/anza-xyz/jetstreamer/blob/cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24/jetstreamer-firehose/src/epoch.rs)
and [subset](https://github.com/anza-xyz/jetstreamer/blob/cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24/jetstreamer-firehose/src/subset.rs)
types identify the missing linkage; none of those nodes has been acquired here.

Do not search the full CAR or fetch legacy indexes to manufacture inclusion.
Additional proof-node ranges, if needed, require identification, budget and
explicit approval. Until then report membership unavailable, retain Raw as a
bounded engineering candidate, and do not claim a fully root-verified dataset.
A partial capture never recomputes the whole-CAR SHA-256. Even valid archive
CIDs do not independently authenticate consensus or execution logs; official
[reproducibility guidance](https://docs.old-faithful.net/usage/validation/reproducibility.md)
warns that different ledger/node versions can produce different logs.

## Shortest executable path and GO gates

1. **Review the implemented bounded fixed-host acquisition path** in the
   existing crate: separate metadata admission/publication, HTTPS adapter,
   payload admission and offline CAR/CID/slot checks above. Keep production
   capability disabled by default; prove it using local fixtures, including
   realistic-size/fragmentation budget stops. No live request is part of that PR.
2. **Metadata GO:** approve the exact M inventory, allocations, operator/time,
   current cost/availability and code/toolchain hashes. Execute only M,
   publish original metadata/receipts and stop.
3. **Offline feasibility → payload GO:** enumerate requests from the captured
   index. The current 128-slot/16-attempt candidate may not fit; propose one
   smaller explicitly approved window if necessary, without outcome-based
   searching. Bind M receipts and all remaining budgets; never renew by restart.
4. **One bounded payload run:** display progress and retain Raw before parsing.
   Verify achieved CAR/CID/slot integrity offline; publish a precise success,
   partial, budget-stop or quarantine receipt. Unknown proof levels remain
   unknown; missing Pump observations cannot be reported as zero before decode.
5. **Review the actual result:** only accepted authentic mechanics may advance
   evidence to Engineering Validation. B4 completion remains a separate
   acceptance decision against issue #83; a partial Raw package is not automatic
   B4 completion or B5 admission. B5 subsequently owns authentic Bronze/Silver
   and the first Pump lifecycle. No strategy inference comes from this slice.

Every GO binds an immutable plan hash and stage, actual operator/approval
timestamp, source/code/toolchain identities, cost confirmation and hard stops.
The five old approval fields alone were insufficient; all implementation and
feasibility gates must also pass. No approval, missing identity, exhausted
budget, source drift or ambiguous publication means **stop and preserve evidence**.
No automatic expansion, cleanup, fallback, reacquisition or receipt repair.

## Source and history notes

Official documentation was checked on **2026-09-06**, documentation traffic
only. [OF1 files](https://docs.old-faithful.net/references/of1-files.md) names
`epoch-{epoch}.cid` and `epoch-{epoch}.sha256`, not `.car.cid` or
`.car.sha256`. Live existence, sizes and validators are unobserved.
The modern slot-range path/format is bound to the existing
[pinned index source](https://github.com/anza-xyz/jetstreamer/blob/cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24/jetstreamer-firehose/src/index.rs);
the old `references/of1-indexes.md` documentation link returned Page Not Found
and is not a fallback specification.

This is a correction to an active **unapproved draft**, not a rewrite of
historical run evidence. Earlier fixture reports, source receipts, B3 matrices,
salvage manifests and migration ledgers remain byte-identical. Docs/test success
does not promote them. No provider call, historical download, dependency change,
wallet/signing action, Bronze/Silver output or edge claim is authorized.
