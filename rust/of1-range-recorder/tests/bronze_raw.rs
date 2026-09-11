//! Sealed synthetic Raw publications only. No socket, provider, lease approval or Pump decode.
#![cfg(feature = "monitor")]

use of1_range_recorder::{
    FormatSource, RECORD_BYTES, SLOTS_PER_EPOCH,
    acquisition::derive_payload_from_metadata,
    acquisition_http::parse_response_head,
    bronze_preparation::{Limits, inspect_raw, render_html},
    durable::{
        Clock, ClockSample, StoreResult,
        acquisition::{
            AGGREGATE_SCHEMA, AcquisitionStore, AggregateBudget, AggregatePlan, Authority,
            MetadataLease, PayloadLease, RequestKind, StageBudget, current_executable_sha256,
        },
    },
    sha256,
};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
};

const SLOT: u64 = 422_496_000;
const OFFSET: u64 = 4096;

#[derive(Clone)]
struct FixedClock;
impl Clock for FixedClock {
    fn sample(&self) -> StoreResult<ClockSample> {
        Ok(ClockSample {
            wall_ms: 100_000,
            boot_ms: 10_000,
            boot_id: "B5_OFFLINE_FIXTURE".into(),
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
    hex::decode(vector()["sections_hex"].as_str().unwrap()).unwrap()
}

struct Harness {
    _temp: tempfile::TempDir,
    root: PathBuf,
    plan: AggregatePlan,
    store: AcquisitionStore<FixedClock>,
}
impl Harness {
    fn new() -> Self {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("run");
        let plan = AggregatePlan {
            schema: AGGREGATE_SCHEMA.into(),
            epoch: 978,
            format_source: FormatSource::pinned(),
            code_sha: "a".repeat(40),
            toolchain_fingerprint: "b".repeat(64),
            executable_sha256: current_executable_sha256().unwrap(),
            budget: AggregateBudget {
                max_slots: 2,
                max_plan_bytes: 131_072,
                max_requests: 8,
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
            FixedClock,
        )
        .unwrap();
        Self {
            _temp: temp,
            root,
            plan,
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
        let mut index = vec![0; usize::try_from(SLOTS_PER_EPOCH * RECORD_BYTES).unwrap()];
        index[..8].copy_from_slice(&OFFSET.to_le_bytes());
        index[8..12].copy_from_slice(&u32::try_from(payload().len()).unwrap().to_le_bytes());
        self.publish(0, &index);
        self.publish(1, format!("{} epoch-978.car\n", "0".repeat(64)).as_bytes());
        self.publish(
            2,
            format!("{}\n", vector()["root_cid_base32"].as_str().unwrap()).as_bytes(),
        );
        self.publish(3, &[]);
    }
    fn admit(&mut self) {
        let metadata = (0..4)
            .map(|n| self.store.published(n).unwrap().unwrap())
            .collect::<Vec<_>>();
        let prepared = derive_payload_from_metadata(&self.plan, &metadata, SLOT, SLOT + 1).unwrap();
        let lease = PayloadLease {
            schema: "OF1_PAYLOAD_LEASE_1".into(),
            authority: Authority::Fixture,
            budget: StageBudget {
                max_requests: 2,
                max_response_entity_bytes_total: 4096,
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

#[test]
fn metadata_only_is_not_a_transaction_dataset_and_reader_takes_no_writer_lock() {
    let mut h = Harness::new();
    h.metadata();
    let before = inventory(&h.root);
    // The writer is deliberately still alive and locked. This is an independent
    // forensic read, not AcquisitionStore::resume with a different executable.
    let a = inspect_raw(&h.root, Limits::default()).unwrap();
    let b = inspect_raw(&h.root, Limits::default()).unwrap();
    assert_eq!(a.input_kind, "METADATA_ONLY");
    assert_eq!(a.source_evidence, "Fixture");
    assert_eq!(a.publications.len(), 4);
    assert!(a.selected_slots.is_empty());
    assert_eq!(a.decoded_transaction_count, None);
    assert_eq!(a.decoded_pump_event_count, None);
    assert!(!a.research_ready);
    assert_eq!(
        serde_json::to_vec(&a).unwrap(),
        serde_json::to_vec(&b).unwrap()
    );
    let html = render_html(&a).unwrap();
    assert!(html.contains("METADATA_ONLY"));
    assert!(html.contains("unavailable, not zero"));
    assert!(!html.contains("<script"));
    assert_eq!(html, render_html(&b).unwrap());
    assert_eq!(before, inventory(&h.root));
}

#[test]
fn sealed_raw_to_atomic_archival_envelope_and_visible_quality_is_deterministic() {
    let mut h = Harness::new();
    h.metadata();
    h.admit();
    h.publish(4, &payload());
    let before = inventory(&h.root);
    let report = inspect_raw(&h.root, Limits::default()).unwrap();
    let slot = &report.selected_slots[0];
    assert_eq!(slot.state, "ARCHIVAL_ENVELOPES_ONLY");
    assert_eq!(
        slot.assembled_sha256.as_deref(),
        Some(sha256(&payload()).as_str())
    );
    let facts = slot.inspection.as_ref().unwrap();
    assert_eq!(facts.archival_nodes.len(), 5);
    assert_eq!(facts.transaction_envelopes.len(), 1);
    let tx = &facts.transaction_envelopes[0];
    assert_eq!(tx.slot, SLOT.to_string());
    assert_eq!(tx.archival_ordinal, 0);
    assert_eq!(slot.ranges[0].car_offset, "4096");
    assert_eq!(slot.ranges[0].assembled_offset, "0");
    let data_span = &tx.data.data_span;
    assert_eq!(
        &payload()[data_span.offset..data_span.offset + data_span.length],
        b"hello"
    );
    assert_eq!(
        report.solana_transaction_decode,
        "UNAVAILABLE_NOT_IMPLEMENTED"
    );
    assert_eq!(report.pump_decode, "UNAVAILABLE_NOT_IMPLEMENTED");
    assert!(
        render_html(&report)
            .unwrap()
            .contains("1 opaque atomic archival transaction envelope(s)")
    );
    assert_eq!(
        serde_json::to_vec(&report).unwrap(),
        serde_json::to_vec(&inspect_raw(&h.root, Limits::default()).unwrap()).unwrap()
    );
    assert_eq!(before, inventory(&h.root));
}

#[test]
fn missing_metadata_and_planned_but_unpublished_payload_stay_explicitly_missing() {
    let mut h = Harness::new();
    let report = inspect_raw(&h.root, Limits::default()).unwrap();
    assert_eq!(report.missing_metadata_sequences, ["0", "1", "2", "3"]);
    h.metadata();
    h.admit();
    let report = inspect_raw(&h.root, Limits::default()).unwrap();
    assert_eq!(report.selected_slots[0].state, "GAP_NOT_PUBLISHED");
    assert!(report.selected_slots[0].inspection.is_none());
    assert_eq!(report.decoded_transaction_count, None);
}

#[test]
fn changed_raw_receipt_hash_fails_without_rewriting_or_partial_output() {
    let mut h = Harness::new();
    h.metadata();
    h.admit();
    h.publish(4, &payload());
    let path = h.root.join("published/0000000004/raw.bin");
    let mut bytes = fs::read(&path).unwrap();
    bytes[10] ^= 1;
    fs::write(&path, bytes).unwrap();
    let before = inventory(&h.root);
    assert!(inspect_raw(&h.root, Limits::default()).is_err());
    assert_eq!(before, inventory(&h.root));
}

#[test]
fn receipt_valid_but_invalid_car_is_quarantined_not_partially_promoted() {
    let mut h = Harness::new();
    h.metadata();
    h.admit();
    let mut bytes = payload();
    bytes[10] ^= 1;
    h.publish(4, &bytes);
    let report = inspect_raw(&h.root, Limits::default()).unwrap();
    assert_eq!(report.selected_slots[0].state, "QUARANTINED");
    assert!(report.selected_slots[0].inspection.is_none());
    assert!(report.selected_slots[0].quarantine_reason.is_some());
    assert_eq!(report.decoded_transaction_count, None);
}

#[test]
fn strict_bounded_reader_rejects_oversize_before_payload_assembly() {
    let mut h = Harness::new();
    h.metadata();
    h.admit();
    h.publish(4, &payload());
    let limits = Limits {
        max_slot_bytes: payload().len() - 1,
        ..Limits::default()
    };
    assert!(
        inspect_raw(&h.root, limits)
            .unwrap_err()
            .to_string()
            .contains("SLOT_BYTE_LIMIT")
    );
    assert!(
        inspect_raw(
            &h.root,
            Limits {
                max_slots: 0,
                ..Limits::default()
            }
        )
        .is_err()
    );
}

#[test]
fn forged_prepared_range_must_fail_metadata_rederivation_even_with_consistent_fixture_hashes() {
    let mut h = Harness::new();
    h.metadata();
    h.admit();
    let path = h.root.join("payload.json");
    let mut value: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    value["prepared"]["requests"][0]["kind"]["start"] = json!(4097);
    value["prepared"]["requests"][0]["kind"]["end_exclusive"] =
        json!(OFFSET + payload().len() as u64 + 1);
    // Match typed serialization; serde_json::Value's key order is not the contract.
    let prepared: of1_range_recorder::durable::acquisition::PreparedPayload =
        serde_json::from_value(value["prepared"].clone()).unwrap();
    value["lease"]["prepared_payload_sha256"] = json!(prepared.sha256().unwrap());
    let lease: PayloadLease = serde_json::from_value(value["lease"].clone()).unwrap();
    value["stage"]["lease_sha256"] = json!(sha256(&serde_json::to_vec(&lease).unwrap()));
    fs::write(path, serde_json::to_vec(&value).unwrap()).unwrap();
    assert!(
        inspect_raw(&h.root, Limits::default())
            .unwrap_err()
            .to_string()
            .contains("prepared payload differs")
    );
}

#[test]
fn html_is_static_and_escapes_source_text() {
    let mut h = Harness::new();
    h.metadata();
    let mut report = inspect_raw(&h.root, Limits::default()).unwrap();
    report.run_id = "<script>alert('not executed')</script>".into();
    let html = render_html(&report).unwrap();
    assert!(html.contains("&lt;script&gt;"));
    assert!(!html.contains("<script"));
    assert!(!html.contains("src=\"http"));
}
