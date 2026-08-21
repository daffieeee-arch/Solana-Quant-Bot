use std::{
    collections::{BTreeMap, BTreeSet},
    ffi::OsString,
    fs::{self, File, OpenOptions},
    io::{Read as _, Write as _},
    path::{Component, Path, PathBuf},
    str::FromStr as _,
    time::{Duration, Instant},
};

use jetstreamer_firehose::firehose::{BlockData, KeyedRewardsAndNumPartitions, TransactionData};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest as _, Sha256};
use solana_account_decoder_client_types::token::UiTokenAmount;
use solana_address::Address;
use solana_hash::Hash;
use solana_message::{
    VersionedMessage,
    compiled_instruction::CompiledInstruction,
    legacy::Message as LegacyMessage,
    v0::{LoadedAddresses, Message as V0Message, MessageAddressTableLookup},
};
use solana_signature::Signature;
use solana_transaction::versioned::VersionedTransaction;
use solana_transaction_error::TransactionError;
use solana_transaction_status::{
    InnerInstruction, InnerInstructions, TransactionStatusMeta, TransactionTokenBalance,
};

use crate::{
    AdapterProvenance, ObserveOutcome, OldFaithfulSourceManifest, Phase5Reducer, ReducerConfig,
    ReducerLimits, ReducerProvenance, SlotRange,
};

const INPUT_SCHEMA: &str = "PHASE8A_BRONZE_FIXTURE_INPUT_1";
const VERDICT_SCHEMA: &str = "PHASE8A_BRONZE_RUNNER_VERDICT_1";
const SOURCE_CLASS: &str = "SYNTHETIC_FIXTURE_ONLY";
const OBSERVED_AT: &str = "2026-08-20T20:00:00.000Z";
const MAX_INPUT_BYTES: u64 = 2 * 1024 * 1024;
const MAX_CALLBACKS: usize = 128;
const MAX_SLOTS: u64 = 16;
const MAX_TRANSACTIONS_PER_SLOT: usize = 32;
const MAX_RUNTIME_SECONDS: u64 = 30;
const MAX_OUTPUT_BYTES: u64 = 32 * 1024 * 1024;
const SOURCE_MANIFEST_SHA256: &str =
    "0e822203d4363cbd5b3863fe257e93c999d9925b10f8c4100f64856ec81bf425";
const ADAPTER_PROVENANCE_SHA256: &str =
    "821034a2711a97b037eda896b05d11c9de9b63eb29a0566a637d40003a04af2e";
const REDUCER_PROVENANCE_SHA256: &str =
    "69cef4043859185b1d02bfd0a876838ae63b041afcca528d645b8b9b50c819ef";
const CONFIG_SHA256: &str = "a3b25f29d7ba57850fa827768c9e09d171a47051a6c66df2a76437ae1fff4d30";
const SCHEMA_SHA256: &str = "a5c19e0a0944932567fbb0b5a530310a3212b3ada968836267df581601382cb6";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum FailureClass {
    Policy,
    Operational,
}

#[derive(Debug)]
struct RunnerFailure {
    class: FailureClass,
    reason: &'static str,
}

impl RunnerFailure {
    const fn policy(reason: &'static str) -> Self {
        Self {
            class: FailureClass::Policy,
            reason,
        }
    }

    const fn operational(reason: &'static str) -> Self {
        Self {
            class: FailureClass::Operational,
            reason,
        }
    }
}

type RunnerResult<T> = Result<T, RunnerFailure>;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
#[allow(clippy::struct_excessive_bools)]
struct Verdict<'a> {
    schema_version: &'static str,
    status: &'static str,
    reason_code: &'static str,
    source_class: &'static str,
    evidence_badge: &'static str,
    real_data: bool,
    accepted_silver: bool,
    research_ready: bool,
    output_published: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    run_id: Option<&'a str>,
}

/// Runs the command-line contract and returns its stable process exit code.
///
/// The function always emits exactly one JSON verdict line to stdout. Diagnostics are fixed,
/// bounded strings and never include user-controlled input or paths.
pub fn phase8a_bronze_runner_main(args: impl IntoIterator<Item = OsString>) -> i32 {
    let parsed = parse_args(args);
    let result = match parsed {
        Ok((input, output)) => execute(&input, &output),
        Err(error) => Err(error),
    };
    match result {
        Ok(run_id) => {
            print_verdict(&Verdict {
                schema_version: VERDICT_SCHEMA,
                status: "SUCCEEDED",
                reason_code: "SUCCESS",
                source_class: SOURCE_CLASS,
                evidence_badge: "SYNTHETIC",
                real_data: false,
                accepted_silver: false,
                research_ready: false,
                output_published: true,
                run_id: Some(&run_id),
            });
            0
        }
        Err(error) => {
            let exit_code = if error.class == FailureClass::Policy {
                eprintln!("phase8a-bronze-runner: policy or input rejected");
                2
            } else {
                eprintln!("phase8a-bronze-runner: operational failure");
                1
            };
            print_verdict(&Verdict {
                schema_version: VERDICT_SCHEMA,
                status: "REJECTED",
                reason_code: error.reason,
                source_class: SOURCE_CLASS,
                evidence_badge: "SYNTHETIC",
                real_data: false,
                accepted_silver: false,
                research_ready: false,
                output_published: false,
                run_id: None,
            });
            exit_code
        }
    }
}

fn print_verdict(verdict: &Verdict<'_>) {
    let bytes = serde_json::to_vec(&verdict).expect("verdict serialization cannot fail");
    let mut stdout = std::io::stdout().lock();
    stdout
        .write_all(&bytes)
        .and_then(|()| stdout.write_all(b"\n"))
        .expect("stdout write failed");
}

fn parse_args(args: impl IntoIterator<Item = OsString>) -> RunnerResult<(PathBuf, PathBuf)> {
    let values = args.into_iter().collect::<Vec<_>>();
    if values.len() != 4 || values[0] != "--input" || values[2] != "--output" {
        return Err(RunnerFailure::policy("INVALID_ARGUMENTS"));
    }
    let input = PathBuf::from(&values[1]);
    let output = PathBuf::from(&values[3]);
    if input.as_os_str().is_empty() || output.as_os_str().is_empty() {
        return Err(RunnerFailure::policy("INVALID_ARGUMENTS"));
    }
    Ok((input, output))
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct FixtureInput {
    schema_version: String,
    source_class: String,
    observed_at: String,
    source_manifest: OldFaithfulSourceManifest,
    source_manifest_sha256: String,
    adapter_provenance: AdapterProvenance,
    adapter_provenance_sha256: String,
    reducer_provenance: ReducerProvenance,
    reducer_provenance_sha256: String,
    config: FixtureConfig,
    config_sha256: String,
    schema_sha256: String,
    expected_slot_range: SlotRange,
    observability: FixtureObservability,
    callbacks: Vec<CallbackInput>,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct FixtureObservability {
    bytes_read: u64,
    bytes_written: u64,
    queue_depth: u64,
    peak_rss_bytes: u64,
    output_bytes: u64,
    stage_durations_ms: FixtureStageDurations,
    wal_status: String,
    checkpoint_status: String,
    quarantine_by_reason: FixtureQuarantineCounts,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct FixtureStageDurations {
    validate: u64,
    reduce: u64,
    publish: u64,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE", deny_unknown_fields)]
struct FixtureQuarantineCounts {
    unknown_discriminator: u64,
    failed_transaction: u64,
}

#[derive(Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
#[allow(clippy::struct_field_names)]
struct FixtureConfig {
    max_callbacks: usize,
    max_slots: u64,
    max_transactions_per_slot: usize,
    max_runtime_seconds: u64,
    max_output_bytes: u64,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(tag = "kind", rename_all = "camelCase", deny_unknown_fields)]
enum CallbackInput {
    Transaction {
        transaction: Box<TransactionInput>,
    },
    Block {
        block: BlockInput,
    },
    PossibleLeaderSkipped {
        #[serde(rename = "possibleLeaderSkipped")]
        possible_leader_skipped: PossibleLeaderSkippedInput,
    },
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct TransactionInput {
    slot: u64,
    transaction_index: usize,
    signature_byte: u8,
    message_hash_byte: u8,
    is_vote: bool,
    version: TransactionVersion,
    execution_status: ExecutionStatus,
    fee_lamports: String,
    static_account_keys: Vec<String>,
    loaded_addresses: LoadedAddressesInput,
    address_table_lookups: Vec<AddressTableLookupInput>,
    pre_balances_lamports: Vec<String>,
    post_balances_lamports: Vec<String>,
    instructions: Vec<CompiledInstructionInput>,
    inner_instructions: Vec<InnerInstructionsInput>,
    log_messages: Vec<String>,
    pre_token_balances: Vec<TokenBalanceInput>,
    post_token_balances: Vec<TokenBalanceInput>,
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
enum TransactionVersion {
    Legacy,
    V0,
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
enum ExecutionStatus {
    Succeeded,
    Failed,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct LoadedAddressesInput {
    writable: Vec<String>,
    readonly: Vec<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct AddressTableLookupInput {
    account_key: String,
    writable_indexes: Vec<u8>,
    readonly_indexes: Vec<u8>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct CompiledInstructionInput {
    program_id_index: u8,
    accounts: Vec<u8>,
    data_hex: String,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct InnerInstructionsInput {
    parent_instruction_index: u8,
    instructions: Vec<InnerInstructionInput>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct InnerInstructionInput {
    program_id_index: u8,
    accounts: Vec<u8>,
    data_hex: String,
    stack_height: u32,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct TokenBalanceInput {
    account_index: u8,
    mint: String,
    owner: String,
    program_id: String,
    decimals: u8,
    amount: String,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct BlockInput {
    slot: u64,
    parent_slot: u64,
    parent_blockhash_byte: u8,
    blockhash_byte: u8,
    block_time: i64,
    block_height: Option<u64>,
    executed_transaction_count: u64,
    entry_count: u64,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct PossibleLeaderSkippedInput {
    slot: u64,
}

fn execute(input_path: &Path, output_path: &Path) -> RunnerResult<String> {
    let started = Instant::now();
    let input_path = absolute_normalized(input_path)?;
    let output_path = absolute_normalized(output_path)?;
    validate_input_path(&input_path)?;
    validate_output_path(&output_path)?;
    let input_bytes = descriptor_read_to_eof(&input_path, MAX_INPUT_BYTES, &started)?;
    let input_sha256 = sha256_bytes(&input_bytes);
    let run_id = format!("phase8a-fixture-{input_sha256}");
    let fixture: FixtureInput = serde_json::from_slice(&input_bytes)
        .map_err(|_| RunnerFailure::policy("INVALID_INPUT_SCHEMA"))?;
    validate_fixture(&fixture)?;

    let parent = output_path
        .parent()
        .ok_or_else(|| RunnerFailure::policy("UNSAFE_OUTPUT_PATH"))?;
    let file_name = output_path
        .file_name()
        .ok_or_else(|| RunnerFailure::policy("UNSAFE_OUTPUT_PATH"))?
        .to_string_lossy();
    let staging = parent.join(format!(".{file_name}.phase8a-{input_sha256}.tmp"));
    if staging.symlink_metadata().is_ok() {
        return Err(RunnerFailure::operational("STALE_STAGING_OUTPUT"));
    }

    let result = execute_staged(&fixture, &input_sha256, &run_id, &staging, started);
    if let Err(error) = result {
        let _ = remove_namespace_lock(&staging);
        let _ = make_tree_writable(&staging);
        let _ = fs::remove_dir_all(&staging);
        return Err(error);
    }
    remove_namespace_lock(&staging)?;
    fs::rename(&staging, &output_path)
        .map_err(|_| RunnerFailure::operational("OUTPUT_PUBLICATION_FAILED"))?;
    if sync_directory(parent).is_err() {
        rollback_publication(&output_path, &staging, parent)?;
        return Err(RunnerFailure::operational("OUTPUT_SYNC_FAILED"));
    }
    Ok(run_id)
}

fn execute_staged(
    fixture: &FixtureInput,
    input_sha256: &str,
    run_id: &str,
    staging: &Path,
    started: Instant,
) -> RunnerResult<()> {
    fs::create_dir(staging).map_err(|_| RunnerFailure::operational("STAGING_CREATE_FAILED"))?;
    let reducer_dir = staging.join("reducer");
    let mut reducer = Phase5Reducer::open(ReducerConfig {
        output_dir: reducer_dir.clone(),
        source_manifest: fixture.source_manifest.clone(),
        adapter_provenance: fixture.adapter_provenance.clone(),
        reducer_provenance: fixture.reducer_provenance.clone(),
        limits: ReducerLimits {
            max_pending_slots: usize::try_from(MAX_SLOTS).expect("constant fits usize"),
            max_transactions_per_slot: MAX_TRANSACTIONS_PER_SLOT,
            max_output_bytes: MAX_OUTPUT_BYTES,
            max_checkpoint_bytes: MAX_OUTPUT_BYTES,
            max_runtime_seconds: MAX_RUNTIME_SECONDS,
        },
    })
    .map_err(map_reducer_error)?;

    let mut exact_retry_duplicates = 0_u64;
    let mut provisional_skips = 0_u64;
    for callback in &fixture.callbacks {
        if started.elapsed() > Duration::from_secs(MAX_RUNTIME_SECONDS) {
            return Err(RunnerFailure::policy("RUNTIME_BOUND_EXCEEDED"));
        }
        let outcome = match callback {
            CallbackInput::Transaction { transaction } => {
                let transaction = build_transaction(transaction)?;
                reducer
                    .observe_transaction(&transaction)
                    .map_err(map_reducer_error)?
            }
            CallbackInput::Block { block } => {
                let block = build_block(block);
                reducer.observe_block(&block).map_err(map_reducer_error)?
            }
            CallbackInput::PossibleLeaderSkipped {
                possible_leader_skipped,
            } => {
                provisional_skips += 1;
                reducer
                    .observe_block(&BlockData::PossibleLeaderSkipped {
                        slot: possible_leader_skipped.slot,
                    })
                    .map_err(map_reducer_error)?
            }
        };
        if matches!(callback, CallbackInput::Transaction { .. })
            && outcome == ObserveOutcome::Duplicate
        {
            exact_retry_duplicates += 1;
        }
    }
    drop(reducer);
    remove_namespace_lock(staging)?;

    publish_derived_artifacts(
        fixture,
        input_sha256,
        run_id,
        staging,
        exact_retry_duplicates,
        provisional_skips,
    )?;
    if started.elapsed() > Duration::from_secs(MAX_RUNTIME_SECONDS) {
        return Err(RunnerFailure::policy("RUNTIME_BOUND_EXCEEDED"));
    }
    if directory_size(staging)? > MAX_OUTPUT_BYTES {
        return Err(RunnerFailure::policy("OUTPUT_BOUND_EXCEEDED"));
    }
    make_tree_read_only(staging)?;
    verify_tree_read_only(staging)?;
    Ok(())
}

#[allow(clippy::needless_pass_by_value)]
fn map_reducer_error(error: crate::ReducerError) -> RunnerFailure {
    let message = error.to_string();
    if message == "conflicting_transaction_retry"
        || message == "conflicting_signature_identity"
        || message == "conflicting_block_retry"
    {
        RunnerFailure::policy("CONFLICTING_DUPLICATE")
    } else if message.starts_with("io:") {
        RunnerFailure::operational("REDUCER_OPERATIONAL_FAILURE")
    } else if message == "runtime_budget_exceeded" {
        RunnerFailure::policy("RUNTIME_BOUND_EXCEEDED")
    } else if message == "output_budget_exceeded" {
        RunnerFailure::policy("OUTPUT_BOUND_EXCEEDED")
    } else {
        RunnerFailure::policy("REDUCER_INPUT_REJECTED")
    }
}

#[allow(clippy::too_many_lines)]
fn validate_fixture(fixture: &FixtureInput) -> RunnerResult<()> {
    if fixture.schema_version != INPUT_SCHEMA {
        return Err(RunnerFailure::policy("INVALID_SCHEMA_VERSION"));
    }
    if fixture.source_class != SOURCE_CLASS {
        return Err(RunnerFailure::policy("SOURCE_CLASS_REJECTED"));
    }
    if fixture.observed_at != OBSERVED_AT {
        return Err(RunnerFailure::policy("INVALID_OBSERVED_AT"));
    }
    let expected_config = FixtureConfig {
        max_callbacks: MAX_CALLBACKS,
        max_slots: MAX_SLOTS,
        max_transactions_per_slot: MAX_TRANSACTIONS_PER_SLOT,
        max_runtime_seconds: MAX_RUNTIME_SECONDS,
        max_output_bytes: MAX_OUTPUT_BYTES,
    };
    if fixture.config != expected_config {
        return Err(RunnerFailure::policy("INVALID_BOUNDS"));
    }
    if fixture.callbacks.is_empty() || fixture.callbacks.len() > MAX_CALLBACKS {
        return Err(RunnerFailure::policy("CALLBACK_BOUND_EXCEEDED"));
    }
    let expected_observability = FixtureObservability {
        bytes_read: 18_000,
        bytes_written: 42_000,
        queue_depth: 2,
        peak_rss_bytes: 24_000_000,
        output_bytes: 42_000,
        stage_durations_ms: FixtureStageDurations {
            validate: 2,
            reduce: 5,
            publish: 3,
        },
        wal_status: "CLEAN".into(),
        checkpoint_status: "PUBLISHED".into(),
        quarantine_by_reason: FixtureQuarantineCounts {
            unknown_discriminator: 2,
            failed_transaction: 1,
        },
    };
    if fixture.observability != expected_observability {
        return Err(RunnerFailure::policy("INVALID_OBSERVABILITY_FIXTURE"));
    }
    let range = &fixture.expected_slot_range;
    let width = range
        .end_exclusive
        .checked_sub(range.start_inclusive)
        .ok_or_else(|| RunnerFailure::policy("INVALID_SLOT_RANGE"))?;
    if width == 0
        || width > MAX_SLOTS
        || range.start_inclusive != 432_000_100
        || range.end_exclusive != 432_000_103
        || range.start_inclusive < fixture.source_manifest.slot_range.start_inclusive
        || range.end_exclusive > fixture.source_manifest.slot_range.end_exclusive
    {
        return Err(RunnerFailure::policy("INVALID_SLOT_RANGE"));
    }
    let expected_schema_hash = sha256_bytes(format!("{INPUT_SCHEMA}\n").as_bytes());
    if fixture.source_manifest_sha256 != SOURCE_MANIFEST_SHA256
        || fixture.source_manifest_sha256 != canonical_sha256(&fixture.source_manifest)?
        || fixture.adapter_provenance_sha256 != ADAPTER_PROVENANCE_SHA256
        || fixture.adapter_provenance_sha256 != canonical_sha256(&fixture.adapter_provenance)?
        || fixture.reducer_provenance_sha256 != REDUCER_PROVENANCE_SHA256
        || fixture.reducer_provenance_sha256 != canonical_sha256(&fixture.reducer_provenance)?
        || fixture.config_sha256 != CONFIG_SHA256
        || fixture.config_sha256 != canonical_sha256(&fixture.config)?
        || fixture.schema_sha256 != SCHEMA_SHA256
        || fixture.schema_sha256 != expected_schema_hash
    {
        return Err(RunnerFailure::policy("HASH_MISMATCH"));
    }

    let mut slots = BTreeSet::new();
    let mut definitive_blocks = BTreeSet::new();
    for callback in &fixture.callbacks {
        let slot = match callback {
            CallbackInput::Transaction { transaction } => {
                validate_transaction_input(transaction)?;
                transaction.slot
            }
            CallbackInput::Block { block } => {
                if block.executed_transaction_count
                    > u64::try_from(MAX_TRANSACTIONS_PER_SLOT).expect("constant fits u64")
                    || block.parent_slot >= block.slot
                {
                    return Err(RunnerFailure::policy("INVALID_BLOCK"));
                }
                definitive_blocks.insert(block.slot);
                block.slot
            }
            CallbackInput::PossibleLeaderSkipped {
                possible_leader_skipped,
            } => possible_leader_skipped.slot,
        };
        if slot < range.start_inclusive || slot >= range.end_exclusive {
            return Err(RunnerFailure::policy("SLOT_OUT_OF_RANGE"));
        }
        slots.insert(slot);
    }
    let expected_slots = (range.start_inclusive..range.end_exclusive).collect::<BTreeSet<_>>();
    if slots != expected_slots || definitive_blocks != expected_slots {
        return Err(RunnerFailure::policy("INCOMPLETE_SLOT_COVERAGE"));
    }
    Ok(())
}

fn validate_transaction_input(transaction: &TransactionInput) -> RunnerResult<()> {
    if transaction.transaction_index >= MAX_TRANSACTIONS_PER_SLOT
        || transaction.static_account_keys.is_empty()
    {
        return Err(RunnerFailure::policy("INVALID_TRANSACTION"));
    }
    canonical_u64(&transaction.fee_lamports)?;
    for value in transaction
        .pre_balances_lamports
        .iter()
        .chain(&transaction.post_balances_lamports)
        .chain(
            transaction
                .pre_token_balances
                .iter()
                .chain(&transaction.post_token_balances)
                .map(|balance| &balance.amount),
        )
    {
        canonical_u64(value)?;
    }
    let resolved_count = transaction.static_account_keys.len()
        + transaction.loaded_addresses.writable.len()
        + transaction.loaded_addresses.readonly.len();
    if transaction.pre_balances_lamports.len() != resolved_count
        || transaction.post_balances_lamports.len() != resolved_count
    {
        return Err(RunnerFailure::policy("INVALID_TRANSACTION"));
    }
    match transaction.version {
        TransactionVersion::Legacy => {
            if !transaction.loaded_addresses.writable.is_empty()
                || !transaction.loaded_addresses.readonly.is_empty()
                || !transaction.address_table_lookups.is_empty()
            {
                return Err(RunnerFailure::policy("INVALID_TRANSACTION"));
            }
        }
        TransactionVersion::V0 => {
            let writable = transaction
                .address_table_lookups
                .iter()
                .map(|lookup| lookup.writable_indexes.len())
                .sum::<usize>();
            let readonly = transaction
                .address_table_lookups
                .iter()
                .map(|lookup| lookup.readonly_indexes.len())
                .sum::<usize>();
            if writable != transaction.loaded_addresses.writable.len()
                || readonly != transaction.loaded_addresses.readonly.len()
            {
                return Err(RunnerFailure::policy("INVALID_TRANSACTION"));
            }
        }
    }
    Ok(())
}

fn canonical_u64(value: &str) -> RunnerResult<u64> {
    value
        .parse::<u64>()
        .ok()
        .filter(|parsed| parsed.to_string() == value)
        .ok_or_else(|| RunnerFailure::policy("NONCANONICAL_INTEGER"))
}

fn canonical_sha256<T: Serialize>(value: &T) -> RunnerResult<String> {
    serde_json::to_vec(value)
        .map(|bytes| sha256_bytes(&bytes))
        .map_err(|_| RunnerFailure::policy("INVALID_INPUT_SCHEMA"))
}

fn build_transaction(input: &TransactionInput) -> RunnerResult<TransactionData> {
    let static_keys = parse_addresses(&input.static_account_keys)?;
    let loaded_writable = parse_addresses(&input.loaded_addresses.writable)?;
    let loaded_readonly = parse_addresses(&input.loaded_addresses.readonly)?;
    let instructions = input
        .instructions
        .iter()
        .map(build_compiled_instruction)
        .collect::<RunnerResult<Vec<_>>>()?;
    let message = match input.version {
        TransactionVersion::Legacy => VersionedMessage::Legacy(LegacyMessage {
            account_keys: static_keys,
            instructions,
            ..LegacyMessage::default()
        }),
        TransactionVersion::V0 => {
            let address_table_lookups = input
                .address_table_lookups
                .iter()
                .map(|lookup| {
                    Ok(MessageAddressTableLookup {
                        account_key: parse_address(&lookup.account_key)?,
                        writable_indexes: lookup.writable_indexes.clone(),
                        readonly_indexes: lookup.readonly_indexes.clone(),
                    })
                })
                .collect::<RunnerResult<Vec<_>>>()?;
            VersionedMessage::V0(V0Message {
                account_keys: static_keys,
                address_table_lookups,
                instructions,
                ..V0Message::default()
            })
        }
    };
    let signature = Signature::from([input.signature_byte; 64]);
    let inner_instructions = input
        .inner_instructions
        .iter()
        .map(|group| {
            Ok(InnerInstructions {
                index: group.parent_instruction_index,
                instructions: group
                    .instructions
                    .iter()
                    .map(|instruction| {
                        Ok(InnerInstruction {
                            instruction: CompiledInstruction {
                                program_id_index: instruction.program_id_index,
                                accounts: instruction.accounts.clone(),
                                data: decode_hex(&instruction.data_hex)?,
                            },
                            stack_height: Some(instruction.stack_height),
                        })
                    })
                    .collect::<RunnerResult<Vec<_>>>()?,
            })
        })
        .collect::<RunnerResult<Vec<_>>>()?;
    let status = match input.execution_status {
        ExecutionStatus::Succeeded => Ok(()),
        ExecutionStatus::Failed => Err(TransactionError::AccountNotFound),
    };
    Ok(TransactionData {
        slot: input.slot,
        transaction_slot_index: input.transaction_index,
        signature,
        message_hash: Hash::from([input.message_hash_byte; 32]),
        is_vote: input.is_vote,
        transaction_status_meta: TransactionStatusMeta {
            status,
            fee: canonical_u64(&input.fee_lamports)?,
            pre_balances: parse_u64_values(&input.pre_balances_lamports)?,
            post_balances: parse_u64_values(&input.post_balances_lamports)?,
            inner_instructions: Some(inner_instructions),
            log_messages: Some(input.log_messages.clone()),
            pre_token_balances: Some(build_token_balances(&input.pre_token_balances)?),
            post_token_balances: Some(build_token_balances(&input.post_token_balances)?),
            loaded_addresses: LoadedAddresses {
                writable: loaded_writable,
                readonly: loaded_readonly,
            },
            ..TransactionStatusMeta::default()
        },
        transaction: VersionedTransaction {
            signatures: vec![signature],
            message,
        },
    })
}

fn build_compiled_instruction(
    input: &CompiledInstructionInput,
) -> RunnerResult<CompiledInstruction> {
    Ok(CompiledInstruction {
        program_id_index: input.program_id_index,
        accounts: input.accounts.clone(),
        data: decode_hex(&input.data_hex)?,
    })
}

fn parse_addresses(values: &[String]) -> RunnerResult<Vec<Address>> {
    values.iter().map(|value| parse_address(value)).collect()
}

fn parse_address(value: &str) -> RunnerResult<Address> {
    Address::from_str(value).map_err(|_| RunnerFailure::policy("INVALID_ADDRESS"))
}

fn parse_u64_values(values: &[String]) -> RunnerResult<Vec<u64>> {
    values.iter().map(|value| canonical_u64(value)).collect()
}

fn build_token_balances(
    values: &[TokenBalanceInput],
) -> RunnerResult<Vec<TransactionTokenBalance>> {
    values
        .iter()
        .map(|value| {
            canonical_u64(&value.amount)?;
            Ok(TransactionTokenBalance {
                account_index: value.account_index,
                mint: value.mint.clone(),
                ui_token_amount: UiTokenAmount {
                    ui_amount: None,
                    decimals: value.decimals,
                    amount: value.amount.clone(),
                    ui_amount_string: value.amount.clone(),
                },
                owner: value.owner.clone(),
                program_id: value.program_id.clone(),
            })
        })
        .collect()
}

fn build_block(input: &BlockInput) -> BlockData {
    BlockData::Block {
        parent_slot: input.parent_slot,
        parent_blockhash: Hash::from([input.parent_blockhash_byte; 32]),
        slot: input.slot,
        blockhash: Hash::from([input.blockhash_byte; 32]),
        rewards: KeyedRewardsAndNumPartitions {
            keyed_rewards: vec![],
            num_partitions: None,
        },
        block_time: Some(input.block_time),
        block_height: input.block_height,
        executed_transaction_count: input.executed_transaction_count,
        entry_count: input.entry_count,
    }
}

fn decode_hex(value: &str) -> RunnerResult<Vec<u8>> {
    if !value.len().is_multiple_of(2) || !value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(RunnerFailure::policy("INVALID_HEX"));
    }
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| {
            let high = hex_nibble(pair[0])?;
            let low = hex_nibble(pair[1])?;
            Ok((high << 4) | low)
        })
        .collect()
}

fn hex_nibble(value: u8) -> RunnerResult<u8> {
    match value {
        b'0'..=b'9' => Ok(value - b'0'),
        b'a'..=b'f' => Ok(value - b'a' + 10),
        b'A'..=b'F' => Ok(value - b'A' + 10),
        _ => Err(RunnerFailure::policy("INVALID_HEX")),
    }
}

#[allow(clippy::too_many_lines)]
fn publish_derived_artifacts(
    fixture: &FixtureInput,
    input_sha256: &str,
    run_id: &str,
    staging: &Path,
    exact_retry_duplicates: u64,
    provisional_skips: u64,
) -> RunnerResult<()> {
    let reducer_dir = staging.join("reducer");
    let slots_dir = staging.join("slots");
    fs::create_dir(&slots_dir).map_err(|_| RunnerFailure::operational("OUTPUT_WRITE_FAILED"))?;
    let mut observations = Vec::new();
    let mut quarantines = Vec::new();
    let mut metrics = Metrics::default();
    let mut slot_hashes = BTreeMap::new();

    for slot in
        fixture.expected_slot_range.start_inclusive..fixture.expected_slot_range.end_exclusive
    {
        let reducer_slot = reducer_dir.join(format!("slots/{slot}.json"));
        let bytes = fs::read(&reducer_slot)
            .map_err(|_| RunnerFailure::policy("INCOMPLETE_SLOT_COVERAGE"))?;
        write_new(&slots_dir.join(format!("{slot}.bronze.json")), &bytes)?;
        slot_hashes.insert(slot.to_string(), sha256_bytes(&bytes));
        metrics.slots_committed += 1;
        collect_slot_semantics(&bytes, &mut metrics, &mut observations, &mut quarantines)?;
    }
    observations.sort_by(|left, right| value_key(left).cmp(value_key(right)));
    quarantines.sort_by(|left, right| value_key(left).cmp(value_key(right)));
    metrics.exact_retry_duplicates = exact_retry_duplicates;
    metrics.provisional_skips = provisional_skips;

    write_ndjson(&staging.join("event-observations.ndjson"), &observations)?;
    write_ndjson(&staging.join("quarantines.ndjson"), &quarantines)?;
    write_json(
        &staging.join("retry-duplicate-conflicts.json"),
        &json!({
            "schemaVersion": "PHASE8A_RETRY_DUPLICATE_CONFLICTS_1",
            "exactRetryDuplicates": exact_retry_duplicates,
            "conflictingDuplicates": 0,
            "evidenceBadge": "SYNTHETIC",
            "realData": false,
            "acceptedSilver": false,
            "researchReady": false
        }),
    )?;
    write_json(
        &staging.join("metrics-snapshot.json"),
        &serde_json::to_value(&metrics)
            .map_err(|_| RunnerFailure::operational("OUTPUT_WRITE_FAILED"))?,
    )?;

    let coverage_lines = fs::read_to_string(reducer_dir.join("coverage.ndjson"))
        .map_err(|_| RunnerFailure::operational("OUTPUT_READ_FAILED"))?;
    let mut coverage_records = coverage_lines
        .lines()
        .map(|line| {
            serde_json::from_str::<Value>(line)
                .map_err(|_| RunnerFailure::operational("OUTPUT_READ_FAILED"))
        })
        .collect::<RunnerResult<Vec<_>>>()?;
    coverage_records.sort_by_key(|record| record["slot"].as_u64().unwrap_or_default());
    if coverage_records.len() != usize::try_from(metrics.slots_committed).expect("count fits") {
        return Err(RunnerFailure::policy("INCOMPLETE_SLOT_COVERAGE"));
    }
    write_json(
        &staging.join("coverage.json"),
        &json!({
            "schemaVersion": "PHASE8A_BRONZE_COVERAGE_1",
            "expectedSlotRange": fixture.expected_slot_range,
            "transportCoverageStatus": "TRANSPORT_COVERAGE_PASS",
            "records": coverage_records,
            "slotContentSha256": slot_hashes,
            "evidenceBadge": "SYNTHETIC",
            "realData": false,
            "acceptedSilver": false,
            "researchReady": false
        }),
    )?;
    write_json(&staging.join("eligibility.json"), &eligibility_value())?;
    write_json(
        &staging.join("provenance.json"),
        &json!({
            "schemaVersion": "PHASE8A_BRONZE_PROVENANCE_1",
            "runId": run_id,
            "inputSha256": input_sha256,
            "observedAt": fixture.observed_at,
            "sourceClass": SOURCE_CLASS,
            "sourceManifest": fixture.source_manifest,
            "sourceManifestSha256": fixture.source_manifest_sha256,
            "adapterProvenance": fixture.adapter_provenance,
            "adapterProvenanceSha256": fixture.adapter_provenance_sha256,
            "reducerProvenance": fixture.reducer_provenance,
            "reducerProvenanceSha256": fixture.reducer_provenance_sha256,
            "configSha256": fixture.config_sha256,
            "schemaSha256": fixture.schema_sha256,
            "evidenceBadge": "SYNTHETIC",
            "realData": false,
            "acceptedSilver": false,
            "researchReady": false
        }),
    )?;
    write_json(
        &staging.join("run-manifest.json"),
        &json!({
            "schemaVersion": "PHASE8A_BRONZE_RUN_MANIFEST_1",
            "runId": run_id,
            "inputSha256": input_sha256,
            "sourceClass": SOURCE_CLASS,
            "observedAt": fixture.observed_at,
            "mode": "OFFLINE_FILE_INPUT_BRONZE_ONLY",
            "reducerAuthority": "PHASE5_REDUCER_OPEN_OBSERVE_TRANSACTION_OBSERVE_BLOCK",
            "retainedArtifactSet": "CORE_REGULAR_FILES_UNDER_OUTPUT_INCLUDING_DETERMINISTIC_REDUCER_CHECKPOINTS_AND_LOCK_EXCLUDING_COCKPIT_SNAPSHOT_AND_AGGREGATE_CONTENT_HASH",
            "aggregateHashBinding": "SORTED_RELATIVE_PATH_NUL_SHA256_NEWLINE",
            "evidenceBadge": "SYNTHETIC",
            "realData": false,
            "acceptedSilver": false,
            "researchReady": false
        }),
    )?;
    let rerun_sha256 = semantic_rerun_hash(staging, &fixture.expected_slot_range)?;
    let aggregate = aggregate_content_hash(staging)?;
    let cockpit = build_cockpit_snapshot(
        fixture,
        input_sha256,
        run_id,
        &aggregate,
        &rerun_sha256,
        &observations,
        exact_retry_duplicates,
        provisional_skips,
    );
    write_json(&staging.join("cockpit-snapshot.json"), &cockpit)?;
    write_new(
        &staging.join("aggregate-content-hash.txt"),
        format!("{aggregate}\n").as_bytes(),
    )?;
    Ok(())
}

fn eligibility_value() -> Value {
    json!({
        "schemaVersion": "PHASE8A_ELIGIBILITY_STATUS_1",
        "pilotEligible": false,
        "transportPilot": {
            "contractReady": true,
            "inputMode": "SYNTHETIC_FIXTURE_ONLY",
            "preflightStatus": "NOT_RUN",
            "eligible": false,
            "executionAuthorized": false,
            "reasonCodes": ["SYNTHETIC_FIXTURE_ONLY", "PREFLIGHT_NOT_RUN", "EXECUTION_NOT_AUTHORIZED"]
        },
        "acceptedSilver": {
            "eligible": false,
            "activationVerdict": "HOLD_UNPROVEN_ACTIVATION",
            "provenRegistryEntries": 0,
            "totalRegistryEntries": 10,
            "reasonCodes": ["NO_PROVEN_REGISTRY_ENTRIES", "UNPROVEN_ACTIVATION"]
        },
        "research": {
            "approved": false,
            "researchReady": false,
            "strategyInputEligible": false,
            "profitabilityEvidence": false,
            "reasonCodes": ["FIXTURE_ONLY", "NOT_APPROVED", "NOT_STRATEGY_INPUT"]
        }
    })
}

#[allow(clippy::too_many_arguments, clippy::too_many_lines)]
fn build_cockpit_snapshot(
    fixture: &FixtureInput,
    input_sha256: &str,
    run_id: &str,
    aggregate_output_sha256: &str,
    rerun_sha256: &str,
    observations: &[Value],
    exact_retry_duplicates: u64,
    provisional_skips: u64,
) -> Value {
    let mut events = Vec::new();
    let mut cockpit_quarantines = Vec::new();
    for observation in observations.iter().take(1_000) {
        let candidate = &observation["candidate"];
        let discriminator = candidate["discriminatorHex"].as_str().unwrap_or_default();
        let execution_status = candidate["executionStatus"].as_str().unwrap_or_default();
        let quarantine_reason = if execution_status == "failed" {
            Some("FAILED_TRANSACTION")
        } else if candidate["parserStatus"] == "quarantined" {
            Some("UNKNOWN_DISCRIMINATOR")
        } else {
            None
        };
        let structural_variant = candidate["variant"].as_str().unwrap_or("unknown");
        let mut event = serde_json::Map::from_iter([
            ("schemaVersion".into(), json!("PHASE8A_EVENT_ROW_1")),
            ("slot".into(), observation["slot"].clone()),
            (
                "transactionIndex".into(),
                observation["transactionIndex"].clone(),
            ),
            ("signature".into(), observation["signature"].clone()),
            (
                "instructionLocation".into(),
                candidate["instructionLocation"].clone(),
            ),
            (
                "instructionIndex".into(),
                candidate["instructionIndex"].clone(),
            ),
            ("observedDiscriminator".into(), json!(discriminator)),
            ("structuralVariant".into(), json!(structural_variant)),
            ("executionStatus".into(), json!(execution_status)),
            (
                "evidenceBadge".into(),
                json!(if quarantine_reason.is_some() {
                    "QUARANTINED"
                } else {
                    "SHADOW_STRUCTURAL_OBSERVATION"
                }),
            ),
            ("quarantineReason".into(), json!(quarantine_reason)),
            (
                "rawDetail".into(),
                json!({
                    "source": "SYNTHETIC",
                    "realData": false,
                    "acceptedSilver": false,
                    "researchReady": false,
                    "strategyStatus": "NOT_STRATEGY_INPUT",
                    "activation": "UNPROVEN_ACTIVATION"
                }),
            ),
        ]);
        if !candidate["parentInstructionIndex"].is_null() {
            event.insert(
                "parentInstructionIndex".into(),
                candidate["parentInstructionIndex"].clone(),
            );
        }
        if let Some(reason) = quarantine_reason {
            cockpit_quarantines.push(json!({
                "schemaVersion": "PHASE8A_QUARANTINE_ROW_1",
                "slot": observation["slot"],
                "transactionIndex": observation["transactionIndex"],
                "reason": reason,
                "evidenceBadge": "QUARANTINED"
            }));
        }
        events.push(Value::Object(event));
    }

    let mut transaction_coordinates = BTreeSet::new();
    let mut transaction_count = 0_u64;
    let mut top_level_instructions = 0_u64;
    let mut inner_instructions = 0_u64;
    let mut blocks = 0_u64;
    for callback in &fixture.callbacks {
        match callback {
            CallbackInput::Transaction { transaction } => {
                if transaction_coordinates.insert((
                    transaction.slot,
                    transaction.transaction_index,
                    transaction.signature_byte,
                )) {
                    transaction_count += 1;
                    top_level_instructions +=
                        u64::try_from(transaction.instructions.len()).unwrap_or(u64::MAX);
                    inner_instructions += transaction
                        .inner_instructions
                        .iter()
                        .map(|group| u64::try_from(group.instructions.len()).unwrap_or(u64::MAX))
                        .sum::<u64>();
                }
            }
            CallbackInput::Block { .. } => blocks += 1,
            CallbackInput::PossibleLeaderSkipped { .. } => {}
        }
    }
    let requested_slots =
        fixture.expected_slot_range.end_exclusive - fixture.expected_slot_range.start_inclusive;
    let last_slot = fixture.expected_slot_range.end_exclusive - 1;
    let unknown_discriminators = events
        .iter()
        .filter(|event| event["quarantineReason"] == "UNKNOWN_DISCRIMINATOR")
        .count();

    json!({
        "schemaVersion": "PHASE8A_COCKPIT_SNAPSHOT_1",
        "sourceClass": SOURCE_CLASS,
        "runId": run_id,
        "observedAt": fixture.observed_at,
        "evidenceClass": "SYNTHETIC",
        "realData": false,
        "acceptedSilver": false,
        "researchReady": false,
        "activationVerdict": "HOLD_UNPROVEN_ACTIVATION",
        "eligibility": eligibility_value(),
        "progress": {
            "requestedSlots": requested_slots,
            "reconciledSlots": requested_slots,
            "skippedSlots": provisional_skips,
            "provisionalSlots": provisional_skips,
            "resolvedSlots": provisional_skips,
            "currentSlot": last_slot,
            "lastCompletedSlot": last_slot,
            "coveragePercent": 100,
            "deterministicRerun": "MATCH"
        },
        "dataflow": {
            "callbacks": fixture.callbacks.len(),
            "blocks": blocks,
            "transactions": transaction_count,
            "topLevelInstructions": top_level_instructions,
            "innerInstructions": inner_instructions,
            "pumpCandidates": observations.len(),
            "failedPumpTransactions": cockpit_quarantines.iter().filter(|row| row["reason"] == "FAILED_TRANSACTION").count(),
            "unknownDiscriminators": unknown_discriminators,
            "quarantines": cockpit_quarantines.len(),
            "exactRetries": exact_retry_duplicates,
            "duplicateConflicts": 0
        },
        "events": events,
        "quarantines": cockpit_quarantines,
        "provenance": {
            "schemaVersion": "PHASE8A_PROVENANCE_1",
            "sourceManifestSha256": fixture.source_manifest_sha256,
            "configSha256": fixture.config_sha256,
            "schemaSha256": fixture.schema_sha256,
            "reducerGitSha": fixture.reducer_provenance.reducer_git_sha,
            "inputSha256": input_sha256,
            "aggregateOutputSha256": aggregate_output_sha256,
            "rerunSha256": rerun_sha256,
            "approvalStatus": "CANDIDATE_UNAPPROVED",
            "completeness": "FIXTURE_COMPLETE",
            "uncertainty": "UNPROVEN_ACTIVATION"
        },
        "resources": {
            "bytesRead": fixture.observability.bytes_read,
            "bytesWritten": fixture.observability.bytes_written,
            "queueDepth": fixture.observability.queue_depth,
            "peakRssBytes": fixture.observability.peak_rss_bytes,
            "outputBytes": fixture.observability.output_bytes,
            "stageDurationsMs": fixture.observability.stage_durations_ms,
            "walStatus": fixture.observability.wal_status,
            "checkpointStatus": fixture.observability.checkpoint_status,
            "quarantineByReason": fixture.observability.quarantine_by_reason,
            "evidenceBasis": "FIXTURE_PINNED_NOT_MEASURED"
        }
    })
}

fn semantic_rerun_hash(root: &Path, range: &SlotRange) -> RunnerResult<String> {
    let mut relative_paths = vec![
        "coverage.json".to_owned(),
        "eligibility.json".to_owned(),
        "event-observations.ndjson".to_owned(),
        "metrics-snapshot.json".to_owned(),
        "quarantines.ndjson".to_owned(),
        "retry-duplicate-conflicts.json".to_owned(),
    ];
    relative_paths.extend(
        (range.start_inclusive..range.end_exclusive)
            .map(|slot| format!("slots/{slot}.bronze.json")),
    );
    let mut bindings = Vec::new();
    for relative in relative_paths {
        let bytes = fs::read(root.join(&relative))
            .map_err(|_| RunnerFailure::operational("OUTPUT_READ_FAILED"))?;
        bindings.push(format!("{relative}\0{}\n", sha256_bytes(&bytes)));
    }
    bindings.sort();
    Ok(sha256_bytes(bindings.concat().as_bytes()))
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct Metrics {
    schema_version: &'static str,
    slots_committed: u64,
    legacy_transactions: u64,
    v0_transactions: u64,
    top_level_pump_instructions: u64,
    inner_cpi_pump_instructions: u64,
    create_discriminator_observations: u64,
    buy_discriminator_observations: u64,
    sell_discriminator_observations: u64,
    failed_pump_transactions: u64,
    unknown_discriminators: u64,
    exact_retry_duplicates: u64,
    provisional_skips: u64,
    evidence_badge: EvidenceBadge,
    real_data: bool,
    accepted_silver: bool,
    research_ready: bool,
}

impl Default for Metrics {
    fn default() -> Self {
        Self {
            schema_version: "PHASE8A_METRICS_SNAPSHOT_1",
            slots_committed: 0,
            legacy_transactions: 0,
            v0_transactions: 0,
            top_level_pump_instructions: 0,
            inner_cpi_pump_instructions: 0,
            create_discriminator_observations: 0,
            buy_discriminator_observations: 0,
            sell_discriminator_observations: 0,
            failed_pump_transactions: 0,
            unknown_discriminators: 0,
            exact_retry_duplicates: 0,
            provisional_skips: 0,
            evidence_badge: EvidenceBadge::Synthetic,
            real_data: false,
            accepted_silver: false,
            research_ready: false,
        }
    }
}

#[derive(Debug, Default, Serialize)]
enum EvidenceBadge {
    #[default]
    #[serde(rename = "SYNTHETIC")]
    Synthetic,
}

fn collect_slot_semantics(
    bytes: &[u8],
    metrics: &mut Metrics,
    observations: &mut Vec<Value>,
    quarantines: &mut Vec<Value>,
) -> RunnerResult<()> {
    let slot: Value = serde_json::from_slice(bytes)
        .map_err(|_| RunnerFailure::operational("OUTPUT_READ_FAILED"))?;
    let slot_number = slot["slot"]
        .as_u64()
        .ok_or_else(|| RunnerFailure::operational("OUTPUT_READ_FAILED"))?;
    let slot_observations = slot["observations"]
        .as_array()
        .ok_or_else(|| RunnerFailure::operational("OUTPUT_READ_FAILED"))?;
    for observation in slot_observations {
        let static_count = observation["staticAccountCount"]
            .as_u64()
            .unwrap_or_default();
        let bronze = &observation["bronze"];
        let account_count = bronze["accountKeys"].as_array().map_or(0, Vec::len);
        if usize::try_from(static_count).ok() == Some(account_count) {
            metrics.legacy_transactions += 1;
        } else {
            metrics.v0_transactions += 1;
        }
        let failed = bronze["executionStatus"] == "failed";
        let transaction_index = bronze["transactionIndex"].as_u64().unwrap_or_default();
        let signature = bronze["signature"].as_str().unwrap_or_default();
        let candidates = bronze["pumpCandidates"]
            .as_array()
            .ok_or_else(|| RunnerFailure::operational("OUTPUT_READ_FAILED"))?;
        if failed && !candidates.is_empty() {
            metrics.failed_pump_transactions += 1;
        }
        for candidate in candidates {
            if candidate["instructionLocation"] == "top_level" {
                metrics.top_level_pump_instructions += 1;
            } else {
                metrics.inner_cpi_pump_instructions += 1;
            }
            match candidate["discriminatorHex"].as_str().unwrap_or_default() {
                "181ec828051c0777" => metrics.create_discriminator_observations += 1,
                "66063d1201daebea" => metrics.buy_discriminator_observations += 1,
                "33e685a4017f83ad" => metrics.sell_discriminator_observations += 1,
                _ => {}
            }
            if candidate["parserStatus"] == "quarantined" {
                metrics.unknown_discriminators += 1;
            }
            let event_key = candidate["eventKey"].as_str().unwrap_or_default();
            observations.push(json!({
                "schemaVersion": "PHASE8A_SHADOW_EVENT_OBSERVATION_1",
                "eventKey": event_key,
                "slot": slot_number,
                "transactionIndex": transaction_index,
                "signature": signature,
                "candidate": candidate,
                "observationClass": "SHADOW_STRUCTURAL_OBSERVATION",
                "activationStatus": "UNPROVEN_ACTIVATION",
                "silverStatus": "NOT_ACCEPTED_SILVER",
                "strategyStatus": "NOT_STRATEGY_INPUT",
                "evidenceBadge": "SYNTHETIC",
                "realData": false,
                "acceptedSilver": false,
                "researchReady": false
            }));
            if candidate["parserStatus"] == "quarantined" || failed {
                quarantines.push(json!({
                    "schemaVersion": "PHASE8A_BRONZE_QUARANTINE_1",
                    "eventKey": event_key,
                    "slot": slot_number,
                    "transactionIndex": transaction_index,
                    "reason": if failed { "FAILED_PUMP_TRANSACTION" } else { "UNKNOWN_PUMP_DISCRIMINATOR" },
                    "observationClass": "SHADOW_STRUCTURAL_OBSERVATION",
                    "activationStatus": "UNPROVEN_ACTIVATION",
                    "silverStatus": "NOT_ACCEPTED_SILVER",
                    "strategyStatus": "NOT_STRATEGY_INPUT",
                    "evidenceBadge": "SYNTHETIC",
                    "realData": false,
                    "acceptedSilver": false,
                    "researchReady": false
                }));
            }
        }
    }
    Ok(())
}

fn value_key(value: &Value) -> &str {
    value["eventKey"].as_str().unwrap_or_default()
}

fn write_json(path: &Path, value: &Value) -> RunnerResult<()> {
    let mut bytes =
        serde_json::to_vec(value).map_err(|_| RunnerFailure::operational("OUTPUT_WRITE_FAILED"))?;
    bytes.push(b'\n');
    write_new(path, &bytes)
}

fn write_ndjson(path: &Path, values: &[Value]) -> RunnerResult<()> {
    let mut bytes = Vec::new();
    for value in values {
        serde_json::to_writer(&mut bytes, value)
            .map_err(|_| RunnerFailure::operational("OUTPUT_WRITE_FAILED"))?;
        bytes.push(b'\n');
    }
    write_new(path, &bytes)
}

fn write_new(path: &Path, bytes: &[u8]) -> RunnerResult<()> {
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|_| RunnerFailure::operational("OUTPUT_WRITE_FAILED"))?;
    file.write_all(bytes)
        .and_then(|()| file.sync_all())
        .map_err(|_| RunnerFailure::operational("OUTPUT_WRITE_FAILED"))?;
    let parent = path
        .parent()
        .ok_or_else(|| RunnerFailure::operational("OUTPUT_WRITE_FAILED"))?;
    sync_directory(parent)
}

fn aggregate_content_hash(root: &Path) -> RunnerResult<String> {
    let mut files = regular_file_paths(root)?;
    files.retain(|path| {
        !matches!(
            path.file_name().and_then(|name| name.to_str()),
            Some("aggregate-content-hash.txt" | "cockpit-snapshot.json")
        )
    });
    let mut bindings = Vec::new();
    for path in files {
        let relative = path
            .strip_prefix(root)
            .map_err(|_| RunnerFailure::operational("OUTPUT_READ_FAILED"))?
            .to_string_lossy();
        let bytes =
            fs::read(&path).map_err(|_| RunnerFailure::operational("OUTPUT_READ_FAILED"))?;
        bindings.push(format!("{relative}\0{}\n", sha256_bytes(&bytes)));
    }
    bindings.sort();
    Ok(sha256_bytes(bindings.concat().as_bytes()))
}

fn regular_file_paths(root: &Path) -> RunnerResult<Vec<PathBuf>> {
    let mut pending = vec![root.to_path_buf()];
    let mut files = Vec::new();
    while let Some(directory) = pending.pop() {
        for entry in
            fs::read_dir(directory).map_err(|_| RunnerFailure::operational("OUTPUT_READ_FAILED"))?
        {
            let entry = entry.map_err(|_| RunnerFailure::operational("OUTPUT_READ_FAILED"))?;
            let file_type = entry
                .file_type()
                .map_err(|_| RunnerFailure::operational("OUTPUT_READ_FAILED"))?;
            if file_type.is_dir() {
                pending.push(entry.path());
            } else if file_type.is_file() && !file_type.is_symlink() {
                files.push(entry.path());
            } else {
                return Err(RunnerFailure::operational("UNSAFE_PUBLISHED_FILE"));
            }
        }
    }
    files.sort();
    Ok(files)
}

fn directory_size(root: &Path) -> RunnerResult<u64> {
    regular_file_paths(root)?
        .into_iter()
        .try_fold(0_u64, |total, path| {
            let length = fs::metadata(path)
                .map_err(|_| RunnerFailure::operational("OUTPUT_READ_FAILED"))?
                .len();
            total
                .checked_add(length)
                .ok_or_else(|| RunnerFailure::policy("OUTPUT_BOUND_EXCEEDED"))
        })
}

fn make_tree_read_only(root: &Path) -> RunnerResult<()> {
    let files = regular_file_paths(root)?;
    for path in files {
        let mut permissions = fs::metadata(&path)
            .map_err(|_| RunnerFailure::operational("IMMUTABILITY_FAILED"))?
            .permissions();
        permissions.set_readonly(true);
        fs::set_permissions(path, permissions)
            .map_err(|_| RunnerFailure::operational("IMMUTABILITY_FAILED"))?;
    }
    sync_tree_directories(root)
}

fn verify_tree_read_only(root: &Path) -> RunnerResult<()> {
    #[cfg(unix)]
    use std::os::unix::fs::PermissionsExt as _;

    for path in regular_file_paths(root)? {
        let metadata =
            fs::metadata(&path).map_err(|_| RunnerFailure::operational("IMMUTABILITY_FAILED"))?;
        #[cfg(unix)]
        if metadata.permissions().mode() & 0o222 != 0 {
            return Err(RunnerFailure::operational("IMMUTABILITY_FAILED"));
        }
        if OpenOptions::new().append(true).open(&path).is_ok() {
            return Err(RunnerFailure::operational("IMMUTABILITY_FAILED"));
        }
    }
    for directory in tree_directories(root)? {
        let metadata = fs::metadata(&directory)
            .map_err(|_| RunnerFailure::operational("IMMUTABILITY_FAILED"))?;
        #[cfg(unix)]
        if metadata.permissions().mode() & 0o222 != 0 {
            return Err(RunnerFailure::operational("IMMUTABILITY_FAILED"));
        }
        let probe = directory.join(".phase8a-write-probe");
        if OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&probe)
            .is_ok()
        {
            let _ = fs::remove_file(&probe);
            return Err(RunnerFailure::operational("IMMUTABILITY_FAILED"));
        }
    }
    Ok(())
}

fn tree_directories(root: &Path) -> RunnerResult<Vec<PathBuf>> {
    let mut pending = vec![root.to_path_buf()];
    let mut directories = Vec::new();
    while let Some(directory) = pending.pop() {
        directories.push(directory.clone());
        for entry in fs::read_dir(&directory)
            .map_err(|_| RunnerFailure::operational("OUTPUT_READ_FAILED"))?
        {
            let entry = entry.map_err(|_| RunnerFailure::operational("OUTPUT_READ_FAILED"))?;
            if entry
                .file_type()
                .map_err(|_| RunnerFailure::operational("OUTPUT_READ_FAILED"))?
                .is_dir()
            {
                pending.push(entry.path());
            }
        }
    }
    Ok(directories)
}

fn sync_tree_directories(root: &Path) -> RunnerResult<()> {
    let mut pending = vec![root.to_path_buf()];
    let mut directories = Vec::new();
    while let Some(directory) = pending.pop() {
        directories.push(directory.clone());
        for entry in fs::read_dir(&directory)
            .map_err(|_| RunnerFailure::operational("OUTPUT_READ_FAILED"))?
        {
            let entry = entry.map_err(|_| RunnerFailure::operational("OUTPUT_READ_FAILED"))?;
            if entry
                .file_type()
                .map_err(|_| RunnerFailure::operational("OUTPUT_READ_FAILED"))?
                .is_dir()
            {
                pending.push(entry.path());
            }
        }
    }
    directories.sort_by_key(|path| std::cmp::Reverse(path.components().count()));
    for directory in directories {
        sync_directory(&directory)?;
        let mut permissions = fs::metadata(&directory)
            .map_err(|_| RunnerFailure::operational("IMMUTABILITY_FAILED"))?
            .permissions();
        permissions.set_readonly(true);
        fs::set_permissions(&directory, permissions)
            .map_err(|_| RunnerFailure::operational("IMMUTABILITY_FAILED"))?;
    }
    Ok(())
}

fn rollback_publication(final_path: &Path, staging: &Path, parent: &Path) -> RunnerResult<()> {
    let cleanup_path = if fs::rename(final_path, staging).is_ok() {
        staging
    } else {
        final_path
    };
    make_tree_writable(cleanup_path)?;
    fs::remove_dir_all(cleanup_path)
        .map_err(|_| RunnerFailure::operational("PUBLICATION_ROLLBACK_FAILED"))?;
    let _ = sync_directory(parent);
    if final_path.symlink_metadata().is_ok() {
        return Err(RunnerFailure::operational("PUBLICATION_ROLLBACK_FAILED"));
    }
    Ok(())
}

fn make_tree_writable(root: &Path) -> RunnerResult<()> {
    #[cfg(unix)]
    use std::os::unix::fs::PermissionsExt as _;

    let metadata = fs::symlink_metadata(root)
        .map_err(|_| RunnerFailure::operational("PUBLICATION_ROLLBACK_FAILED"))?;
    let mut permissions = metadata.permissions();
    #[cfg(unix)]
    permissions.set_mode(if metadata.is_dir() { 0o700 } else { 0o600 });
    #[cfg(not(unix))]
    permissions.set_readonly(false);
    fs::set_permissions(root, permissions)
        .map_err(|_| RunnerFailure::operational("PUBLICATION_ROLLBACK_FAILED"))?;
    if metadata.is_dir() {
        for entry in fs::read_dir(root)
            .map_err(|_| RunnerFailure::operational("PUBLICATION_ROLLBACK_FAILED"))?
        {
            make_tree_writable(
                &entry
                    .map_err(|_| RunnerFailure::operational("PUBLICATION_ROLLBACK_FAILED"))?
                    .path(),
            )?;
        }
    }
    Ok(())
}

fn sync_directory(path: &Path) -> RunnerResult<()> {
    File::open(path)
        .and_then(|file| file.sync_all())
        .map_err(|_| RunnerFailure::operational("OUTPUT_SYNC_FAILED"))
}

fn descriptor_read_to_eof(path: &Path, maximum: u64, started: &Instant) -> RunnerResult<Vec<u8>> {
    #[cfg(unix)]
    use std::os::unix::fs::MetadataExt as _;

    let mut file = File::open(path).map_err(|_| RunnerFailure::policy("UNSAFE_INPUT_PATH"))?;
    let descriptor_metadata = file
        .metadata()
        .map_err(|_| RunnerFailure::policy("UNSAFE_INPUT_PATH"))?;
    if !descriptor_metadata.file_type().is_file() {
        return Err(RunnerFailure::policy("UNSAFE_INPUT_PATH"));
    }
    if descriptor_metadata.len() > maximum {
        return Err(RunnerFailure::policy("INPUT_TOO_LARGE"));
    }
    let path_metadata =
        fs::symlink_metadata(path).map_err(|_| RunnerFailure::policy("UNSAFE_INPUT_PATH"))?;
    if path_metadata.file_type().is_symlink() || !path_metadata.file_type().is_file() {
        return Err(RunnerFailure::policy("UNSAFE_INPUT_PATH"));
    }
    #[cfg(unix)]
    if path_metadata.dev() != descriptor_metadata.dev()
        || path_metadata.ino() != descriptor_metadata.ino()
    {
        return Err(RunnerFailure::policy("UNSAFE_INPUT_PATH"));
    }

    let capacity = usize::try_from(descriptor_metadata.len().min(maximum))
        .map_err(|_| RunnerFailure::policy("INPUT_TOO_LARGE"))?;
    let mut retained = Vec::with_capacity(capacity);
    let mut buffer = [0_u8; 16 * 1024];
    let mut total = 0_u64;
    loop {
        if started.elapsed() > Duration::from_secs(MAX_RUNTIME_SECONDS) {
            return Err(RunnerFailure::policy("RUNTIME_BOUND_EXCEEDED"));
        }
        let count = file
            .read(&mut buffer)
            .map_err(|_| RunnerFailure::policy("INPUT_READ_FAILED"))?;
        if count == 0 {
            break;
        }
        total = total
            .checked_add(u64::try_from(count).expect("buffer count fits u64"))
            .ok_or_else(|| RunnerFailure::policy("INPUT_TOO_LARGE"))?;
        if total > maximum {
            return Err(RunnerFailure::policy("INPUT_TOO_LARGE"));
        }
        retained.extend_from_slice(&buffer[..count]);
    }
    let final_metadata = file
        .metadata()
        .map_err(|_| RunnerFailure::policy("UNSAFE_INPUT_PATH"))?;
    if total != descriptor_metadata.len() || final_metadata.len() != descriptor_metadata.len() {
        return Err(RunnerFailure::policy("INPUT_CHANGED"));
    }
    #[cfg(unix)]
    if final_metadata.dev() != descriptor_metadata.dev()
        || final_metadata.ino() != descriptor_metadata.ino()
        || final_metadata.mtime() != descriptor_metadata.mtime()
        || final_metadata.mtime_nsec() != descriptor_metadata.mtime_nsec()
        || final_metadata.ctime() != descriptor_metadata.ctime()
        || final_metadata.ctime_nsec() != descriptor_metadata.ctime_nsec()
    {
        return Err(RunnerFailure::policy("INPUT_CHANGED"));
    }
    let final_path_metadata =
        fs::symlink_metadata(path).map_err(|_| RunnerFailure::policy("INPUT_CHANGED"))?;
    if final_path_metadata.file_type().is_symlink() || !final_path_metadata.file_type().is_file() {
        return Err(RunnerFailure::policy("INPUT_CHANGED"));
    }
    #[cfg(unix)]
    if final_path_metadata.dev() != descriptor_metadata.dev()
        || final_path_metadata.ino() != descriptor_metadata.ino()
    {
        return Err(RunnerFailure::policy("INPUT_CHANGED"));
    }
    Ok(retained)
}

fn validate_input_path(path: &Path) -> RunnerResult<()> {
    validate_existing_ancestors(path, true, "UNSAFE_INPUT_PATH")
}

fn validate_output_path(path: &Path) -> RunnerResult<()> {
    if path.symlink_metadata().is_ok() {
        return Err(RunnerFailure::policy("OUTPUT_EXISTS"));
    }
    validate_existing_ancestors(path, false, "UNSAFE_OUTPUT_PATH")?;
    let parent = path
        .parent()
        .ok_or_else(|| RunnerFailure::policy("UNSAFE_OUTPUT_PATH"))?;
    if !parent.is_dir() {
        return Err(RunnerFailure::policy("UNSAFE_OUTPUT_PATH"));
    }
    let mut current = Some(parent);
    while let Some(directory) = current {
        if directory.join(".git").symlink_metadata().is_ok() {
            return Err(RunnerFailure::policy("WORKTREE_OUTPUT_REJECTED"));
        }
        current = directory.parent();
    }
    Ok(())
}

fn validate_existing_ancestors(
    path: &Path,
    require_final: bool,
    reason: &'static str,
) -> RunnerResult<()> {
    #[cfg(unix)]
    use std::os::unix::fs::MetadataExt as _;

    #[cfg(unix)]
    let current_uid = fs::metadata("/proc/self")
        .map_err(|_| RunnerFailure::policy(reason))?
        .uid();
    let mut current = PathBuf::new();
    for component in path.components() {
        current.push(component.as_os_str());
        match fs::symlink_metadata(&current) {
            Ok(metadata) => {
                if metadata.file_type().is_symlink() {
                    return Err(RunnerFailure::policy(reason));
                }
                let is_final = current == path;
                if !is_final && !metadata.file_type().is_dir() {
                    return Err(RunnerFailure::policy(reason));
                }
                #[cfg(unix)]
                if metadata.file_type().is_dir() {
                    let mode = metadata.mode();
                    let broadly_writable = mode & 0o022 != 0;
                    let sticky = mode & 0o1000 != 0;
                    if broadly_writable && metadata.uid() != current_uid && !sticky {
                        return Err(RunnerFailure::policy(reason));
                    }
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound && !require_final => break,
            Err(_) => return Err(RunnerFailure::policy(reason)),
        }
    }
    Ok(())
}

fn absolute_normalized(path: &Path) -> RunnerResult<PathBuf> {
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()
            .map_err(|_| RunnerFailure::operational("CURRENT_DIRECTORY_FAILED"))?
            .join(path)
    };
    let mut normalized = PathBuf::new();
    for component in absolute.components() {
        match component {
            Component::RootDir | Component::Prefix(_) => normalized.push(component.as_os_str()),
            Component::CurDir => {}
            Component::ParentDir => {
                if !normalized.pop() {
                    return Err(RunnerFailure::policy("UNSAFE_PATH"));
                }
            }
            Component::Normal(value) => normalized.push(value),
        }
    }
    if !normalized.is_absolute() {
        return Err(RunnerFailure::policy("UNSAFE_PATH"));
    }
    Ok(normalized)
}

fn remove_namespace_lock(reducer_parent: &Path) -> RunnerResult<()> {
    use std::os::unix::ffi::OsStrExt as _;

    let reducer_dir = reducer_parent.join("reducer");
    let parent = reducer_dir
        .parent()
        .ok_or_else(|| RunnerFailure::operational("LOCK_CLEANUP_FAILED"))?;
    let digest = sha256_bytes(reducer_dir.as_os_str().as_bytes());
    let lock = parent.join(format!(".old-faithful-pump-reducer-{digest}.lock"));
    if lock.exists() {
        fs::remove_file(lock).map_err(|_| RunnerFailure::operational("LOCK_CLEANUP_FAILED"))?;
        sync_directory(parent)?;
    }
    Ok(())
}

fn sha256_bytes(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
