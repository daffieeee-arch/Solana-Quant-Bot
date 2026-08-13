import { afterEach, describe, expect, it } from 'vitest';
import { mkdtemp, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createDashboardServer, summarizeProviderHealth, toDashboardData, type DashboardServer } from '../src/dashboard.js';

let dashboard: DashboardServer | undefined;
afterEach(async () => { await dashboard?.close(); dashboard = undefined; });

describe('dashboard data API', () => {
  it('serves a read-only paper dashboard model with metrics and position data', async () => {
    dashboard = await createDashboardServer({
      port: 0,
      getStatus: () => ({
        mode: 'paper' as const,
        updatedAt: '2026-07-25T00:00:00.000Z',
        availableLamports: 9_750_000_000,
        realizedPnlLamports: 250_000_000,
        markPricesByMint: { Mint111111111111111111111111111111111111111: 1.2 },
        marketContext: { updatedAt: '2026-07-25T00:00:00.000Z', ticker: [{ symbol: 'SOL', name: 'Solana', priceUsd: 150, change24hPercent: 3.2 }], news: [{ source: 'CoinDesk', title: 'Market update', url: 'https://www.coindesk.com/example', publishedAt: '2026-07-25T00:00:00.000Z' }] },
        equityHistory: [
          { at: '2026-07-24T23:55:00.000Z', equityLamports: 10_000_000_000 },
          { at: '2026-07-25T00:00:00.000Z', equityLamports: 10_255_000_000 },
        ],
        openPositions: [{
          mint: 'Mint111111111111111111111111111111111111111', symbol: 'ALPHA', openedAt: '2026-07-25T00:00:00.000Z',
          entryPriceUsd: 1, highPriceUsd: 1.4, allocatedLamports: 250_000_000, entryCostLamports: 255_000_000,
        }],
        recentDecisions: [
          { type: 'paper_exit', pairId: 'pair-win', mint: 'MintExit111', symbol: 'WIN', openedAt: '2026-07-24T23:00:00.000Z', reason: 'trailing_stop', pnlLamports: 250_000_000, at: '2026-07-25T00:00:00.000Z' },
          { type: 'duplicate_suppressed', pairId: 'pair-noise', at: '2026-07-25T00:00:00.000Z' },
        ],
      }),
    });
    const response = await fetch(`http://127.0.0.1:${dashboard.port}/api/dashboard-data`, {
      headers: { authorization: 'Bearer test-token' },
    });

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toMatchObject({
      mode: 'paper',
      summary: { availableSol: 9.75, realizedPnlSol: 0.25, openPositions: 1, totalEquitySol: 10.05 },
      positions: [expect.objectContaining({ symbol: 'ALPHA', entryPriceUsd: 1, unrealizedPnlSol: 0.045, unrealizedPnlPercent: 17.6 })],
      scanner: expect.objectContaining({ status: 'online', duplicateSuppressed: 1 }),
      marketContext: { updatedAt: '2026-07-25T00:00:00.000Z', ticker: [{ symbol: 'SOL', name: 'Solana', priceUsd: 150, change24hPercent: 3.2 }], news: [{ source: 'CoinDesk', title: 'Market update', url: 'https://www.coindesk.com/example', publishedAt: '2026-07-25T00:00:00.000Z' }] },
      equity: [
        { at: '2026-07-24T23:55:00.000Z', equitySol: 10 },
        { at: '2026-07-25T00:00:00.000Z', equitySol: 10.255 },
      ],
      candidates: expect.any(Array),
      rejections: expect.any(Array),
      closedTrades: [expect.objectContaining({ symbol: 'WIN', reason: 'trailing_stop', pnlSol: 0.25, heldMinutes: 60 })],
    });
    const data = await (await fetch(`http://127.0.0.1:${dashboard.port}/api/dashboard-data`)).json();
    expect(data.feed).toHaveLength(2);
    expect(data.feed[0]).toMatchObject({ type: 'scan_complete', symbol: 'SCANNER' });
    expect(data.feed[1]).toMatchObject({ type: 'paper_exit', pairId: 'pair-win', symbol: 'WIN' });
  });

  it('keeps a scanner heartbeat and coalesced retained panels when repeated pairs dominate a scan window', () => {
    const repeated = Array.from({ length: 100 }, (_, index) => ({ type: 'duplicate_suppressed' as const, pairId: `repeated-${index}`, at: '2026-07-25T00:05:00.000Z' }));
    const data = toDashboardData({
      mode: 'paper', updatedAt: '2026-07-25T00:10:00.000Z', availableLamports: 10_000_000_000, realizedPnlLamports: 0, openPositions: [],
      recentDecisions: [
        { type: 'paper_entry', pairId: 'qualified-pair', mint: 'qualified-mint', symbol: 'QUAL', score: 47.5, source: 'scanner', at: '2026-07-25T00:01:00.000Z' },
        { type: 'rejected', pairId: 'filtered-pair', mint: 'filtered-mint', symbol: 'FILTER', reason: 'score_below_minimum', score: 26, source: 'scanner', at: '2026-07-25T00:02:00.000Z' },
        { type: 'rejected', pairId: 'filtered-pair', mint: 'filtered-mint', symbol: 'FILTER', reason: 'score_below_minimum', score: 28, source: 'scanner', at: '2026-07-25T00:03:00.000Z' },
        ...repeated,
      ],
    });

    expect(data.feed[0]).toMatchObject({ type: 'scan_complete', symbol: 'SCANNER' });
    expect(data.scanner).toMatchObject({ duplicateSuppressed: 100, meaningfulEvents: 3 });
    expect(data.candidates).toEqual([expect.objectContaining({ symbol: 'QUAL', score: 47.5 })]);
    expect(data.rejections).toEqual([expect.objectContaining({ symbol: 'FILTER', score: 28, occurrences: 2 })]);
  });

  it('exposes launch, discovery, quote and evaluation times with rejection class', () => {
    const data = toDashboardData({
      mode: 'paper', updatedAt: '2026-07-29T13:01:50.000Z', availableLamports: 10_000_000_000, realizedPnlLamports: 0, openPositions: [],
      recentDecisions: [{
        type: 'rejected', pairId: 'pump-pair', mint: 'pump-mint', symbol: 'PUMP',
        reason: 'daily_loss_limit', score: 81.83, source: 'solana_rpc_ws_pumpswap',
        pairCreatedAt: '2026-07-29T12:44:33.000Z', firstSeenAt: '2026-07-29T13:01:50.000Z',
        observedAt: '2026-07-29T13:01:49.500Z', evaluatedAt: '2026-07-29T13:01:50.000Z',
        detectionDelayMs: 1_037_000, at: '2026-07-29T13:01:50.000Z',
      }],
    });

    expect(data.rejections).toEqual([expect.objectContaining({
      rejectionClass: 'risk',
      pairCreatedAt: '2026-07-29T12:44:33.000Z',
      firstSeenAt: '2026-07-29T13:01:50.000Z',
      observedAt: '2026-07-29T13:01:49.500Z',
      evaluatedAt: '2026-07-29T13:01:50.000Z',
      lastEvaluatedAt: '2026-07-29T13:01:50.000Z',
      detectionDelayMs: 1_037_000,
      occurrences: 1,
    })]);
  });

  it('preserves rich timing through legacy and out-of-order rejection coalescing', () => {
    const common = {
      type: 'rejected' as const, pairId: 'pump-pair', mint: 'pump-mint', symbol: 'PUMP',
      reason: 'liquidity_below_minimum', source: 'scanner',
    };
    const data = toDashboardData({
      mode: 'paper', updatedAt: '2026-07-29T13:04:00.000Z', availableLamports: 0, realizedPnlLamports: 0, openPositions: [],
      recentDecisions: [
        { ...common, score: 10, pairCreatedAt: '2026-07-29T12:44:33.000Z', firstSeenAt: '2026-07-29T13:01:50.000Z', observedAt: '2026-07-29T13:02:00.000Z', evaluatedAt: '2026-07-29T13:02:01.000Z', detectionDelayMs: 1_037_000 },
        { ...common, score: 20, at: '2026-07-29T13:03:00.000Z' },
        { ...common, score: 30, firstSeenAt: '2026-07-29T13:02:50.000Z', observedAt: '2026-07-29T13:00:00.000Z', evaluatedAt: '2026-07-29T13:04:00.000Z' },
      ],
    });
    expect(data.rejections).toEqual([expect.objectContaining({
      occurrences: 3,
      pairCreatedAt: '2026-07-29T12:44:33.000Z',
      firstSeenAt: '2026-07-29T13:01:50.000Z',
      observedAt: '2026-07-29T13:02:00.000Z',
      firstEvaluatedAt: '2026-07-29T13:02:01.000Z',
      evaluatedAt: '2026-07-29T13:04:00.000Z',
      lastEvaluatedAt: '2026-07-29T13:04:00.000Z',
      detectionDelayMs: 1_037_000,
    })]);
  });

  it('serves the built frontend shell without exposing writable routes', async () => {
    const staticDir = await mkdtemp(join(tmpdir(), 'paper-dashboard-ui-'));
    await writeFile(join(staticDir, 'index.html'), '<!doctype html><title>Paper Monitor</title><div id="root"></div>');
    dashboard = await createDashboardServer({
      port: 0, staticDir,
      getStatus: () => ({ mode: 'paper' as const, updatedAt: '2026-07-25T00:00:00.000Z', availableLamports: 1, openPositions: [], realizedPnlLamports: 0 }),
    });

    const response = await fetch(`http://127.0.0.1:${dashboard.port}/`, { headers: { authorization: 'Bearer test-token' } });
    expect(response.status).toBe(200);
    expect(response.headers.get('content-type')).toContain('text/html');
    await expect(response.text()).resolves.toContain('Paper Monitor');
  });

  it('keeps dashboard routes read-only', async () => {
    dashboard = await createDashboardServer({
      port: 0,
      getStatus: () => ({ mode: 'paper' as const, updatedAt: '2026-07-25T00:00:00.000Z', availableLamports: 1, openPositions: [], realizedPnlLamports: 0 }),
    });
    const response = await fetch(`http://127.0.0.1:${dashboard.port}/api/dashboard-data`, {
      method: 'POST', headers: { authorization: 'Bearer test-token' },
    });

    expect(response.status).toBe(404);
  });

  it('summarizes provider health: all ok when no errors', () => {
    const health = summarizeProviderHealth([]);
    // TRITON-ONLY: Solana WS lane verwijderd.
    expect(health.map((h) => h.provider)).toEqual(['TRITON']);
    expect(health.every((h) => h.status === 'ok')).toBe(true);
  });

  it('marks Triton down on stream error, degraded on connect issues', () => {
    const down = summarizeProviderHealth(['triton: stream error program=pumpfun']);
    expect(down.find((h) => h.provider === 'TRITON')?.status).toBe('down');
    const rl = summarizeProviderHealth(['triton: subscribe failed; program=675k']);
    expect(rl.find((h) => h.provider === 'TRITON')?.status).toBe('down');
  });

  it('marks Triton degraded on geyser connect issues and rpc errors', () => {
    const health = summarizeProviderHealth([
      'triton: geyser connect timeout',
      'websocket: solana rpc error',
    ]);
    expect(health.find((h) => h.provider === 'TRITON')?.status).toBe('degraded');
  });

  it('Fase-O: Titan pair-level no-route (code 14) degradeert NIET / blokkeert NIET', () => {
    // T2-fix: een per-paar 'could not determine best price' (code 14, geen route)
    // is een normale request-level uitkomst, geen provider-failure. Mag de health
    // NIET op degraded zetten.
    const health = summarizeProviderHealth([
      'titan: Request 320 failed with code 14: could not determine best price',
    ]);
    // géén degraded/down — de provider blijft ok (verbinding+pricing werken)
    expect(health.find((h) => h.provider === 'TRITON')?.status).toBe('ok');
  });

  it('Fase-O: Titan quote-failure voert NOOIT naar down, conn/auth breken wél', () => {
    // tijdelijke quote-fout (code-14 equivalent op de WS) → ok (fail-closed, verbinding intact)
    const quote = summarizeProviderHealth(['titan: could not determine best price for pair']);
    expect(quote.find((h) => h.provider === 'TRITON')?.status).toBe('ok');
    // echte conn/auth/stream-failure → degraded/down
    const auth = summarizeProviderHealth(['titan: 401 unauthorized']);
    expect(auth.find((h) => h.provider === 'TRITON')?.status).toBe('down');
    const conn = summarizeProviderHealth(['titan: connect timeout']);
    expect(conn.find((h) => h.provider === 'TRITON')?.status).toBe('degraded');
    // reviewer-hardening: een ECHTE netwerkfout "no route to host" moet als
    // verbindingsfout degraden (niet ten onrechte als ok door 'no route').
    const noRouteHost = summarizeProviderHealth(['titan: connect to host failed: no route to host']);
    expect(noRouteHost.find((h) => h.provider === 'TRITON')?.status).toBe('degraded');
  });
});
