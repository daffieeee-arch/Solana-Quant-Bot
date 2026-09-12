//! Bounded height-two Pump sell -> its own immediate event-CPI only.
//! Recorded instruction preorder/heights are not CPI `AccountMeta` flags.
use crate::{
    pump_buy::{account_key, key_at},
    pump_sell::{Rejection, decode_event},
};
use of1_range_recorder::sha256;
use pump_protocol_v2::{
    decode::TradeEvent,
    registry::{EVENT_IX_TAG_LE, PUMP_PROGRAM_ID},
};
use serde_json::{Value, json};

pub const SOURCE: &[u8] = include_bytes!("../sources/pump-nested-sell-evidence.json");
pub const CANDIDATE: &str = "pump-sell-9c82f61-token2022-cashback17-nested-height2-v1";

fn check_order(inner: &[Value], top_count: usize) -> Result<(), Rejection> {
    let mut previous = None;
    let mut expected_order = 0_u64;
    for ix in inner {
        let group = ix["outer_index"]
            .as_u64()
            .ok_or(Rejection::InvalidInvocationOrder)?;
        if previous != Some(group) {
            if previous.is_some_and(|p| group <= p) || group >= top_count as u64 {
                return Err(Rejection::InvalidInvocationOrder);
            }
            expected_order = 0;
        }
        if ix["inner_order"].as_u64() != Some(expected_order) {
            return Err(Rejection::InvalidInvocationOrder);
        }
        expected_order += 1;
        previous = Some(group);
    }
    Ok(())
}

/// Pop completed sibling/subtrees before determining the parent. Validate the
/// entire group: an unknown later boundary could hide a duplicate event.
fn recorded_parents(group: &[&Value]) -> Result<(Vec<Option<usize>>, Vec<usize>), Rejection> {
    let mut stack = vec![None];
    let mut parents = Vec::new();
    let mut heights = Vec::new();
    for (position, ix) in group.iter().enumerate() {
        let h = ix["stack_height"]
            .as_u64()
            .and_then(|n| usize::try_from(n).ok())
            .ok_or(Rejection::MissingStackHeight)?;
        if h < 2 || h > stack.len() + 1 {
            return Err(Rejection::InvalidStackHeight);
        }
        stack.truncate(h - 1);
        parents.push(*stack.last().ok_or(Rejection::InvalidStackHeight)?);
        heights.push(h);
        stack.push(Some(position));
    }
    Ok((parents, heights))
}

/// No sorting, missing-height imputation, log-string fallback or nearby-event search.
/// The existing complete Bronze reader is the provenance boundary of this API.
pub(crate) fn associated_event(
    tx: &Value,
    target: &Value,
) -> Result<(Value, TradeEvent), Rejection> {
    let outer = target["outer_index"]
        .as_u64()
        .ok_or(Rejection::InvalidInvocationOrder)?;
    let order = target["inner_order"]
        .as_u64()
        .and_then(|n| usize::try_from(n).ok())
        .ok_or(Rejection::InvalidInvocationOrder)?;
    let top = tx["instructions"]
        .as_array()
        .ok_or(Rejection::InvalidInvocationOrder)?;
    let root = top
        .get(usize::try_from(outer).map_err(|_| Rejection::InvalidInvocationOrder)?)
        .filter(|v| v["index"] == outer)
        .ok_or(Rejection::InvalidInvocationOrder)?;
    let inner = tx["inner_instructions"]
        .as_array()
        .ok_or(Rejection::MissingCpi)?;
    // Verify the recorded flattened group/order keys before selecting a group.
    check_order(inner, top.len())?;
    let group: Vec<_> = inner.iter().filter(|v| v["outer_index"] == outer).collect();
    if group.get(order).copied() != Some(target) {
        return Err(Rejection::InvalidInvocationOrder);
    }
    let identity_ok = |ix: &Value| {
        ix["program_id_index"]
            .as_u64()
            .and_then(|i| key_at(tx, i))
            .is_some_and(|k| ix["program_id"] == k.to_string())
    };
    if !identity_ok(root)
        || group.iter().any(|v| !identity_ok(v))
        || target["program_id"] != PUMP_PROGRAM_ID
    {
        return Err(Rejection::UnsupportedInvocation);
    }
    let root_accounts = root["account_indexes"]
        .as_array()
        .ok_or(Rejection::ParentAccountMismatch)?;
    let target_accounts = target["account_indexes"]
        .as_array()
        .ok_or(Rejection::AccountMismatch)?;
    if !root_accounts.contains(&target["program_id_index"])
        || target_accounts
            .iter()
            .any(|i| !i.is_u64() || !root_accounts.contains(i))
    {
        return Err(Rejection::ParentAccountMismatch);
    }
    let (parents, heights) = recorded_parents(&group)?;
    if heights[order] != 2 || parents[order].is_some() {
        return Err(Rejection::UnsupportedInvocation);
    }
    let end = ((order + 1)..group.len())
        .find(|&i| heights[i] <= heights[order])
        .unwrap_or(group.len());
    let mut events = Vec::new();
    for i in (order + 1)..end {
        if parents[i] != Some(order) || group[i]["program_id"] != PUMP_PROGRAM_ID {
            continue;
        }
        let data = hex::decode(
            group[i]["data_hex"]
                .as_str()
                .ok_or(Rejection::InvalidEvent)?,
        )
        .map_err(|_| Rejection::InvalidEvent)?;
        if data.starts_with(&EVENT_IX_TAG_LE) {
            events.push((i, data));
        }
    }
    if events.is_empty() {
        return Err(Rejection::MissingEvent);
    }
    if events.len() != 1 {
        return Err(Rejection::AmbiguousEvent);
    }
    let (event_order, bytes) = &events[0];
    let event_ix = group[*event_order];
    let authority = account_key(tx, target, 10).ok_or(Rejection::EventAuthorityMismatch)?;
    let event_accounts = event_ix["account_indexes"]
        .as_array()
        .ok_or(Rejection::EventAuthorityMismatch)?;
    if event_accounts.len() != 1
        || event_accounts[0].as_u64().and_then(|i| key_at(tx, i)) != Some(authority)
    {
        return Err(Rejection::EventAuthorityMismatch);
    }
    let event = decode_event(bytes)?;
    let trace:Vec<_>=group.iter().enumerate().map(|(i,ix)|json!({"inner_order":i,"stack_height":heights[i],"program_id":ix["program_id"],"program_id_index":ix["program_id_index"],"parent_inner_order":parents[i],"account_indexes":ix["account_indexes"],"instruction_sha256":ix["data_hex"].as_str().and_then(|s|hex::decode(s).ok()).map(|b|sha256(&b))})).collect();
    Ok((
        json!({"association":"RECORDED_ORDER_HEIGHTS_EXACT_IMMEDIATE_CHILD_EVENT",
        "outer_index":outer,"inner_order":event_order,"stack_height":heights[*event_order],
        "parent":{"program_id":PUMP_PROGRAM_ID,"outer_index":outer,"inner_order":order},
        "selected_invocation":{"outer_index":outer,"inner_order":order,"stack_height":heights[order],"program_id":PUMP_PROGRAM_ID,"parent_program_id":root["program_id"],"parent_accounts_contain_child_accounts":true},
        "subtree":{"start_inner_order":order,"end_inner_order_exclusive":end,"boundary":"FIRST_LATER_HEIGHT_LE_SELECTED_OR_END_OF_RECORDED_GROUP"},
        "ordered_group_trace":trace,"event_cpi_sha256":sha256(bytes),"event_cpi_hex":hex::encode(bytes),"event_cpi_bytes":bytes.len(),"event_authority":authority.to_string(),
        "cpi_account_flags":{"signer":null,"writable":null,"evidence":"UNAVAILABLE_NOT_RECORDED_IN_STATUS_METADATA"},"root_program_semantics":"NOT_DECODED_OR_ASSUMED"}),
        event,
    ))
}
