#!/bin/bash
# The F7 mechanism test on the clean data (research/prereg-f7-mechanism-clean-2026-10-21.md, roadmap #167) on the NAS:
# exit-free markouts of E20's F7 pool_reversed triggers and their N2 twins (wallets.markout --clean-lock), then the
# pool-unit report with the pre-set rule (wallets.mechanism_report). Runs only after E20's run has finished and passed
# its minimum: that is when backtest.clean_test writes clean.lock next to the store; nothing here reads the clean data
# before that, and nothing beyond E20's parts and the locked store is read at all (markout.clean_lock refuses it).
#
#   LOCK=$DATA/store/clean.lock bash lab/wallets/nas_mechanism.sh        (long: the markout scan of the clean store)
#
# Steps, logged in $OUT/log/nas_mechanism.log; any failure RED (rc 1):
#   1. the lock exists and names a test run with parts/ and an engine snapshot;
#   2. the tests: wallets/tests/test_markout.py (with the dev engine, the clean-lock refusals included) and
#      test_mechanism_report.py;
#   3. markout --clean-lock over the run's parts (families F7 and N2_F7) → $OUT/markout-clean-e20.parquet; RED when
#      signals, pool states or rows are 0 or the json carries no clean_lock;
#   4. mechanism_report → $OUT/mechanism.md + mechanism.json; the sha256 of every output.
# Tunables (env): MEMORY (DuckDB limit, default 12GB), BUCKETS (venue-state match buckets, default 8), THREADS (6),
# WORKERS (bootstrap processes, 4), BOOT (10000), LAB_SPILL_DIR, LAB_SPILL_MAX, OUT (default $DATA/wallets/mechanism-<commit7>).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
LOCK=${LOCK:-$DATA/store/clean.lock}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
OUT=${OUT:-$DATA/wallets/mechanism-${COMMIT:0:7}}
LOG=$OUT/log
DEV_ENGINE=${DEV_ENGINE:-$DATA/backtests/f7rev-1033-1046/engine}
export PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1 LAB_WALLETS_OUT=$DATA/wallets LAB_WALLETS_MEMORY=${MEMORY:-12GB} \
  LAB_WALLETS_THREADS=${THREADS:-6} LAB_MARKOUT_BUCKETS=${BUCKETS:-8} LAB_REPORT_WORKERS=${WORKERS:-4} \
  LAB_SPILL_DIR=${LAB_SPILL_DIR:-/lab/tmp/nas-mechanism} LAB_SPILL_MAX=${LAB_SPILL_MAX:-100GB}
mkdir -p "$OUT" "$LOG" "$LAB_SPILL_DIR"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG/nas_mechanism.log"; }
red() { log "RED: $*"; exit 1; }
log "start commit=$COMMIT lock=$LOCK out=$OUT memory=$LAB_WALLETS_MEMORY buckets=$LAB_MARKOUT_BUCKETS threads=$LAB_WALLETS_THREADS workers=$LAB_REPORT_WORKERS"
cd "$REPO/lab"

# 1. the lock: E20's run has finished
[ -f "$LOCK" ] || red "no clean.lock at $LOCK: E20's run has not finished or not passed its minimum; the test waits"
TEST_RUN=$("$PY" -c 'import json, sys; print(json.load(open(sys.argv[1]))["test_run"])' "$LOCK")
PARTS=$TEST_RUN/parts
[ -d "$PARTS" ] || red "the lock's test run has no parts at $PARTS"
[ -f "$TEST_RUN/engine/COMMIT" ] || red "the lock's test run has no engine snapshot at $TEST_RUN/engine"
log "lock: test_run=$TEST_RUN engine=$(cat "$TEST_RUN/engine/COMMIT" | cut -c1-7) trading_days=$("$PY" -c 'import json, sys; print(json.load(open(sys.argv[1]))["trading_days"])' "$LOCK")"

# 2. the tests (the dev engine for test_markout's mini store; the clean run itself never sees it: step 3 unsets it)
rc=0
LAB_ENGINE=$DEV_ENGINE "$PY" -m pytest -q -p no:cacheprovider wallets/tests/test_markout.py wallets/tests/test_mechanism_report.py > "$LOG/pytest.log" 2>&1 || rc=$?
log "tests: $(tail -1 "$LOG/pytest.log")"
[ "$rc" = 0 ] || red "tests failed (rc $rc)"
grep -q "test_clean_lock_refuses_anything_else" <("$PY" -m pytest --collect-only -q -p no:cacheprovider wallets/tests/test_markout.py 2>/dev/null) \
  || red "the clean-lock refusal test was not collected (engine missing?)"

# 3. the markouts under the lock
rc=0
env -u LAB_ENGINE nice -n 10 "$PY" -u -m wallets.markout --parts "$PARTS" --run clean-e20 --out "$OUT" --families F7,N2_F7 --clean-lock "$LOCK" \
  > "$LOG/markout-clean-e20.log" 2>&1 || rc=$?
[ "$rc" = 0 ] || { tail -5 "$LOG/markout-clean-e20.log" | tee -a "$LOG/nas_mechanism.log"; red "markout failed (rc $rc)"; }
"$PY" - "$OUT/run-markout-clean-e20.json" >> "$LOG/nas_mechanism.log" 2>&1 <<'PYEOF' || red "markout output is empty or unlocked"
import json, sys
j = json.load(open(sys.argv[1]))
print(f"markout: signals {j['signals']:,}, pool states {j['pool_states']:,}, rows {j['rows']:,}, store {j.get('store')}, "
      f"clean_lock {j.get('clean_lock') and j['clean_lock'].get('engine_commit')}, {j['seconds']} s")
sys.exit(0 if j["signals"] > 0 and j["pool_states"] > 0 and j["rows"] > 0 and j.get("clean_lock") else 1)
PYEOF
tail -1 "$LOG/nas_mechanism.log"

# 4. the report
rc=0
nice -n 10 "$PY" -m wallets.mechanism_report --markout "$OUT" --run clean-e20 --boot "${BOOT:-10000}" --out "$OUT/mechanism.md" > "$LOG/report.log" 2>&1 || rc=$?
[ "$rc" = 0 ] || { tail -5 "$LOG/report.log" | tee -a "$LOG/nas_mechanism.log"; red "mechanism_report failed (rc $rc)"; }
log "report: $(tail -1 "$LOG/report.log")"
for f in markout-clean-e20.parquet run-markout-clean-e20.json mechanism.md mechanism.json; do log "sha256 $f $(sha256sum "$OUT/$f" | cut -c1-64)"; done
log "done"
