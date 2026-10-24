#!/bin/bash
# NAS (98-clean-5): E20's K3 rows from its locked output, read only, for LAB research's E20 report
# (backtest/clean_k3_rows.py). Admitted: the store and runs of $DATA/store/clean.lock (store sha256 and engine
# checked), the exclusion list (sha256), result/result.json (read only: every count and mean must agree). Writes only
# $DATA/research/clean-test/clean-be47fc4/k3-rows/ (k3-rows.parquet, counts.json, SHA256SUMS; written once, renamed
# from .tmp) and this job's log under $DATA/backtests/clean-k3-rows-<commit7>/. Never result/ or the lock.
#
#   EXCLUSIONS_SHA=<E20's exclusion list sha256> LAB_PY=/lab/venv/bin/python bash lab/backtest/nas_clean_k3_rows.sh
#
# Defaults are E20's (24 Oct): ENGINE=be47fc4, STORE_SHA=e8db6e8d, EXPECT=4424,867,3557 (filled, rt_tx, no rt_tx),
# EXPECT_FAILED=425. Short: the store's sha256, then one pass over the runs' parts and the flags. Any RED: rc 1.
set -euo pipefail
export LC_ALL=C
REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
: "${EXCLUSIONS_SHA:?sha256 of E20 exclusion list}"
ENGINE=${ENGINE:-be47fc4}
STORE_SHA=${STORE_SHA:-e8db6e8d}
EXPECT=${EXPECT:-4424,867,3557}
EXPECT_FAILED=${EXPECT_FAILED:-425}
LOCK=${LOCK:-$DATA/store/clean.lock}
EXCL=${EXCL:-$DATA/research/case-exclusions-clean-data.md}
[ "${#ENGINE}" -ge 7 ] || { echo "ENGINE: at least 7 characters"; exit 1; }
TESTDIR=$DATA/research/clean-test/clean-${ENGINE:0:7}  # as nas_clean_test.sh named it (the commit, 7)
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
LOGDIR=$DATA/backtests/clean-k3-rows-${COMMIT:0:7}
mkdir -p "$LOGDIR"
# This job's own spill directory (removed at the end), and E20's DuckDB memory (nas_clean_test: 8GB; speed only).
export LAB_SPILL_DIR=$DATA/tmp/clean-k3-rows LAB_DUCKDB_MEM=${LAB_DUCKDB_MEM:-8GB} PYTHONDONTWRITEBYTECODE=1
mkdir -p "$LAB_SPILL_DIR"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOGDIR/job.log"; }
red() { log "RED: $*"; exit 1; }
log "start commit=$COMMIT lock=$LOCK out=$TESTDIR/k3-rows engine=$ENGINE store=$STORE_SHA expect=$EXPECT failed=$EXPECT_FAILED"
[ -f "$TESTDIR/result/result.json" ] || red "no $TESTDIR/result/result.json"
[ -e "$TESTDIR/k3-rows" ] && red "$TESTDIR/k3-rows exists: written once"
rc=0
(cd "$LOGDIR" && env -u PYTHONPATH "$PY" -P "$REPO/lab/backtest/clean_k3_rows.py" --lock "$LOCK" --exclusions "$EXCL" \
   --exclusions-sha256 "$EXCLUSIONS_SHA" --result "$TESTDIR/result/result.json" --out "$TESTDIR/k3-rows" \
   --store-sha256 "$STORE_SHA" --engine "$ENGINE" --expect "$EXPECT" --expect-failed "$EXPECT_FAILED") \
  >> "$LOGDIR/job.log" 2>&1 || rc=$?
[ "$rc" = 0 ] || red "the extract was refused or failed (rc $rc, job.log)"
(cd "$TESTDIR/k3-rows" && sha256sum -c SHA256SUMS) >> "$LOGDIR/job.log" 2>&1 || red "SHA256SUMS does not verify"
rm -rf "$LAB_SPILL_DIR"
log "done: GREEN ($TESTDIR/k3-rows; SHA256SUMS $(sha256sum "$TESTDIR/k3-rows/SHA256SUMS" | cut -c1-16))"
