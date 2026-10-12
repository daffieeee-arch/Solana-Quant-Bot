"""Uitslagen: hold-out, M1+/M1-early (M1x) and the paired exact-in measurement, read from the run
files under $LAB_DATA_ROOT/backtests. Read-only: it opens JSON files only and writes nothing.
Served with `marimo run` (app mode, code hidden); see lab/ops/lab-notebooks.service.
"""

import marimo

__generated_with = "0.25.0"
app = marimo.App(width="medium", app_title="Uitslagen")


@app.cell
def _():
    import json
    import os

    import marimo as mo

    DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
    RUNS = {
        "holdout": os.path.join(DATA, "backtests", "holdout-1047-1051", "holdout_eval.json"),
        "m1x": os.path.join(DATA, "backtests", "m1x-dev-1033-1046-453d44e", "m1x.json"),
        "paired": os.path.join(DATA, "backtests", "paired-exactin-2026-10-12", "compare.json"),
    }

    def load(name):
        """The run file as a dict, or None when it is missing or unreadable."""
        try:
            with open(RUNS[name], encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def pct(x, digits=2):
        return "–" if x is None else f"{x * 100:+.{digits}f} %".replace(".", ",")

    def ci(lo, hi):
        return f"{pct(lo)} … {pct(hi)}"

    def missing(name):
        return mo.callout(mo.md(f"Bestand niet gevonden: `{RUNS[name]}`"), kind="warn")

    return RUNS, ci, load, missing, mo, pct


@app.cell
def _(mo):
    mo.md(r"""
    # Uitslagen

    Alleen lezen. Netto rendement per gevulde trade na basiskosten, met een 95 %-betrouwbaarheidsinterval
    uit een bootstrap over handelsdagen. **Geen van deze cijfers is een winstbewijs**: dat kan alleen de
    paper-run na de stopregel (≥ 200 trades, ondergrens > 0).
    """)
    return


@app.cell
def _(ci, load, missing, mo, pct):
    holdout = load("holdout")
    if holdout is None:
        holdout_view = missing("holdout")
    else:
        _rows = []
        for _cid, _cand in holdout["candidates"].items():
            _spec, _main = _cand["spec"], _cand["groups"]["B+C"]["main"]
            _rows.append({
                "kandidaat": _cid,
                "rol": _spec.get("role"),
                "strategie": f"{_spec['family']} {_spec['segment']} {_spec['variant']}",
                "farm": _spec.get("farm"),
                "SOL": _spec.get("size_sol"),
                "trades": _main["filled"],
                "netto/trade": pct(_main["mean"]),
                "95 % CI": ci(_main["lo"], _main["hi"]),
            })
        _decision = holdout["decision"]
        if _decision.get("pick"):
            _verdict = f"Gekozen: **{_decision['pick']}**."
        else:
            _verdict = (f"**Geen kandidaat geslaagd** (groep {_decision['group']}); "
                       f"hoogste gemiddelde: {_decision.get('highest_mean_if_none') or '–'}.")
        holdout_view = mo.vstack([
            mo.md("## Hold-out (epochs 1047–1051, groep B+C)"),
            mo.md(_verdict + " Besluit eigenaar 12 okt: ADJUST, paper met K3, niet bewezen; geen echt geld."),
            mo.ui.table(_rows, selection=None, pagination=False),
        ])
    holdout_view
    return


@app.cell
def _(load, mo):
    m1x = load("m1x")
    families = sorted(m1x["families"]) if m1x else []
    family = mo.ui.dropdown(options=families, value=families[0] if families else None, label="Familie")
    size = mo.ui.dropdown(options=["0.25", "0.5", "1.0", "2.0"], value="0.5", label="SOL")
    return family, m1x, size


@app.cell
def _(ci, family, m1x, missing, mo, pct, size):
    if m1x is None:
        m1x_view = missing("m1x")
    else:
        _fam = m1x["families"][family.value]
        _binding = m1x["binding"]
        _rows = sorted(
            ({
                "variant": _g["variant"],
                "veto": _g["overlay"],
                "d": _g["d"],
                "trades": _g["trades"],
                "netto/trade": pct(_g["mean"], 1),
                "95 % CI": ci(_g["lo"], _g["hi"]),
                "win": pct(_g.get("win"), 0).lstrip("+"),
                "t.o.v. N2": pct(_g.get("vs_n2"), 1),
                "_mean": _g["mean"],
            } for _g in _fam["groups"] if _g["period"] == _binding and float(_g["size"]) == float(size.value)),
            key=lambda _r: _r["_mean"], reverse=True)
        for _r in _rows:
            _r.pop("_mean")
        _useful = _fam["verdict"].get("useful")
        m1x_view = mo.vstack([
            mo.md("## M1+ en M1-early (dev, epochs 1033–1046)"),
            mo.md(f"Bindend: **{_binding}**. Oordeel §10 voor {family.value}: "
                  f"**{'nuttig' if _useful else 'niet nuttig'}**. Regel: {_fam['verdict'].get('rule', '–')}"),
            mo.hstack([family, size], justify="start"),
            mo.ui.table(_rows, selection=None, page_size=12),
        ])
    m1x_view
    return


@app.cell
def _(load, missing, mo, pct):
    paired = load("paired")
    if paired is None:
        paired_view = missing("paired")
    else:
        _blocks = [mo.md("## Gepaarde meting exact-in (forward _store): nieuw min oud per positie")]
        for _store, _items in paired.items():
            if not isinstance(_items, list):
                continue
            _rows = [{
                "familie": _r["family"],
                "segment": _r["segment"],
                "SOL": _r["size_sol"],
                "n": _r["n"],
                "identiek": _r["identical"],
                "exit anders": _r["exit_reason_changed"],
                "ret nieuw": pct(_r["ret_new"]),
                "ret oud": pct(_r["ret_old"]),
                "verschil": pct(_r["d_mean"]),
            } for _r in _items]
            _blocks += [mo.md(f"**{_store}**"), mo.ui.table(_rows, selection=None, pagination=False)]
        paired_view = mo.vstack(_blocks)
    paired_view
    return


if __name__ == "__main__":
    app.run()
