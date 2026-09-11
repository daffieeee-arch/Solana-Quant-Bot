//! Observation-bound structural probes, not candidate selection or Silver.
//! Only the existing pinned B3 parsers interpret layout; no alternative decoder.
use crate::invalid;
use of1_range_recorder::sha256;
use pump_protocol_v2::{
    decode::{probe_buy_instruction_layout, probe_trade_event_cpi_layout},
    registry::{self, SourceManifest, validate_source_manifest},
};
use serde_json::{Value, json};
use std::{collections::BTreeMap, io};

pub const SOURCE: &[u8] =
    include_bytes!("../../../schemas/protocol/pump/pump-public-docs-9c82f61-source-manifest.json");

/// Inspect source/program/layout separately from invocation or committed state.
/// # Errors
/// Refuses source-identity drift or malformed internal Bronze projections.
pub fn inspect(tx: &Value) -> io::Result<Value> {
    let manifest: SourceManifest = serde_json::from_slice(SOURCE).map_err(invalid)?;
    validate_source_manifest(&manifest).map_err(|e| invalid(format!("PUMP_SOURCE: {e:?}")))?;
    let top = tx["instructions"]
        .as_array()
        .ok_or_else(|| invalid("PUMP_TOP_LEVEL"))?;
    let mut observations = Vec::new();
    for ix in top {
        if ix["program_id"] == registry::PUMP_PROGRAM_ID {
            observations.push(probe(ix, &json!({"kind":"DECLARED_TOP_LEVEL","outer_index":ix["index"],"executed":"NOT_ESTABLISHED_BY_DECLARATION"}))?);
        }
    }
    if let Some(inner) = tx["inner_instructions"].as_array() {
        for (position, ix) in inner.iter().enumerate() {
            if ix["program_id"] == registry::PUMP_PROGRAM_ID {
                let parent = parent(top, &inner[..position], ix);
                observations.push(probe(ix, &json!({"kind":"RECORDED_CPI","outer_index":ix["outer_index"],"inner_order":ix["inner_order"],"stack_height":ix["stack_height"],
                    "parent":parent,"parent_basis":"RECORDED_STACK_HEIGHTS_NOT_ACCOUNT_STATE"}))?);
            }
        }
    }
    Ok(
        json!({"source_manifest_sha256":sha256(SOURCE),"official_source":{"repository":registry::SOURCE_REPOSITORY,"commit":registry::SOURCE_COMMIT,"path":registry::SOURCE_PATH,"blob":registry::SOURCE_BLOB,"sha256":registry::SOURCE_SHA256},
        "scope":"STRUCTURAL_LAYOUT_ONLY","candidate_selection":"NOT_PERFORMED_NO_CALLER_SUPPLIED_COUNT","transaction_status":tx["status"],
        "cpi_coverage":if tx["inner_instructions"].is_null(){"UNAVAILABLE"}else{"RECORDED"},"observations":observations,
        "silver":"NOT_PRODUCED","committed_pump_state":"NOT_ESTABLISHED","historical_activation":"UNKNOWN","economic_identity":"UNAVAILABLE",
        "admission_limit":"Layout probes alone do not establish candidate uniqueness, account layout, activation or committed state; no Silver promotion"}),
    )
}

// Reconstruct only the tree actually supported by contiguous recorded heights.
// A missing height or impossible jump invalidates this parent lane, not the tx.
fn parent(top: &[Value], previous: &[Value], current: &Value) -> Value {
    let Some(outer) = current["outer_index"].as_u64() else {
        return Value::Null;
    };
    let Some(root) = top.iter().find(|v| v["index"] == outer) else {
        return Value::Null;
    };
    let mut stack =
        vec![json!({"program_id":root["program_id"],"outer_index":outer,"inner_order":null})];
    for ix in previous
        .iter()
        .filter(|v| v["outer_index"] == outer)
        .chain(std::iter::once(current))
    {
        let Some(h) = ix["stack_height"]
            .as_u64()
            .and_then(|h| usize::try_from(h).ok())
        else {
            return Value::Null;
        };
        if h < 2 || h > stack.len() + 1 {
            return Value::Null;
        }
        stack.truncate(h - 1);
        if std::ptr::eq(ix, current) {
            return stack.last().cloned().unwrap_or(Value::Null);
        }
        stack.push(json!({"program_id":ix["program_id"],"outer_index":outer,"inner_order":ix["inner_order"]}));
    }
    Value::Null
}

fn probe(ix: &Value, context: &Value) -> io::Result<Value> {
    let bytes = hex::decode(
        ix["data_hex"]
            .as_str()
            .ok_or_else(|| invalid("PUMP_DATA"))?,
    )
    .map_err(invalid)?;
    let event = bytes.starts_with(&registry::EVENT_IX_TAG_LE);
    let result = if event {
        probe_trade_event_cpi_layout(&bytes).map(|e| json!({"classification":"STRUCTURAL_EVENT_FIELDS_NOT_SILVER","mint_bytes_base58":bs58::encode(e.mint).into_string(),
            "user_bytes_base58":bs58::encode(e.user).into_string(),"is_buy":e.is_buy,"ix_name":e.ix_name,"timestamp_raw_i64":e.timestamp.to_string(),
            "sol_amount_raw_u64":e.sol_amount.to_string(),"token_amount_raw_u64":e.token_amount.to_string(),
            "event_virtual_sol_reserves_raw_u64":e.virtual_sol_reserves.to_string(),"event_virtual_token_reserves_raw_u64":e.virtual_token_reserves.to_string(),
            "event_real_sol_reserves_raw_u64":e.real_sol_reserves.to_string(),"event_real_token_reserves_raw_u64":e.real_token_reserves.to_string(),
            "quote_mint_bytes_base58":bs58::encode(e.quote_mint).into_string(),"quote_amount_raw_u64":e.quote_amount.to_string(),
            "reserve_role":"EVENT_REPORTED_NOT_ACCOUNT_STATE","quote_mint_identity":"UNKNOWN","quote_decimals":null,"base_decimals":null,"executable_price":null}))
    } else {
        probe_buy_instruction_layout(&bytes).map(|i| json!({"classification":"STRUCTURAL_INSTRUCTION_FIELDS_NOT_SILVER","amount_raw_u64":i.amount.to_string(),"max_sol_cost_raw_u64":i.max_sol_cost.to_string(),"track_volume":i.track_volume}))
    };
    let (outcome, error, fields) = match result {
        Ok(fields) => ("LAYOUT_COMPATIBLE_ONLY", Value::Null, fields),
        Err(error) => (
            "LAYOUT_REJECTED",
            serde_json::to_value(error).map_err(invalid)?,
            Value::Null,
        ),
    };
    Ok(
        json!({"kind":if event{"TRADE_EVENT_CPI_LAYOUT"}else{"BUY_INSTRUCTION_LAYOUT"},"program_id":registry::PUMP_PROGRAM_ID,
        "context":context,"bytes":bytes.len(),"sha256":sha256(&bytes),"discriminator_hex":hex::encode(&bytes[..bytes.len().min(8)]),
        "data_hex":hex::encode(&bytes),"account_indexes":ix["account_indexes"],"layout_outcome":outcome,"layout_error":error,
        "structural_fields":fields,"account_layout":"NOT_ESTABLISHED","state_fact":"NOT_PRODUCED"}),
    )
}

/// Deterministic counts; program presence differs from instruction references.
#[must_use]
pub fn summary(records: &[Value]) -> Value {
    let mut programs = BTreeMap::<String, [u64; 3]>::new();
    let mut outcomes = BTreeMap::<String, u64>::new();
    let mut cases = Vec::new();
    let mut status = BTreeMap::<String, u64>::new();
    let mut votes = 0;
    let mut cpi_unknown = 0;
    for record in records {
        let tx = &record["transaction"];
        if record["disposition"] != "DECODED" {
            continue;
        }
        if let Some(s) = tx["status"].as_str() {
            *status.entry(s.into()).or_default() += 1;
        }
        if tx["inner_instructions"].is_null() {
            cpi_unknown += 1;
        }
        if let Some(ids) = tx["program_ids"].as_array() {
            for id in ids.iter().filter_map(Value::as_str) {
                programs.entry(id.into()).or_default()[0] += 1;
                if id == "Vote111111111111111111111111111111111111111" {
                    votes += 1;
                }
            }
        }
        for (field, index) in [("instructions", 1), ("inner_instructions", 2)] {
            if let Some(ixs) = tx[field].as_array() {
                for id in ixs.iter().filter_map(|v| v["program_id"].as_str()) {
                    programs.entry(id.into()).or_default()[index] += 1;
                }
            }
        }
        if let Some(observations) = tx["pump_structural_analysis"]["observations"].as_array() {
            for o in observations {
                let key = format!(
                    "{}:{}",
                    o["kind"].as_str().unwrap_or("UNAVAILABLE"),
                    o["layout_error"]["reason"]
                        .as_str()
                        .unwrap_or("LAYOUT_COMPATIBLE_ONLY")
                );
                *outcomes.entry(key).or_default() += 1;
            }
            if !observations.is_empty() {
                cases.push(json!({"effective_at":record["effective_at"],"signature":tx["signatures"][0],"raw_sha256":record["source"]["raw_sha256"],
                    "transaction_node_cid_hex":record["source"]["transaction_node_cid_hex"],"analysis":tx["pump_structural_analysis"]}));
            }
        }
    }
    let programs = programs.into_iter().map(|(id, n)| json!({"program_id":id,"transaction_presence":n[0],"declared_top_level_references":n[1],"recorded_cpi_references":n[2]})).collect::<Vec<_>>();
    json!({"program_frequencies":programs,"transaction_status_counts":status,"vote_program_transactions_retained":votes,"decoded_transactions_with_unknown_cpi":cpi_unknown,
        "pump_layout_outcomes":outcomes,"pump_cases":cases,"silver":"NOT_PRODUCED","committed_pump_state":"NOT_ESTABLISHED",
        "frequency_semantics":"Transaction presence / declared top-level references / recorded CPI references; not successful state changes"})
}
