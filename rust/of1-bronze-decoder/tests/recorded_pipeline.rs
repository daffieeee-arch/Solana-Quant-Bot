//! Synthetic receipt/plan/graph harness adapted from retained `recorded_verification` tests.
//! One byte-exact authentic tx section inside a SYNTHETIC slot graph/receipt is still Fixture.

use of1_bronze_decoder::{archive, report};
use of1_range_recorder::{
    FormatSource, RECORD_BYTES, SLOTS_PER_EPOCH,
    acquisition::derive_payload_from_metadata,
    acquisition_http::parse_response_head,
    durable::{
        Clock, ClockSample, StoreResult,
        acquisition::{
            AGGREGATE_SCHEMA, AcquisitionStore, AggregateBudget, AggregatePlan, Authority,
            MetadataLease, PayloadLease, RequestKind, StageBudget, current_executable_sha256,
        },
    },
    sha256,
};
use serde_cbor::Value as C;
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
};

const SLOT: u64 = 422_496_001;
const OFFSET: u64 = 4096;

#[derive(Clone)]
struct HistoricalFixtureClock;
impl Clock for HistoricalFixtureClock {
    fn sample(&self) -> StoreResult<ClockSample> {
        Ok(ClockSample {
            wall_ms: 100_000,
            boot_ms: 10_000,
            boot_id: "HISTORICAL_OFFLINE_FIXTURE_NOT_CURRENT_BOOT".into(),
        })
    }
}

fn vector() -> Value {
    serde_json::from_str(include_str!(
        "../../../schemas/acquisition/of1/car-structural-fixture.json"
    ))
    .unwrap()
}

fn payload() -> Vec<u8> {
    fixture_payload(None)
}

struct Harness {
    temp: tempfile::TempDir,
    root: PathBuf,
    plan: AggregatePlan,
    first_slot: u64,
    store: AcquisitionStore<HistoricalFixtureClock>,
}

impl Harness {
    fn new() -> Self {
        Self::new_sample(None, None)
    }

    fn new_sample(
        sample_identity: Option<of1_range_recorder::sample::SampleIdentity>,
        export: Option<&Path>,
    ) -> Self {
        let temp = tempfile::tempdir().unwrap();
        let root = if let Some(export) = export {
            fs::create_dir(export).unwrap();
            export.join("run")
        } else {
            temp.path().join("run")
        };
        let first_slot = sample_identity.as_ref().map_or(SLOT, |s| s.start_slot);
        let plan = AggregatePlan {
            schema: AGGREGATE_SCHEMA.into(),
            sample_identity,
            epoch: 978,
            format_source: FormatSource::pinned(),
            code_sha: "a".repeat(40),
            toolchain_fingerprint: "b".repeat(64),
            executable_sha256: current_executable_sha256().unwrap(),
            budget: AggregateBudget {
                max_slots: 4,
                max_plan_bytes: 131_072,
                max_requests: 14,
                max_response_entity_bytes: SLOTS_PER_EPOCH * RECORD_BYTES,
                max_total_response_entity_bytes: 16_000_000,
                max_disk_bytes: 64 * 1024 * 1024,
                required_free_disk_bytes: 64 * 1024 * 1024,
                max_memory_bytes: 512 * 1024 * 1024,
                max_runtime_ms: 120_000,
                response_timeout_ms: 10_000,
                request_retries: 1,
            },
        };
        let store = AcquisitionStore::create(
            &root,
            plan.clone(),
            MetadataLease {
                schema: "OF1_METADATA_LEASE_1".into(),
                authority: Authority::Fixture,
                budget: StageBudget {
                    max_requests: 6,
                    max_response_entity_bytes_total: 12_000_000,
                    max_runtime_ms: 60_000,
                },
            },
            HistoricalFixtureClock,
        )
        .unwrap();
        Self {
            temp,
            root,
            plan,
            first_slot,
            store,
        }
    }

    fn publish(&mut self, sequence: u64, bytes: &[u8]) {
        let request = self.store.request(sequence).unwrap().clone();
        let headers = match request.kind {
            RequestKind::CarRange {
                start,
                end_exclusive,
                total,
                ..
            } => format!(
                "HTTP/1.1 206 Partial Content\r\nContent-Length: {}\r\nContent-Range: bytes {start}-{}/{total}\r\nETag: \"fixture\"\r\n\r\n",
                bytes.len(),
                end_exclusive - 1
            ),
            _ => format!(
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nETag: \"fixture\"\r\n\r\n",
                if sequence == 3 { 100_000 } else { bytes.len() }
            ),
        };
        let permit = self.store.reserve(sequence).unwrap();
        self.store
            .begin_stream(
                &permit,
                parse_response_head(headers.as_bytes(), &request).unwrap(),
            )
            .unwrap();
        for fragment in bytes.chunks(65_536) {
            self.store.append_stream(&permit, fragment).unwrap();
        }
        self.store.finish_stream(permit).unwrap();
    }

    fn metadata(&mut self) {
        self.metadata_slots(&[payload()]);
    }

    fn metadata_slots(&mut self, payloads: &[Vec<u8>]) {
        let mut index = vec![0; usize::try_from(SLOTS_PER_EPOCH * RECORD_BYTES).unwrap()];
        let mut offset = OFFSET;
        for (i, bytes) in payloads.iter().enumerate() {
            let at = (usize::try_from(self.first_slot - 978 * SLOTS_PER_EPOCH).unwrap() + i) * 12;
            index[at..at + 8].copy_from_slice(&offset.to_le_bytes());
            index[at + 8..at + 12]
                .copy_from_slice(&u32::try_from(bytes.len()).unwrap().to_le_bytes());
            offset += u64::try_from(bytes.len()).unwrap();
        }
        self.publish(0, &index);
        self.publish(1, format!("{} epoch-978.car\n", "0".repeat(64)).as_bytes());
        self.publish(
            2,
            format!("{}\n", vector()["root_cid_base32"].as_str().unwrap()).as_bytes(),
        );
        self.publish(3, &[]);
    }

    fn admit(&mut self) {
        self.admit_slots(1);
    }

    fn admit_slots(&mut self, count: u64) {
        let metadata = (0..4)
            .map(|n| self.store.published(n).unwrap().unwrap())
            .collect::<Vec<_>>();
        let prepared = derive_payload_from_metadata(
            &self.plan,
            &metadata,
            self.first_slot,
            self.first_slot + count,
        )
        .unwrap();
        let lease = PayloadLease {
            schema: "OF1_PAYLOAD_LEASE_1".into(),
            authority: Authority::Fixture,
            budget: StageBudget {
                max_requests: count * 2,
                max_response_entity_bytes_total: 16384,
                max_runtime_ms: 60_000,
            },
            prepared_payload_sha256: prepared.sha256().unwrap(),
            metadata_receipt_sha256: prepared.metadata_receipt_sha256().into(),
        };
        self.store.admit_payload(lease, &prepared).unwrap();
    }
}

fn inventory(root: &Path) -> BTreeMap<String, String> {
    fn visit(root: &Path, at: &Path, found: &mut BTreeMap<String, String>) {
        for entry in fs::read_dir(at).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                visit(root, &path, found);
            } else {
                found.insert(
                    path.strip_prefix(root).unwrap().to_str().unwrap().into(),
                    sha256(&fs::read(path).unwrap()),
                );
            }
        }
    }
    let mut found = BTreeMap::new();
    visit(root, root, &mut found);
    found
}

fn rewrite_json(path: &Path, change: impl FnOnce(&mut Value)) {
    let mut value: Value = serde_json::from_slice(&fs::read(path).unwrap()).unwrap();
    change(&mut value);
    fs::write(path, serde_json::to_vec(&value).unwrap()).unwrap();
}

fn section(v: &C) -> (Vec<u8>, C) {
    let content = serde_cbor::to_vec(v).unwrap();
    let mut cid = vec![1, 0x71, 0x12, 0x20];
    cid.extend(hex::decode(sha256(&content)).unwrap());
    let mut length = cid.len() + content.len();
    let mut raw = Vec::new();
    while length >= 128 {
        raw.push(u8::try_from(length & 127).unwrap() | 128);
        length >>= 7;
    }
    raw.push(u8::try_from(length).unwrap());
    raw.extend(&cid);
    raw.extend(content);
    let mut identity = vec![0];
    identity.extend(cid);
    (raw, C::Tag(42, Box::new(C::Bytes(identity))))
}
fn a(values: Vec<C>) -> C {
    C::Array(values)
}
fn n(value: u64) -> C {
    C::Integer(i128::from(value))
}
fn fixture_payload(change: Option<&str>) -> Vec<u8> {
    fixture_payload_at(SLOT, change)
}

fn fixture_payload_at(slot: u64, change: Option<&str>) -> Vec<u8> {
    let (file, position) = if change == Some("nested_sell") {
        (include_str!("fixtures/authentic-pump-sections.json"), 3)
    } else if change == Some("sell") {
        (include_str!("fixtures/authentic-pump-sections.json"), 1)
    } else {
        (include_str!("fixtures/authentic-sections.json"), 0)
    };
    let fixtures: Value = serde_json::from_str(file).unwrap();
    let raw = hex::decode(
        fixtures["fixtures"][position]["section_hex"]
            .as_str()
            .unwrap(),
    )
    .unwrap();
    let prefix = raw.iter().position(|b| b & 128 == 0).unwrap() + 1;
    let C::Array(mut tx) = serde_cbor::from_slice(&raw[prefix + 36..]).unwrap() else {
        panic!("tx")
    };
    // This graph is explicitly synthetic: native slot field, receipts and index
    // are constructed together, never masquerading as a copied authentic run.
    tx[3] = n(slot);
    if matches!(change, Some("sell" | "nested_sell")) {
        tx[4] = n(0);
    } // Synthetic one-tx graph; original Raw is never changed.
    if let Some(change) = change.filter(|c| !matches!(*c, "sell" | "nested_sell")) {
        let which = if change == "wire" { 1 } else { 2 };
        let C::Array(f) = &mut tx[which] else {
            panic!("frame")
        };
        f[4] = C::Bytes(if change == "missing" {
            vec![]
        } else {
            vec![255]
        });
    }
    let (tx_raw, tx_link) = section(&a(tx));
    let (entry_raw, entry_link) = section(&a(vec![
        n(1),
        n(0),
        C::Bytes(vec![0; 32]),
        a(vec![tx_link]),
    ]));
    let (rewards_raw, rewards_link) = section(&a(vec![
        n(5),
        n(slot),
        a(vec![n(6), C::Null, C::Null, C::Null, C::Bytes(vec![])]),
    ]));
    let (block_raw, _) = section(&a(vec![
        n(2),
        n(slot),
        a(vec![a(vec![C::Integer(-1), n(0)])]),
        a(vec![entry_link]),
        a(vec![n(slot - 1), n(0)]),
        rewards_link,
    ]));
    [tx_raw, entry_raw, rewards_raw, block_raw].concat()
}

#[test]
fn metadata_only_never_claims_zero_transactions_or_authentic_dataset() {
    let mut h = Harness::new();
    h.metadata();
    let before = inventory(&h.root);
    let report = report::decode_run(&h.root).unwrap();
    assert_eq!(report["input_kind"], "METADATA_ONLY");
    assert!(report["decoded_transactions"].is_null());
    assert_eq!(report["silver_records"], json!([]));
    assert_eq!(report["silver"], "NOT_PRODUCED");
    assert_eq!(before, inventory(&h.root));
    assert!(h.temp.path().exists());
}

#[test]
fn synthetic_native_run_with_authentic_sell_wire_keeps_fixture_provenance_and_silver_determinism() {
    let mut h = Harness::new();
    let bytes = fixture_payload_at(SLOT, Some("sell"));
    h.metadata_slots(std::slice::from_ref(&bytes));
    h.admit_slots(1);
    h.publish(4, &bytes);
    let before = inventory(&h.root);
    let r = report::decode_run(&h.root).unwrap();
    assert_eq!(r, report::decode_run(&h.root).unwrap());
    assert_eq!(r["dispositions"]["DECODED"], 1);
    assert_eq!(r["silver_fact_count"], 1);
    let fact = &r["silver_records"][0];
    assert_eq!(fact["receipt_evidence"], "Fixture");
    assert_eq!(fact["input_kind"], "FIXTURE_RECORDED_CAR_SLOT");
    assert_eq!(fact["source"]["raw_sha256"], sha256(&bytes));
    assert_eq!(fact["source"]["bindings"], r["bindings"]);
    assert_eq!(
        fact["bronze_record_sha256"],
        sha256(&serde_json::to_vec(&r["records"][0]).unwrap())
    );
    let accounted = serde_json::to_vec(&r["records"][0]).unwrap().len()
        + serde_json::to_vec(fact).unwrap().len();
    assert_eq!(r["resource_accounting"]["record_json_bytes"], accounted);
    assert_eq!(before, inventory(&h.root));
    assert!(report::html(&r).contains("1 gekoppelde instructie/event-pakketten"));
}
#[test]
fn native_nested_sell_preserves_receipts_context_and_determinism() {
    let mut h = Harness::new();
    let bytes = fixture_payload_at(SLOT, Some("nested_sell"));
    h.metadata_slots(std::slice::from_ref(&bytes));
    h.admit_slots(1);
    h.publish(4, &bytes);
    let before = inventory(&h.root);
    let r = report::decode_run(&h.root).unwrap();
    assert_eq!(r, report::decode_run(&h.root).unwrap());
    assert_eq!(r["dispositions"]["DECODED"], 1);
    assert_eq!(r["silver_fact_count"], 1);
    let fact = &r["silver_records"][0];
    assert_eq!(fact["receipt_evidence"], "Fixture");
    assert_eq!(fact["source"]["raw_sha256"], sha256(&bytes));
    assert_eq!(fact["source"]["bindings"], r["bindings"]);
    assert_eq!(
        fact["bronze_record_sha256"],
        sha256(&serde_json::to_vec(&r["records"][0]).unwrap())
    );
    let d = &r["records"][0]["transaction"]["pump_sell_analysis"][0];
    assert_eq!(d["event_context"]["parent"]["inner_order"], 0);
    assert_eq!(d["event_context"]["inner_order"], 3);
    assert_eq!(
        d["event_context"]["subtree"]["end_inner_order_exclusive"],
        4
    );
    assert!(
        d["accounts"]
            .as_array()
            .unwrap()
            .iter()
            .all(|a| a["cpi_signer"].is_null() && a["cpi_privileges_verified"] == false)
    );
    assert!(report::html(&r).contains("class=sell-trace"));
    assert_eq!(before, inventory(&h.root));
}

#[test]
fn receipt_to_bronze_is_deterministic_read_only_with_expired_clock_and_held_writer_lock() {
    let mut h = Harness::new();
    h.metadata();
    h.admit();
    h.publish(4, &payload());
    let before = inventory(&h.root);
    let first = report::decode_run(&h.root).unwrap();
    let second = report::decode_run(&h.root).unwrap();
    assert_eq!(first, second);
    assert_eq!(first["input_kind"], "FIXTURE_RECORDED_CAR_SLOT");
    assert_eq!(first["transaction_envelopes"], 1);
    assert_eq!(first["dispositions"]["DECODED"], 1);
    assert_eq!(first["records"][0]["transaction"]["fee_lamports"], "5000");
    assert_eq!(
        first["records"][0]["source"]["raw_sha256"],
        sha256(&payload())
    );
    assert_eq!(first["root_to_slot_membership"], "UNAVAILABLE");
    assert_eq!(before, inventory(&h.root));
}
#[test]
fn receipt_raw_manifest_corruption_fails_without_source_mutation() {
    for target in ["raw", "receipt", "manifest"] {
        let mut h = Harness::new();
        h.metadata();
        h.admit();
        h.publish(4, &payload());
        match target {
            "raw" => fs::write(
                h.root.join("published/0000000004/raw.bin"),
                vec![0; payload().len()],
            )
            .unwrap(),
            "receipt" => rewrite_json(&h.root.join("published/0000000004/receipt.json"), |v| {
                v["sha256"] = json!("f".repeat(64));
            }),
            _ => rewrite_json(&h.root.join("run.json"), |v| {
                v["aggregate_sha256"] = json!("0".repeat(64));
            }),
        }
        let before = inventory(&h.root);
        assert!(report::decode_run(&h.root).is_err());
        assert_eq!(before, inventory(&h.root));
    }
}
#[test]
fn source_signed_shredding_retained_and_graph_corruption_rejected() {
    let raw = payload();
    let archive = archive::inspect(SLOT, &raw).unwrap();
    assert_eq!(archive.envelopes.len(), 1);
    assert_eq!(archive.verified_nodes, 4);
    assert!(archive::inspect(SLOT + 1, &raw).is_err());
    let mut bad = raw.clone();
    bad[80] ^= 1;
    assert!(archive::inspect(SLOT, &bad).is_err());
    assert!(archive::inspect(SLOT, &raw[..raw.len() - 1]).is_err());
}
#[test]
fn each_valid_archival_envelope_can_be_missing_or_quarantined_without_silent_loss() {
    for (change, reason) in [
        ("missing", "MISSING_STATUS_METADATA"),
        ("wire", "TRANSACTION_WIRE"),
        ("protobuf", "STATUS_PROTOBUF"),
    ] {
        let bytes = fixture_payload(Some(change));
        let archived = archive::inspect(SLOT, &bytes).unwrap();
        assert_eq!(archived.envelopes.len(), 1);
        let error =
            of1_bronze_decoder::codec::decode(&archived.envelopes[0], &archived.continuations)
                .unwrap_err()
                .to_string();
        assert!(error.starts_with(reason), "{error}");
    }
}

fn multi(count: u64) -> Harness {
    let mut h = Harness::new();
    let payloads = (0..count)
        .map(|i| fixture_payload_at(SLOT + i, None))
        .collect::<Vec<_>>();
    h.metadata_slots(&payloads);
    h.admit_slots(count);
    for (i, p) in payloads.iter().enumerate() {
        h.publish(4 + u64::try_from(i).unwrap(), p);
    }
    h
}

#[test]
fn three_native_slots_keep_receipt_binding_source_order_and_determinism() {
    let h = multi(3);
    let before = inventory(&h.root);
    let first = report::decode_run(&h.root).unwrap();
    assert_eq!(first, report::decode_run(&h.root).unwrap());
    assert_eq!(first["input_kind"], "FIXTURE_RECORDED_CAR_SELECTION");
    assert_eq!(first["transaction_envelopes"], 3);
    assert_eq!(first["dispositions"]["DECODED"], 3);
    assert_eq!(first["slots"].as_array().unwrap().len(), 3);
    for i in 0..3 {
        assert_eq!(
            first["records"][i]["effective_at"]["slot"],
            (SLOT + u64::try_from(i).unwrap()).to_string()
        );
        assert_eq!(
            first["records"][i]["effective_at"]["transaction_index_in_slot"],
            0
        );
        assert_eq!(first["records"][i]["source"]["receipt_sequence"], i + 4);
        assert_eq!(first["records"][i]["source"]["bindings"], first["bindings"]);
        assert_eq!(first["slots"][i]["dispositions"]["DECODED"], 1);
        assert_eq!(
            first["slots"][i]["resource_accounting"]["max_record_json_bytes"],
            report::MAX_RECORD_JSON_BYTES
        );
    }
    assert_eq!(first["analysis"]["vote_program_transactions_retained"], 3);
    assert_eq!(before, inventory(&h.root));
    let html = report::html(&first);
    for i in 0..3 {
        assert!(html.contains(&(SLOT + i).to_string()));
    }
    assert!(html.contains("Geen Silver geproduceerd"));
}

#[test]
fn native_selection_missing_middle_corrupt_middle_or_changed_index_fails_closed() {
    for target in ["missing", "raw", "receipt", "index"] {
        let h = multi(3);
        match target {
            "missing" => fs::remove_dir_all(h.root.join("published/0000000005")).unwrap(),
            "raw" => fs::write(h.root.join("published/0000000005/raw.bin"), b"corrupt").unwrap(),
            "receipt" => rewrite_json(&h.root.join("published/0000000005/receipt.json"), |v| {
                v["sha256"] = json!("f".repeat(64));
            }),
            _ => fs::write(
                h.root.join("published/0000000000/raw.bin"),
                b"corrupt index",
            )
            .unwrap(),
        }
        let before = inventory(&h.root);
        assert!(report::decode_run(&h.root).is_err(), "{target}");
        assert_eq!(before, inventory(&h.root));
    }
}

#[test]
fn selection_does_not_silently_expand_beyond_three_slots() {
    let h = multi(4);
    assert_eq!(
        report::decode_run(&h.root).unwrap_err().to_string(),
        "BRONZE_SELECTION_SLOT_LIMIT"
    );
    let mut bytes = report::MAX_SELECTION_RECORD_BYTES;
    assert!(report::charge(&mut bytes, 1, report::MAX_SELECTION_RECORD_BYTES).is_err());
    assert_eq!(bytes, report::MAX_SELECTION_RECORD_BYTES);
}

#[test]
fn multi_slot_reports_missing_and_quarantined_envelopes_without_dropping_order() {
    let mut h = Harness::new();
    let payloads = vec![
        fixture_payload_at(SLOT, None),
        fixture_payload_at(SLOT + 1, Some("missing")),
        fixture_payload_at(SLOT + 2, Some("wire")),
    ];
    h.metadata_slots(&payloads);
    h.admit_slots(3);
    for (i, bytes) in payloads.iter().enumerate() {
        h.publish(4 + u64::try_from(i).unwrap(), bytes);
    }
    let before = inventory(&h.root);
    let r = report::decode_run(&h.root).unwrap();
    assert_eq!(r["transaction_envelopes"], 3);
    assert_eq!(
        r["dispositions"],
        json!({"DECODED":1,"MISSING":1,"QUARANTINED":1,"UNSUPPORTED":0})
    );
    for (i, outcome) in ["DECODED", "MISSING", "QUARANTINED"]
        .into_iter()
        .enumerate()
    {
        assert_eq!(r["records"][i]["disposition"], outcome);
    }
    assert_eq!(r["pump_program_unknown"], 2);
    assert!(r["pump_program_involvement_transactions"].is_null());
    assert_eq!(r["reasons"]["MISSING_STATUS_METADATA"], 1);
    assert_eq!(before, inventory(&h.root));
}

#[test]
fn frozen_sample_identity_flows_from_store_receipts_to_bronze_and_silver() {
    use of1_range_recorder::sample::{END_SLOT, FIRST_SLOT, SampleIdentity};
    let export = std::env::var_os("COLUMNAR_SAMPLE_FIXTURE_DIR").map(PathBuf::from);
    let sample = SampleIdentity::fixed_pilot();
    let mut h = Harness::new_sample(Some(sample.clone()), export.as_deref());
    let payloads = [Some("sell"), Some("missing"), Some("wire")]
        .iter()
        .enumerate()
        .map(|(i, variant)| fixture_payload_at(FIRST_SLOT + u64::try_from(i).unwrap(), *variant))
        .collect::<Vec<_>>();
    h.metadata_slots(&payloads);
    let metadata = (0..4)
        .map(|n| h.store.published(n).unwrap().unwrap())
        .collect::<Vec<_>>();
    assert!(derive_payload_from_metadata(&h.plan, &metadata, FIRST_SLOT + 1, END_SLOT).is_err());
    assert!(derive_payload_from_metadata(&h.plan, &metadata, FIRST_SLOT, END_SLOT + 1).is_err());
    h.admit_slots(3);
    for (i, bytes) in payloads.iter().enumerate() {
        h.publish(4 + u64::try_from(i).unwrap(), bytes);
    }
    let before = inventory(&h.root);
    let r = report::decode_run(&h.root).unwrap();
    assert_eq!(r, report::decode_run(&h.root).unwrap());
    let identity = serde_json::to_value(&sample).unwrap();
    assert_eq!(r["sample_identity"], identity);
    assert_eq!(r["bindings"]["sample_identity"], identity);
    assert_eq!(r["slice_class"], "RESEARCH_SAMPLING");
    assert_eq!(r["receipt_evidence"], "Fixture");
    assert_eq!(r["research_ready"], false);
    assert_eq!(
        r["dispositions"],
        json!({"DECODED":1,"MISSING":1,"QUARANTINED":1,"UNSUPPORTED":0})
    );
    assert_eq!(r["silver_records"].as_array().unwrap().len(), 1);
    for layer in ["records", "silver_records"] {
        for row in r[layer].as_array().unwrap() {
            assert_eq!(row["sample_identity"], identity);
            assert_eq!(row["source"]["bindings"]["sample_identity"], identity);
            assert_eq!(row["slice_class"], "RESEARCH_SAMPLING");
            assert_eq!(row["receipt_evidence"], "Fixture");
        }
    }
    let plan: Value = serde_json::from_slice(&fs::read(h.root.join("run.json")).unwrap()).unwrap();
    assert_eq!(plan["plan"]["sample_identity"], identity);
    let aggregate_hash = sha256(&serde_json::to_vec(&h.plan).unwrap());
    for sequence in 0..7 {
        let receipt = h.store.published(sequence).unwrap().unwrap().receipt;
        assert_eq!(receipt.aggregate_sha256, aggregate_hash);
        assert_eq!(receipt.evidence, "Fixture");
    }
    assert_eq!(before, inventory(&h.root));
    // Optional exported source fixture is created here through the real store,
    // never copied from an authentic run. The external gate invokes our normal
    // decoder CLI separately; tests contain no process/network capability.
}

#[test]
fn legacy_engineering_cannot_be_retrofitted_with_sample_identity() {
    use of1_range_recorder::sample::SampleIdentity;
    let mut h = Harness::new();
    let legacy_bytes = serde_json::to_vec(&h.plan).unwrap();
    assert!(
        !String::from_utf8(legacy_bytes.clone())
            .unwrap()
            .contains("sample_identity")
    );
    let roundtrip: AggregatePlan = serde_json::from_slice(&legacy_bytes).unwrap();
    assert_eq!(serde_json::to_vec(&roundtrip).unwrap(), legacy_bytes);
    h.metadata();
    h.admit();
    h.publish(4, &payload());
    let report = report::decode_run(&h.root).unwrap();
    assert_eq!(report["slice_class"], "ENGINEERING_VALIDATION_ONLY");
    assert!(report.get("sample_identity").is_none());
    let mut retrofitted = h.plan.clone();
    retrofitted.sample_identity = Some(SampleIdentity::fixed_pilot());
    let metadata = (0..4)
        .map(|n| h.store.published(n).unwrap().unwrap())
        .collect::<Vec<_>>();
    assert!(
        derive_payload_from_metadata(
            &retrofitted,
            &metadata,
            of1_range_recorder::sample::FIRST_SLOT,
            of1_range_recorder::sample::END_SLOT
        )
        .is_err()
    );
    rewrite_json(&h.root.join("run.json"), |v| {
        v["plan"] = serde_json::to_value(&retrofitted).unwrap();
        v["aggregate_sha256"] = json!(sha256(&serde_json::to_vec(&retrofitted).unwrap()));
    });
    assert!(report::decode_run(&h.root).is_err());
}

#[test]
fn sample_changes_approval_target_and_cannot_resume_an_engineering_run() {
    use of1_range_recorder::{
        durable::{StoreError, acquisition::metadata_proposal_sha256},
        sample::SampleIdentity,
    };
    let h = Harness::new();
    let budget = StageBudget {
        max_requests: 6,
        max_response_entity_bytes_total: 12_000_000,
        max_runtime_ms: 60_000,
    };
    let mut changed = h.plan.clone();
    changed.sample_identity = Some(SampleIdentity::fixed_pilot());
    assert_ne!(
        metadata_proposal_sha256(&h.plan, &budget).unwrap(),
        metadata_proposal_sha256(&changed, &budget).unwrap()
    );
    let lease = h.store.progress().unwrap().current_lease_sha256;
    let before = inventory(&h.root);
    drop(h.store);
    assert!(matches!(
        AcquisitionStore::resume(&h.root, &changed, &lease, HistoricalFixtureClock),
        Err(StoreError::Identity)
    ));
    assert_eq!(before, inventory(&h.root));
}

#[test]
fn sample_identity_rejects_different_seed_class_window_and_epoch_before_initialization() {
    use of1_range_recorder::sample::SampleIdentity;
    let h = Harness::new();
    for key in [
        "seed",
        "sample_class",
        "selection_plan_sha256",
        "start_slot",
        "end_slot_exclusive",
        "epoch",
    ] {
        let mut identity = serde_json::to_value(SampleIdentity::fixed_pilot()).unwrap();
        identity[key] = if identity[key].is_number() {
            json!(0)
        } else {
            json!("changed")
        };
        let sample: SampleIdentity = serde_json::from_value(identity).unwrap();
        let mut plan = h.plan.clone();
        plan.sample_identity = Some(sample);
        let root = h.temp.path().join(key);
        assert!(
            AcquisitionStore::create(
                &root,
                plan,
                MetadataLease {
                    schema: "OF1_METADATA_LEASE_1".into(),
                    authority: Authority::Fixture,
                    budget: StageBudget {
                        max_requests: 6,
                        max_response_entity_bytes_total: 12_000_000,
                        max_runtime_ms: 60_000
                    }
                },
                HistoricalFixtureClock
            )
            .is_err()
        );
        assert!(!root.exists());
    }
}
