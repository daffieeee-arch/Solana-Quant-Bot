"""E22 addendum (spec E §3, week3-specs-exits-2026-10-15.md sha256 7372ba01...): holder concentration and transfer-fed
holdings of the token at the decision slot t, and the transfer-fed selling in the first 30 / 60 s after the entry, for
the F7 pool_reversed triggers inside a transfers extraction window (events/transfers-v1: epochs 1040 and 1045). The
owner ledger, the insiders and the pool legs are transfers.setup's (spec token-transfers v3, the ledger of job 815);
the triggers, the outcomes, the tables and the day bootstrap are latebloom's (spec B markout, research D survival).

Point-in-time: holdings at t are the ledger rows with slot <= t (the trigger transaction is complete at the decision);
received_t is the sum of a holder's non-trade transfers in at or before t (as Q3's recv: transfer / transfer_checked,
not in a trade); the post-entry windows count the non-insider sellers of the pool in (t + d, t + d + X] and, per
seller, the tokens it received by transfer at or before its first sell. Q3's first-hour fed share of the pool is
joined as a reference only (it looks past t). Only triggers whose mint was created inside the window have a ledger
(the others keep NULL features and are counted). Descriptive on used data: no model, no selection, no threshold.

  LAB_STORE=<dev2> PYTHONPATH=lab python -m wallets.latebloom_transfers --chunks <transfers chunks> --parts <f7rev parts> \\
      --markout <markout-f7rev.parquet> --survival <survival.parquet> --out DIR [--from-slot A --to-slot B] [--boot 2000]
"""

import argparse
import json
import os
import sys
import time

import pyarrow as pa

from . import latebloom, transfers
from .common import EPOCH_SLOTS, STORE, Clock, connect, git_commit, guard

POST_SECONDS = latebloom.POST_SECONDS
TRANSFER_KINDS = "('transfer', 'transfer_checked')"

FEATURES_T = [
    ("holders_t", "holders_t", "holders (owners with a positive balance, vaults excluded) at t"),
    ("top1_share_t", "top1_share_t", "largest holder's share of the held supply (sum of positive balances) at t"),
    ("top10_share_t", "top10_share_t", "top-10 holders' share of the held supply at t"),
    ("hhi_t", "hhi_t", "Herfindahl index of the holders' shares at t (1 = one holder)"),
    ("share_c_t", "share_c_t", "insiders' holdings / circulating at t (Q1's share_c: creator + buyers in the first 10 s; NULL when undefined)"),
    ("n_insiders", "n_insiders", "insiders of the mint (Q1)"),
    ("fed_share_t", "fed_share_t", "share of the held supply that its holders received by non-trade transfer at or before t (least(balance, received) per holder)"),
    ("fed_holder_share_t", "fed_holder_share_t", "share of the holders at t with a non-trade transfer in at or before t"),
    ("fed_holders_t", "fed_holders_t", "holders at t with a non-trade transfer in"),
]
FEATURES_POST = [(f"{n}_{x}", f"{n}_{x}", f"{d} in the first {x} s after the entry") for x in POST_SECONDS for n, d in [
    ("sellers", "non-insider wallets selling in the pool"),
    ("fed_sellers", "of those, sellers that received tokens by non-trade transfer at or before their first sell"),
    ("fed_sell_share", "transfer-fed share of the non-insider selling (least(sold, received) per seller / sold; NULL without a sell)")]]
FEATURES_REF = [("q3_fed_share_1h", "q3_fed_share_1h", "Q3: transfer-fed share of the non-insider selling in the pool's first hour (reference, not point-in-time)")]
FEATURES = FEATURES_T + FEATURES_POST + FEATURES_REF


def progress(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", file=sys.stderr, flush=True)


def build(con, clock, lo, hi):
    """feat_t (holdings at t), feat_post (sells in the post-entry windows) and q3 for the window's triggers."""
    con.execute("""CREATE OR REPLACE TEMP TABLE tw AS
        SELECT tr.pool, tr.t, tr.mint, o.entry_slot FROM trig tr JOIN outc o USING (pool, t) SEMI JOIN wmints w ON w.mint = tr.mint
        WHERE o.entry_slot IS NOT NULL""")
    progress(f"window triggers: {con.execute('SELECT count(*) FROM tw').fetchone()[0]} of {con.execute('SELECT count(*) FROM trig').fetchone()[0]} have a ledger")
    # holdings at t per (trigger, owner): the ledger up to and including t
    con.execute("""CREATE OR REPLACE TEMP TABLE hold AS
        SELECT tw.pool, tw.t, tw.mint, l.owner, sum(l.delta)::DOUBLE AS h, bool_or(l.vault) AS vault
        FROM tw JOIN ledger l ON l.mint = tw.mint AND l.slot <= tw.t GROUP BY 1, 2, 3, 4""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE recv AS
        SELECT mint, dest_owner AS owner, slot, amount::DOUBLE AS amount FROM tt
        WHERE kind IN {TRANSFER_KINDS} AND NOT in_trade AND dest_owner IS NOT NULL""")
    con.execute("""CREATE OR REPLACE TEMP TABLE holdr AS
        WITH hp AS (SELECT pool, t, mint, owner, h FROM hold WHERE NOT vault AND h > 0),
             rc AS (SELECT hp.pool, hp.t, hp.owner, sum(r.amount) AS received
                    FROM hp JOIN recv r ON r.mint = hp.mint AND r.owner = hp.owner AND r.slot <= hp.t GROUP BY 1, 2, 3)
        SELECT hp.pool, hp.t, hp.owner, hp.h, COALESCE(rc.received, 0) AS received FROM hp LEFT JOIN rc USING (pool, t, owner)""")
    con.execute("""CREATE OR REPLACE TEMP TABLE feat_t AS
        WITH ranked AS (SELECT *, row_number() OVER (PARTITION BY pool, t ORDER BY h DESC, owner) AS rk, sum(h) OVER (PARTITION BY pool, t) AS held FROM holdr),
             agg AS (SELECT pool, t, count(*) AS holders_t, max(held) AS held_t,
                            sum(h) FILTER (WHERE rk = 1) / max(held) AS top1_share_t,
                            sum(h) FILTER (WHERE rk <= 10) / max(held) AS top10_share_t,
                            sum((h / held) * (h / held)) AS hhi_t,
                            sum(least(h, received)) / max(held) AS fed_share_t,
                            count(*) FILTER (WHERE received > 0) AS fed_holders_t
                     FROM ranked GROUP BY 1, 2),
             circ AS (SELECT pool, t, sum(h) AS circ_t FROM hold WHERE NOT vault GROUP BY 1, 2),
             ins AS (SELECT h.pool, h.t, sum(h.h) AS h_ins FROM hold h JOIN insiders i ON i.mint = h.mint AND i.owner = h.owner WHERE NOT h.vault GROUP BY 1, 2),
             insn AS (SELECT tw.pool, tw.t, count(i.owner) AS n_insiders FROM tw LEFT JOIN insiders i ON i.mint = tw.mint GROUP BY 1, 2)
        SELECT tw.pool, tw.t, a.holders_t, a.held_t, a.top1_share_t, a.top10_share_t, a.hhi_t, a.fed_share_t, a.fed_holders_t,
               a.fed_holders_t::DOUBLE / nullif(a.holders_t, 0) AS fed_holder_share_t, c.circ_t, n.n_insiders, COALESCE(i.h_ins, 0) AS h_ins_t,
               CASE WHEN n.n_insiders > 0 AND c.circ_t > 0 AND COALESCE(i.h_ins, 0) >= 0 AND COALESCE(i.h_ins, 0) <= c.circ_t
                    THEN COALESCE(i.h_ins, 0) / c.circ_t END AS share_c_t
        FROM tw LEFT JOIN agg a USING (pool, t) LEFT JOIN circ c USING (pool, t) LEFT JOIN insn n USING (pool, t) LEFT JOIN ins i USING (pool, t)""")
    progress(f"feat_t: {con.execute('SELECT count(*), count(share_c_t) FROM feat_t').fetchone()}")
    # post-entry windows in slots per epoch (the clock's ms per slot of the trigger's epoch)
    epochs = list(range(lo // EPOCH_SLOTS, (hi - 1) // EPOCH_SLOTS + 1))
    con.register("win", pa.table({"epoch": pa.array([e for e in epochs for _ in POST_SECONDS], pa.int64()),
                                  "secs": pa.array([x for _ in epochs for x in POST_SECONDS], pa.int64()),
                                  "n": pa.array([clock.slots(x * 1000, e * EPOCH_SLOTS) for e in epochs for x in POST_SECONDS], pa.int64())}))
    con.execute(f"""CREATE OR REPLACE TEMP TABLE sells AS
        SELECT tw.pool, tw.t, tw.mint, w.secs, l.owner, l.slot, (-l.delta)::DOUBLE AS sold
        FROM tw JOIN win w ON w.epoch = tw.t // {EPOCH_SLOTS}
        JOIN legs l ON l.key = tw.pool AND NOT l.buy AND l.slot > tw.entry_slot AND l.slot <= tw.entry_slot + w.n
        ANTI JOIN insiders i ON i.mint = tw.mint AND i.owner = l.owner""")
    con.execute("""CREATE OR REPLACE TEMP TABLE ps AS
        WITH s AS (SELECT pool, t, mint, secs, owner, sum(sold) AS sold, min(slot) AS first_sell FROM sells GROUP BY 1, 2, 3, 4, 5),
             rc AS (SELECT s.pool, s.t, s.secs, s.owner, sum(r.amount) AS received
                    FROM s JOIN recv r ON r.mint = s.mint AND r.owner = s.owner AND r.slot <= s.first_sell GROUP BY 1, 2, 3, 4)
        SELECT s.*, COALESCE(rc.received, 0) AS received FROM s LEFT JOIN rc USING (pool, t, secs, owner)""")
    cols = ", ".join(f"COALESCE(max(sellers) FILTER (WHERE secs = {x}), 0) AS sellers_{x}, COALESCE(max(fed_sellers) FILTER (WHERE secs = {x}), 0) AS fed_sellers_{x}, "
                     f"max(fed_sell_share) FILTER (WHERE secs = {x}) AS fed_sell_share_{x}" for x in POST_SECONDS)
    con.execute(f"""CREATE OR REPLACE TEMP TABLE feat_post AS
        WITH per AS (SELECT pool, t, secs, count(*) AS sellers, count(*) FILTER (WHERE received > 0) AS fed_sellers,
                            sum(least(sold, received)) / nullif(sum(sold), 0) AS fed_sell_share FROM ps GROUP BY 1, 2, 3)
        SELECT tw.pool, tw.t, {cols} FROM tw LEFT JOIN per USING (pool, t) GROUP BY 1, 2""")
    progress(f"feat_post: {con.execute('SELECT count(*), sum(sellers_60) FROM feat_post').fetchone()}")
    transfers.q3(con, clock, lo, hi)


def assemble(con):
    con.execute(f"""CREATE OR REPLACE TEMP TABLE lb AS
        SELECT tr.pool, tr.t, tr.mint, tr.day, tr.farm, tr.regime, tr.trigger_farmer, tr.depth_sol,
               CASE WHEN tr.day < '{latebloom.BINDING_START}' THEN 'days 1-9' WHEN tr.day <= '{latebloom.BINDING_END}' THEN 'days 10-18' ELSE 'later' END AS block,
               o.ms_per_slot, o.entry_slot, o.net_1m, o.net_5m, o.net_15m, o.target_5m, o.target_15m, s.drain_slot,
               (s.drain_slot IS NOT NULL AND s.drain_slot <= o.target_5m) AS drained_5,
               (s.drain_slot IS NOT NULL AND s.drain_slot <= o.target_15m) AS drained_15,
               (s.drain_slot IS NULL OR s.drain_slot > o.target_5m) AND o.net_5m > 0 AS late_5,
               (s.drain_slot IS NULL OR s.drain_slot > o.target_15m) AND o.net_15m > 0 AS late_15,
               tw.pool IS NOT NULL AS in_window, tr.farm = 'flagged' AS farm_flagged,
               ft.* EXCLUDE (pool, t), fp.* EXCLUDE (pool, t), q.fed_share AS q3_fed_share_1h, q.sellers AS q3_sellers_1h
        FROM trig tr JOIN outc o USING (pool, t) LEFT JOIN sv s USING (pool, t) LEFT JOIN tw USING (pool, t)
        LEFT JOIN feat_t ft USING (pool, t) LEFT JOIN feat_post fp USING (pool, t) LEFT JOIN q3 q ON q.pool = tr.pool""")
    return con.execute("SELECT count(*), count(*) FILTER (WHERE in_window) FROM lb").fetchone()


def run(chunks, parts, markout, survival, out_dir, lo=None, hi=None, boot=2000, store=None):
    t0 = time.time()
    store = store or os.environ.get("LAB_STORE", STORE)
    con = connect(store, memory=os.environ.get("LAB_WALLETS_MEMORY", "1500MB"), threads=int(os.environ.get("LAB_WALLETS_THREADS", "2")))
    clo, chi = transfers.chunk_bounds(chunks)
    lo = clo if lo is None else lo
    hi = chi if hi is None else hi
    guard(hi)
    clock = Clock.from_store(con)
    shas = transfers.setup(con, chunks, lo, hi, clock)
    if latebloom.load_triggers(con, parts, lo, hi) == 0:
        raise SystemExit("no triggers in the window")
    latebloom.load_outcomes(con, markout, survival)
    build(con, clock, lo, hi)
    n, n_in = assemble(con)
    progress(f"assembled {n} rows ({n_in} with a ledger); statistics (boot {boot})")
    results = {}
    for blk, where in (("days 10-18", "block = 'days 10-18'"), ("days 1-9", "block = 'days 1-9'"), ("all", "TRUE")):
        res = latebloom.analyse(con, f"{where} AND in_window", boot, FEATURES)
        if res:
            results[blk] = res
    os.makedirs(out_dir, exist_ok=True)
    con.execute(f"COPY (SELECT * FROM lb ORDER BY pool, t) TO '{os.path.join(out_dir, 'latebloom-transfers.parquet')}' (FORMAT PARQUET)")
    info = {"chunks": chunks, "parts": parts, "markout": markout, "survival": survival, "store": store, "lo": lo, "hi": hi,
            "extractor_shas": sorted(shas), "family": latebloom.FAMILY, "segment": latebloom.SEGMENT, "d": latebloom.D,
            "triggers": n, "triggers_with_ledger": n_in, "boot": boot, "post_seconds": list(POST_SECONDS), "commit": git_commit(),
            "seconds": round(time.time() - t0, 1)}
    intro = (f"Triggers {n:,} in [{lo}, {hi}), {n_in:,} with a ledger (the mint created inside the transfers window); the feature tables use those, the outcome shares count every trigger. "
             "Features at t from the owner ledger of spec token-transfers v3 (transfers.setup: store legs + kept token_deltas rows, vaults named by "
             "the ledger, insiders = creator + buyers in the first 10 s), holdings = rows with slot ≤ t; the post-entry windows (t + d, t + d + X] "
             "count the non-insider sellers of the pool and the tokens each received by non-trade transfer at or before its first sell. "
             "Per feature: AUC of late_5 against the rest, late share and mean net at 5 min per quintile, top − bottom with a day bootstrap "
             f"({boot:,}×). Descriptive on used data: no model, no selection, no threshold; Q3's first-hour share is a reference, not point-in-time.\n")
    md = latebloom.render(con, info, results, FEATURES,
                          title=f"## Late bloomers vs drains, E22 addendum (holders and transfers at t): {latebloom.FAMILY} {latebloom.SEGMENT}, d = {latebloom.D}, 0.5 SOL\n",
                          intro=intro)
    with open(os.path.join(out_dir, "latebloom-transfers.md"), "w") as f:
        f.write(md + "\n")
    with open(os.path.join(out_dir, "run-latebloom-transfers.json"), "w") as f:
        json.dump(info, f, indent=1, default=str)
    progress(f"written {out_dir} ({info['seconds']} s)")
    return info


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", required=True, help="the transfers extraction chunks (token_deltas, token_transfers, sol_funding)")
    ap.add_argument("--parts", required=True, help="the f7rev parts (triggers, farm, trigger wallet, depth)")
    ap.add_argument("--markout", required=True, help="markout-f7rev.parquet (spec B)")
    ap.add_argument("--survival", required=True, help="survival.parquet (research D)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--from-slot", type=int)
    ap.add_argument("--to-slot", type=int)
    ap.add_argument("--boot", type=int, default=2000)
    a = ap.parse_args(argv)
    info = run(a.chunks, a.parts, a.markout, a.survival, a.out, a.from_slot, a.to_slot, a.boot)
    print(json.dumps(info, default=str), file=sys.stderr)


if __name__ == "__main__":
    main()
