import { describe, it, expect, vi } from 'vitest';
import bs58 from 'bs58';
import { TitanQuoteProvider, type TitanQuote } from '../src/providers/titan.js';

const ENDPOINT = 'johnb-mainnet-2781.mainnet.rpcpool.com';
const TOKEN = '#### REDACTED ####';
const SOL = 'So11111111111111111111111111111111111111112';
const MINT = 'C8VZE8cy71FKrVKMdi8Ne9q9JNyFD7vJEbGMuaS3pump';

// Mock de V1Client zodat er geen echte WebSocket-verbinding nodig is.
// getSwapPrice({inputMint, outputMint, amount}) → { amountIn, amountOut }
vi.mock('@titanexchange/sdk-ts', () => {
  const fakestore: Map<string, (req: never) => Promise<{ amountIn: number | bigint; amountOut: number | bigint }>> = new Map();
  const makeMockClient = () => ({
    getSwapPrice: async (req: { amount: number | bigint }) => {
      const handler = fakestore.get('handler');
      if (handler) return handler(req as never);
      throw new Error('no handler configured');
    },
    close: async () => undefined,
  });
  const V1Client = {
    connect: vi.fn(async () => makeMockClient()),
    __handler: (h: (req: never) => Promise<{ amountIn: number | bigint; amountOut: number | bigint }>) => { fakestore.set('handler', h as never); },
  };
  return { V1Client };
});

import { V1Client } from '@titanexchange/sdk-ts';

function providerWithClock(clock = () => 100_000, cacheMs = 10_000): TitanQuoteProvider {
  return new TitanQuoteProvider(ENDPOINT, TOKEN, 8_000, cacheMs, clock);
}

describe('TitanQuoteProvider (SDK-based)', () => {
  it('decodes a live getSwapPrice response into a TitanQuote', async () => {
    const amount = 10 ** 9; // 1 SOL lamports
    (V1Client as unknown as { __handler: (...a: never[]) => void }).__handler(async (req) => ({
      amountIn: 1_500_000_000,
      amountOut: 120_000_000_000_000n,
    }));
    const provider = providerWithClock();
    const quote = await provider.fetchQuote({ outputMint: MINT, amountLamports: amount });
    expect(quote).toBeDefined();
    expect(quote!.source).toBe('titan');
    expect(quote!.outputMint).toBe(MINT);
    expect(quote!.amountIn).toBe(amount);
  });

  it('caches identical (mint,amount) quotes within the window', async () => {
    let calls = 0;
    (V1Client as unknown as { __handler: (...a: never[]) => void }).__handler(async () => {
      calls += 1;
      return { amountIn: 1_500_000_000, amountOut: 120_000_000_000_000n };
    });
    const provider = providerWithClock();
    await provider.fetchQuote({ outputMint: MINT, amountLamports: 1_000_000_000 });
    await provider.fetchQuote({ outputMint: MINT, amountLamports: 1_000_000_000 });
    expect(calls).toBe(1);
  });

  it('returns undefined (fail-closed) on SDK error and records a diagnostic', async () => {
    (V1Client as unknown as { __handler: (...a: never[]) => void }).__handler(async () => {
      throw new Error('stream error');
    });
    const provider = providerWithClock();
    const quote = await provider.fetchQuote({ outputMint: MINT, amountLamports: 1_000_000_000 });
    expect(quote).toBeUndefined();
    expect(provider.drainDiagnostics().some((d) => d.includes('titan:'))).toBe(true);
  });

  it('returns undefined for invalid mint input', async () => {
    const provider = providerWithClock();
    expect(await provider.fetchQuote({ outputMint: 'not-a-mint', amountLamports: 100 })).toBeUndefined();
    expect(await provider.fetchQuote({ outputMint: MINT, amountLamports: 0 })).toBeUndefined();
  });

  it('quoteToUsd converts SOL-per-token price to USD', () => {
    const quote: TitanQuote = { price: 0.00001, inputMint: SOL, outputMint: MINT, amountIn: 1, amountOut: 100_000, source: 'titan' };
    const provider = providerWithClock();
    expect(provider.quoteToUsd(quote, 150)).toBeCloseTo(0.0015, 6);
    expect(provider.quoteToUsd(quote, undefined as never)).toBeUndefined();
    expect(provider.quoteToUsd(quote, 0)).toBeUndefined();
  });

  it('rejects invalid endpoint/token', () => {
    expect(() => new TitanQuoteProvider('', TOKEN)).toThrow();
    expect(() => new TitanQuoteProvider(ENDPOINT, '')).toThrow();
    expect(() => new TitanQuoteProvider('bad\nendpoint', TOKEN)).toThrow();
  });
});