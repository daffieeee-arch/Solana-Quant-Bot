#!/bin/bash
# Entry tolerance sweep on dev (research/tol-sweep-plan-2026-10-24.md, sha256 35ca706b; E70; exploratory, in-sample) on the
# NAS lab runner (lang, ~1.3 h est.): three tournament runs on dev2 (F7 pool_reversed; flow300_tp100_H6 and hold300;
# 0.5 / 1.25 SOL; d 3; q 0.5; --no-report) at --tol 0.02 / 0.10 / 0.15, then backtest.tol_sweep with E63's run as the
# tol 0.05 cells and the gates T1-T3. The engine's tolerance is one parameter for the entry and the exit, as paper's.
#
#   LAB_PY=/lab/venv/bin/python bash lab/backtest/nas_tol_sweep.sh
#
# Safeguards (as nas_racing_cost.sh, plus T0: the engine files and week2-defaults.yaml equal E63's d15ac45): dev only (the store's meta window and max block slot, a clean store path refused,
# check_run on every run); the inputs sha256-pinned; the job starts only at or before START_DEADLINE (default Sat
# 2026-10-24T08:00:00Z) and its process group is killed at KILL_AT (default 2026-10-24T09:45:00Z: the NAS is the
# Saturday clean runs' from 10:00Z); its own spill dir under /lab/tmp/ or $DATA/tmp/, removed on every exit path.
# Needs, copied from the VPS where missing (each pinned): E63's run $E63 (default $DATA/backtests/capital-ceiling-d15ac45:
# parts/ and config.json c5fcd78e...), its report $E63_JSON (default $E63/ceiling-report-b149a7d/ceiling.json, 8adfda64...)
# and the C3 keys $C3_KEYS (default $DATA/wallets/paidflow-dev-vps/markout-f7rev-txdelay.parquet, 1a274ec6...).
# Overrides: WORKERS (4), MEMORY (DuckDB, 8GB), START_DEADLINE, KILL_AT, RANGE (a dry run's lo:hi for the three tournament
# runs: never evidence; every run dir gets -dry).
# Writes $DATA/backtests/tolsweep-t002-<commit7>/, -t010-, -t015- (the tournament runs) and $DATA/backtests/tol-sweep-<commit7>/
# (job.log, pytest.log, report/tol.json + tol.md, SHA256SUMS). Any RED: rc 1.
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
START_DEADLINE=${START_DEADLINE:-2026-10-24T08:00:00Z}
KILL_AT=${KILL_AT:-2026-10-24T09:45:00Z}
HOLDOUT_START=452304000
SFX=${COMMIT:0:7}
[ -z "${RANGE:-}" ] || SFX=$SFX-dry
OUT=$DATA/backtests/tol-sweep-$SFX
SIZES=0.5,1.25
mkdir -p "$OUT"
export PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1 LAB_STORE=$STORE LAB_BACKTESTS=$DATA/backtests LAB_ENGINE_COMMIT=$COMMIT
export LAB_SPILL_DIR=${LAB_SPILL_DIR:-/lab/tmp/tol-sweep} LAB_SPILL_MAX=${LAB_SPILL_MAX:-24GB}
# The spill dir is removed on every exit path: only a dir under /lab/tmp/ or $DATA/tmp/ (LAB review of #536).
case "$LAB_SPILL_DIR" in
  /lab/tmp/?*|"$DATA"/tmp/?*) ;;
  *) echo "RED: LAB_SPILL_DIR $LAB_SPILL_DIR is not under /lab/tmp/ or $DATA/tmp/" | tee -a "$OUT/job.log"; exit 1;;
esac
clean_spill() { rm -rf -- "${LAB_SPILL_DIR:?}"; }
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
red() { log "RED: $*"; clean_spill; exit 1; }

# ---------------------------------------------------------------- the pipeline (in its own process group, see below)
if [ "${1:-}" = "--pipeline" ]; then
  mkdir -p "$LAB_SPILL_DIR"
  cd "$REPO/lab"
  rc=0
  "$PY" -m pytest -q -p no:cacheprovider backtest/tests/test_tol_sweep.py backtest/tests/test_racing_cost.py \
    backtest/tests/test_capital_ceiling.py backtest/tests/test_settle_sql.py > "$OUT/pytest.log" 2>&1 || rc=$?
  log "tests: $(tail -1 "$OUT/pytest.log")"
  [ "$rc" = 0 ] || red "tests failed (rc $rc)"
  grep -Eq "^[0-9]+ passed" <(tail -1 "$OUT/pytest.log") || red "no 'N passed' line"

  # One tournament run per tolerance; a resume continues the same grid only (the tournament resumes by part index alone).
  tourney() {  # run-id tol
    local dir=$DATA/backtests/$1
    if [ -f "$dir/config.json" ]; then
      rc=0
      "$PY" - "$dir/config.json" "$SIZES" "$2" "${RANGE:-}" >> "$OUT/job.log" 2>&1 <<'EOF' || rc=$?
import json, sys
c = json.load(open(sys.argv[1]))
want = ([float(x) for x in sys.argv[2].split(",")], [3], [0.5], float(sys.argv[3]), [int(x) for x in sys.argv[4].split(":")] if sys.argv[4] else None)
got = (c.get("sizes_sol"), c.get("delays"), c.get("q"), c.get("slippage_tol"), c.get("slot_range"))
sys.exit(0 if got == want else f"the existing run has sizes, delays, q, tol, range {got}, not {want}")
EOF
      [ "$rc" = 0 ] || red "$dir holds another grid (job.log): use a new commit or remove it"
      log "resuming $dir (same grid)"
    fi
    local args=(--families F7 --segments pool_reversed --variants flow300_tp100_H6,hold300 --f7-grid v1,exits,big
                --sizes "$SIZES" --delays 3 --q 0.5 --tau empirical --tol "$2" --no-report --workers "${WORKERS:-4}" --run-id "$1")
    [ -z "${RANGE:-}" ] || { args+=(--range "$RANGE"); log "DRY RUN: --range $RANGE (not evidence)"; }
    mkdir -p "$dir"
    rc=0
    nice -n 10 "$PY" -m backtest.tournament "${args[@]}" >> "$dir/tournament.log" 2>&1 || rc=$?
    tail -2 "$dir/tournament.log" | tee -a "$OUT/job.log"
    [ "$rc" = 0 ] || red "tournament $1 failed (rc $rc; $dir/tournament.log)"
  }
  tourney "tolsweep-t002-$SFX" 0.02
  tourney "tolsweep-t010-$SFX" 0.10
  tourney "tolsweep-t015-$SFX" 0.15

  PARTS=("$DATA/backtests/tolsweep-t002-$SFX" "$DATA/backtests/tolsweep-t010-$SFX" "$DATA/backtests/tolsweep-t015-$SFX" "$E63")
  rm -rf "$OUT/report"
  rc=0
  nice -n 10 "$PY" -m backtest.tol_sweep --parts "${PARTS[@]}" --e63-json "$E63_JSON" --c3-keys "$C3_KEYS" --out "$OUT/report" \
    --memory "${MEMORY:-8GB}" --threads 4 > "$OUT/report.log" 2>&1 || rc=$?
  tail -1 "$OUT/report.log" | tee -a "$OUT/job.log"
  [ -f "$OUT/report/tol.md" ] || red "report rc $rc and no tol.md (report.log)"
  (cd "$OUT" && sha256sum report/tol.json report/tol.md "../tolsweep-t002-$SFX/config.json" "../tolsweep-t010-$SFX/config.json" \
     "../tolsweep-t015-$SFX/config.json" > SHA256SUMS)
  [ "$rc" = 0 ] || red "a gate is RED (report rc $rc; tol.md, report.log)"
  clean_spill
  log "done: $OUT/report/tol.md"
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
ls "$E63"/parts/*.parquet > /dev/null 2>&1 || red "no parts in $E63"
# T0 (LAB review of the plan): the engine files and the cost defaults are E63's (d15ac45), byte for byte, before any run.
rc=0
(cd "$REPO/lab" && "$PY" -m backtest.tol_sweep --check-engine) >> "$OUT/job.log" 2>&1 || rc=$?
[ "$rc" = 0 ] || red "T0: the engine or the cost defaults differ from E63's d15ac45 (job.log): rerun tol 0.05 instead of reusing it"
log "guards: dev2 only, inputs pinned, T0 GREEN, E63 $(ls "$E63"/parts/*.parquet | wc -l) parts"

setsid bash "$0" --pipeline &
PG=$!
(
  # The sleep runs as a child the watcher kills when it is stopped, so no sleep outlives a normal finish holding the
  # inherited descriptors until KILL_AT (coordinator, NAS 99zzzzz58).
  sleep $((kill_at - now)) &
  SP=$!
  trap 'kill "$SP" 2>/dev/null; exit 0' TERM
  wait "$SP"
  log "KILL_AT $KILL_AT reached: killing process group $PG"
  kill -TERM -- "-$PG" 2>/dev/null || true
  sleep 30
  kill -KILL -- "-$PG" 2>/dev/null || true
  clean_spill
) &
WATCH=$!
trap 'kill -TERM -- "-$PG" 2>/dev/null || true; kill "$WATCH" 2>/dev/null || true; clean_spill' INT TERM
rc=0
wait "$PG" || rc=$?
kill "$WATCH" 2>/dev/null || true
[ "$rc" = 0 ] || red "pipeline rc $rc (killed at the deadline or failed; see $OUT/job.log)"
log "finished rc 0"
