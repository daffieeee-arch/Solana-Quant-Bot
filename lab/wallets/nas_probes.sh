#!/bin/bash
# Breadth probes E71-E74 (research/breedte-proeven-plan-2026-10-24.md v0.2 sha 7bbd1aa1…, roadmap #167) on the NAS,
# one probe per call, dev 1033-1046 only, three sizes (0.5 / 1.25 / 5 SOL = capital 10 / 25 / 100 over 20).
#
#   STEP=e74 bash lab/wallets/nas_probes.sh      (kort: PUMP-quote curves, ~1,100 mints per epoch, 3 sizes x 12 epochs)
#   STEP=e71 bash lab/wallets/nas_probes.sh      (lang: Mayhem curves + the base-rate fix)
#   STEP=e72 bash lab/wallets/nas_probes.sh      (lang: ticker clusters)
#   STEP=e73 bash lab/wallets/nas_probes.sh      (lang: good wallets together; needs money-v2)
#
# Safeguards (as nas_x10.sh, LAB review 2026-10-23):
#   1. deadline: starts only at or before START_DEADLINE (default Sat 2026-10-24 09:00Z) and kills its whole process
#      group at KILL_AT (default Sat 2026-10-24 09:45Z, coordinator: the clean runs come first); override by env;
#   2. dev only: epochs outside 1033-1046 refused, the store must end before the hold-out, nothing under clean.lock;
#   3. own spill dir $OUT/tmp, removed at the end and on the kill path.
# Steps, logged in $OUT/log/nas_probes.log; any failure RED (rc 1):
#   0. tests: test_runners.py, test_x10_labels.py, test_probes.py;
#   1. per epoch x size: wallets.probes.run --probe $STEP (window = the epoch; <probe>-<lo>.parquet + run json);
#   2. wallets.probes.report --probe $STEP (report-<probe>.json / .md: cells, CI, verdict, adds-to H5/C3);
#   3. SHA256SUMS over every output.
# Tunables (env): EPOCHS (default 1033..1046; e74 skips epochs before 1035 by itself: no reference price), MEMORY (12GB),
# THREADS (4), BOOT (10000), OUT (default $DATA/wallets/probes-<STEP>-<commit7>), START_DEADLINE, KILL_AT, LAB_SPILL_MAX (100GB).
set -euo pipefail

STEP=${STEP:?set STEP=e74|e71|e72|e73}
case "$STEP" in e71|e72|e73|e74) ;; *) echo "unknown STEP $STEP"; exit 1;; esac
REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
STORE=${LAB_STORE:-$DATA/store/dev2.duckdb}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
OUT_SET=${OUT:-}
OUT=${OUT:-$DATA/wallets/probes-$STEP-${COMMIT:0:7}}
LOG=$OUT/log
EPOCHS=${EPOCHS:-$(seq 1033 1046 | tr '\n' ' ')}
FORWARD=${FORWARD:-0}   # 1: a released forward store (epoch >= 1064; owner 2026-10-24): wallets.probes.run --forward (common.forward_guard); needs EPOCHS, LAB_STORE and a new OUT
FORWARD_START=459648000
SIZES=${SIZES:-"0.5 1.25 5"}
START_DEADLINE=${START_DEADLINE:-2026-10-24T09:00:00Z}
KILL_AT=${KILL_AT:-2026-10-24T09:45:00Z}
HOLDOUT_START=452304000
export PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1 LAB_STORE=$STORE LAB_WALLETS_MEMORY=${MEMORY:-12GB} LAB_WALLETS_THREADS=${THREADS:-4} \
  LAB_SPILL_DIR=$OUT/tmp LAB_SPILL_MAX=${LAB_SPILL_MAX:-100GB} OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=4
mkdir -p "$OUT" "$LOG" "$OUT/tmp"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG/nas_probes.log"; }
red() { log "RED: $*"; rm -rf "${OUT:?}/tmp"; exit 1; }

# ---------------------------------------------------------------- the pipeline (its own process group, see below)
if [ "${1:-}" = "--pipeline" ]; then
  cd "$REPO/lab"
  rc=0
  "$PY" -m pytest -q -p no:cacheprovider wallets/tests/test_runners.py wallets/tests/test_x10_labels.py wallets/tests/test_probes.py > "$LOG/pytest.log" 2>&1 || rc=$?
  log "tests: $(tail -1 "$LOG/pytest.log")"
  [ "$rc" = 0 ] || red "tests failed (rc $rc)"
  grep -Eq "^[0-9]+ passed" <(tail -1 "$LOG/pytest.log") || red "no 'N passed' line"
  for e in $EPOCHS; do
    lo=$((e * 432000)); hi=$(((e + 1) * 432000))
    if [ "$FORWARD" = 1 ]; then [ "$lo" -ge "$FORWARD_START" ] || red "epoch $e lies before 1064 (FORWARD=1)"; else [ "$hi" -le "$HOLDOUT_START" ] || red "epoch $e reaches the hold-out"; fi
    if [ "$STEP" = e74 ] && [ "$e" -lt 1035 ]; then log "epoch $e before the PUMP reference pool: skipped"; continue; fi
    for size in $SIZES; do
      tag="$lo-s$size"
      if [ -f "$OUT/$STEP-$tag.parquet" ]; then log "$STEP-$tag.parquet exists, skipped"; continue; fi
      rc=0
      nice -n 10 "$PY" -u -m wallets.probes.run --probe "$STEP" --from-slot "$lo" --to-slot "$hi" --size-sol "$size" --out "$OUT/s$size" "${fwd[@]}" > "$LOG/$STEP-$tag.log" 2>&1 || rc=$?
      [ "$rc" = 0 ] || { tail -5 "$LOG/$STEP-$tag.log" | tee -a "$LOG/nas_probes.log"; red "$STEP $tag failed (rc $rc)"; }
      ln -sf "s$size/$STEP-$lo.parquet" "$OUT/$STEP-$tag.parquet"
      log "$STEP $tag: $(tail -1 "$LOG/$STEP-$tag.log" | cut -c1-300)"
    done
  done
  # the report reads every size dir: gather the parquet files into one dir of real files
  mkdir -p "$OUT/all"
  for size in $SIZES; do for f in "$OUT/s$size"/$STEP-*.parquet; do [ -f "$f" ] && cp -f "$f" "$OUT/all/$(basename "${f%.parquet}")-s$size.parquet"; done; done
  rc=0
  nice -n 10 "$PY" -u -m wallets.probes.report --probe "$STEP" --in "$OUT/all" --out "$OUT" --boot "${BOOT:-10000}" > "$LOG/report.log" 2>&1 || rc=$?
  [ "$rc" = 0 ] || { tail -5 "$LOG/report.log" | tee -a "$LOG/nas_probes.log"; red "report failed (rc $rc)"; }
  log "report: $(head -1 "$LOG/report.log")"
  (cd "$OUT" && shopt -s nullglob && LC_ALL=C sha256sum s*/*.parquet s*/*.json all/*.parquet report-*.json report-*.md | LC_ALL=C sort -k2 > SHA256SUMS)
  log "pinned $(wc -l < "$OUT/SHA256SUMS") files in $OUT/SHA256SUMS"
  rm -rf "${OUT:?}/tmp"
  log "done"
  exit 0
fi

# ---------------------------------------------------------------- safeguards, then the pipeline under a deadline watcher
now=$(date -u +%s); start_by=$(date -u -d "$START_DEADLINE" +%s); kill_at=$(date -u -d "$KILL_AT" +%s)
log "start step=$STEP commit=$COMMIT store=$STORE epochs='$EPOCHS' sizes='$SIZES' out=$OUT memory=$LAB_WALLETS_MEMORY threads=$LAB_WALLETS_THREADS start_deadline=$START_DEADLINE kill_at=$KILL_AT"
[ "$now" -le "$start_by" ] || red "start deadline $START_DEADLINE passed (set START_DEADLINE / KILL_AT for a later slot)"
[ "$kill_at" -gt "$now" ] || red "KILL_AT $KILL_AT is in the past"
fwd=(); [ "$FORWARD" = 1 ] && fwd=(--forward)
for e in $EPOCHS; do
  if [ "$FORWARD" = 1 ]; then
    [ "$e" -ge 1064 ] || red "epoch $e is before 1064 (FORWARD=1 reads released forward stores only)"
  else
    { [ "$e" -ge 1033 ] && [ "$e" -le 1046 ]; } || red "epoch $e is outside dev 1033-1046 (hold-out 1047-1051 and clean 1055-1063 are closed)"
  fi
done
if [ "$FORWARD" = 1 ]; then
  [ -n "${LAB_STORE:-}" ] || red "FORWARD=1 needs LAB_STORE (a forward store)"
  [ -n "${OUT_SET:-}" ] || red "FORWARD=1 needs its own OUT (never the development probes output)"
fi
case "$STORE" in *clean*) red "the store path names the clean store: refused";; esac
if [ "$STEP" = e73 ]; then
  [ -z "${LAB_E73_SAMPLE:-}" ] || red "LAB_E73_SAMPLE is set: a sampled good set is a smoke, never a result"
  case "${LAB_E73_GOOD:-$OUT/good-days.parquet}" in "$OUT"/*) ;; *) red "LAB_E73_GOOD outside $OUT: the job builds its own good set";; esac
fi
maxslot=$("$PY" -c "import duckdb, sys; c = duckdb.connect(sys.argv[1], read_only=True); print(c.execute('SELECT max(slot) FROM blocks').fetchone()[0])" "$STORE")
if [ "$FORWARD" = 1 ]; then
  minslot=$("$PY" -c "import duckdb, sys; c = duckdb.connect(sys.argv[1], read_only=True); print(c.execute('SELECT min(slot) FROM blocks').fetchone()[0])" "$STORE")
  [ "$minslot" -ge "$FORWARD_START" ] || red "store starts at slot $minslot, before epoch 1064 (FORWARD=1); the probe job checks the store meta too (forward_guard)"
else
  [ "$maxslot" -lt "$HOLDOUT_START" ] || red "store reaches slot $maxslot >= hold-out start $HOLDOUT_START"
fi
log "guards: dev only (max block slot $maxslot), store $STORE, clean.lock untouched"

setsid bash "$0" --pipeline &
PG=$!
(
  trap 'exit 0' TERM
  sleep $((kill_at - now)) &
  wait $! || exit 0
  log "KILL_AT $KILL_AT reached: killing process group $PG"
  kill -TERM -- "-$PG" 2>/dev/null || true
  sleep 30
  kill -KILL -- "-$PG" 2>/dev/null || true
  rm -rf "${OUT:?}/tmp"
) &
WATCH=$!
trap 'kill -TERM -- "-$PG" 2>/dev/null || true; kill "$WATCH" 2>/dev/null || true; rm -rf "${OUT:?}/tmp"' INT TERM
rc=0
wait "$PG" || rc=$?
kill "$WATCH" 2>/dev/null || true
rm -rf "${OUT:?}/tmp"
[ "$rc" = 0 ] || red "pipeline rc $rc (killed at the deadline or failed; see $LOG/nas_probes.log)"
log "finished rc 0"
