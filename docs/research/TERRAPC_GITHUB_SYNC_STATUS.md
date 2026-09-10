# TerraPC ↔ GitHub sync status

> **Document status: ACTIVE.** Operational sync evidence for the WSL host
> `TerraPC` and repository `daffieeee-arch/Solana-Quant-Bot`. This is not acquisition authorization,
> Research Ready evidence, or a profitability claim.

## Verified identities

| Surface | Value |
|---|---|
| Host | `TerraPC` (WSL2 Ubuntu) |
| Repository path | `/home/dmesdary/code/Solana-Quant-Bot` |
| Canonical remote | `https://github.com/daffieeee-arch/Solana-Quant-Bot.git` |
| Historical GitHub rename alias | `daffieeee-arch/solana-paper-scanner` (routing only) |
| Canonical branch | `main` |
| Observed `origin/main` tip at sync | `79d1e9c828525d7eaf3efa5529d1d7b0952fb8cd` (`fix(of1): accept observed checksum source annotation (#107)`) |
| Local `main` tip at sync | identical to `origin/main` |
| Immutable V1 archive tag | `v1-paper-platform-final` |

## Working-tree disposition

Before this sync package, an agent checkout on
`cursor/of1-quant-research-320a` was **10 commits behind** `origin/main` and
diverged from the squash-merged OF1 memo commits. That branch must not be treated
as current product truth.

Untracked local build output `frontend/dist-monitor/` is ignored by Git and is
not product evidence. Source for the monitor lives on PR
[#108](https://github.com/daffieeee-arch/Solana-Quant-Bot/pull/108)
(`v2/b4-download-monitor`).

## Dataset evidence outside Git

The retained authentic metadata-only run remains on the WSL dataset root, not in
Git:

| Artifact | Value |
|---|---|
| Plan directory | `/home/dmesdary/solana-quant-data/run-plans/of1-e978-metadata-184e32eb-02/` |
| `metadata-run-result.json` SHA-256 | `8c77b3f6d7a1daec92835284bdc81ba4e18cb0a0166abcec5135252da5c81b21` |
| Result | four HTTP 200 publications; zero retries; no CAR payload |

That run's expired GO does **not** authorize a new metadata or payload call.

## Sync rule for agents

1. `git fetch origin main`
2. Start work from `origin/main` (or a fresh `cursor/...-43c5` branch from it)
3. Do not treat stale feature-branch tips as `main`
4. Do not commit `frontend/dist-monitor/` unless a reviewed release process asks for hashed build evidence
