# B5 receipt-bound Raw byte inspection

> **Document status: ACTIVE.** Bounded successor to the remaining diagnostic
> capability of #111, tracked in #129 under #84. B4/B5 remain open and Unproven.
> This is engineering diagnostics, not a new decoder or Silver admission.

## Contract

The current Rust CAR verifier and the optional inspection use **one parser**.
`of1-verify-recorded RUN_ROOT --inspect-json` adds a diagnostic view to the
existing immutable reader. `--inspect-html` presents the same verified facts
as a standalone escaped page without scripts, network resources or a service.
The original verification mode and serialized integrity contract stay intact.

All current receipt/manifest/Raw audits, metadata-derived range checks,
CID hashes, canonical CBOR grammar, selected slot, graph closure and resource
bounds run before facts are returned. The reader reaudits the source afterwards.
Invalid receipts fail without a report; CAR or ordering errors quarantine with
no partial facts. Incomplete input has no partial inspection either. Metadata
only remains `NOT_ACQUIRED`; domain decoding remains `NOT_PERFORMED`, never a
fabricated zero. The reader never resumes the historical writer or renews its
expired authority.

Every archive node has its type, full CID bytes (lowercase hex), physical
ordinal, section/CBOR spans, source-ordered links and inline or standalone
DataFrames. The Transaction envelope pairs data and status metadata, separately
recording Block/Entry link ordinals and nullable declared transaction position.
Repeated Entry/Transaction references and conflicting declared positions reject,
matching current Bronze guards. Diagnostic order does not establish execution
semantics. Opaque checksums, frame indices and totals retain exact decimal
strings; no float, checksum reinterpretation or payload decode is introduced.
Rewards inline frames are included as well as transaction data/status frames.

Offsets are zero-based, half-open, relative to the assembled selected slot:

- section span includes the varint, CID and CBOR;
- CBOR span excludes varint and CID;
- data span includes only bytestring content, excluding its CBOR header.

Each slot includes a receipt-range map with sequence, original Raw path/hash,
assembled offset/length, Raw offset and original CAR offset. Split any span at
receipt boundaries. Within each intersection, Raw offset is `raw_offset +
assembled_position - assembled_offset`; CAR offset uses `car_offset` instead.
The receipt file hash is bound by the report's existing `bindings.receipts`.
CAR offsets and optional full-width integers are decimal strings. All offsets
within bounded buffers are exact integers. Physical order and link order are
both retained, with no sorting by wall clocks.

The inspector locates continuation nodes and preserves their links. It does
**not** perform payload concatenation, CRC/FNV checking, decompression, Solana
wire decoding or Pump interpretation; those retain their existing separate
Bronze gates. Deterministic **Raw receipt-range assembly** is tested across
chunk boundaries. It must not be confused with successful DataFrame or domain
decode. No fixture becomes authentic evidence.

## Selected existing input and reproducibility

Selection is fixed before implementation in external `selection.json`:

- run `of1-e978-metadata-09379cff-04`, slot 422496001;
- four existing metadata receipts and payload receipt sequence 4;
- original CAR range `[45110,650512)`, 605,402 bytes;
- Raw SHA256 `f0f29edfa5d07dfc9262208659dec18610fb9365b35a988463369db3894c998e`;
- original engineering class, no new acquisition or sample reclassification.

External reports, executable/build binding, initial/final inventories, exact
bounded commands and execution clocks live under
`/home/chupa/Solana-project/data-old-faithful-one/governance/b5-raw-spans-129-20260922`.
`LEESMIJ.md` there provides the actual report paths and private opening command.
Use the existing project wrapper, network-deny launcher and resource scope;
run only one heavy phase with the current two-CPU/5G-high/6G-max/256-task limits.
No copied historical WSL path is an instruction to relocate or rewrite evidence.

Build with the existing Rust 1.97.1 toolchain and lock, offline:

```bash
node scripts/with-toolchain.mjs -- cargo +1.97.1 build --offline --locked \
  --profile ci-test --manifest-path rust/of1-range-recorder/Cargo.toml \
  --bin of1-verify-recorded
# Execute through the approved resource and socket-deny wrapper; redirect only
# to a NEW output outside Git, never to the original run:
rust/of1-range-recorder/target/ci-test/of1-verify-recorded RUN_ROOT --inspect-json
rust/of1-range-recorder/target/ci-test/of1-verify-recorded RUN_ROOT --inspect-html
```

The report measures its binary and length-framed compiled-source identity.
Git revision, build profile/toolchain and operational clocks are separate
execution evidence. Rerunning unchanged binary/inputs yields identical JSON;
another binary appropriately changes its executable binding.

## Measured authentic result

The selected capture passes: **846 nodes, 845 links, 725 opaque Transaction
envelopes, 119 Entry nodes, one Block and one Rewards**. There are **1,451 inline
DataFrame spans** (data/status for each transaction plus Rewards), and **zero
standalone continuation nodes**. Thus continuation-location and multi-receipt
assembly tests are fixture evidence only. They are not observations in this
capture. All 218 files in the selected original run remain byte-identical.

The execution audit checks all 846 CBOR slices against original CID bytes and
SHA256, all section boundaries and all 1,451 inline span bounds against the
original Raw buffer. Independent sealed-fixture assertions check the exact
known payload bytes, including continuation data. Two authentic JSON executions
are byte-identical. Static HTML structure, escaping, every node row and embedded
identity are checked; no browser rendering/screenshot is claimed on this host.

## Source and retained invariants

The existing [CAR source receipt](../../schemas/acquisition/of1/car-source-receipt.json)
records official Jetstreamer commit `cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24`,
URLs, each source hash and retrieval date 2026-09-06. Its
[node](https://github.com/anza-xyz/jetstreamer/blob/cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24/jetstreamer-firehose/src/node.rs),
[Transaction](https://github.com/anza-xyz/jetstreamer/blob/cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24/jetstreamer-firehose/src/transaction.rs)
and [DataFrame](https://github.com/anza-xyz/jetstreamer/blob/cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24/jetstreamer-firehose/src/dataframe.rs)
contracts remain the structural source. The [signed-Shredding correction](OF1_RECORDED_CAR_VERIFICATION.md)
and its authentic/boundary regressions remain unchanged. No fresh source
round or historical program-activation claim is made.

#111's broader node/link caps and acceptance of conflicting declared positions
are deliberately not imported. The current 4096-node/16384-link and immutable
reader byte caps remain. Existing current-chain tests cover receipt corruption,
source mutation, symlink/FIFO refusal, metadata-only and missing/quarantine;
they exercise inspection too. Added regressions cover deliberately reordered
physical versus link order, repeated Entry/Transaction references, conflicting
position, exact frame/node span round trips and receipt-chunk assembly.

Nothing here modifies acquisition, Raw/Bronze/Silver datasets, 22 retained
Silver facts, seven Mayhem rejections, the prepared unsent Pump question,
network isolation, Rust ci-test safety flags or required CI/security gates.
Whole epoch/root membership, historical activation, full lifecycle, decoder
coverage and Research Ready remain unproven.
