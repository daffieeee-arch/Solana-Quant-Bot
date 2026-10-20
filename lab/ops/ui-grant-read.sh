#!/bin/bash
# Grant the system user lab-ui exactly the read access the web app needs (variant A, lab/ops/ui-variant-a.md), with
# ACLs, as the owner's user (no sudo; needs the 'acl' package). Read only, on the leaf trees the unit binds
# (BindReadOnlyPaths), never a write and never a traverse ACL on the way down: systemd mounts those trees as root
# into the unit's empty home (ProtectHome=tmpfs), so lab-ui needs no way through /home/chupa, and an `x` there would
# open every world-readable file below it to lab-ui outside the sandbox (LAB review on #438). Default ACLs make
# new files (segments, journal days, a new deploy checkout) inherit the read. Idempotent: run it again after a new
# worktree such as paper-run.
#
#   bash lab/ops/ui-grant-read.sh            # show what it would do
#   bash lab/ops/ui-grant-read.sh --apply    # do it
set -euo pipefail
U=lab-ui
H=/home/chupa
P=$H/Solana-project
L=$P/data-old-faithful-one/lab
APPLY=${1:-}
id "$U" >/dev/null 2>&1 || { echo "no user $U: the owner creates it first (ui-variant-a.md step 1)"; exit 1; }
command -v setfacl >/dev/null || { echo "setfacl missing: the owner installs the 'acl' package first (ui-variant-a.md step 1)"; exit 1; }

run() { if [[ $APPLY == --apply ]]; then "$@"; else echo "would: $*"; fi; }

# Read (rX) on the trees the app reads, with a default ACL so new files inherit it.
for d in "$P/worktrees/lab-ui-deploy" "$P/worktrees/paper-run/lab/paper" \
         "$H/.local/share/solana-quant/toolchains/columnar-query-313-duckdb155" "$H/.local/share/uv/python/cpython-3.13.15-linux-x86_64-gnu" \
         "$L/analytics" "$L/store" "$L/journal" "$L/recorder" "$L/ui-cache" "$L/backtests" "$L/wallets/cases-v1" "$L/token_meta"; do
  if [[ -d $d ]]; then
    run setfacl -R -m "u:$U:rX" "$d"
    run find "$d" -type d -exec setfacl -m "d:u:$U:rX" {} +
  else
    echo "skip (missing): $d"
  fi
done

# Never: a traverse ACL on the way down, or anything on the owner's secrets. A check, not a change.
for d in "$H" "$H/.config" "$H/.config/solana-lab" "$H/.local" "$H/.local/share" "$P" "$P/data-old-faithful-one" "$L"; do
  if getfacl -p "$d" 2>/dev/null | grep -q "^user:$U:"; then
    echo "REFUSED: $d has an ACL entry for $U (remove it: setfacl -x u:$U $d)"; exit 1
  fi
done
echo "done${APPLY:+ ($APPLY)}"
