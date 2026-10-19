"""C4's plateau gate (M1 C4 of the Rust live route, since C4-3; the spec is research/m1-c4-plateau-spec-2026-10-19.md §2, its
sha256 pinned in nas_m1c4.sh): over `lab-live paper --mem-log` (lab/live/src/memlog.rs), is the memory flat?

  python lab/live/plateau.py --p1 p1.jsonl [--p2 p2.jsonl] [--p3 p3.jsonl] [--run run.json] --spec-sha SHA --out plateau.md
  python lab/live/plateau.py --journal-sha DIR [--tables-only]   (the determinism check's sha256 of a journal, for
                                                                  run.json; --tables-only: the A/B across commits)

- p1: the segments (the live form), the gated run. p2: the same again (determinism; its log is reported, not gated).
  p3: the same range from the chunks, the upper bound (reported, not gated: spec E5).
- run.json (nas_m1c4.sh): the facts the logs do not hold: the journals' and wallets' sha256 of P1 and P2, the segments
  read and expected, the incomplete ones skipped, each run's exit code, seconds and events.
- The window is the last 24 h of input by chain time (the lines' minutes, block time), after a warm-up of >= 24 h.
- Gates (all must hold, spec §2):
  RSS: the linear fit's slope over the window <= +100 MB per 24 h; the mean of the window's last 2 h <= the mean of its
  first 2 h + 150 MB; the run's max <= 6.0 GB. Heap: the same two growth tests. Each gated structure fails only when
  both hold: on its entries, slope x 24 h > 10 % of its mean in the window or last 2 h mean > first 2 h mean x 1.10;
  and its bytes_est grows by >= 1 MB in the 24 h (the larger of the bytes' slope x 24 h and last 2 h - first 2 h).
  Determinism: P1's and P2's journal sha256 equal, wallet files equal. The run: exit 0, every segment read or the
  incomplete ones named.
- Spec v0.4 (E6): the structure gate applies to the structures that are not bounded by construction. The bounded ones
  (WINDOWED: lab-core's 30 h unit test, a slot or unit, the positions in flight, the ops queue, WALLET_DEPTH) follow the
  chain's activity, so they are reported only, each with the ratio of its max in the window to its max in the 24 h
  before it (visible if one still climbs); RSS and the heap stay gated and catch a leak in any structure.
- Spec v0.5 (E7): wallet_txs is bounded (WALLET_DEPTH 50 per group); the 1 MB floor (under 1 MB a day is under 30 MB a
  month, negligible against M3's ~8 GB), so a few-KB insert-only map cannot turn the gate RED.
- Spec v0.6 (E8, after the v0.5 run): (b) the floor's bytes growth is the entries' growth x bytes_est per entry at the
  window's last line (stricter; a hash map's capacity steps hid farmer_hist's growth); (a) interned_keys, post-hoc after
  the v0.5 run (linear in new traders, ~0.25 M a day, bounded by restarts, > 100 days of room at 8G), is gated by an
  absolute bound instead: its bytes growth <= 100 MB per 24 h, valid with M3's weekly HWM restart and > 1 GB alarm.
- Reported, not gated: the time per event (replay processing time on the NAS, not live latency: M3's), per hour from
  the minutes' buckets; the events per second; the peak RSS and its minute; P3's curves; C4-5: the replay's hash tables
  (memlog's `tables`: interned_keys, farmer_hist) with their resizes, a resize being a step of the whole table in the
  heap and RSS (both gated as they are), and their tombstones.
- MB and GB are 10^6 and 10^9 bytes.

- C4 option (c), the long replay (research/m1-c4-long-replay-design-2026-10-19.md), report only, nothing gated by it:
  --daily adds a view per 24 h day counted back from the gate's window (day 0), so a step or a slow grower before the
  window is seen: each day's RSS and heap growth, its largest line-to-line step, the residue (heap - the structures),
  the table resizes and the structures that grew. A day after the warm-up whose RSS or heap grows or steps by more than
  150 MB must be explained in the report before M3 builds on it (coordinator, 2026-10-19: not a gate, no silent pass);
  the view attributes it to the structures' bytes that grew that day, "attributed" when they make up at least half of
  it, else "UNATTRIBUTED". A long-run line gives MB/day after the warm-up, the level at 7 days (M3's weekly restart),
  the days to 6.0 and 8 GB and to 1 GB of interned keys. --form chunks (a replay of OF1 chunks, spec E5: the upper
  bound) reports the same without a verdict, and adds one row per chunk read.

Writes plateau.md and prints one measure line `lab-live plateau ... GREEN|RED` (with --form chunks: `lab-live long
... reported`). Exit 0 either way (RED is a result); exit 2 when an input is missing or unreadable.
"""

import bisect

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from collections import defaultdict

MB = 10**6
GB = 10**9
DAY = 1440  # minutes
WINDOW = DAY  # the last 24 h of input
WARMUP = DAY  # at least 24 h before it
EDGE = 120  # the window's first and last 2 h
RSS_SLOPE = 100 * MB  # per 24 h
RSS_EDGE = 150 * MB
RSS_MAX = 6 * GB
STRUCT_SLOPE = 0.10  # x its mean in the window, per 24 h
STRUCT_EDGE = 1.10
STEP_EXPLAIN = 150 * MB  # option (c): a day's RSS or heap growth or step above this after the warm-up must be explained
ATTRIBUTED = 0.5  # the structures' bytes that grew make up at least this share of it
STRUCT_FLOOR = 1 * MB  # spec v0.5 (E7): a gated structure fails only when its bytes also grow by this in 24 h
KEYS = "interned_keys"  # spec v0.6 (E8 a): an absolute bound on its bytes growth instead of the 10 % test
KEYS_BOUND = 100 * MB  # per 24 h
# Bounded by construction: a window (lab-core's the_windowed_structures_stay_bounded_over_thirty_hours), the slot or
# unit they belong to, the positions in flight, the ops queue (drained per event), WALLET_DEPTH (wallet_txs, v0.5).
# They follow the chain's activity.
WINDOWED = frozenset({
    "recent_keys", "recent_legs", "farm_pools", "farm_items", "hour_events", "pool_seen", "pool_last", "slot_legs",
    "slot_written", "legs_buffer", "pending", "pending_keys", "grad_keys", "open_positions", "open_order", "grad_wait", "ops",
    "wallet_txs",
})
# Spec v0.4 (E6): the windowed ones are reported (with their max ratio), not gated.
GATE_WINDOWED = False


# The state rows that differ between two equal runs: the run's summary (its seconds and peak RSS).
RUN_STATE = ("lab_live_paper",)


def journal_sha(directory, tables_only=False):
    """sha256 over every table of <directory>/journal.sqlite, each table's rows sorted, without the run's own summary
    (RUN_STATE): two runs with the same rows give the same sha256 whatever their timing.

    tables_only (C4-5, an A/B across two commits): every table but `state`, i.e. the rows the engines write (signals,
    orders, fills, exits, heartbeat, positions and the rest). `state` holds the run's inputs as given and its summary.
    The groups text names the checkout's risk files, so its path differs per commit, and the summary holds the run's
    seconds, RSS and mem-log path. The inputs' own sha256 are in each job's log (nas_m1c4.sh `prepare`)."""
    db = sqlite3.connect(f"file:{os.path.join(directory, 'journal.sqlite')}?mode=ro", uri=True)
    try:
        h = hashlib.sha256()
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        for t in tables:
            if tables_only and t == "state":
                continue
            rows = db.execute(f'SELECT * FROM "{t}"').fetchall()
            if t == "state":
                rows = [r for r in rows if r[0] not in RUN_STATE]
            h.update(f"{t}\n".encode())
            for r in sorted(rows, key=repr):
                h.update(repr(r).encode() + b"\n")
        return h.hexdigest()
    finally:
        db.close()


def load(path):
    """The minute lines and the end line of a --mem-log."""
    lines, end = [], None
    with open(path) as f:
        for raw in f:
            if not raw.strip():
                continue
            v = json.loads(raw)
            if v.get("end"):
                end = v
            else:
                lines.append(v)
    if not lines:
        raise ValueError(f"{path}: no minute line")
    return lines, end


def slope(xs, ys):
    """Least squares slope of ys over xs (per unit of x); 0 for fewer than two distinct xs."""
    n = len(xs)
    if n < 2:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx


def mean(ys):
    return sum(ys) / len(ys) if ys else 0.0


def bucket_top(lo):
    """The highest value of the bucket with lower bound lo (memlog.rs: one per ns below 64, then 32 per power of two)."""
    if lo < 64:
        return lo
    e = lo.bit_length() - 1
    return lo + (1 << (e - 5)) - 1


def quantiles(buckets, mx):
    """p50, p90, p99, p999 of summed buckets {lower bound: count} as memlog.rs Hist.quantile: the bucket's top, at most
    the max."""
    n = sum(buckets.values())
    out = {"n": n, "max": mx}
    for name, p in (("p50", 0.5), ("p90", 0.9), ("p99", 0.99), ("p999", 0.999)):
        if n == 0:
            out[name] = 0
            continue
        rank = min(max(-(-p * n // 1), 1), n)  # ceil(p * n), clamped to [1, n]
        seen = 0
        for lo in sorted(buckets):
            seen += buckets[lo]
            if seen >= rank:
                out[name] = min(bucket_top(lo), mx)
                break
    return out


def window_of(lines):
    """(first minute, window start, last minute) of the input; the window is [start, last]."""
    ms = [v["minute"] for v in lines]
    first, last = min(ms), max(ms)
    return first, last - WINDOW + 1, last


def growth(lines, start, last, get):
    """slope per 24 h over the window, the window's mean, and the means of its first and last 2 h; None where absent."""
    pts = [(v["minute"], get(v)) for v in lines if start <= v["minute"] <= last and get(v) is not None]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    first2 = [y for x, y in pts if x < start + EDGE]
    last2 = [y for x, y in pts if x > last - EDGE]
    return {"slope_day": slope(xs, ys) * DAY, "mean": mean(ys), "first2": mean(first2), "last2": mean(last2),
            "max": max(ys) if ys else None, "n": len(ys)}


def max_ratio(lines, start, last, get):
    """The max in the window over the max in the 24 h before it (the warm-up's last 24 h); None if that is 0 or empty."""
    win = [get(v) for v in lines if start <= v["minute"] <= last and get(v) is not None]
    warm = [get(v) for v in lines if start - DAY <= v["minute"] < start and get(v) is not None]
    return max(win) / max(warm) if win and warm and max(warm) > 0 else None


def bytes_growth(g, entries_last, bytes_last):
    """Spec v0.6 (E8 b): a structure's bytes growth in the 24 h = its entries' growth (the larger of the slope x 24 h and
    last 2 h - first 2 h) x bytes_est per entry at the window's last line. bytes_est is capacity-based, so the factor
    holds the table's slack (stricter), and a map growing between two capacity doublings is seen."""
    if not entries_last or not bytes_last:
        return 0.0
    return max(g["slope_day"], g["last2"] - g["first2"]) * bytes_last / entries_last


def struct_ok(name, g, grown):
    """Spec §2 (v0.6): interned_keys passes while its bytes grow by <= 100 MB per 24 h (E8 a); any other structure fails
    only when its entries grow (slope x 24 h > 10 % of its mean, or last 2 h > first 2 h x 1.10) and its bytes grow by
    >= 1 MB in the 24 h (E7, E8 b)."""
    if name == KEYS:
        return grown <= KEYS_BOUND
    entries_grow = g["slope_day"] > STRUCT_SLOPE * g["mean"] or g["last2"] > g["first2"] * STRUCT_EDGE
    return not (entries_grow and grown >= STRUCT_FLOOR)


def table_steps(lines, start, last):
    """C4-5, reported: per hash table in the lines' `tables` (memlog.rs `Table`), its buckets at the run's start, at the
    window's start and at the end, every resize (a change of buckets: minute, buckets and bytes before and after, in the
    window or not), and its tombstones (most in the window, at the end) against its full capacity. {} without tables."""
    out = {}
    prev = {}
    for v in lines:
        for name, t in (v.get("tables") or {}).items():
            o = out.setdefault(name, {"buckets_first": t["buckets"], "buckets_window": None, "steps": [], "tombstones_max": 0})
            p = prev.get(name)
            if p is not None and t["buckets"] != p["buckets"]:
                o["steps"].append({"minute": v["minute"], "buckets": (p["buckets"], t["buckets"]), "bytes": (p["bytes"], t["bytes"]),
                                   "len": t["len"], "in_window": start <= v["minute"] <= last})
            if start <= v["minute"] <= last:
                if o["buckets_window"] is None:
                    o["buckets_window"] = t["buckets"]
                o["tombstones_max"] = max(o["tombstones_max"], t["tombstones"])
            o.update(buckets_end=t["buckets"], bytes_end=t["bytes"], len_end=t["len"], tombstones_end=t["tombstones"],
                     full_end=t["buckets"] // 8 * 7 if t["buckets"] >= 8 else max(t["buckets"] - 1, 0))
            prev[name] = t
    return out


def hourly_times(lines):
    """Per hour of chain time: the summed buckets' quantiles and the max."""
    hours = defaultdict(lambda: [defaultdict(int), 0])
    for v in lines:
        h = hours[v["minute"] // 60]
        for lo, c in v["time_ns"]["buckets"]:
            h[0][lo] += c
        h[1] = max(h[1], v["time_ns"]["max"])
    return {hr: quantiles(b, mx) for hr, (b, mx) in sorted(hours.items())}


def evaluate(p1, run=None):
    """The gate over P1's lines (and run.json's facts): every check with its value, limit and verdict."""
    lines, end = p1
    first, start, last = window_of(lines)
    checks = []

    def check(name, value, limit, ok, unit=""):
        checks.append({"check": name, "value": value, "limit": limit, "ok": bool(ok), "unit": unit})

    warm = (start - first) / 60
    check("warm-up before the window (h)", round(warm, 2), ">= 24", start - first >= WARMUP)
    rss = growth(lines, start, last, lambda v: v.get("rss_bytes"))
    heap = growth(lines, start, last, lambda v: v.get("heap_bytes"))
    rss_all = [(v["rss_bytes"], v["minute"]) for v in lines + ([end] if end else []) if v.get("rss_bytes") is not None]
    peak, peak_minute = max(rss_all) if rss_all else (None, None)
    check("RSS slope (MB per 24 h)", round(rss["slope_day"] / MB, 1), "<= +100", rss["slope_day"] <= RSS_SLOPE)
    check("RSS last 2 h - first 2 h (MB)", round((rss["last2"] - rss["first2"]) / MB, 1), "<= +150", rss["last2"] <= rss["first2"] + RSS_EDGE)
    check("RSS max of the run (GB)", None if peak is None else round(peak / GB, 3), "<= 6.0", peak is not None and peak <= RSS_MAX)
    check("heap slope (MB per 24 h)", round(heap["slope_day"] / MB, 1), "<= +100", heap["slope_day"] <= RSS_SLOPE)
    check("heap last 2 h - first 2 h (MB)", round((heap["last2"] - heap["first2"]) / MB, 1), "<= +150", heap["last2"] <= heap["first2"] + RSS_EDGE)
    names = list(lines[-1]["structures"])
    structs = {}
    for name in names:
        get = lambda v, n=name: v["structures"].get(n, {}).get("entries")  # noqa: E731
        g = growth(lines, start, last, get)
        at_end = [v["structures"][name] for v in lines if start <= v["minute"] <= last and name in v["structures"]]
        e_last, b_last = (at_end[-1].get("entries"), at_end[-1].get("bytes_est")) if at_end else (None, None)
        grown = bytes_growth(g, e_last, b_last)
        gated = GATE_WINDOWED or name not in WINDOWED
        g.update(group=lines[-1]["structures"][name].get("group"), windowed=name in WINDOWED, gated=gated, ok=struct_ok(name, g, grown),
                 bytes_last=lines[-1]["structures"][name].get("bytes_est"), bytes_growth=grown,
                 max_ratio=max_ratio(lines, start, last, get))
        structs[name] = g
    failing = sorted(n for n, g in structs.items() if g["gated"] and not g["ok"])
    check("structures gated: failing", failing, "none", not failing)
    if run is not None:
        sha = (run.get("p1_journal_sha"), run.get("p2_journal_sha"))
        check("determinism: P1 and P2 journal sha256", "equal" if sha[0] and sha[0] == sha[1] else f"{sha[0]} vs {sha[1]}", "equal",
              sha[0] is not None and sha[0] == sha[1])
        check("determinism: wallet files", "equal" if run.get("wallets_equal") else "differ", "equal", bool(run.get("wallets_equal")))
        # P1 and P2 are gated; P3 (the chunks, the upper bound) is reported only (spec E5): its exit code and reason are in
        # the report, never in the gate (an OOM of P3 beside the others must not turn a flat P1 RED).
        codes = run.get("exit_codes", {})
        gated = {k: codes.get(k) for k in ("p1", "p2")}
        check("the runs' exit codes (P1, P2)", gated, "both 0", all(c == 0 for c in gated.values()))
        read, expected, skipped = run.get("segments_read"), run.get("segments_expected"), run.get("incomplete_skipped", [])
        check("segments read", f"{read} of {expected} ({len(skipped)} incomplete skipped)", "all, or the incomplete named",
              read is not None and expected is not None and read + len(skipped) == expected)
    else:
        check("run facts (run.json)", None, "given", False)
    ok = all(c["ok"] for c in checks)
    return {"ok": ok, "checks": checks, "first": first, "start": start, "last": last, "rss": rss, "heap": heap, "peak": peak,
            "peak_minute": peak_minute, "structures": structs, "times": end["time_ns"] if end else None, "hours": hourly_times(lines),
            "tables": table_steps(lines, start, last)}


def summary_of(log):
    """A not gated run's curves in brief: span, RSS and heap at its start and end, their max, the structures at its end."""
    lines, end = log
    rss = [v["rss_bytes"] for v in lines if v.get("rss_bytes") is not None]
    heap = [v["heap_bytes"] for v in lines if v.get("heap_bytes") is not None]
    return {"minutes": (lines[0]["minute"], lines[-1]["minute"]), "hours": round((lines[-1]["minute"] - lines[0]["minute"]) / 60, 1),
            "rss": (rss[0], rss[-1], max(rss)) if rss else None, "heap": (heap[0], heap[-1], max(heap)) if heap else None,
            "times": end["time_ns"] if end else None, "structures": (end or lines[-1])["structures"]}


def us(ns):
    return f"{ns / 1000:.2f}"


def report(res, spec_sha, others, run):
    out = [f"# lab-live plateau (M1 C4): {'GREEN' if res['ok'] else 'RED'}", "",
           f"Spec research/m1-c4-plateau-spec-2026-10-19.md sha256 `{spec_sha}`. Input P1 (the segments, the live form; built by "
           "the shadow recorder from OF1, no live recording): minutes "
           f"{res['first']}..{res['last']} ({(res['last'] - res['first']) / 60:.1f} h of chain time); window {res['start']}..{res['last']} "
           f"(the last 24 h); warm-up {(res['start'] - res['first']) / 60:.1f} h.", "",
           "## Gates (spec §2)", "", "| check | value | limit | ok |", "|---|---|---|---|"]
    for c in res["checks"]:
        out.append(f"| {c['check']} | {c['value']} | {c['limit']} | {'yes' if c['ok'] else '**no**'} |")
    out += ["", f"Structures: gated {'all' if GATE_WINDOWED else 'the ones not bounded by construction (spec v0.4-v0.6)'}; entries "
            "over the window; a gated one fails only when its entries grow and its bytes (entries' growth x bytes per entry, v0.6) grow by >= 1 MB in 24 h; interned_keys only when its bytes grow by > 100 MB in 24 h (v0.6, post-hoc after the v0.5 run); `max / "
            "warm-up max` is the window's max over the max of the 24 h before it (reported, for the windowed ones above all); "
            "bytes are bytes_est, capacity x size_of, a lower bound (interned_keys and farmer_hist: their table's allocation, every bucket, "
            "since C4-5).", "",
            "| structure | group | windowed | gated | mean | slope / 24 h | % of mean | first 2 h | last 2 h | max | max / warm-up max | bytes at the end | bytes growth / 24 h | ok |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, g in res["structures"].items():
        pct = f"{100 * g['slope_day'] / g['mean']:.1f}" if g["mean"] else "-"
        out.append(f"| {name} | {g['group']} | {'yes' if g['windowed'] else ''} | {'yes' if g['gated'] else ''} | {g['mean']:,.0f} | "
                   f"{g['slope_day']:,.0f} | {pct} | {g['first2']:,.0f} | {g['last2']:,.0f} | {g['max'] if g['max'] is None else f'{g['max']:,}'} | "
                   f"{'-' if g['max_ratio'] is None else f'{g['max_ratio']:.2f}'} | {g['bytes_last']:,} | {g['bytes_growth'] / MB:,.2f} MB | "
                   f"{'yes' if g['ok'] else ('**no**' if g['gated'] else 'no (reported)')} |")
    pk = res["peak"]
    out += ["", "## Reported, not gated", "",
            f"- Peak RSS {pk / GB:.3f} GB at minute {res['peak_minute']}." if pk is not None else "- Peak RSS: unknown.",
            f"- RSS window mean {res['rss']['mean'] / GB:.3f} GB, heap window mean {res['heap']['mean'] / GB:.3f} GB."]
    t = res["times"]
    if t:
        out.append(f"- Time per event over the run (push + drain, **replay processing time on the NAS, not live latency**; "
                   f"design §16.1's <= 20 µs p99 beside it): n {t['n']:,}, p50 {us(t['p50'])} µs, p90 {us(t['p90'])} µs, "
                   f"p99 {us(t['p99'])} µs, p999 {us(t['p999'])} µs, max {us(t['max'])} µs.")
    if run and run.get("p1_secs") and t:
        out.append(f"- Events per second (P1's wall time {run['p1_secs']:.0f} s): {t['n'] / run['p1_secs']:,.0f}.")
    if res.get("tables"):
        out += ["", "Hash tables (C4-5, reported, not gated): a resize is a step of the whole table in the heap and RSS, which are "
                "gated; tombstones (removed entries' slots) bring the next resize closer, and above half the full capacity hashbrown "
                "doubles instead of rehashing in place.", "",
                "| table | buckets: start / window / end | resizes (minute: buckets, MB, entries) | tombstones: max in window / end | entries / full at the end | MB at the end |",
                "|---|---|---|---|---|---|"]
        for name, t in res["tables"].items():
            steps = "; ".join(f"{s['minute']}{' (window)' if s['in_window'] else ''}: {s['buckets'][0]:,} -> {s['buckets'][1]:,}, "
                              f"{s['bytes'][0] / MB:,.1f} -> {s['bytes'][1] / MB:,.1f}, {s['len']:,}" for s in t["steps"]) or "none"
            win = "-" if t["buckets_window"] is None else f"{t['buckets_window']:,}"
            out.append(f"| {name} | {t['buckets_first']:,} / {win} / {t['buckets_end']:,} | {steps} | {t['tombstones_max']:,} / "
                       f"{t['tombstones_end']:,} | {t['len_end']:,} / {t['full_end']:,} | {t['bytes_end'] / MB:,.1f} |")
    out += ["", "Per hour of chain time (µs):", "", "| hour (unix) | n | p50 | p99 | p999 | max |", "|---|---|---|---|---|---|"]
    for hr, q in res["hours"].items():
        out.append(f"| {hr} | {q['n']:,} | {us(q['p50'])} | {us(q['p99'])} | {us(q['p999'])} | {us(q['max'])} |")
    if run is not None:
        p3 = run.get("exit_codes", {}).get("p3")
        if p3 is not None:
            out += ["", f"P3's exit code (reported, not gated): {p3}" + (f"; {run['p3_note']}" if run.get("p3_note") else "") + "."]
    for label, s in others:
        if "error" in s:
            out += ["", f"## {label} (reported)", "", f"- Its log is not readable: {s['error']}."]
            continue
        out += ["", f"## {label} (reported)", "",
                f"- Minutes {s['minutes'][0]}..{s['minutes'][1]} ({s['hours']} h)."]
        if s["rss"]:
            out.append(f"- RSS start {s['rss'][0] / GB:.3f}, end {s['rss'][1] / GB:.3f}, max {s['rss'][2] / GB:.3f} GB; heap start "
                       f"{s['heap'][0] / GB:.3f}, end {s['heap'][1] / GB:.3f}, max {s['heap'][2] / GB:.3f} GB.")
        if s["times"]:
            q = s["times"]
            out.append(f"- Time per event: p50 {us(q['p50'])} µs, p99 {us(q['p99'])} µs, max {us(q['max'])} µs (n {q['n']:,}).")
        out.append("- Structures at the end: " + ", ".join(f"{n} {v['entries']:,}" for n, v in s["structures"].items()) + ".")
    return "\n".join(out) + "\n"


def measure_line(res):
    c = {x["check"]: x for x in res["checks"]}
    g = lambda k: c[k]["value"] if k in c else None  # noqa: E731
    t = res["times"] or {}
    fail = [x["check"] for x in res["checks"] if not x["ok"]]
    return (f"lab-live plateau minutes [{res['start']}, {res['last']}] (24 h after {(res['start'] - res['first']) / 60:.1f} h warm-up): "
            f"RSS slope {g('RSS slope (MB per 24 h)')} MB/24h, last-first {g('RSS last 2 h - first 2 h (MB)')} MB, max "
            f"{g('RSS max of the run (GB)')} GB; heap slope {g('heap slope (MB per 24 h)')} MB/24h, last-first "
            f"{g('heap last 2 h - first 2 h (MB)')} MB; structures failing {g('structures gated: failing')}; "
            f"p99 per event {us(t.get('p99', 0))} us (replay, not live latency); "
            f"{'GREEN' if res['ok'] else 'RED: ' + '; '.join(fail)}")


def day_bounds(first, last):
    """The 24 h days of a log, counted back from its last minute: day 0 is the gate's window [last - 1439, last], day k
    ends the minute before day k - 1 starts; the earliest is partial when the log does not fill it. Oldest first."""
    out, hi, k = [], last, 0
    while hi >= first:
        lo = hi - DAY + 1
        out.append({"day": k, "lo": max(lo, first), "hi": hi, "partial": lo < first})
        hi, k = lo - 1, k + 1
    return out[::-1]


def residue(v):
    """The heap less the structures' bytes (the part no structure reports: rounding, small buffers, untracked state)."""
    return v["heap_bytes"] - sum(x.get("bytes_est", 0) for x in v["structures"].values())


def largest_step(prev, sl, key):
    """The largest line-to-line increase of `key` over the day's lines (from the line before the day): (bytes, minute)."""
    best, at, before = 0, None, prev
    for v in sl:
        if before is not None and v.get(key) is not None and before.get(key) is not None and v[key] - before[key] > best:
            best, at = v[key] - before[key], v["minute"]
        before = v
    return best, at


def daily(lines, first, last, tables=None):
    """The per-day view (module doc, --daily): one dict per day, oldest first. Each day's lines are a bisect slice, so the
    whole view is O(lines x structures)."""
    minutes = [v["minute"] for v in lines]
    out = []
    for d in day_bounds(first, last):
        i, j = bisect.bisect_left(minutes, d["lo"]), bisect.bisect_right(minutes, d["hi"])
        sl, prev = lines[i:j], lines[i - 1] if i > 0 else None
        if not sl:
            out.append({**d, "lines": 0})
            continue
        rss = growth(sl, d["lo"], d["hi"], lambda v: v.get("rss_bytes"))
        heap = growth(sl, d["lo"], d["hi"], lambda v: v.get("heap_bytes"))
        res = growth(sl, d["lo"], d["hi"], residue)
        steps = {k: largest_step(prev, sl, k) for k in ("rss_bytes", "heap_bytes")}
        base = prev or sl[0]
        grew = {}
        for name, x in sl[-1]["structures"].items():
            b0 = base["structures"].get(name, {}).get("bytes_est", 0)
            if x.get("bytes_est", 0) - b0 >= MB:
                grew[name] = x["bytes_est"] - b0
        resizes = [dict(st, table=name) for name, t in (tables or {}).items() for st in t.get("steps", []) if d["lo"] <= st["minute"] <= d["hi"]]
        size = lambda g: max(g["slope_day"], g["last2"] - g["first2"]) if g["n"] else 0  # noqa: E731
        worst = max(size(rss), size(heap), steps["rss_bytes"][0], steps["heap_bytes"][0])
        after_warmup = d["lo"] >= first + WARMUP
        explain = None
        if after_warmup and worst > STEP_EXPLAIN:
            share = sum(grew.values()) / worst
            explain = {"mb": worst / MB, "share": share, "attributed": share >= ATTRIBUTED,
                       "by": sorted(grew.items(), key=lambda kv: -kv[1])[:4]}
        out.append({**d, "lines": len(sl), "events": sl[-1].get("events", 0) - (base.get("events", 0) if prev else 0),
                    "rss": rss, "heap": heap, "residue": res, "steps": steps, "grew": grew, "resizes": resizes,
                    "after_warmup": after_warmup, "explain": explain})
    return out


def long_run(lines, first, last):
    """MB per day from the 2 h after the warm-up to the last 2 h, for RSS, heap and interned_keys' bytes; the level at 7
    days after the warm-up (M3's weekly restart) and the days to 6.0 and 8 GB (to 1 GB for the keys) at that rate."""
    w = first + WARMUP
    if last - w < 2 * EDGE:
        return None
    keys = lambda v: v["structures"].get(KEYS, {}).get("bytes_est")  # noqa: E731
    out = {"days": (last - w) / DAY}
    for name, get, limits in (("rss", lambda v: v.get("rss_bytes"), (6 * GB, 8 * GB)), ("heap", lambda v: v.get("heap_bytes"), (6 * GB, 8 * GB)),
                              ("interned_keys", keys, (1 * GB,))):
        xa = [get(v) for v in lines if w <= v["minute"] < w + EDGE and get(v) is not None]
        xb = [get(v) for v in lines if v["minute"] > last - EDGE and get(v) is not None]
        if not xa or not xb:  # no lines there (mean() would read 0)
            continue
        a, b = mean(xa), mean(xb)
        rate = (b - a) / out["days"]
        out[name] = {"start": a, "end": b, "per_day": rate, "at_7_days": a + 7 * rate,
                     "days_to": {lim: ((lim - b) / rate if rate > 0 and b < lim else None) for lim in limits}}
    return out


def chunk_view(lines):
    """For a replay of chunks: one row per chunk read (the legs buffer's entries change at each), with its minutes, the
    buffer, the heap's min and max, the heap less the buffer, and the keys and curve sums at its end."""
    rows, cur = [], None
    for v in lines:
        lb = v["structures"].get("legs_buffer", {})
        key = (lb.get("entries"), lb.get("bytes_est"))
        if cur is None or key != cur["key"]:
            cur = {"key": key, "lo": v["minute"], "legs": lb.get("bytes_est", 0), "heap": [], "rest": []}
            rows.append(cur)
        cur["hi"] = v["minute"]
        cur["heap"].append(v["heap_bytes"])
        cur["rest"].append(v["heap_bytes"] - lb.get("bytes_est", 0))
        cur["keys"] = v["structures"].get(KEYS, {}).get("entries")
        cur["curve_sums"] = v["structures"].get("run_curve_sums", {}).get("entries")
    return [{"lo": r["lo"], "hi": r["hi"], "legs": r["legs"], "heap_min": min(r["heap"]), "heap_max": max(r["heap"]),
             "rest_max": max(r["rest"]), "keys": r["keys"], "curve_sums": r["curve_sums"]} for r in rows]


def report_daily(res, lines, chunks=False):
    """The --daily sections (report only)."""
    first, last = res["first"], res["last"]
    days = daily(lines, first, last, res.get("tables"))
    mb = lambda x: f"{x / MB:+,.0f}"  # noqa: E731
    g = lambda gr: "-" if not gr["n"] else f"{mb(gr['slope_day'])} / {mb(gr['last2'] - gr['first2'])}"  # noqa: E731
    out = ["", "## Per day (reported, not gated)", "",
           "Days of 24 h counted back from the gate's window (day 0); slope / last 2 h - first 2 h in MB; a step is the largest "
           "line-to-line increase. A day after the warm-up whose RSS or heap grows or steps by more than "
           f"{STEP_EXPLAIN // MB} MB must be explained here before M3 builds on it (not a gate): it is attributed to the structures "
           f"whose bytes grew that day, 'attributed' when they make up >= {ATTRIBUTED:.0%} of it.", "",
           "| day | minutes | lines | events | RSS slope / last-first | heap slope / last-first | residue slope / last-first | "
           "largest step RSS / heap (minute) | table resizes | structures +MB |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for d in days:
        label = f"{d['day']}{' (window)' if d['day'] == 0 else ''}{' (partial)' if d['partial'] else ''}{'' if d.get('after_warmup', True) else ' (warm-up)'}"
        if not d["lines"]:
            out.append(f"| {label} | {d['lo']}..{d['hi']} | 0 | | | | | | | |")
            continue
        st = d["steps"]
        steps = f"{mb(st['rss_bytes'][0])} / {mb(st['heap_bytes'][0])} ({st['heap_bytes'][1] or st['rss_bytes'][1] or '-'})"
        rz = "; ".join(f"{r['table']} {r['buckets'][0]:,} -> {r['buckets'][1]:,} @{r['minute']}" for r in d["resizes"]) or "-"
        gr = ", ".join(f"{n} {mb(b)}" for n, b in sorted(d["grew"].items(), key=lambda kv: -kv[1])[:5]) or "-"
        out.append(f"| {label} | {d['lo']}..{d['hi']} | {d['lines']:,} | {d['events']:,} | {g(d['rss'])} | {g(d['heap'])} | "
                   f"{g(d['residue'])} | {steps} | {rz} | {gr} |")
    flagged = [d for d in days if d.get("explain")]
    out += ["", f"Days after the warm-up above {STEP_EXPLAIN // MB} MB: " + ("none." if not flagged else "")]
    for d in flagged:
        e = d["explain"]
        by = ", ".join(f"{n} {b / MB:+,.0f} MB" for n, b in e["by"]) or "no structure grew"
        out.append(f"- day {d['day']} ({d['lo']}..{d['hi']}): {e['mb']:,.0f} MB; "
                   f"{'attributed' if e['attributed'] else '**UNATTRIBUTED**'} ({e['share']:.0%}): {by}.")
    lr = long_run(lines, first, last)
    if lr:
        out += ["", f"Long run (reported): from the 2 h after the warm-up to the last 2 h, {lr['days']:.1f} days.", "",
                "| measure | after the warm-up | last 2 h | MB per day | at 7 days | days to the limits |", "|---|---|---|---|---|---|"]
        for name in ("rss", "heap", "interned_keys"):
            x = lr.get(name)
            if not x:
                continue
            to = ", ".join(f"{lim / GB:g} GB: " + ("-" if dd is None else f"{dd:,.1f}") for lim, dd in x["days_to"].items())
            out.append(f"| {name} | {x['start'] / GB:.3f} GB | {x['end'] / GB:.3f} GB | {x['per_day'] / MB:+,.1f} | "
                       f"{x['at_7_days'] / GB:.3f} GB | {to} |")
    if chunks:
        out += ["", "Per chunk read (chunk form): the legs buffer holds one chunk's legs, so the heap moves with it.", "",
                "| minutes | legs buffer MB | heap min / max GB | heap - buffer max GB | interned keys | run_curve_sums |", "|---|---|---|---|---|---|"]
        for r in chunk_view(lines):
            out.append(f"| {r['lo']}..{r['hi']} | {r['legs'] / MB:,.0f} | {r['heap_min'] / GB:.3f} / {r['heap_max'] / GB:.3f} | "
                       f"{r['rest_max'] / GB:.3f} | {r['keys'] or 0:,} | {r['curve_sums'] or 0:,} |")
    return "\n".join(out) + "\n", days


def chunk_form(md):
    """A chunk replay's report: the same tables, its title and the checks marked as information (spec E5)."""
    head, _, rest = md.partition("\n")
    return ("# lab-live long replay, chunk form: reported, not gated (spec E5; the checks below are information)\n" + rest
            .replace("## Gates (spec §2)", "## Checks (spec §2), for information: the chunk form is not gated"))


def reported_line(res, days):
    """The measure line of a chunk replay: the values, no verdict."""
    c = {x["check"]: x for x in res["checks"]}
    g = lambda k: c[k]["value"] if k in c else None  # noqa: E731
    flagged = [d for d in days if d.get("explain")]
    un = [d["day"] for d in flagged if not d["explain"]["attributed"]]
    return (f"lab-live long (chunk form) minutes [{res['first']}, {res['last']}] ({(res['last'] - res['first']) / 60:.1f} h): RSS max "
            f"{g('RSS max of the run (GB)')} GB; last 24 h RSS slope {g('RSS slope (MB per 24 h)')} MB/24h, heap slope "
            f"{g('heap slope (MB per 24 h)')} MB/24h; days above {STEP_EXPLAIN // MB} MB after the warm-up {len(flagged)} "
            f"(unattributed {un}); reported, not gated")


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if argv[:1] == ["--journal-sha"]:
        if len(argv) not in (2, 3) or argv[2:] not in ([], ["--tables-only"]):
            print("usage: plateau.py --journal-sha DIR [--tables-only]", file=sys.stderr)
            return 2
        try:
            print(journal_sha(argv[1], tables_only=argv[2:] == ["--tables-only"]))
        except sqlite3.Error as e:
            print(f"lab-live plateau: {argv[1]}: {e}", file=sys.stderr)
            return 2
        return 0
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--p1", required=True)
    ap.add_argument("--p2")
    ap.add_argument("--p3")
    ap.add_argument("--run")
    ap.add_argument("--spec-sha", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--daily", action="store_true", help="add the per-day view (report only)")
    ap.add_argument("--form", choices=("segments", "chunks"), default="segments",
                    help="chunks: a replay of OF1 chunks, reported without a verdict (spec E5)")
    a = ap.parse_args(argv)
    try:
        p1 = load(a.p1)
        run = json.load(open(a.run)) if a.run else None
    except (OSError, ValueError, KeyError) as e:
        print(f"lab-live plateau: unreadable input: {e}", file=sys.stderr)
        return 2
    # P2 and P3 are reported only: an unreadable log (a run that died early) is a note in the report, not an input error
    others = []
    for label, path in (("P2 (the segments again)", a.p2), ("P3 (the chunks, the upper bound)", a.p3)):
        if not path:
            continue
        try:
            others.append((label, summary_of(load(path))))
        except (OSError, ValueError, KeyError) as e:
            others.append((label, {"error": str(e)}))
    res = evaluate(p1, run)
    md = report(res, a.spec_sha, others, run)
    days = []
    if a.daily or a.form == "chunks":
        extra, days = report_daily(res, p1[0], chunks=a.form == "chunks")
        md += extra
    if a.form == "chunks":
        md = chunk_form(md)
    with open(a.out, "w") as f:
        f.write(md)
    print(measure_line(res) if a.form == "segments" else reported_line(res, days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
