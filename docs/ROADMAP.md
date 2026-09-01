# Solana Quant Platform V2 roadmap

> **Document status: ACTIVE.** [GitHub Project #4](https://github.com/users/daffieeee-arch/projects/4) is the central delivery roadmap. [`PROJECT_V2_REBASE.md`](PROJECT_V2_REBASE.md) is the accepted controlled V2 migration ledger; G0 implementation is tracked by issue #70.

## Program outcome

Build authentic, reproducible Solana/Pump research evidence and determine whether a statistically and economically defensible edge exists. `FALSIFIED` and `INSUFFICIENT_SAMPLE` are valid outcomes. Profitability is never an assumption or a milestone label.

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
| G0 issue #70 + bounded sync/config PR | teach trusted `main` the complete V2 Project contract | tested V2 Phase/Disposition/Evidence, Project text, views and lifecycle-aware retention support | no successors, V2 issue metadata or live migration from the branch; merge requires explicit approval of its automatic reconciliation |
| Post-merge migration transaction | controlled Project #4 V2 rebase | successors, V2 metadata, clean views, retention and individual supersession verified without deleting history | no bulk-close or delivery claim |
| PR 2A | obsolete platform removal and legacy safety quarantine | retired TrueNAS/Phase 8/Hermes paths removed after invariant salvage; reachable legacy is bounded/fail-closed | no data pipeline or new legacy features |
| PR 3 | Pump protocol truth walking skeleton | protocol evidence matrix for one official pinned source and one needed bounded variant | no universal registry/framework claim and no activation claim without real bytes |
| PR 4 | bounded Old Faithful acquisition | live terminal/TUI progress, verified receipts, deterministic resume and explicit coverage/gaps | no full epoch, no silent backend override and no research claim |
| PR 5 | authentic Raw → Bronze → Silver | static HTML/JSON data-quality and one token-lifecycle report with provenance/quarantine | no Gold, strategy result or interactive workstation |
| PR 6 | interactive Research Observatory MVP | browser-visible Ingestion/Data Quality and Token Lifecycle Replay for authentic data | no fake panels, paper controls or full workbench |
| PR 7 | bounded scale-up and Cohort Explorer | cohort distributions plus explicit data-sufficiency result | no arbitrary full-history expansion or model fishing |
| PR 8 | PIT Gold v0 and first baseline | falsifiable baseline on untouched evidence, or valid `INSUFFICIENT_SAMPLE`/falsification | no deep learning, auto-promotion or executable-profit claim without execution evidence |

After at most three or four engineering PRs without a new user-visible or research-measurable outcome, the next PR must produce one.

During the Project rebase, its dedicated governance implementation issue is the single concrete `ACTIVE NOW` delivery. After the migration is fully verified, B2A becomes `ACTIVE NOW`, B3 alone is `NEXT`, and B4–B8 remain `LATER` until their direct predecessor is accepted. Programs and epics may retain broader routing, but the Now view must expose one concrete delivery head.

## V2 program phases

The Project rebase adds a separate `V2 Phase`; it does not destroy the historical `Phase` field.

| Phase | Objective | Exit evidence |
|---|---|---|
| 0 Cutover & Cleanup | establish V2 truth and remove obsolete reachability safely | fresh sessions reconstruct V2; invariant-salvaged cleanup is reviewable |
| 1 Pump Protocol Truth | versioned official protocol source and bounded decoder truth | protocol evidence matrix for the needed real variant |
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
