#!/bin/bash
# x10-jager (research/x10-jager-plan-2026-10-23.md v0.3, roadmap #167) on the NAS: stage A labels per dev epoch (sizes 0.5
# and 2 SOL), stage B features, stage C model (when lightgbm is installed on the NAS lab-py; otherwise stage C runs on
# the VPS on the archived parquet). Dev 1033-1046 only.
#
#   bash lab/wallets/nas_x10.sh            (long: ~7 min per epoch per size at 12 GB / 4 threads, ~3.5 h + stage B ~30 min;
#                                           cold cache ~1.4x; memory <= 16 GB)
#
# Safeguards (LAB review, 2026-10-23):
#   1. deadline: the job starts only when the clock is at or before START_DEADLINE (default Fri 2026-10-23 22:00Z) and
#      kills its whole process group (the Python / DuckDB processes included) at KILL_AT (default Sat 2026-10-24 06:00Z);
#      the spill dir is removed on that path too;
#   2. dev only: epochs outside 1033-1046 are refused, the store must end before the hold-out (max block slot <
#      452,304,000), nothing under store/clean.lock is read or written, and every window's hi is checked by
#      wallets.runners.run (raises when it reaches the hold-out);
#   3. own spill dir $OUT/tmp, removed at the end and on the kill path.
# Steps, logged in $OUT/log/nas_x10.log; any failure RED (rc 1):
#   0. the tests: test_runners.py, test_x10_labels.py, test_x10_features.py, test_x10_model.py;
#   1. per epoch: labels size 0.5 (labels-<lo>.parquet, mint_peaks, early_buyers) and size 2 (labels-<lo>-s2.parquet);
#   2. features.parquet over every window (+ the money-v2 wallet-day pin);
#   3. model.json / model.md / cells-val.parquet / scores-*.parquet when lightgbm imports; else a note in the log;
#   4. sha256 of every output in $OUT/SHA256SUMS.
# Tunables (env): EPOCHS (default 1033..1046), MEMORY (DuckDB, default 12GB), THREADS (4), BOOT (10000), AB_AGAINST (dir of an
# earlier run for the labels A/B, default off), OUT (default
# $DATA/wallets/x10-<commit7>), MONEY (default $DATA/wallets/money-v2-94f0fee), START_DEADLINE, KILL_AT, LAB_SPILL_MAX (100GB).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
STORE=${LAB_STORE:-$DATA/store/dev2.duckdb}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
OUT=${OUT:-$DATA/wallets/x10-${COMMIT:0:7}}
MONEY=${MONEY:-$DATA/wallets/money-v2-94f0fee}
LOG=$OUT/log
EPOCHS=${EPOCHS:-$(seq 1033 1046 | tr '\n' ' ')}
START_DEADLINE=${START_DEADLINE:-2026-10-23T22:00:00Z}
KILL_AT=${KILL_AT:-2026-10-24T06:00:00Z}
HOLDOUT_START=452304000
export PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1 LAB_STORE=$STORE LAB_WALLETS_OUT=$DATA/wallets LAB_WALLETS_MEMORY=${MEMORY:-12GB} \
  LAB_WALLETS_THREADS=${THREADS:-4} LAB_SPILL_DIR=$OUT/tmp LAB_SPILL_MAX=${LAB_SPILL_MAX:-100GB} OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=4
mkdir -p "$OUT" "$LOG" "$OUT/tmp"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG/nas_x10.log"; }
red() { log "RED: $*"; rm -rf "$OUT/tmp"; exit 1; }

# ---------------------------------------------------------------- the pipeline (runs in its own process group, see below)
if [ "${1:-}" = "--pipeline" ]; then
  cd "$REPO/lab"
  # 0. the tests
  rc=0
  "$PY" -m pytest -q -p no:cacheprovider wallets/tests/test_runners.py wallets/tests/test_x10_labels.py wallets/tests/test_x10_features.py \
    wallets/tests/test_x10_model.py > "$LOG/pytest.log" 2>&1 || rc=$?
  log "tests: $(tail -1 "$LOG/pytest.log")"
  [ "$rc" = 0 ] || red "tests failed (rc $rc)"
  grep -Eq "^[0-9]+ passed" <(tail -1 "$LOG/pytest.log") || red "no 'N passed' line"
  # 1. stage A per epoch, both sizes
  for e in $EPOCHS; do
    lo=$((e * 432000)); hi=$(((e + 1) * 432000))
    [ "$hi" -le "$HOLDOUT_START" ] || red "epoch $e reaches the hold-out"
    for size in 0.5 2; do
      tag=$lo; [ "$size" = "0.5" ] || tag="$lo-s$size"
      if [ -f "$OUT/labels-$tag.parquet" ]; then log "labels-$tag.parquet exists, skipped"; continue; fi
      rc=0
      nice -n 10 "$PY" -u -m wallets.x10.labels --from-slot "$lo" --to-slot "$hi" --size-sol "$size" --out "$OUT" > "$LOG/labels-$tag.log" 2>&1 || rc=$?
      [ "$rc" = 0 ] || { tail -5 "$LOG/labels-$tag.log" | tee -a "$LOG/nas_x10.log"; red "labels $tag failed (rc $rc)"; }
      log "labels $tag: $(tail -1 "$LOG/labels-$tag.log" | cut -c1-300)"
    done
  done
  # 1b. A/B of the labels against an earlier archived run (AB_AGAINST=<dir with labels-<lo>[-s2].parquet>; counts only;
  #     the E64 recount on the mark-cap-fixed harness compares with x10-6f294dd, research/x10-hertelling-plan-2026-10-24.md)
  if [ -n "${AB_AGAINST:-}" ]; then
    [ -d "$AB_AGAINST" ] || red "AB_AGAINST $AB_AGAINST is not a directory"
    mkdir -p "$OUT/ab"
    for e in $EPOCHS; do
      lo=$((e * 432000))
      for size in 0.5 2; do
        tag=$lo; [ "$size" = "0.5" ] || tag="$lo-s$size"
        if [ ! -f "$AB_AGAINST/labels-$tag.parquet" ]; then log "ab $tag: no old labels in $AB_AGAINST, skipped"; continue; fi
        rc=0
        "$PY" -m wallets.x10.ab_labels --old "$AB_AGAINST/labels-$tag.parquet" --new "$OUT/labels-$tag.parquet" --out "$OUT/ab" --tag "$tag" > "$LOG/ab-$tag.log" 2>&1 || rc=$?
        [ "$rc" = 0 ] || { tail -3 "$LOG/ab-$tag.log" | tee -a "$LOG/nas_x10.log"; red "ab $tag failed (rc $rc)"; }
        log "ab $tag: $(head -1 "$LOG/ab-$tag.log" | cut -c1-200)"
      done
    done
  fi
  # 2. stage B
  rc=0
  nice -n 10 "$PY" -u -m wallets.x10.features --labels "$OUT" --money "$MONEY" --out "$OUT" > "$LOG/features.log" 2>&1 || rc=$?
  [ "$rc" = 0 ] || { tail -5 "$LOG/features.log" | tee -a "$LOG/nas_x10.log"; red "features failed (rc $rc)"; }
  log "features: $(tail -1 "$LOG/features.log" | cut -c1-400)"
  # 3. stage C when lightgbm is there
  if "$PY" -c "import lightgbm" 2>/dev/null; then
    rc=0
    nice -n 10 "$PY" -u -m wallets.x10.model --features "$OUT/features.parquet" --out "$OUT" --boot "${BOOT:-10000}" > "$LOG/model.log" 2>&1 || rc=$?
    [ "$rc" = 0 ] || { tail -5 "$LOG/model.log" | tee -a "$LOG/nas_x10.log"; red "model failed (rc $rc)"; }
    log "model: $(tail -1 "$LOG/model.log" | cut -c1-400)"
  else
    log "lightgbm not importable on this lab-py: stage C runs on the VPS on $OUT/features.parquet (plan v0.2 section 4)"
  fi
  # 4. pins
  (cd "$OUT" && shopt -s nullglob && LC_ALL=C sha256sum *.parquet *.json *.md *.txt ab/*.json ab/*.md | LC_ALL=C sort -k2 > SHA256SUMS)
  log "pinned $(wc -l < "$OUT/SHA256SUMS") files in $OUT/SHA256SUMS"
  rm -rf "$OUT/tmp"
  log "done"
  exit 0
fi

# ---------------------------------------------------------------- safeguards, then the pipeline under a deadline watcher
now=$(date -u +%s); start_by=$(date -u -d "$START_DEADLINE" +%s); kill_at=$(date -u -d "$KILL_AT" +%s)
log "start commit=$COMMIT store=$STORE epochs='$EPOCHS' out=$OUT memory=$LAB_WALLETS_MEMORY threads=$LAB_WALLETS_THREADS start_deadline=$START_DEADLINE kill_at=$KILL_AT"
[ "$now" -le "$start_by" ] || red "start deadline $START_DEADLINE passed: the job runs after the Saturday clean runs (set START_DEADLINE for that run)"
[ "$kill_at" -gt "$now" ] || red "KILL_AT $KILL_AT is in the past"
for e in $EPOCHS; do
  { [ "$e" -ge 1033 ] && [ "$e" -le 1046 ]; } || red "epoch $e is outside dev 1033-1046 (hold-out 1047-1051 and clean 1055-1063 are closed)"
done
case "$STORE" in *clean*) red "the store path names the clean store: refused";; esac
maxslot=$("$PY" -c "import duckdb, sys; c = duckdb.connect(sys.argv[1], read_only=True); print(c.execute('SELECT max(slot) FROM blocks').fetchone()[0])" "$STORE")
[ "$maxslot" -lt "$HOLDOUT_START" ] || red "store reaches slot $maxslot >= hold-out start $HOLDOUT_START"
[ -d "$MONEY" ] || red "money-v2 dir $MONEY missing"
log "guards: dev only (max block slot $maxslot), store $STORE, clean.lock untouched"

# the pipeline in its own session / process group; the watcher kills the whole group at KILL_AT and cleans the spill dir
setsid bash "$0" --pipeline &
PG=$!
(
  sleep $((kill_at - now))
  log "KILL_AT $KILL_AT reached: killing process group $PG"
  kill -TERM -- "-$PG" 2>/dev/null || true
  sleep 30
  kill -KILL -- "-$PG" 2>/dev/null || true
  rm -rf "$OUT/tmp"
) &
WATCH=$!
trap 'kill -TERM -- "-$PG" 2>/dev/null || true; kill "$WATCH" 2>/dev/null || true; rm -rf "$OUT/tmp"' INT TERM
rc=0
wait "$PG" || rc=$?
kill "$WATCH" 2>/dev/null || true
rm -rf "$OUT/tmp"
[ "$rc" = 0 ] || red "pipeline rc $rc (killed at the deadline or failed; see $LOG/nas_x10.log)"
log "finished rc 0"
