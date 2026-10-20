# lab-control: the interactive UI's executor (B0)

Interactive-UI spec v0.3 §2 (`data-old-faithful-one/lab/research/ui-interactive-spec-2026-10-20.md`).

**The flow.**
1. The web app (`lab/ui/actions.py`) checks who is calling, the session, CSRF, the confirmation, the rate and the Tailscale exposure. Only then does it file **one request file** in `$LAB/control/requests/`.
2. **lab-control** (`executor.py`, a `--user` unit of the owner's user, started by `lab-control.path`) validates the file again against its own allowlist, executes it, and writes `results/<id>.json` and one audit line.

B0 knows only `ping`, which changes nothing. New actions come with their own reviewed PR:
- the NAS job (B3b);
- after variant A only: pause (B1) and start/stop (B2).

## Paths

| Path | Who writes | What |
|---|---|---|
| `$LAB/control/requests/<ULID>.json` | web app | a request; moved to `requests/done/` once handled |
| `$LAB/control/results/<ULID>.json` | executor | the outcome |
| `$LAB/control/audit/<YYYY-MM>.jsonl` | executor | what was executed or refused (who, what, when, outcome) |
| `$LAB/control/audit/web-<YYYY-MM>.jsonl` | web app | pairing, prepare, commit and every refusal (auth, CSRF, rate, exposure) |
| `$LAB/control/pairing/*.json` | `lab/ui/pair.py` | the SHA-256 of a pairing code (5 minutes); the web app consumes and deletes it |
| `ui-actors.json`, `ui-sessions.json` in `LAB_UI_STATE` | the owner / web app | allowed devices / session hashes (0600). Variant B: `~/.config/solana-lab`; variant A: `/var/lib/lab-ui` |

No secret is in the repo or a log:
- the pairing code is only printed;
- the audit holds node ids and names, never a session id, a token or an e-mail address.

## Install (the coordinator, after merge; no sudo)

From the deploy worktree pinned at the merge commit:

```bash
cp lab/ops/systemd/lab-control.path lab/ops/systemd/lab-control.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now lab-control.path
```

Then the owner names the devices that may act (stable node ids from `tailscale status --json`, field `StableID`):

```bash
cat > ~/.config/solana-lab/ui-actors.json <<'EOF'
{"nodes": [{"id": "<StableID of the phone>", "name": "iphone"}, {"id": "<StableID of the laptop>", "name": "laptop"}]}
EOF
chmod 600 ~/.config/solana-lab/ui-actors.json
```

## Pairing a device (the owner)

In a VPS shell:

```bash
python3 -I ~/Solana-project/worktrees/lab-ui-deploy/lab/ui/pair.py
```

It prints a 20-character code, valid 5 minutes and usable once. Type it on the phone on the Bediening page (B0b). Five wrong codes lock pairing for that device for 15 minutes.

## Checks at the build

- **Exposure (spec §2.2.7):**
  - `tailscale funnel status` shows no Funnel.
  - `tailscale serve status` has no entry for port 8501. The other project's entry for port 3000 stays as it is.
  - `tailscale status` shows the VPS is not shared out of the tailnet.
  - The web app re-checks Funnel and Serve on every action.
- **Variant B (spec §2.3):** the web app and the executor share the owner's user, so this separation is logical only. B0 and B3 only; no bot action is enabled before variant A (#438).
