"""Read-only view of the paper engine's journal and the live recorder's meter, for the web app's Paper tab.

Sources (never written to):
  journal   <journal>/journal.sqlite of lab/paper (#175/#183): signals, orders, fills, exits, heartbeat,
            usage, positions. LAB_PAPER_JOURNAL picks one; otherwise the most recently written journal under
            <data>/journal/*/journal/. Opened with mode=ro and query_only (the engine keeps writing in WAL).
  recorder  <root>/budget.json and <root>/segments/*/_segment.json of lab/recorder (#182). LAB_RECORDER_ROOT
            picks the root (default <data>/recorder); without a budget.json the page shows the sample in
            lab/ui/paper_sample/recorder, marked as such.

The numbers mirror lab/paper/report.py (daily report). Which exits the stop rule counts, what a trade is and the
clock come from lab/paper/stoprule.py, the same functions the report calls: the primary group (K3) only, filled
trades (a failed entry costs but is no trade), and with a clock start the trades entered from its day; without one
a count of every paper day, no decision. The capital simulation (backtest.report.capital_sim, 10 SOL, 5 positions)
runs over the same trades. Further groups (H) get a measurement of their own, no test. The CI is a day-clustered
bootstrap from 5 days on, in pure Python (the server has no numpy), so it can differ from the report's in the last
digit; mean, trades and verdict are the same.

Slots of the hold-out window (epochs 1047-1051) are never shown: a journal row that touches one is dropped
and counted, as is any recorder usage of such a segment.
"""
import bisect
import calendar
import glob
import heapq
import importlib.util
import json
import os
import random
import sqlite3
import sys
import time
from collections import defaultdict

DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
SAMPLE_RECORDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_sample", "recorder")
HOLDOUT = (452_304_000, 454_464_000)  # epochs 1047-1051, [start, end)
MIN_TRADES, MIN_DAYS_CI = 200, 5  # stop rule (backtest.report.MIN_TRADES, paper.report.MIN_DAYS_CI)
CAPITAL_SOL, MAX_POSITIONS = 10.0, 5
RUNNING_SECS = 180  # a heartbeat younger than this: the engine is running
USD_PER_GB, BUDGET_USD = 0.08, 2.0  # recorder defaults (--usd-per-gb, --budget-usd)
POSITIONS_SHOWN = 300
SLOT_COLS = ("t", "entry_slot", "exit_slot", "exit_decision_slot", "slot", "last_slot")
_segments = {}  # _segment.json path -> its summary (complete segments do not change); None: not shown
_names = {}  # journal key (mint or pool) -> {"mint", "symbol", "name"}; names do not change


def _load_stoprule():
    """lab/paper/stoprule.py by its path (stdlib only; the name 'paper' is this module's here)."""
    spec = importlib.util.spec_from_file_location(
        "lab_paper_stoprule", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "paper", "stoprule.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


stoprule = _load_stoprule()


def journal_path():
    p = os.environ.get("LAB_PAPER_JOURNAL")
    if p:
        p = os.path.join(p, "journal.sqlite") if os.path.isdir(p) else p
        return p if os.path.isfile(p) else None
    found = glob.glob(os.path.join(DATA, "journal", "*", "journal", "journal.sqlite"))
    return max(found, key=os.path.getmtime) if found else None


def recorder_root():
    root = os.environ.get("LAB_RECORDER_ROOT") or os.path.join(DATA, "recorder")
    return (root, False) if os.path.isfile(os.path.join(root, "budget.json")) else (SAMPLE_RECORDER, True)


def token_names(keys):
    """Symbol and name per journal key: a curve key is the mint, a pool key is looked up in the dev store's pools.
    Read-only DuckDB on analytics.duckdb and the store; keys found nowhere (tokens newer than the dev data) keep
    no name, and the page shows the address. Cached per process."""
    todo = [k for k in set(keys) if k and k not in _names]
    if todo:
        import duckdb
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "analytics"))
        import labstore
        con = duckdb.connect(":memory:", config={"memory_limit": "256MB", "threads": 1})
        try:
            con.execute("SET enable_progress_bar=false")
            con.execute(f"ATTACH '{os.path.join(DATA, 'analytics', 'analytics.duckdb')}' AS a (READ_ONLY)")
            labstore.attach(con, DATA)
            con.execute("CREATE TEMP TABLE k (key VARCHAR)")
            con.executemany("INSERT INTO k VALUES (?)", [[x] for x in todo])
            for key, mint, sym, name in con.execute("""
                    SELECT k.key, coalesce(t1.mint, p.mint), coalesce(t1.symbol, t2.symbol), coalesce(t1.name, t2.name)
                    FROM k LEFT JOIN a.token_summary t1 ON t1.mint = k.key
                    LEFT JOIN dev.pools p ON p.pool = k.key LEFT JOIN a.token_summary t2 ON t2.mint = p.mint""").fetchall():
                _names[key] = {"mint": mint, "symbol": sym, "name": name}
        except (duckdb.Error, SystemExit):
            return {k: _names.get(k, {}) for k in keys}
        finally:
            con.close()
    return {k: _names.get(k, {}) for k in keys}


def slot_clock(beats):
    """slot -> unix seconds, interpolated between heartbeats (minute of event time, last slot seen then).
    The heartbeat runs on block time, so this works for replay and live alike; resolution is about a minute."""
    pts = sorted({(b["last_slot"], b["minute"] * 60) for b in beats if b["last_slot"] and not b["note"]})
    if not pts:
        return lambda slot: None
    slots = [p[0] for p in pts]

    def at(slot):
        if slot is None:
            return None
        i = bisect.bisect_left(slots, slot)
        if i == 0:
            return pts[0][1]
        if i >= len(pts):
            return pts[-1][1]
        (s0, t0), (s1, t1) = pts[i - 1], pts[i]
        return int(t0 + (t1 - t0) * (slot - s0) / (s1 - s0)) if s1 > s0 else t0
    return at


def in_holdout(row):
    return any(isinstance(row.get(c), int) and HOLDOUT[0] <= row[c] < HOLDOUT[1] for c in SLOT_COLS)


class Journal:
    def __init__(self, path):
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        self.db.execute("PRAGMA query_only = 1")
        self.tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.dropped = 0

    def rows(self, table, where="1=1", params=()):
        if table not in self.tables:
            return []
        cur = self.db.execute(f"SELECT * FROM {table} WHERE {where}", params)
        names = [c[0] for c in cur.description]
        out = []
        for r in cur.fetchall():
            d = dict(zip(names, r))
            if in_holdout(d):
                self.dropped += 1
            else:
                out.append(d)
        return out

    def close(self):
        self.db.close()


def percentile(xs, p):
    """numpy's default (linear) percentile of a non-empty list."""
    xs = sorted(xs)
    k = (len(xs) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def boot(sums, cnts, n=1000, seed=7):
    """Day-clustered bootstrap of a mean from per-day sums and counts: (lo, hi) 95% (backtest.report._boot)."""
    if sum(cnts) < 2 or len(cnts) < 2:
        return None, None
    rng, k, means = random.Random(seed), len(cnts), []
    for _ in range(n):
        pick = [rng.randrange(k) for _ in range(k)]
        c = sum(cnts[i] for i in pick)
        if c:
            means.append(sum(sums[i] for i in pick) / c)
    return (percentile(means, 2.5), percentile(means, 97.5)) if means else (None, None)


def verdict(pnl_by_day, n, filled_by_day):
    """paper.report._verdict, in SOL: (mean, lo, hi, verdict). pnl_by_day holds every exit's P&L (failed entries'
    costs included), n and filled_by_day count the filled trades (stoprule.stop_rule_inputs)."""
    days = sorted(pnl_by_day)
    mean = sum(sum(v) for v in pnl_by_day.values()) / n / 1e9 if n else None
    lo = hi = None
    if len(days) >= MIN_DAYS_CI:
        lo, hi = boot([sum(pnl_by_day[d]) / 1e9 for d in days], [filled_by_day.get(d, 0) for d in days])
    return mean, lo, hi, stoprule.decide(mean, lo, n, MIN_TRADES)


def stop_rule(exits, orders, state, day):
    """The stop-rule counter and its trades on `day`, as the daily report counts them (stoprule.counted)."""
    primary, further = stoprule.parse_groups(state.get(stoprule.GROUPS_KEY))
    clock = stoprule.parse_clock(state.get(stoprule.CLOCK_KEY))
    trades = stoprule.counted(exits, orders, day, primary, clock)
    by_day, filled_by_day, n = stoprule.stop_rule_inputs([dict(x, pnl=x["pnl"] or 0) for x in trades])
    mean, lo, hi, v = verdict(by_day, n, filled_by_day)
    st = stoprule.clock_state(clock, day, n, MIN_TRADES)
    return {"trades": n, "entry_failed": len(trades) - n, "days": len(by_day), "min_trades": MIN_TRADES,
            "min_days_ci": MIN_DAYS_CI, "mean_sol_per_trade": mean, "ci_lo": lo, "ci_hi": hi, "verdict": v,
            "primary": sorted(primary) if primary else None,
            "clock": None if clock is None else {"date": str(clock.get("date")), "reason": str(clock.get("reason", ""))[:300],
                                                 "set_at": str(clock.get("set_at", ""))[:40], **st},
            "clock_days": stoprule.CLOCK_DAYS, "clock_max_days": stoprule.CLOCK_MAX_DAYS}, trades, primary, further


def capital_sim(trades, start=CAPITAL_SOL * 1e9, max_positions=MAX_POSITIONS):
    """backtest.report.capital_sim: one wallet, fixed size, at most max_positions open; a trade that does not
    fit is skipped. trades: (entry_slot, exit_slot, cost, pnl, failed) in lamports. Also returns the equity
    after each settled trade (for a small curve)."""
    cash, open_, locked, peak, mdd, taken, skipped, curve = start, [], 0, start, 0.0, 0, 0, []

    def settle_until(slot):
        nonlocal cash, locked, peak, mdd
        while open_ and open_[0][0] <= slot:
            _, cost, back = heapq.heappop(open_)
            cash, locked = cash + back, locked - cost
            eq = cash + locked
            peak = max(peak, eq)
            mdd = max(mdd, 1 - eq / peak)
            curve.append(eq / 1e9)

    for entry, exit_, cost, pnl, failed in sorted(trades, key=lambda x: (x[0], x[1] or 0)):
        settle_until(entry)
        if len(open_) >= max_positions or (not failed and cash < cost):
            skipped += 1
            continue
        taken += 1
        if failed:
            cash += pnl
            continue
        cash, locked = cash - cost, locked + cost
        heapq.heappush(open_, (exit_ or entry, cost, cost + pnl))
    settle_until(float("inf"))
    return cash, mdd, taken, skipped, curve


def thin(xs, points=200):
    if len(xs) <= points:
        return xs
    step = len(xs) / points
    return [xs[int(i * step)] for i in range(points)] + [xs[-1]]


def group_stats(xs):
    """Per family x variant, as the daily report: trades = filled, failed entries apart, net SOL per trade over all."""
    filled = [x for x in xs if x["tokens"]]
    pnl = sum(x["pnl"] or 0 for x in xs)
    return {"trades": len(filled), "entry_failed": len(xs) - len(filled), "net_sol": pnl / 1e9,
            "net_sol_per_trade": pnl / len(xs) / 1e9 if xs else None,
            "win_rate": sum(1 for x in filled if (x["pnl"] or 0) > 0) / len(filled) if filled else None}


def range_stats(xs, shadow):
    """Hero card for one period: net SOL, trades, win rate, return per trade (net / invested) and the difference
    to the N2 shadow in percentage points, plus the cumulative net SOL in exit order."""
    g = group_stats(xs)
    ret = lambda ys: (sum(y["pnl"] or 0 for y in ys) / sum(y["cost"] or 0 for y in ys if y["tokens"])  # noqa: E731
                      if any(y["tokens"] and y["cost"] for y in ys) else None)
    own, sh = ret(xs), ret(shadow)
    cum, curve = 0, []
    for x in sorted(xs, key=lambda x: (x["exit_slot"] or x["entry_slot"] or 0)):
        cum += x["pnl"] or 0
        curve.append(round(cum / 1e9, 4))
    return {**g, "ret_per_trade": own, "vs_n2_pt": (own - sh) * 100 if own is not None and sh is not None else None,
            "curve": thin(curve, 120)}


def utc_day(unix):
    return time.strftime("%Y-%m-%d", time.gmtime(unix))


def journal_view(path, now):
    j = Journal(path)
    try:
        beats = j.rows("heartbeat")
        exits = j.rows("exits")
        orders = j.rows("orders")
        signals = {(s["family"], s["variant"], s["key"], s["t"]): s for s in j.rows("signals")}
        positions = j.rows("positions", "status = 'open'")
        usage = j.rows("usage")
        state = {r["k"]: r["v"] for r in j.rows("state")}
    finally:
        j.close()
    run = os.path.basename(os.path.dirname(os.path.dirname(path)))
    pulse = [b for b in beats if not b["note"]]
    last = max(pulse, key=lambda b: b["minute"]) if pulse else None
    days = sorted({x["day"] for x in exits if x["day"]} | {b["day"] for b in beats if b["day"]})
    today = utc_day(now)
    day = today if today in days else (days[-1] if days else today)
    gaps = [b for b in beats if b["note"] and b["day"] == day]
    status = {
        "running": bool(last) and now - last["minute"] * 60 <= RUNNING_SECS,
        "last_heartbeat_unix": last["minute"] * 60 if last else None, "last_slot": last["last_slot"] if last else None,
        "open_positions": last["open_positions"] if last else None,
        "gaps_today": len(gaps), "largest_gap_slots": max((g["gap_slots"] for g in gaps), default=None),
        "last_gap": max(gaps, key=lambda g: g["minute"])["note"] if gaps else None,
        # The engine's config and risk file at start (lab/paper --config; sha256 in the journal's state).
        "config": os.path.basename(str(state["config"])) if state.get("config") else None,
        "config_sha256": str(state.get("config_sha256") or "")[:64] or None,
        "risk_sha256_at_start": str(state.get("risk_sha256_at_start") or "")[:64] or None,
    }

    # The page is the primary group's (K3) with its N2 shadows; further groups (H) get a measurement section apart.
    stop, counted, primary, further = stop_rule(exits, orders, state, day)
    ours = lambda f, v: not f.startswith("N2_") and (primary is None or v in primary)  # noqa: E731
    shade = lambda f, v: f.startswith("N2_") and (primary is None or v in primary)  # noqa: E731
    by_group = defaultdict(list)
    for x in exits:
        if x["day"] == day:
            by_group[(x["family"], x["variant"])].append(x)
    open_by = defaultdict(int)
    for p in positions:
        open_by[(p["family"], p["variant"])] += 1
    groups = sorted(set(by_group) | set(open_by))
    variants = [{"family": f, "variant": v, "open": open_by[(f, v)], **group_stats(by_group[(f, v)])}
                for f, v in groups if ours(f, v)]
    traded_today = [x for (f, v), xs in by_group.items() if ours(f, v) for x in xs]
    totals = {"open": sum(n for (f, v), n in open_by.items() if ours(f, v)), **group_stats(traded_today)}

    last_slot = last["last_slot"] if last else None
    clock = slot_clock(beats)

    def pos_row(p, x=None):
        s = signals.get((p["family"], p["variant"], p["key"], p["t"]), {})
        r = {"family": p["family"], "variant": p["variant"], "key": p["key"], "venue": s.get("venue"),
             "orientation": s.get("orientation"), "farm": s.get("farm"), "signal_slot": p["t"], "entry_slot": p["entry_slot"],
             "cost_sol": (p["cost"] or 0) / 1e9 if p.get("cost") is not None else None,
             "entry_time": clock(p["entry_slot"])}
        if x is None:  # open: the engine's mark (lab/paper #183); none in older journals
            r["slots"] = (last_slot - p["entry_slot"]) if last_slot and p.get("entry_slot") and last_slot >= p["entry_slot"] else None
            mark, mpnl = p.get("mark_proceeds"), p.get("mark_pnl")
            # mark_pnl = mark_proceeds - entry cost including fees: the same definition as exits.pnl, so the open
            # P&L does not jump at the close (mark - cost would leave out the entry costs).
            r.update(status="open", mark_sol=mark / 1e9 if mark is not None else None, mark_slot=p.get("mark_slot"),
                     pnl_sol=mpnl / 1e9 if mpnl is not None else None,
                     ret=mpnl / p["cost"] if mpnl is not None and p.get("cost") else None)
        else:
            r.update(status=x["exit_reason"], exit_slot=x["exit_slot"], exit_time=clock(x["exit_slot"]),
                     pnl_sol=(x["pnl"] or 0) / 1e9, ret=x["ret"],
                     failed=not x["tokens"],
                     slots=x["exit_slot"] - x["entry_slot"] if x["exit_slot"] and x["entry_slot"] else None)
        return r

    closed = sorted((x for x in traded_today), key=lambda x: (x["exit_slot"] or x["entry_slot"] or 0), reverse=True)
    pos = [pos_row(p) for p in positions if ours(p["family"], p["variant"])]
    pos += [pos_row(x, x) for x in closed[:POSITIONS_SHOWN]]
    shadow_today = sorted((x for (f, v), xs in by_group.items() if shade(f, v) for x in xs),
                          key=lambda x: (x["exit_slot"] or x["entry_slot"] or 0), reverse=True)
    shadow_rows = [pos_row(x, x) for x in shadow_today[:POSITIONS_SHOWN]]
    names = token_names([r["key"] for r in pos + shadow_rows])
    for r in pos + shadow_rows:
        r.update(names.get(r["key"], {}))

    traded = [x for x in exits if ours(x["family"], x["variant"])]
    shadow_all = [x for x in exits if shade(x["family"], x["variant"])]
    week = utc_day(calendar.timegm(time.strptime(day, "%Y-%m-%d")) - 6 * 86_400)
    ranges = {"1d": range_stats([x for x in traded if x["day"] == day], [x for x in shadow_all if x["day"] == day]),
              "7d": range_stats([x for x in traded if week <= x["day"] <= day], [x for x in shadow_all if week <= x["day"] <= day]),
              "all": range_stats(traded, shadow_all)}
    ticker = [{"key": x["key"], "ret": x["ret"], "failed": not x["tokens"]} for x in closed[:12]]
    tnames = token_names([t["key"] for t in ticker])
    for t in ticker:
        t.update(symbol=tnames[t["key"]].get("symbol"), mint=tnames[t["key"]].get("mint"))
    # The capital simulation over the stop rule's trades, as the daily report.
    final, mdd, taken, skipped, curve = capital_sim([(x["entry_slot"], x["exit_slot"], x["cost"] or 0, x["pnl"] or 0,
                                                      not x["tokens"]) for x in counted])
    capital = {"start_sol": CAPITAL_SOL, "max_positions": MAX_POSITIONS, "final_sol": final / 1e9, "max_drawdown": mdd,
               "taken": taken, "skipped": skipped, "curve": [round(c, 4) for c in thin(curve)]}

    n2 = []
    all_by = defaultdict(list)
    for x in exits:
        all_by[(x["family"], x["variant"])].append(x)
    for (f, var) in sorted(k for k in all_by if ours(*k)):
        shadow = all_by.get(("N2_" + f, var))
        own_t, sh_t = group_stats(by_group.get((f, var), [])), group_stats(by_group.get(("N2_" + f, var), []))
        own_a, sh_a = group_stats(all_by[(f, var)]), group_stats(shadow or [])
        diff = lambda a, b: (a["net_sol_per_trade"] - b["net_sol_per_trade"]  # noqa: E731
                             if a["net_sol_per_trade"] is not None and b["net_sol_per_trade"] is not None else None)
        n2.append({"family": f, "variant": var, "has_shadow": bool(shadow),
                   "own_sol_per_trade": own_a["net_sol_per_trade"], "n2_sol_per_trade": sh_a["net_sol_per_trade"],
                   "diff_all": diff(own_a, sh_a), "diff_today": diff(own_t, sh_t), "n2_trades": sh_a["trades"]})

    return {"journal": run, "test_run": run.startswith("parity"), "day": day, "is_today": day == today,
            "status": status, "variants": variants, "totals": totals, "positions": pos, "stop_rule": stop,
            "capital": capital, "n2": n2, "ranges": ranges, "ticker": ticker, "shadow_positions": shadow_rows,
            "arms": arm_groups(exits, positions, further, day), "journal_usage_rows": len(usage),
            "holdout_rows_dropped": j.dropped}


def arm_groups(exits, positions, further, day):
    """Further groups (H), per variant: a measurement, no test (paper.report.arm_lines). Up to `day`."""
    out = []
    for group, variants in sorted(further.items()):
        rows = []
        for v in variants:
            xs = [x for x in exits if x["family"] == "F7" and x["variant"] == v and x["day"] <= day]
            rows.append({"variant": v, **group_stats(xs), "today_sol": sum(x["pnl"] or 0 for x in xs if x["day"] == day) / 1e9,
                         "open": sum(1 for p in positions if p["family"] == "F7" and p["variant"] == v)})
        out.append({"group": group, "variants": rows})
    return out


def key_trades(key):
    """The bot's entries and exits on one key (mint or pool), all days, with their time: chart markers."""
    path = journal_path()
    if not path:
        return None
    j = Journal(path)
    try:
        beats = j.rows("heartbeat")
        exits = j.rows("exits", "key = ?", (key,))
        opens = j.rows("positions", "key = ? AND status = 'open'", (key,))
        signals = {(s["family"], s["variant"], s["t"]): s for s in j.rows("signals", "key = ?", (key,))}
    finally:
        j.close()
    clock = slot_clock(beats)
    out = []
    for x in exits:
        if not x["tokens"]:
            continue
        out.append({"family": x["family"], "variant": x["variant"], "side": "entry", "slot": x["entry_slot"],
                    "time": clock(x["entry_slot"]), "sol": (x["cost"] or 0) / 1e9})
        out.append({"family": x["family"], "variant": x["variant"], "side": "exit", "slot": x["exit_slot"],
                    "time": clock(x["exit_slot"]), "ret": x["ret"], "reason": x["exit_reason"], "pnl_sol": (x["pnl"] or 0) / 1e9})
    for p in opens:
        out.append({"family": p["family"], "variant": p["variant"], "side": "entry", "slot": p["entry_slot"],
                    "time": clock(p["entry_slot"]), "sol": (p["cost"] or 0) / 1e9, "open": True})
    venue = next((s.get("venue") for s in signals.values()), None)
    orient = next((s.get("orientation") for s in signals.values()), None)
    return {"key": key, "venue": venue, "orientation": orient, **token_names([key]).get(key, {}),
            "trades": sorted(out, key=lambda t: t["slot"] or 0)}


def _seg_summary(path):
    """A complete segment's _segment.json, reduced to what the pages use (segments never change once complete,
    so a month of them stays small in memory): bytes and messages per UTC day and stream, per-minute bytes of
    its last minutes, budget notes, reconnects, filter level, slots and write time."""
    if path in _segments:
        return _segments[path]
    try:
        with open(path) as f:
            m = json.load(f)
    except (OSError, ValueError):
        return None
    if not m.get("complete"):
        return None
    if any(HOLDOUT[0] <= (m.get(k) or 0) < HOLDOUT[1] for k in ("first_slot", "last_slot")):
        _segments[path] = None
        return None
    days, minutes, notes = defaultdict(lambda: defaultdict(lambda: [0, 0])), defaultdict(lambda: defaultdict(int)), []
    for u in m.get("usage", []):
        minute = u.get("minute") or 0
        if u.get("stream") == "budget":
            if u.get("note"):
                notes.append({"unix": minute * 60, "note": str(u["note"])[:300]})
            continue
        d = days[utc_day(minute * 60)][str(u.get("stream"))]
        d[0] += u.get("bytes") or 0
        d[1] += u.get("messages") or 0
        minutes[minute][str(u.get("stream"))] += u.get("messages") or 0
    flt = m.get("filter") or {}
    out = {"seq": m.get("seq"), "first_slot": m.get("first_slot"), "last_slot": m.get("last_slot"),
           "written_unix": m.get("written_unix"), "level": flt.get("level"), "dropped": list(flt.get("dropped") or []),
           "days": {k: dict(v) for k, v in days.items()}, "minutes": {k: dict(v) for k, v in minutes.items()},
           "notes": notes, "reconnects": [{"unix": r.get("unix"), "reason": str(r.get("reason", ""))[:200],
                                           "after_slot": r.get("after_slot")} for r in m.get("reconnects", [])],
           "dir": os.path.dirname(path)}
    if len(_segments) > 60_000:
        _segments.clear()
    _segments[path] = out
    return out


def _segments_since(root, since_unix):
    """Summaries of the complete segments written since since_unix, newest first."""
    out = []
    for d in sorted(glob.glob(os.path.join(root, "segments", "*")), reverse=True):
        s = _seg_summary(os.path.join(d, "_segment.json"))
        if s is None:
            continue
        if (s["written_unix"] or 0) < since_unix:
            break
        out.append(s)
    return out


def _budget(root):
    try:
        with open(os.path.join(root, "budget.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def recorder_view(now):
    """Today's data use per stream against the daily budget (Paper tab's Triton card and recorder panel)."""
    root, sample = recorder_root()
    budget = _budget(root)
    if budget is None:
        return None
    segs = _segments_since(root, now - 3 * 86_400)
    days = sorted({d for s in segs for d in s["days"]})
    today = utc_day(now)
    day = today if today in days or not days else days[-1]
    streams = defaultdict(lambda: [0, 0])
    for s in segs:
        for st, (b, n) in s["days"].get(day, {}).items():
            streams[st][0] += b
            streams[st][1] += n
    notes = [n for s in segs for n in s["notes"] if utc_day(n["unix"]) == day]
    newest = segs[0] if segs else {}
    return {
        "sample": sample, "day": day, "is_today": day == today,
        "streams": sorted(({"stream": st, "gb": b / 1e9, "messages": n} for st, (b, n) in streams.items()), key=lambda r: -r["gb"]),
        "spent_gb": budget.get("spent_gb"), "cap_gb": (budget.get("cap_bytes") or 0) / 1e9, "usd_per_gb": USD_PER_GB,
        "budget_usd": BUDGET_USD, "stopped": bool(budget.get("stopped")),
        "budget_day": utc_day(budget["day"] * 86400) if isinstance(budget.get("day"), int) else None,
        "level": newest.get("level"), "dropped": newest.get("dropped", []), "last_segment_unix": newest.get("written_unix"),
        "last_slot": newest.get("last_slot"), "segments_today": sum(1 for s in segs if utc_day(s["written_unix"] or 0) == day),
        "reconnects": sum(1 for s in segs for r in s["reconnects"] if utc_day(r["unix"] or 0) == day),
        "notes": sorted(notes, key=lambda n: -n["unix"])[:20],
    }


def _latency(dirs):
    """Transport latency per transaction (receive time minus the server's created_at) over the newest segments'
    arrivals/*.parquet: p50, p90 and a 10-bucket histogram in ms. None without arrivals."""
    files = [f for d in dirs for f in glob.glob(os.path.join(d, "arrivals", "*.parquet"))]
    if not files:
        return None
    import duckdb
    con = duckdb.connect(":memory:", config={"memory_limit": "256MB", "threads": 1})
    try:
        ms = [r[0] for r in con.execute(
            "SELECT (received_unix_ns - created_unix_ns) / 1e6 FROM read_parquet(?) "
            "WHERE created_unix_ns > 0 AND received_unix_ns >= created_unix_ns", [files]).fetchall()]
    except duckdb.Error:
        return None
    finally:
        con.close()
    if not ms:
        return None
    edges = [0, 50, 100, 150, 200, 300, 400, 600, 800, 1200]
    hist = [0] * len(edges)
    for v in ms:
        hist[max(i for i, e in enumerate(edges) if v >= e)] += 1
    return {"p50": percentile(ms, 50), "p90": percentile(ms, 90), "n": len(ms), "edges_ms": edges, "hist": hist}


def triton_view(now):
    """Triton page: cost today and this month against the budget, a forecast, the manual balance, streams with
    their rate, transport latency, the OF1 reconciliation and recent events. From the recorder's files only."""
    root, sample = recorder_root()
    budget = _budget(root)
    if budget is None:
        return None
    month0 = calendar.timegm(time.strptime(utc_day(now)[:7] + "-01", "%Y-%m-%d"))
    segs = _segments_since(root, month0 - 3600)
    rec = recorder_view(now)
    month_bytes = sum(b for s in segs for d, sts in s["days"].items() if d[:7] == utc_day(now)[:7] for b, _ in sts.values())
    days_in_month = calendar.monthrange(*map(int, utc_day(now)[:7].split("-")))[1]
    elapsed = max(1 / 24, (now - month0) / 86_400)
    month_usd = month_bytes / 1e9 * USD_PER_GB
    # Message rate per stream: the newest 5 minutes with usage rows.
    mins = sorted({m for s in segs[:30] for m in s["minutes"]}, reverse=True)[:5]
    rate = defaultdict(int)
    for s in segs[:30]:
        for m in mins:
            for st, n in s["minutes"].get(m, {}).items():
                rate[st] += n
    streams = [dict(st, per_min=rate.get(st["stream"], 0) / max(1, len(mins)), dropped=st["stream"] in (rec or {}).get("dropped", []))
               for st in (rec or {}).get("streams", [])]
    balance = None
    try:
        with open(os.path.join(DATA, "ui-cache", "triton.json")) as f:
            b = json.load(f)
        if isinstance(b.get("balance_usd"), (int, float)):
            balance = {"usd": float(b["balance_usd"]), "as_of": str(b.get("as_of", ""))[:20]}
    except (OSError, ValueError, AttributeError):
        pass
    recon = None
    try:
        with open(os.path.join(root, "reconcile.json")) as f:
            r = json.load(f)
        recon = {"ok": bool(r.get("ok")), **{k: int(r.get("totals", {}).get(k, 0)) for k in
                                             ("missing", "extra", "mismatched", "segments_checked", "segments_waiting")},
                 "last_slot": max((s["slots"][1] for s in r.get("segments", []) if s.get("slots")), default=None)}
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    events = [{"unix": n["unix"], "kind": "budget", "text": n["note"]} for s in segs[:2000] for n in s["notes"]]
    events += [{"unix": r["unix"], "kind": "reconnect", "text": f"herverbonden na slot {r['after_slot']}: {r['reason']}"}
               for s in segs[:2000] for r in s["reconnects"] if r["unix"]]
    return {
        "sample": sample, "recorder": rec, "month_usd": month_usd, "month_gb": month_bytes / 1e9,
        "forecast_usd": month_usd / elapsed * days_in_month, "balance": balance,
        "streams": streams, "latency": _latency([s["dir"] for s in segs[:20]]), "reconcile": recon,
        "events": sorted(events, key=lambda e: -e["unix"])[:20],
    }


RISK_DEFAULT = "/home/chupa/Solana-project/worktrees/lab-paper-live/lab/paper/risk.yaml"  # the paper engine's checkout
SAMPLE_WALLET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_sample", "wallet.json")
ADDRESS_RE = __import__("re").compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def risk_limits():
    """The paper engine's owner-only limits (lab/paper/risk.yaml: flat 'key: value' lines). None if not found."""
    path = os.environ.get("LAB_PAPER_RISK") or RISK_DEFAULT
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        return None
    out = {"path": path}
    for line in text.splitlines():
        k, _, v = line.split("#", 1)[0].partition(":")
        k, v = k.strip(), v.strip()
        if k and v:
            try:
                out[k] = float(v)
            except ValueError:
                out[k] = {"true": True, "false": False}.get(v.lower(), v)
    return out


def _wallet_state(journal):
    """The live engine's <journal>/wallet.json (format in lab/ui/web/README.md), else the marked sample."""
    path = os.environ.get("LAB_WALLET_STATE") or (os.path.join(os.path.dirname(journal), "wallet.json") if journal else "")
    sample = not (path and os.path.isfile(path))
    try:
        with open(SAMPLE_WALLET if sample else path) as f:
            w = json.load(f)
    except (OSError, ValueError):
        return None
    num = lambda v: v if isinstance(v, int) else None  # noqa: E731
    txs = []
    for t in (w.get("txs") or [])[:50]:
        if not isinstance(t, dict) or (isinstance(t.get("slot"), int) and HOLDOUT[0] <= t["slot"] < HOLDOUT[1]):
            continue
        txs.append({"signature": str(t.get("signature", ""))[:100], "slot": num(t.get("slot")), "time_unix": num(t.get("time_unix")),
                    "kind": str(t.get("kind", ""))[:10], "mint": t.get("mint") if isinstance(t.get("mint"), str) else None,
                    "symbol": str(t.get("symbol") or "")[:20] or None, "amount_sol": (num(t.get("amount_lamports")) or 0) / 1e9,
                    "fee_sol": (num(t.get("fee_lamports")) or 0) / 1e9, "tip_sol": (num(t.get("tip_lamports")) or 0) / 1e9,
                    "status": str(t.get("status", ""))[:12], "landed_slot_delta": num(t.get("landed_slot_delta")),
                    "note": str(t.get("note") or "")[:80] or None})
    bal = num(w.get("balance_lamports"))
    # The engine's paper wallet (mode "paper", owner's decision: no real money) is shown as paper, not on-chain.
    paper_mode = w.get("mode") == "paper"
    names = token_names([t["mint"] for t in txs if t["mint"] and not t["symbol"]])
    for t in txs:
        n = names.get(t["mint"]) or {}
        if not t["symbol"] and n.get("symbol"):
            t["symbol"] = str(n["symbol"])[:20]
        if n.get("mint"):
            t["mint"] = n["mint"]
    return {"sample": sample, "mode": "paper" if paper_mode else "onchain",
            "address": None if paper_mode else (w.get("address") if isinstance(w.get("address"), str) and ADDRESS_RE.match(w["address"]) else None),
            "slot": num(w.get("slot")), "time_unix": num(w.get("time_unix")), "balance_sol": bal / 1e9 if bal is not None else None,
            "note": str(w.get("note") or "")[:200] or None, "txs": txs}


def configured_address():
    """The bot wallet's public address from LAB_BOT_WALLET or <data>/ui-cache/bot-wallet.json; never a key."""
    a = os.environ.get("LAB_BOT_WALLET")
    if not a:
        try:
            with open(os.path.join(DATA, "ui-cache", "bot-wallet.json")) as f:
                a = json.load(f).get("address")
        except (OSError, ValueError, AttributeError):
            a = None
    return a if isinstance(a, str) and ADDRESS_RE.match(a) else None


def wallet_view(now):
    """Wallet page. Paper part (real): 10 SOL plus realised P&L, cost of open positions, today's result against
    the daily loss limit, and the paper orders as transactions. On-chain part: the live engine's wallet.json, or
    the sample (marked) until there is one. Nothing here calls a node or touches a key."""
    path = journal_path()
    paper = None
    if path:
        j = Journal(path)
        try:
            exits = j.rows("exits")
            opens = j.rows("positions", "status = 'open'")
            orders = j.rows("orders")
            fills = j.rows("fills")
            beats = j.rows("heartbeat")
            state = {r["k"]: r["v"] for r in j.rows("state")}
        finally:
            j.close()
        clock = slot_clock(beats)
        primary, _ = stoprule.parse_groups(state.get(stoprule.GROUPS_KEY))
        # The bot's wallet is the primary group's (K3); the arms (H) keep their own paper wallet.
        own = lambda r: not r["family"].startswith("N2_") and (primary is None or r["variant"] in primary)  # noqa: E731
        traded = [x for x in exits if own(x)]
        days = sorted({x["day"] for x in traded if x["day"]})
        today = utc_day(now)
        day = today if today in days or not days else days[-1]
        t_day = [x for x in traded if x["day"] == day]
        failed = [x for x in t_day if not x["tokens"]]
        risk = risk_limits() or {}
        exit_of = {(x["family"], x["variant"], x["key"], x["t"]): x for x in traded}
        order_of = {(o["family"], o["variant"], o["key"], o["t"]): o for o in orders if own(o)}
        txs = []
        for f in fills:
            if not own(f):
                continue
            k = (f["family"], f["variant"], f["key"], f["t"])
            o, x = order_of.get(k, {}), exit_of.get(k, {})
            sell = f["side"] == "sell"
            txs.append({"key": f["key"], "kind": f["side"], "slot": f["slot"], "time_unix": clock(f["slot"]),
                        "amount_sol": ((f["quote"] or 0) if sell else -(f["quote"] or 0)) / 1e9, "status": "filled",
                        "delay_slots": (o.get("entry_slot") - f["t"]) if not sell and o.get("entry_slot") else None,
                        "reason": x.get("exit_reason") if sell else None, "ret": x.get("ret") if sell else None})
        for o in order_of.values():
            if o["status"] != "filled":
                x = exit_of.get((o["family"], o["variant"], o["key"], o["t"]), {})
                txs.append({"key": o["key"], "kind": "buy", "slot": o["entry_slot"], "time_unix": clock(o["entry_slot"]),
                            "amount_sol": (x.get("pnl") or 0) / 1e9, "status": o["status"], "delay_slots": None,
                            "reason": o.get("reason"), "ret": None})
        txs = sorted(txs, key=lambda t: t["slot"] or 0, reverse=True)[:40]
        names = token_names([t["key"] for t in txs])
        for t in txs:
            n = names.get(t["key"], {})
            t.update(symbol=n.get("symbol"), mint=n.get("mint"))
        realised = sum(x["pnl"] or 0 for x in traded) / 1e9
        today_net = sum(x["pnl"] or 0 for x in t_day) / 1e9
        # The wallet a bot with this capital would hold: the capital simulation (10 SOL, at most 5 positions) over the
        # stop rule's trades, as the Paper tab and the daily report; not the sum of every journaled trade (a parity
        # run trades without capital limits).
        _, counted, _, _ = stop_rule(exits, orders, state, day)
        final, _, taken, skipped, _ = capital_sim([(x["entry_slot"], x["exit_slot"], x["cost"] or 0, x["pnl"] or 0,
                                                    not x["tokens"]) for x in counted])
        paper = {"journal": os.path.basename(os.path.dirname(os.path.dirname(path))), "day": day, "is_today": day == today,
                 "start_sol": CAPITAL_SOL, "balance_sol": final / 1e9, "sim_taken": taken, "sim_skipped": skipped,
                 "realised_sol": realised, "test_run": os.path.basename(os.path.dirname(os.path.dirname(path))).startswith("parity"),
                 "in_positions_sol": sum(p["cost"] or 0 for p in opens if own(p)) / 1e9, "open_positions": sum(1 for p in opens if own(p)),
                 "today_net_sol": today_net, "failed_today": len(failed), "failed_cost_sol": -sum(x["pnl"] or 0 for x in failed) / 1e9,
                 "daily_loss_limit_sol": risk.get("daily_loss_limit_sol"), "risk_found": bool(risk),
                 "kill_switch": risk.get("kill_switch"), "max_open_positions": risk.get("max_open_positions"),
                 "size_sol": risk.get("size_sol"), "txs": txs}
    return {"paper": paper, "onchain": _wallet_state(path), "address": configured_address(), "now_unix": int(now)}


def view(now=None):
    now = int(now if now is not None else time.time())
    path = journal_path()
    return {"journal": journal_view(path, now) if path else None, "recorder": recorder_view(now), "now_unix": now}


def change_token():
    """Changes when the journal or the recorder writes (file mtimes): the SSE stream's trigger."""
    path = journal_path()
    root, _ = recorder_root()
    parts = []
    for f in ([path, path + "-wal"] if path else []) + [os.path.join(root, "budget.json"), os.path.join(root, "segments")]:
        try:
            parts.append(os.stat(f).st_mtime_ns)
        except OSError:
            parts.append(0)
    return "-".join(map(str, parts))


def available():
    return journal_path() is not None
