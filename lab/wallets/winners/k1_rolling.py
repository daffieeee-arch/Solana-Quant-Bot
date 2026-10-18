"""K1 with a rolling, point-in-time label (lab/reports/2026-10-17-k1-houdbaarheid.md §3, register E40). NAS job.

Candidates = every wallet with at least one curve buy >= 3 SOL in the period (the sensitivity set); the fixed threshold
of the copy is 5 SOL (chosen once, E39), with 3 and 10 SOL shown as sensitivity only. Per candidate: curve rounds
(wallet x mint over the period), cash per UTC day. Label for day t: over the 9 days before t the wallet had >= 7 days
with positive cash on rounds that were closed **as of t** (tokens sold within 5 % of bought, counted over the days
before t only) and > 10 SOL in total (E24's strict label, moved day by day). Then every round of a labelled wallet
entered on day t with a first buy >= MIN_BUY SOL is copied with 0.5 SOL — closed or not: buy at the curve state before
fb + d, sell before xs + d where xs = their first sell after the buy (fs) or, when they have not sold within CAP_SLOTS
slots (~6 h) or before the epoch's end, the cap (exit_kind 'plafond'); fee bps of their buy, the base scenario's cost per
order (fee + tip + the expected failed attempts at land_prob 0.97: costs.Scenario.per_tx); d = 0 / 2 / 3. Beside it,
for comparison with E40 only, l3 = the sell before their LAST sell + 3 (the look-ahead exit of E40).

"Their first sell after the buy" means: the first sell of the wallet in this mint after their first buy of the epoch in
transaction order, (slot, tx_index); a sell later in the same slot as the buy counts (exit_kind '… (zelfde slot)': the
copy then enters at fb + d and exits at fs + d = fb + d, a cost-only round). Codex round 2 (2026-10-18), point 3: the
99q run used `slot > fbe`, which dropped those same-slot sells and took the next sell or the cap instead.

Settlement when the curve completes (Codex round 2, point 2). The 99q run tried to mark a round whose curve completed
before the exit as NULL via `variant ILIKE '%complete%'`, but the store's variants are instruction names (buy, sell, …):
no round was ever NULL, and such rounds were valued at the last curve state (the completion state). Now: completion =
s.mints.complete_slot; a round whose curve completes before the entry (fb + d) is not entered (observable at the entry);
one that completes between entry and exit is sold on its PumpSwap pool (s.mints.pool) at the last pool state before
xs + d (constant product, the pool's fee bps), or at the pool's initial reserves when the pool has no row before xs + d
(then a few slots later than xs + d: migration is a median 2 slots after completion); without a pool state the result is
unknown (NULL). The capacity simulation takes every entered round in causal order (fb, fb_tx of the signal buy), never
on the exit; an unknown round keeps its slot busy and is counted apart, with a lower bound that books it as a total loss.

Codex finding 6 (2026-10-18, confirmed): the E40 run (b57ea02, NAS job 99k) decided `closed` over the whole period and
selected both the label's cash and the copy set on it, plus on the exit being in the entry's epoch and on their last
sell. Bags never closed are the losers, so that favoured K1: the true point-in-time result is at or below E40's
-83.6 SOL/day. This version removes those three selections; E40's numbers stay those of b57ea02 (register E40).

The rolling label needs 9 earlier days, so on dev it exists from day 10 on (270 ms only): a regime split is not
point-in-time possible on dev; the forward period (Alpenglow) is one regime. Capacity simulation: max 5 (and 20) open,
one per mint, busy until the exit. Day sums and the day bootstrap. Still a model assumption: the curve formula without a
landing chance.

  PYTHONPATH=lab python -m wallets.winners.k1_rolling [--smoke]      # --smoke: epochs 1040-1041 only, label window 2 days

Outputs under LAB_WALLETS_OUT/winners-v1: k1_rolling_rounds.parquet, k1_rolling_labels.parquet, k1_rolling.md.
DuckDB memory LAB_WALLETS_MEMORY (default 1400MB), spill LAB_DATA_ROOT/tmp/winners-v1.
"""
import argparse
import heapq
import os
import sys
import time

import duckdb
import numpy as np

DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
S = os.environ.get("LAB_STORE", f"{DATA}/store/dev2.duckdb")
W = os.path.join(os.environ.get("LAB_WALLETS_OUT", f"{DATA}/wallets"), "winners-v1")
MEM = os.environ.get("LAB_WALLETS_MEMORY", "1400MB")
MIN_BUY = 5e9  # lamports: the copy follows buys of at least 5 SOL (fixed, E39); 3 and 10 SOL are sensitivity only
MIN_CAND = 3e9
SIZE = 5e8
CAP_SLOTS = 100_000  # ~6 h at 218 ms: the copy's time cap when the wallet has not sold (E39 §0 point 4)
POOL_BPS = 30  # PumpSwap fee bps when the pool row carries none (lp 20 + protocol 5 + creator 5)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from backtest import costs  # noqa: E402
from backtest.store import load_defaults  # noqa: E402
COST = 2 * costs.Scenario.from_cfg("base", load_defaults()).per_tx()  # 2 x 720,618 lamports: fee, tip and expected failed attempts
EPOCH0, EPOCH_SLOTS = 446_256_000, 432_000
t0 = time.time()

# their first sell after the epoch's first buy, in transaction order (slot, tx_index); fs_same_slot = that sell is in the
# buy's slot. Needs a table lg(wallet, mint, slot, tx_index, is_buy, ...).
FIRST_SELL_SQL = """CREATE OR REPLACE TABLE fsl AS
    SELECT lg.wallet, lg.mint, min(lg.slot) AS fs, min(lg.slot) = min(b.fbe) AS fs_same_slot
    FROM lg JOIN (SELECT wallet, mint, min(slot) AS fbe, arg_min(tx_index, (slot, tx_index)) AS fbe_tx FROM lg WHERE is_buy GROUP BY 1, 2) b USING (wallet, mint)
    WHERE NOT lg.is_buy AND (lg.slot > b.fbe OR (lg.slot = b.fbe AND lg.tx_index > b.fbe_tx)) GROUP BY 1, 2"""

NOT_ENTERED = "niet ingestapt (curve voltooid vóór de instap)"
UNKNOWN = "onbekend (geen poolstand)"


def log(*a):
    print(round(time.time() - t0, 1), *a, flush=True)


def capacity(rows, mo):
    """Greedy capacity: `rows` in causal order (the signal buy's (fb, tx_index)), each (entry_slot, exit_slot, net, day,
    mint); max `mo` open, one per mint, busy until the exit. net None = unknown outcome: the slot stays busy and the
    round is counted apart. Returns (taken, unknown, pnl per day over the known rounds, unknown rounds per day)."""
    heap, busy, taken, unknown, pnl, nunk = [], set(), 0, 0, {}, {}
    for ent, ext, net, day, mint in rows:
        while heap and heap[0][0] <= ent:
            _, m = heapq.heappop(heap)
            busy.discard(m)
        if len(heap) >= mo or mint in busy:
            continue
        heapq.heappush(heap, (ext, mint))
        busy.add(mint)
        taken += 1
        if net is None:
            unknown += 1
            nunk[day] = nunk.get(day, 0) + 1
        else:
            pnl[day] = pnl.get(day, 0) + net
    return taken, unknown, pnl, nunk


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    a = ap.parse_args(argv)
    epochs = [(EPOCH0 + EPOCH_SLOTS * i, EPOCH0 + EPOCH_SLOTS * (i + 1)) for i in (range(7, 9) if a.smoke else range(14))]
    win, min_pos, min_sol = (2, 1, 1e9) if a.smoke else (9, 7, 10e9)
    if a.smoke:
        log("SMOKE: loose label (2 days, 1 positive day, > 1 SOL) on two epochs; the numbers mean nothing, only the pipeline")
    os.makedirs(W, exist_ok=True)
    os.makedirs(f"{DATA}/tmp/winners-v1", exist_ok=True)  # DuckDB does not create its temp_directory (NAS job 99p)
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{MEM}'; SET threads TO 2; SET preserve_insertion_order=false; SET temp_directory='{DATA}/tmp/winners-v1'; SET max_temp_directory_size='40GB'")
    con.execute(f"ATTACH '{S}' AS s (READ_ONLY)")
    lo_all, hi_all = epochs[0][0], epochs[-1][1]
    con.execute("CREATE TABLE days AS SELECT day, slot0, row_number() OVER (ORDER BY slot0) AS di FROM (SELECT strftime(to_timestamp(block_time), '%Y-%m-%d') AS day, min(slot)::BIGINT AS slot0 FROM s.blocks GROUP BY 1)")
    con.execute(f"CREATE TABLE cand AS SELECT DISTINCT trader AS wallet FROM s.curve WHERE is_buy AND q >= {MIN_CAND} AND slot >= {lo_all} AND slot < {hi_all} AND NOT mayhem_trade")
    log("candidates", con.execute("SELECT count(*) FROM cand").fetchone()[0])
    first = True
    for lo, hi in epochs:
        con.execute(f"""CREATE OR REPLACE TABLE lg AS
            SELECT c.trader AS wallet, c.mint, c.slot::BIGINT AS slot, c.tx_index::BIGINT AS tx_index, c.is_buy, c.q::DOUBLE AS sol, c.t::DOUBLE AS tok,
                   (c.fee_bps + c.creator_bps) AS bps, c.tx_fee + c.priority_fee + c.jito_tip AS fee
            FROM s.curve c JOIN cand ON cand.wallet = c.trader LEFT JOIN s.mints m USING (mint)
            WHERE c.slot >= {lo} AND c.slot < {hi} AND NOT c.mayhem_trade AND NOT COALESCE(m.mayhem, false)""")
        con.execute("""CREATE OR REPLACE TABLE ag AS
            SELECT wallet, mint, d.day,
                   sum(CASE WHEN is_buy THEN -sol ELSE sol END) AS cash, sum(CASE WHEN is_buy THEN tok END) AS tok_in, sum(CASE WHEN NOT is_buy THEN tok END) AS tok_out,
                   min(CASE WHEN is_buy THEN slot END) AS fb, arg_min(tx_index, CASE WHEN is_buy THEN (slot, tx_index) ELSE (9e18, 0) END) AS fb_tx,
                   max(CASE WHEN NOT is_buy THEN slot END) AS ls, count(*) FILTER (WHERE is_buy) AS n_buys,
                   arg_min(sol, CASE WHEN is_buy THEN (slot, tx_index) ELSE (9e18, 0) END) AS first_buy_sol, arg_min(bps, CASE WHEN is_buy THEN (slot, tx_index) ELSE (9e18, 0) END) AS bps
            FROM lg ASOF LEFT JOIN days d ON d.slot0 <= lg.slot GROUP BY 1, 2, 3""")
        con.execute(FIRST_SELL_SQL)
        con.execute("CREATE OR REPLACE TABLE fe AS SELECT wallet, mint, day, sum(fee) AS fees FROM (SELECT wallet, min(mint) AS mint, any_value(d.day) AS day, any_value(fee) AS fee FROM lg ASOF LEFT JOIN days d ON d.slot0 <= lg.slot GROUP BY wallet, slot, tx_index) GROUP BY 1, 2, 3")
        con.execute("CREATE OR REPLACE TABLE e AS SELECT ag.*, COALESCE(fe.fees, 0) AS fees, fsl.fs, fsl.fs_same_slot FROM ag LEFT JOIN fe USING (wallet, mint, day) LEFT JOIN fsl USING (wallet, mint)")
        if first:
            con.execute("CREATE TABLE wmd AS SELECT * FROM e")
            first = False
        else:
            con.execute("INSERT INTO wmd SELECT * FROM e")
        log("epoch", lo, con.execute("SELECT count(*) FROM e").fetchone()[0])
    # rounds over the period (closed is reporting only: it is not known at the entry)
    con.execute("""CREATE TABLE rounds AS
        SELECT wallet, mint, sum(tok_in) AS tin, sum(tok_out) AS tout, sum(cash) - sum(fees) AS net, min(fb) AS fb, arg_min(fb_tx, fb) AS fb_tx, max(ls) AS ls,
               min(fs) AS fs, COALESCE(arg_min(fs_same_slot, fs), false) AS fs_same_slot,
               arg_min(day, fb) AS entry_day, arg_min(first_buy_sol, fb) AS first_buy_sol, arg_min(bps, fb) AS bps, sum(n_buys) AS n_buys,
               sum(tok_in) > 0 AND abs(COALESCE(sum(tok_out), 0) - sum(tok_in)) <= 0.05 * sum(tok_in) AS closed
        FROM wmd GROUP BY 1, 2""")
    con.execute(f"COPY rounds TO '{W}/k1_rolling_rounds.parquet' (FORMAT PARQUET)")
    log("rounds", con.execute("SELECT count(*), sum(closed::INT), count(DISTINCT wallet) FROM rounds").fetchone())
    # rolling label per day t: the 'win' days before t, on the rounds closed as of t (sold within 5 % over the days before t)
    con.execute("CREATE TABLE wd AS SELECT w.*, d.di FROM wmd w JOIN days d USING (day)")
    con.execute(f"""CREATE TABLE lab AS
        WITH m AS (SELECT t.day AS t, x.wallet, x.mint FROM days t JOIN wd x ON x.di < t.di WHERE t.di > {win}
                   GROUP BY 1, 2, 3 HAVING sum(x.tok_in) > 0 AND abs(COALESCE(sum(x.tok_out), 0) - sum(x.tok_in)) <= 0.05 * sum(x.tok_in)),
             pdt AS (SELECT t.day AS t, x.wallet, x.day AS h, sum(x.cash - x.fees) AS net
                     FROM days t JOIN wd x ON x.di < t.di AND x.di >= t.di - {win} JOIN m ON m.t = t.day AND m.wallet = x.wallet AND m.mint = x.mint
                     GROUP BY 1, 2, 3)
        SELECT t, wallet, count(*) FILTER (WHERE net > 0) AS d_pos, sum(net) AS net_win, count(*) AS d_act
        FROM pdt GROUP BY 1, 2 HAVING count(*) FILTER (WHERE net > 0) >= {min_pos} AND sum(net) > {min_sol}""")
    con.execute(f"COPY lab TO '{W}/k1_rolling_labels.parquet' (FORMAT PARQUET)")
    log("labels per day", con.execute("SELECT t, count(*) FROM lab GROUP BY 1 ORDER BY 1").fetchall())
    # rounds to copy: every round of a labelled wallet entered on day t with a first buy >= MIN_CAND (no selection on the
    # close, the exit epoch or their last sell); exit xs = their first sell after the buy, else the cap or the epoch's end;
    # completion and the migration pool from s.mints (observable at the time they happen)
    con.execute(f"""CREATE TABLE sel AS SELECT r.*, l.d_pos, l.net_win, m.complete_slot::BIGINT AS complete_slot, m.migrate_slot::BIGINT AS migrate_slot, m.pool,
            ((r.fb - {EPOCH0}) // {EPOCH_SLOTS} + 1) * {EPOCH_SLOTS} + {EPOCH0} - 3 AS epoch_end,
            CASE WHEN r.fs IS NOT NULL AND r.fs <= least(r.fb + {CAP_SLOTS}, epoch_end) THEN r.fs ELSE least(r.fb + {CAP_SLOTS}, epoch_end) END AS xs,
            CASE WHEN r.fs IS NOT NULL AND r.fs <= least(r.fb + {CAP_SLOTS}, epoch_end) THEN (CASE WHEN r.fs_same_slot THEN 'hun eerste verkoop (zelfde slot als de koop)' ELSE 'hun eerste verkoop' END)
                 WHEN r.fs IS NOT NULL THEN 'plafond (verkoop later)' ELSE 'plafond (niet verkocht)' END AS exit_kind,
            CASE WHEN r.ls IS NOT NULL AND r.ls > r.fb AND r.ls <= epoch_end THEN r.ls END AS lsx
        FROM rounds r JOIN lab l ON l.wallet = r.wallet AND l.t = r.entry_day LEFT JOIN s.mints m ON m.mint = r.mint
        WHERE r.first_buy_sol >= {MIN_CAND}""")
    log("rounds to copy", con.execute("SELECT count(*), count(DISTINCT wallet), count(DISTINCT entry_day) FROM sel").fetchone(),
        con.execute("SELECT exit_kind, count(*) FROM sel GROUP BY 1 ORDER BY 1").fetchall(),
        "curve completes before xs + 3:", con.execute("SELECT count(*) FROM sel WHERE complete_slot < xs + 3").fetchone()[0])
    first = True
    pst = lambda d: f"arg_max(CASE WHEN po.orientation = 'reversed' THEN struct_pack(q := p.b::DOUBLE, t := p.e::DOUBLE) ELSE struct_pack(q := p.e::DOUBLE, t := p.b::DOUBLE) END, (p.slot, p.tx_index)) FILTER (WHERE p.slot < se.xs + {d}) AS p{d}"
    for lo, hi in epochs:
        con.execute(f"CREATE OR REPLACE TABLE se AS SELECT wallet, mint, pool, complete_slot, fb, xs, lsx, greatest(xs, COALESCE(lsx, 0)) AS hx FROM sel WHERE fb >= {lo} AND fb < {hi}")
        con.execute(f"""CREATE OR REPLACE TABLE st AS
            SELECT se.wallet, se.mint,
                   arg_max(struct_pack(q := c.vq::DOUBLE, t := c.vt::DOUBLE), (c.slot, c.tx_index)) FILTER (WHERE c.slot < se.fb) AS e0,
                   arg_max(struct_pack(q := c.vq::DOUBLE, t := c.vt::DOUBLE), (c.slot, c.tx_index)) FILTER (WHERE c.slot < se.fb + 2) AS e2,
                   arg_max(struct_pack(q := c.vq::DOUBLE, t := c.vt::DOUBLE), (c.slot, c.tx_index)) FILTER (WHERE c.slot < se.fb + 3) AS e3,
                   arg_max(struct_pack(q := c.vq::DOUBLE, t := c.vt::DOUBLE), (c.slot, c.tx_index)) FILTER (WHERE c.slot < se.xs) AS x0,
                   arg_max(struct_pack(q := c.vq::DOUBLE, t := c.vt::DOUBLE), (c.slot, c.tx_index)) FILTER (WHERE c.slot < se.xs + 2) AS x2,
                   arg_max(struct_pack(q := c.vq::DOUBLE, t := c.vt::DOUBLE), (c.slot, c.tx_index)) FILTER (WHERE c.slot < se.xs + 3) AS x3,
                   arg_max(struct_pack(q := c.vq::DOUBLE, t := c.vt::DOUBLE), (c.slot, c.tx_index)) FILTER (WHERE se.lsx IS NOT NULL AND c.slot < se.lsx + 3) AS l3s
            FROM se JOIN s.curve c ON c.mint = se.mint AND c.slot >= se.fb - 2000 AND c.slot < se.hx + 3 GROUP BY 1, 2""")
        # the PumpSwap pool of a round whose curve completes before xs + 3: last pool state before xs + d, the pool's fee
        # bps, and the initial reserves as the fallback when the pool has no row yet
        con.execute(f"""CREATE OR REPLACE TABLE pt AS
            SELECT se.wallet, se.mint, {pst(0)}, {pst(2)}, {pst(3)},
                   any_value(p.lp_bps + p.protocol_bps + p.creator_bps) AS pbps,
                   any_value(CASE WHEN po.orientation = 'reversed' THEN struct_pack(q := po.b0::DOUBLE, t := po.q0::DOUBLE) ELSE struct_pack(q := po.q0::DOUBLE, t := po.b0::DOUBLE) END) AS pinit
            FROM se LEFT JOIN s.pools po ON po.pool = se.pool LEFT JOIN s.pool p ON p.pool = se.pool AND p.slot < se.xs + 3
            WHERE se.complete_slot IS NOT NULL AND se.complete_slot < se.xs + 3 GROUP BY 1, 2""")
        con.execute("CREATE OR REPLACE TABLE stp AS SELECT st.*, pt.p0, pt.p2, pt.p3, pt.pbps, pt.pinit FROM st LEFT JOIN pt USING (wallet, mint)")
        if first:
            con.execute("CREATE TABLE states AS SELECT * FROM stp")
            first = False
        else:
            con.execute("INSERT INTO states SELECT * FROM stp")
        log("states", lo, con.execute("SELECT count(*), count(p3), count(pinit) FROM stp").fetchone())
    con.execute("CREATE MACRO buy_tok(st, sol, f) AS CASE WHEN st IS NULL OR st.q <= 0 OR st.t <= 0 THEN NULL ELSE st.t - st.t * st.q / (st.q + sol * (1 - f/10000.0)) END")
    con.execute("CREATE MACRO sell_sol(st, tok, f) AS CASE WHEN st IS NULL OR tok IS NULL OR st.q <= 0 OR st.t <= 0 THEN NULL ELSE (st.q - st.q * st.t / (st.t + tok)) * (1 - f/10000.0) END")
    cols = []
    for d in (0, 2, 3):
        tok = f"buy_tok(e{d}, {SIZE}, COALESCE(bps, 125))"
        cols.append(f"complete_slot IS NULL OR complete_slot >= fb + {d} AS entered_{d}")
        cols.append(f"""CASE WHEN NOT (complete_slot IS NULL OR complete_slot >= fb + {d}) THEN '{NOT_ENTERED}'
                             WHEN complete_slot IS NULL OR complete_slot >= xs + {d} THEN 'curve'
                             WHEN COALESCE(p{d}, pinit) IS NOT NULL THEN 'pool (na migratie)' ELSE '{UNKNOWN}' END AS settle_{d}""")
        cols.append(f"""CASE WHEN NOT (complete_slot IS NULL OR complete_slot >= fb + {d}) THEN NULL
                             WHEN complete_slot IS NULL OR complete_slot >= xs + {d} THEN sell_sol(x{d}, {tok}, COALESCE(bps, 125)) - {SIZE} - {COST}
                             ELSE sell_sol(COALESCE(p{d}, pinit), {tok}, COALESCE(pbps, {POOL_BPS})) - {SIZE} - {COST} END AS h{d}""")
    cols.append(f"CASE WHEN lsx IS NULL OR complete_slot < lsx + 3 THEN NULL ELSE sell_sol(l3s, buy_tok(e3, {SIZE}, COALESCE(bps, 125)), COALESCE(bps, 125)) - {SIZE} - {COST} END AS l3")
    con.execute(f"CREATE TABLE out AS SELECT sel.*, {', '.join(cols)} FROM sel JOIN states USING (wallet, mint)")
    con.execute(f"COPY out TO '{W}/k1_rolling_rounds.parquet' (FORMAT PARQUET)")
    # tables
    L = ["# K1 met een rollend point-in-time label (E40, point-in-time versie na Codex-bevinding 6 en Codex-ronde 2)", "",
         f"Kandidaten: wallets met een curve-koop ≥ {MIN_CAND/1e9:.0f} SOL in de periode; label voor dag t: in de {win} dagen vóór t ≥ {min_pos} dagen met positieve kas op rondes die *op dag t* gesloten waren (verkocht binnen 5 %, geteld over de dagen vóór t) en > {min_sol/1e9:.0f} SOL. "
         f"Kopie 0,5 SOL op fb + d van élke ronde van een gelabelde wallet (gesloten of niet), verkoop op xs + d met xs = hun eerste verkoop na de koop in transactievolgorde (ook later in hetzelfde slot), anders het plafond ({CAP_SLOTS:,} slots ≈ 6 u) of het einde van het epoch; "
         f"kosten 2 × {COST//2:,} lamports (base: fee, tip, verwachte mislukte pogingen). Vaste drempel {MIN_BUY/1e9:.0f} SOL; 3 en 10 SOL alleen als gevoeligheid. "
         "Voltooit de curve vóór de instap, dan wordt niet ingestapt; voltooit hij tussen instap en uitstap, dan verkoopt de kopie op de PumpSwap-pool (laatste poolstand vóór xs + d, anders de startreserves van de pool); zonder poolstand is de uitkomst onbekend (NULL). "
         "l3 = verkoop na hun **laatste** verkoop + 3 (de uitstap van E40, kijkt vooruit; alleen ter vergelijking). Alles in SOL.", ""]
    if a.smoke:
        L.append("**SMOKE: los label op twee epochs; de getallen betekenen niets.**\n")
    for thr in (3e9, 5e9, 10e9):
        tag = "VAST" if thr == MIN_BUY else "gevoeligheid"
        L += [f"## Drempel ≥ {thr/1e9:.0f} SOL ({tag})", "", "| dag | wallets gelabeld | rondes (ingestapt, d3) | hun netto gem. | kopie d0 gem. | kopie d2 gem. | kopie d3 gem. | d3 winstkans | Σ d3 | Σ l3 (E40-uitstap) |", "|---|---|---|---|---|---|---|---|---|---|"]
        for r in con.execute(f"""SELECT o.entry_day, (SELECT count(*) FROM lab WHERE lab.t = o.entry_day), count(*) FILTER (WHERE entered_3), round(avg(net)/1e9,3), round(avg(h0)/1e9,4), round(avg(h2)/1e9,4), round(avg(h3)/1e9,4), round(avg((h3>0)::INT),3), round(sum(h3)/1e9,1), round(sum(l3)/1e9,1)
                                FROM out o WHERE first_buy_sol >= {thr} GROUP BY 1 ORDER BY 1""").fetchall():
            L.append("| " + " | ".join(str(x) for x in r) + " |")
        L += ["", "| uitstap | rondes | gesloten (achteraf) | kopie d3 gem. | d3 winstkans | Σ d3 |", "|---|---|---|---|---|---|"]
        for r in con.execute(f"""SELECT exit_kind, count(*), sum(closed::INT), round(avg(h3)/1e9,4), round(avg((h3>0)::INT),3), round(sum(h3)/1e9,1)
                                FROM out WHERE first_buy_sol >= {thr} GROUP BY 1 ORDER BY 1""").fetchall():
            L.append("| " + " | ".join(str(x) for x in r) + " |")
        L += ["", "| afwikkeling (d3) | rondes | kopie d3 gem. | Σ d3 |", "|---|---|---|---|"]
        for r in con.execute(f"SELECT settle_3, count(*), round(avg(h3)/1e9,4), round(sum(h3)/1e9,1) FROM out WHERE first_buy_sol >= {thr} GROUP BY 1 ORDER BY 1").fetchall():
            L.append("| " + " | ".join(str(x) for x in r) + " |")
        rows = con.execute(f"SELECT fb + 3, xs + 3, h3, entry_day, mint FROM out WHERE entered_3 AND first_buy_sol >= {thr} ORDER BY fb, fb_tx, wallet, mint").fetchall()
        days = sorted({r[3] for r in rows})
        rng = np.random.default_rng(7)
        L += ["", f"Capaciteitssimulatie ≥ {thr/1e9:.0f} SOL (gretig in signaalvolgorde (fb, tx_index), één per token, busy tot de exit xs + 3; onbekende uitkomst houdt de plek bezet; ondergrens boekt onbekend als totaal verlies):", "",
              "| max open | genomen / beschikbaar | onbekend | Σ | per dag [2,5 %; 97,5 %] | Σ ondergrens | slechtste dag | dagen < 0 |", "|---|---|---|---|---|---|---|---|"]
        for mo in (5, 20):
            taken, unknown, pnl, nunk = capacity(rows, mo)
            arr = np.array([pnl.get(d, 0) / 1e9 for d in days]) if days else np.array([0.0])
            lower = arr - np.array([nunk.get(d, 0) * (SIZE + COST) / 1e9 for d in days]) if days else arr
            boot = np.array([arr[rng.integers(0, len(arr), len(arr))].mean() for _ in range(10000)])
            L.append(f"| {mo} | {taken} / {len(rows)} | {unknown} | {arr.sum():.1f} | {arr.mean():.2f} [{np.percentile(boot, 2.5):.2f}; {np.percentile(boot, 97.5):.2f}] | {lower.sum():.1f} | {arr.min():.2f} | {(arr < 0).sum()} van {len(arr)} |")
        L.append("")
    L += ["## Regimes", "", "Het rollende label bestaat op dev vanaf dag 10 (negen eerdere dagen nodig); die dagen zijn allemaal 270 ms. Een regime-splitsing is op dev niet point-in-time mogelijk; forward (Alpenglow) is één regime.", "",
          "## Voorbehoud", "", "Curve-formule en constant product op de gelogde reserves, zonder landingskans; gretige capaciteit zonder dagverlies-stop. Een ronde die op de pool wordt afgewikkeld terwijl de pool vóór xs + d nog geen rij heeft, verkoopt tegen de startreserves van de pool (enkele slots later dan xs + d). "
          "Een verkoop in hetzelfde slot als hun koop telt als uitstap: de kopie koopt en verkoopt dan op fb + d (alleen kosten).", ""]
    with open(f"{W}/k1_rolling.md", "w") as f:
        f.write("\n".join(L) + "\n")
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
