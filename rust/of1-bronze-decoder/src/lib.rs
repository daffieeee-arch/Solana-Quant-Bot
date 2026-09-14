//! Offline Solana decoding and pinned Pump structural inspection; no transport,
//! acquisition resume, signer or independently promoted Pump candidate.
pub mod archive;
pub mod codec;
pub mod proto;
pub mod pump;
pub mod pump_buy;
pub mod pump_buy_exact_quote_v2;
pub mod pump_buy_variants;
pub mod pump_nested_buy;
pub mod pump_sell;
pub mod pump_sell16;
pub mod pump_sell_context;
pub mod report;
pub mod resources;
pub mod token_balances;

/// Actual compiled source/lock identity, independent of uncommitted Git claims.
#[must_use]
pub fn source_sha256() -> String {
    let mut framed = Vec::new();
    for bytes in [
        include_bytes!("lib.rs").as_slice(),
        include_bytes!("archive.rs"),
        include_bytes!("codec.rs"),
        include_bytes!("proto.rs"),
        include_bytes!("token_balances.rs"),
        include_bytes!("../sources/token-balance-evidence.json"),
        include_bytes!("report.rs"),
        include_bytes!("resources.rs"),
        include_bytes!("bin/of1-bronze-measure.rs"),
        include_bytes!("pump.rs"),
        include_bytes!("pump_buy.rs"),
        include_bytes!("pump_buy_exact_quote_v2.rs"),
        include_bytes!("pump_buy_exact_quote_context.rs"),
        include_bytes!("../sources/pump-buy-exact-quote-v2-evidence.json"),
        include_bytes!("pump_buy_variants.rs"),
        include_bytes!("pump_nested_buy.rs"),
        include_bytes!("pump_nested_buy_context.rs"),
        include_bytes!("../sources/pump-nested-buy-evidence.json"),
        include_bytes!("../sources/pump-buy24-evidence.json"),
        include_bytes!("pump_sell.rs"),
        include_bytes!("pump_sell16.rs"),
        include_bytes!("../sources/pump-sell16-evidence.json"),
        include_bytes!("pump_sell_context.rs"),
        include_bytes!("../sources/pump-nested-sell-evidence.json"),
        include_bytes!("../sources/pump-sell-evidence.json"),
        include_bytes!("../sources/pump-buy-evidence.json"),
        include_bytes!("main.rs"),
        include_bytes!("../Cargo.toml"),
        include_bytes!("../Cargo.lock"),
        include_bytes!("../sources.json"),
        include_bytes!("../sources/confirmed_block.proto"),
    ] {
        framed.extend_from_slice(&(bytes.len() as u64).to_le_bytes());
        framed.extend_from_slice(bytes);
    }
    // The reused CAR/receipt gate is part of this decoder's identity too.
    framed.extend_from_slice(of1_range_recorder::recorded_verification::source_sha256().as_bytes());
    for bytes in [
        include_bytes!("../../pump-protocol-v2/src/decode.rs").as_slice(),
        include_bytes!("../../pump-protocol-v2/src/registry.rs"),
        include_bytes!("../../pump-protocol-v2/src/lib.rs"),
        pump::SOURCE,
    ] {
        framed.extend_from_slice(&(bytes.len() as u64).to_le_bytes());
        framed.extend_from_slice(bytes);
    }
    of1_range_recorder::sha256(&framed)
}

use std::io;
pub(crate) fn invalid(message: impl std::fmt::Display) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message.to_string())
}
