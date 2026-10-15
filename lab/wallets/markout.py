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
      [--families F7,N2_F7] [--from-slot A --to-slot B] [--out DIR] [--cfg week2-defaults.yaml]

Writes markout-<run>.parquet (one row per signal x d x horizon) and run-markout-<run>.json.
Slots stay before the hold-out (common.guard). Heavy on a full run (ASOF join against the pool
and curve tables): NAS; one day on the VPS as a smoke test.
"""

import argparse
import glob
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
                   any_value(ref_mint) AS ref_mint, any_value(ref_t) AS ref_t,
                   list_sort(list_distinct(list(variant))) AS variants,
                   CASE WHEN venue = 'curve' THEN mint ELSE pool END AS key
            FROM read_parquet({lit}, union_by_name = true) WHERE {' AND '.join(where)}
            GROUP BY family, segment, venue, orientation, mint, pool, t)""")
    n = _count(con, "sig")
    progress("signals", n, f"from {len(files)} parts")
    return n


# --- venue states at the end of each slot ------------------------------------------------------------

def slot_bounds(con, clock):
    """[first signal slot, last horizon slot] of the signals: states outside it are never asked for."""
    lo, hi = con.execute("SELECT min(t), max(t) FROM sig").fetchone()
    if lo is None:
        return 0, 0
    ms = min(clock.per_epoch.values()) if clock.per_epoch else clock.ms_per_slot(hi)
    return int(lo), int(hi) + max(DELAYS) + horizon_slots(ms, HORIZONS_MS[-1][1]) + 1


def pool_legs_sql(lo, hi):
    """Post-states of the pool legs of the signals' pools (and migration pools) in [lo, hi], per leg, following
    streams.pool_leg: buy / sell from the row (b -/+ base, e + e_delta, virt = vq, the row's fee tiers); boost
    (b - base, e + e_delta, virt = vq) and withdraw / deposit (vault + virt -/+ quote_user, b -/+ base) take the
    fee tiers from the last buy / sell at or before them, and the LP legs their virtual quote from the last buy,
    sell or boost (pool_leg carries ctx["virt"] over a boost too)."""
    return f"""
      WITH keys AS (SELECT DISTINCT key FROM sig WHERE venue = 'pool' UNION SELECT pool FROM grad_pool),
      raw AS (
        SELECT p.pool AS key, p.slot, p.tx_index, p.outer_ix, p.inner_ix, p.kind, p.b, p.e, p.vault, p.vq, p.base, p.e_delta, p.quote_user,
               p.lp_bps, p.protocol_bps, p.creator_bps, p.cashback_bps
        FROM pool p SEMI JOIN keys k ON k.key = p.pool WHERE p.slot BETWEEN {lo} AND {hi}),
      trades AS (
        SELECT key, slot, tx_index, outer_ix, inner_ix, CASE WHEN kind = 'sell' THEN b + base ELSE b - base END AS b, e + e_delta AS e,
               COALESCE(vq, 0) AS virt, COALESCE(lp_bps, 0) AS lp_bps, COALESCE(protocol_bps, 0) AS protocol_bps,
               COALESCE(creator_bps, 0) AS creator_bps, COALESCE(cashback_bps, 0) AS cashback_bps
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
               COALESCE(t.cashback_bps, 0) AS cashback_bps
        FROM (SELECT * FROM raw WHERE kind IN ('boost', 'withdraw', 'deposit')) o
        ASOF LEFT JOIN trades t ON t.key = o.key AND o.slot >= t.slot
        ASOF LEFT JOIN virts v ON v.key = o.key AND o.slot >= v.slot)
      SELECT * FROM trades UNION ALL SELECT * FROM others"""


def match_states(con, lo, hi):
    """The venue state at every target slot, without materialising a state per slot: each leg is mapped to the
    first target slot at or after it (ASOF), the last leg per (key, target) wins (one aggregation), and targets
    without a leg since the previous target carry the previous state forward (a window over the targets only).
    Writes pool_at (pools and migration pools), curve_at and venue_last."""
    con.execute("""
      CREATE OR REPLACE TEMP TABLE grad_pool AS
      SELECT mint, arg_min(pool, (CASE WHEN quote_class = 'sol' THEN 0 ELSE 1 END, create_slot)) AS pool,
             arg_min(orientation, (CASE WHEN quote_class = 'sol' THEN 0 ELSE 1 END, create_slot)) AS orientation
      FROM pools WHERE quote_class IN ('sol', 'reversed') AND mint IN (SELECT DISTINCT key FROM sig WHERE venue = 'curve') GROUP BY mint""")
    progress("grad_pool", _count(con, "grad_pool"))
    con.execute("""
      CREATE OR REPLACE TEMP TABLE pool_targets AS
      SELECT DISTINCT s.key, tg.target FROM targets tg JOIN sig s ON s.sid = tg.sid WHERE s.venue = 'pool'
      UNION
      SELECT DISTINCT gp.pool, tg.target FROM targets tg JOIN sig s ON s.sid = tg.sid JOIN grad_pool gp ON gp.mint = s.key WHERE s.venue = 'curve'""")
    con.execute("""
      CREATE OR REPLACE TEMP TABLE curve_targets AS
      SELECT DISTINCT s.key, tg.target FROM targets tg JOIN sig s ON s.sid = tg.sid WHERE s.venue = 'curve'""")
    progress("targets per venue", _count(con, "pool_targets") + _count(con, "curve_targets"), f"slots [{lo}, {hi}]")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE pool_at AS
      WITH legs AS ({pool_legs_sql(lo, hi)}),
      hit AS (
        SELECT kt.key, kt.target, arg_max(l.slot, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS slot,
               arg_max(l.b, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS b, arg_max(l.e, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS e,
               arg_max(l.virt, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS virt,
               arg_max(l.lp_bps, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS lp_bps,
               arg_max(l.protocol_bps, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS protocol_bps,
               arg_max(l.creator_bps, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS creator_bps,
               arg_max(l.cashback_bps, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS cashback_bps
        FROM legs l ASOF JOIN pool_targets kt ON kt.key = l.key AND l.slot <= kt.target GROUP BY kt.key, kt.target),
      filled AS (
        SELECT kt.key, kt.target,
               last_value(h.slot IGNORE NULLS) OVER w AS slot, last_value(h.b IGNORE NULLS) OVER w AS b, last_value(h.e IGNORE NULLS) OVER w AS e,
               last_value(h.virt IGNORE NULLS) OVER w AS virt, last_value(h.lp_bps IGNORE NULLS) OVER w AS lp_bps,
               last_value(h.protocol_bps IGNORE NULLS) OVER w AS protocol_bps, last_value(h.creator_bps IGNORE NULLS) OVER w AS creator_bps,
               last_value(h.cashback_bps IGNORE NULLS) OVER w AS cashback_bps
        FROM pool_targets kt LEFT JOIN hit h ON h.key = kt.key AND h.target = kt.target
        WINDOW w AS (PARTITION BY kt.key ORDER BY kt.target ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW))
      SELECT * FROM filled WHERE slot IS NOT NULL""")
    progress("pool_at (pool legs ASOF targets)", _count(con, "pool_at"))
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE curve_at AS
      WITH legs AS (
        SELECT c.mint AS key, c.slot, c.tx_index, c.outer_ix, c.inner_ix, c.vt, c.vq, c.rt, c.rq, c.fee_bps, c.creator_bps
        FROM curve c SEMI JOIN (SELECT DISTINCT key FROM sig WHERE venue = 'curve') s ON s.key = c.mint WHERE c.slot BETWEEN {lo} AND {hi}),
      hit AS (
        SELECT kt.key, kt.target, arg_max(l.slot, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS slot,
               arg_max(l.vt, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS vt, arg_max(l.vq, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS vq,
               arg_max(l.rt, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS rt, arg_max(l.rq, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS rq,
               arg_max(l.fee_bps, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS fee_bps,
               arg_max(l.creator_bps, (l.slot, l.tx_index, l.outer_ix, l.inner_ix)) AS creator_bps
        FROM legs l ASOF JOIN curve_targets kt ON kt.key = l.key AND l.slot <= kt.target GROUP BY kt.key, kt.target),
      filled AS (
        SELECT kt.key, kt.target, last_value(h.slot IGNORE NULLS) OVER w AS slot, last_value(h.vt IGNORE NULLS) OVER w AS vt,
               last_value(h.vq IGNORE NULLS) OVER w AS vq, last_value(h.rt IGNORE NULLS) OVER w AS rt, last_value(h.rq IGNORE NULLS) OVER w AS rq,
               last_value(h.fee_bps IGNORE NULLS) OVER w AS fee_bps, last_value(h.creator_bps IGNORE NULLS) OVER w AS creator_bps
        FROM curve_targets kt LEFT JOIN hit h ON h.key = kt.key AND h.target = kt.target
        WINDOW w AS (PARTITION BY kt.key ORDER BY kt.target ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW))
      SELECT * FROM filled WHERE slot IS NOT NULL""")
    progress("curve_at (curve legs ASOF targets)", _count(con, "curve_at"))
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


def build_targets(con, clock, end_slot):
    """signal x d x (entry, horizons) -> target slot; the entry is horizon '' at t + d. In SQL (the Python
    executemany took 100 s per 190k rows on the NAS): the same arithmetic as horizon_slots, ms of the entry
    slot's epoch, ceil(h_ms / ms)."""
    hz = "('', 0), " + ", ".join(f"('{n}', {h})" for n, h in HORIZONS_MS)
    ds = ", ".join(str(d) for d in DELAYS)
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE targets AS
      SELECT s.sid::BIGINT AS sid, d.d::INTEGER AS d, hz.h::VARCHAR AS h, hz.h_ms::BIGINT AS h_ms,
             (s.t + d.d + CASE WHEN hz.h_ms = 0 THEN 0 ELSE ceil(hz.h_ms / ({ms_case(clock, 's.t + d.d')}))::BIGINT END)::BIGINT AS target
      FROM sig s, (SELECT unnest([{ds}]) AS d) d, (SELECT * FROM (VALUES {hz}) v(h, h_ms)) hz""")
    n = _count(con, "targets")
    progress("targets", n)
    return n


def build_matched(con, end_slot):
    """targets x signals with the venue state at each target (pool_at / curve_at), the migration pool's state for
    curve signals, and the cut flag (past the store end or the venue's last leg)."""
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE matched AS
      SELECT tg.sid, tg.d, tg.h, tg.h_ms, tg.target, s.venue, s.orientation, s.key,
             ps.slot AS p_slot, ps.b, ps.e, ps.virt, ps.lp_bps, ps.protocol_bps, ps.creator_bps, ps.cashback_bps,
             cs.slot AS c_slot, cs.vt, cs.vq, cs.rt, cs.rq, cs.fee_bps AS c_fee_bps, cs.creator_bps AS c_creator_bps,
             gp.orientation AS g_orientation, gs.slot AS g_slot, gs.b AS g_b, gs.e AS g_e, gs.virt AS g_virt, gs.lp_bps AS g_lp_bps,
             gs.protocol_bps AS g_protocol_bps, gs.creator_bps AS g_creator_bps, gs.cashback_bps AS g_cashback_bps,
             (tg.target >= {end_slot} OR tg.target > greatest(vl.last_slot, COALESCE(gl.last_slot, 0))) AS cut
      FROM targets tg JOIN sig s ON s.sid = tg.sid
      LEFT JOIN venue_last vl ON vl.key = s.key
      LEFT JOIN grad_pool gp ON s.venue = 'curve' AND gp.mint = s.key
      LEFT JOIN venue_last gl ON gl.key = gp.pool
      LEFT JOIN pool_at ps ON s.venue = 'pool' AND ps.key = s.key AND ps.target = tg.target
      LEFT JOIN curve_at cs ON s.venue = 'curve' AND cs.key = s.key AND cs.target = tg.target
      LEFT JOIN pool_at gs ON s.venue = 'curve' AND gs.key = gp.pool AND gs.target = tg.target""")
    progress("matched", _count(con, "matched"))


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


def round_trips(con, per_order, size=SIZE):
    """Rows (sid, d, h, h_ms, target, cut, mid, gross, net, impact_entry) from the matched states."""
    global V
    if V is None:
        V, _ = engine()
    cols = [c[0] for c in con.execute("SELECT * FROM matched LIMIT 0").description]
    out = []
    entry = {}
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
        if len(out) and len(out) % PROGRESS_ROWS == 0:
            el = time.time() - t_loop
            progress("round trips", len(out), f"{len(out) / el:,.0f} rows/s")
        r = dict(zip(cols, row))
        venue, ori = r["venue"], r["orientation"]
        m = mid(venue, ori, r)
        k = (r["sid"], r["d"])
        if r["h"] == "":
            tokens = buy(venue, ori, r, size) if m is not None else None
            impact = (size / tokens) / m - 1 if tokens and m else None  # price paid per token over the mid
            entry[k] = (m, tokens, impact)
            out.append((r["sid"], r["d"], "", 0, r["target"], bool(r["cut"]), m, None, None, impact))
            continue
        m0, tokens, impact = entry.get(k, (None, None, None))
        gross = None if m is None or not m0 else m / m0 - 1
        net = None
        if tokens and m is not None:
            back = sell(venue, ori, r, tokens)
            if back is not None:
                net = back / (size + 2 * per_order) - 1
        out.append((r["sid"], r["d"], r["h"], r["h_ms"], r["target"], bool(r["cut"]), m, gross, net, impact))
    el = time.time() - t_loop
    progress("round trips", len(out), f"{len(out) / el:,.0f} rows/s" if el > 0 else "")
    return out


def run(parts_dir, run_name, out_dir, families=None, lo=None, hi=None, store=None, cfg_path=DEFAULTS):
    import yaml
    t0 = time.time()
    _T0[0] = t0
    PHASES.clear()
    mem = os.environ.get("LAB_WALLETS_MEMORY", "1GB")  # common.connect defaults to 1 GB: the NAS run died in the last join at 953 MiB
    con = connect(store, memory=mem) if store else connect(memory=mem)
    clock = Clock.from_store(con)
    meta = json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
    end_slot = int(meta["slot_end_exclusive"])
    guard(hi if hi is not None else end_slot)
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    global V
    V, costs = engine()
    scn = costs.Scenario.from_cfg("base", cfg)
    per_order = scn.per_tx()
    n_sig = load_signals(con, parts_dir, families, lo, hi, clock)
    n_targets = build_targets(con, clock, end_slot)
    n_pool, n_curve = match_states(con, *slot_bounds(con, clock))
    build_matched(con, end_slot)
    rows = round_trips(con, per_order)
    cols = ("sid", "d", "h", "h_ms", "target", "cut", "mid", "gross", "net", "impact")
    types = (pa.int64(), pa.int32(), pa.string(), pa.int64(), pa.int64(), pa.bool_(), pa.float64(), pa.float64(), pa.float64(), pa.float64())
    mk_arrow = pa.table({c: pa.array([r[i] for r in rows], type=ty) for i, (c, ty) in enumerate(zip(cols, types))})
    con.register("mk_arrow", mk_arrow)  # one bulk insert (executemany took 120 s per 190k rows on the NAS)
    con.execute("CREATE OR REPLACE TEMP TABLE mk AS SELECT * FROM mk_arrow")
    con.unregister("mk_arrow")
    progress("mk insert", len(rows))
    table = con.execute("""
      SELECT s.family, s.segment, s.venue, s.orientation, s.mint, s.pool, s.t, s.day, s.ms_per_slot, s.regime, s.farm, s.ref_mint, s.ref_t, s.variants,
             m.d, m.h, m.h_ms, m.target, m.cut, m.mid, m.gross, m.net, m.impact
      FROM mk m JOIN sig s ON s.sid = m.sid ORDER BY s.family, s.segment, s.mint, s.t, m.d, m.h_ms""").fetch_arrow_table()
    os.makedirs(out_dir, exist_ok=True)
    pq.write_table(table, os.path.join(out_dir, f"markout-{run_name}.parquet"))
    progress("parquet written", table.num_rows)
    info = {"run": run_name, "parts": parts_dir, "families": families, "slots": [lo, hi], "signals": n_sig, "pool_states": n_pool,
            "curve_states": n_curve, "states": "at target slots only", "targets": n_targets, "rows": len(rows), "size_sol": SIZE / SOL, "per_order_lamports": per_order,
            "delays": list(DELAYS), "horizons": dict(HORIZONS_MS), "commit": git_commit(), "seconds": round(time.time() - t0, 1),
            "phases_seconds": dict(PHASES)}
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
    a = ap.parse_args()
    info = run(a.parts, a.run, a.out, a.families.split(",") if a.families else None, a.from_slot, a.to_slot, cfg_path=a.cfg)
    print(json.dumps(info), file=sys.stderr)


if __name__ == "__main__":
    main()
