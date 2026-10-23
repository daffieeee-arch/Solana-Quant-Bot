"""x10-jager stage A (research/x10-jager-plan-2026-10-23.md sections 1, 2, 5 and the F1/F2/F5 columns of section 3;
roadmap #167): per coin created in one slot window, the entries at four moments, the net mark path up to 72 h
across the migration, the x2 / x5 / x10 labels with a -50 % stop, the delayed exit grid and the point-in-time
features of the entry. Exploratory on dev (1033-1046); the window never reaches the hold-out (runners.run checks).

Everything that touches the store reuses wallets.runners (tokens, curve and pool legs with pre- and post-states,
the fill of the entry, the marks): the same venue arithmetic the engine uses, tested there against the engine.
What is new here:
  moments   e10  the first trade >= 10 slots after the create (the coordinator's base-rate entry)
            c30  the first trade whose block time is >= create time + 30 s
            c2   the same at + 2 min
            mig  the first buy / sell of the mint's SOL pool (the migration moment; boost cranks are not trades)
            e10 / c30 / c2 only for non-Mayhem mints with a curve (the Mayhem agent resets the virtual SOL inside its
            own trades, no replay through its legs: runners.setup keeps their curve legs out); mig for every mint.
  entry     the first fillable trade >= t + Clock.slots(600 ms, t) slots, filled at its pre-state (size 0.5 SOL by
            default, LAB_X10_SIZE_SOL for the 2 SOL capacity run), plus the base scenario's network cost.
  marks     the net SOL of a full sell at the state after every later trade up to 72 h (curve, then the pool).
  labels    hit_<m>_<H> for m in 2 / 5 / 10 and H in 24 / 72 h: the first mark >= m x cost comes before the first
            mark <= 0.5 x cost and within H. stop_first_<H>: the stop comes first. peak_<H>, t_peak_<H> (minutes),
            end_<H> (the last mark within H), dd_<H> (the lowest mark before the peak), all as multiples of the cost.
  exits     x_<rule>_<H> as multiples of the cost, the sell at the first mark whose slot is >= the trigger's slot +
            Clock.slots(600 ms) (the entry delay again, section 5): trail30 (the first mark <= 70 % of the highest
            earlier mark), tp2 / tp5 / tp10 (the first mark >= the target), half2 / half5 (half at the target, the
            rest trail30 from there; the whole position trail30 when the target never comes). The -50 % stop closes
            every rule when it comes first; without any trigger the position is marked at the last mark within H.
            A one-step drain triggers on the drained state: the rug is kept.
  features  runners.features (windows all / pre1 / pre5, and post1 / post5 which the model must not use: they lie
            after the entry), plus the first 10 / 30 / 60 s after the create capped at the moment t (buyers, SOL
            in, max buy, share of buys >= 1 SOL, sell / buy SOL, SOL per second), the create-slot buys (count, SOL,
            in the create transaction, within 2 slots: F2), the creator's own holding and sells before t (F4 part)
            and the hour / weekday of the entry (F5 part). The cross-coin families (F3 early-buyer quality, F4 creator
            history, F5 market context) are stage B (features.py) on the per-mint tables written here:
  mint_peaks   per mint of the window: the first trade's price, the highest price within 72 h over curve and pool,
               the slot of that peak and the price multiple (the coordinator's base-rate definition, descriptive);
  early_buyers per mint the wallets that bought within 60 s of the create, with their first buy slot.

  LAB_STORE=<dev2> python -m wallets.x10.labels --from-slot A --to-slot B [--out DIR] [--size-sol 0.5]
Writes labels-<A>.parquet, mint_peaks-<A>.parquet, early_buyers-<A>.parquet and run-labels-<A>.json. NAS job
(nas_x10.sh); a slice of a few thousand slots on the VPS as a smoke test.
"""
import argparse
import json
import os
import sys
import time

from wallets import runners as R
from wallets.common import HOLDOUT_START, OUT_ROOT, SOL, Clock, connect, git_commit
from wallets.markout import ms_case

H24_MS = 24 * 3_600_000
H72_MS = 72 * 3_600_000
TARGETS = (2, 5, 10)
STOP = 0.5
TRAIL = 0.7
EARLY_S = 60
FIRST_WINDOWS_S = (10, 30, 60)
RULES = ("trail30", "tp2", "tp5", "tp10", "half2", "half5")
HORIZONS = ("24", "72")


def _count(con, t):
    return con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]


def setup(con, lo, hi, clock):
    """runners.setup with the mark horizon at 72 h, then the four moments of this study in place of c1 / c5 / c15."""
    R.H6_MS = H72_MS                      # runners.setup and the mom table read the module global at call time
    hi_ext = R.setup(con, lo, hi, clock)  # tok, cl, pl, trades_all, moments (replaced below), mom (rebuilt below)
    con.execute("""CREATE OR REPLACE TEMP TABLE moments AS
        WITH ct AS (SELECT t.key, t.create_slot, b.block_time AS t0 FROM tok t JOIN blocks b ON b.slot = t.create_slot
                    WHERE t.has_curve AND NOT COALESCE(t.mayhem, false)),
             e10 AS (SELECT a.key, 'e10' AS moment, min(a.slot) AS t FROM trades_all a JOIN ct USING (key) WHERE a.slot >= ct.create_slot + 10 GROUP BY 1, 2),
             cs AS (SELECT a.key, m.moment, min(a.slot) AS t
                    FROM trades_all a JOIN ct USING (key) JOIN blocks b ON b.slot = a.slot, (SELECT * FROM (VALUES ('c30', 30), ('c2', 120)) v(moment, secs)) m
                    WHERE b.block_time >= ct.t0 + m.secs GROUP BY 1, 2),
             mig AS (SELECT key, 'mig' AS moment, min(slot) AS t FROM pl WHERE trade GROUP BY 1, 2)
        SELECT * FROM e10 UNION ALL SELECT * FROM cs UNION ALL SELECT * FROM mig""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE mom AS
        SELECT m.key, m.moment, m.t, greatest(1, round({R.ENTRY_MS} / ({ms_case(clock, 'm.t')})))::BIGINT AS d_slots,
               greatest(1, round({R.H1_MS} / ({ms_case(clock, 'm.t')})))::BIGINT AS h1, greatest(1, round({H72_MS} / ({ms_case(clock, 'm.t')})))::BIGINT AS h6,
               greatest(1, round({H24_MS} / ({ms_case(clock, 'm.t')})))::BIGINT AS h24,
               greatest(1, round(60000 / ({ms_case(clock, 'm.t')})))::BIGINT AS m1s, greatest(1, round(300000 / ({ms_case(clock, 'm.t')})))::BIGINT AS m5s
        FROM moments m""")
    R.progress(f"x10 moments {_count(con, 'mom')}: " + str(con.execute("SELECT moment, count(*) FROM mom GROUP BY 1 ORDER BY 1").fetchall()))
    return hi_ext


def labels_and_exits(con):
    """From mk (runners.marks: one net per entry per later trade within 72 h): the labels, then the delayed exits."""
    con.execute("""CREATE OR REPLACE TEMP TABLE mkx AS
        SELECT k.*, m.d_slots, m.h24, k.entry_slot + m.h24 AS cap24, k.entry_slot + m.h6 AS cap72,
               max(k.net) OVER (PARTITION BY k.key, k.moment, k.t ORDER BY k.ord ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS pmax
        FROM mk k JOIN mom m ON m.key = k.key AND m.moment = k.moment AND m.t = k.t""")
    tgt = ", ".join(f"min(ord) FILTER (WHERE net >= {m} * cost) AS ord_{m}, min(slot) FILTER (WHERE net >= {m} * cost) AS slot_{m}" for m in TARGETS)
    per_h = []
    for H in HORIZONS:
        cap = f"cap{H}"
        per_h += [f"max(net) FILTER (WHERE slot <= {cap}) AS peak_net_{H}",
                  f"arg_min(slot, (-net, ord)) FILTER (WHERE slot <= {cap}) AS peak_slot_{H}",
                  f"arg_min(ord, (-net, ord)) FILTER (WHERE slot <= {cap}) AS peak_ord_{H}",
                  f"arg_max(net, ord) FILTER (WHERE slot <= {cap}) AS end_net_{H}",
                  f"arg_max(ord, ord) FILTER (WHERE slot <= {cap}) AS end_ord_{H}", f"max(slot) FILTER (WHERE slot <= {cap}) AS end_slot_{H}",
                  f"count(*) FILTER (WHERE slot <= {cap}) AS n_marks_{H}",
                  f"min(ord) FILTER (WHERE net <= {TRAIL} * pmax AND slot <= {cap}) AS ord_trail_{H}",
                  f"min(slot) FILTER (WHERE net <= {TRAIL} * pmax AND slot <= {cap}) AS slot_trail_{H}"]
    con.execute(f"""CREATE OR REPLACE TEMP TABLE lab AS
        SELECT key, moment, t, any_value(entry_slot) AS entry_slot, any_value(cost) AS cost, any_value(d_slots) AS d_slots,
               any_value(cap24) AS cap24, any_value(cap72) AS cap72,
               min(ord) FILTER (WHERE net <= {STOP} * cost) AS ord_stop, min(slot) FILTER (WHERE net <= {STOP} * cost) AS slot_stop,
               {tgt}, {', '.join(per_h)}
        FROM mkx GROUP BY 1, 2, 3""")
    # the drawdown before the peak and the trail trigger after a half exit (the first trail mark after the target's mark)
    con.execute("""CREATE OR REPLACE TEMP TABLE lab2 AS
        SELECT l.*,
               min(k.net) FILTER (WHERE k.ord <= l.peak_ord_24) AS dd_net_24, min(k.net) FILTER (WHERE k.ord <= l.peak_ord_72) AS dd_net_72,
               min(k.ord) FILTER (WHERE k.ord > l.ord_2 AND k.net <= 0.7 * k.pmax AND k.slot <= l.cap24) AS ord_trail2_24,
               min(k.slot) FILTER (WHERE k.ord > l.ord_2 AND k.net <= 0.7 * k.pmax AND k.slot <= l.cap24) AS slot_trail2_24,
               min(k.ord) FILTER (WHERE k.ord > l.ord_2 AND k.net <= 0.7 * k.pmax AND k.slot <= l.cap72) AS ord_trail2_72,
               min(k.slot) FILTER (WHERE k.ord > l.ord_2 AND k.net <= 0.7 * k.pmax AND k.slot <= l.cap72) AS slot_trail2_72,
               min(k.ord) FILTER (WHERE k.ord > l.ord_5 AND k.net <= 0.7 * k.pmax AND k.slot <= l.cap24) AS ord_trail5_24,
               min(k.slot) FILTER (WHERE k.ord > l.ord_5 AND k.net <= 0.7 * k.pmax AND k.slot <= l.cap24) AS slot_trail5_24,
               min(k.ord) FILTER (WHERE k.ord > l.ord_5 AND k.net <= 0.7 * k.pmax AND k.slot <= l.cap72) AS ord_trail5_72,
               min(k.slot) FILTER (WHERE k.ord > l.ord_5 AND k.net <= 0.7 * k.pmax AND k.slot <= l.cap72) AS slot_trail5_72
        FROM lab l JOIN mkx k ON k.key = l.key AND k.moment = l.moment AND k.t = l.t
        GROUP BY ALL""")
    # triggers per (rule, H): the earliest of the rule's own trigger and the stop, within H; NULL = hold to the end of H
    trig = []
    for H in HORIZONS:
        cap = f"cap{H}"
        own = {"trail30": (f"ord_trail_{H}", f"slot_trail_{H}"),
               "tp2": ("ord_2", "slot_2"), "tp5": ("ord_5", "slot_5"), "tp10": ("ord_10", "slot_10")}
        for rule, (o, s) in own.items():
            trig.append(f"""SELECT key, moment, t, '{rule}' AS rule, '{H}' AS h, 1.0 AS part,
                CASE WHEN {o} IS NOT NULL AND {s} <= {cap} AND (ord_stop IS NULL OR {o} < ord_stop) THEN {s}
                     WHEN ord_stop IS NOT NULL AND slot_stop <= {cap} THEN slot_stop END AS trig_slot FROM lab2""")
        for m in (2, 5):
            # the target part: the target's own trigger (or the stop / the end when the target never comes within H)
            trig.append(f"""SELECT key, moment, t, 'half{m}' AS rule, '{H}' AS h, 0.5 AS part,
                CASE WHEN ord_{m} IS NOT NULL AND slot_{m} <= {cap} AND (ord_stop IS NULL OR ord_{m} < ord_stop) THEN slot_{m}
                     WHEN ord_stop IS NOT NULL AND slot_stop <= {cap} THEN slot_stop END AS trig_slot FROM lab2""")
            # the trailing part: after the target the first trail mark; without the target the whole-position trail
            trig.append(f"""SELECT key, moment, t, 'half{m}' AS rule, '{H}' AS h, 0.5 AS part,
                CASE WHEN ord_{m} IS NOT NULL AND slot_{m} <= {cap} AND (ord_stop IS NULL OR ord_{m} < ord_stop)
                     THEN CASE WHEN ord_trail{m}_{H} IS NOT NULL AND (ord_stop IS NULL OR ord_trail{m}_{H} < ord_stop) THEN slot_trail{m}_{H}
                               WHEN ord_stop IS NOT NULL AND slot_stop <= {cap} THEN slot_stop END
                     WHEN ord_trail_{H} IS NOT NULL AND slot_trail_{H} <= {cap} AND (ord_stop IS NULL OR ord_trail_{H} < ord_stop) THEN slot_trail_{H}
                     WHEN ord_stop IS NOT NULL AND slot_stop <= {cap} THEN slot_stop END AS trig_slot FROM lab2""")
    con.execute("CREATE OR REPLACE TEMP TABLE trig AS " + " UNION ALL ".join(trig))
    # the fill: the first mark at or after trigger slot + d_slots within H; otherwise (no trigger, or nothing after it) the end-of-H mark
    con.execute("""CREATE OR REPLACE TEMP TABLE fills AS
        WITH f AS (
            SELECT tr.key, tr.moment, tr.t, tr.rule, tr.h, tr.part, tr.trig_slot,
                   arg_min(k.net, k.ord) FILTER (WHERE tr.trig_slot IS NOT NULL AND k.slot >= tr.trig_slot + l.d_slots AND k.slot <= CASE tr.h WHEN '24' THEN l.cap24 ELSE l.cap72 END) AS fill_net,
                   any_value(CASE tr.h WHEN '24' THEN l.end_net_24 ELSE l.end_net_72 END) AS end_net
            FROM trig tr JOIN lab2 l USING (key, moment, t) JOIN mkx k ON k.key = tr.key AND k.moment = tr.moment AND k.t = tr.t
            GROUP BY ALL)
        SELECT key, moment, t, rule, h, sum(part * COALESCE(fill_net, end_net)) AS net, bool_or(trig_slot IS NOT NULL) AS triggered
        FROM f GROUP BY 1, 2, 3, 4, 5""")
    cols = ", ".join(f"max(net) FILTER (WHERE rule = '{r}' AND h = '{H}') AS x_{r}_{H}, bool_or(triggered) FILTER (WHERE rule = '{r}' AND h = '{H}') AS trig_{r}_{H}"
                     for r in RULES for H in HORIZONS)
    con.execute(f"CREATE OR REPLACE TEMP TABLE ex AS SELECT key, moment, t, {cols} FROM fills GROUP BY 1, 2, 3")
    R.progress(f"labels {_count(con, 'lab2')}, exits {_count(con, 'ex')}")


def extra_features(con, clock):
    """F1 first-N-seconds windows (capped at the moment t), F2 create-slot buys, the creator's own legs before t, F5 hour / weekday."""
    fw = []
    for n in FIRST_WINDOWS_S:
        c = f"b.block_time < ct.t0 + {n} AND l.slot <= m.t"
        fw += [f"count(DISTINCT l.trader) FILTER (WHERE {c} AND l.is_buy) AS f{n}_buyers",
               f"sum(l.sol) FILTER (WHERE {c} AND l.is_buy) / 1e9 AS f{n}_buy_sol",
               f"max(l.sol) FILTER (WHERE {c} AND l.is_buy) / 1e9 AS f{n}_max_buy",
               f"avg((l.sol >= {SOL})::INT) FILTER (WHERE {c} AND l.is_buy) AS f{n}_big_share",
               f"sum(l.sol) FILTER (WHERE {c} AND NOT l.is_buy)::DOUBLE / nullif(sum(l.sol) FILTER (WHERE {c} AND l.is_buy), 0) AS f{n}_sell_ratio",
               f"sum(l.sol) FILTER (WHERE {c} AND l.is_buy) / 1e9 / {n} AS f{n}_sol_per_s"]
    con.execute(f"""CREATE OR REPLACE TEMP TABLE fx AS
        WITH ct AS (SELECT t.key, t.create_slot, mi.create_tx, t.creator, b.block_time AS t0
                    FROM tok t JOIN blocks b ON b.slot = t.create_slot LEFT JOIN mints mi ON mi.mint = t.mint)
        SELECT m.key, m.moment, m.t, {', '.join(fw)},
               count(*) FILTER (WHERE l.slot = ct.create_slot AND l.is_buy) AS cs_buys,
               sum(l.sol) FILTER (WHERE l.slot = ct.create_slot AND l.is_buy) / 1e9 AS cs_buy_sol,
               count(*) FILTER (WHERE l.slot = ct.create_slot AND l.tx_index = ct.create_tx AND l.is_buy) AS cs_tx_buys,
               count(DISTINCT l.trader) FILTER (WHERE l.slot <= ct.create_slot + 2 AND l.is_buy) AS cs_2slot_buyers,
               COALESCE(sum(CASE WHEN l.is_buy THEN l.tokens ELSE -l.tokens END) FILTER (WHERE l.trader = ct.creator AND l.slot <= m.t), 0)::DOUBLE
                 / nullif(sum(CASE WHEN l.is_buy THEN l.tokens ELSE -l.tokens END) FILTER (WHERE l.slot <= m.t), 0) AS creator_hold_share,
               count(*) FILTER (WHERE l.trader = ct.creator AND NOT l.is_buy AND l.slot <= m.t) AS creator_sells
        FROM ent m JOIN ct USING (key) JOIN trades_all l ON l.key = m.key AND l.slot <= m.t JOIN blocks b ON b.slot = l.slot
        GROUP BY 1, 2, 3""")
    R.progress(f"extra features {_count(con, 'fx')}")


def mint_tables(con, clock, lo):
    """Per mint: the first trade's price, the 72 h peak price over curve and pool and its multiple; the early buyers (60 s)."""
    h72 = f"greatest(1, round({H72_MS} / ({ms_case(clock, 't.create_slot')})))::BIGINT"
    con.execute(f"""CREATE OR REPLACE TEMP TABLE prices AS
        SELECT key, ord, slot, vq::DOUBLE / vt AS price FROM cl WHERE vt > 0
        UNION ALL
        SELECT key, ord, slot, CASE WHEN orientation = 'reversed' THEN b_post::DOUBLE / e_post ELSE e_post::DOUBLE / b_post END FROM pl
        WHERE trade AND b_post > 0 AND e_post > 0""")
    # p0 = the price after the first trade; first_x2_slot / first_x5_slot = the first slot at which the price reached 2 x / 5 x p0
    # within 72 h (stage B's point-in-time history: a coin counts as an earlier x5 only when that slot lies before the entry)
    con.execute(f"""CREATE OR REPLACE TEMP TABLE mint_peaks AS
        WITH p0 AS (SELECT t.key, t.create_slot, arg_min(p.price, p.ord) AS p0, arg_min(p.slot, p.ord) AS first_slot
                    FROM tok t JOIN prices p ON p.key = t.key AND p.slot < t.create_slot + {h72} WHERE t.has_curve GROUP BY 1, 2)
        SELECT t.key AS mint, t.create_slot, t.creator, COALESCE(t.mayhem, false) AS mayhem, t.has_curve, t.pool, t.pool_slot, p0.p0, p0.first_slot,
               max(p.price) AS pmax, arg_min(p.slot, (-p.price, p.ord)) AS peak_slot, max(p.price) / p0.p0 AS peak_mult, count(*) AS n_trades_72,
               min(p.slot) FILTER (WHERE p.price >= 2 * p0.p0) AS first_x2_slot, min(p.slot) FILTER (WHERE p.price >= 5 * p0.p0) AS first_x5_slot,
               min(p.slot) FILTER (WHERE p.price >= 10 * p0.p0) AS first_x10_slot
        FROM tok t JOIN p0 ON p0.key = t.key JOIN prices p ON p.key = t.key AND p.slot < t.create_slot + {h72}
        GROUP BY t.key, t.create_slot, t.creator, t.mayhem, t.has_curve, t.pool, t.pool_slot, p0.p0, p0.first_slot""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE early_buyers AS
        SELECT t.key AS mint, t.create_slot, l.trader AS wallet, min(l.slot) AS first_buy_slot, sum(l.sol) / 1e9 AS buy_sol
        FROM tok t JOIN blocks b ON b.slot = t.create_slot JOIN trades_all l ON l.key = t.key AND l.is_buy JOIN blocks lb ON lb.slot = l.slot
        WHERE t.has_curve AND lb.block_time < b.block_time + {EARLY_S} GROUP BY 1, 2, 3""")
    R.progress(f"mint_peaks {_count(con, 'mint_peaks')}, early_buyers {_count(con, 'early_buyers')}")


def assemble(con, clock, lo):
    """One row per entry: identity, fill, labels, exits, runners features, extra features."""
    n = R.assemble(con, clock, lo)   # out: the runners columns (mult_1h, mult_6h = 72 h here, x_tp2 / x_tr30 / x_tr50 without delay, features)
    lab_cols = []
    for H in HORIZONS:
        for m in TARGETS:
            lab_cols.append(f"(l.ord_{m} IS NOT NULL AND l.slot_{m} <= l.cap{H} AND (l.ord_stop IS NULL OR l.ord_{m} < l.ord_stop)) AS hit_{m}_{H}")
        lab_cols += [f"(l.ord_stop IS NOT NULL AND l.slot_stop <= l.cap{H} AND (l.ord_2 IS NULL OR l.ord_stop < l.ord_2)) AS stop_first_{H}",
                     f"l.peak_net_{H} / l.cost::DOUBLE AS peak_{H}", f"(l.peak_slot_{H} - l.entry_slot) * ({ms_case(clock, 'l.entry_slot')}) / 60000.0 AS t_peak_{H}",
                     f"l.end_net_{H} / l.cost::DOUBLE AS end_{H}", f"l.dd_net_{H} / l.cost::DOUBLE AS dd_{H}", f"l.n_marks_{H}"]
    ex_cols = ", ".join(f"e.x_{r}_{H} / l.cost::DOUBLE AS x_{r}_{H}, e.trig_{r}_{H}" for r in RULES for H in HORIZONS)
    con.execute(f"""CREATE OR REPLACE TEMP TABLE out2 AS
        SELECT o.*, (o.entry_slot // 432000)::INTEGER AS epoch, hour(epoch_ms(b.block_time * 1000)) AS hour_utc, isodow(epoch_ms(b.block_time * 1000)) AS weekday,
               l.d_slots, (l.slot_stop - l.entry_slot) * ({ms_case(clock, 'l.entry_slot')}) / 60000.0 AS t_stop_min,
               {', '.join(lab_cols)}, {ex_cols},
               fx.* EXCLUDE (key, moment, t)
        FROM out o JOIN blocks b ON b.slot = o.entry_slot
        JOIN lab2 l ON l.key = o.key AND l.moment = o.moment AND l.t = o.t
        JOIN ex e ON e.key = o.key AND e.moment = o.moment AND e.t = o.t
        LEFT JOIN fx ON fx.key = o.key AND fx.moment = o.moment AND fx.t = o.t""")
    return _count(con, "out2")


def run(lo, hi, out_dir, size_sol=0.5, cfg=R.DEFAULTS, store=None, memory=None, threads=None):
    if hi > HOLDOUT_START:
        raise ValueError(f"slot {hi} reaches into the hold-out (starts at {HOLDOUT_START})")
    t0 = time.time()
    saved = (R.SIZE, R.H6_MS)   # module globals the runners functions read at call time; restored for other callers
    R.SIZE = int(round(size_sol * SOL))
    con = connect(store or os.environ.get("LAB_STORE", "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb"),
                  memory or os.environ.get("LAB_WALLETS_MEMORY", "1GB"), int(threads or os.environ.get("LAB_WALLETS_THREADS", "2")))
    try:
        clock = Clock.from_store(con)
        cost = R.tx_cost(cfg)
        hi_ext = setup(con, lo, hi, clock)
        R.entries(con, cost)
        R.marks(con, cost, hi_ext)
        labels_and_exits(con)
        R.features(con)
        extra_features(con, clock)
        mint_tables(con, clock, lo)
        n = assemble(con, clock, lo)
    finally:
        R.SIZE, R.H6_MS = saved
    os.makedirs(out_dir, exist_ok=True)
    tag = f"{lo}" if size_sol == 0.5 else f"{lo}-s{size_sol:g}"
    con.execute(f"COPY out2 TO '{out_dir}/labels-{tag}.parquet' (FORMAT PARQUET)")
    if size_sol == 0.5:
        con.execute(f"COPY mint_peaks TO '{out_dir}/mint_peaks-{lo}.parquet' (FORMAT PARQUET)")
        con.execute(f"COPY early_buyers TO '{out_dir}/early_buyers-{lo}.parquet' (FORMAT PARQUET)")
    info = {"lo": lo, "hi": hi, "hi_ext": hi_ext, "size_sol": size_sol, "entries": n, "moments": _count(con, "mom"), "tokens": _count(con, "tok"),
            "marks": _count(con, "mk"), "tx_cost_lamports": cost, "commit": git_commit(), "seconds": round(time.time() - t0, 1),
            "per_moment": {m: c for m, c in con.execute("SELECT moment, count(*) FROM out2 GROUP BY 1").fetchall()},
            "hits": {k: v for k, v in con.execute("SELECT moment, {} FROM out2 GROUP BY 1".format(
                ", ".join(f"avg(hit_{m}_{H}::INT)" for H in HORIZONS for m in TARGETS))).fetchall() and
                     [(r[0], [round(x, 5) if x is not None else None for x in r[1:]]) for r in con.execute("SELECT moment, {} FROM out2 GROUP BY 1 ORDER BY 1".format(
                         ", ".join(f"avg(hit_{m}_{H}::INT)" for H in HORIZONS for m in TARGETS))).fetchall()]},
            "hit_columns": [f"hit_{m}_{H}" for H in HORIZONS for m in TARGETS]}
    with open(f"{out_dir}/run-labels-{tag}.json", "w") as f:
        json.dump(info, f, indent=1, default=str)
    R.progress(f"done: {n} entries in {info['seconds']} s -> {out_dir}/labels-{tag}.parquet")
    return info


def main(argv=None):
    ap = argparse.ArgumentParser(description="x10-jager stage A: entries, 72 h marks, x2/x5/x10 labels, delayed exits, entry features (one window)")
    ap.add_argument("--from-slot", type=int, required=True)
    ap.add_argument("--to-slot", type=int, required=True)
    ap.add_argument("--size-sol", type=float, default=float(os.environ.get("LAB_X10_SIZE_SOL", "0.5")))
    ap.add_argument("--cfg", default=R.DEFAULTS)
    ap.add_argument("--out", default=os.path.join(OUT_ROOT, "x10-labels"))
    a = ap.parse_args(argv)
    info = run(a.from_slot, a.to_slot, a.out, a.size_sol, a.cfg)
    print(json.dumps({k: v for k, v in info.items() if k not in ("per_moment", "hits")}, default=str))


if __name__ == "__main__":
    main()
