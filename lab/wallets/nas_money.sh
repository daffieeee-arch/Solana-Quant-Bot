#!/bin/bash
# Spec A (follow the money) on the NAS (roadmap #167, week3-specs-money-2026-10-12.md): P&L per wallet x day
# over the development period, then the report (labels, groups, placebo, Holm, zero-sum).
#
#   LAB_SPILL_DIR=/lab/tmp/nas-money LAB_SPILL_MAX=16GB MEMORY=12GB REPORT_MEMORY=6GB bash lab/wallets/nas_money.sh
#   MONEY_OUT=$DATA/wallets/money-v2-<sha7> ... bash lab/wallets/nas_money.sh      (a rerun into a new directory; money-v1 stays)
#   REPORT_ARGS=--views ... bash lab/wallets/nas_money.sh                           (the report with the wallet-day rows as views:
#        a few GB instead of ~25 GB of temp tables; money-v2's report hit the 16 GB spill cap on 2026-10-22, see money_report.load)
#
# Tunables (env): FROM/TO slots (development only), BUCKETS (wallet hash buckets, bounds the window working set),
# MEMORY (DuckDB limit), REPORT_MEMORY, REPORT_ARGS (extra money_report flags), BOOT, PLACEBO.
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
MO=${MONEY_OUT:-$OUT/money-v1}   # MONEY_OUT: another output directory (the fee-fix rerun of 2026-10-21 writes money-v2-<sha7>, never over money-v1)
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

REPORT_ARGS=${REPORT_ARGS:-}
log "report (boot $BOOT, placebo $PLACEBO, memory ${REPORT_MEMORY:-6GB}, args '$REPORT_ARGS')"
clusters=""
[ -d "$CL" ] && clusters="--clusters $CL"
F7PARTS=$DATA/backtests/f7rev-1033-1046/parts
export LAB_WALLETS_MEMORY=${REPORT_MEMORY:-6GB}   # the report keeps the wallet-day rows in numpy next to DuckDB: a lower limit than the build
# shellcheck disable=SC2086  # REPORT_ARGS is a flag list
nice -n 10 "$PY" -m wallets.money_report --money "$MO" $clusters --parts "$F7PARTS" --boot "$BOOT" --placebo "$PLACEBO" $REPORT_ARGS --out "$MO/money.md" > "$LOG/report.log" 2>&1
log "done: $MO/money.md"
