#!/usr/bin/env bash
# Backfill a slot range in chunks with lab-extractor, resource-bounded and resumable.
#
#   lab/scripts/backfill.sh <first_slot> <end_slot_exclusive> [chunk_slots] [threads]
#
# Each chunk goes to $LAB_DATA_ROOT/events/v1/chunks/<start>-<end>/. A chunk with a
# `_manifest.json` whose status is "complete" is skipped; any other chunk directory is removed
# and streamed again. Chunks run one after another inside a systemd user scope with CPU and
# memory limits so the other services on the VPS stay responsive.
#
# Old Faithful's storage (Backblaze B2 behind Cloudflare) answers *new* requests with HTTP 429
# whenever the archive owner's account is over its request limit: global, intermittent, not
# tied to our IP; open streams keep flowing. The extractor therefore backs off per firehose
# thread (1 to 15 minutes) instead of stopping, and this script sends no probe requests.
# Exit code 76 (one slot keeps failing) and 3 (incomplete slot coverage) count as failures;
# three failures stop the backfill. Exit code 75 is retried after LAB_429_PAUSE seconds.
#
# Each epoch's slot-ranges index (5 MB) is downloaded once into $LAB_DATA_ROOT/of1-index and
# served to Jetstreamer from 127.0.0.1. Otherwise every extractor process, and every thread
# restart that invalidates the cache, fetches it again from Old Faithful and draws 429s.
set -euo pipefail
trap 'echo "interrupted"; exit 130' INT TERM  # the EXIT trap below stops the index server

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$here/env.sh"

first=${1:?first slot}
end=${2:?end slot (exclusive)}
chunk=${3:-216000}
threads=${4:-4}
cpu_quota=${LAB_CPU_QUOTA:-700%}
mem_max=${LAB_MEM_MAX:-11G}
pause=${LAB_429_PAUSE:-900}
max_pauses=${LAB_MAX_PAUSES:-16}

build="$here/../extractor/target/release/lab-extractor"
[[ -x $build ]] || { echo "build first: cargo build --release in lab/extractor" >&2; exit 1; }
out_root="$LAB_DATA_ROOT/events/v1/chunks"
log_dir="$LAB_DATA_ROOT/logs"
mkdir -p "$out_root" "$log_dir" "$LAB_DATA_ROOT/bin"
exec 9>"$out_root/.backfill.lock"
flock -n 9 || { echo "another backfill is running (lock $out_root/.backfill.lock)" >&2; exit 1; }
# Run a frozen copy so a rebuild during the backfill does not change the binary mid-run.
bin="$LAB_DATA_ROOT/bin/lab-extractor-$(sha256sum "$build" | cut -c1-12)"
[[ -x $bin ]] || cp "$build" "$bin"
echo "$(date -Is) backfill $first..$end chunk=$chunk threads=$threads bin=$bin"

index_root="$LAB_DATA_ROOT/of1-index"
index_port=${LAB_INDEX_PORT:-8771}
mkdir -p "$index_root"
"$LAB_PY" -m http.server --bind 127.0.0.1 "$index_port" --directory "$index_root" >/dev/null 2>&1 &
index_server=$!
trap 'kill $index_server 2>/dev/null' EXIT
export JETSTREAMER_COMPACT_INDEX_BASE_URL="http://127.0.0.1:$index_port/"
# Keep the request count to Old Faithful low: start firehose threads one at a time and do not
# reconnect threads just because they are slower than the fastest one.
export JETSTREAMER_SPAWN_PENDING=${JETSTREAMER_SPAWN_PENDING:-1}
export JETSTREAMER_RECYCLE_PCT=${JETSTREAMER_RECYCLE_PCT:-0}

ensure_index() {
  local epoch=$1 f="$index_root/$1/epoch-$1-slot-ranges.raw" code
  [[ -f $f && $(stat -c %s "$f") == $((432000 * 12)) ]] && return 0
  mkdir -p "$index_root/$epoch"
  while true; do
    code=$(curl -s -o "$f.tmp" -w '%{http_code}' --max-time 120 \
      "https://files.old-faithful.net/$epoch/epoch-$epoch-slot-ranges.raw" || echo 000)
    if [[ $code == 200 && $(stat -c %s "$f.tmp") == $((432000 * 12)) ]]; then
      mv "$f.tmp" "$f"
      echo "$(date -Is) cached slot-ranges index for epoch $epoch"
      return 0
    fi
    echo "$(date -Is) slot-ranges index for epoch $epoch answered $code; pausing ${pause}s"
    sleep "$pause"
  done
}

for ((s = first; s < end; s += chunk)); do
  e=$((s + chunk < end ? s + chunk : end))
  dir="$out_root/$s-$e"
  if [[ -f $dir/_manifest.json ]] && grep -q '"status": "complete"' "$dir/_manifest.json"; then
    echo "skip $s-$e (complete)"
    continue
  fi
  failures=0
  pauses=0
  while true; do
    rm -rf "$dir"
    ensure_index $((s / 432000))
    [[ $(((e - 1) / 432000)) != $((s / 432000)) ]] && ensure_index $(((e - 1) / 432000))
    echo "$(date -Is) start $s-$e threads=$threads" | tee -a "$log_dir/extract-$s-$e.log"
    rc=0
    systemd-run --user --scope --quiet --unit="lab-extract-$s-$(date +%s)" \
      -p CPUQuota="$cpu_quota" -p MemoryMax="$mem_max" -- \
      nice -n 10 "$bin" --out "$dir" --slots "$s:$e" --threads "$threads" \
      >>"$log_dir/extract-$s-$e.log" 2>&1 || rc=$?
    ((rc == 130 || rc == 143)) && { echo "extractor interrupted"; exit 130; }
    status=$(grep -o '"status": "[a-z_]*"' "$dir/_manifest.json" 2>/dev/null || echo '"status": "none"')
    echo "$(date -Is) chunk $s-$e exit=$rc $status"
    [[ $rc == 0 && $status == '"status": "complete"' ]] && break
    if [[ $rc == 75 ]]; then
      pauses=$((pauses + 1))
      if ((pauses > max_pauses)); then
        echo "still rate limited after $max_pauses pauses on $s-$e; stopping" >&2
        exit 1
      fi
      echo "$(date -Is) rate limited ($pauses/$max_pauses); pausing ${pause}s"
      sleep "$pause"
      continue
    fi
    failures=$((failures + 1))
    if ((failures >= 3)); then
      echo "giving up on $s-$e after $failures failures; see $log_dir/extract-$s-$e.log" >&2
      exit 1
    fi
    sleep $((120 * failures))
  done
done
