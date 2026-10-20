"""M3-09b's acceptance (research/m3-09b-follow-source-spec-2026-10-20.md v0.2 §4; lab/live/nas_m3_09b.sh): lab-live's
follow source against Python LiveFeed, from the summaries of `lab-live legs --follow` and `paper.legs_dump --segments`.

  python lab/live/m3_09b_accept.py compare OUT SEGMENTS EVENTS
  python lab/live/m3_09b_accept.py restart OUT SEGMENTS EVENTS ROOT AT N
  python lab/live/m3_09b_accept.py seed OUT SEGMENTS -

compare (spec §4.2, the strict tier), OUT/py, OUT/rs1 and OUT/rs2, GREEN needs:
- the input, hard: SEGMENTS segments read and EVENTS events in every summary;
- the two lab-live runs equal, but for their own measurements (`lab_live`);
- against LiveFeed equal: events, kinds, every window's count and sha256 with and without the farmer flag (M3-10),
  the learned, new_pools, usage, arrivals, history (with and without the flag) and flag_fixes digests, the clock, the
  farm seed, snapshot_before and h24, and the stats segments, late_rows, duplicate_rows, backfill_rows,
  backfill_duplicates, tokens_dropped, seeded_pools, farmer_flags_fixed and unknown_pool_rows;
- #430's counters (unknown_pools by its rule, unknown_pool_other_rows, unknown_mint_rows) equal LiveFeed's where it is
  #430's, else 99zzt's on job 91 (3, 2, 12,127).

restart (spec §4.3): the same summaries of the injected restart (paper.restart_root: segments AT .. AT + N - 1 again,
after the last of them) in OUT/restart-py, -rs1, -rs2. GREEN needs compare's rules on it (SEGMENTS is the restart root's,
N more), and against the plain run's LiveFeed (OUT/py), for LiveFeed and lab-live alike:
- equal: events, kinds, every window (count, sha256 with and without the farmer flag), arrivals, history (with and
  without the flag), flag_fixes, new_pools, the clock and the farm seed (the copies' duplicates change no flag);
- the learned rows as many (those after the copies carry a seq N higher, so the digest differs by design);
- the usage rows N segments' more: the copies' own (with their seqs);
- the stats: segments N more, duplicate_rows more (the copies' admitted rows), late_rows, backfill_rows and
  tokens_dropped equal, backfill_duplicates and the unknown counters at least as many (the copies' rows counted again).

seed (M3-10 spec v0.2 §6.2): compare's rules on OUT/seed-py, -rs1, -rs2, a start inside the store (job 91 from a later
segment, its own snapshot with the seed window's trades); EVENTS `-` takes LiveFeed's count (the input's hard count is
SEGMENTS). GREEN needs too: the seed window holds slots and pools were seeded, so the seed was exercised.
"""

import json
import os
import sys

# The stats both sides count alike (spec §4.2's list).
STATS = ("segments", "late_rows", "duplicate_rows", "backfill_rows", "backfill_duplicates", "tokens_dropped", "seeded_pools",
         "farmer_flags_fixed", "unknown_pool_rows")
# #430's counters on job 91 (99zzt, d5750c6), for a LiveFeed from before #430.
REF_430 = {"unknown_pools": 3, "unknown_pool_other_rows": 2, "unknown_mint_rows": 12127}
EQUAL = ("events", "kinds", "learned", "new_pools", "usage", "arrivals", "history", "history_no_farmer", "flag_fixes", "sha256",
         "sha256_no_farmer", "clock", "farm_seed", "snapshot_before", "h24")


def windows(v):
    """Each window's [count, sha256 with the farmer flag, sha256 without it], by its start."""
    return {w[0]: (w[2], w[3], w[4]) for w in v["windows"]}


def short(x):
    return json.dumps(x)[:300]


def strict(py, rs, rs2, segs, events):
    """compare's rules (module doc): (red reasons, information lines)."""
    red, info = [], []
    own = lambda v: {k: x for k, x in v.items() if k != "lab_live"}  # noqa: E731
    if own(rs) != own(rs2):
        red.append("the two lab-live runs differ in " + ", ".join(sorted(k for k in set(rs) | set(rs2) if k != "lab_live" and rs.get(k) != rs2.get(k))))
    for name, v in (("LiveFeed", py), ("lab-live", rs)):
        if v.get("stats", {}).get("segments") != segs:
            red.append(f"{name} read {v.get('stats', {}).get('segments')} segments, not {segs}")
        if v.get("events") != events:
            red.append(f"{name}: {v.get('events')} events, not {events:,}")
    for k in EQUAL:
        if py.get(k) != rs.get(k):
            red.append(f"{k}: LiveFeed {short(py.get(k))}, lab-live {short(rs.get(k))}")
    ps, ls = py["stats"], rs["stats"]
    for k in STATS:
        if ps.get(k) != ls.get(k):
            red.append(f"stats {k}: LiveFeed {ps.get(k)}, lab-live {ls.get(k)}")
    p430 = "unknown_pool_other_rows" in ps
    for k, ref99 in REF_430.items():
        ref, src = (ps[k], "LiveFeed") if p430 else (ref99, "99zzt (#430)")
        if ls.get(k) != ref:
            red.append(f"stats {k}: lab-live {ls.get(k)}, {src} {ref}")
    if "unknown_mints" in ps and ps["unknown_mints"] != ls.get("unknown_mints"):
        red.append(f"stats unknown_mints: LiveFeed {ps['unknown_mints']}, lab-live {ls.get('unknown_mints')}")
    pw, rw = windows(py), windows(rs)
    differ = sorted(a for a in set(pw) | set(rw) if pw.get(a) != rw.get(a))
    if differ:
        red.append(f"{len(differ)} windows differ in their count or sha256 (with or without the farmer flag), the first {differ[:5]}")
    info.append(f"farmer flags: flag_fixes {(rs.get('flag_fixes') or {}).get('count')}, farmer_flags_fixed {ls.get('farmer_flags_fixed')}; "
                f"windows whose sha256 with the flag differs from the one without: {sum(1 for w in rs['windows'] if w[3] != w[4])}")
    info.append("LiveFeed stats: " + json.dumps(ps))
    info.append("lab-live stats: " + json.dumps(ls))
    info.append("clock: " + json.dumps(rs.get("clock")) + "; farm seed: " + json.dumps(rs.get("farm_seed")))
    info.append("lab-live memory at its largest (entries, bytes): " + json.dumps(rs.get("lab_live", {}).get("mem_max")))
    return red, info


def restart(plain, py, rs, n, copied_usage):
    """restart's rules against the plain run (module doc), for LiveFeed and lab-live: (red reasons, information lines)."""
    red, info = [], []
    ps0 = plain["stats"]
    for name, v in (("LiveFeed", py), ("lab-live", rs)):
        for k in ("events", "kinds", "arrivals", "history", "history_no_farmer", "flag_fixes", "new_pools", "clock", "farm_seed", "sha256",
                  "sha256_no_farmer"):
            if v.get(k) != plain.get(k):
                red.append(f"{name} on the restart: {k} {short(v.get(k))}, the plain run {short(plain.get(k))}")
        if windows(v) != windows(plain):
            red.append(f"{name} on the restart: its windows differ from the plain run's")
        if v["learned"]["count"] != plain["learned"]["count"]:
            red.append(f"{name} on the restart: {v['learned']['count']} learned rows, the plain run {plain['learned']['count']}")
        if v["usage"]["count"] != plain["usage"]["count"] + copied_usage:
            red.append(f"{name} on the restart: {v['usage']['count']} usage rows, not the plain run's {plain['usage']['count']} + the copies' {copied_usage}")
        s = v["stats"]
        if s.get("segments") != ps0.get("segments", 0) + n:
            red.append(f"{name} on the restart: {s.get('segments')} segments, not {ps0.get('segments', 0) + n}")
        if not s.get("duplicate_rows", 0) > ps0.get("duplicate_rows", 0):
            red.append(f"{name} on the restart: duplicate_rows {s.get('duplicate_rows')}, not above the plain run's {ps0.get('duplicate_rows')}")
        for k in ("late_rows", "backfill_rows", "tokens_dropped"):
            if s.get(k) != ps0.get(k):
                red.append(f"{name} on the restart: {k} {s.get(k)}, the plain run {ps0.get(k)}")
        for k in ("backfill_duplicates", "unknown_pool_rows", "unknown_pool_other_rows", "unknown_mint_rows"):
            if k in ps0 and s.get(k, 0) < ps0[k]:
                red.append(f"{name} on the restart: {k} {s.get(k)}, below the plain run's {ps0[k]}")
    s, l = py["stats"], rs["stats"]
    info.append(f"the restart: duplicate_rows {l.get('duplicate_rows')} (plain {ps0.get('duplicate_rows')}), backfill_duplicates "
                f"{l.get('backfill_duplicates')} (plain {ps0.get('backfill_duplicates')}), unknown_pool_rows {l.get('unknown_pool_rows')} "
                f"(plain {ps0.get('unknown_pool_rows')}), usage {rs['usage']['count']} = {plain['usage']['count']} + {copied_usage}; "
                f"learned digest {'equal' if rs['learned'] == plain['learned'] else 'differs (seqs after the copies)'}")
    return red, info


def copied_usage(root, at, n):
    """The usage rows of the copies in the restart root (seqs AT + N .. AT + 2N - 1)."""
    total = 0
    for seq in range(at + n, at + 2 * n):
        with open(os.path.join(root, "segments", f"{seq:09d}", "_segment.json")) as f:
            total += len(json.load(f).get("usage") or ())
    return total


def _load(out, name):
    with open(os.path.join(out, name, "summary.json")) as f:
        return json.load(f)


def _secs(out, name):
    p = os.path.join(out, name, "secs")
    return open(p).read().strip() if os.path.exists(p) else "?"


def seeded(py, rs):
    """The seed step's own rule: the window held slots and pools were seeded (on both sides, which strict makes equal)."""
    w, n = rs.get("farm_seed") or {}, rs.get("stats", {}).get("seeded_pools", 0)
    if not w or w.get("hi", 0) <= w.get("lo", 0) or not n:
        return [f"the seed was not exercised: window {json.dumps(w)}, seeded_pools {n}"], []
    return [], [f"the seed: window [{w['lo']}, {w['hi']}), {n} pools seeded"]


def main(argv):
    step, out, segs = argv[0], argv[1], int(argv[2])
    prefixes = {"compare": "", "restart": "restart-", "seed": "seed-"}
    if step not in prefixes:
        raise SystemExit(f"unknown step {step}")
    prefix = prefixes[step]
    py, rs, rs2 = (_load(out, prefix + n) for n in ("py", "rs1", "rs2"))
    events = py["events"] if argv[3] == "-" else int(argv[3])
    red, info = strict(py, rs, rs2, segs, events)
    if step == "restart":
        root, at, n = argv[4], int(argv[5]), int(argv[6])
        r2, i2 = restart(_load(out, "py"), py, rs, n, copied_usage(root, at, n))
        red, info = red + r2, info + i2
    elif step == "seed":
        r2, i2 = seeded(py, rs)
        red, info = red + r2, info + i2
    for line in info:
        print("information: " + line)
    ls = rs["stats"]
    rss = (rs.get("lab_live") or {}).get("peak_rss_bytes") or 0
    measure = (f"{rs['events']:,} events of {ls['segments']} segments; history {ls['backfill_rows']} rows, {ls['backfill_duplicates']} backfill and "
               f"{ls['duplicate_rows']} recorded duplicates, {ls['tokens_dropped']} tokens dropped, {ls.get('seeded_pools')} pools seeded; unknown: "
               f"{ls['unknown_pools']} pools ({ls['unknown_pool_rows']} trade, {ls['unknown_pool_other_rows']} other rows), {ls['unknown_mints']} mints "
               f"({ls['unknown_mint_rows']} rows); LiveFeed {_secs(out, prefix + 'py')} s, lab-live {_secs(out, prefix + 'rs1')} s and "
               f"{_secs(out, prefix + 'rs2')} s, peak RSS {rss / 2**20:.0f} MB")
    what = {"compare": "M3-09b", "restart": "M3-09b restart", "seed": "M3-10 seed"}[step]
    if red:
        print(f"{what}: RED: " + "; ".join(red))
        print("measure: " + measure)
        return 1
    print(f"{what}: GREEN: lab-live's follow source equals LiveFeed with the farmer flag (events, kinds, every window, learned, new_pools, "
          "usage, arrivals, history, flag fixes, clock, farm seed, stats); two lab-live runs equal"
          + ("; on the restart both equal the plain run but for the copies (learned seqs, usage, duplicate and unknown counts)" if step == "restart" else "")
          + ". " + measure)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
