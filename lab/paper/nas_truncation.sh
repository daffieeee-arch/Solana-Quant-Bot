#!/bin/bash
# The truncation-invariance harness on the NAS (research/truncation-harness-spec v0.2 §4.4, LAB review 2026-10-20;
# paper/truncation.py), the lab Python 3.13 runner. Paper only: a replay of the store, no stream, no keys.
#
#   STEP=harness (lang, ~2-3 h): dev2 with production's burn-in, two windows, three cuts each; per window one full run on
#                [LO, END] and one truncated run per cut, each compared at its cut (GREEN or RED with the reasons, in
#                job.log and w<i>.json). The copies are removed after their run. First the harness's own tests.
#   STEP=parity  (lang, ~1 h; LAB review: the engine change must not move production decisions): paper.engine with its
#                production defaults on one dev2 window, at this commit and at MAIN_REPO (a checkout of main), then
#                paper.journal_diff: GREEN only with every table equal and rows in it.
#
#   STEP=harness LAB_PY=/lab/venv/bin/python bash lab/paper/nas_truncation.sh
#   STEP=parity MAIN_REPO=/lab/code/repo-<main7> LAB_PY=/lab/venv/bin/python bash lab/paper/nas_truncation.sh
#
# Overrides for a dry run (never for the evidence): STORE, WINDOWS ("LO:CUT,CUT,...:END;..."), BURN_IN_MS (harness),
# PARITY_WINDOW ("LO:HI").
# Writes to $DATA/backtests/truncation-<step>-<commit7>/ (job.log, pytest.log, w<i>.json, the journals).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
: "${STEP:?STEP=harness or STEP=parity}"
case "$STEP" in harness | parity) ;; *) echo "unknown STEP=$STEP"; exit 2 ;; esac
OUT=$DATA/backtests/truncation-$STEP-${COMMIT:0:7}
mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
log "start $STEP commit=$COMMIT out=$OUT"
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
STORE=${STORE:-$DATA/store/dev2.duckdb}
[ -f "$STORE" ] || { log "RED: no store at $STORE"; exit 1; }
export PYTHONDONTWRITEBYTECODE=1

if [ "$STEP" = harness ]; then
  export PYTHONPATH=$REPO/lab
  cd "$REPO/lab"
  rc=0
  "$PY" -m pytest -q -p no:cacheprovider paper/tests/test_truncation.py -k "not vps_smoke" > "$OUT/pytest.log" 2>&1 || rc=$?
  tail -1 "$OUT/pytest.log" | tee -a "$OUT/job.log"
  [ "$rc" = 0 ] || { log "RED: the harness's tests failed (rc $rc)"; exit 1; }
  # dev2 (epochs 1033-1046, slots [446256000, 452304000)): an early and a late window of 650,000 slots, the burn-in
  # (~300,000 slots) and three cuts in its decision span; the full run reaches 50,000 slots past the last cut.
  WINDOWS=${WINDOWS:-447000000:447400000,447500000,447600000:447650000;450500000:450900000,451000000,451100000:451150000}
  BURN=${BURN_IN_MS:+--burn-in-ms $BURN_IN_MS}
  i=0
  bad=0
  IFS=";" read -r -a WS <<< "$WINDOWS"
  for w in "${WS[@]}"; do
    i=$((i + 1))
    IFS=":" read -r lo cuts end <<< "$w"
    log "window $i: [$lo, $end], cuts $cuts${BURN:+, $BURN}"
    rc=0
    # shellcheck disable=SC2086
    "$PY" -m paper.truncation --store "$STORE" --work "$OUT/w$i" --from "$lo" --cuts "$cuts" --end "$end" $BURN \
      --out "$OUT/w$i.json" > "$OUT/w$i.log" 2>&1 || rc=$?
    grep -E "^(full|cut |truncation)" "$OUT/w$i.log" | tee -a "$OUT/job.log" || true
    [ "$rc" = 0 ] || bad=$((bad + 1))
  done
  [ "$i" -gt 0 ] || { log "RED: no window"; exit 1; }
  if [ "$bad" = 0 ]; then log "harness: GREEN ($i windows)"; else log "harness: RED ($bad of $i windows)"; exit 1; fi
fi

if [ "$STEP" = parity ]; then
  : "${MAIN_REPO:?MAIN_REPO=a checkout of main (the reference engine)}"
  [ -f "$MAIN_REPO/lab/paper/engine.py" ] || { log "RED: no engine at $MAIN_REPO/lab/paper/engine.py"; exit 1; }
  MAIN_COMMIT=$(git -C "$MAIN_REPO" rev-parse HEAD 2>/dev/null || cat "$MAIN_REPO/COMMIT")
  PARITY_WINDOW=${PARITY_WINDOW:-451000000:451500000}
  IFS=":" read -r lo hi <<< "$PARITY_WINDOW"
  log "parity: paper.engine with production defaults on [$lo, $hi), this commit against main $MAIN_COMMIT"
  for side in head main; do
    src=$REPO
    [ "$side" = main ] && src=$MAIN_REPO
    [ ! -e "$OUT/j_$side" ] || { log "RED: $OUT/j_$side exists"; exit 1; }
    rc=0
    (cd "$src/lab" && PYTHONPATH=$src/lab "$PY" -m paper.engine --store "$STORE" --from "$lo" --to "$hi" --journal "$OUT/j_$side" \
      --ignore-risk --no-backup) > "$OUT/engine-$side.log" 2>&1 || rc=$?
    tail -1 "$OUT/engine-$side.log" | tee -a "$OUT/job.log"
    [ "$rc" = 0 ] || { log "RED: the $side engine failed (rc $rc)"; exit 1; }
  done
  rc=0
  PYTHONPATH=$REPO/lab "$PY" - "$OUT/j_head" "$OUT/j_main" > "$OUT/parity.log" 2>&1 <<'EOF' || rc=$?
import json, sqlite3, sys
from paper.journal_diff import compare


def groups(path):
    """The journal's groups state with each risk file's path from the checkout's lab/ on (the checkout's own path is no
    decision: head and main sit in two directories)."""
    db = sqlite3.connect(f"file:{path}/journal.sqlite?mode=ro", uri=True)
    try:
        g = json.loads(db.execute("SELECT v FROM state WHERE k = 'groups'").fetchone()[0])
    finally:
        db.close()
    for v in g.values():
        if isinstance(v, dict) and isinstance(v.get("risk"), str):
            v["risk"] = v["risk"].rsplit("/lab/", 1)[-1]
    return g


r = compare(sys.argv[1], sys.argv[2])
counts = {t: [v["rows_a"], v["rows_b"]] for t, v in r["tables"].items()}
mismatch = [k for k in r["state_mismatch"] if k != "groups"] + ([] if groups(sys.argv[1]) == groups(sys.argv[2]) else ["groups"])
ok = r["unexplained"] == 0 and not mismatch and not r["schema_diff"] and any(t["rows_a"] for t in r["tables"].values())
print("parity: rows (head, main)", json.dumps(counts))
print(f"parity: unexplained {r['unexplained']}, state mismatch {mismatch} (risk paths taken from lab/), schema diff {bool(r['schema_diff'])}")
print(f"parity: {'GREEN' if ok else 'RED'}")
sys.exit(0 if ok else 1)
EOF
  tee -a "$OUT/job.log" < "$OUT/parity.log"
  [ "$rc" = 0 ] || exit 1
fi
log "done $STEP"
