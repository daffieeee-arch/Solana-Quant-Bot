import { describe, expect, it } from 'vitest';
import { canPaperEnter } from '../src/risk.js';

describe('paper risk gate', () => {
  it('blocks unknown risk status in strict safety mode', () => {
    expect(canPaperEnter('unknown', true)).toEqual({ allowed: false, reason: 'risk_unknown_in_strict_mode' });
  });

  it('permits unknown risk status in alert-oriented non-strict demo mode', () => {
    expect(canPaperEnter('unknown', false)).toEqual({ allowed: true });
  });

  it('always blocks a flagged token', () => {
    expect(canPaperEnter('flagged', false)).toEqual({ allowed: false, reason: 'risk_flagged' });
  });
});
