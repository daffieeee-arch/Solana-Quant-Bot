"""Shared helpers for the wallet research (roadmap #167): store access, clock, hold-out guard.

Read-only on the store; never a slot at or after the hold-out start."""

import json
import os
import subprocess

import duckdb

STORE = os.environ.get("LAB_STORE", "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb")
OUT_ROOT = os.environ.get("LAB_WALLETS_OUT", "/home/chupa/Solana-project/data-old-faithful-one/lab/wallets")
HOLDOUT_START = 452_304_000  # week2-defaults.yaml windows.holdout.start_slot
EPOCH_SLOTS = 432_000
REF_MS = 267.3
SOL = 1_000_000_000


def guard(hi):
    """Development only: every slot range ends before the hold-out."""
    if hi > HOLDOUT_START:
        raise ValueError(f"slot {hi} reaches into the hold-out (starts at {HOLDOUT_START})")


def connect(store=STORE, memory="1GB", threads=2):
    """Read-only store connection with bounded memory; spill goes to disk (never /dev/shm = RAM), at most 1 GB."""
    con = duckdb.connect(store, read_only=True)
    # Spill: LAB_SPILL_DIR (a base directory on NVMe; the NAS sets /lab/tmp/<run>) with LAB_SPILL_MAX.
    # Default: the lab tmp directory next to the store. DuckDB removes its own temp files; a crashed run leaves them
    # on disk, not in RAM (2026-10-12, after 2.3 GB of stale spill was found in /dev/shm).
    spill = os.path.join(os.environ.get("LAB_SPILL_DIR", os.path.join(os.path.dirname(os.path.dirname(STORE)), "tmp")),
                         f"lab-wallets-{os.getpid()}")
    con.execute(f"SET threads TO {threads}; SET memory_limit = '{memory}'; SET temp_directory = '{spill}'; "
                f"SET max_temp_directory_size = '{os.environ.get('LAB_SPILL_MAX', '1GB')}'; "
                "SET preserve_insertion_order = false; SET enable_progress_bar = false")
    return con


class Clock:
    """Milliseconds to slots per epoch, from the store's measured ms per slot (addendum §2)."""

    def __init__(self, ms_per_slot=None):
        self.per_epoch = {int(k): float(v) for k, v in (ms_per_slot or {}).items()}

    @classmethod
    def from_store(cls, con):
        return cls(json.loads(con.execute("SELECT json FROM meta").fetchone()[0]).get("ms_per_slot"))

    def ms_per_slot(self, slot):
        return self.per_epoch.get(slot // EPOCH_SLOTS, REF_MS)

    def slots(self, duration_ms, at_slot):
        return max(1, round(duration_ms / self.ms_per_slot(at_slot)))


def git_commit():
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                          cwd=os.path.dirname(os.path.abspath(__file__))).stdout.strip()
