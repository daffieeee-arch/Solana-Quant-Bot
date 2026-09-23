import { describe, expect, it } from 'vitest';
import { parseInspection } from '../src/mint-inspector/contract.js';
import { fixtureInspection } from './fixtures/mint-inspector.js';

describe('mint inspector immutable display contract', () => {
  it('retains exact strings, nulls, package order, multiple instructions and unknown attribution', () => {
    const input = fixtureInspection(), serialized = JSON.stringify(input);
    const output = parseInspection(JSON.parse(serialized));
    expect(JSON.stringify(output)).toBe(serialized);
    expect(output.timeline.transactions[0].balance_observations[0].observation.amount_u64).toBe('18446744073709551615');
    expect(output.timeline.transactions[0].balance_observations[0].observation.decimals).toBeNull();
    expect(output.timeline.transactions.map(p => p.transaction_index)).toEqual(['0', '1', '2']);
    expect(output.timeline.transactions[1].instructions).toHaveLength(2);
    expect(output.timeline.transactions[1].diagnostics[0].mint_attribution).toBe('UNKNOWN');
  });
  it.each(['duplicate', 'reordered', 'nonatomic', 'failed-fact', 'rounded-integer', 'count', 'class', 'binding', 'lifecycle-fact', 'span'])('rejects %s rather than repairing or partially rendering', mutation => {
    const data = fixtureInspection(), p = data.timeline.transactions;
    if (mutation === 'duplicate') p[1] = structuredClone(p[0]);
    if (mutation === 'reordered') p.reverse();
    if (mutation === 'nonatomic') Object.assign(p[0], { atomic_observation_package: false });
    if (mutation === 'failed-fact') p[2].silver_facts = p[0].silver_facts;
    if (mutation === 'rounded-integer') Object.assign(p[1].balance_observations[0].observation, { amount_u64: 9007199254740992 });
    if (mutation === 'count') data.timeline.counts.balance_observations = '300';
    if (mutation === 'class') p[1].slice_class = 'RESEARCH_SAMPLING';
    if (mutation === 'binding') data.inputs.collection = '0'.repeat(64);
    if (mutation === 'lifecycle-fact') data.lifecycle.facts[0].silver_record_sha256 = '0'.repeat(64);
    if (mutation === 'span') p[0].source.raw_section_length = '101';
    expect(() => parseInspection(data)).toThrow('INSPECTOR_CONTRACT_INVALID');
  });
  it('preserves balance-only evidence and a failed package without successful facts', () => {
    const data = fixtureInspection();
    expect(data.timeline.transactions[1].silver_facts).toEqual([]);
    expect(data.timeline.transactions[1].balance_observations).toHaveLength(1);
    expect(data.timeline.transactions[2].transaction_status).toBe('ERROR');
    expect(data.timeline.transactions[2].silver_facts).toEqual([]);
    expect(data.timeline.counts.status.ERROR).toBe('1');
  });
});
