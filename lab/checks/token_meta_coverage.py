"""Coverage of the extractor's token_meta table on an extracted chunk or smoke output (light).

  python -m checks.token_meta_coverage <chunk dir> [<chunk dir> ...]

- rows per source (metaplex_v1/v2/v3/create, token2022);
- pump.fun tokens: the decoded name and symbol against the CreateEvent's (must agree; a
  disagreement means a decoding error);
- reversed pools created in the data (CreatePoolEvent with WSOL as base): the share whose token
  (quote mint) got a token_meta row, i.e. was minted in a transaction the extractor reads. The rest
  needs the RPC route (Triton getMultipleAccounts, owner's OK).
"""

import glob
import json
import os
import sys

import duckdb

WSOL = "So11111111111111111111111111111111111111112"


def src(dirs, table):
    files = sorted(f for d in dirs for f in glob.glob(os.path.join(d, table, "*.parquet")))
    return f"read_parquet({files!r}, union_by_name = true)" if files else None


def coverage(dirs):
    con = duckdb.connect()
    con.execute("SET memory_limit = '1GB'; SET threads TO 2")
    tm, ce, cp = src(dirs, "token_meta"), src(dirs, "pump/CreateEvent"), src(dirs, "pump_amm/CreatePoolEvent")
    out = {"dirs": dirs, "token_meta_rows": 0}
    if not tm:
        return out
    out["token_meta_rows"] = con.execute(f"SELECT count(*) FROM {tm}").fetchone()[0]
    out["by_source"] = dict(con.execute(f"SELECT source, count(*) FROM {tm} GROUP BY 1 ORDER BY 1").fetchall())
    if ce:
        n, same = con.execute(f"""SELECT count(*), count(*) FILTER (WHERE t.name = c.name AND t.symbol = c.symbol)
                                  FROM {ce} c JOIN (SELECT DISTINCT ON (mint) * FROM {tm} ORDER BY mint, slot) t USING (mint)""").fetchone()
        creates = con.execute(f"SELECT count(DISTINCT mint) FROM {ce}").fetchone()[0]
        out["pump_tokens"] = {"creates": creates, "with_token_meta": n, "name_and_symbol_agree": same}
    if cp:
        r, covered = con.execute(f"""SELECT count(DISTINCT quote_mint), count(DISTINCT quote_mint) FILTER (WHERE quote_mint IN (SELECT mint FROM {tm}))
                                      FROM {cp} WHERE base_mint = '{WSOL}'""").fetchone()
        out["reversed_pools"] = {"tokens": r, "with_token_meta": covered, "share": round(covered / r, 4) if r else None}
    return out


if __name__ == "__main__":
    print(json.dumps(coverage(sys.argv[1:]), indent=1))
