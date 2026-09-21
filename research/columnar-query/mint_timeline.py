#!/usr/bin/env python3
"""Transaction-grouped presentation of retained Rust facts; no wire decoding."""
import argparse
import datetime
import hashlib
import html
import json
import pathlib
import re
import resource
import sys
import time

from collection_reader import attach_collection, load_collection
from collection_report import MAX_REPORT_BYTES
from collection_run import process_limits
from manifest_reader import pairs_unique
from query import connect

HERE = pathlib.Path(__file__).resolve().parent
PROFILE_PATHS = (
    'pump_sell_analysis', 'pump_buy_variant_analysis', 'pump_nested_buy_analysis',
    'pump_buy_exact_quote_v2_analysis', 'pump_structural_analysis.buy_source_diagnostics',
)
PROBE_PATH = 'pump_structural_analysis.observations'
MAX_PACKAGES = 10000  # fail, never publish a truncated success
LIMITS = [
    'POSTHOC_DESCRIPTIVE_LIFECYCLE_FRAGMENT; not an independent research sample.',
    'First/last observation does not establish launch, migration or lifecycle end.',
    'No selected row means no row in these channels, not proven zero activity.',
    'A message reference or diagnostic mint is not a trade or proven account role.',
    'Instructions are declared top-level or recorded CPI references, not all proven executed.',
    'All instructions and evidence in a transaction form one atomic observation package.',
    'Balance deltas are retained Rust transaction-wide observations, never instruction deltas.',
    'Actual CPI privileges, full account state, quote units, fills and returns remain unavailable.',
]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def exact(value):
    """All integers, including nested diagnostics, survive browser JSON exactly."""
    if isinstance(value, float):
        raise ValueError('floating point report value is not admitted')
    if type(value) is int:
        return str(value)
    if isinstance(value, dict):
        return {k: exact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [exact(v) for v in value]
    return value


def canonical(value):
    return (json.dumps(exact(value), sort_keys=True, ensure_ascii=False, indent=2) + '\n').encode()


def at(value, path):
    for key in path.split('.'):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def instruction_key(outer, inner):
    return (int(outer), -1 if inner is None else int(inner))


def diagnoses(transaction, mint):
    rows = []
    states = {}
    for path in (*PROFILE_PATHS, PROBE_PATH):
        source = at(transaction, path)
        states[path] = 'RECORDED' if isinstance(source, list) else 'UNAVAILABLE'
        for index, item in enumerate(source if isinstance(source, list) else []):
            probe = path == PROBE_PATH
            # These explicit Rust field paths are the entire attribution contract.
            # Do not infer a mint from expected account roles, PDA matches or siblings.
            observed_mint = at(item, 'structural_fields.mint_bytes_base58' if probe
                               else 'event_reported.mint_address')
            position = item.get('context', {}) if probe else item
            outer = position.get('outer_index')
            inner = position.get('inner_order' if probe else 'instruction_inner_order')
            rows.append({
                'json_pointer': '/transaction/' + path.replace('.', '/') + '/' + str(index),
                'kind': 'STRUCTURAL_PROBE' if probe else 'PROFILE_DIAGNOSIS',
                'outer_index': outer, 'inner_order': inner,
                'mint_field': 'structural_fields.mint_bytes_base58' if probe else 'event_reported.mint_address',
                'reported_mint': observed_mint,
                'mint_attribution': ('EXPLICIT_MATCH' if observed_mint == mint else
                                     'EXPLICIT_OTHER_MINT' if observed_mint is not None else 'UNKNOWN'),
                'schema': item.get('schema'), 'profile': item.get('evaluated_profile'),
                'disposition': item.get('disposition'), 'reason': item.get('reason'),
                'layout_outcome': item.get('layout_outcome'), 'layout_error': item.get('layout_error'),
                'proof_gaps': item.get('proof_gaps'),
                'source_evidence_sha256': item.get('source_evidence_sha256'),
                'full_instruction_error': item.get('full_instruction_error'),
                'event_association_failure': item.get('event_association_failure'),
                'silver': item.get('silver'),
            })
    rows.sort(key=lambda d: ((2**64, -1) if d['outer_index'] is None else
                            instruction_key(d['outer_index'], d['inner_order']), d['json_pointer']))
    return rows, states


def instructions(transaction):
    rows = []
    seen = set()
    for field in ('instructions', 'inner_instructions'):
        for index, item in enumerate(transaction.get(field) or []):
            outer = item['index'] if field == 'instructions' else item['outer_index']
            inner = None if field == 'instructions' else item['inner_order']
            key = instruction_key(outer, inner)
            if key in seen:
                raise ValueError('duplicate recorded instruction position')
            seen.add(key)
            rows.append({'outer_index': outer, 'inner_order': inner,
                         'kind': 'DECLARED_TOP_LEVEL' if inner is None else 'RECORDED_CPI',
                         'stack_height': item.get('stack_height'), 'program_id': item.get('program_id'),
                         'program_id_index': item.get('program_id_index'),
                         'account_indexes': item.get('account_indexes'),
                         'json_pointer': f'/transaction/{field}/{index}'})
    return sorted(rows, key=lambda r: instruction_key(r['outer_index'], r['inner_order']))


def bound_record(raw, digest):
    if not raw.endswith(b'\n') or sha(raw[:-1]) != digest:
        raise ValueError('canonical record binding mismatch')
    return json.loads(raw, object_pairs_hook=pairs_unique)


def query_timeline(db, mint, definitions):
    """Selection uses EXISTS/list membership, never a detail-row cross product."""
    db.execute('CREATE TEMP TABLE mint_selection AS ' + definitions['selection'], {'mint': mint})
    size = db.execute('SELECT count(*) FROM mint_selection').fetchone()[0]
    if size > MAX_PACKAGES:
        raise ValueError('complete selection exceeds report package cap; no truncated success')
    facts = {}
    for source_id, parent, digest, raw in db.execute(definitions['facts'], {'mint': mint}).fetchall():
        record = bound_record(raw, digest)
        key = (source_id, parent)
        facts.setdefault(key, []).append({'record_sha256': digest, 'record': record})
    cards = []
    for row in db.execute(definitions['packages']).fetchall():
        source_id, parent, role, batch, slot, tx_index, raw, silver, balance, diagnosis, message = row
        record = bound_record(raw, parent)
        tx = record.get('transaction') or {}
        source = record['source']
        if int(record['effective_at']['slot']) != slot or int(record['effective_at']['transaction_index_in_slot']) != tx_index:
            raise ValueError('package chain position mismatch')
        ds, states = diagnoses(tx, mint)
        observations = at(tx, 'token_balance_context.observations')
        balances = [{'observation_index': i, 'observation': o}
                    for i, o in enumerate(observations or []) if o.get('mint') == mint]
        references = [i for i, key in enumerate(tx.get('account_keys') or []) if key == mint]
        selected_facts = facts.pop((source_id, parent), [])
        selected_facts.sort(key=lambda f: (
            instruction_key(f['record']['event_context']['outer_index'],
                            at(f['record'], 'event_context.selected_invocation.inner_order')),
            f['record']['event_context'].get('inner_order', -1), f['record_sha256']))
        # Check SQL signal paths against the displayed exact record, without decoding.
        if (silver, balance, diagnosis, message) != (
                bool(selected_facts), bool(balances),
                any(d['mint_attribution'] == 'EXPLICIT_MATCH' for d in ds), bool(references)):
            raise ValueError('selection channels differ from displayed canonical evidence')
        for fact in selected_facts:
            f = fact['record']
            if f['bronze_record_sha256'] != parent or f['source'] != source:
                raise ValueError('fact source/parent binding mismatch')
        receipts = [r for r in source['bindings']['receipts'] if r['sequence'] == source['receipt_sequence']]
        if len(receipts) != 1 or receipts[0]['raw_sha256'] != source['raw_sha256']:
            raise ValueError('package source receipt binding mismatch')
        cards.append({
            'package_id': source_id + ':' + parent, 'collection_source_id': source_id,
            'bronze_record_sha256': parent, 'collection_batch_id': batch,
            'collection_role': role, 'slice_class': record['slice_class'],
            'slot': slot, 'transaction_index': tx_index, 'effective_at': record['effective_at'],
            'inclusion': {'SILVER_FACT': silver, 'BALANCE_MINT': balance,
                          'EXPLICIT_DIAGNOSIS_MINT': diagnosis, 'MESSAGE_ACCOUNT_REFERENCE': message},
            'transaction_status': tx.get('status'), 'transaction_error': tx.get('transaction_error'),
            'bronze_disposition': record['disposition'], 'bronze_reason': record.get('reason'),
            'signatures': tx.get('signatures'), 'atomic_observation_package': record['atomic_observation_package'],
            'source': {k: source.get(k) for k in ('run_id', 'raw_path', 'raw_sha256', 'receipt_sequence',
                       'raw_section_offset', 'raw_section_length', 'transaction_node_cid_hex')},
            'receipt': receipts[0], 'decoder_source_sha256': record.get('decoder_source_sha256'),
            'sample_identity': record.get('sample_identity'),
            'message_mint_key_indexes': references,
            'message_account_keys_state': 'RECORDED' if isinstance(tx.get('account_keys'), list) else 'UNAVAILABLE',
            'instructions': instructions(tx),
            'instruction_inventory': {k: 'RECORDED' if isinstance(tx.get(k), list) else 'UNAVAILABLE'
                                      for k in ('instructions', 'inner_instructions')},
            'diagnostics': ds, 'diagnostic_inventory': states,
            'balance_observations': balances,
            'balance_collection_status': at(tx, 'token_balance_context.collection_status'),
            'silver_facts': selected_facts,
        })
    if facts or len(cards) != size:
        raise ValueError('selected parent missing or duplicated')
    cards.sort(key=lambda c: (c['slot'], c['transaction_index'], c['package_id']))
    return cards


def summarize(cards):
    ds = [d for c in cards for d in c['diagnostics']]
    facts = [f for c in cards for f in c['silver_facts']]
    return {
        'transactions': len(cards),
        'instruction_references': sum(len(c['instructions']) for c in cards),
        'declared_top_level_instructions': sum(i['kind'] == 'DECLARED_TOP_LEVEL' for c in cards for i in c['instructions']),
        'recorded_cpi_instructions': sum(i['kind'] == 'RECORDED_CPI' for c in cards for i in c['instructions']),
        'diagnoses': len(ds), 'profile_diagnoses': sum(d['kind'] == 'PROFILE_DIAGNOSIS' for d in ds),
        'structural_probes': sum(d['kind'] == 'STRUCTURAL_PROBE' for d in ds),
        'diagnosed_instruction_positions': sum(len({instruction_key(d['outer_index'], d['inner_order'])
                                                    for d in c['diagnostics'] if d['outer_index'] is not None})
                                                for c in cards),
        'diagnoses_without_instruction_position': sum(d['outer_index'] is None for d in ds),
        'diagnoses_explicit_mint_match': sum(d['mint_attribution'] == 'EXPLICIT_MATCH' for d in ds),
        'diagnoses_unknown_mint': sum(d['mint_attribution'] == 'UNKNOWN' for d in ds),
        'not_admitted_profile_diagnoses': sum(d['disposition'] == 'NOT_ADMITTED' for d in ds),
        'balance_observations': sum(len(c['balance_observations']) for c in cards),
        'balance_packages': sum(bool(c['balance_observations']) for c in cards),
        'silver_facts': len(facts),
        'buys': sum(f['record']['event_reported']['is_buy'] is True for f in facts),
        'sells': sum(f['record']['event_reported']['is_buy'] is False for f in facts),
        'status': {state: sum((c['transaction_status'] or 'UNKNOWN') == state for c in cards)
                   for state in sorted({c['transaction_status'] or 'UNKNOWN' for c in cards})},
        'packages_with_unknown_cpi_inventory': sum(c['instruction_inventory']['inner_instructions'] == 'UNAVAILABLE' for c in cards),
        'by_role': {role: {'transactions': len(group),
                           'silver_facts': sum(len(c['silver_facts']) for c in group),
                           'balance_observations': sum(len(c['balance_observations']) for c in group),
                           'balance_packages': sum(bool(c['balance_observations']) for c in group)}
                    for role in sorted({c['collection_role'] for c in cards})
                    for group in [[c for c in cards if c['collection_role'] == role]]},
    }


def table(headers, rows):
    e = lambda v: html.escape('UNAVAILABLE' if v is None else str(v))
    return ("<div class='scroll'><table><thead><tr>" + ''.join('<th>' + e(h) + '</th>' for h in headers)
            + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + e(v) + '</td>' for v in row)
                                            + '</tr>' for row in rows) + '</tbody></table></div>')


def render(result):
    e = lambda v: html.escape('UNAVAILABLE' if v is None else str(v))
    cards = []
    for c in result['transactions']:
        trades = []
        deltas = []
        for f in c['silver_facts']:
            r = f['record']; event = r['event_reported']; units = r.get('token_balance_context') or {}
            trades.append([event.get('ix_name'), event.get('token_amount_raw_u64'), event.get('quote_amount_raw_u64'),
                           units.get('decimals'), r.get('quote_decimals'), f['record_sha256']])
            for role in units.get('roles', []):
                deltas.append([role.get('role'), role.get('account_key'), role.get('pre_observation_indexes'),
                               role.get('post_observation_indexes'), role.get('transaction_delta_raw_signed'),
                               role.get('delta_status')])
        balances = [[o['observation_index'], o['observation'].get('side'), o['observation'].get('account_key'),
                     o['observation'].get('amount_string'), o['observation'].get('decimals'),
                     o['observation'].get('exact_decimal_amount'), o['observation'].get('disposition')]
                    for o in c['balance_observations']]
        dx = [[d['kind'], d['outer_index'], d['inner_order'], d['mint_attribution'], d['profile'],
               d['disposition'] or d['layout_outcome'], d['reason'] or d['layout_error'] or d['full_instruction_error']]
              for d in c['diagnostics']]
        ix = [[i['outer_index'], i['inner_order'], i['kind'], i['stack_height'], i['program_id']]
              for i in c['instructions']]
        cards.append(f"""<section class='transaction' id='tx-{e(c['package_id'])}'>
<h2>Slot {e(c['slot'])} · transactie {e(c['transaction_index'])} · {e(c['transaction_status'])}</h2>
<p>{e(c['collection_role'])} · {e(c['slice_class'])}</p>
<p><b>Opnamegrond:</b> {e(', '.join(k for k, v in c['inclusion'].items() if v))}.
Mintverwijzingen op message-key-index: {e(c['message_mint_key_indexes'])} — geen bewijs van een trade of accountrol.</p>
<p>Transactiefout: {e(c['transaction_error'])}; Bronze: {e(c['bronze_disposition'])}. Eén atomair package.</p>
<h3>Toegelaten mintfeiten: {len(trades)}</h3>{table(['Soort','Event token raw','Event quote raw','Base decimals','Quote decimals','Silver SHA256'], trades)}
<h3>Opgenomen mintbalansen: {len(balances)}</h3>{table(['Observatie-index','Zijde','Account','Amount-string','Decimals','Exacte Rust-weergave','Status'], balances)}
<p>Balanscollectie: {e(c['balance_collection_status'])}. Leeg/afwezig is geen nul saldo.
Eventhoeveelheden, instructiegrenzen en transactiebrede Rust-balansdelta’s staan afzonderlijk in de feiten hieronder.</p>
<h3>Rust-balansdelta’s bij bestaande feiten · transactiebreed</h3>
{table(['Bronrol','Account','Pre-indices','Post-indices','Signed raw delta','Bewijsstatus'], deltas)}
<p>Dezelfde transactiebrede delta kan bij meerdere feiten als context staan; niet optellen als instructievolume.
Zonder bestaande Rust-binding blijft de delta UNAVAILABLE.</p>
<details><summary>Instructievolgorde ({len(ix)} referenties in dit package)</summary>{table(['Outer','Inner','Soort','Hoogte','Programma'], ix)}<pre>{e(json.dumps(c['instruction_inventory']))}</pre></details>
<details><summary>Diagnoses en afwijzingen ({len(dx)}; geen extra transacties)</summary>
<p>UNKNOWN wordt niet aan de mint toegeschreven. Structurele layoutafwijzing is geen on-chain transactiefout of afwijzing door elk ander profiel.</p>
{table(['Soort','Outer','Inner','Mintbinding','Profiel','Uitkomst','Reden'], dx)}</details>
<details><summary>Volledige kaart: feiten, delta’s, bewijsverwijzingen en onbekenden</summary><pre>{e(json.dumps(c, indent=2, ensure_ascii=False))}</pre></details>
</section>""")
    counts = result['counts']
    return f"""<!doctype html><html lang='nl'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>
<title>B5 · Minttijdlijn</title><style>
body{{margin:0;background:#101821;color:#e4edf5;font:15px system-ui}}main{{max-width:1450px;margin:auto;padding:28px}}
h1{{font-size:30px}}h2{{font-size:21px}}p{{line-height:1.6}}section{{background:#192531;border:1px solid #314457;border-radius:10px;padding:20px;margin:18px 0}}
a,summary,th{{color:#8ae0cf}}.warn{{color:#ffd093}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}
.scroll{{overflow:auto}}table{{border-collapse:collapse;white-space:nowrap;width:100%}}td,th{{padding:9px;border-bottom:1px solid #314457;text-align:left}}details{{margin:14px 0}}code{{overflow-wrap:anywhere}}
</style><main><p>RUST-GEAUTORISEERDE RECORDS → MANIFEST PARQUET → DUCKDB → STATISCHE TIJDLIJN</p>
<h1>Eén mint · opgenomen lifecyclefragment</h1><p><code>{e(result['mint'])}</code></p>
<p class='warn'>Post-hoc beschrijvend, geen volledige lifecycle of onderzoekssample. Eerste/laatste waarneming bewijst geen lancering, migratie of einde.
Geen geselecteerde rij bewijst geen nulactiviteit. Geen prijzen, fills of rendementen. Research Ready: false.</p>
<p><b>{e(counts['transactions'])} unieke transactiepackages · {e(counts['silver_facts'])} Silver-feiten
({e(counts['buys'])} buys / {e(counts['sells'])} sells) · {e(counts['balance_observations'])} balansobservaties.</b>
Alle geselecteerde kaarten zijn opgenomen; geen verborgen weergavelimiet. Ordening: slot/transactie en opgenomen instructiepositie, nooit acquisitie-/verwerkingstijd.</p>
<p><a href='query-results.json'>Canonieke JSON, selectie-SQL en bewijs</a> · <a href='query-execution.json'>Afzonderlijke uitvoeringsreceipt</a></p>
<section><h2>Afzonderlijke tellingen en grenzen</h2><pre>{e(json.dumps(counts, indent=2))}</pre>
<p>Instructies omvatten alle declared top-level en recorded CPI-referenties in geselecteerde packages; niet uitsluitend minttrades.
Diagnoses tellen per oorspronkelijke lijstpositie; meerdere profielen kunnen dezelfde instructie beoordelen.</p>
<pre>{e(json.dumps(result['limits'], indent=2))}</pre></section>
<section><h2>Alle geselecteerde slots en oorspronkelijke bronklassen</h2>
{table(['Slot','Bron','Rol','Opgenomen tijdlijnpackages'], [[s['slot'],s['source_id'],s['role'],sum(str(c['slot'])==str(s['slot']) for c in result['transactions'])] for s in result['collection']['selected_slots']])}
<p>Slotdekking verantwoordt Bronze-packages, niet volledige Pump-decoderdekking. De drie oorspronkelijke pilotslots blijven afzonderlijk van de zestien contextslots.</p></section>
{''.join(cards)}<section><h2>Input- en codebinding</h2><pre>{e(json.dumps(result['bindings'], indent=2))}</pre></section>
</main></html>"""


def run(root, output, mint, expected_sha):
    if not re.fullmatch(r'[1-9A-HJ-NP-Za-km-z]{32,44}', mint):
        raise ValueError('literal public mint address required')
    root = root.resolve(strict=True)
    if output.exists() or output.resolve().is_relative_to(root):
        raise ValueError('new report directory outside preserved collection required')
    manifest, digest = load_collection(root)
    if digest != expected_sha or manifest['state'] != 'COMPLETE':
        raise ValueError('expected complete collection identity required')
    sql_bytes = (HERE / 'mint-timeline.sql.json').read_bytes()
    definitions = json.loads(sql_bytes)
    started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    start = time.perf_counter()
    with connect() as db:
        collection = attach_collection(db, root, manifest)
        cards = query_timeline(db, mint, definitions)
    if load_collection(root)[1] != digest:
        raise ValueError('collection changed during read-only query')
    code = {name: sha((HERE / name).read_bytes()) for name in (
        'mint_timeline.py', 'mint-timeline.sql.json', 'collection_reader.py', 'manifest_reader.py',
        'collection_report.py', 'collection_run.py', 'query.py', 'token_balance_report.py', 'requirements.lock')}
    result = exact({'schema': 'OF1_MINT_TIMELINE_1', 'mint': mint, 'selection_class': 'POSTHOC_DESCRIPTIVE_LIFECYCLE_FRAGMENT',
                    'collection': collection, 'counts': summarize(cards), 'transactions': cards,
                    'limits': LIMITS, 'research_ready': False, 'display_complete': True,
                    'bindings': {'collection_sha256': digest, 'plan_sha256': manifest['plan_sha256'],
                                 'batches': manifest['batches'], 'code_sha256': code,
                                 'sql': definitions, 'sql_parameters': {'mint': mint},
                                 'duckdb_version': '1.5.5', 'integer_policy': 'all integers as decimal strings; null unchanged'}})
    raw = canonical(result)
    html_bytes = render(result).encode()
    if max(len(raw), len(html_bytes)) > MAX_REPORT_BYTES:
        raise ValueError('complete report exceeds 8 MiB; no truncated success published')
    execution = {'schema': 'OF1_MINT_TIMELINE_EXECUTION_1', 'started_at_utc': started_at,
                 'completed_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 'elapsed_seconds': time.perf_counter()-start, 'result_sha256': sha(raw),
                 'html_sha256': sha(html_bytes), 'collection_path': str(root),
                 'python_version': sys.version.split()[0], 'provider_calls': False,
                 'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                 'threads': 1, 'duckdb_memory_limit': '256MB', 'spill': False}
    output.mkdir()
    for name, content in [('query-results.json', raw), ('index.html', html_bytes),
                          ('query-execution.json', (json.dumps(execution, indent=2)+'\n').encode())]:
        with (output/name).open('xb') as handle:
            handle.write(content)
    print(json.dumps({'report': str(output/'index.html'), 'counts': result['counts'], **execution}))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('collection', type=pathlib.Path)
    parser.add_argument('output', type=pathlib.Path)
    parser.add_argument('--mint', required=True)
    parser.add_argument('--collection-sha256', required=True)
    args = parser.parse_args()
    process_limits()
    run(args.collection, args.output, args.mint, args.collection_sha256)
