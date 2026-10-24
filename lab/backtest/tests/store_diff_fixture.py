"""Tiny synthetic stores for backtest/store_diff.py (tests/test_store_diff.py and the NAS script's local dry run): the
store schema of store.py (store v2) with a handful of rows, no event data. Only duckdb is imported, so the dry run can
load this file next to another tree's backtest package.

scenario() gives an S1-like store A (warm-up 1042 + window 1043, period dev-diag-clean-path) and a dev2-like store B
(1033-1046, period dev) that agree under store_diff's GREEN rule, with the window effects the rule must not call RED:
  - B has a curve row in 1043 of a mint created before A's start (M_OLD: the warm-up truncation remainder), and rows
    and mints outside A's store;
  - M_B completes after the window in B only (look-ahead: NULL in A);
  - P_OLD is a reversed pool created before A's start: A has no mint for it (its trigger's mint is the pool id) and
    no create_slot; B has both (its create in 1041);
  - P_REV (reversed, created in A's warm-up) and P_NORM (sol, created in the window) agree;
  - P_REV can boost in B only (a boost after the window), P_GONE trades only before A's start.
Each of P_REV, P_OLD and P_NORM has one F7 trigger in the window (a 10 SOL token sell 5-20 slots after a 2 SOL token
buy, 200 SOL deep), M_A one curve trigger. s0_like(a) is A as ba322be would build it (period dev, no u64 meta)."""

import copy
import json

import duckdb

EPOCH = 432_000
LO, HI = 1043 * EPOCH, 1044 * EPOCH  # the window: epoch 1043
WS = LO - EPOCH  # A's store start: 1042 as warm-up
SOL = 10 ** 9
SOLQ = "11111111111111111111111111111111"
WSOL = "So11111111111111111111111111111111111111112"
T0 = 1_760_000_000

SCHEMA = {
    "blocks": "slot UBIGINT, block_time BIGINT",
    "mints": """mint VARCHAR, create_slot UBIGINT, create_tx UBIGINT, creator VARCHAR, creator_field VARCHAR, mayhem BOOLEAN,
      quote_mint VARCHAR, cashback BOOLEAN, holder_reward BOOLEAN, supply UBIGINT, name VARCHAR, symbol VARCHAR,
      complete_slot UBIGINT, migrate_slot UBIGINT, pool VARCHAR""",
    "curve": """mint VARCHAR, slot UBIGINT, tx_index UBIGINT, outer_ix UBIGINT, inner_ix UBIGINT, variant VARCHAR, is_buy BOOLEAN,
      t HUGEINT, q HUGEINT, fee HUGEINT, creator_fee HUGEINT, fee_bps INTEGER, creator_bps INTEGER, cashback_bps INTEGER,
      vt HUGEINT, vq HUGEINT, rt HUGEINT, rq HUGEINT, trader VARCHAR, fee_payer VARCHAR, outer_program VARCHAR,
      priority_fee UBIGINT, jito_tip UBIGINT, tx_fee UBIGINT, mayhem_trade BOOLEAN, arg_amount HUGEINT, arg_budget HUGEINT,
      arg_max_cost HUGEINT, arg_min_tokens HUGEINT, arg_min_quote_out HUGEINT, arg_partial_fill BOOLEAN""",
    "pools": """pool VARCHAR, quote_class VARCHAR, orientation VARCHAR, quote_mint VARCHAR, mint VARCHAR, mint_source VARCHAR,
      mayhem BOOLEAN, mayhem_source VARCHAR, create_slot UBIGINT, coin_creator VARCHAR, can_boost BOOLEAN, b0 BIGINT,
      q0 BIGINT, created_in_period BOOLEAN, part INTEGER""",
    "pool_stats": """pool VARCHAR, first_slot UBIGINT, last_slot UBIGINT, n_buy HUGEINT, n_sell HUGEINT, reversed BOOLEAN,
      can_boost BOOLEAN, coin_creator VARCHAR, last_b BIGINT, last_vault BIGINT""",
    "pool": """kind VARCHAR, pool VARCHAR, slot UBIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT,
      trader VARCHAR, fee_payer VARCHAR, outer_program VARCHAR, priority_fee UBIGINT, jito_tip UBIGINT, tx_fee UBIGINT,
      parent_ix_disc VARCHAR, b BIGINT, vault BIGINT, vq BIGINT, e BIGINT, lp_bps INTEGER, protocol_bps INTEGER,
      creator_bps INTEGER, cashback_bps INTEGER, base BIGINT, e_delta BIGINT, quote_user BIGINT, limit_quote HUGEINT,
      limit_base HUGEINT, boost_left BIGINT, lp_amount BIGINT, lp_supply BIGINT, orientation VARCHAR, token_buy BOOLEAN,
      sol_amount BIGINT, sol_depth BIGINT, token_amount BIGINT, farmer BOOLEAN""",
    "pool_sweeps": "pool VARCHAR, slot UBIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT, amount BIGINT, bucket INTEGER",
    "pool_class_daily": """day DATE, quote_class VARCHAR, mayhem BOOLEAN, pools BIGINT, buys HUGEINT, sells HUGEINT,
      sol_volume HUGEINT, organic_sol_volume HUGEINT""",
}


def block(slot):
    return {"slot": slot, "block_time": T0 + (slot - WS) * 268 // 1000}


def mint(m, create, creator, **kw):
    return {"mint": m, "create_slot": create, "create_tx": 1, "creator": creator, "creator_field": creator, "mayhem": False,
            "quote_mint": SOLQ, "cashback": False, "holder_reward": False, "supply": 10 ** 15, "name": m, "symbol": m[:4],
            "complete_slot": None, "migrate_slot": None, "pool": None, **kw}


def curve(m, slot, is_buy, q, vq, rq, trader):
    return {"mint": m, "slot": slot, "tx_index": 3, "outer_ix": 0, "inner_ix": 1, "variant": "buy" if is_buy else "sell",
            "is_buy": is_buy, "t": 10 ** 12, "q": q, "fee": q // 100, "creator_fee": q // 200, "fee_bps": 95, "creator_bps": 30,
            "cashback_bps": 0, "vt": 10 ** 15, "vq": vq, "rt": 8 * 10 ** 14, "rq": rq, "trader": trader, "fee_payer": None,
            "outer_program": "pump", "priority_fee": 5000, "jito_tip": 0, "tx_fee": 5000, "mayhem_trade": False,
            "arg_amount": None, "arg_budget": None, "arg_max_cost": None, "arg_min_tokens": None, "arg_min_quote_out": None,
            "arg_partial_fill": None}


def pool_row(p, slot, buy, sol, depth, trader, orientation="reversed", farmer=False, tx=2):
    """A token buy (buy=True) or sell of `sol` lamports in pool p, `depth` lamports of real SOL before it."""
    kind = ("buy" if buy else "sell") if orientation == "normal" else ("sell" if buy else "buy")
    return {"kind": kind, "pool": p, "slot": slot, "tx_index": tx, "outer_ix": 0, "inner_ix": 1, "trader": trader,
            "fee_payer": None, "outer_program": "pump_amm", "priority_fee": 5000, "jito_tip": 0, "tx_fee": 5000,
            "parent_ix_disc": "x", "b": depth if orientation == "reversed" else 10 ** 15,
            "vault": 10 ** 15 if orientation == "reversed" else depth, "vq": 0, "e": depth, "lp_bps": 20, "protocol_bps": 5,
            "creator_bps": 5, "cashback_bps": 0, "base": sol, "e_delta": -sol if not buy else sol, "quote_user": sol,
            "limit_quote": None, "limit_base": None, "boost_left": None, "lp_amount": None, "lp_supply": None,
            "orientation": orientation, "token_buy": buy, "sol_amount": sol, "sol_depth": depth, "token_amount": 10 ** 9,
            "farmer": farmer}


def pools_row(p, cls, orientation, m, create, coin_creator, created, part, **kw):
    return {"pool": p, "quote_class": cls, "orientation": orientation, "quote_mint": m if orientation == "reversed" else WSOL,
            "mint": m, "mint_source": "create_pool" if created else None, "mayhem": False,
            "mayhem_source": "create_pool" if created else "fee_recipient", "create_slot": create, "coin_creator": coin_creator,
            "can_boost": False, "b0": 10 ** 12 if created else None, "q0": 10 ** 11 if created else None,
            "created_in_period": created, "part": part, **kw}


def stats(p, first, last, reversed_, coin_creator, last_b=10 ** 12):
    return {"pool": p, "first_slot": first, "last_slot": last, "n_buy": 1, "n_sell": 1, "reversed": reversed_,
            "can_boost": False, "coin_creator": coin_creator, "last_b": last_b, "last_vault": 10 ** 11}


def meta(**kw):
    return {"store_version": 2, "farmer": {"ratio": 0.05, "min_trades": 2, "window_ms": 86_400_000},
            "built_at_unix": 1, "build_seconds": 1, "counts": {}, **kw}


def chunk_list(lo, hi, sha="0cfc4db2" * 8):
    return [{"slots": [s, s + 216_000], "extractor_sha256": sha, "manifest_mtime": 1} for s in range(lo, hi, 216_000)]


def scenario():
    """(a, b): {table: [row]} plus "meta" for an S1-like A and a dev2-like B (see the module docstring)."""
    blocks = [block(s) for s in (WS + 10, WS + 5000, LO + 5, LO + 100, LO + 105, LO + 300, LO + 720, LO + 2000, HI - 1)]
    mints = [mint("M_A", LO + 5, "C1", complete_slot=LO + 500, migrate_slot=LO + 600, pool="P_NORM"),
             mint("M_B", WS + 100, "C2")]
    curves = [curve("M_A", LO + 280, True, SOL, 61 * SOL, 41 * SOL, "T1"),
              curve("M_A", LO + 300, False, 5 * SOL, 60 * SOL, 40 * SOL, "T2"),
              curve("M_B", WS + 200, True, SOL, 31 * SOL, 11 * SOL, "T3"),
              curve("M_B", LO + 50, True, SOL, 32 * SOL, 12 * SOL, "T3")]
    pools = [pools_row("P_NORM", "sol", "normal", "M_A", LO + 600, "C1", True, 0),
             pools_row("P_REV", "reversed", "reversed", "M_R", WS + 300, "CR", True, 1),
             pools_row("P_OLD", "reversed", "reversed", None, None, "CO", False, 2)]
    rows = [pool_row("P_REV", LO + 100, True, 2 * SOL, 200 * SOL, "W0"),
            pool_row("P_REV", LO + 105, False, 10 * SOL, 202 * SOL, "W1"),
            pool_row("P_NORM", LO + 700, True, 2 * SOL, 200 * SOL, "W0", "normal"),
            pool_row("P_NORM", LO + 720, False, 10 * SOL, 202 * SOL, "W2", "normal"),
            pool_row("P_OLD", WS + 5000, True, SOL, 190 * SOL, "W0"),
            pool_row("P_OLD", LO + 1990, True, 2 * SOL, 200 * SOL, "W0"),
            pool_row("P_OLD", LO + 2000, False, 10 * SOL, 202 * SOL, "W3")]
    st = [stats("P_NORM", LO + 700, LO + 720, False, "C1"), stats("P_REV", LO + 100, LO + 105, True, "CR"),
          stats("P_OLD", WS + 5000, LO + 2000, True, "CO")]
    daily = [{"day": "2025-10-10", "quote_class": "sol", "mayhem": False, "pools": 1, "buys": 1, "sells": 1,
              "sol_volume": 12 * SOL, "organic_sol_volume": 12 * SOL}]
    a = {"blocks": blocks, "mints": mints, "curve": curves, "pools": pools, "pool_stats": st, "pool": rows, "pool_sweeps": [],
         "pool_class_daily": daily,
         "meta": meta(period="dev-diag-clean-path", slot_start=WS, slot_end_exclusive=HI, chunks=4,
                      chunk_list=chunk_list(WS, HI),
                      ms_per_slot={"1042": 267.83, "1043": 267.93}, window=[LO, HI], warmup_epochs=1,
                      purpose="DEV DIAGNOSTIC (ii) fixture", diagnostic=True, git_commit="be47fc425fc4",
                      excluded_pools_int64={"pools": 0, "reversed": 0, "not_reversed": 0, "no_trade_rows": 0,
                                            "rows_out_of_range": {}})}
    b = copy.deepcopy(a)
    b["blocks"] += [block(WS - 50), block(HI + 50)]
    b["mints"][1]["complete_slot"] = HI + 100  # look-ahead: completes after the window
    b["mints"] += [mint("M_OLD", WS - 1000, "C3"), mint("M_LATE", HI + 10, "C4")]
    b["curve"] += [curve("M_OLD", LO + 70, True, SOL, 33 * SOL, 13 * SOL, "T4"),
                   curve("M_A", HI + 5, True, SOL, 62 * SOL, 42 * SOL, "T1")]
    b["pools"] = [dict(p, part=p["part"] + 5) for p in b["pools"]]  # dev2: 16 parts
    b["pools"][1]["can_boost"] = True  # a boost after the window
    b["pools"][2].update(quote_mint="M_X", mint="M_X", mint_source="create_pool", mayhem_source="create_pool",
                         create_slot=WS - 20_000, b0=10 ** 12, q0=10 ** 11, created_in_period=True)
    b["pools"].append(pools_row("P_GONE", "sol", "normal", "M_G", WS - 30_000, "CG", True, 9))
    b["pool"] += [pool_row("P_REV", HI + 10, True, SOL, 210 * SOL, "W0"),
                  pool_row("P_GONE", WS - 25_000, True, SOL, 300 * SOL, "W0", "normal")]
    b["pool_stats"][1].update(last_slot=HI + 10, can_boost=True)
    b["pool_stats"][2].update(first_slot=WS - 20_000)
    b["pool_stats"].append(stats("P_GONE", WS - 25_000, WS - 25_000, False, "CG"))
    b["meta"] = meta(period="dev", slot_start=1033 * EPOCH, slot_end_exclusive=1047 * EPOCH, chunks=28,
                     chunk_list=chunk_list(1033 * EPOCH, 1047 * EPOCH),
                     ms_per_slot={str(e): (267.83 if e == 1042 else 267.93 if e == 1043 else 266.5) for e in range(1033, 1047)},
                     git_commit="ba322be60d25c50ddd1aa5107ed85d471cd711d7")
    return a, b


def s0_like(a):
    """A as ba322be's store.py would build it from the same chunks: the same tables, a dev meta without the u64 key."""
    s0 = copy.deepcopy(a)
    s0["meta"] = {k: v for k, v in s0["meta"].items()
                  if k not in ("excluded_pools_int64", "window", "warmup_epochs", "purpose", "diagnostic")}
    s0["meta"].update(period="dev", git_commit="ba322be60d25c50ddd1aa5107ed85d471cd711d7")
    return s0


def write(path, store, types=None, skip=()):
    """The store file at path: every SCHEMA table (types overrides {(table, column): type}; skip leaves tables out),
    the rows of store[table] (missing columns NULL), and meta."""
    con = duckdb.connect(path)
    for t, cols in SCHEMA.items():
        if t in skip:
            continue
        defs = []
        for c in cols.split(","):
            name, typ = c.split()
            defs.append(f"{name} {(types or {}).get((t, name), typ)}")
        con.execute(f"CREATE TABLE {t} ({', '.join(defs)})")
        for r in store.get(t, []):
            con.execute(f"INSERT INTO {t} ({', '.join(r)}) VALUES ({', '.join('?' for _ in r)})", list(r.values()))
    con.execute("CREATE TABLE meta AS SELECT ? AS json", [json.dumps(store["meta"])])
    con.close()
    return path
