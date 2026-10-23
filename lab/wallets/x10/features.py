"""x10-jager stage B (research/x10-jager-plan-2026-10-23.md v0.2 sections 2 and 3, families F3 / F4 / F5 and the purge
flags; roadmap #167): one table over every stage-A window, point-in-time by construction.

Inputs: a stage-A directory (labels-<lo>.parquet, mint_peaks-<lo>.parquet, early_buyers-<lo>.parquet of every window),
the store (mints.migrate_slot, the hourly pump-wide context, the clock), and the money-v2 wallet-day parquets.

  F3 early-buyer quality: the entry's early buyers = the wallets that bought within 60 s of the create, with a first buy
     at or before the moment t, the first 20 by first buy. Per wallet its history is the early buys of other coins
     and whether that coin reached 5 x its first price; only what is KNOWN BEFORE THE ENTRY SLOT counts (plan M1):
     an early buy with first_buy_slot < entry_slot, a hit with max(first_buy_slot, first_x5_slot) < entry_slot. The
     entry's own coin is taken out. Aggregated per entry: buyers used, share of buyers with >= 1 earlier known hit,
     mean known hit rate, sum of known hits, mean number of earlier early buys. money-v2 day labels of those buyers on
     the UTC day before the entry day (nothing after d - 1; plan M4): share active, share bot (>= 50 positions opened
     and median hold <= 30 s), share sniper (>= 1 create-slot snipe), share heavy (>= 50 SOL bought).
  F4 creator history: the creator's other coins of dev whose create / x2 / x5 / migration lies strictly before the
     entry slot (dev starts 2026-09-11: histories are censored at the window start, a known limit), days since the
     creator's previous create.
  F5 market context: pump-wide curve buy SOL and creates in the UTC hour before the entry's hour (store, all mints).
  purge: block = train / val / test by entry slot (plan section 4); censored_24 / censored_72 when the label window
     crosses the next block boundary or the data end (HOLDOUT_START); the model drops censored_24 rows.

  LAB_STORE=<dev2> python -m wallets.x10.features --labels DIR --money DIR --out DIR
Writes features.parquet (one row per entry: every stage-A column plus the families above) and run-features.json.
"""
import argparse
import glob
import hashlib
import json
import os
import time

from wallets.common import EPOCH_SLOTS, HOLDOUT_START, OUT_ROOT, Clock, connect, git_commit
from wallets.markout import ms_case
from wallets import runners as R

TRAIN_EPOCHS = (1033, 1040)
VAL_EPOCHS = (1041, 1043)
TEST_EPOCHS = (1044, 1046)
BOUNDARIES = (VAL_EPOCHS[0] * EPOCH_SLOTS, TEST_EPOCHS[0] * EPOCH_SLOTS, HOLDOUT_START)
EARLY_BUYERS_USED = 20
BOT_POSITIONS = 50
BOT_HOLD_MS = 30_000
HEAVY_SOL = 50.0


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load(con, labels_dir, money_dir):
    # the 0.5 SOL files only: labels-<lo>.parquet (the 2 SOL run writes labels-<lo>-s2.parquet)
    files = sorted(f for f in glob.glob(f"{labels_dir}/labels-*.parquet") if "-s" not in os.path.basename(f))
    con.execute("CREATE OR REPLACE TEMP TABLE L AS SELECT * FROM read_parquet(?, union_by_name = true)", [files])
    con.execute(f"CREATE OR REPLACE TEMP TABLE MP AS SELECT mp.*, mi.migrate_slot FROM read_parquet('{labels_dir}/mint_peaks-*.parquet', union_by_name = true) mp LEFT JOIN mints mi USING (mint)")
    con.execute(f"CREATE OR REPLACE TEMP TABLE EB AS SELECT * FROM read_parquet('{labels_dir}/early_buyers-*.parquet', union_by_name = true)")
    wd = sorted(glob.glob(f"{money_dir}/wallet-day-*.parquet"))
    # a VIEW, not a table: 42 M wallet-days would fill the spill as a temp table (duckdb-temp-table-footprint lesson); the join
    # below probes the parquet scan with the entries' buyers as the build side
    files_sql = "[" + ", ".join("'" + f.replace("'", "''") + "'" for f in wd) + "]"   # a view cannot take prepared parameters
    con.execute(f"""CREATE OR REPLACE TEMP VIEW WD AS
        SELECT wallet, day::DATE AS day, n_opened >= {BOT_POSITIONS} AND median_hold_slots * ms_per_slot <= {BOT_HOLD_MS} AS bot, n_snipe >= 1 AS sniper,
               buy_sol >= {HEAVY_SOL} AS heavy
        FROM read_parquet({files_sql}, union_by_name = true)""")
    R.progress(f"stage B inputs: entries {_n(con, 'L')} ({len(files)} windows), mint_peaks {_n(con, 'MP')}, early_buyers {_n(con, 'EB')}, wallet-days {_n(con, 'WD')} ({len(wd)} files)")
    return files, wd


def _n(con, t):
    return con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]


def early_buyer_quality(con):
    """F3: the entry's first early buyers and their known history before the entry slot (ASOF on cumulative counts)."""
    con.execute(f"""CREATE OR REPLACE TEMP TABLE pairs AS
        SELECT key, moment, t, entry_slot, mint, wallet, first_buy_slot
        FROM (SELECT l.key, l.moment, l.t, l.entry_slot, l.mint, e.wallet, e.first_buy_slot,
                     row_number() OVER (PARTITION BY l.key, l.moment, l.t ORDER BY e.first_buy_slot, e.wallet) AS rn
              FROM L l JOIN EB e ON e.mint = l.mint AND e.first_buy_slot <= l.t)
        WHERE rn <= {EARLY_BUYERS_USED}""")
    # history events per wallet: every early buy (known at first_buy_slot) and every hit (known at max(first_buy, first_x5))
    con.execute("""CREATE OR REPLACE TEMP TABLE ev AS
        SELECT e.wallet, e.mint, e.first_buy_slot AS known_slot, 1 AS early, 0 AS hit FROM EB e
        UNION ALL
        SELECT e.wallet, e.mint, greatest(e.first_buy_slot, m.first_x5_slot), 0, 1 FROM EB e JOIN MP m USING (mint) WHERE m.first_x5_slot IS NOT NULL""")
    con.execute("""CREATE OR REPLACE TEMP TABLE cum AS
        SELECT wallet, known_slot, sum(early) OVER w AS cum_early, sum(hit) OVER w AS cum_hit
        FROM (SELECT wallet, known_slot, sum(early) AS early, sum(hit) AS hit FROM ev GROUP BY 1, 2)
        WINDOW w AS (PARTITION BY wallet ORDER BY known_slot ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)""")
    # the wallet's counts known strictly before the entry slot, minus the entry's own coin
    con.execute("""CREATE OR REPLACE TEMP TABLE pq AS
        SELECT p.key, p.moment, p.t, p.wallet,
               COALESCE(c.cum_early, 0) - 1 AS n_early,
               COALESCE(c.cum_hit, 0) - CASE WHEN m.first_x5_slot IS NOT NULL AND greatest(p.first_buy_slot, m.first_x5_slot) < p.entry_slot THEN 1 ELSE 0 END AS n_hit
        FROM pairs p
        ASOF LEFT JOIN cum c ON c.wallet = p.wallet AND c.known_slot < p.entry_slot
        LEFT JOIN MP m ON m.mint = p.mint""")
    con.execute("""CREATE OR REPLACE TEMP TABLE f3 AS
        SELECT key, moment, t, count(*) AS eb_n, avg((n_hit >= 1)::INT) AS eb_hit_share, avg(n_hit::DOUBLE / nullif(n_early, 0)) AS eb_hit_rate,
               sum(n_hit) AS eb_hits, avg(n_early) AS eb_experience, max(n_hit) AS eb_max_hits
        FROM pq GROUP BY 1, 2, 3""")
    # money-v2 labels of those buyers on the UTC day before the entry day
    con.execute("""CREATE OR REPLACE TEMP TABLE f3m AS
        SELECT p.key, p.moment, p.t, avg((w.wallet IS NOT NULL)::INT) AS eb_active_share, avg(COALESCE(w.bot, false)::INT) AS eb_bot_share,
               avg(COALESCE(w.sniper, false)::INT) AS eb_sniper_share, avg(COALESCE(w.heavy, false)::INT) AS eb_heavy_share
        FROM pairs p JOIN L l USING (key, moment, t) LEFT JOIN WD w ON w.wallet = p.wallet AND w.day = l.day - 1
        GROUP BY 1, 2, 3""")
    R.progress(f"F3: pairs {_n(con, 'pairs')}, events {_n(con, 'ev')}, entries with buyers {_n(con, 'f3')}")


def creator_history(con, clock):
    """F4: the creator's other coins known before the entry slot; days since the previous create."""
    con.execute("""CREATE OR REPLACE TEMP TABLE cev AS
        SELECT creator, mint, create_slot AS known_slot, 1 AS n_coin, 0 AS n_x2, 0 AS n_x5, 0 AS n_mig FROM MP WHERE creator IS NOT NULL
        UNION ALL SELECT creator, mint, first_x2_slot, 0, 1, 0, 0 FROM MP WHERE creator IS NOT NULL AND first_x2_slot IS NOT NULL
        UNION ALL SELECT creator, mint, first_x5_slot, 0, 0, 1, 0 FROM MP WHERE creator IS NOT NULL AND first_x5_slot IS NOT NULL
        UNION ALL SELECT creator, mint, migrate_slot, 0, 0, 0, 1 FROM MP WHERE creator IS NOT NULL AND migrate_slot IS NOT NULL""")
    con.execute("""CREATE OR REPLACE TEMP TABLE ccum AS
        SELECT creator, known_slot, sum(n_coin) OVER w AS c_coins, sum(n_x2) OVER w AS c_x2, sum(n_x5) OVER w AS c_x5, sum(n_mig) OVER w AS c_mig
        FROM (SELECT creator, known_slot, sum(n_coin) AS n_coin, sum(n_x2) AS n_x2, sum(n_x5) AS n_x5, sum(n_mig) AS n_mig FROM cev GROUP BY 1, 2)
        WINDOW w AS (PARTITION BY creator ORDER BY known_slot ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE f4 AS
        SELECT l.key, l.moment, l.t,
               COALESCE(c.c_coins, 0) - 1 AS cr_prior_coins,
               COALESCE(c.c_x2, 0) - CASE WHEN m.first_x2_slot < l.entry_slot THEN 1 ELSE 0 END AS cr_prior_x2,
               COALESCE(c.c_x5, 0) - CASE WHEN m.first_x5_slot < l.entry_slot THEN 1 ELSE 0 END AS cr_prior_x5,
               COALESCE(c.c_mig, 0) - CASE WHEN m.migrate_slot < l.entry_slot THEN 1 ELSE 0 END AS cr_prior_mig,
               (l.create_slot - pv.known_slot) * ({ms_case(clock, 'l.create_slot')}) / 86400000.0 AS cr_days_since_prev,
               (l.create_slot - {TRAIN_EPOCHS[0] * EPOCH_SLOTS}) * ({ms_case(clock, 'l.create_slot')}) / 86400000.0 AS cr_hist_days
        FROM L l LEFT JOIN MP m ON m.mint = l.mint
        ASOF LEFT JOIN ccum c ON c.creator = l.creator AND c.known_slot < l.entry_slot
        ASOF LEFT JOIN (SELECT creator, create_slot AS known_slot FROM MP WHERE creator IS NOT NULL) pv ON pv.creator = l.creator AND pv.known_slot < l.create_slot""")
    con.execute("""CREATE OR REPLACE TEMP TABLE f4r AS
        SELECT *, cr_prior_x2::DOUBLE / nullif(cr_prior_coins, 0) AS cr_x2_rate, cr_prior_x5::DOUBLE / nullif(cr_prior_coins, 0) AS cr_x5_rate FROM f4""")
    R.progress(f"F4: creator events {_n(con, 'cev')}")


def market_context(con):
    """F5: pump-wide curve buy SOL and creates per UTC hour from the store; the hour before the entry's hour."""
    con.execute("""CREATE OR REPLACE TEMP TABLE hrs AS
        WITH cb AS (SELECT (b.block_time // 3600) AS hr, sum(c.q) / 1e9 AS buy_sol, count(*) AS buys
                    FROM curve c JOIN blocks b ON b.slot = c.slot WHERE c.is_buy GROUP BY 1),
             cr AS (SELECT (b.block_time // 3600) AS hr, count(*) AS creates FROM mints m JOIN blocks b ON b.slot = m.create_slot GROUP BY 1)
        SELECT COALESCE(cb.hr, cr.hr) AS hr, COALESCE(buy_sol, 0) AS mkt_buy_sol, COALESCE(buys, 0) AS mkt_buys, COALESCE(creates, 0) AS mkt_creates
        FROM cb FULL OUTER JOIN cr USING (hr)""")
    con.execute("""CREATE OR REPLACE TEMP TABLE f5 AS
        SELECT l.key, l.moment, l.t, h.mkt_buy_sol, h.mkt_buys, h.mkt_creates
        FROM L l JOIN blocks b ON b.slot = l.entry_slot LEFT JOIN hrs h ON h.hr = b.block_time // 3600 - 1""")
    R.progress(f"F5: hours {_n(con, 'hrs')}")


def assemble(con):
    bounds = ", ".join(str(b) for b in BOUNDARIES)
    con.execute(f"""CREATE OR REPLACE TEMP TABLE F AS
        SELECT l.*, f3.* EXCLUDE (key, moment, t), f3m.* EXCLUDE (key, moment, t), f4.* EXCLUDE (key, moment, t), f5.* EXCLUDE (key, moment, t),
               CASE WHEN l.epoch BETWEEN {TRAIN_EPOCHS[0]} AND {TRAIN_EPOCHS[1]} THEN 'train' WHEN l.epoch BETWEEN {VAL_EPOCHS[0]} AND {VAL_EPOCHS[1]} THEN 'val'
                    WHEN l.epoch BETWEEN {TEST_EPOCHS[0]} AND {TEST_EPOCHS[1]} THEN 'test' END AS block,
               nb.next_boundary, l.entry_slot + round(l.h6_slots / 3.0)::BIGINT > nb.next_boundary AS censored_24, l.entry_slot + l.h6_slots > nb.next_boundary AS censored_72
        FROM L l
        LEFT JOIN f3 ON f3.key = l.key AND f3.moment = l.moment AND f3.t = l.t
        LEFT JOIN f3m ON f3m.key = l.key AND f3m.moment = l.moment AND f3m.t = l.t
        LEFT JOIN f4r f4 ON f4.key = l.key AND f4.moment = l.moment AND f4.t = l.t
        LEFT JOIN f5 ON f5.key = l.key AND f5.moment = l.moment AND f5.t = l.t
        JOIN (SELECT l2.key, l2.moment, l2.t, min(b.b) AS next_boundary FROM L l2, (SELECT unnest([{bounds}]) AS b) b WHERE b.b > l2.entry_slot GROUP BY 1, 2, 3) nb
          ON nb.key = l.key AND nb.moment = l.moment AND nb.t = l.t""")
    return _n(con, "F")


def run(labels_dir, money_dir, out_dir, store=None, memory=None, threads=None):
    t0 = time.time()
    con = connect(store or os.environ.get("LAB_STORE", "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb"),
                  memory or os.environ.get("LAB_WALLETS_MEMORY", "1GB"), int(threads or os.environ.get("LAB_WALLETS_THREADS", "2")))
    clock = Clock.from_store(con)
    files, wd = load(con, labels_dir, money_dir)
    early_buyer_quality(con)
    creator_history(con, clock)
    market_context(con)
    n = assemble(con)
    os.makedirs(out_dir, exist_ok=True)
    con.execute(f"COPY F TO '{out_dir}/features.parquet' (FORMAT PARQUET)")
    info = {"entries": n, "windows": len(files), "labels_sha256": {os.path.basename(f): _sha(f) for f in files},
            "mint_peaks_files": len(glob.glob(f"{labels_dir}/mint_peaks-*.parquet")), "wallet_day_files": len(wd),
            "wallet_day_sha256": _sha(f"{money_dir}/SHA256SUMS.wallet-days") if os.path.exists(f"{money_dir}/SHA256SUMS.wallet-days") else None,
            "blocks": {b: c for b, c in con.execute("SELECT block, count(*) FROM F GROUP BY 1").fetchall()},
            "censored_24": {b: c for b, c in con.execute("SELECT block, count(*) FILTER (WHERE censored_24) FROM F GROUP BY 1").fetchall()},
            "censored_72": {b: c for b, c in con.execute("SELECT block, count(*) FILTER (WHERE censored_72) FROM F GROUP BY 1").fetchall()},
            "boundaries": BOUNDARIES, "early_buyers_used": EARLY_BUYERS_USED, "bot_rule": [BOT_POSITIONS, BOT_HOLD_MS], "heavy_sol": HEAVY_SOL,
            "commit": git_commit(), "seconds": round(time.time() - t0, 1)}
    with open(f"{out_dir}/run-features.json", "w") as f:
        json.dump(info, f, indent=1, default=str)
    R.progress(f"done: {n} rows in {info['seconds']} s -> {out_dir}/features.parquet")
    return info


def main(argv=None):
    ap = argparse.ArgumentParser(description="x10-jager stage B: cross-coin families F3 / F4 / F5 and the purge flags over every window")
    ap.add_argument("--labels", required=True)
    ap.add_argument("--money", default=os.path.join(OUT_ROOT, "money-v2-94f0fee"))
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    info = run(a.labels, a.money, a.out)
    print(json.dumps({k: v for k, v in info.items() if k != "labels_sha256"}, default=str))


if __name__ == "__main__":
    main()
