"""E71: Mayhem coins in the curve phase (plan v0.2 section 2). Universe: mints with mayhem = true and a SOL quote
created in the window; trigger e10; Mayhem curve legs allowed (runners.legs mayhem_curve=True; model assumption: our
buy does not change the Mayhem agent's resets, every later trade is priced on the logged, reset state).

Also written per window:
  mint_peaks   section 2.0, the measurement fix: the peak price multiple within 72 h from the first trade over curve
               (vq / vt) and pool, for every Mayhem mint with a trade (not only the graduated ones), straight from the
               curve / pool tables (the x10 base rate only had the pool phase of Mayhem coins);
  parity       section 2.3's fill-parity gate on a sample of non-agent trades: the engine's curve functions on the
               logged pre-state must reproduce the logged amounts (+/- 2) for >= 99 %; otherwise the probe is not
               judged (report.py reads parity_ok from the run json);
  agent-free   the second cell: the forced exit at the last mark before the first agent leg after the entry
               (harness extra_exit_sql -> exit_net_cut / reason_cut).
"""

import random

from backtest import venues as V
from .. import runners as R
from ..common import HOLDOUT_START
from ..markout import ms_case
from . import harness as H

SYS = "11111111111111111111111111111111"
AGENT = "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s"
AGENT_PAYER = "Gygj9QQby4j2jryqyqBHvLP7ctv2SaANgh4sCb69BUpA"
H72_MS = 72 * 3_600_000
PARITY_N = 10_000
PARITY_MIN = 0.99
AGENT_LEG = f"(l.trader = '{AGENT}' OR COALESCE(l.fee_payer, '') = '{AGENT_PAYER}')"


def parity(con, n=PARITY_N, seed=7):
    """Non-agent trades on the window's Mayhem curves against the engine's curve functions on the logged pre-state.
    A buy matches when the pair (q, t) is a constant-product fill on the pre-state: exact-in (t from q, the
    buy_exact_quote_in / buy_exact_sol_in variants) or exact-out (q = t x vq / (vt - t) + 1, the buy / buy_v2 variants
    that ask for tokens); a sell when its quote follows from its tokens. Measured on a 1045 slice: exact-out
    reproduces 100 % of the non-agent buys of every variant, exact-in only the exact-in variants."""
    rows = con.execute(f"""SELECT l.is_buy, l.sol, l.tokens, l.vt_pre, l.vq_pre, l.rt_pre, l.rq_pre, l.fee_bps, l.creator_bps
                           FROM cl l WHERE NOT {AGENT_LEG} AND l.vt_pre > 0 AND l.rt_pre > 0""").fetchall()
    random.Random(seed).shuffle(rows)
    rows = rows[:n]
    out = {"sampled": len(rows), "buys": 0, "buys_ok": 0, "buys_ok_exact_in": 0, "buys_ok_exact_out": 0, "sells": 0, "sells_ok": 0}
    for is_buy, q, t, vt, vq, rt, rq, fb, cb in rows:
        c = V.Curve(int(vt), int(vq), int(rt), int(rq), int(fb or 0), int(cb or 0))
        if is_buy:
            out["buys"] += 1
            q, t = int(q), int(t)
            t_in = (q - 1) * c.vt // (c.vq + q - 1) if q > 1 else None
            q_out = t * c.vq // (c.vt - t) + 1 if 0 < t < c.vt else None
            ok_in = t_in is not None and abs(t_in - t) <= 2
            ok_out = q_out is not None and abs(q_out - q) <= 2
            out["buys_ok_exact_in"] += ok_in
            out["buys_ok_exact_out"] += ok_out
            out["buys_ok"] += ok_in or ok_out
        else:
            out["sells"] += 1
            f = V.curve_sell(c, int(t))
            out["sells_ok"] += f is not None and abs(f.curve_quote - int(q)) <= 2
    ok = out["buys_ok"] + out["sells_ok"]
    out["rate"] = ok / len(rows) if rows else None
    out["parity_ok"] = bool(rows) and out["rate"] >= PARITY_MIN
    return out


def mint_peaks(con, lo, hi, clock):
    """Section 2.0: per Mayhem mint with a SOL quote created in the window and >= 1 trade: the first trade's price, the 72 h
    peak over curve (vq / vt) and pool, the multiple, graduated or not."""
    h72 = f"greatest(1, round({H72_MS} / ({ms_case(clock, 'm.create_slot')})))::BIGINT"
    con.execute(f"""CREATE OR REPLACE TEMP TABLE mint_peaks AS
        WITH m AS (SELECT mint, create_slot, complete_slot, least({HOLDOUT_START}, create_slot + {h72}) AS hi72
                   FROM mints m WHERE mayhem AND quote_mint = '{SYS}' AND create_slot >= {lo} AND create_slot < {hi}),
        pr AS (
            SELECT m.mint, c.slot, c.slot * 1000000000 + c.tx_index * 100000 + c.outer_ix * 1000 + c.inner_ix AS ord, c.vq::DOUBLE / c.vt AS price,
                   (c.trader = '{AGENT}' OR COALESCE(c.fee_payer, '') = '{AGENT_PAYER}') AS agent
            FROM curve c JOIN m ON m.mint = c.mint WHERE c.vt > 0 AND c.slot >= m.create_slot AND c.slot < m.hi72
            UNION ALL
            SELECT m.mint, p.slot, p.slot * 1000000000 + p.tx_index * 100000 + p.outer_ix * 1000 + p.inner_ix,
                   CASE WHEN ps.orientation = 'reversed' THEN p.b::DOUBLE / p.e ELSE p.e::DOUBLE / p.b END, false
            FROM pool p JOIN pools ps ON ps.pool = p.pool JOIN m ON m.mint = ps.mint
            WHERE ps.quote_class = 'sol' AND p.kind IN ('buy', 'sell') AND p.b > 0 AND p.e > 0 AND p.slot >= m.create_slot AND p.slot < m.hi72),
        p0 AS (SELECT mint, arg_min(price, ord) AS p0, arg_min(slot, ord) AS first_slot, count(*) AS n_trades, sum(agent::INT) AS n_agent FROM pr GROUP BY mint)
        SELECT m.mint, m.create_slot, m.complete_slot IS NOT NULL AS graduated, p0.p0, p0.first_slot, p0.n_trades, p0.n_agent,
               max(pr.price) AS pmax, max(pr.price) / p0.p0 AS peak_mult, arg_min(pr.slot, (-pr.price, pr.ord)) AS peak_slot,
               min(pr.slot) FILTER (WHERE pr.price >= 2 * p0.p0) AS first_x2_slot, min(pr.slot) FILTER (WHERE pr.price >= 10 * p0.p0) AS first_x10_slot
        FROM m JOIN p0 USING (mint) JOIN pr USING (mint) WHERE p0.p0 > 0
        GROUP BY m.mint, m.create_slot, m.complete_slot, p0.p0, p0.first_slot, p0.n_trades, p0.n_agent""")
    return con.execute("""SELECT count(*), sum(graduated::INT), avg((peak_mult >= 2)::INT), avg((peak_mult >= 5)::INT), avg((peak_mult >= 10)::INT),
                                 avg((peak_mult >= 20)::INT), median(peak_mult),
                                 avg((peak_mult >= 10)::INT) FILTER (WHERE graduated), avg((peak_mult >= 10)::INT) FILTER (WHERE NOT graduated)
                          FROM mint_peaks""").fetchone()


def run(con, lo, hi, clock, size_sol, cost_tx, out_dir):
    tok = H.tok_sql_for_mints(f"COALESCE(m.mayhem, false) AND m.quote_mint = '{SYS}' AND m.create_slot >= {lo} AND m.create_slot < {hi}")
    hi_ext = H.prepare(con, lo, hi, clock, tok, mayhem_curve=True)
    par = parity(con)
    R.progress(f"E71 parity: {par}")
    H.moments_from(con, H.e10_sql(), clock)
    cut = f"""SELECT e.key, e.moment, e.t, min(l.ord) AS ord_cut FROM ent e JOIN cl l ON l.key = e.key AND l.ord > e.entry_ord AND {AGENT_LEG}
              GROUP BY 1, 2, 3"""
    H.simulate(con, cost_tx, hi_ext, clock, extra_exit_sql=cut)
    pk = mint_peaks(con, lo, hi, clock)
    R.progress(f"E71 mint_peaks: mints {pk[0]}, graduated {pk[1]}, >=x2 {pk[2]:.4f}, >=x5 {pk[3]:.4f}, >=x10 {pk[4]:.4f}, >=x20 {pk[5]:.4f}, median {pk[6]:.3f}")
    con.execute(f"COPY mint_peaks TO '{out_dir}/mint_peaks-{lo}.parquet' (FORMAT PARQUET)")
    base = {"mints": pk[0], "graduated": pk[1], "ge_x2": pk[2], "ge_x5": pk[3], "ge_x10": pk[4], "ge_x20": pk[5], "median_peak": pk[6],
            "ge_x10_graduated": pk[7], "ge_x10_not_graduated": pk[8]}
    return H.write(con, out_dir, "e71", lo, size_sol, "e71", {"parity": par, "parity_ok": par["parity_ok"], "base_rates": base,
                                                                 "agent": AGENT, "agent_payer": AGENT_PAYER})
