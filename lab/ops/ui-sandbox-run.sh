#!/bin/bash
# Run one command as lab-ui inside the same sandbox as lab-ui-web (variant A): a transient system unit with the
# user, the empty home, the read-only binds, /run/user hidden and the other isolation lines of
# lab/ops/systemd/lab-ui-web.system.service, read from that file so the check and the service cannot drift apart.
# Needs sudo (a system unit). Used by lab/ops/ui-variant-a.md for the WAL check and the acceptance checks.
#
#   bash lab/ops/ui-sandbox-run.sh <command> [args...]
set -euo pipefail
UNIT=$(dirname "$0")/systemd/lab-ui-web.system.service
[[ $# -ge 1 ]] || { echo "usage: $0 <command> [args...]"; exit 2; }
props=()
while IFS= read -r line; do
  props+=(-p "$line")
done < <(grep -E '^(User|Group|ProtectHome|ProtectSystem|BindReadOnlyPaths|BindPaths|InaccessiblePaths|PrivateTmp|PrivateDevices|NoNewPrivileges|CapabilityBoundingSet|RestrictSUIDSGID|RestrictNamespaces|ProtectProc|ProcSubset|ProtectKernelTunables|ProtectKernelModules|ProtectControlGroups|SystemCallFilter|SystemCallArchitectures|RestrictAddressFamilies)=' "$UNIT")
exec sudo systemd-run --quiet --pipe --wait --collect "${props[@]}" -- "$@"
