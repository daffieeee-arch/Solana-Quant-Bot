#!/bin/bash
# C3 / E54: the F7 paid-flow test on the clean data (research/prereg-f7-betaalde-kopers-clean-2026-10-21.md, roadmap #167) on the
# NAS: exit-free markouts of E20's F7 pool_reversed triggers (wallets.markout --clean-lock, the paid_buyers_60s / paid_max_60s
# columns counted on the locked store's pool table), then the pool-unit report with the pre-set arm rule (wallets.paidflow_report).
# Runs only after E20's run has finished and passed its minimum (clean.lock next to the store), after E45 and E49; nothing beyond
# E20's parts and the locked store is read (markout.clean_lock refuses it).
#
#   LOCK=$DATA/store/clean.lock bash lab/wallets/nas_paidflow.sh        (long: the markout scan of the clean store)
#
# Steps, logged in $OUT/log/nas_paidflow.log; any failure RED (rc 1):
#   1. the lock exists and names a test run with parts/ and an engine snapshot;
#   2. the tests: test_markout.py (dev engine, the clean-lock refusals included) and test_paidflow_report.py;
#   3. markout --clean-lock --delay-ms 600 over the run's parts (family F7) → $OUT/markout-clean-c3.parquet; RED when
#      signals, pool states or rows are 0, the json carries no clean_lock, no delay per epoch or no paid window per epoch, or
#      paid_buyers_60s is missing or NULL on every pool signal;
#   4. paidflow_report → $OUT/paidflow.md + paidflow.json; RED when the report saw no triggers at all or none in the arm
#      (an arm of zero on nine clean days is a pipeline error, not NOT JUDGED); arm triggers / pools / days logged; the sha256 of every output.
# Tunables (env): MEMORY (default 12GB), BUCKETS (8), THREADS (6), WORKERS (4), BOOT (10000), LAB_SPILL_DIR, LAB_SPILL_MAX,
# OUT (default $DATA/wallets/paidflow-<commit7>).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
LOCK=${LOCK:-$DATA/store/clean.lock}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
OUT=${OUT:-$DATA/wallets/paidflow-${COMMIT:0:7}}
LOG=$OUT/log
DEV_ENGINE=${DEV_ENGINE:-$DATA/backtests/f7rev-1033-1046/engine}
export PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1 LAB_WALLETS_OUT=$DATA/wallets LAB_WALLETS_MEMORY=${MEMORY:-12GB} \
  LAB_WALLETS_THREADS=${THREADS:-6} LAB_MARKOUT_BUCKETS=${BUCKETS:-8} LAB_REPORT_WORKERS=${WORKERS:-4} \
  LAB_SPILL_DIR=${LAB_SPILL_DIR:-/lab/tmp/nas-paidflow} LAB_SPILL_MAX=${LAB_SPILL_MAX:-100GB}
mkdir -p "$OUT" "$LOG" "$LAB_SPILL_DIR"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG/nas_paidflow.log"; }
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
LAB_ENGINE=$DEV_ENGINE "$PY" -m pytest -q -p no:cacheprovider wallets/tests/test_markout.py wallets/tests/test_paidflow_report.py > "$LOG/pytest.log" 2>&1 || rc=$?
log "tests: $(tail -1 "$LOG/pytest.log")"
[ "$rc" = 0 ] || red "tests failed (rc $rc)"
grep -q "test_clean_lock_refuses_anything_else" <("$PY" -m pytest --collect-only -q -p no:cacheprovider wallets/tests/test_markout.py 2>/dev/null) \
  || red "the clean-lock refusal test was not collected (engine missing?)"

# 3. the markouts under the lock
rc=0
env -u LAB_ENGINE nice -n 10 "$PY" -u -m wallets.markout --parts "$PARTS" --run clean-c3 --out "$OUT" --families F7 --clean-lock "$LOCK" --delay-ms 600 \
  > "$LOG/markout-clean-c3.log" 2>&1 || rc=$?
[ "$rc" = 0 ] || { tail -5 "$LOG/markout-clean-c3.log" | tee -a "$LOG/nas_paidflow.log"; red "markout failed (rc $rc)"; }
"$PY" - "$OUT/run-markout-clean-c3.json" "$OUT/markout-clean-c3.parquet" >> "$LOG/nas_paidflow.log" 2>&1 <<'PYEOF' || red "markout output is empty, unlocked, without the paid window or without paid_buyers_60s"
import json, sys, duckdb
j = json.load(open(sys.argv[1]))
con = duckdb.connect()
cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM '{sys.argv[2]}'").fetchall()]
counted = con.execute(f"SELECT count(paid_buyers_60s), count(*) FROM '{sys.argv[2]}' WHERE venue = 'pool' AND h = ''").fetchone() if "paid_buyers_60s" in cols else (0, 0)
print(f"markout: signals {j['signals']:,}, pool states {j['pool_states']:,}, rows {j['rows']:,}, store {j.get('store')}, "
      f"clean_lock {j.get('clean_lock') and j['clean_lock'].get('engine_commit')}, delay {j.get('delay_ms')} ms = slots per epoch "
      f"{j.get('delay_slots_per_epoch')}, paid window {j.get('paid_window_ms')} ms = slots per epoch {j.get('paid_window_slots_per_epoch')}, floor {j.get('paid_min_lamports')} lamports, "
      f"parts {len(j.get('parts_sha256') or {})}, paid_buyers_60s counted on {counted[0]:,} of {counted[1]:,} pool entries, {j['seconds']} s")
sys.exit(0 if j["signals"] > 0 and j["pool_states"] > 0 and j["rows"] > 0 and j.get("clean_lock") and j.get("delay_slots_per_epoch") and j.get("paid_window_slots_per_epoch")
         and "paid_buyers_60s" in cols and counted[0] > 0 else 1)
PYEOF
tail -1 "$LOG/nas_paidflow.log"

# 4. the report
rc=0
nice -n 10 "$PY" -m wallets.paidflow_report --markout "$OUT" --run clean-c3 --boot "${BOOT:-10000}" --out "$OUT/paidflow.md" > "$LOG/report.log" 2>&1 || rc=$?
[ "$rc" = 0 ] || { tail -5 "$LOG/report.log" | tee -a "$LOG/nas_paidflow.log"; red "paidflow_report failed (rc $rc)"; }
log "report: $(tail -1 "$LOG/report.log")"
"$PY" - "$OUT/paidflow.json" >> "$LOG/nas_paidflow.log" 2>&1 <<'PYEOF' || red "the report saw no triggers (all or arm): a pipeline error, not NOT JUDGED"
import json, sys
j = json.load(open(sys.argv[1]))
i = j["input"]
print(f"arm: {i['arm_triggers']:,} triggers in {i['arm_pools']:,} pools over {i['arm_days']} days (all F7 pool_reversed: {i['all_triggers']:,} triggers, "
      f"{i['all_pools']:,} pools, {i['all_days']} days); outcome {j['verdict']['outcome']}")
sys.exit(0 if i["all_triggers"] > 0 and i["arm_triggers"] > 0 else 1)
PYEOF
tail -1 "$LOG/nas_paidflow.log"
for f in markout-clean-c3.parquet run-markout-clean-c3.json paidflow.md paidflow.json; do log "sha256 $f $(sha256sum "$OUT/$f" | cut -c1-64)"; done
log "done"
