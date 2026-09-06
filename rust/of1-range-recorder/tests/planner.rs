use of1_range_recorder::{
    Error, OfflinePlan, PersistedIndex, RangePlan, fixture, plan_ranges, sha256, validate_plan,
};
use std::io::Write;
use tempfile::NamedTempFile;

fn planned(plan: OfflinePlan, bytes: &[u8]) -> Result<RangePlan, Error> {
    let plan = validate_plan(plan)?;
    let mut file = NamedTempFile::new()?;
    file.write_all(bytes)?;
    let index = PersistedIndex::read(&plan, file.path())?;
    plan_ranges(&plan, &index)
}

#[test]
fn exact_slot_index_requests_not_a_full_car() {
    let bytes = fixture::index_bytes().unwrap();
    let output = planned(fixture::plan(), &bytes).unwrap();
    let ranges: Vec<_> = output
        .requests
        .iter()
        .map(|r| (r.start, r.end_exclusive))
        .collect();
    assert_eq!(
        ranges,
        [
            (128, 160),
            (160, 192),
            (192, 224),
            (224, 256),
            (256, 272),
            (400, 416)
        ]
    );
    assert_eq!(output.index_reported_absent, [422_496_012]);
    assert_eq!(output.planned_response_entity_bytes, 160);
    assert_eq!(
        output.slot_semantic_membership,
        "UNAVAILABLE_NOT_DECODED_IN_B4"
    );
    assert_eq!(output.root_membership, "UNAVAILABLE");
    assert_eq!(output.cid_verification, "UNAVAILABLE");
    assert_eq!(output.evidence, "Fixture");
}

#[test]
fn source_fields_and_hashes_are_exact() {
    let base = fixture::plan();
    for field in ["repository", "commit", "path", "blob", "sha256"] {
        let mut value = serde_json::to_value(&base).unwrap();
        value["format_source"][field] = "drift".into();
        assert!(matches!(
            validate_plan(serde_json::from_value(value).unwrap()),
            Err(Error::Identity)
        ));
    }
    for field in [
        "index_sha256",
        "source_fingerprint",
        "code_fingerprint",
        "toolchain_fingerprint",
    ] {
        let mut value = serde_json::to_value(&base).unwrap();
        value[field] = "UNKNOWN".into();
        assert!(validate_plan(serde_json::from_value(value).unwrap()).is_err());
    }
}

#[test]
fn missing_epoch_negative_fractional_and_unknown_fields_fail() {
    for field in [
        "epoch",
        "epoch_first_slot",
        "epoch_end_exclusive",
        "format_source",
        "budget",
    ] {
        let mut value = serde_json::to_value(fixture::plan()).unwrap();
        value.as_object_mut().unwrap().remove(field);
        assert!(serde_json::from_value::<OfflinePlan>(value).is_err());
    }
    for value in [
        serde_json::json!(-2),
        serde_json::json!(1.5),
        serde_json::Value::Null,
    ] {
        let mut plan = serde_json::to_value(fixture::plan()).unwrap();
        plan["start_slot"] = value;
        assert!(serde_json::from_value::<OfflinePlan>(plan).is_err());
    }
    for name in [
        "base_url",
        "proxy",
        "s3",
        "observedSlots",
        "endpoint",
        "objectKind",
    ] {
        let mut plan = serde_json::to_value(fixture::plan()).unwrap();
        plan[name] = "unsupported".into();
        assert!(serde_json::from_value::<OfflinePlan>(plan).is_err());
    }
    let input =
        serde_json::to_string(&fixture::plan())
            .unwrap()
            .replacen('{', "{\"epoch\":978,", 1);
    assert!(serde_json::from_str::<OfflinePlan>(&input).is_err());
}

#[test]
fn complete_epoch_and_inconsistent_epoch_bounds_fail() {
    let mut plan = fixture::plan();
    plan.start_slot = plan.epoch_first_slot;
    plan.end_slot = plan.epoch_end_exclusive;
    plan.budget.max_slots = 432_000;
    assert!(matches!(validate_plan(plan), Err(Error::FullEpoch)));
    let mut plan = fixture::plan();
    plan.epoch_first_slot += 1;
    assert!(matches!(validate_plan(plan), Err(Error::SlotRange)));
    let mut plan = fixture::plan();
    plan.epoch = u64::MAX;
    assert!(matches!(validate_plan(plan), Err(Error::Overflow)));
    let mut plan = fixture::plan();
    plan.end_slot = plan.start_slot;
    assert!(matches!(validate_plan(plan), Err(Error::SlotRange)));
}

#[test]
fn acquisition_authority_and_unsupported_schema_fail() {
    for key in ["approved", "network_enabled"] {
        let mut value = serde_json::to_value(fixture::plan()).unwrap();
        value[key] = true.into();
        assert!(matches!(
            validate_plan(serde_json::from_value(value).unwrap()),
            Err(Error::Authority)
        ));
    }
    for (key, text) in [
        ("mode", "ACQUISITION_LEASED"),
        ("slice_class", "RESEARCH_SAMPLING"),
        ("schema", "old-js-schema"),
    ] {
        let mut value = serde_json::to_value(fixture::plan()).unwrap();
        value[key] = text.into();
        assert!(validate_plan(serde_json::from_value(value).unwrap()).is_err());
    }
}

#[test]
fn persisted_index_truncation_extra_bytes_and_hash_drift_fail() {
    let bytes = fixture::index_bytes().unwrap();
    assert!(matches!(
        planned(fixture::plan(), &bytes[..bytes.len() - 1]),
        Err(Error::IndexSize)
    ));
    let mut extra = bytes.clone();
    extra.push(0);
    assert!(matches!(
        planned(fixture::plan(), &extra),
        Err(Error::IndexSize)
    ));
    let mut corrupt = bytes;
    corrupt[120] ^= 1;
    assert!(matches!(
        planned(fixture::plan(), &corrupt),
        Err(Error::IndexHash)
    ));
}

#[test]
fn malformed_zero_overlap_out_of_object_and_overflow_fail() {
    for (offset, length) in [(0u64, 1u32), (128, 0), (160, 80), (1000, 80), (u64::MAX, 2)] {
        let mut bytes = fixture::index_bytes().unwrap();
        bytes[132..140].copy_from_slice(&offset.to_le_bytes());
        bytes[140..144].copy_from_slice(&length.to_le_bytes());
        let mut plan = fixture::plan();
        plan.index_sha256 = sha256(&bytes);
        assert!(matches!(
            planned(plan, &bytes),
            Err(Error::IndexRecord(_) | Error::Overflow)
        ));
    }
}

#[test]
fn explicit_budgets_prevent_unbounded_planning() {
    let bytes = fixture::index_bytes().unwrap();
    for (key, number) in [
        ("max_slots", 3),
        ("max_requests", 5),
        ("max_total_response_entity_bytes", 159),
        ("max_disk_bytes", 100),
        ("max_index_bytes", 12),
        ("max_runtime_ms", 0),
        ("response_timeout_ms", 60_001),
        ("concurrency", 2),
    ] {
        let mut value = serde_json::to_value(fixture::plan()).unwrap();
        value["budget"][key] = number.into();
        assert!(
            matches!(
                planned(serde_json::from_value(value).unwrap(), &bytes),
                Err(Error::Budget)
            ),
            "{key}"
        );
    }
}

#[test]
fn all_zero_records_are_index_absence_not_chain_coverage() {
    let bytes = vec![0; 432_000 * 12];
    let mut plan = fixture::plan();
    plan.index_sha256 = sha256(&bytes);
    let result = planned(plan, &bytes).unwrap();
    assert!(result.requests.is_empty());
    assert_eq!(result.index_reported_absent.len(), 4);
    assert_eq!(
        result.slot_semantic_membership,
        "UNAVAILABLE_NOT_DECODED_IN_B4"
    );
}

#[test]
fn plan_identity_is_deterministic_and_binds_all_fingerprints() {
    let bytes = fixture::index_bytes().unwrap();
    let baseline = planned(fixture::plan(), &bytes).unwrap();
    assert_eq!(baseline, planned(fixture::plan(), &bytes).unwrap());
    for key in [
        "source_fingerprint",
        "code_fingerprint",
        "toolchain_fingerprint",
    ] {
        let mut value = serde_json::to_value(fixture::plan()).unwrap();
        value[key] = sha256(b"changed fixture context").into();
        assert_ne!(
            baseline.plan_sha256,
            planned(serde_json::from_value(value).unwrap(), &bytes)
                .unwrap()
                .plan_sha256
        );
    }
}

#[test]
fn deterministic_chunking_properties() {
    let bytes = fixture::index_bytes().unwrap();
    let mut seed = 83u64;
    let mut file = NamedTempFile::new().unwrap();
    file.write_all(&bytes).unwrap();
    let base = validate_plan(fixture::plan()).unwrap();
    let index = PersistedIndex::read(&base, file.path()).unwrap();
    for _ in 0..128 {
        seed = seed.wrapping_mul(6_364_136_223_846_793_005).wrapping_add(1);
        let mut p = fixture::plan();
        p.budget.max_response_entity_bytes = 1 + seed % 160;
        p.budget.max_requests = 160;
        let cap = p.budget.max_response_entity_bytes;
        let result = plan_ranges(&validate_plan(p).unwrap(), &index).unwrap();
        assert_eq!(result.planned_response_entity_bytes, 160);
        assert_eq!(
            result
                .requests
                .iter()
                .map(|r| r.end_exclusive - r.start)
                .sum::<u64>(),
            160
        );
        for pair in result.requests.windows(2) {
            assert!(pair[0].end_exclusive <= pair[1].start);
            if pair[0].slot == pair[1].slot {
                assert_eq!(pair[0].end_exclusive, pair[1].start);
            }
        }
        for r in &result.requests {
            assert!(r.end_exclusive > r.start && r.end_exclusive - r.start <= cap);
            assert!((128..272).contains(&r.start) || (400..416).contains(&r.start));
        }
    }
}

#[test]
fn missing_file_and_directory_cannot_be_an_index() {
    let dir = tempfile::tempdir().unwrap();
    let plan = validate_plan(fixture::plan()).unwrap();
    assert!(PersistedIndex::read(&plan, &dir.path().join("missing")).is_err());
    assert!(matches!(
        PersistedIndex::read(&plan, dir.path()),
        Err(Error::IndexSize)
    ));
}

#[test]
fn committed_source_receipt_matches_compiled_identity() {
    let receipt = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../schemas/acquisition/of1/source-receipt.json");
    let value: serde_json::Value =
        serde_json::from_slice(&std::fs::read(receipt).unwrap()).unwrap();
    let source = serde_json::to_value(fixture::plan().format_source).unwrap();
    assert_eq!(value["repository"], source["repository"]);
    assert_eq!(value["commit"], source["commit"]);
    for field in ["path", "blob", "sha256"] {
        assert_eq!(value["files"][0][field], source[field]);
    }
    assert_eq!(
        value["files"][1]["sha256"],
        "d400cb517808548fd830d16453f7647fb1185db3fc9dcbb5b4286634bab470c3"
    );
    assert_eq!(value["capability"], "DOCUMENTATION_ONLY");
    assert_eq!(value["authentic_chain_bytes"], false);
    assert_eq!(value["root_to_slot_membership"], "UNAVAILABLE");
}

#[test]
fn amplification_is_rejected_before_materializing_chunks() {
    let mut bytes = fixture::index_bytes().unwrap();
    bytes[128..132].copy_from_slice(&u32::MAX.to_le_bytes());
    let mut p = fixture::plan();
    p.end_slot = p.start_slot + 1;
    p.index_sha256 = sha256(&bytes);
    p.object_size = u64::from(u32::MAX) + 1024;
    p.budget.max_response_entity_bytes = 1;
    p.budget.max_requests = u64::from(u32::MAX);
    p.budget.max_total_response_entity_bytes = u64::from(u32::MAX);
    // Tiny disk allowance: reject before trying to create >4 billion request entries.
    assert!(matches!(planned(p.clone(), &bytes), Err(Error::Budget)));
    // Sufficient disk but small explicit plan-entry memory cap: also reject before allocation.
    p.budget.max_disk_bytes = u64::MAX;
    assert!(matches!(planned(p, &bytes), Err(Error::Budget)));
}
