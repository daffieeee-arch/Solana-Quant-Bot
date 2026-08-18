#![allow(clippy::default_trait_access)]

use std::{path::PathBuf, str::FromStr};

use jetstreamer_firehose::firehose::{BlockData, KeyedRewardsAndNumPartitions, TransactionData};
use old_faithful_pump_reducer::{
    AdapterProvenance, FaultPoint, ObserveOutcome, OldFaithfulSourceManifest, Phase5Reducer,
    ReducerConfig, ReducerLimits, ReducerProvenance, SlotRange,
};
use solana_address::Address;
use solana_message::{
    VersionedMessage,
    compiled_instruction::CompiledInstruction,
    legacy::Message as LegacyMessage,
    v0::{LoadedAddresses, Message as V0Message, MessageAddressTableLookup},
};
use solana_reward_info::{RewardInfo, RewardType};

use solana_signature::Signature;
use solana_transaction::versioned::VersionedTransaction;
use solana_transaction_status::TransactionStatusMeta;

const START: u64 = 432_000_000;

fn manifest() -> OldFaithfulSourceManifest {
    OldFaithfulSourceManifest {
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
    }
}

fn config(output_dir: PathBuf) -> ReducerConfig {
    ReducerConfig {
        output_dir,
        source_manifest: manifest(),
        adapter_provenance: AdapterProvenance {
            schema_version: "JETSTREAMER_ADAPTER_PROVENANCE_1".into(),
            jetstreamer_git_sha: old_faithful_pump_reducer::JETSTREAMER_V0_7_0_GIT_SHA.into(),
            plugin_git_sha: "1111111111111111111111111111111111111111".into(),
            plugin_source_sha256:
                "2222222222222222222222222222222222222222222222222222222222222222".into(),
        },
        reducer_provenance: ReducerProvenance {
            schema_version: "OLD_FAITHFUL_RUST_REDUCER_PROVENANCE_1".into(),
            reducer_git_sha: "3333333333333333333333333333333333333333".into(),
            reducer_source_sha256:
                "4444444444444444444444444444444444444444444444444444444444444444".into(),
            jetstreamer_git_sha: old_faithful_pump_reducer::JETSTREAMER_V0_7_0_GIT_SHA.into(),
        },
        limits: ReducerLimits::test_defaults(),
    }
}

fn checkpoint_with_generation(base: &serde_json::Value, generation: u64) -> Vec<u8> {
    use sha2::Digest as _;

    #[derive(serde::Serialize)]
    #[serde(rename_all = "camelCase")]
    struct CheckpointState<'a> {
        identity_sha256: &'a serde_json::Value,
        generation: u64,
        pending: &'a serde_json::Value,
        completed: &'a serde_json::Value,
    }

    #[derive(serde::Serialize)]
    #[serde(rename_all = "camelCase")]
    struct CheckpointEnvelope<'a> {
        schema_version: &'a serde_json::Value,
        identity_sha256: &'a serde_json::Value,
        generation: u64,
        pending: &'a serde_json::Value,
        completed: &'a serde_json::Value,
        state_sha256: String,
    }

    let mut checkpoint = base.clone();
    checkpoint["generation"] = generation.into();
    let state = CheckpointState {
        identity_sha256: &checkpoint["identitySha256"],
        generation,
        pending: &checkpoint["pending"],
        completed: &checkpoint["completed"],
    };
    let mut hasher = sha2::Sha256::new();
    hasher.update(b"OLD_FAITHFUL_RUST_CHECKPOINT_STATE_1\n");
    hasher.update(serde_json::to_vec(&state).unwrap());
    let envelope = CheckpointEnvelope {
        schema_version: &checkpoint["schemaVersion"],
        identity_sha256: &checkpoint["identitySha256"],
        generation,
        pending: &checkpoint["pending"],
        completed: &checkpoint["completed"],
        state_sha256: hex::encode(hasher.finalize()),
    };
    let mut bytes = serde_json::to_vec(&envelope).unwrap();
    bytes.push(b'\n');
    bytes
}

fn legacy_tx(slot: u64, index: usize, signature_byte: u8) -> TransactionData {
    let signature = Signature::from([signature_byte; 64]);
    TransactionData {
        slot,
        transaction_slot_index: index,
        signature,
        message_hash: Default::default(),
        is_vote: false,
        transaction_status_meta: TransactionStatusMeta {
            status: Ok(()),
            fee: 5_000,
            pre_balances: vec![10_000],
            post_balances: vec![5_000],
            inner_instructions: Some(vec![]),
            log_messages: Some(vec![]),
            pre_token_balances: Some(vec![]),
            post_token_balances: Some(vec![]),
            loaded_addresses: LoadedAddresses::default(),
            ..TransactionStatusMeta::default()
        },
        transaction: VersionedTransaction {
            signatures: vec![signature],
            message: VersionedMessage::Legacy(LegacyMessage {
                account_keys: vec![Address::from([signature_byte; 32])],
                ..LegacyMessage::default()
            }),
        },
    }
}

fn v0_tx(slot: u64, index: usize, signature_byte: u8) -> TransactionData {
    let signature = Signature::from([signature_byte; 64]);
    TransactionData {
        slot,
        transaction_slot_index: index,
        signature,
        message_hash: Default::default(),
        is_vote: false,
        transaction_status_meta: TransactionStatusMeta {
            status: Ok(()),
            fee: 5_000,
            pre_balances: vec![10_000, 0],
            post_balances: vec![5_000, 0],
            inner_instructions: Some(vec![]),
            log_messages: Some(vec![]),
            pre_token_balances: Some(vec![]),
            post_token_balances: Some(vec![]),
            loaded_addresses: LoadedAddresses {
                writable: vec![Address::from([9; 32])],
                readonly: vec![],
            },
            ..TransactionStatusMeta::default()
        },
        transaction: VersionedTransaction {
            signatures: vec![signature],
            message: VersionedMessage::V0(V0Message {
                account_keys: vec![Address::from([signature_byte; 32])],
                address_table_lookups: vec![MessageAddressTableLookup {
                    account_key: Address::from([8; 32]),
                    writable_indexes: vec![0],
                    readonly_indexes: vec![],
                }],
                ..V0Message::default()
            }),
        },
    }
}

fn block(slot: u64, count: u64) -> BlockData {
    block_with_time(slot, count, 1_700_000_000)
}

fn block_with_time(slot: u64, count: u64, block_time: i64) -> BlockData {
    BlockData::Block {
        parent_slot: slot - 1,
        parent_blockhash: Default::default(),
        slot,
        blockhash: Default::default(),
        rewards: KeyedRewardsAndNumPartitions {
            keyed_rewards: vec![],
            num_partitions: None,
        },
        block_time: Some(block_time),
        block_height: Some(200_000_000),
        executed_transaction_count: count,
        entry_count: 1,
    }
}

#[test]
fn exact_duplicate_compares_the_full_semantic_payload() {
    let temp = tempfile::tempdir().unwrap();
    let mut reducer = Phase5Reducer::open(config(temp.path().join("output"))).unwrap();
    let tx = legacy_tx(START + 1, 0, 1);
    assert_eq!(
        reducer.observe_transaction(&tx).unwrap(),
        ObserveOutcome::Pending
    );
    assert_eq!(
        reducer.observe_transaction(&tx).unwrap(),
        ObserveOutcome::Duplicate
    );

    let mut conflict = tx.clone();
    conflict.transaction_status_meta.fee += 1;
    assert_eq!(
        reducer
            .observe_transaction(&conflict)
            .unwrap_err()
            .to_string(),
        "conflicting_transaction_retry"
    );
}

#[test]
fn rejects_coordinate_and_signature_conflicts() {
    let temp = tempfile::tempdir().unwrap();
    let mut reducer = Phase5Reducer::open(config(temp.path().join("output"))).unwrap();
    let tx = legacy_tx(START + 2, 0, 2);
    reducer.observe_transaction(&tx).unwrap();

    let coordinate_conflict = legacy_tx(START + 2, 0, 3);
    assert_eq!(
        reducer
            .observe_transaction(&coordinate_conflict)
            .unwrap_err()
            .to_string(),
        "conflicting_transaction_retry"
    );
    let mut signature_conflict = tx.clone();
    signature_conflict.transaction_slot_index = 1;
    assert_eq!(
        reducer
            .observe_transaction(&signature_conflict)
            .unwrap_err()
            .to_string(),
        "conflicting_signature_identity"
    );
}

#[test]
fn joins_out_of_order_but_requires_contiguous_transaction_indices() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
    let slot = START + 3;
    reducer.observe_transaction(&legacy_tx(slot, 1, 4)).unwrap();
    reducer.observe_block(&block(slot, 2)).unwrap();
    assert_eq!(
        reducer.observe_transaction(&legacy_tx(slot, 0, 5)).unwrap(),
        ObserveOutcome::Committed { slot }
    );
    let value: serde_json::Value =
        serde_json::from_slice(&std::fs::read(output.join(format!("slots/{slot}.json"))).unwrap())
            .unwrap();
    assert_eq!(value["observations"][0]["bronze"]["transactionIndex"], 0);
    assert_eq!(value["observations"][1]["bronze"]["transactionIndex"], 1);

    let output2 = temp.path().join("gap");
    let mut gap = Phase5Reducer::open(config(output2)).unwrap();
    gap.observe_transaction(&legacy_tx(slot + 1, 1, 6)).unwrap();
    assert_eq!(
        gap.observe_block(&block(slot + 1, 1))
            .unwrap_err()
            .to_string(),
        "non_contiguous_transaction_indices"
    );
}

#[test]
fn block_rejects_an_observed_transaction_index_outside_its_declared_count() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let slot = START + 93;
    let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
    assert_eq!(
        reducer.observe_transaction(&legacy_tx(slot, 5, 1)).unwrap(),
        ObserveOutcome::Pending
    );
    assert_eq!(
        reducer
            .observe_block(&block(slot, 2))
            .unwrap_err()
            .to_string(),
        "non_contiguous_transaction_indices"
    );
    drop(reducer);
    let mut reopened = Phase5Reducer::open(config(output)).unwrap();
    assert_eq!(
        reopened
            .observe_block(&block(slot, 2))
            .unwrap_err()
            .to_string(),
        "non_contiguous_transaction_indices"
    );
}

#[test]
fn skip_block_skip_is_provisional_and_idempotent() {
    let temp = tempfile::tempdir().unwrap();
    let mut reducer = Phase5Reducer::open(config(temp.path().join("output"))).unwrap();
    let slot = START + 5;
    let skip = BlockData::PossibleLeaderSkipped { slot };
    assert_eq!(
        reducer.observe_block(&skip).unwrap(),
        ObserveOutcome::Pending
    );
    assert_eq!(
        reducer.observe_block(&block(slot, 0)).unwrap(),
        ObserveOutcome::Committed { slot }
    );
    assert_eq!(
        reducer.observe_block(&skip).unwrap(),
        ObserveOutcome::Duplicate
    );
    assert_eq!(
        reducer.observe_block(&block(slot, 0)).unwrap(),
        ObserveOutcome::Duplicate
    );
}

#[test]
fn restart_restores_pending_join_state_and_committed_dedup() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let tx = legacy_tx(START + 6, 0, 7);
    {
        let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
        reducer.observe_transaction(&tx).unwrap();
    }
    {
        let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
        assert_eq!(
            reducer.observe_block(&block(START + 6, 1)).unwrap(),
            ObserveOutcome::Committed { slot: START + 6 }
        );
    }
    let mut reducer = Phase5Reducer::open(config(output)).unwrap();
    assert_eq!(
        reducer.observe_transaction(&tx).unwrap(),
        ObserveOutcome::Duplicate
    );
}

#[test]
fn recovers_crashes_at_each_wal_seam() {
    for (offset, fault) in [
        FaultPoint::AfterWalSync,
        FaultPoint::AfterOutputSync,
        FaultPoint::AfterLedgerSync,
    ]
    .into_iter()
    .enumerate()
    {
        let temp = tempfile::tempdir().unwrap();
        let output = temp.path().join(format!("output-{offset}"));
        let slot = START + 10 + u64::try_from(offset).unwrap();
        {
            let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
            reducer
                .observe_transaction(&legacy_tx(slot, 0, 20 + u8::try_from(offset).unwrap()))
                .unwrap();
            reducer.set_fault_point(Some(fault));
            assert_eq!(
                reducer
                    .observe_block(&block(slot, 1))
                    .unwrap_err()
                    .to_string(),
                "injected_crash"
            );
        }
        let _recovered = Phase5Reducer::open(config(output.clone())).unwrap();
        assert!(output.join(format!("slots/{slot}.json")).is_file());
        assert!(!output.join("coverage.wal").exists());
        let ledger = std::fs::read_to_string(output.join("coverage.ndjson")).unwrap();
        assert_eq!(ledger.lines().count(), 1);
    }
}

#[test]
fn wal_checkpoint_and_slot_publication_ignore_crash_temps_until_atomic_link() {
    let temp = tempfile::tempdir().unwrap();

    let wal_output = temp.path().join("wal");
    let wal_slot = START + 94;
    {
        let mut reducer = Phase5Reducer::open(config(wal_output.clone())).unwrap();
        reducer.set_fault_point(Some(FaultPoint::BeforeWalPublish));
        assert!(reducer.observe_block(&block(wal_slot, 0)).is_err());
        assert!(!wal_output.join("coverage.wal").exists());
        assert!(wal_output.join("coverage.wal.tmp").exists());
    }
    std::fs::write(wal_output.join("coverage.wal.tmp"), b"{").unwrap();
    drop(Phase5Reducer::open(config(wal_output.clone())).unwrap());
    assert!(!wal_output.join("coverage.wal.tmp").exists());
    assert!(wal_output.join(format!("slots/{wal_slot}.json")).exists());

    let checkpoint_output = temp.path().join("checkpoint");
    {
        let mut reducer = Phase5Reducer::open(config(checkpoint_output.clone())).unwrap();
        reducer.set_fault_point(Some(FaultPoint::BeforeCheckpointPublish));
        assert!(
            reducer
                .observe_block(&BlockData::PossibleLeaderSkipped { slot: START + 95 })
                .is_err()
        );
    }
    let checkpoint_temp = checkpoint_output.join("checkpoints/checkpoint.tmp");
    assert!(checkpoint_temp.exists());
    std::fs::write(&checkpoint_temp, b"{\"schemaVersion\":").unwrap();
    let mut reopened = Phase5Reducer::open(config(checkpoint_output.clone())).unwrap();
    assert!(!checkpoint_temp.exists());
    assert_eq!(
        reopened
            .observe_block(&BlockData::PossibleLeaderSkipped { slot: START + 95 })
            .unwrap(),
        ObserveOutcome::Pending
    );

    let slot_output = temp.path().join("slot-output");
    let output_slot = START + 96;
    {
        let mut reducer = Phase5Reducer::open(config(slot_output.clone())).unwrap();
        reducer.set_fault_point(Some(FaultPoint::BeforeOutputPublish));
        assert!(reducer.observe_block(&block(output_slot, 0)).is_err());
        assert!(slot_output.join("coverage.wal").exists());
        assert!(slot_output.join("slots/slot-output.tmp").exists());
        assert!(
            !slot_output
                .join(format!("slots/{output_slot}.json"))
                .exists()
        );
    }
    let legacy_slot_temp = slot_output.join(format!(
        "slots/.{output_slot}.00000000-0000-0000-0000-000000000000.tmp"
    ));
    std::fs::rename(slot_output.join("slots/slot-output.tmp"), &legacy_slot_temp).unwrap();
    std::fs::write(&legacy_slot_temp, b"partial").unwrap();
    drop(Phase5Reducer::open(config(slot_output.clone())).unwrap());
    assert!(!legacy_slot_temp.exists());
    assert!(!slot_output.join("coverage.wal").exists());
    assert!(
        slot_output
            .join(format!("slots/{output_slot}.json"))
            .exists()
    );
}

#[test]
fn live_writer_rejects_authoritative_slots_directory_replacement() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
    std::fs::rename(output.join("slots"), output.join("slots.displaced")).unwrap();
    std::fs::create_dir(output.join("slots")).unwrap();
    assert_eq!(
        reducer
            .observe_block(&block(START + 97, 0))
            .unwrap_err()
            .to_string(),
        "unsafe_path"
    );
}

#[test]
fn corrupt_checkpoint_wal_output_and_symlink_paths_fail_closed() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("checkpoint-corrupt");
    {
        let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
        reducer
            .observe_transaction(&legacy_tx(START + 20, 0, 30))
            .unwrap();
    }
    let checkpoint = std::fs::read_dir(output.join("checkpoints"))
        .unwrap()
        .next()
        .unwrap()
        .unwrap()
        .path();
    std::fs::write(checkpoint, b"not-json\n").unwrap();
    assert!(
        Phase5Reducer::open(config(output))
            .unwrap_err()
            .to_string()
            .contains("checkpoint_corrupt")
    );

    let output = temp.path().join("wal-corrupt");
    {
        let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
        reducer
            .observe_transaction(&legacy_tx(START + 21, 0, 31))
            .unwrap();
        reducer.set_fault_point(Some(FaultPoint::AfterWalSync));
        reducer.observe_block(&block(START + 21, 1)).unwrap_err();
    }
    std::fs::write(output.join("coverage.wal"), b"not-json\n").unwrap();
    assert!(
        Phase5Reducer::open(config(output))
            .unwrap_err()
            .to_string()
            .contains("wal_corrupt")
    );

    let output = temp.path().join("output-corrupt");
    {
        let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
        reducer.observe_block(&block(START + 22, 0)).unwrap();
    }
    std::fs::write(output.join(format!("slots/{}.json", START + 22)), b"{}\n").unwrap();
    assert!(
        Phase5Reducer::open(config(output))
            .unwrap_err()
            .to_string()
            .contains("output_corrupt")
    );

    #[cfg(unix)]
    {
        let target = temp.path().join("real");
        std::fs::create_dir(&target).unwrap();
        let link = temp.path().join("linked-output");
        std::os::unix::fs::symlink(target, &link).unwrap();
        assert_eq!(
            Phase5Reducer::open(config(link)).unwrap_err().to_string(),
            "unsafe_path"
        );
    }
}

#[test]
fn provenance_mismatch_and_limits_fail_closed() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    drop(Phase5Reducer::open(config(output.clone())).unwrap());
    let mut mismatch = config(output);
    mismatch.reducer_provenance.reducer_git_sha = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".into();
    assert_eq!(
        Phase5Reducer::open(mismatch).unwrap_err().to_string(),
        "provenance_mismatch"
    );

    let mut bounded = config(temp.path().join("bounded"));
    bounded.limits.max_pending_slots = 1;
    let mut reducer = Phase5Reducer::open(bounded).unwrap();
    reducer
        .observe_transaction(&legacy_tx(START + 30, 0, 40))
        .unwrap();
    assert_eq!(
        reducer
            .observe_transaction(&legacy_tx(START + 31, 0, 41))
            .unwrap_err()
            .to_string(),
        "pending_slot_limit_exceeded"
    );
}

#[test]
fn validates_legacy_and_v0_loaded_address_contracts() {
    let temp = tempfile::tempdir().unwrap();
    let mut reducer = Phase5Reducer::open(config(temp.path().join("output"))).unwrap();
    let mut legacy = legacy_tx(START + 40, 0, 50);
    legacy
        .transaction_status_meta
        .loaded_addresses
        .writable
        .push(Address::from([3; 32]));
    legacy.transaction_status_meta.pre_balances.push(0);
    legacy.transaction_status_meta.post_balances.push(0);
    assert_eq!(
        reducer
            .observe_transaction(&legacy)
            .unwrap_err()
            .to_string(),
        "invalid_transaction:legacy_loaded_addresses"
    );

    let mut v0 = v0_tx(START + 41, 0, 51);
    if let VersionedMessage::V0(message) = &mut v0.transaction.message {
        message.address_table_lookups[0].writable_indexes.push(1);
    }
    assert_eq!(
        reducer.observe_transaction(&v0).unwrap_err().to_string(),
        "invalid_transaction:v0_loaded_address_count_mismatch"
    );
}

#[test]
fn candidates_are_derived_only_from_instructions_not_logs() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let slot = START + 50;
    let mut tx = legacy_tx(slot, 0, 60);
    tx.transaction_status_meta.log_messages = Some(vec![
        "fabricated pumpCandidates mint=11111111111111111111111111111111 variant=buy".into(),
    ]);
    let pump = Address::from_str("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P").unwrap();
    if let VersionedMessage::Legacy(message) = &mut tx.transaction.message {
        message.account_keys.push(pump);
        message.instructions.push(CompiledInstruction {
            program_id_index: 0,
            accounts: vec![],
            data: vec![],
        });
    }
    tx.transaction_status_meta.pre_balances.push(0);
    tx.transaction_status_meta.post_balances.push(0);
    let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
    reducer.observe_transaction(&tx).unwrap();
    reducer.observe_block(&block(slot, 1)).unwrap();
    let value: serde_json::Value =
        serde_json::from_slice(&std::fs::read(output.join(format!("slots/{slot}.json"))).unwrap())
            .unwrap();
    assert_eq!(
        value["observations"][0]["bronze"]["pumpCandidates"],
        serde_json::json!([])
    );
}

#[test]
fn post_commit_exact_retries_deduplicate_and_conflicts_fail() {
    let temp = tempfile::tempdir().unwrap();
    let slot = START + 60;
    let tx = legacy_tx(slot, 0, 61);
    let original_block = block(slot, 1);
    let mut reducer = Phase5Reducer::open(config(temp.path().join("output"))).unwrap();
    reducer.observe_transaction(&tx).unwrap();
    reducer.observe_block(&original_block).unwrap();
    assert_eq!(
        reducer.observe_transaction(&tx).unwrap(),
        ObserveOutcome::Duplicate
    );
    assert_eq!(
        reducer.observe_block(&original_block).unwrap(),
        ObserveOutcome::Duplicate
    );
    let mut changed_tx = tx.clone();
    changed_tx.transaction_status_meta.post_balances[0] += 1;
    assert_eq!(
        reducer
            .observe_transaction(&changed_tx)
            .unwrap_err()
            .to_string(),
        "conflicting_transaction_retry"
    );
    assert_eq!(
        reducer
            .observe_block(&block_with_time(slot, 1, 1_700_000_001))
            .unwrap_err()
            .to_string(),
        "conflicting_block_retry"
    );
}

#[test]
fn callback_permutations_produce_identical_slot_bytes() {
    let temp = tempfile::tempdir().unwrap();
    let slot = START + 61;
    let tx = legacy_tx(slot, 0, 62);
    let first = temp.path().join("first");
    let second = temp.path().join("second");
    let mut tx_first = Phase5Reducer::open(config(first.clone())).unwrap();
    tx_first.observe_transaction(&tx).unwrap();
    tx_first.observe_block(&block(slot, 1)).unwrap();
    let mut block_first = Phase5Reducer::open(config(second.clone())).unwrap();
    block_first.observe_block(&block(slot, 1)).unwrap();
    block_first.observe_transaction(&tx).unwrap();
    assert_eq!(
        std::fs::read(first.join(format!("slots/{slot}.json"))).unwrap(),
        std::fs::read(second.join(format!("slots/{slot}.json"))).unwrap()
    );
}

#[test]
fn corrupt_coverage_and_budget_overflow_fail_closed() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("coverage-corrupt");
    {
        let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
        reducer.observe_block(&block(START + 62, 0)).unwrap();
    }
    std::fs::write(output.join("coverage.ndjson"), b"{}\n").unwrap();
    assert!(
        Phase5Reducer::open(config(output))
            .unwrap_err()
            .to_string()
            .contains("output_corrupt")
    );

    let mut output_bounded = config(temp.path().join("output-budget"));
    output_bounded.limits.max_output_bytes = 1;
    let mut reducer = Phase5Reducer::open(output_bounded).unwrap();
    assert_eq!(
        reducer
            .observe_block(&block(START + 63, 0))
            .unwrap_err()
            .to_string(),
        "output_budget_exceeded"
    );

    let mut checkpoint_bounded = config(temp.path().join("checkpoint-budget"));
    checkpoint_bounded.limits.max_checkpoint_bytes = 1;
    assert!(
        Phase5Reducer::open(checkpoint_bounded)
            .unwrap_err()
            .to_string()
            .contains("checkpoint_corrupt")
    );
}

#[test]
fn restart_retries_a_fully_joined_slot_after_prepublication_failure() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("retry-ready-slot");
    let slot = START + 65;
    {
        let mut too_small = config(output.clone());
        too_small.limits.max_output_bytes = 1;
        let mut reducer = Phase5Reducer::open(too_small).unwrap();
        assert_eq!(
            reducer
                .observe_block(&block(slot, 0))
                .unwrap_err()
                .to_string(),
            "output_budget_exceeded"
        );
    }

    let _recovered = Phase5Reducer::open(config(output.clone())).unwrap();
    assert!(output.join(format!("slots/{slot}.json")).is_file());
    assert_eq!(
        std::fs::read_to_string(output.join("coverage.ndjson"))
            .unwrap()
            .lines()
            .count(),
        1
    );
}

#[test]
fn validates_manifest_provenance_and_required_metadata_before_acceptance() {
    let temp = tempfile::tempdir().unwrap();
    for mutation in 0_u8..4 {
        let output = temp.path().join(format!("invalid-{mutation}"));
        let mut candidate = config(output.clone());
        match mutation {
            0 => candidate.source_manifest.epoch_cid = format!("b{}", "a".repeat(58)),
            1 => candidate.source_manifest.car_sha256 = "A".repeat(64),
            2 => candidate.source_manifest.car_file_size_bytes = "01".into(),
            3 => candidate.adapter_provenance.plugin_source_sha256 = "g".repeat(64),
            _ => unreachable!(),
        }
        assert!(Phase5Reducer::open(candidate).is_err());
        assert!(!output.exists());
    }
    let mut reducer = Phase5Reducer::open(config(temp.path().join("metadata"))).unwrap();
    let mut tx = legacy_tx(START + 64, 0, 63);
    tx.transaction_status_meta.inner_instructions = None;
    assert_eq!(
        reducer.observe_transaction(&tx).unwrap_err().to_string(),
        "invalid_transaction:missing_inner_instructions"
    );
}

#[cfg(unix)]
#[test]
fn rejects_a_symlink_in_any_output_ancestor() {
    let temp = tempfile::tempdir().unwrap();
    let real_parent = temp.path().join("real-parent");
    std::fs::create_dir(&real_parent).unwrap();
    let linked_parent = temp.path().join("linked-parent");
    std::os::unix::fs::symlink(&real_parent, &linked_parent).unwrap();
    let output = linked_parent.join("output");

    assert_eq!(
        Phase5Reducer::open(config(output.clone()))
            .unwrap_err()
            .to_string(),
        "unsafe_path"
    );
    assert!(!real_parent.join("output").exists());
}

#[test]
fn combined_namespace_lock_replacement_cannot_admit_second_writer() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let first = Phase5Reducer::open(config(output.clone())).unwrap();
    let lock_path = std::fs::read_dir(temp.path())
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .find(|path| {
            path.file_name().is_some_and(|name| {
                name.to_string_lossy()
                    .starts_with(".old-faithful-pump-reducer-")
            }) && path
                .extension()
                .is_some_and(|extension| extension == "lock")
        })
        .expect("external namespace lock");
    std::fs::remove_file(output.join("reducer.lock")).unwrap();
    std::fs::rename(&lock_path, temp.path().join("displaced.lock")).unwrap();
    std::fs::write(&lock_path, b"replacement").unwrap();
    std::fs::hard_link(&lock_path, output.join("reducer.lock")).unwrap();

    assert!(Phase5Reducer::open(config(output)).is_err());
    drop(first);
}

#[test]
fn combined_output_and_namespace_lock_replacement_cannot_admit_second_writer() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let moved = temp.path().join("moved-output");
    let first = Phase5Reducer::open(config(output.clone())).unwrap();
    let lock_path = std::fs::read_dir(temp.path())
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .find(|path| {
            path.file_name().is_some_and(|name| {
                name.to_string_lossy()
                    .starts_with(".old-faithful-pump-reducer-")
            }) && path
                .extension()
                .is_some_and(|extension| extension == "lock")
        })
        .expect("external namespace lock");
    std::fs::rename(&output, moved).unwrap();
    std::fs::rename(&lock_path, temp.path().join("displaced.lock")).unwrap();

    assert!(Phase5Reducer::open(config(output)).is_err());
    drop(first);
}

#[test]
fn external_namespace_lock_inode_replacement_cannot_admit_second_writer() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let first = Phase5Reducer::open(config(output.clone())).unwrap();
    let lock_path = std::fs::read_dir(temp.path())
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .find(|path| {
            path.file_name().is_some_and(|name| {
                name.to_string_lossy()
                    .starts_with(".old-faithful-pump-reducer-")
            }) && path
                .extension()
                .is_some_and(|extension| extension == "lock")
        })
        .expect("external namespace lock");
    let displaced = temp.path().join("displaced.lock");
    std::fs::rename(&lock_path, displaced).unwrap();
    std::fs::write(&lock_path, b"replacement").unwrap();

    assert!(Phase5Reducer::open(config(output)).is_err());
    drop(first);
}

#[test]
fn lock_path_replacement_cannot_create_a_second_writer() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let first = Phase5Reducer::open(config(output.clone())).unwrap();
    let lock_path = output.join("reducer.lock");
    if lock_path.exists() {
        std::fs::remove_file(lock_path).unwrap();
    }

    assert!(Phase5Reducer::open(config(output)).is_err());
    drop(first);
}

#[test]
fn wal_recovery_validates_semantic_output_before_durable_publication() {
    use base64::{Engine as _, engine::general_purpose::STANDARD as BASE64};
    use serde::{Deserialize, Serialize};
    use sha2::Digest as _;

    #[derive(Deserialize, Serialize)]
    #[serde(rename_all = "camelCase")]
    struct MirrorWal {
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

    #[derive(Deserialize, Serialize)]
    #[serde(rename_all = "camelCase")]
    struct MirrorCoverage {
        schema_version: String,
        identity_sha256: String,
        slot: u64,
        output_sha256: String,
        transaction_count: usize,
        possible_leader_skipped_observed: bool,
        research_ready: bool,
    }

    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let slot = START + 68;
    {
        let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
        reducer.set_fault_point(Some(FaultPoint::AfterWalSync));
        assert_eq!(
            reducer
                .observe_block(&block(slot, 0))
                .unwrap_err()
                .to_string(),
            "injected_crash"
        );
    }
    let wal_path = output.join("coverage.wal");
    let ledger_path = output.join("coverage.ndjson");
    let original_ledger = std::fs::read(&ledger_path).unwrap();
    let mut wal: MirrorWal = serde_json::from_slice(&std::fs::read(&wal_path).unwrap()).unwrap();
    let mut output_bytes = BASE64.decode(&wal.output_base64).unwrap();
    assert_eq!(output_bytes.pop(), Some(b'\n'));
    output_bytes.extend_from_slice(b" \n");
    let output_sha = hex::encode(sha2::Sha256::digest(&output_bytes));
    let ledger_bytes = BASE64.decode(&wal.ledger_line_base64).unwrap();
    let mut coverage: MirrorCoverage = serde_json::from_slice(&ledger_bytes).unwrap();
    coverage.output_sha256.clone_from(&output_sha);
    let mut changed_ledger = serde_json::to_vec(&coverage).unwrap();
    changed_ledger.push(b'\n');
    wal.output_base64 = BASE64.encode(&output_bytes);
    wal.output_sha256 = output_sha;
    wal.ledger_line_base64 = BASE64.encode(&changed_ledger);
    wal.ledger_line_sha256 = hex::encode(sha2::Sha256::digest(&changed_ledger));
    let mut changed_wal = serde_json::to_vec(&wal).unwrap();
    changed_wal.push(b'\n');
    std::fs::write(&wal_path, changed_wal).unwrap();

    assert_eq!(
        Phase5Reducer::open(config(output.clone()))
            .unwrap_err()
            .to_string(),
        "output_corrupt:semantic_output"
    );
    assert!(!output.join(format!("slots/{slot}.json")).exists());
    assert!(wal_path.exists());
    assert_eq!(std::fs::read(ledger_path).unwrap(), original_ledger);
}

#[test]
fn missing_authoritative_coverage_is_not_recreated_by_erroring_open() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    {
        let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
        reducer.observe_block(&block(START + 67, 0)).unwrap();
    }
    let ledger_path = output.join("coverage.ndjson");
    std::fs::remove_file(&ledger_path).unwrap();

    assert_eq!(
        Phase5Reducer::open(config(output)).unwrap_err().to_string(),
        "output_corrupt:coverage_state_set"
    );
    assert!(!ledger_path.exists());
}

#[test]
fn rejects_self_consistent_checkpoint_with_invalid_block_metadata() {
    use sha2::Digest as _;

    #[derive(serde::Serialize, serde::Deserialize)]
    #[serde(rename_all = "camelCase")]
    struct MirrorBlock {
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

    #[derive(serde::Serialize, serde::Deserialize)]
    #[serde(rename_all = "camelCase")]
    struct MirrorPendingSlot {
        block: Option<MirrorBlock>,
        possible_leader_skipped: bool,
        transactions: std::collections::BTreeMap<usize, serde_json::Value>,
    }

    #[derive(serde::Serialize, serde::Deserialize)]
    #[serde(rename_all = "camelCase")]
    struct MirrorCheckpoint {
        schema_version: String,
        identity_sha256: String,
        generation: u64,
        pending: std::collections::BTreeMap<u64, MirrorPendingSlot>,
        completed: std::collections::BTreeMap<u64, serde_json::Value>,
        state_sha256: String,
    }

    #[derive(serde::Serialize)]
    #[serde(rename_all = "camelCase")]
    struct MirrorState<'a> {
        identity_sha256: &'a str,
        generation: u64,
        pending: &'a std::collections::BTreeMap<u64, MirrorPendingSlot>,
        completed: &'a std::collections::BTreeMap<u64, serde_json::Value>,
    }

    for field in ["rewardsSha256", "blockhash", "parentBlockhash"] {
        let temp = tempfile::tempdir().unwrap();
        let output = temp.path().join("output");
        let slot = START + 69;
        {
            let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
            assert_eq!(
                reducer.observe_block(&block(slot, 1)).unwrap(),
                ObserveOutcome::Pending
            );
        }
        let checkpoint_path = std::fs::read_dir(output.join("checkpoints"))
            .unwrap()
            .map(|entry| entry.unwrap().path())
            .max()
            .unwrap();
        let mut checkpoint: MirrorCheckpoint =
            serde_json::from_slice(&std::fs::read(&checkpoint_path).unwrap()).unwrap();
        let stored_block = checkpoint
            .pending
            .get_mut(&slot)
            .unwrap()
            .block
            .as_mut()
            .unwrap();
        match field {
            "rewardsSha256" => stored_block.rewards_sha256 = "not-a-sha256".into(),
            "blockhash" => stored_block.blockhash = "not-a-solana-hash".into(),
            "parentBlockhash" => stored_block.parent_blockhash = "not-a-solana-hash".into(),
            _ => unreachable!(),
        }
        let state = MirrorState {
            identity_sha256: &checkpoint.identity_sha256,
            generation: checkpoint.generation,
            pending: &checkpoint.pending,
            completed: &checkpoint.completed,
        };
        let mut hasher = sha2::Sha256::new();
        hasher.update(b"OLD_FAITHFUL_RUST_CHECKPOINT_STATE_1\n");
        hasher.update(serde_json::to_vec(&state).unwrap());
        checkpoint.state_sha256 = hex::encode(hasher.finalize());
        let mut bytes = serde_json::to_vec(&checkpoint).unwrap();
        bytes.push(b'\n');
        std::fs::write(checkpoint_path, bytes).unwrap();

        assert!(
            Phase5Reducer::open(config(output)).is_err(),
            "self-consistent checkpoint field {field} was accepted"
        );
    }
}

#[test]
#[allow(clippy::too_many_lines)]
fn rejects_self_consistent_checkpoint_with_invalid_transaction_message_hash() {
    use serde::{Deserialize, Serialize};
    use sha2::Digest as _;

    #[derive(Deserialize, Serialize)]
    #[serde(rename_all = "camelCase")]
    struct MirrorSource {
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
    struct MirrorStoredTransaction {
        semantic_sha256: String,
        source: MirrorSource,
        observation: Option<serde_json::Value>,
    }

    #[derive(Deserialize, Serialize)]
    #[serde(rename_all = "camelCase")]
    struct MirrorPendingSlot {
        block: Option<serde_json::Value>,
        possible_leader_skipped: bool,
        transactions: std::collections::BTreeMap<usize, MirrorStoredTransaction>,
    }

    #[derive(Deserialize, Serialize)]
    #[serde(rename_all = "camelCase")]
    struct MirrorCheckpoint {
        schema_version: String,
        identity_sha256: String,
        generation: u64,
        pending: std::collections::BTreeMap<u64, MirrorPendingSlot>,
        completed: std::collections::BTreeMap<u64, serde_json::Value>,
        state_sha256: String,
    }

    #[derive(Serialize)]
    #[serde(rename_all = "camelCase")]
    struct CanonicalTransaction<'a> {
        source: &'a MirrorSource,
        observation: Option<&'a serde_json::Value>,
    }

    #[derive(Serialize)]
    #[serde(rename_all = "camelCase")]
    struct MirrorCheckpointState<'a> {
        identity_sha256: &'a str,
        generation: u64,
        pending: &'a std::collections::BTreeMap<u64, MirrorPendingSlot>,
        completed: &'a std::collections::BTreeMap<u64, serde_json::Value>,
    }

    fn domain_hash<T: Serialize>(domain: &str, value: &T) -> String {
        let mut hasher = sha2::Sha256::new();
        hasher.update(domain.as_bytes());
        hasher.update(b"\n");
        hasher.update(serde_json::to_vec(value).unwrap());
        hex::encode(hasher.finalize())
    }

    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let slot = START + 71;
    let signature = Signature::from([77_u8; 64]);
    let transaction = TransactionData {
        slot,
        transaction_slot_index: 0,
        signature,
        message_hash: Default::default(),
        is_vote: true,
        transaction_status_meta: TransactionStatusMeta {
            status: Ok(()),
            fee: 5_000,
            pre_balances: vec![10_000],
            post_balances: vec![5_000],
            inner_instructions: Some(vec![]),
            log_messages: Some(vec![]),
            pre_token_balances: Some(vec![]),
            post_token_balances: Some(vec![]),
            loaded_addresses: LoadedAddresses::default(),
            ..TransactionStatusMeta::default()
        },
        transaction: VersionedTransaction {
            signatures: vec![signature],
            message: VersionedMessage::Legacy(LegacyMessage {
                account_keys: vec![Address::from([77_u8; 32])],
                ..LegacyMessage::default()
            }),
        },
    };
    {
        let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
        reducer.observe_transaction(&transaction).unwrap();
    }
    let checkpoint_path = std::fs::read_dir(output.join("checkpoints"))
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .max()
        .unwrap();
    let mut checkpoint: MirrorCheckpoint =
        serde_json::from_slice(&std::fs::read(&checkpoint_path).unwrap()).unwrap();
    let stored = checkpoint
        .pending
        .get_mut(&slot)
        .unwrap()
        .transactions
        .get_mut(&0)
        .unwrap();
    stored.source.message_hash = "not-a-solana-message-hash".into();
    stored.semantic_sha256 = domain_hash(
        "OLD_FAITHFUL_RUST_TRANSACTION_SEMANTICS_1",
        &CanonicalTransaction {
            source: &stored.source,
            observation: stored.observation.as_ref(),
        },
    );
    checkpoint.state_sha256 = domain_hash(
        "OLD_FAITHFUL_RUST_CHECKPOINT_STATE_1",
        &MirrorCheckpointState {
            identity_sha256: &checkpoint.identity_sha256,
            generation: checkpoint.generation,
            pending: &checkpoint.pending,
            completed: &checkpoint.completed,
        },
    );
    let mut bytes = serde_json::to_vec(&checkpoint).unwrap();
    bytes.push(b'\n');
    std::fs::write(checkpoint_path, bytes).unwrap();

    assert!(Phase5Reducer::open(config(output)).is_err());
}

#[test]
fn rejects_checkpoint_semantic_hash_tampering() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    {
        let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
        reducer
            .observe_transaction(&legacy_tx(START + 70, 0, 70))
            .unwrap();
    }
    let checkpoint = std::fs::read_dir(output.join("checkpoints"))
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .max()
        .unwrap();
    let mut value: serde_json::Value =
        serde_json::from_slice(&std::fs::read(&checkpoint).unwrap()).unwrap();
    value["pending"][(START + 70).to_string()]["transactions"]["0"]["semanticSha256"] =
        serde_json::Value::String("0".repeat(64));
    let mut bytes = serde_json::to_vec(&value).unwrap();
    bytes.push(b'\n');
    std::fs::write(checkpoint, bytes).unwrap();

    assert!(Phase5Reducer::open(config(output)).is_err());
}

#[test]
fn wal_recovery_rejects_mutated_prefix_before_publishing_output() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let first_slot = START + 71;
    let pending_slot = START + 72;
    {
        let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
        reducer.observe_block(&block(first_slot, 0)).unwrap();
        reducer.set_fault_point(Some(FaultPoint::AfterWalSync));
        reducer.observe_block(&block(pending_slot, 0)).unwrap_err();
    }
    let ledger_path = output.join("coverage.ndjson");
    let mut coverage: serde_json::Value =
        serde_json::from_slice(std::fs::read(&ledger_path).unwrap().trim_ascii()).unwrap();
    coverage["outputSha256"] = serde_json::Value::String("0".repeat(64));
    let mut mutated = serde_json::to_vec(&coverage).unwrap();
    mutated.push(b'\n');
    std::fs::write(&ledger_path, mutated).unwrap();

    assert!(Phase5Reducer::open(config(output.clone())).is_err());
    assert!(!output.join(format!("slots/{pending_slot}.json")).exists());
    assert!(output.join("coverage.wal").exists());
}

#[test]
fn failed_checkpoint_does_not_leak_pending_state_in_memory() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
    let blocking_checkpoint = output.join("checkpoints/00000000000000000002.json");
    std::fs::write(&blocking_checkpoint, b"occupied\n").unwrap();
    let transaction = legacy_tx(START + 73, 0, 73);

    assert!(reducer.observe_transaction(&transaction).is_err());
    std::fs::remove_file(blocking_checkpoint).unwrap();
    assert_eq!(
        reducer.observe_transaction(&transaction).unwrap(),
        ObserveOutcome::Pending
    );
}

#[test]
fn skip_and_block_permutations_produce_identical_coverage_bytes() {
    let temp = tempfile::tempdir().unwrap();
    let slot = START + 74;
    let skip = BlockData::PossibleLeaderSkipped { slot };
    let first = temp.path().join("skip-first");
    let second = temp.path().join("block-first");
    {
        let mut reducer = Phase5Reducer::open(config(first.clone())).unwrap();
        reducer.observe_block(&skip).unwrap();
        reducer.observe_block(&block(slot, 0)).unwrap();
    }
    {
        let mut reducer = Phase5Reducer::open(config(second.clone())).unwrap();
        reducer.observe_block(&block(slot, 0)).unwrap();
        reducer.observe_block(&skip).unwrap();
    }
    assert_eq!(
        std::fs::read(first.join("coverage.ndjson")).unwrap(),
        std::fs::read(second.join("coverage.ndjson")).unwrap()
    );
}

#[test]
fn accepts_three_valid_checkpoints_as_a_crash_safe_rotation_window() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    drop(Phase5Reducer::open(config(output.clone())).unwrap());
    let first = std::fs::read_dir(output.join("checkpoints"))
        .unwrap()
        .next()
        .unwrap()
        .unwrap()
        .path();
    let value: serde_json::Value = serde_json::from_slice(&std::fs::read(first).unwrap()).unwrap();
    for generation in [2_u64, 3] {
        std::fs::write(
            output.join(format!("checkpoints/{generation:020}.json")),
            checkpoint_with_generation(&value, generation),
        )
        .unwrap();
    }

    drop(Phase5Reducer::open(config(output)).unwrap());
}

#[test]
fn rejects_more_than_three_checkpoint_files_before_loading_them() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    drop(Phase5Reducer::open(config(output.clone())).unwrap());
    let first = std::fs::read_dir(output.join("checkpoints"))
        .unwrap()
        .next()
        .unwrap()
        .unwrap()
        .path();
    let value: serde_json::Value = serde_json::from_slice(&std::fs::read(first).unwrap()).unwrap();
    for generation in [2_u64, 3, 4] {
        std::fs::write(
            output.join(format!("checkpoints/{generation:020}.json")),
            checkpoint_with_generation(&value, generation),
        )
        .unwrap();
    }

    assert!(Phase5Reducer::open(config(output)).is_err());
}

#[test]
fn output_directory_rename_cannot_admit_a_second_writer() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let moved = temp.path().join("moved-output");
    let first = Phase5Reducer::open(config(output.clone())).unwrap();
    std::fs::rename(&output, &moved).unwrap();

    assert!(Phase5Reducer::open(config(output)).is_err());
    drop(first);
}

#[test]
fn rejects_limits_that_disable_hard_resource_bounds() {
    let temp = tempfile::tempdir().unwrap();
    let mut candidate = config(temp.path().join("output"));
    candidate.limits = ReducerLimits {
        max_pending_slots: usize::MAX,
        max_transactions_per_slot: usize::MAX,
        max_output_bytes: u64::MAX,
        max_checkpoint_bytes: u64::MAX,
        max_runtime_seconds: u64::MAX,
    };

    assert!(Phase5Reducer::open(candidate).is_err());
}

#[test]
fn noncontiguous_join_error_does_not_checkpoint_the_rejected_block() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let slot = START + 75;
    {
        let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
        reducer
            .observe_transaction(&legacy_tx(slot, 1, 75))
            .unwrap();
        assert_eq!(
            reducer
                .observe_block(&block(slot, 1))
                .unwrap_err()
                .to_string(),
            "non_contiguous_transaction_indices"
        );
    }
    let mut reducer = Phase5Reducer::open(config(output)).unwrap();
    reducer
        .observe_transaction(&legacy_tx(slot, 0, 76))
        .unwrap();
    assert_eq!(
        reducer.observe_block(&block(slot, 2)).unwrap(),
        ObserveOutcome::Committed { slot }
    );
}

#[test]
fn vote_retries_compare_status_metadata_not_only_identity() {
    let temp = tempfile::tempdir().unwrap();
    let mut reducer = Phase5Reducer::open(config(temp.path().join("output"))).unwrap();
    let mut vote = legacy_tx(START + 76, 0, 77);
    vote.is_vote = true;
    reducer.observe_transaction(&vote).unwrap();
    let mut conflict = vote.clone();
    conflict.transaction_status_meta.fee += 1;

    assert_eq!(
        reducer
            .observe_transaction(&conflict)
            .unwrap_err()
            .to_string(),
        "conflicting_transaction_retry"
    );
}

#[test]
fn block_retries_compare_rewards_semantics() {
    let temp = tempfile::tempdir().unwrap();
    let slot = START + 77;
    let mut reducer = Phase5Reducer::open(config(temp.path().join("output"))).unwrap();
    let original = block(slot, 1);
    reducer.observe_block(&original).unwrap();
    let conflict = BlockData::Block {
        parent_slot: slot - 1,
        parent_blockhash: Default::default(),
        slot,
        blockhash: Default::default(),
        rewards: KeyedRewardsAndNumPartitions {
            keyed_rewards: vec![(
                Address::from([88; 32]),
                RewardInfo {
                    reward_type: RewardType::Fee,
                    lamports: 1,
                    post_balance: 2,
                    commission: None,
                },
            )],
            num_partitions: Some(1),
        },
        block_time: Some(1_700_000_000),
        block_height: Some(200_000_000),
        executed_transaction_count: 1,
        entry_count: 1,
    };

    assert_eq!(
        reducer.observe_block(&conflict).unwrap_err().to_string(),
        "conflicting_block_retry"
    );
}

#[test]
fn coordinated_output_and_coverage_rewrite_is_rejected() {
    use sha2::Digest as _;

    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let slot = START + 78;
    {
        let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
        reducer.observe_block(&block(slot, 0)).unwrap();
    }
    let slot_path = output.join(format!("slots/{slot}.json"));
    let mut value: serde_json::Value =
        serde_json::from_slice(&std::fs::read(&slot_path).unwrap()).unwrap();
    value["sourceManifestSha256"] = serde_json::Value::String("0".repeat(64));
    let mut slot_bytes = serde_json::to_vec(&value).unwrap();
    slot_bytes.push(b'\n');
    std::fs::write(&slot_path, &slot_bytes).unwrap();
    let digest = hex::encode(sha2::Sha256::digest(&slot_bytes));
    let ledger_path = output.join("coverage.ndjson");
    let mut coverage: serde_json::Value =
        serde_json::from_slice(std::fs::read(&ledger_path).unwrap().trim_ascii()).unwrap();
    coverage["outputSha256"] = serde_json::Value::String(digest);
    let mut ledger_bytes = serde_json::to_vec(&coverage).unwrap();
    ledger_bytes.push(b'\n');
    std::fs::write(ledger_path, ledger_bytes).unwrap();

    assert!(Phase5Reducer::open(config(output)).is_err());
}

#[test]
fn wal_recovery_validates_all_prefix_outputs_before_mutating() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let first_slot = START + 79;
    let wal_slot = START + 80;
    {
        let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
        reducer.observe_block(&block(first_slot, 0)).unwrap();
        reducer.set_fault_point(Some(FaultPoint::AfterWalSync));
        reducer.observe_block(&block(wal_slot, 0)).unwrap_err();
    }
    std::fs::write(output.join(format!("slots/{first_slot}.json")), b"{}\n").unwrap();

    assert!(Phase5Reducer::open(config(output.clone())).is_err());
    assert!(!output.join(format!("slots/{wal_slot}.json")).exists());
    assert!(output.join("coverage.wal").exists());
}

#[test]
fn post_publication_checkpoint_failure_never_returns_an_error() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let slot = START + 81;
    let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
    std::fs::write(
        output.join("checkpoints/00000000000000000003.json"),
        b"occupied\n",
    )
    .unwrap();
    let outcome = reducer.observe_block(&block(slot, 0));

    assert!(outcome.is_ok() || !output.join(format!("slots/{slot}.json")).exists());
}

#[test]
fn uncovered_slot_artifacts_are_rejected_on_startup() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    drop(Phase5Reducer::open(config(output.clone())).unwrap());
    std::fs::write(output.join("slots/999.json"), b"{}\n").unwrap();
    assert!(Phase5Reducer::open(config(output)).is_err());
}

#[test]
fn wal_claim_recovery_is_crash_safe_and_multiple_owners_fail_closed() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("claim-recovery");
    let slot = START + 88;
    {
        let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
        reducer.set_fault_point(Some(FaultPoint::AfterLedgerSync));
        assert!(reducer.observe_block(&block(slot, 0)).is_err());
    }
    std::fs::rename(
        output.join("coverage.wal"),
        output.join("coverage.wal.claim"),
    )
    .unwrap();
    drop(Phase5Reducer::open(config(output.clone())).unwrap());
    assert!(!output.join("coverage.wal").exists());
    assert!(!output.join("coverage.wal.claim").exists());
    assert!(output.join(format!("slots/{slot}.json")).exists());

    let output = temp.path().join("multiple-owners");
    {
        let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
        reducer.set_fault_point(Some(FaultPoint::AfterWalSync));
        assert!(reducer.observe_block(&block(slot + 1, 0)).is_err());
    }
    std::fs::copy(
        output.join("coverage.wal"),
        output.join("coverage.wal.claim"),
    )
    .unwrap();
    assert!(Phase5Reducer::open(config(output)).is_err());
}

#[test]
fn wal_recovery_rejects_a_tighter_output_budget_before_any_mutation() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let slot = START + 89;
    {
        let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
        reducer.set_fault_point(Some(FaultPoint::AfterWalSync));
        assert_eq!(
            reducer
                .observe_block(&block(slot, 0))
                .unwrap_err()
                .to_string(),
            "injected_crash"
        );
    }
    let wal_path = output.join("coverage.wal");
    let ledger_path = output.join("coverage.ndjson");
    let wal_before = std::fs::read(&wal_path).unwrap();
    let ledger_before = std::fs::read(&ledger_path).unwrap();
    let slot_path = output.join(format!("slots/{slot}.json"));
    assert!(!slot_path.exists());
    let mut constrained = config(output.clone());
    constrained.limits.max_output_bytes = 1;
    assert!(Phase5Reducer::open(constrained).is_err());
    assert!(!slot_path.exists(), "recovery published over-budget output");
    assert_eq!(std::fs::read(&ledger_path).unwrap(), ledger_before);
    assert_eq!(std::fs::read(&wal_path).unwrap(), wal_before);
}

#[test]
fn returned_post_wal_error_does_not_turn_exact_retry_into_false_duplicate() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let slot = START + 90;
    let callback = block(slot, 0);
    let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
    reducer.set_fault_point(Some(FaultPoint::AfterWalSync));
    assert_eq!(
        reducer.observe_block(&callback).unwrap_err().to_string(),
        "injected_crash"
    );
    assert!(output.join("coverage.wal").exists());
    assert!(!output.join(format!("slots/{slot}.json")).exists());
    let retry = reducer.observe_block(&callback).unwrap();
    assert_eq!(retry, ObserveOutcome::Committed { slot });
    assert!(output.join(format!("slots/{slot}.json")).exists());
    assert!(!output.join("coverage.wal").exists());
}

#[test]
fn recovery_rejects_a_wal_for_an_already_covered_slot_before_mutation() {
    use base64::{Engine as _, engine::general_purpose::STANDARD as BASE64};
    use serde::Serialize;
    use sha2::Digest as _;
    #[derive(Serialize)]
    #[serde(rename_all = "camelCase")]
    struct MirrorWal {
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
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let slot = START + 91;
    {
        let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
        assert_eq!(
            reducer.observe_block(&block(slot, 0)).unwrap(),
            ObserveOutcome::Committed { slot }
        );
    }
    let ledger_path = output.join("coverage.ndjson");
    let wal_path = output.join("coverage.wal");
    let ledger_before = std::fs::read(&ledger_path).unwrap();
    let slot_bytes = std::fs::read(output.join(format!("slots/{slot}.json"))).unwrap();
    let coverage: serde_json::Value = serde_json::from_slice(&ledger_before).unwrap();
    let digest = |bytes: &[u8]| hex::encode(sha2::Sha256::digest(bytes));
    let wal = MirrorWal {
        schema_version: "OLD_FAITHFUL_RUST_REDUCER_WAL_1".into(),
        identity_sha256: coverage["identitySha256"].as_str().unwrap().into(),
        slot,
        output_base64: BASE64.encode(&slot_bytes),
        output_sha256: digest(&slot_bytes),
        ledger_offset: u64::try_from(ledger_before.len()).unwrap(),
        ledger_prefix_sha256: digest(&ledger_before),
        ledger_line_base64: BASE64.encode(&ledger_before),
        ledger_line_sha256: digest(&ledger_before),
    };
    let mut wal_bytes = serde_json::to_vec(&wal).unwrap();
    wal_bytes.push(b'\n');
    std::fs::write(&wal_path, &wal_bytes).unwrap();
    assert!(Phase5Reducer::open(config(output.clone())).is_err());
    assert_eq!(std::fs::read(&ledger_path).unwrap(), ledger_before);
    assert_eq!(std::fs::read(&wal_path).unwrap(), wal_bytes);
}

#[test]
fn active_writer_revalidates_exact_artifact_set_before_committing() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let slot = START + 92;
    let mut reducer = Phase5Reducer::open(config(output.clone())).unwrap();
    std::fs::write(output.join("slots/999.json"), b"").unwrap();
    let coverage_before = std::fs::read(output.join("coverage.ndjson")).unwrap();
    assert!(reducer.observe_block(&block(slot, 0)).is_err());
    assert!(!output.join(format!("slots/{slot}.json")).exists());
    assert_eq!(
        std::fs::read(output.join("coverage.ndjson")).unwrap(),
        coverage_before
    );
}

#[test]
fn slot_artifact_entry_count_is_bounded_independent_of_payload_bytes() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let mut candidate = config(output.clone());
    candidate.source_manifest.slots_file_entry_count = 4;
    drop(Phase5Reducer::open(candidate.clone()).unwrap());
    for index in 0_u64..5 {
        std::fs::write(output.join(format!("slots/{}.json", START + index)), b"").unwrap();
    }
    assert_eq!(
        Phase5Reducer::open(candidate).unwrap_err().to_string(),
        "output_corrupt:slot_artifact_limit"
    );
}
