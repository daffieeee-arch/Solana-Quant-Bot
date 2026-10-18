#!/bin/bash
# M1 part 1 of the Rust live route (lab/docs/rust-live-route-design-2026-10-16.md §3) on the NAS: the lab-live
# replay skeleton reads the same event rows as Python, in the replay's order, and writes journal.py's schema.
#
#   STEP=build   (bookworm runner, Rust 1.97.1): cargo test --release --locked of lab/live (rows, journal, CLI and
#                the extractor's idl/sink tests compiled in), then the release binary into $OUT/bin/.
#                Run with LAB_DATA_ROOT=/lab/data-old-faithful-one/lab (the container mounts only /lab).
#   STEP=python  (the lab Python 3.13 runner): paper/tests/test_lab_live.py with LAB_LIVE_BIN=the binary
#                (the dev chunk's rows and order against DuckDB, a segment of the same range, the journal schema,
#                no key ties), plus test_journal_schema and test_journal_diff. Then the baseline and gate of the
#                in-memory read (LAB review on #319 and #367): 1/16, 1/8 and the four quarters of the dev chunk
#                450144000-450360000, one lab-live run each, one job.log line each (rows, fraction, key ties,
#                duplicate keys, read and sort time, peak RSS, bytes per row) and an extrapolation to the whole chunk,
#                which does not fit the runner's 24 GB (job 99u). Key ties or duplicate keys in any range: exit 1.
#                BASELINE=0 skips it.
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
  # checkout's target only saves time (its registry crates are reused). cp -a keeps that build's file times, so this
  # crate's sources (and the extractor's, compiled in) are touched to make cargo rebuild it, as build-recorder-bookworm.sh
  # does: never the other commit's binary or tests.
  if [ ! -d target ]; then
    prev=$(ls -dt /lab/code/repo-*/lab/live/target 2>/dev/null | head -1 || true)
    if [ -n "$prev" ]; then
      cp -a --reflink=auto "$prev" target && find src ../extractor/src -type f -exec touch {} + && log "target: a copy of $prev"
    fi
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
if [ "${BASELINE:-1}" = 1 ]; then
  # The baseline and gate of the in-memory read (LAB review on #367). One dev chunk (216,000 slots, 15.5 M rows) does
  # not fit this runner's 24 GB in memory (job 99u: killed after 33 s): streaming is a precondition for M1 parity on
  # whole chunks. So: 1/16 and 1/8 of the chunk for the scale, then its four quarters, which cover it for the gate:
  # every range must have no key ties and no duplicate keys (exit 1 otherwise). A range killed for its memory (137, the
  # expected OOM above the limit) is logged with its last progress line and is no gate failure; any other failure is.
  CHUNKS=$("$PY" -c 'from backtest import data; print(data.root())')
  DEV_LO=450144000 DEV_N=216000
  BASE=$OUT/baseline && rm -rf "$BASE" && mkdir -p "$BASE"
  gate=0
  for spec in 1/16:0:13500 1/8:0:27000 q1:0:54000 q2:54000:108000 q3:108000:162000 q4:162000:216000; do
    name=${spec%%:*} rest=${spec#*:}
    a=$((DEV_LO + ${rest%%:*})) b=$((DEV_LO + ${rest#*:})) dir=$BASE/${name//\//_}
    rc=0
    "$LAB_LIVE_BIN" replay --chunks-root "$CHUNKS" --from $a --to $b --journal "$dir" 2> "$dir.log" || rc=$?
    if [ $rc -ne 0 ]; then
      if [ $rc -eq 137 ]; then
        log "baseline $name [$a, $b): killed (137): the expected OOM above the runner's memory limit; last progress: $(grep -a 'so far' "$dir.log" | tail -1)"
      else
        gate=1  # any other failure leaves the range unchecked: red
        log "baseline $name [$a, $b): lab-live FAILED ($rc): $(tail -1 "$dir.log")"
      fi
      continue
    fi
    "$PY" - "$dir" "$name" $a $b $DEV_N <<'EOF' | tee -a "$OUT/job.log" || gate=1
import json, sys
from paper.journal import Journal
path, name, a, b, n = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
j = Journal(path)
rows, m = json.loads(j.state("lab_live_rows")), json.loads(j.state("lab_live_measure"))
j.close()
peak = m.get("peak_rss_bytes")
size = "peak RSS n/a" if not peak else f"peak RSS {peak / 1e9:.2f} GB, {peak / max(m['rows'], 1):,.0f} bytes per row"
print(f"baseline {name}: slots [{a}, {b}) = {(b - a) / n:.4f} of the dev chunk, {m['rows']:,} rows from {rows['parts']} parts, "
      f"key ties {rows['key_ties']}, duplicate keys {rows['duplicate_keys']}, read and sorted in {m['read_secs']:.1f} s, {size}")
sys.exit(1 if rows["key_ties"] or rows["duplicate_keys"] else 0)
EOF
  done
  "$PY" - "$BASE" <<'EOF' | tee -a "$OUT/job.log"
import glob, json, os, sys
from paper.journal import Journal
best = None
for d in sorted(glob.glob(os.path.join(sys.argv[1], "*"))):
    if os.path.isdir(d) and os.path.exists(os.path.join(d, "journal.sqlite")):
        j = Journal(d)
        m = json.loads(j.state("lab_live_measure") or "{}")
        j.close()
        if m.get("peak_rss_bytes") and (best is None or m["rows"] > best["rows"]):
            best = m
if best:
    full = 15_522_384  # the dev chunk's event rows (DuckDB, 2026-10-19)
    print(f"baseline: the whole dev chunk ({full:,} rows) in memory at {best['peak_rss_bytes'] / best['rows']:,.0f} bytes per row "
          f"(the largest range read): ~{best['peak_rss_bytes'] / best['rows'] * full / 1e9:.0f} GB, against the runner's 24 GB; "
          "job 99u was killed after 33 s: streaming is a precondition for M1 parity")
EOF
  [ $gate = 0 ] || { log "baseline: key ties or duplicate keys in a range, or a range that failed: RED"; exit 1; }
fi
log "python: done"
