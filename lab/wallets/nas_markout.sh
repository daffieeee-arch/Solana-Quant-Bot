#!/bin/bash
# Spec B (markouts) on the NAS (roadmap #167, week3-specs-markout-2026-10-12.md): markout curves on every
# signal of the dev runs, then the report. Resumable: a run whose parquet exists is skipped.
#
#   LAB_ENGINE=/home/chupa/Solana-project/data-old-faithful-one/lab/backtests/f7rev-1033-1046/engine \
#   LAB_SPILL_DIR=/lab/tmp/nas-markout LAB_SPILL_MAX=8GB bash lab/wallets/nas_markout.sh
#
# Tunables (env): RUNS (space-separated "<name>:<parts dir>"), MEMORY (DuckDB limit per run), BOOT.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
OUT=${LAB_WALLETS_OUT:-$DATA/wallets}
STORE=${LAB_STORE:-$DATA/store/dev2.duckdb}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
MK=$OUT/markout-v1
LOG=$MK/log
RUNS=${RUNS:-"f7rev:$DATA/backtests/f7rev-1033-1046/parts families-v2:$DATA/backtests/families-v2-1033-1046/parts m1x:$DATA/backtests/m1x-dev-1033-1046-453d44e/parts"}
BOOT=${BOOT:-10000}

case "${LAB_ENGINE:-}" in */backtest) export LAB_ENGINE=${LAB_ENGINE%/backtest} ;; esac
export PYTHONPATH=$REPO/lab LAB_STORE=$STORE LAB_WALLETS_OUT=$OUT LAB_WALLETS_MEMORY=${MEMORY:-6GB}
export LAB_SPILL_DIR=${LAB_SPILL_DIR:-/lab/tmp/nas-markout} LAB_SPILL_MAX=${LAB_SPILL_MAX:-8GB}
mkdir -p "$MK" "$LOG" "$LAB_SPILL_DIR"
log() { echo "$(date +%FT%T) $*" | tee -a "$LOG/nas_markout.log"; }
log "start repo=$REPO commit=$(git -C "$REPO" rev-parse --short HEAD) store=$STORE out=$MK engine=${LAB_ENGINE:-?}"

for spec in $RUNS; do
  name=${spec%%:*}; parts=${spec#*:}
  if [ -s "$MK/markout-$name.parquet" ]; then log "markout $name exists, skipped"; continue; fi
  if [ ! -d "$parts" ]; then log "markout $name: no parts at $parts, skipped"; continue; fi
  log "markout $name from $parts"
  if nice -n 10 "$PY" -m wallets.markout --parts "$parts" --run "$name" --out "$MK" > "$LOG/markout-$name.log" 2>&1; then
    log "markout $name ok: $(tail -1 "$LOG/markout-$name.log")"
  else
    log "markout $name FAILED (see $LOG/markout-$name.log)"; exit 1
  fi
done

# The report: all runs together; the exit-rule cost from the F7 tournament's positions (0.5 SOL, q = 0.5).
F7PARTS=$DATA/backtests/f7rev-1033-1046/parts
log "report (boot $BOOT)"
nice -n 10 "$PY" -m wallets.markout_report --markout "$MK" --parts "$F7PARTS" --boot "$BOOT" --out "$MK/markout.md" > "$LOG/report.log" 2>&1
log "done: $MK/markout.md"
