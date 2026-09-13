//! Executed synthetic metadata -> TLS Raw -> restart -> separately admitted payload evidence.
//! Only numeric loopback is reachable; no approved/live authority or endpoint is accepted.

use of1_range_recorder::{
    FormatSource, RECORD_BYTES, SLOTS_PER_EPOCH,
    acquisition::{derive_payload_from_metadata, verify_payload},
    car::VerificationLimits,
    durable::{
        Clock, ClockSample, RetryComparison, StoreError, StoreResult,
        acquisition::{
            AGGREGATE_SCHEMA, AcquisitionStore, AggregateBudget, AggregatePlan, Authority,
            MetadataLease, PayloadLease, Progress, Published, Receipt, StageBudget,
            current_executable_sha256,
        },
    },
    https::{
        FixtureHttps, HttpsError, OF1_SERVER_NAME,
        fixture::{FixtureServer, ResponseScript},
    },
    sha256,
};
use serde_json::{Value, json};
use std::{
    error::Error,
    fmt::Write as _,
    fs,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    time::Instant,
};

type Result<T> = std::result::Result<T, Box<dyn Error>>;
const SLOT: u64 = 422_496_000;
const INDEX_BYTES: u64 = SLOTS_PER_EPOCH * RECORD_BYTES;
const OFFSET: u64 = 4096;
const BASELINE: &str = "aa21a5a3e5517741b4ae541d157dc6e7cd9a76a8";

struct FixtureClock(u64);
impl Clock for FixtureClock {
    fn sample(&self) -> StoreResult<ClockSample> {
        Ok(ClockSample {
            wall_ms: 100_000 + self.0,
            boot_ms: 10_000 + self.0,
            boot_id: "FIXTURE_ONLY:of1-acquisition-evidence".into(),
        })
    }
}

fn fixture() -> Result<Value> {
    Ok(serde_json::from_str(include_str!(
        "../../../../schemas/acquisition/of1/car-structural-fixture.json"
    ))?)
}

fn car_bytes() -> Result<Vec<u8>> {
    Ok(hex::decode(
        fixture()?["sections_hex"]
            .as_str()
            .ok_or("fixture bytes absent")?,
    )?)
}

fn cid_bytes() -> Result<Vec<u8>> {
    Ok(format!(
        "{}\n",
        fixture()?["root_cid_base32"]
            .as_str()
            .ok_or("fixture CID absent")?
    )
    .into_bytes())
}

fn index_bytes() -> Result<Vec<u8>> {
    let mut bytes = vec![0; usize::try_from(INDEX_BYTES)?];
    bytes[..8].copy_from_slice(&OFFSET.to_le_bytes());
    bytes[8..12].copy_from_slice(&u32::try_from(car_bytes()?.len())?.to_le_bytes());
    Ok(bytes)
}

fn plan() -> Result<AggregatePlan> {
    Ok(AggregatePlan {
        schema: AGGREGATE_SCHEMA.into(),
        sample_identity: None,
        download_rate: None,
        epoch: 978,
        format_source: FormatSource::pinned(),
        code_sha: BASELINE.into(),
        toolchain_fingerprint: sha256(
            b"FIXTURE_ONLY:Rust-1.97.1-declared-not-independent-attestation",
        ),
        executable_sha256: current_executable_sha256()?,
        budget: AggregateBudget {
            max_slots: 1,
            max_plan_bytes: 131_072,
            max_requests: 7,
            max_response_entity_bytes: INDEX_BYTES,
            max_total_response_entity_bytes: INDEX_BYTES + 16_384,
            max_disk_bytes: 64 * 1024 * 1024,
            required_free_disk_bytes: 64 * 1024 * 1024,
            max_memory_bytes: 256 * 1024 * 1024,
            max_runtime_ms: 120_000,
            response_timeout_ms: 10_000,
            request_retries: 1,
        },
    })
}

fn metadata_lease() -> MetadataLease {
    MetadataLease {
        schema: "OF1_METADATA_LEASE_1".into(),
        authority: Authority::Fixture,
        budget: StageBudget {
            max_requests: 5,
            max_response_entity_bytes_total: INDEX_BYTES + 12_288,
            max_runtime_ms: 60_000,
        },
    }
}

fn head(status: u16, length: u64, range: Option<(u64, u64, u64)>) -> Vec<u8> {
    let range = range.map_or_else(String::new, |(start, end, total)| {
        format!("Content-Range: bytes {start}-{}/{total}\r\n", end - 1)
    });
    format!("HTTP/1.1 {status} Fixture\r\nContent-Length: {length}\r\n{range}ETag: \"FIXTURE_ONLY-v1\"\r\nConnection: close\r\n\r\n").into_bytes()
}

fn capture(
    store: &mut AcquisitionStore<FixtureClock>,
    sequence: u64,
    header: Vec<u8>,
    body: Vec<u8>,
    measurements: &mut Vec<Value>,
) -> Result<std::result::Result<Receipt, HttpsError>> {
    let started = Instant::now();
    let request = store.request(sequence)?.clone();
    let server = FixtureServer::start(
        OF1_SERVER_NAME,
        vec![ResponseScript {
            header,
            body,
            fragment_bytes: 4093,
            delay_ms: 0,
            close_notify: true,
        }],
    )?;
    let result = FixtureHttps::new(server.port(), server.root_der())?.capture(store, sequence);
    let requests = server.finish()?;
    measurements.push(
        json!({"sequence":sequence,"method":request.method(),"path":request.path(978),
        "capture_elapsed_ms":started.elapsed().as_millis(),"published":result.is_ok(),
        "includes_fixture_setup_reservation_and_publication":true}),
    );
    if requests.len() != 1
        || !requests[0].starts_with(&format!(
            "{} {} HTTP/1.1\r\n",
            request.method(),
            request.path(978)
        ))
        || !requests[0].contains("Host: files.old-faithful.net\r\n")
        || !requests[0].contains("Accept-Encoding: identity\r\n")
    {
        return Err("unexpected sealed TLS fixture request".into());
    }
    if let of1_range_recorder::durable::acquisition::RequestKind::CarRange {
        start,
        end_exclusive,
        ..
    } = request.kind
        && (!requests[0].contains(&format!("Range: bytes={start}-{}\r\n", end_exclusive - 1))
            || !requests[0].contains("If-Match: \"FIXTURE_ONLY-v1\"\r\n"))
    {
        return Err("range identity or source precondition missing".into());
    }
    Ok(result)
}

fn logical_progress(progress: &Progress) -> Value {
    json!({ "stage":progress.stage,"attempts_reserved":progress.attempts_reserved,
        "charged_entity_bytes":progress.charged_entity_bytes,
        "published_response_entity_bytes":progress.published_response_entity_bytes,
        "published_requests":progress.published_requests,"unpublished_attempts":progress.unpublished_attempts,
        "unreceipted_response_entity_bytes":progress.unreceipted_response_entity_bytes,
        "deadline_wall_ms":progress.deadline_wall_ms,"deadline_boot_ms":progress.deadline_boot_ms,
        "evidence":progress.evidence,"domain_counts":progress.domain_counts })
}

fn crash_child(scratch: &Path) -> Result<()> {
    let mut operations = Vec::new();
    let mut store = AcquisitionStore::create(
        &scratch.join("capture"),
        plan()?,
        metadata_lease(),
        FixtureClock(0),
    )?;
    let index = index_bytes()?;
    capture(
        &mut store,
        0,
        head(200, INDEX_BYTES, None),
        index,
        &mut operations,
    )??;
    let declared = format!(
        "{}  epoch-978.car\n",
        sha256(b"FIXTURE_ONLY:declared-unverified-whole-CAR")
    );
    capture(
        &mut store,
        1,
        head(200, declared.len() as u64, None),
        declared.into_bytes(),
        &mut operations,
    )??;
    let cid = cid_bytes()?;
    if !matches!(
        capture(
            &mut store,
            2,
            head(200, cid.len() as u64, None),
            cid[..11].to_vec(),
            &mut operations,
        )?,
        Err(HttpsError::Truncated)
    ) {
        return Err("expected deliberate short CID response".into());
    }
    let progress = store.progress()?;
    if progress.attempts_reserved != 3
        || progress.published_requests != 2
        || progress.charged_entity_bytes != INDEX_BYTES + 8192
        || store.published(2)?.is_some()
    {
        return Err("pre-exit exact reservation/publication mismatch".into());
    }
    fs::write(
        scratch.join("child-progress.json"),
        serde_json::to_vec_pretty(&progress)?,
    )?;
    fs::write(
        scratch.join("child-operations.json"),
        serde_json::to_vec_pretty(&operations)?,
    )?;
    fs::write(
        scratch.join("metadata-lease-sha256.txt"),
        store.current_lease_sha256(),
    )?;
    // Actual process loss while the store/writer lock are alive: no Rust destructors.
    // This is not an assertion about physical power loss or fsync behaviour of a real device.
    std::process::exit(86);
}

fn metadata(store: &AcquisitionStore<FixtureClock>) -> Result<Vec<Published>> {
    (0..4)
        .map(|sequence| {
            store
                .published(sequence)?
                .ok_or_else(|| "metadata publication missing".into())
        })
        .collect()
}

// One deliberately linear fixture story keeps the causal assertions next to each operation.
#[allow(clippy::too_many_lines)]
fn execute(parent: &Path) -> Result<Value> {
    let started = Instant::now();
    let scratch = parent.join("of1-acquisition-fixture");
    fs::create_dir(&scratch)?;
    let status = Command::new(std::env::current_exe()?)
        .arg("--crash-child")
        .arg(&scratch)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .status()?;
    if status.code() != Some(86) {
        return Err("fixture child missed crash seam".into());
    }
    let lease_sha256 = fs::read_to_string(scratch.join("metadata-lease-sha256.txt"))?;
    let plan = plan()?;
    let mut store = AcquisitionStore::resume(
        &scratch.join("capture"),
        &plan,
        &lease_sha256,
        FixtureClock(100),
    )?;
    let before = store.progress()?;
    let child: Value = serde_json::from_slice(&fs::read(scratch.join("child-progress.json"))?)?;
    let mut operations: Vec<Value> =
        serde_json::from_slice(&fs::read(scratch.join("child-operations.json"))?)?;
    let original = store.published(0)?.ok_or("full index lost on restart")?;
    if logical_progress(&before)["attempts_reserved"] != child["attempts_reserved"]
        || before.deadline_wall_ms
            != child["deadline_wall_ms"]
                .as_u64()
                .ok_or("child deadline absent")?
        || before.deadline_boot_ms
            != child["deadline_boot_ms"]
                .as_u64()
                .ok_or("child deadline absent")?
        || before.published_requests != 2
        || fs::read(&original.raw_path)? != index_bytes()?
        || store.request(4).is_ok()
    {
        return Err("metadata crash/restart or payload gate mismatch".into());
    }
    let cid = cid_bytes()?;
    let retry = capture(
        &mut store,
        2,
        head(200, cid.len() as u64, None),
        cid,
        &mut operations,
    )??;
    if retry.retry_comparison != RetryComparison::RetainedOverlapMatched {
        return Err("retained CID prefix not matched".into());
    }
    let car = car_bytes()?;
    let object_size = OFFSET + car.len() as u64 + 4096;
    capture(
        &mut store,
        3,
        head(200, object_size, None),
        Vec::new(),
        &mut operations,
    )??;
    let after_metadata = store.progress()?;
    if after_metadata.attempts_reserved != 5
        || after_metadata.published_requests != 4
        || after_metadata.charged_entity_bytes != INDEX_BYTES + 12_288
        || after_metadata.deadline_wall_ms != before.deadline_wall_ms
        || after_metadata.deadline_boot_ms != before.deadline_boot_ms
        || store.published(0)?.ok_or("index vanished")?.receipt != original.receipt
    {
        return Err("metadata progression or original index receipt changed".into());
    }
    let prepared = derive_payload_from_metadata(&plan, &metadata(&store)?, SLOT, SLOT + 1)?;
    if prepared.requests().len() != 1
        || prepared.start_slot() != SLOT
        || prepared.end_slot() != SLOT + 1
    {
        return Err("unexpected selected-slot inventory".into());
    }
    store.admit_payload(
        PayloadLease {
            schema: "OF1_PAYLOAD_LEASE_1".into(),
            authority: Authority::Fixture,
            budget: StageBudget {
                max_requests: 2,
                max_response_entity_bytes_total: (car.len() as u64) * 2,
                max_runtime_ms: 60_000,
            },
            prepared_payload_sha256: prepared.sha256()?,
            metadata_receipt_sha256: prepared.metadata_receipt_sha256().into(),
        },
        &prepared,
    )?;
    let payload = capture(
        &mut store,
        4,
        head(
            206,
            car.len() as u64,
            Some((OFFSET, OFFSET + car.len() as u64, object_size)),
        ),
        car.clone(),
        &mut operations,
    )??;
    let after = store.progress()?;
    let integrity = verify_payload(
        &store,
        &prepared,
        VerificationLimits {
            max_total_bytes: 16 * 1024 * 1024,
            max_section_bytes: 16 * 1024 * 1024,
            max_nodes: 4096,
            max_links: 16384,
        },
    )?;
    if after.attempts_reserved != 6
        || after.published_requests != 5
        || after.unpublished_attempts != 1
        || after.charged_entity_bytes != INDEX_BYTES + 12_288 + car.len() as u64
        || payload.sha256 != sha256(&car)
        || integrity.slots.len() != 1
        || integrity.slots[0].verified_nodes != 5
        || integrity.slots[0].verified_links != 4
        || integrity.whole_car_sha256_verified
    {
        return Err("payload publication or integrity mismatch".into());
    }
    let payload_lease = store.current_lease_sha256().to_owned();
    drop(store);
    let mut store = AcquisitionStore::resume(
        &scratch.join("capture"),
        &plan,
        &payload_lease,
        FixtureClock(200),
    )?;
    let published = store
        .published(4)?
        .ok_or("payload did not survive reopen")?;
    if published.receipt != payload || fs::read(&published.raw_path)? != car {
        return Err("reopened Raw/receipt mismatch".into());
    }
    if !matches!(store.reserve(4), Err(StoreError::AlreadyPublished)) {
        return Err("published payload admitted a repeated dispatch".into());
    }
    let mut receipt_hashes = Vec::new();
    for sequence in 0..5 {
        let receipt = store.published(sequence)?.ok_or("receipt absent")?.receipt;
        receipt_hashes.push(
            json!({"sequence":sequence,"receipt_sha256":sha256(&serde_json::to_vec(&receipt)?),
            "raw_sha256":receipt.sha256,"response_headers_sha256":receipt.response_headers_sha256,
            "response_entity_bytes":receipt.response_entity_bytes}),
        );
    }
    let measurements = json!({"schema":"OF1_ACQUISITION_FIXTURE_MEASUREMENTS_1","evidence":"Fixture",
        "not_committed_deterministic_evidence":true,"elapsed_ms":started.elapsed().as_millis(),
        "executable_sha256":plan.executable_sha256,"after_metadata":after_metadata,"final":after,
        "operation_measurements":operations,"receipt_hashes":receipt_hashes,
        "clock_warning":"DETERMINISTIC_FIXTURE_CLOCK_NOT_WALL_PERFORMANCE_PROOF; real transport Instant deadlines separately enforced",
        "child_peak_rss_bytes":child["peak_rss_bytes"],"child_disk_charge_bytes":child["disk_charge_bytes"]});
    fs::write(
        scratch.join("measurements.json"),
        serde_json::to_vec_pretty(&measurements)?,
    )?;
    Ok(
        json!({"schema":"OF1_ACQUISITION_FIXTURE_EVIDENCE_1","evidence":"Fixture",
        "authority":"FIXTURE","approved":false,"live_network_enabled":false,"b4_complete":false,
        "transport":"FEATURE_GATED_NUMERIC_LOOPBACK_TLS_HTTP_1_1","source_baseline":BASELINE,
        "code_toolchain_claim":"DECLARED_FIXTURE_CONTEXT_NOT_INDEPENDENT_BUILD_ATTESTATION",
        "same_executable_hash_verified_across_restart":true,
        "clock_evidence":"DETERMINISTIC_FIXTURE_CLOCK_NOT_WALL_PERFORMANCE_PROOF",
        "scenario":"FULL_INDEX_TLS_RAW_METADATA_SHORT_READ_PROCESS_EXIT_RESTART_RETRY_HEAD_EXPLICIT_PAYLOAD_ADMISSION_RANGE_TLS_RAW_CID_SLOT_VERIFY",
        "fixture_index_bytes":INDEX_BYTES,"fixture_index_sha256":sha256(&index_bytes()?),
        "fixture_car_section_bytes":car.len(),"fixture_car_sections_sha256":sha256(&car),
        "source_receipt":"schemas/acquisition/of1/car-source-receipt.json",
        "structural_fixture":"schemas/acquisition/of1/car-structural-fixture.json",
        "process_exit":{"code":86,"rust_destructors_ran":false,"physical_power_loss_proven":false},
        "metadata_inventory":[{"method":"GET","path":"/978/epoch-978-slot-ranges.raw"},
            {"method":"GET","path":"/978/epoch-978.sha256"},{"method":"GET","path":"/978/epoch-978.cid"},
            {"method":"HEAD","path":"/978/epoch-978.car"}],
        "before_retry_after_restart":logical_progress(&before),
        "after_metadata":logical_progress(&after_metadata),"after_payload":logical_progress(&after),
        "executed_checks":{"one_complete_synthetic_index":true,"failed_short_read_not_published":true,
            "payload_unavailable_before_separate_admission":true,"original_metadata_deadlines_preserved":true,
            "retained_retry_prefix_matched":true,"original_raw_receipt_preserved":true,
            "all_attempts_charged":true,"correct_tls_range_and_if_match":true,
            "receipt_bound_index_planning":true,"payload_receipt_survives_reopen":true},
        "retained_failed_prefix_bytes":11,"budget_metric":"response_entity_bytes","physical_wire_cap":false,
        "interrupted_actual_total":"UNAVAILABLE_AFTER_PROCESS_LOSS","integrity_slots":integrity.slots,
        "whole_car_sha256_verified":false,"root_to_slot_membership":"UNAVAILABLE",
        "domain_counts":"UNAVAILABLE_NOT_DECODED_IN_B4","edge_evaluation":"NOT_EVALUATED_ENGINEERING_FIXTURE",
        "measured_resources":"EXCLUDED_FROM_LOGICAL_ARTIFACT; saved in scratch measurements.json"}),
    )
}

fn markdown(value: &Value) -> Result<String> {
    let mut text = String::from(
        "# OF1 acquisition-path fixture evidence\n\n> **Document status: ACTIVE — FIXTURE ONLY.** Generated by an executed numeric-loopback TLS scenario. No provider, OF1 or historical-data request. B4 remains open; no acquisition run is authorized.\n\nA complete synthetic epoch index → durable reservation → TLS metadata Raw/receipt publication → short CID response → actual child-process exit → restart → matching retry → CAR HEAD → separately admitted payload → exact range TLS capture → offline CID and selected-slot link verification.\n\n",
    );
    writeln!(
        text,
        "- Full synthetic index: `{}` bytes; SHA-256 `{}`.\n- Complete synthetic CAR sections: `{}` bytes; SHA-256 `{}`.\n- Source baseline: `{}`; fixture-declared code/toolchain context is not independent build attestation. The actual executable hash is checked across restart and recorded only in run-local measurements.",
        value["fixture_index_bytes"],
        value["fixture_index_sha256"]
            .as_str()
            .ok_or("hash absent")?,
        value["fixture_car_section_bytes"],
        value["fixture_car_sections_sha256"]
            .as_str()
            .ok_or("hash absent")?,
        BASELINE
    )?;
    text.push_str("\n| Result | Restart before retry | Metadata complete | Payload complete |\n|---|---:|---:|---:|\n");
    for (label, key) in [
        ("Durable attempts", "attempts_reserved"),
        ("Charged entity allowance", "charged_entity_bytes"),
        ("Published entity bytes", "published_response_entity_bytes"),
        ("Published requests", "published_requests"),
        ("Unpublished attempts", "unpublished_attempts"),
        ("Stage wall deadline", "deadline_wall_ms"),
        ("Stage boot deadline", "deadline_boot_ms"),
    ] {
        writeln!(
            text,
            "| {label} | {} | {} | {} |",
            value["before_retry_after_restart"][key],
            value["after_metadata"][key],
            value["after_payload"][key]
        )?;
    }
    text.push_str("\nThe child exits with code 86 while the writer is alive; no Rust destructors run. This demonstrates process-loss/restart, not physical power-loss durability. The eleven-byte interrupted CID prefix is retained; its full allowance stays charged, and the subsequent matching retry is charged again. The completed index is not fetched again. Metadata deadlines survive restart unchanged; explicitly admitted payload receives its separately bounded stage deadline without resetting spent aggregate bytes or attempts.\n\nExactly four metadata operations are defined: index GET, `.sha256` GET, `.cid` GET and CAR HEAD; no CAR entity is acquired in metadata. The CAR body is captured only after explicit fixture payload admission. Both TLS connectors share framing/storage logic; this test has only numeric-loopback authority and no production endpoint configuration.\n\nFive CID-rehashed structural nodes and four typed links form one selected-slot envelope. The declared whole-CAR SHA-256 is **not verified** by a partial range. Epoch-root-to-slot membership remains **UNAVAILABLE**. DataFrame checksums, transaction/metadata payloads and Pump events are not decoded. Domain counts remain `UNAVAILABLE_NOT_DECODED_IN_B4`; neither missing data nor uninterpreted payload is reported as zero.\n\nThe exact byte/source fixtures are [CAR structural fixture](../../schemas/acquisition/of1/car-structural-fixture.json) and [pinned structural source receipt](../../schemas/acquisition/of1/car-source-receipt.json). [Machine-readable logical evidence](../../schemas/acquisition/of1/acquisition-evidence.json) excludes wall-clock performance and build-dependent identities. Measured elapsed time, child/final peak RSS, disk charge and actual executable hash are emitted to the fresh scratch directory's `measurements.json`; they are host/run observations, not universal budget constants.\n\nEvidence stays **Fixture**, authentic source observation and historical activation remain unproven, and edge evaluation is **not performed**. A successful fixture is not completion of authentic B4, Research Ready status, executable economics or profitability.\n");
    text.push_str("\nThe displayed stage deadlines use a deterministic fixture clock and are not a wall-performance benchmark. The TLS path separately enforces its real `Instant` deadline. Per-operation measured elapsed times (including setup/reservation/publication) and revalidated Raw/header/receipt hashes are retained in run-local measurements, separate from this deterministic artifact.\n");
    Ok(text)
}

fn artifact_paths() -> (PathBuf, PathBuf) {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    (
        root.join("schemas/acquisition/of1/acquisition-evidence.json"),
        root.join("docs/research/OF1_ACQUISITION_EVIDENCE.md"),
    )
}

fn run() -> Result<()> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.len() != 2 {
        return Err("usage: of1-acquisition-fixture-evidence --json|--markdown|--check <fresh-scratch-parent>".into());
    }
    if args[0] == "--crash-child" {
        return crash_child(Path::new(&args[1]));
    }
    if !["--json", "--markdown", "--check"].contains(&args[0].as_str()) {
        return Err("unknown evidence mode".into());
    }
    let evidence = execute(Path::new(&args[1]))?;
    let json = format!("{}\n", serde_json::to_string_pretty(&evidence)?);
    let markdown = markdown(&evidence)?;
    let scratch = Path::new(&args[1]).join("of1-acquisition-fixture");
    fs::write(scratch.join("logical-evidence.json"), &json)?;
    fs::write(scratch.join("logical-evidence.md"), &markdown)?;
    match args[0].as_str() {
        "--json" => print!("{json}"),
        "--markdown" => print!("{markdown}"),
        _ => {
            let (json_path, markdown_path) = artifact_paths();
            if fs::read_to_string(json_path)? != json
                || fs::read_to_string(markdown_path)? != markdown
            {
                return Err(
                    "committed acquisition evidence does not match executed fixture".into(),
                );
            }
            println!("OF1 acquisition fixture evidence matches both committed artifacts");
        }
    }
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
