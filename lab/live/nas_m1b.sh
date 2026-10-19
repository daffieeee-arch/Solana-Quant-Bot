#!/bin/bash
# M1 C1b of the Rust live route (lab/docs/rust-live-route-design-2026-10-16.md §3; the porting spec is
# research/m1-c1b-signals-spec-2026-10-19.md §1.5, §6.4) on the NAS: lab-live signals writes the paper engine's F7 signals
# of a replay, and paper.journal_diff --scope signals finds them equal to paper.engine's own on the same range.
#
#   STEP=build   (bookworm runner, Rust 1.97.1): cargo test --release --locked of lab/live (its own tests, the extractor's
#                idl/sink tests compiled in, the typed-event emission against C1a's lines, and the shared golden signal
#                cases paper/tests/data/c1b_signal_cases.json in signals_core.rs) and of ALL of lab/core (the signal state:
#                clock, exact, universe, f7, farm, n2, signals with its cases, legs, venues), then the release binary into
#                $OUT/bin/. Run with LAB_DATA_ROOT=/lab/data-old-faithful-one/lab (the container mounts only /lab).
#   STEP=python  (the lab Python 3.13 runner):
#                1. the fast tests: test_signal_cases (the engine still writes the golden cases), test_journal_diff (its
#                   signals scope), test_engine, and test_lab_live_signals's guard test;
#                2. the legs regression after the typed-event refactor (A5): test_lab_live_legs on the whole of fwd1053
#                   and on dev2's arm range 450128159-450380000, the same counts and sha256 per window as C1a (~15 min
#                   each; LEGS=0 skips it);
#                3. test_lab_live_signals for R1 (dev2 450128159-450380000: three chunks across the 1041/1042 clock
#                   change, ~9-10k rows) and R2 (the whole of fwd1053 454896000-455328000: ~125 triggers) side by side:
#                   at most two paper.engine runs at once (~20-25 min). A1-A3 per run; each writes one measure line to
#                   job.log (rows and gates per side, Python's statuses past the farm gates as "decided in C2/C3", the
#                   Rust summary with the quote NULL count, times, peak RSS). Their logs are r1.log and r2.log. R3 (live
#                   bounds) waits for paper.engine's --dev-bounds, after the paper start.
#                With LAB_LEGS_REQUIRE=1 a missing binary or store fails, never skips.
#
#   STEP=build LAB_DATA_ROOT=/lab/data-old-faithful-one/lab bash lab/live/nas_m1b.sh      (bookworm runner)
#   STEP=python LAB_PY=/lab/venv/bin/python bash lab/live/nas_m1b.sh                      (lab runner)
#   STEP=python LEGS=0 LAB_PY=/lab/venv/bin/python bash lab/live/nas_m1b.sh               (without the legs regression)
#
# Both steps write to $DATA/backtests/live-m1b-<commit7>/ (job.log, cargo-test.log, cargo-test-core.log, bin/; r1.log and
# r2.log, and on a red run its pytest directory with both journals and journal_diff.md).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
OUT=$DATA/backtests/live-m1b-${COMMIT:0:7}
mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
: "${STEP:?STEP=build or STEP=python}"
log "start $STEP commit=$COMMIT out=$OUT"

if [ "$STEP" = build ]; then
  cd "$REPO/lab/live"
  # As nas_m1a.sh: the newest earlier checkout's target only saves time; this crate's sources (and the extractor's,
  # compiled in) are touched so cargo rebuilds them, never the other commit's binary or tests.
  if [ ! -d target ]; then
    prev=$(ls -dt /lab/code/repo-*/lab/live/target 2>/dev/null | head -1 || true)
    if [ -n "$prev" ]; then
      cp -a --reflink=auto "$prev" target && find src ../extractor/src -type f -exec touch {} + && log "target: a copy of $prev"
    fi
  fi
  cargo test --release --locked > "$OUT/cargo-test.log" 2>&1 || { tail -40 "$OUT/cargo-test.log" | tee -a "$OUT/job.log"; log "cargo test: FAILED"; exit 1; }
  grep -E "^test |^test result|^running" "$OUT/cargo-test.log" | tee -a "$OUT/job.log"
  # The golden cases and the C1a line pin must have run, not only passed (a test that is gone passes nothing).
  for t in signals_core::tests::every_golden_case_gives_the_engines_rows_and_state legs::tests::typed_events_render_the_c1a_lines_byte_for_byte \
           signals::tests::a_replay_writes_its_signals_the_states_and_the_summary signals_core::tests::the_seeds_are_pythons \
           signals_core::tests::t1_and_t2_with_the_sha256_keys; do
    grep -q "^test $t ... ok$" "$OUT/cargo-test.log" || { log "cargo test: $t did not run: FAILED"; exit 1; }
  done
  log "cargo test: ok ($(grep -c '^test .* ok$' "$OUT/cargo-test.log") tests ok)"
  # All of lab-core (a dependency's own tests do not run above): the signal state's unit tests and its cases.
  (cd ../core && cargo test --release --locked > "$OUT/cargo-test-core.log" 2>&1) || { tail -40 "$OUT/cargo-test-core.log" | tee -a "$OUT/job.log"; log "cargo test lab-core: FAILED"; exit 1; }
  grep -E "^test |^test result" "$OUT/cargo-test-core.log" | tee -a "$OUT/job.log"
  log "cargo test lab-core: ok ($(grep -c '^test .* ok$' "$OUT/cargo-test-core.log") tests ok)"
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
# The exports and the replays spill under this job's directory (one directory per process: two DuckDBs never share
# one), not the container's /tmp; the measure lines go into job.log.
export LAB_SPILL_MAX=16GB LAB_LIVE_MEASURE_LOG=$OUT/job.log LAB_LEGS_REQUIRE=1

# 1. The fast tests.
LAB_SPILL_DIR=$OUT/spill "$PY" -m pytest -q -rs --basetemp "$OUT/pytest-fast" paper/tests/test_signal_cases.py paper/tests/test_journal_diff.py \
  paper/tests/test_engine.py "paper/tests/test_lab_live_signals.py::test_the_guard_finds_rows_outside_the_universe_and_the_bounds" \
  2>&1 | tee -a "$OUT/job.log" | tail -15
rm -rf "$OUT/pytest-fast" "$OUT/spill"

# 2. The legs regression (A5): the typed events must give C1a's lines.
if [ "${LEGS:-1}" = 1 ]; then
  log "legs regression on fwd1053 (the whole store)"
  LAB_SPILL_DIR=$OUT/spill LAB_LEGS_STORE=$DATA/store/fwd1053.duckdb \
    "$PY" -m pytest -q -rs --basetemp "$OUT/pytest-legs" paper/tests/test_lab_live_legs.py 2>&1 | tee -a "$OUT/job.log" | tail -30
  rm -rf "$OUT/pytest-legs" "$OUT/spill"
  log "legs regression on dev2 [450128159, 450380000) (the arm range)"
  LAB_SPILL_DIR=$OUT/spill LAB_LEGS_STORE=$DATA/store/dev2.duckdb LAB_LEGS_FROM=450128159 LAB_LEGS_TO=450380000 \
    "$PY" -m pytest -q -rs --basetemp "$OUT/pytest-legs" paper/tests/test_lab_live_legs.py 2>&1 | tee -a "$OUT/job.log" | tail -30
  rm -rf "$OUT/pytest-legs" "$OUT/spill"
fi

# 3. The signals, R1 and R2 side by side (two paper.engine runs at once, each with lab-live beside it).
signals() {  # name store from to
  LAB_SPILL_DIR=$OUT/spill-$1 LAB_SIGNALS_STORE=$DATA/store/$2.duckdb LAB_SIGNALS_FROM=$3 LAB_SIGNALS_TO=$4 \
    "$PY" -m pytest -q -rs --basetemp "$OUT/pytest-$1" paper/tests/test_lab_live_signals.py > "$OUT/$1.log" 2>&1
}
log "signals R1 (dev2 [450128159, 450380000)) and R2 (fwd1053 [454896000, 455328000)), side by side"
signals r1 dev2 450128159 450380000 &
p1=$!
signals r2 fwd1053 454896000 455328000 &
p2=$!
rc1=0 rc2=0
wait $p1 || rc1=$?
wait $p2 || rc2=$?
for r in r1 r2; do
  log "$r (exit $([ $r = r1 ] && echo $rc1 || echo $rc2)):"
  tail -30 "$OUT/$r.log" | tee -a "$OUT/job.log"
done
rm -rf "$OUT/spill-r1" "$OUT/spill-r2"
[ $rc1 = 0 ] || log "R1 red: its journals and journal_diff.md stay under $OUT/pytest-r1"
[ $rc2 = 0 ] || log "R2 red: its journals and journal_diff.md stay under $OUT/pytest-r2"
# The comparison itself must have run and be green, not only its pytest exit (LAB review on #373): each run's measure
# line in job.log.
for run in "r1 dev2 450128159 450380000" "r2 fwd1053 454896000 455328000"; do
  read -r name store lo hi <<< "$run"
  if ! grep -qF "lab-live signals [$lo, $hi) on $store.duckdb: GREEN" "$OUT/job.log"; then
    log "$name: no GREEN measure line in job.log (the signals test did not run, or was red)"
    if [ $name = r1 ]; then rc1=${rc1/#0/1}; else rc2=${rc2/#0/1}; fi  # a failing exit stays as it was
  fi
done
[ $rc1 = 0 ] && [ $rc2 = 0 ] || { log "signals: RED"; exit 1; }
rm -rf "$OUT/pytest-r1" "$OUT/pytest-r2"
log "python: done"
