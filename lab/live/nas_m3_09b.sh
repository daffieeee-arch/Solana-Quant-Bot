#!/bin/bash
# M3-09b PR 1 (research/m3-09b-follow-source-spec-2026-10-20.md v0.2 §4): lab-live's follow source (follow.rs) against
# Python LiveFeed on job 91's segments, on the NAS. No Triton stream, no keys.
#
#   STEP=build   (bookworm runner): lab/live/nas_m1c4.sh STEP=build of this commit: cargo test of lab-live and lab-core
#                (the follow source's named tests among those it requires), the release binary into
#                $DATA/backtests/live-m1c4-<commit7>/bin/.
#   STEP=compare (the lab Python 3.13 runner; kort, about an hour: one LiveFeed pass, two lab-live passes):
#                1. the input, hard (spec §4.2, v0.2): exactly JOB91_SEGMENTS complete segments under REC/segments before
#                   any pass, and in every summary JOB91_SEGMENTS read and JOB91_EVENTS events; anything else is RED;
#                2. paper/tests/test_legs_dump.py (legs_dump's clock object);
#                3. the store's snapshot (paper.snapshot_export, no seed) and h24 from the store's clock at LO (legs_dump's
#                   store_h24, as LiveFeed takes it with --snapshot-before);
#                4. `paper.legs_dump --segments` once (LiveFeed, this commit: with the clock object) and `lab-live legs
#                   --follow` twice, the same root and slots;
#                5. GREEN needs: the two lab-live summaries equal (but their own `lab_live` measurements); against LiveFeed
#                   equal events, kinds, each window's count, the learned, new_pools, usage and arrivals digests, the clock,
#                   snapshot_before and h24, and the stats segments, late_rows, duplicate_rows and unknown_pool_rows. #430's
#                   counters (unknown_pools by its rule, which also counts the pools of unknown boost, withdraw and deposit
#                   rows; unknown_pool_other_rows; unknown_mint_rows) equal LiveFeed's where it is #430's, else 99zzt's
#                   (#430, d5750c6, on these segments: 3, 2 and 12,127 = 11,606 + 521); unknown_mints is reported.
#                   Reported, not gated in PR 1: each window's sha256 without the farmer flag (a backfill's rows set the
#                   pool context of the withdraw and deposit legs after them: PR 2 brings the backfill as history), the
#                   sha256 with it (M3-10), history and flag_fixes (PR 2, M3-10), the farm seed (PR 3).
#
#   STEP=build LAB_DATA_ROOT=/lab/data-old-faithful-one/lab bash lab/live/nas_m3_09b.sh      (bookworm runner)
#   STEP=compare LAB_PY=/lab/venv/bin/python bash lab/live/nas_m3_09b.sh                    (lab runner; kort)
#
# Writes to $DATA/backtests/m3-09b-<commit7>/ (job.log, pytest.log, snap/, py/, rs1/, rs2/, acceptance.log).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
: "${STEP:?STEP=build or compare}"
if [ "$STEP" = build ]; then
  STEP=build exec bash "$REPO/lab/live/nas_m1c4.sh"
fi
[ "$STEP" = compare ] || { echo "unknown STEP=$STEP"; exit 2; }
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
STORE=$DATA/store/fwd1053-1054.duckdb
LO=454896000
HI=455760000
MAX_RSS_GB=${MAX_RSS_GB:-8}
[ -x "$BIN" ] || { log "no binary at $BIN: run STEP=build first"; exit 1; }
[ -f "$STORE" ] || { log "no store at $STORE"; exit 1; }
[ -d "$REC/segments" ] || { log "M3-09b: RED: no segments/ under $REC"; exit 1; }
complete=$(grep -l '"complete": *true' "$REC"/segments/*/_segment.json 2>/dev/null | wc -l || true)
[ "$complete" = "$JOB91_SEGMENTS" ] || { log "M3-09b: RED: $complete complete segments under $REC/segments, not $JOB91_SEGMENTS"; exit 1; }
log "input: $complete complete segments under $REC/segments; lab-live $(sha256sum "$BIN" | cut -c1-16)"
export PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1
cd "$REPO/lab"

rc=0
"$PY" -m pytest -q -rs paper/tests/test_legs_dump.py -p no:cacheprovider > "$OUT/pytest.log" 2>&1 || rc=$?
tail -1 "$OUT/pytest.log" | tee -a "$OUT/job.log"
[ "$rc" = 0 ] || { log "tests: FAILED (rc $rc)"; exit 1; }

rm -rf "${OUT:?}/snap"
"$PY" -c "from paper import snapshot_export as s; s.export('$STORE', '$OUT/snap', seed=False)" > "$OUT/snap.log" 2>&1 \
  || { tail -20 "$OUT/snap.log" | tee -a "$OUT/job.log"; log "snapshot export: FAILED"; exit 1; }
H24=$("$PY" -c "from paper.legs_dump import store_h24; print(store_h24('$STORE', $LO))")
log "snapshot exported; h24 at $LO from the store's clock: $H24"

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
pass py "$PY" -m paper.legs_dump --segments "$REC" --store "$STORE" --from $LO --to $HI --snapshot-before $LO \
  --expect-segments "$JOB91_SEGMENTS" --memory 1GB --max-rss-gb "$MAX_RSS_GB"
for k in 1 2; do
  pass "rs$k" "$BIN" legs --follow "$REC" --snapshot "$OUT/snap" --snapshot-before $LO --h24 "$H24" --from $LO --to $HI \
    --expect-segments "$JOB91_SEGMENTS"
done

rc=0
"$PY" - "$OUT" "$JOB91_SEGMENTS" "$JOB91_EVENTS" > "$OUT/acceptance.log" 2>&1 <<'EOF' || rc=$?
import json, os, sys
out, want_segs, want_events = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
load = lambda name: json.load(open(os.path.join(out, name, "summary.json")))
py, rs, rs2 = load("py"), load("rs1"), load("rs2")
red = []
own = lambda v: {k: x for k, x in v.items() if k != "lab_live"}
if own(rs) != own(rs2):
    red.append("the two lab-live runs differ in " + ", ".join(sorted(k for k in set(rs) | set(rs2) if k != "lab_live" and rs.get(k) != rs2.get(k))))
for name, v in (("LiveFeed", py), ("lab-live", rs)):
    if v.get("stats", {}).get("segments") != want_segs:
        red.append(f"{name} read {v.get('stats', {}).get('segments')} segments, not {want_segs}")
    if v.get("events") != want_events:
        red.append(f"{name}: {v.get('events')} events, not {want_events:,}")
for k in ("events", "kinds", "learned", "new_pools", "usage", "arrivals", "clock", "snapshot_before", "h24"):
    if py.get(k) != rs.get(k):
        red.append(f"{k}: LiveFeed {json.dumps(py.get(k))[:300]}, lab-live {json.dumps(rs.get(k))[:300]}")
ps, ls = py["stats"], rs["stats"]
for k in ("segments", "late_rows", "duplicate_rows", "unknown_pool_rows"):
    if ps.get(k) != ls.get(k):
        red.append(f"stats {k}: LiveFeed {ps.get(k)}, lab-live {ls.get(k)}")
# #430's counters: LiveFeed's when it is #430's (main's unknown_pools counts the pools of unknown buys and sells only),
# else 99zzt's (#430, d5750c6, on these segments)
p430 = "unknown_pool_other_rows" in ps
for k, ref99 in (("unknown_pools", 3), ("unknown_pool_other_rows", 2), ("unknown_mint_rows", 12127)):
    ref, src = (ps[k], "LiveFeed") if p430 else (ref99, "99zzt (#430)")
    if ls.get(k) != ref:
        red.append(f"stats {k}: lab-live {ls.get(k)}, {src} {ref}")
pw, rw = ({w[0]: w for w in v["windows"]} for v in (py, rs))
counts = sorted(a for a in set(pw) | set(rw) if a not in pw or a not in rw or pw[a][2] != rw[a][2])
if counts:
    red.append(f"{len(counts)} windows with another count, the first {counts[:5]}")
nf_same = sum(1 for a in pw if a in rw and pw[a][4] == rw[a][4])
nf_differ = sorted(a for a in pw if a in rw and pw[a][4] != rw[a][4])
print(f"information: windows equal without the farmer flag {nf_same} of {len(pw)}"
      + (f"; differing (PR 2's backfill history sets the pool context of withdraw and deposit legs) {nf_differ[:20]}" if nf_differ else ""))
print(f"information: sha256 with the farmer flag {'equal' if py['sha256'] == rs['sha256'] else 'differs'} (M3-10); "
      f"no-farmer total {'equal' if py['sha256_no_farmer'] == rs['sha256_no_farmer'] else 'differs'}; "
      f"LiveFeed history {py.get('history', {}).get('count')} rows and flag_fixes {py.get('flag_fixes', {}).get('count')} (PR 2, M3-10); "
      f"unknown_mints lab-live {ls.get('unknown_mints')}{', LiveFeed ' + str(ps['unknown_mints']) if 'unknown_mints' in ps else ''}")
print("LiveFeed stats: " + json.dumps(ps))
print("lab-live stats: " + json.dumps(ls))
print("clock: " + json.dumps(rs.get("clock")))
secs = {n: open(os.path.join(out, n, "secs")).read().strip() for n in ("py", "rs1", "rs2")}
rss = rs["lab_live"].get("peak_rss_bytes") or 0
measure = (f"{rs['events']:,} events of {ls['segments']} segments; unknown: {ls['unknown_pools']} pools ({ls['unknown_pool_rows']} trade, "
           f"{ls['unknown_pool_other_rows']} other rows), {ls['unknown_mints']} mints ({ls['unknown_mint_rows']} rows); "
           f"windows without the farmer flag {nf_same} of {len(pw)} equal; LiveFeed {secs['py']} s, lab-live {secs['rs1']} s and {secs['rs2']} s, "
           f"peak RSS {rss / 2**20:.0f} MB")
if red:
    print("M3-09b PR 1: RED: " + "; ".join(red))
    print("measure: " + measure)
    sys.exit(1)
print("M3-09b PR 1: GREEN: lab-live's follow source equals LiveFeed (events, kinds, window counts, learned, new_pools, usage, "
      "arrivals, clock, stats); two lab-live runs equal. " + measure)
EOF
tee -a "$OUT/job.log" < "$OUT/acceptance.log"
[ "$rc" = 0 ] || exit 1
log "done"
