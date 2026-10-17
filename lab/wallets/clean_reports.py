"""Report template for the clean test: E20 (clean_test result.json), H3 / H4 / H5 (clean_hx result.json) -> four Dutch
notes in lab/reports plus a short owner summary. Fills only what the result files hold; every number comes from them.

  python -m wallets.clean_reports --date 2026-10-2X --out lab/reports [--e20 .../result.json] [--h3 ...] [--h4 ...] [--h5 ...]
                                  [--h3-parts DIR] [--store clean.duckdb] [--watch-wallets watch-wallets-2026-10-17.json]

With --store (the clean store, read-only, only after the four runs) the H notes get a descriptive table per point-in-time
pool class (registered before the run, 2026-10-17, no gate): the creator class at the pool's creation (unknown = the
system program as coin_creator; first pool; 1-9 / 10-99 / >= 100 earlier pools of the same creator), the opening
liquidity b0, and whether a wallet of the known bot cluster (--watch-wallets, group reversed_bots) traded in the pool
before the trigger slot t. Positions, net SOL, lost-all share and worst day per class, organic, all d. Nothing else is
split on the clean data after the run.
"""
import argparse
import json
import os
import sys

LAB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SYSTEM_PROGRAM = "11111111111111111111111111111111"

PREREG = {
    "H3": ("hypothesis-h3-hold180-2026-10-15.md", "110df1a2", "hold180", "hold180-clean"),
    "H4": ("hypothesis-h4-flow300-organic-2026-10-16.md", "2358103206", "flow300_H6", "flow300-clean"),
    "H5": ("hypothesis-h5-flow300-tp100-organic-2026-10-16.md", "c59c6dfd", "flow300_tp100_H6", "flow300-tp100-clean"),
}
PINS = {"E20": "46a0236", "H3": "dbef1b8", "H4": "165bb04", "H5": "15fa3dd"}
# the exclusions document last registered on #167 (case-exclusions-clean-data.md)
EXCL_SHA = "ccfaed98e50516a7e433187e274fc8e94eacd4d3f5fc56a5e175242c8b33fd19"
DAYDEF = ("Dagdefinitie: E20 telt per UTC-dag van de trigger-slot (`tournament._days`), H3/H4/H5 per UTC-dag van de entry-slot (trigger + d ≈ 0,6 s); "
          "rond middernacht reproduceert de beschrijvende tp4_T600-dagreeks hier die van E20 dus niet exact.")


def excl_warning(o):
    """A line for the top of the note when the run's exclusions sha is not the one registered on #167 (or is missing)."""
    got = (o.get("documents") or {}).get("exclusions")
    if got == EXCL_SHA:
        return None
    return (f"> **Let op:** de uitsluitings-sha van deze run ({got or 'ontbreekt'}) is niet de laatst op #167 geregistreerde "
            f"(`{EXCL_SHA[:8]}…`); de uitkomst hieronder is daarmee niet de vastgelegde toets.")
CAVEATS = {
    "H3": ["H3 en H4/H5 zijn geen onafhankelijke toetsen (dezelfde triggers, store en dagen; hold180 organic ≈ 90 % van flow300 op dev): slagen meerdere, dan is dat één bevinding.",
           "De staart draagt de uitkomst; met T ≈ 5–9 dagen is de dagbootstrap zwak van opzet (grootste positie en slechtste dag hierboven).",
           "Organic is een gereconstrueerd label (farmer-vlag 24 u terugkijkend, drempel 50 % van het SOL-volume).",
           "De familie miste de hold-out al met tp4_T600; een positieve H3 bij een negatieve E20 is geen redding van K3."],
    "H4": ["H3, H4 en H5 zijn geen onafhankelijke toetsen; slagen meerdere, dan één bevinding (lang vasthouden op organic F7-pools).",
           "busy_until over de farm-labels heen: de run simuleert flagged en organic samen; een open flagged positie blokkeert latere organic triggers van dezelfde pool en omgekeerd. H4 meet de dev-grootheid, niet 'alleen organic handelen'; paper/live heeft een organic-only simulatie nodig (paper-spec week4, busy organic-only).",
           "De staart draagt de uitkomst; dagbootstrap met T ≈ 5–9 dagen is zwak van opzet.",
           "Organic is een gereconstrueerd label; de familie miste de hold-out al met tp4_T600."],
    "H5": ["H5 = H4 met een plafond op ×2 (zelfde triggers; op dev vuurt de take-profit op 22 % van de gemeenschappelijke triggers): slagen beide → één bevinding, voorkeur voor het plafond zoals vastgelegd; slaagt alleen H4 → het plafond is verworpen; slaagt alleen H5 → het verschil zit in die 22 %, gerapporteerd met beide dagreeksen.",
           "Binaire uitkomst per trade (take-profits ≈ ×2,03, drains −99,8 %); de dagbootstrap met T ≈ 5–9 dagen is zwak van opzet.",
           "De ×2 is meestal farmer-gemaakt (E30: 60 % farmer-leg op de take-profit-beslissing); de fill met eigen impact is een modelaanname.",
           "busy_until over de farm-labels heen zoals bij H4; paper handelt organic-only (paper-spec week4), het verschil telt de forward-replay (E32).",
           "Organic is een gereconstrueerd label; de familie miste de hold-out al met tp4_T600."],
}
FORWARD = "Forward (E32, sha 2240645b…): de beslisregel van de forward-toets geldt alleen voor een H die hier niet is verworpen; een verworpen H wordt forward alleen beschrijvend gerapporteerd."


def sol(x):
    return "–" if x is None else f"{x + 0.0:+.2f}".replace(".", ",").replace("-0,00", "+0,00")


def num(x, nd=2):
    return "–" if x is None else f"{x:.{nd}f}".replace(".", ",")


def pct(x):
    return "–" if x is None else f"{x * 100:+.2f} %".replace(".", ",")


def load(p):
    return json.load(open(p)) if p else None


def _parts_table(result_path, variant, parts=None, o=None, memory="1GB"):
    """A DuckDB connection with TEMP TABLE p: the run's F7 pool_reversed rows of `variant` (run-parts/ beside result.json, or
    an explicit parts dir with the result's window and exclusions applied as clean_hx does), net per row via costs.settle_sql
    (base). None without parts."""
    d = parts or os.path.join(os.path.dirname(os.path.abspath(result_path)), "run-parts")
    if not os.path.isdir(d):
        return None, None
    sys.path.insert(0, LAB)
    import duckdb
    from backtest.costs import Scenario, settle_sql
    from backtest.store import load_defaults
    pnl, _ = settle_sql(Scenario.from_cfg("base", load_defaults()))
    con = duckdb.connect(config={"memory_limit": memory, "threads": 2})
    extra = ""
    if parts and o:
        w, x = o.get("window", {}), o.get("exclusions", {})
        if w.get("slot_from") is not None:
            extra += f" AND t >= {w['slot_from']} AND t < {w['slot_to']}"
        con.execute("CREATE TEMP TABLE xm AS SELECT unnest(?::VARCHAR[]) AS mint", [sorted(x.get("mints", []))])
        con.execute("CREATE TEMP TABLE xp AS SELECT unnest(?::VARCHAR[]) AS pool", [sorted(x.get("pools", []))])
        extra += " AND NOT (coalesce(mint IN (SELECT mint FROM xm), false) OR coalesce(pool IN (SELECT pool FROM xp), false))"
        extra += " AND size_sol = 0.5 AND q = 0.5 AND overlay = 'none'"
    con.execute(f"""CREATE TEMP TABLE p AS SELECT variant, d, t, day, pool, farm, exit_reason, exit_slot, proceeds, {pnl} / 1e9 AS pnl
        FROM read_parquet('{d}/*.parquet', union_by_name = true)
        WHERE family = 'F7' AND segment = 'pool_reversed' AND NOT skipped AND NOT entry_failed AND variant = '{variant}'{extra}""")
    return con, d


def unsold(result_path, variant, parts=None, o=None):
    """unsold / unsold_busy of a variant on the result's `run-parts/` (clean_hx: the run's F7 pool_reversed rows in the
    window minus the exclusions). unsold: a position that never sold (exit_slot NULL; proceeds 0). The tournament frees
    the pool from the trigger (busy_until = exit_slot or t), so later triggers of the same variant / d in that pool are
    taken: unsold_busy (live the engine keeps the pool busy; conservative, only extra loss). None without run-parts."""
    con, d = _parts_table(result_path, variant, parts, o)
    if con is None:
        return None
    con.execute("CREATE TEMP TABLE u AS SELECT * FROM p WHERE exit_slot IS NULL")
    con.execute("CREATE TEMP TABLE ub AS SELECT DISTINCT q.* FROM p q JOIN u ON q.d = u.d AND q.pool = u.pool AND q.t > u.t")
    out = {}
    fd, pd_ = con.execute("""SELECT count(DISTINCT day), (SELECT count(DISTINCT day) FROM read_parquet('%s/*.parquet', union_by_name = true)
        WHERE family = 'F7' AND segment = 'pool_reversed' AND NOT skipped AND variant = '%s' AND farm = 'organic')
        FROM p WHERE farm = 'organic'""" % (d, variant)).fetchone()
    out["days"] = {"filled_organic": fd, "any_row_organic": pd_}
    # LAB review P3: sum(mayhem_reverted) over the run's rows must be 0 (pins 165bb04 and 15fa3dd carry the column)
    cols = [c[0] for c in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{d}/*.parquet', union_by_name = true)").fetchall()]
    out["mayhem_reverted"] = (con.execute(f"""SELECT coalesce(sum(CASE WHEN mayhem_reverted THEN 1 ELSE 0 END), 0)
        FROM read_parquet('{d}/*.parquet', union_by_name = true) WHERE variant = '{variant}'""").fetchone()[0] if "mayhem_reverted" in cols else None)
    for farm in ("organic", "all"):
        for dd in (None, 1, 2):
            w = ("TRUE" if farm == "all" else f"farm = '{farm}'") + ("" if dd is None else f" AND d = {dd}")
            n, net = con.execute(f"SELECT count(*), coalesce(sum(pnl), 0) FROM p WHERE {w}").fetchone()
            nu, pu = con.execute(f"SELECT count(*), coalesce(sum(pnl), 0) FROM u WHERE {w}").fetchone()
            nb, pb = con.execute(f"SELECT count(*), coalesce(sum(pnl), 0) FROM ub WHERE {w}").fetchone()
            reasons = dict(con.execute(f"SELECT coalesce(exit_reason, '?'), count(*) FROM u WHERE {w} GROUP BY 1 ORDER BY 2 DESC").fetchall())
            out[f"{farm}|{'alle d' if dd is None else f'd{dd}'}"] = {"positions": n, "net_sol": net, "unsold": nu, "unsold_sol": pu,
                                                                     "unsold_reasons": reasons, "unsold_busy": nb, "unsold_busy_sol": pb}
    return out


def classes(result_path, variant, parts=None, o=None, store=None, watch=None):
    """Descriptive point-in-time pool classes of the variant's organic positions (all d), from the store's pools and pool
    tables (read-only): creator class at the creation (earlier pools of the same coin_creator within the store), b0 band,
    bot-in-pool-before-t (the bot query scans the store's pool table: run it in a 3G scope on the VPS or as a NAS step).
    None without parts or store."""
    if not store or not os.path.exists(store):
        return None
    con, d = _parts_table(result_path, variant, parts, o)
    if con is None:
        return None
    con.execute(f"ATTACH '{store}' AS s (READ_ONLY)")
    try:
        meta = json.loads(con.execute("SELECT json FROM s.meta").fetchone()[0])
    except Exception:  # noqa: BLE001 - a store without meta (tests): no sha
        meta = {}
    store_sha = meta.get("sha256") or meta.get("store_sha256") or (o or {}).get("window", {}).get("store_sha256")
    bots = []
    if watch and os.path.exists(watch):
        bots = (json.load(open(watch)).get("groups") or {}).get("reversed_bots", [])
    con.execute("CREATE TEMP TABLE bots AS SELECT unnest(?::VARCHAR[]) AS wallet", [sorted(bots)])
    con.execute("""CREATE TEMP TABLE pl AS SELECT DISTINCT p.pool, p.t FROM p WHERE farm = 'organic'""")
    con.execute(f"""CREATE TEMP TABLE pc AS
        SELECT pl.pool, pl.t, po.coin_creator, po.create_slot, po.b0,
               (SELECT count(*) FROM s.pools q WHERE q.coin_creator = po.coin_creator AND q.create_slot < po.create_slot) AS prior_pools
        FROM pl LEFT JOIN s.pools po USING (pool)""")
    con.execute("""CREATE TEMP TABLE bb AS SELECT pl.pool, pl.t, count(x.pool) > 0 AS bot_before
        FROM pl LEFT JOIN s.pool x ON x.pool = pl.pool AND x.slot < pl.t AND x.kind IN ('buy', 'sell') AND x.trader IN (SELECT wallet FROM bots)
        GROUP BY 1, 2""")
    con.execute(f"""CREATE TEMP TABLE c AS SELECT p.*,
        CASE WHEN pc.coin_creator IS NULL THEN 'pool niet in de store' WHEN pc.coin_creator = '{SYSTEM_PROGRAM}' THEN 'creator onbekend (system-program)'
             WHEN pc.prior_pools = 0 THEN 'eerste pool van de creator' WHEN pc.prior_pools < 10 THEN 'creator met 1-9 eerdere pools'
             WHEN pc.prior_pools < 100 THEN 'creator met 10-99 eerdere pools' ELSE 'creator met >= 100 eerdere pools' END AS creator_class,
        CASE WHEN pc.b0 IS NULL THEN 'onbekend' WHEN pc.b0 < 50e9 THEN 'b0 < 50 SOL' WHEN pc.b0 < 100e9 THEN 'b0 50-100 SOL' WHEN pc.b0 < 200e9 THEN 'b0 100-200 SOL' ELSE 'b0 >= 200 SOL' END AS b0_band,
        CASE WHEN bb.bot_before THEN 'bot in de pool vóór t' ELSE 'geen bot vóór t' END AS bot_class,
        (p.proceeds = 0 OR p.exit_slot IS NULL) AS lost_all
        FROM p JOIN pc ON pc.pool = p.pool AND pc.t = p.t JOIN bb ON bb.pool = p.pool AND bb.t = p.t WHERE p.farm = 'organic'""")
    out = {"n_bots": len(bots), "store_sha256": store_sha, "store": os.path.basename(store)}
    for key in ("creator_class", "b0_band", "bot_class"):
        rows = con.execute(f"""WITH dd AS (SELECT {key} AS k, day, sum(pnl) AS s FROM c GROUP BY 1, 2)
            SELECT c.{key}, count(*), round(sum(pnl), 2), round(avg(lost_all::INT), 3), (SELECT round(min(s), 2) FROM dd WHERE dd.k = c.{key})
            FROM c GROUP BY 1 ORDER BY 2 DESC""").fetchall()
        out[key] = [{"class": r[0], "positions": r[1], "net_sol": r[2], "lost_all": r[3], "worst_day": r[4]} for r in rows]
    return out


def hx_report(name, o, date, others, ub=None, cl=None):
    f, sha, variant, slug = PREREG[name]
    h, w, ds, pb = o["test"], o["window"], o.get("DSR", {}), o.get("PBO", {})
    L = [f"# {name} op schone data: F7 organic met {variant} ({date})", "",
         f"Sessie LAB research (#167). Vastlegging `{f}` (sha256 `{sha}…`), register E{'28' if name == 'H3' else '29' if name == 'H4' else '31'}; "
         f"engine-pin {PINS[name]} (`engine_commit` {w.get('engine_commit')}), store sha256 `{w.get('store_sha256')}` (= clean.lock van E20), "
         f"extractor `{w.get('extractor_sha256')}`. Labels: **feit** (telling), **reconstructie** (kostenmodel, replay, gereconstrueerde labels), **modelaanname**, **onbekend**.", "",
         *([excl_warning(o), ""] if excl_warning(o) else []),
         *([f"> **Let op:** `sum(mayhem_reverted)` = {ub['mayhem_reverted']} in de run-parts van {variant} (moet 0 zijn).", ""]
           if ub and ub.get("mayhem_reverted") else []),
         "## 0. Uitslag", "",
         f"**{name}: gemiddeld Σ netto SOL per UTC-dag van {variant} (organic) = {sol(h.get('mean_daily_net_sol'))} SOL, 95 %-interval "
         f"{sol(h.get('lo'))} … {sol(h.get('hi'))} (dagbootstrap {h.get('draws')}, seed 7) → {'**geslaagd**' if h.get('pass') else '**niet geslaagd**'}. "
         f"Beslissing (vooraf vastgelegd): {o.get('decision')}.** (reconstructie met het kostenmodel)", "",
         f"- {h.get('days_with_a_position')} van {h.get('days')} UTC-dagen met een organic rij (mislukte instappen meegeteld; hierop rust het minimum van 5); "
         + (f"dagen met een **gevulde** organic positie: {ub['days']['filled_organic']} (uit run-parts, dag van de trigger-slot)" if ub and ub.get("days") else "dagen met een gevulde positie: onbekend (geen run-parts)")
         + f"; beoordeeld: {'ja' if h.get('judged') else 'nee'}.",
         f"- Slechtste dag {sol(h.get('worst_day_sol'))} SOL; grootste positie {sol(h.get('largest_position_sol'))} SOL van {sol(h.get('net_sol'))} SOL netto "
         f"(aandeel {num(h.get('largest_position_share'))}); top-10 posities / top-10 pools / top-1 % posities "
         f"{num(h.get('top10_positions_share'))} / {num(h.get('top10_pools_share'))} / {num(h.get('top1pct_positions_share'))}.",
         f"- Controle K3 (fail-closed): tp4_T600 farm alle per epoch rij voor rij gelijk aan E20-run (1): "
         + ", ".join(f"{e}: {v.get('run_rows')} = {v.get('e20_rows')}" for e, v in o.get("control", {}).items()) + ".",
         (f"- `unsold_busy` (organic, alle d): {ub['organic|alle d']['unsold_busy']} posities, Σ {sol(ub['organic|alle d']['unsold_busy_sol'])} SOL — "
          f"vervolgposities in een pool die een eerdere, nooit verkochte positie ({ub['organic|alle d']['unsold']} unsold, Σ {sol(ub['organic|alle d']['unsold_sol'])} SOL) "
          "in het toernooi vrijgaf (busy_until = exit_slot or t); live houdt de engine de pool bezet, dus dit is een conservatieve vertekening (alleen extra verlies, zie §2)."
          if ub else "- `unsold_busy`: onbekend (geen run-parts naast result.json)."),
         ("- `sum(mayhem_reverted)` over de run-parts van " + variant + " = " + (str(ub["mayhem_reverted"]) if ub and ub.get("mayhem_reverted") is not None else "onbekend (kolom of run-parts ontbreekt)") + " (moet 0 zijn)."),
         f"- Venster slots [{w.get('slot_from')}, {w.get('slot_to')}); d per epoch {w.get('d_per_epoch')}; ms per slot {w.get('ms_per_slot_per_epoch')}; "
         f"uitsluitingen {len(o.get('exclusions', {}).get('mints', []))} mints, {len(o.get('exclusions', {}).get('pools', []))} pools.", "",
         "## 1. Dagreeks (feit op de replay)", "",
         "| UTC-dag | Σ netto SOL |", "|---|---|"]
    for d, v in sorted(h.get("daily", {}).items()):
        L.append(f"| {d} | {sol(v)} |")
    if o.get("per_regime"):
        L += ["", f"Per regime: {o['per_regime']}."]
    L += ["", "## 2. Beschrijvend (geen lat)", "", DAYDEF, "",
          "| reeks | farm | Σ netto SOL per dag | 95 %-interval | slechtste dag | top-10 pos. / top-10 pools / top-1 % |", "|---|---|---|---|---|---|"]
    for k, s in o.get("series", {}).items():
        v, farm = k.split("|")
        L.append(f"| {v} | {farm} | {sol(s.get('mean_daily_net_sol'))} | {sol(s.get('lo'))} … {sol(s.get('hi'))} | {sol(s.get('worst_day_sol'))} | "
                 f"{num(s.get('top10_positions_share'))} / {num(s.get('top10_pools_share'))} / {num(s.get('top1pct_positions_share'))} |")
    vs = o.get("vs_tp4_T600", {})
    L += ["", f"- {variant} − tp4_T600 (organic), gemiddeld per dag: {sol(vs.get('mean_daily_diff_sol'))} SOL ({sol(vs.get('lo'))} … {sol(vs.get('hi'))}) — beschrijvend."]
    for k, r in o.get("exit_reasons", {}).items():
        L.append(f"- Exit-redenen {k}: {r}.")
    for k, t in o.get("tp_exits", {}).items():
        if t and t.get("n"):
            L.append(f"- Take-profit-exits {k}: n {t['n']}, proceeds/kosten p5 / mediaan / p95 = {num(t.get('p5'))} / {num(t.get('median'))} / {num(t.get('p95'))}.")
    for k, p in o.get("positions", {}).items():
        L.append(f"- Posities {k}: {p.get('rows')} rijen, {p.get('failed_entries')} mislukte instappen, netto {sol(p.get('net_sol'))} SOL.")
    if o.get("common_with"):
        c = o["common_with"]
        L.append(f"- Gemeenschappelijke triggers met {c['other']} ({c['farm']}): {c['common']}; Σ op die triggers {variant} {sol(c.get(variant + '_sol'))} SOL, "
                 f"{c['other']} {sol(c.get(c['other'] + '_sol'))} SOL; alleen {variant} {c.get('only_' + variant)}, alleen {c['other']} {c.get('only_' + c['other'])}.")
    if ub:
        L += ["", "Nooit verkochte posities (`unsold`: exit_slot leeg, proceeds 0) en de vervolgposities in dezelfde pool die live niet kunnen (`unsold_busy`), netto met het kostenmodel (feit op de replay):", "",
              "| farm | d | posities | Σ netto SOL | unsold | Σ unsold SOL | exit-redenen unsold | unsold_busy | Σ unsold_busy SOL |", "|---|---|---|---|---|---|---|---|---|"]
        for k, s in ub.items():
            if "|" not in k:
                continue
            farm, dd = k.split("|")
            L.append(f"| {farm} | {dd} | {s['positions']} | {sol(s['net_sol'])} | {s['unsold']} | {sol(s['unsold_sol'])} | {s['unsold_reasons']} | {s['unsold_busy']} | {sol(s['unsold_busy_sol'])} |")
        L.append("")
        L.append("Zonder de `unsold_busy`-posities (zoals live) zou de organic reeks Σ " + sol(ub['organic|alle d']['net_sol'] - ub['organic|alle d']['unsold_busy_sol'])
                 + " SOL zijn in plaats van " + sol(ub['organic|alle d']['net_sol']) + " SOL (beschrijvend; de toets hierboven telt ze mee, zoals vastgelegd in de pin).")
    L += ["", "## 2b. Beschrijvende pool-klassen (point-in-time, vastgelegd 2026-10-17, geen poort)", ""]
    if cl:
        L += [f"Organic, alle d; klassen uit de clean store `{cl['store']}` (sha256 `{cl['store_sha256'] or 'onbekend'}`), gelezen ná de vier runs, op het moment van de poolcreatie of vóór t: "
              f"creatorklasse = eerdere pools van dezelfde coin_creator **binnen de clean store** (niet de hele historie); b0 = openingsliquiditeit; "
              f"bot = een van de {cl['n_bots']} bekende reversed-pool-bots (watch-wallets-2026-10-17) handelde in de pool vóór t. "
              "De dag is hier de trigger-dag uit de run-parts, niet de entry-dag van de H-toets. Geen filter, geen beslissing; alleen om forward te vergelijken.", "",
              "| klasse | posities | Σ netto SOL | verlies-alles | slechtste dag |", "|---|---|---|---|---|"]
        for key in ("creator_class", "b0_band", "bot_class"):
            for r in cl[key]:
                L.append(f"| {r['class']} | {r['positions']} | {sol(r['net_sol'])} | {num(r['lost_all'], 3)} | {sol(r['worst_day'])} |")
    else:
        L.append("Onbekend: geen store of run-parts meegegeven (de klassen worden na de vier runs uit de clean store gelezen).")
    L += ["", "## 3. DSR en PBO (voorbehoud, geen beslisregel)", "",
          f"- DSR van de reeks (N = {ds.get('n_trials')}, T = {ds.get('t')}): dev-trials-variantie {num(ds.get('dsr_dev'), 3)}, nulvariantie 1/T {num(ds.get('dsr_null'), 3)}; PSR tegen 0 {num(ds.get('psr0'), 3)}.",
          f"- PBO (CSCV, {pb.get('n_columns')} kolommen organic, T = {pb.get('t')}, {pb.get('blocks')} blokken): {num(pb.get('pbo'), 3)}"
          + (" — indicatief (T < 10)" if pb.get("indicative_only") else "") + f"; helling {num(pb.get('slope'), 3)}, kans op verlies {num(pb.get('prob_loss'), 3)}."]
    if (ds.get("dsr_dev") or 1) < 0.95 or (ds.get("dsr_null") or 1) < 0.95 or (pb.get("pbo") or 0) > 0.5:
        L.append("- **Voorbehoud:** DSR < 0,95 of PBO > 0,5.")
    L += ["", "## 4. Samenhang met de andere toetsen", ""]
    for n2, o2 in others.items():
        if o2 is None or n2 == name:
            continue
        if n2 == "E20":
            L.append(f"- E20 (K3, H1/H2): {o2.get('decision')}.")
        else:
            L.append(f"- {n2} ({PREREG[n2][2]} organic): {o2.get('decision')} (Σ/dag {sol(o2.get('test', {}).get('mean_daily_net_sol'))} "
                     f"[{sol(o2.get('test', {}).get('lo'))} … {sol(o2.get('test', {}).get('hi'))}]).")
    L += ["", "## 5. Kanttekeningen (vastgelegd, horen bij de conclusie)", ""]
    L += [f"{i + 1}. {c}" for i, c in enumerate(CAVEATS[name])]
    L += [f"{len(CAVEATS[name]) + 1}. {FORWARD}", "",
          "## 6. Reproductie", "",
          f"`research/clean-test/{name.lower()}-{PINS[name]}/result.json` en `result.md` (clean_hx, Lab Backtest); deze noot leest alleen die bestanden. "
          "Beschrijvende exits_report-maten per farm en d staan in `result.json` onder `descriptive`."]
    return f"{date}-{slug}.md", "\n".join(L) + "\n"


def e20_report(o, date):
    p, w, ds, pb = o.get("positions", {}), o.get("window", {}), o.get("DSR", {}), o.get("PBO", {})
    L = [f"# E20 op schone data: wash-trigger-toets H1/H2 op K3 ({date})", "",
         f"Sessie LAB research (#167). Hypothese sha256 `35bdaa0d…`, aanvulling v5 `94d09d9f…`, uitsluitingen `ccfaed98…`; engine-pin {PINS['E20']} "
         f"(`engine_commit` {w.get('engine_commit')}); store {w.get('store_period')} sha256 `{w.get('store_sha256')}`; extractor `{w.get('extractor_sha256')}`. "
         "Labels: **feit** / **reconstructie** / **modelaanname** / **onbekend**.", "",
         *([excl_warning(o), ""] if excl_warning(o) else []),
         "## 0. Uitslag", "",
         f"**Beslissing (vooraf vastgelegd): {o.get('decision')}.** (reconstructie met het kostenmodel)", "",
         "| toets | n | waarde | 95 %-interval (dagbootstrap 10.000, seed 7) | geslaagd |", "|---|---|---|---|---|"]
    h1, h2 = o.get("H1", {}), o.get("H2", {})
    L += [f"| H1: K3 zonder rt_tx, gemiddeld nettorendement per gevulde trade | {h1.get('n')} | {pct(h1.get('mean'))} | {pct(h1.get('lo'))} … {pct(h1.get('hi'))} | {'ja' if h1.get('pass') else 'nee'} |",
          f"| H2: zonder − met rt_tx | | {pct(h2.get('diff'))} | {pct(h2.get('lo'))} … {pct(h2.get('hi'))} | {'ja' if h2.get('pass') else 'nee'} |"]
    wr = h2.get("with_rt_tx", {})
    L += [f"| (K3 met rt_tx) | {wr.get('n')} | {pct(wr.get('mean'))} | {pct(wr.get('lo'))} … {pct(wr.get('hi'))} | |", "",
          f"- Gevulde K3-trades {p.get('filled')} op {p.get('trading_days')} handelsdagen (minimum 5); met rt_tx {p.get('rt_tx')}, zonder {p.get('no_rt_tx')}, "
          f"zonder trigger-leg {p.get('no_trigger_leg')}; mislukte instappen {p.get('failed_entries')} (netto {num(p.get('failed_entry_pnl_sol'), 4)} SOL).",
          f"- Venster slots [{w.get('slot_from')}, {w.get('slot_to')}), warm-up-epochs {w.get('warmup_epochs')}; d per epoch {w.get('d_per_epoch')}.",
          "- Gelijkheid toets-run en matrix-run per epoch: " + ", ".join(f"{e}: d={v.get('d')}, {v.get('test_rows')} = {v.get('matrix_rows')}" for e, v in o.get("equality", {}).items()) + ".",
          f"- Uitsluitingen: {len(o.get('exclusions', {}).get('mints', []))} mints, {len(o.get('exclusions', {}).get('pools', []))} pools; "
          f"{p.get('excluded_triggers')} triggers, {p.get('excluded_positions')} van {p.get('k3')} K3-posities vervallen.",
          "- wash_1h (beschrijvend): " + "; ".join(f"{k} n={v.get('n')} {pct(v.get('mean'))} [{pct(v.get('lo'))} … {pct(v.get('hi'))}]" for k, v in o.get("wash_1h", {}).items()) + ".",
          f"- a4c op de matrix-run tegen backtest.wash: {o.get('a4c_consistency', 'niet gedraaid')}.", "",
          "## 1. DSR en PBO (voorbehoud, geen beslisregel)", "",
          f"- DSR H1-reeks (N = {ds.get('n_trials')}, T = {ds.get('t')}): dev-variantie {num(ds.get('dsr_dev'), 3)}, 1/T {num(ds.get('dsr_null'), 3)}; PSR {num(ds.get('psr0'), 3)}; "
          f"K3 bij de Clock-d (beschrijvend) {num(o.get('DSR_K3_clock', {}).get('dsr_dev'), 3)} / {num(o.get('DSR_K3_clock', {}).get('dsr_null'), 3)}.",
          f"- PBO (CSCV, {pb.get('n_trials')} kolommen, T = {pb.get('t')}, {pb.get('blocks')} blokken): {num(pb.get('pbo'), 3)}"
          + (" — indicatief (T < 10)" if pb.get("indicative_only") else "") + (f"; {pb['warning']}" if pb.get("warning") else "") + ".", "",
          "## 2. Kanttekeningen", "",
          "1. De clean-data zijn pre-Alpenglow; de stopregel-klok van K3 (E14) loopt op paper en wordt hier niet beïnvloed.",
          "2. E20 zegt niets over H3/H4/H5 (eigen vastleggingen); een positieve H bij een negatieve E20 is geen redding van K3.",
          "3. Reconstructie met het kostenmodel; DSR/PBO zijn voorbehoud, geen beslisregel.",
          "4. " + DAYDEF, "",
          "## 3. Reproductie", "", "`research/clean-test/<E20-run>/result.json` en `result.md` (clean_test, Lab Backtest); deze noot leest alleen die bestanden."]
    return f"{date}-wash-trigger-clean.md", "\n".join(L) + "\n"


def summary(results, date):
    L = [f"**Clean-toets {date} — uitslag voor de eigenaar (reconstructie met het kostenmodel, vooraf vastgelegde regels):**", ""]
    for n, o in results.items():
        if o is not None and excl_warning(o):
            L.append(f"- **{n}: uitsluitings-sha wijkt af van #167 (ccfaed98…): uitkomst niet de vastgelegde toets.**")
    for n, o in results.items():
        if o is None:
            L.append(f"- {n}: nog niet gedraaid.")
        elif n == "E20":
            L.append(f"- E20 (K3 zonder/met rt_tx): **{o.get('decision')}**; H1 {pct(o.get('H1', {}).get('mean'))} [{pct(o.get('H1', {}).get('lo'))} … {pct(o.get('H1', {}).get('hi'))}], "
                     f"H2 {pct(o.get('H2', {}).get('diff'))} [{pct(o.get('H2', {}).get('lo'))} … {pct(o.get('H2', {}).get('hi'))}]; {o.get('positions', {}).get('trading_days')} handelsdagen.")
        else:
            h = o.get("test", {})
            L.append(f"- {n} ({PREREG[n][2]} organic): **{o.get('decision')}**; Σ/dag {sol(h.get('mean_daily_net_sol'))} SOL [{sol(h.get('lo'))} … {sol(h.get('hi'))}], "
                     f"{h.get('days_with_a_position')}/{h.get('days')} dagen met positie, slechtste dag {sol(h.get('worst_day_sol'))}, grootste positie {sol(h.get('largest_position_sol'))} SOL.")
    L += ["", "H3/H4/H5 zijn één bevinding als er meerdere slagen; busy_until gezamenlijk (paper organic-only); forward-toets E32 alleen beslissend voor H's die hier niet verworpen zijn."]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--out", required=True)
    for n in ("e20", "h3", "h4", "h5"):
        ap.add_argument(f"--{n}")
        ap.add_argument(f"--{n}-parts", help="parts dir for the unsold count when the result has no run-parts/ (H3: the run's parts)")
    ap.add_argument("--store", help="the clean store (read-only), for the descriptive pool classes; only after the four runs")
    ap.add_argument("--watch-wallets", help="watch-wallets-2026-10-17.json (group reversed_bots) for the bot-before-t class")
    a = ap.parse_args(argv)
    results = {"E20": load(a.e20), "H3": load(a.h3), "H4": load(a.h4), "H5": load(a.h5)}
    if a.store:
        # the clean store is read only after the four registered runs (the clean_unspent rule of store.py): all four
        # results present, none a dry run, each with a decision
        missing = [n for n, o in results.items() if o is None or o.get("dry_run") or not o.get("decision")]
        if missing:
            ap.error("--store needs the four results (e20, h3, h4, h5) with dry_run false and a decision; not ok: " + ", ".join(missing))
    os.makedirs(a.out, exist_ok=True)
    written = []
    if results["E20"]:
        fn, md = e20_report(results["E20"], a.date)
        open(os.path.join(a.out, fn), "w").write(md)
        written.append(fn)
    for n in ("H3", "H4", "H5"):
        if results[n]:
            rp, pp = getattr(a, n.lower()), getattr(a, n.lower() + "_parts")
            fn, md = hx_report(n, results[n], a.date, results, unsold(rp, PREREG[n][2], pp, results[n]),
                               classes(rp, PREREG[n][2], pp, results[n], a.store, a.watch_wallets))
            open(os.path.join(a.out, fn), "w").write(md)
            written.append(fn)
    open(os.path.join(a.out, f"{a.date}-clean-samenvatting.txt"), "w").write(summary(results, a.date))
    print("written:", written, f"{a.date}-clean-samenvatting.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
