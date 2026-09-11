# B5 offline Raw inspection — archival facts, not a transaction dataset

> **Document status: ACTIVE — bounded preparation, Fixture delivery evidence.**
> B4/#83 remains In Progress / ACTIVE NOW / Unproven. B5/#84 remains Backlog /
> NEXT / Unproven. This separately authorized offline work neither completes B4
> nor promotes B5. It creates no acquisition permission.

## The one useful case

The existing Rust Raw/receipt reader and CAR verifier feed a small deterministic
JSON/HTML quality report. The sealed CAR fixture exercises one archival
transaction envelope with its data and metadata kept together. Its opaque payload
contains structural fixture bytes, **not a decoded Solana transaction**. The report
can also read the preserved authentic metadata-only run; that input must stay
`METADATA_ONLY`, never become a transaction dataset by passing an integrity check.

This is an executable step toward Rust-owned Bronze, not a completed canonical
Bronze/Silver dataset. It adds no acquisition path, Pump variant, Python decoder,
Parquet writer, database or research framework. The separate physical Parquet
writer decision remains with full B5 and is not implied by JSON serialization.

## Evidence boundary

| Fact | What the report may establish | What it does not establish |
|---|---|---|
| Existing Raw publication | Audited manifest, attempt, receipt and content hashes; source and range identities | New acquisition, renewed approval or current endpoint availability |
| Metadata only | Actual published metadata bytes and receipts, with payload absent | Zero chain transactions, no Pump activity or a valid negative research result |
| CAR node | SHA-256 CID/content agreement and bounded pinned archival schema | Whole-CAR/root membership or universal historical source support |
| Slot envelope | Selected slot agrees with retained index/range and verified archival block | Finality, complete epoch coverage or activation of a Pump version |
| Transaction envelope | Ordered archival links and atomic opaque data + metadata spans | A parsed Solana transaction, signature, instructions, participants or executable price |
| DataFrame fields | Exact decimal integer strings, raw spans and continuation links | Decompression, checksum validation or semantic payload interpretation |

Root-to-slot membership stays `UNAVAILABLE`. Undecoded transaction/Pump/coin
counts remain explicitly unavailable, never numeric zero. Structural envelope
counts are named separately. A corrupt Raw/receipt identity fails the read without
a report. Receipt-valid but unsupported or contradictory archival bytes produce
an explicit `QUARANTINED` slot and reason, with no partially promoted facts. An
unpublished planned slot remains `GAP_NOT_PUBLISHED`; an index-reported absence
is not silently asserted to be a skipped chain slot.

## Ordering, provenance and replay

The inspection shares the existing verified CAR parser rather than creating a
second source decoder. Physical CAR section order and semantic block → entry →
transaction link order are separate fields. An archival ordinal does not stand
in for an unavailable Solana execution position. Duplicate/ambiguous ordering is
rejected. Data and metadata remain one envelope; downstream code may not release
only part of a transaction as historical observation evidence.

Spans refer to exact retained bytes, with their selected range and receipt
provenance. Integer wire fields are preserved exactly without floating point.
Acquisition wall-clock values are operational provenance only, not chain time,
features, labels or decisions. The deterministic report contains no fabricated
processing timestamp, live speed or inherited monitor ETA.

`reader_source_files` lists the inspected component's source paths, lengths and
hashes; `reader_source_sha256` binds that ordered list. It is explicitly a
`LISTED_READER_SOURCE_SET_NOT_EXECUTABLE_OR_BUILD_ATTESTATION`, not an invented
build/code attestation. Executed examples separately record their actual binary
hash and Git identity. The source acquisition binary remains a different identity.

The reader does not acquire `writer.lock`, resume a lease, repair a publication,
write into the run directory or require the reader executable to equal the old
acquisition executable. Reading old immutable evidence is distinct from resuming
its writer. A fresh acquisition still requires the separately prepared binary,
plan and operator GO; this report cannot authorize one.

This initial report is limited to one selected slot, 16 MiB assembled section
bytes, 65,536 nodes and 262,144 links. These are inspection bounds, not larger
acquisition budgets. A larger or incomplete selection is not silently reduced.
The already prepared B4 release executable remains frozen outside Git; merging a
later source change does not retroactively change that executable or its plan.

## Run the report locally

From the checkout containing this preparation, build only the offline reader:

```bash
TOOLCHAIN_RUN=/home/dmesdary/.local/share/solana-quant/run-with-toolchain
"$TOOLCHAIN_RUN" cargo +1.97.1 build --offline --locked --release \
  --manifest-path rust/of1-range-recorder/Cargo.toml \
  --features monitor --bin of1-bronze-evidence
REPORT_DIR="$(mktemp -d /home/dmesdary/solana-quant-data/b5-report.XXXXXX)"
RAW_ROOT=/home/dmesdary/solana-quant-data/runs/of1-e978-metadata-29d04959-01
rust/of1-range-recorder/target/release/of1-bronze-evidence "$RAW_ROOT" > "$REPORT_DIR/metadata.json"
rust/of1-range-recorder/target/release/of1-bronze-evidence "$RAW_ROOT" --html > "$REPORT_DIR/metadata.html"
```

The feature name `monitor` reuses its read-only publication auditor; this command
does not open its telemetry socket. `network-of1` and `tls-fixture` are not enabled.
Outputs go to a fresh directory outside Git, never into the immutable Raw root.
The original acquisition executable need not be rebuilt or changed for a read.

For the payload example use an **already completed local fixture** from the
[monitor scenario](B4_DOWNLOAD_MONITOR.md#local-browser-startup), not the authentic
metadata root. On the night-shift machine that preserved fixture is
`/home/dmesdary/solana-quant-data/runs/of1-monitor-payload-fixture-20260911-01`.
Replace `RAW_ROOT` with that root and choose new `fixture.json` / `fixture.html`
output names. Its 468 payload bytes are synthetic, distinct from the authentic
index's still-undownloaded 45,051-byte compatibility candidate.

Open the HTML directly from the Windows browser, or serve only that report folder
with the already available Python standard library (no installation):

```bash
python3 -m http.server 4174 --bind 127.0.0.1 --directory "$REPORT_DIR"
```

Then open **http://localhost:4174/metadata.html** (or `/fixture.html`). This is a
static local preview, not a new application backend, dataset server or B5 API.
Stop that preview with Ctrl-C; preserve the generated reports as review evidence.

## Verification and acceptance

- Existing CAR verification outputs and sealed vector bytes remain unchanged.
- Tests exercise atomic frames, source ordering, absent positions, raw integer
  boundaries, ambiguity, missing links, invalid CIDs and truncated bytes.
- Raw integration tests exercise metadata-only and complete fixture publications,
  identity/hash failures, exact source preservation and repeatable JSON/HTML.
- The existing OF1 offline gate runs the new reader/tests with socket syscalls
  denied, using the current reviewed dependency graph and unchanged Cargo.lock.
- A real preserved metadata import is authentic **metadata provenance only**;
  the additional payload example is unmistakably Fixture evidence.

## Smallest next step

Accept the B4 metadata decision, execute that separately authorized stage, then
derive an exact payload proposal from its actual receipts. An approved bounded
payload can next test the existing archival checks against authentic bytes.
Only supported, fully reconstructed Solana transaction packages can feed a later
bounded Pump decode and canonical Bronze/Silver promotion. No caller-supplied
`compatible_candidate_count` is authentic candidate-selection evidence.

The first coin view must use canonical mint identity, distinguish dataset-first
observation from creation, and expose missing metadata/coverage. There is no coin
panel or invented name/ticker in this preparation. The Python research workspace,
marimo/MLflow experiment example and evidence-linked AI questions remain later
deliveries on approved Silver, not additions to this PR.

Rollback is the removal of the isolated report command/module and inspection
surface; existing Raw bytes, receipts, fixture vectors and the acquisition path
remain unchanged. No evidence migration is required.
