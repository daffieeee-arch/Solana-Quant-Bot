#!/bin/bash
# A clean-data hypothesis of backtest.clean_hx (SPEC=h5: backtest/clean_h5.py) on the NAS: F7's entry with one exit
# variant on E20's store, a run of its own after E20 (and H3, H4):
#   0. this checkout is the pin (ENGINE) and its replay code is byte-equal to the Spec's; the documents' hashes; the
#      dev trials' parts for the DSR;
#   a. E20's clean.lock exists (E20 passed its minimum: the store is final); the store is the locked one;
#   b. the run from an engine snapshot (engine/ with COMMIT): F7 pool_reversed, 0.5 SOL, q 0.5, d_ms 600, the Spec's
#      --f7-grid, decisions in the store's window, positions only (--no-report). The tournament is called directly
#      (scripts/backtest-run.sh's MemAvailable gate never passes on the NAS: ZFS ARC);
#   c. the Spec's module: the control against E20 run (1), the exclusions, then the test, the descriptive part, DSR, PBO.
#
#   SPEC=h5 ENGINE=<pin> EXCLUSIONS_SHA=<sha256 of case-exclusions-clean-data.md> bash lab/backtest/nas_clean_hx.sh
#
# After a failure the job resumes: the run resumes its parts from the same engine snapshot only.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
: "${SPEC:?the hypothesis module (h5: backtest/clean_h5.py)}" "${ENGINE:?the pin (the merge commit of the PR of this job)}"
: "${EXCLUSIONS_SHA:?sha256 of the exclusion list}"
export PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1
eval "$("$PY" -c "from backtest.clean_$SPEC import SPEC; from backtest.clean_hx import spec_env; print(spec_env(SPEC))")"
HYP=$DATA/research/$HYP_FILE
EXCL=$DATA/research/case-exclusions-clean-data.md
TRIALS_PARTS=${TRIALS_PARTS:-$DATA/backtests/$TRIALS_RUN/parts}
STORE_DIR=${LAB_STORE_DIR:-$DATA/store}
RUNS=${LAB_BACKTESTS:-$DATA/backtests}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
OUT=$DATA/research/clean-test/$NAME-${COMMIT:0:7}
export LAB_SPILL_DIR=${LAB_SPILL_DIR:-/lab/tmp/clean-$NAME} LAB_SPILL_MAX=${LAB_SPILL_MAX:-40GB}
export LAB_DUCKDB_MEM=${LAB_DUCKDB_MEM:-8GB} LAB_DUCKDB_THREADS=${LAB_DUCKDB_THREADS:-4}
mkdir -p "$OUT" "$LAB_SPILL_DIR"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
log "start $NAME commit=$COMMIT engine=$ENGINE grid=$F7_GRID out=$OUT"

# 0. The checkout, its replay code and the documents, before anything opens the clean data.
case $COMMIT in "$ENGINE"*) ;; *) log "this checkout ($COMMIT) is not the $NAME pin $ENGINE: REFUSED"; exit 1 ;; esac
"$PY" - "$SPEC" "$HYP" "$EXCL" "$EXCLUSIONS_SHA" "$REPO/lab/backtest" "$TRIALS_PARTS" >> "$OUT/job.log" 2>&1 <<'PYEOF' || { log "documents or engine REFUSED (job.log)"; exit 1; }
import importlib, os, sys
from backtest.clean_hx import check_replay
from backtest.clean_test import check_document
mod, h, e, e_sha, engine, trials = sys.argv[1:]
spec = importlib.import_module(f"backtest.clean_{mod}").SPEC.check()
print(spec.name, check_document(h, spec.sha, spec.name, sidecar=True))
print("exclusions", check_document(e, e_sha, "exclusions", sidecar=True))
print("replay code equal to the pin's:", check_replay(engine, spec))
assert os.path.isdir(trials), f"no trials parts at {trials}"
PYEOF
log "documents and engine ok"

# a. E20's lock and store.
[ -e "$STORE_DIR/clean.lock" ] || { log "no clean.lock: E20 has not passed its minimum; $NAME waits"; exit 1; }
read -r STORE FROM TO < <("$PY" - "$STORE_DIR/clean.lock" <<'PYEOF'
import json, sys
import duckdb
lock = json.load(open(sys.argv[1]))
meta = json.loads(duckdb.connect(lock["store"], read_only=True).execute("SELECT json FROM meta").fetchone()[0])
print(lock["store"], *meta["window"])
PYEOF
)
[ -n "${TO:-}" ] || { log "cannot read the locked store's window: REFUSED"; exit 1; }
LAST=$(( (TO - 1) / 432000 ))
log "store $STORE (locked by E20), window [$FROM,$TO), epochs 1055-$LAST"

# b. The run from an engine snapshot of this commit.
ID=clean-$NAME-1055-$LAST-$TO-${COMMIT:0:7}
RUN=$RUNS/$ID
if [ ! -d "$RUN/engine/backtest" ]; then
  mkdir -p "$RUN/engine"
  if git -C "$REPO" rev-parse HEAD > /dev/null 2>&1; then
    git -C "$REPO/lab" archive "$COMMIT" backtest | tar -x -C "$RUN/engine"
  else
    cp -r "$REPO/lab/backtest" "$RUN/engine/"
  fi
  echo "$COMMIT" > "$RUN/engine/COMMIT"
elif [ "$(cat "$RUN/engine/COMMIT")" != "$COMMIT" ]; then
  log "$RUN was started with engine $(cat "$RUN/engine/COMMIT"): REFUSED"; exit 1
fi
if (cd "$RUN/engine" && LAB_STORE=$STORE LAB_BACKTESTS=$RUNS LAB_ENGINE_COMMIT=$COMMIT PYTHONPATH=. nice -n 10 "$PY" -m backtest.tournament \
      --families F7 --segments pool_reversed --sizes 0.5 --q 0.5 --tau empirical --delay-ms 600 --f7-grid "$F7_GRID" \
      --batch "clean_$NAME" --decide "$FROM:$TO" --no-report --workers "${WORKERS:-4}" --run-id "$ID") > "$OUT/run.log" 2>&1; then
  log "run $ID ok: $(tail -1 "$OUT/run.log")"
else
  log "run $ID FAILED (run.log)"; exit 1
fi

# c. The test.
if "$PY" -m "backtest.clean_$SPEC" --run "$RUN" --store "$STORE" --out "$OUT/result" --hypothesis "$HYP" --exclusions "$EXCL" \
     --exclusions-sha256 "$EXCLUSIONS_SHA" --engine "$ENGINE" --engine-dir "$RUN/engine" --trials-parts "$TRIALS_PARTS" > "$OUT/$NAME.log" 2>&1; then
  log "$NAME ok: $(tail -1 "$OUT/$NAME.log")"
else
  log "$NAME REFUSED or FAILED ($NAME.log)"; exit 1
fi
(cd "$OUT" && sha256sum result/*.json result/*.md > SHA256SUMS && sha256sum "$STORE" "$RUN/config.json" >> SHA256SUMS)
log "done: $OUT/result/result.md"
