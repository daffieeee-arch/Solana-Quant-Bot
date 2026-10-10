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
import json
import mimetypes
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import duckdb

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "analytics"))
import labstore  # noqa: E402

DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
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
    if has_table(con, "strategy_summary") and con.execute("SELECT count(*) FROM strategy_summary").fetchone()[0]:
        tabs.insert(0, "strategies")
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


def api_strategies(q):
    con = connect()
    if not has_table(con, "strategy_summary"):
        return {"run": None, "summary": []}
    scenario = q.get("scenario", ["base"])[0]
    run = rows(con, "SELECT run_id, config_json FROM strategy_runs")[0]
    return {"run": {"run_id": run["run_id"], "config": json.loads(run["config_json"])},
            "summary": rows(con, "SELECT * FROM strategy_summary WHERE scenario = ? ORDER BY family, variant, size_sol, d, tau",
                            [scenario])}


def api_strategy_curve(q):
    """Equity curves of one family/variant (all sizes) and its N2 shadow (thinned to ~400 points at build time)."""
    con = connect()
    fam = q.get("family", [""])[0]
    params = [fam, "N2_" + fam, q.get("variant", [""])[0], clamp(q.get("d", [1])[0], 0, 100, 1),
              q.get("tau", ["empirical"])[0], q.get("scenario", ["base"])[0]]
    cur = con.execute("""
        SELECT family, size_sol, step, epoch(st.ts)::BIGINT AS time, cum_pnl_sol
        FROM strategy_curves c LEFT JOIN slot_time st USING (slot)
        WHERE family IN (?, ?) AND variant = ? AND d = ? AND tau = ? AND scenario = ?
        ORDER BY family, size_sol, step""", params)
    out = {}
    for fam_, size, step, t, pnl in cur.fetchall():
        out.setdefault(f"{fam_}|{size}", {"family": fam_, "size_sol": size, "points": []})["points"].append([step, t, pnl])
    return {"curves": list(out.values())}


ROUTES = {"/api/meta": api_meta, "/api/overview": api_overview, "/api/tokens": api_tokens,
          "/api/token": api_token, "/api/ticker": api_ticker, "/api/strategies": api_strategies,
          "/api/strategy_curve": api_strategy_curve}


class Handler(BaseHTTPRequestHandler):
    server_version = "lab-ui/0"
    sys_version = ""
    static_root = None

    def send(self, status, body, ctype, cache="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, status, obj):
        self.send(status, json.dumps(obj, default=to_json, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlparse(self.path)
        if url.path.startswith("/api/"):
            fn = ROUTES.get(url.path)
            if fn is None:
                return self.send_json(HTTPStatus.NOT_FOUND, {"error": "onbekend pad"})
            try:
                return self.send_json(HTTPStatus.OK, fn(parse_qs(url.query)))
            except (ValueError, LookupError) as e:
                return self.send_json(HTTPStatus.NOT_FOUND, {"error": str(e)})
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
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        cache = "public, max-age=31536000, immutable" if "/assets/" in full else "no-cache"
        with open(full, "rb") as f:
            self.send(HTTPStatus.OK, f.read(), ctype, cache)

    def do_POST(self):
        self.send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "alleen lezen"})

    do_PUT = do_DELETE = do_PATCH = do_POST


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
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    print(f"lab-ui on http://{a.host}:{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
