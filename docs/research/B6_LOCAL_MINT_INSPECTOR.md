# B6 — local read-only mint inspector

> **Document status: ACTIVE partial B6 increment.** Originally authorized for
> local development on 2026-09-23, then separately authorized for protected
> integration after PR #134. #85 remains open / In Progress / ACTIVE NOW /
> Unproven. This is not complete B6 acceptance or Research Ready.

## Scope and data contract

This independent React/TypeScript entry point displays the existing
[manifest-bound mint timeline](B5_MINT_TIMELINE.md). It does not extend the
frozen legacy dashboard. The proposal is recorded in
`docs/research/B4_B5_ENGINEERING_ACCEPTANCE_20260923.md` at reviewed commit
`f8792bbe83e0f0fd4a32612e5765250dde398b35` on PR #134. That decision was delivered by PR #134 at
`be077022bc163218335d1cc112c03068694819a4`; B4/#83 and B5/#84 were closed in
order with verified Engineering Validation. The original reviewed local
inspector at `74c527d6d502360157b842e330de0502c18c823c` remains preserved in its
original worktree; integration changes only documentation and routing.

The selected mint is `4aG2APjGceKMLjyhFLjAEYaqu4w2meifWb2hAy4Wpump`, within
`[422669516,422669535)`, collection SHA256
`39b4856b9f5b942ef263d7bda142968b5f645a0706f07a9353c59b55babba1ab`.
The existing selection has 15 packages, five admitted facts (two buys, three
sells), 58 balance observations and three failed packages without facts.
Six packages / two facts / 24 balances are pilot `RESEARCH_SAMPLING`; nine
packages / three facts / 34 balances are post-hoc `ENGINEERING_VALIDATION_ONLY`
context. The mint selection itself remains post-hoc descriptive.

The browser preserves each source/parent package identity, recorded chain
order, instruction/CPI arrays, string integers and nulls. It renders existing
trade facts, event quantities, balances, transaction-wide deltas, diagnostics
and evidence fields; it does not decode wire data, derive accounts, compute
prices or admit facts. Mint references and unknown diagnostic attribution are
not trades. Creation, completion, migration and full lifetime remain unproven;
historical program activation and actual CPI privileges are not inferred.
All 22 collection facts and seven Mayhem rejections remain unchanged.

## Registered, bounded access

`src/mint-inspector/server.ts` reuses the existing immutable bounded-file
reader and consumes one `OF1_MINT_INSPECTOR_REGISTRY_1` document. Its `inputs`
contains exactly `timeline`, `lifecycle`, `collection`, `plan`; each supplies a
relative `path` under the explicit data root and the original file `sha256`.
No environment discovery or HTTP-supplied filename is used. Symlinks, ancestor
symlinks, traversal, nonregular or oversized files fail before listening.

The adapter checks the four byte hashes, timeline/lifecycle/collection/plan
bindings, unique package and fact identities, atomicity, source/receipt
consistency, chain order, counts, classes and failure exclusion. It reuses the
sealed report's decoder/query evidence; it does **not** reopen Raw/Parquet or
claim a new cryptographic replay of their records. The runtime display
contract rejects JSON numbers in timeline/lifecycle rather than rounding
integers. These input formats already encode numeric fields as strings.

All inputs and static assets are loaded once before listening. Exact GET
routes are `/`, allowlisted `/assets/*`, `/api/inspection`, and the four
`/evidence/{name}.json` byte-identical input exports. Every other path/query
fails; other methods receive 405. Host/Origin and cross-site checks, no CORS,
CSP, connection/header/body-time limits and a hard loopback bind prevent the
adapter from becoming a general file server or mutation API. There are no
provider clients, writes, wallet or scanner routes. Local OS users remain
within the existing private VPS trust boundary; this is not a multi-user
authentication service.

`READY` means the registered snapshot is available, not research eligibility.
Other state fixtures (`STALE`, `GAP`, `REPLAYING`, `UNAVAILABLE`, `UNPROVEN`,
`QUARANTINED`) do not invent authentic observations. Unknowns and coverage
limits remain visible. No polling or automatic reload is performed.

## Reproduce and open privately

Original local artifacts are under
`/home/chupa/Solana-project/data-old-faithful-one/governance/b6-mint-inspector-local-20260923/`.
`registry.json` pins the preserved timeline, lifecycle report, collection and
plan. That sealed local dossier retains the original review, tests and authentic
browser screenshots. New delivery evidence and private build output belong to
`/home/chupa/Solana-project/data-old-faithful-one/governance/pr134-b6-integration-20260923/`.
Its `RESULTAAT.md` records final heads/checks and evidence reuse. Generated assets
and records never belong in Git.

Use the approved local Node/dependencies; first run the read-only doctor.
No install or upgrade is implied. Build under the existing resource scope:

```bash
cd /home/chupa/Solana-project/Solana-bot/.worktrees/b6-mint-inspector-integration-20260923
SOLANA_QUANT_DATA_ROOT=/home/chupa/Solana-project/data-old-faithful-one \
  node scripts/with-toolchain.mjs -- npm run doctor
systemd-run --user --scope --unit=solana-b6-mint-build \
  -p CPUQuota=200% -p MemoryHigh=5G -p MemoryMax=6G -p TasksMax=256 \
  -p CPUWeight=25 nice -n 10 ionice -c 3 timeout --kill-after=15s 180s \
  node scripts/with-toolchain.mjs -- \
  /home/chupa/Solana-project/data-old-faithful-one/migrations/vps-integration-20260920/launcher \
  /home/chupa/Solana-project/data-old-faithful-one/migrations/vps-integration-20260920/network-deny.bpf \
  npm run build:mint-inspector
```

For this delivered local build, start the already generated private assets
with the existing outbound-denying seccomp launcher. Run in the foreground;
port 7042 must be free. Do not stop an unrelated process to claim the port.

```bash
systemd-run --user --scope --unit=solana-b6-mint-view \
  -p CPUQuota=200% -p MemoryHigh=5G -p MemoryMax=6G -p TasksMax=256 \
  -p CPUWeight=25 nice -n 10 ionice -c 3 \
  node scripts/with-toolchain.mjs -- \
  /home/chupa/Solana-project/data-old-faithful-one/migrations/vps-integration-20260920/launcher \
  /home/chupa/Solana-project/data-old-faithful-one/governance/b6-mint-inspector-local-20260923/outbound-deny.bpf \
  node dist/mint-inspector/main.js \
  /home/chupa/Solana-project/data-old-faithful-one \
  governance/b6-mint-inspector-local-20260923/registry.json \
  /home/chupa/Solana-project/data-old-faithful-one/governance/pr134-b6-integration-20260923/site \
  7042
```

After rebuilding without `--outDir`, use the worktree's absolute
`frontend/dist-inspector` directory instead of the evidence `site` directory.
Do not overwrite a sealed evidence build.

On your own computer, forward loopback only, substituting your known VPS host:

```bash
ssh -N -L 127.0.0.1:7042:127.0.0.1:7042 chupa@<VPS-host>
```

Open `http://127.0.0.1:7042/`. Tab into the package region; arrows select
previous/next and Home/End select first/last. Tab to a detail heading and use
Enter/Space to expand it. Every package remains available, including failures.

Stop with Ctrl-C in the server terminal, or target only this scope:

```bash
systemctl --user stop solana-b6-mint-view.scope
```

The adapter exits automatically after 900 seconds. Close the SSH forward with
Ctrl-C separately. No permanent service, port publication or reverse proxy is
created. The old report service and Hyperliquid are not touched.

## Validation and later integration

Focused contract regressions cover atomicity, duplicate/order rejection,
failed-package fact exclusion, integer/null preservation, class/binding/count
checks and unknown/balance-only context. Adapter tests cover immutable exports,
mutation/path/host/origin denial, hashes, symlinks and limits. UI tests cover
keyboard/button navigation, safe text, unknowns, provenance and state display.
These fixture tests complement, rather than replace, comparison with the
registered authentic package/fact identities and browser inspection.

`npm run build:mint-inspector` typechecks both adapter and UI and builds the
standalone page. The ordinary `build` includes it for eventual required CI;
the exact offline command allowlist includes this build and start pair, with
its prior legacy/network exclusions preserved. No workflow is changed.
No full dataset replay or full local
suite is required for this bounded increment.

The original local review and authentic browser/data evidence are retained.
Only integration/documentation changes receive focused independent review;
unchanged runtime, test and build files must match `74c527d` byte for byte.
Required PR CI, CodeQL, dependency review and roadmap-sync run on the final
integration head, followed by protected squash and main verification. No
duplicate CI steps are added; no dataset replay follows from integration.

Current B6 routing is In Progress / ACTIVE NOW / Unproven. This increment covers
one registered mint snapshot, its package inspection, provenance, state fixtures
and bounded API. It does not deliver the full #85 ingestion-quality/replay
workspaces, supported lifecycle-series/participant views or broader acceptance
evidence. These are remaining scope, not permission to invent missing values or
start another task. B7 is queued NEXT only. Keep #85 open and Research Ready false.
