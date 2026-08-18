#![allow(clippy::default_trait_access, clippy::too_many_lines)]

use old_faithful_pump_reducer::{
    AdapterProvenance, ObserveOutcome, OldFaithfulSourceManifest, Phase5Reducer, ReducerConfig,
    ReducerLimits, ReducerProvenance, SlotRange,
};

fn source_manifest() -> OldFaithfulSourceManifest {
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
            start_inclusive: 432_000_000,
            end_exclusive: 432_432_000,
        },
    }
}

fn valid_config(output_dir: std::path::PathBuf) -> ReducerConfig {
    ReducerConfig {
        output_dir,
        source_manifest: source_manifest(),
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

#[test]
fn rejects_wrong_jetstreamer_pin_before_creating_output() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let mut config = valid_config(output.clone());
    config.reducer_provenance.jetstreamer_git_sha =
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".into();

    let error = Phase5Reducer::open(config).unwrap_err();

    assert_eq!(error.to_string(), "jetstreamer_revision_mismatch");
    assert!(!output.exists());
}

#[test]
fn joins_block_before_legacy_transaction_and_rederives_pump_candidate() {
    use std::str::FromStr;

    use jetstreamer_firehose::firehose::KeyedRewardsAndNumPartitions;
    use jetstreamer_firehose::firehose::{BlockData, TransactionData};
    use solana_address::Address;
    use solana_message::{
        VersionedMessage, compiled_instruction::CompiledInstruction,
        legacy::Message as LegacyMessage,
    };
    use solana_signature::Signature;
    use solana_transaction::versioned::VersionedTransaction;
    use solana_transaction_status::TransactionStatusMeta;

    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let mut reducer = Phase5Reducer::open(valid_config(output.clone())).unwrap();
    let slot = 432_000_001;
    let pump = Address::from_str("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P").unwrap();
    let payer = Address::from([1_u8; 32]);
    let mint = Address::from([2_u8; 32]);
    let (curve, _) = Address::find_program_address(&[b"bonding-curve", mint.as_ref()], &pump);
    let message = LegacyMessage {
        account_keys: vec![payer, pump, mint, curve],
        instructions: vec![CompiledInstruction {
            program_id_index: 1,
            accounts: vec![2, 3],
            data: vec![0x66, 0x06, 0x3d, 0x12, 0x01, 0xda, 0xeb, 0xea],
        }],
        ..LegacyMessage::default()
    };
    let transaction = TransactionData {
        slot,
        transaction_slot_index: 0,
        signature: Signature::from([7_u8; 64]),
        message_hash: Default::default(),
        is_vote: false,
        transaction_status_meta: TransactionStatusMeta {
            status: Ok(()),
            fee: 5_000,
            pre_balances: vec![10_000, 0, 0, 500],
            post_balances: vec![4_500, 0, 0, 6_000],
            log_messages: Some(vec!["Program log: Instruction: Buy".into()]),
            inner_instructions: Some(vec![]),
            pre_token_balances: Some(vec![]),
            post_token_balances: Some(vec![]),
            ..TransactionStatusMeta::default()
        },
        transaction: VersionedTransaction {
            signatures: vec![Signature::from([7_u8; 64])],
            message: VersionedMessage::Legacy(message),
        },
    };
    let block = BlockData::Block {
        parent_slot: slot - 1,
        parent_blockhash: Default::default(),
        slot,
        blockhash: Default::default(),
        rewards: KeyedRewardsAndNumPartitions {
            keyed_rewards: vec![],
            num_partitions: None,
        },
        block_time: Some(1_700_000_000),
        block_height: Some(200_000_000),
        executed_transaction_count: 1,
        entry_count: 1,
    };

    assert_eq!(
        reducer.observe_block(&block).unwrap(),
        ObserveOutcome::Pending
    );
    assert_eq!(
        reducer.observe_transaction(&transaction).unwrap(),
        ObserveOutcome::Committed { slot }
    );

    let bytes = std::fs::read(output.join("slots/432000001.json")).unwrap();
    let value: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(value["schemaVersion"], "OLD_FAITHFUL_PUMP_V2_SLOT_1");
    assert_eq!(value["researchReady"], false);
    assert_eq!(value["blockTime"], "2023-11-14T22:13:20.000Z");
    assert_eq!(value["observations"].as_array().unwrap().len(), 1);
    assert_eq!(
        value["observations"][0]["bronze"]["accountKeys"],
        serde_json::json!([
            payer.to_string(),
            pump.to_string(),
            mint.to_string(),
            curve.to_string()
        ])
    );
    assert_eq!(
        value["observations"][0]["bronze"]["pumpCandidates"][0]["mint"],
        mint.to_string()
    );
    assert_eq!(
        value["observations"][0]["bronze"]["pumpCandidates"][0]["curve"],
        curve.to_string()
    );
    assert_eq!(
        value["observations"][0]["bronze"]["pumpCandidates"][0]["variant"],
        "buy"
    );
}

#[test]
fn buffers_failed_v0_transaction_until_block_and_preserves_loaded_inner_raw_fields() {
    use std::str::FromStr;

    use jetstreamer_firehose::firehose::KeyedRewardsAndNumPartitions;
    use jetstreamer_firehose::firehose::{BlockData, TransactionData};
    use solana_account_decoder_client_types::token::UiTokenAmount;
    use solana_address::Address;
    use solana_message::{
        VersionedMessage,
        compiled_instruction::CompiledInstruction,
        v0::{LoadedAddresses, Message as V0Message, MessageAddressTableLookup},
    };
    use solana_signature::Signature;
    use solana_transaction::versioned::VersionedTransaction;
    use solana_transaction_error::TransactionError;
    use solana_transaction_status::{
        InnerInstruction, InnerInstructions, TransactionStatusMeta, TransactionTokenBalance,
    };

    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("output");
    let mut reducer = Phase5Reducer::open(valid_config(output.clone())).unwrap();
    let slot = 432_000_002;
    let payer = Address::from([11_u8; 32]);
    let pump = Address::from_str("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P").unwrap();
    let mint = Address::from([12_u8; 32]);
    let (curve, _) = Address::find_program_address(&[b"bonding-curve", mint.as_ref()], &pump);
    let token_program = Address::from([13_u8; 32]);
    let signature = Signature::from([14_u8; 64]);
    let transaction = TransactionData {
        slot,
        transaction_slot_index: 0,
        signature,
        message_hash: Default::default(),
        is_vote: false,
        transaction_status_meta: TransactionStatusMeta {
            status: Err(TransactionError::AccountNotFound),
            fee: u64::MAX,
            pre_balances: vec![u64::MAX, 2, 3, 4],
            post_balances: vec![u64::MAX - 1, 2, 3, 5],
            inner_instructions: Some(vec![InnerInstructions {
                index: 0,
                instructions: vec![InnerInstruction {
                    instruction: CompiledInstruction {
                        program_id_index: 1,
                        accounts: vec![2, 3],
                        data: vec![0x33, 0xe6, 0x85, 0xa4, 0x01, 0x7f, 0x83, 0xad],
                    },
                    stack_height: Some(2),
                }],
            }]),
            log_messages: Some(vec!["Program log: Instruction: Sell".into()]),
            pre_token_balances: Some(vec![TransactionTokenBalance {
                account_index: 2,
                mint: mint.to_string(),
                ui_token_amount: UiTokenAmount {
                    ui_amount: None,
                    decimals: 9,
                    amount: u64::MAX.to_string(),
                    ui_amount_string: "18446744073.709551615".into(),
                },
                owner: payer.to_string(),
                program_id: token_program.to_string(),
            }]),
            post_token_balances: Some(vec![TransactionTokenBalance {
                account_index: 2,
                mint: mint.to_string(),
                ui_token_amount: UiTokenAmount {
                    ui_amount: None,
                    decimals: 9,
                    amount: "1".into(),
                    ui_amount_string: "0.000000001".into(),
                },
                owner: payer.to_string(),
                program_id: token_program.to_string(),
            }]),
            loaded_addresses: LoadedAddresses {
                writable: vec![mint],
                readonly: vec![curve],
            },
            ..TransactionStatusMeta::default()
        },
        transaction: VersionedTransaction {
            signatures: vec![signature],
            message: VersionedMessage::V0(V0Message {
                account_keys: vec![payer, pump],
                address_table_lookups: vec![MessageAddressTableLookup {
                    account_key: Address::from([15_u8; 32]),
                    writable_indexes: vec![0],
                    readonly_indexes: vec![1],
                }],
                instructions: vec![CompiledInstruction {
                    program_id_index: 0,
                    accounts: vec![],
                    data: vec![],
                }],
                ..V0Message::default()
            }),
        },
    };

    assert_eq!(
        reducer.observe_transaction(&transaction).unwrap(),
        ObserveOutcome::Pending
    );
    let block = BlockData::Block {
        parent_slot: slot - 1,
        parent_blockhash: Default::default(),
        slot,
        blockhash: Default::default(),
        rewards: KeyedRewardsAndNumPartitions {
            keyed_rewards: vec![],
            num_partitions: None,
        },
        block_time: Some(1_700_000_001),
        block_height: Some(200_000_001),
        executed_transaction_count: 1,
        entry_count: 1,
    };
    assert_eq!(
        reducer.observe_block(&block).unwrap(),
        ObserveOutcome::Committed { slot }
    );

    let value: serde_json::Value =
        serde_json::from_slice(&std::fs::read(output.join("slots/432000002.json")).unwrap())
            .unwrap();
    let bronze = &value["observations"][0]["bronze"];
    assert_eq!(bronze["executionStatus"], "failed");
    assert_eq!(bronze["feeLamports"], u64::MAX.to_string());
    assert_eq!(bronze["preBalancesLamports"][0], u64::MAX.to_string());
    assert_eq!(bronze["accountKeys"][2], mint.to_string());
    assert_eq!(bronze["accountKeys"][3], curve.to_string());
    assert_eq!(bronze["instructions"][1]["instructionLocation"], "inner");
    assert_eq!(bronze["instructions"][1]["parentInstructionIndex"], 0);
    assert_eq!(bronze["instructions"][1]["stackHeight"], 2);
    assert_eq!(
        bronze["preTokenBalances"][0]["amount"],
        u64::MAX.to_string()
    );
    assert_eq!(bronze["preTokenBalances"][0]["owner"], payer.to_string());
    assert_eq!(
        bronze["preTokenBalances"][0]["programId"],
        token_program.to_string()
    );
    assert_eq!(bronze["pumpCandidates"][0]["variant"], "sell");
    assert_eq!(bronze["pumpCandidates"][0]["executionStatus"], "failed");
}
