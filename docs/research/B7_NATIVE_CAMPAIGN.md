# B7 — fixed native sample and campaign boundary

> **Document status: ACTIVE — bounded native campaign.** Current execution authority is the dated 2026-10-04 section below and the active handoff; older decisions retain their historical scope. #86 remains open / Unproven; Research Ready=false. No evaluation outcome release or B8.

## Phase-two preparation — 2026-10-06

The owner conditionally authorized the original ordinals 8–15, in order, after
phase one became `COMPLETE_SEALED`. `of1-acquire campaign-phase2-proposal
AGGREGATE_JSON` is read-only: it verifies the current campaign journal and all
eight closed phase-one collection manifests, retains only their operational
hashes and identities, and emits an exact phase-two approval target. The
separate `campaign-phase2-admit` repeats these checks before committing an
APPROVED phase grant. That grant neither dispatches a request nor replaces the
fresh, separate metadata, payload and processing authorities for each window.
The proposal is invalid after an intervening ledger or source change.

Only DEVELOPMENT ordinals 0–3 and 8–11 may later be read through the separate
manifest-pinned cohort route described in
[the DEVELOPMENT cohort contract](B7_DEVELOPMENT_COHORT.md). Evaluation
ordinals remain sealed. Phase-two execution and any resulting cohort report are
private operational evidence, not completion or research sufficiency claims.

## Initial offline evaluation preparation — 2026-09-28

The 2026-09-28 [accepted method and sealed evaluation route](B7_EVALUATION_SHIELD.md)
supersede the earlier implementation-time statement that no such route exists.
At that delivery, only offline implementation/fixtures were authorized. Acquisition,
real evaluation processing and final visibility require separate exact decisions.
Later dated owner decisions govern current execution; they do not grant visibility.

## Owner decisions, without rewriting the proposal

The owner accepted #142's **feasibility design**, bound to private
`governance/b7-research-sampling-proposal-20260924/report-reviewed/report.json`,
SHA256 `80d8f6fb0d10fa7c210fb4bd6e9fe4d1bdbb8b19539fa7fd021394d03f3bf415`.
The new decision records live in `governance/b7-native-campaign-20260924/` under
the approved OF1 root. The sealed proposal, its false approval fields and old
cost assessment remain publication-time evidence, not current execution authority.

A subsequent explicit owner decision authorizes **only DEVELOPMENT window 1**
`[422526144,422526160)`, after independent review and protected integration:
metadata ≤12 attempts / 15,576,576 reserved entity bytes / 600 seconds; then
payload ≤48 attempts / 99,642,069 reserved entity bytes / 1,200 seconds, only
when fresh metadata and all ranges match the frozen source identities. The
existing offline native processing of that window is also authorized. An
immutable plan must bind exact executables, code/toolchain, paths, ranges and
resource limits before execution. This decision is not authority for another
window, phase two, evaluation outcome inspection or a paid service.

## One unchanged design

`of1_range_recorder::b7` verifies the canonical #142 proposal and selection hashes:

- proposal `a65733f0315e03bcf936c8ec75be21f6b8b0499abe536a824232de3135a3fd3f`;
- selection `085d33c70ad504a9e14378629df7ec584c88826dc918f4eafb780bb44c824782`;
- campaign `b7-recurrence-v1-20260924`, epoch 978.

The sixteen original windows, seed, SHA256 ranking, two cohort roles, phase
assignment, 32-slot embargo, inspected exclusions, question and sufficiency
rule are unchanged. The table is frozen, not redrawn using source availability
or outcomes. `OF1_B7_WINDOW_SAMPLE_1` binds campaign root/id, accepted report,
selection, epoch, window bounds, ordinal, rank/hash, cohort and phase. Its
`selected_center` is explicitly the eight-slot boundary in this version.
The old fixed-pilot identity serializes exactly as before. An absent sample is
still engineering data; a CLI label cannot retrofit a sample onto an old run.

The initial native aggregate hash includes the full sample. Every request and
receipt binds that aggregate; the immutable reader preserves it in Raw bindings,
Bronze/Silver records, batch/execution receipts and physical manifests. Parquet
uses the same Rust validator through a local path dependency with transport
features disabled. It adds no registry version upgrade or alternative decoder.

## Durable campaign enforcement

The production campaign is fixed under OF1:
`campaigns/b7-recurrence-v1-20260924/`. Window runs use `runs/w00` … `runs/w15`;
processing uses `work/w00` … `work/w15`. There is no production root override.
Fixture authority uses separate temporary roots and cannot open production
state or dispatch the official connector.

| Boundary | Mechanical enforcement |
|---|---|
| 16 windows / 256 slots | Exact identity table; next ordinal only; duplicate/replacement paths denied; full original window required in each native collection |
| 960 attempts / 1,527,045,918 reserved entity bytes | Shared hash-chained journal; charge before native per-run reservation and before dispatch; stricter than absolute 2 GiB cap |
| Metadata / payload | Existing separately hash-bound leases; ≤12/48 attempts, ≤600/1,200 seconds; exact source fingerprint/index required for authentic payload |
| Phase two | Separate approval bound to phase-one journal/evidence; no ordinary window lease implies expansion; verified completed phase-one processing required |
| New campaign storage ≤32 GiB | Recursive allocated-or-rounded charge covers Raw, failed attempts, retained intermediate data, Parquet, reports, ledger and anchor; next write is reserved before publication |
| Processing | One fixed ≤4 GiB reservation and 900-second boot-clock deadline per window; exact plan/three-worker hashes; restart cannot renew either |
| Free space and resources | 20 GiB floor plus next write; native RSS cap; production requires existing scope limits CPU≤200%, MemoryHigh≤5 GiB, MemoryMax≤6 GiB, TasksMax≤256; existing per-worker 2 GiB address space, per-file and native buffer caps remain |
| Concurrency | One native campaign writer lock; one collection-driver lock; active processing blocks creation of another acquisition run; workers remain sequential |

Code/build caches, pre-existing immutable inputs and administrative decision
records are outside the campaign data tree. They are not represented as Raw or
processing storage. Every new acquired/derived data artifact goes inside the
fixed campaign tree; administrative receipts separately identify their paths.
No OS-wide disk quota, hostile-user protection or power-loss hardware guarantee
is claimed. The existing owned-directory/non-adversarial-kernel contract applies.

The native recorder guards all reservation/publication space checks. The native
batch decoder, Parquet materializer and collection sealer hold the campaign
guard through writes. `collection_run.py` obtains the native processing lease,
locks the driver, checks remaining time/space before each step, bounds worker
address space and log files, and keeps logs/checkpoints under the reservation.
Its report is generated by the native collection completion route. Native production
workers verify the hard 2 GiB address-space limit even when invoked directly.
Native inspection/publication checks the original deadline; Python computes its
remaining timeout from that same absolute boot-clock deadline after preparation. Generic
analytical query/export commands reject B7, including all-pending collections; they cannot become an unbudgeted
writer or expose reserved evaluation outcomes. The standalone whole-run decoder
requires the bounded collection route for B7.

## Crash and recovery meaning

A create-once anchor outside the campaign directory prevents missing state from
being treated as an unused budget. Atomic head replacement follows fsynced
immutable snapshots. Missing/corrupt journals, pending heads, extra snapshots,
changed roots/locks and unmatched native/campaign charges stop. There is no
automatic refund, pruning, replay of an already published request or lease reset.
A complete post-publication snapshot can resume; an ambiguous seam needs an
explicit repair decision. Existing Raw/receipt pair publication stays atomic.
Small fixtures exercise limits; real child-process exits exercise journal seams.
They do not claim authentic crash evidence or allocate 32 GiB to test a cap.

## Operation and private result

The native read-only `of1-acquire metadata-b7-proposal CODE_SHA TOOLCHAIN_SHA 0`
produces an unapproved binary-bound proposal. The four official descriptors are
GET `/978/epoch-978-slot-ranges.raw`, GET `/978/epoch-978.sha256`,
GET `/978/epoch-978.cid`, HEAD `/978/epoch-978.car`, exclusively
`files.old-faithful.net:443`. Payload requests are the sixteen exact ranged GETs
of that same `.car`, derived by the existing Rust planner and matched to the
pre-recorded index/ranges. Redirects, alternate hosts, unapproved stages and
source/range drift stop; failed attempts remain spent.

Existing `metadata-init`, `prepare-payload`, `payload-admit` and capture commands
retain their explicit lease checks. No proposal command initializes a campaign
or makes a request. `campaign-status` is read-only accounting. The batch
collector adds `campaign-processing-proposal`, `campaign-admit`,
`campaign-check` and `campaign-complete`; `collection_run.py --campaign-approval`
uses them. Resumption uses the original approval and fixed deadline.

Native completion emits `work/w00/collection.json`, `campaign-report.json`,
`index.html` and `campaign-report.COMPLETE`. The visible development report
links bounded per-slot diagnostics, separate Bronze/Silver totals, disposition
and transaction-status counts and source identity. The valid RESERVED_EVALUATION sample identity remains supported, but its
processing and analytical presentation are explicitly denied in this increment.
The ordinary detailed manifests/quality reports are not falsely presented as
sealed evaluation output. A separate reviewed processing/visibility gate is
required before those windows can be processed; no such gate is authorized here. No B8
labels/outcome interpretation or research sufficiency conclusion is produced.
Open via the existing private file-copy/SSH method; no service is deployed.

The independent review found and required fixes for pending-report admission,
late deadline checks, direct-worker process caps and evaluation outcome visibility.
The private dossier records the fixes, regressions and targeted recheck alongside
exact reviewed/head/merge SHAs, offline results,
mandatory CI and, only after those gates, the separately bound first-window
execution and actual consumption. Failure/incomplete status must remain visible.

## Archive access cost — documentation only

On 2026-09-24 the official [sourcing-data documentation](https://docs.old-faithful.net/running-old-faithful/sourcing-data)
states that archive access is free; the [OF1 source page](https://docs.old-faithful.net/running-old-faithful/sourcing-data/of1)
identifies Triton One and `files.old-faithful.net`. The owner independently
confirmed free archive access. **Unconfirmed archive cost is no longer a blocker.**
This does not price managed RPC/gRPC, grant subscriptions or waive VPS
storage/traffic costs. No billing API, account, token or datahost probe was used.

The retrieved `.md` representations are unversioned DOCUMENTATION_ONLY snapshots,
accessed `2026-09-24T13:36:05Z`, retained privately with URL/type/byte length:
`sourcing-data.md` SHA256
`2c2fbe1ba78c690cd0d2f186fa849655cac9110decde07b70f0f9b74a96834e3`;
`of1.md` SHA256
`61e6734e4fce60c347dea3887893c059d050ae73b4fad5f5265bcb669a21826d`.
Cost documentation is not an acquisition lease.

## First-campaign CLI initialization — 2026-09-27

PR #143 merged as `b93d4be04a2014d1de97830e6b0a4a2b1b126188` with green
technical main checks. The first actual `metadata-init` stopped before native
campaign creation or any request: ordinary location admission required the
`runs` parent that native campaign initialization itself creates. The stopped
binary, lease, immutable plan and zero-consumption evidence remain sealed in
`governance/b7-first-development-window-20260924/`.

The owner authorized one bounded repair. Read-only admission recognizes only
the exact first B7 run under its validated, canonical, outside-Git campaign
root. Native creation still validates the full plan/lease/clock before creating
its own anchor, journal, runs and work directories. No admission function creates
parents. Existing/incomplete campaign state and create-once anchors cannot become
fresh budgets; ordinary dataset location rules remain unchanged. The native
guard repeats the sample/location check before any campaign mutation.

The offline regression re-executes the test binary and exercises the actual
`metadata-init` command dispatcher, JSON files and native store/guard. Only a
`cfg(test)` authority seam permits the existing Fixture authority at a temporary
root; production builds remain Approved-only with no feature/environment/CLI
root override. This is offline initialization evidence, not authentic acquisition
or a test of live transport. Historical location and receipt/hash tests remain.

After protected repair integration and technical main verification, the existing
first-window permission permits a new immutable repaired-binary/source/range
plan and fresh lease. Old deadlines are never edited or reused. Matching fresh
metadata is required before payload; any new substantive blocker stops this task.
New evidence belongs to `governance/b7-metadata-init-20260927/`. No other window,
evaluation inspection or B8 is authorized; #86 stays open and Unproven.


## Bounded offline continuation — 2026-09-27

The first authentic DEVELOPMENT window is downloaded, not yet a completed
research dataset. #145's evidence in `governance/b7-ci-diagnostics-20260927/`
preserves the failed decoder operation and twelve verified slots
`[422526144,422526156)`: 15,359 packages, 2,163 ERROR transactions, 39 Silver
facts. Checkpoint SHA256:
`9c6f0b47744405dbf0041abcce769d47cfd45f7681d97325c36b2b4541760e41`.
Slot 422526156 hit `BRONZE_AGGREGATE_LIMIT`: 50,325,641 +121,844 record bytes
would exceed 50,331,648. This is cumulative resident output, not an individual
record, Raw-integrity or Silver-admission failure.

The owner separately authorizes only offline completion of slots
`[422526156,422526160)` after protected integration. No new acquisition, other
window, evaluation inspection or B8 is authorized. Old leases, failed output,
reservations and manifests are immutable evidence.

### Part publication

`OF1_ATOMIC_SLOT_PARTS_128_V1` uses the same Rust receipt verifier, CAR reader,
transaction decoder and native Parquet projector. Each part selects at most
128 whole packages in canonical transaction-index order. Records and Silver
facts retain their original chain positions, source identities and exact
integers. A package never straddles parts. Individual 16-MiB records, 48-MiB
resident record accumulation, metadata/file caps and the existing worker
address-space limit are unchanged. If one part still exceeds a cap, stop;
there is no adaptive limit increase or fallback decoder.

A part manifest says `PART_ACCOUNTED`, never whole-slot completeness. Native
slot publication requires the exact full inventory of contiguous parts,
matching Raw, receipt, producer, count, physical-hash and logical-hash evidence.
Missing, duplicate, corrupt, incomplete or unexpected parts cannot publish a
complete slot. Missing/unsupported/quarantined packages remain evidence;
failed transactions produce no successful Silver facts. Source gaps and
unavailable protocol/account-state evidence retain distinct meanings.

### Explicit continuation authority and scheduler

`of1-bronze-collection continuation-proposal OLD_PLAN CHECKPOINT DECODER_SHA
PROJECTOR_SHA` verifies the preserved prefix and emits a canonical
`OF1_B7_CONTINUATION_DECISION_1` plus approval target. This read-only proposal
binds the original source/receipt/sample/plan, checkpoint, current ledger and
original processing approval/stage; only worker identities and the named
continuation profile differ. Production approval uses the existing validated
clock/authority contract and must be newly issued for that target.

`continuation-admit DECISION AUTHORITY` rechecks the native prefix and ledger
under the campaign lock, appends one separate ≤900-second processing stage,
then creates `work/w00/continuation-1`. It never changes the old lease or grants
new attempts, bytes or storage. A duplicate, stale, mismatched, corrupt or
ambiguous admission fails closed. An admitted-but-interrupted initialization
is retained, not manually repaired or granted another lease.

`research/columnar-query/continuation_run.py DECISION --authority AUTHORITY
--decoder BINARY --projector BINARY --verifier BINARY` schedules only native
inventory parts. For a clean part-boundary restart omit `--authority`; the
same absolute deadline and runner/binary identity apply. `--max-new-parts N`
is a controlled pause, not a new budget. The original driver lock excludes
simultaneous schedulers. The current production authorization is one offline
lease; it does not guarantee time remains for a later restart.

The native campaign guard checks every worker admission and publication. All
old work, failures, new parts, logs, Parquet and reports share the existing
4-GiB work reservation and 32-GiB campaign ceiling. Python schedules workers
and records operational clocks; it does not decode or authorize protocol
meaning. Existing outer CPU 200%, RAM/high 5/6GiB, process 256, one-worker,
free-space/headroom stops and socket-denial requirements remain mandatory.

The final native `OF1_CONTINUED_BATCH_COLLECTION_1` carries the original
checkpoint unchanged and each continued slot's new producer/part evidence.
It uses the existing `OF1_ORDERED_RECORD_CHAIN_1` over all canonical rows;
physical files are separately bound. The static HTML/JSON report stays inside
the campaign work tree. Real operation times, RSS and disk accounting are
separate operational evidence, never historical features. Generic analytical
readers are not extended to B7 or RESERVED_EVALUATION by this increment.

Offline evidence includes a Rust fixture spanning two parts, deterministic
whole/parts equality, failed/unknown packages, native CLI/Parquet continuation
with twelve preserved slots, part-boundary interruption, corrupt publication,
and durable admission crash seams. These fixtures are not authentic counts.
Execution and final authentic-window results are recorded separately in
`governance/b7-bounded-slot-continuation-20260927/`. #86 remains Unproven and
Research Ready remains false regardless of complete engineering accounting.


## Ordinary atomic-part processing and second window — 2026-09-27

The first window completed through #146 with 21,068 atomic packages, 2,965
failures and 57 admitted facts. The twelve original slots and their 39 facts
retain their producer identity and byte hashes. Native completion, not a prose
claim, is required before the second execution. See the sealed
`governance/b7-bounded-slot-continuation-20260927/RESULTAAT.md`.

The owner separately authorizes ordinal 1, DEVELOPMENT `[422552336,422552352)`,
within the same fixed campaign. Metadata ≤12 attempts /15,576,576 reserved
entity bytes /600s; payload ≤48 /100,473,774 /1200s; offline processing ≤900s
under the existing 4-GiB work reservation. Only `files.old-faithful.net:443`;
exact binaries, plans, ranges and fresh leases are frozen first. Payload follows
only matching fresh metadata. No other window, phase two or evaluation outcomes.

The optional immutable plan field `slot_part_profile` accepts only
`OF1_ATOMIC_SLOT_PARTS_128_V1`, one original B7 DEVELOPMENT sample and one slot
per batch. `collection_run.py` dispatches that registered profile directly to
`parts-inventory`, bounded native decoder/projector parts, `parts-seal-slot`
and `parts-complete`. There is no preliminary whole-slot attempt. The native
batch worker refuses whole-slot execution of a part-profile plan. Plans without
this field retain their historical serialization and behavior, including pilot,
engineering and explicit first-window continuation plans.

The slot verifier is shared with the delivered continuation: exact part-directory
inventory, full transaction boundaries, contiguous indices, Raw/receipt/sample
binding, decoder identity, per-part physical/logical checks, failed-parent
exclusion and every disposition must pass before a slot is ACCOUNTED. A partial
or corrupted slot cannot publish a complete manifest. The regular collection
`OF1_PARTED_BATCH_COLLECTION_1` binds each verified slot manifest by path/hash;
slot manifests bind all part files/layers and their full original manifests.
The same ordered logical hash is independently recomputed in an offline fixture.
This does not add an analytical export route or change Silver admission.

All existing individual record/JSONL/report/file/RAM caps remain. The campaign
Guard accounts the entire 4-GiB work reservation while incomplete; temporary
worker output and bounded stdout/stderr are inside it. Drivers use the original
absolute deadline, one registered driver lock, exact retained plan/binary/runner
identity and checked free space. Only fully verified existing parts are reused;
partial failed files remain preserved. New ordinary processing is not a
continuation grant and cannot renew or refund the first window's lease/charges.

The read-only storage assessment records apparent, allocated and native-charge
bytes by category. The first campaign uses 2,339,524,608 native-charge bytes;
second-window admission conservatively adds 4 GiB work +256 MiB acquisition
output +1 MiB administrative headroom, reaching 6,903,975,936 bytes versus
32 GiB, with >20 GiB remaining host free space. The weighted fixed-selection
forecast is 30,044,649,674 bytes. One window cannot establish a universal
expansion factor: +25% more variable output would exceed the cap. These are
planning estimates; unchanged native checks govern actual admission/writes.
The prior transient peak was not continuously measured; bounded temporary
reservations are not presented as measured disk peaks or extra permanent data.
No prior output, retained segments, failed logs or sealed evidence is removed.

Evidence and eventual second-window result:
`governance/b7-second-development-window-20260927/` under the approved OF1 root.
Execution remains conditional on successful protected integration/main checks
and a fresh native storage/previous-completion check. #86 is open / Unproven;
Research Ready=false. The full-selection forecast grants no remaining acquisition.


## Exact four-window read-only capability — 2026-09-28

The later explicit owner decision permits the [DEVELOPMENT cohort view](B7_DEVELOPMENT_COHORT.md)
of ordinals 0–3 only. The completion dossier is
`governance/b7-phase1-development-completion-20260928/`. This is a separate
read-only analysis permission, not a processing lease renewal or campaign budget
reset. `of1-bronze-collection development-cohort` admits only the compiled fixed
selection and four exact collection hashes, with no path/cohort override.
It reads original manifest-listed output, preserves retained/continued producer
identities, and publishes only after all four collections pass existing gates.
Generic Python dataset/collection readers still refuse all B7; evaluation
records, reports and aggregates remain outside the new capability.
Analysis artifacts stay outside the campaign and Git in a bounded governance
folder (report ≤256 MiB, temporary output ≤1 GiB, native export ≤16 MiB,
900-second execution; existing host headroom and CPU/RAM limits). They do not
modify or refund campaign charges. No acquisition or evaluation permission follows.

## Ordinal-4 payload continuation — 2026-10-01

`OF1_B7_PAYLOAD_CONTINUATION_1` is one explicit exception for the already
acquired ordinal-4 prefix, not a general lease-renewal API. Native commands:

```
of1-acquire payload-continuation-proposal RUN AGGREGATE OLD_PAYLOAD_LEASE_HASH
of1-acquire payload-continuation-admit RUN AGGREGATE OLD_PAYLOAD_LEASE_HASH APPROVAL
of1-acquire capture-stage RUN AGGREGATE NEW_CONTINUATION_LEASE_HASH
```

The proposal is outcome-free and grants no authority. Admission recomputes the
binding while holding both run and campaign locks. It binds the fixed B7 sample,
original aggregate/lease/producer, current ledger, prepared source/ranges, hashes
of all retained run files, and the new acquisition executable. Only the existing
13-payload + 4-metadata + one charged-429 prefix is eligible; DEVELOPMENT windows
0–3 must already be complete. The production source pins and three ranges match
the accepted private proposal. Fixture roots remain separate and cannot authorize
a production connector.

Admission appends an intent, a campaign journal entry and an atomic continuation
record. An interrupted admission at an ambiguous boundary fails closed. Neither
old authority nor prior charges are rewritten. Each new reservation is charged
before transport. A failed or unpublished new attempt is terminal; restarting
cannot return it or move on to a different range. After complete publication,
restart skips that range. Pacing is anchored in persisted native boot-clock
initialization/success times: 60 seconds before the first request, then 60 seconds
after each successful request. The 600-second execution grant is separate from
the expired original stage, subject to unchanged aggregate/payload byte, request,
disk and worker limits. It cannot be extended by a restart.

The writer, campaign ledger and immutable source reader all verify this record.
Existing receipts keep their original producer bindings; new receipts bind the
new continuation lease/executable. Downstream source identity includes the
continuation record hash. Completeness is twenty distinct valid planned receipts,
not `unpublished_attempts == 0`: the historical 429 remains charged evidence.
Sealed evaluation preparation/processing still require their separate authority;
generic readers, exports and final visibility remain denied.

`scripts/of1-approved-authority.py` only serializes a separately granted approval
from one caller-supplied native clock sample. The unchanged native validator is
authoritative: initialization window 600,000 ms; approval validity 1,200,000 ms;
stage runtime remains separately bounded. Offline tests run this actual generator
against APPROVED authority validation for metadata, payload, processing and the
continuation. Synthetic CLI tests cover historical expiry, refused/mismatched
consent, reservation/publication interruptions, pacing, terminal failure and
preservation of all historical hashes. The existing sealed end-to-end fixture
now completes ordinal 4 through this public continuation route before processing.
These fixtures expose no authentic evaluation outcomes.

## Metadata continuation and bounded transport retry decision — 2026-10-04

The first ordinal-5 metadata attempt retained 80 segments (5,184,000 bytes) but
published no receipt. That file evidence does not repair the historical failure
or refund its charge. The original batch decision and failed execution remain
sealed; the supplemental owner decision is in
`governance/b7-metadata-continuation-20261004/` outside Git.

The native `metadata-continuation-proposal RUN AGGREGATE OLD_METADATA_LEASE_HASH`
and `metadata-continuation-admit RUN AGGREGATE OLD_METADATA_LEASE_HASH APPROVAL`
commands allow one separate metadata grant per incomplete phase-one run of the
fixed campaign. A proposal grants no permission. Admission rechecks the current
ledger, exact sample/source pins, original plan/lease/producer, retained-file
inventory and new executable. It subtracts historical attempts/entity charges
from the original metadata ceilings and grants at most 600,000 additional ms.
Old leases/deadlines are never rewritten. An ambiguous intent/journal publication,
duplicate admission, wrong software/source or conflicting retained bytes stops.
New metadata must pass ordinary transport and atomic receipt publication; there
is no hand promotion of segments. Writer, campaign guard and immutable source
reader preserve old/new producer and continuation binding through later payload.

The metadata performance repair keeps both pre-read and pre-write space
admission. Within the already admitted bounded segment write, Raw bytes and
segment receipt are written together using the same create-new/fsync/rename
operations without two nested, redundant campaign-tree scans. Final verification,
publication audit, memory/file caps and the 30-second attempt timeout are unchanged.
The synthetic TLS fixture reports network setup/read, resource admission, durable
segment writes, verification and publication separately. Historical provider
latency cannot be inferred from this local fixture.

`capture-stage` still makes no automatic retry. On failure it adds a bounded
request sequence and typed operational retry classification. Only recognizable
timeout/connection-reset failures are candidates; that classification itself
never authorizes dispatch. The separately reviewed batch helper may invoke the
ordinary native route again only under the owner's explicit amendment, a valid
unchanged lease, remaining budgets and the recorded 5/15-second local waits.
Native reservations count every attempt, including the original failure, with
at most two retries per request. HTTP 429, malformed/truncated responses, source,
authority, storage/resource and other protocol errors stop. No provider rate-limit
claim follows from those local waits.

For the authorized resumed batch 5→6→7, every next window still requires native
completion of the preceding sixteen slots and closed collection manifest.
Evaluation processing/release authorities remain separate; all outcomes stay
sealed. #86 remains open/Unproven and Research Ready=false.
