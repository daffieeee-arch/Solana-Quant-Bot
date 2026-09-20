//! Separate source-version comparisons, never an implicit version switch.
//! A full 24-byte argument match against a retained historical extract does
//! not prove compatibility with modern accounts/events or supply `track_volume`.
use crate::{invalid, pump_buy};
use of1_range_recorder::sha256;
use pump_protocol_v2::{
    decode::TradeEvent, registry::BUY_DISCRIMINATOR, registry::PUMP_PROGRAM_ID,
};
use serde_json::{Value, json};
use std::{collections::BTreeSet, io};

pub const SOURCE: &[u8] = include_bytes!("../sources/pump-buy24-evidence.json");
pub const PROFILE: &str = "pump-buy-legacy24-modern18-source-gap-v1";
const TOKEN2022: &str = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb";
const TOKENKEG: &str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";

/// Inspect exactly the observed top-level two-u64 argument shape. Other byte
/// shapes retain their existing diagnostics, without truncation or padding.
/// # Errors
/// Refuses malformed Bronze instruction bytes or corrupted source receipts.
pub fn inspect(tx: &Value) -> io::Result<Vec<Value>> {
    let Some(top) = tx["instructions"].as_array() else {
        return Ok(Vec::new());
    };
    let mut out = Vec::new();
    for ix in top.iter().filter(|ix| ix["program_id"] == PUMP_PROGRAM_ID) {
        let bytes = hex::decode(
            ix["data_hex"]
                .as_str()
                .ok_or_else(|| invalid("BUY24_DATA"))?,
        )
        .map_err(invalid)?;
        if bytes.len() == 24 && bytes.starts_with(&BUY_DISCRIMINATOR) {
            out.push(diagnose(tx, top, ix, &bytes)?);
        }
    }
    Ok(out)
}

pub(crate) fn event_fields(e: &TradeEvent) -> Value {
    json!({"classification":"SOURCE_LAYOUT_EVENT_FIELDS_NOT_ADMITTED_SILVER",
        "mint_address":bs58::encode(e.mint).into_string(),"user_address":bs58::encode(e.user).into_string(),
        "creator_address":bs58::encode(e.creator).into_string(),"fee_recipient":bs58::encode(e.fee_recipient).into_string(),
        "token_amount_raw_u64":e.token_amount.to_string(),"sol_amount_raw_u64":e.sol_amount.to_string(),
        "timestamp_raw_i64":e.timestamp.to_string(),"is_buy":e.is_buy,"ix_name":e.ix_name,
        "track_volume":e.track_volume,"mayhem_mode":e.mayhem_mode,
        "fee_basis_points_raw_u64":e.fee_basis_points.to_string(),"fee_raw_u64":e.fee.to_string(),
        "creator_fee_basis_points_raw_u64":e.creator_fee_basis_points.to_string(),"creator_fee_raw_u64":e.creator_fee.to_string(),
        "buyback_fee_basis_points_raw_u64":e.buyback_fee_basis_points.to_string(),"buyback_fee_raw_u64":e.buyback_fee.to_string(),
        "virtual_sol_reserves_raw_u64":e.virtual_sol_reserves.to_string(),"virtual_token_reserves_raw_u64":e.virtual_token_reserves.to_string(),
        "real_sol_reserves_raw_u64":e.real_sol_reserves.to_string(),"real_token_reserves_raw_u64":e.real_token_reserves.to_string(),
        "quote_mint_bytes_base58":bs58::encode(e.quote_mint).into_string(),"quote_amount_raw_u64":e.quote_amount.to_string(),
        "reserve_role":"EVENT_REPORTED_NOT_ACCOUNT_STATE","timestamp_role":"EVENT_REPORTED_NOT_INDEPENDENT_BLOCK_TIME"})
}

fn comparisons(source: &Value) -> Vec<Value> {
    source["version_comparisons"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|v| {
            let mut comparison = v.clone();
            comparison["args_layout_match"] =
                json!(v["instruction_bytes"] == 24 && v["discriminator_hex"] == "66063d1201daebea");
            comparison["full_observation_profile_match"] = json!(false);
            comparison
        })
        .collect()
}

fn include_event_bytes(tx: &Value, mut context: Value) -> io::Result<Value> {
    let inner = tx["inner_instructions"]
        .as_array()
        .ok_or_else(|| invalid("BUY24_EVENT"))?;
    let own = inner
        .iter()
        .find(|v| {
            v["outer_index"] == context["outer_index"]
                && v["inner_order"] == context["inner_order"]
                && v["program_id"] == PUMP_PROGRAM_ID
                && v["data_hex"]
                    .as_str()
                    .and_then(|s| hex::decode(s).ok())
                    .is_some_and(|b| sha256(&b) == context["event_sha256"])
        })
        .ok_or_else(|| invalid("BUY24_EVENT_IDENTITY"))?;
    context["data_hex"] = own["data_hex"].clone();
    Ok(context)
}

fn diagnose(tx: &Value, top: &[Value], ix: &Value, bytes: &[u8]) -> io::Result<Value> {
    let source: Value = serde_json::from_slice(SOURCE).map_err(invalid)?;
    let account_source: Value = serde_json::from_slice(pump_buy::SOURCE).map_err(invalid)?;
    // Checked extraction; no floating point, fabricated option byte or prefix
    // acceptance. These are documented historical argument spans only.
    let number = |start: usize| -> io::Result<u64> {
        let span: [u8; 8] = bytes
            .get(start..start + 8)
            .ok_or_else(|| invalid("BUY24_BOUNDS"))?
            .try_into()
            .map_err(invalid)?;
        Ok(u64::from_le_bytes(span))
    };
    let (amount, max_cost) = (number(8)?, number(16)?);
    let (event, event_failure) = match pump_buy::event_context(tx, top, ix) {
        Ok(e) => (Some(e), None),
        Err(e) => (None, Some(e)),
    };
    let e = event.as_ref().map(|(_, e)| e);
    let token = pump_buy::account_key(tx, ix, 8).map(|p| p.to_string());
    let supported_token = token
        .as_deref()
        .filter(|t| [TOKENKEG, TOKEN2022].contains(t));
    let mut rows = pump_buy::account_rows_for_token(tx, ix, e, &account_source, supported_token);
    for r in &mut rows {
        r["role_evidence"] = json!("MODERN_SOURCE_EXPECTATION_NOT_HISTORICAL_ROLE_PROOF");
        r["cpi_signer"] = Value::Null;
        r["cpi_writable"] = Value::Null;
        r["cpi_privileges_verified"] = json!(false);
        r["message_minimum_privileges_match"] = r["required_privileges_match"].clone();
        r.as_object_mut()
            .expect("constructed row")
            .remove("required_privileges_match");
        if r["position"] == 8 {
            r["address_basis"] =
                json!("OBSERVED_SUPPORTED_SDK_TOKEN_PARAMETER_NOT_MINT_OWNER_PROOF");
        }
    }
    let count = ix["account_indexes"].as_array().map(Vec::len);
    let addresses = count == Some(18)
        && supported_token.is_some()
        && rows.len() == 18
        && rows.iter().all(|r| r["address_match"] == true)
        && rows[1]["documented_fee_recipient"] == true
        && rows
            .iter()
            .filter_map(|r| r["observed"].as_str())
            .collect::<BTreeSet<_>>()
            .len()
            == 18;
    let flags = rows
        .iter()
        .all(|r| r["message_minimum_privileges_match"] == true);
    let key_matches = |position, raw: Option<[u8; 32]>| {
        pump_buy::account_key(tx, ix, position)
            .zip(raw)
            .map(|(p, raw)| p.as_ref() == raw)
    };
    let correlations = json!({"mint_matches":key_matches(2,e.map(|e|e.mint)),"user_matches":key_matches(6,e.map(|e|e.user)),
        "token_amount_matches":e.map(|e|e.token_amount==amount),"buy_representation":e.map(|e|e.is_buy && e.ix_name=="buy"),
        "track_volume_argument_matches":null,"track_volume_argument_evidence":"NOT_ENCODED_DO_NOT_DEFAULT_FROM_EVENT"});
    let context = event
        .as_ref()
        .map(|(c, _)| include_event_bytes(tx, c.clone()))
        .transpose()?;
    let mut gaps = vec![
        "AUTHORITATIVE_24_BYTE_MODERN_ACCOUNTS_EVENT_COMPATIBILITY_UNAVAILABLE",
        "OMITTED_TRACK_VOLUME_SEMANTICS_UNAVAILABLE",
        "HISTORICAL_IDL_BYTES_NOT_RETAINED_ONLY_HASH_BOUND_EXTRACT",
    ];
    if !addresses {
        gaps.push("MODERN_ACCOUNT_ADDRESS_OR_FEE_CORRESPONDENCE_NOT_ESTABLISHED");
    }
    if !flags {
        gaps.push("MESSAGE_MINIMUM_PRIVILEGES_NOT_ESTABLISHED");
    }
    if event.is_none() {
        gaps.push("OWN_DIRECT_EVENT_CONTEXT_NOT_ESTABLISHED");
    }
    if e.is_some_and(|e| e.token_amount != amount || !e.is_buy || e.ix_name != "buy") {
        gaps.push("INSTRUCTION_EVENT_FIELD_CONFLICT");
    }
    if tx["status"] != "OK" || !tx["transaction_error"].is_null() {
        gaps.push("TRANSACTION_NOT_SUCCESSFUL");
    }
    Ok(
        json!({"schema":"PUMP_BUY_VARIANT_DIAGNOSTIC_1","evaluated_profile":PROFILE,
        "disposition":"NOT_ADMITTED","reason":"SOURCE_VERSION_BRIDGE_UNAVAILABLE","source_evidence_sha256":sha256(SOURCE),
        "outer_index":ix["index"],"instruction":{"data_hex":hex::encode(bytes),"sha256":sha256(bytes),"bytes":bytes.len(),"discriminator_hex":hex::encode(BUY_DISCRIMINATOR),
            "amount_raw_u64":amount.to_string(),"max_sol_cost_raw_u64":max_cost.to_string(),"track_volume":null,"track_volume_evidence":"NOT_ENCODED_IN_24_BYTES",
            "classification":"HISTORICAL_ARGUMENT_LAYOUT_DIAGNOSTIC_NOT_SELECTED_CANDIDATE","full_input_examined":true,"bytes_discarded":0,"bytes_synthesized":0},
        "source_comparisons":comparisons(&source),"token_program":token,"account_count":count,"accounts":rows,
        "account_address_correspondence":addresses,"message_minimum_privileges_match":flags,"account_contents_verified":false,
        "event_context":context,"event_reported":e.map(event_fields),"event_correlation":correlations,"event_association_failure":event_failure,
        "transaction_status":tx["status"],"proof_gaps":gaps,"silver":"NOT_PRODUCED","research_ready":false,
        "legacy_b3_probe":"PRESERVED_SCHEMA_SPECIFIC_REJECTION_NOT_UNIVERSAL_BYTE_CORRUPTION","candidate_selection":"NOT_PERFORMED_NO_SOURCE_BRIDGE",
        "committed_state":"NOT_ESTABLISHED","historical_activation":"UNKNOWN","quote_mint_identity":"UNKNOWN","quote_decimals":null,"base_decimals":null,
        "name":null,"ticker":null,"launch_at":null,"executable_price":null}),
    )
}
