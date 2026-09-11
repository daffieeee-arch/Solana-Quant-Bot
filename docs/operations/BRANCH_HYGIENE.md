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

Delete only refs whose **exact name and current commit SHA** match the head of
a same-repository PR merged into `main`. A matching old name is not sufficient:
branches can be reused, advanced or shared with a fork's PR. Inventory uses all
PR pages, deduplicates branch names and verifies the fixed repository identity
and both `origin` URLs. Unmatched, fork, moved and open-PR heads are left alone.

## Standard process

1. Ensure `main` is fetched and is the integration branch.
2. Dry-run:

```bash
node scripts/cleanup-merged-pr-branches.mjs
```

3. Review the JSON plan, including the exact head SHA. Confirm every candidate
   maps to a same-repository PR merged into `main` and is not an open PR head.
4. Execute:

```bash
node scripts/cleanup-merged-pr-branches.mjs --execute --yes
```

5. The script rechecks the complete inventory immediately before each deletion,
   then uses an explicit expected-ref lease for an atomic compare-and-delete.
   This is not a force-update of branch history. A moved ref, newly open PR,
   mismatched origin or failed mutation stops the run without retry. It verifies
   ref absence after each successful deletion. No script can atomically lock
   GitHub PR creation together with Git refs; open-PR protection reflects the
   last pre-delete read, while the ref lease protects later commit changes.
6. Re-list remotes. Leftover branches without an exact merged PR head identity need a
   human decision (rename mismatch, abandoned draft, or still useful).
7. For active work, open one PR per branch and delete the head on merge
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

The focused offline tests inject Git/GitHub responses. They perform no real
branch deletion. This process itself is not permission for a historical cleanup;
execution still requires operator authorization for the reviewed target set.
