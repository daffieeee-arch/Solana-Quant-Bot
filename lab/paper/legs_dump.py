"""The engine's event stream as lines: the reference for the Rust legs reader (lab/live `lab-live legs`, M1 C1a of
lab/docs/rust-live-route-design-2026-10-16.md; the porting spec is research/m1-c1a-legs-spec-2026-10-19.md, §7 P1),
and with --segments for lab-live's live source (M3-09a of research/m3-design-2026-10-19.md).

  python -m paper.legs_dump --store <store.duckdb> --from SLOT --to SLOT --summary FILE [--out FILE [--out-from SLOT]]
                            [--window N]
  python -m paper.legs_dump --segments <recorder dir> --store <store.duckdb> --from SLOT [--to SLOT] --summary FILE
                            [--snapshot-before SLOT [--h24 N]] [--memory 1GB] [--out FILE [--out-from SLOT]] [--window N]
                            [--max-rss-gb G] [--expect-segments N]

Every Event of ReplayFeed(store, from, to), in its order, as one tab-separated line:
- common: venue key slot tx outer inner block_time tau_u (tau_u = hash(order) % 10_000, the PositionSim's draw);
- curve: variant is_buy t q fee pre(vt vq rt rq) post(vt vq rt rq) fee_bps creator_bps trader budget max_cost
  min_tokens min_out direct;
- pool: kind exact_out base quote_net pre(b e lp protocol creator cashback virt) post(the same) trader limit_quote
  limit_base boost_left direct lp_amount lp_supply farmer.
None is `null`, a bool `true`/`false`, an int decimal. The summary (JSON) has the count and sha256 of all lines, the
count per venue and pool kind, and per slot window [from + k*window, ...) its count and sha256, so a difference can be
found by window; --out writes the lines themselves (small ranges; with --out-from only those of the slots from there
on: a window that differs, in a run from the store's start, whose pool context and farmer history it needs).

--segments: the events of LiveFeed over a recorder's complete segments instead, built as `paper.engine --live` builds
it at a first start (store snapshot, seeded farmer history, follow off). The directory is the recorder's, whose
segments/ LiveFeed reads (lab-live's --segments takes segments/ itself); a segments/ directory, a run that reads no
segment or other than --expect-segments, and a dump without events are refused. --from is the window grid's origin and --to
its end (default none: the last window's end is then nominal, its start + --window); no event is left out. --memory is
LiveFeed's DuckDB limit, so it goes with --segments only (the store form reads at ReplayFeed's own 1 GB). --snapshot-before SLOT takes the snapshot as known before that slot and
the farmer window h24 from the store's clock there, as paper.shadow's `engine --live` step does on segments simulated
inside the store's period (job 91: --snapshot-before 454896000); --h24 sets that window. The event lines are the
store form's, so a window can be compared with it. Every window also has the sha256 of its lines with a pool line's
farmer flag as `-` (M3-09b compares that until M3-10). The summary adds what the engine takes from LiveFeed beside the
events, each as a count and the sha256 of its lines:
- history: the backfill trades of drain_history, drained where the engine drains them (when the next slot's first
  event arrives, and at the end): `at pool slot tx outer inner` and the pool leg's fields, at the slot whose end drains;
- flag_fixes: drain_flag_fixes at the same points: `at pool slot tx outer inner farmer`;
- learned: drain_learned's rows (`kind key seq slot info`) and new_pools, each drain's in sorted order (LiveFeed appends a
  segment's in DuckDB's GROUP BY order, which differs between processes); usage: drain_usage's (as sorted JSON); and
  arrivals: each event's position and (received ns, created ns, segment written) when it has them. The dump drains usage
  and learned at every slot end, the engine at every live minute (engine.py `_live_minute`): the same rows, usage in the
  same order (first in, first out, with no `at`), so only how often they are taken differs;
the clock (M3-09b spec v0.2 §4.1): `per_epoch`, LiveFeed's ms per slot by epoch at the end, and `h24`, its farmer window
as `[seq, value]` each time it changed, taken at the same drain points with the segment read by then; and LiveFeed's
stats and its farmer seed (without the store's path).
"""

import argparse
import glob
import hashlib
import json
import os
import resource

from .feed import ReplayFeed


def render(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _pool_state(p):
    return (p.b, p.e, p.lp_bps, p.protocol_bps, p.creator_bps, p.cashback_bps, p.virt)


def _pool_fields(l):
    return [l.kind, l.exact_out, l.base, l.quote_net, *_pool_state(l.pre), *_pool_state(l.post), l.trader, l.limit_quote,
            l.limit_base, l.boost_left, l.direct, l.lp_amount, l.lp_supply, l.farmer]


def _join(fields):
    return "\t".join(render(v) for v in fields)


def line(ev):
    """One Event as its dump line (the Rust reader writes the same)."""
    l = ev.leg
    if (l.slot, l.tx, l.order) != (ev.slot, ev.tx_index, ev.order):
        raise AssertionError(f"leg and event disagree at {ev.order}")
    out = [ev.venue, ev.key, ev.slot, ev.tx_index, ev.outer_ix, ev.inner_ix, ev.block_time, hash(ev.order) % 10_000]
    if ev.venue == "curve":
        out += [l.variant, l.is_buy, l.t, l.q, l.fee, l.pre.vt, l.pre.vq, l.pre.rt, l.pre.rq, l.post.vt, l.post.vq, l.post.rt,
                l.post.rq, l.post.fee_bps, l.post.creator_bps, l.trader, l.budget, l.max_cost, l.min_tokens, l.min_out, l.direct]
    else:
        out += _pool_fields(l)
    return _join(out)


def no_farmer(ev, s):
    """The event's line with a pool line's farmer flag (its last field) as `-`."""
    return s if ev.venue == "curve" else s[: s.rindex("\t")] + "\t-"


def history_line(at, pool, l):
    """A backfill trade of drain_history, drained at the end of slot `at`."""
    return _join([at, pool, *l.order, *_pool_fields(l)])


class _Digest:
    """A count and the sha256 of lines."""

    def __init__(self):
        self.n, self.sha = 0, hashlib.sha256()

    def add(self, s):
        self.n += 1
        self.sha.update((s + "\n").encode())

    def result(self):
        return {"count": self.n, "sha256": self.sha.hexdigest()}


def capped(events, max_rss_mb, every=50_000):
    """The events, stopping the run (SystemExit) once the process's peak RSS passes max_rss_mb (checked every `every`
    events): a dump beside a long NAS job stays within what was planned for it."""
    for i, ev in enumerate(events):
        if i % every == 0:
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
            if rss > max_rss_mb:
                raise SystemExit(f"legs_dump: peak RSS {rss} MB over the cap of {max_rss_mb} MB after {i:,} events: stopped")
        yield ev


def _summary(events, lo, hi, window, out, out_from, no_farm=False, slot_end=None, arrivals=None):
    """Count and hash the events' lines, in total and per window [lo + k*window, ...) (to hi when given); with
    no_farm the farmer-free digests too, with slot_end a call at the end of each slot (when the next slot's first
    event arrives, and at the end), with arrivals the events' arrival lines."""
    total, nf_total, kinds, windows = hashlib.sha256(), hashlib.sha256(), {}, []
    n = 0
    cur = None  # [a, b, count, sha, sha without the farmer flag]
    slot = None
    f = open(out, "w") if out else None

    def close(w):
        windows.append([w[0], w[1], w[2], w[3].hexdigest()] + ([w[4].hexdigest()] if no_farm else []))

    try:
        for ev in events:
            if slot_end is not None and slot is not None and ev.slot != slot:
                slot_end(slot)
            slot = ev.slot
            s = line(ev)
            a = lo + (ev.slot - lo) // window * window
            if cur is None or cur[0] != a:
                if cur is not None:
                    close(cur)
                cur = [a, a + window if hi is None else min(a + window, hi), 0, hashlib.sha256(), hashlib.sha256()]
            b = (s + "\n").encode()
            total.update(b)
            cur[2] += 1
            cur[3].update(b)
            if no_farm:
                b = (no_farmer(ev, s) + "\n").encode()
                nf_total.update(b)
                cur[4].update(b)
            if arrivals is not None and ev.arrival is not None:
                arrivals.add(_join([*ev.order, *ev.arrival]))
            n += 1
            k = "curve" if ev.venue == "curve" else f"pool/{ev.leg.kind}"
            kinds[k] = kinds.get(k, 0) + 1
            if f and ev.slot >= out_from:
                f.write(s + "\n")
        if slot_end is not None:
            slot_end(slot)
    finally:
        if f:
            f.close()
    if cur is not None:
        close(cur)
    res = {"from": lo, "to": hi, "window": window, "events": n, "sha256": total.hexdigest()}
    if no_farm:
        res["sha256_no_farmer"] = nf_total.hexdigest()
    return dict(res, kinds=dict(sorted(kinds.items())), windows=windows)


def dump(store, lo, hi, window=10_000, out=None, out_from=0, max_rss_mb=None):
    """The summary of ReplayFeed's events over [lo, hi); with `out`, the lines of the slots from `out_from` on too."""
    feed = ReplayFeed(store, lo, hi)
    try:
        events = feed.events() if max_rss_mb is None else capped(feed.events(), max_rss_mb)
        return _summary(events, lo, hi, window, out, out_from)
    finally:
        feed.con.close()


def store_h24(store, slot):
    """paper.shadow's farmer window on segments of the store's period: a day in slots at the store clock's ms per slot
    at `slot`."""
    from backtest import streams
    from backtest.store import DAY_MS
    from backtest.strategies import Clock

    con = streams.open_store(store, threads=1, memory="256MB")
    try:
        return round(DAY_MS / Clock.from_store(con).ms_per_slot(slot))
    finally:
        con.close()


SIDE = ("history", "flag_fixes", "learned", "usage", "new_pools", "arrivals")


def check_root(root):
    """LiveFeed reads the complete segments under <root>/segments (paper/live.py segment_dirs): `root` is the recorder's
    directory, not its segments/ (which lab-live's --segments takes). Refuses a directory named segments, one holding
    segment directories itself, and a root without segments/ (job 99zzo read 0 segments from rec/segments, and its two
    empty summaries were byte-equal)."""
    if os.path.basename(os.path.normpath(root)) == "segments" or glob.glob(os.path.join(root, "*", "_segment.json")):
        raise SystemExit(f"legs_dump: {root} holds the segments themselves; give the recorder's directory, its parent "
                         f"{os.path.dirname(os.path.abspath(root))}: LiveFeed reads <root>/segments")
    if not os.path.isdir(os.path.join(root, "segments")):
        raise SystemExit(f"legs_dump: no segments/ under {root}: refused")


def dump_segments(root, store, lo, hi=None, window=10_000, out=None, out_from=0, snapshot_before=None, h24=None,
                  memory="1GB", max_rss_mb=None, expect_segments=None):
    """The summary of LiveFeed's events over the recorder's complete segments under `root` (the module doc's
    --segments), with what the engine takes from it beside them. Refused (SystemExit): a root LiveFeed reads nothing
    from, no complete segment read, or another number of segments than `expect_segments`."""
    from .live import LiveFeed

    check_root(root)
    if snapshot_before is not None and h24 is None:
        h24 = store_h24(store, snapshot_before)
    feed = LiveFeed(root, store=store, follow=False, snapshot_before=snapshot_before, h24=h24, memory=memory,
                    seed_farmers=True)
    side = {k: _Digest() for k in SIDE}
    pools_seen = [0]  # new_pools taken so far
    clock_h24 = []  # [seq, h24] at each drain point where it changed

    def drain(at):
        for pool, legs in feed.drain_history():
            for l in legs:
                side["history"].add(history_line(at, pool, l))
        for pool, order, farmer in feed.drain_flag_fixes():
            side["flag_fixes"].add(_join([at, pool, *order, farmer]))
        # Learned rows and new pools in sorted order per drain: LiveFeed appends a segment's in the order DuckDB's
        # GROUP BY returns them (live.py _new_tokens_and_pools, no ORDER BY; threads 2, preserve_insertion_order off),
        # which differs between processes (measured 2026-10-20 on a job-91 segment: 49/11 of 60 runs).
        for line in sorted(_join(row) for row in feed.drain_learned()):
            side["learned"].add(line)
        for pool in sorted(feed.new_pools[pools_seen[0]:]):
            side["new_pools"].add(pool)
        pools_seen[0] = len(feed.new_pools)
        for u in feed.drain_usage():
            side["usage"].add(json.dumps(u, sort_keys=True))
        h24_now = feed._h24()
        if not clock_h24 or clock_h24[-1][1] != h24_now:
            clock_h24.append([feed.seq, h24_now])

    try:
        events = feed.events() if max_rss_mb is None else capped(feed.events(), max_rss_mb)
        res = _summary(events, lo, hi, window, out, out_from, no_farm=True, slot_end=drain, arrivals=side["arrivals"])
        seed = {k: v for k, v in (feed.farm_seed or {}).items() if k != "store"} or None
        windows = res.pop("windows")
        res.update({k: side[k].result() for k in SIDE})
        clock = {"per_epoch": {str(e): ms for e, ms in sorted(feed.clock.per_epoch.items())}, "h24": clock_h24}
        res.update({"stats": dict(sorted(feed.stats.items())), "farm_seed": seed, "snapshot_before": snapshot_before,
                    "h24": h24, "clock": clock, "windows": windows})
    finally:
        feed.close()
    read = res["stats"].get("segments", 0)
    if not read:
        raise SystemExit(f"legs_dump: LiveFeed read no complete segment under {root}/segments: refused")
    if expect_segments is not None and read != expect_segments:
        raise SystemExit(f"legs_dump: LiveFeed read {read} complete segments, not the {expect_segments} expected: refused")
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--store", required=True)
    ap.add_argument("--segments", help="a recorder's directory: LiveFeed's events over its complete segments")
    ap.add_argument("--from", dest="lo", type=int, required=True)
    ap.add_argument("--to", dest="hi", type=int)
    ap.add_argument("--summary", required=True)
    ap.add_argument("--out")
    ap.add_argument("--out-from", dest="out_from", type=int, default=0)
    ap.add_argument("--window", type=int, default=10_000)
    ap.add_argument("--snapshot-before", dest="snapshot_before", type=int)
    ap.add_argument("--h24", type=int)
    ap.add_argument("--memory", help="--segments: LiveFeed's DuckDB memory limit (default 1GB)")
    ap.add_argument("--max-rss-gb", dest="max_rss_gb", type=float, help="stop the run when its peak RSS passes this")
    ap.add_argument("--expect-segments", dest="expect_segments", type=int,
                    help="--segments: refuse another number of complete segments read")
    a = ap.parse_args(argv)
    cap = None if a.max_rss_gb is None else int(a.max_rss_gb * 1024)
    if a.segments:
        res = dump_segments(a.segments, a.store, a.lo, a.hi, a.window, a.out, a.out_from, a.snapshot_before, a.h24,
                            a.memory or "1GB", cap, a.expect_segments)
    else:
        if a.hi is None:
            ap.error("--to is needed without --segments")
        if a.snapshot_before is not None or a.h24 is not None or a.expect_segments is not None or a.memory is not None:
            ap.error("--snapshot-before, --h24, --expect-segments and --memory go with --segments")
        res = dump(a.store, a.lo, a.hi, a.window, a.out, a.out_from, cap)
    if not res["events"]:
        raise SystemExit("legs_dump: no event: an empty dump is no result: refused")
    with open(a.summary, "w") as f:
        json.dump(res, f, indent=1)
    # The process's peak RSS beside the summary, not in it (two runs' summaries stay byte-equal).
    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
    print(json.dumps({"rss_max_mb": rss_mb} | {k: v for k, v in res.items() if k not in ("windows", "stats")}))


if __name__ == "__main__":
    main()
