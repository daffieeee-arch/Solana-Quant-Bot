"""The Rust live route's replay skeleton (lab/live, M1 part 1) against Python on the same files:

- the event rows of a dev chunk in a slot range, in the replay's order, with the same values (a digest per row)
  as DuckDB reads them;
- the same rows from a recorder-style segment built from that range;
- its journal has paper/journal.py's schema exactly (paper.journal_diff.schema_diff).

Needs the built binary: LAB_LIVE_BIN=<path to lab-live> (lab/live/nas_m1a.sh builds it on the NAS). Skips without it.
"""

import hashlib
import json
import os
import subprocess

import duckdb
import pytest

from backtest import data
from paper import journal_diff
from paper.journal import Journal

BIN = os.environ.get("LAB_LIVE_BIN", "")
LO, HI = 450_144_000, 450_146_000  # 2,000 slots of the dev chunk the parity jobs use
PREFIXES = ("pump", "pump_amm")

pytestmark = pytest.mark.skipif(not (BIN and os.access(BIN, os.X_OK)), reason="set LAB_LIVE_BIN to the built lab-live (NAS)")


def _render(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _digest(cols, row):
    return hashlib.sha256("\x1f".join(f"{c}={_render(v)}" for c, v in zip(cols, row)).encode()).hexdigest()


def _nl(v):
    return (v is None, v or 0)


def reference(parts, lo, hi):
    """(table, slot, tx_index, outer_ix, inner_ix, event_seq, digest) of every event row, as DuckDB reads the files,
    in the replay's order: the key with NULLs last, then the table."""
    con = duckdb.connect(config={"memory_limit": "1GB", "threads": 2})
    out = []
    for plo, phi, d in parts:
        if plo >= hi or phi <= lo:
            continue
        a, b = max(lo, plo), min(hi, phi)
        for prefix in PREFIXES:
            pdir = os.path.join(d, prefix)
            if not os.path.isdir(pdir):
                continue
            for name in sorted(os.listdir(pdir)):
                tdir = os.path.join(pdir, name)
                for f in sorted(x for x in os.listdir(tdir) if x.endswith(".parquet")):
                    cur = con.execute(f"SELECT * FROM read_parquet('{os.path.join(tdir, f)}') WHERE slot >= {a} AND slot < {b}")
                    cols = [c[0] for c in cur.description]
                    idx = {c: cols.index(c) if c in cols else None for c in ("slot", "tx_index", "outer_ix", "inner_ix", "event_seq")}
                    for r in cur.fetchall():
                        k = tuple(r[i] if i is not None else None for i in idx.values())
                        out.append((f"{prefix}/{name}", *k, _digest(cols, r)))
    out.sort(key=lambda x: (*(_nl(v) for v in x[1:6]), x[0]))
    return out


def _parse(path):
    out = []
    with open(path) as f:
        for line in f:
            t, *nums, dig = line.rstrip("\n").split("\t")
            out.append((t, *(None if n == "null" else int(n) for n in nums), dig))
    return out


def _run(tmp, *args):
    order, jdir = os.path.join(tmp, "order.tsv"), os.path.join(tmp, "journal")
    subprocess.run([BIN, "replay", *args, "--from", str(LO), "--to", str(HI), "--journal", jdir, "--order-out", order],
                   check=True, timeout=1800)
    return _parse(order), jdir


@pytest.fixture(scope="module")
def chunk_run(tmp_path_factory):
    parts = [c for c in data.complete_chunks() if c[0] < HI and c[1] > LO]
    if not parts:
        pytest.skip("the dev chunk is not available")
    tmp = str(tmp_path_factory.mktemp("chunks"))
    rows, jdir = _run(tmp, "--chunks-root", data.root())
    return parts, rows, jdir


def test_the_rows_and_their_order_are_duckdbs(chunk_run):
    parts, rows, _ = chunk_run
    ref = reference(parts, LO, HI)
    assert len(rows) == len(ref) > 1_000
    assert rows == ref
    assert {r[0] for r in rows} >= {"pump/TradeEvent", "pump_amm/BuyEvent", "pump_amm/SellEvent"}


def test_a_segment_of_the_same_range_gives_the_same_rows(chunk_run, tmp_path):
    parts, rows, _ = chunk_run
    seg = tmp_path / "rec" / "segments" / "000000000"
    con = duckdb.connect(config={"memory_limit": "1GB", "threads": 2})
    for plo, phi, d in parts:
        for prefix in PREFIXES:
            pdir = os.path.join(d, prefix)
            for name in sorted(os.listdir(pdir)) if os.path.isdir(pdir) else ():
                files = sorted(os.path.join(pdir, name, x) for x in os.listdir(os.path.join(pdir, name)) if x.endswith(".parquet"))
                n = con.execute(f"SELECT count(*) FROM read_parquet({files!r}) WHERE slot >= {LO} AND slot < {HI}").fetchone()[0]
                if n:
                    out = seg / prefix / name
                    out.mkdir(parents=True, exist_ok=True)
                    con.execute(f"COPY (SELECT * FROM read_parquet({files!r}) WHERE slot >= {LO} AND slot < {HI}) "
                                f"TO '{out / 'part-00000.parquet'}' (FORMAT parquet)")
    (seg / "_segment.json").write_text(json.dumps({"seq": 0, "first_slot": LO, "last_slot": HI - 1, "complete": True}))
    seg_rows, _ = _run(str(tmp_path), "--segments-root", str(tmp_path / "rec"))
    assert seg_rows == rows


def test_its_journal_has_journal_pys_schema(chunk_run, tmp_path):
    _, rows, jdir = chunk_run
    ref = Journal(str(tmp_path / "python"))
    ref.close()
    assert journal_diff.schema_diff(jdir, str(tmp_path / "python")) == []
    j = Journal(jdir)
    feed, counts = json.loads(j.state("feed")), json.loads(j.state("lab_live_rows"))
    j.close()
    assert (feed["engine"], feed["kind"], feed["from"], feed["to"]) == ("lab-live", "replay", LO, HI)
    assert sum(counts["rows"].values()) == len(rows) and counts["duplicate_keys"] == 0
