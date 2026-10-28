"""E20's K3 rows, one per position, from its locked output (a read-only extract, no rerun): every K3 row of the test
run in the decision window minus the exclusions, as backtest.clean_test computed them, with rt_tx and wash_1h from
backtest.wash on the locked store. For the E20 report (LAB research, 24 Oct).

  python -P lab/backtest/clean_k3_rows.py --lock <store dir>/clean.lock --exclusions FILE --exclusions-sha256 SHA \\
      --result <clean-test dir>/result/result.json --out <clean-test dir>/k3-rows [--store-sha256 PREFIX] [--engine PREFIX] \\
      [--expect FILLED,RT_TX,NO_RT_TX] [--expect-failed N]

Admitted are exactly the lock's store (its sha256 is computed before any read and must be the lock's) and the lock's
test and matrix runs (config.json sha256 the lock's). The code is the test run's own engine snapshot (<test_run>/engine,
its COMMIT the lock's engine_commit): clean_test's K3, F7REV, window, exclusions and settle_sql and backtest.wash are
imported from there, so rt_tx and wash_1h are the test's own. result.json (read only) must agree on every position
count, on the failed entries' net SOL, on H1's and H2's means (ret of the filled trades without / with rt_tx) and on
the wash_1h groups (n and mean), and --expect / --expect-failed on the filled trades and failed entries; anything else
is refused before a file is written. The output (k3-rows.parquet, counts.json, SHA256SUMS) is written to <out>.tmp and renamed; an
existing <out> is refused. Nothing under result/ and the lock is written.

Columns: pool, t, mint, day (the UTC trigger day, tournament._days), entry_failed, filled (NOT entry_failed AND ret
IS NOT NULL: H1/H2's trades), cost, proceeds (lamports), entry_slot, exit_slot, trigger_trader, ret, net_sol (the
base scenario's settle_sql, as clean_test), rt_tx (NULL: no trigger leg), wash_1h. Failed entries are included.

--dry-run (tests): any store that is not clean; the runs' and store's pre-registration checks off (clean_test's).
"""

import argparse
import hashlib
import json
import os
import shutil
import sys

import duckdb

LOCK_KEYS = ("store", "store_sha256", "test_run", "matrix_run", "test_config_sha256", "matrix_config_sha256",
             "engine_commit", "trading_days")
POSITION_KEYS = ("k3", "excluded_positions", "failed_entries", "filled", "rt_tx", "no_rt_tx", "no_trigger_leg")


class Refused(Exception):
    pass


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def admit(lock_path, store_prefix=None, engine_prefix=None):
    """(lock, engine_dir): the lock's keys, store sha256, run configs and engine snapshot checked."""
    lock = json.load(open(lock_path))
    missing = [k for k in LOCK_KEYS if k not in lock]
    if missing:
        raise Refused(f"clean.lock lacks {missing}")
    if store_prefix and not lock["store_sha256"].startswith(store_prefix):
        raise Refused(f"clean.lock's store_sha256 {lock['store_sha256'][:8]} is not {store_prefix}")
    if engine_prefix and not str(lock["engine_commit"]).startswith(engine_prefix):
        raise Refused(f"clean.lock's engine_commit {lock['engine_commit']} is not {engine_prefix}")
    if sha256(lock["store"]) != lock["store_sha256"]:
        raise Refused("the store's sha256 is not the locked one")
    for run, key in (("test_run", "test_config_sha256"), ("matrix_run", "matrix_config_sha256")):
        if sha256(os.path.join(lock[run], "config.json")) != lock[key]:
            raise Refused(f"the {run}'s config.json is not the locked one")
    engine_dir = os.path.realpath(os.path.join(lock["test_run"], "engine"))
    commit = open(os.path.join(engine_dir, "COMMIT")).read().strip()
    if not lock["engine_commit"] or not commit.startswith(str(lock["engine_commit"])[:7]):
        raise Refused(f"the test run's engine snapshot {commit[:7]} is not the locked {lock['engine_commit']}")
    return lock, engine_dir


def load_engine(engine_dir):
    """clean_test and wash from the engine snapshot (never this checkout's backtest)."""
    if "backtest" in sys.modules:
        raise Refused("backtest is imported already: run this file with python -P, outside a package")
    sys.path.insert(0, engine_dir)
    from backtest import clean_test, wash
    for m in (clean_test, wash):
        if not os.path.realpath(m.__file__).startswith(engine_dir + os.sep):
            raise Refused(f"{m.__name__} comes from {m.__file__}, not the snapshot {engine_dir}")
    return clean_test, wash


def extract(a, log=print):
    if os.path.exists(a.out) or os.path.exists(a.out + ".tmp"):
        raise Refused(f"{a.out} (or .tmp) exists: an extract is written once")
    if sha256(a.exclusions) != a.exclusions_sha256:
        raise Refused(f"the exclusion list's sha256 is not {a.exclusions_sha256}")
    lock, engine_dir = admit(a.lock, a.store_sha256, a.engine)
    ct, wash = load_engine(engine_dir)
    log(f"lock ok: store {lock['store_sha256'][:12]}, engine {lock['engine_commit']} (snapshot {engine_dir})")
    con = duckdb.connect(config={"memory_limit": os.environ.get("LAB_DUCKDB_MEM", "2GB"), "threads": 2})
    if os.environ.get("LAB_SPILL_DIR"):
        con.execute(f"SET temp_directory='{os.environ['LAB_SPILL_DIR']}'")
    con.execute(f"ATTACH '{lock['store']}' AS s (READ_ONLY)")
    meta = json.loads(con.execute("SELECT json FROM s.meta").fetchone()[0])
    if a.dry_run and (meta.get("period") == "clean" or meta.get("slot_end_exclusive", 0) > ct.CLEAN[0]):
        raise Refused("a dry run never reads the clean data")
    # clean_test.run_test's steps 2, 4 and 6, with its own functions and SQL.
    test_cfg = json.load(open(os.path.join(lock["test_run"], "config.json")))
    matrix_cfg = json.load(open(os.path.join(lock["matrix_run"], "config.json")))
    w = meta.get("window") or [meta["slot_start"], meta["slot_end_exclusive"]]
    lo, hi = ct.check_runs(test_cfg, matrix_cfg, meta, ct.epoch_delays(con, w[0], w[1]), a.dry_run, a.engine)
    tg = os.path.join(lock["test_run"], "parts", "*.parquet")
    mg = os.path.join(lock["matrix_run"], "parts", "*.parquet")
    mints, pools, _ = ct.resolve_exclusions(con, ct.parse_exclusions(open(a.exclusions).read()))
    con.execute("CREATE TEMP TABLE xm AS SELECT unnest(?::VARCHAR[]) AS mint", [sorted(mints)])
    con.execute("CREATE TEMP TABLE xp AS SELECT unnest(?::VARCHAR[]) AS pool", [sorted(pools)])
    keep = "NOT (coalesce(mint IN (SELECT mint FROM xm), false) OR coalesce(pool IN (SELECT pool FROM xp), false))"
    win = f"t >= {lo} AND t < {hi}" if lo is not None else "true"
    pnl, ret = ct.settle_sql(ct.Scenario.from_cfg("base", ct.load_defaults()))
    con.execute(f"""CREATE TEMP TABLE k3all AS SELECT pool, t, mint, day, entry_failed, cost, proceeds, entry_slot, exit_slot,
                      trigger_trader, {ret} AS ret, {pnl} / 1e9 AS net_sol
                    FROM read_parquet('{tg}', union_by_name = true) WHERE {ct.K3} AND {win}""")
    con.execute(f"CREATE TEMP TABLE k3 AS SELECT * FROM k3all WHERE {keep}")
    con.execute(f"""CREATE TEMP TABLE trig AS SELECT DISTINCT pool, t, trigger_trader FROM read_parquet('{tg}', union_by_name = true) WHERE {ct.F7REV} AND {win}
                    UNION SELECT DISTINCT pool, t, trigger_trader FROM read_parquet('{mg}', union_by_name = true) WHERE {ct.F7REV} AND {win}""")
    wash.flags(con, "trig", log=log)
    con.execute(f"""CREATE TEMP TABLE rows AS
      SELECT k3.pool, k3.t, k3.mint, k3.day, k3.entry_failed, NOT k3.entry_failed AND k3.ret IS NOT NULL AS filled,
             k3.cost, k3.proceeds, k3.entry_slot, k3.exit_slot, k3.trigger_trader, k3.ret, k3.net_sol, f_rt.rt_tx,
             coalesce(f_w.wash_share, 0) >= {wash.WASH_SHARE} AS wash_1h
      FROM k3 LEFT JOIN f_rt USING (pool, t, trigger_trader) LEFT JOIN f_w USING (pool, t)""")
    n_all = con.execute("SELECT count(*) FROM k3all").fetchone()[0]
    n, failed, filled, rt, no_rt, no_leg, dup = con.execute("""
      SELECT count(*), count(*) FILTER (WHERE entry_failed), count(*) FILTER (WHERE filled),
             count(*) FILTER (WHERE filled AND rt_tx), count(*) FILTER (WHERE filled AND NOT rt_tx),
             count(*) FILTER (WHERE filled AND rt_tx IS NULL), count(*) - (SELECT count(*) FROM k3) FROM rows""").fetchone()
    counts = {"k3": n_all, "excluded_positions": n_all - n, "failed_entries": failed, "filled": filled, "rt_tx": rt,
              "no_rt_tx": no_rt, "no_trigger_leg": no_leg}
    log(f"rows {n}: {counts}")
    bad = []
    if dup:
        bad.append(f"the flag join changed the row count by {dup}")
    if a.result:
        res = json.load(open(a.result))
        held = res.get("positions", {})
        bad += [f"{k}: {counts[k]} != result.json {held.get(k)}" for k in POSITION_KEYS if counts[k] != held.get(k)]
        mean = lambda cond: con.execute(f"SELECT sum(ret) / count(*), count(*) FROM rows WHERE filled AND {cond}").fetchone()  # noqa: E731
        values = [("failed_entry_pnl_sol", con.execute("SELECT coalesce(sum(net_sol), 0) FROM rows WHERE entry_failed").fetchone()[0],
                   held.get("failed_entry_pnl_sol")),
                  ("H1 mean", mean("NOT rt_tx")[0], res.get("H1", {}).get("mean")),
                  ("H2 with_rt_tx mean", mean("rt_tx")[0], res.get("H2", {}).get("with_rt_tx", {}).get("mean"))]
        for name, cond in (("wash_1h", "wash_1h"), ("geen wash_1h", "NOT wash_1h"), ("geen rt_tx en geen wash_1h", "NOT rt_tx AND NOT wash_1h")):
            m, k = mean(cond)
            held_w = res.get("wash_1h", {}).get(name, {})
            values += [(f"wash_1h[{name}] mean", m, held_w.get("mean")), (f"wash_1h[{name}] n", k, held_w.get("n"))]
        for name, got, want in values:  # an empty group: None in both (clean_test's boot_mean)
            if (got is None) != (want is None) or (got is not None and abs(got - want) > 1e-12 + 1e-9 * abs(want)):
                bad.append(f"{name}: {got!r} != result.json {want!r}")
        log(f"result.json agrees: {', '.join(n for n, _, _ in values)}" if not bad else "result.json disagrees")
    if a.expect:
        want = dict(zip(("filled", "rt_tx", "no_rt_tx"), (int(x) for x in a.expect.split(","))))
        bad += [f"{k}: {counts[k]} != expected {v}" for k, v in want.items() if counts[k] != v]
    if a.expect_failed is not None and failed != a.expect_failed:
        bad.append(f"failed_entries: {failed} != expected {a.expect_failed}")
    if bad:
        raise Refused("the extract does not reproduce the test: " + "; ".join(bad))
    tmp = a.out + ".tmp"
    os.makedirs(tmp)
    con.execute(f"COPY (SELECT * FROM rows ORDER BY t, pool, trigger_trader) TO '{os.path.join(tmp, 'k3-rows.parquet')}' (FORMAT parquet)")
    info = {"counts": counts, "rows": n, "wash_1h_filled": con.execute("SELECT count(*) FROM rows WHERE filled AND wash_1h").fetchone()[0],
            "window": [lo, hi], "exclusions": {"sha256": a.exclusions_sha256, "mints": len(mints), "pools": len(pools)},
            "lock": {k: lock[k] for k in LOCK_KEYS}, "engine_snapshot": engine_dir, "result_json": a.result,
            "result_json_sha256": sha256(a.result) if a.result else None, "dry_run": bool(a.dry_run)}
    with open(os.path.join(tmp, "counts.json"), "w") as f:
        json.dump(info, f, indent=1, default=str)
    with open(os.path.join(tmp, "SHA256SUMS"), "w") as f:
        for name in ("k3-rows.parquet", "counts.json"):
            f.write(f"{sha256(os.path.join(tmp, name))}  {name}\n")
    con.close()
    os.rename(tmp, a.out)
    log(f"written {a.out}")
    return info


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--lock", required=True, help="clean.lock next to the clean store")
    ap.add_argument("--exclusions", required=True)
    ap.add_argument("--exclusions-sha256", required=True)
    ap.add_argument("--result", help="the test's result/result.json (read only): every position count must agree")
    ap.add_argument("--out", required=True)
    ap.add_argument("--store-sha256", help="prefix the lock's store_sha256 must start with")
    ap.add_argument("--engine", help="prefix the lock's engine_commit must start with (also clean_test's run check)")
    ap.add_argument("--expect", help="FILLED,RT_TX,NO_RT_TX the filled trades must reproduce")
    ap.add_argument("--expect-failed", type=int)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if not a.dry_run and not (a.result and a.expect and a.expect_failed is not None and a.store_sha256 and a.engine):
        raise SystemExit("pass --result, --expect, --expect-failed, --store-sha256 and --engine")
    try:
        extract(a)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
