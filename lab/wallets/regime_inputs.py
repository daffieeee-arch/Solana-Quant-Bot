"""Regime inputs from the live recorder's segments for any window (LAB research's step 1 §2.5, coordinator 24 Oct): the
files of wallets/regime-1064-inputs (E67/E68) by the same routes, read-only. TRIGGER AND EVENT COUNTS ONLY: no P&L.

  python -P -m wallets.regime_inputs --segments ROOT (--from-utc T --to-utc T | --from-slot S --to-slot S) --label L \\
      --out DIR [--journal JOURNAL.sqlite] [--clock epoch|window] [--memory 2GB] [--threads 2]

ROOT is the recorder's segments directory (the VPS's recorder/segments, or the NAS archive copy). The window is
[from, to): UTC times map to the first block at or after each. Written to DIR via DIR.tmp (an existing DIR is refused):

- live-triggers-<L>.parquet (venue, mint, t, depth, pool, orientation, trader, trader_farmer, flagged): the tournament's
  F7 trigger function (backtest.strategies.f7_signals_sql) on store-shaped tables built from the segments, E68's route
  (research/regime-1064-2026-10-23/parity/trigger_parity.py) unchanged:
  - reversed pools only (base = WSOL; the live configuration evaluates pool_reversed), 60,000 slots of history first;
  - store.py pass 2's columns: token_buy = (kind = 'buy') = (orientation = 'normal'), sol_amount = base and
    sol_depth = b for a reversed pool; boost inner buys left out; exact-out buys at user_quote_amount_in;
  - backfill rows (the probes' earlier transactions) after the main rows, never overlapping them, as history (hist);
  - farmer = false (it changes only the farm label, never the trigger);
  - the clock: --clock epoch (default) the measured ms per slot of every epoch in the window's blocks, as store.py
    meta; --clock window one value over the whole window, as E68 did.
- probe-pools-<L>.parquet (pool, t0, p0, first_seen): E67's route (research/regime-1064-2026-10-23/coverage.py) unchanged:
  pools with a trade on the tx_probes stream; t0 and p0 = the slot and price ((q + vq) / b) of the first trade leg (main
  rows of the window, ordered by slot, tx_index, outer_ix, inner_ix: E67's order plus the leg, so a tie is no longer
  arbitrary) whose pool SOL side is >= 120 SOL; first_seen = E67's min(slot) over those same rows (so equal to
  t0, kept for the schema). Both orientations, as in E67 (info.json counts them).
- segments.parquet: every segment that overlaps the window or its history (seq, slots, written_unix, filter level and
  flags, complete, slots_closed, reconnects); coverage-hourly.parquet per UTC hour of the window's blocks: blocks,
  first / last slot, segments, the share of block slots in level-0 segments (full filter coverage); info.json has the
  slot gaps between segments inside the window (slot_gaps) and in its 60,000 slots of history (history_gaps,
  history_complete: E68 had the full history; a gap there is logged, not refused).
Refused before anything is written: an existing DIR, DIR inside the segments, a --from-utc the segments do not cover
(no block in the 60 s before it: the window would silently start later), segments that end before the window does, a
segment whose _segment.json counts rows of a table read here but whose file is missing (an incomplete copy).
- counts-hourly.parquet and recorder-counts.json (the window as check-chunk-recorder.json's 'day'): blocks, skipped
  blocks, pump creates (distinct mints), graduations (distinct pools of a CreatePoolEvent arriving on tx_new_pools),
  pump_amm buy + sell rows seen (a subset: only followed pools), first and last block time.
- with --journal: journal-f7-<L>.parquet (the paper journal's F7 signals in the window, read with mode=ro) and the
  (pool, t) comparison against the live triggers on reversed pools in info.json.
- info.json (the window, clock, inputs, counts), SHA256SUMS.
"""

import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys

import duckdb

W = "So11111111111111111111111111111111111111112"  # WSOL
DEFAULT_KEY = "11111111111111111111111111111111"
BOOST_BUY_DISC = "694406af000723a2"
EXACT_OUT = ("66063d1201daebea", "b817ee6167c5d33d")
HISTORY_SLOTS = 60_000  # E68: the rows before the window the trigger windows look back on
START_TOLERANCE_S = 60  # --from-utc: a block within this many seconds before it, or the segments do not cover the start
MIN_CLOCK_SLOTS = 1_000  # an epoch's measured slot time needs this span of blocks (LiveFeed's first 1,000 slots)
PROBE_MIN_SOL = 120  # E67: a probe pool's first trade at >= 120 SOL on the pool's SOL side
EPOCH_SLOTS = 432_000
TRIG_COLS = ("venue", "mint", "t", "depth", "pool", "orientation", "trader", "trader_farmer", "flagged")


class Refused(Exception):
    pass


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def git_commit():
    here = os.path.dirname(os.path.abspath(__file__))
    return subprocess.run(["git", "-C", here, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() or None


def lit(files):
    return "[" + ",".join("'" + f.replace("'", "''") + "'" for f in files) + "]"


def utc(s):
    t = dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    return int(t.timestamp())


def segments(root):
    """[{seq, first_slot, last_slot, written_unix, level, complete, ...}] of every segment with a _segment.json (a
    segment without one is still being written: counted, not read)."""
    out, open_ = [], 0
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d, "_segment.json")
        if not d.isdigit():
            continue
        if not os.path.exists(p):
            open_ += 1
            continue
        j = json.load(open(p))
        f = j.get("filter") or {}
        out.append({"seq": int(j["seq"]), "dir": os.path.join(root, d), "first_slot": int(j["first_slot"]),
                    "last_slot": int(j["last_slot"]), "written_unix": int(j.get("written_unix") or 0),
                    "level": int(f.get("level", -1)), "complete": bool(j.get("complete")),
                    "slots_closed": int(j.get("slots_closed") or 0), "reconnects": len(j.get("reconnects") or []),
                    "backfill": bool(f.get("backfill")), "new_reversed": bool(f.get("new_reversed")),
                    "pump_creates": bool(f.get("pump_creates")), "tx_pools": int(f.get("tx_pools") or 0),
                    "tx_probes": int(f.get("tx_probes") or 0), "new_vaults": int(f.get("new_vaults") or 0),
                    "dropped": ",".join(str(x) for x in (f.get("dropped") or [])) or "-",
                    "rows": j.get("rows") or {}, "backfill_rows": j.get("backfill_rows") or {}})
    return sorted(out, key=lambda s: s["seq"]), open_


def files(segs, table, sub="", first_part_only=False):
    """The table's parquet files in these segments (sub = 'backfill/' for the backfill tables). A segment whose
    _segment.json counts rows of the table (rows, or backfill_rows for the backfill) but has no file for it is refused:
    an incomplete copy (rsync writes a directory's own files before its subdirectories) would give lower counts and
    triggers without a trace."""
    out = []
    for s in segs:
        if first_part_only:  # E67 read part-00000 only
            p = os.path.join(s["dir"], sub, table, "part-00000.parquet")
            got = [p] if os.path.exists(p) else []
        else:
            got = sorted(glob.glob(os.path.join(s["dir"], sub, table, "*.parquet")))
        n = (s.get("backfill_rows") if sub else s.get("rows") or {}) or {}
        if not got and int(n.get(table) or 0) > 0:
            raise Refused(f"segment {s['seq']}: _segment.json counts {n[table]} {sub}{table} rows but the file is missing")
        out += got
    return out


def window_slots(con, segs, lo_utc, hi_utc):
    """[LO, HI): the first block at or after each time, from the blocks of the segments written around the window."""
    near = [s for s in segs if lo_utc - 3 * 3600 <= s["written_unix"] <= hi_utc + 3 * 3600]
    fs = files(near, "blocks")
    if not fs:
        raise Refused("no blocks in the segments written around the window")
    src = f"read_parquet({lit(fs)}, union_by_name = true)"
    lo, hi, last, before = con.execute(f"""SELECT min(slot) FILTER (WHERE block_time >= {lo_utc}), min(slot) FILTER (WHERE block_time >= {hi_utc}),
                                   max(block_time), max(block_time) FILTER (WHERE block_time < {lo_utc}) FROM {src}""").fetchone()
    if lo is None:
        raise Refused(f"no block at or after {lo_utc}")
    if before is None or before < lo_utc - START_TOLERANCE_S:
        raise Refused(f"the segments do not cover the window's start: no block within {START_TOLERANCE_S} s before {lo_utc} "
                      f"(last one before: {before}); the window would start later than asked")
    if hi is None:
        raise Refused(f"the segments end before the window does (last block time {last} < {hi_utc}): too early to run")
    return int(lo), int(hi)


def clock_ms(con, segs, lo, hi, mode):
    """{epoch: ms per slot}: one value over the window's blocks (E68, --clock window), or per epoch over every block of
    the segments read (store.py meta's formula), an epoch with less than MIN_CLOCK_SLOTS of blocks left out (the Clock
    then carries the epoch before it: a piece of an epoch at a window edge gives no NaN or noisy value)."""
    src = f"read_parquet({lit(files(segs, 'blocks'))}, union_by_name = true)"
    if mode == "window":
        v = con.execute(f"""SELECT (max(block_time) - min(block_time)) * 1000.0 / (max(slot) - min(slot)) FROM {src}
                            WHERE slot BETWEEN {lo} AND {hi}""").fetchone()[0]
        return {lo // EPOCH_SLOTS: v}
    ms = {int(e): v for e, v in con.execute(f"""SELECT slot // {EPOCH_SLOTS}, (max(block_time) - min(block_time)) * 1000.0 / (max(slot) - min(slot))
                                                FROM {src} GROUP BY 1 HAVING max(slot) - min(slot) >= {MIN_CLOCK_SLOTS} ORDER BY 1""").fetchall()}
    if not ms or min(ms) > lo // EPOCH_SLOTS:
        raise Refused(f"no epoch at or before the window's start has {MIN_CLOCK_SLOTS} slots of blocks to measure its slot time: {ms}")
    return ms


def build_store_tables(con, segs, lo, hi):
    """E68's store-shaped tables (pools, mints, pool, an empty curve) for reversed pools, rows from lo - HISTORY_SLOTS."""
    def src(t, sub=""):
        fs = files(segs, t, sub)
        return f"read_parquet({lit(fs)}, union_by_name = true)" if fs else None
    cp = " UNION ALL BY NAME ".join(f"SELECT * FROM {x}" for x in (src("pump_amm/CreatePoolEvent"), src("pump_amm/CreatePoolEvent", "backfill/")) if x)
    if not cp:
        raise Refused("no CreatePoolEvent table in the segments")
    con.execute(f"""CREATE TABLE cpe AS SELECT pool, slot, base_mint, quote_mint, coin_creator, COALESCE(is_mayhem_mode, false) AS mayhem
                    FROM ({cp}) QUALIFY row_number() OVER (PARTITION BY pool ORDER BY slot, tx_index) = 1""")
    common = ("pool, slot, tx_index, outer_ix, inner_ix, \"user\" AS trader, ix_base_mint, ix_quote_mint, coin_creator, parent_ix_disc, "
              "pool_base_token_reserves::BIGINT AS b, pool_quote_token_reserves::BIGINT AS vault")
    exo = ",".join(f"'{d}'" for d in EXACT_OUT)
    first = lo - HISTORY_SLOTS

    def trade_parts(sub, hist):
        b, s_ = src("pump_amm/BuyEvent", sub), src("pump_amm/SellEvent", sub)
        out = []
        if b:
            out.append(f"""SELECT 'buy' AS kind, {common}, base_amount_out::BIGINT AS base,
                (CASE WHEN parent_ix_disc IN ({exo}) THEN user_quote_amount_in ELSE quote_amount_in END)::BIGINT AS quote_user, {hist} AS hist
                FROM {b} WHERE parent_ix_disc IS DISTINCT FROM '{BOOST_BUY_DISC}' AND ix_base_mint = '{W}' AND slot >= {first} AND slot < {hi}""")
        if s_:
            out.append(f"""SELECT 'sell' AS kind, {common}, base_amount_in::BIGINT AS base, user_quote_amount_out::BIGINT AS quote_user, {hist} AS hist
                FROM {s_} WHERE ix_base_mint = '{W}' AND slot >= {first} AND slot < {hi}""")
        return " UNION ALL BY NAME ".join(out)
    main = trade_parts("", "false")
    if not main:
        raise Refused("no pump_amm Buy/Sell tables in the segments")
    con.execute(f"CREATE TABLE raw AS {main}")
    bf = trade_parts("backfill/", "true")
    if bf:
        con.execute(f"""INSERT INTO raw BY NAME SELECT * FROM ({bf}) b
                        WHERE NOT EXISTS (SELECT 1 FROM raw r WHERE r.pool = b.pool AND r.slot = b.slot AND r.tx_index = b.tx_index
                                          AND r.outer_ix = b.outer_ix AND r.inner_ix = b.inner_ix)""")
    con.execute(f"""CREATE TABLE pools AS
      WITH t AS (SELECT pool, arg_max(ix_base_mint, slot) AS base_mint, arg_max(ix_quote_mint, slot) AS quote_mint,
                        arg_max(coin_creator, slot) AS coin_creator FROM raw GROUP BY 1)
      SELECT t.pool, COALESCE(cpe.base_mint, t.base_mint) AS bm, COALESCE(cpe.quote_mint, t.quote_mint) AS qm,
             CASE WHEN COALESCE(cpe.quote_mint, t.quote_mint) = '{W}' THEN 'sol' WHEN COALESCE(cpe.base_mint, t.base_mint) = '{W}' THEN 'reversed' ELSE 'other' END AS quote_class,
             CASE WHEN COALESCE(cpe.base_mint, t.base_mint) = '{W}' THEN 'reversed' ELSE 'normal' END AS orientation,
             CASE WHEN COALESCE(cpe.base_mint, t.base_mint) = '{W}' THEN COALESCE(cpe.quote_mint, t.quote_mint) ELSE COALESCE(cpe.base_mint, t.base_mint) END AS mint,
             COALESCE(cpe.coin_creator, t.coin_creator) AS coin_creator, COALESCE(cpe.mayhem, false) AS mayhem, cpe.slot AS create_slot
      FROM t LEFT JOIN cpe USING (pool)""")
    ce = src("pump/CreateEvent")
    if ce:
        con.execute(f"""CREATE TABLE mints AS SELECT mint, slot AS create_slot, "user" AS creator, COALESCE(is_mayhem_mode, false) AS mayhem,
                        COALESCE(quote_mint, '{DEFAULT_KEY}') AS quote_mint FROM {ce} QUALIFY row_number() OVER (PARTITION BY mint ORDER BY slot, tx_index) = 1""")
    else:
        con.execute("CREATE TABLE mints (mint VARCHAR, create_slot UBIGINT, creator VARCHAR, mayhem BOOLEAN, quote_mint VARCHAR)")
    con.execute("""CREATE TABLE pool AS SELECT r.kind, r.pool, r.slot, r.tx_index, r.outer_ix, r.inner_ix, r.trader, p.orientation,
       (r.kind = 'buy') = (p.orientation = 'normal') AS token_buy,
       CASE WHEN p.orientation = 'normal' THEN r.quote_user ELSE r.base END AS sol_amount,
       CASE WHEN p.orientation = 'normal' THEN r.vault ELSE r.b END AS sol_depth, false AS farmer, r.hist
       FROM raw r JOIN pools p USING (pool) WHERE p.quote_class IN ('sol', 'reversed')""")
    con.execute("CREATE TABLE curve (mint VARCHAR, slot UBIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT, trader VARCHAR, rq BIGINT, q BIGINT, vq BIGINT, is_buy BOOLEAN)")
    return con.execute("SELECT count(*), sum(hist::INT), count(DISTINCT pool) FROM raw").fetchone()


def live_triggers(con, segs, lo, hi, ms):
    from backtest.store import load_defaults
    from backtest.strategies import Clock, f7_signals_sql

    raw = build_store_tables(con, segs, lo, hi)
    trig = f7_signals_sql(con, load_defaults(), lo - 600_000, hi + 200_000, clock=Clock(ms), trig_range=(lo, hi - 1))
    con.execute("CREATE TABLE trig (venue VARCHAR, mint VARCHAR, t BIGINT, depth BIGINT, pool VARCHAR, orientation VARCHAR, "
                "trader VARCHAR, trader_farmer BOOLEAN, flagged BOOLEAN)")
    if trig:
        con.executemany("INSERT INTO trig VALUES (?,?,?,?,?,?,?,?,?)", trig)
    return {"raw_reversed_trades": raw[0], "raw_backfill": raw[1], "raw_pools": raw[2],
            "triggers": dict(con.execute("SELECT orientation, count(*) FROM trig GROUP BY 1").fetchall()),
            "trigger_pools": con.execute("SELECT count(DISTINCT pool) FROM trig").fetchone()[0]}


def probe_pools(con, segs, lo, hi):
    """E67's p0: pools with a trade on tx_probes; the first trade at >= PROBE_MIN_SOL on the SOL side (t0, p0, first_seen)."""
    def view(t, name):
        fs = files(segs, t, first_part_only=True)
        if not fs:
            raise Refused(f"no {t} in the segments")
        con.execute(f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet({lit(fs)}, union_by_name = true) WHERE slot >= {lo} AND slot < {hi}")
    view("arrivals", "ar")
    view("pump_amm/BuyEvent", "buy")
    view("pump_amm/SellEvent", "sell")
    con.execute(f"""CREATE TABLE tr AS
      SELECT *, CASE WHEN ix_quote_mint = '{W}' THEN 'normal' WHEN ix_base_mint = '{W}' THEN 'reversed' ELSE 'other' END AS orient,
         CASE WHEN ix_quote_mint = '{W}' THEN qres/1e9 WHEN ix_base_mint = '{W}' THEN bres/1e9 END AS sol_depth,
         CASE WHEN ix_quote_mint = '{W}' THEN (qres + vq)/nullif(bres,0) WHEN ix_base_mint = '{W}' THEN bres/nullif(qres + vq,0) END AS p
      FROM (SELECT slot, tx_index, outer_ix, inner_ix, signature, pool, pool_quote_token_reserves::DOUBLE AS qres,
                   pool_base_token_reserves::DOUBLE AS bres, coalesce(try_cast(virtual_quote_reserves AS DOUBLE), 0) AS vq, ix_quote_mint, ix_base_mint FROM buy
            UNION ALL SELECT slot, tx_index, outer_ix, inner_ix, signature, pool, pool_quote_token_reserves, pool_base_token_reserves,
                   coalesce(try_cast(virtual_quote_reserves AS DOUBLE), 0), ix_quote_mint, ix_base_mint FROM sell)""")
    con.execute("CREATE TABLE trs AS SELECT tr.*, ar.stream FROM tr LEFT JOIN ar ON ar.slot = tr.slot AND ar.tx_index = tr.tx_index AND ar.signature = tr.signature")
    con.execute("CREATE TABLE px AS SELECT pool, slot, tx_index, outer_ix, inner_ix, sol_depth AS q, p, stream, orient FROM trs WHERE p > 0 AND orient <> 'other'")
    # The first leg (slot, tx_index, outer_ix, inner_ix): E67 ordered by slot and tx_index only, so a transaction with two
    # qualifying legs gave either leg's price (3 of the explore window's 1,282 pools: same t0, another p0).
    con.execute(f"""CREATE TABLE p0 AS SELECT pool, arg_min(slot, (slot, tx_index, outer_ix, inner_ix)) AS t0,
                    arg_min(p, (slot, tx_index, outer_ix, inner_ix)) AS p0, min(slot) AS first_seen
                    FROM px WHERE pool IN (SELECT DISTINCT pool FROM px WHERE stream='tx_probes') AND q >= {PROBE_MIN_SOL} GROUP BY 1""")
    return {"probe_pools": con.execute("SELECT count(*) FROM p0").fetchone()[0],
            "by_orientation": dict(con.execute("SELECT orient, count(DISTINCT pool) FROM px WHERE pool IN (SELECT pool FROM p0) GROUP BY 1").fetchall())}


def coverage_and_counts(con, segs, lo, hi):
    """Per UTC hour of the window's blocks: coverage (level-0 share) and the recorder's counts; the window's totals."""
    def src(t):
        fs = files(segs, t)
        return f"read_parquet({lit(fs)}, union_by_name = true)" if fs else None
    con.execute(f"CREATE TABLE blk AS SELECT slot, block_time, COALESCE(skipped, false) AS skipped FROM {src('blocks')} WHERE slot >= {lo} AND slot < {hi}")
    con.execute("CREATE TABLE hr AS SELECT slot, date_trunc('hour', to_timestamp(block_time)) AS h FROM blk")
    con.execute("CREATE TABLE seg (seq INTEGER, first_slot BIGINT, last_slot BIGINT, level INTEGER)")
    con.executemany("INSERT INTO seg VALUES (?,?,?,?)", [(s["seq"], s["first_slot"], s["last_slot"], s["level"]) for s in segs])
    con.execute("""CREATE TABLE cov AS SELECT h, count(*) AS blocks, min(hr.slot) AS first_slot, max(hr.slot) AS last_slot,
                   count(DISTINCT seg.seq) AS segments, round(avg((seg.level = 0)::INT), 4) AS level0_share
                   FROM hr LEFT JOIN seg ON hr.slot BETWEEN seg.first_slot AND seg.last_slot GROUP BY 1 ORDER BY 1""")
    q = lambda sql: con.execute(sql).fetchall()  # noqa: E731
    cr, cp, ar, buy, sell = src("pump/CreateEvent"), src("pump_amm/CreatePoolEvent"), src("arrivals"), src("pump_amm/BuyEvent"), src("pump_amm/SellEvent")
    w = f"slot >= {lo} AND slot < {hi}"
    cw = f"cp.slot >= {lo} AND cp.slot < {hi}"
    con.execute(f"CREATE TABLE c_cr AS SELECT h, count(DISTINCT mint) AS creates_mints FROM {cr} JOIN hr USING (slot) WHERE {w} GROUP BY 1" if cr
                else "CREATE TABLE c_cr (h TIMESTAMPTZ, creates_mints BIGINT)")
    con.execute(f"""CREATE TABLE c_gr AS SELECT h, count(DISTINCT cp.pool) AS graduations_pools FROM {cp} cp JOIN {ar} a
                    ON a.slot = cp.slot AND a.tx_index = cp.tx_index AND a.signature = cp.signature JOIN hr ON hr.slot = cp.slot
                    WHERE a.stream = 'tx_new_pools' AND {cw} GROUP BY 1""" if cp and ar else "CREATE TABLE c_gr (h TIMESTAMPTZ, graduations_pools BIGINT)")
    parts = [f"SELECT slot FROM {x} WHERE {w}" for x in (buy, sell) if x]
    con.execute(f"CREATE TABLE c_tr AS SELECT h, count(*) AS pump_amm_buy_sell_rows_seen FROM ({' UNION ALL '.join(parts)}) JOIN hr USING (slot) GROUP BY 1"
                if parts else "CREATE TABLE c_tr (h TIMESTAMPTZ, pump_amm_buy_sell_rows_seen BIGINT)")
    con.execute("""CREATE TABLE counts AS SELECT b.h, b.blocks, b.blocks_skipped, COALESCE(c_cr.creates_mints, 0) AS creates_mints,
                   COALESCE(c_gr.graduations_pools, 0) AS graduations_pools, COALESCE(c_tr.pump_amm_buy_sell_rows_seen, 0) AS pump_amm_buy_sell_rows_seen,
                   b.first_block_time, b.last_block_time
                   FROM (SELECT date_trunc('hour', to_timestamp(block_time)) AS h, count(*) AS blocks, sum(skipped::INT) AS blocks_skipped,
                                min(block_time) AS first_block_time, max(block_time) AS last_block_time FROM blk GROUP BY 1) b
                   LEFT JOIN c_cr USING (h) LEFT JOIN c_gr USING (h) LEFT JOIN c_tr USING (h) ORDER BY 1""")
    tot = {"slots": [lo, hi], "blocks": q("SELECT count(*) FROM blk")[0][0], "blocks_skipped": q("SELECT sum(skipped::INT) FROM blk")[0][0],
           "creates_mints": q(f"SELECT count(DISTINCT mint) FROM {cr} WHERE {w}")[0][0] if cr else 0,
           "graduations_pools": q(f"""SELECT count(DISTINCT cp.pool) FROM {cp} cp JOIN {ar} a ON a.slot = cp.slot AND a.tx_index = cp.tx_index
                                     AND a.signature = cp.signature WHERE a.stream = 'tx_new_pools' AND {cw}""")[0][0] if cp and ar else 0,
           "pump_amm_buy_sell_rows_seen": q(f"SELECT count(*) FROM ({' UNION ALL '.join(parts)})")[0][0] if parts else 0,
           "block_time_first_last": list(q("SELECT min(block_time), max(block_time) FROM blk")[0])}
    return tot


def gaps(segs, lo, hi):
    """Slot ranges between consecutive segments inside [lo, hi) that no segment covers."""
    out, edge = [], lo
    for s in sorted(segs, key=lambda s: s["first_slot"]):
        if s["last_slot"] < lo or s["first_slot"] >= hi:
            continue
        if s["first_slot"] > edge:
            out.append([edge, s["first_slot"]])
        edge = max(edge, s["last_slot"] + 1)
    if edge < hi:
        out.append([edge, hi])
    return out


def journal_f7(con, journal, lo, hi):
    """The paper journal's F7 signals in [lo, hi) (mode=ro) and the (pool, t) comparison on reversed pools."""
    sq = sqlite3.connect(f"file:{journal}?mode=ro", uri=True)
    try:
        jr = sq.execute("""SELECT key, t, min(trigger_trader), min(depth), min(farm), min(status), min(orientation), min(pool_seen_slot)
                           FROM signals WHERE family = 'F7' AND t >= ? AND t < ? GROUP BY 1, 2""", (lo, hi)).fetchall()
    finally:
        sq.close()
    con.execute("CREATE TABLE live (pool VARCHAR, t BIGINT, trader VARCHAR, depth BIGINT, farm VARCHAR, status VARCHAR, orientation VARCHAR, seen BIGINT)")
    if jr:
        con.executemany("INSERT INTO live VALUES (?,?,?,?,?,?,?,?)", jr)
    a, both, b = con.execute("""SELECT sum((a.pool IS NOT NULL AND b.pool IS NULL)::INT), sum((a.pool IS NOT NULL AND b.pool IS NOT NULL)::INT),
                                sum((a.pool IS NULL AND b.pool IS NOT NULL)::INT)
                                FROM (SELECT * FROM trig WHERE orientation = 'reversed') a FULL OUTER JOIN live b ON a.pool = b.pool AND a.t = b.t""").fetchone()
    return {"journal": journal, "journal_f7_signals": len(jr), "reversed_pairs": {"segments_only": a or 0, "both": both or 0, "journal_only": b or 0}}


def run(a, log=print):
    out = os.path.normpath(a.out)
    if os.path.exists(out) or os.path.exists(out + ".tmp"):
        raise Refused(f"{out} (or .tmp) exists: written once")
    root = os.path.realpath(a.segments)
    if os.path.commonpath([os.path.realpath(os.path.dirname(out) or "."), root]) == root:
        raise Refused("the output may not be inside the segments")
    segs_all, open_ = segments(root)
    if not segs_all:
        raise Refused(f"no segment with a _segment.json under {root}")
    con = duckdb.connect(config={"memory_limit": a.memory, "threads": a.threads})
    con.execute("SET TimeZone = 'UTC'; SET preserve_insertion_order = false")
    if os.environ.get("LAB_SPILL_DIR"):
        con.execute(f"SET temp_directory = '{os.environ['LAB_SPILL_DIR']}'")
    if a.from_slot is not None:
        lo, hi = a.from_slot, a.to_slot
    else:
        lo, hi = window_slots(con, segs_all, utc(a.from_utc), utc(a.to_utc))
    if not lo < hi:
        raise Refused(f"empty window [{lo}, {hi})")
    hist = [s for s in segs_all if s["last_slot"] >= lo - HISTORY_SLOTS and s["first_slot"] < hi]
    win = [s for s in hist if s["last_slot"] >= lo]
    if not win:
        raise Refused(f"no segment overlaps [{lo}, {hi})")
    if max(s["last_slot"] for s in segs_all) < hi - 1:
        raise Refused(f"the segments end at {max(s['last_slot'] for s in segs_all)}, before the window does ({hi}): too early to run")
    ms = clock_ms(con, hist, lo, hi, a.clock)  # every block read; window mode still measures [lo, hi] only
    log(f"window [{lo}, {hi}) {len(win)} segments (+{len(hist) - len(win)} history), clock {ms}")
    if gaps(hist, lo - HISTORY_SLOTS, lo):
        log(f"WARNING: the {HISTORY_SLOTS:,} slots of history before the window are not covered: {gaps(hist, lo - HISTORY_SLOTS, lo)} "
            "(E68's route had them; triggers near the window's start and the creators of tokens made in the gap can differ)")
    info = {"label": a.label, "window_slots": [lo, hi], "window_utc": [a.from_utc, a.to_utc], "clock": a.clock,
            "ms_per_slot": {str(k): v for k, v in ms.items()}, "segments_root": root, "segments_in_window": len(win),
            "segments_history": len(hist) - len(win), "segments_still_open": open_,
            "segments_not_level0": sum(s["level"] != 0 for s in win), "segments_not_complete": sum(not s["complete"] for s in win),
            "reconnects": sum(s["reconnects"] for s in win), "slot_gaps": gaps(win, lo, hi),
            "history_gaps": gaps(hist, lo - HISTORY_SLOTS, lo), "commit": git_commit(),
            "counts_only": "no P&L, no returns"}
    info["history_complete"] = not info["history_gaps"]
    info["triggers"] = live_triggers(con, hist, lo, hi, ms)
    log(f"triggers {info['triggers']}")
    info["probe"] = probe_pools(con, win, lo, hi)
    info["recorder_counts"] = coverage_and_counts(con, win, lo, hi)
    if a.journal:
        info["journal_comparison"] = journal_f7(con, a.journal, lo, hi)
    tmp = out + ".tmp"
    os.makedirs(tmp)
    try:
        q = lambda name: os.path.join(tmp, name).replace("'", "''")  # noqa: E731
        con.execute(f"COPY (SELECT {', '.join(TRIG_COLS)} FROM trig ORDER BY t, pool, trader) TO '{q(f'live-triggers-{a.label}.parquet')}' (FORMAT parquet)")
        con.execute(f"COPY (SELECT pool, t0::UBIGINT AS t0, p0, first_seen::UBIGINT AS first_seen FROM p0 ORDER BY pool) TO '{q(f'probe-pools-{a.label}.parquet')}' (FORMAT parquet)")
        con.execute(f"COPY (SELECT * FROM cov) TO '{q('coverage-hourly.parquet')}' (FORMAT parquet)")
        con.execute(f"COPY (SELECT * FROM counts) TO '{q('counts-hourly.parquet')}' (FORMAT parquet)")
        con.execute("CREATE TABLE segs (seq INTEGER, first_slot BIGINT, last_slot BIGINT, written_unix BIGINT, level INTEGER, complete BOOLEAN, "
                    "slots_closed INTEGER, reconnects INTEGER, backfill BOOLEAN, new_reversed BOOLEAN, pump_creates BOOLEAN, tx_pools INTEGER, "
                    "tx_probes INTEGER, new_vaults INTEGER, dropped VARCHAR, in_window BOOLEAN)")
        con.executemany("INSERT INTO segs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        [(s["seq"], s["first_slot"], s["last_slot"], s["written_unix"], s["level"], s["complete"], s["slots_closed"],
                          s["reconnects"], s["backfill"], s["new_reversed"], s["pump_creates"], s["tx_pools"], s["tx_probes"],
                          s["new_vaults"], s["dropped"], s["last_slot"] >= lo) for s in hist])
        con.execute(f"COPY (SELECT * FROM segs ORDER BY seq) TO '{q('segments.parquet')}' (FORMAT parquet)")
        if a.journal:
            con.execute(f"COPY (SELECT * FROM live ORDER BY t, pool) TO '{q(f'journal-f7-{a.label}.parquet')}' (FORMAT parquet)")
        with open(os.path.join(tmp, "recorder-counts.json"), "w") as f:
            json.dump({"window": info["recorder_counts"]}, f, indent=1)
        with open(os.path.join(tmp, "info.json"), "w") as f:
            json.dump(info, f, indent=1, default=str)
        names = sorted(n for n in os.listdir(tmp))
        with open(os.path.join(tmp, "SHA256SUMS"), "w") as f:
            for n in names:
                f.write(f"{sha256(os.path.join(tmp, n))}  {n}\n")
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    os.rename(tmp, out)
    log(f"written {out}")
    return info


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--segments", required=True)
    ap.add_argument("--from-utc")
    ap.add_argument("--to-utc")
    ap.add_argument("--from-slot", type=int)
    ap.add_argument("--to-slot", type=int)
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--journal", help="the paper journal (journal.sqlite), read with mode=ro")
    ap.add_argument("--clock", choices=("epoch", "window"), default="epoch")
    ap.add_argument("--memory", default="2GB")
    ap.add_argument("--threads", type=int, default=2)
    a = ap.parse_args(argv)
    if (a.from_slot is None) == (a.from_utc is None) or (a.from_slot is None) != (a.to_slot is None) or (a.from_utc is None) != (a.to_utc is None):
        ap.error("give --from-utc and --to-utc, or --from-slot and --to-slot")
    if not a.label.replace("-", "").replace("_", "").isalnum():
        ap.error("--label: letters, digits, - and _ only")
    try:
        run(a)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
