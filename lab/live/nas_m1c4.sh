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
#                C4-2: the same run again with --mem-log (the counting allocator, each structure, the time per event):
#                its journal must equal the first run's (every table but the run's own summary) and the wallet files
#                too; a second measure line gives the log's lines and minutes, RSS and heap, the time per event (replay
#                processing time, not live latency), the largest structures at the end, and the seconds with and
#                without the log.
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
           legs_read::tests::the_mem_log_view_keeps_the_farmer_totals_and_counts_the_segments \
           paper::tests::the_mem_log_writes_a_line_per_minute_and_changes_no_journal_row memlog::tests::a_line_per_minute_then_the_end \
           memlog::tests::the_buckets_are_contiguous_and_at_most_a_32nd_wide memlog::tests::the_quantiles_are_their_buckets_top_capped_by_the_max \
           mem::tests::the_counting_allocator_holds_every_live_byte \
           paper_core::tests::every_c3_case_gives_the_engines_writes_with_risk_on paper::tests::with_risk_on_each_group_reads_its_own_file \
           paper::tests::a_replay_writes_its_paper_journal_the_states_and_the_summary; do
    grep -q "^test $t ... ok$" "$OUT/cargo-test.log" || { log "cargo test: $t did not run: FAILED"; exit 1; }
  done
  log "cargo test: ok ($(grep -c '^test .* ok$' "$OUT/cargo-test.log") tests ok)"
  (cd ../core && cargo test --release --locked > "$OUT/cargo-test-core.log" 2>&1) || { tail -40 "$OUT/cargo-test-core.log" | tee -a "$OUT/job.log"; log "cargo test lab-core: FAILED"; exit 1; }
  grep -E "^test result" "$OUT/cargo-test-core.log" | tee -a "$OUT/job.log"
  for t in signals::tests::the_windowed_structures_stay_bounded_over_thirty_hours mem::tests::the_estimates_are_capacity_times_size; do
    grep -q "^test $t ... ok$" "$OUT/cargo-test-core.log" || { log "cargo test lab-core: $t did not run: FAILED"; exit 1; }
  done
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
rm -rf "$OUT/ref-states" "$OUT/snap" "$OUT/smoke" "$OUT/smoke-mem" "$OUT/smoke-mem.jsonl"
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
# C4-2: the same run with --mem-log; its journal and wallets must be the first run's.
rc=0
"$BIN" paper --segments "$SEGMENTS" --snapshot "$OUT/snap" --from $LO --to $HI --groups "$OUT/groups.json" --variants 0.04:600 \
  --cost-scenario "$OUT/cost_scenario.json" --journal "$OUT/smoke-mem" --summary "$OUT/smoke-mem.json" --mem-log "$OUT/smoke-mem.jsonl" \
  > "$OUT/smoke-mem.log" 2>&1 || rc=$?
tail -3 "$OUT/smoke-mem.log" | tee -a "$OUT/job.log"
[ $rc = 0 ] || { log "smoke: lab-live paper --mem-log FAILED (exit $rc)"; exit 1; }
"$PY" - "$OUT" <<'EOF' | tee -a "$OUT/job.log" || { log "smoke: the mem-log run is RED"; exit 1; }
import filecmp, hashlib, json, os, sqlite3, sys
out = sys.argv[1]


def journal_sha(d):  # every table, rows sorted; the run's own summary (seconds, RSS, the mem-log entry) left out
    db = sqlite3.connect(f"file:{os.path.join(d, 'journal.sqlite')}?mode=ro", uri=True)
    h = hashlib.sha256()
    for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall():
        rows = db.execute(f'SELECT * FROM "{t}"').fetchall()
        if t == "state":
            rows = [r for r in rows if r[0] != "lab_live_paper"]
        h.update(t.encode() + b"\n")
        for r in sorted(rows, key=repr):
            h.update(repr(r).encode() + b"\n")
    return h.hexdigest()


a, b = journal_sha(os.path.join(out, "smoke")), journal_sha(os.path.join(out, "smoke-mem"))
wallets = sorted(f for f in os.listdir(os.path.join(out, "smoke")) if f.startswith("wallet") and f.endswith(".json"))
w_equal = bool(wallets) and all(filecmp.cmp(os.path.join(out, "smoke", f), os.path.join(out, "smoke-mem", f), shallow=False) for f in wallets)
lines = [json.loads(l) for l in open(os.path.join(out, "smoke-mem.jsonl"))]
mins, end = [l for l in lines if not l.get("end")], lines[-1]
s0, s1 = json.load(open(os.path.join(out, "smoke.json"))), json.load(open(os.path.join(out, "smoke-mem.json")))
rss = [l["rss_bytes"] for l in mins if l.get("rss_bytes")]
t = end["time_ns"]
top = sorted(end["structures"].items(), key=lambda kv: -kv[1]["bytes"])[:8]
grow = {n: (mins[0]["structures"][n]["entries"], end["structures"][n]["entries"]) for n, _ in top}
ok = a == b and w_equal and bool(end.get("end")) and t["n"] == s1["events"] and sum(l["time_ns"]["n"] for l in mins) == s1["events"]
print(f"lab-live mem-log smoke: journals {'equal' if a == b else 'DIFFER'} ({a[:12]}), wallets {'equal' if w_equal else 'DIFFER'} "
      f"({len(wallets)}); {len(mins)} minute lines over {(mins[-1]['minute'] - mins[0]['minute']) / 60:.1f} h, end line "
      f"{bool(end.get('end'))}; RSS {min(rss) / 1e9:.2f}-{max(rss) / 1e9:.2f} GB, heap at the end {end['heap_bytes'] / 1e9:.2f} GB; "
      f"time per event (replay processing, not live latency) p50 {t['p50'] / 1e3:.2f} us, p99 {t['p99'] / 1e3:.2f} us, "
      f"p999 {t['p999'] / 1e3:.2f} us, max {t['max'] / 1e6:.1f} ms over {t['n']:,} events; largest at the end (bytes, entries "
      f"first -> last): " + ", ".join(f"{n} {v['bytes'] / 1e6:.0f} MB {grow[n][0]:,}->{grow[n][1]:,}" for n, v in top)
      + f"; seconds without / with the log {s0['secs']:.0f} / {s1['secs']:.0f}; {'GREEN' if ok else 'RED'}")
sys.exit(0 if ok else 1)
EOF
log "smoke: done"
