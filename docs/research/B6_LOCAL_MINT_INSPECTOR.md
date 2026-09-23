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
original worktree; PR #135 integrated it with documentation/routing changes only,
at `eef008317f178e14c5aad93e92f9ed8d7fdf7aa4`. The separate pilot-quality
increment below adds a bounded report view; it does not complete #85.

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

## Pilot data quality — bounded second increment

The keyboard-accessible workspace buttons distinguish the existing mint
fragment (15 packages / 5 facts / 58 balances, including pilot and context) from
the complete three-slot pilot `[422669516,422669519)` across all mints:
3 blocks, 3,224 atomic packages, 223 failed transactions and 7 existing facts.
The pilot retains `RESEARCH_SAMPLING`; no context slot is reclassified.

Bronze decoding, transaction success and Silver admission have separate panels.
The recorded zero Bronze quarantines/unsupported packages does not imply full
Pump instruction coverage. This manifest does not publish a complete rejected
Pump instruction count: that field is UNAVAILABLE, never a package/fact
subtraction. Mayhem remains rejected. Missing optional coverage is unavailable,
not zero. Creation/completion/migration/full lifetime, historical activation,
account state and actual CPI privileges remain unproven; Research Ready is false.

An optional registry `pilot` group contains exactly `manifest`, `decoder0`,
`decoder1`, `decoder2`, using the same relative-path/SHA256 contract, capped at
256 KiB per input and response. It binds the reviewed #132
`report-f23b254/manifest.json` and the three decoder execution receipts named by
that manifest. The earlier pre-review `report-final` is not this input.
The adapter copies only existing string decoder identities; numeric domain
fields in execution receipts are not projected. Receipt exports retain their
original bytes. An absent registration leaves pilot quality UNAVAILABLE;
an invalid registration prevents publication of the snapshot.

With that registration the same allowlist adds `/api/pilot-quality` and
`/evidence/pilot-{manifest,decoder0,decoder1,decoder2}.json`. No path parameter,
provider or write route is added. The shared display contract checks pilot range,
class, collection/plan hashes, source-scoped receipts, decoder receipt hashes,
batch/writer identity, count consistency and logical row counts. It preserves
string integers/nulls and reuses existing offline report evidence; it does not
reopen source Raw or Parquet files. Both views retain bounded reads and aborts;
switching back to the mint keeps its package position. There is no polling.

Expandable provenance exposes source/receipt/sample bindings, all batch decoder
identities, native Rust writer/settings, physical and logical hashes and report
code identities. Hash-pinning the existing reports is not a fresh dataset replay.
The authentic input bindings and browser/validation evidence are preserved in
the separate OF1 dossier `governance/b6-quality-delivery-20260923/`.

## Reproduce and open privately

Original local artifacts are under
`/home/chupa/Solana-project/data-old-faithful-one/governance/b6-mint-inspector-local-20260923/`.
`registry.json` pins the preserved timeline, lifecycle report, collection and
plan. That sealed local dossier retains the original review, tests and authentic
browser screenshots. New delivery evidence and private build output belong to
`/home/chupa/Solana-project/data-old-faithful-one/governance/pr134-b6-integration-20260923/`.
Its `RESULTAAT.md` retains historical integration evidence. Current pilot-quality
delivery uses `governance/b6-quality-delivery-20260923/`, with
`registry-quality.json` and `site-quality/`; older sealed builds remain intact.
Generated assets and records never belong in Git.

Use the approved local Node/dependencies; first run the read-only doctor.
No install or upgrade is implied. Build under the existing resource scope:

```bash
cd /home/chupa/Solana-project/Solana-bot/.worktrees/b6-pilot-quality-20260923
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
  governance/b6-quality-delivery-20260923/registry-quality.json \
  /home/chupa/Solana-project/data-old-faithful-one/governance/b6-quality-delivery-20260923/site-quality \
  7042
```

After rebuilding without `--outDir`, use the worktree's absolute
`frontend/dist-inspector` directory instead of the evidence `site` directory.
Do not overwrite a sealed evidence build.

On your own computer, forward loopback only, substituting your known VPS host:

```bash
ssh -N -L 127.0.0.1:7042:127.0.0.1:7042 chupa@<VPS-host>
```

Open `http://127.0.0.1:7042/`. Choose **Datakwaliteit pilot** with a click or
Tab/Enter; **Mintdossier** returns to the package view. Tab into the package region; arrows select
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
PR #135 reused that review with a focused review of integration/documentation;
its runtime, test and build files matched `74c527d` byte for byte. The separate
pilot-quality change receives one independent review and, only for findings, one
focused fix/recheck. Focused contract/adapter/UI tests, build/typechecks and one
authentic browser session validate the increment. Required PR CI, CodeQL and
dependency review run on the final head,
followed by protected squash and technical main verification. Roadmap synchronization
is separate administration under the 2026-09-23 delivery amendment; its known
external visibility failure does not erase this inspector's technical evidence. No
duplicate CI steps are added; no dataset replay follows from integration.

Current B6 routing is In Progress / ACTIVE NOW / Unproven. This increment covers
one registered mint snapshot, its package inspection, provenance, state fixtures,
a bounded three-slot pilot quality snapshot, chronological presentation replay and bounded API. It does not deliver the full #85 ingestion-quality/replay
workspaces, supported lifecycle-series/participant views or broader acceptance
evidence. These are remaining scope, not permission to invent missing values or
start another task. B7 is queued NEXT only. Keep #85 open and Research Ready false.


## Chronological presentation replay — 2026-09-23

The mint workspace presents every existing package in validated chain order in
one compact timeline. Selection opens the same atomic package details and source
bindings. Play advances by one complete package every two seconds; pause,
previous/next, timeline/trade selection, Home/End and restart cancel the pending
step. Restart returns to the first package paused. Switching workspace pauses
without losing the selection; unmount clears the timer. Playback stops at the
last package. The tempo is presentation only: no historical latency, information
availability, actionable time or execution opportunity is modeled.

The trade table shows all five existing Silver facts (two buys, three sells),
in package order, with exact original event raw u64 quantities and a parent
package link. It displays recorded token decimals from balance context without
changing `base_decimals`; quote identity `UNKNOWN` and quote decimals `null`
remain unknown. No amount conversion, new price, cumulative quantity, return,
fill or account interpretation is introduced. Full-dossier totals remain fixed
at 15 packages / 5 facts / 58 balance observations / 3 failed packages; they do
not count replay progress. The separate pilot-quality workspace remains the
three-slot selection with 3 blocks / 3,224 packages / 223 failures / 7 facts.

Focused tests cover single-step/end behavior, pause/resume, restart, manual and
keyboard selection, workspace change, StrictMode/unmount cleanup, multiple facts
per atomic package, exact large integers and unknown units. Authentic browser
validation and screenshots use the same eight registered inputs, with output in
`governance/b6-mint-replay-20260923/` below the private OF1 root. No source dataset
replay is required. Build/start commands above apply with the new worktree,
`registry.json` and `site` in that evidence directory. Stop the foreground
adapter with Ctrl-C; it retains the existing automatic lifetime bound.

This is another partial #85 increment, not a full replay engine or completed B6.
Missing lifecycle phases and Research Ready=false remain visible. Technical
checks and Projects synchronization retain their separate delivery statuses.


## Event-reported reserve points and addresses — 2026-09-23

The mint workspace shows the five unchanged Silver facts as two discrete point
series: `real_token_reserves_raw_u64` (circle) and
`virtual_token_reserves_raw_u64` (diamond). They share a labelled raw-u64 axis;
package positions are ordinal, not time or latency. Points are slightly offset
within their atomic package for readability, not to invent chain positions.
There are no connecting series lines, interpolation, forward-fill or values for
packages without an observation. These are event-reported reserves, never
verified historical account-write state, executable liquidity or completion.

The adjacent exact table preserves every integer string, slot/transaction,
pilot/context class and full fact hash. A bounded BigInt ratio supplies screen
coordinates only. Missing fields, explicit null, literal `0` and invalid
u64 syntax/type/overflow remain distinct; missing/invalid values have no point.
No quote-reserve conversion, price, SOL amount, return or completion percentage
is calculated. Existing quote identities/decimals elsewhere remain UNKNOWN/null.

The address table shows `user_address`, `creator_address_reported` and
`fee_recipient` separately for every fact, grouped under their complete atomic
package, with unchanged text and no external links/enrichment. Field names are
reported roles, not proof of signer rights, economic ownership, unique persons
or wallet clusters. Missing/null/non-text/empty address fields remain explicit;
rendering a nonempty reported string is not address validation.

Both views cover the whole dossier and mark the selected package. Point
click/Enter/Space and table selection reuse the existing selection action,
pause playback and open the same full package with source bindings. Multiple
facts in one package retain distinct hashes/rows/points while selecting their
whole parent. Existing timeline, replay and separate pilot-quality view remain.
The API, registries, all eight input hashes and five fact hashes are unchanged.

Focused value/selection/grouping regressions, existing UI tests, build/typechecks
and one authentic sandboxed browser walkthrough provide this increment's
evidence under OF1 `governance/b6-reserves-addresses-20260923/`. Its `OPENEN.md`,
`registry.json`, `site/` and start script describe the current private build;
previous sealed builds stay intact. No dataset replay or acquisition occurs.

Relative to original #85, this supplies bounded supported reserve series,
reported-address participation context, adjacent fact provenance and keyboard
selection with evidence classes and explicit missing states. It infers no participant
identity and does not claim complete Observatory acceptance. Software still
not delivered includes compact acquisition-byte/runtime presentation and
volume/cumulative-flow/curve/phase presentations where a suitable authorized
source contract exists, plus full-workspace acceptance/accessibility evidence.
Unavailable historical creation/completion/migration, full lifespan, activation,
actual CPI rights and independently evidenced price/account-state semantics are
source-evidence limits, not values the UI may compute or invent. #85 stays open /
Unproven; Research Ready=false. No new acceptance criteria or follow-on work.
