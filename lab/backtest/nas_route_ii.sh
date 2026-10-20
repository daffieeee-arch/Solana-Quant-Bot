#!/bin/bash
# Route (ii) on the NAS, the lab Python 3.13 runner (research/spec-route-ii-sneller-instappen-2026-10-20.md v0.3 §3.4,
# §4): the tournament with an entry position apart from the exit's (--entries), dev2 only.
#
#   STEP=smoke (short): one day, 2026-09-24 (decisions in that UTC day; the store before it is warm-up), the codes plus
#              the smoke's property codes D1Q1 and D0Q1, then the checks (GREEN or RED with the reasons):
#              1. anchor R (entry and exit d 2, q 0.5): tp4_T600 at 0.5 SOL equal to f7rev-1033-1046's d 2 rows on
#                 cost, proceeds, exit_slot and exit_reason, trigger for trigger;
#              2. anchor D1both (d 1 for entry and exit): equal to f7rev's d 1 rows the same way;
#              3. K past the entry slot's token buys (capped) equal to D1Q1 (d_e 1, q 1.0) row for row;
#              4. E0 (= K0) and D0Q1 (d_e 0, q 1.0): equal cost and tokens, E0's entry slot one higher;
#              5. the entry, capped and k_tx columns and config.json's entries.
#              f7rev decided over the whole dev window, the smoke only in its day: a trigger the smoke takes that f7rev
#              skipped as busy (a position opened before the day) is reported and allowed when f7rev has that busy
#              position; any other difference is RED.
#   STEP=full  (long): the whole dev window with R,D1both,D1,E0,K1,K2,K3,K5 (§3.4), --no-report; the report is LAB
#              research's (wallets/entry_report.py).
#
#   STEP=smoke LAB_PY=/lab/venv/bin/python bash lab/backtest/nas_route_ii.sh       (lab runner; short)
#   STEP=full  LAB_PY=/lab/venv/bin/python bash lab/backtest/nas_route_ii.sh       (lab runner; long, ~20-40 min est.)
#
# Writes to $DATA/backtests/route-ii-<step>-<commit7>/ (the tournament's parts and config.json, job.log, checks.log).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
: "${STEP:?STEP=smoke or STEP=full}"
RUN=route-ii-$STEP-${COMMIT:0:7}
OUT=$DATA/backtests/$RUN
mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
log "start $STEP commit=$COMMIT out=$OUT"
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
STORE=$DATA/store/dev2.duckdb
F7REV=$DATA/backtests/f7rev-1033-1046
WORKERS=${WORKERS:-4}
[ -f "$STORE" ] || { log "RED: no store at $STORE"; exit 1; }
export PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1 LAB_STORE=$STORE LAB_BACKTESTS=$DATA/backtests LAB_ENGINE_COMMIT=$COMMIT
cd "$REPO/lab"

rc=0
"$PY" -m pytest -q -p no:cacheprovider backtest/tests/test_route_ii_entry.py > "$OUT/pytest.log" 2>&1 || rc=$?
tail -1 "$OUT/pytest.log" | tee -a "$OUT/job.log"
[ "$rc" = 0 ] || { log "RED: tests failed (rc $rc)"; exit 1; }

COMMON=(--families F7 --segments pool_reversed --sizes 0.5 --variants tp4_T600,flow300_H6,flow300_tp100_H6 --f7-grid v1,long,big
        --tau empirical --tol 0.05 --no-report --workers "$WORKERS" --run-id "$RUN")
if [ "$STEP" = smoke ]; then
  [ -d "$F7REV/parts" ] || { log "RED: no f7rev parts at $F7REV/parts (the anchors' reference)"; exit 1; }
  DAY=$("$PY" -c 'import sys
from backtest import streams
con = streams.open_store(sys.argv[1], threads=1, memory="256MB")
lo, hi = con.execute("""SELECT min(slot), max(slot) + 1 FROM blocks
  WHERE block_time >= epoch(TIMESTAMP '"'"'2026-09-24 00:00:00'"'"') AND block_time < epoch(TIMESTAMP '"'"'2026-09-25 00:00:00'"'"')""").fetchone()
print(f"{lo}:{hi}")' "$STORE")
  log "smoke: 2026-09-24 is slots $DAY"
  rc=0
  "$PY" -m backtest.tournament "${COMMON[@]}" --entries R,D1both,D1,D1Q1,D0Q1,E0,K1,K2,K3,K5 --decide "$DAY" \
    > "$OUT/tournament.log" 2>&1 || rc=$?
  [ "$rc" = 0 ] || { tail -20 "$OUT/tournament.log" | tee -a "$OUT/job.log"; log "RED: the tournament failed (rc $rc)"; exit 1; }
  tail -2 "$OUT/tournament.log" | tee -a "$OUT/job.log"
  rc=0
  "$PY" - "$OUT" "$F7REV" > "$OUT/checks.log" 2>&1 <<'EOF' || rc=$?
import json, os, sys
import duckdb
out, ref = sys.argv[1], sys.argv[2]
con = duckdb.connect(); con.execute("SET memory_limit = '2GB'; SET threads TO 2")
cfg = json.load(open(os.path.join(out, "config.json")))
cols = [c[0] for c in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{out}/parts/*.parquet')").fetchall()]
why = []
if not {"entry", "capped", "k_tx"} <= set(cols) or not cfg.get("entries"):
    why.append("the entry, capped or k_tx column or config.json's entries is missing")
con.execute(f"CREATE VIEW s AS SELECT * FROM read_parquet('{out}/parts/*.parquet')")
con.execute(f"CREATE VIEW f AS SELECT * FROM read_parquet('{ref}/parts/*.parquet') WHERE family = 'F7' AND variant = 'tp4_T600' "
            "AND size_sol = 0.5 AND q = 0.5 AND (overlay IS NULL OR overlay = 'none')")
F = "cost, proceeds, exit_slot, exit_reason"
for code, d in (("R", 2), ("D1both", 1)):
    s = f"(SELECT * FROM s WHERE entry = '{code}' AND variant = 'tp4_T600')"
    # every smoke row against its f7rev twin (the smoke's triggers can start a little before the day: --decide's
    # signals); f7rev's rows over the smoke's span that the smoke lacks
    lo, hi = con.execute(f"SELECT min(t), max(t) FROM {s}").fetchone()
    r = f"(SELECT * FROM f WHERE d = {d})"
    both, differ = con.execute(f"""SELECT count(*), count(*) FILTER (WHERE (a.cost, a.proceeds, a.exit_slot, a.exit_reason)
        IS DISTINCT FROM (b.cost, b.proceeds, b.exit_slot, b.exit_reason)) FROM {s} a JOIN {r} b USING (pool, t)""").fetchone()
    only_ref = con.execute(f"SELECT count(*) FROM {r} b ANTI JOIN {s} a USING (pool, t) WHERE b.t >= {lo} AND b.t <= {hi}").fetchone()[0]
    # a trigger only the smoke has: f7rev skipped it as busy, a position of the pool opened before the smoke's span
    only_s, busy = con.execute(f"""SELECT count(*), count(*) FILTER (WHERE EXISTS (SELECT 1 FROM f p WHERE p.d = {d}
        AND p.pool = a.pool AND p.t < {lo} AND a.t <= COALESCE(p.exit_slot, p.t)))
        FROM {s} a ANTI JOIN {r} b USING (pool, t)""").fetchone()
    print(f"anchor {code} (f7rev d {d}, triggers {lo}..{hi}): {both} in both, {differ} differ in ({F}); {only_ref} only in "
          f"f7rev; {only_s} only in the smoke, {busy} of them busy in f7rev from before the span")
    if differ or only_ref or only_s != busy or not both:
        why.append(f"anchor {code}")
capped, k_differ = con.execute("""SELECT count(*), count(*) FILTER (WHERE (k.cost, k.tokens, k.proceeds, k.entry_slot, k.exit_slot,
    k.exit_reason, k.exit_attempts) IS DISTINCT FROM (q.cost, q.tokens, q.proceeds, q.entry_slot, q.exit_slot, q.exit_reason,
    q.exit_attempts)) FROM s k JOIN s q USING (variant, pool, t) WHERE k.entry LIKE 'K%' AND k.capped AND q.entry = 'D1Q1'""").fetchone()
print(f"K past the slot's buys = D1Q1: {capped} capped K rows with a D1Q1 twin, {k_differ} differ")
if k_differ or not capped:
    why.append("K capped vs D1Q1")
e0, e0_differ = con.execute("""SELECT count(*), count(*) FILTER (WHERE (e.cost, e.tokens) IS DISTINCT FROM (z.cost, z.tokens)
    OR e.entry_slot IS DISTINCT FROM z.entry_slot + 1) FROM s e JOIN s z USING (variant, pool, t)
    WHERE e.entry = 'E0' AND z.entry = 'D0Q1' AND NOT e.skipped AND NOT z.skipped AND NOT e.entry_failed AND NOT z.entry_failed""").fetchone()
print(f"E0 vs D0Q1: {e0} filled twins, {e0_differ} differ in cost, tokens or the one-slot entry offset")
if e0_differ or not e0:
    why.append("E0 vs D0Q1")
print("route (ii) smoke: " + ("RED: " + "; ".join(why) if why else "GREEN"))
sys.exit(1 if why else 0)
EOF
  tee -a "$OUT/job.log" < "$OUT/checks.log"
  [ "$rc" = 0 ] || exit 1
elif [ "$STEP" = full ]; then
  rc=0
  "$PY" -m backtest.tournament "${COMMON[@]}" --entries R,D1both,D1,E0,K1,K2,K3,K5 > "$OUT/tournament.log" 2>&1 || rc=$?
  [ "$rc" = 0 ] || { tail -20 "$OUT/tournament.log" | tee -a "$OUT/job.log"; log "RED: the tournament failed (rc $rc)"; exit 1; }
  tail -2 "$OUT/tournament.log" | tee -a "$OUT/job.log"
else
  log "unknown STEP=$STEP"
  exit 2
fi
log "done"
