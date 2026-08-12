import { describe, expect, it, vi } from 'vitest';
import { TritonReserveReader } from '../src/providers/triton-reserves.js';

describe('TritonReserveReader', () => {
  it('resolves the real coin ticker via Triton DAS getAsset and caches it', async () => {
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body));
      if (body.method === 'getAsset') {
        return new Response(JSON.stringify({
          jsonrpc: '2.0', id: body.id, result: { content: { metadata: { symbol: 'USDC' } }, token_info: { symbol: 'USDC' } },
        }), { status: 200 });
      }
      return new Response('{}', { status: 200 });
    });
    const reader = new TritonReserveReader('endpoint.rpcpool.com', 'token', fetcher);
    await expect(reader.resolveSymbol('EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v')).resolves.toBe('USDC');
    await expect(reader.resolveSymbol('EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v')).resolves.toBe('USDC');
    expect(fetcher).toHaveBeenCalledTimes(1); // gecached
    // Verbruiksregistratie: dé 1 getAsset-call is geteld (geen ruis van de cache-hit).
    expect(reader.rpcCallCountsSnapshot()).toEqual({ getAsset: 1 });
  });

  it('falls back to token_info.symbol when content metadata is absent', async () => {
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body));
      return new Response(JSON.stringify({ jsonrpc: '2.0', id: body.id, result: { token_info: { symbol: 'POPCAT' } } }), { status: 200 });
    });
    const reader = new TritonReserveReader('endpoint.rpcpool.com', 'token', fetcher);
    await expect(reader.resolveSymbol('7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr')).resolves.toBe('POPCAT');
  });

  it('returns undefined fail-closed when getAsset errors', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ jsonrpc: '2.0', id: 1, error: { code: -32602, message: 'invalid' } }), { status: 200 }));
    const reader = new TritonReserveReader('endpoint.rpcpool.com', 'token', fetcher);
    await expect(reader.resolveSymbol('unknown-mint')).resolves.toBeUndefined();
  });

  it('assesses rug/honeypot evidence from getAsset + holder concentration', async () => {
    const calls: string[] = [];
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body));
      calls.push(body.method);
      if (body.method === 'getAsset') {
        return new Response(JSON.stringify({ jsonrpc: '2.0', id: 1, result: { burnt: false, mutable: true, authorities: [{ scopes: ['full'] }] } }), { status: 200 });
      }
      if (body.method === 'getTokenLargestAccounts') {
        return new Response(JSON.stringify({ jsonrpc: '2.0', id: 1, result: { value: [{ amount: '800' }, { amount: '100' }, { amount: '100' }] } }), { status: 200 });
      }
      return new Response('{}', { status: 200 });
    });
    const reader = new TritonReserveReader('endpoint.rpcpool.com', 'token', fetcher);
    const evidence = await reader.assessRugSafety('mint-x');
    expect(calls).toEqual(['getAsset', 'getTokenLargestAccounts']);
    expect(evidence?.fullAuthorityCount).toBe(1);
    expect(evidence?.mutable).toBe(true);
    // top3=1000/1000 = 1.0
    expect(evidence?.holderConcentration).toBe(1);
  });

  it('caches rug evidence within TTL', async () => {
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body));
      if (body.method === 'getAsset') return new Response(JSON.stringify({ jsonrpc: '2.0', id: 1, result: { burnt: true } }), { status: 200 });
      if (body.method === 'getTokenLargestAccounts') return new Response(JSON.stringify({ jsonrpc: '2.0', id: 1, result: { value: [] } }), { status: 200 });
      return new Response('{}', { status: 200 });
    });
    const reader = new TritonReserveReader('endpoint.rpcpool.com', 'token', fetcher);
    await reader.assessRugSafety('mint-y');
    await reader.assessRugSafety('mint-y');
    // 2 calls (getAsset+largest) voor eerste; 0 voor gecached tweede
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('REG: gebruikt de RPC-response-decimals i.p.v. de hardcoded 6/9-caller-waarden (SOL-quoted pool)', async () => {
    // De oudere code negeerde de response-decimals (Number.isInteger(callerWaarde)
    // won) en kreeg met hardcoded 6/9 een ~10^6× prijsfout voor SOL-quoted AMMv4
    // pools (token 6-dec, WSOL 9-dec). De response `decimals` zijn autoritatief.
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body));
      if (body.method === 'getTokenAccountBalance') {
        const vault = body.params[0];
        const isQuote = vault === 'quoteVault';
        return new Response(JSON.stringify({
          jsonrpc: '2.0', id: 1,
          result: { value: { amount: isQuote ? '5000000000' : '1000000000000', decimals: isQuote ? 9 : 6 } },
        }), { status: 200 });
      }
      return new Response('{}', { status: 200 });
    });
    const reader = new TritonReserveReader('endpoint.rpcpool.com', 'token', fetcher);
    const depth = await reader.fetchDepth('amm4:test', 'quoteVault', 'baseVault', 6, 9);
    // Response-decimals moeten winnen (9 voor quote/WSOL, 6 voor base/token),
    // NIET de omgedraaide caller-waarden (6/9).
    expect(depth?.quoteDecimals).toBe(9);
    expect(depth?.baseDecimals).toBe(6);
  });
});