//! Physical projection only. No protocol decoding, network, or inferred domain fields.
pub mod admission;
pub mod columns;
pub mod shards;
pub mod storage;
mod token_balances;

use sha2::{Digest, Sha256};
use std::io;

#[must_use]
pub fn hash(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

pub fn invalid(message: impl std::fmt::Display) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message.to_string())
}

/// Identity of the physical projection implementation, separate from the decoder.
#[must_use]
pub fn source_sha256() -> String {
    let mut h = Sha256::new();
    for bytes in [
        include_bytes!("lib.rs").as_slice(),
        include_bytes!("columns.rs"),
        include_bytes!("token_balances.rs"),
        include_bytes!("storage.rs"),
        include_bytes!("shards.rs"),
        include_bytes!("admission.rs"),
        include_bytes!("main.rs"),
        include_bytes!("../Cargo.toml"),
        include_bytes!("../Cargo.lock"),
    ] {
        h.update((bytes.len() as u64).to_le_bytes());
        h.update(bytes);
    }
    hex::encode(h.finalize())
}
