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
- **Wat jij doet:** eenmalig stap 1 en 3 hieronder, met `sudo`, zo'n 2 minuten werk. Stap 2 doet de coördinator of LAB ui, zonder sudo.
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
bash lab/ops/ui-grant-read.sh            # shows what it will do: traverse on the path down, read on the app's trees
bash lab/ops/ui-grant-read.sh --apply
```

- **What it grants:**
  - traverse (`x`) on the directories down from `/home/chupa`;
  - read (`rX`, with default ACLs so new files inherit) on the deploy worktree, the server's Python, `paper-run/lab/paper` (the risk files), and `lab/{analytics,store,journal,recorder,ui-cache,backtests,wallets/cases-v1}`.
- **Never:** any write, and nothing under `~/.config/solana-lab` (the script refuses if it finds one).
- **Run it again** after `paper-run` exists, and after a new checkout outside the inherited trees.

**Check before step 3: SQLite WAL as a reader that cannot write `-shm`.** Every journal page depends on this.

```bash
sudo -u lab-ui ~/.local/share/solana-quant/toolchains/columnar-query-313-duckdb155/bin/python -I -c '
import glob, sqlite3
for p in glob.glob("/home/chupa/Solana-project/data-old-faithful-one/lab/journal/*/journal/journal.sqlite"):
    db = sqlite3.connect(f"file:{p}?mode=ro", uri=True); db.execute("PRAGMA query_only=1")
    print(p, db.execute("SELECT count(*) FROM exits").fetchone())'
```

If it fails with "attempt to write a readonly database" or "unable to open", grant `rw` on the `-shm` file only (`setfacl -m u:lab-ui:rw …/journal.sqlite-shm`). Do not use `immutable=1`: it would read stale data.

## Step 3 — the owner, with sudo: switch the web app to the system unit

```bash
cd ~/Solana-project/worktrees/lab-ui-deploy
sudo install -m 644 lab/ops/systemd/lab-ui-web.system.service /etc/systemd/system/lab-ui-web.service
sudo systemd-analyze verify /etc/systemd/system/lab-ui-web.service     # no errors
systemctl --user disable --now lab-ui-web                               # the old user unit (same port)
sudo systemctl daemon-reload && sudo systemctl enable --now lab-ui-web
systemctl status lab-ui-web --no-pager | head -5                        # active (running), User lab-ui
for p in / /api/paper /api/wallet /api/triton /api/overview; do curl -s -o /dev/null -w "$p %{http_code}\n" http://100.112.193.63:8501$p; done   # all 200
```

## Acceptance (variant A), as `lab-ui` inside the unit's sandbox

Each of these must **fail**:

```bash
sudo systemd-run --pipe --wait -p User=lab-ui -p ProtectHome=tmpfs -p InaccessiblePaths=/run/user \
  -- sh -c 'systemctl --user is-active lab-ui-web; echo "systemctl rc=$?"'            # rc ≠ 0 (no user bus)
sudo -u lab-ui cat /home/chupa/.config/solana-lab/recorder.env                        # Permission denied
sudo -u lab-ui touch /home/chupa/Solana-project/data-old-faithful-one/lab/journal/x   # Permission denied
sudo -u lab-ui kill -0 "$(systemctl --user show -p MainPID --value lab-paper)"        # Operation not permitted
```

## Rollback

```bash
sudo systemctl disable --now lab-ui-web && sudo rm /etc/systemd/system/lab-ui-web.service && sudo systemctl daemon-reload
systemctl --user enable --now lab-ui-web            # the user unit again (lab/ops/systemd/lab-ui-web.service)
```

The ACLs and the user can stay; they grant read only. Remove them with `setfacl -R -x u:lab-ui <dir>` and `sudo userdel lab-ui`.
