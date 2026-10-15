#!/bin/bash
# Spec F section 1 (runners, roadmap #167, week3-specs-runners-2026-10-15.md) on the NAS: entries and marks per epoch
# of dev (1033-1046) from the store, then the report (AUC with a 10,000-draw day bootstrap, quintiles).
#
#   LAB_SPILL_DIR=/lab/tmp/nas-runners LAB_SPILL_MAX=50GB bash lab/wallets/nas_runners.sh
#
# Tunables (env): EPOCHS (default 1033..1046), MEMORY (DuckDB, default 12GB), THREADS (4), BOOT (10000), OUT_DIR
# (default $OUT/runners-v1), F7_PARTS (f7rev-1033-1046/parts), M1X_TRIGGERS (the m1x triggers parquet of transfers-v1),
# CFG (week2-defaults.yaml: the base scenario's network cost).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
OUT=${LAB_WALLETS_OUT:-$DATA/wallets}
STORE=${LAB_STORE:-$DATA/store/dev2.duckdb}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
RV=${OUT_DIR:-$OUT/runners-v1}
LOG=$RV/log
CFG=${CFG:-$DATA/research/week2-defaults.yaml}
F7_PARTS=${F7_PARTS:-$DATA/backtests/f7rev-1033-1046/parts}
M1X=${M1X_TRIGGERS:-$DATA/wallets/transfers-v1/triggers-m1x-m1x-dev-1033-1046-5090d14.parquet}
EPOCHS=${EPOCHS:-$(seq -s, 1033 1046)}

export PYTHONPATH=$REPO/lab LAB_STORE=$STORE LAB_WALLETS_OUT=$OUT LAB_WALLETS_MEMORY=${MEMORY:-12GB} LAB_WALLETS_THREADS=${THREADS:-4} LAB_DEFAULTS=$CFG
export LAB_SPILL_DIR=${LAB_SPILL_DIR:-/lab/tmp/nas-runners} LAB_SPILL_MAX=${LAB_SPILL_MAX:-50GB}
mkdir -p "$RV" "$LOG" "$LAB_SPILL_DIR"
log() { echo "$(date +%FT%T) $*" | tee -a "$LOG/nas_runners.log"; }
log "start repo=$REPO commit=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || cat "$REPO/COMMIT" 2>/dev/null || echo ?) store=$STORE epochs=$EPOCHS out=$RV memory=$LAB_WALLETS_MEMORY"
[ -d "$F7_PARTS" ] || { log "no F7 parts at $F7_PARTS"; exit 1; }
[ -s "$M1X" ] || { log "no m1x triggers at $M1X"; exit 1; }

IFS=',' read -ra EP <<< "$EPOCHS"
for e in "${EP[@]}"; do
  lo=$((e * 432000)); hi=$(((e + 1) * 432000))
  if [ -s "$RV/entries-$lo.parquet" ]; then log "epoch $e exists, skipped"; continue; fi
  log "epoch $e [$lo, $hi)"
  if nice -n 10 "$PY" -u -m wallets.runners --from-slot "$lo" --to-slot "$hi" --f7-parts "$F7_PARTS" --m1x-triggers "$M1X" --cfg "$CFG" --out "$RV" > "$LOG/runners-$e.log" 2>&1; then
    log "epoch $e ok: $(tail -1 "$LOG/runners-$e.log" | cut -c1-300)"
  else
    log "epoch $e FAILED (see $LOG/runners-$e.log)"; exit 1
  fi
done
log "report (boot ${BOOT:-10000})"
nice -n 10 "$PY" -u -m wallets.runners_report --entries "$RV" --boot "${BOOT:-10000}" --memory "$LAB_WALLETS_MEMORY" --out "$RV/runners.md" > "$LOG/report.log" 2>&1 || { log "report FAILED (see $LOG/report.log)"; exit 1; }
log "report ok: $(tail -1 "$LOG/report.log" | cut -c1-200)"
(cd "$RV" && sha256sum runners.md runners.json entries-*.parquet run-runners-*.json > SHA256SUMS)
log "done: $RV/runners.md"
