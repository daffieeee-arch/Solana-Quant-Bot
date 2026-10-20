#!/bin/bash
# Seller buyback after F7 triggers on the NAS (dev only, descriptive; coordinator 2026-10-20 "edge first" priority 2):
# wallets.buyback over the K3 rows of the F7 dev run and the dev markouts, one scan of the store's pool legs restricted to
# the triggers' 15-minute windows (a hash join on pool with the slot range as a filter).
#
#   MEMORY=8GB THREADS=6 WORKERS=4 LAB_SPILL_DIR=/lab/tmp/nas-buyback LAB_SPILL_MAX=50GB bash lab/wallets/nas_buyback.sh   (short)
#
# Steps, logged in $OUT/log/nas_buyback.log; any failure RED (rc 1): the tests (wallets/tests/test_buyback.py); the run →
# $OUT/{buyback.parquet, buyback.md, buyback.json}; RED when the json shows 0 triggers, 0 trigger legs found or 0 buys;
# the sha256 of every output. Tunables (env): PARTS (default $DATA/backtests/f7rev-1033-1046/parts), MARKOUT_DIR
# (default $DATA/wallets/markout-v1, run f7rev), OUT (default $DATA/wallets/buyback-<commit7>), BOOT (10000).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
STORE=${LAB_STORE:-$DATA/store/dev2.duckdb}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
PARTS=${PARTS:-$DATA/backtests/f7rev-1033-1046/parts}
MK=${MARKOUT_DIR:-$DATA/wallets/markout-v1}
OUT=${OUT:-$DATA/wallets/buyback-${COMMIT:0:7}}
LOG=$OUT/log
export PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1 LAB_STORE=$STORE LAB_WALLETS_MEMORY=${MEMORY:-8GB} LAB_WALLETS_THREADS=${THREADS:-6} \
  LAB_REPORT_WORKERS=${WORKERS:-4} LAB_SPILL_DIR=${LAB_SPILL_DIR:-/lab/tmp/nas-buyback} LAB_SPILL_MAX=${LAB_SPILL_MAX:-50GB}
mkdir -p "$OUT" "$LOG" "$LAB_SPILL_DIR"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG/nas_buyback.log"; }
red() { log "RED: $*"; exit 1; }
log "start commit=$COMMIT store=$STORE parts=$PARTS markout=$MK out=$OUT memory=$LAB_WALLETS_MEMORY threads=$LAB_WALLETS_THREADS workers=$LAB_REPORT_WORKERS"
[ -f "$STORE" ] || red "no store at $STORE"
[ -d "$PARTS" ] || red "no parts at $PARTS"
[ -s "$MK/markout-f7rev.parquet" ] || red "no markout-f7rev.parquet in $MK"
cd "$REPO/lab"
rc=0
"$PY" -m pytest -q -p no:cacheprovider wallets/tests/test_buyback.py > "$LOG/pytest.log" 2>&1 || rc=$?
log "tests: $(tail -1 "$LOG/pytest.log")"
[ "$rc" = 0 ] || red "tests failed (rc $rc)"
rc=0
nice -n 10 "$PY" -u -m wallets.buyback --parts "$PARTS" --markout "$MK" --run f7rev --out "$OUT" --boot "${BOOT:-10000}" > "$LOG/buyback.log" 2>&1 || rc=$?
[ "$rc" = 0 ] || { tail -5 "$LOG/buyback.log" | tee -a "$LOG/nas_buyback.log"; red "buyback failed (rc $rc)"; }
"$PY" - "$OUT/buyback.json" >> "$LOG/nas_buyback.log" 2>&1 <<'PYEOF' || red "empty input: 0 triggers, trigger legs or buys"
import json, sys
j = json.load(open(sys.argv[1]))
t, i = j["triggers"], j["input"]
print(f"run: {t['n']:,} triggers by {t['sellers']:,} sellers over {t['days']} days, trigger leg found {t['trigger_leg_found']:,}, buys {i['buys']:,}, "
      f"markouts joined {i['markouts_joined']:,}, flags {j['flags']}, {i['seconds']} s")
sys.exit(0 if t["n"] > 0 and t["trigger_leg_found"] > 0 and i["buys"] > 0 and i["markouts_joined"] > 0 else 1)
PYEOF
tail -1 "$LOG/nas_buyback.log"
for f in buyback.parquet buyback.md buyback.json; do log "sha256 $f $(sha256sum "$OUT/$f" | cut -c1-64)"; done
log "done"
