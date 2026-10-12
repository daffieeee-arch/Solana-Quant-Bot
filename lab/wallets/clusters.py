"""Walvis stap 0: bundles and payer clusters from columns already in the store (roadmap #167).

A cluster event is a group of >= 2 different wallets that buy the same token (curve mint or pool)
in the same slot and are linked by at least one of:

  tx   the buys are in one transaction (every buying wallet signs it: shared co-signers);
  pay  the same fee payer, when it is not the wallet itself (the payer is a node of the cluster);
  tip  adjacent transactions (tx_index differs by 1) with the same Jito tip > 0 (a likely bundle).

Links are joined with union-find per (venue, token, slot). Fee payers that, in the 24 h before the window being
processed, paid for >= 100 different wallets in >= 50 different tokens are services (relayers, gas
sponsors) and give no `pay` link; that registry looks back only. (On a sample day 18 payers met
this and paid for 843k trades; ~400 payers with 20-49 wallets in a median of 4 tokens look like
bundlers and stay clusters.)

For every cluster event, and for every solo buy (one wallet, not in a cluster) as the baseline,
we measure the share of the bought tokens that the event's wallets sold of the same token (curve
and pools of the mint together) within
3 s, 40 s and 10 min (in ms, converted to slots per epoch). Sells in the same slot count when
their transaction comes after the event's last buy.

  python -m wallets.clusters --from-slot A --to-slot B [--out DIR]

Writes per 20-min window: events-<a>.parquet (cluster events), members-<a>.parquet and
stats-<a>.parquet (aggregates for clusters and solo buys). Slots must lie before the hold-out.
"""

import argparse
import json
import os
import sys
import time

import pyarrow.parquet as pq

from .common import OUT_ROOT, SOL, Clock, connect, git_commit, guard

HOUR_MS = 3_600_000
WINDOW_MS = 20 * 60_000  # buys are processed in windows of 20 min (bounded memory)
SERVICE_WINDOW_MS = 24 * HOUR_MS
SERVICE_MIN_WALLETS = 100
SERVICE_MIN_TOKENS = 50
SELL_WINDOWS_MS = {"3s": 3_000, "40s": 40_000, "10m": 600_000}
POS = 1 << 20  # chain position of a transaction: slot * POS + tx_index
SOL_QUOTES = ("11111111111111111111111111111111", "So11111111111111111111111111111111111111112")


def legs_sql(lo, hi):
    """Trade legs of SOL markets (non-mayhem) in [lo, hi), on the token side: buy = wallet gets tokens."""
    quotes = ",".join(f"'{q}'" for q in SOL_QUOTES)
    return f"""
      SELECT 'curve' AS venue, c.mint AS key, c.mint, 'curve' AS orientation, c.slot::BIGINT AS slot,
             c.tx_index::BIGINT AS tx_index, c.trader, COALESCE(c.fee_payer, c.trader) AS payer,
             COALESCE(c.jito_tip, 0)::BIGINT AS tip, c.is_buy AS buy, c.t::BIGINT AS tokens, c.q::BIGINT AS sol,
             false AS farmer, c.slot = m.create_slot AS create_slot
      FROM curve c JOIN mints m USING (mint)
      WHERE c.slot >= {lo} AND c.slot < {hi} AND NOT m.mayhem AND m.quote_mint IN ({quotes})
      UNION ALL
      SELECT 'pool', e.pool, p.mint, p.orientation, e.slot::BIGINT, e.tx_index::BIGINT, e.trader,
             COALESCE(e.fee_payer, e.trader), COALESCE(e.jito_tip, 0)::BIGINT, e.token_buy, e.token_amount,
             e.sol_amount, e.farmer, false
      FROM pool e JOIN pools p USING (pool)
      WHERE e.slot >= {lo} AND e.slot < {hi} AND e.kind IN ('buy', 'sell')
        AND p.quote_class IN ('sol', 'reversed') AND NOT COALESCE(p.mayhem, false)"""


def service_payers(con, at, clock):
    """Fee payers that paid for >= SERVICE_MIN_WALLETS other wallets in >= SERVICE_MIN_TOKENS
    tokens in [at - 24 h, at)."""
    lo = max(0, at - clock.slots(SERVICE_WINDOW_MS, at))
    rows = con.execute(f"""
        SELECT payer FROM (
          SELECT fee_payer AS payer, trader, mint AS k FROM curve
          WHERE slot >= {lo} AND slot < {at} AND fee_payer IS NOT NULL
          UNION ALL
          SELECT fee_payer, trader, pool FROM pool WHERE slot >= {lo} AND slot < {at} AND fee_payer IS NOT NULL
            AND kind IN ('buy', 'sell'))
        WHERE payer <> trader GROUP BY payer
        HAVING count(DISTINCT trader) >= {SERVICE_MIN_WALLETS} AND count(DISTINCT k) >= {SERVICE_MIN_TOKENS}""").fetchall()
    return {r[0] for r in rows}


EDGES_SQL = """
  WITH b AS (SELECT * FROM legs WHERE buy AND slot >= $lo AND slot < $hi),
  txw AS (SELECT venue, key, slot, tx_index, trader, min(trader) OVER (PARTITION BY venue, key, slot, tx_index) AS root
          FROM (SELECT DISTINCT venue, key, slot, tx_index, trader FROM b)),
  tx AS (SELECT venue, key, slot, tx_index, min(trader) AS trader, max(tip) AS tip FROM b GROUP BY ALL),
  adj AS (SELECT *, lag(tx_index) OVER w AS p_tx, lag(trader) OVER w AS p_trader, lag(tip) OVER w AS p_tip
          FROM tx WINDOW w AS (PARTITION BY venue, key, slot ORDER BY tx_index))
  SELECT venue, key, slot, trader AS u, root AS v, 'tx' AS link FROM txw WHERE trader <> root
  UNION ALL
  SELECT DISTINCT venue, key, slot, trader, payer, 'pay' FROM b
  WHERE payer <> trader AND payer NOT IN (SELECT payer FROM service)
  UNION ALL
  SELECT venue, key, slot, trader, p_trader, 'tip' FROM adj
  WHERE tx_index = p_tx + 1 AND tip > 0 AND tip = p_tip AND trader <> p_trader"""


class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        p = self.parent.setdefault(x, x)
        while p != self.parent[p]:
            self.parent[p] = self.parent[self.parent[p]]
            p = self.parent[p]
        self.parent[x] = p
        return p

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def components(edges, buyers):
    """Cluster events from link edges.

    edges: iterable of (venue, key, slot, u, v, link); buyers: set of (venue, key, slot, wallet) that
    bought. Returns [(venue, key, slot, sorted buyer wallets, sorted other nodes, sorted links)] for
    components with >= 2 buying wallets."""
    uf, links = UnionFind(), {}
    for venue, key, slot, u, v, link in edges:
        a, b = (venue, key, slot, u), (venue, key, slot, v)
        uf.union(a, b)
        links.setdefault(a, set()).add(link)
    groups = {}
    for node in list(uf.parent):
        groups.setdefault(uf.find(node), []).append(node)
    out = []
    for nodes in groups.values():
        buy = sorted(n[3] for n in nodes if n in buyers)
        if len(buy) < 2:
            continue
        other = sorted(n[3] for n in nodes if n not in buyers)
        ls = sorted(set().union(*(links.get(n, set()) for n in nodes)))
        out.append((nodes[0][0], nodes[0][1], nodes[0][2], buy, other, ls))
    return sorted(out)


def process_window(con, a, b, clock, service):
    """Cluster events and sell stats for buys in [a, b); sells are read up to b + 10 min."""
    tail = max(clock.slots(ms, b) for ms in SELL_WINDOWS_MS.values())
    con.execute(f"CREATE OR REPLACE TEMP TABLE legs AS SELECT * FROM ({legs_sql(a, b)}) WHERE buy")
    con.execute("CREATE OR REPLACE TEMP TABLE service (payer VARCHAR)")
    if service:
        con.executemany("INSERT INTO service VALUES (?)", [(p,) for p in sorted(service)])
    edges = con.execute(EDGES_SQL.replace("$lo", str(a)).replace("$hi", str(b))).fetchall()
    keys = {(e[0], e[1], e[2]) for e in edges}
    con.execute("CREATE OR REPLACE TEMP TABLE ekeys (venue VARCHAR, key VARCHAR, slot BIGINT)")
    con.executemany("INSERT INTO ekeys VALUES (?, ?, ?)", sorted(keys))
    buyers = {tuple(r) for r in con.execute(
        "SELECT DISTINCT l.venue, l.key, l.slot, l.trader FROM legs l SEMI JOIN ekeys k USING (venue, key, slot)").fetchall()}
    comps = components(edges, buyers)

    # Cluster members (buyers and their payer nodes); every other buying wallet is a solo event.
    rows = []
    for i, (venue, key, slot, buy, other, ls) in enumerate(comps):
        rows += [(i, venue, key, slot, w, True, ",".join(ls)) for w in buy]
        rows += [(i, venue, key, slot, w, False, ",".join(ls)) for w in other]
    con.execute("""CREATE OR REPLACE TEMP TABLE cm (eid BIGINT, venue VARCHAR, key VARCHAR, slot BIGINT,
                   wallet VARCHAR, buyer BOOLEAN, links VARCHAR)""")
    if rows:
        con.executemany("INSERT INTO cm VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    con.execute("""
      CREATE OR REPLACE TEMP TABLE bl AS
      SELECT venue, key, slot, trader, any_value(mint) AS mint, any_value(orientation) AS orientation,
             sum(tokens) AS tokens, sum(sol) AS sol, max(tx_index) AS last_tx, bool_or(farmer) AS farmer,
             bool_or(create_slot) AS create_slot
      FROM legs GROUP BY venue, key, slot, trader""")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE e AS
      SELECT cm.eid, 'cluster' AS kind, cm.venue, cm.key, any_value(cm.slot) AS slot, max(bl.last_tx) AS last_tx,
             sum(bl.tokens) AS tokens, sum(bl.sol) AS sol, count(bl.tokens) AS n_wallets, any_value(cm.links) AS links,
             bool_or(bl.farmer) AS farmer, bool_or(bl.create_slot) AS create_slot, any_value(bl.mint) AS mint,
             any_value(bl.orientation) AS orientation, NULL::VARCHAR AS trader
      FROM cm LEFT JOIN bl ON bl.venue = cm.venue AND bl.key = cm.key AND bl.slot = cm.slot AND bl.trader = cm.wallet
      GROUP BY cm.eid, cm.venue, cm.key
      UNION ALL
      SELECT {1 << 40} + row_number() OVER (), 'solo', venue, key, slot, last_tx, tokens, sol, 1, '', farmer,
             create_slot, mint, orientation, trader
      FROM (SELECT * FROM bl ANTI JOIN cm ON cm.venue = bl.venue AND cm.key = bl.key AND cm.slot = bl.slot
                                          AND cm.wallet = bl.trader)""")
    # Wallets whose sells count for an event: cluster members, or the solo buyer.
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE m AS
      SELECT cm.eid, hash(COALESCE(e.mint, e.key)) AS k, hash(cm.wallet) AS w, e.slot, e.last_tx
      FROM cm JOIN e USING (eid)
      UNION ALL
      SELECT eid, hash(COALESCE(mint, key)), hash(trader), slot, last_tx FROM e WHERE kind = 'solo'""")
    # Tokens a wallet sold of a token (by mint, on the curve and in any of its pools, so a curve buy
    # sold after graduation counts), cumulative in chain order: sold in (p0, p1] = cum(p1) - cum(p0).
    # Linear in the number of legs (a range join is quadratic for bots with thousands of trades);
    # token and wallet are hashed to keep the working set small.
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE sc AS
      SELECT k, w, pos, sum(tokens) OVER (PARTITION BY k, w ORDER BY pos) AS cum
      FROM (SELECT hash(COALESCE(mint, key)) AS k, hash(trader) AS w, slot * {POS} + tx_index AS pos, sum(tokens) AS tokens
            FROM ({legs_sql(a, b + tail)}) WHERE NOT buy GROUP BY ALL)""")
    n_slots = {k: clock.slots(ms, a) for k, ms in SELL_WINDOWS_MS.items()}
    joins = [f"ASOF LEFT JOIN sc c0 ON c0.k = m.k AND c0.w = m.w AND c0.pos <= m.slot * {POS} + m.last_tx"]
    joins += [f"ASOF LEFT JOIN sc c_{k} ON c_{k}.k = m.k AND c_{k}.w = m.w "
              f"AND c_{k}.pos <= (m.slot + {n}) * {POS} + {POS - 1}" for k, n in n_slots.items()]
    sold = ", ".join(f"sum(COALESCE(c_{k}.cum, 0) - COALESCE(c0.cum, 0)) AS sold_{k}" for k in n_slots)
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE ev AS
      WITH s AS (SELECT m.eid, {sold} FROM m {' '.join(joins)} GROUP BY m.eid)
      SELECT e.*, {', '.join(f'COALESCE(s.sold_{k}, 0) AS sold_{k}' for k in n_slots)}
      FROM e LEFT JOIN s USING (eid)""")
    stats = con.execute(f"""
      SELECT {a}::BIGINT AS window_slot, kind, venue, orientation, create_slot, farmer,
             CASE WHEN n_wallets >= 5 THEN '5+' ELSE n_wallets::VARCHAR END AS size,
             CASE WHEN kind = 'solo' THEN '' WHEN links LIKE '%tip%' THEN 'tip' WHEN links LIKE '%tx%' THEN 'tx' ELSE 'pay' END AS link,
             count(*) AS events, sum(n_wallets) AS wallets, sum(sol) / {SOL} AS sol,
             {', '.join(f"sum(least(1.0, sold_{k}::DOUBLE / tokens)) AS share_sum_{k}, "
                        f"sum((sold_{k} > 0)::INT) AS any_{k}, sum((sold_{k} >= 0.5 * tokens)::INT) AS half_{k}"
                        for k in n_slots)}
      FROM ev WHERE tokens > 0 GROUP BY ALL""").to_arrow_table()
    events = con.execute(f"SELECT {a}::BIGINT AS window_slot, * EXCLUDE (trader) FROM ev WHERE kind = 'cluster' ORDER BY eid").to_arrow_table()
    members = con.execute(f"SELECT {a}::BIGINT AS window_slot, eid, venue, key, slot, wallet, buyer FROM cm ORDER BY eid").to_arrow_table()
    return events, members, stats, len(edges)


def windows(lo, hi, clock):
    """[a, b) windows of WINDOW_MS each (slots from the clock at a)."""
    out, a = [], lo
    while a < hi:
        b = min(hi, a + clock.slots(WINDOW_MS, a))
        out.append((a, b))
        a = b
    return out


def run(lo, hi, out_dir, store=None):
    guard(hi)
    con = connect(store) if store else connect()
    clock = Clock.from_store(con)
    # Sells are read up to 10 min after the last buy window; stay before the hold-out.
    tail = max(clock.slots(ms, hi) for ms in SELL_WINDOWS_MS.values())
    guard(hi + tail)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"run-{lo}-{hi}.json"), "w") as f:
        json.dump({"from_slot": lo, "to_slot": hi, "commit": git_commit(), "service_min_wallets": SERVICE_MIN_WALLETS, "service_min_tokens": SERVICE_MIN_TOKENS,
                   "service_window_ms": SERVICE_WINDOW_MS, "sell_windows_ms": SELL_WINDOWS_MS,
                   "started_unix": int(time.time())}, f, indent=1)
    for a, b in windows(lo, hi, clock):
        if os.path.exists(os.path.join(out_dir, f"stats-{a}.parquet")):
            continue
        t0 = time.time()
        service = service_payers(con, a, clock)
        events, members, stats, n_edges = process_window(con, a, b, clock, service)
        for name, tbl in (("events", events), ("members", members), ("stats", stats)):  # stats last: marks done
            pq.write_table(tbl, os.path.join(out_dir, f"{name}-{a}.parquet"))
        print(f"{a}-{b}: {events.num_rows} cluster events, {n_edges} edges, {len(service)} service payers, "
              f"{time.time() - t0:.1f}s", file=sys.stderr, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-slot", type=int, required=True)
    ap.add_argument("--to-slot", type=int, required=True)
    ap.add_argument("--out", default=os.path.join(OUT_ROOT, "clusters-v1"))
    ap.add_argument("--store")
    a = ap.parse_args()
    run(a.from_slot, a.to_slot, a.out, a.store)


if __name__ == "__main__":
    main()
