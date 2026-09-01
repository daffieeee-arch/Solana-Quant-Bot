# PROJECT_V2_REBASE.md — controlled Project #4 migration plan

> **Document status: ACTIVE migration plan.** This document proposes the post-merge migration. It does not itself mutate [Project #4](https://github.com/users/daffieeee-arch/projects/4), any issue, or any pull request.

## Authority and migration rules

Project #4 remains the central delivery roadmap. The repository keeps this reviewable ledger so a Project mutation cannot silently reinterpret history.

The migration is non-destructive:

- preserve every original issue body, acceptance criterion, comment, state and close reason;
- create successors before adding migration notes to old issues;
- do not use `Closes`, `Fixes` or equivalent links during migration;
- treat V2 disposition as delivery routing, never as proof of completion;
- keep #56 `Done`; keep #62 and #63 open;
- retain the existing `Phase` field and its values as historical metadata;
- add a separate `V2 Phase` field rather than repurposing `Phase`;
- do not bulk-close any issue;
- export current issue bodies and Project fields/views before mutation.

The actual rebase occurs only after PR 1 is merged and verified, and before PR 2A starts.

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
| `ACTIVE NOW` | current V2 delivery or a delivered control that remains operational |
| `NEXT` | accepted ordered near-term V2 pipeline behind current work; not necessarily the single next PR |
| `LATER` | valid requirement gated by later evidence/dependencies |
| `SPLIT` | original requirements route to two or more named V2 successors |
| `SUPERSEDED` | one named V2 delivery contract replaces execution as written; history remains |
| `RETIRED` | no longer product scope and no successor is required |

Suggested colors are red, orange, yellow, purple, gray and gray respectively. No #27–#63 issue is classified `RETIRED`; retirement applies to obsolete TrueNAS/Phase 8/Hermes project items outside this range.

## Successor catalog

The identifiers below are migration keys, not GitHub issue numbers. During the post-merge migration, create the issue first, record its actual number in the migration evidence, then replace keys in issue notes with links.

### Program and epics

| Key | Proposed issue | V2 phase | Initial disposition | Observable purpose |
|---|---|---|---|---|
| E0 | Data-first Solana Quant Platform — edge discovery or falsification | 0 | `ACTIVE NOW` | one program contract whose valid outcome includes falsification |
| E1 | Versioned Pump truth and authentic research datasets | 1 | `ACTIVE NOW` | parent for protocol, acquisition and immutable datasets |
| E2 | Research Observatory | 4 | `NEXT` | authentic data quality/lifecycle/cohort evidence visible early |
| E3 | Edge discovery, data sufficiency and falsification | 5 | `NEXT` | PIT research with explicit sufficiency and failure criteria |
| E4 | Prospective Triton-only shadow | 7 | `LATER` | no-order live parity/quote/latency/capacity evidence |
| E5 | New Rust paper engine | 8 | `LATER` | replacement for frozen scanner/portfolio/paper semantics |
| E6 | Professional Trading Workstation | 9 | `LATER` | full linked/dockable cockpit after evidence/runtime contracts |
| E7 | VPS and gated live execution | 10 | `LATER` | generic Linux operations and separately approved live boundary |

E2 and E6 are deliberately separate frontend epics. E4 and E5 are deliberately separate prospective-shadow and paper-engine epics.

### First bounded deliveries

| Key | Proposed issue | Parent | V2 phase | Initial disposition | Visible/research-measurable outcome |
|---|---|---|---|---|---|
| B2A | Remove obsolete platform paths and quarantine frozen legacy runtime | E0 | 0 | `NEXT` | obsolete target removed without losing invariants; reachable legacy fail-closed |
| B3 | Prove one Pump protocol truth walking skeleton | E1 | 1 | `NEXT` | protocol evidence matrix |
| B4 | Acquire bounded official OF1 slices with live progress and deterministic resume | E1 | 2 | `NEXT` | terminal/TUI progress and verified run receipt |
| B5 | Produce authentic Raw → Bronze → Silver with static evidence report | E1 | 3 | `NEXT` | static HTML/JSON data-quality and token-lifecycle report |
| B6 | Deliver interactive Research Observatory MVP | E2 | 4 | `NEXT` | browser lifecycle replay and data-quality panels |
| B7 | Scale bounded sampling and deliver Cohort Explorer/data-sufficiency report | E1, E2, E3 | 5 | `NEXT` | cohort distributions plus sufficient/insufficient verdict |
| B8 | Produce PIT Gold v0 and first falsifiable baseline | E3 | 6 | `NEXT` | baseline with untouched evaluation, or valid `INSUFFICIENT_SAMPLE`/falsification |

B3–B5 must distinguish `ENGINEERING_VALIDATION_ONLY` from outcome-independent `RESEARCH_SAMPLING`; the former never enters strategy evidence.

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
| [#49](https://github.com/daffieeee-arch/solana-paper-scanner/issues/49) | immutable Parquet/Arrow and analytical projections | `SPLIT` | 3 | B5 Python-materialized immutable layers; B7 scaled Polars/DuckDB research; ClickHouse is a later adoption gate under E1 |
| [#50](https://github.com/daffieeee-arch/solana-paper-scanner/issues/50) | PIT features, labels and walk-forward | `SPLIT` | 6 | B8 PIT/censoring/baseline; E3 later scaled evaluation |
| [#51](https://github.com/daffieeee-arch/solana-paper-scanner/issues/51) | ML/ranking/survival research | `LATER` | 6 | E3 after stable sufficient Gold; no automatic promotion |
| [#52](https://github.com/daffieeee-arch/solana-paper-scanner/issues/52) | prospective shadow and engine boundary | `SPLIT` | 7 | E4 owns Triton-only shadow; E5 owns a later explicit adopt/reject engine decision |
| [#53](https://github.com/daffieeee-arch/solana-paper-scanner/issues/53) | execution, signing and reconciliation boundary | `SPLIT` | 8 | E5 owns paper/transaction semantics; E7 owns isolated signing/VPS/live gating |
| [#54](https://github.com/daffieeee-arch/solana-paper-scanner/issues/54) | modularization/property/mutation/fuzz/state-machine tests | `SPLIT` | 0 | B2A/B3/B4/B5/B6/B8 own scoped tests; #62 retains cross-cutting governance |
| [#55](https://github.com/daffieeee-arch/solana-paper-scanner/issues/55) | OpenTelemetry/SLO/evidence-aware observability | `SPLIT` | 2 | B4/B5/B6 own bounded run/evidence telemetry; E4/E7 own later runtime SLOs |
| [#56](https://github.com/daffieeee-arch/solana-paper-scanner/issues/56) | Roadmap Sync | `ACTIVE NOW` | 0 | delivered automation remains an active control; GitHub state remains `Done` |
| [#57](https://github.com/daffieeee-arch/solana-paper-scanner/issues/57) | trusted legacy-runtime correctness epic | `SUPERSEDED` | 8 | E5 replaces execution target; #27–#34 requirements remain evidence |
| [#58](https://github.com/daffieeee-arch/solana-paper-scanner/issues/58) | combined professional cockpit epic | `SPLIT` | 4 | E2 Research Observatory and E6 Professional Trading Workstation |
| [#59](https://github.com/daffieeee-arch/solana-paper-scanner/issues/59) | authentic data/research epic | `SUPERSEDED` | 1 | E1 removes ClickHouse/TrueNAS assumptions and supplies visible bounded gates |
| [#60](https://github.com/daffieeee-arch/solana-paper-scanner/issues/60) | quant/ML validation epic | `SUPERSEDED` | 5 | E3 accepts edge discovery, falsification or insufficient sample |
| [#61](https://github.com/daffieeee-arch/solana-paper-scanner/issues/61) | combined shadow/execution epic | `SPLIT` | 7 | E4 prospective shadow, E5 new paper engine and E7 gated live/VPS |
| [#62](https://github.com/daffieeee-arch/solana-paper-scanner/issues/62) | engineering/observability/governance epic | `ACTIVE NOW` | 0 | PR 1 and B2A; keep open and continue as cross-cutting control |
| [#63](https://github.com/daffieeee-arch/solana-paper-scanner/issues/63) | paper-first program baseline | `SUPERSEDED` | 0 | E0 is the data-first program contract; keep #63 open during migration |

All 37 legacy rows are mapped exactly once. Every `SPLIT` row has at least two named successor destinations; every `SUPERSEDED` row has exactly one.

## Project schema delta

Add `V2 Disposition` and `V2 Phase` as separate single-select fields. Preserve `Status`, legacy `Phase`, existing item state and dates.

The current synchronizer does **not** parse or write `v2Disposition`/`v2Phase`. It preserves unconfigured extra fields/views, but manual values would not be repository-driven. Before issue metadata receives V2 keys, land a separately reviewed bounded Roadmap Sync/config extension with tests, or explicitly approve a temporary manual migration ledger. The recommended route is the tested sync extension. Do not edit old `phase` metadata.

## Views

Create and verify new views before archiving any old view. Exact GitHub filter grammar must be dry-run/live validated because a view-update warning is not equivalent to success.

| View | Layout | Intended filter |
|---|---|---|
| Now | Board | `V2 Disposition:"ACTIVE NOW" -Status:Done -Status:Cancelled` |
| Next | Table | `V2 Disposition:NEXT -Status:Done -Status:Cancelled` |
| Data | Table | V2 phases 1, 2, 3 and 5; active/next/later only |
| Observatory | Table | V2 phase 4; active/next/later only |
| Research | Table | V2 phases 5 and 6; active/next/later only |
| Later | Roadmap or table | `V2 Disposition:LATER` |
| Retired | Table | `V2 Disposition:SUPERSEDED,RETIRED` |
| Migration Ledger | Table | `V2 Disposition:SPLIT,SUPERSEDED,RETIRED` |

Show at least Title, Status, V2 Disposition, V2 Phase, Priority, Area, Evidence, Risk and Assignees. Active delivery views exclude `SPLIT` and `SUPERSEDED` anchors so historical issues do not masquerade as current tasks.

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

## Post-merge migration transaction

1. Verify merged PR 1 SHA and green CI.
2. Export issue #27–#63 bodies/state and Project #4 items, fields, options, values and views read-only; hash the export.
3. Create E0–E7 and B2A–B8, recording actual numbers and parent relationships without closing old issues.
4. Extend and test Roadmap Sync/config for `V2 Disposition`, `V2 Phase` and the new views, or stop for explicit approval of the documented temporary-manual alternative.
5. Run offline config dry-run and tests.
6. Append notes and V2 metadata in bounded, reviewable batches; preserve old metadata/content.
7. Reconcile Project #4 and compare item/field/update counts with the migration ledger.
8. Verify all 37 mappings, successors, states, evidence values and view filters.
9. Only after new views work, archive or rename obsolete views deliberately; do not delete historical issue data.
10. Record migration evidence and then authorize PR 2A.

Stop on a count mismatch, missing successor, unexpected issue state change, option-ID drift or view warning. The preflight exports and original issue bodies are the rollback source; restore values/notes explicitly rather than guessing.

## Acceptance checks for the migration

- exactly 37 old issues mapped once;
- #56 remains `Done`; #62 and #63 remain open;
- original acceptance criteria remain byte-for-byte present;
- every `SPLIT` has at least two valid successor links;
- every `SUPERSEDED` has one valid successor link;
- no orphan successor and no unintended `Closes` relationship;
- `Phase` remains unchanged and `V2 Phase` uses the same order as [`ROADMAP.md`](ROADMAP.md);
- Now, Next, Data, Observatory, Research, Later and Retired views return the intended sets;
- Roadmap Sync dry-run/tests and a post-reconcile audit are green.

## PR 1 non-goals

No GitHub mutation, issue creation/edit/close, Project field/view change, Project sync run, provider call, PR 2A cleanup or code removal occurs in PR 1.
