#!/bin/bash
# UI job template "backtest-f7" (spec v0.3 §5): one F7 variant and K3 (tp4_T600) on the same store period, the
# tournament with the job's size, delay and segment, decisions in the job's whole epochs; then the result
# (uijobs/result.py): net P&L per trade and Σ per day with the 10,000-draw day bootstrap, under the base costs.
# Pinned on the NAS by SHA256SUMS; run by nas_runner.py only. Every job value arrives as a UI_* environment value
# that schema.py validated (enums, bounded numbers, a ULID); nothing here is built from free text.
set -euo pipefail
: "${UI_ID:?}" "${UI_VARIANT:?}" "${UI_GRID:?}" "${UI_K3:?}" "${UI_K3_GRID:?}" "${UI_SEGMENT:?}" "${UI_FARM:?}"
: "${UI_SIZE:?}" "${UI_DMS:?}" "${UI_STORE_FILE:?}" "${UI_FROM_SLOT:?}" "${UI_TO_SLOT:?}" "${UI_OUT:?}" "${UI_LIB:?}"
: "${LAB_PY:?the lab venv python}" "${LAB_CODE:?the pinned code checkout}" "${LAB_STORE_DIR:?the stores}"
[[ $UI_ID =~ ^[0-9A-HJKMNP-TV-Z]{26}$ ]] || { echo "bad id"; exit 2; }   # defence in depth; schema.py checked it

export PYTHONPATH=$LAB_CODE/lab PYTHONDONTWRITEBYTECODE=1
export LAB_STORE=$LAB_STORE_DIR/$UI_STORE_FILE LAB_BACKTESTS=$UI_OUT/run LAB_TRIALS=$UI_OUT/trials
export LAB_DUCKDB_MEM=6GB LAB_DUCKDB_THREADS=3 LAB_SPILL_DIR=$UI_OUT/spill LAB_SPILL_MAX=20GB
mkdir -p "$LAB_BACKTESTS" "$LAB_TRIALS" "$LAB_SPILL_DIR"
grids=$UI_GRID
[[ $UI_K3_GRID == "$UI_GRID" ]] || grids=$UI_GRID,$UI_K3_GRID
echo "$(date -u +%FT%TZ) ui job $UI_ID: $UI_VARIANT vs $UI_K3, $UI_SEGMENT, farm $UI_FARM, $UI_SIZE SOL, ${UI_DMS} ms, $UI_STORE_FILE $UI_FROM_SLOT:$UI_TO_SLOT"
"$LAB_PY" -m backtest.tournament --families F7 --sizes "$UI_SIZE" --delay-ms "$UI_DMS" --variants "$UI_VARIANT,$UI_K3" \
  --f7-grid "$grids" --segments "$UI_SEGMENT" --decide "$UI_FROM_SLOT:$UI_TO_SLOT" --no-report --workers 3 \
  --run-id "ui-$UI_ID" --batch "ui-$UI_ID"
"$LAB_PY" "$UI_LIB/result.py" --run "$LAB_BACKTESTS/ui-$UI_ID" --variant "$UI_VARIANT" --k3 "$UI_K3" --segment "$UI_SEGMENT" \
  --farm "$UI_FARM" --size "$UI_SIZE" --id "$UI_ID" > "$UI_OUT/result.json.tmp"
mv "$UI_OUT/result.json.tmp" "$UI_OUT/result.json"
rm -rf "$LAB_SPILL_DIR"
echo "$(date -u +%FT%TZ) done"
