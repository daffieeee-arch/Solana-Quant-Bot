#!/usr/bin/env python3
"""Count sealed Rust facts through DuckDB. No wire parsing or Silver admission."""
import html
import json
import pathlib
import sys
import time

from query import connect, dataset_manifest, rows, sha
from manifest_reader import attach_dataset, inventory, layer_rows, reader_source_sha256, selection_inventory

HERE = pathlib.Path(__file__).resolve().parent
LIMIT = 64 * 1024 * 1024


def bound_json(path, digest):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > LIMIT:
        raise ValueError("bounded regular evidence file required")
    raw = path.read_bytes()
    if sha(raw) != digest:
        raise ValueError("Rust diagnosis / run binding hash mismatch")
    return json.loads(raw)


def objects(table):
    return [dict(zip([c['name'] for c in table['columns']], row)) for row in table['rows']]


def rust_slots(quality):
    """The existing Rust reader emits a direct slot report for single-slot input."""
    if 'slots' in quality:
        return quality['slots']
    if 'slot' in quality and 'transaction_envelopes' in quality:
        return [quality]
    raise ValueError('unsupported Rust slot report shape')


def coverage(expected, verified, actual, extents=None):
    """Start with selection inventory, including empty or entirely missing slots."""
    if len(expected) != len(set(expected)) or len(expected) > 128:
        raise ValueError("invalid expected slot inventory")
    by_slot = {str(row['slot']): row for row in actual}
    if len(by_slot) != len(actual) or set(by_slot) - set(map(str, expected)):
        raise ValueError("duplicate/unselected slot")
    proof = {str(row['slot']): row for row in verified}
    indexes = {str(row['slot']):row for row in (extents or [])}
    if len(proof) != len(verified) or set(proof) - set(map(str, expected)):
        raise ValueError("invalid Rust slot inventory")
    result = []
    for slot in expected:
        evidence = proof.get(str(slot))
        row = by_slot.get(str(slot))
        wanted = evidence['transaction_envelopes'] if evidence else None
        present = int(row['present_envelopes']) if row else 0
        verified_car = bool(evidence and evidence['stages']['car_slot'] == 'VERIFIED')
        reconciled = wanted is not None and present == wanted and verified_car
        extent = indexes.get(str(slot))
        index_exact = (present == 0 and wanted == 0) or (wanted is not None and extent is not None
                       and int(extent['known_indices']) == wanted
                       and extent['first_index'] is not None and int(extent['first_index']) == 0
                       and extent['last_index'] is not None and int(extent['last_index']) == wanted-1)
        reconciled = reconciled and index_exact
        if row and sum(int(row[k]) for k in ['decoded', 'missing', 'unsupported', 'quarantined']) != present:
            raise ValueError("unexplained envelope outcome")
        result.append({'slot': str(slot), 'expected_envelopes': wanted,
                       'present_envelopes': present, 'missing_rows': None if wanted is None else max(0, wanted-present),
                       'unaccounted_extra_rows': None if wanted is None else max(0, present-wanted),
                       'car_verified': verified_car, 'reconciled': reconciled,
                       'transaction_index_extent_exact':index_exact,
                       'inventory_state': 'VERIFIED_EMPTY' if reconciled and wanted == 0 else 'RECONCILED' if reconciled else 'INCOMPLETE',
                       'outcomes': row, 'raw_sha256': evidence['raw_sha256'] if evidence else None})
    return result


def suitability(summary):
    """Question requirements, not an alternative protocol/economic classifier."""
    slots = summary['slots']
    silver = summary['silver']
    complete = all(s['reconciled'] for s in slots) and not summary['integrity_errors']
    return [
        {'question': 'Is deze geselecteerde Raw-invoer volledig tot transactiepackages verwerkt?',
         'requires': ['selected slot inventory', 'CAR envelope counts', 'one declared outcome per envelope'],
         'present': {'expected_slots': len(slots), 'reconciled_slots': sum(s['reconciled'] for s in slots)},
         'missing': [] if complete else ['unreconciled slot/envelope/integrity check', *summary['integrity_errors']],
         'result_kind': 'BOUNDED_ENGINEERING_CHECK' if complete else 'ENGINEERING_FAILURE',
         'next_step': 'Retain failed, unsupported and missing outcomes when adding a new sample.'},
        {'question': 'Welke ruwe sells zijn binnen de ondersteunde bronprofielen waargenomen?',
         'requires': ['mint', 'exact raw units', 'instruction/account/event context', 'transaction status', 'provenance'],
         'present': silver, 'missing': ['global variant coverage', 'quote mint/decimals for economic normalization', 'actual nested CPI privileges are not recorded'],
         'result_kind': 'BOUNDED_RECORDED_FACTS_ONLY',
         'next_step': 'Query these raw facts without converting unknown units or event reserves into prices/account state.'},
        {'question': 'Kunnen buys en sells symmetrisch worden vergeleken?',
         'requires': ['source-supported whole buy instruction', 'same context/evidence rules for both sides', 'common unit identity'],
         'present': {'sell_facts': silver['facts'], 'buy_diagnostics': summary['buy_diagnostics']},
         'missing': ['authoritative meaning/compatibility rule for the retained extra buy byte', 'economic unit evidence'],
         'result_kind': 'INSUFFICIENT_SUITABLE_DATA',
         'next_step': 'Source-bound buy compatibility evidence; no permissive parser or discarded byte.'},
        {'question': 'Welke vroege flow voorspelt een volledige coin-lifecycle?',
         'requires': ['mint identity', 'creation/earliest required state', 'buys and sells', 'continuous predeclared horizon', 'migration/state evidence'],
         'present': {'bounded_sell_facts': silver['facts']},
         'missing': ['creation/horizon completeness', 'supported lifecycle variants and state', 'causal observation model'],
         'result_kind': 'INSUFFICIENT_SUITABLE_DATA',
         'next_step': 'Define a horizon before sampling and support required Rust facts. Name/ticker/logo are optional, not identity.'},
        {'question': 'Is een hypothese na kosten en uitvoeringsaannames gefalsifieerd?',
         'requires': ['outcome-independent research sample', 'untouched holdout', 'adequate sample size', 'cost/latency/execution evidence', 'registered test'],
         'present': {'sample_class': summary.get('sample_class', 'ENGINEERING_VALIDATION_ONLY'),
                     'research_ready': False},
         'missing': ['all research-design and execution gates'],
         'result_kind': 'NOT_EVALUATED_INSUFFICIENT_SUITABLE_DATA',
         'next_step': 'A valid negative research outcome is possible only after these gates. Failed parsing or too little data is not edge falsification.'}
    ]


def render(result):
    e = lambda value: html.escape(str(value))
    s = result['summary']
    tables = []
    for name, table in result['queries'].items():
        head = ''.join('<th>'+e(c['name'])+'</th>' for c in table['columns'])
        body = ''.join('<tr>'+''.join('<td>'+e('NULL / onbekend' if v is None else v)+'</td>' for v in row)+'</tr>' for row in table['rows'])
        tables.append(f"<details><summary>{e(name)}</summary><div class='scroll'><table><tr>{head}</tr>{body}</table></div><pre>{e(table['sql'])}</pre></details>")
    matrix = ''.join(f"<section><h3>{e(q['question'])}</h3><b>{e(q['result_kind'])}</b><p>Nodig: {e('; '.join(q['requires']))}</p><p>Aanwezig: {e(json.dumps(q['present']))}</p><p>Ontbreekt: {e('; '.join(q['missing']) or 'geen binnen deze begrensde toets')}</p><p>Volgende stap: {e(q['next_step'])}</p></section>" for q in result['suitability_matrix'])
    pilot = result.get('pilot')
    slot_rows = []
    for slot in s['slots']:
        outcomes = slot['outcomes'] or {}
        values = [slot['slot'],slot['expected_envelopes'],slot['present_envelopes'],
                  outcomes.get('decoded'),outcomes.get('status_error'),outcomes.get('unsupported'),
                  outcomes.get('missing'),outcomes.get('quarantined'),slot['inventory_state']]
        slot_rows.append('<tr>'+''.join('<td>'+e('ONBEKEND' if v is None else v)+'</td>' for v in values)+'</tr>')
    slot_head = ''.join('<th>'+e(v)+'</th>' for v in ['Slot','Verwacht','Aanwezig','Decoded','On-chain ERROR','Unsupported','Missing','Quarantaine','Inventaris'])
    pilot_view = 'Geen pilot gekoppeld'
    if pilot:
        selected=pilot['selection']; budget=pilot.get('budgets',{})
        range_rows=''.join('<tr>'+''.join('<td>'+e(r[k])+'</td>' for k in ['slot','start','end_exclusive','entity_bytes'])+'</tr>' for r in pilot.get('ranges',[]))
        pilot_view=f"""<p><b>{e(pilot['range_feasibility'])}</b> — geen toestemming of lease.</p>
<p>Vaste seed: <code>{e(pilot['proposal']['seed'])}</code><br>Venster [{e(selected['start_slot'])}, {e(selected['end_slot_exclusive'])}), gekozen uit {e(selected['eligible_centers'])} centers. Geen herloting of vervanging.</p>
<div class='scroll'><table><tr><th>Slot</th><th>Start byte</th><th>Einde exclusief</th><th>Entitybytes</th></tr>{range_rows}</table></div>
<p>Unieke payload: <b>{e(budget.get('unique_payload_entity_bytes','ONBEKEND'))} bytes</b>.
Drie pogingen per range vereisen <b>{e(budget.get('payload_three_attempt_reservation_required','ONBEKEND'))}</b> reserveringsbytes;
behouden payloadcap: <b>{e(budget.get('payload_reservation_cap_unchanged','ONBEKEND'))}</b>.</p>
<p class='warn'>Geen budgetverhoging. Twee pogingen per range ({e(budget.get('payload_two_attempt_calculation_not_approved','ONBEKEND'))} bytes) zijn alleen een berekende optie voor afzonderlijke review.</p>
<p>Transactieaantallen, Pump-aanwezigheid en UTC-context zijn onbekend. Eén venster vertegenwoordigt geen marktregimes of coin-lifecycle. De huidige decoder/writer blijft engineering-only.</p>
<details><summary>Volledige selectie, budgetten, grenzen en bronbinding</summary><pre>{e(json.dumps(pilot,indent=2))}</pre></details>"""
    return f"""<!doctype html><html lang='nl'><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>Dekking & geschiktheid</title><style>
body{{background:#111b24;color:#e0eaf3;font:15px system-ui;margin:0}}main{{max-width:1200px;margin:auto;padding:32px}}h1{{font-size:32px}}p{{line-height:1.55}}section,details,.metric{{background:#1b2b39;border:1px solid #3d5365;border-radius:10px;padding:18px;margin:14px 0}}.metrics{{display:flex;gap:12px;flex-wrap:wrap}}.metric{{flex:1;min-width:180px}}.metric b{{display:block;color:#86e1ce;font-size:26px}}.warn{{color:#ffd495}}a,summary{{color:#86e1ce}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}.scroll{{overflow:auto}}td,th{{text-align:left;padding:8px;border-bottom:1px solid #3d5365}}table{{border-collapse:collapse}}small{{overflow-wrap:anywhere}}</style><main>
<small>PARQUET → DUCKDB · RUST-FEITEN, GEEN NIEUWE DOMEINDECODE</small><h1>Decoderdekking is meetbaar.<br>Onderzoeksgeschiktheid blijft begrensd.</h1>
<div class='metrics'><div class='metric'><b>{sum(x['reconciled'] for x in s['slots'])} / {len(s['slots'])}</b>slots gereconcilieerd</div><div class='metric'><b>{s['present_envelopes']} / {s['expected_envelopes']}</b>transactiepackages aanwezig / verwacht</div><div class='metric'><b>{s['pump_positive']} / {s['present_envelopes']}</b>packages met Pump-verwijzing</div><div class='metric'><b>{s['silver']['parent_packages']} / {s['pump_positive']}</b>Pump-packages met begrensde sell-feiten</div></div>
<p class='warn'>{e('ENGINEERING_FAILURE: '+', '.join(s['integrity_errors']) if s['integrity_errors'] else 'Begrensde verwerking gereconcilieerd; onderzoeksgeschiktheid NIET bewezen.')}</p>
<p class='warn'>Toegelaten sells zijn geen volledige Pump-dekking. {e(result['evidence']['slice_class'])}: sampleklasse, authentiek/fixture-bewijs, pakketverantwoording en geschiktheid blijven afzonderlijk. Geen Research Ready-, representativiteits-, lifecycle- of edgeclaim.</p>
<section><h2>Manifestgebonden dataset</h2><p>{e(json.dumps(result['inventory']))}</p><p>Fysieke publicatie gecontroleerd. Selectiedekking: <b>{e(result['selection']['status'])}</b>. Pakketten verantwoord betekent niet allemaal geslaagd gedecodeerd of geschikt voor onderzoek.</p><details><summary>Volledige geselecteerde slots en brongebonden sample-identiteit</summary><pre>{e(json.dumps({'selection':result['selection'],'sample_identity':result['sample_identity']},indent=2))}</pre></details></section>
<p>Elke envelope blijft in de noemer: ook failed, missing, unsupported of quarantined. Historische buy-layoutprobes worden apart getoond; een sell die zo'n probe afwijst is niet opnieuw een mislukte sell-decode.</p>
<section><h2>Slotinventaris en volledige noemers</h2><div class='scroll'><table><tr>{slot_head}</tr>{''.join(slot_rows)}</table></div><p>Transactiestatus: <b>{s['status_ok']} OK</b> / <b>{s['status_error']} ERROR</b> / {s['status_unknown']} onbekend. On-chain ERROR is niet hetzelfde als een fout in onze verwerking.</p><details><summary>Volledige tellingen, onbekenden en Raw-hashes</summary><pre>{e(json.dumps(s,indent=2))}</pre></details></section>
<h2>Onderzoeksvraag → aanwezige feiten → ontbrekende stap</h2>{matrix}
<section><h2>Vaste pilotselectie — voorstel, géén GO</h2>{pilot_view}</section>
<h2>Werkelijk uitgevoerde DuckDB-controles</h2>{''.join(tables)}
<section><h2>Herkomst</h2><pre>{e(json.dumps(result['bindings'],indent=2))}</pre><a href='query-results.json'>Volledig machineleesbaar resultaat / matrix / SQL</a> · <a href='query-execution.json'>Uitvoeringsreceipt</a></section></main></html>"""


def run(root, quality_path, pilot_path, output):
    root = root.resolve(strict=True)
    if output.exists() or output.resolve().is_relative_to(root):
        raise ValueError('new output directory required')
    manifest, manifest_sha = dataset_manifest(root)
    quality_sha = manifest['input']['files']['quality.json']['sha256']
    quality = bound_json(quality_path, quality_sha)
    runroot = pathlib.Path(manifest['input']['execution']['run_root'])
    payload = bound_json(runroot/'payload.json', quality['bindings']['payload_manifest_sha256'])
    start, end = payload['prepared']['start_slot'], payload['prepared']['end_slot']
    if not 0 < end-start <= 128:
        raise ValueError('bounded inventory required')
    queries_raw = (HERE/'coverage.sql.json').read_bytes()
    base_raw = (HERE/'queries.sql.json').read_bytes()
    queries = json.loads(queries_raw)
    base = json.loads(base_raw)
    for key in ['rejected_buy', 'sells', 'unknown_economics', 'parent_binding']:
        queries[key] = base[key]
    started = time.perf_counter()
    with connect() as db:
        attach_dataset(db, root, manifest)
        results = {key: dict(rows(db.execute(sql)), sql=sql) for key,sql in queries.items()}
    selected = selection_inventory(manifest)
    expected_slots = list(range(start,end))
    if manifest['schema'] == 'OF1_PARQUET_DATASET_2' and selected['selected_slots'] != expected_slots:
        raise ValueError('manifest selected slots differ from original payload selection')
    slots = coverage(expected_slots, rust_slots(quality), objects(results['slot_counts']),objects(results['index_extents']))
    integrity_errors = []
    if results['duplicate_identity']['rows']: integrity_errors.append('DUPLICATE_TRANSACTION_IDENTITY')
    if results['foreign_parent']['rows']: integrity_errors.append('SILVER_PARENT_MISMATCH')
    if not all(x['reconciled'] for x in slots): integrity_errors.append('SLOT_OR_ENVELOPE_GAP')
    if not all(x['transaction_index_extent_exact'] for x in slots): integrity_errors.append('TRANSACTION_INDEX_SET_MISMATCH_OR_UNKNOWN')
    counts = objects(results['slot_counts'])
    total = lambda key: sum(int(row[key]) for row in counts)
    silver = {key:int(value) for key,value in objects(results['silver_suitability'])[0].items()}
    if total('present_envelopes') != layer_rows(manifest, 'bronze') or silver['facts'] != layer_rows(manifest, 'silver'):
        integrity_errors.append('MANIFEST_ROW_MISMATCH')
    if manifest['schema'] == 'OF1_PARQUET_DATASET_2' and not selected['all_expected_packages_accounted']:
        integrity_errors.append('MANIFEST_SELECTION_INCOMPLETE')
    summary = {'slots':slots, 'expected_envelopes': sum(x['expected_envelopes'] for x in slots) if all(x['expected_envelopes'] is not None for x in slots) else None,
               **{key:total(key) for key in ['present_envelopes','decoded','missing','unsupported','quarantined','status_ok','status_error','status_unknown','pump_positive','pump_negative','pump_unknown']},
               'silver':silver, 'buy_diagnostics':len(results['rejected_buy']['rows']),
               'economic_complete_observations': 'UNAVAILABLE_NOT_PROVEN', 'global_pump_variant_denominator':'UNKNOWN',
               'sample_class':manifest['evidence']['slice_class'],
               'index_reported_absent': payload['prepared']['index_reported_absent'],
               'integrity_errors':integrity_errors}
    proofs = {str(s['slot']):s for s in rust_slots(quality)}
    for row in objects(results['slot_source_bindings']):
        proof = proofs.get(row['slot'])
        if not proof or row['raw_sha256'] != proof['raw_sha256'] or any(row[k] != quality['bindings'][k] for k in ['manifest_sha256','payload_manifest_sha256']):
            integrity_errors.append('PARQUET_RUST_SOURCE_BINDING_MISMATCH')
    pilot_raw = pilot_path.read_bytes()
    if len(pilot_raw)>1048576: raise ValueError('oversize pilot report')
    pilot = json.loads(pilot_raw)
    if pilot['schema']!='OF1_OFFLINE_PILOT_SELECTION_1' or pilot['network_authorized'] is not False:
        raise ValueError('not an offline pilot proposal')
    if dataset_manifest(root)[1] != manifest_sha: raise ValueError('input changed')
    result = {'schema':'OF1_DECODER_COVERAGE_1','duckdb_version':'1.5.5', 'summary':summary,
              'bindings':{'parquet_manifest_sha256':manifest_sha,'rust_quality_sha256':quality_sha,
                          'decoder':manifest['input']['execution']['decoder_source_sha256'],
                          'writer':manifest['writer'], 'coverage_sql_sha256':sha(queries_raw),'base_sql_sha256':sha(base_raw),
                          'pilot_sha256':sha(pilot_raw), 'files':{k:{x:v[x] for x in ['bytes','sha256']} for k,v in manifest['files'].items()}},
              'evidence':manifest['evidence'], 'inventory':inventory(manifest), 'selection':selected,
              'sample_identity':manifest.get('sample_identity'),
              'suitability_matrix':suitability(summary), 'pilot':pilot, 'queries':results}
    raw = (json.dumps(result,indent=2,ensure_ascii=False)+'\n').encode()
    receipt = {'schema':'OF1_COVERAGE_EXECUTION_1','result_sha256':sha(raw),'seconds':time.perf_counter()-started,
               'python':sys.version.split()[0],'runner_sha256':sha(pathlib.Path(__file__).read_bytes()),
               'manifest_reader_sha256':reader_source_sha256(),
               'query_helpers_sha256':sha((HERE/'query.py').read_bytes()),
               'dataset_path':str(root),'quality_path':str(quality_path.resolve()),'provider_calls':False}
    output.mkdir()
    (output/'query-results.json').write_bytes(raw)
    (output/'query-execution.json').write_text(json.dumps(receipt,indent=2)+'\n')
    (output/'index.html').write_text(render(result))
    print(json.dumps({'summary':summary,'result_sha256':sha(raw),'report':str(output/'index.html')}))
    if integrity_errors: raise ValueError('report incomplete: '+','.join(integrity_errors))


if __name__=='__main__':
    if len(sys.argv)!=5: raise SystemExit('coverage.py DATASET RUST_QUALITY_JSON PILOT_JSON NEW_OUTPUT')
    run(*(pathlib.Path(p) for p in sys.argv[1:]))
