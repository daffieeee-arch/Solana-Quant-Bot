"""C3 / E54 dev reference for the paid-flow pre-registration (run with PYTHONPATH=lab in a scope; LAB_DATA for the data root): the
F7 pool_reversed rows of NAS run markout-v1-txdelay-v4 with markout.paid_buyers() computed on the dev store (the same SQL and
Clock as a markout run), written as a markout directory for wallets.paidflow_report --d 2. The NAS proof (nas_paidflow_proof.sh)
must give the same arm row from a full markout rerun.

  python -m wallets.explore.paidflow_dev OUT_DIR
"""
import json
import os
import sys
import time

from wallets import markout
from wallets.common import Clock, connect

LAB = os.environ.get("LAB_DATA", "/home/chupa/Solana-project/data-old-faithful-one/lab")
STORE = os.environ.get("LAB_STORE", f"{LAB}/store/dev2.duckdb")
V4 = f"{LAB}/wallets/markout-v1-txdelay-v4"
RUN = "f7rev-txdelay"
out = sys.argv[1]
os.makedirs(out, exist_ok=True)
t0 = time.time()
con = connect(STORE, memory=os.environ.get("LAB_WALLETS_MEMORY", "1500MB"), threads=int(os.environ.get("LAB_WALLETS_THREADS", "2")))  # spill on disk as a markout run
clock = Clock.from_store(con)
con.execute(f"""CREATE TEMP TABLE sig AS SELECT row_number() OVER (ORDER BY mint, t, pool) AS sid, venue, mint, pool, t
                FROM (SELECT DISTINCT venue, mint, pool, t FROM '{V4}/markout-{RUN}.parquet' WHERE family = 'F7' AND segment = 'pool_reversed')""")
n = con.execute("SELECT count(*) FROM sig").fetchone()[0]
per_epoch, default = markout.paid_buyers(con, clock)
print(f"signals {n:,}; window slots per epoch {per_epoch} (default {default}); {time.time() - t0:.0f} s", flush=True)
con.execute(f"""COPY (SELECT v.*, s.paid_buyers_60s, s.paid_max_60s FROM '{V4}/markout-{RUN}.parquet' v JOIN sig s USING (venue, mint, pool, t)
                     WHERE v.family = 'F7' AND v.segment = 'pool_reversed' ORDER BY v.mint, v.t, v.d, v.h_ms)
                TO '{out}/markout-{RUN}.parquet' (FORMAT PARQUET)""")
info = json.load(open(f"{V4}/run-markout-{RUN}.json"))
info |= {"paid_window_ms": markout.PAID_WINDOW_MS, "paid_min_lamports": markout.PAID_MIN_LAMPORTS, "paid_window_slots_per_epoch": {str(k): v for k, v in per_epoch.items()},
         "paid_window_slots_default": default, "note": "F7 pool_reversed rows of markout-v1-txdelay-v4 + paid_buyers_60s / paid_max_60s computed on the dev store (explore/paidflow_dev.py)",
         "commit": markout.git_commit()}
json.dump(info, open(f"{out}/run-markout-{RUN}.json", "w"), indent=1)
print(con.execute(f"""SELECT count(*), count(DISTINCT pool), sum((paid_buyers_60s >= 2)::INT), count(paid_max_60s) FROM '{out}/markout-{RUN}.parquet'
                      WHERE d = 2 AND h = '5m' AND net IS NOT NULL""").fetchone(), f"{time.time() - t0:.0f} s")
