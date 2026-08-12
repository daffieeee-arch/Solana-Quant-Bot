import { describe, expect, it } from 'vitest';
import { assessRugRisk, isRugRisky } from '../src/rug-risk.js';

describe('assessRugRisk (Triton-first rug/honeypot)', () => {
  it('flags missing evidence as high risk (fail-closed)', () => {
    const risk = assessRugRisk(undefined);
    expect(risk.className).toBe('high');
    expect(risk.score).toBe(1);
    expect(isRugRisky(risk)).toBe(true);
  });

  it('treats left-over full mint/freeze authority as elevated risk', () => {
    const risk = assessRugRisk({ fullAuthorityCount: 1, burnt: false, mutable: true });
    expect(risk.flags).toContain('full_authority_1');
    // 0.3 (authority) + 0.15 (mutable) = 0.45 → moderate
    expect(risk.score).toBe(0.45);
    expect(risk.className).toBe('moderate');
    expect(isRugRisky(risk, 0.5)).toBe(false);
  });

  it('reduces risk when supply is burnt', () => {
    const risk = assessRugRisk({ burnt: true, fullAuthorityCount: 1, mutable: false });
    // 0.3 (authority) - 0.4 (burnt) = 0 => clean
    expect(risk.score).toBe(0);
    expect(risk.className).toBe('clean');
    expect(risk.flags).toContain('supply_burnt');
  });

  it('flags extreme holder concentration as high risk', () => {
    const risk = assessRugRisk({ burnt: false, fullAuthorityCount: 0, mutable: false, holderConcentration: 0.9 });
    // enkel extreme_holder_concentration → 0.35
    expect(risk.score).toBe(0.35);
    expect(risk.className).toBe('moderate');
    expect(risk.flags).toContain('extreme_holder_concentration');
  });

  it('classifies a clean token correctly', () => {
    const risk = assessRugRisk({ burnt: true, fullAuthorityCount: 0, mutable: false, holderConcentration: 0.05 });
    expect(risk.className).toBe('clean');
    expect(isRugRisky(risk)).toBe(false);
  });
});