#!/usr/bin/env bash
# Live segment retention (lab-live-retention.timer, hourly). Deletes complete recorder segments
# (with _segment.json) older than KEEP_DAYS (3: owner decision 2026-10-10). Journals, reports,
# budget.json, watchlists, token_meta, backups and logs are never touched; the newest segment and
# segments without _segment.json (still being written) neither.
# Disk guard: with lab/locks/io-stall the round is skipped (deleting is I/O); with
# lab/locks/disk-low it keeps LOW_KEEP_DAYS (1) instead. Reconcile a segment against OF1 within
# the retention, or keep it (KEEP_DAYS) longer.
# Before a segment goes, paper.keeper keeps what the retention rule promises (AGENTS.md): the trades
# of tokens with a signal, their arrival times and 1-minute candles, under KEPT/<seq>/. A segment is
# deleted only once KEPT/<seq>/DONE.json exists: a failing keeper leaves it in place (logged).
set -euo pipefail
LAB=${LAB:-$HOME/Solana-project/data-old-faithful-one/lab}
ROOT=${ROOT:-$LAB/recorder}
KEEP_DAYS=${KEEP_DAYS:-3}
LOW_KEEP_DAYS=${LOW_KEEP_DAYS:-1}
DRY_RUN=${DRY_RUN:-0}
LOG=${LOG:-$LAB/logs/live-retention.log}
JOURNAL=${JOURNAL:-$LAB/journal/paper-k3/journal}
KEPT=${KEPT:-$LAB/journal/paper-k3/kept}
CODE=${CODE:-$(cd "$(dirname "$0")/.." && pwd)}
LAB_PY=${LAB_PY:-$HOME/.local/share/solana-quant/toolchains/lab-py/bin/python}
KEEPER_CMD=${KEEPER_CMD:-$LAB_PY -m paper.keeper}
mkdir -p "$(dirname "$LOG")"
log() { echo "$(date -Is) $*" >> "$LOG"; }
for v in KEEP_DAYS LOW_KEEP_DAYS; do
  if ! [[ ${!v} =~ ^[0-9]+$ ]]; then
    log "$v=${!v} is not a whole number of days: nothing deleted"
    echo "$v must be a whole number of days, got '${!v}'" >&2
    exit 2
  fi
done

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
old=()
while IFS= read -r meta; do
  d=$(dirname "$meta")
  name=$(basename "$d")
  [[ $name =~ ^[0-9]{9}$ ]] || continue          # only segment directories
  [[ $name == "$newest" ]] && continue
  [[ $(dirname "$d") == "$seg" ]] || continue     # never outside <root>/segments
  old+=("$d")
done < <(find "$seg" -mindepth 2 -maxdepth 2 -name _segment.json -mmin +$((keep * 1440)) | sort)
(( ${#old[@]} )) || exit 0
if (( ! DRY_RUN )); then
  todo=()
  for d in "${old[@]}"; do
    [[ -e $KEPT/$(basename "$d")/DONE.json ]] || todo+=("$d")
  done
  if (( ${#todo[@]} )); then
    rc=0
    # shellcheck disable=SC2086  # KEEPER_CMD is a command line
    (cd "$CODE" && $KEEPER_CMD --journal "$JOURNAL" --out "$KEPT" "${todo[@]}") >> "$LOG" 2>&1 || rc=$?
    (( rc == 0 )) || log "keeper exit $rc: segments without KEPT/<seq>/DONE.json stay"
  fi
fi
n=0
kb=0
unkept=0
for d in "${old[@]}"; do
  size=$(du -sk "$d" 2>/dev/null | cut -f1) || true  # a vanished file must not end the round
  size=${size:-0}
  if (( DRY_RUN )); then
    echo "would delete $d (${size} KB)"
  elif [[ -e $KEPT/$(basename "$d")/DONE.json ]]; then
    rm -rf -- "$d"
  else
    unkept=$((unkept + 1))
    continue
  fi
  n=$((n + 1))
  kb=$((kb + size))
done
if (( n )); then
  log "$( (( DRY_RUN )) && echo "dry run: would delete" || echo deleted) $n segments older than $keep day(s), $((kb / 1024)) MB"
fi
if (( unkept )); then
  log "$unkept old segments not deleted: the keeper has not kept them (no KEPT/<seq>/DONE.json)"
fi
