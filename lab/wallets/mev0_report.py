"""MEV phase 0 report: the GO / NO-GO table of the frozen spec (week3-specs-mev0-2026-10-12.md v4,
section 3) on top, then the per-day measurements of section 2, from the parquet files of wallets.mev0.

Days: UTC days of the measured epochs. Section 3 judges "the median per day over the measurement
days". A day that an epoch boundary cuts (slot coverage < FULL_DAY of 24 h) is shown, but it is not
a measurement day for the medians: its counts would understate a full day.

Arbs are the closed-loop ones (v4 point 1: the fee payer is flat in every token but SOL and WSOL).
The literal count, without the loop requirement, is named apart next to them.

  python -m wallets.mev0_report --dir <wallets>/mev0-v1 --epochs 1040,1045 --out lab/reports/<date>-mev-fase0.md
"""

import argparse
import os
import statistics

import duckdb

from .common import SOL

X = 0.01 * SOL  # spec 3.1: a reachable opportunity counts at net >= 0.01 SOL
N_MIN = 30  # 3.1
TOP3_MAX, HHI_MAX = 0.60, 0.25  # 3.2
SIGNERS_MIN, NEWCOMER_SHARE_MIN = 10, 0.05  # 3.3
FAIL_SHARE_MAX, FAIL_COST_MAX = 0.90, 0.1 * X  # 3.4
FULL_DAY = 0.9
DEPTH_CLASSES = ((0, 50, "< 50 SOL"), (50, 200, "50–200 SOL"), (200, 10**12, "> 200 SOL"))
AGE_CLASSES = ((0, 15, "< 15 min"), (15, 60, "15–60 min"), (60, 10**12, "> 60 min"))


def concentration(values):
    """(top-1, top-3, top-5 share, HHI) of positive amounts (one per signer); Nones without any."""
    v = sorted((x for x in values if x and x > 0), reverse=True)
    t = sum(v)
    if not t:
        return None, None, None, None
    s = [x / t for x in v]
    return sum(s[:1]), sum(s[:3]), sum(s[:5]), sum(x * x for x in s)


def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def decide(c1, c2, c3, c4, n1):
    """GO when all four criteria hold; 'meer meten' when only criterion 1 fails and by less than a
    factor 2 (15-30 per day); NO-GO otherwise. A criterion that could not be measured (None) fails."""
    ok = [bool(c) for c in (c1, c2, c3, c4)]
    if all(ok):
        return "GO (voor fase 1 op de markt van vóór Alpenglow, v3 punt 4)"
    if ok[1:] == [True, True, True] and n1 is not None and N_MIN / 2 <= n1 < N_MIN:
        return "meer meten (alleen criterium 1 faalt, met minder dan een factor 2: twee extra epochs, drempels ongewijzigd)"
    return "NO-GO voor fase 1"


def load(con, d, epochs):
    def src(name):
        return "read_parquet([" + ",".join(f"'{os.path.join(d, f'{name}-{e}.parquet')}'" for e in epochs) + "], union_by_name = true)"

    for name in ("arbs", "attempts", "gaps", "harm", "days", "signers"):
        con.execute(f"CREATE OR REPLACE VIEW {name}_all AS SELECT * FROM {src(name)}")
    con.execute("CREATE OR REPLACE VIEW arbs_cl AS SELECT * FROM arbs_all WHERE closed_loop")
    con.execute(f"""CREATE OR REPLACE TABLE dayinfo AS
        SELECT day, sum(slots) AS slots, max(ms_per_slot) AS ms, sum(slots) * max(ms_per_slot) / 86400000.0 AS coverage
        FROM days_all GROUP BY day ORDER BY day""")
    con.execute("""CREATE OR REPLACE TABLE first_seen AS SELECT fee_payer, min(first_day) AS first_day FROM signers_all GROUP BY 1""")


def per_day(con):
    """{day: measures} for every day (section 2 and the inputs of section 3). The first day of every
    epoch counts no newcomers: the measured epochs are not contiguous (1040, 1045), so on the first
    day of a later epoch everyone active only in the epochs between would look new."""
    days = [r[0] for r in con.execute("SELECT day FROM dayinfo ORDER BY day").fetchall()]
    firsts = {r[0] for r in con.execute("SELECT min(day) FROM days_all GROUP BY epoch").fetchall()}
    out = {}
    for day in days:
        m = {"coverage": con.execute("SELECT coverage FROM dayinfo WHERE day = ?", [day]).fetchone()[0]}
        a = con.execute("""SELECT count(*), count(*) FILTER (WHERE backrun), count(*) FILTER (WHERE reachable),
                                  count(*) FILTER (WHERE reachable AND net >= ?), count(DISTINCT fee_payer),
                                  count(DISTINCT fee_payer) FILTER (WHERE reachable)
                           FROM arbs_cl WHERE day = ?""", [X, day]).fetchone()
        m.update(arbs=a[0], backrun=a[1], reachable=a[2], reachable_x=a[3], winners=a[4], winners_reach=a[5])
        m["literal"] = con.execute("SELECT count(*) FROM arbs_all WHERE day = ?", [day]).fetchone()[0]
        g = con.execute("""SELECT count(*) FILTER (WHERE NOT closed_by_arb), count(*) FILTER (WHERE NOT closed_by_arb AND reachable),
                                  count(*) FILTER (WHERE NOT closed_by_arb AND reachable AND best_net >= ?)
                           FROM gaps_all WHERE day = ?""", [X, day]).fetchone()
        m.update(gaps=g[0], gaps_reach=g[1], gaps_reach_x=g[2])
        m["volume"] = m["reachable_x"] + m["gaps_reach_x"]
        for cls, where in (("all", "true"), ("reach", "reachable")):
            per_signer = [r[0] for r in con.execute(f"SELECT sum(net) FROM arbs_cl WHERE day = ? AND {where} GROUP BY fee_payer", [day]).fetchall()]
            m[f"top1_{cls}"], m[f"top3_{cls}"], m[f"top5_{cls}"], m[f"hhi_{cls}"] = concentration(per_signer)
        n = con.execute("""SELECT count(*), count(*) FILTER (WHERE reachable), median(cost), sum(cost)
                           FROM attempts_all WHERE day = ?""", [day]).fetchone()
        m.update(attempts=n[0], attempts_reach=n[1], attempt_cost_median=n[2], attempt_cost_total=n[3])
        m["fail_share"] = n[0] / (n[0] + m["arbs"]) if n[0] + m["arbs"] else None
        m["fail_share_reach"] = n[1] / (n[1] + m["reachable"]) if n[1] + m["reachable"] else None
        nc = con.execute("""SELECT count(*), sum(a.net), count(DISTINCT a.fee_payer)
                            FROM arbs_cl a JOIN first_seen f USING (fee_payer) WHERE a.day = ? AND f.first_day = a.day""", [day]).fetchone()
        ncr = con.execute("""SELECT sum(a.net) FROM arbs_cl a JOIN first_seen f USING (fee_payer)
                             WHERE a.day = ? AND a.reachable AND f.first_day = a.day""", [day]).fetchone()[0]
        reach_net = con.execute("SELECT sum(net) FROM arbs_cl WHERE day = ? AND reachable", [day]).fetchone()[0]
        all_net = con.execute("SELECT sum(net) FROM arbs_cl WHERE day = ?", [day]).fetchone()[0]
        m["first_day"] = day in firsts
        m.update(newcomer_wins=nc[0], newcomer_winners=nc[2], newcomer_share_wins=(nc[0] / m["arbs"]) if m["arbs"] else None,
                 newcomer_share_net=((nc[1] or 0) / all_net) if all_net else None,
                 newcomer_share_reach_net=((ncr or 0) / reach_net) if reach_net else None,
                 net_total=all_net or 0, net_reach=reach_net or 0)
        out[day] = m
    return out


def criteria(days):
    """Section 3 on the measurement days (coverage >= FULL_DAY): ({criterion: (value text, pass)}, n1)."""
    full = {d: m for d, m in days.items() if m["coverage"] >= FULL_DAY}
    n1 = median([m["volume"] for m in full.values()])
    top3 = median([m["top3_reach"] for m in full.values()])
    hhi = median([m["hhi_reach"] for m in full.values()])
    winners = median([m["winners_reach"] for m in full.values()])
    newc = median([m["newcomer_share_reach_net"] for m in full.values() if not m["first_day"]])
    fail = median([m["fail_share_reach"] for m in full.values()])
    cost = median([m["attempt_cost_median"] for m in full.values()])

    def ok(v, test):
        return v is not None and test(v)

    c = {
        "1 volume": (f"mediaan {fmt(n1)} bereikbare kansen ≥ 0,01 SOL per dag (drempel ≥ {N_MIN})", ok(n1, lambda v: v >= N_MIN)),
        "2 niet gemonopoliseerd": (f"top-3 {pct(top3)} (< 60 %), HHI {fmt(hhi, 3)} (< 0,25)",
                                   ok(top3, lambda v: v < TOP3_MAX) and ok(hhi, lambda v: v < HHI_MAX)),
        "3 open voor nieuwkomers": (f"{fmt(winners)} winnaars per dag (≥ 10), nieuwkomers {pct(newc)} van de netto winst (≥ 5 %)",
                                    ok(winners, lambda v: v >= SIGNERS_MIN) and ok(newc, lambda v: v >= NEWCOMER_SHARE_MIN)),
        "4 mislukkingen betaalbaar": (f"bovengrens mislukt {pct(fail)} (< 90 %), mediane kosten {'–' if cost is None else fmt(cost / SOL, 6)} SOL (< 0,001)",
                                      ok(fail, lambda v: v < FAIL_SHARE_MAX) and ok(cost, lambda v: v < FAIL_COST_MAX)),
    }
    return c, n1, len(full)


def fmt(v, nd=1):
    if v is None:
        return "–"
    if isinstance(v, int):
        return f"{v:,}".replace(",", ".")
    return f"{v:.{nd}f}".replace(".", ",")


def pct(v):
    return "–" if v is None else f"{100 * v:.1f} %".replace(".", ",")


def sol(v):
    return "–" if v is None else f"{v / SOL:.6f}".replace(".", ",")


def table(head, rows):
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(str(x) for x in r) + " |" for r in rows])


def render(con, epochs):
    days = per_day(con)
    crit, n1, n_full = criteria(days)
    verdict = decide(*(p for _, p in crit.values()), n1)
    lines = [f"# MEV fase 0: arbitrage en backruns rond pump/PumpSwap (epochs {', '.join(map(str, epochs))})", "",
             "Alleen meten op OF1-historie; er is niets verstuurd en niets gehandeld. Spec: "
             "`week3-specs-mev0-2026-10-12.md` versie 4 (sha256 57112dee…).", "",
             f"## Go/no-go (§3): **{verdict}**", "",
             f"Mediaan per dag over {n_full} volledige meetdagen (dekking ≥ {int(FULL_DAY * 100)} %).", "",
             table(["criterium", "gemeten", "gehaald"], [(k, v, "ja" if p else "nee") for k, (v, p) in crit.items()]), "",
             "- Criterium 4 rekent met de bovengrens van de mislukte pogingen (v3 punt 1). Haalt die bovengrens 90 % niet, dan is het "
             "criterium gehaald; gaat hij erover, dan is het niet aangetoond en telt het als niet gehaald.",
             "- Een GO geldt voor de markt van vóór Alpenglow; fase 1 begint met een hermeting van §2.1, §2.5 en §2.9 na epoch 1064 "
             "(v3 punt 4).",
             "- Arbs zijn gesloten lussen (v4 punt 1): de fee-payer staat vlak in elk token behalve SOL/WSOL. De letterlijke telling "
             "zonder lus-eis staat er apart naast (kolom \"letterlijk\"); die bevat gerouteerde verkopen van andere tokens.", ""]
    rows = []
    for d, m in days.items():
        rows.append((d, pct(m["coverage"]), m["literal"], m["arbs"], m["backrun"], m["reachable"], m["reachable_x"], m["gaps"],
                     m["gaps_reach_x"], m["volume"]))
    lines += ["## §2.1 Kansen en arbs per dag", "",
              table(["dag", "dekking", "letterlijk", "arbs (gesloten lus)", "backruns", "bereikbaar", "bereikbaar ≥ 0,01 SOL",
                     "gaps (niet door een arb gesloten)", "bereikbare gaps ≥ 0,01 SOL", "volume (§3.1)"], rows), ""]
    q = """SELECT day, reachable, count(*), median(net), avg(net), quantile_cont(net, 0.9), quantile_cont(net, 0.99), max(net), sum(net)
           FROM arbs_cl GROUP BY ALL ORDER BY ALL"""
    lines += ["## §2.2 Netto winst per arb (SOL)", "",
              table(["dag", "bereikbaar", "n", "mediaan", "gemiddeld", "P90", "P99", "max", "totaal"],
                    [(r[0], "ja" if r[1] else "zelfde slot", r[2], *(sol(x) for x in r[3:])) for r in con.execute(q).fetchall()]), ""]
    rows = [(d, m["attempts"], m["attempts_reach"], pct(m["fail_share"]), pct(m["fail_share_reach"]), sol(m["attempt_cost_median"]),
             sol(m["attempt_cost_total"])) for d, m in days.items()]
    ratio = con.execute("""WITH f AS (SELECT fee_payer, count(*) AS n FROM attempts_all GROUP BY 1),
                                w AS (SELECT fee_payer, count(*) AS n FROM arbs_cl GROUP BY 1)
                           SELECT median(f.n / w.n), quantile_cont(f.n / w.n, 0.9) FROM w JOIN f USING (fee_payer)""").fetchone()
    split = con.execute("""SELECT count(*) FILTER (WHERE by_winner), count(*) FILTER (WHERE same_programs AND NOT by_winner)
                           FROM attempts_all""").fetchone()
    lines += ["## §2.3 Mislukte pogingen (bovengrens, v3 punt 1 en v4 punt 2)", "",
              table(["dag", "pogingen", "in de bereikbare klasse", "aandeel", "aandeel bereikbaar", "mediane kosten", "totale kosten"], rows), "",
              f"- Per winnaar: mislukt/geslaagd mediaan {fmt(ratio[0], 1)}, P90 {fmt(ratio[1], 1)}.",
              f"- Fee-payer is winnaar: {fmt(split[0])} pogingen. Programma-set, zonder router-only (v4 punt 2): {fmt(split[1])}.",
              "- \"Bereikbaar\" is bij pogingen een proxy. Een mislukte transactie heeft geen pool, dus ze telt als bereikbaar als ze in "
              "dezelfde of de volgende slot landt als een bereikbare arb.", ""]
    q = """SELECT day, median(jito_tip), quantile_cont(jito_tip, 0.9), median(priority_fee), quantile_cont(priority_fee, 0.9),
                  sum(jito_tip + priority_fee) / sum(gross) FROM arbs_cl GROUP BY day ORDER BY day"""
    lines += ["## §2.4 Tips en priority fees van winnende arbs", "",
              table(["dag", "tip mediaan", "tip P90", "priority mediaan", "priority P90", "aandeel van de bruto winst"],
                    [(r[0], *(sol(x) for x in r[1:5]), pct(r[5])) for r in con.execute(q).fetchall()]), ""]
    rows = [(d, m["winners"], pct(m["top1_all"]), pct(m["top3_all"]), pct(m["top5_all"]), fmt(m["hhi_all"], 3), m["winners_reach"],
             pct(m["top3_reach"]), fmt(m["hhi_reach"], 3)) for d, m in days.items()]
    lines += ["## §2.5 Searchers en concentratie van de netto winst", "",
              table(["dag", "winnaars", "top-1", "top-3", "top-5", "HHI", "winnaars bereikbaar", "top-3 bereikbaar", "HHI bereikbaar"], rows), ""]
    q = """SELECT backrun, count(*), median(dist_tx), quantile_cont(dist_tx, 0.9), median(dist_slots), quantile_cont(dist_slots, 0.9),
                  median(dist_ms) FROM arbs_cl WHERE prior_slot IS NOT NULL GROUP BY 1 ORDER BY 1 DESC"""
    lines += ["## §2.6 Afstand tot de trigger of de vorige trade", "",
              table(["backrun", "n", "tx_index mediaan (zelfde slot)", "P90", "slots mediaan", "P90", "ms mediaan"],
                    [("ja" if r[0] else "nee", r[1], *(fmt(x) for x in r[2:])) for r in con.execute(q).fetchall()]), ""]
    q = """SELECT kind, other_venue_unparsed, array_to_string(store_programs, '+') || CASE WHEN len(other_dex) > 0
                  THEN '+' || array_to_string(list_sort(other_dex), '+') ELSE '' END AS pair, count(*), median(net), sum(net)
           FROM arbs_cl GROUP BY ALL ORDER BY 4 DESC LIMIT 20"""
    lines += ["## §2.7 Venue-paren", "",
              table(["soort", "andere venue niet geparset", "paar", "n", "mediane winst", "totale winst"],
                    [(r[0], "ja" if r[1] else "nee", r[2], r[3], sol(r[4]), sol(r[5])) for r in con.execute(q).fetchall()]), ""]
    rows = []
    for dlo, dhi, dl in DEPTH_CLASSES:
        for alo, ahi, al in AGE_CLASSES:
            r = con.execute("""SELECT count(*), median(net), count(DISTINCT fee_payer) FROM arbs_cl
                               WHERE depth >= ? AND depth < ? AND age_min >= ? AND age_min < ?""",
                            [dlo * SOL, dhi * SOL, alo, ahi]).fetchone()
            rows.append((dl, al, r[0], sol(r[1]), r[2]))
    lines += ["## §2.8 Pool-diepte en -leeftijd bij de arb", "",
              "Mislukte pogingen hebben geen pool (v3 punt 1); ze ontbreken hier.", "",
              table(["diepte", "leeftijd", "arbs", "mediane winst", "winnaars"], rows), ""]
    stay = con.execute("""SELECT avg(datediff('day', lo, hi)), median(datediff('day', lo, hi)) FROM
                          (SELECT fee_payer, min(day) AS lo, max(day) AS hi FROM arbs_cl GROUP BY 1)""").fetchone()
    rows = [(d, "eerste dag van een epoch (telt niet)" if m["first_day"] else m["newcomer_winners"], m["newcomer_wins"],
             pct(m["newcomer_share_wins"]), pct(m["newcomer_share_net"]), pct(m["newcomer_share_reach_net"])) for d, m in days.items()]
    lines += ["## §2.9 Nieuwkomers", "",
              table(["dag", "winnende nieuwkomers", "hun wins", "aandeel wins", "aandeel netto", "aandeel bereikbare netto"], rows), "",
              f"- Een winnaar wint gemiddeld {fmt(stay[0])} dagen (mediaan {fmt(stay[1])}) tussen zijn eerste en laatste win.",
              "- De eerste dag van elk epoch telt geen nieuwkomers. De meetepochs liggen niet aan elkaar, dus wie alleen in de epochs "
              "ertussen actief was, zou daar nieuw lijken. Ook later in zo'n epoch kan dat nog gebeuren; het aandeel is dus een bovengrens.", ""]
    q = """SELECT day, kind, count(*), count(DISTINCT fee_payer), median(profit), sum(profit) FROM harm_all GROUP BY ALL ORDER BY ALL"""
    lines += ["## §2.10 Schadelijke MEV (alleen beschrijving, nooit een optie)", "",
              table(["dag", "soort", "aantal", "signers", "mediane winst", "totale winst"],
                    [(r[0], r[1], r[2], r[3], sol(r[4]), sol(r[5])) for r in con.execute(q).fetchall()]), ""]
    z = con.execute("""SELECT count(*), median(abs(net - (legs_sol - tx_fee - jito_tip))),
                              count(*) FILTER (WHERE abs(net - (legs_sol - tx_fee - jito_tip)) <= 0.01 * abs(legs_sol) + 10000)
                       FROM arbs_cl WHERE NOT other_venue_unparsed""").fetchone()
    lines += ["## §2.11 Nulsom-controle", "",
              f"Bij de volledig geparste arbs ({fmt(z[0])}) wordt het netto saldo van de signer vergeleken met de SOL die de store-legs "
              f"opleveren, min fee en tip. Mediaan van het verschil: {sol(z[1])} SOL. Binnen 1 % (+ 0,00001 SOL): {fmt(z[2])}.", ""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", required=True)
    ap.add_argument("--epochs", default="1040,1045")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    epochs = [int(x) for x in a.epochs.split(",") if x]
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{os.environ.get('LAB_DUCKDB_MEM', '1GB')}'; SET threads TO 2")
    load(con, a.dir, epochs)
    md = render(con, epochs)
    if a.out:
        with open(a.out, "w") as f:
            f.write(md + "\n")
    else:
        print(md)


if __name__ == "__main__":
    main()
