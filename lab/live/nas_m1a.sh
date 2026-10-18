#!/bin/bash
# M1 part 1 of the Rust live route (lab/docs/rust-live-route-design-2026-10-16.md §3) on the NAS: the lab-live
# replay skeleton reads the same event rows as Python, in the replay's order, and writes journal.py's schema.
#
#   STEP=build   (bookworm runner, Rust 1.97.1): cargo test --release --locked of lab/live (rows, journal, CLI and
#                the extractor's idl/sink tests compiled in), then the release binary into $OUT/bin/.
#                Run with LAB_DATA_ROOT=/lab/data-old-faithful-one/lab (the container mounts only /lab).
#   STEP=python  (the lab Python 3.13 runner): paper/tests/test_lab_live.py with LAB_LIVE_BIN=the binary
#                (the dev chunk's rows and order against DuckDB, a segment of the same range, the journal schema,
#                no key ties), plus test_journal_schema and test_journal_diff. Then the baseline for the streaming
#                step (LAB review on #319): lab-live reads the whole dev chunk 450144000-450360000 (15.5 M rows, all
#                held in memory before the sort; expect several GB) and job.log gets one line with its rows, parts,
#                key ties, read time and peak RSS. FULL_DEV=0 skips it.
#
#   STEP=build LAB_DATA_ROOT=/lab/data-old-faithful-one/lab bash lab/live/nas_m1a.sh      (bookworm runner)
#   STEP=python LAB_PY=/lab/venv/bin/python bash lab/live/nas_m1a.sh                      (lab runner)
#
# Both steps write to $DATA/backtests/live-m1a-<commit7>/ (job.log, cargo-test.log, bin/).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
OUT=$DATA/backtests/live-m1a-${COMMIT:0:7}
mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
: "${STEP:?STEP=build or STEP=python}"
log "start $STEP commit=$COMMIT out=$OUT"

if [ "$STEP" = build ]; then
  cd "$REPO/lab/live"
  # lab/live needs no libclang (no clang-sys or bindgen in its Cargo.lock), so a fresh target builds; the newest earlier
  # checkout's target only saves time (its registry crates are reused, this crate is rebuilt for its own path).
  if [ ! -d target ]; then
    prev=$(ls -dt /lab/code/repo-*/lab/live/target 2>/dev/null | head -1 || true)
    if [ -n "$prev" ]; then cp -a --reflink=auto "$prev" target && log "target: a copy of $prev"; fi
  fi
  cargo test --release --locked > "$OUT/cargo-test.log" 2>&1 || { tail -40 "$OUT/cargo-test.log" | tee -a "$OUT/job.log"; log "cargo test: FAILED"; exit 1; }
  grep -E "^test |^test result|^running" "$OUT/cargo-test.log" | tee -a "$OUT/job.log"
  log "cargo test: ok ($(grep -c '^test .* ok$' "$OUT/cargo-test.log") tests ok)"
  cargo build --release --locked >> "$OUT/cargo-test.log" 2>&1 || { tail -20 "$OUT/cargo-test.log"; log "cargo build: FAILED"; exit 1; }
  mkdir -p "$OUT/bin" && install -m 755 target/release/lab-live "$OUT/bin/lab-live"
  sha256sum "$OUT/bin/lab-live" | tee -a "$OUT/job.log"
  objdump -T "$OUT/bin/lab-live" | grep -o 'GLIBC_[0-9.]*' | sort -V | tail -1 | tee -a "$OUT/job.log"
  log "build: done"
  exit 0
fi

[ "$STEP" = python ] || { log "unknown STEP=$STEP"; exit 2; }
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
export LAB_LIVE_BIN=$OUT/bin/lab-live PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1
[ -x "$LAB_LIVE_BIN" ] || { log "no binary at $LAB_LIVE_BIN: run STEP=build first"; exit 1; }
cd "$REPO/lab"
"$LAB_LIVE_BIN" --version | tee -a "$OUT/job.log"
"$PY" -m pytest -q -rs paper/tests/test_lab_live.py paper/tests/test_journal_schema.py paper/tests/test_journal_diff.py 2>&1 \
  | tee -a "$OUT/job.log" | tail -8
if [ "${FULL_DEV:-1}" = 1 ]; then
  CHUNKS=$("$PY" -c 'from backtest import data; print(data.root())')
  rm -rf "$OUT/full-dev" && mkdir -p "$OUT/full-dev"
  log "full dev chunk: lab-live replay --chunks-root $CHUNKS --from 450144000 --to 450360000"
  "$LAB_LIVE_BIN" replay --chunks-root "$CHUNKS" --from 450144000 --to 450360000 --journal "$OUT/full-dev/journal" \
    2>> "$OUT/full-dev/lab-live.log" || { tail -5 "$OUT/full-dev/lab-live.log"; log "full dev chunk: FAILED"; exit 1; }
  "$PY" - "$OUT/full-dev/journal" <<'EOF' | tee -a "$OUT/job.log"
import json, sys
from paper.journal import Journal
j = Journal(sys.argv[1])
rows, m = json.loads(j.state("lab_live_rows")), json.loads(j.state("lab_live_measure"))
print(f"full dev chunk baseline: {m['rows']:,} rows from {rows['parts']} parts, key ties {rows['key_ties']}, "
      f"duplicate keys {rows['duplicate_keys']}, read {m['read_secs']:.1f} s, peak RSS {m['peak_rss_bytes'] / 1e9:.2f} GB")
EOF
fi
log "python: done"
