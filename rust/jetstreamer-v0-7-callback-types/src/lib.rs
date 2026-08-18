//! Transport-free callback-type snapshot from Jetstreamer v0.7.0.
//!
//! Only the callback payload structs required by the offline reducer are
//! included. The upstream source identity is pinned in `Cargo.toml` metadata.

pub mod firehose;

pub const UPSTREAM_GIT_SHA: &str = "cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24";
pub const UPSTREAM_FIREHOSE_RS_SHA256: &str =
    "572ec56122e898f2318adc34d5998296b4fa0d31cf4bcab40950f5615a526a00";
pub const SOLANA_RUNTIME_CRATE_CHECKSUM: &str = solana_runtime::UPSTREAM_CRATE_CHECKSUM;
pub const SOLANA_RUNTIME_REWARDS_SOURCE_SHA256: &str =
    solana_runtime::UPSTREAM_PARTITIONED_EPOCH_REWARDS_SOURCE_SHA256;
