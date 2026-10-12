#!/usr/bin/env bash
# Live segment retention (lab-live-retention.timer, hourly). Deletes complete recorder segments
# (with _segment.json) older than KEEP_DAYS (3: owner decision 2026-10-10). Journals, reports,
# budget.json, watchlists, token_meta, backups and logs are never touched; the newest segment and
# segments without _segment.json (still being written) neither.
# Disk guard: with lab/locks/io-stall the round is skipped (deleting is I/O); with
# lab/locks/disk-low it keeps LOW_KEEP_DAYS (1) instead. Reconcile a segment against OF1 within
# the retention, or keep it (KEEP_DAYS) longer.
set -euo pipefail
LAB=${LAB:-$HOME/Solana-project/data-old-faithful-one/lab}
ROOT=${ROOT:-$LAB/recorder}
KEEP_DAYS=${KEEP_DAYS:-3}
LOW_KEEP_DAYS=${LOW_KEEP_DAYS:-1}
DRY_RUN=${DRY_RUN:-0}
LOG=${LOG:-$LAB/logs/live-retention.log}
mkdir -p "$(dirname "$LOG")"
log() { echo "$(date -Is) $*" >> "$LOG"; }

seg=$ROOT/segments
[[ -d $seg ]] || exit 0
if [[ -e $LAB/locks/io-stall ]]; then
  log "io-stall flag: skipped"
  exit 0
fi
keep=$KEEP_DAYS
if [[ -e $LAB/locks/disk-low ]]; then
  keep=$LOW_KEEP_DAYS
  log "disk-low flag: keeping $keep day(s)"
fi
newest=$(find "$seg" -mindepth 1 -maxdepth 1 -type d -regextype posix-extended -regex '.*/[0-9]{9}' -printf '%f\n' | sort | tail -1)
n=0
kb=0
while IFS= read -r meta; do
  d=$(dirname "$meta")
  name=$(basename "$d")
  [[ $name =~ ^[0-9]{9}$ ]] || continue          # only segment directories
  [[ $name == "$newest" ]] && continue
  [[ $(dirname "$d") == "$seg" ]] || continue     # never outside <root>/segments
  size=$(du -sk "$d" | cut -f1)
  if (( DRY_RUN )); then
    echo "would delete $d (${size} KB)"
  else
    rm -rf -- "$d"
  fi
  n=$((n + 1))
  kb=$((kb + size))
done < <(find "$seg" -mindepth 2 -maxdepth 2 -name _segment.json -mmin +$((keep * 1440)) | sort)
if (( n )); then
  log "$( (( DRY_RUN )) && echo "dry run: would delete" || echo deleted) $n segments older than $keep day(s), $((kb / 1024)) MB"
fi
