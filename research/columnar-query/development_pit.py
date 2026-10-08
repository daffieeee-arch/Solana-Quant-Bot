"""Fixed four-window DEVELOPMENT chain-order Gold walking skeleton.

This is a presentation of native admitted facts, not historical observability,
actionability, a B7 final label, or an executable B8 baseline. No reader path or
cohort override is accepted by the CLI.
"""
import argparse
import datetime
import html
import json
import pathlib
import time

from development_cohort import PINS, PINS_BYTES, build_report, require, uint
from manifest_reader import pairs_unique, regular_bytes, sha
from mint_timeline import canonical, exact

ROOT = pathlib.Path('/home/chupa/Solana-project/data-old-faithful-one')
SOURCE = ROOT / 'governance/b7-development-cohort-20260928/report/review'
ADMISSION_SHA256 = '8dde67278b264482f099afcfaf18b8024d830715cf85c3396de84cf16aba9626'
COHORT_SHA256 = 'b8308de7160baaf5c39805d0b4956dfe119f3b4e09298e6b8a3d037e05989486'
SCHEMA = 'B8_DEVELOPMENT_PIT_WALKING_SKELETON_1'
MODEL = 'DEVELOPMENT_CHAIN_BOUNDARY_ONLY_1'
MAX_SOURCE_BYTES = 16 * 1024 * 1024


def fact_projection(fact, ordinal, collection_sha):
    record = fact['record']
    source = record['source']
    event = record['event_reported']
    require(record['transaction_status'] == 'OK' and record['atomic_observation_package'] is True,
            'one successful atomic parent')
    require(all(record[key] is None for key in ('observed_at', 'observation_model_id',
            'actionable_at', 'execution_opportunity_at', 'executable_price')),
            'do not replace unavailable historical timing or fill')
    require(record['quote_mint_identity'] == 'UNKNOWN' and record['quote_decimals'] is None,
            'quote units remain unknown')
    slot = uint(record['effective_at']['slot'])
    tx = uint(record['effective_at']['transaction_index_in_slot'])
    require(slot == uint(fact['slot']) and tx == uint(fact['transaction_index']),
            'canonical fact position')
    require(event['mint_address'] == fact['mint'] and event['is_buy'] == (fact['side'] == 'buy'),
            'original event identity')
    require(fact['package_id'] == source['run_id'] + ':' + record['bronze_record_sha256'],
            'atomic package identity')
    receipts = [receipt for receipt in source['bindings']['receipts']
                if uint(receipt['sequence']) == uint(source['receipt_sequence'])]
    require(len(receipts) == 1 and receipts[0]['raw_sha256'] == source['raw_sha256']
            and source['raw_path'].endswith(receipts[0]['path'].removesuffix('receipt.json') + 'raw.bin'),
            'exact Raw receipt/source binding')
    return {'window_ordinal': ordinal, 'mint': fact['mint'], 'side': fact['side'],
            'effective_at': {'slot': str(slot), 'transaction_index_in_slot': str(tx)},
            'fact_sha256': fact['record_sha256'], 'package_id': fact['package_id'],
            'token_amount_raw_u64': event['token_amount_raw_u64'],
            'quote_amount_raw_u64': event['quote_amount_raw_u64'],
            'quote_mint_identity': 'UNKNOWN', 'quote_decimals': None,
            'source': {'collection_sha256': collection_sha, 'part_id': fact['part_id'],
                       'run_id': source['run_id'], 'raw_path': source['raw_path'],
                       'raw_sha256': source['raw_sha256'],
                       'receipt_sequence': source['receipt_sequence'],
                       'receipt_path': receipts[0]['path'],
                       'receipt_sha256': receipts[0]['sha256'],
                       'manifest_sha256': source['bindings']['manifest_sha256'],
                       'source_evidence_sha256': record['source_evidence_sha256']}}


def project_window(window):
    sample = window['sample_identity']
    b7 = sample['b7']
    ordinal = uint(window['ordinal'])
    require(ordinal in range(4) and uint(b7['window_ordinal']) == ordinal
            and uint(b7['phase']) == 1 and b7['cohort_role'] == 'DEVELOPMENT'
            and sample['sample_class'] == 'RESEARCH_SAMPLING'
            and b7['selection_sha256'] == PINS['selection_sha256'], 'four fixed DEVELOPMENT roles')
    start, end = uint(sample['start_slot']), uint(sample['end_slot_exclusive'])
    require([start, end] == PINS['windows'][ordinal]['range'] and end - start == 16,
            'fixed window range')
    midpoint = start + 8
    rows = [fact_projection(f, ordinal, window['collection_sha256'])
            for mint in window['mints'] for f in mint['facts']]
    rows.sort(key=lambda row: (int(row['effective_at']['slot']),
                               int(row['effective_at']['transaction_index_in_slot']),
                               row['fact_sha256']))
    require(len(rows) == uint(window['counts']['silver_facts'])
            and len({row['fact_sha256'] for row in rows}) == len(rows), 'all original facts exactly once')
    package_positions = {}
    roles = {'EARLIER_BUY_CANDIDATE': 0, 'EARLIER_OTHER_FACT': 0,
             'LATER_SELL_OUTCOME_EVIDENCE': 0, 'LATER_OTHER_FACT': 0}
    for row in rows:
        slot = int(row['effective_at']['slot'])
        tx = int(row['effective_at']['transaction_index_in_slot'])
        require(start <= slot < end, 'fact in selected window')
        position = (slot, tx)
        prior = package_positions.setdefault(row['package_id'], position)
        require(prior == position, 'one atomic parent cannot span chain positions')
        if slot < midpoint:
            role = 'EARLIER_BUY_CANDIDATE' if row['side'] == 'buy' else 'EARLIER_OTHER_FACT'
        else:
            role = 'LATER_SELL_OUTCOME_EVIDENCE' if row['side'] == 'sell' else 'LATER_OTHER_FACT'
        row['pit_role'] = role
        roles[role] += 1
    halves = window['halves']
    require(roles['EARLIER_BUY_CANDIDATE'] == uint(halves['FIRST_8']['buy_facts'])
            and roles['EARLIER_OTHER_FACT'] == uint(halves['FIRST_8']['sell_facts'])
            and roles['LATER_SELL_OUTCOME_EVIDENCE'] == uint(halves['LAST_8']['sell_facts'])
            and roles['LATER_OTHER_FACT'] == uint(halves['LAST_8']['buy_facts']),
            'first/last-half fact counts')
    by_hash = {row['fact_sha256']: row for row in rows}
    witnesses = []
    for mint in window['mints']:
        pair = mint['observed_pair']
        if pair is None:
            continue
        buy = by_hash[pair['buy_fact_sha256']]
        sell = by_hash[pair['sell_fact_sha256']]
        require(buy['pit_role'] == 'EARLIER_BUY_CANDIDATE'
                and sell['pit_role'] == 'LATER_SELL_OUTCOME_EVIDENCE'
                and buy['mint'] == sell['mint'] == mint['mint']
                and buy['package_id'] != sell['package_id']
                and buy['package_id'] == pair['buy_package_id']
                and sell['package_id'] == pair['sell_package_id'], 'positive witness/atomic separation')
        witnesses.append({'mint': mint['mint'], 'earlier_buy_fact_sha256': buy['fact_sha256'],
                          'later_sell_fact_sha256': sell['fact_sha256'],
                          'earlier_package_id': buy['package_id'],
                          'later_package_id': sell['package_id']})
    require(len(witnesses) == uint(window['observed_pair_mints']), 'unchanged pair witnesses')
    witness_by_mint = {witness['mint']: witness for witness in witnesses}
    snapshots = []
    for mint in sorted({row['mint'] for row in rows if row['pit_role'] == 'EARLIER_BUY_CANDIDATE'}):
        earlier = [row['fact_sha256'] for row in rows
                   if row['mint'] == mint and row['pit_role'] == 'EARLIER_BUY_CANDIDATE']
        later = [row['fact_sha256'] for row in rows
                 if row['mint'] == mint and row['pit_role'] == 'LATER_SELL_OUTCOME_EVIDENCE']
        snapshots.append({'mint': mint, 'chain_earlier_buy_candidate_hashes': earlier,
                          'historically_known_at_boundary': None,
                          'later_sell_label_only_hashes': later,
                          'observed_positive_witness': witness_by_mint.get(mint),
                          'negative_state': 'NOT_APPLICABLE_POSITIVE_WITNESS' if mint in witness_by_mint
                                            else 'UNAVAILABLE_INCOMPLETE_SEMANTIC_COVERAGE'})
    used_parts = sorted({uint(row['source']['part_id']) for row in rows})
    parts = []
    for part_id in used_parts:
        part = window['parts'][part_id]
        require(uint(part['part_id']) == part_id, 'part identity')
        parts.append({'part_id': str(part_id), 'source_id': part['source_id'],
                      'parquet_manifest_path': part['parquet_manifest_path'],
                      'parquet_manifest_sha256': part['parquet_manifest_sha256'],
                      'decoder_source_sha256': part['decoder_source_sha256'],
                      'decoder_binary_sha256': part['decoder_binary_sha256'],
                      'writer': {'version': part['writer']['version'],
                                 'source_sha256': part['writer']['source_sha256'],
                                 'executable_sha256': part['writer']['executable_sha256']}})
    return {'ordinal': ordinal, 'sample_identity': sample,
    'source': {'collection_path': window['collection_path'],
                       'collection_sha256': window['collection_sha256'],
                       'layers': window['layers'], 'used_parts': parts,
                       'full_part_inventory': 'BOUND_BY_ORIGINAL_NATIVE_ADMISSION_AND_COLLECTION_HASH'},
            'counts': window['counts'], 'coverage': window['coverage'],
            'decision_boundary': {'model_id': MODEL, 'after_complete_slots': [str(start), str(midpoint)],
                                  'before_slot': str(midpoint), 'historical_decision_at': None,
                                  'status': 'PROPOSED_CHAIN_ORDER_ONLY_NOT_HISTORICALLY_ACTIONABLE'},
            'role_counts': roles, 'atomic_package_count_with_admitted_facts': len(package_positions),
            'facts': rows, 'candidate_mint_snapshots': snapshots,
            'observed_positive_witnesses': witnesses,
            'causal_evidence': {'observed_at': None, 'observation_model_id': None,
                                'actionable_at': None, 'decision_at': None,
                                'latency_model_id': None, 'execution_opportunity_at': None,
                                'executable_fill': None,
                                'status': 'UNAVAILABLE_NO_HISTORICAL_INFORMATION_OR_EXECUTION_EVIDENCE'},
            'negative_conclusion': 'UNAVAILABLE_SEMANTIC_COVERAGE_NOT_ESTABLISHED'}


def build_pit(cohort, admission_sha256, cohort_sha256):
    require(cohort['schema'] == 'OF1_B7_DEVELOPMENT_COHORT_1'
            and cohort['pins_sha256'] == sha(PINS_BYTES)
            and cohort['selection_sha256'] == PINS['selection_sha256']
            and cohort['state'] == 'READY' and cohort['evaluation_access'] == 'DENIED'
            and cohort['research_ready'] is False
            and len(cohort['windows']) == 4, 'fixed admitted DEVELOPMENT snapshot')
    for ordinal, window in enumerate(cohort['windows']):
        pin = PINS['windows'][ordinal]
        require(window['collection_sha256'] == pin['sha256']
                and window['layers'] == exact(pin['layers'])
                and window['counts'] == exact(pin['counts']), 'fixed manifest/layer/count snapshot')
    windows = [project_window(w) for w in cohort['windows']]
    require([w['ordinal'] for w in windows] == [0, 1, 2, 3], 'window order')
    totals = {key: str(sum(uint(w['counts'][key]) for w in windows))
              for key in ('blocks', 'packages', 'failures', 'silver_facts')}
    require(totals == cohort['totals'] == {'blocks': '64', 'packages': '80541',
            'failures': '9505', 'silver_facts': '122'}, 'complete fixed four-window totals')
    require(cohort['native_admission_sha256'] == admission_sha256, 'native admission hash')
    return exact({'schema': SCHEMA, 'scope': 'DEVELOPMENT_ORDINALS_0_TO_3_ONLY',
                  'research_ready': False, 'b7_complete': False, 'b8_accepted': False,
                  'evaluation_access': 'DENIED', 'source': {'native_admission_sha256': admission_sha256,
                     'cohort_report_sha256': cohort_sha256, 'pins_sha256': sha(PINS_BYTES),
                     'selection_sha256': PINS['selection_sha256'],
                     'native_reader_source_sha256': cohort['producer']['native_source_sha256'],
                     'native_reader_binary_sha256': cohort['producer']['native_binary_sha256']},
                  'producer': {'version': SCHEMA, 'python_sha256': sha(pathlib.Path(__file__).read_bytes())},
                  'totals': totals, 'windows': windows,
                  'interpretation': 'Four already inspected DEVELOPMENT windows have source-bound admitted positive witnesses. Chain-earlier facts are candidate inputs only; historical knowledge, actionability, execution and a held-out baseline assessment are unavailable. No effect, return or exit is estimated.',
                  'window_order': 'PREREGISTERED_ORDINAL_NOT_HISTORICAL_TIME',
                  'clock_rule': 'acquired_at/processed_at/report clocks are operational provenance, never features or labels'})


def html_report(report):
    esc = lambda value: html.escape(str(value), quote=True)
    overview, facts, candidates = [], [], []
    for w in report['windows']:
        c = w['counts']; r = w['role_counts']
        overview.append('<tr>' + ''.join(f'<td>{esc(v)}</td>' for v in
            (w['ordinal'], f"{w['sample_identity']['start_slot']}–{w['sample_identity']['end_slot_exclusive']}",
             w['decision_boundary']['before_slot'], c['packages'], c['failures'], c['silver_facts'],
             r['EARLIER_BUY_CANDIDATE'], r['LATER_SELL_OUTCOME_EVIDENCE'],
             len(w['observed_positive_witnesses']))) + '</tr>')
        for f in w['facts']:
            facts.append('<tr>' + ''.join(f'<td>{esc(v)}</td>' for v in
                (w['ordinal'], f['effective_at']['slot'], f['effective_at']['transaction_index_in_slot'],
                 f['mint'], f['side'], f['pit_role'], f['token_amount_raw_u64'],
                 f['fact_sha256'], f['package_id'], f['source']['manifest_sha256'])) + '</tr>')
        for mint in w['candidate_mint_snapshots']:
            candidates.append('<tr>' + ''.join(f'<td>{esc(v)}</td>' for v in
                (w['ordinal'], mint['mint'], ', '.join(mint['chain_earlier_buy_candidate_hashes']),
                 ', '.join(mint['later_sell_label_only_hashes']) or 'UNAVAILABLE',
                 'OBSERVED_ADMITTED_PAIR' if mint['observed_positive_witness'] else 'UNAVAILABLE')) + '</tr>')
    return ('<!doctype html><html lang="nl"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<title>B8 DEVELOPMENT · ketengrens</title><style>body{font:16px system-ui;max-width:88rem;margin:auto;padding:1rem}'
            'section{overflow-x:auto}table{border-collapse:collapse;width:100%}td,th{border:1px solid #777;padding:.45rem;text-align:left}'
            'code{overflow-wrap:anywhere}th{background:#eee}</style><h1>DEVELOPMENT · point-in-time walking skeleton</h1>'
            '<p>Alleen w00–w03. De grens na acht slots is een voorgestelde ketengrens, geen bewezen historisch beslismoment. '
            'Eerdere buys zijn kandidaat-invoer; latere sells zijn uitsluitend uitkomstbewijs. Een event is geen uitvoerbare fill. '
            'Historische observed_at, actionable_at, decision_at, latency en execution_opportunity_at: <strong>UNAVAILABLE</strong>. '
            'Vier bekeken vensters zijn geen onaangeroerde holdout of baselinebeoordeling. Research Ready: false.</p>'
            '<p>Native admission <code>' + esc(report['source']['native_admission_sha256']) + '</code>; selectie <code>'
            + esc(report['source']['selection_sha256']) + '</code>.</p><section><table><caption>Vensters en beslisgrens</caption>'
            '<thead><tr><th>Ordinal</th><th>Slots [start,end)</th><th>Eerste latere slot</th><th>Packages</th><th>Failures</th>'
            '<th>Silver-feiten</th><th>Eerdere buys</th><th>Latere sells</th><th>Positieve paarmints</th></tr></thead><tbody>'
            + ''.join(overview) + '</tbody></table></section><h2>Kandidaat-mints op de ketengrens</h2><section><table>'
            '<thead><tr><th>Ordinal</th><th>Mint</th><th>Eerdere buy-hashes</th><th>Latere sell-hashes: label-only</th><th>Paarbewijs</th></tr></thead><tbody>'
            + ''.join(candidates) + '</tbody></table></section><h2>Alle toegelaten feiten in ketenvolgorde</h2><section><table>'
            '<thead><tr><th>Ordinal</th><th>Slot</th><th>Tx</th><th>Mint</th><th>Kant</th><th>PIT-rol</th><th>Token raw</th>'
            '<th>Feithash</th><th>Atomair package</th><th>Manifest</th></tr></thead><tbody>' + ''.join(facts)
            + '</tbody></table></section><p><a href="report.json">JSON</a> bevat exacte bron-, receipt-, part- en manifestbindingen '
            'en expliciete ontbrekende velden. Operationele uitvoeringsklok staat apart.</p></html>\n').encode()


def produce(output):
    output = pathlib.Path(output)
    require(output.is_absolute() and output.resolve() == output
            and output.is_relative_to(ROOT / 'governance')
            and output.parent.is_dir() and not output.exists(), 'new private output directory')
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    t0 = time.monotonic()
    code_sha256 = sha(pathlib.Path(__file__).read_bytes())
    admission_bytes = regular_bytes(SOURCE / 'admission.json', MAX_SOURCE_BYTES)
    cohort_bytes = regular_bytes(SOURCE / 'cohort.json', MAX_SOURCE_BYTES)
    require(sha(admission_bytes) == ADMISSION_SHA256 and sha(cohort_bytes) == COHORT_SHA256,
            'exact original four-window snapshots only')
    admission = json.loads(admission_bytes, object_pairs_hook=pairs_unique)
    cohort = json.loads(cohort_bytes, object_pairs_hook=pairs_unique)
    rebuilt = build_report(admission)
    require({k: v for k, v in rebuilt.items() if k != 'producer'}
            == {k: v for k, v in cohort.items() if k != 'producer'},
            'native facts agree with sealed cohort apart from historical producer code hash')
    report = build_pit(cohort, ADMISSION_SHA256, COHORT_SHA256)
    require(report['producer']['python_sha256'] == code_sha256
            and sha(pathlib.Path(__file__).read_bytes()) == code_sha256,
            'producer code changed during report')
    raw = canonical(report)
    require(len(raw) <= 4 * 1024 * 1024, 'bounded Gold skeleton')
    page = html_report(report)
    require(len(page) <= 2 * 1024 * 1024, 'bounded static presentation')
    output.mkdir()
    for name, data in [('report.json', raw), ('report.html', page)]:
        with (output / name).open('xb') as f:
            f.write(data)
    execution = {'schema': 'B8_DEVELOPMENT_PIT_EXECUTION_1',
                 'report_sha256': sha(raw), 'html_sha256': sha(page),
                 'source_admission_sha256': ADMISSION_SHA256, 'source_cohort_sha256': COHORT_SHA256,
                 'producer_python_sha256': report['producer']['python_sha256'],
                 'started_at_utc': started, 'elapsed_ms': str(int((time.monotonic() - t0) * 1000)),
                 'clocks_are_operational_only': True, 'network_requests': 0,
                 'evaluation_access': 'DENIED', 'research_ready': False}
    with (output / 'execution.json').open('xb') as f:
        f.write(canonical(execution))
    print(json.dumps({'path': str(output), 'report_sha256': sha(raw), 'totals': report['totals']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('new_private_output_directory')
    args = parser.parse_args()
    produce(args.new_private_output_directory)
