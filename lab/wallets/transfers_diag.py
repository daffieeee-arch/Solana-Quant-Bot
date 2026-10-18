"""Diagnosis of check 4.1 (conservation per mint-day) of the transfers module: on the NAS runs of epochs 1040 / 1045
only 95–96 % of the mint-days conserve (bar 99.9 %), the failures concentrated in mints with `buy_exact_sol_in` curve
legs. Conservation is a sum over transactions, so the deficit of a mint is the sum of its transactions' residuals:

    residual(tx) = Σ ledger deltas of the mint in the tx − (minted − burned in the tx, from token_transfers)

A transfer, a trade or a transfer-free tx has residual 0; the create tx has 0 after the mint_to. Every non-zero
residual names the rows that are missing or doubled. This module groups the residuals by the transaction's pattern
(venue, curve variants, which ledger sources it has, which token_transfers kinds) so the pattern that carries the
deficit is named, with examples and their rows for inspection. Nothing here changes the ledger or the report.

  LAB_STORE=<store> python -m wallets.transfers_diag --chunks <root> --from-slot A --to-slot B --out DIR
"""

# no-quote-filter: consumed data, conclusions audited 2026-10-18 (lab/reports/2026-10-18-audit-niet-sol-quote.md)
import argparse
import json
import os
import sys
import time

from . import transfers as T
from .common import STORE, Clock, connect, git_commit


def diagnose(con, lo, hi):
    """txres: one row per (mint, slot, tx_index) with a non-zero residual, its pattern and whether the mint fails 4.1."""
    con.execute(f"""CREATE OR REPLACE TEMP TABLE cv AS
        SELECT c.mint, c.slot, c.tx_index, list_sort(list_distinct(list(COALESCE(c.variant, '?')))) AS variants, count(*) AS n_curve,
               bool_or(c.is_buy) AS curve_buy, bool_or(NOT c.is_buy) AS curve_sell
        FROM curve c SEMI JOIN wmints w ON w.mint = c.mint WHERE c.slot >= {lo} AND c.slot < {hi} GROUP BY 1, 2, 3""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE pv AS
        SELECT ps.mint, p.slot, p.tx_index, count(*) AS n_pool, list_sort(list_distinct(list(p.kind))) AS pool_kinds
        FROM pool p JOIN pools ps USING (pool) SEMI JOIN wmints w ON w.mint = ps.mint WHERE p.slot >= {lo} AND p.slot < {hi} GROUP BY 1, 2, 3""")
    con.execute("""CREATE OR REPLACE TEMP TABLE lg AS
        SELECT mint, slot, tx_index, sum(delta) AS net,
               sum(delta) FILTER (src = 'leg') AS net_leg, sum(delta) FILTER (src = 'delta' AND NOT vault) AS net_delta,
               sum(delta) FILTER (src = 'delta' AND vault) AS net_vault_delta, sum(delta) FILTER (src = 'leg_vault') AS net_leg_vault,
               count(*) FILTER (src = 'leg') AS n_leg, count(*) FILTER (src = 'delta' AND NOT vault) AS n_delta,
               count(*) FILTER (src = 'delta' AND vault) AS n_vault_delta, count(*) FILTER (src = 'leg_vault') AS n_leg_vault
        FROM ledger GROUP BY 1, 2, 3""")
    con.execute("""CREATE OR REPLACE TEMP TABLE mb AS
        SELECT mint, slot, tx_index, sum(CASE WHEN kind = 'mint_to' THEN amount ELSE 0 END) AS minted, sum(CASE WHEN kind = 'burn' THEN amount ELSE 0 END) AS burned,
               count(*) FILTER (kind IN ('transfer', 'transfer_checked') AND NOT in_trade) AS n_transfer,
               count(*) FILTER (kind IN ('transfer', 'transfer_checked') AND in_trade) AS n_in_trade,
               count(*) FILTER (kind = 'close_account') AS n_close, bool_or(owner_unresolved) AS unresolved
        FROM tt GROUP BY 1, 2, 3""")
    con.execute("""CREATE OR REPLACE TEMP TABLE txres AS
        WITH keys AS (SELECT mint, slot, tx_index FROM lg UNION SELECT mint, slot, tx_index FROM mb)
        SELECT k.mint, k.slot, k.tx_index, k.slot = w.create_slot AS create_tx,
               COALESCE(lg.net, 0) - (COALESCE(mb.minted, 0) - COALESCE(mb.burned, 0)) AS residual,
               COALESCE(lg.net, 0) AS net, COALESCE(mb.minted, 0) AS minted, COALESCE(mb.burned, 0) AS burned,
               lg.net_leg, lg.net_delta, lg.net_vault_delta, lg.net_leg_vault,
               COALESCE(lg.n_leg, 0) AS n_leg, COALESCE(lg.n_delta, 0) AS n_delta, COALESCE(lg.n_vault_delta, 0) AS n_vault_delta, COALESCE(lg.n_leg_vault, 0) AS n_leg_vault,
               COALESCE(mb.n_transfer, 0) AS n_transfer, COALESCE(mb.n_in_trade, 0) AS n_in_trade, COALESCE(mb.n_close, 0) AS n_close, COALESCE(mb.unresolved, false) AS unresolved,
               CASE WHEN cv.mint IS NOT NULL AND pv.mint IS NOT NULL THEN 'curve+pool' WHEN cv.mint IS NOT NULL THEN 'curve' WHEN pv.mint IS NOT NULL THEN 'pool' ELSE 'none' END AS venue,
               COALESCE(cv.variants, []) AS variants, COALESCE(pv.pool_kinds, []) AS pool_kinds,
               f.mint IS NOT NULL AS mint_fails
        FROM keys k JOIN wmints w USING (mint)
        LEFT JOIN lg USING (mint, slot, tx_index) LEFT JOIN mb USING (mint, slot, tx_index)
        LEFT JOIN cv USING (mint, slot, tx_index) LEFT JOIN pv USING (mint, slot, tx_index)
        LEFT JOIN (SELECT DISTINCT mint FROM cons WHERE NOT ok) f USING (mint)""")
    con.execute("""CREATE OR REPLACE TEMP TABLE pat AS
        SELECT mint_fails, venue, variants, pool_kinds, create_tx,
               n_leg > 0 AS has_leg, n_delta > 0 AS has_delta, n_vault_delta > 0 AS has_vault_delta, n_leg_vault > 0 AS has_leg_vault,
               n_transfer > 0 AS has_transfer, n_in_trade > 0 AS has_in_trade, n_close > 0 AS has_close, unresolved, minted > 0 AS has_mint, burned > 0 AS has_burn,
               count(*) AS txs, count(*) FILTER (residual <> 0) AS txs_nonzero, sum(residual) AS residual, sum(abs(residual)) AS abs_residual,
               count(DISTINCT mint) AS mints, count(DISTINCT mint) FILTER (residual <> 0) AS mints_nonzero
        FROM txres GROUP BY ALL""")
    # identity: a mint's summed residual = its last-day diff
    ident = con.execute("""
        SELECT count(*), count(*) FILTER (abs(r.s - c.diff) <= 1)
        FROM (SELECT mint, sum(residual) AS s FROM txres GROUP BY 1) r
        JOIN (SELECT mint, arg_max(diff, day) AS diff FROM cons GROUP BY 1) c USING (mint)""").fetchone()
    return ident


def pct(x):
    return "–" if x is None else f"{100 * x:.1f} %"


def render(con, info):
    tot = con.execute("SELECT count(*), count(*) FILTER (residual <> 0), count(DISTINCT mint), count(DISTINCT mint) FILTER (mint_fails), sum(abs(residual)) FILTER (mint_fails), sum(abs(residual)) FILTER (NOT mint_fails) FROM txres").fetchone()
    L = [f"## Check 4.1 diagnosis — window [{info['lo']}, {info['hi']}), {info['mints']:,} mints, conservation {info['cons'][1]:,} / {info['cons'][0]:,} mint-days ({pct(info['cons'][1] / max(info['cons'][0], 1))})\n",
         f"Transactions of the window's mints {tot[0]:,} (ledger or token_transfers rows), with a non-zero residual {tot[1]:,}; mints {tot[2]:,}, failing 4.1 on some day {tot[3]:,}. "
         f"Σ|residual| over the failing mints {tot[4] or 0:,}, over the conserving mints {tot[5] or 0:,}. "
         f"Identity Σ residual(tx) = last-day diff holds for {info['identity'][1]:,} of {info['identity'][0]:,} mints.\n",
         "Residual = Σ ledger deltas in the tx − (minted − burned in the tx). A pattern is the transaction's venue, its curve variants, the ledger sources it has "
         "(leg = store leg on the owner side, delta = kept non-vault row, vault_delta = kept vault row, leg_vault = the leg's vault side added because no vault row was kept) "
         "and the token_transfers kinds it has. Patterns are ranked by Σ|residual| over the failing mints.\n",
         "### Patterns with a residual, failing mints\n",
         "| venue | curve variants | pool kinds | create | leg | delta | vault_delta | leg_vault | transfer | in_trade | close | unresolved | mint | burn | txs | ≠ 0 | mints ≠ 0 | Σ residual | Σ abs | share of Σ abs |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    rows = con.execute("""SELECT venue, variants, pool_kinds, create_tx, has_leg, has_delta, has_vault_delta, has_leg_vault, has_transfer, has_in_trade, has_close, unresolved, has_mint, has_burn,
                                 txs, txs_nonzero, mints_nonzero, residual, abs_residual
                          FROM pat WHERE mint_fails AND txs_nonzero > 0 ORDER BY abs_residual DESC LIMIT 25""").fetchall()
    den = tot[4] or 1
    yn = lambda b: "y" if b else "·"
    for r in rows:
        L.append(f"| {r[0]} | {','.join(r[1]) or '–'} | {','.join(r[2]) or '–'} | {yn(r[3])} | {yn(r[4])} | {yn(r[5])} | {yn(r[6])} | {yn(r[7])} | {yn(r[8])} | {yn(r[9])} | {yn(r[10])} | {yn(r[11])} | {yn(r[12])} | {yn(r[13])} | "
                 f"{r[14]:,} | {r[15]:,} | {r[16]:,} | {r[17]:,} | {r[18]:,} | {pct(r[18] / den)} |")
    L += ["\n### The same patterns in conserving mints (a pattern with residuals there too is a definition issue, not a data gap)\n",
          "| venue | curve variants | create | leg | delta | vault_delta | leg_vault | transfer | in_trade | mint | txs | ≠ 0 | Σ residual |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in con.execute("""SELECT venue, variants, create_tx, has_leg, has_delta, has_vault_delta, has_leg_vault, has_transfer, has_in_trade, has_mint, txs, txs_nonzero, residual
                            FROM pat WHERE NOT mint_fails AND txs_nonzero > 0 ORDER BY abs_residual DESC LIMIT 12""").fetchall():
        L.append(f"| {r[0]} | {','.join(r[1]) or '–'} | {yn(r[2])} | {yn(r[3])} | {yn(r[4])} | {yn(r[5])} | {yn(r[6])} | {yn(r[7])} | {yn(r[8])} | {yn(r[9])} | {r[10]:,} | {r[11]:,} | {r[12]:,} |")
    L += ["\n### Largest residuals (failing mints)\n", "| mint | slot | tx_index | venue | variants | residual | net leg | net delta | net vault delta | net leg_vault | minted | burned | transfers | in_trade |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    ex = con.execute("""SELECT mint, slot, tx_index, venue, variants, residual, net_leg, net_delta, net_vault_delta, net_leg_vault, minted, burned, n_transfer, n_in_trade
                        FROM txres WHERE mint_fails AND residual <> 0 ORDER BY abs(residual) DESC LIMIT 15""").fetchall()
    f = lambda v: "–" if v is None else f"{v:,}"   # NULL-safe: a close_account row has no amount, a boost row no token_amount
    for r in ex:
        L.append(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {','.join(r[4]) or '–'} | {f(r[5])} | {f(r[6])} | {f(r[7])} | {f(r[8])} | {f(r[9])} | {f(r[10])} | {f(r[11])} | {r[12]} | {r[13]} |")
    L.append("\n### Rows of the first three examples\n")
    for r in ex[:3]:
        L.append(f"**{r[0]} slot {r[1]} tx {r[2]}** (residual {f(r[5])})\n")
        L.append("| source | owner | delta | vault |")
        L.append("|---|---|---|---|")
        for x in con.execute("SELECT src, owner, delta, vault FROM ledger WHERE mint = ? AND slot = ? AND tx_index = ? ORDER BY src, owner", [r[0], r[1], r[2]]).fetchall():
            L.append(f"| {x[0]} | {x[1]} | {f(x[2])} | {x[3]} |")
        L.append("\n| kind | ix_path | source owner | dest owner | amount | in_trade | unresolved |")
        L.append("|---|---|---|---|---|---|---|")
        for x in con.execute("SELECT kind, ix_path, source_owner, dest_owner, amount, in_trade, owner_unresolved FROM tt WHERE mint = ? AND slot = ? AND tx_index = ? ORDER BY ix_path", [r[0], r[1], r[2]]).fetchall():
            L.append(f"| {x[0]} | {x[1]} | {x[2]} | {x[3]} | {f(x[4])} | {x[5]} | {x[6]} |")
        L.append("\n| store | variant / kind | trader | is_buy / token_buy | t / token_amount |")
        L.append("|---|---|---|---|---|")
        for x in con.execute("SELECT 'curve', variant, trader, is_buy, t FROM curve WHERE mint = ? AND slot = ? AND tx_index = ? ORDER BY trader", [r[0], r[1], r[2]]).fetchall():
            L.append(f"| {x[0]} | {x[1]} | {x[2]} | {x[3]} | {f(x[4])} |")
        for x in con.execute("SELECT 'pool', p.kind, p.trader, p.token_buy, p.token_amount FROM pool p JOIN pools ps USING (pool) WHERE ps.mint = ? AND p.slot = ? AND p.tx_index = ? ORDER BY p.trader", [r[0], r[1], r[2]]).fetchall():
            L.append(f"| {x[0]} | {x[1]} | {x[2]} | {x[3]} | {f(x[4])} |")
        L.append("")
    L.append(f"Run: commit {info['commit']}, {info['seconds']} s.")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", required=True)
    ap.add_argument("--from-slot", type=int)
    ap.add_argument("--to-slot", type=int)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    t0 = time.time()
    lo, hi = T.chunk_bounds(a.chunks)
    lo = a.from_slot if a.from_slot is not None else lo
    hi = a.to_slot if a.to_slot is not None else hi
    store = os.environ.get("LAB_STORE", STORE)
    con = connect(store, memory=os.environ.get("LAB_WALLETS_MEMORY", "1500MB"), threads=int(os.environ.get("LAB_WALLETS_THREADS", "2")))
    clock = Clock.from_store(con)
    os.makedirs(a.out, exist_ok=True)
    T.setup(con, a.chunks, lo, hi, clock)
    info = {"lo": lo, "hi": hi, "store": store, "chunks": a.chunks, "commit": git_commit(), "mints": con.execute("SELECT count(*) FROM wmints").fetchone()[0]}
    info["cons"] = T.conservation(con)
    T.progress(f"conservation: {info['cons']}")
    info["identity"] = diagnose(con, lo, hi)
    T.progress(f"diagnosis: identity {info['identity']}")
    con.execute(f"COPY (SELECT * FROM txres WHERE residual <> 0 ORDER BY mint, slot, tx_index) TO '{a.out}/txres.parquet' (FORMAT PARQUET)")
    con.execute(f"COPY pat TO '{a.out}/patterns.parquet' (FORMAT PARQUET)")
    info["seconds"] = round(time.time() - t0, 1)
    with open(os.path.join(a.out, "diag.md"), "w") as f:
        f.write(render(con, info) + "\n")
    with open(os.path.join(a.out, "run-diag.json"), "w") as f:
        json.dump(info, f, indent=1, default=str)
    T.progress(f"written {a.out} ({info['seconds']} s)")
    print(json.dumps(info, default=str), file=sys.stderr)


if __name__ == "__main__":
    main()
