"""Experimentregister: every lab experiment with its data, outcome and status, which epochs are spent,
closed or clean, and the owner's cases excluded from the clean-data test. Reads the markdown tables of
lab/reports/experiment-register.md (the copy in the served worktree; $LAB_REGISTER overrides) and
$LAB_DATA_ROOT/research/case-exclusions-clean-data.md. Read-only; it never reads the clean extraction.
Served with `marimo run` (app mode, code hidden).
"""

import marimo

__generated_with = "0.25.0"
app = marimo.App(width="medium", app_title="Experimentregister")


@app.cell
def _():
    import hashlib
    import html
    import os
    import re

    import marimo as mo

    DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
    _here = mo.notebook_dir() or os.path.join(os.getcwd(), "lab", "notebooks")
    REGISTER = os.environ.get("LAB_REGISTER") or os.path.join(str(_here), "..", "reports", "experiment-register.md")
    CASES = os.path.join(DATA, "research", "case-exclusions-clean-data.md")

    def read(path):
        """The file's text (newlines kept as on disk, so its sha256 matches the file), or None when missing."""
        try:
            with open(path, encoding="utf-8", newline="") as f:
                return f.read()
        except (OSError, UnicodeDecodeError):
            return None

    def split_row(line):
        """The cells of one markdown table row; `\\|` is a literal bar, and bars inside `code` do not split."""
        cells, cur, code, i = [], "", False, 0
        body = line.strip()
        body = body[1:] if body.startswith("|") else body
        body = body[:-1] if body.endswith("|") and not body.endswith("\\|") else body
        while i < len(body):
            ch = body[i]
            if ch == "\\" and body[i + 1:i + 2] == "|":
                cur, i = cur + "|", i + 2
                continue
            if ch == "`":
                code = not code
            if ch == "|" and not code:
                cells.append(cur.strip())
                cur = ""
            else:
                cur += ch
            i += 1
        cells.append(cur.strip())
        return cells

    def tables(text):
        """{section heading: [rows as dicts]} for every markdown table, keyed by the `## ` heading above it."""
        found, section, block = {}, "", []

        def flush():
            if len(block) >= 2 and re.fullmatch(r"\|?[\s:|-]+\|?", block[1].strip()):
                head = split_row(block[0])
                found.setdefault(section, []).extend(
                    dict(zip(head, split_row(_l) + [""] * (len(head) - len(split_row(_l))))) for _l in block[2:])
            block.clear()

        for _line in (text or "").splitlines():
            if _line.lstrip().startswith("|"):
                block.append(_line)
                continue
            flush()
            if _line.startswith("## "):
                section = _line[3:].strip()
        flush()
        return found

    def section(found, word):
        """Rows of the first section whose heading contains `word` (case-insensitive), or []."""
        return next((v for k, v in found.items() if word.lower() in k.lower()), [])

    def plain(s):
        """Cell text without markdown emphasis or code marks."""
        return re.sub(r"\*\*|(?<!\w)\*|\*(?!\w)|`", "", s or "").strip()

    def esc(s):
        """File text for mo.md: HTML-escaped, so the register can never inject markup into the page."""
        return html.escape(s or "", quote=True).replace("*", "&#42;").replace("_", "&#95;")

    return CASES, REGISTER, esc, hashlib, mo, plain, re, read, section, tables


@app.cell
def _(mo):
    mo.md(r"""
    # Experimentregister

    Elke proef van het lab staat hier: wat er is getest, op welke data en met welke uitkomst. **Verworpen
    ideeën blijven staan.** Zo tellen we eerlijk hoeveel proeven er zijn gedaan, en wordt dezelfde data
    niet twee keer als bewijs gebruikt.

    - **verworpen**: getoetst en niet goed genoeg;
    - **verkennend**: op al gebruikte data; een idee om te toetsen, geen bewijs;
    - een bevestigende toets kan alleen op **schone** data, met een vooraf vastgelegde regel.

    Deze pagina leest alleen. Het register zelf wordt bijgehouden door de coördinator.
    """)
    return


@app.cell
def _(REGISTER, mo, read, section, tables):
    _text = read(REGISTER)
    _found = tables(_text)
    experiments = section(_found, "proeven")
    usage = section(_found, "datagebruik")
    if _text is None:
        register_note = mo.callout(mo.md(f"Bestand niet gevonden: `{REGISTER}`."), kind="warn")
    elif not experiments or not usage:
        register_note = mo.callout(mo.md("Het register is gevonden, maar de tabel met proeven of het datagebruik "
                                         "ontbreekt of heeft een andere vorm."), kind="warn")
    else:
        register_note = mo.md("")
    register_note
    return experiments, usage


@app.cell
def _(experiments, plain):
    # Order matters: a status like "verworpen (K3 blijft als paper-meting)" counts as verworpen.
    KINDS = [("verworpen", "verworpen"), ("vervallen", "vervallen"), ("verkennend", "verkennend"),
             ("lopend", "lopend"), ("vastgezet", "vastgezet"), ("diagnose", "diagnose"),
             ("technische check", "techniek"), ("beschrijvend", "beschrijvend"), ("illustratie", "beschrijvend")]
    COLORS = {"verworpen": ("#fde2e1", "#8a1c1c"), "verkennend": ("#fff1c2", "#6b4e00"),
              "vervallen": ("#ececec", "#555555"), "vastgezet": ("#dbeafe", "#1e3a8a"),
              "lopend": ("#e0f2fe", "#075985"), "schoon": ("#dcfce7", "#14532d"),
              "gesloten": ("#fde2e1", "#8a1c1c"), "verbruikt": ("#fff1c2", "#6b4e00")}

    def kind(status):
        _s = plain(status).lower()
        return next((k for w, k in KINDS if w in _s), "anders")

    rows = [{"id": plain(_e.get("id")), "datum": plain(_e.get("datum")), "wat": plain(_e.get("wat")),
             "data": plain(_e.get("data")), "uitkomst": plain(_e.get("uitkomst")), "soort": kind(_e.get("status")),
             "status": plain(_e.get("status")), "correcties": plain(_e.get("correcties"))}
            for _e in experiments]
    return COLORS, rows


@app.cell
def _(mo, rows):
    _kinds = sorted({_r["soort"] for _r in rows})
    kind_filter = mo.ui.multiselect(options=_kinds, value=_kinds, label="Soort")
    search = mo.ui.text(placeholder="zoek in alle kolommen", label="Zoek")
    return kind_filter, search


@app.cell
def _(COLORS, kind_filter, mo, rows, search):
    _q = search.value.strip().lower()
    _shown = [_r for _r in rows if _r["soort"] in kind_filter.value
              and (not _q or any(_q in str(_v).lower() for _v in _r.values()))]

    def _style(row_id, column, value):
        _c = COLORS.get(_shown[int(row_id)]["soort"]) if column in ("id", "soort", "status") else None
        return {"backgroundColor": _c[0], "color": _c[1]} if _c else {}

    _counts = {}
    for _r in rows:
        _counts[_r["soort"]] = _counts.get(_r["soort"], 0) + 1
    _summary = ", ".join(f"{n} {k}" for k, n in sorted(_counts.items(), key=lambda kv: -kv[1]))
    mo.vstack([
        mo.md(f"## 1. Proeven\n\n{len(rows)} proeven: {_summary}. "
              "Rood = verworpen, geel = verkennend, grijs = vervallen, blauw = vastgezet of lopend."),
        mo.hstack([kind_filter, search], justify="start", wrap=True),
        mo.ui.table(_shown, selection=None, pagination=False, style_cell=_style, show_download=False,
                    wrapped_columns=["wat", "uitkomst", "status", "correcties"]),
    ]) if rows else mo.md("")
    return


@app.cell
def _(COLORS, mo, plain, usage):
    def _kind(row):
        _s = (plain(row.get("status")) + " " + plain(row.get("periode"))).lower()
        if "gesloten" in _s:
            return "gesloten"
        if "verbruikt" in _s:
            return "verbruikt"
        if "onaangeroerd" in _s or "schoon" in _s:
            return "schoon"
        return "nog niet beschikbaar"

    _rows = [{"periode": plain(_u.get("periode")), "epochs": plain(_u.get("epochs")), "slots": plain(_u.get("slots")),
              "staat": _kind(_u), "status": plain(_u.get("status")), "gebruikt voor": plain(_u.get("gebruikt voor"))}
             for _u in usage]

    def _style(row_id, column, value):
        _c = COLORS.get(_rows[int(row_id)]["staat"]) if column in ("epochs", "staat") else None
        return {"backgroundColor": _c[0], "color": _c[1]} if _c else {}

    mo.vstack([
        mo.md("## 2. Datagebruik: welke epochs zijn verbruikt, gesloten of schoon\n\n"
              "Geel = verbruikt (alleen nog verkennend), rood = gesloten (hold-out, nooit meer afstemmen), "
              "groen = schoon (bewaard voor de volgende bevestigende toets; niet lezen vóór een pre-registratie)."),
        mo.ui.table(_rows, selection=None, pagination=False, style_cell=_style, show_download=False,
                    wrapped_columns=["periode", "slots", "status", "gebruikt voor"]),
    ]) if _rows else mo.md("")
    return


@app.cell
def _(CASES, esc, hashlib, mo, plain, re, read, tables):
    # Status of the case analyses: their data is in epochs 1056/1057, which OF1 publishes in October.
    WAITING = "wacht op epoch 1056/1057"
    _text = read(CASES)
    if _text is None:
        cases_view = mo.callout(mo.md(f"Bestand niet gevonden: `{CASES}`."), kind="warn")
    else:
        _cases = {}
        for _r in next(iter(tables(_text).values()), []):
            _m = re.search(r"\(([A-Z0-9]{2,12})\)", _r.get("naam", ""))
            if not _m:
                continue
            _c = _cases.setdefault(_m.group(1), {"naam": plain(_r["naam"]), "adressen": [], "waarom": []})
            # Only base58 addresses become links (the link is built here, the page fetches nothing).
            _c["adressen"] += [_a for _a in re.findall(r"`([1-9A-HJ-NP-Za-km-z]{32,44})`", _r.get("mint", ""))
                               if _a not in _c["adressen"]]
            _rule = ("" if re.search(r"`[1-9A-HJ-NP-Za-km-z]{32,44}`", _r.get("mint", ""))
                     else f" (regel: {plain(_r.get('mint'))})")
            _c["waarom"].append(plain(_r.get("waarom")) + _rule)
        _cards = []
        for _sym, _c in _cases.items():
            _links = " · ".join(
                f'<a href="https://dexscreener.com/solana/{_a}" target="_blank" rel="noopener noreferrer">'
                f"DexScreener {_a[:6]}…{_a[-4:]}</a>" for _a in _c["adressen"]) or "nog geen adres"
            _cards.append(mo.vstack([
                mo.md(f"### {esc(_c['naam'])}"),
                mo.hstack([mo.md(_links), mo.callout(mo.md(f"**{WAITING}**"), kind="info")],
                          justify="space-between", wrap=True),
                mo.md("\n".join(f"- {esc(_w)}" for _w in _c["waarom"])),
            ]))
        cases_view = mo.vstack([
            mo.md("## 3. Casussen van de eigenaar\n\n"
                  "Tokens die de eigenaar heeft bekeken, tellen niet mee in de bevestigende toets op de schone "
                  "epochs 1055–1063. De links openen DexScreener in je eigen browser; deze pagina haalt daar "
                  f"niets op. Bestand-sha256: `{hashlib.sha256(_text.encode('utf-8')).hexdigest()[:12]}…`"),
            *(_cards or [mo.callout(mo.md("Geen casussen gevonden in het bestand."), kind="warn")]),
        ])
    cases_view
    return


if __name__ == "__main__":
    app.run()
