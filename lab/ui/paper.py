"""Read-only view of the paper engine's journal and the live recorder's meter, for the web app's Paper tab.

Sources (never written to):
  journal   <journal>/journal.sqlite of lab/paper (#175/#183): signals, orders, fills, exits, heartbeat,
            usage, positions. LAB_PAPER_JOURNAL picks one; otherwise the most recently written journal under
            <data>/journal/*/journal/. Opened with mode=ro and query_only (the engine keeps writing in WAL).
  recorder  <root>/budget.json and <root>/segments/*/_segment.json of lab/recorder (#182). LAB_RECORDER_ROOT
            picks the root (default <data>/recorder); without a budget.json the page shows the sample in
            lab/ui/paper_sample/recorder, marked as such.

The numbers mirror lab/paper/report.py (daily report). Which exits the stop rule counts, what a trade is, the
day bootstrap (10,000 draws), the verdict, the clock and what a verdict rests on come from lab/paper/stoprule.py,
the same functions the report calls, so mean, interval and verdict are exactly the report's: the primary group (K3)
only, filled trades (a failed entry costs but is no trade), and with a clock start the trades entered from its day;
without one a count of every paper day, no decision. A replay journal (a test, e.g. the parity run) is marked as
such (`source`, `test_run`): no paper trading, no decision. The capital simulation (backtest.report.capital_sim,
10 SOL, 5 positions) runs over the same trades. Further groups (H) get a measurement of their own, no test.

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
import sqlite3
import sys
import time
from collections import defaultdict

DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
SAMPLE_RECORDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_sample", "recorder")
HOLDOUT = (452_304_000, 454_464_000)  # epochs 1047-1051, [start, end)
CAPITAL_SOL, MAX_POSITIONS = 10.0, 5
RUNNING_SECS = 180  # a heartbeat younger than this: the engine is running
USD_PER_GB, BUDGET_USD = 0.08, 2.0  # recorder defaults (--usd-per-gb, --budget-usd)
POSITIONS_SHOWN = 300
SLOT_COLS = ("t", "entry_slot", "exit_slot", "exit_decision_slot", "slot", "last_slot")
_segments = {}  # _segment.json path -> its summary (complete segments do not change); None: not shown
SOL_MINT = "So11111111111111111111111111111111111111112"
_names = {}  # journal key (mint or pool) -> {"mint", "symbol", "name"}; names do not change


def _load_stoprule():
    """lab/paper/stoprule.py by its path (stdlib only; the name 'paper' is this module's here)."""
    spec = importlib.util.spec_from_file_location(
        "lab_paper_stoprule", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "paper", "stoprule.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


stoprule = _load_stoprule()
MIN_TRADES, MIN_DAYS_CI = stoprule.MIN_TRADES, stoprule.MIN_DAYS_CI


PAPER_RUN = "paper-k3"  # the paper run's journal, <data>/journal/paper-k3/journal (lab-paper.service, runbook §4)


def _activity(db):
    """When a journal last changed: in WAL mode the writes land in journal.sqlite-wal until a checkpoint."""
    return max(os.path.getmtime(f) for f in (db, db + "-wal") if os.path.exists(f))


def journal_path():
    """LAB_PAPER_JOURNAL, else the paper run's journal (paper-k3) once it exists, else the most recently written
    journal: a measuring-hour chain test or a parity run never takes the page from the paper run."""
    p = os.environ.get("LAB_PAPER_JOURNAL")
    if p:
        p = os.path.join(p, "journal.sqlite") if os.path.isdir(p) else p
        return p if os.path.isfile(p) else None
    run = os.path.join(DATA, "journal", PAPER_RUN, "journal", "journal.sqlite")
    if os.path.isfile(run):
        return run
    found = glob.glob(os.path.join(DATA, "journal", "*", "journal", "journal.sqlite"))
    return max(found, key=_activity) if found else None


def recorder_root():
    root = os.environ.get("LAB_RECORDER_ROOT") or os.path.join(DATA, "recorder")
    return (root, False) if os.path.isfile(os.path.join(root, "budget.json")) else (SAMPLE_RECORDER, True)


NAME_RETRY = 120  # seconds before a key without a name is looked up again (a live token's create may still come)
NAMES_MAX = 50_000  # bound of the name caches
_name_miss = {}  # key -> (unix of the last lookup without a symbol, what it did find: mint or {})
_name_seq = {}  # key -> the newest recorder segment already searched for it (later lookups read newer ones only)


def token_names(keys):
    """Symbol and name per journal key (a curve key is the mint, a pool key is mapped to its mint), from the data
    we hold, never an outside service:
    1. the development data: analytics token_summary and the dev store's pools;
    2. the paper run's store (the journal's state farmer_seed `store`, e.g. fwd1053-1054): pools -> mint, mints'
       name and symbol (pump.fun CreateEvent);
    3. backtest.token_meta's build when it exists (<data>/token_meta/token_meta.parquet);
    4. live, newer than any store: the journal's `learned` pools (pool -> mint) and the recorder's segments of the
       last 3 days (pump/CreateEvent with --pump-creates and token_meta for Metaplex / Token-2022 creates: name and
       symbol; pool_creations and CreatePoolEvent: pool -> mint), and the owner-approved RPC route's rows
       (<recorder>/rpc/token_meta, backtest.token_meta_rpc) when it runs.
    A key found nowhere (e.g. a token created between the store's end and the recorder's start) keeps its mint if
    known and no name; the page shows the shortened address. Names are user-chosen text: the page escapes them.
    Read-only, DuckDB in memory at 256 MB and one thread. Found names are cached; misses are retried after
    NAME_RETRY seconds, reading only segments newer than the last search."""
    now = time.time()
    todo = [k for k in set(keys) if k and k not in _names and now - _name_miss.get(k, (0, {}))[0] >= NAME_RETRY]
    if todo:
        found = _dev_names(todo)
        rest = [k for k in todo if not (found.get(k) or {}).get("symbol")]
        if rest:
            for k, v in _live_names(rest, {k: (found.get(k) or {}).get("mint") for k in rest}).items():
                found[k] = {**(found.get(k) or {}), **{a: b for a, b in v.items() if b}}
        if len(_names) > NAMES_MAX:
            _names.clear()
        if len(_name_miss) > NAMES_MAX:
            _name_miss.clear()
            _name_seq.clear()
        for k in todo:
            v = found.get(k) or {}
            if v.get("symbol") or v.get("name"):
                _names[k] = v
                _name_miss.pop(k, None)
                _name_seq.pop(k, None)
            else:
                _name_miss[k] = (now, {"mint": v["mint"]} if v.get("mint") else {})
    return {k: _names.get(k) or _name_miss.get(k, (0, {}))[1] for k in keys}


def _dev_names(keys):
    """Layer 1: the development data (analytics token_summary, the dev store's pools)."""
    import duckdb
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "analytics"))
    import labstore
    out = {}
    con = duckdb.connect(":memory:", config={"memory_limit": "256MB", "threads": 1})
    try:
        con.execute("SET enable_progress_bar=false")
        con.execute(f"ATTACH '{os.path.join(DATA, 'analytics', 'analytics.duckdb')}' AS a (READ_ONLY)")
        labstore.attach(con, DATA)
        con.execute("CREATE TEMP TABLE k (key VARCHAR)")
        con.executemany("INSERT INTO k VALUES (?)", [[x] for x in keys])
        for key, mint, sym, name in con.execute("""
                SELECT k.key, coalesce(t1.mint, p.mint), coalesce(t1.symbol, t2.symbol), coalesce(t1.name, t2.name)
                FROM k LEFT JOIN a.token_summary t1 ON t1.mint = k.key
                LEFT JOIN dev.pools p ON p.pool = k.key LEFT JOIN a.token_summary t2 ON t2.mint = p.mint""").fetchall():
            out[key] = {"mint": mint, "symbol": sym, "name": name}
    except (duckdb.Error, SystemExit):
        pass
    finally:
        con.close()
    return out


def _live_names(keys, mints):
    """Layers 2-4 for keys the development data does not name. mints: key -> mint already known (or None)."""
    import duckdb
    mint_of = {k: m or None for k, m in mints.items()}
    store, learned = None, {}
    path = journal_path()
    if path:
        j = Journal(path)
        try:
            state = {r["k"]: r["v"] for r in j.rows("state", "k = 'farmer_seed'")}
            try:
                store = json.loads(state.get("farmer_seed") or "{}").get("store")
            except (ValueError, AttributeError):
                store = None
            want = [k for k in keys if not mint_of.get(k)]
            if want and "learned" in j.tables:
                for r in j.db.execute(f"SELECT key, info FROM learned WHERE kind = 'pool' AND key IN ({','.join('?' * len(want))})", want):
                    try:
                        info = json.loads(r[1])
                        if len(info) > 3 and isinstance(info[3], str):
                            learned[r[0]] = info[3]  # [class, orientation, pool, mint, ...] (feed.PoolInfo)
                    except (ValueError, TypeError):
                        pass
        finally:
            j.close()
    for k, m in learned.items():
        mint_of[k] = mint_of.get(k) or m
    out = {k: {} for k in keys}
    con = duckdb.connect(":memory:", config={"memory_limit": "256MB", "threads": 1})
    try:
        con.execute("SET enable_progress_bar=false")
        if isinstance(store, str) and store.endswith(".duckdb") and os.path.isfile(store):
            try:
                con.execute(f"ATTACH '{store}' AS s (READ_ONLY)")
                want = [k for k in keys if not mint_of.get(k)]
                if want:
                    for pool, mint in con.execute("SELECT pool, mint FROM s.pools WHERE list_contains(?, pool)", [want]).fetchall():
                        mint_of[pool] = mint
                ms = sorted({mint_of.get(k) or k for k in keys})
                for mint, sym, name in con.execute("SELECT mint, symbol, name FROM s.mints WHERE list_contains(?, mint)", [ms]).fetchall():
                    for k in keys:
                        if (mint_of.get(k) or k) == mint:
                            out[k] = {"mint": mint, "symbol": sym, "name": name}
            except duckdb.Error:
                pass  # e.g. the store is being rebuilt: the next layer still runs
        rest = [k for k in keys if not out[k].get("symbol")]
        if rest and os.path.isfile(os.path.join(DATA, TOKEN_META)):
            _name_rows(con, rest, mint_of, out, [os.path.join(DATA, TOKEN_META)])
        rest = [k for k in keys if not out[k].get("symbol")]
        if rest:
            _segment_names(con, rest, mint_of, out)
    except duckdb.Error:
        pass
    finally:
        con.close()
    for k in keys:
        if mint_of.get(k) and not out[k].get("mint"):
            out[k]["mint"] = mint_of[k]
    return out


def _name_rows(con, keys, mint_of, out, files):
    """Names from parquet files with token_meta's columns (mint, name, symbol): pump.fun's CreateEvent, the
    extractor's or recorder's token_meta (Metaplex and Token-2022 creates), the RPC route's rows; first non-empty."""
    if not files:
        return
    ms = sorted({mint_of.get(k) or k for k in keys})
    rows = con.execute("""SELECT mint, any_value(symbol) FILTER (WHERE coalesce(symbol, '') <> ''),
                                 any_value(name) FILTER (WHERE coalesce(name, '') <> '')
                          FROM read_parquet(?, union_by_name = true) WHERE list_contains(?, mint) GROUP BY mint""", [files, ms]).fetchall()
    for mint, sym, name in rows:
        if not (sym or name):
            continue
        for k in keys:
            if (mint_of.get(k) or k) == mint and not out[k].get("symbol"):
                out[k] = {"mint": mint, "symbol": sym, "name": name}


TOKEN_META = "token_meta/token_meta.parquet"  # backtest.token_meta's build, if made (<data>/token_meta/)


def _segment_names(con, keys, mint_of, out):
    """Layer 3: the recorder's segments of the last 3 days, only those newer than the last search for these keys."""
    root, sample = recorder_root()
    if sample:
        return
    segs = _segments_since(root, time.time() - 3 * 86_400)
    if not segs:
        return
    since = min(_name_seq.get(k, -1) for k in keys)
    dirs = [x["dir"] for x in segs if (x["seq"] or 0) > since]
    if not dirs:
        return
    files = lambda sub: [f for d in dirs for f in glob.glob(os.path.join(d, sub, "*.parquet"))]  # noqa: E731
    pools = [k for k in keys if not mint_of.get(k)]
    if pools:
        for sub, sql in (("pool_creations", "SELECT pool, base_mint, quote_mint FROM read_parquet(?, union_by_name = true) WHERE list_contains(?, pool)"),
                         ("pump_amm/CreatePoolEvent", "SELECT pool, base_mint, quote_mint FROM read_parquet(?, union_by_name = true) WHERE list_contains(?, pool)")):
            fs = files(sub)
            if fs:
                for pool, base, quote in con.execute(sql, [fs, pools]).fetchall():
                    mint_of[pool] = mint_of.get(pool) or (quote if base == SOL_MINT else base)
    _name_rows(con, keys, mint_of, out, files("pump/CreateEvent") + files("token_meta")
               + glob.glob(os.path.join(root, "rpc", "token_meta", "*.parquet")))
    for k in keys:  # searched up to here: a later lookup reads newer segments only
        _name_seq[k] = segs[0]["seq"] or 0


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


percentile = stoprule.percentile  # numpy's default (linear) percentile


def verdict(pnl_by_day, n, filled_by_day):
    """paper.report._verdict (stoprule.verdict, lamports) in SOL: (mean, lo, hi, verdict)."""
    mean, lo, hi, v = stoprule.verdict(pnl_by_day, n, filled_by_day)
    sol = lambda x: None if x is None else x / 1e9  # noqa: E731
    return sol(mean), sol(lo), sol(hi), v


def _assessment_sol(a):
    """stoprule.assessment with its lamport amounts also in SOL (the page's unit)."""
    b = dict(a["basis"])
    b["mean_sol"] = None if b["mean_lamports"] is None else b["mean_lamports"] / 1e9
    b["ci95_sol"] = [x / 1e9 for x in b["ci95_lamports"]] if b["ci95_lamports"] else None
    b["costs_sol"] = {k: v / 1e9 for k, v in b["costs_lamports"].items()} if b["costs_lamports"] else None
    return dict(a, basis=b)


def stop_rule(exits, orders, state, day, live_rows=False):
    """The stop-rule counter and its trades on `day`, as the daily report counts and decides (paper/stoprule.py).
    live_rows: whether any signal has a live arrival time (the feed of a journal from before the state 'feed')."""
    primary, further = stoprule.parse_groups(state.get(stoprule.GROUPS_KEY))
    clock = stoprule.parse_clock(state.get(stoprule.CLOCK_KEY))
    trades = [dict(x, pnl=x["pnl"] or 0, cost=x.get("cost") or 0, proceeds=x.get("proceeds") or 0)
              for x in stoprule.counted(exits, orders, day, primary, clock)]
    by_day, filled_by_day, n = stoprule.stop_rule_inputs(trades)
    mean, lo, hi, v = stoprule.verdict(by_day, n, filled_by_day)
    st = stoprule.clock_state(clock, day, v)
    feed = stoprule.feed_of(state.get(stoprule.FEED_KEY), live_rows)
    scn = json.loads(state[stoprule.COSTS_KEY]) if state.get(stoprule.COSTS_KEY) else None
    judged = stoprule.assessment(trades, mean, lo, hi, v, st, scn, stoprule.primary_group(state.get(stoprule.GROUPS_KEY)), feed)
    sol = lambda x: None if x is None else x / 1e9  # noqa: E731
    return {"trades": n, "entry_failed": len(trades) - n, "days": len(by_day), "min_trades": MIN_TRADES,
            "min_days_ci": MIN_DAYS_CI, "mean_sol_per_trade": sol(mean), "ci_lo": sol(lo), "ci_hi": sol(hi), "verdict": v,
            "primary": sorted(primary) if primary else None,
            "clock": None if clock is None else {"date": str(clock.get("date")), "reason": str(clock.get("reason", ""))[:300],
                                                 "set_at": str(clock.get("set_at", ""))[:40], **st},
            "clock_days": stoprule.CLOCK_DAYS, "clock_max_days": stoprule.CLOCK_MAX_DAYS,
            "assessment": _assessment_sol(judged)}, trades, primary, further


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
    live_rows = any(s.get("received_unix_ns") for s in signals.values())
    stop, counted, primary, further = stop_rule(exits, orders, state, day, live_rows)
    feed = stop["assessment"]["feed"]
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
    # Further groups' (H) open positions and the day's exits, each row with its group: the arms' own fills and exits.
    arm_of = {v: g for g, vs in further.items() for v in vs}
    arm = lambda f, v: f == "F7" and v in arm_of  # noqa: E731
    arm_rows = [dict(pos_row(p), group=arm_of[p["variant"]]) for p in positions if arm(p["family"], p["variant"])]
    arm_closed = sorted((x for (f, v), xs in by_group.items() if arm(f, v) for x in xs),
                        key=lambda x: (x["exit_slot"] or x["entry_slot"] or 0), reverse=True)
    arm_rows += [dict(pos_row(x, x), group=arm_of[x["variant"]]) for x in arm_closed[:POSITIONS_SHOWN]]
    names = token_names([r["key"] for r in pos + shadow_rows + arm_rows])
    for r in pos + shadow_rows + arm_rows:
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

    # A replay journal (the parity run on dev data, a shadow) is a test, not paper trading: no decision.
    return {"journal": run, "test_run": feed != "live" or run.startswith("parity"), "day": day, "is_today": day == today,
            "source": {"feed": feed, "paper_trading": feed == "live" and not run.startswith("parity"), "journal": run},
            "status": status, "variants": variants, "totals": totals, "positions": pos, "stop_rule": stop,
            "capital": capital, "n2": n2, "ranges": ranges, "ticker": ticker, "shadow_positions": shadow_rows,
            "arms": arm_groups(exits, positions, further, day), "arm_positions": arm_rows, "journal_usage_rows": len(usage),
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
    cap_gb = (budget.get("cap_bytes") or 0) / 1e9  # the recorder's --budget-usd at USD_PER_GB
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
        "spent_gb": budget.get("spent_gb"), "cap_gb": cap_gb, "usd_per_gb": USD_PER_GB,
        "budget_usd": cap_gb * USD_PER_GB if cap_gb else BUDGET_USD, "stopped": bool(budget.get("stopped")),
        # Why the recorder stopped the day when today's spend could not be known (meter.rs `unknown`, exit 75).
        "unknown": str(budget["unknown"])[:300] if budget.get("unknown") else None,
        "budget_day": utc_day(budget["day"] * 86400) if isinstance(budget.get("day"), int) else None,
        "level": newest.get("level"), "dropped": newest.get("dropped", []), "last_segment_unix": newest.get("written_unix"),
        "last_slot": newest.get("last_slot"), "segments_today": sum(1 for s in segs if utc_day(s["written_unix"] or 0) == day),
        "reconnects": sum(1 for s in segs for r in s["reconnects"] if utc_day(r["unix"] or 0) == day),
        "notes": sorted(notes, key=lambda n: -n["unix"])[:20],
    }


LIVE_PAD = 9_000  # slots (~1 h) of live price around a paper position's trades
LIVE_MAX_FILES = 800  # at most this many segment files per chart (about 6 hours of one-minute segments)
_live_candles = {}  # (pool, lo, hi, newest segment) -> candles; small and bounded (LIVE_CACHE)
LIVE_CACHE = 16


def live_pool_candles(pool, slots, reversed_, now=None):
    """1-minute price candles of one PumpSwap pool from the live recorder's segments (pump_amm Buy/SellEvent
    parquet, the watchlist's trades), around the bot's trades: for tokens newer than the development store. Price
    in SOL per token as the store's (labstore: SOL reserve / token reserve / 1000 after the trade); `reversed_`: the
    pool's base is WSOL. Read-only, DuckDB in memory with a fixed budget (256 MB, one thread): the recorder and the
    paper engine come first on the VPS. [] without a recorder or without trades of the pool."""
    root, sample = recorder_root()
    if sample or not slots:
        return []
    lo, hi = max(HOLDOUT[1], min(slots) - LIVE_PAD), max(slots) + LIVE_PAD
    segs = [x for x in _segments_since(root, (now or time.time()) - 3 * 86_400)
            if x["first_slot"] is not None and x["last_slot"] is not None and x["last_slot"] >= lo and x["first_slot"] <= hi]
    if not segs:
        return []
    key = (pool, lo, hi, segs[0]["seq"], len(segs), bool(reversed_))
    if key in _live_candles:
        return _live_candles[key]
    files = {ev: [f for x in segs[:LIVE_MAX_FILES // 2] for f in glob.glob(os.path.join(x["dir"], "pump_amm", ev, "*.parquet"))]
             for ev in ("BuyEvent", "SellEvent")}
    parts, params = [], []
    for ev, base_col, quote_col in (("BuyEvent", "base_amount_out", "quote_amount_in"), ("SellEvent", "base_amount_in", "quote_amount_out")):
        if files[ev]:
            parts.append(f"""SELECT slot, tx_index, outer_ix, inner_ix, "timestamp" AS ts, pool_base_token_reserves AS b,
                                    pool_quote_token_reserves AS q, {base_col} AS base_amt, {quote_col} AS quote_amt, '{ev}' AS ev
                             FROM read_parquet(?, union_by_name = true) WHERE pool = ? AND slot BETWEEN ? AND ?""")
            params += [files[ev], pool, lo, hi]
    if not parts:
        return []
    rev = "true" if reversed_ else "false"
    sql = f"""
        WITH t AS ({" UNION ALL ".join(parts)}),
        p AS (SELECT *, CASE WHEN {rev} THEN b::DOUBLE / NULLIF(q::DOUBLE, 0) ELSE q::DOUBLE / NULLIF(b::DOUBLE, 0) END / 1000 AS price,
                     CASE WHEN {rev} THEN base_amt ELSE quote_amt END AS sol,
                     (ev = 'BuyEvent') <> {rev} AS token_buy FROM t)
        SELECT 'pool' AS venue, (ts // 60 * 60)::BIGINT AS time,
               arg_min(price, (slot, tx_index, outer_ix, inner_ix)) AS open, max(price) AS high, min(price) AS low,
               arg_max(price, (slot, tx_index, outer_ix, inner_ix)) AS "close", count(*) AS trades,
               sum(sol) / 1e9 AS volume_quote, coalesce(sum(sol) FILTER (WHERE token_buy), 0) / 1e9 AS buy_quote
        FROM p WHERE price > 0 AND ts > 0 GROUP BY 2 ORDER BY 2"""
    import duckdb
    con = duckdb.connect(":memory:", config={"memory_limit": "256MB", "threads": 1})
    try:
        cur = con.execute(sql, params)
        cols = [c[0] for c in cur.description]
        out = [dict(zip(cols, r)) for r in cur.fetchall()]
    except duckdb.Error:
        out = []
    finally:
        con.close()
    if len(_live_candles) >= LIVE_CACHE:
        _live_candles.pop(next(iter(_live_candles)))
    _live_candles[key] = out
    return out


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
    month_usd = month_bytes / 1e9 * USD_PER_GB
    # The forecast runs from the month's first recorded minute, not from the 1st: a recorder that starts mid-month
    # (the paper start, 2026-10-20) would otherwise spread its spend over days it did not run. The rate per day is
    # at most the daily cap (the recorder stops there); the month = spent so far + that rate over the days left.
    first = min((m * 60 for s in segs for m in s["minutes"] if m * 60 >= month0), default=None)
    elapsed = max(1 / 24, (now - (first if first is not None else now)) / 86_400)
    cap_usd = (rec or {}).get("budget_usd") or BUDGET_USD
    usd_rate = min(month_usd / elapsed, cap_usd)
    month_end = month0 + days_in_month * 86_400
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
        "forecast_usd": month_usd + usd_rate * max(0, month_end - now) / 86_400, "rate_usd_per_day": usd_rate,
        "since_unix": first, "balance": balance,
        "streams": streams, "latency": _latency([s["dir"] for s in segs[:20]]), "reconcile": recon,
        "events": sorted(events, key=lambda e: -e["unix"])[:20],
    }


RISK_DEFAULT = "/home/chupa/Solana-project/worktrees/paper-run/lab/paper/risk.yaml"  # the run checkout (runbook §3)
SAMPLE_WALLET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_sample", "wallet.json")
ADDRESS_RE = __import__("re").compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def risk_limits(path=None):
    """The paper engine's owner-only limits (lab/paper/risk.yaml: flat 'key: value' lines). None if not found.
    path: a group's risk file as the journal records it (state 'groups', `risk`: absolute, next to its config);
    else LAB_PAPER_RISK or the run checkout's risk.yaml."""
    ok = isinstance(path, str) and os.path.isabs(path) and path.endswith(".yaml")
    path = path if ok else os.environ.get("LAB_PAPER_RISK") or RISK_DEFAULT
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


def _wallet_state(journal, name="wallet.json", sample_ok=True, clock=None):
    """The live engine's <journal>/<name> (wallet.json for K3, a further group's own file such as wallet-h.json;
    format in lab/ui/web/README.md), else the marked sample (sample_ok) or None. clock: slot -> unix for the paper
    wallet's transactions, which carry no time of their own."""
    if os.path.basename(name) != name or not name.endswith(".json"):
        return None
    env = os.environ.get("LAB_WALLET_STATE") if name == "wallet.json" else None
    path = env or (os.path.join(os.path.dirname(journal), name) if journal else "")
    sample = not (path and os.path.isfile(path))
    if sample and not sample_ok:
        return None
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
        sig = t.get("signature")
        txs.append({"signature": sig[:100] if isinstance(sig, str) and sig else None, "slot": num(t.get("slot")),
                    "time_unix": num(t.get("time_unix")) or (clock(t["slot"]) if clock and isinstance(t.get("slot"), int) else None),
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
            "slot": num(w.get("slot")), "time_unix": num(w.get("time_unix")) or (clock(w["slot"]) if clock and num(w.get("slot")) else None),
            "balance_sol": bal / 1e9 if bal is not None else None,
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
        primary, further = stoprule.parse_groups(state.get(stoprule.GROUPS_KEY))
        settings = _group_settings(state)
        # The bot's wallet is the primary group's (K3); the arms (H) keep their own paper wallet.
        own = lambda r: not r["family"].startswith("N2_") and (primary is None or r["variant"] in primary)  # noqa: E731
        traded = [x for x in exits if own(x)]
        days = sorted({x["day"] for x in traded if x["day"]})
        today = utc_day(now)
        day = today if today in days or not days else days[-1]
        t_day = [x for x in traded if x["day"] == day]
        failed = [x for x in t_day if not x["tokens"]]
        risk = risk_limits(next((g["risk"] for g in settings.values() if g.get("primary")), None)) or {}
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
                 "size_sol": risk.get("size_sol"), "risk_file": os.path.basename(risk["path"]) if risk else None, "txs": txs}
        groups = [_group_wallet(name, set(variants), settings.get(name, {}), exits, opens, path, day, clock)
                  for name, variants in sorted(further.items())]
    else:
        groups, clock = [], None
    return {"paper": paper, "onchain": _wallet_state(path, clock=clock), "groups": groups, "address": configured_address(),
            "now_unix": int(now)}


def _group_settings(state):
    """The journal's groups (state 'groups', engine.groups_state): name -> {primary, variants, risk, wallet}."""
    try:
        gs = json.loads(state.get(stoprule.GROUPS_KEY) or "{}")
    except ValueError:
        return {}
    return {str(k): v for k, v in gs.items() if isinstance(v, dict)} if isinstance(gs, dict) else {}


def _group_wallet(name, variants, conf, exits, opens, journal, day, clock):
    """A further group's (H) paper wallet beside K3's: its own file (wallet-h.json), its own risk file and daily
    loss, as the engine keeps them per group; its trades are its variants' (no N2: the arms have no shadow)."""
    mine = lambda r: not r["family"].startswith("N2_") and r["variant"] in variants  # noqa: E731
    traded = [x for x in exits if mine(x)]
    t_day = [x for x in traded if x["day"] == day]
    failed = [x for x in t_day if not x["tokens"]]
    risk = risk_limits(conf.get("risk")) if conf.get("risk") else None
    return {"group": name, "variants": sorted(variants),
            "wallet": _wallet_state(journal, conf.get("wallet") or "", sample_ok=False, clock=clock),
            "wallet_file": conf.get("wallet"), "trades": sum(1 for x in traded if x["tokens"]),
            "realised_sol": sum(x["pnl"] or 0 for x in traded) / 1e9, "today_net_sol": sum(x["pnl"] or 0 for x in t_day) / 1e9,
            "failed_today": len(failed), "open_positions": sum(1 for p in opens if mine(p)),
            "in_positions_sol": sum(p["cost"] or 0 for p in opens if mine(p)) / 1e9,
            "risk_found": bool(risk), "risk_file": os.path.basename(risk["path"]) if risk else None,
            "daily_loss_limit_sol": (risk or {}).get("daily_loss_limit_sol"), "kill_switch": (risk or {}).get("kill_switch"),
            "max_open_positions": (risk or {}).get("max_open_positions"), "size_sol": (risk or {}).get("size_sol")}


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
