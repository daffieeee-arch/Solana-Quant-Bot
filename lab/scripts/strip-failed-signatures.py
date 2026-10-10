"""One-off maintenance: drop the `signature` column from failed_txs in complete chunks.

From extractor commit "slim failed_txs" on, failed_txs has no signature column ((slot, tx_index)
identifies the transaction; the signatures were ~75% of the table). This rewrites the files of
chunks written before that change, so the whole dataset has one failed_txs schema and the disk
space is freed. Row counts are checked before each file is replaced; chunks without a complete
manifest (still being written) are skipped.

Usage: python strip-failed-signatures.py <chunks_dir>
"""

import glob
import json
import os
import sys

import duckdb


def main():
    root = sys.argv[1]
    con = duckdb.connect()
    con.execute(f"SET threads TO {int(os.environ.get('LAB_DUCKDB_THREADS', '2'))}")
    con.execute(f"SET memory_limit = '{os.environ.get('LAB_DUCKDB_MEM', '2GB')}'")
    freed = 0
    for m in sorted(glob.glob(os.path.join(root, "*", "_manifest.json"))):
        with open(m) as f:
            if json.load(f)["status"] != "complete":
                continue
        for part in sorted(glob.glob(os.path.join(os.path.dirname(m), "failed_txs", "part-*.parquet"))):
            cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{part}')").fetchall()]
            if "signature" not in cols:
                continue
            tmp = os.path.join(os.path.dirname(part), "." + os.path.basename(part) + ".strip.tmp")
            con.execute(f"""COPY (SELECT * EXCLUDE (signature) FROM read_parquet('{part}'))
                            TO '{tmp}' (FORMAT parquet, COMPRESSION zstd)""")
            before = con.execute(f"SELECT count(*) FROM read_parquet('{part}')").fetchone()[0]
            after = con.execute(f"SELECT count(*) FROM read_parquet('{tmp}')").fetchone()[0]
            if before != after:
                os.remove(tmp)
                raise SystemExit(f"row count mismatch for {part}: {before} != {after}")
            with open(tmp, "rb") as f:
                os.fsync(f.fileno())
            size_before = os.path.getsize(part)
            os.replace(tmp, part)
            freed += size_before - os.path.getsize(part)
        print(f"{os.path.basename(os.path.dirname(m))}: freed so far {freed / 1e9:.1f} GB", flush=True)
    print(f"done, freed {freed / 1e9:.1f} GB")


if __name__ == "__main__":
    main()
