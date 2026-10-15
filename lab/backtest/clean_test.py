"""The clean-data test of the wash-trigger hypothesis (research/hypothesis-wash-trigger-2026-10-15.md,
sha256 35bdaa0d) on epochs 1055-1063: K3 without and with the triggers whose trigger leg is a
same-transaction round trip (rt_tx, backtest.wash).

  python -m backtest.clean_test --run DIR --store clean.duckdb --out DIR --trials N \\
      --hypothesis FILE --addendum FILE --addendum-sha256 SHA --exclusions FILE --exclusions-sha256 SHA

Order, fail-closed (any failure exits before the next step):
  1. the documents: the hypothesis's sha256 is 35bdaa0d..., the addendum's and the exclusion
     list's are the ones passed (each also equal to its .sha256 file when that exists);
  2. the run: K3 (F7 pool_reversed, tp4_T600, 0.5 SOL, q 0.5, overlay none, tau empirical,
     tolerance 0.05), d_ms 600, on a store of period 'clean' whose pre-registration hash is the
     addendum's, decisions only in the store's window; the window spans >= MIN_DAYS UTC days;
  3. the exclusions: every rule of the list resolved against the store (mints and pools, both
     ways) and written to exclusions.json; positions of those mints and pools are dropped;
  4. only then the outcomes: the flags, H1, H2, the decision, wash_1h (descriptive) and the DSR.

H1: mean net return per filled K3 trade without rt_tx > 0 (lower bound of the 95 % interval of a
bootstrap over UTC trading days, DRAWS draws). H2: mean without rt_tx - mean with rt_tx > 0 (both
groups drawn together per day). "bevestigd" when H1 and H2 hold, "risicofilter zonder bewezen
winst" when only H2 holds, else "verworpen". Groups as in the audit (a4c): rt_tx true / false;
filled trades without a trigger leg (rt_tx NULL) are in neither group and are counted.
DSR: the deflated Sharpe ratio of the daily net P&L without rt_tx against the best of --trials
skill-less trials (wallets.overfit, null variance 1/T). PBO needs at least two trials on the same
data; one pre-registered trial has none, so it is reported as not applicable.

--dry-run: on any store and run (no period, window or addendum checks), the output marked as a dry
run; for testing the job, never for the test itself.
"""

import argparse
import hashlib
import json
import os
import re
import sys

import duckdb
import numpy as np

from . import wash
from .costs import Scenario, settle_sql
from .store import load_defaults

HYPOTHESIS_SHA = "35bdaa0d3f5ed9a36e2af0582810412d29d7d35b217d50d1dc45fd4acb083a28"
K3 = {"families": ["F7"], "segments": ["pool_reversed"], "variants": ["tp4_T600"], "sizes_sol": [0.5], "q": [0.5],
      "tau": ["empirical"], "slippage_tol": 0.05, "delay_ms": 600}
MIN_DAYS = 5
DRAWS = 10_000
SEED = 7
DAY_MS = 86_400_000
B58 = re.compile(r"`([1-9A-HJ-NP-Za-km-z]{32,44})`")


class Refused(SystemExit):
    pass


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def check_document(path, expected, name):
    """The document's sha256 when it equals the expected one and its .sha256 file (when present)."""
    if not path or not os.path.exists(path):
        raise Refused(f"{name}: {path} does not exist")
    got = sha256(path)
    if not expected or len(expected) < 16 or not got.startswith(expected.lower()):
        raise Refused(f"{name}: sha256 {got} is not the expected {expected}")
    side = os.path.splitext(path)[0] + ".sha256"
    if os.path.exists(side):
        recorded = open(side).read().split()[0]
        if recorded != got:
            raise Refused(f"{name}: sha256 {got} differs from {side} ({recorded})")
    return got


# ------------------------------------------------------------------------------------- exclusions

def parse_exclusions(text):
    """The rules of the exclusion list's table: per row the backticked addresses (a mint or a pool),
    the other backticked words (symbols) and the quoted names. A row with none of them is refused."""
    rules = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.lstrip().startswith("|") else []
        if len(cells) < 3 or set(cells[0]) <= set("-: ") or cells[0].lower() == "datum":
            continue
        addresses = B58.findall(cells[1])
        symbols = [w for w in re.findall(r"`([^`]+)`", cells[1]) if w not in addresses]
        names = re.findall(r'"([^"]+)"', cells[1])
        if not (addresses or symbols or names):
            raise Refused(f"exclusion rule without an address, symbol or name: {line}")
        rules.append({"date": cells[0], "addresses": addresses, "symbols": symbols, "names": names, "label": cells[2]})
    if not rules:
        raise Refused("the exclusion list has no rules")
    return rules


def resolve_exclusions(con, rules, store="s"):
    """(mints, pools, per-rule resolution): an address is a mint or a pool; a mint brings its pools and a
    pool its mint; symbols and names match the store's mints (case-insensitive, any creation slot)."""
    mints, pools, per_rule = set(), set(), []
    for r in rules:
        m, p = set(), set()
        for a in r["addresses"]:
            if con.execute(f"SELECT 1 FROM {store}.mints WHERE mint = ?", [a]).fetchone():
                m.add(a)
            if con.execute(f"SELECT 1 FROM {store}.pools WHERE pool = ?", [a]).fetchone():
                p.add(a)
            if a not in m and a not in p:
                m.add(a)  # not in the store: kept as a mint (a position's own mint column still matches)
        for sym in r["symbols"]:
            m |= {x for (x,) in con.execute(f"SELECT mint FROM {store}.mints WHERE upper(trim(symbol)) = upper(?)", [sym]).fetchall()}
        for name in r["names"]:
            m |= {x for (x,) in con.execute(f"SELECT mint FROM {store}.mints WHERE lower(trim(name)) = lower(?)", [name]).fetchall()}
        m |= {x for (x,) in con.execute(f"SELECT DISTINCT mint FROM {store}.pools WHERE pool IN (SELECT unnest(?::VARCHAR[])) AND mint IS NOT NULL",
                                        [sorted(p)]).fetchall()}
        p |= {x for (x,) in con.execute(f"SELECT pool FROM {store}.pools WHERE mint IN (SELECT unnest(?::VARCHAR[]))", [sorted(m)]).fetchall()}
        per_rule.append({**r, "mints": sorted(m), "pools": sorted(p)})
        mints |= m
        pools |= p
    return mints, pools, per_rule


# ------------------------------------------------------------------------------------- statistics

def boot_mean(day_rows, draws=DRAWS, seed=SEED):
    """Mean per position with a 95 % interval from resampling whole days: day_rows = [(sum, count)]."""
    a = np.array([r for r in day_rows if r[1] > 0], dtype=float)
    if len(a) == 0:
        return None, None, None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), size=(draws, len(a)))
    s, c = a[idx, 0].sum(1), a[idx, 1].sum(1)
    m = s / np.where(c > 0, c, np.nan)
    return float(a[:, 0].sum() / a[:, 1].sum()), float(np.nanpercentile(m, 2.5)), float(np.nanpercentile(m, 97.5))


def boot_diff(day_rows, draws=DRAWS, seed=SEED):
    """Mean of group 0 - mean of group 1 with a 95 % interval, both groups drawn together per day:
    day_rows = [(sum0, n0, sum1, n1)] over the days with a trade in either group."""
    a = np.array(day_rows, dtype=float)
    if len(a) == 0 or a[:, 1].sum() == 0 or a[:, 3].sum() == 0:
        return None, None, None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), size=(draws, len(a)))
    with np.errstate(invalid="ignore", divide="ignore"):
        d = a[idx, 0].sum(1) / a[idx, 1].sum(1) - a[idx, 2].sum(1) / a[idx, 3].sum(1)
    point = a[:, 0].sum() / a[:, 1].sum() - a[:, 2].sum() / a[:, 3].sum()
    return float(point), float(np.nanpercentile(d, 2.5)), float(np.nanpercentile(d, 97.5))


def decision(h1, h2):
    if h1 and h2:
        return "bevestigd"
    return "risicofilter zonder bewezen winst" if h2 else "verworpen"


def dsr_of(daily, n_trials):
    """PSR against 0 and the DSR against the best of n_trials skill-less trials (null variance 1/T)."""
    from wallets.overfit import expected_max_sharpe, moments, psr

    x = np.asarray(daily, dtype=float)
    t = len(x)
    if t < 2 or x.std(ddof=1) == 0:
        return {"t": t, "sr": None, "psr0": None, "dsr": None, "n_trials": n_trials}
    sr = float(x.mean() / x.std(ddof=1))
    skew, kurt = moments(x)
    sr0 = expected_max_sharpe(n_trials, 1.0 / t)
    return {"t": t, "n_trials": n_trials, "sr": sr, "sr0": sr0, "psr0": psr(sr, 0.0, t, skew, kurt),
            "dsr": psr(sr, sr0, t, skew, kurt), "skew": skew, "kurt": kurt}


# ------------------------------------------------------------------------------------- the test

def check_run(run_dir, store_meta, dry_run):
    """The run's configuration is K3's on this store, decisions in its window: (lo, hi) of the window."""
    cfg = json.load(open(os.path.join(run_dir, "config.json")))
    bad = [f"{k}: {cfg.get(k)!r} != {v!r}" for k, v in K3.items() if cfg.get(k) != v]
    if "O7" in cfg.get("families", []):
        bad.append("overlays in the run")
    window = cfg.get("decide_range")
    if not dry_run:
        if store_meta.get("period") != "clean":
            bad.append(f"store period {store_meta.get('period')!r} != 'clean'")
        lo, hi = store_meta.get("slot_start"), store_meta.get("slot_end_exclusive")
        if not window or window[1] != hi or not lo < window[0] < hi:
            bad.append(f"decide_range {window} is not the store's window (warm-up {lo}, end {hi})")
    if bad:
        raise Refused("the run is not the pre-registered configuration: " + "; ".join(bad))
    return tuple(window) if window else (None, None)


def run_test(a, log=print):
    out = {"dry_run": bool(a.dry_run), "documents": {}}
    # 1. documents
    out["documents"]["hypothesis"] = check_document(a.hypothesis, HYPOTHESIS_SHA, "hypothesis")
    if not a.dry_run:
        out["documents"]["addendum"] = check_document(a.addendum, a.addendum_sha256, "addendum")
    out["documents"]["exclusions"] = check_document(a.exclusions, a.exclusions_sha256, "exclusions")
    con = duckdb.connect(config={"memory_limit": os.environ.get("LAB_DUCKDB_MEM", "2GB"), "threads": 2})
    if os.environ.get("LAB_SPILL_DIR"):
        con.execute(f"SET temp_directory='{os.environ['LAB_SPILL_DIR']}'")
    con.execute(f"ATTACH '{a.store}' AS s (READ_ONLY)")
    meta = json.loads(con.execute("SELECT json FROM s.meta").fetchone()[0])
    if not a.dry_run and meta.get("prereg_sha256") != out["documents"]["addendum"]:
        raise Refused(f"the store was built for pre-registration {meta.get('prereg_sha256')}, not the addendum's")
    # 2. the run and the window
    lo, hi = check_run(a.run, meta, a.dry_run)
    days = con.execute(f"""SELECT (max(block_time) - min(block_time)) / 86400.0, count(DISTINCT CAST(to_timestamp(block_time) AS DATE))
                           FROM s.blocks WHERE {f'slot >= {lo} AND slot < {hi}' if lo is not None else 'true'}""").fetchone()
    out["window"] = {"slot_from": lo, "slot_to": hi, "days": days[0], "utc_dates": days[1], "store": meta.get("period"),
                     "store_commit": meta.get("git_commit")}
    if not a.dry_run and (days[0] or 0) < MIN_DAYS:
        raise Refused(f"the window spans {days[0]:.2f} days < {MIN_DAYS}: the test waits for more epochs")
    # 3. exclusions, before any outcome
    rules = parse_exclusions(open(a.exclusions).read())
    mints, pools, per_rule = resolve_exclusions(con, rules)
    out["exclusions"] = {"rules": per_rule, "mints": sorted(mints), "pools": sorted(pools)}
    os.makedirs(a.out, exist_ok=True)
    json.dump(out, open(os.path.join(a.out, "exclusions.json"), "w"), indent=1)
    log(f"exclusions: {len(rules)} rules, {len(mints)} mints, {len(pools)} pools (exclusions.json)")
    # 4. outcomes
    pnl, ret = settle_sql(Scenario.from_cfg("base", load_defaults()))
    parts = os.path.join(a.run, "parts", "*.parquet")
    win = f"AND t >= {lo} AND t < {hi}" if lo is not None else ""
    con.execute(f"""CREATE TEMP TABLE k3 AS
    SELECT pool, mint, t, trigger_trader, day, entry_failed, {ret} AS ret, {pnl} AS pnl FROM read_parquet('{parts}')
    WHERE family = 'F7' AND segment = 'pool_reversed' AND variant = 'tp4_T600' AND size_sol = 0.5 AND q = 0.5
      AND overlay = 'none' AND NOT skipped {win}""")
    con.execute("CREATE TEMP TABLE xm AS SELECT unnest(?::VARCHAR[]) AS mint", [sorted(mints)])
    con.execute("CREATE TEMP TABLE xp AS SELECT unnest(?::VARCHAR[]) AS pool", [sorted(pools)])
    n_all = con.execute("SELECT count(*) FROM k3").fetchone()[0]
    con.execute("DELETE FROM k3 WHERE mint IN (SELECT mint FROM xm) OR pool IN (SELECT pool FROM xp)")
    n_kept = con.execute("SELECT count(*) FROM k3").fetchone()[0]
    out["positions"] = {"k3": n_all, "excluded": n_all - n_kept}
    wash.flags(con, "SELECT pool, t, trigger_trader FROM k3", log=log)
    con.execute(f"""CREATE TEMP TABLE j AS SELECT k3.*, f_rt.rt_tx, coalesce(f_w.wash_share, 0) >= {wash.WASH_SHARE} AS wash_1h
                    FROM k3 LEFT JOIN f_rt USING (pool, t, trigger_trader) LEFT JOIN f_w USING (pool, t)
                    WHERE NOT entry_failed AND ret IS NOT NULL""")
    filled = con.execute("SELECT count(*), count(*) FILTER (WHERE rt_tx), count(*) FILTER (WHERE NOT rt_tx), count(*) FILTER (WHERE rt_tx IS NULL), count(DISTINCT day) FROM j").fetchone()
    out["positions"].update({"filled": filled[0], "rt_tx": filled[1], "no_rt_tx": filled[2], "no_trigger_leg": filled[3], "trading_days": filled[4]})
    group = lambda cond: con.execute(f"SELECT sum(ret), count(*) FROM j WHERE {cond} GROUP BY day").fetchall()  # noqa: E731
    m, l1, h1_ = boot_mean(group("NOT rt_tx"), a.draws)
    out["H1"] = {"mean": m, "lo": l1, "hi": h1_, "pass": l1 is not None and l1 > 0}
    pairs = con.execute("""SELECT coalesce(sum(ret) FILTER (WHERE NOT rt_tx), 0), count(*) FILTER (WHERE NOT rt_tx),
                                  coalesce(sum(ret) FILTER (WHERE rt_tx), 0), count(*) FILTER (WHERE rt_tx)
                           FROM j WHERE rt_tx IS NOT NULL GROUP BY day""").fetchall()
    d, l2, h2_ = boot_diff(pairs, a.draws)
    m_rt = boot_mean(group("rt_tx"), a.draws)
    out["H2"] = {"diff": d, "lo": l2, "hi": h2_, "pass": l2 is not None and l2 > 0,
                 "with_rt_tx": {"mean": m_rt[0], "lo": m_rt[1], "hi": m_rt[2]}}
    out["decision"] = decision(out["H1"]["pass"], out["H2"]["pass"])
    out["wash_1h"] = {k: dict(zip(("mean", "lo", "hi"), boot_mean(group(c), a.draws))) | {"n": sum(n for _, n in group(c))}
                      for k, c in (("wash_1h", "wash_1h"), ("geen wash_1h", "NOT wash_1h"))}
    # DSR over every UTC date of the window (a date without a trade counts 0)
    daily = dict(con.execute("SELECT day, sum(pnl) FROM j WHERE NOT rt_tx GROUP BY day").fetchall())
    dates = [str(x) for (x,) in con.execute(f"""SELECT DISTINCT CAST(to_timestamp(block_time) AS DATE) FROM s.blocks
                                               WHERE {f'slot >= {lo} AND slot < {hi}' if lo is not None else 'true'} ORDER BY 1""").fetchall()]
    out["DSR"] = dsr_of([float(daily.get(x, daily.get(_date(x), 0.0)) or 0.0) for x in dates], a.trials)
    out["PBO"] = {"pbo": None, "why": "één vooraf vastgelegde trial; CSCV vraagt minstens twee trials op dezelfde data"}
    json.dump(out, open(os.path.join(a.out, "result.json"), "w"), indent=1, default=str)
    with open(os.path.join(a.out, "result.md"), "w") as f:
        f.write(report_md(out))
    log(f"{'DRY RUN: ' if a.dry_run else ''}decision: {out['decision']} (result.json, result.md)")
    return out


def _date(x):
    import datetime

    return datetime.date.fromisoformat(x)


def report_md(o):
    pct = lambda x: "–" if x is None else f"{x * 100:+.2f} %"  # noqa: E731
    p, w = o["positions"], o["window"]
    lines = [
        "# Clean-toets wash-trigger (hypothese 35bdaa0d)" + (" — DRY RUN, geen toets" if o["dry_run"] else ""), "",
        f"Venster: slots [{w['slot_from']}, {w['slot_to']}), {w['days']:.2f} dagen, {w['utc_dates']} UTC-datums; store {w['store']} ({w['store_commit']}).",
        "Documenten (sha256): " + ", ".join(f"{k} {v[:16]}" for k, v in o["documents"].items()) + ".",
        f"Uitsluitingen: {len(o['exclusions']['mints'])} mints, {len(o['exclusions']['pools'])} pools; {p['excluded']} van {p['k3']} K3-posities vervallen.",
        f"Gevulde K3-trades: {p['filled']} op {p['trading_days']} handelsdagen; met rt_tx {p['rt_tx']}, zonder {p['no_rt_tx']}, zonder trigger-leg {p['no_trigger_leg']}.",
        "",
        "| toets | waarde | 95 %-interval (dagbootstrap) | geslaagd |",
        "|---|---|---|---|",
        f"| H1: zonder rt_tx, gemiddeld nettorendement | {pct(o['H1']['mean'])} | {pct(o['H1']['lo'])} … {pct(o['H1']['hi'])} | {'ja' if o['H1']['pass'] else 'nee'} |",
        f"| H2: zonder − met rt_tx | {pct(o['H2']['diff'])} | {pct(o['H2']['lo'])} … {pct(o['H2']['hi'])} | {'ja' if o['H2']['pass'] else 'nee'} |",
        f"| (met rt_tx) | {pct(o['H2']['with_rt_tx']['mean'])} | {pct(o['H2']['with_rt_tx']['lo'])} … {pct(o['H2']['with_rt_tx']['hi'])} | |",
        "", f"**Beslissing: {o['decision']}.**", "",
        "wash_1h (beschrijvend): " + "; ".join(f"{k} n={v['n']} {pct(v['mean'])} [{pct(v['lo'])} … {pct(v['hi'])}]" for k, v in o["wash_1h"].items()) + ".",
        f"DSR (dag-P&L zonder rt_tx, {o['DSR'].get('n_trials')} trials, T={o['DSR'].get('t')}): {o['DSR'].get('dsr')}; PSR tegen 0: {o['DSR'].get('psr0')}.",
        f"PBO: niet van toepassing ({o['PBO']['why']}).", "",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", required=True, help="the tournament run directory (config.json, parts/)")
    ap.add_argument("--store", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--hypothesis", required=True)
    ap.add_argument("--addendum")
    ap.add_argument("--addendum-sha256")
    ap.add_argument("--exclusions", required=True)
    ap.add_argument("--exclusions-sha256", required=True)
    ap.add_argument("--trials", type=int, required=True, help="the experiment register's number of trials (DSR)")
    ap.add_argument("--draws", type=int, default=DRAWS)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not a.dry_run and not (a.addendum and a.addendum_sha256):
        raise SystemExit("pass --addendum and --addendum-sha256 (the hashed pre-registration)")
    run_test(a)


if __name__ == "__main__":
    sys.exit(main())
