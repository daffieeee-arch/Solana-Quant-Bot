//! Own direct event association for the separate 27-account exact-quote v2 buy.
//! Reuses recorded order/parent validation only, never legacy account positions.
use crate::{
    pump_buy::{account_key, key_at},
    pump_buy_exact_quote_v2::{Rejection, decode_event},
    pump_sell_context::{check_order, recorded_parents},
};
use of1_range_recorder::sha256;
use pump_protocol_v2::{
    decode::TradeEvent,
    registry::{EVENT_IX_TAG_LE, PUMP_PROGRAM_ID},
};
use serde_json::{Value, json};

pub(crate) fn associated_event(
    tx: &Value,
    target: &Value,
) -> Result<(Value, TradeEvent), Rejection> {
    let outer = target["index"]
        .as_u64()
        .ok_or(Rejection::UnsupportedInvocation)?;
    let top = tx["instructions"]
        .as_array()
        .ok_or(Rejection::UnsupportedInvocation)?;
    if top.get(usize::try_from(outer).map_err(|_| Rejection::UnsupportedInvocation)?)
        != Some(target)
    {
        return Err(Rejection::UnsupportedInvocation);
    }
    let inner = tx["inner_instructions"]
        .as_array()
        .ok_or(Rejection::MissingCpi)?;
    check_order(inner, top.len()).map_err(|_| Rejection::InvalidInvocationOrder)?;
    let group: Vec<_> = inner.iter().filter(|v| v["outer_index"] == outer).collect();
    let identity = |ix: &Value| {
        ix["program_id_index"]
            .as_u64()
            .and_then(|i| key_at(tx, i))
            .is_some_and(|p| ix["program_id"] == p.to_string())
    };
    if !identity(target)
        || target["program_id"] != PUMP_PROGRAM_ID
        || group.iter().any(|v| !identity(v))
    {
        return Err(Rejection::UnsupportedInvocation);
    }
    let (parents, heights) = recorded_parents(&group).map_err(|_| Rejection::InvalidStackHeight)?;
    let mut events = Vec::new();
    for (i, ix) in group.iter().enumerate() {
        if ix["program_id"] != PUMP_PROGRAM_ID {
            continue;
        }
        let data = hex::decode(ix["data_hex"].as_str().ok_or(Rejection::InvalidEvent)?)
            .map_err(|_| Rejection::InvalidEvent)?;
        if data.starts_with(&EVENT_IX_TAG_LE) {
            if parents[i].is_some() || heights[i] != 2 {
                continue;
            }
            events.push((i, data));
        }
    }
    if events.is_empty() {
        return Err(Rejection::MissingEvent);
    }
    if events.len() != 1 {
        return Err(Rejection::AmbiguousEvent);
    }
    let (order, bytes) = &events[0];
    let event_ix = group[*order];
    let authority = account_key(tx, target, 25).ok_or(Rejection::EventAuthorityMismatch)?;
    let accounts = event_ix["account_indexes"]
        .as_array()
        .ok_or(Rejection::EventAuthorityMismatch)?;
    if accounts.len() != 1 || accounts[0].as_u64().and_then(|i| key_at(tx, i)) != Some(authority) {
        return Err(Rejection::EventAuthorityMismatch);
    }
    let event = decode_event(bytes)?;
    let trace:Vec<_>=group.iter().enumerate().map(|(i,ix)|json!({"inner_order":i,"stack_height":heights[i],"program_id":ix["program_id"],"program_id_index":ix["program_id_index"],"parent_inner_order":parents[i],"account_indexes":ix["account_indexes"],"instruction_sha256":ix["data_hex"].as_str().and_then(|s|hex::decode(s).ok()).map(|b|sha256(&b))})).collect();
    Ok((
        json!({"outer_index":outer,"inner_order":order,"stack_height":heights[*order],"parent":{"program_id":PUMP_PROGRAM_ID,"outer_index":outer,"inner_order":null},
        "association":"EXACT_ORDER_AND_HEIGHT_DIRECT_OWN_EVENT_V2_AUTHORITY_25","event_cpi_sha256":sha256(bytes),"event_cpi_hex":hex::encode(bytes),"event_cpi_bytes":bytes.len(),"event_authority":authority.to_string(),"ordered_group_trace":trace,
        "cpi_account_flags":{"evidence":"UNAVAILABLE_NOT_RECORDED","signer":null,"writable":null},"committed_account_state":"NOT_ESTABLISHED"}),
        event,
    ))
}
