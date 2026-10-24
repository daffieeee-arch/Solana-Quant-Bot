"""Breadth probes E71-E74 (research/breedte-proeven-plan-2026-10-24.md, roadmap #167): one harness for four
pre-registered stop/go probes on dev 1033-1046. Each probe supplies a tok table (the keys: mints or pools) and a
moments table (key, moment, t); the harness does the rest with wallets.runners (the engine's venue arithmetic):

  entry   the first fillable trade with slot >= t + D_SLOTS (3 slots, fixed), filled at its pre-state with our own
          impact, the row's venue fees and the base scenario's network cost (plan section 1);
  marks   net SOL of a full sell at the state after every later trade within H6 (curve, then the pool; pool marks
          include Withdraw / Deposit and are capped at the pool's real SOL: drain-aware);
  exit    one rule, pre-registered: trail30 (first mark <= 70 % of the highest earlier mark) or the stop (first mark
          <= 50 % of the cost), whichever comes first; the sell is at the first mark with slot >= trigger slot +
          D_SLOTS; without a trigger (or without a later mark) the last mark within H6. A one-step drain triggers on
          the drained state: the rug is kept.

Dev only: every window ends before the hold-out (common.guard). Nothing here is a strategy input.
"""

import json
import os
import time

import pyarrow as pa
import pyarrow.parquet as pq

from .. import runners as R
from ..common import HOLDOUT_START, SOL, Clock, connect, git_commit, guard
from ..markout import ms_case

D_SLOTS = 3
TRAIL = 0.7
STOP = 0.5
TOK_COLS = "key, mint, create_slot, creator, mayhem, complete_slot, has_curve, pool, orientation, pool_slot"


def tok_sql_for_mints(where):
    """The runners tok shape for mints rows matching `where` (their first SOL / reversed pool, when any)."""
    return f"""
        SELECT m.mint AS key, m.mint, m.create_slot, m.creator, m.mayhem, m.complete_slot, true AS has_curve, p.pool, p.orientation, p.pool_slot
        FROM mints m LEFT JOIN (
            SELECT mint, arg_min(pool, (CASE WHEN quote_class = 'sol' THEN 0 ELSE 1 END, create_slot)) AS pool,
                   arg_min(orientation, (CASE WHEN quote_class = 'sol' THEN 0 ELSE 1 END, create_slot)) AS orientation,
                   min(create_slot) AS pool_slot
            FROM pools WHERE quote_class IN ('sol', 'reversed') GROUP BY mint) p ON p.mint = m.mint
        WHERE {where}"""


def prepare(con, lo, hi, clock, tok_sql, mayhem_curve=False):
    """tok from the probe's SQL, then the legs (runners.legs) and trades_all. Returns hi_ext."""
    guard(hi)
    con.execute(f"CREATE OR REPLACE TEMP TABLE tok AS SELECT {TOK_COLS} FROM ({tok_sql}) q")
    hi_ext = R.horizon(con, hi, clock)
    R.legs(con, lo, hi_ext, mayhem_curve=mayhem_curve)
    con.execute("""CREATE OR REPLACE TEMP TABLE trades_all AS
        SELECT key, ord, slot, is_buy, sol, tokens, trader, fee_payer, farmer, tx_index FROM cl
        UNION ALL SELECT key, ord, slot, is_buy, sol, tokens, trader, fee_payer, farmer, tx_index FROM pl WHERE trade""")
    R.progress(f"probe tok {R._count(con, 'tok')}, curve legs {R._count(con, 'cl')}, pool legs {R._count(con, 'pl')}")
    return hi_ext


def e10_sql(moment="e10"):
    """The first trade >= create + 10 slots of every tok key with a curve (the x10 base-rate entry)."""
    return f"""SELECT a.key, '{moment}' AS moment, min(a.slot) AS t FROM trades_all a JOIN tok t USING (key)
               WHERE t.has_curve AND a.slot >= t.create_slot + 10 GROUP BY 1, 2"""


def moments_from(con, moments_sql, clock):
    """mom (runners' shape) from a (key, moment, t) query, with the fixed 3-slot entry delay and the 1 h / 6 h horizons."""
    con.execute(f"CREATE OR REPLACE TEMP TABLE moments AS SELECT key, moment, t::BIGINT AS t FROM ({moments_sql}) q")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE mom AS
        SELECT m.key, m.moment, m.t, {D_SLOTS}::BIGINT AS d_slots,
               greatest(1, round({R.H1_MS} / ({ms_case(clock, 'm.t')})))::BIGINT AS h1, greatest(1, round({R.H6_MS} / ({ms_case(clock, 'm.t')})))::BIGINT AS h6,
               greatest(1, round(60000 / ({ms_case(clock, 'm.t')})))::BIGINT AS m1s, greatest(1, round(300000 / ({ms_case(clock, 'm.t')})))::BIGINT AS m5s
        FROM moments m""")
    R.progress(f"probe moments {R._count(con, 'mom')}: " + str(con.execute("SELECT moment, count(*) FROM mom GROUP BY 1 ORDER BY 1").fetchall()))


def simulate(con, cost_tx, hi_ext, clock, extra_exit_sql=None):
    """runners.entries + marks, then the exit rule -> TEMP TABLE ex (one row per entry).

    extra_exit_sql: optional query (key, moment, t, ord_cut) with an earlier forced exit per entry (E71's agent-free
    cell: the sell at the last mark before ord_cut); applied as a second column set, the primary exit stays."""
    R.entries(con, cost_tx)
    R.marks(con, cost_tx, hi_ext)
    con.execute(f"""CREATE OR REPLACE TEMP TABLE w AS
        SELECT *, max(net) OVER (PARTITION BY key, moment, t ORDER BY ord ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS pmax FROM mk""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE trig AS
        SELECT key, moment, t, min(ord) AS ord_trig FROM w WHERE net <= {TRAIL} * pmax OR net <= {STOP} * cost GROUP BY 1, 2, 3""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE ex AS
        WITH tr AS (
            SELECT g.key, g.moment, g.t, g.ord_trig, m.slot AS trig_slot, CASE WHEN m.net <= {STOP} * m.cost THEN 'stop' ELSE 'trail' END AS reason
            FROM trig g JOIN mk m ON m.key = g.key AND m.moment = g.moment AND m.t = g.t AND m.ord = g.ord_trig),
        sell AS (
            SELECT m.key, m.moment, m.t, arg_min(m.net, m.ord) AS net_sell, min(m.ord) AS ord_sell, arg_min(m.slot, m.ord) AS slot_sell
            FROM mk m JOIN tr ON tr.key = m.key AND tr.moment = m.moment AND tr.t = m.t WHERE m.slot >= tr.trig_slot + {D_SLOTS} GROUP BY 1, 2, 3),
        last AS (
            SELECT key, moment, t, arg_max(net, ord) AS net_last, max(ord) AS ord_last, arg_max(slot, ord) AS slot_last, count(*) AS n_marks,
                   max(net) AS max_net, max(net) FILTER (WHERE slot <= entry_slot + h1) AS max_net_1h
            FROM mk GROUP BY 1, 2, 3)
        SELECT e.key, e.moment, e.t, e.d_slots, e.entry_ord, e.entry_slot, e.venue AS entry_venue, e.orientation, e.depth, e.tokens, e.cost,
               COALESCE(s.net_sell, l.net_last) AS exit_net, COALESCE(s.ord_sell, l.ord_last) AS exit_ord, COALESCE(s.slot_sell, l.slot_last) AS exit_slot,
               CASE WHEN s.net_sell IS NOT NULL THEN tr.reason WHEN tr.reason IS NOT NULL THEN 'end_after_' || tr.reason ELSE 'end' END AS reason,
               tr.trig_slot, l.n_marks, l.max_net, l.max_net_1h,
               strftime(make_timestamp(b.block_time * 1000000), '%Y-%m-%d') AS day, t.mayhem, t.creator, t.pool
        FROM ent e JOIN last l USING (key, moment, t) LEFT JOIN tr USING (key, moment, t) LEFT JOIN sell s USING (key, moment, t)
             JOIN tok t ON t.key = e.key LEFT JOIN blocks b ON b.slot = e.entry_slot""")
    if extra_exit_sql:
        con.execute(f"""CREATE OR REPLACE TEMP TABLE cut AS SELECT key, moment, t, ord_cut FROM ({extra_exit_sql}) q""")
        con.execute("""CREATE OR REPLACE TEMP TABLE ex2 AS
            WITH pre AS (
                SELECT m.key, m.moment, m.t, arg_max(m.net, m.ord) AS net_pre, max(m.ord) AS ord_pre, arg_max(m.slot, m.ord) AS slot_pre
                FROM mk m JOIN cut c ON c.key = m.key AND c.moment = m.moment AND c.t = m.t WHERE m.ord < c.ord_cut GROUP BY 1, 2, 3)
            SELECT e.*, c.ord_cut,
                   CASE WHEN c.ord_cut IS NOT NULL AND c.ord_cut <= e.exit_ord THEN COALESCE(p.net_pre, 0) ELSE e.exit_net END AS exit_net_cut,
                   CASE WHEN c.ord_cut IS NOT NULL AND c.ord_cut <= e.exit_ord THEN CASE WHEN p.net_pre IS NULL THEN 'cut_no_mark' ELSE 'cut' END ELSE e.reason END AS reason_cut
            FROM ex e LEFT JOIN cut c USING (key, moment, t) LEFT JOIN pre p USING (key, moment, t)""")
        con.execute("CREATE OR REPLACE TEMP TABLE ex AS SELECT * FROM ex2")
    R.progress(f"probe exits {R._count(con, 'ex')}: " + str(con.execute("SELECT reason, count(*) FROM ex GROUP BY 1 ORDER BY 1").fetchall()))


def write(con, out_dir, name, lo, size_sol, probe, extra=None):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{name}-{lo}.parquet")
    con.execute(f"COPY (SELECT '{probe}' AS probe, {size_sol} AS size_sol, * FROM ex) TO '{path}' (FORMAT PARQUET)")
    info = {"probe": probe, "lo": lo, "size_sol": size_sol, "rows": R._count(con, "ex"), "commit": git_commit(), "written_unix": int(time.time()),
            "d_slots": D_SLOTS, "trail": TRAIL, "stop": STOP, "h6_ms": R.H6_MS, **(extra or {})}
    with open(os.path.join(out_dir, f"run-{name}-{lo}.json"), "w") as f:
        json.dump(info, f, indent=1)
    return path, info


def open_store(store, memory, threads):
    con = connect(store or R.os.environ.get("LAB_STORE", connect.__defaults__[0]), memory=memory or "1GB", threads=threads or 2)
    return con, Clock.from_store(con)


def with_size(size_sol, fn):
    """Run fn() with runners.SIZE set to size_sol (the module global the runners functions read at call time)."""
    saved = R.SIZE
    R.SIZE = int(round(size_sol * SOL))
    try:
        return fn()
    finally:
        R.SIZE = saved
