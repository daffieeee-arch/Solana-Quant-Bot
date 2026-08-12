import { describe, expect, it } from 'vitest';
import { FlowTelemetry } from '../src/flow-telemetry.js';

describe('FlowTelemetry (Laag B)', () => {
  it('counts buys and sells for a mint within the window', () => {
    const flow = new FlowTelemetry(3 * 60_000, () => 1000);
    flow.recordBuy('mint-a');
    flow.recordBuy('mint-a');
    flow.recordSell('mint-a');
    flow.recordBuy('mint-b');
    expect(flow.snapshot('mint-a', 1000)).toEqual({ buys: 2, sells: 1 });
    expect(flow.snapshot('mint-b', 1000)).toEqual({ buys: 1, sells: 0 });
    expect(flow.snapshot('mint-nope', 1000)).toEqual({ buys: 0, sells: 0 });
  });

  it('drops events older than the window', () => {
    let t = 0;
    const flow = new FlowTelemetry(100, () => t);
    flow.recordBuy('mint-x');   // t=0
    t = 250;                     // beyond 100ms window
    flow.recordBuy('mint-x');
    expect(flow.snapshot('mint-x', t).buys).toBe(1); // oud weg, nieuw telt
  });
});
