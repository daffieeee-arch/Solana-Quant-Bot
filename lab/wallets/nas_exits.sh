#!/bin/bash
# Spec E §2 (exit grid of F7 pool_reversed, roadmap #167, week3-specs-exits-2026-10-15.md) on the NAS: the report over
# the exits run's positions (Lab Backtest's engine run, parts under $PARTS), then DSR / PBO over every trial.
#
#   PARTS=$DATA/backtests/exits-dev-1033-1046-<engine>/parts bash lab/wallets/nas_exits.sh
#
# Tunables (env): PARTS (required), OUT_DIR (default $OUT/exits-v1), FAMILIES (F7; spec F long grid: F7,M1,M1E,M1P), BASELINE (tp4_T600), BOOT (10000), MEMORY (DuckDB),
# CFG (week2-defaults.yaml), LAB_ENGINE (the run's engine dir: its costs.settle_sql is used when set).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
OUT=${LAB_WALLETS_OUT:-$DATA/wallets}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
PARTS=${PARTS:?set PARTS to the parts directory of the exits run}
EX=${OUT_DIR:-$OUT/exits-v1}
LOG=$EX/log
CFG=${CFG:-$DATA/research/week2-defaults.yaml}
case "${LAB_ENGINE:-}" in */backtest) export LAB_ENGINE=${LAB_ENGINE%/backtest} ;; esac
export PYTHONPATH=$REPO/lab LAB_WALLETS_OUT=$OUT LAB_WALLETS_MEMORY=${MEMORY:-6GB} LAB_DEFAULTS=$CFG
export LAB_SPILL_DIR=${LAB_SPILL_DIR:-/lab/tmp/nas-exits} LAB_SPILL_MAX=${LAB_SPILL_MAX:-8GB}
mkdir -p "$EX" "$LOG" "$LAB_SPILL_DIR"
log() { echo "$(date +%FT%T) $*" | tee -a "$LOG/nas_exits.log"; }
log "start repo=$REPO commit=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || cat "$REPO/COMMIT" 2>/dev/null || echo ?) parts=$PARTS out=$EX engine=${LAB_ENGINE:-repo} memory=$LAB_WALLETS_MEMORY"
[ -d "$PARTS" ] || { log "no parts at $PARTS"; exit 1; }
log "report (boot ${BOOT:-10000})"
nice -n 10 "$PY" -u -m wallets.exits_report --parts "$PARTS" --families "${FAMILIES:-F7}" --baseline "${BASELINE:-tp4_T600}" --cfg "$CFG" --boot "${BOOT:-10000}" --out "$EX/exits.md" > "$LOG/report.log" 2>&1 || { log "report FAILED (see $LOG/report.log)"; exit 1; }
log "report ok: $(tail -1 "$LOG/report.log" | cut -c1-200)"
# DSR / PBO over every trial of the grid (variant x d x farm; the 9 existing variants included): the N of spec E §1
log "overfit (all trials)"
nice -n 10 "$PY" -u -m wallets.overfit --parts "$PARTS" --family F7 --where "size_sol = 0.5 AND q = 0.5 AND segment = 'pool_reversed'" --key family,variant,d,farm --blocks 16 --cfg "$CFG" --out "$EX/overfit.md" > "$LOG/overfit.log" 2>&1 || { log "overfit FAILED (see $LOG/overfit.log)"; exit 1; }
log "overfit ok"
(cd "$EX" && sha256sum exits.md exits.json overfit.md > SHA256SUMS)
log "done: $EX/exits.md"
