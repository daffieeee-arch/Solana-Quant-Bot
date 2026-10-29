"""Walvis stap 1a: leader episodes, one per wallet x token position opened by a buy >= 1 SOL (#167).

An episode of wallet w in token k (curve mint, or pool for pools without a known mint) opens at a
buy of >= TRIGGER_SOL that is not flagged as volume farming (store `farmer`, backward-looking)
while w holds no open episode in k. It then follows w's own trades in k, curve and pools of the
mint together, for at most EPISODE_MS:

  H        tokens held since the trigger (later buys add; sells take at most H: tokens the wallet
           held from before the trigger, or received by transfer, are not ours to count)
  cost     SOL paid for the trigger and later buys inside the episode
  proceeds SOL received for the sold part (pro rata when a sell exceeds H)
  hold     slots from the trigger to the first sell after which H <= EXIT_SHARE x max H; NULL when
           the episode is still open at its end (censored at EPISODE_MS or at the data end)
  mark     price of the token's last trade (any wallet) at or before the episode end, for the
           remaining tokens

  leader_ret_realized = proceeds / cost - 1              (remaining tokens worth 0)
  leader_ret_marked   = (proceeds + H_end x mark) / cost - 1

The leader's return is what the wallet earned; the follower's return (follow.py) is what we would
have earned entering at t0 + d with our own costs and impact. Episodes are the unit of the
point-in-time score: on day D only episodes with end_slot < D count.

  python -m wallets.episodes --from-slot A --to-slot B [--out DIR]

Triggers in [A, B); legs are read from A - EPISODE_MS (so an episode opened just before A is not
opened again at A) to B + EPISODE_MS. Output: episodes-<A>.parquet. Slots stay before the hold-out.
"""

import argparse
import json
import os
import sys
import time

import pyarrow as pa
import pyarrow.parquet as pq

from .common import OUT_ROOT, SOL, Clock, connect, forward_guard, git_commit, guard, store_meta

TRIGGER_SOL = 1 * SOL
EPISODE_MS = 4 * 3_600_000
EXIT_SHARE = 0.10
BUCKETS = 16  # wallet hash buckets per query: bounds the sorted working set (~0.8 GB peak RSS)
MEMORY = os.environ.get("LAB_EPISODES_MEMORY", os.environ.get("LAB_WALLETS_MEMORY", "640MB"))  # DuckDB limit; VPS default
POS = 1 << 20
MARK_MIN_SOL = SOL // 100  # a trade this small does not set the mark price
MARK_MAX_MULT = 50  # the mark is capped at this multiple of the episode's average buy price (flagged)
SOL_QUOTES = ("11111111111111111111111111111111", "So11111111111111111111111111111111111111112")
POOL_KEY = "CASE WHEN p.pool = m.pool THEN p.mint ELSE e.pool END"


def legs_sql(lo, hi, bucket, trigger_sol=TRIGGER_SOL, min_offset=0):
    """Token-side legs of SOL markets in [lo, hi) for wallets in one hash bucket, chain order.
    (wallet, key, venue, orientation, slot, pos, tokens, sol, token_buy, trigger, create_slot, pool, outer_ix, inner_ix)

    The order must be total: walk() is order-sensitive (exit share, time cap), and two legs of one
    wallet and key in one transaction (buy and sell in one tx, several events) tie on pos. The
    instruction position in the transaction (outer_ix, inner_ix) breaks the tie, then the leg's
    content; without it a rerun of the same slice differed in ~0.4% of the episodes (NAS, 2026-10-11).

    The key joins a token's curve with its canonical migration pool (mints.pool), so a curve buyer
    who sells after graduation is followed into the pool; any other pool of the mint is its own key
    (dust pools of the same mint must not mark or close a position).

    trigger_sol: the buy size that opens an episode (lamports; E82: 0.5 SOL). min_offset > 0 (E82: 4): a trigger
    must lie >= min_offset slots after the token's create slot; a pool whose mint has no create row (a pool from
    before the store window) then never triggers, because its offset is unknown."""
    off_c = f" AND c.slot >= m.create_slot + {min_offset}" if min_offset else ""
    off_p = f" AND m.create_slot IS NOT NULL AND e.slot >= m.create_slot + {min_offset}" if min_offset else ""
    quotes = ",".join(f"'{q}'" for q in SOL_QUOTES)
    return f"""
      SELECT * FROM (
        SELECT c.trader AS wallet, c.mint AS key, 'curve' AS venue, 'curve' AS orientation, c.slot::BIGINT AS slot,
               c.slot::BIGINT * {POS} + c.tx_index AS pos, c.t::BIGINT AS tokens, c.q::BIGINT AS sol, c.is_buy AS token_buy,
               c.is_buy AND c.q >= {trigger_sol}{off_c} AS trigger, c.slot = m.create_slot AS create_slot, NULL::VARCHAR AS pool,
               c.outer_ix::BIGINT AS outer_ix, c.inner_ix::BIGINT AS inner_ix
        FROM curve c JOIN mints m USING (mint)
        WHERE c.slot >= {lo} AND c.slot < {hi} AND NOT m.mayhem AND m.quote_mint IN ({quotes})
        UNION ALL
        SELECT e.trader, {POOL_KEY}, 'pool', p.orientation, e.slot::BIGINT, e.slot::BIGINT * {POS} + e.tx_index,
               e.token_amount, e.sol_amount, e.token_buy,
               e.token_buy AND e.sol_amount >= {trigger_sol} AND NOT e.farmer{off_p}, false, e.pool,
               e.outer_ix::BIGINT, e.inner_ix::BIGINT
        FROM pool e JOIN pools p USING (pool) LEFT JOIN mints m ON m.mint = p.mint
        WHERE e.slot >= {lo} AND e.slot < {hi} AND e.kind IN ('buy', 'sell')
          AND p.quote_class IN ('sol', 'reversed') AND NOT COALESCE(p.mayhem, false))
      WHERE hash(wallet) % {BUCKETS} = {bucket}
      ORDER BY wallet, key, pos, outer_ix, inner_ix, venue, token_buy DESC, tokens, sol"""


FIELDS = ["wallet", "key", "venue", "orientation", "t0", "t0_pos", "trigger_sol", "cost_sol", "proceeds_sol",
          "remaining_tokens", "max_tokens", "hold_slots", "end_slot", "n_buys", "n_sells", "create_slot", "closed"]


def walk(rows, lo, hi, episode_slots, first_buy_only=False):
    """Episodes from legs of one (wallet, key) group in chain order; emits triggers in [lo, hi).

    first_buy_only (E82): only the wallet's first buy of the key in the loaded legs can open an episode — a
    trigger that follows an earlier (non-trigger or episode) buy opens nothing, so a launch-slot buyer who tops up
    later is not a signal (E81's round definition: first buy >= 4 slots after the create)."""
    out, ep, seen_buy = [], None, False

    def close(ep):
        if ep.pop("emit"):  # episodes opened before lo are walked (their top-ups are not triggers) but not emitted
            out.append(ep)

    for row in rows:
        wallet, key, venue, orientation, slot, pos, tokens, sol, token_buy, trigger, create_slot, pool = row[:12]
        if ep is not None and slot > ep["end_slot"]:
            close(ep)  # time cap: closed at end_slot, remaining tokens marked there
            ep = None
        if ep is None:
            if trigger and not (first_buy_only and seen_buy):
                if slot >= hi:
                    break
                ep = {"wallet": wallet, "key": key, "venue": venue, "orientation": orientation, "t0": slot, "t0_pos": pos,
                      "trigger_sol": sol, "cost_sol": sol, "proceeds_sol": 0, "remaining_tokens": tokens,
                      "max_tokens": tokens, "hold_slots": None, "end_slot": slot + episode_slots, "n_buys": 1,
                      "n_sells": 0, "create_slot": bool(create_slot), "closed": False, "pool": pool, "emit": slot >= lo}
                seen_buy = True
            elif token_buy:
                seen_buy = True
            continue
        if token_buy:
            ep["cost_sol"] += sol
            ep["remaining_tokens"] += tokens
            ep["max_tokens"] = max(ep["max_tokens"], ep["remaining_tokens"])
            ep["n_buys"] += 1
        else:
            sold = min(tokens, ep["remaining_tokens"])
            if sold <= 0:
                continue
            ep["proceeds_sol"] += sol * sold // tokens
            ep["remaining_tokens"] -= sold
            ep["n_sells"] += 1
            if ep["remaining_tokens"] <= EXIT_SHARE * ep["max_tokens"]:
                ep["hold_slots"], ep["end_slot"], ep["closed"] = slot - ep["t0"], slot, True
                close(ep)
                ep = None
    if ep is not None:
        close(ep)
    return out


def run(lo, hi, out_dir, store=None, forward=False, trigger_sol=TRIGGER_SOL, min_offset=0, first_buy_only=False):
    """forward (E82): a forward store from epoch 1064 on (common.forward_guard) instead of the dev guard; the legs read
    EPISODE_MS before lo and after hi must lie inside the store."""
    if not forward:
        guard(hi)
    con = connect(store, memory=MEMORY) if store else connect(memory=MEMORY)
    clock = Clock.from_store(con)
    n_ep = clock.slots(EPISODE_MS, lo)
    if forward:
        forward_guard(lo - n_ep, hi + n_ep, store_meta(con))
    else:
        guard(hi + n_ep)
    os.makedirs(out_dir, exist_ok=True)
    t_start = time.time()
    eps = []
    for b in range(BUCKETS):
        res = con.execute(legs_sql(lo - n_ep, hi + n_ep, b, trigger_sol, min_offset))
        group, cur = [], None
        while True:
            batch = res.fetchmany(100_000)
            for r in batch:
                k = (r[0], r[1])
                if k != cur:
                    if group:
                        eps += walk(group, lo, hi, n_ep, first_buy_only)
                    group, cur = [], k
                group.append(r)
            if not batch:
                break
        if group:
            eps += walk(group, lo, hi, n_ep, first_buy_only)
        print(f"bucket {b}: {len(eps)} episodes so far, {time.time() - t_start:.0f}s", file=sys.stderr, flush=True)
    tbl = pa.Table.from_pylist(eps, schema=pa.schema([
        ("wallet", pa.string()), ("key", pa.string()), ("venue", pa.string()), ("orientation", pa.string()),
        ("t0", pa.int64()), ("t0_pos", pa.int64()), ("trigger_sol", pa.int64()), ("cost_sol", pa.int64()),
        ("proceeds_sol", pa.int64()), ("remaining_tokens", pa.int64()), ("max_tokens", pa.int64()),
        ("hold_slots", pa.int64()), ("end_slot", pa.int64()), ("n_buys", pa.int64()), ("n_sells", pa.int64()),
        ("create_slot", pa.bool_()), ("closed", pa.bool_()), ("pool", pa.string())]))
    con.register("ep", tbl)
    # Mark the remaining tokens at the token's last trade of >= MARK_MIN_SOL at or before the
    # episode end, capped at MARK_MAX_MULT x the episode's average buy price; stamp the UTC day.
    out = con.execute(f"""
      WITH px AS (
        SELECT key, slot, sol::DOUBLE / tokens AS price FROM (
          SELECT c.mint AS key, c.slot::BIGINT AS slot, c.tx_index, c.outer_ix, c.inner_ix, c.q AS sol, c.t AS tokens FROM curve c
          WHERE c.slot >= {lo} AND c.slot < {hi + n_ep} AND c.t > 0 AND c.q >= {MARK_MIN_SOL}
          UNION ALL
          SELECT {POOL_KEY}, e.slot::BIGINT, e.tx_index, e.outer_ix, e.inner_ix, e.sol_amount, e.token_amount
          FROM pool e JOIN pools p USING (pool) LEFT JOIN mints m ON m.mint = p.mint
          WHERE e.slot >= {lo} AND e.slot < {hi + n_ep} AND e.kind IN ('buy', 'sell') AND e.token_amount > 0
            AND e.sol_amount >= {MARK_MIN_SOL})
        -- the last trade of the slot: a total order (tx, then instruction position) keeps the mark reproducible
        QUALIFY row_number() OVER (PARTITION BY key, slot ORDER BY tx_index DESC, outer_ix DESC, inner_ix DESC, sol DESC) = 1),
      d AS (SELECT slot, strftime(make_timestamp(block_time * 1000000), '%Y-%m-%d') AS day FROM blocks
            WHERE slot >= {lo} AND slot < {hi}),
      m AS (SELECT ep.*, px.price AS last_price, ep.cost_sol::DOUBLE / ep.max_tokens AS avg_buy_price, d.day
            FROM ep ASOF LEFT JOIN px ON px.key = ep.key AND px.slot <= ep.end_slot
            ASOF LEFT JOIN d ON d.slot <= ep.t0)
      SELECT * EXCLUDE (last_price, avg_buy_price),
             LEAST(last_price, {MARK_MAX_MULT} * avg_buy_price) AS mark_price,
             last_price > {MARK_MAX_MULT} * avg_buy_price AS mark_capped,
             proceeds_sol::DOUBLE / cost_sol - 1 AS leader_ret_realized,
             (proceeds_sol + COALESCE(remaining_tokens * LEAST(last_price, {MARK_MAX_MULT} * avg_buy_price), 0)) / cost_sol - 1
               AS leader_ret_marked
      FROM m ORDER BY t0, wallet""").to_arrow_table()
    pq.write_table(out, os.path.join(out_dir, f"episodes-{lo}.parquet"))
    with open(os.path.join(out_dir, f"run-episodes-{lo}-{hi}.json"), "w") as f:
        json.dump({"from_slot": lo, "to_slot": hi, "commit": git_commit(), "trigger_sol": trigger_sol, "min_offset": min_offset,
                   "first_buy_only": first_buy_only, "forward": forward,
                   "episode_ms": EPISODE_MS, "exit_share": EXIT_SHARE, "episodes": out.num_rows,
                   "seconds": round(time.time() - t_start)}, f, indent=1)
    print(f"{lo}-{hi}: {out.num_rows} episodes, {time.time() - t_start:.0f}s", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-slot", type=int, required=True)
    ap.add_argument("--to-slot", type=int, required=True)
    ap.add_argument("--out", default=os.path.join(OUT_ROOT, "episodes-v1"))
    ap.add_argument("--store")
    ap.add_argument("--forward", action="store_true", help="a forward store from epoch 1064 on (common.forward_guard) instead of the dev guard")
    ap.add_argument("--trigger-sol", type=float, default=TRIGGER_SOL / SOL, help="buy size that opens an episode, SOL (default 1)")
    ap.add_argument("--min-offset", type=int, default=0, help="a trigger must lie >= this many slots after the token's create (E82: 4)")
    ap.add_argument("--first-buy-only", action="store_true", help="only a wallet's first buy of a key can open an episode (E82)")
    a = ap.parse_args()
    run(a.from_slot, a.to_slot, a.out, a.store, a.forward, int(round(a.trigger_sol * SOL)), a.min_offset, a.first_buy_only)


if __name__ == "__main__":
    main()
