//! A real paced loopback TLS download, interrupted response and explicit process restart.
//! All source content is synthetic. This executable cannot dispatch official acquisition.
use of1_range_recorder::{
    FormatSource, RECORD_BYTES, SLOTS_PER_EPOCH,
    acquisition::{derive_payload_from_metadata, read_limited, verify_payload},
    car::VerificationLimits,
    durable::{
        SystemClock,
        acquisition::{
            AcquisitionStore, AggregateBudget, AggregatePlan, Authority, MetadataLease,
            PayloadLease, StageBudget, current_executable_sha256,
        },
    },
    https::{
        FixtureHttps, HttpsError, OF1_SERVER_NAME,
        fixture::{FixtureServer, ResponseScript},
    },
    monitor::{Monitor, read_run_context},
    sha256,
};
use serde_json::Value;
use std::{
    error::Error,
    path::Path,
    process::{Command, Stdio},
};

type Result<T> = std::result::Result<T, Box<dyn Error>>;
const INDEX: u64 = SLOTS_PER_EPOCH * RECORD_BYTES;

fn plan() -> Result<AggregatePlan> {
    Ok(AggregatePlan {
        schema: "OF1_ACQUISITION_AGGREGATE_1".into(),
        sample_identity: None,
        epoch: 978,
        format_source: FormatSource::pinned(),
        code_sha: "79d1e9c828525d7eaf3efa5529d1d7b0952fb8cd".into(),
        toolchain_fingerprint: sha256(b"FIXTURE_ONLY:Rust-1.97.1-monitor"),
        executable_sha256: current_executable_sha256()?,
        budget: AggregateBudget {
            max_slots: 1,
            max_plan_bytes: 131_072,
            max_requests: 7,
            max_response_entity_bytes: INDEX,
            max_total_response_entity_bytes: INDEX + 16_384,
            max_disk_bytes: 64 * 1024 * 1024,
            required_free_disk_bytes: 64 * 1024 * 1024,
            max_memory_bytes: 256 * 1024 * 1024,
            max_runtime_ms: 180_000,
            response_timeout_ms: 60_000,
            request_retries: 1,
        },
    })
}

fn lease() -> MetadataLease {
    MetadataLease {
        schema: "OF1_METADATA_LEASE_1".into(),
        authority: Authority::Fixture,
        budget: StageBudget {
            max_requests: 5,
            max_response_entity_bytes_total: INDEX + 12_288,
            max_runtime_ms: 120_000,
        },
    }
}

fn fixture() -> Result<Value> {
    Ok(serde_json::from_str(include_str!(
        "../../../../schemas/acquisition/of1/car-structural-fixture.json"
    ))?)
}

fn payload_bytes() -> Result<Vec<u8>> {
    let fixture = fixture()?;
    let hex = fixture["sections_hex"]
        .as_str()
        .ok_or("fixture sections missing")?;
    (0..hex.len())
        .step_by(2)
        .map(|i| Ok(u8::from_str_radix(&hex[i..i + 2], 16)?))
        .collect()
}

fn body(sequence: u64, with_payload: bool) -> Result<Vec<u8>> {
    Ok(match sequence {
        0 => {
            let mut bytes = vec![0; usize::try_from(INDEX)?];
            bytes[..8].copy_from_slice(&4096_u64.to_le_bytes());
            let length = if with_payload {
                u32::try_from(payload_bytes()?.len())?
            } else {
                128
            };
            bytes[8..12].copy_from_slice(&length.to_le_bytes());
            bytes
        }
        1 => format!(
            "{}  epoch-978.car\n",
            sha256(b"LOCAL_SIMULATION:unverified-whole-CAR")
        )
        .into_bytes(),
        2 => {
            let fixture = fixture()?;
            format!(
                "{}\n",
                fixture["root_cid_base32"]
                    .as_str()
                    .ok_or("missing fixture CID")?
            )
            .into_bytes()
        }
        3 => Vec::new(),
        _ => return Err("unsupported fixture sequence".into()),
    })
}

fn capture(
    store: &mut AcquisitionStore<SystemClock>,
    sequence: u64,
    short: bool,
    with_payload: bool,
    monitor: &mut Monitor,
) -> Result<bool> {
    let full = body(sequence, with_payload)?;
    let length = if sequence == 3 {
        8192
    } else {
        full.len() as u64
    };
    let header =
        format!("HTTP/1.1 200 Fixture\r\nContent-Length: {length}\r\nConnection: close\r\n\r\n")
            .into_bytes();
    let server = FixtureServer::start_paced(
        OF1_SERVER_NAME,
        vec![ResponseScript {
            header,
            body: if short { full[..11].to_vec() } else { full },
            fragment_bytes: 8192,
            delay_ms: 100,
            close_notify: true,
        }],
        if sequence == 0 { 20 } else { 0 },
    )?;
    let result = FixtureHttps::new(server.port(), server.root_der())?
        .capture_observed(store, sequence, monitor);
    let requests = server.finish()?;
    if requests.len() != 1 {
        return Err("fixture request count changed".into());
    }
    if short {
        if !matches!(result, Err(HttpsError::Truncated)) {
            return Err("expected short fixture response".into());
        }
        return Ok(false);
    }
    result?;
    Ok(true)
}

fn stage(root: &Path, socket: &Path, restart: bool, with_payload: bool) -> Result<()> {
    let mut store = if restart {
        let manifest: Value =
            serde_json::from_slice(&read_limited(&root.join("run.json"), 1_048_576)?)?;
        let plan: AggregatePlan = serde_json::from_value(manifest["plan"].clone())?;
        let lease = manifest["metadata_stage"]["lease_sha256"]
            .as_str()
            .ok_or("lease missing")?;
        AcquisitionStore::resume(root, &plan, lease, SystemClock)?
    } else {
        AcquisitionStore::create(root, plan()?, lease(), SystemClock)?
    };
    if !matches!(store.authority(), Authority::Fixture) {
        return Err("simulation refuses live authority".into());
    }
    let context = read_run_context(root)?;
    let mut monitor = Monitor::new(context.snapshot, socket);
    monitor.snapshot.label = "Lokale TLS-simulatie · onderbreking + herstart".into();
    monitor.emit(true);
    for sequence in 0..4 {
        if store.published(sequence)?.is_some() {
            continue;
        }
        if !capture(
            &mut store,
            sequence,
            !restart && sequence == 2,
            with_payload,
            &mut monitor,
        )? {
            println!(
                "LOCAL_SIMULATION: interrupted CID after 11 bytes; durable reservation retained; process exits 86"
            );
            // Actual process loss: writer destructors do not run. Original run deadline remains.
            std::process::exit(86);
        }
    }
    let end = read_run_context(root)?;
    monitor.snapshot.storage = end.snapshot.storage;
    monitor.snapshot.budgets = end.snapshot.budgets;
    monitor.snapshot.artifacts = end.snapshot.artifacts;
    monitor.complete();
    println!("{}", serde_json::to_string_pretty(&monitor.snapshot)?);
    if with_payload {
        payload_stage(&mut store, root, socket)?;
    }
    Ok(())
}

/// A separate Fixture admission, not a live GO. Sealed archival envelopes are
/// synthetic and contain no decodable Solana/Pump transaction dataset.
fn payload_stage(
    store: &mut AcquisitionStore<SystemClock>,
    root: &Path,
    socket: &Path,
) -> Result<()> {
    let metadata = (0..4)
        .map(|n| store.published(n)?.ok_or("missing fixture metadata".into()))
        .collect::<Result<Vec<_>>>()?;
    let prepared =
        derive_payload_from_metadata(store.aggregate_plan(), &metadata, 422_496_000, 422_496_001)?;
    let bytes = payload_bytes()?;
    let length = u64::try_from(bytes.len())?;
    store.admit_payload(
        PayloadLease {
            schema: "OF1_PAYLOAD_LEASE_1".into(),
            authority: Authority::Fixture,
            budget: StageBudget {
                max_requests: 2,
                max_response_entity_bytes_total: length * 2,
                max_runtime_ms: 60_000,
            },
            prepared_payload_sha256: prepared.sha256()?,
            metadata_receipt_sha256: prepared.metadata_receipt_sha256().into(),
        },
        &prepared,
    )?;
    let context = read_run_context(root)?;
    let mut monitor = Monitor::new(context.snapshot, socket);
    monitor.snapshot.label = "Lokale simulatie · metadata + synthetische CAR-range".into();
    let server = FixtureServer::start_paced(OF1_SERVER_NAME, vec![ResponseScript {
        header: format!("HTTP/1.1 206 Fixture\r\nContent-Length: {length}\r\nContent-Range: bytes 4096-{}/8192\r\nConnection: close\r\n\r\n", 4096 + length - 1).into_bytes(),
        body: bytes, fragment_bytes: 16, delay_ms: 0, close_notify: true,
    }], 200)?;
    FixtureHttps::new(server.port(), server.root_der())?.capture_observed(
        store,
        4,
        &mut monitor,
    )?;
    if server.finish()?.len() != 1 {
        return Err("payload fixture request count changed".into());
    }
    let report = verify_payload(
        store,
        &prepared,
        VerificationLimits {
            max_total_bytes: 8192,
            max_section_bytes: 8192,
            max_nodes: 16,
            max_links: 32,
        },
    )?;
    // Verification receipt goes to stdout/outside-run evidence, not into the
    // immutable acquisition tree. Monitor never invents persisted CAR proof.
    println!(
        "LOCAL_SIMULATION_INTEGRITY: {}",
        serde_json::to_string(&report)?
    );
    let end = read_run_context(root)?;
    monitor.snapshot.storage = end.snapshot.storage;
    monitor.snapshot.budgets = end.snapshot.budgets;
    monitor.snapshot.artifacts = end.snapshot.artifacts;
    monitor.snapshot.integrity.car = "CID_SLOT_VERIFIED_FIXTURE_ONLY".into();
    monitor.complete();
    println!(
        "LOCAL_SIMULATION_PAYLOAD_RESULT: {}",
        serde_json::to_string(&serde_json::json!({
            "snapshot": monitor.snapshot, "integrity": report,
        }))?
    );
    Ok(())
}

fn run() -> Result<()> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    match args
        .iter()
        .map(String::as_str)
        .collect::<Vec<_>>()
        .as_slice()
    {
        ["--first", root, socket] => stage(Path::new(root), Path::new(socket), false, false),
        ["--resume", root, socket] => stage(Path::new(root), Path::new(socket), true, false),
        ["--first-payload", root, socket] => stage(Path::new(root), Path::new(socket), false, true),
        ["--resume-payload", root, socket] => stage(Path::new(root), Path::new(socket), true, true),
        [root, socket] | ["--with-payload", root, socket] => {
            let with_payload = args.len() == 3;
            let exe = std::env::current_exe()?;
            let first = Command::new(&exe)
                .args([
                    if with_payload {
                        "--first-payload"
                    } else {
                        "--first"
                    },
                    root,
                    socket,
                ])
                .stdin(Stdio::null())
                .status()?;
            if first.code() != Some(86) {
                return Err("fixture missed deliberate interruption".into());
            }
            let resumed = Command::new(exe)
                .args([
                    if with_payload {
                        "--resume-payload"
                    } else {
                        "--resume"
                    },
                    root,
                    socket,
                ])
                .stdin(Stdio::null())
                .status()?;
            if !resumed.success() {
                return Err("fixture restart failed".into());
            }
            Ok(())
        }
        _ => Err(
            "usage: of1-monitor-simulation NEW_ABSOLUTE_FIXTURE_ROOT LOCAL_MONITOR_SOCKET".into(),
        ),
    }
}

fn main() {
    if let Err(error) = run() {
        eprintln!("LOCAL_SIMULATION_STOP: {error}");
        std::process::exit(1);
    }
}
