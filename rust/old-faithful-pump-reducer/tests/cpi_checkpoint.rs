#![allow(
    clippy::default_trait_access,
    clippy::struct_field_names,
    clippy::too_many_lines
)]

use std::{collections::BTreeMap, path::PathBuf};

use jetstreamer_firehose::firehose::TransactionData;
use old_faithful_pump_reducer::{
    AdapterProvenance, OldFaithfulSourceManifest, Phase5Reducer, ReducerConfig, ReducerLimits,
    ReducerProvenance, SlotRange,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};
use solana_address::Address;
use solana_message::{
    VersionedMessage, compiled_instruction::CompiledInstruction, legacy::Message as LegacyMessage,
    v0::LoadedAddresses,
};
use solana_signature::Signature;
use solana_transaction::versioned::VersionedTransaction;
use solana_transaction_status::{InnerInstruction, InnerInstructions, TransactionStatusMeta};

const START: u64 = 432_000_000;

fn config(output_dir: PathBuf) -> ReducerConfig {
    ReducerConfig {
        output_dir,
        source_manifest: OldFaithfulSourceManifest {
            schema_version: "OLD_FAITHFUL_EPOCH_SOURCE_1".into(),
            epoch: 1_000,
            epoch_cid: "bafyreihvgnloaiilehijbe42cloousmwvl2z666zr7wvqprs6wnqfeqo4q".into(),
            car_sha256: "3c9727378e617f5cba8b5e206bce8fc6ae5df34d3a4408eeb69aea4d62ac7218".into(),
            car_file_size_bytes: "767389334224".into(),
            slots_file_sha256: "b04cec20c168d256fadcebc8626b711ac12558b207142dd6872d0deb382e8930"
                .into(),
            slots_file_size_bytes: 4_317_820,
            slots_file_entry_count: 431_782,
            slots_first: 431_999_999,
            slots_last: 432_431_999,
            slot_range: SlotRange {
                start_inclusive: START,
                end_exclusive: START + 432_000,
            },
        },
        adapter_provenance: AdapterProvenance {
            schema_version: "JETSTREAMER_ADAPTER_PROVENANCE_1".into(),
            jetstreamer_git_sha: old_faithful_pump_reducer::JETSTREAMER_V0_7_0_GIT_SHA.into(),
            plugin_git_sha: "1".repeat(40),
            plugin_source_sha256: "2".repeat(64),
        },
        reducer_provenance: ReducerProvenance {
            schema_version: "OLD_FAITHFUL_RUST_REDUCER_PROVENANCE_1".into(),
            reducer_git_sha: "3".repeat(40),
            reducer_source_sha256: "4".repeat(64),
            jetstreamer_git_sha: old_faithful_pump_reducer::JETSTREAMER_V0_7_0_GIT_SHA.into(),
        },
        limits: ReducerLimits::test_defaults(),
    }
}

fn transaction(slot: u64) -> TransactionData {
    let signature = Signature::from([120_u8; 64]);
    TransactionData {
        slot,
        transaction_slot_index: 0,
        signature,
        message_hash: Default::default(),
        is_vote: false,
        transaction_status_meta: TransactionStatusMeta {
            status: Ok(()),
            fee: 5_000,
            pre_balances: vec![10_000],
            post_balances: vec![5_000],
            inner_instructions: Some(vec![InnerInstructions {
                index: 0,
                instructions: vec![InnerInstruction {
                    instruction: CompiledInstruction {
                        program_id_index: 0,
                        accounts: vec![],
                        data: vec![3],
                    },
                    stack_height: Some(2),
                }],
            }]),
            log_messages: Some(vec![]),
            pre_token_balances: Some(vec![]),
            post_token_balances: Some(vec![]),
            loaded_addresses: LoadedAddresses::default(),
            ..TransactionStatusMeta::default()
        },
        transaction: VersionedTransaction {
            signatures: vec![signature],
            message: VersionedMessage::Legacy(LegacyMessage {
                account_keys: vec![Address::from([120_u8; 32])],
                instructions: vec![
                    CompiledInstruction {
                        program_id_index: 0,
                        accounts: vec![],
                        data: vec![1],
                    },
                    CompiledInstruction {
                        program_id_index: 0,
                        accounts: vec![],
                        data: vec![2],
                    },
                ],
                ..LegacyMessage::default()
            }),
        },
    }
}

fn domain_hash<T: Serialize>(domain: &str, value: &T) -> String {
    let mut hash = Sha256::new();
    hash.update(domain.as_bytes());
    hash.update(b"\n");
    hash.update(serde_json::to_vec(value).unwrap());
    hex::encode(hash.finalize())
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct Source {
    slot: u64,
    transaction_slot_index: usize,
    signature: String,
    message_hash: String,
    is_vote: bool,
    transaction_status_meta_sha256: String,
    transaction_sha256: String,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct Instruction {
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

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct Bronze {
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
    instructions: Vec<Instruction>,
    pump_candidates: Vec<serde_json::Value>,
    quarantines: Vec<serde_json::Value>,
    pre_token_balances: Vec<serde_json::Value>,
    post_token_balances: Vec<serde_json::Value>,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct Observation {
    schema_version: String,
    source_manifest: OldFaithfulSourceManifest,
    source_manifest_sha256: String,
    adapter_provenance: AdapterProvenance,
    adapter_provenance_sha256: String,
    static_account_count: usize,
    bronze: Bronze,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct StoredTransaction {
    semantic_sha256: String,
    source: Source,
    observation: Option<Observation>,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct PendingSlot {
    block: Option<serde_json::Value>,
    possible_leader_skipped: bool,
    transactions: BTreeMap<usize, StoredTransaction>,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct Checkpoint {
    schema_version: String,
    identity_sha256: String,
    generation: u64,
    pending: BTreeMap<u64, PendingSlot>,
    completed: BTreeMap<u64, serde_json::Value>,
    state_sha256: String,
}

#[derive(Serialize)]
struct CanonicalTransaction<'a> {
    source: &'a Source,
    observation: Option<&'a Observation>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct CheckpointState<'a> {
    identity_sha256: &'a str,
    generation: u64,
    pending: &'a BTreeMap<u64, PendingSlot>,
    completed: &'a BTreeMap<u64, serde_json::Value>,
}

#[test]
fn checkpoint_rejects_inner_instruction_moved_after_later_top_level() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let slot = START + 130;
    {
        let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
        reducer.observe_transaction(&transaction(slot)).unwrap();
    }
    let checkpoint = std::fs::read_dir(output.join("checkpoints"))
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .max()
        .unwrap();
    let mut value: Checkpoint =
        serde_json::from_slice(&std::fs::read(&checkpoint).unwrap()).unwrap();
    let stored = value
        .pending
        .get_mut(&slot)
        .unwrap()
        .transactions
        .get_mut(&0)
        .unwrap();
    let instructions = &mut stored.observation.as_mut().unwrap().bronze.instructions;
    assert_eq!(instructions[1].instruction_location, "inner");
    let inner = instructions.remove(1);
    instructions.push(inner);
    stored.semantic_sha256 = domain_hash(
        "OLD_FAITHFUL_RUST_TRANSACTION_SEMANTICS_1",
        &CanonicalTransaction {
            source: &stored.source,
            observation: stored.observation.as_ref(),
        },
    );
    value.state_sha256 = domain_hash(
        "OLD_FAITHFUL_RUST_CHECKPOINT_STATE_1",
        &CheckpointState {
            identity_sha256: &value.identity_sha256,
            generation: value.generation,
            pending: &value.pending,
            completed: &value.completed,
        },
    );
    let mut bytes = serde_json::to_vec(&value).unwrap();
    bytes.push(b'\n');
    std::fs::write(&checkpoint, bytes).unwrap();

    assert!(
        Phase5Reducer::open(config(output)).is_err(),
        "accepted self-consistent noncanonical CPI instruction ordering"
    );
}
