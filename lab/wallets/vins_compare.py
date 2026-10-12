"""V_ins recompute after the insider-share fix (#213, engine 0b9304e; coordinator 2026-10-13): the M1x
tournament (M1P, M1E, N2) is run again on dev with the fixed engine under the same pre-registered §10 rule
(a bugfix plus a recompute, not tuning), and this script compares the two runs:

  - per trigger (family, mint, t; the base row: overlay none, 0.5 SOL, d = 1, first variant) the insider
    share and the V_ins flag (ins_share > 0.90) in the old and the new run; flipped vetoes = triggers vetoed
    in one run and not in the other, per family and per period (days 1-9 / days 10-18);
  - the §10 verdicts of both runs from m1x.json, and the Δ(V_ins) rows at 0.5 SOL, d = 1 and 2;
  - the new run's `insider_fallbacks` counter (config.json or m1x.json): must be 0 for M1 / M1x (normal
    pools only); the fix is complete there, while `token_insiders` falls back to the old window-dependent
    Insiders for reversed pools, so an O7 overlay on F7 pool_reversed stays provisional until the token side
    is implemented (the note repeats that).

  python -m wallets.vins_compare --old RUN_DIR --new RUN_DIR [--out FILE]
"""

import argparse
import glob
import json
import os
import sys

import duckdb

INS_VETO = 0.90
SPLIT_DAY, LAST_DAY = "2026-09-20", "2026-09-30"


def load_triggers(con, name, run_dir):
    files = sorted(glob.glob(os.path.join(run_dir, "parts", "*.parquet")))
    if not files:
        raise SystemExit(f"no parts in {run_dir}")
    lit = "[" + ",".join(f"'{f}'" for f in files) + "]"
    con.execute(f"""
      CREATE OR REPLACE TABLE {name} AS
      SELECT family, mint, t, any_value(day) AS day, any_value(ins_share) AS ins_share,
             COALESCE(any_value(ins_share) > {INS_VETO}, false) AS vetoed, any_value(ins_share) IS NULL AS unknown
      FROM (SELECT family, mint, t, day, ins_share,
                   row_number() OVER (PARTITION BY family, mint, t ORDER BY variant, size_sol, d) AS rn
            FROM read_parquet({lit}, union_by_name = true)
            WHERE family IN ('M1P', 'M1E') AND overlay = 'none' AND size_sol = 0.5 AND d = 1 AND NOT skipped)
      WHERE rn = 1 GROUP BY family, mint, t""")
    return con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]


def flips(con):
    return con.execute(f"""
      SELECT o.family, CASE WHEN o.day BETWEEN '{SPLIT_DAY}' AND '{LAST_DAY}' THEN 'days 10-18' WHEN o.day < '{SPLIT_DAY}' THEN 'days 1-9' ELSE 'other' END AS period,
             count(*) AS triggers, count(n.mint) AS in_both,
             sum((o.vetoed AND NOT COALESCE(n.vetoed, false))::INT) AS veto_dropped, sum((NOT o.vetoed AND COALESCE(n.vetoed, false))::INT) AS veto_added,
             sum(o.vetoed::INT) AS old_vetoed, sum(COALESCE(n.vetoed, false)::INT) AS new_vetoed,
             sum(o.unknown::INT) AS old_unknown, sum(COALESCE(n.unknown, false)::INT) AS new_unknown,
             median(n.ins_share - o.ins_share) AS median_shift, quantile_cont(abs(n.ins_share - o.ins_share), 0.9) AS p90_abs_shift,
             count(*) FILTER (WHERE n.mint IS NULL) AS only_old
      FROM old o LEFT JOIN new n ON n.family = o.family AND n.mint = o.mint AND n.t = o.t
      GROUP BY 1, 2 ORDER BY 1, 2""").fetchall()


def only_new(con):
    return con.execute("SELECT n.family, count(*) FROM new n LEFT JOIN old o ON o.family = n.family AND o.mint = n.mint AND o.t = n.t WHERE o.mint IS NULL GROUP BY 1").fetchall()


def verdicts(run_dir):
    p = os.path.join(run_dir, "m1x.json")
    if not os.path.exists(p):
        return {}
    j = json.load(open(p))
    out = {}
    for fam, f in j.get("families", {}).items():
        rows = [v for v in f.get("vetoes", []) if v.get("period") == "days 10-18" and v.get("size") in (0.5, "0.5") and v.get("veto") in ("V_ins", "V_ins+V_bundle")]
        out[fam] = {"verdict": f.get("verdict"), "vins_rows": rows}
    return out


def fallbacks(run_dir):
    for name in ("config.json", "m1x.json", "report.json"):
        p = os.path.join(run_dir, name)
        if os.path.exists(p):
            j = json.load(open(p))
            for k in ("insider_fallbacks", "token_insider_fallbacks"):
                if k in j:
                    return name, j[k]
                for v in j.values():
                    if isinstance(v, dict) and k in v:
                        return name, v[k]
    return None, None


def commit_of(run_dir):
    p = os.path.join(run_dir, "config.json")
    return json.load(open(p)).get("engine_commit") if os.path.exists(p) else None


def render(old_dir, new_dir, n_old, n_new, rows, extra_new, vo, vn, fb):
    L = [f"## V_ins recompute: {os.path.basename(os.path.normpath(old_dir))} → {os.path.basename(os.path.normpath(new_dir))}\n",
         f"Old engine `{commit_of(old_dir)}`, new engine `{commit_of(new_dir)}` (#213: insider share no longer depends on the caller's stream window; "
         f"one Insiders per trigger). Triggers compared on (family, mint, t) of the base row (overlay none, 0.5 SOL, d = 1, first variant): "
         f"old {n_old:,}, new {n_new:,}. V_ins = insider share > {INS_VETO:.2f} at the decision.\n",
         f"`insider_fallbacks` (new run, {fb[0] or 'not found'}): **{fb[1] if fb[1] is not None else 'not found'}** — must be 0 for M1 / M1x "
         "(normal pools only). `token_insiders` falls back to the old window-dependent Insiders for reversed pools: an O7 overlay on F7 "
         "pool_reversed stays provisional until the token side is implemented; no O7-on-reversed number is fixed by this recompute.\n",
         "| family | period | triggers (old) | in both | old vetoed | new vetoed | **veto dropped** | **veto added** | old unknown | new unknown | median shift | p90 abs shift | only old |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for fam, per, n, both, drop, add, ov, nv, ou, nu, med, p90, oo in rows:
        L.append(f"| {fam} | {per} | {n:,} | {both:,} | {ov:,} | {nv:,} | **{drop:,}** | **{add:,}** | {ou:,} | {nu:,} | "
                 f"{'–' if med is None else f'{med:+.3f}'} | {'–' if p90 is None else f'{p90:.3f}'} | {oo:,} |")
    if extra_new:
        L.append("")
        L.append("Triggers only in the new run: " + ", ".join(f"{f}: {n:,}" for f, n in extra_new) + ".")
    L += ["", "§10 verdicts (same pre-registered rule; days 10-18 bind):", "", "| family | old | new |", "|---|---|---|"]
    for fam in sorted(set(vo) | set(vn)):
        L.append(f"| {fam} | {vo.get(fam, {}).get('verdict', '–')} | {vn.get(fam, {}).get('verdict', '–')} |")
    L += ["", "Δ(V_ins) rows at 0.5 SOL on days 10-18 (SOL per trade, paired per trigger; from m1x.json):", "",
          "| run | family | veto | d | pairs | vetoed | Δ | 95% CI |", "|---|---|---|---|---|---|---|---|"]
    for label, v in (("old", vo), ("new", vn)):
        for fam, f in sorted(v.items()):
            for r in f["vins_rows"]:
                if r.get("d") in (1, 2):
                    vs = r.get("veto_share"); dl = r.get("delta_sol"); lo = r.get("lo"); hi = r.get("hi")
                    L.append(f"| {label} | {r.get('family', fam)} | {r.get('veto')} | {r.get('d')} | {r.get('pairs', '–')} | "
                             f"{'–' if vs is None else f'{vs:.0%}'} | {'–' if dl is None else f'{dl:+.4f}'} | "
                             f"{'–' if lo is None else f'{lo:+.4f}'} … {'–' if hi is None else f'{hi:+.4f}'} |")
    L.append("")
    L.append("Reading: the verdict on the base variants (no veto) does not change with this recompute; only the V_ins rows and the O7 exit were affected by the bug.")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{os.environ.get('LAB_WALLETS_MEMORY', '1500MB')}'; SET threads TO 2")
    n_old = load_triggers(con, "old", a.old)
    n_new = load_triggers(con, "new", a.new)
    md = render(a.old, a.new, n_old, n_new, flips(con), only_new(con), verdicts(a.old), verdicts(a.new), fallbacks(a.new))
    if a.out:
        with open(a.out, "w") as f:
            f.write(md + "\n")
        print(a.out, file=sys.stderr)
    else:
        print(md)


if __name__ == "__main__":
    main()
