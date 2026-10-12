"""Build the "Wat zit er in de data" page from lab/bin/q results.

    "$LAB_PY" lab/ui/pages/build_data_overview.py   # light: four q queries, ~15 s

Writes $LAB_DATA_ROOT/ui-cache/pages/data-overview.html (published as a private Artifact).
Hold-out epochs are listed by number only; their chunks and manifests are not read.
"""
import datetime as dt
import json
import os
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
SQL_DIR = os.path.join(REPO, "lab", "analytics", "sql")
EPOCH = 432_000
HOLDOUT_START = 452_304_000
FIRST_EPOCH, LAST_OF1_EPOCH = 1033, 1051


def q(name):
    path = os.path.join(SQL_DIR, name + ".sql")
    r = subprocess.run([os.path.join(REPO, "lab", "bin", "q"), "--json", "--file", path],
                       capture_output=True, text=True, timeout=400)
    if r.returncode != 0:
        sys.exit(f"q failed for {name}: {r.stderr}")
    out = json.loads(r.stdout)
    return {"sql": open(path).read(), "columns": out["columns"], "rows": out["rows"]}


def chunk_status():
    """Extraction status per development epoch from chunk manifests (status field only)."""
    root = os.path.join(DATA, "events", "v1", "chunks")
    status = {}
    for name in sorted(os.listdir(root)):
        parts = name.split("-")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            continue
        start, end = int(parts[0]), int(parts[1])
        if start >= HOLDOUT_START:
            continue
        man = os.path.join(root, name, "_manifest.json")
        ok = False
        if os.path.exists(man):
            with open(man) as f:
                ok = json.load(f).get("status") == "complete"
        e = start // EPOCH
        status.setdefault(e, []).append((start, end, ok))
    return status


def epochs(store_epochs):
    chunks = chunk_status()
    rows = []
    for e in range(FIRST_EPOCH, LAST_OF1_EPOCH + 1):
        if e * EPOCH >= HOLDOUT_START:
            state = "holdout"
        elif e in store_epochs:
            state = "store"
        else:
            parts = chunks.get(e, [])
            covered = sum(end - start for start, end, ok in parts if ok)
            state = "extracted" if covered >= EPOCH else ("running" if parts else "planned")
        rows.append({"epoch": e, "state": state})
    return rows


def main():
    data = {name: q(name) for name in
            ["overview_summary", "overview_epochs", "overview_days", "overview_tokens"]}
    store_epochs = {r[0] for r in data["overview_epochs"]["rows"]}
    data["coverage"] = epochs(store_epochs)
    data["built_at"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    blob = json.dumps(data, ensure_ascii=False, default=str)
    blob = blob.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    tpl = open(os.path.join(os.path.dirname(__file__), "data-overview.html")).read()
    out_dir = os.path.join(DATA, "ui-cache", "pages")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "data-overview.html")
    with open(out + ".tmp", "w") as f:
        f.write(tpl.replace("/*__DATA__*/null", blob, 1))
    os.replace(out + ".tmp", out)
    print(out)


if __name__ == "__main__":
    main()
