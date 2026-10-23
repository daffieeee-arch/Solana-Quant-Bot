#!/bin/bash
# Racing cost on dev (research/racing-cost-plan-2026-10-23.md, sha256 342876de; E65; exploratory, in-sample) on the NAS lab
# runner (lang, ~1-1.5 h est.): two tournament runs on dev2 (F7 pool_reversed; flow300_tp100_H6 and hold300; 0.5 / 1.25 /
# 5 SOL; --no-report): A = d 0 at q 1.0 (the trigger slot's end state: first in t + 1), B = d 1, 5, 8, 13 at q 0.5; the
# race table (t + 1 token buys and the first buyer's fee from dev2.pool) over A, B and E63's run; then backtest.racing_cost
# report with the gates G1-G5. E63's d 2 and 3 come from capital-ceiling-d15ac45 unchanged (not rerun). Research only.
#
#   LAB_PY=/lab/venv/bin/python bash lab/backtest/nas_racing_cost.sh
#
# Safeguards (LAB review of the plan, should-fix 6):
#   1. dev only: the store's meta window is dev2's [446,256,000, 452,304,000) and its max block slot < 452,304,000; a store
#      path naming the clean store is refused; every run is checked by capital_ceiling.check_run (racing_cost refuses rows
#      outside dev); the inputs are sha256-pinned;
#   2. deadline: the job starts only at or before START_DEADLINE (default Sat 2026-10-24T07:30:00Z) and its whole process
#      group is killed at KILL_AT (default 2026-10-24T09:45:00Z): the NAS is the Saturday clean runs' from 10:00Z;
#   3. its own spill dir, removed at the end and on the kill path.
# Needs $DATA/store/dev2.duckdb and, copied from the VPS where missing (each pinned):
#   E63's run $E63 (default $DATA/backtests/capital-ceiling-d15ac45: parts/ and config.json c5fcd78e...) and its report
#   $E63_JSON (default $E63/ceiling-report-b149a7d/ceiling.json, 8adfda64...: main's capital_ceiling, gate G4);
#   the C3 keys $C3_KEYS (default $DATA/wallets/paidflow-dev-vps/markout-f7rev-txdelay.parquet, 1a274ec6...);
#   route (ii)'s rows $ROUTE_II (default $DATA/backtests/route-ii-full-547f422/entry-rows/part-000000.parquet, b4f4be7b...).
# Overrides: WORKERS (4), DRAWS (200), MEMORY (DuckDB, 8GB), START_DEADLINE, KILL_AT, RANGE (a dry run's lo:hi for the two
# tournament runs: never evidence; every run dir gets -dry).
# Writes $DATA/backtests/racing-d0-<commit7>/ and racing-dB-<commit7>/ (the tournament runs) and $DATA/backtests/racing-cost-<commit7>/
# (job.log, pytest.log, race/, report/racing.json + racing.md, SHA256SUMS). Any RED: rc 1.
set -euo pipefail
export LC_ALL=C  # the same sort order and tool output on the VPS and in the NAS container

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
STORE=$DATA/store/dev2.duckdb
E63=${E63:-$DATA/backtests/capital-ceiling-d15ac45}
E63_JSON=${E63_JSON:-$E63/ceiling-report-b149a7d/ceiling.json}
C3_KEYS=${C3_KEYS:-$DATA/wallets/paidflow-dev-vps/markout-f7rev-txdelay.parquet}
ROUTE_II=${ROUTE_II:-$DATA/backtests/route-ii-full-547f422/entry-rows/part-000000.parquet}
START_DEADLINE=${START_DEADLINE:-2026-10-24T07:30:00Z}
KILL_AT=${KILL_AT:-2026-10-24T09:45:00Z}
HOLDOUT_START=452304000
SFX=${COMMIT:0:7}
[ -z "${RANGE:-}" ] || SFX=$SFX-dry
RUN_A=racing-d0-$SFX
RUN_B=racing-dB-$SFX
OUT=$DATA/backtests/racing-cost-$SFX
SIZES=0.5,1.25,5
mkdir -p "$OUT"
export PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1 LAB_STORE=$STORE LAB_BACKTESTS=$DATA/backtests LAB_ENGINE_COMMIT=$COMMIT
export LAB_SPILL_DIR=${LAB_SPILL_DIR:-/lab/tmp/racing-cost} LAB_SPILL_MAX=${LAB_SPILL_MAX:-24GB}
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
red() { log "RED: $*"; rm -rf "$LAB_SPILL_DIR"; exit 1; }

# ---------------------------------------------------------------- the pipeline (in its own process group, see below)
if [ "${1:-}" = "--pipeline" ]; then
  mkdir -p "$LAB_SPILL_DIR"
  cd "$REPO/lab"
  rc=0
  "$PY" -m pytest -q -p no:cacheprovider backtest/tests/test_racing_cost.py backtest/tests/test_capital_ceiling.py \
    backtest/tests/test_settle_sql.py > "$OUT/pytest.log" 2>&1 || rc=$?
  log "tests: $(tail -1 "$OUT/pytest.log")"
  [ "$rc" = 0 ] || red "tests failed (rc $rc)"
  grep -Eq "^[0-9]+ passed" <(tail -1 "$OUT/pytest.log") || red "no 'N passed' line"

  # The two tournament runs; a resume continues the same grid only (the tournament resumes by part index alone).
  tourney() {  # run-id delays q
    local dir=$DATA/backtests/$1
    if [ -f "$dir/config.json" ]; then
      rc=0
      "$PY" - "$dir/config.json" "$SIZES" "$2" "$3" "${RANGE:-}" >> "$OUT/job.log" 2>&1 <<'EOF' || rc=$?
import json, sys
c = json.load(open(sys.argv[1]))
want = ([float(x) for x in sys.argv[2].split(",")], [int(x) for x in sys.argv[3].split(",")], [float(sys.argv[4])],
        [int(x) for x in sys.argv[5].split(":")] if sys.argv[5] else None)
got = (c.get("sizes_sol"), c.get("delays"), c.get("q"), c.get("slot_range"))
sys.exit(0 if got == want else f"the existing run has sizes, delays, q, range {got}, not {want}")
EOF
      [ "$rc" = 0 ] || red "$dir holds another grid (job.log): use a new commit or remove it"
      log "resuming $dir (same grid)"
    fi
    local args=(--families F7 --segments pool_reversed --variants flow300_tp100_H6,hold300 --f7-grid v1,exits,big
                --sizes "$SIZES" --delays "$2" --q "$3" --tau empirical --tol 0.05 --no-report --workers "${WORKERS:-4}" --run-id "$1")
    [ -z "${RANGE:-}" ] || { args+=(--range "$RANGE"); log "DRY RUN: --range $RANGE (not evidence)"; }
    mkdir -p "$dir"
    rc=0
    nice -n 10 "$PY" -m backtest.tournament "${args[@]}" >> "$dir/tournament.log" 2>&1 || rc=$?
    tail -2 "$dir/tournament.log" | tee -a "$OUT/job.log"
    [ "$rc" = 0 ] || red "tournament $1 failed (rc $rc; $dir/tournament.log)"
  }
  tourney "$RUN_A" 0 1.0
  tourney "$RUN_B" 1,5,8,13 0.5

  PARTS=("$DATA/backtests/$RUN_A" "$DATA/backtests/$RUN_B" "$E63")
  rm -rf "$OUT/race" "$OUT/report"
  rc=0
  nice -n 10 "$PY" -m backtest.racing_cost table --parts "${PARTS[@]}" --store "$STORE" --out "$OUT/race" --memory "${MEMORY:-8GB}" \
    --threads 4 > "$OUT/race.log" 2>&1 || rc=$?
  tail -1 "$OUT/race.log" | tee -a "$OUT/job.log"
  [ "$rc" = 0 ] || red "race table rc $rc (race.log)"
  rc=0
  nice -n 10 "$PY" -m backtest.racing_cost report --parts "${PARTS[@]}" --race "$OUT/race" --c3-keys "$C3_KEYS" --store "$STORE" \
    --route-ii "$ROUTE_II" --e63-json "$E63_JSON" --out "$OUT/report" --draws "${DRAWS:-200}" --workers "${WORKERS:-4}" \
    --memory "${MEMORY:-8GB}" --threads 4 > "$OUT/report.log" 2>&1 || rc=$?
  tail -1 "$OUT/report.log" | tee -a "$OUT/job.log"
  [ -f "$OUT/report/racing.md" ] || red "report rc $rc and no racing.md (report.log)"
  (cd "$OUT" && sha256sum race/race.parquet race/race_txs.parquet race/race.json report/racing.json report/racing.md \
     "../$RUN_A/config.json" "../$RUN_B/config.json" > SHA256SUMS)
  [ "$rc" = 0 ] || red "a gate is RED (report rc $rc; racing.md, report.log)"
  rm -rf "$LAB_SPILL_DIR"
  log "done: $OUT/report/racing.md"
  exit 0
fi

# ---------------------------------------------------------------- safeguards, then the pipeline under a deadline watcher
now=$(date -u +%s); start_by=$(date -u -d "$START_DEADLINE" +%s); kill_at=$(date -u -d "$KILL_AT" +%s)
log "start commit=$COMMIT out=$OUT store=$STORE start_deadline=$START_DEADLINE kill_at=$KILL_AT${RANGE:+ range=$RANGE}"
[ "$now" -le "$start_by" ] || red "start deadline $START_DEADLINE passed: the job runs after the Saturday clean runs (set START_DEADLINE and KILL_AT for that run)"
[ "$kill_at" -gt "$now" ] || red "KILL_AT $KILL_AT is in the past"
case "$STORE" in *clean*) red "the store path names the clean store: refused";; esac
[ -f "$STORE" ] || red "no store at $STORE"
rc=0
"$PY" - "$STORE" "$HOLDOUT_START" >> "$OUT/job.log" 2>&1 <<'EOF' || rc=$?
import json, sys
import duckdb
c = duckdb.connect(sys.argv[1], read_only=True)
m = json.loads(c.execute("SELECT json FROM meta").fetchone()[0])
w = (m.get("slot_start"), m.get("slot_end_exclusive"))
mx = c.execute("SELECT max(slot) FROM blocks").fetchone()[0]
print(f"store window {list(w)}, max block slot {mx}")
sys.exit(0 if w == (446256000, 452304000) and mx < int(sys.argv[2]) else f"not dev2: window {list(w)}, max block slot {mx}")
EOF
[ "$rc" = 0 ] || red "the store is not dev2 (job.log)"
pin() {  # file sha256 what
  [ -f "$1" ] || red "missing $3 at $1 (copy it from the VPS: same path under the data root)"
  [ "$(sha256sum "$1" | cut -d' ' -f1)" = "$2" ] || red "$3 at $1 is not sha256 $2"
}
pin "$E63/config.json" c5fcd78ef083b67fe0bb326bee4b546fe11d937354f83ed7b18dfa6607e246be "E63's run config"
pin "$E63_JSON" 8adfda647cf914c4c6c04537418726ea8a36280862777f120fea2cefa1de0c6e "E63's report (ceiling-report-b149a7d)"
pin "$C3_KEYS" 1a274ec60a5610f8abf22b70aed23266feaad8559fd79df77f35d8862fb10a23 "the C3 keys"
pin "$ROUTE_II" b4f4be7b09030075e8732b17e5f93a7bd9d26563505bd7999dea29e415106a5c "route (ii)'s rows"
ls "$E63"/parts/*.parquet > /dev/null 2>&1 || red "no parts in $E63"
log "guards: dev2 only, inputs pinned, E63 $(ls "$E63"/parts/*.parquet | wc -l) parts"

setsid bash "$0" --pipeline &
PG=$!
(
  sleep $((kill_at - now))
  log "KILL_AT $KILL_AT reached: killing process group $PG"
  kill -TERM -- "-$PG" 2>/dev/null || true
  sleep 30
  kill -KILL -- "-$PG" 2>/dev/null || true
  rm -rf "$LAB_SPILL_DIR"
) &
WATCH=$!
trap 'kill -TERM -- "-$PG" 2>/dev/null || true; kill "$WATCH" 2>/dev/null || true; rm -rf "$LAB_SPILL_DIR"' INT TERM
rc=0
wait "$PG" || rc=$?
kill "$WATCH" 2>/dev/null || true
[ "$rc" = 0 ] || red "pipeline rc $rc (killed at the deadline or failed; see $OUT/job.log)"
log "finished rc 0"
