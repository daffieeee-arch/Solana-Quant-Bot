"""Offline selection/budget presentation only. No provider, index or Pump parser."""
import hashlib
import html
import json
import math
import pathlib
import struct
import sys

PROPOSAL = pathlib.Path(__file__).with_name('b7-proposal.json')


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + '\n').encode()


def select(plan):
    if plan['schema'] != 'B7_SAMPLING_PROPOSAL_1' or any(
        plan[key] is not False for key in ('approved', 'network_enabled', 'metadata_approved',
                                         'payload_approved', 'execution_approved', 'research_ready')
    ):
        raise ValueError('unapproved offline proposal required')
    if plan['new_sample_class_proposed'] != 'RESEARCH_SAMPLING':
        raise ValueError('engineering data cannot become a research proposal')
    first, end, width = plan['epoch_first_slot'], plan['epoch_end_slot_exclusive'], plan['window_slots']
    if (first, end, width, plan['feature_slots'], plan['epoch']) != (422496000, 422928000, 16, 8, 978):
        raise ValueError('unsupported bounded geometry')
    n, stage = plan['windows_per_stratum'], plan['first_stage_per_stratum']
    if (n, stage, len(plan['strata'])) != (8, 4, 2) or plan['replacement_allowed'] is not False:
        raise ValueError('unsupported expansion')
    windows, populations = [], []
    for stratum in plan['strata']:
        a, b = stratum['start'], stratum['end_exclusive']
        if not first <= a < b <= end or (a-first) % width or (b-first) % width:
            raise ValueError('invalid stratum')
        candidates = []
        for start in range(a, b, width):
            if any(start < y and x < start+width for x, y in plan['inspected_exclusions']):
                continue
            digest = hashlib.sha256(plan['hash_domain'].encode() + plan['seed'].encode()
                                    + struct.pack('<QQ', plan['epoch'], start)).hexdigest()
            candidates.append((digest, start))
        populations.append({'role': stratum['role'], 'eligible_windows': len(candidates)})
        for rank, (digest, start) in enumerate(sorted(candidates)[:n], 1):
            windows.append({'role': stratum['role'], 'rank': rank,
                            'stage': 1 if rank <= stage else 2,
                            'start_slot': start, 'end_slot_exclusive': start+width,
                            'boundary_slot': start+plan['feature_slots'], 'rank_sha256': digest})
    if len(windows) != 16 or len({w['start_slot'] for w in windows}) != 16:
        raise ValueError('overlapping/short selection')
    windows.sort(key=lambda w: (w['stage'], w['role'], w['rank']))
    return {'schema': 'B7_FIXED_WINDOWS_1', 'network_authorized': False,
            'proposal_sha256': hashlib.sha256(canonical(plan)).hexdigest(),
            'populations': populations, 'windows': windows}


def budgets(plan, selection, ranges):
    """Consume only Rust planner output. Unknown/stopped windows never become zero."""
    if ranges['schema'] != 'OF1_OFFLINE_WINDOW_RANGES_1' or ranges['network_authorized'] is not False:
        raise ValueError('wrong Rust report')
    if ranges['selection_sha256'] != hashlib.sha256(canonical(selection)).hexdigest():
        raise ValueError('wrong selection binding')
    if len(ranges['windows']) != len(selection['windows']):
        raise ValueError('missing window')
    result = []
    b = plan['budget']
    for wanted, measured in zip(selection['windows'], ranges['windows'], strict=True):
        if wanted != measured['selection']:
            raise ValueError('changed order/window')
        item = dict(wanted, planning_status=measured['status'])
        if measured['status'] != 'PLANNED':
            item.update(payload_bytes=None, payload_reserved_bytes=None, stop=measured.get('error'))
        else:
            p = measured['prepared']
            if (p['start_slot'], p['end_slot']) != (wanted['start_slot'], wanted['end_slot_exclusive']):
                raise ValueError('prepared range mismatch')
            requests = p['requests']
            slots = []
            total = 0
            for request in requests:
                k = request['kind']
                if k['kind'] != 'CAR_RANGE' or not wanted['start_slot'] <= k['slot'] < wanted['end_slot_exclusive']:
                    raise ValueError('unexpected request')
                length = k['end_exclusive']-k['start']
                if length <= 0 or length > b['single_response_bytes']:
                    raise ValueError('invalid range length')
                slots.append(k['slot']); total += length
            absent = p['index_reported_absent']
            absent_slots = [x['slot'] for x in absent]
            if len(set(slots + absent_slots)) != 16 or sorted(slots + absent_slots) != list(range(wanted['start_slot'], wanted['end_slot_exclusive'])):
                raise ValueError('missing/duplicate slot accounting')
            item.update(payload_bytes=total, payload_reserved_bytes=total*b['attempts_per_operation'],
                        payload_attempts=len(requests)*b['attempts_per_operation'], index_absent=absent,
                        stop='INDEX_ABSENCE_NOT_PROVEN_EMPTY' if absent else None)
        result.append(item)
    complete = all(w['payload_bytes'] is not None for w in result)
    total = sum(w['payload_bytes'] for w in result) if complete else None
    reserved = total*3 + 16*b['metadata_reserved_entity_bytes_per_window'] if complete else None
    attempts = sum(w['payload_attempts'] for w in result)+16*b['metadata_max_attempts_per_window'] if complete else None
    if reserved is not None and reserved > b['campaign_reserved_entity_bytes_cap']:
        raise ValueError('campaign byte cap exceeded: retain selection, no replacement')
    if attempts is not None and attempts > b['campaign_attempts_cap']:
        raise ValueError('campaign attempt cap exceeded')
    return {'windows': result, 'unique_payload_entity_bytes': total,
            'metadata_reserved_entity_bytes': 16*b['metadata_reserved_entity_bytes_per_window'],
            'combined_reserved_entity_bytes': reserved, 'combined_max_attempts': attempts,
            'network_authorized': False, 'execution_ready': False,
            'blocking_identity': plan['acquisition_identity_status'], 'cost_status': b['cost_status']}


def precision_bound(assigned, positives, unknown):
    """Design scenarios only; never label unacquired data or declare B7 complete."""
    if any(type(v) is not int for v in (assigned, positives, unknown)) or not (
        0 < assigned <= 8 and 0 <= positives <= assigned and 0 <= unknown <= assigned-positives
    ):
        raise ValueError('invalid window denominator')
    radius = math.sqrt(math.log(40)/(2*assigned))
    lower = max(0, positives/assigned-radius)
    upper = min(1, (positives+unknown)/assigned+radius)
    return {'lower': lower, 'upper': upper, 'width': upper-lower,
            'precision_target_met': upper-lower <= 0.50,
            'semantic_and_source_gates_not_evaluated': True}


def render(report):
    e = lambda x: html.escape(str(x))
    rows = ''.join('<tr>'+''.join('<td>'+e(w.get(k))+'</td>' for k in
                  ['role','stage','rank','start_slot','end_slot_exclusive','payload_bytes','stop'])+'</tr>'
                  for w in report['budget']['windows'])
    return ('<!doctype html><html lang="nl"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<title>B7 — samplingbesluit</title><style>body{font:16px system-ui;max-width:1100px;margin:2em auto;padding:1em;background:#f6f8fa;color:#152839}table{border-collapse:collapse}td,th{padding:.5em;border:1px solid #ccd}pre{white-space:pre-wrap;overflow-wrap:anywhere}.scroll{overflow:auto}h1,h2{color:#125a66}</style>'
            '<h1>B7 — onderzoeks- en samplingvoorstel</h1><p><b>GEEN ACQUISITIEGOEDKEURING · Research Ready false · #86 open / Unproven</b></p>'
            '<p>'+e(report['plan']['question'])+'</p><p>'+e(report['plan']['estimand'])+'</p>'
            '<h2>Besluit en blokkades</h2><p>'+e(report['decision'])+'</p>'
            '<h2>Vaste selectie — geen nieuwe uitkomsten bekeken</h2><div class="scroll"><table><tr><th>Cohort</th><th>Fase</th><th>Rang</th><th>Begin</th><th>Einde exclusief</th><th>Raw entity-bytes</th><th>Stop</th></tr>'+rows+'</table></div>'
            '<h2>Onderbouwing, gegevenskloof en budgetten</h2>'+''.join('<h3>'+e(k)+'</h3><pre>'+e(json.dumps(report[k],indent=2,ensure_ascii=False))+'</pre>' for k in ['current_data','gaps','method','operational_summary'])+'<details><summary>Volledige bronbindingen en machinegegevens</summary><pre>'+e(json.dumps(report,indent=2,ensure_ascii=False))+'</pre></details>'
            '<p>Alle bronbindingsdetails staan ook in <a href="report.json">report.json</a>. Geen externe scripts of verbindingen.</p></html>')


def main():
    plan = json.loads(PROPOSAL.read_bytes())
    if sys.argv[1:] == ['select']:
        sys.stdout.buffer.write(canonical(select(plan)))
    else:
        raise SystemExit('b7_sampling.py select; report construction uses the pinned private evidence inventory')


if __name__ == '__main__':
    main()
