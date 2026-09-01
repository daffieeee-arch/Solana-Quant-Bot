# Project #4 V2 G0 preflight receipt

> **Document status: ACTIVE.** This is the repository-safe governance-evidence receipt for the read-only state captured immediately before governance issue G0 was created. It contains hashes, counts and public identifiers only. The raw export contains issue/PR bodies and therefore remains outside Git.

## Purpose and boundary

This receipt proves the input state for the bounded Project #4 sync/config change. It does not authorize or perform a Project migration, item archival, issue-state change or successor creation. The operative migration plan is [`../PROJECT_V2_REBASE.md`](../PROJECT_V2_REBASE.md); the security and runtime contract is [`GITHUB_PROJECTS_ROADMAP.md`](GITHUB_PROJECTS_ROADMAP.md).

The export was read-only and completed before G0 existed. Issue [#70](https://github.com/daffieeee-arch/solana-paper-scanner/issues/70) is the actual G0 governance issue created afterward with only metadata supported by the then-current `main`. It is open, current-schema `In Progress`, and the single concrete migration delivery focus.

## Verified merge and automation baseline

| Evidence | Result |
|---|---|
| PR #69 squash-merge SHA | `05c3d885943c2319d6317b43546daca4b5918074` |
| [Post-merge main CI run](https://github.com/daffieeee-arch/solana-paper-scanner/actions/runs/33548641051) | `success` for the exact squash-merge SHA |
| [Main-push Roadmap Sync run](https://github.com/daffieeee-arch/solana-paper-scanner/actions/runs/33548641553) | `cancelled` by workflow concurrency |
| [Succeeding issue-event Roadmap Sync run](https://github.com/daffieeee-arch/solana-paper-scanner/actions/runs/33548642312) | `success` for the same SHA; this was the ordinary reconciliation that superseded the cancelled instance |
| [G0 creation Roadmap Sync run](https://github.com/daffieeee-arch/solana-paper-scanner/actions/runs/33551185481) | `success` under the current schema after the snapshot |

The cancelled main-push instance is not described as green. The successful successor run is the verified post-merge ordinary reconciliation. Its triggering issue event was temporally associated with PR #69's merge and the issue #63 transition captured below; the exact initiating mechanism is unproven.

## Immutable private snapshot identity

| Field | Value |
|---|---|
| Storage class | `LOCAL_PRIVATE_WSL_EXT4` |
| Local retention path | `/home/dmesdary/.local/share/solana-quant-platform/governance/project4-v2-rebase/project4-pre-v2-g0-20260901T193839Z-05c3d885943c/` |
| Snapshot ID | `project4-pre-v2-g0-20260901T193839Z-05c3d885943c` |
| Capture interval (UTC) | `2026-09-01T19:38:39Z` through `2026-09-01T19:42:51Z` |
| Snapshot root SHA-256 | `98a97b7bfb74d194898d0aa1d65c0a9caf8c4015f09ae6a93c76fd8289ee3585` |
| Exact issues #27–#63 SHA-256 | `a4181fff5009797e720f82f92f26df61f2973c532e1f25dc1a14e312b8730769` |
| Pre-V2 merged-PR candidate-set SHA-256 | `0211a16af1a18006bfa1005f12e4b57c9373508d655884f59a2a3f18656d9e67` |
| Consistency reread | `MATCHED` |
| Snapshot secret scan | `PASS` |
| Local access mode at verification | directory `0700`; receipt/manifest files `0600` |

`SHA256SUMS` binds the individual exported files; `SNAPSHOT_SHA256` binds that checksum ledger. Verification passed with every ledger entry intact. The absolute path is intentionally machine-local and non-portable. Git retains only this receipt: no exported issue body, PR body, comment, review, timeline, commit payload, token or complete Project JSON is committed.

## Captured counts

| Count | Exact value |
|---|---:|
| Project items | 69 |
| Linked repository issues | 37 |
| Linked repository pull requests | 32 |
| Archived Project items | 0 |
| Exact issues #27–#63 | 37 |
| Linked merged pre-V2 PR archive candidates through PR #68 | 30 |

The 69-item snapshot predates G0 issue #70. These are baseline counts, not expected post-migration counts. Reconciliation must compute and report deterministic planned/applied/skipped counts and stop on unexplained drift.

## Captured issue #63 state and post-snapshot delta

The accepted plan expected issue #63 to remain open during initial migration. The preflight instead observed:

- state `CLOSED`;
- state reason / Project status `COMPLETED` / `Done`;
- transition time `2026-09-01T19:17:25Z`;
- PR #69's squash commit references issue #63 and contains `Roadmap: #62 #63`; `Roadmap:` is not a documented closing keyword, and the exact initiating mechanism is unproven;
- preflight action `NONE`.

The immutable snapshot correctly records that captured state and remains unchanged. After capture, governance explicitly authorized and performed a reopen of issue #63. The correction is recorded in [issue #63](https://github.com/daffieeee-arch/solana-paper-scanner/issues/63#issuecomment-5500016645) and [G0 issue #70](https://github.com/daffieeee-arch/solana-paper-scanner/issues/70#issuecomment-5500023997); current-schema [Roadmap Sync run `33555734237`](https://github.com/daffieeee-arch/solana-paper-scanner/actions/runs/33555734237) passed with 38 issues, 33 pull requests, zero additions, zero unarchives and one normal field update. This reopen is a documented post-snapshot delta, not a reason to replace the snapshot or alter its hashes. Issue #63 remains open and temporarily pinned through initial G0/E0, then is closed individually as `not planned / superseded` only after verified E0.

## Bounded G0 support PR contract

The support PR may change only the Project synchronizer, declarative Project configuration, directly required sync tests and directly required operations/migration documentation. It implements and tests:

- separate `V2 Phase` and `V2 Disposition` fields;
- all ten reviewed Evidence options while preserving IDs for semantically equal live options;
- Now, Next, Data, Observatory, Research, Later, Retired and Migration Ledger views;
- replacement data-first Project short description and README;
- mandatory explicit `closed_item_retention_days`, with the approved value `30` and no code/config fallback; the exact 30 audited pre-V2 PR items archive immediately, while the grace interval applies to later closed items;
- deterministic eligibility and archival planning for the exact 30 captured active pre-V2 merged-PR candidates (PRs #1–#26, #64, #65, #67 and #68), with the sorted allowlist, initial active membership, root snapshot hash and candidate-set hash enforced before the first mutation;
- closed-item non-reactivation and reactivation only after evidence of an actual reopen;
- pinned continuing controls, including issue #56, plus a temporary issue #63 pin through initial G0/E0;
- eligibility before both item addition and unarchive;
- deterministic reconciliation counts and fail-closed drift handling;
- the trusted-default-branch `PROJECT_TOKEN` security boundary.

The PR branch performs no live archive or V2 migration. It creates no E0–E7/B2A–B8 issue, writes no V2 metadata or cutover note to issues #27–#63, and its synchronizer changes no issue state. The separately authorized issue #63 governance correction is a documented post-snapshot action outside synchronizer behavior.

## Security boundary

`PROJECT_TOKEN` remains a protected Actions secret and is never accepted from a pull-request branch, command line, config file or log. `pull_request_target` execution must use the trusted default-branch workflow, synchronizer and config; it must never check out or execute PR-head code. Pull-request validation is offline. Explicit approval now covers squash merge and the automatic trusted-default-branch push reconciliation after the correction commit and full CI pass; that run is the first point at which this change may apply the V2 schema/view/retention policy to the user-owned Project.

The private snapshot is a rollback/audit source, not runtime input. Its bodies are not copied into issues, PR text, prompts or MCP queries. No deletion is part of rollback: discrepancies are restored explicitly against the hashed source.

## Merge approval and post-merge procedure

Merging is the activation boundary because the existing push-to-`main` trigger runs Roadmap Sync. Governance has completed the decision gates:

1. `closed_item_retention_days: 30` is approved;
2. issue #63 was explicitly reopened, documented as a post-snapshot delta and successfully reconciled under the current schema;
3. squash merge plus automatic trusted-main reconciliation is explicitly authorized;
4. the correction commit and full CI must still pass before merge.

After merge:

1. verify the exact merge SHA and automatic trusted-main reconciliation;
2. audit fields, option IDs, views, item states, Project text, archival decisions and exact counts against this snapshot;
3. only after that audit, create E0–E7 and B2A–B8 and record their actual issue numbers;
4. add V2 metadata and append cutover notes in bounded batches without rewriting original bodies or acceptance criteria;
5. run subsequent reconciliation only from trusted default-branch code through the protected Actions path;
6. audit links, evidence assignments and all post-successor counts;
7. commit repository-safe migration evidence and actual successor mappings;
8. perform any later anchor retirement only as a separate individually verified pass;
9. authorize PR 2A only after the entire governance audit is accepted.

Stop on any mismatch, unexpected state transition, option-ID churn, view warning, retention error, count drift or security-boundary violation.
