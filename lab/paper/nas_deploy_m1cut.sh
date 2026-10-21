#!/bin/bash
# NAS proof for the deploy-only commit deploy-m1cut-2026-10-22 (3a9e982: the paper run c1e36a0 + the engine and test
# lines of #489/#491; coordinator and LAB review, 2026-10-20). The lab Python 3.13 runner (kort, ~45 min). Paper only:
# live catch-ups on link copies of a recorder directory, no stream, no keys.
#
#   1. the code: DEPLOY_REPO is BASE_REPO but for lab/paper/engine.py and lab/paper/tests/test_watchlist_live.py, and
#      their changed lines are exactly #489/#491's (+/- lines sorted in the C locale, sha256 pinned below);
#   2. the deploy commit's tests: test_watchlist_live.py (with dev2's batch measurement: no skip), test_engine.py and
#      test_restart.py;
#   3. link copies of REC from FROM_SEQ on (one copy, then cp -a, so every run reads the same segments) and a part copy
#      up to CUT_SEQ; the engine writes its watchlist log into the root it reads, so each run gets its own;
#   4. plain runs (paper.engine --live ROOT --catch-up, the paper start's config and groups, the snapshot as known before
#      the root's first slot): c1e36a0, the deploy commit without the flag, the deploy commit with --watchlist-no-m1;
#   5. the restart rehearsal, the deploy's own path: c1e36a0 on the part copy (a journal with an HWM), copied twice; then
#      the restart on the full copy by c1e36a0 (the control) and by the deploy commit with the flag;
#   6. GREEN only when
#      - every journal pair (deploy vs c1e36a0, flag vs no flag, restart flag vs restart control) is equal in
#        paper.journal_diff (0 unexplained), in its schema, and in every state key but 'watchlist_rules' (checkout paths
#        taken as <repo>), and both journals hold signals;
#      - the flag's restart record equals c1e36a0's (the control), field by field: hwm, counts and compare_6h; one record
#        each, at the part run's HWM (LAB review on 99zzzzj: c1e36a0's own restart may count differences, e.g.
#        exits_forced_from_journal; the deploy commit must count the same, so counts {} is no criterion here; it stays
#        the day-1 pass of the real deploy);
#      - 'watchlist_rules': none from c1e36a0; m1 true / false from the deploy commit's plain runs (from_hwm_slot null);
#        m1 false with the part run's HWM after the flag's restart;
#      - watchlist logs: the deploy commit's without the flag is c1e36a0's line for line; with the flag (plain and
#        restart) every minute has m1 0, the same f7_rev, tracked, archived, pruned and restored, and the default's list
#        minus exactly its m1 pools; the default's logs hold m1 and f7_rev members.
#
#   BASE_REPO=/lab/code/repo-c1e36a0 DEPLOY_REPO=/lab/code/repo-3a9e982 LAB_PY=/lab/venv/bin/python \
#     bash lab/paper/nas_deploy_m1cut.sh
#
# BASE_REPO and DEPLOY_REPO: exports (or checkouts) of c1e36a0 and of the tag deploy-m1cut-2026-10-22; their commit is
# checked. Overrides for a dry run (never for the evidence): REC, STORE, FROM_SEQ, CUT_SEQ.
# Writes to $DATA/backtests/deploy-m1cut-3a9e982/ (job.log, pytest-*.log, rec-*/, j-*/, the run logs, check.log).
set -euo pipefail
export LC_ALL=C  # sort order and every tool's output the same on the VPS and in the NAS container (99zzzzi)

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
BASE_SHA=c1e36a03318bd4474f25003492e72d12253b9e7b
DEPLOY_SHA=3a9e9824659e548584421b2726ddd46968ea5399
ENGINE_LINES=94227d7fa909dcf52d651722c5fd81913810b1d43e885e3eae63d5dd10ee4f0a   # #489/#491's lines of engine.py (C sort)
TEST_LINES=156fada27cb1253708cb59c1a13577fe6f62f7d3b9153713d64e123fc7586a8c     # ... of test_watchlist_live.py
: "${BASE_REPO:?BASE_REPO=an export of c1e36a0}"
: "${DEPLOY_REPO:?DEPLOY_REPO=an export of deploy-m1cut-2026-10-22}"
OUT=$DATA/backtests/deploy-m1cut-${DEPLOY_SHA:0:7}
mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
log "start proof=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT") out=$OUT"
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
# Job 91's recorder directory (#299, 2c3e5fe), as nas_m3_11.sh reads it; the seed step's start inside the store.
REC=${REC:-$DATA/backtests/shadow-arms-1053-1054-2c3e5fe/rec}
STORE=${STORE:-$DATA/store/fwd1053-1054.duckdb}
FROM_SEQ=${FROM_SEQ:-2249}
CUT_SEQ=${CUT_SEQ:-2849}
CONFIG=paper/k3-paper-2026-10-12.yaml
H_ARMS=paper/h-arms-2026-10-16.yaml  # not GROUPS: a bash builtin
export PYTHONDONTWRITEBYTECODE=1
[ -f "$STORE" ] || { log "RED: no store at $STORE"; exit 1; }
[ -d "$REC/segments" ] || { log "RED: no segments/ under $REC"; exit 1; }
for x in rec-full rec-part rec-base rec-nof rec-flag rec-rbase rec-rflag j-base j-nof j-flag j-first j-rbase j-rflag; do
  [ ! -e "$OUT/$x" ] || { log "RED: $OUT/$x exists (a run is made once)"; exit 1; }
done

# 1. the code
commit_of() { git -C "$1" rev-parse HEAD 2>/dev/null || cat "$1/COMMIT" 2>/dev/null || true; }
for pair in "BASE_REPO $BASE_REPO $BASE_SHA" "DEPLOY_REPO $DEPLOY_REPO $DEPLOY_SHA"; do
  read -r name dir want <<< "$pair"
  have=$(commit_of "$dir")
  if [ "${#have}" -lt 7 ] || [[ "$want" != "$have"* ]]; then
    log "RED: $name $dir is at ${have:-no commit}, not $want"
    exit 1
  fi
done
changed=$(diff -rq --exclude=__pycache__ --exclude=target --exclude=.git "$BASE_REPO/lab" "$DEPLOY_REPO/lab" \
  | sed "s|$BASE_REPO/||; s|$DEPLOY_REPO/||" | sort || true)
want_changed=$(printf '%s\n' "Files lab/paper/engine.py and lab/paper/engine.py differ" \
  "Files lab/paper/tests/test_watchlist_live.py and lab/paper/tests/test_watchlist_live.py differ")
[ "$changed" = "$want_changed" ] || { log "RED: the deploy commit changes more than engine.py and the test:"; echo "$changed" | tee -a "$OUT/job.log"; exit 1; }
lines() { diff -U0 "$BASE_REPO/lab/$1" "$DEPLOY_REPO/lab/$1" | grep -E '^[+-][^+-]|^[+-]$' | LC_ALL=C sort | sha256sum | cut -c1-64; }
[ "$(lines paper/engine.py)" = "$ENGINE_LINES" ] || { log "RED: engine.py's changed lines are not #489/#491's"; exit 1; }
[ "$(lines paper/tests/test_watchlist_live.py)" = "$TEST_LINES" ] || { log "RED: the test's changed lines are not #489/#491's"; exit 1; }
log "code: c1e36a0 + #489/#491's lines only (engine.py, test_watchlist_live.py)"

# 2. the deploy commit's tests
cd "$DEPLOY_REPO/lab"
rc=0
PYTHONPATH=$DEPLOY_REPO/lab "$PY" -m pytest -q -p no:cacheprovider paper/tests/test_watchlist_live.py > "$OUT/pytest-watchlist.log" 2>&1 || rc=$?
tail -1 "$OUT/pytest-watchlist.log" | tee -a "$OUT/job.log"
[ "$rc" = 0 ] || { log "RED: test_watchlist_live failed (rc $rc)"; exit 1; }
if grep -q 'skipped' "$OUT/pytest-watchlist.log"; then
  log "RED: a watchlist test was skipped (the batch measurement needs dev2)"
  exit 1
fi
rc=0
PYTHONPATH=$DEPLOY_REPO/lab "$PY" -m pytest -q -p no:cacheprovider paper/tests/test_engine.py paper/tests/test_restart.py \
  > "$OUT/pytest-engine.log" 2>&1 || rc=$?
tail -1 "$OUT/pytest-engine.log" | tee -a "$OUT/job.log"
[ "$rc" = 0 ] || { log "RED: test_engine / test_restart failed (rc $rc)"; exit 1; }

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

# 3. the link copies (paper.restart_root --from-seq's form; c1e36a0 has no restart_root)
link_root() {  # REC OUT FROM TO: OUT/segments/<name> per segment with FROM <= seq <= TO, links to its entries
  "$PY" - "$@" <<'EOF'
import json, os, sys

rec, out, lo, hi = os.path.realpath(sys.argv[1]), os.path.abspath(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
if os.path.commonpath([rec, out]) == rec:
    raise SystemExit(f"{out} is inside {rec}: refused")
if os.path.exists(out) and os.listdir(out):
    raise SystemExit(f"{out} exists and is not empty: refused")
base, segs = os.path.join(rec, "segments"), []
for name in sorted(os.listdir(base)):
    path = os.path.join(base, name, "_segment.json")
    if name.startswith(".") or not os.path.isfile(path):
        continue
    with open(path) as f:
        meta = json.load(f)
    if lo <= int(meta["seq"]) <= hi:
        segs.append((int(meta["seq"]), name, meta))
segs.sort(key=lambda s: (s[0], s[1]))
if not segs or segs[0][0] != lo or not segs[0][2].get("complete"):
    raise SystemExit(f"segment {lo} is not there and complete: refused")
os.makedirs(os.path.join(out, "segments"))
for seq, name, meta in segs:
    src, dst = os.path.join(base, name), os.path.join(out, "segments", name)
    os.mkdir(dst)
    for entry in sorted(os.listdir(src)):
        if entry != "_segment.json":
            os.symlink(os.path.join(src, entry), os.path.join(dst, entry))
    with open(os.path.join(dst, "_segment.json"), "w") as f:
        json.dump(meta, f)
done = [s for s in segs if s[2].get("complete")]
print(json.dumps({"segments": len(segs), "complete": len(done), "first_slot": int(segs[0][2]["first_slot"]),
                  "last_complete_seq": done[-1][0]}))
EOF
}
field() { "$PY" -c "import json, sys; print(json.loads(open(sys.argv[1]).read().strip().splitlines()[-1])[sys.argv[2]])" "$@"; }
timed rec-full link_root "$REC" "$OUT/rec-full" "$FROM_SEQ" 999999999
timed rec-part link_root "$REC" "$OUT/rec-part" "$FROM_SEQ" "$CUT_SEQ"
first=$(field "$OUT/rec-full.log" first_slot)
full_last=$(field "$OUT/rec-full.log" last_complete_seq)
part_last=$(field "$OUT/rec-part.log" last_complete_seq)
[ "$part_last" = "$CUT_SEQ" ] && [ "$full_last" -gt $((CUT_SEQ + 30)) ] || {
  log "RED: the part copy must end complete at $CUT_SEQ and the full one well after it (part $part_last, full $full_last)"; exit 1; }
for x in base nof flag rbase rflag; do
  cp -a "$OUT/rec-full" "$OUT/rec-$x"
done
log "roots: $REC from seq $FROM_SEQ (first slot $first) to $full_last; the part copy to $CUT_SEQ"

run() {  # name, checkout, root, journal, extra engine flags: paper.engine --live, the snapshot before the root's first slot
  local src=$2 root=$3 journal=$4
  shift 4
  (cd "$src/lab" && PYTHONPATH=$src/lab "$PY" - "$first" --live "$root" --catch-up --no-backup --config "$CONFIG" \
    --groups "$H_ARMS" --store "$STORE" --journal "$journal" "$@" <<'EOF'
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
# 4. plain runs
timed engine-base run base "$BASE_REPO" "$OUT/rec-base" "$OUT/j-base"
timed engine-nof run nof "$DEPLOY_REPO" "$OUT/rec-nof" "$OUT/j-nof"
timed engine-flag run flag "$DEPLOY_REPO" "$OUT/rec-flag" "$OUT/j-flag" --watchlist-no-m1
# 5. the restart rehearsal
timed engine-first run first "$BASE_REPO" "$OUT/rec-part" "$OUT/j-first"
cp -a "$OUT/j-first" "$OUT/j-rbase"
cp -a "$OUT/j-first" "$OUT/j-rflag"
timed engine-rbase run rbase "$BASE_REPO" "$OUT/rec-rbase" "$OUT/j-rbase"
timed engine-rflag run rflag "$DEPLOY_REPO" "$OUT/rec-rflag" "$OUT/j-rflag" --watchlist-no-m1
for x in base nof flag first rbase rflag; do
  grep -E '^restart:' "$OUT/engine-$x.log" | tee -a "$OUT/job.log" || true
  tail -1 "$OUT/engine-$x.log" | tee -a "$OUT/job.log"
done

# 6. the checks
rc=0
PYTHONPATH=$DEPLOY_REPO/lab "$PY" - "$OUT" "$BASE_REPO" "$DEPLOY_REPO" > "$OUT/check.log" 2>&1 <<'EOF' || rc=$?
import json, os, sqlite3, sys
from paper.journal_diff import compare

out, repos = sys.argv[1], [os.path.realpath(p) for p in sys.argv[2:4]]
bad = []


def db(side):
    return sqlite3.connect(f"file:{os.path.join(out, 'j-' + side)}/journal.sqlite?mode=ro", uri=True)


def state(side):
    """Every state key but watchlist_rules, the checkouts' paths as <repo>."""
    con = db(side)
    try:
        rows = con.execute("SELECT k, v FROM state WHERE k != 'watchlist_rules'").fetchall()
    finally:
        con.close()
    res = {}
    for k, v in rows:
        for r in repos:
            v = v.replace(r, "<repo>") if isinstance(v, str) else v
        res[k] = v
    return res


def schema(side):
    con = db(side)
    try:
        return sorted(con.execute("SELECT type, name, sql FROM sqlite_master").fetchall(), key=str)
    finally:
        con.close()


def rules(side):
    con = db(side)
    try:
        r = con.execute("SELECT v FROM state WHERE k = 'watchlist_rules'").fetchone()
    finally:
        con.close()
    return [{k: e[k] for k in ("from_hwm_slot", "watchlist", "m1")} for e in json.loads(r[0])] if r else None


for a, b in (("nof", "base"), ("flag", "nof"), ("rflag", "rbase")):
    r = compare(os.path.join(out, f"j-{a}"), os.path.join(out, f"j-{b}"))
    with open(os.path.join(out, f"diff-{a}-{b}.json"), "w") as f:
        json.dump(r, f, indent=1, default=str)
    sa, sb = state(a), state(b)
    keys = sorted(k for k in set(sa) | set(sb) if sa.get(k) != sb.get(k))
    rows = {t: [v["rows_a"], v["rows_b"]] for t, v in r["tables"].items()}
    sig = r["tables"]["signals"]
    print(f"journals {a} vs {b}: rows {json.dumps(rows)}; unexplained {r['unexplained']}, state keys that differ "
          f"(but watchlist_rules) {keys}, schema equal {schema(a) == schema(b)}")
    if r["unexplained"] or keys or schema(a) != schema(b) or not (sig["rows_a"] and sig["rows_b"]):
        bad.append(f"journals {a} vs {b}")

con = db("first")
hwm = json.loads(con.execute("SELECT v FROM state WHERE k = 'hwm'").fetchone()[0])["slot"]
con.close()
records = {}
for side in ("rbase", "rflag"):
    con = db(side)
    raw = con.execute("SELECT v FROM state WHERE k = 'restarts'").fetchone()
    con.close()
    records[side] = json.loads(raw[0]) if raw else []
    rec = records[side]
    print(f"restart {side}: {len(rec)} record(s)" + (f", the last {json.dumps(rec[-1], sort_keys=True)}" if rec else ""))
    if len(rec) != 1 or rec[0].get("hwm") != hwm:
        bad.append(f"the {side} restart is not one record at the part run's HWM {hwm}")
if len(records["rbase"]) == 1 and len(records["rflag"]) == 1:
    # LAB review on 99zzzzj: the flag's restart record is the control's, field by field (not counts {})
    differ = [f for f in ("hwm", "counts", "compare_6h") if records["rflag"][0].get(f) != records["rbase"][0].get(f)]
    print(f"restart records rflag vs rbase (hwm, counts, compare_6h): {'equal' if not differ else 'differ in ' + ', '.join(differ)}")
    if differ:
        bad.append(f"the flag's restart record differs from c1e36a0's in {differ}")
want = {"base": None, "first": None, "rbase": None,
        "nof": [{"from_hwm_slot": None, "watchlist": True, "m1": True}],
        "flag": [{"from_hwm_slot": None, "watchlist": True, "m1": False}],
        "rflag": [{"from_hwm_slot": hwm, "watchlist": True, "m1": False}]}
for side, w in want.items():
    have = rules(side)
    print(f"watchlist_rules {side}: {have}")
    if have != w:
        bad.append(f"watchlist_rules of {side}: {have}, not {w}")


def wl(side):
    with open(os.path.join(out, f"rec-{side}", "watchlist-log.jsonl")) as f:
        return [json.loads(x) for x in f if x.strip()]


def without_m1(default, flag, name):
    """flag's log is default's minus its m1 pools, minute for minute; default holds m1 and f7_rev members."""
    print(f"watchlist {name}: {len(default)} minutes; default m1 {sum(x['counts']['m1'] for x in default)}, f7_rev "
          f"{sum(x['counts']['f7_rev'] for x in default)}; flag m1 {sum(x['counts']['m1'] for x in flag)}, f7_rev "
          f"{sum(x['counts']['f7_rev'] for x in flag)}")
    if not default or not sum(x["counts"]["m1"] for x in default) or not sum(x["counts"]["f7_rev"] for x in default):
        bad.append(f"{name}: the default's log holds no m1 or no f7_rev member: the input proves nothing")
    if [(x["minute"], x["slot"]) for x in flag] != [(x["minute"], x["slot"]) for x in default]:
        bad.append(f"{name}: the flag's log has other minutes than the default's")
        return
    wrong = []
    for x, y in zip(default, flag):
        cx, cy = x["counts"], y["counts"]
        same = {k: v for k, v in cx.items() if k not in ("m1", "pools")} == {k: v for k, v in cy.items() if k not in ("m1", "pools")}
        gone = set(x["pools"]) - set(y["pools"])
        if not (cy["m1"] == 0 and same and set(y["pools"]) <= set(x["pools"]) and len(gone) == cx["m1"]
                and cy["pools"] == cx["pools"] - cx["m1"] == len(y["pools"])):
            wrong.append(x["minute"])
    if wrong:
        bad.append(f"{name}: {len(wrong)} minutes where the flag's list is not the default's minus its m1 pools (first {wrong[:3]})")


base, nof = wl("base"), wl("nof")
if nof != base:
    bad.append(f"the deploy commit's log without the flag differs from c1e36a0's ({len(nof)} vs {len(base)} lines)")
without_m1(nof, wl("flag"), "plain")
without_m1(wl("rbase"), wl("rflag"), "restart")
print("deploy-m1cut: " + ("GREEN" if not bad else "RED: " + "; ".join(bad)))
sys.exit(1 if bad else 0)
EOF
tee -a "$OUT/job.log" < "$OUT/check.log"
[ "$rc" = 0 ] || exit 1
log "done"
