# GitHub Projects Roadmap Operations

## Purpose

The repository contains an idempotent Projects-v2 reconciler. It creates or finds the user-owned project **Solana Quant Platform — Roadmap & Cockpit**, links it to this repository, creates/reconciles the configured fields and views, adds repository issues and PRs, and synchronizes field values from issue/PR state and `roadmap-meta`.

The implementation consists of:

- `.github/workflows/roadmap-sync.yml`
- `scripts/github-projects/sync.mjs`
- `roadmap/project-config.json`
- `tests/github-projects-sync.test.ts`

## One-time activation

The repository automation cannot create its own GitHub secret. Complete these steps once after the PR is merged:

1. Create a **personal access token (classic)** owned by `daffieeee-arch`.
2. Grant scopes `project` and `repo`. The project is user-owned and the tracked repository is private.
3. In repository settings, create the Actions secret `PROJECT_TOKEN` and store the token there.
4. Open **Actions → Roadmap sync → Run workflow** on `main`.
5. Read the final `roadmap_sync_complete` line. It contains the user-project number and URL.

Do not store the token in `.env`, YAML, issue text, logs, an AI prompt or the repository.

## Security boundary

`GITHUB_TOKEN` is intentionally not used for Projects because repository-scoped workflow tokens do not have access to user Projects.

The workflow observes PR lifecycle via `pull_request_target`, but it always checks out the trusted repository default branch. It must never be changed to:

- check out a PR head SHA;
- run scripts from a PR branch;
- download and execute PR-produced artifacts;
- interpolate untrusted PR body/title text into a shell command.

Those changes would expose the high-privilege `PROJECT_TOKEN` to unreviewed code. Any future workflow modification must preserve this boundary.

Rotate or revoke the token if it is ever exposed. The script never prints request headers, token contents or complete API responses.

## Synchronization cadence

A full reconciliation runs on:

- issue create/edit/reopen/close and planning metadata changes;
- PR open/edit/synchronize/reopen/ready/draft/close;
- pushes to `main`;
- manual dispatch;
- a daily repair run at 04:17 UTC.

The scheduled run repairs missed events and Project drift. The synchronizer is deliberately full-state and idempotent rather than event-payload dependent.

## Source-of-truth metadata

A roadmap issue can include one hidden metadata block:

```html
<!-- roadmap-meta
{"schemaVersion":1,"type":"Feature","area":"Frontend","priority":"P1","phase":"3 Core Workspaces","risk":"Medium","evidence":"Unproven","workflow":"Ready","effort":8,"startDate":"2026-09-01","targetDate":"2026-09-30"}
-->
```

Supported keys:

| Key | Project field | Notes |
|---|---|---|
| `workflow` | Status | Open issues only; GitHub state overrides closed/PR status |
| `priority` | Priority | P0–P3 configured options |
| `area` | Area | Must match a configured option |
| `type` | Type | PRs are always `Pull Request` |
| `phase` | Phase | Program phase 0–7 |
| `risk` | Risk | Critical, High, Medium or Low |
| `evidence` | Evidence | Unproven through Live Proven |
| `effort` | Effort | Integer 0–100 |
| `startDate` | Start date | `YYYY-MM-DD` |
| `targetDate` | Target date | `YYYY-MM-DD` |

Unknown keys, duplicate metadata blocks, invalid dates and values not present in Project configuration fail the reconciliation. This makes metadata typos visible instead of silently creating inconsistent fields.

## PR inheritance

A PR inherits Area, Priority, Phase, Risk and Evidence from the first linked issue when its body/title contains a delivery line such as:

```markdown
Closes #35
Roadmap: #58
```

Direct PR metadata overrides inherited values. PR Type and Status are always derived:

| GitHub PR state | Project Status |
|---|---|
| Draft/open | In Progress |
| Ready/open | In Review |
| Merged | Done |
| Closed unmerged | Cancelled |

Issue state mapping:

| GitHub issue state | Project Status |
|---|---|
| Open | `workflow` metadata, default Backlog |
| Closed/completed | Done |
| Closed/not planned | Cancelled |

The sync is intentionally repository-to-Project. Manual changes to synchronized fields may be overwritten. Change the issue metadata or PR state instead.

## Fields and views

Configuration is declared in `roadmap/project-config.json`. The baseline creates:

- Status, Priority, Area, Type, Phase, Risk and Evidence single-select fields;
- Effort, Start date and Target date;
- Executive Roadmap, Delivery Board, P0 Blockers, Frontend Cockpit, Data & Research, Live Shadow & Execution, Recently Updated and Done & Cancelled views.

The script preserves matching single-select option IDs, including mapping a new project's default `Todo` Status to `Backlog`, so existing item values are not needlessly destroyed.

## Local validation

No token or network is used for configuration validation:

```bash
node scripts/github-projects/sync.mjs --dry-run
npm test -- --run tests/github-projects-sync.test.ts
```

A live local run is possible only with a protected shell environment:

```bash
PROJECT_TOKEN='<protected value>' \
GITHUB_REPOSITORY='daffieeee-arch/solana-paper-scanner' \
node scripts/github-projects/sync.mjs
```

Do not place the token in shell history. Prefer the Actions secret path.

## Troubleshooting

### `PROJECT_TOKEN is required`

The Actions secret is missing or not available to the workflow. Verify the exact secret name and rerun manually.

### `PROJECT_TOKEN must belong to daffieeee-arch`

The token belongs to another account. A user-owned Project must be managed by its owner token in this configuration.

### `metadata value ... is not a configured ... option`

The issue metadata and `roadmap/project-config.json` disagree. Correct the typo or review and add the new controlled option.

### View update warning

Item synchronization continues when GitHub changes or rejects a view filter grammar. The named view remains present. Review the warning and adjust only the declarative filter string.

### Duplicate project title

The synchronizer refuses to guess between multiple exact-title projects. Rename or archive the unintended duplicate and rerun.

## Project bootstrap limitation

Before the one-time secret and workflow dispatch, the repository issues and epics exist but the Projects-v2 board itself is not yet instantiated. This is an intentional credential boundary, not background work.
