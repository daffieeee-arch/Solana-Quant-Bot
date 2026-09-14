//! Separate source-bound exact-quote v2 buy, not a relaxation of legacy buys.
//! All quantities are raw integers; recorded events never become account state.
use crate::{
    invalid,
    pump_buy::{account_key, pda, privileges},
    pump_sell,
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

#[path = "pump_buy_exact_quote_context.rs"]
mod context;
pub const SOURCE: &[u8] = include_bytes!("../sources/pump-buy-exact-quote-v2-evidence.json");
pub const CANDIDATE: &str = "pump-buy-exact-quote-v2-9c82f61-token2022-native-sol-direct27-v1";
pub const DISCRIMINATOR: [u8; 8] = [194, 171, 28, 70, 104, 77, 91, 47];
const ATA: &str = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL";
const FEE: &str = "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ";
const TOKEN2022: &str = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb";
const TOKEN: &str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
const WSOL: &str = "So11111111111111111111111111111111111111112";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum Rejection {
    TruncatedInstruction,
    WrongDiscriminator,
    TrailingInstruction,
    InvalidEvent,
    TrailingEvent,
    UnsupportedEventVariant,
    UnsupportedInvocation,
    MissingCpi,
    MissingEvent,
    AmbiguousEvent,
    InvalidInvocationOrder,
    InvalidStackHeight,
    EventAuthorityMismatch,
    AccountMismatch,
    EventCorrelationMismatch,
    UnsupportedPattern,
    TransactionNotSuccessful,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Instruction {
    pub spendable_quote_in: u64,
    pub min_tokens_out: u64,
}

/// Exact two-u64 v2 instruction. No optional bool, prefix success or padding.
/// # Errors
/// Rejects wrong discriminator, truncation and every trailing byte.
pub fn decode_instruction(data: &[u8]) -> Result<Instruction, Rejection> {
    if data.len() < 8 {
        return Err(Rejection::TruncatedInstruction);
    }
    if data[..8] != DISCRIMINATOR {
        return Err(Rejection::WrongDiscriminator);
    }
    if data.len() < 24 {
        return Err(Rejection::TruncatedInstruction);
    }
    if data.len() != 24 {
        return Err(Rejection::TrailingInstruction);
    }
    Ok(Instruction {
        spendable_quote_in: u64::from_le_bytes(
            data[8..16]
                .try_into()
                .map_err(|_| Rejection::TruncatedInstruction)?,
        ),
        min_tokens_out: u64::from_le_bytes(
            data[16..24]
                .try_into()
                .map_err(|_| Rejection::TruncatedInstruction)?,
        ),
    })
}

/// The pinned `TradeEvent` type, but the separate observed exact-quote label.
/// # Errors
/// Requires both discriminators, bounded Borsh and full input exhaustion.
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
    if !event.is_buy || event.ix_name != "buy_exact_quote_in" {
        return Err(Rejection::UnsupportedEventVariant);
    }
    Ok(event)
}

fn expected_pda(
    tx: &Value,
    ix: &Value,
    pos: usize,
    event: Option<&TradeEvent>,
) -> Option<(Pubkey, u8)> {
    let key = |p| account_key(tx, ix, p);
    let ata = |owner, token, mint| {
        pda(
            ATA,
            &[
                key(owner)?.as_ref(),
                key(token)?.as_ref(),
                key(mint)?.as_ref(),
            ],
        )
    };
    match pos {
        0 => pda(PUMP_PROGRAM_ID, &[b"global"]),
        7 => ata(6, 4, 2),
        9 => ata(8, 4, 2),
        10 => pda(PUMP_PROGRAM_ID, &[b"bonding-curve", key(1)?.as_ref()]),
        11 => ata(10, 3, 1),
        12 => ata(10, 4, 2),
        14 => ata(13, 3, 1),
        15 => ata(13, 4, 2),
        16 => pda(PUMP_PROGRAM_ID, &[b"creator-vault", &event?.creator]),
        17 => ata(16, 4, 2),
        18 => pda(FEE, &[b"sharing-config", key(1)?.as_ref()]),
        19 => pda(PUMP_PROGRAM_ID, &[b"global_volume_accumulator"]),
        20 => pda(
            PUMP_PROGRAM_ID,
            &[b"user_volume_accumulator", key(13)?.as_ref()],
        ),
        21 => ata(20, 4, 2),
        22 => pda(
            FEE,
            &[
                b"fee_config",
                Pubkey::from_str(PUMP_PROGRAM_ID).ok()?.as_ref(),
            ],
        ),
        25 => pda(PUMP_PROGRAM_ID, &[b"__event_authority"]),
        _ => None,
    }
}

fn account_rows(tx: &Value, ix: &Value, event: Option<&TradeEvent>, source: &Value) -> Vec<Value> {
    source["account_rules"].as_array().into_iter().flatten().enumerate().map(|(pos,rule)|{
        let observed=account_key(tx,ix,pos).map(|p|p.to_string());
        let derived=expected_pda(tx,ix,pos,event);
        let expected=derived.map(|(p,_)|p.to_string()).or_else(||match pos{
            1=>event.map(|e|bs58::encode(e.mint).into_string()),2=>Some(WSOL.into()),3=>Some(TOKEN2022.into()),4=>Some(TOKEN.into()),
            6=>event.map(|e|bs58::encode(e.fee_recipient).into_string()),13=>event.map(|e|bs58::encode(e.user).into_string()),_=>rule["fixed_address"].as_str().map(str::to_owned)});
        let listed=|list:&str|observed.as_ref().map(|o|source[list].as_array().is_some_and(|a|a.iter().any(|v|v==o)));
        let address_match=if pos==8{listed("buyback_fee_recipients")}else{observed.as_ref().zip(expected.as_ref()).map(|(o,e)|o==e)};
        let index=ix["account_indexes"].as_array().and_then(|a|a.get(pos)).and_then(Value::as_u64).and_then(|v|usize::try_from(v).ok());
        let flags=index.and_then(|i|privileges(tx,i));let required=flags.map(|(s,w)|(rule["signer"]!=true||s)&&(rule["writable"]!=true||w));
        json!({"position":pos,"role":rule["name"],"compiled_key_index":index,"observed":observed,"expected":expected,"pda_bump":derived.map(|(_,b)|b),"address_match":address_match,
            "required_signer":rule["signer"],"required_writable":rule["writable"],"message_signer":flags.map(|(s,_)|s),"message_writable":flags.map(|(_,w)|w),"required_privileges_match":required,
        "source":"PINNED_V2_27_ACCOUNT_IDL_WITH_EXPLICIT_PROFILE_CONSTRAINTS","address_basis":match pos{1|6|13=>"RECORDED_EVENT_CORRESPONDENCE",2=>"DOCUMENTED_NATIVE_SOL_INTERFACE_WSOL_ACCOUNT",3|4=>"SELECTED_TOKEN_PROGRAM_PROFILE_NOT_MINT_OWNER_PROOF",8=>"DOCUMENTED_BUYBACK_RECIPIENT",14=>"BOUNDED_USER_BASE_ATA_CORRESPONDENCE_NOT_ACCOUNT_CONTENT",16=>"PDA_FROM_EVENT_CREATOR_NOT_CURVE_ACCOUNT_CONTENT",_=>"PINNED_IDL_PDA_OR_FIXED_ADDRESS"},"account_contents_verified":false,
            "documented_fee_recipient":if pos==6{event.and_then(|e|listed(if e.mayhem_mode{"mayhem_fee_recipients"}else{"normal_fee_recipients"}))}else{None}})
    }).collect()
}

fn diagnose(tx: &Value, ix: &Value, data: &[u8], source: &Value) -> Value {
    let instruction = decode_instruction(data);
    let context = context::associated_event(tx, ix);
    let event = context.as_ref().ok().map(|(_, e)| e);
    let rows = account_rows(tx, ix, event, source);
    let accounts_match = ix["account_indexes"]
        .as_array()
        .is_some_and(|a| a.len() == 27)
        && rows.len() == 27
        && rows
            .iter()
            .all(|r| r["address_match"] == true && r["required_privileges_match"] == true)
        && rows[6]["documented_fee_recipient"] == true
        && rows
            .iter()
            .filter_map(|r| r["observed"].as_str())
            .collect::<BTreeSet<_>>()
            .len()
            == 27;
    let correlated = instruction.as_ref().ok().zip(event).is_some_and(|(i, e)| {
        account_key(tx, ix, 1).is_some_and(|p| p.as_ref() == e.mint)
            && account_key(tx, ix, 13).is_some_and(|p| p.as_ref() == e.user)
            && e.token_amount >= i.min_tokens_out
            && e.sol_amount <= i.spendable_quote_in
    });
    let pattern = event.is_some_and(|e| {
        e.quote_mint == [0; 32]
            && e.quote_amount == e.sol_amount
            && e.virtual_quote_reserves == e.virtual_sol_reserves
            && e.real_quote_reserves == e.real_sol_reserves
            && e.shareholders.is_empty()
            && !e.track_volume
    });
    let status = tx["status"] == "OK" && tx["transaction_error"].is_null();
    let predicates = json!({"full_instruction_exhaustion":instruction.is_ok(),"own_direct_event_context":context.is_ok(),"all_27_accounts_and_direct_message_privileges":accounts_match,"mint_user_argument_event_correspondence":correlated,"selected_native_sol_event_representation":pattern,"recorded_transaction_ok":status});
    let reason = instruction
        .as_ref()
        .err()
        .copied()
        .or_else(|| context.as_ref().err().copied())
        .or({
            if !accounts_match {
                Some(Rejection::AccountMismatch)
            } else if !correlated {
                Some(Rejection::EventCorrelationMismatch)
            } else if !pattern {
                Some(Rejection::UnsupportedPattern)
            } else if !status {
                Some(Rejection::TransactionNotSuccessful)
            } else {
                None
            }
        });
    let matching: Vec<_> = [CANDIDATE]
        .into_iter()
        .filter(|_| {
            predicates
                .as_object()
                .is_some_and(|p| p.values().all(|v| v == true))
        })
        .collect();
    json!({"schema":"PUMP_BUY_EXACT_QUOTE_V2_OBSERVATION_1","evaluated_profile":CANDIDATE,"source_evidence_sha256":sha256(SOURCE),"outer_index":ix["index"],"instruction_inner_order":null,
        "instruction_data_hex":hex::encode(data),"instruction_sha256":sha256(data),"instruction_bytes":data.len(),"instruction":instruction.as_ref().ok().map(|i|json!({"spendable_quote_in_raw_u64":i.spendable_quote_in.to_string(),"min_tokens_out_raw_u64":i.min_tokens_out.to_string()})),"track_volume_argument":"NOT_PART_OF_THIS_INSTRUCTION_SCHEMA",
        "accounts":rows,"account_count":ix["account_indexes"].as_array().map(Vec::len),"event_context":context.as_ref().ok().map(|(v,_)|v),"event_reported":event.map(pump_sell::event_fields),
        "candidate_predicates":predicates,"argument_event_bounds_basis":"OBSERVED_RAW_CONSISTENCY_NOT_HANDLER_NET_COST_OR_PRICE_PROOF","matching_registered_candidates":matching,"selected_candidate":if matching.len()==1{Some(matching[0])}else{None},"candidate_scope":"UNIQUENESS_WITHIN_ONE_REGISTERED_PROFILE_NOT_GLOBAL_HISTORICAL_ACTIVATION",
        "disposition":if reason.is_none()&&matching.len()==1{"MATCHED_RECORDED_EVENT_FACTS"}else{"NOT_ADMITTED"},"reason":reason,"transaction_status":tx["status"],"committed_account_state":"NOT_ESTABLISHED","historical_activation":"UNKNOWN",
        "quote_account_address":account_key(tx,ix,2).map(|p|p.to_string()),"quote_account_role":"PINNED_NATIVE_SOL_INTERFACE_ACCOUNT_NOT_EVENT_MINT_SUBSTITUTION","quote_decimals":null,"base_decimals":null,"research_ready":false})
}

/// Only actual top-level occurrences of this exact instruction discriminator.
/// # Errors
/// Rejects malformed internal source JSON or instruction bytes; no network.
pub fn inspect(tx: &Value) -> io::Result<Vec<Value>> {
    let source: Value = serde_json::from_slice(SOURCE).map_err(invalid)?;
    let mut out = Vec::new();
    if let Some(ixs) = tx["instructions"].as_array() {
        for ix in ixs.iter().filter(|v| v["program_id"] == PUMP_PROGRAM_ID) {
            let data = hex::decode(
                ix["data_hex"]
                    .as_str()
                    .ok_or_else(|| invalid("BUY_EXACT_QUOTE_V2_DATA"))?,
            )
            .map_err(invalid)?;
            if data.starts_with(&DISCRIMINATOR) {
                out.push(diagnose(tx, ix, &data, &source));
            }
        }
    }
    Ok(out)
}

/// Materialize only reproduced observations from receipt-verified atomic Bronze.
/// The archival run reader, not this JSON entrypoint, verifies external source bytes.
/// # Errors
/// Requires provenance and native sample identity; never accepts a caller count.
pub fn facts(record: &Value) -> io::Result<Vec<Value>> {
    if record["disposition"] != "DECODED" {
        return Ok(Vec::new());
    }
    let tx = &record["transaction"];
    let diagnostics = inspect(tx)?;
    let mut out = Vec::new();
    for d in diagnostics
        .iter()
        .filter(|d| d["disposition"] == "MATCHED_RECORDED_EVENT_FACTS")
    {
        if record["atomic_observation_package"] != true
            || !record["source"]["raw_sha256"].is_string()
            || !record["source"]["bindings"].is_object()
            || !record["source"]["transaction_node_cid_hex"].is_string()
            || tx["signatures"][0].is_null()
        {
            return Err(invalid("SILVER_BUY_REQUIRES_BOUND_ATOMIC_BRONZE"));
        }
        let mut fact = json!({"schema":"PUMP_SILVER_RECORDED_BUY_EXACT_QUOTE_V2_1","record_kind":"INSTRUCTION_AND_RECORDED_TRADE_EVENT","candidate_id":d["selected_candidate"],"candidate_evidence":"OBSERVED_LAYOUT_CONTEXT_COMPATIBLE",
            "source_evidence_sha256":d["source_evidence_sha256"],"decoder_source_sha256":record["decoder_source_sha256"],"bronze_record_sha256":sha256(&serde_json::to_vec(record).map_err(invalid)?),
            "source":record["source"],"effective_at":record["effective_at"],"signatures":tx["signatures"],"wire_sha256":tx["wire_sha256"],"metadata_sha256":tx["metadata_sha256"],"transaction_status":tx["status"],"transaction_fee_lamports":tx["fee_lamports"],"atomic_observation_package":true,
            "instruction":d["instruction"],"instruction_data_hex":d["instruction_data_hex"],"instruction_sha256":d["instruction_sha256"],"event_context":d["event_context"],"accounts":d["accounts"],"event_reported":d["event_reported"],"candidate_predicates":d["candidate_predicates"],"matching_registered_candidates":d["matching_registered_candidates"],
            "quote_account_address":d["quote_account_address"],"quote_account_role":d["quote_account_role"],"argument_event_bounds_basis":d["argument_event_bounds_basis"]});
        let limits = json!({"input_kind":record["input_kind"],"receipt_evidence":record["receipt_evidence"],"slice_class":"ENGINEERING_VALIDATION_ONLY","root_to_slot_membership":"UNAVAILABLE","observed_at":null,"actionable_at":null,"execution_opportunity_at":null,"observation_model_id":null,
            "account_contents_verified":false,"signature_crypto_verification":"NOT_PERFORMED","committed_account_state":"NOT_ESTABLISHED","historical_activation":"UNKNOWN","quote_mint_identity":"UNKNOWN","quote_decimals":null,"base_decimals":null,
            "name":null,"ticker":null,"launch_at":null,"executable_price":null,"net_proceeds":null,"research_ready":false});
        fact.as_object_mut()
            .ok_or_else(|| invalid("SILVER_BUY_OBJECT"))?
            .extend(
                limits
                    .as_object()
                    .ok_or_else(|| invalid("SILVER_BUY_LIMITS"))?
                    .clone(),
            );
        if let Some(sample) = record.get("sample_identity") {
            let parsed: of1_range_recorder::sample::SampleIdentity =
                serde_json::from_value(sample.clone()).map_err(invalid)?;
            parsed.validate(parsed.epoch).map_err(invalid)?;
            let slot = record["effective_at"]["slot"]
                .as_str()
                .and_then(|s| s.parse::<u64>().ok())
                .ok_or_else(|| invalid("SILVER_BUY_SAMPLE_SLOT_MISSING"))?;
            if sample != &record["source"]["bindings"]["sample_identity"]
                || sample["sample_class"] != record["slice_class"]
                || !(parsed.start_slot..parsed.end_slot_exclusive).contains(&slot)
            {
                return Err(invalid("SILVER_BUY_SAMPLE_BINDING_MISMATCH"));
            }
            fact["sample_identity"] = sample.clone();
            fact["slice_class"] = record["slice_class"].clone();
        } else if record
            .get("slice_class")
            .is_some_and(|v| v != "ENGINEERING_VALIDATION_ONLY")
        {
            return Err(invalid("SILVER_BUY_SAMPLE_BINDING_MISSING"));
        }
        out.push(fact);
    }
    Ok(out)
}
