"""Every wallet-research script that reads curve trades filters on the quote mint (SOL only), or carries an explicit
`# no-quote-filter:` marker with its reason. Background: 11.6 % of the dev mints have another quote mint (PUMP, …) and
log `q` in that token's units; the 2026-10-18 audit (lab/reports/2026-10-18-audit-niet-sol-quote.md) found E33/E39/E40
contaminated. The engine filters already (strategies.py)."""

import glob
import os
import re

from wallets import common

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
READS_CURVE = re.compile(r"\b(FROM|JOIN)\s+(s\.)?curve\b")


def test_sol_quote_predicate():
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE TABLE m (mint VARCHAR, quote_mint VARCHAR)")
    con.execute(f"INSERT INTO m VALUES ('A', '{common.SOL_KEY}'), ('B', NULL), ('C', 'pumpCmXqMfrs')")
    assert con.execute(f"SELECT list(mint ORDER BY mint) FROM m WHERE {common.SOL_QUOTE}").fetchone()[0] == ["A", "B"]


def test_every_curve_reader_filters_the_quote_or_says_why():
    offenders = []
    for path in sorted(glob.glob(os.path.join(ROOT, "**", "*.py"), recursive=True)):
        if os.sep + "tests" + os.sep in path:
            continue
        src = open(path).read()
        if not READS_CURVE.search(src):
            continue
        if "quote_mint" in src or "SOL_QUOTE" in src or "# no-quote-filter:" in src:
            continue
        offenders.append(os.path.relpath(path, ROOT))
    assert offenders == [], f"curve readers without a quote filter or a `# no-quote-filter: <reason>` marker: {offenders}"
