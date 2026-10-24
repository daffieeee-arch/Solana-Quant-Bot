#!/bin/bash
# The E20 store fix's proof on dev (no clean data): the same dev chunks built by the E20 pin's store.py (46a0236, OLD)
# and by the fixed one (NEW), table by table equal (row count, sum and xor of the row hashes over every column), every
# table non-empty, NEW's meta.excluded_pools_int64 = 0 pools; then the fixed store's tests (the UINT64-max fixture with a
# tournament smoke). dev2.duckdb itself is not the reference: it was built by ba322be's store.py, 186 lines from 46a0236.
#
#   OLD=<export of 46a0236> NEW=<export of the pin> LAB_PY=/lab/venv/bin/python bash $NEW/lab/backtest/nas_store_u64_proof.sh
#
# EPOCHS (default 1043: two chunks); the stores and the log go to $DATA/backtests/store-u64-proof-<new7>/; any RED: rc 1.
# pool_sweeps is empty on dev (a forward-protocol table, test_store_sweeps); every other table must have rows.
set -euo pipefail
export LC_ALL=C
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
: "${OLD:?an export of 46a0236}" "${NEW:?an export of the pin}"
OLD=$(cd "$OLD" && pwd) NEW=$(cd "$NEW" && pwd)  # absolute: build() runs from each repo's lab/
EPOCHS=${EPOCHS:-1043}
NEW_C=$(git -C "$NEW" rev-parse HEAD 2>/dev/null || cat "$NEW/COMMIT")
OLD_C=$(git -C "$OLD" rev-parse HEAD 2>/dev/null || cat "$OLD/COMMIT")
OUT=$DATA/backtests/store-u64-proof-${NEW_C:0:7}
mkdir -p "$OUT"
export LAB_SPILL_DIR=${LAB_SPILL_DIR:-$DATA/tmp/store-u64-proof} PYTHONDONTWRITEBYTECODE=1
mkdir -p "$LAB_SPILL_DIR"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
red() { log "RED: $*"; exit 1; }
log "start old=$OLD_C new=$NEW_C epochs=$EPOCHS"
case $OLD_C in 46a0236*) ;; *) red "OLD is not 46a0236";; esac

build() {  # repo out; run from the repo's lab/ (python - puts the working directory first on sys.path)
  (cd "$1/lab" && PYTHONPATH=$1/lab "$PY" - "$2" "$EPOCHS" "$1/lab" >> "$OUT/build-$(basename "$2" .duckdb).log" 2>&1 <<'EOF'
import hashlib, json, os, sys
from backtest import data, health, store
src = os.path.realpath(store.__file__)
assert src.startswith(os.path.realpath(sys.argv[3]) + os.sep), f"store.py from {src}, not from {sys.argv[3]}"
print("STORE_PY", src, hashlib.sha256(open(src, "rb").read()).hexdigest())
health.wait_ok = lambda *a, **k: 0
eps = [int(e) for e in sys.argv[2].split(",")]
chunks = [c for c in data.complete_chunks() if c[0] // 432000 in eps]
assert chunks and all(1033 <= c[0] // 432000 <= 1046 for c in chunks), "dev chunks only"
meta = store.build("dev", sys.argv[1], store.load_defaults(), chunks)
print("META", json.dumps({k: meta.get(k) for k in ("counts", "excluded_pools_int64", "slot_start", "slot_end_exclusive")}))
EOF
  )
}
build "$OLD" "$OUT/old.duckdb" || red "the old build failed (build-old.log)"
log "old build: $(grep '^STORE_PY' "$OUT/build-old.log" | tail -1)"
log "old build: $(grep '^META' "$OUT/build-old.log" | tail -1 | cut -c1-300)"
# 46a0236 writes no excluded_pools_int64: a value here means the old build ran the new store.py
grep '^META' "$OUT/build-old.log" | tail -1 | grep -q '"excluded_pools_int64": null' || red "the old build did not run 46a0236's store.py"
build "$NEW" "$OUT/new.duckdb" || red "the new build failed (build-new.log)"
log "new build: $(grep '^STORE_PY' "$OUT/build-new.log" | tail -1)"
log "new build: $(grep '^META' "$OUT/build-new.log" | tail -1 | cut -c1-300)"

rc=0
"$PY" - "$OUT/old.duckdb" "$OUT/new.duckdb" >> "$OUT/job.log" 2>&1 <<'EOF' || rc=$?
import json, sys
import duckdb
con = duckdb.connect()
con.execute(f"ATTACH '{sys.argv[1]}' AS o (READ_ONLY)")
con.execute(f"ATTACH '{sys.argv[2]}' AS n (READ_ONLY)")
names = lambda db: sorted(r[0] for r in con.execute(  # noqa: E731
    f"SELECT table_name FROM duckdb_tables() WHERE database_name = '{db}' AND table_name <> 'meta'").fetchall())
tables = names("o")
assert tables and tables == names("n"), (tables, names("n"))
# arg_max(.., slot) over a tie in the last slot is not deterministic between two builds (old and new store.py alike):
# those columns are compared apart, informational.
TIE = {"pools": {"coin_creator"}, "pool_stats": {"last_b", "last_vault", "coin_creator"}}
EMPTY_OK = {"pool_sweeps"}  # a forward-protocol table: no rows on dev
bad = []
for t in tables:
    allc = [c[0] for c in con.execute(f"DESCRIBE o.{t}").fetchall()]
    assert allc == [c[0] for c in con.execute(f"DESCRIBE n.{t}").fetchall()], t
    cols = ", ".join(c for c in allc if c not in TIE.get(t, ()))
    q = "SELECT count(*), sum(hash({c})::HUGEINT), bit_xor(hash({c})) FROM {db}.{t}"
    x, y = (con.execute(q.format(c=cols, db=db, t=t)).fetchone() for db in ("o", "n"))
    print(t, "rows", x[0], "equal" if x == y else f"DIFFER {x} vs {y}",
          f"(tie columns apart: {sorted(TIE[t])})" if t in TIE else "", "(empty on dev)" if t in EMPTY_OK else "")
    if x != y or (x[0] == 0 and t not in EMPTY_OK):
        bad.append(t)
    if t in TIE:
        d = " OR ".join(f"a.{c} IS DISTINCT FROM b.{c}" for c in sorted(TIE[t]))
        n = con.execute(f"SELECT count(*) FROM o.{t} a JOIN n.{t} b USING (pool) WHERE {d}").fetchone()[0]
        print(f"  {t}: {n} pools differ in the tie columns (informational)")
ex = json.loads(con.execute("SELECT json FROM n.meta").fetchone()[0])["excluded_pools_int64"]
print("new meta.excluded_pools_int64", ex)
print("tables differing or empty:", bad)
sys.exit(1 if bad or ex["pools"] != 0 else 0)
EOF
[ "$rc" = 0 ] || red "the stores differ, a table is empty or a pool was excluded (job.log)"
log "every table equal (pool_sweeps empty on dev), the others non-empty; no pool excluded"
rc=0
(cd "$NEW/lab" && PYTHONPATH=$NEW/lab "$PY" -m pytest -q -p no:cacheprovider backtest/tests/test_store_u64.py \
  backtest/tests/test_store_sweeps.py backtest/tests/test_store_periods.py) > "$OUT/pytest.log" 2>&1 || rc=$?
log "tests: $(tail -1 "$OUT/pytest.log")"
[ "$rc" = 0 ] || red "tests failed (pytest.log)"
grep -q "skipped" "$OUT/pytest.log" && red "a test was skipped (the dev chunk is missing?)"
rm -rf "$OUT/old.duckdb" "$OUT/new.duckdb" "$LAB_SPILL_DIR"
log "done: GREEN"
(cd "$OUT" && sha256sum job.log build-old.log build-new.log pytest.log > SHA256SUMS)  # after job.log's last line
