# jetstreamer-firehose, vendored with one local patch

- **Source:** anza-xyz/jetstreamer at rev `1f9d30600fb479acdd5eb956ea2e19455c903590` (0.7.0), the extractor's pin. Packaged with `cargo package -p jetstreamer-firehose --no-verify`, so `Cargo.toml` is cargo's normalized manifest.
- **Licence:** MIT OR Apache-2.0. Both licence files are copied from the upstream workspace root.
- **Not vendored:** the upstream `tests/` and their dev-dependencies (`serial_test` stays as a normal dependency).

## The patch (`src/firehose.rs`, `convert_proto_rewards`)

Upstream returns `unsupported reward type` for any reward type it does not know. The rewards node is decoded for every block, because `jetstreamer-plugin` always passes `on_block` and `on_reward`. So one unknown reward fails the whole block with `NodeDecodingError`, and the firehose retries it.

Alpenglow (mainnet epoch 1064, 2026-10-21) adds `VATDebit` (proto type 6). yellowstone-faithful v0.7.30 (#409) archives it in block rewards.

What the patch changes:
- A reward of an unknown type is skipped instead of failing the block.
- Each skip is counted in `jetstreamer_firehose::firehose::UNKNOWN_REWARD_TYPES_SKIPPED`. The extractor reports it in `_manifest.json` (`counters.rewards_unknown_type_skipped`).
- Nothing else changes: the extractor reads no rewards.
- Test: `unknown_reward_type_tests` in `src/firehose.rs`.

Upstream issue: anza-xyz/jetstreamer#124. Drop this copy, and the `[patch]` in `../../extractor/Cargo.toml`, once a Jetstreamer release handles the type and the pin moves to it.
