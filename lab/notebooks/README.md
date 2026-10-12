# lab/notebooks: marimo notebooks for the owner (read-only)

Notebooks are plain `.py` files (marimo format). `lab/ops/systemd/lab-notebooks.service` serves this
folder with `marimo run` on http://100.112.193.63:8502 (Tailscale only, token auth), from the pinned
deploy worktree `worktrees/lab-notebooks` (detached on a main commit, moved only on deploy). That is
app mode: the code is not sent to the browser, and nothing can be edited or run from there. Every
`.py` file here that contains `marimo.App(` shows up in the gallery; `labdata.py` is a helper, not a
notebook.

Rules for new notebooks:
- Read only. Query data through `labdata.connect()`. It opens DuckDB in memory with `memory_limit` 1GB
  and 2 threads, with `analytics.duckdb` attached READ_ONLY as `a`. Never write files.
- Development data only; hold-out results only as finished reports (as `uitslagen.py` does).
- Show a missing input as a warning, not a crash.
- Use `_name` for cell-local variables; marimo forbids defining the same name in two cells.
- Check: `"$LAB_PY" lab/notebooks/tests/test_notebooks.py` (lab-py; it runs `marimo check` and every
  notebook headless). Try it out locally with
  `marimo run lab/notebooks --host 127.0.0.1 --port <free>`, or `marimo edit` on 127.0.0.1 only, never on the service.

| Notebook | Content |
|---|---|
| `uitslagen.py` | Hold-out (K1–K3, group B+C, decision), M1+/M1-early (binding period, per family and size, §10 verdict), paired exact-in measurement |
