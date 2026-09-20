//! Source-bound additive status projection. No Pump parsing/admission occurs here.
//! Optional scalar declarations below observe wire presence; values still obey
//! proto3 defaults from the pinned schema. Exact nested bytes are never rewritten.
use crate::proto::Status;
use of1_range_recorder::sha256;
use prost::Message;
use serde_json::{Value, json};
use std::collections::BTreeSet;

pub const SOURCE: &[u8] = include_bytes!("../sources/token-balance-evidence.json");

#[derive(Clone, PartialEq, Message)]
pub struct Balance {
    #[prost(uint32, optional, tag = "1")]
    pub account_index: Option<u32>,
    #[prost(string, optional, tag = "2")]
    pub mint: Option<String>,
    #[prost(message, optional, tag = "3")]
    pub ui_token_amount: Option<Amount>,
    #[prost(string, optional, tag = "4")]
    pub owner: Option<String>,
    #[prost(string, optional, tag = "5")]
    pub program_id: Option<String>,
}
#[derive(Clone, PartialEq, Message)]
pub struct Amount {
    #[prost(double, optional, tag = "1")]
    pub ui_amount: Option<f64>,
    #[prost(uint32, optional, tag = "2")]
    pub decimals: Option<u32>,
    #[prost(string, optional, tag = "3")]
    pub amount: Option<String>,
    #[prost(string, optional, tag = "4")]
    pub ui_amount_string: Option<String>,
}

/// Decimal display constructed only from a checked u64 and source-bound u8.
/// No floating-point intermediate or removal of the original integer/string.
#[must_use]
pub fn exact_decimal(value: u64, decimals: u8) -> String {
    let digits = value.to_string();
    let places = usize::from(decimals);
    if places == 0 {
        digits
    } else if digits.len() > places {
        format!(
            "{}.{}",
            &digits[..digits.len() - places],
            &digits[digits.len() - places..]
        )
    } else {
        format!("0.{}{}", "0".repeat(places - digits.len()), digits)
    }
}

fn varint(bytes: &[u8], at: &mut usize) -> Result<u64, &'static str> {
    let mut value = 0_u64;
    for shift in (0..70).step_by(7) {
        let byte = *bytes.get(*at).ok_or("TOKEN_BALANCE_PROTOBUF_TRUNCATED")?;
        *at += 1;
        if shift == 63 && byte > 1 {
            return Err("TOKEN_BALANCE_PROTOBUF_VARINT_OVERFLOW");
        }
        value |= u64::from(byte & 127) << shift;
        if byte < 128 {
            return Ok(value);
        }
    }
    Err("TOKEN_BALANCE_PROTOBUF_VARINT_OVERFLOW")
}

// Prost's uint32 projection must not truncate a larger encoded varint. This
// bounded scanner checks only wire shape/range, not new domain semantics.
fn checked_wire(bytes: &[u8], amount: bool) -> Result<(), &'static str> {
    let mut at = 0;
    while at < bytes.len() {
        let key = varint(bytes, &mut at)?;
        let tag = key >> 3;
        if tag == 0 || tag > 0x1fff_ffff {
            return Err("TOKEN_BALANCE_PROTOBUF_TAG");
        }
        let wire = key & 7;
        match wire {
            0 => {
                let value = varint(bytes, &mut at)?;
                if ((!amount && tag == 1) || (amount && tag == 2)) && value > u64::from(u32::MAX) {
                    return Err("TOKEN_BALANCE_UINT32_OVERFLOW");
                }
            }
            1 | 5 => {
                at = at
                    .checked_add(if wire == 1 { 8 } else { 4 })
                    .ok_or("TOKEN_BALANCE_PROTOBUF_LENGTH")?;
            }
            2 => {
                let len = usize::try_from(varint(bytes, &mut at)?)
                    .map_err(|_| "TOKEN_BALANCE_PROTOBUF_LENGTH")?;
                let end = at.checked_add(len).ok_or("TOKEN_BALANCE_PROTOBUF_LENGTH")?;
                let part = bytes
                    .get(at..end)
                    .ok_or("TOKEN_BALANCE_PROTOBUF_TRUNCATED")?;
                if !amount && tag == 3 {
                    checked_wire(part, true)?;
                }
                at = end;
            }
            _ => return Err("TOKEN_BALANCE_PROTOBUF_UNSUPPORTED_WIRE_TYPE"),
        }
        if at > bytes.len() {
            return Err("TOKEN_BALANCE_PROTOBUF_TRUNCATED");
        }
    }
    Ok(())
}

fn address(value: &str) -> bool {
    bs58::decode(value)
        .into_vec()
        .is_ok_and(|bytes| bytes.len() == 32 && bs58::encode(bytes).into_string() == value)
}
fn state(present: bool) -> &'static str {
    if present {
        "EXPLICIT_WIRE"
    } else {
        "PROTO3_DEFAULT"
    }
}

fn observation(raw: &[u8], side: &str, ordinal: usize, keys: &[String]) -> Value {
    let mut out = json!({"side":side,"ordinal":ordinal,"account_index":null,"account_key":null,"mint":null,"owner":null,"program_id":null,"amount_string":null,"amount_u64":null,"decimals":null,"exact_decimal_amount":null,"ui_amount_bits_hex":null,"ui_amount_string":null,"disposition":"QUARANTINED","reason":null,"presence":null,"field_states":null,"raw_token_balance_hex":hex::encode(raw),"raw_token_balance_sha256":sha256(raw)});
    let decoded = checked_wire(raw, false)
        .and_then(|()| Balance::decode(raw).map_err(|_| "TOKEN_BALANCE_PROTOBUF_INVALID"));
    let b = match decoded {
        Ok(b) => b,
        Err(reason) => {
            out["reason"] = json!(reason);
            return out;
        }
    };
    let ui = b.ui_token_amount.as_ref();
    let index = b.account_index.unwrap_or_default();
    out["presence"] = json!({"account_index":b.account_index.is_some(),"mint":b.mint.is_some(),"owner":b.owner.is_some(),"program_id":b.program_id.is_some(),"ui_token_amount":ui.is_some(),"amount":ui.is_some_and(|v|v.amount.is_some()),"decimals":ui.is_some_and(|v|v.decimals.is_some()),"ui_amount":ui.is_some_and(|v|v.ui_amount.is_some()),"ui_amount_string":ui.is_some_and(|v|v.ui_amount_string.is_some())});
    out["field_states"] = json!({"account_index":state(b.account_index.is_some()),"decimals":ui.map_or("UNAVAILABLE_MISSING_MESSAGE", |v|state(v.decimals.is_some()))});
    out["account_index"] = json!(index);
    out["account_key"] = json!(keys.get(index as usize));
    out["mint"] = json!(b.mint.unwrap_or_default());
    out["owner"] = json!(b.owner.unwrap_or_default());
    out["program_id"] = json!(b.program_id.unwrap_or_default());
    let mut missing = Vec::new();
    let mut invalid = Vec::new();
    if index > u32::from(u8::MAX) || out["account_key"].is_null() {
        invalid.push("ACCOUNT_INDEX_OUT_OF_RANGE");
    }
    for field in ["mint", "owner", "program_id"] {
        let value = out[field].as_str().unwrap_or_default();
        if value.is_empty() {
            missing.push(match field {
                "mint" => "MINT_UNAVAILABLE",
                "owner" => "OWNER_UNAVAILABLE",
                _ => "PROGRAM_ID_UNAVAILABLE",
            });
        } else if !address(value) {
            invalid.push(match field {
                "mint" => "MINT_INVALID",
                "owner" => "OWNER_INVALID",
                _ => "PROGRAM_ID_INVALID",
            });
        }
    }
    if let Some(ui) = ui {
        let amount_string = ui.amount.clone().unwrap_or_default();
        let amount = (!amount_string.is_empty()
            && amount_string.bytes().all(|c| c.is_ascii_digit()))
        .then(|| amount_string.parse::<u64>().ok())
        .flatten();
        let decimals = ui.decimals.unwrap_or_default();
        out["amount_string"] = json!(amount_string);
        out["amount_u64"] = json!(amount.map(|v| v.to_string()));
        out["decimals"] = json!(decimals);
        out["ui_amount_bits_hex"] = json!(ui.ui_amount.map(|v| format!("{:016x}", v.to_bits())));
        out["ui_amount_string"] = json!(ui.ui_amount_string.clone().unwrap_or_default());
        if out["amount_string"] == "" {
            missing.push("AMOUNT_UNAVAILABLE");
        } else if amount.is_none() {
            invalid.push("AMOUNT_NOT_U64_DECIMAL_STRING");
        }
        if let Ok(decimals) = u8::try_from(decimals) {
            out["exact_decimal_amount"] = json!(amount.map(|v| exact_decimal(v, decimals)));
        } else {
            invalid.push("DECIMALS_OUTSIDE_SOURCE_U8");
        }
    } else {
        missing.push("UI_TOKEN_AMOUNT_MESSAGE_UNAVAILABLE");
    }
    out["disposition"] = json!(if !invalid.is_empty() {
        "QUARANTINED"
    } else if !missing.is_empty() {
        "MISSING"
    } else {
        "PROJECTED"
    });
    invalid.extend(missing);
    out["reason"] = if invalid.is_empty() {
        Value::Null
    } else {
        json!(invalid.join("|"))
    };
    // A decimal rendering without valid address/index context is not admitted.
    if out["disposition"] != "PROJECTED" {
        out["exact_decimal_amount"] = Value::Null;
    }
    out
}

/// Each retained nested message receives one explicit outcome in original side
/// order. A bad new projection never changes the already decoded transaction.
#[must_use]
pub fn project(status: &Status, keys: &[String], metadata: &[u8], stored: &[u8]) -> Value {
    let mut observations = Vec::new();
    for (side, values) in [
        ("PRE", &status.pre_token_balances_unprojected),
        ("POST", &status.post_token_balances_unprojected),
    ] {
        for (ordinal, raw) in values.iter().enumerate() {
            observations.push(observation(raw, side, ordinal, keys));
        }
    }
    json!({"schema":"TOKEN_BALANCE_CONTEXT_V1","source_receipt_sha256":sha256(SOURCE),"metadata_sha256":sha256(stored),"protobuf_metadata_sha256":sha256(metadata),"observation_count":observations.len(),"pre_observation_count":status.pre_token_balances_unprojected.len(),"post_observation_count":status.post_token_balances_unprojected.len(),"collection_status":if observations.is_empty(){"NO_OBSERVATIONS_RECORDED_EMPTY_OR_UNAVAILABLE"}else{"OBSERVATIONS_RECORDED"},"repeated_empty_semantics":"UNAVAILABLE_EMPTY_OR_NOT_RECORDED_NOT_ZERO_BALANCES","scalar_semantics":"PROTO3_DEFAULT_SEPARATE_FROM_WIRE_PRESENCE","account_contents":"NOT_A_FULL_ACCOUNT_SNAPSHOT","observations":observations})
}

fn role<'a>(fact: &'a Value, name: &str) -> Option<&'a Value> {
    let mut rows = fact["accounts"]
        .as_array()?
        .iter()
        .filter(|r| r["role"] == name);
    let row = rows.next()?;
    if rows.next().is_some() {
        return None;
    }
    Some(row)
}

/// Separate metadata correspondence appended after existing trade admission.
/// A transaction-wide delta never becomes the amount of one instruction.
#[must_use]
#[allow(clippy::too_many_lines)] // Keep this bounded, additive correspondence gate together.
pub fn trade_context(record: &Value, fact: &Value) -> Value {
    let buy = fact["schema"] == "PUMP_SILVER_RECORDED_BUY_EXACT_QUOTE_V2_1";
    let (mint_role, program_role, user_role, curve_role) = if buy {
        (
            "base_mint",
            "base_token_program",
            "associated_base_user",
            "associated_base_bonding_curve",
        )
    } else {
        (
            "mint",
            "token_program",
            "associated_user",
            "associated_bonding_curve",
        )
    };
    let mint = role(fact, mint_role).and_then(|r| r["observed"].as_str());
    let program = role(fact, program_role).and_then(|r| r["observed"].as_str());
    let context = &record["transaction"]["token_balance_context"];
    let observations = context["observations"]
        .as_array()
        .map_or(&[][..], Vec::as_slice);
    let mut gaps = BTreeSet::new();
    let mut conflict = false;
    let mut matched = BTreeSet::new();
    let mut decimal_values = BTreeSet::new();
    let mut decimal_states = BTreeSet::new();
    if mint.is_none()
        || program.is_none()
        || fact["event_reported"]["mint_address"].as_str() != mint
    {
        gaps.insert("SOURCE_ROLE_OR_EVENT_MINT_MISMATCH");
        conflict = true;
    }
    // Contradictory metadata for the same mint cannot disappear through a role
    // filter. This check creates no global statement about other transactions.
    for o in observations
        .iter()
        .filter(|o| o["mint"].as_str() == mint && mint.is_some())
    {
        if o["disposition"] != "PROJECTED" {
            gaps.insert("MINT_OBSERVATION_INCOMPLETE_OR_QUARANTINED");
        }
        if o["program_id"]
            .as_str()
            .is_some_and(|p| !p.is_empty() && Some(p) != program)
        {
            gaps.insert("CONFLICTING_MINT_TOKEN_PROGRAM");
            conflict = true;
        }
        if let Some(d) = o["decimals"].as_u64() {
            decimal_values.insert(d);
        }
    }
    if decimal_values.len() > 1 {
        gaps.insert("CONFLICTING_MINT_DECIMALS");
        conflict = true;
    }
    let mut roles = Vec::new();
    for (name, owner_role) in [(user_role, "user"), (curve_role, "bonding_curve")] {
        let r = role(fact, name);
        let index = r.and_then(|v| v["compiled_key_index"].as_u64());
        let key = r.and_then(|v| v["observed"].as_str());
        let owner = role(fact, owner_role).and_then(|v| v["observed"].as_str());
        let mut pre = Vec::new();
        let mut post = Vec::new();
        if index.is_none() || key.is_none() || owner.is_none() {
            gaps.insert("SOURCE_TOKEN_ACCOUNT_ROLE_UNAVAILABLE");
        }
        for (i, o) in observations
            .iter()
            .enumerate()
            .filter(|(_, o)| o["account_index"].as_u64() == index && index.is_some())
        {
            if o["account_key"].as_str() != key
                || o["mint"].as_str() != mint
                || o["program_id"].as_str() != program
                || o["owner"].as_str() != owner
            {
                gaps.insert("TOKEN_ACCOUNT_ROLE_METADATA_MISMATCH");
                conflict = true;
                continue;
            }
            if o["disposition"] != "PROJECTED" {
                gaps.insert("TOKEN_ACCOUNT_BALANCE_UNAVAILABLE");
                continue;
            }
            matched.insert(i);
            if let Some(state) = o["field_states"]["decimals"].as_str() {
                decimal_states.insert(state);
            }
            if o["side"] == "PRE" {
                pre.push(i);
            } else if o["side"] == "POST" {
                post.push(i);
            }
        }
        if pre.is_empty() && post.is_empty() {
            gaps.insert("TOKEN_ACCOUNT_BALANCE_NOT_RECORDED");
        }
        if pre.len() > 1 || post.len() > 1 {
            gaps.insert("DUPLICATE_TOKEN_ACCOUNT_SIDE");
            conflict = true;
        }
        let delta = if pre.len() == 1
            && post.len() == 1
            && observations[pre[0]]["decimals"] == observations[post[0]]["decimals"]
        {
            let amount = |i: usize| {
                observations[i]["amount_u64"]
                    .as_str()
                    .and_then(|n| n.parse::<u64>().ok())
            };
            amount(pre[0])
                .zip(amount(post[0]))
                .map(|(a, b)| (i128::from(b) - i128::from(a)).to_string())
        } else {
            None
        };
        roles.push(json!({"role":name,"account_index":index,"account_key":key,"expected_owner":owner,"pre_observation_indexes":pre,"post_observation_indexes":post,"transaction_delta_raw_signed":delta,"delta_status":if delta.is_some(){"RECORDED_TRANSACTION_WIDE_NOT_INSTRUCTION_AMOUNT"}else{"UNAVAILABLE_MISSING_OR_AMBIGUOUS_SIDE"}}));
    }
    let bound = !conflict && gaps.is_empty() && decimal_values.len() == 1 && !matched.is_empty();
    let decimals = if bound {
        decimal_values
            .first()
            .copied()
            .and_then(|n| u8::try_from(n).ok())
    } else {
        None
    };
    let amount = fact["event_reported"]["token_amount_raw_u64"]
        .as_str()
        .and_then(|n| n.parse::<u64>().ok());
    json!({"schema":"TRADE_TOKEN_BALANCE_CONTEXT_V1","source_receipt_sha256":sha256(SOURCE),"bronze_record_sha256":fact["bronze_record_sha256"],"metadata_sha256":record["transaction"]["metadata_sha256"],"base_mint":mint,"base_token_program":program,"binding_status":if conflict{"CONFLICTING"}else if decimals.is_some(){"BOUND_RECORDED_BASE_UNITS"}else{"UNAVAILABLE"},"decimals":decimals,"decimals_evidence":if decimals.is_none(){"UNAVAILABLE"}else if decimal_states.len()>1{"MIXED_EXPLICIT_WIRE_AND_PROTO3_DEFAULT"}else{decimal_states.first().copied().unwrap_or("UNAVAILABLE")},"event_token_amount_u64":amount.map(|v|v.to_string()),"event_token_amount_decimal":amount.zip(decimals).map(|(n,d)|exact_decimal(n,d)),"matched_observation_indexes":matched,"roles":roles,"gaps":gaps,"delta_semantics":"TRANSACTION_WIDE_NOT_SINGLE_INSTRUCTION_QUANTITY","account_contents":"NOT_A_FULL_ACCOUNT_SNAPSHOT","cpi_privileges":"UNAVAILABLE_NOT_RECORDED","quote_units":"UNAVAILABLE_NOT_INFERRED_FROM_BASE_METADATA","economic_execution":"NOT_ESTABLISHED","research_ready":false})
}
