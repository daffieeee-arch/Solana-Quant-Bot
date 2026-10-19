#!/bin/bash
# M1 C3 of the Rust live route (lab/docs/rust-live-route-design-2026-10-16.md §3; the porting spec is
# research/m1-c3-risk-spec-2026-10-19.md) on the NAS: risk on in lab-live paper. C3-1 added the risk files' reader, C3-2
# the paper engine's risk (the gate, the entry's re-read, the size, the day's loss, the pool cooldown) with its golden
# cases, C3-3 the heartbeat rows and the paper wallets (their files byte for byte); the acceptance runs RC1-RC3 come with
# C3-4 and extend STEP=python.
#
#   STEP=build   (bookworm runner, Rust 1.97.1): cargo test --release --locked of lab/live (the risk reader against
#                paper/tests/data/c3_risk_load_cases.json, the paper engine with risk on against c3_risk_cases.json, and
#                every earlier test) and of ALL of lab/core, named tests required to have run; then the release binary into
#                $OUT/bin/. Run with LAB_DATA_ROOT=/lab/data-old-faithful-one/lab.
#   STEP=python  (the lab Python 3.13 runner): the C3 golden tables first (risk.load and the engine with risk on still give
#                every stored result, and regenerating each file gives the same file), then C2's golden cases and pins. The
#                *_CASES_REGEN switches are cleared: a regenerated table would test itself.
#
#   STEP=build LAB_DATA_ROOT=/lab/data-old-faithful-one/lab bash lab/live/nas_m1c3.sh      (bookworm runner)
#   STEP=python LAB_PY=/lab/venv/bin/python bash lab/live/nas_m1c3.sh                      (lab runner)
#
# Both steps write to $DATA/backtests/live-m1c3-<commit7>/ (job.log, cargo-test.log, cargo-test-core.log, bin/, risk.log,
# fast.log).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
OUT=$DATA/backtests/live-m1c3-${COMMIT:0:7}
mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
: "${STEP:?STEP=build or STEP=python}"
log "start $STEP commit=$COMMIT out=$OUT"

if [ "$STEP" = build ]; then
  cd "$REPO/lab/live"
  # As nas_m1c2.sh: the newest earlier checkout's target only saves time; this crate's sources (and the extractor's,
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
  # The new tests and C2's acceptance tests must have run, not only passed.
  for t in risk::tests::every_risk_load_case_gives_pythons_result_or_is_refused risk::tests::a_file_that_is_not_utf8_is_refused \
           paper_core::tests::every_c3_case_gives_the_engines_writes_with_risk_on \
           risk::tests::values_are_typed_as_pyyaml_types_them paper_core::tests::every_c2_case_gives_the_engines_writes \
           paper::tests::a_replay_writes_its_paper_journal_the_states_and_the_summary journal::tests::a_delete_removes_the_row_put_wrote_and_refuses_a_bad_key \
           signals_core::tests::every_golden_case_gives_the_engines_rows_and_state; do
    grep -q "^test $t ... ok$" "$OUT/cargo-test.log" || { log "cargo test: $t did not run: FAILED"; exit 1; }
  done
  log "cargo test: ok ($(grep -c '^test .* ok$' "$OUT/cargo-test.log") tests ok)"
  (cd ../core && cargo test --release --locked > "$OUT/cargo-test-core.log" 2>&1) || { tail -40 "$OUT/cargo-test-core.log" | tee -a "$OUT/job.log"; log "cargo test lab-core: FAILED"; exit 1; }
  grep -E "^test |^test result" "$OUT/cargo-test-core.log" | tee -a "$OUT/job.log"
  for t in risk::tests::the_size_rounds_half_to_even_as_python paper::tests::what_no_reference_runs_is_refused \
           positions::tests::positions_follow_the_replay_call_by_call exits::tests::decisions_follow_pythons_rules_call_by_call; do
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
export PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1
cd "$REPO/lab"

NOREGEN="env -u C3_RISK_CASES_REGEN -u C3_CASES_REGEN -u C2_CASES_REGEN -u C1B_CASES_REGEN"

# The C3 tables on their own (C3-1's risk.load table, C3-2's cases with risk on): every test must pass, none skipped.
$NOREGEN "$PY" -m pytest -q -rs -p no:cacheprovider paper/tests/test_risk_load_cases.py paper/tests/test_risk_cases.py > "$OUT/risk.log" 2>&1 \
  || { tail -15 "$OUT/risk.log" | tee -a "$OUT/job.log"; log "C3 tables: RED"; exit 1; }
tail -1 "$OUT/risk.log" | tee -a "$OUT/job.log"
want=$((4 + $("$PY" -c "import json; print(len(json.load(open('paper/tests/data/c3_risk_cases.json'))['cases']))")))
grep -q "^$want passed in" "$OUT/risk.log" || { log "C3 tables: not exactly $want passed"; exit 1; }

# The fast tests: C2's golden cases and the lab/core table pins (the engine still makes their writes).
fast=0
LAB_SPILL_DIR=$OUT/spill $NOREGEN "$PY" -m pytest -q -rs --basetemp "$OUT/pytest-fast" paper/tests/test_position_cases.py \
  paper/tests/test_signal_cases.py paper/tests/test_journal_diff.py paper/tests/test_engine.py backtest/tests/test_position_table.py \
  backtest/tests/test_exit_table.py backtest/tests/test_tau_table.py > "$OUT/fast.log" 2>&1 || fast=$?
tail -15 "$OUT/fast.log" | tee -a "$OUT/job.log"
rm -rf "$OUT/pytest-fast" "$OUT/spill"
[ $fast = 0 ] || { log "fast tests: RED (exit $fast, fast.log)"; exit 1; }
log "python: done"
