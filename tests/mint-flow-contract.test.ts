import { describe, expect, it } from 'vitest';
import { fixtureMintFlow } from './fixtures/mint-flow.js';
import { parseMintFlow } from '../src/mint-inspector/mint-flow.js';

describe('Python mint-flow presentation binding', () => {
  it('accepts the shared exact golden report with atomic prefixes and role totals', () => {
    const { inspection, flow } = fixtureMintFlow();
    expect(parseMintFlow(flow, inspection)).toBe(flow);
    expect(flow.report.totals.ALL.net_token_raw).toBe('1');
    expect(flow.report.packages[0].cumulative.ALL.net_token_raw).toBe('-3');
    expect(flow.report.packages[0].fact_hashes).toHaveLength(2);
    expect(flow.report.packages[2].fact_hashes).toHaveLength(0);
    expect(flow.report.quote_volume.quote_decimals).toBeNull();
  });
  it.each(['snapshot', 'mint', 'package', 'source', 'order', 'class', 'fact', 'duplicate-fact', 'prefix', 'numeric', 'negative-zero', 'missing', 'user', 'amount', 'quote', 'producer'])('rejects inconsistent %s without replacing values', variant => {
    const { inspection, flow } = fixtureMintFlow(), r = flow.report;
    if (variant === 'snapshot') r.inputs.timeline = '0'.repeat(64);
    if (variant === 'mint') r.mint = 'other';
    if (variant === 'package') r.packages.pop();
    if (variant === 'source') r.packages[0].collection_source_id = 'other';
    if (variant === 'order') r.packages.reverse();
    if (variant === 'class') r.packages[0].slice_class = 'ENGINEERING_VALIDATION_ONLY';
    if (variant === 'fact') r.packages[0].fact_hashes.pop();
    if (variant === 'duplicate-fact') r.packages[0].fact_hashes[1] = r.packages[0].fact_hashes[0];
    if (variant === 'prefix') r.packages[2].cumulative.ALL.net_token_raw = '2';
    if (variant === 'numeric') Object.assign(r.totals.ALL, { buy_token_raw: 11 });
    if (variant === 'negative-zero') r.totals.ALL.net_token_raw = '-0';
    if (variant === 'missing') Object.assign(r.totals.ALL, { buy_token_raw: null });
    if (variant === 'user') delete inspection.timeline.transactions[0].silver_facts[0].record.event_reported.user_address;
    if (variant === 'amount') inspection.timeline.transactions[0].silver_facts[0].record.event_reported.token_amount_raw_u64 = '18446744073709551616';
    if (variant === 'quote') Object.assign(r.quote_volume, { quote_decimals: '9' });
    if (variant === 'producer') r.producer.version = 'UNKNOWN';
    expect(() => parseMintFlow(flow, inspection)).toThrow('INSPECTOR_CONTRACT_INVALID');
  });
});
