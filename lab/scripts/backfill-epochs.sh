#!/usr/bin/env bash
# Backfill whole epochs, newest first, with backfill.sh. An epoch that fails is logged and
# skipped so one bad epoch does not stop the rest; rerun the script to retry it (complete
# chunks are skipped).
#
#   lab/scripts/backfill-epochs.sh <newest_epoch> <oldest_epoch> [threads]
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
newest=${1:?newest epoch}
oldest=${2:?oldest epoch}
threads=${3:-6}
trap 'echo "interrupted"; exit 130' INT TERM

for ((epoch = newest; epoch >= oldest; epoch--)); do
  echo "$(date -Is) epoch $epoch"
  "$here/backfill.sh" $((epoch * 432000)) $(((epoch + 1) * 432000)) 216000 "$threads"
  rc=$?
  ((rc == 130)) && exit 130
  ((rc != 0)) && echo "$(date -Is) epoch $epoch FAILED (rc=$rc); continuing with the next epoch"
done
echo "$(date -Is) done"
