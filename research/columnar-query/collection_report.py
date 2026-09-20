#!/usr/bin/env python3
"""Bounded summaries over Rust manifest-listed batches. No domain decoding."""
import hashlib
import html
import json
import pathlib
import sys
import time

from query import connect

DETAIL_ROWS = 200
MAX_REPORT_BYTES = 8 * 1024 * 1024


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def bounded_rows(db, sql, limit=DETAIL_ROWS):
    """Count the whole result independently; retain only an explicit preview.

    No LIMIT is applied to source accounting or a denominator. The preview is
    visibly incomplete when necessary and never becomes dataset completeness.
    """
    total = db.execute("SELECT COUNT(*) FROM (" + sql + ") AS exact_count").fetchone()[0]
    cursor = db.execute("SELECT * FROM (" + sql + ") AS displayed_result LIMIT ?", [limit])
    columns = [{"name": c[0], "duckdb_type": str(c[1])} for c in cursor.description]
    values = []
    for row in cursor.fetchall():
        if any(isinstance(value, float) for value in row):
            raise ValueError("floating point query value is not admitted")
        values.append([str(value) if isinstance(value, int) and not isinstance(value, bool)
                       else value for value in row])
    return {"columns": columns, "rows": values, "total_result_rows": total,
            "displayed_rows": len(values), "preview_complete": len(values) == total,
            "display_limit": limit, "sql": sql}


def render(result, execution):
    e = lambda value: html.escape(str(value))
    def table(columns, values):
        return ("<div class='scroll'><table><tr>" + ''.join('<th>' + e(c) + '</th>' for c in columns)
                + '</tr>' + ''.join('<tr>' + ''.join('<td>' + e('UNAVAILABLE' if v is None else v)
                                                   + '</td>' for v in row) + '</tr>' for row in values)
                + '</table></div>')
    collection = result['collection']
    batches = collection['batches']
    selected = collection['selected_slots']
    published = sum(b['state'] == 'VERIFIED' for b in batches)
    summaries = []
    for name, query in result['queries'].items():
        summaries.append("<section><h2>" + e(name) + "</h2><p>Weergave: "
                         + str(query['displayed_rows']) + ' van ' + str(query['total_result_rows'])
                         + ' queryrijen. ' + ('' if query['preview_complete'] else '<b>Begrensde detailweergave; geen volledige rijlijst.</b>')
                         + '</p>' + table([c['name'] for c in query['columns']], query['rows'])
                         + '<details><summary>Werkelijk uitgevoerde DuckDB-SQL</summary><pre>'
                         + e(query['sql']) + '</pre></details></section>')
    slots = [[s['slot'], s['source_id'], s['role'], s['state'], s.get('expected_packages'),
              s.get('present_packages'), s.get('decoded_packages'),
              json.dumps(s.get('outcomes'), sort_keys=True), s.get('accounted')]
             for s in selected]
    batch_rows = [[b['batch_id'], b['source_id'], ', '.join(map(str, b['slots'])), b['state'],
                   b.get('bronze_rows'), b.get('silver_rows'), b.get('parquet_files'),
                   b.get('parquet_bytes')] for b in batches]
    def query_records(name):
        query = result['queries'].get(name)
        if query is None or not query['preview_complete']:
            return None  # a bounded preview must not silently become a total
        return [dict(zip([c['name'] for c in query['columns']], row, strict=True))
                for row in query['rows']]
    coverage = query_records('role_coverage')
    sides = query_records('mint_admitted_sides')
    recurrence = query_records('pilot_mint_recurrence')
    findings = ''
    if coverage is not None and sides is not None and recurrence is not None:
        packages = sum(int(r['packages']) for r in coverage)
        failed = sum(int(r['packages']) for r in coverage if r['transaction_status'] == 'ERROR')
        pump = sum(int(r['pump_referencing_packages']) for r in coverage)
        buys = sum(int(r['buys']) for r in sides)
        sells = sum(int(r['sells']) for r in sides)
        returning = sum(int(r['context_admitted_trades']) > 0
                        or int(r['context_recorded_balance_observations']) > 0 for r in recurrence)
        both = sum(r['has_both_admitted_sides'] is True for r in sides)
        findings = (f'<section><h2>Werkelijk bevraagde uitkomst</h2><p><b>{packages:,}</b> pakketten; '
                    f'<b>{failed:,}</b> opgenomen mislukte transacties; <b>{pump}</b> pakketten met '
                    f'Pump-programmaverwijzing. De bestaande profielen laten <b>{buys} buys en {sells} sells</b> toe.</p>'
                    f'<p><b>{returning} van {len(recurrence)}</b> pilotmints keren terug in de context via '
                    f'tradefeiten of balansmetadata. <b>{both}</b> mints hebben beide toegelaten kanten '
                    'in de collectie; dit is geen bewezen trader-round-trip of rendement.</p>'
                    '<p>Deze tellingen komen uit de manifestgebonden Parquetqueries hieronder. '
                    'Profielanalyses kunnen dezelfde instructie vanuit verschillende kandidaten beoordelen: '
                    'tel hun diagnoseaantallen niet op als unieke transacties. '
                    'Pakket-, instructie- en Silver-noemers staan afzonderlijk.</p></section>')
    return f"""<!doctype html><html lang='nl'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>
<title>Manifestgebonden collectie · B5</title><style>
body{{margin:0;background:#101821;color:#e4edf5;font:15px system-ui}}main{{max-width:1500px;margin:auto;padding:30px}}
h1{{font-size:30px}}h2{{font-size:21px}}p{{line-height:1.6}}section,.metric{{background:#192531;border:1px solid #314457;border-radius:10px;padding:20px;margin:18px 0}}
.metrics{{display:flex;gap:16px;flex-wrap:wrap}}.metric{{flex:1;min-width:170px}}.metric b{{display:block;font-size:28px;color:#8ae0cf}}
.warn{{color:#ffd093}}.scroll{{overflow:auto;max-height:560px}}table{{border-collapse:collapse;white-space:nowrap;width:100%}}td,th{{padding:10px;border-bottom:1px solid #314457;text-align:left}}th,a,summary{{color:#8ae0cf}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}details{{margin-top:14px}}progress{{width:100%;height:24px}}small{{color:#a9bbca}}
</style><main><small>RUST → BOUNDED BATCHES → MANIFEST PARQUET → DUCKDB</small>
<h1>Manifestgebonden collectie</h1><p>{e(collection['collection_id'])}</p>
<div class='metrics'><div class='metric'><b>{published} / {len(batches)}</b>geverifieerde verwerkingsbatches</div>
<div class='metric'><b>{len(selected)}</b>expliciet geselecteerde slots</div>
<div class='metric'><b>{e(collection['status'])}</b>collectieverantwoording, niet Research Ready</div></div>
<progress value='{published}' max='{max(1,len(batches))}'></progress>
<p class='warn'>Research Ready: false. Een complete pakkettenverantwoording is geen volledige decoderdekking of onderzoeksgeschiktheid.
Ontbrekende context is niet nul activiteit. De oorspronkelijke onafhankelijke sample blijft apart van post-hoc beschrijvende context.
Geen nieuwe acquisitie, herclassificatie, prijs-/fill- of edgeclaim.
Een buy en sell van dezelfde mint zijn geen bewezen round-trip van dezelfde trader of rendement.
Eventtijd is bron-gerapporteerd; alleen slot/transactie/instructievolgorde bepaalt hier historische ordening.
Acquisitie- en verwerkingstijden zijn uitsluitend operationele provenance.</p>
{findings}
<section><h2>Volledige logische selectie en context</h2>{table(['Slot','Bron','Rol','Publicatie','Verwachte pakketten','Aanwezig','Decoded','Alle uitkomsten','Verantwoord'], slots)}</section>
<section><h2>Fysieke batches en bestanden</h2>{table(['Batch','Oorspronkelijke bron','Slots','Status','Bronze','Silver','Parquetbestanden','Parquetbytes'],batch_rows)}
<p>Batchgrenzen zijn verwerking, geen nieuwe bronruns of wijziging van sample-identiteit. Alleen expliciet genoemde batchmanifests en shardpaden worden gelezen; geen globs.</p></section>
<section><h2>Bronbinding en uitvoering</h2><p>DuckDB {e(result['duckdb_version'])}, één thread, 256 MB querygeheugencap; querytijd {execution['query_seconds']:.3f} s.
Hieronder staan globale tellingen plus maximaal {DETAIL_ROWS} detailrijen per query; het volledige queryrijaantal staat er afzonderlijk bij.</p>
<p><a href='query-results.json'>Deterministische resultaten / SQL / bindingen</a> · <a href='query-execution.json'>Werkelijke procesmeting</a></p>
<details><summary>Manifest, oorspronkelijke sample-identiteiten en beperkingen</summary><pre>{e(json.dumps(collection,indent=2))}</pre></details></section>
{''.join(summaries) if summaries else '<section><h2>Queries nog niet beschikbaar</h2><p>Er is geen geverifieerde batch. Geen nulresultaat of complete collectie geclaimd.</p></section>'}
<p>Een eventhoeveelheid blijft onderscheiden van pre/postmetadata en transactiebrede balansdelta. Onbekende coinmetadata, accounttoestand en CPI-privileges blijven onbekend.</p></main></html>"""


def run(collection_root, output):
    from collection_reader import attach_collection, load_collection
    source = collection_root.resolve(strict=True)
    root = source if source.is_dir() else source.parent
    if output.exists() or output.resolve().is_relative_to(root):
        raise ValueError('new report directory outside collection required')
    sql_bytes = pathlib.Path(__file__).with_name('collection.sql.json').read_bytes()
    context_sql_bytes = pathlib.Path(__file__).with_name('context.sql.json').read_bytes()
    manifest, digest = load_collection(source)
    started = time.perf_counter()
    with connect() as db:
        summary = attach_collection(db, root, manifest)
        definitions = json.loads(sql_bytes)
        definitions.update(json.loads(context_sql_bytes))
        queries = ({name: bounded_rows(db, sql) for name, sql in definitions.items()}
                   if summary['verified_batches'] else {})
    elapsed = time.perf_counter() - started
    if load_collection(source)[1] != digest:
        raise ValueError('collection changed during read-only queries')
    result = {'schema':'OF1_BATCH_COLLECTION_QUERY_1','collection_manifest_sha256':digest,
              'collection':summary,'queries':queries,'duckdb_version':'1.5.5',
              'queries_sha256':sha(sql_bytes),'research_ready':False,
              'context_queries_sha256':sha(context_sql_bytes),
              'integer_json_policy':'decimal strings plus DuckDB types; null unchanged'}
    raw = (json.dumps(result,indent=2,ensure_ascii=False)+'\n').encode()
    execution = {'schema':'OF1_BATCH_COLLECTION_QUERY_EXECUTION_1','collection_path':str(root),
                 'query_seconds':elapsed,'result_sha256':sha(raw),'provider_calls':False,
                 'runner_sha256':sha(pathlib.Path(__file__).read_bytes()),
                 'reader_sha256':sha(pathlib.Path(__file__).with_name('collection_reader.py').read_bytes()),
                 'duckdb_memory_limit':'256MB','threads':1,'detail_limit':DETAIL_ROWS}
    html_bytes = render(result,execution).encode()
    if max(len(raw),len(html_bytes)) > MAX_REPORT_BYTES:
        raise ValueError('bounded report output cap exceeded; no truncated success published')
    output.mkdir()
    (output/'query-results.json').write_bytes(raw)
    (output/'query-execution.json').write_text(json.dumps(execution,indent=2)+'\n')
    (output/'index.html').write_bytes(html_bytes)
    print(json.dumps({'report':str(output/'index.html'),'sha256':sha(raw),'query_seconds':elapsed}))


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('usage: collection_report.py COLLECTION_DIRECTORY NEW_REPORT_DIRECTORY')
    run(pathlib.Path(sys.argv[1]),pathlib.Path(sys.argv[2]))
