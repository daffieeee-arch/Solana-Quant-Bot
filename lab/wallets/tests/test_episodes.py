"""Leader episodes: opening, top-ups, capped sells, exit share, time cap and the trigger window."""

from wallets.episodes import EXIT_SHARE, walk

SOL = 10**9


def leg(slot, tokens, sol, buy, trigger=None, create_slot=False):
    trigger = (buy and sol >= SOL) if trigger is None else trigger
    return ("w", "k", "curve", "curve", slot, slot << 20, tokens, sol, buy, trigger, create_slot, None)


def test_episode_opens_on_trigger_and_closes_at_exit_share():
    rows = [leg(10, 100, SOL // 2, True),  # small buy: no episode
            leg(20, 1000, 2 * SOL, True),  # trigger
            leg(30, 500, 1 * SOL, True),  # top-up (not a new episode, also >= 1 SOL)
            leg(40, 1400, 4 * SOL, False),  # sells 1400 of 1500: 100 left <= 10% of max -> closed
            leg(50, 100, SOL // 10, False),  # after the close: ignored (no open episode, not a trigger)
            leg(60, 2000, 3 * SOL, True)]  # new episode, still open at the end
    eps = walk(rows, 0, 100, episode_slots=1000)
    assert len(eps) == 2
    a, b = eps
    assert (a["t0"], a["cost_sol"], a["proceeds_sol"], a["remaining_tokens"], a["max_tokens"]) == (20, 3 * SOL, 4 * SOL, 100, 1500)
    assert a["closed"] and a["hold_slots"] == 20 and a["end_slot"] == 40 and a["n_buys"] == 2 and a["n_sells"] == 1
    assert b["t0"] == 60 and not b["closed"] and b["hold_slots"] is None and b["end_slot"] == 1060


def test_sell_capped_at_held_tokens_pro_rata():
    rows = [leg(20, 1000, 2 * SOL, True),
            leg(30, 4000, 8 * SOL, False)]  # sells 4000 but we count only 1000: 2 SOL of the 8
    (a,) = walk(rows, 0, 100, 1000)
    assert a["proceeds_sol"] == 2 * SOL and a["remaining_tokens"] == 0 and a["closed"]
    assert abs(EXIT_SHARE - 0.10) < 1e-9


def test_time_cap_and_trigger_window():
    rows = [leg(5, 1000, 2 * SOL, True),  # before the window: opens an episode that is not emitted...
            leg(50, 500, SOL, True),  # ...so this buy inside the window is a top-up, not a trigger
            leg(2000, 1000, 5 * SOL, True),  # after the cap: the first episode is emitted (censored) and
            leg(2100, 200, SOL // 2, False)]  # this >= 1 SOL buy opens a second one (slot 2000 >= hi: stop)
    eps = walk(rows, 10, 1000, episode_slots=1000)
    assert [e["t0"] for e in eps] == []  # the pre-window episode is dropped, slot 2000 is past hi
    rows = [leg(20, 1000, 2 * SOL, True), leg(1500, 100, SOL // 10, False)]
    (a,) = walk(rows, 10, 1000, 1000)
    assert not a["closed"] and a["end_slot"] == 1020 and a["proceeds_sol"] == 0 and a["n_sells"] == 0


def test_legs_order_is_total_within_a_transaction():
    """Two legs of one wallet and key in one tx tie on pos; the instruction position decides (reproducible runs)."""
    import duckdb

    from wallets import episodes as E

    con = duckdb.connect()
    con.execute("CREATE TABLE mints (mint VARCHAR, mayhem BOOLEAN, quote_mint VARCHAR, create_slot BIGINT, pool VARCHAR)")
    con.execute("CREATE TABLE pools (pool VARCHAR, mint VARCHAR, orientation VARCHAR, quote_class VARCHAR, mayhem BOOLEAN)")
    con.execute("CREATE TABLE curve (mint VARCHAR, slot BIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT, is_buy BOOLEAN, "
                "t HUGEINT, q HUGEINT, trader VARCHAR)")
    con.execute("CREATE TABLE pool (kind VARCHAR, pool VARCHAR, slot BIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT, "
                "trader VARCHAR, orientation VARCHAR, token_buy BOOLEAN, sol_amount BIGINT, token_amount BIGINT, farmer BOOLEAN)")
    con.execute("INSERT INTO mints VALUES ('M', false, 'So11111111111111111111111111111111111111112', 1, 'P')")
    con.execute("INSERT INTO pools VALUES ('P', 'M', 'normal', 'sol', false)")
    # inserted in the wrong order on purpose: the sell (inner_ix 7) before the buy (inner_ix 3) of the same tx
    con.execute("INSERT INTO curve VALUES ('M', 10, 5, 2, 7, false, 1000, 1500000000, 'w'), ('M', 10, 5, 2, 3, true, 1000, 2000000000, 'w')")
    con.execute("INSERT INTO pool VALUES ('sell', 'P', 10, 5, 4, 1, 'w', 'normal', false, 500000000, 400, false), "
                "('buy', 'P', 10, 5, 3, 9, 'w', 'normal', true, 3000000000, 2000, false)")
    rows = []
    for b in range(E.BUCKETS):
        rows += con.execute(E.legs_sql(0, 100, b)).fetchall()
    assert [(r[2], r[8], r[12], r[13]) for r in rows] == [("curve", True, 2, 3), ("curve", False, 2, 7), ("pool", True, 3, 9), ("pool", False, 4, 1)]
    eps = walk(rows, 0, 100, episode_slots=1000)
    # curve buy then curve sell (everything sold: closed), then the pool buy opens a second episode
    assert [(e["venue"], e["closed"], e["n_buys"], e["n_sells"]) for e in eps] == [("curve", True, 1, 1), ("pool", False, 1, 1)]


def test_first_buy_only_opens_nothing_after_an_earlier_buy():
    """E82: a launch-slot buy (no trigger: offset 0) followed by a top-up >= trigger size opens no episode; a wallet
    whose first buy is the trigger opens one; after a closed episode no second one opens on the same key."""
    rows = [leg(10, 1000, 2 * SOL, True, trigger=False),  # offset-0 buy: filtered out as a trigger by min_offset
            leg(20, 1000, 2 * SOL, True)]  # the top-up would trigger, but it is not the wallet's first buy
    assert walk(rows, 0, 100, 1000, first_buy_only=True) == []
    assert [e["t0"] for e in walk(rows, 0, 100, 1000)] == [20]  # K1 behaviour unchanged without the flag
    rows = [leg(20, 1000, 2 * SOL, True), leg(30, 1000, 3 * SOL, False), leg(60, 2000, 3 * SOL, True)]
    assert [e["t0"] for e in walk(rows, 0, 100, 1000, first_buy_only=True)] == [20]
    assert [e["t0"] for e in walk(rows, 0, 100, 1000)] == [20, 60]


def test_legs_sql_trigger_size_and_min_offset():
    """E82: --trigger-sol lowers the trigger; --min-offset refuses triggers before create + offset, and a pool whose
    mint has no create row never triggers with an offset (its offset is unknown)."""
    import duckdb

    from wallets import episodes as E

    con = duckdb.connect()
    con.execute("CREATE TABLE mints (mint VARCHAR, mayhem BOOLEAN, quote_mint VARCHAR, create_slot BIGINT, pool VARCHAR)")
    con.execute("CREATE TABLE pools (pool VARCHAR, mint VARCHAR, orientation VARCHAR, quote_class VARCHAR, mayhem BOOLEAN)")
    con.execute("CREATE TABLE curve (mint VARCHAR, slot BIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT, is_buy BOOLEAN, "
                "t HUGEINT, q HUGEINT, trader VARCHAR)")
    con.execute("CREATE TABLE pool (kind VARCHAR, pool VARCHAR, slot BIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT, "
                "trader VARCHAR, orientation VARCHAR, token_buy BOOLEAN, sol_amount BIGINT, token_amount BIGINT, farmer BOOLEAN)")
    con.execute("INSERT INTO mints VALUES ('M', false, 'So11111111111111111111111111111111111111112', 100, 'P')")
    con.execute("INSERT INTO pools VALUES ('P', 'M', 'normal', 'sol', false), ('Q', NULL, 'reversed', 'reversed', false)")
    con.execute("INSERT INTO curve VALUES ('M', 100, 1, 0, 0, true, 1000, 600000000, 'w'), ('M', 103, 1, 0, 0, true, 1000, 600000000, 'w'), "
                "('M', 104, 1, 0, 0, true, 1000, 600000000, 'w'), ('M', 110, 1, 0, 0, true, 1000, 2000000000, 'w')")
    con.execute("INSERT INTO pool VALUES ('buy', 'P', 120, 1, 0, 0, 'w', 'normal', true, 2000000000, 10, false), "
                "('buy', 'Q', 130, 1, 0, 0, 'w', 'reversed', true, 2000000000, 10, false)")

    def triggers(**kw):
        rows = []
        for b in range(E.BUCKETS):
            rows += con.execute(E.legs_sql(0, 1000, b, **kw)).fetchall()
        return [(r[2], r[4], r[9]) for r in sorted(rows, key=lambda r: r[4])]

    assert triggers() == [("curve", 100, False), ("curve", 103, False), ("curve", 104, False), ("curve", 110, True), ("pool", 120, True), ("pool", 130, True)]
    assert triggers(trigger_sol=SOL // 2) == [("curve", 100, True), ("curve", 103, True), ("curve", 104, True), ("curve", 110, True), ("pool", 120, True), ("pool", 130, True)]
    assert triggers(trigger_sol=SOL // 2, min_offset=4) == [("curve", 100, False), ("curve", 103, False), ("curve", 104, True), ("curve", 110, True), ("pool", 120, True), ("pool", 130, False)]


def _mark_store(path, n_keys=40):
    """A file store with n_keys tokens: a 2 SOL trigger by wallet w<i> at slot 100 + i (1000 tokens), a 0.05 SOL trade by
    another wallet at 110 + i, a closing sell by w<i> at 120 + i for even i, a 0.5 SOL trade at 150 + i (250 x the average
    buy price: capped) and a dust trade at 160 + i (below MARK_MIN_SOL: never a mark). Blocks: one hour per slot."""
    import duckdb

    con = duckdb.connect(path)
    con.execute("CREATE TABLE meta (json VARCHAR)")
    con.execute("""INSERT INTO meta VALUES ('{"ms_per_slot": {"0": 200.0}}')""")
    con.execute("CREATE TABLE blocks (slot BIGINT, block_time BIGINT)")
    con.execute("INSERT INTO blocks SELECT range, 1700000000 + range * 3600 FROM range(0, 1000)")
    con.execute("CREATE TABLE mints (mint VARCHAR, mayhem BOOLEAN, quote_mint VARCHAR, create_slot BIGINT, pool VARCHAR)")
    con.execute("CREATE TABLE pools (pool VARCHAR, mint VARCHAR, orientation VARCHAR, quote_class VARCHAR, mayhem BOOLEAN)")
    con.execute("CREATE TABLE curve (mint VARCHAR, slot BIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT, is_buy BOOLEAN, "
                "t HUGEINT, q HUGEINT, trader VARCHAR)")
    con.execute("CREATE TABLE pool (kind VARCHAR, pool VARCHAR, slot BIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT, "
                "trader VARCHAR, orientation VARCHAR, token_buy BOOLEAN, sol_amount BIGINT, token_amount BIGINT, farmer BOOLEAN)")
    for i in range(n_keys):
        k, w = f"M{i:02d}", f"w{i:02d}"
        con.execute("INSERT INTO mints VALUES (?, false, 'So11111111111111111111111111111111111111112', 50, NULL)", [k])
        rows = [(k, 100 + i, 1, 0, 0, True, 1000, 2 * SOL, w),
                (k, 110 + i, 1, 0, 0, True, 10, SOL // 20, "x"),
                (k, 150 + i, 1, 0, 0, True, 1, SOL // 2, "x"),
                (k, 160 + i, 1, 0, 0, True, 100, SOL // 200, "y")]
        if i % 2 == 0:
            rows.append((k, 120 + i, 1, 0, 0, False, 950, 3 * SOL, w))
        con.executemany("INSERT INTO curve VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    con.close()


def test_run_marks_per_key_bucket_identically(tmp_path, monkeypatch):
    """The mark per key hash bucket (MARK_BUCKETS) gives the same episodes as one bucket: the mark is the last trade
    >= MARK_MIN_SOL at or before the episode end, capped at MARK_MAX_MULT x the average buy price; the day is t0's."""
    import pyarrow.parquet as pq

    from wallets import episodes as E

    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path))
    store = str(tmp_path / "s.duckdb")
    _mark_store(store)
    E.run(100, 400, str(tmp_path / "b16"), store=store)
    out = pq.read_table(tmp_path / "b16" / "episodes-100.parquet").to_pylist()
    assert len(out) == 40 and [e["key"] for e in out] == [f"M{i:02d}" for i in range(40)]  # sorted by t0
    closed, open_ = out[0], out[1]
    assert closed["closed"] and closed["end_slot"] == 120 and closed["remaining_tokens"] == 50
    assert abs(closed["mark_price"] - 3 * SOL / 950) < 1e-6 and not closed["mark_capped"]  # the closing sell is the last trade
    assert abs(closed["leader_ret_realized"] - 0.5) < 1e-12
    assert abs(closed["leader_ret_marked"] - ((3 * SOL + 50 * 3 * SOL / 950) / (2 * SOL) - 1)) < 1e-9
    assert not open_["closed"] and open_["end_slot"] == 101 + 72_000 and open_["remaining_tokens"] == 1000
    assert open_["mark_capped"] and abs(open_["mark_price"] - E.MARK_MAX_MULT * 2 * SOL / 1000) < 1e-6  # 0.5 SOL for 1 token: capped
    assert abs(open_["leader_ret_marked"] - (1000 * E.MARK_MAX_MULT * 2 * SOL / 1000 / (2 * SOL) - 1)) < 1e-9
    from datetime import datetime, timezone

    day = lambda slot: datetime.fromtimestamp(1700000000 + slot * 3600, timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
    assert [e["day"] for e in out] == [day(100 + i) for i in range(40)] and closed["day"] == "2023-11-19"  # t0's block, UTC
    assert all(e["mark_price"] is not None for e in out)
    monkeypatch.setattr(E, "MARK_BUCKETS", 1)
    E.run(100, 400, str(tmp_path / "b1"), store=store)
    assert pq.read_table(tmp_path / "b1" / "episodes-100.parquet").to_pylist() == out
