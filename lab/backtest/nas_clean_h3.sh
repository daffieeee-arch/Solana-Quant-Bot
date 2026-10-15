#!/bin/bash
# H3 (research/hypothesis-h3-hold180-2026-10-15.md, sha256 110df1a2) on the NAS: K3's entry with the exit hold180
# against tp4_T600 on the clean data, a run of its own beside E20, on E20's store, after E20's job:
#   0. this checkout is the H3 pin (ENGINE) and its replay code is byte-equal to befacff; the documents' hashes;
#   a. E20's clean.lock exists (E20 passed its minimum: the store is final); the store is the locked one;
#   b. the run from an engine snapshot (engine/ with COMMIT): F7 pool_reversed, 0.5 SOL, q 0.5, d_ms 600, all 27
#      spec E variants (--f7-grid all), decisions in the store's window, positions only (--no-report). The
#      tournament is called directly (scripts/backtest-run.sh's MemAvailable gate never passes on the NAS: ZFS ARC);
#      the memory is bounded by the workers and LAB_DUCKDB_MEM;
#   c. backtest.clean_h3: the control against E20 run (1), the exclusions, then H3, the descriptive part, DSR, PBO.
#
#   ENGINE=<H3 pin> EXCLUSIONS_SHA=<sha256 of case-exclusions-clean-data.md> bash lab/backtest/nas_clean_h3.sh
#
# After a failure the job resumes: the run resumes its parts from the same engine snapshot only.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
: "${ENGINE:?the H3 pin (the merge commit of the PR of this job)}" "${EXCLUSIONS_SHA:?sha256 of the exclusion list}"
HYP=$DATA/research/hypothesis-h3-hold180-2026-10-15.md
EXCL=$DATA/research/case-exclusions-clean-data.md
EXITS_PARTS=${EXITS_PARTS:-$DATA/backtests/exits-dev-1033-1046-befacff/parts}
STORE_DIR=${LAB_STORE_DIR:-$DATA/store}
RUNS=${LAB_BACKTESTS:-$DATA/backtests}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
OUT=$DATA/research/clean-test/h3-${COMMIT:0:7}
export PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1
export LAB_SPILL_DIR=${LAB_SPILL_DIR:-/lab/tmp/clean-h3} LAB_SPILL_MAX=${LAB_SPILL_MAX:-40GB}
export LAB_DUCKDB_MEM=${LAB_DUCKDB_MEM:-8GB} LAB_DUCKDB_THREADS=${LAB_DUCKDB_THREADS:-4}
mkdir -p "$OUT" "$LAB_SPILL_DIR"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
log "start commit=$COMMIT engine=$ENGINE out=$OUT"

# 0. The checkout, its replay code and the documents, before anything opens the clean data.
case $COMMIT in "$ENGINE"*) ;; *) log "this checkout ($COMMIT) is not the H3 pin $ENGINE: REFUSED"; exit 1 ;; esac
"$PY" - "$HYP" "$EXCL" "$EXCLUSIONS_SHA" "$REPO/lab/backtest" "$EXITS_PARTS" >> "$OUT/job.log" 2>&1 <<'EOF' || { log "documents or engine REFUSED (job.log)"; exit 1; }
import os, sys
from backtest.clean_h3 import H3_SHA, check_replay
from backtest.clean_test import check_document
h, e, e_sha, engine, exits = sys.argv[1:]
print("H3", check_document(h, H3_SHA, "H3", sidecar=True))
print("exclusions", check_document(e, e_sha, "exclusions", sidecar=True))
print("replay code equal to befacff:", check_replay(engine))
assert os.path.isdir(exits), f"no exits-dev parts at {exits}"
EOF
log "documents and engine ok"

# a. E20's lock and store.
[ -e "$STORE_DIR/clean.lock" ] || { log "no clean.lock: E20 has not passed its minimum; H3 waits"; exit 1; }
read -r STORE FROM TO < <("$PY" - "$STORE_DIR/clean.lock" <<'EOF'
import json, sys
import duckdb
lock = json.load(open(sys.argv[1]))
meta = json.loads(duckdb.connect(lock["store"], read_only=True).execute("SELECT json FROM meta").fetchone()[0])
print(lock["store"], *meta["window"])
EOF
)
[ -n "${TO:-}" ] || { log "cannot read the locked store's window: REFUSED"; exit 1; }
LAST=$(( (TO - 1) / 432000 ))
log "store $STORE (locked by E20), window [$FROM,$TO), epochs 1055-$LAST"

# b. The run from an engine snapshot of this commit.
ID=clean-h3-1055-$LAST-$TO-${COMMIT:0:7}
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
      --families F7 --segments pool_reversed --sizes 0.5 --q 0.5 --tau empirical --delay-ms 600 --f7-grid all \
      --batch clean_h3 --decide "$FROM:$TO" --no-report --workers "${WORKERS:-4}" --run-id "$ID") > "$OUT/run.log" 2>&1; then
  log "run $ID ok: $(tail -1 "$OUT/run.log")"
else
  log "run $ID FAILED (run.log)"; exit 1
fi

# c. H3.
if "$PY" -m backtest.clean_h3 --run "$RUN" --store "$STORE" --out "$OUT/result" --hypothesis "$HYP" --exclusions "$EXCL" \
     --exclusions-sha256 "$EXCLUSIONS_SHA" --engine "$ENGINE" --engine-dir "$RUN/engine" --exits-parts "$EXITS_PARTS" > "$OUT/h3.log" 2>&1; then
  log "H3 ok: $(tail -1 "$OUT/h3.log")"
else
  log "H3 REFUSED or FAILED (h3.log)"; exit 1
fi
(cd "$OUT" && sha256sum result/*.json result/*.md > SHA256SUMS && sha256sum "$STORE" "$RUN/config.json" >> SHA256SUMS)
log "done: $OUT/result/result.md"
