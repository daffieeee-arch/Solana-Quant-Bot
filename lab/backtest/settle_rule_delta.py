"""Codex review #6 (B2): how much settle rule 2 moves existing results; read-only, nothing is re-run and no verdict changes.

Rule 1 (before 24 Oct) charges exit_attempts - 1 failed exit fees; rule 2 charges exit_attempts - filled (costs' docstring).
They differ only on a row whose last counted exit attempt failed (not filled, at least one attempt): rule 2 charges one
failed_tx more. Per source, base and pessimistic, per cell (family, variant, size_sol, d, q, overlay, farm): the settled
rows, the rows that change, the days, net SOL under each rule and the difference (also per day), and the mean return.
Checked on every cell: the difference is exactly the changed rows x failed_tx (else RED).

  python -P -m backtest.settle_rule_delta --out DIR [--run LABEL=PARTS_DIR ...] [--clean NAME=RESULT_JSON ...]
                                         [--e20 K3_ROWS_DIR=TEST_RUN_DIR]

Sources:
- --run: a dev tournament's parts (every row but the skipped ones).
- --clean H3|H4|H5=<clean-test dir>/result/result.json: the producer's parts beside result.json (wallets.clean_reports.
  parts_dir: the run's F7 pool_reversed rows in the window minus the exclusions). Admitted only when the run's SHA256SUMS
  (one directory up) lists result/result.json with its actual sha256 and result.json holds no settle_rule other than 1.
- --e20: E20's K3 rows (k3-rows, #557; their own SHA256SUMS checked) joined on (pool, t, trigger_trader) to the locked test
  run's K3 rows (counts.json's lock: the test run directory and its config.json sha256). Every K3 row must match exactly
  one parts row whose rule-1 net and return equal k3-rows' (net_sol, ret) bit for bit; else REFUSED. Then H1 (filled, no
  rt_tx) and H2 (filled, rt_tx) mean returns under both rules.
The clean results are frozen under rule 1 (clean_reports re-settles under the producer's settle_rule); this only measures.
Writes delta.json, delta.md and SHA256SUMS to DIR (via DIR.tmp; an existing DIR is refused). Memory: LAB_DUCKDB_MEM (1GB).
"""

import argparse
import hashlib
import json
import os
import shutil
import sys

import duckdb

from .costs import FILLED_SQL, Scenario, settle_sql
from .store import load_defaults

SCENARIOS = ("base", "pessimistic")
CELL = ("family", "variant", "size_sol", "d", "q", "overlay", "farm")
E20_K3 = ("family = 'F7' AND segment = 'pool_reversed' AND variant = 'tp4_T600' AND size_sol = 0.5 AND q = 0.5 "
          "AND overlay = 'none' AND NOT skipped")  # clean_test.K3 at E20's engine be47fc4 (the join checks it)
CHANGED = f"(NOT entry_failed AND NOT {FILLED_SQL} AND COALESCE(exit_attempts, 1) >= 1)"
SETTLED = ("day", "skipped", "entry_failed", "cost", "proceeds", "exit_attempts", "exit_slot", "exit_reason")  # settle_sql's columns


class Refused(Exception):
    pass


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def sums(path):
    """{name: sha256} of a SHA256SUMS file."""
    out = {}
    for line in open(path):
        if line.strip():
            h, name = line.split(None, 1)
            out[name.strip()] = h
    return out


def connect():
    con = duckdb.connect(config={"memory_limit": os.environ.get("LAB_DUCKDB_MEM", "1GB"), "threads": 2})
    if os.environ.get("LAB_SPILL_DIR"):
        con.execute(f"SET temp_directory = '{os.environ['LAB_SPILL_DIR']}'")
    return con


def scenarios():
    cfg = load_defaults()
    return {s: Scenario.from_cfg(s, cfg) for s in SCENARIOS}


def cells(con, src, scns, where="NOT skipped"):
    """Per cell of a parts source: rows, changed rows, days, net SOL and mean return under rule 1 and rule 2, per scenario;
    the identity (rule 1 - rule 2) = changed x failed_tx checked per cell and scenario."""
    cols = {c[0] for c in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()}
    missing = [c for c in SETTLED if c not in cols]
    if missing:
        raise Refused(f"{src}: no {missing} (rule 2 reads exit_slot and exit_reason)")
    key = ", ".join(c if c in cols else f"NULL AS {c}" for c in CELL)
    sel = []
    for s, scn in scns.items():
        for rule in (1, 2):
            pnl, ret = settle_sql(scn, rule)
            sel += [f"sum({pnl}) AS pnl_{s}_{rule}", f"avg({ret}) AS ret_{s}_{rule}"]
    rows = con.execute(f"""SELECT {", ".join(CELL)}, count(*) AS n, count(*) FILTER (WHERE changed) AS changed,
        count(DISTINCT day) AS days, {", ".join(sel)}
        FROM (SELECT {key}, {", ".join(SETTLED)}, {CHANGED} AS changed FROM {src} WHERE {where})
        GROUP BY ALL ORDER BY ALL""").fetchall()
    names = [d[0] for d in con.description]
    out, bad = [], []
    for r in rows:
        c = dict(zip(names, r))
        cell = {k: c[k] for k in CELL}
        e = {"cell": cell, "n": c["n"], "changed": c["changed"], "days": c["days"]}
        for s, scn in scns.items():
            p1, p2 = int(c[f"pnl_{s}_1"] or 0), int(c[f"pnl_{s}_2"] or 0)
            if p1 - p2 != c["changed"] * scn.failed_tx:
                bad.append(f"{cell} {s}: rule 1 - rule 2 = {p1 - p2} != {c['changed']} x {scn.failed_tx}")
            e[s] = {"net_sol_1": p1 / 1e9, "net_sol_2": p2 / 1e9, "delta_sol": (p2 - p1) / 1e9,
                    "delta_sol_per_day": (p2 - p1) / 1e9 / c["days"] if c["days"] else None,
                    "mean_ret_1": c[f"ret_{s}_1"], "mean_ret_2": c[f"ret_{s}_2"]}
        out.append(e)
    if bad:
        raise Refused("the rule difference is not changed x failed_tx: " + "; ".join(bad[:5]))
    days = con.execute(f"SELECT count(DISTINCT day) FROM {src} WHERE {where}").fetchone()[0]
    return {"cells": out, "rows": sum(e["n"] for e in out), "changed": sum(e["changed"] for e in out), "days": days}


def parts_glob(d):
    files = sorted(f for f in os.listdir(d) if f.endswith(".parquet")) if os.path.isdir(d) else []
    if not files:
        raise Refused(f"no parquet parts in {d}")
    return f"read_parquet('{os.path.join(d, '*.parquet')}', union_by_name = true)", {f: sha256(os.path.join(d, f)) for f in files}


def clean_source(name, result_path):
    """(parts dir, provenance) of a locked H3 / H4 / H5 result."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    from wallets.clean_reports import PRODUCER, parts_dir

    if name not in PRODUCER:
        raise Refused(f"--clean {name}: one of {sorted(PRODUCER)}")
    run = os.path.dirname(os.path.dirname(os.path.abspath(result_path)))
    listed = sums(os.path.join(run, "SHA256SUMS")).get("result/result.json")
    got = sha256(result_path)
    if listed != got:
        raise Refused(f"{name}: result.json {got[:12]} is not the one {run}/SHA256SUMS lists ({str(listed)[:12]})")
    rule = json.load(open(result_path)).get("settle_rule", 1)
    if rule != 1:
        raise Refused(f"{name}: result.json was settled under rule {rule}, not the frozen rule 1")
    d = parts_dir(result_path, name=name)
    if d is None or os.path.basename(d) != PRODUCER[name][2]:
        raise Refused(f"{name}: no {PRODUCER[name][2]} beside {result_path}")
    return d, {"result_json": os.path.abspath(result_path), "result_json_sha256": got, "settle_rule": rule}


def e20(con, k3_dir, test_run, scns):
    """E20's K3 rows with the test run's exit attempts; refused unless rule 1 reproduces k3-rows bit for bit."""
    listed = sums(os.path.join(k3_dir, "SHA256SUMS"))
    for f in ("k3-rows.parquet", "counts.json"):
        if listed.get(f) != sha256(os.path.join(k3_dir, f)):
            raise Refused(f"E20: {f} is not the one k3-rows/SHA256SUMS lists")
    counts = json.load(open(os.path.join(k3_dir, "counts.json")))
    lock = counts["lock"]
    if os.path.realpath(lock["test_run"]) != os.path.realpath(test_run):
        raise Refused(f"E20: the test run {test_run} is not the lock's {lock['test_run']}")
    if sha256(os.path.join(test_run, "config.json")) != lock["test_config_sha256"]:
        raise Refused("E20: the test run's config.json is not the locked one")
    src, files = parts_glob(os.path.join(test_run, "parts"))
    k3 = os.path.join(k3_dir, "k3-rows.parquet")
    pnl1, ret1 = settle_sql(scns["base"], 1)
    con.execute(f"""CREATE TEMP TABLE e20 AS SELECT * FROM (SELECT * FROM {src} WHERE {E20_K3}) p
                    RIGHT JOIN (SELECT pool, t, trigger_trader, filled, rt_tx, net_sol AS k_net, ret AS k_ret FROM read_parquet('{k3}')) k
                    USING (pool, t, trigger_trader)""")
    n_k3, n_join, unmatched, keys = con.execute("""SELECT (SELECT count(*) FROM read_parquet(?)), count(*),
        count(*) FILTER (WHERE family IS NULL), count(DISTINCT (pool, t, trigger_trader)) FROM e20""", [k3]).fetchone()
    if not n_k3 == n_join == keys or unmatched:
        raise Refused(f"E20: {n_k3} K3 rows, {n_join} joined, {keys} keys, {unmatched} without a parts row (1:1 expected)")
    off = con.execute(f"SELECT count(*) FROM e20 WHERE k_net IS DISTINCT FROM {pnl1} / 1e9 OR k_ret IS DISTINCT FROM {ret1}").fetchone()[0]
    if off:
        raise Refused(f"E20: rule 1 does not reproduce k3-rows' net_sol / ret on {off} rows")
    out = cells(con, "e20", scns, where="TRUE")
    groups = {}
    _, ret2 = settle_sql(scns["base"], 2)
    for name, cond in (("H1 (filled, no rt_tx)", "filled AND NOT rt_tx"), ("H2 (filled, rt_tx)", "filled AND rt_tx"),
                       ("filled", "filled")):
        n, m1, m2, ch = con.execute(f"SELECT count(*), avg({ret1}), avg({ret2}), count(*) FILTER (WHERE {CHANGED}) FROM e20 WHERE {cond}").fetchone()
        groups[name] = {"n": n, "changed": ch, "mean_ret_1": m1, "mean_ret_2": m2, "delta": None if m1 is None else m2 - m1}
    out["e20_groups"] = groups
    out["provenance"] = {"k3_rows": os.path.abspath(k3_dir), "k3_rows_sums": listed, "test_run": os.path.abspath(test_run),
                         "parts_sha256": files, "rows_reproduced": n_k3}
    return out


def render(res):
    L = ["# Settle rule 1 vs 2 (Codex review #6, B2): read-only", "",
         "Rule 2 charges one failed_tx more on every row whose last counted exit attempt failed (unsold, written off); "
         "nothing else moves. Per cell the difference is exactly the changed rows x failed_tx (checked). Frozen results keep "
         "rule 1; this measures only. SOL; base scenario unless named.", ""]
    for label, r in res["sources"].items():
        L += [f"## {label}", "", f"{r['rows']:,} settled rows, {r['changed']:,} change ({r['changed'] / max(r['rows'], 1):.2%}), "
              f"{r['days']} days.", ""]
        if "e20_groups" in r:
            L += ["| group | n | changed | mean ret rule 1 | rule 2 | difference |", "|---|---|---|---|---|---|"]
            for g, v in r["e20_groups"].items():
                f = lambda x: "–" if x is None else f"{x:+.5f}"  # noqa: E731
                L.append(f"| {g} | {v['n']:,} | {v['changed']:,} | {f(v['mean_ret_1'])} | {f(v['mean_ret_2'])} | {f(v['delta'])} |")
            L.append("")
        top = sorted(r["cells"], key=lambda e: -abs(e["base"]["delta_sol_per_day"] or 0))[:15]
        L += ["Largest cells by |difference per day| (base; pessimistic in delta.json):", "",
              "| cell | n | changed | net rule 1 | net rule 2 | difference | per day | mean ret 1 → 2 |", "|---|---|---|---|---|---|---|---|"]
        for e in top:
            c, b = e["cell"], e["base"]
            name = " ".join(f"{v}" for v in c.values() if v is not None)
            mr = "–" if b["mean_ret_1"] is None else f"{b['mean_ret_1']:+.5f} → {b['mean_ret_2']:+.5f}"
            L.append(f"| {name} | {e['n']:,} | {e['changed']:,} | {b['net_sol_1']:+.3f} | {b['net_sol_2']:+.3f} | {b['delta_sol']:+.4f} | "
                     f"{b['delta_sol_per_day'] or 0:+.4f} | {mr} |")
        L.append("")
    return "\n".join(L) + "\n"


def run(a, log=print):
    if os.path.exists(a.out) or os.path.exists(a.out + ".tmp"):
        raise Refused(f"{a.out} (or .tmp) exists")
    scns = scenarios()
    con = connect()
    res = {"failed_tx": {s: scn.failed_tx for s, scn in scns.items()}, "sources": {}}
    for spec in a.run or []:
        label, d = spec.split("=", 1)
        src, files = parts_glob(d)
        r = cells(con, src, scns)
        r["provenance"] = {"parts": os.path.abspath(d), "files": len(files)}
        res["sources"][f"dev {label}"] = r
        log(f"dev {label}: {r['rows']:,} rows, {r['changed']:,} change")
    for spec in a.clean or []:
        name, result_path = spec.split("=", 1)
        d, prov = clean_source(name, result_path)
        src, files = parts_glob(d)
        r = cells(con, src, scns)
        r["provenance"] = {**prov, "parts": d, "parts_sha256": files}
        res["sources"][f"clean {name}"] = r
        log(f"clean {name}: {r['rows']:,} rows, {r['changed']:,} change")
    if a.e20:
        k3_dir, test_run = a.e20.split("=", 1)
        r = e20(con, k3_dir, test_run, scns)
        res["sources"]["clean E20 (K3)"] = r
        log(f"clean E20: {r['rows']:,} rows reproduced under rule 1, {r['changed']:,} change")
    if not res["sources"]:
        raise Refused("no source: pass --run, --clean or --e20")
    tmp = a.out + ".tmp"
    os.makedirs(tmp)
    try:
        with open(os.path.join(tmp, "delta.json"), "w") as f:
            json.dump(res, f, indent=1, default=str)
        with open(os.path.join(tmp, "delta.md"), "w") as f:
            f.write(render(res))
        with open(os.path.join(tmp, "SHA256SUMS"), "w") as f:
            for n in ("delta.json", "delta.md"):
                f.write(f"{sha256(os.path.join(tmp, n))}  {n}\n")
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    os.rename(tmp, a.out)
    log(f"written {a.out}")
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--run", action="append", help="LABEL=PARTS_DIR of a dev tournament")
    ap.add_argument("--clean", action="append", help="H3|H4|H5=<clean-test dir>/result/result.json")
    ap.add_argument("--e20", help="K3_ROWS_DIR=TEST_RUN_DIR (counts.json's lock.test_run)")
    a = ap.parse_args(argv)
    try:
        run(a)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
