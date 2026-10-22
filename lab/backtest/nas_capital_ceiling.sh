#!/bin/bash
# The capital ceiling on dev (research/capital-ceiling-plan-2026-10-23.md, sha256 2a17f2d2; exploratory, no new rules) on
# the NAS, the lab runner (kort-lang, ~1-1.5 h est.): one tournament run on dev2 (F7 pool_reversed; tp4_T600, hold300,
# flow300_tp100_H6; sizes 0.5, 1, 1.25, 2, 2.5, 5, 10 SOL; d 2 and 3; q 0.5; --no-report), then backtest.capital_ceiling
# on its rows: the candidates K3, H5 and C3 at C = 10 / 25 / 100 SOL under paper's risk rules scaled with C, the size
# curves and the sandwich flag. Dev only: the store must be dev2's window [446,256,000, 452,304,000).
#
#   LAB_PY=/lab/venv/bin/python bash lab/backtest/nas_capital_ceiling.sh
#
# Needs $DATA/store/dev2.duckdb and the C3 keys $C3_KEYS (default $DATA/wallets/paidflow-dev-vps/markout-f7rev-txdelay.parquet,
# sha256 1a274ec6..., copied from the VPS). With $DATA/backtests/families-v2-1033-1046 present, the parity of tp4_T600 at
# 0.5 / 1 / 2 SOL, d 2 and 3 (rows and SOL/day) is checked against it (RED on a difference); without it, INFO.
# Overrides: WORKERS (4), C3_KEYS, PARITY_REF, SIZES and DELAYS (dry runs only), RANGE (a dry run's lo:hi; never evidence).
# Writes $DATA/backtests/capital-ceiling-<commit7>/ (parts/, config.json, job.log, tournament.log, ceiling.log,
# ceiling/ceiling.json and ceiling.md, SHA256SUMS).
set -euo pipefail
export LC_ALL=C  # the same sort order and tool output on the VPS and in the NAS container

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
RUN=capital-ceiling-${COMMIT:0:7}
OUT=$DATA/backtests/$RUN
mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
log "start commit=$COMMIT out=$OUT"
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
STORE=$DATA/store/dev2.duckdb
C3_KEYS=${C3_KEYS:-$DATA/wallets/paidflow-dev-vps/markout-f7rev-txdelay.parquet}
PARITY_REF=${PARITY_REF:-$DATA/backtests/families-v2-1033-1046}
SIZES=${SIZES:-0.5,1,1.25,2,2.5,5,10}
DELAYS=${DELAYS:-2,3}
WORKERS=${WORKERS:-4}
[ -f "$STORE" ] || { log "RED: no store at $STORE"; exit 1; }
[ -f "$C3_KEYS" ] || { log "RED: no C3 keys at $C3_KEYS (copy wallets/paidflow-dev-vps/markout-f7rev-txdelay.parquet from the VPS)"; exit 1; }
export PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1 LAB_STORE=$STORE LAB_BACKTESTS=$DATA/backtests LAB_ENGINE_COMMIT=$COMMIT
export LAB_SPILL_DIR=${LAB_SPILL_DIR:-/lab/tmp/capital-ceiling} LAB_SPILL_MAX=${LAB_SPILL_MAX:-24GB}
mkdir -p "$LAB_SPILL_DIR"
cd "$REPO/lab"

# Dev only: the store's window before anything reads it.
rc=0
"$PY" - "$STORE" >> "$OUT/job.log" 2>&1 <<'EOF' || rc=$?
import json, sys
import duckdb
from backtest.capital_ceiling import DEV
meta = json.loads(duckdb.connect(sys.argv[1], read_only=True).execute("SELECT json FROM meta").fetchone()[0])
w = (meta.get("slot_start"), meta.get("slot_end_exclusive"))
print(f"store window {list(w)}")
sys.exit(0 if w == DEV else f"the store's window {list(w)} is not dev2's {list(DEV)}")
EOF
[ "$rc" = 0 ] || { log "RED: the store is not dev2 (job.log)"; exit 1; }
log "store $STORE: dev2's window"

rc=0
"$PY" -m pytest -q -p no:cacheprovider backtest/tests/test_capital_ceiling.py backtest/tests/test_settle_sql.py > "$OUT/pytest.log" 2>&1 || rc=$?
tail -1 "$OUT/pytest.log" | tee -a "$OUT/job.log"
[ "$rc" = 0 ] || { log "RED: tests failed (rc $rc)"; exit 1; }

ARGS=(--families F7 --segments pool_reversed --variants tp4_T600,hold300,flow300_tp100_H6 --f7-grid v1,exits,big
      --sizes "$SIZES" --delays "$DELAYS" --q 0.5 --tau empirical --tol 0.05 --no-report --workers "$WORKERS" --run-id "$RUN")
[ -z "${RANGE:-}" ] || { ARGS+=(--range "$RANGE"); log "DRY RUN: --range $RANGE (not evidence)"; }
rc=0
nice -n 10 "$PY" -m backtest.tournament "${ARGS[@]}" > "$OUT/tournament.log" 2>&1 || rc=$?
tail -2 "$OUT/tournament.log" | tee -a "$OUT/job.log"
[ "$rc" = 0 ] || { log "RED: the tournament failed (rc $rc)"; exit 1; }

PAR=()
if [ -f "$PARITY_REF/config.json" ]; then PAR=(--parity "$PARITY_REF"); else log "INFO: no parity reference at $PARITY_REF (check it on the VPS)"; fi
rc=0
"$PY" -m backtest.capital_ceiling --parts "$OUT" --c3-keys "$C3_KEYS" --store "$STORE" --out "$OUT/ceiling" --memory "${MEMORY:-8GB}" \
  --threads 4 "${PAR[@]}" > "$OUT/ceiling.log" 2>&1 || rc=$?
tail -1 "$OUT/ceiling.log" | tee -a "$OUT/job.log"
(cd "$OUT" && sha256sum ceiling/ceiling.json ceiling/ceiling.md config.json > SHA256SUMS)
[ "$rc" = 0 ] || { log "RED: capital_ceiling rc $rc (a parity difference or a failure; ceiling.log)"; exit 1; }
log "done: $OUT/ceiling/ceiling.md"
