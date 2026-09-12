//! One source-pinned direct sell/Token-2022/cashback account pattern.
//! Silver contains recorded instruction/event facts, NEVER account-write state,
//! executable economics, historical activation, or a repaired buy suffix.
use crate::{
    invalid, pump,
    pump_buy::{account_key, key_at, pda, privileges},
};
use borsh::BorshDeserialize;
use of1_range_recorder::sha256;
use pump_protocol_v2::{
    decode::TradeEvent,
    registry::{EVENT_IX_TAG_LE, PUMP_PROGRAM_ID, TRADE_EVENT_DISCRIMINATOR},
};
use serde::Serialize;
use serde_json::{Value, json};
use solana_pubkey::Pubkey;
use std::{
    collections::BTreeSet,
    io::{self, Cursor},
    str::FromStr,
};

pub const SOURCE: &[u8] = include_bytes!("../sources/pump-sell-evidence.json");
pub const CANDIDATE: &str = "pump-sell-9c82f61-token2022-cashback17-direct-v1";
pub const SELL_DISCRIMINATOR: [u8; 8] = [51, 230, 133, 164, 1, 127, 131, 173];
const TOKEN_2022: &str = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb";
const ATA: &str = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL";
const FEE: &str = "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum Rejection {
    TruncatedInstruction,
    WrongDiscriminator,
    TrailingInstruction,
    InvalidEvent,
    TrailingEvent,
    UnsupportedEventVariant,
    MissingCpi,
    MissingEvent,
    AmbiguousEvent,
    UnsupportedInvocation,
    EventAuthorityMismatch,
    AccountMismatch,
    EventCorrelationMismatch,
    TransactionNotSuccessful,
    UnsupportedPattern,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SellInstruction {
    pub amount: u64,
    pub min_sol_output: u64,
}

/// Exact pinned sell bytes; no prefix success or optional trailing byte.
/// # Errors
/// Rejects any wrong discriminator, truncated payload or trailing bytes.
pub fn decode_instruction(data: &[u8]) -> Result<SellInstruction, Rejection> {
    if data.len() < 8 {
        return Err(Rejection::TruncatedInstruction);
    }
    if data[..8] != SELL_DISCRIMINATOR {
        return Err(Rejection::WrongDiscriminator);
    }
    if data.len() < 24 {
        return Err(Rejection::TruncatedInstruction);
    }
    if data.len() != 24 {
        return Err(Rejection::TrailingInstruction);
    }
    Ok(SellInstruction {
        amount: u64::from_le_bytes(
            data[8..16]
                .try_into()
                .map_err(|_| Rejection::TruncatedInstruction)?,
        ),
        min_sol_output: u64::from_le_bytes(
            data[16..24]
                .try_into()
                .map_err(|_| Rejection::TruncatedInstruction)?,
        ),
    })
}

/// Reuse the exact bounded B3 `TradeEvent` wire type, not its buy-only admission.
/// No changes to the B3 parser, source, registry or buy behavior.
/// # Errors
/// Validates both discriminators, full bounded Borsh, exact exhaustion and sell.
pub fn decode_event(data: &[u8]) -> Result<TradeEvent, Rejection> {
    if data.len() < 16
        || data.len() > 4096
        || data[..8] != EVENT_IX_TAG_LE
        || data[8..16] != TRADE_EVENT_DISCRIMINATOR
    {
        return Err(Rejection::InvalidEvent);
    }
    let mut reader = Cursor::new(&data[16..]);
    let event = TradeEvent::deserialize_reader(&mut reader).map_err(|_| Rejection::InvalidEvent)?;
    if reader.position() != (data.len() - 16) as u64 {
        return Err(Rejection::TrailingEvent);
    }
    if event.is_buy || event.ix_name != "sell" {
        return Err(Rejection::UnsupportedEventVariant);
    }
    Ok(event)
}

fn associated_event(tx: &Value, ix: &Value) -> Result<(Value, TradeEvent), Rejection> {
    let top = tx["instructions"]
        .as_array()
        .ok_or(Rejection::UnsupportedInvocation)?;
    let inner = tx["inner_instructions"]
        .as_array()
        .ok_or(Rejection::MissingCpi)?;
    let mut events = Vec::new();
    for (position, v) in inner
        .iter()
        .enumerate()
        .filter(|(_, v)| v["outer_index"] == ix["index"] && v["program_id"] == PUMP_PROGRAM_ID)
    {
        let data = hex::decode(v["data_hex"].as_str().ok_or(Rejection::InvalidEvent)?)
            .map_err(|_| Rejection::InvalidEvent)?;
        if data.starts_with(&EVENT_IX_TAG_LE) {
            events.push((position, v, data));
        }
    }
    if events.is_empty() {
        return Err(Rejection::MissingEvent);
    }
    if events.len() != 1 {
        return Err(Rejection::AmbiguousEvent);
    }
    let (position, v, bytes) = &events[0];
    let parent = pump::parent(top, &inner[..*position], v);
    if v["stack_height"] != 2
        || parent["program_id"] != PUMP_PROGRAM_ID
        || !parent["inner_order"].is_null()
    {
        return Err(Rejection::UnsupportedInvocation);
    }
    let event = decode_event(bytes)?;
    let authority = account_key(tx, ix, 10).ok_or(Rejection::EventAuthorityMismatch)?;
    let accounts = v["account_indexes"]
        .as_array()
        .ok_or(Rejection::EventAuthorityMismatch)?;
    if accounts.len() != 1 || accounts[0].as_u64().and_then(|i| key_at(tx, i)) != Some(authority) {
        return Err(Rejection::EventAuthorityMismatch);
    }
    if v["program_id_index"]
        .as_u64()
        .and_then(|i| key_at(tx, i))
        .map(|p| p.to_string())
        .as_deref()
        != Some(PUMP_PROGRAM_ID)
    {
        return Err(Rejection::UnsupportedInvocation);
    }
    Ok((
        json!({"outer_index":v["outer_index"],"inner_order":v["inner_order"],"stack_height":v["stack_height"],"parent":parent,
        "event_cpi_sha256":sha256(bytes),"event_cpi_hex":hex::encode(bytes),"event_cpi_bytes":bytes.len(),"event_authority":authority.to_string(),
        "association":"ONE_RECORDED_DIRECT_EVENT_CPI_WITH_EXACT_EXHAUSTION"}),
        event,
    ))
}

fn expected_pda(tx: &Value, ix: &Value, pos: usize, event: &TradeEvent) -> Option<(Pubkey, u8)> {
    let mint = account_key(tx, ix, 2)?;
    let user = account_key(tx, ix, 6)?;
    let token = account_key(tx, ix, 9)?;
    let curve = account_key(tx, ix, 3)?;
    match pos {
        0 => pda(PUMP_PROGRAM_ID, &[b"global"]),
        3 => pda(PUMP_PROGRAM_ID, &[b"bonding-curve", mint.as_ref()]),
        4 => pda(ATA, &[curve.as_ref(), token.as_ref(), mint.as_ref()]),
        5 => pda(ATA, &[user.as_ref(), token.as_ref(), mint.as_ref()]),
        8 => pda(PUMP_PROGRAM_ID, &[b"creator-vault", &event.creator]),
        10 => pda(PUMP_PROGRAM_ID, &[b"__event_authority"]),
        12 => pda(
            FEE,
            &[
                b"fee_config",
                Pubkey::from_str(PUMP_PROGRAM_ID).ok()?.as_ref(),
            ],
        ),
        14 => pda(
            PUMP_PROGRAM_ID,
            &[b"user_volume_accumulator", user.as_ref()],
        ),
        15 => pda(PUMP_PROGRAM_ID, &[b"bonding-curve-v2", mint.as_ref()]),
        _ => None,
    }
}

fn accounts(tx: &Value, ix: &Value, event: &TradeEvent, source: &Value) -> Vec<Value> {
    source["account_rules"].as_array().into_iter().flatten().enumerate().map(|(pos,rule)| {
        let key=account_key(tx,ix,pos); let derived=expected_pda(tx,ix,pos,event);
        let expected=derived.map(|(p,_)|p.to_string()).or_else(||match pos {
            1=>Some(bs58::encode(event.fee_recipient).into_string()),
            2=>Some(bs58::encode(event.mint).into_string()),
            6=>Some(bs58::encode(event.user).into_string()),
            9=>Some(TOKEN_2022.into()),
            _=>rule["fixed_address"].as_str().map(str::to_owned),
        });
        let observed=key.map(|k|k.to_string());
        let listed=|list:&str| observed.as_ref().is_some_and(|k|source[list].as_array().is_some_and(|a|a.iter().any(|v|v==k)));
        let address_match=if pos==16 {listed("buyback_fee_recipients")} else {observed.is_some() && observed==expected};
        let index=ix["account_indexes"][pos].as_u64().and_then(|v|usize::try_from(v).ok());
        let flags=index.and_then(|i|privileges(tx,i));
        let valid_flags=flags.is_some_and(|(s,w)|(rule["signer"]!=true||s)&&(rule["writable"]!=true||w));
        json!({"position":pos,"role":rule["name"],"compiled_key_index":index,"observed":observed,"expected":expected,"pda_bump":derived.map(|(_,b)|b),
            "address_match":address_match,"message_signer":flags.map(|(s,_)|s),"message_writable":flags.map(|(_,w)|w),"required_signer":rule["signer"],"required_writable":rule["writable"],
            "required_privileges_match":valid_flags,"documented_fee_recipient":if pos==1{Some(listed("normal_fee_recipients"))}else{None},
            "basis":match pos {1|2|6=>"EVENT_FIELD_CORRESPONDENCE",5=>"SDK_ATA_CORRESPONDENCE_NOT_ACCOUNT_CONTENT",8=>"PDA_FROM_EVENT_CREATOR_NOT_CURVE_ACCOUNT",14|15=>"OFFICIAL_REMAINING_ACCOUNT_PDA",16=>"OFFICIAL_BUYBACK_RECIPIENT_LIST",9=>"SELECTED_TOKEN_2022_PROGRAM",_=>"PINNED_IDL_ADDRESS_OR_PDA"}})
    }).collect()
}

fn event_fields(e: &TradeEvent) -> Value {
    // All numeric wire quantities are decimal strings, including zero. Unknown
    // identity/decimals remain null, distinct from the observed zero mint bytes.
    json!({"mint_address":bs58::encode(e.mint).into_string(),"user_address":bs58::encode(e.user).into_string(),"is_buy":e.is_buy,"ix_name":e.ix_name,
        "sol_amount_raw_u64":e.sol_amount.to_string(),"token_amount_raw_u64":e.token_amount.to_string(),"timestamp_raw_i64":e.timestamp.to_string(),
        "virtual_sol_reserves_raw_u64":e.virtual_sol_reserves.to_string(),"virtual_token_reserves_raw_u64":e.virtual_token_reserves.to_string(),
        "real_sol_reserves_raw_u64":e.real_sol_reserves.to_string(),"real_token_reserves_raw_u64":e.real_token_reserves.to_string(),
        "fee_recipient":bs58::encode(e.fee_recipient).into_string(),"fee_basis_points_raw_u64":e.fee_basis_points.to_string(),"fee_raw_u64":e.fee.to_string(),
        "creator_address_reported":bs58::encode(e.creator).into_string(),"creator_fee_basis_points_raw_u64":e.creator_fee_basis_points.to_string(),"creator_fee_raw_u64":e.creator_fee.to_string(),
        "track_volume":e.track_volume,"total_unclaimed_tokens_raw_u64":e.total_unclaimed_tokens.to_string(),"total_claimed_tokens_raw_u64":e.total_claimed_tokens.to_string(),
        "current_sol_volume_raw_u64":e.current_sol_volume.to_string(),"last_update_timestamp_raw_i64":e.last_update_timestamp.to_string(),"mayhem_mode":e.mayhem_mode,
        "cashback_fee_basis_points_raw_u64":e.cashback_fee_basis_points.to_string(),"cashback_raw_u64":e.cashback.to_string(),
        "buyback_fee_basis_points_raw_u64":e.buyback_fee_basis_points.to_string(),"buyback_fee_raw_u64":e.buyback_fee.to_string(),
        "shareholders":e.shareholders.iter().map(|s|json!({"address":bs58::encode(s.address).into_string(),"share_bps_raw_u16":s.share_bps.to_string()})).collect::<Vec<_>>(),
        "quote_mint_raw_hex":hex::encode(e.quote_mint),"quote_amount_raw_u64":e.quote_amount.to_string(),"virtual_quote_reserves_raw_u64":e.virtual_quote_reserves.to_string(),"real_quote_reserves_raw_u64":e.real_quote_reserves.to_string(),
        "field_role":"EVENT_REPORTED_NOT_ACCOUNT_WRITE_STATE","fee_role":"NAMED_EVENT_FIELDS_NOT_ADDITIVE_NET_PROCEEDS_OR_RECIPIENT_BALANCE_DELTAS"})
}

fn diagnose(tx: &Value, ix: &Value, data: &[u8], direct: bool, source: &Value) -> Value {
    let instruction = decode_instruction(data);
    let context = if direct {
        associated_event(tx, ix)
    } else {
        Err(Rejection::UnsupportedInvocation)
    };
    let mut rows = Vec::new();
    let mut predicates = serde_json::Map::new();
    let mut reason = instruction
        .as_ref()
        .err()
        .copied()
        .or_else(|| context.as_ref().err().copied());
    if let (Ok(i), Ok((_, e))) = (&instruction, &context) {
        rows = accounts(tx, ix, e, source);
        let distinct = rows
            .iter()
            .filter_map(|r| r["observed"].as_str())
            .collect::<BTreeSet<_>>()
            .len()
            == 17;
        let account_match = ix["account_indexes"]
            .as_array()
            .is_some_and(|a| a.len() == 17)
            && rows.len() == 17
            && distinct
            && rows
                .iter()
                .all(|r| r["address_match"] == true && r["required_privileges_match"] == true)
            && rows[1]["documented_fee_recipient"] == true;
        let correlation = account_key(tx, ix, 2).is_some_and(|p| p.as_ref() == e.mint)
            && account_key(tx, ix, 6).is_some_and(|p| p.as_ref() == e.user)
            && i.amount == e.token_amount;
        let pattern = !e.mayhem_mode && e.shareholders.is_empty() && !e.track_volume;
        let direct_program = ix["index"].is_u64()
            && ix["program_id_index"]
                .as_u64()
                .and_then(|i| key_at(tx, i))
                .map(|p| p.to_string())
                .as_deref()
                == Some(PUMP_PROGRAM_ID);
        for (key, passed) in [
            ("exact_instruction_and_event", true),
            ("single_direct_cpi_context", direct_program),
            ("all_17_accounts", account_match),
            ("mint_user_amount_correspondence", correlation),
            ("selected_nonmayhem_cashback_pattern", pattern),
            (
                "recorded_transaction_ok",
                tx["status"] == "OK" && tx["transaction_error"].is_null(),
            ),
        ] {
            predicates.insert(key.into(), json!(passed));
        }
        reason = if !direct_program {
            Some(Rejection::UnsupportedInvocation)
        } else if !account_match {
            Some(Rejection::AccountMismatch)
        } else if !correlation {
            Some(Rejection::EventCorrelationMismatch)
        } else if !pattern {
            Some(Rejection::UnsupportedPattern)
        } else if predicates["recorded_transaction_ok"] != true {
            Some(Rejection::TransactionNotSuccessful)
        } else {
            None
        };
    }
    // Evaluate the bounded registered profile from actual observation predicates.
    // No caller count, no claim that all historical deployed versions were searched.
    let matching: Vec<_> = [CANDIDATE]
        .into_iter()
        .filter(|_| !predicates.is_empty() && predicates.values().all(|v| v == true))
        .collect();
    json!({"schema":"PUMP_SELL_OBSERVATION_1","source_evidence_sha256":sha256(SOURCE),"outer_index":if direct{ix["index"].clone()}else{ix["outer_index"].clone()},"instruction_inner_order":ix["inner_order"],
        "instruction_data_hex":hex::encode(data),"instruction_sha256":sha256(data),"instruction_bytes":data.len(),
        "instruction":instruction.as_ref().ok().map(|i|json!({"amount_raw_u64":i.amount.to_string(),"min_sol_output_raw_u64":i.min_sol_output.to_string()})),
        "accounts":rows,"account_count":ix["account_indexes"].as_array().map(Vec::len),"event_context":context.as_ref().ok().map(|(v,_)|v),
        "event_reported":context.as_ref().ok().map(|(_,e)|event_fields(e)),"candidate_predicates":predicates,"matching_registered_candidates":matching,
        "selected_candidate":if matching.len()==1{Some(matching[0])}else{None},"candidate_scope":"UNIQUENESS_WITHIN_ONE_REGISTERED_PROFILE_NOT_GLOBAL_HISTORICAL_ACTIVATION",
        "disposition":if reason.is_none()&&matching.len()==1{"MATCHED_RECORDED_EVENT_FACTS"}else{"NOT_ADMITTED"},"reason":reason,
        "transaction_status":tx["status"],"committed_account_state":"NOT_ESTABLISHED","historical_activation":"UNKNOWN","economic_identity":"UNKNOWN",
        "quote_decimals":null,"base_decimals":null,"name":null,"ticker":null,"launch_at":null,"executable_price":null})
}

/// Actual direct and nested sell occurrences each keep an explicit outcome.
/// # Errors
/// Rejects broken internal source/data JSON; no network or caller candidate count.
pub fn inspect(tx: &Value) -> io::Result<Vec<Value>> {
    let source: Value = serde_json::from_slice(SOURCE).map_err(invalid)?;
    let mut result = Vec::new();
    for (field, direct) in [("instructions", true), ("inner_instructions", false)] {
        if let Some(ixs) = tx[field].as_array() {
            for ix in ixs.iter().filter(|v| v["program_id"] == PUMP_PROGRAM_ID) {
                let data = hex::decode(
                    ix["data_hex"]
                        .as_str()
                        .ok_or_else(|| invalid("SELL_DATA"))?,
                )
                .map_err(invalid)?;
                if data.starts_with(&SELL_DISCRIMINATOR) {
                    result.push(diagnose(tx, ix, &data, direct, &source));
                }
            }
        }
    }
    Ok(result)
}

/// Project matched observations only from a complete receipt-verified Bronze
/// package. The run reader is the provenance trust boundary, not this JSON API.
/// # Errors
/// Rejects missing source binding or a diagnosis that cannot be reproduced.
pub fn facts(record: &Value) -> io::Result<Vec<Value>> {
    if record["disposition"] != "DECODED" {
        return Ok(Vec::new());
    }
    let tx = &record["transaction"];
    let diagnostics = inspect(tx)?;
    let matched = diagnostics
        .iter()
        .filter(|d| d["disposition"] == "MATCHED_RECORDED_EVENT_FACTS");
    let mut out = Vec::new();
    for d in matched {
        if record["atomic_observation_package"] != true
            || !record["source"]["raw_sha256"].is_string()
            || !record["source"]["bindings"].is_object()
            || !record["source"]["transaction_node_cid_hex"].is_string()
            || tx["signatures"][0].is_null()
        {
            return Err(invalid("SILVER_REQUIRES_BOUND_ATOMIC_BRONZE"));
        }
        let mut fact = json!({"schema":"PUMP_SILVER_RECORDED_SELL_1","record_kind":"INSTRUCTION_AND_RECORDED_TRADE_EVENT","candidate_id":CANDIDATE,"candidate_evidence":"OBSERVED_LAYOUT_CONTEXT_COMPATIBLE",
            "source_evidence_sha256":sha256(SOURCE),"decoder_source_sha256":record["decoder_source_sha256"],"bronze_record_sha256":sha256(&serde_json::to_vec(record).map_err(invalid)?),
            "source":record["source"],"effective_at":record["effective_at"],"signatures":tx["signatures"],"wire_sha256":tx["wire_sha256"],"metadata_sha256":tx["metadata_sha256"],
            "transaction_status":tx["status"],"transaction_fee_lamports":tx["fee_lamports"],"atomic_observation_package":true,"instruction":d["instruction"],
            "instruction_data_hex":d["instruction_data_hex"],"instruction_sha256":d["instruction_sha256"],"event_context":d["event_context"],"accounts":d["accounts"],"event_reported":d["event_reported"],
            "matching_registered_candidates":d["matching_registered_candidates"],"candidate_predicates":d["candidate_predicates"]});
        let limits = json!({"input_kind":record["input_kind"],"receipt_evidence":record["receipt_evidence"],"slice_class":"ENGINEERING_VALIDATION_ONLY","root_to_slot_membership":"UNAVAILABLE",
            "observed_at":null,"actionable_at":null,"execution_opportunity_at":null,"observation_model_id":null,"account_contents_verified":false,"signature_crypto_verification":"NOT_PERFORMED",
            "committed_account_state":"NOT_ESTABLISHED","historical_activation":"UNKNOWN","quote_mint_identity":"UNKNOWN","quote_decimals":null,"base_decimals":null,
            "name":null,"ticker":null,"launch_at":null,"executable_price":null,"net_proceeds":null,"research_ready":false});
        fact.as_object_mut()
            .ok_or_else(|| invalid("SILVER_OBJECT"))?
            .extend(
                limits
                    .as_object()
                    .ok_or_else(|| invalid("SILVER_LIMITS"))?
                    .clone(),
            );
        out.push(fact);
    }
    Ok(out)
}
