#!/bin/bash
# M3-09a (research/m3-design-2026-10-19.md): the Python reference of lab-live's live source on the NAS, the lab Python
# 3.13 runner. No Triton stream, no Rust.
#   1. the tests: paper/tests/test_legs_dump.py, and test_live.py's segments-dump test (skipped where the dev fixture's
#      store and chunk are not at their VPS paths);
#   2. `paper.legs_dump --segments` twice on job 91's recording (fwd1053-1054, [LO, HI), the snapshot before LO as
#      paper.shadow's `engine --live` step takes it). GREEN needs two byte-equal summaries that are not empty: events
#      > 0, a sha256 other than the empty input's, and exactly JOB91_SEGMENTS complete segments read. (99zzo on c88c801
#      was a false GREEN: the script gave rec/segments, LiveFeed read <that>/segments, found nothing, and the two empty
#      summaries were byte-equal. legs_dump now refuses that directory, 0 segments and 0 events.)
#   3. the store form over the same slots once, and per window whether its lines equal LiveFeed's (information for
#      M3-09b, no acceptance: LiveFeed differs from the replay by causes paper.shadow names, e.g. late rows).
#
#   LAB_PY=/lab/venv/bin/python bash lab/paper/nas_m3_09a.sh          (lab runner; short: three passes over ~2 days)
#
# The passes run one after another. Each is one Python process, DuckDB at 1 GB (as paper.engine's LiveFeed), and stops
# itself once its peak RSS passes MAX_RSS_GB (default 8; RED in job.log), so it fits beside a long job in the runner.
# Measured on the VPS (dev2, every row, 20,000 slots): ~9,000 events/s, ~0.43 KB of state per event before the
# one-day prunes; job 91's 16.4 M events give an estimate of ~3-5 GB and ~30-50 min for a LiveFeed pass.
# Writes to $DATA/backtests/m3-09a-<commit7>/ (job.log, pytest.log, run1/, run2/, store/: summary.json and run.log).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
OUT=$DATA/backtests/m3-09a-${COMMIT:0:7}
mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
log "start commit=$COMMIT out=$OUT"
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
# Job 91's recorder directory (#299, 2c3e5fe). LiveFeed reads REC/segments; lab-live's --segments (nas_m1c4.sh
# SEGMENTS) takes REC/segments itself.
REC=$DATA/backtests/shadow-arms-1053-1054-2c3e5fe/rec
JOB91_SEGMENTS=3145   # job 91's complete segments: explicit, not derived from the directory (LAB review on 99zzo)
STORE=$DATA/store/fwd1053-1054.duckdb
LO=454896000
HI=455760000
MAX_RSS_GB=${MAX_RSS_GB:-8}
[ -f "$STORE" ] || { log "no store at $STORE"; exit 1; }
# Before any pass: REC/segments holds exactly the expected complete segments (fail-closed at 0 or another number).
[ -d "$REC/segments" ] || { log "M3-09a: RED: no segments/ under $REC"; exit 1; }
complete=$(grep -l '"complete": *true' "$REC"/segments/*/_segment.json 2>/dev/null | wc -l || true)
[ "$complete" = "$JOB91_SEGMENTS" ] || { log "M3-09a: RED: $complete complete segments under $REC/segments, not $JOB91_SEGMENTS"; exit 1; }
log "input: $complete complete segments under $REC/segments"
export PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1
cd "$REPO/lab"

rc=0
"$PY" -m pytest -q -rs paper/tests/test_legs_dump.py paper/tests/test_live.py -k "legs_dump or segments_dump" -p no:cacheprovider \
  > "$OUT/pytest.log" 2>&1 || rc=$?
tail -3 "$OUT/pytest.log" | tee -a "$OUT/job.log"
[ "$rc" = 0 ] || { log "tests: FAILED (rc $rc)"; exit 1; }

run() {  # name, then legs_dump's arguments
  local name=$1 t0 rc=0
  shift
  mkdir -p "$OUT/$name"
  t0=$(date +%s)
  "$PY" -m paper.legs_dump --store "$STORE" --from $LO --to $HI --summary "$OUT/$name/summary.json" --memory 1GB \
    --max-rss-gb "$MAX_RSS_GB" "$@" \
    > "$OUT/$name/run.log" 2>&1 || rc=$?
  [ "$rc" = 0 ] || { tail -20 "$OUT/$name/run.log" | tee -a "$OUT/job.log"; log "$name: FAILED (rc $rc)"; exit 1; }
  log "$name: $(($(date +%s) - t0)) s; $(tail -1 "$OUT/$name/run.log" | cut -c1-240)"   # starts with the peak RSS
}
run run1 --segments "$REC" --snapshot-before $LO --expect-segments "$JOB91_SEGMENTS"
run run2 --segments "$REC" --snapshot-before $LO --expect-segments "$JOB91_SEGMENTS"
# GREEN: byte-equal AND not empty (the degenerate case, two empty summaries, is byte-equal too: RED here).
rc=0
"$PY" - "$OUT" "$JOB91_SEGMENTS" > "$OUT/acceptance.log" 2>&1 <<'EOF' || rc=$?
import hashlib, json, os, sys
out, want = sys.argv[1], int(sys.argv[2])
empty = hashlib.sha256(b"").hexdigest()
texts = [open(os.path.join(out, r, "summary.json"), "rb").read() for r in ("run1", "run2")]
v = json.loads(texts[0])
why = [w for w, bad in (("the summaries differ", texts[0] != texts[1]), ("no event", not v.get("events")),
                        ("the empty input's sha256", v.get("sha256") in (None, empty)), ("no window", not v.get("windows")),
                        (f"{v.get('stats', {}).get('segments')} segments read, not {want}", v.get("stats", {}).get("segments") != want))
       if bad]
if why:
    print("M3-09a: RED: " + "; ".join(why))
    sys.exit(1)
print(f"M3-09a: GREEN: run1 and run2 byte-equal and not empty: {v['events']:,} events in {len(v['windows'])} windows, "
      f"{v['stats']['segments']} of {want} segments read, sha256 {v['sha256'][:16]} (summary {hashlib.sha256(texts[0]).hexdigest()[:16]})")
EOF
tee -a "$OUT/job.log" < "$OUT/acceptance.log"
[ "$rc" = 0 ] || exit 1
run store
rc=0
"$PY" - "$OUT" > "$OUT/compare.log" 2>&1 <<'EOF' || rc=$?
import json, os, sys
out = sys.argv[1]
live = json.load(open(os.path.join(out, "run1", "summary.json")))
store = json.load(open(os.path.join(out, "store", "summary.json")))
lw = {w[0]: w for w in live["windows"]}
sw = {w[0]: w for w in store["windows"]}
same = sum(1 for a in sw if a in lw and lw[a][2:4] == sw[a][2:4])
differ = sorted(a for a in set(lw) | set(sw) if a not in lw or a not in sw or lw[a][2:4] != sw[a][2:4])
print(f"store form vs LiveFeed: events {store['events']} vs {live['events']}, windows equal {same} of {len(set(lw) | set(sw))}"
      f"{', first differing ' + str(differ[:5]) if differ else ''}")
print("LiveFeed: " + json.dumps({k: live[k] for k in ("history", "flag_fixes", "learned", "new_pools", "usage", "arrivals")})[:600])
print("LiveFeed stats: " + json.dumps(live["stats"]))
EOF
tee -a "$OUT/job.log" < "$OUT/compare.log"
[ "$rc" = 0 ] || log "the store-form comparison: FAILED (rc $rc; information only)"
log "done"
