#!/bin/bash
# Token transfers and wallet funding (spec-token-transfers-2026-10-15.md v3) on the NAS: Q1 (corrected insider
# share on the M1x triggers), Q2 (funder graph with the money-v1 labels), Q3 and the conservation / reproduction
# checks, per epoch of the transfers extraction (events/transfers-v1, epochs 1040 and 1045).
#
#   LAB_SPILL_DIR=/lab/tmp/nas-transfers LAB_SPILL_MAX=50GB bash lab/wallets/nas_transfers.sh
#
# Tunables (env): EPOCHS (default 1040,1045), CHUNKS (the transfers chunks root), MEMORY, THREADS,
# TRIGGERS (parquet mint,t,ins_share; default built from the m1x parts), MONEY (money-v1 dir for the labels).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
OUT=${LAB_WALLETS_OUT:-$DATA/wallets}
STORE=${LAB_STORE:-$DATA/store/dev2.duckdb}
CHUNKS=${CHUNKS:-$DATA/events/transfers-v1/chunks}
EPOCHS=${EPOCHS:-1040,1045}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
M1X=${M1X_PARTS:-$DATA/backtests/m1x-dev-1033-1046-5090d14/parts}
MONEY=${MONEY:-$DATA/wallets/money-v1}
TV=$OUT/transfers-v1
LOG=$TV/log

export PYTHONPATH=$REPO/lab LAB_STORE=$STORE LAB_WALLETS_OUT=$OUT LAB_WALLETS_MEMORY=${MEMORY:-12GB} LAB_WALLETS_THREADS=${THREADS:-4}
export LAB_SPILL_DIR=${LAB_SPILL_DIR:-/lab/tmp/nas-transfers} LAB_SPILL_MAX=${LAB_SPILL_MAX:-50GB}
mkdir -p "$TV" "$LOG" "$LAB_SPILL_DIR"
log() { echo "$(date +%FT%T) $*" | tee -a "$LOG/nas_transfers.log"; }
log "start repo=$REPO commit=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || cat "$REPO/COMMIT" 2>/dev/null || echo ?) store=$STORE chunks=$CHUNKS epochs=$EPOCHS out=$TV"

# Q1 triggers: one row per M1x trigger (mint, t, the engine's old insider share) from the parts' base rows,
# named after the m1x run (default the run after #235, 5090d14).
TRIGGERS=${TRIGGERS:-$TV/triggers-m1x-$(basename "$(dirname "$M1X")").parquet}
if [ ! -s "$TRIGGERS" ]; then
  "$PY" - "$M1X" "$TRIGGERS" <<'PYEOF'
import sys, duckdb
parts, out = sys.argv[1], sys.argv[2]
con = duckdb.connect(); con.execute("SET memory_limit='4GB'; SET threads TO 4")
con.execute(f"""COPY (SELECT mint, t::BIGINT AS t, any_value(ins_share) AS ins_share, any_value(family) AS family
                     FROM read_parquet('{parts}/*.parquet', union_by_name = true)
                     WHERE overlay = 'none' AND d = 1 AND size_sol = 0.5 AND family IN ('M1P', 'M1E', 'N2_M1P', 'N2_M1E') GROUP BY mint, t)
               TO '{out}' (FORMAT PARQUET)""")
print(out, con.execute(f"SELECT count(*) FROM '{out}'").fetchone()[0], "triggers")
PYEOF
fi

IFS=',' read -ra EP <<< "$EPOCHS"
for e in "${EP[@]}"; do
  lo=$((e * 432000)); hi=$(((e + 1) * 432000)); od=$TV/epoch-$e
  if [ -s "$od/transfers.md" ]; then log "epoch $e exists, skipped"; continue; fi
  log "epoch $e [$lo, $hi)"
  if nice -n 10 "$PY" -u -m wallets.transfers --chunks "$CHUNKS" --from-slot "$lo" --to-slot "$hi" --triggers "$TRIGGERS" --money "$MONEY" --out "$od" > "$LOG/transfers-$e.log" 2>&1; then
    log "epoch $e ok: $(tail -1 "$LOG/transfers-$e.log")"
  else
    log "epoch $e FAILED (see $LOG/transfers-$e.log)"; exit 1
  fi
  # DIAG=1: the check 4.1 diagnosis (per-transaction residuals by pattern) next to the epoch's report
  if [ "${DIAG:-0}" = "1" ] && [ ! -s "$od/diag/diag.md" ]; then
    log "epoch $e diag"
    if nice -n 10 "$PY" -u -m wallets.transfers_diag --chunks "$CHUNKS" --from-slot "$lo" --to-slot "$hi" --out "$od/diag" > "$LOG/diag-$e.log" 2>&1; then
      log "epoch $e diag ok: $(tail -1 "$LOG/diag-$e.log")"
    else
      log "epoch $e diag FAILED (see $LOG/diag-$e.log)"; exit 1
    fi
  fi
done
(cd "$TV" && find . -name '*.parquet' -o -name '*.md' -o -name '*.json' | sort | xargs sha256sum > SHA256SUMS)
log "done"
