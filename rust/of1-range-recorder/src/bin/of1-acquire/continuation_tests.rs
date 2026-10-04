// Exact-source-pinned CLI harness. Synthetic clocks/transport exist only in this test binary.
use super::*;
use of1_range_recorder::{
    acquisition_http::parse_response_head,
    b7,
    campaign::{Guard, ProcessingApproval},
    durable::{ClockSample, FaultPoint, StoreResult, acquisition::RequestKind},
    sha256,
};
use serde_json::{Value, json};
use std::{
    fs,
    path::PathBuf,
    process::{Command, Stdio},
    time::{Duration, Instant},
};

pub(super) struct CliClock;
impl Clock for CliClock {
    fn sample(&self) -> StoreResult<ClockSample> {
        if let Ok(path) = std::env::var("OF1_CONTINUATION_TEST_CLOCK") {
            let bytes = fs::read(path)?;
            return serde_json::from_slice(&bytes)
                .map_err(|_| of1_range_recorder::durable::StoreError::Corrupt);
        }
        SystemClock.sample()
    }
}
pub(super) fn active() -> bool {
    std::env::var_os("OF1_CONTINUATION_TEST_CLOCK").is_some()
}
#[test]
fn cli_child() {
    let Ok(file) = std::env::var("OF1_CONTINUATION_TEST_ARGS") else {
        return;
    };
    let args: Vec<String> = read(&file).unwrap();
    if let Err(error) = run(&args) {
        eprintln!("OF1_STOP: {error}");
        std::process::exit(1);
    }
}
fn advance(ms: u64) -> Result<()> {
    let mut at = CliClock.sample()?;
    at.boot_ms += ms;
    at.wall_ms += ms;
    fs::write(
        std::env::var("OF1_CONTINUATION_TEST_CLOCK")?,
        serde_json::to_vec(&at)?,
    )?;
    Ok(())
}
pub(super) fn capture(root: &str, plan: &str, lease: &str) -> Result<()> {
    if std::env::var_os("OF1_METADATA_CONTINUATION_TEST").is_some() {
        return metadata_continuation_tests::capture(root, plan, lease);
    }
    let mut store = open(root, plan, lease)?;
    let mode = std::env::var("OF1_CONTINUATION_TEST_MODE").unwrap_or_default();
    for sequence in 17..20 {
        if store.published(sequence)?.is_some() {
            continue;
        }
        let wait = store.continuation_wait_ms()?;
        if mode != "NO_WAIT" {
            advance(wait)?;
        }
        let request = store.request(sequence)?.clone();
        if mode == "BEFORE_RESERVE" {
            store.inject_fault(FaultPoint::BeforeReservationPublish);
        }
        if mode == "AFTER_RESERVE" {
            store.inject_fault(FaultPoint::AfterReservationPublish);
        }
        let permit = store.reserve(sequence)?;
        if mode == "429" {
            store.reject_response(
                &permit,
                b"HTTP/1.1 429 Too Many Requests\r\nContent-Length: 0\r\n\r\n",
                "HTTP_REJECTED",
            )?;
            store.stop_payload_continuation()?;
            return Err("synthetic 429 retained".into());
        }
        let file = PathBuf::from(std::env::var("OF1_CONTINUATION_TEST_RAW")?)
            .join(format!("{sequence}.bin"));
        let raw = fs::read(file)?;
        let RequestKind::CarRange {
            start,
            end_exclusive,
            total,
            ..
        } = request.kind
        else {
            return Err("not range".into());
        };
        let header = format!(
            "HTTP/1.1 206 Partial Content\r\nContent-Length: {}\r\nContent-Range: bytes {start}-{}/{total}\r\nETag: \"fixture\"\r\n\r\n",
            raw.len(),
            end_exclusive - 1
        );
        store.begin_stream(&permit, parse_response_head(header.as_bytes(), &request)?)?;
        for chunk in raw.chunks(65_536) {
            store.append_stream(&permit, chunk)?;
        }
        if mode == "BEFORE_PUBLISH" {
            store.inject_fault(FaultPoint::BeforePublish);
        }
        if mode == "AFTER_PUBLISH" {
            store.inject_fault(FaultPoint::AfterPublish);
        }
        store.finish_stream(permit)?;
        if mode == "ONE" {
            break;
        }
    }
    print(&store.progress()?)
}

struct Fixed(ClockSample);
impl Clock for Fixed {
    fn sample(&self) -> StoreResult<ClockSample> {
        Ok(self.0.clone())
    }
}
struct Case {
    dir: tempfile::TempDir,
    root: PathBuf,
    plan: PathBuf,
    lease: String,
    old: std::collections::BTreeMap<String, String>,
}
fn snapshot(root: &Path) -> std::collections::BTreeMap<String, String> {
    fn visit(root: &Path, at: &Path, out: &mut std::collections::BTreeMap<String, String>) {
        for entry in fs::read_dir(at).unwrap() {
            let p = entry.unwrap().path();
            if p.is_dir() {
                visit(root, &p, out);
            } else {
                out.insert(
                    p.strip_prefix(root).unwrap().to_str().unwrap().into(),
                    sha256(&fs::read(&p).unwrap()),
                );
            }
        }
    }
    let mut out = std::collections::BTreeMap::new();
    visit(root, root, &mut out);
    out
}
fn publish(store: &mut AcquisitionStore<Fixed>, seq: u64, raw: &[u8]) {
    let req = store.request(seq).unwrap().clone();
    let header = match req.kind {
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
    let permit = store.reserve(seq).unwrap();
    store
        .begin_stream(
            &permit,
            parse_response_head(header.as_bytes(), &req).unwrap(),
        )
        .unwrap();
    for chunk in raw.chunks(65_536) {
        store.append_stream(&permit, chunk).unwrap();
    }
    store.finish_stream(permit).unwrap();
}
impl Case {
    #[allow(clippy::too_many_lines)] // One synthetic four-complete/one-interrupted campaign prefix.
    fn new() -> Self {
        let dir = tempfile::tempdir().unwrap();
        let campaign = dir.path().join("campaign");
        let at = SystemClock.sample().unwrap();
        // No dependency on runner uptime; only APPROVED-generator tests use real clocks.
        let old_at = ClockSample {
            wall_ms: 100_000,
            boot_ms: 10_000,
            boot_id: "CONTINUATION_SYNTHETIC_BOOT".into(),
        };
        let continuation_at = ClockSample {
            wall_ms: 1_900_000,
            boot_ms: 1_810_000,
            boot_id: old_at.boot_id.clone(),
        };
        fs::write(
            dir.path().join("clock.json"),
            serde_json::to_vec(&continuation_at).unwrap(),
        )
        .unwrap();
        let raw_dir = dir.path().join("raw");
        fs::create_dir(&raw_dir).unwrap();
        for seq in 17..20 {
            fs::write(raw_dir.join(format!("{seq}.bin")), [42; 32]).unwrap();
        }
        let mut last = None;
        for ordinal in 0..5 {
            let sample = b7::sample(ordinal, &campaign).unwrap();
            let root = campaign.join(format!("runs/w{ordinal:02}"));
            let plan = AggregatePlan {
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
            };
            let mut store = AcquisitionStore::create(
                &root,
                plan.clone(),
                MetadataLease {
                    schema: "OF1_METADATA_LEASE_1".into(),
                    authority: Authority::Fixture,
                    budget: metadata_budget(false),
                },
                Fixed(old_at.clone()),
            )
            .unwrap();
            let mut index = vec![0u8; 432_000 * 12];
            for i in 0..16 {
                let pos = usize::try_from(sample.start_slot - 978 * 432_000 + i).unwrap() * 12;
                index[pos..pos + 8].copy_from_slice(&(4096 + i * 32).to_le_bytes());
                index[pos + 8..pos + 12].copy_from_slice(&32u32.to_le_bytes());
            }
            publish(&mut store, 0, &index);
            publish(
                &mut store,
                1,
                format!("{} epoch-978.car\n", "0".repeat(64)).as_bytes(),
            );
            let fixture: Value = serde_json::from_str(include_str!(
                "../../../../../schemas/acquisition/of1/car-structural-fixture.json"
            ))
            .unwrap();
            publish(
                &mut store,
                2,
                format!("{}\n", fixture["root_cid_base32"].as_str().unwrap()).as_bytes(),
            );
            publish(&mut store, 3, &[]);
            let metadata = (0..4)
                .map(|n| store.published(n).unwrap().unwrap())
                .collect::<Vec<_>>();
            let prepared = derive_payload_from_metadata(
                &plan,
                &metadata,
                sample.start_slot,
                sample.end_slot_exclusive,
            )
            .unwrap();
            store
                .admit_payload(
                    PayloadLease {
                        schema: "OF1_PAYLOAD_LEASE_1".into(),
                        authority: Authority::Fixture,
                        budget: StageBudget {
                            max_requests: 48,
                            max_response_entity_bytes_total: 68_136_183,
                            max_runtime_ms: 1_200_000,
                        },
                        prepared_payload_sha256: prepared.sha256().unwrap(),
                        metadata_receipt_sha256: prepared.metadata_receipt_sha256().into(),
                    },
                    &prepared,
                )
                .unwrap();
            for seq in 4..if ordinal == 4 { 17 } else { 20 } {
                publish(&mut store, seq, &[42; 32]);
            }
            let lease = store.progress().unwrap().current_lease_sha256;
            if ordinal == 4 {
                let permit = store.reserve(17).unwrap();
                store
                    .reject_response(
                        &permit,
                        b"HTTP/1.1 429 Too Many Requests\r\nContent-Length: 0\r\n\r\n",
                        "HTTP_REJECTED",
                    )
                    .unwrap();
                drop(store);
                let p = dir.path().join("aggregate.json");
                fs::write(&p, serde_json::to_vec(&plan).unwrap()).unwrap();
                last = Some((root, p, lease));
            } else {
                drop(store);
                let hash = "e".repeat(64);
                let exe = current_executable_sha256().unwrap();
                Guard::admit_processing(
                    &sample,
                    &root,
                    ProcessingApproval {
                        authority: Authority::Fixture,
                        window: ordinal as u64,
                        plan_sha256: hash.clone(),
                        worker_sha256s: vec![exe; 3],
                        evaluation: None,
                    },
                    &at,
                )
                .unwrap();
                Guard::output(
                    &sample,
                    &root,
                    &campaign.join(format!("work/w{ordinal:02}")),
                    &hash,
                    0,
                )
                .unwrap()
                .complete_processing(ordinal as u64)
                .unwrap();
            }
        }
        let (root, plan, lease) = last.unwrap();
        let old = snapshot(&root);
        Self {
            dir,
            root,
            plan,
            lease,
            old,
        }
    }
    fn invoke(&self, op: &str, lease: &str, extra: Option<&Path>, mode: &str, ok: bool) -> Value {
        let mut args = vec![
            op.into(),
            self.root.display().to_string(),
            self.plan.display().to_string(),
            lease.into(),
        ];
        if let Some(p) = extra {
            args.push(p.display().to_string());
        }
        let input = self.dir.path().join("args.json");
        fs::write(&input, serde_json::to_vec(&args).unwrap()).unwrap();
        let out = self.dir.path().join("stdout");
        let err = self.dir.path().join("stderr");
        let mut child = Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "continuation_tests::cli_child", "--nocapture"])
            .env("OF1_CONTINUATION_TEST_ARGS", input)
            .env(
                "OF1_CONTINUATION_TEST_CLOCK",
                self.dir.path().join("clock.json"),
            )
            .env("OF1_CONTINUATION_TEST_RAW", self.dir.path().join("raw"))
            .env("OF1_CONTINUATION_TEST_MODE", mode)
            .stdout(Stdio::from(fs::File::create(&out).unwrap()))
            .stderr(Stdio::from(fs::File::create(&err).unwrap()))
            .spawn()
            .unwrap();
        let limit = Instant::now() + Duration::from_secs(30);
        while child.try_wait().unwrap().is_none() {
            if Instant::now() > limit {
                child.kill().unwrap();
                child.wait().unwrap();
                panic!("CLI bound exceeded");
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        let status = child.wait().unwrap();
        let stdout = fs::read_to_string(out).unwrap();
        let stderr = fs::read_to_string(err).unwrap();
        assert_eq!(status.success(), ok, "{op}: {stdout} {stderr}");
        if !ok {
            assert!(stderr.contains("OF1_STOP:"), "{stderr}");
            return Value::Null;
        }
        serde_json::from_str(&stdout[stdout.find('{').unwrap()..=stdout.rfind('}').unwrap()])
            .unwrap()
    }
    fn proposal(&self) -> Value {
        self.invoke("payload-continuation-proposal", &self.lease, None, "", true)
    }
    fn admit(&self, a: &Value, ok: bool) -> Value {
        let path = self.dir.path().join("approval.json");
        fs::write(&path, serde_json::to_vec(a).unwrap()).unwrap();
        self.invoke(
            "payload-continuation-admit",
            &self.lease,
            Some(&path),
            "",
            ok,
        )
    }
    fn preserve(&self) {
        for (p, h) in &self.old {
            assert_eq!(sha256(&fs::read(self.root.join(p)).unwrap()), *h, "{p}");
        }
    }
}
#[test]
fn cli_continuation_preserves_expired_history_and_completes_exactly_three() {
    let case = Case::new();
    let proposal = case.proposal();
    let approval = &proposal["approval"];
    for key in [
        "previous_ledger_sha256",
        "prepared_payload_sha256",
        "original_executable_sha256",
        "continuation_executable_sha256",
    ] {
        let mut bad = approval.clone();
        bad["binding"][key] = json!("0".repeat(64));
        case.admit(&bad, false);
        case.preserve();
    }
    let mut bad = approval.clone();
    bad["binding"]["requests"][0]["kind"]["start"] = json!(1);
    case.admit(&bad, false);
    let admitted = case.admit(approval, true);
    let lease = admitted["current_lease_sha256"].as_str().unwrap();
    case.admit(approval, false);
    case.invoke("capture-stage", lease, None, "NO_WAIT", false); // refusal consumes no attempt
    let before = case.invoke("progress", lease, None, "", true);
    assert_eq!(before["attempts_reserved"], 18);
    case.invoke("capture-stage", lease, None, "ONE", true);
    case.preserve();
    case.invoke("capture-stage", lease, None, "NO_WAIT", false);
    let end = case.invoke("capture-stage", lease, None, "", true);
    assert_eq!(end["attempts_reserved"], 21);
    assert_eq!(end["published_requests"], 20);
    case.preserve();
    #[cfg(feature = "monitor")]
    {
        let context = of1_range_recorder::monitor::read_run_context(&case.root).unwrap();
        assert_eq!(context.snapshot.operations.len(), 20);
    }
    let record: Value =
        serde_json::from_slice(&fs::read(case.root.join("continuation.json")).unwrap()).unwrap();
    assert_eq!(record["approval"], *approval);
    let mut previous = record["stage"]["started_at"]["boot_ms"].as_u64().unwrap();
    for attempt in 18..21 {
        let r: Value = serde_json::from_slice(
            &fs::read(case.root.join(format!("attempts/{attempt:010}.json"))).unwrap(),
        )
        .unwrap();
        assert!(r["at"]["boot_ms"].as_u64().unwrap() >= previous + 60_000);
        previous = r["at"]["boot_ms"].as_u64().unwrap();
    }
}
#[test]
fn cli_continuation_crashes_and_first_error_never_refund_or_repeat() {
    for mode in [
        "BEFORE_RESERVE",
        "AFTER_RESERVE",
        "BEFORE_PUBLISH",
        "AFTER_PUBLISH",
        "429",
    ] {
        let case = Case::new();
        let admitted = case.admit(&case.proposal()["approval"], true);
        let lease = admitted["current_lease_sha256"].as_str().unwrap();
        case.invoke("capture-stage", lease, None, mode, false);
        case.preserve();
        if mode == "AFTER_PUBLISH" {
            let end = case.invoke("capture-stage", lease, None, "", true);
            assert_eq!(end["attempts_reserved"], 21);
            assert_eq!(end["published_requests"], 20);
        } else {
            case.invoke("capture-stage", lease, None, "", false);
        }
        case.preserve();
    }
}
