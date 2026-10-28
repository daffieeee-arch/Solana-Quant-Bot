# The web app as its own system user (`lab-ui`) — variant A

Interactive-UI spec v0.3 §2.3 (`data-old-faithful-one/lab/research/ui-interactive-spec-2026-10-20.md`).

## In gewone taal (voor de eigenaar)

- **Nu.** De webapp (poort 8501, alleen via Tailscale) draait als jouw eigen Linux-gebruiker `chupa`. Zit er ooit een fout in waardoor iemand op je tailnet code kan uitvoeren, dan mag die code alles wat jij mag:
  - de paper-bot en recorder stoppen;
  - journals aanpassen;
  - het Triton-token in `~/.config/solana-lab/` lezen.
- **Waarom systemd niet helpt.** De extra afscherming die systemd normaal biedt, werkt op deze VPS niet voor gebruikersservices: getest op 20 oktober, systemd negeert die opties stil.
- **De oplossing.** Een aparte systeemgebruiker `lab-ui`, zonder wachtwoord en zonder login. Onder die gebruiker draait alleen de webapp.
  - Hij mag precies de mappen **lezen** die de pagina's tonen, en verder niets.
  - Hij kan niet bij je instellingen of het token, kan geen services starten of stoppen, en kan de bot niet beïnvloeden.
- **Pas daarna** kunnen knoppen die de bot raken (pauze, start/stop) veilig worden gebouwd.
- **Wat jij doet:** eenmalig stap 1 en 3 hieronder, met `sudo`, zo'n 5 minuten werk. Daarbij zet je ook het lijstje toestellen dat knoppen mag gebruiken (je telefoon en laptop) op zijn nieuwe plek; dat kan alleen met `sudo` (stap 3c). Stap 2 doet de coördinator of LAB ui, zonder sudo.
- **Effect op de bot: geen.** Recorder en paper-bot draaien gewoon door. Alleen de webapp herstart een keer (ongeveer 5 seconden weg).

## Step 1 — the owner, with sudo (once)

```bash
sudo apt-get install -y acl                                   # setfacl/getfacl (not installed on this VPS)
sudo useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin lab-ui
id lab-ui                                                     # uid=…(lab-ui) gid=…(lab-ui) groups=…(lab-ui)
```

## Step 2 — the owner's user `chupa`, no sudo: read access through ACLs

From the deploy worktree (it carries this file once merged):

```bash
cd ~/Solana-project/worktrees/lab-ui-deploy
bash lab/ops/ui-grant-read.sh            # shows what it will do
bash lab/ops/ui-grant-read.sh --apply
```

- **What it grants:** read (`rX`, with default ACLs so new files inherit) on exactly the trees the unit binds: the deploy worktree, the server's Python, `paper-run/lab/paper` (the risk files), and `lab/{analytics,store,journal,recorder,ui-cache,backtests,wallets/cases-v1,token_meta}`.
- **What it may write (B0, the interactive UI):**
  - `lab/control/audit/web`, the web app's own audit lines, and `lab/control/pairing`, where it consumes a pairing hash: `rwX`, with default ACLs so the owner's user reads what `lab-ui` creates.
  - `lab/control/requests` itself, where it files a request: `rwX` on the directory only. The default ACL there is for the owner's user (the executor reads and moves the request), not for `lab-ui`.
  - **Not writable:** the executor's audit (`lab/control/audit/*.jsonl`) and its handled requests (`lab/control/requests/done`). A broken web server can therefore never empty or rewrite the evidence of what was executed (LAB review on #457). The unit also binds both read-only; `requests/done` is bound read-only inside the writable `requests/`, because the request files there remain `lab-ui`'s own.
  - The rest of `lab/control` is read-only too: results, notes, the NAS queue and status.
- **Run again after the switch:** it skips the files `lab-ui` owns (its own requests and audit lines), so it never fails on them.
- **What it never grants:**
  - a write;
  - a traverse (`x`) on the way down (`/home/chupa`, `.local`, …). systemd mounts the trees as root into the unit's empty home, so `lab-ui` needs no path through `/home/chupa`. An `x` there would open every world-readable file below it to `lab-ui` outside the sandbox.
  - anything under `~/.config`. The script refuses if it finds an entry for `lab-ui` on those directories.
- **Run it again** after `paper-run` exists, and after a new checkout outside the inherited trees.

## Step 3 — the owner, with sudo: check inside the sandbox, then switch

All checks run **inside a transient unit with the service's own sandbox** (`lab/ops/ui-sandbox-run.sh` reads the isolation lines from the unit file), not as `sudo -u lab-ui` on the real tree.

**3a. SQLite WAL as a reader that cannot write `-shm`.** Every journal page depends on this.

```bash
cd ~/Solana-project/worktrees/lab-ui-deploy
PY=/home/chupa/.local/share/solana-quant/toolchains/columnar-query-313-duckdb155/bin/python
bash lab/ops/ui-sandbox-run.sh $PY -I -c '
import glob, sqlite3
for p in glob.glob("/home/chupa/Solana-project/data-old-faithful-one/lab/journal/*/journal/journal.sqlite"):
    db = sqlite3.connect(f"file:{p}?mode=ro", uri=True); db.execute("PRAGMA query_only=1")
    print(p, db.execute("SELECT count(*) FROM exits").fetchone())'
```

If it fails with "attempt to write a readonly database" or "unable to open", grant `rw` on the `-shm` file only (`setfacl -m u:lab-ui:rw …/journal.sqlite-shm`), as `chupa`. Do not use `immutable=1`: it would read stale data.

**3b. Acceptance: each of these must FAIL inside the sandbox** (the last line prints `ALL BLOCKED`):

```bash
P=$(systemctl --user show -p MainPID --value lab-paper)      # 0 if the paper bot is not running: the kill check is then skipped
bash lab/ops/ui-sandbox-run.sh /bin/sh -c "
  ok=1
  test -e /run/user/1000/bus && { echo 'user bus visible'; ok=0; }
  ls /home/chupa/.config >/dev/null 2>&1 && { echo '.config readable'; ok=0; }
  touch /home/chupa/Solana-project/data-old-faithful-one/lab/journal/x 2>/dev/null && { echo 'journal writable'; ok=0; }
  [ $P -gt 0 ] && kill -0 $P 2>/dev/null && { echo 'can signal the bot'; ok=0; }
  [ \$ok = 1 ] && echo 'ALL BLOCKED'"
```

**3c. The devices that may act.** Actions exist only when the unit also sets `Environment=LAB_UI_ACTIONS=1` (off by default since the owner's pause of 2026-10-20: no Bediening tab, `/api/action/*` answers 404 and nothing is written). Under variant A the web app keeps its sessions and its actors file in `/var/lib/lab-ui` (the unit's `StateDirectory`, mode 0700, owned by `lab-ui`), not in `~/.config/solana-lab`. So the owner places `ui-actors.json` there with sudo: only the owner's phone and laptop, never the VPS node. Without the file every action is refused (403); the pages work as before.

```bash
sudo install -d -o lab-ui -g lab-ui -m 700 /var/lib/lab-ui
sudo install -o lab-ui -g lab-ui -m 600 ~/.config/solana-lab/ui-actors.json /var/lib/lab-ui/ui-actors.json   # if you already have one under variant B
sudo cat /var/lib/lab-ui/ui-actors.json                                    # only the phone and the laptop
```

Changing the devices later is the same `sudo install` again. Pairing codes keep working as before (`lab/ui/pair.py` as `chupa`, through `lab/control/pairing`). Sessions from variant B do not carry over: pair each device once more.

**3d. Switch.** Start the unit once without any traverse ACL; all five pages must answer 200.

```bash
sudo install -m 644 lab/ops/systemd/lab-ui-web.system.service /etc/systemd/system/lab-ui-web.service
sudo systemd-analyze verify /etc/systemd/system/lab-ui-web.service     # no errors
systemctl --user disable --now lab-ui-web                               # the old user unit (same port)
sudo systemctl daemon-reload && sudo systemctl enable --now lab-ui-web
systemctl status lab-ui-web --no-pager | head -5                        # active (running), User lab-ui
for p in / /api/paper /api/wallet /api/triton /api/overview; do curl -s -o /dev/null -w "$p %{http_code}\n" http://100.112.193.63:8501$p; done   # all 200
```

## Rollback

```bash
sudo systemctl disable --now lab-ui-web && sudo rm /etc/systemd/system/lab-ui-web.service && sudo systemctl daemon-reload
systemctl --user enable --now lab-ui-web            # the user unit again (lab/ops/systemd/lab-ui-web.service)
```

The ACLs and the user can stay; they grant read only. To remove them (as `chupa`, then `sudo userdel lab-ui`):

```bash
H=/home/chupa; L=$H/Solana-project/data-old-faithful-one/lab
for d in $H/Solana-project/worktrees/lab-ui-deploy $H/Solana-project/worktrees/paper-run/lab/paper \
         $H/.local/share/solana-quant/toolchains/columnar-query-313-duckdb155 $H/.local/share/uv/python/cpython-3.13.15-linux-x86_64-gnu \
         $L/analytics $L/store $L/journal $L/recorder $L/ui-cache $L/backtests $L/wallets/cases-v1 $L/token_meta; do
  [ -d $d ] && setfacl -R -x u:lab-ui $d && find $d -type d -exec setfacl -x d:u:lab-ui {} +
done
for d in $H $H/.local $H/.local/share $H/Solana-project $H/Solana-project/data-old-faithful-one $L; do
  setfacl -x u:lab-ui $d 2>/dev/null || true             # traverse ACLs of an older version of the script, if any
done
```
