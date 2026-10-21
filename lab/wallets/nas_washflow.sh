#!/bin/bash
# E59: the wash-flow population replica on dev (research/prereg-washflow-replica-2026-10-22.md v1.0, roadmap #167) on the NAS:
# selection on the fit half from money-v1, replica of the selected wallets' legs on the test half with the dev store
# (wallets.explore.washflow_replica). Dev only (1033-1046); never the hold-out, clean or live data.
#
#   bash lab/wallets/nas_washflow.sh            (short-long: the legs of the selected wallets' pools over 10 days; ~20-40 min)
#
# Steps, logged in $OUT/log/nas_washflow.log; any failure RED (rc 1):
#   1. the tests: test_washflow_replica.py;
#   2. the replica -> $OUT/{selected.parquet, legs-shift.parquet, washflow.md, washflow.json, run-washflow.json}; RED when no
#      wallet is selected, the extract is empty or the outcome is missing; the sha256 of every output.
# Tunables (env): MEMORY (DuckDB limit, default 12GB), THREADS (6), BUCKETS (4), BOOT (10000), LAB_SPILL_DIR, LAB_SPILL_MAX, MONEY, OUT.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
OUT=${OUT:-$DATA/wallets/washflow-${COMMIT:0:7}}
LOG=$OUT/log
export PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1 LAB_DATA=$DATA LAB_STORE=${LAB_STORE:-$DATA/store/dev2.duckdb} LAB_MONEY=${MONEY:-$DATA/wallets/money-v1} \
  LAB_WALLETS_MEMORY=${MEMORY:-12GB} LAB_WALLETS_THREADS=${THREADS:-6} LAB_MARKOUT_BUCKETS=${BUCKETS:-4} BOOT=${BOOT:-10000} \
  LAB_SPILL_DIR=${LAB_SPILL_DIR:-/lab/tmp/nas-washflow} LAB_SPILL_MAX=${LAB_SPILL_MAX:-100GB}
mkdir -p "$OUT" "$LOG" "$LAB_SPILL_DIR"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG/nas_washflow.log"; }
red() { log "RED: $*"; exit 1; }
log "start commit=$COMMIT store=$LAB_STORE money=$LAB_MONEY out=$OUT memory=$LAB_WALLETS_MEMORY threads=$LAB_WALLETS_THREADS buckets=$LAB_MARKOUT_BUCKETS"
cd "$REPO/lab"
[ -f "$LAB_STORE" ] || red "no store at $LAB_STORE"
[ -s "$LAB_MONEY/run-money.json" ] || red "no money-v1 run at $LAB_MONEY"

# 1. the tests
rc=0
"$PY" -m pytest -q -p no:cacheprovider wallets/tests/test_washflow_replica.py > "$LOG/pytest.log" 2>&1 || rc=$?
log "tests: $(tail -1 "$LOG/pytest.log")"
[ "$rc" = 0 ] || red "tests failed (rc $rc)"

# 2. the replica
rc=0
nice -n 10 "$PY" -u -m wallets.explore.washflow_replica "$OUT" > "$LOG/washflow.log" 2>&1 || rc=$?
[ "$rc" = 0 ] || { tail -5 "$LOG/washflow.log" | tee -a "$LOG/nas_washflow.log"; red "washflow_replica failed (rc $rc)"; }
log "replica: $(tail -1 "$LOG/washflow.log")"
"$PY" - "$OUT/run-washflow.json" "$OUT/washflow.json" >> "$LOG/nas_washflow.log" 2>&1 <<'PYEOF' || red "empty selection or extract, or no outcome"
import json, sys
r, j = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
p = j["cells"]["k3_iii_base_all"]
print(f"selected {r['selected']}, legs {r['legs']:,}, pool legs {r['pool_legs']:,}, days {p['days']}, outcome {j['verdict']['outcome']}: primary {p['sol_per_day']:+.2f} SOL/day [{p['lo']}; {p['hi']}], net per leg {p['net_per_leg']}")
sys.exit(0 if r["selected"] > 0 and r["legs"] > 0 and r["pool_legs"] > 0 and p["days"] > 0 and j["verdict"]["outcome"] in ("LEAD LIVES", "CLOSED") else 1)
PYEOF
tail -1 "$LOG/nas_washflow.log"
for f in selected.parquet legs-shift.parquet washflow.md washflow.json run-washflow.json; do log "sha256 $f $(sha256sum "$OUT/$f" | cut -c1-64)"; done
log "done"
