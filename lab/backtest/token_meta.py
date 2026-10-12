"""token_meta: a token's name, symbol and uri, for the UI (coordinator, 2026-10-12).

Sources, free, from the data we already extract:
  pump_create      pump.fun's CreateEvent (pump/CreateEvent: name, symbol, uri): every curve token.
  metaplex_*       Metaplex Token Metadata create instructions, and
  token2022        Token-2022's metadata Initialize: decoded by the extractor (table token_meta/)
                   in transactions that touch pump.fun or PumpSwap, e.g. a token minted in the same
                   transaction that creates its pool.
Reversed-pool tokens are not pump.fun mints (0 of 54,027 in the dev store), so their names come
only from the second source, when the mint was created inside the data and in such a transaction;
otherwise through the RPC route (backtest.token_meta_rpc: Triton getMultipleAccounts, off by default,
owner's OK), whose rows under <recorder>/rpc/token_meta/ are read with --recorder.

One row per mint: the first record by slot, pump_create before the others at the same slot.

  python -m backtest.token_meta [--chunks DIR] [--recorder DIR] [--out PATH]
"""

import argparse
import glob
import os

from . import data

OUT = "/home/chupa/Solana-project/data-old-faithful-one/lab/token_meta/token_meta.parquet"
COLS = "mint, name, symbol, uri, source, slot"


def _sources(chunks, recorder=None):
    """SQL selects (mint, name, symbol, uri, source, slot) over the chunks and recorder segments."""
    out = []
    places = [chunks]
    if recorder:  # the recorder's segments, and the RPC route's output (backtest.token_meta_rpc)
        places.append([(0, 2**63 - 1, d) for d in sorted(glob.glob(os.path.join(recorder, "segments", "*")))]
                      + [(0, 2**63 - 1, os.path.join(recorder, "rpc"))])
    for chs in places:
        if data.files("pump/CreateEvent", chs):
            out.append(f"SELECT mint, name, symbol, uri, 'pump_create' AS source, slot FROM {data.parquet('pump/CreateEvent', chs)} "
                       "WHERE mint IS NOT NULL")
        if data.files("token_meta", chs):
            out.append(f"SELECT mint, name, symbol, uri, source, slot FROM {data.parquet('token_meta', chs)} WHERE mint IS NOT NULL")
    return out


def build(chunks=None, recorder=None, out=OUT, memory="1GB"):
    """Write token_meta.parquet; returns {source: mints}."""
    con = data.connect(threads=2, memory=memory)
    chunks = data.complete_chunks() if chunks is None else chunks
    sels = _sources(chunks, recorder)
    if not sels:
        raise SystemExit("no pump/CreateEvent or token_meta rows found")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    con.execute(f"""
      CREATE TEMP TABLE t AS
      SELECT {COLS} FROM ({" UNION ALL ".join(sels)})
      QUALIFY row_number() OVER (PARTITION BY mint ORDER BY slot, CASE source WHEN 'pump_create' THEN 0 ELSE 1 END) = 1""")
    con.execute(f"COPY (SELECT {COLS} FROM t ORDER BY mint) TO '{out}.tmp' (FORMAT parquet)")
    os.replace(out + ".tmp", out)
    counts = dict(con.execute("SELECT source, count(*) FROM t GROUP BY 1 ORDER BY 1").fetchall())
    con.close()
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", default=None, help="OF1 chunks root (default LAB_CHUNKS)")
    ap.add_argument("--recorder", default=None, help="also the recorder's segments under this root")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    chunks = data.complete_chunks(a.chunks)
    print(build(chunks, a.recorder, a.out))


if __name__ == "__main__":
    main()
