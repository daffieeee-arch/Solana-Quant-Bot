#!/bin/bash
# The clean-data test of the wash-trigger hypothesis (research/hypothesis-wash-trigger-2026-10-15.md, sha256
# 35bdaa0d) on the NAS, as research/prereg-wash-trigger-addendum-2026-10-15.md (v5) lays it down:
#   0. the documents (hypothesis, addendum, exclusion list, a4c) and the engine: this checkout is the pinned
#      commit and its replay code is byte-equal to f2c9666, before any clean data is read;
#   a. the store backtest.store clean: window [455,760,000, end of the complete clean-v1 chunks), epoch 1054
#      from events/v1 as warm-up, one extractor build; store/clean-1055-<last epoch>.duckdb;
#   b. two tournament runs from an engine snapshot (engine/ with COMMIT), decisions only in the window,
#      positions only (--no-report): the test (--delay-ms 600) and the matrix (--delays 1,2,3 and any other
#      d the Clock gives for 600 ms);
#   c/d. backtest.clean_test: the runs' equality per epoch, the exclusions before any outcome, the minimum of
#      5 trading days, then H1, H2, the decision, wash_1h, a4c's consistency, the DSR and the PBO.
#
#   ENGINE=<pinned commit> ADDENDUM_SHA=<sha256> EXCLUSIONS_SHA=<sha256> bash lab/backtest/nas_clean_test.sh
#
# Start when epoch 1063 is complete in clean-v1 or on 2026-10-24 12:00 UTC (addendum section 4); operationally
# not before 5 complete epochs. Below 5 trading days the test writes only "te weinig dagen" and no lock: a later
# start rebuilds the store with more epochs. Once the test passes the minimum it writes clean.lock (the one
# run); a resume after a failure then reuses exactly that store and those runs (same engine snapshot only).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
: "${ENGINE:?the pinned engine commit (prefix)}" "${ADDENDUM_SHA:?sha256 of the addendum}" "${EXCLUSIONS_SHA:?sha256 of the exclusion list}"
FROM=455760000
TRIALS=42
HYP=$DATA/research/hypothesis-wash-trigger-2026-10-15.md
ADDENDUM=$DATA/research/prereg-wash-trigger-addendum-2026-10-15.md
EXCL=$DATA/research/case-exclusions-clean-data.md
A4C=$DATA/research/blindspots/a4c_f7rev_returns.py
DEV_PARTS=$DATA/backtests/f7rev-1033-1046/parts
STORE_DIR=${LAB_STORE_DIR:-$DATA/store}
RUNS=${LAB_BACKTESTS:-$DATA/backtests}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
OUT=$DATA/research/clean-test/clean-${COMMIT:0:7}
export PYTHONPATH=$REPO/lab LAB_STORE_DIR=$STORE_DIR LAB_CLEAN_CHUNKS=${LAB_CLEAN_CHUNKS:-$DATA/events/clean-v1/chunks}
export LAB_SPILL_DIR=${LAB_SPILL_DIR:-/lab/tmp/clean-test} LAB_SPILL_MAX=${LAB_SPILL_MAX:-40GB}
export LAB_DUCKDB_MEM=${LAB_DUCKDB_MEM:-8GB} LAB_DUCKDB_THREADS=${LAB_DUCKDB_THREADS:-4}
mkdir -p "$OUT" "$LAB_SPILL_DIR"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
log "start commit=$COMMIT engine=$ENGINE clean=$LAB_CLEAN_CHUNKS out=$OUT"

# 0. Documents and engine, before anything opens the clean data.
case $COMMIT in "$ENGINE"*) ;; *) log "this checkout ($COMMIT) is not the pinned engine $ENGINE: REFUSED"; exit 1 ;; esac
"$PY" - "$HYP" "$ADDENDUM" "$ADDENDUM_SHA" "$EXCL" "$EXCLUSIONS_SHA" "$A4C" "$REPO/lab/backtest" >> "$OUT/job.log" 2>&1 <<'EOF' || { log "documents or engine REFUSED (job.log)"; exit 1; }
import sys
from backtest.clean_test import A4C_SHA, HYPOTHESIS_SHA, check_document, check_replay_code
h, a, a_sha, e, e_sha, a4c, engine = sys.argv[1:]
for path, sha, name in ((h, HYPOTHESIS_SHA, "hypothesis"), (a, a_sha, "addendum"), (e, e_sha, "exclusions"), (a4c, A4C_SHA, "a4c")):
    print(name, path, check_document(path, sha, name, sidecar=True))
print("replay code equal to f2c9666:", check_replay_code(engine))
EOF
log "documents and engine ok"

# a. The store. clean.lock (written by clean_test once the minimum of trading days is passed: the one run)
# pins it; without a lock a store for the current end of the clean root is reused, else built (a "te weinig
# dagen" store with fewer epochs is superseded, and logged).
NOW_END=$("$PY" -c "from backtest.store import clean_window_end; print(clean_window_end($FROM))")
if [ -e "$STORE_DIR/clean.lock" ]; then
  STORE=$("$PY" - "$STORE_DIR/clean.lock" "$ADDENDUM_SHA" "$NOW_END" 2>> "$OUT/job.log" <<'EOF'
import json, sys
import duckdb
lock = json.load(open(sys.argv[1]))
assert lock["addendum_sha256"].startswith(sys.argv[2].lower()), lock["addendum_sha256"]
meta = json.loads(duckdb.connect(lock["store"], read_only=True).execute("SELECT json FROM meta").fetchone()[0])
print(f"locked store, window {meta['window']}, warm-up epochs {meta['warmup_epochs']}; clean root now ends at {sys.argv[3]}", file=sys.stderr)
print(lock["store"])
EOF
  ) || { log "clean.lock exists for another addendum or store: REFUSED"; exit 1; }
  log "store locked by the test's run: $STORE (window and clean-root end in job.log)"
else
  CANDIDATE=$STORE_DIR/clean-1055-$(( (NOW_END - 1) / 432000 )).duckdb
  REUSE=$("$PY" - "$CANDIDATE" "$ADDENDUM_SHA" "$FROM" "$NOW_END" 2>> "$OUT/job.log" <<'EOF'
import json, os, sys
import duckdb
path, sha, lo, hi = sys.argv[1], sys.argv[2].lower(), int(sys.argv[3]), int(sys.argv[4])
ok = False
if os.path.exists(path):
    meta = json.loads(duckdb.connect(path, read_only=True).execute("SELECT json FROM meta").fetchone()[0])
    ok = meta["period"] == "clean" and meta["prereg_sha256"].startswith(sha) and meta["window"] == [lo, hi] and meta["warmup_epochs"] == 1
    print(f"existing {path}: window {meta['window']}, reuse {ok}", file=sys.stderr)
print("yes" if ok else "no")
EOF
  )
  for old in "$STORE_DIR"/clean-1055-*.duckdb; do
    [ -e "$old" ] && [ "$old" != "$CANDIDATE" ] && log "superseded store (fewer epochs, no outcome): $old"
  done
  if [ "$REUSE" = yes ]; then
    STORE=$CANDIDATE
    log "store reused: $STORE (window [$FROM,$NOW_END))"
  elif nice -n 10 "$PY" -m backtest.store clean --final --warmup-epochs 1 --from-slot "$FROM" --prereg "$ADDENDUM" \
         --prereg-sha256 "$ADDENDUM_SHA" > "$OUT/store.log" 2>&1; then
    STORE=$(sed -n 's/^store: //p' "$OUT/store.log")
    log "store built: $STORE (window [$FROM,$NOW_END))"
  else
    log "store FAILED (store.log)"; exit 1
  fi
fi
read -r TO DELAYS < <("$PY" - "$STORE" <<'EOF'
import json, sys
import duckdb
from backtest.strategies import EPOCH_SLOTS, Clock
meta = json.loads(duckdb.connect(sys.argv[1], read_only=True).execute("SELECT json FROM meta").fetchone()[0])
lo, hi = meta["window"]
clock = Clock(meta["ms_per_slot"])
ds = {clock.slots(600, e * EPOCH_SLOTS) for e in range(lo // EPOCH_SLOTS, (hi - 1) // EPOCH_SLOTS + 1)}
print(hi, ",".join(str(d) for d in sorted(ds | {1, 2, 3})))
EOF
)
[ -n "${TO:-}" ] && [ -n "${DELAYS:-}" ] || { log "cannot read the store's window and delays: REFUSED"; exit 1; }
LAST=$(( (TO - 1) / 432000 ))
log "window [$FROM,$TO) (epochs 1055-$LAST); matrix delays $DELAYS; store sha256 $(sha256sum "$STORE" | cut -c1-16)"

# b. Two runs from engine snapshots of this commit.
ID=clean-1055-$LAST-${COMMIT:0:7}
snapshot() {  # $1: run dir
  if [ ! -d "$1/engine/backtest" ]; then
    mkdir -p "$1/engine"
    if git -C "$REPO" rev-parse HEAD > /dev/null 2>&1; then
      git -C "$REPO/lab" archive "$COMMIT" backtest | tar -x -C "$1/engine"
    else
      cp -r "$REPO/lab/backtest" "$1/engine/"
    fi
    echo "$COMMIT" > "$1/engine/COMMIT"
  elif [ "$(cat "$1/engine/COMMIT")" != "$COMMIT" ]; then
    log "$1 was started with engine $(cat "$1/engine/COMMIT"): REFUSED"; exit 1
  fi
}
run() {  # $1: run id, then the delay arguments
  local id=$1; shift
  snapshot "$RUNS/$id"
  if (cd "$RUNS/$id/engine" && LAB_STORE=$STORE LAB_BACKTESTS=$RUNS LAB_ENGINE_COMMIT=$COMMIT PYTHONPATH=. nice -n 10 "$PY" -m backtest.tournament \
        --families F7,N2 --segments pool_reversed --sizes 0.5 --q 0.5 --tau empirical --n2-k 5 --batch clean_wash \
        --decide "$FROM:$TO" --no-report --workers "${WORKERS:-4}" --run-id "$id" "$@") > "$OUT/$id.log" 2>&1; then
    log "run $id ok: $(tail -1 "$OUT/$id.log")"
  else
    log "run $id FAILED ($id.log)"; exit 1
  fi
}
run "$ID" --delay-ms 600
run "$ID-matrix" --delays "$DELAYS"

# c/d. The test.
if "$PY" -m backtest.clean_test --test-run "$RUNS/$ID" --matrix-run "$RUNS/$ID-matrix" --store "$STORE" --out "$OUT/result" \
     --hypothesis "$HYP" --addendum "$ADDENDUM" --addendum-sha256 "$ADDENDUM_SHA" --exclusions "$EXCL" \
     --exclusions-sha256 "$EXCLUSIONS_SHA" --trials "$TRIALS" --dev-parts "$DEV_PARTS" --engine "$ENGINE" \
     --engine-dir "$RUNS/$ID/engine" --a4c "$A4C" > "$OUT/test.log" 2>&1; then
  log "test ok: $(tail -1 "$OUT/test.log")"
else
  log "test REFUSED or FAILED (test.log)"; exit 1
fi
(cd "$OUT" && sha256sum result/* > SHA256SUMS && sha256sum "$STORE" "$RUNS/$ID/config.json" "$RUNS/$ID-matrix/config.json" >> SHA256SUMS)
log "done: $OUT/result/result.md"
