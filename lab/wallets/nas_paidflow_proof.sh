#!/bin/bash
# C3 / E54 proof on dev (roadmap #167), before the paid-flow pre-registration freezes: the markout run at this commit over the
# dev f7rev parts (the same command as NAS runs markout-v1-txdelay-v4 and the E49 proof: every family, --tx-delays) must
# (1) reproduce every existing column of the E49 proof parquet (99zzzzg, sha256 4491e8d4…) bit for bit, (2) carry the new
# columns paid_buyers_60s / paid_max_60s, counted on the dev store, and (3) give the dev reference row of the pre-registration
# through wallets.paidflow_report --d 2: the arm (>= 2 paying buyers in the 60 s before the trigger) = EXP_POOLS pools, EXP_TRIG
# triggers, net 5 m EXP_NET pt per pool, as explore/paidflow_dev.py computed on the VPS from the v4 rows with the same code.
#
#   bash lab/wallets/nas_paidflow_proof.sh            (short: ~15 min; the v4 run took 790 s on the NAS)
#
# Steps, logged in $OUT/log/nas_paidflow_proof.log; any failure RED (rc 1):
#   1. the reference parquet exists and has the expected sha256; the tests: test_markout.py, test_paidflow_report.py, test_depthband_report.py (dev engine);
#   2. markout --tx-delays over $PARTS with the dev store and engine → $OUT/markout-f7rev-txdelay.parquet; RED on empty output;
#   3. the comparison with $REF: row counts, the old columns set-equal both ways (EXCEPT ALL), the new columns present and
#      counted (non-NULL on every pool entry), the run JSON's paid window per epoch;
#   4. paidflow_report --d 2 → $OUT/paidflow-dev.md + json; RED unless the arm row is exactly EXP_POOLS / EXP_TRIG and the trimmed
#      mean is EXP_NET within 0.005 pt (the same code on the same rows: the bootstrap seed is fixed), and RED when all_triggers or
#      arm_triggers is 0.
# Tunables (env): MEMORY (DuckDB limit, default 12GB), BUCKETS (32, as v4), THREADS (6), WORKERS (6), BOOT (10000, report only), REF, REF_SHA, PARTS, STORE,
# DEV_ENGINE, EXP_POOLS, EXP_TRIG, EXP_NET, OUT (default $DATA/wallets/paidflow-proof-<commit7>).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
OUT=${OUT:-$DATA/wallets/paidflow-proof-${COMMIT:0:7}}
LOG=$OUT/log
PARTS=${PARTS:-$DATA/backtests/f7rev-1033-1046/parts}
STORE=${STORE:-$DATA/store/dev2.duckdb}
REF=${REF:-$DATA/wallets/depthband-proof-45a1106/markout-f7rev-txdelay.parquet}
REF_SHA=${REF_SHA:-4491e8d4d4cb8d52cb06db802ecf5d146658188e70240bad59e45abf493c49b2}
DEV_ENGINE=${DEV_ENGINE:-$DATA/backtests/f7rev-1033-1046/engine}
EXP_POOLS=${EXP_POOLS:-1662}
EXP_TRIG=${EXP_TRIG:-9308}
EXP_NET=${EXP_NET:-9.19}
# the settings of NAS job 99zx-ma4-full-21711ea / 99zzzzg (nas_markout.sh, MARKOUT_ARGS="--tx-delays", no --families): BUCKETS 32, MEMORY 12GB,
# WORKERS 6, LAB_SPILL_MAX 150GB, OPENBLAS_NUM_THREADS 1; BOOT only reaches the report (markout.py has no bootstrap: the rows do not depend on it)
export PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=${OPENBLAS_NUM_THREADS:-1} LAB_WALLETS_OUT=$DATA/wallets LAB_WALLETS_MEMORY=${MEMORY:-12GB} \
  LAB_WALLETS_THREADS=${THREADS:-6} LAB_MARKOUT_BUCKETS=${BUCKETS:-32} LAB_REPORT_WORKERS=${WORKERS:-6} \
  LAB_SPILL_DIR=${LAB_SPILL_DIR:-/lab/tmp/nas-paidflow-proof} LAB_SPILL_MAX=${LAB_SPILL_MAX:-150GB} LAB_STORE=$STORE LAB_ENGINE=$DEV_ENGINE
mkdir -p "$OUT" "$LOG" "$LAB_SPILL_DIR"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG/nas_paidflow_proof.log"; }
red() { log "RED: $*"; exit 1; }
log "start commit=$COMMIT parts=$PARTS store=$STORE ref=$REF out=$OUT memory=$LAB_WALLETS_MEMORY buckets=$LAB_MARKOUT_BUCKETS expected arm $EXP_POOLS pools / $EXP_TRIG triggers / $EXP_NET pt"
cd "$REPO/lab"
[ -d "$PARTS" ] || red "no parts at $PARTS"
[ -f "$STORE" ] || red "no store at $STORE"
[ -f "$REF" ] || red "no reference parquet at $REF (NAS job 99zzzzg, the E49 proof)"
[ -f "$DEV_ENGINE/COMMIT" ] || red "no engine snapshot at $DEV_ENGINE"
got=$(sha256sum "$REF" | cut -c1-64)
[ "$got" = "$REF_SHA" ] || red "the reference parquet's sha256 is $got, expected $REF_SHA"
log "reference sha256 $got OK"

# 1. the tests
rc=0
"$PY" -m pytest -q -p no:cacheprovider wallets/tests/test_markout.py wallets/tests/test_paidflow_report.py wallets/tests/test_depthband_report.py > "$LOG/pytest.log" 2>&1 || rc=$?
log "tests: $(tail -1 "$LOG/pytest.log")"
[ "$rc" = 0 ] || red "tests failed (rc $rc)"

# 2. the markouts (the v4 command: every family of the parts, the transaction-level entries)
rc=0
nice -n 10 "$PY" -u -m wallets.markout --parts "$PARTS" --run f7rev-txdelay --out "$OUT" --tx-delays > "$LOG/markout.log" 2>&1 || rc=$?
[ "$rc" = 0 ] || { tail -5 "$LOG/markout.log" | tee -a "$LOG/nas_paidflow_proof.log"; red "markout failed (rc $rc)"; }
log "markout: $(tail -1 "$LOG/markout.log")"
[ -s "$OUT/markout-f7rev-txdelay.parquet" ] || red "markout wrote no parquet"

# 3. the comparison with the reference run and the new columns
"$PY" - "$OUT/markout-f7rev-txdelay.parquet" "$REF" "$OUT/run-markout-f7rev-txdelay.json" >> "$LOG/nas_paidflow_proof.log" 2>&1 <<'PYEOF' || red "comparison with the reference run failed (see the log)"
import json, os, sys, duckdb
new, ref, rj = sys.argv[1], sys.argv[2], json.load(open(sys.argv[3]))
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
chk = con.execute(f"""SELECT count(*), count(paid_buyers_60s), sum((paid_buyers_60s >= 2)::INT), count(paid_max_60s), max(paid_buyers_60s)
                      FROM '{new}' WHERE venue = 'pool' AND h = '' AND d = 2""").fetchone() if "paid_buyers_60s" in cols and "paid_max_60s" in cols else (0, 0, 0, 0, None)
curve = con.execute(f"SELECT count(paid_buyers_60s) FROM '{new}' WHERE venue = 'curve'").fetchone()[0] if "paid_buyers_60s" in cols else -1
win = rj.get("paid_window_slots_per_epoch") or {}
print(f"compare: rows new {n_new:,} ref {n_ref:,}; old columns {len(old)}, missing {missing}, added {added}; new-minus-ref {d1:,}, ref-minus-new {d2:,}; "
      f"pool entries at d 2: {chk[0]:,}, paid_buyers_60s counted on {chk[1]:,} (>= 2 on {chk[2]:,}, max {chk[4]}), paid_max_60s on {chk[3]:,}; curve rows with a count {curve}; "
      f"paid window {rj.get('paid_window_ms')} ms = slots per epoch {win}, floor {rj.get('paid_min_lamports')} lamports")
ok = (n_new == n_ref and n_new > 0 and not missing and sorted(added) == ["paid_buyers_60s", "paid_max_60s"] and d1 == 0 and d2 == 0
      and chk[0] > 0 and chk[1] == chk[0] and chk[2] > 0 and curve == 0 and len(win) > 0 and rj.get("paid_window_ms") == 60000 and rj.get("paid_min_lamports") == 5000)
print("compare:", "OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
PYEOF
tail -2 "$LOG/nas_paidflow_proof.log"

# 4. the arm row through the report (dev descriptive: a constant d 2)
rc=0
nice -n 10 "$PY" -m wallets.paidflow_report --markout "$OUT" --run f7rev-txdelay --boot "${BOOT:-10000}" --d 2 --out "$OUT/paidflow-dev.md" > "$LOG/report.log" 2>&1 || rc=$?
[ "$rc" = 0 ] || { tail -5 "$LOG/report.log" | tee -a "$LOG/nas_paidflow_proof.log"; red "paidflow_report failed (rc $rc)"; }
log "report: $(tail -1 "$LOG/report.log")"
"$PY" - "$OUT/paidflow-dev.json" "$EXP_POOLS" "$EXP_TRIG" "$EXP_NET" >> "$LOG/nas_paidflow_proof.log" 2>&1 <<'PYEOF' || red "the arm row does not reproduce the dev reference (see the log)"
import json, sys
j = json.load(open(sys.argv[1])); ep, et, en = int(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4])
i, p = j["input"], j["primary"]["5m"]["P1_net"]
print(f"arm: {i['arm_pools']:,} pools, {i['arm_triggers']:,} triggers, net 5 m {p['mean'] * 100:+.2f} [{p['lo'] * 100:+.2f}; {p['hi'] * 100:+.2f}] over {i['arm_days']} days "
      f"(all: {i['all_triggers']:,} triggers, {i['all_pools']:,} pools); expected {ep:,} / {et:,} / {en:+.2f}")
sys.exit(0 if i["all_triggers"] > 0 and i["arm_triggers"] > 0 and i["arm_pools"] == ep and i["arm_triggers"] == et and abs(p["mean"] * 100 - en) <= 0.005 else 1)
PYEOF
tail -1 "$LOG/nas_paidflow_proof.log"
for f in markout-f7rev-txdelay.parquet run-markout-f7rev-txdelay.json paidflow-dev.md paidflow-dev.json; do log "sha256 $f $(sha256sum "$OUT/$f" | cut -c1-64)"; done
log "done"
