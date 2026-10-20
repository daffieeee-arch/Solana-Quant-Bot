"""M3-11's acceptance (research/m3-11-paper-loop-spec-2026-10-20.md v0.3 §5; lab/live/nas_m3_11.sh): lab-live's paper
loop on the follow source (`lab-live paper --follow`) against paper.engine's own live run (paper/live_reference.py),
from their journals and summaries.

  python lab/live/m3_11_accept.py strict OUT PREFIX SEGMENTS EVENTS [--incomplete] [--seeded]
  python lab/live/m3_11_accept.py arrivals OUT PREFIX SEGMENTS FIXTURE_JSON

OUT/<PREFIX>py is the Python run's journal directory (with summary.json, live_reference's: LiveFeed's stats and seed),
OUT/<PREFIX>rs1 and OUT/<PREFIX>rs2 lab-live's (with summary.json, its own). journal_diff's report goes to
OUT/<PREFIX>diff.

strict (spec §5.2), GREEN needs all of:
- journal_diff scope all of Python against the first lab-live run: 0 unexplained, the same schema, groups and
  cost_scenario;
- signals, orders, fills and exits each above 0 in both journals (an empty input is RED, v0.2 #5);
- `learned` and `usage` equal as sets (learned: kind, key, seq, slot, info; usage: every column), learned above 0
  (v0.2 #3);
- the states (v0.2 #4): `feed` byte-equal; `farmer_seed` and `farmer_seeds` equal on every key but `store`;
  `farm_flag_fixes` equal (absent on both counts as equal). The flag fixes counted are reported; with none, the log says
  the engine side of a fix rests on the golden case alone;
- the two lab-live journals equal on every table, the run's own summary aside (plateau.journal_sha);
- the input hard: SEGMENTS segments read on both sides, EVENTS events in lab-live's summaries (`-`: not fixed);
- (v0.3) lab-live's TokenForget items equal its tokens_dropped and LiveFeed's tokens_dropped; with none, the log says the
  forget rests on the golden case alone; and farmer_flags_fixed equal on both sides;
- with --incomplete (v0.2 #2, the run's first day): rows with farm_complete 0 above 0, and group H's skips "farm history
  incomplete" above 0, on both sides;
- with --seeded (M3-10's seed step): pools seeded above 0, equal on both sides.

arrivals (spec §5.4), on the fixture root of paper.arrivals_fixture (FIXTURE_JSON its fixture.json): strict's rules but
the input's event count and learned above 0, and every signal row's seven live columns (block_time, received_unix_ns,
created_unix_ns, latency_ms, segment_written_unix, segment_lag_ms, d_ms) equal on both sides (journal_diff leaves the
measured ones out); at least one row with all seven set, one at the fixture's no_created slot with a block time and no
created_unix_ns, and one at its no_block_time slot with all seven NULL.
"""

import argparse
import importlib.util
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LAB = os.path.dirname(HERE)
if LAB not in sys.path:
    sys.path.insert(0, LAB)
from paper import journal_diff  # noqa: E402

_spec = importlib.util.spec_from_file_location("plateau", os.path.join(HERE, "plateau.py"))
plateau = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plateau)

LAT = ("block_time", "received_unix_ns", "created_unix_ns", "latency_ms", "segment_written_unix", "segment_lag_ms", "d_ms")
FOUR = ("signals", "orders", "fills", "exits")


def _db(journal):
    return sqlite3.connect(f"file:{os.path.join(journal, 'journal.sqlite')}?mode=ro", uri=True)


def rows(journal, sql):
    db = _db(journal)
    try:
        return db.execute(sql).fetchall()
    finally:
        db.close()


def state(journal, k):
    r = rows(journal, f"SELECT v FROM state WHERE k = '{k}'")
    return r[0][0] if r else None


def summary(path):
    with open(os.path.join(path, "summary.json")) as f:
        return json.load(f)


def _seed(text):
    """A farmer seed state without the store's path (it differs by construction: a store against its export)."""
    if text is None:
        return None
    v = json.loads(text)
    strip = lambda s: {k: x for k, x in s.items() if k != "store"}  # noqa: E731
    return [strip(s) for s in v] if isinstance(v, list) else strip(v)


def strict(out, prefix, segments, events, incomplete=False, seeded=False, need_learned=True):
    """(red, info): the strict rules (module doc) on OUT/<prefix>py, rs1 and rs2."""
    py, rs1, rs2 = (os.path.join(out, prefix + n) for n in ("py", "rs1", "rs2"))
    red, info = [], []
    d = journal_diff.run(py, rs1, os.path.join(out, prefix + "diff"))
    if not d["green"]:
        red.append(f"journal_diff: {d['unexplained']} unexplained, schema {d['schema_diff'] or 'equal'}, state mismatch "
                   f"{d['state_mismatch'] or 'none'}")
    for t in FOUR:
        n = [rows(j, f"SELECT count(*) FROM {t}")[0][0] for j in (py, rs1)]
        info.append(f"{t}: Python {n[0]:,}, lab-live {n[1]:,}")
        if min(n) == 0:
            red.append(f"{t}: Python {n[0]}, lab-live {n[1]}: an empty table proves nothing")
    for t, cols in (("learned", "kind, key, seq, slot, info"), ("usage", "minute, day, stream, bytes, messages, seq, note")):
        a, b = (set(rows(j, f"SELECT {cols} FROM {t}")) for j in (py, rs1))
        info.append(f"{t}: Python {len(a):,} rows, lab-live {len(b):,}, {len(a & b):,} in both")
        if a != b:
            red.append(f"{t}: {len(a - b)} rows only in Python, {len(b - a)} only in lab-live")
        if t == "learned" and need_learned and not a:
            red.append("learned: no row on the Python side: the drain at the dated minutes is not exercised")
    if state(py, "feed") != state(rs1, "feed"):
        red.append(f"state feed: Python {state(py, 'feed')!r}, lab-live {state(rs1, 'feed')!r}")
    for k in ("farmer_seed", "farmer_seeds"):
        a, b = _seed(state(py, k)), _seed(state(rs1, k))
        if a is None or a != b:
            red.append(f"state {k} (store aside): Python {a}, lab-live {b}")
    fa, fb = state(py, "farm_flag_fixes"), state(rs1, "farm_flag_fixes")
    if fa != fb:
        red.append(f"state farm_flag_fixes: Python {fa}, lab-live {fb}")
    fixes = sum(n for n, _ in json.loads(fa or "{}").values())
    info.append(f"flag fixes counted: {fixes} (state farm_flag_fixes {fa})")
    if not fixes:
        info.append("no flag fix on this input: the engine side of a fix rests on the golden case alone")
    sha1, sha2 = plateau.journal_sha(rs1), plateau.journal_sha(rs2)
    if sha1 != sha2:
        red.append(f"the two lab-live journals differ ({sha1[:12]} against {sha2[:12]})")
    sp, s1, s2 = summary(py), summary(rs1), summary(rs2)
    for name, s in (("Python", sp["stats"]["segments"]), ("lab-live 1", s1["follow"]["stats"]["segments"]),
                    ("lab-live 2", s2["follow"]["stats"]["segments"])):
        if s != segments:
            red.append(f"{name}: {s} segments read, not {segments}")
    if events is not None:
        for name, s in (("lab-live 1", s1), ("lab-live 2", s2)):
            if s["events"] != events:
                red.append(f"{name}: {s['events']:,} events, not {events:,}")
    info.append(f"input: {s1['follow']['stats']['segments']:,} segments, {s1['events']:,} events")
    forgets, dropped = s1["follow"]["token_forgets"], s1["follow"]["stats"]["tokens_dropped"]
    if not (forgets == dropped == sp["stats"]["tokens_dropped"]):
        red.append(f"tokens: lab-live forgot {forgets} and dropped {dropped}, LiveFeed dropped {sp['stats']['tokens_dropped']}")
    info.append(f"tokens forgotten: {forgets}")
    if not forgets:
        info.append("no token dropped on this input: the forget rests on the golden case alone")
    if s1["follow"]["stats"]["farmer_flags_fixed"] != sp["stats"]["farmer_flags_fixed"]:
        red.append(f"farmer_flags_fixed: lab-live {s1['follow']['stats']['farmer_flags_fixed']}, LiveFeed {sp['stats']['farmer_flags_fixed']}")
    if incomplete:
        for name, j in (("Python", py), ("lab-live", rs1)):
            zero = rows(j, "SELECT count(*) FROM signals WHERE farm_complete = 0")[0][0]
            skips = rows(j, "SELECT count(*) FROM signals WHERE reason = 'farm history incomplete'")[0][0]
            info.append(f"{name}: {zero:,} rows with farm_complete 0, {skips:,} skipped 'farm history incomplete'")
            if not (zero and skips):
                red.append(f"{name}: farm_complete 0 on {zero} rows, 'farm history incomplete' on {skips}: the first day is not exercised")
    if seeded:
        a, b = sp["stats"]["seeded_pools"], s1["follow"]["stats"]["seeded_pools"]
        info.append(f"pools seeded: LiveFeed {a}, lab-live {b}")
        if not a or a != b:
            red.append(f"seeded_pools: LiveFeed {a}, lab-live {b}: the seed is not exercised or differs")
    return red, info


def arrivals(out, prefix, segments, fixture):
    """(red, info): strict's rules and the seven columns (module doc)."""
    red, info = strict(out, prefix, segments, None, need_learned=False)
    py, rs1 = (os.path.join(out, prefix + n) for n in ("py", "rs1"))
    sql = f"SELECT family, variant, key, t, {', '.join(LAT)} FROM signals"
    a = {r[:4]: r[4:] for r in rows(py, sql)}
    b = {r[:4]: r[4:] for r in rows(rs1, sql)}
    diff = sorted(k for k in a.keys() | b.keys() if a.get(k) != b.get(k))
    if diff:
        red.append(f"the seven live columns differ on {len(diff)} rows, the first {diff[0]}: Python {a.get(diff[0])}, lab-live {b.get(diff[0])}")
    full = [k for k, v in a.items() if all(x is not None for x in v)]
    no_created = [k for k, v in a.items() if k[3] == fixture["no_created"] and v[0] is not None and v[2] is None]
    none = [k for k, v in a.items() if k[3] == fixture["no_block_time"] and all(x is None for x in v)]
    info.append(f"rows: {len(a):,}; all seven set {len(full)}, no created_unix_ns {len(no_created)}, all NULL (no block time) {len(none)}")
    for what, ks in (("all seven set", full), ("no created_unix_ns at the no_created slot", no_created),
                     ("all NULL at the no_block_time slot", none)):
        if not ks:
            red.append(f"no row with {what}: the fixture does not exercise it")
    return red, info


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    s = sub.add_parser("strict")
    s.add_argument("out")
    s.add_argument("prefix")
    s.add_argument("segments", type=int)
    s.add_argument("events")
    s.add_argument("--incomplete", action="store_true")
    s.add_argument("--seeded", action="store_true")
    r = sub.add_parser("arrivals")
    r.add_argument("out")
    r.add_argument("prefix")
    r.add_argument("segments", type=int)
    r.add_argument("fixture")
    a = ap.parse_args(argv)
    name = f"{a.prefix or 'job 91 '}{a.step}".strip()
    if a.step == "strict":
        events = None if a.events == "-" else int(a.events)
        red, info = strict(a.out, a.prefix, a.segments, events, a.incomplete, a.seeded)
    else:
        with open(a.fixture) as f:
            red, info = arrivals(a.out, a.prefix, a.segments, json.load(f))
    for line in info:
        print(f"  {line}")
    for line in red:
        print(f"  RED: {line}")
    print(f"M3-11 {name}: {'GREEN' if not red else 'RED'}")
    return 0 if not red else 1


if __name__ == "__main__":
    sys.exit(main())
