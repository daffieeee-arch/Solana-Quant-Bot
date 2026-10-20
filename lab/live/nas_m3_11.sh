#!/bin/bash
# M3-11 (research/m3-11-paper-loop-spec-2026-10-20.md v0.3 §5): lab-live's paper loop on the follow source (`lab-live
# paper --follow`) against paper.engine's own live run (paper/live_reference.py: main with LiveFeed given the snapshot
# before the first live slot and, but in seed-h24, a fixed h24), on the NAS. Paper only: no Triton stream, no keys, no
# signing. The rules are lab/live/m3_11_accept.py's. Both sides take the paper start's config and groups
# (paper/k3-paper-2026-10-12.yaml, paper/h-arms-2026-10-16.yaml) with risk on; lab-live their groups and cost scenario
# as the Python journal holds them.
#
#   STEP=build    (bookworm runner): lab/live/nas_m1c4.sh STEP=build of this commit (lab-live and lab-core tests, the M3-11
#                 ones among those it requires; the release binary into $DATA/backtests/live-m1c4-<commit7>/bin/).
#   STEP=paper    (lab runner; lang: paper.engine with LiveFeed and lab-live twice over job 91's 3,145 segments):
#                 1. the input, hard: exactly JOB91_SEGMENTS complete segments under REC/segments;
#                 2. the Python tests of M3-11 (the golden cases, live_reference, arrivals_fixture, the acceptance);
#                 3. the store's snapshot (no seed: the store starts after LO - h24, so the window holds no slot) and h24 at
#                    LO from the store's clock;
#                 4. the reference into py/, lab-live into rs1/ (with --mem-log) and rs2/, the snapshot before LO;
#                 5. m3_11_accept.py strict with --incomplete: the run's first day has farm_complete 0 (spec §2.3).
#   STEP=seed     (lab runner; kort, after paper): job 91 from SEED_FROM_SEQ as links (paper.restart_root --from-seq), a
#                 start inside the store; its snapshot with the seed of [first - 2 h24, first) (the window of seed and of
#                 seed-h24 inside it; lab-live takes only the window's trades); h24 the store clock's at first; strict with
#                 --seeded (spec §5.3: the farm_complete 0 gate does not apply, no row is before complete_from there).
#   STEP=seed-h24 (lab runner; kort, after seed): the same root and snapshot with h24 unset on both sides (production's
#                 path: the seed's h24 measured from the first segment's blocks, spec v0.2 #7); strict with --seeded.
#   STEP=arrivals (lab runner; kort, after paper): paper.arrivals_fixture over REC around three F7 signals of paper's
#                 Python run (synthetic arrivals, written_unix, one slot's block row dropped); its snapshot with the seed of
#                 [first - h24, first); m3_11_accept.py arrivals: strict's rules and the seven live columns (spec §5.4).
#   STEP=restart  (lab runner; lang, after paper): spec §5.5 as LAB review reads it: the Python reference on the restart
#                 root against lab-live on the restart root (the M3-09b form: paper.restart_root, segments RESTART_AT ..
#                 RESTART_AT + 2 again after the last of them), with strict's gates and --incomplete. Not compared with
#                 the plain run's journals here (their learned and usage rows differ by the copies' seqs by design).
#
#   STEP=build LAB_DATA_ROOT=/lab/data-old-faithful-one/lab bash lab/live/nas_m3_11.sh      (bookworm runner)
#   STEP=paper LAB_PY=/lab/venv/bin/python bash lab/live/nas_m3_11.sh                      (lab runner; lang)
#   STEP=seed LAB_PY=/lab/venv/bin/python bash lab/live/nas_m3_11.sh                       (lab runner; kort, after paper)
#   STEP=seed-h24 LAB_PY=/lab/venv/bin/python bash lab/live/nas_m3_11.sh                   (lab runner; kort, after seed)
#   STEP=arrivals LAB_PY=/lab/venv/bin/python bash lab/live/nas_m3_11.sh                   (lab runner; kort, after paper)
#   STEP=restart LAB_PY=/lab/venv/bin/python bash lab/live/nas_m3_11.sh                    (lab runner; lang, after paper)
#
# Writes to $DATA/backtests/m3-11-<commit7>/ (job.log, pytest.log, snap/, groups.json, cost_scenario.json, py/, rs1/, rs2/,
# diff/, seed-root/, seed-snap/, seed-*/, seedh-*/, arr-root/, arr-snap/, arr-*/, restart-root/, restart-*/, and each
# step's acceptance log).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
: "${STEP:?STEP=build, paper, seed, seed-h24, arrivals or restart}"
if [ "$STEP" = build ]; then
  STEP=build exec bash "$REPO/lab/live/nas_m1c4.sh"
fi
case "$STEP" in paper | seed | seed-h24 | arrivals | restart) ;; *) echo "unknown STEP=$STEP"; exit 2 ;; esac
OUT=$DATA/backtests/m3-11-${COMMIT:0:7}
mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
log "start $STEP commit=$COMMIT out=$OUT"
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
BIN=$DATA/backtests/live-m1c4-${COMMIT:0:7}/bin/lab-live
# Job 91's recorder directory (#299, 2c3e5fe), as nas_m3_09b.sh reads it.
REC=$DATA/backtests/shadow-arms-1053-1054-2c3e5fe/rec
JOB91_SEGMENTS=3145     # job 91's complete segments (nas_m3_09a.sh)
JOB91_EVENTS=16403207   # LiveFeed's and the follow source's events on them (M3-09b)
RESTART_AT=${RESTART_AT:-2249}
RESTART_N=3
SEED_FROM_SEQ=${SEED_FROM_SEQ:-2249}
STORE=$DATA/store/fwd1053-1054.duckdb
LO=454896000
CONFIG=paper/k3-paper-2026-10-12.yaml
H_ARMS=paper/h-arms-2026-10-16.yaml  # not GROUPS: a bash builtin (the gids), an assignment to it is silently ignored
VARIANTS=0.04:600
ACCEPT=$REPO/lab/live/m3_11_accept.py
[ -x "$BIN" ] || { log "no binary at $BIN: run STEP=build first"; exit 1; }
[ -f "$STORE" ] || { log "no store at $STORE"; exit 1; }
[ -d "$REC/segments" ] || { log "M3-11: RED: no segments/ under $REC"; exit 1; }
count() {  # the complete segments under $1/segments
  grep -l '"complete": *true' "$1"/segments/*/_segment.json 2>/dev/null | wc -l || true
}
complete=$(count "$REC")
[ "$complete" = "$JOB91_SEGMENTS" ] || { log "M3-11: RED: $complete complete segments under $REC/segments, not $JOB91_SEGMENTS"; exit 1; }
log "input: $complete complete segments under $REC/segments; lab-live $(sha256sum "$BIN" | cut -c1-16)"
sha256sum "$REPO/lab/$CONFIG" "$REPO/lab/$H_ARMS" "$REPO/lab/paper/risk.yaml" "$REPO/lab/paper/risk-h.yaml" | tee -a "$OUT/job.log"
export PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1
cd "$REPO/lab"

timed() {  # name, then the command; its output into OUT/name.log, a failure ends the job RED
  local name=$1 t0 rc=0
  shift
  t0=$(date +%s)
  "$@" > "$OUT/$name.log" 2>&1 || rc=$?
  if [ "$rc" != 0 ]; then
    tail -20 "$OUT/$name.log" | tee -a "$OUT/job.log"
    log "$name: FAILED (rc $rc)"
    exit 1
  fi
  log "$name: $(($(date +%s) - t0)) s"
}
runs() {  # prefix, root, segments, snapshot, the first live slot, h24 (- for unset): the reference once, lab-live twice
  local prefix=$1 root=$2 segs=$3 snap=$4 first=$5 h24=$6 h=() hr=()
  if [ "$h24" != - ]; then
    h=(--h24 "$h24")
  fi
  rm -rf "${OUT:?}/${prefix}py" "${OUT:?}/${prefix}rs1" "${OUT:?}/${prefix}rs2" "${OUT:?}/${prefix}diff"
  mkdir -p "$OUT/${prefix}py"
  timed "${prefix}py" "$PY" -m paper.live_reference --snapshot-before "$first" "${h[@]}" --summary "$OUT/${prefix}py/summary.json" -- \
    --live "$root" --catch-up --no-watchlist --no-backup --config "$CONFIG" --groups "$H_ARMS" --store "$STORE" --journal "$OUT/${prefix}py"
  # the groups and the cost scenario as this run journaled them: lab-live's inputs
  "$PY" - "$OUT/${prefix}py" "$OUT/${prefix}" <<'EOF'
import os, sqlite3, sys
db = sqlite3.connect(f"file:{os.path.join(sys.argv[1], 'journal.sqlite')}?mode=ro", uri=True)
for k in ("groups", "cost_scenario"):
    with open(f"{sys.argv[2]}{k}.json", "w") as f:
        f.write(db.execute("SELECT v FROM state WHERE k = ?", [k]).fetchone()[0])
EOF
  for n in 1 2; do
    hr=()
    if [ $n = 1 ]; then
      hr=(--mem-log "$OUT/${prefix}rs1/mem.jsonl")
      mkdir -p "$OUT/${prefix}rs1"
    fi
    timed "${prefix}rs$n" "$BIN" paper --follow "$root" --snapshot "$snap" --snapshot-before "$first" "${h[@]}" \
      --groups "$OUT/${prefix}groups.json" --variants "$VARIANTS" --cost-scenario "$OUT/${prefix}cost_scenario.json" \
      --journal "$OUT/${prefix}rs$n" --summary "$OUT/${prefix}rs$n.json" --expect-segments "$segs" "${hr[@]}"
    cp "$OUT/${prefix}rs$n.json" "$OUT/${prefix}rs$n/summary.json"
  done
}
accept() {  # log name, then m3_11_accept.py's arguments; its verdict into job.log, rc 1 when RED
  local name=$1 rc=0
  shift
  "$PY" "$ACCEPT" "$@" > "$OUT/$name" 2>&1 || rc=$?
  tee -a "$OUT/job.log" < "$OUT/$name"
  [ "$rc" = 0 ] || exit 1
}
seed_snapshot() {  # out, first, h24: the store's snapshot with the seed of [first - h24, first)
  local out=$1 first=$2 h24=$3 lo hi
  read -r lo hi < <("$PY" -c "from paper.snapshot_export import live_seed_window as w; print(*w('$STORE', $first, $h24))")
  [ "$hi" -gt "$lo" ] || { log "M3-11: RED: the seed window [$lo, $hi) at $first holds no slot"; exit 1; }
  rm -rf "${out:?}"
  timed "$(basename "$out")" "$PY" -m paper.snapshot_export --store "$STORE" --out "$out" --seed-from "$lo" --seed-to "$hi"
  log "$(basename "$out"): the seed [$lo, $hi) at $first"
}

if [ "$STEP" = paper ]; then
  rc=0
  "$PY" -m pytest -q -rs paper/tests/test_live_cases.py paper/tests/test_live_reference.py paper/tests/test_arrivals_fixture.py \
    paper/tests/test_lab_live_m3_11_accept.py paper/tests/test_restart_root.py paper/tests/test_journal_diff.py \
    -p no:cacheprovider > "$OUT/pytest.log" 2>&1 || rc=$?
  tail -1 "$OUT/pytest.log" | tee -a "$OUT/job.log"
  [ "$rc" = 0 ] || { log "tests: FAILED (rc $rc)"; exit 1; }
  rm -rf "${OUT:?}/snap"
  timed snap "$PY" -c "from paper import snapshot_export as s; s.export('$STORE', '$OUT/snap', seed=False)"
fi
[ -f "$OUT/snap/meta.json" ] || { log "no snapshot at $OUT/snap: run STEP=paper first"; exit 1; }
H24=$("$PY" -c "from paper.legs_dump import store_h24; print(store_h24('$STORE', $LO))")
log "h24 at $LO from the store's clock: $H24"

if [ "$STEP" = paper ]; then
  runs "" "$REC" "$JOB91_SEGMENTS" "$OUT/snap" $LO "$H24"
  accept acceptance.log strict "$OUT" "" "$JOB91_SEGMENTS" "$JOB91_EVENTS" --incomplete
  # spec §5.7, reported (not gated): the peak RSS and the memory log's end line, its largest structures (the engine's and
  # the follow source's), for the plateau report against v0.7
  "$PY" - "$OUT/rs1.json" "$OUT/rs1/mem.jsonl" <<'EOF' | tee -a "$OUT/job.log"
import json, sys
s = json.load(open(sys.argv[1]))
end = json.loads(open(sys.argv[2]).read().strip().splitlines()[-1])
top = sorted(end["structures"].items(), key=lambda kv: -(kv[1].get("bytes_est") or 0))[:12]
print(f"memory: peak RSS {s['peak_rss_bytes'] >> 20} MB, heap at the end {end['heap_bytes'] >> 20} MB, {s['follow']['keys']:,} keys")
for name, v in top:
    print(f"  {name}: {v.get('entries')} entries, {(v.get('bytes_est') or 0) >> 20} MB")
EOF
  log "done"
  exit 0
fi

[ -f "$OUT/py/summary.json" ] || { log "no reference run at $OUT/py: run STEP=paper first"; exit 1; }

if [ "$STEP" = seed ] || [ "$STEP" = seed-h24 ]; then
  if [ "$STEP" = seed ]; then
    rm -rf "${OUT:?}/seed-root"
    timed seed-root "$PY" -m paper.restart_root --root "$REC" --out "$OUT/seed-root" --from-seq "$SEED_FROM_SEQ"
  fi
  [ -f "$OUT/seed-root.log" ] || { log "no seed root: run STEP=seed first"; exit 1; }
  first=$("$PY" -c "import json, sys; print(json.loads(open(sys.argv[1]).read().strip().splitlines()[-1])['first_slot'])" "$OUT/seed-root.log")
  segs=$(count "$OUT/seed-root")
  h24=$("$PY" -c "from paper.legs_dump import store_h24; print(store_h24('$STORE', $first))")
  if [ "$STEP" = seed ]; then
    seed_snapshot "$OUT/seed-snap" "$first" $((2 * h24))
    log "seed root: $segs complete segments from seq $SEED_FROM_SEQ, first slot $first, h24 $h24"
    runs seed- "$OUT/seed-root" "$segs" "$OUT/seed-snap" "$first" "$h24"
    accept seed-acceptance.log strict "$OUT" seed- "$segs" - --seeded
  else
    [ -f "$OUT/seed-snap/meta.json" ] || { log "no seed snapshot: run STEP=seed first"; exit 1; }
    log "seed root with h24 unset on both sides (the store clock's would be $h24)"
    runs seedh- "$OUT/seed-root" "$segs" "$OUT/seed-snap" "$first" -
    accept seedh-acceptance.log strict "$OUT" seedh- "$segs" - --seeded
    "$PY" -c "import json, sys; print('farmer_seed h24 measured:', json.load(open(sys.argv[1]))['farm_seed']['h24'])" \
      "$OUT/seedh-py/summary.json" | tee -a "$OUT/job.log"
  fi
  log "done"
  exit 0
fi

if [ "$STEP" = arrivals ]; then
  rm -rf "${OUT:?}/arr-root"
  timed arr-root "$PY" -m paper.arrivals_fixture --root "$REC" --journal "$OUT/py" --out "$OUT/arr-root"
  first=$("$PY" -c "import json, sys; print(json.load(open(sys.argv[1]))['first_slot'])" "$OUT/arr-root/fixture.json")
  segs=$(count "$OUT/arr-root")
  h24=$("$PY" -c "from paper.legs_dump import store_h24; print(store_h24('$STORE', $first))")
  seed_snapshot "$OUT/arr-snap" "$first" "$h24"
  log "fixture: $(cat "$OUT/arr-root/fixture.json" | tr -d '\n ')"
  runs arr- "$OUT/arr-root" "$segs" "$OUT/arr-snap" "$first" "$h24"
  accept arrivals-acceptance.log arrivals "$OUT" arr- "$segs" "$OUT/arr-root/fixture.json"
  log "done"
  exit 0
fi

# STEP=restart: the reference and lab-live on the restart root, the same gates (LAB review's reading of spec §5.5)
rm -rf "${OUT:?}/restart-root"
timed restart-root "$PY" -m paper.restart_root --root "$REC" --out "$OUT/restart-root" --at "$RESTART_AT" --n "$RESTART_N"
want=$((JOB91_SEGMENTS + RESTART_N))
complete=$(count "$OUT/restart-root")
[ "$complete" = "$want" ] || { log "M3-11 restart: RED: $complete complete segments in the restart root, not $want"; exit 1; }
runs restart- "$OUT/restart-root" "$want" "$OUT/snap" $LO "$H24"
accept restart-acceptance.log strict "$OUT" restart- "$want" "$JOB91_EVENTS" --incomplete
log "done"
