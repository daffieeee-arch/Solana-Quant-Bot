#!/bin/bash
# Diagnostic (ii) of the pipeline diagnostics (research/notes/pipeline-diagnostics-map-2026-10-24.json, part store), on
# dev data only; whatever it shows, no verdict changes and nothing is re-tuned. Does the clean store's construction
# (be47fc4's store.py through the clean code path, WARMUP epochs before the window from events/v1) reproduce dev2
# (ba322be, 1033-1046) on a dev epoch?
#   S1  be47fc4's store.py: store.CLEAN and store.CLEAN_CHUNKS set in-process to the dev window [FROM_SLOT, TO_SLOT)
#       (default epoch 1043) and WINDOW_ROOT, then period_chunks('clean', ..., WARMUP) (default 1), main()'s
#       single-build check of the window, and build() with the period label dev-diag-clean-path (clean_test wants
#       'clean', analytics 'dev': both refuse the store; build() uses the period only in meta) and a DEV DIAGNOSTIC
#       meta. Never store.main, never under store/ (store/clean.lock exists on the NAS).
#   S0  ba322be's store.py (dev2's code), period dev, over the same chunks: the code control (core run only).
#   C1  store_diff full S1 S0; C2 store_diff window S1 dev2; C4 store_diff triggers S1 dev2 with be47fc4's strategies.py.
# The GREEN rule is store_diff.py's docstring, pinned before the run. NAS only, 48G long runner: about 30-45 min, peak
# RSS about 10 GB, about 6 GB per store, spill up to about 20 GB per chunk.
#
#   NEW=/lab/code/repo-be47fc4 OLD=/lab/code/repo-ba322be LAB_PY=/lab/venv/bin/python \
#     bash /lab/code/repo-<diag7>/lab/backtest/nas_store_warmup_diag.sh
#
# Optional runs (an S1-style build, C2 and C4; no S0, no C1), each in its own output directory (OUT_TAG):
#   OUT_TAG=s2 WINDOW_ROOT=<diagnostic (i)'s chunk root>  the window from another extractor build (its manifests are
#                                                          logged, not checked); a 1-chunk window: FROM_SLOT/TO_SLOT
#   OUT_TAG=s4 TO_SLOT=452304000                           window 1043-1046, ends where dev2 ends: the warm-up length only
#   OUT_TAG=s1w0 WARMUP=0                                  no warm-up (C2 pool is RED by construction: the farmer flags
#                                                          of the first 24 h; read the categories)
# Output: $DATA/backtests/store-warmup-diag-<diag7>-<EPOCH>[-<OUT_TAG>]/ (new): job.log, build and report logs, the
# inputs/c1/c2/c4 JSON reports, c4-triggers.csv, chunks.json, SHA256SUMS. The stores are deleted after a GREEN run
# (KEEP_S1=1 keeps S1) and kept after a RED one. Any RED: rc 1 with the reason in job.log.
set -euo pipefail
export LC_ALL=C
die() { echo "RED: $*" >&2; exit 1; }
num() { case $2 in '' | *[!0-9]*) die "$1 is not a number: $2" ;; esac; }
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
PY=${LAB_PY:-/lab/venv/bin/python}
: "${NEW:?the be47fc4 checkout}" "${OLD:?the ba322be checkout}"
NEW=$(cd "$NEW" && pwd) OLD=$(cd "$OLD" && pwd)
DIAG=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)  # this repo: the comparator and the manifest reference
NEW_SHA=be47fc425fc46942884db256e5c9d809a11d57a9
OLD_SHA=ba322be60d25c50ddd1aa5107ed85d471cd711d7
DUCKDB_VERSION=1.5.5  # lab/requirements-lab.txt; dev2 was built with it on the VPS
EPOCH=${EPOCH:-1043} WARMUP=${WARMUP:-1}
num EPOCH "$EPOCH"
num WARMUP "$WARMUP"
FROM_SLOT=${FROM_SLOT:-$((EPOCH * 432000))}
num FROM_SLOT "$FROM_SLOT"
TO_SLOT=${TO_SLOT:-$((FROM_SLOT + 432000))}
num TO_SLOT "$TO_SLOT"
WS=$((FROM_SLOT - WARMUP * 432000))
[ "$WARMUP" -le 1 ] || die "WARMUP must be 0 or 1 (as the clean build)"
[ $((FROM_SLOT / 432000)) = "$EPOCH" ] || die "FROM_SLOT $FROM_SLOT is not in epoch $EPOCH"
[ $((FROM_SLOT % 216000)) = 0 ] && [ $((TO_SLOT % 216000)) = 0 ] && [ "$FROM_SLOT" -lt "$TO_SLOT" ] \
  || die "the window [$FROM_SLOT, $TO_SLOT) must be whole chunks"
[ "$WS" -ge 446256000 ] && [ "$TO_SLOT" -le 452304000 ] || die "dev epochs 1033-1046 only, never the hold-out"
V1=$DATA/events/v1/chunks
WINDOW_ROOT=${WINDOW_ROOT:-$V1}
DEV2=${DEV2:-$DATA/store/dev2.duckdb}
OUT_TAG=${OUT_TAG:-}
case $OUT_TAG in *[!a-z0-9]*) die "OUT_TAG: lower-case letters and digits only" ;; esac
[ -d "$V1" ] && [ -d "$WINDOW_ROOT" ] || die "no chunk root $V1 or $WINDOW_ROOT"
V1=$(realpath "$V1") WINDOW_ROOT=$(realpath "$WINDOW_ROOT")
case $WINDOW_ROOT/ in *clean* | *holdout*) die "WINDOW_ROOT $WINDOW_ROOT: a clean or hold-out root" ;; esac
[ -n "$OUT_TAG" ] || { [ "$WINDOW_ROOT" = "$V1" ] && [ "$WARMUP" = 1 ]; } \
  || die "the core run (no OUT_TAG) is WARMUP=1 with WINDOW_ROOT=events/v1"
[ -f "$DEV2" ] || die "no dev2 store at $DEV2"
case $(realpath "$DEV2") in "$(realpath "$DATA/store")"/*) ;; *) die "DEV2 must lie under $DATA/store" ;; esac
DIAG_C=$(git -C "$DIAG" rev-parse HEAD) || die "$DIAG is not a git checkout"
TAG=store-warmup-diag-${DIAG_C:0:7}-$EPOCH${OUT_TAG:+-$OUT_TAG}
OUT=$(realpath -m "$DATA/backtests/$TAG")
SPILL=$(realpath -m "${LAB_SPILL_DIR:-/lab/tmp/$TAG}")
TMP1=$(realpath -m /lab/tmp) TMP2=$(realpath -m "$DATA/tmp")  # the spill roots allowed
[ -d "$DATA/backtests" ] || die "no $DATA/backtests"
case $OUT in */events/* | */store/* | *clean* | *holdout*) die "output $OUT" ;; esac
case $SPILL in "$TMP1"/?* | "$TMP2"/?*) ;; *) die "LAB_SPILL_DIR $SPILL must lie under /lab/tmp/ or $DATA/tmp/" ;; esac
case $SPILL in */events/* | */store/* | *clean* | *holdout*) die "spill $SPILL" ;; esac
[ ! -e "$OUT" ] || die "$OUT exists: every run writes a new directory"
[ ! -e "$SPILL" ] || die "$SPILL exists: the spill is new and removed at the end"
mkdir "$OUT"
mkdir -p "$SPILL/build" "$SPILL/diff"
export LAB_CHUNKS=$V1 LAB_CLEAN_CHUNKS=$WINDOW_ROOT LAB_SPILL_DIR=$SPILL/build LAB_SPILL_MAX=${LAB_SPILL_MAX:-60GB}
export LAB_DUCKDB_MEM=${LAB_DUCKDB_MEM:-8GB} LAB_DUCKDB_THREADS=${LAB_DUCKDB_THREADS:-4} LAB_POOL_PARTS=${LAB_POOL_PARTS:-4}
export PYTHONDONTWRITEBYTECODE=1
DIFF_MEM=${DIFF_MEM:-6GB} KEEP_S1=${KEEP_S1:-0}
REDS=""

log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
logfile() { while IFS= read -r l; do log "  $l"; done < "$1"; }
sums() { (cd "$OUT" && ls | grep -v -e '\.duckdb' -e '^SHA256SUMS$' | xargs -r sha256sum > SHA256SUMS); }
clean_spill() {
  case $SPILL in "$TMP1"/?* | "$TMP2"/?*) rm -rf -- "$SPILL" ;; *) log "spill $SPILL not removed (unexpected path)" ;; esac
}
red() {  # the reason; partial stores go, finished ones stay for a follow-up diff
  trap - ERR
  log "RED: $*"
  rm -f -- "$OUT/s0.duckdb.building" "$OUT/s0.duckdb.building.wal" "$OUT/s1.duckdb.building" "$OUT/s1.duckdb.building.wal"
  for s in "$OUT/s0.duckdb" "$OUT/s1.duckdb"; do [ ! -e "$s" ] || log "kept for follow-up: $s ($(stat -c %s "$s") bytes)"; done
  clean_spill
  sums
  exit 1
}
trap 'red "unexpected failure (line $LINENO)"' ERR
sha16() { sha256sum "$1" | cut -c1-16; }
run_diff() {  # report name, store_diff.py arguments; run from the be47fc4 tree (C4 imports its strategies.py)
  local name=$1
  shift
  (cd "$NEW/lab" && PYTHONPATH=$NEW/lab "$PY" -P "$DIAG/lab/backtest/store_diff.py" "$@" --out "$OUT/$name.json" \
    --spill "$SPILL/diff" --memory "$DIFF_MEM" --threads "$LAB_DUCKDB_THREADS") > "$OUT/$name.log" 2>&1
}

log "start: $0 $*"
log "command: NEW=$NEW OLD=$OLD EPOCH=$EPOCH FROM_SLOT=$FROM_SLOT TO_SLOT=$TO_SLOT WARMUP=$WARMUP WINDOW_ROOT=$WINDOW_ROOT" \
  "DEV2=$DEV2 OUT_TAG=$OUT_TAG LAB_PY=$PY DATA=$DATA"
log "env: LAB_DUCKDB_MEM=$LAB_DUCKDB_MEM LAB_DUCKDB_THREADS=$LAB_DUCKDB_THREADS LAB_POOL_PARTS=$LAB_POOL_PARTS" \
  "LAB_SPILL_DIR=$LAB_SPILL_DIR LAB_SPILL_MAX=$LAB_SPILL_MAX DIFF_MEM=$DIFF_MEM KEEP_S1=$KEEP_S1" \
  "LAB_BUILD_MAX_CHUNK_GB=${LAB_BUILD_MAX_CHUNK_GB:-unset}"
log "warm-up [$WS, $FROM_SLOT), window [$FROM_SLOT, $TO_SLOT), out $OUT"

# Code: the pinned trees, clean, and the files the builds and C4 import.
for r in "$NEW" "$OLD" "$DIAG"; do
  [ -z "$(git -C "$r" status --porcelain)" ] || red "$r has local changes"
done
NEW_C=$(git -C "$NEW" rev-parse HEAD) OLD_C=$(git -C "$OLD" rev-parse HEAD)
log "HEAD: diag $DIAG_C ($DIAG), new $NEW_C, old $OLD_C"
[ "$NEW_C" = "$NEW_SHA" ] || red "NEW is $NEW_C, not be47fc4"
[ "$OLD_C" = "$OLD_SHA" ] || red "OLD is $OLD_C, not ba322be"
for f in "$NEW/lab/backtest/store.py 28edae50864a947e" "$NEW/lab/backtest/strategies.py a3c2dfc77beedc38" \
  "$NEW/lab/backtest/data.py 5ea7765cbf8cca4d" "$OLD/lab/backtest/store.py 5bfa9c660610d127" \
  "$OLD/lab/backtest/data.py 5ea7765cbf8cca4d" "$NEW/lab/backtest/pda.py 007f3c1d50314824" \
  "$OLD/lab/backtest/pda.py 007f3c1d50314824" "$NEW/lab/backtest/health.py 520ec81215c05577" \
  "$OLD/lab/backtest/health.py 520ec81215c05577"; do
  set -- $f
  [ "$(sha16 "$1")" = "$2" ] || red "$1: sha256 $(sha16 "$1")..., not $2..."
done
log "code: store.py 28edae50/5bfa9c66, strategies.py a3c2dfc7, data.py, pda.py and health.py as pinned"
DV=$("$PY" -P -c 'import duckdb; print(duckdb.__version__)') || red "$PY: no duckdb, or no -P (Python 3.11 or later)"
log "python $("$PY" -V 2>&1), duckdb $DV"
[ "$DV" = "$DUCKDB_VERSION" ] || red "duckdb $DV, not $DUCKDB_VERSION"
[ ! -e "$DATA/locks/disk-low" ] || red "$DATA/locks/disk-low exists (disk guard)"

# Inputs: dev2 is ba322be's dev store; the chunks are the ones dev2 was built from (the VPS manifests, committed).
run_diff dev2-meta meta "$DEV2" --expect-period dev --expect-commit ba322be60d25 \
  || red "dev2 is not ba322be's dev store (dev2-meta.log)"
log "dev2: $(stat -c %s "$DEV2") bytes; $(tail -1 "$OUT/dev2-meta.log")"
REF=$DIAG/lab/backtest/dev_chunk_manifests.json
if [ "$WARMUP" = 1 ]; then
  run_diff inputs-warmup inputs --ref "$REF" --root "$V1" --lo "$WS" --hi "$FROM_SLOT" \
    || red "the warm-up chunks differ from dev2's (inputs-warmup.log)"
  logfile "$OUT/inputs-warmup.log"
fi
rc=0
run_diff inputs-window inputs --ref "$REF" --root "$WINDOW_ROOT" --lo "$FROM_SLOT" --hi "$TO_SLOT" || rc=$?
logfile "$OUT/inputs-window.log"
if [ "$WINDOW_ROOT" = "$V1" ]; then
  [ "$rc" = 0 ] || red "window chunks differ from dev2's (inputs-window.log)"
else
  log "window root $WINDOW_ROOT: its manifests are another build's (informational, rc $rc)"
fi

# S1: be47fc4's store.py through the clean code path.
(cd "$NEW/lab" && PYTHONPATH=$NEW/lab CODE_LAB=$NEW/lab STORE_SHA=28edae50864a947e PERIOD_LABEL=dev-diag-clean-path \
  FROM_SLOT=$FROM_SLOT TO_SLOT=$TO_SLOT WARMUP=$WARMUP WINDOW_ROOT=$WINDOW_ROOT \
  "$PY" - "$OUT/s1.duckdb" "$OUT/chunks.json" >> "$OUT/build-s1.log" 2>&1 <<'EOF'
import hashlib, json, os, sys

import duckdb

from backtest import data, health, store


def need(ok, msg):
    if not ok:
        raise SystemExit(f"REFUSED: {msg}")


lab = os.path.realpath(os.environ["CODE_LAB"])
for m in (store, data, health):
    need(os.path.realpath(m.__file__).startswith(lab + os.sep), f"{m.__name__} from {m.__file__}, not from {lab}")
src = os.path.realpath(store.__file__)
with open(src, "rb") as f:
    sha = hashlib.sha256(f.read()).hexdigest()
print("STORE_PY", src, sha, "duckdb", duckdb.__version__, flush=True)
need(sha.startswith(os.environ["STORE_SHA"]), f"store.py sha256 {sha}")
health.wait_ok = lambda *a, **k: 0  # as the u64 proof: the VPS IO-health wait does not apply on the NAS
E = store.HOLDOUT_WARMUP_SLOTS
lo, hi, w = int(os.environ["FROM_SLOT"]), int(os.environ["TO_SLOT"]), int(os.environ["WARMUP"])
need(w in (0, 1) and 446_256_000 <= lo - w * E and lo < hi <= 452_304_000, "dev epochs 1033-1046 only")
v1, root = os.path.realpath(data.root()), os.path.realpath(os.environ["WINDOW_ROOT"])
store.CLEAN = (lo, hi)  # in-process only: the dev window passes period_range's clean guard and caps clean_window_end
store.CLEAN_CHUNKS = os.environ["WINDOW_ROOT"]
cfg = store.load_defaults()
ch = store.period_chunks("clean", cfg, lo, None, w)  # to_slot None, as the clean build: clean_window_end ends the window
win = [c for c in ch if c[0] >= ch[0][0] + w * E]
warm = [c for c in ch if c not in win]
need(ch[0][0] == lo - w * E and ch[-1][1] == hi and win and win[0][0] == lo, f"chunks {[c[:2] for c in ch]}")
for c in ch:
    real = os.path.realpath(c[2])
    need("clean" not in real and "holdout" not in real, f"chunk {real}")
    need(446_256_000 <= c[0] and c[1] <= 452_304_000, f"chunk {c[:2]} outside the dev epochs")
need(all(os.path.realpath(c[2]).startswith(v1 + os.sep) for c in warm), "a warm-up chunk not from events/v1")
need(all(os.path.realpath(c[2]).startswith(root + os.sep) for c in win), "a window chunk not from WINDOW_ROOT")
shas = sorted({str(store._chunk_meta(c)["extractor_sha256"]) for c in win})  # main()'s check of the clean window
need(len(shas) == 1 and shas[0] != "None", f"the window's chunks must come from one extractor build; found {shas}")
need(not os.path.exists(store.DISK_LOW), f"{store.DISK_LOW} exists (disk guard)")
gb, free = store._disk_check(os.path.dirname(sys.argv[1]), ch)
print(f"{len(ch)} chunks [{ch[0][0]}, {ch[-1][1]}), {len(warm)} warm-up; ~{gb:.0f} GB needed, {free:.0f} GB free", flush=True)
with open(sys.argv[2], "w") as f:
    json.dump([[c[0], c[1], os.path.realpath(c[2])] for c in ch], f)
extra = {"purpose": "DEV DIAGNOSTIC (ii): the clean store's code path on a dev window; not a clean store",
         "diagnostic": True, "warmup_epochs": w, "window": [lo, hi], "clean_patch": [lo, hi],
         "clean_extractor_sha256": shas[0], "window_root": root, "warmup_chunks": [store._chunk_meta(c) for c in warm]}
meta = store.build(os.environ["PERIOD_LABEL"], sys.argv[1], cfg, ch, log=lambda m: print(m, flush=True), extra_meta=extra)
print("META", json.dumps({k: meta.get(k) for k in ("period", "counts", "excluded_pools_int64", "slot_start",
                                                     "slot_end_exclusive", "window", "ms_per_slot", "git_commit")}), flush=True)
EOF
) || red "the S1 build failed or refused (build-s1.log: $(tail -1 "$OUT/build-s1.log"))"
log "S1: $(grep '^STORE_PY' "$OUT/build-s1.log" | tail -1)"
log "S1: $(grep '^META' "$OUT/build-s1.log" | tail -1 | cut -c1-600)"

if [ -z "$OUT_TAG" ]; then
  # S0: ba322be's store.py, period dev, over S1's chunks.
  (cd "$OLD/lab" && PYTHONPATH=$OLD/lab CODE_LAB=$OLD/lab STORE_SHA=5bfa9c660610d127 \
    "$PY" - "$OUT/s0.duckdb" "$OUT/chunks.json" >> "$OUT/build-s0.log" 2>&1 <<'EOF'
import hashlib, json, os, sys

import duckdb

from backtest import data, health, store


def need(ok, msg):
    if not ok:
        raise SystemExit(f"REFUSED: {msg}")


lab = os.path.realpath(os.environ["CODE_LAB"])
for m in (store, data, health):
    need(os.path.realpath(m.__file__).startswith(lab + os.sep), f"{m.__name__} from {m.__file__}, not from {lab}")
src = os.path.realpath(store.__file__)
with open(src, "rb") as f:
    sha = hashlib.sha256(f.read()).hexdigest()
print("STORE_PY", src, sha, "duckdb", duckdb.__version__, flush=True)
need(sha.startswith(os.environ["STORE_SHA"]), f"store.py sha256 {sha}")
health.wait_ok = lambda *a, **k: 0
with open(sys.argv[2]) as f:
    want = json.load(f)
cfg = store.load_defaults()
ch = store.period_chunks("dev", cfg, want[0][0], want[-1][1])
need([[c[0], c[1], os.path.realpath(c[2])] for c in ch] == want, "S0's dev chunks are not S1's")
need(all(446_256_000 <= c[0] and c[1] <= 452_304_000 for c in ch), "dev epochs 1033-1046 only")
need(not os.path.exists(store.DISK_LOW), f"{store.DISK_LOW} exists (disk guard)")
gb, free = store._disk_check(os.path.dirname(sys.argv[1]), ch)
print(f"{len(ch)} chunks [{ch[0][0]}, {ch[-1][1]}); ~{gb:.0f} GB needed, {free:.0f} GB free", flush=True)
meta = store.build("dev", sys.argv[1], cfg, ch, log=lambda m: print(m, flush=True))
print("META", json.dumps({k: meta.get(k) for k in ("period", "counts", "excluded_pools_int64", "slot_start",
                                                     "slot_end_exclusive", "ms_per_slot", "git_commit")}), flush=True)
EOF
  ) || red "the S0 build failed or refused (build-s0.log: $(tail -1 "$OUT/build-s0.log"))"
  log "S0: $(grep '^STORE_PY' "$OUT/build-s0.log" | tail -1)"
  log "S0: $(grep '^META' "$OUT/build-s0.log" | tail -1 | cut -c1-600)"
  # ba322be writes no excluded_pools_int64: a value here means S0 ran another store.py
  grep '^META' "$OUT/build-s0.log" | tail -1 | grep -q '"excluded_pools_int64": null' || red "S0 did not run ba322be's store.py"
fi

# The comparisons (GREEN rule: store_diff.py's docstring). Each runs; any RED makes the job RED.
for step in c1 c2 c4; do
  rc=0
  case $step in
    c1) [ -z "$OUT_TAG" ] || continue
        run_diff c1 full "$OUT/s1.duckdb" "$OUT/s0.duckdb" --excluded-zero a || rc=$? ;;
    c2) run_diff c2 window "$OUT/s1.duckdb" "$DEV2" --lo "$FROM_SLOT" --hi "$TO_SLOT" || rc=$? ;;
    c4) run_diff c4 triggers "$OUT/s1.duckdb" "$DEV2" --lo "$FROM_SLOT" --hi "$TO_SLOT" --code-lab "$NEW/lab" \
          --strategies-sha256 a3c2dfc77beedc38 --rows "$OUT/c4-triggers.csv" || rc=$? ;;
  esac
  log "$step: rc $rc"
  logfile "$OUT/$step.log"
  [ "$rc" = 0 ] || REDS="$REDS $step"
done
[ -z "$REDS" ] || red "comparisons RED:$REDS (reports $OUT/<step>.json)"
rm -f -- "$OUT/s0.duckdb" "$OUT/s0.duckdb.wal"
if [ "$KEEP_S1" = 1 ]; then
  log "S1 kept: $OUT/s1.duckdb"
else
  rm -f -- "$OUT/s1.duckdb" "$OUT/s1.duckdb.wal"
fi
clean_spill
log "done: GREEN"
sums  # after job.log's last line
