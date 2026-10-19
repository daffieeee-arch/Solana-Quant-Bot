#!/bin/bash
# M1 C3 of the Rust live route (lab/docs/rust-live-route-design-2026-10-16.md §3; the porting spec is
# research/m1-c3-risk-spec-2026-10-19.md) on the NAS: risk on in lab-live paper. C3-1 added the risk files' reader, C3-2
# the paper engine's risk (the gate, the entry's re-read, the size, the day's loss, the pool cooldown) with its golden
# cases, C3-3 the heartbeat rows and the paper wallets (their files byte for byte), C3-4 lab-live paper with risk on and
# the acceptance runs RC1-RC3 (spec §1).
#
#   STEP=build   (bookworm runner, Rust 1.97.1): cargo test --release --locked of lab/live (the risk reader against
#                paper/tests/data/c3_risk_load_cases.json, the paper engine with risk on against c3_risk_cases.json, and
#                every earlier test) and of ALL of lab/core, named tests required to have run; then the release binary into
#                $OUT/bin/. Run with LAB_DATA_ROOT=/lab/data-old-faithful-one/lab.
#   STEP=python  (the lab Python 3.13 runner): the C3 golden tables first (risk.load and the engine with risk on still give
#                every stored result, and regenerating each file gives the same file), then C2's golden cases and pins and
#                test_lab_live_paper's own checks. The *_CASES_REGEN switches are cleared: a regenerated table would test
#                itself. Then the acceptance (test_lab_live_paper.py with risk on), three runs side by side, each
#                paper.engine in its own process with lab-live paper beside it:
#                - RC1, dev2 [450144000, 450360000) (nas_hwm.sh's U range), the paper start's files (LAB_PAPER_MODE=config,
#                  their sha256 c1e36a0's), and Python's daily export and report made again from lab-live's journal;
#                - RC2, fwd1053 [454896000, 455328000) (the whole store), the paper start's files;
#                - RC3, dev2 [450128159, 450380000) (C2's R1), C2's arguments with risk on (LAB_PAPER_MODE=risk) and the
#                  seed proof.
#                Each: journal_diff 0 unexplained, the heartbeat row for row, the wallet files byte for byte, the per-chain
#                report 0 chains, no curve order at or after `to`, and its GREEN measure line in job.log with the risk
#                reasons per group on each side. Both journals of each run and the wallet files stay in journals-rc1/2/3.
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
           paper_core::tests::every_c3_case_gives_the_engines_writes_with_risk_on paper::tests::with_risk_on_each_group_reads_its_own_file \
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
  backtest/tests/test_exit_table.py backtest/tests/test_tau_table.py \
  paper/tests/test_lab_live_paper.py::test_the_chain_report_finds_the_root_of_a_red_pair \
  paper/tests/test_lab_live_paper.py::test_the_reference_checks_and_the_structure_rules \
  paper/tests/test_lab_live_paper.py::test_the_heartbeat_wallet_and_risk_checks \
  paper/tests/test_lab_live_paper.py::test_the_report_check_makes_the_reference_again > "$OUT/fast.log" 2>&1 || fast=$?
tail -15 "$OUT/fast.log" | tee -a "$OUT/job.log"
rm -rf "$OUT/pytest-fast" "$OUT/spill"
[ $fast = 0 ] || { log "fast tests: RED (exit $fast, fast.log)"; exit 1; }

# The acceptance: RC1, RC2 and RC3 side by side (spec §1).
export LAB_LIVE_BIN=$OUT/bin/lab-live LAB_LIVE_MEASURE_LOG=$OUT/job.log LAB_LEGS_REQUIRE=1 LAB_SPILL_MAX=16GB
[ -x "$LAB_LIVE_BIN" ] || { log "no binary at $LAB_LIVE_BIN: run STEP=build first"; exit 1; }
"$LAB_LIVE_BIN" --version | tee -a "$OUT/job.log"
ENGINE_FILES="paper/engine.py paper/feed.py paper/journal.py paper/risk.py paper/report.py backtest/replay.py backtest/strategies.py backtest/store.py backtest/streams.py backtest/venues.py backtest/costs.py"
sha256sum $ENGINE_FILES paper/k3-paper-2026-10-12.yaml paper/h-arms-2026-10-16.yaml paper/risk.yaml paper/risk-h.yaml \
  | tee "$OUT/ref-engine.sha256" | tee -a "$OUT/job.log"
run() {  # name store from to mode seed_proof report
  rm -rf "$OUT/journals-$1"
  LAB_SPILL_DIR=$OUT/spill-$1 LAB_PAPER_STORE=$DATA/store/$2.duckdb LAB_PAPER_FROM=$3 LAB_PAPER_TO=$4 LAB_PAPER_MODE=$5 \
    LAB_PAPER_SEED_PROOF=$6 LAB_PAPER_REPORT=$7 LAB_PAPER_KEEP=$OUT/journals-$1 $NOREGEN "$PY" -m pytest -q -rs -p no:cacheprovider \
    --basetemp "$OUT/pytest-$1" "paper/tests/test_lab_live_paper.py::test_the_rust_paper_journal_is_the_engines" > "$OUT/$1.log" 2>&1
}
RUNS=("rc1 dev2 450144000 450360000 config 0 1" "rc2 fwd1053 454896000 455328000 config 0 0" "rc3 dev2 450128159 450380000 risk 1 0")
log "RC1, RC2 and RC3 side by side"
pids=()
for r in "${RUNS[@]}"; do
  read -r name store lo hi mode proof report <<< "$r"
  run "$name" "$store" "$lo" "$hi" "$mode" "$proof" "$report" &
  pids+=($!)
done
rcs=()
for p in "${pids[@]}"; do
  rc=0; wait "$p" || rc=$?; rcs+=("$rc")
done
red=0
for i in "${!RUNS[@]}"; do
  read -r name store lo hi mode proof report <<< "${RUNS[$i]}"
  log "$name (exit ${rcs[$i]}):"
  tail -30 "$OUT/$name.log" | tee -a "$OUT/job.log"
  rm -rf "$OUT/spill-$name"
  # The comparison must have run and be green, not only its pytest exit: its GREEN measure line in job.log, in its mode.
  line=$(grep -F "lab-live paper [$lo, $hi) on $store.duckdb: GREEN" "$OUT/job.log" | grep -F "mode $mode;" | tail -1 || true)
  ok=1
  [ "${rcs[$i]}" = 0 ] && [ -n "$line" ] || ok=0
  [ "$proof" = 1 ] && ! grep -q "seed proof " <<< "$line" && ok=0
  [ "$report" = 1 ] && ! grep -qE "daily export and report from lab-live's journal: [1-9][0-9]* days equal" <<< "$line" && ok=0
  if [ $ok = 1 ]; then
    (cd "$OUT" && sha256sum journals-$name/*/journal.sqlite journals-$name/*/wallet*.json 2>/dev/null || true) | tee -a "$OUT/job.log"
    rm -rf "$OUT/pytest-$name"
  else
    log "$name: RED (both journals, the wallets and chains.md in $OUT/journals-$name, journal_diff.md under $OUT/pytest-$name)"
    red=1
  fi
done
[ $red = 0 ] || { log "acceptance: RED"; exit 1; }
log "python: done (RC1-RC3 GREEN; both journals kept in $OUT/journals-rc1, -rc2, -rc3; ref-engine.sha256)"
