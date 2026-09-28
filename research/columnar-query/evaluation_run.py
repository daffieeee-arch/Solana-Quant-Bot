#!/usr/bin/env python3
"""Fixed B7 sealed processing entrypoint. No acquisition or evaluation visibility.

Native authority binds these driver bytes, imports, Python executable, exact
workers, source plan, method and the existing campaign ledger. Not an OS barrier
against the data-owning user reading files or replacing approved software.
"""
import contextlib
import json
import pathlib
import sys

from collection_run import Runner, process_limits
from manifest_reader import MAX_MANIFEST_BYTES, pairs_unique, regular_bytes


class Quiet:
    """Discard operational driver chatter; native detail logs stay in work root."""
    def write(self, text):
        return len(text)

    def flush(self):
        pass


def process(plan, decoder, projector, verifier, approval=None):
    data = json.loads(regular_bytes(plan, MAX_MANIFEST_BYTES), object_pairs_hook=pairs_unique)
    if len(data['sources']) != 1 or data.get('slot_part_profile') != 'OF1_ATOMIC_SLOT_PARTS_128_V1':
        raise ValueError('fixed evaluation plan required')
    binding = data['sources'][0]['sample_identity']['b7']
    if binding['cohort_role'] != 'RESERVED_EVALUATION' or binding['window_ordinal'] not in [4, 5, 6, 7, 12, 13, 14, 15]:
        raise ValueError('fixed evaluation cohort required')
    root = pathlib.Path(binding['campaign_root']) / 'work' / f"w{binding['window_ordinal']:02d}"
    # No step count, fact count, manifest body, exception text or arbitrary path
    # is returned by this interface, including exceptions before native admission.
    with contextlib.redirect_stdout(Quiet()), contextlib.redirect_stderr(Quiet()):
        runner = Runner(plan, root, decoder, projector, verifier, approval)
        try:
            runner.run()
        finally:
            if runner.driver_lock is not None:
                runner.driver_lock.close()
    return {'state': 'SEALED_PROCESSING_COMPLETE', 'outcomes_released': False, 'research_ready': False}


def main(args):
    try:
        if len(args) not in [4, 5]:
            raise ValueError('fixed arguments required')
        process_limits()
        value = process(*[pathlib.Path(arg).resolve(strict=True) for arg in args])
    except BaseException:
        # Never echo CalledProcessError, TimeoutExpired, argv or captured bytes.
        print(json.dumps({'state': 'SEALED_PROCESSING_STOPPED', 'outcomes_released': False}))
        return 1
    print(json.dumps(value))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
