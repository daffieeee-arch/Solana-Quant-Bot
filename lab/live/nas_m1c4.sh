#!/bin/bash
# M1 C4 of the Rust live route (lab/docs/rust-live-route-design-2026-10-16.md §3, §16; the spec is
# research/m1-c4-plateau-spec-2026-10-19.md) on the NAS: lab-live in live form, the recorder's segments, before any VPS
# or streaming test. C4-1 adds the segment reader; C4-2 the memory log; C4-3 the plateau gate, the 1054 legs/signals
# parity and RC4 (STEP=plateau).
#
#   STEP=build   (bookworm runner, Rust 1.97.1): cargo test --release --locked of lab/live (the segments against the
#                chunks on the fixture, and every earlier test) and of ALL of lab/core, named tests required to have run;
#                then the release binary into $OUT/bin/. Run with LAB_DATA_ROOT=/lab/data-old-faithful-one/lab.
#   STEP=smoke   (the lab Python 3.13 runner): lab-live paper with risk on over job 91's segments (fwd1053-1054, slots
#                454,896,000-455,759,999; built by the shadow recorder from OF1, the live form, no live recording), with
#                the paper start's groups and cost scenario (paper.engine journals them on a one-slot run of the same
#                checkout) and fwd1053-1054's snapshot. One measure line in job.log: the segments read, the units, the
#                incomplete ones skipped, and as measured facts the backfill rows not read (per segment min, median,
#                max; total; their share of all rows); the journal's rows, peak RSS and seconds. No reference: parity in
#                live form is M3's.
#
#   STEP=build LAB_DATA_ROOT=/lab/data-old-faithful-one/lab bash lab/live/nas_m1c4.sh      (bookworm runner)
#   STEP=smoke LAB_PY=/lab/venv/bin/python bash lab/live/nas_m1c4.sh                       (lab runner)
#
# Both steps write to $DATA/backtests/live-m1c4-<commit7>/.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
OUT=$DATA/backtests/live-m1c4-${COMMIT:0:7}
mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
: "${STEP:?STEP=build or STEP=smoke}"
log "start $STEP commit=$COMMIT out=$OUT"
SEGMENTS=$DATA/backtests/shadow-arms-1053-1054-2c3e5fe/rec/segments  # job 91's (#299, 2c3e5fe), read again by 99m
STORE=$DATA/store/fwd1053-1054.duckdb
LO=454896000
HI=455760000

if [ "$STEP" = build ]; then
  cd "$REPO/lab/live"
  if [ ! -d target ]; then
    prev=$(ls -dt /lab/code/repo-*/lab/live/target 2>/dev/null | head -1 || true)
    if [ -n "$prev" ]; then
      log "target: a copy of $prev"
      cp -a "$prev" target
      touch src/*.rs src/*/*.rs ../extractor/src/*.rs 2>/dev/null || true
    fi
  fi
  cargo test --release --locked > "$OUT/cargo-test.log" 2>&1 || { tail -40 "$OUT/cargo-test.log" | tee -a "$OUT/job.log"; log "cargo test: FAILED"; exit 1; }
  grep -E "^test |^test result|^running" "$OUT/cargo-test.log" | tee -a "$OUT/job.log"
  for t in legs_read::tests::segments_give_the_chunks_events_and_count_their_backfill legs_read::tests::the_chunks_become_the_stores_event_stream \
           paper_core::tests::every_c3_case_gives_the_engines_writes_with_risk_on paper::tests::with_risk_on_each_group_reads_its_own_file \
           paper::tests::a_replay_writes_its_paper_journal_the_states_and_the_summary; do
    grep -q "^test $t ... ok$" "$OUT/cargo-test.log" || { log "cargo test: $t did not run: FAILED"; exit 1; }
  done
  log "cargo test: ok ($(grep -c '^test .* ok$' "$OUT/cargo-test.log") tests ok)"
  (cd ../core && cargo test --release --locked > "$OUT/cargo-test-core.log" 2>&1) || { tail -40 "$OUT/cargo-test-core.log" | tee -a "$OUT/job.log"; log "cargo test lab-core: FAILED"; exit 1; }
  grep -E "^test result" "$OUT/cargo-test-core.log" | tee -a "$OUT/job.log"
  log "cargo test lab-core: ok ($(grep -c '^test .* ok$' "$OUT/cargo-test-core.log") tests ok)"
  cargo build --release --locked >> "$OUT/cargo-test.log" 2>&1 || { tail -20 "$OUT/cargo-test.log"; log "cargo build: FAILED"; exit 1; }
  mkdir -p "$OUT/bin" && install -m 755 target/release/lab-live "$OUT/bin/lab-live"
  sha256sum "$OUT/bin/lab-live" | tee -a "$OUT/job.log"
  log "build: done"
  exit 0
fi

[ "$STEP" = smoke ] || { log "unknown STEP=$STEP"; exit 2; }
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
BIN=$OUT/bin/lab-live
[ -x "$BIN" ] || { log "no binary at $BIN: run STEP=build first"; exit 1; }
export PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1
cd "$REPO/lab"
[ -d "$SEGMENTS" ] || { log "no segments at $SEGMENTS"; exit 1; }
rm -rf "$OUT/ref-states" "$OUT/snap" "$OUT/smoke"
# The paper start's groups and cost scenario as paper.engine journals them (the state's text, verbatim), on one slot.
"$PY" -m paper.engine --from $LO --to $((LO + 1)) --config paper/k3-paper-2026-10-12.yaml --groups paper/h-arms-2026-10-16.yaml \
  --no-backup --store "$STORE" --journal "$OUT/ref-states" > "$OUT/ref-states.log" 2>&1 || { tail -20 "$OUT/ref-states.log" | tee -a "$OUT/job.log"; exit 1; }
"$PY" - "$OUT" <<'EOF'
import os, sqlite3, sys
out = sys.argv[1]
db = sqlite3.connect(f"file:{os.path.join(out, 'ref-states', 'journal.sqlite')}?mode=ro", uri=True)
for k in ("groups", "cost_scenario"):
    v = db.execute("SELECT v FROM state WHERE k = ?", [k]).fetchone()[0]
    with open(os.path.join(out, f"{k}.json"), "w") as f:
        f.write(v)
EOF
sha256sum paper/k3-paper-2026-10-12.yaml paper/h-arms-2026-10-16.yaml paper/risk.yaml paper/risk-h.yaml | tee -a "$OUT/job.log"
# fwd1053-1054's snapshot for a replay of the whole store (no farmer history or curve seed before its start).
"$PY" -c "from paper import snapshot_export as s; s.export('$STORE', '$OUT/snap', history=($LO, $HI), seed=False)" \
  > "$OUT/snap.log" 2>&1 || { tail -20 "$OUT/snap.log" | tee -a "$OUT/job.log"; exit 1; }
# lab-live paper on the segments, risk on.
rc=0
"$BIN" paper --segments "$SEGMENTS" --snapshot "$OUT/snap" --from $LO --to $HI --groups "$OUT/groups.json" --variants 0.04:600 \
  --cost-scenario "$OUT/cost_scenario.json" --journal "$OUT/smoke" --summary "$OUT/smoke.json" > "$OUT/smoke.log" 2>&1 || rc=$?
tail -5 "$OUT/smoke.log" | tee -a "$OUT/job.log"
[ $rc = 0 ] || { log "smoke: lab-live paper FAILED (exit $rc)"; exit 1; }
"$PY" - "$OUT/smoke.json" <<'EOF' | tee -a "$OUT/job.log"
import json, sys
v = json.load(open(sys.argv[1]))
s, j, p = v["segments"], v["journal"], v["paper"]
b = s["backfill_rows_not_read"]
print(f"lab-live paper segments [{v['from']}, {v['to']}): {s['read']} segments read ({s['units']} units, incomplete skipped "
      f"{s['incomplete_skipped']}), {s['rows']:,} rows; backfill rows not read: total {b['total']:,}, per segment min "
      f"{b['min']}, median {b['median']}, max {b['max']}, share of all rows {b['share_of_all_rows']:.4%} (measured); "
      f"{v['events']:,} events; journal signals {j['signals']}, orders {j['orders']}, fills {j['fills']}, exits {j['exits']}, "
      f"positions {j['positions']}; risk skips {p['risk_skips']}; peak RSS {v['peak_rss_bytes'] / 1e9:.2f} GB, {v['secs']:.0f} s")
EOF
log "smoke: done"
