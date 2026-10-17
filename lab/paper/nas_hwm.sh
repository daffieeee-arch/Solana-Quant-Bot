#!/bin/bash
# The high-water-mark restart (paper/restart.py) on the NAS.
#
#   STEP=recorder (bookworm runner, Rust 1.97.1): cargo test --release --locked of lab/recorder, with the test that a
#                 missing or old open-pools.json / watchlist-current.json keeps the base filters without a reload loop
#                 (the engine writes neither while it replays or catches up). Run with
#                 LAB_DATA_ROOT=/lab/data-old-faithful-one/lab (the container mounts only /lab).
#   STEP=python   (the lab Python 3.13 runner):
#                 1. the restart tests: synthetic (test_restart.py) and on dev2 with K3 and group H (test_restart_replay.py:
#                    a crash with an H position open over the HWM, a restart from a later start; LAB review point 7),
#                    plus the paper tests they touch (engine, LiveFeed, watchlist, arms replay, journal diff, report);
#                 2. the first-start regression: the uninterrupted replay U (K3 + H, the paper config) over the dev chunk
#                    [450144000, 450360000) with main's code (MAIN_DIR: an export of main at this branch's base) and with
#                    this commit's; paper.journal_diff must be green, the wallets, heartbeats and daily reports equal,
#                    and the only schema difference the new table `learned`. Both runs read this commit's config and
#                    groups files (unchanged by this branch), so the journal state 'groups' names the same risk paths.
#
#   STEP=recorder LAB_DATA_ROOT=/lab/data-old-faithful-one/lab bash lab/paper/nas_hwm.sh                   (bookworm runner)
#   STEP=python LAB_PY=/lab/venv/bin/python MAIN_DIR=<main export> bash lab/paper/nas_hwm.sh             (lab runner)
#
# Both steps write to $DATA/backtests/hwm-<commit7>/ (job.log, cargo-test.log, pytest.log, u-main/, u-new/, diff/).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
OUT=$DATA/backtests/hwm-${COMMIT:0:7}
mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
: "${STEP:?STEP=recorder or STEP=python}"
log "start $STEP commit=$COMMIT out=$OUT"

if [ "$STEP" = recorder ]; then
  cd "$REPO/lab/recorder"
  # A target without a built clang-sys must build it (bindgen), which needs libclang; the bookworm runner has none.
  # Without libclang, take the newest recorder build's target (build-recorder-bookworm.sh) instead of any target here:
  # one left by a failed build has no clang-sys either (jobs 17 and 19, 2026-10-17).
  if ! ldconfig -p 2>/dev/null | grep -q libclang && ! ls /usr/lib/llvm-*/lib/libclang*.so* >/dev/null 2>&1; then
    prev=$(ls -dt /lab/build/*-bookworm/lab/recorder/target 2>/dev/null | head -1 || true)
    [ -n "$prev" ] || { log "no libclang and no recorder target to reuse (/lab/build/*-bookworm/lab/recorder/target)"; exit 1; }
    rm -rf target
    cp -a --reflink=auto "$prev" target
    log "target: a copy of $prev (no libclang here)"
  fi
  cargo test --release --locked > "$OUT/cargo-test.log" 2>&1 || { tail -40 "$OUT/cargo-test.log" | tee -a "$OUT/job.log"; log "cargo test: FAILED"; exit 1; }
  grep -E "^test result|missing_or_old_input_files" "$OUT/cargo-test.log" | tee -a "$OUT/job.log"
  log "cargo test: ok ($(grep -c '^test .* ok$' "$OUT/cargo-test.log") tests ok)"
  exit 0
fi

[ "$STEP" = python ] || { log "unknown STEP=$STEP"; exit 2; }
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
: "${MAIN_DIR:?MAIN_DIR=an export of main at the base of this branch, for the first-start regression}"
STORE=${LAB_STORE:-$DATA/store/dev2.duckdb}
export LAB_STORE=$STORE PYTHONDONTWRITEBYTECODE=1
[ -f "$STORE" ] || { log "no dev store at $STORE"; exit 1; }

# 1. the tests
cd "$REPO/lab"
rc=0
LAB_NAS_TESTS=1 PYTHONPATH=$REPO/lab "$PY" -m pytest -q -rs -s paper/tests/test_restart.py paper/tests/test_restart_replay.py \
  paper/tests/test_arms_replay.py paper/tests/test_engine.py paper/tests/test_livefeed_state.py paper/tests/test_live.py \
  paper/tests/test_watchlist_live.py paper/tests/test_journal_diff.py paper/tests/test_report_verdict.py \
  paper/tests/test_keeper.py > "$OUT/pytest.log" 2>&1 || rc=$?
grep -E "H position|passed|failed|skipped|SKIPPED|Error" "$OUT/pytest.log" | tail -20 | tee -a "$OUT/job.log"
log "pytest rc=$rc"

# 2. the first-start regression: U with main's code and with this commit's
for side in main new; do
  src=$([ "$side" = main ] && echo "$MAIN_DIR" || echo "$REPO")
  rm -rf "$OUT/u-$side"
  log "U $side from $src"
  (cd "$src/lab" && PYTHONPATH=. "$PY" -m paper.engine --from 450144000 --to 450360000 --journal "$OUT/u-$side" \
     --config "$REPO/lab/paper/k3-paper-2026-10-12.yaml" --groups "$REPO/lab/paper/h-arms-2026-10-16.yaml" \
     --store "$STORE" --no-backup) \
     >> "$OUT/u-$side.log" 2>&1 || { tail -20 "$OUT/u-$side.log" | tee -a "$OUT/job.log"; log "U $side: FAILED"; exit 1; }
  tail -1 "$OUT/u-$side.log" | tee -a "$OUT/job.log"
done
cd "$REPO/lab"
PYTHONPATH=. "$PY" -m paper.journal_diff --journal-a "$OUT/u-main" --journal-b "$OUT/u-new" --out "$OUT/diff" | tee -a "$OUT/job.log" \
  || log "journal_diff: RED"
PYTHONPATH=. "$PY" - "$OUT/u-main" "$OUT/u-new" <<'PYEOF' 2>&1 | tee -a "$OUT/job.log"
import filecmp, glob, json, os, sqlite3, sys

a, b = sys.argv[1:3]
dbs = [sqlite3.connect(f"file:{os.path.join(d, 'journal.sqlite')}?mode=ro", uri=True) for d in (a, b)]
tables = [{r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")} for db in dbs]
print("schema: only main", sorted(tables[0] - tables[1]), "only new", sorted(tables[1] - tables[0]))
hb = [db.execute("SELECT * FROM heartbeat ORDER BY minute, note").fetchall() for db in dbs]
print("heartbeat rows", len(hb[0]), len(hb[1]), "equal" if hb[0] == hb[1] else "DIFFERENT")
learned = dbs[1].execute("SELECT count(*) FROM learned").fetchone()[0]
state = [dict(db.execute("SELECT k, v FROM state")) for db in dbs]
print("learned rows (replay: none expected)", learned, "| new state keys", sorted(set(state[1]) - set(state[0])))
for w in ("wallet.json", "wallet-h.json"):
    print(w, "equal" if json.load(open(os.path.join(a, w))) == json.load(open(os.path.join(b, w))) else "DIFFERENT")
for rep in sorted(glob.glob(os.path.join(a, "*", "report.md"))):
    other = os.path.join(b, os.path.relpath(rep, a))
    print(os.path.relpath(rep, a), "equal" if os.path.exists(other) and filecmp.cmp(rep, other, shallow=False) else "DIFFERENT")
PYEOF
log "python: done (pytest rc=$rc)"
exit $rc
