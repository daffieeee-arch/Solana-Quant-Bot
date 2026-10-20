#!/bin/bash
# E49 proof on dev (roadmap #167), before the depth-band pre-registration freezes: the markout run at this commit over the
# dev f7rev parts (the same command as NAS run markout-v1-txdelay-v4: every family, --tx-delays) must (1) reproduce every
# existing column of that run's parquet bit for bit, (2) carry the new columns depth_entry / fee_bps / creator_prior_pools,
# with depth_entry equal to E48's reconstruction from impact and the fee tier, and (3) give E48's band row through
# wallets.depthband_report --d 2: 100-150 SOL at entry = 2,113 pools, 5,770 triggers, net 5 m +8.28 pt per pool.
#
#   bash lab/wallets/nas_depthband_proof.sh            (short: ~15 min; the v4 run took 790 s on the NAS)
#
# Steps, logged in $OUT/log/nas_depthband_proof.log; any failure RED (rc 1):
#   1. the tests: test_markout.py, test_depthband_report.py, test_mechanism_report.py (dev engine);
#   2. markout --tx-delays over $PARTS with the dev store and engine → $OUT/markout-f7rev-txdelay.parquet;
#   3. the comparison with $REF: row counts, the old columns set-equal both ways (EXCEPT ALL), the new columns present,
#      depth_entry vs the reconstruction 0.5 / ((1 + impact)(1 - fee) - 1) within 1e-6 on >= 99.9 % of the F7 pool_reversed entries;
#   4. depthband_report --d 2 → $OUT/depthband-dev.md + json; RED unless the band row is 2,113 pools / 5,770 triggers / +8.28 ± 0.05.
# Tunables (env): MEMORY (DuckDB limit, default 12GB), BUCKETS (32, as v4), THREADS (6), WORKERS (4), BOOT (10000), REF, PARTS, STORE,
# DEV_ENGINE, OUT (default $DATA/wallets/depthband-proof-<commit7>).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
OUT=${OUT:-$DATA/wallets/depthband-proof-${COMMIT:0:7}}
LOG=$OUT/log
PARTS=${PARTS:-$DATA/backtests/f7rev-1033-1046/parts}
STORE=${STORE:-$DATA/store/dev2.duckdb}
REF=${REF:-$DATA/wallets/markout-v1-txdelay-v4/markout-f7rev-txdelay.parquet}
DEV_ENGINE=${DEV_ENGINE:-$DATA/backtests/f7rev-1033-1046/engine}
export PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1 LAB_WALLETS_OUT=$DATA/wallets LAB_WALLETS_MEMORY=${MEMORY:-12GB} \
  LAB_WALLETS_THREADS=${THREADS:-6} LAB_MARKOUT_BUCKETS=${BUCKETS:-32} LAB_REPORT_WORKERS=${WORKERS:-4} \
  LAB_SPILL_DIR=${LAB_SPILL_DIR:-/lab/tmp/nas-depthband-proof} LAB_SPILL_MAX=${LAB_SPILL_MAX:-100GB} LAB_STORE=$STORE LAB_ENGINE=$DEV_ENGINE
mkdir -p "$OUT" "$LOG" "$LAB_SPILL_DIR"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG/nas_depthband_proof.log"; }
red() { log "RED: $*"; exit 1; }
log "start commit=$COMMIT parts=$PARTS store=$STORE ref=$REF out=$OUT memory=$LAB_WALLETS_MEMORY buckets=$LAB_MARKOUT_BUCKETS"
cd "$REPO/lab"
[ -d "$PARTS" ] || red "no parts at $PARTS"
[ -f "$STORE" ] || red "no store at $STORE"
[ -f "$REF" ] || red "no reference parquet at $REF (NAS run markout-v1-txdelay-v4)"
[ -f "$DEV_ENGINE/COMMIT" ] || red "no engine snapshot at $DEV_ENGINE"

# 1. the tests
rc=0
"$PY" -m pytest -q -p no:cacheprovider wallets/tests/test_markout.py wallets/tests/test_depthband_report.py wallets/tests/test_mechanism_report.py > "$LOG/pytest.log" 2>&1 || rc=$?
log "tests: $(tail -1 "$LOG/pytest.log")"
[ "$rc" = 0 ] || red "tests failed (rc $rc)"

# 2. the markouts (the v4 command: every family of the parts, the transaction-level entries)
rc=0
nice -n 10 "$PY" -u -m wallets.markout --parts "$PARTS" --run f7rev-txdelay --out "$OUT" --tx-delays > "$LOG/markout.log" 2>&1 || rc=$?
[ "$rc" = 0 ] || { tail -5 "$LOG/markout.log" | tee -a "$LOG/nas_depthband_proof.log"; red "markout failed (rc $rc)"; }
log "markout: $(tail -1 "$LOG/markout.log")"

# 3. the comparison with the reference run and the reconstruction check
"$PY" - "$OUT/markout-f7rev-txdelay.parquet" "$REF" >> "$LOG/nas_depthband_proof.log" 2>&1 <<'PYEOF' || red "comparison with the reference run failed (see the log)"
import os, sys, duckdb
new, ref = sys.argv[1], sys.argv[2]
con = duckdb.connect()
con.execute(f"SET memory_limit = '{os.environ.get('LAB_WALLETS_MEMORY', '12GB')}'; SET threads TO {os.environ.get('LAB_WALLETS_THREADS', '6')}")
old = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM '{ref}'").fetchall()]
cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM '{new}'").fetchall()]
missing = [c for c in old if c not in cols]
added = [c for c in cols if c not in old]
n_new, n_ref = con.execute(f"SELECT count(*) FROM '{new}'").fetchone()[0], con.execute(f"SELECT count(*) FROM '{ref}'").fetchone()[0]
sel = ", ".join(old)
d1 = con.execute(f"SELECT count(*) FROM (SELECT {sel} FROM '{new}' EXCEPT ALL SELECT {sel} FROM '{ref}')").fetchone()[0]
d2 = con.execute(f"SELECT count(*) FROM (SELECT {sel} FROM '{ref}' EXCEPT ALL SELECT {sel} FROM '{new}')").fetchone()[0]
chk = con.execute(f"""SELECT count(*), sum((depth_entry IS NULL OR fee_bps IS NULL)::INT),
                        sum((abs(0.5 / ((1 + impact) * (1 - fee_bps / 10000.0) - 1) - depth_entry / 1e9) <= 1e-6 * depth_entry / 1e9)::INT),
                        count(creator_prior_pools), sum((creator_prior_pools = -1)::INT)
                      FROM '{new}' WHERE family = 'F7' AND segment = 'pool_reversed' AND h = '' AND d = 2 AND impact IS NOT NULL AND mid IS NOT NULL""").fetchone()
agree = chk[2] / chk[0] if chk[0] else 0
print(f"compare: rows new {n_new:,} ref {n_ref:,}; old columns {len(old)}, missing {missing}, added {added}; new-minus-ref {d1:,}, ref-minus-new {d2:,}; "
      f"F7 pool_reversed entries at d 2: {chk[0]:,}, without depth/fee {chk[1]}, depth_entry = reconstruction within 1e-6: {agree * 100:.3f} %, "
      f"creator_prior_pools known {chk[3]:,} (system program {chk[4]:,})")
ok = n_new == n_ref and not missing and sorted(added) == ["creator_prior_pools", "depth_entry", "fee_bps"] and d1 == 0 and d2 == 0 and chk[1] == 0 and agree >= 0.999
print("compare:", "OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
PYEOF
tail -2 "$LOG/nas_depthband_proof.log"

# 4. the band row through the report (dev descriptive: a constant d 2)
rc=0
nice -n 10 "$PY" -m wallets.depthband_report --markout "$OUT" --run f7rev-txdelay --boot "${BOOT:-10000}" --d 2 --out "$OUT/depthband-dev.md" > "$LOG/report.log" 2>&1 || rc=$?
[ "$rc" = 0 ] || { tail -5 "$LOG/report.log" | tee -a "$LOG/nas_depthband_proof.log"; red "depthband_report failed (rc $rc)"; }
log "report: $(tail -1 "$LOG/report.log")"
"$PY" - "$OUT/depthband-dev.json" >> "$LOG/nas_depthband_proof.log" 2>&1 <<'PYEOF' || red "the band row does not reproduce E48 (2,113 pools, 5,770 triggers, +8.28 pt)"
import json, sys
j = json.load(open(sys.argv[1]))
i, p = j["input"], j["primary"]["5m"]["P1_net"]
print(f"band: {i['band_pools']:,} pools, {i['band_triggers']:,} triggers, net 5 m {p['mean'] * 100:+.2f} [{p['lo'] * 100:+.2f}; {p['hi'] * 100:+.2f}] (E48: 2,113 / 5,770 / +8.28 [+4.64; +11.83])")
sys.exit(0 if i["band_pools"] == 2113 and i["band_triggers"] == 5770 and abs(p["mean"] - 0.0828) <= 0.0005 else 1)
PYEOF
tail -1 "$LOG/nas_depthband_proof.log"
for f in markout-f7rev-txdelay.parquet run-markout-f7rev-txdelay.json depthband-dev.md depthband-dev.json; do log "sha256 $f $(sha256sum "$OUT/$f" | cut -c1-64)"; done
log "done"
