#!/bin/bash
# Revival step 1 on the NAS (spec research/spec-revival-2026-10-19.md v0.1a, roadmap #167): the pool-hour aggregate of every
# SOL-quoted PumpSwap pool over the whole dev store (one scan of the 425 M pool legs, per key-hash bucket of the pool).
#
#   MEMORY=12GB BUCKETS=16 THREADS=6 LAB_SPILL_DIR=/lab/tmp/nas-revival LAB_SPILL_MAX=100GB bash lab/wallets/nas_revival.sh
#
# Step 2 + 3 (STEP=markout, short): `detect` on the aggregate (AGG_DIR, default $REVIVAL_DIR: the rolling stats, then one scan
# of the store's pool legs restricted to the candidate hours for the point-in-time t of every cell), then `wallets.markout`
# on the signals (the pool legs of the signal pools around every signal; the big scan is pool_at over the store, bucketed),
# into $REVIVAL_DIR/{detect,markout}. The aggregate of an earlier commit stays valid (its code is unchanged since 370ecb2):
#   STEP=markout AGG_DIR=$DATA/wallets/revival-v1-370ecb2 REVIVAL_DIR=$DATA/wallets/revival-v2-<commit7> \
#     LAB_ENGINE=$DATA/backtests/f7rev-1033-1046/engine MEMORY=12GB BUCKETS=8 THREADS=6 \
#     LAB_SPILL_DIR=/lab/tmp/nas-revival LAB_SPILL_MAX=100GB bash lab/wallets/nas_revival.sh
#
# Tunables (env): MEMORY (DuckDB limit), BUCKETS (key-hash buckets; the union is the unbucketed result), THREADS,
# REVIVAL_DIR (output, default $OUT/revival-v1), REVIVAL_ARGS (e.g. "--from-slot A --to-slot B" for a smoke).
# Output: $REVIVAL_DIR/pool_hour.parquet, pools.parquet, run-aggregate.json; progress per bucket in $REVIVAL_DIR/log/aggregate.log.
# Resumable: an existing pool_hour.parquet is not rebuilt, so a run on a new commit needs a fresh REVIVAL_DIR.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
OUT=${LAB_WALLETS_OUT:-$DATA/wallets}
STORE=${LAB_STORE:-$DATA/store/dev2.duckdb}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
RV=${REVIVAL_DIR:-$OUT/revival-v1}
AGG=${AGG_DIR:-$RV}
LOG=$RV/log

export PYTHONPATH=$REPO/lab LAB_STORE=$STORE LAB_WALLETS_OUT=$OUT LAB_WALLETS_MEMORY=${MEMORY:-6GB} LAB_WALLETS_THREADS=${THREADS:-4}
export LAB_SPILL_DIR=${LAB_SPILL_DIR:-/lab/tmp/nas-revival} LAB_SPILL_MAX=${LAB_SPILL_MAX:-20GB}
mkdir -p "$RV" "$LOG" "$LAB_SPILL_DIR"
log() { echo "$(date +%FT%T) $*" | tee -a "$LOG/nas_revival.log"; }
log "start repo=$REPO commit=$(git -C "$REPO" rev-parse --short HEAD) store=$STORE out=$RV memory=$LAB_WALLETS_MEMORY threads=$LAB_WALLETS_THREADS buckets=${BUCKETS:-8} spill=$LAB_SPILL_DIR/$LAB_SPILL_MAX"

STEP=${STEP:-aggregate}
if [ "$STEP" = markout ]; then
  case "${LAB_ENGINE:-}" in */backtest) export LAB_ENGINE=${LAB_ENGINE%/backtest} ;; esac
  [ -s "$AGG/pool_hour.parquet" ] || { log "markout: no aggregate at $AGG/pool_hour.parquet"; exit 1; }
  log "detect on aggregate $AGG (sha256 $(sha256sum "$AGG/pool_hour.parquet" | cut -c1-16)) into $RV/detect"
  if nice -n 10 "$PY" -u -m wallets.revival detect --agg "$AGG" --out "$RV/detect" --store "$STORE" > "$LOG/detect.log" 2>&1; then
    log "detect ok: $(tail -1 "$LOG/detect.log")"
  else
    log "detect FAILED (see $LOG/detect.log)"; exit 1
  fi
  if [ -s "$RV/markout/markout-revival.parquet" ]; then log "markout exists, skipped"; exit 0; fi
  log "markout on $RV/detect (engine ${LAB_ENGINE:-?})"
  if nice -n 10 "$PY" -u -m wallets.markout --parts "$RV/detect" --run revival --out "$RV/markout" > "$LOG/markout.log" 2>&1; then
    log "markout ok: $(tail -1 "$LOG/markout.log")"
  else
    log "markout FAILED (see $LOG/markout.log)"; exit 1
  fi
  log "done: $RV/markout/markout-revival.parquet (the report is wallets.revival report, after this job)"; exit 0
fi
if [ -s "$RV/pool_hour.parquet" ]; then log "aggregate exists, skipped"; exit 0; fi
# shellcheck disable=SC2086
if nice -n 10 "$PY" -u -m wallets.revival aggregate --out "$RV" --buckets "${BUCKETS:-8}" ${REVIVAL_ARGS:-} > "$LOG/aggregate.log" 2>&1; then
  log "aggregate ok: $(tail -1 "$LOG/aggregate.log")"
else
  log "aggregate FAILED (see $LOG/aggregate.log)"; exit 1
fi
log "done: $RV/pool_hour.parquet"
