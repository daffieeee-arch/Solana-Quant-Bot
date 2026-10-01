#!/usr/bin/env python3
"""Serialize an explicitly granted OF1 approval from ONE native clock sample.

No clock reads, filesystem writes, requests or implied owner approval. The caller
must preserve this output once. Approval validity is not the stage runtime.
"""
import json
import sys


def authority(request):
    clock = request['clock']
    target = request['target_sha256']
    if set(clock) != {'wall_ms', 'boot_ms', 'boot_id'}:
        raise ValueError('clock identity')
    if any(type(clock[k]) is not int or not 0 <= clock[k] <= 2**64-1-1_200_000
           for k in ['wall_ms', 'boot_ms']):
        raise ValueError('clock integer bounds')
    if not clock['wall_ms'] or not isinstance(clock['boot_id'], str) or not clock['boot_id']:
        raise ValueError('clock binding')
    if len(target) != 64 or any(c not in '0123456789abcdef' for c in target):
        raise ValueError('target identity')
    return {'mode': 'APPROVED', 'approval_id': request['approval_id'],
            'operator': request['operator'], 'approved_at_ms': clock['wall_ms'],
            'not_after_ms': clock['wall_ms'] + 1_200_000,
            'approved_plan_sha256': target,
            'cost_confirmation': 'CONFIRMED_NO_CREDIT_SPEND',
            'clock_anchor': {'t0': clock,
                'initialize_by_boot_ms': clock['boot_ms'] + 600_000,
                'expires_at_boot_ms': clock['boot_ms'] + 1_200_000}}


if __name__ == '__main__':
    raw = sys.stdin.buffer.read(8193)
    if len(raw) > 8192:
        raise ValueError('bounded approval input exceeded')
    print(json.dumps(authority(json.loads(raw)), sort_keys=True))
