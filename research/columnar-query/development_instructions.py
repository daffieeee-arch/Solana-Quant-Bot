"""Descriptive counts over the fixed native DEVELOPMENT instruction inventory.
No protocol parser, Raw replay, Silver admission, evaluation or B8 labels.
"""
import argparse
import datetime
import json
import pathlib
import subprocess
import time
from collections import Counter

from development_cohort import PINS, PINS_BYTES, ROOT, MAX_BYTES, digest, require, uint
from manifest_reader import pairs_unique, regular_bytes, sha
from mint_timeline import canonical, exact

RULE = 'DEVELOPMENT_INSTRUCTION_COVERAGE_1'
CATEGORIES = ('ADMITTED_TRADE', 'SUPPORTING_EVENT_CPI', 'OTHER_INSTRUCTION', 'REJECTED_TRADE', 'UNEXPLAINED')
COHORT_PATH = ROOT / 'governance/b7-development-cohort-20260928/report/review/cohort.json'
COHORT_SHA = 'b8308de7160baaf5c39805d0b4956dfe119f3b4e09298e6b8a3d037e05989486'


def summarize(inventory, facts):
    require(inventory['rule_version'] == RULE, 'inventory rule version')
    categories = Counter({k: 0 for k in CATEGORIES})
    reasons, outcomes, unknown_reasons = Counter(), Counter(), Counter()
    ids, packages, linked, probe_values, hiding = set(), set(), set(), set(), set()
    probes = diagnoses = failed_instructions = references = 0
    by_id = {}
    for package in inventory['packages']:
        pid = package['package_id']
        require(pid not in packages, 'duplicate inventory package')
        packages.add(pid)
        require(pid.endswith(':' + digest(package['bronze_record_sha256'])), 'package binding')
        previous = None
        for row in package['instructions']:
            key = row['instruction_id']; loc = row['location']
            outer = uint(loc['outer_index']); inner = loc['inner_order']
            require((loc['kind'] == 'DECLARED_TOP_LEVEL' and inner is None)
                    or (loc['kind'] == 'RECORDED_CPI' and inner is not None), 'location kind')
            position = (outer, -1 if inner is None else uint(inner))
            require(previous is None or previous < position, 'unique canonical instruction positions')
            previous = position
            require(key == f'{pid}:{outer}:' + ('TOP' if inner is None else f'CPI:{uint(inner)}'), 'location identity')
            require(key not in ids and row['category'] in CATEGORIES, 'unique instruction/category')
            ids.add(key); by_id[key] = (package, row)
            categories[row['category']] += 1
            require(type(row['may_hide_trade_observation']) is bool, 'native coverage verdict')
            if row['may_hide_trade_observation']: hiding.add(pid)
            references += uint(row['reference_count'])
            require(uint(row['reference_count']) >= 1, 'reference count')
            failed_instructions += package['transaction_status'] == 'ERROR'
            require(row['execution'] == 'NOT_ESTABLISHED_BY_INSTRUCTION_PRESENCE', 'execution boundary')
            require(row['reasons'], 'explicit instruction reason')
            reasons.update(set(row['reasons']))
            probes += len(row['probes']); diagnoses += len(row['diagnoses'])
            for probe in row['probes']:
                probe_values.add((key, digest(probe['evidence_sha256'])))
                o = probe['original']; error = o.get('layout_error')
                outcomes[o['kind'] + ':' + (error['reason'] if error else o['layout_outcome'])] += 1
    require(len(ids) == uint(inventory['unique_pump_instructions']), 'native instruction count')
    require(len(inventory['fact_links']) == len(facts), 'complete fact mapping')
    for link in inventory['fact_links']:
        fh = digest(link['fact_sha256'])
        require(fh in facts and fh not in linked, 'exact unchanged fact set')
        linked.add(fh)
        fact = facts[fh]; r = fact['record']
        require(r['transaction_status'] == 'OK', 'no facts from failures')
        require(link['package_id'] == fact['package_id'], 'successful original parent')
        trade = by_id.get(link['trade_instruction_id']); event = by_id.get(link['event_instruction_id'])
        require(trade is not None and event is not None and trade[0]['package_id'] == event[0]['package_id'] == link['package_id']
                and trade[0]['transaction_status'] == 'OK', 'atomic trade/event package')
        require(trade[1]['category'] == 'ADMITTED_TRADE' and event[1]['category'] == 'SUPPORTING_EVENT_CPI'
                and fh in trade[1]['trade_fact_sha256s'] and fh in event[1]['event_fact_sha256s'], 'separate trade and event roles')
        require(trade[1]['instruction_sha256'] == link['instruction_sha256'] == r['instruction_sha256']
                and event[1]['instruction_sha256'] == link['event_cpi_sha256'] == r['event_context']['event_cpi_sha256'], 'original instruction/event hashes')
    require(linked == set(facts), 'all original facts linked')
    # Every per-instruction reference must be present in the authoritative links.
    for key, (package, row) in by_id.items():
        for field, role in [('trade_fact_sha256s', 'trade_instruction_id'), ('event_fact_sha256s', 'event_instruction_id')]:
            expected = [link['fact_sha256'] for link in inventory['fact_links'] if link[role] == key]
            require(row[field] == expected, 'no phantom or repeated per-instruction facts')
    uncertain_packages = set()
    for u in inventory['uncertainty']:
        require(u['package_id'] not in uncertain_packages and u['items'], 'uncertainty package identity')
        uncertain_packages.add(u['package_id'])
        unknown_reasons.update(item['reason'] for item in u['items'])
        if u['transaction_status'] != 'ERROR': hiding.add(u['package_id'])
    return {'packages_with_identified_pump_instructions': len(packages), 'unique_pump_instructions': len(ids),
        'failed_parent_instructions': failed_instructions, 'instruction_references': references,
        'duplicate_instruction_references': references-len(ids), 'category_counts': dict(categories),
        'probe_references': probes, 'distinct_instruction_probe_values': len(probe_values),
        'diagnosis_references': diagnoses, 'silver_facts': len(linked), 'reason_instruction_counts': dict(sorted(reasons.items())),
        'probe_outcomes': dict(sorted(outcomes.items())), 'uncertain_packages': len(uncertain_packages),
        'uncertainty_items': sum(unknown_reasons.values()), 'uncertainty_reason_counts': dict(sorted(unknown_reasons.items())),
        'potentially_hiding_packages': len(hiding),
        'negative_conclusion': 'UNAVAILABLE' if hiding else 'NO_GAP_IDENTIFIED_BY_THIS_INVENTORY_NOT_A_NEGATIVE_LABEL'}


def build_report(admission, cohort, admission_sha, cohort_sha):
    require(admission['schema'] == 'OF1_B7_DEVELOPMENT_INSTRUCTIONS_ADMISSION_1'
            and admission['pins_sha256'] == sha(PINS_BYTES) and admission['selection_sha256'] == PINS['selection_sha256']
            and admission['research_ready'] is False, 'fixed native route')
    require(cohort['schema'] == 'OF1_B7_DEVELOPMENT_COHORT_1' and cohort['pins_sha256'] == sha(PINS_BYTES)
            and cohort['selection_sha256'] == PINS['selection_sha256'] and cohort['research_ready'] is False
            and cohort['evaluation_access'] == 'DENIED', 'existing cohort identity')
    require(len(admission['windows']) == len(cohort['windows']) == 4, 'four windows')
    windows = []
    for w, c, pin in zip(admission['windows'], cohort['windows'], PINS['windows'], strict=True):
        s = w['sample_identity']; b = s['b7']
        require(w['ordinal'] == pin['ordinal'] and w['collection_sha256'] == pin['sha256']
                and w['layers'] == pin['layers'] and w['counts'] == pin['counts'], 'pinned window snapshot')
        require(b['cohort_role'] == 'DEVELOPMENT' and b['window_ordinal'] == pin['ordinal']
                and b['phase'] == 1 and b['selection_sha256'] == PINS['selection_sha256']
                and s['sample_class'] == 'RESEARCH_SAMPLING' and [s['start_slot'],s['end_slot_exclusive']] == pin['range'], 'role/sample')
        for key in ['ordinal','collection_sha256','collection_path','sample_identity','counts','layers','parts','coverage','slot_outcomes']:
            require(exact(w[key]) == c[key], 'cohort snapshot ' + key)
        facts = {f['record_sha256']: f for m in c['mints'] for f in m['facts']}
        require(len(facts) == pin['counts']['silver_facts'], 'original fact count')
        for h, f in facts.items():
            raw = f['canonical_record_json']; digest(h)
            require(sha(raw.encode()) == h and exact(json.loads(raw, object_pairs_hook=pairs_unique)) == f['record'], 'unchanged source fact')
        inv = w['instruction_inventory']
        require(inv['all_packages_checked'] == pin['counts']['packages']
                and inv['failed_packages_checked'] == pin['counts']['failures'], 'complete package denominator')
        summary = summarize(inv, facts)
        windows.append({**{k:v for k,v in w.items() if k != 'facts'}, 'summary': summary,
            'observed_pair_mints': c['observed_pair_mints'],
            'pair_evidence': [{'mint':m['mint'], 'observed_pair':m['observed_pair']} for m in c['mints'] if m['observed_pair'] is not None]})
    return exact({'schema':'OF1_B7_DEVELOPMENT_INSTRUCTIONS_1','state':'READY','research_ready':False,'evaluation_access':'DENIED',
        'selection_sha256':PINS['selection_sha256'],'pins_sha256':sha(PINS_BYTES),
        'native_admission_sha256':digest(admission_sha),'cohort_report_sha256':digest(cohort_sha),
        'producer':{'version':RULE,'python_sha256':sha(pathlib.Path(__file__).read_bytes()),
                    'native_source_sha256':digest(admission['reader_source_sha256']),'native_binary_sha256':digest(admission['reader_binary_sha256'])},
        'rules':{'version':RULE,'positive':'Existing admitted buy/sell pair in separate packages remains a positive observation.',
            'absence':'No pair in admitted facts is not an automatic negative observation.',
            'unavailable':'Potentially hiding non-failed/unknown-parent instructions or missing context keep a negative conclusion UNAVAILABLE.',
            'failures':'Failed parents remain evidence; no successful transition or admitted fact follows.',
            'scope':'Inventory rules only; no evaluation visibility, B8 labels, frequency or sufficiency verdict. No requirement to decode every Pump variant.'},
        'totals':{k:sum(w['counts'][k] for w in windows) for k in ['blocks','packages','failures','silver_facts']},'windows':windows})


def produce(reader, binary_sha, cohort_path, cohort_sha, output):
    reader = pathlib.Path(reader); output = pathlib.Path(output); cohort_path=pathlib.Path(cohort_path)
    # Deny other cohort/evaluation inputs before opening anything supplied by the caller.
    require(cohort_path == COHORT_PATH and cohort_sha == COHORT_SHA, 'approved existing DEVELOPMENT report only')
    require(reader.is_absolute() and reader.resolve() == reader and sha(reader.read_bytes()) == digest(binary_sha), 'native binary')
    require(cohort_path.is_relative_to(ROOT) and cohort_path.resolve() == cohort_path, 'registered external cohort')
    c_raw = regular_bytes(cohort_path, MAX_BYTES); require(sha(c_raw) == digest(cohort_sha), 'cohort file hash')
    require(output.is_absolute() and output.resolve()==output and output.is_relative_to(ROOT) and not output.exists() and output.parent.is_dir(), 'new external output')
    start=datetime.datetime.now(datetime.timezone.utc).isoformat(); t0=time.monotonic()
    result=subprocess.run([str(reader),'development-instructions'],capture_output=True,check=True,timeout=900)
    require(len(result.stdout)<=MAX_BYTES and len(result.stderr)<=262144 and sha(reader.read_bytes())==binary_sha,'bounded unchanged binary')
    a=json.loads(result.stdout,object_pairs_hook=pairs_unique); require(a['reader_binary_sha256']==binary_sha,'execution identity')
    report=build_report(a,json.loads(c_raw,object_pairs_hook=pairs_unique),sha(result.stdout),cohort_sha); raw=canonical(report)
    require(len(raw)<=MAX_BYTES,'bounded report');output.mkdir()
    for name,data in [('admission.json',result.stdout),('instructions.json',raw)]:
        with (output/name).open('xb') as f:f.write(data)
    execution={'started_at_utc':start,'elapsed_seconds':time.monotonic()-t0,'clocks_are_operational_only':True,
        'binary_sha256':binary_sha,'report_sha256':sha(raw),'admission_sha256':sha(result.stdout),'cohort_sha256':cohort_sha}
    with (output/'execution.json').open('xb') as f:f.write(canonical(execution))
    print(json.dumps({'output':str(output),'sha256':sha(raw),'totals':report['totals']}))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['reader','binary_sha','cohort_path','cohort_sha','output']:p.add_argument(name)
    a=p.parse_args();produce(a.reader,a.binary_sha,a.cohort_path,a.cohort_sha,a.output)
