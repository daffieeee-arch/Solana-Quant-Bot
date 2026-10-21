"""Wallet accounting on the live recorder's segments: per wallet x UTC day the legs, the SOL bought and sold, the
realised cash, the network fees and how close behind the previous trade in the same pool each trade came. Read-only.

  python -m wallets.live_wallet_pnl --wallets FILE | --wallet W [--wallet W ...] [--root $LAB/recorder]
                                    [--from-slot A] [--to-slot B] [--store PAPER_STORE | --no-store] [--out DIR]

Inputs: the complete segments under <root>/segments with first_slot >= MIN_SLOT (459,648,000, the first slot of
epoch 1064, after the Alpenglow switch). Earlier segments are skipped and an earlier --from-slot is refused: epoch
1063 and before are clean-locked. Rows are read with the store's own SQL (backtest.store._curve_trades and
_pool_trades, as paper.live.LiveFeed), and a row recorded twice (a restart) counts once.

Per leg (the conventions of wallets.money):
  wallet      COALESCE(fee_payer, trader): a proxy's trades go to the fee payer that paid them
  sol         user side; curve: q plus the curve and creator fee on a buy, minus them on a sell; pool: the store's
              sol_amount (quote_user of a normal pool, base of a reversed one)
  cash        SOL received for sells - SOL paid for buys (no inventory and no mark: realised cash only)
  fees        tx_fee + jito_tip once per transaction (slot, tx_index); tx_fee is meta.fee and already holds the
              priority fee, so priority_fee is not added again (MEV E43 finding, #468)
  offset      slots since the previous transaction in the same pool (curve: the same mint), any wallet, among the
              recorded transactions; 0 = the same slot. The reserves on a leg are the state before the trade.

Pool orientation, per pool, in this order: its CreatePoolEvent in the segments; the base and quote mint accounts of
its own trades (ix_base_mint / ix_quote_mint, every direct buy and sell names them; a pool whose trades name two
pairs is a conflict and stays unknown); the paper engine's store (fwd1053-1054, read-only; never the clean store, a
1055-1063 store or a hold-out store). A pool known by none of them is counted (unknown_pool_legs), not priced; a pool without WSOL and a
curve with another quote mint are counted as non_sol_legs. No F7 or H rule is evaluated here (E32 forward lock).

Partial view: the recorder only records the pools on its transaction filter (the watchlist, open positions, probes
and new reversed pools, pump.fun creates), not the whole market. A wallet's day here is what it did in those pools.

Writes <out>/wallet_day.parquet, wallet_day.csv and summary.json; without --out, <out> is a new
$LAB/wallets/live-pnl-v1/<UTC stamp>/ per run. Bounded: DuckDB memory_limit at most 1 GB (--memory), 2 threads,
segments read in batches of BATCH; per batch every transaction's offset is computed (the last transaction per pool is
carried to the next batch) and only the listed wallets' legs are kept. Run it in
`systemd-run --user --scope -p MemoryMax=2G`.
"""

import argparse
import json
import os
import re
import sys
import time

import duckdb

from backtest.store import Chunk, _curve_trades, _pool_trades
from paper.live import segment_dirs

from .common import SOL, git_commit

LAB = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
ROOT = os.path.join(LAB, "recorder")
PAPER_STORE = os.environ.get("PAPER_STORE", os.path.join(LAB, "store", "fwd1053-1054.duckdb"))
OUT = os.path.join(LAB, "wallets", "live-pnl-v1")
MIN_SLOT = 459_648_000  # epoch 1064 (1064 x 432,000): after Alpenglow; 1063 and before are clean-locked
# Stores this tool may never open (by file name): the clean store and any store of the locked epochs 1055-1063, and
# the hold-out (epochs 1047-1051, or a name with "holdout"; LAB review on #502).
LOCKED_STORE = re.compile(r"clean|hold-?out|10(4[7-9]|5[01]|5[5-9]|6[0-3])", re.IGNORECASE)
MAX_MEMORY_MB = 1024  # DuckDB's limit at most 1 GB; the 2 GB scope (MemoryMax) around the run is the hard limit
WSOL = "So11111111111111111111111111111111111111112"
SOL_KEY = "11111111111111111111111111111111"  # a curve's quote_mint when it trades against SOL
SOL_QUOTES = (SOL_KEY, WSOL)
BATCH = 200  # segments per read (a segment is ~150 slots; 200 x ~60 parquet sources per query)
BASE58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
NOTE = ("partial view: only the pools the recorder records (watchlist, open positions, probes, new reversed pools, "
        "pump.fun creates), not the whole market; realised cash without inventory marks; no F7/H rule evaluated")


def check_store(path):
    if path and LOCKED_STORE.search(os.path.basename(path)):
        raise ValueError(f"{path}: the clean, hold-out (1047-1051) and 1055-1063 stores are locked; use the paper store "
                         f"({os.path.basename(PAPER_STORE)}) or --no-store")
    return path


def check_memory(text):
    """--memory as DuckDB takes it (e.g. 512MB, 1GB), at most MAX_MEMORY_MB."""
    m = re.fullmatch(r"(\d+)\s*(MB|GB)", text.strip(), re.IGNORECASE)
    mb = int(m.group(1)) * (1024 if m.group(2).upper() == "GB" else 1) if m else None
    if not mb or mb > MAX_MEMORY_MB:
        raise ValueError(f"--memory {text!r}: give 1-{MAX_MEMORY_MB} MB as e.g. 512MB or 1GB (the 2 GB scope is the hard limit)")
    return f"{mb}MB"


def run_dir(root=None, now=None):
    """The default output: a directory of its own per run, <OUT>/<UTC stamp> (never files beside earlier runs')."""
    return os.path.join(root or OUT, time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now)))


def read_wallets(path=None, given=()):
    """Wallets from a file (one per line, '#' comments, or a JSON list) and/or --wallet; base58 only."""
    out = list(given)
    if path:
        with open(path) as f:
            text = f.read()
        if text.lstrip().startswith("["):
            out += json.loads(text)
        else:
            out += [ln.split("#", 1)[0].strip() for ln in text.splitlines()]
    out = list(dict.fromkeys(w for w in out if w))
    bad = [w for w in out if not BASE58.match(w)]
    if bad:
        raise ValueError(f"not a base58 address: {bad[:3]}")
    if not out:
        raise ValueError("no wallets: pass --wallets FILE or --wallet W")
    return out


def pick_segments(root, lo=None, hi=None):
    """Complete segments with first_slot >= MIN_SLOT that overlap [lo, hi); (picked, skipped before MIN_SLOT)."""
    if lo is not None and lo < MIN_SLOT:
        raise ValueError(f"--from-slot {lo} is before {MIN_SLOT} (epoch 1064): epoch 1063 and before are clean-locked")
    picked, early = [], 0
    for seq, d, meta in segment_dirs(root):
        first, last = int(meta["first_slot"]), int(meta["last_slot"])
        if first < MIN_SLOT:
            early += 1
            continue
        if (lo is not None and last < lo) or (hi is not None and first >= hi):
            continue
        picked.append((seq, d, meta))
    return picked, early


def _connect(memory="1GB", threads=2):
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{memory}'; SET threads TO {int(threads)}; SET preserve_insertion_order = false; "
                "SET enable_progress_bar = false")
    con.execute("""
      CREATE TEMP TABLE wanted (wallet VARCHAR);
      CREATE TEMP TABLE blocks (slot BIGINT, block_time BIGINT);
      CREATE TEMP TABLE created (pool VARCHAR, base_mint VARCHAR, quote_mint VARCHAR, slot BIGINT);
      CREATE TEMP TABLE pool_ix (pool VARCHAR, base_mint VARCHAR, quote_mint VARCHAR, n BIGINT);
      CREATE TEMP TABLE curve_quote (mint VARCHAR, quote_mint VARCHAR, n BIGINT);
      CREATE TEMP TABLE last_tx (pool_key VARCHAR, slot BIGINT, tx_index BIGINT);
      CREATE TEMP TABLE legs_w (venue VARCHAR, pool_key VARCHAR, slot BIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT,
                                trader VARCHAR, fee_payer VARCHAR, wallet VARCHAR, kind VARCHAR, is_buy BOOLEAN, base BIGINT,
                                quote_user BIGINT, q HUGEINT, fee HUGEINT, creator_fee HUGEINT, tx_fee BIGINT, jito_tip BIGINT,
                                offset_slots BIGINT)""")
    return con


def _read_batch(con, segs, lo, hi):
    """One batch of segments (in chain order): the side tables for every row, the offset of every transaction to the
    previous one in its pool (carried over batches in last_tx), and the legs of the listed wallets. A row whose
    transaction is at or before the last one already read in its pool is a repeat (a restart records slots again) and
    is dropped. Returns (rows read, rows kept)."""
    c = Chunk(None)
    c.chunk = [(seq, None, d) for seq, d, _ in segs]  # data.files reads the third field as the directory
    w = f"slot >= {lo}" + (f" AND slot < {hi}" if hi is not None else "")
    if c.src("blocks"):
        con.execute(f"INSERT INTO blocks SELECT slot, block_time FROM {c.src('blocks')} WHERE {w} AND block_time IS NOT NULL")
    if c.src("pump_amm/CreatePoolEvent"):
        con.execute(f"INSERT INTO created SELECT pool, base_mint, quote_mint, slot FROM {c.src('pump_amm/CreatePoolEvent')}")
    for t in ("pump_amm/BuyEvent", "pump_amm/SellEvent"):
        if c.src(t):
            base, quote = c.col(t, "ix_base_mint", "VARCHAR"), c.col(t, "ix_quote_mint", "VARCHAR")
            con.execute(f"INSERT INTO pool_ix SELECT pool, {base}, {quote}, count(*) FROM {c.src(t)} GROUP BY ALL")
    if c.src("pump/TradeEvent"):
        q = c.col("pump/TradeEvent", "quote_mint", "VARCHAR")
        con.execute(f"INSERT INTO curve_quote SELECT mint, COALESCE({q}, '{SOL_KEY}'), count(*) FROM {c.src('pump/TradeEvent')} GROUP BY ALL")
    parts = []
    pool, curve = _pool_trades(c), _curve_trades(c, mints=None)
    if pool:
        parts.append(f"""SELECT 'pool' AS venue, pool AS pool_key, slot, tx_index, outer_ix, inner_ix, trader, fee_payer, kind,
                                NULL::BOOLEAN AS is_buy, base, quote_user, NULL::HUGEINT AS q, NULL::HUGEINT AS fee,
                                NULL::HUGEINT AS creator_fee, tx_fee::BIGINT AS tx_fee, COALESCE(jito_tip, 0)::BIGINT AS jito_tip
                         FROM ({pool}) WHERE kind IN ('buy', 'sell') AND {w}""")
    if curve:
        parts.append(f"""SELECT 'curve' AS venue, mint AS pool_key, slot, tx_index, outer_ix, inner_ix, trader, fee_payer,
                                NULL::VARCHAR AS kind, is_buy, NULL::BIGINT AS base, NULL::BIGINT AS quote_user, q, fee, creator_fee,
                                tx_fee::BIGINT AS tx_fee, COALESCE(jito_tip, 0)::BIGINT AS jito_tip
                         FROM ({curve}) WHERE {w}""")
    if not parts:
        return 0, 0
    con.execute(f"CREATE OR REPLACE TEMP TABLE b_raw AS {' UNION ALL BY NAME '.join(parts)}")
    con.execute("""
      CREATE OR REPLACE TEMP TABLE b_legs AS
      SELECT r.* FROM b_raw r LEFT JOIN last_tx t USING (pool_key)
      WHERE t.pool_key IS NULL OR r.slot > t.slot OR (r.slot = t.slot AND r.tx_index > t.tx_index)
      QUALIFY row_number() OVER (PARTITION BY r.venue, r.pool_key, r.slot, r.tx_index, r.outer_ix, r.inner_ix) = 1""")
    con.execute("""
      CREATE OR REPLACE TEMP TABLE b_tx AS
      SELECT pool_key, slot, tx_index, seed, slot - lag(slot) OVER (PARTITION BY pool_key ORDER BY slot, tx_index) AS offset_slots
      FROM (SELECT DISTINCT pool_key, slot, tx_index, false AS seed FROM b_legs
            UNION ALL SELECT pool_key, slot, tx_index, true FROM last_tx SEMI JOIN (SELECT DISTINCT pool_key FROM b_legs) USING (pool_key))""")
    con.execute("""
      INSERT INTO legs_w BY NAME
      SELECT l.*, COALESCE(l.fee_payer, l.trader) AS wallet, t.offset_slots
      FROM b_legs l JOIN b_tx t USING (pool_key, slot, tx_index)
      WHERE NOT t.seed AND (COALESCE(l.fee_payer, l.trader) IN (SELECT wallet FROM wanted) OR l.trader IN (SELECT wallet FROM wanted))""")
    con.execute("""
      CREATE OR REPLACE TEMP TABLE last_tx AS
      SELECT pool_key, slot, tx_index FROM (SELECT * FROM last_tx UNION ALL SELECT DISTINCT pool_key, slot, tx_index FROM b_legs)
      QUALIFY row_number() OVER (PARTITION BY pool_key ORDER BY slot DESC, tx_index DESC) = 1""")
    return con.execute("SELECT (SELECT count(*) FROM b_raw), (SELECT count(*) FROM b_legs)").fetchone()


def _classes(con, store):
    """pool -> quote_class and its source, for the pools of the listed wallets' legs; returns the counts per source."""
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE cls_created AS
      SELECT pool, CASE WHEN base_mint = '{WSOL}' THEN 'reversed' WHEN quote_mint = '{WSOL}' THEN 'sol' ELSE 'other' END AS quote_class
      FROM created QUALIFY row_number() OVER (PARTITION BY pool ORDER BY slot) = 1""")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE cls_ix AS
      SELECT pool, CASE WHEN any_value(base_mint) = '{WSOL}' THEN 'reversed' WHEN any_value(quote_mint) = '{WSOL}' THEN 'sol'
                        ELSE 'other' END AS quote_class, count(DISTINCT (base_mint, quote_mint)) AS pairs
      FROM pool_ix WHERE base_mint IS NOT NULL AND quote_mint IS NOT NULL GROUP BY pool""")
    con.execute("CREATE OR REPLACE TEMP TABLE w_pools AS SELECT DISTINCT pool_key AS pool FROM legs_w WHERE venue = 'pool'")
    con.execute("CREATE OR REPLACE TEMP TABLE cls_store (pool VARCHAR, quote_class VARCHAR)")
    if store:
        con.execute(f"ATTACH '{store.replace(chr(39), chr(39) * 2)}' AS st (READ_ONLY)")
        con.execute("""INSERT INTO cls_store SELECT s.pool, s.quote_class FROM st.pools s SEMI JOIN w_pools USING (pool)
                       WHERE s.quote_class IN ('sol', 'reversed', 'other')""")  # the store's 'unknown' stays unknown
        con.execute("DETACH st")
    con.execute("""
      CREATE OR REPLACE TEMP TABLE pool_class AS
      SELECT r.pool,
             COALESCE(c.quote_class, CASE WHEN i.pairs = 1 THEN i.quote_class END, s.quote_class) AS quote_class,
             CASE WHEN c.pool IS NOT NULL THEN 'create' WHEN i.pairs = 1 THEN 'ix_mints' WHEN s.pool IS NOT NULL THEN 'store'
                  WHEN i.pairs > 1 THEN 'conflict' END AS source
      FROM w_pools r LEFT JOIN cls_created c USING (pool) LEFT JOIN cls_ix i USING (pool) LEFT JOIN cls_store s USING (pool)""")
    return dict(con.execute("SELECT COALESCE(source, 'unknown'), count(*) FROM pool_class GROUP BY 1 ORDER BY 1").fetchall())


def _price(con):
    """The listed wallets' legs with their quote class, user-side SOL (NULL when not priced) and UTC day."""
    sol_q = ",".join(f"'{q}'" for q in SOL_QUOTES)
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE legs_day AS
      WITH quote AS (SELECT mint AS pool_key, arg_max(quote_mint, n) AS quote_mint FROM curve_quote GROUP BY mint),
      l AS (
        SELECT l.venue, l.pool_key, l.slot, l.tx_index, l.outer_ix, l.inner_ix, l.trader, l.fee_payer, l.wallet, l.tx_fee,
               l.jito_tip, l.offset_slots, k.quote_class,
               CASE WHEN k.quote_class IN ('sol', 'reversed') THEN (l.kind = 'buy') = (k.quote_class = 'sol') END AS is_buy,
               CASE WHEN k.quote_class = 'sol' THEN l.quote_user WHEN k.quote_class = 'reversed' THEN l.base END AS sol
        FROM legs_w l LEFT JOIN pool_class k ON k.pool = l.pool_key WHERE l.venue = 'pool'
        UNION ALL
        SELECT l.venue, l.pool_key, l.slot, l.tx_index, l.outer_ix, l.inner_ix, l.trader, l.fee_payer, l.wallet, l.tx_fee,
               l.jito_tip, l.offset_slots, CASE WHEN q.quote_mint IN ({sol_q}) THEN 'sol' ELSE 'other' END,
               CASE WHEN q.quote_mint IN ({sol_q}) THEN l.is_buy END,
               CASE WHEN q.quote_mint IN ({sol_q}) THEN
                 (l.q + CASE WHEN l.is_buy THEN l.fee + l.creator_fee ELSE -(l.fee + l.creator_fee) END)::BIGINT END
        FROM legs_w l LEFT JOIN quote q USING (pool_key) WHERE l.venue = 'curve')
      SELECT l.*, strftime(make_timestamp(b.block_time * 1000000), '%Y-%m-%d') AS day
      FROM l ASOF LEFT JOIN (SELECT slot AS bslot, any_value(block_time) AS block_time FROM blocks GROUP BY slot) b
           ON l.slot >= b.bslot""")


WALLET_DAY = f"""
  WITH w AS (SELECT * FROM legs_day WHERE wallet IN (SELECT wallet FROM wanted)),
  fee AS (SELECT wallet, day, slot, tx_index, max(tx_fee + jito_tip) AS fee FROM w GROUP BY ALL),
  off AS (SELECT DISTINCT wallet, day, pool_key, slot, tx_index, offset_slots FROM w),
  by_trader AS (  -- legs a listed wallet signed as the trader while another fee payer paid (counted under that payer)
    SELECT trader AS wallet, day, count(*) AS legs_as_trader_paid_by_other FROM legs_day
    WHERE trader IN (SELECT wallet FROM wanted) AND fee_payer IS NOT NULL AND fee_payer <> trader GROUP BY ALL)
  SELECT k.wallet, k.day, COALESCE(g.legs, 0) AS legs, COALESCE(g.priced_legs, 0) AS priced_legs,
         COALESCE(g.curve_legs, 0) AS curve_legs, COALESCE(g.pool_legs, 0) AS pool_legs, COALESCE(g.buys, 0) AS buys,
         COALESCE(g.sells, 0) AS sells, COALESCE(g.buy_sol, 0) AS buy_sol, COALESCE(g.sell_sol, 0) AS sell_sol,
         COALESCE(g.sell_sol - g.buy_sol, 0) AS cash_sol, COALESCE(f.fees_sol, 0) AS fees_sol,
         COALESCE(g.sell_sol - g.buy_sol - f.fees_sol, 0) AS net_cash_sol, COALESCE(f.txs, 0) AS txs, COALESCE(g.pools, 0) AS pools,
         COALESCE(g.unknown_pool_legs, 0) AS unknown_pool_legs, COALESCE(g.non_sol_legs, 0) AS non_sol_legs,
         COALESCE(o.offset_n, 0) AS offset_n, o.offset_median, o.offset_p10, o.offset_min, o.same_slot_share,
         COALESCE(t.legs_as_trader_paid_by_other, 0) AS legs_as_trader_paid_by_other, g.first_slot, g.last_slot
  FROM (SELECT wallet, day FROM w UNION SELECT wallet, day FROM by_trader) k
  LEFT JOIN (SELECT wallet, day, count(*) AS legs, count(sol) AS priced_legs, count(*) FILTER (WHERE venue = 'curve') AS curve_legs,
               count(*) FILTER (WHERE venue = 'pool') AS pool_legs, count(*) FILTER (WHERE is_buy) AS buys,
               count(*) FILTER (WHERE NOT is_buy) AS sells,
               COALESCE(sum(sol) FILTER (WHERE is_buy), 0) / {SOL} AS buy_sol, COALESCE(sum(sol) FILTER (WHERE NOT is_buy), 0) / {SOL} AS sell_sol,
               count(DISTINCT pool_key) AS pools,
               count(*) FILTER (WHERE venue = 'pool' AND quote_class IS NULL) AS unknown_pool_legs,
               count(*) FILTER (WHERE quote_class = 'other') AS non_sol_legs, min(slot) AS first_slot, max(slot) AS last_slot
        FROM w GROUP BY wallet, day) g USING (wallet, day)
  LEFT JOIN (SELECT wallet, day, count(*) AS txs, sum(fee) / {SOL} AS fees_sol FROM fee GROUP BY ALL) f USING (wallet, day)
  LEFT JOIN (SELECT wallet, day, count(offset_slots) AS offset_n, median(offset_slots) AS offset_median,
               quantile_cont(offset_slots, 0.1) AS offset_p10, min(offset_slots) AS offset_min,
               avg((offset_slots = 0)::INTEGER) AS same_slot_share
        FROM off GROUP BY ALL) o USING (wallet, day)
  LEFT JOIN by_trader t USING (wallet, day)
  ORDER BY wallet, day"""


def build(con, segs, wallets, lo, hi, store, log=print):
    """Reads the segments in batches and prices the listed wallets' legs (legs_day); returns the read counts."""
    con.executemany("INSERT INTO wanted VALUES (?)", [(w,) for w in wallets])
    raw = kept = 0
    t0 = time.time()
    for i in range(0, len(segs), BATCH):
        n_raw, n_kept = _read_batch(con, segs[i:i + BATCH], lo, hi)
        raw, kept = raw + n_raw, kept + n_kept
        log(f"segments {min(i + BATCH, len(segs))}/{len(segs)} read ({time.time() - t0:.0f} s)")
    sources = _classes(con, store)
    _price(con)
    return {"rows_read": raw, "legs": kept, "duplicate_rows_dropped": raw - kept, "pool_class_sources": sources}


def run(wallets, root=ROOT, lo=None, hi=None, store=PAPER_STORE, memory="1GB", threads=2, log=print):
    """(wallet x day rows as dicts, summary)."""
    check_store(store)
    memory = check_memory(memory)
    segs, early = pick_segments(root, lo, hi)
    lo = MIN_SLOT if lo is None else lo
    con = _connect(memory, threads)
    counts = build(con, segs, wallets, lo, hi, store, log)
    cur = con.execute(WALLET_DAY)
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, r)) for r in cur.fetchall()]
    mine = con.execute("""SELECT count(*), count(sol), count(*) FILTER (WHERE venue = 'pool' AND quote_class IS NULL),
                                 count(*) FILTER (WHERE quote_class = 'other'), count(*) FILTER (WHERE day IS NULL)
                          FROM legs_day WHERE wallet IN (SELECT wallet FROM wanted)""").fetchone()
    slots = con.execute("SELECT min(slot), max(slot) FROM blocks").fetchone()
    con.close()
    summary = {
        "tool": "wallets.live_wallet_pnl", "commit": git_commit(), "written_unix": int(time.time()), "note": NOTE,
        "min_slot": MIN_SLOT, "from_slot": lo, "to_slot": hi, "root": root,
        "segments_read": len(segs), "segments_skipped_before_min_slot": early,
        "first_segment": segs[0][0] if segs else None, "last_segment": segs[-1][0] if segs else None,
        "block_slot_range": list(slots), "store": store or None,
        "legs_all_wallets": counts["legs"], "rows_read": counts["rows_read"],
        "duplicate_rows_dropped": counts["duplicate_rows_dropped"],
        "pool_class_sources": counts["pool_class_sources"],  # the pools of the listed wallets' legs
        "listed_wallet_legs": mine[0], "listed_priced_legs": mine[1], "listed_unknown_pool_legs": mine[2],
        "listed_non_sol_legs": mine[3], "listed_legs_without_day": mine[4],
        "wallets": len(wallets), "wallets_seen": len({r["wallet"] for r in rows}), "wallet_days": len(rows),
        "fees": "tx_fee + jito_tip once per (slot, tx_index); tx_fee already holds the priority fee",
        "wallet_key": "COALESCE(fee_payer, trader)",
    }
    return rows, summary


def write(rows, summary, out):
    import pyarrow as pa
    import pyarrow.csv as pcsv
    import pyarrow.parquet as pq

    os.makedirs(out, exist_ok=True)
    table = pa.Table.from_pylist(rows) if rows else pa.table({"wallet": pa.array([], pa.string())})
    for name, fn in (("wallet_day.parquet", pq.write_table), ("wallet_day.csv", pcsv.write_csv)):
        tmp = os.path.join(out, name + ".tmp")
        fn(table, tmp)
        os.replace(tmp, os.path.join(out, name))
    tmp = os.path.join(out, "summary.json.tmp")
    with open(tmp, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    os.replace(tmp, os.path.join(out, "summary.json"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--wallets", help="file: one address per line ('#' comments) or a JSON list")
    ap.add_argument("--wallet", action="append", default=[])
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--from-slot", type=int)
    ap.add_argument("--to-slot", type=int)
    ap.add_argument("--store", default=PAPER_STORE)
    ap.add_argument("--no-store", action="store_true", help="classify pools from the segments only")
    ap.add_argument("--out", help=f"output directory (default: a new {OUT}/<UTC stamp> per run)")
    ap.add_argument("--memory", default="1GB", help=f"DuckDB memory_limit, at most {MAX_MEMORY_MB} MB; the real limit is "
                                                    "the MemoryMax=2G scope the run is started in")
    a = ap.parse_args(argv)
    try:
        wallets = read_wallets(a.wallets, a.wallet)
        memory = check_memory(a.memory)
        rows, summary = run(wallets, a.root, a.from_slot, a.to_slot, None if a.no_store else a.store, memory,
                            log=lambda m: print(m, file=sys.stderr))
    except ValueError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    out = a.out or run_dir()
    write(rows, summary, out)
    print(f"wrote {out}", file=sys.stderr)
    for r in rows:
        print(f"{r['wallet'][:8]}… {r['day']}  legs {r['legs']:>6}  buy {r['buy_sol']:>10.2f}  sell {r['sell_sol']:>10.2f}  "
              f"cash {r['cash_sol']:>+9.2f}  fees {r['fees_sol']:>7.3f}  offset med {r['offset_median']}")
    print(f"{summary['wallet_days']} wallet-days from {summary['segments_read']} segments; {NOTE}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
