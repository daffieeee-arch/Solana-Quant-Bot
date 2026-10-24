"""Lab web app v0 backend: Python stdlib HTTP server + read-only DuckDB.

    "$LAB_PY" lab/ui/server.py --host 100.112.193.63 --port 8501

Serves the built frontend (default $LAB_DATA_ROOT/ui-cache/web) and a small JSON API over
analytics.duckdb (plus the dev store for a token's trade tape). Every request opens its own
read-only connection, so a rebuilt analytics.duckdb (build-and-swap) is picked up on the next
request. Binds only to 127.0.0.1 or the Tailscale IP; never 0.0.0.0.
"""
import argparse
import datetime as dt
import decimal
import gzip
import importlib.util
import json
import os
import re
import sqlite3
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import duckdb

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "analytics"))
import labstore  # noqa: E402


def _load_paper():
    """lab/ui/paper.py by its path, as the module 'lab_ui_paper': the name 'paper' is lab/paper's package, and both
    can be loaded in one process (pytest over lab/paper/tests and lab/ui/tests)."""
    if "lab_ui_paper" not in sys.modules:
        spec = importlib.util.spec_from_file_location("lab_ui_paper", os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["lab_ui_paper"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["lab_ui_paper"]


paper = _load_paper()


def _load_actions():
    """lab/ui/actions.py by its path, as 'lab_ui_actions' (the interactive UI's security layer, spec v0.3 §2)."""
    if "lab_ui_actions" not in sys.modules:
        spec = importlib.util.spec_from_file_location("lab_ui_actions", os.path.join(os.path.dirname(os.path.abspath(__file__)), "actions.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["lab_ui_actions"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["lab_ui_actions"]


actions = _load_actions()

DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
# The interactive UI (Bediening: pairing, confirmed actions, notes; lab/ui/actions.py) is off unless LAB_UI_ACTIONS=1
# (the owner's pause, 2026-10-20). Off: no Bediening tab, and /api/action/* answers 404 before the action layer, so
# nothing is written (no audit line, no session) and the app is read-only as before B0.
ACTIONS_ON = os.environ.get("LAB_UI_ACTIONS") == "1"
ANALYTICS = os.path.join(DATA, "analytics", "analytics.duckdb")
ALLOWED_HOSTS = {"127.0.0.1", "100.112.193.63"}
FORBIDDEN_PORTS = {3000, 443, 8443, 8787, 8771}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                                "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"),
}
MAX_ROWS = 2000
# Static files: content type from a fixed table by extension, never from the request (no header injection).
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8", ".webmanifest": "application/manifest+json; charset=utf-8",
    ".map": "application/json; charset=utf-8", ".txt": "text/plain; charset=utf-8", ".svg": "image/svg+xml",
    ".png": "image/png", ".ico": "image/x-icon", ".woff2": "font/woff2", ".woff": "font/woff",
}
PAPER_CHART_PAD = 40_000  # slots (~3 h) of price around a paper position's trades
STREAM_SLOTS = threading.BoundedSemaphore(8)  # concurrent SSE connections
STREAM_SECS = 600
MIN_TRADES = 200  # stop rule: below this a group is reported, not ranked
HOLDOUT_START = labstore.HOLDOUT_START


def connect(with_dev=False):
    con = duckdb.connect(":memory:", config={"memory_limit": "512MB", "threads": 2})
    con.execute(f"ATTACH '{ANALYTICS}' AS a (READ_ONLY)")
    if with_dev:
        labstore.attach(con, DATA)  # READ_ONLY, development-only meta check, TEMP VIEW pool_trades
    con.execute("USE a")
    con.execute("SET temp_directory=''; SET enable_progress_bar=false; SET TimeZone='UTC'")
    con.execute("SET autoinstall_known_extensions=false; SET autoload_known_extensions=false")
    con.execute("SET enable_external_access=false; SET lock_configuration=true")
    return con


def rows(con, sql, params=()):
    cur = con.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchmany(MAX_ROWS)]


def to_json(o):
    if isinstance(o, decimal.Decimal):
        return float(o)
    if isinstance(o, (dt.datetime, dt.date)):
        return o.isoformat()
    return str(o)


def clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def has_table(con, name):
    return con.execute("SELECT count(*) FROM duckdb_tables() WHERE database_name = 'a' AND table_name = ?",
                       [name]).fetchone()[0] > 0


# ---- API -------------------------------------------------------------------------------------

def api_meta(q):
    con = connect()
    meta = json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
    tabs = ["overview", "tokens", "ticker"]
    if any(cases_files()):
        tabs.insert(0, "cases")
    if paper.available():
        tabs.insert(0, "paper")
        tabs.append("wallet")
    if paper.recorder_view(time.time()) is not None:
        tabs.append("triton")
    if has_table(con, "strategy_summary") and con.execute("SELECT count(*) FROM strategy_summary").fetchone()[0]:
        tabs.insert(0, "strategies")
    if ACTIONS_ON:
        tabs.append("control")  # Bediening: pairing, confirmed actions, the audit log (lab/ui/actions.py)
    return {"meta": meta, "tabs": tabs}


def api_overview(q):
    con = connect()
    return {
        "coverage": rows(con, "SELECT * FROM data_coverage ORDER BY epoch"),
        "days": rows(con, """
            WITH c AS (SELECT CAST(create_time AS DATE) AS day, count(*) AS tokens_created,
                              count(*) FILTER (WHERE mayhem) AS mayhem_created
                       FROM token_summary GROUP BY 1),
                 g AS (SELECT CAST(complete_time AS DATE) AS day, count(*) AS graduations,
                              count(*) FILTER (WHERE in_create_slot) AS grad_in_create_slot
                       FROM graduations GROUP BY 1)
            SELECT * FROM c FULL JOIN g USING (day) ORDER BY day"""),
        "totals": rows(con, """
            SELECT count(*) AS tokens, count(*) FILTER (WHERE graduated) AS graduated,
                   count(*) FILTER (WHERE grad_in_create_slot) AS graduated_in_create_slot,
                   sum(curve_trades) AS curve_trades, sum(pool_trades) AS pool_trades,
                   median(curve_trades) AS median_curve_trades
            FROM token_summary""")[0],
    }


TOKEN_COLS = """mint, name, symbol, create_time, sol_quote, mayhem, cashback, holder_reward, graduated,
                grad_in_create_slot, curve_trades, pool_trades, total_volume_quote, curve_max_mcap, pool_max_mcap"""


SORTABLE = {"volume": "total_volume_quote", "new": "create_slot", "trades": "curve_trades + pool_trades",
            "symbol": "lower(symbol)", "name": "lower(name)", "create_time": "create_slot",
            "curve_trades": "curve_trades", "pool_trades": "pool_trades", "total_volume_quote": "total_volume_quote",
            "max_mcap": "greatest(coalesce(curve_max_mcap, 0), coalesce(pool_max_mcap, 0))"}


def api_tokens(q):
    con = connect()
    term = (q.get("q", [""])[0] or "").strip()[:64]
    limit = clamp(q.get("limit", [50])[0], 1, 200, 50)
    offset = clamp(q.get("offset", [0])[0], 0, 10_000_000, 0)
    col = SORTABLE.get(q.get("sort", ["volume"])[0], "total_volume_quote")
    direction = "ASC" if q.get("dir", ["desc"])[0] == "asc" else "DESC"
    graduated = q.get("graduated", [""])[0] == "1"
    where, params = [], []
    if term and len(term) >= 32 and term.isalnum():
        where.append("mint = ?")
        params.append(term)
    elif term:
        like = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(symbol ILIKE ? ESCAPE '\\' OR name ILIKE ? ESCAPE '\\' OR mint LIKE ? ESCAPE '\\')")
        params += [like, like, like]
    if graduated:
        where.append("graduated")
    sql = f"""SELECT {TOKEN_COLS},
                     greatest(coalesce(curve_max_mcap, 0), coalesce(pool_max_mcap, 0)) AS max_mcap
              FROM token_summary {"WHERE " + " AND ".join(where) if where else ""}
              ORDER BY {col} {direction} NULLS LAST, mint LIMIT ? OFFSET ?"""
    return {"tokens": rows(con, sql, params + [limit, offset])}


def api_token(q):
    mint = (q.get("mint", [""])[0] or "").strip()
    if not (32 <= len(mint) <= 44 and mint.isalnum()):
        raise ValueError("unknown mint")
    con = connect(with_dev=True)
    summary = rows(con, "SELECT * FROM token_summary WHERE mint = ?", [mint])
    if not summary:
        raise LookupError("token not in the development data")
    s = summary[0]
    candles = rows(con, """
        SELECT venue, epoch(minute)::BIGINT AS time, open, high, low, close, trades, volume_quote, buy_quote
        FROM token_candles_1m WHERE mint = ? ORDER BY minute, venue""", [mint])
    events = rows(con, """
        SELECT slot, epoch(time)::BIGINT AS time, kind, venue, side, sol, wallet, note
        FROM market_events WHERE mint = ? ORDER BY slot LIMIT 500""", [mint])
    tape = rows(con, """
        SELECT * FROM (
          SELECT 'curve' AS venue, c.slot, c.tx_index, b.block_time AS time,
                 CASE WHEN c.is_buy THEN 'buy' ELSE 'sell' END AS side,
                 c.q::DOUBLE / 1e9 AS quote, c.t::DOUBLE / 1e6 AS tokens,
                 c.vq::DOUBLE / NULLIF(c.vt::DOUBLE, 0) / 1000 AS price, c.trader
          FROM dev.curve c JOIN dev.blocks b USING (slot) WHERE c.mint = ?
          UNION ALL
          SELECT 'pool', p.slot, p.tx_index, b.block_time, p.kind,
                 p.quote::DOUBLE / 1e9, p.token_amount::DOUBLE / 1e6, p.price, p.trader
          FROM pool_trades p JOIN dev.blocks b USING (slot)
          WHERE p.mint = ?)
        ORDER BY slot DESC, tx_index DESC LIMIT 300""", [mint, mint])
    return {"token": s, "candles": candles, "events": events, "tape": tape}


def api_ticker(q):
    con = connect()
    limit = clamp(q.get("limit", [100])[0], 1, 500, 100)
    kind = q.get("kind", ["all"])[0]
    kinds = {"graduation": ["graduation"], "large": ["large_buy", "large_sell"]}.get(
        kind, ["graduation", "large_buy", "large_sell"])
    before = clamp(q.get("before", [10**12])[0], 0, 10**12, 10**12)
    events = rows(con, """
        SELECT slot, tx_index, epoch(time)::BIGINT AS time, kind, mint, symbol, name, venue, side, sol, wallet, note
        FROM market_events WHERE kind IN (SELECT unnest(?)) AND slot < ?
        ORDER BY slot DESC, tx_index DESC NULLS LAST LIMIT ?""", [kinds, before, limit])
    markers = rows(con, "SELECT slot, note FROM market_events WHERE kind = 'protocol_marker' ORDER BY slot")
    return {"events": events, "markers": markers}


def api_candles(q):
    """A token's 1-minute candles and market events only (no trade tape): the light chart for Casussen."""
    mint = (q.get("mint", [""])[0] or "").strip()
    if not (32 <= len(mint) <= 44 and mint.isalnum()):
        raise ValueError("unknown mint")
    con = connect()
    t = rows(con, "SELECT mint, symbol, name, supply_tokens, create_time FROM token_summary WHERE mint = ?", [mint])
    if not t:
        raise LookupError("token not in the development data")
    return {"token": t[0],
            "candles": rows(con, """SELECT venue, epoch(minute)::BIGINT AS time, open, high, low, close, trades,
                                           volume_quote, buy_quote
                                    FROM token_candles_1m WHERE mint = ? ORDER BY minute, venue""", [mint]),
            "events": rows(con, """SELECT slot, epoch(time)::BIGINT AS time, kind, venue, side, sol, wallet, note
                                   FROM market_events WHERE mint = ? ORDER BY slot LIMIT 500""", [mint])}


# ---- Casussen: lab/wallets/cases-v1 (written by another session; read on every request, so a re-run that
# replaces the files shows up without a restart). Only the cases directory is readable from this connection.
CASES = os.path.join(DATA, "wallets", "cases-v1")
DAY_FILE = re.compile(r"^cases-(\d{4}-\d{2}-\d{2})\.parquet$")
WINDOW_FILE = re.compile(r"^cases-(7d-\d{9})\.parquet$")
SLOTISH = re.compile(r"slot|_t_dec$")


def cases_files():
    try:
        names = os.listdir(CASES)
    except FileNotFoundError:
        return {}, {}
    days = {m.group(1): n for n in names if (m := DAY_FILE.match(n))}
    windows = {m.group(1): n for n in names if (m := WINDOW_FILE.match(n))}
    return days, windows


def cases_con():
    con = duckdb.connect(":memory:", config={"memory_limit": "256MB", "threads": 1})
    con.execute("SET enable_progress_bar=false; SET autoinstall_known_extensions=false; SET autoload_known_extensions=false")
    con.execute("SET allowed_directories=[?]", [CASES.rstrip("/") + "/"])
    con.execute("SET enable_external_access=false; SET lock_configuration=true")
    return con


def dev_only(d):
    """False if any slot-like field reaches the hold-out (the cases are development-only; this is the guard)."""
    return not any(SLOTISH.search(k) and isinstance(v, int) and v >= HOLDOUT_START for k, v in d.items())


def api_cases(q):
    days, windows = cases_files()
    key = q.get("day", [""])[0] or q.get("window", [""])[0]
    if not key:
        mt = [os.path.getmtime(os.path.join(CASES, n)) for n in (*days.values(), *windows.values())]
        return {"days": sorted(days), "windows": sorted(windows), "updated_unix": int(max(mt)) if mt else None}
    name = days.get(key) or windows.get(key)
    if not name:
        raise LookupError("geen casussen voor deze periode")
    con = cases_con()
    cur = con.execute("SELECT * FROM read_parquet(?)", [os.path.join(CASES, name)])
    cols = [d[0] for d in cur.description]
    types = {d[0]: str(d[1]) for d in cur.description}
    data = [dict(zip(cols, r)) for r in cur.fetchmany(MAX_ROWS)]
    dropped = sum(not dev_only(r) for r in data)
    data = [r for r in data if dev_only(r)]
    tl_path = os.path.join(CASES, "timelines-" + key + ".json")
    timelines = {}
    if os.path.isfile(tl_path):
        with open(tl_path) as f:
            timelines = {m: t for m, t in (json.load(f).get("timelines") or {}).items() if isinstance(t, dict) and dev_only(t)}
    # Symbol and name: the case row, else its timeline, else token_summary (the day lists carry neither).
    missing = [r["mint"] for r in data if not r.get("symbol") and r.get("mint") not in timelines]
    known = {}
    if missing:
        known = {r["mint"]: r for r in rows(connect(), "SELECT mint, symbol, name FROM token_summary WHERE mint IN "
                                                         "(SELECT unnest(?))", [missing])}
    for r in data:
        src = timelines.get(r.get("mint")) or known.get(r.get("mint")) or {}
        r.setdefault("symbol", src.get("symbol"))
        r.setdefault("name", src.get("name"))
    lists = list(dict.fromkeys(r["list"] for r in data))
    flags = [c for c in cols if types[c] == "BOOLEAN" and c != "grad_in_create"]
    return {"key": key, "lists": lists, "flags": flags, "rows": data, "timelines": timelines, "dropped_holdout": dropped}


# Run keys beyond family/variant/size/d/tau that a tournament run may split its groups by (strategies.GROUP).
RUN_KEYS = ("q", "segment", "regime", "farm", "overlay")


def run_keys(con, table):
    have = {r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()}
    return [k for k in RUN_KEYS if k in have]


NOTES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notes")
STRATEGY_COLS = ["family", "variant", "size_sol", "d", "tau", "scenario", "n", "filled", "mean_ret", "median_ret", "win_rate",
                 "ci_lo", "ci_hi", "total_pnl_sol", "mean_pnl_sol", "n2_mean_ret", "edge_vs_n2", "hist_drift", "fail_rate",
                 "verdict", "pnl_ci_lo", "pnl_ci_hi"]
STRATEGY_ROWS = 50_000


def run_note(run_id):
    """The run's banner note (lab/ui/notes/<run_id>.json): hold-out plan and robust measures, plain text only."""
    p = os.path.join(NOTES, os.path.basename(str(run_id)) + ".json")
    if not os.path.exists(p):
        return None
    with open(p) as f:
        n = json.load(f)
    num = lambda v: float(v) if isinstance(v, (int, float)) else None  # noqa: E731
    cands = [{"id": str(c.get("id", "")), "name": str(c.get("name", "")), "variant": str(c.get("variant", "")),
              "farm": str(c.get("farm", "")), "role": str(c.get("role", "")),
              **{k: num(c.get(k)) for k in ("ev", "ci_lo", "ci_hi", "d1_q09", "d2_q05", "d3_q09")}}
             for c in n.get("candidates") or [] if isinstance(c, dict)]
    result = holdout_result(n.get("holdout_eval"), [c["id"] for c in cands])
    phase = str(n.get("phase") or "")
    if result:
        phase = "Hold-out: niet geslaagd" if result["pick"] is None else f"Hold-out: geslaagd ({result['pick']})"
    return {"holdout": str(n.get("holdout") or ""), "points": [str(x) for x in n.get("points") or []],
            "phase": phase, "candidates": cands, "candidates_source": str(n.get("candidates_source") or ""),
            "holdout_result": result, "owner_decision": str(n.get("owner_decision") or ""),
            "owner_decision_source": str(n.get("owner_decision_source") or ""),
            "holdout_report": str(n.get("holdout_report") or "")}


def holdout_result(rel, ids):
    """The one-time hold-out evaluation (Lab Backtest's holdout_eval.json): per candidate the pre-registered main
    measure on the decision group (B+C) and the decision. Only these aggregates are read, never hold-out data;
    the file must sit under <data>/backtests."""
    if not isinstance(rel, str) or not rel:
        return None
    root = os.path.realpath(os.path.join(DATA, "backtests"))
    path = os.path.realpath(os.path.join(DATA, rel))
    if not path.startswith(root + os.sep) or not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            ev = json.load(f)
        dec = ev.get("decision") or {}
        group = str(dec.get("group") or "B+C")
        per = {}
        for cid in ids:
            m = ((ev.get("candidates") or {}).get(cid) or {}).get("groups", {}).get(group, {}).get("main") or {}
            num = lambda v: float(v) * 100 if isinstance(v, (int, float)) else None  # noqa: E731
            per[cid] = {"point": str(m.get("point") or ""), "mean": num(m.get("mean")), "lo": num(m.get("lo")),
                        "hi": num(m.get("hi")), "filled": m.get("filled") if isinstance(m.get("filled"), int) else None}
    except (OSError, ValueError, AttributeError):
        return None
    return {"source": os.path.relpath(path, DATA), "group": group, "pick": dec.get("pick") if isinstance(dec.get("pick"), str) else None,
            "highest_mean": dec.get("highest_mean_if_none") if isinstance(dec.get("highest_mean_if_none"), str) else None,
            "candidates": per}


def api_strategy_overview(q):
    """Phone overview: the run, its note (pre-registered candidates) and the best variant per family and segment
    (d = 1, q = 0.5, 0.5 SOL, base costs, no overlay, all trades; per slot regime, at least MIN_TRADES)."""
    con = connect()
    if not has_table(con, "strategy_summary"):
        return {"run": None}
    run = rows(con, "SELECT run_id, config_json FROM strategy_runs")[0]
    conf = json.loads(run["config_json"])
    have = {r[0] for r in con.execute("DESCRIBE strategy_summary").fetchall()}
    regime = clamp_str(q.get("regime", [""])[0], {r[0] for r in con.execute("SELECT DISTINCT regime FROM strategy_summary").fetchall()}) \
        if "regime" in have else None
    cond = ["scenario = 'base'", "d = 1", "size_sol = 0.5", "NOT starts_with(family, 'N2_')", f"n >= {MIN_TRADES}", "mean_ret IS NOT NULL"]
    cond += [c for k, c in (("q", "q = 0.5"), ("overlay", "overlay = 'none'"), ("farm", "farm IN ('all', 'n/a')")) if k in have]
    params = []
    if regime:
        cond.append("regime = ?")
        params.append(regime)
    seg = "segment" if "segment" in have else "NULL"
    fams = rows(con, f"""SELECT family, {seg} AS segment, arg_max(variant, mean_ret) AS variant, max(mean_ret) AS mean_ret,
                                arg_max(ci_lo, mean_ret) AS ci_lo, arg_max(ci_hi, mean_ret) AS ci_hi, arg_max(n, mean_ret) AS n
                         FROM strategy_summary WHERE {" AND ".join(cond)} GROUP BY ALL ORDER BY mean_ret DESC""", params)
    regimes = sorted(r[0] for r in con.execute("SELECT DISTINCT regime FROM strategy_summary").fetchall()) if "regime" in have else []
    return {"run": {"run_id": run["run_id"], "positions": conf.get("positions"), "store": conf.get("store", {}),
                    "note": run_note(run["run_id"])},
            "regime": regime, "regimes": regimes, "families": fams}


def clamp_str(v, allowed):
    """A query value from a fixed set, else the set's first value in sort order ('270ms', the current regime)."""
    if v in allowed:
        return v
    return sorted(allowed)[0] if allowed else None


def api_strategies(q):
    con = connect()
    if not has_table(con, "strategy_summary"):
        return {"run": None, "summary": [], "keys": []}
    scenario = q.get("scenario", ["base"])[0]
    gates = "gate_sizes" if "gate_sizes" in {r[0] for r in con.execute("DESCRIBE strategy_runs").fetchall()} else "NULL"
    run = rows(con, f"SELECT run_id, config_json, {gates} AS gate_sizes FROM strategy_runs")[0]
    keys = run_keys(con, "strategy_summary")
    order = ", ".join(["family", "variant", "size_sol", "d", "tau", *keys])
    # The whole grid for one scenario (families-v2: ~4.6k rows), only the columns the tab shows; not MAX_ROWS,
    # which would silently drop the families that sort last.
    have = {r[0] for r in con.execute("DESCRIBE strategy_summary").fetchall()}
    cols = ", ".join(c for c in [*STRATEGY_COLS, *keys] if c in have)
    cur = con.execute(f"SELECT {cols} FROM strategy_summary WHERE scenario = ? ORDER BY {order} LIMIT {STRATEGY_ROWS + 1}",
                      [scenario])
    names = [d[0] for d in cur.description]
    summary = [dict(zip(names, r)) for r in cur.fetchall()]
    if len(summary) > STRATEGY_ROWS:
        raise ValueError(f"run grid larger than {STRATEGY_ROWS} rows per scenario")
    return {"run": {"run_id": run["run_id"], "config": json.loads(run["config_json"]), "gate_sizes": run["gate_sizes"],
                    "note": run_note(run["run_id"])},
            "keys": keys,
            "summary": summary}


def api_paper(q):
    """Paper engine journal and recorder meter (lab/ui/paper.py); read-only, per request."""
    return paper.view()


def api_wallet(q):
    """Wallet page: paper wallet (real, from the journal) and the on-chain wallet file (or the marked sample)."""
    return paper.wallet_view(time.time())


def api_triton(q):
    """Triton page: data use and cost from the recorder's files (or the marked sample)."""
    return {"triton": paper.triton_view(time.time())}


def api_paper_chart(q):
    """One paper position's chart: the bot's entries and exits on that key (journal) and the token's 1-minute
    candles from the development data when it has them; a live pool newer than the store gets them from the
    recorder's segments (paper.live_pool_candles, the last 3 days)."""
    key = (q.get("key", [""])[0] or "").strip()
    if not (32 <= len(key) <= 44 and key.isalnum()):
        raise ValueError("unknown key")
    t = paper.key_trades(key)
    if t is None or not t["trades"]:
        raise LookupError("no paper trades on this key")
    mint = t.get("mint") or key
    out = {**t, "token": None, "candles": [], "events": [], "unit": "mcap"}
    try:
        out.update(api_candles({"mint": [mint]}))
    except LookupError:
        pass
    slots = [x["slot"] for x in t["trades"] if x["slot"]]
    if not out["candles"] and t.get("venue") == "pool" and slots and max(slots) < HOLDOUT_START:
        # A token without pump.fun data (most reversed pools): price candles straight from the store's pool
        # trades, three hours around the bot's trades (slot-filtered, so a zone-map scan, not the whole table).
        lo, hi = max(0, min(slots) - PAPER_CHART_PAD), min(HOLDOUT_START, max(slots) + PAPER_CHART_PAD)
        con = connect(with_dev=True)
        out["candles"] = rows(con, """
            SELECT 'pool' AS venue, epoch(time_bucket(INTERVAL 1 minute, st.ts))::BIGINT AS time,
                   arg_min(price, (p.slot, p.tx_index, p.outer_ix, p.inner_ix)) AS open, max(price) AS high, min(price) AS low,
                   arg_max(price, (p.slot, p.tx_index, p.outer_ix, p.inner_ix)) AS "close", count(*) AS trades,
                   sum(p.quote) / 1e9 AS volume_quote, coalesce(sum(p.quote) FILTER (WHERE p.kind = 'buy'), 0) / 1e9 AS buy_quote
            FROM pool_trades p JOIN slot_time st USING (slot)
            WHERE p.pool = ? AND p.slot >= ? AND p.slot < ? AND p.price > 0 GROUP BY 2 ORDER BY 2""", [key, lo, hi])
        out["unit"] = "price"
    elif not out["candles"] and t.get("venue") == "pool" and slots and min(slots) >= paper.HOLDOUT[1]:
        # A live token, newer than the development store: candles from the recorder's segments (last 3 days).
        out["candles"] = paper.live_pool_candles(key, slots, t.get("orientation") == "reversed")
        if out["candles"]:
            out.update(unit="price", source="recorder")
    return out


def api_paper_explain(q):
    """One paper position explained (paper.explain): key, variant and signal slot t."""
    key = (q.get("key", [""])[0] or "").strip()
    variant = (q.get("variant", [""])[0] or "").strip()
    t = (q.get("t", [""])[0] or "").strip()
    if not (32 <= len(key) <= 44 and key.isalnum()) or not re.fullmatch(r"[A-Za-z0-9_]{1,40}", variant) or not t.isdigit():
        raise ValueError("bad position")
    return paper.explain(key, variant, int(t))


def api_ui_jobs(q):
    """The UI's research jobs and the form's choices (read-only; submitting one is an action: /api/action)."""
    return {"jobs": actions.jobs_view(), "options": actions.job_options(), "in_flight": actions.job_in_flight(),
            "dispatcher": actions.dispatcher_state()}


def api_paper_trades(q):
    """The paper bot's entries and exits on one token, for its token page: the mint itself (curve trades) and
    every pool of that mint in the development store, the paper journal's learned pools and the paper run's store
    (pool trades; a live position's pool is in neither store of the development data)."""
    mint = (q.get("mint", [""])[0] or "").strip()
    if not (32 <= len(mint) <= 44 and mint.isalnum()):
        raise ValueError("unknown mint")
    if not paper.available():
        return {"trades": []}
    con = connect(with_dev=True)
    keys = [mint] + [r[0] for r in con.execute("SELECT pool FROM dev.pools WHERE mint = ?", [mint]).fetchall()]
    keys = list(dict.fromkeys(keys + paper.mint_pools(mint)))
    trades = []
    for k in keys:
        t = paper.key_trades(k)
        trades += (t or {}).get("trades", [])
    return {"trades": sorted(trades, key=lambda x: x["slot"] or 0)}


def api_strategy_curve(q):
    """Equity curves of one family/variant (all sizes) and its N2 shadow (thinned to ~400 points at build time).
    Run keys (segment, farm, ...) narrow it to one group; a key left out of the query is not filtered, and a
    group whose value is 'n/a' (it does not split by that key, like N2_F7 for farm) always matches."""
    con = connect()
    fam = q.get("family", [""])[0]
    params = [fam, "N2_" + fam, q.get("variant", [""])[0], clamp(q.get("d", [1])[0], 0, 100, 1),
              q.get("tau", ["empirical"])[0], q.get("scenario", ["base"])[0]]
    extra = ""
    for k in run_keys(con, "strategy_curves"):
        if k in q:
            extra += f" AND ({k}::VARCHAR IS NOT DISTINCT FROM ? OR {k}::VARCHAR = 'n/a')"
            params.append(q[k][0] or None)
    cur = con.execute(f"""
        SELECT family, size_sol, step, epoch(st.ts)::BIGINT AS time, cum_pnl_sol
        FROM strategy_curves c LEFT JOIN slot_time st USING (slot)
        WHERE family IN (?, ?) AND variant = ? AND d = ? AND tau = ? AND scenario = ?{extra}
        ORDER BY family, size_sol, step""", params)
    out = {}
    for fam_, size, step, t, pnl in cur.fetchall():
        out.setdefault(f"{fam_}|{size}", {"family": fam_, "size_sol": size, "points": []})["points"].append([step, t, pnl])
    return {"curves": list(out.values())}


ROUTES = {"/api/meta": api_meta, "/api/overview": api_overview, "/api/tokens": api_tokens,
          "/api/token": api_token, "/api/ticker": api_ticker, "/api/strategies": api_strategies,
          "/api/strategy_curve": api_strategy_curve, "/api/candles": api_candles, "/api/cases": api_cases,
          "/api/paper": api_paper,
          "/api/strategy_overview": api_strategy_overview, "/api/paper_chart": api_paper_chart, "/api/paper_explain": api_paper_explain,
          "/api/ui_jobs": api_ui_jobs,
          "/api/paper_trades": api_paper_trades,
          "/api/wallet": api_wallet, "/api/triton": api_triton}


class Handler(BaseHTTPRequestHandler):
    server_version = "lab-ui/0"
    sys_version = ""
    static_root = None
    bound = None  # (host, port): actions only from this origin and Host (no DNS rebinding)

    def send(self, status, body, ctype, cache="no-store", extra=()):
        if any(c in str(v) for v in (ctype, cache) for c in "\r\n"):
            raise ValueError("header value with a line break")
        gz = len(body) > 32_768 and ctype.startswith("application/json") and "gzip" in self.headers.get("Accept-Encoding", "")
        if gz:
            body = gzip.compress(body, 5)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        if gz:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        for k, v in extra:
            if any(c in str(v) for c in "\r\n"):
                raise ValueError("header value with a line break")
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, status, obj, extra=()):
        self.send(status, json.dumps(obj, default=to_json, ensure_ascii=False).encode(), "application/json; charset=utf-8",
                  extra=extra)

    def do_HEAD(self):
        self.do_GET()

    def stream(self):
        """Server-Sent Events: 'change' whenever the paper journal or the recorder writes, so open pages refetch at
        once instead of waiting for their poll. Read-only and tiny (a token, no data); a slot limit keeps threads
        bounded and each connection ends after STREAM_SECS (EventSource reconnects by itself)."""
        if not STREAM_SLOTS.acquire(blocking=False):
            return self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "te veel live-verbindingen"})
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            last, t0, ping = None, time.time(), time.time()
            self.wfile.write(b"retry: 5000\n\n")
            while time.time() - t0 < STREAM_SECS:
                tok = paper.change_token()
                if tok != last:
                    self.wfile.write(f"event: change\ndata: {tok}\n\n".encode())
                    self.wfile.flush()
                    last = tok
                elif time.time() - ping > 15:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    ping = time.time()
                time.sleep(2)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        finally:
            STREAM_SLOTS.release()

    # --- the interactive UI's actions (lab/ui/actions.py): login, CSRF, confirmation, audit ---

    def _peer(self):
        return f"{self.client_address[0]}:{self.client_address[1]}"

    def _cookie(self):
        from http.cookies import CookieError, SimpleCookie
        try:
            c = SimpleCookie(self.headers.get("Cookie") or "")
        except CookieError:
            return None
        return c[actions.COOKIE].value if actions.COOKIE in c else None

    def _same_origin(self, origin):
        host, port = self.bound or (None, None)
        return host is not None and origin == f"http://{host}:{port}" and self.headers.get("Host") == f"{host}:{port}"

    def action_get(self, path, query):
        try:
            if path == "/api/action/session":
                s = actions.session(self._peer(), self._cookie())
                why = actions.exposure(self.bound[1])
                return self.send_json(HTTPStatus.OK, {"paired": True, "name": s["name"], "csrf": s["csrf"], "expires_unix": s["expires"],
                                                      "actions_off": why, "actions": actions.DESCRIPTIONS})
            actions.session(self._peer(), self._cookie())
            if path == "/api/action/result":
                return self.send_json(HTTPStatus.OK, actions.result((query.get("id") or [""])[0]))
            if path == "/api/action/log":
                return self.send_json(HTTPStatus.OK, {"events": actions.log()})
            if path == "/api/action/notes":  # the owner's notes: only for a paired device
                g = lambda k: (query.get(k) or [""])[0]  # noqa: E731
                t = g("t")
                return self.send_json(HTTPStatus.OK, {"notes": actions.notes(day=g("day") or None, key=g("key") or None,
                                                                           variant=g("variant") or None, t=int(t) if t.isdigit() else None)})
            return self.send_json(HTTPStatus.NOT_FOUND, {"error": "onbekend pad"})
        except actions.Refused as e:
            return self.send_json(e.status, {"error": e.reason})

    def do_POST(self):
        path = urlparse(self.path).path
        if path.startswith("/api/action/") and not ACTIONS_ON:
            return self.send_json(HTTPStatus.NOT_FOUND, {"error": "onbekend pad"})
        if path not in ("/api/action/pair", "/api/action/prepare", "/api/action/commit"):
            return self.send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "alleen lezen"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if not 0 <= n <= actions.MAX_BODY:
                raise actions.Refused(413, "body too large")
            data = actions.check_post(self.headers, self._same_origin, self.rfile.read(n))
            port = self.bound[1]
            if path == "/api/action/pair":
                sid, csrf, exp = actions.pair(self._peer(), data.get("code", ""))
                cookie = f"{actions.COOKIE}={sid}; HttpOnly; SameSite=Strict; Path=/api/action; Max-Age={actions.SESSION_SECS}"
                return self.send_json(HTTPStatus.OK, {"paired": True, "csrf": csrf, "expires_unix": exp}, extra=[("Set-Cookie", cookie)])
            if path == "/api/action/prepare":
                return self.send_json(HTTPStatus.OK, actions.prepare(self._peer(), self._cookie(), data, port))
            return self.send_json(HTTPStatus.OK, actions.commit(self._peer(), self._cookie(), data, port))
        except actions.Refused as e:
            actions.audit("refused", path=path, status=e.status, reason=e.reason, peer=self.client_address[0])
            return self.send_json(e.status, {"error": e.reason})
        except ValueError:
            return self.send_json(HTTPStatus.BAD_REQUEST, {"error": "bad request"})

    do_PUT = do_DELETE = do_PATCH = lambda self: self.send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "alleen lezen"})

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/stream":
            return self.stream()
        if url.path.startswith("/api/action/"):
            if not ACTIONS_ON:
                return self.send_json(HTTPStatus.NOT_FOUND, {"error": "onbekend pad"})
            return self.action_get(url.path, parse_qs(url.query))
        if url.path.startswith("/api/"):
            fn = ROUTES.get(url.path)
            if fn is None:
                return self.send_json(HTTPStatus.NOT_FOUND, {"error": "onbekend pad"})
            try:
                return self.send_json(HTTPStatus.OK, fn(parse_qs(url.query)))
            except (ValueError, LookupError) as e:
                return self.send_json(HTTPStatus.NOT_FOUND, {"error": str(e)})
            except sqlite3.Error as e:
                self.log_error("sqlite: %s", e)
                return self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "journaal niet beschikbaar"})
            except duckdb.Error as e:
                self.log_error("duckdb: %s", e)
                return self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "database niet beschikbaar"})
        return self.serve_static(url.path)

    def serve_static(self, path):
        root = os.path.realpath(self.static_root)
        rel = os.path.normpath(path.lstrip("/")) if path not in ("", "/") else "index.html"
        full = os.path.realpath(os.path.join(root, rel))
        if not full.startswith(root + os.sep) or not os.path.isfile(full):
            full = os.path.join(root, "index.html")  # SPA fallback
            if not os.path.isfile(full):
                return self.send(HTTPStatus.NOT_FOUND, b"frontend not built", "text/plain; charset=utf-8")
        ctype = STATIC_TYPES.get(os.path.splitext(full)[1].lower(), "application/octet-stream")
        cache = "public, max-age=31536000, immutable" if "/assets/" in full else "no-cache"
        with open(full, "rb") as f:
            self.send(HTTPStatus.OK, f.read(), ctype, cache)



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8501)
    ap.add_argument("--static", default=os.path.join(DATA, "ui-cache", "web"))
    a = ap.parse_args()
    if a.host not in ALLOWED_HOSTS:
        sys.exit(f"refusing to bind {a.host}: only {sorted(ALLOWED_HOSTS)}")
    if a.port in FORBIDDEN_PORTS or a.port < 1024:
        sys.exit(f"refusing port {a.port}")
    Handler.static_root = a.static
    Handler.bound = (a.host, a.port)
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    print(f"lab-ui on http://{a.host}:{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
