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
#                processing time, not live latency), the largest structures at the end, and the seconds of the first
#                (cold) run and of the run with the log.
#   STEP=plateau (the lab Python 3.13 runner; C4-3): spec v0.6's gate (SPEC_SHA below; refused otherwise, logged before the
#                first event). plateau.py's tests first; then P1 and P2 (lab-live paper --mem-log on job 91's segments,
#                risk on, the paper start's files) and P3 (the same range from the chunks, the upper bound) side by side;
#                run.json (P1/P2 journal sha256 without the run's summary, the wallets, exit codes, segments read);
#                plateau.py writes plateau.md and the measure line `lab-live plateau ... GREEN|RED`. A RED gate is a
#                result, not a failed job.
#   STEP=ab      (the lab Python 3.13 runner; C4-4): the farmer-history prune's A/B on job 91's segments: P0 with
#                --keep-farmer-history and P1 with the prune, side by side; their journals and wallet files must be
#                byte-identical. The measure line gives the sweeps, the pairs dropped and both runs' memory at the end.
#   STEP=m2      (the lab Python 3.13 runner; C4-3, M2 in replay): on fwd1053-1054 [LO, HI) the legs parity (the whole
#                store), then the signals parity and RC4 (lab-live paper against paper.engine, the paper start's files,
#                config mode) side by side, each with its GREEN measure line in job.log.
#
#   STEP=build LAB_DATA_ROOT=/lab/data-old-faithful-one/lab bash lab/live/nas_m1c4.sh      (bookworm runner)
#   STEP=smoke LAB_PY=/lab/venv/bin/python bash lab/live/nas_m1c4.sh                       (lab runner; short)
#   STEP=plateau LAB_PY=/lab/venv/bin/python bash lab/live/nas_m1c4.sh                     (lab runner; long: 3 runs of ~52 h)
#   STEP=m2 LAB_PY=/lab/venv/bin/python bash lab/live/nas_m1c4.sh                          (lab runner; long: paper.engine twice)
#   STEP=ab LAB_PY=/lab/venv/bin/python bash lab/live/nas_m1c4.sh                          (lab runner; short: two runs side by side)
#
# Every step writes to $DATA/backtests/live-m1c4-<commit7>/.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
OUT=$DATA/backtests/live-m1c4-${COMMIT:0:7}
mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
: "${STEP:?STEP=build, smoke, ab, plateau or m2}"
log "start $STEP commit=$COMMIT out=$OUT"
SEGMENTS=$DATA/backtests/shadow-arms-1053-1054-2c3e5fe/rec/segments  # job 91's (#299, 2c3e5fe), read again by 99m
STORE=$DATA/store/fwd1053-1054.duckdb
LO=454896000
HI=455760000
SPEC_SHA=e14bdf24c75c00318daef017a96a5ebe56545b835a0e31ef05be7c5cb2e23291   # research/m1-c4-plateau-spec-2026-10-19.md v0.6 (E8)

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
           mem::tests::the_counting_allocator_counts_every_path_exactly \
           legs::tests::the_prune_keeps_every_flag_and_gives_back_the_idle_pairs legs_read::tests::the_farm_prune_changes_no_event \
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

case "$STEP" in smoke | ab | plateau | m2) ;; *) log "unknown STEP=$STEP"; exit 2 ;; esac
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
BIN=$OUT/bin/lab-live
[ -x "$BIN" ] || { log "no binary at $BIN: run STEP=build first"; exit 1; }
export PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1
cd "$REPO/lab"

prepare() {  # the paper start's groups and cost scenario as paper.engine journals them (one slot), fwd1053-1054's snapshot
  rm -rf "$OUT/ref-states" "$OUT/snap"
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
  # a replay of the whole store: no farmer history or curve seed before its start
  "$PY" -c "from paper import snapshot_export as s; s.export('$STORE', '$OUT/snap', history=($LO, $HI), seed=False)" \
    > "$OUT/snap.log" 2>&1 || { tail -20 "$OUT/snap.log" | tee -a "$OUT/job.log"; exit 1; }
}

if [ "$STEP" = ab ]; then
  # C4-4's A/B: the farmer-history prune changes no row. P0 without it (--keep-farmer-history) and P1 with it, on job 91's
  # segments with --mem-log, side by side: their journals (every table but the run's summary) and wallet files must be
  # byte-identical; the measure line gives the prune's sweeps and pairs dropped and both runs' memory at the end.
  [ -d "$SEGMENTS" ] || { log "no segments at $SEGMENTS"; exit 1; }
  prepare
  rm -rf "$OUT/ab0" "$OUT/ab1" "$OUT"/ab[01].json "$OUT"/ab[01].jsonl
  abrun() {  # name, then extra flags
    local name=$1; shift
    "$BIN" paper --segments "$SEGMENTS" --snapshot "$OUT/snap" --from $LO --to $HI --groups "$OUT/groups.json" --variants 0.04:600 \
      --cost-scenario "$OUT/cost_scenario.json" --journal "$OUT/$name" --summary "$OUT/$name.json" --mem-log "$OUT/$name.jsonl" "$@" \
      > "$OUT/$name.log" 2>&1
  }
  abrun ab0 --keep-farmer-history & a=$!
  abrun ab1 & b=$!
  r0=0 r1=0
  wait $a || r0=$?
  wait $b || r1=$?
  for n in ab0 ab1; do tail -2 "$OUT/$n.log" | tee -a "$OUT/job.log"; done
  [ $r0 = 0 ] && [ $r1 = 0 ] || { log "ab: a run failed (exit $r0 / $r1)"; exit 1; }
  "$PY" - "$OUT" "$REPO/lab/live/plateau.py" <<'EOF' | tee -a "$OUT/job.log" || { log "ab: RED"; exit 1; }
import filecmp, json, os, subprocess, sys
out, plateau = sys.argv[1], sys.argv[2]
sha = lambda n: subprocess.run([sys.executable, plateau, "--journal-sha", os.path.join(out, n)], capture_output=True, text=True).stdout.strip()
a, b = sha("ab0"), sha("ab1")
wallets = sorted(f for f in os.listdir(os.path.join(out, "ab0")) if f.startswith("wallet") and f.endswith(".json"))
w_equal = bool(wallets) and all(filecmp.cmp(os.path.join(out, "ab0", f), os.path.join(out, "ab1", f), shallow=False) for f in wallets)
s0, s1 = (json.load(open(os.path.join(out, f"{n}.json"))) for n in ("ab0", "ab1"))
end = lambda n: [json.loads(l) for l in open(os.path.join(out, f"{n}.jsonl"))][-1]
e0, e1 = end("ab0"), end("ab1")
p0, p1 = s0["farm_hist_pruned"], s1["farm_hist_pruned"]
st = lambda e, k, f: e["structures"][k][f]
ok = bool(a) and a == b and w_equal and not p0["on"] and p1["on"] and p1["sweeps"] > 0
print(f"lab-live farm prune A/B on job 91's segments: journals {'equal' if a and a == b else 'DIFFER'} ({a[:12]}), wallets "
      f"{'equal' if w_equal else 'DIFFER'} ({len(wallets)}); prune {p1['sweeps']} sweeps, {p1['pairs_dropped']:,} pairs dropped, "
      f"{p1['pairs_left']:,} left (without: {p0['pairs_left']:,}); at the end farmer_hist_trades {st(e0, 'farmer_hist_trades', 'entries'):,} -> "
      f"{st(e1, 'farmer_hist_trades', 'entries'):,}, heap {e0['heap_bytes'] / 1e9:.2f} -> {e1['heap_bytes'] / 1e9:.2f} GB, peak RSS "
      f"{s0['peak_rss_bytes'] / 1e9:.2f} -> {s1['peak_rss_bytes'] / 1e9:.2f} GB; seconds {s0['secs']:.0f} / {s1['secs']:.0f}; "
      f"{'GREEN' if ok else 'RED'}")
sys.exit(0 if ok else 1)
EOF
  log "ab: done"
  exit 0
fi

if [ "$STEP" = plateau ]; then
  # The spec as pinned (v0.6, E8), into job.log before the first event; refused otherwise.
  SPEC=$DATA/research/m1-c4-plateau-spec-2026-10-19.md
  got=$(sha256sum "$SPEC" | cut -d' ' -f1)
  log "spec $SPEC sha256 $got (pinned $SPEC_SHA)"
  [ "$got" = "$SPEC_SHA" ] || { log "plateau: the spec is not v0.6 as pinned: refused"; exit 1; }
  "$PY" -m pytest -q -p no:cacheprovider paper/tests/test_lab_live_plateau.py > "$OUT/plateau-tests.log" 2>&1 \
    || { tail -15 "$OUT/plateau-tests.log" | tee -a "$OUT/job.log"; log "plateau.py tests: RED"; exit 1; }
  tail -1 "$OUT/plateau-tests.log" | tee -a "$OUT/job.log"
  [ -d "$SEGMENTS" ] || { log "no segments at $SEGMENTS"; exit 1; }
  prepare
  run() {  # name, then the input flags
    local name=$1; shift
    "$BIN" paper "$@" --snapshot "$OUT/snap" --from $LO --to $HI --groups "$OUT/groups.json" --variants 0.04:600 \
      --cost-scenario "$OUT/cost_scenario.json" --journal "$OUT/$name" --summary "$OUT/$name.json" --mem-log "$OUT/$name.jsonl" \
      > "$OUT/$name.log" 2>&1
  }
  rm -rf "$OUT/p1" "$OUT/p2" "$OUT/p3" "$OUT"/p[123].json "$OUT"/p[123].jsonl "$OUT/run.json" "$OUT/plateau.md"
  log "P1 and P2 (segments) and P3 (chunks, the upper bound), side by side"
  run p1 --segments "$SEGMENTS" & a=$!
  run p2 --segments "$SEGMENTS" & b=$!
  run p3 --chunks-root "$DATA/events/v1/chunks" & c=$!
  rc1=0 rc2=0 rc3=0
  wait $a || rc1=$?
  wait $b || rc2=$?
  wait $c || rc3=$?
  for n in p1 p2 p3; do tail -2 "$OUT/$n.log" | tee -a "$OUT/job.log"; done
  log "exit codes p1 $rc1 p2 $rc2 p3 $rc3"
  # run.json: the facts the logs do not hold (plateau.py's determinism and run checks)
  "$PY" - "$OUT" "$REPO/lab/live/plateau.py" "$rc1" "$rc2" "$rc3" <<'EOF'
import filecmp, json, os, subprocess, sys
out, plateau = sys.argv[1], sys.argv[2]
rc = dict(zip(("p1", "p2", "p3"), map(int, sys.argv[3:6])))


def sha(name):
    if rc[name] != 0:
        return None
    r = subprocess.run([sys.executable, plateau, "--journal-sha", os.path.join(out, name)], capture_output=True, text=True)
    return r.stdout.strip() or None


wallets = sorted(f for f in os.listdir(os.path.join(out, "p1")) if f.startswith("wallet") and f.endswith(".json")) if rc["p1"] == 0 else []
p1 = json.load(open(os.path.join(out, "p1.json"))) if rc["p1"] == 0 else {}
seg = p1.get("segments", {})
run = {"p1_journal_sha": sha("p1"), "p2_journal_sha": sha("p2"),
       "wallets_equal": bool(wallets) and rc["p2"] == 0
       and all(filecmp.cmp(os.path.join(out, "p1", f), os.path.join(out, "p2", f), shallow=False) for f in wallets),
       "exit_codes": rc, "segments_read": seg.get("read"), "segments_expected": 3145,
       "incomplete_skipped": seg.get("incomplete_skipped", []), "p1_secs": p1.get("secs")}
if rc["p3"] != 0:  # reported, not gated (spec E5): the reason from its log's last line
    try:
        run["p3_note"] = (open(os.path.join(out, "p3.log")).read().strip().splitlines() or ["(empty log)"])[-1][-300:]
    except OSError as e:
        run["p3_note"] = f"no log: {e}"
json.dump(run, open(os.path.join(out, "run.json"), "w"), indent=1)
print(f"run.json: journals {'equal' if run['p1_journal_sha'] and run['p1_journal_sha'] == run['p2_journal_sha'] else 'DIFFER'}, "
      f"wallets {'equal' if run['wallets_equal'] else 'DIFFER'}, segments {run['segments_read']} of 3145")
EOF
  "$PY" "$REPO/lab/live/plateau.py" --p1 "$OUT/p1.jsonl" --p2 "$OUT/p2.jsonl" --p3 "$OUT/p3.jsonl" --run "$OUT/run.json" \
    --spec-sha "$SPEC_SHA" --out "$OUT/plateau.md" | tee -a "$OUT/job.log"
  (cd "$OUT" && sha256sum plateau.md run.json p1.jsonl p2.jsonl p3.jsonl) | tee -a "$OUT/job.log"
  if grep -q "^lab-live plateau .*GREEN$" "$OUT/job.log"; then log "plateau: GREEN"; else log "plateau: RED (plateau.md says why)"; fi
  exit 0
fi

if [ "$STEP" = m2 ]; then
  # M2 in replay on fwd1053-1054 [LO, HI): the legs and signals parity, then RC4 (lab-live paper against paper.engine
  # with the paper start's files, config mode), each GREEN as in nas_m1b.sh / nas_m1c3.sh.
  NOREGEN="env -u C3_RISK_CASES_REGEN -u C3_CASES_REGEN -u C2_CASES_REGEN -u C1B_CASES_REGEN"
  export LAB_LIVE_BIN=$BIN LAB_LIVE_MEASURE_LOG=$OUT/job.log LAB_LEGS_REQUIRE=1 LAB_SPILL_MAX=16GB
  "$BIN" --version | tee -a "$OUT/job.log"
  log "legs parity on fwd1053-1054 (the whole store)"
  legs=0
  LAB_SPILL_DIR=$OUT/spill-legs LAB_LEGS_STORE=$STORE $NOREGEN "$PY" -m pytest -q -rs -p no:cacheprovider --basetemp "$OUT/pytest-legs" \
    paper/tests/test_lab_live_legs.py > "$OUT/legs.log" 2>&1 || legs=$?
  tail -15 "$OUT/legs.log" | tee -a "$OUT/job.log"
  rm -rf "$OUT/spill-legs"
  if [ $legs != 0 ] || grep -qE "[0-9]+ skipped" "$OUT/legs.log"; then log "legs: RED (exit $legs, legs.log; pytest-legs kept)"; exit 1; fi
  rm -rf "$OUT/pytest-legs"
  log "signals parity and RC4 on fwd1053-1054 [$LO, $HI), side by side"
  rm -rf "$OUT/journals-rc4"
  LAB_SPILL_DIR=$OUT/spill-sig LAB_SIGNALS_STORE=$STORE LAB_SIGNALS_FROM=$LO LAB_SIGNALS_TO=$HI $NOREGEN "$PY" -m pytest -q -rs \
    -p no:cacheprovider --basetemp "$OUT/pytest-sig" paper/tests/test_lab_live_signals.py > "$OUT/signals.log" 2>&1 &
  s=$!
  LAB_SPILL_DIR=$OUT/spill-rc4 LAB_PAPER_STORE=$STORE LAB_PAPER_FROM=$LO LAB_PAPER_TO=$HI LAB_PAPER_MODE=config LAB_PAPER_SEED_PROOF=0 \
    LAB_PAPER_REPORT=0 LAB_PAPER_KEEP=$OUT/journals-rc4 $NOREGEN "$PY" -m pytest -q -rs -p no:cacheprovider --basetemp "$OUT/pytest-rc4" \
    "paper/tests/test_lab_live_paper.py::test_the_rust_paper_journal_is_the_engines" > "$OUT/rc4.log" 2>&1 &
  r=$!
  rs=0 rr=0
  wait $s || rs=$?
  wait $r || rr=$?
  for n in signals rc4; do log "$n:"; tail -20 "$OUT/$n.log" | tee -a "$OUT/job.log"; done
  rm -rf "$OUT/spill-sig" "$OUT/spill-rc4"
  st=$(basename "$STORE")
  sig_ok=0 rc4_ok=0
  sig_line=$(grep -F "lab-live signals [$LO, $HI) on $st: GREEN" "$OUT/job.log" | tail -1 || true)
  rc4_line=$(grep -F "lab-live paper [$LO, $HI) on $st: GREEN" "$OUT/job.log" | grep -F "mode config;" | tail -1 || true)
  [ $rs = 0 ] && [ -n "$sig_line" ] && sig_ok=1
  [ $rr = 0 ] && [ -n "$rc4_line" ] && rc4_ok=1
  [ $sig_ok = 1 ] && rm -rf "$OUT/pytest-sig" || log "signals: RED (exit $rs; signals.log, pytest-sig kept)"
  if [ $rc4_ok = 1 ]; then
    (cd "$OUT" && sha256sum journals-rc4/*/journal.sqlite journals-rc4/*/wallet*.json 2>/dev/null || true) | tee -a "$OUT/job.log"
    rm -rf "$OUT/pytest-rc4"
  else
    log "RC4: RED (exit $rr; both journals in $OUT/journals-rc4, journal_diff.md under $OUT/pytest-rc4)"
  fi
  [ $sig_ok = 1 ] && [ $rc4_ok = 1 ] || { log "m2: RED"; exit 1; }
  log "m2: GREEN (legs, signals and RC4 on fwd1053-1054)"
  exit 0
fi

# STEP=smoke
[ -d "$SEGMENTS" ] || { log "no segments at $SEGMENTS"; exit 1; }
rm -rf "$OUT/smoke" "$OUT/smoke-mem" "$OUT/smoke-mem.jsonl"
prepare
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
top = sorted(end["structures"].items(), key=lambda kv: -kv[1]["bytes_est"])[:8]
grow = {n: (mins[0]["structures"][n]["entries"], end["structures"][n]["entries"]) for n, _ in top}
ok = a == b and w_equal and bool(end.get("end")) and t["n"] == s1["events"] and sum(l["time_ns"]["n"] for l in mins) == s1["events"]
print(f"lab-live mem-log smoke: journals {'equal' if a == b else 'DIFFER'} ({a[:12]}), wallets {'equal' if w_equal else 'DIFFER'} "
      f"({len(wallets)}); {len(mins)} minute lines over {(mins[-1]['minute'] - mins[0]['minute']) / 60:.1f} h, end line "
      f"{bool(end.get('end'))}; RSS {min(rss) / 1e9:.2f}-{max(rss) / 1e9:.2f} GB, heap at the end {end['heap_bytes'] / 1e9:.2f} GB; "
      f"time per event (replay processing, not live latency) p50 {t['p50'] / 1e3:.2f} us, p99 {t['p99'] / 1e3:.2f} us, "
      f"p999 {t['p999'] / 1e3:.2f} us, max {t['max'] / 1e6:.1f} ms over {t['n']:,} events; largest at the end (bytes, entries "
      f"first -> last): " + ", ".join(f"{n} {v['bytes_est'] / 1e6:.0f} MB {grow[n][0]:,}->{grow[n][1]:,}" for n, v in top)
      + f"; seconds of the first run (cold cache) and of the run with the log {s0['secs']:.0f} / {s1['secs']:.0f} (not an overhead measure); {'GREEN' if ok else 'RED'}")
sys.exit(0 if ok else 1)
EOF
log "smoke: done"
