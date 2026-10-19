"""The Rust paper engine (lab-live paper: lab/live/src/paper.rs on lab/core's PaperEngine; M1 C2 of the Rust live route)
against the paper engine's own journal on the same replay with risk inert (research/m1-c2-positionsim-spec-2026-10-19.md
§1.5, §6.4):

- The store and range: LAB_PAPER_STORE (default dev2) and LAB_PAPER_FROM / LAB_PAPER_TO; the default is R1, dev2's arm
  range 450128159-450380000, and for another store its whole period (R2: fwd1053). Both sides take the store's
  meta_bounds as their dev bounds.
- The snapshot: paper.snapshot_export with this replay's farmer history and curve holdings, without the seed day.
- The Python reference: paper.engine main with the C1b arguments (tp4_T600 as the literal --variants 0.04:600, d_ms 600,
  --ignore-risk, the groups file paper/h-arms-2026-10-16.yaml, --no-backup), in its own process. Its states 'groups' and
  'cost_scenario' (committed before its run) become lab-live's --groups and --cost-scenario; lab-live runs meanwhile.
- A1: paper.journal_diff, default scope, is green: 0 unexplained, the same schema, the same groups and costs.
- A2: the reference is what C2 assumes: venue_math 'python', every skipped signal 'busy' or a farm gate, no order
  reason 'at entry: ...', every order 500,000,000.
- A3: state 'feed' is the same text on both sides, and 'groups' and 'cost_scenario' are present on both.
- A4: rows in signals, orders, fills and exits on both sides; the positions table empty on both.
- A5 (`structure`): entered signals ⊆ orders ⊆ signals; a filled order has a buy and a sell fill and an exit; a failed
  one an exit and no fill; a skipped one neither.
- The reference has no curve entry at or after `to` (its seed and fill would read store legs past the replay): red with
  the count otherwise (coordinator, 2026-10-19).
- The seed proof (LAB_PAPER_SEED_PROOF=1, R1): every curve position's holdings at its fill, as lab-live's --trace hashes
  them, equal `Holdings.seed(feed.curve_history(mint, S)[:i])` with backtest.replay's own functions.
- The measure (A7): one line `lab-live paper [LO, HI) on <store>.duckdb: GREEN, ...` to LAB_LIVE_MEASURE_LOG. On red
  the per-chain report goes there first (`chains`): every difference of the five tables grouped by (family, variant,
  key), the earliest t of a chain its root, the rest its consequences.

Needs the built binary: LAB_LIVE_BIN=<path to lab-live> (lab/live/nas_m1c2.sh builds it); those tests skip without it, and
fail instead with LAB_LEGS_REQUIRE=1. A NAS job, never on the VPS. The report's, the reference checks' and the structure
rules' own tests run anywhere.
"""

import hashlib
import json
import os
import sqlite3
import time
from collections import Counter, defaultdict

import duckdb
import pytest

from backtest import data
from backtest.replay import Holdings, _insertion_index
from paper import journal_diff, snapshot_export
from paper.feed import ReplayFeed
from paper.journal import Journal
from paper.tests.test_lab_live_signals import ENGINE_ARGS, _start, _state, _wait, _wait_groups

LAB = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BIN = os.environ.get("LAB_LIVE_BIN", "")
STORE = os.environ.get("LAB_PAPER_STORE", "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb")
R1 = (450_128_159, 450_380_000)
SIZE = 500_000_000
FIVE = ("signals", "orders", "fills", "exits", "positions")
SHOWN = 20

REQUIRE = os.environ.get("LAB_LEGS_REQUIRE") == "1"
needs_bin = pytest.mark.skipif(not (BIN and os.access(BIN, os.X_OK)) and not REQUIRE,
                               reason="set LAB_LIVE_BIN to the built lab-live (NAS)")


def _measure(line):
    if os.environ.get("LAB_LIVE_MEASURE_LOG"):
        with open(os.environ["LAB_LIVE_MEASURE_LOG"], "a") as f:
            f.write(line + "\n")


def _db(journal):
    return sqlite3.connect(f"file:{os.path.join(journal, 'journal.sqlite')}?mode=ro", uri=True)


def _all(journal, sql, params=()):
    db = _db(journal)
    try:
        return db.execute(sql, params).fetchall()
    finally:
        db.close()


# ---------------------------------------------------------------------------------------------- the checks

def reference_checks(journal):
    """A2: what is wrong with the reference for C2 (empty: nothing)."""
    bad = []
    if _state(journal, "venue_math") != "python":
        bad.append(f"venue_math {_state(journal, 'venue_math')!r}, not 'python'")
    allowed = ("busy", *journal_diff.FARM_GATES)
    for reason, n in _all(journal, "SELECT reason, count(*) FROM signals WHERE status = 'skipped' GROUP BY reason"):
        if reason not in allowed:
            bad.append(f"{n} signals skipped for {reason!r}")
    n = _all(journal, "SELECT count(*) FROM orders WHERE reason LIKE 'at entry:%'")[0][0]
    if n:
        bad.append(f"{n} orders skipped at entry by risk")
    for size, n in _all(journal, "SELECT size, count(*) FROM orders WHERE size != ? GROUP BY size", [SIZE]):
        bad.append(f"{n} orders of size {size}")
    return bad


def structure(journal):
    """A5: the rules every journal of this engine keeps (empty: kept)."""
    bad = []
    sig = {tuple(r[:4]): r[4] for r in _all(journal, "SELECT family, variant, key, t, status FROM signals")}
    orders = {tuple(r[:4]): r[4] for r in _all(journal, "SELECT family, variant, key, t, status FROM orders")}
    fills = Counter((tuple(r[:4]), r[4]) for r in _all(journal, "SELECT family, variant, key, t, side FROM fills"))
    exits = {tuple(r) for r in _all(journal, "SELECT family, variant, key, t FROM exits")}
    for k, s in sig.items():
        if s == "entered" and k not in orders:
            bad.append(("entered without an order", k))
    for k, s in orders.items():
        if k not in sig:
            bad.append(("an order without a signal", k))
        has = (fills[(k, "buy")] == 1, fills[(k, "sell")] == 1, k in exits)
        want = {"filled": (True, True, True), "entry_failed": (False, False, True), "skipped": (False, False, False)}.get(s)
        if has != want:
            bad.append((f"a {s} order with (buy, sell, exit) {has}", k))
    for k in exits:
        if k not in orders:
            bad.append(("an exit without an order", k))
    return bad


def curve_entries_at_or_after(journal, to):
    """The reference's curve entries at or after the replay's end: their seed and fill would read store legs past it."""
    return _all(journal, """SELECT count(*) FROM orders o JOIN signals s USING (family, variant, key, t)
                           WHERE s.venue = 'curve' AND o.entry_slot >= ?""", [to])[0][0]


def chains(journal_a, journal_b):
    """The per-chain report (coordinator, 2026-10-19): every difference of the five tables (journal_diff's keys and
    compared columns) grouped by chain (family, variant, key). A chain's root is its difference with the earliest t (the
    tables in journal order on a tie); the later ones are its consequences (busy rows, only-A or only-B orders and their
    fills and exits) and still count as unexplained. Returns (summary, report lines)."""
    found = defaultdict(list)
    for ti, table in enumerate(FIVE):
        keys, cols = journal_diff.TABLES[table]
        ra = journal_diff._rows(journal_a, table, keys, cols, None, None)
        rb = journal_diff._rows(journal_b, table, keys, cols, None, None)
        for k in sorted(set(ra) | set(rb)):
            a, b = ra.get(k), rb.get(k)
            if a is None or b is None:
                what = {"only": "A" if b is None else "B"}
            else:
                what = {c: [a[c], b[c]] for c in cols if a[c] != b[c]}
                if not what:
                    continue
            found[k[:3]].append((k[3], ti, table, list(k[4:]), what))
    roots, rows = Counter(), []
    for chain, diffs in found.items():
        diffs.sort(key=lambda d: (d[0], d[1]))
        t, _, table, extra, what = diffs[0]
        roots[(table, "only " + what["only"] if "only" in what else ",".join(sorted(what)))] += 1
        rows.append((t, chain, diffs))
    rows.sort(key=lambda x: (x[0], x[1]))
    summary = {"chains": len(found), "differences": sum(len(d) for d in found.values()),
               "roots": {f"{tb}: {w}": n for (tb, w), n in sorted(roots.items())}}
    lines = [f"per-chain report: {summary['chains']} chains, {summary['differences']} differences; roots by table and column: "
             f"{summary['roots']}"]
    for t, chain, diffs in rows[:SHOWN]:
        _, _, table, extra, what = diffs[0]
        lines.append(f"- {'/'.join(map(str, chain))}: root {table} t {t}{' ' + str(extra) if extra else ''} {what}; "
                     f"{len(diffs) - 1} consequences {[(d[2], d[0]) for d in diffs[1:6]]}{' ...' if len(diffs) > 6 else ''}")
    return summary, lines


def seed_sha(hist):
    """lab-live paper's seed hash (paper.rs `seed_sha`): `<trader> <net>` lines of the non-zero nets, sorted, None as
    'None'."""
    lines = sorted(f"{'None' if t is None else t} {n}\n" for t, n in hist.items() if n != 0)
    return hashlib.sha256("".join(lines).encode()).hexdigest()


def seed_proof(store, lo, hi, trace, journal):
    """Every `seed` line of lab-live's trace against Holdings.seed(curve_history(mint, S)[:i]) at the reference's own
    entry slot and the position's group q: (lines checked, the mismatches, the reference's filled curve orders)."""
    q = {v: g["q"] for g in json.loads(_state(journal, "groups")).values() for v in g["variants"]}
    entry = {tuple(r[:4]): r[4] for r in _all(journal, """SELECT o.family, o.variant, o.key, o.t, o.entry_slot FROM orders o
                                                         JOIN signals s USING (family, variant, key, t)
                                                         WHERE s.venue = 'curve' AND o.status = 'filled'""")}
    feed = ReplayFeed(store, lo, hi)
    try:
        bad, n = [], 0
        with open(trace) as f:
            for line in f:
                if not line.startswith("seed "):
                    continue
                _, family, variant, key, t, sha = line.split()
                k = (family, variant, key, int(t))
                s = entry.get(k)
                if s is None:
                    bad.append((k, "no filled curve order in the reference"))
                    continue
                legs = feed.curve_history(key, s)
                hold = Holdings(exact=True)
                hold.seed(legs[:_insertion_index(legs, s, q[variant])])
                n += 1
                if seed_sha(hold.hist) != sha:
                    bad.append((k, "another seed"))
    finally:
        feed.con.close()
    return n, bad, len(entry)


# ---------------------------------------------------------------------------------------------- the run

@pytest.fixture(scope="module")
def store_meta():
    if not os.path.exists(STORE):
        (pytest.fail if REQUIRE else pytest.skip)(f"no store at {STORE}")
    con = duckdb.connect(STORE, read_only=True)
    try:
        return json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
    finally:
        con.close()


def _range(meta):
    whole = (meta["slot_start"], meta["slot_end_exclusive"])
    lo, hi = R1 if os.path.basename(STORE) == "dev2.duckdb" else whole
    return int(os.environ.get("LAB_PAPER_FROM", lo)), int(os.environ.get("LAB_PAPER_TO", hi))


@pytest.fixture(scope="module")
def export(tmp_path_factory, store_meta):
    """The snapshot with this replay's farmer history and curve holdings (empty from the store's start), no seed day."""
    out = str(tmp_path_factory.mktemp("paper-snapshot"))
    return out, snapshot_export.export(STORE, out, history=_range(store_meta), seed=False)


def _python(lo, hi, journal, log):
    import sys

    return _start([sys.executable, "-m", "paper.engine", "--from", str(lo), "--to", str(hi), *ENGINE_ARGS, "--store", STORE,
                   "--journal", journal], log, cwd=LAB, env={**os.environ, "PYTHONPATH": LAB})


def _rust(snap, lo, hi, groups, scn, journal, summary, trace):
    return _start([BIN, "paper", "--chunks-root", data.root(), "--snapshot", snap, "--from", str(lo), "--to", str(hi), "--groups",
                   groups, "--variants", "0.04:600", "--cost-scenario", scn, "--ignore-risk", "--journal", journal, "--summary",
                   summary, "--trace", trace], summary + ".log")


def _counts(journal, sql):
    return dict(sorted((str(k), n) for k, n in _all(journal, sql)))


@needs_bin
def test_the_rust_paper_journal_is_the_engines(export, store_meta, tmp_path):
    snap, _ = export
    lo, hi = _range(store_meta)
    py_j, rs_j = str(tmp_path / "python"), str(tmp_path / "rust")
    groups, scn, trace = str(tmp_path / "groups.json"), str(tmp_path / "cost_scenario.json"), str(tmp_path / "trace.txt")
    t0 = time.time()
    py = _python(lo, hi, py_j, str(tmp_path / "python.log"))
    try:
        with open(groups, "w") as f:
            f.write(_wait_groups(py, py_j))  # groups and cost_scenario are committed together (engine.py:1490-1498)
        with open(scn, "w") as f:
            f.write(_state(py_j, "cost_scenario"))
        t1 = time.time()
        rc, out, rs_peak = _wait(_rust(snap, lo, hi, groups, scn, rs_j, str(tmp_path / "rust.json"), trace), timeout=7200)
        rs_secs = time.time() - t1
        assert rc == 0, out[-3000:]
        rc, py_out, py_peak = _wait(py)
        py_secs = time.time() - t0
        assert rc == 0, py_out[-3000:]
    finally:
        if py.poll() is None:
            py.kill()
    with open(tmp_path / "rust.json") as f:
        s = json.load(f)
    res = journal_diff.run(py_j, rs_j, str(tmp_path / "diff"))
    problems = []
    if not res["green"]:
        problems.append(f"A1: journal_diff red, {res['unexplained']} unexplained, state mismatch {res['state_mismatch']}, schema "
                        f"{bool(res['schema_diff'])}")
    problems += [f"A2: {p}" for p in reference_checks(py_j)]
    if not (_state(py_j, "feed") == _state(rs_j, "feed") == json.dumps({"kind": "replay", "from": lo, "to": hi})):
        problems.append(f"A3: feed {_state(py_j, 'feed')!r} / {_state(rs_j, 'feed')!r}")
    for key in ("groups", "cost_scenario"):
        if _state(py_j, key) is None or _state(rs_j, key) is None:
            problems.append(f"A3: no state {key} on one side")
    rows = {j: {t: _all(j, f"SELECT count(*) FROM {t}")[0][0] for t in FIVE} for j in (py_j, rs_j)}
    for j, side in ((py_j, "Python"), (rs_j, "Rust")):
        empty = [t for t in FIVE[:4] if not rows[j][t]]
        if empty or rows[j]["positions"]:
            problems.append(f"A4: {side} has no rows in {empty}, {rows[j]['positions']} open positions")
        problems += [f"A5 {side}: {p}" for p in structure(j)[:SHOWN]]
    at_to = curve_entries_at_or_after(py_j, hi)
    if at_to or s["paper"]["curve_entries_at_or_after_to"]:
        problems.append(f"curve entries at or after to {hi}: reference {at_to}, lab-live {s['paper']['curve_entries_at_or_after_to']}")
    proof = ""
    if os.environ.get("LAB_PAPER_SEED_PROOF") == "1":
        n, bad, filled = seed_proof(STORE, lo, hi, trace, py_j)
        proof = f"; seed proof {n - len([b for b in bad if b[1] == 'another seed'])}/{n} equal over {filled} filled curve orders"
        if bad or not n:
            problems.append(f"seed proof: {len(bad)} of {n} lines wrong, the first {bad[:5]}")
    green = not problems
    if not green:
        summary, lines = chains(py_j, rs_j)
        with open(tmp_path / "chains.md", "w") as f:
            f.write("\n".join(lines) + "\n")
        for line in [f"lab-live paper [{lo}, {hi}) on {os.path.basename(STORE)}: RED, the per-chain report:", *lines, *problems]:
            _measure(line)
    tb = res["tables"]
    _measure(f"lab-live paper [{lo}, {hi}) on {os.path.basename(STORE)}: {'GREEN' if green else 'RED'}, {res['unexplained']} unexplained; "
             f"rows Python {rows[py_j]}, Rust {rows[rs_j]} (only Python {sum(t['only_a'] for t in tb.values())}, only Rust "
             f"{sum(t['only_b'] for t in tb.values())}, different {sum(t['different'] for t in tb.values())}); orders Python "
             f"{_counts(py_j, 'SELECT status, count(*) FROM orders GROUP BY 1')}, Rust {_counts(rs_j, 'SELECT status, count(*) FROM orders GROUP BY 1')}; "
             f"exits Python {_counts(py_j, 'SELECT exit_reason, count(*) FROM exits GROUP BY 1')}, Rust "
             f"{_counts(rs_j, 'SELECT exit_reason, count(*) FROM exits GROUP BY 1')}; curve entries {s['paper']['curve_entries']} "
             f"({s['paper']['curve_seeded']} with a seed from before {lo}, {s['paper']['curve_hold_rows']} curve_hold rows; at or after "
             f"to: reference {at_to}){proof}; lab-live {s['events']:,} events, {rs_secs:.0f} s, peak RSS {(rs_peak or 0) / 1e9:.2f} GB; "
             f"paper.engine {py_secs:.0f} s, peak RSS {(py_peak or 0) / 1e9:.2f} GB")
    if os.environ.get("LAB_PAPER_KEEP"):  # nas_m1c2.sh keeps the references (coordinator: D9)
        os.makedirs(os.environ["LAB_PAPER_KEEP"], exist_ok=True)
        for name in ("journal.sqlite",):
            src = os.path.join(py_j, name)
            db = sqlite3.connect(src)
            try:
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                db.close()
            with open(src, "rb") as f, open(os.path.join(os.environ["LAB_PAPER_KEEP"], name), "wb") as g:
                g.write(f.read())
    assert green, "\n".join(problems)


# ---------------------------------------------------------------------------------------------- the checks' own tests

def _journal(path, rows):
    j = Journal(str(path))
    for table, row in rows:
        j.put(table, **row)
    j.state("venue_math", "python")
    j.close()
    return str(path)


def _pos(t, status, exit_slot=None, reason=None):
    """A chain's rows for a signal at t: (signals, orders, fills, exits) as a filled, failed or skipped order makes them."""
    k = dict(family="F7", variant="tp4_T600", key="P", t=t)
    if status == "busy":
        return [("signals", {**k, "venue": "pool", "status": "skipped", "reason": "busy"})]
    rows = [("signals", {**k, "venue": "pool", "status": "entered"}),
            ("orders", {**k, "entry_slot": t + 2, "size": SIZE, "status": status, "reason": reason})]
    if status == "filled":
        rows += [("fills", {**k, "side": "buy", "slot": t + 2, "tokens": 10, "quote": SIZE}),
                 ("fills", {**k, "side": "sell", "slot": exit_slot, "tokens": 10, "quote": SIZE}),
                 ("exits", {**k, "entry_slot": t + 2, "exit_slot": exit_slot, "exit_reason": "time"})]
    elif status == "entry_failed":
        rows.append(("exits", {**k, "entry_slot": t + 2, "exit_reason": reason, "pnl": -505_000}))
    return rows


def test_the_chain_report_finds_the_root_of_a_red_pair(tmp_path):
    """A synthetic red pair (spec §6.4 item 4): one wrong exit_slot, whose busy consequence turns the chain's next signal
    from skipped busy (A) to entered (B), with its order, fills and exit only in B."""
    other = _pos(400, "filled", 600)  # another chain, equal on both sides
    other = [(t, {**r, "key": "Q"}) for t, r in other]
    a = _journal(tmp_path / "a", [*_pos(100, "filled", 300), *_pos(200, "busy"), *other])
    b = _journal(tmp_path / "b", [*_pos(100, "filled", 150), *_pos(200, "filled", 400), *other])
    summary, lines = chains(a, b)
    assert summary == {"chains": 1, "differences": 7, "roots": {"fills: slot": 1}}, summary
    assert "F7/tp4_T600/P: root fills t 100 ['sell'] {'slot': [300, 150]}; 6 consequences" in lines[1], lines
    assert "('exits', 100), ('signals', 200), ('orders', 200), ('fills', 200)" in lines[1], lines
    assert chains(a, a) == ({"chains": 0, "differences": 0, "roots": {}}, ["per-chain report: 0 chains, 0 differences; roots by table "
                                                                           "and column: {}"])


def test_the_reference_checks_and_the_structure_rules(tmp_path):
    good = _journal(tmp_path / "good", [*_pos(100, "filled", 300), *_pos(200, "busy"), *_pos(500, "entry_failed", reason="entry_slippage"),
                                        *_pos(700, "skipped", reason="no_curve_state")])
    assert (reference_checks(good), structure(good)) == ([], [])
    bad = _journal(tmp_path / "bad", [*_pos(100, "filled", 300)[:-1], ("signals", dict(family="F7", variant="tp4_T600", key="R", t=9,
                                                                                         status="skipped", reason="kill switch")),
                                      ("orders", dict(family="F7", variant="tp4_T600", key="S", t=9, size=1, status="skipped",
                                                      reason="at entry: kill switch"))])
    with sqlite3.connect(os.path.join(bad, "journal.sqlite")) as db:
        db.execute("UPDATE state SET v = 'rust' WHERE k = 'venue_math'")
    assert reference_checks(bad) == ["venue_math 'rust', not 'python'", "1 signals skipped for 'kill switch'",
                                     "1 orders skipped at entry by risk", "1 orders of size 1"]
    assert sorted(structure(bad)) == sorted([("a filled order with (buy, sell, exit) (True, True, False)", ("F7", "tp4_T600", "P", 100)),
                                             ("an order without a signal", ("F7", "tp4_T600", "S", 9))])


def test_the_seed_hash_is_lab_lives():
    # paper.rs tests: sha256 of "A 3\nNone 5\n"; the empty seed hashes nothing.
    assert seed_sha({"A": 3, None: 5, "B": 0}) == "28e498e804832e66a8e61f51bef07a5f2429392794fb16539b740d56123494fb"
    assert seed_sha({}) == hashlib.sha256(b"").hexdigest()
