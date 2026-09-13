#!/usr/bin/env python3
"""Read Parquet only: fixed DuckDB SQL and an escaped standalone report, no decoding."""
import hashlib
import html
import json
import pathlib
import sys
import time

import duckdb
from manifest_reader import (attach_dataset, inventory, load_manifest,
                             reader_source_sha256, selection_inventory)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def connect():
    if duckdb.__version__ != "1.5.5":
        raise ValueError("DuckDB version differs from reviewed lock")
    return duckdb.connect(config={"threads": "1", "memory_limit": "256MB",
                                 "autoload_known_extensions": "false",
                                 "autoinstall_known_extensions": "false"})


def dataset_manifest(root):
    return load_manifest(root)


def rows(cursor):
    # JSON/browser integers are decimal strings, accompanied by DuckDB types.
    # No float conversion and no domain interpretation.
    columns = [{"name": c[0], "duckdb_type": str(c[1])} for c in cursor.description]
    result = []
    while batch := cursor.fetchmany(256):
        for row in batch:
            if len(result) == 10000:
                raise ValueError("bounded report query exceeded 10000 rows; no truncated report published")
            values = []
            for value in row:
                if isinstance(value, float):
                    raise ValueError("unexpected floating point query value")
                values.append(str(value) if isinstance(value, int) and not isinstance(value, bool) else value)
            result.append(values)
    return {"columns": columns, "rows": result}


def render(result, operations):
    e = lambda v: html.escape(str(v))
    counts = {layer: sum(int(row[-1]) for row in result["queries"]["input_evidence"]["rows"] if row[0] == layer) for layer in ["bronze", "silver"]}
    sections = []
    for name, query in result["queries"].items():
        head = "".join(f"<th>{e(c['name'])}<small>{e(c['duckdb_type'])}</small></th>" for c in query["columns"])
        body = "".join("<tr>" + "".join(f"<td>{e('NULL' if v is None else v)}</td>" for v in row) + "</tr>" for row in query["rows"])
        sections.append(f"<section><h2>{e(name)}</h2><div class='scroll'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div><details><summary>Werkelijk uitgevoerde SQL</summary><pre>{e(query['sql'])}</pre></details></section>")
    inventory_rows = ''.join('<tr>' + ''.join(f'<td>{e(value)}</td>' for value in
                              [layer.title(), item['file_count'], f"{item['rows']:,}", f"{item['bytes']:,}"]) + '</tr>'
                             for layer, item in result['inventory'].items())
    slot_rows = ''.join('<tr>' + ''.join(f'<td>{e("ONBEKEND" if value is None else value)}</td>' for value in
                         [row['slot'], row['expected_packages'], row['present_packages'], row['decoded_packages'],
                          ' · '.join(f'{name}: {count}' for name, count in row['outcomes'].items()),
                          'ja' if row['accounted'] else 'NEE']) + '</tr>' for row in result['selection'].get('slots', []))
    slot_table = ("<div class='scroll'><table><tr><th>Slot</th><th>Verwacht</th><th>Aanwezig</th><th>Decoded</th><th>Alle uitkomsten</th><th>Verantwoord</th></tr>"
                  + slot_rows + '</table></div>') if slot_rows else '<p>Geselecteerde slots zijn niet vastgelegd in dit oude manifest; niet als complete selectie aangemerkt.</p>'
    sample = result['sample_identity']
    sample_text = (f"{sample['schema']} · [{sample['start_slot']}, {sample['end_slot_exclusive']}) · centrum {sample['selected_center']}"
                   if sample else 'Geen sample-identiteit op de oorspronkelijke bron; engineeringdata blijft engineeringdata.')
    return f"""<!doctype html><html lang='nl'><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>Bronze / Silver · Parquet querycontrole</title>
<style>body{{margin:0;background:#101821;color:#e4edf5;font:15px system-ui}}main{{max-width:1450px;margin:auto;padding:32px}}h1{{font-size:30px}}h2{{font-size:20px}}p{{line-height:1.6}}section,.receipt{{background:#192531;border:1px solid #314457;border-radius:10px;padding:20px;margin:18px 0}}.tag{{color:#8ae0cf}}.warn{{color:#ffd093}}.scroll{{overflow:auto}}table{{border-collapse:collapse;white-space:nowrap;width:100%}}td,th{{padding:10px;border-bottom:1px solid #314457;text-align:left}}th{{color:#8ae0cf}}small{{display:block;color:#9eafbf}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}a{{color:#8ae0cf}}</style>
<style>.receipt pre{{max-height:160px;overflow:auto}}.metrics{{display:flex;gap:18px;flex-wrap:wrap}}.metric{{background:#192531;border:1px solid #314457;padding:18px;border-radius:10px;flex:1;min-width:170px}}.metric b{{display:block;font-size:28px;color:#8ae0cf}}details{{margin-top:12px}}summary{{cursor:pointer;color:#8ae0cf}}.context{{color:#a9bbca}}</style>
<main><div class='tag'>RUST → ARROW / PARQUET → DUCKDB {e(result['duckdb_version'])}</div><h1>Bronze / Silver-records, verliesloos bevraagbaar</h1>
<div class='metrics'><div class='metric'><b>{counts['bronze']:,}</b>Bronze-records uit Parquet</div><div class='metric'><b>{counts['silver']:,}</b>Silver-pakketten uit Parquet</div><div class='metric'><b>{operations['query_seconds']:.3f} s</b>daadwerkelijke DuckDB-querytijd</div></div>
<p>Alle resultaten hieronder komen uit de geschreven Parquet-bestanden. Geen vooraf ingevulde totalen en geen herlezing van de oorspronkelijke JSON voor de queries.</p>
<p class='warn'>{e(result['evidence']['slice_class'])} — sampleklasse is geen Research Ready-status of edgebewijs. Fixture-input blijft fixture; oude engineeringdata blijft engineeringdata. Eventhoeveelheden zijn geen uitvoerbare prijzen. Onbekende coinmetadata en CPI-privileges blijven onbekend. Bestaande afwijzingen blijven behouden.</p>
<section><h2>Manifestgebonden bestanden</h2><table><tr><th>Laag</th><th>Bestanden</th><th>Rijen</th><th>Parquetbytes</th></tr>{inventory_rows}</table>
<h2>Selectie: {e(result['selection']['status'])}</h2>{slot_table}<p class='context'>Verantwoorde pakketten, geslaagde decoding en onderzoeksgeschiktheid zijn afzonderlijke grootheden.</p>
<p><b>Sampleklasse:</b> {e(result['evidence']['slice_class'])}<br><b>Bronbewijs:</b> {e(result['evidence'].get('receipt_evidence','zie input_evidence'))}<br><b>Research Ready:</b> nee</p>
<p>{e(sample_text)}</p><details><summary>Volledige sample-identiteit en selectie-inventaris</summary><pre>{e(json.dumps({'sample_identity':sample,'selection':result['selection']},indent=2))}</pre></details></section>
<div class='receipt'><a href='query-results.json'>JSON-resultaten en SQL</a> · <a href='query-execution.json'>Uitvoeringsreceipt</a><details><summary>Alle bestandpaden, hashes en writeridentiteit</summary><b>Fysieke bestanden en oorspronkelijke volgorde</b><pre>{e(json.dumps({'inventory':result['inventory'],'files':result['files']},indent=2))}</pre><b>Querytijd</b><p>{operations['query_seconds']:.6f} seconden (operationele meting, geen historische feature)</p><b>Dataset</b><pre>{e(operations['dataset_path'])}</pre><b>Manifest SHA-256</b><pre>{e(result['manifest_sha256'])}</pre><b>Writer / bronbinding</b><pre>{e(json.dumps(result['writer'], indent=2))}</pre></details></div>
{''.join(sections)}<p>NULL is SQL-afwezigheid; *_state onderscheidt MISSING van oorspronkelijke JSON NULL. UNAVAILABLE blijft een letterlijke bewijsstatus. Getallen staan zonder floating-pointconversie in het JSON-rapport.</p></main></html>"""


def run(root, output):
    root = root.resolve(strict=True)
    if output.exists() or output.resolve().is_relative_to(root):
        raise ValueError("new report directory outside dataset required")
    manifest, manifest_sha = dataset_manifest(root)
    query_bytes = pathlib.Path(__file__).with_name("queries.sql.json").read_bytes()
    queries = json.loads(query_bytes)
    started = time.perf_counter()
    with connect() as db:
        attach_dataset(db, root, manifest)
        results = {name: dict(rows(db.execute(sql)), sql=sql) for name, sql in queries.items()}
        # Bounds, exact original bytes and integer parity are checked independently
        # by the Rust read-back verifier; Python is only a query consumer.
    elapsed = time.perf_counter() - started
    if dataset_manifest(root)[1] != manifest_sha:
        raise ValueError("dataset changed during queries")
    result = {"schema": "OF1_PARQUET_QUERY_RESULT_1", "duckdb_version": duckdb.__version__,
              "manifest_sha256": manifest_sha, "queries_sha256": sha(query_bytes),
              "writer": manifest["writer"], "files": {n: {k: v[k] for k in ["sha256", "bytes"]} for n, v in manifest["files"].items()},
              "inventory": inventory(manifest), "selection": selection_inventory(manifest),
              "sample_identity": manifest.get("sample_identity"),
              "evidence": manifest["evidence"], "integer_json_policy": "decimal strings plus DuckDB type; NULL unchanged", "queries": results}
    raw = (json.dumps(result, indent=2, ensure_ascii=False) + "\n").encode()
    operations = {"schema": "OF1_PARQUET_QUERY_EXECUTION_1", "dataset_path": str(root), "query_seconds": elapsed,
                  "result_sha256": sha(raw), "python": sys.version.split()[0], "runner_sha256": sha(pathlib.Path(__file__).read_bytes()),
                  "manifest_reader_sha256": reader_source_sha256(),
                  "lock_sha256": sha(pathlib.Path(__file__).with_name("requirements.lock").read_bytes()), "provider_calls": False}
    output.mkdir()
    (output / "query-results.json").write_bytes(raw)
    (output / "query-execution.json").write_text(json.dumps(operations, indent=2) + "\n")
    (output / "index.html").write_text(render(result, operations))
    print(json.dumps({"result_sha256": sha(raw), "query_seconds": elapsed, "report": str(output / "index.html")}))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: query.py DATASET_DIRECTORY NEW_REPORT_DIRECTORY")
    run(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]))
