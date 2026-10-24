#!/bin/bash
# Diagnostic (i) stage 1 (research/notes/pipeline-diagnostics-map-2026-10-24.json part "extract"): re-extract ONE dev
# chunk, 1043a = 450576000-450792000 (dev build 0cfc4db2, threads 6, in events/v1), as clean-v1 was extracted:
# /lab/bin/lab-extractor-1b16335 (sha256 f5dd11df...), THREADS=1, LAB_TRANSFERS=1, LAB_PUMP_MINTS = the clean-v1 start
# list (sha256 a98c534e...), the same proxy, index and JETSTREAMER env, into the NEW root events/diag-f5dd11df-t1; then
# checks.chunk_diff against the original with --normalize fca8c80 (0cfc4db2 predates fca8c80). Diagnostics only: no
# clean verdict changes on its result and nothing is re-tuned.
#
#   TRIES=1 LAB_PY=/lab/venv/bin/python bash /lab/code/repo-<sha7>/lab/checks/nas_reextract_diag.sh   # OK for ~0.35-0.45 TB
#   TRIES=3 LAB_PY=/lab/venv/bin/python bash /lab/code/repo-<sha7>/lab/checks/nas_reextract_diag.sh   # OK for ~1.05-1.35 TB
#
# DOWNLOAD: ~0.35-0.45 TB from Old Faithful over the owner's line PER TRY, and TRIES (required, 1-3) tries at most: a
# try that fails (exit 76 on a stuck slot, 3 on incomplete coverage) is not resumed, the next try downloads the whole
# chunk again (smoke_extract.sh empties OUT with rm -rf; the extractor refuses a non-empty --out). Worst case TRIES x
# ~0.35-0.45 TB: TRIES=3 up to ~1.05-1.35 TB. Needs the owner's explicit OK for that worst case (earlier approvals named
# epochs 1055-1063); set TRIES to what was approved. Queue /lab/jobs-extract (xrunner, python:3.13-trixie, 7 CPU / 12G;
# f5dd11df needs glibc >= 2.38).
# Est. 2-3 h extraction (CPU-bound at threads 1, ~370-400 Mbit/s, proxy cap 800 Mbit/s) plus OF1 429 waits, then
# ~15-40 min chunk_diff (DuckDB 6 GB, 4 threads). Disk: ~11-13 GB chunk + up to 40 GB spill; >= 60 GB free is checked.
# Only while no other OF1 extraction runs anywhere on the NAS (/lab/jobs too): they share the home line, the OF1
# limiter, and the proxy/index ports 3128/8771 in their containers. Job 36 (stale in jobs-extract/running/) is the
# coordinator's to settle first.
#
# Steps: guards (outputs new; never under events/v1, store/ or a clean or hold-out path; the chunk only under
# events/diag-*) -> sha256 of BIN, MINTS and /lab/ops/smoke_extract.sh -> the old chunk: complete, extractor 0cfc4db2, its
# footprint = the VPS one (lab/checks/ref/), listed in dev2's meta -> the chunk_diff tests -> up to TRIES tries of
# smoke_extract.sh (threads 1 hard-coded; rm -rf of OUT inside it, OUT asserted here), 15 min apart, until the manifest
# says complete -> chunk_diff, the new manifest asserted (extractor f5dd11df, threads 1, jetstreamer 1f9d306, the IDL
# sha256s, ix_accounts_json, rewards_unknown_type_skipped 0, the mint list file and its count) -> SHA256SUMS. Its rc 0
# (GREEN) and 1 (RED) count only with that verdict in out/summary.json; its rc 3 (an exception), a kill or a missing
# summary is a RED that says "tool or resource failure, not a data finding".
# Writes $DATA/events/diag-f5dd11df-t1/chunks/450576000-450792000 (its token tables come from a list with later mints:
# never research; its _learned_mints.txt never feeds a clean-v1 carry-over), /lab/runs/diag-reextract-1043a-<commit7>/
# (try<n>/: smoke.log, proxy.log, extract.log) and $DATA/reports/diag-reextract-1043a-<commit7>/ (job.log, pytest.log,
# dev2-meta.log, chunk_diff.log, out/ with summary.json, report.md, diffs/, footprints.json; SHA256SUMS). RED = rc 1.
# Not covered here (known dev -> clean pipeline differences, see the map's check): the clean store's warm-up epoch 1054
# came from events/v1 extracted by fada8c3 (sha 142dfe95, threads 3); dev2's 1044-1046 chunks (3b8b45ff) have no
# parent_ix_args, so their arg_* columns are null in dev2 and the replay falls back to q + fee; a transaction callback
# dropped inside a delivered block by both builds would not show (sum(executed_tx_count) = txs_seen is checked per side).
# COMPARE_ONLY=1: no extraction and no TRIES; an existing complete diag chunk is compared again into a new report dir
# (-compare-<utc>).
# DRY_RUN=1 (a local rehearsal, never evidence): DRY_BIN, DRY_MINTS, DRY_SMOKE, DRY_RUNS, DRY_REF, DRY_BIN_SHA,
# DRY_MINTS_SHA, DRY_SMOKE_SHA, DRY_RETRY_SLEEP and DRY_MIN_FREE_GB replace the constants; every output name ends in -dry.
set -euo pipefail
export LC_ALL=C  # the same sort order and tool output on the VPS and in the NAS container

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
PY=${LAB_PY:-/lab/venv/bin/python}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
CH=450576000-450792000
OLD_SHA=0cfc4db2a11218c674bcd152bebe25e0f69255b41b9a6aebc35d98a4e2209dcb
BIN=/lab/bin/lab-extractor-1b16335
BIN_SHA=f5dd11df1aadd2f5d1da1ede39bf919b27b980eb9d656eea7c9a6fdb2f32a1b5
MINTS=/lab/runs/extract-clean-v1/mints-start.txt
MINTS_SHA=a98c534e87233d3d23ca988a704a2252b8a3e356b978b005131c65d3f7d90f90
SMOKE=/lab/ops/smoke_extract.sh
SMOKE_SHA=42bc3de7b5e6abfd0e9bafe69ed64b70f6e366442ab599dd212ab700ed6d9aae
JET=https://github.com/anza-xyz/jetstreamer@1f9d30600fb479acdd5eb956ea2e19455c903590
REF=$REPO/lab/checks/ref/footprints-events-v1-dev-vps-2026-10-24.json
RUNS=/lab/runs RETRY_SLEEP=900 MIN_FREE_GB=60 SUFFIX=
if [ "${DRY_RUN:-0}" = 1 ]; then
  BIN=${DRY_BIN:-$BIN} MINTS=${DRY_MINTS:-$MINTS} SMOKE=${DRY_SMOKE:-$SMOKE} RUNS=${DRY_RUNS:-$RUNS} REF=${DRY_REF:-$REF}
  BIN_SHA=${DRY_BIN_SHA:-$BIN_SHA} MINTS_SHA=${DRY_MINTS_SHA:-$MINTS_SHA} SMOKE_SHA=${DRY_SMOKE_SHA:-$SMOKE_SHA}
  RETRY_SLEEP=${DRY_RETRY_SLEEP:-$RETRY_SLEEP} MIN_FREE_GB=${DRY_MIN_FREE_GB:-$MIN_FREE_GB} SUFFIX=-dry
fi
OLD=$DATA/events/v1/chunks/$CH
OUTROOT=$DATA/events/diag-f5dd11df-t1$SUFFIX
OUT=$OUTROOT/chunks/$CH
LOGS=$RUNS/diag-reextract-1043a-${COMMIT:0:7}$SUFFIX
RUN=$DATA/reports/diag-reextract-1043a-${COMMIT:0:7}$SUFFIX
SPILL_ROOT=${LAB_SPILL_ROOT:-/lab/tmp}
SPILL=$SPILL_ROOT/diag-chunkdiff-1043a-${COMMIT:0:7}$SUFFIX
if [ "${COMPARE_ONLY:-0}" = 1 ]; then
  STAMP=$(date -u +%Y%m%dT%H%M%SZ)
  RUN=$RUN-compare-$STAMP SPILL=$SPILL-compare-$STAMP
fi

refuse() { echo "$(date -u +%FT%TZ) RED: $*" >&2; exit 1; }
if [ "${COMPARE_ONLY:-0}" != 1 ]; then  # before anything is written: the download cap the owner approved
  case "${TRIES:-}" in
    1|2|3) ;;
    *) refuse "TRIES=1, 2 or 3 is required (now: ${TRIES:-unset}): each try downloads the whole chunk again," \
              "~0.35-0.45 TB; set it to the worst case the owner approved";;
  esac
fi
bad_path() {  # 0 when the path is under events/v1 or store/, or a clean or hold-out path
  case "${1,,}" in */events/v1|*/events/v1/*|*/store|*/store/*|*clean*|*holdout*|*hold-out*) return 0;; esac
  return 1
}
guard_new() {  # a report, log or spill directory: new, never under events/ or a forbidden path
  case "$1" in */events/*) refuse "output $1 is under events/";; esac
  ! bad_path "$1" || refuse "output $1 is under events/v1 or store/, or a clean or hold-out path"
  [ ! -e "$1" ] || refuse "$1 exists (every output is new: a new commit, or remove it on purpose)"
}
# The chunk: only the new diag root, which smoke_extract.sh empties with rm -rf before each try.
case "$OUT" in "$DATA"/events/diag-f5dd11df-t1*/chunks/"$CH") ;; *) refuse "OUT $OUT is not under $DATA/events/diag-f5dd11df-t1";; esac
! bad_path "$OUT" || refuse "OUT $OUT is under events/v1 or store/, or a clean or hold-out path"
if [ "${COMPARE_ONLY:-0}" = 1 ]; then
  grep -q '"status": "complete"' "$OUT/_manifest.json" 2>/dev/null || refuse "COMPARE_ONLY: no complete chunk at $OUT"
else
  [ ! -e "$OUTROOT" ] || refuse "$OUTROOT exists (a new root per run: COMPARE_ONLY=1 to compare it again, or remove it on purpose)"
  guard_new "$LOGS"
fi
guard_new "$RUN"
guard_new "$SPILL"
mkdir -p "$RUN"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$RUN/job.log"; }
sums() { (cd "$RUN" && find . -type f ! -path ./SHA256SUMS | sort | xargs sha256sum > SHA256SUMS); }
red() { log "RED: $*"; sums; exit 1; }
log "start commit=$COMMIT head=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo "export (COMMIT file)") chunk=$CH out=$OUT run=$RUN"
log "command: $0 $* (DRY_RUN=${DRY_RUN:-0} COMPARE_ONLY=${COMPARE_ONLY:-0} TRIES=${TRIES:-none} py=$PY bin=$BIN mints=$MINTS smoke=$SMOKE)"
[ -z "$SUFFIX" ] || log "DRY RUN: not evidence"

free_gb() { df -Pk "$1" | awk 'NR == 2 {print int($4 / 1048576)}'; }
mkdir -p "$SPILL_ROOT"
for d in "$DATA" "$SPILL_ROOT"; do
  [ "$(free_gb "$d")" -ge "$MIN_FREE_GB" ] || red "only $(free_gb "$d") GB free on $d (needs $MIN_FREE_GB)"
done
sha() { sha256sum < "$1" | cut -c1-64; }
[ "$(sha "$MINTS")" = "$MINTS_SHA" ] || red "MINTS $MINTS sha256 $(sha "$MINTS"), expected $MINTS_SHA"
if [ "${COMPARE_ONLY:-0}" != 1 ]; then
  [ -x "$BIN" ] && [ "$(sha "$BIN")" = "$BIN_SHA" ] || red "BIN $BIN missing or not sha256 $BIN_SHA"
  [ "$(sha "$SMOKE")" = "$SMOKE_SHA" ] || red "$SMOKE sha256 $(sha "$SMOKE"), expected $SMOKE_SHA (threads 1 and its rm -rf are relied on)"
fi
N_MINTS=$(sed 's/^[[:space:]]*//; s/[[:space:]]*$//' "$MINTS" | grep -v '^$' | sort -u | wc -l)  # as Transfers::from_env counts
log "BIN $BIN sha256 $BIN_SHA; MINTS $MINTS sha256 $MINTS_SHA, $N_MINTS distinct mints; smoke_extract.sh $SMOKE_SHA"

grep -q '"status": "complete"' "$OLD/_manifest.json" || red "the old chunk $OLD is not complete"
grep -q "\"extractor_sha256\": \"$OLD_SHA\"" "$OLD/_manifest.json" || red "the old chunk $OLD is not extractor $OLD_SHA"
cd "$REPO/lab"
export PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1
# Before any download: the old chunk is the VPS copy dev2 was built from (footprint), and dev2's meta lists it.
rc=0
"$PY" - "$OLD" "$REF" >> "$RUN/job.log" 2>&1 <<'EOF' || rc=$?
import json, os, sys
from checks import chunk_diff
name = os.path.basename(sys.argv[1])
ref = json.load(open(sys.argv[2])).get(name)
got = chunk_diff.footprint(sys.argv[1])
off = sorted(k for k in set(ref or {}) | set(got) if (ref or {}).get(k) != got.get(k))
print(f"footprint of {name}: {len(got)} files, {len(off)} differ from the VPS reference", off[:5])
sys.exit(1 if ref is None or off else 0)
EOF
[ "$rc" = 0 ] || red "the old chunk does not match the VPS footprint (job.log)"
if [ -f "$DATA/store/dev2.duckdb" ]; then
  rc=0
  "$PY" - "$DATA/store/dev2.duckdb" "$OLD" > "$RUN/dev2-meta.log" 2>&1 <<'EOF' || rc=$?
import json, os, sys
import duckdb
meta = json.loads(duckdb.connect(sys.argv[1], read_only=True).execute("SELECT json FROM meta").fetchone()[0])
s, t = (int(x) for x in os.path.basename(sys.argv[2]).split("-"))
c = {tuple(x["slots"]): x for x in meta.get("chunk_list") or []}.get((s, t))
m = json.load(open(os.path.join(sys.argv[2], "_manifest.json")))
print("dev2", c, "manifest", m.get("extractor_sha256"), "mtime", int(os.path.getmtime(os.path.join(sys.argv[2], "_manifest.json"))),
      "(mtime informative: a copy may not keep it)")
sys.exit(0 if c is not None and c.get("extractor_sha256") == m.get("extractor_sha256") else 1)
EOF
  log "dev2 meta: $(tail -1 "$RUN/dev2-meta.log" | cut -c1-300)"
  [ "$rc" = 0 ] || red "dev2 meta does not list $CH with the old manifest extractor (dev2-meta.log)"
else
  log "INFO: no $DATA/store/dev2.duckdb on this host: the dev2 meta check is skipped"
  echo "skipped: no $DATA/store/dev2.duckdb" > "$RUN/dev2-meta.log"
fi
rc=0
"$PY" -m pytest -q -p no:cacheprovider checks/test_chunk_diff.py > "$RUN/pytest.log" 2>&1 || rc=$?
log "tests: $(tail -1 "$RUN/pytest.log")"
[ "$rc" = 0 ] || red "the chunk_diff tests failed (pytest.log)"
grep -Eq "skipped|no tests ran" "$RUN/pytest.log" && red "a chunk_diff test was skipped (pytest.log)"

if [ "${COMPARE_ONLY:-0}" != 1 ]; then
  mkdir -p "$OUTROOT/chunks" "$LOGS"
  done_=0
  for try in $(seq 1 "$TRIES"); do
    log "extract try $try of $TRIES (a whole-chunk download, ~0.35-0.45 TB): $SMOKE SLOTS=${CH/-/:} threads 1 (hard-coded)" \
        "LAB_TRANSFERS=1 PROXY_LIMIT_MBPS=800 D=$LOGS/try$try"
    rc=0
    LAB_TRANSFERS=1 LAB_PUMP_MINTS=$MINTS PROXY_LIMIT_MBPS=800 BIN=$BIN SLOTS=${CH/-/:} D=$LOGS/try$try OUT=$OUT \
      bash "$SMOKE" > "$LOGS/try$try.out" 2>&1 || rc=$?
    log "try $try: smoke_extract rc=$rc; $(grep 'extract exit=' "$LOGS/try$try/smoke.log" 2>/dev/null | tail -1 | cut -c1-200 || echo 'no smoke.log')"
    if grep -q '"status": "complete"' "$OUT/_manifest.json" 2>/dev/null; then done_=1; break; fi
    [ "$try" = "$TRIES" ] || { log "not complete; waiting ${RETRY_SLEEP}s (OF1 limiter)"; sleep "$RETRY_SLEEP"; }
  done
  [ "$done_" = 1 ] || red "no complete chunk after $TRIES tries ($LOGS)"
  log "extracted: $(du -sh "$OUT" | cut -f1) in $OUT"
fi

CMD=("$PY" -m checks.chunk_diff --old "$OLD" --new "$OUT" --normalize fca8c80 --old-footprint "$REF"
     --expect-old "extractor_sha256=$OLD_SHA" --expect-new "extractor_sha256=$BIN_SHA" --expect-new threads=1
     --expect-new "jetstreamer=$JET" --expect-new ix_accounts_json=true --expect-new counters.rewards_unknown_type_skipped=0
     --expect-new "transfers.pump_mints_file=$MINTS" --expect-new "transfers.pump_mints=$N_MINTS"
     --memory "${MEMORY:-6GB}" --threads "${THREADS:-4}" --buckets "${BUCKETS:-8}" --spill "$SPILL" --spill-max 40GB --out "$RUN/out")
log "chunk_diff: ${CMD[*]}"
mkdir -p "$SPILL"
rc=0
nice -n 10 "${CMD[@]}" > "$RUN/chunk_diff.log" 2>&1 || rc=$?
log "chunk_diff rc=$rc: $(tail -1 "$RUN/chunk_diff.log")"
case "$SPILL" in  # the rm -rf target asserted first: this job's own spill directory only
  /?*/diag-chunkdiff-1043a-[0-9a-f]*) rm -rf -- "$SPILL";;
  *) red "refusing to remove the spill path $SPILL";;
esac
verdict() { grep -q "\"verdict\": \"$1\"" "$RUN/out/summary.json" 2>/dev/null; }
case $rc in
  0) verdict GREEN || red "chunk_diff rc 0 without a GREEN out/summary.json: a tool failure (chunk_diff.log)"
     log "done: GREEN, the re-extracted 1043a equals the dev chunk up to the fca8c80 rules (out/report.md)";;
  1) verdict RED || red "chunk_diff rc 1 without a RED out/summary.json: a tool failure (an import error?), not a data finding (chunk_diff.log)"
     red "chunk_diff found unexpected differences (out/report.md, out/summary.json)";;
  2) red "chunk_diff refused its inputs (chunk_diff.log)";;
  3) red "chunk_diff failed: a tool or resource failure (DuckDB memory or spill cap, an unreadable file), not a data finding (chunk_diff.log)";;
  *) red "chunk_diff failed with rc $rc (killed? out of memory?): a tool failure, not a data finding (chunk_diff.log)";;
esac
sums  # after job.log's last line
