#!/bin/bash
# Research D (exploratory) on the NAS: time from every F7 pool_reversed trigger of the f7rev dev run to the LP drain
# of its pool (Kaplan-Meier, lifelines), the K3 window (drains within 600 slots) and the inactivity exit.
#
#   LAB_SPILL_DIR=/lab/tmp/nas-survival LAB_SPILL_MAX=50GB bash lab/wallets/nas_survival.sh
#
# Tunables (env): PARTS (default the f7rev parts), MEMORY (DuckDB limit, default 8GB), SURVIVAL_DIR (output, default
# $OUT/survival-v1), SURVIVAL_ARGS (e.g. "--from-slot A --to-slot B --observe-slots 80000" for a smoke).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
OUT=${LAB_WALLETS_OUT:-$DATA/wallets}
STORE=${LAB_STORE:-$DATA/store/dev2.duckdb}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
PARTS=${PARTS:-$DATA/backtests/f7rev-1033-1046/parts}
SV=${SURVIVAL_DIR:-$OUT/survival-v1}
LOG=$SV/log

export PYTHONPATH=$REPO/lab LAB_STORE=$STORE LAB_WALLETS_OUT=$OUT LAB_WALLETS_MEMORY=${MEMORY:-8GB}
export LAB_SPILL_DIR=${LAB_SPILL_DIR:-/lab/tmp/nas-survival} LAB_SPILL_MAX=${LAB_SPILL_MAX:-50GB}
mkdir -p "$SV" "$LOG" "$LAB_SPILL_DIR"
log() { echo "$(date +%FT%T) $*" | tee -a "$LOG/nas_survival.log"; }
log "start repo=$REPO commit=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || cat "$REPO/COMMIT" 2>/dev/null || echo ?) store=$STORE parts=$PARTS out=$SV"
# shellcheck disable=SC2086
if nice -n 10 "$PY" -u -m wallets.survival --parts "$PARTS" --out "$SV" ${SURVIVAL_ARGS:-} > "$LOG/survival.log" 2>&1; then
  log "ok: $(tail -1 "$LOG/survival.log")"
else
  log "FAILED (see $LOG/survival.log)"; exit 1
fi
(cd "$SV" && sha256sum survival.parquet survival.md run-survival.json > SHA256SUMS)
log "done: $SV/survival.md"
