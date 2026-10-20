"""Spec B: markout curves on every signal of a run (week3-specs-markout-2026-10-12.md).

For each distinct signal (family, segment, venue, token, t) in a run's parts and each delay
d in DELAYS, the entry is the venue's state after its last leg with slot <= t + d (the end of
slot t + d, the replay's q = 1), and the markout at horizon h is taken from the state after the
last leg with slot <= t + d + slots(h), with slots(h) = ceil(h / ms_per_slot of the entry's
epoch) from the store meta.

  gross_h = mid_h / mid_entry - 1        mid = e / b (normal pool), b / e (reversed), vq / vt (curve)
  net_h   = SOL back / (SIZE + 2 x network cost per order) - 1

where SOL back is a round trip simulated with the engine's venue functions: a buy of SIZE at the
entry state (pool_buy_exact_in / rev_buy_token / curve_buy_exact_in) and a sell of the tokens
received at the horizon state (pool_sell_capped / rev_sell_token / curve_sell), each against
the real reserves of its moment, plus the network cost of the costs.py scenario (base) on both
legs. A horizon past the store end or past the venue's last leg keeps the last known state and
is flagged `cut`; a horizon with no state at all gives NULL. Pool post-states follow
`streams.pool_leg` of the engine (buy / sell / boost: b -/+ base and e + e_delta; withdraw /
deposit: vault + the last virtual quote -/+ quote_user), taken after the last leg at or before the
target slot. States are computed only at the target slots (each leg is mapped to the first target
at or after it, one aggregation per pool x target, a forward fill over the targets): no state per
slot is materialised and no window runs over all legs (the first NAS run spilled 7 GB there).

Graduation is not death: a curve signal whose curve is complete at the entry or at a horizon is
valued on the mint's migration pool (its SOL-quote PumpSwap pool, the state after the pool's last
leg <= that slot, bought or sold through the pool's own fill functions); before the pool has a
state there is no market and the row is NULL. `ms_per_slot` is the measured ms per slot of the
signal's epoch (store meta) and `regime` its label rounded to 10 ms (320ms / 270ms on dev, 200ms after
Alpenglow; spec section 3): nothing about regimes is hardcoded.

Signals are deduplicated over exit variants (tp4 / tp6, trail20 / trail35 share their trigger);
`variants` keeps the list. N2 rows carry ref_mint / ref_t so the report can pair them with
their trigger. Farm (organic / flagged) and the trigger's day come along for the K1-K3 rows.

  LAB_STORE=<dev2> LAB_ENGINE=<engine dir> python -m wallets.markout --parts DIR --run NAME
      [--families F7,N2_F7] [--from-slot A --to-slot B] [--out DIR] [--cfg week2-defaults.yaml] [--delay-ms 600]

--delay-ms adds, beside DELAYS, the slot delay of that many ms per epoch through the store's Clock (the engine's rule for
E20's d_ms 600: 2 slots at 268 ms, 3 at 218 ms); the run JSON lists it per epoch (delay_slots_per_epoch).

Writes markout-<run>.parquet (one row per signal x d x horizon) and run-markout-<run>.json.
Slots stay before the hold-out (common.guard); the one exception is E20's finished clean run under its clean.lock
(--clean-lock, clean_lock(): that run's parts, store and engine snapshot only; the F7 mechanism pre-registration). Heavy on a full run (ASOF join against the pool
and curve tables): NAS; one day on the VPS as a smoke test.
"""

# no-quote-filter: consumed data, conclusions audited 2026-10-18 (lab/reports/2026-10-18-audit-niet-sol-quote.md)
import argparse
import glob
import hashlib
import json
import math
import os
import sys
import time

import pyarrow as pa
import pyarrow.parquet as pq

from .common import EPOCH_SLOTS as EPOCH, OUT_ROOT, REF_MS, SOL, Clock, connect, git_commit, guard, regime_label



def engine():
    """The store-v2 engine's venues and costs modules, loaded under their own package name from
    LAB_ENGINE (a directory holding backtest/), so the repo's own `backtest` package never shadows it."""
    path = os.environ.get("LAB_ENGINE")
    if not path:
        raise SystemExit("markout needs the store-v2 engine: set LAB_ENGINE to a directory holding backtest/")
    import importlib
    import importlib.util
    if "lab_engine_backtest" not in sys.modules:
        spec = importlib.util.spec_from_file_location("lab_engine_backtest", os.path.join(path, "backtest", "__init__.py"),
                                                      submodule_search_locations=[os.path.join(path, "backtest")])
        pkg = importlib.util.module_from_spec(spec)
        sys.modules["lab_engine_backtest"] = pkg
        spec.loader.exec_module(pkg)
    venues = importlib.import_module("lab_engine_backtest.venues")
    costs = importlib.import_module("lab_engine_backtest.costs")
    if not hasattr(venues, "rev_buy_token") or not hasattr(venues, "pool_sell_capped"):
        raise SystemExit(f"{path} is not the store-v2 engine (no rev_buy_token / pool_sell_capped)")
    return venues, costs


V = None

DELAYS = (1, 2)
# Transaction-level entries (spec M-a, research/spec-rust-ma-instap-per-transactie-2026-10-19.md; --tx-delays): the
# entry state is the post-state of a leg of the signal's own pool instead of the state at a slot's end. Codes in the
# `d` column: 1000 + k = the k-th leg of the pool after the trigger leg inside the trigger slot t (capped at the slot's
# last leg; k = 0 is the trigger leg itself = the upper bound that needs a same-slot fill); 2001 = the first leg of the
# pool in slot t + 1 (the earliest reaction on `processed` after one other leg; the slot's end of t when t + 1 has no
# leg). Every tx-level entry is the state at the END of that leg's transaction (slot, tx_index): a transaction with
# two legs on the pool (a buy and a sell-back in one transaction: on the one-day smoke 10 % of the first transactions
# of t + 1, 15 % of all transactions in t and t + 1, with a 6-8 % price gap between the legs) is atomic, so an entry
# between its legs is impossible (`tx_end` flags the moved entries). The horizons run from the entry slot (t for 1xxx,
# t + 1 for 2001). The trigger leg is the pool's FIRST leg in
# slot t by the signal's trigger wallet (`trigger_trader` of the parts) of the trigger's kind (a token sell: 'buy' in a
# reversed pool, 'sell' in a normal one), as the backtest picks the slot's first qualifying leg (strategies.f7_signals_sql);
# else the wallet's first leg in t, else the slot's first leg. With --tx-delays the slot delays 0 (the end of t = a fill
# as the first transaction of t + 1, the causal bound without bundles) and 3 (the Alpenglow-era proxy of d_ms 600) are
# added to DELAYS. Pool signals only: curve signals keep DELAYS.
TX_DELAYS = {1000: 0, 1001: 1, 1003: 3, 1010: 10, 2001: None}
TX_SLOT_DELAYS = (0, 3)  # slot delays added beside DELAYS with --tx-delays


def delays(tx_delays=False):
    """The slot delays of a run: DELAYS, plus TX_SLOT_DELAYS with --tx-delays."""
    return tuple(sorted(set(DELAYS) | set(TX_SLOT_DELAYS))) if tx_delays else DELAYS


def delay_label(d):
    """Human label of a `d` value: slots, or the transaction-level code."""
    if d < 1000:
        return f"{d} slot{'s' if d != 1 else ''}"
    if d >= 2000:
        return "t+1, first tx"
    return f"t, trigger tx + {d - 1000}"
HORIZONS_MS = (("1s", 1_000), ("10s", 10_000), ("1m", 60_000), ("5m", 300_000), ("15m", 900_000), ("1h", 3_600_000), ("6h", 21_600_000))
SIZE = SOL // 2
DEFAULTS = os.environ.get("LAB_DEFAULTS", "/home/chupa/Solana-project/data-old-faithful-one/lab/research/week2-defaults.yaml")
SIG_COLS = ("family", "segment", "venue", "orientation", "mint", "pool", "t", "day", "farm")

PHASES = {}   # phase name -> seconds, filled by progress() for the run json
_T0 = [time.time()]


def progress(phase, rows=None, note=""):
    """One flushed stderr line per finished phase (the NAS log stayed empty for 3 h 40 min on the full f7rev run:
    nothing was printed before the end): time of day, elapsed, phase, rows, seconds since the previous line."""
    now = time.time()
    dt = now - _T0[0]
    _T0[0] = now
    PHASES[phase] = round(PHASES.get(phase, 0) + dt, 1)
    print(f"{time.strftime('%H:%M:%S')} {phase}: {rows if rows is not None else ''} rows {dt:,.1f}s {note}".replace("  ", " "),
          file=sys.stderr, flush=True)


def _count(con, table):
    return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


# --- signals from the parts -----------------------------------------------------------------------

def load_signals(con, parts_dir, families=None, lo=None, hi=None, clock=None):
    """One row per distinct signal; `variants` lists the exit variants that share it."""
    files = sorted(glob.glob(os.path.join(parts_dir, "*.parquet")))
    if not files:
        raise SystemExit(f"no parquet parts in {parts_dir}")
    lit = "[" + ",".join(f"'{f}'" for f in files) + "]"
    where = ["t IS NOT NULL"]
    if families:
        where.append("family IN (" + ",".join(f"'{f}'" for f in families) + ")")
    if lo is not None:
        where.append(f"t >= {lo}")
    if hi is not None:
        where.append(f"t < {hi}")
    tt = "trigger_trader" if "trigger_trader" in [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet({lit}, union_by_name = true)").fetchall()] else "NULL::VARCHAR"
    per_epoch = clock.per_epoch if clock and clock.per_epoch else {}
    ms_case = ("CASE t // {e} " + " ".join(f"WHEN {ep} THEN {ms!r}" for ep, ms in sorted(per_epoch.items())) + f" ELSE {REF_MS!r} END").format(e=EPOCH) \
        if per_epoch else repr(REF_MS)
    labels = {ep: regime_label(ms) for ep, ms in per_epoch.items()}
    reg_case = ("CASE t // {e} " + " ".join(f"WHEN {ep} THEN '{lab}'" for ep, lab in sorted(labels.items())) + f" ELSE '{regime_label(REF_MS)}' END").format(e=EPOCH) \
        if labels else f"'{regime_label(REF_MS)}'"
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE sig AS
      SELECT row_number() OVER (ORDER BY family, segment, mint, t, pool) AS sid, *, ({ms_case})::DOUBLE AS ms_per_slot, {reg_case} AS regime
      FROM (SELECT family, segment, venue, orientation, mint, pool, t, any_value(day) AS day, any_value(farm) AS farm,
                   any_value(ref_mint) AS ref_mint, any_value(ref_t) AS ref_t, any_value({tt}) AS trigger_trader,
                   list_sort(list_distinct(list(variant))) AS variants,
                   CASE WHEN venue = 'curve' THEN mint ELSE pool END AS key
            FROM read_parquet({lit}, union_by_name = true) WHERE {' AND '.join(where)}
            GROUP BY family, segment, venue, orientation, mint, pool, t)""")
    creator_prior_pools(con)
    n = _count(con, "sig")
    progress("signals", n, f"from {len(files)} parts")
    return n


SYSTEM_PROGRAM = "11111111111111111111111111111111"


def creator_prior_pools(con):
    """sig.creator_prior_pools: the number of earlier pools of the signal pool's coin_creator within the store (the
    point-in-time creator class of clean_reports / E41: 0 = first pool, 1-9, 10-99, >= 100), -1 when the creator is the
    system program (unknown), NULL for a curve signal, a pool outside the store's pools table or a store without
    coin_creator. Descriptive only: a signal column copied to every markout row."""
    con.execute("ALTER TABLE sig ADD COLUMN creator_prior_pools INTEGER")
    try:
        cols = [r[0] for r in con.execute("DESCRIBE pools").fetchall()]
    except duckdb.Error:
        return
    if "coin_creator" not in cols or "create_slot" not in cols:
        return
    con.execute(f"""
      UPDATE sig SET creator_prior_pools = c.n FROM (
        WITH po AS (SELECT p.pool, p.coin_creator, p.create_slot FROM pools p SEMI JOIN (SELECT DISTINCT pool FROM sig WHERE pool IS NOT NULL) k ON k.pool = p.pool)
        SELECT po.pool, CASE WHEN po.coin_creator = '{SYSTEM_PROGRAM}' THEN -1 ELSE count(q.pool) END AS n
        FROM po LEFT JOIN pools q ON q.coin_creator = po.coin_creator AND q.create_slot < po.create_slot AND po.coin_creator <> '{SYSTEM_PROGRAM}'
        GROUP BY po.pool, po.coin_creator) c
      WHERE sig.pool = c.pool""")


# --- venue states at the end of each slot ------------------------------------------------------------

def slot_bounds(con, clock, tx_delays=False):
    """[first signal slot, last horizon slot] of the signals: states outside it are never asked for."""
    lo, hi = con.execute("SELECT min(t), max(t) FROM sig").fetchone()
    if lo is None:
        return 0, 0
    ms = min(clock.per_epoch.values()) if clock.per_epoch else clock.ms_per_slot(hi)
    return int(lo), int(hi) + max(delays(tx_delays)) + horizon_slots(ms, HORIZONS_MS[-1][1]) + 1


def pool_legs_sql(lo, hi):
    """Post-states of the pool legs of the signals' pools (and migration pools: the keys of pool_targets) in [lo, hi], per leg, following
    streams.pool_leg: buy / sell from the row (b -/+ base, e + e_delta, virt = vq, the row's fee tiers); boost
    (b - base, e + e_delta, virt = vq) and withdraw / deposit (vault + virt -/+ quote_user, b -/+ base) take the
    fee tiers from the last buy / sell at or before them, and the LP legs their virtual quote from the last buy,
    sell or boost (pool_leg carries ctx["virt"] over a boost too)."""
    return f"""
      WITH keys AS (SELECT DISTINCT key FROM pool_targets),
      raw AS (
        SELECT p.pool AS key, p.slot, p.tx_index, p.outer_ix, p.inner_ix, p.kind, p.b, p.e, p.vault, p.vq, p.base, p.e_delta, p.quote_user,
               p.lp_bps, p.protocol_bps, p.creator_bps, p.cashback_bps, p.trader, p.token_buy, p.sol_amount, p.sol_depth
        FROM pool p SEMI JOIN keys k ON k.key = p.pool WHERE p.slot BETWEEN {lo} AND {hi}),
      trades AS (
        SELECT key, slot, tx_index, outer_ix, inner_ix, CASE WHEN kind = 'sell' THEN b + base ELSE b - base END AS b, e + e_delta AS e,
               COALESCE(vq, 0) AS virt, COALESCE(lp_bps, 0) AS lp_bps, COALESCE(protocol_bps, 0) AS protocol_bps,
               COALESCE(creator_bps, 0) AS creator_bps, COALESCE(cashback_bps, 0) AS cashback_bps, kind, trader, token_buy, sol_amount, sol_depth
        FROM raw WHERE kind IN ('buy', 'sell')),
      virts AS (  -- the virtual quote after every buy, sell or boost (the LP legs take the last one)
        SELECT key, slot, tx_index, COALESCE(vq, 0) AS virt FROM raw WHERE kind IN ('buy', 'sell', 'boost')),
      others AS (
        SELECT o.key, o.slot, o.tx_index, o.outer_ix, o.inner_ix,
               CASE WHEN o.kind = 'deposit' THEN o.b + o.base ELSE o.b - o.base END AS b,
               CASE WHEN o.kind = 'boost' THEN o.e + o.e_delta
                    WHEN o.kind = 'withdraw' THEN o.vault + COALESCE(v.virt, 0) - o.quote_user
                    ELSE o.vault + COALESCE(v.virt, 0) + o.quote_user END AS e,
               CASE WHEN o.kind = 'boost' THEN COALESCE(o.vq, 0) ELSE COALESCE(v.virt, 0) END AS virt,
               COALESCE(t.lp_bps, 0) AS lp_bps, COALESCE(t.protocol_bps, 0) AS protocol_bps, COALESCE(t.creator_bps, 0) AS creator_bps,
               COALESCE(t.cashback_bps, 0) AS cashback_bps, o.kind AS kind, NULL::VARCHAR AS trader, NULL::BOOLEAN AS token_buy,
               NULL::BIGINT AS sol_amount, NULL::BIGINT AS sol_depth
        FROM (SELECT * FROM raw WHERE kind IN ('boost', 'withdraw', 'deposit')) o
        ASOF LEFT JOIN trades t ON t.key = o.key AND o.slot >= t.slot
        ASOF LEFT JOIN virts v ON v.key = o.key AND o.slot >= v.slot)
      SELECT * FROM trades UNION ALL SELECT * FROM others"""


def _per_bucket(con, table, body, nb, phase):
    """CREATE `table` from `body` (a SELECT with {b} for the bucket) for bucket 0, INSERT the other buckets."""
    for b in range(nb):
        verb = f"CREATE OR REPLACE TEMP TABLE {table} AS" if b == 0 else f"INSERT INTO {table}"
        con.execute(f"{verb} {body.format(b=b)}")
        progress(phase, _count(con, table), f"bucket {b + 1}/{nb}" if nb > 1 else "")


def buckets():
    """LAB_MARKOUT_BUCKETS: the ASOF match and the carry-forward window run per key-hash bucket (every step is
    per key, so the union over the buckets is the unbucketed result). The families-v2 run (9x the f7rev signals)
    spilled 93 GiB in one unbucketed pool_at on the NAS; 1 = unbucketed (the smoke and the f7rev run)."""
    n = int(os.environ.get("LAB_MARKOUT_BUCKETS", "1"))
    if n < 1:
        raise SystemExit("LAB_MARKOUT_BUCKETS must be >= 1")
    return n


def match_states(con, lo, hi, tx_delays=False):
    """The venue state at every target slot, without materialising a state per slot: each leg is mapped to the
    first target slot at or after it (ASOF), the last leg per (key, target) wins (one aggregation), and targets
    without a leg since the previous target carry the previous state forward (a window over the targets only).
    The legs are materialised once (one scan of pool / curve); the match and the window run per key-hash bucket
    (buckets()). Writes pool_at (pools and migration pools), curve_at and venue_last."""
    nb = buckets()
    con.execute("""
      CREATE OR REPLACE TEMP TABLE grad_pool AS
      SELECT mint, arg_min(pool, (CASE WHEN quote_class = 'sol' THEN 0 ELSE 1 END, create_slot)) AS pool,
             arg_min(orientation, (CASE WHEN quote_class = 'sol' THEN 0 ELSE 1 END, create_slot)) AS orientation
      FROM pools WHERE quote_class IN ('sol', 'reversed') AND mint IN (SELECT DISTINCT key FROM sig WHERE venue = 'curve') GROUP BY mint""")
    progress("grad_pool", _count(con, "grad_pool"))
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE pool_targets AS
      SELECT key, target, hash(key) % {nb} AS bkt FROM (
        SELECT DISTINCT s.key, tg.target FROM targets tg JOIN sig s ON s.sid = tg.sid WHERE s.venue = 'pool'
        UNION
        SELECT DISTINCT gp.pool, tg.target FROM targets tg JOIN sig s ON s.sid = tg.sid JOIN grad_pool gp ON gp.mint = s.key WHERE s.venue = 'curve')""")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE curve_targets AS
      SELECT key, target, hash(key) % {nb} AS bkt FROM (
        SELECT DISTINCT s.key, tg.target FROM targets tg JOIN sig s ON s.sid = tg.sid WHERE s.venue = 'curve')""")
    progress("targets per venue", _count(con, "pool_targets") + _count(con, "curve_targets"), f"slots [{lo}, {hi}], {nb} buckets")
    con.execute(f"CREATE OR REPLACE TEMP TABLE pool_legs AS SELECT *, hash(key) % {nb} AS bkt FROM ({pool_legs_sql(lo, hi)})")
    progress("pool legs", _count(con, "pool_legs"))
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE curve_legs AS
      SELECT c.mint AS key, c.slot, c.tx_index, c.outer_ix, c.inner_ix, c.vt, c.vq, c.rt, c.rq, c.fee_bps, c.creator_bps, hash(c.mint) % {nb} AS bkt
      FROM curve c SEMI JOIN (SELECT DISTINCT key FROM curve_targets) s ON s.key = c.mint WHERE c.slot BETWEEN {lo} AND {hi}""")
    progress("curve legs", _count(con, "curve_legs"))
    pool_at_sql = """
      WITH legs AS (SELECT * FROM pool_legs WHERE bkt = {b}), kt_b AS (SELECT key, target FROM pool_targets WHERE bkt = {b}),
      hit AS (
        SELECT kt.key, kt.target, arg_max(l.slot, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS slot,
               arg_max(l.b, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS b, arg_max(l.e, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS e,
               arg_max(l.virt, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS virt,
               arg_max(l.lp_bps, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS lp_bps,
               arg_max(l.protocol_bps, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS protocol_bps,
               arg_max(l.creator_bps, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS creator_bps,
               arg_max(l.cashback_bps, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS cashback_bps
        FROM legs l ASOF JOIN kt_b kt ON kt.key = l.key AND l.slot <= kt.target GROUP BY kt.key, kt.target),
      filled AS (
        SELECT kt.key, kt.target,
               last_value(h.slot IGNORE NULLS) OVER w AS slot, last_value(h.b IGNORE NULLS) OVER w AS b, last_value(h.e IGNORE NULLS) OVER w AS e,
               last_value(h.virt IGNORE NULLS) OVER w AS virt, last_value(h.lp_bps IGNORE NULLS) OVER w AS lp_bps,
               last_value(h.protocol_bps IGNORE NULLS) OVER w AS protocol_bps, last_value(h.creator_bps IGNORE NULLS) OVER w AS creator_bps,
               last_value(h.cashback_bps IGNORE NULLS) OVER w AS cashback_bps
        FROM kt_b kt LEFT JOIN hit h ON h.key = kt.key AND h.target = kt.target
        WINDOW w AS (PARTITION BY kt.key ORDER BY kt.target ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW))
      SELECT * FROM filled WHERE slot IS NOT NULL"""
    _per_bucket(con, "pool_at", pool_at_sql, nb, "pool_at (pool legs ASOF targets)")
    curve_at_sql = """
      WITH legs AS (SELECT * FROM curve_legs WHERE bkt = {b}), kt_b AS (SELECT key, target FROM curve_targets WHERE bkt = {b}),
      hit AS (
        SELECT kt.key, kt.target, arg_max(l.slot, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS slot,
               arg_max(l.vt, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS vt, arg_max(l.vq, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS vq,
               arg_max(l.rt, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS rt, arg_max(l.rq, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS rq,
               arg_max(l.fee_bps, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS fee_bps,
               arg_max(l.creator_bps, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS creator_bps
        FROM legs l ASOF JOIN kt_b kt ON kt.key = l.key AND l.slot <= kt.target GROUP BY kt.key, kt.target),
      filled AS (
        SELECT kt.key, kt.target, last_value(h.slot IGNORE NULLS) OVER w AS slot, last_value(h.vt IGNORE NULLS) OVER w AS vt,
               last_value(h.vq IGNORE NULLS) OVER w AS vq, last_value(h.rt IGNORE NULLS) OVER w AS rt, last_value(h.rq IGNORE NULLS) OVER w AS rq,
               last_value(h.fee_bps IGNORE NULLS) OVER w AS fee_bps, last_value(h.creator_bps IGNORE NULLS) OVER w AS creator_bps
        FROM kt_b kt LEFT JOIN hit h ON h.key = kt.key AND h.target = kt.target
        WINDOW w AS (PARTITION BY kt.key ORDER BY kt.target ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW))
      SELECT * FROM filled WHERE slot IS NOT NULL"""
    _per_bucket(con, "curve_at", curve_at_sql, nb, "curve_at (curve legs ASOF targets)")
    if tx_delays:
        sig_creators(con)
        tx_entries(con)  # needs the legs: before they are dropped
    con.execute("DROP TABLE pool_legs; DROP TABLE curve_legs")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE venue_last AS
      SELECT key, max(slot) AS last_slot FROM (
        SELECT p.pool AS key, p.slot FROM pool p SEMI JOIN (SELECT key FROM pool_targets) k ON k.key = p.pool WHERE p.slot BETWEEN {lo} AND {hi}
        UNION ALL
        SELECT c.mint, c.slot FROM curve c SEMI JOIN (SELECT key FROM curve_targets) k ON k.key = c.mint WHERE c.slot BETWEEN {lo} AND {hi})
      GROUP BY key""")
    progress("venue_last", _count(con, "venue_last"))
    return (_count(con, "pool_at"), _count(con, "curve_at"))


def horizon_slots(ms_per_slot, h_ms):
    return int(math.ceil(h_ms / ms_per_slot))


def ms_case(clock, slot_sql):
    """SQL: the clock's ms per slot of the epoch of `slot_sql` (Clock.ms_per_slot: the epoch's value, else the last
    measured earlier epoch, else REF_MS), as a CASE over the measured epochs."""
    per_epoch = clock.per_epoch if clock and clock.per_epoch else {}
    if not per_epoch:
        return repr(REF_MS)
    eps = sorted(per_epoch)
    # epoch e takes per_epoch[e]; an unmeasured epoch takes the last measured one before it (REF_MS below the first)
    parts = [f"WHEN ({slot_sql}) // {EPOCH} < {eps[0]} THEN {REF_MS!r}"]
    for i, e in enumerate(eps):
        nxt = eps[i + 1] if i + 1 < len(eps) else None
        cond = f"({slot_sql}) // {EPOCH} < {nxt}" if nxt is not None else "true"
        parts.append(f"WHEN {cond} THEN {per_epoch[e]!r}")
    return "CASE " + " ".join(parts) + " END"


def delay_slots(clock, delay_ms):
    """The slot delay of `delay_ms` per measured epoch of the store (Clock.slots: max(1, round(ms / ms_per_slot))), and the
    default for an unmeasured epoch (REF_MS). E20 enters at d_ms 600 through the engine's Clock: 2 slots at 268 ms, 3 at 218 ms."""
    per_epoch = {ep: clock.slots(delay_ms, ep * EPOCH) for ep in sorted(clock.per_epoch)} if clock and clock.per_epoch else {}
    return per_epoch, max(1, round(delay_ms / REF_MS))


def build_targets(con, clock, end_slot, tx_delays=False, delay_ms=None):
    """signal x d x (entry, horizons) -> target slot; the entry is horizon '' at t + d. In SQL (the Python
    executemany took 100 s per 190k rows on the NAS): the same arithmetic as horizon_slots, ms of the entry
    slot's epoch, ceil(h_ms / ms). With tx_delays the slot delays are delays(True) and the TX_DELAYS codes are added
    for pool signals: their entry slot is t (1xxx) or t + 1 (2001) and the entry state comes from tx_entries(), not
    from the slot's end."""
    hz = "('', 0), " + ", ".join(f"('{n}', {h})" for n, h in HORIZONS_MS)
    ds = "[" + ", ".join(str(d) for d in (delays(True) + tuple(TX_DELAYS) if tx_delays else DELAYS)) + "]"
    if delay_ms:  # the per-signal delay of delay_ms through the Clock of the signal's epoch, beside DELAYS
        per_epoch, default = delay_slots(clock, delay_ms)
        d_case = ("CASE t // {e} " + " ".join(f"WHEN {ep} THEN {d}" for ep, d in per_epoch.items()) + f" ELSE {default} END").format(e=EPOCH)
        ds = f"list_distinct({ds} || [{d_case}])"
    con.execute(f"CREATE OR REPLACE TEMP TABLE sd AS SELECT sid, unnest({ds})::INTEGER AS d FROM sig")
    dslot = "CASE WHEN d.d >= 2000 THEN 1 WHEN d.d >= 1000 THEN 0 ELSE d.d END"
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE targets AS
      SELECT s.sid::BIGINT AS sid, d.d::INTEGER AS d, hz.h::VARCHAR AS h, hz.h_ms::BIGINT AS h_ms,
             (s.t + {dslot} + CASE WHEN hz.h_ms = 0 THEN 0 ELSE ceil(hz.h_ms / ({ms_case(clock, 's.t + ' + dslot)}))::BIGINT END)::BIGINT AS target
      FROM sig s JOIN sd d USING (sid), (SELECT * FROM (VALUES {hz}) v(h, h_ms)) hz
      {"WHERE s.venue = 'pool' OR d.d IN (" + ", ".join(str(d) for d in DELAYS) + ")" if tx_delays else ""}""")
    n = _count(con, "targets")
    progress("targets", n)
    return n


def tx_entries(con):
    """The entry state per (signal, TX_DELAYS code) from the pool's own legs in slots t and t + 1 (pool_legs, so
    after match_states): the k-th leg after the trigger leg inside t (capped at t's last leg), or the first leg of
    t + 1 (else t's last leg), each taken at the end of that leg's transaction (slot, tx_index): a transaction with
    several legs on the pool (a round trip in one transaction) is atomic, our entry cannot land between its legs
    (Codex review 2026-10-19; on the one-day smoke 10 % of the first transactions of t + 1 had two legs, with a
    6.5 % mid gap between them). `tx_end` flags an entry moved to its transaction's end; tx_stats counts them per
    code. The trigger leg is the trigger wallet's first leg in t that qualifies as the backtest's
    trigger (a token sell >= max(3 SOL, 4 % of the SOL depth), depth >= 150 SOL), else its first token sell, else its
    first leg, else the slot's first leg. Writes tx_entry(sid, d, slot, b, e, virt, bps…, capped); a signal whose
    trigger slot has no leg gets no entry (its tx-level rows stay without a state).
    Writes tx_stats (one row) too: how many pool signals have a leg in t, how often the trigger wallet has more
    than one leg in t (the v1 'last leg' choice would differ), how often it has no token sell in t and how often none
    of its token sells passes the size (fallbacks), how often it sold a smaller amount earlier in t (where v2, without
    the size condition, would differ), how many signals see a token sell by the pool's creator after the trigger leg
    in t (the slot rule's creator veto, which a per-transaction decision cannot see yet), per code how many entries
    are capped at t's last leg, and how many 2001 entries fall back to t's last leg (no leg in t + 1)."""
    ks = ", ".join(f"({d}, {k if k is not None else 'NULL'})" for d, k in TX_DELAYS.items())
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE tx_entry_all AS
      WITH L AS (
        SELECT s.sid, s.t, s.trigger_trader, cr.creator, l.*, row_number() OVER (PARTITION BY s.sid ORDER BY l.slot, l.tx_index, l.outer_ix, l.inner_ix) AS rn,
               l.slot = s.t AND l.trader = s.trigger_trader AS mine, NOT COALESCE(l.token_buy, true) AS tsell,
               NOT COALESCE(l.token_buy, true) AND l.sol_amount >= greatest({3 * SOL}, 0.04 * l.sol_depth) AND l.sol_depth >= {150 * SOL} AS trig_size
        FROM sig s JOIN pool_legs l ON l.key = s.key AND l.slot IN (s.t, s.t + 1)
        LEFT JOIN sig_creator cr ON cr.sid = s.sid WHERE s.venue = 'pool'),
      L2 AS (SELECT *, max(rn) OVER (PARTITION BY sid, slot, tx_index) AS rn_tx_end FROM L),
      R AS (
        SELECT sid, max(rn) FILTER (WHERE slot = t) AS rn_last_t, min(rn) FILTER (WHERE slot = t + 1) AS rn_n1,
               COALESCE(min(rn) FILTER (WHERE mine AND trig_size), min(rn) FILTER (WHERE mine AND tsell), min(rn) FILTER (WHERE mine),
                        min(rn) FILTER (WHERE slot = t)) AS rn_trig,
               max(rn) FILTER (WHERE mine) AS rn_trader_last, min(rn) FILTER (WHERE mine AND tsell) AS rn_first_sell,
               bool_or(mine AND tsell) AS sell_found, bool_or(mine AND trig_size) AS size_found
        FROM L GROUP BY sid),
      R2 AS (
        SELECT r.*, bool_or(l.slot = l.t AND l.rn > r.rn_trig AND l.tsell AND l.trader = l.creator) AS creator_veto
        FROM R r JOIN L l ON l.sid = r.sid GROUP BY ALL),
      E AS (
        SELECT r.sid, c.d, CASE WHEN c.d >= 2000 THEN COALESCE(r.rn_n1, r.rn_last_t) ELSE least(r.rn_trig + c.k, r.rn_last_t) END AS rn,
               CASE WHEN c.d >= 2000 THEN r.rn_n1 IS NULL ELSE r.rn_trig + c.k > r.rn_last_t END AS capped,
               r.rn_trader_last <> r.rn_trig AS trader_multi, NOT COALESCE(r.sell_found, false) AS kind_missing,
               NOT COALESCE(r.size_found, false) AS size_missing, r.rn_first_sell < r.rn_trig AS smaller_before, r.creator_veto
        FROM R2 r, (SELECT * FROM (VALUES {ks}) v(d, k)) c WHERE r.rn_last_t IS NOT NULL),
      E2 AS (  -- the entry lands after the whole transaction of the chosen leg, not between its legs
        SELECT e.*, l.rn_tx_end AS rn_end, l.rn_tx_end <> e.rn AS tx_end FROM E e JOIN L2 l ON l.sid = e.sid AND l.rn = e.rn)
      SELECT e.sid, e.d::INTEGER AS d, l.slot, l.b, l.e, l.virt, l.lp_bps, l.protocol_bps, l.creator_bps, l.cashback_bps,
             e.capped, e.tx_end, e.trader_multi, e.kind_missing, e.size_missing, e.smaller_before, e.creator_veto
      FROM E2 e JOIN L2 l ON l.sid = e.sid AND l.rn = e.rn_end""")
    con.execute("""
      CREATE OR REPLACE TEMP TABLE tx_stats AS
      WITH f AS (SELECT e.* FROM tx_entry_all e JOIN sig s ON s.sid = e.sid WHERE s.family NOT LIKE 'N2\\_%' ESCAPE '\\')
      SELECT (SELECT count(*) FROM sig WHERE venue = 'pool' AND family NOT LIKE 'N2\\_%' ESCAPE '\\') AS pool_signals,
             (SELECT count(DISTINCT sid) FROM f) AS with_leg_in_t,
             (SELECT count(DISTINCT sid) FROM f WHERE trader_multi) AS trigger_trader_multi_leg,
             (SELECT count(DISTINCT sid) FROM f WHERE kind_missing) AS trigger_kind_missing,
             (SELECT count(DISTINCT sid) FROM f WHERE size_missing) AS trigger_size_missing,
             (SELECT count(DISTINCT sid) FROM f WHERE smaller_before) AS trigger_smaller_sell_before,
             (SELECT count(DISTINCT sid) FROM f WHERE creator_veto) AS creator_sell_after_trigger""")
    con.execute("""
      CREATE OR REPLACE TEMP TABLE tx_capped AS
      SELECT e.d, count(*) FILTER (WHERE e.capped) AS n, count(*) FILTER (WHERE e.tx_end) AS n_tx_end FROM tx_entry_all e JOIN sig s ON s.sid = e.sid
      WHERE s.family NOT LIKE 'N2\\_%' ESCAPE '\\' GROUP BY e.d ORDER BY e.d""")
    con.execute("""
      CREATE OR REPLACE TEMP TABLE tx_entry AS
      SELECT sid, d, slot, b, e, virt, lp_bps, protocol_bps, creator_bps, cashback_bps, capped FROM tx_entry_all""")
    con.execute("DROP TABLE tx_entry_all")
    progress("tx entries", _count(con, "tx_entry"), json.dumps(tx_stats(con)))


def tx_stats(con):
    """The counts of tx_entries() as a dict (F7 signals only, N2 twins left out); None without --tx-delays."""
    if not con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'tx_stats'").fetchone()[0]:
        return None
    cur = con.execute("SELECT * FROM tx_stats")
    out = {c[0]: int(v) for c, v in zip(cur.description, cur.fetchone())}
    out["capped"] = {int(d): int(n) for d, n in con.execute("SELECT d, n FROM tx_capped").fetchall()}
    out["tx_end"] = {int(d): int(n) for d, n in con.execute("SELECT d, n_tx_end FROM tx_capped").fetchall()}
    return out


def sig_creators(con):
    """sig_creator(sid, creator): the creator as the backtest's trigger filter sees it (mints.creator of the pool's
    mint, else pools.coin_creator; strategies.f7_signals_sql) for the creator-veto count; the tables may lack the
    columns (small fixtures): then no creator is known."""
    have = lambda t, c: bool(con.execute(f"SELECT count(*) FROM information_schema.columns WHERE table_name = '{t}' AND column_name = '{c}'").fetchone()[0])  # noqa: E731
    pc = "p.coin_creator" if have("pools", "coin_creator") else "NULL::VARCHAR"
    mc = "m.creator" if have("mints", "creator") else "NULL::VARCHAR"
    mj = "LEFT JOIN mints m ON m.mint = p.mint" if have("mints", "mint") else "LEFT JOIN (SELECT NULL::VARCHAR AS mint, NULL::VARCHAR AS creator) m ON false"
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE sig_creator AS
      SELECT s.sid, COALESCE({mc}, {pc}) AS creator FROM sig s JOIN pools p ON p.pool = s.key {mj} WHERE s.venue = 'pool'""")


def build_matched(con, end_slot):
    """targets x signals with the venue state at each target (pool_at / curve_at), the migration pool's state for
    curve signals, and the cut flag (past the store end or the venue's last leg). Two steps of pure equi-joins:
    the state key per venue is computed first (NULL where the venue does not apply), so every state join is a
    hash join (the f7rev run spent 15,901 of 16,300 s in one query whose LEFT JOINs carried `s.venue = ...`
    beside the key match). The same holds for the tx-level entry join: its key is NULL unless the row is a
    tx-level entry (tsid), because a left-only predicate (`x.tx AND ...`) beside the key makes DuckDB plan a
    blockwise nested-loop join: 35.7 s on the one-day smoke, hours on the full f7rev run (14.4 M targets x 1 M
    entries). test_matched_plans_hash_joins_only checks the plan."""
    con.execute(f"CREATE OR REPLACE TEMP TABLE tsig AS {tsig_sql(end_slot)}")
    con.execute(f"CREATE OR REPLACE TEMP TABLE matched AS {matched_sql()}")
    con.execute("DROP TABLE tsig")
    progress("matched", _count(con, "matched"))


def tsig_sql(end_slot):
    """The SELECT behind tsig (targets x signals with the state keys and the cut flag); the migration pool joins on
    a key that is NULL for pool signals, not on `s.venue = 'curve'` beside the key (see build_matched)."""
    return f"""
      SELECT tg.sid, tg.d, tg.h, tg.h_ms, tg.target, s.venue, s.orientation, s.key, gp.orientation AS g_orientation,
             CASE WHEN s.venue = 'pool' THEN s.key END AS pkey, CASE WHEN s.venue = 'curve' THEN s.key END AS ckey,
             CASE WHEN s.venue = 'curve' THEN gp.pool END AS gkey,
             (tg.target >= {end_slot} OR tg.target > greatest(vl.last_slot, COALESCE(gl.last_slot, 0))) AS cut
      FROM targets tg JOIN sig s ON s.sid = tg.sid
      LEFT JOIN venue_last vl ON vl.key = s.key
      LEFT JOIN grad_pool gp ON gp.mint = CASE WHEN s.venue = 'curve' THEN s.key END
      LEFT JOIN venue_last gl ON gl.key = gp.pool"""


def matched_sql():
    """The SELECT behind the matched table (tsig x the states); pure equi-joins, see build_matched."""
    return """
      SELECT x.sid, x.d, x.h, x.h_ms, x.target, x.venue, x.orientation, x.key,
             CASE WHEN tx THEN te.slot ELSE ps.slot END AS p_slot, CASE WHEN tx THEN te.b ELSE ps.b END AS b, CASE WHEN tx THEN te.e ELSE ps.e END AS e,
             CASE WHEN tx THEN te.virt ELSE ps.virt END AS virt, CASE WHEN tx THEN te.lp_bps ELSE ps.lp_bps END AS lp_bps,
             CASE WHEN tx THEN te.protocol_bps ELSE ps.protocol_bps END AS protocol_bps, CASE WHEN tx THEN te.creator_bps ELSE ps.creator_bps END AS creator_bps,
             CASE WHEN tx THEN te.cashback_bps ELSE ps.cashback_bps END AS cashback_bps, CASE WHEN tx THEN te.capped END AS capped,
             cs.slot AS c_slot, cs.vt, cs.vq, cs.rt, cs.rq, cs.fee_bps AS c_fee_bps, cs.creator_bps AS c_creator_bps,
             x.g_orientation, gs.slot AS g_slot, gs.b AS g_b, gs.e AS g_e, gs.virt AS g_virt, gs.lp_bps AS g_lp_bps,
             gs.protocol_bps AS g_protocol_bps, gs.creator_bps AS g_creator_bps, gs.cashback_bps AS g_cashback_bps, x.cut
      FROM (SELECT *, d >= 1000 AND h = '' AS tx, CASE WHEN d >= 1000 AND h = '' THEN sid END AS tsid FROM tsig) x
      LEFT JOIN pool_at ps ON ps.key = x.pkey AND ps.target = x.target
      LEFT JOIN curve_at cs ON cs.key = x.ckey AND cs.target = x.target
      LEFT JOIN pool_at gs ON gs.key = x.gkey AND gs.target = x.target
      LEFT JOIN tx_entry te ON te.sid = x.tsid AND te.d = x.d"""


# --- round trips with the engine -----------------------------------------------------------------------

def _pool(r):
    return V.Pool(int(r["b"]), int(r["e"]), int(r["lp_bps"]), int(r["protocol_bps"]), int(r["creator_bps"]), int(r["cashback_bps"]), virt=int(r["virt"]))


def _curve(r):
    return V.Curve(int(r["vt"]), int(r["vq"]), int(r["rt"]), int(r["rq"]), int(r["c_fee_bps"]), int(r["c_creator_bps"]))


def resolve(venue, orientation, r):
    """The venue that prices the signal at this row: ('curve', None, state) while the curve is live; ('pool',
    orientation, state) on a pool, or on the migration pool once the curve is complete; None without a market."""
    if venue == "curve":
        if r["vt"] is not None and r["rt"] is not None and r["rt"] > 0:
            return ("curve", None, r)
        if r["g_b"] is not None and r["g_b"] > 0 and r["g_e"] > 0:  # graduated: the migration pool
            g = {"b": r["g_b"], "e": r["g_e"], "virt": r["g_virt"], "lp_bps": r["g_lp_bps"], "protocol_bps": r["g_protocol_bps"],
                 "creator_bps": r["g_creator_bps"], "cashback_bps": r["g_cashback_bps"]}
            return ("pool", r["g_orientation"], g)
        return None
    if r["b"] is None or r["b"] <= 0 or r["e"] <= 0:
        return None
    return ("pool", orientation, r)


def mid(venue, orientation, r):
    v = resolve(venue, orientation, r)
    if v is None:
        return None
    kind, ori, st = v
    if kind == "curve":
        return None if st["vt"] <= 0 else st["vq"] / st["vt"]
    return st["b"] / st["e"] if ori == "reversed" else st["e"] / st["b"]


def buy(venue, orientation, r, size):
    """Tokens received for `size` quote at state r; None when the venue cannot fill."""
    v = resolve(venue, orientation, r)
    if v is None:
        return None
    kind, ori, st = v
    if kind == "curve":
        f = V.curve_buy_exact_in(_curve(st), size)
    elif ori == "reversed":
        f = V.rev_buy_token(_pool(st), size)
    else:
        f = V.pool_buy_exact_in(_pool(st), size)
    return None if f is None else f.tokens


def entry_state(venue, orientation, r):
    """(depth_entry, fee_bps) of the state that fills our entry: the SOL side of the venue in lamports (reversed pool: b, the
    WSOL base; normal pool: the real quote vault e - virt; curve: the real quote rq) and the venue's total fee in bps
    (pool: lp + protocol + creator + cashback; curve: fee + creator); (None, None) without a market. The horizon rows
    carry the entry's values, like impact."""
    v = resolve(venue, orientation, r)
    if v is None:
        return None, None
    kind, ori, st = v
    if kind == "curve":
        return int(st["rq"] or 0), int(st["c_fee_bps"] or 0) + int(st["c_creator_bps"] or 0)
    fee = int(st["lp_bps"] or 0) + int(st["protocol_bps"] or 0) + int(st["creator_bps"] or 0) + int(st["cashback_bps"] or 0)
    return (int(st["b"]) if ori == "reversed" else int(st["e"]) - int(st["virt"] or 0)), fee


def sell(venue, orientation, r, tokens):
    """Quote received for `tokens` at state r (capped at what the venue can pay); None when there is no market."""
    v = resolve(venue, orientation, r)
    if v is None:
        return None
    kind, ori, st = v
    if kind == "curve":
        f = V.curve_sell(_curve(st), tokens)
    elif ori == "reversed":
        f = V.rev_sell_token(_pool(st), tokens)
    else:
        f = V.pool_sell_capped(_pool(st), tokens)
    return 0 if f is None else f.trader_quote  # rev_sell_token already puts the WSOL received in trader_quote


PROGRESS_ROWS = 500_000


MK_COLS = ("sid", "d", "h", "h_ms", "target", "cut", "mid", "gross", "net", "impact", "capped", "depth_entry", "fee_bps")
MK_TYPES = (pa.int64(), pa.int32(), pa.string(), pa.int64(), pa.int64(), pa.bool_(), pa.float64(), pa.float64(), pa.float64(), pa.float64(), pa.bool_(), pa.int64(), pa.int32())
FLUSH_ROWS = 1_000_000


def _flush(writer, rows):
    """One Arrow batch of `rows` to the mk parquet (a DuckDB insert here would replace the connection's pending
    matched cursor; executemany took 120 s per 190k rows on the NAS)."""
    writer.write_table(pa.table({c: pa.array([r[i] for r in rows], type=ty) for i, (c, ty) in enumerate(zip(MK_COLS, MK_TYPES))}))


def round_trips(con, per_order, mk_path, size=SIZE):
    """Writes the mk parquet (sid, d, h, h_ms, target, cut, mid, gross, net, impact_entry) from the matched
    states, in chunks of FLUSH_ROWS (the full families-v2 run has ~30M rows: never all of them as Python objects);
    returns the row count."""
    global V
    if V is None:
        V, _ = engine()
    cols = [c[0] for c in con.execute("SELECT * FROM matched LIMIT 0").description]
    out = []
    n = 0
    entry = {}
    writer = pq.ParquetWriter(mk_path, pa.schema(list(zip(MK_COLS, MK_TYPES))))
    cur = con.execute("SELECT * FROM matched ORDER BY sid, d, h_ms")
    progress("matched sorted (first fetch)", 0)
    t_loop = time.time()

    def rows():  # chunked: the matched table of a full run has millions of wide rows
        while True:
            chunk = cur.fetchmany(50_000)
            if not chunk:
                return
            yield from chunk

    for row in rows():
        if n and n % PROGRESS_ROWS == 0:
            el = time.time() - t_loop
            progress("round trips", n, f"{n / el:,.0f} rows/s")
        if len(out) >= FLUSH_ROWS:
            _flush(writer, out)
            out = []
        n += 1
        r = dict(zip(cols, row))
        venue, ori = r["venue"], r["orientation"]
        m = mid(venue, ori, r)
        k = (r["sid"], r["d"])
        if r["h"] == "":
            tokens = buy(venue, ori, r, size) if m is not None else None
            impact = (size / tokens) / m - 1 if tokens and m else None  # price paid per token over the mid
            capped = r.get("capped")
            depth, fee_bps = entry_state(venue, ori, r) if m is not None else (None, None)
            entry = {k: (m, tokens, impact, capped, depth, fee_bps)}  # the rows come ordered by (sid, d, h_ms): only the current entry is needed
            out.append((r["sid"], r["d"], "", 0, r["target"], bool(r["cut"]), m, None, None, impact, capped, depth, fee_bps))
            continue
        m0, tokens, impact, capped, depth, fee_bps = entry.get(k, (None, None, None, None, None, None))
        gross = None if m is None or not m0 else m / m0 - 1
        net = None
        if tokens and m is not None:
            back = sell(venue, ori, r, tokens)
            if back is not None:
                net = back / (size + 2 * per_order) - 1
        out.append((r["sid"], r["d"], r["h"], r["h_ms"], r["target"], bool(r["cut"]), m, gross, net, impact, capped, depth, fee_bps))
    _flush(writer, out)
    writer.close()
    el = time.time() - t_loop
    progress("round trips", n, f"{n / el:,.0f} rows/s" if el > 0 else "")
    return n


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


CLEAN_LOCK_KEYS = ("store", "store_sha256", "test_run", "test_config_sha256", "engine_commit", "trading_days")


def clean_lock(path, parts_dir, lo=None, hi=None):
    """The one exception to guard() (research/prereg-f7-mechanism-clean-2026-10-21.md): the finished E20 run under its
    clean.lock. backtest.clean_test writes the lock once E20's run has passed its minimum of trading days (the store is
    final); nothing in it is this module's. Admitted are exactly the lock's test run's parts and the lock's store:
    - parts_dir is <test_run>/parts of the lock, and <test_run>/config.json still has the lock's sha256;
    - the store is the lock's, and its sha256 (computed here, before any read) is the lock's;
    - the run's engine snapshot <test_run>/engine carries the lock's engine commit; LAB_ENGINE must be that directory
      (or unset: it is set to it);
    - no --from-slot / --to-slot: the whole run, no sub-window;
    - trading_days >= 1 (the lock holds E20's count).
    Anything else is refused with ValueError; without a lock guard() applies as always. Returns (store, engine_dir, lock)."""
    try:
        lock = json.load(open(path))
    except (OSError, ValueError) as e:
        raise ValueError(f"clean.lock unreadable: {e}")
    missing = [k for k in CLEAN_LOCK_KEYS if k not in lock]
    if missing:
        raise ValueError(f"clean.lock lacks {missing}")
    if lo is not None or hi is not None:
        raise ValueError("clean.lock: no --from-slot / --to-slot, the whole locked run only")
    run_dir = os.path.realpath(lock["test_run"])
    if os.path.realpath(parts_dir) != os.path.join(run_dir, "parts"):
        raise ValueError(f"clean.lock admits only {os.path.join(run_dir, 'parts')}, not {parts_dir}")
    cfg = os.path.join(run_dir, "config.json")
    if not os.path.exists(cfg) or _sha256(cfg) != lock["test_config_sha256"]:
        raise ValueError("clean.lock: the test run's config.json is missing or not the locked one")
    store = os.path.realpath(lock["store"])
    if not os.path.exists(store):
        raise ValueError(f"clean.lock: no store at {store}")
    if _sha256(store) != lock["store_sha256"]:
        raise ValueError("clean.lock: the store's sha256 is not the locked one")
    engine_dir = os.path.join(run_dir, "engine")
    commit_file = os.path.join(engine_dir, "COMMIT")
    if not os.path.exists(os.path.join(engine_dir, "backtest", "venues.py")) or not os.path.exists(commit_file):
        raise ValueError(f"clean.lock: no engine snapshot at {engine_dir}")
    commit = open(commit_file).read().strip()
    if not lock["engine_commit"] or not commit.startswith(str(lock["engine_commit"])[:7]):
        raise ValueError(f"clean.lock: engine snapshot {commit[:7]} is not the locked {lock['engine_commit']}")
    want = os.environ.get("LAB_ENGINE")
    if want and os.path.realpath(want.rstrip("/")) != os.path.realpath(engine_dir):
        raise ValueError(f"clean.lock: LAB_ENGINE must be the run's snapshot {engine_dir}, not {want}")
    os.environ["LAB_ENGINE"] = engine_dir
    if not isinstance(lock["trading_days"], int) or lock["trading_days"] < 1:
        raise ValueError("clean.lock: E20's run has no trading days")
    return store, engine_dir, lock


def run(parts_dir, run_name, out_dir, families=None, lo=None, hi=None, store=None, cfg_path=DEFAULTS, tx_delays=False, clean=None, delay_ms=None):
    import yaml
    t0 = time.time()
    _T0[0] = t0
    PHASES.clear()
    lock = None
    if clean:
        store, _, lock = clean_lock(clean, parts_dir, lo, hi)
    mem = os.environ.get("LAB_WALLETS_MEMORY", "1GB")  # common.connect defaults to 1 GB: the NAS run died in the last join at 953 MiB
    con = connect(store, memory=mem) if store else connect(memory=mem)
    clock = Clock.from_store(con)
    meta = json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
    end_slot = int(meta["slot_end_exclusive"])
    if lock is None:  # the clean.lock run is the one admitted past the hold-out (clean_lock above)
        guard(hi if hi is not None else end_slot)
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    global V
    V, costs = engine()
    scn = costs.Scenario.from_cfg("base", cfg)
    per_order = scn.per_tx()
    n_sig = load_signals(con, parts_dir, families, lo, hi, clock)
    n_targets = build_targets(con, clock, end_slot, tx_delays, delay_ms)
    if not tx_delays:
        con.execute("CREATE OR REPLACE TEMP TABLE tx_entry (sid BIGINT, d INTEGER, slot BIGINT, b BIGINT, e BIGINT, virt BIGINT, lp_bps BIGINT, protocol_bps BIGINT, creator_bps BIGINT, cashback_bps BIGINT, capped BOOLEAN)")
    n_pool, n_curve = match_states(con, *slot_bounds(con, clock, tx_delays), tx_delays=tx_delays)
    build_matched(con, end_slot)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"markout-{run_name}.parquet")
    mk_path = out_path + ".mk.tmp"
    n_rows = round_trips(con, per_order, mk_path)
    con.execute(f"CREATE OR REPLACE TEMP TABLE mk AS SELECT * FROM read_parquet('{mk_path}')")
    os.remove(mk_path)
    progress("mk insert", n_rows)
    con.execute(f"""
      COPY (
        SELECT s.family, s.segment, s.venue, s.orientation, s.mint, s.pool, s.t, s.day, s.ms_per_slot, s.regime, s.farm, s.ref_mint, s.ref_t, s.variants,
               m.d, m.h, m.h_ms, m.target, m.cut, m.mid, m.gross, m.net, m.impact, m.capped, m.depth_entry, m.fee_bps, s.creator_prior_pools
        FROM mk m JOIN sig s ON s.sid = m.sid ORDER BY s.family, s.segment, s.mint, s.t, m.d, m.h_ms
      ) TO '{out_path}' (FORMAT PARQUET)""")  # streamed by DuckDB: the full table is never an Arrow table in Python
    progress("parquet written", n_rows)
    per_epoch, default = delay_slots(clock, delay_ms) if delay_ms else ({}, None)
    info = {"run": run_name, "parts": parts_dir, "parts_sha256": {os.path.basename(f): _sha256(f) for f in sorted(glob.glob(os.path.join(parts_dir, "*.parquet")))},
            "families": families, "slots": [lo, hi], "signals": n_sig, "pool_states": n_pool,
            "delay_ms": delay_ms, "delay_slots_per_epoch": {str(ep): d for ep, d in per_epoch.items()}, "delay_slots_default": default,
            "curve_states": n_curve, "states": "at target slots only", "buckets": buckets(), "targets": n_targets, "rows": n_rows, "size_sol": SIZE / SOL,
            "per_order_lamports": per_order, "delays": list(delays(tx_delays)) + (list(TX_DELAYS) if tx_delays else []),
            "tx_delays": {d: delay_label(d) for d in TX_DELAYS} if tx_delays else None, "tx_stats": tx_stats(con) if tx_delays else None,
            "horizons": dict(HORIZONS_MS), "commit": git_commit(), "clean_lock": {k: lock.get(k) for k in CLEAN_LOCK_KEYS} | {"path": os.path.abspath(clean)} if lock else None, "store": store or os.environ.get("LAB_STORE"),
            "seconds": round(time.time() - t0, 1), "phases_seconds": dict(PHASES)}
    with open(os.path.join(out_dir, f"run-markout-{run_name}.json"), "w") as f:
        json.dump(info, f, indent=1)
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parts", required=True)
    ap.add_argument("--run", required=True, help="name for the output files, e.g. f7rev")
    ap.add_argument("--families", help="comma-separated; default all families in the parts")
    ap.add_argument("--from-slot", type=int)
    ap.add_argument("--to-slot", type=int)
    ap.add_argument("--out", default=os.path.join(OUT_ROOT, "markout-v1"))
    ap.add_argument("--cfg", default=DEFAULTS)
    ap.add_argument("--tx-delays", action="store_true", help="add the transaction-level entries (TX_DELAYS codes in d; spec M-a)")
    ap.add_argument("--clean-lock", help="E20's clean.lock: the one run admitted past the hold-out (clean_lock); the store and engine come from it")
    ap.add_argument("--delay-ms", type=int, help="add the slot delay of this many ms per epoch through the store's Clock (E20's d_ms 600) beside DELAYS")
    a = ap.parse_args()
    info = run(a.parts, a.run, a.out, a.families.split(",") if a.families else None, a.from_slot, a.to_slot, cfg_path=a.cfg, tx_delays=a.tx_delays,
               clean=a.clean_lock, delay_ms=a.delay_ms)
    print(json.dumps(info), file=sys.stderr)


if __name__ == "__main__":
    main()
