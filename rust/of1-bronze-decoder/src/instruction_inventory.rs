//! Inventory of already-authorized Bronze evidence, never a protocol decoder.
use crate::{collection, invalid};
use of1_range_recorder::sha256;
use pump_protocol_v2::registry::PUMP_PROGRAM_ID;
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    io,
};

pub(crate) const RULE: &str = "DEVELOPMENT_INSTRUCTION_COVERAGE_1";
const MAX_PACKAGES: u64 = 80_541;
const MAX_INSTRUCTIONS: usize = 16_384;

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
struct Location(u64, Option<u64>);
impl Location {
    fn from_instruction(ix: &Value, inner: bool) -> Option<Self> {
        Some(Self(
            ix[if inner { "outer_index" } else { "index" }].as_u64()?,
            if inner {
                Some(ix["inner_order"].as_u64()?)
            } else {
                None
            },
        ))
    }
    fn diagnosis(d: &Value) -> Option<Self> {
        let outer = d["outer_index"].as_u64()?;
        let inner = match d.get("instruction_inner_order") {
            None | Some(Value::Null) if d["schema"] != "PUMP_NESTED_BUY_DIAGNOSTIC_1" => None,
            Some(v) => Some(v.as_u64()?),
            _ => return None,
        };
        Some(Self(outer, inner))
    }
    fn context(c: &Value) -> Option<Self> {
        let outer = c["outer_index"].as_u64()?;
        match c["kind"].as_str()? {
            "DECLARED_TOP_LEVEL" => Some(Self(outer, None)),
            "RECORDED_CPI" => Some(Self(outer, Some(c["inner_order"].as_u64()?))),
            _ => None,
        }
    }
    fn value(&self) -> Value {
        json!({"kind":if self.1.is_some(){"RECORDED_CPI"}else{"DECLARED_TOP_LEVEL"},"outer_index":self.0,"inner_order":self.1})
    }
    fn id(&self, package: &str) -> String {
        format!(
            "{package}:{}:{}",
            self.0,
            self.1.map_or("TOP".into(), |n| format!("CPI:{n}"))
        )
    }
}
fn array(v: &Value) -> &[Value] {
    v.as_array().map_or(&[], Vec::as_slice)
}
fn string(v: &Value) -> io::Result<&str> {
    v.as_str().ok_or_else(|| invalid("INVENTORY_STRING"))
}
fn hash_value(v: &Value) -> io::Result<String> {
    Ok(sha256(&serde_json::to_vec(v).map_err(invalid)?))
}
fn bytes_hash(ix: &Value) -> io::Result<String> {
    Ok(sha256(
        &hex::decode(string(&ix["data_hex"])?).map_err(invalid)?,
    ))
}
fn selected(v: &Value, keys: &[&str]) -> Value {
    Value::Object(
        keys.iter()
            .filter_map(|k| v.get(*k).map(|v| ((*k).to_owned(), v.clone())))
            .collect(),
    )
}
fn source(v: &Value) -> Value {
    let mut s = selected(
        v,
        &[
            "run_id",
            "raw_path",
            "raw_sha256",
            "raw_section_offset",
            "raw_section_length",
            "transaction_node_cid_hex",
            "receipt_sequence",
        ],
    );
    s["bindings"] = selected(
        &v["bindings"],
        &[
            "aggregate_sha256",
            "manifest_sha256",
            "metadata_receipt_sha256",
            "payload_manifest_sha256",
            "prepared_payload_sha256",
        ],
    );
    s["bindings"]["receipt"] = array(&v["bindings"]["receipts"])
        .iter()
        .find(|r| r["sequence"] == v["receipt_sequence"])
        .cloned()
        .unwrap_or(Value::Null);
    s
}
fn evidence(value: &Value, pointer: &str) -> io::Result<Value> {
    Ok(
        json!({"bronze_json_pointer":pointer,"evidence_sha256":hash_value(value)?,
        "original":selected(value,&["schema","kind","source_evidence_sha256","context","outer_index","instruction_inner_order",
            "instruction_sha256","full_data_sha256","sha256","discriminator_hex","layout_outcome","layout_error",
            "disposition","reason","admission","proof_gaps","full_instruction_layout_match","full_instruction_error",
            "instruction_error","event_association_failure","selected_candidate","matching_registered_candidates",
            "candidate_predicates","observation_predicates","remaining_proof"])}),
    )
}
struct Instruction {
    ix: Value,
    references: u64,
    probes: Vec<Value>,
    diagnoses: Vec<Value>,
    trade_facts: Vec<String>,
    event_facts: Vec<String>,
}
impl Instruction {
    fn classification(&self) -> (&'static str, Vec<String>) {
        if !self.trade_facts.is_empty() {
            return ("ADMITTED_TRADE", vec!["BOUND_EXISTING_SILVER".into()]);
        }
        if !self.event_facts.is_empty() {
            return (
                "SUPPORTING_EVENT_CPI",
                vec!["BOUND_EXISTING_SILVER_EVENT".into()],
            );
        }
        let mut reasons = BTreeSet::new();
        for d in &self.diagnoses {
            let d = &d["original"];
            if d["disposition"] == "NOT_ADMITTED"
                || d["admission"] == "NOT_ADMITTED_DIAGNOSTIC_ONLY"
            {
                reasons.insert(
                    d["reason"]
                        .as_str()
                        .unwrap_or("NOT_ADMITTED_DIAGNOSTIC_ONLY")
                        .to_owned(),
                );
            }
        }
        if !reasons.is_empty() {
            return ("REJECTED_TRADE", reasons.into_iter().collect());
        }
        if self.probes.iter().any(|p| {
            p["original"]["kind"] == "TRADE_EVENT_CPI_LAYOUT"
                && p["original"]["context"]["kind"] == "RECORDED_CPI"
                && p["original"]["layout_outcome"] == "LAYOUT_COMPATIBLE_ONLY"
        }) {
            return (
                "OTHER_INSTRUCTION",
                vec!["RECORDED_EVENT_LAYOUT_NOT_A_TRADE_INSTRUCTION".into()],
            );
        }
        (
            "UNEXPLAINED",
            vec!["NO_CONCLUSIVE_EXISTING_INSTRUCTION_VERDICT".into()],
        )
    }
}

fn collect_instructions(
    tx: &Value,
    unknown: &mut Vec<Value>,
) -> io::Result<BTreeMap<Location, Instruction>> {
    let mut instructions = BTreeMap::<Location, Instruction>::new();
    for (field, inner) in [("instructions", false), ("inner_instructions", true)] {
        for (n, ix) in array(&tx[field]).iter().enumerate() {
            let pointer = format!("/transaction/{field}/{n}");
            if ix["program_id"]
                .as_str()
                .and_then(|s| bs58::decode(s).into_vec().ok())
                .is_none_or(|b| b.len() != 32)
            {
                unknown
                    .push(json!({"reason":"PROGRAM_ID_UNRESOLVED","bronze_json_pointer":pointer}));
                continue;
            }
            if ix["program_id"] != PUMP_PROGRAM_ID {
                continue;
            }
            let Some(loc) = Location::from_instruction(ix, inner) else {
                unknown.push(
                    json!({"reason":"PUMP_LOCATION_UNAVAILABLE","bronze_json_pointer":pointer}),
                );
                continue;
            };
            if inner
                && (!ix["stack_height"].is_u64()
                    || !array(&tx["instructions"])
                        .iter()
                        .any(|t| t["index"] == loc.0))
            {
                unknown.push(json!({"reason":"CPI_CONTEXT_UNAVAILABLE","bronze_json_pointer":pointer,"location":loc.value()}));
            }
            if let Some(old) = instructions.get_mut(&loc) {
                if old.ix != *ix {
                    return Err(invalid("INVENTORY_CONFLICTING_LOCATION"));
                }
                old.references += 1;
            } else {
                instructions.insert(
                    loc,
                    Instruction {
                        ix: ix.clone(),
                        references: 1,
                        probes: vec![],
                        diagnoses: vec![],
                        trade_facts: vec![],
                        event_facts: vec![],
                    },
                );
            }
        }
    }
    Ok(instructions)
}
fn attach_evidence(
    record: &Value,
    instructions: &mut BTreeMap<Location, Instruction>,
    unknown: &mut Vec<Value>,
) -> io::Result<()> {
    let tx = &record["transaction"];
    for (n, p) in array(&tx["pump_structural_analysis"]["observations"])
        .iter()
        .enumerate()
    {
        let loc = Location::context(&p["context"]);
        let ev = evidence(
            p,
            &format!("/transaction/pump_structural_analysis/observations/{n}"),
        )?;
        if let Some(i) = loc.and_then(|l| instructions.get_mut(&l)) {
            if p["program_id"] != PUMP_PROGRAM_ID || p["sha256"] != bytes_hash(&i.ix)? {
                return Err(invalid("INVENTORY_PROBE_BINDING"));
            }
            if p["context"]["kind"] == "RECORDED_CPI" && p["context"]["parent"].is_null() {
                unknown.push(json!({"reason":"CPI_PARENT_UNRESOLVED","evidence_sha256":ev["evidence_sha256"],"location":p["context"]}));
            }
            i.probes.push(ev);
        } else {
            unknown.push(json!({"reason":"PROBE_LOCATION_UNRESOLVED","evidence":ev}));
        }
    }
    for field in [
        "pump_sell_analysis",
        "pump_buy_variant_analysis",
        "pump_nested_buy_analysis",
        "pump_buy_exact_quote_v2_analysis",
        "pump_structural_analysis/buy_source_diagnostics",
    ] {
        let path = format!("/transaction/{field}");
        for (n, d) in array(record.pointer(&path).unwrap_or(&Value::Null))
            .iter()
            .enumerate()
        {
            let loc = Location::diagnosis(d);
            let ev = evidence(d, &format!("{path}/{n}"))?;
            if let Some(i) = loc.and_then(|l| instructions.get_mut(&l)) {
                let declared_hash = d
                    .get("instruction_sha256")
                    .or_else(|| d.get("full_data_sha256"))
                    .unwrap_or(&d["instruction"]["sha256"]);
                if *declared_hash != bytes_hash(&i.ix)? {
                    return Err(invalid("INVENTORY_DIAGNOSIS_BINDING"));
                }
                i.diagnoses.push(ev);
            } else {
                unknown.push(json!({"reason":"DIAGNOSIS_LOCATION_UNRESOLVED","evidence":ev}));
            }
        }
    }
    Ok(())
}

#[derive(Default)]
pub(crate) struct Inventory {
    pub(crate) logical: collection::Logical,
    packages: Vec<Value>,
    uncertainty: Vec<Value>,
    facts: Vec<Value>,
    package_count: u64,
    failures: u64,
    instruction_count: usize,
    seen_packages: BTreeSet<String>,
    seen_facts: BTreeSet<String>,
}
impl Inventory {
    fn link_facts(
        &mut self,
        record: &Value,
        hash: &str,
        package: &str,
        instructions: &mut BTreeMap<Location, Instruction>,
        facts: &[Value],
    ) -> io::Result<Vec<Value>> {
        let tx = &record["transaction"];
        let mut fact_links = Vec::new();
        for f in facts
            .iter()
            .filter(|f| f["record"]["bronze_record_sha256"] == hash)
        {
            let r = &f["record"];
            let c = &r["event_context"];
            if tx["status"] != "OK"
                || r["transaction_status"] != "OK"
                || record["atomic_observation_package"] != true
                || r["source"] != record["source"]
                || r["effective_at"] != record["effective_at"]
            {
                return Err(invalid("INVENTORY_FACT_PARENT"));
            }
            let outer = c["outer_index"]
                .as_u64()
                .ok_or_else(|| invalid("INVENTORY_FACT_LOCATION"))?;
            let parent = &c["parent"];
            if parent["program_id"] != PUMP_PROGRAM_ID || parent["outer_index"] != outer {
                return Err(invalid("INVENTORY_EVENT_PARENT"));
            }
            let trade_inner = match parent.get("inner_order") {
                Some(Value::Null) => None,
                Some(v) => Some(
                    v.as_u64()
                        .ok_or_else(|| invalid("INVENTORY_FACT_PARENT_LOCATION"))?,
                ),
                None => return Err(invalid("INVENTORY_FACT_PARENT_LOCATION")),
            };
            let trade = Location(outer, trade_inner);
            let event = Location(
                outer,
                Some(
                    c["inner_order"]
                        .as_u64()
                        .ok_or_else(|| invalid("INVENTORY_EVENT_LOCATION"))?,
                ),
            );
            if trade == event {
                return Err(invalid("INVENTORY_EVENT_IS_TRADE"));
            }
            for (location, expected) in [
                (&trade, &r["instruction_sha256"]),
                (&event, &c["event_cpi_sha256"]),
            ] {
                let ix = instructions
                    .get(location)
                    .ok_or_else(|| invalid("INVENTORY_FACT_INSTRUCTION_MISSING"))?;
                if *expected != bytes_hash(&ix.ix)? {
                    return Err(invalid("INVENTORY_FACT_BYTES"));
                }
            }
            let fh = string(&f["record_sha256"])?;
            if !self.seen_facts.insert(fh.to_owned()) {
                return Err(invalid("INVENTORY_DUPLICATE_FACT"));
            }
            instructions
                .get_mut(&trade)
                .ok_or_else(|| invalid("INVENTORY_TRADE"))?
                .trade_facts
                .push(fh.to_owned());
            instructions
                .get_mut(&event)
                .ok_or_else(|| invalid("INVENTORY_EVENT"))?
                .event_facts
                .push(fh.to_owned());
            let link = json!({"fact_sha256":fh,"package_id":package,"trade_instruction_id":trade.id(package),"event_instruction_id":event.id(package),
                "instruction_sha256":r["instruction_sha256"],"event_cpi_sha256":c["event_cpi_sha256"],"association":c["association"],"source_evidence_sha256":r["source_evidence_sha256"]});
            self.facts.push(link.clone());
            fact_links.push(link);
        }
        Ok(fact_links)
    }

    /// The shared fixed-manifest reader has already verified these records.
    /// Hash the *second* stream too, so the exported inventory cannot use drift.
    pub(crate) fn package(
        &mut self,
        raw: &[u8],
        record: &Value,
        part_id: usize,
        facts: &[Value],
    ) -> io::Result<()> {
        self.logical.add(raw)?;
        self.package_count += 1;
        if self.package_count > MAX_PACKAGES {
            return Err(invalid("INVENTORY_PACKAGE_CAP"));
        }
        let tx = &record["transaction"];
        self.failures += u64::from(tx["status"] == "ERROR");
        let hash = sha256(raw);
        let package = format!("{}:{hash}", string(&record["source"]["run_id"])?);
        if !self.seen_packages.insert(package.clone()) {
            return Err(invalid("INVENTORY_DUPLICATE_PACKAGE"));
        }
        let mut unknown = Vec::new();
        if record["disposition"] != "DECODED" {
            unknown.push(json!({"reason":"BRONZE_NOT_DECODED"}));
        }
        if !tx["instructions"].is_array() {
            unknown.push(json!({"reason":"TOP_LEVEL_INFORMATION_UNAVAILABLE"}));
        }
        if !tx["inner_instructions"].is_array() {
            unknown.push(json!({"reason":"CPI_INFORMATION_UNAVAILABLE"}));
        }
        let mut instructions = collect_instructions(tx, &mut unknown)?;
        attach_evidence(record, &mut instructions, &mut unknown)?;
        let fact_links = self.link_facts(record, &hash, &package, &mut instructions, facts)?;
        if !unknown.is_empty() {
            self.uncertainty.push(json!({"package_id":package,"bronze_record_sha256":hash,"part_id":part_id,"effective_at":record["effective_at"],"transaction_status":tx["status"],"items":unknown}));
        }
        if instructions.is_empty() {
            return Ok(());
        }
        self.instruction_count += instructions.len();
        if self.instruction_count > MAX_INSTRUCTIONS {
            return Err(invalid("INVENTORY_INSTRUCTION_CAP"));
        }
        let rows=instructions.into_iter().map(|(loc,i)| {
            if !i.trade_facts.is_empty() && !i.event_facts.is_empty() {return Err(invalid("INVENTORY_CONFLICTING_FACT_ROLES"));}
            let(category,reasons)=i.classification();
            Ok(json!({"instruction_id":loc.id(&package),"location":loc.value(),"instruction":i.ix,"instruction_sha256":bytes_hash(&i.ix)?,
                "reference_count":i.references,"category":category,
                "may_hide_trade_observation":tx["status"]!="ERROR" && !matches!(category,"ADMITTED_TRADE"|"SUPPORTING_EVENT_CPI"),"reasons":reasons,"probes":i.probes,"diagnoses":i.diagnoses,
                "trade_fact_sha256s":i.trade_facts,"event_fact_sha256s":i.event_facts,"execution":"NOT_ESTABLISHED_BY_INSTRUCTION_PRESENCE"}))
        }).collect::<io::Result<Vec<_>>>()?;
        self.packages.push(json!({"package_id":package,"bronze_record_sha256":hash,"part_id":part_id,"effective_at":record["effective_at"],
            "transaction_status":tx["status"],"atomic_observation_package":record["atomic_observation_package"],"source":source(&record["source"]),
            "original_source_object_sha256":hash_value(&record["source"])?,"protocol_source":tx["pump_structural_analysis"]["official_source"],
            "protocol_source_manifest_sha256":tx["pump_structural_analysis"]["source_manifest_sha256"],"instructions":rows,"fact_links":fact_links}));
        Ok(())
    }
    pub(crate) fn finish(self, counts: &Value) -> io::Result<Value> {
        if counts["packages"] != self.package_count
            || counts["failures"] != self.failures
            || counts["silver_facts"] != self.facts.len()
        {
            return Err(invalid("INVENTORY_COMPLETE_PARENT_FACT_COUNTS"));
        }
        Ok(
            json!({"rule_version":RULE,"packages":self.packages,"uncertainty":self.uncertainty,"fact_links":self.facts,
            "all_packages_checked":self.package_count,"failed_packages_checked":self.failures,"unique_pump_instructions":self.instruction_count,
            "execution_limit":"DECLARATIONS_AND_RECORDED_CPI_ARE_NOT_EXECUTION_OR_STATE_TRANSITION_PROOF"}),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> Value {
        json!({"source":{"run_id":"fixture","raw_sha256":"f".repeat(64)},"disposition":"DECODED","atomic_observation_package":true,
          "effective_at":{"slot":"100","transaction_index_in_slot":0},
          "transaction":{"status":"OK","instructions":[{"index":0,"program_id":PUMP_PROGRAM_ID,"data_hex":"0102"}],
          "inner_instructions":[{"outer_index":0,"inner_order":0,"stack_height":2,"program_id":PUMP_PROGRAM_ID,"data_hex":"0304"}],
          "pump_structural_analysis":{"observations":[]}}})
    }
    fn probe(loc: Location, data: &[u8], kind: &str, reason: Option<&str>) -> Value {
        json!({"context":loc.value(),"program_id":PUMP_PROGRAM_ID,"sha256":sha256(data),"kind":kind,
            "layout_outcome":if reason.is_some(){"LAYOUT_REJECTED"}else{"LAYOUT_COMPATIBLE_ONLY"},"layout_error":reason.map(|r|json!({"reason":r}))})
    }
    fn fact(r: &Value, id: &str) -> Value {
        json!({"record_sha256":sha256(id.as_bytes()),"record":{"bronze_record_sha256":hash_value(r).unwrap(),"source":r["source"],"effective_at":r["effective_at"],"transaction_status":"OK",
            "instruction_sha256":sha256(&[1,2]),"event_context":{"outer_index":0,"inner_order":0,"parent":{"outer_index":0,"inner_order":null,"program_id":PUMP_PROGRAM_ID},"event_cpi_sha256":sha256(&[3,4])}}})
    }
    fn read(r: &Value, facts: &[Value]) -> io::Result<Inventory> {
        let mut i = Inventory::default();
        i.package(&serde_json::to_vec(r).unwrap(), r, 0, facts)?;
        Ok(i)
    }
    #[test]
    fn probes_do_not_multiply_instructions_and_different_positions_remain() {
        let mut r = fixture();
        let p = probe(
            Location(0, None),
            &[1, 2],
            "BUY_INSTRUCTION_LAYOUT",
            Some("WRONG_DISCRIMINATOR"),
        );
        r["transaction"]["pump_structural_analysis"]["observations"] = json!([p, p]);
        let same = r["transaction"]["instructions"][0].clone();
        r["transaction"]["instructions"]
            .as_array_mut()
            .unwrap()
            .push(same.clone());
        let mut another = same;
        another["index"] = json!(1);
        r["transaction"]["instructions"]
            .as_array_mut()
            .unwrap()
            .push(another);
        let i = read(&r, &[]).unwrap();
        let rows = array(&i.packages[0]["instructions"]);
        assert_eq!(rows.len(), 3);
        assert_eq!(rows[0]["reference_count"], 2);
        assert_eq!(array(&rows[0]["probes"]).len(), 2);
        assert_eq!(rows[0]["category"], "UNEXPLAINED");
        assert_eq!(rows[2]["location"]["outer_index"], 1);
        r["transaction"]["instructions"][1]["data_hex"] = json!("ff");
        assert!(read(&r, &[]).is_err());
    }
    #[test]
    fn trade_and_event_links_preserve_many_facts_without_double_trade_count() {
        let mut r = fixture();
        r["transaction"]["pump_structural_analysis"]["observations"] = json!([probe(
            Location(0, None),
            &[1, 2],
            "BUY_INSTRUCTION_LAYOUT",
            Some("WRONG_DISCRIMINATOR")
        )]);
        let facts = vec![fact(&r, "one"), fact(&r, "two")];
        let i = read(&r, &facts).unwrap();
        let rows = array(&i.packages[0]["instructions"]);
        assert_eq!(rows[0]["category"], "ADMITTED_TRADE");
        assert_eq!(array(&rows[0]["trade_fact_sha256s"]).len(), 2);
        assert_eq!(rows[1]["category"], "SUPPORTING_EVENT_CPI");
        assert!(array(&rows[1]["trade_fact_sha256s"]).is_empty());
        assert_eq!(i.facts.len(), 2);
        let mut wrong = facts.clone();
        wrong[0]["record"]["event_context"]["inner_order"] = json!(5);
        assert!(read(&r, &wrong).is_err());
        let mut wrong = facts;
        wrong[0]["record"]["event_context"]["event_cpi_sha256"] = json!("0".repeat(64));
        assert!(read(&r, &wrong).is_err());
    }
    #[test]
    fn failed_instructions_are_retained_but_never_fact_parents() {
        let mut r = fixture();
        r["transaction"]["status"] = json!("ERROR");
        r["transaction"]["pump_sell_analysis"] = json!([{"schema":"PUMP_SELL_OBSERVATION_1","outer_index":0,"instruction_inner_order":null,
            "instruction_sha256":sha256(&[1,2]),"disposition":"NOT_ADMITTED","reason":"TRANSACTION_NOT_SUCCESSFUL"}]);
        let i = read(&r, &[]).unwrap();
        assert_eq!(i.failures, 1);
        assert_eq!(
            i.packages[0]["instructions"][0]["category"],
            "REJECTED_TRADE"
        );
        assert!(read(&r, &[fact(&r, "bad")]).is_err());
        assert_eq!(i.packages[0]["transaction_status"], "ERROR");
    }
    #[test]
    fn missing_cpi_program_and_location_stay_separate_uncertainty() {
        let mut r = fixture();
        r["transaction"]["inner_instructions"] = Value::Null;
        r["transaction"]["instructions"]
            .as_array_mut()
            .unwrap()
            .push(json!({"index":1,"program_id":null}));
        r["transaction"]["instructions"]
            .as_array_mut()
            .unwrap()
            .push(json!({"program_id":PUMP_PROGRAM_ID,"data_hex":"01"}));
        let i = read(&r, &[]).unwrap();
        assert_eq!(i.instruction_count, 1);
        let u = array(&i.uncertainty[0]["items"]);
        assert_eq!(u.len(), 3);
        assert_eq!(u[0]["reason"], "CPI_INFORMATION_UNAVAILABLE");
        assert_eq!(u[1]["reason"], "PROGRAM_ID_UNRESOLVED");
        assert_eq!(u[2]["reason"], "PUMP_LOCATION_UNAVAILABLE");
        assert!(
            i.finish(&json!({"packages":1,"failures":0,"silver_facts":1}))
                .is_err()
        );
    }
    #[test]
    fn full_event_probe_is_other_but_unrecognized_probe_does_not_invent_type() {
        let mut r = fixture();
        r["transaction"]["pump_structural_analysis"]["observations"] = json!([probe(
            Location(0, Some(0)),
            &[3, 4],
            "TRADE_EVENT_CPI_LAYOUT",
            None
        )]);
        let i = read(&r, &[]).unwrap();
        assert_eq!(
            i.packages[0]["instructions"][1]["category"],
            "OTHER_INSTRUCTION"
        );
        r["transaction"]["pump_structural_analysis"]["observations"][0]["layout_outcome"] =
            json!("LAYOUT_REJECTED");
        assert_eq!(
            read(&r, &[]).unwrap().packages[0]["instructions"][1]["category"],
            "UNEXPLAINED"
        );
    }
}
