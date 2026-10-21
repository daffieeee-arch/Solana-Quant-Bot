"""Venue census, step 0 of the other venues (owner 2026-10-21; coordinator design, approved): per venue and UTC day from
the extractor's venue_census table (LAB_VENUE_CENSUS=1; lab/extractor/src/extract.rs VENUE_CENSUS_COLS) and the chunks'
blocks. Descriptive, dev data only (never hold-out 1047-1051 or clean 1055-1063). Nothing is sent or traded.

  python -m wallets.venue_census --chunks <events/census-v1/chunks> --epoch 1045 --out <dir> [--memory 6GB] [--threads 4]
  python -m wallets.venue_census --chunks <smoke chunk root> --from LO --to HI --out <dir>   (a smoke's slot range)

Definitions (v1; v0 counted same-pool round trips as arbs, 1045's sanity check):
- venue: a DEX label of the row's venues (pump, pump_amm, raydium_*, meteora_*, orca_whirlpool). A transaction that
  invokes k venues counts once in each venue's transactions and 1/k in the venue's sums (fees, tips, MEV).
- swap: a DEX instruction in SWAP_NAMES (dex_ixs "label:name:pool"): the venues' swap, buy and sell instructions by
  their IDL names. Not LaunchLab's migrate_to_cpswap, DLMM's set_pre_activation_swap_address, pump's
  update_buyback_config or PumpSwap's boost_buy_and_burn (a name pattern took the first two).
- volume proxy: per successful transaction with one venue and a swap, |signer SOL delta + its WSOL delta + fee + tip|
  in SOL: the fee payer's SOL flow without its costs. Rent of new token accounts stays in; a swap that moves no SOL
  (USDC against a token) counts ~0. A proxy, not a decoded amount.
- launches, graduations, pool creations: successful transactions with an instruction in LAUNCH, GRADUATION or
  POOL_CREATION (by name, per venue).
- arb (v1; mev0's closed loop at the token-delta level, with E17's lesson): a successful transaction whose DEX
  instructions are all swaps (no liquidity, claim or close instruction: E17's unexplained "profit" was mostly LP
  withdrawals), at least two of them over at least two distinct pools, the fee payer flat in every mint but WSOL, and
  net = signer SOL delta + WSOL delta > 0 (fee and tip paid already). Its net counts 1/k per venue. Over rent: the arbs
  with net > RENT (one token account's rent: a closed account refunds it, and closes are not in the census), the
  conservative line. Round trips: the same with one pool (v0 counted them; on 1045 their average net was one rent):
  volume bots, reported apart.
- memecoin share: of a venue's successful swap transactions, those touching a mint outside MAJORS (non-major) and those
  touching a mint ending in pump or bonk (launchpad mint).
- backrun: an arb whose first swap's pool had a swap by another fee payer of at least BACKRUN_MIN_SOL (that
  transaction's volume proxy, any venue count) in the BACKRUN_SLOTS slots before it (same slot: earlier tx_index).
- failed attempts: failed transactions of fee payers with an arb in the window; cost = their fees, 1/k per venue.
Outputs in --out: venue_day.parquet (the table per venue and day, with the day's hours covered), venue_census.json and
venue_census.md (per-day columns = totals / covered days: hours with blocks / 24, so partial days are not inflated).
"""

import argparse
import glob
import json
import os
import shutil

import duckdb

WSOL = "So11111111111111111111111111111111111111112"
EPOCH_SLOTS = 432_000
KEY = 1 << 20  # (slot, tx_index) -> slot * KEY + tx_index
BACKRUN_MIN_SOL = 1.0
BACKRUN_SLOTS = 2
# One token account's rent-exempt lamports (165 bytes): a same-pool round trip that closes the account gets it back.
RENT = 2_039_280
# The swap instructions of every venue (src/venue_ix.rs and AMM_V4_IXS names; test_swap_names_are_venue_instructions).
SWAP_NAMES = ("swap", "swap2", "swap_v2", "swap2_with_transfer_hook", "swap_exact_out", "swap_exact_out2",
              "swap_with_price_impact", "swap_with_price_impact2", "two_hop_swap", "two_hop_swap_v2", "swap_router_base_in",
              "swap_base_input", "swap_base_output", "swap_base_in", "swap_base_out", "swap_base_in_v2", "swap_base_out_v2",
              "buy", "buy_v2", "buy_v3", "buy_exact_sol_in", "buy_exact_quote_in", "buy_exact_quote_in_v2",
              "buy_exact_quote_in_v3", "buy_exact_in", "buy_exact_out", "sell", "sell_v2", "sell_v3", "sell_exact_in",
              "sell_exact_out", "multi_hop_swap", "multi_hop_curve_swap")
# Majors for the memecoin-share check: WSOL, USDC, USDT, JitoSOL, mSOL, bSOL, JUP (the canonical mints).
MAJORS = (WSOL, "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
          "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn", "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",
          "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1", "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN")
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
    """days (slot -> block time and UTC day, small) and the view vc over the census parquet with the derived columns.
    Nothing per transaction is materialized: an epoch has ~66 M rows (99zzzzn), and each query below streams over vc
    (DuckDB keeps only the columns a query reads)."""
    files = chunk_files(chunks_root, "venue_census", lo, hi)
    blocks = chunk_files(chunks_root, "blocks", lo, hi)
    if not files or not blocks:
        raise SystemExit(f"no venue_census or blocks for [{lo}, {hi}) under {chunks_root}")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE days AS
        SELECT slot, any_value(block_time)::BIGINT AS bt, epoch_ms(any_value(block_time)::BIGINT * 1000)::DATE AS day
        FROM read_parquet({_lit(blocks)}, union_by_name = true)
        WHERE slot >= {lo} AND slot < {hi} AND block_time IS NOT NULL GROUP BY slot ORDER BY slot""")
    tok = "string_split(COALESCE(c.signer_token_deltas, ''), ';')"
    swap = "(split_part(x, ':', 2) IN (" + ", ".join(f"'{n}'" for n in SWAP_NAMES) + "))"
    con.execute(f"""CREATE OR REPLACE TEMP VIEW vc AS
        SELECT c.slot, c.tx_index, c.slot * {KEY} + c.tx_index AS k, c.failed, c.fee_payer, c.tx_fee, c.priority_fee, c.jito_tip,
               c.venues, c.n_dex, c.top_programs, c.dex_ixs, c.n_dex_ixs, c.signer_sol_delta, c.mints, d.day,
               COALESCE(TRY_CAST(split_part(list_filter({tok}, x -> starts_with(x, '{WSOL}:'))[1], ':', 2) AS HUGEINT), 0) AS wsol,
               len(list_filter({tok}, x -> x <> '' AND NOT starts_with(x, '{WSOL}:'))) AS other_tokens,
               list_filter(string_split(c.dex_ixs, ';'), x -> {swap}) AS swaps
        FROM read_parquet({_lit(files)}, union_by_name = true) c JOIN days d USING (slot)
        WHERE c.slot >= {lo} AND c.slot < {hi}""")
    con.execute(f"""CREATE OR REPLACE TEMP VIEW txs AS
        SELECT *, len(swaps) AS n_swaps, split_part(swaps[1], ':', 3) AS first_pool,
               len(list_distinct(list_transform(swaps, x -> split_part(x, ':', 3)))) AS n_swap_pools,
               abs(signer_sol_delta + wsol + tx_fee + jito_tip) / 1e9 AS size_sol, (signer_sol_delta + wsol) AS net
        FROM vc""")
    _kinds(con)


def venue_day(con):
    """The table per venue and day. Materialized: the arbs (few) and the large swaps on their first pools (few); the
    rest streams."""
    con.execute("""CREATE OR REPLACE TEMP TABLE arbs AS
        SELECT k, slot, day, fee_payer, venues, n_dex, net, first_pool FROM txs
        WHERE NOT failed AND n_swaps >= 2 AND n_swap_pools >= 2 AND n_swaps = n_dex_ixs AND other_tokens = 0 AND net > 0""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE big AS
        SELECT k, slot, fee_payer, pool FROM (
            SELECT k, slot, fee_payer, split_part(unnest(swaps), ':', 3) AS pool FROM txs
            WHERE NOT failed AND size_sol >= {BACKRUN_MIN_SOL} AND len(swaps) > 0)
        WHERE pool IN (SELECT first_pool FROM arbs WHERE first_pool <> '')""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE arbs2 AS
        SELECT a.*, EXISTS (SELECT 1 FROM big b WHERE b.pool = a.first_pool AND b.fee_payer <> a.fee_payer AND b.k < a.k
                            AND b.slot >= a.slot - {BACKRUN_SLOTS}) AS backrun
        FROM arbs a""")
    majors = ", ".join(f"'{m}'" for m in MAJORS)
    return con.execute(f"""
        WITH base AS (
            SELECT venue, day, count(*) AS txs, count(*) FILTER (WHERE failed) AS failed,
                   count(DISTINCT fee_payer) FILTER (WHERE NOT failed) AS signers,
                   count(*) FILTER (WHERE NOT failed AND n_swaps > 0) AS swap_txs,
                   sum(size_sol) FILTER (WHERE NOT failed AND n_dex = 1 AND n_swaps > 0) AS volume_sol_proxy,
                   avg((jito_tip > 0)::INT) FILTER (WHERE NOT failed) AS tip_share,
                   sum(jito_tip / n_dex) FILTER (WHERE NOT failed) / 1e9 AS tips_sol,
                   sum(priority_fee / n_dex) / 1e9 AS priority_fees_sol,
                   avg(non_major::INT) FILTER (WHERE NOT failed AND n_swaps > 0) AS non_major_share,
                   avg(launchpad_mint::INT) FILTER (WHERE NOT failed AND n_swaps > 0) AS launchpad_mint_share
            FROM (SELECT unnest(string_split(venues, ',')) AS venue, day, failed, fee_payer, n_swaps, size_sol, n_dex, jito_tip,
                         priority_fee,
                         len(list_filter(string_split(COALESCE(mints, ''), ';'), x -> x <> '' AND x NOT IN ({majors}))) > 0 AS non_major,
                         len(list_filter(string_split(COALESCE(mints, ''), ';'), x -> suffix(x, 'pump') OR suffix(x, 'bonk'))) > 0
                             AS launchpad_mint
                  FROM txs)
            GROUP BY venue, day),
        kinds AS (
            SELECT i.venue, i.day, count(DISTINCT i.k) FILTER (WHERE kd.kind = 'launch') AS launches,
                   count(DISTINCT i.k) FILTER (WHERE kd.kind = 'graduation') AS graduations,
                   count(DISTINCT i.k) FILTER (WHERE kd.kind = 'pool_creation') AS pool_creations,
                   count(DISTINCT i.pool) FILTER (WHERE kd.kind = 'launch' AND i.pool <> '') AS launch_pools,
                   count(DISTINCT i.pool) FILTER (WHERE kd.kind = 'graduation' AND i.pool <> '') AS graduation_pools
            FROM (SELECT k, day, split_part(x, ':', 1) AS venue, split_part(x, ':', 2) AS name, split_part(x, ':', 3) AS pool
                  FROM (SELECT k, day, unnest(string_split(dex_ixs, ';')) AS x FROM vc WHERE NOT failed)) i
            JOIN kinds kd ON kd.venue = i.venue AND kd.name = i.name
            GROUP BY i.venue, i.day),
        mev AS (
            SELECT venue, day, count(*) AS arbs, sum(net / n_dex) / 1e9 AS arb_net_sol, count(DISTINCT fee_payer) AS arb_bots,
                   count(*) FILTER (WHERE backrun) AS backruns, sum(net / n_dex) FILTER (WHERE backrun) / 1e9 AS backrun_net_sol,
                   count(*) FILTER (WHERE net > {RENT}) AS arbs_over_rent, sum(net / n_dex) FILTER (WHERE net > {RENT}) / 1e9 AS arb_net_over_rent_sol
            FROM (SELECT a.*, unnest(string_split(a.venues, ',')) AS venue FROM arbs2 a) GROUP BY venue, day),
        roundtrips AS (
            SELECT venue, day, count(*) AS roundtrips, sum(net / n_dex) / 1e9 AS roundtrip_net_sol, count(DISTINCT fee_payer) AS roundtrip_payers
            FROM (SELECT unnest(string_split(venues, ',')) AS venue, day, net, n_dex, fee_payer FROM txs
                  WHERE NOT failed AND n_swaps >= 2 AND n_swap_pools = 1 AND n_swaps = n_dex_ixs AND other_tokens = 0)
            GROUP BY venue, day),
        attempts AS (
            SELECT venue, day, count(*) AS failed_attempts, sum(tx_fee / n_dex) / 1e9 AS failed_attempt_cost_sol
            FROM (SELECT unnest(string_split(venues, ',')) AS venue, day, tx_fee, n_dex FROM vc
                  WHERE failed AND fee_payer IN (SELECT DISTINCT fee_payer FROM arbs2))
            GROUP BY venue, day),
        cov AS (SELECT day, (max(bt) - min(bt) + 1) / 3600.0 AS hours FROM days GROUP BY day)
        SELECT b.*, cov.hours, 1 - b.failed / b.txs AS success_rate, COALESCE(k.launches, 0) AS launches,
               COALESCE(k.graduations, 0) AS graduations, COALESCE(k.pool_creations, 0) AS pool_creations,
               COALESCE(m.arbs, 0) AS arbs, COALESCE(m.arb_net_sol, 0) AS arb_net_sol, COALESCE(m.arb_bots, 0) AS arb_bots,
               COALESCE(m.backruns, 0) AS backruns, COALESCE(m.backrun_net_sol, 0) AS backrun_net_sol,
               COALESCE(m.arbs_over_rent, 0) AS arbs_over_rent, COALESCE(m.arb_net_over_rent_sol, 0) AS arb_net_over_rent_sol,
               COALESCE(r.roundtrips, 0) AS roundtrips, COALESCE(r.roundtrip_net_sol, 0) AS roundtrip_net_sol,
               COALESCE(r.roundtrip_payers, 0) AS roundtrip_payers,
               COALESCE(k.launch_pools, 0) AS launch_pools, COALESCE(k.graduation_pools, 0) AS graduation_pools,
               COALESCE(a.failed_attempts, 0) AS failed_attempts, COALESCE(a.failed_attempt_cost_sol, 0) AS failed_attempt_cost_sol
        FROM base b JOIN cov USING (day) LEFT JOIN kinds k USING (venue, day) LEFT JOIN mev m USING (venue, day)
        LEFT JOIN roundtrips r USING (venue, day) LEFT JOIN attempts a USING (venue, day)
        ORDER BY venue, day""").df()


def unknown_programs(con, n=20):
    """The top-level programs without a label (by id) in the census transactions: other venues, routers and bots."""
    return con.execute(f"""SELECT p AS program, count(*) AS txs, count(*) FILTER (WHERE NOT failed) AS ok
        FROM (SELECT failed, unnest(string_split(top_programs, ';')) AS p FROM vc WHERE top_programs IS NOT NULL)
        WHERE length(p) >= 32 GROUP BY p ORDER BY txs DESC LIMIT {n}""").fetchall()


def coverage(con, lo, hi):
    """Per UTC day of the window: slots with a block, first and last slot, and the hours covered (the block time span;
    a day at an epoch's edge, or a smoke's minutes, is partial)."""
    rows = con.execute("""SELECT day, count(*) AS slots, min(slot), max(slot), (max(bt) - min(bt) + 1) / 3600.0
                          FROM days GROUP BY day ORDER BY day""").fetchall()
    return [{"day": str(d), "slots_with_block": n, "first": a, "last": b, "hours": h} for d, n, a, b, h in rows]


def report(df, unknown, cov, lo, hi):
    sums = ["txs", "failed", "swap_txs", "volume_sol_proxy", "launches", "graduations", "pool_creations", "launch_pools",
            "graduation_pools", "tips_sol", "priority_fees_sol", "arbs", "arb_net_sol", "arbs_over_rent", "arb_net_over_rent_sol",
            "backruns", "backrun_net_sol", "roundtrips", "roundtrip_net_sol", "failed_attempts", "failed_attempt_cost_sol"]
    d = df.copy()
    for c in ("non_major_share", "launchpad_mint_share"):  # weighted by the venue-day's successful swap transactions
        d[c + "_w"] = d[c].fillna(0) * d["swap_txs"]
    tot = d.groupby("venue")[sums + ["non_major_share_w", "launchpad_mint_share_w"]].sum()
    for c in ("non_major_share", "launchpad_mint_share"):
        tot[c] = tot[c + "_w"] / tot["swap_txs"].where(tot["swap_txs"] > 0)
    hours = sum(c["hours"] for c in cov)
    days = hours / 24 if hours else 1
    tot = tot.sort_values("txs", ascending=False)
    pct = lambda x: "n/a" if x != x else f"{x:.0%}"
    lines = [f"# Venue census, slots [{lo}, {hi})", "",
             f"Covered: {hours:,.2f} h over {len(cov)} UTC day(s) (block time span per day, coverage below). Per-day columns "
             f"are totals / {days:,.4f} covered days, not per calendar day: a partial edge day or a smoke's minutes count "
             "by their hours. Launches, graduations and pool creations are totals (transactions, and distinct pools). "
             "Definitions (v1) in wallets/venue_census.py. Pump reference: E17/E43 (experiment register).", "",
             "## Activity", "",
             "The volume proxy is for ranking only: on 1045's 29 Sep it was 57 % of the decoded PumpSwap SOL volume and 21 % of "
             "the curve's (round trips in one transaction net out). Non-major: successful swaps touching a mint other than "
             "WSOL, USDC, USDT, JitoSOL, mSOL, bSOL, JUP; launchpad mint: a mint ending in pump or bonk.", "",
             "| venue | txs/day | success | swap txs/day | volume proxy SOL/day | non-major | launchpad mint | launches (pools) | "
             "graduations (pools) | pool creations | tips SOL/day | prio fees SOL/day |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for v, r in tot.iterrows():
        lines.append(f"| {v} | {r.txs / days:,.0f} | {1 - r.failed / r.txs:.1%} | {r.swap_txs / days:,.0f} | "
                     f"{(r.volume_sol_proxy or 0) / days:,.0f} | {pct(r.non_major_share)} | {pct(r.launchpad_mint_share)} | "
                     f"{int(r.launches):,} ({int(r.launch_pools):,}) | {int(r.graduations):,} ({int(r.graduation_pools):,}) | "
                     f"{int(r.pool_creations):,} | {r.tips_sol / days:,.2f} | {r.priority_fees_sol / days:,.2f} |")
    lines += ["", "## MEV v1 (an upper bound)", "",
              "Arb: a successful transaction whose DEX instructions are all swaps, at least two, over at least two distinct pools, "
              "the fee payer flat in every mint but WSOL, net > 0 after fee and tip; 1/k per venue. Over rent: net above one "
              "token account's rent (0.00204 SOL), the conservative line (closes are not visible). Round trips: the same with "
              "one pool (buy and sell back): volume bots, not arbitrage, kept apart. Failed attempt cost: fees of failed "
              "transactions of payers with an arb (indicative).", "",
              "| venue | arbs/day | arb net SOL/day | over-rent net SOL/day | backrun share | round trips/day | round-trip net SOL/day | "
              "failed attempt cost SOL/day |",
              "|---|---|---|---|---|---|---|---|"]
    for v, r in tot.sort_values("arb_net_over_rent_sol", ascending=False).iterrows():
        bs = r.backrun_net_sol / r.arb_net_sol if r.arb_net_sol else 0
        lines.append(f"| {v} | {r.arbs / days:,.0f} | {r.arb_net_sol / days:,.2f} | {r.arb_net_over_rent_sol / days:,.2f} | {bs:.0%} | "
                     f"{r.roundtrips / days:,.0f} | {r.roundtrip_net_sol / days:,.2f} | {r.failed_attempt_cost_sol / days:,.2f} |")
    lines += ["", "## Unlabelled top-level programs (by transactions)", "", "| program | txs | succeeded |", "|---|---|---|"]
    lines += [f"| {p} | {n:,} | {ok:,} |" for p, n, ok in unknown]
    lines += ["", "## Coverage", "", "| day | hours covered | slots with a block | first | last |", "|---|---|---|---|---|"]
    lines += [f"| {c['day']} | {c['hours']:,.2f} | {c['slots_with_block']:,} | {c['first']} | {c['last']} |" for c in cov]
    return "\n".join(lines) + "\n", tot.drop(columns=["non_major_share_w", "launchpad_mint_share_w"])


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
    spill = os.path.join(a.out, "duckdb-spill")
    con.execute(f"SET memory_limit = '{a.memory}'; SET threads = {a.threads}; SET preserve_insertion_order = false; "
                f"SET temp_directory = '{spill}'")
    setup(con, a.chunks, lo, hi)
    df = venue_day(con)
    unknown = unknown_programs(con)
    cov = coverage(con, lo, hi)
    md, tot = report(df, unknown, cov, lo, hi)
    covered_days = sum(c["hours"] for c in cov) / 24
    df.to_parquet(os.path.join(a.out, "venue_day.parquet"), index=False)
    with open(os.path.join(a.out, "venue_census.md"), "w") as f:
        f.write(md)
    summary = {"slots": [lo, hi], "rows": con.execute("SELECT count(*) FROM vc").fetchone()[0], "covered_days": covered_days,
               "venues": json.loads(tot.reset_index().to_json(orient="records")), "unknown_programs": unknown, "coverage": cov,
               "definitions": {"backrun_min_sol": BACKRUN_MIN_SOL, "backrun_slots": BACKRUN_SLOTS}}
    with open(os.path.join(a.out, "venue_census.json"), "w") as f:
        json.dump(summary, f, indent=1, default=str)
    con.close()
    shutil.rmtree(spill, ignore_errors=True)
    print(md)


if __name__ == "__main__":
    main()
