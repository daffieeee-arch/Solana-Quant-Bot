#!/bin/bash
# --watchlist-no-m1 (paper.engine watchlist_rules) on the NAS, the lab Python 3.13 runner (kort). Paper only: a live
# catch-up on copies of job 91's recorder directory, no stream, no keys.
#
#   1. the tests: paper/tests/test_watchlist_live.py (with the batch measurement on dev2) and test_engine.py;
#   2. three link copies of job 91's root from FROM_SEQ on (paper.restart_root --from-seq): the engine writes its
#      watchlist-log.jsonl into the root it reads, so each run gets its own;
#   3. paper.engine --live ROOT --catch-up with the paper start's config and groups, the snapshot as known before the
#      root's first slot (live_reference's LiveFeed wrap, but with the watchlist on): main (MAIN_REPO) and this commit
#      without the flag, and this commit with --watchlist-no-m1;
#   4. GREEN only when
#      - the journals of all three are equal (paper.journal_diff; the risk paths in state 'groups' taken from lab/ on,
#        as main sits in another checkout): the watchlist decides no trade;
#      - this commit's watchlist log without the flag is main's, line for line (the default is unchanged);
#      - with the flag, per minute: no m1 pool, the same f7_rev count, tracked, archived, pruned and restored, and the
#        pools on the list are the default's minus exactly its m1 pools (m1 is sol/normal, f7_rev reversed: no pool
#        has both tags);
#      - the input means something: m1 and f7_rev members in the default's log.
#   The daily reports are not compared: their latency lines are measured per run.
#
#   MAIN_REPO=/lab/code/repo-<main7> LAB_PY=/lab/venv/bin/python bash lab/paper/nas_watchlist_no_m1.sh
#
# Overrides for a dry run (never for the evidence): REC, STORE, FROM_SEQ.
# Writes to $DATA/backtests/watchlist-no-m1-<commit7>/ (job.log, pytest.log, rec-*/, j-*/, the run logs, diff-*.json).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
: "${MAIN_REPO:?MAIN_REPO=a checkout of main (the reference engine)}"
OUT=$DATA/backtests/watchlist-no-m1-${COMMIT:0:7}
mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
log "start commit=$COMMIT out=$OUT"
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
# Job 91's recorder directory (#299, 2c3e5fe), as nas_m3_11.sh reads it; the seed step's start inside the store.
REC=${REC:-$DATA/backtests/shadow-arms-1053-1054-2c3e5fe/rec}
STORE=${STORE:-$DATA/store/fwd1053-1054.duckdb}
FROM_SEQ=${FROM_SEQ:-2249}
CONFIG=paper/k3-paper-2026-10-12.yaml
H_ARMS=paper/h-arms-2026-10-16.yaml  # not GROUPS: a bash builtin
export PYTHONDONTWRITEBYTECODE=1
[ -f "$MAIN_REPO/lab/paper/engine.py" ] || { log "RED: no engine at $MAIN_REPO/lab/paper/engine.py"; exit 1; }
MAIN_COMMIT=$(git -C "$MAIN_REPO" rev-parse HEAD 2>/dev/null || cat "$MAIN_REPO/COMMIT")
[ -f "$STORE" ] || { log "RED: no store at $STORE"; exit 1; }
[ -d "$REC/segments" ] || { log "RED: no segments/ under $REC"; exit 1; }
for x in rec-main rec-head rec-nom1 j-main j-head j-nom1; do
  [ ! -e "$OUT/$x" ] || { log "RED: $OUT/$x exists (a run is made once)"; exit 1; }
done

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

cd "$REPO/lab"
rc=0
PYTHONPATH=$REPO/lab "$PY" -m pytest -q -p no:cacheprovider paper/tests/test_watchlist_live.py paper/tests/test_engine.py \
  > "$OUT/pytest.log" 2>&1 || rc=$?
tail -1 "$OUT/pytest.log" | tee -a "$OUT/job.log"
[ "$rc" = 0 ] || { log "RED: the tests failed (rc $rc)"; exit 1; }
if grep -q 'skipped' "$OUT/pytest.log"; then
  log "RED: a test was skipped (the batch measurement needs dev2)"
  exit 1
fi

for side in main head nom1; do
  timed "rec-$side" "$PY" -m paper.restart_root --root "$REC" --out "$OUT/rec-$side" --from-seq "$FROM_SEQ"
done
first=$("$PY" -c "import json, sys; print(json.loads(open(sys.argv[1]).read().strip().splitlines()[-1])['first_slot'])" "$OUT/rec-head.log")
log "roots: job 91 from seq $FROM_SEQ, first slot $first; main $MAIN_COMMIT"

run() {  # side, checkout, extra engine flags: paper.engine --live on OUT/rec-<side>, the snapshot before its first slot
  local side=$1 src=$2
  shift 2
  (cd "$src/lab" && PYTHONPATH=$src/lab "$PY" - "$first" --live "$OUT/rec-$side" --catch-up --no-backup --config "$CONFIG" \
    --groups "$H_ARMS" --store "$STORE" --journal "$OUT/j-$side" "$@" <<'EOF'
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
timed "engine-main" run main "$MAIN_REPO"
timed "engine-head" run head "$REPO"
timed "engine-nom1" run nom1 "$REPO" --watchlist-no-m1
for side in main head nom1; do
  tail -1 "$OUT/engine-$side.log" | tee -a "$OUT/job.log"
done

rc=0
PYTHONPATH=$REPO/lab "$PY" - "$OUT" > "$OUT/check.log" 2>&1 <<'EOF' || rc=$?
import json, os, sqlite3, sys
from paper.journal_diff import compare

out = sys.argv[1]


def groups(path):
    """The journal's groups state with each risk file's path from the checkout's lab/ on (main sits elsewhere)."""
    db = sqlite3.connect(f"file:{path}/journal.sqlite?mode=ro", uri=True)
    try:
        g = json.loads(db.execute("SELECT v FROM state WHERE k = 'groups'").fetchone()[0])
    finally:
        db.close()
    for v in g.values():
        if isinstance(v, dict) and isinstance(v.get("risk"), str):
            v["risk"] = v["risk"].rsplit("/lab/", 1)[-1]
    return g


bad = []
for a, b in (("head", "main"), ("nom1", "head")):
    ja, jb = os.path.join(out, f"j-{a}"), os.path.join(out, f"j-{b}")
    r = compare(ja, jb)
    with open(os.path.join(out, f"diff-{a}-{b}.json"), "w") as f:
        json.dump(r, f, indent=1, default=str)
    mismatch = [k for k in r["state_mismatch"] if k != "groups"] + ([] if groups(ja) == groups(jb) else ["groups"])
    rows = {t: [v["rows_a"], v["rows_b"]] for t, v in r["tables"].items()}
    print(f"journals {a} vs {b}: rows {json.dumps(rows)}; unexplained {r['unexplained']}, state mismatch {mismatch}, "
          f"schema diff {bool(r['schema_diff'])}")
    if r["unexplained"] or mismatch or r["schema_diff"] or not any(v["rows_a"] for v in r["tables"].values()):
        bad.append(f"journals {a} vs {b}")


def wl(side):
    with open(os.path.join(out, f"rec-{side}", "watchlist-log.jsonl")) as f:
        return [json.loads(x) for x in f if x.strip()]


m, h, n = wl("main"), wl("head"), wl("nom1")
print(f"watchlist log: {len(h)} minutes; default's minute-members m1 {sum(x['counts']['m1'] for x in h)}, "
      f"f7_rev {sum(x['counts']['f7_rev'] for x in h)}; with the flag m1 {sum(x['counts']['m1'] for x in n)}, "
      f"f7_rev {sum(x['counts']['f7_rev'] for x in n)}")
if h != m:
    bad.append(f"the default's watchlist log differs from main's ({len(h)} vs {len(m)} lines, first at "
               f"{next((i for i, (x, y) in enumerate(zip(h, m)) if x != y), min(len(h), len(m)))})")
if not h or not sum(x["counts"]["m1"] for x in h) or not sum(x["counts"]["f7_rev"] for x in h):
    bad.append("the default's log holds no m1 or no f7_rev member: the input proves nothing")
if [(x["minute"], x["slot"]) for x in n] != [(x["minute"], x["slot"]) for x in h]:
    bad.append("the flag's log has other minutes than the default's")
else:
    wrong = []
    for x, y in zip(h, n):
        cx, cy = x["counts"], y["counts"]
        same = {k: v for k, v in cx.items() if k not in ("m1", "pools")} == {k: v for k, v in cy.items() if k not in ("m1", "pools")}
        gone = set(x["pools"]) - set(y["pools"])
        if not (cy["m1"] == 0 and same and set(y["pools"]) <= set(x["pools"]) and len(gone) == cx["m1"]
                and cy["pools"] == cx["pools"] - cx["m1"] == len(y["pools"])):
            wrong.append(x["minute"])
    if wrong:
        bad.append(f"{len(wrong)} minutes where the flag's list is not the default's minus its m1 pools (first {wrong[:3]})")
print("watchlist-no-m1: " + ("GREEN" if not bad else "RED: " + "; ".join(bad)))
sys.exit(1 if bad else 0)
EOF
tee -a "$OUT/job.log" < "$OUT/check.log"
[ "$rc" = 0 ] || exit 1
log "done"
