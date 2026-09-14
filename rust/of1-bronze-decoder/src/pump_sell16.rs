//! One observed Tokenkeg/no-accumulator profile, not generic 16-account support.
//! Mayhem is diagnostic only: PDA correspondence cannot invent CPI privileges
//! or explain the independently mismatching remaining account.
use crate::{
    pump_buy::{account_key, pda, privileges},
    pump_sell::{self, Rejection},
    pump_sell_context,
};
use of1_range_recorder::sha256;
use pump_protocol_v2::decode::TradeEvent;
use serde_json::{Value, json};
use std::collections::BTreeSet;

pub const SOURCE: &[u8] = include_bytes!("../sources/pump-sell16-evidence.json");
pub const CANDIDATE: &str = "pump-sell-9c82f61-tokenkeg-noaccumulator16-nested-height2-v1";
pub const UNRESOLVED: &str = "pump-sell-9c82f61-token2022-mayhem16-unresolved-v1";
const TOKENKEG: &str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
const TOKEN2022: &str = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb";
const MAYHEM: &str = "MAyhSmzXzV1pTf7LsNkrNwkWKTo4ougAJ1PPg47MD4e";
const RESERVED: [&str; 8] = [
    "GesfTA3X2arioaHp8bbKdjG9vJtskViWACZoYvxp4twS",
    "4budycTjhs9fD6xw62VBducVTNgMgJJ5BgtKq7mAZwn6",
    "8SBKzEQU4nLSzcwF4a74F2iaUDQyTfjGndn6qUWBnrpR",
    "4UQeTP1T39KZ9Sfxzo3WR5skgsaP6NZa87BAkuazLEKH",
    "8sNeir4QsLsJdYpc9RZacohhK1Y5FLU3nC5LXgYB4aa6",
    "Fh9HmeLNUMVCvejxCtCL2DbYaRyBFVJ5xrWkLnMH6fdk",
    "463MEnMeGyJekNZFQSTUABBEbLnvMTALbT6ZmsxAbAdq",
    "6AUH3WEHucYZyC61hqpqYUWVto5qA5hjHuNQ32GNnNxA",
];

fn accounts(tx: &Value, ix: &Value, e: &TradeEvent, source: &Value) -> Vec<Value> {
    (0..16).map(|pos| {
        // Same fourteen IDL roles; no accumulator at 14. The SDK appends
        // curve-v2 then buyback recipient. This says nothing about account data.
        let old_pos = if pos < 14 {pos} else {pos + 1};
        let rule = &source["account_rules"][old_pos];
        let observed = account_key(tx, ix, pos).map(|p| p.to_string());
        let derived = pump_sell::expected_pda(tx, ix, old_pos, e);
        let expected = derived.map(|(p, _)| p.to_string()).or_else(|| match pos {
            1 => Some(bs58::encode(e.fee_recipient).into_string()),
            2 => Some(bs58::encode(e.mint).into_string()),
            6 => Some(bs58::encode(e.user).into_string()),
            9 => Some(if e.mayhem_mode {TOKEN2022} else {TOKENKEG}.into()),
            _ => rule["fixed_address"].as_str().map(str::to_owned),
        });
        let listed = |name: &str| observed.as_ref().is_some_and(|key| source[name].as_array().is_some_and(|a| a.iter().any(|v| v == key)));
        let fee_list_match = observed.as_ref().is_some_and(|key| if e.mayhem_mode {RESERVED.contains(&key.as_str())} else {listed("normal_fee_recipients")});
        let address_match = if pos == 15 {listed("buyback_fee_recipients")} else {observed.is_some() && observed == expected};
        let index = ix["account_indexes"][pos].as_u64().and_then(|n| usize::try_from(n).ok());
        let flags = index.and_then(|n| privileges(tx, n));
        let valid_flags = flags.is_some_and(|(s,w)| (rule["signer"] != true || s) && (rule["writable"] != true || w));
        json!({"position":pos,"role":rule["name"],"role_evidence":"PINNED_PROFILE_EXPECTATION_NOT_OBSERVED_ACCOUNT_CONTENT","actual_role_if_mismatched":if address_match {Value::Null}else{json!("UNAVAILABLE_SOURCE_PROOF_MISSING")},"compiled_key_index":index,"observed":observed,"expected":expected,"pda_bump":derived.map(|(_,b)|b),
            "address_match":address_match,"message_signer":flags.map(|(s,_)|s),"message_writable":flags.map(|(_,w)|w),"required_signer":rule["signer"],"required_writable":rule["writable"],
            "required_privileges_match":valid_flags,"documented_fee_recipient":if pos==1{Some(fee_list_match)}else{None},
            "fee_recipient_list":if pos==1{Some(if e.mayhem_mode {"RESERVED_MAYHEM"}else{"NORMAL_NONMAYHEM"})}else{None},
            "basis":match pos {1|2|6=>"EVENT_FIELD_CORRESPONDENCE",5=>"SDK_ATA_CORRESPONDENCE_NOT_ACCOUNT_CONTENT",8=>"PDA_FROM_EVENT_CREATOR_NOT_CURVE_ACCOUNT",14=>"OFFICIAL_REMAINING_CURVE_V2_PDA",15=>"OFFICIAL_BUYBACK_RECIPIENT_LIST",9=>"EXPLICIT_OBSERVED_TOKEN_PROGRAM_PROFILE",_=>"PINNED_IDL_ADDRESS_OR_PDA"}})
    }).collect()
}

fn mayhem_diagnostic(
    tx: &Value,
    ix: &Value,
    rows: &[Value],
    flag_key: &str,
) -> (Vec<&'static str>, Value) {
    let mut gaps = vec!["MAYHEM_16_ACCOUNT_VARIANT_NOT_REGISTERED"];
    if rows[14]["address_match"] != true {
        gaps.push("REMAINING_ACCOUNT_14_DIFFERS_FROM_PINNED_CURVE_V2_PDA");
    }
    if rows[6][flag_key] != true {
        gaps.push("USER_MESSAGE_SIGNATURE_ABSENT_CPI_SIGNATURE_UNAVAILABLE");
    }
    let sol_vault = pda(MAYHEM, &[b"sol-vault"]);
    (
        gaps,
        json!({"user_matches_official_mayhem_sol_vault_pda":sol_vault.is_some_and(|(p,_)|Some(p)==account_key(tx,ix,6)),
        "mayhem_sol_vault_expected":sol_vault.map(|(p,_)|p.to_string()),"mayhem_sol_vault_bump":sol_vault.map(|(_,b)|b),
        "pda_correspondence_is_not_cpi_signer_evidence":true,"remaining_account_14_semantics":"UNAVAILABLE_SOURCE_PROOF_MISSING"}),
    )
}

pub(crate) fn diagnose(tx: &Value, ix: &Value, data: &[u8], direct: bool, source: &Value) -> Value {
    let instruction = pump_sell::decode_instruction(data);
    let context = if direct {
        pump_sell::associated_event(tx, ix)
    } else {
        pump_sell_context::associated_event(tx, ix)
    };
    let mut reason = instruction
        .as_ref()
        .err()
        .copied()
        .or_else(|| context.as_ref().err().copied());
    let mut rows = Vec::new();
    let mut predicates = serde_json::Map::new();
    let mut gaps = Vec::new();
    let mut profile = "UNCLASSIFIED_16_ACCOUNT_SELL";
    let mut details = Value::Null;
    if let (Ok(i), Ok((c, e))) = (&instruction, &context) {
        let token = account_key(tx, ix, 9).map(|p| p.to_string());
        let mayhem_profile = e.mayhem_mode && token.as_deref() == Some(TOKEN2022);
        let selected_profile = !e.mayhem_mode && token.as_deref() == Some(TOKENKEG);
        if mayhem_profile {
            profile = UNRESOLVED;
        } else if selected_profile {
            profile = CANDIDATE;
        }
        rows = accounts(tx, ix, e, source);
        // All supported calls in this new profile are nested. Never expose
        // message minimum flags as proven AccountMeta flags of an invocation.
        if !direct {
            pump_sell::mark_cpi_flags_unavailable(&mut rows);
        }
        let flag_key = if direct {
            "required_privileges_match"
        } else {
            "message_minimum_privileges_match"
        };
        let address_ok = rows.len() == 16
            && rows.iter().all(|r| r["address_match"] == true)
            && rows
                .iter()
                .filter_map(|r| r["observed"].as_str())
                .collect::<BTreeSet<_>>()
                .len()
                == 16
            && rows[1]["documented_fee_recipient"] == true;
        let message_ok = rows.iter().all(|r| r[flag_key] == true);
        let correlation = account_key(tx, ix, 2).is_some_and(|p| p.as_ref() == e.mint)
            && account_key(tx, ix, 6).is_some_and(|p| p.as_ref() == e.user)
            && i.amount == e.token_amount;
        let pattern = selected_profile
            && !e.track_volume
            && e.shareholders.is_empty()
            && e.cashback_fee_basis_points == 0
            && e.cashback == 0;
        for (key, value) in [
            (
                "exact_instruction_and_367_byte_event",
                c["event_cpi_bytes"] == 367,
            ),
            ("single_bounded_nested_cpi_context", !direct),
            ("all_16_account_addresses_and_fee_list", address_ok),
            ("message_minimum_privileges", message_ok),
            ("mint_user_amount_correspondence", correlation),
            ("selected_tokenkeg_nonmayhem_noaccumulator_pattern", pattern),
            (
                "recorded_transaction_ok",
                tx["status"] == "OK" && tx["transaction_error"].is_null(),
            ),
        ] {
            predicates.insert(key.into(), json!(value));
        }
        if mayhem_profile {
            (gaps, details) = mayhem_diagnostic(tx, ix, &rows, flag_key);
        }
        reason = if direct {
            Some(Rejection::UnsupportedInvocation)
        } else if c["event_cpi_bytes"] != 367 {
            Some(Rejection::UnsupportedEventVariant)
        } else if !address_ok || !message_ok {
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
    let matching: Vec<_> = [CANDIDATE]
        .into_iter()
        .filter(|_| !predicates.is_empty() && predicates.values().all(|v| v == true))
        .collect();
    let admitted = reason.is_none() && matching.len() == 1;
    json!({"schema":"PUMP_SELL_OBSERVATION_1","source_evidence_sha256":sha256(SOURCE),"outer_index":if direct {ix["index"].clone()}else{ix["outer_index"].clone()},"instruction_inner_order":ix["inner_order"],
        "instruction_data_hex":hex::encode(data),"instruction_sha256":sha256(data),"instruction_bytes":data.len(),"instruction":instruction.as_ref().ok().map(|i|json!({"amount_raw_u64":i.amount.to_string(),"min_sol_output_raw_u64":i.min_sol_output.to_string()})),
        "accounts":rows,"account_count":ix["account_indexes"].as_array().map(Vec::len),"event_context":context.as_ref().ok().map(|(v,_)|v),"event_reported":context.as_ref().ok().map(|(_,e)|pump_sell::event_fields(e)),
        "evaluated_profile":profile,"profile_evidence_status":if admitted {"OBSERVATION_PREDICATES_MATCHED"}else{"REJECTED_SOURCE_OR_PRIVILEGE_GAPS"},"proof_gaps":gaps,"observed_profile_details":details,
        "candidate_predicates":predicates,"matching_registered_candidates":matching,"selected_candidate":if admitted {Some(CANDIDATE)}else{None},"candidate_scope":"OBSERVATION_DERIVED_TOKENKEG16_PROFILE_NOT_GLOBAL_HISTORICAL_ACTIVATION",
        "disposition":if admitted {"MATCHED_RECORDED_EVENT_FACTS"}else{"NOT_ADMITTED"},"reason":reason,"transaction_status":tx["status"],"committed_account_state":"NOT_ESTABLISHED","historical_activation":"UNKNOWN","economic_identity":"UNKNOWN",
        "cashback_coin_account_flag":"UNAVAILABLE_NOT_READ","quote_decimals":null,"base_decimals":null,"name":null,"ticker":null,"launch_at":null,"executable_price":null})
}
