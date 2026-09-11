//! Source-bound modern transaction/protobuf lane. No RPC, vote filter, account
//! lookup, status default, Pump wire decoding or signature-verification claim.
use crate::{
    archive::{Envelope, Frame, MAX_FRAME_BYTES, assemble},
    invalid,
    proto::Status,
};
use bincode::Options;
use prost::Message;
use serde_json::{Value, json};
use solana_message::VersionedMessage;
use solana_transaction::versioned::VersionedTransaction;
use solana_transaction_error::TransactionError;
use std::{
    collections::{BTreeMap, BTreeSet},
    hash::Hasher,
    io::{self, Read},
};

pub const PUMP_PROGRAM: &str = pump_protocol_v2::registry::PUMP_PROGRAM_ID;

/// Verify the assembled (still compressed, if applicable) source bytes.
/// # Errors
/// Rejects missing continuations or a checksum mismatch under both source algorithms.
pub fn checked_frame(
    frame: &Frame,
    rest: &BTreeMap<String, Frame>,
) -> io::Result<(Vec<u8>, &'static str)> {
    let bytes = assemble(frame, rest)?;
    let Some(expected) = frame.checksum else {
        return Ok((bytes, "NOT_PRESENT_CID_VERIFIED"));
    };
    if crc::Crc::<u64>::new(&crc::CRC_64_GO_ISO).checksum(&bytes) == expected {
        return Ok((bytes, "CRC64_GO_ISO"));
    }
    let mut fnv = fnv::FnvHasher::default();
    fnv.write(&bytes);
    if fnv.finish() == expected {
        return Ok((bytes, "FNV1A64_LEGACY"));
    }
    Err(invalid("DATAFRAME_CHECKSUM_MISMATCH"))
}

/// Only a zstd magic-tagged stream is decompressed; malformed zstd never falls
/// back to protobuf. Non-zstd bytes follow the source's raw protobuf lane.
/// # Errors
/// Rejects empty, corrupt or excessive decompressed metadata.
pub fn metadata_bytes(bytes: &[u8]) -> io::Result<(Vec<u8>, &'static str)> {
    if bytes.is_empty() {
        return Err(invalid("MISSING_STATUS_METADATA"));
    }
    if !bytes.starts_with(&[0x28, 0xb5, 0x2f, 0xfd]) {
        return Ok((bytes.to_vec(), "PROTOBUF_UNCOMPRESSED"));
    }
    let mut decoder =
        zstd::stream::read::Decoder::new(bytes).map_err(|_| invalid("ZSTD_INVALID"))?;
    decoder
        .window_log_max(23)
        .map_err(|_| invalid("ZSTD_WINDOW_LIMIT"))?;
    let mut out = Vec::new();
    decoder
        .take(MAX_FRAME_BYTES as u64 + 1)
        .read_to_end(&mut out)
        .map_err(|_| invalid("ZSTD_INVALID"))?;
    if out.len() > MAX_FRAME_BYTES {
        return Err(invalid("ZSTD_OUTPUT_LIMIT"));
    }
    if out.is_empty() {
        return Err(invalid("MISSING_STATUS_METADATA"));
    }
    Ok((out, "ZSTD_PROTOBUF"))
}

fn options() -> impl Options {
    bincode::DefaultOptions::new()
        .with_fixint_encoding()
        .with_limit(MAX_FRAME_BYTES as u64)
        .reject_trailing_bytes()
}

/// Decodes a complete atomic data/status pair; no partial success.
/// # Errors
/// Rejects missing status, malformed wire/protobuf, unknown errors or index drift.
pub fn decode(envelope: &Envelope, rest: &BTreeMap<String, Frame>) -> io::Result<Value> {
    let (wire, wire_checksum) = checked_frame(&envelope.data, rest)?;
    let (stored_meta, metadata_checksum) = checked_frame(&envelope.metadata, rest)?;
    let (meta, compression) = metadata_bytes(&stored_meta)?;
    let tx: VersionedTransaction = options()
        .deserialize(&wire)
        .map_err(|e| invalid(format!("TRANSACTION_WIRE: {e}")))?;
    tx.sanitize()
        .map_err(|e| invalid(format!("TRANSACTION_SANITIZE: {e}")))?;
    if options().serialize(&tx).map_err(invalid)? != wire {
        return Err(invalid("TRANSACTION_WIRE_NONCANONICAL"));
    }
    let status =
        Status::decode(meta.as_slice()).map_err(|e| invalid(format!("STATUS_PROTOBUF: {e}")))?;
    let (version, keys) = resolved_keys(&tx, &status)?;
    project_transaction(
        &tx,
        &status,
        version,
        &keys,
        &wire,
        &stored_meta,
        &meta,
        wire_checksum,
        metadata_checksum,
        compression,
    )
}

fn resolved_keys(
    tx: &VersionedTransaction,
    status: &Status,
) -> io::Result<(&'static str, Vec<String>)> {
    let header = tx.message.header();
    let static_keys = tx.message.static_account_keys();
    let required = usize::from(header.num_required_signatures);
    if required == 0
        || tx.signatures.len() != required
        || required > static_keys.len()
        || usize::from(header.num_readonly_signed_accounts) >= required
        || usize::from(header.num_readonly_unsigned_accounts) > static_keys.len() - required
    {
        return Err(invalid("TRANSACTION_HEADER_SIGNATURE_COUNTS"));
    }
    let (version, writable, readonly) = match &tx.message {
        VersionedMessage::Legacy(_) => ("LEGACY", 0, 0),
        VersionedMessage::V0(m) => (
            "V0",
            m.address_table_lookups
                .iter()
                .map(|l| l.writable_indexes.len())
                .sum(),
            m.address_table_lookups
                .iter()
                .map(|l| l.readonly_indexes.len())
                .sum(),
        ),
    };
    if writable != status.loaded_writable_addresses.len()
        || readonly != status.loaded_readonly_addresses.len()
    {
        return Err(invalid("LOADED_ADDRESS_COUNTS"));
    }
    let mut keys = static_keys
        .iter()
        .map(ToString::to_string)
        .collect::<Vec<_>>();
    for key in status
        .loaded_writable_addresses
        .iter()
        .chain(&status.loaded_readonly_addresses)
    {
        if key.len() != 32 {
            return Err(invalid("LOADED_ADDRESS_LENGTH"));
        }
        keys.push(bs58::encode(key).into_string());
    }
    if keys.len() > 256
        || status.pre_balances.len() != keys.len()
        || status.post_balances.len() != keys.len()
    {
        return Err(invalid("ACCOUNT_BALANCE_COVERAGE"));
    }
    if status.inner_instructions_none && !status.inner_instructions.is_empty()
        || status.log_messages_none && !status.log_messages.is_empty()
        || status.return_data_none && status.return_data_unprojected.is_some()
    {
        return Err(invalid("STATUS_PRESENCE_CONTRADICTION"));
    }
    Ok((version, keys))
}

// These arguments deliberately keep the original/assembled/decompressed byte
// identities separate at the atomic projection boundary.
#[allow(clippy::too_many_arguments)]
fn project_transaction(
    tx: &VersionedTransaction,
    status: &Status,
    version: &str,
    keys: &[String],
    wire: &[u8],
    stored_meta: &[u8],
    meta: &[u8],
    wire_checksum: &str,
    metadata_checksum: &str,
    compression: &str,
) -> io::Result<Value> {
    let mut programs = BTreeSet::new();
    let mut instructions = Vec::new();
    for (i, ix) in tx.message.instructions().iter().enumerate() {
        let program = keys
            .get(usize::from(ix.program_id_index))
            .ok_or_else(|| invalid("PROGRAM_INDEX"))?;
        if ix.accounts.iter().any(|a| usize::from(*a) >= keys.len()) {
            return Err(invalid("ACCOUNT_INDEX"));
        }
        programs.insert(program.clone());
        instructions.push(json!({"index":i,"program_id":program,"program_id_index":ix.program_id_index,"account_indexes":ix.accounts,"data_hex":hex::encode(&ix.data)}));
    }
    let mut inner = Vec::new();
    let mut previous = None;
    for group in &status.inner_instructions {
        if group.index as usize >= instructions.len() || previous.is_some_and(|p| group.index <= p)
        {
            return Err(invalid("INNER_INSTRUCTION_ORDER"));
        }
        previous = Some(group.index);
        for (order, ix) in group.instructions.iter().enumerate() {
            let program = keys
                .get(ix.program_id_index as usize)
                .ok_or_else(|| invalid("INNER_PROGRAM_INDEX"))?;
            if ix.accounts.iter().any(|a| usize::from(*a) >= keys.len()) {
                return Err(invalid("INNER_ACCOUNT_INDEX"));
            }
            programs.insert(program.clone());
            inner.push(json!({"outer_index":group.index,"inner_order":order,"program_id":program,"program_id_index":ix.program_id_index,"account_indexes":ix.accounts,"data_hex":hex::encode(&ix.data),"stack_height":ix.stack_height}));
        }
    }
    let error = if let Some(e) = &status.err {
        let decoded: TransactionError = options()
            .deserialize(&e.err)
            .map_err(|_| invalid("UNSUPPORTED_TRANSACTION_ERROR"))?;
        if options().serialize(&decoded).map_err(invalid)? != e.err {
            return Err(invalid("TRANSACTION_ERROR_NONCANONICAL"));
        }
        serde_json::to_value(decoded).map_err(invalid)?
    } else {
        Value::Null
    };
    let pump = if programs.contains(PUMP_PROGRAM) {
        Some(true)
    } else if status.inner_instructions_none {
        None
    } else {
        Some(false)
    };
    Ok(json!({
        "version":version,"signatures":tx.signatures.iter().map(ToString::to_string).collect::<Vec<_>>(),"signature_crypto_verification":"NOT_PERFORMED",
        "status":if error.is_null(){"OK"}else{"ERROR"},"transaction_error":error,
        "fee_lamports":status.fee.to_string(),"pre_balances_lamports":status.pre_balances.iter().map(ToString::to_string).collect::<Vec<_>>(),"post_balances_lamports":status.post_balances.iter().map(ToString::to_string).collect::<Vec<_>>(),
        "account_keys":keys,"recent_blockhash":tx.message.recent_blockhash().to_string(),"instructions":instructions,"inner_instructions":if status.inner_instructions_none{Value::Null}else{json!(inner)},
        "log_messages":if status.log_messages_none{Value::Null}else{json!(status.log_messages)},"program_ids":programs,"program_ids_semantics":"DECLARED_TOP_LEVEL_AND_RECORDED_INNER_CPI_NOT_EXECUTION_PROOF","pump_program_involvement":pump,"pump_event_decode":"NOT_PERFORMED",
        "compute_units_consumed":status.compute_units_consumed.map(|n|n.to_string()),"cost_units":status.cost_units.map(|n|n.to_string()),
        "wire_hex":hex::encode(wire),"stored_metadata_hex":hex::encode(stored_meta),"protobuf_metadata_hex":hex::encode(meta),
        "wire_sha256":of1_range_recorder::sha256(wire),"metadata_sha256":of1_range_recorder::sha256(stored_meta),
        "wire_checksum":wire_checksum,"metadata_checksum":metadata_checksum,"metadata_codec":compression,"decoded_metadata_bytes":meta.len(),
        "unprojected_fields":["token_balances","rewards","return_data","protobuf_unknown_fields"],"unprojected_bytes_preserved":true,
        "economic_identity":"UNAVAILABLE","name":null,"ticker":null,"launch_at":null
    }))
}
