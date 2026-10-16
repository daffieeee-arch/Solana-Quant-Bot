#!/bin/bash
# E22 addendum (spec E §3, roadmap #167): holder concentration and transfer-fed holdings at t, and transfer-fed selling
# in the first 30 / 60 s, for the F7 pool_reversed triggers of the transfers epochs (events/transfers-v1, 1040 and
# 1045), against the latebloom outcomes (spec B markout, research D survival). One run per epoch, like nas_transfers.sh.
#
#   LAB_SPILL_DIR=/lab/tmp/nas-latebloom-transfers LAB_SPILL_MAX=50GB bash lab/wallets/nas_latebloom_transfers.sh
#
# Tunables (env): EPOCHS (default 1040,1045), CHUNKS (the transfers chunks root), MEMORY, THREADS, PARTS (f7rev parts),
# MARKOUT, SURVIVAL, OUT_DIR (default $OUT/latebloom-v1/transfers), BOOT (default 2000).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
OUT=${LAB_WALLETS_OUT:-$DATA/wallets}
STORE=${LAB_STORE:-$DATA/store/dev2.duckdb}
CHUNKS=${CHUNKS:-$DATA/events/transfers-v1/chunks}
EPOCHS=${EPOCHS:-1040,1045}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
PARTS=${PARTS:-$DATA/backtests/f7rev-1033-1046/parts}
MARKOUT=${MARKOUT:-$OUT/markout-v1/markout-f7rev.parquet}
SURVIVAL=${SURVIVAL:-$OUT/survival-v1/survival.parquet}
LT=${OUT_DIR:-$OUT/latebloom-v1/transfers}
LOG=$LT/log

export PYTHONPATH=$REPO/lab LAB_STORE=$STORE LAB_WALLETS_OUT=$OUT LAB_WALLETS_MEMORY=${MEMORY:-12GB} LAB_WALLETS_THREADS=${THREADS:-4}
export LAB_SPILL_DIR=${LAB_SPILL_DIR:-/lab/tmp/nas-latebloom-transfers} LAB_SPILL_MAX=${LAB_SPILL_MAX:-50GB}
mkdir -p "$LT" "$LOG" "$LAB_SPILL_DIR"
log() { echo "$(date +%FT%T) $*" | tee -a "$LOG/nas_latebloom_transfers.log"; }
log "start repo=$REPO commit=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || cat "$REPO/COMMIT" 2>/dev/null || echo ?) store=$STORE chunks=$CHUNKS epochs=$EPOCHS out=$LT"
for f in "$MARKOUT" "$SURVIVAL"; do [ -s "$f" ] || { log "missing input $f"; exit 1; }; done

IFS=, read -ra EPS <<< "$EPOCHS"
for E in "${EPS[@]}"; do
  LO=$((E * 432000)); HI=$(((E + 1) * 432000))
  D=$LT/epoch-$E
  mkdir -p "$D"
  log "epoch $E [$LO, $HI)"
  if nice -n 10 "$PY" -u -m wallets.latebloom_transfers --chunks "$CHUNKS" --from-slot "$LO" --to-slot "$HI" --parts "$PARTS" \
       --markout "$MARKOUT" --survival "$SURVIVAL" --out "$D" --boot "${BOOT:-2000}" > "$LOG/latebloom-transfers-$E.log" 2>&1; then
    log "epoch $E ok: $(tail -1 "$LOG/latebloom-transfers-$E.log")"
  else
    log "epoch $E FAILED (see $LOG/latebloom-transfers-$E.log)"; exit 1
  fi
done
(cd "$LT" && find . -path ./log -prune -o -type f \( -name '*.parquet' -o -name '*.md' -o -name '*.json' \) -print | sort | xargs sha256sum > SHA256SUMS)
log "done: $LT"
