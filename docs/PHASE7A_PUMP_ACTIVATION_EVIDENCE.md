# Phase 7A — Historical Pump Activation Evidence

## Status

This document persists the bounded, strictly read-only Phase-7A investigation for epoch 978 and candidate range `[422506000, 422507000)`.

The result is fail-closed:

- `status: CANDIDATE_UNAPPROVED`
- `activationVerdict: HOLD_UNPROVEN_ACTIVATION`
- `approved: false`
- `researchReady: false`
- `pilotEligible: false`
- `promotionCount: 0`
- no registry mutation
- no accepted real-data Silver decode
- no bandwidth preflight, network shaping, payload retrieval, Pilot A, Pilot B, or runtime authorization

The machine-readable contract is [`research/PUMP_ACTIVATION_EVIDENCE_EPOCH_978.json`](research/PUMP_ACTIVATION_EVIDENCE_EPOCH_978.json). The external scratch chain is bound by [`research/PUMP_ACTIVATION_SCRATCH_MANIFEST.json`](research/PUMP_ACTIVATION_SCRATCH_MANIFEST.json); no raw scratch response is committed.

## Candidate time boundary

[claim:ACT-CANDIDATE-TIME]

Two independent RPC methods agree:

- slot `422506000` → Unix `1779886412` → `2026-05-27T12:53:32Z`;
- slot `422506999` → Unix `1779886805` → `2026-05-27T13:00:05Z`.

Blocktime is context only and never used as unique ledger ordering.

## Pump ProgramData

[claim:ACT-PROGRAMDATA]

The raw executable Program account is owned by `BPFLoaderUpgradeab1e11111111111111111111111` and encodes ProgramData account:

`B5MvUwXdiW1NMM6QFFD3ssPKBujD4zMohncbM73Z2BQu`

The address was parsed using the official loader state layout; it was not guessed. The current ProgramData header reports last deploy/upgrade slot `433095571`, but current program bytes are not treated as historical candidate bytes.

## Upgrade boundaries

[claim:ACT-UPGRADE-BOUNDARY]

- Deployment boundary reached: `DeployWithMaxDataLen` at slot `241767269`.
- Last decoded `Upgrade` before the candidate: slot `422112651`.
- First decoded `Upgrade` after the candidate: slot `433095571`.
- Closed ProgramData signature pagination contains no ProgramData signature inside the candidate range or between those decoded boundaries.

One ProgramData revision is therefore directly boundary-corroborated across the candidate range. This does not prove its exact ELF or its source/IDL relationship.

## Global history completeness

[claim:ACT-HISTORY-INCOMPLETE]

ProgramData signature pagination closed with 96 signatures, but only 88 transaction details were retrieved within the bounded retry policy. The eight unavailable transaction details are old and outside the candidate boundary, yet the strict all-transactions completeness requirement still yields:

`INCOMPLETE_UPGRADE_HISTORY`

## Binary/source mapping

[claim:ACT-BINARY-UNPROVEN]

The candidate upgrade buffer has 1,457 signatures. Reconstructing every write transaction would have been bulk retrieval and was not authorized. The official Pump docs repository exposed no release or tag artifact.

Therefore:

- candidate historical ELF SHA-256: unavailable;
- source-to-binary mapping: `UNPROVEN`;
- IDL-to-binary mapping: `UNPROVEN`;
- classification: `BINARY_TO_SOURCE_MAPPING_UNPROVEN`.

## Official Pump IDL history

[claim:ACT-OFFICIAL-IDL]

The complete relevant `idl/pump.json` path history contains 15 commits. The last revision before the candidate blocktime is commit:

`3c6721a67c0b206b39130b454c8ba22a83ce972e`

All ten target entries are present in that exact revision. Git and IDL timestamps prove only official structural documentation; they do not prove activation.

## On-chain Anchor IDL

[claim:ACT-ONCHAIN-IDL]

Official Anchor derivation yields legacy IDL account:

`AYgC53tU5BbP2NAnv5nConJxAdpQZctvmZK88pu69xRs`

The account history closed with 48 signatures and 48 raw transaction responses. Its last relevant write is slot `419747849`, before the candidate, and there is no later IDL-account signature. All ten relevant on-chain instruction/event structures exactly match the official pinned Pump IDL revision.

This is strong structure corroboration, but the IDL-to-active-ELF relationship remains unproven.

## Candidate-range observations

[claim:ACT-RANGE-OBSERVATIONS]

Old Faithful documents signature indexes, but the epoch-978 indexes are 4.19–35.42 GB and no small server-side candidate-signature query was found. No local immutable candidate-signature fixture exists.

No index body, CAR, range, block payload, candidate transaction, or slot payload was fetched. Status:

`TARGETED_RANGE_OBSERVATION_NOT_AVAILABLE_WITHIN_BOUNDS`

## Network and scratch bounds

[claim:ACT-NETWORK-BUDGET]

Phase 7A used:

- 86 of 500 requests;
- 22,117,677 of 104,857,600 response-body bytes;
- zero CAR payload bytes;
- zero CAR range bytes;
- zero index payload bytes;
- zero candidate-range transaction responses;
- zero paid-provider and Triton requests.

[claim:ACT-SCRATCH-BINDING]

The retained external scratch contains 201 files. Its inventory bindings are:

- `scratch-inventory.json`: 36,806 bytes, SHA-256 `2251ec21a1ef905fd41e5a62e198f6bcf6ff163add20f29ceaac3392ec691c2c`;
- `scratch-inventory.sha256`: 96 bytes, SHA-256 `5ee43fc45ffa26dcbc82d89d52afd0769d9f8661251e4e5fc7dea2f1dcbf5fbb`.

The tracked scratch manifest records every external path, byte count and SHA-256 without committing raw response bodies.

## Registry result

[claim:ACT-REGISTRY-HOLD]

Zero of ten entries meet the strict promotion norm. Every entry remains:

`STRUCTURALLY_SUPPORTED_UNPROVEN_ACTIVATION`

The ten entries are:

- `instruction:create` — `181ec828051c0777`
- `instruction:create_v2` — `d6904cec5f8b31b4`
- `instruction:buy` — `66063d1201daebea`
- `instruction:sell` — `33e685a4017f83ad`
- `instruction:buy_exact_sol_in` — `38fc74089edfcd5f`
- `instruction:buy_v2` — `b817ee6167c5d33d`
- `instruction:sell_v2` — `5df6823ce7e940b2`
- `instruction:buy_exact_quote_in_v2` — `c2ab1c46684d5b2f`
- `event:CreateEvent` — `1b72a94ddeeb6376`
- `event:TradeEvent` — `bddb7fd34ee661ee`

No registry file or readiness manifest is changed by Phase 7B.

## Authorization boundary

[claim:ACT-NO-AUTHORIZATION]

Persisting evidence and enforcing citations does not authorize:

- registry promotion;
- accepted Silver;
- bandwidth-cap preflight or network shaping;
- CAR/range/index/archive retrieval;
- Pilot A or Pilot B;
- real block, transaction or slot processing;
- ClickHouse/backfill;
- runtime, reducer or callback changes;
- strategy, OOS, execution or profitability claims.

`researchReady` and `pilotEligible` remain false. A future registry change requires a new exact-byte review epoch.

## Citation enforcement

The earlier local exact-byte citation check used an external profile ledger and `sources.py --strict`; it was not enforced remotely. Phase 7B replaces that gap with a tracked, offline deterministic gate:

`npm run ci:research-citations`

The gate validates complete load-bearing primary-response coverage, exact claim/source identity, official source classes, immutable GitHub refs, response hashes, the rederived embedded scratch inventory, the ten-entry HOLD verdict and the all-false authorization boundary. It also binds the complete evidence semantics and complete cited document bytes to trusted SHA-256 values, so pointer-preserving value/prose rewrites and source-ID/body swaps fail closed. GitHub Actions executes it as a separate required canonical step; repository policy rejects omission, renaming, comments-as-substitute and command drift. Both entrypoints reject tracked raw scratch-response paths, and the citation CLI recursively scans its own local module graph against a closed offline built-in allowlist before evaluating evidence.
