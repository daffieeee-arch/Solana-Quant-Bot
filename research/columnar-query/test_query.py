"""Independent reader boundary test using the Rust-generated real Parquet fixture."""
import pathlib
import sys
import json
from query import connect, rows

with connect() as db:
    db.from_parquet(str(pathlib.Path(sys.argv[1]).resolve() / "boundary.parquet")).create_view("bounds")
    cursor = db.execute("SELECT amount_raw_u64, timestamp_raw_i64, base_decimals, base_decimals_state, quote_decimals, quote_decimals_state FROM bounds ORDER BY record_ordinal")
    result = rows(cursor)
    assert result["columns"][0]["duckdb_type"] == "UBIGINT"
    assert result["columns"][1]["duckdb_type"] == "BIGINT"
    assert result["rows"] == [["0", "-9223372036854775808", None, "NULL", None, "MISSING"],
                              ["9007199254740993", "-1", None, "NULL", None, "MISSING"],
                              ["9223372036854775808", "0", None, "NULL", None, "MISSING"],
                              ["18446744073709551615", "9223372036854775807", None, "NULL", None, "MISSING"]]
    print("DuckDB actual Parquet UBIGINT/BIGINT/null/MISSING boundary parity PASS (4 rows)")
    root = pathlib.Path(sys.argv[1]).resolve()
    db.from_parquet(str(root / "duplicate-bronze.parquet")).create_view("bronze")
    db.from_parquet(str(root / "one-silver.parquet")).create_view("silver")
    query = json.loads(pathlib.Path(__file__).with_name("queries.sql.json").read_text())["parent_binding"]
    assert db.execute(query).fetchall() == [(1, 1)]
    assert db.execute("SELECT COUNT(*) FROM bronze").fetchone() == (2,)
    print("DuckDB duplicate Bronze parents retained without inflated Silver count PASS")
    coverage_queries = json.loads(pathlib.Path(__file__).with_name("coverage.sql.json").read_text())
    for sql in coverage_queries.values():
        db.execute(sql).fetchall()
    assert db.execute(coverage_queries['duplicate_identity']).fetchall()[0][-1] == 2
    assert db.execute(coverage_queries['foreign_parent']).fetchall() == []
    assert db.execute(coverage_queries['slot_counts']).fetchall()[0][1] == 2
    print("Coverage SQL runs on Rust Parquet; duplicate packages remain detectable PASS")
