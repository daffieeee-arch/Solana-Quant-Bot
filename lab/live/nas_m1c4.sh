#!/bin/bash
# M1 C4 of the Rust live route (lab/docs/rust-live-route-design-2026-10-16.md §3, §16; the spec is
# research/m1-c4-plateau-spec-2026-10-19.md) on the NAS: lab-live in live form, the recorder's segments, before any VPS
# or streaming test. C4-1 adds the segment reader; C4-2 the memory log; C4-3 the plateau gate, the 1054 legs/signals
# parity and RC4 (STEP=plateau); C4-4 the farmer-history prune (STEP=ab); C4-5 the farmer histories boxed (the table holds
# 16 B per bucket, a resize is no longer a 237 MB step), the key buffer growing by 1 MiB steps, and the hash tables' shape
# in the memory log (reported in plateau.md, not gated).
#
#   STEP=build   (bookworm runner, Rust 1.97.1): cargo test --release --locked of lab/live (the segments against the
#                chunks on the fixture, and every earlier test) and of ALL of lab/core, named tests required to have run;
#                then the release binary into $OUT/bin/. Run with LAB_DATA_ROOT=/lab/data-old-faithful-one/lab. Since
#                STEP=repro, through lab/scripts/rust-repro.sh: cargo reaches the checkout by one fixed path
#                ($LAB_RUST_ROOT/lab, a symlink, under a lock) and maps it and CARGO_HOME to /src and /cargo, so the
#                binary's sha256 does not depend on the checkout's path. lab/scripts/test-rust-repro.sh runs first (the
#                helper's refusals and its lock, without cargo); STEP=repro runs it too.
#   STEP=repro   (bookworm runner): the proof of that: two release builds of this commit from two copies of lab/ at
#                other paths (another depth and length), each with a fresh target dir; their sha256 must be equal, and
#                equal to STEP=build's binary when it is there (run STEP=build first). Each binary is searched for its
#                copy's path, CARGO_HOME, the link's root and /lab/code (expected: none).
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
#                C4-5: P1's rows must also equal C4-4's P1 (8a3d21b, job 99zza: its ab1 journal, AB_REF_DIR below), so
#                the boxed histories and the key buffer change no row. Across commits that is every table but `state`
#                (plateau.py --journal-sha --tables-only): `state` holds the inputs as given (the groups text names the
#                checkout's risk files, a path per commit; the inputs' sha256 are in each job.log) and the run's summary.
#                AB_REF_DIR= (empty) skips it. The line adds farmer_hist's table at the end.
#   STEP=long-b, long-shadow, long-a (the lab Python 3.13 runner, its 24G; C4 option (c), the design
#                research/m1-c4-long-replay-design-2026-10-19.md): a replay of dev2's epochs 1033-1036 from the store's
#                start ([446,256,000, 447,984,000), 151.9 h, ~5.3 days of decisions after the 24 h burn-in). Each builds
#                (or checks) $LONG_ROOT first: a symlink to each of the 8 chunks, nothing else, each complete with the
#                store's extractor build (long_root, fail-closed; never under events/v1/chunks, so neither shadow.py nor
#                lab-live sees another chunk, the hold-out's files among them).
#                long-b: lab-live on those chunks (the chunk form, the upper bound), plateau.py --form chunks: reported,
#                not gated (spec E5). long-shadow: paper.shadow on dev2 over the range with job 91's flags, writing the
#                recorder's segments to $LONG_SHADOW (36-43 h, ended after LONG_SHADOW_HOURS, default 46). long-a:
#                lab-live P1 and P2 on those segments, judged by spec v0.6 as pinned on the last 24 h, plus plateau.py
#                --daily (reported: a day after the warm-up above 150 MB must be explained there before M3 builds on it).
#   STEP=engine-only (the lab Python 3.13 runner, its 24G; M3-04): paper.shadow --engine-only on fwd1053-1054 with job
#                91's flags, through a root of only that store's 4 chunks ($DATA/events/v1/chunks-fwd1053-1054, long_root
#                with arguments): recorder A's segments and recorder B's (b2's engine-only filter), B's rows checked within
#                A's, both volumes in GB/day and the transactions per stream into job.log. The whole store: > 20 h (~23.6 s
#                a segment); EO_TO=<slot> ends the window there (from the store's start), EO_HOURS (default 20).
#   STEP=m2      (the lab Python 3.13 runner; C4-3, M2 in replay): on fwd1053-1054 [LO, HI) the legs parity (the whole
#                store), then the signals parity and RC4 (lab-live paper against paper.engine, the paper start's files,
#                config mode) side by side, each with its GREEN measure line in job.log.
#
#   STEP=build LAB_DATA_ROOT=/lab/data-old-faithful-one/lab bash lab/live/nas_m1c4.sh      (bookworm runner)
#   STEP=repro LAB_DATA_ROOT=/lab/data-old-faithful-one/lab bash lab/live/nas_m1c4.sh      (bookworm runner; short: two clean builds)
#   STEP=smoke LAB_PY=/lab/venv/bin/python bash lab/live/nas_m1c4.sh                       (lab runner; short)
#   STEP=plateau LAB_PY=/lab/venv/bin/python bash lab/live/nas_m1c4.sh                     (lab runner; short: 3 runs over ~52 h of input, P1 ~1 min)
#   STEP=m2 LAB_PY=/lab/venv/bin/python bash lab/live/nas_m1c4.sh                          (lab runner; long: paper.engine twice)
#   STEP=ab LAB_PY=/lab/venv/bin/python bash lab/live/nas_m1c4.sh                          (lab runner; short: two runs side by side)
#   STEP=long-b LAB_PY=/lab/venv/bin/python bash lab/live/nas_m1c4.sh                      (lab runner, 24G; short: ~15-30 min, heap ~7 GB)
#   STEP=long-shadow LAB_PY=/lab/venv/bin/python bash lab/live/nas_m1c4.sh                 (lab runner, 24G; long: 36-43 h)
#   STEP=long-a LAB_PY=/lab/venv/bin/python bash lab/live/nas_m1c4.sh                      (lab runner; short: after long-shadow)
#   STEP=engine-only LAB_PY=/lab/venv/bin/python bash lab/live/nas_m1c4.sh                 (lab runner, 24G; long: > 20 h)
#   STEP=engine-only EO_TO=455328000 LAB_PY=/lab/venv/bin/python bash lab/live/nas_m1c4.sh (epoch 1053 only: ~10 h)
#
# Every step writes to $DATA/backtests/live-m1c4-<commit7>/.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
OUT=$DATA/backtests/live-m1c4-${COMMIT:0:7}
mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
: "${STEP:?STEP=build, repro, smoke, ab, plateau, m2, long-b, long-shadow, long-a or engine-only}"
log "start $STEP commit=$COMMIT out=$OUT"
SEGMENTS=$DATA/backtests/shadow-arms-1053-1054-2c3e5fe/rec/segments  # job 91's (#299, 2c3e5fe), read again by 99m
STORE=$DATA/store/fwd1053-1054.duckdb
LO=454896000
HI=455760000
SPEC_SHA=e14bdf24c75c00318daef017a96a5ebe56545b835a0e31ef05be7c5cb2e23291   # research/m1-c4-plateau-spec-2026-10-19.md v0.6 (E8)
AB_REF_DIR=${AB_REF_DIR-$DATA/backtests/live-m1c4-8a3d21b/ab1}   # C4-4's P1 journal on these segments (8a3d21b, 99zza)
# C4 option (c), research/m1-c4-long-replay-design-2026-10-19.md: dev2's epochs 1033-1036 from its start (8 chunks)
LONG_STORE=$DATA/store/dev2.duckdb
LONG_LO=446256000
LONG_HI=447984000
LONG_N=8
LONG_ROOT=${LONG_ROOT:-$DATA/events/v1/chunks-1033-1036}   # beside events/v1/chunks, not under it (long_root)
LONG_SHADOW=${LONG_SHADOW:-$OUT/long-shadow}               # the output of STEP=long-shadow; long-a reads its rec/segments
LONG_SPILL=${LONG_SPILL:-/lab/tmp/shadow-long}            # the DuckDB spill of paper.shadow (job 91: /lab/tmp/shadow-arms)

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
  if ! bash "$REPO/lab/scripts/test-rust-repro.sh" > "$OUT/test-rust-repro.log" 2>&1; then
    tail -5 "$OUT/test-rust-repro.log" | tee -a "$OUT/job.log"; log "test-rust-repro: FAILED"; exit 1
  fi
  tail -1 "$OUT/test-rust-repro.log" | tee -a "$OUT/job.log"
  # M3-01: no signing-capable crate in lab-live's and lab-core's locks (lab/security/no-signing.toml; CI runs it in the
  # dependency-audit workflow); here when the runner has python3
  if command -v python3 > /dev/null; then
    nosign=0
    python3 "$REPO/lab/security/dependency_audit.py" no-signing > "$OUT/no-signing.log" 2>&1 || nosign=$?
    tee -a "$OUT/job.log" < "$OUT/no-signing.log"
    [ $nosign = 0 ] || { log "no-signing: FAILED (rc $nosign)"; exit 1; }
  else
    log "no-signing: no python3 on this runner (CI checks it in the dependency-audit workflow)"
  fi
  . "$REPO/lab/scripts/rust-repro.sh"
  rust_repro "$REPO" || { log "rust_repro: FAILED"; exit 1; }
  log "cargo through $LAB_RUST -> $(readlink "$LAB_RUST"); RUSTFLAGS=$RUSTFLAGS CFLAGS=$CFLAGS"
  cargo test --release --locked --manifest-path "$LAB_RUST/live/Cargo.toml" > "$OUT/cargo-test.log" 2>&1 || { tail -40 "$OUT/cargo-test.log" | tee -a "$OUT/job.log"; log "cargo test: FAILED"; exit 1; }
  grep -E "^test |^test result|^running" "$OUT/cargo-test.log" | tee -a "$OUT/job.log"
  for t in legs_read::tests::segments_give_the_chunks_events_and_count_their_backfill legs_read::tests::the_chunks_become_the_stores_event_stream \
           legs_read::tests::the_mem_log_view_keeps_the_farmer_totals_and_counts_the_segments \
           paper::tests::the_mem_log_writes_a_line_per_minute_and_changes_no_journal_row memlog::tests::a_line_per_minute_then_the_end \
           memlog::tests::the_buckets_are_contiguous_and_at_most_a_32nd_wide memlog::tests::the_quantiles_are_their_buckets_top_capped_by_the_max \
           mem::tests::the_counting_allocator_counts_every_path_exactly \
           legs::tests::the_prune_keeps_every_flag_and_gives_back_the_idle_pairs legs_read::tests::the_farm_prune_changes_no_event \
           paper_core::tests::every_c3_case_gives_the_engines_writes_with_risk_on paper::tests::with_risk_on_each_group_reads_its_own_file \
           paper::tests::a_replay_writes_its_paper_journal_the_states_and_the_summary \
           legs::tests::a_boxed_farm_history_is_72_bytes_and_its_sums_stay_exact_beyond_i64 legs::tests::the_key_buffer_grows_by_steps_not_doublings \
           memlog::tests::a_churned_table_resizes_once_then_stays; do
    grep -q "^test $t ... ok$" "$OUT/cargo-test.log" || { log "cargo test: $t did not run: FAILED"; exit 1; }
  done
  log "cargo test: ok ($(grep -c '^test .* ok$' "$OUT/cargo-test.log") tests ok)"
  cargo test --release --locked --manifest-path "$LAB_RUST/core/Cargo.toml" > "$OUT/cargo-test-core.log" 2>&1 || { tail -40 "$OUT/cargo-test-core.log" | tee -a "$OUT/job.log"; log "cargo test lab-core: FAILED"; exit 1; }
  grep -E "^test result" "$OUT/cargo-test-core.log" | tee -a "$OUT/job.log"
  for t in signals::tests::the_windowed_structures_stay_bounded_over_thirty_hours mem::tests::the_estimates_are_capacity_times_size; do
    grep -q "^test $t ... ok$" "$OUT/cargo-test-core.log" || { log "cargo test lab-core: $t did not run: FAILED"; exit 1; }
  done
  log "cargo test lab-core: ok ($(grep -c '^test .* ok$' "$OUT/cargo-test-core.log") tests ok)"
  cargo build --release --locked --manifest-path "$LAB_RUST/live/Cargo.toml" >> "$OUT/cargo-test.log" 2>&1 || { tail -20 "$OUT/cargo-test.log"; log "cargo build: FAILED"; exit 1; }
  mkdir -p "$OUT/bin" && install -m 755 target/release/lab-live "$OUT/bin/lab-live"
  sha256sum "$OUT/bin/lab-live" | tee -a "$OUT/job.log"
  log "build: done"
  exit 0
fi

if [ "$STEP" = repro ]; then
  rustc -V | tee -a "$OUT/job.log"
  cargo -V | tee -a "$OUT/job.log"
  if ! bash "$REPO/lab/scripts/test-rust-repro.sh" > "$OUT/test-rust-repro.log" 2>&1; then
    tail -5 "$OUT/test-rust-repro.log" | tee -a "$OUT/job.log"; log "test-rust-repro: FAILED"; exit 1
  fi
  tail -1 "$OUT/test-rust-repro.log" | tee -a "$OUT/job.log"
  . "$REPO/lab/scripts/rust-repro.sh"
  home=${CARGO_HOME:-$HOME/.cargo}
  rm -rf "$OUT/repro"
  dirs=("$OUT/repro/a" "$OUT/repro/another-checkout/one-level-deeper/x")
  shas=()
  for d in "${dirs[@]}"; do
    mkdir -p "$d"
    tar -C "$REPO" --exclude=target --exclude=__pycache__ --exclude=.pytest_cache -cf - lab | tar -xf - -C "$d"
    rust_repro "$d" || { log "rust_repro $d: FAILED"; exit 1; }
    log "repro: building $d through $LAB_RUST (RUSTFLAGS=$RUSTFLAGS)"
    cargo build --release --locked --manifest-path "$LAB_RUST/live/Cargo.toml" > "$d.log" 2>&1 \
      || { tail -20 "$d.log" | tee -a "$OUT/job.log"; log "repro: the build of $d FAILED"; exit 1; }
    bin=$d/lab/live/target/release/lab-live
    sha=$(sha256sum "$bin" | cut -d' ' -f1)
    shas+=("$sha")
    left=$(for p in "$d" "$(cd "$d" && pwd -P)" "$home" "$(dirname "$LAB_RUST")" /lab/code; do
             printf '%s:%s ' "$p" "$(grep -c -F -a "$p" "$bin" || true)"; done)
    log "repro: $sha (paths left in it: $left)"
  done
  rust_repro "$REPO" || { log "rust_repro: FAILED"; exit 1; }  # the link back to this checkout
  ref="" same="" other=""
  if [ -x "$OUT/bin/lab-live" ]; then
    ref=$(sha256sum "$OUT/bin/lab-live" | cut -d' ' -f1)
    log "repro: the STEP=build binary $ref (from $REPO)"
    same=", equal to the STEP=build binary" other=" (the STEP=build binary: $ref)"
  fi
  if [ "${shas[0]}" = "${shas[1]}" ] && { [ -z "$ref" ] || [ "$ref" = "${shas[0]}" ]; }; then
    log "lab-live repro: two copies at other paths give ${shas[0]}$same: GREEN"
    rm -rf "${dirs[0]}/lab/live/target" "${dirs[1]}/lab/live/target"
  else
    cmp "${dirs[0]}/lab/live/target/release/lab-live" "${dirs[1]}/lab/live/target/release/lab-live" | head -3 | tee -a "$OUT/job.log" || true
    log "lab-live repro: ${shas[0]} vs ${shas[1]}$other: RED (both target dirs kept)"
    exit 1
  fi
  exit 0
fi

case "$STEP" in smoke | ab | plateau | m2 | long-b | long-shadow | long-a | engine-only) ;; *) log "unknown STEP=$STEP"; exit 2 ;; esac
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
BIN=$OUT/bin/lab-live
[ -x "$BIN" ] || { log "no binary at $BIN: run STEP=build first"; exit 1; }
export PYTHONPATH=$REPO/lab PYTHONDONTWRITEBYTECODE=1
cd "$REPO/lab"

prepare() {  # [store lo hi dir]: the paper start's groups and cost scenario as paper.engine journals them (one slot) and the
  # store's snapshot into dir; without arguments job 91's (fwd1053-1054, [LO, HI), into $OUT)
  local store=${1:-$STORE} lo=${2:-$LO} hi=${3:-$HI} dir=${4:-$OUT}
  mkdir -p "${dir:?}"
  rm -rf "${dir:?}/ref-states" "${dir:?}/snap"
  "$PY" -m paper.engine --from $lo --to $((lo + 1)) --config paper/k3-paper-2026-10-12.yaml --groups paper/h-arms-2026-10-16.yaml \
    --no-backup --store "$store" --journal "$dir/ref-states" > "$dir/ref-states.log" 2>&1 || { tail -20 "$dir/ref-states.log" | tee -a "$OUT/job.log"; exit 1; }
  "$PY" - "$dir" <<'EOF'
import os, sqlite3, sys
out = sys.argv[1]
db = sqlite3.connect(f"file:{os.path.join(out, 'ref-states', 'journal.sqlite')}?mode=ro", uri=True)
for k in ("groups", "cost_scenario"):
    v = db.execute("SELECT v FROM state WHERE k = ?", [k]).fetchone()[0]
    with open(os.path.join(out, f"{k}.json"), "w") as f:
        f.write(v)
EOF
  sha256sum paper/k3-paper-2026-10-12.yaml paper/h-arms-2026-10-16.yaml paper/risk.yaml paper/risk-h.yaml | tee -a "$OUT/job.log"
  # a replay from the store's start: no farmer history or curve seed before it
  "$PY" -c "from paper import snapshot_export as s; s.export('$store', '$dir/snap', history=($lo, $hi), seed=False)" \
    > "$dir/snap.log" 2>&1 || { tail -20 "$dir/snap.log" | tee -a "$OUT/job.log"; exit 1; }
}

long_root() {  # [lo hi n root meta]: a symlink per chunk of [lo, hi) under events/v1/chunks in root, nothing else, checked
  # fail-closed against the store chunk_list in meta (a snapshot's meta.json); without arguments the long steps' range
  # (LONG_LO, LONG_HI, LONG_N, LONG_ROOT, $OUT/long/snap/meta.json)
  local lo=${1:-$LONG_LO} hi=${2:-$LONG_HI} n=${3:-$LONG_N} root=${4:-$LONG_ROOT} meta=${5:-$OUT/long/snap/meta.json} rc=0
  # (not `| tee`: under set -e and pipefail a refusal would end the job before its reason reached job.log)
  "$PY" - "$DATA/events/v1/chunks" "$root" $lo $hi $n "$meta" > "$OUT/long_root.log" 2>&1 <<'EOF' || rc=$?
import json, os, sys
src, root, lo, hi, n, meta = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]), sys.argv[6]
fail = lambda why: sys.exit(f"long_root: {why}: refused")
if os.path.realpath(root).startswith(os.path.realpath(src) + os.sep):
    fail(f"{root} lies under {src}")
# the store's chunks of the range (the snapshot copies its chunk_list): they tile [lo, hi), n of them
want = sorted((c["slots"][0], c["slots"][1], c["extractor_sha256"]) for c in json.load(open(meta))["chunk_list"]
              if c["slots"][0] < hi and c["slots"][1] > lo)
if len(want) != n or want[0][0] != lo or want[-1][1] != hi or any(a[1] != b[0] for a, b in zip(want, want[1:])):
    fail(f"the store's chunks of [{lo}, {hi}) are {[(a, b) for a, b, _ in want]}, not {n} that tile it")
links = {}
for a, b, sha in want:
    d = os.path.join(src, f"{a}-{b}")
    man = json.load(open(os.path.join(d, "_manifest.json")))
    if (man.get("status"), man.get("slot_start"), man.get("slot_end_exclusive"), man.get("extractor_sha256")) != ("complete", a, b, sha):
        fail(f"{d}: status {man.get('status')}, slots {man.get('slot_start')}-{man.get('slot_end_exclusive')}, "
             f"extractor {str(man.get('extractor_sha256'))[:12]} (the store's {sha[:12]})")
    links[f"{a}-{b}"] = os.path.realpath(d)
if os.path.lexists(root):
    have = sorted(os.listdir(root))
    if have != sorted(links) or any(not os.path.islink(os.path.join(root, k)) or os.path.realpath(os.path.join(root, k)) != v
                                    for k, v in links.items()):
        fail(f"{root} exists and holds {have}, not exactly the {n} links (left as it is)")
else:
    tmp = root + ".tmp"
    if os.path.lexists(tmp):
        fail(f"{tmp} exists (left as it is)")
    os.makedirs(tmp)
    for k, v in links.items():
        os.symlink(v, os.path.join(tmp, k))
    os.rename(tmp, root)
print(f"long_root: {root}: {n} chunks [{lo}, {hi}), each complete with the store's extractor build {want[0][2][:12]}... "
      f"({len({x[2] for x in want})} build(s))")
EOF
  tee -a "$OUT/job.log" < "$OUT/long_root.log"
  [ $rc = 0 ] || { log "long_root: FAILED (rc $rc)"; exit 1; }
}

if [ "$STEP" = engine-only ]; then
  # M3-04 (research/m3-design-2026-10-19.md): paper.shadow --engine-only on fwd1053-1054 with job 91's flags (the whole
  # store, --seed 1053, K3 0.04:600 and the H arms, --memory 1GB), its chunks through a root of only that store's 4 chunks
  # (long_root, checked against the store's chunk_list): recorder A's segments as job 91's, recorder B's (b2's
  # engine-only filter) beside them, B's rows checked within A's and both volumes. The whole store took more than 20 h
  # (99zzm2: ~23.6 s a segment, 3,145 segments); EO_TO (an end slot, exclusive) bounds the window from the store's own
  # start, so the warm-up and the seed stay the whole run's; EO_HOURS (default 20) ends it before the next job. The measure lines go to job.log. LAB_DUCKDB_TMP: the B-within-A check's EXCEPT ALL over
  # all of A's rows (--memory 1GB) spills into EO_SPILL, not the checkout.
  EO_ROOT=${EO_ROOT:-$DATA/events/v1/chunks-fwd1053-1054}
  EO_OUT=${EO_OUT:-$OUT/engine-only}
  prepare "$STORE" $LO $HI "$OUT/eo"  # for long_root: the store's chunk_list in the snapshot's meta.json
  long_root $LO $HI 4 "$EO_ROOT" "$OUT/eo/snap/meta.json"
  [ ! -e "$EO_OUT" ] || { log "engine-only: $EO_OUT exists (left as it is): move it away first"; exit 1; }
  mkdir -p "${EO_SPILL:=/lab/tmp/shadow-b}"
  log "engine-only: paper.shadow --engine-only into $EO_OUT (fwd1053-1054, slots [$LO, ${EO_TO:-$HI})), at most ${EO_HOURS:-20} h"
  rc=0
  LAB_ENGINE_COMMIT=$COMMIT LAB_SPILL_DIR=$EO_SPILL LAB_SPILL_MAX=16GB LAB_DUCKDB_TMP=$EO_SPILL nice -n 10 timeout "${EO_HOURS:-20}h" "$PY" -m paper.shadow \
    --store "$STORE" --chunks "$EO_ROOT" --out "$EO_OUT" --memory 1GB --seed 1053 --variants 0.04:600 \
    --groups "$REPO/lab/paper/h-arms-2026-10-16.yaml" --engine-only ${EO_TO:+--to "$EO_TO"} > "$OUT/engine-only.log" 2>&1 || rc=$?
  tail -5 "$OUT/engine-only.log" | tee -a "$OUT/job.log"
  sum=0
  "$PY" - "$EO_OUT/compare.json" > "$OUT/engine-only-summary.log" 2>&1 <<'EOF' || sum=$?
import json, sys
v = json.load(open(sys.argv[1]))
ck, a, b = v.get("engine_only_check", {}), v.get("recorder", {}), v.get("recorder_b", {})
streams = ("tx_watchlist", "tx_probes", "tx_new_pools", "tx_new_reversed", "tx_pump_creates", "backfill_txs", "pool_creations")
print(f"engine-only: B's rows within A's {ck.get('ok')} (blocks equal {ck.get('blocks_equal')}, "
      f"{sum(t['b_rows'] for t in ck.get('tables', {}).values()):,} rows of B in {len(ck.get('tables', {}))} tables); "
      f"GB/day A {v.get('estimated_gb_per_day')} B {v.get('estimated_gb_per_day_b')} (3 KB per transaction); unexplained {v.get('unexplained')}")
print("engine-only: transactions per stream A " + ", ".join(f"{s} {a.get(s, 0):,}" for s in streams))
print("engine-only: transactions per stream B " + ", ".join(f"{s} {b.get(s, 0):,}" for s in streams))
# GREEN only with B within A and the live side's differences all explained (LAB review on #418)
ok = ck.get("ok") is True and v.get("unexplained") == 0
print(f"engine-only: {'GREEN' if ok else 'RED'} (B within A {ck.get('ok')}, unexplained {v.get('unexplained')})")
sys.exit(0 if ok else 1)
EOF
  tee -a "$OUT/job.log" < "$OUT/engine-only-summary.log"
  status=$("$PY" -c 'import json, sys; d = json.load(open(sys.argv[1])); print(d["step"], "at", d.get("time"))' "$EO_OUT/STATUS.json" 2>/dev/null || echo none)
  log "engine-only: rc $rc; summary rc $sum; status $status"
  [ $rc = 0 ] && [ $sum = 0 ] || exit 1
  exit 0
fi

if [ "$STEP" = long-b ]; then
  # C4 option (c) B: lab-live on dev2's chunks of epochs 1033-1036 from the store's start, risk on, the paper start's
  # files: the chunk form, the upper bound, reported and not gated (spec E5; plateau.py --form chunks)
  prepare "$LONG_STORE" $LONG_LO $LONG_HI "$OUT/long"
  long_root
  rm -rf "${OUT:?}/long-b" "${OUT:?}/long-b.json" "${OUT:?}/long-b.jsonl" "${OUT:?}/long-b.md"
  "$BIN" paper --chunks-root "$LONG_ROOT" --snapshot "$OUT/long/snap" --from $LONG_LO --to $LONG_HI --groups "$OUT/long/groups.json" \
    --variants 0.04:600 --cost-scenario "$OUT/long/cost_scenario.json" --journal "$OUT/long-b" --summary "$OUT/long-b.json" \
    --mem-log "$OUT/long-b.jsonl" > "$OUT/long-b.log" 2>&1 || { tail -20 "$OUT/long-b.log" | tee -a "$OUT/job.log"; log "long-b: lab-live FAILED"; exit 1; }
  tail -2 "$OUT/long-b.log" | tee -a "$OUT/job.log"
  rc=0
  "$PY" - "$OUT/long-b.json" $LONG_N > "$OUT/long-b-check.log" 2>&1 <<'EOF' || rc=$?
import json, sys
v = json.load(open(sys.argv[1]))
n = len(v.get("h24", []))
print(f"long-b: {n} chunks read (expected {sys.argv[2]}), chunks_verified {v.get('chunks_verified')}, events {v.get('events')}, "
      f"peak RSS {v.get('peak_rss_bytes', 0) / 1e9:.2f} GB, {v.get('secs', 0):.0f} s")
sys.exit(0 if n == int(sys.argv[2]) and v.get("chunks_verified") is True else 1)
EOF
  tee -a "$OUT/job.log" < "$OUT/long-b-check.log"
  [ $rc = 0 ] || { log "long-b: not the expected chunks"; exit 1; }
  "$PY" "$REPO/lab/live/plateau.py" --p1 "$OUT/long-b.jsonl" --spec-sha "$SPEC_SHA" --out "$OUT/long-b.md" --form chunks | tee -a "$OUT/job.log"
  (cd "$OUT" && sha256sum long-b.md long-b.json long-b.jsonl) | tee -a "$OUT/job.log"
  log "long-b: done (reported, not gated)"
  exit 0
fi

if [ "$STEP" = long-shadow ]; then
  # C4 option (c) A, part 1: the recorder's segments of epochs 1033-1036, simulated by paper.shadow from dev2 and its
  # chunks with job 91's flags (coordinator 2026-10-19: no --d, no --pump-creates, the default gaps and reconnects), the
  # chunks root filtered to the range. Expect 36-43 h; LONG_SHADOW_HOURS (default 46) ends it before the next job.
  prepare "$LONG_STORE" $LONG_LO $LONG_HI "$OUT/long"  # long_root checks the chunks against this snapshot's chunk_list
  long_root
  [ ! -e "$LONG_SHADOW" ] || { log "long-shadow: $LONG_SHADOW exists (left as it is): move it away first"; exit 1; }
  mkdir -p "$LONG_SPILL"
  log "long-shadow: paper.shadow into $LONG_SHADOW for [$LONG_LO, $LONG_HI), at most ${LONG_SHADOW_HOURS:-46} h"
  rc=0
  LAB_ENGINE_COMMIT=$COMMIT LAB_SPILL_DIR=$LONG_SPILL LAB_SPILL_MAX=16GB nice -n 10 timeout "${LONG_SHADOW_HOURS:-46}h" "$PY" -m paper.shadow \
    --store "$LONG_STORE" --chunks "$LONG_ROOT" --out "$LONG_SHADOW" --from $LONG_LO --to $LONG_HI --memory 1GB --seed 1033 \
    --variants 0.04:600 --groups "$REPO/lab/paper/h-arms-2026-10-16.yaml" > "$OUT/long-shadow.log" 2>&1 || rc=$?
  tail -5 "$OUT/long-shadow.log" | tee -a "$OUT/job.log"
  segs=$(find "$LONG_SHADOW/rec/segments" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)
  complete=$(grep -l '"complete": *true' "$LONG_SHADOW"/rec/segments/*/_segment.json 2>/dev/null | wc -l || true)
  # shadow.py writes <out>/STATUS.json, {"step": the last finished step, "time": ...} (LAB review on #412)
  status=$("$PY" -c 'import json, sys; d = json.load(open(sys.argv[1])); print(d["step"], "at", d.get("time"))' "$LONG_SHADOW/STATUS.json" 2>/dev/null || echo none)
  log "long-shadow: rc $rc; status $status; $segs segments, $complete complete; $(du -sh "$LONG_SHADOW/rec/segments" 2>/dev/null | cut -f1)"
  exit $rc
fi

if [ "$STEP" = long-a ]; then
  # C4 option (c) A, part 2: lab-live P1 and P2 on long-shadow's segments (risk on, the paper start's files, a dev2
  # snapshot from the store's start), judged by spec v0.6 as pinned on the last 24 h, plus the per-day view (reported:
  # a day after the warm-up above 150 MB must be explained in it before M3 builds on it).
  SPEC=$DATA/research/m1-c4-plateau-spec-2026-10-19.md
  got=$(sha256sum "$SPEC" | cut -d' ' -f1)
  log "spec $SPEC sha256 $got (pinned $SPEC_SHA)"
  [ "$got" = "$SPEC_SHA" ] || { log "long-a: the spec is not v0.6 as pinned: refused"; exit 1; }
  "$PY" -m pytest -q -p no:cacheprovider paper/tests/test_lab_live_plateau.py > "$OUT/plateau-tests.log" 2>&1 \
    || { tail -15 "$OUT/plateau-tests.log" | tee -a "$OUT/job.log"; log "plateau.py tests: RED"; exit 1; }
  tail -1 "$OUT/plateau-tests.log" | tee -a "$OUT/job.log"
  SEGS=${LONG_SEGMENTS:-$LONG_SHADOW/rec/segments}
  [ -d "$SEGS" ] || { log "long-a: no segments at $SEGS (STEP=long-shadow first, or LONG_SEGMENTS)"; exit 1; }
  expected=$(find "$SEGS" -mindepth 1 -maxdepth 1 -type d | wc -l)
  prepare "$LONG_STORE" $LONG_LO $LONG_HI "$OUT/long"
  arun() {  # name
    "$BIN" paper --segments "$SEGS" --snapshot "$OUT/long/snap" --from $LONG_LO --to $LONG_HI --groups "$OUT/long/groups.json" \
      --variants 0.04:600 --cost-scenario "$OUT/long/cost_scenario.json" --journal "$OUT/$1" --summary "$OUT/$1.json" \
      --mem-log "$OUT/$1.jsonl" > "$OUT/$1.log" 2>&1
  }
  rm -rf "${OUT:?}/long-a1" "${OUT:?}/long-a2" "${OUT:?}"/long-a[12].json "${OUT:?}"/long-a[12].jsonl "${OUT:?}/long-a-run.json" "${OUT:?}/long-a.md"
  log "long-a: P1 and P2 on $SEGS ($expected segment directories), side by side"
  arun long-a1 & a=$!
  arun long-a2 & b=$!
  rc1=0 rc2=0
  wait $a || rc1=$?
  wait $b || rc2=$?
  for n in long-a1 long-a2; do tail -2 "$OUT/$n.log" | tee -a "$OUT/job.log"; done
  "$PY" - "$OUT" "$REPO/lab/live/plateau.py" "$rc1" "$rc2" "$expected" <<'EOF'
import filecmp, json, os, subprocess, sys
out, plateau, rc1, rc2, expected = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])


def sha(name, rc):
    if rc != 0:
        return None
    r = subprocess.run([sys.executable, plateau, "--journal-sha", os.path.join(out, name)], capture_output=True, text=True)
    return r.stdout.strip() or None


wallets = sorted(f for f in os.listdir(os.path.join(out, "long-a1")) if f.startswith("wallet") and f.endswith(".json")) if rc1 == 0 else []
p1 = json.load(open(os.path.join(out, "long-a1.json"))) if rc1 == 0 else {}
seg = p1.get("segments", {})
run = {"p1_journal_sha": sha("long-a1", rc1), "p2_journal_sha": sha("long-a2", rc2),
       "wallets_equal": bool(wallets) and rc2 == 0
       and all(filecmp.cmp(os.path.join(out, "long-a1", f), os.path.join(out, "long-a2", f), shallow=False) for f in wallets),
       "exit_codes": {"p1": rc1, "p2": rc2}, "segments_read": seg.get("read"), "segments_expected": expected,
       "incomplete_skipped": seg.get("incomplete_skipped", []), "p1_secs": p1.get("secs")}
json.dump(run, open(os.path.join(out, "long-a-run.json"), "w"), indent=1)
print(f"long-a run: journals {'equal' if run['p1_journal_sha'] and run['p1_journal_sha'] == run['p2_journal_sha'] else 'DIFFER'}, "
      f"wallets {'equal' if run['wallets_equal'] else 'DIFFER'}, segments {run['segments_read']} of {expected}")
EOF
  "$PY" "$REPO/lab/live/plateau.py" --p1 "$OUT/long-a1.jsonl" --p2 "$OUT/long-a2.jsonl" --run "$OUT/long-a-run.json" --spec-sha "$SPEC_SHA" \
    --out "$OUT/long-a.md" --daily | tee -a "$OUT/job.log"
  sed -n '/^Days after the warm-up above/,/^$/p' "$OUT/long-a.md" | tee -a "$OUT/job.log"
  (cd "$OUT" && sha256sum long-a.md long-a-run.json long-a1.jsonl long-a2.jsonl) | tee -a "$OUT/job.log"
  if tail -n 40 "$OUT/job.log" | grep -q "^lab-live plateau .*GREEN$"; then log "long-a: GREEN (v0.6, last 24 h)"; else log "long-a: RED (long-a.md says why)"; fi
  exit 0
fi

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
  "$PY" - "$OUT" "$REPO/lab/live/plateau.py" "$AB_REF_DIR" <<'EOF' | tee -a "$OUT/job.log" || { log "ab: RED"; exit 1; }
import filecmp, json, os, subprocess, sys
out, plateau, ref_dir = sys.argv[1], sys.argv[2], sys.argv[3]
run = lambda *a: subprocess.run([sys.executable, plateau, "--journal-sha", *a], capture_output=True, text=True).stdout.strip()
sha = lambda n: run(os.path.join(out, n))
a, b = sha("ab0"), sha("ab1")
# C4-5 across commits: P1's tables (every table but state) against C4-4's P1; None: not asked (AB_REF_DIR empty)
t_new = run(os.path.join(out, "ab1"), "--tables-only")
t_ref = run(ref_dir, "--tables-only") if ref_dir and os.path.isfile(os.path.join(ref_dir, "journal.sqlite")) else ""
cross = None if not ref_dir else bool(t_new) and t_new == t_ref
wallets = sorted(f for f in os.listdir(os.path.join(out, "ab0")) if f.startswith("wallet") and f.endswith(".json"))
w_equal = bool(wallets) and all(filecmp.cmp(os.path.join(out, "ab0", f), os.path.join(out, "ab1", f), shallow=False) for f in wallets)
s0, s1 = (json.load(open(os.path.join(out, f"{n}.json"))) for n in ("ab0", "ab1"))
end = lambda n: [json.loads(l) for l in open(os.path.join(out, f"{n}.jsonl"))][-1]
e0, e1 = end("ab0"), end("ab1")
p0, p1 = s0["farm_hist_pruned"], s1["farm_hist_pruned"]
st = lambda e, k, f: e["structures"][k][f]
ft = e1.get("tables", {}).get("farmer_hist", {})
ok = bool(a) and a == b and cross is not False and w_equal and not p0["on"] and p1["on"] and p1["sweeps"] > 0
vs = "not asked" if cross is None else f"{'equal' if cross else 'DIFFER'} ({t_new[:12]} vs {t_ref[:12] or 'no journal at ' + ref_dir})"
print(f"lab-live farm prune A/B on job 91's segments: journals {'equal' if a and a == b else 'DIFFER'} ({a[:12]}); P1's tables vs "
      f"C4-4's P1: {vs}; wallets "
      f"{'equal' if w_equal else 'DIFFER'} ({len(wallets)}); prune {p1['sweeps']} sweeps, {p1['pairs_dropped']:,} pairs dropped, "
      f"{p1['pairs_left']:,} left (without: {p0['pairs_left']:,}); at the end farmer_hist_trades {st(e0, 'farmer_hist_trades', 'entries'):,} -> "
      f"{st(e1, 'farmer_hist_trades', 'entries'):,}, heap {e0['heap_bytes'] / 1e9:.2f} -> {e1['heap_bytes'] / 1e9:.2f} GB, peak RSS "
      f"{s0['peak_rss_bytes'] / 1e9:.2f} -> {s1['peak_rss_bytes'] / 1e9:.2f} GB; farmer_hist at the end {st(e1, 'farmer_hist', 'bytes_est') / 1e6:.1f} MB "
      f"(table {ft.get('buckets', 0):,} buckets, {ft.get('tombstones', 0):,} tombstones); seconds {s0['secs']:.0f} / {s1['secs']:.0f}; "
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
