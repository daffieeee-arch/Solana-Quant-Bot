"""E74: curves with PUMP as quote (plan section 5). Universe: mints with quote_mint = PUMP, not Mayhem, created in the
window and at or after the PUMP/SOL reference pool's creation; trigger e10. The curve legs are in PUMP units (the
store's q for a non-SOL quote), so entries and marks run with the network cost at 0 and a PUMP budget, and the
conversion to SOL comes after (plan v0.2: the only PUMP/SOL pool in the store, 5r9N5yEc..., is 0.005 SOL deep and not
tradable; its mid price e / b is a reference only):

  budget_pump   size_sol / median mid price (lamports per PUMP unit) over the window's pool trades (fixed per window);
  SOL cost      cost_pump x price at the entry (last pool trade at or before the entry slot) x (1 + SWAP) + 2 x tx cost;
  SOL exit      exit_pump x price at the exit x (1 - SWAP) - 2 x tx cost;
  SWAP          0.30 % per side, a fixed model assumption for the SOL <-> PUMP leg (not measured in the store).
"""

from .. import runners as R
from ..common import SOL
from . import harness as H

PUMP = "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn"
REF_POOL = "5r9N5yEcKkF5BGiCQ1L3FCmdBKidTVFQKsxNunfspNKT"
REF_POOL_CREATE = 447_266_123
SWAP = 0.003


def run(con, lo, hi, clock, size_sol, cost_tx, out_dir):
    tok = H.tok_sql_for_mints(f"m.quote_mint = '{PUMP}' AND NOT COALESCE(m.mayhem, false) AND m.create_slot >= {lo} AND m.create_slot < {hi} "
                              f"AND m.create_slot >= {REF_POOL_CREATE}")
    hi_ext = H.prepare(con, lo, hi, clock, tok)
    # the reference price: lamports per PUMP unit at every pool trade (normal pool: e = SOL side incl. virtual, b = PUMP side)
    con.execute(f"""CREATE OR REPLACE TEMP TABLE ref AS
        SELECT slot, slot * 1000000000 + tx_index * 100000 + outer_ix * 1000 + inner_ix AS ord, (e - COALESCE(vq, 0))::DOUBLE / b AS price
        FROM pool WHERE pool = '{REF_POOL}' AND kind IN ('buy', 'sell') AND b > 0 AND slot < {hi_ext}""")
    med = con.execute(f"SELECT median(price) FROM ref WHERE slot >= {lo} AND slot < {hi}").fetchone()[0]
    if med is None:
        med = con.execute(f"SELECT arg_max(price, ord) FROM ref WHERE slot < {lo}").fetchone()[0]
    if not med:
        raise SystemExit("E74: no PUMP/SOL reference price in or before the window")
    budget_pump = int(round(size_sol * SOL / med))
    H.moments_from(con, H.e10_sql(), clock)
    saved = R.SIZE
    R.SIZE = budget_pump
    try:
        H.simulate(con, 0, hi_ext, clock)
    finally:
        R.SIZE = saved
    # SOL conversion at the entry and the exit (ASOF: the last reference trade at or before the slot)
    con.execute(f"""CREATE OR REPLACE TEMP TABLE ex AS
        WITH r AS (SELECT slot, arg_max(price, ord) AS price FROM ref GROUP BY slot)
        SELECT e.*, re.price AS price_entry, rx.price AS price_exit, {med} AS price_median, {budget_pump} AS budget_pump,
               e.cost AS cost_pump, e.exit_net AS exit_pump,
               (e.cost * re.price * (1 + {SWAP}) + 2 * {cost_tx})::HUGEINT AS cost_sol,
               (e.exit_net * rx.price * (1 - {SWAP}) - 2 * {cost_tx})::HUGEINT AS exit_sol
        FROM ex e
        ASOF LEFT JOIN r re ON e.entry_slot >= re.slot
        ASOF LEFT JOIN r rx ON e.exit_slot >= rx.slot""")
    # the harness columns cost / exit_net are in PUMP for this probe: the report reads cost_sol / exit_sol instead
    return H.write(con, out_dir, "e74", lo, size_sol, "e74", {"budget_pump": budget_pump, "price_median_lamports_per_unit": med,
                                                                 "swap_per_side": SWAP, "ref_pool": REF_POOL, "units": "cost/exit_net in PUMP; cost_sol/exit_sol in lamports"})
