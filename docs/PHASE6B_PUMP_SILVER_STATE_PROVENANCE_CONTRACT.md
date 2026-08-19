# Phase 6B — Pump Silver State & Provenance Contract

Status: implemented on the Phase 6B branch as a fully offline, synthetic, fixture-only contract.

This phase does **not** make the historical pipeline research-ready. Every accepted and quarantined output has:

- `researchReady: false`;
- `pilotEligible: false`;
- `approved: false`;
- `evidenceClass: SYNTHETIC_TEST_ONLY`.

No real CAR, archive, slot, callback, account, dataset, ClickHouse, Triton, RPC, DAS, Titan, deployment, or application runtime data is read or produced.

## 1. Purpose and hard boundary

Phase 6A proves deterministic Pump event extraction from Bronze transaction evidence. Phase 6B adds the exact account-state and provenance evidence that would be required before such an event could later be promoted to Silver state.

The pinned Jetstreamer callback does not expose instruction-exact complete account state: owner, lamports, executable flag and raw data bytes. Consequently:

- transaction-wide pre/post lamport or token balances are not account snapshots;
- event payload fields are not state authority;
- slot-end account state is not an instruction-boundary snapshot;
- multiple writes in one transaction are ambiguous without a unique instruction boundary;
- an IDL, documentation commit, or Git commit does not prove an activation slot;
- no real slot-effective registry range is approved in Phase 6B.

`evaluatePumpSilverStateFromBronze` re-evaluates Phase 6A and then applies the synthetic state contract. `evaluatePumpSilverStateFixture` and the Rust mirror exist only for normalized golden-vector parity. They are not wired to callbacks, reducer execution, archive streaming, or persisted output.

## 2. Exact synthetic input

The fixture evaluator consumes two exact-key objects:

- `PUMP_SILVER_STATE_EVENT_BINDING_1`;
- `PUMP_SILVER_STATE_EVIDENCE_1`.

Unknown, missing, non-enumerable, symbolic, sparse, cyclic, undefined, floating-point, accessor-backed, proxied, or otherwise non-canonical input is not accepted. Accessors and proxy `get` traps are not executed by schema validation.

### Event binding

The normalized event binding carries:

- event key;
- Pump variant and buy/sell kind;
- mint and independently derived bonding-curve PDA;
- token program, quote token program and quote mint;
- base bonding-curve token account and, for v2, quote bonding-curve token account;
- signature and slot;
- transaction index;
- complete top-level parent location/index/stack coordinates;
- complete inner event location/parent-index/index/stack coordinates;
- Phase 6A canonical hash;
- creator and `mayhemMode` copied from the authenticated Phase 6A trade event;
- `isCashbackCoin: false`, pinned by this closed synthetic fixture epoch rather than inferred from event amounts;
- exact raw token and quote amounts;
- Phase 6A post-event virtual and real reserve fields for reconciliation only.

The Bronze wrapper is authoritative for constructing this binding, but it cannot authorize arbitrary Bronze identity: both it and the separate normalized fixture entrypoint must match the same closed set of eight exact synthetic event-binding hashes: seven successful golden transitions and one failed-transaction rollback fixture. The Token-2022 transition is built from an explicit Token-2022 Bronze base-token-program role and the failed binding is built from the actual failed Phase 6A output; neither is a relabelled successful binding. Changing signature, source hash, coordinates, accounts, amounts or variant cannot be relabelled by rebinding caller-controlled provenance.

A failed execution is not an early status shortcut. The evaluator first proves the allowlisted event identity, exact evidence schema, complete account state, snapshot coordinates, registry, provenance, coverage, budget and rerun hash. Once complete, correctly owned, non-executable account-state evidence is authenticated and decoded, any changed lamports or raw data emits `FAILED_TRANSACTION_ROLLBACK_MISMATCH` before reserve/event semantics can mask it. It emits `FAILED_TRANSACTION` only for exact full-state rollback. Missing, malformed, wrongly owned or executable evidence keeps its normal fail-closed reason. No failed execution can emit an accepted transition.

### Snapshot envelope

Exactly six synthetic snapshots are required for a legacy trade and eight for a v2 trade:

- bonding curve before the parent instruction;
- mint before the parent instruction;
- base bonding-curve token account before the parent instruction;
- for v2, quote bonding-curve token account before the parent instruction;
- bonding curve after the parent instruction;
- mint after the parent instruction.
- base bonding-curve token account after the parent instruction;
- for v2, quote bonding-curve token account after the parent instruction.

Each `PUMP_SILVER_ACCOUNT_SNAPSHOT_1` contains:

- account role and pubkey;
- owner program;
- canonical unsigned account lamports;
- strict `executable: false` for every accepted data-account role;
- lowercase exact raw data hex and SHA-256;
- `parent_instruction_pre` or `parent_instruction_post`;
- `SYNTHETIC_EXACT_BYTES` source;
- `INSTRUCTION_EXACT_SYNTHETIC` evidence class;
- signature, slot, transaction index, parent/event instruction coordinates and event key;
- canonical unsigned write ordinal;
- `transactionWideBalanceOnly: false`;
- `stateAuthority: RAW_ACCOUNT_STATE`;
- an explicit Token-2022 extension list.

All before ordinals must be strictly lower than all after ordinals. Any snapshot beyond the exact six/eight role-boundary identities is treated as an ambiguous write, not as additional evidence.

## 3. Exact account layouts

### Pump bonding curve

The fixture uses the pinned official Pump IDL at:

- docs commit `9c82f61cb711b044a17f770ab8ce9f9bdf78f333`;
- IDL SHA-256 `b90bc471327f671449271d5d1d42354d1fae6f5a06502f5834459a3108138e49`;
- account discriminator `17b7f83760d8ac60`;
- fixture layout identity `PUMP_IDL_BONDING_CURVE_9C82F61_1`.

The accepted byte layout is exactly 115 bytes:

- bytes `0..8`: account discriminator;
- `8..16`: virtual token reserves, little-endian `u64`;
- `16..24`: virtual quote/SOL reserves, little-endian `u64`;
- `24..32`: real token reserves, little-endian `u64`;
- `32..40`: real quote/SOL reserves, little-endian `u64`;
- `40..48`: token total supply, little-endian `u64`;
- byte `48`: strict boolean `complete`;
- `49..81`: creator pubkey;
- byte `81`: strict boolean `isMayhemMode`;
- byte `82`: strict boolean `isCashbackCoin`;
- `83..115`: quote-mint pubkey.

Longer legacy/extended layouts are not guessed or truncated. They remain unknown until a separately evidenced layout is added.

### Mint

The accepted base mint layout is exactly 82 bytes:

- mint authority as strict SPL `COption<Pubkey>`;
- supply as little-endian `u64`;
- decimals as `u8`;
- initialized as strict boolean;
- freeze authority as strict SPL `COption<Pubkey>`.

A `COption` tag must be exactly zero or one. A zero tag requires zeroed payload bytes in this synthetic contract.

Two owner programs are distinguished:

- legacy SPL Token;
- Token-2022.

Token-2022 is accepted only for the explicit 82-byte base-mint golden vector with an empty extension list and an authoritative Phase 6A source hash derived from the Token-2022 Bronze account roles. Every named, unknown, additional, or unparsed extension fails closed.

Authority fields are neutral state facts. The evaluator emits no rug score, fraud label, strategy advice, or profitability claim.

### SPL token accounts

The base bonding-curve token account and v2 quote bonding-curve token account use the exact 165-byte SPL base-account layout. The authoritative Bronze producer validates ATAs over owner, token program and mint before binding; the normalized TypeScript fixture lane accepts only the eight exact synthetic event-binding hashes described above, and both the TypeScript state boundary and Rust mirror independently rederive the same base and v2 quote ATAs. The evaluator validates strict `COption` encodings, mint, owner, amount, state, delegate, delegated amount, native reserve and close authority. Accepted synthetic accounts are initialized, undelegated, non-native and have no close authority. The base amount must equal the corresponding curve real-token reserve; the v2 quote amount must equal the corresponding real-quote reserve. Token-2022 remains limited to the same explicit empty-extension policy.

## 4. State reconciliation

For a buy:

- virtual and real token reserves decrease by exact `tokenAmount`;
- virtual and real quote reserves increase by exact `quoteAmount`.

For a sell, those operations are reversed.

All arithmetic is checked. Underflow or overflow quarantines the input.

The transition additionally requires:

- identical mint supply and decimals before/after;
- identical mint and freeze authorities before/after;
- both mint states initialized and decimals equal to the event decimal domain;
- identical bonding-curve total supply before/after;
- bonding-curve total supply equal to both before and after mint supply;
- stable creator, mayhem and cashback flags;
- fail-closed completion lifecycle and reserve bounds;
- curve quote mint equal to the event quote mint;
- base and v2 quote token-account state exactly reconcile to raw curve reserves;
- for legacy native-quote fixtures, curve-account lamports equal raw real-quote reserves plus the exact pinned synthetic rentreserve of `1_000_000` lamports at both instruction boundaries;
- for every other successful account role, including the v2 bonding curve, mint and base/v2 quote token accounts, pre/post lamports are exactly equal; unexplained lamport movement emits `ACCOUNT_LAMPORTS_MISMATCH`;
- decoded post-state reserves equal to the Phase 6A event reserve fields.

The event fields are used only as reconciliation assertions. Complete snapshot state—owner, lamports, executable flag and raw data bytes—remains authoritative.

## 5. Exact integers and canonical output

- On-chain amounts, reserves, account lamports, supply, slots, sizes, counts and write ordinals use canonical unsigned decimal strings.
- No leading zero is allowed except the single value `0`.
- Account integers are bounded by `u64`.
- Cumulative source bytes and the storage-budget domain are checked as `u128`.
- Transaction and instruction indexes are safe non-negative `u32` values.
- Mint decimals are decoded as `u8`.
- No JavaScript `number` is used for on-chain integer truth.
- No float, `uiAmount`, tolerance, estimation, interpolation, or implicit decimal conversion is accepted.

Canonical JSON sorts object keys, rejects sparse arrays and unsafe numbers, and is domain-separated before SHA-256. TypeScript and Rust emit byte-equivalent JSON values and canonical hashes for the shared vectors.

Every returned value is detached from caller input and recursively frozen, including quarantined roots and their reason arrays. Callers cannot mutate a canonical hash, state, provenance, coverage or quarantine reason after return.

## 6. Synthetic registry

Every fixture registry entry is exact-key and carries:

- `approved: false`;
- `researchReady: false`;
- `evidenceClass: SYNTHETIC_TEST_ONLY`;
- `realActivationSlotRange: null`;
- `activationSlotEvidence: NONE_SYNTHETIC_FIXTURE_ONLY`;
- pinned Pump program, IDL, discriminator, layout and token-program identities;
- `supportedToken2022Extensions: []`.

A self-approved entry, `researchReady: true`, or any claimed real activation range is quarantined. There is no real slot-effective range in this phase.

## 7. Synthetic provenance and coverage

`PUMP_SILVER_STATE_PROVENANCE_1` contains fixture-only golden values for:

- immutable CAR CID;
- CAR SHA-256 and file size;
- slot-inventory SHA-256, file size and entry count;
- exact half-open source slot range;
- parser, reducer and adapter Git SHAs;
- Phase 6A source hash;
- canonical event-binding hash;
- account-snapshot-set output hash;
- cumulative source bytes;
- deterministic rerun hash.

These values are invented golden test identities. They do not identify a downloaded CAR or real Old Faithful artifact.

The fixture pins every value exactly. A structurally valid but different CID, hash, size, Git SHA, wider slot range, or budget does not become trusted merely because it is well formed.

Coverage records exact canonical counts for:

- expected, observed, skipped and quarantined slots;
- expected, observed, skipped and quarantined callbacks;
- quarantine totals by closed reason.

Each expected total must equal observed plus skipped plus quarantined with checked arithmetic. Accepted fixtures pin one observed slot, two observed callbacks, zero skipped/quarantined work and an empty reason ledger. Declared coverage must not exceed its exact pilot budget. Incomplete, overflowing, skipped-only, over-budget or non-closing accounting fails closed.

The synthetic pilot budget is exact and bounded:

- bytes read: 1,048,576;
- bytes written: 1,048,576;
- slots: 1;
- callbacks: 16;
- runtime: 60,000 ms;
- storage: 2,097,152 bytes.

Changing any budget value requires a new reviewed fixture and rerun hash; it is not accepted as an input override.

## 8. Fail-closed result

The output schema is `PUMP_SILVER_STATE_CONTRACT_1`.

An accepted fixture emits:

- `status: FIXTURE_VALID`;
- `approved: false`;
- decoded before/after curve, mint, base-token-account and optional v2 quote-token-account state;
- synthetic provenance and coverage;
- empty quarantine reasons;
- `researchReady: false` and `pilotEligible: false`.

The TypeScript result graph is a detached, deeply frozen snapshot; later caller mutation cannot alter accepted output after its canonical hash is computed.

A rejected fixture emits:

- `status: QUARANTINED`;
- `approved: false`;
- no state, provenance or coverage payload;
- one deterministic closed reason;
- `researchReady: false` and `pilotEligible: false`.

The 34 bounded reason codes are defined by `PUMP_SILVER_STATE_QUARANTINE_REASONS`. They cover invalid schema/Phase 6A evidence, registry violations, raw-state and causal failures, account identity/layout failures, integer/state reconciliation failures, provenance/coverage/rerun failures and observability-boundary violations.

There is no warning-only path, best match, fallback, guessed state, partial acceptance, or mechanism that can raise `researchReady`.

## 9. Transport-free observability boundary

Phase 6B defines metadata only. It adds no Prometheus dependency, metric client, listener, timer, exporter, dashboard, Grafana change, Redis dependency, queue or callback hook.

A later side-channel consumer may observe 20 bounded signals:

- callbacks received;
- transactions processed;
- snapshot pairs received;
- Silver state accepted/quarantined;
- expected/observed/skipped/quarantined coverage;
- rerun-hash match/mismatch;
- current and last completed slot;
- bytes read/written;
- callback and Silver-state evaluation durations supplied by the caller as external observations;
- queue depth;
- WAL and checkpoint results.

Allowed labels are restricted to:

- `stage`: `reducer`, `bronze`, `silver`;
- `result`: seven closed result values;
- `reason`: the 34 closed quarantine reasons.

Forbidden labels include mint, signature, wallet, account pubkey, slot, transaction ID, event key and arbitrary error text.

Metrics are never an evaluator input and never enter canonical state, hashes, ordering, quarantine or output. A missing or failing metrics consumer cannot alter research output. Redis remains outside the contract and is never required for replay, provenance, deduplication, state or recovery.

## 10. Shared fixtures and parity

`tests/fixtures/pump-silver/state-vectors.json` contains:

- seven golden vectors: all six pinned trade variants plus a Token-2022/no-extension v2 transition;
- 44 adversarial quarantine vectors, including authenticated failed rollback, arbitrary failed identity, changed failed raw data or lamports, unauthenticated changed rollback, missing or malformed failed evidence, malformed execution/stack coordinates, null/exact-key registry drift, strict SPL `COption` drift, valid-pubkey ATA drift, executable/noncanonical account metadata, native-quote lamport and rentreserve drift, unexplained successful lamport movement for every relevant role, uninitialized raw token-account state, coverage overflow, provenance, source authentication and canonical-input cases.

TypeScript and Rust evaluate the same event binding, complete raw account state, registry, provenance, coverage and budget and must equal the same expected JSON value byte-for-byte, including canonical hash and reason code.

The TypeScript suite additionally probes every required mutation class, including owner/PDA/ATA/coordinates/order, raw-byte absence, event authority, duplicate/ambiguous writes, mint/curve/token-account invariants, unsafe integers, supply/decimal/reserve/lamport conflict, CAR/inventory drift, coverage closure/budget overflow, rerun drift, source authentication, provenance-before-rollback precedence, registry self-approval, immutable accepted/quarantined output, inert exact-key input and observability cardinality/timing.

## 11. Production graph isolation

The TypeScript functions are referenced only by Phase 6B tests. The Rust module is exported for standalone fixture tests but has no caller in reducer execution.

Phase 6B does not modify:

- Jetstreamer callback types;
- callback ingestion;
- reducer processing loops;
- Bronze persistence;
- adapter runtime;
- archive/CAR readers;
- checkpoint or WAL execution;
- dataset writers.

The repository transport-isolation scan covers the built `dist/research` graph. The new module imports only deterministic local code, `node:crypto`, `node:util` and `bs58`.

## 12. Gate to any real pilot

A real bounded slot pilot remains HOLD until a separate review proves all of the following:

- a source supplies instruction-exact complete pre/post account state or a deterministic era-faithful replay boundary;
- deployment/codehash and upgrade boundaries are established independently;
- slot-effective IDL, discriminator and layout ranges are evidenced without gaps or overlaps;
- real CAR CID/SHA/size and slot inventory are independently verified;
- parser/reducer/adapter identities are bound to immutable reviewed bytes;
- source, output, coverage and rerun hashes reproduce;
- every bounded budget and skip/quarantine rule is approved;
- TypeScript/Rust real-source parity is green;
- the pilot receives separate explicit authorization.

Until then, `researchReady` remains hard false and no registry entry can be approved.
