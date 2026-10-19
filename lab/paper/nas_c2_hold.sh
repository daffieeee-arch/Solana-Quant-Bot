#!/bin/bash
# M1 C2-5 of the Rust live route (research/m1-c2-positionsim-spec-2026-10-19.md D4, §4.1, §7) on the NAS:
# curve_hold.parquet (paper/snapshot_export.py), the curve seed before a replay's from, is the unmodified paper engine's
# own seed, and lab-live reads it (live/src/curve_hold.rs).
#
#   STEP=build   (bookworm runner, Rust 1.97.1): cargo test --release --locked of lab/live (all its tests, the extractor's
#                idl/sink tests compiled in), with the curve_hold reader's tests and the snapshot's curve_hold meta test
#                required to have run. No binary: no command uses the reader yet (C2-4b).
#                Run with LAB_DATA_ROOT=/lab/data-old-faithful-one/lab (the container mounts only /lab).
#   STEP=python  (the lab Python 3.13 runner):
#                1. the fast tests: test_snapshot_export (the export's meta, farmer history and curve_hold: a recount on
#                   dev2, a tiny store with the refusals) and test_curve_hold_seed's synthetic part (the proof script on
#                   the tiny store);
#                2. the proof, paper/tests/curve_hold_proof.py over every mint (spec §4.1), R1 and R2 side by side:
#                   - R1, dev2 [450128159, 450380000): 33,285 mints, 4,981,887 earlier legs, 142,498 rows over 8,039
#                     mints, pinned;
#                   - R2, the whole of fwd1053 [454896000, 455328000): an empty file.
#                   Each run writes its line "curve_hold proof [LO, HI) on <store>.duckdb: EQUAL, <rows> rows, <mints>
#                   mints; ..." into job.log; a run without it, with these counts, is red. Their logs are r1.log and
#                   r2.log. The sha256 of the engine code the proof ran (backtest/replay.py, backtest/streams.py,
#                   paper/feed.py, paper/engine.py) goes into job.log too.
#
#   STEP=build LAB_DATA_ROOT=/lab/data-old-faithful-one/lab bash lab/paper/nas_c2_hold.sh      (bookworm runner)
#   STEP=python LAB_PY=/lab/venv/bin/python bash lab/paper/nas_c2_hold.sh                      (lab runner)
#
# Both steps write to $DATA/backtests/c2-hold-<commit7>/ (job.log, cargo-test.log; fast.log, r1.log and r2.log, and on a red
# run its export directory, export-r1 or export-r2).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
OUT=$DATA/backtests/c2-hold-${COMMIT:0:7}
mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
: "${STEP:?STEP=build or STEP=python}"
log "start $STEP commit=$COMMIT out=$OUT"

if [ "$STEP" = build ]; then
  cd "$REPO/lab/live"
  # As nas_m1a.sh: the newest earlier checkout's target only saves time; this crate's sources (and the extractor's,
  # compiled in) are touched so cargo rebuilds them, never the other commit's tests.
  if [ ! -d target ]; then
    prev=$(ls -dt /lab/code/repo-*/lab/live/target 2>/dev/null | head -1 || true)
    if [ -n "$prev" ]; then
      cp -a --reflink=auto "$prev" target && find src ../extractor/src -type f -exec touch {} + && log "target: a copy of $prev"
    fi
  fi
  cargo test --release --locked > "$OUT/cargo-test.log" 2>&1 || { tail -40 "$OUT/cargo-test.log" | tee -a "$OUT/job.log"; log "cargo test: FAILED"; exit 1; }
  grep -E "^test |^test result|^running" "$OUT/cargo-test.log" | tee -a "$OUT/job.log"
  # The reader's tests must have run, not only passed (a test that is gone passes nothing).
  for t in curve_hold::tests::the_nets_per_mint_and_trader_with_the_null_trader_its_own_key \
           curve_hold::tests::another_replays_file_or_none_is_refused_unless_the_replay_starts_with_the_store \
           curve_hold::tests::a_zero_a_missing_or_twice_given_net_and_a_double_column_are_refused \
           snapshot::tests::the_curve_hold_window_is_optional_and_whole; do
    grep -q "^test $t ... ok$" "$OUT/cargo-test.log" || { log "cargo test: $t did not run: FAILED"; exit 1; }
  done
  log "cargo test: ok ($(grep -c '^test .* ok$' "$OUT/cargo-test.log") tests ok)"
  log "build: done"
  exit 0
fi

[ "$STEP" = python ] || { log "unknown STEP=$STEP"; exit 2; }
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
export PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1
cd "$REPO/lab"
# The exports spill under this job's directory (one directory per process: two DuckDBs never share one), not the
# container's /tmp; the measure lines go into job.log.
export LAB_SPILL_MAX=16GB LAB_LIVE_MEASURE_LOG=$OUT/job.log
sha256sum backtest/replay.py backtest/streams.py paper/feed.py paper/engine.py | tee -a "$OUT/job.log"

# 1. The fast tests.
fast=0
LAB_SPILL_DIR=$OUT/spill LAB_STORE=$DATA/store/dev2.duckdb "$PY" -m pytest -q -rs --basetemp "$OUT/pytest-fast" \
  paper/tests/test_snapshot_export.py "paper/tests/test_curve_hold_seed.py::test_a_zero_entry_acts_like_none" \
  "paper/tests/test_curve_hold_seed.py::test_the_engines_seed_on_a_tiny_store_is_its_curve_hold" > "$OUT/fast.log" 2>&1 || fast=$?
tail -15 "$OUT/fast.log" | tee -a "$OUT/job.log"
rm -rf "$OUT/pytest-fast" "$OUT/spill"
[ $fast = 0 ] || { log "fast tests: RED (exit $fast, fast.log)"; exit 1; }

# 2. The proof, R1 and R2 side by side, every mint.
proof() {  # name store from to
  rm -rf "$OUT/export-$1"
  LAB_SPILL_DIR=$OUT/spill-$1 "$PY" -m paper.tests.curve_hold_proof "$DATA/store/$2.duckdb" "$3" "$4" --mints 0 \
    --out "$OUT/export-$1" > "$OUT/$1.log" 2>&1
}
log "curve_hold proof R1 (dev2 [450128159, 450380000)) and R2 (fwd1053 [454896000, 455328000)), side by side"
proof r1 dev2 450128159 450380000 &
p1=$!
proof r2 fwd1053 454896000 455328000 &
p2=$!
rc1=0 rc2=0
wait $p1 || rc1=$?
wait $p2 || rc2=$?
for r in r1 r2; do
  log "$r (exit $([ $r = r1 ] && echo $rc1 || echo $rc2)):"
  tail -30 "$OUT/$r.log" | tee -a "$OUT/job.log"
done
rm -rf "$OUT/spill-r1" "$OUT/spill-r2"
# The proof itself must have run over every mint and be equal, not only its exit: each run's EQUAL line, with its counts.
for run in "r1 dev2 450128159 450380000 142,498 8,039" "r2 fwd1053 454896000 455328000 0 0"; do
  read -r name store lo hi rows mints <<< "$run"
  if ! grep -qF "curve_hold proof [$lo, $hi) on $store.duckdb: EQUAL, $rows rows, $mints mints; every one of" "$OUT/job.log"; then
    log "$name: no EQUAL line with $rows rows, $mints mints in job.log (the proof did not run, or was red)"
    if [ $name = r1 ]; then rc1=${rc1/#0/1}; else rc2=${rc2/#0/1}; fi  # a failing exit stays as it was
  fi
done
[ $rc1 = 0 ] || log "R1 red: its export stays under $OUT/export-r1"
[ $rc2 = 0 ] || log "R2 red: its export stays under $OUT/export-r2"
[ $rc1 = 0 ] && [ $rc2 = 0 ] || { log "curve_hold proof: RED"; exit 1; }
rm -rf "$OUT/export-r1" "$OUT/export-r2"
log "python: done"
