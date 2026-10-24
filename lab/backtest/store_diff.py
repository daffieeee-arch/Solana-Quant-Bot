"""Two stores compared table by table, read-only: diagnostic (ii) of the pipeline diagnostics
(research/notes/pipeline-diagnostics-map-2026-10-24.json, part store). Dev data only; whatever it shows, no verdict
changes and nothing is re-tuned.

  python -P store_diff.py meta STORE [--expect-period P] [--expect-commit C]     # the store's meta, chunk list apart
  python -P store_diff.py inputs --ref dev_chunk_manifests.json --root CHUNKS --lo L --hi H
  python -P store_diff.py full A B --excluded-zero a --out c1.json               # C1
  python -P store_diff.py window A B --lo L --hi H --out c2.json                 # C2
  python -P store_diff.py triggers A B --lo L --hi H --code-lab LAB --strategies-sha256 P --out c4.json [--rows CSV]
                                                                                 # C4 (strategies.py from LAB, pinned)
Every command takes --spill DIR (default LAB_SPILL_DIR), --memory (DuckDB memory_limit) and --threads.

A is the store under test, B the reference. C1: A = S1 (be47fc4's store.py through the clean code path: a 1-epoch
warm-up from events/v1 before the window [L, H), backtest/nas_store_warmup_diag.sh), B = S0 (ba322be's store.py, period
dev, the same chunks). C2 and C4: A = S1, B = dev2 (ba322be, 1033-1046). Tables are compared as multisets: row count,
sum and xor of a row hash over the columns, each column read as one type on both sides (both integer: HUGEINT, since a
negative BIGINT hashes apart from the same HUGEINT; both numeric: DECIMAL(38,10); else VARCHAR), type drift listed
apart. Where the hashes differ, a keyed diff counts the rows only in A, only in B and per column the paired rows that
differ, with a few keys each. Columns that do not repeat between two builds of the same input are compared apart,
keyed, informational: arg_max over a tie in the last slot (pools.coin_creator; pool_stats.last_b, last_vault,
coin_creator), any_value (mints.pool; pools.mint where mint_source IS NULL, i.e. more than one link) and pools.part
(ntile over each store's own pool set: dev2 used LAB_POOL_PARTS=16, the clean build 4).

GREEN rule, pinned before any run (the map's C1, C2 and C4 with its checker's corrections):
  Every check: A's meta.excluded_pools_int64.pools = 0 (be47fc4 leaves out a pool with a u64 amount outside BIGINT,
    ba322be did not: the equalities below hold only where nothing is left out); both stores dev (meta.period dev or
    dev-diag-clean-path, no prereg, slots in [446,256,000, 452,304,000): never a clean or hold-out store).
  C1 (full): the same tables (meta apart) with the same columns and slot range, the same chunk slots and extractor
    builds, every table but pool_sweeps (a forward-protocol table, empty on dev) non-empty, and every table equal (the
    columns above apart). Meta otherwise informational. GREEN means ba322be equals be47fc4, the warm-up path included,
    on identical input.
  C2 (window): [L, H) is A's meta.window; A's store starts at S = L - warm-up and ends at H; B covers [S, H).
    blocks and pool rows with slot in [L, H) equal on every column (pool with farmer: with a 1-epoch warm-up every
    flag in the window has its 24 h history in A); curve rows in [L, H) equal to B's rows of mints with
    create_slot >= S (B's rows of older mints are counted as truncation by the warm-up length); mints equal to B's
    with create_slot in [S, H), B's complete_slot, migrate_slot and pool set to NULL at or after H (B's look-ahead);
    each of the four non-empty in A.
    Categories, never RED (they depend on the store window, and dev2 also looks ahead to 1046): pools (class and
    create-derived columns, per column split by whether B saw the pool's create before S or trades at or after H, and
    the F7 universe's older pools with rows in the window: mint, mayhem, coin_creator, created_in_period, creator via
    mints), pool_stats (only pools B saw entirely inside [S, H)), pool_class_daily (full UTC days with 24 h of A's
    history before them), pool_sweeps and meta (ms per slot, farmer parameters, extractor builds per chunk).
  C4 (triggers): F7 triggers with t in [L, H) from the pinned strategies.f7_signals_sql on each store, with the same
    (dev_start, dev_end) and one clock (A's ms per slot), keyed (venue, pool or mint, t). GREEN: A has a pool_reversed
    (K3) trigger; pool_reversed triggers of in-store pools (created in A's store or at or after S in B, and whose mint
    B did not create before S) equal on every column (mint, depth, orientation, trigger wallet, trigger_farmer,
    flagged); every other pool_reversed difference explained by a pool attribute that differs between the stores:
    a trigger on one side only by quote_class, mayhem, coin_creator or creator (COALESCE(mints.creator,
    pools.coin_creator), the filters f7 reads from them); a differing mint or orientation column by pools.mint or
    pools.orientation. Anything else is unexplained and RED. curve and pool_normal: the same counts, informational.
rc 0 GREEN, 1 RED (the report and the last output line say why); guards and errors exit non-zero too (fail closed). A
GREEN is a proxy scoped to the window, its slot time and its extractor build, not proof for the clean epochs.
"""

import argparse
import csv
import datetime
import hashlib
import json
import os
import re
import sys

import duckdb

EPOCH = 432_000
DEV_FIRST = 446_256_000  # epoch 1033: dev2's first slot
HOLDOUT_START = 452_304_000  # week2-defaults windows.holdout.start_slot: nothing at or after it is read
PERIODS = ("dev", "dev-diag-clean-path")  # the clean-path build's label: clean_test wants 'clean', analytics 'dev'
KEYS = {"blocks": ("slot",), "mints": ("mint",), "curve": ("mint", "slot", "tx_index", "outer_ix", "inner_ix"),
        "pools": ("pool",), "pool_stats": ("pool",), "pool": ("pool", "slot", "tx_index", "outer_ix", "inner_ix", "kind"),
        "pool_sweeps": ("pool", "slot", "tx_index", "outer_ix", "inner_ix"), "pool_class_daily": ("day", "quote_class", "mayhem")}
APART = {"pools": ("coin_creator", "part"), "pool_stats": ("last_b", "last_vault", "coin_creator"), "mints": ("pool",)}
MASK = {("pools", "mint"): 'CASE WHEN "mint_source" IS NULL THEN NULL ELSE "mint" END'}  # any_value over links: apart
EMPTY_OK = ("pool_sweeps",)
INTS = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT")
POOL_COLS = ("quote_class", "orientation", "quote_mint", "mint", "mint_source", "mayhem", "mayhem_source", "create_slot",
             "coin_creator", "can_boost", "b0", "q0", "created_in_period")
EXPLAIN_PRESENCE = ("quote_class", "mayhem", "coin_creator", "creator")  # what f7's pool universe and filters read
EXPLAIN_COLUMN = {"mint": "mint", "orientation": "orientation"}  # trigger column <- the pool attribute it is read from
TRIGGER_COLS = ("mint", "depth", "orientation", "trigger", "trigger_farmer", "flagged")


def _lit(s):
    return "'" + str(s).replace("'", "''") + "'"


def _q(c):
    return '"' + c.replace('"', '""') + '"'


def _guard_path(path):
    """The store's real path; exits for a clean or hold-out path, before anything is opened."""
    real = os.path.realpath(path)
    low = real.lower()
    if any(s in low for s in ("clean-v1", "clean-test", "holdout")) or os.path.basename(low).startswith(("clean", "holdout")):
        raise SystemExit(f"{path}: a clean or hold-out path, not a dev store")
    if not os.path.isfile(real):
        raise SystemExit(f"{path}: no such store")
    return real


def _guard_meta(meta, path):
    lo, hi = meta.get("slot_start"), meta.get("slot_end_exclusive")
    if meta.get("period") not in PERIODS or "prereg" in meta or not isinstance(lo, int) or not isinstance(hi, int) \
            or not DEV_FIRST <= lo < hi <= HOLDOUT_START:
        raise SystemExit(f"{path}: not a dev store (period {meta.get('period')!r}, slots [{lo}, {hi}))")


def _guard_window(lo, hi):
    if not DEV_FIRST <= lo < hi <= HOLDOUT_START or lo % 216_000 or hi % 216_000:
        raise SystemExit(f"window [{lo}, {hi}) must be whole dev chunks in [{DEV_FIRST}, {HOLDOUT_START})")


def connect(stores, spill, memory="1GB", threads=2):
    """(con, metas): an in-memory connection with each store of {name: path} ATTACHed READ_ONLY under its name, and
    their metas (guarded: dev stores only). Spill goes to `spill`, never beside a store: a bare read-only connect
    spills to <store>.tmp (store/dev2.duckdb.tmp)."""
    if not re.fullmatch(r"\d+(\.\d+)?\s*[KMGT]i?B", memory):
        raise SystemExit(f"memory {memory!r}: e.g. 1GB")
    if not spill:
        raise SystemExit("pass --spill (or set LAB_SPILL_DIR)")
    spill = os.path.realpath(spill)
    reals = {name: _guard_path(p) for name, p in stores.items()}
    for real in reals.values():
        d = os.path.dirname(real)
        if spill == d or spill.startswith(d + os.sep):
            raise SystemExit(f"spill {spill} lies in the store directory {d}")
    os.makedirs(spill, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET threads TO {int(threads)}")
    con.execute(f"SET memory_limit = '{memory}'")
    con.execute(f"SET temp_directory = {_lit(spill)}")
    con.execute(f"SET max_temp_directory_size = {_lit(os.environ.get('LAB_SPILL_MAX', '20GB'))}")
    con.execute("SET preserve_insertion_order = false")
    metas = {}
    for name, real in reals.items():
        con.execute(f"ATTACH {_lit(real)} AS {name} (READ_ONLY)")
        metas[name] = json.loads(con.execute(f"SELECT json FROM {name}.meta").fetchone()[0])
        _guard_meta(metas[name], stores[name])
    return con, metas


def _tables(con, db):
    return sorted(r[0] for r in con.execute(
        "SELECT table_name FROM duckdb_tables() WHERE database_name = ? AND table_name <> 'meta'", [db]).fetchall())


def _columns(con, db, table):
    return con.execute("SELECT column_name, data_type FROM duckdb_columns() WHERE database_name = ? AND table_name = ? "
                       "ORDER BY column_index", [db, table]).fetchall()


def _read_as(ta, tb):
    """The type both sides are read as (None: the same type, read as is)."""
    if ta == tb:
        return None
    if ta in INTS and tb in INTS:
        return "HUGEINT"
    num = lambda t: t in INTS or t in ("FLOAT", "DOUBLE", "REAL") or t.startswith("DECIMAL")  # noqa: E731
    return "DECIMAL(38,10)" if num(ta) and num(tb) else "VARCHAR"


def columns(con, table):
    """(cols, drift, one_side): {name: SQL expression read the same on both sides} for the columns of a.table and
    b.table, in A's order; drift [(column, type A, type B)]; one_side [(column, 'a' or 'b')]."""
    ca, cb = dict(_columns(con, "a", table)), dict(_columns(con, "b", table))
    cols, drift = {}, []
    for c, ta in ca.items():
        if c not in cb:
            continue
        cast = _read_as(ta, cb[c])
        e = MASK.get((table, c), _q(c))
        cols[c] = f"({e})::{cast}" if cast else e
        if cast:
            drift.append([c, ta, cb[c]])
    one_side = [[c, "a"] for c in ca if c not in cb] + [[c, "b"] for c in cb if c not in ca]
    return cols, drift, one_side


def triple(con, src, exprs):
    """[rows, sum, xor] of the row hash over exprs: equal triples mean equal multisets (up to a hash collision)."""
    h = f"hash({', '.join(exprs)})"
    return list(con.execute(f"SELECT count(*), sum({h}::HUGEINT), bit_xor({h}) FROM {src}").fetchone())


def keyed_diff(con, src_a, src_b, key, cols, examples=5):
    """Rows of A and B paired on the key (a row number per key value, in row-hash order, pairs duplicates): rows only in
    A, only in B, and per non-key column the pairs whose values differ, each with up to `examples` keys. The differing
    rows stay in TEMP TABLE d_ (columns a0.., b0.. in the order of cols, ar/br the row numbers) for the caller."""
    names = list(cols)
    sel = ", ".join(f"{e} AS c{i}" for i, e in enumerate(cols.values()))
    k = [names.index(c) for c in key]
    h = "hash(" + ", ".join(f"c{i}" for i in range(len(names))) + ")"
    side = lambda src: (f"SELECT *, {h} AS h_, row_number() OVER (PARTITION BY {', '.join(f'c{i}' for i in k)} "  # noqa: E731
                        f"ORDER BY {h}) AS rn_ FROM (SELECT {sel} FROM {src})")
    on = " AND ".join(f"x.c{i} IS NOT DISTINCT FROM y.c{i}" for i in k)
    both = ", ".join(f"x.c{i} AS a{i}, y.c{i} AS b{i}" for i in range(len(names)))
    con.execute(f"""CREATE OR REPLACE TEMP TABLE d_ AS
                    SELECT {both}, x.rn_ AS ar, y.rn_ AS br FROM ({side(src_a)}) x FULL OUTER JOIN ({side(src_b)}) y
                      ON {on} AND x.rn_ = y.rn_
                    WHERE x.rn_ IS NULL OR y.rn_ IS NULL OR x.h_ <> y.h_""")

    def part(where, s):
        n = con.execute(f"SELECT count(*) FROM d_ WHERE {where}").fetchone()[0]
        ks = con.execute(f"SELECT {', '.join(f'{s}{i}' for i in k)} FROM d_ WHERE {where} ORDER BY ALL LIMIT {int(examples)}").fetchall()
        return {"rows": n, "keys": [[str(v) for v in r] for r in ks]}

    out = {"only_a": part("br IS NULL", "a"), "only_b": part("ar IS NULL", "b"), "columns": {}}
    for i, c in enumerate(names):
        if i not in k:
            d = part(f"ar IS NOT NULL AND br IS NOT NULL AND a{i} IS DISTINCT FROM b{i}", "a")
            if d["rows"]:
                out["columns"][c] = d
    return out


def _apart(con, table, src_a, src_b):
    """Per column compared apart: the keys in both stores whose value differs (informational)."""
    on = " AND ".join(f"x.{_q(c)} = y.{_q(c)}" for c in KEYS[table])
    out = {}
    for c in APART.get(table, ()):
        out[c] = con.execute(f"SELECT count(*) FROM {src_a} x JOIN {src_b} y ON {on} "
                             f"WHERE x.{_q(c)} IS DISTINCT FROM y.{_q(c)}").fetchone()[0]
    if table == "pools":
        out["mint (mint_source NULL)"] = con.execute(
            f"SELECT count(*) FROM {src_a} x JOIN {src_b} y ON {on} WHERE (x.mint_source IS NULL OR y.mint_source IS NULL) "
            "AND x.mint IS DISTINCT FROM y.mint").fetchone()[0]
    return out


def compare(con, table, src_a, src_b, examples=5):
    """One table: rows, the multiset hash on both sides (columns compared apart left out), type drift, columns on one
    side only, the apart columns and, where the hashes differ, the keyed diff."""
    cols, drift, one_side = columns(con, table)
    hashed = [e for c, e in cols.items() if c not in APART.get(table, ())]
    x, y = triple(con, src_a, hashed), triple(con, src_b, hashed)
    r = {"rows_a": x[0], "rows_b": y[0], "equal": x == y and not one_side, "hash_a": x, "hash_b": y,
         "type_drift": drift, "one_side": one_side}
    if table in KEYS and all(k in cols for k in KEYS[table]):
        if table in APART:
            r["apart"] = _apart(con, table, src_a, src_b)
        if x != y:
            r["diff"] = keyed_diff(con, src_a, src_b, KEYS[table], {c: e for c, e in cols.items() if c not in APART.get(table, ())},
                                   examples)
    return r


def _excluded_zero(meta, red, side):
    ex = meta.get("excluded_pools_int64")
    if not isinstance(ex, dict) or ex.get("pools") != 0:
        red.append(f"{side}: meta.excluded_pools_int64 is {ex!r}, not 0 pools")


def _chunks(meta):
    return [[c["slots"][0], c["slots"][1], c.get("extractor_sha256")] for c in meta.get("chunk_list", [])]


# ----------------------------------------------------------------------------- C1


def full(con, metas, excluded_zero="a", examples=5):
    """C1: every table of A against B's, whole stores (see the module docstring for the GREEN rule)."""
    red = []
    _excluded_zero(metas[excluded_zero], red, excluded_zero)
    ma, mb = metas["a"], metas["b"]
    if [ma["slot_start"], ma["slot_end_exclusive"]] != [mb["slot_start"], mb["slot_end_exclusive"]]:
        red.append("the stores cover different slots")
    if _chunks(ma) != _chunks(mb):
        red.append("the chunk slots or extractor builds differ")
    ta, tb = _tables(con, "a"), _tables(con, "b")
    if ta != tb:
        red.append(f"tables differ: only in A {sorted(set(ta) - set(tb))}, only in B {sorted(set(tb) - set(ta))}")
    tables = {}
    for t in sorted(set(ta) & set(tb)):
        r = tables[t] = compare(con, t, f"a.{t}", f"b.{t}", examples)
        if not r["equal"]:
            red.append(f"{t} differs")
        elif r["rows_a"] == 0 and t not in EMPTY_OK:
            red.append(f"{t} is empty")
    skip = ("chunk_list", "built_at_unix", "build_seconds")
    meta = {k: [ma.get(k), mb.get(k)] for k in sorted(set(ma) | set(mb)) if k not in skip and ma.get(k) != mb.get(k)}
    return {"check": "C1", "green": not red, "red": red, "tables": tables, "meta_differs": meta}


# ----------------------------------------------------------------------------- C2


def window(con, metas, lo, hi, examples=5):
    """C2: A's window [lo, hi) against B (see the module docstring for the GREEN rule and the categories)."""
    _guard_window(lo, hi)
    ma, mb = metas["a"], metas["b"]
    s = ma["slot_start"]
    if ma["slot_end_exclusive"] != hi or ma.get("window", [lo, hi]) != [lo, hi] or not s <= lo:
        raise SystemExit(f"A's store [{s}, {ma['slot_end_exclusive']}) with window {ma.get('window')} does not end at "
                         f"the window [{lo}, {hi})")
    if not (mb["slot_start"] <= s and mb["slot_end_exclusive"] >= hi):
        raise SystemExit(f"B [{mb['slot_start']}, {mb['slot_end_exclusive']}) does not cover A's store [{s}, {hi})")
    red = []
    _excluded_zero(ma, red, "a")
    w = f"slot >= {lo} AND slot < {hi}"
    life = (f"CASE WHEN complete_slot >= {hi} THEN NULL ELSE complete_slot END AS complete_slot, "
            f"CASE WHEN migrate_slot >= {hi} THEN NULL ELSE migrate_slot END AS migrate_slot, "
            f"CASE WHEN migrate_slot >= {hi} THEN NULL ELSE pool END AS pool")
    src = {
        "blocks": (f"(SELECT * FROM a.blocks WHERE {w})", f"(SELECT * FROM b.blocks WHERE {w})"),
        "pool": (f"(SELECT * FROM a.pool WHERE {w})", f"(SELECT * FROM b.pool WHERE {w})"),
        "curve": (f"(SELECT * FROM a.curve WHERE {w})",
                  f"(SELECT * FROM b.curve WHERE {w} AND mint IN (SELECT mint FROM b.mints WHERE create_slot >= {s}))"),
        "mints": ("a.mints", f"(SELECT * REPLACE ({life}) FROM b.mints WHERE create_slot >= {s} AND create_slot < {hi})"),
    }
    exact = {}
    for t, (sa, sb) in src.items():
        r = exact[t] = compare(con, t, sa, sb, examples)
        if not r["equal"]:
            red.append(f"{t} differs in the window")
        if r["rows_a"] == 0:
            red.append(f"{t} has no rows in A's window")
        if t == "pool" and "diff" in r:
            r["one_sided_by_pool"] = _pool_one_sided(con, list(columns(con, "pool")[0]).index("pool"))
    exact["curve"]["remainder_b"] = con.execute(f"SELECT count(*) FROM b.curve WHERE {w}").fetchone()[0] - exact["curve"]["rows_b"]
    cats = {"pools": _pools_categories(con, s, lo, hi), "pool_stats": _pool_stats_contained(con, s, hi, examples),
            "pool_class_daily": _class_daily(con, examples),
            "pool_sweeps": [con.execute(f"SELECT count(*) FROM {d}.pool_sweeps WHERE {w}").fetchone()[0] for d in "ab"],
            "meta": _meta_window(ma, mb)}
    return {"check": "C2", "green": not red, "red": red, "window": [lo, hi], "store_start_a": s, "exact": exact,
            "categories": cats}


def _pool_one_sided(con, i):
    """Pool rows on one side only (TEMP TABLE d_ of keyed_diff), by the pool's presence and class in the other store."""
    out = {}
    for side, other, cond in (("a", "b", "br IS NULL"), ("b", "a", "ar IS NULL")):
        out[f"only_{side}"] = {r[0]: r[1] for r in con.execute(f"""
            SELECT CASE WHEN o.pool IS NULL THEN 'pool absent in {other.upper()}'
                        WHEN o.quote_class IS DISTINCT FROM p.quote_class THEN 'pool of another class in {other.upper()}'
                        ELSE 'pool present, same class' END, count(*)
            FROM d_ LEFT JOIN {side}.pools p ON p.pool = d_.{side}{i} LEFT JOIN {other}.pools o ON o.pool = d_.{side}{i}
            WHERE {cond} GROUP BY 1 ORDER BY 1""").fetchall()}
    return out


def _pools_categories(con, s, lo, hi):
    """pools of both stores, paired: per column the pools that differ, by group (created in A's store or older) and by
    cause (B saw the create before S: warm-up length; B saw trades at or after H: look-ahead; other). part is left out.
    Then the F7 universe's older pools with buy/sell rows in A's window: mint, mayhem, coin_creator, created_in_period
    and creator via mints, by A's orientation."""
    pairs = ", ".join(f"x.{c} AS a_{c}, y.{c} AS b_{c}" for c in POOL_COLS)
    con.execute(f"""CREATE OR REPLACE TEMP TABLE pm_ AS
        SELECT x.pool, CASE WHEN x.created_in_period THEN 'created_in_a' ELSE 'older' END AS grp,
               CASE WHEN y.create_slot < {s} THEN 'b_created_before_start' WHEN bs.last_slot >= {hi} THEN 'b_trades_after_end'
                    ELSE 'other' END AS cause, {pairs}
        FROM a.pools x JOIN b.pools y USING (pool) LEFT JOIN b.pool_stats bs USING (pool)""")
    out = {"a": con.execute("SELECT count(*) FROM a.pools").fetchone()[0],
           "paired": con.execute("SELECT count(*) FROM pm_").fetchone()[0],
           "a_only": con.execute("SELECT count(*) FROM a.pools WHERE pool NOT IN (SELECT pool FROM b.pools)").fetchone()[0],
           "b_only_with_rows_in_window": con.execute(f"""SELECT count(DISTINCT pool) FROM b.pool WHERE slot >= {lo} AND slot < {hi}
                AND pool NOT IN (SELECT pool FROM a.pools)""").fetchone()[0],
           "part": "left out (ntile over each store's own pool set)", "columns": {}}
    for c in POOL_COLS:
        rows = con.execute(f"SELECT grp, cause, count(*) FROM pm_ WHERE a_{c} IS DISTINCT FROM b_{c} GROUP BY ALL ORDER BY ALL").fetchall()
        if rows:
            out["columns"][c] = {f"{g}/{k}": n for g, k, n in rows}
    con.execute(f"""CREATE OR REPLACE TEMP TABLE pu_ AS
        SELECT pm_.*, ma.creator AS a_creator, mb.creator AS b_creator FROM pm_
        LEFT JOIN a.mints ma ON ma.mint = pm_.a_mint LEFT JOIN b.mints mb ON mb.mint = pm_.b_mint
        WHERE grp = 'older' AND (a_quote_class IN ('sol', 'reversed') OR b_quote_class IN ('sol', 'reversed'))
          AND NOT (COALESCE(a_mayhem, false) AND COALESCE(b_mayhem, false))
          AND pm_.pool IN (SELECT pool FROM a.pool WHERE kind IN ('buy', 'sell') AND slot >= {lo} AND slot < {hi})""")
    u = {"pools": dict(con.execute("SELECT a_orientation, count(*) FROM pu_ GROUP BY 1 ORDER BY 1").fetchall())}
    for c, cond in (("mint", "a_mint IS DISTINCT FROM b_mint"),
                    ("mayhem", "COALESCE(a_mayhem, false) <> COALESCE(b_mayhem, false)"),
                    ("coin_creator", "a_coin_creator IS DISTINCT FROM b_coin_creator"),
                    ("created_in_period", "a_created_in_period IS DISTINCT FROM b_created_in_period"),
                    ("creator", "COALESCE(a_creator, a_coin_creator) IS DISTINCT FROM COALESCE(b_creator, b_coin_creator)")):
        u[c] = dict(con.execute(f"SELECT a_orientation, count(*) FROM pu_ WHERE {cond} GROUP BY 1 ORDER BY 1").fetchall())
    out["f7_universe_older_pools_in_window"] = u
    return out


def _pool_stats_contained(con, s, hi, examples):
    """pool_stats of the pools B saw entirely inside A's store [S, H): those aggregate the same trades."""
    inside = f"SELECT pool FROM b.pool_stats WHERE first_slot >= {s} AND last_slot < {hi}"
    r = compare(con, "pool_stats", f"(SELECT * FROM a.pool_stats WHERE pool IN ({inside}))",
                f"(SELECT * FROM b.pool_stats WHERE pool IN ({inside}))", examples)
    r["pools"] = r["rows_b"]
    return r


def _class_daily(con, examples):
    """pool_class_daily of the full UTC days with at least 24 h of A's blocks before them (informational: the class
    and mayhem of a pool depend on the window)."""
    first, last = con.execute("SELECT min(block_time), max(block_time) FROM a.blocks").fetchone()
    if first is None:
        return {"days": []}
    d0, d1 = -(-(first + 86_400) // 86_400), (last + 1) // 86_400  # first full day after 24 h, end of the last full day
    days = [str(datetime.date(1970, 1, 1) + datetime.timedelta(days=d)) for d in range(d0, d1)]
    if not days:
        return {"days": []}
    cond = f"day >= DATE '{days[0]}' AND day <= DATE '{days[-1]}'"
    r = compare(con, "pool_class_daily", f"(SELECT * FROM a.pool_class_daily WHERE {cond})",
                f"(SELECT * FROM b.pool_class_daily WHERE {cond})", examples)
    r["days"] = days
    return r


def _meta_window(ma, mb):
    ms = {e: [v, mb.get("ms_per_slot", {}).get(e)] for e, v in sorted(ma.get("ms_per_slot", {}).items())}
    bx = {(c[0], c[1]): c[2] for c in _chunks(mb)}
    ex = {f"{c[0]}-{c[1]}": [c[2], bx.get((c[0], c[1]))] for c in _chunks(ma)}
    mt = {(c["slots"][0], c["slots"][1]): c.get("manifest_mtime") for c in mb.get("chunk_list", [])}
    return {"ms_per_slot": ms, "ms_per_slot_equal": all(a == b for a, b in ms.values()),
            "farmer_equal": ma.get("farmer") == mb.get("farmer"), "extractor_sha256": ex,
            "extractor_equal": all(a == b for a, b in ex.values()),
            "manifest_mtime_equal": sum(1 for c in ma.get("chunk_list", [])
                                        if mt.get((c["slots"][0], c["slots"][1])) == c.get("manifest_mtime")),
            "excluded_pools_int64_a": ma.get("excluded_pools_int64"), "git_commit": [ma.get("git_commit"), mb.get("git_commit")]}


# ----------------------------------------------------------------------------- C4


def f7_range(decision_bounds, clock, lo, hi):
    """(dev_start, dev_end) whose decision range [a, b] covers [lo, hi - 1] with a <= lo and b >= hi - 1 at the nearest
    slots (f7_signals_sql's trig_range then cuts it to exactly that): dev_start sets the curve universe (mints created
    at or after a), the burn-in before lo and the end guard after hi are data only, as tournament.build_signals'
    slot_range does. Triggers look backward only, so the window's last end guard is kept on both stores."""
    d = lo - (decision_bounds({}, lo, hi, clock)[0] - lo)
    e = hi + (hi - decision_bounds({}, lo, hi, clock)[1])
    for _ in range(100_000):
        a, b = decision_bounds({}, d, e, clock)
        if a > lo:
            d -= 1
        elif b < hi - 1:
            e += 1
        elif decision_bounds({}, d + 1, e, clock)[0] <= lo:
            d += 1
        else:
            return d, e
    raise SystemExit("no decision range found for the window")


def _segment(row):
    return "curve" if row[0] == "curve" else f"pool_{row[5]}"


def f7_triggers(con, strategies, clock, lo, hi):
    """{key: row}: f7_signals_sql's triggers with t in [lo, hi) on one store (bare table names: the caller's USE),
    key (venue, pool or mint, t), row (venue, mint, t, depth, pool, orientation, trigger, trigger_farmer, flagged)."""
    dev_start, dev_end = f7_range(strategies.decision_bounds, clock, lo, hi)
    out = {}
    for r in strategies.f7_signals_sql(con, {}, dev_start, dev_end, clock=clock, trig_range=(lo, hi - 1)):
        r = tuple(r)
        k = (r[0], r[4] if r[0] == "pool" else r[1], r[2])
        if k in out or not lo <= r[2] < hi:
            raise SystemExit(f"trigger {k} twice or outside the window")
        out[k] = r
    return out


def _pool_attrs(con, db):
    """{pool: {quote_class, orientation, mayhem (NULL as false), mint, coin_creator, creator (COALESCE of the mint's
    creator and coin_creator, as f7), created_in_period, create_slot, mint_create_slot}} of one store, for the pools
    of TEMP TABLE dp_."""
    rows = con.execute(f"""SELECT p.pool, p.quote_class, p.orientation, COALESCE(p.mayhem, false), p.mint, p.coin_creator,
                                  COALESCE(m.creator, p.coin_creator), p.created_in_period, p.create_slot, m.create_slot
                           FROM {db}.pools p LEFT JOIN {db}.mints m ON m.mint = p.mint
                           WHERE p.pool IN (SELECT pool FROM dp_)""").fetchall()
    names = ("quote_class", "orientation", "mayhem", "mint", "coin_creator", "creator", "created_in_period", "create_slot",
             "mint_create_slot")
    return {r[0]: dict(zip(names, r[1:])) for r in rows}


def triggers(stores, strategies, lo, hi, spill, memory="1GB", threads=2, examples=5):
    """C4 on {a: path, b: path} with a strategies module (on the NAS the be47fc4 tree's, checked by main()): (report,
    rows) where rows are every trigger of both sides for the archive (see the module docstring for the GREEN rule)."""
    _guard_window(lo, hi)
    con, metas = connect(stores, spill, memory, threads)
    ma, mb = metas["a"], metas["b"]
    s = ma["slot_start"]
    if not (s <= lo and ma["slot_end_exclusive"] >= hi and mb["slot_start"] <= s and mb["slot_end_exclusive"] >= hi):
        raise SystemExit(f"the stores do not cover the window [{lo}, {hi}) and A's store start {s}")
    clock = strategies.Clock(ma["ms_per_slot"])
    trig = {}
    for side in ("a", "b"):
        one, _ = connect({side: stores[side]}, spill, memory, threads)
        one.execute(f"USE {side}")
        trig[side] = f7_triggers(one, strategies, clock, lo, hi)
        one.close()
    ta, tb = trig["a"], trig["b"]
    diff = sorted(k for k in set(ta) | set(tb) if ta.get(k) != tb.get(k))
    con.execute("CREATE OR REPLACE TEMP TABLE dp_ (pool VARCHAR)")
    pools = sorted({(k[1],) for k in diff if k[0] == "pool"})
    if pools:
        con.executemany("INSERT INTO dp_ VALUES (?)", pools)
    pa, pb = _pool_attrs(con, "a"), _pool_attrs(con, "b")
    segs = {}
    for k in set(ta) | set(tb):
        for g in {_segment(r) for r in (ta.get(k), tb.get(k)) if r}:
            seg = segs.setdefault(g, {"a": 0, "b": 0, "equal": 0, "only_a": 0, "only_b": 0, "differ": 0, "in_store": 0,
                                      "explained": {}, "unexplained": 0, "examples": []})
            seg["a"] += k in ta
            seg["b"] += k in tb
            if ta.get(k) == tb.get(k):
                seg["equal"] += 1
                continue
            seg["only_a" if k not in tb else "only_b" if k not in ta else "differ"] += 1
            why, in_store = _explain(k, ta.get(k), tb.get(k), pa.get(k[1]), pb.get(k[1]), s)
            if in_store:
                seg["in_store"] += 1
            if why:
                seg["explained"][why] = seg["explained"].get(why, 0) + 1
            else:
                seg["unexplained"] += 1
            if len(seg["examples"]) < examples:
                seg["examples"].append({"key": [str(v) for v in k], "a": _row(ta.get(k)), "b": _row(tb.get(k)),
                                        "why": why or "unexplained", "in_store": in_store})
    red = []
    _excluded_zero(ma, red, "a")
    rev = segs.get("pool_reversed", {})
    if not rev.get("a"):
        red.append("A has no pool_reversed trigger in the window")
    if rev.get("in_store"):
        red.append(f"{rev['in_store']} pool_reversed trigger(s) of in-store pools differ")
    if rev.get("unexplained"):
        red.append(f"{rev['unexplained']} pool_reversed difference(s) unexplained by a pool attribute")
    ms_b = {e: mb.get("ms_per_slot", {}).get(e) for e in ma.get("ms_per_slot", {})}
    report = {"check": "C4", "green": not red, "red": red, "window": [lo, hi], "store_start_a": s,
              "f7_range": list(f7_range(strategies.decision_bounds, clock, lo, hi)),
              "clock": {"ms_per_slot_a": ma.get("ms_per_slot"), "ms_per_slot_b_same_epochs": ms_b,
                        "regimes": [list(r) for r in clock.regimes(lo, hi)]},
              "segments": dict(sorted(segs.items()))}
    rows = [("a",) + _row(r, True) for r in ta.values()] + [("b",) + _row(r, True) for r in tb.values()]
    return report, sorted(rows, key=lambda r: [str(v) for v in r])


def _explain(k, ra, rb, pa, pb, s):
    """(why, in_store) of one differing trigger. in_store: a pool created in A's store or at or after S in B, whose
    mint B did not create before S (its whole history is in A). why: the pool attributes that differ between the stores
    and explain the difference ('' when none does): a trigger on one side only by EXPLAIN_PRESENCE, differing trigger
    columns only when each is read from a differing pool attribute (EXPLAIN_COLUMN). A curve trigger or a pool absent
    from one store has no explanation here."""
    if k[0] != "pool":
        return "", False
    in_store = bool((pa and pa["created_in_period"]) or (pb and pb["create_slot"] is not None and pb["create_slot"] >= s)) \
        and not (pb and pb["mint_create_slot"] is not None and pb["mint_create_slot"] < s)
    if not pa or not pb:
        return "", in_store
    if ra is None or rb is None:
        return ",".join(c for c in EXPLAIN_PRESENCE if pa[c] != pb[c]), in_store
    cols = [c for c, i in zip(TRIGGER_COLS, (1, 3, 5, 6, 7, 8)) if ra[i] != rb[i]]
    if all(c in EXPLAIN_COLUMN and pa[EXPLAIN_COLUMN[c]] != pb[EXPLAIN_COLUMN[c]] for c in cols):
        return ",".join(EXPLAIN_COLUMN[c] for c in cols), in_store
    return "", in_store


def _row(r, flat=False):
    """A trigger row for the report (strings) or, flat, for the CSV: (venue, segment, mint, t, depth, pool,
    orientation, trigger, trigger_farmer, flagged)."""
    if r is None:
        return None
    if flat:
        return (r[0], _segment(r)) + tuple(r[1:])
    return [None if v is None else str(v) for v in r]


def pinned_strategies(code_lab, sha_prefix):
    """backtest.strategies imported from code_lab (on PYTHONPATH: the NAS script runs this file from the be47fc4 tree,
    never main's package), its sha256 checked against the pinned prefix of at least 16 hex digits."""
    from backtest import strategies

    src, lab = os.path.realpath(strategies.__file__), os.path.realpath(code_lab)
    if not src.startswith(lab + os.sep):
        raise SystemExit(f"strategies.py from {src}, not from {lab}")
    with open(src, "rb") as f:
        got = hashlib.sha256(f.read()).hexdigest()
    if len(sha_prefix or "") < 16 or not got.startswith(sha_prefix.lower()):
        raise SystemExit(f"{src}: sha256 {got} is not the pinned {sha_prefix}")
    print("STRATEGIES_PY", src, got, flush=True)
    return strategies


# ----------------------------------------------------------------------------- inputs and meta


def inputs(ref_path, root, lo, hi):
    """The complete chunks under root that tile [lo, hi), each manifest equal to the reference (status,
    extractor_sha256, threads, sha256 of its row counts): the chunks a build reads are the ones dev2 was built from.
    Only directories named <lo>-<hi> inside the range are opened (never a hold-out manifest)."""
    _guard_window(lo, hi)
    real = os.path.realpath(root)
    if any(s in real.lower() for s in ("clean-v1", "clean-test", "holdout")):
        raise SystemExit(f"{root}: a clean or hold-out root")
    with open(ref_path) as f:
        ref = json.load(f)["chunks"]
    red, chunks, edge = [], {}, lo
    for name in sorted(os.listdir(real), key=lambda n: [int(x) for x in n.split("-")] if re.fullmatch(r"\d+-\d+", n) else [0]):
        m = re.fullmatch(r"(\d+)-(\d+)", name)
        if not m or not (lo <= int(m[1]) and int(m[2]) <= hi):
            continue
        with open(os.path.join(real, name, "_manifest.json")) as f:
            man = json.load(f)
        rows = json.dumps(man.get("rows", {}), sort_keys=True, separators=(",", ":"))
        got = {"status": man.get("status"), "extractor_sha256": man.get("extractor_sha256"), "threads": man.get("threads"),
               "rows_sha256": hashlib.sha256(rows.encode()).hexdigest()}
        chunks[name] = {"equal": got == ref.get(name), "got": got, "ref": ref.get(name)}
        if (man.get("slot_start"), man.get("slot_end_exclusive")) != (int(m[1]), int(m[2])):
            red.append(f"{name}: the manifest says [{man.get('slot_start')}, {man.get('slot_end_exclusive')})")
        elif got["status"] == "complete":
            if int(m[1]) != edge:
                red.append(f"gap [{edge}, {m[1]})")
            edge = int(m[2])
        if not chunks[name]["equal"]:
            red.append(f"{name}: manifest differs from the reference")
    if edge != hi:
        red.append(f"gap [{edge}, {hi})")
    return {"check": "inputs", "green": not red, "red": red, "root": real, "chunks": chunks}


def show_meta(path, spill, expect_period=None, expect_commit=None):
    """The store's meta without its chunk list (guarded), and whether it is the expected period and commit."""
    con, metas = connect({"a": path}, spill)
    m = metas["a"]
    con.close()
    red = []
    if expect_period and m.get("period") != expect_period:
        red.append(f"period {m.get('period')!r}, not {expect_period!r}")
    if expect_commit and not str(m.get("git_commit") or "").startswith(expect_commit):
        red.append(f"git_commit {m.get('git_commit')!r}, not {expect_commit}")
    return {"check": "meta", "green": not red, "red": red, "store": os.path.realpath(path),
            "bytes": os.path.getsize(path), "meta": {k: v for k, v in m.items() if k != "chunk_list"},
            "chunks": _chunks(m)}


# ----------------------------------------------------------------------------- CLI


def _summary(r):
    if r["check"] == "C1":
        for t, x in r["tables"].items():
            print(t, "rows", x["rows_a"], x["rows_b"], "equal" if x["equal"] else "DIFFER", "drift" if x["type_drift"] else "",
                  f"apart {x['apart']}" if "apart" in x else "")
    elif r["check"] == "C2":
        for t, x in r["exact"].items():
            print(t, "rows", x["rows_a"], x["rows_b"], "equal" if x["equal"] else "DIFFER", "drift" if x["type_drift"] else "",
                  f"apart {x['apart']}" if "apart" in x else "", f"remainder_b {x['remainder_b']}" if "remainder_b" in x else "")
        print("pools", json.dumps(r["categories"]["pools"]["columns"], sort_keys=True)[:2000])
    elif r["check"] == "C4":
        for g, x in r["segments"].items():
            print(g, {k: v for k, v in x.items() if k != "examples"})
    elif r["check"] == "inputs":
        for n, x in r["chunks"].items():
            print(n, "equal" if x["equal"] else "DIFFER", (x["got"]["extractor_sha256"] or "")[:16], x["got"]["threads"])
    else:
        print(json.dumps({k: r["meta"].get(k) for k in ("period", "slot_start", "slot_end_exclusive", "git_commit", "counts")}))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Two stores compared table by table, read-only (diagnostic (ii)).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, window=False):
        p.add_argument("--spill", default=os.environ.get("LAB_SPILL_DIR"), help="DuckDB spill (default LAB_SPILL_DIR)")
        p.add_argument("--memory", default="1GB")
        p.add_argument("--threads", type=int, default=2)
        p.add_argument("--examples", type=int, default=5)
        p.add_argument("--out", help="the JSON report (a new file)")
        if window:
            p.add_argument("--lo", type=int, required=True)
            p.add_argument("--hi", type=int, required=True)
        return p

    p = common(sub.add_parser("meta"))
    p.add_argument("store")
    p.add_argument("--expect-period")
    p.add_argument("--expect-commit")
    p = common(sub.add_parser("inputs"), window=True)
    p.add_argument("--ref", required=True)
    p.add_argument("--root", required=True)
    for name in ("full", "window", "triggers"):
        p = common(sub.add_parser(name), window=name != "full")
        p.add_argument("a")
        p.add_argument("b")
        if name == "full":
            p.add_argument("--excluded-zero", choices=("a", "b"), default="a")
        if name == "triggers":
            p.add_argument("--code-lab", required=True, help="the lab/ tree whose backtest.strategies is pinned")
            p.add_argument("--strategies-sha256", required=True)
            p.add_argument("--rows", help="CSV of every trigger of both stores (a new file)")
    a = ap.parse_args(argv)
    rows = None
    if a.cmd == "meta":
        report = show_meta(a.store, a.spill, a.expect_period, a.expect_commit)
    elif a.cmd == "inputs":
        report = inputs(a.ref, a.root, a.lo, a.hi)
    elif a.cmd == "triggers":
        report, rows = triggers({"a": a.a, "b": a.b}, pinned_strategies(a.code_lab, a.strategies_sha256), a.lo, a.hi,
                                a.spill, a.memory, a.threads, a.examples)
    else:
        con, metas = connect({"a": a.a, "b": a.b}, a.spill, a.memory, a.threads)
        report = full(con, metas, a.excluded_zero, a.examples) if a.cmd == "full" else window(con, metas, a.lo, a.hi, a.examples)
    if a.cmd in ("full", "window", "triggers"):
        report["stores"] = {"a": os.path.realpath(a.a), "b": os.path.realpath(a.b)}
    if a.out:
        with open(a.out, "x") as f:
            json.dump(report, f, indent=1, default=str)
            f.write("\n")
    if rows is not None and a.rows:
        with open(a.rows, "x", newline="") as f:
            w = csv.writer(f)
            w.writerow(("side", "venue", "segment", "mint", "t", "depth", "pool", "orientation", "trigger", "trigger_farmer",
                        "flagged"))
            w.writerows(rows)
    _summary(report)
    print(report["check"], "GREEN" if report["green"] else "RED: " + "; ".join(report["red"]), flush=True)
    return 0 if report["green"] else 1


if __name__ == "__main__":
    sys.exit(main())
