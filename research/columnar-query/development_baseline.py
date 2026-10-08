"""Fixed, offline B8 DEVELOPMENT baseline prototype over the pinned #157 PIT report."""
import argparse
import datetime
import html
import json
import pathlib
import time

from development_cohort import build_report, require, PINS
from development_pit import (ADMISSION_SHA256, COHORT_SHA256, MAX_SOURCE_BYTES,
                             ROOT, SOURCE, build_pit)
from manifest_reader import pairs_unique, regular_bytes, sha
from mint_timeline import canonical

SCHEMA = 'B8_DEVELOPMENT_BASELINE_PROTOTYPE_1'
PIT_SHA256 = '6c547cb1d3c1b28fa1b98bf869abc9eba0eaa3b933aad494581cf62a40c09420'
METHOD = pathlib.Path(__file__).with_name('b8-development-baseline-method.json')


def load_pinned_pit():
    admission_bytes = regular_bytes(SOURCE / 'admission.json', MAX_SOURCE_BYTES)
    cohort_bytes = regular_bytes(SOURCE / 'cohort.json', MAX_SOURCE_BYTES)
    require(sha(admission_bytes) == ADMISSION_SHA256 and sha(cohort_bytes) == COHORT_SHA256,
            'pinned four-window source snapshots')
    admission = json.loads(admission_bytes, object_pairs_hook=pairs_unique)
    cohort = json.loads(cohort_bytes, object_pairs_hook=pairs_unique)
    rebuilt = build_report(admission)
    require({k: v for k, v in rebuilt.items() if k != 'producer'}
            == {k: v for k, v in cohort.items() if k != 'producer'},
            'native fact and manifest admission agrees with sealed cohort')
    pit = build_pit(cohort, ADMISSION_SHA256, COHORT_SHA256)
    require(sha(canonical(pit)) == PIT_SHA256, 'exact reviewed point-in-time projection')
    return pit


def project_window(window):
    """Classify from the complete atomic #157 packages; never feed late facts to the rule."""
    first = [f for f in window['facts'] if f['pit_role'].startswith('EARLIER_')]
    later = [f for f in window['facts'] if f['pit_role'].startswith('LATER_')]
    require(len(first) + len(later) == len(window['facts']), 'each fact has one temporal role')
    boundary = int(window['decision_boundary']['before_slot'])
    require(all(int(f['effective_at']['slot']) < boundary for f in first)
            and all(int(f['effective_at']['slot']) >= boundary for f in later),
            'complete packages stay on their respective side of the boundary')
    package_positions = {}
    for fact in window['facts']:
        position = (fact['effective_at']['slot'], fact['effective_at']['transaction_index_in_slot'])
        prior = package_positions.setdefault(fact['package_id'], position)
        require(prior == position, 'one atomic package has one chain position')
    require(not ({f['package_id'] for f in first} & {f['package_id'] for f in later}),
            'an atomic package cannot cross the boundary')
    earlier_buys = [f for f in first if f['pit_role'] == 'EARLIER_BUY_CANDIDATE']
    prediction = int(bool(earlier_buys))
    witnesses = window['observed_positive_witnesses']
    by_hash = {f['fact_sha256']: f for f in window['facts']}
    require(len(by_hash) == len(window['facts']), 'fact hashes unique')
    for witness in witnesses:
        buy = by_hash[witness['earlier_buy_fact_sha256']]
        sell = by_hash[witness['later_sell_fact_sha256']]
        require(buy['pit_role'] == 'EARLIER_BUY_CANDIDATE'
                and sell['pit_role'] == 'LATER_SELL_OUTCOME_EVIDENCE'
                and buy['mint'] == sell['mint'] == witness['mint']
                and buy['package_id'] != sell['package_id']
                and buy['package_id'] == witness['earlier_package_id']
                and sell['package_id'] == witness['later_package_id'],
                'source-bound positive pair remains across distinct packages')
    require(len(witnesses) == len({w['mint'] for w in witnesses}), 'one pair witness per mint')
    # The current admitted PIT contract has no certified-negative state. A
    # missing witness cannot be converted into a zero label by this prototype.
    require(window['negative_conclusion'] == 'UNAVAILABLE_SEMANTIC_COVERAGE_NOT_ESTABLISHED',
            'negative coverage certification is not present in this snapshot')
    label = 1 if witnesses else None
    return {'ordinal': window['ordinal'], 'sample_identity': window['sample_identity'],
            'source': window['source'], 'counts': window['counts'], 'coverage': window['coverage'],
            'decision_boundary': window['decision_boundary'],
            'feature': {'first_half_admitted_buy_present': bool(earlier_buys),
                        'first_half_admitted_buy_count': str(len(earlier_buys)),
                        'first_half_buy_fact_sha256': [f['fact_sha256'] for f in earlier_buys],
                        'first_half_package_count_with_admitted_facts': str(len({f['package_id'] for f in first})),
                        'candidate_input_status': 'CHAIN_EARLIER_NOT_PROVEN_HISTORICALLY_OBSERVED'},
            'prediction': {'pair_probability': str(prediction), 'always_no_comparator': '0'},
            'outcome': {'label': None if label is None else str(label),
                        'state': 'OBSERVED_ADMITTED_POSITIVE_PAIR' if label else 'UNKNOWN_INCOMPLETE_PUMP_COVERAGE',
                        'positive_witnesses': witnesses,
                        'late_facts_are_label_only': True},
            'first_half_admitted_facts': first, 'second_half_label_only_facts': later,
            'unavailable': {'historical_observed_at': None, 'actionable_at': None,
                            'latency_model_id': None, 'execution_opportunity_at': None,
                            'executable_fill': None, 'quote_currency_volume': None,
                            'negative_coverage_certificate': None}}


def build_baseline(pit, method_sha256, code_sha256):
    require(pit['schema'] == 'B8_DEVELOPMENT_PIT_WALKING_SKELETON_1'
            and pit['scope'] == 'DEVELOPMENT_ORDINALS_0_TO_3_ONLY'
            and pit['evaluation_access'] == 'DENIED'
            and pit['research_ready'] is False and pit['b8_accepted'] is False
            and pit['source']['selection_sha256'] == PINS['selection_sha256']
            and len(pit['windows']) == 4, 'fixed DEVELOPMENT point-in-time input only')
    for ordinal, window in enumerate(pit['windows']):
        sample = window['sample_identity']; b7 = sample['b7']; pin = PINS['windows'][ordinal]
        require(window['ordinal'] == str(ordinal)
                and sample['sample_class'] == 'RESEARCH_SAMPLING'
                and b7['cohort_role'] == 'DEVELOPMENT'
                and int(b7['phase']) == 1 and int(b7['window_ordinal']) == ordinal
                and b7['selection_sha256'] == PINS['selection_sha256']
                and [int(sample['start_slot']), int(sample['end_slot_exclusive'])] == pin['range']
                and window['source']['collection_sha256'] == pin['sha256'],
                'no alternate role, ordinal, range or collection snapshot')
    windows = [project_window(w) for w in pit['windows']]
    require([w['ordinal'] for w in windows] == ['0', '1', '2', '3'], 'fixed window order')
    feature_rows = [{'ordinal': w['ordinal'], 'decision_boundary': w['decision_boundary'],
                     'feature': w['feature'], 'prediction': w['prediction']}
                    for w in windows]
    label_rows = [{'ordinal': w['ordinal'], 'outcome': w['outcome']}
                  for w in windows]
    scored = [w for w in windows if w['outcome']['label'] is not None]
    rule_errors = sum((int(w['prediction']['pair_probability']) - int(w['outcome']['label'])) ** 2
                      for w in scored)
    comparator_errors = sum(int(w['outcome']['label']) ** 2 for w in scored)
    return {'schema': SCHEMA, 'scope': 'DEVELOPMENT_W00_W03_ALREADY_INSPECTED',
            'evaluation_access': 'DENIED', 'research_ready': False, 'b8_accepted': False,
            'method': {'schema': 'B8_DEVELOPMENT_BASELINE_METHOD_1', 'sha256': method_sha256},
            'producer': {'python_sha256': code_sha256},
            'source': {**pit['source'], 'pit_report_sha256': sha(canonical(pit))},
            'gold_content': {'feature_logical_sha256': sha(canonical(feature_rows)),
                             'label_logical_sha256': sha(canonical(label_rows)),
                             'hash_contract': 'canonical JSON of ordered feature/label rows, without execution clocks'},
            'totals': pit['totals'], 'windows': windows,
            'assessment': {'state': 'INSUFFICIENT_SAMPLE',
                           'reason': 'Four already inspected positive DEVELOPMENT windows; no certified negatives or untouched holdout. Arithmetic is descriptive only.',
                           'known_positive_windows': str(sum(w['outcome']['label'] == '1' for w in windows)),
                           'known_negative_windows': '0',
                           'unknown_windows': str(len(windows) - len(scored)),
                           'scored_windows': str(len(scored)),
                           'rule_brier': {'squared_error_sum': str(rule_errors),
                                          'denominator': str(len(scored))},
                           'always_no_brier': {'squared_error_sum': str(comparator_errors),
                                               'denominator': str(len(scored))}},
            'limits': {'historical_information_availability': 'UNAVAILABLE',
                       'execution_and_entry_exit_evidence': 'UNAVAILABLE',
                       'population_or_held_out_performance': 'UNAVAILABLE',
                       'clock_rule': 'chain order only; operational clocks never inputs'}}


def html_report(report):
    esc = lambda x: html.escape(str(x), quote=True)
    rows = []
    for window in report['windows']:
        earlier = ', '.join(window['feature']['first_half_buy_fact_sha256']) or 'geen toegelaten buy'
        witnesses = '; '.join(f"{x['mint']}: {x['earlier_buy_fact_sha256']} → {x['later_sell_fact_sha256']}"
                              for x in window['outcome']['positive_witnesses']) or 'UNKNOWN'
        rows.append('<tr>' + ''.join(f'<td>{esc(x)}</td>' for x in
                    (f"w{int(window['ordinal']):02d}", window['sample_identity']['start_slot'],
                     window['decision_boundary']['before_slot'], window['sample_identity']['end_slot_exclusive'],
                     window['counts']['packages'], window['counts']['failures'], window['counts']['silver_facts'],
                     window['feature']['first_half_admitted_buy_count'],
                     window['prediction']['pair_probability'], window['outcome']['state'],
                     earlier, witnesses, window['source']['collection_sha256'])) + '</tr>')
    return ('<!doctype html><html lang="nl"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<title>B8 · DEVELOPMENT-baseline</title><style>body{font:16px system-ui;max-width:100rem;margin:auto;padding:1rem}'
            'section{overflow-x:auto}table{border-collapse:collapse;width:100%}td,th{border:1px solid #777;padding:.4rem;text-align:left;overflow-wrap:anywhere}'
            'th{background:#eee}code{overflow-wrap:anywhere}</style><h1>B8 · eerste falsifieerbare DEVELOPMENT-baseline</h1>'
            '<p><strong>INSUFFICIENT SAMPLE.</strong> Vier eerder bekeken vensters zijn positief; geen gecertificeerde negatieven of onaangeraakte holdout. '
            'De regel voorspelt een later toegelaten paar bij minstens één buy in de eerste acht slots; de vergelijking voorspelt altijd geen paar. '
            'Brier-som regel <strong>' + esc(report['assessment']['rule_brier']['squared_error_sum']) + '/'
            + esc(report['assessment']['rule_brier']['denominator']) + '</strong>; vergelijking <strong>'
            + esc(report['assessment']['always_no_brier']['squared_error_sum']) + '/'
            + esc(report['assessment']['always_no_brier']['denominator']) + '</strong>. Dit is alleen een beschrijvende softwarecontrole.</p>'
            '<p>De ketengrens is geen bewezen observed_at of actionable_at. Latency, uitvoerbare fills, quote-eenheden en winst: '
            '<strong>UNAVAILABLE</strong>. Latere feiten zijn uitsluitend labelbewijs. Ontbrekende Pump-dekking is geen negatieve waarneming. '
            'Research Ready: false.</p><section><table><caption>Vaste vensters w00–w03; hashes verwijzen naar bestaande bronfeiten</caption>'
            '<thead><tr><th>Venster</th><th>Startslot</th><th>Grens vóór slot</th><th>Eindslot</th><th>Packages</th><th>Failures</th>'
            '<th>Silver-feiten</th><th>Eerdere buys</th><th>Voorspelling</th><th>Later label</th><th>Eerdere buy-hashes</th>'
            '<th>Later paarbewijs (buy → sell)</th><th>Collectiehash</th></tr></thead><tbody>'
            + ''.join(rows) + '</tbody></table></section><p>Volledige fact-, package-, receipt-, manifest- en methodebindingen staan in '
            '<a href="report.json">de reproduceerbare JSON</a>. Operationele klokken staan afzonderlijk in execution.json.</p></html>\n').encode()


def produce(output):
    output = pathlib.Path(output)
    require(output.is_absolute() and output.resolve() == output
            and output.is_relative_to(ROOT / 'governance')
            and output.parent.is_dir() and not output.exists(), 'create-only private output directory')
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    t0 = time.monotonic()
    code_sha = sha(pathlib.Path(__file__).read_bytes())
    method_bytes = regular_bytes(METHOD, 16 * 1024)
    method = json.loads(method_bytes, object_pairs_hook=pairs_unique)
    require(method['schema'] == 'B8_DEVELOPMENT_BASELINE_METHOD_1'
            and method['scope'] == 'FIXED_DEVELOPMENT_W00_W03_ALREADY_INSPECTED', 'fixed method')
    report = build_baseline(load_pinned_pit(), sha(method_bytes), code_sha)
    require(sha(pathlib.Path(__file__).read_bytes()) == code_sha
            and sha(regular_bytes(METHOD, 16 * 1024)) == report['method']['sha256'],
            'producer/method unchanged during creation')
    raw = canonical(report)
    page = html_report(report)
    require(len(raw) <= 4 * 1024 * 1024 and len(page) <= 2 * 1024 * 1024,
            'bounded private report')
    output.mkdir()
    for name, data in [('report.json', raw), ('report.html', page)]:
        with (output / name).open('xb') as f:
            f.write(data)
    execution = {'schema': 'B8_DEVELOPMENT_BASELINE_EXECUTION_1',
                 'report_sha256': sha(raw), 'html_sha256': sha(page),
                 'started_at_utc': started, 'elapsed_ms': str(int((time.monotonic() - t0) * 1000)),
                 'network_requests': 0, 'evaluation_access': 'DENIED'}
    with (output / 'execution.json').open('xb') as f:
        f.write(canonical(execution))
    print(json.dumps({'path': str(output), 'report_sha256': sha(raw),
                      'assessment': report['assessment']['state']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('new_private_output_directory')
    produce(parser.parse_args().new_private_output_directory)
