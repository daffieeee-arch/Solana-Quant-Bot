#!/bin/bash
# Regime inputs for LAB research's step 1 §2.5 (coordinator, 24 Oct): wallets.regime_inputs on the live recorder's
# segments for one window, the files of wallets/regime-1064-inputs (E67/E68) by the same routes. Read-only on the
# segments and the journal; TRIGGER AND EVENT COUNTS ONLY, no P&L. Light to medium (the reversed-pool rows of the window
# plus 60,000 slots of history in DuckDB: the explore window of 33 h, 6.1 M reversed-pool rows, took about 2.5 min on the
# VPS at 2 GB and 2 threads; a 3-day window is about 3 times that).
#
#   SEGMENTS=$DATA/recorder-archive/full-coverage-2026-10-24_27/segments FROM_UTC=2026-10-24T00:23:00Z \
#   TO_UTC=2026-10-27T00:05:00Z LABEL=fullcov-20261024 OUT=$DATA/wallets/regime-1066-inputs \
#   LAB_PY=/lab/venv/bin/python bash lab/wallets/nas_regime_inputs.sh
#
# Optional: JOURNAL=<journal.sqlite> (the paper journal, mode=ro: its F7 signals and the (pool, t) comparison),
# CLOCK=epoch|window (default epoch), MEMORY (DuckDB, default 8GB), THREADS (4), LAB_SPILL_DIR (a spill ROOT under /lab/tmp
# or $DATA/tmp, default /lab/tmp: the job makes its own new directory in it with mktemp and removes only that one).
# Steps: guards (OUT new and never inside SEGMENTS; SEGMENTS exists) -> test_regime_inputs.py -> the run -> sha256sum -c
# of OUT's SHA256SUMS. Logs in $DATA/wallets/regime-inputs-logs/<LABEL>-<commit7>-<utc>/ (job.log, pytest.log, run.log).
# Any RED: rc 1.
set -euo pipefail
export LC_ALL=C

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
for v in SEGMENTS FROM_UTC TO_UTC LABEL OUT; do
  [ -n "${!v:-}" ] || { echo "RED: set $v" >&2; exit 1; }
done
case "$LABEL" in *[!A-Za-z0-9_-]*) echo "RED: LABEL: letters, digits, - and _ only" >&2; exit 1 ;; esac
LOGD=$DATA/wallets/regime-inputs-logs/$LABEL-${COMMIT:0:7}-$(date -u +%Y%m%dT%H%M%SZ)  # one per invocation: a refused rerun never touches a run's logs
SPILL_ROOT=$(realpath -m "${LAB_SPILL_DIR:-/lab/tmp}")
SPILL=  # set by mktemp below; only that directory is ever removed
[ ! -e "$LOGD" ] || { echo "RED: $LOGD exists" >&2; exit 1; }
mkdir -p "$LOGD"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOGD/job.log"; }
clean_spill() { if [ -n "$SPILL" ] && [ -d "$SPILL" ]; then rm -rf -- "$SPILL"; fi; }
red() { log "RED: $*"; clean_spill; exit 1; }
log "start commit=$COMMIT command: SEGMENTS=$SEGMENTS FROM_UTC=$FROM_UTC TO_UTC=$TO_UTC LABEL=$LABEL OUT=$OUT JOURNAL=${JOURNAL:-} CLOCK=${CLOCK:-epoch} MEMORY=${MEMORY:-8GB} THREADS=${THREADS:-4} SPILL_ROOT=$SPILL_ROOT"
[ -d "$SEGMENTS" ] || red "no segments directory $SEGMENTS"
[ ! -e "$OUT" ] || red "$OUT exists: the inputs are written once (move it aside)"
case "$(realpath -m "$OUT")/" in "$(realpath -m "$SEGMENTS")"/*) red "OUT may not be inside SEGMENTS" ;; esac
case "$SPILL_ROOT/" in /lab/tmp/*|"$(realpath -m "$DATA/tmp")"/*) ;; *) red "LAB_SPILL_DIR $SPILL_ROOT is not /lab/tmp or $DATA/tmp or under them" ;; esac
[ -z "${JOURNAL:-}" ] || [ -f "$JOURNAL" ] || red "no journal at $JOURNAL"
mkdir -p "$SPILL_ROOT"
SPILL=$(mktemp -d "$SPILL_ROOT/regime-inputs-$LABEL-XXXXXX") || red "no spill directory in $SPILL_ROOT"
log "spill $SPILL"

rc=0
(cd "$REPO/lab" && PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1 LAB_STORE=/nonexistent/dev2.duckdb "$PY" -P -m pytest -q -p no:cacheprovider \
  wallets/tests/test_regime_inputs.py) > "$LOGD/pytest.log" 2>&1 || rc=$?
log "tests: $(tail -1 "$LOGD/pytest.log")"
[ "$rc" = 0 ] || red "tests failed (rc $rc; pytest.log)"

args=(--segments "$SEGMENTS" --from-utc "$FROM_UTC" --to-utc "$TO_UTC" --label "$LABEL" --out "$OUT" --clock "${CLOCK:-epoch}"
      --memory "${MEMORY:-8GB}" --threads "${THREADS:-4}")
[ -z "${JOURNAL:-}" ] || args+=(--journal "$JOURNAL")
rc=0
(cd "$REPO/lab" && PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1 LAB_SPILL_DIR=$SPILL nice -n 10 "$PY" -P -m wallets.regime_inputs "${args[@]}") \
  > "$LOGD/run.log" 2>&1 || rc=$?
tail -4 "$LOGD/run.log" | tee -a "$LOGD/job.log"
[ "$rc" = 0 ] || red "regime_inputs rc $rc (run.log)"
(cd "$OUT" && sha256sum -c --quiet SHA256SUMS) >> "$LOGD/job.log" 2>&1 || red "SHA256SUMS of $OUT does not verify"
clean_spill
log "done: $OUT ($(wc -l < "$OUT/SHA256SUMS") files)"
(cd "$LOGD" && sha256sum job.log pytest.log run.log > SHA256SUMS)
