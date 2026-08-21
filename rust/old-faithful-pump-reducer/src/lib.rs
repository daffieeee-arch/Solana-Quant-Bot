use std::{
    collections::{BTreeMap, HashMap},
    fs::{self, File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    str::FromStr,
    time::Instant,
};

use base64::{Engine, engine::general_purpose::STANDARD as BASE64};
use fs2::FileExt;
use jetstreamer_firehose::firehose::{BlockData, TransactionData};
#[cfg(target_os = "linux")]
use linux_kernel_namespace_lock::NamespaceLock;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use solana_address::Address;
use solana_message::{VersionedMessage, compiled_instruction::CompiledInstruction};
use solana_transaction_status::TransactionTokenBalance;
use thiserror::Error;
use time::{OffsetDateTime, macros::format_description};

mod phase8a_runner;
pub use phase8a_runner::phase8a_bronze_runner_main;
mod silver_event;
pub use silver_event::{SilverEventError, decode_pump_silver_event_hex};
mod silver_state;
pub use silver_state::evaluate_pump_silver_state_fixture;

#[cfg(unix)]
use std::os::unix::fs::MetadataExt;

pub const JETSTREAMER_V0_7_0_GIT_SHA: &str = "cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24";
pub const JETSTREAMER_V0_7_0_FIREHOSE_RS_SHA256: &str =
    "572ec56122e898f2318adc34d5998296b4fa0d31cf4bcab40950f5615a526a00";
const SOLANA_RUNTIME_V3_1_12_CRATE_CHECKSUM: &str =
    "0eb482d0c82ee826621758e7fdba331cd78857a94107247a2674216abb4ff808";
const SOLANA_RUNTIME_V3_1_12_REWARDS_SOURCE_SHA256: &str =
    "58d97efa198e8997f72681b7def92a5cee1ef859902047c5a05fccafe90199db";
const PUMP_PROGRAM_ID: &str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
const MAX_RESOLVED_ACCOUNT_KEYS: usize = 256;
const MAX_TOP_LEVEL_INSTRUCTIONS: usize = 256;
const MAX_INNER_INSTRUCTIONS: usize = 4_096;
const MAX_INSTRUCTION_ACCOUNT_REFERENCES: usize = 4_096;
const MAX_LOG_MESSAGES: usize = 1_024;
const MAX_LOG_BYTES: usize = 10_000;
const MAX_INSTRUCTION_DATA_BYTES: usize = 1_232;
const MAX_SLOTS_FILE_BYTES: u64 = 16 * 1_024 * 1_024;
const CHECKPOINT_SCHEMA: &str = "OLD_FAITHFUL_RUST_REDUCER_CHECKPOINT_1";
const WAL_SCHEMA: &str = "OLD_FAITHFUL_RUST_REDUCER_WAL_1";
const COVERAGE_SCHEMA: &str = "OLD_FAITHFUL_RUST_REDUCER_COVERAGE_1";
const MAX_CHECKPOINT_FILES: usize = 3;
const HARD_MAX_PENDING_SLOTS: usize = 4_096;
const HARD_MAX_TRANSACTIONS_PER_SLOT: usize = 65_536;
const HARD_MAX_OUTPUT_BYTES: u64 = 1_073_741_824;
const HARD_MAX_CHECKPOINT_BYTES: u64 = 1_073_741_824;
const HARD_MAX_RUNTIME_SECONDS: u64 = 604_800;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SlotRange {
    pub start_inclusive: u64,
    pub end_exclusive: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct OldFaithfulSourceManifest {
    pub schema_version: String,
    pub epoch: u64,
    pub epoch_cid: String,
    pub car_sha256: String,
    pub car_file_size_bytes: String,
    pub slots_file_sha256: String,
    pub slots_file_size_bytes: u64,
    pub slots_file_entry_count: u64,
    pub slots_first: u64,
    pub slots_last: u64,
    pub slot_range: SlotRange,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AdapterProvenance {
    #[serde(default = "adapter_schema")]
    pub schema_version: String,
    pub jetstreamer_git_sha: String,
    pub plugin_git_sha: String,
    pub plugin_source_sha256: String,
}

fn adapter_schema() -> String {
    "JETSTREAMER_ADAPTER_PROVENANCE_1".into()
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ReducerProvenance {
    pub schema_version: String,
    pub reducer_git_sha: String,
    pub reducer_source_sha256: String,
    pub jetstreamer_git_sha: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ReducerLimits {
    pub max_pending_slots: usize,
    pub max_transactions_per_slot: usize,
    pub max_output_bytes: u64,
    pub max_checkpoint_bytes: u64,
    pub max_runtime_seconds: u64,
}

impl ReducerLimits {
    #[must_use]
    pub const fn test_defaults() -> Self {
        Self {
            max_pending_slots: 16,
            max_transactions_per_slot: 256,
            max_output_bytes: 16 * 1024 * 1024,
            max_checkpoint_bytes: 16 * 1024 * 1024,
            max_runtime_seconds: 60,
        }
    }
}

#[derive(Debug, Clone)]
pub struct ReducerConfig {
    pub output_dir: PathBuf,
    pub source_manifest: OldFaithfulSourceManifest,
    pub adapter_provenance: AdapterProvenance,
    pub reducer_provenance: ReducerProvenance,
    pub limits: ReducerLimits,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ObserveOutcome {
    Pending,
    Duplicate,
    Committed { slot: u64 },
}

#[derive(Debug)]
pub struct Phase5Reducer {
    config: ReducerConfig,
    pending: BTreeMap<u64, PendingSlot>,
    completed: BTreeMap<u64, CompletedSlot>,
    generation: u64,
    identity_sha256: String,
    #[cfg(target_os = "linux")]
    _namespace_marker: NamespaceLock,
    _lock_file: File,
    _lock_link_file: File,
    lock_path: PathBuf,
    lock_link_path: PathBuf,
    #[cfg(unix)]
    output_device: u64,
    #[cfg(unix)]
    output_inode: u64,
    #[cfg(unix)]
    slots_device: u64,
    #[cfg(unix)]
    slots_inode: u64,
    #[cfg(unix)]
    checkpoints_device: u64,
    #[cfg(unix)]
    checkpoints_inode: u64,
    #[cfg(unix)]
    lock_device: u64,
    #[cfg(unix)]
    lock_inode: u64,
    fault_point: Option<FaultPoint>,
    started: Instant,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct PendingSlot {
    block: Option<BlockProjection>,
    possible_leader_skipped: bool,
    transactions: BTreeMap<usize, PendingTransaction>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct BlockProjection {
    parent_slot: u64,
    parent_blockhash: String,
    slot: u64,
    blockhash: String,
    rewards_sha256: String,
    block_time: i64,
    block_height: Option<u64>,
    executed_transaction_count: u64,
    entry_count: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct PendingTransaction {
    semantic_sha256: String,
    source: PersistedTransactionSource,
    observation: Option<Observation>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct PersistedTransactionSource {
    slot: u64,
    transaction_slot_index: usize,
    signature: String,
    message_hash: String,
    is_vote: bool,
    transaction_status_meta_sha256: String,
    transaction_sha256: String,
}

impl PersistedTransactionSource {
    fn from_callback(transaction: &TransactionData) -> Result<Self, ReducerError> {
        Ok(Self {
            slot: transaction.slot,
            transaction_slot_index: transaction.transaction_slot_index,
            signature: transaction.signature.to_string(),
            message_hash: transaction.message_hash.to_string(),
            is_vote: transaction.is_vote,
            transaction_status_meta_sha256: domain_hash(
                "OLD_FAITHFUL_TRANSACTION_STATUS_META_DEBUG_1",
                &format!("{:#?}", transaction.transaction_status_meta),
            )?,
            transaction_sha256: domain_hash(
                "OLD_FAITHFUL_VERSIONED_TRANSACTION_1",
                &transaction.transaction,
            )?,
        })
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct CompletedSlot {
    block: BlockProjection,
    transactions: BTreeMap<usize, CompletedTransaction>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct CompletedTransaction {
    semantic_sha256: String,
    source: PersistedTransactionSource,
    observation: Option<Observation>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FaultPoint {
    BeforeWalPublish,
    BeforeCheckpointPublish,
    BeforeOutputPublish,
    AfterWalSync,
    AfterOutputSync,
    AfterLedgerSync,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Checkpoint {
    schema_version: String,
    identity_sha256: String,
    generation: u64,
    pending: BTreeMap<u64, PendingSlot>,
    completed: BTreeMap<u64, CompletedSlot>,
    state_sha256: String,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct WalRecord {
    schema_version: String,
    identity_sha256: String,
    slot: u64,
    output_base64: String,
    output_sha256: String,
    ledger_offset: u64,
    ledger_prefix_sha256: String,
    ledger_line_base64: String,
    ledger_line_sha256: String,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct CoverageRecord {
    schema_version: String,
    identity_sha256: String,
    slot: u64,
    output_sha256: String,
    transaction_count: usize,
    possible_leader_skipped_observed: bool,
    research_ready: bool,
}

#[derive(Debug, Error)]
pub enum ReducerError {
    #[error("jetstreamer_revision_mismatch")]
    JetstreamerRevisionMismatch,
    #[error("invalid_reducer_config:{0}")]
    InvalidConfig(&'static str),
    #[error("runtime_budget_exceeded")]
    RuntimeBudgetExceeded,
    #[error("pending_slot_limit_exceeded")]
    PendingSlotLimitExceeded,
    #[error("transaction_limit_exceeded")]
    TransactionLimitExceeded,
    #[error("invalid_transaction:{0}")]
    InvalidTransaction(&'static str),
    #[error("conflicting_transaction_retry")]
    ConflictingTransactionRetry,
    #[error("conflicting_signature_identity")]
    ConflictingSignatureIdentity,
    #[error("conflicting_block_retry")]
    ConflictingBlockRetry,
    #[error("block_time_missing")]
    BlockTimeMissing,
    #[error("output_budget_exceeded")]
    OutputBudgetExceeded,
    #[error("output_already_exists")]
    OutputAlreadyExists,
    #[error("non_contiguous_transaction_indices")]
    NonContiguousTransactionIndices,
    #[error("unsafe_path")]
    UnsafePath,
    #[error("checkpoint_corrupt:{0}")]
    CheckpointCorrupt(&'static str),
    #[error("wal_corrupt:{0}")]
    WalCorrupt(&'static str),
    #[error("output_corrupt:{0}")]
    OutputCorrupt(&'static str),
    #[error("provenance_mismatch")]
    ProvenanceMismatch,
    #[error("injected_crash")]
    InjectedCrash,
    #[error("io:{0}")]
    Io(#[from] std::io::Error),
    #[error("serialization:{0}")]
    Serialization(#[from] serde_json::Error),
    #[error("invalid_block_time")]
    InvalidBlockTime,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct LocatedInstruction {
    instruction_location: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    parent_instruction_index: Option<usize>,
    instruction_index: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    stack_height: Option<u32>,
    program_id_index: usize,
    program_id: String,
    account_indices: Vec<usize>,
    accounts: Vec<String>,
    data_hex: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct PumpCandidate {
    event_key: String,
    instruction_location: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    parent_instruction_index: Option<usize>,
    instruction_index: usize,
    discriminator_hex: String,
    discriminator_source: Option<String>,
    parser_status: String,
    variant: Option<String>,
    kind: Option<String>,
    mint: Option<String>,
    curve: Option<String>,
    execution_status: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct PumpQuarantine {
    event_key: String,
    reason: String,
    discriminator_hex: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct TokenBalance {
    account_index: u8,
    mint: String,
    owner: String,
    program_id: String,
    decimals: u8,
    amount: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct BronzeTransaction {
    schema_version: String,
    slot: u64,
    transaction_index: usize,
    signature: String,
    block_time: String,
    execution_status: String,
    fee_lamports: String,
    log_messages: Vec<String>,
    account_keys: Vec<String>,
    pre_balances_lamports: Vec<String>,
    post_balances_lamports: Vec<String>,
    instructions: Vec<LocatedInstruction>,
    pump_candidates: Vec<PumpCandidate>,
    quarantines: Vec<PumpQuarantine>,
    pre_token_balances: Vec<TokenBalance>,
    post_token_balances: Vec<TokenBalance>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Observation {
    schema_version: String,
    source_manifest: OldFaithfulSourceManifest,
    source_manifest_sha256: String,
    adapter_provenance: AdapterProvenance,
    adapter_provenance_sha256: String,
    static_account_count: usize,
    bronze: BronzeTransaction,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct SlotOutput {
    schema_version: String,
    source_manifest_sha256: String,
    adapter_provenance_sha256: String,
    reducer_provenance: ReducerProvenance,
    reducer_provenance_sha256: String,
    slot: u64,
    parent_slot: u64,
    parent_blockhash: String,
    blockhash: String,
    block_time: String,
    block_height: Option<u64>,
    executed_transaction_count: u64,
    entry_count: u64,
    transaction_projection: String,
    observations: Vec<Observation>,
    research_ready: bool,
}

impl Phase5Reducer {
    /// Opens or resumes a reducer output directory under the configured immutable identity.
    ///
    /// # Errors
    ///
    /// Returns an error for invalid provenance or limits, unsafe/corrupt persisted state,
    /// lock contention, recovery failure, or filesystem I/O failure.
    pub fn open(config: ReducerConfig) -> Result<Self, ReducerError> {
        validate_config(&config)?;
        let identity_sha256 = reducer_identity_sha256(&config)?;
        #[cfg(target_os = "linux")]
        let namespace_marker = acquire_kernel_namespace_marker(&config.output_dir)?;
        let output_existed = config.output_dir.symlink_metadata().is_ok();
        prepare_output_directories(&config.output_dir)?;
        cleanup_publication_residues(&config.output_dir)?;
        let lock_path = namespace_lock_path(&config.output_dir)?;
        let lock_link_path = config.output_dir.join("reducer.lock");
        reject_non_regular_if_present(&lock_path)?;
        reject_non_regular_if_present(&lock_link_path)?;
        if !lock_path.exists() {
            if output_existed {
                return Err(ReducerError::UnsafePath);
            }
            let file = OpenOptions::new()
                .read(true)
                .write(true)
                .create_new(true)
                .open(&lock_path)?;
            file.sync_all()?;
            sync_parent(&lock_path)?;
        }
        let lock_file = OpenOptions::new().read(true).write(true).open(&lock_path)?;
        lock_file.try_lock_exclusive().map_err(ReducerError::Io)?;
        if !lock_link_path.exists() {
            if output_existed {
                return Err(ReducerError::UnsafePath);
            }
            fs::hard_link(&lock_path, &lock_link_path)?;
            sync_dir(&config.output_dir)?;
        }
        let lock_link_file = OpenOptions::new()
            .read(true)
            .write(true)
            .open(&lock_link_path)?;
        #[cfg(unix)]
        let output_metadata = fs::metadata(&config.output_dir)?;
        #[cfg(unix)]
        let slots_metadata = fs::metadata(config.output_dir.join("slots"))?;
        #[cfg(unix)]
        let checkpoints_metadata = fs::metadata(config.output_dir.join("checkpoints"))?;
        #[cfg(unix)]
        let lock_metadata = lock_file.metadata()?;
        #[cfg(unix)]
        {
            let lock_path_metadata = fs::metadata(&lock_path)?;
            let lock_link_metadata = lock_link_file.metadata()?;
            let lock_link_path_metadata = fs::metadata(&lock_link_path)?;
            if lock_path_metadata.dev() != lock_metadata.dev()
                || lock_path_metadata.ino() != lock_metadata.ino()
                || lock_link_metadata.dev() != lock_metadata.dev()
                || lock_link_metadata.ino() != lock_metadata.ino()
                || lock_link_path_metadata.dev() != lock_metadata.dev()
                || lock_link_path_metadata.ino() != lock_metadata.ino()
            {
                return Err(ReducerError::UnsafePath);
            }
        }
        initialize_ledger(&config.output_dir)?;
        restore_wal_claim(&config.output_dir)?;
        let (pending, completed, generation) = load_checkpoint(&config, &identity_sha256)?;
        recover_wal(&config, &identity_sha256, &pending, &completed)?;
        validate_coverage_and_outputs(&config, &identity_sha256, &pending, &completed)?;
        let mut reducer = Self {
            config,
            pending,
            completed,
            generation,
            identity_sha256,
            #[cfg(target_os = "linux")]
            _namespace_marker: namespace_marker,
            _lock_file: lock_file,
            _lock_link_file: lock_link_file,
            lock_path,
            lock_link_path,
            #[cfg(unix)]
            output_device: output_metadata.dev(),
            #[cfg(unix)]
            output_inode: output_metadata.ino(),
            #[cfg(unix)]
            slots_device: slots_metadata.dev(),
            #[cfg(unix)]
            slots_inode: slots_metadata.ino(),
            #[cfg(unix)]
            checkpoints_device: checkpoints_metadata.dev(),
            #[cfg(unix)]
            checkpoints_inode: checkpoints_metadata.ino(),
            #[cfg(unix)]
            lock_device: lock_metadata.dev(),
            #[cfg(unix)]
            lock_inode: lock_metadata.ino(),
            fault_point: None,
            started: Instant::now(),
        };
        reducer.reconcile_recovered_commits()?;
        if reducer.generation == 0 {
            reducer.save_checkpoint()?;
        }
        Ok(reducer)
    }

    pub fn set_fault_point(&mut self, fault_point: Option<FaultPoint>) {
        self.fault_point = fault_point;
    }

    /// Applies a block callback, preserving provisional-skip semantics and exact retries.
    ///
    /// # Errors
    ///
    /// Returns an error for conflicts, malformed/out-of-range callbacks, exceeded budgets,
    /// corrupt persistence, or filesystem I/O failure.
    pub fn observe_block(&mut self, block: &BlockData) -> Result<ObserveOutcome, ReducerError> {
        self.check_runtime()?;
        let slot = block.slot();
        self.ensure_slot_in_range(slot)?;
        let recovered_slot = self.recover_live_wal()?;
        if let Some(completed) = self.completed.get(&slot) {
            return match block {
                BlockData::PossibleLeaderSkipped { .. } => Ok(ObserveOutcome::Duplicate),
                BlockData::Block { .. } => {
                    let projection = block_projection(block)?;
                    if completed.block == projection {
                        Ok(if recovered_slot == Some(slot) {
                            ObserveOutcome::Committed { slot }
                        } else {
                            ObserveOutcome::Duplicate
                        })
                    } else {
                        Err(ReducerError::ConflictingBlockRetry)
                    }
                }
            };
        }
        if matches!(block, BlockData::PossibleLeaderSkipped { .. }) {
            self.ensure_pending_capacity(slot)?;
            let mut next_pending = self.pending.clone();
            let pending = next_pending.entry(slot).or_default();
            if pending.possible_leader_skipped {
                return Ok(ObserveOutcome::Duplicate);
            }
            pending.possible_leader_skipped = true;
            self.replace_pending_and_checkpoint(next_pending)?;
            return Ok(ObserveOutcome::Pending);
        }
        let projection = block_projection(block)?;
        if projection.executed_transaction_count
            > u64::try_from(self.config.limits.max_transactions_per_slot).unwrap_or(u64::MAX)
        {
            return Err(ReducerError::TransactionLimitExceeded);
        }
        self.ensure_pending_capacity(slot)?;
        let mut next_pending = self.pending.clone();
        let pending = next_pending.entry(slot).or_default();
        if let Some(existing) = &pending.block {
            if existing == &projection {
                return Ok(ObserveOutcome::Duplicate);
            }
            return Err(ReducerError::ConflictingBlockRetry);
        }
        pending.block = Some(projection);
        validate_pending_join(pending)?;
        self.replace_pending_and_checkpoint(next_pending)?;
        self.try_commit(slot)
    }

    /// Applies a transaction callback and durably checkpoints its normalized evidence.
    ///
    /// # Errors
    ///
    /// Returns an error for semantic conflicts, malformed transactions, exceeded budgets,
    /// corrupt persistence, or filesystem I/O failure.
    pub fn observe_transaction(
        &mut self,
        transaction: &TransactionData,
    ) -> Result<ObserveOutcome, ReducerError> {
        self.check_runtime()?;
        self.ensure_slot_in_range(transaction.slot)?;
        let recovered_slot = self.recover_live_wal()?;
        if transaction.transaction_slot_index >= self.config.limits.max_transactions_per_slot {
            return Err(ReducerError::TransactionLimitExceeded);
        }
        let signature = transaction.signature.to_string();
        let observation = project_observation(
            transaction,
            None,
            &self.config.source_manifest,
            &self.config.adapter_provenance,
        )?;
        let semantic_sha256 = semantic_transaction_sha256(transaction, observation.as_ref())?;
        if let Some(completed) = self.completed.get(&transaction.slot) {
            if let Some(existing) = completed
                .transactions
                .get(&transaction.transaction_slot_index)
            {
                return if existing.semantic_sha256 == semantic_sha256 {
                    Ok(if recovered_slot == Some(transaction.slot) {
                        ObserveOutcome::Committed {
                            slot: transaction.slot,
                        }
                    } else {
                        ObserveOutcome::Duplicate
                    })
                } else {
                    Err(ReducerError::ConflictingTransactionRetry)
                };
            }
            return Err(ReducerError::ConflictingTransactionRetry);
        }
        if let Some(existing) = self.pending.get(&transaction.slot).and_then(|pending| {
            pending
                .transactions
                .get(&transaction.transaction_slot_index)
        }) {
            return if existing.semantic_sha256 == semantic_sha256 {
                Ok(ObserveOutcome::Duplicate)
            } else {
                Err(ReducerError::ConflictingTransactionRetry)
            };
        }
        if self.signature_exists_elsewhere(
            &signature,
            transaction.slot,
            transaction.transaction_slot_index,
        ) {
            return Err(ReducerError::ConflictingSignatureIdentity);
        }
        let pending_transaction = PendingTransaction {
            semantic_sha256,
            source: PersistedTransactionSource::from_callback(transaction)?,
            observation,
        };
        self.ensure_pending_capacity(transaction.slot)?;
        let mut next_pending = self.pending.clone();
        next_pending
            .entry(transaction.slot)
            .or_default()
            .transactions
            .insert(transaction.transaction_slot_index, pending_transaction);
        let inserted = next_pending
            .get(&transaction.slot)
            .ok_or(ReducerError::CheckpointCorrupt("pending_insert"))?;
        validate_pending_join(inserted)?;
        self.replace_pending_and_checkpoint(next_pending)?;
        self.try_commit(transaction.slot)
    }

    fn check_runtime(&self) -> Result<(), ReducerError> {
        self.ensure_namespace_identity()?;
        cleanup_publication_residues(&self.config.output_dir)?;
        if self.started.elapsed().as_secs() > self.config.limits.max_runtime_seconds {
            return Err(ReducerError::RuntimeBudgetExceeded);
        }
        Ok(())
    }

    fn recover_live_wal(&mut self) -> Result<Option<u64>, ReducerError> {
        let path = self.config.output_dir.join("coverage.wal");
        if !path.exists() {
            return Ok(None);
        }
        let bytes = bounded_read(&path, self.config.limits.max_checkpoint_bytes)
            .map_err(|_| ReducerError::WalCorrupt("oversized"))?;
        let wal: WalRecord =
            serde_json::from_slice(&bytes).map_err(|_| ReducerError::WalCorrupt("invalid_json"))?;
        recover_wal(
            &self.config,
            &self.identity_sha256,
            &self.pending,
            &self.completed,
        )?;
        validate_coverage_and_outputs(
            &self.config,
            &self.identity_sha256,
            &self.pending,
            &self.completed,
        )?;
        self.reconcile_recovered_commits()?;
        Ok(Some(wal.slot))
    }

    fn ensure_namespace_identity(&self) -> Result<(), ReducerError> {
        reject_non_regular_if_present(&self.lock_path)?;
        reject_non_regular_if_present(&self.lock_link_path)?;
        #[cfg(unix)]
        {
            let output = fs::metadata(&self.config.output_dir)?;
            let slots = fs::metadata(self.config.output_dir.join("slots"))?;
            let checkpoints = fs::metadata(self.config.output_dir.join("checkpoints"))?;
            let lock = fs::metadata(&self.lock_path)?;
            let lock_link = fs::metadata(&self.lock_link_path)?;
            if output.dev() != self.output_device
                || output.ino() != self.output_inode
                || slots.dev() != self.slots_device
                || slots.ino() != self.slots_inode
                || checkpoints.dev() != self.checkpoints_device
                || checkpoints.ino() != self.checkpoints_inode
                || lock.dev() != self.lock_device
                || lock.ino() != self.lock_inode
                || lock_link.dev() != self.lock_device
                || lock_link.ino() != self.lock_inode
            {
                return Err(ReducerError::UnsafePath);
            }
        }
        Ok(())
    }

    fn ensure_slot_in_range(&self, slot: u64) -> Result<(), ReducerError> {
        if slot < self.config.source_manifest.slot_range.start_inclusive
            || slot >= self.config.source_manifest.slot_range.end_exclusive
        {
            return Err(ReducerError::InvalidTransaction("slot_out_of_range"));
        }
        Ok(())
    }

    fn ensure_pending_capacity(&self, slot: u64) -> Result<(), ReducerError> {
        if !self.pending.contains_key(&slot)
            && self.pending.len() >= self.config.limits.max_pending_slots
        {
            return Err(ReducerError::PendingSlotLimitExceeded);
        }
        Ok(())
    }

    fn signature_exists_elsewhere(&self, signature: &str, slot: u64, index: usize) -> bool {
        self.pending.iter().any(|(seen_slot, pending)| {
            pending
                .transactions
                .iter()
                .any(|(seen_index, transaction)| {
                    (*seen_slot != slot || *seen_index != index)
                        && transaction.source.signature == signature
                })
        }) || self.completed.iter().any(|(seen_slot, completed)| {
            completed
                .transactions
                .iter()
                .any(|(seen_index, transaction)| {
                    (*seen_slot != slot || *seen_index != index)
                        && transaction.source.signature == signature
                })
        })
    }

    fn try_commit(&mut self, slot: u64) -> Result<ObserveOutcome, ReducerError> {
        let Some(pending) = self.pending.get(&slot) else {
            return Ok(ObserveOutcome::Pending);
        };
        let Some(block) = pending.block.as_ref() else {
            return Ok(ObserveOutcome::Pending);
        };
        let expected = usize::try_from(block.executed_transaction_count)
            .map_err(|_| ReducerError::TransactionLimitExceeded)?;
        if pending.transactions.len() != expected {
            return Ok(ObserveOutcome::Pending);
        }
        if pending.transactions.keys().copied().ne(0..expected) {
            return Err(ReducerError::NonContiguousTransactionIndices);
        }
        let pending = pending.clone();
        let wal_sha256 = self.publish_slot(&pending)?;
        let block = pending.block.clone().expect("ready block");
        let transactions = pending
            .transactions
            .into_iter()
            .map(|(index, transaction)| {
                (
                    index,
                    CompletedTransaction {
                        semantic_sha256: transaction.semantic_sha256,
                        source: transaction.source,
                        observation: transaction.observation,
                    },
                )
            })
            .collect();
        self.pending.remove(&slot);
        self.completed.insert(
            slot,
            CompletedSlot {
                block,
                transactions,
            },
        );
        if self.save_checkpoint().is_ok() {
            let _ = remove_wal(&self.config.output_dir, &wal_sha256);
        }
        Ok(ObserveOutcome::Committed { slot })
    }

    fn publish_slot(&mut self, pending: &PendingSlot) -> Result<String, ReducerError> {
        validate_coverage_and_outputs(
            &self.config,
            &self.identity_sha256,
            &self.pending,
            &self.completed,
        )?;
        let block = pending.block.as_ref().expect("ready block exists");
        let output_bytes = build_slot_output_bytes(&self.config, block, &pending.transactions)?;
        let output_sha256 = sha256_bytes(&output_bytes);
        let coverage = CoverageRecord {
            schema_version: COVERAGE_SCHEMA.into(),
            identity_sha256: self.identity_sha256.clone(),
            slot: block.slot,
            output_sha256: output_sha256.clone(),
            transaction_count: pending.transactions.len(),
            // A late provisional skip can arrive after immutable block output. The
            // definitive block therefore canonicalizes this callback-order detail.
            possible_leader_skipped_observed: false,
            research_ready: false,
        };
        let mut ledger_line = serde_json::to_vec(&coverage)?;
        ledger_line.push(b'\n');
        enforce_output_budget(&self.config, output_bytes.len(), ledger_line.len(), 1)?;
        let ledger_path = self.config.output_dir.join("coverage.ndjson");
        let ledger_current = bounded_read(&ledger_path, self.config.limits.max_output_bytes)?;
        let existing = parse_coverage_bytes(&ledger_current)?;
        if existing
            .values()
            .any(|record| record.identity_sha256 != self.identity_sha256)
        {
            return Err(ReducerError::ProvenanceMismatch);
        }
        let ledger_offset =
            u64::try_from(ledger_current.len()).map_err(|_| ReducerError::OutputBudgetExceeded)?;
        let wal = WalRecord {
            schema_version: WAL_SCHEMA.into(),
            identity_sha256: self.identity_sha256.clone(),
            slot: block.slot,
            output_base64: BASE64.encode(&output_bytes),
            output_sha256,
            ledger_offset,
            ledger_prefix_sha256: sha256_bytes(&ledger_current),
            ledger_line_base64: BASE64.encode(&ledger_line),
            ledger_line_sha256: sha256_bytes(&ledger_line),
        };
        let wal_sha256 = write_wal(
            &self.config.output_dir,
            &wal,
            self.config.limits.max_checkpoint_bytes,
            &mut self.fault_point,
        )?;
        self.maybe_fault(FaultPoint::AfterWalSync)?;
        publish_immutable_output(
            &self.config.output_dir,
            block.slot,
            &output_bytes,
            &mut self.fault_point,
        )?;
        self.maybe_fault(FaultPoint::AfterOutputSync)?;
        append_ledger_exact(&ledger_path, ledger_offset, &ledger_line)?;
        self.maybe_fault(FaultPoint::AfterLedgerSync)?;
        Ok(wal_sha256)
    }

    fn maybe_fault(&mut self, point: FaultPoint) -> Result<(), ReducerError> {
        inject_fault(&mut self.fault_point, point)
    }

    fn save_checkpoint(&mut self) -> Result<(), ReducerError> {
        let next_generation = self
            .generation
            .checked_add(1)
            .ok_or(ReducerError::CheckpointCorrupt("generation_overflow"))?;
        let checkpoint = build_checkpoint(
            &self.identity_sha256,
            next_generation,
            self.pending.clone(),
            self.completed.clone(),
        )?;
        save_checkpoint_file(&self.config, &checkpoint, &mut self.fault_point)?;
        self.generation = next_generation;
        Ok(())
    }

    fn replace_pending_and_checkpoint(
        &mut self,
        next_pending: BTreeMap<u64, PendingSlot>,
    ) -> Result<(), ReducerError> {
        let next_generation = self
            .generation
            .checked_add(1)
            .ok_or(ReducerError::CheckpointCorrupt("generation_overflow"))?;
        let checkpoint = build_checkpoint(
            &self.identity_sha256,
            next_generation,
            next_pending.clone(),
            self.completed.clone(),
        )?;
        save_checkpoint_file(&self.config, &checkpoint, &mut self.fault_point)?;
        self.pending = next_pending;
        self.generation = next_generation;
        Ok(())
    }

    fn reconcile_recovered_commits(&mut self) -> Result<(), ReducerError> {
        let covered = coverage_slots(&self.config.output_dir, self.config.limits.max_output_bytes)?;
        if self
            .completed
            .keys()
            .any(|slot| !covered.contains_key(slot))
        {
            return Err(ReducerError::CheckpointCorrupt("completed_coverage"));
        }
        let ready = self
            .pending
            .iter()
            .filter_map(|(slot, pending)| {
                let block = pending.block.as_ref()?;
                let expected = usize::try_from(block.executed_transaction_count).ok()?;
                (pending.transactions.len() == expected
                    && pending.transactions.keys().copied().eq(0..expected))
                .then_some((*slot, covered.contains_key(slot)))
            })
            .collect::<Vec<_>>();
        let mut reconciled_covered = false;
        for (slot, is_covered) in ready {
            if !is_covered {
                let outcome = self.try_commit(slot)?;
                debug_assert_eq!(outcome, ObserveOutcome::Committed { slot });
                continue;
            }
            let pending = self.pending.remove(&slot).expect("selected pending slot");
            let block = pending.block.expect("selected pending block");
            let transactions = pending
                .transactions
                .into_iter()
                .map(|(index, transaction)| {
                    (
                        index,
                        CompletedTransaction {
                            semantic_sha256: transaction.semantic_sha256,
                            source: transaction.source,
                            observation: transaction.observation,
                        },
                    )
                })
                .collect();
            self.completed.insert(
                slot,
                CompletedSlot {
                    block,
                    transactions,
                },
            );
            reconciled_covered = true;
        }
        if reconciled_covered && self.generation > 0 {
            self.save_checkpoint()?;
        }
        Ok(())
    }
}

fn validate_pending_join(pending: &PendingSlot) -> Result<(), ReducerError> {
    let Some(block) = &pending.block else {
        return Ok(());
    };
    let expected = usize::try_from(block.executed_transaction_count)
        .map_err(|_| ReducerError::TransactionLimitExceeded)?;
    if pending
        .transactions
        .keys()
        .copied()
        .any(|index| index >= expected)
        || (pending.transactions.len() == expected
            && pending.transactions.keys().copied().ne(0..expected))
    {
        return Err(ReducerError::NonContiguousTransactionIndices);
    }
    Ok(())
}

macro_rules! block_rewards_sha256 {
    ($rewards:expr) => {{
        #[derive(Serialize)]
        #[serde(rename_all = "camelCase")]
        struct CanonicalReward {
            address: String,
            reward_type: String,
            lamports: i64,
            post_balance: u64,
            commission: Option<u8>,
        }
        #[derive(Serialize)]
        #[serde(rename_all = "camelCase")]
        struct CanonicalRewards {
            keyed_rewards: Vec<CanonicalReward>,
            num_partitions: Option<u64>,
        }
        let keyed_rewards = $rewards
            .keyed_rewards
            .iter()
            .map(|(address, info)| CanonicalReward {
                address: address.to_string(),
                reward_type: format!("{:?}", info.reward_type),
                lamports: info.lamports,
                post_balance: info.post_balance,
                commission: info.commission,
            })
            .collect();
        domain_hash(
            "OLD_FAITHFUL_RUST_BLOCK_REWARDS_1",
            &CanonicalRewards {
                keyed_rewards,
                num_partitions: $rewards.num_partitions,
            },
        )
    }};
}

fn block_projection(block: &BlockData) -> Result<BlockProjection, ReducerError> {
    match block {
        BlockData::Block {
            parent_slot,
            parent_blockhash,
            slot,
            blockhash,
            rewards,
            block_time,
            block_height,
            executed_transaction_count,
            entry_count,
            ..
        } => Ok(BlockProjection {
            parent_slot: *parent_slot,
            parent_blockhash: parent_blockhash.to_string(),
            slot: *slot,
            blockhash: blockhash.to_string(),
            rewards_sha256: block_rewards_sha256!(rewards)?,
            block_time: block_time.ok_or(ReducerError::BlockTimeMissing)?,
            block_height: *block_height,
            executed_transaction_count: *executed_transaction_count,
            entry_count: *entry_count,
        }),
        BlockData::PossibleLeaderSkipped { .. } => {
            Err(ReducerError::InvalidTransaction("expected_block"))
        }
    }
}

fn semantic_transaction_sha256(
    transaction: &TransactionData,
    observation: Option<&Observation>,
) -> Result<String, ReducerError> {
    let source = PersistedTransactionSource::from_callback(transaction)?;
    stored_transaction_sha256(&source, observation)
}

fn stored_transaction_sha256(
    source: &PersistedTransactionSource,
    observation: Option<&Observation>,
) -> Result<String, ReducerError> {
    #[derive(Serialize)]
    #[serde(rename_all = "camelCase")]
    struct CanonicalTransaction<'a> {
        source: &'a PersistedTransactionSource,
        observation: Option<&'a Observation>,
    }

    domain_hash(
        "OLD_FAITHFUL_RUST_TRANSACTION_SEMANTICS_1",
        &CanonicalTransaction {
            source,
            observation,
        },
    )
}

fn reducer_identity_sha256(config: &ReducerConfig) -> Result<String, ReducerError> {
    #[derive(Serialize)]
    struct Identity<'a> {
        source_manifest: &'a OldFaithfulSourceManifest,
        adapter_provenance: &'a AdapterProvenance,
        reducer_provenance: &'a ReducerProvenance,
    }
    domain_hash(
        "OLD_FAITHFUL_RUST_REDUCER_IDENTITY_1",
        &Identity {
            source_manifest: &config.source_manifest,
            adapter_provenance: &config.adapter_provenance,
            reducer_provenance: &config.reducer_provenance,
        },
    )
}

#[cfg(target_os = "linux")]
fn acquire_kernel_namespace_marker(output_dir: &Path) -> Result<NamespaceLock, ReducerError> {
    let absolute = normalized_absolute_output_path(output_dir)?;
    NamespaceLock::acquire(absolute.as_os_str().as_encoded_bytes()).map_err(ReducerError::Io)
}

fn normalized_absolute_output_path(output_dir: &Path) -> Result<PathBuf, ReducerError> {
    let absolute = if output_dir.is_absolute() {
        output_dir.to_path_buf()
    } else {
        std::env::current_dir()?.join(output_dir)
    };
    let mut normalized = PathBuf::new();
    for component in absolute.components() {
        match component {
            std::path::Component::Prefix(_) | std::path::Component::RootDir => {
                normalized.push(component.as_os_str());
            }
            std::path::Component::CurDir => {}
            std::path::Component::ParentDir => {
                if !normalized.pop() {
                    return Err(ReducerError::UnsafePath);
                }
            }
            std::path::Component::Normal(value) => normalized.push(value),
        }
    }
    if !normalized.is_absolute() {
        return Err(ReducerError::UnsafePath);
    }
    Ok(normalized)
}

fn namespace_lock_path(output_dir: &Path) -> Result<PathBuf, ReducerError> {
    let absolute = normalized_absolute_output_path(output_dir)?;
    let parent = absolute.parent().ok_or(ReducerError::UnsafePath)?;
    let digest = sha256_bytes(absolute.as_os_str().as_encoded_bytes());
    Ok(parent.join(format!(".old-faithful-pump-reducer-{digest}.lock")))
}

fn prepare_output_directories(output_dir: &Path) -> Result<(), ReducerError> {
    reject_symlink_ancestors(output_dir)?;
    if output_dir.symlink_metadata().is_ok() {
        let metadata = fs::symlink_metadata(output_dir)?;
        if !metadata.file_type().is_dir() || metadata.file_type().is_symlink() {
            return Err(ReducerError::UnsafePath);
        }
    } else {
        fs::create_dir(output_dir)?;
        sync_parent(output_dir)?;
    }
    for name in ["slots", "checkpoints"] {
        let path = output_dir.join(name);
        if path.symlink_metadata().is_ok() {
            let metadata = fs::symlink_metadata(&path)?;
            if !metadata.file_type().is_dir() || metadata.file_type().is_symlink() {
                return Err(ReducerError::UnsafePath);
            }
        } else {
            fs::create_dir(&path)?;
            sync_dir(output_dir)?;
        }
    }
    Ok(())
}

fn cleanup_publication_residues(output_dir: &Path) -> Result<(), ReducerError> {
    for path in [
        output_dir.join("coverage.wal.tmp"),
        output_dir.join("checkpoints/checkpoint.tmp"),
        output_dir.join("slots/slot-output.tmp"),
    ] {
        reject_non_regular_if_present(&path)?;
        if path.exists() {
            fs::remove_file(&path)?;
            sync_parent(&path)?;
        }
    }
    Ok(())
}

fn reject_symlink_ancestors(path: &Path) -> Result<(), ReducerError> {
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()?.join(path)
    };
    let mut current = PathBuf::new();
    for component in absolute.components() {
        current.push(component.as_os_str());
        match fs::symlink_metadata(&current) {
            Ok(metadata) => {
                if metadata.file_type().is_symlink()
                    || (current != absolute && !metadata.file_type().is_dir())
                {
                    return Err(ReducerError::UnsafePath);
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => break,
            Err(error) => return Err(ReducerError::Io(error)),
        }
    }
    Ok(())
}

fn reject_non_regular_if_present(path: &Path) -> Result<(), ReducerError> {
    if let Ok(metadata) = fs::symlink_metadata(path)
        && (!metadata.file_type().is_file() || metadata.file_type().is_symlink())
    {
        return Err(ReducerError::UnsafePath);
    }
    Ok(())
}

fn initialize_ledger(output_dir: &Path) -> Result<(), ReducerError> {
    let path = output_dir.join("coverage.ndjson");
    reject_non_regular_if_present(&path)?;
    if !path.exists() {
        let has_slot_state = fs::read_dir(output_dir.join("slots"))?
            .next()
            .transpose()?
            .is_some();
        let has_checkpoint_state = fs::read_dir(output_dir.join("checkpoints"))?
            .next()
            .transpose()?
            .is_some();
        let has_wal_state = output_dir.join("coverage.wal").symlink_metadata().is_ok()
            || output_dir
                .join("coverage.wal.claim")
                .symlink_metadata()
                .is_ok();
        if has_slot_state || has_checkpoint_state || has_wal_state {
            return Err(ReducerError::OutputCorrupt("coverage_state_set"));
        }
        let file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)?;
        file.sync_all()?;
        sync_dir(output_dir)?;
    }
    Ok(())
}

fn sync_parent(path: &Path) -> Result<(), ReducerError> {
    let parent = path.parent().ok_or(ReducerError::UnsafePath)?;
    sync_dir(parent)
}

fn sync_dir(path: &Path) -> Result<(), ReducerError> {
    File::open(path)?.sync_all()?;
    Ok(())
}

fn regular_file_len(path: &Path) -> Result<u64, ReducerError> {
    reject_non_regular_if_present(path)?;
    Ok(fs::metadata(path)?.len())
}

fn bounded_read(path: &Path, maximum: u64) -> Result<Vec<u8>, ReducerError> {
    let length = regular_file_len(path)?;
    if length > maximum {
        return Err(ReducerError::OutputBudgetExceeded);
    }
    let capacity = usize::try_from(length).map_err(|_| ReducerError::OutputBudgetExceeded)?;
    let mut bytes = Vec::with_capacity(capacity);
    File::open(path)?
        .take(maximum.saturating_add(1))
        .read_to_end(&mut bytes)?;
    if u64::try_from(bytes.len()).map_or(true, |size| size > maximum) {
        return Err(ReducerError::OutputBudgetExceeded);
    }
    Ok(bytes)
}

fn checkpoint_paths(
    output_dir: &Path,
    maximum_entries: usize,
) -> Result<Vec<PathBuf>, ReducerError> {
    let directory = output_dir.join("checkpoints");
    let mut paths = Vec::new();
    for entry in fs::read_dir(directory)? {
        let entry = entry?;
        let path = entry.path();
        if path.extension().and_then(|value| value.to_str()) != Some("json") {
            return Err(ReducerError::CheckpointCorrupt("unexpected_entry"));
        }
        reject_non_regular_if_present(&path)?;
        paths.push(path);
        if paths.len() > maximum_entries {
            return Err(ReducerError::CheckpointCorrupt("too_many_files"));
        }
    }
    paths.sort();
    Ok(paths)
}

type RecoveredCheckpoint = (
    BTreeMap<u64, PendingSlot>,
    BTreeMap<u64, CompletedSlot>,
    u64,
);

fn checkpoint_state_sha256(
    identity_sha256: &str,
    generation: u64,
    pending: &BTreeMap<u64, PendingSlot>,
    completed: &BTreeMap<u64, CompletedSlot>,
) -> Result<String, ReducerError> {
    #[derive(Serialize)]
    #[serde(rename_all = "camelCase")]
    struct CheckpointState<'a> {
        identity_sha256: &'a str,
        generation: u64,
        pending: &'a BTreeMap<u64, PendingSlot>,
        completed: &'a BTreeMap<u64, CompletedSlot>,
    }
    domain_hash(
        "OLD_FAITHFUL_RUST_CHECKPOINT_STATE_1",
        &CheckpointState {
            identity_sha256,
            generation,
            pending,
            completed,
        },
    )
}

fn build_checkpoint(
    identity_sha256: &str,
    generation: u64,
    pending: BTreeMap<u64, PendingSlot>,
    completed: BTreeMap<u64, CompletedSlot>,
) -> Result<Checkpoint, ReducerError> {
    let state_sha256 = checkpoint_state_sha256(identity_sha256, generation, &pending, &completed)?;
    Ok(Checkpoint {
        schema_version: CHECKPOINT_SCHEMA.into(),
        identity_sha256: identity_sha256.into(),
        generation,
        pending,
        completed,
        state_sha256,
    })
}

fn validate_checkpoint_semantics(
    config: &ReducerConfig,
    checkpoint: &Checkpoint,
) -> Result<(), ReducerError> {
    if checkpoint.pending.len() > config.limits.max_pending_slots {
        return Err(ReducerError::CheckpointCorrupt("pending_limit"));
    }
    if checkpoint
        .pending
        .keys()
        .any(|slot| checkpoint.completed.contains_key(slot))
    {
        return Err(ReducerError::CheckpointCorrupt("slot_state_overlap"));
    }
    let mut signatures = std::collections::HashSet::new();
    for (slot, pending) in &checkpoint.pending {
        validate_checkpoint_slot(config, *slot)?;
        if pending.transactions.len() > config.limits.max_transactions_per_slot {
            return Err(ReducerError::CheckpointCorrupt("transaction_limit"));
        }
        if let Some(block) = &pending.block {
            validate_checkpoint_block(config, *slot, block)?;
            let expected = usize::try_from(block.executed_transaction_count)
                .map_err(|_| ReducerError::CheckpointCorrupt("transaction_limit"))?;
            if pending.transactions.len() > expected {
                return Err(ReducerError::CheckpointCorrupt("transaction_count"));
            }
        }
        for (index, transaction) in &pending.transactions {
            if !signatures.insert(transaction.source.signature.clone()) {
                return Err(ReducerError::CheckpointCorrupt("duplicate_signature"));
            }
            validate_checkpoint_transaction(config, *slot, *index, transaction)?;
        }
    }
    for (slot, completed) in &checkpoint.completed {
        validate_checkpoint_slot(config, *slot)?;
        validate_checkpoint_block(config, *slot, &completed.block)?;
        let expected = usize::try_from(completed.block.executed_transaction_count)
            .map_err(|_| ReducerError::CheckpointCorrupt("transaction_limit"))?;
        if completed.transactions.len() != expected
            || completed.transactions.keys().copied().ne(0..expected)
        {
            return Err(ReducerError::CheckpointCorrupt("completed_transactions"));
        }
        for (index, transaction) in &completed.transactions {
            if !signatures.insert(transaction.source.signature.clone()) {
                return Err(ReducerError::CheckpointCorrupt("duplicate_signature"));
            }
            validate_checkpoint_transaction(config, *slot, *index, transaction)?;
        }
    }
    Ok(())
}

fn validate_checkpoint_slot(config: &ReducerConfig, slot: u64) -> Result<(), ReducerError> {
    if slot < config.source_manifest.slot_range.start_inclusive
        || slot >= config.source_manifest.slot_range.end_exclusive
    {
        return Err(ReducerError::CheckpointCorrupt("slot_range"));
    }
    Ok(())
}

fn validate_checkpoint_block(
    config: &ReducerConfig,
    slot: u64,
    block: &BlockProjection,
) -> Result<(), ReducerError> {
    if block.slot != slot
        || block.parent_slot >= block.slot
        || block.executed_transaction_count
            > u64::try_from(config.limits.max_transactions_per_slot).unwrap_or(u64::MAX)
        || !valid_sha256(&block.rewards_sha256)
        || solana_hash::Hash::from_str(&block.blockhash).is_err()
        || solana_hash::Hash::from_str(&block.parent_blockhash).is_err()
    {
        return Err(ReducerError::CheckpointCorrupt("block"));
    }
    canonical_block_time(block.block_time)
        .map_err(|_| ReducerError::CheckpointCorrupt("block_time"))?;
    Ok(())
}

fn validate_checkpoint_transaction<T>(
    config: &ReducerConfig,
    slot: u64,
    index: usize,
    transaction: &T,
) -> Result<(), ReducerError>
where
    T: StoredTransaction,
{
    let source = transaction.source();
    if index >= config.limits.max_transactions_per_slot
        || source.slot != slot
        || source.transaction_slot_index != index
        || solana_signature::Signature::from_str(&source.signature).is_err()
        || solana_hash::Hash::from_str(&source.message_hash).is_err()
        || !valid_sha256(&source.transaction_status_meta_sha256)
        || !valid_sha256(&source.transaction_sha256)
        || source.is_vote != transaction.observation().is_none()
    {
        return Err(ReducerError::CheckpointCorrupt("transaction"));
    }
    if let Some(observation) = transaction.observation() {
        validate_checkpoint_observation(config, slot, index, &source.signature, observation)?;
    }
    let expected = stored_transaction_sha256(source, transaction.observation())?;
    if transaction.semantic_sha256() != expected {
        return Err(ReducerError::CheckpointCorrupt("semantic_hash"));
    }
    Ok(())
}

trait StoredTransaction {
    fn semantic_sha256(&self) -> &str;
    fn source(&self) -> &PersistedTransactionSource;
    fn observation(&self) -> Option<&Observation>;
}

macro_rules! impl_stored_transaction {
    ($type:ty) => {
        impl StoredTransaction for $type {
            fn semantic_sha256(&self) -> &str {
                &self.semantic_sha256
            }
            fn source(&self) -> &PersistedTransactionSource {
                &self.source
            }
            fn observation(&self) -> Option<&Observation> {
                self.observation.as_ref()
            }
        }
    };
}
impl_stored_transaction!(PendingTransaction);
impl_stored_transaction!(CompletedTransaction);

fn build_slot_output_bytes<T: StoredTransaction>(
    config: &ReducerConfig,
    block: &BlockProjection,
    transactions: &BTreeMap<usize, T>,
) -> Result<Vec<u8>, ReducerError> {
    let block_time = canonical_block_time(block.block_time)?;
    let observations = transactions
        .values()
        .filter_map(|transaction| transaction.observation().cloned())
        .map(|mut observation| {
            observation.bronze.block_time.clone_from(&block_time);
            observation
        })
        .collect();
    let output = SlotOutput {
        schema_version: "OLD_FAITHFUL_PUMP_V2_SLOT_1".into(),
        source_manifest_sha256: source_manifest_sha256(&config.source_manifest)?,
        adapter_provenance_sha256: adapter_provenance_sha256(&config.adapter_provenance)?,
        reducer_provenance: config.reducer_provenance.clone(),
        reducer_provenance_sha256: domain_hash(
            "OLD_FAITHFUL_RUST_REDUCER_PROVENANCE_1",
            &config.reducer_provenance,
        )?,
        slot: block.slot,
        parent_slot: block.parent_slot,
        parent_blockhash: block.parent_blockhash.clone(),
        blockhash: block.blockhash.clone(),
        block_time,
        block_height: block.block_height,
        executed_transaction_count: block.executed_transaction_count,
        entry_count: block.entry_count,
        transaction_projection: "PUMP_V2_NON_VOTE_ONLY".into(),
        observations,
        research_ready: false,
    };
    let mut bytes = serde_json::to_vec(&output)?;
    bytes.push(b'\n');
    Ok(bytes)
}

#[allow(clippy::too_many_lines)]
fn validate_checkpoint_observation(
    config: &ReducerConfig,
    slot: u64,
    index: usize,
    signature: &str,
    observation: &Observation,
) -> Result<(), ReducerError> {
    let bronze = &observation.bronze;
    if observation.schema_version != "OLD_FAITHFUL_PUMP_V2_OBSERVATION_1"
        || observation.source_manifest != config.source_manifest
        || observation.adapter_provenance != config.adapter_provenance
        || observation.source_manifest_sha256 != source_manifest_sha256(&config.source_manifest)?
        || observation.adapter_provenance_sha256
            != adapter_provenance_sha256(&config.adapter_provenance)?
        || bronze.schema_version != "PUMP_V2_BRONZE_TRANSACTION_1"
        || bronze.slot != slot
        || bronze.transaction_index != index
        || bronze.signature != signature
        || !bronze.block_time.is_empty()
        || !matches!(bronze.execution_status.as_str(), "succeeded" | "failed")
        || observation.static_account_count == 0
        || observation.static_account_count > bronze.account_keys.len()
        || bronze.account_keys.len() > MAX_RESOLVED_ACCOUNT_KEYS
        || bronze.pre_balances_lamports.len() != bronze.account_keys.len()
        || bronze.post_balances_lamports.len() != bronze.account_keys.len()
    {
        return Err(ReducerError::CheckpointCorrupt("observation"));
    }
    if bronze.log_messages.len() > MAX_LOG_MESSAGES
        || bronze
            .log_messages
            .iter()
            .try_fold(0_usize, |total, message| total.checked_add(message.len()))
            .is_none_or(|total| total > MAX_LOG_BYTES)
    {
        return Err(ReducerError::CheckpointCorrupt("log_messages"));
    }
    let resolved = bronze
        .account_keys
        .iter()
        .map(|value| Address::from_str(value))
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| ReducerError::CheckpointCorrupt("account_keys"))?;
    let mut unique = std::collections::HashSet::new();
    if !resolved.iter().all(|key| unique.insert(*key)) {
        return Err(ReducerError::CheckpointCorrupt("account_keys"));
    }
    let canonical_u64 = |value: &str| {
        value
            .parse::<u64>()
            .is_ok_and(|parsed| parsed.to_string() == value)
    };
    if !canonical_u64(&bronze.fee_lamports)
        || !bronze
            .pre_balances_lamports
            .iter()
            .all(|value| canonical_u64(value))
        || !bronze
            .post_balances_lamports
            .iter()
            .all(|value| canonical_u64(value))
    {
        return Err(ReducerError::CheckpointCorrupt("balances"));
    }
    let mut references = 0_usize;
    let mut top_level_count = 0_usize;
    let mut inner_counts = BTreeMap::<usize, usize>::new();
    let mut total_inner = 0_usize;
    for instruction in &bronze.instructions {
        let top_level = instruction.instruction_location == "top_level";
        let inner = instruction.instruction_location == "inner";
        if (!top_level && !inner)
            || instruction.program_id_index >= resolved.len()
            || (top_level && instruction.program_id_index >= observation.static_account_count)
            || instruction.program_id != bronze.account_keys[instruction.program_id_index]
            || (top_level
                && (instruction.parent_instruction_index.is_some()
                    || instruction.stack_height != Some(1)))
            || (inner
                && (instruction.parent_instruction_index.is_none()
                    || instruction
                        .stack_height
                        .is_some_and(|height| !(2..=9).contains(&height))))
            || instruction.data_hex.len() % 2 != 0
            || instruction.data_hex.len() / 2 > MAX_INSTRUCTION_DATA_BYTES
            || !instruction
                .data_hex
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit())
        {
            return Err(ReducerError::CheckpointCorrupt("instruction"));
        }
        if top_level {
            if instruction.instruction_index != top_level_count {
                return Err(ReducerError::CheckpointCorrupt("instruction_order"));
            }
            top_level_count += 1;
            if top_level_count > MAX_TOP_LEVEL_INSTRUCTIONS {
                return Err(ReducerError::CheckpointCorrupt("instruction_limit"));
            }
        } else {
            let parent = instruction
                .parent_instruction_index
                .ok_or(ReducerError::CheckpointCorrupt("instruction_parent"))?;
            if parent
                .checked_add(1)
                .is_none_or(|expected_top_level_count| expected_top_level_count != top_level_count)
            {
                return Err(ReducerError::CheckpointCorrupt("instruction_parent"));
            }
            let next = inner_counts.entry(parent).or_default();
            if instruction.instruction_index != *next {
                return Err(ReducerError::CheckpointCorrupt("instruction_order"));
            }
            *next += 1;
            total_inner += 1;
            if total_inner > MAX_INNER_INSTRUCTIONS {
                return Err(ReducerError::CheckpointCorrupt("instruction_limit"));
            }
        }
        references = references
            .checked_add(instruction.account_indices.len())
            .ok_or(ReducerError::CheckpointCorrupt("instruction_references"))?;
        if references > MAX_INSTRUCTION_ACCOUNT_REFERENCES
            || instruction.account_indices.len() != instruction.accounts.len()
            || instruction
                .account_indices
                .iter()
                .zip(&instruction.accounts)
                .any(|(account_index, account)| {
                    *account_index >= resolved.len()
                        || bronze.account_keys[*account_index] != *account
                })
        {
            return Err(ReducerError::CheckpointCorrupt("instruction_references"));
        }
    }
    let (candidates, quarantines) = capture_candidates(
        slot,
        index,
        signature,
        &bronze.execution_status,
        &bronze.instructions,
        &resolved,
    )?;
    if candidates != bronze.pump_candidates || quarantines != bronze.quarantines {
        return Err(ReducerError::CheckpointCorrupt("pump_candidates"));
    }
    validate_checkpoint_token_balances(&bronze.pre_token_balances, resolved.len())?;
    validate_checkpoint_token_balances(&bronze.post_token_balances, resolved.len())?;
    Ok(())
}

fn validate_checkpoint_token_balances(
    balances: &[TokenBalance],
    account_count: usize,
) -> Result<(), ReducerError> {
    let mut previous = None;
    for balance in balances {
        if usize::from(balance.account_index) >= account_count
            || previous.is_some_and(|seen| balance.account_index <= seen)
            || Address::from_str(&balance.mint).is_err()
            || Address::from_str(&balance.owner).is_err()
            || Address::from_str(&balance.program_id).is_err()
            || balance.amount.parse::<u64>().is_err()
            || balance
                .amount
                .parse::<u64>()
                .is_ok_and(|value| value.to_string() != balance.amount)
        {
            return Err(ReducerError::CheckpointCorrupt("token_balances"));
        }
        previous = Some(balance.account_index);
    }
    Ok(())
}

fn load_checkpoint(
    config: &ReducerConfig,
    identity_sha256: &str,
) -> Result<RecoveredCheckpoint, ReducerError> {
    let mut newest = None;
    for path in checkpoint_paths(&config.output_dir, MAX_CHECKPOINT_FILES)? {
        let bytes = bounded_read(&path, config.limits.max_checkpoint_bytes)
            .map_err(|_| ReducerError::CheckpointCorrupt("oversized"))?;
        if !bytes.ends_with(b"\n") {
            return Err(ReducerError::CheckpointCorrupt("unterminated"));
        }
        let checkpoint: Checkpoint = serde_json::from_slice(&bytes)
            .map_err(|_| ReducerError::CheckpointCorrupt("invalid_json"))?;
        let mut canonical_checkpoint = serde_json::to_vec(&checkpoint)
            .map_err(|_| ReducerError::CheckpointCorrupt("invalid_json"))?;
        canonical_checkpoint.push(b'\n');
        if bytes != canonical_checkpoint {
            return Err(ReducerError::CheckpointCorrupt("noncanonical"));
        }
        if checkpoint.schema_version != CHECKPOINT_SCHEMA {
            return Err(ReducerError::CheckpointCorrupt("schema"));
        }
        if checkpoint.identity_sha256 != identity_sha256 {
            return Err(ReducerError::ProvenanceMismatch);
        }
        if checkpoint.state_sha256
            != checkpoint_state_sha256(
                &checkpoint.identity_sha256,
                checkpoint.generation,
                &checkpoint.pending,
                &checkpoint.completed,
            )?
        {
            return Err(ReducerError::CheckpointCorrupt("state_hash"));
        }
        validate_checkpoint_semantics(config, &checkpoint)?;
        if newest
            .as_ref()
            .is_some_and(|value: &Checkpoint| value.generation >= checkpoint.generation)
        {
            return Err(ReducerError::CheckpointCorrupt("generation_order"));
        }
        newest = Some(checkpoint);
    }
    Ok(newest.map_or_else(
        || (BTreeMap::new(), BTreeMap::new(), 0),
        |checkpoint| {
            (
                checkpoint.pending,
                checkpoint.completed,
                checkpoint.generation,
            )
        },
    ))
}

fn inject_fault(
    fault_point: &mut Option<FaultPoint>,
    point: FaultPoint,
) -> Result<(), ReducerError> {
    if *fault_point == Some(point) {
        *fault_point = None;
        return Err(ReducerError::InjectedCrash);
    }
    Ok(())
}

fn save_checkpoint_file(
    config: &ReducerConfig,
    checkpoint: &Checkpoint,
    fault_point: &mut Option<FaultPoint>,
) -> Result<(), ReducerError> {
    let existing = checkpoint_paths(&config.output_dir, MAX_CHECKPOINT_FILES)?;
    if existing.len() == MAX_CHECKPOINT_FILES {
        fs::remove_file(&existing[0])?;
        sync_dir(&config.output_dir.join("checkpoints"))?;
    }
    let mut bytes = serde_json::to_vec(checkpoint)?;
    bytes.push(b'\n');
    if u64::try_from(bytes.len()).map_or(true, |size| size > config.limits.max_checkpoint_bytes) {
        return Err(ReducerError::CheckpointCorrupt("budget_exceeded"));
    }
    let directory = config.output_dir.join("checkpoints");
    let path = directory.join(format!("{:020}.json", checkpoint.generation));
    let temp_path = directory.join("checkpoint.tmp");
    reject_non_regular_if_present(&path)?;
    reject_non_regular_if_present(&temp_path)?;
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temp_path)?;
    file.write_all(&bytes)?;
    file.sync_all()?;
    inject_fault(fault_point, FaultPoint::BeforeCheckpointPublish)?;
    fs::hard_link(&temp_path, &path)?;
    fs::remove_file(&temp_path)?;
    sync_dir(&directory)?;
    let paths = checkpoint_paths(&config.output_dir, MAX_CHECKPOINT_FILES)?;
    let remove_count = paths.len().saturating_sub(2);
    for old in paths.into_iter().take(remove_count) {
        fs::remove_file(old)?;
    }
    sync_dir(&directory)?;
    Ok(())
}

fn write_wal(
    output_dir: &Path,
    wal: &WalRecord,
    maximum: u64,
    fault_point: &mut Option<FaultPoint>,
) -> Result<String, ReducerError> {
    let path = output_dir.join("coverage.wal");
    let temp_path = output_dir.join("coverage.wal.tmp");
    reject_non_regular_if_present(&path)?;
    reject_non_regular_if_present(&temp_path)?;
    let mut bytes = serde_json::to_vec(wal)?;
    bytes.push(b'\n');
    if u64::try_from(bytes.len()).map_or(true, |size| size > maximum) {
        return Err(ReducerError::CheckpointCorrupt("wal_budget_exceeded"));
    }
    let wal_sha256 = sha256_bytes(&bytes);
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temp_path)?;
    file.write_all(&bytes)?;
    file.sync_all()?;
    inject_fault(fault_point, FaultPoint::BeforeWalPublish)?;
    fs::hard_link(&temp_path, &path)?;
    fs::remove_file(&temp_path)?;
    sync_dir(output_dir)?;
    Ok(wal_sha256)
}

fn restore_wal_claim(output_dir: &Path) -> Result<(), ReducerError> {
    let path = output_dir.join("coverage.wal");
    let claim = output_dir.join("coverage.wal.claim");
    reject_non_regular_if_present(&path)?;
    reject_non_regular_if_present(&claim)?;
    if !claim.exists() {
        return Ok(());
    }
    if path.exists() {
        return Err(ReducerError::WalCorrupt("multiple_owners"));
    }
    fs::rename(&claim, &path)?;
    sync_dir(output_dir)
}

fn remove_wal(output_dir: &Path, expected_sha256: &str) -> Result<(), ReducerError> {
    let path = output_dir.join("coverage.wal");
    let claim = output_dir.join("coverage.wal.claim");
    reject_non_regular_if_present(&path)?;
    reject_non_regular_if_present(&claim)?;
    if claim.exists() {
        return Err(ReducerError::WalCorrupt("claim_exists"));
    }
    fs::rename(&path, &claim)?;
    sync_dir(output_dir)?;
    let bytes = bounded_read(&claim, HARD_MAX_CHECKPOINT_BYTES)?;
    if sha256_bytes(&bytes) != expected_sha256 {
        if !path.exists() {
            fs::rename(&claim, &path)?;
            sync_dir(output_dir)?;
        }
        return Err(ReducerError::WalCorrupt("ownership_changed"));
    }
    fs::remove_file(&claim)?;
    sync_dir(output_dir)
}

fn decode_wal_bytes(wal: &WalRecord) -> Result<(Vec<u8>, Vec<u8>), ReducerError> {
    let output = BASE64
        .decode(&wal.output_base64)
        .map_err(|_| ReducerError::WalCorrupt("output_base64"))?;
    let ledger = BASE64
        .decode(&wal.ledger_line_base64)
        .map_err(|_| ReducerError::WalCorrupt("ledger_base64"))?;
    if sha256_bytes(&output) != wal.output_sha256
        || sha256_bytes(&ledger) != wal.ledger_line_sha256
        || !output.ends_with(b"\n")
        || !ledger.ends_with(b"\n")
    {
        return Err(ReducerError::WalCorrupt("hash"));
    }
    Ok((output, ledger))
}

fn validate_wal_semantics(
    config: &ReducerConfig,
    slot: u64,
    output: &[u8],
    ledger_line: &[u8],
    coverage: &CoverageRecord,
    pending: &BTreeMap<u64, PendingSlot>,
    completed: &BTreeMap<u64, CompletedSlot>,
) -> Result<(), ReducerError> {
    let (expected_output, expected_transaction_count) =
        expected_slot_output_from_state(config, slot, pending, completed)?;
    let mut expected_ledger_line = serde_json::to_vec(coverage)?;
    expected_ledger_line.push(b'\n');
    if output != expected_output
        || ledger_line != expected_ledger_line
        || coverage.output_sha256 != sha256_bytes(&expected_output)
        || coverage.transaction_count != expected_transaction_count
        || coverage.possible_leader_skipped_observed
    {
        return Err(ReducerError::OutputCorrupt("semantic_output"));
    }
    Ok(())
}

fn recover_wal(
    config: &ReducerConfig,
    identity_sha256: &str,
    pending: &BTreeMap<u64, PendingSlot>,
    completed: &BTreeMap<u64, CompletedSlot>,
) -> Result<(), ReducerError> {
    let path = config.output_dir.join("coverage.wal");
    if !path.exists() {
        return Ok(());
    }
    reject_non_regular_if_present(&path)?;
    let bytes = bounded_read(&path, config.limits.max_checkpoint_bytes)
        .map_err(|_| ReducerError::WalCorrupt("oversized"))?;
    let wal_sha256 = sha256_bytes(&bytes);
    if !bytes.ends_with(b"\n") {
        return Err(ReducerError::WalCorrupt("unterminated"));
    }
    let wal: WalRecord =
        serde_json::from_slice(&bytes).map_err(|_| ReducerError::WalCorrupt("invalid_json"))?;
    let mut canonical_wal =
        serde_json::to_vec(&wal).map_err(|_| ReducerError::WalCorrupt("invalid_json"))?;
    canonical_wal.push(b'\n');
    if bytes != canonical_wal {
        return Err(ReducerError::WalCorrupt("noncanonical"));
    }
    if wal.schema_version != WAL_SCHEMA || !valid_sha256(&wal.ledger_prefix_sha256) {
        return Err(ReducerError::WalCorrupt("schema"));
    }
    if wal.identity_sha256 != identity_sha256 {
        return Err(ReducerError::ProvenanceMismatch);
    }
    let (output, ledger_line) = decode_wal_bytes(&wal)?;
    if ledger_line[..ledger_line.len() - 1].contains(&b'\n') {
        return Err(ReducerError::WalCorrupt("ledger_line"));
    }
    let coverage: CoverageRecord = serde_json::from_slice(&ledger_line[..ledger_line.len() - 1])
        .map_err(|_| ReducerError::WalCorrupt("ledger_line"))?;
    if coverage.schema_version != COVERAGE_SCHEMA
        || coverage.identity_sha256 != identity_sha256
        || coverage.slot != wal.slot
        || coverage.output_sha256 != wal.output_sha256
        || coverage.research_ready
    {
        return Err(ReducerError::WalCorrupt("ledger_line"));
    }
    validate_wal_semantics(
        config,
        wal.slot,
        &output,
        &ledger_line,
        &coverage,
        pending,
        completed,
    )?;

    // Validate every recovery precondition before publishing any immutable bytes.
    let ledger_path = config.output_dir.join("coverage.ndjson");
    let current = bounded_read(&ledger_path, config.limits.max_output_bytes)?;
    let offset =
        usize::try_from(wal.ledger_offset).map_err(|_| ReducerError::WalCorrupt("offset"))?;
    if current.len() < offset || sha256_bytes(&current[..offset]) != wal.ledger_prefix_sha256 {
        return Err(ReducerError::WalCorrupt("ledger_prefix"));
    }
    let prefix_records = parse_coverage_bytes(&current[..offset])?;
    validate_recovery_prefix(identity_sha256, wal.slot, &prefix_records)?;
    validate_coverage_records_and_outputs(
        config,
        identity_sha256,
        &prefix_records,
        pending,
        completed,
        Some(wal.slot),
    )?;
    let tail = &current[offset..];
    let partial_tail = if tail.is_empty() || tail == ledger_line {
        false
    } else if ledger_line.starts_with(tail) && tail.len() < ledger_line.len() {
        true
    } else {
        return Err(ReducerError::WalCorrupt("ledger_tail"));
    };
    let output_path = config.output_dir.join(format!("slots/{}.json", wal.slot));
    let output_exists = output_path.exists();
    if output_exists && bounded_read(&output_path, config.limits.max_output_bytes)? != output {
        return Err(ReducerError::OutputCorrupt("wal_output_mismatch"));
    }
    enforce_recovery_output_budget(config, output_exists, output.len(), tail, &ledger_line)?;
    cleanup_legacy_wal_slot_temp(config, wal.slot)?;

    if !output_exists {
        let mut no_fault = None;
        publish_immutable_output(&config.output_dir, wal.slot, &output, &mut no_fault)?;
    }
    if tail.is_empty() {
        append_ledger_exact(&ledger_path, wal.ledger_offset, &ledger_line)?;
    } else if partial_tail {
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .open(&ledger_path)?;
        file.set_len(wal.ledger_offset)?;
        file.sync_all()?;
        append_ledger_exact(&ledger_path, wal.ledger_offset, &ledger_line)?;
    }
    remove_wal(&config.output_dir, &wal_sha256)
}

fn is_legacy_slot_temp_name(name: &str, slot: u64) -> bool {
    let prefix = format!(".{slot}.");
    let Some(token) = name
        .strip_prefix(&prefix)
        .and_then(|value| value.strip_suffix(".tmp"))
    else {
        return false;
    };
    token.len() == 36
        && token.bytes().enumerate().all(|(index, byte)| {
            if matches!(index, 8 | 13 | 18 | 23) {
                byte == b'-'
            } else {
                byte.is_ascii_hexdigit()
            }
        })
}

fn cleanup_legacy_wal_slot_temp(config: &ReducerConfig, slot: u64) -> Result<(), ReducerError> {
    let slots_dir = config.output_dir.join("slots");
    let mut matched = None;
    let mut entry_count = 0_usize;
    let maximum_entries = max_slot_artifact_count(config)?.saturating_add(1);
    for entry in fs::read_dir(&slots_dir)? {
        let path = entry?.path();
        entry_count = entry_count
            .checked_add(1)
            .ok_or(ReducerError::OutputCorrupt("slot_artifact_limit"))?;
        if entry_count > maximum_entries {
            return Err(ReducerError::OutputCorrupt("slot_artifact_limit"));
        }
        let Some(name) = path.file_name().and_then(|value| value.to_str()) else {
            continue;
        };
        if is_legacy_slot_temp_name(name, slot) {
            reject_non_regular_if_present(&path)?;
            if matched.replace(path).is_some() {
                return Err(ReducerError::OutputCorrupt("duplicate_slot_temp"));
            }
        }
    }
    if let Some(path) = matched {
        fs::remove_file(path)?;
        sync_dir(&slots_dir)?;
    }
    Ok(())
}

fn publish_immutable_output(
    output_dir: &Path,
    slot: u64,
    bytes: &[u8],
    fault_point: &mut Option<FaultPoint>,
) -> Result<(), ReducerError> {
    let slots_dir = output_dir.join("slots");
    let final_path = slots_dir.join(format!("{slot}.json"));
    if final_path.symlink_metadata().is_ok() {
        return Err(ReducerError::OutputAlreadyExists);
    }
    let temp_path = slots_dir.join("slot-output.tmp");
    reject_non_regular_if_present(&temp_path)?;
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temp_path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    inject_fault(fault_point, FaultPoint::BeforeOutputPublish)?;
    fs::hard_link(&temp_path, &final_path).map_err(|error| {
        if error.kind() == std::io::ErrorKind::AlreadyExists {
            ReducerError::OutputAlreadyExists
        } else {
            ReducerError::Io(error)
        }
    })?;
    fs::remove_file(&temp_path)?;
    sync_dir(&slots_dir)
}

fn append_ledger_exact(path: &Path, expected_offset: u64, line: &[u8]) -> Result<(), ReducerError> {
    reject_non_regular_if_present(path)?;
    let mut file = OpenOptions::new().read(true).write(true).open(path)?;
    if file.metadata()?.len() != expected_offset {
        return Err(ReducerError::WalCorrupt("ledger_offset_changed"));
    }
    file.seek(SeekFrom::Start(expected_offset))?;
    file.write_all(line)?;
    file.sync_all()?;
    Ok(())
}

fn coverage_slots(
    output_dir: &Path,
    maximum: u64,
) -> Result<BTreeMap<u64, CoverageRecord>, ReducerError> {
    let bytes = bounded_read(&output_dir.join("coverage.ndjson"), maximum)?;
    parse_coverage_bytes(&bytes)
}

fn parse_coverage_bytes(bytes: &[u8]) -> Result<BTreeMap<u64, CoverageRecord>, ReducerError> {
    if !bytes.is_empty() && !bytes.ends_with(b"\n") {
        return Err(ReducerError::OutputCorrupt("coverage_unterminated"));
    }
    let mut records = BTreeMap::new();
    for line in bytes
        .split(|byte| *byte == b'\n')
        .filter(|line| !line.is_empty())
    {
        let record: CoverageRecord = serde_json::from_slice(line)
            .map_err(|_| ReducerError::OutputCorrupt("coverage_json"))?;
        let canonical_record = serde_json::to_vec(&record)
            .map_err(|_| ReducerError::OutputCorrupt("coverage_json"))?;
        if line != canonical_record {
            return Err(ReducerError::OutputCorrupt("coverage_noncanonical"));
        }
        if record.schema_version != COVERAGE_SCHEMA
            || record.research_ready
            || records.insert(record.slot, record).is_some()
        {
            return Err(ReducerError::OutputCorrupt("coverage_record"));
        }
    }
    Ok(records)
}

fn validate_coverage_and_outputs(
    config: &ReducerConfig,
    identity_sha256: &str,
    pending: &BTreeMap<u64, PendingSlot>,
    completed: &BTreeMap<u64, CompletedSlot>,
) -> Result<(), ReducerError> {
    let records = coverage_slots(&config.output_dir, config.limits.max_output_bytes)?;
    validate_coverage_records_and_outputs(
        config,
        identity_sha256,
        &records,
        pending,
        completed,
        None,
    )
}

fn validate_coverage_records_and_outputs(
    config: &ReducerConfig,
    identity_sha256: &str,
    records: &BTreeMap<u64, CoverageRecord>,
    pending: &BTreeMap<u64, PendingSlot>,
    completed: &BTreeMap<u64, CompletedSlot>,
    allowed_wal_slot: Option<u64>,
) -> Result<(), ReducerError> {
    let mut allowed_state_slots = completed
        .keys()
        .copied()
        .collect::<std::collections::BTreeSet<_>>();
    for (slot, state) in pending {
        if state.block.as_ref().is_some_and(|block| {
            usize::try_from(block.executed_transaction_count).is_ok_and(|expected| {
                state.transactions.len() == expected
                    && state.transactions.keys().copied().eq(0..expected)
            })
        }) {
            allowed_state_slots.insert(*slot);
        }
    }
    let record_slots = records
        .keys()
        .copied()
        .collect::<std::collections::BTreeSet<_>>();
    if !record_slots.is_subset(&allowed_state_slots)
        || completed
            .keys()
            .any(|slot| !record_slots.contains(slot) && Some(*slot) != allowed_wal_slot)
    {
        return Err(ReducerError::OutputCorrupt("coverage_state_set"));
    }

    let mut artifact_slots = std::collections::BTreeSet::new();
    let mut allowed_temp_seen = false;
    let mut directory_entry_count = 0_usize;
    let maximum_artifacts = max_slot_artifact_count(config)?;
    let mut aggregate = regular_file_len(&config.output_dir.join("coverage.ndjson"))?;
    for entry in fs::read_dir(config.output_dir.join("slots"))? {
        let path = entry?.path();
        directory_entry_count = directory_entry_count
            .checked_add(1)
            .ok_or(ReducerError::OutputCorrupt("slot_artifact_limit"))?;
        if directory_entry_count
            > maximum_artifacts.saturating_add(usize::from(allowed_wal_slot.is_some()))
        {
            return Err(ReducerError::OutputCorrupt("slot_artifact_limit"));
        }
        reject_non_regular_if_present(&path)?;
        let name = path
            .file_name()
            .and_then(|value| value.to_str())
            .ok_or(ReducerError::OutputCorrupt("slot_filename"))?;
        if allowed_wal_slot.is_some_and(|slot| is_legacy_slot_temp_name(name, slot)) {
            if allowed_temp_seen {
                return Err(ReducerError::OutputCorrupt("duplicate_slot_temp"));
            }
            allowed_temp_seen = true;
            continue;
        }
        let slot = name
            .strip_suffix(".json")
            .and_then(|value| value.parse::<u64>().ok())
            .ok_or(ReducerError::OutputCorrupt("slot_filename"))?;
        if !artifact_slots.insert(slot) {
            return Err(ReducerError::OutputCorrupt("duplicate_slot_artifact"));
        }
        if artifact_slots.len() > maximum_artifacts {
            return Err(ReducerError::OutputCorrupt("slot_artifact_limit"));
        }
        aggregate = aggregate
            .checked_add(fs::metadata(&path)?.len())
            .ok_or(ReducerError::OutputBudgetExceeded)?;
        if aggregate > config.limits.max_output_bytes {
            return Err(ReducerError::OutputBudgetExceeded);
        }
    }
    let mut allowed_artifacts = record_slots.clone();
    if let Some(slot) = allowed_wal_slot {
        allowed_artifacts.insert(slot);
    }
    if !artifact_slots.is_subset(&allowed_artifacts)
        || record_slots
            .iter()
            .any(|slot| !artifact_slots.contains(slot))
    {
        return Err(ReducerError::OutputCorrupt("artifact_coverage_set"));
    }

    for (slot, record) in records {
        if record.identity_sha256 != identity_sha256 {
            return Err(ReducerError::ProvenanceMismatch);
        }
        let (expected_bytes, transaction_count) =
            expected_slot_output_from_state(config, *slot, pending, completed)?;
        let path = config.output_dir.join(format!("slots/{slot}.json"));
        let bytes = bounded_read(&path, config.limits.max_output_bytes)
            .map_err(|_| ReducerError::OutputCorrupt("missing_output"))?;
        if bytes != expected_bytes
            || sha256_bytes(&bytes) != record.output_sha256
            || record.transaction_count != transaction_count
        {
            return Err(ReducerError::OutputCorrupt("semantic_output"));
        }
    }
    Ok(())
}

fn expected_slot_output_from_state(
    config: &ReducerConfig,
    slot: u64,
    pending: &BTreeMap<u64, PendingSlot>,
    completed: &BTreeMap<u64, CompletedSlot>,
) -> Result<(Vec<u8>, usize), ReducerError> {
    if let Some(state) = completed.get(&slot) {
        return Ok((
            build_slot_output_bytes(config, &state.block, &state.transactions)?,
            state.transactions.len(),
        ));
    }
    let state = pending
        .get(&slot)
        .ok_or(ReducerError::OutputCorrupt("missing_checkpoint_state"))?;
    Ok((
        build_slot_output_bytes(
            config,
            state
                .block
                .as_ref()
                .ok_or(ReducerError::OutputCorrupt("missing_checkpoint_block"))?,
            &state.transactions,
        )?,
        state.transactions.len(),
    ))
}

fn validate_recovery_prefix(
    identity_sha256: &str,
    wal_slot: u64,
    records: &BTreeMap<u64, CoverageRecord>,
) -> Result<(), ReducerError> {
    if records
        .values()
        .any(|record| record.identity_sha256 != identity_sha256)
    {
        return Err(ReducerError::ProvenanceMismatch);
    }
    if records.contains_key(&wal_slot) {
        return Err(ReducerError::WalCorrupt("slot_already_covered"));
    }
    Ok(())
}

fn enforce_recovery_output_budget(
    config: &ReducerConfig,
    output_exists: bool,
    output_bytes: usize,
    tail: &[u8],
    ledger_line: &[u8],
) -> Result<(), ReducerError> {
    let additional_output_bytes = if output_exists { 0 } else { output_bytes };
    let additional_ledger_bytes = if tail == ledger_line {
        0
    } else {
        ledger_line
            .len()
            .checked_sub(tail.len())
            .ok_or(ReducerError::OutputBudgetExceeded)?
    };
    enforce_output_budget(
        config,
        additional_output_bytes,
        additional_ledger_bytes,
        usize::from(!output_exists),
    )
}

fn enforce_output_budget(
    config: &ReducerConfig,
    output_bytes: usize,
    ledger_bytes: usize,
    additional_artifacts: usize,
) -> Result<(), ReducerError> {
    let mut current = regular_file_len(&config.output_dir.join("coverage.ndjson"))?;
    let max_artifacts = max_slot_artifact_count(config)?;
    let mut artifact_count = 0_usize;
    for entry in fs::read_dir(config.output_dir.join("slots"))? {
        let path = entry?.path();
        reject_non_regular_if_present(&path)?;
        artifact_count = artifact_count
            .checked_add(1)
            .ok_or(ReducerError::OutputCorrupt("slot_artifact_limit"))?;
        if artifact_count
            .checked_add(additional_artifacts)
            .is_none_or(|count| count > max_artifacts)
        {
            return Err(ReducerError::OutputCorrupt("slot_artifact_limit"));
        }
        current = current
            .checked_add(fs::metadata(path)?.len())
            .ok_or(ReducerError::OutputBudgetExceeded)?;
    }
    let additional = u64::try_from(
        output_bytes
            .checked_add(ledger_bytes)
            .ok_or(ReducerError::OutputBudgetExceeded)?,
    )
    .map_err(|_| ReducerError::OutputBudgetExceeded)?;
    if current
        .checked_add(additional)
        .is_none_or(|total| total > config.limits.max_output_bytes)
    {
        return Err(ReducerError::OutputBudgetExceeded);
    }
    Ok(())
}

fn max_slot_artifact_count(config: &ReducerConfig) -> Result<usize, ReducerError> {
    usize::try_from(config.source_manifest.slots_file_entry_count)
        .map_err(|_| ReducerError::InvalidConfig("source_manifest"))
}

fn sha256_bytes(bytes: &[u8]) -> String {
    hex_lower(&Sha256::digest(bytes))
}

fn validate_config(config: &ReducerConfig) -> Result<(), ReducerError> {
    if jetstreamer_firehose::UPSTREAM_GIT_SHA != JETSTREAMER_V0_7_0_GIT_SHA
        || jetstreamer_firehose::UPSTREAM_FIREHOSE_RS_SHA256
            != JETSTREAMER_V0_7_0_FIREHOSE_RS_SHA256
        || jetstreamer_firehose::SOLANA_RUNTIME_CRATE_CHECKSUM
            != SOLANA_RUNTIME_V3_1_12_CRATE_CHECKSUM
        || jetstreamer_firehose::SOLANA_RUNTIME_REWARDS_SOURCE_SHA256
            != SOLANA_RUNTIME_V3_1_12_REWARDS_SOURCE_SHA256
        || config.adapter_provenance.jetstreamer_git_sha != JETSTREAMER_V0_7_0_GIT_SHA
        || config.reducer_provenance.jetstreamer_git_sha != JETSTREAMER_V0_7_0_GIT_SHA
    {
        return Err(ReducerError::JetstreamerRevisionMismatch);
    }
    if config.source_manifest.schema_version != "OLD_FAITHFUL_EPOCH_SOURCE_1"
        || config.adapter_provenance.schema_version != "JETSTREAMER_ADAPTER_PROVENANCE_1"
        || config.reducer_provenance.schema_version != "OLD_FAITHFUL_RUST_REDUCER_PROVENANCE_1"
    {
        return Err(ReducerError::InvalidConfig("schema_version"));
    }
    let source = &config.source_manifest;
    let expected_start = source
        .epoch
        .checked_mul(432_000)
        .ok_or(ReducerError::InvalidConfig("epoch_range"))?;
    let expected_end = expected_start
        .checked_add(432_000)
        .ok_or(ReducerError::InvalidConfig("epoch_range"))?;
    if source.slot_range.start_inclusive != expected_start
        || source.slot_range.end_exclusive != expected_end
        || source.slots_file_size_bytes == 0
        || source.slots_file_size_bytes > MAX_SLOTS_FILE_BYTES
        || source.slots_file_entry_count == 0
        || source.slots_file_entry_count > 432_001
        || source.slots_last < source.slots_first
        || source.slots_first < expected_start.saturating_sub(1)
        || source.slots_first >= expected_end
        || source.slots_last >= expected_end
        || !valid_epoch_cid(&source.epoch_cid)
        || !valid_sha256(&source.car_sha256)
        || !valid_sha256(&source.slots_file_sha256)
        || !valid_positive_u64_text(&source.car_file_size_bytes)
    {
        return Err(ReducerError::InvalidConfig("source_manifest"));
    }
    let adapter = &config.adapter_provenance;
    let reducer = &config.reducer_provenance;
    if !valid_git_sha(&adapter.jetstreamer_git_sha)
        || !valid_git_sha(&adapter.plugin_git_sha)
        || !valid_sha256(&adapter.plugin_source_sha256)
        || !valid_git_sha(&reducer.reducer_git_sha)
        || !valid_sha256(&reducer.reducer_source_sha256)
        || !valid_git_sha(&reducer.jetstreamer_git_sha)
    {
        return Err(ReducerError::InvalidConfig("provenance"));
    }
    if config.limits.max_pending_slots == 0
        || config.limits.max_pending_slots > HARD_MAX_PENDING_SLOTS
        || config.limits.max_transactions_per_slot == 0
        || config.limits.max_transactions_per_slot > HARD_MAX_TRANSACTIONS_PER_SLOT
        || config.limits.max_output_bytes == 0
        || config.limits.max_output_bytes > HARD_MAX_OUTPUT_BYTES
        || config.limits.max_checkpoint_bytes == 0
        || config.limits.max_checkpoint_bytes > HARD_MAX_CHECKPOINT_BYTES
        || config.limits.max_runtime_seconds == 0
        || config.limits.max_runtime_seconds > HARD_MAX_RUNTIME_SECONDS
    {
        return Err(ReducerError::InvalidConfig("limits"));
    }
    Ok(())
}

fn valid_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn valid_git_sha(value: &str) -> bool {
    value.len() == 40
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn valid_positive_u64_text(value: &str) -> bool {
    !value.is_empty()
        && value != "0"
        && !value.starts_with('0')
        && value.bytes().all(|byte| byte.is_ascii_digit())
        && value.parse::<u64>().is_ok()
}

fn valid_epoch_cid(value: &str) -> bool {
    if value.len() != 59 || !value.starts_with('b') {
        return false;
    }
    let alphabet = b"abcdefghijklmnopqrstuvwxyz234567";
    let mut accumulator = 0_u32;
    let mut bits = 0_u32;
    let mut decoded = Vec::with_capacity(36);
    for byte in value.bytes().skip(1) {
        let Some(digit) = alphabet.iter().position(|candidate| *candidate == byte) else {
            return false;
        };
        accumulator = (accumulator << 5) | u32::try_from(digit).unwrap_or(u32::MAX);
        bits += 5;
        while bits >= 8 {
            bits -= 8;
            decoded.push(((accumulator >> bits) & 0xff) as u8);
            accumulator &= (1_u32 << bits).saturating_sub(1);
        }
    }
    (bits == 0 || accumulator == 0)
        && decoded.len() == 36
        && decoded[..4] == [0x01, 0x71, 0x12, 0x20]
}

#[allow(clippy::too_many_lines)]
fn project_observation(
    transaction: &TransactionData,
    block_time: Option<i64>,
    source_manifest: &OldFaithfulSourceManifest,
    adapter_provenance: &AdapterProvenance,
) -> Result<Option<Observation>, ReducerError> {
    let signature = transaction.signature.to_string();
    if transaction.is_vote {
        return Ok(None);
    }
    let meta = &transaction.transaction_status_meta;
    let (static_keys, top_level) = match &transaction.transaction.message {
        VersionedMessage::Legacy(message) => {
            if !meta.loaded_addresses.writable.is_empty()
                || !meta.loaded_addresses.readonly.is_empty()
            {
                return Err(ReducerError::InvalidTransaction("legacy_loaded_addresses"));
            }
            (&message.account_keys, &message.instructions)
        }
        VersionedMessage::V0(message) => {
            let writable = message
                .address_table_lookups
                .iter()
                .try_fold(0_usize, |total, lookup| {
                    total.checked_add(lookup.writable_indexes.len())
                })
                .ok_or(ReducerError::InvalidTransaction(
                    "v0_loaded_address_count_mismatch",
                ))?;
            let readonly = message
                .address_table_lookups
                .iter()
                .try_fold(0_usize, |total, lookup| {
                    total.checked_add(lookup.readonly_indexes.len())
                })
                .ok_or(ReducerError::InvalidTransaction(
                    "v0_loaded_address_count_mismatch",
                ))?;
            if writable != meta.loaded_addresses.writable.len()
                || readonly != meta.loaded_addresses.readonly.len()
            {
                return Err(ReducerError::InvalidTransaction(
                    "v0_loaded_address_count_mismatch",
                ));
            }
            (&message.account_keys, &message.instructions)
        }
    };

    let inner_groups =
        meta.inner_instructions
            .as_deref()
            .ok_or(ReducerError::InvalidTransaction(
                "missing_inner_instructions",
            ))?;
    let log_messages = meta
        .log_messages
        .clone()
        .ok_or(ReducerError::InvalidTransaction("missing_log_messages"))?;
    let pre_token_balance_values =
        meta.pre_token_balances
            .as_deref()
            .ok_or(ReducerError::InvalidTransaction(
                "missing_pre_token_balances",
            ))?;
    let post_token_balance_values =
        meta.post_token_balances
            .as_deref()
            .ok_or(ReducerError::InvalidTransaction(
                "missing_post_token_balances",
            ))?;
    if static_keys.is_empty() {
        return Err(ReducerError::InvalidTransaction(
            "empty_static_account_keys",
        ));
    }
    let mut resolved = static_keys.clone();
    resolved.extend(meta.loaded_addresses.writable.iter().copied());
    resolved.extend(meta.loaded_addresses.readonly.iter().copied());
    if resolved.len() > MAX_RESOLVED_ACCOUNT_KEYS {
        return Err(ReducerError::InvalidTransaction("too_many_account_keys"));
    }
    let mut unique = std::collections::HashSet::new();
    if !resolved.iter().all(|key| unique.insert(*key)) {
        return Err(ReducerError::InvalidTransaction("duplicate_account_key"));
    }
    if meta.pre_balances.len() != resolved.len() || meta.post_balances.len() != resolved.len() {
        return Err(ReducerError::InvalidTransaction(
            "native_balance_length_mismatch",
        ));
    }
    if top_level.len() > MAX_TOP_LEVEL_INSTRUCTIONS {
        return Err(ReducerError::InvalidTransaction("too_many_instructions"));
    }
    let account_strings = resolved.iter().map(ToString::to_string).collect::<Vec<_>>();
    let mut located = Vec::new();
    let mut references = 0_usize;
    let mut inner_by_parent: HashMap<usize, _> = HashMap::new();
    let mut inner_count = 0_usize;
    for group in inner_groups {
        let parent = usize::from(group.index);
        if parent >= top_level.len()
            || inner_by_parent
                .insert(parent, &group.instructions)
                .is_some()
        {
            return Err(ReducerError::InvalidTransaction(
                "invalid_inner_instruction_group",
            ));
        }
        inner_count = inner_count
            .checked_add(group.instructions.len())
            .ok_or(ReducerError::InvalidTransaction("too_many_instructions"))?;
        if inner_count > MAX_INNER_INSTRUCTIONS {
            return Err(ReducerError::InvalidTransaction("too_many_instructions"));
        }
    }
    for (parent, instruction) in top_level.iter().enumerate() {
        located.push(locate_instruction(
            instruction,
            &account_strings,
            static_keys.len(),
            "top_level",
            parent,
            None,
            Some(1),
            &mut references,
        )?);
        if let Some(inner) = inner_by_parent.get(&parent) {
            for (index, instruction) in inner.iter().enumerate() {
                located.push(locate_instruction(
                    &instruction.instruction,
                    &account_strings,
                    static_keys.len(),
                    "inner",
                    index,
                    Some(parent),
                    instruction.stack_height,
                    &mut references,
                )?);
            }
        }
    }
    let execution_status = if meta.status.is_ok() {
        "succeeded"
    } else {
        "failed"
    };
    let (pump_candidates, quarantines) = capture_candidates(
        transaction.slot,
        transaction.transaction_slot_index,
        &signature,
        execution_status,
        &located,
        &resolved,
    )?;
    if log_messages.len() > MAX_LOG_MESSAGES
        || log_messages
            .iter()
            .try_fold(0_usize, |total, message| total.checked_add(message.len()))
            .is_none_or(|total| total > MAX_LOG_BYTES)
    {
        return Err(ReducerError::InvalidTransaction("invalid_log_messages"));
    }
    let bronze = BronzeTransaction {
        schema_version: "PUMP_V2_BRONZE_TRANSACTION_1".into(),
        slot: transaction.slot,
        transaction_index: transaction.transaction_slot_index,
        signature,
        block_time: block_time.map_or_else(|| Ok(String::new()), canonical_block_time)?,
        execution_status: execution_status.into(),
        fee_lamports: meta.fee.to_string(),
        log_messages,
        account_keys: account_strings,
        pre_balances_lamports: meta.pre_balances.iter().map(u64::to_string).collect(),
        post_balances_lamports: meta.post_balances.iter().map(u64::to_string).collect(),
        instructions: located,
        pump_candidates,
        quarantines,
        pre_token_balances: token_balances(pre_token_balance_values, resolved.len())?,
        post_token_balances: token_balances(post_token_balance_values, resolved.len())?,
    };
    let observation = Observation {
        schema_version: "OLD_FAITHFUL_PUMP_V2_OBSERVATION_1".into(),
        source_manifest: source_manifest.clone(),
        source_manifest_sha256: source_manifest_sha256(source_manifest)?,
        adapter_provenance: adapter_provenance.clone(),
        adapter_provenance_sha256: adapter_provenance_sha256(adapter_provenance)?,
        static_account_count: static_keys.len(),
        bronze,
    };
    Ok(Some(observation))
}

#[allow(clippy::too_many_arguments)]
fn locate_instruction(
    instruction: &CompiledInstruction,
    account_keys: &[String],
    static_account_count: usize,
    location: &'static str,
    instruction_index: usize,
    parent_instruction_index: Option<usize>,
    stack_height: Option<u32>,
    references: &mut usize,
) -> Result<LocatedInstruction, ReducerError> {
    let program_id_index = usize::from(instruction.program_id_index);
    if program_id_index >= account_keys.len()
        || (location == "top_level" && program_id_index >= static_account_count)
    {
        return Err(ReducerError::InvalidTransaction("invalid_program_id_index"));
    }
    if instruction.data.len() > MAX_INSTRUCTION_DATA_BYTES {
        return Err(ReducerError::InvalidTransaction(
            "instruction_data_too_large",
        ));
    }
    if (location == "top_level" && stack_height != Some(1))
        || (location == "inner" && stack_height.is_some_and(|height| !(2..=9).contains(&height)))
    {
        return Err(ReducerError::InvalidTransaction("invalid_stack_height"));
    }
    let account_indices = instruction
        .accounts
        .iter()
        .map(|index| usize::from(*index))
        .collect::<Vec<_>>();
    if account_indices
        .iter()
        .any(|index| *index >= account_keys.len())
    {
        return Err(ReducerError::InvalidTransaction("invalid_account_index"));
    }
    *references =
        references
            .checked_add(account_indices.len())
            .ok_or(ReducerError::InvalidTransaction(
                "too_many_instruction_account_references",
            ))?;
    if *references > MAX_INSTRUCTION_ACCOUNT_REFERENCES {
        return Err(ReducerError::InvalidTransaction(
            "too_many_instruction_account_references",
        ));
    }
    Ok(LocatedInstruction {
        instruction_location: location.into(),
        parent_instruction_index,
        instruction_index,
        stack_height,
        program_id_index,
        program_id: account_keys[program_id_index].clone(),
        accounts: account_indices
            .iter()
            .map(|index| account_keys[*index].clone())
            .collect(),
        account_indices,
        data_hex: hex_lower(&instruction.data),
    })
}

fn capture_candidates(
    slot: u64,
    transaction_index: usize,
    signature: &str,
    execution_status: &str,
    instructions: &[LocatedInstruction],
    resolved: &[Address],
) -> Result<(Vec<PumpCandidate>, Vec<PumpQuarantine>), ReducerError> {
    let pump = Address::from_str(PUMP_PROGRAM_ID)
        .map_err(|_| ReducerError::InvalidTransaction("invalid_pump_program"))?;
    let mut candidates = Vec::new();
    let mut quarantines = Vec::new();
    let mut pda_cache = HashMap::new();
    for instruction in instructions {
        if instruction.program_id != PUMP_PROGRAM_ID {
            continue;
        }
        let discriminator_hex = instruction.data_hex.chars().take(16).collect::<String>();
        let known = known_discriminator(&discriminator_hex);
        let mut identities = BTreeMap::new();
        let account_set = instruction
            .account_indices
            .iter()
            .map(|index| resolved[*index])
            .collect::<std::collections::HashSet<_>>();
        for mint in &account_set {
            let curve = *pda_cache.entry(*mint).or_insert_with(|| {
                Address::find_program_address(&[b"bonding-curve", mint.as_ref()], &pump).0
            });
            if curve != *mint && account_set.contains(&curve) {
                identities.insert((mint.to_string(), curve.to_string()), (mint, curve));
            }
        }
        let identity = if identities.len() == 1 {
            identities.keys().next().cloned()
        } else {
            None
        };
        let event_key = format!(
            "{slot}:{transaction_index}:{signature}:{}:{}:{}:{}:{discriminator_hex}",
            instruction.instruction_location,
            instruction
                .parent_instruction_index
                .map_or_else(|| "-".into(), |index| index.to_string()),
            instruction.instruction_index,
            instruction.program_id,
        );
        let reason = if known.is_none() {
            Some("unknown_pump_discriminator")
        } else if identity.is_none() {
            Some("unresolved_pump_identity")
        } else {
            None
        };
        if let Some(reason) = reason {
            quarantines.push(PumpQuarantine {
                event_key: event_key.clone(),
                reason: reason.into(),
                discriminator_hex: discriminator_hex.clone(),
            });
        }
        candidates.push(PumpCandidate {
            event_key,
            instruction_location: instruction.instruction_location.clone(),
            parent_instruction_index: instruction.parent_instruction_index,
            instruction_index: instruction.instruction_index,
            discriminator_hex,
            discriminator_source: known.map(|value| value.2.into()),
            parser_status: if reason.is_some() {
                "quarantined"
            } else {
                "known_discriminator"
            }
            .into(),
            variant: known.map(|value| value.0.into()),
            kind: known.map(|value| value.1.into()),
            mint: identity.as_ref().map(|value| value.0.clone()),
            curve: identity.as_ref().map(|value| value.1.clone()),
            execution_status: execution_status.into(),
        });
    }
    Ok((candidates, quarantines))
}

fn known_discriminator(value: &str) -> Option<(&'static str, &'static str, &'static str)> {
    match value {
        "66063d1201daebea" => Some(("buy", "buy", "official_idl")),
        "33e685a4017f83ad" => Some(("sell", "sell", "official_idl")),
        "b817ee6167c5d33d" => Some(("buy_v2", "buy", "official_idl")),
        "5df6823ce7e940b2" => Some(("sell_v2", "sell", "official_idl")),
        "c2ab1c46684d5b2f" => Some(("buy_exact_quote_in_v2", "buy", "official_idl")),
        "38fc74089edfcd5f" => Some(("buy_exact_sol_in", "buy", "official_idl")),
        "e6345c8dd8b14540" => Some(("live_sell_dispatcher", "sell", "observed_runtime")),
        "0094d0da1f435eb0" => Some(("live_buy_dispatcher", "buy", "observed_runtime")),
        "1e7435e21cba7f11" => Some(("live_buy_v2_dispatcher", "buy", "observed_runtime")),
        "e822865bc7d49d0e" => Some((
            "live_buy_exact_sol_in_dispatcher",
            "buy",
            "observed_runtime",
        )),
        _ => None,
    }
}

fn token_balances(
    values: &[TransactionTokenBalance],
    account_count: usize,
) -> Result<Vec<TokenBalance>, ReducerError> {
    let mut previous = None;
    let balances = values
        .iter()
        .map(|value| {
            let index = usize::from(value.account_index);
            if index >= account_count || previous.is_some_and(|seen| value.account_index <= seen) {
                return Err(ReducerError::InvalidTransaction(
                    "invalid_token_balance_order",
                ));
            }
            if Address::from_str(&value.mint).is_err()
                || Address::from_str(&value.owner).is_err()
                || Address::from_str(&value.program_id).is_err()
                || value.ui_token_amount.amount.parse::<u64>().is_err()
                || (value.ui_token_amount.amount.starts_with('0')
                    && value.ui_token_amount.amount != "0")
            {
                return Err(ReducerError::InvalidTransaction("invalid_token_balance"));
            }
            previous = Some(value.account_index);
            Ok(TokenBalance {
                account_index: value.account_index,
                mint: value.mint.clone(),
                owner: value.owner.clone(),
                program_id: value.program_id.clone(),
                decimals: value.ui_token_amount.decimals,
                amount: value.ui_token_amount.amount.clone(),
            })
        })
        .collect::<Result<Vec<_>, _>>()?;
    Ok(balances)
}

fn source_manifest_sha256(manifest: &OldFaithfulSourceManifest) -> Result<String, ReducerError> {
    domain_hash("OLD_FAITHFUL_OF1_SOURCE_MANIFEST_1", manifest)
}

fn adapter_provenance_sha256(provenance: &AdapterProvenance) -> Result<String, ReducerError> {
    domain_hash("JETSTREAMER_ADAPTER_PROVENANCE_1", provenance)
}

fn domain_hash<T: Serialize>(domain: &str, value: &T) -> Result<String, ReducerError> {
    let mut hasher = Sha256::new();
    hasher.update(domain.as_bytes());
    hasher.update(b"\n");
    hasher.update(serde_json::to_vec(value)?);
    Ok(hex_lower(&hasher.finalize()))
}

fn canonical_block_time(unix_seconds: i64) -> Result<String, ReducerError> {
    let time = OffsetDateTime::from_unix_timestamp(unix_seconds)
        .map_err(|_| ReducerError::InvalidBlockTime)?;
    time.format(format_description!(
        "[year]-[month]-[day]T[hour]:[minute]:[second].[subsecond digits:3]Z"
    ))
    .map_err(|_| ReducerError::InvalidBlockTime)
}

fn hex_lower(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        output.push(char::from(HEX[usize::from(byte >> 4)]));
        output.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    output
}
