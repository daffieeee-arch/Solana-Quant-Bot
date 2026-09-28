"""Descriptive aggregation of the single native four-window capability.

No Raw decoder, Parquet discovery, admission override, labels or evaluation.
"""
import argparse
import datetime
import json
import pathlib
import re
import subprocess
import time

from manifest_reader import pairs_unique, regular_bytes, sha
from mint_timeline import canonical, exact

HERE = pathlib.Path(__file__).resolve().parent
PINS_PATH = HERE.parents[1] / 'rust/of1-bronze-decoder/sources/b7-development-cohort.json'
PINS_BYTES = regular_bytes(PINS_PATH, 16384)
PINS = json.loads(PINS_BYTES, object_pairs_hook=pairs_unique)
ROOT = pathlib.Path('/home/chupa/Solana-project/data-old-faithful-one')
MAX_BYTES = 16 * 1024 * 1024


def require(condition, reason):
    if not condition:
        raise ValueError('DEVELOPMENT_COHORT_INVALID: ' + reason)


def uint(value):
    require(type(value) is int or isinstance(value, str) and re.fullmatch('0|[1-9][0-9]{0,19}', value),
            'native exact unsigned integer')
    require(0 <= int(value) < 2**64, 'native unsigned range')
    return int(value)


def digest(value):
    require(isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value), 'hash')
    return value


def summarize_window(window):
    """One observation unit. Preserve native fact order and atomic parent IDs."""
    sample = window['sample_identity']
    start, end = uint(sample['start_slot']), uint(sample['end_slot_exclusive'])
    require(end - start == 16, 'fixed sixteen-slot window')
    midpoint = start + 8
    halves = {name: {'buy_facts': 0, 'sell_facts': 0, 'mints': set()} for name in ('FIRST_8', 'LAST_8')}
    seen, previous, mints, facts = set(), None, {}, []
    for f in window['facts']:
        record = f['record']
        raw = f['canonical_record_json']
        require(isinstance(raw, str) and sha(raw.encode()) == digest(f['record_sha256']), 'original fact hash')
        require(json.loads(raw, object_pairs_hook=pairs_unique) == record, 'native record projection')
        require(f['record_sha256'] not in seen, 'duplicate fact')
        seen.add(f['record_sha256'])
        require(record['sample_identity'] == sample and record['source']['bindings']['sample_identity'] == sample,
                'record/source sample binding')
        require(record['transaction_status'] == 'OK' and record['atomic_observation_package'] is True,
                'successful atomic parent required')
        slot = uint(record['effective_at']['slot'])
        tx = uint(record['effective_at']['transaction_index_in_slot'])
        require(start <= slot < end and (previous is None or previous <= (slot, tx)), 'canonical window order')
        previous = (slot, tx)
        event = record['event_reported']
        mint = event['mint_address']
        require(isinstance(mint, str) and re.fullmatch('[1-9A-HJ-NP-Za-km-z]{32,44}', mint), 'mint identity')
        require(type(event['is_buy']) is bool, 'buy/sell must be explicit')
        # Validate presence/type; zero is a real observation, not missing.
        for name in ('token_amount_raw_u64', 'quote_amount_raw_u64'):
            value = event[name]
            require(isinstance(value, str) and re.fullmatch('0|[1-9][0-9]{0,19}', value)
                    and int(value) < 2**64, 'exact event quantity')
        parent = digest(record['bronze_record_sha256'])
        package = record['source']['run_id'] + ':' + parent
        half = 'FIRST_8' if slot < midpoint else 'LAST_8'
        side = 'buy' if event['is_buy'] else 'sell'
        halves[half][side + '_facts'] += 1
        halves[half]['mints'].add(mint)
        require(type(f['part_id']) is int and 0 <= f['part_id'] < len(window['parts']), 'part reference')
        row = {**f, 'mint': mint, 'side': side, 'half': half, 'slot': slot,
               'transaction_index': tx, 'package_id': package}
        facts.append(row)
        mints.setdefault(mint, []).append(row)
    require(len(facts) == window['counts']['silver_facts'], 'complete fact inventory')
    mint_rows = []
    for mint, rows in sorted(mints.items()):
        buys = [f for f in rows if f['half'] == 'FIRST_8' and f['side'] == 'buy']
        sells = [f for f in rows if f['half'] == 'LAST_8' and f['side'] == 'sell']
        pair = next(({'buy_fact_sha256': b['record_sha256'], 'sell_fact_sha256': s['record_sha256'],
                      'buy_package_id': b['package_id'], 'sell_package_id': s['package_id']}
                     for b in buys for s in sells if b['package_id'] != s['package_id']), None)
        mint_rows.append({'mint': mint, 'facts': rows, 'observed_pair': pair,
                          'pair_state': 'OBSERVED_ADMITTED_PAIR' if pair else 'NO_PAIR_IN_ADMITTED_FACTS',
                          'negative_conclusion': 'UNAVAILABLE_SEMANTIC_COVERAGE_NOT_ESTABLISHED'})
    return {**{k: v for k, v in window.items() if k != 'facts'}, 'halves': {
                name: {'buy_facts': h['buy_facts'], 'sell_facts': h['sell_facts'],
                       'unique_mints': len(h['mints'])} for name, h in halves.items()},
            'mint_count': len(mint_rows), 'mints': mint_rows,
            'observed_pair_mints': sum(m['observed_pair'] is not None for m in mint_rows),
            'semantic_coverage': {'state': 'UNPROVEN', 'negative_conclusion_allowed': False,
                'reason': 'Bronze completeness and admitted facts do not prove exhaustive Pump instruction/event coverage.'},
            'missing_information': ['Unknown quote identity/decimals; no named-currency volume or price.',
                'Account roles, actual CPI privileges, historical activation and verified account state remain unproven.',
                'Creation, completion, migration and full lifetime are not established.',
                'Historical information availability and execution opportunity are unavailable.'],
            'unit': 'FIXED_WINDOW', 'midpoint_slot': midpoint}


def native_bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode()


def build_report(admission):
    require(admission['schema'] == 'OF1_B7_DEVELOPMENT_ADMISSION_1'
            and admission['pins_sha256'] == sha(PINS_BYTES)
            and admission['selection_sha256'] == PINS['selection_sha256']
            and admission['research_ready'] is False, 'native capability identity')
    digest(admission['reader_source_sha256']); digest(admission['reader_binary_sha256'])
    require(len(admission['windows']) == 4, 'exact four-window set')
    results, hashes = [], set()
    for window, pin in zip(admission['windows'], PINS['windows'], strict=True):
        sample = window['sample_identity']; b7 = sample['b7']
        require(window['ordinal'] == pin['ordinal'] and window['collection_sha256'] == pin['sha256']
                and window['layers'] == pin['layers'] and window['counts'] == pin['counts'], 'immutable snapshot')
        require(sample['schema'] == 'OF1_B7_WINDOW_SAMPLE_1' and sample['sample_class'] == 'RESEARCH_SAMPLING'
                and b7['cohort_role'] == 'DEVELOPMENT' and b7['window_ordinal'] == pin['ordinal']
                and b7['phase'] == 1 and b7['selection_sha256'] == PINS['selection_sha256']
                and [sample['start_slot'], sample['end_slot_exclusive']] == pin['range'], 'role/range/selection')
        require(window['coverage']['all_selected_slots_accounted'] is True
                and window['coverage']['missing_selected_raw_slots'] == 0, 'verified coverage')
        result = summarize_window(window)
        for mint in result['mints']:
            for fact in mint['facts']:
                require(fact['record_sha256'] not in hashes, 'fact repeated across windows')
                hashes.add(fact['record_sha256'])
        results.append(result)
    totals = {k: sum(w['counts'][k] for w in results) for k in ('blocks', 'packages', 'failures', 'silver_facts')}
    return exact({'schema': 'OF1_B7_DEVELOPMENT_COHORT_1', 'state': 'READY', 'research_ready': False,
        'selection_sha256': PINS['selection_sha256'], 'pins_sha256': sha(PINS_BYTES),
        'native_admission_sha256': sha(native_bytes(admission)),
        'producer': {'version': 'DEVELOPMENT_DESCRIPTIVE_PAIRS_1', 'python_sha256': sha(pathlib.Path(__file__).read_bytes()),
                     'native_source_sha256': admission['reader_source_sha256'], 'native_binary_sha256': admission['reader_binary_sha256']},
        'totals': totals, 'windows': results,
        'interpretation': 'Descriptive admitted observations only; no B8 labels, recurrence estimate or sufficiency verdict.',
        'window_order': 'PREREGISTERED_ORDINAL_NOT_HISTORICAL_TIME', 'evaluation_access': 'DENIED'})


def produce(reader, binary_sha256, output):
    reader = pathlib.Path(reader)
    require(reader.is_absolute() and reader.resolve() == reader and reader.is_file(), 'literal native executable')
    require(sha(reader.read_bytes()) == digest(binary_sha256), 'native binary pin')
    output = pathlib.Path(output)
    require(output.is_absolute() and output.resolve() == output and output.is_relative_to(ROOT)
            and not output.exists() and output.parent.is_dir(), 'new external output directory')
    started = datetime.datetime.now(datetime.timezone.utc).isoformat(); t0 = time.monotonic()
    native = subprocess.run([str(reader), 'development-cohort'], check=True, capture_output=True, timeout=900)
    require(len(native.stdout) <= MAX_BYTES and len(native.stderr) <= 256 * 1024, 'bounded native output')
    require(sha(reader.read_bytes()) == binary_sha256, 'binary changed during execution')
    admission = json.loads(native.stdout, object_pairs_hook=pairs_unique)
    require(admission['reader_binary_sha256'] == binary_sha256, 'native execution identity')
    report = build_report(admission)
    raw = canonical(report)
    require(len(raw) <= MAX_BYTES, 'bounded report')
    output.mkdir()
    for name, data in [('admission.json', native_bytes(admission)), ('cohort.json', raw)]:
        with (output / name).open('xb') as f: f.write(data)
    receipt = {'schema': 'OF1_B7_COHORT_EXECUTION_1', 'report_sha256': sha(raw),
               'admission_sha256': sha(native_bytes(admission)), 'reader_binary_sha256': binary_sha256,
               'started_at_utc': started, 'elapsed_seconds': time.monotonic() - t0,
               'clocks_are_operational_only': True, 'research_ready': False}
    with (output / 'execution.json').open('xb') as f: f.write(native_bytes(receipt))
    print(json.dumps({'path': str(output), 'sha256': sha(raw), 'totals': report['totals']}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('reader'); p.add_argument('binary_sha256'); p.add_argument('output')
    a = p.parse_args(); produce(a.reader, a.binary_sha256, a.output)
