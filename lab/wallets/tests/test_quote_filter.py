"""Every wallet-research script that reads curve trades filters on the quote mint (SOL only), or carries an explicit
`# no-quote-filter:` marker with its reason. Background: 11.6 % of the dev mints have another quote mint (PUMP, …) and
log `q` in that token's units; the 2026-10-18 audit (lab/reports/2026-10-18-audit-niet-sol-quote.md) found E33/E39/E40
contaminated. The engine filters already (strategies.py).

The check is a substring tripwire, not a parser: a file that reads `curve` passes when it contains `quote_mint`,
`SOL_QUOTE` or the marker anywhere. It cannot see whether the filter is applied to the right query; it only makes a
new script without any quote thought fail. A self-check asserts that known readers were actually scanned (LAB review on
#364: an unnormalised ROOT made every path contain `/tests/` and the scan silently skipped all files)."""

import glob
import os
import re

from wallets import common

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))  # lab/wallets
READS_CURVE = re.compile(r"\b(FROM|JOIN)\s+(s\.)?curve\b")


def test_sol_quote_predicate():
    # after a LEFT JOIN on mints: a SOL row passes, another quote fails, and a trade whose mint has no mints row is NOT
    # admitted (its quote is unknown; Codex round 3, point 2)
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE TABLE m (mint VARCHAR, quote_mint VARCHAR)")
    con.execute(f"INSERT INTO m VALUES ('A', '{common.SOL_KEY}'), ('C', 'pumpCmXqMfrs')")
    con.execute("CREATE TABLE c (mint VARCHAR, q BIGINT)")
    con.execute("INSERT INTO c VALUES ('A', 1), ('C', 1), ('Z', 1)")
    assert con.execute(f"SELECT list(c.mint ORDER BY c.mint) FROM c LEFT JOIN m USING (mint) WHERE {common.SOL_QUOTE}").fetchone()[0] == ["A"]


def scan(root=ROOT):
    """(scanned files, curve readers, offenders) under root, relative paths; the tests directory is skipped."""
    scanned, readers, offenders = [], [], []
    for path in sorted(glob.glob(os.path.join(root, "**", "*.py"), recursive=True)):
        rel = os.path.relpath(path, root)
        if rel.split(os.sep)[0] == "tests":
            continue
        scanned.append(rel)
        src = open(path).read()
        if not READS_CURVE.search(src):
            continue
        readers.append(rel)
        if "quote_mint" in src or "SOL_QUOTE" in src or "# no-quote-filter:" in src:
            continue
        offenders.append(rel)
    return scanned, readers, offenders


def test_every_curve_reader_filters_the_quote_or_says_why():
    scanned, readers, offenders = scan()
    # self-check: the scan really saw the known readers (one with the filter, one with the marker)
    assert "common.py" in scanned and "winners/k1_rolling.py" in scanned and len(scanned) > 50, scanned
    assert "winners/k1_rolling.py" in readers and "winners/slow_legs.py" in readers, readers
    assert offenders == [], f"curve readers without a quote filter or a `# no-quote-filter: <reason>` marker: {offenders}"


def test_scan_catches_an_unfiltered_reader(tmp_path):
    (tmp_path / "ok.py").write_text("x = 'SELECT 1 FROM curve c JOIN mints m WHERE m.quote_mint IS NOT NULL'\n")
    (tmp_path / "marked.py").write_text("# no-quote-filter: a test\nx = 'SELECT 1 FROM s.curve'\n")
    (tmp_path / "bad.py").write_text("x = 'SELECT q FROM s.curve WHERE slot > 1'\n")
    (tmp_path / "none.py").write_text("x = 'SELECT 1 FROM pool'\n")
    scanned, readers, offenders = scan(str(tmp_path))
    assert len(scanned) == 4 and sorted(readers) == ["bad.py", "marked.py", "ok.py"] and offenders == ["bad.py"]
