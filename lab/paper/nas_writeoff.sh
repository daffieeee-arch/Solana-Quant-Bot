#!/bin/bash
# The write-off's proof on the NAS (engine._write_off_if_stuck; owner, 2026-10-23: a position whose exit can no longer
# fill is written off at its rule's deadline), the lab Python 3.13 runner (lang, ~3-4 h est.). Paper only: replays of the
# dev2 store, no stream, no keys. Every engine run with --no-backup.
#
#   STEP=python LAB_PY=/lab/venv/bin/python MAIN_REPO=/lab/code/repo-<main7> bash lab/paper/nas_writeoff.sh
#
#   0. the code: against MAIN_REPO (main at this branch's base) only lab/paper files changed (no lab/backtest file),
#      and the configs (k3-paper, risk.yaml, risk-h.yaml, h-arms) byte-equal;
#   1. the tests: test_writeoff, the engine's, the restarts', the golden cases', risk, journal_diff, parity, the
#      backtest's stream end, then the NAS tests (LAB_NAS_TESTS=1: test_restart_replay, test_arms_replay); no skip;
#   2. strict parity: paper.engine's default grid with --ignore-risk on W1 and W2, main against this commit;
#      paper.journal_diff with the write-off's causes (written_off, busy_freed): GREEN with 0 unexplained, the same
#      schema and rows; the written-off rows counted against main's unsold ones;
#   3. risk on (the production config: k3-paper + h-arms) on W1 and W2, main against this commit: per group every row
#      decided before the group's first write-off is the same (RED otherwise); after it the cascade (freed places,
#      earlier day losses) is counted, not judged;
#   4. the H arms against the backtest (paper.parity --arm) on W1 with main's engine and this commit's: this commit
#      has no more unexplained differences than main; its write-offs are explained (written_off).
#
# W1 = [450144000, 450360000) (the U window of nas_hwm.sh), W2 = [451440000, 451656000) (epoch 1045). Overrides for a
# dry run (never for the evidence): STORE, W1, W2, SKIP_NAS_TESTS=1.
# Writes $DATA/backtests/writeoff-<commit7>/ (job.log, pytest.log, the journals, the diffs, arm-*/parity.json).
set -euo pipefail
export LC_ALL=C  # the same sort order and tool output on the VPS and in the NAS container

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
: "${STEP:?STEP=python}"
[ "$STEP" = python ] || { echo "unknown STEP=$STEP"; exit 2; }
: "${MAIN_REPO:?MAIN_REPO=a checkout of main at the base of this branch (the reference engine)}"
OUT=$DATA/backtests/writeoff-${COMMIT:0:7}
mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
log "start $STEP commit=$COMMIT main=$MAIN_REPO out=$OUT"
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
STORE=${STORE:-$DATA/store/dev2.duckdb}
W1=${W1:-450144000:450360000}
W2=${W2:-451440000:451656000}
[ -f "$STORE" ] || { log "RED: no store at $STORE"; exit 1; }
[ -f "$MAIN_REPO/lab/paper/engine.py" ] || { log "RED: no engine at $MAIN_REPO/lab/paper/engine.py"; exit 1; }
export PYTHONDONTWRITEBYTECODE=1 LAB_STORE=$STORE
CONFIGS=(lab/paper/k3-paper-2026-10-12.yaml lab/paper/risk.yaml lab/paper/risk-h.yaml lab/paper/h-arms-2026-10-16.yaml)

# 0. the code
rc=0
"$PY" - "$REPO" "$MAIN_REPO" "${CONFIGS[@]}" >> "$OUT/job.log" 2>&1 <<'EOF' || rc=$?
import filecmp, hashlib, os, sys
repo, main, configs = sys.argv[1], sys.argv[2], sys.argv[3:]
bad = []
for c in configs:
    a, b = os.path.join(repo, c), os.path.join(main, c)
    sha = hashlib.sha256(open(a, "rb").read()).hexdigest()[:16]
    print(f"{c}: sha256 {sha}… {'equal to main' if filecmp.cmp(a, b, shallow=False) else 'DIFFERENT from main'}")
    if not filecmp.cmp(a, b, shallow=False):
        bad.append(c)
changed = []
for root in ("lab/backtest", "lab/core/src", "lab/live/src"):
    for d, _, fs in os.walk(os.path.join(repo, root)):
        for f in fs:
            if f.endswith((".py", ".rs")):
                rel = os.path.relpath(os.path.join(d, f), repo)
                other = os.path.join(main, rel)
                if not os.path.exists(other) or not filecmp.cmp(os.path.join(repo, rel), other, shallow=False):
                    changed.append(rel)
print("lab/backtest, lab/core/src, lab/live/src files that differ from main:", changed or "none")
bad += [c for c in changed if c.startswith("lab/backtest/")]
sys.exit(f"RED: {bad}" if bad else 0)
EOF
[ "$rc" = 0 ] || { log "RED: the code check (job.log)"; exit 1; }
log "code: the configs equal to main, lab/backtest unchanged"

# 1. the tests
cd "$REPO/lab"
NAS_TESTS="paper/tests/test_restart_replay.py paper/tests/test_arms_replay.py"
[ -z "${SKIP_NAS_TESTS:-}" ] || { NAS_TESTS=""; log "DRY RUN: SKIP_NAS_TESTS (not evidence)"; }
rc=0
# shellcheck disable=SC2086
LAB_NAS_TESTS=1 PYTHONPATH=$REPO/lab "$PY" -m pytest -q -rs -p no:cacheprovider paper/tests/test_writeoff.py \
  paper/tests/test_engine.py paper/tests/test_restart.py paper/tests/test_position_cases.py paper/tests/test_risk_cases.py \
  paper/tests/test_risk_load_cases.py paper/tests/test_journal_diff.py paper/tests/test_parity.py \
  backtest/tests/test_stream_end.py $NAS_TESTS > "$OUT/pytest.log" 2>&1 || rc=$?
tail -1 "$OUT/pytest.log" | tee -a "$OUT/job.log"
grep -q "skipped" "$OUT/pytest.log" && [ -z "${SKIP_NAS_TESTS:-}" ] && { log "RED: a test was skipped (pytest.log)"; exit 1; }
[ "$rc" = 0 ] || { log "RED: tests failed (rc $rc)"; exit 1; }

engine() {  # side (head|main), journal dir, window lo:hi, extra args...
  local side=$1 j=$2 w=$3; shift 3
  local src=$REPO lo hi
  [ "$side" = main ] && src=$MAIN_REPO
  IFS=":" read -r lo hi <<< "$w"
  [ ! -e "$j" ] || { log "RED: $j exists"; exit 1; }
  rc=0
  (cd "$src/lab" && PYTHONPATH=. "$PY" -m paper.engine --store "$STORE" --from "$lo" --to "$hi" --journal "$j" --no-backup "$@") \
    > "$j.log" 2>&1 || rc=$?
  log "engine $side $(basename "$j"): rc $rc, $(tail -1 "$j.log")"
  [ "$rc" = 0 ] || { tail -20 "$j.log" | tee -a "$OUT/job.log"; log "RED: the $side engine failed"; exit 1; }
}

# 2. strict parity (risk off)
bad=0
for w in W1 W2; do
  win=${!w}
  engine main "$OUT/strict-$w-main" "$win" --ignore-risk
  engine head "$OUT/strict-$w-head" "$win" --ignore-risk
  rc=0
  PYTHONPATH=$REPO/lab "$PY" - "$OUT/strict-$w-main" "$OUT/strict-$w-head" > "$OUT/strict-$w.log" 2>&1 <<'EOF' || rc=$?
import json, sqlite3, sys
from paper.journal_diff import compare, writeoff_causes
a, b = sys.argv[1:3]


def groups(path):
    """The journal's groups state with each risk file's path from the checkout's lab/ on (head and main sit in two
    directories; nas_truncation.sh's rule)."""
    db = sqlite3.connect(f"file:{path}/journal.sqlite?mode=ro", uri=True)
    try:
        g = json.loads(db.execute("SELECT v FROM state WHERE k = 'groups'").fetchone()[0])
    finally:
        db.close()
    for v in g.values():
        if isinstance(v, dict) and isinstance(v.get("risk"), str):
            v["risk"] = v["risk"].rsplit("/lab/", 1)[-1]
    return g


r = compare(a, b, causes=writeoff_causes(a, b))
mismatch = [k for k in r["state_mismatch"] if k != "groups"] + ([] if groups(a) == groups(b) else ["groups"])
q = lambda p, sql: sqlite3.connect(f"file:{p}/journal.sqlite?mode=ro", uri=True).execute(sql).fetchone()[0]  # noqa: E731
n_wo = q(b, "SELECT count(*) FROM exits WHERE exit_reason = 'written_off'")
n_unsold = q(a, "SELECT count(*) FROM exits WHERE tokens > 0 AND exit_slot IS NULL AND proceeds = 0")
counts = {t: [v["rows_a"], v["rows_b"]] for t, v in r["tables"].items()}
print(f"rows (main, head) {counts}")
print(f"written_off in head {n_wo}; main's unsold positions (exit_slot NULL, proceeds 0) {n_unsold}; explained {r['explained']}")
for t, v in r["tables"].items():
    if v["unexplained"]:
        print(f"  {t}: only main {v['only_a_first'][:3]}, only head {v['only_b_first'][:3]}, different {v['different_first'][:3]}")
ok = r["unexplained"] == 0 and not r["schema_diff"] and not mismatch and n_wo <= n_unsold and any(v["rows_a"] for v in r["tables"].values())
print(f"strict: unexplained {r['unexplained']}, schema diff {bool(r['schema_diff'])}, state mismatch {mismatch} (risk paths from lab/): {'GREEN' if ok else 'RED'}")
sys.exit(0 if ok else 1)
EOF
  tee -a "$OUT/job.log" < "$OUT/strict-$w.log"
  [ "$rc" = 0 ] || bad=1
done
[ "$bad" = 0 ] || { log "RED: strict parity"; exit 1; }

# 3. risk on: identical up to each group's first write-off
for w in W1 W2; do
  win=${!w}
  for side in main head; do
    engine "$side" "$OUT/risk-$w-$side" "$win" --config "$REPO/lab/paper/k3-paper-2026-10-12.yaml" --groups "$REPO/lab/paper/h-arms-2026-10-16.yaml"
  done
  rc=0
  PYTHONPATH=$REPO/lab "$PY" - "$OUT/risk-$w-main" "$OUT/risk-$w-head" > "$OUT/risk-$w.log" 2>&1 <<'EOF' || rc=$?
import sqlite3, sys
from paper.journal_diff import TABLES, writeoff_causes
a, b = sys.argv[1:3]
GROUPS = {"K3": ("tp4_T600",), "H": ("flow300_H6", "flow300_tp100_H6")}
written_off, _ = writeoff_causes(a, b)


def rows(p, table):
    db = sqlite3.connect(f"file:{p}/journal.sqlite?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    keys, cols = TABLES[table]
    return {tuple(r[k] for k in keys): {c: r[c] for c in cols} for r in db.execute(f"SELECT * FROM {table}")}


wo = rows(b, "exits")
bad = []
for g, variants in GROUPS.items():
    offs = [r["exit_slot"] for k, r in wo.items() if k[1] in variants and r["exit_reason"] == "written_off"]
    cut = min(offs) if offs else None
    same = after = 0
    for table in ("signals", "orders", "fills", "exits"):
        ra, rb = rows(a, table), rows(b, table)
        for k in set(ra) | set(rb):
            if k[1] not in variants:
                continue
            x, y = ra.get(k), rb.get(k)
            if x == y:
                same += 1
                continue
            if cut is not None and k[3] >= cut:
                after += 1
            elif x is not None and y is not None and written_off(table, k, x, y):
                pass
            else:
                bad.append((g, table, k))
    print(f"{g}: write-offs {len(offs)}, first at {cut}; rows equal {same}; different from the first write-off on {after} (the cascade)")
print("risk on: " + ("GREEN" if not bad else f"RED: {len(bad)} rows differ before their group's first write-off, e.g. {bad[:5]}"))
sys.exit(1 if bad else 0)
EOF
  tee -a "$OUT/job.log" < "$OUT/risk-$w.log"
  [ "$rc" = 0 ] || { log "RED: risk on $w"; exit 1; }
done

# 4. the H arms against the backtest, main's engine and this commit's
IFS=":" read -r lo hi <<< "$W1"
for arm in flow300_H6 flow300_tp100_H6; do
  for side in main head; do
    src=$REPO
    [ "$side" = main ] && src=$MAIN_REPO
    d=$OUT/arm-$arm-$side
    [ ! -e "$d" ] || { log "RED: $d exists"; exit 1; }
    rc=0
    (cd "$src/lab" && PYTHONPATH=. "$PY" -m paper.parity --arm "$arm" --from "$lo" --to "$hi" --store "$STORE" --out "$d") \
      > "$d.log" 2>&1 || rc=$?
    [ "$rc" = 0 ] || { tail -20 "$d.log" | tee -a "$OUT/job.log"; log "RED: arm parity $arm $side failed"; exit 1; }
  done
  rc=0
  "$PY" - "$OUT/arm-$arm-main/parity.json" "$OUT/arm-$arm-head/parity.json" >> "$OUT/job.log" 2>&1 <<'EOF' || rc=$?
import json, sys
m, h = (json.load(open(p)) for p in sys.argv[1:3])
for name, x in (("main", m), ("head", h)):
    o, t = x["organic_busy"], x["tournament_busy"]
    print(f"{x['arm']} {name}: organic busy {o['different']} unexplained, {o['unsold_busy']} unsold_busy, {o.get('written_off', '-')} "
          f"written_off; tournament busy {t['different']} unexplained")
ok = (h["organic_busy"]["different"] <= m["organic_busy"]["different"]
      and h["tournament_busy"]["different"] <= m["tournament_busy"]["different"])
print(f"{h['arm']}: {'GREEN' if ok else 'RED'} (head no more unexplained than main)")
sys.exit(0 if ok else 1)
EOF
  [ "$rc" = 0 ] || { log "RED: arm parity $arm (job.log)"; exit 1; }
done
log "done: GREEN"
