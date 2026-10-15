#!/bin/bash
# The clean-data test of the wash-trigger hypothesis (research/hypothesis-wash-trigger-2026-10-15.md, sha256
# 35bdaa0d) on the NAS, in this order:
#   0. the documents' hashes (hypothesis, the hashed addendum, the exclusion list), before any clean data is read;
#   a. the clean store from events/clean-v1/chunks (warm-up: the epoch before the window, from events/v1), with
#      the same store code as dev2 and the hold-out; it leaves store/clean.lock (one build);
#   b. K3 on it: F7 pool_reversed tp4_T600, 0.5 SOL, q 0.5, overlay none, d_ms 600 in whole slots of each
#      trigger's epoch (the paper engine's Clock rule), decisions only in the window, positions only;
#   c/d. backtest.clean_test: the exclusions resolved and applied before any outcome, then the split with and
#      without rt_tx (H1, H2, the decision), wash_1h descriptive, the DSR.
#
#   FROM=<slot> TO=<slot> ADDENDUM=<file> ADDENDUM_SHA=<sha256> EXCLUSIONS_SHA=<sha256> TRIALS=<n> \
#     bash lab/backtest/nas_clean_test.sh
#
# FROM/TO: the pre-registered decision window (chunk boundaries inside epochs 1055-1063). Start only after the
# addendum is hashed and the window's extraction is complete. After a failure the job resumes: an existing clean
# store is reused only when it was built for the same addendum and window; the tournament resumes its parts.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
: "${FROM:?first slot of the window}" "${TO:?end slot of the window (exclusive)}" "${ADDENDUM:?the addendum file}"
: "${ADDENDUM_SHA:?sha256 of the addendum}" "${EXCLUSIONS_SHA:?sha256 of the exclusion list}" "${TRIALS:?trials in the register}"
HYP=${HYPOTHESIS:-$DATA/research/hypothesis-wash-trigger-2026-10-15.md}
EXCL=${EXCLUSIONS:-$DATA/research/case-exclusions-clean-data.md}
STORE_DIR=${LAB_STORE_DIR:-$DATA/store}
STORE=$STORE_DIR/clean.duckdb
COMMIT=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || cat "$REPO/COMMIT" 2>/dev/null || echo unknown)
OUT=${OUT:-$DATA/research/clean-test/clean-$COMMIT}
export PYTHONPATH=$REPO/lab LAB_STORE_DIR=$STORE_DIR LAB_CLEAN_CHUNKS=${LAB_CLEAN_CHUNKS:-$DATA/events/clean-v1/chunks}
export LAB_SPILL_DIR=${LAB_SPILL_DIR:-/lab/tmp/clean-test} LAB_SPILL_MAX=${LAB_SPILL_MAX:-40GB}
export LAB_DUCKDB_MEM=${LAB_DUCKDB_MEM:-8GB} LAB_DUCKDB_THREADS=${LAB_DUCKDB_THREADS:-4}
mkdir -p "$OUT" "$LAB_SPILL_DIR"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
log "start commit=$COMMIT window=[$FROM,$TO) clean=$LAB_CLEAN_CHUNKS store=$STORE out=$OUT trials=$TRIALS"

# 0. The documents, before anything opens the clean data.
"$PY" - "$HYP" "$ADDENDUM" "$ADDENDUM_SHA" "$EXCL" "$EXCLUSIONS_SHA" >> "$OUT/job.log" 2>&1 <<'EOF' || { log "documents REFUSED (job.log)"; exit 1; }
import sys
from backtest.clean_test import HYPOTHESIS_SHA, check_document
h, a, a_sha, e, e_sha = sys.argv[1:]
for path, sha, name in ((h, HYPOTHESIS_SHA, "hypothesis"), (a, a_sha, "addendum"), (e, e_sha, "exclusions")):
    print(name, path, check_document(path, sha, name))
EOF
log "documents ok"

# a. The store (one build; reused only for the same addendum and window).
if [ -e "$STORE_DIR/clean.lock" ]; then
  "$PY" - "$STORE" "$ADDENDUM_SHA" "$FROM" "$TO" >> "$OUT/job.log" 2>&1 <<'EOF' || { log "clean.lock exists for another build: REFUSED"; exit 1; }
import json, sys
import duckdb
store, sha, lo, hi = sys.argv[1], sys.argv[2].lower(), int(sys.argv[3]), int(sys.argv[4])
meta = json.loads(duckdb.connect(store, read_only=True).execute("SELECT json FROM meta").fetchone()[0])
assert meta["period"] == "clean" and meta["prereg_sha256"].startswith(sha), meta.get("prereg_sha256")
assert meta["slot_end_exclusive"] == hi and meta["slot_start"] == lo - 432_000, (meta["slot_start"], meta["slot_end_exclusive"])
print("reusing", store, meta["git_commit"], meta["built_at_unix"])
EOF
  log "store reused: $STORE"
else
  if nice -n 10 "$PY" -m backtest.store clean --final --from-slot "$FROM" --to-slot "$TO" --prereg "$ADDENDUM" \
       --prereg-sha256 "$ADDENDUM_SHA" --out "$STORE" > "$OUT/store.log" 2>&1; then
    log "store ok: $(tail -c 300 "$OUT/store.log" | tr '\n' ' ')"
  else
    log "store FAILED (store.log)"; exit 1
  fi
fi

# b. K3 on the clean store: positions only (no report, no other variant, size or delay).
if LAB_STORE=$STORE LAB_BACKTESTS=$OUT nice -n 10 "$PY" -m backtest.tournament --families F7 --segments pool_reversed \
     --variants tp4_T600 --sizes 0.5 --q 0.5 --delay-ms 600 --decide "$FROM:$TO" --run-id k3 \
     --workers "${WORKERS:-4}" --no-report > "$OUT/tournament.log" 2>&1; then
  log "K3 ok: $(tail -1 "$OUT/tournament.log")"
else
  log "K3 FAILED (tournament.log)"; exit 1
fi

# c/d. The test: exclusions first, then the outcomes.
if "$PY" -m backtest.clean_test --run "$OUT/k3" --store "$STORE" --out "$OUT/result" --hypothesis "$HYP" \
     --addendum "$ADDENDUM" --addendum-sha256 "$ADDENDUM_SHA" --exclusions "$EXCL" --exclusions-sha256 "$EXCLUSIONS_SHA" \
     --trials "$TRIALS" > "$OUT/test.log" 2>&1; then
  log "test ok: $(tail -1 "$OUT/test.log")"
else
  log "test REFUSED or FAILED (test.log)"; exit 1
fi
(cd "$OUT" && sha256sum result/* k3/config.json > SHA256SUMS)
log "done: $OUT/result/result.md"
