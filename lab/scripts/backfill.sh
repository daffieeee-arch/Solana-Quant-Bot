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
# Old Faithful rate-limits per client (HTTP 429). Before every chunk one tiny range request
# checks that the archive answers; while it does not, the script waits LAB_429_PAUSE seconds.
# The extractor exits with code 75 when 429s pile up during a chunk, and the chunk is retried
# after the same pause without counting as a failure.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$here/env.sh"

first=${1:?first slot}
end=${2:?end slot (exclusive)}
chunk=${3:-108000}
threads=${4:-4}
cpu_quota=${LAB_CPU_QUOTA:-700%}
mem_max=${LAB_MEM_MAX:-11G}
pause=${LAB_429_PAUSE:-900}

bin="$here/../extractor/target/release/lab-extractor"
[[ -x $bin ]] || { echo "build first: cargo build --release in lab/extractor" >&2; exit 1; }
out_root="$LAB_DATA_ROOT/events/v1/chunks"
log_dir="$LAB_DATA_ROOT/logs"
mkdir -p "$out_root" "$log_dir"

wait_for_archive() {
  local epoch=$(($1 / 432000)) code
  while true; do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -r 0-15 \
      "https://files.old-faithful.net/$epoch/epoch-$epoch.car" || echo 000)
    [[ $code == 206 ]] && return 0
    echo "$(date -Is) archive answered $code for epoch $epoch; pausing ${pause}s"
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
  while true; do
    rm -rf "$dir"
    wait_for_archive "$s"
    echo "$(date -Is) start $s-$e threads=$threads"
    rc=0
    systemd-run --user --scope --quiet --unit="lab-extract-$s-$(date +%s)" \
      -p CPUQuota="$cpu_quota" -p MemoryMax="$mem_max" -- \
      nice -n 10 "$bin" --out "$dir" --slots "$s:$e" --threads "$threads" \
      >"$log_dir/extract-$s-$e.log" 2>&1 || rc=$?
    status=$(grep -o '"status": "[a-z_]*"' "$dir/_manifest.json" 2>/dev/null || echo '"status": "none"')
    echo "$(date -Is) chunk $s-$e exit=$rc $status"
    [[ $rc == 0 && $status == '"status": "complete"' ]] && break
    if [[ $rc == 75 ]]; then
      echo "$(date -Is) rate limited; pausing ${pause}s"
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
