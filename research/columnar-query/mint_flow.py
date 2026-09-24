#!/usr/bin/env python3
"""Bounded descriptive sums over a pinned mint report, never a wire decoder."""
import argparse
import datetime
import json
import pathlib
import re
import sys
import time

from manifest_reader import pairs_unique, regular_bytes, sha
from mint_timeline import canonical, exact, summarize

HERE = pathlib.Path(__file__).resolve().parent
MAX_BYTES = 8 * 1024 * 1024
INPUTS = ('timeline', 'lifecycle', 'collection', 'plan')
ROLES = ('ORIGINAL_SELECTION', 'POSTHOC_DESCRIPTIVE_CONTEXT')
GROUPS = ('ALL', *ROLES)
SELECTION = 'POSTHOC_DESCRIPTIVE_LIFECYCLE_FRAGMENT'
VERSION = 'ADMITTED_EVENT_TOKEN_FLOW_1'


def require(condition, message):
    if not condition:
        raise ValueError('MINT_FLOW_INVALID: ' + message)


def digest(value):
    require(isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value), 'hash')
    return value


def uint64(value):
    require(isinstance(value, str) and re.fullmatch(r'0|[1-9][0-9]{0,19}', value), 'exact raw u64 string')
    result = int(value)
    require(result < 2**64, 'u64 range')
    return result


class Ledger:
    """One bounded scan; snapshot after the complete atomic parent only."""
    def __init__(self):
        self.buy = self.sell = self.buys = self.sells = 0
        self.users = set()

    def add(self, facts):
        for side, amount, user in facts:
            if side:
                self.buy += amount
                self.buys += 1
            else:
                self.sell += amount
                self.sells += 1
            self.users.add(user)

    def snapshot(self):
        return exact({'buy_token_raw': self.buy, 'sell_token_raw': self.sell,
                      'gross_token_raw': self.buy + self.sell, 'net_token_raw': self.buy - self.sell,
                      'unique_event_users': len(self.users), 'buy_facts': self.buys,
                      'sell_facts': self.sells, 'facts': self.buys + self.sells})


def build_summary(timeline, lifecycle, hashes):
    require(set(hashes) == set(INPUTS), 'input set')
    for value in hashes.values():
        digest(value)
    t = timeline
    require(isinstance(t['mint'], str) and re.fullmatch(r'[1-9A-HJ-NP-Za-km-z]{32,44}', t['mint']), 'mint identity')
    require(t['schema'] == 'OF1_MINT_TIMELINE_1' and t['display_complete'] is True
            and t['research_ready'] is False and t['selection_class'] == SELECTION, 'timeline contract')
    require(t['bindings']['collection_sha256'] == hashes['collection']
            and t['bindings']['plan_sha256'] == hashes['plan'], 'timeline source binding')
    require(lifecycle['schema'] == 'B5_LIFECYCLE_ACCEPTANCE_PROPOSAL_1'
            and lifecycle['mint'] == t['mint'] and lifecycle['research_ready'] is False
            and lifecycle['collection']['sha256'] == hashes['collection']
            and lifecycle['collection']['plan_sha256'] == hashes['plan'], 'lifecycle binding')
    cards = t['transactions']
    require(isinstance(cards, list) and 0 < len(cards) <= 10000, 'bounded complete package list')
    require(exact(summarize(cards)) == t['counts'], 'existing timeline count contract')
    for key in ('transactions', 'silver_facts', 'balance_observations'):
        require(lifecycle['counts'][key] == t['counts'][key], 'lifecycle counts')
    seen_packages, seen_facts, previous = set(), set(), None
    ledger = {group: Ledger() for group in GROUPS}
    rows = []
    for p in cards:
        parent = digest(p['bronze_record_sha256'])
        identity = p['package_id']
        require(identity == p['collection_source_id'] + ':' + parent
                and identity not in seen_packages and p['atomic_observation_package'] is True, 'atomic package identity')
        seen_packages.add(identity)
        position = (uint64(p['slot']), uint64(p['transaction_index']), identity)
        require(previous is None or previous < position, 'chain order')
        previous = position
        role = p['collection_role']
        require(role in ROLES and p['slice_class'] == ('RESEARCH_SAMPLING' if role == ROLES[0]
                                                    else 'ENGINEERING_VALIDATION_ONLY'), 'source class')
        require(p['transaction_status'] in ('OK', 'ERROR', 'UNKNOWN'), 'transaction status')
        facts = p['silver_facts']
        require(isinstance(facts, list) and (p['transaction_status'] == 'OK' or not facts), 'failed/unknown fact exclusion')
        contributions, references = [], []
        for f in facts:
            fid = digest(f['record_sha256'])
            require(fid not in seen_facts and len(seen_facts) < 10000, 'unique bounded fact list')
            seen_facts.add(fid)
            r = f['record']
            require(r['bronze_record_sha256'] == parent and r['transaction_status'] == 'OK'
                    and r['atomic_observation_package'] is True and r['research_ready'] is False, 'fact parent/status')
            event = r['event_reported']
            require(event['mint_address'] == t['mint'] and type(event['is_buy']) is bool, 'mint/side')
            amount = uint64(event['token_amount_raw_u64'])
            user = event['user_address']
            require(isinstance(user, str) and 0 < len(user) <= 128 and user.strip() == user, 'reported event user')
            require(r['quote_mint_identity'] == 'UNKNOWN' and r['quote_decimals'] is None, 'bounded unknown quote contract')
            contributions.append((event['is_buy'], amount, user))
            references.append(fid)
        # Publish a prefix only after every fact of this atomic parent is added.
        ledger['ALL'].add(contributions)
        ledger[role].add(contributions)
        package_ledger = Ledger()
        package_ledger.add(contributions)
        rows.append({key: p[key] for key in ('package_id', 'bronze_record_sha256', 'collection_source_id',
                     'collection_role', 'slice_class', 'slot', 'transaction_index', 'transaction_status')}
                    | {'fact_hashes': references, 'package_totals': package_ledger.snapshot(),
                       'cumulative': {group: ledger[group].snapshot() for group in GROUPS}})
    lifecycle_facts = [f['silver_record_sha256'] for f in lifecycle['facts']]
    require(len(lifecycle_facts) == len(seen_facts) and set(lifecycle_facts) == seen_facts, 'lifecycle fact set')
    return {'schema': 'OF1_MINT_FLOW_1', 'state': 'READY', 'research_ready': False,
            'mint': t['mint'], 'selection_class': SELECTION, 'inputs': hashes,
            'producer': {'name': 'research/columnar-query/mint_flow.py', 'version': VERSION,
                         'source_sha256': {name: sha((HERE / name).read_bytes()) for name in
                                           ('mint_flow.py', 'mint_timeline.py', 'manifest_reader.py')}},
            'quote_volume': {'state': 'UNAVAILABLE', 'quote_mint_identity': 'UNKNOWN',
                             'quote_decimals': None, 'amount_raw': None},
            'totals': {group: ledger[group].snapshot() for group in GROUPS}, 'packages': rows}


def read_registered(root, entry, maximum=MAX_BYTES):
    relative = entry['path']
    require(isinstance(relative, str) and len(relative) <= 1024
            and all(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', part) for part in relative.split('/')), 'registered relative path')
    path = root / relative
    require(path.resolve(strict=True) == path, 'no ancestor symlink')
    raw = regular_bytes(path, maximum)
    require(sha(raw) == digest(entry['sha256']), 'registered byte hash')
    return json.loads(raw, object_pairs_hook=pairs_unique)


def produce(root, registry_path, output):
    require(root.is_absolute() and root.resolve(strict=True) == root, 'explicit canonical data root')
    require(registry_path.resolve(strict=True) == registry_path and registry_path.is_relative_to(root), 'registry location')
    registry_raw = regular_bytes(registry_path, 16384)
    registry = json.loads(registry_raw, object_pairs_hook=pairs_unique)
    require(registry['schema'] == 'OF1_MINT_INSPECTOR_REGISTRY_1' and set(registry['inputs']) == set(INPUTS), 'registry')
    inputs = {name: read_registered(root, registry['inputs'][name]) for name in INPUTS}
    hashes = {name: registry['inputs'][name]['sha256'] for name in INPUTS}
    c = inputs['collection']
    require(c['schema'] == 'OF1_BATCH_COLLECTION_1' and c['state'] == 'COMPLETE'
            and c['research_ready'] is False and c['plan_sha256'] == hashes['plan'], 'collection binding')
    start, clock = datetime.datetime.now(datetime.timezone.utc).isoformat(), time.perf_counter()
    result = build_summary(inputs['timeline'], inputs['lifecycle'], hashes)
    raw = canonical(result)
    require(len(raw) <= MAX_BYTES, 'complete output byte cap')
    require(output.is_absolute() and output.parent.resolve(strict=True) == output.parent
            and output.is_relative_to(root) and not output.exists(), 'new output directory under data root')
    execution = {'schema': 'OF1_MINT_FLOW_EXECUTION_1', 'started_at_utc': start,
                 'completed_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 'elapsed_seconds': time.perf_counter() - clock, 'python_version': sys.version.split()[0],
                 'result_sha256': sha(raw), 'registry_sha256': sha(registry_raw),
                 'provider_calls': False, 'dataset_replay': False}
    output.mkdir()
    (output / 'flow.json').write_bytes(raw)
    (output / 'execution.json').write_text(json.dumps(execution, indent=2) + '\n')
    print(json.dumps({'output': str(output), 'sha256': sha(raw), 'totals': result['totals']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('data_root', type=pathlib.Path)
    parser.add_argument('registry', type=pathlib.Path)
    parser.add_argument('output', type=pathlib.Path)
    args = parser.parse_args()
    produce(args.data_root, args.registry, args.output)
