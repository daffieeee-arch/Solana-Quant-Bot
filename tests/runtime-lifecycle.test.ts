import { describe, expect, it, vi } from 'vitest';
import {
  closeRuntimeResources, closeRuntimeStores, withRuntimeLifecycle, type RuntimeResources,
} from '../src/runtime-lifecycle.js';

describe('withRuntimeLifecycle', () => {
  it('closes all owned resources after bounded normal completion and returns the result', async () => {
    const order: string[] = [];
    const result = await withRuntimeLifecycle(async (resources) => {
      resources.ledgerStore = { close: async () => { order.push('ledger'); } };
      resources.provider = { destroy: () => { order.push('provider'); } };
      resources.opportunityStorage = { close: () => { order.push('opportunity'); } };
      resources.dashboard = { close: async () => { order.push('dashboard'); } };
      return 'bounded-complete';
    });

    expect(result).toBe('bounded-complete');
    expect(order).toEqual(['dashboard', 'provider', 'opportunity', 'ledger']);
  });

  it.each([
    ['ledger load', ['ledger'], (resources: RuntimeResources, order: string[]) => {
      resources.ledgerStore = { close: async () => { order.push('ledger'); } };
    }],
    ['history load', ['ledger'], (resources: RuntimeResources, order: string[]) => {
      resources.ledgerStore = { close: async () => { order.push('ledger'); } };
    }],
    ['provider construction before assignment', ['ledger'], (resources: RuntimeResources, order: string[]) => {
      resources.ledgerStore = { close: async () => { order.push('ledger'); } };
    }],
    ['controller construction', ['provider', 'ledger'], (resources: RuntimeResources, order: string[]) => {
      resources.ledgerStore = { close: async () => { order.push('ledger'); } };
      resources.provider = { destroy: () => { order.push('provider'); } };
    }],
    ['dashboard initialization', ['provider', 'opportunity', 'ledger'], (resources: RuntimeResources, order: string[]) => {
      resources.ledgerStore = { close: async () => { order.push('ledger'); } };
      resources.provider = { destroy: () => { order.push('provider'); } };
      resources.opportunityStorage = { close: () => { order.push('opportunity'); } };
    }],
  ])('cleans the resources reachable after a staged %s failure', async (_stage, expectedOrder, initialize) => {
    const order: string[] = [];
    const initializationError = new Error(`${_stage} failed`);

    const failure = await withRuntimeLifecycle(async (resources) => {
      initialize(resources, order);
      throw initializationError;
    }).then(() => undefined, (error: unknown) => error);

    expect(failure).toBe(initializationError);
    expect(order).toEqual(expectedOrder);
  });

  it('preserves the runtime failure together with cleanup failures while continuing cleanup', async () => {
    const runtimeError = new Error('scan failed');
    const providerError = new Error('provider destroy failed');
    const closeLedger = vi.fn(async () => undefined);

    const failure = await withRuntimeLifecycle(async (resources) => {
      resources.ledgerStore = { close: closeLedger };
      resources.provider = { destroy: () => { throw providerError; } };
      throw runtimeError;
    }).then(() => undefined, (error: unknown) => error);

    expect(failure).toBeInstanceOf(AggregateError);
    expect((failure as AggregateError).errors).toEqual([runtimeError, providerError]);
    expect(closeLedger).toHaveBeenCalledOnce();
  });
});

describe('closeRuntimeResources', () => {
  it('closes every initialized optional resource in deterministic shutdown order', async () => {
    const order: string[] = [];
    await closeRuntimeResources({
      dashboard: { close: async () => { order.push('dashboard'); } },
      provider: { destroy: () => { order.push('provider'); } },
      opportunityStorage: { close: () => { order.push('opportunity'); } },
      ledgerStore: { close: async () => { order.push('ledger'); } },
    });

    expect(order).toEqual(['dashboard', 'provider', 'opportunity', 'ledger']);
  });

  it('skips resources that were not initialized during staged startup', async () => {
    const order: string[] = [];
    await closeRuntimeResources({
      provider: { destroy: () => { order.push('provider'); } },
      ledgerStore: { close: async () => { order.push('ledger'); } },
    });

    expect(order).toEqual(['provider', 'ledger']);
  });

  it('attempts every later shutdown and reports all cleanup failures in order', async () => {
    const order: string[] = [];
    const dashboardError = new Error('dashboard close failed');
    const providerError = new Error('provider destroy failed');
    const opportunityError = new Error('opportunity close failed');
    const ledgerError = new Error('ledger close failed');

    const failure = await closeRuntimeResources({
      dashboard: { close: async () => { order.push('dashboard'); throw dashboardError; } },
      provider: { destroy: () => { order.push('provider'); throw providerError; } },
      opportunityStorage: { close: () => { order.push('opportunity'); throw opportunityError; } },
      ledgerStore: { close: async () => { order.push('ledger'); throw ledgerError; } },
    }).then(() => undefined, (error: unknown) => error);

    expect(order).toEqual(['dashboard', 'provider', 'opportunity', 'ledger']);
    expect(failure).toBeInstanceOf(AggregateError);
    expect((failure as AggregateError).errors).toEqual([
      dashboardError, providerError, opportunityError, ledgerError,
    ]);
  });
});

describe('closeRuntimeStores', () => {
  it('closes opportunity storage before awaiting the ledger store', async () => {
    const order: string[] = [];
    await closeRuntimeStores(
      { close: () => { order.push('opportunity'); } },
      { close: async () => { order.push('ledger'); } },
    );
    expect(order).toEqual(['opportunity', 'ledger']);
  });

  it('still closes the ledger store when opportunity close throws', async () => {
    const closeLedger = vi.fn(async () => undefined);
    await expect(closeRuntimeStores(
      { close: () => { throw new Error('opportunity close failed'); } },
      { close: closeLedger },
    )).rejects.toThrow('opportunity close failed');
    expect(closeLedger).toHaveBeenCalledOnce();
  });
});
