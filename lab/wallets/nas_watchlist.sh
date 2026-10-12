#!/bin/bash
# Watchlist measurement over the development period on the NAS (triton-edge §6; owner request 2026-10-12).
# One pool-table scan per UTC day (plus 6 h warm-up), the shared state machine, stats per day, then the Markdown.
#
#   LAB_SPILL_DIR=/lab/tmp/watchlist LAB_SPILL_MAX=8GB LAB_WATCHLIST_MEMORY=1800MB bash lab/wallets/nas_watchlist.sh
#
# First one scan for the per-pool history (creation, first >= 50 / 150 SOL depth) that the universe-with-lag
# counts need; then the days. Resumable: a day with its stats file is skipped. TRIGGERS: a tournament run directory with parts/ (F7 pool
# triggers for the coverage check); WALLETS: optional CSV with a `wallet` column.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
OUT=${LAB_WATCHLIST_OUT:-$DATA/wallets/watchlist-v1}
STORE=${LAB_STORE:-$DATA/store/dev2.duckdb}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
FROM=${FROM:-446256000}
TO=${TO:-452304000}
TRIGGERS=${TRIGGERS:-$DATA/backtests/f7rev-1033-1046}
WALLETS=${WALLETS:-}
export PYTHONPATH=$REPO/lab LAB_STORE=$STORE LAB_SPILL_DIR=${LAB_SPILL_DIR:-/lab/tmp/watchlist} LAB_SPILL_MAX=${LAB_SPILL_MAX:-8GB}
mkdir -p "$OUT" "$LAB_SPILL_DIR"
log() { echo "$(date +%FT%T) $*" | tee -a "$OUT/run.log"; }
log "start commit=$(git -C "$REPO" rev-parse --short HEAD) store=$STORE out=$OUT triggers=$TRIGGERS"

days=$($PY -I - <<PYEOF
import duckdb
con = duckdb.connect("$STORE", read_only=True)
con.execute("SET memory_limit='500MB'; SET threads TO 2")
rows = con.execute("""SELECT strftime(make_timestamp(block_time * 1000000), '%Y-%m-%d') AS d, min(slot) FROM blocks
                      WHERE slot >= $FROM AND slot < $TO GROUP BY d ORDER BY d""").fetchall()
for (d, lo), nxt in zip(rows, rows[1:] + [(None, $TO)]):
    print(d, lo, nxt[1])
PYEOF
)
HISTORY=$OUT/pool-history.parquet
if [ ! -f "$HISTORY" ]; then
  nice -n 10 $PY -m wallets.watchlist --from-slot "$FROM" --to-slot "$TO" --build-history "$HISTORY" > "$OUT/log-history.txt" 2>&1 \
    && log "history ok: $(tail -1 "$OUT/log-history.txt")" || { log "history FAILED (see log-history.txt)"; exit 1; }
fi
opt=(--history "$HISTORY")
[ -d "$TRIGGERS/parts" ] && opt+=(--triggers "$TRIGGERS")
[ -n "$WALLETS" ] && opt+=(--wallets "$WALLETS")
echo "$days" | while read -r d lo hi; do
  if [ -f "$OUT/watchlist-stats-$lo.json" ]; then log "$d already done"; continue; fi
  if nice -n 10 $PY -m wallets.watchlist --from-slot "$lo" --to-slot "$hi" --out "$OUT" "${opt[@]}" > "$OUT/log-$d.txt" 2>&1; then
    log "$d ok: $(tail -1 "$OUT/log-$d.txt")"
  else
    log "$d FAILED (see log-$d.txt)"; exit 1
  fi
done
$PY -m wallets.watchlist --report --out "$OUT" > "$OUT/report.md"
log "done: $OUT/report.md"
