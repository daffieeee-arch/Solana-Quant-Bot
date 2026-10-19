#!/bin/bash
# LiveFeed's learn order (live.py _new_tokens_and_pools: ORDER BY per query) on the NAS, the lab Python 3.13 runner. The
# proof that only the order of the learned rows changes, not which rows nor any decision:
#   1. the tests: paper/tests/test_live_learn_order.py (fresh processes learn in one order; first slot, then key) and the
#      learned-row restart test;
#   2. job 91's first SEGS segments (4 h of fwd1053-1054 from LO, the snapshot before LO, as paper.shadow's engine --live
#      step), with main's code (MAIN_DIR: an export of main at this branch's base) and with this commit's:
#      - LiveFeed alone, twice per side in fresh processes: this commit's two runs learn in the same order; both sides
#        hand out the same events (sha256) and learn the same rows and new pools (as sets); main's own two runs are
#        reported (same order or not);
#      - the paper engine on it, once per side (K3 0.04:600 with the H arms and risk.yaml, as job 91): paper.journal_diff
#        main vs this commit must be green, the learned table equal row for row (its key is (kind, key)), the wallets equal.
#
#   MAIN_DIR=<main export> LAB_PY=/lab/venv/bin/python bash lab/paper/nas_learn_order.sh      (lab runner; short)
#
# Writes to $DATA/backtests/learn-order-<commit7>/ (job.log, pytest.log, cap-*.json, j-main/, j-new/, diff/).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
OUT=$DATA/backtests/learn-order-${COMMIT:0:7}
mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
log "start commit=$COMMIT out=$OUT"
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
: "${MAIN_DIR:?MAIN_DIR=an export of main at the base of this branch}"
# job 91's recorder directory (LiveFeed reads REC/segments), its store and first slot; REC, STORE and LO can be set for a
# smoke run elsewhere
REC=${REC:-$DATA/backtests/shadow-arms-1053-1054-2c3e5fe/rec}
STORE=${STORE:-$DATA/store/fwd1053-1054.duckdb}
LO=${LO:-454896000}
SEGS=${SEGS:-240}
[ -f "$STORE" ] || { log "no store at $STORE"; exit 1; }
[ -d "$REC/segments" ] || { log "RED: no segments/ under $REC"; exit 1; }
export PYTHONDONTWRITEBYTECODE=1

# 1. the tests
rc=0
(cd "$REPO/lab" && PYTHONPATH=. "$PY" -m pytest -q -rs paper/tests/test_live_learn_order.py paper/tests/test_restart.py \
   -k "learn" -p no:cacheprovider) > "$OUT/pytest.log" 2>&1 || rc=$?
tail -3 "$OUT/pytest.log" | tee -a "$OUT/job.log"
[ "$rc" = 0 ] || { log "tests: FAILED (rc $rc)"; exit 1; }

# 2. a recorder directory per side with job 91's first SEGS segments (links; the engine writes open-pools.json there)
for side in main new; do
  root=$OUT/rec-$side
  rm -rf "${root:?}"
  mkdir -p "$root/segments"
  ls "$REC/segments" | sort | head -n "$SEGS" | while read -r s; do ln -s "$REC/segments/$s" "$root/segments/$s"; done
done
n=$(ls "$OUT/rec-new/segments" | wc -l)
[ "$n" = "$SEGS" ] || { log "RED: $n segments linked, not $SEGS"; exit 1; }
H24=$(cd "$REPO/lab" && PYTHONPATH=. "$PY" -c 'import sys
from backtest import streams
from backtest.store import DAY_MS
from backtest.strategies import Clock
con = streams.open_store(sys.argv[1], threads=1, memory="256MB")
print(round(DAY_MS / Clock.from_store(con).ms_per_slot(int(sys.argv[2]))))' "$STORE" "$LO")
log "input: the first $SEGS segments of $REC from slot $LO, h24 $H24"

capture() {  # side, run: LiveFeed alone, its events' sha256 and what it learned, in order
  local side=$1 k=$2 src
  src=$([ "$side" = main ] && echo "$MAIN_DIR" || echo "$REPO")
  (cd "$src/lab" && PYTHONPATH=. "$PY" - "$OUT/rec-$side" "$STORE" "$LO" "$H24" > "$OUT/cap-$side-$k.json" 2> "$OUT/cap-$side-$k.log" <<'EOF'
import hashlib, json, sys
from paper.live import LiveFeed
root, store, lo, h24 = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
f = LiveFeed(root, store=store, follow=False, snapshot_before=lo, h24=h24, memory="1GB", seed_farmers=True)
h, n, learned = hashlib.sha256(), 0, []
for ev in f.events():
    h.update(repr((ev.venue, ev.key, ev.order, ev.block_time, ev.leg)).encode())
    n += 1
    learned += f.drain_learned()
learned += f.drain_learned()
print(json.dumps({"events": n, "events_sha256": h.hexdigest(), "segments": f.stats["segments"],
                  "learned": [list(r) for r in learned], "new_pools": list(f.new_pools)}))
EOF
  ) || { tail -20 "$OUT/cap-$side-$k.log" | tee -a "$OUT/job.log"; log "capture $side $k: FAILED"; exit 1; }
}
for side in main new; do
  for k in 1 2; do
    capture "$side" "$k"
  done
done

for side in main new; do  # the engine as paper.shadow's engine --live step runs it, the same files for both sides
  src=$([ "$side" = main ] && echo "$MAIN_DIR" || echo "$REPO")
  rm -rf "${OUT:?}/j-$side"
  (cd "$src/lab" && PYTHONPATH=. "$PY" - "$OUT/rec-$side" "$STORE" "$LO" "$H24" "$OUT/j-$side" "$REPO/lab" > "$OUT/j-$side.log" 2>&1 <<'EOF'
import os, sys
from paper.engine import Engine, config_groups, load_groups
from paper.journal import Journal
from paper.live import LiveFeed
root, store, lo, h24, jdir, files = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5], sys.argv[6]
feed = LiveFeed(root, store=store, follow=False, snapshot_before=lo, h24=h24, memory="1GB", seed_farmers=True)
groups = config_groups(load_groups(os.path.join(files, "paper", "h-arms-2026-10-16.yaml"))[1])
Engine(feed, Journal(jdir), os.path.join(files, "paper", "risk.yaml"), variants=[(0.04, 600)], d=1, q=0.5, clock=feed.clock,
       backup=False, groups=groups).run()
feed.close()
print("engine done")
EOF
  ) || { tail -20 "$OUT/j-$side.log" | tee -a "$OUT/job.log"; log "engine $side: FAILED"; exit 1; }
done

# 3. the verdict
jd=0
(cd "$REPO/lab" && PYTHONPATH=. "$PY" -m paper.journal_diff --journal-a "$OUT/j-main" --journal-b "$OUT/j-new" --out "$OUT/diff") \
  > "$OUT/journal_diff.log" 2>&1 || jd=$?
tail -5 "$OUT/journal_diff.log" | tee -a "$OUT/job.log"
rc=0
(cd "$REPO/lab" && "$PY" - "$OUT" "$jd" > "$OUT/verdict.log" 2>&1 <<'EOF') || rc=$?
import json, os, sqlite3, sys
out, jd = sys.argv[1], int(sys.argv[2])
cap = {(s, k): json.load(open(os.path.join(out, f"cap-{s}-{k}.json"))) for s in ("main", "new") for k in (1, 2)}
n1, n2, m1, m2 = cap["new", 1], cap["new", 2], cap["main", 1], cap["main", 2]
key = lambda c: (c["events"], c["events_sha256"], sorted(map(tuple, c["learned"])), sorted(c["new_pools"]))  # noqa: E731
db = {s: sqlite3.connect(f"file:{os.path.join(out, 'j-' + s, 'journal.sqlite')}?mode=ro", uri=True) for s in ("main", "new")}
rows = {s: d.execute("SELECT kind, key, seq, slot, info FROM learned ORDER BY kind, key").fetchall() for s, d in db.items()}
order = {s: [tuple(r) for r in d.execute("SELECT kind, key FROM learned ORDER BY rowid")] for s, d in db.items()}
wallets = all(json.load(open(os.path.join(out, "j-main", w))) == json.load(open(os.path.join(out, "j-new", w)))
              for w in sorted(os.listdir(os.path.join(out, "j-main"))) if w.startswith("wallet") and w.endswith(".json"))
why = [w for w, bad in (
    ("this commit's two runs learn in different orders", (n1["learned"], n1["new_pools"]) != (n2["learned"], n2["new_pools"])),
    ("the sides differ in events or in what they learn (as sets)", key(m1) != key(n1) or key(m2) != key(n1)),
    ("nothing learned (the run proves nothing)", not n1["learned"] or not n1["new_pools"]),
    ("journal_diff is not green", jd != 0),
    ("the learned table differs", rows["main"] != rows["new"]),
    ("the wallet files differ", not wallets)) if bad]
print(f"events {n1['events']:,} over {n1['segments']} segments; learned {len(n1['learned'])} rows, {len(n1['new_pools'])} new pools; "
      f"main's own two runs: {'the same order' if (m1['learned'], m1['new_pools']) == (m2['learned'], m2['new_pools']) else 'DIFFERENT orders'}; "
      f"main vs this commit: {'the same order' if (m1['learned'], m1['new_pools']) == (n1['learned'], n1['new_pools']) else 'other orders'}, "
      f"the same rows; journal learned rows {len(rows['new'])}, write order {'the same' if order['main'] == order['new'] else 'different'}")
if why:
    print("learn order: RED: " + "; ".join(why))
    sys.exit(1)
print("learn order: GREEN: only the order of the learned rows changes; the events, the learned rows and every journal row "
      "and wallet are the same")
EOF
tee -a "$OUT/job.log" < "$OUT/verdict.log"
[ "$rc" = 0 ] || exit 1
log "done"
