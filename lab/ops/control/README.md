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

- **Never list the VPS itself.** `whois` of the VPS's own address answers with the VPS node, so an entry for it would let anything running on the VPS act through the UI. Only the owner's phone and laptop belong in the file.
- **Every later action validates its own `params` in the executor** (exact keys, types and ranges per action), not only in the web app.

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

## The NAS job dispatcher (B3b)

The action `nas_job` takes the strategy chooser's parameters:
- **Checked three times:** by the web app (prepare), the executor, and the dispatcher, all against `lab/ops/uijobs/schema.py`. The NAS checks a fourth time.
- **Queued:** the executor writes `nas-queue/<id>.json` and records the trial.
- **Dispatched:** `lab-nas-dispatch` (`nas_dispatch.py`, every 2 minutes) hands one job at a time to the NAS through the forced command:
  1. `sums` for the pinned template sha;
  2. `put`;
  3. `status <id>` until done;
  4. `get <id>` → `$LAB/nas-results/ui/<id>/result.json`.

**Trials.** Every job counts as a trial (spec §5.2): a row in the experiment register's format in `$LAB/control/ui-trials.md` (plus `ui-trials.jsonl`). The coordinator carries the count into `lab/reports/experiment-register.md`.

**Install (the coordinator, after #445 and the NAS side are in place; no sudo).**

1. A key only for the dispatcher. Not the coordinator's key. Its public half goes on the NAS behind `restrict,command=…` (lab/ops/uijobs/README.md):

   ```bash
   ssh-keygen -t ed25519 -N '' -C lab-ui-jobs -f ~/.ssh/id_ed25519_nas_ui
   ```

2. The NAS's host key in a known_hosts of its own. Check the fingerprint against the NAS console before trusting it:

   ```bash
   ssh-keyscan -t ed25519 100.79.221.55 > ~/.config/solana-lab/nas-ui.known_hosts
   ```

3. The settings. These are no secrets, but they stay out of the repo:

   ```bash
   cat > ~/.config/solana-lab/nas-ui.env <<'EOF'
   NAS_UI_HOST=100.79.221.55
   NAS_UI_USER=ClaudeCode
   NAS_UI_KEY=/home/chupa/.ssh/id_ed25519_nas_ui
   NAS_UI_KNOWN_HOSTS=/home/chupa/.config/solana-lab/nas-ui.known_hosts
   EOF
   chmod 600 ~/.config/solana-lab/nas-ui.env
   ```

4. The units:

   ```bash
   cp lab/ops/systemd/lab-nas-dispatch.service lab/ops/systemd/lab-nas-dispatch.timer ~/.config/systemd/user/
   systemctl --user daemon-reload && systemctl --user enable --now lab-nas-dispatch.timer
   ```

`ssh` always runs with:
- `BatchMode`, `StrictHostKeyChecking=yes` against that file only (`GlobalKnownHostsFile=/dev/null`), `IdentitiesOnly`, `IdentityAgent=none`;
- `ConnectTimeout=10`, no forwarding;
- an argv, with no shell on this side.

The log goes to `$LAB/logs/nas-dispatch.log`.

**Under variant B** the key is readable by the web server's user (the same user). That is acceptable because the forced command can only queue a valid templated job (spec §2.3). Under variant A only the owner's user reads it.
