"""Load a finished development tournament run into analytics.duckdb (used by build.py --tournament).

Tables:
  strategy_runs     one row: run id, config, store window, gate sizes (from the run's report.md)
  strategy_results  one row per position (base costs), settled exactly like lab/backtest/costs.py
  strategy_summary  the run's own summary.parquet (n, mean, CI, edge vs N2 per family x variant x size x d x tau x scenario)
  strategy_curves   cumulative P&L per group and scenario in exit order, thinned to ~400 points (N2 shadows and
                    farm 'all' roll-ups included)

Positions are settled one family x variant at a time from a hive-partitioned copy in stage_dir (build.py passes
its temp dir), so a run of ~80M positions fits the heavy-job memory limit.

Refuses runs that are unfinished (no summary.parquet) or whose store is not development-only, and checks
that the re-settled totals equal the run's own summary to the lamport.
"""
import glob
import json
import os
import re
import shutil
import tempfile

HOLDOUT_START = 452_304_000
DEFAULTS = "/home/chupa/Solana-project/data-old-faithful-one/lab/research/week2-defaults.yaml"
# Summary keys. From run v2 on also q, segment, regime, farm and overlay; keys a run lacks are skipped.
# farm = 'all' in summary.parquet is organic + flagged together (S1/F6): checked as a roll-up, never summed
# with them. overlay O7* rows are paired variants of the same triggers: separate groups, never added to 'none'.
GROUP = ["family", "variant", "size_sol", "d", "tau", "q", "segment", "regime", "farm", "overlay", "scenario"]
ROLLUP = {"farm": "all"}
CURVE_POINTS = 400


def read_costs(path):
    """The `costs` block of week2-defaults.yaml (flow-style scenario maps), without a YAML dependency.
    Any misread shows up in load(): re-settled totals must equal the run's summary."""
    text = open(path).read()
    block = text[text.index("\ncosts:"):]
    rent = int(re.search(r"ata_rent_lamports:\s*(\d+)", block).group(1))
    scen = {}
    for name, body in re.findall(r"^\s{4}(\w+):\s*\{([^}]*)\}", block, re.M):
        scen[name] = {k.strip(): v.strip() for k, v in (kv.split(":", 1) for kv in body.split(","))}
    if set(scen) != {"optimistic", "base", "pessimistic"}:
        raise SystemExit(f"unexpected cost scenarios in {path}: {sorted(scen)}")
    return {"costs": {"ata_rent_lamports": rent, "scenarios": scen}}


def scenarios(cfg):
    out = []
    rent = int(cfg["costs"]["ata_rent_lamports"])
    for name, s in cfg["costs"]["scenarios"].items():
        lam = lambda x: int(round(float(x) * 1_000_000_000))  # noqa: E731
        land = float(s["land_prob"])
        failed = lam(s["failed_tx_sol"])
        per_tx = lam(s["fee_per_tx_sol"]) + lam(s["tip_sol"]) + int((1 / land - 1) * failed)
        out.append((name, per_tx, failed, rent, s["ata_rent"], float(s["adverse_exec_per_side"])))
    return out


def gate_sizes(report, sizes):
    """The sizes the run's verdict is judged at, from the fixed sentence in its report.md (None if it has none).
    v2 reports: "Gate sizes: 0.5, 1.0 SOL (...)"; v1 reports: "Sizes below 5 SOL are informative only"."""
    m = re.search(r"Gate sizes: ([0-9][0-9., ]*?) SOL", report)
    if m:
        return [float(x) for x in m.group(1).split(",")]
    m = re.search(r"Sizes below ([0-9.]+) SOL are informative only", report)
    if m:
        return [s for s in sizes if s >= float(m.group(1))]
    return None


def load(con, run_dir, defaults=DEFAULTS, stage_dir=None):
    # Positions: results.parquet if the run wrote one, else its per-batch parts (same rows, no
    # pnl_base_lamports/ret_base). summary.parquet is written last, so it marks a finished run;
    # every scenario's group totals are checked against it below, which also catches missing or
    # duplicated parts. Nothing here depends on file order: curves sort explicitly.
    # A large run (families-v2: ~80M positions, 83% in N2_F7) is settled one family x variant at a time:
    # one pass copies the needed columns into hive partitions under stage_dir, then each chunk reads only
    # its own partition. Every group key includes family and variant, so no group spans two chunks.
    results = os.path.join(run_dir, "results.parquet")
    if not os.path.exists(results):
        results = os.path.join(run_dir, "parts", "*.parquet")
        if not glob.glob(results):
            raise SystemExit(f"tournament run has no positions: {run_dir}/results.parquet or parts/ missing")
    summary = os.path.join(run_dir, "summary.parquet")
    config = os.path.join(run_dir, "config.json")
    for p in (summary, config):
        if not os.path.exists(p):
            raise SystemExit(f"tournament run not finished: {p} missing")
    with open(config) as f:
        conf = json.load(f)
    store = conf.get("store", {})
    if store.get("period") != "dev" or store.get("slot_end_exclusive", HOLDOUT_START + 1) > HOLDOUT_START:
        raise SystemExit(f"refusing a tournament run that is not development-only: {store}")
    cfg = read_costs(defaults)

    # Gate sizes: config.json's own field (runs after families-v2), else the fixed sentence in report.md.
    report = os.path.join(run_dir, "report.md")
    gates = conf.get("gate_sizes")
    if gates is None and os.path.exists(report):
        with open(report) as f:
            gates = gate_sizes(f.read(), conf.get("sizes_sol") or [])
    con.execute("CREATE TABLE strategy_runs AS SELECT ? AS run_id, ? AS config_json, ?::UBIGINT AS store_first_slot, "
                "?::UBIGINT AS store_last_slot, ?::DOUBLE[] AS gate_sizes",
                [conf.get("run_id", os.path.basename(run_dir)), json.dumps(conf), store["slot_start"],
                 store["slot_end_exclusive"] - 1, gates])
    res_cols = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{results}')").fetchall()}
    sum_types = {r[0]: r[1] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{summary}')").fetchall()}
    sum_cols = set(sum_types)
    keys = [k for k in GROUP if k in sum_cols and (k == "scenario" or k in res_cols)]
    pos_keys = [k for k in keys if k != "scenario"]
    con.execute(f"CREATE TABLE strategy_summary AS SELECT * FROM read_parquet('{summary}')")
    con.execute(f"""CREATE TABLE strategy_curves ({", ".join(f'"{k}" {sum_types[k]}' for k in keys)},
                    step BIGINT, n BIGINT, slot UBIGINT, cum_pnl_sol DOUBLE)""")
    k_sql = ", ".join(pos_keys)
    has_base = "pnl_base_lamports" in res_cols
    need = [*pos_keys, "mint", "venue", "day", "t", "entry_slot", "exit_slot", "exit_reason", "entry_failed",
            "cf_graduation", "hist_ret", "proceeds", "cost", "exit_attempts"] + (["pnl_base_lamports"] if has_base else [])
    sel = ", ".join(c if c in res_cols else f"NULL AS {c}" for c in need)
    if stage_dir:
        os.makedirs(stage_dir, exist_ok=True)
    stage = tempfile.mkdtemp(prefix="strategy-stage-", dir=stage_dir)
    try:
        con.execute(f"""COPY (SELECT {sel} FROM read_parquet('{results}') WHERE NOT coalesce(skipped, false))
                        TO '{stage}/pos' (FORMAT parquet, PARTITION_BY (family, variant), OVERWRITE_OR_IGNORE)""")
        if not glob.glob(os.path.join(stage, "pos", "*", "*", "*.parquet")):
            raise SystemExit(f"tournament run has no positions that were not skipped: {run_dir}")
        staged = f"read_parquet('{stage}/pos/*/*/*.parquet', hive_partitioning = true, hive_types_autocast = false)"
        chunks = con.execute("SELECT DISTINCT family, variant FROM strategy_summary ORDER BY ALL").fetchall()
        extra = con.execute(f"SELECT DISTINCT family, variant FROM {staged} EXCEPT "
                            "SELECT DISTINCT family, variant FROM strategy_summary").fetchall()
        if extra:
            raise SystemExit(f"positions for family x variant missing from summary.parquet: {extra[:5]}")
        for fam, var in chunks:
            where = f"family = {sql_str(fam)} AND variant = {sql_str(var)}"
            con.execute(f"CREATE OR REPLACE TEMP TABLE pos AS SELECT * FROM {staged} WHERE {where}")
            settle_chunk(con, cfg, pos_keys, k_sql, where, has_base)
        con.execute("DROP TABLE IF EXISTS pos")
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    on_s = " AND ".join(f"c.{k} IS NOT DISTINCT FROM s.{k}" for k in pos_keys)
    for name, *_ in scenarios(cfg):
        off = con.execute(f"""
            SELECT count(*) FROM strategy_summary s
            LEFT JOIN (SELECT * FROM strategy_curves WHERE step = n AND scenario = ?) c ON {on_s}
            WHERE s.scenario = ? AND s.n > 0
              AND (c.n IS DISTINCT FROM s.n OR abs(c.cum_pnl_sol - s.total_pnl_sol) > 1e-6
                   OR c.cum_pnl_sol IS NULL)""", [name, name]).fetchone()[0]
        if off:
            raise SystemExit(f"scenario {name}: {off} summary groups have no curve ending at their n and total")
    return conf


def sql_str(v):
    return "NULL" if v is None else "'" + str(v).replace("'", "''") + "'"


def settle_chunk(con, cfg, pos_keys, k_sql, where, has_base):
    """Settle temp table `pos` (one family x variant) under every cost scenario, check its group totals
    against strategy_summary, and append its curves (and, for base, its positions to strategy_results)."""
    for name, per_tx, failed, rent, rent_mode, adverse in scenarios(cfg):
        # Every exit is full: the remainder is burned and the account closed (backtest/costs.py exit_net's doc).
        refund = rent if rent_mode in ("refunded", "refunded_on_full_exit") else 0
        # Same arithmetic as costs.settle_sql: the factors are Python floats, so multiply as DOUBLE (a bare
        # 0.995 literal is DECIMAL in DuckDB and truncates differently by one lamport).
        up, down = f"{1 + adverse!r}::DOUBLE", f"{1 - adverse!r}::DOUBLE"
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE settled AS
            SELECT {k_sql}, mint, venue, day, t AS signal_slot, entry_slot, exit_slot, exit_reason, entry_failed,
                   cf_graduation, hist_ret,
                   {"pnl_base_lamports," if has_base else ""}
                   CASE WHEN entry_failed THEN -{failed}
                        ELSE (trunc(coalesce(proceeds, 0)::DOUBLE * {down})::BIGINT - {per_tx} + {refund}
                              - greatest(0, coalesce(exit_attempts, 1) - 1) * {failed})
                             - (trunc(coalesce(cost, 0)::DOUBLE * {up})::BIGINT + {per_tx} + {rent}) END AS pnl,
                   CASE WHEN entry_failed THEN NULL
                        ELSE (trunc(coalesce(proceeds, 0)::DOUBLE * {down})::BIGINT - {per_tx} + {refund}
                              - greatest(0, coalesce(exit_attempts, 1) - 1) * {failed})
                             / (trunc(coalesce(cost, 0)::DOUBLE * {up})::BIGINT + {per_tx} + {rent}) - 1 END AS ret
            FROM pos""")
        if name == "base" and has_base:
            off = con.execute("SELECT count(*) FROM settled WHERE pnl IS DISTINCT FROM pnl_base_lamports").fetchone()[0]
            if off:
                raise SystemExit(f"{off} positions differ from the run's own pnl_base_lamports ({where})")
        # Group totals per key, plus roll-ups (farm = 'all'); keys may be NULL, so match IS NOT DISTINCT FROM.
        mine = [f"SELECT {k_sql}, sum(pnl) AS pnl FROM settled GROUP BY ALL"]
        for rk, rv in ROLLUP.items():
            if rk in pos_keys:
                cols = ", ".join(f"'{rv}' AS {k}" if k == rk else k for k in pos_keys)
                mine.append(f"SELECT {cols}, sum(pnl) FROM settled GROUP BY ALL")
        on = " AND ".join(f"a.{k} IS NOT DISTINCT FROM b.{k}" for k in pos_keys)
        bad, missing = con.execute(f"""
            WITH a AS ({" UNION ALL ".join(mine)}),
                 b AS (SELECT * FROM strategy_summary WHERE scenario = ? AND {where})
            SELECT (SELECT count(*) FROM a JOIN b ON {on} WHERE abs(a.pnl / 1e9 - b.total_pnl_sol) > 1e-6),
                   (SELECT count(*) FROM b ANTI JOIN a ON {on} WHERE b.filled > 0)""", [name]).fetchone()
        if bad or missing:
            raise SystemExit(f"scenario {name}: re-settled P&L differs from summary.parquet "
                             f"({bad} groups differ, {missing} missing; {where})")
        # Curves per group, and per roll-up group the summary has (farm 'all': all positions of the other keys).
        sources = [("settled", "")]
        for rk, rv in ROLLUP.items():
            if rk in pos_keys:
                sources.append((f"(SELECT * REPLACE ('{rv}' AS {rk}) FROM settled)",
                                f"SEMI JOIN (SELECT {k_sql} FROM strategy_summary WHERE {rk} = '{rv}' AND scenario = '{name}') b "
                                f"ON {' AND '.join(f's.{k} IS NOT DISTINCT FROM b.{k}' for k in pos_keys)}"))
        for src, only in sources:
            con.execute(f"""
                INSERT INTO strategy_curves BY NAME
                SELECT * EXCLUDE (pnl) FROM (
                  SELECT {k_sql}, '{name}' AS scenario,
                         row_number() OVER w AS step, count(*) OVER (PARTITION BY {k_sql}) AS n,
                         coalesce(exit_slot, entry_slot, signal_slot)::UBIGINT AS slot, pnl,
                         sum(pnl) OVER w / 1e9 AS cum_pnl_sol
                  FROM {src} s {only}
                  WINDOW w AS (PARTITION BY {k_sql} ORDER BY coalesce(exit_slot, entry_slot, signal_slot), signal_slot, mint
                               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW))
                WHERE step = 1 OR step = n OR step % greatest(1, n // {CURVE_POINTS}) = 0""")
        if name == "base":
            exists = con.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name = 'strategy_results' "
                                 "AND NOT temporary AND database_name = current_database()").fetchone()[0]
            con.execute(f"""{"INSERT INTO strategy_results" if exists else "CREATE TABLE strategy_results AS"}
                            SELECT {k_sql}, mint, venue, day, signal_slot::UBIGINT AS signal_slot,
                                   entry_slot::UBIGINT AS entry_slot, exit_slot::UBIGINT AS exit_slot, exit_reason,
                                   entry_failed, cf_graduation, hist_ret, pnl / 1e9 AS pnl_sol, ret
                            FROM settled""")
        con.execute("DROP TABLE settled")
