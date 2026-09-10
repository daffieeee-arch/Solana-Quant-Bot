# Branch hygiene process

> **Document status: ACTIVE.** Operational process for keeping
> `daffieeee-arch/Solana-Quant-Bot` branch inventory small after the public
> cutover. This is not acquisition authorization.

## Why this exists

Squash-merges leave remote feature branches that `git branch --merged` cannot
see as ancestors of `main`. Those branches clutter the repo, confuse agents, and
waste CI/notification attention. After making the repository public, only live
delivery heads should remain remote.

## Protection rules

Never delete:

- `main`
- any branch that is the head of an **open** pull request
- annotated tags (including `v1-paper-platform-final`)

Prefer deleting only branches whose **exact name** is the `headRefName` of a
**merged** pull request.

## Standard process

1. Ensure `main` is fetched and is the integration branch.
2. Dry-run:

```bash
node scripts/cleanup-merged-pr-branches.mjs
```

3. Review the JSON plan. Confirm every candidate maps to a merged PR and is not
   an open PR head.
4. Execute:

```bash
node scripts/cleanup-merged-pr-branches.mjs --execute --yes
```

5. Re-list remotes. Leftover branches without a merged PR head name need a
   human decision (rename mismatch, abandoned draft, or still useful).
6. For active work, open one PR per branch and delete the head on merge
   (repository setting: automatically delete head branches).

## Branch naming for new work

- Integration: `main` only
- Agent/task branches: `cursor/<short-description>-43c5` (or current agent suffix)
- Delivery branches: `v2/<delivery>-...`
- Do not resurrect retired Phase 8C/8D/TrueNAS/Hermes branch names as active targets

## After a merge

1. Confirm GitHub deleted the head branch (or run the cleanup script).
2. Locally: `git fetch --prune` and delete the local branch.
3. Start the next task from a fresh `origin/main` checkout.

## Explicit non-goals

- Force-pushing `main`
- Rewriting published history to remove old branch commits
- Deleting open PR heads
- Treating branch deletion as evidence that work is Research Ready
