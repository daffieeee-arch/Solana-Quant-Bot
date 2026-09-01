# Old Faithful Quant Data Specification

> **Document status: SUPERSEDED.** Replaced by the V2 boundaries in [`../HANDOFF_V2.md`](../HANDOFF_V2.md) and [`../ARCHITECTURE.md`](../ARCHITECTURE.md). Historical ranges/caps below are not V2 constants or approvals.

Status: **CANDIDATE_UNAPPROVED**. This specification is a design contract only.

- `approved: false`
- `researchReady: false`
- `pilotEligible: false`
- It authorizes no download, stream, slot processing, infrastructure mutation, OOS run, strategy optimization, or profitability claim.

## 1. Availability classes

Every canonical field must have exactly one class:

- `DIRECT_OF1`: present in the pinned OF1 block, transaction, message, metadata, log, reward, or source sidecar bytes.
- `DETERMINISTICALLY_DERIVABLE_FROM_OF1`: reproducible from direct OF1 bytes with frozen code, slot-effective registry, exact integer arithmetic, and no future input.
- `REQUIRES_HISTORICAL_ACCOUNT_STATE`: requires raw account bytes at a proven historical causal boundary.
- `REQUIRES_PROSPECTIVE_LIVE_CAPTURE`: requires intent, receipt, retry, reject, latency, or Geyser evidence captured prospectively.
- `REQUIRES_POINT_IN_TIME_MARKET_SOURCE`: requires a market observation whose `observedAt` and `effectiveAt` are both preserved.
- `INFERRED_NOT_FACT`: model- or rule-derived interpretation, always carrying evidence and uncertainty.
- `NOT_RELIABLY_AVAILABLE`: cannot be claimed reliably from the approved sources.

Point-in-time market-source classes are:

- `ONCHAIN_ORACLE_OBSERVED`
- `OFFCHAIN_PROVIDER_OBSERVED`
- `CROSS_RATE_DERIVED`
- `UNKNOWN`

`UNKNOWN` is missing evidence, never zero.

## 2. Integer, causal, and provenance rules

- Slots, raw amounts, lamports, fees, reserves, supply, bytes, and counts use unsigned integers or canonical decimal strings; JavaScript floating point is not source-of-truth.
- Prices retain numerator, denominator, base decimals, quote decimals, and display decimal separately.
- Transaction identity is `(slot, transactionIndex, signature)`.
- Instruction/event identity adds `(topLevelInstructionIndex, innerInstructionIndex, stackHeight, eventOrdinal)`.
- Account-state identity adds `(pubkey, slot, writeVersion, causingSignature, boundary)`.
- Market identity adds `(sourceClass, sourceId, instrument, observedAt, effectiveAt, rawResponseHash)`.
- Null semantics distinguish `SOURCE_NULL`, `NOT_CAPTURED`, `NOT_APPLICABLE`, `UNKNOWN`, `QUARANTINED`, `RIGHT_CENSORED`, and `SOURCE_GAP`.
- Every Bronze/Silver/Gold artifact binds source CID/SHA, source range, inventory hash, parser/registry/config/schema hashes, and canonical content hash.

Old Faithful publishes per-epoch CAR, CID, SHA-256, slot inventory, recap, and index sidecars.[1] OF1 transaction logs are execution metadata and can differ between warehouse ledgers, so a content-pinned archive proves the reviewed bytes but logs are not raw account-state authority.[2] The pinned Jetstreamer callback source exposes ordered transaction identity/status data separately from block metadata, which is why the adapter/reducer must bind both callbacks before durable event projection.[4]

## 3. Field-level data availability matrix

Each bullet states: canonical fields — class; datatype/semantics; quant purpose; as-of/causal boundary; missing/validation; layer and destination.

### Chain envelope

- `epoch` — `DETERMINISTICALLY_DERIVABLE_FROM_OF1`; `UInt64` from slot schedule; partition/cohort; block as-of; non-null and range-checked; Silver, research/frontend/Grafana aggregate.
- `slot`, `parentSlot`, `blockhash`, `previousBlockhash` — `DIRECT_OF1`; exact integer/hashes; ordering/fork continuity; block causal; validate parent links and inventory; Bronze, research/frontend.
- `blockTime` — `DIRECT_OF1`; nullable Unix seconds; calendar features only; block causal and estimated; null remains null; Bronze/Silver, research/frontend/Grafana aggregate.
- `transactionIndex`, `signature`, `transactionVersion` — `DIRECT_OF1`; exact ordered identity; transaction causal; unique/index-bounded/version-aware; Bronze, research/frontend.
- `accountKeys`, `addressTableLookups`, `loadedAddresses` — `DIRECT_OF1`; ordered pubkeys/indexes; account resolution; transaction causal; static+writable+readonly ordering and bounds; Bronze, research/frontend.
- `topLevelInstructions`, `innerInstructions`, `stackHeight` — `DIRECT_OF1`; raw program/account/data coordinates; protocol evidence; instruction causal; null/absence distinct and stack location validated; Bronze, research/frontend.
- `logs`, `returnData` — `DIRECT_OF1`; ordered raw evidence; event decoding/debug; transaction causal; return data is only the final returned value; Bronze, research/frontend.
- `successFailure`, `feeLamports`, `computeUnitsConsumed`, `costUnits`, `rewards` — `DIRECT_OF1`; exact transaction/block economics; transaction/block causal; nullable metadata is not zero; Bronze/Silver, research/frontend/Grafana aggregate.
- `nativePrePostBalances`, `tokenPrePostBalances` — `DIRECT_OF1`; transaction-wide arrays; reconciliation only; transaction boundary; never instruction-exact raw state; Bronze, research/frontend.

Solana's confirmed block/transaction structures include message, metadata, balances, logs, inner instructions, loaded addresses, fees, compute use, status, and rewards.[5]

### Pump lifecycle and event tape

- `CreateEvent`, `TradeEvent` — `DETERMINISTICALLY_DERIVABLE_FROM_OF1`; exact Borsh fields plus raw bytes; launch/trade tape; event coordinates; only `PROVEN_AT_SLOT_RANGE` registry entries may promote to accepted Silver; Silver, research/frontend/Grafana aggregate.
- `buySell`, `mint`, `bondingCurve`, `creator`, `feePayer` — `DETERMINISTICALLY_DERIVABLE_FROM_OF1`; enums/pubkeys; identity and signed flow; instruction/event causal; roles/PDA/fee-payer ordering validated; Silver, research/frontend.
- `launchSlotTime`, `firstTrade`, `curveCompletion`, `migration` — `DETERMINISTICALLY_DERIVABLE_FROM_OF1`; coordinates/timestamps; lifecycle/survival; first explicit successful evidence only; missing event remains unknown; Silver/Gold, research/frontend.
- `failedLaunchAttempt`, `failedPumpTransaction` — `DETERMINISTICALLY_DERIVABLE_FROM_OF1`; failed envelope plus discriminators; survivorship/cost control; transaction causal; never promoted to successful lifecycle; Silver, research/frontend/Grafana aggregate.
- `unknownDiscriminator` — `DETERMINISTICALLY_DERIVABLE_FROM_OF1`; raw bytes/coordinate; registry-gap evidence; instruction causal; always Bronze+quarantine, never best effort; Bronze/Silver quarantine, research/frontend.
- `tokenDeathOrInactivity` — `INFERRED_NOT_FACT`; rule/version/censoring status; survival feature; observation-window causal; never presented as an on-chain death fact; Gold, research/frontend.

The official Pump IDL at the pinned commit provides the reviewed program, instruction, account, and event layouts.[3] Its commit and content hash do not prove activation during epoch 978.

### Financial trade tape

- `rawTokenAmount`, `rawSolOrQuoteAmount` — `DETERMINISTICALLY_DERIVABLE_FROM_OF1`; raw `u64`; exact tape/notional; event causal; event/role/reconciliation checks; Silver, research/frontend/Grafana aggregate.
- `tokenSolPrice` — `DETERMINISTICALLY_DERIVABLE_FROM_OF1`; exact ratio plus decimals; returns/features; event causal; zero denominator forbidden; Silver/Gold, research/frontend.
- `tokenUsd`, `solUsd` — `REQUIRES_POINT_IN_TIME_MARKET_SOURCE`; decimal plus source class and as-of times; USD notional/regime; observation causal; stale/missing explicit; Silver/Gold, research/frontend/Grafana aggregate.
- `protocolFee`, `creatorFee`, landed `networkFee` — `DETERMINISTICALLY_DERIVABLE_FROM_OF1` or direct transaction fee; raw lamports/bps; net cost; transaction/event causal; component reconciliation; Silver/Gold, research/frontend.
- `eventReportedReserves` — `DETERMINISTICALLY_DERIVABLE_FROM_OF1`; raw reserve fields; event-tape feature; event causal; badge `OBSERVED_EVENT_FIELD`, never raw state; Silver, research/frontend.
- `instructionExactPriceImpact`, `positionSizeQuote`, `exitLiquidity` — `REQUIRES_HISTORICAL_ACCOUNT_STATE`; exact pre/post state and fee/curve version; executability/capacity; decision boundary; unavailable without state; Silver/Gold, research/execution.
- `signedFlow`, `tradeIntensity`, `uniqueTraders`, `tradeSizeDistribution`, `whaleShare`, `buySellStreaks`, `realizedVolatility`, `momentum_*` — `DETERMINISTICALLY_DERIVABLE_FROM_OF1`; integer/rational past-only windows; breadth/flow/regime/features; observation cutoff; frozen horizons and explicit empty windows; Gold, research/Grafana aggregate.

### Account state

- `bondingCurveRawBytes`, `accountOwner`, real/virtual reserves, `complete` — `REQUIRES_HISTORICAL_ACCOUNT_STATE`; exact raw account snapshot; authoritative liquidity/transition; account-write or transaction boundary; owner/layout/hash validated; Bronze-state/Silver-state, research/frontend/execution.
- `mintSupply`, `decimals`, mint/freeze authority, token-program kind, Token-2022 extensions — `REQUIRES_HISTORICAL_ACCOUNT_STATE`; exact mint bytes/TLV; scaling/dilution/transfer semantics; account-write boundary; unknown extension quarantined; Bronze-state/Silver-state, research/frontend/execution.
- Generic `instructionExactBeforeAfterState` — `NOT_RELIABLY_AVAILABLE` from OF1 or standard Geyser alone. Standard Geyser account updates carry raw account data, slot, write version, and causing transaction at account-update/transaction boundaries, not every intermediate CPI state.[6]

### Creator, wallet, and holders

- `priorCreatorLaunches`, `creatorSells`, `observedTransferEdges`, `earlyBuyerConcentration` — `DETERMINISTICALLY_DERIVABLE_FROM_OF1`; counts/events before cutoff; behavior/concentration; complete prior coverage required; Silver/Gold, research/frontend.
- `creatorHoldings`, `holderCount`, `topHolderConcentration`, `holderChurn` — `REQUIRES_HISTORICAL_ACCOUNT_STATE`; exact token-account snapshots; sell pressure/distribution; snapshot as-of cutoff; complete account coverage required; Silver/Gold, research/frontend/Grafana aggregate.
- `creatorFundingSource`, `creatorWalletAge`, `relatedWallet`, `freshWalletPercentage`, `walletClusterId` — `INFERRED_NOT_FACT`; evidence edges/rule/model/confidence; behavior/risk features; observation causal; no identity/ownership attribution; Gold, research/frontend.

### Execution

- `baseFee`, `priorityFee`, `computeUnitPrice`, `computeUnitsRequested` — `DETERMINISTICALLY_DERIVABLE_FROM_OF1`; integer components from message/fee rules; landed cost/regime; transaction causal; reconcile with total fee; Silver/Gold, research/execution/Grafana aggregate.
- `computeUnitsConsumed`, landed success/failure, block position — `DIRECT_OF1`; exact metadata/index; landed execution context; transaction causal; Silver, research/frontend.
- `tipTransferToAddress` — `DETERMINISTICALLY_DERIVABLE_FROM_OF1`; exact transfer destination/amount and transaction coordinates; proves only that a transfer occurred.
- `JITO_TIP_TRANSFER_OBSERVED` — `REQUIRES_POINT_IN_TIME_MARKET_SOURCE`; combines the OF1-observed transfer with a separately pinned official Jito tip-account-set source, source class, `observedAt`, `effectiveAt`, raw response hash, and validity interval; proves only that the destination was officially classified as a Jito tip account at that time.
- `JITO_BUNDLE_PROVEN` — `REQUIRES_PROSPECTIVE_LIVE_CAPTURE`; bundle ID, submission response/status, transaction set, landing context, and immutable telemetry; a tip transfer alone is insufficient. Jito documents separate transaction/bundle submission and status semantics.[7]
- `clientRetries`, `rejectStatus`, `neverLanded`, receipt/send/land latency — `REQUIRES_PROSPECTIVE_LIVE_CAPTURE`; intent/attempt/timestamp records; failure and latency modelling; monotonic receipt boundary; ledger absence alone is not a reject reason; Silver/Gold, research/execution/Grafana aggregate.
- `expectedVsExecutablePrice` — `REQUIRES_PROSPECTIVE_LIVE_CAPTURE` plus state evidence; intent quote versus bound landed fill; slippage/latency; same intent ID required; Gold, research/execution.

### Market regime

- `solUsd`, `solVolatility` — `REQUIRES_POINT_IN_TIME_MARKET_SOURCE`; source-classified as-of observations; USD/regime; no revised future value; Silver/Gold, research/Grafana aggregate.
- `pumpLaunchRate`, aggregate Pump flow, priority-fee regime, block compute load, UTC time/day, competing Pump momentum — `DETERMINISTICALLY_DERIVABLE_FROM_OF1`; frozen past-only windows; contemporaneous regime; complete coverage required; Gold, research/Grafana aggregate.
- `networkCongestionRegime` — `INFERRED_NOT_FACT`; model from ledger-observed fee/failure/fullness/compute proxies; not mempool truth; Gold, research/Grafana aggregate.
- `broaderMemecoinActivity` — `REQUIRES_POINT_IN_TIME_MARKET_SOURCE` unless a separate complete frozen on-chain universe is approved; Gold, research/Grafana aggregate.

## 4. Bronze, Silver, and Gold

### Bronze

Immutable evidence: source manifests, blocks, transactions, messages, top-level/inner instructions, logs, return data, native/token balances, rewards, coverage, range fetches, retries, WAL/checkpoints, conflicts, and quarantine. Separate future state/offchain/execution Bronze sources may be added only with their own provenance.

### Silver

Canonical facts: Pump creates/trades, event-reported reserves, separately account-verified state, mint state, token lifecycle, migration, wallet actions, holder snapshots, execution context, and market-regime snapshots. Event-reported and raw-account-state columns are never merged silently.

### Gold

Point-in-time observation snapshots, feature vectors, executable forward labels, cohorts, censoring, immutable strategy trials, and OOS reports. Gold rejects any input whose Silver provenance/approval is absent or whose `availableAt` exceeds the observation cutoff.

## 5. Point-in-time and anti-leakage protocol

- Universe is all Pump-touching transactions in the declared slot coverage: successful/failed creates, winners, losers, no-volume/non-graduated/illiquid tokens, failed transactions, unknowns, and quarantines.
- DexScreener visibility or later success never defines inclusion.
- Every `scanCycleId` binds cadence version, token, observation slot/time, exact causal cutoff, feature freshness, completeness, gate verdict, score, and candidate/no-candidate.
- A fixed slot cadence and event-trigger cadence are declared before label inspection.
- Features use only `availableAt <= observationCutoff`.
- `NO_ENTRY`, `NO_TRADE`, `NO_EXECUTABLE_EXIT`, `SOURCE_GAP`, `QUARANTINED`, `RIGHT_CENSORED`, and `OPEN_AT_CUTOFF` remain separate outcomes.
- Splits are chronological by token launch; all rows for one token stay in one split.
- Purge and embargo are at least the maximum feature and hold horizon.
- One final period remains untouched.
- Every feature/parameter/strategy attempt enters an immutable trial ledger before result inspection.

## 6. Executable cost and label requirements

Profitability requires position-size-conditioned entry/exit fills, historical state, protocol/creator/network/priority fees, proven Jito costs where applicable, failed/rejected attempts, latency, retries, slippage, impact, exit liquidity, censoring, and capacity. Forward return, MFE, MAE, TP/SL/peak time, drawdown, survival, graduation, and realized PnL are invalid without those inputs.

Pilot A cannot produce these claims.

## 7. Frontend evidence contract

Every future API row exposes dataset/run, as-of cutoff, provenance, approval, and one of:

- `OBSERVED`
- `DERIVED`
- `INFERRED`
- `UNKNOWN`
- `QUARANTINED`
- `SYNTHETIC`
- `REAL_UNAPPROVED`
- `RESEARCH_READY`

Views: dataset/run selector, provenance/data quality, lifecycle, trade tape, event/instruction inspector, quant feature explorer, and coverage/quarantine explorer. Pilot A must not populate liquidity, holder, raw-state, executable-fill, or profitability views as proven.

## 8. Grafana and Prometheus boundary

Prometheus/Grafana is an observability side-channel, not research ground truth. Metrics never influence canonical bytes, ordering, hashes, acceptance, or quarantine. Prometheus guidance requires stable metric names and restrained labels; high-cardinality entity identities belong in ClickHouse/API, not labels.[8] Grafana may query Prometheus for operations and ClickHouse later for aggregate research.[9]

Allowed labels: `stage`, `result`, `quarantine_reason`, `source`, `schema_version`, `run_mode`.

Forbidden labels: mint, signature, wallet, account pubkey, slot, transaction/event/run ID, and arbitrary error text.

## 9. Pilot A versus Pilot B

### Pilot A

`OLD_FAITHFUL_EVENT_TRANSPORT_PILOT`: may later prove bounded retrieval, block/transaction/instruction/log/event evidence, ordering, transport coverage, quarantine, retries/idempotence, deterministic reruns, and resource profile. `TRANSPORT_COVERAGE_PASS` is independent from `EVENT_SEMANTIC_COVERAGE_PASS`; absence of an event category does not invalidate transport coverage or justify outcome-driven range selection.

### Pilot B

State-enriched and **NO-GO** until an independently verified historical raw-account-state source, causal binding, activation/layout ranges, and reproducible state provenance exist.

## Sources

[1] https://docs.old-faithful.net/references/of1-files
[2] https://docs.old-faithful.net/usage/validation/reproducibility
[3] https://raw.githubusercontent.com/pump-fun/pump-public-docs/9c82f61cb711b044a17f770ab8ce9f9bdf78f333/idl/pump.json
[4] https://raw.githubusercontent.com/anza-xyz/jetstreamer/cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24/jetstreamer-firehose/src/firehose.rs
[5] https://solana.com/docs/rpc/json-structures
[6] https://docs.anza.xyz/validator/geyser
[7] https://docs.jito.wtf/lowlatencytxnsend
[8] https://prometheus.io/docs/practices/naming
[9] https://grafana.com/docs/grafana/latest/datasources/prometheus
