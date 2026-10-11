#!/usr/bin/env bash
# Families v2 chain (coordinator decision 2026-10-11), unattended. Steps:
#   1. build: store 1033-1046 into store/dev2.duckdb. The old file's per-epoch counts are kept,
#      then the file is deleted right before the build.
#   2. validate: the counts against the old store, plus the backtest and paper tests on the new
#      store. test_reversed_fills is check b: exact replay of reversed fills, fee tier included.
#   3. f7rev: F7 in reversed pools at 0.5/1/2 SOL, d 1/2 (check a, plus the c-e tables).
#   4. full: the families v2 run.
# Each step must pass, and a failure stops the chain. A rerun skips the steps already done.
# The chain takes heavy.lock once (fd 9). Every heavy step runs in its own memory-capped scope,
# and the disk health gate runs before each store chunk and each tournament batch.
#
#   cp scripts/families-v2-chain.sh $LAB/logs/ && systemd-run --user --unit lab-families-v2-chain -E LAB_DIR=$PWD \
#     $LAB/logs/families-v2-chain.sh   (bash reads a running script lazily: never run it from the worktree)
set -euo pipefail

lab=${LAB_DIR:-$(cd "$(dirname "$0")/.." && pwd)}  # set when running a frozen copy of this script
LAB=/home/chupa/Solana-project/data-old-faithful-one/lab
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
STORE=$LAB/store/dev2.duckdb
NOTES=$LAB/store/notes
STATE=$LAB/logs/families-v2-chain
LOG=$STATE.log
mkdir -p "$STATE.done" "$NOTES"
log() { echo "$(date -Is) $*" >> "$LOG"; }
done_() { [[ -e $STATE.done/$1 ]]; }
mark() { date -Is > "$STATE.done/$1"; log "step $1 done"; }
fail() { log "FAILED: $*"; exit 1; }
cd "$lab"

exec 9>"$LAB/locks/heavy.lock"
until flock -n 9; do log "waiting for heavy.lock"; sleep 300; done
log "chain started (heavy.lock held), lab $(git rev-parse --short HEAD)"

gate() { "$PY" -c "from backtest import health; health.wait_ok(lambda m: print(m, flush=True), '$1')" >> "$LOG" 2>&1; }

if ! done_ build; then
  "$PY" -c "from backtest.store import load_defaults, period_chunks; print(len(period_chunks('dev', load_defaults())), 'chunks complete')" \
    >> "$LOG" 2>&1 || fail "the dev period is not covered by complete chunks"
  while (( $(awk '/MemAvailable/ {print int($2 / 1048576)}' /proc/meminfo) < 9 )); do
    log "waiting: less than 9 GB RAM available for the 7G build"; sleep 600
  done
  if [[ -e $STORE && ! -e $NOTES/dev2-before-rebuild.json ]]; then
    "$PY" -m backtest.validate_store snapshot "$STORE" "$NOTES/dev2-before-rebuild.json" >> "$LOG" 2>&1 \
      || fail "snapshot of the old store"
    log "old store counts saved"
  fi
  gate "store build"
  rm -f "$STORE" "$STORE.wal"
  log "old dev2.duckdb deleted; building"
  systemd-run --user --scope -q -p MemoryMax=7G -p CPUQuota=200% -- \
    env LAB_SPILL_DIR="$LAB/tmp-duckdb" LAB_SPILL_MAX=15GB LAB_DUCKDB_MEM=3GB \
    nice -n 10 "$PY" -m backtest.store dev --out "$STORE" >> "$LOG" 2>&1 || fail "store build"
  rm -rf "$LAB/tmp-duckdb"
  mark build
fi

if ! done_ validate; then
  "$PY" -m backtest.validate_store check "$STORE" "$NOTES/dev2-before-rebuild.json" \
    "$NOTES/dev2-validation-$(date +%F).md" >> "$LOG" 2>&1 || fail "store counts (see $NOTES)"
  mkdir -p /dev/shm/lab-chain-tests
  systemd-run --user --scope -q -p MemoryMax=3G -p CPUQuota=200% -- \
    env LAB_SPILL_DIR=/dev/shm/lab-chain-tests LAB_SPILL_MAX=1GB \
    "$PY" -m pytest -q -p no:cacheprovider backtest/tests paper/tests >> "$LOG" 2>&1 || fail "tests on the new store"
  rm -rf /dev/shm/lab-chain-tests
  mark validate
fi

run() {  # run_id, then backtest.tournament arguments
  local id=$1; shift
  mkdir -p "/dev/shm/$id"
  systemd-run --user --scope -q -p MemoryMax=4G -p CPUQuota=200% -- \
    env LAB_SPILL_DIR="/dev/shm/$id" LAB_SPILL_MAX=2GB nice -n 10 scripts/backtest-run.sh "$id" "$@" >> "$LOG" 2>&1 \
    || fail "run $id (see $LAB/backtests/$id/run.log)"
  rm -rf "/dev/shm/$id"
}

if ! done_ f7rev; then
  run f7rev-1033-1046 --families F7,N2 --segments pool_reversed --sizes 0.5,1,2 --delays 1,2 --workers 2 --batch f7rev_checks
  mark f7rev
fi

if ! done_ full; then
  run families-v2-1033-1046 --families F7,F6,S1,O7,M1,N3,N4,N2 --sizes 0.25,0.5,1,2 --delays 1,2,3 --q 0.5,0.9 \
    --only size=0.25:M1,N2_M1 --only d=3:F7/pool_reversed,N2_F7/pool_reversed \
    --only q=0.9:F7/pool_reversed,N2_F7/pool_reversed --workers 2 --batch families_v2
  mark full
fi
log "chain complete"
