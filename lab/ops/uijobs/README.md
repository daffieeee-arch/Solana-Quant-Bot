# UI research jobs on the NAS (B3, part a: the NAS side)

Interactive-UI spec v0.3 §5 and §5.1 (`data-old-faithful-one/lab/research/ui-interactive-spec-2026-10-20.md`).

The UI's strategy chooser asks for a backtest. The VPS sends **parameters only**: one JSON file of at most 4 KB, checked against `schema.py`. The NAS renders it from a template **pinned on the NAS** and runs it. **A compromised VPS, web app or dispatcher can only queue a valid, bounded, templated job.**

## Files

| File | Where it runs | What it does |
|---|---|---|
| `schema.py` | everywhere (stdlib) | The one schema: template, F7 variant (the engine's grids), segment, farm, size, delay, store, whole epochs ≤ 7. **Hold-out 1047–1051 and clean 1055–1063 are never selectable.** `env()` gives the template's `UI_*` values. |
| `ui_queue.py` | NAS host, as the SSH forced command | `sums` (the pinned templates' sha256, read-only), `put` (a job on stdin → `queue/ui-<id>.json`), `status <id>`, `get <id>` (`result.json`). Nothing else. |
| `nas_runner.py` | NAS, a service of the app (≤ 3 CPU / 8 GB) | One job at a time, held while `ui-hold` exists. It runs a template only if the job's `template_sha256` is the one in `SHA256SUMS` and the file still hashes to it. The job's values reach the template as environment only. |
| `templates/backtest-f7.sh` | NAS, via the runner | The tournament for the chosen variant and K3 (`tp4_T600`) on the job's store and epochs, then `result.py`. |
| `result.py` | NAS (lab venv) | The base-scenario result: net P&L per trade and Σ per day, each with the 10,000-draw day bootstrap of `paper/stoprule.py`, for the variant and for K3. |

## Install (the coordinator, after review)

`<C>` is the merge commit of this PR, unpacked as `/lab/code/<C>` with its `MANIFEST.sha256`, as for other NAS jobs. `<NAS_LAB>` is the host path that the app mounts as `/lab`.

1. **The code and the templates (host).**

   ```bash
   install -d -m 755 <NAS_LAB>/bin/uijobs <NAS_LAB>/templates <NAS_LAB>/jobs-ui
   install -m 644 /lab/code/<C>/lab/ops/uijobs/{schema,ui_queue,nas_runner,result}.py <NAS_LAB>/bin/uijobs/
   install -m 644 /lab/code/<C>/lab/ops/uijobs/templates/backtest-f7.sh <NAS_LAB>/templates/
   (cd <NAS_LAB>/templates && sha256sum backtest-f7.sh > SHA256SUMS)       # the pin; change only after a review
   ```

   Make `bin/uijobs` and `templates` writable by root only. The job key's user must not be able to change them.

2. **The job key (VPS → NAS).**
   - On the VPS, as the owner's user:

     ```bash
     ssh-keygen -t ed25519 -N '' -C lab-ui-jobs -f ~/.config/solana-lab/nas-ui-jobs
     chmod 600 ~/.config/solana-lab/nas-ui-jobs
     ```

     - **Under variant B** (spec §2.3) this key is readable by the web server's user. That is acceptable: its forced command can only queue a valid templated job, read a job's status or result.
     - **Under variant A** only the owner's user (the dispatcher) reads it.
   - On the NAS, in the `authorized_keys` of an unprivileged user that may write `<NAS_LAB>/jobs-ui` (one line):

     ```text
     restrict,command="UI_JOBS_DIR=<NAS_LAB>/jobs-ui UI_TEMPLATES_DIR=<NAS_LAB>/templates /usr/bin/python3 -I <NAS_LAB>/bin/uijobs/ui_queue.py" ssh-ed25519 AAAA… lab-ui-jobs
     ```

     `restrict` turns off port, agent and X11 forwarding and the pty. The forced command ignores whatever command the client asks for and reads it only from `SSH_ORIGINAL_COMMAND`.
   - Check from the VPS:
     - `ssh -i ~/.config/solana-lab/nas-ui-jobs <nas> status 01JABCDEFGHJKMNPQRSTVWXYZ0` prints `{"…": "unknown"}`;
     - `ssh … ls` prints `refused: …` and exits 2.

3. **The runner (a service of the app, lowest priority).**
   - Image `python:3.13` with `/lab/venv`, limits **3 CPU / 8 GB**.
   - Environment:
     - `UI_JOBS_DIR=/lab/jobs-ui`
     - `UI_TEMPLATES_DIR=/lab/templates`
     - `LAB_PY=/lab/venv/bin/python`
     - `LAB_CODE=/lab/code/<C>` (the checkout root; the template adds `/lab` to `PYTHONPATH`)
     - `LAB_STORE_DIR=/home/chupa/Solana-project/data-old-faithful-one/lab/store`
   - Command:

     ```bash
     cd /lab/code/<C> && sha256sum --quiet -c MANIFEST.sha256 && exec nice -n 19 /usr/local/bin/python3 -I /lab/bin/uijobs/nas_runner.py
     ```

   - **Hold UI jobs:** `touch <NAS_LAB>/jobs-ui/ui-hold` (during a hold-out run or an app update). Remove it to go on.
   - Results land in `<NAS_LAB>/jobs-ui/results/<id>/`: `status.json`, `result.json` and `job.log`.

## What stays out

- No script travels: the VPS cannot send code.
- No path, free text or shell value enters a job: everything is an enum, a bounded number or a ULID, checked three times (UI, VPS, NAS).
- No store with the hold-out or the clean period is listed. `validate()` refuses any epoch range that touches them, again on the NAS before a store is opened.
- No `app.update` from a UI job.

## Tests

`lab/ops/tests/test_uijobs.py` covers:
- the schema, including fuzzing and the hold-out/clean refusals;
- that the grids equal the engine's (`backtest.strategies`);
- that the forced command writes only queue files and reads only results;
- the runner: it is held by `ui-hold`, refuses a sha that is not pinned or a changed template, and passes no other environment (a secret in its own environment does not reach the template).

`result.py` was smoke-tested on `backtests/long-dev-1033-1046-fc4173c`:

| Variant | Net per trade | 95% interval | Run time |
|---|---|---|---|
| flow300_H6, organic | +0.051 SOL | [0.026, 0.078] | 1.6 s |
| K3 (tp4_T600) | +0.0058 SOL | [0.0034, 0.0076] | — |
