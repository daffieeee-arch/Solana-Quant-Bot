#!/bin/bash
# V_ins recompute of the M1x tournament on dev with the fixed engine (#213, 0b9304e; coordinator 2026-10-13): the same
# command as m1x-dev-1033-1046-453d44e (data-old-faithful-one/lab/logs/m1x-dev-453d44e-nas-command.txt) on a new run id and
# the code of the fix, under the same pre-registered §10 rule; then the §10 evaluation and the old-vs-new comparison
# (flipped vetoes, insider_fallbacks).
#
#   X=<full sha of the fixed engine, containing 0b9304e> bash lab/wallets/nas_vins.sh
#
# Expects the NAS layout: the code export at /lab/code/<short sha>/lab, /lab/venv/bin/python, /lab/tmp, /lab/runs.
set -euo pipefail

X=${X:?full sha of the engine commit (must contain 0b9304e)}
S=${X:0:7}
L=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
PY=${LAB_PY:-/lab/venv/bin/python}
CODE=${LAB_CODE:-/lab/code/$S/lab}
RUN=${RUN:-m1x-dev-1033-1046-$S}
OLD=${OLD:-$L/backtests/m1x-dev-1033-1046-453d44e}
OUT=$L/backtests/$RUN
WORKERS=${WORKERS:-4}
mkdir -p "$OUT/engine" "/lab/tmp/$RUN" /lab/runs/m1x
log() { echo "$(date -Is) $*" | tee -a /lab/runs/m1x/run.log; }
if ! git -C "$CODE" merge-base --is-ancestor 0b9304e HEAD 2>/dev/null; then
  grep -q "0b9304e" "$CODE/../MANIFEST" 2>/dev/null || log "warning: cannot verify that $CODE contains 0b9304e (no git, no MANIFEST hit)"
fi
if [ ! -d "$OUT/engine/backtest" ]; then
  (cd "$CODE" && tar -c --exclude=__pycache__ backtest) | tar -x -C "$OUT/engine"
  echo "$X" > "$OUT/engine/COMMIT"
fi
log "vins recompute start run=$RUN engine=$X old=$OLD"
cd "$OUT/engine"
LAB_ENGINE_COMMIT=$X PYTHONUNBUFFERED=1 LAB_STORE=$L/store/dev2.duckdb LAB_SPILL_DIR=/lab/tmp/$RUN LAB_SPILL_MAX=${LAB_SPILL_MAX:-16GB} LAB_SQL_BUCKETS=64 \
  nice -n 10 "$PY" -m backtest.tournament --run-id "$RUN" --families M1P,M1E,N2 --sizes 0.25,0.5,1,2 --delays 1,2,3 \
  --only d=3:M1E,N2_M1E --q 0.5 --workers "$WORKERS" --batch m1x_dev >> "$OUT/run.log" 2>&1
log "tournament exit $?"
LAB_STORE=$L/store/dev2.duckdb "$PY" -m backtest.analysis m1x "$OUT" --store "$L/store/dev2.duckdb" >> "$OUT/run.log" 2>&1
log "m1x evaluation done: $OUT/m1x.md"
PYTHONPATH="$CODE" LAB_WALLETS_MEMORY=${REPORT_MEMORY:-4GB} "$PY" -m wallets.vins_compare --old "$OLD" --new "$OUT" --out "$OUT/vins-compare.md"
log "done: $OUT/vins-compare.md"
rm -rf "/lab/tmp/$RUN"
