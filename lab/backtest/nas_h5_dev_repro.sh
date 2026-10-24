#!/bin/bash
# Diagnostic (iii) on the NAS (lang: ~35-45 min, timeout 1.5 h; STEP0_ONLY=1: kort, ~1-2 min), dev only and read-only on
# its inputs (research/notes/pipeline-diagnostics-map-2026-10-24.json, result[2]; exploratory: no verdict changes,
# nothing re-tuned): E63's H5 cells (flow300_tp100_H6, F7 pool_reversed, q 0.5, farm organic; capital-ceiling-d15ac45,
# engine d15ac45) re-run with the H5 clean pin's engine 15fa3dd on dev2 and compared exactly (backtest.h5_dev_repro):
#   guards  the inputs' sha256s, dev2's meta == E63's store block, the reviewed capital_ceiling.py / costs.py, settle
#           rule 1, LAB_CORE python; the light tests
#   0       job 87 (bigtp-dev-1033-1046-4dbd34b, engine 4dbd34b: 15fa3dd's engine files) against E63 at 0.5 SOL d 2:
#           informative only (job 87 also ran N2); STEP0_ONLY=1 stops after it (rc 0 only when equal)
#   1       the tournament at 15fa3dd from a snapshot of ENGINE_REPO (git archive of the pin, or a copy without
#           __pycache__; every file checked against the pin's tree), E63's flags restricted to H5, dev2's whole window
#   2       capital_ceiling's main (this checkout, unchanged: rule 1, the daily loss on the exit day) on the new rows
#   3       compare: E63's side anchored first, then rows, day series (integer lamports) and ceiling.json fields; exact
#
#   cd /lab/code/repo-<this commit7> && LAB_PY=/lab/venv/bin/python bash lab/backtest/nas_h5_dev_repro.sh
#
# Inputs (each sha256-checked by step guards): $DATA/backtests/capital-ceiling-d15ac45 (parts, config.json,
# ceiling/ceiling.json), $E63_EXITDAY (capital-exitday-576db7a/e63/ceiling.json), $DATA/backtests/bigtp-dev-1033-1046-4dbd34b,
# the C3 keys, $DATA/store/dev2.duckdb (read-only), research/week2-defaults.yaml; ENGINE_REPO (/lab/code/repo-15fa3dd)
# is only read (git archive / tar -c), never run in.
# Overrides: WORKERS (4), ENGINE_REPO, E63_EXITDAY, C3_KEYS, MEMORY (8GB), LAB_SPILL_ROOT (/lab/tmp; its dir for this job is
# removed on exit, so one that is the output dir, inside it or holding it is refused), LAB_SPILL_MAX (24GB, E63's),
# STEP0_ONLY=1, RANGE=lo:hi (a dry run of the whole job: not evidence), CLEAN_BRIDGE=1 (step 4: refused).
# Writes a new $DATA/backtests/h5-dev-repro-<commit7>[-step0|-dry]/ (job.log, guards.log, pytest.log, step0/, step0.log,
# h5-dev2-15fa3dd/ with engine/, parts/ and config.json, tournament.log, ceiling-new/, repro/, SHA256SUMS of every file
# in it); an existing one is refused (move it aside; never delete evidence). RED = rc 1 with its reason in job.log.
set -euo pipefail
export LC_ALL=C LAB_CORE=python PYTHONDONTWRITEBYTECODE=1

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=$(realpath -m "${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}")
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
PIN=15fa3dd759d6ffd9b995879554ec29ae0bad749c
ENGINE_REPO=${ENGINE_REPO:-/lab/code/repo-15fa3dd}
STORE=$DATA/store/dev2.duckdb
E63=$DATA/backtests/capital-ceiling-d15ac45
E63_EXITDAY=${E63_EXITDAY:-$DATA/backtests/capital-exitday-576db7a/e63/ceiling.json}
BIGTP=$DATA/backtests/bigtp-dev-1033-1046-4dbd34b
C3_KEYS=${C3_KEYS:-$DATA/wallets/paidflow-dev-vps/markout-f7rev-txdelay.parquet}
WORKERS=${WORKERS:-4}
MEMORY=${MEMORY:-8GB}
NAME=h5-dev-repro-${COMMIT:0:7}
if [ "${STEP0_ONLY:-}" = 1 ]; then NAME=$NAME-step0; fi
if [ -n "${RANGE:-}" ]; then NAME=$NAME-dry; fi
OUT=$DATA/backtests/$NAME
SPILL_ROOT=$(realpath -m "${LAB_SPILL_ROOT:-/lab/tmp}")
SPILL=$SPILL_ROOT/$NAME
RUN_ID=h5-dev2-${PIN:0:7}
RUN=$OUT/$RUN_ID

fail() { echo "$(date -u +%FT%TZ) RED: $*" >&2; exit 1; }
overlap() {  # rc 0 when the two dirs, symlinks resolved, are one dir or one holds the other
  local a b
  a=$(realpath -m "$1")
  b=$(realpath -m "$2")
  case "$a/" in "$b/"*) return 0 ;; esac
  case "$b/" in "$a/"*) return 0 ;; esac
  return 1
}
[ -z "${CLEAN_BRIDGE:-}" ] || fail "CLEAN_BRIDGE: step 4 (the clean conventions with the case exclusions on dev2) needs LAB research's OK"
case "${RANGE:-}" in
  "") ;;
  [0-9]*:[0-9]*) [ "${STEP0_ONLY:-}" != 1 ] || fail "RANGE is for the tournament; STEP0_ONLY has none" ;;
  *) fail "RANGE=$RANGE is not lo:hi" ;;
esac
# Every output dir: absolute, new, and never under the event chunks, a store, the clean data or the hold-out.
for p in "$OUT" "$SPILL"; do
  case "$p" in
    /*) ;;
    *) fail "output $p is not absolute" ;;
  esac
  case "$p/" in
    */events/*|*/store/*|*clean-v1*|*/clean-test/*|*/backtests/clean-*|*/clean.lock*|*holdout*|*hold-out*)
      fail "output $p is under the events, a store, the clean data or the hold-out" ;;
  esac
  [ ! -e "$p" ] || fail "$p exists: every output dir is new (move it aside; never delete evidence)"
done
# The spill dir is removed on every exit: never the evidence dir, nor inside it, nor holding it (LAB_SPILL_ROOT=$DATA/backtests).
if overlap "$OUT" "$SPILL"; then fail "the spill dir $SPILL and the output $OUT overlap (LAB_SPILL_ROOT): the exit would delete evidence"; fi
mkdir -p "$OUT" "$SPILL"

finish() {  # on every exit: SHA256SUMS of every file written (the logs too), the spill dir removed (asserted first)
  local rc=$?
  (cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 -r sha256sum > SHA256SUMS) || rc=1
  if overlap "$OUT" "$SPILL"; then
    echo "$(date -u +%FT%TZ) RED: the spill dir $SPILL overlaps $OUT: not removed" >&2
    rc=1
  elif [ -n "$SPILL" ] && [ "$SPILL" = "$SPILL_ROOT/$NAME" ] && [ "${NAME#h5-dev-repro-}" != "$NAME" ] && [ -d "$SPILL" ]; then
    rm -rf -- "$SPILL"
  fi
  exit "$rc"
}
trap finish EXIT
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
red() { log "RED: $*"; exit 1; }
export LAB_SPILL_DIR=$SPILL LAB_SPILL_MAX=${LAB_SPILL_MAX:-24GB} PYTHONPATH=$REPO/lab
log "start commit=$COMMIT out=$OUT"
log "command: bash $0 $* (LAB_PY=$PY ENGINE_REPO=$ENGINE_REPO WORKERS=$WORKERS MEMORY=$MEMORY STEP0_ONLY=${STEP0_ONLY:-} RANGE=${RANGE:-} LAB_SPILL_DIR=$LAB_SPILL_DIR LAB_SPILL_MAX=$LAB_SPILL_MAX LC_ALL=$LC_ALL LAB_CORE=$LAB_CORE)"
cd "$REPO/lab"

step() {  # name, then the command: its output to <name>.log; RED on a non-zero rc
  local name=$1 rc=0
  shift
  log "$name: $*"
  "$@" > "$OUT/$name.log" 2>&1 || rc=$?
  log "$name: rc $rc; $(tail -1 "$OUT/$name.log")"
  [ "$rc" = 0 ] || red "$name ($name.log)"
}
INPUTS=(--e63 "$E63" --e63-exitday-json "$E63_EXITDAY" --c3-keys "$C3_KEYS" --store "$STORE")
step guards "$PY" -m backtest.h5_dev_repro inputs "${INPUTS[@]}" --bigtp "$BIGTP"
step pytest "$PY" -m pytest -q -p no:cacheprovider backtest/tests/test_h5_dev_repro.py backtest/tests/test_capital_ceiling.py \
  backtest/tests/test_settle_sql.py

# 0. Job 87 against E63 at 0.5 SOL d 2 (seconds): informative only.
rc=0
log "step0: $PY -m backtest.h5_dev_repro compare --mode step0 --new $BIGTP ${INPUTS[*]} --out $OUT/step0"
"$PY" -m backtest.h5_dev_repro compare --mode step0 --new "$BIGTP" "${INPUTS[@]}" --out "$OUT/step0" --memory "$MEMORY" \
  --threads 4 > "$OUT/step0.log" 2>&1 || rc=$?
log "step0: rc $rc; $(tail -1 "$OUT/step0.log")"
case $rc in
  0) ;;
  3) log "INFO: step 0 differs: informative only (job 87 also ran N2: the N2_F7 cluster-key confound); step 1 decides" ;;
  *) red "step 0 refused or failed (step0.log)" ;;
esac
if [ "${STEP0_ONLY:-}" = 1 ]; then
  [ "$rc" = 0 ] || red "STEP0_ONLY: step 0 differs (informative only; the full job decides)"
  log "done: step 0 GREEN (step0/repro.md)"
  exit 0
fi

# 1. The tournament at 15fa3dd from a snapshot in the run dir.
mkdir "$RUN" "$RUN/engine"
if git -C "$ENGINE_REPO" cat-file -e "$PIN^{commit}" 2>/dev/null; then
  log "snapshot: git -C $ENGINE_REPO archive $PIN lab/backtest"
  git -C "$ENGINE_REPO" archive "$PIN" lab/backtest | tar -x -C "$RUN/engine" --strip-components=1 || red "git archive of $PIN failed"
else
  [ -d "$ENGINE_REPO/lab/backtest" ] || red "no $ENGINE_REPO/lab/backtest and no git object $PIN"
  log "snapshot: a copy of $ENGINE_REPO/lab/backtest without __pycache__ (no git object $PIN there)"
  tar -c -C "$ENGINE_REPO/lab" --exclude=__pycache__ backtest | tar -x -C "$RUN/engine" || red "the copy of $ENGINE_REPO/lab/backtest failed"
fi
echo "$PIN" > "$RUN/engine/COMMIT"
step engine "$PY" -m backtest.h5_dev_repro engine "$RUN/engine/backtest"
ARGS=(--families F7 --segments pool_reversed --variants flow300_tp100_H6 --f7-grid v1,exits,big --sizes 0.5,1,1.25,2,2.5,5,10
      --delays 2,3 --q 0.5 --tau empirical --tol 0.05 --no-report --workers "$WORKERS" --run-id "$RUN_ID")
DRY=()
if [ -n "${RANGE:-}" ]; then
  ARGS+=(--range "$RANGE")
  DRY=(--dry-run)
  log "DRY RUN: --range $RANGE (not evidence)"
fi
log "tournament: cd $RUN/engine && LAB_STORE=$STORE LAB_BACKTESTS=$OUT LAB_ENGINE_COMMIT=$PIN PYTHONPATH=. nice -n 10 $PY -m backtest.tournament ${ARGS[*]}"
rc=0
(cd "$RUN/engine" && LAB_STORE=$STORE LAB_BACKTESTS=$OUT LAB_ENGINE_COMMIT=$PIN PYTHONPATH=. nice -n 10 "$PY" -m backtest.tournament "${ARGS[@]}") \
  > "$OUT/tournament.log" 2>&1 || rc=$?
log "tournament: rc $rc; $(tail -1 "$OUT/tournament.log")"
[ "$rc" = 0 ] || red "the tournament failed (tournament.log)"
[ -f "$RUN/config.json" ] || red "the tournament wrote no $RUN/config.json"

# 2. E63's module, unchanged, on the new rows.
step ceiling "$PY" -m backtest.capital_ceiling --parts "$RUN" --c3-keys "$C3_KEYS" --store "$STORE" --out "$OUT/ceiling-new" \
  --memory "$MEMORY" --threads 4

# 3. The comparison.
rc=0
log "repro: $PY -m backtest.h5_dev_repro compare --mode full --new $RUN --engine-dir $RUN/engine/backtest --new-json $OUT/ceiling-new/ceiling.json ${INPUTS[*]} --out $OUT/repro ${DRY[*]}"
"$PY" -m backtest.h5_dev_repro compare --mode full --new "$RUN" --engine-dir "$RUN/engine/backtest" \
  --new-json "$OUT/ceiling-new/ceiling.json" "${INPUTS[@]}" --out "$OUT/repro" --memory "$MEMORY" --threads 4 "${DRY[@]}" \
  > "$OUT/repro.log" 2>&1 || rc=$?
log "repro: rc $rc; $(tail -1 "$OUT/repro.log")"
[ "$rc" = 0 ] || red "step 3: a difference or a refusal (repro.log, repro/repro.md)"
log "done: $(tail -1 "$OUT/repro.log") (repro/repro.md)"
