# GitHub Project #4 operations

> **Document status: ACTIVE.** This is the operations boundary for [Solana Quant Platform — Roadmap & Cockpit, Project #4](https://github.com/users/daffieeee-arch/projects/4). The V2 data/issue migration is specified separately in [`../PROJECT_V2_REBASE.md`](../PROJECT_V2_REBASE.md).

## Current post-G0 state

Project #4 exists and is the central delivery roadmap. It is reconciled by:

- `.github/workflows/roadmap-sync.yml`;
- `scripts/github-projects/sync.mjs`;
- `roadmap/project-config.json`;
- `tests/github-projects-sync.test.ts`.

PR #69 was squash-merged as `05c3d885943c2319d6317b43546daca4b5918074`. Its post-merge main CI passed. The main-push Roadmap Sync run was superseded by an issue-state event temporally associated with that merge; the exact initiating mechanism is unproven, and the successor ordinary reconciliation passed. The exact run identities and immutable pre-G0 export receipt are recorded in [`PROJECT_V2_G0_PREFLIGHT.md`](PROJECT_V2_G0_PREFLIGHT.md).

The G0 infrastructure and its bounded correctness repairs are active on trusted `main`: projection verification retries reads without replaying mutations, PR inheritance follows explicit ordered routing, and configured managed fields reconcile to exact SET/CLEAR state. Governance issue [#70](https://github.com/daffieeee-arch/Solana-Quant-Bot/issues/70) is closed `COMPLETED`, with Project Status `Done`, V2 Disposition `SUPERSEDED` and Evidence `Operationally Verified`. Pinned #56 and open #62 remain steady-state controls.

The canonical GitHub repository identity is `daffieeee-arch/Solana-Quant-Bot`. Roadmap Sync fails closed when `GITHUB_REPOSITORY` differs from `roadmap/project-config.json`. The former name `daffieeee-arch/solana-paper-scanner` is a routing alias for historical issue URLs only; it is not the Actions identity and does not replay Project migration.

The controlled content migration created E0–E7 as #72/#74–#80 and B2A–B8 as #81–#87, migrated every issue #27–#63, and individually closed the 26 reviewed ordinary anchors. No original body prefix changed, no historical PR reactivated and no Project schema/text/view identity drifted. The exact ledger is [`../../roadmap/project-v2-content-migration-ledger.json`](../../roadmap/project-v2-content-migration-ledger.json). PR #90 merged that evidence and removed only #63's temporary retention pin. Issue #63 was then closed individually as `not planned / superseded`, G0/#70 completed, and B2A/#81 subsequently completed with `Operationally Verified` evidence. B3/#82 retains `Done` / `SUPERSEDED` / `Fixture`. B4/#83 and B5/#84 are technically accepted on 2026-09-23 as `Done` / `SUPERSEDED` / `Engineering Validation` within their bounded contracts. B6/#85 is `In Progress` / `ACTIVE NOW` / `Unproven`: its separately approved first read-only mint inspector is a partial increment, not complete Observatory acceptance. B7/#86 is queued `Backlog` / `NEXT` / `Unproven`; this routing does not start B7, and B8 remains `LATER`. Research Ready remains false; no acquisition is authorized. See the [dated decision](../research/B4_B5_ENGINEERING_ACCEPTANCE_20260923.md); original migration evidence is unchanged.

The immutable preflight recorded issue #63 as `CLOSED` / `COMPLETED`, contrary to the accepted migration plan. Its close was temporally associated with PR #69's merge, but the exact initiating mechanism remains unproven. A separately authorized governance correction reopened it after the snapshot; current-schema Roadmap Sync run `33555734237` passed, and the snapshot and hashes remain unchanged. Issue #63 stayed open and pinned through the completed G0/E0 audit. PR #90 removed the pin; only after that gate passed was #63 closed individually as `not planned / superseded`. The immutable preflight remains unchanged and correctly differs from these documented later events.

## Post-mutation projection convergence

Roadmap Sync run `33560315744` created and fully populated the Project item for issue #72, but its immediate read-back did not yet expose that item as active. This is treated as bounded GitHub Projects read-after-write projection lag, not as permission to replay the successful mutation or resume the paused content migration.

Every changed Project surface follows one rule:

```text
mutate once
→ verify read-only
→ retry only read-only verification when the captured pre-state or a compatible partial projection remains visible
→ accept only exact convergence; otherwise fail closed
```

The fixed verification reads occur immediately and then after `500`, `1,000`, `2,000` and `4,000` milliseconds: five reads with at most `7,500` milliseconds total waiting per changed logical operation. The schedule has no jitter, environment override or unbounded fallback. Mutation errors and GraphQL/read errors are not retried because their commit state can be ambiguous.

Verifier outcomes are:

- `CONVERGED`: the exact expected identity and state are visible;
- `NOT_YET_CONVERGED`: only a newly added item is absent, a SET/create projection is still missing or partial, or the exact captured pre-mutation value remains visible. For a CLEAR, true target-field absence is `CONVERGED`; only the exact captured value or a compatible partial/null target projection is retryable;
- `HARD_DRIFT`: duplicate/unexpected content, wrong repository or option identity, unrelated field change, existing-item deletion, schema drift or any contradictory third state.

Each lifecycle, item-field, Project metadata, field-definition and view mutation is outside the retry loop and runs at most once per reconciliation attempt. The next mutation is gated on convergence of the prior logical operation. A later read may temporarily omit or partially expose an item created earlier in the same reconciliation; only mutation-returned item identities are eligible for that bounded lag treatment, while disappearance or identity drift of a pre-existing item remains a hard failure. Retry exhaustion reports a compact expected/final state and attempt count, then stops before later mutations. Logs contain only the operation type, bounded issue/PR/field/view identity, attempt and reason code; they never contain `PROJECT_TOKEN`, issue bodies or GraphQL payloads.

The final item counts and exact-state audit first use the most recent snapshot that passed an operation-level convergence gate (or the immutable pre-mutation planning snapshot when there were no item mutations). If that combined snapshot is compatibly stale for an earlier mutation, the same bounded read-only verifier obtains later snapshots; it never replays a mutation. This protects the aggregate count boundary against a non-monotonic Project projection while retaining hard failure for contradictory or unrelated drift.

## Pull-request metadata inheritance routing

Before routing or metadata parsing, the checked-in `contentIntake` policy admits
trusted authors and explicitly reviewed exact-content external submissions.
Public contributor status or a claimed approval in an issue body is not Project
authority. An unreviewed external item is excluded without changing any existing
Project fields or archive state. A trusted PR whose selected inheritance source
needs external review is also held unchanged, with a bounded intake diagnostic;
other trusted work continues. Missing or malformed trusted routes still fail
before mutations. See [public repository controls](GITHUB_PUBLIC_REPOSITORY_CONTROLS.md).

Project metadata inheritance uses an explicit, body-only PR route. The generic issue-link collector may still support reporting, but its numerically sorted references and incidental prose are never an inheritance source. The routing precedence is:

1. one exact `Roadmap:` line, with the first issue as primary owner and later issues as secondary context;
2. only when `Roadmap:` is absent, exact line-leading `Close`, `Closes`, `Fix`, `Fixes`, `Resolve` or `Resolves` directives;
3. only when both higher routes are absent, exact `Implements:` or `Tracks:` directives;
4. otherwise no inherited issue metadata.

References retain line and token order and are deduplicated by first occurrence. A reference is either `#N` or an issue URL on the canonical repository `daffieeee-arch/Solana-Quant-Bot` or a configured former-name alias such as `daffieeee-arch/solana-paper-scanner`. Every reference selected by the winning route must resolve to an existing Issue in this repository. A missing issue, PR-only number, foreign-repository URL, malformed explicit `Roadmap:` payload or multiple `Roadmap:` lines fails during full-state planning, before the first Project mutation. A Markdown heading such as `## Roadmap`, fenced example, HTML comment, title reference or prose mention is not a route.

The selected primary Issue supplies inherited roadmap dimensions. A valid PR-local `roadmap-meta` block then overrides only the fields it explicitly contains; it neither changes the route nor excuses an invalid explicit route. PR state continues to own Status and PR kind continues to own Work Type. The synchronizer does not guess an owner from arbitrary issue links. After complete derivation, omission of a configured managed field means true field absence; a direct PR value prevents that field from being cleared. This does not add an explicit-null metadata syntax.

The reviewed compatibility set is:

| PR | Route source | Ordered issue route | Primary owner | Lifecycle note |
|---|---|---|---|---|
| #64 | `DIRECT_ROADMAP` | #58, #63 | #58 | archived; remains archived |
| #65 | `DIRECT_ROADMAP` | #56, #63 | #56 | archived; remains archived |
| #67 | `DIRECT_ROADMAP` | #56, #63 | #56 | archived; remains archived |
| #68 | `DIRECT_ROADMAP` | #56, #63 | #56 | archived; remains archived |
| #69 | `DIRECT_ROADMAP` | #62, #63 | #62 | only active semantic owner correction |
| #71 | `DIRECT_ROADMAP` | #70 | #70 | unchanged |
| #73 | `DIRECT_ROADMAP` | #70 | #70 | unchanged |
| #88 | `DIRECT_ROADMAP` | #70 | #70 | G0 routing repair |
| #89 | `DIRECT_ROADMAP` | #70 | #70 | G0 exact-state repair |
| #90 | `DIRECT_ROADMAP` | #70 | #70 | G0 migration-evidence closeout |

Among the active Project PR items audited for the routing repair, #66 remained unrouted, #71/#73/#88/#89/#90 routed to #70, and only #69 changed owner: its explicit `Roadmap: #62 #63` can no longer be displaced by a later incidental #56 reference. Closing #70 does not change those explicit route identities. Routing changes never authorize unarchive or bypass item-retention eligibility.

## Managed item-field exact-state reconciliation

Roadmap Sync owns exactly the configured item fields, in `roadmap/project-config.json` order: Status, Priority, Area, Work Type, Phase, V2 Phase, V2 Disposition, Risk, Evidence, Effort, Start date and Target date. Once title inference, primary-owner inheritance, direct PR overrides, PR Status and PR Work Type have produced complete metadata, the synchronizer deterministically plans one action per managed field:

- `SET` when a derived value differs from the current value;
- `CLEAR` when no value is derived but a current managed value exists;
- `NO_OP` when the exact desired value is already visible or both states are absent.

Absence remains actual Project-field absence; no placeholder option is created. A clear uses GitHub's `clearProjectV2ItemFieldValue` mutation with the exact project, item and field IDs, requires the returned item identity to match, and executes at most once. The existing fixed `0 / 500 / 1,000 / 2,000 / 4,000 ms` read-only verifier then gates the next mutation. Exact absence converges; the captured pre-clear value or a compatible partial/null value is temporarily retryable; a third value, item/field identity change, duplicate, unrelated supported-field change or schema drift is a hard failure. Exhaustion fails closed without replay. Run output reports SET mutations as `fieldUpdates` and CLEAR mutations as `fieldClears`.

The final expected field state consists of captured supported unmanaged fields unchanged, every configured managed field with a derived value set exactly, and every configured managed field without a derived value absent. Unsupported field-value types are outside this audit. Title, Assignees, Labels, Milestone, Iteration and user-created fields not declared in the config are never mutated; supported unmanaged values are nevertheless captured and verified unchanged. Archived and skipped items receive no managed-field mutation. Manual values inside configured fields are non-authoritative and may be set or cleared on reconciliation.

## Security boundary

The user-owned Project requires a protected `PROJECT_TOKEN`; GitHub's repository-scoped `GITHUB_TOKEN` is insufficient. Never place the token in Git, `.env`, shell history, YAML, issue/PR text, logs, prompts or MCP queries.

The workflow receives `pull_request_target` events but must always execute trusted default-branch code. A pull-request branch may run offline validation but may not receive `PROJECT_TOKEN` or mutate the user Project. Mutation is allowed only through the reviewed workflow running code and config from the trusted default branch. Never:

- check out a PR head;
- run scripts or artifacts from a PR branch;
- interpolate untrusted title/body into a shell command;
- print request headers/token/API dumps.

Rotate/revoke the token if exposed. Workflow changes require independent security review.

## Pre-G0 main reconciliation semantics

At the captured baseline, the synchronizer is full-state/idempotent and runs on relevant issue/PR events, pushes to `main`, manual dispatch and scheduled repair. It finds/links the user Project, ensures configured fields/views, adds repository issues/PRs and maps hidden `roadmap-meta` plus GitHub state into Project values.

That baseline lifecycle behavior is not suitable for the V2 cockpit: it enumerates all open/closed issues and all open/closed/merged PRs, adds a missing historical item, and unconditionally unarchives any archived repository item. The G0 extension applies eligibility before both add and unarchive, preserves closed archived items, and reactivates only an item backed by an actual reopen transition.

Pre-G0 metadata keys are:

| Key | Field |
|---|---|
| `workflow` | Status |
| `priority` | Priority |
| `area` | Area |
| `type` | Work Type |
| `phase` | historical Phase |
| `risk` | Risk |
| `evidence` | Evidence |
| `effort` | Effort |
| `startDate` | Start date |
| `targetDate` | Target date |

Unknown keys and invalid options fail closed. GitHub issue/PR state still determines Done/Cancelled/In Review semantics. Manual edits to synchronized fields may be overwritten.

The pre-G0 synchronizer does not recognize `v2Phase` or `v2Disposition`. The G0 branch adds tested support, but do not put those keys into issue metadata until that extension is merged to the default branch and live migration is separately authorized.

## V2 migration boundary

The controlled migration sequence is:

1. **Complete:** verify merged PR #69 SHA/CI and export/hash all current Project-linked repository issues/PRs plus complete Project state read-only, retaining an exact #27–#63 subset.
2. **Complete:** create governance issue G0 as issue #70 using only metadata understood by current `main`.
3. **Complete:** merge PR #71 and verify the V2 fields, Evidence taxonomy, views, Project text and lifecycle-aware retention against the hashed baseline.
4. **Complete:** merge projection-convergence, PR-routing and managed-field-clearing repairs #73, #88 and #89; prove mutate-once/read-retry semantics and the #69/#62 inheritance boundary.
5. **Complete:** create E0–E7 as #72/#74–#80 and B2A–B8 as #81–#87 exactly once.
6. **Complete:** migrate all #27–#63 notes and metadata through trusted-main reconciliation while preserving the original body prefix of every issue.
7. **Complete:** audit mappings, links, option IDs, fields, states, views, Project text and retention, then close the 26 approved ordinary anchors individually as `not planned`.
8. **Complete:** PR #90 committed [`../../roadmap/project-v2-content-migration-ledger.json`](../../roadmap/project-v2-content-migration-ledger.json) and removed only #63's temporary pin while retaining #56's continuing-control pin.
9. **Complete:** the pin reconciliation passed; #63 was closed individually as `not planned / superseded`; #70 completed; B2A/#81 became `ACTIVE NOW`; B3/#82 became `NEXT`.
10. **Complete:** B2A/#81 removed the bounded retired paths and quarantined the retained legacy surface. Its removals are recorded by [`../../roadmap/b2a-invariant-salvage-manifest.json`](../../roadmap/b2a-invariant-salvage-manifest.json), and the pre-cleanup tree is archived at `v1-paper-platform-final`.
11. **Complete:** B3/#82 completed as `Done` / `SUPERSEDED` / `Fixture`; its accepted evidence remains structural and fixture-compatible only.
12. **Current, 2026-09-23 acceptance:** B4/#83 and B5/#84 are technically accepted on 2026-09-23 as `Done` / `SUPERSEDED` / `Engineering Validation` within their bounded contracts. B6/#85 is `In Progress` / `ACTIVE NOW` / `Unproven`: its separately approved first read-only mint inspector is a partial increment, not complete Observatory acceptance. B7/#86 is queued `Backlog` / `NEXT` / `Unproven`; this routing does not start B7, and B8 remains `LATER`. Research Ready remains false; no acquisition is authorized. Close #83 before #84 as completed through the existing synchronization; retain original issue criteria and verify Project read-back.

`V2 Disposition` values are `ACTIVE NOW`, `NEXT`, `LATER`, `SPLIT`, `SUPERSEDED` and `RETIRED`. Disposition is not completion. Issue #56 remains Done and pinned; issue #62 stays open. Issue #63's separately gated unpin and individual `not planned / superseded` close are complete. Do not bulk-retire anchors.

During migration G0 was the single concrete `ACTIVE NOW`. B4/B5 now join the accepted engineering milestones; the later explicit B6 authorization makes #85 `In Progress` / `ACTIVE NOW` / `Unproven` with a partial mint-inspector result. B7/#86 is queued `NEXT` without implementation approval; B8 remains `LATER`. Programs/epics do not authorize another delivery head. Completed #83/#84 use `SUPERSEDED` routing and `Engineering Validation`, with issue close reason `completed`; dataset classes and individual fixture evidence do not change.

The Evidence field is, in order: `Not Applicable`, `Unproven`, `Fixture`, `Operationally Verified`, `Engineering Validation`, `Research Candidate`, `Research Ready`, `Shadow`, `Paper Proven`, `Live Proven`. The six pre-existing semantically equal option IDs remain preserved. Programs/epics use `Not Applicable`; operational governance is `Unproven` until verified then `Operationally Verified`; #56 is `Operationally Verified`; research advances only through its named evidence gates.

The merged config replaced the Project short description and README with the approved E0 data-first text and a link to [`../HANDOFF_V2.md`](../HANDOFF_V2.md), explicitly naming edge discovery or falsification, Observatory before Workstation, Triton-only and no profitability assumption.

Retention keeps open V2 issues/open PRs active and binds the exact 30 linked merged pre-V2 PRs—#1–#26, #64, #65, #67 and #68—to the root snapshot hash, a separate candidate-set hash and a sorted config allowlist. The approved value is `closed_item_retention_days: 30`; there is no fallback or implicit default. Those 30 items remain archived, while the grace interval keeps newly closed anchors active temporarily. Archived closed items remain archived unless an actual GitHub reopen transition is evidenced; no item or GitHub history is deleted. Issue #56 remains the sole pinned continuing control. Issue #63 is unpinned and closed after its separate post-PR-#90 gate. Tests cover eligibility before both add and unarchive, audited-set/initial-membership drift, the configured grace period, closed-item non-reactivation, evidence-backed reopen behavior and deterministic planned counts.

GitHub exposes `ProjectV2Item.updatedAt`, not a dedicated archive timestamp. Reopen eligibility therefore requires the underlying item to be open and its latest `ReopenedEvent.createdAt` to be strictly newer than that Project timestamp. Ambiguity remains archived. This tested fail-closed limitation is accepted for G0 and must be audited during the first controlled migration; no reopen event is manufactured for testing.

The verified closeout individually marked 26 fully covered `SPLIT`/`SUPERSEDED` anchors `not planned`; standalone `LATER` requirements remain open. Issue #56 stays Done and pinned, issue #62 stays open, and issue #63 completed its separately reviewed pin-removal and individual-close sequence.

The exact #27–#63 ledger, successor catalog, V2 phases, filters, acceptance checks and rollback are in [`../PROJECT_V2_REBASE.md`](../PROJECT_V2_REBASE.md).

## Offline validation

Configuration validation uses no token/network:

```bash
node scripts/github-projects/sync.mjs --dry-run
npm test -- --run tests/github-projects-sync.test.ts
npm test -- --run tests/github-projects-lifecycle.test.ts
npm test -- --run tests/github-projects-routing.test.ts tests/github-projects-verification.test.ts tests/github-projects-managed-field-clearing.test.ts
```

Do not manually dispatch Roadmap Sync from a feature branch. Ordinary PR-event reconciliation under current trusted-main configuration is expected and must be allowed to finish. Do not overlap it with a manual reconciliation or place a token on a command line.

## Migration stop conditions

Stop without continuing batches on:

- issue/item count mismatch;
- missing/duplicate successor;
- unexpected issue state or close change;
- original acceptance-criteria loss;
- single-select option-ID/value drift;
- unsupported metadata key;
- view filter warning or unintended result set;
- workflow/security-boundary drift.

The issue #63 preflight discrepancy was resolved by an explicit, documented post-snapshot reopen—not by silently normalizing the snapshot. The immutable preflight continues to record its captured `CLOSED` / `COMPLETED` state. Live history separately proves the later reopen, pin retention through audit, PR #90 pin removal and individual `not planned / superseded` close. These later events never rewrite the snapshot.

Use the hashed preflight export and original issue bodies for explicit rollback. Never “repair” mismatches by overwriting history.

## Project truth boundary

Project #4 is the delivery cockpit, not evidence by itself. Tests, code, manifests, source hashes, issue/PR history and accepted review determine whether a field such as Research Ready or Paper Proven is justified. A Project status cannot turn fixture/synthetic data into authentic research evidence.
