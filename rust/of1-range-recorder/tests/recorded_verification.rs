//! Sealed synthetic publications only: independent read-only CAR verification,
//! not historical acquisition, an authority renewal, or B5 domain decoding.

use of1_range_recorder::{
    FormatSource, RECORD_BYTES, SLOTS_PER_EPOCH,
    acquisition::derive_payload_from_metadata,
    acquisition_http::parse_response_head,
    durable::{
        Clock, ClockSample, StoreResult,
        acquisition::{
            AGGREGATE_SCHEMA, AcquisitionStore, AggregateBudget, AggregatePlan, Authority,
            MetadataLease, PayloadLease, PreparedPayload, RequestKind, StageBudget,
            current_executable_sha256,
        },
    },
    recorded_verification::verify_recorded,
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
    hex::decode(vector()["sections_hex"].as_str().unwrap()).unwrap()
}

struct Harness {
    temp: tempfile::TempDir,
    root: PathBuf,
    plan: AggregatePlan,
    store: AcquisitionStore<HistoricalFixtureClock>,
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
            HistoricalFixtureClock,
        )
        .unwrap();
        Self {
            temp,
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

fn rewrite_json(path: &Path, change: impl FnOnce(&mut Value)) {
    let mut value: Value = serde_json::from_slice(&fs::read(path).unwrap()).unwrap();
    change(&mut value);
    fs::write(path, serde_json::to_vec(&value).unwrap()).unwrap();
}

#[test]
fn expired_historical_fixture_is_read_without_writer_lock_or_source_changes() {
    let mut h = Harness::new();
    h.metadata();
    // Store remains alive and holds writer.lock. Its boot/deadline are historical.
    let before = inventory(&h.root);
    let first = verify_recorded(&h.root, None).unwrap();
    let second = verify_recorded(&h.root, None).unwrap();
    assert_eq!(first, second);
    assert_eq!(first["stages"]["capture"], "COMPLETE");
    assert_eq!(first["stages"]["raw_receipts"], "VERIFIED");
    assert_eq!(first["stages"]["car_slot"], "NOT_ACQUIRED");
    assert_eq!(first["stages"]["domain_decoding"], "NOT_PERFORMED");
    assert!(first["integrity"]["slots"].as_array().unwrap().is_empty());
    assert_eq!(before, inventory(&h.root));
}

#[test]
fn selected_unpublished_payload_remains_incomplete_not_verified_or_zero() {
    let mut h = Harness::new();
    h.metadata();
    h.admit();
    let before = inventory(&h.root);
    let report = verify_recorded(&h.root, None).unwrap();
    assert_eq!(report["stages"]["capture"], "INCOMPLETE");
    assert_eq!(report["stages"]["car_slot"], "INCOMPLETE");
    assert_eq!(report["stages"]["domain_decoding"], "NOT_PERFORMED");
    assert!(report["integrity"]["slots"].as_array().unwrap().is_empty());
    assert_eq!(before, inventory(&h.root));
}

#[test]
fn full_fixture_report_binds_receipt_bytes_and_all_archival_node_kinds() {
    let mut h = Harness::new();
    h.metadata();
    h.admit();
    h.publish(4, &payload());
    let before = inventory(&h.root);
    let report = verify_recorded(&h.root, None).unwrap();
    assert_eq!(report["stages"]["capture"], "COMPLETE");
    assert_eq!(report["stages"]["raw_receipts"], "VERIFIED");
    assert_eq!(report["stages"]["car_slot"], "VERIFIED");
    assert_eq!(report["stages"]["domain_decoding"], "NOT_PERFORMED");
    let bindings = &report["bindings"];
    assert_eq!(bindings["manifest_sha256"], before["run.json"]);
    assert_eq!(bindings["payload_manifest_sha256"], before["payload.json"]);
    let receipts = bindings["receipts"].as_array().unwrap();
    assert_eq!(receipts.len(), 5);
    for (sequence, receipt) in receipts.iter().enumerate() {
        let receipt_path = format!("published/{sequence:010}/receipt.json");
        let raw_path = format!("published/{sequence:010}/raw.bin");
        assert_eq!(receipt["sequence"], sequence);
        assert_eq!(receipt["path"], receipt_path);
        assert_eq!(receipt["sha256"], before[&receipt_path]);
        assert_eq!(receipt["raw_sha256"], before[&raw_path]);
        assert_eq!(
            receipt["raw_bytes"],
            fs::metadata(h.root.join(&raw_path)).unwrap().len()
        );
    }
    let slots = report["integrity"]["slots"].as_array().unwrap();
    assert_eq!(slots.len(), 1);
    assert_eq!(slots[0]["slot"], SLOT);
    assert_eq!(
        slots[0]["archival_node_counts"],
        json!({"transaction":1,"entry":1,"block":1,"rewards":1,"dataframe":1})
    );
    assert_eq!(slots[0]["report"]["verified_nodes"], 5);
    assert_eq!(slots[0]["report"]["root_to_slot_membership"], "UNAVAILABLE");
    assert_eq!(before, inventory(&h.root));
}

#[test]
fn corrupt_raw_receipt_and_aggregate_fail_without_mutating_source() {
    for target in ["raw", "receipt", "aggregate", "payload-binding"] {
        let mut h = Harness::new();
        h.metadata();
        h.admit();
        h.publish(4, &payload());
        match target {
            "raw" => {
                let path = h.root.join("published/0000000004/raw.bin");
                let mut bytes = fs::read(&path).unwrap();
                bytes[10] ^= 1;
                fs::write(path, bytes).unwrap();
            }
            "receipt" => rewrite_json(&h.root.join("published/0000000004/receipt.json"), |v| {
                v["sha256"] = json!("0".repeat(64));
            }),
            "aggregate" => rewrite_json(&h.root.join("run.json"), |v| {
                v["aggregate_sha256"] = json!("0".repeat(64));
            }),
            "payload-binding" => rewrite_json(&h.root.join("payload.json"), |v| {
                v["prepared"]["metadata_receipt_sha256"] = json!("0".repeat(64));
            }),
            _ => unreachable!(),
        }
        let before = inventory(&h.root);
        assert!(verify_recorded(&h.root, None).is_err(), "{target}");
        assert_eq!(before, inventory(&h.root), "{target}");
    }
}

#[test]
fn receipt_valid_but_invalid_car_is_quarantined_without_partial_slot_promotion() {
    let mut h = Harness::new();
    h.metadata();
    h.admit();
    let mut bytes = payload();
    bytes[10] ^= 1;
    h.publish(4, &bytes);
    let before = inventory(&h.root);
    let report = verify_recorded(&h.root, None).unwrap();
    assert_eq!(report["stages"]["capture"], "COMPLETE");
    assert_eq!(report["stages"]["raw_receipts"], "VERIFIED");
    assert_eq!(report["stages"]["car_slot"], "QUARANTINED");
    assert_eq!(report["stages"]["domain_decoding"], "NOT_PERFORMED");
    assert!(report["integrity"]["slots"].as_array().unwrap().is_empty());
    assert_eq!(report["integrity"]["error"], "CAR_CID_CONTENT_MISMATCH");
    assert_eq!(before, inventory(&h.root));
}

#[test]
fn self_consistent_rehashed_range_is_rejected_when_index_metadata_disagrees() {
    let mut h = Harness::new();
    h.metadata();
    h.admit();
    rewrite_json(&h.root.join("payload.json"), |v| {
        v["prepared"]["requests"][0]["kind"]["start"] = json!(OFFSET + 1);
        v["prepared"]["requests"][0]["kind"]["end_exclusive"] =
            json!(OFFSET + payload().len() as u64 + 1);
        let prepared: PreparedPayload = serde_json::from_value(v["prepared"].clone()).unwrap();
        v["lease"]["prepared_payload_sha256"] = json!(prepared.sha256().unwrap());
        let lease: PayloadLease = serde_json::from_value(v["lease"].clone()).unwrap();
        v["stage"]["lease_sha256"] = json!(sha256(&serde_json::to_vec(&lease).unwrap()));
    });
    let before = inventory(&h.root);
    assert!(verify_recorded(&h.root, None).is_err());
    assert_eq!(before, inventory(&h.root));
}

fn synthetic_history(h: &Harness) -> (PathBuf, PathBuf) {
    let manifest: Value =
        serde_json::from_slice(&fs::read(h.root.join("run.json")).unwrap()).unwrap();
    let payload_manifest: Value =
        serde_json::from_slice(&fs::read(h.root.join("payload.json")).unwrap()).unwrap();
    // Synthetic history exercises linkage only; it is not a replay of an authentic failure.
    // Both files deliberately live outside the immutable fixture run.
    let failure_path = h.temp.path().join("synthetic-prior-failure.json");
    let result_path = h.temp.path().join("synthetic-prior-result.json");
    let error = "CAR_CBOR_OR_ARCHIVAL_SCHEMA_INVALID";
    let executable_path = "/synthetic/old-acquisition-executable";
    let lease = payload_manifest["stage"]["lease_sha256"].as_str().unwrap();
    let raw_path = h.root.join("published/0000000004/raw.bin");
    let receipt_path = h.root.join("published/0000000004/receipt.json");
    fs::write(
        &failure_path,
        serde_json::to_vec(&json!({
            "executable_sha256":h.plan.executable_sha256,
            "network_calls":0,
            "message":format!("Error: Command failed: {executable_path} verify-payload {} /synthetic/aggregate.json {lease} /synthetic/prepared.json\nOF1_STOP: offline integrity quarantine: {error}\n",h.root.display())
        }))
        .unwrap(),
    )
    .unwrap();
    fs::write(
        &result_path,
        serde_json::to_vec(&json!({
            "run_id":manifest["run_id"],"root":h.root,
            "executable_path":executable_path,
            "executable_sha256":h.plan.executable_sha256,
            "raw":{"path":raw_path,"bytes":payload().len(),"sha256":sha256(&payload())},
            "receipt":{"path":receipt_path,"sha256":sha256(&fs::read(&receipt_path).unwrap())},
            "integrity":{"offline_exit_code":1,"error":error}
        }))
        .unwrap(),
    )
    .unwrap();
    (failure_path, result_path)
}

#[test]
fn separately_retained_failure_is_hash_bound_and_wrong_raw_history_fails_closed() {
    let mut h = Harness::new();
    h.metadata();
    h.admit();
    h.publish(4, &payload());
    let (failure_path, result_path) = synthetic_history(&h);
    let error = "CAR_CBOR_OR_ARCHIVAL_SCHEMA_INVALID";
    let before = inventory(&h.root);
    let failure_before = fs::read(&failure_path).unwrap();
    let result_before = fs::read(&result_path).unwrap();
    let report = verify_recorded(&h.root, Some((&failure_path, &result_path))).unwrap();
    assert_eq!(report["stages"]["car_slot"], "VERIFIED");
    assert_eq!(report["prior_failure"]["error"], error);
    assert_eq!(
        report["prior_failure"]["artifact_sha256"],
        sha256(&failure_before)
    );
    assert_eq!(
        report["prior_failure"]["run_result_sha256"],
        sha256(&result_before)
    );
    assert_eq!(
        report["prior_failure"]["status"],
        "HISTORICAL_FAILURE_PRESERVED"
    );
    assert_eq!(failure_before, fs::read(&failure_path).unwrap());
    assert_eq!(result_before, fs::read(&result_path).unwrap());
    for target in [
        "invocation-root",
        "receipt-path",
        "receipt-hash",
        "raw-path",
        "raw-bytes",
        "raw-hash",
    ] {
        // Restore only these test-created side reports, never alter captured fixture Raw.
        fs::write(&failure_path, &failure_before).unwrap();
        fs::write(&result_path, &result_before).unwrap();
        if target == "invocation-root" {
            rewrite_json(&failure_path, |v| {
                v["message"] = json!(
                    v["message"]
                        .as_str()
                        .unwrap()
                        .replace(h.root.to_str().unwrap(), "/synthetic/different-run")
                );
            });
        } else {
            rewrite_json(&result_path, |v| match target {
                "receipt-path" => v["receipt"]["path"] = json!("/synthetic/wrong-receipt"),
                "receipt-hash" => v["receipt"]["sha256"] = json!("0".repeat(64)),
                "raw-path" => v["raw"]["path"] = json!("/synthetic/wrong-raw"),
                "raw-bytes" => v["raw"]["bytes"] = json!(payload().len() + 1),
                "raw-hash" => v["raw"]["sha256"] = json!("0".repeat(64)),
                _ => unreachable!(),
            });
        }
        assert!(
            verify_recorded(&h.root, Some((&failure_path, &result_path))).is_err(),
            "{target}"
        );
    }
    assert_eq!(before, inventory(&h.root));
}

#[cfg(unix)]
#[test]
fn substituted_raw_symlink_or_fifo_is_rejected_without_opening_streams() {
    use std::os::unix::fs::symlink;
    for kind in ["symlink", "fifo"] {
        let mut h = Harness::new();
        h.metadata();
        h.admit();
        h.publish(4, &payload());
        let raw = h.root.join("published/0000000004/raw.bin");
        // Only the test-created fixture path is changed; authentic evidence is never used.
        fs::remove_file(&raw).unwrap();
        if kind == "symlink" {
            let retained = h.temp.path().join("retained-fixture.bin");
            fs::write(&retained, payload()).unwrap();
            symlink(retained, &raw).unwrap();
        } else {
            rustix::fs::mknodat(
                rustix::fs::CWD,
                &raw,
                rustix::fs::FileType::Fifo,
                rustix::fs::Mode::from_raw_mode(0o600),
                0,
            )
            .unwrap();
        }
        assert!(verify_recorded(&h.root, None).is_err(), "{kind}");
        let metadata = fs::symlink_metadata(raw).unwrap();
        if kind == "symlink" {
            assert!(metadata.is_symlink());
        } else {
            use std::os::unix::fs::FileTypeExt;
            assert!(metadata.file_type().is_fifo());
        }
    }
}
