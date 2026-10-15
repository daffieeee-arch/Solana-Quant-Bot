#!/bin/bash
# Spec E §3 (late bloomers vs drains, roadmap #167, week3-specs-exits-2026-10-15.md) on the NAS: point-in-time features
# of every F7 pool_reversed trigger of the f7rev run against the outcomes of spec B (markout-f7rev.parquet) and
# research D (survival.parquet), per-feature AUC / quintiles with a day bootstrap.
#
#   MEMORY=12GB LAB_SPILL_DIR=/lab/tmp/nas-latebloom LAB_SPILL_MAX=50GB bash lab/wallets/nas_latebloom.sh
#
# Inputs (env): PARTS (f7rev parts), MARKOUT (markout-f7rev.parquet of wallets/markout-v1), SURVIVAL (survival.parquet of
# wallets/survival-v1); OUT_DIR (default $OUT/latebloom-v1); BUCKETS (pool hash buckets, default 8); BOOT (default 2000);
# LATEBLOOM_ARGS (e.g. "--from-slot A --to-slot B --lookback-slots N" for a smoke).
# One-day smoke (epoch 1040 day 1) on the VPS, under the 3G scope:
#   LAB_WALLETS_MEMORY=1500MB BUCKETS=4 BOOT=200 OUT_DIR=<dir> MARKOUT=<markout-v1-smoke-day/markout-f7rev.parquet> \
#   LATEBLOOM_ARGS="--from-slot 449280000 --to-slot 449568000 --lookback-slots 432000" bash lab/wallets/nas_latebloom.sh
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
OUT=${LAB_WALLETS_OUT:-$DATA/wallets}
STORE=${LAB_STORE:-$DATA/store/dev2.duckdb}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
PARTS=${PARTS:-$DATA/backtests/f7rev-1033-1046/parts}
MARKOUT=${MARKOUT:-$OUT/markout-v1/markout-f7rev.parquet}
SURVIVAL=${SURVIVAL:-$OUT/survival-v1/survival.parquet}
LB=${OUT_DIR:-$OUT/latebloom-v1}
LOG=$LB/log

export PYTHONPATH=$REPO/lab LAB_STORE=$STORE LAB_WALLETS_OUT=$OUT LAB_WALLETS_MEMORY=${MEMORY:-${LAB_WALLETS_MEMORY:-6GB}} LAB_LATEBLOOM_BUCKETS=${BUCKETS:-8}
export LAB_SPILL_DIR=${LAB_SPILL_DIR:-/lab/tmp/nas-latebloom} LAB_SPILL_MAX=${LAB_SPILL_MAX:-8GB}
mkdir -p "$LB" "$LOG" "$LAB_SPILL_DIR"
log() { echo "$(date +%FT%T) $*" | tee -a "$LOG/nas_latebloom.log"; }
log "start repo=$REPO commit=$(git -C "$REPO" rev-parse --short HEAD) store=$STORE out=$LB memory=$LAB_WALLETS_MEMORY buckets=$LAB_LATEBLOOM_BUCKETS markout=$MARKOUT survival=$SURVIVAL"
for f in "$MARKOUT" "$SURVIVAL"; do [ -s "$f" ] || { log "missing input $f"; exit 1; }; done
# shellcheck disable=SC2086
if nice -n 10 "$PY" -u -m wallets.latebloom --parts "$PARTS" --markout "$MARKOUT" --survival "$SURVIVAL" --out "$LB" --boot "${BOOT:-2000}" ${LATEBLOOM_ARGS:-} > "$LOG/latebloom.log" 2>&1; then
  log "ok: $(tail -1 "$LOG/latebloom.log")"
else
  log "FAILED (see $LOG/latebloom.log)"; exit 1
fi
(cd "$LB" && sha256sum latebloom.parquet latebloom.md run-latebloom.json > SHA256SUMS)
log "done: $LB/latebloom.md"
