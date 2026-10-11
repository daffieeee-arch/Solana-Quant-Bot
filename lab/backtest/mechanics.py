"""Mechanical validation of the replay on a forward epoch (plan W2b step 4: epoch 1052, never the
hold-out), before the one hold-out run. No P&L and no signals: every trade of the period goes
through the venue math, the leg chains and the replay, and the protocol features of the new
protocol are counted together with how they are handled.

  python -m backtest.mechanics <store.duckdb> [--out <dir>] [--positions-per-pool 12]

The store is a separate build of the period (python -m backtest.store validate --out ...); the raw
events come from the complete chunks under LAB_CHUNKS inside the store's slot range. Output:
<out>/mechanics.md (one pass/fail table plus details) and mechanics.json. Exit code 1 if a check fails.

Checks:
- R1-R5 venue math on every raw trade: curve sells, exact-out buys and exact-in buys (v1/v2/v3);
  PumpSwap sells and buys (both orientations), including pools with negative virtual reserves.
  The rules and tolerances are those of tests/test_venues_real.py.
- S1/S2 leg chains in the store: per curve and per pool, the post-state of each leg (from its
  logged amounts) equals the pre-state of the next one (B and E for pools; across fee sweeps E
  must not move).
- S3 non-SOL-quote pools and tokens: recognized, excluded from the SOL markets, counted.
- S4-S6 protocol features: fee sweeps, v3 trades, migrations with and without a pool in the data
  (the replay's synthetic seed pool), negative virtual reserves.
- S7 replay: a deterministic grid of 0.5 SOL positions in every pool and token (d = 1, tp 4%,
  stop 8%, 600-slot time exit) must run without an exception and with sane results.
"""

import argparse
import bisect
import json
import os
import sys
import time
import traceback
from collections import Counter, defaultdict

from . import data, replay, streams
from .costs import Scenario
from .store import load_defaults
from .strategies import SOL_QUOTES, Clock, ms, tp_sl_time, universe_sql
from .venues import (Curve, Pool, curve_buy_exact_in, curve_buy_exact_out, curve_sell, pool_buy_exact_in,
                     pool_buy_exact_out, pool_sell)

MAYHEM_AGENT = "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s"
DEFAULT_KEY = "11111111111111111111111111111111"
# Curve fees: the program sometimes charges no creator fee although creator_fee_basis_points > 0;
# we always charge it for our own orders (conservative). A fee below the program's is a failure.
EXACT_IN_TOLERANCE = {"curve": 0.002, "pool": 0.005}
CHAIN_TOLERANCE = 1e-5  # one-sided vault transfers outside the programs (week-1 report: ~4e-7)
BATCH = 200_000

EXACT_OUT = {"buy", "buy_v2", "buy_v3"}
EXACT_IN = {"buy_exact_sol_in", "buy_exact_quote_in_v2", "buy_exact_quote_in_v3"}
SELLS = {"sell", "sell_v2", "sell_v3"}


class Check:
    def __init__(self, name, rule):
        self.name, self.rule = name, rule
        self.rows = self.failures = 0
        self.ok = None
        self.detail = Counter()
        self.samples = []

    def fail(self, sample=None, n=1):
        self.failures += n
        if sample is not None and len(self.samples) < 5:
            self.samples.append(sample)

    def finish(self, ok):
        self.ok = bool(ok)
        return self

    def row(self):
        return {"check": self.name, "rows": self.rows, "failures": self.failures, "rule": self.rule,
                "result": "PASS" if self.ok else "FAIL", "detail": dict(self.detail), "samples": self.samples}


def _fetch(con, sql):
    cur = con.execute(sql)
    while rows := cur.fetchmany(BATCH):
        yield from rows


# ----------------------------------------------------------------------------- raw venue math


def pre_curve(is_buy, t, q, vt, vq, rt, rq, fb, cb):
    if is_buy:
        return Curve(vt + t, vq - q, rt + t, rq - q, fb, cb)
    return Curve(vt - t, vq + q, rt - t, rq + q, fb, cb)


def curve_checks(con, chunks):
    src = data.parquet("pump/TradeEvent", chunks)
    cols = data.columns("pump/TradeEvent", chunks)
    cash = "COALESCE(cashback, 0)" if "cashback" in cols else "0"
    args = "parent_ix_args" if "parent_ix_args" in cols else "NULL"
    ix_case = " ".join(f"WHEN '{d}' THEN '{n}'" for d, n in data.PUMP_IX.items())
    sells = Check("R1 curve sells: curve quote exact", "0 quote mismatches; 0 fees below the program's")
    outs = Check("R2 curve exact-out buys: quote exact", "0 quote mismatches; 0 fees below the program's")
    ins = Check("R3 curve exact-in buys: tokens and quote", f"mismatches <= {EXACT_IN_TOLERANCE['curve']:.1%}")
    variants = Counter()
    fee_bad = Counter()
    for (variant, user, cashback, is_buy, t, q, fee, cfee, vt, vq, rt, rq, fb, cb, a) in _fetch(con, f"""
            SELECT CASE parent_ix_disc {ix_case} ELSE 'other' END, "user", {cash}, is_buy, token_amount::HUGEINT,
                   COALESCE(quote_amount, sol_amount)::HUGEINT, fee::HUGEINT, creator_fee::HUGEINT,
                   virtual_token_reserves::HUGEINT, COALESCE(virtual_quote_reserves, virtual_sol_reserves)::HUGEINT,
                   real_token_reserves::HUGEINT, COALESCE(real_quote_reserves, real_sol_reserves)::HUGEINT,
                   fee_basis_points::INTEGER, creator_fee_basis_points::INTEGER, {args}
            FROM {src}"""):
        variants[variant] += 1
        if user == MAYHEM_AGENT or cashback:
            variants["excluded: mayhem agent or cashback"] += 1
            continue
        pre = pre_curve(is_buy, int(t), int(q), int(vt), int(vq), int(rt), int(rq), fb, cb)
        if variant in SELLS and not is_buy:
            sells.rows += 1
            sells.detail[variant] += 1
            f = curve_sell(pre, int(t))
            if f.curve_quote != q:
                sells.fail({"variant": variant, "t": str(t), "q": str(q), "ours": str(f.curve_quote)})
            fee_bad["sell over"] += f.fees > fee + cfee
            fee_bad["sell under"] += f.fees < fee + cfee
        elif variant in EXACT_OUT and is_buy:
            outs.rows += 1
            outs.detail[variant] += 1
            f = curve_buy_exact_out(pre, int(t))
            if f.curve_quote != q:
                outs.fail({"variant": variant, "t": str(t), "q": str(q), "ours": str(f.curve_quote)})
            fee_bad["buy over"] += f.fees > fee + cfee
            fee_bad["buy under"] += f.fees < fee + cfee
        elif variant in EXACT_IN and is_buy and a:
            ins.rows += 1
            ins.detail[variant] += 1
            j = json.loads(a)
            budget = j.get("spendable_sol_in", j.get("spendable_quote_in"))
            if budget is None:
                ins.detail["no budget argument"] += 1
                continue
            f = curve_buy_exact_in(pre, int(budget))
            if (f.tokens, f.curve_quote) != (t, q):
                ins.fail({"variant": variant, "budget": str(budget), "t": str(t), "ours": str(f.tokens)})
    sells.detail.update({"fee above the program's (conservative)": fee_bad["sell over"], "fee below": fee_bad["sell under"]})
    outs.detail.update({"fee above the program's (conservative)": fee_bad["buy over"], "fee below": fee_bad["buy under"]})
    sells.finish(sells.rows and sells.failures == 0 and fee_bad["sell under"] == 0)
    outs.finish(outs.rows and outs.failures == 0 and fee_bad["buy under"] == 0)
    ins.finish(ins.rows and ins.failures <= EXACT_IN_TOLERANCE["curve"] * ins.rows)
    return [sells, outs, ins], variants


def pool_checks(con, chunks):
    sells = Check("R4 PumpSwap sells: gross and user quote exact", "0 mismatches")
    buys = Check("R5 PumpSwap buys: exact-out exact, exact-in tokens and quote",
                 f"exact-out 0 mismatches; exact-in <= {EXACT_IN_TOLERANCE['pool']:.1%}")
    neg = Check("S6 negative virtual quote reserves: venue math", "0 mismatches on those trades")
    sp = data.parquet("pump_amm/SellEvent", chunks, missing_ok=True)
    scols = data.columns("pump_amm/SellEvent", chunks) if sp else set()
    if sp:
        cash = "COALESCE(cashback, 0)" if "cashback" in scols else "0"
        for (a, gross, user_out, lpb, pb, cb, b, q, v, cashback, no_creator) in _fetch(con, f"""
                SELECT base_amount_in::HUGEINT, quote_amount_out::HUGEINT, user_quote_amount_out::HUGEINT,
                       lp_fee_basis_points, protocol_fee_basis_points, coin_creator_fee_basis_points,
                       pool_base_token_reserves::HUGEINT, pool_quote_token_reserves::HUGEINT,
                       TRY_CAST(virtual_quote_reserves AS HUGEINT), {cash}, coin_creator = '{DEFAULT_KEY}'
                FROM {sp}"""):
            sells.rows += 1
            v = int(v or 0)
            f = pool_sell(Pool(int(b), int(q) + v, lpb, pb, 0 if no_creator else (cb or 0)), int(a))
            bad = f is None or f.curve_quote != gross or (cashback == 0 and f.trader_quote != user_out)
            if bad:
                sells.fail({"a": str(a), "gross": str(gross), "ours": None if f is None else str(f.curve_quote)})
            if v < 0:
                neg.rows += 1
                neg.fail(n=int(bad))
    bp = data.parquet("pump_amm/BuyEvent", chunks, missing_ok=True)
    exact_in_bad = 0
    if bp:
        boost = data.BOOST_BUY_DISC
        for (disc, b_out, q_in, user_in, max_in, lpb, pb, cb, b, q, v, no_creator) in _fetch(con, f"""
                SELECT parent_ix_disc, base_amount_out::HUGEINT, quote_amount_in::HUGEINT, user_quote_amount_in::HUGEINT,
                       max_quote_amount_in::HUGEINT, lp_fee_basis_points, protocol_fee_basis_points,
                       coin_creator_fee_basis_points, pool_base_token_reserves::HUGEINT, pool_quote_token_reserves::HUGEINT,
                       TRY_CAST(virtual_quote_reserves AS HUGEINT), coin_creator = '{DEFAULT_KEY}'
                FROM {bp} WHERE parent_ix_disc IS DISTINCT FROM '{boost}'"""):
            buys.rows += 1
            v = int(v or 0)
            p = Pool(int(b), int(q) + v, lpb, pb, 0 if no_creator else (cb or 0))
            if disc in data.POOL_EXACT_OUT:
                buys.detail["exact-out"] += 1
                f = pool_buy_exact_out(p, int(b_out))
                bad = f is None or f.curve_quote != q_in
                if bad:
                    buys.fail({"exact": "out", "b_out": str(b_out), "q_in": str(q_in)})
            else:
                buys.detail["exact-in"] += 1
                f = pool_buy_exact_in(p, int(max_in))
                bad = f is None or (f.tokens, f.curve_quote) != (b_out, user_in)
                exact_in_bad += bad
            if v < 0:
                neg.rows += 1
                neg.fail(n=int(bad))
    buys.detail["exact-in mismatches"] = exact_in_bad
    sells.finish(sells.rows and sells.failures == 0)
    buys.finish(buys.rows and buys.failures == 0 and exact_in_bad <= EXACT_IN_TOLERANCE["pool"] * buys.detail["exact-in"])
    neg.finish(neg.failures == 0)
    return [sells, buys, neg]


# ----------------------------------------------------------------------------- store chains


def curve_chain(con, where="TRUE"):
    chk = Check("S1 curve leg chain: post(n) = pre(n+1)", f"mismatch share <= {CHAIN_TOLERANCE:g}")
    prev_mint, prev = None, None
    for r in _fetch(con, f"SELECT mint, {streams.CURVE_COLS} FROM curve WHERE {where} ORDER BY mint, slot, tx_index, outer_ix, inner_ix"):
        mint, leg = r[0], streams.curve_leg(r[1:])
        if mint == prev_mint:
            chk.rows += 1
            if leg.trader == MAYHEM_AGENT or prev.trader == MAYHEM_AGENT:
                chk.detail["skipped: mayhem agent (virtual quote reset)"] += 1
            else:
                a, b = prev.post, leg.pre
                if (a.vt, a.vq, a.rt, a.rq) != (b.vt, b.vq, b.rt, b.rq):
                    chk.fail({"mint": mint, "slot": leg.slot, "prev_slot": prev.slot})
        prev_mint, prev = mint, leg
    return chk.finish(chk.rows and chk.failures <= CHAIN_TOLERANCE * chk.rows)


def pool_chain(con, where="TRUE"):
    chk = Check("S2 pool leg chain: post(n) = pre(n+1) (B; E where both sides log it)",
                f"mismatch share <= {CHAIN_TOLERANCE:g}")
    sweep = Check("S4 fee sweeps: E does not move across a sweep", "0 mismatches on pairs spanning a sweep")
    sweeps = defaultdict(list)
    for pool, slot, tx, o, i in _fetch(con, f"SELECT pool, slot, tx_index, outer_ix, inner_ix FROM pool_sweeps WHERE {where} ORDER BY ALL"):
        sweeps[pool].append((slot, tx, o, i))
    sweep.detail["sweeps"] = sum(len(v) for v in sweeps.values())
    prev_pool, prev, ctx = None, None, {}
    for r in _fetch(con, f"SELECT pool, {streams.POOL_COLS} FROM pool WHERE {where} ORDER BY pool, slot, tx_index, outer_ix, inner_ix"):
        pool = r[0]
        if pool != prev_pool:
            ctx = {}
        leg = streams.pool_leg(r[1:], ctx)
        if pool == prev_pool:
            chk.rows += 1
            e_known = prev.kind in ("buy", "sell") and leg.kind in ("buy", "sell")
            bad = prev.post.b != leg.pre.b or (e_known and prev.post.e != leg.pre.e)
            if leg.kind == "boost" or prev.kind == "boost":
                chk.detail["boost pairs (post-state only)"] += 1
                bad = False
            if bad:
                chk.fail({"pool": pool, "slot": leg.slot, "prev": prev.kind, "kind": leg.kind})
            s = sweeps.get(pool)
            if s and e_known:
                k = bisect.bisect_right(s, prev.order)
                if k < len(s) and s[k] < leg.order:
                    sweep.rows += 1
                    if prev.post.e != leg.pre.e:
                        sweep.fail({"pool": pool, "slot": leg.slot})
        prev_pool, prev = pool, leg
    chk.finish(chk.rows and chk.failures <= CHAIN_TOLERANCE * chk.rows)
    return chk, sweep.finish(sweep.failures == 0)


# ----------------------------------------------------------------------------- classification


def classification(con, meta, cfg):
    chk = Check("S3 non-SOL-quote pools and tokens: recognized, excluded, counted",
                "0 SOL-market rows of other-quote pools; 0 other-quote tokens in the SOL universe")
    classes = dict(con.execute("SELECT quote_class, count(*) FROM pools GROUP BY 1").fetchall())
    chk.detail.update({f"pools {k}": v for k, v in classes.items()})
    leaked = con.execute("""SELECT count(*) FROM pool e JOIN pools p USING (pool)
                            WHERE p.quote_class NOT IN ('sol', 'reversed')""").fetchone()[0]
    quotes = ",".join(f"'{q}'" for q in SOL_QUOTES)
    other_tokens, other_trades = con.execute(f"""
        SELECT count(DISTINCT m.mint), count(c.mint) FROM mints m LEFT JOIN curve c USING (mint)
        WHERE m.quote_mint NOT IN ({quotes})""").fetchone()
    in_universe = con.execute(f"""SELECT count(*) FROM mints m WHERE {universe_sql(cfg, meta['slot_start'], meta['slot_end_exclusive'])}
                                  AND m.quote_mint NOT IN ({quotes})""").fetchone()[0]
    chk.detail.update({"pool rows of other-quote pools": leaked, "other-quote tokens": other_tokens,
                       "their curve trades (kept in the store)": other_trades,
                       "other-quote tokens passing the strategies' universe filter": in_universe})
    chk.rows = sum(classes.values())
    chk.failures = leaked + in_universe
    return chk.finish(leaked == 0 and in_universe == 0)


def features(con, meta, variants):
    chk = Check("S5 protocol features counted (informational)", "counted; handled as noted")
    chk.detail.update({f"curve {k}": v for k, v in sorted(variants.items())})
    chk.detail["curve v3 trades"] = sum(v for k, v in variants.items() if k.endswith("_v3"))
    for t, n in (meta.get("unhandled_event_files") or {}).items():
        chk.detail[f"raw {t} (not replayed: no state change for our fills)"] = n
    mig, mig_pool, mig_known = con.execute("""
        SELECT count(*), count(m.pool), count(p.pool) FILTER (WHERE p.created_in_period)
        FROM mints m LEFT JOIN pools p ON p.pool = m.pool WHERE m.migrate_slot IS NOT NULL""").fetchone()
    chk.detail.update({"migrations": mig, "with a pool address": mig_pool,
                       "pool created in the data (exit at pool open + 1)": mig_known,
                       "without (replay uses the synthetic seed pool)": mig - mig_known})
    neg = con.execute("SELECT count(*) FROM pool WHERE vq < 0").fetchone()[0]
    chk.detail["pool rows with negative virtual quote (E = vault + vq)"] = neg
    chk.rows = sum(variants.values())
    return chk.finish(True)


# ----------------------------------------------------------------------------- replay grid


def replay_grid(con, cfg, per_key, lo, hi, where="TRUE"):
    """Positions at the first trade after every 2-hour step of each pool and token (at most per_key
    each). Every position loads only a window around it (t - 300 to t + 3,000 slots), as the
    tournament does, so busy pools never load their whole history."""
    chk = Check("S7 replay over every pool and token (0.5 SOL, d = 1, tp 4%, stop 8%, T600)",
                "0 exceptions; 0 insane results (negative amounts, exit before entry)")
    clock = Clock.from_store(con)
    scn, tau = Scenario.from_cfg("base", cfg), replay.Tau("empirical")
    size = int(0.5 * 1e9)
    step = clock.slots(2 * 3_600_000, lo)
    before, after = 300, 3_000

    def run(stream, t, sim):
        chk.rows += 1
        try:
            rule = tp_sl_time(0.04, -0.08, clock.slots(ms(600), t), scn)
            r = sim(stream, t, size, 1, 0.5, rule, 0.05, tau)
        except Exception as e:  # noqa: BLE001 - every failure is a finding
            chk.fail({"key": stream.mint, "t": t, "error": f"{type(e).__name__}: {e}",
                      "trace": traceback.format_exc(limit=3)[-400:]})
            return
        chk.detail["skipped" if r.skipped else "entry failed" if r.entry_failed else "filled"] += 1
        chk.detail["vault capped"] += bool(r.vault_capped)
        chk.detail["seed pool (synthetic migration)"] += bool(r.seed_pool)
        chk.detail["graduated while held"] += r.exit_reason == "graduation"
        insane = (r.tokens or 0) < 0 or (r.proceeds or 0) < 0 or (r.cost or 0) < 0 or (
            r.exit_slot is not None and r.entry_slot is not None and r.exit_slot < r.entry_slot)
        if insane:
            chk.fail({"key": stream.mint, "t": t, "result": repr(r)[:300]})

    def grid(table, key, kinds):
        return con.execute(f"""
            WITH ev AS (SELECT {key} AS k, slot FROM {table} WHERE {kinds} AND {where}),
            busy AS (SELECT k FROM ev GROUP BY k HAVING count(*) >= 20),
            g AS (SELECT k, min(slot) AS t FROM ev SEMI JOIN busy USING (k) GROUP BY k, slot // {step})
            SELECT k, t FROM g QUALIFY row_number() OVER (PARTITION BY k ORDER BY t) <= {per_key}
            ORDER BY k, t""").fetchall()

    def rounds(points):
        """The k-th position of every key per round: one load window per key per call."""
        per = defaultdict(list)
        for k, t in points:
            per[k].append(t)
        for i in range(max((len(v) for v in per.values()), default=0)):
            yield [(k, v[i]) for k, v in per.items() if i < len(v)]

    pools = grid("pool", "pool", "kind IN ('buy', 'sell')")
    chk.detail["pools"] = len({k for k, _ in pools})
    for batch in rounds(pools):
        for i in range(0, len(batch), 100):
            part = dict(batch[i:i + 100])
            got = streams.load_pool_streams(con, {k: (max(lo, t - before), t + after) for k, t in part.items()})
            for k, st in got.items():
                run(st, part[k], replay.simulate_pool_position)
    mints = grid("curve", "mint", "TRUE")
    chk.detail["tokens"] = len({k for k, _ in mints})
    for batch in rounds(mints):
        for i in range(0, len(batch), 200):
            part = dict(batch[i:i + 200])
            got = streams.load_streams(con, {k: (max(lo, t - before), t + after) for k, t in part.items()})
            for k, st in got.items():
                if st.curve:
                    run(st, part[k], replay.simulate_curve_position)
    return chk.finish(chk.rows and chk.failures == 0)


# ----------------------------------------------------------------------------- main


def run(store, out, per_key=12, log=print, where="TRUE", forward_only=True, chunks=None):
    """All checks on a store and its raw chunks. Tests pass forward_only=False, a slot filter
    (where) and their own chunks; the forward run uses the whole store and LAB_CHUNKS."""
    t0 = time.time()
    mem = os.environ.get("LAB_DUCKDB_MEM", "1GB")
    con = streams.open_store(store, threads=2, memory=mem)
    meta = json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
    con.close()  # one DuckDB at a time: the raw checks first, then the store
    lo, hi = meta["slot_start"], meta["slot_end_exclusive"]
    if forward_only and lo < 454_464_000:
        raise SystemExit(f"store starts at {lo}: the mechanical check runs on a forward period (>= 454464000), never the hold-out")
    if chunks is None:
        chunks = [c for c in data.complete_chunks() if c[0] >= lo and c[1] <= hi]
        if not chunks or chunks[0][0] != lo or chunks[-1][1] != hi:
            raise SystemExit(f"raw chunks under {data.root()} do not cover [{lo}, {hi})")
    raw = data.connect(threads=2, memory=mem)
    checks = []
    log(f"store {store} [{lo}, {hi}), {len(chunks)} chunks; venue math (curve)")
    c, variants = curve_checks(raw, chunks)
    checks += c
    log(f"venue math (PumpSwap) ({time.time() - t0:.0f}s)")
    checks += pool_checks(raw, chunks)
    raw.close()
    con = streams.open_store(store, threads=2, memory=mem)
    log(f"curve chain ({time.time() - t0:.0f}s)")
    checks.append(curve_chain(con, where))
    log(f"pool chain and sweeps ({time.time() - t0:.0f}s)")
    checks += list(pool_chain(con, where))
    checks.append(classification(con, meta, load_defaults()))
    checks.append(features(con, meta, variants))
    log(f"replay grid ({time.time() - t0:.0f}s)")
    checks.append(replay_grid(con, load_defaults(), per_key, lo, hi, where))
    checks.sort(key=lambda c: c.name)
    rows = [c.row() for c in checks]
    ok = all(c.ok for c in checks)
    os.makedirs(out, exist_ok=True)
    lines = [f"# Mechanical validation: {os.path.basename(store)} (slots {lo}–{hi})", "",
             f"Store built from {meta.get('git_commit')}; {len(chunks)} raw chunks. No P&L, no signals. "
             f"{time.time() - t0:.0f} s. **Overall: {'PASS' if ok else 'FAIL'}**", "",
             "| check | rows | failures | rule | result |", "|---|---|---|---|---|"]
    lines += [f"| {r['check']} | {r['rows']:,} | {r['failures']:,} | {r['rule']} | **{r['result']}** |" for r in rows]
    lines += ["", "## Details", ""]
    for r in rows:
        lines.append(f"- **{r['check']}**: " + ", ".join(f"{k} {v:,}" if isinstance(v, int) else f"{k} {v}"
                                                         for k, v in r["detail"].items()))
        for s in r["samples"]:
            lines.append(f"  - sample: `{json.dumps(s)[:300]}`")
    with open(os.path.join(out, "mechanics.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    with open(os.path.join(out, "mechanics.json"), "w") as f:
        json.dump({"store": store, "slots": [lo, hi], "pass": ok, "checks": rows, "seconds": round(time.time() - t0)},
                  f, indent=1, default=str)
    log(f"{'PASS' if ok else 'FAIL'}: {out}/mechanics.md")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("store")
    ap.add_argument("--out", default=None)
    ap.add_argument("--positions-per-pool", type=int, default=12)
    a = ap.parse_args()
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.store)), "notes",
                                os.path.splitext(os.path.basename(a.store))[0] + "-mechanics")
    sys.exit(0 if run(a.store, out, a.positions_per_pool, log=lambda m: print(time.strftime("%H:%M:%S"), m, flush=True)) else 1)


if __name__ == "__main__":
    main()
