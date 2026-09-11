//! Source-bound diagnostics for one buy account pattern, NOT a Silver decoder.
//! Full instruction exhaustion remains mandatory. A separately labelled prefix
//! interpretation explains known offsets without accepting or discarding a suffix.
use crate::{invalid, pump};
use of1_range_recorder::sha256;
use pump_protocol_v2::{
    decode::{
        BuyInstruction, TradeEvent, probe_buy_instruction_layout, probe_trade_event_cpi_layout,
    },
    registry::{BUY_DISCRIMINATOR, EVENT_IX_TAG_LE, PUMP_PROGRAM_ID},
};
use serde_json::{Value, json};
use solana_pubkey::Pubkey;
use std::{collections::BTreeSet, io, str::FromStr};

pub const SOURCE: &[u8] = include_bytes!("../sources/pump-buy-evidence.json");
const PREFIX_BYTES: usize = 25;
const ATA: &str = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL";
const TOKEN: &str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
const FEE: &str = "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ";

/// Diagnose actual top-level buy bytes and recorded direct event CPI only.
/// No caller-supplied candidate count, alternative wire decoder or admission.
/// # Errors
/// Rejects corrupt internal source JSON or non-hex Bronze instruction bytes.
pub fn inspect(tx: &Value) -> io::Result<Vec<Value>> {
    let mut output = Vec::new();
    if let Some(top) = tx["instructions"].as_array() {
        for ix in top.iter().filter(|v| v["program_id"] == PUMP_PROGRAM_ID) {
            let bytes = hex::decode(ix["data_hex"].as_str().ok_or_else(|| invalid("BUY_DATA"))?)
                .map_err(invalid)?;
            if bytes.starts_with(&BUY_DISCRIMINATOR) {
                output.push(diagnose(tx, top, ix, &bytes)?);
            }
        }
    }
    Ok(output)
}

fn diagnose(tx: &Value, top: &[Value], ix: &Value, bytes: &[u8]) -> io::Result<Value> {
    let source: Value = serde_json::from_slice(SOURCE).map_err(invalid)?;
    // Both results remain separate. In particular the prefix is NEVER passed to
    // a candidate resolver or exported as an accepted full instruction.
    let full = probe_buy_instruction_layout(bytes);
    let prefix = bytes
        .get(..PREFIX_BYTES)
        .and_then(|b| probe_buy_instruction_layout(b).ok());
    let (event, event_failure) = match event_context(tx, top, ix) {
        Ok(e) => (Some(e), None),
        Err(reason) => (None, Some(reason)),
    };
    let rows = account_rows(tx, ix, event.as_ref().map(|(_, e)| e), &source);
    let correlations = correlations(ix, tx, prefix.as_ref(), event.as_ref().map(|(_, e)| e));
    let accounts_match = ix["account_indexes"]
        .as_array()
        .is_some_and(|a| a.len() == 18)
        && rows.len() == 18
        && rows
            .iter()
            .all(|r| r["address_match"] == true && r["required_privileges_match"] == true)
        && rows[1]["documented_fee_recipient"] == true;
    let distinct = rows
        .iter()
        .filter_map(|r| r["observed"].as_str())
        .collect::<BTreeSet<_>>()
        .len()
        == 18;
    let prefix_json = prefix.map(|p| json!({"classification":"PREFIX_DIAGNOSTIC_NOT_FULL_INSTRUCTION_DECODE","bytes_examined":PREFIX_BYTES,
        "amount_raw_u64":p.amount.to_string(),"max_sol_cost_raw_u64":p.max_sol_cost.to_string(),"track_volume":p.track_volume}));
    let complete = full.is_ok();
    let error = full
        .err()
        .map(|e| serde_json::to_value(e).map_err(invalid))
        .transpose()?;
    Ok(
        json!({"schema":"PUMP_BUY_DIAGNOSTIC_1","source_evidence_sha256":sha256(SOURCE),
        "outer_index":ix["index"],"full_data_hex":hex::encode(bytes),"full_data_sha256":sha256(bytes),"full_bytes":bytes.len(),
        "full_instruction_layout_match":complete,"full_instruction_error":error,"prefix_diagnostic":prefix_json,
        "unexplained_suffix":{"offset":PREFIX_BYTES,"hex":hex::encode(bytes.get(PREFIX_BYTES..).unwrap_or_default()),"semantics":"UNKNOWN","discarded":false},
        "account_pattern":"PINNED_IDL_16_PLUS_DOCUMENTED_2_REMAINING_ACCOUNTS","account_count":ix["account_indexes"].as_array().map(Vec::len),
        "accounts":rows,"selected_pattern_addresses_distinct":distinct,"account_address_correspondence":accounts_match && distinct,
        "account_contents_verified":false,"event_correlation":correlations,
        "event_context":event.as_ref().map(|(v,_)|v),"event_association_failure":event_failure,"transaction_status":tx["status"],
        "observation_predicates":{"full_instruction_exhaustion":complete,"documented_account_pattern":accounts_match && distinct,"single_direct_event_context":event.is_some()},
        "admission":"NOT_ADMITTED_DIAGNOSTIC_ONLY","silver":"NOT_PRODUCED","committed_state":"NOT_ESTABLISHED",
        "compatibility_rule_for_extra_bytes":"UNAVAILABLE","deployed_handler_identity":"UNAVAILABLE","historical_activation":"UNKNOWN",
        "quote_mint_identity":"UNKNOWN","quote_decimals":null,"base_decimals":null,"executable_price":null,
        "remaining_proof":"Pump-authoritative deployed decoding/compatibility rule explaining every byte; account-content and economic evidence remain separate gates"}),
    )
}

// Require exactly one source-tagged event in this outer instruction, exact B3
// event exhaustion, and a recorded direct Pump parent. No last-event-wins rule.
fn event_context(
    tx: &Value,
    top: &[Value],
    ix: &Value,
) -> Result<(Value, TradeEvent), &'static str> {
    let inner = tx["inner_instructions"].as_array().ok_or("MISSING_CPI")?;
    let tagged = inner
        .iter()
        .enumerate()
        .filter_map(|(position, v)| {
            if v["outer_index"] != ix["index"] || v["program_id"] != PUMP_PROGRAM_ID {
                return None;
            }
            let bytes = hex::decode(v["data_hex"].as_str()?).ok()?;
            bytes
                .starts_with(&EVENT_IX_TAG_LE)
                .then_some((position, v, bytes))
        })
        .collect::<Vec<_>>();
    if tagged.is_empty() {
        return Err("NO_RECORDED_TAGGED_EVENT");
    }
    if tagged.len() != 1 {
        return Err("AMBIGUOUS_RECORDED_EVENTS");
    }
    let (position, v, bytes) = &tagged[0];
    let parent = pump::parent(top, &inner[..*position], v);
    if v["stack_height"] != 2
        || parent["program_id"] != PUMP_PROGRAM_ID
        || !parent["inner_order"].is_null()
    {
        return Err("UNSUPPORTED_PARENT_CONTEXT");
    }
    let e = probe_trade_event_cpi_layout(bytes).map_err(|_| "UNSUPPORTED_EVENT_LAYOUT")?;
    let accounts = v["account_indexes"]
        .as_array()
        .ok_or("EVENT_AUTHORITY_UNAVAILABLE")?;
    let authority = account_key(tx, ix, 10).ok_or("EVENT_AUTHORITY_UNAVAILABLE")?;
    if accounts.len() != 1 || accounts[0].as_u64().and_then(|i| key_at(tx, i)) != Some(authority) {
        return Err("EVENT_AUTHORITY_MISMATCH");
    }
    Ok((
        json!({"basis":"RECORDED_DIRECT_CPI_PLUS_EXACT_EVENT_LAYOUT_NOT_COMMITTED_STATE","outer_index":v["outer_index"],"inner_order":v["inner_order"],
        "stack_height":v["stack_height"],"parent":parent,"event_sha256":sha256(bytes),"event_bytes":bytes.len(),"event_authority":authority.to_string(),
        "creator_bytes_base58":bs58::encode(e.creator).into_string(),"fee_recipient_bytes_base58":bs58::encode(e.fee_recipient).into_string(),
        "reserve_role":"EVENT_REPORTED_NOT_ACCOUNT_STATE"}),
        e,
    ))
}

fn key_at(tx: &Value, index: u64) -> Option<Pubkey> {
    let index = usize::try_from(index).ok()?;
    Pubkey::from_str(tx["account_keys"].as_array()?.get(index)?.as_str()?).ok()
}
fn account_key(tx: &Value, ix: &Value, position: usize) -> Option<Pubkey> {
    key_at(
        tx,
        ix["account_indexes"].as_array()?.get(position)?.as_u64()?,
    )
}
fn pda(program: &str, seeds: &[&[u8]]) -> Option<(Pubkey, u8)> {
    Pubkey::try_find_program_address(seeds, &Pubkey::from_str(program).ok()?)
}

// Flags are compiled-message requirements, not inferred account ownership or
// exact CPI privileges. An IDL readonly account may be globally writable.
fn privileges(tx: &Value, index: usize) -> Option<(bool, bool)> {
    let h = &tx["message_account_layout"];
    let n = |key| h[key].as_u64().and_then(|v| usize::try_from(v).ok());
    let (static_n, signed, ro_signed, ro_unsigned, loaded_w, loaded_r) = (
        n("static_keys")?,
        n("required_signatures")?,
        n("readonly_signed")?,
        n("readonly_unsigned")?,
        n("loaded_writable")?,
        n("loaded_readonly")?,
    );
    let keys = tx["account_keys"].as_array()?.len();
    if static_n > 256
        || loaded_w > 256
        || loaded_r > 256
        || signed > static_n
        || ro_signed > signed
        || ro_unsigned > static_n - signed
        || static_n + loaded_w + loaded_r != keys
        || index >= keys
    {
        return None;
    }
    let is_signer = index < signed;
    let writable = if is_signer {
        index < signed - ro_signed
    } else if index < static_n {
        index < static_n - ro_unsigned
    } else {
        index < static_n + loaded_w
    };
    Some((is_signer, writable))
}

fn expected_pda(
    tx: &Value,
    ix: &Value,
    position: usize,
    event: Option<&TradeEvent>,
) -> Option<(Pubkey, u8)> {
    let mint = account_key(tx, ix, 2)?;
    let user = account_key(tx, ix, 6)?;
    let token = account_key(tx, ix, 8)?;
    let curve = account_key(tx, ix, 3)?;
    match position {
        0 => pda(PUMP_PROGRAM_ID, &[b"global"]),
        3 => pda(PUMP_PROGRAM_ID, &[b"bonding-curve", mint.as_ref()]),
        4 => pda(ATA, &[curve.as_ref(), token.as_ref(), mint.as_ref()]),
        5 => pda(ATA, &[user.as_ref(), token.as_ref(), mint.as_ref()]),
        9 => pda(PUMP_PROGRAM_ID, &[b"creator-vault", &event?.creator]),
        10 => pda(PUMP_PROGRAM_ID, &[b"__event_authority"]),
        12 => pda(PUMP_PROGRAM_ID, &[b"global_volume_accumulator"]),
        13 => pda(
            PUMP_PROGRAM_ID,
            &[b"user_volume_accumulator", user.as_ref()],
        ),
        14 => pda(
            FEE,
            &[
                b"fee_config",
                Pubkey::from_str(PUMP_PROGRAM_ID).ok()?.as_ref(),
            ],
        ),
        16 => pda(PUMP_PROGRAM_ID, &[b"bonding-curve-v2", mint.as_ref()]),
        _ => None,
    }
}

fn account_rows(tx: &Value, ix: &Value, event: Option<&TradeEvent>, source: &Value) -> Vec<Value> {
    let Some(rules) = source["account_rules"].as_array() else {
        return Vec::new();
    };
    rules.iter().enumerate().map(|(position, rule)| {
        let index = ix["account_indexes"].as_array().and_then(|a| a.get(position)).and_then(Value::as_u64).and_then(|n|usize::try_from(n).ok());
        let observed = account_key(tx, ix, position).map(|p|p.to_string());
        let derived = expected_pda(tx, ix, position, event);
        let expected = derived.map(|(p,_)|p.to_string()).or_else(|| match position {
            1 => event.map(|e| bs58::encode(e.fee_recipient).into_string()),
            2 => event.map(|e| bs58::encode(e.mint).into_string()),
            6 => event.map(|e| bs58::encode(e.user).into_string()),
            8 => Some(TOKEN.into()),
            _ => rule["fixed_address"].as_str().map(str::to_owned),
        });
        let listed = |list:&str| observed.as_ref().map(|o|source[list].as_array().is_some_and(|a|a.iter().any(|v|v==o)));
        let address_match = if position == 17 { listed("buyback_fee_recipients") }
            else { observed.as_ref().zip(expected.as_ref()).map(|(o,e)|o==e) };
        let flags = index.and_then(|i|privileges(tx,i));
        let privileges_match = flags.map(|(s,w)| (rule["signer"]!=true || s) && (rule["writable"]!=true || w));
        json!({"position":position,"role":rule["name"],"compiled_key_index":index,"observed":observed,"expected":expected,"pda_bump":derived.map(|(_,b)|b),
            "address_match":address_match,"message_signer":flags.map(|(s,_)|s),"message_writable":flags.map(|(_,w)|w),"required_signer":rule["signer"],"required_writable":rule["writable"],
            "required_privileges_match":privileges_match,"source":if position<16{"PINNED_IDL"}else{"OFFICIAL_REMAINING_ACCOUNTS_DOC_AND_SDK"},
            "address_basis":match position { 1|2|6=>"EVENT_BYTES_CORRESPONDENCE",5=>"SDK_ATA_ADDRESS_CORRESPONDENCE_NOT_ACCOUNT_CONTENT",9=>"PDA_FROM_EVENT_CREATOR_NOT_CURVE_ACCOUNT_CREATOR",16=>"SDK_BONDING_CURVE_V2_PDA",17=>"DOCUMENTED_BUYBACK_RECIPIENT_LIST",8=>"SELECTED_LEGACY_TOKEN_PROGRAM",_=>"IDL_ADDRESS_OR_PDA"},
            "documented_fee_recipient":if position==1{event.and_then(|e|listed(if e.mayhem_mode{"mayhem_fee_recipients"}else{"normal_fee_recipients"}))}else{None}})
    }).collect()
}

fn correlations(
    ix: &Value,
    tx: &Value,
    prefix: Option<&BuyInstruction>,
    event: Option<&TradeEvent>,
) -> Value {
    let keys_match = |position, bytes: Option<[u8; 32]>| {
        account_key(tx, ix, position)
            .zip(bytes)
            .map(|(p, b)| p.as_ref() == b)
    };
    json!({"mint_matches":keys_match(2,event.map(|e|e.mint)),"user_matches":keys_match(6,event.map(|e|e.user)),
        "token_amount_matches_prefix":prefix.zip(event).map(|(p,e)|p.amount==e.token_amount),
        "track_volume_matches_prefix":prefix.zip(event).map(|(p,e)|p.track_volume==e.track_volume),
        "buy_representation":event.map(|e|e.is_buy && e.ix_name=="buy"),
        "transaction_status_ok":tx["status"]=="OK","limit":"Correlation does not resolve extra bytes, account contents, historical activation or committed economic state"})
}
