# OF1 staged Raw acquisition

> **Document status: ACTIVE. Evidence: Fixture.** This implementation PR is not a
> metadata or payload lease. B4/#83 remains open / In Progress / ACTIVE NOW /
> Unproven; B5 does not start here. A separately approved four-operation metadata
> run completed on 2026-09-06. A later separately approved retained CAR range is
> covered by the [offline verification repair](OF1_RECORDED_CAR_VERIFICATION.md);
> no further acquisition is authorized. Historical run descriptions below remain unchanged.

## Observed checksum compatibility (offline correction)

The preserved run `8a350ea0c149f9c49e0615a9460ed3695eec64af21a778b0f8d7786adbae336f`
published the index, SHA sidecar, CID sidecar and CAR HEAD: four HTTP 200 results,
four attempts, zero retries, **5,184,161 received entity bytes** versus
**5,192,192 reserved entity bytes**. These are authentic metadata observations,
not a decoded coin dataset, payload-integrity proof or completed B4. The full
result remains outside Git in
`/home/dmesdary/solana-quant-data/run-plans/of1-e978-metadata-184e32eb-02/metadata-run-result.json`
(SHA-256 `8c77b3f6d7a1daec92835284bdc81ba4e18cb0a0166abcec5135252da5c81b21`).

The exact [101-byte observed checksum](../../schemas/acquisition/of1/epoch-978-observed.sha256)
and its [provenance](../../schemas/acquisition/of1/epoch-978-observed.provenance.json)
are the regression fixture. Besides the existing bare digest and exact basename,
the parser accepts precisely the observed source notation
`/tank/solana/car/{epoch}/epoch-{epoch}.car`, with the existing optional single
`*` filename marker. Both epoch occurrences must match. This is an opaque text
annotation: never opened, canonicalized, used as a local destination or turned
into a URL. It does not restore a TrueNAS target. Other path forms still reject;
Raw bytes and receipt identities are never normalized or rewritten. The declared
CAR checksum is not verified by downloading its sidecar.

| Offline check | Observed result / limitation |
|---|---|
| Original parser with exact fixture | `Corrupt`, reproduced before the fix |
| Corrected parser and fixture planning | Same derived requests/source fingerprint as basename notation; distinct metadata-receipt identity preserved |
| Read-only derivation from all four original publications | First epoch slot `[422496000, 422496001)` maps to CAR range `[59, 45110)`, **45,051 bytes**; no request dispatched |
| Original evidence audit | All 190 run files hash-identical before/after; original aggregate/executable/lease unchanged; no store resume |
| Integrity and research | Whole-CAR hash not verified; root-to-slot membership `UNAVAILABLE`; domain counts `UNAVAILABLE_NOT_DECODED_IN_B4`; no activation/edge claim |

The single-slot derivation is a compatibility check, **not a chosen or approved
payload run plan**. It calls the pure metadata planner, not `AcquisitionStore::resume`
with a different binary. The old executable and expired lease remain immutable.
Finish the separately scoped monitor/telemetry work before preparing a final
binary and a new, separately approved metadata/payload decision packet; do not
rewrite old manifests to make a new binary fit. No new run is authorized here.

## One path, two approvals

The existing `rust/of1-range-recorder` crate now separates metadata admission,
fixed-host HTTPS, receipt-bound payload planning and offline integrity checks.
The old fixture planner/store/loopback reports remain unchanged historical test
evidence; their `OFFLINE_FIXTURE` schema is not promoted into network authority.
The new staged store reuses the existing clock, lock, hashing, durable
reservation and paired-publication principles without importing old checkpoints.

Default features remain empty. `network-of1` alone exposes the official adapter;
`tls-fixture` exposes a numeric-loopback adapter with ephemeral in-memory test
trust, and rejects approved/live authority. The official adapter rejects fixture
authority before DNS. Building either feature is not approval to execute it.

The only production authority is a reviewed `Authority::Approved` receipt with
operator, approval identity, approval/expiry times, exact proposed-plan hash and
`CONFIRMED_NO_CREDIT_SPEND`. These are operator attestation fields, not an
automatic authorization service. This PR supplies no approved receipt. New cost
terms require review, not a different endpoint or implicit spending permission.

1. Run offline `dataset-preflight ROOT` before plan preparation. The shared
   canonical location validator also gates `metadata-proposal ROOT ...` and
   `metadata-init`: only a demonstrably empty ordinary `.git` directory is ignored;
   real checkout/worktree markers remain excluded. This is read-only location
   admission, not a lease or a run-directory creation.
   Create one aggregate plan and separate metadata lease. Its internally fixed
   inventory is index GET, SHA sidecar GET, CID sidecar GET, CAR HEAD.
2. Reserve each attempt durably before DNS/TCP/TLS. Capture metadata entity bytes
   and original bounded headers without domain parsing. Stop after metadata.
3. Offline, verify the four stored objects/receipts and exact modern index size,
   then derive ranges. Approve a separate payload lease bound to these receipts,
   selected slots, range list and remaining aggregate budget.
4. Admission independently rederives the plan from stored metadata. It cannot
   accept a caller-invented range or promote a fixture authority. Metadata is
   permanently closed once payload admission succeeds.
5. Capture exact approved ranges; stop. Independently invoke offline CAR/CID and
   selected-slot verification on published Raw. Nothing produces Bronze/Silver.

## Concrete contracts and state

The executable JSON contracts are Rust serde types in
[`durable/acquisition.rs`](../../rust/of1-range-recorder/src/durable/acquisition.rs),
all with unknown-field rejection. They are not the older draft lease JSON.

| Record | Required identity / purpose |
|---|---|
| `OF1_ACQUISITION_AGGREGATE_1` | epoch, pinned format source, code SHA, toolchain fingerprint, actual executable SHA-256, shared request/entity/disk/memory/runtime budgets |
| `OF1_METADATA_LEASE_1` | authority plus metadata request/entity/runtime allocation; approval hash includes aggregate and exact fixed inventory |
| `PreparedPayload` | four-receipt hash, index hash, CAR length/strong ETag or explicit absence, declared CAR hash/root CID, slot interval, exact derived requests and index-reported absences |
| `OF1_PAYLOAD_LEASE_1` | distinct authority/allocation, prepared-plan hash and metadata-receipt hash; no reset of earlier charges |
| `OF1_ACQUISITION_RECEIPT_1` | aggregate/lease/run/attempt identity, method/path/range, original bounded response headers plus hash, status/length/validator, entity length/hash, acquired wall/boot time, retry comparison, evidence and unavailable domain counts |

The run directory has an immutable manifest, durable `attempts/`, retained
`pending/` evidence and paired `published/<sequence>/raw.bin + receipt.json`.
A later `payload.json` is published through a retained intent and hard link.
An unmatched/torn intent fails closed rather than replaying a mutation.

`INITIALIZED → RESERVED → HEADERS_RETAINED → SEGMENTS_RETAINED → PUBLISHED`
is per attempt. Failure stops the current process; an explicit later invocation
may reserve another attempt only after resume validation. There is no automatic
retry, refund, recovery download or reconstruction of a missing receipt.
Publication writes/syncs Raw and receipt, syncs the candidate directory, then
renames the pair and syncs both parents. Pre-publication partials are never complete
Raw. A crash after publication is verified, not re-requested. Process-crash tests
do not claim physical power-loss proof.

TLS fragments are coalesced into at most 64 KiB durable segments. A short final
fragment is retained if the original deadline still allows it. Bytes lost in
userspace at a crash remain unmeasured; the full pre-reserved entity allowance
stays charged. Retained streams and publication copies count toward disk usage.
Single writer locking, immutable files and complete audits at reserve/resume/
publication boundaries protect exact pairs, content hashes and ordering.

Every stage records wall-clock and boot-clock deadlines and boot identity.
Restart cannot renew them. A reboot, rollback, code/executable or toolchain-plan
identity change rejects resume. Declared code/toolchain hashes are approval-bound
provenance, not independently attested builds; the executable hash is measured.
Expired metadata may be inspected and used for separately approved payload
admission, but never dispatched again. Metadata and payload runtime allocations
sum to at most the original aggregate allocation; human review time is not a new
metadata lease. Payload allowances use actual permanently charged metadata
attempts, not a second full budget.

## HTTPS and resources

The host/path are constructed internally: `files.old-faithful.net:443` and the
four pinned epoch paths. No URL, proxy, credential, S3/backend, redirect,
compression, caller host or environment endpoint is accepted. TLS uses normal
certificate/hostname validation and the reviewed static Mozilla roots. One fixed
`/usr/bin/getent ahosts files.old-faithful.net` child resolves the host, with a
bounded output and original deadline; timeout kills and reaps it. One address is
selected, one connection attempted. No DNS/address retry creates hidden HTTP
attempts. DNS packets/TLS overhead are not response entity bytes.

The same original deadline covers resolver, connect, handshake, headers, reads,
hashing/fsync and publication. Headers are limited to 16 KiB. Metadata GET requires
bounded 200 plus Content-Length; HEAD requires 200, positive object length and no
entity. Deadline checks surround blocking hashing/fsync: they do not interrupt a
stalled kernel syscall or prove hard real-time storage. RSS is a sampled guard,
not an instantaneous OS-enforced ceiling. Payload requires exact
206/Content-Range/length. Transfer/content encoding,
redirects, duplicate singleton headers, wrong status/range and trailing response
bytes are rejected. One EOF probe is outside entity accounting and bounded by the
same deadline. Bare TLS EOF is accepted only after an exactly exhausted HTTP
entity; it never repairs a short body. Request bytes are deterministic and retained
by identity; received header bytes are retained verbatim, never logged wholesale.

Changed CAR length/strong validator is `SOURCE_DRIFT`; same-identity retry bytes
disagreeing with retained overlap are `CONFLICTING_BYTES`. Both quarantine and
prevent later dispatch. Missing strong validators remain explicit; digest/length
checks do not invent an immutable HTTP version. An ordinary interrupted stream
can retry only with unchanged retained identity/overlap and spent budgets.

Resource admission checks response allowances, conservative run-tree disk charge,
available filesystem bytes (`statvfs`), current RSS (`/proc`) and the original
deadline. Allocation bounds precede reads. Progress exposes current/peak RSS,
available disk, retained disk charge, attempts, charged/published entity bytes,
stage, lease and deadlines. These measurements are not physical network billing.
The original CLI JSON progress remains request-level (and on terminal failure).
The separately enabled [B4 monitor](B4_DOWNLOAD_MONITOR.md) observes actual
intra-request entity reads through bounded, nonblocking local telemetry; it does
not alter reservations, durable publication or acquisition authority.
Draft warning thresholds are not separate implemented stop guarantees; hard caps
are authoritative. No full epoch CAR or completion guarantee is implied by a cap.

## Offline integrity, not historical protocol promotion

[`car.rs`](../../rust/of1-range-recorder/src/car.rs) supports only the source-pinned
CAR/uvarint/CIDv1 DAG-CBOR SHA2-256 envelope. It recomputes each complete node hash,
requires exact bounded parsing and one terminal archival Block for the selected
slot, and checks transaction/reward slot consistency and the captured intra-slot
link closure. Truncation, unsupported codecs, wrong kinds/slots, duplicate CIDs,
missing links, cycles and unrelated nodes fail closed. Cross-request assembly
uses only already-published contiguous ranges. There is no missing-node fetch.

The [source receipt](../../schemas/acquisition/of1/car-source-receipt.json) binds
the exact upstream source paths/hashes. No Solana runtime, Pump parser, DataFrame
checksum/decompression or execution validation is hidden in this envelope check.
Domain block/transaction/Pump metrics stay `UNAVAILABLE_NOT_DECODED_IN_B4`; the
report counts verified archival envelopes/nodes, not domain events.

CAR-header root comparison exists as an offline primitive. The minimal runner
does not request a header prefix or proof nodes. The CID sidecar is a declaration,
not root-to-slot inclusion. **Root-to-slot membership remains UNAVAILABLE** and
the whole-CAR SHA-256 is not recomputed from a partial capture. The CLI currently
bounds verification to 4096 nodes / 16384 links plus plan byte limits; an
unsupported larger envelope stops for review instead of widening the limit.

`ENGINEERING_FAILURE`, budget stop and quarantine are acquisition outcomes.
Data sufficiency is not evaluated before owning decode; economic edge is
`NOT_EVALUATED_ENGINEERING_SLICE`. Neither a failed download nor a valid engineering
Raw fixture can establish insufficient market edge, activation or Research Ready.

## Visible verification and next GO

The [executed acquisition report](OF1_ACQUISITION_EVIDENCE.md) combines realistic
index-size TLS simulation, durable attempts, process restart, distinct payload
admission and CAR/slot checks. Logical JSON/Markdown regenerate identically.
Machine/runtime-dependent measurements are separate receipts, not golden bytes.
The [metadata run proposal](OF1_METADATA_RUN_PROPOSAL.md) supplies the exact next
operator commands and unchanged aggregate caps. Metadata GO and payload GO remain
separate; no authentic observation is claimed by this implementation.

The [locked dependency review](../../rust/of1-range-recorder/dependency-review.json)
separates default, official and fixture feature graphs, licenses and build scripts.
Rustls/ring/static roots provide TLS; rcgen is fixture-only; rustix supplies safe
filesystem resource measurement. No system package, server, Solana RPC, full
Jetstreamer runtime, RocksDB, OpenSSL or new infrastructure is required.
