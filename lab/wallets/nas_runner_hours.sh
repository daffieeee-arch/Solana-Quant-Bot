#!/bin/bash
# E58 runner census, step 1 (research/spec-runner-census-2026-10-21.md, roadmap #167) on the NAS: the pool-hour series of every
# PumpSwap pool with legs in the dev store (wallets.explore.runner_hours). Store v2 holds legs for the SOL markets only: 'other'
# pools (USDC / PUMP / no quote mint) are in pools.parquet without hours; INFO, not RED. Dev only (1033-1046; common.guard refuses a
# store that reaches the hold-out), or with FORWARD=1 a forward store from epoch 1064 on (common.forward_guard; owner, 2026-10-24).
#
#   bash lab/wallets/nas_runner_hours.sh            (short: one pass over the pool legs; ~15-30 min)
#   FORWARD=1 LAB_STORE=$DATA/store/fwd1064-day-65865b3.duckdb OUT=$DATA/wallets/runners-hours-fwd1064-<sha7> bash lab/wallets/nas_runner_hours.sh
#        (FORWARD=1 needs LAB_STORE and OUT: never the dev store or the dev output directory)
#
# Steps, logged in $OUT/log/nas_runner_hours.log; any failure RED (rc 1):
#   1. the test: test_runner_hours.py;
#   2. the aggregate -> $OUT/{hours.parquet, pools.parquet, hour_bounds.parquet, run-runner-hours.json}; RED when rows, pools or
#      hours are 0 or the sol / reversed classes have no pools or no pool-hours; 'other' reported as INFO; the sha256 of every output.
# Tunables (env): MEMORY (DuckDB limit, default 12GB), THREADS (6), BUCKETS (16), LAB_SPILL_DIR, LAB_SPILL_MAX, OUT, FORWARD.
set -euo pipefail
export LC_ALL=C
FORWARD=${FORWARD:-0}
if [ "$FORWARD" = 1 ]; then
  [ -n "${LAB_STORE:-}" ] && [ -n "${OUT:-}" ] || { echo "FORWARD=1 needs LAB_STORE (a forward store) and OUT" >&2; exit 1; }
  FWD_ARG=--forward
else
  FWD_ARG=
fi

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
OUT=${OUT:-$DATA/wallets/runners-hours-${COMMIT:0:7}}
LOG=$OUT/log
export PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1 LAB_DATA=$DATA LAB_STORE=${LAB_STORE:-$DATA/store/dev2.duckdb} \
  LAB_WALLETS_MEMORY=${MEMORY:-12GB} LAB_WALLETS_THREADS=${THREADS:-6} LAB_MARKOUT_BUCKETS=${BUCKETS:-16} \
  LAB_SPILL_DIR=${LAB_SPILL_DIR:-/lab/tmp/nas-runner-hours} LAB_SPILL_MAX=${LAB_SPILL_MAX:-100GB}
mkdir -p "$OUT" "$LOG" "$LAB_SPILL_DIR"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG/nas_runner_hours.log"; }
red() { log "RED: $*"; exit 1; }
log "start commit=$COMMIT forward=$FORWARD store=$LAB_STORE out=$OUT memory=$LAB_WALLETS_MEMORY threads=$LAB_WALLETS_THREADS buckets=$LAB_MARKOUT_BUCKETS"
cd "$REPO/lab"
[ -f "$LAB_STORE" ] || red "no store at $LAB_STORE"

# 1. the test
rc=0
"$PY" -m pytest -q -p no:cacheprovider wallets/tests/test_runner_hours.py > "$LOG/pytest.log" 2>&1 || rc=$?
log "tests: $(tail -1 "$LOG/pytest.log")"
[ "$rc" = 0 ] || red "tests failed (rc $rc)"

# 2. the aggregate
rc=0
# shellcheck disable=SC2086  # FWD_ARG is empty or one flag
nice -n 10 "$PY" -u -m wallets.explore.runner_hours "$OUT" $FWD_ARG > "$LOG/runner_hours.log" 2>&1 || rc=$?
[ "$rc" = 0 ] || { tail -5 "$LOG/runner_hours.log" | tee -a "$LOG/nas_runner_hours.log"; red "runner_hours failed (rc $rc)"; }
log "aggregate: $(tail -1 "$LOG/runner_hours.log")"
"$PY" - "$OUT/run-runner-hours.json" "$OUT/pools.parquet" "$OUT/hours.parquet" >> "$LOG/nas_runner_hours.log" 2>&1 <<'PYEOF' || red "empty aggregate or the sol / reversed classes missing"
import json, sys, duckdb
j = json.load(open(sys.argv[1])); c = duckdb.connect()
classes = dict(c.execute(f"SELECT quote_class, count(*) FROM '{sys.argv[2]}' GROUP BY 1").fetchall())
per_class = dict(c.execute(f"SELECT p.quote_class, count(*) FROM '{sys.argv[3]}' h JOIN '{sys.argv[2]}' p USING (pool) GROUP BY 1").fetchall())
print(f"rows {j['rows']:,} pools {j['pools']:,} hours {j['hours']} ({j['hour_first']}..{j['hour_last']}); null-close hours {j.get('null_close_hours')}; pools per class {classes}; pool-hours per class {per_class}; {j['seconds']} s")
print(f"INFO: 'other' class: {classes.get('other', 0):,} pools, {per_class.get('other', 0):,} pool-hours (0 expected on store v2: no non-SOL market legs)")
sys.exit(0 if j["rows"] > 0 and j["pools"] > 0 and j["hours"] > 0 and all(classes.get(k, 0) > 0 and per_class.get(k, 0) > 0 for k in ("sol", "reversed")) else 1)
PYEOF
tail -1 "$LOG/nas_runner_hours.log"
for f in hours.parquet pools.parquet hour_bounds.parquet run-runner-hours.json; do log "sha256 $f $(sha256sum "$OUT/$f" | cut -c1-64)"; done
log "done"
