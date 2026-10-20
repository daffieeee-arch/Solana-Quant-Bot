#!/bin/bash
# Route (ii) entry report on the NAS (spec research/spec-route-ii-sneller-instappen-2026-10-20.md v0.3 §3.5-§3.6, roadmap
# #167): wallets.entry_report over the parts of a full --entries tournament run (nas_route_ii.sh STEP=full, --no-report).
#
#   RUN_DIR=$DATA/backtests/route-ii-full-<commit7> bash lab/wallets/nas_entry_report.sh        (short: minutes)
#
# Steps, each logged in $RUN_DIR/entry_report.log; a failed step ends the job RED (rc 1):
#   1. the report's tests (wallets/tests/test_entry_report.py);
#   2. the input: parts, rows, the entry codes and days of the F7 pool_reversed 0.5 SOL rows; RED when a part, a row or an
#      --entries code is missing (empty input is RED); the parts' size per column (the biggest first) goes to the log;
#   3. a compact copy of the positions in $RUN_DIR/entry-rows/part-000000.parquet (every column when the parts' compressed
#      size is at most $COPY_ALL_GB; else only the columns the report reads), with its rows, bytes and sha256: the report
#      stays reproducible from this copy once the big parts are removed;
#   4. the report over the parts → $RUN_DIR/entry_report.md + entry_report.json (BOOT draws, seed 7);
#   5. the report over the copy → $RUN_DIR/entry-rows/entry_report.{md,json}; RED unless its json equals step 4's
#      (every number within 1e-9 relative: the summation order of the rows differs; the verdict block exactly). Then
#      the copy is a full substitute for the parts: the parts may go.
# Tunables (env): LAB_PY, MEMORY (DuckDB limit, default 4GB), THREADS (default 4), REPORT_WORKERS (bootstrap processes,
# default 4), BOOT (default: entry_report's BOOT = 10,000), COPY_ALL_GB (default 4), LAB_DEFAULTS (the cost config).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
: "${RUN_DIR:?RUN_DIR=<run directory with parts/>}"
PARTS=$RUN_DIR/parts
ROWS=$RUN_DIR/entry-rows
LOG=$RUN_DIR/entry_report.log
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
CODES=${CODES:-R,D1both,D1,E0,K1,K2,K3,K5}
export PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1 LAB_WALLETS_MEMORY=${MEMORY:-4GB} LAB_WALLETS_THREADS=${THREADS:-4} \
  LAB_REPORT_WORKERS=${REPORT_WORKERS:-4} LAB_DEFAULTS=${LAB_DEFAULTS:-$DATA/research/week2-defaults.yaml}
BOOT_ARG=(); [ -n "${BOOT:-}" ] && BOOT_ARG=(--boot "$BOOT")
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }
red() { log "RED: $*"; exit 1; }
[ -d "$PARTS" ] || red "no parts directory at $PARTS"
log "start commit=$COMMIT run=$RUN_DIR memory=$LAB_WALLETS_MEMORY threads=$LAB_WALLETS_THREADS workers=$LAB_REPORT_WORKERS cfg=$LAB_DEFAULTS"
cd "$REPO/lab"

# 1. tests
rc=0
"$PY" -m pytest -q -p no:cacheprovider wallets/tests/test_entry_report.py > "$RUN_DIR/entry_report.pytest.log" 2>&1 || rc=$?
log "tests: $(tail -1 "$RUN_DIR/entry_report.pytest.log")"
[ "$rc" = 0 ] || red "tests failed (rc $rc)"

# 2. input + 3. compact copy
mkdir -p "$ROWS"
rc=0
"$PY" - "$PARTS" "$ROWS/part-000000.parquet" "$CODES" "${COPY_ALL_GB:-4}" >> "$LOG" 2>&1 <<'PYEOF' || rc=$?
import glob, hashlib, os, sys
import duckdb
parts, out, codes, copy_all_gb = sys.argv[1], sys.argv[2], sys.argv[3].split(","), float(sys.argv[4])
files = sorted(glob.glob(os.path.join(parts, "*.parquet")))
if not files:
    print("RED: no parquet parts"); sys.exit(1)
con = duckdb.connect()
con.execute(f"SET threads TO {os.environ['LAB_WALLETS_THREADS']}; SET memory_limit = '{os.environ['LAB_WALLETS_MEMORY']}'")
lit = "[" + ",".join(f"'{f}'" for f in files) + "]"
sizes = con.execute(f"SELECT path_in_schema, sum(total_compressed_size) FROM parquet_metadata({lit}) GROUP BY 1 ORDER BY 2 DESC").fetchall()
total = sum(s for _, s in sizes)
print(f"input: {len(files)} parts, {sum(os.path.getsize(f) for f in files) / 1e9:.3f} GB on disk, {total / 1e9:.3f} GB of column "
      f"chunks; biggest columns: " + ", ".join(f"{c} {s / 1e6:.1f} MB" for c, s in sizes[:6]))
con.execute(f"CREATE VIEW s AS SELECT * FROM read_parquet({lit}, union_by_name = true)")
n_all, n, n_codes, n_days, n_pools = con.execute("""SELECT count(*), count(*) FILTER (w), count(DISTINCT entry) FILTER (w),
    count(DISTINCT day) FILTER (w), count(DISTINCT pool) FILTER (w) FROM (SELECT *, family = 'F7' AND segment = 'pool_reversed'
    AND size_sol = 0.5 AND entry IS NOT NULL AS w FROM s)""").fetchone()
per_code = dict(con.execute("SELECT entry, count(*) FROM s WHERE family = 'F7' AND segment = 'pool_reversed' AND size_sol = 0.5 "
                            "GROUP BY 1 ORDER BY 1").fetchall())
print(f"input: {n_all:,} rows, {n:,} F7 pool_reversed 0.5 SOL rows with an entry code, {n_codes} codes, {n_days} days, {n_pools:,} pools; "
      "per code: " + ", ".join(f"{c} {v:,}" for c, v in per_code.items()))
missing = [c for c in codes if not per_code.get(c)]
if n == 0 or missing:
    print(f"RED: empty input or codes without rows: {missing}"); sys.exit(1)
report_cols = ["family", "segment", "size_sol", "entry", "variant", "farm", "regime", "day", "pool", "t", "entry_slot", "exit_slot",
               "exit_reason", "skipped", "entry_failed", "capped", "k_tx", "cost", "tokens", "proceeds", "exit_attempts", "vault_capped"]
cols = "*" if total <= copy_all_gb * 1e9 else ", ".join(report_cols)
con.execute(f"COPY (SELECT {cols} FROM s ORDER BY entry, variant, pool, t) TO '{out}' (FORMAT parquet, COMPRESSION zstd)")
m = con.execute(f"SELECT count(*) FROM read_parquet('{out}')").fetchone()[0]
sha = hashlib.sha256(open(out, "rb").read()).hexdigest()
print(f"copy: {out} ({'all columns' if cols == '*' else 'report columns'}), {m:,} rows, {os.path.getsize(out) / 1e6:.1f} MB, sha256 {sha}")
if m != n_all:
    print(f"RED: the copy has {m:,} rows, the parts {n_all:,}"); sys.exit(1)
PYEOF
tail -3 "$LOG" | grep -v "^20" || true
[ "$rc" = 0 ] || red "input or copy failed (rc $rc)"

# 4. the report over the parts
rc=0
"$PY" -m wallets.entry_report --parts "$PARTS" "${BOOT_ARG[@]}" --out "$RUN_DIR/entry_report.md" > "$RUN_DIR/entry_report.run.log" 2>&1 || rc=$?
[ "$rc" = 0 ] || { tail -5 "$RUN_DIR/entry_report.run.log" | tee -a "$LOG"; red "entry_report failed (rc $rc)"; }
log "report: $(tail -1 "$RUN_DIR/entry_report.run.log")"

# 5. the report over the copy must give the same json
rc=0
"$PY" -m wallets.entry_report --parts "$ROWS" "${BOOT_ARG[@]}" --out "$ROWS/entry_report.md" > "$ROWS/entry_report.run.log" 2>&1 || rc=$?
[ "$rc" = 0 ] || { tail -5 "$ROWS/entry_report.run.log" | tee -a "$LOG"; red "entry_report over the copy failed (rc $rc)"; }
rc=0
"$PY" - "$RUN_DIR/entry_report.json" "$ROWS/entry_report.json" >> "$LOG" 2>&1 <<'PYEOF' || rc=$?
import json, sys
a, b = (json.load(open(f)) for f in sys.argv[1:3])
worst, bad = 0.0, []
def walk(x, y, path):
    global worst
    if isinstance(x, dict) and isinstance(y, dict):
        if x.keys() != y.keys():
            bad.append(f"{path}: keys differ"); return
        for k in x: walk(x[k], y[k], f"{path}.{k}")
    elif isinstance(x, list) and isinstance(y, list):
        if len(x) != len(y):
            bad.append(f"{path}: length {len(x)} vs {len(y)}"); return
        for i, (u, v) in enumerate(zip(x, y)): walk(u, v, f"{path}[{i}]")
    elif isinstance(x, bool) or isinstance(y, bool) or isinstance(x, str) or isinstance(y, str) or x is None or y is None:
        if x != y: bad.append(f"{path}: {x!r} vs {y!r}")
    elif isinstance(x, (int, float)) and isinstance(y, (int, float)):
        d = abs(x - y)
        worst = max(worst, d)
        if d > 1e-9 * max(1.0, abs(x), abs(y)): bad.append(f"{path}: {x} vs {y}")
    else:
        bad.append(f"{path}: {type(x).__name__} vs {type(y).__name__}")
walk(a, b, "")
def flags(x):  # the verdict's booleans and strings, without its numbers
    if isinstance(x, dict): return {k: flags(v) for k, v in x.items() if not isinstance(v, (int, float)) or isinstance(v, bool)}
    return x
same_verdict = flags(a["verdict"]) == flags(b["verdict"])
print(f"copy check: {len(bad)} differences beyond 1e-9 (relative), largest absolute deviation {worst:.3g}; verdict flags "
      f"{'equal' if same_verdict else 'DIFFER'}")
for m in bad[:10]: print("  " + m)
sys.exit(1 if bad or not same_verdict else 0)
PYEOF
tail -1 "$LOG"
[ "$rc" = 0 ] || red "entry_report.json over the copy differs from the one over the parts"
log "copy check passed: the copy reproduces the report (the parts may go)"
for f in entry_report.md entry_report.json; do log "sha256 $f $(sha256sum "$RUN_DIR/$f" | cut -c1-64)"; done
log "done"
