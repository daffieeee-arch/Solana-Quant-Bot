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
- No curve order with entry_slot >= `to` (its seed and fill would read store legs past the replay), counted in both
  journals by the same query and in lab-live's summary: red with the counts otherwise (coordinator, 2026-10-19).
- The seed proof (LAB_PAPER_SEED_PROOF=1, R1): every curve position's holdings at its fill, as lab-live's --trace hashes
  them, equal `Holdings.seed(feed.curve_history(mint, S)[:i])` with backtest.replay's own functions.
- The per-chain report (`chains`), on every run: every difference of the five tables grouped by (family, variant, key),
  the earliest t of a chain its root, the rest its consequences. Its count is in the measure line; on red its lines go
  to LAB_LIVE_MEASURE_LOG first.
- The measure (A7): one line `lab-live paper [LO, HI) on <store>.duckdb: GREEN, ...` to LAB_LIVE_MEASURE_LOG.
- LAB_PAPER_KEEP=DIR (nas_m1c2.sh, coordinator): both journals stay, green or red, as DIR/python/journal.sqlite and
  DIR/rust/journal.sqlite (each one whole file, by SQLite's backup), with lab-live's summary and trace, the groups and
  cost scenario it ran with, and chains.md. `python -m paper.journal_diff DIR/python DIR/rust OUT` compares them again.

- LAB_PAPER_MODE (M1 C3-4, research/m1-c3-risk-spec-2026-10-19.md §1): 'inert' (the default: C2's runs above), 'config'
  (RC1/RC2: the paper start's own files, `--config paper/k3-paper-2026-10-12.yaml --groups paper/h-arms-2026-10-16.yaml`,
  risk on; their sha256 must be c1e36a0's, D5) or 'risk' (RC3: C2's arguments without --ignore-risk, so paper/risk.yaml
  and risk-h.yaml). With risk on, lab-live runs without --ignore-risk and reads the risk files the groups name; A2 then
  allows the risk gate's reasons and the 'at entry' orders; the heartbeat table must be equal row for row and each
  group's wallet file byte for byte; the measure line counts the risk reasons per group on each side.
- LAB_PAPER_REPORT=1 (RC1, spec D3): Python's daily export and report made from lab-live's journal equal the reference's
  own: every day's Parquet tables, and the last day's report.md and report.parquet (an earlier day's report counts the
  journal up to its close, so only the last day's can be made again afterwards).

Needs the built binary: LAB_LIVE_BIN=<path to lab-live> (lab/live/nas_m1c2.sh builds it); those tests skip without it, and
fail instead with LAB_LEGS_REQUIRE=1. A NAS job, never on the VPS. The report's, the reference checks' and the structure
rules' own tests run anywhere.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
from collections import Counter, defaultdict

import duckdb
import pyarrow.parquet as pq
import pytest

from backtest import data
from backtest.replay import Holdings, _insertion_index
from paper import journal_diff, snapshot_export
from paper.feed import ReplayFeed
from paper.journal import DAILY, Journal
from paper.report import daily_report
from paper.tests.test_lab_live_signals import ENGINE_ARGS, _start, _state, _wait, _wait_groups

LAB = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BIN = os.environ.get("LAB_LIVE_BIN", "")
STORE = os.environ.get("LAB_PAPER_STORE", "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb")
R1 = (450_128_159, 450_380_000)
SIZE = 500_000_000
FIVE = ("signals", "orders", "fills", "exits", "positions")
SHOWN = 20

REQUIRE = os.environ.get("LAB_LEGS_REQUIRE") == "1"
MODE = os.environ.get("LAB_PAPER_MODE", "inert")
assert MODE in ("inert", "config", "risk"), MODE
RISK_ON = MODE != "inert"
# The paper start's files at c1e36a0 (spec D5): RC1/RC2 run with exactly these.
START_FILES = {"paper/k3-paper-2026-10-12.yaml": "04b5e652eb0301ae768745adeb7127c5c69be3af889e209715ff3122d3e81f64",
               "paper/h-arms-2026-10-16.yaml": "0fdd1750e2dab2e097bf08179d03554424071b2cc7168cd550abd6a6597ff13b",
               "paper/risk.yaml": "f484714528521f3e73beeafe45535c3018c97e23cc38899a64b71cc3f5bcc9ee",
               "paper/risk-h.yaml": "18c495b5c7604f2a6f75994617e1cb9a09bbf43d804a9eb993f8a6a0ff5ce92e"}
CONFIG_ARGS = ["--config", "paper/k3-paper-2026-10-12.yaml", "--groups", "paper/h-arms-2026-10-16.yaml", "--no-backup"]
WALLETS = ("wallet.json", "wallet-h.json")
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

def reference_checks(journal, risk_on=False):
    """A2: what is wrong with the reference for C2 (empty: nothing). With risk on (C3) the risk gate's reasons and the 'at
    entry' orders are the point, so only Python venue math and the 0.5 SOL orders (both risk files' size) are required."""
    bad = []
    if _state(journal, "venue_math") != "python":
        bad.append(f"venue_math {_state(journal, 'venue_math')!r}, not 'python'")
    allowed = ("busy", *journal_diff.FARM_GATES)
    for reason, n in [] if risk_on else _all(journal, "SELECT reason, count(*) FROM signals WHERE status = 'skipped' GROUP BY reason"):
        if reason not in allowed:
            bad.append(f"{n} signals skipped for {reason!r}")
    n = 0 if risk_on else _all(journal, "SELECT count(*) FROM orders WHERE reason LIKE 'at entry:%'")[0][0]
    if n:
        bad.append(f"{n} orders skipped at entry by risk")
    at_entry_ok = " AND (reason IS NULL OR reason NOT LIKE 'at entry:%')" if risk_on else ""  # an 'at entry' order has size 0
    for size, n in _all(journal, f"SELECT size, count(*) FROM orders WHERE size != ?{at_entry_ok} GROUP BY size", [SIZE]):
        bad.append(f"{n} orders of size {size}")
    return bad


def heartbeat_diff(journal_a, journal_b):
    """C3-3: the heartbeat tables row for row (key minute, note; every column). Returns (rows a, rows b, differences)."""
    q = "SELECT minute, day, last_slot, last_event, events, open_positions, gap_slots, note FROM heartbeat ORDER BY minute, note"
    a, b = _all(journal_a, q), _all(journal_b, q)
    diff = [(x, y) for x, y in zip(a, b) if x != y] + [(x, None) for x in a[len(b):]] + [(None, y) for y in b[len(a):]]
    return len(a), len(b), diff


def wallet_diff(dir_a, dir_b):
    """C3-3: each group's wallet file byte for byte (a missing file on one side is a difference)."""
    out = []
    for name in WALLETS:
        pa_, pb = os.path.join(dir_a, name), os.path.join(dir_b, name)
        a = open(pa_, "rb").read() if os.path.exists(pa_) else None
        b = open(pb, "rb").read() if os.path.exists(pb) else None
        if a != b:
            out.append(f"{name}: {'missing' if a is None else len(a)} / {'missing' if b is None else len(b)} bytes, different")
    return out


def risk_reasons(journal):
    """The risk gate's skips and the 'at entry' orders per group and reason (spec §1; the farm gates and busy left out):
    {group: {reason: n}}, every group of state 'groups' present, so a group whose gate counted nothing shows {}. A row's
    group is its variant's (N2 rows carry the primary's variant)."""
    groups = json.loads(_state(journal, "groups") or "{}")
    of = {v: g for g, spec in groups.items() for v in spec["variants"]}

    def per_group(rows):
        out = {g: {} for g in sorted(groups)}
        for variant, reason, n in rows:
            g = of.get(variant, "?")
            out.setdefault(g, {})[reason] = out.get(g, {}).get(reason, 0) + n
        return out

    skip = per_group(_all(journal, "SELECT variant, reason, count(*) FROM signals WHERE status = 'skipped' AND reason NOT IN "
                                   "(?, ?, ?, 'busy') GROUP BY variant, reason ORDER BY variant, reason", list(journal_diff.FARM_GATES)))
    entry = per_group(_all(journal, "SELECT variant, reason, count(*) FROM orders WHERE reason LIKE 'at entry:%' GROUP BY variant, "
                                    "reason ORDER BY variant, reason"))
    return skip, entry


def report_diff(ref, rust):
    """Spec D3 (RC1): Python's daily export and report made from a copy of lab-live's journal against the reference's own
    (paper.engine's _close_day): every day's Parquet tables, and the last day's report. Returns (days, problems)."""
    days = sorted(d for d in os.listdir(ref) if len(d) == 10 and d[4] == "-" and os.path.isdir(os.path.join(ref, d)))
    bad = []
    with tempfile.TemporaryDirectory(prefix="c3-report-") as tmp:
        copy = os.path.join(tmp, "rust")
        keep(copy, {".": rust}, [])
        j = Journal(copy)
        try:
            for day in days:
                j.export_day(day)
                for t in DAILY:
                    a, b = pq.read_table(os.path.join(ref, day, f"{t}.parquet")), pq.read_table(os.path.join(copy, day, f"{t}.parquet"))
                    if not a.equals(b):
                        bad.append(f"{day} {t}.parquet: {a.num_rows} / {b.num_rows} rows, different")
            if days:
                daily_report(j, days[-1])
                for name in ("report.md",):
                    if open(os.path.join(ref, days[-1], name), "rb").read() != open(os.path.join(copy, days[-1], name), "rb").read():
                        bad.append(f"{days[-1]} {name}: different")
                a, b = pq.read_table(os.path.join(ref, days[-1], "report.parquet")), pq.read_table(os.path.join(copy, days[-1], "report.parquet"))
                if not a.equals(b):
                    bad.append(f"{days[-1]} report.parquet: different")
        finally:
            j.close()
    if not days:
        bad.append("the reference closed no day")
    return days, bad


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
    """A journal's curve orders with entry_slot >= the replay's end, whatever their status: their seed and fill would read
    store legs past it."""
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


def keep(dst, journals, files):
    """Copies each journal directory's journal.sqlite whole (SQLite's backup: the WAL included, the source only read) to
    dst/<name>/journal.sqlite, and the files to dst/. journals: {name: directory}; files: paths, a missing one skipped.
    Returns the kept paths."""
    import shutil

    kept = []
    for name, src in journals.items():
        os.makedirs(os.path.join(dst, name), exist_ok=True)
        out = os.path.join(dst, name, "journal.sqlite")
        if os.path.exists(out):
            os.remove(out)
        a, b = _db(src), sqlite3.connect(out)
        try:
            a.backup(b)
        finally:
            a.close()
            b.close()
        kept.append(out)
    for f in files:
        if os.path.exists(f):
            shutil.copyfile(f, os.path.join(dst, os.path.basename(f)))
            kept.append(os.path.join(dst, os.path.basename(f)))
    return kept


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


def engine_args():
    """paper.engine's arguments by LAB_PAPER_MODE: C1b's (risk inert), the paper start's files, or C1b's with risk on."""
    if MODE == "config":
        return CONFIG_ARGS
    return [a for a in ENGINE_ARGS if not (RISK_ON and a == "--ignore-risk")]


def _python(lo, hi, journal, log):
    import sys

    # This harness verifies the frozen Rust rule-1 port. Do not compare it with
    # new Python rule-2 settlements, or silently waive that financial difference.
    reference = "from functools import partial; from paper import engine; engine.Engine = partial(engine.Engine, settle_rule=1); engine.main()"
    return _start([sys.executable, "-c", reference, "--from", str(lo), "--to", str(hi), *engine_args(), "--store", STORE,
                   "--journal", journal], log, cwd=LAB, env={**os.environ, "PYTHONPATH": LAB})


def _rust(snap, lo, hi, groups, scn, journal, summary, trace):
    return _start([BIN, "paper", "--chunks-root", data.root(), "--snapshot", snap, "--from", str(lo), "--to", str(hi), "--groups",
                   groups, "--variants", "0.04:600", "--cost-scenario", scn, *([] if RISK_ON else ["--ignore-risk"]), "--journal",
                   journal, "--summary", summary, "--trace", trace], summary + ".log")


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
    problems += [f"A2: {p}" for p in reference_checks(py_j, RISK_ON)]
    if MODE == "config":  # spec D5: exactly the paper start's files
        for f, want in START_FILES.items():
            with open(os.path.join(LAB, f), "rb") as fh:
                got = hashlib.sha256(fh.read()).hexdigest()
            if got != want:
                problems.append(f"D5: {f} sha256 {got[:12]}, not c1e36a0's {want[:12]}")
            _measure(f"lab-live paper config file {f} sha256 {got}")
    beats = heartbeat_diff(py_j, rs_j)
    if beats[2]:
        problems.append(f"heartbeat: {beats[0]} / {beats[1]} rows, {len(beats[2])} different, the first {beats[2][:3]}")
    wallets = wallet_diff(py_j, rs_j)
    problems += [f"wallet {w}" for w in wallets]
    days = None
    if os.environ.get("LAB_PAPER_REPORT") == "1":
        days, rbad = report_diff(py_j, rs_j)
        problems += [f"D3: {b}" for b in rbad]
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
    at_to = (curve_entries_at_or_after(py_j, hi), curve_entries_at_or_after(rs_j, hi), s["paper"]["curve_entries_at_or_after_to"])
    if any(at_to):
        problems.append(f"curve orders with entry_slot >= to {hi}: reference {at_to[0]}, lab-live journal {at_to[1]}, its summary {at_to[2]}")
    proof = ""
    if os.environ.get("LAB_PAPER_SEED_PROOF") == "1":
        n, bad, filled = seed_proof(STORE, lo, hi, trace, py_j)
        proof = f"; seed proof {n - len([b for b in bad if b[1] == 'another seed'])}/{n} equal over {filled} filled curve orders"
        if bad or not n:
            problems.append(f"seed proof: {len(bad)} of {n} lines wrong, the first {bad[:5]}")
    summary, lines = chains(py_j, rs_j)
    with open(tmp_path / "chains.md", "w") as f:
        f.write("\n".join(lines) + "\n")
    if summary["chains"]:
        problems.append(f"per-chain report: {summary['chains']} chains, {summary['differences']} differences")
    green = not problems
    if not green:
        for line in [f"lab-live paper [{lo}, {hi}) on {os.path.basename(STORE)}: RED, the per-chain report:", *lines, *problems]:
            _measure(line)
    tb = res["tables"]
    skip_py, entry_py = risk_reasons(py_j)
    skip_rs, entry_rs = risk_reasons(rs_j)
    _measure(f"lab-live paper [{lo}, {hi}) on {os.path.basename(STORE)}: {'GREEN' if green else 'RED'}, {res['unexplained']} unexplained; "
             f"mode {MODE}; heartbeat rows {beats[0]} / {beats[1]} ({len(beats[2])} different); wallet files "
             f"{sum(os.path.exists(os.path.join(py_j, w)) for w in WALLETS)} ({len(wallets)} different); risk skips Python {skip_py}, "
             f"Rust {skip_rs}; at entry Python {entry_py}, Rust {entry_rs}"
             + ("" if days is None else f"; daily export and report from lab-live's journal: {len(days)} days equal") + "; "
             f"rows Python {rows[py_j]}, Rust {rows[rs_j]} (only Python {sum(t['only_a'] for t in tb.values())}, only Rust "
             f"{sum(t['only_b'] for t in tb.values())}, different {sum(t['different'] for t in tb.values())}); orders Python "
             f"{_counts(py_j, 'SELECT status, count(*) FROM orders GROUP BY 1')}, Rust {_counts(rs_j, 'SELECT status, count(*) FROM orders GROUP BY 1')}; "
             f"exits Python {_counts(py_j, 'SELECT exit_reason, count(*) FROM exits GROUP BY 1')}, Rust "
             f"{_counts(rs_j, 'SELECT exit_reason, count(*) FROM exits GROUP BY 1')}; curve entries {s['paper']['curve_entries']} "
             f"({s['paper']['curve_seeded']} with a seed from before {lo}, {s['paper']['curve_hold_rows']} curve_hold rows); curve "
             f"orders with entry_slot >= to: reference {at_to[0]}, lab-live {at_to[1]} (summary {at_to[2]}); per-chain report "
             f"{summary['chains']} chains, {summary['differences']} differences{proof}; lab-live {s['events']:,} events, {rs_secs:.0f} s, peak RSS {(rs_peak or 0) / 1e9:.2f} GB; "
             f"paper.engine {py_secs:.0f} s, peak RSS {(py_peak or 0) / 1e9:.2f} GB")
    if os.environ.get("LAB_PAPER_KEEP"):  # nas_m1c2.sh keeps both journals, green or red (coordinator)
        keep(os.environ["LAB_PAPER_KEEP"], {"python": py_j, "rust": rs_j},
             [groups, scn, trace, str(tmp_path / "rust.json"), str(tmp_path / "chains.md")])
        for side, d in (("python", py_j), ("rust", rs_j)):  # C3-3: the wallet files beside each journal
            for w in WALLETS:
                if os.path.exists(os.path.join(d, w)):
                    shutil.copyfile(os.path.join(d, w), os.path.join(os.environ["LAB_PAPER_KEEP"], side, w))
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


def test_curve_orders_at_or_after_to_are_counted_whatever_their_status(tmp_path):
    def curve(t, status, reason=None):
        return [(tb, {**r, "venue": "curve"} if tb == "signals" else r) for tb, r in _pos(t, status, t + 50, reason)]

    j = _journal(tmp_path / "j", [*curve(100, "filled"), *curve(197, "entry_failed", "no_curve_state"), *curve(198, "skipped", "x"),
                                  *_pos(300, "filled", 400)])
    # entry slots 102, 199, 200 on the curve and 302 in a pool: `to` counts entry_slot >= to, on the curve only
    assert [curve_entries_at_or_after(j, to) for to in (103, 199, 200, 201, 302)] == [2, 2, 1, 0, 0]


def test_the_keep_copies_both_journals_whole(tmp_path):
    a = _journal(tmp_path / "a", _pos(100, "filled", 300))
    j = Journal(str(tmp_path / "b"))  # still open: its rows are in the WAL, not yet in journal.sqlite
    for table, row in _pos(100, "filled", 300):
        j.put(table, **row)
    j.state("venue_math", "python")
    j.commit()
    (tmp_path / "trace.txt").write_text("seed F7 tp4_T600 P 100 x\n")
    try:
        kept = keep(str(tmp_path / "keep"), {"python": a, "rust": str(tmp_path / "b")},
                    [str(tmp_path / "trace.txt"), str(tmp_path / "missing.json")])
    finally:
        j.close()
    assert [os.path.relpath(k, tmp_path / "keep") for k in kept] == ["python/journal.sqlite", "rust/journal.sqlite", "trace.txt"]
    for side in ("python", "rust"):
        assert not os.path.exists(tmp_path / "keep" / side / "journal.sqlite-wal")
        assert _all(str(tmp_path / "keep" / side), "SELECT count(*) FROM fills")[0][0] == 2
    assert journal_diff.run(str(tmp_path / "keep" / "python"), str(tmp_path / "keep" / "rust"), str(tmp_path / "diff"))["green"]


def test_the_seed_hash_is_lab_lives():
    # paper.rs tests: sha256 of "A 3\nNone 5\n"; the empty seed hashes nothing.
    assert seed_sha({"A": 3, None: 5, "B": 0}) == "28e498e804832e66a8e61f51bef07a5f2429392794fb16539b740d56123494fb"
    assert seed_sha({}) == hashlib.sha256(b"").hexdigest()


def _day_journal(path, pnl=-1_000):
    """A journal of one day: a filled position with its exit (pnl given), two heartbeat rows and its states."""
    j = Journal(str(path))
    for table, row in _pos(100, "filled", 300):
        j.put(table, **{**row, "day": "2026-09-22", **({"pnl": pnl, "cost": SIZE, "tokens": 10} if table == "exits" else {})})
    for minute, note in ((1, ""), (1, "feed gap 5..40")):
        j.put("heartbeat", minute=minute, day="2026-09-22", last_slot=5, last_event="5:0:0:0", events=3, open_positions=1,
              gap_slots=34 if note else 0, note=note)
    j.state("venue_math", "python")
    j.commit()
    return j


def test_the_heartbeat_wallet_and_risk_checks(tmp_path):
    a, b = _day_journal(tmp_path / "a"), _day_journal(tmp_path / "b")
    a.close()
    b.put("heartbeat", minute=1, day="2026-09-22", last_slot=5, last_event="5:0:0:0", events=4, open_positions=1, gap_slots=0, note="")
    b.put("signals", family="F7", variant="tp4_T600", key="Q", t=7, venue="pool", status="skipped", reason="max open positions")
    b.put("orders", family="F7", variant="tp4_T600", key="R", t=8, size=0, status="skipped", reason="at entry: kill switch")
    b.close()
    n_a, n_b, diff = heartbeat_diff(str(tmp_path / "a"), str(tmp_path / "b"))
    assert (n_a, n_b, len(diff)) == (2, 2, 1) and diff[0][0][4] == 3 and diff[0][1][4] == 4
    assert heartbeat_diff(str(tmp_path / "a"), str(tmp_path / "a"))[2] == []
    assert risk_reasons(str(tmp_path / "a")) == ({}, {})  # no state 'groups': no group
    with sqlite3.connect(os.path.join(tmp_path / "b", "journal.sqlite")) as db:
        db.execute("INSERT OR REPLACE INTO state (k, v) VALUES ('groups', ?)",
                   [json.dumps({"K3": {"variants": ["tp4_T600"]}, "H": {"variants": ["flow300_H6", "flow300_tp100_H6"]}})])
    assert risk_reasons(str(tmp_path / "b")) == ({"H": {}, "K3": {"max open positions": 1}}, {"H": {}, "K3": {"at entry: kill switch": 1}})
    # wallets: equal bytes pass; a different or a missing file is a difference
    for d in ("a", "b"):
        (tmp_path / d / "wallet.json").write_text('{"mode": "paper"}')
    assert wallet_diff(str(tmp_path / "a"), str(tmp_path / "b")) == []
    (tmp_path / "b" / "wallet.json").write_text('{"mode": "paper", "txs": []}')
    (tmp_path / "a" / "wallet-h.json").write_text("{}")
    assert wallet_diff(str(tmp_path / "a"), str(tmp_path / "b")) == ["wallet.json: 17 / 28 bytes, different",
                                                                     "wallet-h.json: 2 / missing bytes, different"]
    # with risk on, A2 allows the gate's reasons and the 'at entry' orders (size 0)
    assert reference_checks(str(tmp_path / "b"), risk_on=True) == []
    assert reference_checks(str(tmp_path / "b")) == ["1 signals skipped for 'max open positions'", "1 orders skipped at entry by risk",
                                                     "1 orders of size 0"]


def test_the_report_check_makes_the_reference_again(tmp_path):
    """Spec D3: the reference's day exports and last report, made again from an equal journal, are equal; from a journal
    whose exit differs, the day's exits.parquet and the report differ."""
    ref = _day_journal(tmp_path / "ref")
    ref.export_day("2026-09-22")
    daily_report(ref, "2026-09-22")
    ref.close()
    _day_journal(tmp_path / "same").close()
    _day_journal(tmp_path / "other", pnl=-50_000_000).close()
    assert report_diff(str(tmp_path / "ref"), str(tmp_path / "same")) == (["2026-09-22"], [])
    days, bad = report_diff(str(tmp_path / "ref"), str(tmp_path / "other"))
    assert days == ["2026-09-22"] and "2026-09-22 exits.parquet: 1 / 1 rows, different" in bad and "2026-09-22 report.md: different" in bad, bad
    assert not os.path.exists(tmp_path / "same" / "2026-09-22")  # made in a copy, not in lab-live's journal
