#!/bin/bash
# Grant the system user lab-ui exactly the read access the web app needs (variant A, lab/ops/ui-variant-a.md), with
# ACLs, as the owner's user (no sudo; needs the 'acl' package). Read and traverse only, never write; never
# ~/.config/solana-lab (the Triton token) or anything else. Default ACLs make new files (segments, journal days,
# a new deploy checkout) inherit the read. Idempotent: run it again after a new worktree such as paper-run.
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

# Traverse only (x): the directories on the way down. Their contents stay unreadable to lab-ui.
for d in "$H" "$P" "$P/data-old-faithful-one" "$L" "$L/wallets" "$P/worktrees" "$P/worktrees/paper-run" "$P/worktrees/paper-run/lab" \
         "$H/.local" "$H/.local/share" "$H/.local/share/uv" "$H/.local/share/uv/python" \
         "$H/.local/share/solana-quant" "$H/.local/share/solana-quant/toolchains"; do
  [[ -d $d ]] && run setfacl -m "u:$U:x" "$d" || echo "skip (missing): $d"
done

# Read (rX) on the trees the app reads, with a default ACL so new files inherit it.
for d in "$P/worktrees/lab-ui-deploy" "$P/worktrees/paper-run/lab/paper" \
         "$H/.local/share/solana-quant/toolchains/columnar-query-313-duckdb155" "$H/.local/share/uv/python/cpython-3.13.15-linux-x86_64-gnu" \
         "$L/analytics" "$L/store" "$L/journal" "$L/recorder" "$L/ui-cache" "$L/backtests" "$L/wallets/cases-v1"; do
  if [[ -d $d ]]; then
    run setfacl -R -m "u:$U:rX" "$d"
    run find "$d" -type d -exec setfacl -m "d:u:$U:rX" {} +
  else
    echo "skip (missing): $d"
  fi
done

# Never: the owner's secrets. A check, not a change: fail loudly if lab-ui could read them.
if [[ $APPLY == --apply ]] && getfacl -p "$H/.config/solana-lab" 2>/dev/null | grep -q "user:$U:"; then
  echo "REFUSED: ~/.config/solana-lab has an ACL entry for $U"; exit 1
fi
echo "done${APPLY:+ ($APPLY)}"
