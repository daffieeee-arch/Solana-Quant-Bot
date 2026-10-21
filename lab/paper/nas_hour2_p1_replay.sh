#!/bin/bash
# Measuring hour 2's P1 effect on the paper run (LAB review and coordinator, 2026-10-21; before P1/P3's deploy). Paper
# only: catch-ups on copies of the live recorder's root, no stream, no keys.
#
#   1. removed: the live root's transactions in hour 2's slots [LO, HI] that arrived there on tx_new_reversed and that
#      the hour-2 root (105140b, learned exclusions) holds on no stream, each classed by its keys on disk against the
#      hour-2 requests as sent (lab/paper/hour2_requests.json, sha pinned; a replay of exclude.rs that reproduces the
#      recorder's 41 notes, 119 request counts and final set): excluded_learned, excluded_static, background, no_keys;
#   2. roots: A = a link copy of the live root's segments FROM_SEQ..TO_SEQ; B = the same with the removed
#      transactions' rows filtered out of every top-level table of the segments that overlap [LO, HI]; C = A again
#      (the control);
#   3. the production engine (BASE_REPO = c1e36a0, or the deploy tag's export when that is the running commit) with the
#      production config, groups and store, cold over each root (paper.engine --live --catch-up --no-backup);
#   4. GREEN only when
#      - the control A against C is equal in paper.journal_diff (the replay is deterministic);
#      - every difference of A against B is explained (paper.hour2_p1 explain): its pool lost only excluded_*
#        transactions, or it follows from such a difference (its own signal; the trigger trader's excluded
#        transactions; a capacity reason after the first one); none before LO;
#      - A holds signals in [LO, HI].
#   PROD_JOURNAL (optional, read-only): the production journal's rows in [LO, HI] next to A's, for the replay's fidelity.
#   INFO next to it (paper.hour2_p1 state): the watchlist log (counts and pool set per minute) and the learned table of
#   A against B, which can differ while no decision does.
#
#   BASE_REPO=/lab/code/repo-c1e36a0 LAB_PY=/lab/venv/bin/python bash lab/paper/nas_hour2_p1_replay.sh      (NAS)
#   BASE_REPO=<git archive c1e36a0 export> bash lab/paper/nas_hour2_p1_replay.sh                           (VPS)
#
# Inputs (defaults: the VPS paths): REC (the live root), MEAS (the hour-2 root), STORE. FROM_SEQ 0 (the live root's
# start, as the paper run), TO_SEQ 2818 (an hour after the window). CONTROL=0 skips the control run (~1/3 less time).
# Writes to $DATA/backtests/hour2-p1-replay-<commit7>/ (job.log, removed/, rec-*/, j-*/, the run logs, diff-*/).
set -euo pipefail
export LC_ALL=C  # the same sort order and tool output on the VPS and in the NAS container (99zzzzi)

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
BASE_SHA=c1e36a03318bd4474f25003492e72d12253b9e7b
DEPLOY_SHA=3a9e9824659e548584421b2726ddd46968ea5399  # deploy-m1cut-2026-10-22, accepted as BASE_REPO when it runs
REQ_SHA=15c181b3fa85e257afd56f7656fe45a48f536360571ab2647c5fd190487d4b88
LO=459708792
HI=459725716
: "${BASE_REPO:?BASE_REPO=an export (or checkout) of the running engine commit}"
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
OUT=$DATA/backtests/hour2-p1-replay-${COMMIT:0:7}
REC=${REC:-$DATA/recorder}
MEAS=${MEAS:-$DATA/recorder-measure-20261021}
STORE=${STORE:-$DATA/store/fwd1053-1054.duckdb}
FROM_SEQ=${FROM_SEQ:-0}
TO_SEQ=${TO_SEQ:-2818}
CONTROL=${CONTROL:-1}
CONFIG=paper/k3-paper-2026-10-12.yaml
H_ARMS=paper/h-arms-2026-10-16.yaml  # not GROUPS: a bash builtin
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
export PYTHONDONTWRITEBYTECODE=1
mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
log "start commit=$COMMIT out=$OUT rec=$REC meas=$MEAS seqs=$FROM_SEQ..$TO_SEQ control=$CONTROL"
for x in removed rec-a rec-b rec-c j-a j-b j-c diff-control diff-p1 diff-state; do
  [ ! -e "$OUT/$x" ] || { log "RED: $OUT/$x exists (a run is made once)"; exit 1; }
done
[ -f "$STORE" ] || { log "RED: no store at $STORE"; exit 1; }
[ -d "$REC/segments" ] && [ -d "$MEAS/segments" ] || { log "RED: no segments/ under $REC or $MEAS"; exit 1; }
have=$(git -C "$BASE_REPO" rev-parse HEAD 2>/dev/null || cat "$BASE_REPO/COMMIT" 2>/dev/null || true)
if [ "${#have}" -lt 7 ] || { [[ "$BASE_SHA" != "$have"* ]] && [[ "$DEPLOY_SHA" != "$have"* ]]; }; then
  log "RED: BASE_REPO $BASE_REPO is at ${have:-no commit}, not c1e36a0 or the deploy tag"
  exit 1
fi
REQ=$REPO/lab/paper/hour2_requests.json
[ "$(sha256sum "$REQ" | cut -c1-64)" = "$REQ_SHA" ] || { log "RED: hour2_requests.json is not the pinned one"; exit 1; }
log "engine $have; requests $REQ_SHA"

timed() {  # name, then the command; its output into OUT/name.log, a failure ends the job RED
  local name=$1 t0 rc=0
  shift
  t0=$(date +%s)
  "$@" > "$OUT/$name.log" 2>&1 || rc=$?
  if [ "$rc" != 0 ]; then
    tail -20 "$OUT/$name.log" | tee -a "$OUT/job.log"
    log "RED: $name failed (rc $rc)"
    exit 1
  fi
  log "$name: $(($(date +%s) - t0)) s"
}
p1() { (cd "$REPO/lab" && PYTHONPATH=$REPO/lab "$PY" -m paper.hour2_p1 "$@"); }

# 1. what P1 took away in hour 2
timed removed p1 removed --live "$REC" --meas "$MEAS" --requests "$REQ" --lo "$LO" --hi "$HI" --out "$OUT/removed"
tail -1 "$OUT/removed.log" | tee -a "$OUT/job.log"
"$PY" -c "import json, sys; c = json.load(open(sys.argv[1]))['completions']; print(f'INFO completions in the window: {c[\"removed\"]} of {c[\"total\"]} removed ({c[\"share_removed\"]:.1%}' + (', OVER 10 %' if c['over_10pct'] else '') + f'); by stream {c[\"by_stream\"]}, removed by stream {c[\"removed_by_stream\"]}')" "$OUT/removed/removed.json" | tee -a "$OUT/job.log"
# 2. the roots
timed rec-a p1 build --live "$REC" --removed none --from-seq "$FROM_SEQ" --to-seq "$TO_SEQ" --lo "$LO" --hi "$HI" --out "$OUT/rec-a"
timed rec-b p1 build --live "$REC" --removed "$OUT/removed/removed.parquet" --from-seq "$FROM_SEQ" --to-seq "$TO_SEQ" \
  --lo "$LO" --hi "$HI" --out "$OUT/rec-b"
tail -1 "$OUT/rec-b.log" | tee -a "$OUT/job.log"
[ "$CONTROL" = 0 ] || cp -a "$OUT/rec-a" "$OUT/rec-c"
first=$("$PY" -c "import json, sys; print(json.loads(open(sys.argv[1]).read().strip().splitlines()[-1])['first_slot'])" "$OUT/rec-a.log")

run() {  # root, journal: paper.engine --live, the snapshot as known before the root's first slot (as the m1cut proof)
  local root=$1 journal=$2
  (cd "$BASE_REPO/lab" && PYTHONPATH=$BASE_REPO/lab "$PY" - "$first" --live "$root" --catch-up --no-backup --config "$CONFIG" \
    --groups "$H_ARMS" --store "$STORE" --journal "$journal" <<'EOF'
import sys

from paper import engine, live

first, base = int(sys.argv[1]), live.LiveFeed


class Feed(base):
    """paper.live_reference's wrap without its --no-watchlist: the snapshot as known before the root's first slot."""

    def __init__(self, *args, **kw):
        kw["snapshot_before"] = first
        super().__init__(*args, **kw)


live.LiveFeed = Feed  # engine.main imports LiveFeed when it is called
engine.main(sys.argv[2:])
EOF
  )
}
# 3. the engine, cold over each root
timed engine-a run "$OUT/rec-a" "$OUT/j-a"
timed engine-b run "$OUT/rec-b" "$OUT/j-b"
[ "$CONTROL" = 0 ] || timed engine-c run "$OUT/rec-c" "$OUT/j-c"

# 4. the comparisons
rc_control=0
if [ "$CONTROL" != 0 ]; then
  (cd "$BASE_REPO/lab" && PYTHONPATH=$BASE_REPO/lab "$PY" -m paper.journal_diff --journal-a "$OUT/j-a" --journal-b "$OUT/j-c" \
    --out "$OUT/diff-control") > "$OUT/diff-control.log" 2>&1 || rc_control=$?
  log "control (A against C): $(sed -n 3p "$OUT/diff-control/journal_diff.md" 2>/dev/null || echo 'no report')"
fi
rc_p1=0
p1 explain --journal-a "$OUT/j-a" --journal-b "$OUT/j-b" --removed "$OUT/removed/removed.parquet" --lo "$LO" --hi "$HI" \
  --journal-diff-path "$BASE_REPO/lab" --out "$OUT/diff-p1" > "$OUT/diff-p1.log" 2>&1 || rc_p1=$?
log "P1 (A against B): $(sed -n 3p "$OUT/diff-p1/explain.md" 2>/dev/null || echo 'no report')"
grep -E '^- (removed transactions|causes)' "$OUT/diff-p1/explain.md" | tee -a "$OUT/job.log" || true
# INFO (LAB review on eaa5626): the engines' own state can differ without a decision differing (the latent cascade)
p1 state --root-a "$OUT/rec-a" --root-b "$OUT/rec-b" --journal-a "$OUT/j-a" --journal-b "$OUT/j-b" --out "$OUT/diff-state" \
  > "$OUT/diff-state.log" 2>&1 || log "state comparison failed (INFO only): $(tail -1 "$OUT/diff-state.log")"
sed 's/^/INFO state /' "$OUT/diff-state.log" | tee -a "$OUT/job.log" || true
n_sig=$("$PY" - "$OUT/j-a" "$LO" "$HI" <<'EOF'
import os, sqlite3, sys
db = sqlite3.connect(f"file:{os.path.join(sys.argv[1], 'journal.sqlite')}?mode=ro", uri=True)
print(db.execute("SELECT count(*) FROM signals WHERE t BETWEEN ? AND ?", (int(sys.argv[2]), int(sys.argv[3]))).fetchone()[0])
EOF
)
log "A: $n_sig signal rows in [$LO, $HI]"
if [ -n "${PROD_JOURNAL:-}" ]; then
  "$PY" - "$PROD_JOURNAL" "$OUT/j-a" "$LO" "$HI" <<'EOF' | tee -a "$OUT/job.log"
import os, sqlite3, sys
lo, hi = int(sys.argv[3]), int(sys.argv[4])
for name, path in (("production", sys.argv[1]), ("A", sys.argv[2])):
    db = sqlite3.connect(f"file:{os.path.join(path, 'journal.sqlite')}?mode=ro", uri=True)
    c = {t: db.execute(f"SELECT count(*) FROM {t} WHERE t BETWEEN ? AND ?", (lo, hi)).fetchone()[0]
         for t in ("signals", "orders", "fills", "exits")}
    s = dict(db.execute("SELECT status, count(*) FROM signals WHERE t BETWEEN ? AND ? GROUP BY 1", (lo, hi)).fetchall())
    print(f"{name} in [{lo}, {hi}]: {c}; signals by status {s}")
EOF
fi
bad=()
[ "$CONTROL" = 0 ] || [ "$rc_control" = 0 ] || bad+=("the control A against C differs (the replay is not deterministic)")
[ "$rc_p1" = 0 ] || bad+=("A against B has unexplained differences (diff-p1/explain.md)")
[ "$n_sig" -gt 0 ] || bad+=("A has no signal in the window (no evidence)")
# LAB review on eaa5626: hour 2 ran without tx_pools (no watchlist), so its learned set is not the one a P1 production
# recorder would learn; B is the counterfactual of hour 2's set, and the day-1 gate (spec §5.3) stays.
log "caveat: B removes what hour 2's recorder (no watchlist, its own learned set) missed; not a production P1 set"
if [ ${#bad[@]} = 0 ]; then
  log "GREEN: every difference P1's hour-2 exclusions make in the paper run is explained"
else
  log "RED: $(printf '%s; ' "${bad[@]}")"
  exit 1
fi
