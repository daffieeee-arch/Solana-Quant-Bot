//! The metered size of one update, shared by the recorder's byte meter (meter.rs) and lab-probe
//! (src/bin/lab-probe.rs), so both count Triton's bytes the same way: the protobuf size as received
//! (`encoded_len`, before the zstd transport compression) times `--wire-ratio`, rounded up.

pub fn metered(bytes: usize, wire_ratio: f64) -> u64 {
    (bytes as f64 * wire_ratio).ceil() as u64
}
