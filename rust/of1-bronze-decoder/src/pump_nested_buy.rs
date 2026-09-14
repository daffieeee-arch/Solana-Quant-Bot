//! Exact nested 25-byte buy observations, without admitting an unexplained
//! Mayhem account profile. Message capacity is never relabelled CPI privilege.
use crate::{invalid, pump_buy, pump_buy_variants, pump_sell};
use of1_range_recorder::sha256;
use pump_protocol_v2::{
    decode::probe_buy_instruction_layout,
    registry::{BUY_DISCRIMINATOR, PUMP_PROGRAM_ID},
};
use serde_json::{Value, json};
use std::{collections::BTreeSet, io};

#[path = "pump_nested_buy_context.rs"]
mod context;

pub const SOURCE: &[u8] = include_bytes!("../sources/pump-nested-buy-evidence.json");
pub const PROFILE: &str = "pump-buy-9c82f61-token2022-mayhem18-nested-height2-unresolved-v1";
const TOKEN2022: &str = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb";
const MAYHEM: &str = "MAyhSmzXzV1pTf7LsNkrNwkWKTo4ougAJ1PPg47MD4e";

/// Record only the bounded nested buy observations; no top-level or historical
/// suffix/absent-argument route is changed and no Silver candidate is registered.
/// # Errors
/// Rejects malformed internal instruction bytes or source-receipt JSON.
pub fn inspect(tx: &Value) -> io::Result<Vec<Value>> {
    let mut out = Vec::new();
    if let Some(inner) = tx["inner_instructions"].as_array() {
        for ix in inner
            .iter()
            .filter(|ix| ix["program_id"] == PUMP_PROGRAM_ID)
        {
            let bytes = hex::decode(
                ix["data_hex"]
                    .as_str()
                    .ok_or_else(|| invalid("NESTED_BUY_DATA"))?,
            )
            .map_err(invalid)?;
            if bytes.len() == 25 && bytes.starts_with(&BUY_DISCRIMINATOR) {
                out.push(diagnose(tx, ix, &bytes)?);
            }
        }
    }
    Ok(out)
}

fn diagnostic_accounts(
    tx: &Value,
    ix: &Value,
    event: Option<&pump_protocol_v2::decode::TradeEvent>,
    source: &Value,
) -> Vec<Value> {
    let mut rows = pump_buy::account_rows_for_token(tx, ix, event, source, Some(TOKEN2022));
    pump_sell::mark_cpi_flags_unavailable(&mut rows);
    for r in &mut rows {
        r["role_evidence"] = json!("PINNED_BUY_EXPECTATION_NOT_PROOF_OF_MISMATCHED_ACCOUNT_ROLE");
        r["actual_role_if_mismatched"] = if r["address_match"] == true {
            Value::Null
        } else {
            json!("UNAVAILABLE_SOURCE_PROOF_MISSING")
        };
        if r["position"] == 8 {
            r["address_basis"] = json!("EXPLICIT_TOKEN2022_PROFILE_NOT_MINT_OWNER_PROOF");
        }
    }
    rows
}

fn diagnose(tx: &Value, ix: &Value, data: &[u8]) -> io::Result<Value> {
    let source: Value = serde_json::from_slice(pump_buy::SOURCE).map_err(invalid)?;
    let instruction = probe_buy_instruction_layout(data);
    let context = context::associated_event(tx, ix);
    let event = context.as_ref().ok().map(|(_, e)| e);
    let rows = diagnostic_accounts(tx, ix, event, &source);
    let count = ix["account_indexes"].as_array().map(Vec::len);
    let addresses = count == Some(18)
        && rows.len() == 18
        && rows.iter().all(|r| r["address_match"] == true)
        && rows[1]["documented_fee_recipient"] == true
        && rows
            .iter()
            .filter_map(|r| r["observed"].as_str())
            .collect::<BTreeSet<_>>()
            .len()
            == 18;
    let message_flags = rows
        .iter()
        .all(|r| r["message_minimum_privileges_match"] == true);
    let correlation=instruction.as_ref().ok().zip(event).map(|(i,e)|json!({
        "mint_matches":pump_buy::account_key(tx,ix,2).is_some_and(|p|p.as_ref()==e.mint),
        "user_matches":pump_buy::account_key(tx,ix,6).is_some_and(|p|p.as_ref()==e.user),
        "token_amount_matches":i.amount==e.token_amount,"track_volume_matches":i.track_volume==e.track_volume,
        "buy_representation":e.is_buy&&e.ix_name=="buy"}));
    let correlated = correlation
        .as_ref()
        .and_then(Value::as_object)
        .is_some_and(|o| o.values().all(|v| v == true));
    let sol_vault = pump_buy::pda(MAYHEM, &[b"sol-vault"]);
    let mayhem = event.is_some_and(|e| e.mayhem_mode)
        && pump_buy::account_key(tx, ix, 8).is_some_and(|p| p.to_string() == TOKEN2022)
        && context
            .as_ref()
            .ok()
            .is_some_and(|(c, _)| c["selected_invocation"]["parent_program_id"] == MAYHEM);
    let status_ok = tx["status"] == "OK" && tx["transaction_error"].is_null();
    let mut gaps = vec!["NESTED_MAYHEM_BUY_PROFILE_NOT_REGISTERED_NO_SILVER_ADMISSION"];
    if !addresses {
        gaps.push("ACCOUNT_ADDRESS_OR_FEE_PROFILE_MISMATCH");
    }
    match rows[16]["address_match"].as_bool() {
        Some(false) => gaps.push("REMAINING_BUY_ACCOUNT_16_DIFFERS_FROM_SOURCE_CURVE_V2_PDA"),
        None => gaps.push("REMAINING_BUY_ACCOUNT_16_CORRESPONDENCE_UNAVAILABLE"),
        Some(true) => {}
    }
    match rows[6]["message_signer"].as_bool() {
        Some(false) => gaps.push("USER_MESSAGE_SIGNATURE_ABSENT_CPI_SIGNER_UNAVAILABLE"),
        None => gaps.push("USER_MESSAGE_SIGNER_UNAVAILABLE_CPI_SIGNER_UNAVAILABLE"),
        Some(true) => {}
    }
    if !message_flags {
        gaps.push("MESSAGE_MINIMUM_PRIVILEGES_NOT_ESTABLISHED");
    }
    if !correlated {
        gaps.push("INSTRUCTION_EVENT_CORRESPONDENCE_NOT_ESTABLISHED");
    }
    if !mayhem {
        gaps.push("OBSERVED_MAYHEM_TOKEN2022_PARENT_PROFILE_NOT_ESTABLISHED");
    }
    if !status_ok {
        gaps.push("TRANSACTION_NOT_SUCCESSFUL");
    }
    let reason = if instruction.is_err() {
        "INSTRUCTION_LAYOUT_REJECTED"
    } else if context.is_err() {
        "OWN_EVENT_CONTEXT_REJECTED"
    } else if !status_ok {
        "TRANSACTION_NOT_SUCCESSFUL"
    } else if !addresses {
        "ACCOUNT_MISMATCH"
    } else {
        "SOURCE_PROFILE_NOT_ADMITTED"
    };
    Ok(
        json!({"schema":"PUMP_NESTED_BUY_DIAGNOSTIC_1","evaluated_profile":PROFILE,"source_evidence_sha256":sha256(SOURCE),
        "outer_index":ix["outer_index"],"instruction_inner_order":ix["inner_order"],"instruction_stack_height":ix["stack_height"],
        "instruction_data_hex":hex::encode(data),"instruction_sha256":sha256(data),"instruction_bytes":data.len(),
        "instruction":instruction.as_ref().ok().map(|i|json!({"amount_raw_u64":i.amount.to_string(),"max_sol_cost_raw_u64":i.max_sol_cost.to_string(),"track_volume":i.track_volume,"track_volume_evidence":"ACTUAL_BORSCH_BOOL_BYTE_AT_OFFSET_24"})),
        "instruction_error":instruction.as_ref().err(),"full_instruction_layout_match":instruction.is_ok(),
        "account_count":count,"accounts":rows,"account_address_correspondence":addresses,"message_minimum_privileges_match":message_flags,
        "event_context":context.as_ref().ok().map(|(c,_)|c),"event_association_failure":context.as_ref().err(),"event_reported":event.map(pump_buy_variants::event_fields),"event_correlation":correlation,
        "observation_predicates":{"full_instruction_exhaustion":instruction.is_ok(),"own_complete_nested_context":context.is_ok(),"all_18_accounts":addresses,"message_minimum_capacity":message_flags,"matching_instruction_event":correlated,"recorded_transaction_ok":status_ok,"mayhem_token2022_parent":mayhem},
        "mayhem_context":{"user_matches_sdk_sol_vault_pda":sol_vault.is_some_and(|(p,_)|Some(p)==pump_buy::account_key(tx,ix,6)),"sol_vault_expected":sol_vault.map(|(p,_)|p.to_string()),"bump":sol_vault.map(|(_,b)|b),"cpi_signer":null,"cpi_writable":null,"privileges_evidence":"UNAVAILABLE_NOT_RECORDED_NO_INFERENCE_FROM_PDA_OR_SUCCESS"},
        "matching_registered_candidates":[],"selected_candidate":null,"disposition":"NOT_ADMITTED","reason":reason,"proof_gaps":gaps,
        "silver":"NOT_PRODUCED","transaction_status":tx["status"],"account_contents_verified":false,"committed_state":"NOT_ESTABLISHED","historical_activation":"UNKNOWN",
        "quote_mint_identity":"UNKNOWN","quote_decimals":null,"base_decimals":null,"name":null,"ticker":null,"launch_at":null,"executable_price":null,"research_ready":false}),
    )
}
