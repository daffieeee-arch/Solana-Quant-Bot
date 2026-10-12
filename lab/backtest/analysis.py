"""Checks on F7 in reversed pools (coordinator review of v1.1, 2026-10-10).

  python -m backtest.analysis f7rev <run_dir> [--store dev2.duckdb]  # writes <run_dir>/f7_reversed_checks.md

  c) latency: F7 reversed by d and q (base costs, mean per trade and day-CI from summary.parquet);
  d) concentration: share of P&L from the top-10 pools and top-10 trigger wallets, and the result
     without the top-10 pools;
  e) mechanism: P&L split by whether the trigger seller bought the token back within T slots
     (T of the variant). This looks at the future of the trigger on purpose: it explains results,
     it is never a signal.
"""

import argparse
import json
import os

import duckdb
import numpy as np

from .report import _boot
from .streams import SPILL_DIR, SPILL_MAX

STORE = "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb"


def _con():
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '1GB'; SET threads TO 2; SET temp_directory = '{SPILL_DIR}'; "
                f"SET max_temp_directory_size = '{SPILL_MAX}'")
    return con


def _table(cols, rows):
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join("" if x is None else str(x) for x in r) + " |" for r in rows]
    return out


def f7_reversed(run_dir, store=STORE, segment="pool_reversed"):
    from .costs import Scenario, settle_sql
    from .store import load_defaults
    from .tournament import results_glob

    con = _con()
    pnl, ret = settle_sql(Scenario.from_cfg("base", load_defaults()))
    src = f"read_parquet('{results_glob(run_dir)}')"
    res = f"(SELECT *, {pnl} AS pnl_base_lamports, {ret} AS ret_base FROM {src})"
    # Base rows only: O7 overlay rows repeat each trigger (runs without O7 have no overlay column).
    has_overlay = "overlay" in {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()}
    f7 = (f"(SELECT * FROM {res} WHERE family = 'F7' AND segment = '{segment}' AND NOT skipped"
          + (" AND overlay = 'none')" if has_overlay else ")"))
    lines = [f"# F7 in {segment}: checks", ""]

    lines += ["## c) Latency (base costs)", ""]
    rows = con.execute(f"""
        SELECT size_sol, d, q, count(*) FILTER (WHERE ret_base IS NOT NULL) AS n, round(avg(ret_base) * 100, 2) AS mean_pct,
               round(median(ret_base) * 100, 2) AS median_pct, round(avg(pnl_base_lamports) / 1e9, 4) AS sol_per_trade
        FROM {f7} GROUP BY ALL ORDER BY ALL""").fetchall()
    lines += _table(["size SOL", "d", "q", "trades", "mean %", "median %", "SOL/trade"], rows) + [""]

    lines += ["## d) Concentration (base costs, all F7 variants)", ""]
    rows = []
    for size, d, q in con.execute(f"SELECT DISTINCT size_sol, d, q FROM {f7} ORDER BY ALL").fetchall():
        w = f"{f7} WHERE size_sol = {size} AND d = {d} AND q = {q} AND pnl_base_lamports IS NOT NULL"
        tot, n = con.execute(f"SELECT sum(pnl_base_lamports) / 1e9, count(*) FROM (SELECT * FROM {w})").fetchone()
        top_p = con.execute(f"""SELECT sum(s) FROM (SELECT sum(pnl_base_lamports) / 1e9 AS s FROM (SELECT * FROM {w})
                                GROUP BY pool ORDER BY s DESC LIMIT 10)""").fetchone()[0]
        top_w = con.execute(f"""SELECT sum(s) FROM (SELECT sum(pnl_base_lamports) / 1e9 AS s FROM (SELECT * FROM {w})
                                GROUP BY trigger_trader ORDER BY s DESC LIMIT 10)""").fetchone()[0]
        ex = con.execute(f"""WITH x AS (SELECT * FROM {w}),
                               top AS (SELECT pool FROM x GROUP BY pool ORDER BY sum(pnl_base_lamports) DESC LIMIT 10)
                             SELECT round(avg(ret_base) * 100, 2), count(*) FROM x WHERE pool NOT IN (SELECT pool FROM top)""").fetchone()
        rows.append((size, d, q, n, round(tot, 2), round(top_p, 2), round(top_w, 2), ex[0], ex[1]))
    lines += _table(["size SOL", "d", "q", "trades", "total P&L SOL", "top-10 pools", "top-10 trigger wallets",
                     "mean % without top-10 pools", "trades without"], rows) + [""]

    lines += ["## e) Mechanism: does the trigger seller buy back within T?", ""]
    from .strategies import Clock, ms

    con.execute(f"ATTACH '{store}' AS st (READ_ONLY)")
    clock = Clock(json.loads(con.execute("SELECT json FROM st.meta").fetchone()[0]).get("ms_per_slot"))
    # T of the variant is in v1 slot units (267.3 ms); the window in slots follows each epoch's slot time.
    # (Column names are case-insensitive: the window must not be called "T" next to the trigger slot "t".)
    trig = con.execute(f"""SELECT DISTINCT pool, t, trigger_trader, TRY_CAST(split_part(variant, '_T', 2) AS BIGINT)
                           FROM {f7}""").fetchall()
    con.execute("CREATE TEMP TABLE trig (pool VARCHAR, t BIGINT, trigger_trader VARCHAR, var_t BIGINT, horizon BIGINT)")
    con.executemany("INSERT INTO trig VALUES (?, ?, ?, ?, ?)",
                    [(p, t, w, v, clock.slots(ms(v), t)) for p, t, w, v in trig])
    con.execute("""CREATE TEMP TABLE back AS
        SELECT g.pool, g.t, g.var_t, bool_or(e.slot IS NOT NULL) AS bought_back
        FROM trig g LEFT JOIN st.pool e ON e.pool = g.pool AND e.trader = g.trigger_trader AND e.token_buy
             AND e.slot > g.t AND e.slot <= g.t + g.horizon
        GROUP BY ALL""")
    rows = con.execute(f"""
        SELECT r.size_sol, r.d, r.q, b.bought_back, count(*) AS n, round(avg(r.ret_base) * 100, 2) AS mean_pct,
               round(avg(r.pnl_base_lamports) / 1e9, 4) AS sol_per_trade,
               round(avg(r.trigger_farmer::INT) * 100, 1) AS trigger_farmer_pct
        FROM {f7} r JOIN back b ON b.pool = r.pool AND b.t = r.t AND b.var_t = TRY_CAST(split_part(r.variant, '_T', 2) AS BIGINT)
        WHERE r.ret_base IS NOT NULL GROUP BY r.size_sol, r.d, r.q, b.bought_back ORDER BY ALL""").fetchall()
    lines += _table(["size SOL", "d", "q", "seller bought back", "trades", "mean %", "SOL/trade", "trigger seller farmer %"],
                    rows) + [""]

    lines += ["## a) Farming split (base costs)", ""]
    rows = []
    for size, d, q, farm in con.execute(f"SELECT DISTINCT size_sol, d, q, farm FROM {f7} ORDER BY ALL").fetchall():
        days = con.execute(f"""SELECT day, sum(ret_base), count(ret_base) FROM {f7}
                               WHERE size_sol = {size} AND d = {d} AND q = {q} AND farm = '{farm}' AND ret_base IS NOT NULL
                               GROUP BY day""").fetchall()
        sums = np.array([x[1] for x in days], dtype=float)
        cnts = np.array([x[2] for x in days], dtype=float)
        lo, hi = _boot(sums, cnts)
        rows.append((size, d, q, farm, int(cnts.sum()), round(sums.sum() / cnts.sum() * 100, 2) if cnts.sum() else None,
                     None if lo is None else f"{lo * 100:+.2f} … {hi * 100:+.2f}"))
    lines += _table(["size SOL", "d", "q", "pool farming flag", "trades", "mean %", "95% CI (days)"], rows) + [""]
    path = os.path.join(run_dir, "f7_reversed_checks.md")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path


def _ci_row(con, sql_days):
    """(trades, mean %, 'lo … hi') from per-day (sum ret, count ret) rows (days sorted: a reproducible CI)."""
    days = sorted(con.execute(sql_days).fetchall())
    sums = np.array([x[1] for x in days], dtype=float)
    cnts = np.array([x[2] for x in days], dtype=float)
    if not cnts.sum():
        return 0, None, ""
    lo, hi = _boot(sums, cnts)
    return int(cnts.sum()), round(sums.sum() / cnts.sum() * 100, 2), "" if lo is None else f"{lo * 100:+.2f} … {hi * 100:+.2f}"


def f7_candidate(run_dir, store=STORE, segment="pool_reversed", size=0.5):
    """Coordinator checks on F7 in reversed pools as a candidate (2026-10-11): cost scenarios and the
    break-even tip, latency grid with d in ms per regime, the capital simulation with daily P&L,
    the sandwich flag (V > f x X, prep note 5c) and stability per regime and week. Base rows only."""
    from .costs import Scenario, settle_sql
    from .report import CAPITAL_SOL, MAX_POSITIONS
    from .store import load_defaults
    from .tournament import results_glob

    cfg = load_defaults()
    con = _con()
    src = f"read_parquet('{results_glob(run_dir)}')"
    cols = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()}
    base_f = f"family = 'F7' AND segment = '{segment}' AND NOT skipped" + (" AND overlay = 'none'" if "overlay" in cols else "")
    scn = {n: Scenario.from_cfg(n, cfg) for n in ("optimistic", "base", "pessimistic")}
    exprs = ", ".join(f"{p} AS pnl_{n}, {r} AS ret_{n}" for n, (p, r) in ((n, settle_sql(x)) for n, x in scn.items()))
    keep = "variant, size_sol, d, q, regime, day, d_ms, entry_slot, exit_slot, cost, proceeds, entry_failed, depth, pool, t, farm"
    con.execute(f"CREATE TEMP TABLE f AS SELECT {keep}, {exprs} FROM {src} WHERE {base_f}")
    lines = [f"# F7 in {segment}: candidate checks ({size:g} SOL unless stated)", ""]

    # 1. Cost scenarios and break-even tip.
    lines += ["## 1. Cost scenarios and break-even tip", "",
              "Break-even tip: the tip per transaction at which the mean P&L per trade is 0, from the base scenario "
              "(a filled trade pays the tip twice, a failed entry not at all).", ""]
    rows = []
    for d, q in con.execute(f"SELECT DISTINCT d, q FROM f WHERE size_sol = {size} ORDER BY ALL").fetchall():
        w = f"size_sol = {size} AND d = {d} AND q = {q}"
        out = [d, q]
        for n in scn:
            n_, m, ci = _ci_row(con, f"SELECT day, sum(ret_{n}), count(ret_{n}) FROM f WHERE {w} AND ret_{n} IS NOT NULL GROUP BY day")
            out += [m, ci]
        mean_pnl, filled = con.execute(f"SELECT avg(pnl_base), avg((NOT entry_failed)::INT) FROM f WHERE {w}").fetchone()
        be = scn["base"].tip + mean_pnl / (2 * filled) if filled else None
        out += [n_, None if be is None else f"{be / 1e9:.5f}"]
        rows.append(out)
    lines += _table(["d", "q", "optimistic %", "CI", "base %", "CI", "pessimistic %", "CI", "trades", "break-even tip SOL/tx"], rows) + [""]

    # 2. Latency grid with d in ms per regime.
    lines += ["## 2. Latency: d and q per regime (base costs)", ""]
    rows = []
    for reg, d, q in con.execute(f"SELECT DISTINCT regime, d, q FROM f WHERE size_sol = {size} ORDER BY ALL").fetchall():
        w = f"size_sol = {size} AND regime = '{reg}' AND d = {d} AND q = {q}"
        d_ms = con.execute(f"SELECT round(avg(d_ms)) FROM f WHERE {w}").fetchone()[0]
        n_, m, ci = _ci_row(con, f"SELECT day, sum(ret_base), count(ret_base) FROM f WHERE {w} AND ret_base IS NOT NULL GROUP BY day")
        rows.append((reg, d, d_ms, q, n_, m, ci))
    lines += _table(["regime", "d", "d in ms", "q", "trades", "mean %", "95% CI (days)"], rows) + [""]

    # 3. Capital simulation per variant with daily P&L.
    lines += [f"## 3. Capital simulation: {CAPITAL_SOL:g} SOL, {size:g} SOL per trade, at most {MAX_POSITIONS} positions (base costs, q = 0.5)", ""]
    lines += ["Path matters: a run of total losses (section 6) can empty the account, after which no position fits "
              "(cash below the size) and every later signal is skipped.", ""]
    rows = []
    for pools, pf in (("all", "true"), ("organic", "farm = 'organic'")):
        for var, d in con.execute(f"SELECT DISTINCT variant, d FROM f WHERE size_sol = {size} AND q = 0.5 ORDER BY ALL").fetchall():
            trades = con.execute(f"""SELECT entry_slot, exit_slot, cost, pnl_base, entry_failed, day FROM f
                                     WHERE size_sol = {size} AND q = 0.5 AND variant = '{var}' AND d = {d} AND {pf}
                                     ORDER BY entry_slot, exit_slot""").fetchall()
            rows.append((pools, var, d) + _capital_days(trades, CAPITAL_SOL * 1e9, MAX_POSITIONS))
    lines += _table(["pools", "variant", "d", "signals", "taken", "skipped", "final SOL", "max drawdown %", "days",
                     "positive days", "worst day SOL", "mean day SOL"], rows) + [""]

    # 4. Sandwich flag: V > f x X, f = the pool's fee per side at the trigger, X = SOL depth before the fill.
    lines += ["## 4. Sandwich flag (V > f x X, base costs)", "",
              "f: LP + protocol + creator fee of the pool's trigger-slot events; X: real SOL depth at the trigger.", ""]
    con.execute(f"ATTACH '{store}' AS st (READ_ONLY)")
    con.execute("CREATE TEMP TABLE trig AS SELECT DISTINCT pool, t FROM f")
    con.execute("""CREATE TEMP TABLE fee AS SELECT e.pool, e.slot AS t, max(e.lp_bps + e.protocol_bps + e.creator_bps) / 1e4 AS f
                   FROM st.pool e SEMI JOIN trig g ON e.pool = g.pool AND e.slot = g.t
                   WHERE e.kind IN ('buy', 'sell') GROUP BY ALL""")
    rows = []
    for sz, d in con.execute("SELECT DISTINCT size_sol, d FROM f WHERE q = 0.5 AND size_sol <= 1 ORDER BY ALL").fetchall():
        w = f"r.size_sol = {sz} AND r.d = {d} AND r.q = 0.5"
        flag = f"r.size_sol * 1e9 > fee.f * r.depth"
        share, n_fee = con.execute(f"""SELECT avg(({flag})::INT), count(fee.f) FROM f r LEFT JOIN fee USING (pool, t) WHERE {w}""").fetchone()
        n_, m_all, _ = _ci_row(con, f"SELECT day, sum(ret_base), count(ret_base) FROM f r WHERE {w} AND ret_base IS NOT NULL GROUP BY day")
        n2, m_wo, ci_wo = _ci_row(con, f"""SELECT r.day, sum(r.ret_base), count(r.ret_base) FROM f r LEFT JOIN fee USING (pool, t)
                                          WHERE {w} AND r.ret_base IS NOT NULL AND NOT COALESCE({flag}, false) GROUP BY r.day""")
        rows.append((sz, d, n_, None if share is None else round(share * 100, 1), m_all, n2, m_wo, ci_wo))
    lines += _table(["size SOL", "d", "trades", "flagged %", "mean % all", "trades not flagged", "mean % not flagged",
                     "95% CI"], rows) + [""]

    # 5. Stability per regime and per week.
    lines += ["## 5. Stability (base costs, d = 1 and 2, q = 0.5)", ""]
    rows = []
    for d in (1, 2):
        for key, label in (("regime", "regime"), ("strftime(CAST(day AS DATE), '%G-W%V')", "week")):
            for (k,) in con.execute(f"SELECT DISTINCT {key} FROM f WHERE size_sol = {size} AND d = {d} ORDER BY 1").fetchall():
                w = f"size_sol = {size} AND d = {d} AND q = 0.5 AND {key} = '{k}'"
                n_, m, ci = _ci_row(con, f"SELECT day, sum(ret_base), count(ret_base) FROM f WHERE {w} AND ret_base IS NOT NULL GROUP BY day")
                rows.append((d, label, k, n_, m, ci))
    lines += _table(["d", "by", "value", "trades", "mean %", "95% CI (days)"], rows) + [""]
    # 6. Total losses: the exit returns less than 10% of the entry (the pool's SOL side drained).
    lines += ["## 6. Total losses (exit proceeds below 10% of the entry cost)", ""]
    rows = con.execute(f"""SELECT d, farm, count(*) AS trades, round(avg((NOT entry_failed AND proceeds < 0.1 * cost)::INT) * 100, 2),
                                  round(sum(pnl_base) FILTER (WHERE NOT entry_failed AND proceeds < 0.1 * cost) / 1e9, 1),
                                  round(sum(pnl_base) / 1e9, 1)
                           FROM f WHERE size_sol = {size} AND q = 0.5 GROUP BY ALL ORDER BY ALL""").fetchall()
    lines += _table(["d", "farm", "trades", "total-loss %", "their P&L SOL", "all P&L SOL"], rows) + [""]
    rows = con.execute(f"""SELECT day, count(*) FILTER (WHERE NOT entry_failed AND proceeds < 0.1 * cost) AS n,
                                  count(DISTINCT pool) FILTER (WHERE NOT entry_failed AND proceeds < 0.1 * cost) AS pools
                           FROM f WHERE size_sol = {size} AND q = 0.5 AND d = 1 GROUP BY day ORDER BY n DESC LIMIT 5""").fetchall()
    lines += ["Days with the most total losses (d = 1, all variants):", ""] + _table(["day", "total losses", "pools"], rows) + [""]
    # 7. The candidate's limits (coordinator, 2026-10-11).
    lines += ["## 7. Candidate capital simulation: organic pools at t, 1 open position per pool, daily loss limit 1 SOL, "
              "24 h pool cooldown after a total loss", "",
              f"10 SOL, {size:g} SOL per trade, at most {MAX_POSITIONS} positions, base costs. Skips: pool busy, pool cooling down, "
              "daily limit reached, positions full or cash short.", ""]
    rows = []
    for var, d, q in con.execute(f"SELECT DISTINCT variant, d, q FROM f WHERE size_sol = {size} ORDER BY ALL").fetchall():
        trades = con.execute(f"""SELECT entry_slot, exit_slot, cost, pnl_base, entry_failed, day, pool, proceeds, d_ms / d
                                 FROM f WHERE size_sol = {size} AND variant = '{var}' AND d = {d} AND q = {q} AND farm = 'organic'
                                 ORDER BY entry_slot, exit_slot""").fetchall()
        rows.append((var, d, q) + _capital_limited(trades, CAPITAL_SOL * 1e9, MAX_POSITIONS, 1e9, 86_400_000))
    lines += _table(["variant", "d", "q", "signals", "taken", "skip pool busy", "skip cooldown", "skip daily limit", "skip full/cash",
                     "final SOL", "max drawdown %", "positive days", "days", "worst day SOL", "mean day SOL"], rows) + [""]
    path = os.path.join(run_dir, "f7_reversed_candidate.md")
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def f7_competition(run_dir, store=STORE, segment="pool_reversed", break_even_tip=None):
    """Who else buys right after an F7 trigger, and what they pay: priority fee and Jito tip of the
    token buys in the same pool in slots t+1 and t+2 (p50, p90, p99), and how concentrated the first
    buyers are (wallets and fee payers). Uses only recorded transactions after t: an explanation of
    the competition for landing at d = 1 or 2, not a signal."""
    from .tournament import results_glob

    con = _con()
    src = f"read_parquet('{results_glob(run_dir)}')"
    cols = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()}
    flt = f"family = 'F7' AND segment = '{segment}' AND NOT skipped" + (" AND overlay = 'none'" if "overlay" in cols else "")
    con.execute(f"CREATE TEMP TABLE trig AS SELECT DISTINCT pool, t, farm FROM {src} WHERE {flt}")
    con.execute(f"ATTACH '{store}' AS st (READ_ONLY)")
    con.execute("""CREATE TEMP TABLE nb AS
        SELECT g.pool, g.t, g.farm, e.slot - g.t AS dt, e.tx_index, e.trader, COALESCE(e.fee_payer, e.trader) AS payer,
               e.priority_fee / 1e9 AS prio, e.jito_tip / 1e9 AS tip, (e.priority_fee + e.jito_tip) / 1e9 AS paid
        FROM trig g JOIN st.pool e ON e.pool = g.pool AND e.slot BETWEEN g.t + 1 AND g.t + 2
        WHERE e.kind IN ('buy', 'sell') AND e.token_buy""")
    n_trig = con.execute("SELECT count(*) FROM trig").fetchone()[0]
    lines = [f"# F7 in {segment}: competition after the trigger", "",
             f"{n_trig} triggers. Token buys by others in the same pool in slot t+1 and t+2, priority fee plus Jito tip "
             "per transaction in SOL" + (f"; break-even tip of the strategy at d = 1: {break_even_tip} SOL" if break_even_tip else "") + ".", ""]
    rows = con.execute("""SELECT dt, count(DISTINCT (pool, t)) AS triggers_with_buys, count(*) AS buys,
                                 round(quantile_cont(paid, 0.5), 6), round(quantile_cont(paid, 0.9), 6), round(quantile_cont(paid, 0.99), 6),
                                 round(avg((tip > 0)::INT) * 100, 1), round(quantile_cont(tip, 0.5) FILTER (WHERE tip > 0), 6)
                          FROM nb GROUP BY dt ORDER BY dt""").fetchall()
    lines += _table(["slot", "triggers with a buy", "buys", "paid p50", "paid p90", "paid p99", "with Jito tip %", "tip p50 (if tipped)"],
                    rows) + [""]
    if break_even_tip:
        rows = con.execute(f"""SELECT dt, round(avg((paid > {break_even_tip})::INT) * 100, 1) FROM nb GROUP BY dt ORDER BY dt""").fetchall()
        lines += ["Share of those buys that paid more than the break-even tip:", ""] + _table(["slot", "% above"], rows) + [""]
    # First buyer per trigger (earliest token buy in t+1..t+2): concentration.
    con.execute("""CREATE TEMP TABLE first AS SELECT * FROM nb QUALIFY row_number() OVER (PARTITION BY pool, t ORDER BY dt, tx_index) = 1""")
    for who in ("trader", "payer"):
        tot, distinct, top10, top1 = con.execute(f"""
            WITH c AS (SELECT {who} AS w, count(*) AS n FROM first GROUP BY 1)
            SELECT sum(n), count(*), (SELECT sum(n) FROM (SELECT n FROM c ORDER BY n DESC LIMIT 10)), max(n) FROM c""").fetchone()
        lines.append(f"- First buyer by {who}: {tot} triggers, {distinct} distinct; top 10 take {top10 / tot * 100:.1f}%, "
                     f"the largest {top1 / tot * 100:.1f}%.")
    rows = con.execute("""SELECT left(trader, 8) || '…', count(*) AS n, round(quantile_cont(paid, 0.5), 6), round(avg((tip > 0)::INT) * 100, 0),
                                 round(avg((dt = 1)::INT) * 100, 0)
                          FROM first GROUP BY trader ORDER BY n DESC LIMIT 10""").fetchall()
    lines += ["", "Top first buyers:", ""] + _table(["wallet", "first at triggers", "paid p50", "tipped %", "in t+1 %"], rows) + [""]
    path = os.path.join(run_dir, "f7_reversed_competition.md")
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def _capital_limited(trades, start, max_positions, daily_loss, cooldown_ms):
    """The candidate's capital simulation: at most one open position per pool, no new entries on a
    UTC day once its realized P&L reaches -daily_loss, and no entry in a pool for cooldown_ms after
    a total loss there (exit proceeds below 10% of the entry cost). trades: (entry_slot, exit_slot,
    cost, pnl, failed, day, pool, proceeds, ms_per_slot), in lamports."""
    import heapq
    from collections import defaultdict

    cash, open_, locked, peak, mdd = start, [], 0, start, 0.0
    taken = skipped = skip_pool = skip_day = skip_cool = 0
    day_pnl = defaultdict(float)
    open_pools, cool_until = defaultdict(int), {}

    def settle_until(slot):
        nonlocal cash, locked, peak, mdd
        while open_ and open_[0][0] <= slot:
            exit_slot, cost, back, pool, day, pnl, rug_until = heapq.heappop(open_)
            cash, locked = cash + back, locked - cost
            open_pools[pool] -= 1
            day_pnl[day] += pnl
            if rug_until:
                cool_until[pool] = max(cool_until.get(pool, 0), rug_until)
            peak = max(peak, cash + locked)
            mdd = max(mdd, 1 - (cash + locked) / peak)

    days = set()
    for entry, exit_, cost, pnl, failed, day, pool, proceeds, mps in trades:
        days.add(day)
        settle_until(entry)
        cost, pnl = cost or 0, pnl or 0
        if day_pnl[day] <= -daily_loss:
            skip_day += 1
            continue
        if cool_until.get(pool, -1) > entry:
            skip_cool += 1
            continue
        if open_pools[pool] >= 1:
            skip_pool += 1
            continue
        if len(open_) >= max_positions or (not failed and cash < cost):
            skipped += 1
            continue
        taken += 1
        if failed:
            cash += pnl
            day_pnl[day] += pnl
            continue
        x = exit_ or entry
        rug = (proceeds or 0) < 0.1 * cost
        cash, locked = cash - cost, locked + cost
        open_pools[pool] += 1
        heapq.heappush(open_, (x, cost, cost + pnl, pool, day, pnl, x + round(cooldown_ms / mps) if rug else 0))
    settle_until(float("inf"))
    vals = [day_pnl.get(d, 0.0) / 1e9 for d in sorted(days)]
    return (len(trades), taken, skip_pool, skip_cool, skip_day, skipped, round(cash / 1e9, 3), round(mdd * 100, 1),
            sum(v > 0 for v in vals), len(vals), round(min(vals), 3) if vals else None, round(sum(vals) / len(vals), 3) if vals else None)


def _capital_days(trades, start, max_positions):
    """capital_sim with the P&L of taken trades per trigger day: (signals, taken, skipped, final SOL,
    max drawdown %, days, positive days, worst day SOL, mean day SOL)."""
    import heapq
    from collections import defaultdict

    cash, open_, locked, peak, mdd, taken, skipped = start, [], 0, start, 0.0, 0, 0
    per_day = defaultdict(float)

    def settle_until(slot):
        nonlocal cash, locked, peak, mdd
        while open_ and open_[0][0] <= slot:
            _, cost, back = heapq.heappop(open_)
            cash, locked = cash + back, locked - cost
            peak = max(peak, cash + locked)
            mdd = max(mdd, 1 - (cash + locked) / peak)

    days = set()
    for entry, exit_, cost, pnl, failed, day in trades:
        days.add(day)
        settle_until(entry)
        cost, pnl = cost or 0, pnl or 0
        if len(open_) >= max_positions or (not failed and cash < cost):
            skipped += 1
            continue
        taken += 1
        per_day[day] += pnl / 1e9
        if failed:
            cash += pnl
            continue
        cash, locked = cash - cost, locked + cost
        heapq.heappush(open_, (exit_ or entry, cost, cost + pnl))
    settle_until(float("inf"))
    vals = [per_day.get(d, 0.0) for d in sorted(days)]
    return (len(trades), taken, skipped, round(cash / 1e9, 3), round(mdd * 100, 1), len(vals), sum(v > 0 for v in vals),
            round(min(vals), 3) if vals else None, round(sum(vals) / len(vals), 3) if vals else None)


def _ci_num(con, sql_days):
    """(trades, mean, lo, hi) as numbers from per-day (sum ret, count ret) rows; lo/hi None with < 2 days.
    Days are sorted, so the seeded bootstrap gives the same CI on every run."""
    days = sorted(con.execute(sql_days).fetchall())
    sums = np.array([x[1] for x in days], dtype=float)
    cnts = np.array([x[2] for x in days], dtype=float)
    if not cnts.sum():
        return 0, None, None, None
    lo, hi = _boot(sums, cnts)
    return int(cnts.sum()), float(sums.sum() / cnts.sum()), lo, hi


def _pct(x):
    return "" if x is None else f"{x * 100:+.2f}%"


def holdout(run_dir, config, store=STORE):
    """The pre-registered hold-out evaluation. config: the frozen candidates file (JSON) with the
    parts (slot ranges of the trigger), the combined groups, the candidates (family, segment,
    variant, farm, size, overlay, role), the reference family, the main measure's (d, q) points and
    the risk rules. Per candidate and part: the main measure (of its points, the one with the lower
    mean return per filled trade after base costs, with its 95% day-bootstrap CI), every other
    point, pessimistic costs, the farm-matched reference (N2: entries matched to the candidate's
    triggers) and the difference, trades, win rate, total losses, top-10 pool concentration,
    window_end closes and the capital simulation with the risk rules. Then the decision rule.
    Writes <run_dir>/holdout_eval.md and .json."""
    from .costs import Scenario, settle_sql
    from .store import load_defaults
    from .tournament import results_glob

    with open(config) as f:
        c = json.load(f)
    cfg = load_defaults()
    con = _con()
    src = f"read_parquet('{results_glob(run_dir)}')"
    pnl, ret = settle_sql(Scenario.from_cfg("base", cfg))
    _, ret_p = settle_sql(Scenario.from_cfg("pessimistic", cfg))
    parts = c["parts"]
    part = "CASE " + " ".join(f"WHEN t >= {lo} AND t < {hi} THEN '{k}'" for k, (lo, hi) in parts.items()) + " END"
    groups = {k: [k] for k in parts} | c.get("combined", {})
    ref = c["reference"]
    segs = sorted({k["segment"] for k in c["candidates"]})
    sizes = sorted({k["size_sol"] for k in c["candidates"]})
    fams = sorted({k["family"] for k in c["candidates"]} | {ref["family"]})
    lit = lambda xs: ",".join(f"'{x}'" if isinstance(x, str) else repr(x) for x in xs)  # noqa: E731
    variants = sorted({k["variant"] for k in c["candidates"]})
    con.execute(f"""CREATE TEMP TABLE x AS
        SELECT family, variant, segment, farm, size_sol, d, q, day, t, mint, pool, ref_mint, ref_t, entry_slot, exit_slot,
               cost, proceeds, entry_failed, exit_reason, d_ms, {pnl} AS pnl, {ret} AS ret, {ret_p} AS ret_p, {part} AS part
        FROM {src} WHERE NOT skipped AND overlay = 'none' AND family IN ({lit(fams)}) AND segment IN ({lit(segs)})
          AND size_sol IN ({lit(sizes)}) AND variant IN ({lit(variants)})""")
    # The reference rows carry the farm of the trigger they were matched to (an N2 row's ref is the
    # trigger's signal key: the pool for pool signals, the mint on the curve).
    cand_fams = sorted({k["family"] for k in c["candidates"]})
    con.execute(f"""CREATE TEMP TABLE h AS
        SELECT x.*, g.farm AS ref_farm FROM x
        LEFT JOIN (SELECT DISTINCT COALESCE(pool, mint) AS key, t, farm FROM x WHERE family IN ({lit(cand_fams)})) g
          ON x.family = '{ref["family"]}' AND x.ref_mint = g.key AND x.ref_t = g.t""")
    con.execute("DROP TABLE x")
    points = [tuple(p) for p in c["main_measure"]["points"]] + [tuple(p) for p in c.get("also_points", [])]
    rr = c["risk_rules"]
    out = {"config": os.path.basename(config), "run_dir": run_dir, "candidates": {}}
    for k in c["candidates"]:
        sel = (f"family = '{k['family']}' AND segment = '{k['segment']}' AND variant = '{k['variant']}' "
               f"AND size_sol = {k['size_sol']}" + ("" if k["farm"] == "all" else f" AND farm = '{k['farm']}'"))
        # The reference: N2 entries matched to this candidate's triggers (same variant's exit rule).
        rsel = (f"family = '{ref['family']}' AND segment = '{k['segment']}' AND variant = '{k['variant']}' "
                f"AND size_sol = {k['size_sol']}" + ("" if k["farm"] == "all" else f" AND ref_farm = '{k['farm']}'"))
        res = {}
        for g, ps in groups.items():
            w = f"part IN ({lit(ps)})"
            pts = {}
            for d, q in points:
                pw = f"{w} AND d = {d} AND q = {q}"
                n, m, lo, hi = _ci_num(con, f"SELECT day, sum(ret), count(ret) FROM h WHERE {sel} AND {pw} AND ret IS NOT NULL GROUP BY day")
                _, mp, lop, hip = _ci_num(con, f"SELECT day, sum(ret_p), count(ret_p) FROM h WHERE {sel} AND {pw} AND ret_p IS NOT NULL GROUP BY day")
                rn, rm, _, _ = _ci_num(con, f"SELECT day, sum(ret), count(ret) FROM h WHERE {rsel} AND {pw} AND ret IS NOT NULL GROUP BY day")
                extra = con.execute(f"""SELECT count(*), avg(pnl) / 1e9, avg((ret > 0)::INT),
                                               count(*) FILTER (WHERE NOT entry_failed AND proceeds < 0.1 * cost),
                                               count(*) FILTER (WHERE exit_reason = 'window_end'), sum(pnl) / 1e9
                                        FROM h WHERE {sel} AND {pw}""").fetchone()
                top = con.execute(f"""SELECT sum(p) FROM (SELECT sum(pnl) / 1e9 AS p FROM h WHERE {sel} AND {pw}
                                      GROUP BY pool ORDER BY p DESC LIMIT 10)""").fetchone()[0]
                trades = con.execute(f"""SELECT entry_slot, exit_slot, cost, pnl, entry_failed, day, pool, proceeds, d_ms / d
                                         FROM h WHERE {sel} AND {pw} ORDER BY entry_slot, exit_slot""").fetchall()
                sim = _capital_limited(trades, rr["capital_sol"] * 1e9, rr["max_positions"], rr["daily_loss_sol"] * 1e9,
                                       rr["pool_cooldown_h"] * 3_600_000) if trades else None
                pts[f"d{d}_q{q}"] = {
                    "d": d, "q": q, "filled": n, "mean": m, "lo": lo, "hi": hi, "pess_mean": mp, "pess_lo": lop, "pess_hi": hip,
                    "ref_filled": rn, "ref_mean": rm, "vs_ref": None if m is None or rm is None else m - rm,
                    "positions": extra[0], "sol_per_trade": extra[1], "win_rate": extra[2], "total_losses": extra[3],
                    "window_end": extra[4], "total_pnl_sol": extra[5], "top10_pools_pnl_sol": top,
                    "capital": None if sim is None else dict(zip(
                        ("signals", "taken", "skip_pool", "skip_cooldown", "skip_daily_limit", "skip_full_cash", "final_sol",
                         "max_drawdown_pct", "positive_days", "days", "worst_day_sol", "mean_day_sol"), sim)),
                }
            main = [pts[f"d{d}_q{q}"] for d, q in (tuple(p) for p in c["main_measure"]["points"])]
            worst = min(main, key=lambda x: float("inf") if x["mean"] is None else x["mean"])
            res[g] = {"main": {"point": f"d{worst['d']}_q{worst['q']}", "mean": worst["mean"], "lo": worst["lo"],
                               "hi": worst["hi"], "filled": worst["filled"]}, "points": pts}
        out["candidates"][k["id"]] = {"spec": k, "groups": res}
    out["decision"] = _holdout_decision(out, c)
    with open(os.path.join(run_dir, "holdout_eval.json"), "w") as f:
        json.dump(out, f, indent=1, default=str)
    path = os.path.join(run_dir, "holdout_eval.md")
    with open(path, "w") as f:
        f.write(_holdout_md(out, c))
    return path


def _holdout_decision(out, c):
    """Pre-registration §4 on the decision group (B+C): the eligible candidate (role main or
    variant) with the highest CI lower bound and a mean above 0; the control only when its lower
    bound is above 0 and above theirs; a candidate positive in part A but not in the decision group
    drops out."""
    g = c["decision_group"]
    m = {k: v["groups"][g]["main"] for k, v in out["candidates"].items()}
    roles = {k["id"]: k["role"] for k in c["candidates"]}
    lo = lambda k: float("-inf") if m[k]["lo"] is None else m[k]["lo"]  # noqa: E731
    eligible = [k for k in m if roles[k] in ("main", "variant") and m[k]["mean"] is not None and m[k]["mean"] > 0]
    pick = max(eligible, key=lo) if eligible else None
    for k in m:
        if roles[k] == "control" and m[k]["lo"] is not None and m[k]["lo"] > 0 and all(lo(k) > lo(e) for e in eligible):
            pick = k
    dropped = [k for k, v in out["candidates"].items() if "A" in v["groups"]
               and (v["groups"]["A"]["main"]["mean"] or 0) > 0 and not (m[k]["mean"] or 0) > 0]
    best_mean = max(m, key=lambda k: float("-inf") if m[k]["mean"] is None else m[k]["mean"])
    return {"group": g, "pick": pick, "dropped_after_part_A": dropped,
            "no_positive_candidate": pick is None, "highest_mean_if_none": None if pick else best_mean}


def _holdout_md(out, c):
    lines = [f"# Hold-out evaluation ({out['config']})", "",
             f"Run: `{out['run_dir']}`. Main measure: of the points {c['main_measure']['points']} (d, q) the one with the "
             "lower mean return per filled trade after base costs; 95% CI by day bootstrap (parts of 1–4 days give wide "
             "or no CIs). Parts by trigger slot: " + ", ".join(f"{k} [{a}, {b})" for k, (a, b) in c["parts"].items()) + ".", ""]
    for kid, v in out["candidates"].items():
        k = v["spec"]
        lines += [f"## {kid}: {k['family']} {k['variant']}, {k['segment']}, {k['farm']} pools, {k['size_sol']} SOL ({k['role']})", "",
                  "| part | main point | mean | 95% CI | filled |", "|---|---|---|---|---|"]
        for g, r in v["groups"].items():
            mn = r["main"]
            ci = "" if mn["lo"] is None else f"{_pct(mn['lo'])} … {_pct(mn['hi'])}"
            lines.append(f"| {g} | {mn['point']} | {_pct(mn['mean'])} | {ci} | {mn['filled']} |")
        lines += ["", "| part | point | mean | 95% CI | pessimistic | reference | vs reference | SOL/trade | win | total losses | "
                  "window_end | top-10 pools SOL / all SOL | capital: final SOL, max DD %, positive days |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for g, r in v["groups"].items():
            for name, p in r["points"].items():
                ci = "" if p["lo"] is None else f"{_pct(p['lo'])} … {_pct(p['hi'])}"
                cap = p["capital"]
                capt = "" if not cap else f"{cap['final_sol']}, {cap['max_drawdown_pct']}, {cap['positive_days']}/{cap['days']}"
                top = "" if p["top10_pools_pnl_sol"] is None else f"{p['top10_pools_pnl_sol']:.2f} / {p['total_pnl_sol']:.2f}"
                spt = "" if p["sol_per_trade"] is None else f"{p['sol_per_trade']:+.4f}"
                win = "" if p["win_rate"] is None else f"{p['win_rate'] * 100:.0f}%"
                lines.append(f"| {g} | {name} | {_pct(p['mean'])} | {ci} | {_pct(p['pess_mean'])} | {_pct(p['ref_mean'])} | "
                             f"{_pct(p['vs_ref'])} | {spt} | {win} | {p['total_losses']} | {p['window_end']} | {top} | {capt} |")
        lines.append("")
    d = out["decision"]
    lines += ["## Decision (pre-registration §4)", "",
              f"- Group: {d['group']}. Pick: **{d['pick'] or 'none'}**.",
              f"- Positive in part A but not in {d['group']} (drop out): {', '.join(d['dropped_after_part_A']) or 'none'}."]
    if d["no_positive_candidate"]:
        lines.append(f"- No candidate has a mean above 0: the stop-rule clock starts with {d['highest_mean_if_none']} marked "
                     "\"not proven\" (N2 in the shadow); the owner decides on ADJUST or STOP.")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["f7rev", "f7cand", "f7comp", "holdout"])
    ap.add_argument("run_dir")
    ap.add_argument("--store", default=STORE)
    ap.add_argument("--segment", default="pool_reversed")
    ap.add_argument("--config", help="holdout: the frozen candidates file")
    a = ap.parse_args()
    if a.what == "holdout":
        if not a.config:
            raise SystemExit("holdout needs --config (the frozen candidates file)")
        print(holdout(a.run_dir, a.config, a.store))
        return
    fn = {"f7rev": f7_reversed, "f7cand": f7_candidate, "f7comp": f7_competition}[a.what]
    print(fn(a.run_dir, a.store, a.segment))


if __name__ == "__main__":
    main()
