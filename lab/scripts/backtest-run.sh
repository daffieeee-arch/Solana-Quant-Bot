#!/usr/bin/env bash
# Run a tournament from a frozen copy of the committed engine.
#
#   scripts/backtest-run.sh <run_id> [backtest.tournament args...]
#
# Copies lab/backtest at HEAD (git archive, so uncommitted edits never leak in) to
# $LAB_BACKTESTS/<run_id>/engine/ and runs it from there: workers import that copy, so editing
# the worktree during a run changes nothing. config.json records the commit.
# Bound it like any heavy job, e.g. as a transient unit holding the shared lock:
#   systemd-run --user --unit lab-backtest-<id> -p MemoryMax=3G -p CPUQuota=200% \
#     flock -n "$LAB_DATA/lab/locks/heavy.lock" nice -n 10 scripts/backtest-run.sh <id> ...
# Re-running with the same run_id resumes: batches already in parts/ are skipped (same commit only).
set -euo pipefail

run_id=${1:?run_id}
disk_low=/home/chupa/Solana-project/data-old-faithful-one/lab/locks/disk-low
if [[ -e $disk_low ]]; then
  echo "$disk_low exists (disk guard): not starting" >&2
  exit 3
fi
shift
lab=$(cd "$(dirname "$0")/.." && pwd)
out=${LAB_BACKTESTS:-/home/chupa/Solana-project/data-old-faithful-one/lab/backtests}/$run_id
py=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}

if [[ -n $(git -C "$lab" status --porcelain -- backtest) ]]; then
  echo "lab/backtest has uncommitted changes; commit first (the run uses HEAD)" >&2
  exit 2
fi
commit=$(git -C "$lab" rev-parse HEAD)
if [[ -f $out/engine/COMMIT && $(cat "$out/engine/COMMIT") != "$commit" ]]; then
  echo "$out was started with engine $(cat "$out/engine/COMMIT"), HEAD is $commit; use a new run_id" >&2
  exit 2
fi
if [[ ! -d $out/engine/backtest ]]; then
  mkdir -p "$out/engine"
  git -C "$lab" archive "$commit" backtest | tar -x -C "$out/engine"
  echo "$commit" > "$out/engine/COMMIT"
fi

cd "$out/engine"
export LAB_ENGINE_COMMIT=$commit PYTHONUNBUFFERED=1
exec "$py" -m backtest.tournament --run-id "$run_id" "$@" >> "$out/run.log" 2>&1
