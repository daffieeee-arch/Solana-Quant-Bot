"""Parity: the paper engine against the backtest replay on one dev chunk.

  python -m paper.parity --from 450144000 --to 450360000 --out <dir>

Both trade F7 tp4_T600 at 0.5 SOL, d = 1, q = 0.5, empirical tau, without risk limits. Compared for
every F7 trigger in [from, to): the signal itself, the entry (filled / failed / skipped), the fill
(tokens, cost), the exit (decision slot, slot, reason, proceeds, attempts) and the P&L in lamports.
Writes parity.md and parity.json to <dir>.
"""

import argparse
import json
import os
import time
from collections import defaultdict

from backtest import replay, streams
from backtest.costs import Scenario, settle
from backtest.store import load_defaults
from backtest.strategies import F6_FARM_MS, Clock, Signal, f7_signals_sql
from backtest.tournament import STORE, exit_rule_for, work_items

from .engine import Engine
from .feed import ReplayFeed
from .journal import Journal

VARIANT, SIZE, D, Q, TOL = (0.04, 600), 0.5, 1, 0.5, 0.05
WARMUP = 2_000  # slots before `from`: triggers there are traded but not compared (busy state)
TAIL = 2_000  # slots after `to`: positions opened before `to` close


def backtest_side(con, cfg, clock, a, b):
    lo, hi = con.execute("SELECT min(slot), max(slot) + 1 FROM blocks").fetchone()
    sigs = []
    farm = {}
    for venue, m, t, depth, pool, orient, trader, _, flagged in f7_signals_sql(con, cfg, lo, hi, clock=clock,
                                                                              trig_range=(a - WARMUP, b - 1)):
        sigs.append(Signal("F7", f"tp{int(VARIANT[0] * 100)}_T{VARIANT[1]}", m, t, venue, int(depth), VARIANT,
                           pool=pool, orientation=orient, trigger=trader))
        farm[(pool or m, t)] = "n/a" if flagged is None else ("flagged" if flagged else "organic")
    sigs.sort(key=lambda s: (s.t, s.mint, s.pool or ""))
    scn = Scenario.from_cfg("base", cfg)
    out = {}
    for venue, key, lo_w, hi_w, cl in work_items(sigs, clock):
        st = (streams.load_pool_streams(con, {key: (lo_w, hi_w)}) if venue == "pool"
              else streams.load_streams(con, {key: (lo_w, hi_w)})).get(key)
        busy = -1
        for s in cl:
            if s.t <= busy:
                out[(key, s.t)] = {"status": "busy", "farm": farm.get((key, s.t))}
                continue
            sim = replay.simulate_pool_position if venue == "pool" else replay.simulate_curve_position
            r = sim(st, s.t, int(SIZE * 1e9), D, Q, exit_rule_for(s, scn, D, clock), TOL, replay.Tau("empirical"))
            busy = r.exit_slot or s.t
            pnl, _, _ = settle(r, scn) if not r.skipped else (None, None, None)
            out[(key, s.t)] = {"farm": farm.get((key, s.t)),
                               "status": "skipped" if r.skipped else "entry_failed" if r.entry_failed else "filled",
                               "tokens": r.tokens, "cost": r.cost, "exit_decision_slot": r.exit_decision_slot,
                               "exit_slot": r.exit_slot, "exit_reason": r.exit_reason, "proceeds": r.proceeds,
                               "exit_attempts": r.exit_attempts, "pnl": pnl}
    return out


def engine_side(store, a, b, journal_dir, clock):
    # From an hour (the farming window) plus the warm-up before `from`.
    feed = ReplayFeed(store, a - WARMUP - 400 - clock.slots(F6_FARM_MS, a), b + TAIL)
    eng = Engine(feed, Journal(journal_dir), None, variants=[VARIANT], d=D, q=Q, tol=TOL, ignore_risk=True, n2_k=0,
                 dev_bounds=feed.meta_bounds, clock=clock, backup=False)
    eng.run()
    j = eng.j
    sig = {(r["key"], r["t"]): r for r in j.rows("signals", "family = 'F7'")}
    orders = {(r["key"], r["t"]): r for r in j.rows("orders", "family = 'F7'")}
    fills = {(r["key"], r["t"], r["side"]): r for r in j.rows("fills", "family = 'F7'")}
    exits = {(r["key"], r["t"]): r for r in j.rows("exits", "family = 'F7'")}
    out = {}
    for k, s in sig.items():
        if s["status"] == "skipped":
            out[k] = {"status": "busy" if s["reason"] == "busy" else "risk:" + str(s["reason"]), "farm": s["farm"]}
            continue
        o, x = orders.get(k), exits.get(k)
        st = o["status"] if o else "missing"
        rec = {"status": st, "farm": s["farm"]}
        if st in ("filled", "entry_failed") and x:
            rec.update(tokens=x["tokens"], cost=x["cost"], exit_decision_slot=x["exit_decision_slot"],
                       exit_slot=x["exit_slot"], exit_reason=x["exit_reason"], proceeds=x["proceeds"],
                       exit_attempts=x["exit_attempts"], pnl=x["pnl"])
        out[k] = rec
    return out, eng.events_seen


def compare(bt, en, a, b):
    keys = sorted(k for k in set(bt) | set(en) if a <= k[1] < b)
    fields = ["status", "farm", "tokens", "cost", "exit_decision_slot", "exit_slot", "exit_reason", "proceeds", "exit_attempts", "pnl"]
    diffs, counts = [], defaultdict(int)
    for k in keys:
        x, y = bt.get(k), en.get(k)
        if x is None or y is None:
            diffs.append({"key": k, "only_in": "backtest" if y is None else "engine", "row": x or y})
            continue
        counts[x["status"]] += 1
        d = {f: (x.get(f), y.get(f)) for f in fields if x.get(f) != y.get(f)}
        if d:
            diffs.append({"key": k, "diff": d})
    return keys, diffs, dict(counts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="a", type=int, default=450144000)
    ap.add_argument("--to", dest="b", type=int, default=450360000)
    ap.add_argument("--out", required=True)
    ap.add_argument("--store", default=STORE)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    cfg = load_defaults()
    con = streams.open_store(a.store, threads=2, memory="1GB")
    clock = Clock.from_store(con)
    t0 = time.time()
    bt = backtest_side(con, cfg, clock, a.a, a.b)
    t1 = time.time()
    en, n_events = engine_side(a.store, a.a, a.b, os.path.join(a.out, "journal"), clock)
    t2 = time.time()
    keys, diffs, counts = compare(bt, en, a.a, a.b)
    pnl_bt = sum(v.get("pnl") or 0 for k, v in bt.items() if a.a <= k[1] < a.b)
    pnl_en = sum(v.get("pnl") or 0 for k, v in en.items() if a.a <= k[1] < a.b)
    res = {"slots": [a.a, a.b], "config": {"variant": VARIANT, "size_sol": SIZE, "d": D, "q": Q}, "triggers": len(keys),
           "by_status": counts, "different": len(diffs), "pnl_backtest_lamports": pnl_bt, "pnl_engine_lamports": pnl_en,
           "engine_events": n_events, "seconds": {"backtest": round(t1 - t0), "engine": round(t2 - t1)},
           "diffs": diffs[:50]}
    with open(os.path.join(a.out, "parity.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    lines = [f"# Paper engine parity, slots {a.a}–{a.b}", "",
             f"F7 tp4_T600, {SIZE} SOL, d = {D}, q = {Q}. Triggers compared: {len(keys)} ({counts}).",
             f"Identical: {len(keys) - len(diffs)} of {len(keys)}. P&L: backtest {pnl_bt} lamports, engine {pnl_en} lamports.",
             f"Engine processed {n_events} events in {t2 - t1:.0f} s; backtest {t1 - t0:.0f} s.", ""]
    for d in diffs[:20]:
        lines.append(f"- {d}")
    with open(os.path.join(a.out, "parity.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines[:5]))


if __name__ == "__main__":
    main()
