# PROJECT_V2_REBASE.md — controlled Project #4 migration plan

> **Document status: ACTIVE migration plan.** G0 implementation is in progress under governance issue [#70](https://github.com/daffieeee-arch/solana-paper-scanner/issues/70). This document does not itself mutate [Project #4](https://github.com/users/daffieeee-arch/projects/4), any issue, or any pull request.

## Authority and migration rules

Project #4 remains the central delivery roadmap. The repository keeps this reviewable ledger so a Project mutation cannot silently reinterpret history.

The migration preserves GitHub history. “Non-destructive” does not require obsolete Project items or fully superseded anchor issues to remain active forever:

- preserve every original issue body, acceptance criterion and comment, plus the pre-migration state/close reason in the hashed export and migration evidence;
- create successors before adding migration notes to old issues;
- do not use `Closes`, `Fixes` or equivalent links during migration;
- treat V2 disposition as delivery routing, never as proof of completion;
- keep issue #56 `Done` and issue #62 open; the accepted plan expected issue #63 to remain open through initial migration and change individually to `not planned / superseded` only after verified E0 cutover;
- retain the existing `Phase` field and its values as historical metadata;
- add a separate `V2 Phase` field rather than repurposing `Phase`;
- do not bulk-close any issue. A later verified closeout pass may close fully covered anchors individually as `not planned / superseded`;
- export all current Project-linked repository issue/PR bodies and lifecycle state plus complete Project fields/views before mutation; retain a separate exact #27–#63 migration snapshot.

PR #69 is merged and verified. The actual Project rebase remains blocked while the bounded G0 sync/config PR is under review. Because an approved merge triggers push-to-main Roadmap Sync, the explicit merge GO must cover that live reconciliation and may occur only after all pre-merge decisions and stop conditions are resolved. PR 2A cannot start before the verified rebase completes.

## G0 preflight checkpoint

The read-only pre-G0 export and governance-issue creation are complete. [`operations/PROJECT_V2_G0_PREFLIGHT.md`](operations/PROJECT_V2_G0_PREFLIGHT.md) is the public receipt; raw issue/PR bodies and Project JSON remain outside Git under the `LOCAL_PRIVATE_WSL_EXT4` storage class.

| Fact | Verified value |
|---|---|
| PR #69 squash-merge | `05c3d885943c2319d6317b43546daca4b5918074` |
| Snapshot ID | `project4-pre-v2-g0-20260901T193839Z-05c3d885943c` |
| Snapshot root SHA-256 | `98a97b7bfb74d194898d0aa1d65c0a9caf8c4015f09ae6a93c76fd8289ee3585` |
| Exact issues #27–#63 SHA-256 | `a4181fff5009797e720f82f92f26df61f2973c532e1f25dc1a14e312b8730769` |
| Pre-V2 merged-PR candidates SHA-256 | `0211a16af1a18006bfa1005f12e4b57c9373508d655884f59a2a3f18656d9e67` |
| Captured Project items | 69 total: 37 linked issues, 32 linked PRs, 0 archived |
| Pre-V2 PR archive candidates | 30 linked merged PR items through PR #68 |
| G0 owner | issue #70, open and `In Progress` under the current schema |

The export found issue #63 already `CLOSED` / `COMPLETED` at `2026-09-01T19:17:25Z`, linked to PR #69, rather than open as the accepted migration plan required. No G0 preflight action changed that state. This is an unresolved stop condition: before any live V2 migration, governance must explicitly approve whether to preserve the present state as a documented exception or perform a separate state correction with its resulting reconciliation effects. The sync/config PR must not make that choice implicitly.

## V2 phases

| Order | `V2 Phase` value | Observable gate |
|---|---|---|
| 0 | `0 Cutover & Cleanup` | V2 truth is authoritative; obsolete paths can be removed without losing evidence |
| 1 | `1 Pump Protocol Truth` | one version-bounded Pump path has a reviewed evidence matrix |
| 2 | `2 Authentic Acquisition` | one approved bounded OF1 plan produces verified authentic raw evidence and live progress |
| 3 | `3 Bronze & Silver` | authentic immutable Bronze/Silver plus static quality/lifecycle evidence |
| 4 | `4 Research Observatory` | authentic lifecycle and quality evidence are interactively visible in a browser |
| 5 | `5 Scale & Data Sufficiency` | outcome-independent sampling yields a Cohort Explorer and an explicit sufficiency verdict |
| 6 | `6 Gold & Edge Validation` | PIT Gold produces a falsifiable baseline, `INSUFFICIENT_SAMPLE`, or falsification |
| 7 | `7 Prospective Shadow` | no-order Triton-only prospective evidence measures parity, quotes, latency and capacity |
| 8 | `8 New Rust Paper Engine` | new event-sourced paper semantics pass prospective executable-evidence gates |
| 9 | `9 Professional Workstation` | later linked, dockable professional trading workflows use proven contracts |
| 10 | `10 VPS & Gated Live` | only separately approved VPS and isolated live-capable boundaries |

Exact range sizes, budgets, research windows, folds and dates are approved plan parameters, not phase constants.

## V2 disposition field

Add a single-select field named `V2 Disposition` without changing GitHub `Status`.

| Option | Meaning |
|---|---|
| `ACTIVE NOW` | the one concrete current V2 delivery; programs/epics may carry broader routing but do not select another delivery head |
| `NEXT` | the one explicitly accepted direct successor behind the concrete current delivery |
| `LATER` | valid requirement gated by later evidence/dependencies |
| `SPLIT` | original requirements route to two or more named V2 successors |
| `SUPERSEDED` | one named V2 delivery contract replaces execution as written; history remains |
| `RETIRED` | no longer product scope and no successor is required |

Suggested colors are red, orange, yellow, purple, gray and gray respectively. No #27–#63 issue is classified `RETIRED`; retirement applies to obsolete TrueNAS/Phase 8/Hermes project items outside this range.

Exactly one concrete non-program/non-epic delivery is `ACTIVE NOW`. Completed operational controls such as #56 retain their verified Evidence without receiving a second active-delivery disposition. Cross-cutting programs/epics may be broader without obscuring the delivery head.

## Successor catalog

The identifiers below are migration keys, not GitHub issue numbers. Do not create E0–E7 or B2A–B8 until the bounded sync/config PR is green and merged. Then create each issue, record its actual number in migration evidence, and use only actual links in issue notes.

### Governance implementation

| Key | Proposed issue | V2 phase | Disposition during migration | Observable purpose |
|---|---|---|---|---|
| [G0 / #70](https://github.com/daffieeee-arch/solana-paper-scanner/issues/70) | Implement and verify the Project #4 V2 rebase | 0 | `ACTIVE NOW` | own the export, bounded Roadmap Sync/config PR, successor migration, reconciliation, retention audit and repository evidence |

G0 was created before V2 metadata support existed, so its initial body uses only metadata accepted by current `main`; its current-schema status is `In Progress`. After an explicit merge/migration GO and trusted-main reconciliation, G0 may receive its V2 fields. G0 remains the single concrete `ACTIVE NOW` delivery until every migration gate passes.

### Program and epics

| Key | Proposed issue | V2 phase | Initial disposition | Observable purpose |
|---|---|---|---|---|
| E0 | Data-first Solana Quant Platform — edge discovery or falsification | 0 | `ACTIVE NOW` | one program contract whose valid outcome includes falsification |
| E1 | Versioned Pump truth and authentic research datasets | 1 | `NEXT` | parent for protocol, acquisition and immutable datasets |
| E2 | Research Observatory | 4 | `LATER` | authentic data quality/lifecycle/cohort evidence visible before the workstation |
| E3 | Edge discovery, data sufficiency and falsification | 5 | `LATER` | PIT research with explicit sufficiency and failure criteria |
| E4 | Prospective Triton-only shadow | 7 | `LATER` | no-order live parity/quote/latency/capacity evidence |
| E5 | New Rust paper engine | 8 | `LATER` | replacement for frozen scanner/portfolio/paper semantics |
| E6 | Professional Trading Workstation | 9 | `LATER` | full linked/dockable cockpit after evidence/runtime contracts |
| E7 | VPS and gated live execution | 10 | `LATER` | generic Linux operations and separately approved live boundary |

E2 and E6 are deliberately separate frontend epics. E4 and E5 are deliberately separate prospective-shadow and paper-engine epics.

### First bounded deliveries

| Key | Proposed issue | Parent | V2 phase | Disposition after verified migration | Visible/research-measurable outcome |
|---|---|---|---|---|---|
| B2A | Remove obsolete platform paths and quarantine frozen legacy runtime | E0 | 0 | `ACTIVE NOW` | obsolete target removed without losing invariants; reachable legacy fail-closed |
| B3 | Prove one Pump protocol truth walking skeleton | E1 | 1 | `NEXT` | protocol evidence matrix |
| B4 | Acquire bounded official OF1 slices with live progress and deterministic resume | E1 | 2 | `LATER` | terminal/TUI progress and verified run receipt |
| B5 | Produce Rust-owned/authorized authentic Raw → Bronze → Silver with static evidence report | E1 | 3 | `LATER` | static HTML/JSON data-quality and token-lifecycle report; physical Parquet writer decided here |
| B6 | Deliver interactive Research Observatory MVP | E2 | 4 | `LATER` | browser lifecycle replay and data-quality panels |
| B7 | Scale bounded sampling and deliver Cohort Explorer/data-sufficiency report | E1, E2, E3 | 5 | `LATER` | cohort distributions plus sufficient/insufficient verdict |
| B8 | Produce PIT Gold v0 and first falsifiable baseline | E3 | 6 | `LATER` | baseline with untouched evaluation, or valid `INSUFFICIENT_SAMPLE`/falsification |

B3–B5 must distinguish `ENGINEERING_VALIDATION_ONLY` from outcome-independent `RESEARCH_SAMPLING`; the former never enters strategy evidence.

During migration G0 alone is concrete `ACTIVE NOW`; when successors are created, B2A starts as `NEXT` and B3–B8 as `LATER`. After verified migration G0 becomes Done, B2A becomes `ACTIVE NOW`, B3 alone becomes `NEXT`, and B4–B8 stay `LATER`. Thereafter a later delivery cannot leave `LATER` until its direct predecessor is accepted; each promotion is an explicit governance update, not automatic queue inflation.

## Complete #27–#63 disposition and successor mapping

| Issue | Preserved requirement | V2 disposition | Earliest V2 phase | Successor/child mapping and rationale |
|---|---|---|---|---|
| [#27](https://github.com/daffieeee-arch/solana-paper-scanner/issues/27) | canonical pool/asset birth | `LATER` | 8 | E5; retain canonical birth evidence for the new engine, not legacy patching |
| [#28](https://github.com/daffieeee-arch/solana-paper-scanner/issues/28) | asset identity versus dynamic routes/graduation | `LATER` | 8 | E5; asset owns the position while routes evolve |
| [#29](https://github.com/daffieeee-arch/solana-paper-scanner/issues/29) | transactional outbox/replayable projections | `SPLIT` | 3 | B5 carries atomic immutable dataset publication/replay; E5 carries ledger/outbox/projectors |
| [#30](https://github.com/daffieeee-arch/solana-paper-scanner/issues/30) | bounded state machine, backpressure, resume, gaps/finality | `SPLIT` | 2 | B4/B5 carry historical ordering, coverage, gaps and resume; E4 carries live stream semantics |
| [#31](https://github.com/daffieeee-arch/solana-paper-scanner/issues/31) | observed state versus executable quotes/reference price | `SPLIT` | 1 | B3/B5/B8 carry evidence taxonomy; E5 carries executable-quote boundary |
| [#32](https://github.com/daffieeee-arch/solana-paper-scanner/issues/32) | quote-required paper fill and explicit no-fill | `LATER` | 8 | E5; no new strategy uses legacy fill semantics |
| [#33](https://github.com/daffieeee-arch/solana-paper-scanner/issues/33) | versioned shared SOL/USD context | `SPLIT` | 6 | B8 carries PIT context or explicit `UNAVAILABLE`; E4/E5 carry prospective/runtime context |
| [#34](https://github.com/daffieeee-arch/solana-paper-scanner/issues/34) | Pump capability, flow and finality semantics | `SPLIT` | 1 | B3/B5 carry registry/normalized flow; E5 carries entry capability gate |
| [#35](https://github.com/daffieeee-arch/solana-paper-scanner/issues/35) | professional information architecture | `SPLIT` | 4 | E2/B6 define Observatory IA; E6 defines later workstation IA |
| [#36](https://github.com/daffieeee-arch/solana-paper-scanner/issues/36) | dockable/resizable multi-monitor shell | `LATER` | 9 | E6; not required for the first authentic visible result |
| [#37](https://github.com/daffieeee-arch/solana-paper-scanner/issues/37) | sequenced snapshot/resume/gap WebSocket plane | `LATER` | 9 | E6, dependent on E4; early Observatory remains bounded/read-only |
| [#38](https://github.com/daffieeee-arch/solana-paper-scanner/issues/38) | professional charting | `SPLIT` | 4 | B6 supplies lifecycle charts; E6 supplies full chart foundation |
| [#39](https://github.com/daffieeee-arch/solana-paper-scanner/issues/39) | virtualized grids and linked context | `SPLIT` | 4 | B6/B7 supply bounded tape/cohort linking; E6 supplies workstation-scale grids |
| [#40](https://github.com/daffieeee-arch/solana-paper-scanner/issues/40) | Markets/Launch Explorer | `SPLIT` | 4 | B6/B7 supply historical token/cohort exploration; E6 supplies prospective Markets workspace |
| [#41](https://github.com/daffieeee-arch/solana-paper-scanner/issues/41) | Paper Trading workspace | `LATER` | 9 | E6, blocked on E5 |
| [#42](https://github.com/daffieeee-arch/solana-paper-scanner/issues/42) | Flow & Microstructure | `SPLIT` | 4 | B6/B7 supply historical lifecycle/flow; E6 supplies prospective/executable microstructure |
| [#43](https://github.com/daffieeee-arch/solana-paper-scanner/issues/43) | Risk & Actor Intelligence | `SPLIT` | 5 | B7/E3 carry evidence-gated Class-B actor work; E6 supplies later risk workspace |
| [#44](https://github.com/daffieeee-arch/solana-paper-scanner/issues/44) | Portfolio & Journal | `SPLIT` | 5 | B7/B8 supply experiment/cohort attribution; E6 supplies later portfolio/journal |
| [#45](https://github.com/daffieeee-arch/solana-paper-scanner/issues/45) | Strategy Lab, Research and Data Quality | `SPLIT` | 3 | B5/B6/B7/B8 produce data quality, cohort and strategy outcomes; E6 supplies full workspace |
| [#46](https://github.com/daffieeee-arch/solana-paper-scanner/issues/46) | command palette, alerts and mobile | `LATER` | 9 | E6 |
| [#47](https://github.com/daffieeee-arch/solana-paper-scanner/issues/47) | design system, accessibility, performance and quality | `SPLIT` | 4 | B6 owns MVP quality; E6 owns workstation design system/scale |
| [#48](https://github.com/daffieeee-arch/solana-paper-scanner/issues/48) | authentic Old Faithful vertical slice | `SPLIT` | 2 | B4 acquisition, B5 Bronze/Silver/static report, B6 interactive visibility; two slice classes required |
| [#49](https://github.com/daffieeee-arch/solana-paper-scanner/issues/49) | immutable Parquet/Arrow and analytical projections | `SPLIT` | 3 | B5 carries Rust-owned/authorized canonical layers and the explicit physical-writer decision; any Python writer is generated/lossless with schema/logical-hash parity. B7 carries scaled Polars/DuckDB research; ClickHouse is a later adoption gate under E1 |
| [#50](https://github.com/daffieeee-arch/solana-paper-scanner/issues/50) | PIT features, labels and walk-forward | `SPLIT` | 6 | B8 PIT/censoring/baseline; E3 later scaled evaluation |
| [#51](https://github.com/daffieeee-arch/solana-paper-scanner/issues/51) | ML/ranking/survival research | `LATER` | 6 | E3 after stable sufficient Gold; no automatic promotion |
| [#52](https://github.com/daffieeee-arch/solana-paper-scanner/issues/52) | prospective shadow and engine boundary | `SPLIT` | 7 | E4 owns Triton-only shadow; E5 owns a later explicit adopt/reject engine decision |
| [#53](https://github.com/daffieeee-arch/solana-paper-scanner/issues/53) | execution, signing and reconciliation boundary | `SPLIT` | 8 | E5 owns paper/transaction semantics; E7 owns isolated signing/VPS/live gating |
| [#54](https://github.com/daffieeee-arch/solana-paper-scanner/issues/54) | modularization/property/mutation/fuzz/state-machine tests | `SPLIT` | 0 | B2A/B3/B4/B5/B6/B8 own scoped tests; #62 retains cross-cutting governance |
| [#55](https://github.com/daffieeee-arch/solana-paper-scanner/issues/55) | OpenTelemetry/SLO/evidence-aware observability | `SPLIT` | 2 | B4/B5/B6 own bounded run/evidence telemetry; E4/E7 own later runtime SLOs |
| [#56](https://github.com/daffieeee-arch/solana-paper-scanner/issues/56) | Roadmap Sync | `SUPERSEDED` | 0 | G0 replaces/extends its V1 sync/config contract; #56 remains `Done`, pinned as a visible operational control and Evidence `Operationally Verified` |
| [#57](https://github.com/daffieeee-arch/solana-paper-scanner/issues/57) | trusted legacy-runtime correctness epic | `SUPERSEDED` | 8 | E5 replaces execution target; #27–#34 requirements remain evidence |
| [#58](https://github.com/daffieeee-arch/solana-paper-scanner/issues/58) | combined professional cockpit epic | `SPLIT` | 4 | E2 Research Observatory and E6 Professional Trading Workstation |
| [#59](https://github.com/daffieeee-arch/solana-paper-scanner/issues/59) | authentic data/research epic | `SUPERSEDED` | 1 | E1 removes ClickHouse/TrueNAS assumptions and supplies visible bounded gates |
| [#60](https://github.com/daffieeee-arch/solana-paper-scanner/issues/60) | quant/ML validation epic | `SUPERSEDED` | 5 | E3 accepts edge discovery, falsification or insufficient sample |
| [#61](https://github.com/daffieeee-arch/solana-paper-scanner/issues/61) | combined shadow/execution epic | `SPLIT` | 7 | E4 prospective shadow, E5 new paper engine and E7 gated live/VPS |
| [#62](https://github.com/daffieeee-arch/solana-paper-scanner/issues/62) | engineering/observability/governance epic | `ACTIVE NOW` | 0 | PR 1 and B2A; keep open and continue as cross-cutting control |
| [#63](https://github.com/daffieeee-arch/solana-paper-scanner/issues/63) | paper-first program baseline | `SUPERSEDED` | 0 | E0 is the data-first program contract; preflight found this issue already `CLOSED` / `COMPLETED` from PR #69, so its state is a stop condition pending an explicit governance decision |

All 37 legacy rows are mapped exactly once. Every `SPLIT` row has at least two named successor destinations; every `SUPERSEDED` row has exactly one.

## Project schema delta

The bounded G0 Roadmap Sync/config PR must add and test, as one reviewable default-branch contract:

- `V2 Disposition` and `V2 Phase` as separate single-select fields while preserving `Status` and historical `Phase`;
- `v2Disposition`/`v2Phase` metadata parsing, validation and idempotent writes;
- the expanded Evidence options and option-ID retention;
- all V2 views and their exact tested filters/visible fields;
- replacement Project short description and README;
- item eligibility, retention, archive and reopen behavior.

The pre-G0 `main` synchronizer does **not** parse/write V2 keys; it scans all historical issues/PRs, adds missing items and unconditionally unarchives archived items. The G0 branch adds the tested replacement contract, but no manual V2-field workaround is allowed. The sync/config PR must merge before any issue receives V2 metadata or any E0–E7/B2A–B8 successor is created. Do not edit old `phase` metadata.

### Evidence field

The final ordered option set is:

| Evidence option | Assignment rule |
|---|---|
| `Not Applicable` | coordination-only program/epic container; parents never inherit child evidence |
| `Unproven` | no accepted named evidence yet; default for concrete work |
| `Fixture` | deterministic synthetic/fixture evidence only |
| `Operationally Verified` | operational/governance control passed its named CI and live reconciliation/audit contract |
| `Engineering Validation` | authentic engineering mechanics passed, but not eligible for edge claims |
| `Research Candidate` | outcome-independent authentic sample passed acquisition/basic evidence gates but not full PIT research gates |
| `Research Ready` | accepted PIT dataset and methodology evidence |
| `Shadow` | accepted prospective no-order evidence |
| `Paper Proven` | accepted prospective executable paper evidence |
| `Live Proven` | separately approved live evidence |

Preserve the existing Project option IDs for semantically unchanged `Unproven`, `Fixture`, `Research Ready`, `Shadow`, `Paper Proven` and `Live Proven`; update descriptions in place rather than delete/recreate. Add IDs only for `Not Applicable`, `Operationally Verified`, `Engineering Validation` and `Research Candidate`. Tests must fail on option-ID churn.

Program/epic containers E0–E7 receive `Not Applicable`. G0 starts `Unproven` and becomes `Operationally Verified` only after the end-to-end audit. #56 remains `Done` and receives `Operationally Verified` because Roadmap Sync operation was actually verified. Concrete research items begin `Unproven` and advance only through their named evidence gates; `Done` alone never upgrades Evidence. The immutable slice class `ENGINEERING_VALIDATION_ONLY` maps at most to Project Evidence `Engineering Validation` and can never reach `Research Candidate` or `Research Ready`.

### Project description and README

The G0 config PR replaces—not appends to—the old paper/runtime-first Project short description and README. Proposed synchronized text:

```text
Short description:
Data-first, Triton-only Solana/Pump quant program (E0): authentic evidence,
Research Observatory before Professional Workstation, and edge discovery or
falsification. Profitability is not assumed.
```

```markdown
# Solana Quant Platform V2

Project #4 is the active delivery cockpit for program E0: build authentic,
point-in-time Solana/Pump evidence and discover a defensible edge or falsify
the hypothesis. Profitability is not assumed.

- Authoritative handoff: [docs/HANDOFF_V2.md](https://github.com/daffieeee-arch/solana-paper-scanner/blob/main/docs/HANDOFF_V2.md)
- Network boundary: Triton One only.
- Product order: authentic data and Research Observatory before the
  Professional Trading Workstation, prospective shadow and new paper engine.
- Project status is delivery metadata, never research evidence by itself.
```

Tests verify removal of the old #63/runtime-first Project copy and exact presence of E0, the handoff link, edge discovery or falsification, Observatory-before-Workstation, Triton-only and no-profitability-assumption language.

## Views

Reuse each named legacy view in place through its configured alias, then verify the renamed V2 view. No view is archived or deleted by G0. Exact GitHub filter grammar must be dry-run/live validated because a view-update warning is not equivalent to success.

| View | Layout | Intended filter |
|---|---|---|
| Now | Board | `v2-disposition:"ACTIVE NOW" -status:Done,Cancelled -work-type:Program,Epic` |
| Next | Table | `v2-disposition:NEXT -status:Done,Cancelled -work-type:Program,Epic` |
| Data | Table | `v2-phase:"1 Pump Protocol Truth","2 Authentic Acquisition","3 Bronze & Silver","5 Scale & Data Sufficiency" v2-disposition:"ACTIVE NOW",NEXT,LATER` |
| Observatory | Table | `v2-phase:"4 Research Observatory" v2-disposition:"ACTIVE NOW",NEXT,LATER` |
| Research | Table | `v2-phase:"5 Scale & Data Sufficiency","6 Gold & Edge Validation" v2-disposition:"ACTIVE NOW",NEXT,LATER` |
| Later | Table | `v2-disposition:LATER` |
| Retired | Table | `v2-disposition:SUPERSEDED,RETIRED` |
| Migration Ledger | Table | `v2-disposition:SPLIT,SUPERSEDED,RETIRED` |

Show at least Title, Status, V2 Disposition, V2 Phase, Priority, Area, Evidence, Risk and Assignees. Active delivery views exclude `SPLIT` and `SUPERSEDED` anchors. Now/Next also separate Program/Epic containers so exactly one concrete delivery head remains obvious. Exact GitHub filter grammar is an acceptance test, not assumed from this proposed spelling.

## Project-item retention and archive policy

Project #4 is an active delivery cockpit, not a permanent duplicate of GitHub history.

- Open V2 roadmap issues and open PRs remain active Project items.
- The hashed export identified exactly 30 active linked merged pre-V2 PR items through PR #68 as archive candidates: PRs #1–#26, #64, #65, #67 and #68. The config binds that sorted allowlist, its separate set hash and the root snapshot hash. Initial reconciliation rereads the live repository and Project and fails before its first mutation if the observed merged set differs or any audited candidate is absent/already archived. The items are not deleted, and the G0 branch performs no live archival.
- The G0 config PR proposes the explicit reviewed value `closed_item_retention_days: 30`. The key is mandatory: validation fails when it is absent or invalid, and code supplies no implicit fallback. Future merged/closed PR items—and closed issue items unless explicitly pinned as continuing controls—stay active for that interval and are then eligible for archival by an authorized reconciliation.
- #56 is an explicitly pinned continuing operational control: it remains visible as `Done` / `Operationally Verified` until a later reviewed governance decision replaces it.
- Issue #63 is temporarily pinned against archival while its preflight state discrepancy is unresolved. That pin preserves audit visibility; it does not approve or infer a state correction.
- An archived item is unarchived only when its underlying issue or PR is actually reopened. Ordinary scheduled/event reconciliation, metadata drift or discovery of a still-closed item must not reactivate it.
- Eligibility is checked before both add and unarchive, so archived/absent historical items are not re-added through full-history enumeration.
- GitHub issue/PR bodies, comments, close reasons and event history remain the permanent archive. The synchronizer never deletes items or GitHub history.

The Roadmap Sync extension tests open retention, the exact pre-V2 candidate rule, the configured grace interval, pinned-control behavior, closed-item non-reactivation, evidence-backed actual-reopen reactivation, no-delete behavior and deterministic counts. Thirty days remains a reviewable candidate until the G0 PR is accepted. No policy is applied from the PR branch. An explicit GO to merge also authorizes the automatic trusted-main reconciliation, so the value and stop conditions must be decided before merge.

## Issue-note template

Append—never replace—a note like:

```markdown
## V2 cutover note — YYYY-MM-DD

- V2 Disposition: SPLIT
- V2 Phase: 4 Research Observatory
- Successors: #<actual>, #<actual>

The original problem statement and acceptance criteria above remain historical
engineering evidence. This note neither marks them delivered nor changes GitHub state.
```

Retain existing `roadmap-meta`. Add V2 metadata keys only after the synchronizer on `main` validates them.

## G0 and post-merge migration transaction

1. **Complete:** PR #69 merged at `05c3d885943c2319d6317b43546daca4b5918074`; post-merge CI and the succeeding ordinary Roadmap reconciliation passed.
2. **Complete:** the read-only Project/repository export was captured and hashed, including exact #27–#63 and pre-V2 merged-PR candidate subsets. See [`operations/PROJECT_V2_G0_PREFLIGHT.md`](operations/PROJECT_V2_G0_PREFLIGHT.md).
3. **Complete:** governance issue G0 exists as issue #70, created with only current-main metadata.
4. **Current bounded delivery:** create, review and make green one PR that extends Roadmap Sync, its directly required tests, `roadmap/project-config.json` and directly required operations/migration documentation. It performs no successor/issue metadata mutation and no live Project migration from the branch.
5. Require that PR to support and test `V2 Phase`, `V2 Disposition`, the complete Evidence taxonomy/ID retention, V2 views, replacement Project description/README, mandatory explicit retention, item eligibility/archive/reopen semantics, deterministic counts and the trusted-default-branch `PROJECT_TOKEN` boundary.
6. **Pre-merge stop and decide:** approve the retention value, resolve the documented issue #63 state discrepancy and clear every remaining review/security decision. The explicit GO must cover both merge and the automatic trusted-main reconciliation.
7. Merge, verify the exact SHA and audit the resulting schema/view/retention reconciliation against the hashed baseline.
8. Only after step 7 passes, create E0–E7 and B2A–B8, record their actual numbers/parents, add V2 metadata, and append bounded cutover notes to existing issues without changing their state in the initial batch.
9. Run subsequent reconciliation only through the protected trusted-default-branch workflow.
10. Audit all item/issue counts, successor links, option IDs/names, field values, states, views/filters, Evidence assignments, Project text and archive outcomes against the hashed ledger.
11. Record actual successor issue numbers, reconciled counts/hashes and the migration outcome in repository evidence. Complete the separate individual retirement gate below and append its final results to that evidence.
12. Only after repository evidence and retirement audit are accepted may PR 2A be authorized.

Stop on a count mismatch, missing successor, unexpected issue state change, option-ID drift, retention error, description/README drift or view warning. The preflight exports and original issue bodies are the rollback source; restore values/notes explicitly rather than guessing.

## Verified superseded-anchor closeout

The initial migration performs no bulk closure. After successors exist and mappings, notes, fields, views and Evidence are verified:

1. review each non-active `SPLIT` or `SUPERSEDED` anchor independently;
2. add a final successor-linked comment while preserving the body, acceptance criteria and existing comments;
3. close only fully covered anchors individually as `not planned / superseded`;
4. leave every standalone `LATER` requirement open;
5. keep #56 `Done` and keep #62 open as the cross-cutting control;
6. apply the separately approved resolution for issue #63; the original plan was to retain it open until E0 verification and then mark it individually `not planned / superseded`, but preflight found it already `CLOSED` / `COMPLETED`;
7. record each decision/close reason and post-close Project archive state in repository evidence.

Expected individual closure candidates, subject to the per-issue verification, are #29–#31, #33–#35, #38–#40, #42–#45, #47–#50, #52–#55, #57–#61 and finally #63. Standalone `LATER` requirements #27, #28, #32, #36, #37, #41, #46 and #51 remain open. No item, issue, PR, comment or historical acceptance criterion is deleted.

## Acceptance checks for the migration

- exactly 37 old issues mapped once;
- the exact merged #69 SHA/CI and hashed preflight export are recorded;
- G0 exists as issue #70; its one bounded sync/config PR merged before any successor/V2 metadata creation;
- issue #56 remains `Done` with `Operationally Verified`; issue #62 remains open; the issue #63 discrepancy has an explicit, audited resolution before migration proceeds;
- original acceptance criteria remain byte-for-byte present;
- every `SPLIT` has at least two valid successor links;
- every `SUPERSEDED` has one valid successor link;
- no orphan successor and no unintended `Closes` relationship;
- `Phase` remains unchanged and `V2 Phase` uses the same order as [`ROADMAP.md`](ROADMAP.md);
- all ten Evidence options exist; semantically unchanged option IDs are retained and assignment rules pass;
- the Project description/README contains the approved E0/handoff/falsification/Observatory-first/Triton-only/no-profit-assumption text;
- exactly one concrete delivery is `ACTIVE NOW`; after migration it is B2A, with only B3 `NEXT` and B4–B8 `LATER`;
- Now, Next, Data, Observatory, Research, Later and Retired views return the intended sets;
- open/closed/archive/reopen retention tests and live audit pass without unconditional reactivation;
- actual E0–E7/B2A–B8 issue numbers and post-close results are committed as repository evidence;
- Roadmap Sync dry-run/tests and a post-reconcile audit are green.

## G0 support PR non-goals

No E0–E7/B2A–B8 creation, V2 note/metadata mutation, issue-state repair, live Project migration/archive, provider call, PR 2A cleanup, tag or product/code removal occurs in the G0 support PR. Ordinary current-schema Roadmap Sync triggered by its PR events may reconcile only normal PR and G0 fields; it is not the V2 migration.
