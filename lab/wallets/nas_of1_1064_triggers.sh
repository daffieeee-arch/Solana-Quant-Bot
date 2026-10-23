#!/bin/bash
# Trigger supply, step 2 (coordinator 2026-10-23, roadmap #167): ONE UTC day of OF1 epoch 1064 (2026-10-22, slots
# 459,823,752-460,230,895, the explore window's full day) extracted on the NAS, a forward store (illustration only) built
# from it, and the dev tournament's F7 trigger function counted on it against the live recorder's triggers and probe pools
# of the same hours (wallets/regime-1064-inputs/, synced from the VPS). TRIGGER COUNTS ONLY.
#
#   !!! Epoch 1064 is the future forward window for H5/C3. This job reads trigger counts and pool counts, never a fill,
#   !!! an exit, a return or any P&L. Nobody reads anything else from the day's store without a pre-registration.
#
#   STEP=check (kort, ~10 min): the first 1,000 slots of the day through the extractor (Alpenglow format check): the
#              chunk must be complete, and its blocks / pump creates / graduations must be >= the recorder's counts on the
#              same slots (check-chunk-recorder.json: 1,000 blocks, 142 mints, 2 graduations); unknown-reward counts logged.
#   STEP=day   (lang, est. 4-6 h): the whole day in 16 chunks of 27,000 slots into $DATA/events/fwd1064/chunks (resumable:
#              complete chunks are skipped), then `backtest.store forward --illustration` into
#              $DATA/store/fwd1064-day-<commit7>.duckdb, then wallets.of1_1064.count_triggers into
#              $DATA/wallets/of1-1064-triggers-<commit7>/ (count_triggers.json + job.log), sha256 of every output.
#
#   BIN=/lab/bin/lab-extractor-<commit7> STEP=check LAB_PY=/lab/venv/bin/python bash lab/wallets/nas_of1_1064_triggers.sh
#   BIN=/lab/bin/lab-extractor-<commit7> STEP=day   LAB_PY=/lab/venv/bin/python bash lab/wallets/nas_of1_1064_triggers.sh
#
# Runs only after Saturday's clean runs: START_NOT_BEFORE (default 2026-10-24T12:45:00Z, after E56) is enforced.
# Extraction uses the NAS ops' smoke_extract.sh per chunk (BIN, SLOTS=a:b, D=<log dir>, OUT=<chunk dir>), as the venue census
# job did (nas_venue_census.sh); if ops prefer extract_epochs.sh (whole epoch 1064), point CHUNKS at its root instead and the
# store/readout still use only the day's slots. Tunables: MEMORY (DuckDB for the store build, default 8GB), THREADS (3),
# LAB_SPILL_MAX (40GB), CHUNK (27000), START_NOT_BEFORE, OPS (/lab/ops), INPUTS.
set -euo pipefail
export LC_ALL=C

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
OPS=${OPS:-/lab/ops}
: "${STEP:?STEP=check or day}"
case "$STEP" in check | day) ;; *) echo "unknown STEP=$STEP"; exit 2 ;; esac
DAY_LO=459823752; DAY_HI=460230895          # 2026-10-22 00:00:00Z .. 23:59:59Z (live blocks), epoch 1064 only
CHECK_HI=$((DAY_LO + 1000))
CHUNK=${CHUNK:-27000}
CHUNKS=${CHUNKS:-$DATA/events/fwd1064/chunks}
INPUTS=${INPUTS:-$DATA/wallets/regime-1064-inputs}
OUT=$DATA/wallets/of1-1064-triggers-${COMMIT:0:7}
STORE=$DATA/store/fwd1064-day-${COMMIT:0:7}.duckdb
START_NOT_BEFORE=${START_NOT_BEFORE:-2026-10-24T12:45:00Z}
mkdir -p "$OUT" "$CHUNKS"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
red() { log "RED: $*"; exit 1; }
log "start $STEP commit=$COMMIT out=$OUT chunks=$CHUNKS store=$STORE"
log "RULE: epoch 1064 is the future forward window for H5/C3 -- trigger counts only, never P&L"
[ "$(date -u +%s)" -ge "$(date -u -d "$START_NOT_BEFORE" +%s)" ] || red "not before $START_NOT_BEFORE (Saturday's clean runs go first)"
[ $((DAY_LO / 432000)) = 1064 ] && [ $(((DAY_HI - 1) / 432000)) = 1064 ] || red "window outside epoch 1064"
case "$CHUNKS$STORE" in *clean*) red "a clean path is named";; esac
: "${BIN:?BIN=/lab/bin/lab-extractor-<commit7>, this commit build}"
[ -x "$BIN" ] || red "no extractor at $BIN"
log "extractor $BIN sha256 $(sha256sum "$BIN" | cut -c1-16)"
for f in live-triggers-explore.parquet probe-pools-explore.parquet check-chunk-recorder.json SHA256SUMS; do
  [ -f "$INPUTS/$f" ] || red "input $INPUTS/$f missing (sync wallets/regime-1064-inputs from the VPS)"
done
(cd "$INPUTS" && LC_ALL=C sha256sum -c --quiet SHA256SUMS) || red "inputs do not match their SHA256SUMS"
export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$REPO/lab LAB_CHUNKS=$CHUNKS LAB_STORE_DIR=$DATA/store
export LAB_SPILL_DIR=${LAB_SPILL_DIR:-$DATA/tmp/of1-1064-spill} LAB_SPILL_MAX=${LAB_SPILL_MAX:-40GB}
export LAB_DUCKDB_MEM=${MEMORY:-8GB} LAB_DUCKDB_THREADS=${THREADS:-3}
mkdir -p "$LAB_SPILL_DIR"

extract() {  # extract <lo> <hi> <dir>: one chunk through ops' smoke_extract.sh; complete manifest required
  local lo=$1 hi=$2 dir=$3 rc=0
  if [ -f "$dir/_manifest.json" ] && grep -q '"status": "complete"' "$dir/_manifest.json"; then log "chunk $lo-$hi complete, skipped"; return 0; fi
  rm -rf "$dir"; mkdir -p "$OUT/extract"
  BIN=$BIN SLOTS="$lo:$hi" D="$OUT/extract/$lo-$hi" OUT="$dir" THREADS=${THREADS:-3} bash "$OPS/smoke_extract.sh" > "$OUT/extract/$lo-$hi.log" 2>&1 || rc=$?
  [ "$rc" = 0 ] || { tail -5 "$OUT/extract/$lo-$hi.log" | tee -a "$OUT/job.log"; red "extract $lo-$hi rc $rc"; }
  grep -q '"status": "complete"' "$dir/_manifest.json" 2>/dev/null || red "chunk $lo-$hi has no complete manifest"
  log "chunk $lo-$hi complete: $(tr -d '\n' < "$dir/_manifest.json" | cut -c1-300)"
}

check_chunk() {  # the Alpenglow format check on the first 1,000 slots, against the recorder's counts
  "$PY" - "$1" "$INPUTS/check-chunk-recorder.json" <<'PYEOF'
import duckdb, glob, json, sys
d, ref = sys.argv[1], json.load(open(sys.argv[2]))["check"]
c = duckdb.connect(); c.execute("SET memory_limit='1GB'")
def n(t, expr="count(*)"):
    fs = glob.glob(f"{d}/{t}/*.parquet")
    return c.execute(f"SELECT {expr} FROM read_parquet({fs!r}, union_by_name=true)").fetchone()[0] if fs else 0
man = json.load(open(f"{d}/_manifest.json"))
got = {"blocks": n("blocks"), "creates_mints": n("pump/CreateEvent", "count(DISTINCT mint)"),
       "graduations_pools": n("pump/CompletePumpAmmMigrationEvent", "count(DISTINCT pool)") or n("pump_amm/CreatePoolEvent", "count(DISTINCT pool)"),
       "pump_amm_buy_sell_rows": n("pump_amm/BuyEvent") + n("pump_amm/SellEvent"), "footers_or_unknown_rewards": {k: v for k, v in man.items() if "reward" in k.lower() or "footer" in k.lower() or "skipped" in k.lower()}}
print("check chunk", got, "recorder reference", ref)
bad = [k for k in ("blocks", "creates_mints", "graduations_pools") if got[k] < ref[k]]
bad += [k for k in ("blocks", "pump_amm_buy_sell_rows") if got[k] == 0]
if got["pump_amm_buy_sell_rows"] < ref["pump_amm_buy_sell_rows_seen"]: bad.append("pump_amm rows below the recorder's subset")
print("FORMAT CHECK", "GREEN" if not bad else f"RED {bad}")
sys.exit(0 if not bad else 1)
PYEOF
}

if [ "$STEP" = check ]; then
  extract "$DAY_LO" "$CHECK_HI" "$CHUNKS/$DAY_LO-$CHECK_HI"
  check_chunk "$CHUNKS/$DAY_LO-$CHECK_HI" 2>&1 | tee -a "$OUT/job.log" || red "format check failed (see job.log)"
  log "done check: the ≥ 1064 format decodes; STEP=day may run"
  exit 0
fi

# STEP=day: the chunks tile [DAY_LO, DAY_HI) exactly (the check chunk is re-extracted as part of the first full chunk)
for ((s = DAY_LO; s < DAY_HI; s += CHUNK)); do
  e=$((s + CHUNK < DAY_HI ? s + CHUNK : DAY_HI))
  extract "$s" "$e" "$CHUNKS/$s-$e"
done
n_complete=$("$PY" -c "import json,glob,sys; print(sum(1 for m in glob.glob(sys.argv[1]+'/*/_manifest.json') if json.load(open(m))['status']=='complete'))" "$CHUNKS")
log "complete chunks in $CHUNKS: $n_complete"
[ ! -e "$STORE" ] || red "$STORE exists (built once; remove by hand after a failed build)"
rc=0
(cd "$REPO/lab" && "$PY" -m backtest.store forward --illustration --from-slot "$DAY_LO" --to-slot "$DAY_HI" --out "$STORE") > "$OUT/store-build.log" 2>&1 || rc=$?
[ "$rc" = 0 ] || { tail -10 "$OUT/store-build.log" | tee -a "$OUT/job.log"; red "store build rc $rc"; }
log "store built: $(tail -1 "$OUT/store-build.log" | cut -c1-200)"
rc=0
(cd "$REPO/lab" && "$PY" -m wallets.of1_1064.count_triggers --store "$STORE" --from-slot "$DAY_LO" --to-slot "$DAY_HI" --inputs "$INPUTS" --out "$OUT" --memory "$LAB_DUCKDB_MEM") > "$OUT/count_triggers.log" 2>&1 || rc=$?
[ "$rc" = 0 ] || { tail -10 "$OUT/count_triggers.log" | tee -a "$OUT/job.log"; red "count_triggers rc $rc"; }
log "count_triggers: $(grep -E 'OF1-only / both / live-only' "$OUT/count_triggers.log" | tail -1)"
(cd "$OUT" && LC_ALL=C sha256sum count_triggers.json count_triggers.log store-build.log job.log 2>/dev/null | LC_ALL=C sort -k2 > SHA256SUMS; sha256sum "$STORE" > store.sha256)
rm -rf "$LAB_SPILL_DIR"
log "done day: counts only; the store $STORE stays closed for anything but counts (forward window of H5/C3)"
