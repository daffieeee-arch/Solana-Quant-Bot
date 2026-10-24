"""Spec F section 1 runner module on a mini store: the entry fill and every mark reproduce the engine's venue
functions (curve_buy_exact_in / curve_sell, pool_sell_capped after the migration, rev_buy_token / rev_sell_token on an
F7 reversed pool), the moments follow block time, and the multiples / drawdown / end marks follow from the marks.
A mayhem twin of the token (same rows) gets no curve entry: the Mayhem agent resets the virtual SOL inside its own
trades, so a replay cannot price through its legs."""

import json
import os

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from backtest import venues as V
from wallets import runners

E = 1040
LO = E * 432_000
S = LO + 1_000
T0 = 1_790_000_000
MS = 400.0            # ms per slot of the fixture's epoch (blocks: 2.5 slots per second)
M = "Mpump"
SUPPLY = 1_000_000_000_000_000
VT0, VQ0, RT0, RQ0 = 1_073_000_000_000_000, 30_000_000_000, 793_100_000_000_000, 0
FEE, CFEE = 95, 30
TX = 705_000
SIZE = runners.SIZE


def _curve_rows(trades):
    """Simulate curve trades with the engine's functions: rows (slot, is_buy, t, q, post state, fee tiers, variant)."""
    c = V.Curve(VT0, VQ0, RT0, RQ0, FEE, CFEE)
    rows, states = [], []
    for slot, is_buy, amount, event in trades:
        f = V.curve_buy_exact_in(c, amount) if is_buy else V.curve_sell(c, amount)
        c = f.state
        rows.append((slot, is_buy, f.tokens, f.curve_quote, c, event))
        states.append((slot, c))
    return rows, states


def _store(path, curve_rows, pool_rows, pool_kind="normal"):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE meta (json VARCHAR)")
    con.execute("INSERT INTO meta VALUES (?)", [json.dumps({"ms_per_slot": {str(E): MS}})])
    con.execute("CREATE TABLE blocks (slot UBIGINT, block_time BIGINT)")
    con.execute(f"INSERT INTO blocks SELECT r, {T0} + floor((r - {S}) * {MS} / 1000)::BIGINT FROM range({S - 10}, {S + 60_000}) t(r)")
    con.execute("CREATE TABLE mints (mint VARCHAR, create_slot UBIGINT, creator VARCHAR, mayhem BOOLEAN, supply UBIGINT, complete_slot UBIGINT)")
    con.execute("INSERT INTO mints VALUES (?, ?, 'CR', false, ?, NULL), ('Mmay', ?, 'CR', true, ?, NULL)", [M, S, SUPPLY, S, SUPPLY])
    con.execute("""CREATE TABLE curve (mint VARCHAR, slot UBIGINT, tx_index UBIGINT, outer_ix UBIGINT, inner_ix UBIGINT, variant VARCHAR, is_buy BOOLEAN,
                   t HUGEINT, q HUGEINT, fee_bps INTEGER, creator_bps INTEGER, vt HUGEINT, vq HUGEINT, rt HUGEINT, rq HUGEINT, trader VARCHAR, fee_payer VARCHAR)""")
    for i, (slot, is_buy, t, q, c, event) in enumerate(curve_rows):
        for mint in (M, "Mmay"):     # the mayhem twin carries the same rows and must get no curve entry
            con.execute("INSERT INTO curve VALUES (?, ?, 1, 2, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [mint, slot, "buy" if is_buy else "sell", is_buy, t, q, FEE, CFEE, c.vt, c.vq, c.rt, c.rq, f"W{i}", None if i % 2 else "PAYER"])  # NULL: pays itself
    con.execute("CREATE TABLE pools (pool VARCHAR, mint VARCHAR, quote_class VARCHAR, orientation VARCHAR, create_slot UBIGINT, coin_creator VARCHAR, mayhem BOOLEAN)")
    con.execute("""CREATE TABLE pool (kind VARCHAR, pool VARCHAR, slot UBIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT, trader VARCHAR, fee_payer VARCHAR,
                   b BIGINT, vault BIGINT, vq BIGINT, e BIGINT, lp_bps INTEGER, protocol_bps INTEGER, creator_bps INTEGER, cashback_bps INTEGER,
                   base BIGINT, e_delta BIGINT, quote_user BIGINT, orientation VARCHAR, token_buy BOOLEAN, sol_amount BIGINT, token_amount BIGINT, farmer BOOLEAN)""")
    for pr in pool_rows:
        con.execute("INSERT INTO pools VALUES (?, ?, ?, ?, ?, 'CR', false)", [pr["pool"], pr["mint"], "sol" if pool_kind == "normal" else "reversed", pool_kind, pr["create_slot"]])
        for r in pr["rows"]:
            con.execute("INSERT INTO pool VALUES (?, ?, ?, 1, 3, 0, ?, ?, ?, ?, ?, ?, 20, 5, 5, 0, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [r["kind"], pr["pool"], r["slot"], r.get("trader", "U"), None, r["b"], r.get("vault", r["e"]), 0, r["e"],
                         r.get("base"), r.get("e_delta"), r.get("quote_user"), pool_kind, r.get("token_buy"), r.get("sol_amount"), r.get("token_amount"), r.get("farmer", False)])
    con.close()


def _pool_trades(p, trades):
    """Simulate normal-pool trades (sol in / tokens in) with the engine: rows with the pre-state and post-states."""
    rows, states = [], []
    for slot, token_buy, amount in trades:
        f = V.pool_buy_exact_in(p, amount) if token_buy else V.pool_sell(p, amount)
        rows.append({"kind": "buy" if token_buy else "sell", "slot": slot, "b": p.b, "e": p.e, "base": f.tokens, "e_delta": f.state.e - p.e,
                     "token_buy": token_buy, "sol_amount": f.trader_quote if token_buy else f.trader_quote, "token_amount": f.tokens})
        p = f.state
        states.append((slot, p))
    return rows, states


def test_curve_entry_marks_and_migration(tmp_path, monkeypatch):
    # trades: dev buy at the create, buys / sells over the first minutes (some without an event), a buy that completes the curve
    trades = [(S, True, 2 * 10**9, True), (S + 300, True, 3 * 10**9, False), (S + 400, True, 10**9, True), (S + 450, False, 10**12, False),
              (S + 900, True, 25 * 10**9, True), (S + 1500, False, 5 * 10**12, True), (S + 3000, True, 200 * 10**9, True)]
    rows, states = _curve_rows(trades)
    final = states[-1][1]
    assert final.complete, "the last buy must complete the curve"
    # the migration pool: 206.9 M tokens against the real SOL (minus the fee), then trades
    p0 = V.Pool(206_900_000_000_000, final.rq - 6 * 10**9, 20, 5, 5, 0)
    prow = [{"kind": "deposit", "slot": S + 3100, "b": 0, "e": 0, "vault": 0, "base": p0.b, "e_delta": None, "quote_user": p0.e}]
    ptrades, pstates = _pool_trades(p0, [(S + 3200, True, 3 * 10**9), (S + 3300, False, 2 * 10**12), (S + 3400, True, 20 * 10**9), (S + 9000, False, 40 * 10**12)])
    _store(tmp_path / "s.duckdb", rows, [{"pool": "P1", "mint": M, "create_slot": S + 3100, "rows": prow + ptrades}])
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path / "spill"))
    out = tmp_path / "out"
    info = runners.run(LO, LO + 5_000, str(out), store=str(tmp_path / "s.duckdb"), cfg="/nonexistent.yaml")
    assert info["tx_cost_lamports"] == TX and info["curve_excluded"] == {"mayhem": 1} and info["curve_rows_without_fee_tiers"] == 0
    df = duckdb.connect().execute(f"SELECT * FROM '{out}/entries-{LO}.parquet' ORDER BY moment").arrow().read_all().to_pylist()
    assert all(r["key"] == M for r in df) and len(df) == 3       # the mayhem twin: no entries at all (no pool either)
    by = {r["moment"]: r for r in df}
    # c1: the first trade at or after create + 60 s = slot S + 150 -> S + 300; d = round(600 / 400) = 2 slots -> the entry trade is S + 400
    r = by["c1"]
    assert r["t"] == S + 300 and r["entry_slot"] == S + 400 and r["entry_venue"] == "curve" and r["segment"] == "curve"
    pre = states[1][1]       # the state before the S + 400 trade = after the S + 300 trade
    fill = V.curve_buy_exact_in(pre, SIZE)
    assert abs(r["tokens"] - fill.tokens) <= 2 and abs(r["cost_sol"] * 1e9 - (SIZE + TX)) < 2
    assert abs(r["depth_sol"] * 1e9 - pre.rq) < 1
    # marks: the curve post-states from S + 400 on, then the pool states after the migration (all within 6 h = 54,000 slots)
    marks = []
    for slot, c in states[2:]:
        if c.complete:          # no market between the completing buy and the pool's first state: no mark
            continue
        f = V.curve_sell(c, fill.tokens)
        marks.append((slot, f.trader_quote - TX))
    for slot, p in [(S + 3100, p0)] + pstates:     # the deposit's post-state (the pool's opening state) is a mark point too
        f = V.pool_sell_capped(p, fill.tokens)
        marks.append((slot, (f.trader_quote if f else 0) - TX))
    cost = SIZE + TX
    net = [m for _, m in marks]
    assert r["n_marks"] == len(marks) and r["n_curve"] == len(states) - 3 and r["n_pool"] == len(pstates) + 1
    assert abs(r["mult_6h"] - max(net) / cost) < 1e-6
    h1 = round(3_600_000 / MS)
    assert abs(r["mult_1h"] - max(m for s_, m in marks if s_ <= S + 400 + h1) / cost) < 1e-6
    imax = max(range(len(net)), key=lambda i: (net[i], -i))
    assert abs(r["dd_6h"] - min(net[: imax + 1]) / cost) < 1e-6 and abs(r["end_6h"] - net[-1] / cost) < 1e-6
    assert abs(r["t_max_min"] - (marks[imax][0] - (S + 400)) * MS / 60000) < 1e-9
    # the simple exits: tp x2 = the first mark >= 2 x cost; trail 30 / 50 = the first mark <= 70 % / 50 % of the highest earlier mark
    tp2 = next((m for m in net if m >= 2 * cost), net[-1])
    tr30 = next((net[i] for i in range(1, len(net)) if net[i] <= 0.7 * max(net[:i])), net[-1])
    tr50 = next((net[i] for i in range(1, len(net)) if net[i] <= 0.5 * max(net[:i])), net[-1])
    assert abs(r["x_tp2"] - tp2 / cost) < 1e-6 and abs(r["x_tr30"] - tr30 / cost) < 1e-6 and abs(r["x_tr50"] - tr50 / cost) < 1e-6
    assert r["x_tp2"] >= 2 and r["x_tr30"] < r["mult_6h"]
    # the 25 SOL curve buy and the 20 SOL pool buy make a c1 entry a runner
    assert r["mult_6h"] > 2
    # c5: first trade at or after +300 s = slot S + 750 -> S + 900; entry = S + 1500 (the sell); c15: +900 s = S + 2250 -> S + 3000, the
    # completing buy; the entry would be the next trade: the pool deposit is no trade, the pool buy at S + 3200 is (pool entry)
    assert by["c5"]["t"] == S + 900 and by["c5"]["entry_slot"] == S + 1500
    assert by["c15"]["t"] == S + 3000 and by["c15"]["entry_slot"] == S + 3200 and by["c15"]["entry_venue"] == "pool"
    pf = V.pool_buy_exact_in(p0, SIZE)
    assert abs(by["c15"]["tokens"] - pf.tokens) <= 2 and abs(by["c15"]["depth_sol"] * 1e9 - p0.e) < 1
    # features of c1 (t = S + 300, entry S + 400): since the create two buys (W0, W1) and no sells; buyers 2, PAYER pays W0 only
    assert r["all_buyers"] == 2 and r["all_sellers"] == 0 and abs(r["all_inflow"] - (rows[0][3] + rows[1][3]) / 1e9) < 1e-9
    assert r["all_payer_other"] == 0.5 and r["payer_hub"] == 0.0 and r["all_rt_share"] == 0.0 and r["holders"] == 2
    assert abs(r["top5_share"] - 1.0) < 1e-12
    # post5: the 5 min after the entry = (S + 400, S + 1150]: the sell at S + 450 and the buy at S + 900
    assert r["post5_buyers"] == 1 and r["post5_sellers"] == 1 and abs(r["post5_inflow"] - (rows[4][3] - rows[3][3]) / 1e9) < 1e-9
    assert abs(r["min_since_create"] - 400 * MS / 60000) < 1e-9 and r["regime"] == "400ms" and not r["cut"]


def test_reversed_pool_f7_entry(tmp_path, monkeypatch):
    """An F7 trigger on a reversed pool (the token is the pool's quote side): the entry buys tokens with 0.5 SOL through
    rev_buy_token at the state before the first trade >= t + d, the marks sell them through rev_sell_token."""
    # reversed pool: b = SOL side, e = token side (effective), no virtual quote
    p = V.Pool(80 * 10**9, 500_000_000_000_000, 20, 5, 5, 0)
    rows, states = [], []
    for slot, sol_in, amount in [(S + 10, True, 2 * 10**9), (S + 20, False, 10**13), (S + 40, True, 30 * 10**9), (S + 90, False, 10**14), (S + 5000, True, 10**9)]:
        f = V.rev_buy_token(p, amount) if sol_in else V.rev_sell_token(p, amount)
        # the store row: a reversed 'buy' row is a token buy; b / e the pre-state; base = the SOL moved, e_delta the token side
        rows.append({"kind": "buy" if sol_in else "sell", "slot": slot, "b": p.b, "e": p.e, "base": f.state.b - p.b if not sol_in else p.b - f.state.b,
                     "e_delta": f.state.e - p.e, "token_buy": sol_in, "sol_amount": amount if sol_in else f.trader_quote,
                     "token_amount": f.tokens if sol_in else amount})
        p = f.state
        states.append((slot, p))
    # the store's pool_leg for a reversed row: buy -> b - base, sell -> b + base (base = SOL side), e + e_delta
    for r_, (slot, st) in zip(rows, states):
        assert (r_["b"] - r_["base"] if r_["kind"] == "buy" else r_["b"] + r_["base"]) == st.b and r_["e"] + r_["e_delta"] == st.e
    _store(tmp_path / "s.duckdb", [], [{"pool": "PR", "mint": "Tq", "create_slot": S, "rows": rows}], pool_kind="reversed")
    parts = tmp_path / "parts"
    parts.mkdir()
    pq.write_table(pa.Table.from_pylist([{"family": "F7", "variant": "tp4_T600", "d": 1, "size_sol": 0.5, "q": 0.5, "t": S + 15, "pool": "PR", "mint": "Tq"},
                                         {"family": "F7", "variant": "tp4_T600", "d": 1, "size_sol": 0.5, "q": 0.5, "t": S + 45, "pool": "PR", "mint": "Tq"},
                                         {"family": "N2_F7", "variant": "tp4_T600", "d": 1, "size_sol": 0.5, "q": 0.5, "t": S + 15, "pool": "PR", "mint": "Tq"}]),
                   parts / "part-0.parquet")
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path / "spill"))
    out = tmp_path / "out"
    runners.run(LO, LO + 5_000, str(out), f7_parts=str(parts), store=str(tmp_path / "s.duckdb"), cfg="/nonexistent.yaml")
    df = duckdb.connect().execute(f"SELECT * FROM '{out}/entries-{LO}.parquet' ORDER BY t").arrow().read_all().to_pylist()
    # every trigger of the pool is an entry of its own (the second: t = S + 45 -> the first trade >= S + 47 is the sell at S + 90)
    assert len(df) == 2 and all(r_["moment"] == "f7" and r_["segment"] == "pool_reversed" and r_["key"] == "PR" for r_ in df)
    assert df[1]["t"] == S + 45 and df[1]["entry_slot"] == S + 90 and df[1]["n_marks"] == 2
    r = df[0]
    # t = S + 15, d = 2 -> the entry trade is S + 20; its pre-state = the state after S + 10
    assert r["entry_slot"] == S + 20
    pre = states[0][1]
    fill = V.rev_buy_token(pre, SIZE)
    assert abs(r["tokens"] - fill.tokens) <= 2 and abs(r["depth_sol"] * 1e9 - pre.b) < 1
    marks = [(V.rev_sell_token(st, fill.tokens).trader_quote - TX) for _, st in states[1:]]
    assert r["n_marks"] == len(marks) and abs(r["mult_6h"] - max(marks) / (SIZE + TX)) < 1e-6 and abs(r["end_6h"] - marks[-1] / (SIZE + TX)) < 1e-6
    assert r["mult_6h"] > 1.2   # the 30 SOL buy at S + 40 lifts the mark


def test_thin_curve_mark_includes_our_own_deposit(tmp_path, monkeypatch):
    """spec-runners-mark-cap-2026-10-24: on a curve with less real SOL than our budget, the mark after the entry is the
    constant-product sell value of our tokens (minus fees and network cost), capped at the logged reserve PLUS our own net
    deposit; the old cap (the logged reserve alone) valued the position at roughly the reserve (E71 section 3.3)."""
    trades = [(S, True, 2 * 10**7, True), (S + 20, True, 2 * 10**7, True), (S + 40, True, 10**7, True)]   # 0.02 / 0.02 / 0.01 SOL
    rows, states = _curve_rows(trades)
    _store(tmp_path / "s.duckdb", rows, [])
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path / "spill"))
    con = runners.connect(str(tmp_path / "s.duckdb"), memory="500MB", threads=1)
    clock = runners.Clock.from_store(con)
    runners.setup(con, LO, LO + 5_000, clock)
    # one moment at S + 10 with the fixed 3-slot delay of the probes: the entry is the S + 20 trade (pre-state = after S)
    con.execute(f"CREATE OR REPLACE TEMP TABLE mom AS SELECT '{M}' AS key, 'x' AS moment, {S + 10}::BIGINT AS t, 3::BIGINT AS d_slots, 9000::BIGINT AS h1, 54000::BIGINT AS h6, 150::BIGINT AS m1s, 750::BIGINT AS m5s")
    runners.entries(con, TX)
    runners.marks(con, TX, LO + 60_000)
    pre = states[0][1]
    fill = V.curve_buy_exact_in(pre, SIZE)
    (q_in, tokens, cost) = con.execute("SELECT q_in, tokens, cost FROM ent").fetchone()
    assert abs(int(tokens) - fill.tokens) <= 2 and int(q_in) == fill.curve_quote and int(cost) == SIZE + TX
    assert pre.rq < SIZE // 5, "the fixture curve must be far thinner than the budget"
    marks = con.execute("SELECT slot, net FROM mk ORDER BY ord").fetchall()
    assert [m[0] for m in marks] == [S + 20, S + 40]
    for (slot, net), (_, c) in zip(marks, states[1:]):
        gross = fill.tokens * c.vq // (c.vt + fill.tokens)
        capped = min(gross, c.rq + fill.curve_quote)          # the new cap: logged reserve + our own deposit
        expect = max(0, capped - V.fee(capped, FEE) - V.fee(capped, CFEE)) - TX
        assert abs(int(net) - expect) <= 2
        assert int(net) > 0.9 * (SIZE + TX), "the position is worth about what we paid, not the thin reserve"
        assert c.rq < 0.2 * (SIZE + TX), "the old cap (logged reserve alone) would have given less than 20 % of the cost"


def test_thin_normal_pool_mark_includes_our_own_deposit(tmp_path, monkeypatch):
    """spec-runners-mark-cap-2026-10-24 section 4, normal pool with a virtual quote: real = e - virt < budget. First mark: the
    gross sell value fits in real + q_in (cap branch, uncapped); second mark after an 80 % liquidity withdrawal: the gross
    exceeds real + q_in, so only the tokens that drain real + q_in are sold (drain branch). Expected values follow the
    engine's pool arithmetic on the logged state with the real vault raised by our own deposit."""
    rows, states = _curve_rows([(S, True, 2 * 10**9, True)])    # a curve row so the token exists; the entry is on the pool
    B0, REAL, VIRT = 10**12, 2 * 10**8, 10 * 10**9                 # 0.2 SOL real, 10 SOL virtual -> e = 10.2 SOL
    e0 = REAL + VIRT
    buy_tokens = 10**9                                             # a small buy by someone else: the entry's reference trade
    e_delta = (e0 * buy_tokens) // (B0 - buy_tokens) + 1
    prow = [{"kind": "deposit", "slot": S + 100, "b": 0, "e": 0, "vault": 0, "base": B0, "e_delta": None, "quote_user": REAL},
            {"kind": "buy", "slot": S + 120, "b": B0, "e": e0, "vault": REAL, "base": buy_tokens, "e_delta": e_delta, "token_buy": True, "sol_amount": e_delta, "token_amount": buy_tokens},
            {"kind": "withdraw", "slot": S + 200, "b": B0 - buy_tokens, "e": e0 + e_delta, "vault": REAL + e_delta, "base": (B0 - buy_tokens) * 8 // 10, "e_delta": None, "quote_user": (REAL + e_delta) * 8 // 10}]
    _store(tmp_path / "s.duckdb", rows, [{"pool": "P1", "mint": M, "create_slot": S + 100, "rows": prow}])
    w = duckdb.connect(str(tmp_path / "s.duckdb"))
    w.execute(f"UPDATE pool SET vq = {VIRT} WHERE pool = 'P1'")  # the fixture writes vq = 0; here the pool prices with a virtual quote
    w.close()
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path / "spill"))
    con = runners.connect(str(tmp_path / "s.duckdb"), memory="500MB", threads=1)
    clock = runners.Clock.from_store(con)
    runners.setup(con, LO, LO + 5_000, clock)
    con.execute(f"CREATE OR REPLACE TEMP TABLE mom AS SELECT '{M}' AS key, 'x' AS moment, {S + 110}::BIGINT AS t, 3::BIGINT AS d_slots, 9000::BIGINT AS h1, 54000::BIGINT AS h6, 150::BIGINT AS m1s, 750::BIGINT AS m5s")
    runners.entries(con, TX)
    runners.marks(con, TX, LO + 60_000)
    venue, tokens, q_in, depth = con.execute("SELECT venue, tokens, q_in, depth FROM ent").fetchone()
    p0 = V.Pool(B0, e0, 20, 5, 5, 0, VIRT)
    fill = V.pool_buy_exact_in(p0, SIZE)
    assert venue == "pool" and abs(int(tokens) - fill.tokens) <= 2 and int(depth) == REAL and int(depth) < SIZE
    n = V.net_for_budget(SIZE, p0.fee_list)
    assert int(q_in) == n
    marks = dict(con.execute("SELECT slot, net FROM mk ORDER BY ord").fetchall())
    assert set(marks) == {S + 120, S + 200}
    # mark 1: state after the small buy; gross <= real + q_in -> the uncapped constant-product value (old code: capped at real 0.2 SOL)
    b1, e1 = B0 - buy_tokens, e0 + e_delta
    gross1 = e1 * fill.tokens // (b1 + fill.tokens)
    assert gross1 <= (e1 - VIRT) + n and gross1 > e1 - VIRT, "fixture: the cap must bind without our deposit and not with it"
    f1 = V._fees(V.Pool(b1, e1, 20, 5, 5, 0, VIRT), gross1)[1]
    assert abs(int(marks[S + 120]) - (gross1 - f1 - TX)) <= 2 and int(marks[S + 120]) > 0.8 * SIZE
    # mark 2: after the 80 % withdrawal the gross exceeds real + q_in: sell only the tokens that drain real + q_in
    b2 = b1 - prow[2]["base"]
    e2 = (REAL + e_delta) + VIRT - prow[2]["quote_user"]
    real2 = (e2 - VIRT) + n
    gross2 = e2 * fill.tokens // (b2 + fill.tokens)
    assert gross2 > real2, "fixture: the drain branch must apply"
    tmax = (real2 * b2) // VIRT
    pg2 = e2 * tmax // (b2 + tmax)
    f2 = V._fees(V.Pool(b2, e2, 20, 5, 5, 0, VIRT), pg2)[1]
    assert abs(int(marks[S + 200]) - (pg2 - f2 - TX)) <= 2
    assert pg2 <= real2 and pg2 > n * 9 // 10, "the drain branch pays out about real + our deposit, not real alone"
