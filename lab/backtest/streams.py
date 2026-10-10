"""Load per-token event streams from a store (DuckDB file built by store.py)."""

from dataclasses import replace

import duckdb

from . import data
from .replay import PUMP, PUMP_AMM, CurveLeg, PoolLeg, Stream
from .venues import Curve, Pool


SPILL_DIR = "/home/chupa/Solana-project/data-old-faithful-one/lab/tmp-duckdb"


def open_store(path, threads=2, memory="1500MB"):
    con = duckdb.connect(path, read_only=True)
    con.execute(f"SET threads TO {threads}")
    con.execute(f"SET memory_limit = '{memory}'")
    con.execute(f"SET temp_directory = '{SPILL_DIR}'")
    con.execute("SET preserve_insertion_order = false")
    return con


def _curve_legs(rows):
    legs = []
    for (slot, tx, oix, iix, variant, is_buy, t, q, fee, cfee, fbps, cbps, vt, vq, rt, rq, trader,
         budget, max_cost, min_tokens, min_out, outer) in rows:
        if is_buy:
            pre = Curve(vt + t, vq - q, rt + t, rq - q, fbps, cbps)
        else:
            pre = Curve(vt - t, vq + q, rt - t, rq + q, fbps, cbps)
        post = Curve(vt, vq, rt, rq, fbps, cbps)
        legs.append(CurveLeg(slot, tx, (slot, tx, oix, iix), is_buy, t, q, fee + cfee, variant, pre, post, trader,
                             budget, max_cost, min_tokens, min_out, outer == PUMP))
    return legs


def _pool_legs(rows):
    """Pool legs from store rows. Post-states come from the logged amounts (the (B, E) chain the
    continuity check verifies), not from re-running the formulas. Withdraw and deposit events do
    not log the virtual quote reserves, so they take them from the previous leg."""
    legs = []
    virt, fees = 0, (0, 0, 0, 0)
    for (kind, slot, tx, oix, iix, disc, b, vault, vq, e, lpb, pb, cb, cbb, base, e_delta, quote_user, lq, lb,
         left, lp_amount, lp_supply, trader, outer) in rows:
        order = (slot, tx, oix, iix)
        if kind in ("withdraw", "deposit"):
            pre = Pool(b, vault + virt, *fees, virt=virt)
            sign = -1 if kind == "withdraw" else 1
            post = replace(pre, b=b + sign * base, e=pre.e + sign * quote_user)
            legs.append(PoolLeg(slot, tx, order, kind, False, base, quote_user, pre, post, trader,
                                lp_amount=lp_amount, lp_supply=lp_supply, direct=outer == PUMP_AMM))
            continue
        virt = vq or 0
        if kind != "boost":
            fees = (lpb or 0, pb or 0, cb or 0, cbb or 0)
        pre = Pool(b, e, *(fees if kind != "boost" else (0, 0, 0, 0)), virt=virt)
        post = replace(pre, b=b + base if kind == "sell" else b - base, e=e + e_delta)
        if kind == "boost":
            legs.append(PoolLeg(slot, tx, order, "boost", False, base, e_delta, pre, post, trader, boost_left=left))
            continue
        legs.append(PoolLeg(slot, tx, order, kind, disc in data.POOL_EXACT_OUT, base, quote_user, pre, post, trader,
                            lq, lb, direct=outer == PUMP_AMM))
    return legs


CURVE_COLS = """slot, tx_index, outer_ix, inner_ix, variant, is_buy, t, q, fee, creator_fee, fee_bps, creator_bps,
                vt, vq, rt, rq, trader, arg_budget, arg_max_cost, arg_min_tokens, arg_min_quote_out, outer_program"""
POOL_COLS = """kind, slot, tx_index, outer_ix, inner_ix, parent_ix_disc, b, vault, vq, e, lp_bps, protocol_bps, creator_bps,
               cashback_bps, base, e_delta, quote_user, limit_quote, limit_base, boost_left, lp_amount, lp_supply, trader,
               outer_program"""


def _want(con, windows):
    con.execute("CREATE OR REPLACE TEMP TABLE want (k VARCHAR, lo UBIGINT, hi UBIGINT)")
    con.executemany("INSERT INTO want VALUES (?, ?, ?)", [(k, int(max(0, lo)), int(hi)) for k, (lo, hi) in windows.items()])


def _pool_legs_by_pool(con, want="want"):
    """{pool: legs} for the pools and windows in the `want` table. Rows are streamed and turned
    into legs one pool at a time, so raw rows of busy pools never pile up in memory."""
    out, cur, rows = {}, None, []
    res = con.execute(f"""SELECT e.pool AS k, {POOL_COLS} FROM pool e JOIN {want} w ON w.k = e.pool
                          WHERE e.slot BETWEEN w.lo AND w.hi
                          ORDER BY k, e.slot, e.tx_index, e.outer_ix, e.inner_ix""")
    while True:
        batch = res.fetchmany(50_000)
        for r in batch:
            if r[0] != cur:
                if rows:
                    out[cur] = _pool_legs(rows)
                cur, rows = r[0], []
            rows.append(r[1:])
        if not batch:
            break
    if rows:
        out[cur] = _pool_legs(rows)
    return out


def load_streams(con, windows):
    """Token streams for {mint: (lo, hi)}: curve legs with slot <= hi, and the legs of the mint's
    own migration pool (canonical, SOL quote) with lo <= slot <= hi.

    Curve legs are kept from the create on (filters such as "buyers since create" need them and a
    curve's life is short); pool legs only inside the window, because busy pools have millions of
    events. Each pool leg carries its own logged pre-state, so nothing before lo is needed.
    """
    if isinstance(windows, (list, tuple)):
        windows = {m: (0, 2**62) for m in windows}
    if not windows:
        return {}
    _want(con, windows)
    info = {r[0]: r for r in con.execute("""
        SELECT m.mint, m.create_slot, m.creator, m.complete_slot, p.pool, p.create_slot
        FROM mints m JOIN want w ON w.k = m.mint
        LEFT JOIN pools p ON p.pool = m.pool AND p.quote_class = 'sol'""").fetchall()}
    curve = {}
    for r in con.execute(f"""SELECT c.mint, {CURVE_COLS} FROM curve c JOIN want w ON w.k = c.mint WHERE c.slot <= w.hi
                             ORDER BY c.mint, slot, tx_index, outer_ix, inner_ix""").fetchall():
        curve.setdefault(r[0], []).append(r[1:])
    con.execute("""CREATE OR REPLACE TEMP TABLE want_pool AS
                   SELECT p.pool AS k, w.lo, w.hi FROM want w JOIN mints m ON m.mint = w.k
                   JOIN pools p ON p.pool = m.pool AND p.quote_class = 'sol'""")
    by_pool = _pool_legs_by_pool(con, "want_pool")
    out = {}
    for m in windows:
        if m not in info:
            continue
        _, cslot, creator, complete_slot, pool, pool_slot = info[m]
        out[m] = Stream(m, cslot, creator, _curve_legs(curve.pop(m, [])), by_pool.pop(pool, []),
                        pool_open_slot=pool_slot, complete_slot=complete_slot, pool=pool)
    return out


def load_pool_streams(con, windows):
    """Pool streams for {pool: (lo, hi)} (SOL markets of either orientation): pool legs with
    lo <= slot <= hi. Stream.mint is the token mint when known, else the pool address."""
    if not windows:
        return {}
    _want(con, windows)
    info = {r[0]: r for r in con.execute("""
        SELECT p.pool, COALESCE(p.mint, p.pool), p.create_slot, COALESCE(m.creator, p.coin_creator), p.orientation
        FROM pools p JOIN want w ON w.k = p.pool LEFT JOIN mints m ON m.mint = p.mint
        WHERE p.quote_class IN ('sol', 'reversed')""").fetchall()}
    legs = _pool_legs_by_pool(con)
    out = {}
    for pool in windows:
        if pool not in info:
            continue
        _, mint, cslot, creator, orientation = info[pool]
        out[pool] = Stream(mint, cslot, creator, [], legs.pop(pool, []), pool_open_slot=cslot,
                           pool=pool, orientation=orientation)
    return out
