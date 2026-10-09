"""Reserve-continuity check for the lab event dataset.

Events are put in chain order (slot, tx_index, outer_ix, inner_ix) per bonding curve (mint) and
per PumpSwap pool. The state after event n must equal the state before event n+1. A mismatch
means a missing event, a wrong order, a decoding error, or a reserve change without an event.
Every pair is classified as ok, bad, or unverifiable (an operand is unknown, e.g. after an event
whose pre- or post-state the IDL does not expose). Bad pairs are reported per chunk, so a local
loss is not diluted by a global rate.

pump (bonding curve). TradeEvent reserves are POST-trade; amounts are curve-side, net of fees.
  The quote_* fields hold the real values for every quote mint (for SOL pairs they equal sol_*).
  buy:  pre_tokens = post + token_amount,  pre_quote = post - quote_amount
  sell: pre_tokens = post - token_amount,  pre_quote = post + quote_amount
  Links: CreateEvent anchors a chain (post = initial reserves, real quote 0);
  UpdateMayhemVirtualParamsEvent maps old -> new virtual reserves. Trades by the Mayhem program's
  sol vault reset virtual quote reserves without an event, so the virtual-quote check skips pairs
  that involve one; the main check uses virtual tokens, real tokens and real quote.

pump_amm (PumpSwap). Buy/SellEvent reserves are PRE-trade. Prices use the effective quote
  E = pool_quote_token_reserves + virtual_quote_reserves; fees kept in the pool move Q and V in
  opposite directions and fee sweeps move them back, so (B, E) is the robust invariant:
  buy:  B' = B - base_amount_out,  E' = E + quote_amount_in_with_lp_fee
  sell: B' = B + base_amount_in,   E' = E - quote_amount_out_without_lp_fee
  Links: CreatePoolEvent anchors a chain (B, E = pool amounts, V assumed 0); deposits and
  withdrawals check B only; BoostBuyAndBurnEvent and InitBoostEvent expose only a post-state.

Usage: python continuity.py <chunks_dir> [--expect START:END] [--report out.json] [--examples N]
Only chunks whose `_manifest.json` has status "complete" are used.
"""

import argparse
import glob
import json
import os
import sys

import duckdb

MAYHEM_AGENT = "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s"


def scan_chunks(root):
    done, other = [], []
    for d in sorted(glob.glob(os.path.join(root, "*-*"))):
        m = os.path.join(d, "_manifest.json")
        if not os.path.isfile(m):
            other.append({"dir": os.path.basename(d), "status": "no_manifest"})
            continue
        with open(m) as f:
            man = json.load(f)
        row = (man["slot_start"], man["slot_end_exclusive"], man["status"], d)
        (done if man["status"] == "complete" else other).append(
            row if man["status"] == "complete" else {"dir": os.path.basename(d), "status": man["status"]})
    return sorted(done), other


def coverage_holes(done, expect):
    holes = []
    pos = expect[0] if expect else (done[0][0] if done else 0)
    stop = expect[1] if expect else (done[-1][1] if done else 0)
    for s, e, _, _ in done:
        if s > pos:
            holes.append([pos, s])
        pos = max(pos, e)
    if pos < stop:
        holes.append([pos, stop])
    return holes


def files(dirs, table):
    return sorted(f for d in dirs for f in glob.glob(os.path.join(d, table, "*.parquet")))


def lit(paths):
    return "[" + ",".join("'" + p.replace("'", "''") + "'" for p in paths) + "]"


def src(paths):
    # Deduplicate in case a firehose thread restart replayed part of a slot.
    return f"""(SELECT DISTINCT ON (signature, outer_ix, inner_ix) *
                FROM read_parquet({lit(paths)}, union_by_name = true))"""


H = "::HUGEINT"


def pair_stats(con, table, ok_expr, cmp_cols, key, kind_col="kind"):
    null_any = " OR ".join(f"{c} IS NULL" for c in cmp_cols)
    con.execute(f"""
      CREATE OR REPLACE TEMP VIEW {table}_pairs AS
      SELECT *, CASE WHEN n = 1 THEN NULL
                     WHEN {null_any} THEN 'unverifiable'
                     WHEN {ok_expr} THEN 'ok' ELSE 'bad' END AS verdict
      FROM {table}""")
    r = dict(con.execute(f"SELECT verdict, count(*) FROM {table}_pairs WHERE n > 1 GROUP BY 1").fetchall())
    out = {"pairs": sum(r.values()), "ok": r.get("ok", 0), "bad": r.get("bad", 0),
           "unverifiable": r.get("unverifiable", 0)}
    out["ok_rate_of_verifiable"] = out["ok"] / max(1, out["ok"] + out["bad"])
    out["by_kind"] = {f"{k}<-{pk}": {"ok": o, "bad": b, "unverifiable": u} for k, pk, o, b, u in con.execute(f"""
      SELECT {kind_col}, prev_kind, count(*) FILTER (WHERE verdict = 'ok'), count(*) FILTER (WHERE verdict = 'bad'),
             count(*) FILTER (WHERE verdict = 'unverifiable')
      FROM {table}_pairs WHERE n > 1 GROUP BY 1, 2 ORDER BY 4 DESC, 3 DESC""").fetchall()}
    out["bad_by_chunk"] = {f"{s}-{e}": c for s, e, c in con.execute(f"""
      SELECT c.s, c.e, count(*) FROM {table}_pairs p JOIN chunks c ON p.slot >= c.s AND p.slot < c.e
      WHERE verdict = 'bad' GROUP BY 1, 2 ORDER BY 1""").fetchall()}
    gap = con.execute(f"""SELECT quantile_cont(slot - prev_slot, [0.5, 0.9, 0.99]) FROM {table}_pairs
                          WHERE verdict = 'bad'""").fetchone()[0]
    out["bad_slot_gap_p50_p90_p99"] = gap
    out["chains"] = dict(zip(["total", "anchored"], con.execute(f"""
      SELECT count(DISTINCT {key}), count(DISTINCT {key}) FILTER (WHERE n = 1 AND {kind_col} LIKE 'create%')
      FROM {table}""").fetchone()))
    return out


def pump_check(con, dirs, examples):
    trades = files(dirs, "pump/TradeEvent")
    if not trades:
        return None
    parts = [f"""
      SELECT 'trade' AS kind, slot, tx_index, outer_ix, inner_ix, signature, mint, "user" AS actor,
        CASE WHEN is_buy THEN virtual_token_reserves{H} + token_amount{H} ELSE virtual_token_reserves{H} - token_amount{H} END AS pre_vt,
        CASE WHEN is_buy THEN COALESCE(virtual_quote_reserves, virtual_sol_reserves){H} - COALESCE(quote_amount, sol_amount){H}
             ELSE COALESCE(virtual_quote_reserves, virtual_sol_reserves){H} + COALESCE(quote_amount, sol_amount){H} END AS pre_vq,
        CASE WHEN is_buy THEN real_token_reserves{H} + token_amount{H} ELSE real_token_reserves{H} - token_amount{H} END AS pre_rt,
        CASE WHEN is_buy THEN COALESCE(real_quote_reserves, real_sol_reserves){H} - COALESCE(quote_amount, sol_amount){H}
             ELSE COALESCE(real_quote_reserves, real_sol_reserves){H} + COALESCE(quote_amount, sol_amount){H} END AS pre_rq,
        virtual_token_reserves{H} AS post_vt, COALESCE(virtual_quote_reserves, virtual_sol_reserves){H} AS post_vq,
        real_token_reserves{H} AS post_rt, COALESCE(real_quote_reserves, real_sol_reserves){H} AS post_rq,
        decode_status
      FROM {src(trades)}"""]
    creates = files(dirs, "pump/CreateEvent")
    if creates:
        parts.append(f"""
      SELECT 'create', slot, tx_index, outer_ix, inner_ix, signature, mint, "user",
        NULL, NULL, NULL, NULL,
        virtual_token_reserves{H}, COALESCE(virtual_quote_reserves, virtual_sol_reserves){H}, real_token_reserves{H}, 0{H},
        decode_status
      FROM {src(creates)}""")
    mayhem = files(dirs, "pump/UpdateMayhemVirtualParamsEvent")
    if mayhem:
        parts.append(f"""
      SELECT 'mayhem_update', slot, tx_index, outer_ix, inner_ix, signature, mint, NULL,
        virtual_token_reserves{H}, virtual_sol_reserves{H}, real_token_reserves{H}, real_sol_reserves{H},
        new_virtual_token_reserves{H}, new_virtual_sol_reserves{H}, real_token_reserves{H}, real_sol_reserves{H},
        decode_status
      FROM {src(mayhem)}""")
    con.execute("CREATE TEMP TABLE pump AS WITH ev AS (" + " UNION ALL ".join(parts) + """)
      SELECT *, LAG(post_vt) OVER w AS prev_vt, LAG(post_vq) OVER w AS prev_vq,
             LAG(post_rt) OVER w AS prev_rt, LAG(post_rq) OVER w AS prev_rq,
             LAG(kind) OVER w AS prev_kind, LAG(actor) OVER w AS prev_actor, LAG(slot) OVER w AS prev_slot,
             ROW_NUMBER() OVER w AS n
      FROM ev WINDOW w AS (PARTITION BY mint ORDER BY slot, tx_index, outer_ix, inner_ix)""")
    res = {"events": dict(con.execute("SELECT kind, count(*) FROM pump GROUP BY 1").fetchall()),
           "decode_status": dict(con.execute("SELECT decode_status, count(*) FROM pump GROUP BY 1").fetchall())}
    res.update(pair_stats(con, "pump", "pre_vt = prev_vt AND pre_rt = prev_rt AND pre_rq = prev_rq",
                          ["pre_vt", "prev_vt", "pre_rt", "prev_rt", "pre_rq", "prev_rq"], "mint"))
    vq = con.execute(f"""
      SELECT count(*) FILTER (WHERE pre_vq = prev_vq), count(*) FILTER (WHERE pre_vq <> prev_vq)
      FROM pump WHERE n > 1 AND COALESCE(actor, '') <> '{MAYHEM_AGENT}' AND COALESCE(prev_actor, '') <> '{MAYHEM_AGENT}'""").fetchone()
    res["virtual_quote_without_mayhem_agent"] = {"ok": vq[0], "bad": vq[1]}
    cols = ["kind", "prev_kind", "mint", "slot", "prev_slot", "signature", "pre_vt", "prev_vt", "pre_rt", "prev_rt", "pre_rq", "prev_rq"]
    res["bad_examples"] = [dict(zip(cols, row)) for row in con.execute(f"""
      SELECT {', '.join(cols)} FROM pump_pairs WHERE verdict = 'bad' ORDER BY slot LIMIT {examples}""").fetchall()]
    return res


def amm_check(con, dirs, examples):
    eff = f"(pool_quote_token_reserves{H} + COALESCE(TRY_CAST(virtual_quote_reserves AS HUGEINT), 0))"
    sources = {
        "buy": ("pump_amm/BuyEvent", f"""pool_base_token_reserves{H} AS b, {eff} AS e,
               pool_base_token_reserves{H} - base_amount_out{H} AS b2, {eff} + quote_amount_in_with_lp_fee{H} AS e2"""),
        "sell": ("pump_amm/SellEvent", f"""pool_base_token_reserves{H} AS b, {eff} AS e,
               pool_base_token_reserves{H} + base_amount_in{H} AS b2, {eff} - quote_amount_out_without_lp_fee{H} AS e2"""),
        "deposit": ("pump_amm/DepositEvent", f"""pool_base_token_reserves{H} AS b, NULL::HUGEINT AS e,
               pool_base_token_reserves{H} + base_amount_in{H} AS b2, NULL::HUGEINT AS e2"""),
        "withdraw": ("pump_amm/WithdrawEvent", f"""pool_base_token_reserves{H} AS b, NULL::HUGEINT AS e,
               pool_base_token_reserves{H} - base_amount_out{H} AS b2, NULL::HUGEINT AS e2"""),
        "create_pool": ("pump_amm/CreatePoolEvent", f"""NULL::HUGEINT AS b, NULL::HUGEINT AS e,
               pool_base_amount{H} AS b2, pool_quote_amount{H} AS e2"""),
        "boost_buy_and_burn": ("pump_amm/BoostBuyAndBurnEvent", f"""NULL::HUGEINT AS b, NULL::HUGEINT AS e,
               base_reserves_after{H} AS b2, real_quote_reserves_after{H} + TRY_CAST(virtual_quote_reserves AS HUGEINT) AS e2"""),
        "init_boost": ("pump_amm/InitBoostEvent", """NULL::HUGEINT AS b, NULL::HUGEINT AS e,
               NULL::HUGEINT AS b2, NULL::HUGEINT AS e2"""),
    }
    parts = []
    for kind, (table, expr) in sources.items():
        fs = files(dirs, table)
        if fs:
            parts.append(f"""SELECT '{kind}' AS kind, slot, tx_index, outer_ix, inner_ix, signature, pool, {expr}
                             FROM {src(fs)}""")
    if not any(p.startswith("SELECT 'buy'") or p.startswith("SELECT 'sell'") for p in parts):
        return None
    con.execute("CREATE TEMP TABLE amm AS WITH ev AS (" + " UNION ALL BY NAME ".join(parts) + """)
      SELECT *, LAG(b2) OVER w AS prev_b2, LAG(e2) OVER w AS prev_e2, LAG(kind) OVER w AS prev_kind,
             LAG(slot) OVER w AS prev_slot, ROW_NUMBER() OVER w AS n
      FROM ev WINDOW w AS (PARTITION BY pool ORDER BY slot, tx_index, outer_ix, inner_ix)""")
    res = {"events": dict(con.execute("SELECT kind, count(*) FROM amm GROUP BY 1").fetchall())}
    # Base reserve on every link; effective quote only where both sides expose it.
    res["base"] = pair_stats(con, "amm", "b = prev_b2", ["b", "prev_b2"], "pool")
    res["base_and_effective_quote"] = pair_stats(con, "amm", "b = prev_b2 AND e = prev_e2",
                                                 ["b", "prev_b2", "e", "prev_e2"], "pool")
    cols = ["kind", "prev_kind", "pool", "slot", "prev_slot", "signature", "b", "prev_b2", "e", "prev_e2"]
    res["bad_examples"] = [dict(zip(cols, row)) for row in con.execute(f"""
      SELECT {', '.join(cols)} FROM amm_pairs WHERE verdict = 'bad' ORDER BY slot LIMIT {examples}""").fetchall()]
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--expect", help="START:END slot range the dataset should cover")
    ap.add_argument("--report")
    ap.add_argument("--examples", type=int, default=5)
    a = ap.parse_args()

    done, other = scan_chunks(a.root)
    expect = tuple(map(int, a.expect.split(":"))) if a.expect else None
    report = {
        "complete_chunks": len(done),
        "other_chunks": other,
        "uncovered_slot_ranges": coverage_holes(done, expect),
    }
    dirs = [c[3] for c in done]
    con = duckdb.connect()
    con.execute("SET threads TO 4")
    con.execute("SET memory_limit = '6GB'")
    con.execute("CREATE TEMP TABLE chunks (s UBIGINT, e UBIGINT)")
    if done:
        con.executemany("INSERT INTO chunks VALUES (?, ?)", [(s, e) for s, e, _, _ in done])

    pump = pump_check(con, dirs, a.examples)
    if pump:
        report["pump"] = pump
    amm = amm_check(con, dirs, a.examples)
    if amm:
        report["pump_amm"] = amm
    anomalies = files(dirs, "anomalies")
    if anomalies:
        report["anomalies"] = {f"{p}/{k}/{e}": c for p, k, e, c in con.execute(f"""
          SELECT program, kind, event, count(*) FROM read_parquet({lit(anomalies)}) GROUP BY ALL ORDER BY 4 DESC""").fetchall()}

    out = json.dumps(report, indent=2, default=str)
    if a.report:
        with open(a.report, "w") as f:
            f.write(out)
    print(out)


if __name__ == "__main__":
    sys.exit(main())
