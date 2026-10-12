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
is flagged `cut`; a horizon with no state at all gives NULL.

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

from .common import OUT_ROOT, SOL, Clock, connect, git_commit, guard



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
EPOCH = 432_000
SIG_COLS = ("family", "segment", "venue", "orientation", "mint", "pool", "t", "day", "farm")


# --- signals from the parts -----------------------------------------------------------------------

def load_signals(con, parts_dir, families=None, lo=None, hi=None):
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
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE sig AS
      SELECT row_number() OVER (ORDER BY family, segment, mint, t, pool) AS sid, *
      FROM (SELECT family, segment, venue, orientation, mint, pool, t, any_value(day) AS day, any_value(farm) AS farm,
                   any_value(ref_mint) AS ref_mint, any_value(ref_t) AS ref_t,
                   list_sort(list_distinct(list(variant))) AS variants,
                   CASE WHEN venue = 'curve' THEN mint ELSE pool END AS key
            FROM read_parquet({lit}, union_by_name = true) WHERE {' AND '.join(where)}
            GROUP BY family, segment, venue, orientation, mint, pool, t)""")
    return con.execute("SELECT count(*) FROM sig").fetchone()[0]


# --- venue states at the end of each slot ------------------------------------------------------------

def slot_bounds(con, clock):
    """[first signal slot, last horizon slot] of the signals: states outside it are never asked for."""
    lo, hi = con.execute("SELECT min(t), max(t) FROM sig").fetchone()
    if lo is None:
        return 0, 0
    ms = min(clock.per_epoch.values()) if clock.per_epoch else clock.ms_per_slot(hi)
    return int(lo), int(hi) + max(DELAYS) + horizon_slots(ms, HORIZONS_MS[-1][1]) + 1


def build_states(con, lo=0, hi=2**62):
    """End-of-slot post-states of every venue a signal touches, for slots in [lo, hi]: pool (b, e, fees,
    virt) and curve (vt, vq, rt, rq, fees). Pool rows hold pre-trade reserves; the post-state follows
    streams.pool_leg: buy/sell/boost e + e_delta and b -/+ base, withdraw/deposit vault + last virt -/+
    quote_user (the virt and fee tiers carried from the last trade before `lo` are read from one extra
    trade before the range)."""
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE pool_states AS
      WITH keys AS (SELECT DISTINCT key FROM sig WHERE venue = 'pool'),
      legs AS (
        SELECT p.pool AS key, p.slot, p.tx_index, p.outer_ix, p.inner_ix, p.kind, p.b, p.e, p.vault, p.vq, p.base, p.e_delta, p.quote_user,
               p.lp_bps, p.protocol_bps, p.creator_bps, p.cashback_bps
        FROM pool p SEMI JOIN keys s ON s.key = p.pool WHERE p.slot BETWEEN {lo} AND {hi}
        UNION ALL
        SELECT key, slot, tx_index, outer_ix, inner_ix, kind, b, e, vault, vq, base, e_delta, quote_user, lp_bps, protocol_bps, creator_bps, cashback_bps FROM (
          SELECT p.pool AS key, p.slot, p.tx_index, p.outer_ix, p.inner_ix, p.kind, p.b, p.e, p.vault, p.vq, p.base, p.e_delta, p.quote_user,
                 p.lp_bps, p.protocol_bps, p.creator_bps, p.cashback_bps,
                 row_number() OVER (PARTITION BY p.pool ORDER BY p.slot DESC, p.tx_index DESC, p.outer_ix DESC, p.inner_ix DESC) AS rn
          FROM pool p SEMI JOIN keys s ON s.key = p.pool WHERE p.slot < {lo}) WHERE rn = 1),
      ctx AS (
        SELECT *,
               last_value(CASE WHEN kind IN ('buy', 'sell', 'boost') THEN COALESCE(vq, 0) END IGNORE NULLS)
                 OVER w AS virt,
               last_value(CASE WHEN kind IN ('buy', 'sell') THEN lp_bps END IGNORE NULLS) OVER w AS lpb,
               last_value(CASE WHEN kind IN ('buy', 'sell') THEN protocol_bps END IGNORE NULLS) OVER w AS pb,
               last_value(CASE WHEN kind IN ('buy', 'sell') THEN creator_bps END IGNORE NULLS) OVER w AS cb,
               last_value(CASE WHEN kind IN ('buy', 'sell') THEN cashback_bps END IGNORE NULLS) OVER w AS cbb
        FROM legs WINDOW w AS (PARTITION BY key ORDER BY slot, tx_index, outer_ix, inner_ix ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)),
      post AS (
        SELECT key, slot, tx_index, outer_ix, inner_ix,
               CASE WHEN kind = 'sell' THEN b + base WHEN kind = 'withdraw' THEN b - base WHEN kind = 'deposit' THEN b + base ELSE b - base END AS b_post,
               CASE WHEN kind IN ('buy', 'sell', 'boost') THEN e + e_delta
                    WHEN kind = 'withdraw' THEN vault + COALESCE(virt, 0) - quote_user
                    ELSE vault + COALESCE(virt, 0) + quote_user END AS e_post,
               COALESCE(virt, 0) AS virt, COALESCE(lpb, 0) AS lp_bps, COALESCE(pb, 0) AS protocol_bps,
               COALESCE(cb, 0) AS creator_bps, COALESCE(cbb, 0) AS cashback_bps
        FROM ctx)
      SELECT key, slot, b_post AS b, e_post AS e, virt, lp_bps, protocol_bps, creator_bps, cashback_bps
      FROM (SELECT *, row_number() OVER (PARTITION BY key, slot ORDER BY tx_index DESC, outer_ix DESC, inner_ix DESC) AS rn FROM post)
      WHERE rn = 1""")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE curve_states AS
      SELECT key, slot, vt, vq, rt, rq, fee_bps, creator_bps
      FROM (SELECT c.mint AS key, c.slot, c.vt, c.vq, c.rt, c.rq, c.fee_bps, c.creator_bps,
                   row_number() OVER (PARTITION BY c.mint, c.slot ORDER BY c.tx_index DESC, c.outer_ix DESC, c.inner_ix DESC) AS rn
            FROM curve c SEMI JOIN (SELECT DISTINCT key FROM sig WHERE venue = 'curve') s ON s.key = c.mint
            WHERE c.slot BETWEEN {lo} AND {hi})
      WHERE rn = 1""")
    con.execute("""
      CREATE OR REPLACE TEMP TABLE venue_last AS
      SELECT key, max(slot) AS last_slot FROM (SELECT key, slot FROM pool_states UNION ALL SELECT key, slot FROM curve_states) GROUP BY key""")
    return (con.execute("SELECT count(*) FROM pool_states").fetchone()[0], con.execute("SELECT count(*) FROM curve_states").fetchone()[0])


def horizon_slots(ms_per_slot, h_ms):
    return int(math.ceil(h_ms / ms_per_slot))


def build_targets(con, clock, end_slot):
    """signal x d x (entry, horizons) -> target slot; the entry is horizon '' at t + d."""
    rows = []
    for sid, t in con.execute("SELECT sid, t FROM sig").fetchall():
        for d in DELAYS:
            entry = t + d
            ms = clock.ms_per_slot(entry)
            rows.append((sid, d, "", 0, entry))
            for name, h_ms in HORIZONS_MS:
                rows.append((sid, d, name, h_ms, entry + horizon_slots(ms, h_ms)))
    con.execute("CREATE OR REPLACE TEMP TABLE targets (sid BIGINT, d INTEGER, h VARCHAR, h_ms BIGINT, target BIGINT)")
    if rows:
        con.executemany("INSERT INTO targets VALUES (?, ?, ?, ?, ?)", rows)
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE matched AS
      SELECT tg.sid, tg.d, tg.h, tg.h_ms, tg.target, s.venue, s.orientation, s.key,
             ps.slot AS p_slot, ps.b, ps.e, ps.virt, ps.lp_bps, ps.protocol_bps, ps.creator_bps, ps.cashback_bps,
             cs.slot AS c_slot, cs.vt, cs.vq, cs.rt, cs.rq, cs.fee_bps AS c_fee_bps, cs.creator_bps AS c_creator_bps,
             (tg.target >= {end_slot} OR tg.target > vl.last_slot) AS cut
      FROM targets tg JOIN sig s ON s.sid = tg.sid
      LEFT JOIN venue_last vl ON vl.key = s.key
      ASOF LEFT JOIN pool_states ps ON s.venue = 'pool' AND ps.key = s.key AND tg.target >= ps.slot
      ASOF LEFT JOIN curve_states cs ON s.venue = 'curve' AND cs.key = s.key AND tg.target >= cs.slot""")
    return len(rows)


# --- round trips with the engine -----------------------------------------------------------------------

def _pool(r):
    return V.Pool(int(r["b"]), int(r["e"]), int(r["lp_bps"]), int(r["protocol_bps"]), int(r["creator_bps"]), int(r["cashback_bps"]), virt=int(r["virt"]))


def _curve(r):
    return V.Curve(int(r["vt"]), int(r["vq"]), int(r["rt"]), int(r["rq"]), int(r["c_fee_bps"]), int(r["c_creator_bps"]))


def mid(venue, orientation, r):
    if venue == "curve":
        return None if r["vt"] is None or r["vt"] <= 0 else r["vq"] / r["vt"]
    if r["b"] is None or r["b"] <= 0 or r["e"] <= 0:
        return None
    return r["b"] / r["e"] if orientation == "reversed" else r["e"] / r["b"]


def buy(venue, orientation, r, size):
    """Tokens received for `size` quote at state r; None when the venue cannot fill."""
    if venue == "curve":
        if r["vt"] is None or r["rt"] is None or r["rt"] <= 0:
            return None
        f = V.curve_buy_exact_in(_curve(r), size)
    elif orientation == "reversed":
        f = V.rev_buy_token(_pool(r), size)
    else:
        f = V.pool_buy_exact_in(_pool(r), size)
    return None if f is None else f.tokens


def sell(venue, orientation, r, tokens):
    """Quote received for `tokens` at state r (capped at what the venue can pay); None when no state."""
    if venue == "curve":
        if r["vt"] is None or r["rt"] is None:
            return None
        if r["rt"] <= 0:
            return 0  # the curve completed: tokens are worth 0 there (the pool is a different venue)
        f = V.curve_sell(_curve(r), tokens)
    elif orientation == "reversed":
        f = V.rev_sell_token(_pool(r), tokens)
    else:
        f = V.pool_sell_capped(_pool(r), tokens)
    return 0 if f is None else f.trader_quote  # rev_sell_token already puts the WSOL received in trader_quote


def round_trips(con, per_order, size=SIZE):
    """Rows (sid, d, h, h_ms, target, cut, mid, gross, net, impact_entry) from the matched states."""
    global V
    if V is None:
        V, _ = engine()
    cols = [c[0] for c in con.execute("SELECT * FROM matched LIMIT 0").description]
    out = []
    entry = {}
    for row in con.execute("SELECT * FROM matched ORDER BY sid, d, h_ms").fetchall():
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
    return out


def run(parts_dir, run_name, out_dir, families=None, lo=None, hi=None, store=None, cfg_path=DEFAULTS):
    import yaml
    t0 = time.time()
    con = connect(store) if store else connect()
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
    n_sig = load_signals(con, parts_dir, families, lo, hi)
    n_pool, n_curve = build_states(con, *slot_bounds(con, clock))
    n_targets = build_targets(con, clock, end_slot)
    rows = round_trips(con, per_order)
    con.execute("CREATE OR REPLACE TEMP TABLE mk (sid BIGINT, d INTEGER, h VARCHAR, h_ms BIGINT, target BIGINT, cut BOOLEAN, mid DOUBLE, gross DOUBLE, net DOUBLE, impact DOUBLE)")
    con.executemany("INSERT INTO mk VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    table = con.execute("""
      SELECT s.family, s.segment, s.venue, s.orientation, s.mint, s.pool, s.t, s.day, s.farm, s.ref_mint, s.ref_t, s.variants,
             m.d, m.h, m.h_ms, m.target, m.cut, m.mid, m.gross, m.net, m.impact
      FROM mk m JOIN sig s ON s.sid = m.sid ORDER BY s.family, s.segment, s.mint, s.t, m.d, m.h_ms""").fetch_arrow_table()
    os.makedirs(out_dir, exist_ok=True)
    pq.write_table(table, os.path.join(out_dir, f"markout-{run_name}.parquet"))
    info = {"run": run_name, "parts": parts_dir, "families": families, "slots": [lo, hi], "signals": n_sig, "pool_states": n_pool,
            "curve_states": n_curve, "targets": n_targets, "rows": len(rows), "size_sol": SIZE / SOL, "per_order_lamports": per_order,
            "delays": list(DELAYS), "horizons": dict(HORIZONS_MS), "commit": git_commit(), "seconds": round(time.time() - t0, 1)}
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
