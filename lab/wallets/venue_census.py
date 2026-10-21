"""Venue census, step 0 of the other venues (owner 2026-10-21; coordinator design, approved): per venue and UTC day from
the extractor's venue_census table (LAB_VENUE_CENSUS=1; lab/extractor/src/extract.rs VENUE_CENSUS_COLS) and the chunks'
blocks. Descriptive, dev data only (never hold-out 1047-1051 or clean 1055-1063). Nothing is sent or traded.

  python -m wallets.venue_census --chunks <events/census-v1/chunks> --epoch 1045 --out <dir> [--memory 6GB] [--threads 4]
  python -m wallets.venue_census --chunks <smoke chunk root> --from LO --to HI --out <dir>   (a smoke's slot range)

Definitions (v0):
- venue: a DEX label of the row's venues (pump, pump_amm, raydium_*, meteora_*, orca_whirlpool). A transaction that
  invokes k venues counts once in each venue's transactions and 1/k in the venue's sums (fees, tips, MEV).
- swap: a DEX instruction named *swap*, buy* or sell* (dex_ixs "label:name:pool"); pump's boost_buy_and_burn is none.
- volume proxy: per successful transaction with one venue and a swap, |signer SOL delta + its WSOL delta + fee + tip|
  in SOL: the fee payer's SOL flow without its costs. Rent of new token accounts stays in; a swap that moves no SOL
  (USDC against a token) counts ~0. A proxy, not a decoded amount.
- launches, graduations, pool creations: successful transactions with an instruction in LAUNCH, GRADUATION or
  POOL_CREATION (by name, per venue).
- arb (v0; mev0's closed loop at the token-delta level, with E17's lesson): a successful transaction whose DEX
  instructions are all swaps (no liquidity, claim or close instruction: E17's unexplained "profit" was mostly LP
  withdrawals), at least two of them, the fee payer flat in every mint but WSOL, and net = signer SOL delta + WSOL delta
  > 0 (fee and tip paid already). Its net counts 1/k per venue.
- backrun: an arb whose first swap's pool had a swap by another fee payer of at least BACKRUN_MIN_SOL (that
  transaction's volume proxy, any venue count) in the BACKRUN_SLOTS slots before it (same slot: earlier tx_index).
- failed attempts: failed transactions of fee payers with an arb in the window; cost = their fees, 1/k per venue.
Outputs in --out: venue_day.parquet (the table per venue and day), venue_census.json and venue_census.md.
"""

import argparse
import glob
import json
import os

import duckdb

WSOL = "So11111111111111111111111111111111111111112"
EPOCH_SLOTS = 432_000
KEY = 1 << 20  # (slot, tx_index) -> slot * KEY + tx_index
BACKRUN_MIN_SOL = 1.0
BACKRUN_SLOTS = 2
# Per venue: the instructions that launch a token, graduate it, or create a pool (IDL names, src/venue_ix.rs).
LAUNCH = {
    "pump": ["create", "create_v2"],
    "raydium_launchlab": ["initialize", "initialize_v2", "initialize_with_token_2022"],
    "meteora_dbc": ["initialize_virtual_pool_with_spl_token", "initialize_virtual_pool_with_token2022",
                    "initialize_virtual_pool_with_token2022_transfer_hook"],
}
GRADUATION = {
    "pump": ["migrate", "migrate_v2"],
    "raydium_launchlab": ["migrate_to_amm", "migrate_to_cpswap"],
    "meteora_dbc": ["migrate_meteora_damm", "migration_damm_v2"],
}
POOL_CREATION = {
    "pump_amm": ["create_pool"],
    "raydium_amm_v4": ["initialize2"],
    "raydium_cpmm": ["initialize", "initialize_with_permission"],
    "raydium_clmm": ["create_pool", "create_customizable_pool"],
    "meteora_dlmm": ["initialize_lb_pair", "initialize_lb_pair2", "initialize_permission_lb_pair",
                     "initialize_customizable_permissionless_lb_pair", "initialize_customizable_permissionless_lb_pair2"],
    "meteora_damm_v1": ["initialize_permissionless_pool", "initialize_permissionless_pool_with_fee_tier",
                        "initialize_permissionless_constant_product_pool_with_config",
                        "initialize_permissionless_constant_product_pool_with_config2",
                        "initialize_customizable_permissionless_constant_product_pool", "initialize_permissioned_pool"],
    "meteora_damm_v2": ["initialize_pool", "initialize_customizable_pool", "initialize_pool_with_dynamic_config"],
    "orca_whirlpool": ["initialize_pool", "initialize_pool_v2", "initialize_pool_with_adaptive_fee"],
}
SWAP_SQL = "(name LIKE '%swap%' OR name LIKE 'buy%' OR name LIKE 'sell%')"


def chunk_files(chunks_root, table, lo, hi):
    """Parquet files of `table` in the complete chunks that overlap [lo, hi)."""
    out = []
    for m in sorted(glob.glob(os.path.join(chunks_root, "*", "_manifest.json"))):
        with open(m) as f:
            man = json.load(f)
        if man.get("status") == "complete" and man["slot_start"] < hi and lo < man["slot_end_exclusive"]:
            out += sorted(glob.glob(os.path.join(os.path.dirname(m), table, "*.parquet")))
    return out


def _lit(files):
    return "[" + ",".join("'" + f.replace("'", "''") + "'" for f in files) + "]"


def _kinds(con):
    rows = [(v, n, kind) for kind, m in (("launch", LAUNCH), ("graduation", GRADUATION), ("pool_creation", POOL_CREATION))
            for v, names in m.items() for n in names]
    con.execute("CREATE OR REPLACE TEMP TABLE kinds (venue VARCHAR, name VARCHAR, kind VARCHAR)")
    con.executemany("INSERT INTO kinds VALUES (?, ?, ?)", rows)


def setup(con, chunks_root, lo, hi):
    """vc (the census rows of [lo, hi) with derived columns), ix (one row per DEX instruction) and days."""
    files = chunk_files(chunks_root, "venue_census", lo, hi)
    blocks = chunk_files(chunks_root, "blocks", lo, hi)
    if not files or not blocks:
        raise SystemExit(f"no venue_census or blocks for [{lo}, {hi}) under {chunks_root}")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE days AS
        SELECT slot, epoch_ms(any_value(block_time)::BIGINT * 1000)::DATE AS day
        FROM read_parquet({_lit(blocks)}, union_by_name = true)
        WHERE slot >= {lo} AND slot < {hi} AND block_time IS NOT NULL GROUP BY slot ORDER BY slot""")
    tok = "string_split(COALESCE(signer_token_deltas, ''), ';')"
    con.execute(f"""CREATE OR REPLACE TEMP TABLE vc AS
        SELECT c.slot, c.tx_index, c.slot * {KEY} + c.tx_index AS k, c.failed, c.fee_payer, c.tx_fee, c.priority_fee,
               c.jito_tip, c.venues, c.n_dex, c.top_programs, c.dex_ixs, c.n_dex_ixs, c.signer_sol_delta, d.day,
               COALESCE(TRY_CAST(split_part(list_filter({tok}, x -> starts_with(x, '{WSOL}:'))[1], ':', 2) AS HUGEINT), 0) AS wsol,
               len(list_filter({tok}, x -> x <> '' AND NOT starts_with(x, '{WSOL}:'))) AS other_tokens
        FROM read_parquet({_lit(files)}, union_by_name = true) c JOIN days d USING (slot)
        WHERE c.slot >= {lo} AND c.slot < {hi} ORDER BY c.slot, c.tx_index""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE ix AS
        SELECT k, slot, tx_index, failed, fee_payer, day, n_dex, split_part(x, ':', 1) AS venue, split_part(x, ':', 2) AS name,
               split_part(x, ':', 3) AS pool, pos
        FROM (SELECT k, slot, tx_index, failed, fee_payer, day, n_dex, unnest(string_split(dex_ixs, ';')) AS x,
                     generate_subscripts(string_split(dex_ixs, ';'), 1) AS pos FROM vc WHERE dex_ixs <> '')""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE txs AS
        SELECT vc.*, COALESCE(s.n_swaps, 0) AS n_swaps, s.first_pool,
               abs(signer_sol_delta + wsol + tx_fee + jito_tip) / 1e9 AS size_sol,
               (signer_sol_delta + wsol) AS net
        FROM vc LEFT JOIN (SELECT k, count(*) FILTER (WHERE {SWAP_SQL}) AS n_swaps,
                                  arg_min(pool, pos) FILTER (WHERE {SWAP_SQL}) AS first_pool
                           FROM ix GROUP BY k) s USING (k)
        ORDER BY k""")
    _kinds(con)


def venue_day(con):
    """The table per venue and day."""
    con.execute(f"""CREATE OR REPLACE TEMP TABLE arbs AS
        SELECT t.*, EXISTS (SELECT 1 FROM txs b JOIN ix bi USING (k)
                            WHERE bi.pool = t.first_pool AND t.first_pool <> '' AND {SWAP_SQL.replace('name', 'bi.name')}
                              AND NOT b.failed AND b.fee_payer <> t.fee_payer AND b.size_sol >= {BACKRUN_MIN_SOL}
                              AND b.k < t.k AND b.slot >= t.slot - {BACKRUN_SLOTS}) AS backrun
        FROM txs t
        WHERE NOT t.failed AND t.n_swaps >= 2 AND t.n_swaps = t.n_dex_ixs AND t.other_tokens = 0 AND t.net > 0""")
    con.execute("""CREATE OR REPLACE TEMP TABLE tv AS
        SELECT t.*, unnest(string_split(t.venues, ',')) AS venue FROM txs t""")
    return con.execute("""
        WITH base AS (
            SELECT venue, day, count(*) AS txs, count(*) FILTER (WHERE failed) AS failed,
                   count(DISTINCT fee_payer) FILTER (WHERE NOT failed) AS signers,
                   count(*) FILTER (WHERE NOT failed AND n_swaps > 0) AS swap_txs,
                   sum(size_sol) FILTER (WHERE NOT failed AND n_dex = 1 AND n_swaps > 0) AS volume_sol_proxy,
                   avg((jito_tip > 0)::INT) FILTER (WHERE NOT failed) AS tip_share,
                   sum(jito_tip / n_dex) FILTER (WHERE NOT failed) / 1e9 AS tips_sol,
                   sum(priority_fee / n_dex) / 1e9 AS priority_fees_sol
            FROM tv GROUP BY venue, day),
        kinds AS (
            SELECT i.venue, i.day, count(DISTINCT i.k) FILTER (WHERE kd.kind = 'launch') AS launches,
                   count(DISTINCT i.k) FILTER (WHERE kd.kind = 'graduation') AS graduations,
                   count(DISTINCT i.k) FILTER (WHERE kd.kind = 'pool_creation') AS pool_creations
            FROM ix i JOIN kinds kd ON kd.venue = i.venue AND kd.name = i.name
            WHERE NOT i.failed GROUP BY i.venue, i.day),
        mev AS (
            SELECT venue, day, count(*) AS arbs, sum(net / n_dex) / 1e9 AS arb_net_sol, count(DISTINCT fee_payer) AS arb_bots,
                   count(*) FILTER (WHERE backrun) AS backruns, sum(net / n_dex) FILTER (WHERE backrun) / 1e9 AS backrun_net_sol
            FROM (SELECT a.*, unnest(string_split(a.venues, ',')) AS venue FROM arbs a) GROUP BY venue, day),
        attempts AS (
            SELECT venue, day, count(*) AS failed_attempts, sum(tx_fee / n_dex) / 1e9 AS failed_attempt_cost_sol
            FROM tv WHERE failed AND fee_payer IN (SELECT DISTINCT fee_payer FROM arbs) GROUP BY venue, day)
        SELECT b.*, 1 - b.failed / b.txs AS success_rate, COALESCE(k.launches, 0) AS launches,
               COALESCE(k.graduations, 0) AS graduations, COALESCE(k.pool_creations, 0) AS pool_creations,
               COALESCE(m.arbs, 0) AS arbs, COALESCE(m.arb_net_sol, 0) AS arb_net_sol, COALESCE(m.arb_bots, 0) AS arb_bots,
               COALESCE(m.backruns, 0) AS backruns, COALESCE(m.backrun_net_sol, 0) AS backrun_net_sol,
               COALESCE(a.failed_attempts, 0) AS failed_attempts, COALESCE(a.failed_attempt_cost_sol, 0) AS failed_attempt_cost_sol
        FROM base b LEFT JOIN kinds k USING (venue, day) LEFT JOIN mev m USING (venue, day) LEFT JOIN attempts a USING (venue, day)
        ORDER BY venue, day""").df()


def unknown_programs(con, n=20):
    """The top-level programs without a label (by id) in the census transactions: other venues, routers and bots."""
    return con.execute(f"""SELECT p AS program, count(*) AS txs, count(*) FILTER (WHERE NOT failed) AS ok
        FROM (SELECT failed, unnest(string_split(top_programs, ';')) AS p FROM txs WHERE top_programs IS NOT NULL)
        WHERE length(p) >= 32 GROUP BY p ORDER BY txs DESC LIMIT {n}""").fetchall()


def coverage(con, lo, hi):
    """Days with their slot share of the window (a day at an epoch's edge is partial)."""
    rows = con.execute("SELECT day, count(*) AS slots, min(slot), max(slot) FROM days GROUP BY day ORDER BY day").fetchall()
    return [{"day": str(d), "slots_with_block": n, "first": a, "last": b} for d, n, a, b in rows]


def report(df, unknown, cov, lo, hi):
    tot = df.groupby("venue").agg(txs=("txs", "sum"), failed=("failed", "sum"), swap_txs=("swap_txs", "sum"),
                                  volume_sol_proxy=("volume_sol_proxy", "sum"), launches=("launches", "sum"),
                                  graduations=("graduations", "sum"), pool_creations=("pool_creations", "sum"),
                                  tips_sol=("tips_sol", "sum"), priority_fees_sol=("priority_fees_sol", "sum"),
                                  arbs=("arbs", "sum"), arb_net_sol=("arb_net_sol", "sum"), backruns=("backruns", "sum"),
                                  backrun_net_sol=("backrun_net_sol", "sum"), failed_attempts=("failed_attempts", "sum"),
                                  failed_attempt_cost_sol=("failed_attempt_cost_sol", "sum"))
    days = max(1, df["day"].nunique())
    tot = tot.sort_values("txs", ascending=False)
    lines = [f"# Venue census, slots [{lo}, {hi})", "",
             f"{days} UTC day(s) in the window; per-day columns are means over them. A day at an epoch's edge is partial "
             "(coverage below). v0 definitions in wallets/venue_census.py. Pump reference: E17/E43 (experiment register).", "",
             "| venue | txs/day | success | swap txs/day | volume proxy SOL/day | launches | graduations | pool creations | "
             "tips SOL/day | prio fees SOL/day | arbs/day | arb net SOL/day | backrun share | failed attempt cost SOL/day |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for v, r in tot.iterrows():
        bs = r.backrun_net_sol / r.arb_net_sol if r.arb_net_sol else 0
        lines.append(f"| {v} | {r.txs / days:,.0f} | {1 - r.failed / r.txs:.1%} | {r.swap_txs / days:,.0f} | "
                     f"{(r.volume_sol_proxy or 0) / days:,.0f} | {r.launches:,} | {r.graduations:,} | {r.pool_creations:,} | "
                     f"{r.tips_sol / days:,.2f} | {r.priority_fees_sol / days:,.2f} | {r.arbs / days:,.0f} | "
                     f"{r.arb_net_sol / days:,.2f} | {bs:.0%} | {r.failed_attempt_cost_sol / days:,.2f} |")
    lines += ["", "## Unlabelled top-level programs (by transactions)", "", "| program | txs | succeeded |", "|---|---|---|"]
    lines += [f"| {p} | {n:,} | {ok:,} |" for p, n, ok in unknown]
    lines += ["", "## Coverage", "", "| day | slots with a block | first | last |", "|---|---|---|---|"]
    lines += [f"| {c['day']} | {c['slots_with_block']:,} | {c['first']} | {c['last']} |" for c in cov]
    return "\n".join(lines) + "\n", tot


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--chunks", required=True)
    ap.add_argument("--epoch", type=int)
    ap.add_argument("--from", dest="lo", type=int)
    ap.add_argument("--to", dest="hi", type=int)
    ap.add_argument("--out", required=True)
    ap.add_argument("--memory", default=os.environ.get("LAB_DUCKDB_MEMORY", "2GB"))
    ap.add_argument("--threads", type=int, default=2)
    a = ap.parse_args(argv)
    if a.epoch is not None:
        if a.epoch in range(1047, 1052) or a.epoch in range(1055, 1064):
            ap.error(f"epoch {a.epoch} is hold-out or clean: refused")
        lo, hi = a.epoch * EPOCH_SLOTS, (a.epoch + 1) * EPOCH_SLOTS
    elif a.lo is not None and a.hi is not None:
        lo, hi = a.lo, a.hi
    else:
        ap.error("give --epoch, or --from and --to")
    os.makedirs(a.out, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{a.memory}'; SET threads = {a.threads}; SET preserve_insertion_order = false")
    setup(con, a.chunks, lo, hi)
    df = venue_day(con)
    unknown = unknown_programs(con)
    cov = coverage(con, lo, hi)
    md, tot = report(df, unknown, cov, lo, hi)
    df.to_parquet(os.path.join(a.out, "venue_day.parquet"), index=False)
    with open(os.path.join(a.out, "venue_census.md"), "w") as f:
        f.write(md)
    summary = {"slots": [lo, hi], "rows": con.execute("SELECT count(*) FROM vc").fetchone()[0],
               "venues": json.loads(tot.reset_index().to_json(orient="records")), "unknown_programs": unknown, "coverage": cov,
               "definitions": {"backrun_min_sol": BACKRUN_MIN_SOL, "backrun_slots": BACKRUN_SLOTS}}
    with open(os.path.join(a.out, "venue_census.json"), "w") as f:
        json.dump(summary, f, indent=1, default=str)
    print(md)


if __name__ == "__main__":
    main()
