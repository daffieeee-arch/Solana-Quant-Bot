#!/bin/bash
# Codex review #5 on the NAS (kort, report-only: no tournament run): E63, E65 and E70 recomputed from their existing
# parts with the daily loss limit on the exit slot's UTC day (capital_ceiling.capital_days, paper's book), then each
# report beside its earlier one (backtest.report_delta: every changed number, old and new; RED if anything without
# limits moves). The earlier reports stay where they are; this job only adds.
#
#   LAB_PY=/lab/venv/bin/python bash lab/backtest/nas_capital_exitday.sh
#
# Inputs (the runs' defaults; each must exist): dev2 ($DATA/store/dev2.duckdb); E63's run $E63 (capital-ceiling-d15ac45)
# and its report $E63_OLD (ceiling-report-b149a7d/ceiling.json, E65's and E70's gate reference); the C3 keys; the parity
# reference (families-v2-1033-1046); E65's runs racing-d0-01b1853 and racing-dB-01b1853, its race table
# (racing-cost-01b1853/race) and report, route (ii)'s rows; E70's runs tolsweep-t002/-t010/-t015-e3bef48 and report.
# Writes $DATA/backtests/capital-exitday-<commit7>/ (e63/, e65/, e70/, delta-*.md/.json, job.log, SHA256SUMS). Any RED: rc 1.
set -euo pipefail
export LC_ALL=C
REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
B=$DATA/backtests
STORE=$DATA/store/dev2.duckdb
E63=${E63:-$B/capital-ceiling-d15ac45}
E63_OLD=${E63_OLD:-$E63/ceiling-report-b149a7d/ceiling.json}
C3_KEYS=${C3_KEYS:-$DATA/wallets/paidflow-dev-vps/markout-f7rev-txdelay.parquet}
PARITY_REF=${PARITY_REF:-$B/families-v2-1033-1046}
ROUTE_II=${ROUTE_II:-$B/route-ii-full-547f422/entry-rows/part-000000.parquet}
E65=${E65:-$B/racing-cost-01b1853}
E65_RUNS=("$B/racing-d0-01b1853" "$B/racing-dB-01b1853")
E70=${E70:-$B/tol-sweep-e3bef48}
E70_RUNS=("$B/tolsweep-t002-e3bef48" "$B/tolsweep-t010-e3bef48" "$B/tolsweep-t015-e3bef48")
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
OUT=$B/capital-exitday-${COMMIT:0:7}
mkdir -p "$OUT"
export PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1 LAB_SPILL_DIR=$DATA/tmp/capital-exitday
mkdir -p "$LAB_SPILL_DIR"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
red() { log "RED: $*"; exit 1; }
log "start commit=$COMMIT out=$OUT"
for f in "$STORE" "$E63/config.json" "$E63_OLD" "$C3_KEYS" "$PARITY_REF/config.json" "$ROUTE_II" "$E65/race/race.parquet" \
         "$E65/report/racing.json" "$E70/report/tol.json" "${E65_RUNS[0]}/config.json" "${E65_RUNS[1]}/config.json" \
         "${E70_RUNS[0]}/config.json" "${E70_RUNS[1]}/config.json" "${E70_RUNS[2]}/config.json"; do
  [ -e "$f" ] || red "missing input $f"
done
[ -e "$OUT/e63" ] && red "$OUT/e63 exists: a rerun of this commit writes nothing over it"
step() {  # name, then the command
  local name=$1 rc=0; shift
  "$@" > "$OUT/$name.log" 2>&1 || rc=$?
  log "$name: rc $rc; $(tail -1 "$OUT/$name.log")"
  [ "$rc" = 0 ] || red "$name (its log)"
}
cd "$REPO/lab"
step e63 "$PY" -m backtest.capital_ceiling --parts "$E63" --c3-keys "$C3_KEYS" --store "$STORE" --out "$OUT/e63" \
  --memory "${MEMORY:-8GB}" --threads 4 --parity "$PARITY_REF"
step e65 "$PY" -m backtest.racing_cost report --parts "${E65_RUNS[@]}" "$E63" --race "$E65/race" --c3-keys "$C3_KEYS" \
  --store "$STORE" --route-ii "$ROUTE_II" --e63-json "$OUT/e63/ceiling.json" --out "$OUT/e65" --draws "${DRAWS:-200}" \
  --workers "${WORKERS:-4}" --memory "${MEMORY:-8GB}"
step e70 "$PY" -m backtest.tol_sweep --parts "${E70_RUNS[@]}" "$E63" --e63-json "$OUT/e63/ceiling.json" --c3-keys "$C3_KEYS" \
  --store "$STORE" --out "$OUT/e70" --memory "${MEMORY:-8GB}"
step delta-e63 "$PY" -m backtest.report_delta "$E63_OLD" "$OUT/e63/ceiling.json" --label E63 --out "$OUT"
step delta-e65 "$PY" -m backtest.report_delta "$E65/report/racing.json" "$OUT/e65/racing.json" --label E65 --out "$OUT"
step delta-e70 "$PY" -m backtest.report_delta "$E70/report/tol.json" "$OUT/e70/tol.json" --label E70 --out "$OUT"
rm -rf "$LAB_SPILL_DIR"
log "done: GREEN"
(cd "$OUT" && sha256sum job.log e63/ceiling.json e63/ceiling.md e65/racing.json e65/racing.md e70/tol.json e70/tol.md \
  delta-E63.md delta-E63.json delta-E65.md delta-E65.json delta-E70.md delta-E70.json > SHA256SUMS)
