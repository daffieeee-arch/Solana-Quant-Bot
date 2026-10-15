"""checks.learned_mints on a synthetic transfers chunk."""

import json
import os

import duckdb

from checks import learned_mints as lm

PUMP = "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn"


def _table(chunk, table, rows):
    d = os.path.join(chunk, table)
    os.makedirs(d, exist_ok=True)
    values = ", ".join(f"('{m}')" for m in rows)
    duckdb.connect().execute(f"COPY (SELECT * FROM (VALUES {values}) v(mint)) TO '{d}/part.parquet' (FORMAT parquet)")


def test_sources_failures_and_non_pump_rows(tmp_path):
    chunk = str(tmp_path / "455760000-455976000")
    os.makedirs(chunk)
    with open(os.path.join(chunk, "_manifest.json"), "w") as f:
        json.dump({"status": "complete"}, f)
    _table(chunk, "pump/CreateEvent", ["NewMint"])
    _table(chunk, "pump/DistributeCreatorFeesEvent", ["FeeMint"])
    _table(chunk, "pump/ClaimTokenIncentivesEvent", [PUMP])
    _table(chunk, "token_transfers", [PUMP, PUMP, "NewMint"])
    _table(chunk, "token_deltas", [PUMP])
    with open(os.path.join(chunk, "_learned_mints.txt"), "w") as f:
        f.write("\n".join(["NewMint", "FeeMint", PUMP, "StoreMint", "Orphan"]) + "\n")
    store = str(tmp_path / "store.duckdb")
    s = duckdb.connect(store)
    s.execute("CREATE TABLE mints AS SELECT 'StoreMint' AS mint")
    s.close()
    out = tmp_path / "out.json"
    assert lm.main([str(tmp_path), "--store", store, "--out", str(out)]) == 1  # PUMP and Orphan fail
    r = json.load(open(out))
    c = r["chunks"][0]
    assert c["sources"] == {"create_or_trade": 1, "curve_event": 1, "store": 1, "incentive": 1, "none": 1}
    assert c["failures"] == sorted([PUMP, "Orphan"]) and c["non_pump_learned"] == ["PUMP"]
    assert c["non_pump_rows"] == {"token_transfers": {"PUMP": 2}, "token_deltas": {"PUMP": 1}}
    assert r["total"] == {"chunks": 1, "learned": 5, "failures": 2, "without_learned_file": []}
