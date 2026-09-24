# B7 — fixed native sample and campaign boundary

> **Document status: ACTIVE — bounded implementation and conditional first-window execution.** 2026-09-24. #86 remains open / Unproven; Research Ready=false. No B8 labels, evaluation outcomes or Cohort Explorer expansion.

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
Its report is generated by the native collection completion route. Generic
analytical query/export commands reject B7; they cannot become an unbudgeted
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
and transaction-status counts and source identity. Reserved-evaluation reports
omit analytical totals; their immutable native records retain the role. No B8
labels/outcome interpretation or research sufficiency conclusion is produced.
Open via the existing private file-copy/SSH method; no service is deployed.

The private dossier records exact reviewed/head/merge SHAs, offline results,
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
