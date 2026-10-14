#!/bin/bash
# MEV phase 0 on the NAS (roadmap #206, week3-specs-mev0-2026-10-12.md v3): arbs, attempts, gaps, signers and harm
# of the dev epochs from the tx_ctx re-extraction and the store, then the report. Measurement only.
#
#   LAB_SPILL_DIR=/lab/tmp/nas-mev0 LAB_SPILL_MAX=20GB bash lab/wallets/nas_mev0.sh
#
# Tunables (env): EPOCHS (default 1040,1045), CHUNKS (the tx_ctx chunks), MEMORY (DuckDB limit), THREADS.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
OUT=${LAB_WALLETS_OUT:-$DATA/wallets}
STORE=${LAB_STORE:-$DATA/store/dev2.duckdb}
CHUNKS=${CHUNKS:-$DATA/events/txctx-v1/chunks}
EPOCHS=${EPOCHS:-1040,1045}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
MV=$OUT/mev0-v1
LOG=$MV/log

export PYTHONPATH=$REPO/lab LAB_STORE=$STORE LAB_WALLETS_OUT=$OUT LAB_WALLETS_MEMORY=${MEMORY:-8GB} LAB_WALLETS_THREADS=${THREADS:-4}
export LAB_SPILL_DIR=${LAB_SPILL_DIR:-/lab/tmp/nas-mev0} LAB_SPILL_MAX=${LAB_SPILL_MAX:-20GB}
mkdir -p "$MV" "$LOG" "$LAB_SPILL_DIR"
log() { echo "$(date +%FT%T) $*" | tee -a "$LOG/nas_mev0.log"; }
log "start repo=$REPO commit=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || cat "$REPO/COMMIT" 2>/dev/null || echo ?) store=$STORE chunks=$CHUNKS epochs=$EPOCHS out=$MV"

if nice -n 10 "$PY" -m wallets.mev0 --chunks "$CHUNKS" --epochs "$EPOCHS" --out "$MV" > "$LOG/mev0.log" 2>&1; then
  log "mev0 ok: $(tail -2 "$LOG/mev0.log" | tr '\n' ' ')"
else
  log "mev0 FAILED (see $LOG/mev0.log)"; exit 1
fi
REPORT=$MV/mev0-report.md
if "$PY" -m wallets.mev0_report --dir "$MV" --epochs "$EPOCHS" --out "$REPORT" > "$LOG/report.log" 2>&1; then
  log "report ok: $REPORT ($(sha256sum "$REPORT" | cut -c1-16))"
else
  log "report FAILED (see $LOG/report.log)"; exit 1
fi
(cd "$MV" && sha256sum ./*.parquet ./*.json ./*.md > SHA256SUMS)
log "done"
