//! The single owner-accepted B7 design. Identity only: never execution authority.
use crate::{
    durable::{StoreError, StoreResult},
    sample::SampleIdentity,
    sha256,
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::path::{Component, Path};

pub const CAMPAIGN_ID: &str = "b7-recurrence-v1-20260924";
pub const REPORT_SHA256: &str = "80d8f6fb0d10fa7c210fb4bd6e9fe4d1bdbb8b19539fa7fd021394d03f3bf415";
pub const SELECTION_SHA256: &str =
    "085d33c70ad504a9e14378629df7ec584c88826dc918f4eafb780bb44c824782";
pub const PROPOSAL_SHA256: &str =
    "a65733f0315e03bcf936c8ec75be21f6b8b0499abe536a824232de3135a3fd3f";
pub const SEED: &str = "solana-quant-b7-86-recurrence-v1-20260924";
pub const ALGORITHM: &str = "SHA256_GRID_STRATIFIED_WITHOUT_REPLACEMENT_1";
pub const PRODUCTION_ROOT: &str =
    "/home/chupa/Solana-project/data-old-faithful-one/campaigns/b7-recurrence-v1-20260924";
pub const STARTS: [u64; 16] = [
    422_526_144,
    422_552_336,
    422_692_432,
    422_659_936,
    422_802_704,
    422_778_400,
    422_901_312,
    422_760_864,
    422_623_328,
    422_579_632,
    422_675_168,
    422_681_872,
    422_730_416,
    422_742_656,
    422_789_360,
    422_781_216,
];
pub const PAYLOAD_BYTES: [u64; 16] = [
    33_214_023, 33_491_258, 30_903_372, 24_857_555, 22_712_061, 22_925_409, 23_466_017, 26_694_077,
    27_356_201, 24_721_726, 20_727_246, 19_911_171, 36_134_514, 27_136_374, 24_286_843, 27_402_387,
];
pub const INDEX_SHA256: &str = "649754195dd6182846a9754ffd7fa26a487e0b65bea66794e7d8bf180321a59b";
pub const SOURCE_FINGERPRINT: &str =
    "c9dd698fab16c73ec2e832a52cf2f1fb9b729133d9cacb13d5da7257cde8ba9d";

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Binding {
    pub campaign_id: String,
    pub campaign_root: String,
    pub accepted_report_sha256: String,
    pub selection_sha256: String,
    pub window_ordinal: u64,
    pub rank: u64,
    pub rank_sha256: String,
    pub cohort_role: String,
    pub phase: u64,
}

fn rank_hash(start: u64) -> String {
    let mut bytes = b"B7_WINDOW_1\0".to_vec();
    bytes.extend(SEED.as_bytes());
    bytes.extend(978_u64.to_le_bytes());
    bytes.extend(start.to_le_bytes());
    sha256(&bytes)
}

/// Canonical selection copied by identity from #142; no metadata/outcomes enter it.
#[must_use]
pub fn selection() -> Value {
    let windows: Vec<_> = STARTS
        .iter()
        .enumerate()
        .map(|(i, start)| {
            json!({
                "start_slot":start,"end_slot_exclusive":start+16,"boundary_slot":start+8,
                "rank":i%4+1+if i>=8{4}else{0},"rank_sha256":rank_hash(*start),
                "role":if i%8<4{"DEVELOPMENT"}else{"RESERVED_EVALUATION"},"stage":i/8+1
            })
        })
        .collect();
    json!({"schema":"B7_FIXED_WINDOWS_1","network_authorized":false,"proposal_sha256":PROPOSAL_SHA256,
        "populations":[{"role":"DEVELOPMENT","eligible_windows":13496},{"role":"RESERVED_EVALUATION","eligible_windows":13499}],"windows":windows})
}

/// # Errors
/// Only one of the frozen sixteen windows and an unambiguous absolute root exist.
pub fn sample(ordinal: usize, root: &Path) -> StoreResult<SampleIdentity> {
    let start = *STARTS.get(ordinal).ok_or(StoreError::Identity)?;
    let root = root.to_str().ok_or(StoreError::Identity)?;
    if !Path::new(root).is_absolute()
        || Path::new(root)
            .components()
            .any(|c| matches!(c, Component::ParentDir | Component::CurDir))
    {
        return Err(StoreError::Identity);
    }
    Ok(SampleIdentity {
        schema: "OF1_B7_WINDOW_SAMPLE_1".into(),
        sample_class: "RESEARCH_SAMPLING".into(),
        selection_plan_sha256: PROPOSAL_SHA256.into(),
        epoch: 978,
        seed: SEED.into(),
        algorithm: ALGORITHM.into(),
        // In this version the existing center field is exactly the half-window boundary.
        selected_center: start + 8,
        start_slot: start,
        end_slot_exclusive: start + 16,
        b7: Some(Binding {
            campaign_id: CAMPAIGN_ID.into(),
            campaign_root: root.into(),
            accepted_report_sha256: REPORT_SHA256.into(),
            selection_sha256: SELECTION_SHA256.into(),
            window_ordinal: ordinal as u64,
            rank: (ordinal % 4 + 1 + if ordinal >= 8 { 4 } else { 0 }) as u64,
            rank_sha256: rank_hash(start),
            cohort_role: if ordinal % 8 < 4 {
                "DEVELOPMENT"
            } else {
                "RESERVED_EVALUATION"
            }
            .into(),
            phase: (ordinal / 8 + 1) as u64,
        }),
    })
}

/// # Errors
/// Changes to the original proposal or the frozen selection fail closed.
pub fn validate(value: &SampleIdentity) -> StoreResult<()> {
    let binding = value.b7.as_ref().ok_or(StoreError::Identity)?;
    let ordinal = usize::try_from(binding.window_ordinal).map_err(|_| StoreError::Identity)?;
    let proposal: Value = serde_json::from_slice(include_bytes!(
        "../../../research/columnar-query/b7-proposal.json"
    ))
    .map_err(|_| StoreError::Identity)?;
    let mut selected = serde_json::to_vec_pretty(&selection()).map_err(|_| StoreError::Identity)?;
    selected.push(b'\n');
    // #142 uses sorted, two-space-indented JSON with a trailing newline.
    let mut canonical = serde_json::to_vec_pretty(&proposal).map_err(|_| StoreError::Identity)?;
    canonical.push(b'\n');
    if value != &sample(ordinal, Path::new(&binding.campaign_root))?
        || sha256(&selected) != SELECTION_SHA256
        || sha256(&canonical) != PROPOSAL_SHA256
    {
        return Err(StoreError::Identity);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn exact_frozen_selection_and_pilot_identity() {
        let old = SampleIdentity::fixed_pilot();
        old.validate(978).unwrap();
        assert!(!serde_json::to_string(&old).unwrap().contains("b7"));
        let root = Path::new("/tmp/b7-fixture");
        for i in 0..16 {
            let v = sample(i, root).unwrap();
            v.validate(978).unwrap();
            assert_eq!(v.end_slot_exclusive - v.start_slot, 16);
        }
        assert!(sample(16, root).is_err());
        let v = sample(0, root).unwrap();
        for change in [
            "role",
            "hash",
            "selection",
            "epoch",
            "start",
            "end",
            "rank",
            "phase",
            "class",
            "campaign",
        ] {
            let mut bad = v.clone();
            let b = bad.b7.as_mut().unwrap();
            match change {
                "role" => b.cohort_role = "RESERVED_EVALUATION".into(),
                "hash" => b.accepted_report_sha256 = "a".repeat(64),
                "selection" => b.selection_sha256 = "b".repeat(64),
                "epoch" => bad.epoch += 1,
                "start" => bad.start_slot += 1,
                "end" => bad.end_slot_exclusive += 1,
                "rank" => b.rank += 1,
                "phase" => b.phase = 2,
                "class" => bad.sample_class = "ENGINEERING_VALIDATION_ONLY".into(),
                _ => b.campaign_id = "other".into(),
            }
            assert!(bad.validate(978).is_err(), "{change}");
        }
    }
}
