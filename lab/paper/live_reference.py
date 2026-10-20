"""M3-11's Python reference (research/m3-11-paper-loop-spec-2026-10-20.md v0.3 §1): paper.engine's own `main`, live on a
recorder root, with paper.live.LiveFeed wrapped to start as lab-live's follow source starts.

  python -m paper.live_reference --snapshot-before LO [--h24 N] [--summary FILE] -- \
      --live ROOT --catch-up --no-watchlist --no-backup --config CONFIG --groups GROUPS --store STORE --journal DIR

Everything after `--` goes to paper.engine.main unchanged; it must hold --live, --catch-up, --no-watchlist and
--no-backup. Before the call, `paper.live.LiveFeed` (main imports it at call time, engine.py:1452) is replaced by a
subclass that adds `snapshot_before=LO` and, when given, `h24=N`; without --h24 the feed measures h24 from the first
segment's blocks (production's path, spec §5.3). So the reference differs from production's `engine --live` in exactly
these two named points. The class is restored after the call.

--summary writes what the acceptance needs from the feed (paper.engine journals none of it): LiveFeed's stats (its
segments, tokens_dropped, seeded_pools, farmer_flags_fixed, ...), its farm_seed without the store's path, and the two
arguments. The journal is main's own.
"""

import argparse
import json
import sys

from paper import engine
from paper import live

REQUIRED = ("--live", "--catch-up", "--no-watchlist", "--no-backup")


def run(snapshot_before, h24, engine_argv):
    """paper.engine.main(engine_argv) with LiveFeed wrapped (module doc); the feed it built."""
    missing = [x for x in REQUIRED if x not in engine_argv]
    if missing:
        raise SystemExit(f"live_reference: paper.engine's arguments lack {', '.join(missing)}: refused")
    base, feeds = live.LiveFeed, []

    class ReferenceFeed(base):
        def __init__(self, *args, **kw):
            kw["snapshot_before"] = snapshot_before
            if h24 is not None:
                kw["h24"] = h24
            super().__init__(*args, **kw)
            feeds.append(self)

    live.LiveFeed = ReferenceFeed
    try:
        engine.main(list(engine_argv))
    finally:
        live.LiveFeed = base
    if len(feeds) != 1:
        raise SystemExit(f"live_reference: paper.engine built {len(feeds)} live feeds, not 1")
    return feeds[0]


def summary(feed, snapshot_before, h24):
    seed = feed.farm_seed and {k: v for k, v in feed.farm_seed.items() if k != "store"}
    return {"snapshot_before": snapshot_before, "h24": h24, "stats": dict(feed.stats), "farm_seed": seed}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" not in argv:
        raise SystemExit("live_reference: give paper.engine's arguments after --")
    i = argv.index("--")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--snapshot-before", type=int, required=True, help="the first live slot: the snapshot as known before it")
    ap.add_argument("--h24", type=int, help="the farmer window in slots (default: LiveFeed measures it)")
    ap.add_argument("--summary", help="write LiveFeed's stats and farm_seed here (JSON)")
    a = ap.parse_args(argv[:i])
    if a.h24 is not None and a.h24 <= 0:
        ap.error("--h24 must be above 0")
    feed = run(a.snapshot_before, a.h24, argv[i + 1:])
    if a.summary:
        with open(a.summary, "w") as f:
            json.dump(summary(feed, a.snapshot_before, a.h24), f, indent=1, sort_keys=True)


if __name__ == "__main__":
    main()
