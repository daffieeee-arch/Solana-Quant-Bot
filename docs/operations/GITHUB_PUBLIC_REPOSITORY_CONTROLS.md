# Public GitHub repository controls

> **Document status: ACTIVE.** Bounded repository governance under #62. These
> controls do not authorize acquisition, change B4/B5 delivery state, or establish
> research evidence. GitHub settings are external state and must be reread when
> their current value matters.

## Small-team operating profile

The repository is public; Project #4 remains private. Ordinary CI validates
untrusted contributions without secrets. Roadmap Sync runs trusted `main` with
its separate Project token. A public issue body is input, not maintainer approval.

The operator approved this profile on 2026-09-11:

| Setting | Intended value |
|---|---|
| Repository secret scanning | Enabled |
| Repository push protection | Enabled |
| Dependabot vulnerability alerts | Enabled |
| Dependabot automatic security-update PRs | Disabled; updates remain a separate reviewed decision |
| Automatically delete merged PR head branches | Enabled |
| `main`: pull request required | Enabled, including administrators |
| `main`: approving-review count | Zero; independent review remains part of delivery, without requiring a second GitHub account |
| `main`: required check | `tests-build-zero-cost`, from GitHub Actions app ID `15368` |
| `main`: require up-to-date branch | Disabled; PR CI and post-merge main CI remain required by workflow |
| `main`: resolve review conversations | Enabled |
| `main`: force pushes / deletion | Disabled |

The secret-scanning and push-protection settings were enabled and read back on
2026-09-11. The `main` protection object and enabled Dependabot-alert endpoint
were also read back successfully. This is a configuration observation, not a
claim that repository history is secret-free or that every credential format is
recognized. A credential exposed in a chat/tool log must still be revoked at its
issuer; enabling GitHub protection cannot revoke it or scan that private log.

No credentials, alert secret values, account billing settings, provider budgets,
or historical dataset files belong in this controls document.

## Public Roadmap intake

`roadmap/project-config.json` declares `contentIntake`. Only its explicitly
trusted GitHub authors and separately reviewed, exact-content external items
may supply Project delivery metadata or PR inheritance sources. The initial
trusted author is the repository owner, `daffieeee-arch`.

An external item's approval is bound to the exact reviewed content. A later
title/body edit does not silently retain metadata authority. GitHub author
association, contributor status, an item title, or a body claiming approval is
not a substitute for the checked-in decision.

Unreviewed or malformed external inputs must not stop reconciliation of trusted
work. They receive bounded diagnostics without printing their bodies. Existing
Project items excluded by intake remain untouched: no field rewriting,
archiving, unarchiving or deletion. Trusted metadata errors still stop before
mutations rather than silently weakening accepted contracts.

A trusted PR depending on an external source that needs review is held unchanged
with `INHERITANCE_REVIEW_REQUIRED`, rather than allowing that external source to
block unrelated trusted work. It never falls back to unreviewed metadata.

The intake boundary applies before metadata parsing and inheritance, not only
to event triggers: scheduled reconciliation also enumerates repository content.
The existing retention contract, historical migration ledgers, field identities,
and acquisition/evidence boundaries remain unchanged.

## Verification and changes

The [pre-merge read-only compatibility audit](../../roadmap/github-public-intake-baseline-20260911.json)
on 2026-09-11 compared the old and new planners against live repository content:
112 admitted items, 82 active and 30 archived, identical derived metadata and
lifecycle decisions, and zero required mutations. A query-only API adapter
prevented writes during this check. Adversarial external inputs are tested with
fake APIs, not by injecting public issues. Post-merge CI and ordinary trusted-main
Roadmap Sync are separate required checks.

- Review and test code/config changes through a bounded PR linked with
  `Roadmap: #62`; do not close the continuing governance epic.
- Compare the trusted existing Project projection before/after intake changes.
- Verify GitHub settings by reading the repository and main-protection APIs;
  never test protection by pushing an actual credential or force-pushing main.
- Repository secret protection does not require a new CI secret or a provider call.
- Do not add CodeQL, deployment, paid runners, automatic dependency merges or
  additional mandatory reviewer accounts as an implicit part of this profile.

Related contracts: [CI](../CI.md), [development workflow](../DEVELOPMENT_WORKFLOW.md),
[branch hygiene](BRANCH_HYGIENE.md), and [Project operations](GITHUB_PROJECTS_ROADMAP.md).
