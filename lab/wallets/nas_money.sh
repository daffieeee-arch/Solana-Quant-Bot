#!/bin/bash
# Spec A (follow the money) on the NAS (roadmap #167, week3-specs-money-2026-10-12.md): P&L per wallet x day
# over the development period, then the report (labels, groups, placebo, Holm, zero-sum).
#
#   LAB_SPILL_DIR=/lab/tmp/nas-money LAB_SPILL_MAX=16GB MEMORY=12GB REPORT_MEMORY=6GB bash lab/wallets/nas_money.sh
#
# Tunables (env): FROM/TO slots (development only), BUCKETS (wallet hash buckets, bounds the window working set),
# MEMORY (DuckDB limit), BOOT, PLACEBO.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
OUT=${LAB_WALLETS_OUT:-$DATA/wallets}
STORE=${LAB_STORE:-$DATA/store/dev2.duckdb}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
FROM=${FROM:-446256000}
TO=${TO:-452304000}
BUCKETS=${BUCKETS:-8}
BOOT=${BOOT:-10000}
PLACEBO=${PLACEBO:-100}
MO=$OUT/money-v1
CL=$OUT/clusters-v1
LOG=$MO/log

export PYTHONPATH=$REPO/lab LAB_STORE=$STORE LAB_WALLETS_OUT=$OUT LAB_WALLETS_MEMORY=${MEMORY:-12GB}
export LAB_SPILL_DIR=${LAB_SPILL_DIR:-/lab/tmp/nas-money} LAB_SPILL_MAX=${LAB_SPILL_MAX:-16GB}
mkdir -p "$MO" "$LOG" "$LAB_SPILL_DIR"
log() { echo "$(date +%FT%T) $*" | tee -a "$LOG/nas_money.log"; }
log "start repo=$REPO commit=$(git -C "$REPO" rev-parse --short HEAD) store=$STORE out=$MO"

if [ -s "$MO/run-money.json" ]; then
  log "money exists, skipped"
else
  log "money $FROM-$TO, $BUCKETS buckets"
  if nice -n 10 "$PY" -m wallets.money --from-slot "$FROM" --to-slot "$TO" --buckets "$BUCKETS" --out "$MO" > "$LOG/money.log" 2>&1; then
    log "money ok: $(tail -1 "$LOG/money.log")"
  else
    log "money FAILED (see $LOG/money.log)"; exit 1
  fi
fi

log "report (boot $BOOT, placebo $PLACEBO)"
clusters=""
[ -d "$CL" ] && clusters="--clusters $CL"
F7PARTS=$DATA/backtests/f7rev-1033-1046/parts
export LAB_WALLETS_MEMORY=${REPORT_MEMORY:-6GB}   # the report keeps the wallet-day rows in numpy next to DuckDB: a lower limit than the build
nice -n 10 "$PY" -m wallets.money_report --money "$MO" $clusters --parts "$F7PARTS" --boot "$BOOT" --placebo "$PLACEBO" --out "$MO/money.md" > "$LOG/report.log" 2>&1
log "done: $MO/money.md"
