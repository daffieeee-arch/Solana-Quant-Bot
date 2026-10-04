// Real dispatcher subprocesses; synthetic capture is unavailable in production.
use super::*;
use of1_range_recorder::{
    acquisition_http::parse_response_head,
    b7,
    durable::{ClockSample, StoreResult, acquisition::RequestKind},
    sha256,
};
use serde_json::{Value, json};
use std::{
    fs,
    path::PathBuf,
    process::{Command, Stdio},
    time::{Duration, Instant},
};

struct Fixed(ClockSample);
impl Clock for Fixed {
    fn sample(&self) -> StoreResult<ClockSample> {
        Ok(self.0.clone())
    }
}
pub(super) fn capture(root: &str, plan: &str, lease: &str) -> Result<()> {
    let mut store = open(root, plan, lease)?;
    let raw_dir = PathBuf::from(std::env::var("OF1_CONTINUATION_TEST_RAW")?);
    let mode = std::env::var("OF1_CONTINUATION_TEST_MODE").unwrap_or_default();
    let start = if store.prepared_payload().is_some() {
        4
    } else {
        0
    };
    let end = if start == 4 { 20 } else { 4 };
    for seq in start..end {
        if store.published(seq)?.is_some() {
            continue;
        }
        let request = store.request(seq)?.clone();
        let raw = fs::read(raw_dir.join(format!("{seq}.bin")))?;
        let header = match request.kind {
            RequestKind::CarRange {
                start,
                end_exclusive,
                total,
                ..
            } => format!(
                "HTTP/1.1 206 Partial Content\r\nContent-Length: {}\r\nContent-Range: bytes {start}-{}/{total}\r\nETag: \"fixture\"\r\n\r\n",
                raw.len(),
                end_exclusive - 1
            ),
            _ => format!(
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nETag: \"fixture\"\r\n\r\n",
                if seq == 3 { 1_048_576 } else { raw.len() }
            ),
        };
        let permit = store.reserve(seq)?;
        store.begin_stream(&permit, parse_response_head(header.as_bytes(), &request)?)?;
        for chunk in raw.chunks(65_536) {
            store.append_stream(&permit, chunk)?;
        }
        store.finish_stream(permit)?;
        if mode == "ONE" {
            break;
        }
    }
    print(&store.progress()?)
}
struct Case {
    dir: tempfile::TempDir,
    root: PathBuf,
    plan: AggregatePlan,
    old_lease: String,
    old: std::collections::BTreeMap<String, String>,
}
fn snapshot(root: &Path) -> std::collections::BTreeMap<String, String> {
    fn visit(root: &Path, path: &Path, files: &mut std::collections::BTreeMap<String, String>) {
        for entry in fs::read_dir(path).unwrap() {
            let p = entry.unwrap().path();
            if p.is_dir() {
                visit(root, &p, files);
            } else {
                files.insert(
                    p.strip_prefix(root).unwrap().to_str().unwrap().into(),
                    sha256(&fs::read(&p).unwrap()),
                );
            }
        }
    }
    let mut files = std::collections::BTreeMap::new();
    visit(root, root, &mut files);
    files
}
fn fixture_plan(sample: &of1_range_recorder::sample::SampleIdentity) -> AggregatePlan {
    AggregatePlan {
        schema: AGGREGATE_SCHEMA.into(),
        epoch: 978,
        format_source: FormatSource::pinned(),
        code_sha: "a".repeat(40),
        toolchain_fingerprint: "b".repeat(64),
        executable_sha256: current_executable_sha256().unwrap(),
        sample_identity: Some(sample.clone()),
        clock_policy: Some(ClockPolicy::standard()),
        download_rate: Some(of1_range_recorder::rate::DownloadRate::standard()),
        budget: AggregateBudget {
            max_slots: 16,
            max_plan_bytes: 1_048_576,
            max_requests: 60,
            max_response_entity_bytes: 16_777_216,
            max_total_response_entity_bytes: 134_217_728,
            max_disk_bytes: 268_435_456,
            required_free_disk_bytes: 1,
            max_memory_bytes: 536_870_912,
            max_runtime_ms: 1_800_000,
            response_timeout_ms: 30_000,
            request_retries: 2,
        },
    }
}

impl Case {
    fn new() -> Self {
        let dir = tempfile::tempdir().unwrap();
        let campaign = dir.path().join("campaign");
        let root = campaign.join("runs/w00");
        let sample = b7::sample(0, &campaign).unwrap();
        let plan = fixture_plan(&sample);
        let old = ClockSample {
            wall_ms: 100_000,
            boot_ms: 10_000,
            boot_id: "METADATA_CONTINUATION_SYNTHETIC_BOOT".into(),
        };
        let mut store = AcquisitionStore::create(
            &root,
            plan.clone(),
            MetadataLease {
                schema: "OF1_METADATA_LEASE_1".into(),
                authority: Authority::Fixture,
                budget: metadata_budget(false),
            },
            Fixed(old.clone()),
        )
        .unwrap();
        let mut index = vec![0u8; 432_000 * 12];
        for i in 0..16 {
            let at = usize::try_from(sample.start_slot - 978 * 432_000 + i).unwrap() * 12;
            index[at..at + 8].copy_from_slice(&(4096 + i * 32).to_le_bytes());
            index[at + 8..at + 12].copy_from_slice(&32u32.to_le_bytes());
        }
        let raw = dir.path().join("raw");
        fs::create_dir(&raw).unwrap();
        fs::write(raw.join("0.bin"), &index).unwrap();
        fs::write(
            raw.join("1.bin"),
            format!("{} epoch-978.car\n", "0".repeat(64)),
        )
        .unwrap();
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../../../schemas/acquisition/of1/car-structural-fixture.json"
        ))
        .unwrap();
        fs::write(
            raw.join("2.bin"),
            format!("{}\n", fixture["root_cid_base32"].as_str().unwrap()),
        )
        .unwrap();
        fs::write(raw.join("3.bin"), []).unwrap();
        for i in 4..20 {
            fs::write(raw.join(format!("{i}.bin")), [42; 32]).unwrap();
        }
        let request = store.request(0).unwrap().clone();
        let permit = store.reserve(0).unwrap();
        let header = format!(
            "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nETag: \"fixture\"\r\n\r\n",
            index.len()
        );
        store
            .begin_stream(
                &permit,
                parse_response_head(header.as_bytes(), &request).unwrap(),
            )
            .unwrap();
        // Comparable complete retained response, deliberately without publication.
        for part in index.chunks(65_536) {
            store.append_stream(&permit, part).unwrap();
        }
        let old_lease = store.progress().unwrap().current_lease_sha256;
        drop(store);
        let now = ClockSample {
            wall_ms: old.wall_ms + 600_001,
            boot_ms: old.boot_ms + 600_001,
            boot_id: old.boot_id,
        };
        fs::write(
            dir.path().join("clock.json"),
            serde_json::to_vec(&now).unwrap(),
        )
        .unwrap();
        fs::write(
            dir.path().join("plan.json"),
            serde_json::to_vec(&plan).unwrap(),
        )
        .unwrap();
        let old = snapshot(&root);
        Self {
            dir,
            root,
            plan,
            old_lease,
            old,
        }
    }
    fn invoke(&self, args: &[String], mode: &str, ok: bool) -> Value {
        let input = self.dir.path().join("args.json");
        fs::write(&input, serde_json::to_vec(args).unwrap()).unwrap();
        let out = self.dir.path().join("stdout");
        let err = self.dir.path().join("stderr");
        let mut child = Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "continuation_tests::cli_child", "--nocapture"])
            .env("OF1_CONTINUATION_TEST_ARGS", input)
            .env(
                "OF1_CONTINUATION_TEST_CLOCK",
                self.dir.path().join("clock.json"),
            )
            .env("OF1_METADATA_CONTINUATION_TEST", "1")
            .env("OF1_CONTINUATION_TEST_RAW", self.dir.path().join("raw"))
            .env("OF1_CONTINUATION_TEST_MODE", mode)
            .stdout(Stdio::from(fs::File::create(&out).unwrap()))
            .stderr(Stdio::from(fs::File::create(&err).unwrap()))
            .spawn()
            .unwrap();
        let deadline = Instant::now() + Duration::from_secs(30);
        while child.try_wait().unwrap().is_none() {
            if Instant::now() > deadline {
                child.kill().unwrap();
                child.wait().unwrap();
                panic!("metadata CLI bound exceeded");
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        let status = child.wait().unwrap();
        let stdout = fs::read_to_string(out).unwrap();
        let stderr = fs::read_to_string(err).unwrap();
        assert_eq!(status.success(), ok, "{}: {stdout} {stderr}", args[0]);
        if !ok {
            assert!(stderr.contains("OF1_STOP:"));
            return Value::Null;
        }
        serde_json::from_str(&stdout[stdout.find('{').unwrap()..=stdout.rfind('}').unwrap()])
            .unwrap()
    }
    fn command(&self, op: &str, lease: &str, extra: &[String], ok: bool) -> Value {
        let mut args = vec![
            op.into(),
            self.root.display().to_string(),
            self.dir.path().join("plan.json").display().to_string(),
            lease.into(),
        ];
        args.extend_from_slice(extra);
        self.invoke(&args, "", ok)
    }
    fn proposal(&self) -> Value {
        self.command("metadata-continuation-proposal", &self.old_lease, &[], true)
    }
    fn admit(&self, a: &Value, ok: bool) -> Value {
        let p = self.dir.path().join("approval.json");
        fs::write(&p, serde_json::to_vec(a).unwrap()).unwrap();
        self.command(
            "metadata-continuation-admit",
            &self.old_lease,
            &[p.display().to_string()],
            ok,
        )
    }
    fn unchanged(&self) {
        let current = snapshot(&self.root);
        for (p, h) in &self.old {
            assert_eq!(current.get(p), Some(h), "{p}");
        }
    }
}
#[test]
fn cli_metadata_continuation_preserves_charges_and_proceeds_to_payload_reader() {
    let c = Case::new();
    c.command("capture-stage", &c.old_lease, &[], false); // expired original authority
    let p = c.proposal();
    assert_eq!(
        p["approval"]["binding"]["remaining_budget"]["max_requests"],
        11
    );
    assert_eq!(
        p["approval"]["binding"]["remaining_budget"]["max_response_entity_bytes_total"],
        10_392_576u64
    );
    for field in [
        "previous_ledger_sha256",
        "expected_index_sha256",
        "continuation_executable_sha256",
    ] {
        let mut a = p["approval"].clone();
        a["binding"][field] = json!("f".repeat(64));
        c.admit(&a, false);
    }
    let mut wrong = p["approval"].clone();
    wrong["binding"]["sample_identity"]["b7"]["cohort_role"] = json!("RESERVED_EVALUATION");
    c.admit(&wrong, false);
    let admitted = c.admit(&p["approval"], true);
    let lease = admitted["current_lease_sha256"].as_str().unwrap();
    c.admit(&p["approval"], false);
    c.command("capture-stage", &c.old_lease, &[], false);
    let end = c.command("capture-stage", lease, &[], true);
    assert_eq!(end["attempts_reserved"], 5);
    assert_eq!(end["charged_entity_bytes"], 10_376_192u64);
    c.unchanged();
    let sample = c.plan.sample_identity.as_ref().unwrap();
    let prepared = c.command(
        "prepare-payload",
        lease,
        &[
            sample.start_slot.to_string(),
            sample.end_slot_exclusive.to_string(),
        ],
        true,
    );
    let prep = c.dir.path().join("prepared.json");
    fs::write(&prep, serde_json::to_vec(&prepared).unwrap()).unwrap();
    let prepared: PreparedPayload = serde_json::from_value(prepared).unwrap();
    let payload = PayloadLease {
        schema: "OF1_PAYLOAD_LEASE_1".into(),
        authority: Authority::Fixture,
        budget: StageBudget {
            max_requests: 48,
            max_response_entity_bytes_total: 99_642_069,
            max_runtime_ms: 1_200_000,
        },
        prepared_payload_sha256: prepared.sha256().unwrap(),
        metadata_receipt_sha256: prepared.metadata_receipt_sha256().into(),
    };
    let payload_path = c.dir.path().join("payload-lease.json");
    fs::write(&payload_path, serde_json::to_vec(&payload).unwrap()).unwrap();
    let admitted = c.command(
        "payload-admit",
        lease,
        &[
            payload_path.display().to_string(),
            prep.display().to_string(),
        ],
        true,
    );
    let payload_hash = admitted["current_lease_sha256"].as_str().unwrap();
    c.command("capture-stage", payload_hash, &[], true);
    let context = of1_range_recorder::monitor::read_run_context(&c.root).unwrap();
    assert_eq!(context.published.len(), 20);
    assert_eq!(context.aggregate_plan, c.plan);
    assert_eq!(
        of1_range_recorder::recorded_verification::bindings(&context).unwrap()["metadata_continuation_sha256"],
        sha256(&fs::read(c.root.join("metadata-continuation.json")).unwrap())
    );
    assert!(
        context
            .snapshot
            .artifacts
            .iter()
            .any(|a| a.path == "metadata-continuation.json")
    );
    c.unchanged();
}
#[test]
fn cli_metadata_continuation_conflicting_retry_is_terminal_and_charged() {
    let c = Case::new();
    let p = c.proposal();
    let admitted = c.admit(&p["approval"], true);
    let lease = admitted["current_lease_sha256"].as_str().unwrap();
    let raw = c.dir.path().join("raw/0.bin");
    let mut bytes = fs::read(&raw).unwrap();
    bytes[0] ^= 1;
    fs::write(raw, bytes).unwrap();
    c.command("capture-stage", lease, &[], false);
    assert!(c.root.join("pending/quarantine.json").exists());
    assert_eq!(fs::read_dir(c.root.join("attempts")).unwrap().count(), 2);
    assert_eq!(fs::read_dir(c.root.join("published")).unwrap().count(), 0);
    c.command("capture-stage", lease, &[], false); // no extra attempt after conflict
    assert_eq!(fs::read_dir(c.root.join("attempts")).unwrap().count(), 2);
    c.unchanged();
}
