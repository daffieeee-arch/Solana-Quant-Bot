//! Executed loopback-only transport evidence with an actual same-binary child-process exit.
//! Synthetic bytes only. No provider endpoint, DNS, credential or acquisition authority.

use of1_range_recorder::{
    OfflinePlan,
    durable::{Clock, ClockSample, RetryComparison, Store, StoreResult},
    fixture, sha256,
    transport::{LoopbackFixture, TransportError},
};
use serde_json::{Value, json};
use std::{
    error::Error,
    fmt::Write as _,
    fs,
    io::{Read, Write},
    net::{Ipv4Addr, Shutdown, SocketAddrV4, TcpListener},
    path::Path,
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};

type Result<T> = std::result::Result<T, Box<dyn Error>>;

struct FixtureClock(u64);

impl Clock for FixtureClock {
    fn sample(&self) -> StoreResult<ClockSample> {
        Ok(ClockSample {
            wall_ms: 100_000 + self.0,
            boot_ms: 10_000 + self.0,
            boot_id: "FIXTURE_ONLY:transport-evidence-boot".into(),
        })
    }
}

fn plan() -> OfflinePlan {
    let mut plan = fixture::plan();
    plan.end_slot = plan.start_slot + 1;
    plan.budget.max_slots = 1;
    plan.budget.max_requests = 3;
    plan.budget.max_total_response_entity_bytes = 96;
    plan.budget.request_retries = 1;
    plan.budget.response_timeout_ms = 5_000;
    plan
}

fn capture(
    store: &mut Store<FixtureClock>,
    sequence: u64,
    payload: Vec<u8>,
) -> Result<std::result::Result<of1_range_recorder::durable::Receipt, TransportError>> {
    let listener = TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST, 0))?;
    listener.set_nonblocking(true)?;
    let port = listener.local_addr()?.port();
    let worker = thread::spawn(move || -> std::io::Result<Vec<u8>> {
        let deadline = Instant::now() + Duration::from_secs(10);
        let mut socket = loop {
            match listener.accept() {
                Ok((socket, peer)) => {
                    if !peer.ip().is_loopback() {
                        return Err(std::io::Error::other("non-loopback fixture peer"));
                    }
                    break socket;
                }
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                    if Instant::now() >= deadline {
                        return Err(std::io::Error::new(
                            std::io::ErrorKind::TimedOut,
                            "fixture accept deadline",
                        ));
                    }
                    thread::sleep(Duration::from_millis(1));
                }
                Err(e) => return Err(e),
            }
        };
        socket.set_read_timeout(Some(Duration::from_secs(5)))?;
        socket.set_write_timeout(Some(Duration::from_secs(5)))?;
        let mut request = Vec::new();
        while !request.ends_with(b"\r\n\r\n") {
            let mut byte = [0];
            socket.read_exact(&mut byte)?;
            request.extend_from_slice(&byte);
            if request.len() > 16_384 {
                return Err(std::io::Error::other("fixture request too large"));
            }
        }
        let start = 128 + sequence * 32;
        let end = start + 31;
        let head = format!(
            "HTTP/1.1 206 Partial Content\r\nContent-Length: 32\r\nContent-Range: bytes {start}-{end}/1024\r\nConnection: close\r\nETag: \"FIXTURE_ONLY-object-v1\"\r\n\r\n"
        );
        socket.write_all(head.as_bytes())?;
        socket.write_all(&payload)?;
        socket.shutdown(Shutdown::Write)?;
        Ok(request)
    });
    let result = LoopbackFixture::new(port)?.capture(store, sequence);
    let request = worker.join().map_err(|_| "fixture server panicked")??;
    let request = String::from_utf8(request)?.to_ascii_lowercase();
    let start = 128 + sequence * 32;
    if !request.starts_with("get /978/epoch-978.car http/1.1\r\n")
        || !request.contains(&format!("range: bytes={start}-{}\r\n", start + 31))
        || !request.contains("connection: close\r\n")
    {
        return Err("fixture request identity/range mismatch".into());
    }
    Ok(result)
}

fn crash_child(scratch: &Path) -> Result<()> {
    let mut store = Store::create(
        &scratch.join("capture"),
        plan(),
        &scratch.join("index.raw"),
        FixtureClock(0),
    )?;
    capture(&mut store, 0, vec![0x42; 32])??;
    if !matches!(
        capture(&mut store, 1, vec![0x43; 7])?,
        Err(TransportError::Truncated)
    ) {
        return Err("expected interrupted fixture body".into());
    }
    let summary = store.summary()?;
    if summary.attempts_reserved != 2
        || summary.published_requests != 1
        || summary.charged_entity_bytes != 64
        || summary.verified_response_entity_bytes != 32
        || store.published(1)?.is_some()
    {
        return Err("pre-exit accounting/publication mismatch".into());
    }
    // Intentional process exit with Store and its writer lock alive: no Rust destructors.
    // This proves process-loss/reopen behavior, not physical power-loss durability.
    std::process::exit(86);
}

fn execute(parent: &Path) -> Result<Value> {
    let scratch = parent.join("of1-transport-fixture");
    fs::create_dir(&scratch)?;
    fs::write(scratch.join("index.raw"), fixture::index_bytes()?)?;
    let child = Command::new(std::env::current_exe()?)
        .arg("--crash-child")
        .arg(&scratch)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .status()?;
    if child.code() != Some(86) {
        return Err("transport fixture child did not reach its reviewed crash seam".into());
    }
    let mut store = Store::resume(
        &scratch.join("capture"),
        plan(),
        &scratch.join("index.raw"),
        FixtureClock(100),
    )?;
    let before = store.summary()?;
    let original = store
        .published(0)?
        .ok_or("publication lost across process exit")?;
    if original.bytes != [0x42; 32]
        || before.attempts_reserved != 2
        || before.published_requests != 1
    {
        return Err("restart did not retain exact fixture progress".into());
    }
    let second = capture(&mut store, 1, vec![0x43; 32])??;
    let after = store.summary()?;
    if after.attempts_reserved != 3
        || after.charged_entity_bytes != 96
        || after.verified_response_entity_bytes != 64
        || after.published_requests != 2
        || after.unpublished_attempts != 1
        || after.deadline_wall_ms != before.deadline_wall_ms
        || after.deadline_boot_ms != before.deadline_boot_ms
        || store
            .published(0)?
            .ok_or("first publication disappeared")?
            .receipt
            != original.receipt
        || store.published(1)?.ok_or("retry publication absent")?.bytes != [0x43; 32]
        || second.sha256 != sha256(&[0x43; 32])
        || second.retry_comparison != Some(RetryComparison::RetainedOverlapMatched)
    {
        return Err("post-restart exact progression mismatch".into());
    }
    Ok(json!({
        "schema": "OF1_TRANSPORT_FIXTURE_EVIDENCE_1",
        "evidence": "Fixture",
        "approved": false,
        "live_network_enabled": false,
        "transport": "FEATURE_GATED_NUMERIC_LOOPBACK_HTTP_1_1_SUBSET",
        "fixture_index_sha256": fixture::INDEX_SHA256,
        "plan_sha256": second.plan_sha256,
        "payload_sha256": [sha256(&[0x42; 32]), sha256(&[0x43; 32])],
        "scenario": "INDEX_PLAN_RESERVE_HTTP_PUBLISH_TRUNCATE_PROCESS_EXIT_RESTART_RETRY_PROGRESS",
        "process_exit": {"code":86,"rust_destructors_ran":false,"physical_power_loss_proven":false},
        "before_restart_completion": before,
        "after_restart_completion": after,
        "executed_checks": {
            "exact_requested_ranges_sent": true,
            "first_raw_receipt_survived_process_exit": true,
            "truncated_attempt_not_published": true,
            "retry_retained_matching_prefix": true,
            "every_dispatched_attempt_durably_charged": true,
            "published_request_not_refetched": true,
            "original_deadlines_preserved": true,
            "all_published_raw_receipts_revalidated": true
        },
        "budget_metric":"response_entity_bytes",
        "budget_is_physical_wire_cap":false,
        "interrupted_actual_total":"UNAVAILABLE_AFTER_PROCESS_LOSS",
        "retained_truncated_prefix_bytes":7,
        "controlled_fixture_entity_supply_bytes":71,
        "domain_counts":"UNAVAILABLE_NOT_DECODED_IN_B4",
        "historical_source_bytes":"UNAVAILABLE",
        "root_membership":"UNAVAILABLE",
        "slot_semantic_membership":"UNAVAILABLE_NOT_DECODED_IN_B4",
        "cid_verification":"UNAVAILABLE",
        "b4_complete":false,
        "research_outcome":"NOT_EVALUATED_ENGINEERING_FIXTURE_ONLY"
    }))
}

fn markdown(value: &Value) -> Result<String> {
    let mut text = String::from(
        "# OF1 offline transport evidence\n\n> **Document status: ACTIVE — FIXTURE ONLY.** Deterministically generated from an executed local HTTP scenario. No provider or historical-data traffic.\n\nIndex planning → durable reservation → exact loopback HTTP response → Raw/receipt publication → truncated next response → actual process exit → restart → matching retry → verified progress.\n\n",
    );
    for (label, key) in [
        ("Fixture index SHA-256", "fixture_index_sha256"),
        ("Plan SHA-256", "plan_sha256"),
    ] {
        writeln!(
            text,
            "- {label}: `{}`",
            value[key].as_str().ok_or("missing evidence identity")?
        )?;
    }
    text.push_str("\n| Executed result | Before retry after restart | Final |\n|---|---:|---:|\n");
    for (label, key) in [
        ("Durable attempts", "attempts_reserved"),
        ("Charged response-entity allowance", "charged_entity_bytes"),
        (
            "Verified published response-entity bytes",
            "verified_response_entity_bytes",
        ),
        ("Published requests", "published_requests"),
        ("Unpublished attempts", "unpublished_attempts"),
        ("Original wall deadline", "deadline_wall_ms"),
        ("Original boot deadline", "deadline_boot_ms"),
    ] {
        writeln!(
            text,
            "| {label} | {} | {} |",
            value["before_restart_completion"][key], value["after_restart_completion"][key]
        )?;
    }
    text.push_str(
        "\nThe child exits with code 86 while the store is alive; Rust destructors do not run.\nThe first publication survives. A seven-byte truncated prefix is retained and the next process retries the same range; the full retry agrees on those bytes.\nAll three attempts remain charged. The already-published range is not fetched again.\nThe report proves this process-loss/restart scenario, not physical power loss.\n\nThe fixed fixture server supplies 71 entity bytes (32 + 7 + 32). The conservative durable allowance is 96 bytes.\nThis known fixture schedule does not convert unreceipted actual bytes after arbitrary process loss into a measured zero or complete total.\n`response_entity_bytes` is not a physical-wire cap. HTTP headers and a bounded post-entity framing probe are distinct from the declared entity.\n\nEvery published Raw/receipt pair is revalidated; both original deadlines remain unchanged.\nChanged source identity is source drift; differing retained overlapping bytes under the same claimed identity are conflicting bytes. Neither may be published.\nThose rejection cases, malformed ranges/streams, retry limits and deadline stops are exercised by the focused transport tests.\n\nDomain counts and slot-semantic membership remain `UNAVAILABLE_NOT_DECODED_IN_B4`; epoch-root membership and CID verification remain `UNAVAILABLE`.\nNo authentic observation, historical activation, research readiness, economics or profitability is established.\nB4 remains open / In Progress / ACTIVE NOW / Unproven. No acquisition run is authorized.\n",
    );
    Ok(text)
}

fn run() -> Result<()> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.len() != 2 {
        return Err(
            "usage: of1-transport-evidence --json|--markdown <fresh-scratch-parent>".into(),
        );
    }
    if args[0] == "--crash-child" {
        return crash_child(Path::new(&args[1]));
    }
    if !["--json", "--markdown"].contains(&args[0].as_str()) {
        return Err("unsupported fixture evidence mode".into());
    }
    let value = execute(Path::new(&args[1]))?;
    if args[0] == "--json" {
        println!("{}", serde_json::to_string_pretty(&value)?);
    } else {
        print!("{}", markdown(&value)?);
    }
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
