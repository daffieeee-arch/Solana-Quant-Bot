# UI research jobs on the NAS (B3, part a: the NAS side)

Interactive-UI spec v0.3 §5 and §5.1 (`data-old-faithful-one/lab/research/ui-interactive-spec-2026-10-20.md`).

The UI's strategy chooser asks for a backtest. The VPS sends **parameters only**: one JSON file of at most 4 KB, checked against `schema.py`. The NAS renders it from a template **pinned on the NAS** and runs it. **A compromised VPS, web app or dispatcher can only queue a valid, bounded, templated job.**

## Files

| File | Where it runs | What it does |
|---|---|---|
| `schema.py` | everywhere (stdlib) | The one schema: template, F7 variant (the engine's grids), segment, farm, size, delay, store, whole epochs ≤ 7. **Hold-out 1047–1051 and clean 1055–1063 are never selectable.** `env()` gives the template's `UI_*` values. |
| `ui_queue.py` | NAS host, as the SSH forced command | `sums` (the pinned templates' sha256, read-only), `put` (a job on stdin → `queue/ui-<id>.json`, in canonical form; the same job again is accepted once more without storing anything, another job under a used id is refused), `status <id>` (with the stored job's `job_sha256`), `get <id>` (`result.json`, only once the job is `done`). Nothing else. |
| `nas_runner.py` | NAS, a service of the app (≤ 3 CPU / 8 GB) | One job at a time (`runner.lock`), held while `ui-hold` exists. It runs a template only if the job's `template_sha256` is the one in `SHA256SUMS` and the file still hashes to it. The job's values reach the template as environment only. A job left in `running/` by a stopped runner is recovered, never run again: it waits while the job's processes (found by `UI_JOB_ID` in their environment, via `/proc`) still run, kills them past the timeout, then gives it the final status already written or `failed` "interrupted". |
| `templates/backtest-f7.sh` | NAS, via the runner | The tournament for the chosen variant and K3 (`tp4_T600`) on the job's store and epochs, then `result.py`. |
| `result.py` | NAS (lab venv) | The result **as the canonical tournament report computes it**: `backtest.report.summarize` itself on the run's parts (base costs, the report's day-clustered bootstrap), one row per variant, d and regime, for the variant and for K3. |

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

## Retention

Keep `jobs-ui/done/ui-<id>.json` as long as `jobs-ui/results/<id>/`: `status` takes the job's `job_sha256` from that file, and the dispatcher treats an answer without it as unclear (after 10 passes the job is failed). Delete the two together, and only for a job that is final on the VPS (`control/nas-status/<id>.json` done, failed or refused).

## Template checklist (review of a template or of what it runs)

The runner finds a job's processes by `UI_JOB_ID` in their environment (recovery after a stopped runner). So a template and everything it starts:
- never clears or replaces the environment: no `env -i`, no `exec -c`, no re-exec with a new environment (Python's `subprocess` and `multiprocessing` keep it, which is fine);
- never daemonises: no `setsid`, `nohup … &`, `disown` or double fork that leaves a process behind; every worker is a child the template waits for;
- writes `result.json` last, and only when the job succeeded (the runner's `done` needs exit 0 and the file).

Considered, not done: the container's own `cgroup.procs` as a second liveness source. It also lists an operator's `docker exec` shell, which the runner cannot tell from a job's process (and must never kill); a per-job cgroup would need cgroup delegation the app does not have.

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

`result.py` reproduces the report exactly: `test_result_rows_are_the_tournament_reports` compares every K3 and flow300_H6 row of `long-dev-1033-1046-fc4173c/summary.parquet` (farm all and organic) with its output.
