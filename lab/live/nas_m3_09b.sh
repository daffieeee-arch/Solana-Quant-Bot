#!/bin/bash
# M3-09b (research/m3-09b-follow-source-spec-2026-10-20.md v0.2 §4): lab-live's follow source (follow.rs) against Python
# LiveFeed on job 91's segments, on the NAS. No Triton stream, no keys. The rules are lab/live/m3_09b_accept.py's.
#
#   STEP=build   (bookworm runner): lab/live/nas_m1c4.sh STEP=build of this commit: cargo test of lab-live and lab-core
#                (the follow source's named tests among those it requires), the release binary into
#                $DATA/backtests/live-m1c4-<commit7>/bin/.
#   STEP=compare (the lab Python 3.13 runner; kort, about an hour: one LiveFeed pass, two lab-live passes):
#                1. the input, hard (spec §4.2): exactly JOB91_SEGMENTS complete segments under REC/segments before any
#                   pass, and in every summary JOB91_SEGMENTS read and JOB91_EVENTS events; anything else is RED;
#                2. the tests of legs_dump, paper.restart_root and the acceptance rules;
#                3. the store's snapshot (paper.snapshot_export, no seed) and h24 from the store's clock at LO (legs_dump's
#                   store_h24, as LiveFeed takes it with --snapshot-before);
#                4. `paper.legs_dump --segments` once (LiveFeed) and `lab-live legs --follow` twice (the first with
#                   --mem-log, spec §4.5), the same root and slots;
#                5. m3_09b_accept.py compare: the strict tier (its module doc).
#   STEP=restart (the lab Python 3.13 runner; kort, about an hour; after STEP=compare on this commit, whose LiveFeed run is
#                the plain one): spec §4.3, the injected restart. paper.restart_root writes $OUT/restart-root, job 91's
#                segments as links with segments RESTART_AT .. RESTART_AT + 2 again after the last of them (new seqs, the
#                later ones moved up by 3: 3,148 complete segments); LiveFeed once and lab-live twice on it;
#                m3_09b_accept.py restart: the strict tier on it, and both equal the plain run but for the copies (its
#                module doc).
#
#   STEP=build LAB_DATA_ROOT=/lab/data-old-faithful-one/lab bash lab/live/nas_m3_09b.sh      (bookworm runner)
#   STEP=compare LAB_PY=/lab/venv/bin/python bash lab/live/nas_m3_09b.sh                    (lab runner; kort)
#   STEP=restart LAB_PY=/lab/venv/bin/python bash lab/live/nas_m3_09b.sh                    (lab runner; kort, after compare)
#
# Writes to $DATA/backtests/m3-09b-<commit7>/ (job.log, pytest.log, snap/, py/, rs1/, rs2/, restart-root/, restart-py/,
# restart-rs1/, restart-rs2/, acceptance.log, restart-acceptance.log).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
: "${STEP:?STEP=build, compare or restart}"
if [ "$STEP" = build ]; then
  STEP=build exec bash "$REPO/lab/live/nas_m1c4.sh"
fi
case "$STEP" in compare | restart) ;; *) echo "unknown STEP=$STEP"; exit 2 ;; esac
OUT=$DATA/backtests/m3-09b-${COMMIT:0:7}
mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
log "start $STEP commit=$COMMIT out=$OUT"
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
BIN=$DATA/backtests/live-m1c4-${COMMIT:0:7}/bin/lab-live
# Job 91's recorder directory (#299, 2c3e5fe): LiveFeed and the follow source both read REC/segments.
REC=$DATA/backtests/shadow-arms-1053-1054-2c3e5fe/rec
JOB91_SEGMENTS=3145     # job 91's complete segments (nas_m3_09a.sh)
JOB91_EVENTS=16403207   # LiveFeed's events on them (99zzp, c61b43b; note m3-09a v0.3 §1)
RESTART_AT=${RESTART_AT:-2249}   # the restart's first segment again (the VPS's local sample of job 91 starts there)
RESTART_N=3
STORE=$DATA/store/fwd1053-1054.duckdb
LO=454896000
HI=455760000
MAX_RSS_GB=${MAX_RSS_GB:-8}
ACCEPT=$REPO/lab/live/m3_09b_accept.py
[ -x "$BIN" ] || { log "no binary at $BIN: run STEP=build first"; exit 1; }
[ -f "$STORE" ] || { log "no store at $STORE"; exit 1; }
[ -d "$REC/segments" ] || { log "M3-09b: RED: no segments/ under $REC"; exit 1; }
count() {  # the complete segments under $1/segments
  grep -l '"complete": *true' "$1"/segments/*/_segment.json 2>/dev/null | wc -l || true
}
complete=$(count "$REC")
[ "$complete" = "$JOB91_SEGMENTS" ] || { log "M3-09b: RED: $complete complete segments under $REC/segments, not $JOB91_SEGMENTS"; exit 1; }
log "input: $complete complete segments under $REC/segments; lab-live $(sha256sum "$BIN" | cut -c1-16)"
export PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1
cd "$REPO/lab"

pass() {  # name, then the command; a failed pass ends the job RED
  local name=$1 t0 rc=0
  shift
  mkdir -p "$OUT/$name"
  rm -f "$OUT/$name/summary.json"  # a rerun on this commit never reads an earlier run's
  t0=$(date +%s)
  "$@" --summary "$OUT/$name/summary.json" > "$OUT/$name/run.log" 2>&1 || rc=$?
  if [ "$rc" != 0 ]; then
    tail -20 "$OUT/$name/run.log" | tee -a "$OUT/job.log"
    log "$name: FAILED (rc $rc)"
    exit 1
  fi
  echo "$(($(date +%s) - t0))" > "$OUT/$name/secs"
  log "$name: $(cat "$OUT/$name/secs") s"
}
runs() {  # prefix, root, segments: LiveFeed once, lab-live twice (the first with the memory log)
  local prefix=$1 root=$2 segs=$3
  pass "${prefix}py" "$PY" -m paper.legs_dump --segments "$root" --store "$STORE" --from $LO --to $HI --snapshot-before $LO \
    --expect-segments "$segs" --memory 1GB --max-rss-gb "$MAX_RSS_GB"
  pass "${prefix}rs1" "$BIN" legs --follow "$root" --snapshot "$OUT/snap" --snapshot-before $LO --h24 "$H24" --from $LO --to $HI \
    --expect-segments "$segs" --mem-log "$OUT/${prefix}rs1/mem.jsonl"
  pass "${prefix}rs2" "$BIN" legs --follow "$root" --snapshot "$OUT/snap" --snapshot-before $LO --h24 "$H24" --from $LO --to $HI \
    --expect-segments "$segs"
}
accept() {  # log name, then m3_09b_accept.py's arguments; its verdict into job.log, rc 1 when RED
  local name=$1 rc=0
  shift
  "$PY" "$ACCEPT" "$@" > "$OUT/$name" 2>&1 || rc=$?
  tee -a "$OUT/job.log" < "$OUT/$name"
  [ "$rc" = 0 ] || exit 1
}

if [ "$STEP" = compare ]; then
  rc=0
  "$PY" -m pytest -q -rs paper/tests/test_legs_dump.py paper/tests/test_restart_root.py paper/tests/test_lab_live_m3_09b_accept.py \
    -p no:cacheprovider > "$OUT/pytest.log" 2>&1 || rc=$?
  tail -1 "$OUT/pytest.log" | tee -a "$OUT/job.log"
  [ "$rc" = 0 ] || { log "tests: FAILED (rc $rc)"; exit 1; }
  rm -rf "${OUT:?}/snap"
  "$PY" -c "from paper import snapshot_export as s; s.export('$STORE', '$OUT/snap', seed=False)" > "$OUT/snap.log" 2>&1 \
    || { tail -20 "$OUT/snap.log" | tee -a "$OUT/job.log"; log "snapshot export: FAILED"; exit 1; }
fi
[ -f "$OUT/snap/meta.json" ] || { log "no snapshot at $OUT/snap: run STEP=compare first"; exit 1; }
H24=$("$PY" -c "from paper.legs_dump import store_h24; print(store_h24('$STORE', $LO))")
log "h24 at $LO from the store's clock: $H24"

if [ "$STEP" = compare ]; then
  runs "" "$REC" "$JOB91_SEGMENTS"
  accept acceptance.log compare "$OUT" "$JOB91_SEGMENTS" "$JOB91_EVENTS"
  log "done"
  exit 0
fi

# STEP=restart: the plain run's LiveFeed summary is compare's
[ -f "$OUT/py/summary.json" ] || { log "no plain LiveFeed run at $OUT/py: run STEP=compare first"; exit 1; }
rm -rf "${OUT:?}/restart-root"
"$PY" -m paper.restart_root --root "$REC" --out "$OUT/restart-root" --at "$RESTART_AT" --n "$RESTART_N" > "$OUT/restart-root.log" 2>&1 \
  || { tail -5 "$OUT/restart-root.log" | tee -a "$OUT/job.log"; log "restart_root: FAILED"; exit 1; }
want=$((JOB91_SEGMENTS + RESTART_N))
complete=$(count "$OUT/restart-root")
[ "$complete" = "$want" ] || { log "M3-09b restart: RED: $complete complete segments in the restart root, not $want"; exit 1; }
log "restart root: $(cat "$OUT/restart-root.log")"
runs restart- "$OUT/restart-root" "$want"
accept restart-acceptance.log restart "$OUT" "$want" "$JOB91_EVENTS" "$OUT/restart-root" "$RESTART_AT" "$RESTART_N"
log "done"
