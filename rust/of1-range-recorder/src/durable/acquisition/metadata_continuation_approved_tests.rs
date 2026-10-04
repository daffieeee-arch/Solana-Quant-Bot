use super::*;
use crate::{
    FormatSource,
    durable::{SystemClock, acquisition::AggregateBudget},
};
use std::{
    io::Write,
    process::{Command, Stdio},
};

fn approved(at: &super::super::ClockSample, target: &str) -> Authority {
    let mut child = Command::new("python3")
        .arg(Path::new(env!("CARGO_MANIFEST_DIR")).join("../../scripts/of1-approved-authority.py"))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child.stdin.take().unwrap().write_all(&serde_json::to_vec(&serde_json::json!({"clock":at,"target_sha256":target,"approval_id":"metadata-continuation-offline-native-validation","operator":"offline fixture"})).unwrap()).unwrap();
    let output = child.wait_with_output().unwrap();
    assert!(output.status.success());
    serde_json::from_slice(&output.stdout).unwrap()
}
fn fixture_binding(root: &Path) -> (AggregatePlan, MetadataContinuation) {
    let plan = AggregatePlan {
        schema: super::super::AGGREGATE_SCHEMA.into(),
        epoch: 978,
        format_source: FormatSource::pinned(),
        code_sha: "a".repeat(40),
        toolchain_fingerprint: "b".repeat(64),
        executable_sha256: "c".repeat(64),
        sample_identity: Some(crate::b7::sample(5, Path::new(crate::b7::PRODUCTION_ROOT)).unwrap()),
        clock_policy: Some(ClockPolicy::standard()),
        download_rate: Some(crate::rate::DownloadRate::standard()),
        budget: AggregateBudget {
            max_slots: 16,
            max_plan_bytes: 1_048_576,
            max_requests: 60,
            max_response_entity_bytes: 16_777_216,
            max_total_response_entity_bytes: 134_217_728,
            max_disk_bytes: 268_435_456,
            required_free_disk_bytes: crate::campaign::FREE_BYTES,
            max_memory_bytes: 536_870_912,
            max_runtime_ms: 1_800_000,
            response_timeout_ms: 30_000,
            request_retries: 2,
        },
    };
    let old_budget = StageBudget {
        max_requests: 12,
        max_response_entity_bytes_total: 15_576_576,
        max_runtime_ms: 600_000,
    };
    let approval = MetadataContinuation {
        schema: SCHEMA.into(),
        authority: Authority::Fixture,
        binding: MetadataContinuationBinding {
            run_id: "e".repeat(64),
            aggregate_sha256: sha256(&encode(&plan).unwrap()),
            original_metadata_lease_sha256: "8".repeat(64),
            previous_ledger_sha256: "d".repeat(64),
            original_executable_sha256: plan.executable_sha256.clone(),
            continuation_executable_sha256: executable_hash().unwrap(),
            sample_identity: plan.sample_identity.clone().unwrap(),
            expected_index_sha256: crate::b7::INDEX_SHA256.into(),
            expected_source_fingerprint: crate::b7::SOURCE_FINGERPRINT.into(),
            requests: metadata_requests(),
            retained_files: inventory(root).unwrap(),
            prior_attempts: 1,
            prior_entity_bytes: 5_184_000,
            remaining_budget: remaining_budget(&old_budget, 1, 5_184_000).unwrap(),
        },
    };
    (plan, approval)
}

#[test]
fn approved_generator_native_metadata_continuation_target_and_clock_refusals() {
    let at = SystemClock.sample().unwrap();
    let temp = tempfile::tempdir().unwrap();
    fs::write(temp.path().join("retained.bin"), b"retained original bytes").unwrap();
    let (plan, mut approval) = fixture_binding(temp.path());
    let target = approval.target().unwrap();
    approval.authority = approved(&at, &target);
    let stage = make_stage(
        plan.clock_policy.as_ref(),
        &approval.authority,
        &approval.binding.remaining_budget,
        &approval,
        &at,
    )
    .unwrap();
    let record = MetadataContinuationRecord { approval, stage };
    validate_authority(
        plan.clock_policy.as_ref(),
        &record.approval.authority,
        &record.approval.target().unwrap(),
    )
    .unwrap();
    validate_stage(
        &record.stage,
        plan.clock_policy.as_ref(),
        &record.approval.authority,
        &record.approval.binding.remaining_budget,
        &record.approval,
    )
    .unwrap();
    assert_eq!(record.stage.budget.max_runtime_ms, 600_000);
    assert_eq!(record.stage.budget.max_requests, 11);
    assert_eq!(
        record.stage.budget.max_response_entity_bytes_total,
        10_392_576
    );
    let mut wrong = record.clone();
    wrong.approval.binding.expected_index_sha256 = "f".repeat(64);
    assert!(
        validate_authority(
            plan.clock_policy.as_ref(),
            &wrong.approval.authority,
            &wrong.approval.target().unwrap()
        )
        .is_err()
    );
    let mut wrong = record.clone();
    if let Authority::Approved {
        approved_plan_sha256,
        ..
    } = &mut wrong.approval.authority
    {
        *approved_plan_sha256 = "f".repeat(64);
    }
    assert!(
        validate_authority(
            plan.clock_policy.as_ref(),
            &wrong.approval.authority,
            &wrong.approval.target().unwrap()
        )
        .is_err()
    );
    let mut expired = at.clone();
    expired.boot_ms += 1_200_001;
    expired.wall_ms += 1_200_001;
    assert!(
        make_stage(
            plan.clock_policy.as_ref(),
            &record.approval.authority,
            &record.stage.budget,
            &record.approval,
            &expired
        )
        .is_err()
    );
    let mut wrong_boot = at.clone();
    wrong_boot.boot_id.push_str("-wrong");
    assert!(
        make_stage(
            plan.clock_policy.as_ref(),
            &record.approval.authority,
            &record.stage.budget,
            &record.approval,
            &wrong_boot
        )
        .is_err()
    );
}
