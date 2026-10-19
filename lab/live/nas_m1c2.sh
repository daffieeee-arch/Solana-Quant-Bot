#!/bin/bash
# M1 C2 of the Rust live route (lab/docs/rust-live-route-design-2026-10-16.md §3; the porting spec is
# research/m1-c2-positionsim-spec-2026-10-19.md §1.5, §6.4) on the NAS: lab-live paper writes the paper engine's journal
# of a replay with risk inert, and paper.journal_diff (default scope) finds it equal to paper.engine's own on the same
# range: signals, orders, fills, exits and positions.
#
#   STEP=build   (bookworm runner, Rust 1.97.1): cargo test --release --locked of lab/live (the golden journal writes of
#                paper/tests/data/c2_position_cases.json and the C1b cases with their statuses in paper_core.rs, the
#                `paper` command on a small store, the journal's delete, and the rest) and of ALL of lab/core (the paper
#                engine, the positions, the exits and their generated cases), named tests required to have run; then the
#                release binary into $OUT/bin/. Run with LAB_DATA_ROOT=/lab/data-old-faithful-one/lab.
#   STEP=python  (the lab Python 3.13 runner):
#                1. the fast tests: the C2 and C1b golden cases (the engine still makes their writes), journal_diff,
#                   test_engine, the lab/core table pins (positions, exits, tau) and test_lab_live_paper's own checks
#                   (the per-chain report on a synthetic red pair, the reference checks, the structure rules, the count of
#                   curve orders at or after `to`, the keep of both journals);
#                2. test_lab_live_paper for R1 (dev2 450128159-450380000, with the seed proof) and R2 (the whole of
#                   fwd1053 454896000-455328000) side by side: paper.engine with the literal --variants 0.04:600 in its own
#                   process and lab-live paper beside it; A1-A5, no curve order with entry_slot >= `to` (in both
#                   journals and lab-live's summary), the per-chain report (0 chains), and one measure line each in job.log
#                   (`lab-live paper [LO, HI) on <store>.duckdb: GREEN, ...`); on red the report's lines go there first.
#                   Their logs are r1.log and r2.log;
#                3. the curve_hold proof again (paper/tests/curve_hold_proof.py over every mint: R1 142,498 rows over
#                   8,039 mints, R2 0), its EQUAL lines in job.log.
#                Both journals of each run stay, green or red (coordinator): journals-r1/ and journals-r2/, each with
#                python/journal.sqlite (the reference) and rust/journal.sqlite (lab-live), lab-live's summary (rust.json)
#                and trace, the groups and cost scenario, and chains.md; their sha256 in job.log, and ref-engine.sha256 (the
#                engine files the references were made with). The pytest directories and spill go on green.
#
#   STEP=build LAB_DATA_ROOT=/lab/data-old-faithful-one/lab bash lab/live/nas_m1c2.sh      (bookworm runner)
#   STEP=python LAB_PY=/lab/venv/bin/python bash lab/live/nas_m1c2.sh                      (lab runner)
#
# Both steps write to $DATA/backtests/live-m1c2-<commit7>/ (job.log, cargo-test.log, cargo-test-core.log, bin/; r1.log,
# r2.log, the proofs' logs, journals-r1/, journals-r2/; on a red run also its pytest directory with journal_diff.md).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
OUT=$DATA/backtests/live-m1c2-${COMMIT:0:7}
mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
: "${STEP:?STEP=build or STEP=python}"
log "start $STEP commit=$COMMIT out=$OUT"

if [ "$STEP" = build ]; then
  cd "$REPO/lab/live"
  # As nas_m1b.sh: the newest earlier checkout's target only saves time; this crate's sources (and the extractor's,
  # compiled in) are touched so cargo rebuilds them, never the other commit's binary or tests.
  if [ ! -d target ]; then
    prev=$(ls -dt /lab/code/repo-*/lab/live/target 2>/dev/null | head -1 || true)
    if [ -n "$prev" ]; then
      log "target: a copy of $prev"
      cp -a "$prev" target
      touch src/*.rs src/*/*.rs ../extractor/src/*.rs 2>/dev/null || true
    fi
  fi
  cargo test --release --locked > "$OUT/cargo-test.log" 2>&1 || { tail -40 "$OUT/cargo-test.log" | tee -a "$OUT/job.log"; log "cargo test: FAILED"; exit 1; }
  grep -E "^test |^test result|^running" "$OUT/cargo-test.log" | tee -a "$OUT/job.log"
  # The golden cases, the command's own test and the delete must have run, not only passed.
  for t in paper_core::tests::every_c2_case_gives_the_engines_writes paper_core::tests::the_c1b_cases_carry_the_engines_statuses \
           paper::tests::a_replay_writes_its_paper_journal_the_states_and_the_summary paper::tests::the_variants_must_be_m1s_specs_of_the_primary_groups_names \
           paper::tests::the_seed_hash_is_over_the_sorted_non_zero_nets journal::tests::a_delete_removes_the_row_put_wrote_and_refuses_a_bad_key \
           signals_core::tests::every_golden_case_gives_the_engines_rows_and_state; do
    grep -q "^test $t ... ok$" "$OUT/cargo-test.log" || { log "cargo test: $t did not run: FAILED"; exit 1; }
  done
  log "cargo test: ok ($(grep -c '^test .* ok$' "$OUT/cargo-test.log") tests ok)"
  (cd ../core && cargo test --release --locked > "$OUT/cargo-test-core.log" 2>&1) || { tail -40 "$OUT/cargo-test-core.log" | tee -a "$OUT/job.log"; log "cargo test lab-core: FAILED"; exit 1; }
  grep -E "^test |^test result" "$OUT/cargo-test-core.log" | tee -a "$OUT/job.log"
  for t in paper::tests::what_no_reference_runs_is_refused paper::tests::a_pool_position_from_its_signal_to_its_exit \
           positions::tests::positions_follow_the_replay_call_by_call positions::tests::single_reruns_are_pythons \
           exits::tests::decisions_follow_pythons_rules_call_by_call; do
    grep -q "^test $t ... ok$" "$OUT/cargo-test-core.log" || { log "cargo test lab-core: $t did not run: FAILED"; exit 1; }
  done
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
export LAB_SPILL_MAX=16GB LAB_LIVE_MEASURE_LOG=$OUT/job.log LAB_LEGS_REQUIRE=1
ENGINE_FILES="paper/engine.py paper/feed.py paper/journal.py backtest/replay.py backtest/strategies.py backtest/store.py backtest/streams.py backtest/venues.py backtest/costs.py"

# 1. The fast tests.
fast=0
LAB_SPILL_DIR=$OUT/spill "$PY" -m pytest -q -rs --basetemp "$OUT/pytest-fast" paper/tests/test_position_cases.py paper/tests/test_signal_cases.py \
  paper/tests/test_journal_diff.py paper/tests/test_engine.py backtest/tests/test_position_table.py backtest/tests/test_exit_table.py \
  backtest/tests/test_tau_table.py "paper/tests/test_lab_live_paper.py::test_the_chain_report_finds_the_root_of_a_red_pair" \
  "paper/tests/test_lab_live_paper.py::test_the_reference_checks_and_the_structure_rules" "paper/tests/test_lab_live_paper.py::test_the_seed_hash_is_lab_lives" \
  "paper/tests/test_lab_live_paper.py::test_curve_orders_at_or_after_to_are_counted_whatever_their_status" \
  "paper/tests/test_lab_live_paper.py::test_the_keep_copies_both_journals_whole" \
  > "$OUT/fast.log" 2>&1 || fast=$?
tail -15 "$OUT/fast.log" | tee -a "$OUT/job.log"
rm -rf "$OUT/pytest-fast" "$OUT/spill"
[ $fast = 0 ] || { log "fast tests: RED (exit $fast, fast.log)"; exit 1; }

# 2. The paper journals, R1 and R2 side by side (two paper.engine runs at once, each with lab-live beside it).
paper() {  # name store from to seed_proof
  rm -rf "$OUT/journals-$1"
  LAB_SPILL_DIR=$OUT/spill-$1 LAB_PAPER_STORE=$DATA/store/$2.duckdb LAB_PAPER_FROM=$3 LAB_PAPER_TO=$4 LAB_PAPER_SEED_PROOF=$5 \
    LAB_PAPER_KEEP=$OUT/journals-$1 "$PY" -m pytest -q -rs --basetemp "$OUT/pytest-$1" \
    "paper/tests/test_lab_live_paper.py::test_the_rust_paper_journal_is_the_engines" > "$OUT/$1.log" 2>&1
}
sha256sum $ENGINE_FILES | tee "$OUT/ref-engine.sha256" | tee -a "$OUT/job.log"
log "paper R1 (dev2 [450128159, 450380000), seed proof) and R2 (fwd1053 [454896000, 455328000)), side by side"
paper r1 dev2 450128159 450380000 1 &
p1=$!
paper r2 fwd1053 454896000 455328000 0 &
p2=$!
rc1=0 rc2=0
wait $p1 || rc1=$?
wait $p2 || rc2=$?
for r in r1 r2; do
  log "$r (exit $([ $r = r1 ] && echo $rc1 || echo $rc2)):"
  tail -30 "$OUT/$r.log" | tee -a "$OUT/job.log"
done
rm -rf "$OUT/spill-r1" "$OUT/spill-r2"
for r in r1 r2; do
  if [ -f "$OUT/journals-$r/python/journal.sqlite" ] && [ -f "$OUT/journals-$r/rust/journal.sqlite" ]; then
    (cd "$OUT" && sha256sum journals-$r/python/journal.sqlite journals-$r/rust/journal.sqlite) | tee -a "$OUT/job.log"
  else
    log "$r: its journals were not kept (the run stopped before its comparison)"
  fi
done
[ $rc1 = 0 ] || log "R1 red: both journals and chains.md in $OUT/journals-r1, journal_diff.md under $OUT/pytest-r1"
[ $rc2 = 0 ] || log "R2 red: both journals and chains.md in $OUT/journals-r2, journal_diff.md under $OUT/pytest-r2"
# The comparison must have run and be green, not only its pytest exit: each run's measure line in job.log.
for run in "r1 dev2 450128159 450380000" "r2 fwd1053 454896000 455328000"; do
  read -r name store lo hi <<< "$run"
  if ! grep -qF "lab-live paper [$lo, $hi) on $store.duckdb: GREEN" "$OUT/job.log"; then
    log "$name: no GREEN measure line in job.log (the paper test did not run, or was red)"
    if [ $name = r1 ]; then rc1=${rc1/#0/1}; else rc2=${rc2/#0/1}; fi  # a failing exit stays as it was
  fi
done
# R1's GREEN line must carry the seed proof (LAB_PAPER_SEED_PROOF=1): every curve position's seed equal to the engine's.
grep -F "lab-live paper [450128159, 450380000) on dev2.duckdb: GREEN" "$OUT/job.log" | grep -q "seed proof " \
  || { log "r1: no seed proof in its GREEN measure line"; rc1=${rc1/#0/1}; }
[ $rc1 = 0 ] && [ $rc2 = 0 ] || { log "paper: RED"; exit 1; }
rm -rf "$OUT/pytest-r1" "$OUT/pytest-r2"

# 3. The curve_hold proof again, R1 and R2 side by side, every mint.
proof() {  # name store from to
  rm -rf "$OUT/export-$1"
  LAB_SPILL_DIR=$OUT/spill-$1 "$PY" -m paper.tests.curve_hold_proof "$DATA/store/$2.duckdb" "$3" "$4" --mints 0 \
    --out "$OUT/export-$1" > "$OUT/proof-$1.log" 2>&1
}
log "curve_hold proof R1 and R2, side by side"
proof r1 dev2 450128159 450380000 &
p1=$!
proof r2 fwd1053 454896000 455328000 &
p2=$!
rc1=0 rc2=0
wait $p1 || rc1=$?
wait $p2 || rc2=$?
for r in r1 r2; do
  tail -5 "$OUT/proof-$r.log" | tee -a "$OUT/job.log"
done
rm -rf "$OUT/spill-r1" "$OUT/spill-r2"
for run in "r1 dev2 450128159 450380000 142,498 8,039" "r2 fwd1053 454896000 455328000 0 0"; do
  read -r name store lo hi rows mints <<< "$run"
  if ! grep -qF "curve_hold proof [$lo, $hi) on $store.duckdb: EQUAL, $rows rows, $mints mints; every one of" "$OUT/job.log"; then
    log "$name: no EQUAL curve_hold line with $rows rows, $mints mints in job.log"
    if [ $name = r1 ]; then rc1=${rc1/#0/1}; else rc2=${rc2/#0/1}; fi
  fi
done
[ $rc1 = 0 ] && [ $rc2 = 0 ] || { log "curve_hold proof: RED (the exports stay under $OUT/export-r1, export-r2)"; exit 1; }
rm -rf "$OUT/export-r1" "$OUT/export-r2"
log "python: done (both journals kept in $OUT/journals-r1, $OUT/journals-r2; ref-engine.sha256)"
