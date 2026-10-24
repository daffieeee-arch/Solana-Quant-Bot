#!/bin/bash
# Diagnostic (i) stage 0, no download (research/notes/pipeline-diagnostics-map-2026-10-24.json part "extract"): the NAS
# events/transfers-v1 chunks (0d915ac, binary 5092ffa4, threads 6, LAB_TRANSFERS=1) against events/v1 event for event
# with checks.chunk_diff: 1040a/b (dev build eac48884) and 1045a/b (3b8b45ff), plus tx_ctx against events/txctx-v1
# (107f6c5, binary 1028c4b4, threads 3). By code this tests the clean-v1 build's core tables: git diff 0d915ac 1b16335
# -- lab/extractor touches only the transfer path (and 107f6c5..0d915ac only comments of tx_ctx). Open after it: threads 1
# and the exact binary (stage 1, nas_reextract_diag.sh). Diagnostics only: no clean verdict changes, nothing is re-tuned.
#
#   LAB_PY=/lab/venv/bin/python bash /lab/code/repo-<sha7>/lab/checks/nas_chunk_diff_stage0.sh
#
# Runner queue /lab/jobs (python:3.13, 48G); no network. Est. 30-90 min: reads the 12 chunks (~45 GB parquet), DuckDB
# 12 GB (MEMORY) and 6 threads, spill up to 40 GB under /lab/tmp. Before: the merged commit exported to
# /lab/code/repo-<sha7> (a git checkout, or an export with a COMMIT file).
# Steps: guards (every output new, never under events/, store/ or a clean or hold-out path) -> inputs present -> the
# chunk_diff tests -> dev2's meta lists every old chunk with its manifest's extractor (read-only; INFO without dev2) ->
# chunk_diff with the old chunks matched to the VPS footprints (lab/checks/ref/, taken 2026-10-24 on the VPS from
# events/v1 with `python -m checks.chunk_diff footprint`, footer reads only) and the new manifests asserted
# (extractor 5092ffa4, threads 6) -> SHA256SUMS. RED = rc 1, the reason last in job.log.
# Writes $DATA/reports/diag-chunkdiff-stage0-<commit7>/: job.log, pytest.log, dev2-meta.log, chunk_diff.log, out/
# (summary.json, report.md, diffs/, footprints.json, SHA256SUMS of its own), SHA256SUMS over all of these.
# Overrides: LAB_DATA_ROOT, LAB_SPILL_ROOT (/lab/tmp), MEMORY (12GB), THREADS (6), BUCKETS (8), EXTRAS ("tx_ctx"; add
# token_meta only if every txctx-v1 chunk has it). DRY_RUN=1 (a local rehearsal, never evidence): DRY_CHUNKS, DRY_REF and
# DRY_NEW_SHA may replace the chunk list, the footprint file and the new extractor sha256; every output name ends in -dry.
set -euo pipefail
export LC_ALL=C  # the same sort order and tool output on the VPS and in the NAS container

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
PY=${LAB_PY:-/lab/venv/bin/python}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
CHUNKS=449280000-449496000,449496000-449712000,451440000-451656000,451656000-451872000
REF=$REPO/lab/checks/ref/footprints-events-v1-dev-vps-2026-10-24.json
NEW_SHA=5092ffa4643a384da9832f10c8bef8fedcfee06ed1f052b8e3de541271020974
SUFFIX=
if [ "${DRY_RUN:-0}" = 1 ]; then
  CHUNKS=${DRY_CHUNKS:-$CHUNKS} REF=${DRY_REF:-$REF} NEW_SHA=${DRY_NEW_SHA:-$NEW_SHA} SUFFIX=-dry
fi
RUN=$DATA/reports/diag-chunkdiff-stage0-${COMMIT:0:7}$SUFFIX
SPILL=${LAB_SPILL_ROOT:-/lab/tmp}/diag-chunkdiff-stage0-${COMMIT:0:7}$SUFFIX

refuse() { echo "$(date -u +%FT%TZ) RED: $*" >&2; exit 1; }
guard_new() {  # an output directory: new, never under events/ or store/, never a clean or hold-out path
  case "${1,,}" in
    */events/*|*/store/*|*clean*|*holdout*|*hold-out*) refuse "output $1 is under events/ or store/, or a clean or hold-out path";;
  esac
  [ ! -e "$1" ] || refuse "$1 exists (every output is new: a new commit, or remove it on purpose)"
}
guard_new "$RUN"
guard_new "$SPILL"
mkdir -p "$RUN"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$RUN/job.log"; }
sums() { (cd "$RUN" && find . -type f ! -path ./SHA256SUMS | sort | xargs sha256sum > SHA256SUMS); }
red() { log "RED: $*"; sums; exit 1; }
log "start commit=$COMMIT head=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo "export (COMMIT file)") run=$RUN"
log "command: $0 $* (DRY_RUN=${DRY_RUN:-0} MEMORY=${MEMORY:-12GB} THREADS=${THREADS:-6} BUCKETS=${BUCKETS:-8} EXTRAS=${EXTRAS:-tx_ctx} py=$PY)"
[ -z "$SUFFIX" ] || log "DRY RUN: not evidence"

[ -f "$REF" ] || red "no footprint reference $REF"
EXTRA_ARGS=()
for t in ${EXTRAS:-tx_ctx}; do EXTRA_ARGS+=(--extra "$t=$DATA/events/txctx-v1/chunks"); done
for c in ${CHUNKS//,/ }; do
  for root in v1 transfers-v1 txctx-v1; do
    [ -f "$DATA/events/$root/chunks/$c/_manifest.json" ] || red "no chunk $DATA/events/$root/chunks/$c"
  done
  for t in ${EXTRAS:-tx_ctx}; do
    [ -d "$DATA/events/txctx-v1/chunks/$c/$t" ] || red "txctx-v1 chunk $c has no $t"
  done
done
log "inputs: events/v1, transfers-v1 and txctx-v1 hold $CHUNKS"

cd "$REPO/lab"
export PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1
rc=0
"$PY" -m pytest -q -p no:cacheprovider checks/test_chunk_diff.py > "$RUN/pytest.log" 2>&1 || rc=$?
log "tests: $(tail -1 "$RUN/pytest.log")"
[ "$rc" = 0 ] || red "the chunk_diff tests failed (pytest.log)"
grep -Eq "skipped|no tests ran" "$RUN/pytest.log" && red "a chunk_diff test was skipped (pytest.log)"

# dev2's meta (store.py _chunk_meta: extractor_sha256 and manifest_mtime per chunk) must list every old chunk with the
# extractor its manifest names: the events/v1 copy is the one the dev store was built from. Read-only.
if [ -f "$DATA/store/dev2.duckdb" ]; then
  rc=0
  "$PY" - "$DATA/store/dev2.duckdb" "$DATA/events/v1/chunks" "$CHUNKS" > "$RUN/dev2-meta.log" 2>&1 <<'EOF' || rc=$?
import json, os, sys
import duckdb
con = duckdb.connect(sys.argv[1], read_only=True)
meta = json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
listed = {tuple(c["slots"]): c for c in meta.get("chunk_list") or []}
bad = 0
for name in sys.argv[3].split(","):
    s, t = (int(x) for x in name.split("-"))
    m = json.load(open(os.path.join(sys.argv[2], name, "_manifest.json")))
    mtime = int(os.path.getmtime(os.path.join(sys.argv[2], name, "_manifest.json")))
    c = listed.get((s, t))
    ok = c is not None and c.get("extractor_sha256") == m.get("extractor_sha256")
    bad += not ok
    print(name, "OK" if ok else "MISMATCH", "dev2", c, "manifest", m.get("extractor_sha256"), "mtime", mtime,
          "(mtime informative: a copy may not keep it)")
sys.exit(1 if bad else 0)
EOF
  log "dev2 meta: $(grep -c ' OK ' "$RUN/dev2-meta.log" || true) of the old chunks listed with their extractor"
  [ "$rc" = 0 ] || red "dev2 meta does not list an old chunk with its manifest extractor (dev2-meta.log)"
else
  log "INFO: no $DATA/store/dev2.duckdb on this host: the dev2 meta check is skipped"
  echo "skipped: no $DATA/store/dev2.duckdb" > "$RUN/dev2-meta.log"
fi

CMD=("$PY" -m checks.chunk_diff --old-root "$DATA/events/v1/chunks" --new-root "$DATA/events/transfers-v1/chunks" --chunks "$CHUNKS"
     "${EXTRA_ARGS[@]}" --old-footprint "$REF" --expect-new "extractor_sha256=$NEW_SHA" --expect-new threads=6
     --memory "${MEMORY:-12GB}" --threads "${THREADS:-6}" --buckets "${BUCKETS:-8}" --spill "$SPILL" --spill-max 40GB --out "$RUN/out")
log "chunk_diff: ${CMD[*]}"
mkdir -p "$SPILL"
rc=0
nice -n 10 "${CMD[@]}" > "$RUN/chunk_diff.log" 2>&1 || rc=$?
log "chunk_diff rc=$rc: $(tail -1 "$RUN/chunk_diff.log")"
case "$SPILL" in  # the rm -rf target asserted first: this job's own spill directory only
  /?*/diag-chunkdiff-stage0-[0-9a-f]*) rm -rf -- "$SPILL";;
  *) red "refusing to remove the spill path $SPILL";;
esac
case $rc in
  0) log "done: GREEN (out/report.md)";;
  1) red "chunk_diff found unexpected differences (out/report.md, out/summary.json)";;
  2) red "chunk_diff refused its inputs (chunk_diff.log)";;
  *) red "chunk_diff failed with rc $rc (chunk_diff.log)";;
esac
sums  # after job.log's last line
