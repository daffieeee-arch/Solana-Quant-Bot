#!/usr/bin/env bash
# Backfill a slot range in chunks with lab-extractor, resource-bounded and resumable.
#
#   lab/scripts/backfill.sh <first_slot> <end_slot_exclusive> [chunk_slots] [threads]
#
# Each chunk goes to $LAB_DATA_ROOT/events/v1/chunks/<start>-<end>/. A chunk with a
# `_manifest.json` whose status is "complete" is skipped; any other chunk directory is removed
# and streamed again. Chunks run one after another inside a systemd user scope with CPU and
# memory limits so the other services on the VPS stay responsive.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$here/env.sh"

first=${1:?first slot}
end=${2:?end slot (exclusive)}
chunk=${3:-108000}
threads=${4:-4}
cpu_quota=${LAB_CPU_QUOTA:-450%}
mem_max=${LAB_MEM_MAX:-8G}

bin="$here/../extractor/target/release/lab-extractor"
[[ -x $bin ]] || { echo "build first: cargo build --release in lab/extractor" >&2; exit 1; }
out_root="$LAB_DATA_ROOT/events/v1/chunks"
log_dir="$LAB_DATA_ROOT/logs"
mkdir -p "$out_root" "$log_dir"

for ((s = first; s < end; s += chunk)); do
  e=$((s + chunk < end ? s + chunk : end))
  dir="$out_root/$s-$e"
  if [[ -f $dir/_manifest.json ]] && grep -q '"status": "complete"' "$dir/_manifest.json"; then
    echo "skip $s-$e (complete)"
    continue
  fi
  rm -rf "$dir"
  echo "$(date -Is) start $s-$e threads=$threads"
  attempt=0
  until systemd-run --user --scope --quiet --unit="lab-extract-$s" \
      -p CPUQuota="$cpu_quota" -p MemoryMax="$mem_max" -- \
      nice -n 10 "$bin" --out "$dir" --slots "$s:$e" --threads "$threads" \
      >"$log_dir/extract-$s-$e.log" 2>&1; do
    attempt=$((attempt + 1))
    status=$(grep -o '"status": "[a-z_]*"' "$dir/_manifest.json" 2>/dev/null || echo none)
    echo "$(date -Is) chunk $s-$e failed (attempt $attempt, $status)"
    if ((attempt >= 3)); then
      echo "giving up on $s-$e; see $log_dir/extract-$s-$e.log" >&2
      exit 1
    fi
    rm -rf "$dir"
    sleep $((60 * attempt))
  done
  grep -o '"status": "[a-z_]*"' "$dir/_manifest.json"
  echo "$(date -Is) done $s-$e"
done
