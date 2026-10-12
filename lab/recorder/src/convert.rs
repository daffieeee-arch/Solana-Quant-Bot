//! Yellowstone transaction updates as the firehose's `TransactionData`, so the extractor's own
//! `LabPlugin::rows_for` decodes live transactions exactly as it decodes Old Faithful's.
//!
//! The proto carries the same fields as the ledger (message, signatures, status meta with inner
//! instructions, loaded addresses and compute units). Fields the extractor does not read (token
//! balances, logs, rewards, return data) are left empty.

use anyhow::{anyhow, bail, Context, Result};
use jetstreamer_firehose::firehose::TransactionData;
use solana_message::compiled_instruction::CompiledInstruction;
use solana_message::v0::{LoadedAddresses, MessageAddressTableLookup};
use solana_message::{legacy, v0, v1, Address, Hash, MessageHeader, VersionedMessage};
use solana_transaction::versioned::VersionedTransaction;
use solana_transaction::Signature;
use solana_transaction_status::{InnerInstruction, InnerInstructions, TransactionStatusMeta};
use yellowstone_grpc_proto::prelude::{
    CompiledInstruction as PCompiledInstruction, Message as PMessage, SubscribeUpdateTransactionInfo,
    TransactionStatusMeta as PMeta,
};

fn address(b: &[u8]) -> Result<Address> {
    let a: [u8; 32] = b.try_into().map_err(|_| anyhow!("address of {} bytes", b.len()))?;
    Ok(Address::from(a))
}

fn signature(b: &[u8]) -> Result<Signature> {
    let s: [u8; 64] = b.try_into().map_err(|_| anyhow!("signature of {} bytes", b.len()))?;
    Ok(Signature::from(s))
}

fn byte(v: u32, what: &str) -> Result<u8> {
    u8::try_from(v).with_context(|| format!("{what} {v} does not fit in u8"))
}

fn instruction(ix: &PCompiledInstruction) -> Result<CompiledInstruction> {
    Ok(CompiledInstruction {
        program_id_index: byte(ix.program_id_index, "program_id_index")?,
        accounts: ix.accounts.clone(),
        data: ix.data.clone(),
    })
}

fn message(m: &PMessage) -> Result<VersionedMessage> {
    let h = m.header.as_ref().context("message without header")?;
    let header = MessageHeader {
        num_required_signatures: byte(h.num_required_signatures, "num_required_signatures")?,
        num_readonly_signed_accounts: byte(h.num_readonly_signed_accounts, "num_readonly_signed_accounts")?,
        num_readonly_unsigned_accounts: byte(h.num_readonly_unsigned_accounts, "num_readonly_unsigned_accounts")?,
    };
    let account_keys = m.account_keys.iter().map(|k| address(k)).collect::<Result<Vec<_>>>()?;
    let blockhash: [u8; 32] =
        m.recent_blockhash.as_slice().try_into().map_err(|_| anyhow!("blockhash of {} bytes", m.recent_blockhash.len()))?;
    let blockhash = Hash::new_from_array(blockhash);
    let instructions = m.instructions.iter().map(instruction).collect::<Result<Vec<_>>>()?;
    // V1 messages (SIMD-0385) carry their compute budget in `config` and have no lookup tables.
    if let Some(c) = &m.config {
        if !m.address_table_lookups.is_empty() {
            bail!("v1 message with address table lookups");
        }
        return Ok(VersionedMessage::V1(v1::Message {
            header,
            config: v1::TransactionConfig {
                priority_fee: c.priority_fee,
                compute_unit_limit: c.compute_unit_limit,
                loaded_accounts_data_size_limit: c.loaded_accounts_data_size_limit,
                heap_size: c.heap_size,
            },
            lifetime_specifier: blockhash,
            account_keys,
            instructions,
        }));
    }
    if !m.versioned {
        return Ok(VersionedMessage::Legacy(legacy::Message { header, account_keys, recent_blockhash: blockhash, instructions }));
    }
    let address_table_lookups = m
        .address_table_lookups
        .iter()
        .map(|l| {
            Ok(MessageAddressTableLookup {
                account_key: address(&l.account_key)?,
                writable_indexes: l.writable_indexes.clone(),
                readonly_indexes: l.readonly_indexes.clone(),
            })
        })
        .collect::<Result<Vec<_>>>()?;
    Ok(VersionedMessage::V0(v0::Message { header, account_keys, recent_blockhash: blockhash, instructions, address_table_lookups }))
}

fn meta(m: &PMeta) -> Result<TransactionStatusMeta> {
    let inner = if m.inner_instructions_none {
        None
    } else {
        Some(
            m.inner_instructions
                .iter()
                .map(|g| {
                    Ok(InnerInstructions {
                        index: byte(g.index, "inner instruction group index")?,
                        instructions: g
                            .instructions
                            .iter()
                            .map(|ix| {
                                Ok(InnerInstruction {
                                    instruction: CompiledInstruction {
                                        program_id_index: byte(ix.program_id_index, "program_id_index")?,
                                        accounts: ix.accounts.clone(),
                                        data: ix.data.clone(),
                                    },
                                    stack_height: ix.stack_height,
                                })
                            })
                            .collect::<Result<Vec<_>>>()?,
                    })
                })
                .collect::<Result<Vec<_>>>()?,
        )
    };
    Ok(TransactionStatusMeta {
        status: Ok(()),
        fee: m.fee,
        pre_balances: m.pre_balances.clone(),
        post_balances: m.post_balances.clone(),
        inner_instructions: inner,
        loaded_addresses: LoadedAddresses {
            writable: m.loaded_writable_addresses.iter().map(|k| address(k)).collect::<Result<Vec<_>>>()?,
            readonly: m.loaded_readonly_addresses.iter().map(|k| address(k)).collect::<Result<Vec<_>>>()?,
        },
        compute_units_consumed: m.compute_units_consumed,
        cost_units: m.cost_units,
        ..Default::default()
    })
}

/// What became of one transaction update.
pub enum Converted {
    Tx(Box<TransactionData>),
    /// A failed transaction (the filter asks for successful ones only): not written.
    Failed,
}

/// The firehose's view of a Yellowstone transaction update of `slot`.
pub fn transaction(slot: u64, info: &SubscribeUpdateTransactionInfo) -> Result<Converted> {
    let tx = info.transaction.as_ref().context("update without transaction")?;
    let m = info.meta.as_ref().context("update without status meta")?;
    if m.err.as_ref().is_some_and(|e| !e.err.is_empty()) {
        return Ok(Converted::Failed);
    }
    let msg = message(tx.message.as_ref().context("transaction without message")?)?;
    let signatures = tx.signatures.iter().map(|s| signature(s)).collect::<Result<Vec<_>>>()?;
    Ok(Converted::Tx(Box::new(TransactionData {
        slot,
        transaction_slot_index: usize::try_from(info.index)?,
        signature: signature(&info.signature)?,
        message_hash: Default::default(),
        is_vote: info.is_vote,
        transaction_status_meta: meta(m)?,
        transaction: VersionedTransaction { signatures, message: msg },
    })))
}

#[cfg(test)]
pub mod tests {
    use super::*;
    use crate::extract::LabPlugin;
    use crate::idl::{Kind, Val, EVENT_IX_TAG};
    use yellowstone_grpc_proto::prelude::{
        InnerInstruction as PInner, InnerInstructions as PInnerGroup, MessageHeader as PHeader, Transaction as PTx,
        TransactionConfig as PConfig, TransactionError as PErr,
    };

    pub const PUMP_AMM: &str = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA";
    const COMPUTE_BUDGET: &str = "ComputeBudget111111111111111111111111111111";

    fn key(b58: &str) -> Vec<u8> {
        bs58::decode(b58).into_vec().unwrap()
    }

    pub fn fake_key(n: u8) -> Vec<u8> {
        let mut k = vec![7u8; 32];
        k[0] = n;
        k
    }

    /// A v0 transaction: fee payer, a pool (static), ComputeBudget, and PumpSwap loaded through a
    /// lookup table, one outer PumpSwap instruction and below it an event instruction (stack
    /// height 2) whose discriminator no IDL event has, so the extractor writes an anomaly row.
    pub fn sample(_slot: u64, index: u64, versioned: bool, v1: bool) -> SubscribeUpdateTransactionInfo {
        let mut event = EVENT_IX_TAG.to_vec();
        event.extend([0xAB; 8]);
        event.extend([1, 2, 3]);
        let mut cu_price = vec![3u8];
        cu_price.extend(25_000u64.to_le_bytes());
        let mut keys = vec![fake_key(1), fake_key(2), key(COMPUTE_BUDGET)];
        let mut loaded = Vec::new();
        if versioned && !v1 {
            loaded.push(key(PUMP_AMM));
        } else {
            keys.push(key(PUMP_AMM));
        }
        let mut instructions = vec![PCompiledInstruction { program_id_index: 3, accounts: vec![1, 0], data: vec![0x66, 0x06, 0x3d, 0x12, 0, 0, 0, 0] }];
        if !v1 {
            instructions.insert(0, PCompiledInstruction { program_id_index: 2, accounts: vec![], data: cu_price });
        }
        let outer = if v1 { 0 } else { 1 };
        SubscribeUpdateTransactionInfo {
            signature: vec![9u8; 64],
            is_vote: false,
            transaction: Some(PTx {
                signatures: vec![vec![9u8; 64]],
                message: Some(PMessage {
                    header: Some(PHeader { num_required_signatures: 1, num_readonly_signed_accounts: 0, num_readonly_unsigned_accounts: 1 }),
                    account_keys: keys,
                    recent_blockhash: vec![5u8; 32],
                    instructions,
                    versioned: versioned || v1,
                    address_table_lookups: if versioned && !v1 {
                        vec![yellowstone_grpc_proto::prelude::MessageAddressTableLookup {
                            account_key: fake_key(9),
                            writable_indexes: vec![],
                            readonly_indexes: vec![0],
                        }]
                    } else {
                        vec![]
                    },
                    config: v1.then(|| PConfig { priority_fee: Some(50_000), compute_unit_limit: Some(200_000), ..Default::default() }),
                }),
            }),
            meta: Some(PMeta {
                fee: 5_000 + 5_000,
                inner_instructions: vec![PInnerGroup {
                    index: outer,
                    instructions: vec![PInner { program_id_index: 3, accounts: vec![], data: event, stack_height: Some(2) }],
                }],
                loaded_readonly_addresses: loaded,
                compute_units_consumed: Some(42_000),
                ..Default::default()
            }),
            index,
        }
    }

    pub fn plugin() -> LabPlugin {
        use crate::extract::Coverage;
        use crate::sink::Sink;
        use std::sync::Arc;
        let dir = std::env::temp_dir().join(format!("lab-recorder-test-sink-{}", std::process::id()));
        LabPlugin::new(Arc::new(Sink::start(dir)), Arc::new(Coverage::new(0, 0))).unwrap()
    }

    fn rows(info: &SubscribeUpdateTransactionInfo) -> Vec<crate::sink::Row> {
        match transaction(1000, info).unwrap() {
            Converted::Tx(tx) => plugin().rows_for(&tx),
            Converted::Failed => panic!("not failed"),
        }
    }

    fn col<'a>(r: &'a crate::sink::Row, name: &str) -> &'a Val {
        let i = r.columns.iter().position(|(n, _)| n == name).unwrap();
        &r.values[i]
    }

    #[test]
    fn legacy_v0_and_v1_messages_decode_like_the_ledger() {
        for (versioned, v1, version) in [(false, false, "legacy"), (true, false, "v0"), (false, true, "v1")] {
            let info = sample(1000, 17, versioned, v1);
            let out = rows(&info);
            assert_eq!(out.len(), 1, "{version}");
            let r = &out[0];
            assert_eq!(&*r.table, "anomalies");
            assert!(matches!(col(r, "slot"), Val::U64(1000)));
            assert!(matches!(col(r, "tx_index"), Val::U64(17)));
            assert!(matches!(col(r, "kind"), Val::Str(k) if k == "unknown_discriminator"), "{version}");
            assert!(matches!(col(r, "program"), Val::Str(p) if p == "pump_amm"));
            assert!(r.columns.iter().all(|(_, k)| matches!(k, Kind::U64 | Kind::Utf8 | Kind::Bool | Kind::I64)));
        }
    }

    #[test]
    fn message_fields_survive_the_conversion() {
        let Converted::Tx(tx) = transaction(1000, &sample(1000, 3, true, false)).unwrap() else { panic!() };
        let m = &tx.transaction.message;
        assert!(matches!(m, VersionedMessage::V0(_)));
        assert_eq!(m.static_account_keys().len(), 3);
        assert_eq!(tx.transaction_status_meta.loaded_addresses.readonly.len(), 1);
        assert_eq!(m.header().num_required_signatures, 1);
        assert_eq!(m.instructions()[0].data[0], 3);
        let inner = tx.transaction_status_meta.inner_instructions.as_ref().unwrap();
        assert_eq!((inner[0].index, inner[0].instructions[0].stack_height), (1, Some(2)));
        assert_eq!(tx.transaction_status_meta.compute_units_consumed, Some(42_000));
        assert_eq!(tx.transaction_slot_index, 3);
        let Converted::Tx(tx) = transaction(1000, &sample(1000, 3, false, true)).unwrap() else { panic!() };
        match &tx.transaction.message {
            VersionedMessage::V1(m) => assert_eq!((m.config.priority_fee, m.config.compute_unit_limit), (Some(50_000), Some(200_000))),
            _ => panic!("v1 expected"),
        }
    }

    #[test]
    fn failed_transactions_are_not_converted() {
        let mut info = sample(1000, 0, false, false);
        info.meta.as_mut().unwrap().err = Some(PErr { err: vec![1, 2] });
        assert!(matches!(transaction(1000, &info).unwrap(), Converted::Failed));
    }

    #[test]
    fn missing_inner_instructions_stay_missing() {
        let mut info = sample(1000, 0, false, false);
        info.meta.as_mut().unwrap().inner_instructions_none = true;
        let out = rows(&info);
        assert!(out.iter().any(|r| matches!(col(r, "kind"), Val::Str(k) if k == "missing_inner_instructions")));
    }
}
