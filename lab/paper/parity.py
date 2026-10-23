"""Parity: the paper engine against the backtest replay on one dev chunk.

  python -m paper.parity --from 450144000 --to 450360000 --out <dir>

Both trade F7 tp4_T600 at 0.5 SOL, d = 1, q = 0.5, empirical tau, without risk limits. Compared for
every F7 trigger in [from, to): the signal itself, the entry (filled / failed / skipped), the fill
(tokens, cost), the exit (decision slot, slot, reason, proceeds, attempts) and the P&L in lamports.
Writes parity.md and parity.json to <dir>.

  python -m paper.parity --arm flow300_H6 --from A --to B --out <dir>
  python -m paper.parity --journal-a <dir> --journal-b <dir> --out <dir>

Journal against journal (paper/journal_diff.py): the Python engine against the Rust live route on the same input,
row for row, 0 unexplained (lab/docs/rust-live-route-design-2026-10-16.md §3).

The paper arms (research/week4-spec-paper-h4h5-2026-10-16.md §6.4): an exit variant on the organic triggers,
d_ms 600, against the backtest with busy over the organic triggers only (as the engine's arm: equal on every
organic trigger) and with the tournament's busy over every trigger (equal on the organic triggers of pools without
a flagged trigger; the others are the documented difference, counted).
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
from backtest.tournament import STORE, delay_slots, exit_rule_for, work_items

from .engine import Engine, variant
from .feed import ReplayFeed
from .journal import Journal

VARIANT, SIZE, D, Q, TOL = (0.04, 600), 0.5, 1, 0.5, 0.05
WARMUP = 2_000  # slots before `from`: triggers there are traded but not compared (busy state)
TAIL = 2_000  # slots after `to`: positions opened before `to` close


def backtest_side(con, cfg, clock, a, b, var=VARIANT, d=D, d_ms=None, segments=None, organic_busy=False):
    """Every F7 trigger's position in the backtest replay. organic_busy: flagged triggers open and block nothing
    (the paper arms); else busy over every trigger (the tournament)."""
    name, params = variant(var)
    lo, hi = con.execute("SELECT min(slot), max(slot) + 1 FROM blocks").fetchone()
    sigs = []
    farm = {}
    for venue, m, t, depth, pool, orient, trader, _, flagged in f7_signals_sql(con, cfg, lo, hi, clock=clock,
                                                                              trig_range=(a - WARMUP, b - 1)):
        if segments and ("curve" if venue == "curve" else f"pool_{orient}") not in segments:
            continue
        sigs.append(Signal("F7", name, m, t, venue, int(depth), params, pool=pool, orientation=orient, trigger=trader))
        farm[(pool or m, t)] = "n/a" if flagged is None else ("flagged" if flagged else "organic")
    sigs.sort(key=lambda s: (s.t, s.mint, s.pool or ""))
    scn = Scenario.from_cfg("base", cfg)
    out = {}
    for venue, key, lo_w, hi_w, cl in work_items(sigs, clock):
        st = (streams.load_pool_streams(con, {key: (lo_w, hi_w)}) if venue == "pool"
              else streams.load_streams(con, {key: (lo_w, hi_w)})).get(key)
        busy = -1
        for s in cl:
            if organic_busy and farm.get((key, s.t)) == "flagged":
                out[(key, s.t)] = {"status": "farm flagged", "farm": "flagged"}
                continue
            if s.t <= busy:
                out[(key, s.t)] = {"status": "busy", "farm": farm.get((key, s.t))}
                continue
            dd = delay_slots(d_ms, s.t, clock) if d_ms else d
            sim = replay.simulate_pool_position if venue == "pool" else replay.simulate_curve_position
            r = sim(st, s.t, int(SIZE * 1e9), dd, Q, exit_rule_for(s, scn, dd, clock, st), TOL, replay.Tau("empirical"))
            busy = r.exit_slot or s.t
            pnl, _, _ = settle(r, scn) if not r.skipped else (None, None, None)
            out[(key, s.t)] = {"farm": farm.get((key, s.t)),
                               "status": "skipped" if r.skipped else "entry_failed" if r.entry_failed else "filled",
                               "tokens": r.tokens, "cost": r.cost, "exit_decision_slot": r.exit_decision_slot,
                               "exit_slot": r.exit_slot, "exit_reason": r.exit_reason, "proceeds": r.proceeds,
                               "exit_attempts": r.exit_attempts, "pnl": pnl}
    return out


def engine_side(store, a, b, journal_dir, clock, var=VARIANT, d=D, d_ms=None, segments=None, farm="all", tail=TAIL):
    # From an hour (the farming window) plus the warm-up before `from`.
    feed = ReplayFeed(store, a - WARMUP - 400 - clock.slots(F6_FARM_MS, a), b + tail)
    eng = Engine(feed, Journal(journal_dir), None, variants=[var], d=d, d_ms=d_ms, q=Q, tol=TOL, ignore_risk=True, n2_k=0,
                 dev_bounds=feed.meta_bounds, clock=clock, backup=False, segments=segments, farm=farm)
    eng.run()
    j = eng.j
    name = variant(var)[0]
    sig = {(r["key"], r["t"]): r for r in j.rows("signals", "family = 'F7' AND variant = ?", (name,))}
    orders = {(r["key"], r["t"]): r for r in j.rows("orders", "family = 'F7' AND variant = ?", (name,))}
    exits = {(r["key"], r["t"]): r for r in j.rows("exits", "family = 'F7' AND variant = ?", (name,))}
    out = {}
    for k, s in sig.items():
        if s["status"] == "skipped":
            st = "busy" if s["reason"] == "busy" else "farm flagged" if s["reason"] == "farm flagged" else "risk:" + str(s["reason"])
            out[k] = {"status": st, "farm": s["farm"]}
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


ARM_TAIL = 20_000  # slots after `to` for the arms (6 h caps: a position still open at the data's end is not compared)


def unsold_busy(bt, en, k):
    """The backtest entered a trigger the engine saw as busy because the pool's previous position never sold
    (exit_slot None, e.g. a drained pool): the backtest's busy_until is exit_slot or t, so an unsold position frees
    its pool at its trigger (a lookahead: whether it will ever sell is not known while it is open); the engine
    keeps the pool busy while its position is open. Explained, not a bug of the arm."""
    x, y = bt.get(k), en.get(k)
    if not (x and y and x["status"] in ("filled", "entry_failed") and y["status"] == "busy"):
        return False
    prev = [kk for kk, v in bt.items() if kk[0] == k[0] and kk[1] < k[1] and v["status"] == "filled"]
    if not prev:
        return False
    p = max(prev, key=lambda kk: kk[1])
    e = en.get(p) or {}
    # The engine keeps the pool busy until the position's exit: never (an engine without the write-off), or its
    # write-off at the rule's deadline (engine._write_off_if_stuck), after this trigger.
    held = e.get("exit_slot") is None or (e.get("exit_reason") == "written_off" and k[1] <= e["exit_slot"])
    return bt[p].get("exit_slot") is None and held and e.get("status") == "filled"


def written_off_row(bt, en, k):
    """The backtest's unsold position (exit_slot None, proceeds 0: a drained pool) that the engine wrote off at its rule's
    deadline (exit_reason 'written_off', proceeds 0, the same fill): the same position, closed where the backtest leaves
    it open (its exit_slot, reason, attempts and the P&L of the attempts differ). Explained."""
    x, y = bt.get(k), en.get(k)
    return bool(x and y and x["status"] == y["status"] == "filled" and x.get("exit_slot") is None and not x.get("proceeds")
                and y.get("exit_reason") == "written_off" and not y.get("proceeds")
                and (x.get("tokens"), x.get("cost")) == (y.get("tokens"), y.get("cost")))


def written_off_later_sold(bt, en, k):
    """The engine wrote the position off at its rule's deadline (no sale possible then); the backtest sold it later,
    after the pool got liquidity again: the late sale the write-off forgoes by the owner's rule. Explained, listed apart."""
    x, y = bt.get(k), en.get(k)
    return bool(x and y and x["status"] == y["status"] == "filled" and y.get("exit_reason") == "written_off"
                and not y.get("proceeds") and (x.get("tokens"), x.get("cost")) == (y.get("tokens"), y.get("cost"))
                and x.get("exit_slot") is not None and y.get("exit_slot") is not None and x["exit_slot"] > y["exit_slot"])


def _split(diffs, bt, en):
    """(unexplained diffs, explained unsold_busy diffs, explained written_off diffs, explained written_off_later_sold
    diffs): the two write-off causes apart, so a forgone late sale shows (LAB review on #533)."""
    wo = [d for d in diffs if "diff" in d and written_off_row(bt, en, tuple(d["key"]))]
    later = [d for d in diffs if "diff" in d and d not in wo and written_off_later_sold(bt, en, tuple(d["key"]))]
    expl = [d for d in diffs if "diff" in d and d not in wo and d not in later and unsold_busy(bt, en, tuple(d["key"]))]
    return [d for d in diffs if d not in expl and d not in wo and d not in later], expl, wo, later


def arm_parity(store, a, b, arm, journal_dir, con=None, tail=ARM_TAIL, d_ms=600, segments=("pool_reversed",)):
    """An arm of the paper engine (farm organic, d_ms) against the backtest replay. Returns a dict: the
    comparison with busy over organic triggers only (every organic trigger; positions the engine closed at the
    data's end are left out) and with the tournament's busy (organic triggers of pools without a flagged trigger)."""
    cfg = load_defaults()
    con = con or streams.open_store(store, threads=2, memory="1GB")
    clock = Clock.from_store(con)
    segs = list(segments)
    bt_org = backtest_side(con, cfg, clock, a, b, var=arm, d_ms=d_ms, segments=segs, organic_busy=True)
    bt_tour = backtest_side(con, cfg, clock, a, b, var=arm, d_ms=d_ms, segments=segs, organic_busy=False)
    en, n_events = engine_side(store, a, b, journal_dir, clock, var=arm, d_ms=d_ms, segments=segs, farm="organic", tail=tail)
    open_at_end = {k for k, v in en.items() if v.get("exit_reason") == "end_of_data"}
    flagged_pools = {k[0] for k, v in bt_tour.items() if v.get("farm") == "flagged" and a - WARMUP <= k[1] < b}
    org = {k for k in set(bt_org) | set(en) if a <= k[1] < b and (bt_org.get(k) or en.get(k)).get("farm") == "organic"}
    keys1 = sorted(k for k in org if k not in open_at_end)
    _, d1, c1 = compare({k: bt_org[k] for k in keys1 if k in bt_org}, {k: en[k] for k in keys1 if k in en}, a, b)
    d1, e1, w1, s1 = _split(d1, bt_org, en)
    keys2 = sorted(k for k in keys1 if k[0] not in flagged_pools)
    _, d2, c2 = compare({k: bt_tour[k] for k in keys2 if k in bt_tour}, {k: en[k] for k in keys2 if k in en}, a, b)
    d2, e2, w2, s2 = _split(d2, bt_tour, en)
    in_flagged = [k for k in keys1 if k[0] in flagged_pools]
    _, d3, _ = compare({k: bt_tour[k] for k in in_flagged if k in bt_tour}, {k: en[k] for k in in_flagged if k in en}, a, b)
    pnl = lambda side, ks: sum((side.get(k) or {}).get("pnl") or 0 for k in ks)  # noqa: E731
    return {"arm": variant(arm)[0], "slots": [a, b], "d_ms": d_ms, "engine_events": n_events,
            "organic_busy": {"triggers": len(keys1), "by_status": c1, "different": len(d1), "diffs": d1[:20],
                             "unsold_busy": len(e1), "unsold_busy_keys": [d["key"] for d in e1],
                             "unsold_busy_pnl_backtest": sum((bt_org[tuple(d["key"])] or {}).get("pnl") or 0 for d in e1),
                             "written_off": len(w1), "written_off_keys": [d["key"] for d in w1],
                             "written_off_later_sold": len(s1), "written_off_later_sold_keys": [d["key"] for d in s1],
                             "written_off_later_sold_pnl_backtest": sum((bt_org[tuple(d["key"])] or {}).get("pnl") or 0 for d in s1),
                             "open_at_data_end": len(open_at_end), "pnl_backtest": pnl(bt_org, keys1), "pnl_engine": pnl(en, keys1)},
            "tournament_busy": {"triggers": len(keys2), "by_status": c2, "different": len(d2), "diffs": d2[:20],
                                "unsold_busy": len(e2), "written_off": len(w2), "written_off_later_sold": len(s2)},
            "flagged_pools": {"organic_triggers": len(in_flagged), "different_from_the_tournament": len(d3)}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="a", type=int, default=450144000)
    ap.add_argument("--to", dest="b", type=int, default=450360000)
    ap.add_argument("--out", required=True)
    ap.add_argument("--store", default=STORE)
    ap.add_argument("--arm", help="a paper arm (an exit variant, e.g. flow300_H6) on the organic triggers, d_ms 600")
    ap.add_argument("--tail", type=int, default=ARM_TAIL, help="--arm: slots of data after --to")
    ap.add_argument("--journal-a", help="journal against journal: the reference journal directory (paper/journal_diff.py)")
    ap.add_argument("--journal-b", help="journal against journal: the other journal directory")
    ap.add_argument("--from-day", help="journal against journal: the first UTC day compared")
    ap.add_argument("--to-day", help="journal against journal: the last UTC day compared")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    if a.journal_a or a.journal_b:
        if not (a.journal_a and a.journal_b):
            raise SystemExit("give both --journal-a and --journal-b")
        from .journal_diff import run

        raise SystemExit(0 if run(a.journal_a, a.journal_b, a.out, a.from_day, a.to_day)["green"] else 1)
    if a.arm:
        t0 = time.time()
        res = arm_parity(a.store, a.a, a.b, a.arm, os.path.join(a.out, "journal"), tail=a.tail)
        res["seconds"] = round(time.time() - t0)
        with open(os.path.join(a.out, "parity.json"), "w") as f:
            json.dump(res, f, indent=1, default=str)
        o, t = res["organic_busy"], res["tournament_busy"]
        lines = [f"# Paper arm parity {res['arm']}, slots {a.a}–{a.b} (organic triggers, d_ms {res['d_ms']})", "",
                 f"- busy over organic triggers (the engine's arm): {o['triggers']} triggers {o['by_status']}, different "
                 f"{o['different']} unexplained, {o['unsold_busy']} unsold_busy (the backtest's busy frees a pool at the trigger "
                 f"of a position that never sells; its P&L {o['unsold_busy_pnl_backtest']}), {o['written_off']} written_off (the "
                 f"backtest's unsold position, written off by the engine at its rule's deadline), {o['written_off_later_sold']} "
                 f"written_off_later_sold (the backtest sold it later: a forgone late sale; its backtest P&L "
                 f"{o['written_off_later_sold_pnl_backtest']}); P&L backtest {o['pnl_backtest']}, "
                 f"engine {o['pnl_engine']} lamports; open at the data's end (not compared) {o['open_at_data_end']}",
                 f"- the tournament's busy, pools without a flagged trigger: {t['triggers']} triggers {t['by_status']}, different "
                 f"{t['different']} unexplained, {t['unsold_busy']} unsold_busy, {t['written_off']} written_off, "
                 f"{t['written_off_later_sold']} written_off_later_sold",
                 f"- organic triggers in pools with a flagged trigger: {res['flagged_pools']['organic_triggers']}, different from "
                 f"the tournament {res['flagged_pools']['different_from_the_tournament']} (the documented difference, spec §2)",
                 f"- engine {res['engine_events']} events, {res['seconds']} s", ""]
        lines += [f"- {d}" for d in o["diffs"] + t["diffs"]]
        with open(os.path.join(a.out, "parity.md"), "w") as f:
            f.write("\n".join(lines) + "\n")
        print("\n".join(lines[:6]))
        return
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
