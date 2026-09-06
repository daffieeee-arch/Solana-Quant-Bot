//! Deterministic, executed fixture evidence. Writes only beneath a fresh caller scratch parent.
//! No transport: all bytes below are explicitly synthetic, never authentic OF1 data.

use of1_range_recorder::{
    OfflinePlan,
    durable::{Clock, ClockSample, Response, Store, StoreError, StoreResult, Summary},
    fixture, sha256,
};
use serde_json::{Value, json};
use std::{
    cell::Cell,
    fmt::Write as _,
    fs,
    path::{Path, PathBuf},
    rc::Rc,
};

#[derive(Clone)]
struct FixtureClock(Rc<Cell<(u64, u64)>>);

impl FixtureClock {
    fn new() -> Self {
        Self(Rc::new(Cell::new((100_000, 10_000))))
    }

    fn set(&self, wall_ms: u64, boot_ms: u64) {
        self.0.set((wall_ms, boot_ms));
    }
}

impl Clock for FixtureClock {
    fn sample(&self) -> StoreResult<ClockSample> {
        let (wall_ms, boot_ms) = self.0.get();
        Ok(ClockSample {
            wall_ms,
            boot_ms,
            boot_id: "FIXTURE_ONLY:deterministic-boot-identity".into(),
        })
    }
}

fn plan() -> OfflinePlan {
    let mut plan = fixture::plan();
    plan.end_slot = plan.start_slot + 1;
    plan.budget.max_slots = 1;
    plan.budget.max_requests = 3;
    plan.budget.max_total_response_entity_bytes = 96;
    plan.budget.request_retries = 2;
    plan
}

fn response() -> Response {
    Response {
        status: 206,
        start: 128,
        end_exclusive: 160,
        total: 1024,
    }
}

fn published_fixture(
    root: &Path,
    index: &Path,
    fill: u8,
) -> Result<PathBuf, Box<dyn std::error::Error>> {
    let mut store = Store::create(root, plan(), index, FixtureClock::new())?;
    let permit = store.reserve(0)?;
    store.commit(permit, response(), &[fill; 32])?;
    store.published(0)?.ok_or("fixture publication missing")?;
    Ok(root.join("published/0000000000"))
}

fn execute(parent: &Path) -> Result<Value, Box<dyn std::error::Error>> {
    // A fixed child created exclusively prevents accidental reuse/overwrite of a prior report run.
    let scratch = parent.join("of1-durability-fixture");
    fs::create_dir(&scratch)?;
    let index = scratch.join("fixture-index.raw");
    fs::write(&index, fixture::index_bytes()?)?;
    let root = scratch.join("restart");
    let clock = FixtureClock::new();
    let mut first = Store::create(&root, plan(), &index, clock.clone())?;
    let initial = first.summary()?;
    let abandoned = first.reserve(0)?;
    drop(abandoned);
    drop(first); // Equivalent store reopen seam, not a claim of process-kill/power-loss testing.
    clock.set(100_100, 10_100);
    let mut resumed = Store::resume(&root, plan(), &index, clock.clone())?;
    let permit = resumed.reserve(0)?;
    clock.set(100_101, 10_101);
    let receipt = resumed.commit(permit, response(), &[0x42; 32])?;
    let verified = resumed.published(0)?.ok_or("resumed publication missing")?;
    let summary = resumed.summary()?;
    if verified.bytes != [0x42; 32]
        || receipt != verified.receipt
        || summary.attempts_reserved != 2
        || summary.charged_entity_bytes != 64
        || summary.verified_response_entity_bytes != 32
        || summary.published_requests != 1
        || summary.unpublished_attempts != 1
        || summary.unreceipted_response_entity_bytes.is_some()
        || summary.deadline_wall_ms != initial.deadline_wall_ms
        || summary.deadline_boot_ms != initial.deadline_boot_ms
    {
        return Err("restart fixture invariant mismatch".into());
    }
    drop(resumed);
    clock.set(initial.deadline_wall_ms, initial.deadline_boot_ms);
    if !matches!(
        Store::resume(&root, plan(), &index, clock),
        Err(StoreError::Deadline)
    ) {
        return Err("original deadline unexpectedly renewed".into());
    }

    let tampered = scratch.join("tampered");
    let tampered_object = published_fixture(&tampered, &index, 0x42)?;
    fs::write(tampered_object.join("raw.bin"), [0x43; 32])?;
    if !matches!(
        Store::resume(&tampered, plan(), &index, FixtureClock::new()),
        Err(StoreError::Corrupt)
    ) {
        return Err("changed Raw bytes accepted against original receipt".into());
    }

    let left = scratch.join("receipt-left");
    let right = scratch.join("receipt-right");
    let left_object = published_fixture(&left, &index, 0x42)?;
    let right_object = published_fixture(&right, &index, 0x42)?;
    fs::copy(
        right_object.join("receipt.json"),
        left_object.join("receipt.json"),
    )?;
    if !matches!(
        Store::resume(&left, plan(), &index, FixtureClock::new()),
        Err(StoreError::Corrupt)
    ) {
        return Err("cross-run receipt unexpectedly accepted".into());
    }
    Ok(evidence(&summary, &receipt.plan_sha256))
}

fn evidence(summary: &Summary, plan_sha256: &str) -> Value {
    json!({
        "schema": "OF1_DURABILITY_FIXTURE_EVIDENCE_1",
        "evidence": "Fixture",
        "approved": false,
        "network_enabled": false,
        "historical_source_bytes": "UNAVAILABLE",
        "plan_sha256": plan_sha256,
        "fixture_index_sha256": fixture::INDEX_SHA256,
        "fixture_payload_sha256": sha256(&[0x42; 32]),
        "runtime_executable_identity": "VERIFIED_DURING_RESUME",
        "declared_code_and_toolchain_fingerprints": "FIXTURE_CONTEXT_NOT_RUNTIME_ATTESTATIONS",
        "identity_proves_source_authenticity": false,
        "scenario": "RESERVE_DROP_REOPEN_RETRY_PUBLISH",
        "crash_scope": "STORE_REOPEN_SEAM_NOT_PROCESS_KILL_OR_POWER_LOSS",
        "summary": summary,
        "checks": {
            "abandoned_reservation_remains_charged": true,
            "original_deadline_unchanged": true,
            "restart_at_original_deadline_rejected": true,
            "changed_raw_rejected_against_original_receipt": true,
            "cross_run_receipt_rejected": true,
            "published_raw_matches_receipt": true
        },
        "budget_metric": "response_entity_bytes",
        "interrupted_actual_bytes": "UNAVAILABLE",
        "interrupted_allowance_is_refunded": false,
        "root_membership": "UNAVAILABLE",
        "slot_semantic_membership": "UNAVAILABLE_NOT_DECODED_IN_B4",
        "cid_verification": "UNAVAILABLE",
        "research_outcome": "NOT_EVALUATED_ENGINEERING_FIXTURE_ONLY",
        "b4_complete": false
    })
}

fn markdown(value: &Value) -> Result<String, Box<dyn std::error::Error>> {
    let summary = &value["summary"];
    let mut text = String::from(
        "# OF1 durable publication evidence\n\n> **Document status: ACTIVE — FIXTURE ONLY.** Generated by `of1-durability-evidence`; no network or authentic historical data.\n\nThis executes a store-reopen crash seam, not a process-kill or power-loss test.\nOne synthetic request is reserved, abandoned, retried after reopening, then published as one verified Raw/receipt pair.\n\n",
    );
    writeln!(
        text,
        "- Plan SHA-256: `{}`\n- Fixture index SHA-256: `{}`\n- Published fixture payload SHA-256: `{}`\n",
        value["plan_sha256"].as_str().ok_or("missing plan hash")?,
        value["fixture_index_sha256"]
            .as_str()
            .ok_or("missing index hash")?,
        value["fixture_payload_sha256"]
            .as_str()
            .ok_or("missing payload hash")?,
    )?;
    text.push_str("| Executed result | Value |\n|---|---|\n");
    for (name, key) in [
        ("Durably reserved attempts", "attempts_reserved"),
        (
            "Charged response-entity allowance (bytes)",
            "charged_entity_bytes",
        ),
        (
            "Verified published response-entity bytes",
            "verified_response_entity_bytes",
        ),
        ("Published requests", "published_requests"),
        ("Unpublished attempts", "unpublished_attempts"),
        (
            "Original wall-clock deadline (fixture ms)",
            "deadline_wall_ms",
        ),
        (
            "Original boot-clock deadline (fixture ms)",
            "deadline_boot_ms",
        ),
    ] {
        writeln!(text, "| {name} | {} |", summary[key])?;
    }
    text.push_str(
        "\nInterrupted actual response bytes remain **UNAVAILABLE**, not zero; the full reserved allowance remains charged.\nThe metric is `response_entity_bytes`, not physical wire bytes.\n\nExecuted rejection checks: restart at the original deadline, changed Raw bytes with the original receipt,\nand a substituted receipt from a different run. All fail closed.\nRuntime executable identity is checked during resume; declared code/toolchain fingerprints remain synthetic fixture context, not runtime attestations.\nNo identity/hash check proves source authenticity.\n\nDomain counts and slot semantics: `UNAVAILABLE_NOT_DECODED_IN_B4`.\nEpoch-root membership and CID verification: `UNAVAILABLE`.\nThis is engineering fixture evidence, not insufficient-data or edge-falsification research evidence.\n\nB4 remains open / In Progress / ACTIVE NOW / Unproven. No acquisition run is authorized.\nThis partial durability delivery does not complete B4, replace the JS integration paths or establish authentic data, research readiness or profitability.\n",
    );
    Ok(text)
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.len() != 2 || !["--json", "--markdown"].contains(&args[0].as_str()) {
        return Err("usage: of1-durability-evidence --json|--markdown <scratch-parent>".into());
    }
    let evidence = execute(Path::new(&args[1]))?;
    if args[0] == "--json" {
        println!("{}", serde_json::to_string_pretty(&evidence)?);
    } else {
        print!("{}", markdown(&evidence)?);
    }
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
