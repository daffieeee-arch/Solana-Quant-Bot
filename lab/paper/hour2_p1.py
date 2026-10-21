"""Measuring hour 2's P1 effect on the paper run (LAB review and coordinator, 2026-10-21). The live recorder's root,
replayed by the production engine twice: as recorded (A) and without exactly the transactions the hour-2 recorder
(105140b, learned tx_new_reversed exclusions) did not receive (B). Every journal difference is explained or not.

  python -m paper.hour2_p1 removed --live REC --meas MEAS --lo LO --hi HI --requests FILE --out DIR
  python -m paper.hour2_p1 build --live REC --removed DIR/removed.parquet --from-seq A --to-seq B --lo LO --hi HI --out ROOT
  python -m paper.hour2_p1 explain --journal-a JA --journal-b JB --removed DIR/removed.parquet --lo LO --hi HI --out DIR
  python -m paper.hour2_p1 state --root-a RA --root-b RB --journal-a JA --journal-b JB --out DIR

removed: the live root's transactions in [LO, HI] that arrived there on tx_new_reversed (stream_of names tx_pools and
  tx_probes first, so none of them matched those) and that the hour-2 root holds on no stream: what P1 took away in that
  hour, the exclusions as the server applied them and the background misses. Each is classed by its keys on disk
  (ix_accounts, signers, fee_payer, user of its rows; tx_ctx's fee_payer):
  - excluded_learned: a key that was in a hour-2 request at its created_at (within TOL s of the send times);
  - excluded_static: a static exclusion;
  - background: keys on disk, none of them excluded;
  - no_keys: nothing on disk.
build: a link copy of the live root's segments FROM..TO (as paper.restart_root: entries linked, _segment.json copied).
  The segments that overlap [LO, HI] are rebuilt with every top-level parquet table filtered (rows of a removed
  signature; tx_ctx by (slot, tx_index)). backfill/, blocks and block_footers stay links. Nothing is written under
  the live root.
explain: paper.journal_diff of the two journals, with a cause per difference:
  - excluded: its pool has removed transactions in [LO, HI], all of them excluded_*;
  - follows_signal:<cause>: an order, fill, exit or position row of a signal whose own difference is explained;
  - cascade_trader: its pool has none, but the signal's trigger trader signed removed transactions, all excluded_*;
  - cascade_capacity: its pool has none, it is at or after the first 'excluded' difference, and A or B gives a capacity
    reason (busy, max open positions, daily loss limit);
  - otherwise unexplained.
  A difference before LO is unexplained by construction: the inputs are equal there, so the engine must be.
state (INFO): the engines' own state, A against B: the watchlist log per minute (counts and pool set) and the learned
  table (slot and info per kind and key); it can differ while no decision does.
"""

import argparse
import bisect
import glob
import importlib.util
import json
import os
import sqlite3
import sys

import duckdb

TOL_S = 2  # the request log has whole seconds; the server applies a request a moment after it is sent
CAPACITY = ("busy", "max open positions", "daily loss limit")
KEY_COLS = ("ix_accounts", "signers", "fee_payer", "user")
POOL_COLS = ("pool", "ix_pool")


def connect():
    con = duckdb.connect()
    con.execute("SET memory_limit = '1GB'; SET threads = 2")
    return con


def segments(root):
    """[(seq, dir, meta)] of the complete segments, by seq."""
    out = []
    for p in glob.glob(os.path.join(root, "segments", "*", "_segment.json")):
        with open(p) as f:
            m = json.load(f)
        if m.get("complete"):
            out.append((int(m["seq"]), os.path.dirname(p), m))
    return sorted(out)


def tables(seg_dir, top_only=True):
    """{relative table dir: [parquet files]} of one segment (backfill/ left out when top_only)."""
    out = {}
    for f in glob.glob(os.path.join(seg_dir, "**", "*.parquet"), recursive=True):
        rel = os.path.relpath(os.path.dirname(f), seg_dir)
        if top_only and rel.split(os.sep)[0] == "backfill":
            continue
        out.setdefault(rel, []).append(f)
    return out


def flat_keys(ix):
    """Every address string in an ix_accounts JSON (lists and _extra included)."""
    out = set()
    try:
        v = json.loads(ix)
    except (TypeError, ValueError):
        return out
    stack = [v]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)
        elif isinstance(x, str) and 32 <= len(x) <= 44:
            out.add(x)
    return out


class Requests:
    """The hour-2 requests as sent (hour2_requests.json): is an address excluded at a unix second?"""

    def __init__(self, path):
        with open(path) as f:
            d = json.load(f)
        self.static = set(d["static_excludes"])
        reqs = sorted((r["send_unix"], set(r["learned"])) for r in d["requests"])
        self.times = [t for t, _ in reqs]
        self.sets = [s for _, s in reqs]
        self.end = d["end_unix"]

    def learned_at(self, t, tol=TOL_S):
        """The learned addresses of every request in force at some moment of [t - tol, t + tol] (request i is in force
        from its send time to the next one's)."""
        out = set()
        i0 = bisect.bisect_right(self.times, t - tol) - 1  # in force at t - tol (-1: none sent yet)
        i1 = bisect.bisect_right(self.times, t + tol) - 1  # in force at t + tol
        for i in range(max(i0, 0), i1 + 1):
            out |= self.sets[i]
        return out


def cmd_removed(a):
    live = [s for s in segments(a.live) if s[2]["last_slot"] >= a.lo and s[2]["first_slot"] <= a.hi]
    meas = segments(a.meas)
    if not live or not meas:
        raise SystemExit(f"no complete segments: live {len(live)}, meas {len(meas)}: refused")
    ms = [m for _, _, m in meas]
    if min(m["first_slot"] for m in ms) > a.lo or max(m["last_slot"] for m in ms) < a.hi:
        raise SystemExit(f"the hour-2 root does not cover [{a.lo}, {a.hi}]: refused")
    con = connect()
    live_arr = [f for _, d, _ in live for f in glob.glob(os.path.join(d, "arrivals", "*.parquet"))]
    meas_arr = [f for _, d, _ in meas for f in glob.glob(os.path.join(d, "arrivals", "*.parquet"))]
    con.execute("CREATE TEMP TABLE la AS SELECT signature, slot, tx_index, stream, created_unix_ns, received_unix_ns "
                "FROM read_parquet(?, union_by_name = true) WHERE slot BETWEEN ? AND ?", [live_arr, a.lo, a.hi])
    con.execute("CREATE TEMP TABLE ms AS SELECT DISTINCT signature FROM read_parquet(?, union_by_name = true) "
                "WHERE slot BETWEEN ? AND ?", [meas_arr, a.lo, a.hi])
    dup = con.execute("SELECT count(*) - count(DISTINCT signature) FROM la").fetchone()[0]
    con.execute("CREATE TEMP TABLE r AS SELECT signature, slot, tx_index, created_unix_ns, received_unix_ns FROM la "
                "WHERE stream = 'tx_new_reversed' AND signature NOT IN (SELECT signature FROM ms)")
    counts = dict(con.execute("SELECT stream, count(*) FROM la GROUP BY 1").fetchall())
    n_nr = counts.get("tx_new_reversed", 0)
    n_r = con.execute("SELECT count(*) FROM r").fetchone()[0]
    # the removed transactions' keys and pools, from every top-level table of the live window
    keys, pools = {}, {}
    sigs = {s for (s,) in con.execute("SELECT signature FROM r").fetchall()}
    pairs = {(sl, ti): s for s, sl, ti in con.execute("SELECT signature, slot, tx_index FROM r").fetchall()}
    for _, d, _ in live:
        for rel, fs in tables(d).items():
            cols = [c[0] for c in con.execute("DESCRIBE SELECT * FROM read_parquet(?, union_by_name = true)", [fs]).fetchall()]
            kc, pc = [c for c in KEY_COLS if c in cols], [c for c in POOL_COLS if c in cols]
            if "signature" in cols and (kc or pc):
                sel = ", ".join(["signature"] + kc + pc)
                rows = con.execute(f"SELECT {sel} FROM read_parquet(?, union_by_name = true) "
                                   "WHERE signature IN (SELECT signature FROM r)", [fs]).fetchall()
                for row in rows:
                    sig, vals = row[0], dict(zip(kc + pc, row[1:]))
                    k = keys.setdefault(sig, set())
                    if vals.get("ix_accounts"):
                        k |= flat_keys(vals["ix_accounts"])
                    if vals.get("signers"):
                        k |= {x for x in vals["signers"].split(",") if x}
                    for c in ("fee_payer", "user"):
                        if vals.get(c):
                            k.add(vals[c])
                    for c in pc:
                        if vals.get(c):
                            pools.setdefault(sig, set()).add(vals[c])
            elif rel == "tx_ctx" and "fee_payer" in cols:
                for sl, ti, fp in con.execute("SELECT slot, tx_index, fee_payer FROM read_parquet(?, union_by_name = true) "
                                              "WHERE slot BETWEEN ? AND ?", [fs, a.lo, a.hi]).fetchall():
                    s = pairs.get((sl, ti))
                    if s and fp:
                        keys.setdefault(s, set()).add(fp)
    req = Requests(a.requests)
    out_rows, by_class = [], {}
    for s, sl, ti, cr, rc in con.execute("SELECT * FROM r ORDER BY slot, tx_index").fetchall():
        k = keys.get(s, set())
        t = int((cr or rc) // 10**9)
        learned_hit = sorted(k & req.learned_at(t))
        strict_hit = sorted(k & req.learned_at(t, 0))
        static_hit = sorted(k & req.static)
        cls = ("excluded_static" if static_hit else "excluded_learned" if learned_hit else "background" if k else "no_keys")
        by_class[cls] = by_class.get(cls, 0) + 1
        out_rows.append((s, sl, ti, cr, rc, cls, ",".join(static_hit or learned_hit), bool(strict_hit) or bool(static_hit),
                         ",".join(sorted(pools.get(s, ()))), ",".join(sorted(k))))
    os.makedirs(a.out, exist_ok=True)
    con.execute("CREATE TEMP TABLE o (signature VARCHAR, slot UBIGINT, tx_index UBIGINT, created_unix_ns UBIGINT, "
                "received_unix_ns UBIGINT, class VARCHAR, hit VARCHAR, hit_strict BOOLEAN, pools VARCHAR, keys VARCHAR)")
    con.executemany("INSERT INTO o VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", out_rows)
    path = os.path.join(a.out, "removed.parquet")
    con.execute(f"COPY o TO '{path}' (FORMAT parquet)")
    summary = {"lo": a.lo, "hi": a.hi, "live_segments": [live[0][0], live[-1][0]], "meas_segments": [meas[0][0], meas[-1][0]],
               "live_arrivals_by_stream": counts, "live_duplicate_signatures": dup, "live_tx_new_reversed": n_nr,
               "removed": n_r, "removed_share_of_nr": round(n_r / n_nr, 4) if n_nr else None, "by_class": by_class,
               "learned_hit_needs_tolerance": sum(1 for r in out_rows if r[5] == "excluded_learned" and not r[7]),
               "removed_with_a_pool": sum(1 for r in out_rows if r[8]), "tol_s": TOL_S}
    with open(os.path.join(a.out, "removed.json"), "w") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps(summary))
    if dup:
        raise SystemExit(f"{dup} duplicate signatures in the live arrivals: the removal by signature is ambiguous")


def cmd_build(a):
    rec, out = os.path.realpath(a.live), os.path.abspath(a.out)
    if os.path.commonpath([rec, out]) == rec:
        raise SystemExit(f"{out} is inside {rec}: refused")
    if os.path.exists(out) and os.listdir(out):
        raise SystemExit(f"{out} exists and is not empty: refused")
    segs = [s for s in segments(rec) if a.from_seq <= s[0] <= a.to_seq]
    if not segs or segs[0][0] != a.from_seq or segs[-1][0] != a.to_seq or len(segs) != a.to_seq - a.from_seq + 1:
        raise SystemExit(f"segments {a.from_seq}..{a.to_seq} are not all there and complete: refused")
    con = connect()
    if a.removed == "none":  # a plain link copy (root A)
        con.execute("CREATE TEMP TABLE r (signature VARCHAR, slot UBIGINT, tx_index UBIGINT)")
    else:
        con.execute("CREATE TEMP TABLE r AS SELECT signature, slot, tx_index FROM read_parquet(?)", [a.removed])
    n_r = con.execute("SELECT count(*) FROM r").fetchone()[0]
    os.makedirs(os.path.join(out, "segments"))
    dropped, filtered_segs = {}, 0
    for seq, src, meta in segs:
        dst = os.path.join(out, "segments", os.path.basename(src))
        os.mkdir(dst)
        window = meta["last_slot"] >= a.lo and meta["first_slot"] <= a.hi
        if not window:
            for entry in sorted(os.listdir(src)):
                if entry != "_segment.json":
                    os.symlink(os.path.join(src, entry), os.path.join(dst, entry))
        else:
            filtered_segs += 1
            for root, dirs, files in os.walk(src):
                rel = os.path.relpath(root, src)
                top = rel.split(os.sep)[0]
                if rel != "." and (top == "backfill" or top in ("blocks", "block_footers")):
                    # whole subtree as links: create the link at its top and stop descending
                    os.symlink(root, os.path.join(dst, rel))
                    dirs[:] = []
                    continue
                if rel != ".":
                    os.makedirs(os.path.join(dst, rel), exist_ok=True)
                for name in sorted(files):
                    s, d = os.path.join(root, name), os.path.join(dst, rel, name)
                    if name == "_segment.json":
                        continue
                    if not name.endswith(".parquet"):
                        os.symlink(s, d)
                        continue
                    cols = [c[0] for c in con.execute("DESCRIBE SELECT * FROM read_parquet(?)", [s]).fetchall()]
                    if "signature" in cols:
                        cond = "signature NOT IN (SELECT signature FROM r)"
                    elif "slot" in cols and "tx_index" in cols:
                        cond = "(slot, tx_index) NOT IN (SELECT (slot, tx_index) FROM r)"
                    else:
                        os.symlink(s, d)
                        continue
                    n_all, n_keep = con.execute(f"SELECT count(*), count(*) FILTER (WHERE {cond}) FROM read_parquet(?)", [s]).fetchone()
                    if n_keep == n_all:
                        os.symlink(s, d)
                        continue
                    dropped[rel] = dropped.get(rel, 0) + n_all - n_keep
                    if n_keep:
                        con.execute(f"COPY (SELECT * FROM read_parquet('{s}') WHERE {cond}) TO '{d}' (FORMAT parquet)")
            # remove table directories left empty by the filter (a recorder writes no empty table)
            for root, dirs, files in os.walk(dst, topdown=False):
                if root != dst and not os.path.islink(root) and not os.listdir(root):
                    os.rmdir(root)
        with open(os.path.join(dst, "_segment.json"), "w") as f:
            json.dump(meta, f)
    res = {"segments": len(segs), "first_slot": segs[0][2]["first_slot"], "last_complete_seq": segs[-1][0],
           "filtered_segments": filtered_segs, "removed": n_r, "rows_dropped": dropped}
    print(json.dumps(res))
    if dropped.get("arrivals", 0) != n_r:
        raise SystemExit(f"arrivals rows dropped {dropped.get('arrivals', 0)} != removed signatures {n_r}: RED")


def cmd_explain(a):
    # the engine's own comparison, loaded by path: `paper` is this checkout's package, BASE_REPO's may differ
    src = os.path.join(a.journal_diff_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."), "paper", "journal_diff.py")
    spec = importlib.util.spec_from_file_location("base_journal_diff", src)
    journal_diff = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(journal_diff)

    con = connect()
    rem = con.execute("SELECT signature, slot, class, pools, keys FROM read_parquet(?)", [a.removed]).fetchall()
    by_pool, by_key = {}, {}
    for sig, slot, cls, pools, keys in rem:
        for p in (pools or "").split(","):
            if p:
                by_pool.setdefault(p, []).append((slot, cls))
        for k in (keys or "").split(","):
            if k:
                by_key.setdefault(k, []).append((slot, cls))

    def all_excluded(entries):
        classes = {c for _, c in entries}
        return bool(classes) and classes <= {"excluded_learned", "excluded_static"}

    # the trigger trader of every signal, from both journals (orders, fills and exits do not carry it)
    trader = {}
    for j in (a.journal_a, a.journal_b):
        db = sqlite3.connect(f"file:{os.path.join(j, 'journal.sqlite')}?mode=ro", uri=True)
        for fam, var, key, t, tr in db.execute("SELECT family, variant, key, t, trigger_trader FROM signals"):
            if tr:
                trader.setdefault((fam, var, key, int(t)), set()).add(tr)
        db.close()

    # pass 1: every difference (journal_diff lists only the first ones); the first one an exclusion explains
    seen = []
    journal_diff.compare(a.journal_a, a.journal_b, causes=(lambda table, key, ra, rb: seen.append((table, key)) or None,))
    roots = [int(k[3]) for _, k in seen if int(k[3]) >= a.lo and k[2] in by_pool and all_excluded(by_pool[k[2]])]
    first_root = min(roots) if roots else None
    calls, signal_cause = [], {}

    def cause(table, key, row_a, row_b):
        pool, t, sig_key = key[2], int(key[3]), (key[0], key[1], key[2], int(key[3]))
        rows = [x for x in (row_a, row_b) if x]
        reasons = {x.get("reason") for x in rows}
        hits = [e for tr in trader.get(sig_key, ()) for e in by_key.get(tr, ())]
        if t < a.lo:
            c = None  # equal inputs: a difference here is the engine's own
        elif pool in by_pool:
            c = "excluded" if all_excluded(by_pool[pool]) else None
        elif table != "signals" and signal_cause.get(sig_key):
            c = f"follows_signal:{signal_cause[sig_key]}"
        elif hits and all_excluded(hits):
            c = "cascade_trader"
        elif first_root is not None and t >= first_root and reasons & set(CAPACITY):
            c = "cascade_capacity"
        else:
            c = None
        if table == "signals":
            signal_cause[sig_key] = c
        calls.append({"table": table, "key": list(key), "a": row_a, "b": row_b, "cause": c or "UNEXPLAINED",
                      "pool_removed": len(by_pool.get(pool, ())),
                      "pool_removed_classes": sorted({x for _, x in by_pool.get(pool, ())})})
        return c

    res = journal_diff.compare(a.journal_a, a.journal_b, causes=(cause,))
    res["differences"] = calls
    res["first_excluded_t"] = first_root
    res["removed_classes"] = {c: sum(1 for x in rem if x[2] == c) for c in sorted({x[2] for x in rem})}
    res["before_lo"] = sum(1 for x in calls if int(x["key"][3]) < a.lo)
    # per cause; cascade_capacity is the widest rule (LAB review on eaa5626): the strict variant also needs the reasons
    # to differ between A and B (a row in one journal only counts as differing), and a share above 10 % is flagged
    by_cause = {}
    for x in calls:
        c = x["cause"].split(":")[0]
        by_cause[c] = by_cause.get(c, 0) + 1
    cc = [x for x in calls if x["cause"] == "cascade_capacity"]

    def strict(x):
        ra, rb = (x["a"] or {}).get("reason"), (x["b"] or {}).get("reason")
        return (x["a"] is None or x["b"] is None or ra != rb) and bool({ra, rb} & set(CAPACITY))

    n_strict = sum(1 for x in cc if strict(x))
    res["by_cause"] = by_cause
    res["cascade_capacity"] = {"n": len(cc), "strict": n_strict, "share": round(len(cc) / len(calls), 4) if calls else 0.0,
                               "over_10pct": bool(calls) and len(cc) > 0.1 * len(calls)}
    res["green_strict"] = res["green"] and n_strict == len(cc)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "explain.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    lines = journal_diff.report(res)
    ccs = res["cascade_capacity"]
    lines += ["", f"- removed transactions by class: {res['removed_classes']}; first 'excluded' difference at t={first_root}; "
              f"differences before LO: {res['before_lo']}",
              f"- causes: {by_cause}; cascade_capacity {ccs['n']} ({ccs['share']:.1%} of the differences"
              + (", OVER 10 %" if ccs["over_10pct"] else "") + f"), strict (reasons differ A/B) {ccs['strict']}; "
              f"GREEN {'yes' if res['green'] else 'no'}, strict GREEN {'yes' if res['green_strict'] else 'no'}",
              "", "| table | key | cause | A reason | B reason | pool removed (classes) |", "|---|---|---|---|---|---|"]
    for x in calls:
        ra, rb = (x["a"] or {}).get("reason", "-") if x["a"] else "(none)", (x["b"] or {}).get("reason", "-") if x["b"] else "(none)"
        lines.append(f"| {x['table']} | {x['key']} | {x['cause']} | {ra} | {rb} | {x['pool_removed']} {x['pool_removed_classes']} |")
    with open(os.path.join(a.out, "explain.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines[:3]))
    return 0 if res["green"] else 1


def cmd_state(a):
    """INFO next to journal_diff (LAB review on the eaa5626 run): the engines' own state, which can differ while no
    decision does (the latent cascade): the watchlist log per minute (its counts, and its pool set) and the learned
    table (kind, key: slot and info)."""

    def wl(root):
        out = {}
        with open(os.path.join(root, "watchlist-log.jsonl")) as f:
            for line in f:
                r = json.loads(line)
                out[int(r["minute"])] = r
        return out

    wa, wb = wl(a.root_a), wl(a.root_b)
    minutes = sorted(set(wa) | set(wb))
    one_side = [m for m in minutes if (m in wa) != (m in wb)]
    counts, pools = {}, {}
    for m in minutes:
        ra, rb = wa.get(m), wb.get(m)
        if not ra or not rb:
            continue
        ca, cb = ra.get("counts", {}), rb.get("counts", {})
        for k in sorted(set(ca) | set(cb)):
            if ca.get(k) != cb.get(k):
                counts.setdefault(k, []).append((m, ca.get(k), cb.get(k)))
        pa, pb = set(ra.get("pools", ())), set(rb.get("pools", ()))
        for side, extra, slot in (("A", pa - pb, ra.get("slot")), ("B", pb - pa, rb.get("slot"))):
            for p in extra:
                pools.setdefault((p, side), []).append((m, slot))
    differing = sorted({m for v in counts.values() for m, *_ in v} | {m for v in pools.values() for m, _ in v})

    def learned(j):
        db = sqlite3.connect(f"file:{os.path.join(j, 'journal.sqlite')}?mode=ro", uri=True)
        try:
            return {(k, key): (slot, info) for k, key, slot, info in db.execute("SELECT kind, key, slot, info FROM learned")}
        finally:
            db.close()

    la, lb = learned(a.journal_a), learned(a.journal_b)
    l_only_a = sorted(set(la) - set(lb))
    l_only_b = sorted(set(lb) - set(la))
    l_diff = []
    for k in sorted(set(la) & set(lb)):
        (sa, ia), (sb, ib) = la[k], lb[k]
        if sa != sb or ia != ib:
            try:
                xa, xb = json.loads(ia) if ia else None, json.loads(ib) if ib else None
                pos = [i for i in range(max(len(xa or ()), len(xb or ())))
                       if (xa[i] if xa and i < len(xa) else None) != (xb[i] if xb and i < len(xb) else None)]
                info_diff = {i: [xa[i] if xa and i < len(xa) else None, xb[i] if xb and i < len(xb) else None] for i in pos}
            except (TypeError, ValueError):
                info_diff = {"raw": [ia, ib]}
            l_diff.append({"kind": k[0], "key": k[1], "slot": [sa, sb], "info": info_diff})
    res = {
        "watchlist": {"minutes_a": len(wa), "minutes_b": len(wb), "minutes_one_side": len(one_side),
                      "minutes_differing": len(differing),
                      "first_last_differing": [differing[0], differing[-1]] if differing else None,
                      "counts": {k: {"minutes": len(v), "b_minus_a_min": min((y or 0) - (x or 0) for _, x, y in v),
                                     "b_minus_a_max": max((y or 0) - (x or 0) for _, x, y in v),
                                     "first_minute": v[0][0], "last_minute": v[-1][0]} for k, v in counts.items()},
                      "pools": [{"pool": p, "only_in": side, "minutes": len(v), "first_minute": v[0][0], "last_minute": v[-1][0],
                                 "first_slot": v[0][1], "last_slot": v[-1][1]} for (p, side), v in sorted(pools.items())]},
        "learned": {"rows_a": len(la), "rows_b": len(lb), "only_a": [list(k) for k in l_only_a], "only_b": [list(k) for k in l_only_b],
                    "different": l_diff},
    }
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "state.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    w, ln = res["watchlist"], res["learned"]
    lines = ["# Engine state, A against B (INFO: no decision; LAB review on the eaa5626 run)", "",
             f"- watchlist log: {w['minutes_a']} / {w['minutes_b']} minutes, {w['minutes_one_side']} on one side only, "
             f"{w['minutes_differing']} differing" + (f" (minutes {w['first_last_differing'][0]}..{w['first_last_differing'][1]})" if differing else ""),
             f"- watchlist counts differing (B - A): " + ("; ".join(f"{k} {v['minutes']} min, {v['b_minus_a_min']:+d}..{v['b_minus_a_max']:+d}"
                                                               for k, v in w["counts"].items()) or "none"),
             f"- watchlist pools on one side only: {len(w['pools'])}" + "".join(
                 f"; {x['pool']} only in {x['only_in']} {x['minutes']} min (slots {x['first_slot']}..{x['last_slot']})" for x in w["pools"][:10]),
             f"- learned: {ln['rows_a']} / {ln['rows_b']} rows; only A {len(ln['only_a'])}, only B {len(ln['only_b'])}, "
             f"different {len(ln['different'])}" + "".join(f"; {x['kind']} {x['key']} slot {x['slot']} info {x['info']}" for x in ln["different"][:10])
             + "".join(f"; only A {k}" for k in ln["only_a"][:5]) + "".join(f"; only B {k}" for k in ln["only_b"][:5])]
    with open(os.path.join(a.out, "state.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines[2:]))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("removed")
    r.add_argument("--live", required=True)
    r.add_argument("--meas", required=True)
    r.add_argument("--requests", required=True)
    b = sub.add_parser("build")
    b.add_argument("--live", required=True)
    b.add_argument("--removed", required=True, help="removed.parquet, or none for a plain link copy")
    b.add_argument("--from-seq", type=int, required=True)
    b.add_argument("--to-seq", type=int, required=True)
    e = sub.add_parser("explain")
    e.add_argument("--journal-a", required=True)
    e.add_argument("--journal-b", required=True)
    e.add_argument("--removed", required=True)
    e.add_argument("--journal-diff-path", help="the lab/ directory whose paper.journal_diff is used")
    for p in (r, b, e):
        p.add_argument("--lo", type=int, required=True)
        p.add_argument("--hi", type=int, required=True)
        p.add_argument("--out", required=True)
    st = sub.add_parser("state")
    st.add_argument("--root-a", required=True)
    st.add_argument("--root-b", required=True)
    st.add_argument("--journal-a", required=True)
    st.add_argument("--journal-b", required=True)
    st.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    return {"removed": cmd_removed, "build": cmd_build, "explain": cmd_explain, "state": cmd_state}[a.cmd](a) or 0


if __name__ == "__main__":
    sys.exit(main())
