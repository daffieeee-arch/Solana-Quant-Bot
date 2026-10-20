"""Seller buyback after an F7 trigger, dev only (coordinator 2026-10-20, owner "edge first"): is the reversal after the
large sell the same seller's own scripted buyback? For every F7 pool_reversed trigger of the K3 rows of a tournament run
(tp4_T600, 0.5 SOL, q 0.5, d 2, no overlay: one row per trigger, with trigger_trader), the token BUYS in the pool in the
windows (t, t + 1 m], (t, t + 5 m], (t, t + 15 m] (slots through the store's Clock) from the store's pool legs, split into

  self     the trigger seller's own buys (and, for the net flow, their own sells);
  linked   buys paid by the trigger transaction's fee payer or by the seller, or made by that fee payer (the store's
           fee_payer column: the one wallet link the store itself holds; clusters-v1 lists no wallets per event and
           the sol_funding ledger covers two epochs only, so other links stay unknown);
  other    every other buy;

plus the buys in the trigger transaction itself (rt_tx: the seller sells and the same transaction buys back, E16), and the
NET flow (token buys − token sells in SOL: what moves the price) with the seller's own net. Per trigger: the SOL per class and window, the self share (self / all buys in the window; NULL without a buy) and the flag
self_buyback = self share at 1 m >= SELF_SHARE. Markouts from a markout run (markout-<run>.parquet, F7 pool_reversed, d 2,
h 1m / 5m / 15m: log markout, net 0.5 SOL round trip) are joined on (pool, t).

Report (buyback.md / .json), all descriptive on used data:
  1. the inflow split: SOL by class per window over all triggers; per trigger the median self share; the share of
     triggers with self_buyback, with any seller buy, with no buy at all; rt_tx beside it;
  2. markouts by self_buyback (yes / no / no buy) at 1 m and 5 m: log markout and net per trigger (trimmed mean, day CI);
  3. persistence per seller: sellers with >= MIN_TRIGGERS triggers in days 1-9: their mean self share in days 1-9 and in
     days 10-18, the transition (block-1 buyback sellers -> block-2 share), the share of block-2 triggers by block-1
     sellers, and the block-2 markouts of triggers by block-1 buyback sellers against the rest.
If most of the inflow after the trigger is the seller's own (or linked) money, the reversal is not a market edge: the
seller moves the price back themselves. Dev only: nothing here is a claim.

  LAB_STORE=<dev2> python -m wallets.buyback --parts DIR [--markout DIR --run NAME] --out DIR [--from-slot A --to-slot B]
"""
import argparse
import glob
import json
import os
import sys
import time

import duckdb
import numpy as np

from .common import SOL, Clock, connect, git_commit, guard
from .markout_report import BOOT, SPLIT_DAY, LAST_DAY, _pool, close_pool, day_ci, fmt, trimmed_mean, workers

WINDOWS = (("1m", 60_000), ("5m", 300_000), ("15m", 900_000))
SELF_SHARE = 0.5
MIN_TRIGGERS = 3
SEED = 7
K3 = "family = 'F7' AND segment = 'pool_reversed' AND variant = 'tp4_T600' AND size_sol = 0.5 AND d = 2 AND q = 0.5 AND (overlay IS NULL OR overlay = 'none')"


def load_triggers(con, parts_dir, lo=None, hi=None):
    files = sorted(glob.glob(os.path.join(parts_dir, "*.parquet")))
    if not files:
        raise SystemExit(f"no parquet parts in {parts_dir}")
    lit = "[" + ",".join(f"'{f}'" for f in files) + "]"
    w = [K3, "trigger_trader IS NOT NULL"]
    if lo is not None:
        w.append(f"t >= {lo}")
    if hi is not None:
        w.append(f"t < {hi}")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE trig AS
      SELECT pool, t, any_value(trigger_trader) AS seller, any_value(trigger_farmer) AS seller_farmer, any_value(farm) AS farm, any_value(day) AS day
      FROM read_parquet({lit}, union_by_name = true) WHERE {' AND '.join(w)} GROUP BY pool, t""")
    return con.execute("SELECT count(*) FROM trig").fetchone()[0]


def windows(con, clock):
    """The window ends per trigger in slots (the Clock of the trigger's epoch), as columns w1, w5, w15."""
    rows = con.execute("SELECT pool, t FROM trig").fetchall()
    ends = [(p, t) + tuple(t + clock.slots(ms, t) for _, ms in WINDOWS) for p, t in rows]
    con.execute("CREATE OR REPLACE TEMP TABLE wend (pool VARCHAR, t BIGINT, w1 BIGINT, w5 BIGINT, w15 BIGINT)")
    con.executemany("INSERT INTO wend VALUES (?, ?, ?, ?, ?)", ends)
    con.execute("CREATE OR REPLACE TEMP TABLE trig2 AS SELECT g.*, w.w1, w.w5, w.w15 FROM trig g JOIN wend w USING (pool, t)")


def legs_sql(legs="pool"):
    """The trigger leg (the seller's first TOKEN sell in the pool at t: its fee payer and transaction) and every TOKEN buy in the
    pool in (t, t + 15 m] with its class; an equality join on pool with the slot range as a filter (a hash join). Token side
    through the store's token_buy (in a reversed pool a token buy is a pool 'sell' of WSOL: kind is the pool's side)."""
    return f"""
      CREATE OR REPLACE TEMP TABLE tleg AS
      SELECT g.pool, g.t, p.fee_payer AS payer, p.tx_index AS tx_t, p.fee_payer IS NOT NULL AS payer_known
      FROM trig2 g JOIN {legs} p ON p.pool = g.pool AND p.slot = g.t
      WHERE p.kind IN ('buy', 'sell') AND NOT COALESCE(p.token_buy, p.kind = 'buy') AND p.trader = g.seller
      QUALIFY row_number() OVER (PARTITION BY g.pool, g.t ORDER BY p.tx_index, p.outer_ix, p.inner_ix) = 1;
      CREATE OR REPLACE TEMP TABLE buys AS
      SELECT g.pool, g.t, p.slot, p.tx_index, COALESCE(p.sol_amount, 0)::BIGINT AS sol, p.fee_payer IS NOT NULL AS payer_known,
             COALESCE(p.token_buy, p.kind = 'buy') AS is_buy,
             CASE WHEN p.trader = g.seller THEN 'self'
                  WHEN p.fee_payer IS NOT NULL AND (p.fee_payer = l.payer OR p.fee_payer = g.seller) AND p.fee_payer <> p.trader THEN 'linked'
                  WHEN l.payer IS NOT NULL AND p.trader = l.payer AND l.payer <> g.seller THEN 'linked'
                  ELSE 'other' END AS cls,
             p.slot <= g.w1 AS in1, p.slot <= g.w5 AS in5, p.slot = g.t AND l.tx_t IS NOT NULL AND p.tx_index = l.tx_t AS rt
      FROM trig2 g JOIN {legs} p ON p.pool = g.pool AND p.slot >= g.t AND p.slot <= g.w15
      LEFT JOIN tleg l ON l.pool = g.pool AND l.t = g.t
      WHERE p.kind IN ('buy', 'sell') AND (p.slot > g.t OR (l.tx_t IS NOT NULL AND p.tx_index = l.tx_t AND COALESCE(p.token_buy, p.kind = 'buy')))"""


def per_trigger(con):
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE bt AS
      SELECT g.pool, g.t, g.seller, g.seller_farmer, g.farm, g.day, l.tx_t IS NOT NULL AS trigger_leg_found, COALESCE(l.payer_known, false) AS payer_known,
             count(b.sol) FILTER (WHERE NOT b.rt AND b.is_buy AND b.payer_known) AS buys_payer_known, count(b.sol) FILTER (WHERE NOT b.rt AND b.is_buy) AS buys_15m,
             COALESCE(sum(b.sol) FILTER (WHERE b.rt), 0) AS rt_sol, count(b.sol) FILTER (WHERE b.rt) > 0 AS rt_tx,
             COALESCE(sum(CASE WHEN b.is_buy THEN b.sol ELSE -b.sol END) FILTER (WHERE NOT b.rt AND b.in1), 0) AS net_1m_flow,
             COALESCE(sum(CASE WHEN b.is_buy THEN b.sol ELSE -b.sol END) FILTER (WHERE NOT b.rt AND b.in1 AND b.cls = 'self'), 0) AS self_net_1m,
             COALESCE(sum(CASE WHEN b.is_buy THEN b.sol ELSE -b.sol END) FILTER (WHERE NOT b.rt AND b.in5), 0) AS net_5m_flow,
             COALESCE(sum(CASE WHEN b.is_buy THEN b.sol ELSE -b.sol END) FILTER (WHERE NOT b.rt AND b.in5 AND b.cls = 'self'), 0) AS self_net_5m,
             COALESCE(sum(b.sol) FILTER (WHERE NOT b.rt AND b.is_buy AND b.in1 AND b.cls = 'self'), 0) AS self_1m,
             COALESCE(sum(b.sol) FILTER (WHERE NOT b.rt AND b.is_buy AND b.in1 AND b.cls = 'linked'), 0) AS linked_1m,
             COALESCE(sum(b.sol) FILTER (WHERE NOT b.rt AND b.is_buy AND b.in1), 0) AS all_1m,
             COALESCE(sum(b.sol) FILTER (WHERE NOT b.rt AND b.is_buy AND b.in5 AND b.cls = 'self'), 0) AS self_5m,
             COALESCE(sum(b.sol) FILTER (WHERE NOT b.rt AND b.is_buy AND b.in5 AND b.cls = 'linked'), 0) AS linked_5m,
             COALESCE(sum(b.sol) FILTER (WHERE NOT b.rt AND b.is_buy AND b.in5), 0) AS all_5m,
             COALESCE(sum(b.sol) FILTER (WHERE NOT b.rt AND b.is_buy AND b.cls = 'self'), 0) AS self_15m,
             COALESCE(sum(b.sol) FILTER (WHERE NOT b.rt AND b.is_buy AND b.cls = 'linked'), 0) AS linked_15m,
             COALESCE(sum(b.sol) FILTER (WHERE NOT b.rt AND b.is_buy), 0) AS all_15m,
             count(b.sol) FILTER (WHERE NOT b.rt AND b.is_buy AND b.in1) AS buys_1m, count(b.sol) FILTER (WHERE NOT b.rt AND b.is_buy AND b.in1 AND b.cls = 'self') AS self_buys_1m
      FROM trig2 g LEFT JOIN tleg l ON l.pool = g.pool AND l.t = g.t LEFT JOIN buys b ON b.pool = g.pool AND b.t = g.t
      GROUP BY ALL""")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE bt2 AS
      SELECT *, CASE WHEN all_1m > 0 THEN self_1m / all_1m END AS self_share_1m, CASE WHEN all_5m > 0 THEN self_5m / all_5m END AS self_share_5m,
             CASE WHEN all_15m > 0 THEN self_15m / all_15m END AS self_share_15m,
             CASE WHEN all_1m > 0 THEN (self_1m + linked_1m) / all_1m END AS own_share_1m,
             CASE WHEN all_1m = 0 THEN 'no buy' WHEN self_1m / all_1m >= {SELF_SHARE} THEN 'yes' ELSE 'no' END AS self_buyback,
             CASE WHEN day < '{SPLIT_DAY}' THEN 'days 1-9' WHEN day <= '{LAST_DAY}' THEN 'days 10-18' ELSE 'later' END AS block
      FROM bt""")


def join_markouts(con, markout_dir, run):
    pat = f"markout-{run}.parquet" if run else "markout-*.parquet"
    files = sorted(glob.glob(os.path.join(markout_dir, pat)))
    if not files:
        raise SystemExit(f"no {pat} in {markout_dir}")
    lit = "[" + ",".join(f"'{f}'" for f in files) + "]"
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE mk AS
      SELECT pool, t, h, any_value(ln(GREATEST(1 + gross, 0.001))) AS lg, any_value(net) AS net
      FROM read_parquet({lit}, union_by_name = true)
      WHERE family = 'F7' AND segment = 'pool_reversed' AND d = 2 AND h IN ('1m', '5m', '15m') AND gross IS NOT NULL GROUP BY ALL""")
    con.execute("""
      CREATE OR REPLACE TEMP TABLE bt3 AS
      SELECT b.*, m1.lg AS lg_1m, m1.net AS net_1m, m5.lg AS lg_5m, m5.net AS net_5m, m15.lg AS lg_15m, m15.net AS net_15m
      FROM bt2 b LEFT JOIN mk m1 ON m1.pool = b.pool AND m1.t = b.t AND m1.h = '1m'
                 LEFT JOIN mk m5 ON m5.pool = b.pool AND m5.t = b.t AND m5.h = '5m'
                 LEFT JOIN mk m15 ON m15.pool = b.pool AND m15.t = b.t AND m15.h = '15m'""")
    return con.execute("SELECT count(lg_1m) FROM bt3").fetchone()[0]


def _ci(con, col, where, boot):
    rows = con.execute(f"SELECT {col}, day FROM bt3 WHERE {where} AND {col} IS NOT NULL").fetchall()
    if not rows:
        return None
    x = np.array([r[0] for r in rows], dtype=float)
    lo, hi = day_ci(x, np.array([r[1] for r in rows]), boot, seed=SEED)
    return {"n": len(x), "mean": float(trimmed_mean(x)), "lo": lo, "hi": hi}


def analyse(con, boot, log=lambda m: None):
    t0 = time.time()
    res = {"windows": [w for w, _ in WINDOWS], "self_share": SELF_SHARE, "min_triggers": MIN_TRIGGERS, "boot": boot, "seed": SEED}
    n, found, days = con.execute("SELECT count(*), sum(trigger_leg_found::INT), count(DISTINCT day) FROM bt3").fetchone()
    pk, bpk, b15 = con.execute("SELECT sum(payer_known::INT), sum(buys_payer_known), sum(buys_15m) FROM bt3").fetchone()
    res["triggers"] = {"n": n, "trigger_leg_found": found, "days": days, "payer_known": pk, "buys_15m": b15, "buys_payer_known": bpk,
                       "sellers": con.execute("SELECT count(DISTINCT seller) FROM bt3").fetchone()[0]}
    log(f"{n:,} triggers, trigger leg found for {found:,}, {days} days")
    # 1. the inflow split
    split = {}
    for w in res["windows"]:
        s, l, a, med, q75 = con.execute(f"""SELECT sum(self_{w}), sum(linked_{w}), sum(all_{w}), quantile_cont(self_share_{w}, 0.5),
                                                   quantile_cont(self_share_{w}, 0.75) FROM bt3""").fetchone()
        split[w] = {"self_sol": s / SOL, "linked_sol": l / SOL, "all_sol": a / SOL, "self_share_of_sol": (s / a) if a else None,
                    "own_share_of_sol": ((s + l) / a) if a else None, "median_self_share": med, "p75_self_share": q75,
                    "triggers_any_self": con.execute(f"SELECT count(*) FROM bt3 WHERE self_{w} > 0").fetchone()[0],
                    "triggers_no_buy": con.execute(f"SELECT count(*) FROM bt3 WHERE all_{w} = 0").fetchone()[0]}
    res["inflow"] = split
    nf = {}
    for w in ("1m", "5m"):
        a, sn, pos = con.execute(f"SELECT sum(net_{w}_flow), sum(self_net_{w}), count(*) FILTER (WHERE net_{w}_flow > 0) FROM bt3").fetchone()
        med = con.execute(f"SELECT quantile_cont(self_net_{w} / net_{w}_flow, 0.5) FROM bt3 WHERE net_{w}_flow > 0").fetchone()[0]
        nf[w] = {"net_flow_sol": a / SOL, "self_net_sol": sn / SOL, "self_share_of_net": (sn / a) if a else None, "triggers_net_positive": pos,
                 "median_self_share_of_net": med}
    res["net_flow"] = nf
    res["flags"] = dict(con.execute("SELECT self_buyback, count(*) FROM bt3 GROUP BY 1").fetchall())
    res["rt_tx"] = {"triggers": con.execute("SELECT count(*) FROM bt3 WHERE rt_tx").fetchone()[0],
                    "sol": con.execute("SELECT sum(rt_sol) FROM bt3").fetchone()[0] / SOL}
    res["by_farm"] = {k: {"n": v, "self_buyback": s} for k, v, s in con.execute(
        "SELECT farm, count(*), avg((self_buyback = 'yes')::INT) FROM bt3 GROUP BY 1 ORDER BY 1").fetchall()}
    res["by_seller_farmer"] = {str(k): {"n": v, "self_buyback": s} for k, v, s in con.execute(
        "SELECT seller_farmer, count(*), avg((self_buyback = 'yes')::INT) FROM bt3 GROUP BY 1 ORDER BY 1").fetchall()}
    log("inflow 1m: self " + fmt(split["1m"]["self_share_of_sol"], pct=True) + " of the SOL, own (self + linked) " + fmt(split["1m"]["own_share_of_sol"], pct=True)
        + f"; flags {res['flags']}")
    # 2. markouts by flag
    res["markouts"] = {}
    for flag in ("yes", "no", "no buy"):
        res["markouts"][flag] = {c: _ci(con, c, f"self_buyback = '{flag}'", boot) for c in ("lg_1m", "net_1m", "lg_5m", "net_5m", "lg_15m", "net_15m")}
    res["markouts"]["all"] = {c: _ci(con, c, "TRUE", boot) for c in ("lg_1m", "net_1m", "lg_5m", "net_5m")}
    # 3. persistence per seller
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE sb AS
      SELECT seller, block, count(*) AS n, avg(self_share_1m) AS self_share, avg((self_buyback = 'yes')::INT) AS buyback_rate,
             avg(lg_5m) AS lg_5m, avg(net_5m) AS net_5m
      FROM bt3 WHERE block IN ('days 1-9', 'days 10-18') GROUP BY ALL""")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE pers AS
      SELECT a.seller, a.n AS n1, b.n AS n2, a.self_share AS share1, b.self_share AS share2, a.buyback_rate AS rate1, b.buyback_rate AS rate2,
             a.buyback_rate >= {SELF_SHARE} AS buyback_seller_1
      FROM sb a LEFT JOIN sb b ON b.seller = a.seller AND b.block = 'days 10-18' WHERE a.block = 'days 1-9' AND a.n >= {MIN_TRIGGERS}""")
    p = {}
    p["sellers_block1"], p["sellers_in_both"], p["buyback_sellers_1"] = con.execute(
        "SELECT count(*), count(n2), COALESCE(sum(buyback_seller_1::INT), 0) FROM pers").fetchone()
    p["rate_block2_of_buyback_sellers_1"], p["rate_block2_of_other_sellers_1"] = con.execute(
        "SELECT avg(rate2) FILTER (WHERE buyback_seller_1), avg(rate2) FILTER (WHERE NOT buyback_seller_1) FROM pers WHERE n2 IS NOT NULL").fetchone()
    p["share2_of_buyback_sellers_1"], p["share2_of_other_sellers_1"] = con.execute(
        "SELECT avg(share2) FILTER (WHERE buyback_seller_1), avg(share2) FILTER (WHERE NOT buyback_seller_1) FROM pers WHERE n2 IS NOT NULL").fetchone()
    rows = con.execute("SELECT rate1, rate2 FROM pers WHERE n2 IS NOT NULL AND rate1 IS NOT NULL AND rate2 IS NOT NULL").fetchall()
    p["corr_rate_1_2"] = float(np.corrcoef([r[0] for r in rows], [r[1] for r in rows])[0, 1]) if len(rows) >= 3 else None
    n2_all, n2_b1, n2_bb = con.execute(f"""SELECT count(*), count(*) FILTER (WHERE seller IN (SELECT seller FROM pers)),
        count(*) FILTER (WHERE seller IN (SELECT seller FROM pers WHERE buyback_seller_1)) FROM bt3 WHERE block = 'days 10-18'""").fetchone()
    p["block2_triggers"] = {"n": n2_all, "by_block1_sellers": n2_b1, "by_block1_buyback_sellers": n2_bb}
    p["block2_markouts"] = {
        "by_block1_buyback_sellers": {c: _ci(con, c, "block = 'days 10-18' AND seller IN (SELECT seller FROM pers WHERE buyback_seller_1)", boot) for c in ("lg_5m", "net_5m", "lg_1m", "net_1m")},
        "rest": {c: _ci(con, c, "block = 'days 10-18' AND seller NOT IN (SELECT seller FROM pers WHERE buyback_seller_1)", boot) for c in ("lg_5m", "net_5m", "lg_1m", "net_1m")}}
    res["persistence"] = p
    res["seconds"] = round(time.time() - t0, 1)
    return res


def _m(s, pct=False):
    if not s:
        return "–"
    f = (lambda x: fmt(None if x is None else x * 100, sign=True)) if not pct else (lambda x: fmt(x, pct=True, sign=True))
    return f"{f(s['mean'])} [{f(s['lo'])}; {f(s['hi'])}] (n {s['n']:,})"


def markdown(res, parts_dir):
    t, i = res["triggers"], res["inflow"]
    L = [f"## Seller buyback after F7 triggers (dev, descriptive) — {os.path.basename(os.path.dirname(parts_dir.rstrip('/')))}", "",
         f"{t['n']:,} F7 pool_reversed triggers (K3 rows: tp4_T600, 0.5 SOL, d 2) by {t['sellers']:,} sellers over {t['days']} days; trigger leg found for "
         f"{t['trigger_leg_found']:,}; fee payer known for {t['payer_known']:,} trigger legs and {t['buys_payer_known']:,} of {t['buys_15m']:,} buys (the store's "
         f"fee_payer is sparse: the linked class is a lower bound). Classes: self = the seller's own buys; linked = buys paid by the trigger transaction's fee payer or by the seller, "
         f"or made by that payer; rt_tx = a buy in the trigger transaction itself. self_buyback = self share of the SOL bought in (t, t + 1 m] ≥ "
         f"{res['self_share'] * 100:.0f} %. Markouts: log × 100 (lp), net in pt; day bootstrap {res['boot']:,} draws.", "",
         "### 1. Where the inflow after the trigger comes from", "",
         "| window | all buys (SOL) | self (SOL) | self share of SOL | self + linked share | median self share per trigger | triggers with a seller buy | triggers without a buy |",
         "|---|---|---|---|---|---|---|---|"]
    for w, s in i.items():
        L.append(f"| {w} | {s['all_sol']:,.0f} | {s['self_sol']:,.0f} | {fmt(s['self_share_of_sol'], pct=True)} | {fmt(s['own_share_of_sol'], pct=True)} | "
                 f"{fmt(s['median_self_share'], pct=True)} | {s['triggers_any_self']:,} ({s['triggers_any_self'] / t['n'] * 100:.0f} %) | {s['triggers_no_buy']:,} |")
    nf = res["net_flow"]
    L += ["", "Net flow (token buys − token sells in SOL, what moves the price): " + "; ".join(
        f"{w}: {v['net_flow_sol']:,.0f} SOL over all triggers, the seller's own net {v['self_net_sol']:,.0f} SOL = {fmt(v['self_share_of_net'], pct=True)} of it; "
        f"{v['triggers_net_positive']:,} triggers with a positive net flow, median self share of that net {fmt(v['median_self_share_of_net'], pct=True)}" for w, v in nf.items()) + "."]
    f = res["flags"]
    L += ["", f"self_buyback at 1 m: yes {f.get('yes', 0):,}, no {f.get('no', 0):,}, no buy {f.get('no buy', 0):,}; rt_tx (buy in the trigger transaction) "
          f"{res['rt_tx']['triggers']:,} triggers, {res['rt_tx']['sol']:,.0f} SOL.",
          "Per farm label: " + "; ".join(f"{k} {v['n']:,} triggers, self_buyback {fmt(v['self_buyback'], pct=True)}" for k, v in res["by_farm"].items())
          + ". Per seller farmer flag: " + "; ".join(f"{k} {v['n']:,}, {fmt(v['self_buyback'], pct=True)}" for k, v in res["by_seller_farmer"].items()) + ".", "",
          "### 2. Markouts by self_buyback (per trigger, trimmed mean, day CI)", "",
          "| self_buyback | log 1 m | net 1 m | log 5 m | net 5 m | log 15 m | net 15 m |", "|---|---|---|---|---|---|---|"]
    for flag in ("yes", "no", "no buy"):
        m = res["markouts"][flag]
        L.append(f"| {flag} | " + " | ".join(_m(m[c]) for c in ("lg_1m", "net_1m", "lg_5m", "net_5m", "lg_15m", "net_15m")) + " |")
    m = res["markouts"]["all"]
    L.append(f"| all | " + " | ".join(_m(m[c]) for c in ("lg_1m", "net_1m", "lg_5m", "net_5m")) + " | – | – |")
    p = res["persistence"]
    L += ["", "### 3. Persistence per seller (days 1-9 → days 10-18)", "",
          f"- sellers with ≥ {res['min_triggers']} triggers in days 1-9: {p['sellers_block1']:,}; of them {p['sellers_in_both']:,} trigger again in days 10-18; "
          f"buyback sellers in block 1 (buyback rate ≥ {res['self_share'] * 100:.0f} %): {p['buyback_sellers_1']:,}.",
          f"- block-2 buyback rate of block-1 buyback sellers {fmt(p['rate_block2_of_buyback_sellers_1'], pct=True)} against {fmt(p['rate_block2_of_other_sellers_1'], pct=True)} "
          f"for the other block-1 sellers; mean self share {fmt(p['share2_of_buyback_sellers_1'], pct=True)} against {fmt(p['share2_of_other_sellers_1'], pct=True)}; "
          f"correlation of the per-seller buyback rate between the blocks {fmt(p['corr_rate_1_2'])}.",
          f"- block-2 triggers: {p['block2_triggers']['n']:,}, of which {p['block2_triggers']['by_block1_sellers']:,} by block-1 sellers and "
          f"{p['block2_triggers']['by_block1_buyback_sellers']:,} by block-1 buyback sellers.",
          "- block-2 markouts, triggers by block-1 buyback sellers: " + "; ".join(f"{c} {_m(s)}" for c, s in p["block2_markouts"]["by_block1_buyback_sellers"].items())
          + "; the rest: " + "; ".join(f"{c} {_m(s)}" for c, s in p["block2_markouts"]["rest"].items()) + ".", ""]
    return "\n".join(L) + "\n"


def run(parts_dir, out_dir, markout_dir=None, markout_run=None, lo=None, hi=None, store=None, boot=BOOT, log=lambda m: None):
    t0 = time.time()
    mem = os.environ.get("LAB_WALLETS_MEMORY", "1GB")
    con = connect(store, memory=mem) if store else connect(memory=mem)
    clock = Clock.from_store(con)
    meta = json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
    guard(hi if hi is not None else int(meta["slot_end_exclusive"]))
    n = load_triggers(con, parts_dir, lo, hi)
    if n == 0:
        raise SystemExit("no F7 pool_reversed K3 triggers in the parts (or the slot window)")
    log(f"{n:,} triggers")
    windows(con, clock)
    con.execute(legs_sql())
    nb = con.execute("SELECT count(*) FROM buys").fetchone()[0]
    log(f"{nb:,} buys in the 15 m windows")
    per_trigger(con)
    n_mk = join_markouts(con, markout_dir, markout_run) if markout_dir else con.execute(
        "CREATE OR REPLACE TEMP TABLE bt3 AS SELECT *, NULL::DOUBLE AS lg_1m, NULL::DOUBLE AS net_1m, NULL::DOUBLE AS lg_5m, NULL::DOUBLE AS net_5m, "
        "NULL::DOUBLE AS lg_15m, NULL::DOUBLE AS net_15m FROM bt2") and 0
    log(f"markouts joined for {n_mk:,} triggers")
    os.makedirs(out_dir, exist_ok=True)
    con.execute(f"COPY (SELECT * FROM bt3 ORDER BY pool, t) TO '{os.path.join(out_dir, 'buyback.parquet')}' (FORMAT parquet)")
    res = analyse(con, boot, log)
    res["input"] = {"parts": parts_dir, "markout": markout_dir, "run": markout_run, "slots": [lo, hi], "buys": nb, "markouts_joined": n_mk,
                    "store": store or os.environ.get("LAB_STORE"), "commit": git_commit(), "seconds": round(time.time() - t0, 1)}
    with open(os.path.join(out_dir, "buyback.md"), "w") as f:
        f.write(markdown(res, parts_dir))
    with open(os.path.join(out_dir, "buyback.json"), "w") as f:
        json.dump(res, f, indent=1, default=float)
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--parts", required=True)
    ap.add_argument("--markout")
    ap.add_argument("--run", help="markout-<run>.parquet in --markout")
    ap.add_argument("--out", required=True)
    ap.add_argument("--from-slot", type=int)
    ap.add_argument("--to-slot", type=int)
    ap.add_argument("--boot", type=int, default=BOOT)
    a = ap.parse_args()
    if workers() > 1:
        _pool(workers())
    try:
        res = run(a.parts, a.out, a.markout, a.run, a.from_slot, a.to_slot, boot=a.boot, log=lambda m: print(m, file=sys.stderr, flush=True))
        print(json.dumps({"out": a.out, "triggers": res["triggers"]["n"], "flags": res["flags"], "seconds": res["input"]["seconds"]}))
    finally:
        close_pool()


if __name__ == "__main__":
    main()
