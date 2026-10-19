#!/bin/bash
# Spec B (markouts) on the NAS (roadmap #167, week3-specs-markout-2026-10-12.md): markout curves on every
# signal of the dev runs, then the report. Resumable: a run whose parquet exists is skipped.
#
#   LAB_ENGINE=/home/chupa/Solana-project/data-old-faithful-one/lab/backtests/f7rev-1033-1046/engine \
#   LAB_SPILL_DIR=/lab/tmp/nas-markout LAB_SPILL_MAX=8GB bash lab/wallets/nas_markout.sh
#
# The big runs (families-v2 has 9x the f7rev signals: one unbucketed pool_at spilled 93 GiB at MEMORY 12GB) on the
# 24 GB runner, with the venue-state match in 32 key-hash buckets and 200 GB of spill:
#   MEMORY=12GB BUCKETS=32 LAB_SPILL_MAX=200GB LAB_SPILL_DIR=/lab/tmp/nas-markout LAB_ENGINE=... bash lab/wallets/nas_markout.sh
#
# Tunables (env): RUNS (space-separated "<name>:<parts dir>"), MEMORY (DuckDB limit per run), BUCKETS (key-hash
# buckets of the venue-state match, default 1 = unbucketed; the union over the buckets is the unbucketed result),
# BOOT, WORKERS (LAB_REPORT_WORKERS: processes for the report's bootstrap draws, default 1; the lab runner has 6; the CIs are
# bit-identical to the serial loop), MARKOUT_DIR (output, default $OUT/markout-v1), MARKOUT_ARGS (e.g. "--from-slot A --to-slot B" for a smoke).
# Progress: every phase writes a timed line to $MARKOUT_DIR/log/markout-<run>.log (signals, targets, pool_at,
# curve_at, matched, round trips per 500k rows, parquet), and run-markout-<run>.json has phases_seconds.
#
# One-day smoke of f7rev (epoch 1040, the first ~288k slots ≈ 21.6 h at 270 ms), with timing per phase:
#   RUNS="f7rev:$DATA/backtests/f7rev-1033-1046/parts" MARKOUT_DIR=$DATA/wallets/markout-v1-smoke-day \
#   MARKOUT_ARGS="--from-slot 449280000 --to-slot 449568000" bash lab/wallets/nas_markout.sh
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
OUT=${LAB_WALLETS_OUT:-$DATA/wallets}
STORE=${LAB_STORE:-$DATA/store/dev2.duckdb}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
MK=${MARKOUT_DIR:-$OUT/markout-v1}
LOG=$MK/log
RUNS=${RUNS:-"f7rev:$DATA/backtests/f7rev-1033-1046/parts families-v2:$DATA/backtests/families-v2-1033-1046/parts m1x:$DATA/backtests/m1x-dev-1033-1046-5090d14/parts"}
BOOT=${BOOT:-10000}

case "${LAB_ENGINE:-}" in */backtest) export LAB_ENGINE=${LAB_ENGINE%/backtest} ;; esac
export PYTHONPATH=$REPO/lab LAB_STORE=$STORE LAB_WALLETS_OUT=$OUT LAB_WALLETS_MEMORY=${MEMORY:-6GB} LAB_MARKOUT_BUCKETS=${BUCKETS:-1}
export LAB_SPILL_DIR=${LAB_SPILL_DIR:-/lab/tmp/nas-markout} LAB_SPILL_MAX=${LAB_SPILL_MAX:-8GB} LAB_REPORT_WORKERS=${WORKERS:-1}
mkdir -p "$MK" "$LOG" "$LAB_SPILL_DIR"
log() { echo "$(date +%FT%T) $*" | tee -a "$LOG/nas_markout.log"; }
log "start repo=$REPO commit=$(git -C "$REPO" rev-parse --short HEAD) store=$STORE out=$MK engine=${LAB_ENGINE:-?} memory=$LAB_WALLETS_MEMORY buckets=$LAB_MARKOUT_BUCKETS spill=$LAB_SPILL_DIR/$LAB_SPILL_MAX"

for spec in $RUNS; do
  name=${spec%%:*}; parts=${spec#*:}
  if [ -s "$MK/markout-$name.parquet" ]; then log "markout $name exists, skipped"; continue; fi
  if [ ! -d "$parts" ]; then log "markout $name: no parts at $parts, skipped"; continue; fi
  log "markout $name from $parts"
  # shellcheck disable=SC2086
  if nice -n 10 "$PY" -u -m wallets.markout --parts "$parts" --run "$name" --out "$MK" ${MARKOUT_ARGS:-} > "$LOG/markout-$name.log" 2>&1; then
    log "markout $name ok: $(tail -1 "$LOG/markout-$name.log")"
  else
    log "markout $name FAILED (see $LOG/markout-$name.log)"; exit 1
  fi
done

# The report: all runs together; the exit-rule cost from the F7 tournament's positions (0.5 SOL, q = 0.5).
F7PARTS=$DATA/backtests/f7rev-1033-1046/parts
log "report (boot $BOOT, workers $LAB_REPORT_WORKERS)"
nice -n 10 "$PY" -m wallets.markout_report --markout "$MK" --parts "$F7PARTS" --boot "$BOOT" --out "$MK/markout.md" > "$LOG/report.log" 2>&1
log "done: $MK/markout.md"
