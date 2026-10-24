#!/bin/bash
# Codex review #7 (kort, read-only; light enough for the VPS in a 1G scope): the write-off proof's strict comparisons
# (#533, nas_writeoff.sh step 2, $DATA/backtests/writeoff-<sha7>/strict-W1/-W2, main against head) compared again with
# this commit's journal_diff (busy_freed tied to a busy-against-entered signal pair; written_off to the exit columns a
# write-off ends). GREEN as step 2's comparison: 0 unexplained, no schema difference, no state mismatch (the groups'
# risk paths from lab/ on), rows present; step 2's completeness check (every eligible unsold position written off) is
# about the engine, not the comparator, and is not repeated. In #533 busy_freed fired 0 times (W1 explained {}, W2
# {written_off: 6}): this run is a non-regression check of the comparator on real journals.
#
#   LAB_PY=/lab/venv/bin/python bash lab/paper/nas_writeoff_recheck.sh        (RUN: writeoff-d41a351 by default)
#   VPS: systemd-run --user --scope -p MemoryMax=1G -- bash lab/paper/nas_writeoff_recheck.sh
#
# Writes $DATA/backtests/writeoff-recheck-<commit7>/ (job.log, strict-W1.json, strict-W2.json, SHA256SUMS). Any RED: rc 1.
set -euo pipefail
export LC_ALL=C
REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
RUN=$DATA/backtests/${RUN:-writeoff-d41a351}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
OUT=$DATA/backtests/writeoff-recheck-${COMMIT:0:7}
mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
red() { log "RED: $*"; exit 1; }
log "start commit=$COMMIT run=$RUN"
bad=0
for w in W1 W2; do
  a=$RUN/strict-$w-main b=$RUN/strict-$w-head
  [ -f "$a/journal.sqlite" ] && [ -f "$b/journal.sqlite" ] || red "no journals $a, $b"
  rc=0
  PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1 "$PY" - "$a" "$b" "$OUT/strict-$w.json" >> "$OUT/job.log" 2>&1 <<'EOF' || rc=$?
import json, sqlite3, sys
from paper.journal_diff import compare, writeoff_causes
a, b, out = sys.argv[1:4]


def groups(path):
    """nas_writeoff.sh step 2's: the groups state with each risk file's path from the checkout's lab/ on."""
    db = sqlite3.connect(f"file:{path}/journal.sqlite?mode=ro", uri=True)
    try:
        g = json.loads(db.execute("SELECT v FROM state WHERE k = 'groups'").fetchone()[0])
    finally:
        db.close()
    for v in g.values():
        if isinstance(v, dict) and isinstance(v.get("risk"), str):
            v["risk"] = v["risk"].rsplit("/lab/", 1)[-1]
    return g


r = compare(a, b, causes=writeoff_causes(a, b))
mismatch = [k for k in r["state_mismatch"] if k != "groups"] + ([] if groups(a) == groups(b) else ["groups"])
ok = r["unexplained"] == 0 and not r["schema_diff"] and not mismatch and any(v["rows_a"] for v in r["tables"].values())
r["recheck"] = {"green": ok, "state_mismatch_normalised": mismatch, "note": "compare()'s own green counts the raw groups paths"}
json.dump(r, open(out, "w"), indent=1, default=str)
print(f"{out}: unexplained {r['unexplained']}, explained {r['explained']}, schema diff {bool(r['schema_diff'])}, state mismatch "
      f"{mismatch}, rows {({t: [v['rows_a'], v['rows_b']] for t, v in r['tables'].items()})}: {'GREEN' if ok else 'RED'}")
for t, v in r["tables"].items():
    if v["unexplained"]:
        print(f"  {t}: only main {v['only_a_first'][:5]}, only head {v['only_b_first'][:5]}, different {v['different_first'][:5]}")
sys.exit(0 if ok else 1)
EOF
  log "strict-$w: rc $rc"
  [ "$rc" = 0 ] || bad=1
done
[ "$bad" = 0 ] || red "a difference the fixed busy_freed no longer explains (job.log, strict-W*.json)"
log "done: GREEN"
(cd "$OUT" && sha256sum job.log strict-W1.json strict-W2.json > SHA256SUMS)
