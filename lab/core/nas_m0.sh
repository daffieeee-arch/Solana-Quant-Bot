#!/bin/bash
# M0 of the Rust live route (lab/docs/rust-live-route-design-2026-10-16.md §12) on the NAS: lab-core's venue math
# must equal backtest/venues.py exactly. Two steps, because Rust and the lab Python live in different runners:
#
#   STEP=build   (bookworm runner, Rust 1.97.1): cargo test --release --locked of lab/core (unit tests and
#                proptest), then the abi3-py313 wheel of lab/core-py with maturin 1.15, into $OUT/wheel/.
#   STEP=python  (the lab Python 3.13 runner): the wheel into $OUT/site (not into the shared venv), then
#                a. backtest/tests/test_venues_core.py: Python against Rust on 20,000 random and edge cases per
#                   function, exactly equal;
#                b. the venue tests under LAB_CORE=rust (test_round_trip; test_venues_real where its chunk exists);
#                c. the A/B: one dev tournament with LAB_CORE unset and once with LAB_CORE=rust (curve and pool
#                   math both run), then the parts compared with EXCEPT ALL both ways. Green: the same row
#                   count, 0 rows only on one side.
#
#   STEP=build  bash lab/core/nas_m0.sh
#   STEP=python bash lab/core/nas_m0.sh
#
# Both steps write to $DATA/backtests/core-m0-<commit7>/ (job.log, wheel/, site/, ab.txt).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
OUT=$DATA/backtests/core-m0-${COMMIT:0:7}
mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
: "${STEP:?STEP=build or STEP=python}"
log "start $STEP commit=$COMMIT out=$OUT"

if [ "$STEP" = build ]; then
  cd "$REPO/lab/core"
  cargo test --release --locked 2>&1 | tee -a "$OUT/job.log" | tail -5
  log "cargo test: ok"
  W=$(mktemp -d)
  python3 -m venv "$W/m" && "$W/m/bin/pip" install -q maturin==1.15.0
  "$W/m/bin/maturin" build --release --locked -m "$REPO/lab/core-py/Cargo.toml" -o "$OUT/wheel" 2>&1 | tee -a "$OUT/job.log" | tail -3
  rm -rf "$W"
  ls -l "$OUT/wheel" | tee -a "$OUT/job.log"
  log "build: done"
  exit 0
fi

[ "$STEP" = python ] || { log "unknown STEP=$STEP"; exit 2; }
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
WHEEL=$(ls "$OUT"/wheel/labcore-*.whl | head -1)
rm -rf "$OUT/site" && "$PY" -m pip install -q --no-deps --target "$OUT/site" "$WHEEL"
export PYTHONPATH=$REPO/lab:$OUT/site PYTHONDONTWRITEBYTECODE=1
cd "$REPO/lab"
"$PY" -c "import labcore; print('labcore', labcore.__version__, labcore.__file__)" | tee -a "$OUT/job.log"

# a. Python against Rust, exactly.
env -u LAB_CORE "$PY" -m pytest -q backtest/tests/test_venues_core.py 2>&1 | tee -a "$OUT/job.log" | tail -3
# b. The venue tests with lab-core underneath.
LAB_CORE=rust "$PY" -m pytest -q backtest/tests/test_round_trip.py backtest/tests/test_venues_real.py 2>&1 | tee -a "$OUT/job.log" | tail -3

# c. The A/B: the same dev tournament with the Python and the Rust venue math.
export LAB_BACKTESTS=$OUT/runs LAB_ENGINE_COMMIT=$COMMIT
for core in python rust; do
  t0=$(date +%s) rc=0
  LAB_CORE=$core "$PY" -m backtest.tournament --families F7,F1,N2 --sizes 0.5 --q 0.5 --tau empirical --delays 1,2 \
    --f7-grid v1,exits,long,big --decide 449300000:449513000 --no-report --workers 4 --run-id ab-$core > "$OUT/$core.log" 2>&1 || rc=$?
  log "$core rc=$rc $(( $(date +%s) - t0 ))s $(tail -1 "$OUT/$core.log")"
  [ "$rc" = 0 ] || exit "$rc"
done
"$PY" - "$OUT/runs" <<'PYEOF' | tee "$OUT/ab.txt" | tee -a "$OUT/job.log"
import json, sys
import duckdb
runs = sys.argv[1]
con = duckdb.connect(config={"memory_limit": "4GB"})
q = lambda r: f"SELECT * FROM read_parquet('{runs}/{r}/parts/*.parquet', union_by_name = true)"
n = [con.execute(f"SELECT count(*) FROM ({q(r)})").fetchone()[0] for r in ("ab-python", "ab-rust")]
d1 = con.execute(f"SELECT count(*) FROM ({q('ab-python')} EXCEPT ALL {q('ab-rust')})").fetchone()[0]
d2 = con.execute(f"SELECT count(*) FROM ({q('ab-rust')} EXCEPT ALL {q('ab-python')})").fetchone()[0]
math = [json.load(open(f"{runs}/{r}/config.json"))["venue_math"] for r in ("ab-python", "ab-rust")]
print(f"A/B venue math {math[0]} vs {math[1]}: {n[0]} / {n[1]} rows, only python {d1}, only rust {d2}: "
      + ("GREEN" if n[0] == n[1] and d1 == d2 == 0 and n[0] > 0 and math == ["python", "rust"] else "RED"))
PYEOF
log "python: done"
