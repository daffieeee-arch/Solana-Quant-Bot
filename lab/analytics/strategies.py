"""Load a finished development tournament run into analytics.duckdb (used by build.py --tournament).

Tables:
  strategy_runs     one row: run id, config, store window
  strategy_results  one row per position per cost scenario, settled exactly like lab/backtest/costs.py
  strategy_summary  the run's own summary.parquet (n, mean, CI, edge vs N2 per family x variant x size x d x tau x scenario)
  strategy_curves   cumulative P&L per group in exit order (the equity curve), N2 shadows included

Refuses runs that are unfinished (no summary.parquet) or whose store is not development-only, and checks
that the re-settled totals equal the run's own summary to the lamport.
"""
import json
import os
import re

HOLDOUT_START = 452_304_000
DEFAULTS = "/home/chupa/Solana-project/data-old-faithful-one/lab/research/week2-defaults.yaml"
GROUP = ["family", "variant", "size_sol", "d", "tau", "scenario"]


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


def load(con, run_dir, defaults=DEFAULTS):
    results = os.path.join(run_dir, "results.parquet")
    summary = os.path.join(run_dir, "summary.parquet")
    config = os.path.join(run_dir, "config.json")
    for p in (results, summary, config):
        if not os.path.exists(p):
            raise SystemExit(f"tournament run not finished: {p} missing")
    with open(config) as f:
        conf = json.load(f)
    store = conf.get("store", {})
    if store.get("period") != "dev" or store.get("slot_end_exclusive", HOLDOUT_START + 1) > HOLDOUT_START:
        raise SystemExit(f"refusing a tournament run that is not development-only: {store}")
    cfg = read_costs(defaults)

    con.execute("CREATE TABLE strategy_runs AS SELECT ? AS run_id, ? AS config_json, ?::UBIGINT AS store_first_slot, "
                "?::UBIGINT AS store_last_slot",
                [conf.get("run_id", os.path.basename(run_dir)), json.dumps(conf), store["slot_start"],
                 store["slot_end_exclusive"] - 1])
    con.execute("CREATE TEMP TABLE scn (scenario VARCHAR, per_tx BIGINT, failed_tx BIGINT, rent BIGINT, "
                "rent_mode VARCHAR, adverse DOUBLE)")
    con.executemany("INSERT INTO scn VALUES (?, ?, ?, ?, ?, ?)", scenarios(cfg))
    # Same arithmetic as costs.settle (Python int() truncates toward zero).
    con.execute(f"""
        CREATE TABLE strategy_results AS
        WITH r AS (SELECT * FROM read_parquet('{results}') WHERE NOT coalesce(skipped, false)),
             s AS (
          SELECT r.family, r.variant, r.size_sol, r.d, r.tau, scn.scenario, r.mint, r.venue, r.day,
                 r.t AS signal_slot, r.entry_slot, r.exit_slot, r.exit_reason, r.entry_failed,
                 r.cf_graduation, r.hist_ret,
                 CASE WHEN r.entry_failed THEN NULL
                      ELSE trunc(coalesce(r.cost, 0) * (1 + scn.adverse))::BIGINT + scn.per_tx + scn.rent END AS cost_total,
                 CASE WHEN r.entry_failed THEN NULL
                      ELSE trunc(coalesce(r.proceeds, 0) * (1 - scn.adverse))::BIGINT - scn.per_tx
                           + CASE WHEN scn.rent_mode IN ('refunded', 'refunded_on_full_exit') THEN scn.rent ELSE 0 END
                           - greatest(0, coalesce(r.exit_attempts, 1) - 1) * scn.failed_tx END AS net,
                 scn.failed_tx
          FROM r CROSS JOIN scn)
        SELECT family, variant, size_sol, d, tau, scenario, mint, venue, day, signal_slot, entry_slot, exit_slot,
               exit_reason, entry_failed, cf_graduation, hist_ret,
               CASE WHEN entry_failed THEN -failed_tx ELSE net - cost_total END / 1e9 AS pnl_sol,
               CASE WHEN entry_failed THEN NULL ELSE net / cost_total - 1 END AS ret
        FROM s""")
    con.execute(f"CREATE TABLE strategy_summary AS SELECT * FROM read_parquet('{summary}')")
    con.execute(f"""
        CREATE TABLE strategy_curves AS
        SELECT {", ".join(GROUP)},
               row_number() OVER w AS step,
               coalesce(exit_slot, entry_slot, signal_slot) AS slot,
               pnl_sol,
               sum(pnl_sol) OVER w AS cum_pnl_sol
        FROM strategy_results
        WINDOW w AS (PARTITION BY {", ".join(GROUP)}
                     ORDER BY coalesce(exit_slot, entry_slot, signal_slot), signal_slot, mint
                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)""")
    bad = con.execute(f"""
        SELECT count(*) FROM (
          SELECT {", ".join(GROUP)}, sum(pnl_sol) AS mine FROM strategy_results GROUP BY ALL) a
        JOIN strategy_summary b USING ({", ".join(GROUP)})
        WHERE abs(a.mine - b.total_pnl_sol) > 1e-6""").fetchone()[0]
    missing = con.execute(f"""
        SELECT count(*) FROM strategy_summary b
        ANTI JOIN (SELECT DISTINCT {", ".join(GROUP)} FROM strategy_results) a USING ({", ".join(GROUP)})
        WHERE b.filled > 0""").fetchone()[0]
    if bad or missing:
        raise SystemExit(f"re-settled P&L differs from the run's summary: {bad} groups differ, {missing} missing")
    return conf
