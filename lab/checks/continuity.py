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

Scale: the projection is first written to an on-disk DuckDB file, then chains are checked per
hash bucket of mint/pool (a chain never spans buckets, so the result is exact). Duplicates from
firehose restarts are removed on the integer event position (slot, tx_index, outer_ix, inner_ix).

Usage: python continuity.py <chunks_dir> [--expect START:END] [--report out.json] [--examples N]
Env: LAB_DUCKDB_THREADS, LAB_DUCKDB_MEM, LAB_DUCKDB_TMP (directory for the work database).
Only chunks whose `_manifest.json` has status "complete" are used.
"""

import argparse
import glob
import json
import os
import sys
import tempfile
from collections import Counter

import duckdb

MAYHEM_AGENT = "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s"
BUCKETS = int(os.environ.get("LAB_CONTINUITY_BUCKETS", "16"))
H = "::HUGEINT"
POS = "slot, tx_index, outer_ix, inner_ix"


def scan_chunks(root):
    done, other = [], []
    for d in sorted(glob.glob(os.path.join(root, "*-*"))):
        m = os.path.join(d, "_manifest.json")
        if not os.path.isfile(m):
            other.append({"dir": os.path.basename(d), "status": "no_manifest"})
            continue
        with open(m) as f:
            man = json.load(f)
        if man["status"] == "complete":
            done.append((man["slot_start"], man["slot_end_exclusive"], man["status"], d))
        else:
            other.append({"dir": os.path.basename(d), "status": man["status"]})
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


def rp(paths):
    return f"read_parquet({lit(paths)}, union_by_name = true)"


class Pairs:
    """Accumulates pair verdicts over buckets."""

    def __init__(self):
        self.by_kind = Counter()
        self.by_chunk = Counter()
        self.bad_gaps = []
        self.examples = []
        self.chains = 0
        self.anchored = 0

    def add_bucket(self, con, table, ok_expr, cmp_cols, key, examples, example_cols):
        null_any = " OR ".join(f"{c} IS NULL" for c in cmp_cols)
        verdict = f"""CASE WHEN {null_any} THEN 'unverifiable' WHEN {ok_expr} THEN 'ok' ELSE 'bad' END"""
        for k, pk, v, c in con.execute(f"""
            SELECT kind, prev_kind, {verdict} AS v, count(*) FROM {table} WHERE n > 1 GROUP BY ALL""").fetchall():
            self.by_kind[(k, pk, v)] += c
        for s, e, c in con.execute(f"""
            SELECT c.chunk_s, c.chunk_e, count(*) FROM {table} p JOIN chunks c ON p.slot >= c.chunk_s AND p.slot < c.chunk_e
            WHERE n > 1 AND {verdict} = 'bad' GROUP BY ALL""").fetchall():
            self.by_chunk[f"{s}-{e}"] += c
        self.bad_gaps += [r[0] for r in con.execute(f"""
            SELECT slot - prev_slot FROM {table} WHERE n > 1 AND {verdict} = 'bad'""").fetchall()]
        if len(self.examples) < examples:
            cols = ", ".join(example_cols)
            self.examples += [dict(zip(example_cols, r)) for r in con.execute(f"""
                SELECT {cols} FROM {table} WHERE n > 1 AND {verdict} = 'bad' ORDER BY slot
                LIMIT {examples - len(self.examples)}""").fetchall()]
        t, a = con.execute(f"""SELECT count(DISTINCT {key}),
            count(DISTINCT {key}) FILTER (WHERE n = 1 AND kind LIKE 'create%') FROM {table}""").fetchone()
        self.chains += t
        self.anchored += a

    def report(self):
        tot = Counter()
        kinds = {}
        for (k, pk, v), c in self.by_kind.items():
            tot[v] += c
            kinds.setdefault(f"{k}<-{pk}", {"ok": 0, "bad": 0, "unverifiable": 0})[v] += c
        out = {"pairs": sum(tot.values()), "ok": tot["ok"], "bad": tot["bad"], "unverifiable": tot["unverifiable"]}
        out["ok_rate_of_verifiable"] = out["ok"] / max(1, out["ok"] + out["bad"])
        out["by_kind"] = dict(sorted(kinds.items(), key=lambda kv: (-kv[1]["bad"], -kv[1]["ok"])))
        out["bad_by_chunk"] = dict(sorted(self.by_chunk.items()))
        g = sorted(self.bad_gaps)
        out["bad_slot_gap_p50_p90_p99"] = [g[int(q * (len(g) - 1))] for q in (0.5, 0.9, 0.99)] if g else None
        out["chains"] = {"total": self.chains, "anchored": self.anchored}
        out["bad_examples"] = self.examples
        return out


def pump_check(con, dirs, examples):
    trades = files(dirs, "pump/TradeEvent")
    if not trades:
        return None
    quote_amt = "COALESCE(quote_amount, sol_amount)"
    vq, rq = "COALESCE(virtual_quote_reserves, virtual_sol_reserves)", "COALESCE(real_quote_reserves, real_sol_reserves)"
    parts = [f"""
      SELECT 'trade' AS kind, {POS}, mint, "user" AS actor,
        CASE WHEN is_buy THEN virtual_token_reserves{H} + token_amount{H} ELSE virtual_token_reserves{H} - token_amount{H} END AS pre_vt,
        CASE WHEN is_buy THEN {vq}{H} - {quote_amt}{H} ELSE {vq}{H} + {quote_amt}{H} END AS pre_vq,
        CASE WHEN is_buy THEN real_token_reserves{H} + token_amount{H} ELSE real_token_reserves{H} - token_amount{H} END AS pre_rt,
        CASE WHEN is_buy THEN {rq}{H} - {quote_amt}{H} ELSE {rq}{H} + {quote_amt}{H} END AS pre_rq,
        virtual_token_reserves{H} AS post_vt, {vq}{H} AS post_vq, real_token_reserves{H} AS post_rt, {rq}{H} AS post_rq,
        decode_status
      FROM {rp(trades)}"""]
    creates = files(dirs, "pump/CreateEvent")
    if creates:
        parts.append(f"""
      SELECT 'create', {POS}, mint, "user", NULL, NULL, NULL, NULL,
        virtual_token_reserves{H}, {vq}{H}, real_token_reserves{H}, 0{H}, decode_status
      FROM {rp(creates)}""")
    mayhem = files(dirs, "pump/UpdateMayhemVirtualParamsEvent")
    if mayhem:
        parts.append(f"""
      SELECT 'mayhem_update', {POS}, mint, NULL,
        virtual_token_reserves{H}, virtual_sol_reserves{H}, real_token_reserves{H}, real_sol_reserves{H},
        new_virtual_token_reserves{H}, new_virtual_sol_reserves{H}, real_token_reserves{H}, real_sol_reserves{H}, decode_status
      FROM {rp(mayhem)}""")
    con.execute(f"CREATE OR REPLACE TABLE pump_ev AS SELECT *, hash(mint) % {BUCKETS} AS b FROM ("
                + " UNION ALL ".join(parts) + ")")
    res = {"events": dict(con.execute("SELECT kind, count(*) FROM pump_ev GROUP BY 1").fetchall()),
           "decode_status": dict(con.execute("SELECT decode_status, count(*) FROM pump_ev GROUP BY 1").fetchall()),
           "duplicate_events": con.execute(f"SELECT count(*) - count(DISTINCT ({POS})) FROM pump_ev").fetchone()[0]}
    pairs = Pairs()
    vq_ok = vq_bad = 0
    for b in range(BUCKETS):
        con.execute(f"""CREATE OR REPLACE TEMP TABLE pb AS
          SELECT *, LAG(post_vt) OVER w AS prev_vt, LAG(post_vq) OVER w AS prev_vq,
                 LAG(post_rt) OVER w AS prev_rt, LAG(post_rq) OVER w AS prev_rq,
                 LAG(kind) OVER w AS prev_kind, LAG(actor) OVER w AS prev_actor, LAG(slot) OVER w AS prev_slot,
                 ROW_NUMBER() OVER w AS n
          FROM (SELECT * FROM pump_ev WHERE b = {b} QUALIFY row_number() OVER (PARTITION BY {POS}) = 1)
          WINDOW w AS (PARTITION BY mint ORDER BY {POS})""")
        pairs.add_bucket(con, "pb", "pre_vt = prev_vt AND pre_rt = prev_rt AND pre_rq = prev_rq",
                         ["pre_vt", "prev_vt", "pre_rt", "prev_rt", "pre_rq", "prev_rq"], "mint", examples,
                         ["kind", "prev_kind", "mint", "slot", "prev_slot", "tx_index", "pre_vt", "prev_vt",
                          "pre_rt", "prev_rt", "pre_rq", "prev_rq"])
        o, x = con.execute(f"""
          SELECT count(*) FILTER (WHERE pre_vq = prev_vq), count(*) FILTER (WHERE pre_vq <> prev_vq)
          FROM pb WHERE n > 1 AND COALESCE(actor, '') <> '{MAYHEM_AGENT}' AND COALESCE(prev_actor, '') <> '{MAYHEM_AGENT}'""").fetchone()
        vq_ok, vq_bad = vq_ok + o, vq_bad + x
    res.update(pairs.report())
    res["virtual_quote_without_mayhem_agent"] = {"ok": vq_ok, "bad": vq_bad}
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
            parts.append(f"SELECT '{kind}' AS kind, {POS}, pool, {expr} FROM {rp(fs)}")
    if not files(dirs, "pump_amm/BuyEvent") and not files(dirs, "pump_amm/SellEvent"):
        return None
    con.execute(f"CREATE OR REPLACE TABLE amm_ev AS SELECT *, hash(pool) % {BUCKETS} AS bk FROM ("
                + " UNION ALL BY NAME ".join(parts) + ")")
    res = {"events": dict(con.execute("SELECT kind, count(*) FROM amm_ev GROUP BY 1").fetchall()),
           "duplicate_events": con.execute(f"SELECT count(*) - count(DISTINCT ({POS})) FROM amm_ev").fetchone()[0]}
    base, both = Pairs(), Pairs()
    cols = ["kind", "prev_kind", "pool", "slot", "prev_slot", "tx_index", "b", "prev_b2", "e", "prev_e2"]
    for bk in range(BUCKETS):
        con.execute(f"""CREATE OR REPLACE TEMP TABLE ab AS
          SELECT *, LAG(b2) OVER w AS prev_b2, LAG(e2) OVER w AS prev_e2, LAG(kind) OVER w AS prev_kind,
                 LAG(slot) OVER w AS prev_slot, ROW_NUMBER() OVER w AS n
          FROM (SELECT * FROM amm_ev WHERE bk = {bk} QUALIFY row_number() OVER (PARTITION BY {POS}) = 1)
          WINDOW w AS (PARTITION BY pool ORDER BY {POS})""")
        base.add_bucket(con, "ab", "b = prev_b2", ["b", "prev_b2"], "pool", examples, cols)
        both.add_bucket(con, "ab", "b = prev_b2 AND e = prev_e2", ["b", "prev_b2", "e", "prev_e2"], "pool", examples, cols)
    res["base"] = base.report()
    res["base_and_effective_quote"] = both.report()
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
    work_dir = os.environ.get("LAB_DUCKDB_TMP") or tempfile.gettempdir()
    os.makedirs(work_dir, exist_ok=True)
    db_path = os.path.join(work_dir, f"continuity-{os.getpid()}.duckdb")
    con = duckdb.connect(db_path)
    try:
        con.execute(f"SET threads TO {int(os.environ.get('LAB_DUCKDB_THREADS', '4'))}")
        con.execute(f"SET memory_limit = '{os.environ.get('LAB_DUCKDB_MEM', '6GB')}'")
        con.execute(f"SET temp_directory = '{work_dir}'")
        con.execute("SET preserve_insertion_order = false")
        con.execute("CREATE TEMP TABLE chunks (chunk_s UBIGINT, chunk_e UBIGINT)")
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
              SELECT program, kind, event, count(*) FROM {rp(anomalies)} GROUP BY ALL ORDER BY 4 DESC""").fetchall()}
    finally:
        con.close()
        for suffix in ("", ".wal"):
            if os.path.exists(db_path + suffix):
                os.remove(db_path + suffix)

    out = json.dumps(report, indent=2, default=str)
    if a.report:
        with open(a.report, "w") as f:
            f.write(out)
    print(out)


if __name__ == "__main__":
    sys.exit(main())
