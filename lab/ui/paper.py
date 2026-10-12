"""Read-only view of the paper engine's journal and the live recorder's meter, for the web app's Paper tab.

Sources (never written to):
  journal   <journal>/journal.sqlite of lab/paper (#175/#183): signals, orders, fills, exits, heartbeat,
            usage, positions. LAB_PAPER_JOURNAL picks one; otherwise the most recently written journal under
            <data>/journal/*/journal/. Opened with mode=ro and query_only (the engine keeps writing in WAL).
  recorder  <root>/budget.json and <root>/segments/*/_segment.json of lab/recorder (#182). LAB_RECORDER_ROOT
            picks the root (default <data>/recorder); without a budget.json the page shows the sample in
            lab/ui/paper_sample/recorder, marked as such.

The numbers mirror lab/paper/report.py (daily report): trades and net SOL per trade count failed entries,
the stop-rule counter covers every paper day of the traded families (not N2), the CI is a day-clustered
bootstrap from 5 days on, and the capital simulation is backtest.report.capital_sim (10 SOL, 5 positions).
The bootstrap here is pure Python (the server has no numpy), so its CI can differ from the report's in the
last digit; mean, trades and verdict are the same.

Slots of the hold-out window (epochs 1047-1051) are never shown: a journal row that touches one is dropped
and counted, as is any recorder usage of such a segment.
"""
import calendar
import glob
import heapq
import json
import os
import random
import sqlite3
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
_segments = {}  # _segment.json path -> parsed (complete segments do not change)


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


def verdict(pnl_by_day, n):
    """paper.report._verdict, in SOL: (mean, lo, hi, verdict); 'too few trades' below MIN_TRADES."""
    days = sorted(pnl_by_day)
    mean = sum(sum(v) for v in pnl_by_day.values()) / n / 1e9 if n else None
    lo = hi = None
    if len(days) >= MIN_DAYS_CI:
        lo, hi = boot([sum(pnl_by_day[d]) / 1e9 for d in days], [len(pnl_by_day[d]) for d in days])
    if mean is None or n < MIN_TRADES:
        v = "too few trades"
    elif lo is not None and lo > 0:
        v = "GO"
    else:
        v = "ADJUST" if mean > 0 else "STOP"
    return mean, lo, hi, v


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
        signals = {(s["family"], s["variant"], s["key"], s["t"]): s for s in j.rows("signals")}
        positions = j.rows("positions", "status = 'open'")
        usage = j.rows("usage")
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
    }

    by_group = defaultdict(list)
    for x in exits:
        if x["day"] == day:
            by_group[(x["family"], x["variant"])].append(x)
    open_by = defaultdict(int)
    for p in positions:
        open_by[(p["family"], p["variant"])] += 1
    groups = sorted(set(by_group) | set(open_by))
    variants = [{"family": f, "variant": v, "open": open_by[(f, v)], **group_stats(by_group[(f, v)])}
                for f, v in groups if not f.startswith("N2_")]
    traded_today = [x for (f, _), xs in by_group.items() if not f.startswith("N2_") for x in xs]
    totals = {"open": sum(n for (f, _), n in open_by.items() if not f.startswith("N2_")), **group_stats(traded_today)}

    last_slot = last["last_slot"] if last else None

    def pos_row(p, x=None):
        s = signals.get((p["family"], p["variant"], p["key"], p["t"]), {})
        r = {"family": p["family"], "variant": p["variant"], "key": p["key"], "venue": s.get("venue"),
             "orientation": s.get("orientation"), "signal_slot": p["t"], "entry_slot": p["entry_slot"],
             "cost_sol": (p["cost"] or 0) / 1e9 if p.get("cost") is not None else None}
        if x is None:  # open: the engine journals no mark yet; shown when it does
            r["slots"] = (last_slot - p["entry_slot"]) if last_slot and p.get("entry_slot") else None
            mark = p.get("mark_proceeds")
            r.update(status="open", mark_sol=mark / 1e9 if mark is not None else None, mark_slot=p.get("mark_slot"),
                     pnl_sol=(mark - p["cost"]) / 1e9 if mark is not None and p.get("cost") else None)
        else:
            r.update(status=x["exit_reason"], exit_slot=x["exit_slot"], pnl_sol=(x["pnl"] or 0) / 1e9, ret=x["ret"],
                     failed=not x["tokens"],
                     slots=x["exit_slot"] - x["entry_slot"] if x["exit_slot"] and x["entry_slot"] else None)
        return r

    closed = sorted((x for x in traded_today), key=lambda x: (x["exit_slot"] or x["entry_slot"] or 0), reverse=True)
    pos = [pos_row(p) for p in positions if not p["family"].startswith("N2_")]
    pos += [pos_row(x, x) for x in closed[:POSITIONS_SHOWN]]

    traded = [x for x in exits if not x["family"].startswith("N2_")]
    shadow_all = [x for x in exits if x["family"].startswith("N2_")]
    week = utc_day(calendar.timegm(time.strptime(day, "%Y-%m-%d")) - 6 * 86_400)
    ranges = {"1d": range_stats([x for x in traded if x["day"] == day], [x for x in shadow_all if x["day"] == day]),
              "7d": range_stats([x for x in traded if week <= x["day"] <= day], [x for x in shadow_all if week <= x["day"] <= day]),
              "all": range_stats(traded, shadow_all)}
    ticker = [{"key": x["key"], "ret": x["ret"], "failed": not x["tokens"]} for x in closed[:12]]
    by_day = defaultdict(list)
    for x in traded:
        by_day[x["day"]].append(x["pnl"] or 0)
    mean, lo, hi, v = verdict(by_day, len(traded))
    stop = {"trades": len(traded), "days": len(by_day), "min_trades": MIN_TRADES, "min_days_ci": MIN_DAYS_CI,
            "mean_sol_per_trade": mean, "ci_lo": lo, "ci_hi": hi, "verdict": v}
    final, mdd, taken, skipped, curve = capital_sim([(x["entry_slot"], x["exit_slot"], x["cost"] or 0, x["pnl"] or 0,
                                                      not x["tokens"]) for x in traded])
    capital = {"start_sol": CAPITAL_SOL, "max_positions": MAX_POSITIONS, "final_sol": final / 1e9, "max_drawdown": mdd,
               "taken": taken, "skipped": skipped, "curve": [round(c, 4) for c in thin(curve)]}

    n2 = []
    all_by = defaultdict(list)
    for x in exits:
        all_by[(x["family"], x["variant"])].append(x)
    for (f, var) in sorted(k for k in all_by if not k[0].startswith("N2_")):
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
            "capital": capital, "n2": n2, "ranges": ranges, "ticker": ticker, "journal_usage_rows": len(usage), "holdout_rows_dropped": j.dropped}


def recorder_view(now):
    root, sample = recorder_root()
    try:
        with open(os.path.join(root, "budget.json")) as f:
            budget = json.load(f)
    except (OSError, ValueError):
        return None
    metas = []
    for d in sorted(glob.glob(os.path.join(root, "segments", "*")), reverse=True):
        p = os.path.join(d, "_segment.json")
        if p not in _segments:
            try:
                with open(p) as f:
                    m = json.load(f)
            except (OSError, ValueError):
                continue
            if not m.get("complete"):
                continue
            if len(_segments) > 5000:
                _segments.clear()
            _segments[p] = m
        m = _segments[p]
        metas.append(m)
        if len(metas) > 2000:
            break
    metas = [m for m in metas if not any(HOLDOUT[0] <= (m.get(k) or 0) < HOLDOUT[1] for k in ("first_slot", "last_slot"))]
    days = sorted({utc_day(u["minute"] * 60) for m in metas for u in m.get("usage", [])})
    today = utc_day(now)
    day = today if today in days or not days else days[-1]
    streams, notes, reconnects = defaultdict(lambda: [0, 0]), [], 0
    for m in metas:
        for u in m.get("usage", []):
            if utc_day(u["minute"] * 60) != day:
                continue
            if u.get("stream") == "budget":
                if u.get("note"):
                    notes.append({"unix": u["minute"] * 60, "note": str(u["note"])})
                continue
            streams[u["stream"]][0] += u.get("bytes") or 0
            streams[u["stream"]][1] += u.get("messages") or 0
        reconnects += sum(1 for r in m.get("reconnects", []) if utc_day(r.get("unix") or 0) == day)
    newest = metas[0] if metas else {}
    flt = newest.get("filter", {})
    cap_gb = (budget.get("cap_bytes") or 0) / 1e9
    return {
        "sample": sample, "day": day, "is_today": day == today,
        "streams": sorted(({"stream": s, "gb": b / 1e9, "messages": n} for s, (b, n) in streams.items()), key=lambda r: -r["gb"]),
        "spent_gb": budget.get("spent_gb"), "cap_gb": cap_gb, "usd_per_gb": USD_PER_GB, "budget_usd": BUDGET_USD,
        "stopped": bool(budget.get("stopped")),
        "budget_day": utc_day(budget["day"] * 86400) if isinstance(budget.get("day"), int) else None,
        "level": flt.get("level"), "dropped": flt.get("dropped", []), "last_segment_unix": newest.get("written_unix"),
        "last_slot": newest.get("last_slot"), "segments_today": sum(1 for m in metas if utc_day(m.get("written_unix") or 0) == day),
        "reconnects": reconnects, "notes": sorted(notes, key=lambda n: -n["unix"])[:20],
    }


def view(now=None):
    now = int(now if now is not None else time.time())
    path = journal_path()
    return {"journal": journal_view(path, now) if path else None, "recorder": recorder_view(now), "now_unix": now}


def available():
    return journal_path() is not None
