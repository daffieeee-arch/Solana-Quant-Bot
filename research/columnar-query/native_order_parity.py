#!/usr/bin/env python3
"""Three bounded native Rust executions; Python verifies/presents, never decodes wire."""
import argparse
import copy
import hashlib
import html
import json
import pathlib
import subprocess
import time

from collection_reader import load_collection
from manifest_reader import attach_dataset, load_manifest, regular_bytes
from query import connect

ORDERS = ('canonical', 'reverse', 'odd-even')
COLLECTION = '39b4856b9f5b942ef263d7bda142968b5f645a0706f07a9353c59b55babba1ab'
MAX_OUTPUT = 4 * 1024**3


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        while data := f.read(1024 * 1024):
            h.update(data)
    return h.hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def seal(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def source_inventory(root):
    # Immutable local run only. No discovery of datasets outside this explicit root.
    files = sorted(p for p in root.rglob('*') if p.is_file())
    require(len(files) <= 2000 and all(not p.is_symlink() for p in files), 'source inventory bound')
    require(sum(p.stat().st_size for p in files) <= 128 * 1024**2, 'source byte bound')
    return {str(p.relative_to(root)): digest(p) for p in files}


def process(command, directory):
    with (directory / 'process.log').open('xb') as log:
        started = time.time()
        result = subprocess.run(['prlimit', '--as=2147483648', '--cpu=180', '--fsize=268435456', '--', *map(str, command)],
                                stdout=log, stderr=subprocess.STDOUT, timeout=240, check=False)
    require(result.returncode == 0, f'native process failed ({result.returncode}); see {directory / "process.log"}')
    return {'command': list(map(str, command)), 'started_at_unix': started,
            'elapsed_seconds': time.time() - started, 'log_sha256': digest(directory / 'process.log')}


def normalized(record, execution, parents, layer):
    """Only explain provenance changes; all domain fields compare exactly.

    New decoder code and a migrated run location change full record hashes. Both
    parent hashes are independently checked before replacing them with the full
    normalized Bronze identity. No arbitrary key or protocol field is removed.
    """
    require(record['decoder_source_sha256'] == execution['decoder_source_sha256'], 'decoder identity mismatch')
    r = copy.deepcopy(record)
    source = r['source']
    relative = pathlib.Path(source['raw_path']).relative_to(execution['run_root'])
    require(str(relative) == f"published/{int(source['receipt_sequence']):010}/raw.bin", 'raw path binding')
    source['raw_path'] = str(relative)
    r['decoder_source_sha256'] = 'COMPARED_SEPARATELY_IN_EXECUTION'
    if layer == 'silver':
        parent = parents.get(r['bronze_record_sha256'])
        require(parent is not None and parent['status'] == 'OK', 'Silver parent missing or failed')
        require(record['source'] == parent['source'], 'Silver source differs from exact Bronze parent')
        require(r['token_balance_context']['bronze_record_sha256'] == r['bronze_record_sha256'], 'balance parent mismatch')
        r['bronze_record_sha256'] = parent['semantic_hash']
        r['token_balance_context']['bronze_record_sha256'] = parent['semantic_hash']
    return seal(r)


def inspect_dataset(root, expected_source=None):
    manifest, manifest_sha = load_manifest(root)
    execution = manifest['input']['execution']
    parents, keys, identities, semantics = {}, [], [], {'bronze': [], 'silver': []}
    counts = {'packages': 0, 'failed_transactions': 0, 'silver_facts': 0, 'silver_from_failed': 0,
              'top_level_instructions': 0, 'recorded_cpi_instructions': 0}
    statuses, dispositions, bindings = {}, {}, None
    with connect() as db:
        attach_dataset(db, root, manifest)  # Rehash actual Rust-written files/records/logical chains.
        for layer in ('bronze', 'silver'):
            cursor = db.execute(f'SELECT record_bytes, record_sha256 FROM {layer} ORDER BY record_ordinal')
            while row := cursor.fetchone():
                raw, record_sha = row
                record = json.loads(raw)
                require(record['atomic_observation_package'] is True, 'non-atomic package')
                source = record['source']
                if bindings is None:
                    bindings = source['bindings']
                require(source['bindings'] == bindings, 'mixed source bindings')
                if expected_source is not None:
                    receipt = next((x for x in bindings['receipts'] if x['sequence'] == source['receipt_sequence']), None)
                    require(receipt is not None, 'missing receipt')
                    relative = f"published/{int(source['receipt_sequence']):010}"
                    require(expected_source[relative+'/raw.bin'] == source['raw_sha256'] == receipt['raw_sha256'], 'raw hash binding')
                    require(expected_source[relative+'/receipt.json'] == receipt['sha256'], 'receipt hash binding')
                semantic = normalized(record, execution, parents, layer)
                semantics[layer].append(semantic)
                if layer == 'bronze':
                    require(record_sha not in parents, 'duplicate Bronze parent')
                    tx = record['transaction']
                    status = tx.get('status', 'UNAVAILABLE') if tx is not None else 'UNAVAILABLE'
                    parents[record_sha] = {'semantic_hash': semantic, 'status': status, 'source': source}
                    e = record['effective_at']
                    key = [e['slot'], e['transaction_index_in_slot'], source['receipt_sequence'], source['raw_sha256'], source['transaction_node_cid_hex']]
                    identities.append(key)
                    keys.append((int(e['slot']), int(e['transaction_index_in_slot'])))
                    counts['packages'] += 1
                    counts['failed_transactions'] += status == 'ERROR'
                    counts['top_level_instructions'] += len(tx.get('instructions', [])) if tx else 0
                    counts['recorded_cpi_instructions'] += len(tx.get('inner_instructions') or []) if tx else 0
                    statuses[status] = statuses.get(status, 0) + 1
                    state = record['disposition']
                    dispositions[state] = dispositions.get(state, 0) + 1
                else:
                    counts['silver_facts'] += 1
                    require(record['transaction_status'] == 'OK', 'non-successful Silver fact')
    require(keys == sorted(set(keys)), 'non-canonical or duplicate transaction positions')
    return {'manifest_sha256': manifest_sha, 'counts': counts, 'statuses': statuses, 'dispositions': dispositions,
            'canonical_inputs': identities, 'semantic_record_hashes': semantics, 'bindings': bindings,
            'sample_identity': manifest['sample_identity'], 'selection': manifest['selection'],
            'logical_hashes': {k: manifest['layers'][k]['ordered_logical_sha256'] for k in ('bronze', 'silver')},
            'physical_files': {k: {'sha256': v['sha256'], 'bytes': v['bytes']} for k, v in manifest['files'].items()},
            'decoder_identity': {k: execution[k] for k in ('decoder_source_sha256','executable_sha256','lock_sha256')},
            'execution_receipt_sha256': manifest['input']['execution_sha256'], 'evidence': manifest['evidence'], 'writer': manifest['writer']}


def reference(collection):
    manifest, sha = load_collection(collection)
    require(sha == COLLECTION, 'unexpected reference collection')
    batches = [b for b in manifest['batches'] if b['source_id'] == 'pilot' and b['selected_slots'][0] in range(422669516, 422669519)]
    require([b['selected_slots'] for b in batches] == [[422669516], [422669517], [422669518]], 'reference pilot selection')
    summaries = []
    for b in batches:
        path = collection / b['parquet_manifest_path']
        require(digest(path) == b['parquet_manifest_sha256'], 'reference child binding')
        summaries.append(inspect_dataset(path.parent))
    counts = {k: sum(s['counts'][k] for s in summaries) for k in summaries[0]['counts']}
    require((counts['packages'], counts['failed_transactions'], counts['silver_facts']) == (3224, 223, 7), 'reference counts changed')
    return {'collection_sha256': sha, 'children': [s['manifest_sha256'] for s in summaries], 'counts': counts,
            'canonical_inputs': [i for s in summaries for i in s['canonical_inputs']],
            'semantic_record_hashes': {k: [h for s in summaries for h in s['semantic_record_hashes'][k]] for k in ('bronze', 'silver')},
            'bindings': summaries[0]['bindings'], 'sample_identity': summaries[0]['sample_identity']}


def render(result):
    e = lambda x: html.escape(str(x))
    rows = ''.join('<tr>'+''.join(f'<td>{e(v)}</td>' for v in [x['order'], x['counts']['packages'], x['counts']['failed_transactions'],
                     x['counts']['silver_facts'], x['logical_hashes']['bronze'], x['logical_hashes']['silver'], x['equal_to_baseline']])+'</tr>' for x in result['runs'])
    return f"""<!doctype html><html lang='nl'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>
<title>Native Rust · aanlevervolgordepariteit</title><style>body{{font:16px system-ui;margin:2rem;background:#101821;color:#e4edf5}}h1{{color:#8ae0cf}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #425266;padding:12px;text-align:left}}td{{overflow-wrap:anywhere}}.scroll{{overflow:auto}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}a{{color:#8ae0cf}}.warn{{color:#ffd093}}</style>
<h1>Dezelfde pakketten · drie aanlevervolgordes</h1><p>Rust-reader → transacties/status/Pump → canonieke atomaire pakketten → Rust Parquet. Python controleert de geschreven records; geen wiredecode.</p>
<p class='warn'>Begrensde technische pariteitstest. Geen volledige Pump-dekking, lifecycle of Research Ready. B5 blijft open/Unproven. GAP, UNAVAILABLE en QUARANTINED worden niet gelijkgesteld.</p>
<p>Volgorde vóór decode: 0…N−1; N−1…0; oneven nulgebaseerde rangen oplopend, daarna even rangen oplopend. Instructies en CPIs binnen een pakket veranderen niet.</p>
<div class='scroll'><table><tr><th>Invoer</th><th>Pakketten</th><th>Failed</th><th>Silver</th><th>Bronze logical SHA-256</th><th>Silver logical SHA-256</th><th>Gelijk</th></tr>{rows}</table></div>
<p>Feiten uit mislukte transacties: 0. Alle bron- en receiptbindingen zijn per uitvoering gecontroleerd. Volledige werkelijk doorlopen ranglijsten en fysieke hashes staan in <a href='parity.json'>parity.json</a>.</p>
<p>Gelijkheid tussen de drie nieuwe uitvoeringen is exact. De historische vergelijking houdt uitsluitend de gedeclareerde decoderidentiteit, de gemigreerde Raw-locatie en gecontroleerde afgeleide Bronze-parenthashes afzonderlijk; alle overige velden moeten exact gelijk zijn.</p>
<details><summary>Bronnen, grenzen en vergelijking</summary><pre>{e(json.dumps({k:v for k,v in result.items() if k!='runs'},indent=2))}</pre></details></html>"""


def run(source, output, decoder, projector, collection=None):
    source, decoder, projector = source.resolve(), decoder.resolve(), projector.resolve()
    require(not output.exists() and not output.resolve().is_relative_to(source), 'new output outside source required')
    before = source_inventory(source)
    previous = reference(collection) if collection else None
    output.mkdir()
    runs, operations = [], []
    for order in ORDERS:
        stage = output/order
        stage.mkdir()
        operations.append(process([decoder, source, stage/'decode', order], stage))
        ex_path = stage/'decode/execution.json'
        execution = json.loads(regular_bytes(ex_path, 1024**2))
        require(execution['executable_sha256'] == digest(decoder), 'decoder executable identity')
        writer_log = stage/'writer'
        writer_log.mkdir()
        operations.append(process([projector, stage/'decode', digest(ex_path), stage/'parquet'], writer_log))
        current = inspect_dataset(stage/'parquet', before)
        require(current['writer']['executable_sha256'] == digest(projector), 'writer executable identity')
        trace = execution['native_delivery']
        n = current['counts']['packages']
        wanted = {'canonical': list(range(n)), 'reverse': list(reversed(range(n))), 'odd-even': list(range(1,n,2))+list(range(0,n,2))}[order]
        require(trace['order'] == order and trace['delivered_canonical_ranks'] == wanted, 'delivery trace mismatch')
        require(trace['canonical_inputs'] == current['canonical_inputs'], 'delivery/package source mismatch')
        if previous:
            for key in ('counts', 'canonical_inputs', 'semantic_record_hashes', 'bindings', 'sample_identity'):
                require(current[key] == previous[key], 'historical reference mismatch: '+key)
        else:
            require(current['evidence']['receipt_evidence'] == 'Fixture' and current['evidence']['slice_class'] == 'ENGINEERING_VALIDATION_ONLY', 'only explicit engineering Fixture accepted without authentic reference')
            require(current['counts']['packages'] == 6 and current['counts']['failed_transactions'] == 1 and current['counts']['silver_facts'] == 2, 'fixture counts')
            require(current['dispositions'] == {'DECODED':3,'MISSING':1,'QUARANTINED':1,'UNSUPPORTED':1}, 'fixture state distinctions')
        if runs:
            require(current['writer'] == runs[0]['writer'], 'writer configuration changed')
            require(current['physical_files'] == runs[0]['physical_files'], 'identical writer inputs/config must retain deterministic physical bytes')
            for key in ('counts','statuses','dispositions','logical_hashes','canonical_inputs','semantic_record_hashes','bindings','sample_identity','selection'):
                require(current[key] == runs[0][key], 'permutation differs: '+key)
        require(source_inventory(source) == before, 'source changed during run')
        require(sum(p.stat().st_size for p in output.rglob('*') if p.is_file()) <= MAX_OUTPUT, 'output disk cap')
        current.update({'order':order,'delivered_canonical_ranks':wanted,'equal_to_baseline':True,
                        'physical_equal_to_baseline':not runs or current['physical_files']==runs[0]['physical_files']})
        runs.append(current)
    result = {'schema':'OF1_NATIVE_ORDER_PARITY_1','runs':runs,'source_inventory':before,'source_root':str(source),
              'reference':previous, 'reader_source_sha256':digest(pathlib.Path(__file__)),
              'decoder_executable_sha256':digest(decoder),'projector_executable_sha256':digest(projector),
              'historical_comparison_exceptions':['/decoder_source_sha256','/source/raw_path (validated run-relative location)',
                                                '/bronze_record_sha256','/token_balance_context/bronze_record_sha256 (both verified against full parent)'],
              'physical_hash_equality_required':'Same exact records, writer and partition settings; existing Rust physical determinism contract. No cross-version/partition equality claim.','research_ready':False,'b5_complete':False,
              'limitations':['No new protocol support, state transition or lifecycle evidence.', 'Actual CPI privileges and historical activation remain unavailable.',
                             'Selection completeness is not complete Pump instruction coverage.'],
              'commands':[['of1-bronze-decoder','RECORDED_RUN','NEW_DECODE',order] for order in ORDERS]}
    (output/'parity.json').write_bytes(encoded(result)+b'\n')
    (output/'index.html').write_text(render(result))
    (output/'execution.json').write_bytes(encoded({'operations':operations,'parity_sha256':digest(output/'parity.json')})+b'\n')
    (output/'COMPLETE').write_text(digest(output/'parity.json')+'\n')
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source','output','decoder','projector'): parser.add_argument(name,type=pathlib.Path)
    parser.add_argument('--reference-collection',type=pathlib.Path)
    args=parser.parse_args()
    result=run(args.source,args.output,args.decoder,args.projector,args.reference_collection)
    print(json.dumps({'result':str(args.output),'logical_hashes':result['runs'][0]['logical_hashes'],'counts':result['runs'][0]['counts']}))
