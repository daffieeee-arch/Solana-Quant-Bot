"""Token transfers and wallet funding (spec-token-transfers-2026-10-15.md v3, sha256 611a6327...): what the store
does not see. Inputs: the transfer extraction chunks (token_deltas, token_transfers, sol_funding; --chunks root with
*/_manifest.json) and the store (LAB_STORE). Everything is restricted to mints created inside the window, so the
owner ledger has the mint's full history (minted at the create, every leg, every transfer).

Ledger: per (mint, owner) the token movements are the store legs (curve: trader +/- t; pool: trader +/- token_amount,
plus the trader's own non-trade transfers, burns and mint_to in the same transaction) plus the token_deltas rows the
extractor kept (those a store leg does not explain, vaults included); a vault side for every leg and every pool boost
(boost_buy_and_burn: the pool's vault burns the base) whose vault row the extractor dropped. Vaults are named by the
ledger itself: the mint's bonding-curve PDA (wallets/keys.py) and its pools; any other program-derived owner (a bot's
PDA, a PDA-held trader account) is a holder. Holdings at a slot are the sum up to and including it. Insiders of a mint (O7 definition): the creator and every buyer in the
first 10 s after the create.

Q1 (--triggers parquet: mint, t[, ins_share]): share_c = insiders' holdings / circulating (all non-vault holdings)
at t, undefined (reason) when circ <= 0, H_ins > circ, H_ins < 0 or the insider set is unknown; the legs-only share
next to it (section 4.2: must reproduce the trigger file's ins_share where defined); for triggers whose legs-only
share is > 1, how much of the non-insider pool selling in the hour after t is explained by transfers from an
insider within two hops (section 1, bar b).
Q2 (sol_funding, optional --labels parquet wallet,label or --money dir): wallet births (first SOL into a fresh
account), hubs (>= 50 births on one UTC day or >= 200 in 7 days), the funder-link share per label with hubs
excluded (the bar) and included (comparison), the hub table with its label mix.
Q3: per pool created in the window, the transfer-fed share of the non-insider selling in its first hour.
Checks: conservation per mint-day (all holdings incl. vaults = minted - burned) and the old-share reproduction.

  LAB_STORE=<store> python -m wallets.transfers --chunks <root> [--from-slot A --to-slot B] [--triggers P] [--labels P | --money DIR] --out DIR
"""

import argparse
import glob
import json
import os
import sys
import time

import pyarrow as pa

from . import keys
from .common import EPOCH_SLOTS, SOL, STORE, Clock, connect, git_commit

INSIDER_WINDOW_MS = 10_000
HUB_DAY, HUB_WEEK = 50, 200
Q2_MIN_LAMPORTS = 5_000_000  # 0.005 SOL (the extraction keeps >= 0.001)
Q2_SIBLINGS = 5
Q2_WINDOW_DAYS = 7
HOUR_MS = 3_600_000
LABELS = ("creator", "sniper", "bundler", "farmer", "bot", "whale", "retail")


def chunk_files(root, table, lo, hi):
    out, shas = [], set()
    for m in sorted(glob.glob(os.path.join(root, "*", "_manifest.json"))):
        with open(m) as f:
            man = json.load(f)
        if man.get("status") == "complete" and man["slot_start"] < hi and lo < man["slot_end_exclusive"]:
            out += sorted(glob.glob(os.path.join(os.path.dirname(m), table, "*.parquet")))
            shas.add(man.get("extractor_sha256") or man.get("extractor_commit") or "?")
    return out, shas


def chunk_bounds(root):
    lo, hi = None, None
    for m in sorted(glob.glob(os.path.join(root, "*", "_manifest.json"))):
        with open(m) as f:
            man = json.load(f)
        if man.get("status") == "complete":
            lo = man["slot_start"] if lo is None else min(lo, man["slot_start"])
            hi = man["slot_end_exclusive"] if hi is None else max(hi, man["slot_end_exclusive"])
    return lo, hi


def _lit(files):
    return "[" + ",".join("'" + f.replace("'", "''") + "'" for f in files) + "]"


_T0 = [time.time()]


def progress(msg):
    """One flushed stderr line per step with the seconds since the previous line (the NAS log must not stay empty)."""
    now = time.time()
    dt, _T0[0] = now - _T0[0], now
    print(f"{time.strftime('%H:%M:%S')} {msg} ({dt:,.1f}s)", file=sys.stderr, flush=True)


def _count(con, table):
    return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def setup(con, root, lo, hi, clock):
    """Views td / tt / sf over the chunks, the window's mints (created inside it), insiders, the owner ledger and the
    UTC days. Returns the extractor shas seen."""
    shas = set()
    for table, view in (("token_deltas", "td"), ("token_transfers", "tt"), ("sol_funding", "sf")):
        files, s = chunk_files(root, table, lo, hi)
        shas |= s
        if not files:
            raise SystemExit(f"no {table} under {root} for [{lo}, {hi})")
        con.execute(f"CREATE OR REPLACE TEMP VIEW {view}_raw AS SELECT * FROM read_parquet({_lit(files)}, union_by_name = true) WHERE slot >= {lo} AND slot < {hi}")
        progress(f"setup: {len(files)} {table} files for [{lo}, {hi})")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE days AS
        SELECT slot, epoch_ms(block_time::BIGINT * 1000)::DATE AS day FROM blocks WHERE slot >= {lo} AND slot < {hi} AND block_time IS NOT NULL ORDER BY slot""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE wmints AS
        SELECT m.mint, m.create_slot, m.creator FROM mints m WHERE m.create_slot >= {lo} AND m.create_slot < {hi}""")
    # the UTC day of every slot in the window (the last block day at or before it): an equi-join target, where an
    # ASOF join on the slot alone was planned as a nested-loop join
    con.execute(f"""CREATE OR REPLACE TEMP TABLE slot_day AS
        SELECT slot, last_value(day IGNORE NULLS) OVER (ORDER BY slot ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS day
        FROM (SELECT r.range AS slot, d.day FROM range({lo}, {hi}) r LEFT JOIN days d ON d.slot = r.range)""")
    progress(f"setup: {_count(con, 'days')} day slots, {_count(con, 'wmints')} mints created in the window")
    # The vault owners of every window mint: the bonding-curve PDA (it owns the curve's token vault) and its pools (a
    # pool owns its vaults). The extractor's is_vault marks every off-curve owner, a bot's PDA or a PDA-held trader
    # account included (check 4.1 diagnosis of 2026-10-15: the sniper ARu4n5mF… and the sellers it feeds are PDAs),
    # so the ledger names the vaults itself and treats every other owner as a holder.
    mints = [r[0] for r in con.execute("SELECT mint FROM wmints ORDER BY mint").fetchall()]
    con.register("curve_pdas", pa.table({"mint": pa.array(mints, pa.string()), "owner": pa.array([keys.curve_pda(m) for m in mints], pa.string())}))
    con.execute("""CREATE OR REPLACE TEMP TABLE wvaults AS
        SELECT DISTINCT mint, owner FROM (SELECT mint, owner FROM curve_pdas
                                          UNION ALL SELECT ps.mint, ps.pool FROM pools ps SEMI JOIN wmints w ON w.mint = ps.mint)""")
    # td / tt materialised once, for the window's mints only (every later use is on those mints; a view would rescan
    # the chunks at each use: td four times, tt three times on an epoch). sf (SOL funding) is not per mint: a view.
    con.execute("""CREATE OR REPLACE TEMP TABLE td AS
        SELECT t.slot, t.tx_index, t.mint, t.owner, t.token_account, t.delta_raw::HUGEINT AS delta, v.owner IS NOT NULL AS vault, t.is_vault AS is_pda,
               t.has_pump_event_same_mint, t.fee_payer
        FROM td_raw t SEMI JOIN wmints w ON w.mint = t.mint LEFT JOIN wvaults v ON v.mint = t.mint AND v.owner = t.owner WHERE NOT t.failed""")
    progress(f"setup: td {_count(con, 'td')} token_deltas rows of the window's mints")
    con.execute("""CREATE OR REPLACE TEMP TABLE tt AS
        SELECT slot, tx_index, ix_path, kind, mint, source_owner, dest_owner, source_account, dest_account, amount_raw::HUGEINT AS amount, in_trade, owner_unresolved
        FROM tt_raw SEMI JOIN wmints USING (mint) WHERE NOT failed""")
    progress(f"setup: tt {_count(con, 'tt')} token_transfers rows of the window's mints")
    con.execute("CREATE OR REPLACE TEMP VIEW sf AS SELECT slot, tx_index, from_account AS funder, to_account AS wallet, lamports::BIGINT AS lamports, kind FROM sf_raw WHERE NOT failed")
    # ms per slot of each mint's create epoch -> the 10 s insider window in slots
    per_epoch = clock.per_epoch or {}
    case = "CASE create_slot // {e} ".format(e=EPOCH_SLOTS) + " ".join(f"WHEN {ep} THEN {ms!r}" for ep, ms in sorted(per_epoch.items())) + f" ELSE {clock.ms_per_slot(lo)!r} END" if per_epoch else repr(clock.ms_per_slot(lo))
    con.execute(f"CREATE OR REPLACE TEMP TABLE wmints2 AS SELECT *, greatest(1, round({INSIDER_WINDOW_MS} / ({case})))::BIGINT AS window_slots FROM wmints")
    # store legs of the window's mints, owner side and vault side (vault_owner: the owner of the vault's token account)
    con.execute(f"""CREATE OR REPLACE TEMP TABLE legs AS
        SELECT c.mint, c.slot, c.tx_index, c.trader AS owner, CASE WHEN c.is_buy THEN c.t ELSE -c.t END::HUGEINT AS delta, c.is_buy AS buy,
               c.q::HUGEINT AS sol, 'curve' AS venue, c.mint || ':curve' AS key, v.owner AS vault_owner
        FROM curve c JOIN curve_pdas v ON v.mint = c.mint WHERE c.slot >= {lo} AND c.slot < {hi}
        UNION ALL
        SELECT ps.mint, p.slot, p.tx_index, p.trader, CASE WHEN p.token_buy THEN p.token_amount ELSE -p.token_amount END::HUGEINT, p.token_buy,
               p.sol_amount::HUGEINT, 'pool', p.pool, p.pool
        FROM pool p JOIN pools ps USING (pool) SEMI JOIN wmints w ON w.mint = ps.mint
        WHERE p.slot >= {lo} AND p.slot < {hi} AND p.kind IN ('buy', 'sell')""")
    # pool boosts (boost_buy_and_burn): the pool buys base with the boost vault's quote and burns it from its own
    # vault, no user side; the store has the burned base in `base`. The extractor's legs do not cover the boost, yet
    # the diagnosis of 2026-10-15 found no delta row for it (53 % of the 4.1 deficit on 1040), so the vault side is
    # added here unless a vault row was kept.
    con.execute(f"""CREATE OR REPLACE TEMP TABLE boosts AS
        SELECT ps.mint, p.slot, p.tx_index, p.pool AS key, p.base::HUGEINT AS burned
        FROM pool p JOIN pools ps USING (pool) SEMI JOIN wmints w ON w.mint = ps.mint
        WHERE p.slot >= {lo} AND p.slot < {hi} AND p.kind = 'boost' AND p.base IS NOT NULL""")
    # A non-trade transfer that continues an in-trade transfer through an intermediate token account — a router's
    # account that receives the pool's output and forwards it to the trader, or a temporary account (owner
    # unresolved, closed in the same transaction) that the trader fills and that then feeds the vault — is the same
    # trade's token movement: the leg already carries it, so it is not a flow (job 79 diagnosis of 2026-10-15: the
    # trader's net leg was exactly twice the amount on such routes, 82 % of the remaining 4.1 residual). Matched on
    # the shared token account, or on the shared owner when the chunk has no account columns, and only through an
    # intermediary that is not itself a leg trader of the transaction (a bot that buys and forwards from its own
    # account, or a seller fed by a PDA, keeps its flows). Four equality joins (an OR would plan a nested loop).
    con.execute("""CREATE OR REPLACE TEMP TABLE hops AS
        WITH x AS (SELECT mint, slot, tx_index, ix_path, source_account, dest_account, source_owner, dest_owner FROM tt
                   WHERE kind IN ('transfer', 'transfer_checked') AND NOT in_trade),
             y AS (SELECT mint, slot, tx_index, source_account, dest_account, source_owner, dest_owner FROM tt
                   WHERE kind IN ('transfer', 'transfer_checked') AND in_trade),
             h AS (
               SELECT x.mint, x.slot, x.tx_index, x.ix_path, y.dest_owner AS via FROM x JOIN y
                 ON y.mint = x.mint AND y.slot = x.slot AND y.tx_index = x.tx_index AND y.dest_account = x.source_account
               UNION ALL SELECT x.mint, x.slot, x.tx_index, x.ix_path, y.source_owner FROM x JOIN y
                 ON y.mint = x.mint AND y.slot = x.slot AND y.tx_index = x.tx_index AND y.source_account = x.dest_account
               UNION ALL SELECT x.mint, x.slot, x.tx_index, x.ix_path, y.dest_owner FROM x JOIN y
                 ON y.mint = x.mint AND y.slot = x.slot AND y.tx_index = x.tx_index AND y.dest_owner = x.source_owner AND x.source_account IS NULL
               UNION ALL SELECT x.mint, x.slot, x.tx_index, x.ix_path, y.source_owner FROM x JOIN y
                 ON y.mint = x.mint AND y.slot = x.slot AND y.tx_index = x.tx_index AND y.source_owner = x.dest_owner AND x.dest_account IS NULL)
        SELECT DISTINCT h.mint, h.slot, h.tx_index, h.ix_path FROM h
        ANTI JOIN (SELECT DISTINCT mint, slot, tx_index, owner FROM legs) t
             ON t.mint = h.mint AND t.slot = h.slot AND t.tx_index = h.tx_index AND t.owner = h.via""")
    # a trader's token movements besides the trade in the same transaction (a bot buys and forwards the tokens to
    # its customers; a seller receives from a PDA and sells at once): non-trade transfers (hops excluded), burns and
    # mint_to per owner
    con.execute("""CREATE OR REPLACE TEMP TABLE flows AS
        SELECT mint, slot, tx_index, owner, sum(amount) AS flow FROM (
            SELECT mint, slot, tx_index, dest_owner AS owner, amount FROM tt ANTI JOIN hops USING (mint, slot, tx_index, ix_path)
                WHERE kind IN ('transfer', 'transfer_checked') AND NOT in_trade AND dest_owner IS NOT NULL
            UNION ALL SELECT mint, slot, tx_index, source_owner, -amount FROM tt ANTI JOIN hops USING (mint, slot, tx_index, ix_path)
                WHERE kind IN ('transfer', 'transfer_checked') AND NOT in_trade AND source_owner IS NOT NULL
            UNION ALL SELECT mint, slot, tx_index, source_owner, -amount FROM tt WHERE kind = 'burn' AND source_owner IS NOT NULL
            UNION ALL SELECT mint, slot, tx_index, dest_owner, amount FROM tt WHERE kind = 'mint_to' AND dest_owner IS NOT NULL) GROUP BY 1, 2, 3, 4""")
    progress(f"setup: legs {_count(con, 'legs')}, boosts {_count(con, 'boosts')}, hops {_count(con, 'hops')}, flows {_count(con, 'flows')}")
    # Insiders: the creator and every buyer in the first 10 s (O7); buyers both as the store's trader and as the
    # chain's token owner (a kept positive non-vault delta in that window), since the two can differ.
    con.execute("""CREATE OR REPLACE TEMP TABLE insiders AS
        SELECT DISTINCT w.mint, x.owner FROM wmints2 w JOIN (
            SELECT mint, owner, slot FROM legs WHERE buy
            UNION ALL SELECT mint, owner, slot FROM td WHERE NOT vault AND delta > 0 AND has_pump_event_same_mint
            UNION ALL SELECT mint, creator, create_slot FROM wmints WHERE creator IS NOT NULL) x
        ON x.mint = w.mint AND x.slot <= w.create_slot + w.window_slots""")
    progress(f"setup: insiders {_count(con, 'insiders')}")
    # the owner ledger: legs (owner side) + kept token_deltas rows (holders); vaults: legs and boosts (vault side) +
    # kept vault rows.
    # The vault side of a leg (or boost) is added only when the extractor dropped the row of *that* vault (spec v2: a
    # vault delta equal to the leg's base is dropped; otherwise the kept row is the transaction's whole net vault
    # delta, mint and leg included, e.g. the create transaction with the dev buy). A kept row of another
    # program-derived owner in the transaction (a bot's PDA feeding the seller) is a holder's row and explains nothing
    # (check 4.1 diagnosis of 2026-10-15: "any vault row kept" skipped the curve's side on such sells).
    # Same rule on the owner side: a kept holder row is the owner's whole net delta in that transaction (the
    # extractor only drops a row equal to the leg's base), so the leg's owner side is added only without a kept row;
    # then the trader's non-trade flows in the transaction are added to it (a bot that buys and forwards the tokens at
    # once has no row at all, its net being 0; the leg alone doubled the tokens), once per trader.
    # The store's trader can differ from the chain's token owner (a PDA-held token account: the kept row then
    # carries the leg's exact amount under the owner), so a leg is also explained by a kept row with its amount —
    # one to one (row_number per (mint, slot, tx, delta) on both sides) and only by kept rows whose owner is not
    # itself a leg trader in that transaction (review C3: a bundle with two exact-out buys of X by B and C, B's
    # tokens on a PDA, must keep C's leg; matching on the amount alone dropped both).
    # "explained by owner OR by amount" as two hash anti-joins: one anti-join with the OR in its condition is planned
    # as a blockwise nested-loop join (legs x kept rows; the NAS run of 2026-10-15 sat in it for 6 h on epoch 1040).
    con.execute("""CREATE OR REPLACE TEMP TABLE kept_other AS
        SELECT k.mint, k.slot, k.tx_index, k.delta, row_number() OVER (PARTITION BY k.mint, k.slot, k.tx_index, k.delta ORDER BY k.owner) AS rn
        FROM (SELECT DISTINCT mint, slot, tx_index, owner, delta FROM td WHERE NOT vault) k
        ANTI JOIN (SELECT DISTINCT mint, slot, tx_index, owner FROM legs) lt
             ON lt.mint = k.mint AND lt.slot = k.slot AND lt.tx_index = k.tx_index AND lt.owner = k.owner""")
    con.execute("""CREATE OR REPLACE TEMP TABLE ledger AS
        SELECT mint, slot, tx_index, owner, delta, false AS vault, 'leg' AS src FROM (
            SELECT l.mint, l.slot, l.tx_index, l.owner, l.delta + CASE WHEN l.rno = 1 THEN COALESCE(f.flow, 0) ELSE 0 END AS delta
            FROM (SELECT *, row_number() OVER (PARTITION BY mint, slot, tx_index, delta ORDER BY owner) AS rn,
                            row_number() OVER (PARTITION BY mint, slot, tx_index, owner ORDER BY delta) AS rno FROM legs) l
            ANTI JOIN (SELECT DISTINCT mint, slot, tx_index, owner FROM td WHERE NOT vault) ko
                 ON ko.mint = l.mint AND ko.slot = l.slot AND ko.tx_index = l.tx_index AND ko.owner = l.owner
            ANTI JOIN kept_other kd
                 ON kd.mint = l.mint AND kd.slot = l.slot AND kd.tx_index = l.tx_index AND kd.delta = l.delta AND kd.rn = l.rn
            LEFT JOIN flows f ON f.mint = l.mint AND f.slot = l.slot AND f.tx_index = l.tx_index AND f.owner = l.owner)
        WHERE delta <> 0
        UNION ALL SELECT mint, slot, tx_index, owner, delta, vault, 'delta' FROM td
        UNION ALL SELECT l.mint, l.slot, l.tx_index, '<vault:' || l.key || '>', -l.delta, true, 'leg_vault' FROM legs l
                  ANTI JOIN (SELECT DISTINCT mint, slot, tx_index, owner FROM td WHERE vault) v
                       ON v.mint = l.mint AND v.slot = l.slot AND v.tx_index = l.tx_index AND v.owner = l.vault_owner
        UNION ALL SELECT b.mint, b.slot, b.tx_index, '<vault:' || b.key || '>', -b.burned, true, 'leg_vault' FROM boosts b
                  ANTI JOIN (SELECT DISTINCT mint, slot, tx_index, owner FROM td WHERE vault) v
                       ON v.mint = b.mint AND v.slot = b.slot AND v.tx_index = b.tx_index AND v.owner = b.key""")
    con.execute("""CREATE OR REPLACE TEMP TABLE ledger_legs AS
        SELECT mint, slot, tx_index, owner, delta FROM legs""")
    progress(f"setup: {_count(con, 'wmints')} mints created in the window, {_count(con, 'legs')} legs, {_count(con, 'ledger')} ledger rows, "
             f"{_count(con, 'insiders')} insiders")
    return shas


def holdings_sql(ledger, t_expr, mint_expr="mint"):
    return f"(SELECT mint, owner, sum(delta) AS h FROM {ledger} WHERE slot <= {t_expr} GROUP BY mint, owner)"


def q1(con, triggers_parquet, clock):
    """share_c per trigger with the undefined reasons, the legs-only share, and bar (b) for the share > 1 triggers."""
    con.execute(f"CREATE OR REPLACE TEMP TABLE trig AS SELECT * EXCLUDE (rn) FROM (SELECT mint, t::BIGINT AS t, row_number() OVER (PARTITION BY mint, t) AS rn, * EXCLUDE (mint, t) FROM read_parquet('{triggers_parquet}')) WHERE rn = 1")
    if "ins_share" not in [c[0] for c in con.execute("SELECT * FROM trig LIMIT 0").description]:
        con.execute("ALTER TABLE trig ADD COLUMN ins_share DOUBLE")
    con.execute("CREATE OR REPLACE TEMP TABLE trig AS SELECT tr.* FROM trig tr SEMI JOIN wmints w ON w.mint = tr.mint")
    con.execute("""CREATE OR REPLACE TEMP TABLE q1 AS
        WITH h AS (SELECT tr.mint, tr.t, l.owner, sum(l.delta) AS h, bool_or(l.vault) AS vault
                   FROM trig tr JOIN ledger l ON l.mint = tr.mint AND l.slot <= tr.t GROUP BY 1, 2, 3),
             hl AS (SELECT tr.mint, tr.t, l.owner, sum(l.delta) AS h FROM trig tr JOIN ledger_legs l ON l.mint = tr.mint AND l.slot <= tr.t GROUP BY 1, 2, 3),
             agg AS (SELECT h.mint, h.t, sum(h.h) FILTER (WHERE NOT vault) AS circ, sum(h.h) FILTER (WHERE NOT vault AND i.owner IS NOT NULL) AS h_ins,
                            count(*) FILTER (WHERE NOT vault AND h.h < 0) AS neg_owners, count(*) FILTER (WHERE NOT vault AND h.h > 0) AS holders
                     FROM h LEFT JOIN insiders i ON i.mint = h.mint AND i.owner = h.owner GROUP BY 1, 2),
             aggl AS (SELECT hl.mint, hl.t, sum(hl.h) AS circ_legs, sum(hl.h) FILTER (WHERE i.owner IS NOT NULL) AS h_ins_legs
                      FROM hl LEFT JOIN insiders i ON i.mint = hl.mint AND i.owner = hl.owner GROUP BY 1, 2),
             ins_n AS (SELECT mint, count(*) AS n_insiders FROM insiders GROUP BY 1)
        SELECT tr.mint, tr.t, tr.ins_share AS ins_share_old, a.circ, a.h_ins, a.neg_owners, a.holders, COALESCE(n.n_insiders, 0) AS n_insiders,
               CASE WHEN COALESCE(n.n_insiders, 0) = 0 THEN 'insiders_unknown' WHEN a.circ IS NULL OR a.circ <= 0 THEN 'circ_le_0'
                    WHEN a.h_ins < 0 THEN 'h_ins_lt_0' WHEN a.h_ins > a.circ THEN 'h_ins_gt_circ' END AS undefined,
               CASE WHEN COALESCE(n.n_insiders, 0) > 0 AND a.circ > 0 AND a.h_ins >= 0 AND a.h_ins <= a.circ THEN a.h_ins::DOUBLE / a.circ END AS share_c,
               CASE WHEN al.circ_legs > 0 THEN al.h_ins_legs::DOUBLE / al.circ_legs END AS share_legs
        FROM trig tr LEFT JOIN agg a USING (mint, t) LEFT JOIN aggl al USING (mint, t) LEFT JOIN ins_n n ON n.mint = tr.mint""")
    progress(f"q1: share_c on {_count(con, 'q1')} triggers ({_count(con, 'trig')} in the window)")
    # bar (b): triggers whose legs-only share is > 1: non-insider selling in the hour after t and its transfer origin
    ms = clock.ms_per_slot(con.execute("SELECT COALESCE(min(t), 0) FROM trig").fetchone()[0])
    hour = int(round(HOUR_MS / ms))
    con.execute(f"""CREATE OR REPLACE TEMP TABLE q1b AS
        WITH tb AS (SELECT mint, t FROM q1 WHERE share_legs > 1),
             sells AS (SELECT tb.mint, tb.t, l.owner, l.slot, l.tx_index, -l.delta AS sold
                       FROM tb JOIN legs l ON l.mint = tb.mint AND l.slot > tb.t AND l.slot <= tb.t + {hour} AND NOT l.buy
                       ANTI JOIN insiders i ON i.mint = tb.mint AND i.owner = l.owner),
             e AS (SELECT mint, slot, tx_index, source_owner AS src, dest_owner AS dst, amount FROM tt
                   WHERE kind IN ('transfer', 'transfer_checked') AND NOT in_trade AND source_owner IS NOT NULL AND dest_owner IS NOT NULL),
             hop1 AS (SELECT e.mint, e.dst AS owner, e.slot, e.amount FROM e JOIN insiders i ON i.mint = e.mint AND i.owner = e.src),
             -- a second hop only from the first hop's receipt on (review C2a: a transfer B -> C before B received from
             -- the insider is not insider tokens)
             hop2 AS (SELECT e.mint, e.dst AS owner, e.slot, e.amount FROM e JOIN (SELECT mint, owner, min(slot) AS first_slot FROM hop1 GROUP BY 1, 2) h
                      ON h.mint = e.mint AND h.owner = e.src AND e.slot >= h.first_slot
                      ANTI JOIN insiders i ON i.mint = e.mint AND i.owner = e.dst),
             -- explained chronologically (review C2b): the explainable amount of a seller is the minimum over its sales
             -- k of (received up to slot_k + sold after k), capped at the total sold; a receipt after a sale cannot
             -- explain it
             sr AS (SELECT s.*, sum(s.sold) OVER (PARTITION BY s.mint, s.t, s.owner) AS tot_sold,
                           sum(s.sold) OVER (PARTITION BY s.mint, s.t, s.owner ORDER BY s.slot, s.tx_index ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cum_sold,
                           (SELECT COALESCE(sum(amount), 0) FROM hop1 h WHERE h.mint = s.mint AND h.owner = s.owner AND h.slot <= s.slot) AS r1,
                           (SELECT COALESCE(sum(amount), 0) FROM hop2 h WHERE h.mint = s.mint AND h.owner = s.owner AND h.slot <= s.slot) AS r2
                    FROM sells s),
             per_seller AS (
                SELECT mint, t, owner, max(tot_sold) AS sold,
                       least(max(tot_sold), min(r1 + tot_sold - cum_sold)) AS expl1, least(max(tot_sold), min(r1 + r2 + tot_sold - cum_sold)) AS expl2
                FROM sr GROUP BY 1, 2, 3)
        SELECT mint, t, count(*) AS sellers, sum(sold) AS sold, sum(expl1) AS from_insider_1hop, sum(expl2) AS from_insider_2hops,
               sum(expl2)::DOUBLE / nullif(sum(sold), 0) AS explained_share
        FROM per_seller GROUP BY 1, 2""")
    progress(f"q1: bar (b) on {_count(con, 'q1b')} triggers with a legs-only share > 1")
    con.execute("CREATE OR REPLACE TEMP TABLE q1 AS SELECT q1.*, b.sellers, b.sold AS sold_1h, b.from_insider_1hop, b.from_insider_2hops, b.explained_share FROM q1 LEFT JOIN q1b b USING (mint, t)")
    return con.execute("SELECT count(*) FROM q1").fetchone()[0]


def q2(con, labels_parquet=None, money_dir=None):
    """Births, hubs, the funder-link share per label (hubs excluded: the bar; included: comparison)."""
    con.execute("""CREATE OR REPLACE TEMP TABLE births AS
        SELECT * EXCLUDE (rn) FROM (
            SELECT s.wallet, s.funder, s.slot, s.tx_index, s.lamports, d.day, row_number() OVER (PARTITION BY s.wallet ORDER BY s.slot, s.tx_index) AS rn
            FROM sf s LEFT JOIN slot_day d USING (slot) WHERE s.kind IN ('transfer', 'create_account')) WHERE rn = 1""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE hubs AS
        WITH per_day AS (SELECT funder, day, count(*) AS n FROM births GROUP BY 1, 2),
             wk AS (SELECT a.funder, a.day, sum(b.n) AS n7 FROM per_day a JOIN per_day b ON b.funder = a.funder AND b.day BETWEEN a.day - INTERVAL 6 DAY AND a.day GROUP BY 1, 2)
        SELECT funder, max(n) AS max_day, max(n7) AS max_week FROM per_day JOIN wk USING (funder, day) GROUP BY 1
        HAVING max(n) >= {HUB_DAY} OR max(n7) >= {HUB_WEEK}""")
    progress(f"q2: {_count(con, 'births')} births, {_count(con, 'hubs')} hubs")
    if labels_parquet:
        con.execute(f"CREATE OR REPLACE TEMP TABLE lab AS SELECT wallet, label FROM read_parquet('{labels_parquet}')")
    elif money_dir:
        from . import money_report as R
        R.load(con, money_dir, materialize=False)
        con.execute("CREATE OR REPLACE TEMP TABLE lab AS SELECT wallet, label FROM labels")
    else:
        con.execute("CREATE OR REPLACE TEMP TABLE lab (wallet VARCHAR, label VARCHAR)")
    con.execute("CREATE OR REPLACE TEMP TABLE bl AS SELECT b.*, COALESCE(l.label, 'onbekend') AS label, h.funder IS NOT NULL AS hub FROM births b LEFT JOIN lab l USING (wallet) LEFT JOIN hubs h USING (funder)")
    win = Q2_WINDOW_DAYS
    con.execute(f"""CREATE OR REPLACE TEMP TABLE q2 AS
        WITH w AS (SELECT * FROM bl WHERE lamports >= {Q2_MIN_LAMPORTS}),
             cnt AS (SELECT funder, label, day, count(*) AS n FROM w GROUP BY 1, 2, 3),
             -- siblings = other wallets of the same funder and label born within the window: counted per (funder,
             -- label, day) and summed, minus the wallet itself (a self-join per funder is quadratic in a hub's births)
             sib AS (SELECT a.wallet, sum(c.n) - 1 AS siblings
                     FROM w a JOIN cnt c ON c.funder = a.funder AND c.label = a.label AND abs(c.day - a.day) <= {win} GROUP BY 1)
        SELECT w.label, w.hub, count(*) AS wallets, count(*) FILTER (WHERE COALESCE(s.siblings, 0) >= {Q2_SIBLINGS}) AS linked
        FROM w LEFT JOIN sib s USING (wallet) GROUP BY 1, 2""")
    con.execute("""CREATE OR REPLACE TEMP TABLE hub_table AS
        SELECT h.funder, h.max_day, h.max_week, count(*) AS births, median(b.lamports) / 1e9 AS median_sol, sum(b.lamports) / 1e9 AS total_sol,
               """ + ", ".join(f"avg((b.label = '{l}')::INT) AS {l}" for l in LABELS) + """, avg((b.label = 'onbekend')::INT) AS onbekend,
               CASE WHEN """ + " OR ".join(f"avg((b.label = '{l}')::INT) >= 0.8" for l in LABELS if l != "retail") + """ THEN 'farm-achtig'
                    WHEN count(DISTINCT b.label) >= 3 THEN 'beurs-achtig' ELSE 'anders' END AS kind
        FROM hubs h JOIN bl b USING (funder) GROUP BY 1, 2, 3 ORDER BY births DESC""")
    return con.execute("SELECT count(*), count(DISTINCT funder) FROM births").fetchone(), con.execute("SELECT count(*) FROM hubs").fetchone()[0]


def q3(con, clock, lo, hi):
    """Per pool created in the window: the transfer-fed share of the non-insider selling in its first hour."""
    ms = clock.ms_per_slot(lo)
    hour = int(round(HOUR_MS / ms))
    con.execute(f"""CREATE OR REPLACE TEMP TABLE q3 AS
        WITH np AS (SELECT ps.pool, ps.mint, ps.create_slot FROM pools ps SEMI JOIN wmints w ON w.mint = ps.mint WHERE ps.create_slot >= {lo} AND ps.create_slot < {hi}),
             first AS (SELECT l.key AS pool, min(l.slot) AS s0 FROM legs l SEMI JOIN np ON np.pool = l.key GROUP BY 1),
             sells AS (SELECT np.pool, np.mint, l.owner, l.slot, -l.delta AS sold, l.sol
                       FROM np JOIN first f USING (pool) JOIN legs l ON l.key = np.pool AND NOT l.buy AND l.slot >= f.s0 AND l.slot <= f.s0 + {hour}
                       ANTI JOIN insiders i ON i.mint = np.mint AND i.owner = l.owner),
             recv AS (SELECT mint, dest_owner AS owner, slot, amount FROM tt WHERE kind IN ('transfer', 'transfer_checked') AND NOT in_trade AND dest_owner IS NOT NULL),
             per_seller AS (SELECT s.pool, s.mint, s.owner, sum(s.sold) AS sold, sum(s.sol) AS sol, min(s.slot) AS first_sell,
                                   (SELECT COALESCE(sum(amount), 0) FROM recv r WHERE r.mint = s.mint AND r.owner = s.owner AND r.slot <= min(s.slot)) AS received,
                                   (SELECT max(slot) FROM recv r WHERE r.mint = s.mint AND r.owner = s.owner AND r.slot <= min(s.slot)) AS last_recv
                            FROM sells s GROUP BY 1, 2, 3)
        SELECT np.pool, np.mint, count(ps.owner) AS sellers, count(*) FILTER (WHERE ps.received > 0) AS sellers_fed, COALESCE(sum(ps.sold), 0) AS sold,
               COALESCE(sum(least(ps.sold, ps.received)), 0) AS sold_fed, sum(least(ps.sold, ps.received))::DOUBLE / nullif(sum(ps.sold), 0) AS fed_share,
               COALESCE(sum(ps.sol), 0) / 1e9 AS sol_sold,
               median((ps.first_sell - ps.last_recv) * {ms} / 60000.0) FILTER (WHERE ps.received > 0) AS median_min_transfer_to_sell
        FROM np LEFT JOIN per_seller ps USING (pool, mint) GROUP BY 1, 2""")
    progress(f"q3: {_count(con, 'q3')} pools")
    return _count(con, "q3")


def conservation(con):
    """Per mint-day: all holdings (vaults included) against minted - burned from token_transfers."""
    # Holdings at the end of every UTC day from the day of a mint's first ledger row on: per (mint, day) sums
    # through slot_day, then a cumulative window over the day frame (a join on slot <= the day's last slot was
    # planned as a nested-loop join). Minted / burned the same way from the day of the first mint_to / burn.
    days = con.execute("SELECT list(DISTINCT day ORDER BY day) FROM days").fetchone()[0] or []
    dl = "[" + ", ".join(f"DATE '{d}'" for d in days) + "]::DATE[]"  # inlined: a scalar subquery here plans as a cross product
    con.execute(f"""CREATE OR REPLACE TEMP TABLE cons AS
        WITH lrow AS (SELECT l.mint, sd.day, sum(l.delta) AS s FROM ledger l JOIN slot_day sd USING (slot) GROUP BY 1, 2),
             trow AS (SELECT t.mint, sd.day, sum(CASE WHEN t.kind = 'mint_to' THEN t.amount ELSE 0 END) AS minted,
                             sum(CASE WHEN t.kind = 'burn' THEN t.amount ELSE 0 END) AS burned
                      FROM tt t JOIN slot_day sd USING (slot) WHERE t.kind IN ('mint_to', 'burn') GROUP BY 1, 2),
             lframe AS (SELECT mint, unnest(list_filter({dl}, x -> x >= d0)) AS day FROM (SELECT mint, min(day) AS d0 FROM lrow GROUP BY 1)),
             tframe AS (SELECT mint, unnest(list_filter({dl}, x -> x >= d0)) AS day FROM (SELECT mint, min(day) AS d0 FROM trow GROUP BY 1)),
             held AS (SELECT f.mint, f.day, sum(COALESCE(l.s, 0)) OVER (PARTITION BY f.mint ORDER BY f.day ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS held
                      FROM lframe f LEFT JOIN lrow l USING (mint, day)),
             mb AS (SELECT f.mint, f.day, sum(COALESCE(t.minted, 0)) OVER (PARTITION BY f.mint ORDER BY f.day ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS minted,
                           sum(COALESCE(t.burned, 0)) OVER (PARTITION BY f.mint ORDER BY f.day ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS burned
                    FROM tframe f LEFT JOIN trow t USING (mint, day)),
             unres AS (SELECT t.mint, count(*) AS n FROM tt t WHERE t.owner_unresolved GROUP BY 1)
        SELECT h.mint, h.day, h.held, COALESCE(mb.minted, 0) AS minted, COALESCE(mb.burned, 0) AS burned, h.held - (COALESCE(mb.minted, 0) - COALESCE(mb.burned, 0)) AS diff,
               COALESCE(u.n, 0) AS unresolved, abs(h.held - (COALESCE(mb.minted, 0) - COALESCE(mb.burned, 0))) <= greatest(1, COALESCE(mb.minted, 0) / 1000000) AS ok,
               m.mayhem, m.cashback, m.holder_reward, v.variants
        FROM held h LEFT JOIN mb USING (mint, day) LEFT JOIN unres u ON u.mint = h.mint LEFT JOIN mints m ON m.mint = h.mint
        LEFT JOIN (SELECT mint, list_sort(list_distinct(list(COALESCE(variant, '?')))) AS variants FROM curve GROUP BY 1) v ON v.mint = h.mint""")
    return con.execute("SELECT count(*), count(*) FILTER (WHERE abs(diff) <= greatest(1, minted / 1000000)), count(*) FILTER (WHERE unresolved > 0) FROM cons").fetchone()


def pct(x):
    return "–" if x is None else f"{100 * x:.1f} %"


def render(con, info, have_triggers, have_labels):
    L = [f"## Token transfers and wallet funding (spec v3, sha256 611a6327…) — window [{info['lo']}, {info['hi']})\n",
         f"Mints created in the window: {info['mints']:,}; extractor sha(s): {', '.join(sorted(info['extractor_shas']))}. Restricted to those mints so every ledger is complete from the create.\n",
         "### Check 4.1: conservation per mint-day (all holdings incl. vaults = minted − burned)\n",
         f"Mint-days {info['cons'][0]:,}, within tolerance (1 unit or 1e-6 of minted) {info['cons'][1]:,} ({pct(info['cons'][1] / max(info['cons'][0], 1))}), with unresolved owners {info['cons'][2]:,}. Bar: ≥ 99.9 %.\n"]
    worst = con.execute("SELECT mint, day, held, minted, burned, diff FROM cons ORDER BY abs(diff) DESC LIMIT 5").fetchall()
    if worst:
        L += ["| mint | day | held | minted | burned | diff |", "|---|---|---|---|---|---|"] + [f"| {m} | {d} | {h:,} | {mi:,} | {b:,} | {df:,} |" for m, d, h, mi, b, df in worst] + [""]
    L += ["Per mint variant (a failure concentrated in one variant points at legs the store and the extractor treat differently):\n",
          "| mayhem | cashback | holder_reward | curve variants | mint-days | ok | share |", "|---|---|---|---|---|---|---|"]
    for mh, cb, hr, va, n, ok in con.execute("SELECT mayhem, cashback, holder_reward, variants, count(*), count(*) FILTER (WHERE ok) FROM cons GROUP BY ALL ORDER BY count(*) DESC LIMIT 12").fetchall():
        L.append(f"| {mh} | {cb} | {hr} | {','.join(va) if va else '–'} | {n:,} | {ok:,} | {pct(ok / n)} |")
    L.append("")
    if have_triggers:
        r = con.execute("""SELECT count(*), count(*) FILTER (WHERE undefined IS NULL), count(*) FILTER (WHERE undefined = 'insiders_unknown'), count(*) FILTER (WHERE undefined = 'circ_le_0'),
                                  count(*) FILTER (WHERE undefined = 'h_ins_gt_circ'), count(*) FILTER (WHERE undefined = 'h_ins_lt_0'),
                                  quantile_cont(share_c, 0.8), count(*) FILTER (WHERE share_c > 0.9), count(*) FILTER (WHERE share_legs > 1),
                                  count(*) FILTER (WHERE ins_share_old IS NOT NULL AND share_legs IS NOT NULL), count(*) FILTER (WHERE ins_share_old IS NOT NULL AND share_legs IS NOT NULL AND abs(ins_share_old - share_legs) <= 1e-9)
                           FROM q1""").fetchone()
        n, defined, unk, c0, gt, lt, q80, veto, legs_gt1, cmp_n, cmp_ok = r
        L += ["### Q1: corrected insider share per trigger\n",
              f"Triggers {n:,} (mint created in the window). share_c defined {defined:,} ({pct(defined / max(n, 1))}); undefined: insiders_unknown {unk:,}, circ_le_0 {c0:,}, h_ins_gt_circ {gt:,}, h_ins_lt_0 {lt:,}. Bar (a): undefined < 1 % except insiders_unknown.",
              f"Q80 of share_c {('–' if q80 is None else f'{q80:.3f}')} (the pre-registered veto stays at 0.90; share_c > 0.90: {veto:,}). Legs-only share > 1: {legs_gt1:,}.",
              f"Check 4.2 (old share reproduced from legs only): {cmp_ok:,} of {cmp_n:,} triggers with an old share agree to 1e-9.\n"]
        b = con.execute("SELECT count(*), count(*) FILTER (WHERE explained_share >= 0.5), median(explained_share), sum(sold_1h), sum(from_insider_1hop), sum(from_insider_2hops) FROM q1 WHERE share_legs > 1 AND sold_1h > 0").fetchone()
        if b and b[0]:
            L += [f"Bar (b), triggers with a legs-only share > 1 and non-insider selling in the hour after t: {b[0]:,}; at least half of that selling explained by transfers from an insider within two hops: {b[1]:,} ({pct(b[1] / b[0])}; bar ≥ 80 %); median explained share {pct(b[2])}; tokens sold {b[3]:,}, from insiders 1 hop {b[4]:,}, within 2 hops {b[5]:,}.\n"]
    r = con.execute("SELECT count(*), count(DISTINCT funder), count(*) FILTER (WHERE lamports < 5000000), median(lamports) / 1e9 FROM births").fetchone()
    L += ["### Q2: wallet births and funders\n",
          f"Births {r[0]:,} from {r[1]:,} funders; below 0.005 SOL (outside Q2) {r[2]:,} ({pct(r[2] / max(r[0], 1))}); median birth {r[3]:.4f} SOL. Hubs (≥ {HUB_DAY} births on a day or ≥ {HUB_WEEK} in 7 days): {info['hubs']:,}.\n"]
    if have_labels:
        L += ["| label | wallets (non-hub) | linked (≥ 5 same-label siblings within 7 d) | share | wallets incl. hubs | linked incl. hubs | share incl. hubs |", "|---|---|---|---|---|---|---|"]
        for lab, w0, l0, w1, l1 in con.execute("""SELECT label, sum(wallets) FILTER (WHERE NOT hub), sum(linked) FILTER (WHERE NOT hub), sum(wallets), sum(linked) FROM q2 GROUP BY 1 ORDER BY 1""").fetchall():
            w0, l0, w1, l1 = (x or 0 for x in (w0, l0, w1, l1))
            L.append(f"| {lab} | {w0:,} | {l0:,} | {pct(l0 / w0) if w0 else '–'} | {w1:,} | {l1:,} | {pct(l1 / w1) if w1 else '–'} |")
        L.append("\nBar: bundler and sniper ≥ 30 %, retail ≤ 5 % (hubs excluded).\n")
    L += ["| hub (funder) | max/day | max/7 d | births | median SOL | total SOL | " + " | ".join(LABELS) + " | onbekend | kind |", "|---|---|---|---|---|---|" + "---|" * (len(LABELS) + 2)]
    for row in con.execute("SELECT * FROM hub_table LIMIT 20").fetchall():
        funder, md, mw, n, med, tot = row[:6]
        mix = row[6:6 + len(LABELS) + 1]
        L.append(f"| {funder[:12]}… | {md} | {mw} | {n:,} | {med:.4f} | {tot:,.1f} | " + " | ".join(pct(x) for x in mix) + f" | {row[-1]} |")
    r = con.execute("SELECT count(*), count(*) FILTER (WHERE sellers > 0), sum(sold_fed)::DOUBLE / nullif(sum(sold), 0), median(fed_share), count(*) FILTER (WHERE fed_share >= 0.5), median(median_min_transfer_to_sell) FROM q3").fetchone()
    L += ["\n### Q3: selling wallets of bundles (descriptive)\n",
          f"Pools created in the window {r[0]:,}, with non-insider selling in their first hour {r[1]:,}; transfer-fed share of that selling {pct(r[2])} (tokens); median per pool {pct(r[3])}; pools with ≥ 50 % transfer-fed {r[4]:,}; median minutes from the last transfer to the first sell {('–' if r[5] is None else f'{r[5]:.1f}')}.\n"]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", required=True)
    ap.add_argument("--from-slot", type=int)
    ap.add_argument("--to-slot", type=int)
    ap.add_argument("--triggers", help="parquet with mint, t[, ins_share] (Q1)")
    ap.add_argument("--labels", help="parquet with wallet, label (Q2)")
    ap.add_argument("--money", help="money-v1 dir: labels through money_report.load (heavy)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    t0 = time.time()
    lo, hi = chunk_bounds(a.chunks)
    lo = a.from_slot if a.from_slot is not None else lo
    hi = a.to_slot if a.to_slot is not None else hi
    store = os.environ.get("LAB_STORE", STORE)
    con = connect(store, memory=os.environ.get("LAB_WALLETS_MEMORY", "1500MB"), threads=int(os.environ.get("LAB_WALLETS_THREADS", "2")))
    clock = Clock.from_store(con)
    os.makedirs(a.out, exist_ok=True)
    shas = setup(con, a.chunks, lo, hi, clock)
    info = {"lo": lo, "hi": hi, "store": store, "chunks": a.chunks, "extractor_shas": sorted(shas), "commit": git_commit(),
            "mints": con.execute("SELECT count(*) FROM wmints").fetchone()[0]}
    info["cons"] = conservation(con)
    progress(f"conservation: {info['cons']}")
    con.execute(f"COPY cons TO '{a.out}/conservation.parquet' (FORMAT PARQUET)")
    if a.triggers:
        info["q1_triggers"] = q1(con, a.triggers, clock)
        progress(f"q1: {info['q1_triggers']} triggers")
        con.execute(f"COPY q1 TO '{a.out}/q1_triggers.parquet' (FORMAT PARQUET)")
    (info["births"], info["funders"]), info["hubs"] = q2(con, a.labels, a.money)
    progress(f"q2: {info['births']} births, {info['funders']} funders, {info['hubs']} hubs")
    con.execute(f"COPY births TO '{a.out}/q2_births.parquet' (FORMAT PARQUET)")
    con.execute(f"COPY hub_table TO '{a.out}/q2_hubs.parquet' (FORMAT PARQUET)")
    con.execute(f"COPY q2 TO '{a.out}/q2_measure.parquet' (FORMAT PARQUET)")
    info["q3_pools"] = q3(con, clock, lo, hi)
    progress(f"q3: {info['q3_pools']} pools")
    con.execute(f"COPY q3 TO '{a.out}/q3_pools.parquet' (FORMAT PARQUET)")
    md = render(con, info, bool(a.triggers), bool(a.labels or a.money))
    with open(os.path.join(a.out, "transfers.md"), "w") as f:
        f.write(md + "\n")
    info["seconds"] = round(time.time() - t0, 1)
    with open(os.path.join(a.out, "run-transfers.json"), "w") as f:
        json.dump(info, f, indent=1, default=str)
    print(json.dumps({k: v for k, v in info.items() if k != "cons"}, default=str), file=sys.stderr)


if __name__ == "__main__":
    main()
