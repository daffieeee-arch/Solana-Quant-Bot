"""Cases of named tokens (the owner's Phantom list), from one store, as illustration only (#167).

Forward stores (epoch 1052+) are never used for tuning: this module describes what happened to a
few tokens the owner saw, so the numbers in the reports get faces. It refuses a store that
overlaps the hold-out.

Per symbol the most-traded mint in the store is chosen (pool trades first, then curve trades);
every other mint with the same symbol and any trade is kept as an alternative, because the
same names are launched again and again (copycats). Per chosen mint:

  - the timeline: create, graduation (or where the curve stopped), the 24 h after the pool opened
    in buckets (5 min for the first hour, then hourly): trades, unique buyers, SOL volume, net
    SOL flow, farmer share, price at the end and the peak of the bucket over the opening price;
  - the dev-bundle flag of cases.py (graduation in the create slot, or <= 5 curve buyers);
  - the insiders (O7: creator plus buyers within 10 s of the create) and their share at +15 min
    and at +24 h; creator sells;
  - the five biggest buyers and sellers of the first 24 h (wallet, SOL, minutes, creator / insider);
  - whether our strategies would have triggered, purely descriptive: F7 through the engine's own
    signal SQL (curve 20-75 SOL, pool >= 150 SOL; no replay, no P&L), M1 (k = 2, 3) through the
    engine's candidate conditions, organic buyers and insider share, and the M1+ decision at
    +15 min (k >= 1.5 / 2, net flow > 0, >= 20 organic buyers, insider veto > 0.90, hype veto
    > 585 curve buyers), with the price path after each trigger.

  LAB_STORE=.../fwd1052.duckdb python -m wallets.phantom --symbols Pepper,DOPE,... --tag 1052 [--out DIR]

Writes timelines-phantom-<tag>.json; phantom_report.py merges the tags into the owner's report.
"""

import argparse
import bisect
import datetime as dt
import json
import os
import sys
import time

from . import cases
from .common import HOLDOUT_START, OUT_ROOT, SOL, Clock, connect, git_commit
from .score import DEFAULTS

INSIDER_MS = 10_000  # O7: creator plus buyers within 10 s of the create (backtest.overlay.INSIDER_WINDOW_MS)


def engine():
    """The store-v2 backtest engine (LAB_ENGINE: a directory holding backtest/ with overlay, streams,
    strategies incl. M1); None when it is not there (tests, CI), then the strategy checks are skipped."""
    path = os.environ.get("LAB_ENGINE")
    if not path:
        return None
    import importlib
    import importlib.util
    spec = importlib.util.spec_from_file_location("lab_engine_backtest", os.path.join(path, "backtest", "__init__.py"),
                                                  submodule_search_locations=[os.path.join(path, "backtest")])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["lab_engine_backtest"] = pkg
    spec.loader.exec_module(pkg)
    mods = {}
    for name in ("overlay", "streams", "strategies"):
        try:
            mods[name] = importlib.import_module(f"lab_engine_backtest.{name}")
        except ImportError:
            return None
    return mods

HOLDOUT_END = 454_464_000
DAY_MS, HOUR_MS = 86_400_000, 3_600_000
TOP = 5
M1_KS = (2, 3)
M1PLUS_KS = (1.5, 2)
V_INS, V_HYPE = 0.90, 585
AFTER_SLOTS = (150, 600, 2_400)


def check_store(con):
    meta = json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
    lo, hi = meta["slot_start"], meta["slot_end_exclusive"]
    if lo < HOLDOUT_END and hi > HOLDOUT_START:
        raise SystemExit(f"store [{lo}, {hi}) overlaps the hold-out [{HOLDOUT_START}, {HOLDOUT_END}): refused")
    return meta, lo, hi


def block_time(con, slot):
    r = con.execute("SELECT block_time FROM blocks WHERE slot <= ? AND block_time IS NOT NULL ORDER BY slot DESC LIMIT 1",
                    [int(slot)]).fetchone()
    return None if r is None else dt.datetime.fromtimestamp(r[0], dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def candidates(con, syms):
    rows = con.execute("""
      SELECT lower(m.symbol) AS s, m.mint, m.name, m.create_slot, m.migrate_slot IS NOT NULL AS graduated,
             COALESCE(ps.n_buy + ps.n_sell, 0)::BIGINT AS n_pool, COALESCE(cc.n, 0)::BIGINT AS n_curve
      FROM mints m LEFT JOIN pool_stats ps ON ps.pool = m.pool
      LEFT JOIN (SELECT mint, count(*) AS n FROM curve SEMI JOIN mints USING (mint) WHERE lower(symbol) IN (SELECT unnest(?)) GROUP BY mint) cc
        ON cc.mint = m.mint
      WHERE lower(m.symbol) IN (SELECT unnest(?)) ORDER BY s, n_pool DESC, n_curve DESC, m.create_slot""", [syms, syms]).fetchall()
    out = {}
    for s, mint, name, cslot, grad, n_pool, n_curve in rows:
        out.setdefault(s, []).append({"mint": mint, "name": name, "create_slot": cslot, "graduated": bool(grad),
                                      "n_pool": int(n_pool), "n_curve": int(n_curve)})
    return out


def load_trades(con, tl, clock):
    """Temp table tr: curve and canonical-pool trades of the mint from the create to +24 h after the
    pool opened (or after the create). Price = reserve price; farmer only on the pool."""
    c, open_slot = tl["create_slot"], tl.get("open_slot")
    anchor = open_slot if open_slot is not None else c
    n_day = clock.slots(DAY_MS, anchor)
    end = anchor + n_day
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE tr AS
      SELECT * FROM (
        SELECT 'curve' AS phase, c.slot::BIGINT AS slot, c.tx_index::BIGINT AS tx_index, c.trader, c.is_buy AS token_buy,
               c.q::DOUBLE / {SOL} AS sol, c.vq::DOUBLE / c.vt AS px, false AS farmer, c.rq::DOUBLE / {SOL} AS depth_sol,
               CASE WHEN c.is_buy THEN c.t ELSE -c.t END AS dtok
        FROM curve c WHERE c.mint = ? AND c.vt > 0 AND c.slot < {end}
        UNION ALL
        SELECT 'pool', e.slot::BIGINT, e.tx_index, e.trader, e.token_buy, e.sol_amount::DOUBLE / {SOL}, e.e::DOUBLE / e.b, e.farmer,
               e.sol_depth::DOUBLE / {SOL}, CASE WHEN e.token_buy THEN e.token_amount ELSE -e.token_amount END
        FROM pool e WHERE e.pool = ? AND e.kind IN ('buy', 'sell') AND e.b > 0 AND e.e > 0 AND e.slot < {end})
      ORDER BY slot, tx_index""", [tl["mint"], tl.get("pool") or ""])
    n_ins = clock.slots(INSIDER_MS, c)
    con.execute("CREATE OR REPLACE TEMP TABLE ins AS SELECT DISTINCT trader FROM tr WHERE phase = 'curve' AND token_buy AND slot <= ? "
                "UNION SELECT ?", [c + n_ins, tl.get("creator") or ""])
    return anchor, end, n_day


def series(con, tl, clock, anchor, end):
    """Buckets relative to the anchor (pool open, or the create when there is no pool)."""
    ms = clock.ms_per_slot(anchor)
    p0 = tl.get("p_open")
    edges = [(-10**9, 0, "curve")] if tl.get("open_slot") is not None else []
    edges += [(m, m + 5, "pool") for m in range(0, 60, 5)] + [(h * 60, (h + 1) * 60, "pool") for h in range(1, 24)]
    rows = []
    for a, b, phase in edges:
        lo = anchor + round(a * 60000 / ms) if a > -10**9 else 0
        hi = anchor + round(b * 60000 / ms)
        r = con.execute(f"""
          SELECT count(*), count(DISTINCT trader) FILTER (WHERE token_buy), count(DISTINCT trader) FILTER (WHERE NOT token_buy),
                 sum(sol), sum(CASE WHEN token_buy THEN sol ELSE -sol END), sum(sol) FILTER (WHERE farmer) / NULLIF(sum(sol), 0),
                 arg_max(px, slot * 1048576 + tx_index), max(px), min(px), arg_max(depth_sol, slot * 1048576 + tx_index)
          FROM tr WHERE slot >= {lo} AND slot < {hi} AND phase = '{phase if tl.get("open_slot") is not None else "curve"}'""").fetchone()
        if not r[0]:
            continue
        base = p0 or (tl.get("p_first") or r[8])
        rows.append({"from_min": None if a == -10**9 else a, "to_min": b, "phase": phase, "trades": r[0], "buyers": r[1], "sellers": r[2],
                     "vol_sol": r[3], "net_sol": r[4], "farmer_share": r[5], "px_end_mult": r[6] / base if base else None,
                     "px_max_mult": r[7] / base if base else None, "depth_sol": r[9]})
    return rows


def phase_of(tl):
    return "pool" if tl.get("open_slot") is not None else "curve"


def big_traders(con, tl, anchor, end, clock, buy):
    ms = clock.ms_per_slot(anchor)
    rows = con.execute(f"""
      SELECT trader, sum(sol) AS s, min(slot), trader = ?, trader IN (SELECT trader FROM ins), bool_or(farmer)
      FROM tr WHERE token_buy = ? AND slot >= {anchor} AND slot < {end} AND phase = '{phase_of(tl)}'
      GROUP BY trader ORDER BY s DESC LIMIT {TOP}""",
                      [tl.get("creator") or "", buy]).fetchall()
    o = tl.get("open_slot")
    return [{"wallet": w, "sol": s, "minutes_after_create": (slot - tl["create_slot"]) * ms / 60000,
             "minutes_after_open": (slot - o) * ms / 60000 if o is not None else None, "creator": bool(c),
             "insider": bool(i), "farmer": bool(f)} for w, s, slot, c, i, f in rows]


def price_after(con, t, base, windows):
    """{label: (price at the end of the window / base, peak in the window / base)} for {label: slots}."""
    out = {}
    for label, n in windows.items():
        r = con.execute(f"SELECT arg_max(px, slot * 1048576 + tx_index), max(px) FROM tr WHERE slot > {t} AND slot <= {t + n}").fetchone()
        out[label] = {"slots": n, "end_mult": r[0] / base if r[0] and base else None, "max_mult": r[1] / base if r[1] and base else None}
    return out


def f7_windows():
    return {f"{n} slots": n for n in AFTER_SLOTS}


def f7_check(con, cfg, tl, eng, eng_clock, store_lo, store_hi):
    strategies = eng["strategies"]
    c = tl.get("create_slot")
    if c is None:
        return {"checked": False, "reason": "creatie vóór de store"}
    burn = eng_clock.slots(strategies.BURN_IN_MS, c)
    dev_start = c - burn - 1
    dev_end = store_hi + eng_clock.slots(strategies.END_GUARD_MS, store_hi) + 1
    sigs = strategies.f7_signals_sql(con, cfg, dev_start, dev_end, only_mints=[tl["mint"]], clock=eng_clock)
    ms = eng_clock.ms_per_slot(c)
    trig = []
    seen = set()
    for venue, mint, t, depth, *rest in sorted(sigs, key=lambda s: s[2]):
        if venue in seen:
            continue
        seen.add(venue)
        px = con.execute(f"SELECT arg_max(px, slot * 1048576 + tx_index) FROM tr WHERE slot <= {t}").fetchone()[0]
        trig.append({"venue": venue, "t": int(t), "minutes_after_create": (t - c) * ms / 60000, "depth_sol": depth / SOL,
                     "px_mult": px / tl["p_open"] if px and tl.get("p_open") else None, "after": price_after(con, t, px, f7_windows()),
                     "trigger_farmer": bool(rest[3]) if len(rest) > 3 and rest[3] is not None else None,
                     "pool_farming_hour": bool(rest[4]) if len(rest) > 4 and rest[4] is not None else None})
    return {"checked": True, "n_triggers": len(sigs), "first": trig}


def m1_check(con, cfg, tl, eng, eng_clock, clock):
    """M1 (spec 2026-10-10, engine conditions) for k = 2 and 3, and the M1+ decision at +15 min."""
    overlay, streams, strategies = eng["overlay"], eng["streams"], eng["strategies"]
    if tl.get("open_slot") is None or not tl.get("pool"):
        return {"checked": False, "reason": "niet gegradueerd"}
    if tl.get("grad_in_create"):
        return {"checked": False, "reason": "graduatie in de create-slot (buiten het M1-universum)"}
    if not tl.get("pool_normal_sol"):
        return {"checked": False, "reason": "geen normale SOL-pool"}
    o = tl["open_slot"]
    n_age, n_flow, n_buy = (clock.slots(x, o) for x in (strategies.M1_AGE_MS, strategies.M1_FLOW_MS, strategies.M1_BUYERS_MS))
    n_15 = clock.slots(15 * 60_000, o)
    ms = clock.ms_per_slot(o)
    p0 = tl["p_open"]
    cands = con.execute(f"""
      WITH ev AS (
        SELECT e.slot, e.tx_index, e.outer_ix, e.inner_ix, e.sol_depth,
               (e.e + e.e_delta)::DOUBLE / (CASE WHEN e.token_buy THEN e.b - e.base ELSE e.b + e.base END) AS price,
               CASE WHEN e.farmer THEN 0 WHEN e.token_buy THEN e.sol_amount ELSE -e.sol_amount END AS dq
        FROM pool e WHERE e.pool = ? AND e.kind IN ('buy', 'sell') AND e.slot BETWEEN {o} AND {o + n_age}),
      w AS (SELECT *, SUM(dq) OVER (ORDER BY slot RANGE BETWEEN {n_flow} PRECEDING AND CURRENT ROW) AS inflow FROM ev)
      SELECT slot, sol_depth, price / {p0} AS ratio FROM w
      WHERE price >= 2 * {p0} AND inflow >= {strategies.M1_FLOW_SHARE} * sol_depth
      QUALIFY row_number() OVER (PARTITION BY slot ORDER BY tx_index DESC, outer_ix DESC, inner_ix DESC) = 1
      ORDER BY slot""", [tl["pool"]]).fetchall()
    toks = streams.load_streams(con, {tl["mint"]: (o, o + n_age)})
    tok = toks.get(tl["mint"])
    out = {"checked": True, "candidates": len(cands), "k": {}}
    if tok is None:
        out.update(checked=False, reason="stream niet laadbaar")
        return out
    ins = overlay.Insiders(tok, eng_clock.slots(overlay.INSIDER_WINDOW_MS, tok.create_slot))
    buys = [(l.slot, l.trader) for l in tok.pool_legs if l.kind == "buy" and not l.farmer]
    bslots = [b[0] for b in buys]

    def organic_buyers(lo, hi):
        a, b = bisect.bisect_left(bslots, lo), bisect.bisect_right(bslots, hi)
        return len({buys[j][1] for j in range(a, b)})

    out["insiders_known"] = bool(ins.known)
    out["n_insiders"] = len(ins.insiders)
    for k in M1_KS:
        hit, first_ratio = None, None
        for t, depth, ratio in cands:
            if ratio < k:
                continue
            if first_ratio is None:
                first_ratio = {"t": int(t), "minutes_after_open": (t - o) * ms / 60000, "ratio": ratio}
            nb, sh = organic_buyers(t - n_buy, t), ins.share(t)
            if nb >= strategies.M1_MIN_BUYERS and sh <= overlay.VETO_SHARE:
                px = con.execute(f"SELECT arg_max(px, slot * 1048576 + tx_index) FROM tr WHERE slot <= {t}").fetchone()[0]
                hit = {"t": int(t), "minutes_after_open": (t - o) * ms / 60000, "ratio": ratio, "organic_buyers_15m": nb,
                       "insider_share": sh, "depth_sol": depth / SOL,
                       "after": price_after(con, t, px, {"15 min": n_15, "1 u": clock.slots(HOUR_MS, o), "6 u": n_age})}
                break
        if hit is None and first_ratio is not None:
            t = first_ratio["t"]
            first_ratio.update(organic_buyers_15m=organic_buyers(t - n_buy, t), insider_share=ins.share(t))
        out["k"][str(k)] = {"trigger": hit, "first_price_candidate": first_ratio}
    # M1+: the fixed decision at open + 15 min.
    t15 = o + n_15
    r = con.execute(f"""SELECT arg_max(px, slot * 1048576 + tx_index), sum(CASE WHEN token_buy THEN sol ELSE -sol END), count(*)
                        FROM tr WHERE phase = 'pool' AND slot >= {o} AND slot <= {t15}""").fetchone()
    px15 = r[0]
    plus = {"t": int(t15), "px_mult": px15 / p0 if px15 else None, "net_sol": r[1], "pool_trades": r[2],
            "organic_buyers": organic_buyers(o, t15), "insider_share": ins.share(t15), "curve_buyers": tl.get("curve_buyers")}
    plus["trigger_k"] = {str(k): bool(plus["px_mult"] is not None and plus["px_mult"] >= k and (plus["net_sol"] or 0) > 0
                                   and plus["organic_buyers"] >= strategies.M1_MIN_BUYERS) for k in M1PLUS_KS}
    plus["veto_ins"] = plus["insider_share"] > V_INS
    plus["veto_hype"] = (tl.get("curve_buyers") or 0) > V_HYPE
    plus["after"] = price_after(con, t15, px15, {"1 u": clock.slots(HOUR_MS, o), "6 u": n_age, "24 u": clock.slots(DAY_MS, o)}) if px15 else None
    out["m1plus_t15"] = plus
    return out


def describe(con, cfg, mint, clock, store_lo, store_hi, eng=None, eng_clock=None):
    base = cases.timelines(con, [mint], clock).get(mint)
    if base is None:
        return None
    tl = dict(base)
    mp = con.execute("SELECT pool, creator, supply FROM mints WHERE mint = ?", [mint]).fetchone()
    tl.update(pool=mp[0], creator=tl.get("creator") or mp[1], supply=tl.get("supply") or mp[2])
    tl["create_time"] = block_time(con, tl["create_slot"]) if tl.get("create_slot") is not None else None
    tl["open_time"] = block_time(con, tl["open_slot"]) if tl.get("open_slot") is not None else None
    cv = con.execute("""SELECT count(DISTINCT trader) FILTER (WHERE is_buy), count(*), sum(q)::DOUBLE / 1e9,
                               arg_max(rq, slot * 1048576 + tx_index)::DOUBLE / 1e9, arg_min(vq::DOUBLE / vt, slot * 1048576 + tx_index),
                               min(slot), max(slot)
                        FROM curve WHERE mint = ? AND vt > 0""", [mint]).fetchone()
    tl.update(curve_buyers=cv[0], curve_trades=cv[1], curve_sol=cv[2], curve_end_rq_sol=cv[3], p_first=cv[4],
              curve_first_slot=cv[5], curve_last_slot=cv[6])
    c, done, o = tl.get("create_slot"), tl.get("complete_slot"), tl.get("open_slot")
    if "grad_in_create" not in tl:
        tl["grad_in_create"] = bool(c is not None and done is not None and done == c)
    tl["self_funded"] = bool(tl["grad_in_create"] or (cv[0] is not None and cv[0] <= cases.MAX_BUNDLE_CURVE_BUYERS))
    ms = clock.ms_per_slot(c or store_lo)
    tl["curve_minutes"] = (done - c) * ms / 60000 if c is not None and done is not None else None
    if o is not None and tl.get("pool"):
        p = con.execute("SELECT quote_class, orientation, COALESCE(mayhem, false), q0::DOUBLE / NULLIF(b0, 0) FROM pools WHERE pool = ?",
                        [tl["pool"]]).fetchone()
        if p:
            tl.update(pool_quote_class=p[0], pool_orientation=p[1], pool_mayhem=bool(p[2]), p_open=p[3],
                      pool_normal_sol=(p[0] == "sol" and p[1] == "normal" and not p[2]))
    anchor, end, n_day = load_trades(con, tl, clock)
    tl["n_insiders"] = con.execute("SELECT count(*) FROM ins WHERE trader <> ''").fetchone()[0]
    r = con.execute(f"""
      SELECT count(*), count(DISTINCT trader), sum(sol), sum(sol) FILTER (WHERE NOT farmer), count(DISTINCT trader) FILTER (WHERE token_buy),
             sum(dtok) FILTER (WHERE trader IN (SELECT trader FROM ins))::DOUBLE / NULLIF(sum(dtok), 0),
             count(*) FILTER (WHERE NOT token_buy AND trader = ?),
             sum(sol) FILTER (WHERE NOT token_buy AND trader = ?)
      FROM tr WHERE slot >= {anchor} AND slot < {end} AND phase = '{phase_of(tl)}'""",
                    [tl.get("creator") or "", tl.get("creator") or ""]).fetchone()
    tl["window_24h"] = {"phase": phase_of(tl), "trades": r[0], "traders": r[1], "vol_sol": r[2], "organic_sol": r[3],
                        "buyers": r[4], "creator_sells": r[6], "creator_sold_sol": r[7]}
    tl["insider_share_24h"] = con.execute(f"""SELECT sum(dtok) FILTER (WHERE trader IN (SELECT trader FROM ins))::DOUBLE / NULLIF(sum(dtok), 0)
                                              FROM tr WHERE slot < {end}""").fetchone()[0]
    if o is None:  # no pool: the curve's own peak and end over its first price
        pk = con.execute("SELECT max(px), arg_max(px, slot * 1048576 + tx_index), arg_max(slot, px) FROM tr").fetchone()
        if pk[0] and tl.get("p_first"):
            tl.update(max_mult=pk[0] / tl["p_first"], end_mult=pk[1] / tl["p_first"], peak_slot_24h=pk[2])
    tl["series"] = series(con, tl, clock, anchor, end)
    tl["big_buyers"] = big_traders(con, tl, anchor, end, clock, True)
    if not tl.get("big_sellers"):
        tl["big_sellers"] = big_traders(con, tl, anchor, end, clock, False)
    if eng is not None:
        tl["f7"] = f7_check(con, cfg, tl, eng, eng_clock, store_lo, store_hi)
        tl["m1"] = m1_check(con, cfg, tl, eng, eng_clock, clock)
    else:
        tl["f7"] = tl["m1"] = {"checked": False, "reason": "engine (LAB_ENGINE) niet beschikbaar"}
    return tl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", required=True, help="comma-separated symbols (case-insensitive)")
    ap.add_argument("--tag", required=True, help="store tag, e.g. 1052; names the output file")
    ap.add_argument("--out", default=os.path.join(OUT_ROOT, "phantom-v1"))
    ap.add_argument("--max-alternatives", type=int, default=8)
    a = ap.parse_args()
    import yaml
    with open(DEFAULTS) as f:
        cfg = yaml.safe_load(f)
    con = connect(memory=os.environ.get("LAB_WALLETS_MEMORY", "1000MB"))
    meta, store_lo, store_hi = check_store(con)
    clock = Clock.from_store(con)
    eng = engine()
    if eng is None:
        print("LAB_ENGINE not set or without M1/overlay: strategy checks skipped", file=sys.stderr)
    eng_clock = eng["strategies"].Clock.from_store(con) if eng else None
    syms = [x.strip().lower() for x in a.symbols.split(",") if x.strip()]
    t0 = time.time()
    cand = candidates(con, syms)
    chosen = {s: rows[0]["mint"] for s, rows in cand.items() if rows and (rows[0]["n_pool"] or rows[0]["n_curve"])}
    tls = {}
    for s, mint in chosen.items():
        t1 = time.time()
        tl = describe(con, cfg, mint, clock, store_lo, store_hi, eng, eng_clock)
        if tl is not None:
            tls[mint] = tl
        print(f"{s}: {mint[:8]} {tl.get('name') if tl else '?'} graduated={bool(tl and tl.get('open_slot'))} "
              f"f7={tl['f7'].get('n_triggers', '-') if tl else '?'} {time.time() - t1:.0f}s", file=sys.stderr)
    os.makedirs(a.out, exist_ok=True)
    out = {"tag": a.tag, "store": os.environ.get("LAB_STORE"), "slot_range": [store_lo, store_hi],
           "time_range": [block_time(con, store_lo), block_time(con, store_hi - 1)], "commit": git_commit(),
           "symbols": syms, "chosen": chosen, "missing": [s for s in syms if s not in chosen],
           "candidates": {s: [r for r in rows if r["n_pool"] or r["n_curve"]][:a.max_alternatives] for s, rows in cand.items()},
           "timelines": tls}
    with open(os.path.join(a.out, f"timelines-phantom-{a.tag}.json"), "w") as f:
        json.dump(out, f, indent=1, default=str)
    print(f"{a.tag}: {len(chosen)} of {len(syms)} symbols found, missing {out['missing']}, {time.time() - t0:.0f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
