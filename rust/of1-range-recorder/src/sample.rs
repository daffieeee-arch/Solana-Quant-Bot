//! One frozen pilot identity bound before acquisition, not a generic sampling API.
//! Absence on historical plans means engineering-only and cannot be retrofitted.
use crate::{durable::StoreError, sha256};
use serde::{Deserialize, Serialize};

pub const SELECTION_PLAN: &[u8] =
    include_bytes!("../../../research/columnar-query/pilot-proposal.json");
pub const SELECTION_PLAN_SHA256: &str =
    "df930707d0ece9915744aec7cf771c60e92f35298f2a6b4251aeb30d5a6d85a1";
pub const FIRST_SLOT: u64 = 422_669_516;
pub const END_SLOT: u64 = 422_669_519;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct SampleIdentity {
    pub schema: String,
    pub sample_class: String,
    pub selection_plan_sha256: String,
    pub epoch: u64,
    pub seed: String,
    pub algorithm: String,
    pub selected_center: u64,
    pub start_slot: u64,
    pub end_slot_exclusive: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub b7: Option<crate::b7::Binding>,
}

impl SampleIdentity {
    /// The reviewed fixed draw, not authority or a statement of data suitability.
    #[must_use]
    pub fn fixed_pilot() -> Self {
        Self {
            b7: None,
            schema: "OF1_FIXED_PILOT_SAMPLE_1".into(),
            sample_class: "RESEARCH_SAMPLING".into(),
            selection_plan_sha256: SELECTION_PLAN_SHA256.into(),
            epoch: 978,
            seed: "solana-quant-epoch978-pilot-v1-20260912".into(),
            algorithm: "SHA256_MIN_CENTER_1".into(),
            selected_center: 422_669_517,
            start_slot: FIRST_SLOT,
            end_slot_exclusive: END_SLOT,
        }
    }

    /// # Errors
    /// A different class, seed, proposal or selection requires a new reviewed lane.
    pub fn validate(&self, epoch: u64) -> Result<(), StoreError> {
        if self.b7.is_some() {
            crate::b7::validate(self)?;
            return if self.epoch == epoch {
                Ok(())
            } else {
                Err(StoreError::Identity)
            };
        }
        if self != &Self::fixed_pilot()
            || self.epoch != epoch
            || sha256(SELECTION_PLAN) != SELECTION_PLAN_SHA256
        {
            return Err(StoreError::Identity);
        }
        Ok(())
    }

    /// # Errors
    /// No center-only, replacement or widened selection is admitted.
    pub fn validate_range(&self, epoch: u64, first: u64, end: u64) -> Result<(), StoreError> {
        self.validate(epoch)?;
        if first != self.start_slot || end != self.end_slot_exclusive {
            return Err(StoreError::Identity);
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fixed_pilot_binds_frozen_source_and_expected_selection_rank() {
        let sample = SampleIdentity::fixed_pilot();
        sample.validate(978).unwrap();
        let p: serde_json::Value = serde_json::from_slice(SELECTION_PLAN).unwrap();
        assert_eq!(p["seed"], sample.seed);
        assert_eq!(p["epoch"], sample.epoch);
        assert_eq!(
            p["engineering_exclusions"],
            serde_json::json!([[422_496_000_u64, 422_496_005_u64]])
        );
        assert_eq!(p["context_before_slots"], 1);
        assert_eq!(p["context_after_slots"], 1);
        let mut bytes = b"OF1_PILOT_CENTER_1\0".to_vec();
        bytes.extend(sample.seed.as_bytes());
        bytes.extend(sample.epoch.to_le_bytes());
        bytes.extend(sample.selected_center.to_le_bytes());
        assert_eq!(
            sha256(&bytes),
            "000016e8217f2c8b36c8738d7714133bff9ae8557bbdecf79c7b7720c2bb3abc"
        );
        assert_eq!(sample.start_slot + 1, sample.selected_center);
        assert_eq!(sample.end_slot_exclusive, sample.selected_center + 2);
    }
}
