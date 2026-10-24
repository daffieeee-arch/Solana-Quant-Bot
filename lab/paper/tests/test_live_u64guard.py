"""The u64 guard (deploy-u64guard-2026-10-24): a PumpSwap row with an amount pool_select casts to BIGINT outside its
range makes pool_select raise (a ConversionException, or an overflow of e = vault + vq), so lab-paper would exit on the
same segment at every restart. Three synthetic segments built from the repo's smoke1054 rows (pools created live):
the guard leaves such a pool out from its first such row on (events, backfill history), counts and logs it, and every
other pool's events and history equal a run in which those rows never existed. No store, no recorder data."""

import json
import os

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backtest.store import Chunk
from paper import live
from paper.live import LiveFeed, int64_select, pool_select, segment_dirs

FIX = os.path.join(os.path.dirname(live.__file__), "..", "backtest", "tests", "fixtures", "smoke1054_multihop", "pump_amm")
HUGE = 9_991_111_111_970_383_772  # E20's base reserves in epoch 1061, > 2^63 - 1
P, Q, R, U, V, W = (f"{x}U64" + x.lower() * 40 for x in "PQRUVW")  # U: never created
SEGS = ((1, 1_000, 1_999), (2, 2_000, 2_999), (3, 3_000, 3_999))
# (table, pool, slot, tx, overrides). Segment 2: P's base reserves and R's e = vault + vq leave BIGINT (U's too, an
# unknown pool). Segment 3: W's own row; V's row only in the segment's backfill.
ROWS = [("BuyEvent", P, 1_100, 1, {}), ("SellEvent", Q, 1_150, 1, {}), ("BuyEvent", R, 1_200, 1, {}),
        ("BuyEvent", V, 1_300, 1, {}), ("BuyEvent", W, 1_400, 1, {}),
        ("BuyEvent", P, 2_100, 1, {"pool_base_token_reserves": HUGE}), ("BuyEvent", Q, 2_150, 1, {}),
        ("SellEvent", P, 2_200, 1, {}), ("BuyEvent", V, 2_250, 1, {}),
        ("BuyEvent", R, 2_300, 1, {"pool_quote_token_reserves": 2 ** 63 - 10, "virtual_quote_reserves": "'100'"}),
        ("BuyEvent", U, 2_400, 1, {"pool_base_token_reserves": HUGE}),
        ("BuyEvent", P, 3_100, 1, {}), ("SellEvent", V, 3_150, 1, {}), ("SellEvent", Q, 3_200, 1, {}),
        ("BuyEvent", W, 3_300, 1, {"pool_base_token_reserves": HUGE})]
# Backfills: segment 2's row of W lies after the segment (pending, then inside segment 3, where W is found); segment
# 3's rows are history, V's with base reserves outside BIGINT (found by the backfill's own scan).
BACKFILL = {2: ([("BuyEvent", W, 3_500, 3, {})], 3_400, 3_600),
            3: ([("BuyEvent", P, 2_550, 2, {}), ("BuyEvent", Q, 2_560, 2, {}),
                 ("BuyEvent", V, 2_570, 2, {"pool_base_token_reserves": HUGE})], 2_500, 2_600)}
DROP = ((P, 2_000), (R, 2_000), (U, 0), (V, 2_500), (W, 3_000))  # the reference run: these rows never existed


def write_rows(con, d, rows):
    for t in ("BuyEvent", "SellEvent"):
        sel = [r for r in rows if r[0] == t]
        if not sel:
            continue
        tpl = os.path.join(FIX, t, "rows.parquet")
        types = dict(con.execute(f"SELECT column_name, column_type FROM (DESCRIBE SELECT * FROM '{tpl}')").fetchall())
        parts = []
        for _, pool, slot, tx, over in sel:
            rep = {"pool": f"'{pool}'", "slot": slot, "tx_index": tx, "outer_ix": 0, "inner_ix": 0, **over}
            parts.append(f"(SELECT * REPLACE ({', '.join(f'CAST({v} AS {types[k]}) AS {k}' for k, v in rep.items())}) "
                         f"FROM '{tpl}' ORDER BY slot, tx_index LIMIT 1)")
        os.makedirs(os.path.join(d, "pump_amm", t), exist_ok=True)
        con.execute(f"COPY ({' UNION ALL '.join(parts)}) TO '{os.path.join(d, 'pump_amm', t, 'part-0.parquet')}' (FORMAT parquet)")


def build(root, drop=()):
    """The three segments; drop: (pool, first slot) whose rows from that slot on are left out (the reference run)."""
    con = duckdb.connect()
    keep = lambda rows: [r for r in rows if not any(r[1] == p and r[2] >= s for p, s in drop)]  # noqa: E731
    for seq, first, last in SEGS:
        d = os.path.join(root, "segments", f"{seq:09d}")
        os.makedirs(d)
        write_rows(con, d, keep([r for r in ROWS if first <= r[2] <= last]))
        meta = {"seq": seq, "first_slot": first, "last_slot": last, "complete": True, "written_unix": 1_700_000_000 + seq}
        if seq == 1:  # P, Q, R, V, W created live, SOL quote: admitted
            created = [P, Q, R, V, W]
            os.makedirs(os.path.join(d, "pump_amm", "CreatePoolEvent"))
            pq.write_table(pa.table({"slot": pa.array([1_010 + i for i in range(5)], pa.uint64()),
                                     "tx_index": pa.array([0] * 5, pa.uint64()), "outer_ix": pa.array([0] * 5, pa.uint64()),
                                     "inner_ix": pa.array([0] * 5, pa.uint64()), "pool": created,
                                     "base_mint": ["M" + p[0] * 43 for p in created], "quote_mint": [live.WSOL] * 5,
                                     "coin_creator": ["C" * 44] * 5, "is_mayhem_mode": [False] * 5}),
                           os.path.join(d, "pump_amm", "CreatePoolEvent", "part-0.parquet"))
        if seq in BACKFILL:
            rows, lo, hi = BACKFILL[seq]
            write_rows(con, os.path.join(d, "backfill"), keep(rows))
            meta["backfills"] = [{"status": "complete", "rows": len(rows), "from_slot": lo, "to_slot": hi}]
        with open(os.path.join(d, "_segment.json"), "w") as f:
            json.dump(meta, f)
    con.close()


def run(root):
    f = LiveFeed(str(root), store=None, follow=False, memory="200MB")
    out = []
    for seq, seg, meta in segment_dirs(str(root)):
        out.append(f.segment_events(seq, seg, meta))
    return f, out, f.drain_history()


def test_the_guard_leaves_a_pool_with_an_amount_outside_bigint_out(tmp_path, caplog):
    build(tmp_path / "a")
    build(tmp_path / "b", drop=DROP)

    # Without the guard (pool_select's unchanged SQL) segment 2 raises, for P's reserves and, P aside, for R's e.
    f = LiveFeed(str(tmp_path / "a"), store=None, follow=False, memory="200MB")
    segs = segment_dirs(str(tmp_path / "a"))
    f._new_tokens_and_pools(Chunk((1_000, 2_000, segs[0][1])))
    c2 = Chunk((2_000, 3_000, segs[1][1]))
    assert pool_select(c2, "live_pools", ()) == pool_select(c2, "live_pools")  # no exclusion: the old SQL text
    with pytest.raises(duckdb.ConversionException, match="9991111111970383772"):
        f.con.execute(f"SELECT * FROM ({pool_select(c2, 'live_pools')})").fetchall()
    with pytest.raises(duckdb.OutOfRangeException, match="Overflow in addition of INT64"):
        f.con.execute(f"SELECT * FROM ({pool_select(c2, 'live_pools', {P, U})})").fetchall()
    assert sorted(f.con.execute(f"SELECT kind, pool FROM ({int64_select(c2)})").fetchall()) == [("buy", P), ("buy", R), ("buy", U)]

    with caplog.at_level("WARNING", logger="paper.live"):
        fa, ea, ha = run(tmp_path / "a")
    fb, eb, hb = run(tmp_path / "b")
    assert ea == eb and ha == hb  # every other pool as if the rows never existed; earlier events stay
    assert [sorted({e.key for e in s if e.venue == "pool"}) for s in ea] == [sorted([P, Q, R, V, W]), sorted([Q, V]), [Q]]
    assert [p for p, _ in ha] == [Q]  # segment 3's backfill: Q's row is history; P's, V's and W's pending row are left out
    assert fa.int64_pools == {P, R, U, V, W} and fb.int64_pools == set()
    # Rows left out of the segments' own chunks: P 2, R 1, U 1 (segment 2); P, V, W 1 each (segment 3).
    assert (fa.stats["int64_pools"], fa.stats["int64_pool_rows"]) == (5, 7)
    # U's row is neither an unknown-pool row nor anything else: every other count equals the reference run's.
    assert {k: v for k, v in fa.stats.items() if not k.startswith("int64")} == \
        {k: v for k, v in fb.stats.items() if not k.startswith("int64")}
    msgs = [r.getMessage() for r in caplog.records if "outside BIGINT" in r.getMessage()]
    assert sorted(m.split(": pool ")[0] for m in msgs) == ["segment 2 (segment)"] * 3 + ["segment 3 (backfill)", "segment 3 (segment)"]
    assert any(P in m and "(sol)" in m for m in msgs) and any(U in m and "(not known)" in m for m in msgs)
    assert any(V in m and "(backfill)" in m for m in msgs) and any(W in m and "segment 3 (segment)" in m for m in msgs)


def test_int64_select_flags_each_cast_class(tmp_path):
    """One row per cast int64_select checks (store._pool_trades / _pool_other's), each in its own pool; in range: none."""
    con = duckdb.connect()
    tpl = {t: os.path.join(FIX, t, "rows.parquet") for t in ("BuyEvent", "SellEvent")}
    exact_out = sorted(live.data.POOL_EXACT_OUT)[0]
    rows = {"BuyEvent": [("b0", {}), ("b1", {"base_amount_out": HUGE}), ("b2", {"quote_amount_in_with_lp_fee": HUGE}),
                         ("b3", {"quote_amount_in": HUGE}), ("b4", {"user_quote_amount_in": HUGE}),  # exact-in: not cast
                         ("b5", {"user_quote_amount_in": HUGE, "parent_ix_disc": f"'{exact_out}'"}),
                         ("b6", {"virtual_quote_reserves": f"'{2 ** 63}'"}),
                         ("b7", {"pool_base_token_reserves": HUGE, "parent_ix_disc": f"'{live.data.BOOST_BUY_DISC}'"})],  # crank: not read
            "SellEvent": [("s0", {}), ("s1", {"pool_quote_token_reserves": HUGE}), ("s2", {"base_amount_in": HUGE}),
                          ("s3", {"quote_amount_out_without_lp_fee": HUGE}), ("s4", {"user_quote_amount_out": HUGE})]}
    for t, specs in rows.items():
        types = dict(con.execute(f"SELECT column_name, column_type FROM (DESCRIBE SELECT * FROM '{tpl[t]}')").fetchall())
        parts = [f"(SELECT * REPLACE ({', '.join(f'CAST({v} AS {types[k]}) AS {k}' for k, v in ({'pool': repr(p)} | o).items())}) "
                 f"FROM '{tpl[t]}' WHERE parent_ix_disc IS DISTINCT FROM '{live.data.BOOST_BUY_DISC}' ORDER BY slot LIMIT 1)"
                 for p, o in specs]
        os.makedirs(tmp_path / "pump_amm" / t)
        con.execute(f"COPY ({' UNION ALL '.join(parts)}) TO '{tmp_path / 'pump_amm' / t / 'part-0.parquet'}' (FORMAT parquet)")
    u = lambda xs: pa.array(xs, pa.uint64())  # noqa: E731
    boost = {"slot": u([1, 1, 1]), "pool": ["o0", "o1", "o2"], "base_reserves_after": u([5, HUGE, 5]), "base_amount_burned": u([1, 1, 1]),
             "real_quote_reserves_after": u([9, 9, 9]), "quote_amount_in_used": u([2, 2, 2]), "virtual_quote_reserves": ["7", "7", "7"],
             "boost_vault_remaining": u([3, 3, HUGE])}
    os.makedirs(tmp_path / "pump_amm" / "BoostBuyAndBurnEvent")
    pq.write_table(pa.table(boost), str(tmp_path / "pump_amm" / "BoostBuyAndBurnEvent" / "part-0.parquet"))
    for t, kind, base, quote, lp in (("WithdrawEvent", "w", "base_amount_out", "quote_amount_out", "lp_token_amount_in"),
                                     ("DepositEvent", "d", "base_amount_in", "quote_amount_in", "lp_token_amount_out")):
        cols = ("pool_base_token_reserves", "pool_quote_token_reserves", base, quote, lp, "lp_mint_supply")
        tab = {"slot": u([1] * 7), "pool": [f"{kind}{i}" for i in range(7)]}
        tab |= {c: u([HUGE if i == j + 1 else 4 for i in range(7)]) for j, c in enumerate(cols)}
        os.makedirs(tmp_path / "pump_amm" / t)
        pq.write_table(pa.table(tab), str(tmp_path / "pump_amm" / t / "part-0.parquet"))
    c = Chunk((0, 10 ** 12, str(tmp_path)))
    got = sorted(duckdb.connect().execute(f"SELECT pool FROM ({int64_select(c)})").fetchall())
    assert [p for (p,) in got] == ["b1", "b2", "b3", "b5", "b6", "d1", "d2", "d3", "d4", "d5", "d6", "o1", "o2",
                                   "s1", "s2", "s3", "s4", "w1", "w2", "w3", "w4", "w5", "w6"]
