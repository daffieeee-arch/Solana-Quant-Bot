#!/bin/bash
# The recorder on yellowstone-grpc-client 15.0.0 / proto 14.0.1 (the versions Triton names for Alpenglow), on the NAS
# before any deploy: cargo test --release --locked of lab/recorder, the footer and certificate tests named, then the
# release binary into $OUT/bin/ (not deployed: the coordinator deploys after the Alpenglow switch, with the owner).
#
#   STEP=build LAB_DATA_ROOT=/lab/data-old-faithful-one/lab bash lab/recorder/nas_ys15.sh      (bookworm runner, Rust 1.97.1)
#
# Writes $DATA/backtests/recorder-ys15-<commit7>/ (job.log, cargo-test.log, bin/lab-recorder).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
OUT=$DATA/backtests/recorder-ys15-${COMMIT:0:7}
mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
: "${STEP:?STEP=build}"
[ "$STEP" = build ] || { log "unknown STEP=$STEP"; exit 2; }
log "start $STEP commit=$COMMIT out=$OUT"

cd "$REPO/lab/recorder"
grep -E '^(yellowstone-grpc-client|yellowstone-grpc-proto) ' Cargo.toml | tee -a "$OUT/job.log"
# The recorder reads no certificate field (it asks for none: include_certificates false).
if grep -rn 'block_final_cert\|skip_reward_cert\|notar_reward_cert' src; then
  log "a certificate field is read: FAILED"
  exit 1
fi
# A target without a built clang-sys must build it (bindgen), which needs libclang; the bookworm runner has none
# (nas_hwm.sh): then the newest recorder build's target.
if ! ldconfig -p 2>/dev/null | grep -q libclang && ! ls /usr/lib/llvm-*/lib/libclang*.so* >/dev/null 2>&1; then
  prev=$(ls -dt /lab/build/*-bookworm/lab/recorder/target 2>/dev/null | head -1 || true)
  [ -n "$prev" ] || { log "no libclang and no recorder target to reuse (/lab/build/*-bookworm/lab/recorder/target)"; exit 1; }
  rm -rf target
  cp -a --reflink=auto "$prev" target
  log "target: a copy of $prev (no libclang here)"
fi
cargo test --release --locked > "$OUT/cargo-test.log" 2>&1 || { tail -40 "$OUT/cargo-test.log" | tee -a "$OUT/job.log"; log "cargo test: FAILED"; exit 1; }
grep -E "^test result" "$OUT/cargo-test.log" | tee -a "$OUT/job.log"
for t in watch::tests::requests_never_ask_for_everything recorder::tests::block_footers_become_rows_and_the_first_one_is_noted_once \
         recorder::tests::a_footer_after_its_slot_closed_is_still_a_row_and_notes_the_switch; do
  grep -q "^test $t ... ok$" "$OUT/cargo-test.log" || { log "cargo test: $t did not run: FAILED"; exit 1; }
done
log "cargo test: ok ($(grep -c '^test .* ok$' "$OUT/cargo-test.log") tests ok)"
cargo build --release --locked >> "$OUT/cargo-test.log" 2>&1 || { tail -20 "$OUT/cargo-test.log" | tee -a "$OUT/job.log"; log "cargo build: FAILED"; exit 1; }
mkdir -p "$OUT/bin" && install -m 755 target/release/lab-recorder "$OUT/bin/lab-recorder"
sha256sum "$OUT/bin/lab-recorder" | tee -a "$OUT/job.log"
log "build: done (not deployed)"
