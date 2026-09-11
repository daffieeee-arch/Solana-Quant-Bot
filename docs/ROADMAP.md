# Solana Quant Platform V2 roadmap

> **Document status: ACTIVE.** [GitHub Project #4](https://github.com/users/daffieeee-arch/projects/4) is the central delivery roadmap. [`PROJECT_V2_REBASE.md`](PROJECT_V2_REBASE.md) and [`../roadmap/project-v2-content-migration-ledger.json`](../roadmap/project-v2-content-migration-ledger.json) preserve the completed G0 migration contract and evidence. B4/[#83](https://github.com/daffieeee-arch/Solana-Quant-Bot/issues/83) is the concrete current delivery. PR #94 remains `Fixture` evidence. A separately approved run published four metadata objects and one 45,051-byte CAR range; the [read-only verification repair](research/OF1_RECORDED_CAR_VERIFICATION.md) preserves the original failure and checks that same range without acquisition. A later separately approved 605,402-byte range at slot 422496001 supports the [bounded offline Raw → Bronze result](research/B5_AUTHENTIC_RAW_BRONZE.md). The later three-slot run is now processed offline: [3,137 atomic Bronze decodes and five Pump-referencing packages](research/B5_MULTISLOT_PUMP_SEARCH.md), with one structural event-layout match but no admitted Silver candidate. No new acquisition or delivery promotion follows. No further run is authorized. The draft `ENGINEERING_VALIDATION_ONLY` lease in [`research/B4_ENGINEERING_VALIDATION_LEASE_PLAN.md`](research/B4_ENGINEERING_VALIDATION_LEASE_PLAN.md) remains `approved: false`, distinct from actual expired approvals retained outside Git.

## Program outcome

Build authentic, reproducible Solana/Pump research evidence and determine whether a statistically and economically defensible edge exists. `FALSIFIED` and `INSUFFICIENT_SAMPLE` are valid outcomes. Profitability is never an assumption or a milestone label.

These are research outcomes, not synonyms for an acquisition failure. Engineering errors/budget stops remain engineering outcomes; undecoded or insufficient evidence cannot falsify an edge. The [B4 lease contract](research/B4_ENGINEERING_VALIDATION_LEASE_PLAN.md) separates metadata/index GO from payload GO, stage budgets and evidence gates. No live run or B4 completion follows from the existing loopback fixtures.

No phase authorizes signing, real orders, transaction submission or live funds.

## Ordering principles

1. Protocol and source truth precede derived features.
2. Authentic bounded data precedes strategy claims.
3. Research Observatory precedes Professional Trading Workstation.
4. Point-in-time evidence precedes prospective shadow; prospective shadow precedes the new paper engine.
5. The frozen legacy paper runtime is not repaired feature-by-feature.
6. User-visible or research-measurable results appear throughout delivery.
7. The number of PRs is not a target; bounded evidence is.

The implementation rule is one walking skeleton: one official source, one acquisition plan, one small authentic range, one required Pump variant, one Bronze path, one Silver path, one token lifecycle and one visible result. Do not generalize for hypothetical cases before a second proven use case requires it.

## Bounded result sequence

| Delivery | Scope | Required observable outcome | Explicit non-goals |
|---|---|---|---|
| PR 1 | V2 source of truth and roadmap rebase plan | accepted docs, status map and complete #27–#63 migration ledger | no manual V2 Project/issue migration, cleanup, provider calls or product code; ordinary PR-item sync may run |
| G0 issue #70 + bounded sync/config PRs | **Complete:** teach trusted `main` the complete V2 Project contract | tested exact-state reconciliation, V2 fields/Evidence, Project text, views and lifecycle-aware retention | no product work or provider calls |
| G0 content migration | **Complete:** controlled Project #4 V2 rebase | successors, V2 metadata, clean views, retention and individual supersession verified without deleting history | no bulk-close or delivery claim |
| PR 2A / B2A #81 | **Complete / Operationally Verified:** obsolete platform removal and legacy safety quarantine | retired TrueNAS/Phase 8/Hermes paths removed after manifest-backed invariant salvage; reachable legacy is bounded/fail-closed | no data pipeline, new legacy features, VPS replacement or provider call |
| PR 3 / B3 #82 | **Complete / Fixture:** Pump protocol truth walking skeleton | generated [protocol evidence matrix](research/PUMP_PROTOCOL_EVIDENCE_MATRIX_V2.md) for one official pinned source and one selected bounded candidate | no universal registry/framework claim; observed compatibility, historical activation, independent protocol authority, economic identity, Research Ready status and profitability remain unproven |
| PR 4 / B4 #83 | **In progress / ACTIVE NOW / Unproven:** preserved empty 45,051-byte range plus later 605,402-byte transaction-bearing range | existing monitor and separate verification: later range has 846 nodes/845 links and 725 Transaction envelopes; [B4 acceptance matrix](research/B5_AUTHENTIC_RAW_BRONZE.md#b4--issue-83-acceptance-matrix--no-automatic-closeout) | no further acquisition, B4 completion, root membership, full epoch, backend override or research claim |
| PR 5 / B5 #84 | **Backlog / NEXT; bounded offline preparation separately authorized:** authentic Raw → Bronze → Silver | [725 retained decodes](research/B5_AUTHENTIC_RAW_BRONZE.md) plus [3,137 multi-slot decodes and bounded Pump layout search](research/B5_MULTISLOT_PUMP_SEARCH.md); Silver/lifecycle/Parquet writer still outstanding | no B5 completion, Gold, strategy result or interactive workstation |
| PR 6 | interactive Research Observatory MVP | browser-visible Ingestion/Data Quality and Token Lifecycle Replay for authentic data | no fake panels, paper controls or full workbench |
| PR 7 | bounded scale-up and Cohort Explorer | cohort distributions plus explicit data-sufficiency result | no arbitrary full-history expansion or model fishing |
| PR 8 | PIT Gold v0 and first baseline | falsifiable baseline on untouched evidence, or valid `INSUFFICIENT_SAMPLE`/falsification | no deep learning, auto-promotion or executable-profit claim without execution evidence |

After at most three or four engineering PRs without a new user-visible or research-measurable outcome, the next PR must produce one.

The content migration created E0–E7 as #72/#74–#80 and B2A–B8 as #81–#87. PR #90, the separate #63 closeout and G0/#70 completion are verified. B2A/#81 is completed and Operationally Verified. B3/#82 is `Done` / `SUPERSEDED` / `Fixture`; its evidence is structural and fixture-compatible only. B4/#83 is `In Progress` and the one concrete `ACTIVE NOW` delivery; PR #94 recorded B4A as a `Fixture` partial result of B4 and does not complete it. The OF1 memo and offline remainder contract do not complete it either and do not authorize a lease. B5/#84 is `Backlog` and the one concrete `NEXT` item. B6–B8 remain `LATER`. Programs and epics may retain broader routing, but the Now and Next views each expose exactly one concrete delivery head. The completed bounded metadata/payload run does not authorize another range or acquisition resume. The separate offline B5 development GO authorizes only the bounded Raw → Bronze component; no delivery promotion follows.

The last pre-B2A main commit is archived by annotated tag `v1-paper-platform-final`: tag object `de5b3850e0527afe8271c54abfdb95098d55e395`, peeled commit `f870621f5df76b935ce828fa9205fb9ff7504f67`. B2A's exact removal, test-classification and retained-invariant inventory is [`../roadmap/b2a-invariant-salvage-manifest.json`](../roadmap/b2a-invariant-salvage-manifest.json).

## V2 program phases

The Project rebase adds a separate `V2 Phase`; it does not destroy the historical `Phase` field.

| Phase | Objective | Exit evidence |
|---|---|---|
| 0 Cutover & Cleanup | establish V2 truth and remove obsolete reachability safely | fresh sessions reconstruct V2; invariant-salvaged cleanup is reviewable |
| 1 Pump Protocol Truth | versioned official protocol source and bounded decoder truth | protocol evidence matrix for one selected bounded candidate; authentic bytes remain required for observed/activation evidence |
| 2 Authentic Acquisition | direct official OF1, bounded and resumable | authentic verified bytes/blocks with receipts, coverage and progress |
| 3 Bronze & Silver | Rust-owned/authorized lossless canonical facts and immutable manifests | deterministic Raw/Bronze/Silver plus static report; physical Parquet writer resolved explicitly in PR 5 |
| 4 Research Observatory | make authentic evidence visible | interactive data-quality and lifecycle replay |
| 5 Scale & Data Sufficiency | expand only through preregistered sampling | Cohort Explorer and sufficient/insufficient verdict |
| 6 Gold & Edge Validation | PIT features/labels and simple baselines | walk-forward evidence, falsification or insufficient sample |
| 7 Prospective Shadow | Triton-only no-order measurement | quote availability, finality, latency, capacity and parity evidence |
| 8 New Rust Paper Engine | replace frozen fill/lifecycle/accounting semantics | restart-safe engine with quote/no-fill and reconciliation evidence |
| 9 Professional Workstation | full linked/dockable trader workflow | proven contracts visualized without evidence inflation |
| 10 VPS & Gated Live | later generic Linux operations and isolated execution | separate explicit approval and safety review |

## Historical slice gates

Every acquisition plan declares its class before payload inspection:

- `ENGINEERING_VALIDATION_ONLY` may intentionally contain known Pump activity and may validate acquisition/decode/Bronze/Silver/Observatory. It is forever excluded from strategy and edge claims.
- `RESEARCH_SAMPLING` is deterministically selected independent of outcomes, with a preregistered sampling/expansion plan. It is only a research candidate after all evidence gates pass.

The range `[422506000, 422506128)` is provisional until origin and selection rationale are documented. Slot count, byte/request/disk/runtime caps, epoch/range and later windows/folds/holdout values are separately approved run-plan or methodology parameters, not roadmap constants.

## Research progression

Start with deterministic rules and descriptive statistics, then logistic regression, gradient-boosted trees, survival models, learning-to-rank and evidence-supported actor/graph features. No deep learning initially, autonomous LLM trade signals or automatic model promotion.

Initial hypotheses include early-launch ranking, flow acceleration/imbalance, participant growth, trade-size distribution, early concentration, creator/funder history where reconstructible, curve progression, momentum continuation/exhaustion, graduation, rug/exitability survival, post-graduation behavior and capacity versus signal quality.

Every result records censoring, coverage, dataset/model manifest, train/validation/test boundaries, costs/evidence class and uncertainty. Historical event prices remain non-executable observations unless an independent execution-evidence contract proves a later opportunity. The following historical transaction is not a default fill.

## Causality gate

All instructions, CPIs, events, logs and metadata from a historical transaction become available as one atomic package. A decision cannot see a partial package or execute against a fact from that same already-completed transaction.

`acquired_at` and `processed_at` are real wall-clock operational provenance for receipt and local processing; neither may enter historical features, labels, splits or decisions. Gold separately defines canonical chain-order `effective_at`, reconstructed atomic-package `observed_at` under `observation_model_id`, first permitted `actionable_at`, actually recorded `decision_at`, and independently evidenced later `execution_opportunity_at`. Bind `latency_model_id` when latency modeling changes actionability/execution. The execution boundary is nullable/`UNAVAILABLE` without evidence, and the next historical transaction is not automatically a fill.

## Language and data-ownership gate

Rust owns logical/canonical Raw/Bronze/Silver semantics, decoding, ordering, exact integers, evidence, coverage, quarantine and manifest identity and produces or authorizes Bronze/Silver records. Python begins at approved Silver and owns Gold, features, labels, cohorts/splits, statistics, backtests and experiments; it contains no Pump wire decode or alternative Silver logic. PR 5 decides the physical Bronze/Silver Parquet writer. A Python-only writer must be generated, lossless and prove schema/logical-hash parity.

## Long-term epics

The V2 Project migration creates separate epics for:

- versioned Pump truth and authentic datasets;
- Research Observatory;
- edge discovery/data sufficiency/falsification;
- prospective Triton-only shadow;
- new Rust paper engine;
- Professional Trading Workstation;
- VPS and gated live execution.

Requirements in #27–#34 remain valuable but route to the later new engine or their earlier data-evidence component. Frontend #35–#47 splits authentic Observatory needs from the later workstation. Exact mapping is in [`PROJECT_V2_REBASE.md`](PROJECT_V2_REBASE.md).

## Evidence semantics

The Project-facing Evidence options are `Not Applicable`, `Unproven`, `Fixture`, `Operationally Verified`, `Engineering Validation`, `Research Candidate`, `Research Ready`, `Shadow`, `Paper Proven` and `Live Proven`. The dataset slice class `ENGINEERING_VALIDATION_ONLY` remains a stricter immutable tag and is not renamed by the Project label `Engineering Validation`.

- `Not Applicable`: coordination-only program/epic container; never inherited from children.
- `Unproven`: no accepted evidence.
- `Fixture`: deterministic synthetic/fixture evidence only.
- `Operationally Verified`: an operational/governance control passed its named CI and end-to-end reconciliation evidence.
- `Engineering Validation`: authentic engineering mechanics proven, but permanently excluded from edge claims when the slice is `ENGINEERING_VALIDATION_ONLY`.
- `Research Candidate`: outcome-independent authentic evidence awaiting all PIT/research gates.
- `Research Ready`: accepted PIT research dataset/methodology.
- `Shadow`, `Paper Proven`, `Live Proven`: only their named prospective gates.

Unknown, stale, missing or unavailable evidence is never converted to zero, healthy, safe or successful.
