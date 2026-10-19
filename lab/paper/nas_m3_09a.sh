#!/bin/bash
# M3-09a (research/m3-design-2026-10-19.md): the Python reference of lab-live's live source on the NAS, the lab Python
# 3.13 runner. No Triton stream, no Rust.
#   1. the tests: paper/tests/test_legs_dump.py, and test_live.py's segments-dump test (skipped where the dev fixture's
#      store and chunk are not at their VPS paths);
#   2. `paper.legs_dump --segments` twice on job 91's segments (fwd1053-1054, [LO, HI), the snapshot before LO as
#      paper.shadow's `engine --live` step takes it): the acceptance is two byte-equal summaries;
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
SEGMENTS=$DATA/backtests/shadow-arms-1053-1054-2c3e5fe/rec/segments  # job 91's (#299, 2c3e5fe), as lab/live/nas_m1c4.sh
STORE=$DATA/store/fwd1053-1054.duckdb
LO=454896000
HI=455760000
MAX_RSS_GB=${MAX_RSS_GB:-8}
[ -d "$SEGMENTS" ] || { log "no segments at $SEGMENTS"; exit 1; }
[ -f "$STORE" ] || { log "no store at $STORE"; exit 1; }
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
run run1 --segments "$SEGMENTS" --snapshot-before $LO
run run2 --segments "$SEGMENTS" --snapshot-before $LO
if cmp -s "$OUT/run1/summary.json" "$OUT/run2/summary.json"; then
  log "M3-09a: the two summaries are byte-equal: GREEN ($(sha256sum "$OUT/run1/summary.json" | cut -c1-16))"
else
  log "M3-09a: the two summaries DIFFER: RED"
  exit 1
fi
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
