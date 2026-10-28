"""Run one breadth probe on one slot window (one epoch on the NAS, a slice on the VPS):

  LAB_STORE=<dev2> python -m wallets.probes.run --probe e74 --from-slot A --to-slot B --size-sol 0.5 --out DIR

Writes <probe>-<A>.parquet (one row per entry with the exit) and run-<probe>-<A>.json. Dev only (the window must end
before the hold-out), or --forward on a released forward store (epoch 1064 on, common.forward_guard). The report over all windows and sizes: wallets.probes.report.
"""

import argparse
import importlib
import os
import sys

from .. import runners as R
from . import harness as H

PROBES = ("e74", "e71", "e72", "e73")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", required=True, choices=PROBES)
    ap.add_argument("--from-slot", type=int, required=True)
    ap.add_argument("--to-slot", type=int, required=True)
    ap.add_argument("--size-sol", type=float, default=0.5)
    ap.add_argument("--out", required=True)
    ap.add_argument("--store", default=None)
    ap.add_argument("--memory", default=os.environ.get("LAB_WALLETS_MEMORY"))
    ap.add_argument("--threads", type=int, default=int(os.environ.get("LAB_WALLETS_THREADS", "2")))
    ap.add_argument("--cfg", default=R.DEFAULTS)
    ap.add_argument("--forward", action="store_true", help="a forward store from epoch 1064 on (common.forward_guard) instead of the dev-only guard")
    a = ap.parse_args(argv)
    H.FORWARD = a.forward
    os.makedirs(a.out, exist_ok=True)  # the probes write side files (mint_peaks, copies, good-days) before H.write
    con, clock = H.open_store(a.store, a.memory, a.threads)
    cost_tx = R.tx_cost(a.cfg)
    mod = importlib.import_module(f"wallets.probes.{a.probe}")
    path, info = H.with_size(a.size_sol, lambda: mod.run(con, a.from_slot, a.to_slot, clock, a.size_sol, cost_tx, a.out))
    print(f"{a.probe} {a.from_slot}..{a.to_slot} size {a.size_sol}: {info['rows']} entries -> {path}")


if __name__ == "__main__":
    sys.exit(main())
