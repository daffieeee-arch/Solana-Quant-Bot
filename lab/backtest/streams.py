"""Load per-token event streams from a store (DuckDB file built by store.py)."""

import duckdb

from . import venues as V
from .replay import CurveLeg, PoolLeg, Stream
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
         budget, max_cost, min_tokens, min_out) in rows:
        if is_buy:
            pre = Curve(vt + t, vq - q, rt + t, rq - q, fbps, cbps)
        else:
            pre = Curve(vt - t, vq + q, rt - t, rq + q, fbps, cbps)
        post = Curve(vt, vq, rt, rq, fbps, cbps)
        legs.append(CurveLeg(slot, tx, (slot, tx, oix, iix), is_buy, t, q, fee + cfee, variant, pre, post, trader,
                             budget, max_cost, min_tokens, min_out))
    return legs


def _pool_legs(rows):
    legs = []
    for (kind, slot, tx, oix, iix, disc, b, e, lpb, pb, cb, base, qg, qn, lq, lb, b_after, e_after, left, trader) in rows:
        if kind == "boost":
            pre = Pool(b_after + base, e_after - qg, lpb or 0, pb or 0, cb or 0)
            post = Pool(b_after, e_after, lpb or 0, pb or 0, cb or 0)
            legs.append(PoolLeg(slot, tx, (slot, tx, oix, iix), "boost", False, base, qg, pre, post, trader, boost_left=left))
            continue
        pre = Pool(b, e, lpb, pb, cb)
        exact_out = disc in ("66063d1201daebea", "b817ee6167c5d33d")
        if kind == "buy":
            f = V.pool_buy_exact_out(pre, base) if exact_out else V.pool_buy_exact_in(pre, lq)
        else:
            f = V.pool_sell(pre, base)
        post = f.state if f else pre
        legs.append(PoolLeg(slot, tx, (slot, tx, oix, iix), kind, exact_out, base, qn, pre, post, trader, lq, lb))
    return legs


CURVE_COLS = """slot, tx_index, outer_ix, inner_ix, variant, is_buy, t, q, fee, creator_fee, fee_bps, creator_bps,
                vt, vq, rt, rq, trader, arg_budget, arg_max_cost, arg_min_tokens, arg_min_quote_out"""
POOL_COLS = """kind, slot, tx_index, outer_ix, inner_ix, parent_ix_disc, b, e, lp_bps, protocol_bps, creator_bps,
               base, quote_gross, quote_net, limit_quote, limit_base, b_after, e_after, boost_left, trader"""


def load_streams(con, windows):
    """Streams for {mint: (lo, hi)}: curve legs with slot <= hi, pool legs with lo <= slot <= hi.

    Curve legs are kept from the create on (filters such as "buyers since create" need them and a
    curve's life is short); pool legs only inside the window, because busy pools have millions of
    events. Each pool leg carries its own logged pre-trade state, so nothing before lo is needed.
    """
    if isinstance(windows, (list, tuple)):
        windows = {m: (0, 2**62) for m in windows}
    if not windows:
        return {}
    con.execute("CREATE OR REPLACE TEMP TABLE want (mint VARCHAR, lo UBIGINT, hi UBIGINT)")
    con.executemany("INSERT INTO want VALUES (?, ?, ?)", [(m, int(max(0, lo)), int(hi)) for m, (lo, hi) in windows.items()])
    info = {r[0]: r for r in con.execute("""
        SELECT m.mint, m.create_slot, m.creator, m.complete_slot, p.pool, p.create_slot
        FROM mints m JOIN want USING (mint) LEFT JOIN pools p ON p.mint = m.mint""").fetchall()}
    curve = {}
    for r in con.execute(f"""SELECT c.mint, {CURVE_COLS} FROM curve c JOIN want w USING (mint) WHERE c.slot <= w.hi
                             ORDER BY c.mint, slot, tx_index, outer_ix, inner_ix""").fetchall():
        curve.setdefault(r[0], []).append(r[1:])
    pools = {}
    for r in con.execute(f"""SELECT p.mint, {POOL_COLS} FROM pool e JOIN pools p USING (pool) JOIN want w ON w.mint = p.mint
                             WHERE e.slot BETWEEN w.lo AND w.hi
                             ORDER BY p.mint, e.slot, e.tx_index, e.outer_ix, e.inner_ix""").fetchall():
        pools.setdefault(r[0], []).append(r[1:])
    out = {}
    for m in windows:
        if m not in info:
            continue
        _, cslot, creator, complete_slot, pool, pool_slot = info[m]
        out[m] = Stream(m, cslot, creator, _curve_legs(curve.pop(m, [])), _pool_legs(pools.pop(m, [])),
                        pool_open_slot=pool_slot, complete_slot=complete_slot)
    return out
