"""E73: several good wallets buying together (plan v0.2 section 4). Good set per UTC day D from money-v2 wallet-days
(realised = cash - fees): realised > 0 on >= 5 of the calendar days D-7 .. D-1, sum over those days >= 5 SOL, buy_sol > 0
on >= 5 of them; only days < D. Wallet identity = the trader of the leg on both sides. Trigger: the k-th distinct good
wallet buying the same key (curve mint, normal-pool mint, or reversed pool) within N seconds of the first, t = that buy's
slot, then 60 min cooldown per key. SOL quote / reversed, not Mayhem. Cells: k2n30 (primary), k3n30, k2n10, k2n120.

The good set is built once per run into <out>/good-days.parquet (LAB_E73_GOOD to reuse one; LAB_E73_SAMPLE=<n> keeps
1 / n of the wallets for a VPS smoke, never for a result).
"""

import glob
import os

from .. import runners as R
from ..markout import ms_case
from . import harness as H

SYS = "11111111111111111111111111111111"
MONEY = os.environ.get("LAB_E73_MONEY", "/home/chupa/Solana-project/data-old-faithful-one/lab/wallets/money-v2-94f0fee")
CELLS = {"k2n30": (2, 30), "k3n30": (3, 30), "k2n10": (2, 10), "k2n120": (2, 120)}
COOLDOWN_MS = 3_600_000
FIRST_DAY = "2026-09-19"


def good_days(con, out_dir):
    """(wallet, day) rows of the good set per measured day, from the wallet-day files; cached as parquet."""
    cached = os.environ.get("LAB_E73_GOOD") or os.path.join(out_dir, "good-days.parquet")
    if os.path.exists(cached):
        con.execute(f"CREATE OR REPLACE TEMP TABLE good AS SELECT wallet, day FROM read_parquet('{cached}')")
        return cached
    files = sorted(glob.glob(os.path.join(MONEY, "wallet-day-*.parquet")))
    if not files:
        raise SystemExit(f"E73: no money-v2 wallet-day files under {MONEY}")
    sample = int(os.environ.get("LAB_E73_SAMPLE", "0") or 0)
    where = f"WHERE hash(wallet) % {sample} = 0" if sample > 0 else ""
    con.execute(f"""CREATE OR REPLACE TEMP TABLE wd AS
        SELECT wallet, day::DATE AS d, cash - fees AS realised, buy_sol FROM read_parquet({files!r}) {where}
        QUALIFY count(*) OVER (PARTITION BY wallet) >= 5""")  -- pos7 >= 5 needs at least 5 wallet-days: the rest never qualifies
    # exact recomputation per (wallet, D): the window D-7 .. D-1 over the calendar
    con.execute(f"""CREATE OR REPLACE TEMP TABLE good AS
        WITH days AS (SELECT DISTINCT d FROM wd WHERE d >= DATE '{FIRST_DAY}'),
        cand AS (SELECT DISTINCT w.wallet, days.d FROM wd w JOIN days ON w.d < days.d AND w.d >= days.d - INTERVAL 7 DAY),
        st AS (
            SELECT c.wallet, c.d, sum(w.realised) AS s7, count(*) FILTER (WHERE w.realised > 0) AS pos7, count(*) FILTER (WHERE w.buy_sol > 0) AS buy7
            FROM cand c JOIN wd w ON w.wallet = c.wallet AND w.d < c.d AND w.d >= c.d - INTERVAL 7 DAY GROUP BY 1, 2)
        SELECT wallet, strftime(d, '%Y-%m-%d') AS day FROM st WHERE pos7 >= 5 AND s7 >= 5 AND buy7 >= 5""")
    con.execute(f"COPY good TO '{cached}' (FORMAT PARQUET)")
    return cached


def triggers(con, clock):
    """From gb (key, slot, ord, trader of the good wallets' buys): per cell the k-th distinct good wallet within N s, then
    the 60 min cooldown per key -> TEMP TABLE trig_all (key, moment, t)."""
    cool = f"greatest(1, round({COOLDOWN_MS} / ({ms_case(clock, 'a.slot')})))::BIGINT"
    parts = []
    for cell, (k, n) in CELLS.items():
        win = f"greatest(1, round({n * 1000} / ({ms_case(clock, 'a.slot')})))::BIGINT"
        # a buy is a candidate trigger when >= k-1 other distinct good wallets bought the key in the N s before it
        con.execute(f"""CREATE OR REPLACE TEMP TABLE cand_{cell} AS
            SELECT a.key, a.slot, a.ord, {cool} AS cool
            FROM gb a WHERE (SELECT count(DISTINCT b.trader) FROM gb b WHERE b.key = a.key AND b.trader <> a.trader AND b.slot < a.slot AND b.slot >= a.slot - {win}) >= {k - 1}
            ORDER BY a.key, a.ord""")
        # the cooldown: the first candidate per key, then the first one at least 60 min after the previous trigger
        rows = con.execute(f"SELECT key, slot, cool FROM cand_{cell} ORDER BY key, slot").fetchall()
        trig, last = [], {}
        for key, slot, cool_slots in rows:
            if key not in last or slot >= last[key] + cool_slots:
                trig.append((key, cell, slot))
                last[key] = slot
        con.execute(f"CREATE OR REPLACE TEMP TABLE trig_{cell} AS SELECT * FROM (VALUES " + (", ".join(f"('{a}', '{b}', {c})" for a, b, c in trig) if trig else "(NULL, NULL, NULL)") + ") v(key, moment, t)" + ("" if trig else " WHERE key IS NOT NULL"))
        parts.append(f"SELECT key, moment, t FROM trig_{cell}")
        R.progress(f"E73 {cell}: candidates {len(rows)}, triggers after cooldown {len(trig)}")
    con.execute("CREATE OR REPLACE TEMP TABLE trig_all AS " + " UNION ALL ".join(parts))


def run(con, lo, hi, clock, size_sol, cost_tx, out_dir):
    cached = good_days(con, out_dir)
    R.progress(f"E73 good set: {R._count(con, 'good')} wallet-days from {cached}: " + str(con.execute("SELECT day, count(*) FROM good GROUP BY 1 ORDER BY 1").fetchall()))
    # buys by good wallets in the window, with the day of the buy; key = curve mint / normal-pool mint / reversed pool
    con.execute(f"""CREATE OR REPLACE TEMP TABLE gb AS
        WITH b AS (
            SELECT c.mint AS key, c.slot, c.slot * 1000000000 + c.tx_index * 100000 + c.outer_ix * 1000 + c.inner_ix AS ord, c.trader
            FROM curve c JOIN mints m ON m.mint = c.mint WHERE c.is_buy AND c.slot >= {lo} AND c.slot < {hi} AND m.quote_mint = '{SYS}' AND NOT COALESCE(m.mayhem, false)
            UNION ALL
            SELECT CASE WHEN ps.orientation = 'reversed' THEN p.pool ELSE ps.mint END, p.slot, p.slot * 1000000000 + p.tx_index * 100000 + p.outer_ix * 1000 + p.inner_ix, p.trader
            FROM pool p JOIN pools ps ON ps.pool = p.pool WHERE p.kind = 'buy' AND p.token_buy AND p.slot >= {lo} AND p.slot < {hi}
              AND ps.quote_class IN ('sol', 'reversed') AND NOT COALESCE(ps.mayhem, false))
        SELECT b.key, b.slot, b.ord, b.trader, strftime(make_timestamp(bl.block_time * 1000000), '%Y-%m-%d') AS day
        FROM b JOIN blocks bl ON bl.slot = b.slot JOIN good g ON g.wallet = b.trader AND g.day = strftime(make_timestamp(bl.block_time * 1000000), '%Y-%m-%d')""")
    R.progress(f"E73 good-wallet buys in the window: {R._count(con, 'gb')}")
    triggers(con, clock)
    # tok: the triggered keys (mints with a curve / normal pool via the mints row; reversed pools via pools)
    tok = f"""
        {H.tok_sql_for_mints("m.mint IN (SELECT key FROM trig_all)")}
        UNION ALL
        SELECT ps.pool AS key, ps.mint, ps.create_slot, ps.coin_creator AS creator, ps.mayhem, NULL::UBIGINT AS complete_slot, false AS has_curve,
               ps.pool, ps.orientation, ps.create_slot AS pool_slot
        FROM pools ps WHERE ps.orientation = 'reversed' AND ps.pool IN (SELECT key FROM trig_all)"""
    hi_ext = H.prepare(con, lo, hi, clock, tok)
    H.moments_from(con, "SELECT key, moment, t FROM trig_all WHERE key IN (SELECT key FROM tok)", clock)
    H.simulate(con, cost_tx, hi_ext, clock)
    return H.write(con, out_dir, "e73", lo, size_sol, "e73", {"good_wallet_days": R._count(con, "good"), "good_buys": R._count(con, "gb"),
                                                                 "cells": CELLS, "good_cache": cached, "sample": os.environ.get("LAB_E73_SAMPLE", "")})
