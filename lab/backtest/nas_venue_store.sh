#!/bin/bash
# The venue store (venue decoders PR 3, backtest/venue_store.py; spec research/venue-decoders-spec-2026-10-21.md v0.2)
# on the NAS, the lab runner. Old Faithful history only: nothing is sent or traded.
#
#   STEP=smoke (kort): a forward store from #513's smoke chunk (1054, slots 455,328,000-455,329,000; illustration only)
#              into $DATA/backtests/venue-store-<commit7>/smoke.duckdb.
#   STEP=epoch (kort-lang): the dev store of epoch 1045 from events/venues-v2/chunks (99zzzzz4, eb6d8ca) into
#              $DATA/store/venues-1045-<commit7>.duckdb (built once, never grown; its sha256 next to it).
#   GREEN only when the build ends and every check of the store's meta is green: DBC chained share <= 5 %, CPMM
#   balance rows <= 1 %, graduations to DAMM v2 / CPMM linked >= 99 %, LaunchLab continuity >= 99 %. The counts per
#   table and the checks are printed; meta.json is written next to the store.
#
#   STEP=smoke LAB_PY=/lab/venv/bin/python bash lab/backtest/nas_venue_store.sh
#   STEP=epoch LAB_PY=/lab/venv/bin/python bash lab/backtest/nas_venue_store.sh
#
# Overrides for a dry run (never for the evidence): SMOKE_ROOT, VENUES_ROOT, MEMORY (DuckDB, default 6GB), THREADS.
set -euo pipefail
export LC_ALL=C  # the same sort order and tool output on the VPS and in the NAS container

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
: "${STEP:?STEP=smoke or epoch}"
case "$STEP" in smoke | epoch) ;; *) echo "unknown STEP=$STEP"; exit 2 ;; esac
OUT=$DATA/backtests/venue-store-${COMMIT:0:7}
mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
log "start $STEP commit=$COMMIT out=$OUT"
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
SMOKE_ROOT=${SMOKE_ROOT:-$DATA/backtests/venues-eb6d8ca/smoke-on}   # #513's smoke chunk (its dir is smoke-on/chunk)
VENUES_ROOT=${VENUES_ROOT:-$DATA/events/venues-v2/chunks}
MEMORY=${MEMORY:-6GB}
THREADS=${THREADS:-2}
export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$REPO/lab LAB_DUCKDB_TMP=${LAB_DUCKDB_TMP:-$DATA/tmp-duckdb}
mkdir -p "$LAB_DUCKDB_TMP"

if [ "$STEP" = smoke ]; then
  args=(forward --chunks "$SMOKE_ROOT" --from-slot 455328000 --to-slot 455329000 --out "$OUT/smoke.duckdb")
  store=$OUT/smoke.duckdb
else
  store=$DATA/store/venues-1045-${COMMIT:0:7}.duckdb
  args=(dev --chunks "$VENUES_ROOT" --from-slot 451440000 --to-slot 451872000 --out "$store")
fi
[ ! -e "$store" ] || { log "RED: $store exists (a venue store is built once)"; exit 1; }
rc=0
"$PY" -m backtest.venue_store "${args[@]}" --memory "$MEMORY" --threads "$THREADS" > "$OUT/$STEP-build.log" 2>&1 || rc=$?
tail -12 "$OUT/$STEP-build.log" | tee -a "$OUT/job.log"
[ -f "$store" ] || { log "RED: no store written (rc $rc)"; exit 1; }
"$PY" - "$store" "$OUT/$STEP-meta.json" <<'EOF' | tee -a "$OUT/job.log"
import json, sys
import duckdb
con = duckdb.connect(sys.argv[1], read_only=True)
meta = json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
json.dump(meta, open(sys.argv[2], "w"), indent=1)
print("counts:", meta["counts"])
for c in meta["checks"]:
    print(f"check {c['check']}: {c['value']} (limit {c.get('limit')}) {c['status'].upper()}")
for t in ("dbc", "damm2", "launchlab", "cpmm"):
    print(t, dict(con.execute(f"SELECT {'reserve_source' if t == 'dbc' else ('kind' if t != 'launchlab' else 'status')}::VARCHAR, count(*) FROM {t} GROUP BY 1 ORDER BY 1").fetchall()))
print("venue_pools", dict(con.execute("SELECT venue, count(*) FROM venue_pools GROUP BY 1 ORDER BY 1").fetchall()))
print("venue_graduations", con.execute("SELECT to_venue, count(*), count(*) FILTER (WHERE linked) FROM venue_graduations GROUP BY 1 ORDER BY 1").fetchall())
EOF
if [ "$STEP" = epoch ]; then
  (cd "$(dirname "$store")" && sha256sum "$(basename "$store")" > "$(basename "$store").sha256")
  log "store $(cat "$store.sha256")"
fi
[ "$rc" = 0 ] || { log "RED: venue_store rc $rc (a red check or a failure; see $OUT/$STEP-build.log)"; exit 1; }
log "$STEP: GREEN"
